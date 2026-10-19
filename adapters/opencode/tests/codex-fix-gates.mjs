// These gates accept only plain report data and intentionally have no I/O or release default.
export function evaluateRun(report, contract) {
  const reject = reason => ({ pass: false, reason });
  if (!report || report.fresh !== true || report.reused !== false) return reject('REUSE_DISABLED');
  if (!contract || !/^[0-9a-f]{64}$/.test(contract.identity ?? '') || report.identity !== contract.identity) return reject('IDENTITY_MISMATCH');
  if (report.closed !== true || report.timedOut !== false || report.exceeded !== false) return reject('RUN_INCOMPLETE');
  if (!Array.isArray(contract.tests) || contract.tests.length === 0 || !Array.isArray(report.results) ||
      report.results.length !== contract.tests.length) return reject('TEST_SET_MISMATCH');
  const names = new Set(report.results.map(test => test.nameHash));
  if (names.size !== report.results.length || new Set(contract.tests.map(test => test.nameHash)).size !== contract.tests.length) return reject('TEST_SET_MISMATCH');
  for (const expected of contract.tests) {
    const actual = report.results.find(test => test.nameHash === expected.nameHash);
    if (!actual || actual.status !== expected.status) return reject('TEST_RESULT_MISMATCH');
    if (actual.status === 'FAIL' && actual.assertionFailure !== true) return reject('UNRELATED_FAILURE');
    if (!['PASS', 'FAIL', 'SKIP'].includes(expected.status)) return reject('INVALID_CONTRACT');
  }
  const expectedExit = contract.tests.some(test => test.status === 'FAIL') ? 1 : 0;
  if (report.code !== expectedExit) return reject('EXIT_MISMATCH');
  return { pass: true, reason: 'EXACT_FRESH_MATCH' };
}

export function evaluateKnownFix(reports, contracts) {
  const negative = evaluateRun(reports?.negative, contracts?.negative);
  const candidate = evaluateRun(reports?.candidate, contracts?.candidate);
  return { knownFixVerification: negative.pass && candidate.pass ? 'PASS' : 'FAIL', negative, candidate };
}

// Historical compatibility only; callers must not use this as a release gate.
export function evaluateRepairAcceptanceLegacy(knownFixVerification, nativeReport, nativeContract) {
  if (knownFixVerification !== 'PASS' || !nativeReport ||
      !evaluateRun(nativeReport, nativeContract).pass || nativeReport.realRedactionZero !== true ||
      nativeReport.realEmptyPersisted !== true) return 'FAIL';
  return 'PASS';
}
