import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { Fleet } from '../runtime.mjs';
import { fileURLToPath } from 'node:url';

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const binary = process.env.STARFIX_TEST_OPENCODE;
assert.ok(binary && fs.existsSync(binary), 'Set STARFIX_TEST_OPENCODE to the installed executable');
const dir = fs.mkdtempSync(path.join(root, 'host-'));
let child;
let exited;
let output = '';
try {
  const portProbe = net.createServer();
  portProbe.listen(0, '127.0.0.1');
  await once(portProbe, 'listening');
  const port = portProbe.address().port;
  await new Promise(resolve => portProbe.close(resolve));
  const temp = path.join(dir, 'temp');
  fs.mkdirSync(temp);
  // Isolate host state and temporary files while loading the real global plugin.
  // The only session belongs to this fixture. noReply persists native variant
  // metadata without invoking any model; finally deletes that exact session.
  child = spawn(binary, ['serve', '--hostname', '127.0.0.1', '--port', String(port)], {
    cwd: dir, windowsHide: true,
    env: { ...process.env, XDG_DATA_HOME: path.join(dir, 'data'),
      XDG_STATE_HOME: path.join(dir, 'state'), XDG_CACHE_HOME: path.join(dir, 'cache'),
      TEMP: temp, TMP: temp, OPENCODE_SERVER_USERNAME: 'starfix-smoke',
      OPENCODE_SERVER_PASSWORD: 'local-offline-smoke' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  exited = once(child, 'exit');
  for (const stream of [child.stdout, child.stderr]) stream.on('data', data => { output = (output + data).slice(-12000); });
  const get = async (route, options = {}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {
      ...options,
      headers: { Authorization: `Basic ${Buffer.from('starfix-smoke:local-offline-smoke').toString('base64')}`, 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(15000),
    });
    assert.equal(response.status, 200, `${route}: ${await response.clone().text()}`);
    return response.json();
  };
  let healthy = false;
  for (let i = 0; i < 60; i++) {
    if (child.exitCode !== null) throw new Error(`Host exited: ${output}`);
    try { await get('/global/health'); healthy = true; break; } catch { /* Wait only for server readiness. */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert.ok(healthy, `OpenCode did not become healthy: ${output}`);
  const ids = await get(`/experimental/tool/ids?directory=${encodeURIComponent(dir)}`);
  const expected = ['activate', 'read', 'status', 'control', 'task', 'sessions', 'worker', 'dispatch', 'notify', 'handoff', 'ack', 'panel', 'memory', 'monitor', 'quota', 'trajectory', 'compile', 'scrub', 'codex', 'channel', 'audit'].map(x => `starfix_${x}`);
  for (const id of expected) assert.ok(ids.includes(id), `Missing tool ${id}`);
  const commands = await get(`/command?directory=${encodeURIComponent(dir)}`);
  assert.ok(commands.some(command => command.name === 'starfix'), 'Missing /starfix command');
  const query = `?directory=${encodeURIComponent(dir)}`;
  const providers = await get(`/provider${query}`);
  const choice = providers.all.flatMap(p => Object.entries(p.models).map(([id, m]) => ({ providerID: p.id, modelID: id, variants: Object.keys(m.variants ?? {}) }))).find(m => m.variants.includes('high'));
  assert.ok(choice, 'Native model variant metadata unavailable');
  const model = { providerID: choice.providerID, modelID: choice.modelID };
  const native = await get(`/session${query}`, { method: 'POST', body: JSON.stringify({ title: 'StarFix isolated variant test' }) });
  try {
    await get(`/session/${native.id}/message${query}`, { method: 'POST', body: JSON.stringify({ model, variant: 'high', noReply: true, parts: [{ type: 'text', text: 'No response requested: isolated variant persistence fixture.' }] }) });
    const fleet = new Fleet({ sourceRoot: fileURLToPath(new URL('../../../', import.meta.url)), directory: dir, dataRoot: path.join(dir, 'fleet') });
    await fleet.activate('fixture-captain', false, false);
    const nativeClient = { session: {
      get: async ({ path: p }) => ({ data: await get(`/session/${p.id}${query}`) }),
      messages: async ({ path: p }) => ({ data: await get(`/session/${p.id}/message${query}`) }),
    } };
    const worker = await fleet.registerWorker('fixture-captain', 'native-worker', dir, native.id, nativeClient);
    assert.equal(worker.variant, 'high');
    assert.deepEqual(worker.model, model);
    await get(`/session/${native.id}/message${query}`, { method: 'POST', body: JSON.stringify({ model, variant: '', noReply: true, parts: [{ type: 'text', text: 'No response requested: native default variant fixture.' }] }) });
    const history = await get(`/session/${native.id}/message${query}`);
    assert.equal(history.findLast(message => message.info.role === 'user').info.model.variant, '');
    console.log(JSON.stringify({ host: 'OpenCode', tools: expected, command: 'starfix', modelCalls: 0, sessionsCreated: 1, nativeVariant: worker.variant, existingSettingsAdopted: true }));
  } finally { await get(`/session/${native.id}${query}`, { method: 'DELETE' }); }
} finally {
  if (child && child.exitCode === null) child.kill();
  if (exited) await exited;
  assert.equal(path.dirname(fs.realpathSync(dir)), root, 'Cleanup escaped test root');
  fs.rmSync(dir, { recursive: true });
  assert.equal(fs.existsSync(dir), false);
}
