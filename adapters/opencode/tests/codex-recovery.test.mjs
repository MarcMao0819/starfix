import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { Fleet } from '../runtime.mjs';
import { Services } from '../services.mjs';
import { AuditRuntime } from '../audit.mjs';
import { tool } from '@opencode-ai/plugin';

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const modules = process.env.CODEX_FIX_MODULE_ROOT ?? fileURLToPath(new URL('../', import.meta.url));
const codex = await import(pathToFileURL(path.join(modules, 'codex.mjs')));
const { CodexConnection } = codex;
const { createStarfixPlugin } = await import(pathToFileURL(path.join(modules, 'plugin.mjs')));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(root, 'recovery-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}
async function childConnection(t, source, options = {}) {
  const dir = fixture(t);
  const child = spawn(process.execPath, ['-e', source], { cwd: dir, stdio: ['pipe', 'pipe', 'ignore'], windowsHide: true });
  child.on('error', () => {}); child.stdin.on('error', () => {});
  const c = new CodexConnection({ directory: dir, shutdownTimeoutMs: 1000, ...options });
  c.child = child; c.exited = new Promise(resolve => child.once('close', resolve));
  await once(child.stdout, 'data');
  t.after(async () => { if (child.exitCode === null) { child.kill(); await c.exited; } });
  return { c, child, dir };
}

test('recovery: native EOF waits for delayed durable save and concurrent close is single-owner', async t => {
  const { c, dir } = await childConnection(t, `process.stdin.resume();process.stdin.on('end',()=>setTimeout(()=>{require('fs').writeFileSync('saved','complete');process.exit(0)},180));process.stdout.write('ready')`);
  await Promise.all([c.close(), c.close(), c.disconnect()]);
  assert.equal(fs.readFileSync(path.join(dir, 'saved'), 'utf8'), 'complete');
  assert.equal(c.lastDisconnect.forced, false);
  assert.equal(c.lastDisconnect.closed, true);
  await c.close();
});

test('recovery: native timeout is forced, bounded and never reported as graceful persistence', async t => {
  const { c } = await childConnection(t, `process.stdin.resume();setInterval(()=>{},1000);process.stdout.write('ready')`, { shutdownTimeoutMs: 80, killTimeoutMs: 1000 });
  await assert.rejects(c.close(), error => error.code === 'CODEX_FORCED_SHUTDOWN');
  assert.equal(c.lastDisconnect.closed, true);
  assert.equal(c.lastDisconnect.forced, true);
  assert.equal(c.child, null);
});

test('recovery: already exited native has bounded repeat cleanup', async t => {
  const { c } = await childConnection(t, `process.stdout.write('ready');setTimeout(()=>process.exit(0),30)`);
  await c.exited;
  await c.disconnect(); await c.disconnect();
  assert.equal(c.child, null);
  assert.equal(c.lastDisconnect.forced, false);
});

test('recovery: initialization failure drains the owned process before allowing another start', async t => {
  const dir = fixture(t);
  const c = new CodexConnection({ binary: process.execPath, directory: dir, shutdownTimeoutMs: 500 });
  // Node is intentionally not an app-server. Its own immediate exit is the failure fixture.
  await assert.rejects(c.start());
  assert.equal(c.child, null); assert.equal(c.starting, null);
  assert.equal(c.lastDisconnect.closed, true);
  await c.close();
});

test('recovery: UI detach preserves server work and native approvals; initialize errors are cleaned', async t => {
  const dir = fixture(t), sockets = new Set(); let stopCalls = 0, completed = false;
  const server = net.createServer(socket => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket)); socket.on('error', () => {});
    createInterface({ input: socket }).on('line', line => {
      const request = JSON.parse(line);
      if (request.method === 'initialize') socket.write(JSON.stringify({ id: request.id, result: {} }) + '\n');
      if (request.method === 'starfix/stop') stopCalls++;
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { for (const s of sockets) s.destroy(); await new Promise(resolve => server.close(resolve)); });
  const c = new CodexConnection({ binary: process.execPath, directory: dir, home: dir });
  c.connectHost = async () => {
    const socket = net.createConnection(server.address().port, '127.0.0.1'); await once(socket, 'connect');
    socket.stdin = socket; socket.stdout = socket; socket.stderr = { resume() {} };
    socket.exitCode = null; socket.kill = () => socket.destroy(); socket.on('close', () => { socket.exitCode = 0; });
    return socket;
  };
  await c.start();
  const work = pause(90).then(() => { completed = true; });
  await c.disconnect(); await work;
  assert.equal(completed, true); assert.equal(stopCalls, 0); assert.equal(server.listening, true);
  await c.start(); await c.disconnect();
});

test('recovery: error classification is narrow, redacted and survives both RPC boundaries', async t => {
  const id = crypto.randomUUID();
  const sentinel = 'PRIVATE_SENTINEL_7481';
  const native = { code: -32600, message: `missing source rollout: ${sentinel}`, data: { token: sentinel } };
  const first = codex.codexRPCError(native, 'thread/resume', { threadId: id });
  assert.equal(first.category, 'MISSING_ROLLOUT'); assert.equal(first.nativeCode, -32600);
  const wire = codex.codexErrorWire(first);
  const second = codex.codexRPCError(wire, 'thread/resume', { threadId: id }, true);
  assert.equal(second.category, 'MISSING_ROLLOUT'); assert.equal(second.nativeCode, -32600);
  assert.equal(JSON.stringify({ message: first.message, wire, second }).includes(sentinel), false);
  for (const message of ['failed to read rollout history', 'missing source rollout: permission denied', 'missing source rollout: timed out', 'model not found', 'invalid json', 'source file missing']) {
    assert.equal(codex.codexRPCError({ code: -32600, message }, 'thread/resume', { threadId: id }).category, 'UNKNOWN');
  }
  assert.equal(codex.codexRPCError(native, 'turn/start', { threadId: id }).category, 'UNKNOWN');
  assert.equal(codex.codexRPCError({ ...native, code: -32001 }, 'thread/resume', { threadId: id }).category, 'UNKNOWN');
  assert.equal(codex.codexRPCError({ code: -32600, message: `no rollout found for thread id ${id}` }, 'thread/resume', { threadId: id }).category, 'MISSING_ROLLOUT');
  assert.equal(codex.codexRPCError({ code: -32600, message: 'no rollout found for thread id another' }, 'thread/resume', { threadId: id }).category, 'UNKNOWN');
  assert.equal(codex.codexErrorWire(new Error(sentinel)).message.includes(sentinel), false);
  second.code = 'DELIVERY_NOT_SENT';
  assert.equal(second.nativeCode, -32600); // Preflight semantics do not erase native evidence.

  const dir = fixture(t);
  const server = net.createServer(socket => {
    socket.on('error', () => {});
    createInterface({ input: socket }).on('line', line => {
      const request = JSON.parse(line);
      if (request.id !== undefined) socket.write(JSON.stringify({ id: request.id,
        ...(request.method === 'initialize' ? { result: {} } : { error: wire }) }) + '\n');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const c = new CodexConnection({ binary: process.execPath, directory: dir, home: dir });
  c.connectHost = async () => {
    const s = net.createConnection(server.address().port, '127.0.0.1'); await once(s, 'connect');
    s.stdin = s; s.stdout = s; s.stderr = { resume() {} }; s.exitCode = null;
    s.kill = () => s.destroy(); s.on('close', () => { s.exitCode = 0; }); return s;
  };
  try {
    await assert.rejects(c.rpc('thread/resume', { threadId: id }), e => e.category === 'MISSING_ROLLOUT' && e.nativeCode === -32600 && !e.message.includes(sentinel));
  } finally { await c.disconnect(); await new Promise(resolve => server.close(resolve)); }
});

test('recovery: missing background resume is suppressed, explicit retry and other observations continue', async t => {
  const dir = fixture(t), project = path.join(dir, 'project'); fs.mkdirSync(project);
  const config = { sourceRoot: fileURLToPath(new URL('../../../', import.meta.url)), dataRoot: path.join(dir, 'data'), directory: project, python: process.env.STARFIX_TEST_PYTHON };
  const f = new Fleet(config); await f.activate('captain', false, false);
  const model = { providerID: 'openai', modelID: 'fixture' };
  const state = f.state();
  for (const name of ['missing', 'other', 'unknown']) state.workers[name] = { name, sessionID: name, directory: project, model, harness: 'codex', status: 'unknown', reasoningEffort: 'medium' };
  f.save(state);
  const originals = { resume: CodexConnection.prototype.resume, client: CodexConnection.prototype.client,
    collect: Services.prototype.collectQuota, inbox: Services.prototype.applyInbox, audit: AuditRuntime.prototype.poll };
  let missingCalls = 0, otherCalls = 0, unknownCalls = 0, quota = 0, inbox = 0, repaired = false;
  CodexConnection.prototype.resume = async function(id) {
    if (id === 'unknown') { unknownCalls++; throw new Error('unknown fixture failure'); }
    if (id === 'missing') {
      missingCalls++;
      if (!repaired) {
        if (codex.CodexRPCError) throw new codex.CodexRPCError(-32600, 'MISSING_ROLLOUT');
        throw new Error('legacy missing fixture');
      }
    } else otherCalls++;
    this.threads.set(id, { persisted: true }); return { thread: { id } };
  };
  CodexConnection.prototype.client = () => ({ session: {
    status: async ({ path: p }) => ({ data: { [p.id]: { type: 'idle' } } }),
    get: async ({ path: p }) => ({ data: { id: p.id, directory: project } }), messages: async () => ({ data: [] }),
  } });
  Services.prototype.collectQuota = async () => { quota++; };
  Services.prototype.applyInbox = async () => { inbox++; };
  AuditRuntime.prototype.poll = async () => {};
  const client = { app: { log: async () => {} }, session: {} };
  const plugin = await createStarfixPlugin({ directory: project, client }, { ...config, tool, codex: 'fixture', intervalMs: 25 });
  try {
    fs.writeFileSync(path.join(f.home, 'receipts', 'kept.md'), 'fixture');
    const until = Date.now() + 2500;
    while ((!f.state().events.some(e => e.type === 'hourly_report') || quota < 3) && Date.now() < until) await pause(30);
    assert.equal(missingCalls, 1); assert.equal(otherCalls, 1);
    assert.ok(unknownCalls >= 2, 'Unknown failures must not inherit missing-rollout suppression');
    assert.equal(f.state().workers.missing.status, 'unavailable');
    assert.equal(f.state().workers.missing.reasoningEffort, 'medium');
    assert.ok(quota >= 3 && inbox >= 3);
    assert.ok(f.state().events.some(e => e.type === 'receipt_changed'));
    assert.ok(f.state().events.some(e => e.type === 'hourly_report'));
    await assert.rejects(plugin.tool.starfix_sessions.execute({ worker: 'missing' }, { sessionID: 'captain' }));
    assert.equal(missingCalls, 2);
    repaired = true;
    await plugin.tool.starfix_sessions.execute({ worker: 'missing' }, { sessionID: 'captain' });
    assert.equal(missingCalls, 3);
    await pause(80);
    assert.equal(f.state().workers.missing.status, 'idle'); assert.equal(missingCalls, 3);
  } finally {
    await plugin.dispose();
    CodexConnection.prototype.resume = originals.resume; CodexConnection.prototype.client = originals.client;
    Services.prototype.collectQuota = originals.collect; Services.prototype.applyInbox = originals.inbox; AuditRuntime.prototype.poll = originals.audit;
  }
});

test('recovery: isolated real native EOF persists empty named thread and real errors are sanitized', { skip: !process.env.STARFIX_TEST_CODEX || process.env.CODEX_FIX_NEGATIVE === '1' }, async t => {
  const dir = fixture(t), home = path.join(dir, 'codex-home'); fs.mkdirSync(home);
  const environment = { ...process.env, CODEX_HOME: home, HOME: dir, USERPROFILE: dir };
  const options = { binary: process.env.STARFIX_TEST_CODEX, directory: dir, env: environment };
  const c = new CodexConnection(options); let id;
  const captured = [];
  let stage = 'START';
  try {
    await c.start();
    const lines = createInterface({ input: c.child.stdout });
    lines.on('line', line => { try { const message = JSON.parse(line); if (message.error) captured.push(message.error); } catch {} });
    const missingThreadId = crypto.randomUUID();
    await assert.rejects(c.rpc('thread/resume', { threadId: missingThreadId }), e => {
      const raw = captured.at(-1);
      const safe = JSON.stringify({ message: e.message, ...e, wire: codex.codexErrorWire(e) });
      assert.ok(raw && Number.isInteger(raw.code));
      assert.equal(e.nativeCode, raw.code);
      assert.ok(!safe.includes(raw.message));
      assert.ok(!safe.includes(home) && !safe.includes(dir));
      console.log('CODEX_FIX_REAL_REDACTION_ZERO'); return true;
    });
    stage = 'CREATE';
    const created = await c.rpc('thread/start', { cwd: dir }); id = created.thread.id;
    stage = 'NAME';
    await c.rpc('thread/name/set', { threadId: id, name: 'isolated-close-save' });
    stage = 'CLOSE';
    await c.close();
    assert.equal(c.lastDisconnect.forced, false); assert.equal(c.lastDisconnect.closed, true);
    lines.close();
  } catch (error) {
    console.log('CODEX_FIX_NATIVE_STAGE ' + JSON.stringify({ stage, nativeCode: Number.isInteger(error.nativeCode) ? error.nativeCode : null,
      category: ['UNKNOWN', 'MISSING_ROLLOUT'].includes(error.category) ? error.category : 'OTHER', assertion: error.code === 'ERR_ASSERTION' }));
    throw error;
  } finally { await c.close(); }
  const next = new CodexConnection(options);
  try {
    stage = 'READ_REOPENED';
    const thread = await next.read(id);
    stage = 'VERIFY_REOPENED';
    assert.equal(thread.id, id); assert.equal(thread.name, 'isolated-close-save');
    const declared = thread.path;
    const confined = typeof declared === 'string' && path.relative(dir, declared) !== '..' && !path.relative(dir, declared).startsWith('..' + path.sep) && !path.isAbsolute(path.relative(dir, declared));
    console.log('CODEX_FIX_FILE_STATE ' + JSON.stringify({ confined, exists: confined && fs.existsSync(declared) }));
    stage = 'RESUME_REOPENED';
    const resumed = await next.resume(id);
    assert.equal(resumed.thread.id, id);
    console.log('CODEX_FIX_REAL_EMPTY_PERSISTED');
  } catch (error) {
    console.log('CODEX_FIX_NATIVE_STAGE ' + JSON.stringify({ stage, nativeCode: Number.isInteger(error.nativeCode) ? error.nativeCode : null,
      category: ['UNKNOWN', 'MISSING_ROLLOUT'].includes(error.category) ? error.category : 'OTHER', assertion: error.code === 'ERR_ASSERTION' }));
    throw error;
  } finally { await next.close(); }
});

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function shutdownFixture(t, { preWorker, resume, wait = 100 } = {}) {
  const dir = fixture(t), project = path.join(dir, 'project'); fs.mkdirSync(project);
  const config = { sourceRoot: fileURLToPath(new URL('../../../', import.meta.url)), dataRoot: path.join(dir, 'data'),
    directory: project, python: process.env.STARFIX_TEST_PYTHON };
  const fleet = new Fleet(config); await fleet.activate('captain', false, false);
  const state = fleet.state(); state.workers.one = { name: 'one', sessionID: 'fixture-worker', directory: project,
    model: { providerID: 'fixture', modelID: 'own' }, harness: 'codex', status: 'unknown' }; fleet.save(state);
  const changes = [];
  function replace(object, key, value) { changes.push([object, key, object[key]]); object[key] = value; }
  const calls = { resumes: 0, statuses: 0, detached: 0, logs: [] };
  replace(CodexConnection.prototype, 'resume', async () => { calls.resumes++; return resume ? resume() : {}; });
  replace(CodexConnection.prototype, 'disconnect', async () => { calls.detached++; });
  replace(CodexConnection.prototype, 'client', () => ({ session: {
    status: async () => { calls.statuses++; return { data: { 'fixture-worker': { type: 'idle' } } }; },
    get: async () => ({ data: { id: 'fixture-worker', directory: project } }), messages: async () => ({ data: [] }),
  } }));
  replace(Services.prototype, 'applyInbox', preWorker ?? (async () => {}));
  replace(Services.prototype, 'collectQuota', async () => {});
  replace(Services.prototype, 'scanMonitors', async () => {});
  replace(AuditRuntime.prototype, 'poll', async () => {});
  replace(AuditRuntime.prototype, 'stop', async () => {});
  replace(Fleet.prototype, 'scanStalls', async () => {});
  replace(Fleet.prototype, 'hourlyReport', async () => {});
  const client = { app: { log: async event => calls.logs.push(event.body.extra?.code) }, session: {} };
  const plugin = await createStarfixPlugin({ directory: project, client }, { ...config, tool, codex: 'fixture', intervalMs: 5, shutdownTimeoutMs: wait });
  return { fleet, plugin, calls, restore: () => { for (const [object, key, value] of changes.reverse()) object[key] = value; } };
}

test('recovery v2: dispose during pre-worker await starts no observer', async t => {
  const entered = deferred(), release = deferred();
  const f = await shutdownFixture(t, { preWorker: async () => { entered.resolve(); await release.promise; } });
  try {
    await entered.promise;
    const closing = f.plugin.dispose();
    release.resolve();
    const result = await closing;
    assert.equal(f.calls.resumes, 0, 'No new resume after disposal began');
    assert.equal(result.tickSettled, true);
    const bytes = fs.readFileSync(f.fleet.stateFile);
    await pause(25);
    assert.deepEqual(fs.readFileSync(f.fleet.stateFile), bytes);
  } finally { release.resolve(); await f.plugin.dispose(); f.restore(); }
});

test('recovery v2: dispose waits for registered observer settlement', async t => {
  const entered = deferred(), release = deferred();
  const f = await shutdownFixture(t, { resume: async () => { entered.resolve(); await release.promise; return {}; } });
  try {
    await entered.promise;
    let returned = false;
    const closing = f.plugin.dispose().then(result => { returned = true; return result; });
    await pause(20);
    const returnedBeforeRelease = returned;
    release.resolve();
    const result = await closing;
    assert.equal(returnedBeforeRelease, false, 'Abort is not proof the observer settled');
    assert.equal(result?.workersSettled, true);
    assert.equal(result?.pendingObservers, 0);
    assert.equal(f.calls.statuses, 0, 'Aborted resume must not continue to status/read');
  } finally { release.resolve(); await f.plugin.dispose(); f.restore(); }
});

test('recovery v2: ignored cancellation is bounded and explicitly unconfirmed', async t => {
  const entered = deferred(), release = deferred();
  const f = await shutdownFixture(t, { wait: 35, resume: async () => { entered.resolve(); await release.promise; return {}; } });
  try {
    await entered.promise;
    const started = Date.now();
    const result = await f.plugin.dispose();
    assert.ok(Date.now() - started < 1000, 'Ignored transport cancellation must not hang disposal');
    const bytes = fs.readFileSync(f.fleet.stateFile);
    release.resolve(); await pause(25);
    assert.equal(result?.workersSettled, false);
    assert.equal(result?.observationSettled, false);
    assert.equal(result?.pendingObservers, 1);
    assert.deepEqual(fs.readFileSync(f.fleet.stateFile), bytes);
    assert.equal(f.calls.statuses, 0);
  } finally { release.resolve(); await f.plugin.dispose(); await pause(10); f.restore(); }
});

test('recovery v2: stalled pre-worker tick is bounded without late registration', async t => {
  const entered = deferred(), release = deferred();
  const f = await shutdownFixture(t, { wait: 35, preWorker: async () => { entered.resolve(); await release.promise; } });
  try {
    await entered.promise;
    const closing = f.plugin.dispose();
    const timeout = Symbol('timeout');
    const first = await Promise.race([closing, pause(300).then(() => timeout)]);
    release.resolve();
    await closing; await pause(20);
    assert.notEqual(first, timeout, 'A stuck tick must return an explicit bounded report');
    assert.equal(first?.tickSettled, false);
    assert.equal(first?.observationSettled, false);
    assert.equal(f.calls.resumes, 0, 'Settling a late tick must not register another observer');
  } finally { release.resolve(); await f.plugin.dispose(); await pause(10); f.restore(); }
});
