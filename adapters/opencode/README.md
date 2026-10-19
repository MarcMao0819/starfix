# StarFix Windows / OpenCode Adapter

This directory contains the Windows/OpenCode adapter candidate for `pan264/starfix`. Its fixed upstream reference is `1e3f08600d51526186dbea79069d5884c5e5676a`. The older `d9be075f5a9b49285daa0ec7db0ef536641bb043` results are historical evidence only; they do not certify this candidate.

The adapter preserves the upstream task, delivery, approval, and audit boundaries. It is not a deployment guide, a production configuration, or proof that every upstream workflow has been accepted. Read the [capability and evidence boundary](FIDELITY-AUDIT.md#evidence-scope) and the [verification summary](VERIFICATION.md#verification-summary) before using it for a change decision.

## Install

Run installation from this adapter directory with a supported Node.js runtime:

```sh
npm ci
```

`package.json` declares `@opencode-ai/plugin` version `1.18.30` as the local development dependency. The package is marked `private: true`, which prevents accidental npm publication; it does not grant credentials, change global settings, or configure a model provider.

Keep credentials, private word lists, audit profiles, runtime state, and user data outside the repository. Installation alone does not activate a fleet, start a service, or contact an external system.

## Host Registration

`npm ci` installs only this directory's dependency tree. It does not register this adapter as an OpenCode plugin, or register a `/starfix` command or skill with an OpenCode host.

Before invoking `/starfix`, register the adapter through the actual host plugin and command or skill mechanism. The verified local loader constructs `createStarfixPlugin(input, config)` with configuration categories `tool` (the local public SDK), `sourceRoot`, `dataRoot`, `opencode`, `codex`, `opencodeAuth`, `codexAuth`, `python`, `bash`, and `powershell`. `opencodeAuth` and `codexAuth` refer to existing local authentication-file paths; retain those references locally and never copy their contents into this repository or a PR.

This candidate provides no automatic installer, host registration, or global-configuration mutation. Follow the OpenCode plugin documentation that matches the actual host version when completing the separate registration step. Documentation review is not an installation end-to-end test.

## Safe Use

Only after the separate host-registration prerequisite is complete, start OpenCode in the intended project and invoke `/starfix`. Use `starfix_sessions` to inspect the sessions and the model identifiers actually available in that runtime before creating or configuring a worker. This documentation intentionally does not name a fixed model identifier: availability and supported reasoning settings belong to the live session list.

Use the adapter tools for their defined responsibilities:

| Need | Adapter entry point | Boundary |
| --- | --- | --- |
| Fleet activation and status | `starfix_activate`, `starfix_status` | Activation does not replace Owner approval or project rules. |
| Session discovery and workers | `starfix_sessions`, `starfix_worker` | A worker keeps its own session and configuration; do not assume the captain setting applies to it. |
| Delivery and receipts | `starfix_dispatch`, `starfix_notify` | Unknown delivery is investigated from history, not blindly resent. A receipt is not an acceptance decision. |
| Task and decision flow | `starfix_task`, `starfix_panel` | Preserve the task contract and explicit human decisions. |
| Monitoring and control | `starfix_monitor`, `starfix_control`, `starfix_ack` | Pause, wake, and quota behavior remain separate from receipt delivery. |
| File memory and handoff | `starfix_memory`, `starfix_handoff` | Files remain the authority; do not overwrite unrelated user edits. |
| Trajectory and compilation | `starfix_trajectory`, `starfix_compile` | Business dependencies and project-specific inputs still require explicit configuration. |
| Audit and channel operations | `starfix_audit`, `starfix_channel` | External targets, repair commands, and production data require their own authorization. |
| Share gate | `starfix_scrub` | A gate result is evidence for its stated rules, not a substitute for review. |

Model or native tests can consume quota or require real inputs. The four native cases in the Node test suite run only when `STARFIX_TEST_CODEX` is set to an explicitly selected Codex executable. Leave it unset for simulated regression. Standalone smoke scripts are separate, deliberate invocations and are not covered by that suite-level opt-in.

## Test Inputs

Most public checks need no private fixture. The tests that intentionally exercise a structural negative case or a simulated registration require an explicit local file selected with `STARFIX_PRIVATE_TEST_INPUTS`.

- The file is machine-local and must not be committed or copied into a PR.
- Its required fields are `structureIp` and `changesetNo`; this document intentionally provides no values or JSON example.
- The Node loader and Python mock consume the same explicit input boundary.
- A missing or invalid input is a test configuration failure. It must not be silently skipped or converted into a pass.

Runtime UUIDs and relative times are generated by the tests where their value is not part of the assertion contract. No host-specific test data belongs in this README.

Independent review accepted the limited eight TAP cases with zero skips, six input rejection paths, six candidate protections, and task cleanup. This remains limited to that named input slice; it does not certify a full suite, real native behavior, or any public fixture value.

## Model-Test Input

Model-oriented test entries require an explicit, nonblank `STARFIX_TEST_MODEL_ID`. There is no fallback to a host default, account default, or fixed model identifier. Select the value from the actual runtime model list only when a separately authorized controlled test needs it; a real effort test also requires a model that supports the existing `high` effort expectation.

Setting this parameter does not authorize or start a real Codex connection, a model call, or the two-turn smoke scenario. `tests/public-deps.test.mjs` remains independent of this parameter and of private test inputs.

## Verification

After `npm ci` has installed the local dependency tree, run the two public dependency assertions from this directory. This validates the public dependency slice only; it does not register the plugin or `/starfix` with a host:

```sh
node --test tests/public-deps.test.mjs
```

`tests/codex-fix-gates.mjs` is a pure helper module, not a functional test. If syntax parsing is needed, `node --check tests/codex-fix-gates.mjs` only parses that module; it does not import it or run assertions, and is not an acceptance command. The public dependency test does not establish that all tests, real model paths, native persistence, or business integrations have passed. The private-input checks require the explicit boundary described above and are tracked separately from these public assertions.

The historical upstream archive at `d9be075f5a9b49285daa0ec7db0ef536641bb043` remains distinct from the candidate baseline `1e3f08600d51526186dbea79069d5884c5e5676a`. Do not substitute a current working tree for either archive when performing a baseline comparison.

The final local non-native regression recorded 113 passing Node cases, four explicitly skipped native cases, and three passing Python cases. See [local regression inputs and commands](VERIFICATION.md#local-regression) for reproduction and limits.

## Publication Boundary

The assembled public tree passed the original scrub gate's ten structural categories. No private HMAC word list was available, so its person/company-name coverage remains unverified. This reduced coverage is not a full privacy conclusion. Do not bypass a finding through string splitting, renamed fixtures, exclusions, or temporary exceptions.

The following remain outside the accepted scope:

- Long-lived native persistence and recovery after a normal empty-thread EOF.
- A proof that closing a visible worker window stops every descendant process.
- Runtime-plugin cutover in an already running host.
- Real business databases, deployments, and project-specific audit targets.
- Full public-tree privacy review and any real-model or native acceptance not explicitly enabled.

For phase counts, historical failures, and the exact acceptance limits, see [VERIFICATION.md](VERIFICATION.md#acceptance-boundaries). For the capability map and unverified work, see [FIDELITY-AUDIT.md](FIDELITY-AUDIT.md#capability-map).
