import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Fleet } from '../runtime.mjs';
import { CodexConnection } from '../codex.mjs';

// Fail before a temporary root or native connection can be created.
const testModelID = process.env.STARFIX_TEST_MODEL_ID;
if (typeof testModelID !== 'string' || testModelID.trim().length === 0) {
  throw new Error('STARFIX_TEST_MODEL_ID is required');
}

const root = fs.realpathSync(process.env.STARFIX_TEST_ROOT);
const dir = fs.mkdtempSync(path.join(root, 'codex-live-'));
const model = { providerID: 'openai', modelID: testModelID };
const c = new CodexConnection({ binary: process.env.STARFIX_TEST_CODEX, directory: dir });
let worker;
try {
  const fleet = new Fleet({ sourceRoot: fileURLToPath(new URL('../../../', import.meta.url)), directory: dir, dataRoot: path.join(dir, 'data'), python: process.env.STARFIX_TEST_PYTHON });
  await fleet.activate('ses_captain');
  worker = await fleet.createWorker('ses_captain', 'smoke', dir, c.client(model), model);
  const first = 'This is an isolated transport acceptance test. Do not use tools or change files. Remember the code STARFIX-PERSIST-7291. Reply only READY.';
  const delivery = await fleet.dispatch('ses_captain', 'smoke', 'TEST-1', first, c.client(model), 'first');
  console.log(JSON.stringify({ stage: 'first-dispatch', state: delivery.state }));
  async function waitTurn(count) {
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const thread = await c.read(worker.sessionID, true);
      if (thread.turns.length >= count && thread.turns[count - 1].status !== 'inProgress') {
        assert.equal(thread.turns[count - 1].status, 'completed');
        return thread.turns[count - 1];
      }
      assert.equal(c.approvals.size, 0, 'Unexpected native approval: do not auto-approve');
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('Native turn completion timed out');
  }
  await waitTurn(1);
  await c.close(); await c.start(); await c.resume(worker.sessionID, model);
  const repeated = await fleet.dispatch('ses_captain', 'smoke', 'TEST-1', first, c.client(model), 'first');
  assert.equal(repeated.repeated, true);
  assert.equal(repeated.state, 'confirmed');
  const follow = await fleet.dispatch('ses_captain', 'smoke', 'TEST-2', 'What exact code did I ask you to remember? Reply with the code only. Do not use tools.', c.client(model), 'second');
  const turn = await waitTurn(2);
  if (follow.state !== 'confirmed') assert.equal((await fleet.dispatch('ses_captain', 'smoke', 'TEST-2', 'What exact code did I ask you to remember? Reply with the code only. Do not use tools.', c.client(model), 'second')).state, 'confirmed');
  const answer = turn.items.filter(item => item.type === 'agentMessage').map(item => item.text).join('\n');
  assert.match(answer, /STARFIX-PERSIST-7291/);
  assert.equal((await c.read(worker.sessionID, true)).turns.length, 2);
  console.log(JSON.stringify({ engine: 'Codex CLI app-server', model: model.modelID, modelCalls: 2, independentThread: true, processRestart: true, sameThread: true, readbackConfirmed: true, duplicateNotResent: true, nativeContextRemembered: true }));
} finally {
  if (worker) {
    await c.start();
    const thread = await c.read(worker.sessionID, true);
    const active = thread.turns.find(t => t.status === 'inProgress');
    if (active) await c.rpc('turn/interrupt', { threadId: worker.sessionID, turnId: active.id });
    await c.rpc('thread/archive', { threadId: worker.sessionID });
    await c.rpc('thread/delete', { threadId: worker.sessionID });
  }
  await c.close();
  assert.equal(path.dirname(fs.realpathSync(dir)), root);
  fs.rmSync(dir, { recursive: true });
  assert.equal(fs.existsSync(dir), false);
}
