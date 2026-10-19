import fs from 'node:fs';
import path from 'node:path';
import { Fleet, readJSON } from './runtime.mjs';
import { Services } from './services.mjs';
import { CodexConnection, CodexRPCError } from './codex.mjs';
import { ChannelGuards } from './channels.mjs';
import { AuditRuntime } from './audit.mjs';

const json = (value) => JSON.stringify(value, null, 2);
const unavailable = [
  'Feishu/IM away relay excluded by user; local decision inbox is available',
  'Automatic quota collection covers verified matching OpenAI OAuth accounts; other providers require their own telemetry',
  'Project database, build/deploy gates and business heartbeat remain unconfigured; full parity is incomplete',
  'Codex native sessions are supported; arbitrary terminal keystrokes and other CLI backends are not integrated (installation is a separate fact)',
  'Codex two-turn persistence was tested; full multi-worker business tasks and long unattended endurance are not yet accepted',
];

export async function createStarfixPlugin(input, { tool, sourceRoot, dataRoot, python, bash, powershell, opencode, codex, opencodeAuth, codexAuth, intervalMs = 30000, shutdownTimeoutMs = 1000 }) {
  let disposed = false;
  const observerController = new AbortController();
  const fleet = new Fleet({ sourceRoot, dataRoot, directory: input.directory, python });
  const codexConnection = codex ? new CodexConnection({ binary: codex, directory: input.directory, home: fleet.home, onEvent: async message => {
    if (disposed) return;
    const state = fleet.state();
    const sid = message.params?.threadId ?? message.params?.thread?.id;
    if (!state || !Object.values(state.workers).some(w => w.harness === 'codex' && w.sessionID === sid)) return;
    if (message.id !== undefined) await fleet.lock(async () => {
      if (disposed) return;
      const s = fleet.state(); fleet.addEvent(s, 'codex_request', `${sid}:${message.id}:${message.method}`); fleet.save(s);
    });
    if (message.method === 'turn/started' || message.method === 'turn/completed') {
      await fleet.recordEvent({ type: 'session.status', properties: { sessionID: sid, status: { type: message.method === 'turn/started' ? 'busy' : 'idle' } } }, observerController.signal);
    } else if (message.method.startsWith('item/')) {
      await fleet.recordEvent({ type: 'message.part.updated', properties: { sessionID: sid } }, observerController.signal);
    }
  } }) : null;
  // Services can resume after an awaited local operation. The abort signal makes
  // the shutdown boundary durable even if that operation settles after dispose().
  const isStopped = () => disposed;
  const services = new Services(fleet, { bash, powershell, opencode, codex: codexConnection, opencodeAuth, codexAuth, isStopped, signal: observerController.signal });
  const channels = new ChannelGuards(fleet, codexConnection, { isStopped, signal: observerController.signal });
  const audit = new AuditRuntime(fleet, { bash, opencode });
  const clientFor = worker => worker?.harness === 'codex' ? codexConnection?.client(worker.model) : input.client;
  const observed = new Map();
  function fleetFor(sessionID) {
    if (!sessionID) return null;
    const owns = s => s && (s.captain === sessionID || Object.values(s.workers).some(w => w.sessionID === sessionID));
    if (owns(fleet.state())) return fleet;
    if (!fs.existsSync(dataRoot)) return null;
    for (const entry of fs.readdirSync(dataRoot, { withFileTypes: true })) {
      if (!entry.isDirectory() || !/^[a-f0-9]{20}$/.test(entry.name)) continue;
      const state = readJSON(path.join(dataRoot, entry.name, 'opencode-state.json'), null);
      if (owns(state)) {
        const owner = new Fleet({ sourceRoot, dataRoot, directory: state.directory, python });
        if (path.basename(owner.home) === entry.name) return owner;
      }
    }
    return null;
  }
  let busy = false;
  let pendingAudit = null;
  const pendingWorkers = new Map();
  // Instance-local negative evidence. No fleet identity/history/configuration is replaced.
  // Restart permits one new observation; explicit tools always retry against new evidence.
  const missingResumes = new Map(), resumeAttempts = new Map();
  async function resumeWorker(worker, { background = false, reclaim = false } = {}) {
    if (background && disposed) return false;
    const id = worker.sessionID;
    const identity = JSON.stringify([id, worker.directory, worker.model, worker.reasoningEffort]);
    if (background && missingResumes.get(id)?.identity === identity) return false;
    missingResumes.delete(id);
    const attempt = Symbol(); resumeAttempts.set(id, attempt);
    try {
      const result = await codexConnection.resume(id, worker.model, { reclaim });
      if (resumeAttempts.get(id) === attempt) missingResumes.delete(id);
      return result;
    } catch (error) {
      if (!disposed && resumeAttempts.get(id) === attempt && error instanceof CodexRPCError && error.category === 'MISSING_ROLLOUT') {
        missingResumes.set(id, { identity });
      }
      throw error;
    }
  }
  let pendingStalls = null;
  let nextStallRound = 0;
  const reportError = async (error) => {
    // Do not log tool arguments, provider configuration, message bodies or credentials.
    try { await input.client.app.log({ body: { service: 'starfix', level: 'warn', message: 'StarFix background operation failed; inspect status before resuming', extra: { code: String(error.code ?? 'ADAPTER_ERROR') } } }); }
    catch { /* Logging failure must not trigger model calls or recursive plugin errors. */ }
  };
  async function tick() {
    if (disposed || busy || !fleet.state()) return;
    busy = true;
    try {
      services.panelSnapshot();
      // Audit can legitimately run longer than a monitor tick. Keep one audit
      // in flight without imposing a new deadline on upstream business graphs.
      if (!pendingAudit) pendingAudit = audit.poll().catch(async error => {
        if (disposed) return;
        try { await fleet.lock(async () => { const s = fleet.state(); fleet.addEvent(s, 'monitor_error', 'audit'); fleet.save(s); }); }
        catch { /* Logging cannot prevent other observers from running. */ }
        await reportError(error);
      }).finally(() => { pendingAudit = null; });
      await Promise.all([
        ['channels', () => channels.scan()],
        ['inbox', () => services.applyInbox()],
        ['quota', async () => { await services.collectQuota(); if (!disposed) await services.scanMonitors(); }],
      ].map(async ([name, check]) => {
        try { await check(); }
        catch (error) {
          if (disposed) return;
          try { await fleet.lock(async () => { const s = fleet.state(); fleet.addEvent(s, 'monitor_error', name); fleet.save(s); }); }
          catch { /* Keep waiting for the other observers even if state storage failed. */ }
          await reportError(error);
        }
      }));
      if (disposed || fleet.state().paused) return;
      // Upstream sentinel sleeps 600 seconds between rounds. Its directory walk
      // must not become a new prerequisite for fast receipt/session observers.
      if (!pendingStalls && Date.now() >= nextStallRound) {
        pendingStalls = fleet.scanStalls(observerController.signal).catch(async error => {
          if (!disposed) await reportError(error);
        }).finally(() => { pendingStalls = null; nextStallRound = Date.now() + 600000; });
      }
      if (fleet.state().captainUnavailable === 'session.error') {
        try {
          const s = fleet.state();
          const info = await input.client.session.get({ path: { id: s.captain }, query: { directory: fleet.directory }, signal: AbortSignal.timeout(15000), throwOnError: true });
          if (disposed) return;
          const status = await input.client.session.status({ query: { directory: fleet.directory }, signal: AbortSignal.timeout(15000), throwOnError: true });
          if (disposed) return;
          if (!info.error && info.data?.id === s.captain && !status.error && status.data &&
              (!status.data[s.captain] || ['idle', 'busy'].includes(status.data[s.captain].type))) {
            await fleet.recordEvent({ type: 'session.status', properties: { sessionID: s.captain, status: status.data[s.captain] ?? { type: 'idle' } } }, observerController.signal);
          }
        } catch { /* Failed readback leaves only captain delivery suspended. */ }
      }
      for (const worker of Object.values(fleet.state().workers)) {
        if (disposed) break;
        if (!worker.sessionID || worker.released || pendingWorkers.has(worker.sessionID)) continue;
        const controller = new AbortController();
        const signal = controller.signal;
        // One read-only observer per worker. A stuck transport must neither hold
        // the fleet tick nor accumulate duplicate requests on subsequent ticks.
        const pending = (async () => {
          try {
            signal.throwIfAborted();
            if (worker.harness === 'codex' && !codexConnection?.threads.has(worker.sessionID) &&
                await resumeWorker(worker, { background: true }) === false) return;
            signal.throwIfAborted();
            const client = clientFor(worker);
            await fleet.confirmDeliveries(worker.name, client, AbortSignal.any([signal, AbortSignal.timeout(15000)]));
            signal.throwIfAborted();
            const result = await client.session.status({ query: { directory: worker.directory }, path: { id: worker.sessionID }, signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), throwOnError: true });
            signal.throwIfAborted();
            if (result.error || !result.data) throw new Error('Worker status unavailable');
            const status = result.data[worker.sessionID]?.type ?? 'idle';
            // OpenCode omits idle sessions from its status map. Check existence separately.
            const session = await client.session.get({ query: { directory: worker.directory }, path: { id: worker.sessionID }, signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), throwOnError: true });
            signal.throwIfAborted();
            if (session.error || session.data?.id !== worker.sessionID) throw new Error('Worker identity unavailable');
            if (status !== worker.status) await fleet.recordEvent({ type: 'session.status', properties: { sessionID: worker.sessionID, status: { type: status } } }, controller.signal);
          } catch (error) {
            if (disposed) return;
            await fleet.recordEvent({ type: 'session.status', properties: { sessionID: worker.sessionID, status: { type: 'unavailable' } } }, controller.signal);
            await reportError(error);
          }
        })().catch(reportError).finally(() => pendingWorkers.delete(worker.sessionID));
        pendingWorkers.set(worker.sessionID, { pending, controller });
      }
      if (disposed) return;
      await fleet.scan({ stalls: false });
      if (disposed) return;
      await fleet.hourlyReport();
      if (disposed) return;
      await fleet.flushNotifications(input.client);
      if (disposed) return;
      await fleet.wake(input.client);
      if (!disposed) services.panelSnapshot();
    } catch (error) {
      if (disposed) return;
      // No automatic retries of external writes. Read-side errors are visible in status.
      try {
        await fleet.lock(async () => {
          const s = fleet.state();
          fleet.addEvent(s, 'monitor_error', String(error.code ?? 'check-status'));
          fleet.save(s);
        });
      } catch { /* A held writer lock is left intact for its owner. */ }
      await reportError(error);
    } finally { busy = false; }
  }
  let pendingTick = Promise.resolve();
  const timer = intervalMs > 0 ? setInterval(() => { if (!busy) pendingTick = tick().catch(reportError); }, intervalMs) : null;
  timer?.unref?.();

  let shutdownOperation;
  const shutdownWait = Number.isFinite(shutdownTimeoutMs) ? Math.max(1, Math.min(10000, shutdownTimeoutMs)) : 1000;
  async function settledWithin(promise) {
    let timeout;
    try { return await Promise.race([Promise.resolve(promise).then(() => true, () => true),
      new Promise(resolve => { timeout = setTimeout(() => resolve(false), shutdownWait); })]); }
    finally { clearTimeout(timeout); }
  }
  async function shutdown() {
    if (!shutdownOperation) shutdownOperation = drainShutdown();
    return shutdownOperation;
  }
  async function drainShutdown() {
    // Latch service observers before awaiting any shutdown work. Their in-flight
    // reads may settle later, but must not begin a repair or next inbox transaction.
    services.stop();
    channels.stop();
    disposed = true;
    audit.disposed = true;
    if (timer) clearInterval(timer);
    // Abort only observation. Never interrupt a native model turn on UI exit,
    // and do not wait forever for a transport that ignores cancellation.
    for (const { controller } of pendingWorkers.values()) controller.abort();
    observerController.abort();
    // No observer can register after disposed. Snapshot real promises, not just abort flags.
    const workers = [...pendingWorkers.values()].map(entry => entry.pending);
    const [tickSettled, workersSettled, stallsSettled] = await Promise.all([
      settledWithin(pendingTick), settledWithin(Promise.allSettled(workers)), settledWithin(pendingStalls),
    ]);
    // A late channel repair must settle before detaching its transport. Returning a
    // bounded, explicit incomplete report does not claim that ignored cancellation finished.
    const finishing = Promise.resolve(pendingTick).catch(() => {}).then(async () => {
      await audit.stop(null, true);
      await pendingAudit;
      await codexConnection?.disconnect(); // UI socket only; never stop the native host.
      return true;
    }).catch(async error => { await reportError(error); return false; });
    const transportSettled = await settledWithin(finishing) && await finishing;
    const result = { tickSettled, workersSettled, stallsSettled, transportSettled,
      pendingObservers: pendingWorkers.size, observationSettled: tickSettled && workersSettled && stallsSettled && transportSettled };
    if (!result.observationSettled) void reportError({ code: 'SHUTDOWN_OBSERVATION_UNSETTLED' });
    return result;
  }

  async function approval(context, action, details) {
    if (typeof context.ask !== 'function') throw new Error('OpenCode permission API unavailable; refusing mutation');
    await context.ask({ permission: `starfix_${action}`, patterns: [fleet.directory], always: [fleet.directory], metadata: { project: fleet.directory, fleetHome: fleet.home, ...details } });
  }

  function contextText(sessionID, owner) {
    const s = owner.state();
    const captain = s.captain === sessionID;
    const worker = Object.values(s.workers).find(w => w.sessionID === sessionID);
    const references = [
      path.join(owner.home, 'handoff', `HANDOFF-${sessionID}.md`),
      path.join(owner.home, 'handoff', `SNAPSHOT-${sessionID}.json`),
      ...(captain ? [path.join(owner.home, 'MEMORY.md')] : []),
      ...(captain && s.previousCaptain && !fs.existsSync(path.join(owner.home, 'handoff', `HANDOFF-${sessionID}.md`)) ? [path.join(owner.home, 'handoff', `HANDOFF-${s.previousCaptain}.md`), path.join(owner.home, 'handoff', `SNAPSHOT-${s.previousCaptain}.json`)] : []),
    ].filter(p => fs.existsSync(p)).map(p => `Saved session context, ${p}:\n${fs.readFileSync(p, 'utf8')}`);
    if (!captain) return [
      `StarFix worker: ${worker.name}. Your own OpenCode session: ${sessionID}. Captain: ${s.captain}.`,
      `FLEET_HOME: ${owner.home}. Profile: ${worker.profile ?? 'not yet evaluated'}.`,
      'Continue in your own session context and follow the assigned task book. starfix_handoff saves only your session handoff. Do not assume the captain role.',
      'After writing the receipt, call starfix_notify with its task ID, absolute receipt path and keyword. It delivers to your captain through native history, not task completion or Owner authorization.',
      ...references,
    ].join('\n\n');
    return [
      owner.readDocument('skill/SKILL.md'),
      'Windows / OpenCode dependency mapping (not replacement workflow rules):',
      `Source: ${owner.sourceRoot}\nProject: ${owner.directory}\nFLEET_HOME: ${owner.home}`,
      `Captain session: ${s.captain}. Paused: ${s.paused}; automatic wake: ${s.autoWake}.`,
      '- Original role separation, task contracts, acceptance, decision boundaries and crew evaluation remain unchanged. Host/user/project instructions still apply.',
      '- Terminal discovery and roster: starfix_sessions. Same-harness delivery: starfix_worker registers or creates a session, starfix_dispatch sends to that session and checks its history. Reuse idle workers; choose by their profiles.',
      '- Each worker has its own model, agent and native conversation history. New workers can snapshot the captain selection once; changing the captain never changes workers. Use starfix_worker configure to change a worker explicitly. Profiles are separated by window/model/harness identity, never mix evaluations.',
      '- Native reasoning settings are per worker: OpenCode variant names come from starfix_sessions models[].variants; Codex reasoningEffort values come from its native model/list. Preserve existing session settings when registering. Configure affects future turns; never restart an active Codex turn just to change effort. Agent is not reasoning effort.',
      '- starfix_dispatch dryRun:true prints the selected channel protocol without sending. Busy API sessions can receive follow-ups; Codex uses native turn/steer. starfix_notify is the worker return channel. Native Codex workers receive the equivalent file-mailbox CLI in their task message.',
      '- starfix_worker interrupt stops only that worker current turn; release stops automatic control for human takeover without deleting history, and reclaim resumes control only after the other controller exits.',
      '- Set harness:codex on starfix_worker to create or register a native Codex CLI thread. Existing Codex threads use their own recorded model. starfix_codex lists native requests and forwards responses only through host approval. No simulated keystrokes or fresh context per task.',
      '- starfix_task forwards original task-activator.py arguments. Normal source scripts and file tools remain available. Memory Markdown files are authoritative; starfix_memory is a convenience, not the only editing channel.',
      '- starfix_panel replaces the macOS decision window with Windows UI. Feishu is excluded by the Owner. Business-specific deployment/database/heartbeat targets are not configured in this generic installation; this does not prohibit configuring them later.',
      '- 舰长决策题只有一个入口：用 starfix_task 的 ask add 登记到面板，不再调用 OpenCode question 重复提问。面板答复产生 human_answer 事件；通过 starfix_status 读取用户完整答复，保留条件和补充说明，不用选项摘要覆盖原文。可执行的任务继续推进；若其余任务都在等用户，结束当前回合，由既有事件唤醒接续；自动唤醒关闭时等待用户下一条消息。命令权限审批仍由原生权限系统独立处理，面板答案不能代批。',
      '- Original append-only ask-inbox.jsonl is consumed with a durable cursor. Install the monitor before appending answers; never rewrite its history. The existing Windows panel inbox is also supported.',
      '- Before sharing any memory or source, starfix_scrub runs the original tools/scrub-gate.sh. Missing private wordlist means structural-only coverage, not a full privacy check.',
      '- starfix_monitor and native session events replace host monitor dependencies. Activating this workflow enables event-driven wake by default; autoWake:false disables it. Pause/resume preserves the selected mode. OpenCode must remain running, and wake uses model quota.',
      '- Read adapters/opencode/FIDELITY-AUDIT.md for verified mappings and remaining platform gaps. Do not describe unverified features as equivalent.',
      ...references,
    ].join('\n\n');
  }

  return {
    dispose: shutdown,
    tool: {
      starfix_activate: tool({
        description: 'Activate the original resident-captain workflow in this chat. Event wake is enabled by default and uses model quota while OpenCode runs; autoWake:false disables it. Installation alone never activates it.',
        args: { takeover: tool.schema.boolean().optional(), autoWake: tool.schema.boolean().optional() },
        async execute(args, context) {
          await approval(context, 'activate', { takeover: args.takeover ?? false, autoWake: args.autoWake ?? true });
          const result = await fleet.activate(context.sessionID, args.takeover ?? false, args.autoWake);
          const selected = observed.get(context.sessionID);
          if (selected) await fleet.rememberModel(context.sessionID, selected.model, selected.agent, { variant: selected.variant });
          if (codexConnection && !fleet.state().quota) await services.quota(context.sessionID, { enabled: true, source: 'codex' });
          if (codexConnection && !fleet.state().channels?.['codex-v1']) await channels.configure(context.sessionID, { action: 'register', name: 'codex-v1', kind: 'codex' });
          await services.collectQuota();
          await services.scanMonitors();
          services.panelSnapshot();
          return json(result);
        },
      }),
      starfix_read: tool({
        description: 'Read any file within the original StarFix source tree without changing it.',
        args: { path: tool.schema.string() },
        async execute(args) { return fleet.readDocument(args.path); },
      }),
      starfix_status: tool({
        description: 'Read fleet status and unread events. An idle session is not proof of task completion.',
        args: {},
        async execute(args, context) {
          if (fleet.state()?.captain !== context.sessionID) return json({ activeInThisSession: false, source: fleet.sourceRoot, unavailable });
          const s = fleet.requireCaptain(context.sessionID, true);
          const db = JSON.parse(fs.existsSync(fleet.dbFile) ? fs.readFileSync(fleet.dbFile, 'utf8') : '{"tasks":[]}');
          const { memories, memoryViewHashes, ...status } = s;
          return json({ activeInThisSession: true, home: fleet.home, ...status,
            memoryCount: (await services.memory(context.sessionID, { action: 'list' })).length, tasks: db.tasks, decisions: db.decisions ?? [], unavailable });
        },
      }),
      starfix_control: tool({
        description: 'Pause/resume scheduling without resetting wake configuration, or enable/disable event-driven wake using the captain model.',
        args: { action: tool.schema.enum(['pause', 'resume', 'wake_on', 'wake_off']) },
        async execute(args, context) {
          await approval(context, 'control', args);
          const result = await fleet.control(context.sessionID, args.action);
          services.panelSnapshot();
          return json(result);
        },
      }),
      starfix_task: tool({
        description: 'Run original task-activator.py arguments unchanged, including --force, --reg and argparse syntax. Captain decisions use ask add and the panel; read full answers with starfix_status, do not repeat them through question. Native permission approval remains separate. Backend checks remain original, not bypassed.',
        args: { args: tool.schema.array(tool.schema.string()) },
        async execute(args, context) {
          await approval(context, 'task', { args: args.args });
          const result = await fleet.activator(context.sessionID, args.args);
          services.panelSnapshot();
          return result;
        },
      }),
      starfix_sessions: tool({
        description: 'Discover OpenCode sessions or Codex native threads and model IDs, or inspect a registered worker actual message history by name. This replaces terminal roster and read-screen dependencies; history is evidence, not task authorization.',
        args: { directory: tool.schema.string().optional(), worker: tool.schema.string().optional(), harness: tool.schema.enum(['opencode', 'codex']).optional(), cursor: tool.schema.string().optional() },
        async execute(args, context) {
          const state = fleet.requireCaptain(context.sessionID, true);
          if (args.worker) {
            const worker = state.workers[args.worker];
            if (!worker?.sessionID) throw new Error('Worker is not registered');
            if (worker.harness === 'codex' && !worker.released && !codexConnection.threads.has(worker.sessionID)) await resumeWorker(worker);
            const client = clientFor(worker);
            const session = await client.session.get({ path: { id: worker.sessionID }, query: { directory: worker.directory }, throwOnError: true });
            const messages = await client.session.messages({ path: { id: worker.sessionID }, query: { directory: worker.directory }, throwOnError: true });
            return json({ worker, session: session.data, messages: messages.data });
          }
          const directory = args.directory ?? fleet.directory;
          if (args.harness === 'codex') {
            if (!codexConnection) throw new Error('Codex CLI is not configured');
            const roster = { harness: 'codex', directory, ...(await codexConnection.rpc('thread/list', { cwd: directory, cursor: args.cursor })), models: await codexConnection.rpc('model/list') };
            const { atomic } = await import('./runtime.mjs');
            atomic(path.join(fleet.home, 'fleet-snapshot-codex.json'), json(roster));
            return json(roster);
          }
          const sessions = await input.client.session.list({ query: { directory }, throwOnError: true });
          const status = await input.client.session.status({ query: { directory }, throwOnError: true });
          const providers = await input.client.provider.list({ query: { directory }, throwOnError: true });
          // Expose identities, never provider options or credential configuration.
          const models = providers.data.all.map(p => ({ providerID: p.id, models: Object.keys(p.models),
            variants: Object.fromEntries(Object.entries(p.models).map(([id, model]) => [id, Object.entries(model.variants ?? {}).filter(([, v]) => !v.disabled).map(([name]) => name)])) }));
          const roster = { directory, sessions: sessions.data.map(s => ({ id: s.id, title: s.title, directory: s.directory, status: status.data[s.id]?.type ?? 'idle' })), models };
          const { atomic } = await import('./runtime.mjs');
          atomic(path.join(fleet.home, 'fleet-snapshot.json'), json(roster));
          return json(roster);
        },
      }),
      starfix_worker: tool({
        description: 'Create, register or configure a worker with its OWN model, history and reasoning settings. OpenCode uses variant (empty string selects native default); Codex uses reasoningEffort. Choose native supported values from starfix_sessions, not a fixed adapter enum. Omitted settings preserve the same model settings; switching model clears an omitted incompatible override. New settings apply to later turns, not active Codex steering. interrupt/release/reclaim preserve native history. New workers copy the captain model once if omitted. Original single-writer rule still applies.',
        args: { name: tool.schema.string(), harness: tool.schema.enum(['opencode', 'codex']).optional(), action: tool.schema.enum(['create', 'register', 'configure', 'interrupt', 'release', 'reclaim']).optional(), directory: tool.schema.string().optional(), sessionID: tool.schema.string().optional(), recoverSessionID: tool.schema.string().optional(),
          model: tool.schema.object({ providerID: tool.schema.string(), modelID: tool.schema.string() }).optional(), agent: tool.schema.string().optional(),
          variant: tool.schema.string().optional(), reasoningEffort: tool.schema.string().min(1).optional() },
        async execute(args, context) {
          await approval(context, 'worker', args);
          const state = fleet.requireCaptain(context.sessionID, true);
          const existing = state.workers[args.name];
          const harness = existing?.harness ?? args.harness ?? 'opencode';
          const settings = { variant: args.variant, reasoningEffort: args.reasoningEffort };
          if (harness === 'codex' && args.variant !== undefined) throw new Error('Codex uses reasoningEffort, not an OpenCode variant');
          if (harness === 'opencode' && args.reasoningEffort !== undefined) throw new Error('OpenCode uses its native variant name; inspect starfix_sessions variants');
          if (['interrupt', 'release', 'reclaim'].includes(args.action)) {
            if (!existing?.sessionID) throw new Error('Worker is not registered');
            if (args.action === 'release') await fleet.lock(async () => {
              const s = fleet.state(); s.workers[args.name].released = true; fleet.save(s);
            });
            if (args.action === 'reclaim') {
              if (harness === 'codex') await resumeWorker(existing, { reclaim: true });
              else await input.client.session.get({ path: { id: existing.sessionID }, query: { directory: existing.directory }, throwOnError: true });
              await fleet.lock(async () => { const s = fleet.state(); s.workers[args.name].released = false; fleet.save(s); });
              return json({ sessionID: existing.sessionID, reclaimed: true });
            }
            if (harness === 'codex') return json(await codexConnection[args.action](existing.sessionID));
            await input.client.session.abort({ path: { id: existing.sessionID }, query: { directory: existing.directory }, throwOnError: true });
            const status = await input.client.session.status({ query: { directory: existing.directory }, throwOnError: true });
            if (status.error || !status.data || (status.data[existing.sessionID] && status.data[existing.sessionID].type !== 'idle')) throw new Error('Interrupt requested; worker stop unconfirmed');
            return json({ sessionID: existing.sessionID, interrupted: true, released: args.action === 'release', command: [opencode, '--session', existing.sessionID, existing.directory] });
          }
          let model = args.model ?? (args.action === 'configure' ? existing?.model : undefined);
          if (harness === 'codex') {
            if (!codexConnection) throw new Error('Codex CLI is not configured');
            if (args.action === 'register' || args.recoverSessionID) {
              const resumed = await codexConnection.resume(args.sessionID ?? args.recoverSessionID, model);
              model ??= { providerID: resumed.modelProvider, modelID: resumed.model };
              if (settings.reasoningEffort === undefined && resumed.reasoningEffort != null) settings.reasoningEffort = resumed.reasoningEffort;
            } else model ??= existing?.model ?? state.model;
            if (args.reasoningEffort !== undefined) {
              const catalog = await codexConnection.rpc('model/list', { includeHidden: true });
              const entry = catalog.data?.find(m => m.model === model?.modelID || m.id === model?.modelID);
              if (entry?.supportedReasoningEfforts && !entry.supportedReasoningEfforts.some(e => e.reasoningEffort === args.reasoningEffort)) throw new Error('Reasoning effort is not advertised by this native model; inspect starfix_sessions');
            }
          }
          const client = harness === 'codex' ? codexConnection.client(model) : input.client;
          if (harness === 'opencode' && args.variant !== undefined && !model && args.action === 'register') {
            const history = await client.session.messages({ path: { id: args.sessionID }, query: { directory: args.directory ?? fleet.directory }, throwOnError: true });
            model = history.data?.findLast(m => m.info.role === 'user' && m.info.model)?.info.model;
          }
          if ((model || args.variant !== undefined) && harness === 'opencode') {
            const providers = await input.client.provider.list({ query: { directory: args.directory ?? fleet.directory }, throwOnError: true });
            const target = model ?? state.model;
            const selected = providers.data?.all?.find(p => p.id === target?.providerID)?.models[target?.modelID];
            if (!selected) throw new Error('Selected provider/model is not available in OpenCode');
            if (args.variant && (!Object.hasOwn(selected.variants ?? {}, args.variant) || selected.variants[args.variant].disabled)) throw new Error('Variant is not available for this OpenCode model; inspect starfix_sessions');
          }
          if (args.recoverSessionID) return json(await fleet.recoverWorker(context.sessionID, args.name, args.recoverSessionID, client));
          if (args.action === 'configure') return json(await fleet.configureWorker(context.sessionID, args.name, model, args.agent, settings));
          if (args.action === 'register') {
            if (!args.sessionID) throw new Error('Copy the session ID from starfix_sessions');
            return json(await fleet.registerWorker(context.sessionID, args.name, args.directory ?? fleet.directory, args.sessionID, client, model, args.agent, settings));
          }
          return json(await fleet.createWorker(context.sessionID, args.name, args.directory ?? fleet.directory, client, model, args.agent, settings));
        },
      }),
      starfix_dispatch: tool({
        description: 'Deliver using the worker own model and context, including busy sessions (native Codex turn/steer). dryRun previews the protocol without sending or writing. keyword, when supplied, must occur in the message. Same-task follow-ups are allowed; identical requests are deduplicated, with a new deliveryID for intentional repeats after prior confirmation.',
        args: { worker: tool.schema.string(), taskID: tool.schema.string(), message: tool.schema.string(), deliveryID: tool.schema.string().optional(), dryRun: tool.schema.boolean().optional(), keyword: tool.schema.string().optional() },
        async execute(args, context) {
          if (args.dryRun) return json(await fleet.dispatch(context.sessionID, args.worker, args.taskID, args.message, null, args.deliveryID, args));
          await approval(context, 'dispatch', { worker: args.worker, taskID: args.taskID, message: args.message });
          await services.collectQuota();
          const worker = fleet.requireCaptain(context.sessionID).workers[args.worker];
          if (worker?.released) throw new Error('Worker is under manual control; reclaim it after the other controller exits');
            if (worker?.harness === 'codex' && !codexConnection.threads.has(worker.sessionID)) await resumeWorker(worker);
          return json(await fleet.dispatch(context.sessionID, args.worker, args.taskID, args.message, clientFor(worker), args.deliveryID, args));
        },
      }),
      starfix_notify: tool({
        description: 'Worker return channel: send task ID, receipt path and keyword to this fleet captain. Durable queued messages are confirmed from native history; no blind resend, no task completion or Owner authorization.',
        args: { taskID: tool.schema.string(), message: tool.schema.string(), deliveryID: tool.schema.string().optional() },
        async execute(args, context) {
          await approval(context, 'notify', { taskID: args.taskID });
          const owner = fleetFor(context.sessionID);
          if (!owner) throw new Error('Session is not in a fleet');
          const result = await owner.notify(context.sessionID, args.taskID, args.message, args.deliveryID);
          await owner.flushNotifications(input.client);
          return json(owner.state().notifications[result.id]);
        },
      }),
      starfix_handoff: tool({
        description: 'Save current judgment, open decisions, next actions and safety constraints, plus a mechanical task snapshot. Do not include secrets.',
        args: { note: tool.schema.string() },
        async execute(args, context) {
          await approval(context, 'handoff', {});
          return json(await (fleetFor(context.sessionID) ?? fleet).handoff(context.sessionID, args.note));
        },
      }),
      starfix_ack: tool({
        description: 'Acknowledge only events whose actual evidence has been inspected and handled; does not mark tasks complete.',
        args: { ids: tool.schema.array(tool.schema.string()) },
        async execute(args, context) {
          await approval(context, 'ack', { count: args.ids.length });
          return json(await fleet.acknowledge(context.sessionID, args.ids));
        },
      }),
      starfix_panel: tool({
        description: 'Open the local Windows decision/status panel. Answers require a user gesture and are checked against the displayed question. No IM connection.',
        args: {},
        async execute(args, context) {
          await approval(context, 'panel', {});
          return json(services.openPanel(context.sessionID));
        },
      }),
      starfix_memory: tool({
        description: 'Manage original six-layer fleet memory: index, typed facts, local doctrine, crew/person profiles, ledger/daily report, and explicit opt-in archive. Never store secrets.',
        args: { action: tool.schema.enum(['list', 'read', 'put', 'archive', 'restore', 'delete']),
          layer: tool.schema.enum(['fact', 'crew', 'person', 'doctrine', 'ledger', 'daily']).optional(),
          name: tool.schema.string().optional(), title: tool.schema.string().optional(),
          content: tool.schema.string().optional(), type: tool.schema.enum(['user', 'feedback', 'project', 'reference']).optional(),
          archive: tool.schema.boolean().optional() },
        async execute(args, context) {
          if (!['list', 'read'].includes(args.action) || args.archive) await approval(context, 'memory', args);
          return json(await services.memory(context.sessionID, args));
        },
      }),
      starfix_monitor: tool({
        description: 'Persist one-shot timed reminders and file-appearance monitors across host restarts. Requires the OpenCode host to remain running to fire.',
        args: { action: tool.schema.enum(['list', 'remind', 'wait_file', 'cancel']), name: tool.schema.string().optional(),
          at: tool.schema.string().optional(), path: tool.schema.string().optional(), message: tool.schema.string().optional() },
        async execute(args, context) {
          if (args.action !== 'list') await approval(context, 'monitor', args);
          return json(await services.monitor(context.sessionID, args));
        },
      }),
      starfix_quota: tool({
        description: 'Configure actual account quota collection via Codex, or consume an external quota file. Codex source verifies the OpenCode OAuth account matches. Missing/stale/non-GO/>=95% stops dispatch, never estimates usage from calls.',
        args: { enabled: tool.schema.boolean(), source: tool.schema.enum(['codex', 'external']).optional(), limitId: tool.schema.string().optional(), maxAgeSeconds: tool.schema.number().int().positive().optional() },
        async execute(args, context) {
          await approval(context, 'quota', args);
          const config = await services.quota(context.sessionID, { ...args, source: args.source ?? (codexConnection ? 'codex' : 'external') });
          const telemetry = await services.collectQuota(true);
          await services.scanMonitors();
          return json({ config, telemetry });
        },
      }),
      starfix_trajectory: tool({
        description: 'Run any graph supported by the original StarFix runner. Fallback uses a separate OpenCode CLI context with a pinned adjudicator model, not captain-supplied PASS. First use snapshots captain model unless model is supplied. May use model quota. Project graph dependencies must actually exist.',
        args: { graph: tool.schema.string(), inputs: tool.schema.record(tool.schema.string(), tool.schema.string()),
          model: tool.schema.object({ providerID: tool.schema.string(), modelID: tool.schema.string() }).optional(),
          inject: tool.schema.array(tool.schema.string()).optional(), runTag: tool.schema.string().optional() },
        async execute(args, context) {
          await approval(context, 'trajectory', args);
          return json(await services.trajectory(context.sessionID, args));
        },
      }),
      starfix_compile: tool({
        description: 'Run the original compiler, validator, drift detector, updater, inducer or block executor. Extra args pass through unchanged. execute runs real commands and retains original human gates and canary checks.',
        args: { action: tool.schema.enum(['compile', 'validate', 'detect', 'update', 'induce', 'execute']), source: tool.schema.string(), args: tool.schema.array(tool.schema.string()).optional() },
        async execute(args, context) {
          await approval(context, 'compile', args);
          return json(await services.compile(context.sessionID, args));
        },
      }),
      starfix_scrub: tool({
        description: 'Run original sharing/redaction gate read-only. Exit 0/1/2 means PASS/FAIL/unavailable; without the private HMAC wordlist PASS is structural-only. Supply saltFile, never the salt itself. Does not publish anything.',
        args: { directory: tool.schema.string().optional(), termsFile: tool.schema.string().optional(), saltFile: tool.schema.string().optional() },
        async execute(args, context) {
          await approval(context, 'scrub', args);
          return json(await services.scrub(context.sessionID, args));
        },
      }),
      starfix_codex: tool({
        description: 'Inspect pending native Codex approval/input requests, or forward a response after host approval. No approval is automatically granted. Responses follow the native request schema.',
        args: { requestID: tool.schema.string().optional(), response: tool.schema.string().optional() },
        async execute(args, context) {
          fleet.requireCaptain(context.sessionID, true);
          if (!codexConnection) throw new Error('Codex CLI is not configured');
          if (args.requestID) {
            const request = codexConnection.approvals.get(args.requestID);
            if (!request) throw new Error('No such pending native request');
            const response = JSON.parse(args.response);
            await approval(context, 'codex_native_response', { request, response });
            codexConnection.answer(args.requestID, response);
          }
          return json([...codexConnection.approvals.values()]);
        },
      }),
      starfix_channel: tool({
        description: 'Register/list/cancel a channel guard. Repairs only after two consecutive probe failures and confirms recovery by probing again. Commands are executable + argv arrays, not model-generated success flags. Registering authorizes the exact repair command to run on future failures.',
        args: { action: tool.schema.enum(['register', 'list', 'cancel', 'check']), name: tool.schema.string().optional(), kind: tool.schema.enum(['codex', 'command']).optional(),
          probe: tool.schema.array(tool.schema.string()).optional(), repair: tool.schema.array(tool.schema.string()).optional(), intervalSeconds: tool.schema.number().positive().optional(), timeoutSeconds: tool.schema.number().positive().optional() },
        async execute(args, context) {
          fleet.requireCaptain(context.sessionID, true);
          if (args.action !== 'list') await approval(context, 'channel', args);
          if (args.action === 'check') { await channels.scan(true); return json(fleet.state().channels ?? {}); }
          return json(await channels.configure(context.sessionID, args));
        },
      }),
      starfix_audit: tool({
        description: 'Configure/start/stop/status the original background audit chain, or run one original component. Requires a real project dependency profile; absence is unconfigured, not PASS. Runs upstream registration gates, settle, drift, alert rerouting and snapshot programs with Windows bindings.',
        args: { action: tool.schema.enum(['configure', 'start', 'stop', 'status', 'run']), profile: tool.schema.string().optional(), script: tool.schema.string().optional(), args: tool.schema.array(tool.schema.string()).optional() },
        async execute(args, context) {
          fleet.requireCaptain(context.sessionID, true);
          if (args.action !== 'status') await approval(context, 'audit', args);
          if (args.action === 'configure') return json(await audit.configure(context.sessionID, args.profile));
          if (args.action === 'start') return json(await audit.start(context.sessionID));
          if (args.action === 'stop') return json(await audit.stop(context.sessionID));
          if (args.action === 'run') return json(await audit.run(context.sessionID, args.script, args.args));
          return json(audit.status());
        },
      }),
    },
    'tool.execute.before': async ({ tool: name, sessionID }) => {
      if (name !== 'question') return;
      const owner = fleetFor(sessionID);
      const state = owner?.state();
      if (!state || state.captain !== sessionID || state.paused) return;
      // An outstanding native question blocks the captain from consuming panel
      // answers. Route decisions before creating it; never answer permissions here.
      throw new Error('StarFix 舰长确认题请用 starfix_task ask add 登记面板，并用 starfix_status 读取用户完整答复，不再调用 question。若仅等待用户，结束当前回合以便事件唤醒接续；命令权限审批保持独立。');
    },
    'chat.message': async (message, output) => {
      const variant = output?.message?.model?.variant ?? output?.message?.variant ?? message.variant ?? message.model?.variant ?? null;
      observed.set(message.sessionID, { model: message.model, agent: message.agent, variant });
      await fleetFor(message.sessionID)?.rememberModel(message.sessionID, message.model, message.agent, { variant });
    },
    'experimental.chat.system.transform': async ({ sessionID, model }, output) => {
      const owner = fleetFor(sessionID);
      if (!owner) return;
      // Activation can occur after chat.message in the first turn. Observe the actual
      // model at request time rather than guessing a provider from its display name.
      if (model?.providerID && model?.id) await owner.rememberModel(sessionID, { providerID: model.providerID, modelID: model.id });
      if (owner.state().captain === sessionID && model?.limit?.context > 0) {
        await owner.lock(async () => { const s = owner.state(); s.contextLimit = model.limit.context; owner.save(s); });
      }
      output.system.push(contextText(sessionID, owner));
    },
    'experimental.session.compacting': async ({ sessionID }, output) => {
      const owner = fleetFor(sessionID);
      if (!owner) return;
      const files = await owner.handoff(sessionID);
      output.context.push(`StarFix handoff saved: ${json(files)}. Preserve project, captain identity, open decisions, task evidence and user authorization boundaries. Resume from disk, not guessed memory.`);
    },
    'shell.env': async ({ sessionID }, output) => {
      const owner = fleetFor(sessionID);
      if (!owner) return;
      output.env.FLEET_HOME = owner.home;
      output.env.FLEET_ASK_INBOX = owner.askInbox;
      output.env.FLEET_INTEGRATION_REPO = owner.directory;
      output.env.PYTHONUTF8 = '1';
    },
    event: async ({ event }) => {
      try {
        if (event.type === 'server.instance.disposed' && path.resolve(event.properties.directory) === path.resolve(input.directory)) {
          await shutdown();
          return;
        }
        const owner = fleetFor(event.properties?.sessionID ?? event.properties?.part?.sessionID ?? event.properties?.info?.sessionID ?? event.properties?.info?.id);
        if (!owner) return;
        if (event.type === 'session.compacted') {
          await owner.compacted(event.properties.sessionID, input.client);
        }
        if (['session.status', 'session.idle', 'session.error', 'session.deleted', 'message.updated', 'message.part.updated'].includes(event.type)) await owner.recordEvent(event);
      } catch (error) { await reportError(error); }
    },
  };
}
