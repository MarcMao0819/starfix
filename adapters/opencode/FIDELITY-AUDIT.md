# StarFix Adapter Fidelity Audit

This document maps the adapter's intended behavior to the upstream contract and records what the available evidence does, and does not, establish. It is not a claim of complete Windows equivalence or a release approval.

## Evidence Scope

- Candidate baseline: `1e3f08600d51526186dbea79069d5884c5e5676a` for `pan264/starfix`.
- Historical comparison baseline: `d9be075f5a9b49285daa0ec7db0ef536641bb043`. Its archived material remains historical comparison evidence; no current-candidate test count is attributed to it here.
- The adapter is a compatibility layer. It must preserve upstream task rules, delivery uncertainty, approval boundaries, and project-specific configuration rather than introducing a replacement workflow.
- This public document records scoped results only. Detailed phase evidence and counts live in [VERIFICATION.md](VERIFICATION.md#verification-summary); original pre-publication text is retained outside the public tree.

## Capability Map

| Capability | Adapter responsibility | Fidelity boundary |
| --- | --- | --- |
| Fleet identity and workers | Discover, create, register, configure, interrupt, release, and reclaim individual workers. | Each worker retains its own session and configuration; a captain setting must not silently overwrite it. |
| Model and reasoning settings | Read available identifiers and supported settings from the actual runtime, then pass explicit configuration to the matching worker. | No fixed model identifier or universal reasoning level is assumed by the adapter or this document. |
| Delivery and confirmation | Preserve preflight, send, history confirmation, receipt, and uncertain-delivery states. | An unknown outcome is not automatically resent or changed to a success. |
| Control and human handoff | Preserve pause, resume, interrupt, release, reclaim, approval, and Owner decision boundaries. | A receipt, background connection, or successful process exit is not a substitute for explicit acceptance. |
| Monitoring and inboxes | Keep task observation, quotas, file waits, reports, and decision input distinct. | Monitoring state does not authorize delivery or mutate user decisions. |
| Memory and handoff | Use the established file-based memory and handoff flow. | Existing user files and unrelated histories are not overwritten. |
| Trajectory, compilation, and audit | Bind platform execution details while preserving upstream graph, guard, and audit decisions. | Real projects, databases, deployments, and audit targets require their own configuration and authorization. |
| Channel repair and sharing | Probe configured channels and expose the scrub gate without bypassing it. | A repair or scrub result has only the scope of its observed command and inputs. |

## Scoped Results

| Area | Evidence retained for this candidate | What it does not prove |
| --- | --- | --- |
| Service shutdown | The limited shutdown review accepted the named-result uniqueness checks, behavior witnesses, bounded close, ten candidates, six retained behaviors, and the normal CLI regression of 66 checks. The before-drift and hidden-close scenarios record JSON `BLOCKED`; a CLI exit code is a separate signal. | It does not prove every lifecycle, host exit, or descendant-process path, and exit `0` alone is not acceptance PASS. |
| Public dependencies | The local package declares `@opencode-ai/plugin` `1.18.30`; `private: true`, package lock, ignore policy, and the two `public-deps.test.mjs` assertions were independently accepted. | It does not add a global SDK, publish a package, or validate private fixtures. |
| Private test inputs | The candidate separates structural and registration fixtures behind an explicit local input boundary. Independent review accepted the limited eight TAP cases with zero skips, six input rejection paths, six candidate protections, and task cleanup. | This does not establish a full suite, real native behavior, or a public disclosure of fixture values. |
| Public-dependency candidate slice | The current candidate's affected adapter/recovery slice recorded 54 passing cases with one real native case excluded from that slice. It is not the `d9be075f5a9b49285daa0ec7db0ef536641bb043` archive. | It is implementation evidence, not independent acceptance of 54 cases or a full candidate suite. Independent acceptance covered only the two `public-deps.test.mjs` assertions. |

Older private loader/module requirements have been removed from the public dependency boundary. Public instructions must not reintroduce them as prerequisites or replace them with a copied private environment.

The final local regression on this candidate passed 113 Node cases and three Python cases. Four native Node cases were explicitly skipped because no Codex executable was selected. The ten service-shutdown cases are included in the 113, not an additional count. This supersedes no historical evidence and establishes neither real native acceptance nor complete upstream equivalence.

## Acceptance Boundaries

| Unverified or constrained area | Required interpretation |
| --- | --- |
| Empty-thread persistence and old identifiers | Normal empty-thread EOF observations did not establish rollout or resume. The withdrawn old-identifier requirement must not be described as restored. A new session driven by task memory is a separate workflow. |
| Visible worker window shutdown | A background connection and a UI disconnect do not prove that every descendant process has stopped. |
| Runtime cutover | The currently running plugin host has not been switched as part of this candidate documentation work. |
| Host registration | `npm ci` installs the local dependency tree only. Plugin registration and `/starfix` command or skill registration remain separate host prerequisites. |
| Real models and native work | These require explicit enablement, real inputs, and quota-aware authorization; simulated or protocol tests are not equivalents. |
| Business systems | Production databases, deployments, external channels, and project-specific audit profiles remain outside this adapter acceptance. |
| Public privacy review | The assembled public tree passed all ten original structural categories. The private HMAC word list was unavailable, so person/company-name coverage remains unverified; this is not full privacy acceptance. |

## Publication Boundary

Private test inputs contain only the field names `structureIp` and `changesetNo` in public documentation. Their values, local file location, credentials, runtime state, audit data, tokens, and private HMAC material are not publication inputs.

The share gate must be addressed through the original rule and an independently reviewed finding. Renaming a fixture, splitting a literal, moving it into an ignored directory, or adding an exception is not a valid release remedy.

## Related Documents

- [Usage and safety guidance](README.md#safe-use)
- [Verification summary and phase counts](VERIFICATION.md#verification-summary)
- [Acceptance boundaries](VERIFICATION.md#acceptance-boundaries)
