import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { Fleet } from '../runtime.mjs';
import { Services } from '../services.mjs';
import { loadPrivateTestInputs } from './test-inputs.mjs';

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const upstream = fs.realpathSync(process.env.STARFIX_UPSTREAM_ROOT);
const sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
const python = process.env.STARFIX_TEST_PYTHON;
const bash = process.env.STARFIX_TEST_BASH;
const privateInputs = loadPrivateTestInputs();
const lf = s => s.replaceAll('\r\n', '\n');

async function fixture(t, activate = true) {
  const dir = fs.mkdtempSync(path.join(root, 'parity-'));
  t.after(() => {
    assert.equal(path.dirname(fs.realpathSync(dir)), root);
    fs.rmSync(dir, { recursive: true });
    assert.equal(fs.existsSync(dir), false);
  });
  const project = path.join(dir, 'project with spaces');
  fs.mkdirSync(project);
  const config = { sourceRoot, directory: project, dataRoot: path.join(dir, 'data'), python };
  const f = new Fleet(config);
  if (activate) await f.activate('ses_captain');
  return { dir, project, config, f, service: new Services(f, { bash }) };
}

test('runtime dependencies match freshly downloaded GitHub source, not a local reference copy', () => {
  for (const file of ['scripts/task-activator.py', 'tools/scrub-gate.sh', 'tools/scrub_terms_check.py']) {
    assert.equal(lf(fs.readFileSync(path.join(sourceRoot, file), 'utf8')), lf(fs.readFileSync(path.join(upstream, file), 'utf8')), file);
  }
});

test('JSONL initial registration skips history; partial UTF-8 lines wait; restart resumes and permits a new correction', async t => {
  const { f, config, service } = await fixture(t, false);
  fs.mkdirSync(f.home, { recursive: true });
  fs.writeFileSync(f.askInbox, '{"qid":"Q1","answer":"old"}\n');
  await f.activate('ses_captain');
  await f.activator('ses_captain', ['add', 'T1', 'fixture']);
  await f.activator('ses_captain', ['ask', 'add', 'Q1', 'Question?', '--tasks', 'T1']);
  await service.applyInbox();
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[0].answer, '');
  const record = Buffer.from('{"qid":"Q1","answer":"可以执行","via":"ask-cli"}\n');
  const cut = record.indexOf(Buffer.from('以')) + 1;
  fs.appendFileSync(f.askInbox, record.subarray(0, cut));
  await service.applyInbox();
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[0].answer, '');
  fs.appendFileSync(f.askInbox, record.subarray(cut));
  const restarted = new Services(new Fleet(config), { bash });
  await restarted.applyInbox();
  const db = fs.readFileSync(f.dbFile, 'utf8');
  assert.equal(JSON.parse(db).tasks[0].blocker, '');
  assert.equal(JSON.parse(db).decisions[0].answer, '可以执行');
  assert.match(f.state().events.find(e => e.type === 'human_answer').subject, /Q1=可以执行[\s\S]*T1/);
  fs.appendFileSync(f.askInbox, record);
  await restarted.applyInbox();
  assert.equal(fs.readFileSync(f.dbFile, 'utf8'), db);
  assert.equal(f.state().events.filter(e => e.type === 'human_answer').length, 1);
  fs.appendFileSync(f.askInbox, '{"qid":"Q1","answer":"B"}\n');
  await restarted.applyInbox();
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[0].answer, 'B');
});

test('JSONL ignores malformed/unknown records, preserves multi-question blockers, and detects rewritten history', async t => {
  const { f, service } = await fixture(t);
  await f.activator('ses_captain', ['add', 'T1', 'fixture']);
  for (const qid of ['Q1', 'Q2']) await f.activator('ses_captain', ['ask', 'add', qid, 'Question?', '--tasks', 'T1']);
  fs.appendFileSync(f.askInbox, 'bad-json\n{"qid":"Qmissing","answer":"A"}\n{"qid":"Q1","answer":"A"}\n');
  await service.applyInbox();
  assert.match(JSON.parse(fs.readFileSync(f.dbFile)).tasks[0].blocker, /Q2/);
  assert.equal(f.state().events.filter(e => e.type === 'inbox_error').length, 2);
  const offset = f.state().askCursor.offset;
  fs.writeFileSync(f.askInbox, '{"qid":"Q2","answer":"A"}\n');
  await service.applyInbox();
  assert.equal(f.state().askCursor.offset, offset);
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[1].answer, '');
  assert.match(f.state().events.at(-1).subject, /not append-only/);
});

test('JSONL task-write/cursor-write interruption recovers without duplicate history or a lost notification', async t => {
  const { f, config, service } = await fixture(t);
  await f.activator('ses_captain', ['add', 'T1', 'fixture']);
  await f.activator('ses_captain', ['ask', 'add', 'Q1', 'Question?', '--tasks', 'T1']);
  fs.appendFileSync(f.askInbox, '{"qid":"Q1","answer":"A"}\n');
  const save = f.save.bind(f);
  f.save = s => { if (s.askCursor.offset > 0) throw new Error('simulated cursor I/O failure'); save(s); };
  await assert.rejects(service.applyInbox(), /cursor I\/O/);
  f.save = save;
  const db = fs.readFileSync(f.dbFile, 'utf8');
  assert.equal(JSON.parse(db).decisions[0].answer, 'A');
  assert.equal(f.state().askCursor.offset, 0);
  await new Services(new Fleet(config), { bash }).applyInbox();
  assert.equal(fs.readFileSync(f.dbFile, 'utf8'), db);
  assert.equal(f.state().events.filter(e => e.type === 'human_answer').length, 1);
  assert.match(f.state().events.at(-1).subject, /Recovered recorded answer/);
});

test('JSONL pending retry does not overwrite an intervening decision and backend failures do not consume input', async t => {
  const { f, service } = await fixture(t);
  await f.activator('ses_captain', ['ask', 'add', 'Q1', 'Question?']);
  fs.appendFileSync(f.askInbox, '{"qid":"Q1","answer":"A"}\n');
  const actualPython = f.python;
  f.python = path.join(f.home, 'missing-python.exe');
  await assert.rejects(service.applyInbox(), /Activator failed/);
  assert.equal(f.state().askCursor.offset, 0);
  f.python = actualPython;
  await f.activator('ses_captain', ['ask', 'answer', 'Q1', '--answer', 'B']);
  await service.applyInbox();
  assert.equal(JSON.parse(fs.readFileSync(f.dbFile)).decisions[0].answer, 'B');
  assert.equal(f.state().askCursor.lastResult.state, 'rejected');
});

test('hourly report is produced by original GitHub activator and repeats only at a new hour', async t => {
  const { f, project } = await fixture(t);
  await f.activator('ses_captain', ['add', 'T1', 'ready']);
  await f.activator('ses_captain', ['add', 'T2', 'blocked', '--blocker', 'Owner']);
  const receipt = path.join(project, 'result.md');
  fs.writeFileSync(receipt, '状态: PASS\n');
  await f.activator('ses_captain', ['add', 'T3', 'active', '--owner', 'crew-one', '--status', '施工中', '--receipt', receipt]);
  const expected = execFileSync(python, ['-B', path.join(upstream, 'scripts/task-activator.py'), 'report'], {
    cwd: project, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', FLEET_HOME: f.home, ACTIVATOR_JSON: f.dbFile },
  });
  const report = await f.hourlyReport();
  assert.equal(lf(report.report).split('\n').slice(1).join('\n'), lf(expected).split('\n').slice(1).join('\n'));
  assert.match(report.report, /可开工未派：T1/);
  assert.match(report.report, /回执已到\(PASS\)/);
  assert.equal(fs.readFileSync(report.file, 'utf8'), report.report);
  await f.hourlyReport();
  assert.equal(f.state().events.filter(e => e.type === 'hourly_report').length, 1);
  const state = f.state(); state.hour = 'old-hour'; f.save(state);
  await f.hourlyReport();
  assert.equal(f.state().hour, new Date().toISOString().slice(0, 13));
});

test('stall signals, roster, first-seen and hourly repeats match the remote sentinel', async t => {
  const { f, project, dir } = await fixture(t);
  const portable = p => p.replaceAll('\\', '/');
  let now = 1900000000;
  t.mock.timers.enable({ apis: ['Date'], now: now * 1000 });
  const content = path.join(project, 'work.txt'); fs.writeFileSync(content, 'old');
  fs.utimesSync(content, now - 10000, now - 10000);
  const receipt = path.join(f.home, 'receipt.md'); fs.writeFileSync(receipt, 'old'); fs.utimesSync(receipt, now - 10000, now - 10000);
  const tasks = [
    { id: 'active', owner: 'crew-one', status: '施工中', worktree: portable(project), receipt: 'receipt.md' },
    { id: 'queued', owner: 'crew-one', status: '待开工', worktree: portable(project) },
    { id: 'done', owner: 'crew-one', status: '已完成', worktree: portable(project) },
    { id: 'human', owner: 'Owner', status: '施工中', worktree: portable(project) },
    { id: 'waiting', owner: '队列→crew-one', status: '施工中', worktree: portable(project) },
  ];
  fs.writeFileSync(f.dbFile, JSON.stringify({ tasks }));
  // Execute the remote script with only stat/Python platform substitutions and
  // an injected clock. Do not replace its roster, activity or alert decisions.
  const script = path.join(dir, 'stall-sentinel.sh');
  const raw = fs.readFileSync(path.join(upstream, 'scripts/stall-sentinel.sh'), 'utf8');
  fs.writeFileSync(script, raw.replaceAll('stat -f %m', 'stat -c %Y')
    .replace('python3 "${D}/sentinel-roster.py"', '"$STARFIX_TEST_PYTHON" -B "${D}/sentinel-roster.py"')
    .replace('now=$(date +%s)', 'now=$STARFIX_TEST_NOW'));
  fs.copyFileSync(path.join(upstream, 'scripts/sentinel-roster.py'), path.join(dir, 'sentinel-roster.py'));
  const env = { ...process.env, FLEET_HOME: portable(f.home), ACTIVATOR_JSON: portable(f.dbFile),
    STARFIX_TEST_PYTHON: portable(python), PYTHONUTF8: '1', STALL_STATE: portable(path.join(dir, 'original-state')),
    STALL_SEEN: portable(path.join(dir, 'original-seen')), STALL_LEGACY_LIST: portable(path.join(dir, 'no-legacy')) };
  async function round(seconds, expected) {
    now += seconds; t.mock.timers.setTime(now * 1000);
    const original = execFileSync(bash, [portable(script), '--once'], { env: { ...env, STARFIX_TEST_NOW: String(now) }, encoding: 'utf8', windowsHide: true });
    const before = f.state().events.filter(e => e.type === 'stall_suspected').length;
    await f.scan();
    const added = f.state().events.filter(e => e.type === 'stall_suspected').slice(before);
    assert.equal(original.trim().length > 0, expected);
    assert.equal(added.length, expected ? 1 : 0);
    if (expected) assert.equal(added[0].subject, original.trim());
    for (const [local, remote] of [['.stall-seen', 'original-seen'], ['.stall-state', 'original-state']]) {
      assert.equal(lf(fs.readFileSync(path.join(f.home, local), 'utf8')), lf(fs.readFileSync(path.join(dir, remote), 'utf8')));
    }
  }
  await round(0, false); // New task must not inherit an old directory's age.
  await round(3600, false);
  await round(1, true); // No registered worker is needed by the original roster.
  await f.acknowledge('ses_captain', f.state().events.filter(e => e.type === 'stall_suspected').map(e => e.id));
  await round(3600, false);
  await round(1, true); // Acknowledgment does not disable hourly reminders.
  fs.utimesSync(content, now, now);
  await round(60, false);
  await round(3541, true);
  fs.utimesSync(receipt, now, now);
  await round(60, false);
  await round(3541, true);
  // Synthetic Git history is confined to this disposable test project.
  now += 1800; t.mock.timers.setTime(now * 1000);
  execFileSync('git', ['init', '-q', project], { windowsHide: true });
  execFileSync('git', ['-C', project, 'add', '--', 'work.txt'], { windowsHide: true });
  execFileSync('git', ['-C', project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], {
    windowsHide: true, env: { ...process.env, GIT_AUTHOR_DATE: `${now} +0000`, GIT_COMMITTER_DATE: `${now} +0000` },
  });
  fs.utimesSync(path.join(project, '.git', 'index'), now + 10000, now + 10000);
  await round(60, false); // New commit counts; a future .git/index mtime does not.
  await round(3541, true);
  tasks[0].status = '已完成'; fs.writeFileSync(f.dbFile, JSON.stringify({ tasks }));
  await round(3601, false);
});

function originalScrub(directory, termsFile, salt) {
  const env = { ...process.env, PYTHONUTF8: '1', STARFIX_PYTHON: python.replaceAll('\\', '/'), SCRUB_TERMS: termsFile.replaceAll('\\', '/') };
  if (salt !== undefined) env.SCRUB_SALT = salt; else delete env.SCRUB_SALT;
  return spawnSync(bash, ['-c', 'python3() { "$STARFIX_PYTHON" -B "$@"; }; export -f python3; bash "$1" "$2"', 'upstream-scrub',
    path.join(upstream, 'tools/scrub-gate.sh').replaceAll('\\', '/'), directory.replaceAll('\\', '/')], { env, encoding: 'utf8', windowsHide: true });
}

test('scrub adapter matches GitHub script for clean/degraded, structural failures, and salted private terms', async t => {
  const { dir, project, f, service } = await fixture(t);
  const termsFile = path.join(dir, 'private-terms.hmac');
  const saltFile = path.join(dir, 'private-salt');
  const salt = crypto.randomBytes(24).toString('hex');
  const content = path.join(project, 'note.md');
  fs.writeFileSync(content, 'Public project notes\n');
  for (const stage of ['clean', 'structure', 'terms', 'terms-clean']) {
    if (stage === 'structure') fs.writeFileSync(content, `${privateInputs.structureIp}\n`);
    if (stage === 'terms') {
      fs.writeFileSync(content, 'HarborNeedle\n');
      fs.writeFileSync(termsFile, crypto.createHmac('sha256', salt).update('HarborNeedle').digest('hex').slice(0, 16) + '\n');
      fs.writeFileSync(saltFile, salt);
    }
    if (stage === 'terms-clean') fs.writeFileSync(content, 'Public project notes\n');
    const privateLayer = stage.startsWith('terms');
    const expected = originalScrub(project, termsFile, privateLayer ? salt : undefined);
    const actual = await service.scrub('ses_captain', { directory: project, termsFile, saltFile: privateLayer ? saltFile : undefined });
    assert.equal(actual.exitCode, expected.status, `${stage}: ${actual.output} ${actual.stderr}`);
    assert.equal(lf(actual.output), lf(expected.stdout));
    assert.equal(actual.exitCode, ['clean', 'terms-clean'].includes(stage) ? 0 : 1);
    assert.equal(actual.output.includes(salt), false);
    if (stage === 'terms') assert.equal(actual.output.includes('HarborNeedle'), false);
    assert.equal(fs.readdirSync(path.join(f.home, '.scratch')).length, 0);
  }
});

test('scrub missing salt/dependencies cannot become a PASS', async t => {
  const { dir, service } = await fixture(t);
  const termsFile = path.join(dir, 'private-terms.hmac');
  fs.writeFileSync(termsFile, '1234567890abcdef\n');
  const salt = process.env.SCRUB_SALT;
  delete process.env.SCRUB_SALT;
  try {
    const result = await service.scrub('ses_captain', { termsFile });
    assert.equal(result.exitCode, 2);
    assert.equal(result.verdict, 'UNAVAILABLE');
  } finally { if (salt !== undefined) process.env.SCRUB_SALT = salt; }
  service.bash = path.join(dir, 'missing-bash.exe');
  await assert.rejects(service.scrub('ses_captain'), /execution failed/);
});
