import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { once } from 'node:events';

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
if (process.argv[2] === '--cleanup') {
  const target = fs.realpathSync(process.argv[3]);
  assert.equal(path.dirname(target), root);
  assert.match(path.basename(target), /^panel-[A-Za-z0-9]+$/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(target, 'panel.json'))).captain, 'ses_fixture');
  assert.deepEqual(fs.readdirSync(path.join(target, 'inbox')), []);
  assert.deepEqual(fs.readdirSync(target).sort(), ['decisions.png', 'empty.png', 'inbox', 'minimum.png', 'panel.json', 'status.png']);
  fs.rmSync(target, { recursive: true });
  assert.equal(fs.existsSync(target), false);
  console.log('Owned panel fixture cleaned.');
  process.exit(0);
}
const dir = fs.mkdtempSync(path.join(root, 'panel-'));
try {
  fs.mkdirSync(path.join(dir, 'inbox'));
  fs.writeFileSync(path.join(dir, 'panel.json'), JSON.stringify({ project: 'E:\\example-project', captain: 'ses_fixture', paused: false,
    reason: '', updated: new Date().toISOString(), workers: [{ name: 'crew-one', status: 'idle', directory: 'E:\\example-worker' }],
    tasks: [{ id: 'T1', title: '验证隔离测试数据', status: 'pending', owner: 'crew-one', blocker: 'Q01' }], events: [],
    quota: { status: 'GO', usedPercent: 42, limitId: 'codex', observedAt: new Date().toISOString() }, channels: { codex: { state: 'healthy', failures: 0 } },
    audit: { state: 'RUNNING', checkedAt: new Date().toISOString() }, auditSnapshot: { health: 'UNKNOWN', poller_alive: true, backlog: null, problems: ['Synthetic fixture: business database not connected'] },
    decisions: [{ qid: 'Q01', who: 'Owner', question: '是否批准本机验证？', detail: '仅限隔离测试数据，不调用模型，不发送外部消息。',
      recommend: '执行本机验证。', tasks: ['T1'], questionHash: 'fixture-hash' }] }));
  const panelFile = fileURLToPath(new URL('../panel.ps1', import.meta.url));
  assert.deepEqual([...fs.readFileSync(panelFile).subarray(0, 3)], [0xef, 0xbb, 0xbf], 'Windows PowerShell 5.1 requires a UTF-8 BOM for Chinese');
  for (const [name, tab, minimum] of [['decisions', 'Decisions', false], ['status', 'Fleet Status', false], ['minimum', 'Decisions', true], ['empty', 'Decisions', false]]) {
    if (name === 'empty') {
      const snapshot = JSON.parse(fs.readFileSync(path.join(dir, 'panel.json')));
      snapshot.decisions = [];
      fs.writeFileSync(path.join(dir, 'panel.json'), JSON.stringify(snapshot));
    }
    const before = fs.readFileSync(path.join(dir, 'panel.json'));
    const screenshot = path.join(dir, `${name}.png`);
    // The Windows PowerShell host is the actual installed panel dependency,
    // not PowerShell 7, whose UTF-8 default could conceal an encoding regression.
    const output = execFileSync(process.env.STARFIX_TEST_POWERSHELL, ['-NoProfile', '-STA', '-File',
      panelFile, '-FleetHome', dir, '-RenderTest', screenshot, '-RenderTab', tab, ...(minimum ? ['-RenderMinimum'] : [])],
    { windowsHide: true, timeout: 30000, stdio: 'pipe', encoding: 'utf8' });
    const ui = JSON.parse(output);
    assert.equal(ui.title, 'StarFix 舰队面板');
    assert.deepEqual(ui.tabs, ['待确认事项', '舰队状态']);
    assert.equal(ui.submit, '提交答复');
    assert.equal(ui.pin, '窗口置顶');
    assert.match(ui.status, /已暂停：否.*舰员：1.*快照更新于/);
    assert.match(ui.fleetStatus, /任务[\r\n]+T1 \| 待处理/);
    assert.match(ui.fleetStatus, /crew-one \| 空闲/);
    assert.match(ui.fleetStatus, /账户额度[\r\n]+可用/);
    assert.equal(ui.detail.includes('建议：执行本机验证。'), name !== 'empty');
    assert.deepEqual(fs.readFileSync(path.join(dir, 'panel.json')), before, 'Rendering must not translate stored data');
    assert.ok(fs.statSync(screenshot).size > 3000);
    assert.deepEqual(fs.readdirSync(path.join(dir, 'inbox')), []);
    console.log(`Panel rendered without submitting an answer: ${screenshot}`);
  }
  if (process.env.STARFIX_TEST_INSPECT_PANEL === '1') {
    console.log('Press Enter after inspecting the screenshot to clean the test directory.');
    const deadline = setTimeout(() => process.stdin.emit('data', Buffer.from('\n')), 300000);
    process.stdin.resume();
    await once(process.stdin, 'data');
    process.stdin.pause();
    clearTimeout(deadline);
  }
} finally {
  assert.equal(path.dirname(fs.realpathSync(dir)), root);
  fs.rmSync(dir, { recursive: true });
  assert.equal(fs.existsSync(dir), false);
}
