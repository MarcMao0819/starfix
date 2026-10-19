import fs from 'node:fs';
import path from 'node:path';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { once } from 'node:events';
import { atomic, inside, readJSON } from './runtime.mjs';

const exec = promisify(execFile);

export class AuditRuntime {
  constructor(fleet, { bash, opencode }) { this.fleet = fleet; this.bash = bash; this.opencode = opencode; this.child = null; fleet.auditRuntime = this; }

  async configure(sessionID, profile) {
    const f = this.fleet;
    if (this.child) throw new Error('Stop the audit runtime before changing dependency bindings');
    const file = fs.realpathSync(profile);
    const config = readJSON(file);
    for (const key of ['container', 'database', 'baseRef']) if (typeof config[key] !== 'string' || !config[key] || /[\r\n\0]/.test(config[key])) throw new Error(`Missing/invalid audit ${key}`);
    if (!Number.isInteger(config.baselineId) || config.baselineId < 0) throw new Error('Explicit baselineId required; do not inherit the author historical exemption');
    if (!Array.isArray(config.dockerCommand) || !config.dockerCommand.length || config.dockerCommand.some(s => typeof s !== 'string')) throw new Error('dockerCommand must contain the actual executable and arguments');
    if (config.snapshot) {
      if (![config.snapshot.minimumRegistrations, config.snapshot.minimumFingerprints].every(n => Number.isInteger(n) && n >= 0) || typeof config.snapshot.sentinelChangeset !== 'string' || !/^CS-\d{8}-\d{4}$/.test(config.snapshot.sentinelChangeset)) throw new Error('Snapshot measurements need explicit real counts and a standard sentinel changeset');
    }
    await f.lock(async () => {
      const s = f.requireCaptain(sessionID, true);
      s.audit = { profile: file, enabled: false, state: 'configured_not_started' };
      f.save(s);
    });
    this.prepared = null;
    return { profile: file, state: 'configured_not_started' };
  }

  async prepare() {
    const f = this.fleet, settings = f.state()?.audit;
    if (!f.state().adjudicatorModel && f.state().model) await f.lock(async () => {
      const s = f.state(); s.adjudicatorModel ??= s.model; f.save(s);
    });
    const directory = path.join(f.home, 'audit');
    fs.mkdirSync(directory, { recursive: true });
    const requestFile = path.join(directory, 'request.json');
    const request = JSON.stringify({ sourceRoot: f.sourceRoot, home: f.home, directory: f.directory, requestFile,
      config: settings?.profile ? readJSON(settings.profile) : {}, bash: this.bash, opencode: this.opencode, model: f.state().adjudicatorModel ?? f.state().model });
    if (fs.existsSync(path.join(directory, 'data/changeset-audit/.audit-poller.lock')) &&
        (!fs.existsSync(requestFile) || fs.readFileSync(requestFile, 'utf8') !== request)) {
      throw new Error('Existing audit lock: stop the poller before changing dependency bindings');
    }
    atomic(requestFile, request);
    const result = await exec(f.python, ['-B', path.join(f.sourceRoot, 'adapters/opencode/audit_bridge.py'), requestFile],
      { cwd: f.directory, windowsHide: true, maxBuffer: Infinity, env: { ...process.env, PYTHONUTF8: '1' } });
    const prepared = JSON.parse(result.stdout);
    this.prepared = { ...prepared, env: { ...process.env, ...prepared.env } };
    return this.prepared;
  }

  async start(sessionID) {
    if (this.disposed) throw new Error('Audit host disposed');
    const f = this.fleet;
    f.requireCaptain(sessionID, true);
    if (!f.state()?.audit?.profile) throw new Error('Audit poller needs a project dependency profile');
    if (this.child?.exitCode === null) return { running: true, pid: this.child.pid };
    const p = await this.prepare();
    if (this.disposed) throw new Error('Audit host disposed during preparation');
    const output = path.join(f.home, 'audit', 'poller.log');
    const log = fs.openSync(output, 'a', 0o600);
    try {
      this.child = spawn(this.bash, [path.join(p.root, 'runner/audit-poller.sh').replaceAll('\\', '/')],
        { cwd: f.directory, env: p.env, windowsHide: true, stdio: ['ignore', log, log] });
    } finally { fs.closeSync(log); }
    const child = this.child;
    this.exited = once(child, 'close').catch(() => {});
    child.on('error', () => {});
    try { await once(child, 'spawn'); }
    catch (error) { await this.exited; this.child = null; throw error; }
    await f.lock(async () => {
      const s = f.state(); s.audit.enabled = true; s.audit.state = 'starting'; s.audit.pid = child.pid; s.audit.log = output; f.save(s);
    });
    return { requested: true, pid: child.pid, log: output };
  }

  async stop(sessionID, preserve = false) {
    const f = this.fleet;
    if (sessionID) f.requireCaptain(sessionID, true);
    this.snapshotAbort?.abort();
    const child = this.child;
    if (child && child.exitCode === null) {
      // Only the process handle spawned by this instance is eligible. Never kill
      // a PID read from somebody else's state file after restart.
      if (process.platform === 'win32') {
        try { await exec('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }); }
        catch { if (child.exitCode === null) throw new Error('Audit process termination unconfirmed'); }
      } else child.kill();
    }
    if (this.exited) await this.exited;
    let lockReleased = null;
    if (child && this.prepared) {
      try {
        await exec(f.python, ['-B', path.join(f.sourceRoot, 'adapters/opencode/audit_bridge.py'), '--release-lock', path.join(f.home, 'audit/data/changeset-audit/.audit-poller.lock')],
          { env: this.prepared.env, cwd: f.directory, windowsHide: true });
        lockReleased = true;
      } catch { lockReleased = false; }
    }
    this.child = null;
    if (f.state()?.audit && !preserve) await f.lock(async () => { const s = f.state(); s.audit.enabled = false; s.audit.state = 'stopped'; f.save(s); });
    return { stopped: true, lockReleased };
  }

  async run(sessionID, script, args = [], options = {}) {
    this.fleet.requireCaptain(sessionID, true);
    const p = this.prepared ?? await this.prepare();
    const file = fs.realpathSync(path.resolve(p.root, script));
    if (!inside(p.root, file)) throw new Error('Audit program escapes prepared upstream tree');
    const binary = file.endsWith('.sh') ? this.bash : this.fleet.python;
    try {
      const result = await exec(binary, [...(file.endsWith('.py') ? ['-B'] : []), file.replaceAll('\\', '/'), ...args],
        { cwd: this.fleet.directory, env: p.env, windowsHide: true, maxBuffer: Infinity, signal: options.signal });
      return { exitCode: 0, output: result.stdout, stderr: result.stderr };
    } catch (error) {
      if (!Number.isInteger(error.code)) throw error;
      return { exitCode: error.code, output: error.stdout ?? '', stderr: error.stderr ?? '' };
    }
  }

  async poll() {
    const f = this.fleet, config = f.state()?.audit;
    if (this.disposed || !config?.enabled) return;
    if (this.child && this.child.exitCode !== null) {
      // Release only the lock of this instance's exited process, then rehang
      // the original poller. Its durable cursor prevents replaying old work.
      await this.stop(null, true);
    }
    if (this.disposed || !f.state()?.audit?.enabled) return;
    if (!this.child) await this.start(f.state().captain);
    const heartbeat = readJSON(path.join(f.home, 'audit/data/changeset-audit/.audit-poller.heartbeat'), null);
    const now = Date.now();
    const age = heartbeat ? now - Date.parse(heartbeat.ts) : Infinity;
    const alive = this.child.exitCode === null;
    const interval = Number(this.prepared?.env.AUDITPOLLER_INTERVAL ?? 60);
    const status = !alive ? 'DOWN' : !heartbeat ? 'STARTING' : age > Math.max(180000, interval * 3000) || !heartbeat.ok ? 'DOWN' : 'RUNNING';
    await f.lock(async () => {
      const s = f.state();
      if (s.audit.state !== status && ['DOWN'].includes(status)) f.addEvent(s, 'audit_error', `Original poller ${status}; inspect ${s.audit.log}`);
      s.audit.state = status; s.audit.heartbeat = heartbeat; s.audit.exitCode = this.child.exitCode;
      s.audit.checkedAt = new Date().toISOString(); f.save(s);
    });
    if (alive && heartbeat && now - (this.lastSnapshot ?? 0) > 60000) {
      this.lastSnapshot = now;
      this.snapshotAbort = new AbortController();
      const result = await this.run(f.state().captain, 'runner/status_snapshot.py', [], { signal: this.snapshotAbort.signal });
      await f.lock(async () => {
        const s = f.state();
        s.audit.snapshotExitCode = result.exitCode;
        if (result.exitCode !== 0) f.addEvent(s, 'audit_error', 'Original status snapshot failed; previous snapshot is stale');
        f.save(s);
      });
    }
  }

  status() {
    const settings = this.fleet.state()?.audit;
    return { ...settings, originalSnapshot: settings?.snapshotExitCode ? null : readJSON(path.join(this.fleet.home, 'audit/status.json'), null),
      notice: 'RUNNING describes the poller, not a passing business gate. Original snapshot/ledgers remain authoritative.' };
  }
}
