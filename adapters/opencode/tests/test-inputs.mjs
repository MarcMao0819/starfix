import fs from 'node:fs';

// Private fixtures require an explicit path so public tests never inherit host data.
export function loadPrivateTestInputs() {
  const inputPath = process.env.STARFIX_PRIVATE_TEST_INPUTS;
  if (typeof inputPath !== 'string' || inputPath.length === 0) {
    throw new Error('STARFIX_PRIVATE_TEST_INPUTS is required');
  }
  let inputs;
  try {
    inputs = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  } catch {
    throw new Error('STARFIX_PRIVATE_TEST_INPUTS is unreadable or invalid');
  }
  if (!inputs || typeof inputs !== 'object' ||
      typeof inputs.structureIp !== 'string' || inputs.structureIp.length === 0 ||
      typeof inputs.changesetNo !== 'string' || inputs.changesetNo.length === 0) {
    throw new Error('STARFIX_PRIVATE_TEST_INPUTS has invalid structure');
  }
  return Object.freeze({ structureIp: inputs.structureIp, changesetNo: inputs.changesetNo });
}
