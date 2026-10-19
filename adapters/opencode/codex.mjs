import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import net from 'node:net';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Fleet, atomic, readJSON } from './runtime.mjs';

const errorCategories = new Set(['UNKNOWN', 'MISSING_ROLLOUT', 'SHUTDOWN_FORCED', 'SHUTDOWN_UNCONFIRMED']);
export class CodexRPCError extends Error {
  constructor(nativeCode = null, category = 'UNKNOWN') {
    const safeCode = Number.isSafeInteger(nativeCode) ? nativeCode : null;
    const safeCategory = errorCategories.has(category) ? category : 'UNKNOWN';
    super(`Codex RPC failed (${safeCode ?? 'unknown'}; ${safeCategory})`);
    this.nativeCode = safeCode;
    this.category = safeCategory;
  }
}

// Host wire errors carry only this bounded schema. Never forward native message/data.
export function codexErrorWire(error) {
  const category = error instanceof CodexRPCError ? error.category :
    error?.code === 'CODEX_FORCED_SHUTDOWN' ? 'SHUTDOWN_FORCED' :
    error?.code === 'CODEX_SHUTDOWN_UNCONFIRMED' ? 'SHUTDOWN_UNCONFIRMED' : 'UNKNOWN';
  const nativeCode = error instanceof CodexRPCError ? error.nativeCode : null;
  return { code: nativeCode ?? -32000, message: 'Codex operation failed',
    data: { starfixCodexError: 1, nativeCode, category } };
}

export function codexRPCError(error, method, params = {}, fromHost = false) {
  if (fromHost && error?.data?.starfixCodexError === 1) {
    return new CodexRPCError(error.data.nativeCode, error.data.category);
  }
  const code = Number.isSafeInteger(error?.code) ? error.code : null;
  const text = typeof error?.message === 'string' ? error.message : '';
  let missing = false;
  // Classification applies to the exact requested thread, not arbitrary RPCs or I/O failures.
  if (['thread/read', 'thread/resume'].includes(method) && typeof params.threadId === 'string' &&
      /^[a-zA-Z0-9-]+$/.test(params.threadId) && [-32600, -32000].includes(code) &&
      !/permission|denied|timeout|timed out|parse|decode|model|provider/i.test(text)) {
    const id = params.threadId.replaceAll('-', '\\-');
    missing = /^missing source rollout(?:$|: )/i.test(text) ||
      new RegExp(`^no rollout found for thread(?: id)?[: ]+["']?${id}["']?[.!]?$`, 'i').test(text);
  }
  return new CodexRPCError(code, missing ? 'MISSING_ROLLOUT' : 'UNKNOWN');
}

async function closedWithin(exited, ms) {
  let timer;
  try { return await Promise.race([exited.then(() => true), new Promise(resolve => { timer = setTimeout(() => resolve(false), ms); })]); }
  finally { clearTimeout(timer); }
}

export class CodexConnection {
  constructor({ binary, directory, home, node = 'node', env = process.env, onEvent = async () => {}, shutdownTimeoutMs = 5000, killTimeoutMs = 2000 }) {
    this.binary = binary;
    this.directory = directory;
    this.home = home;
    this.node = node;
    this.env = env;
    this.onEvent = onEvent;
    this.requests = new Map();
    this.approvals = new Map();
    this.threads = new Map();
    this.releasedThreads = new Set();
    this.nextID = 1;
    this.shutdownTimeoutMs = Number.isFinite(shutdownTimeoutMs) ? Math.max(1, Math.min(60000, shutdownTimeoutMs)) : 5000;
    this.killTimeoutMs = Number.isFinite(killTimeoutMs) ? Math.max(1, Math.min(10000, killTimeoutMs)) : 2000;
  }

  async connectHost() {
    const directory = path.join(this.home, 'codex-host');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    if (process.platform === 'win32') {
      // POSIX mode 0600 alone does not protect a Windows bearer token. Restrict
      // only this adapter-owned directory, never the project or account store.
      const sid = execFileSync('powershell.exe', ['-NoProfile', '-Command', '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'], { encoding: 'utf8', windowsHide: true }).trim();
      if (!/^S-\d(?:-\d+)+$/.test(sid)) throw new Error('Cannot establish local host token ownership');
      execFileSync('icacls.exe', [directory, '/inheritance:r', '/grant:r', `*${sid}:(OI)(CI)F`, '*S-1-5-18:(OI)(CI)F'], { windowsHide: true, stdio: 'pipe' });
    }
    const file = path.join(directory, 'connection.json');
    return Fleet.prototype.lock.call({ home: directory }, async () => {
      let config = readJSON(file, null);
      let alive = false;
      if (config?.pid) {
        try { process.kill(config.pid, 0); alive = true; }
        catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      if (!alive) {
        config = { binary: this.binary, directory: this.directory, token: crypto.randomBytes(32).toString('hex') };
        atomic(file, JSON.stringify(config));
        // The durable host owns native stdio. Detaching the UI must not close
        // native stdin, cancel its approval requests, or interrupt active turns.
        const launcher = spawn(this.node, [fileURLToPath(new URL('./codex_host.mjs', import.meta.url)), file],
          { cwd: this.directory, env: this.env, detached: true, windowsHide: true, stdio: 'ignore' });
        await once(launcher, 'spawn'); launcher.unref();
        const deadline = Date.now() + 30000;
        while (!config.port) {
          if (launcher.exitCode !== null || Date.now() >= deadline) throw new Error('Codex persistent host did not become ready');
          await delay(50); config = readJSON(file, {});
        }
      }
      if (config.binary !== this.binary || path.resolve(config.directory) !== path.resolve(this.directory)) throw new Error('Codex host identity mismatch; stop the previous host explicitly before rebinding');
      this.hostPID = config.pid;
      const socket = net.createConnection({ host: '127.0.0.1', port: config.port });
      socket.setTimeout(15000, () => socket.destroy(new Error('Codex host connection timed out')));
      await once(socket, 'connect'); socket.setTimeout(0);
      socket.write(JSON.stringify({ method: 'starfix/auth', token: config.token }) + '\n');
      socket.stdin = socket; socket.stdout = socket; socket.stderr = { resume() {} };
      socket.exitCode = null; socket.kill = () => socket.destroy();
      socket.on('close', () => { socket.exitCode = 0; });
      return socket;
    });
  }

  async start() {
    if (this.closing) await this.closing;
    if (this.disconnecting) await this.disconnecting;
    if (this.starting) return this.starting;
    if (this.child) await this.disconnect(); // Never replace a transport whose close is unconfirmed.
    this.starting = (async () => {
      if (!this.binary || !fs.existsSync(this.binary)) throw new Error('Codex CLI executable not configured');
      const child = this.home ? await this.connectHost() : spawn(this.binary, ['app-server', '--listen', 'stdio://'], {
        cwd: this.directory, env: this.env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      });
      this.child = child;
      // An error is not a close. Wait for actual closure before replacing a
      // transport, and scope every callback to its own connection generation.
      this.exited = new Promise(resolve => child.once('close', resolve));
      child.stderr.resume(); // Never persist auth/configuration diagnostics or credential-bearing output.
      const lines = createInterface({ input: child.stdout });
      lines.on('line', line => {
        if (this.child !== child) return;
        let message;
        try { message = JSON.parse(line); } catch { return; }
        if (message.method === 'starfix/loaded') {
          this.threads = new Map(message.params.threads);
          return;
        }
        if (message.method === 'starfix/requestResolved') {
          this.approvals.delete(String(message.params.id));
          return;
        }
        if (message.id !== undefined && !message.method) {
          const pending = this.requests.get(message.id);
          if (!pending) return;
          this.requests.delete(message.id);
          clearTimeout(pending.timer);
          if (message.error) pending.reject(codexRPCError(message.error, pending.method, pending.params, Boolean(this.home)));
          else pending.resolve(message.result);
        } else {
          if (message.method === 'thread/closed') this.threads.delete(message.params.threadId);
          if (message.method === 'thread/settings/updated') {
            const selected = this.threads.get(message.params.threadId);
            const settings = message.params.threadSettings;
            if (selected && settings) Object.assign(selected, { model: settings.model, modelProvider: settings.modelProvider, reasoningEffort: settings.effort });
          }
          // Native approval/input requests are never auto-accepted. The host UI
          // can inspect and answer them through starfix_codex after its own approval.
          if (message.id !== undefined) this.approvals.set(String(message.id), message);
          void Promise.resolve(this.onEvent(message)).catch(() => {});
        }
      });
      const fail = () => {
        if (this.child !== child) return;
        for (const pending of this.requests.values()) { clearTimeout(pending.timer); pending.reject(new Error('Codex transport disconnected; outcome may be unknown')); }
        this.requests.clear(); this.approvals.clear(); this.threads.clear();
        this.starting = null;
      };
      child.on('error', fail);
      child.on('close', fail);
      child.stdin.on('error', fail);
      lines.on('error', fail);
      this.initialization = await this.send('initialize', { clientInfo: { name: 'starfix_opencode', version: '1.0.0' } });
      child.stdin.write(JSON.stringify({ method: 'initialized' }) + '\n');
      this.everStarted = true;
    })();
    try { return await this.starting; } catch (error) { await this.disconnect(); throw error; }
  }

  send(method, params = {}, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const id = this.nextID++;
      const timer = setTimeout(() => { this.requests.delete(id); reject(new Error(`Codex RPC ${method} timed out; do not blindly retry writes`)); }, timeoutMs);
      this.requests.set(id, { resolve, reject, timer, method, params: { threadId: params.threadId } });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => {
        if (error) { clearTimeout(timer); this.requests.delete(id); reject(new Error('Codex transport write failed; outcome may be unknown')); }
      });
    });
  }

  async rpc(method, params = {}) {
    if (this.everStarted && !this.starting) throw new Error('Codex channel disconnected; channel guard must confirm repair before continuing');
    await this.start(); return this.send(method, params);
  }

  async close() {
    if (this.closing) return this.closing;
    const operation = this.closeTransport();
    this.closing = operation;
    try { return await operation; } finally { if (this.closing === operation) this.closing = null; }
  }

  async closeTransport() {
    // A concurrent initialization must settle before shutdown owns its transport.
    if (this.starting) await this.starting.catch(() => {});
    let stopped = false;
    if (this.home && this.child && !this.child.destroyed) {
      await this.send('starfix/stop');
      stopped = true;
    }
    await this.disconnect();
    if (stopped) {
      const deadline = Date.now() + 10000;
      for (;;) {
        try { process.kill(this.hostPID, 0); }
        catch (error) { if (error.code === 'ESRCH') break; throw error; }
        if (Date.now() >= deadline) throw new Error('Codex host stop acknowledged but process exit unconfirmed');
        await delay(50);
      }
    }
  }

  async disconnect() {
    if (this.disconnecting) return this.disconnecting;
    const operation = this.disconnectTransport();
    this.disconnecting = operation;
    try { return await operation; } finally { if (this.disconnecting === operation) this.disconnecting = null; }
  }

  async disconnectTransport() {
    const child = this.child;
    if (!child) return this.lastDisconnect ?? { kind: 'none', closed: true, forced: false };
    const exited = this.exited;
    if (!exited) throw Object.assign(new Error('Codex close evidence unavailable'), { code: 'CODEX_SHUTDOWN_UNCONFIRMED' });
    const kind = this.home ? 'ui' : 'native';
    let forced = false;
    try { child.stdin.end(); } catch { /* Still require actual closure. */ }
    if (kind === 'ui') child.destroy(); // Only the UI socket, never native stdio or the host.
    let closed = await closedWithin(exited, this.shutdownTimeoutMs);
    if (!closed && kind === 'native') {
      forced = true;
      try { child.kill(); } catch { /* A failed kill is not exit evidence. */ }
      closed = await closedWithin(exited, this.killTimeoutMs);
    }
    const result = { kind, closed, forced, exitCode: Number.isInteger(child.exitCode) ? child.exitCode : null };
    this.lastDisconnect = result;
    if (closed && this.child === child) { this.child = null; this.starting = null; }
    if (!closed) throw Object.assign(new Error('Codex shutdown exit unconfirmed'), { code: 'CODEX_SHUTDOWN_UNCONFIRMED' });
    if (forced) throw Object.assign(new Error('Codex shutdown required forced termination; persistence unconfirmed'), { code: 'CODEX_FORCED_SHUTDOWN' });
    if (kind === 'native' && result.exitCode !== 0) throw Object.assign(new Error('Codex exited unsuccessfully; persistence unconfirmed'), { code: 'CODEX_SHUTDOWN_UNCONFIRMED' });
    return result;
  }

  async read(id, includeTurns = false) {
    const result = await this.rpc('thread/read', { threadId: id, includeTurns });
    if (result.thread.id !== id) throw new Error('Codex thread identity mismatch');
    return result.thread;
  }

  async resume(id, model, { reclaim = false } = {}) {
    if (this.releasedThreads.has(id) && !reclaim) throw new Error('Codex worker is under manual control');
    if (this.home) {
      await this.start();
      const loaded = this.threads.get(id);
      // Reattaching a UI to a still-loaded worker is not a native resume:
      // native resume can replace an active in-memory turn.
      if (loaded && (!model || (loaded.model === model.modelID && loaded.modelProvider === model.providerID))) {
        if (reclaim) this.releasedThreads.delete(id);
        return loaded;
      }
    }
    const result = await this.rpc('thread/resume', { threadId: id,
      ...(model ? { model: model.modelID, modelProvider: model.providerID } : {}) });
    this.threads.set(id, { ...result, persisted: true });
    if (reclaim) this.releasedThreads.delete(id);
    return result;
  }

  answer(id, result) {
    const request = this.approvals.get(String(id));
    if (!request) throw new Error('Native request no longer pending; inspect current Codex state');
    this.child.stdin.write(JSON.stringify({ id: request.id, result }) + '\n');
    this.approvals.delete(String(id));
  }

  async interrupt(id) {
    // A just-created thread may not have a persisted transcript yet. Its
    // native status must still prove idle; missing history is not a stop proof.
    const thread = await this.read(id, true).catch(() => this.read(id));
    const active = thread.turns?.findLast(t => t.status === 'inProgress');
    if (!active) {
      if (thread.status?.type !== 'idle') throw new Error('Native idle state unconfirmed; inspect the worker before releasing it');
      return { interrupted: false, idle: true };
    }
    await this.rpc('turn/interrupt', { threadId: id, turnId: active.id });
    const deadline = Date.now() + 15000;
    do {
      const current = await this.read(id, true);
      const turn = current.turns?.find(t => t.id === active.id);
      if (turn && ['completed', 'interrupted', 'failed'].includes(turn.status)) return { interrupted: true, turnID: active.id };
      await delay(50);
    } while (Date.now() < deadline);
    throw new Error('Interrupt requested; completion unconfirmed. Inspect history, do not assume the worker stopped');
  }

  async release(id) {
    this.releasedThreads.add(id);
    await this.interrupt(id);
    await this.rpc('thread/unsubscribe', { threadId: id });
    this.threads.delete(id);
    return { sessionID: id, released: true, command: [this.binary, 'resume', id] };
  }

  client(model) {
    const get = async ({ path: p }) => {
      let thread;
      try { thread = await this.read(p.id); }
      catch (error) { if (!this.threads.has(p.id) || this.threads.get(p.id).persisted) throw error; thread = this.threads.get(p.id).thread; }
      return { data: { id: thread.id, directory: thread.cwd, title: thread.name ?? thread.preview, reasoningEffort: this.threads.get(p.id)?.reasoningEffort } };
    };
    return { harness: 'codex', session: {
      create: async ({ query, body }) => {
        const result = await this.rpc('thread/start', { cwd: query.directory, ...(model ? { model: model.modelID, modelProvider: model.providerID } : {}),
          ...(body.reasoningEffort !== undefined ? { config: { model_reasoning_effort: body.reasoningEffort } } : {}) });
        this.threads.set(result.thread.id, result);
        await this.rpc('thread/name/set', { threadId: result.thread.id, name: body.title });
        return { data: { id: result.thread.id, reasoningEffort: result.reasoningEffort } };
      },
      get,
      status: async ({ path: target } = {}) => {
        const data = {};
        for (const [id, cached] of this.threads) {
          if (target?.id && target.id !== id) continue;
          let thread;
          try { thread = await this.read(id); } catch (error) { if (cached.persisted) throw error; thread = cached.thread; }
          data[id] = { type: thread.status?.type === 'active' ? 'busy' : thread.status?.type === 'systemError' ? 'error' : 'idle' };
        }
        return { data };
      },
      messages: async ({ path: p }) => {
        let thread;
        try { thread = await this.read(p.id, true); }
        catch (error) { if (!this.threads.has(p.id) || this.threads.get(p.id).persisted) throw error; thread = this.threads.get(p.id).thread; }
        const selected = this.threads.get(p.id);
        return { data: (thread.turns ?? []).flatMap(turn => (turn.items ?? []).filter(item => ['userMessage', 'agentMessage'].includes(item.type)).map(item => ({
          info: { id: item.id, role: item.type === 'userMessage' ? 'user' : 'assistant',
            model: { providerID: selected?.modelProvider ?? model?.providerID, modelID: selected?.model ?? model?.modelID }, reasoningEffort: selected?.reasoningEffort },
          parts: item.type === 'userMessage' ? item.content.filter(c => c.type === 'text').map(c => ({ type: 'text', text: c.text })) : [{ type: 'text', text: item.text }],
        }))) };
      },
      promptAsync: async ({ path: p, body }) => {
        let submitted = false;
        try {
          if (this.releasedThreads.has(p.id)) throw new Error('Codex worker is under manual control');
          const loaded = this.threads.get(p.id);
          const thread = await this.read(p.id, true).catch(error => {
            if (!loaded || loaded.persisted) throw error;
            return loaded.thread;
          });
          const active = thread.turns?.findLast(t => t.status === 'inProgress');
          const input = body.parts.filter(p => p.type === 'text').map(p => ({ type: 'text', text: p.text, text_elements: [] }));
          if (active) {
            // Native steering cannot override model/cwd. Never resume an active
            // thread to switch models: that can replace its in-flight work.
            if (!loaded || loaded.model !== body.model.modelID || loaded.modelProvider !== body.model.providerID) throw new Error('Active Codex turn uses another model; finish or explicitly interrupt it before changing models');
            if (body.reasoningEffort !== undefined && loaded.reasoningEffort !== body.reasoningEffort) throw new Error('Active Codex turn uses another reasoning effort; the new setting applies to the next turn, not steering. Wait or explicitly interrupt first');
            submitted = true;
            return { data: await this.rpc('turn/steer', { threadId: p.id, expectedTurnId: active.id, input }) };
          }
          // Resuming an already loaded thread can replace its in-memory turn.
          // Resume only after transport restart or an explicit worker-model change.
          if (!loaded || loaded.model !== body.model.modelID || loaded.modelProvider !== body.model.providerID) await this.resume(p.id, body.model);
          submitted = true;
          const result = await this.rpc('turn/start', { threadId: p.id, model: body.model.modelID, input,
            ...(body.reasoningEffort !== undefined ? { effort: body.reasoningEffort } : {}) });
          if (body.reasoningEffort !== undefined && this.threads.has(p.id)) this.threads.get(p.id).reasoningEffort = body.reasoningEffort;
          return { data: result };
        } catch (error) {
          // Preflight reads/resume have not submitted task text. After entering
          // turn/start or turn/steer, even a timeout must remain an unknown write.
          if (!submitted) error.code = 'DELIVERY_NOT_SENT';
          throw error;
        }
      },
    } };
  }
}

export function quotaTelemetry(result, limitId = 'codex') {
  const bucket = result.rateLimitsByLimitId ? result.rateLimitsByLimitId[limitId] : result.rateLimits;
  if (!bucket) throw new Error(`No account quota bucket ${limitId}; not zero usage`);
  const windows = [bucket.primary, bucket.secondary].filter(Boolean);
  if (!windows.length || windows.some(w => !Number.isFinite(w.usedPercent) || w.usedPercent < 0 || w.usedPercent > 100)) throw new Error('Account quota windows unavailable');
  return { source: 'codex account/rateLimits/read', limitId, observedAt: new Date().toISOString(),
    usedPercent: Math.max(...windows.map(w => w.usedPercent)), windows,
    status: windows.some(w => w.usedPercent >= 95) || bucket.rateLimitReachedType ? 'STOP' : 'GO' };
}

export function sameOpenAIAccount(opencodeAuth, codexAuth) {
  // Compare locally without emitting account IDs, OAuth tokens or credentials.
  try {
    const left = JSON.parse(fs.readFileSync(opencodeAuth, 'utf8')).openai;
    const right = JSON.parse(fs.readFileSync(codexAuth, 'utf8'));
    return left?.type === 'oauth' && Boolean(left.accountId) && left.accountId === right.tokens?.account_id;
  } catch { throw new Error('Account identity unavailable; check the existing CLI sign-ins locally'); }
}
