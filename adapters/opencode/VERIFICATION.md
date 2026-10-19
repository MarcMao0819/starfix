# StarFix Verification Summary

This is a scoped evidence summary for the public candidate. It compresses prior host-local records without publishing local paths, session identifiers, fixture values, or private runtime material. A scoped PASS never upgrades the whole PR to a release PASS.

## Verification Summary

| Evidence label | Baseline or scope | Recorded result | Acceptance limit |
| --- | --- | --- | --- |
| Candidate baseline | `1e3f08600d51526186dbea79069d5884c5e5676a` for `pan264/starfix` | This is the public candidate under preparation. | No full candidate regression or release conclusion follows from this table. |
| Historical archive | `d9be075f5a9b49285daa0ec7db0ef536641bb043` | Archived material is retained as historical comparison evidence only. | No current-candidate test count is attributed to this archive, and it does not mean all tests passed. |
| Service shutdown | Named-result uniqueness, behavior witnesses, bounded close, ten candidates, six retained behaviors, and a normal CLI regression of 66 checks were accepted in the stated scope. | The before-drift and hidden-close scenarios record a JSON result of `BLOCKED`; their observed CLI process exit code is `0`. | Exit `0` alone is not acceptance PASS or proof of complete cleanup or lifecycle acceptance. |
| Public dependencies | Local `@opencode-ai/plugin` dependency is `1.18.30`; package, lock, ignore policy, pure gate, and the two `public-deps.test.mjs` assertions were independently accepted. | The package remains `private: true`. | This does not publish a package or provide private test material. |
| Public-dependency candidate slice | The current candidate's affected adapter/recovery tests recorded 54 passing cases with one real native case excluded from that slice. This is not the `d9be075f5a9b49285daa0ec7db0ef536641bb043` archive. | This is implementation evidence only. Independent acceptance covered only the two `public-deps.test.mjs` assertions. | It is neither an independent 54-case review nor a full candidate or upstream suite. |
| Test-input separation | Independent review accepted the limited eight TAP cases with zero skips, six input rejection paths, six candidate protections, and task cleanup. Missing or invalid Node input exits with `1`, and missing Python input exits with `64`. | This is limited acceptance of the named slice only. | Do not expand this limited result into a full suite, real native behavior, or disclosure of test values. |
| Public documentation | This document, README, and FIDELITY-AUDIT describe only the boundaries above. | Their validation is structural and hash-based. | Documentation validation is not a model, native, business, or full scrub test. |

## Public Checks

From the adapter directory, install the declared local dependency and run the two public dependency assertions:

```sh
npm ci
node --test tests/public-deps.test.mjs
```

`npm ci` installs the local dependency tree only. It does not register the adapter as an OpenCode plugin or register `/starfix` as a command or skill. Complete that separate host-registration prerequisite before attempting `/starfix`, as described in [README.md](README.md#host-registration). This documentation does not validate a host-installation end-to-end path.

Only `public-deps.test.mjs` supplies TAP functional assertions. `tests/codex-fix-gates.mjs` is a pure helper, not a functional test. `node --check tests/codex-fix-gates.mjs` is optional syntax parsing only: it does not import the module or execute assertions, and is not an acceptance command. The public dependency test is not a wildcard regression command and must not be used to imply that real-model, native, or private-input tests have run.

Tests that exercise private structural or registration fixtures require an explicitly selected machine-local input through `STARFIX_PRIVATE_TEST_INPUTS`. The public contract exposes only the required fields, `structureIp` and `changesetNo`. Missing or invalid input is a failure, never an implicit skip or pass.

## Local Regression

The final candidate run recorded **113 Node passes, zero failures, four native skips**, plus **three Python passes**. The Node total is 117, including the ten service-shutdown cases. Node module syntax and the PowerShell panel parser also passed. Panel rendering, host registration, standalone smoke scripts, and real model/native execution were not run.

Required process-local inputs:

- `STARFIX_TEST_ROOT`: an existing disposable directory outside the repository.
- `TMPDIR`: an existing Git Bash-compatible temporary directory inside that disposable root. The original sentinel uses this variable; a missing default `/tmp` is an environment failure, not a sentinel verdict.
- `STARFIX_TEST_PYTHON` and `STARFIX_TEST_BASH`: the selected Python and Git Bash executables.
- `STARFIX_UPSTREAM_ROOT`: an extracted archive of the freshly fetched `origin/main` at the candidate baseline above, not the installed adapter checkout.
- `STARFIX_PRIVATE_TEST_INPUTS`: the local fixture file described above.
- `STARFIX_TEST_MODEL_ID`: a nonblank mock identifier for this non-native run; this does not select a real model.
- `PYTHONUTF8=1` and `PYTHONDONTWRITEBYTECODE=1`: preserve Unicode inputs without leaving bytecode in the source tree.

Leave `STARFIX_TEST_CODEX`, `CODEX_FIX_MODULE_ROOT`, and `STARFIX_SERVICE_FIX_MODULE_ROOT` unset. This exercises the candidate's own module paths and skips the four real Codex cases. Setting `STARFIX_TEST_CODEX` enables those cases and requires a separate native/model decision; the empty-thread persistence case remains an unresolved expectation, not an accepted guarantee.

Run from this adapter directory after `npm ci` and input setup:

```sh
node --test --test-reporter=tap --test-concurrency=1 tests/adapter.test.mjs tests/services.test.mjs tests/parity.test.mjs tests/four-gaps.test.mjs tests/codex-recovery.test.mjs tests/service-shutdown.test.mjs tests/public-deps.test.mjs
python -B -m unittest discover -s tests -p test_platform_bridge.py -v
```

The initial run had one sentinel test failure caused by the absent Git Bash temporary directory. Setting `TMPDIR` resolved it; no upstream script or assertion was changed. Disposable roots must be removed after the run, including service-shutdown fixtures retained for caller-owned evidence inspection.

The public-tree check assembled the fetched upstream archive and the exact adapter additions, without dependency installations or runtime data. The original scrub gate passed all ten structural categories. Its private HMAC word list was unavailable, and that layer was not claimed as checked.

## Acceptance Boundaries

| Area | Current interpretation |
| --- | --- |
| Model and native execution | The four native Node cases require `STARFIX_TEST_CODEX`; standalone smoke scripts remain separate invocations. No real model or native execution was accepted by the local regression above. |
| Empty-thread recovery | A normal empty-thread EOF did not establish rollout or resume. The old-identifier requirement was withdrawn; a task-memory-driven new session is not restoration of an old thread. |
| Shutdown and visible windows | A background host or a disconnected UI does not demonstrate that every descendant has stopped. Only the recorded bounded-close scope is accepted. |
| Runtime activation | The running plugin host has not been switched as part of this work. |
| Business integrations | Production systems, databases, deployments, and external channels are not included in these results. |
| Privacy gate | The assembled public tree passed ten original structural categories. The private HMAC word-list layer was unavailable; structural-only coverage is not a complete privacy conclusion. |

## Evidence Reading Rules

- The candidate baseline and the historical archive must remain distinct in review, test reports, and PR text.
- A PASS applies only to the named slice, inputs, and observations. A JSON result of `BLOCKED` means the required condition was not established; the separately observed CLI exit code, including `0`, does not convert it to PASS.
- Do not replace a blocked negative case with an unrelated failure, skip, renamed fixture, ignored file, or exception.
- Do not copy credentials, runtime state, audit records, private test inputs, or HMAC material into a public branch to make a command runnable.
- The final source scrub and PR release decision require their own independent evidence after all public changes are assembled.

## Related Documents

- [Installation, configuration, and safe use](README.md#install)
- [Capability map and fidelity limits](FIDELITY-AUDIT.md#capability-map)
- [Publication boundary](FIDELITY-AUDIT.md#publication-boundary)
