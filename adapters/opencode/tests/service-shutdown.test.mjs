import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root = path.resolve(process.env.STARFIX_SERVICE_FIX_MODULE_ROOT ?? fileURLToPath(new URL('..', import.meta.url)));
const { ChannelGuards } = await import(pathToFileURL(path.join(root, 'channels.mjs')).href);
const { Services } = await import(pathToFileURL(path.join(root, 'services.mjs')).href);
const { Fleet } = await import(pathToFileURL(path.join(root, 'runtime.mjs')).href);
const { createStarfixPlugin } = await import(pathToFileURL(path.join(root, 'plugin.mjs')).href);
const testRoot = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const witnessVersion = 'service-shutdown-r2/v1';

function witness(scenario, observed, condition) {
  if (process.env.STARFIX_SERVICE_FIX_LEGACY_WITNESS === '1' && condition) {
    console.log(JSON.stringify({ event: 'service-fix-witness', version: witnessVersion, scenario, observed }));
  }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function channelFleet(config) {
  const state = { captain: 'ses_captain', channels: { guard: {
    kind: 'command', probe: ['probe'], repair: ['repair'], intervalSeconds: 1, timeoutSeconds: 1,
    nextAt: 0, failures: 0, state: 'new', ...config,
  } } };
  return { state: () => state, lock: async fn => fn(), save: () => {}, addEvent: () => {}, requireCaptain: () => state };
}

function inboxFleet(dir, { jsonl, pauseFirst = true }) {
  const home = path.join(dir, 'home');
  fs.mkdirSync(path.join(home, 'inbox'), { recursive: true });
  const askInbox = path.join(home, 'ask-inbox.jsonl');
  const db = { tasks: [], decisions: [
    { qid: 'Q1', question: 'One?', tasks: [], answer: '' },
    { qid: 'Q2', question: 'Two?', tasks: [], answer: '' },
  ] };
  const state = { captain: 'ses_captain', inboxResults: {} };
  if (jsonl) state.askCursor = { path: askInbox, offset: 0, prefixHash: sha256(Buffer.alloc(0)) };
  const controller = new AbortController();
  const entered = deferred(), release = deferred();
  const calls = [];
  const fleet = {
    home, askInbox, dbFile: path.join(home, 'tasks.json'), state: () => state, lock: async fn => fn(), save: () => {},
    addEvent: () => {}, requireCaptain: sessionID => { assert.equal(sessionID, 'ses_captain'); return state; },
    activator: async (_sessionID, args, validate) => {
      validate(db);
      calls.push(args[2]);
      if (pauseFirst && calls.length === 1) { entered.resolve(); await release.promise; }
      const q = db.decisions.find(item => item.qid === args[2]);
      q.answer = args.at(-1);
      return `applied ${q.qid}`;
    },
  };
  return { fleet, db, state, askInbox, entered, release, calls, stop: () => controller.abort(), signal: controller.signal };
}

function answer(qid, question, value) {
  return { captain: 'ses_captain', qid, answer: value,
    questionHash: sha256(JSON.stringify([qid, question, '', '', [], undefined])) };
}

function quotaFleet(dir) {
  const home = path.join(dir, 'quota-home'); fs.mkdirSync(home, { recursive: true });
  const state = { captain: 'ses_captain', quota: { enabled: true, source: 'codex', limitId: 'codex', maxAgeSeconds: 300, revision: 'old' } };
  return { home, state: () => state, lock: async fn => fn(), save: () => {},
    requireCaptain: sessionID => { assert.equal(sessionID, 'ses_captain'); return state; }, stateObject: state };
}

function testDir(t) {
  void t;
  // The verifier owns and removes the single task root only after every tracked
  // child closes, avoiding per-case deletion races with local probe processes.
  return fs.mkdtempSync(path.join(testRoot, 'service-shutdown-'));
}

test('S01 shutdown during an in-flight probe starts no repair', async t => {
  const controller = new AbortController();
  const repairFlag = path.join(testDir(t), 'repair-started');
  const started = deferred(), release = deferred();
  const fleet = channelFleet({ failures: 1,
    repair: [process.execPath, '-e', `require('node:fs').writeFileSync(${JSON.stringify(repairFlag)}, '1')`] });
  const guards = new ChannelGuards(fleet, null, { signal: controller.signal });
  guards.probe = async () => { started.resolve(); await release.promise; throw new Error('probe failed'); };
  const scan = guards.scan(true);
  await started.promise;
  controller.abort();
  release.resolve();
  await scan;
  const channel = fleet.state().channels.guard;
  witness('S01-inflight-probe', fs.existsSync(repairFlag) ? 'repair-started' : 'stale-probe-state-committed',
    fs.existsSync(repairFlag) || channel.failures !== 1 || channel.state !== 'new');
  assert.equal(fs.existsSync(repairFlag), false, 'shutdown must stop before the configured repair command is invoked');
  assert.equal(channel.failures, 1, 'shutdown must not apply a stale failed-probe result');
  assert.equal(channel.state, 'new', 'shutdown must leave the channel state unchanged');
});

test('S01 shutdown after repair submission preserves unknown state and avoids a blind retry', async () => {
  const controller = new AbortController();
  let repairs = 0;
  const started = deferred(), release = deferred();
  const fleet = channelFleet({ failures: 1, repair: [process.execPath, '-e', 'process.exit(0)'] });
  const guards = new ChannelGuards(fleet, null, { signal: controller.signal });
  guards.probe = async () => { throw new Error('probe failed'); };
  guards.repair = async () => {
    repairs += 1;
    started.resolve();
    await release.promise;
    const error = new Error('transport closed after submission'); error.code = 'EPIPE'; throw error;
  };
  const scan = guards.scan(true);
  await started.promise;
  controller.abort();
  release.resolve();
  await scan;
  assert.equal(fleet.state().channels.guard.repairUnconfirmed, true, 'a submitted repair remains unknown after shutdown');
  const restarted = new ChannelGuards(fleet);
  restarted.probe = guards.probe;
  restarted.repair = guards.repair;
  await restarted.scan(true);
  assert.equal(repairs, 1, 'an unknown repair is never replayed blindly');
});

test('S02 JSONL shutdown completes the entered transaction and leaves the next record untouched', async t => {
  const fixture = inboxFleet(testDir(t), { jsonl: true });
  fs.writeFileSync(fixture.askInbox, `${JSON.stringify({ qid: 'Q1', answer: 'A' })}\n${JSON.stringify({ qid: 'Q2', answer: 'B' })}\n`);
  const service = new Services(fixture.fleet, { signal: fixture.signal });
  const applying = service.applyJsonlInbox();
  await fixture.entered.promise;
  fixture.stop();
  fixture.release.resolve();
  await applying;
  witness('S02-jsonl-next-record', 'next-record-started', fixture.calls.includes('Q2'));
  assert.deepEqual(fixture.calls, ['Q1']);
  assert.equal(fixture.db.decisions[0].answer, 'A');
  assert.equal(fixture.db.decisions[1].answer, '');
  assert.ok(fixture.state.askCursor.offset > 0, 'the entered record keeps its durable cursor result');
});

test('S02 file inbox shutdown completes the entered transaction and starts no second file', async t => {
  const fixture = inboxFleet(testDir(t), { jsonl: false });
  fs.writeFileSync(path.join(fixture.fleet.home, 'inbox', `${crypto.randomUUID()}.json`), JSON.stringify(answer('Q1', 'One?', 'A')));
  fs.writeFileSync(path.join(fixture.fleet.home, 'inbox', `${crypto.randomUUID()}.json`), JSON.stringify(answer('Q2', 'Two?', 'B')));
  const service = new Services(fixture.fleet, { signal: fixture.signal });
  const applying = service.applyInbox();
  await fixture.entered.promise;
  fixture.stop();
  fixture.release.resolve();
  await applying;
  witness('S02-file-next-record', 'next-record-started', fixture.calls.includes('Q2'));
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.db.decisions.filter(q => q.answer).length, 1);
  assert.equal(Object.keys(fixture.state.inboxResults).length, 1, 'only the entered file gets a durable result');
});

test('S03 shutdown during quota RPC cannot commit stale telemetry', async t => {
  const started = deferred(), release = deferred();
  const controller = new AbortController();
  const fleet = quotaFleet(testDir(t));
  const service = new Services(fleet, { signal: controller.signal, codex: { rpc: async method => {
    assert.equal(method, 'account/rateLimits/read'); started.resolve(); await release.promise;
    return { rateLimits: { codex: { usedPercent: 20 } } };
  } } });
  const collecting = service.collectQuota(true);
  await started.promise;
  controller.abort();
  release.resolve();
  const result = await collecting;
  witness('S03-shutdown-quota', 'stale-telemetry-committed', result !== undefined ||
    fs.existsSync(path.join(fleet.home, 'quota.json')) || fleet.stateObject.quota.nextCollectionAt !== undefined);
  assert.equal(result, undefined);
  assert.equal(fs.existsSync(path.join(fleet.home, 'quota.json')), false);
  assert.equal(fleet.stateObject.quota.nextCollectionAt, undefined);
});

test('S03 reconfigured quota rejects an old RPC result without changing the new runtime fields', async t => {
  const started = deferred(), release = deferred();
  const fleet = quotaFleet(testDir(t));
  const service = new Services(fleet, { codex: { rpc: async () => {
    started.resolve(); await release.promise;
    return { rateLimits: { codex: { usedPercent: 20 } } };
  } } });
  const collecting = service.collectQuota(true);
  await started.promise;
  fleet.stateObject.quota = { enabled: true, source: 'codex', limitId: 'codex', maxAgeSeconds: 60, revision: 'new' };
  release.resolve();
  const result = await collecting;
  witness('S03-reconfigured-quota', 'old-rpc-overwrote-new-config', result !== undefined ||
    fs.existsSync(path.join(fleet.home, 'quota.json')) || fleet.stateObject.quota.revision !== 'new');
  assert.equal(result, undefined);
  assert.equal(fs.existsSync(path.join(fleet.home, 'quota.json')), false);
  assert.deepEqual(fleet.stateObject.quota, { enabled: true, source: 'codex', limitId: 'codex', maxAgeSeconds: 60, revision: 'new' });
});

test('S01 normal repair confirms a healthy post-repair probe', async () => {
  const fleet = channelFleet();
  const guards = new ChannelGuards(fleet);
  let probes = 0, repairs = 0;
  guards.probe = async () => { probes += 1; if (probes < 3) throw new Error('probe failed'); };
  guards.repair = async () => { repairs += 1; };
  await guards.scan(true);
  await guards.scan(true);
  const channel = fleet.state().channels.guard;
  assert.equal(repairs, 1);
  assert.equal(probes, 3);
  assert.equal(channel.state, 'healthy');
  assert.equal(channel.lastRepair.recovered, true);
});

test('S02 normal JSONL inbox processes two records', async t => {
  const fixture = inboxFleet(testDir(t), { jsonl: true, pauseFirst: false });
  fs.writeFileSync(fixture.askInbox, `${JSON.stringify({ qid: 'Q1', answer: 'A' })}\n${JSON.stringify({ qid: 'Q2', answer: 'B' })}\n`);
  const service = new Services(fixture.fleet);
  await service.applyJsonlInbox();
  assert.deepEqual(fixture.calls, ['Q1', 'Q2']);
  assert.deepEqual(fixture.db.decisions.map(q => q.answer), ['A', 'B']);
  assert.equal(fixture.state.askCursor.offset, fs.readFileSync(fixture.askInbox).length);
});

test('S03 reconfigured quota accepts a fresh RPC result', async t => {
  const started = deferred(), release = deferred();
  const fleet = quotaFleet(testDir(t));
  let calls = 0;
  const service = new Services(fleet, { codex: { rpc: async () => {
    calls += 1;
    if (calls === 1) { started.resolve(); await release.promise; }
    return { rateLimits: { codex: { usedPercent: calls === 1 ? 20 : 35 } } };
  } } });
  const old = service.collectQuota(true);
  await started.promise;
  await service.quota('ses_captain', { enabled: true, source: 'codex', limitId: 'codex', maxAgeSeconds: 60 });
  release.resolve();
  assert.equal(await old, undefined);
  const fresh = await service.collectQuota(true);
  assert.ok(fresh, 'the new configuration must accept its own RPC result');
  assert.equal(calls, 2);
  assert.equal(fleet.stateObject.quota.maxAgeSeconds, 60);
  assert.ok(fs.existsSync(path.join(fleet.home, 'quota.json')));
});

const schemaNode = new Proxy(() => schemaNode, { get: () => schemaNode });
const tool = definition => definition;
tool.schema = { enum: () => schemaNode, string: () => schemaNode, boolean: () => schemaNode, number: () => schemaNode,
  array: () => schemaNode, object: () => schemaNode, record: () => schemaNode };

test('S01 plugin shutdown waits for its full tick and never starts a late channel repair', async t => {
  const dir = testDir(t);
  const project = path.join(dir, 'project'); fs.mkdirSync(project);
  const config = { sourceRoot: path.resolve(root, '..', '..'), dataRoot: path.join(dir, 'data'), directory: project, python: process.execPath };
  const client = { session: {
    status: async () => ({ data: {} }), get: async () => ({ data: { id: 'ses_captain', directory: project } }),
    messages: async () => ({ data: [] }), promptAsync: async () => ({ data: undefined }), create: async () => ({ data: { id: 'worker' } }),
  }, app: { log: async () => {} } };
  const started = deferred(), release = deferred();
  const originalProbe = ChannelGuards.prototype.probe, originalRepair = ChannelGuards.prototype.repair;
  let repairs = 0;
  const repairFlag = path.join(dir, 'repair-started');
  ChannelGuards.prototype.probe = async () => { started.resolve(); await release.promise; throw new Error('fixture probe failed'); };
  ChannelGuards.prototype.repair = async () => { repairs += 1; };
  let plugin, fleet;
  try {
    // No subprocess is needed here: this is the plugin-to-service shutdown handoff.
    plugin = await createStarfixPlugin({ directory: project, client }, { ...config, tool, intervalMs: 20, shutdownTimeoutMs: 1000 });
    fleet = new Fleet(config);
    await fleet.activate('ses_captain');
    const context = { sessionID: 'ses_captain', ask: async () => {} };
    await plugin.tool.starfix_channel.execute({ action: 'register', name: 'late', kind: 'command', probe: ['fixture'],
      repair: [process.execPath, '-e', `require('node:fs').writeFileSync(${JSON.stringify(repairFlag)}, '1')`], intervalSeconds: 1, timeoutSeconds: 1 }, context);
    const state = fleet.state(); state.channels.late.failures = 1; state.channels.late.nextAt = 0; fleet.save(state);
    await started.promise;
    const disposing = plugin.dispose();
    release.resolve();
    const result = await disposing;
    const channel = fleet.state().channels.late;
    witness('S01-plugin-shutdown', repairs ? 'late-repair-started' : 'stale-probe-state-committed',
      repairs !== 0 || fs.existsSync(repairFlag) || channel.state !== 'unobserved' || channel.failures !== 1);
    assert.equal(result.observationSettled, true);
    assert.equal(repairs, 0, 'dispose must close the channel observer before its probe failure can start repair');
    assert.equal(fs.existsSync(repairFlag), false, 'dispose must prevent the legacy direct repair command as well');
    assert.equal(channel.state, 'unobserved', 'dispose must not commit a stale failed-probe state');
    assert.equal(channel.failures, 1, 'dispose must preserve the pre-existing failure count');
  } finally {
    release.resolve();
    if (plugin) await plugin.dispose();
    ChannelGuards.prototype.probe = originalProbe;
    ChannelGuards.prototype.repair = originalRepair;
  }
});
