import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { atomic, readJSON, identifier } from './runtime.mjs';
import { quotaTelemetry, sameOpenAIAccount } from './codex.mjs';

const exec = promisify(execFile);
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const now = () => new Date().toISOString();
const memoryLayers = { fact: 'memory', crew: 'crew', person: 'people', doctrine: 'doctrine', ledger: 'ledger', daily: 'daily' };
const questionHash = q => digest(JSON.stringify([q.qid, q.question, q.detail ?? '', q.recommend ?? '', q.tasks, q.who]));
const quotaIdentity = q => q && JSON.stringify([q.revision ?? null, q.enabled, q.source, q.limitId ?? 'codex', q.maxAgeSeconds]);

export class Services {
  constructor(fleet, { bash, powershell, opencode, codex, opencodeAuth, codexAuth, isStopped = () => false, signal } = {}) {
    this.fleet = fleet;
    this.bash = bash;
    this.powershell = powershell;
    this.opencode = opencode;
    this.codex = codex;
    this.opencodeAuth = opencodeAuth;
    this.codexAuth = codexAuth;
    this.panelProcess = null;
    this.stopped = false;
    // Keep the legacy predicate and use the plugin's abort edge for awaits that settle during shutdown.
    this.isStopped = () => this.stopped || isStopped() || signal?.aborted === true;
  }

  stop() { this.stopped = true; }

  panelSnapshot() {
    const f = this.fleet;
    const s = f.state();
    if (!s) return;
    const db = readJSON(f.dbFile, { tasks: [] });
    const view = { project: f.directory, captain: s.captain, paused: s.paused,
      reason: s.reason, autoWake: s.autoWake, updated: now(), workers: Object.values(s.workers),
      events: s.events.filter(e => !e.ack), tasks: db.tasks, trajectory: s.lastTrajectory ?? null,
      quota: readJSON(path.join(f.home, 'quota.json'), null), channels: s.channels ?? {},
      audit: s.audit ?? null, auditSnapshot: s.audit?.snapshotExitCode ? null : readJSON(path.join(f.home, 'audit/status.json'), null),
      decisions: (db.decisions ?? []).filter(q => !q.answer).map(q => ({ ...q, questionHash: questionHash(q) })) };
    fs.mkdirSync(path.join(f.home, 'inbox'), { recursive: true });
    atomic(path.join(f.home, 'panel.json'), JSON.stringify(view, null, 2));
    return view;
  }

  openPanel(sessionID) {
    this.fleet.requireCaptain(sessionID, true);
    if (process.platform !== 'win32' || !this.powershell) throw new Error('Windows PowerShell is not configured');
    this.panelSnapshot();
    if (this.panelProcess && this.panelProcess.exitCode === null) return { alreadyOpen: true };
    // This is an explicitly requested interactive window, never a hidden auto-approver.
    const child = spawn(this.powershell, ['-NoProfile', '-STA', '-File',
      path.join(this.fleet.sourceRoot, 'adapters/opencode/panel.ps1'), '-FleetHome', this.fleet.home],
    { windowsHide: false, stdio: 'ignore' });
    this.panelProcess = child;
    child.on('error', () => { this.panelProcess = null; });
    child.unref();
    return { requested: true, pid: child.pid, file: path.join(this.fleet.home, 'panel.json') };
  }

  async applyInbox() {
    const f = this.fleet;
    const s = f.state();
    if (!s || this.isStopped()) return;
    await this.applyJsonlInbox();
    if (this.isStopped()) return;
    const dir = path.join(f.home, 'inbox');
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (this.isStopped()) return;
      if (!entry.isFile() || !/^[a-f0-9-]{36}\.json$/.test(entry.name)) continue;
      if (f.state().inboxResults?.[entry.name]) continue;
      let result, stoppedBeforeStart = false, transactionEntered = false;
      try {
        const file = path.join(dir, entry.name);
        const answer = readJSON(file);
        if (typeof answer.qid !== 'string' || answer.captain !== s.captain || typeof answer.answer !== 'string' || !answer.answer.trim()) throw new Error('Invalid or stale answer');
        let duplicate = false;
        const validate = db => {
          if (this.isStopped()) { stoppedBeforeStart = true; throw new Error('Inbox stopped before transaction'); }
          transactionEntered = true;
          const q = (db.decisions ?? []).find(q => q.qid === answer.qid);
          if (!q || questionHash(q) !== answer.questionHash) throw new Error('Question changed; ask the user again');
          if (q.answer) {
            if (q.answer !== answer.answer) throw new Error('Conflicting answer; existing decision preserved');
            duplicate = true;
            throw new Error('Already applied');
          }
        };
        let summary = '';
        try { summary = await f.activator(s.captain, ['ask', 'answer', answer.qid, '--answer', answer.answer], validate); }
        catch (error) { if (stoppedBeforeStart) return; if (!duplicate) throw error; }
        result = { at: now(), qid: answer.qid, answer: answer.answer, summary: summary.trim(), state: duplicate ? 'duplicate' : 'applied' };
      } catch (error) { result = { at: now(), state: 'rejected', reason: error.message }; }
      await f.lock(async () => {
        if (this.isStopped() && !transactionEntered) return;
        const current = f.requireCaptain(s.captain, true);
        current.inboxResults ??= {};
        current.inboxResults[entry.name] = result;
        if (result.state === 'applied') f.addEvent(current, 'human_answer', `${result.qid}=${result.answer}\n${result.summary}`);
        if (result.state === 'rejected') f.addEvent(current, 'inbox_error', `${entry.name}:${result.state}`);
        f.save(current);
      });
    }
  }

  async applyJsonlInbox() {
    const f = this.fleet;
    if (this.isStopped() || !f.state()?.askCursor || !fs.existsSync(f.askInbox)) return;
    for (;;) {
      if (this.isStopped()) return;
      const bytes = fs.readFileSync(f.askInbox);
      const s = f.state();
      const cursor = s.askCursor;
      if (cursor.path !== f.askInbox || cursor.offset > bytes.length || digest(bytes.subarray(0, cursor.offset)) !== cursor.prefixHash) {
        // Upstream requires append-only input. Never replay rewritten history as
        // new human decisions. Preserve the cursor and report the broken contract.
        await f.lock(async () => {
          if (this.isStopped()) return;
          const current = f.state();
          f.addEvent(current, 'inbox_error', 'ask-inbox.jsonl is not append-only or its configured path changed; cursor preserved');
          f.save(current);
        });
        return;
      }
      const end = bytes.indexOf(10, cursor.offset);
      if (end === -1) return; // Wait for the complete UTF-8 JSONL record, including newline.
      const line = bytes.subarray(cursor.offset, end).toString('utf8').trim();
      let answer, result, recoveredSummary, duplicate = false, invalid = false, superseded = false;
      let stoppedBeforeStart = false, transactionEntered = false;
      try {
        answer = JSON.parse(line);
        if (!answer || typeof answer.qid !== 'string' || !answer.qid || typeof answer.answer !== 'string' || !answer.answer.trim()) throw new Error('Invalid answer record');
      } catch {
        result = { state: line ? 'rejected' : 'blank', reason: 'Invalid JSONL answer', at: now() };
      }
      if (!result) {
        const validate = db => {
          if (this.isStopped()) { stoppedBeforeStart = true; throw new Error('Inbox stopped before transaction'); }
          transactionEntered = true;
          const current = f.state();
          if (current.askCursor.offset !== cursor.offset) { superseded = true; throw new Error('Cursor advanced by another consumer'); }
          const q = (db.decisions ?? []).find(q => q.qid === answer.qid);
          if (!q) { invalid = true; throw new Error('Unknown question'); }
          const pending = current.askCursor.pending;
          if (pending && (pending.recordHash !== digest(line) || pending.questionHash !== questionHash(q))) {
            invalid = true; throw new Error('Record or question changed during interrupted delivery');
          }
          if (q.answer === answer.answer) {
            duplicate = true;
            if (pending) recoveredSummary = `Recovered recorded answer; ready tasks: ${db.tasks.filter(t => q.tasks.includes(t.id) && t.status === '待开工' && !t.blocker).map(t => t.id).join(', ')}`;
            throw new Error('Already applied');
          }
          if (pending && pending.previousAnswer !== q.answer) {
            invalid = true; throw new Error('Question changed during interrupted delivery');
          }
          // Persist preconditions before applying: a crash after the task write
          // can be retried without re-unlocking tasks or overwriting a later decision.
          current.askCursor.pending = { recordHash: digest(line), questionHash: questionHash(q), previousAnswer: q.answer };
          f.save(current);
        };
        try {
          const output = await f.activator(s.captain, ['ask', 'answer', answer.qid, '--answer', answer.answer], validate);
          result = { state: 'applied', qid: answer.qid, answer: answer.answer, summary: output.trim(), at: now() };
        } catch (error) {
          if (stoppedBeforeStart) return;
          if (superseded) continue;
          if (!duplicate && !invalid) throw error; // I/O/backend failures keep the cursor for retry.
          result = recoveredSummary ? { state: 'applied', qid: answer.qid, answer: answer.answer, summary: recoveredSummary, at: now() }
            : { state: duplicate ? 'duplicate' : 'rejected', qid: answer.qid, reason: duplicate ? 'Already applied' : 'Unknown or changed question', at: now() };
        }
      }
      await f.lock(async () => {
        if (this.isStopped() && !transactionEntered) return;
        const current = f.requireCaptain(s.captain, true);
        if (current.askCursor.offset !== cursor.offset) return;
        current.askCursor = { path: f.askInbox, offset: end + 1, prefixHash: digest(bytes.subarray(0, end + 1)), lastResult: result };
        if (result.state === 'applied') f.addEvent(current, 'human_answer', `${result.qid}=${result.answer}\n${result.summary}`);
        if (result.state === 'rejected') f.addEvent(current, 'inbox_error', `ask-inbox.jsonl byte ${cursor.offset}: ${result.reason}`);
        f.save(current);
      });
    }
  }

  async scrub(sessionID, { directory, termsFile, saltFile } = {}) {
    const f = this.fleet;
    f.requireCaptain(sessionID, true);
    const target = fs.realpathSync(directory ?? f.directory);
    if (!fs.statSync(target).isDirectory()) throw new Error('Scrub target must be a directory');
    if (!this.bash || !f.python) throw new Error('Scrub requires configured Bash and Python');
    const env = { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', STARFIX_PYTHON: f.python.replaceAll('\\', '/') };
    if (termsFile) env.SCRUB_TERMS = path.resolve(termsFile).replaceAll('\\', '/');
    if (saltFile) env.SCRUB_SALT = fs.readFileSync(fs.realpathSync(saltFile), 'utf8').replace(/[\r\n]+$/, '');
    const parent = path.join(f.home, '.scratch');
    fs.mkdirSync(parent, { recursive: true });
    const run = fs.mkdtempSync(path.join(parent, 'scrub-'));
    try {
      // Git's CRLF checkout is not executable Bash. Normalize a disposable copy,
      // preserving upstream predicates, exits, HMAC algorithm and exclusions.
      for (const name of ['scrub-gate.sh', 'scrub_terms_check.py']) {
        atomic(path.join(run, name), fs.readFileSync(path.join(f.sourceRoot, 'tools', name), 'utf8').replaceAll('\r\n', '\n'));
      }
      const command = 'python3() { "$STARFIX_PYTHON" -B "$@"; }; export -f python3; ' +
        'test -x /usr/bin/grep && command -v sed >/dev/null && python3 -c "import hmac, hashlib" || exit 2; ' +
        'bash "$1" "$2"';
      let result;
      try {
        const out = await exec(this.bash, ['-c', command, 'starfix-scrub', path.join(run, 'scrub-gate.sh').replaceAll('\\', '/'), target.replaceAll('\\', '/')],
          { cwd: f.directory, env, windowsHide: true, maxBuffer: Infinity });
        result = { code: 0, ...out };
      } catch (error) {
        if (![1, 2].includes(error.code)) throw new Error(`Scrub execution failed (${error.code ?? 'unknown'}); no PASS result`);
        result = { code: error.code, stdout: error.stdout ?? '', stderr: error.stderr ?? '' };
      }
      return { directory: target, exitCode: result.code, verdict: ['PASS', 'FAIL', 'UNAVAILABLE'][result.code],
        output: result.stdout, stderr: result.stderr,
        notice: 'Original scrub gate result. A degraded PASS excludes the private wordlist layer. No files were published or rewritten.' };
    } finally {
      if (path.dirname(fs.realpathSync(run)) !== fs.realpathSync(parent)) throw new Error('Scrub scratch boundary changed; cleanup stopped');
      fs.rmSync(run, { recursive: true });
      if (fs.existsSync(run)) throw new Error('Scrub scratch cleanup failed');
    }
  }

  async memory(sessionID, args) {
    const f = this.fleet;
    return f.lock(async () => {
      f.requireCaptain(sessionID, true);
      // Markdown is the original source of truth. Never regenerate all files from
      // a shadow JSON copy or reject edits made with the host's normal file tools.
      if (args.action === 'list') {
        const entries = [];
        for (const [layer, folder] of Object.entries(memoryLayers)) {
          const dir = path.join(f.home, folder);
          if (!fs.existsSync(dir)) continue;
          for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            if (entry.isFile() && entry.name.endsWith('.md')) entries.push({ layer, name: entry.name.slice(0, -3), path: path.join(dir, entry.name) });
          }
        }
        if (args.archive) return fs.readdirSync(f.home).filter(n => /^MEMORY-(archive|optin-).*\.md$/.test(n)).map(n => ({ path: path.join(f.home, n) }));
        if (fs.existsSync(path.join(f.home, 'daily_report_latest.md'))) entries.push({ layer: 'daily', name: 'latest', path: path.join(f.home, 'daily_report_latest.md') });
        return entries;
      }
      if (!memoryLayers[args.layer]) throw new Error('Unknown memory layer');
      identifier(args.name);
      const relative = args.layer === 'daily' ? 'daily_report_latest.md' : `${memoryLayers[args.layer]}/${args.name}.md`;
      const file = path.join(f.home, relative);
      const archive = path.join(f.home, `MEMORY-optin-${args.layer}-${args.name}.md`);
      const indexFile = path.join(f.home, 'MEMORY.md');
      let index = fs.existsSync(indexFile) ? fs.readFileSync(indexFile, 'utf8') : '# Memory Index\n';
      if (args.action === 'read') {
        const target = args.archive ? archive : file;
        return { layer: args.layer, name: args.name, path: target, content: fs.readFileSync(target, 'utf8'), archived: Boolean(args.archive) };
      }
      fs.mkdirSync(path.dirname(file), { recursive: true });
      if (args.action === 'put') {
        if (typeof args.content !== 'string') throw new Error('Memory content is required');
        if (args.layer === 'fact' && !['user', 'feedback', 'project', 'reference'].includes(args.type)) throw new Error('Fact memory needs a valid type');
        if (args.type === 'feedback' && (!/Why/i.test(args.content) || !/How to apply/i.test(args.content))) throw new Error('Feedback needs Why and How to apply');
        const content = args.layer === 'fact' && !args.content.startsWith('---') ? `---\ntype: ${args.type}\n---\n\n${args.content}` : args.content;
        atomic(file, content);
        if (!['daily', 'ledger'].includes(args.layer) && !index.includes(relative)) {
          const title = (args.title ?? args.name).replace(/[\r\n]/g, ' ');
          index += `\n- ${title}: ${relative}\n`;
        }
      } else if (args.action === 'archive' || args.action === 'restore') {
        const from = args.action === 'archive' ? file : archive;
        const to = args.action === 'archive' ? archive : file;
        const content = fs.readFileSync(from);
        if (fs.existsSync(to) && !fs.readFileSync(to).equals(content)) throw new Error('Destination contains different memory; reconcile without overwriting');
        atomic(to, content);
        fs.unlinkSync(from);
        const fromRef = args.action === 'archive' ? relative : path.basename(archive);
        const toRef = args.action === 'archive' ? path.basename(archive) : relative;
        index = index.split(fromRef).join(toRef);
        if (!index.includes(toRef)) index += `\n- ${args.name}: ${toRef}\n`;
      } else if (args.action === 'delete') {
        fs.unlinkSync(file);
        index = index.split('\n').filter(line => !line.includes(relative)).join('\n');
      } else throw new Error('Unknown memory action');
      atomic(indexFile, index);
      return { path: file, archived: args.action === 'archive', updated: now() };
    });
  }

  async monitor(sessionID, args) {
    const f = this.fleet;
    return f.lock(async () => {
      const s = f.requireCaptain(sessionID, true);
      s.monitors ??= {};
      if (args.action === 'list') return s.monitors;
      identifier(args.name);
      if (args.action === 'cancel') {
        if (!s.monitors[args.name]) throw new Error('Monitor not found');
        s.monitors[args.name].cancelled = true;
      } else {
        if (!args.message) throw new Error('Monitor needs a message');
        if (args.action === 'remind') {
          const at = Date.parse(args.at);
          if (!Number.isFinite(at) || !/(Z|[+-]\d\d:\d\d)$/.test(args.at)) throw new Error('Use an absolute ISO time with timezone');
          s.monitors[args.name] = { kind: 'remind', at, message: args.message };
        } else if (args.action === 'wait_file') {
          if (!path.isAbsolute(args.path)) throw new Error('Use an absolute file path');
          const file = path.resolve(args.path);
          s.monitors[args.name] = { kind: 'wait_file', path: file, message: args.message };
        } else throw new Error('Unknown monitor action');
      }
      f.save(s);
      atomic(path.join(f.home, 'monitors-latest.json'), JSON.stringify({ backend: 'OpenCode plugin timer and session events', intervalSeconds: 30, monitors: s.monitors, restore: 'Restart OpenCode in the captain project; registrations load from opencode-state.json' }, null, 2));
      return s.monitors[args.name];
    });
  }

  async scanMonitors() {
    const f = this.fleet;
    if (!f.state()) return;
    await f.lock(async () => {
      const s = f.state();
      // Migrate only adapter-created freezes; never override an explicit pause.
      if (['quota', 'wake_unconfirmed'].includes(s.pauseCause)) {
        if (s.pauseCause === 'wake_unconfirmed') s.autoWake = true;
        s.paused = false; s.pauseCause = null; s.reason = '';
      }
      if (s.pauseCause === 'captain_error') {
        // The old adapter overwrote autoWake, so its previous user choice is
        // unknowable. Restore observation but do not invent consent to wake.
        s.paused = false; s.pauseCause = null; s.reason = '';
        f.addEvent(s, 'legacy_captain_pause_cleared', 'Observers restored; inspect the saved wake preference before enabling model wake');
      }
      if (s.paused) return;
      for (const [name, m] of Object.entries(s.monitors ?? {})) {
        if (m.cancelled || m.fired) continue;
        if ((m.kind === 'remind' && Date.now() >= m.at) || (m.kind === 'wait_file' && fs.existsSync(m.path))) {
          // Persist firing with the event, so restart or acknowledgment cannot re-fire it.
          f.addEvent(s, 'scheduled', `${name}: ${m.message}`);
          if (!s.paused) m.fired = now();
        }
      }
      if (s.quota?.enabled) {
        const problem = f.quotaProblem(s);
        if (problem) {
          if (s.quota.blocked !== problem) f.addEvent(s, 'quota_blocked', problem);
          s.quota.blocked = problem;
        } else if (s.quota.blocked) {
          s.quota.blocked = null;
          f.addEvent(s, 'quota_reset', 'Account telemetry returned to GO below 95 percent');
        }
      }
      f.save(s);
    });
  }

  async collectQuota(force = false) {
    const f = this.fleet, config = f.state()?.quota;
    if (this.isStopped() || !config?.enabled || config.source !== 'codex' || (!force && Date.now() < (config.nextCollectionAt ?? 0))) return;
    const expected = quotaIdentity(config);
    let telemetry;
    try {
      if (!this.codex) throw new Error('Codex account collector unavailable');
      // The native account belongs to Codex workers. Only share it with
      // OpenCode sessions after independently checking their OAuth identity.
      try { config.accountVerified = sameOpenAIAccount(this.opencodeAuth, this.codexAuth); }
      catch { config.accountVerified = false; }
      telemetry = quotaTelemetry(await this.codex.rpc('account/rateLimits/read'), config.limitId ?? 'codex');
    } catch (error) {
      telemetry = { observedAt: now(), source: 'codex account/rateLimits/read', status: 'UNKNOWN', usedPercent: null, error: error.message };
    }
    let committed = false;
    await f.lock(async () => {
      const s = f.state();
      if (this.isStopped() || !s?.quota?.enabled || s.quota.source !== 'codex' || quotaIdentity(s.quota) !== expected) return;
      s.quota.accountVerified = config.accountVerified === true;
      atomic(path.join(f.home, 'quota.json'), JSON.stringify(telemetry, null, 2));
      s.quota.nextCollectionAt = Date.now() + Math.min(300000, s.quota.maxAgeSeconds * 500);
      f.save(s);
      committed = true;
    });
    return committed ? telemetry : undefined;
  }

  async quota(sessionID, { enabled, maxAgeSeconds = 300, source = 'external', limitId = 'codex' }) {
    const f = this.fleet;
    return f.lock(async () => {
      const s = f.requireCaptain(sessionID, true);
      if (!Number.isInteger(maxAgeSeconds) || maxAgeSeconds <= 0) throw new Error('Quota freshness must be a positive number of seconds');
      s.quota = { enabled, maxAgeSeconds, source, limitId, revision: crypto.randomUUID() };
      if (!enabled && s.pauseCause === 'quota') { s.paused = false; s.pauseCause = null; s.reason = ''; }
      f.save(s);
      return { ...s.quota, telemetryFile: path.join(f.home, 'quota.json'), notice: 'Real account telemetry only; never inferred from local call counts' };
    });
  }

  async trajectory(sessionID, { graph, inputs, model, inject = [], runTag = 'opencode' }) {
    const f = this.fleet;
    f.requireCaptain(sessionID);
    let file = fs.realpathSync(path.resolve(f.sourceRoot, 'trajectory/graphs', graph));
    const audit = f.state().audit?.profile && f.auditRuntime ? await f.auditRuntime.prepare() : null;
    if (audit) {
      const relative = path.relative(path.join(f.sourceRoot, 'trajectory'), file);
      if (!relative.startsWith('..') && !path.isAbsolute(relative)) file = path.join(audit.root, relative);
    }
    inputs = { ...inputs };
    for (const key of ['worktree', 'receipt']) {
      if (!inputs[key]) continue;
      const target = fs.realpathSync(inputs[key]);
      inputs[key] = target;
    }
    const run = path.join(f.home, 'trajectory', crypto.randomUUID());
    fs.mkdirSync(run, { recursive: true });
    await f.lock(async () => {
      const s = f.requireCaptain(sessionID, true);
      model ??= s.adjudicatorModel ?? s.model;
      if (model) s.adjudicatorModel = model;
      f.save(s);
    });
    const database = path.join(f.home, 'audit', 'runtime.db');
    fs.mkdirSync(path.dirname(database), { recursive: true });
    const request = { graph: file, inputs, workdir: run, database, trajectoryRoot: audit?.root, sourceRoot: f.sourceRoot, bash: this.bash, opencode: this.opencode, model, inject, runTag };
    atomic(path.join(run, 'request.json'), JSON.stringify(request));
    const { stdout } = await exec(f.python, ['-B', path.join(f.sourceRoot, 'adapters/opencode/trajectory_bridge.py'), path.join(run, 'request.json')],
      { cwd: f.directory, windowsHide: true, maxBuffer: Infinity, env: { ...process.env, ...audit?.env, FLEET_INTEGRATION_REPO: f.directory, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' } });
    const result = JSON.parse(stdout);
    atomic(path.join(run, 'result.json'), JSON.stringify(result, null, 2));
    await f.lock(async () => { const s = f.requireCaptain(sessionID, true); s.lastTrajectory = { run, overall: result.overall, at: now() }; f.save(s); });
    return { ...result, run };
  }

  async compile(sessionID, { action, source, args: extra = [] }) {
    const f = this.fleet;
    f.requireCaptain(sessionID);
    const input = fs.realpathSync(source);
    if (!['compile', 'validate', 'detect', 'update', 'induce', 'execute'].includes(action)) throw new Error('Unknown compiler action');
    const run = path.join(f.home, 'compiled', crypto.randomUUID());
    fs.mkdirSync(run, { recursive: true });
    const output = path.join(run, 'graph.json');
    const script = path.join(f.sourceRoot, action === 'induce' ? 'trajectory/inducer/induce.py' : action === 'execute' ? 'trajectory/skillc/skill_runner.py' : 'trajectory/skillc/skill_graph.py');
    const args = action === 'induce' ? [input, output] : action === 'compile' ? ['--compile', input, '--out', output] : action === 'execute' ? ['--blocks', input, '--cwd', f.directory] : [`--${action}`, input];
    const request = path.join(run, 'request.json');
    atomic(request, JSON.stringify({ script, args: [...args, ...extra], directory: f.directory, bash: this.bash, opencode: this.opencode, model: f.state().adjudicatorModel, logDir: path.join(f.home, 'compiled', 'logs') }));
    const { stdout } = await exec(f.python, ['-B', path.join(f.sourceRoot, 'adapters/opencode/platform_bridge.py'), request], { windowsHide: true, maxBuffer: Infinity,
      env: { ...process.env, FLEET_HOME: f.home, TEMP: run, TMP: run, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' } });
    atomic(path.join(run, 'result.txt'), stdout);
    return { run, output: fs.existsSync(output) ? output : null, result: stdout,
      notice: 'Original compiler/inducer executed. Compilation is not activation or proof that a workflow is safe to execute. Retain draft/invalidated state until its original validation gates are satisfied.' };
  }
}
