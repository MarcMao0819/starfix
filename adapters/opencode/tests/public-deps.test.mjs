import test from 'node:test';
import assert from 'node:assert/strict';
import { tool } from '@opencode-ai/plugin';
import { evaluateKnownFix, evaluateRepairAcceptanceLegacy, evaluateRun } from './codex-fix-gates.mjs';

test('public SDK tool factory is importable and usable', () => {
  assert.equal(typeof tool, 'function');
  assert.equal(typeof tool.schema.string, 'function');
  const definition = tool({
    description: 'Public dependency test tool',
    args: { input: tool.schema.string() },
    execute: async () => 'ok',
  });
  assert.equal(typeof definition, 'object');
  assert.equal(typeof definition.execute, 'function');
  assert.equal(definition.args.input.safeParse('ok').success, true);
});

test('verification gate rejects missing skipped unrelated and cached evidence', () => {
  const contract = { identity: 'a'.repeat(64), tests: [{ nameHash: 'case-a', status: 'FAIL' }, { nameHash: 'control', status: 'PASS' }] };
  const valid = { fresh: true, reused: false, identity: contract.identity, closed: true, timedOut: false, exceeded: false,
    code: 1, results: [{ nameHash: 'case-a', status: 'FAIL', assertionFailure: true }, { nameHash: 'control', status: 'PASS', assertionFailure: false }] };
  assert.equal(evaluateRun(valid, contract).pass, true);
  assert.equal(evaluateRun({ ...valid, results: valid.results.slice(0, 1) }, contract).reason, 'TEST_SET_MISMATCH');
  assert.equal(evaluateRun({ ...valid, results: [{ ...valid.results[0], status: 'SKIP' }, valid.results[1]] }, contract).reason, 'TEST_RESULT_MISMATCH');
  assert.equal(evaluateRun({ ...valid, results: [{ ...valid.results[0], assertionFailure: false }, valid.results[1]] }, contract).reason, 'UNRELATED_FAILURE');
  assert.equal(evaluateRun({ ...valid, results: [valid.results[0], valid.results[0]] }, contract).reason, 'TEST_SET_MISMATCH');
  assert.equal(evaluateRun({ ...valid, code: 0 }, contract).reason, 'EXIT_MISMATCH');
  assert.equal(evaluateRun({ ...valid, fresh: false, reused: true }, contract).reason, 'REUSE_DISABLED');
  assert.equal(evaluateRun({ ...valid, identity: 'b'.repeat(64) }, contract).reason, 'IDENTITY_MISMATCH');
  assert.equal(evaluateRun({ ...valid, timedOut: true }, contract).reason, 'RUN_INCOMPLETE');
  assert.equal(evaluateKnownFix({ negative: valid, candidate: { ...valid, results: [] } }, { negative: contract, candidate: contract }).knownFixVerification, 'FAIL');
  assert.equal(evaluateRepairAcceptanceLegacy('PASS', null, contract), 'FAIL');
  assert.equal(evaluateRepairAcceptanceLegacy('PASS', { ...valid, reused: true, realRedactionZero: true, realEmptyPersisted: true }, contract), 'FAIL');
});
