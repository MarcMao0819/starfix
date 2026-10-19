import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Fleet } from '../runtime.mjs';
import { Services } from '../services.mjs';
import { CodexConnection, quotaTelemetry, sameOpenAIAccount } from '../codex.mjs';
import { ChannelGuards } from '../channels.mjs';
import { AuditRuntime } from '../audit.mjs';
import { loadPrivateTestInputs } from './test-inputs.mjs';

// Fail before roots, fixture reads, or a native connection can be created.
const testModelID = process.env.STARFIX_TEST_MODEL_ID;
if (typeof testModelID !== 'string' || testModelID.trim().length === 0) {
  throw new Error('STARFIX_TEST_MODEL_ID is required');
}

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
const python = process.env.STARFIX_TEST_PYTHON;
const bash = process.env.STARFIX_TEST_BASH;
const privateInputs = loadPrivateTestInputs();

async function fixture(t) {
  const dir = fs.mkdtempSync(path.join(root, 'gaps-'));
  t.after(() => { assert.equal(path.dirname(fs.realpathSync(dir)), root); fs.rmSync(dir, { recursive: true }); });
  const project = path.join(dir, 'project with spaces'); fs.mkdirSync(project);
  const config = { sourceRoot, directory: project, dataRoot: path.join(dir, 'data'), python };
  const f = new Fleet(config); await f.activate('ses_captain');
  const s = f.state(); s.model = { providerID: 'openai', modelID: testModelID }; f.save(s);
  return { f, config, dir, project };
}

test('real quota contract distinguishes absent windows, buckets and exhausted limits', () => {
  const result = { rateLimitsByLimitId: { codex: { primary: { usedPercent: 12 }, secondary: { usedPercent: 96 } }, other: { primary: { usedPercent: 1 } } } };
  assert.equal(quotaTelemetry(result).usedPercent, 96);
  assert.equal(quotaTelemetry(result).status, 'STOP');
  assert.equal(quotaTelemetry(result, 'other').usedPercent, 1);
  assert.throws(() => quotaTelemetry(result, 'absent'), /not zero/);
  assert.throws(() => quotaTelemetry({ rateLimits: { primary: null, secondary: null } }), /unavailable/);
});

test('Codex effort is transmitted per native turn, preserved per worker, and never changed by steering', async t => {
  const { f, project, config } = await fixture(t);
  const model = { providerID: 'openai', modelID: 'own' };
  const c = new CodexConnection({ binary: 'fixture' });
  const calls = [], threads = new Map();
  c.rpc = async (method, params) => {
    calls.push({ method, params });
    if (method === 'thread/start') {
      const thread = { id: `worker-${threads.size}`, cwd: project, status: { type: 'idle' }, turns: [] };
      threads.set(thread.id, thread);
      return { thread, model: 'own', modelProvider: 'openai', reasoningEffort: params.config?.model_reasoning_effort ?? 'low' };
    }
    if (method === 'thread/name/set') return {};
    const thread = threads.get(params.threadId);
    if (method === 'thread/read') return { thread };
    if (method === 'turn/start') {
      thread.turns.push({ id: 'turn', status: 'completed', items: [{ id: 'message', type: 'userMessage', content: params.input }] });
      return { turn: thread.turns.at(-1) };
    }
    if (method === 'turn/steer') return {};
    throw new Error(method);
  };
  const one = await f.createWorker('ses_captain', 'one', project, c.client(model), model, undefined, { reasoningEffort: 'high' });
  const two = await f.createWorker('ses_captain', 'two', project, c.client(model), model);
  assert.equal(one.reasoningEffort, 'high'); assert.equal(two.reasoningEffort, 'low');
  const restarted = new Fleet(config);
  await restarted.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model));
  await restarted.dispatch('ses_captain', 'two', 'T2', 'work', c.client(model));
  assert.deepEqual(calls.filter(c => c.method === 'turn/start').map(c => c.params.effort), ['high', 'low']);
  await restarted.configureWorker('ses_captain', 'one', undefined, undefined, { reasoningEffort: 'custom-native-level' });
  threads.get(one.sessionID).turns.at(-1).status = 'inProgress';
  const rejected = await restarted.dispatch('ses_captain', 'one', 'T3', 'followup', c.client(model));
  assert.equal(rejected.state, 'not_sent');
  assert.equal(calls.filter(c => c.method === 'turn/steer').length, 0);
  threads.get(one.sessionID).turns.at(-1).status = 'completed';
  await restarted.dispatch('ses_captain', 'one', 'T3', 'followup', c.client(model));
  assert.equal(calls.filter(c => c.method === 'turn/start').at(-1).params.effort, 'custom-native-level');
  assert.equal(restarted.state().workers.two.reasoningEffort, 'low');
});

test('Codex busy delivery steers the same turn; explicit interrupt and release preserve the thread', async () => {
  const c = new CodexConnection({ binary: 'codex' });
  const model = { providerID: 'openai', modelID: 'own' };
  const thread = { id: 'thread-one', cwd: root, turns: [{ id: 'turn-one', status: 'inProgress', items: [] }] };
  c.threads.set(thread.id, { thread, model: model.modelID, modelProvider: model.providerID });
  const calls = [];
  c.rpc = async (method, params) => {
    calls.push({ method, params });
    if (method === 'thread/read') return { thread };
    if (method === 'turn/steer') return { turnId: params.expectedTurnId };
    if (method === 'turn/interrupt') { thread.turns[0].status = 'interrupted'; return {}; }
    if (method === 'thread/unsubscribe') return {};
    if (method === 'thread/resume') return { thread, model: 'own', modelProvider: 'openai' };
    throw new Error(method);
  };
  await c.client(model).session.promptAsync({ path: { id: thread.id }, body: { model, parts: [{ type: 'text', text: 'clarification' }] } });
  assert.equal(calls.find(x => x.method === 'turn/steer').params.expectedTurnId, 'turn-one');
  assert.ok(!calls.some(x => ['thread/resume', 'turn/start'].includes(x.method)));
  await assert.rejects(c.client().session.promptAsync({ path: { id: thread.id }, body: { model: { ...model, modelID: 'different' }, parts: [] } }), /another model/);
  const release = await c.release(thread.id);
  assert.equal(release.released, true); assert.deepEqual(release.command, ['codex', 'resume', thread.id]);
  assert.ok(calls.some(x => x.method === 'turn/interrupt'));
  await assert.rejects(c.resume(thread.id, model), /manual control/);
  await c.resume(thread.id, model, { reclaim: true });
  assert.equal(c.releasedThreads.has(thread.id), false);
  assert.ok(!calls.some(x => /delete|archive/.test(x.method)));
});

test('native release does not treat missing activity evidence as idle', async () => {
  const c = new CodexConnection({ binary: 'codex' });
  const calls = [];
  c.rpc = async method => { calls.push(method); return { thread: { id: 'unknown', turns: [], status: { type: 'systemError' } } }; };
  await assert.rejects(c.release('unknown'), /idle state unconfirmed/);
  assert.ok(!calls.includes('thread/unsubscribe'));
  assert.equal(c.releasedThreads.has('unknown'), true);
});

test('preflight rejection is not an unknown write and the same delivery can be retried explicitly', async t => {
  const { f, project } = await fixture(t);
  const c = new CodexConnection({ binary: 'fixture' });
  const model = { providerID: 'openai', modelID: 'new-model' };
  const thread = { id: 'worker', cwd: project, status: { type: 'active' }, turns: [{ id: 'old-turn', status: 'inProgress', items: [] }] };
  c.threads.set(thread.id, { thread, model: 'old-model', modelProvider: 'openai', persisted: true });
  let sends = 0;
  c.rpc = async (method, params) => {
    if (method === 'thread/read') return { thread };
    if (method === 'thread/resume') return { thread, model: params.model, modelProvider: params.modelProvider };
    if (method === 'turn/start') {
      sends++;
      thread.turns.push({ id: 'next', status: 'inProgress', items: [{ id: 'msg', type: 'userMessage', content: params.input }] });
      return { turn: thread.turns.at(-1) };
    }
    throw new Error(method);
  };
  const s = f.state(); s.workers.one = { name: 'one', sessionID: thread.id, directory: project, model, harness: 'codex' }; f.save(s);
  const rejected = await f.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model), 'delivery-one');
  assert.equal(rejected.state, 'not_sent');
  assert.equal(sends, 0);
  assert.equal(f.state().events.some(e => e.type === 'delivery_unconfirmed'), false);
  thread.turns[0].status = 'completed'; thread.status.type = 'idle';
  const retried = await f.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model), 'delivery-one');
  assert.equal(retried.state, 'confirmed'); assert.equal(sends, 1);
  assert.equal(retried.previousAttempts[0].token, rejected.token);
  assert.equal(retried.previousAttempts[0].state, 'not_sent');
  await f.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model), 'delivery-one');
  assert.equal(sends, 1);
});

test('native errors after the send boundary stay unknown and never unlock retry', async t => {
  const { f, project } = await fixture(t);
  const c = new CodexConnection({ binary: 'fixture' });
  const model = { providerID: 'openai', modelID: 'own' };
  const thread = { id: 'worker', cwd: project, status: { type: 'idle' }, turns: [] };
  c.threads.set(thread.id, { thread, model: model.modelID, modelProvider: model.providerID, persisted: true });
  let sends = 0;
  c.rpc = async method => {
    if (method === 'thread/read') return { thread };
    if (method === 'turn/start') { sends++; throw new Error('connection lost after write'); }
    throw new Error(method);
  };
  const s = f.state(); s.workers.one = { name: 'one', sessionID: thread.id, directory: project, model, harness: 'codex' }; f.save(s);
  assert.equal((await f.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model))).state, 'unconfirmed');
  await f.dispatch('ses_captain', 'one', 'T1', 'work', c.client(model));
  await assert.rejects(f.dispatch('ses_captain', 'one', 'T1', 'followup', c.client(model)), /unconfirmed/);
  assert.equal(sends, 1);
});

test('native status for one worker never reads another worker transcript', async () => {
  const c = new CodexConnection({ binary: 'fixture' });
  c.threads.set('unreachable', { persisted: true });
  c.threads.set('healthy', { persisted: true });
  const reads = [];
  c.read = async id => { reads.push(id); if (id === 'unreachable') throw new Error('unavailable'); return { id, status: { type: 'idle' } }; };
  assert.deepEqual(await c.client().session.status({ path: { id: 'healthy' } }), { data: { healthy: { type: 'idle' } } });
  assert.deepEqual(reads, ['healthy']);
});

test('real native Codex preserves configured effort across host reconnect and idle release', { skip: !process.env.STARFIX_TEST_CODEX }, async t => {
  const { f, config, dir, project } = await fixture(t);
  const c = new CodexConnection({ binary: process.env.STARFIX_TEST_CODEX, directory: project, home: path.join(dir, 'native-home') });
  let id;
  try {
    const model = { providerID: 'openai', modelID: testModelID };
    const worker = await f.createWorker('ses_captain', 'effort-native', project, c.client(model), model, undefined, { reasoningEffort: 'high' });
    id = worker.sessionID;
    assert.equal(worker.reasoningEffort, 'high');
    assert.equal(c.threads.get(id).reasoningEffort, 'high');
    const oldSocket = c.child;
    const oldPID = c.hostPID;
    await c.disconnect();
    await c.start();
    assert.equal(c.hostPID, oldPID, 'UI disconnection must preserve the native host');
    oldSocket.emit('error', new Error('Fixture: old connection error delivered after replacement'));
    assert.equal(c.threads.get(id)?.model, model.modelID, 'Reconnected native model metadata');
    assert.equal(c.threads.get(id)?.modelProvider, model.providerID, 'Reconnected native provider metadata');
    const attached = await c.resume(id, model);
    assert.equal(attached.reasoningEffort, 'high');
    assert.equal(new Fleet(config).state().workers['effort-native'].reasoningEffort, 'high');
    const result = await c.release(id);
    assert.equal(result.released, true);
    assert.equal(c.threads.has(id), false);
    assert.equal(c.releasedThreads.has(id), true);
    console.log('Native high effort and reconnect/release confirmed, no model turn started');
  } finally {
    // This thread was created by this test only; never touch an existing user thread.
    if (id) {
      try { await c.rpc('thread/archive', { threadId: id }); await c.rpc('thread/delete', { threadId: id }); }
      catch (error) {
        // Empty, never-run threads may have no persisted rollout to delete.
        for (const archived of [false, true]) {
          const listed = await c.rpc('thread/list', { cwd: project, archived });
          assert.ok(!listed.data.some(thread => thread.id === id), 'Test thread cleanup unconfirmed');
        }
      }
    }
    await c.close();
  }
});

test('collector keeps Codex quota but never shares it with a different OpenCode account', async t => {
  const { f, dir } = await fixture(t);
  const left = path.join(dir, 'open.json'), right = path.join(dir, 'codex.json');
  fs.writeFileSync(left, JSON.stringify({ openai: { type: 'oauth', accountId: 'fixture-a', access: 'not-a-real-secret' } }));
  fs.writeFileSync(right, JSON.stringify({ tokens: { account_id: 'fixture-a' } }));
  let calls = 0;
  const service = new Services(f, { codex: { rpc: async method => { assert.equal(method, 'account/rateLimits/read'); calls++; return { rateLimits: { primary: { usedPercent: 31 } } }; } }, opencodeAuth: left, codexAuth: right });
  await service.quota('ses_captain', { enabled: true, source: 'codex' });
  assert.equal((await service.collectQuota()).usedPercent, 31);
  await service.collectQuota(); assert.equal(calls, 1);
  fs.writeFileSync(right, JSON.stringify({ tokens: { account_id: 'fixture-b' } }));
  assert.equal(sameOpenAIAccount(left, right), false);
  assert.equal((await service.collectQuota(true)).status, 'GO'); assert.equal(calls, 2);
  assert.equal(f.state().quota.accountVerified, false);
  assert.equal(f.quotaProblem(f.state()), null);
  const telemetry = fs.readFileSync(path.join(f.home, 'quota.json'), 'utf8');
  assert.equal(telemetry.includes('fixture-a'), false); assert.equal(telemetry.includes('not-a-real-secret'), false);
});

test('Codex worker resumes its same independent thread with its own model and reads native history', async t => {
  const { f, project, config } = await fixture(t);
  const c = new CodexConnection({}); const calls = [], threads = new Map();
  const model = { providerID: 'openai', modelID: 'worker-model' };
  c.rpc = async (method, params) => {
    calls.push({ method, params });
    if (method === 'thread/start') {
      const thread = { id: `thread-${threads.size + 1}`, cwd: project, status: { type: 'idle' }, turns: [] }; threads.set(thread.id, thread);
      return { thread, model: params.model, modelProvider: params.modelProvider };
    }
    if (method === 'thread/name/set') return {};
    if (method === 'thread/read') return { thread: threads.get(params.threadId) };
    if (method === 'thread/resume') return { thread: threads.get(params.threadId), model: params.model, modelProvider: params.modelProvider };
    if (method === 'turn/start') {
      const thread = threads.get(params.threadId);
      const turn = { id: `turn-${thread.turns.length + 1}`, items: [{ id: `msg-${thread.turns.length + 1}`, type: 'userMessage', content: params.input }] };
      thread.turns.push(turn); return { turn };
    }
    throw new Error(method);
  };
  const worker = await f.createWorker('ses_captain', 'coder', project, c.client(model), model);
  const other = await f.createWorker('ses_captain', 'reviewer', project, c.client(model), model);
  assert.notEqual(worker.sessionID, other.sessionID); assert.equal(worker.harness, 'codex');
  const s = f.state(); s.model = { providerID: 'openai', modelID: 'different-captain-model' }; f.save(s);
  await c.client(model).session.promptAsync({ path: { id: worker.sessionID }, body: { model: worker.model, parts: [{ type: 'text', text: 'Remember fixture context' }] } });
  c.threads.clear(); await c.resume(worker.sessionID, new Fleet(config).state().workers.coder.model);
  const messages = await c.client(model).session.messages({ path: { id: worker.sessionID } });
  assert.equal(messages.data[0].parts[0].text, 'Remember fixture context');
  assert.equal(calls.filter(c => c.method === 'thread/start').length, 2);
  assert.ok(calls.filter(c => c.method === 'thread/resume').every(c => c.params.model === 'worker-model'));
  assert.deepEqual(threads.get(other.sessionID).turns, []);
});

test('two real command failures persist across restart, rebuild once and verify recovery', async t => {
  const { f, config, dir } = await fixture(t);
  const flag = path.join(dir, 'repaired');
  const probe = [process.execPath, '-e', `process.exit(require('node:fs').existsSync(${JSON.stringify(flag)}) ? 0 : 1)`];
  const repair = [process.execPath, '-e', `require('node:fs').appendFileSync(${JSON.stringify(flag)}, 'x')`];
  const guards = new ChannelGuards(f);
  await guards.configure('ses_captain', { action: 'register', name: 'fixture', probe, repair });
  await guards.scan(true); assert.equal(f.state().channels.fixture.failures, 1); assert.equal(fs.existsSync(flag), false);
  await new ChannelGuards(new Fleet(config)).scan(true);
  assert.equal(f.state().channels.fixture.lastRepair.recovered, true); assert.equal(fs.readFileSync(flag, 'utf8'), 'x');
  await guards.scan(true); assert.equal(fs.readFileSync(flag, 'utf8'), 'x');
});

test('a successful rebuild command without a successful probe is not reported recovered', async t => {
  const { f } = await fixture(t); const guards = new ChannelGuards(f);
  await guards.configure('ses_captain', { action: 'register', name: 'fixture', probe: [process.execPath, '-e', 'process.exit(1)'], repair: [process.execPath, '-e', 'process.exit(0)'] });
  await guards.scan(true); await guards.scan(true);
  assert.equal(f.state().channels.fixture.state, 'repair_failed');
  assert.deepEqual(f.state().channels.fixture.lastRepair.recovered, false);
});

test('unknown rebuild outcome is not blindly repeated after restart', async t => {
  const { f, config } = await fixture(t); const guards = new ChannelGuards(f);
  await guards.configure('ses_captain', { action: 'register', name: 'fixture', probe: [process.execPath, '-e', 'process.exit(1)'], repair: [process.execPath, '-e', 'setTimeout(()=>{}, 2000)'], timeoutSeconds: 0.2 });
  await guards.scan(true); await guards.scan(true);
  assert.equal(f.state().channels.fixture.state, 'repair_unconfirmed');
  const timestamp = f.state().channels.fixture.lastRepair.at;
  await new ChannelGuards(new Fleet(config)).scan(true);
  assert.equal(f.state().channels.fixture.lastRepair.at, timestamp);
});

test('actual Codex app-server disconnect needs two probes and recovers with a verified native RPC', { skip: !process.env.STARFIX_TEST_CODEX }, async t => {
  const { f, project } = await fixture(t);
  const c = new CodexConnection({ binary: process.env.STARFIX_TEST_CODEX, directory: project });
  try {
    await c.rpc('model/list'); await c.close();
    const guards = new ChannelGuards(f, c);
    await guards.configure('ses_captain', { action: 'register', name: 'codex-live', kind: 'codex' });
    await guards.scan(true); assert.equal(f.state().channels['codex-live'].failures, 1);
    await guards.scan(true); assert.equal(f.state().channels['codex-live'].lastRepair.recovered, true);
    assert.ok((await c.rpc('model/list')).data.length > 0);
  } finally { await c.close(); }
});

test('pending native approval is forwarded only by an explicit response, never by discovery', () => {
  const c = new CodexConnection({}); const writes = [];
  c.child = { stdin: { write: value => writes.push(JSON.parse(value)) } };
  c.approvals.set('8', { id: 8, method: 'item/commandExecution/requestApproval', params: { threadId: 'fixture' } });
  assert.equal([...c.approvals.values()].length, 1); assert.equal(writes.length, 0);
  c.answer('8', { decision: 'decline' });
  assert.deepEqual(writes, [{ id: 8, result: { decision: 'decline' } }]);
  assert.throws(() => c.answer('8', { decision: 'accept' }), /no longer pending/);
});

test('delayed native readback resolves a pending delivery without another model call', async t => {
  const { f } = await fixture(t); const s = f.state();
  s.workers.fixture = { sessionID: 'native-thread', directory: f.directory };
  s.deliveries.one = { worker: 'fixture', state: 'unconfirmed', token: 'STARFIX-fixture' }; f.save(s);
  const client = { session: { messages: async () => ({ data: [{ info: { role: 'user' }, parts: [{ type: 'text', text: 'Work\nDelivery: STARFIX-fixture' }] }] }) } };
  await f.confirmDeliveries('fixture', client);
  assert.equal(f.state().deliveries.one.state, 'confirmed');
});

test('quota stops dispatch but receipts and hourly observation continue, other providers are independent', async t => {
  const { f } = await fixture(t);
  const service = new Services(f);
  await service.quota('ses_captain', { enabled: true });
  fs.writeFileSync(path.join(f.home, 'quota.json'), JSON.stringify({ usedPercent: 99, status: 'STOP', observedAt: new Date().toISOString() }));
  await service.scanMonitors();
  assert.equal(f.state().paused, false);
  fs.writeFileSync(path.join(f.home, 'receipts', 'fresh.md'), 'PASS\n');
  await f.scan(); await f.hourlyReport();
  assert.ok(f.state().events.some(e => e.type === 'receipt_changed'));
  assert.ok(f.state().events.some(e => e.type === 'hourly_report'));
  const state = f.state(); state.quota.source = 'codex'; state.quota.accountVerified = true;
  assert.match(f.quotaProblem(state, { harness: 'codex', model: { providerID: 'openai' } }), /blocked/);
  assert.equal(f.quotaProblem(state, { harness: 'opencode', model: { providerID: 'other' } }), null);
});

test('uncertain wake is reconciled after restart without duplicate notification or global freeze', async t => {
  const { f, config } = await fixture(t);
  const state = f.state(); f.addEvent(state, 'fixture', 'one'); f.save(state);
  let text, sends = 0, visible = false;
  const client = { session: {
    status: async () => ({ data: {} }),
    promptAsync: async request => { sends++; text = request.body.parts[0].text; throw new Error('lost response'); },
    messages: async () => ({ data: visible ? [{ info: { role: 'user' }, parts: [{ type: 'text', text }] }] : [] }),
  } };
  await f.wake(client);
  assert.equal(f.state().paused, false); assert.equal(f.state().autoWake, true);
  const restart = new Fleet(config);
  await restart.wake(client); assert.equal(sends, 1);
  visible = true; await restart.wake(client);
  assert.equal(restart.state().wake.state, 'confirmed'); assert.equal(sends, 1);
});

test('writer lock reclaims a proven exited owner without touching unknown files', async t => {
  const { f } = await fixture(t);
  const { stdout } = await promisify(execFile)(process.execPath, ['-e', 'console.log(process.pid)']);
  const lock = path.join(f.home, '.opencode-writer-v2.lock'); fs.mkdirSync(lock);
  fs.writeFileSync(path.join(lock, `${stdout.trim()}-abcdef.owner`), '');
  await f.lock(async () => { assert.equal(fs.readdirSync(lock).length, 1); });
  assert.equal(fs.existsSync(lock), false);
});

test('writer lock serializes independent processes and preserves legacy lock ownership', async t => {
  const { f, config, dir } = await fixture(t);
  const counter = path.join(dir, 'counter'); fs.writeFileSync(counter, '0');
  const script = `const {Fleet}=await import(${JSON.stringify(new URL('../runtime.mjs', import.meta.url).href)}); const fs=await import('node:fs'); const f=new Fleet(${JSON.stringify(config)}); for(let i=0;i<12;i++)await f.lock(async()=>{const n=Number(fs.readFileSync(${JSON.stringify(counter)},'utf8'));await new Promise(r=>setTimeout(r,5));fs.writeFileSync(${JSON.stringify(counter)},String(n+1));});`;
  const results = await Promise.allSettled(Array.from({ length: 4 }, () => promisify(execFile)(process.execPath, ['--input-type=module', '-e', script], { windowsHide: true })));
  assert.deepEqual(results.filter(r => r.status === 'rejected').map(r => r.reason.stderr || r.reason.message), []);
  assert.equal(fs.readFileSync(counter, 'utf8'), '48');
  const legacy = path.join(f.home, '.opencode-writer.lock'); fs.mkdirSync(legacy);
  await assert.rejects(f.lock(async () => {}), /Legacy writer/);
  assert.ok(fs.existsSync(legacy)); fs.rmdirSync(legacy);
});

test('reattaching a loaded native worker does not resume or interrupt its active turn', async () => {
  const c = new CodexConnection({ home: 'not-used' });
  c.start = async () => { c.threads.set('worker', { model: 'fixed', modelProvider: 'openai', thread: { id: 'worker', status: { type: 'active' } } }); };
  c.rpc = async () => { throw new Error('Unexpected native resume'); };
  assert.equal((await c.resume('worker', { providerID: 'openai', modelID: 'fixed' })).thread.status.type, 'active');
});

test('offline audit conversion does not require a database profile', async t => {
  const { f, dir } = await fixture(t);
  const audit = new AuditRuntime(f, { bash });
  const worksheet = path.join(dir, 'worksheet.md'), output = path.join(dir, 'trace.jsonl');
  fs.writeFileSync(worksheet, `# 操作单 · ${privateInputs.changesetNo}\n- 候选 worktree：\`E:/fixture\`\n- 候选 HEAD：\`abc123\`\n## 《实际结果》回填表格\n| step | cmd | result | hint |\n|---|---|---|---|\n| A1 | git status | point=a1/verdict=PASS | fixture |\n`);
  const result = await audit.run('ses_captain', 'release-gate/worksheet_to_trace.py', [worksheet, output]);
  assert.equal(result.exitCode, 0, result.stderr);
  assert.equal(JSON.parse(fs.readFileSync(output, 'utf8').trim().split('\n').at(-1)).pass, true);
  await assert.rejects(audit.start('ses_captain'), /dependency profile/);
});

test('registered task completion uses the same explicit database binding as audit', async t => {
  const { f, dir } = await fixture(t);
  const receipt = path.join(f.home, 'receipts', 'done.md'); fs.writeFileSync(receipt, '# 终态：PASS\n');
  const audit = new AuditRuntime(f, { bash });
  const profile = path.join(dir, 'profile.json');
  fs.writeFileSync(profile, JSON.stringify({ container: 'fixture', database: 'fixture', baseRef: 'HEAD', baselineId: 0,
    dockerCommand: [process.execPath, '-e', 'console.log(1)', '--'] }));
  await audit.configure('ses_captain', profile);
  await f.activator('ses_captain', ['add', 'T1', 'fixture', '--receipt', receipt]);
  const result = await f.activator('ses_captain', ['set', 'T1', '\u5df2\u5b8c\u6210', '--reg', privateInputs.changesetNo]);
  assert.match(result, /T1/); assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).tasks[0].status, '\u5df2\u5b8c\u6210');
});

test('direct graph runs preserve prior SQLite evidence instead of creating a fresh history', async t => {
  const { f } = await fixture(t); const service = new Services(f, { bash });
  const receipt = path.join(f.home, 'receipts', 'sample.md'); fs.writeFileSync(receipt, '# PASS\n```text\nEvidence: fixture\n```\n');
  const first = await service.trajectory('ses_captain', { graph: 'receipt-compliance.v2.json', inputs: { receipt } });
  const second = await service.trajectory('ses_captain', { graph: 'receipt-compliance.v2.json', inputs: { receipt } });
  assert.equal(first.database, second.database); assert.equal(second.overall, 'PASS');
  const result = await promisify(execFile)(python, ['-B', '-c', 'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute("SELECT COUNT(DISTINCT run_id) FROM trace_runs").fetchone()[0])', second.database]);
  assert.ok(Number(result.stdout) >= 2);
});

test('exited audit poller is cleaned and re-started while retaining its configuration', async t => {
  const { f } = await fixture(t); const s = f.state(); s.audit = { enabled: true }; f.save(s);
  const audit = new AuditRuntime(f, { bash }); audit.child = { exitCode: 1 };
  let starts = 0, stops = 0;
  audit.stop = async (sid, preserve) => { assert.equal(preserve, true); stops++; audit.child = null; };
  audit.start = async () => { starts++; audit.child = { exitCode: null }; };
  await audit.poll(); await audit.poll();
  assert.equal(stops, 1); assert.equal(starts, 1);
});

test('native Codex execution survives UI disconnection and reattaches to the same host', { skip: !process.env.STARFIX_TEST_CODEX }, async t => {
  const { f, dir } = await fixture(t);
  const options = { binary: process.env.STARFIX_TEST_CODEX, directory: f.directory, home: f.home, node: process.execPath };
  const first = new CodexConnection(options), second = new CodexConnection(options);
  try {
  await first.start();
  const hostFile = path.join(f.home, 'codex-host/connection.json');
  const pid = JSON.parse(fs.readFileSync(hostFile)).pid;
  const started = path.join(dir, 'started'), finished = path.join(dir, 'finished');
  const execution = first.rpc('command/exec', { command: [process.execPath, '-e', `const fs=require('node:fs');fs.writeFileSync(${JSON.stringify(started)},'started');setTimeout(()=>fs.writeFileSync(${JSON.stringify(finished)},'finished'),1800)`], cwd: f.directory, timeoutMs: 10000 }).catch(e => e);
  const deadline = Date.now() + 15000;
  while (!fs.existsSync(started) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(fs.existsSync(started), 'Native command must actually start before UI disconnect');
  await first.disconnect(); await execution;
  await second.start(); assert.equal(JSON.parse(fs.readFileSync(hostFile)).pid, pid);
  while (!fs.existsSync(finished) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(fs.readFileSync(finished, 'utf8'), 'finished');
  assert.ok(await second.rpc('model/list'));
  } finally { await first.disconnect(); await second.start(); await second.close(); }
});

test('full audit preparation matches fresh GitHub source and executes original selftests', async t => {
  const { f, dir } = await fixture(t);
  const audit = new AuditRuntime(f, { bash });
  const profile = path.join(dir, 'audit-profile.json');
  fs.writeFileSync(profile, JSON.stringify({ container: 'fixture', database: 'fixture', baseRef: 'HEAD', baselineId: 0, dockerCommand: [process.execPath, '-e', 'process.exit(42)'] }));
  await audit.configure('ses_captain', profile);
  const prepared = await audit.prepare(); assert.ok(prepared.sourceFiles > 60);
  const manifest = JSON.parse(fs.readFileSync(path.join(f.home, 'audit/source-manifest.json'), 'utf8'));
  const upstream = fs.realpathSync(process.env.STARFIX_UPSTREAM_ROOT);
  for (const item of manifest) {
    const local = fs.readFileSync(path.join(sourceRoot, 'trajectory', item.path), 'utf8').replaceAll('\r\n', '\n');
    const remote = fs.readFileSync(path.join(upstream, 'trajectory', item.path), 'utf8').replaceAll('\r\n', '\n');
    assert.equal(local, remote, `Current code differs from GitHub: ${item.path}`);
  }
  for (const script of ['runner/checks/registration_settle.py', 'runner/checks/registration_gate.py', 'runner/checks/alert_consumer.py', 'runner/drift_scan.py']) {
    const result = await audit.run('ses_captain', script, ['--selftest']);
    if (script.endsWith('registration_gate.py')) {
      // GitHub anonymizes a positive date fixture, making A7 fail upstream too.
      // Preserve the real gate; compare with the freshly downloaded selftest.
      const original = await promisify(execFile)(python, ['-B', path.join(upstream, 'trajectory', script), '--selftest'], { env: { ...process.env, PYTHONUTF8: '1' }, windowsHide: true }).then(r => ({ exitCode: 0, output: r.stdout }), e => ({ exitCode: e.code, output: e.stdout }));
      assert.equal(result.exitCode, original.exitCode);
      assert.equal(result.output.replaceAll('\r\n', '\n'), original.output.replaceAll('\r\n', '\n'));
      assert.doesNotMatch(result.stderr, /Traceback/);
    } else assert.equal(result.exitCode, 0, `${script}: ${result.output}\n${result.stderr}`);
  }
  const result = await audit.run('ses_captain', 'runner/audit-poller.sh', ['--once']);
  assert.notEqual(result.exitCode, 0, 'Missing business backend must not pass');
  assert.match(result.output, /RUNNER_ERR/);
  const heartbeat = JSON.parse(fs.readFileSync(path.join(f.home, 'audit/data/changeset-audit/.audit-poller.heartbeat'), 'utf8'));
  assert.equal(heartbeat.ok, false);
  const recheck = await audit.run('ses_captain', 'runner/drift_scan.py', ['--recheck-selftest']);
  assert.equal(recheck.exitCode, 1, 'The author historical multi-repo commit is unavailable in the generic fixture');
  assert.match(recheck.output, /None/);
  assert.doesNotMatch(recheck.stderr, /Traceback/);
  const snapshot = await audit.run('ses_captain', 'runner/status_snapshot.py');
  assert.equal(snapshot.exitCode, 0, snapshot.output + snapshot.stderr);
  assert.ok(audit.status().originalSnapshot);
  t.after(async () => { await audit.stop(); });
  await audit.start('ses_captain');
  await new Promise(resolve => setTimeout(resolve, 2500));
  await audit.poll();
  assert.equal(audit.status().state, 'DOWN');
  assert.equal(audit.status().originalSnapshot.poller_alive, true, 'Native process evidence must recognize this actual persistent poller');
  const oldPID = audit.child.pid;
  if (process.platform === 'win32') await promisify(execFile)('taskkill.exe', ['/PID', String(oldPID), '/T', '/F'], { windowsHide: true });
  else audit.child.kill();
  await audit.exited;
  await audit.poll();
  assert.notEqual(audit.child.pid, oldPID, 'The original poller must rehang after an actual process crash');
  await audit.stop('ses_captain'); assert.equal(audit.status().enabled, false);
  // Stale-PID reclamation must also allow a subsequent original poller start.
  const restarted = await audit.run('ses_captain', 'runner/audit-poller.sh', ['--once']);
  assert.doesNotMatch(restarted.output, /lock:already_running_or_unrecoverable/);
});

test('original audit poller judges a synthetic registration, verifies SQLite and records its fingerprint', async t => {
  const { f, dir } = await fixture(t);
  const audit = new AuditRuntime(f, { bash });
  const profile = path.join(dir, 'profile.json');
  fs.writeFileSync(profile, JSON.stringify({ container: 'fixture', database: 'fixture', baseRef: 'HEAD', baselineId: 0,
    snapshot: { minimumRegistrations: 1, minimumFingerprints: 1, sentinelChangeset: privateInputs.changesetNo },
    dockerCommand: [python, '-B', fileURLToPath(new URL('./audit-backend.py', import.meta.url))] }));
  await audit.configure('ses_captain', profile);
  await audit.prepare();
  const oldAlert = path.join(f.home, `audit/alerts/机检告警-${privateInputs.changesetNo}.md`);
  const originalAlert = '# Synthetic prior alert - overall=FAIL\nOriginal evidence must remain.\n';
  fs.writeFileSync(oldAlert, originalAlert);
  const result = await audit.run('ses_captain', 'runner/audit-poller.sh', ['--audit-one', privateInputs.changesetNo]);
  if (result.exitCode !== 0) {
    const diagnostic = await audit.run('ses_captain', 'runner/run_graph.py', [path.join(audit.prepared.root, 'graphs/changeset-audit.v5.4.json'), '--input', `changeset_no=${privateInputs.changesetNo}`, '--workdir', path.join(f.home, 'audit/workdir'), '--run-tag', 'diagnostic']);
    assert.equal(diagnostic.exitCode, 0, diagnostic.output + diagnostic.stderr);
  }
  assert.equal(result.exitCode, 0, result.output + result.stderr);
  assert.match(result.output, new RegExp(`${privateInputs.changesetNo} N/A`));
  const pilot = path.join(f.home, 'audit/data/changeset-audit');
  const decisions = fs.readFileSync(path.join(pilot, '机检判决.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(decisions.at(-1).overall, 'N/A');
  assert.equal(decisions.at(-1).model_calls, 0);
  assert.equal(JSON.parse(fs.readFileSync(path.join(pilot, '判决指纹.jsonl'), 'utf8').trim()).changeset_no, privateInputs.changesetNo);
  assert.equal(JSON.parse(fs.readFileSync(path.join(pilot, 'A门判定.jsonl'), 'utf8').trim()).verdict, 'PASS');
  assert.ok(fs.readFileSync(oldAlert, 'utf8').startsWith(originalAlert));
  assert.equal(JSON.parse(fs.readFileSync(path.join(pilot, '告警撤回留痕.jsonl'), 'utf8').trim()).action, 'SUPERSEDED');
  const direct = await new Services(f, { bash }).trajectory('ses_captain', { graph: 'changeset-audit.v5.4.json', inputs: { changeset_no: privateInputs.changesetNo } });
  assert.equal(direct.overall, 'N/A', JSON.stringify(direct));
  assert.equal(direct.modelCalls, 0);
  assert.equal(path.resolve(direct.database), path.join(f.home, 'audit/runtime.db'));
  const snapshot = await audit.run('ses_captain', 'runner/status_snapshot.py');
  assert.equal(snapshot.exitCode, 0, snapshot.stderr);
  assert.equal(audit.status().originalSnapshot.backlog, 0, 'Explicit fixture baselines replace the author deployment counts, not the measurement guard');
  audit.prepared.env.STARFIX_FIXTURE_DRIFT = '1';
  const drift = await audit.run('ses_captain', 'runner/drift_scan.py', ['--scan', '--dry-run']);
  assert.equal(drift.exitCode, 0, drift.stderr); assert.match(drift.output, new RegExp(privateInputs.changesetNo));
});
