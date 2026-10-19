import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { Fleet } from '../runtime.mjs';
import { Services } from '../services.mjs';

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
const python = process.env.STARFIX_TEST_PYTHON;
const bash = process.env.STARFIX_TEST_BASH;

async function fixture(t) {
  const dir = fs.mkdtempSync(path.join(root, 'service-'));
  t.after(() => { assert.equal(path.dirname(fs.realpathSync(dir)), root); fs.rmSync(dir, { recursive: true }); assert.equal(fs.existsSync(dir), false); });
  const project = path.join(dir, 'project with spaces');
  fs.mkdirSync(project);
  const config = { sourceRoot, directory: project, dataRoot: path.join(dir, 'data'), python };
  const f = new Fleet(config);
  await f.activate('ses_captain');
  return { f, service: new Services(f, { bash }), config, dir, project };
}

test('six memory layers persist, index excludes daily/archive and archiving retains exact content', async t => {
  const { service, f, config } = await fixture(t);
  for (const layer of ['fact', 'crew', 'person', 'doctrine', 'ledger', 'daily']) {
    await service.memory('ses_captain', { action: 'put', layer, name: layer === 'daily' ? 'latest' : 'one', type: 'project', title: layer, content: `# ${layer}\nExact content\n` });
  }
  const original = await service.memory('ses_captain', { action: 'read', layer: 'fact', name: 'one' });
  await service.memory('ses_captain', { action: 'archive', layer: 'fact', name: 'one' });
  const index = fs.readFileSync(path.join(f.home, 'MEMORY.md'), 'utf8');
  assert.equal(index.includes('memory/one.md'), false);
  assert.equal(index.includes('daily_report_latest.md'), false);
  await assert.rejects(service.memory('ses_captain', { action: 'read', layer: 'fact', name: 'one' }), /ENOENT/);
  const restarted = new Services(new Fleet(config));
  assert.equal((await restarted.memory('ses_captain', { action: 'read', layer: 'fact', name: 'one', archive: true })).content, original.content);
  await restarted.memory('ses_captain', { action: 'restore', layer: 'fact', name: 'one' });
  assert.match(fs.readFileSync(path.join(f.home, 'MEMORY.md'), 'utf8'), /memory\/one.md/);
});

test('memory remains file-authoritative and unrelated writes preserve manual edits', async t => {
  const { service, f } = await fixture(t);
  const args = { action: 'put', layer: 'fact', name: 'one', type: 'feedback', title: 'Decision', content: 'missing sections' };
  await assert.rejects(service.memory('ses_captain', args), /Why/);
  args.content = 'Why: evidence\nHow to apply: check';
  await service.memory('ses_captain', args);
  fs.writeFileSync(path.join(f.home, 'memory/one.md'), 'User edit');
  assert.equal((await service.memory('ses_captain', { action: 'read', layer: 'fact', name: 'one' })).content, 'User edit');
  await service.memory('ses_captain', { ...args, name: '另一事实', content: 'Why: new\nHow to apply: verify' });
  assert.equal(fs.readFileSync(path.join(f.home, 'memory/one.md'), 'utf8'), 'User edit');
  assert.equal(f.state().memories, undefined);
  await service.memory('ses_captain', { action: 'delete', layer: 'fact', name: 'one' });
  assert.equal(fs.existsSync(path.join(f.home, 'memory/one.md')), false);
});

function enqueue(f, view, qid, answer) {
  const q = view.decisions.find(q => q.qid === qid);
  const name = `${crypto.randomUUID()}.json`;
  fs.writeFileSync(path.join(f.home, 'inbox', name), JSON.stringify({ captain: view.captain, qid, questionHash: q.questionHash, answer }));
  return name;
}

test('human inbox uses original dependency unlocking and duplicate answers do not repeat history', async t => {
  const { service, f } = await fixture(t);
  await f.activator('ses_captain', ['add', 'T1', 'fixture']);
  await f.activator('ses_captain', ['ask', 'add', 'Q1', 'First?', '--tasks', 'T1']);
  await f.activator('ses_captain', ['ask', 'add', 'Q2', 'Second?', '--tasks', 'T1']);
  const view = service.panelSnapshot();
  enqueue(f, view, 'Q1', 'A');
  await service.applyInbox();
  assert.match(JSON.parse(fs.readFileSync(f.dbFile)).tasks[0].blocker, /Q2/);
  enqueue(f, view, 'Q2', 'B');
  await service.applyInbox();
  const db = fs.readFileSync(f.dbFile, 'utf8');
  assert.equal(JSON.parse(db).tasks[0].blocker, '');
  const duplicate = enqueue(f, view, 'Q2', 'B');
  await service.applyInbox();
  assert.equal(f.state().inboxResults[duplicate].state, 'duplicate');
  assert.equal(fs.readFileSync(f.dbFile, 'utf8'), db);
  const conflicting = enqueue(f, view, 'Q2', 'C');
  await service.applyInbox();
  assert.equal(f.state().inboxResults[conflicting].state, 'rejected');
  assert.equal(fs.readFileSync(f.dbFile, 'utf8'), db);
});

test('changed questions and previous captains cannot apply stale answers', async t => {
  const { service, f } = await fixture(t);
  await f.activator('ses_captain', ['ask', 'add', 'Q1', 'Approve?']);
  const view = service.panelSnapshot();
  await f.activator('ses_captain', ['ask', 'detail', 'Q1', 'Scope changed']);
  const stale = enqueue(f, view, 'Q1', 'A');
  await service.applyInbox();
  assert.equal(f.state().inboxResults[stale].state, 'rejected');
  const currentView = service.panelSnapshot();
  await f.activate('ses_next', true);
  const oldCaptain = enqueue(f, currentView, 'Q1', 'B');
  await service.applyInbox();
  assert.equal(f.state().inboxResults[oldCaptain].state, 'rejected');
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[0].answer, '');
});

test('reminders and file waits survive restart and fire only once', async t => {
  const { service, f, config, project } = await fixture(t);
  await service.monitor('ses_captain', { action: 'remind', name: 'time', at: new Date(Date.now() - 60 * 60 * 1000).toISOString(), message: 'Due' });
  const target = path.join(project, 'result.md');
  await service.monitor('ses_captain', { action: 'wait_file', name: 'file', path: target, message: 'Inspect result' });
  await service.scanMonitors();
  assert.equal(f.state().events.filter(e => e.type === 'scheduled').length, 1);
  fs.writeFileSync(target, 'ready');
  const restarted = new Services(new Fleet(config));
  await restarted.scanMonitors();
  await f.acknowledge('ses_captain', f.state().events.map(e => e.id));
  await restarted.scanMonitors();
  assert.equal(f.state().events.filter(e => e.type === 'scheduled').length, 2);
  assert.equal(f.state().events.some(e => !e.ack), false);
  await assert.rejects(service.monitor('ses_captain', { action: 'remind', name: 'bad', at: new Date().toISOString().replace(/Z$/, ''), message: 'bad' }), /timezone/);
});

test('quota gate fails on missing, stale, non-GO and >=95 percent, but not healthy telemetry', async t => {
  const { service, f } = await fixture(t);
  await service.quota('ses_captain', { enabled: true });
  await service.scanMonitors();
  assert.equal(f.state().paused, false);
  assert.ok(f.state().quota.blocked);
  for (const q of [{ usedPercent: 95, status: 'GO', observedAt: new Date().toISOString() },
    { usedPercent: 1, status: 'STOP', observedAt: new Date().toISOString() },
    { usedPercent: 1, status: 'GO', observedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() }]) {
    fs.writeFileSync(path.join(f.home, 'quota.json'), JSON.stringify(q));
    await f.control('ses_captain', 'resume');
    await service.scanMonitors();
    assert.equal(f.state().paused, false);
    assert.ok(f.state().quota.blocked);
  }
  fs.writeFileSync(path.join(f.home, 'quota.json'), JSON.stringify({ usedPercent: 10, status: 'GO', observedAt: new Date().toISOString() }));
  await f.control('ses_captain', 'resume');
  await service.scanMonitors();
  assert.equal(f.state().paused, false);
});

test('quota is checked at dispatch time, not only at the next monitor tick', async t => {
  const { service, f } = await fixture(t);
  const state = f.state(); state.workers['crew-one'] = { sessionID: 'worker', model: { providerID: 'fixture', modelID: 'test' } }; f.save(state);
  await service.quota('ses_captain', { enabled: true });
  let called = false;
  await assert.rejects(f.dispatch('ses_captain', 'crew-one', 'T1', 'fixture', { session: { status: async () => { called = true; } } }), /Quota/);
  assert.equal(called, false);
  assert.equal(f.state().paused, false);
});

test('quota reset resumes quota-paused scheduling but never overrides a user pause', async t => {
  const { service, f } = await fixture(t);
  await service.quota('ses_captain', { enabled: true });
  await service.scanMonitors();
  assert.ok(f.state().quota.blocked);
  fs.writeFileSync(path.join(f.home, 'quota.json'), JSON.stringify({ observedAt: new Date().toISOString(), usedPercent: 10, status: 'GO' }));
  await service.scanMonitors();
  assert.equal(f.state().paused, false);
  assert.equal(f.state().autoWake, true);
  await f.control('ses_captain', 'pause');
  await service.scanMonitors();
  assert.equal(f.state().paused, true);
  assert.equal(f.state().pauseCause, 'user');
});

test('original receipt graph really executes on Windows with whitespace paths and persisted evidence', async t => {
  const { service, f } = await fixture(t);
  const receipt = path.join(f.home, 'receipts', 'sample with spaces.md');
  fs.writeFileSync(receipt, '# Status\nPASS\n```text\nEvidence: fixture\n```\n');
  const result = await service.trajectory('ses_captain', { graph: 'receipt-compliance.v2.json', inputs: { receipt } });
  assert.equal(result.overall, 'PASS', JSON.stringify(result));
  assert.ok(result.evidenceRows > 0);
  assert.equal(result.modelCalls, 0);
  assert.ok(fs.existsSync(result.database));
  fs.writeFileSync(receipt, '# Status\nthis is not a verdict\n');
  const broken = await service.trajectory('ses_captain', { graph: 'receipt-compliance.v2.json', inputs: { receipt } });
  assert.notEqual(broken.overall, 'PASS');
  assert.notEqual(broken.inputFingerprint, result.inputFingerprint);
});

test('original premerge graph uses explicit base and detects dirty tracked files', async t => {
  const { service, project } = await fixture(t);
  execFileSync('git', ['clone', '--local', '--no-hardlinks', sourceRoot, project], { windowsHide: true, stdio: 'pipe' });
  const inputs = { worktree: project, base: 'HEAD~1' };
  const result = await service.trajectory('ses_captain', { graph: 'premerge-gate.v7.json', inputs });
  assert.equal(result.overall, 'PASS', JSON.stringify(result));
  fs.appendFileSync(path.join(project, 'README.md'), '\nfixture dirty change\n');
  const dirty = await service.trajectory('ses_captain', { graph: 'premerge-gate.v7.json', inputs });
  assert.equal(dirty.verdicts.g1, 'FAIL');
  assert.equal(dirty.overall, 'FAIL');
});

test('original compiler produces a graph and source drift is detectable without executing commands', async t => {
  const { service, project } = await fixture(t);
  const source = path.join(project, 'SKILL.md');
  fs.writeFileSync(source, '# Fixture\n\n```bash\necho READY\n```\n');
  const compiled = await service.compile('ses_captain', { action: 'compile', source });
  assert.ok(fs.existsSync(compiled.output));
  const graph = JSON.parse(fs.readFileSync(compiled.output));
  assert.equal(graph.state, 'draft');
  const checked = await service.compile('ses_captain', { action: 'validate', source: compiled.output });
  assert.equal(JSON.parse(checked.result).ok, true);
  const before = await service.compile('ses_captain', { action: 'detect', source: compiled.output });
  fs.appendFileSync(source, '\n## Changed\nNew condition\n');
  const after = await service.compile('ses_captain', { action: 'detect', source: compiled.output });
  assert.notEqual(after.result, before.result);
});

test('captain-provided PASS cannot replace upstream independent fallback', async t => {
  const { service, f } = await fixture(t);
  const receipt = path.join(f.home, 'receipts', 'pending.md');
  fs.writeFileSync(receipt, '# Status\nPASS\n');
  const args = { graph: 'receipt-compliance.v2.json', inputs: { receipt } };
  const pending = await service.trajectory('ses_captain', args);
  assert.equal(pending.overall, 'NEEDS_HUMAN');
  const decisions = [{ fingerprint: '*', pass: true, reason: 'Captain says PASS' }];
  const rejected = await service.trajectory('ses_captain', { ...args, decisions });
  assert.equal(rejected.overall, 'NEEDS_HUMAN');
  assert.ok(rejected.modelCalls > 0);
  fs.appendFileSync(receipt, 'Different input\n');
  const changed = await service.trajectory('ses_captain', { ...args, decisions });
  assert.equal(changed.adjudications, undefined);
  assert.equal(changed.overall, 'NEEDS_HUMAN');
});

test('original compiler update and original block executor remain accessible', async t => {
  const { service, project } = await fixture(t);
  const source = path.join(project, 'SKILL.md');
  fs.writeFileSync(source, '# Fixture\n\n```bash\nprintf READY\n```\n');
  const compiled = await service.compile('ses_captain', { action: 'compile', source });
  fs.appendFileSync(source, '\n## New\nNew paragraph\n');
  const updated = await service.compile('ses_captain', { action: 'update', source: compiled.output });
  assert.ok(updated.result.length > 0);
  const blocks = path.join(project, 'blocks.json');
  fs.writeFileSync(blocks, JSON.stringify([{ kind: 'command', cmd: 'printf READY', line_start: 1 }]));
  const run = await service.compile('ses_captain', { action: 'execute', source: blocks, args: ['--skill', 'fidelity-fixture'] });
  assert.ok(run.result.includes('READY'), run.result);
  assert.equal(JSON.parse(run.result).canary.verdict, 'BASELINE_WRITTEN');
  const repeated = await service.compile('ses_captain', { action: 'execute', source: blocks, args: ['--skill', 'fidelity-fixture'] });
  assert.notEqual(JSON.parse(repeated.result).canary.verdict, 'BASELINE_WRITTEN');
  assert.ok(JSON.parse(repeated.result).log.includes(path.join('compiled', 'logs')));
});
