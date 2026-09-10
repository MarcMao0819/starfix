# Why a Fleet Rather Than Sub-agents; Why the Trajectory Compiler

**语言 / Language:** [中文](why.md) · [English](why.en.md)

## 1. The StarFix fleet vs. an agent spawning its own sub-agents

"An agent spawns its own sub-agent" is the most common multi-agent shape today: a parent agent sends a short-lived sub-agent to do one thing, and the result comes back into the parent's context. It suits a one-off lookup or a one-off small task. StarFix solves a different class of problem: **a project that has to run for weeks, with dozens of parallel lines, swapping people and models, and a human able to take over at any moment**. The differences are in eight places:

| Dimension | Sub-agent (spawned by the parent) | StarFix fleet | Why it matters |
|---|---|---|---|
| Lifetime | Short-lived; ends with the parent's turn | Crew are resident sessions that run for hours or across days and compact their own context | Real development tasks often take 2–4 hours; a sub-agent dies before it finishes |
| Context | Inherits or shares the parent's context; the result only returns to the parent | Each crew member has independent context; facts are externalised into **receipt files + the activator** | Once the parent's context compacts, the sub-agent's process is gone; files are not |
| Model and harness | Only the parent's model/quota/tools | Mixed: Codex builds, Claude reviews, headless workers run batches; routed by profile | Cross-model review is what independent review means; same-model self-review is grading yourself; quota can be spread |
| Isolation | Shares the parent's cwd/permissions/credentials | Per-task worktree, throwaway database, separate process, read-only originals | Sub-agents writing across into the parent's directory is a real source of incidents |
| Visibility and takeover | Humans cannot see what the sub-agent is doing | A crew member is a terminal window; a human can watch it, interrupt it, take it over at any time | The Owner does not have to trust a "black box"; when things go wrong a human can step in |
| Facts and audit | The parent paraphrases the sub-agent's conclusion | Receipts land on disk in three terminal states, registrations go into the database, gates are machine-checked; "a receipt is not a fact; the artifact is" | Paraphrase is the breeding ground for every false green |
| Identity and growth | Use once and discard; no profile | Crew have long-lived codes, profiles (strengths / failure modes / throughput), four review numbers | Dispatch precision comes from profiles, not from impressions |
| The commander itself | The parent both does and manages, and tends to "just do it quickly" | The captain only judges: dispatch / review / acceptance / merge / deploy; doing the work itself is a violation | Separating judgement from execution is what keeps judgement from being held hostage by the sunk cost of execution |

In one line: a sub-agent is a **function call**, a fleet is an **organisation**. A function call returns a value; what an organisation leaves behind is receipts, registrations, profiles and case files, which survive replacing anyone — including the captain.

The cost has to be stated too: a fleet needs infrastructure — terminal automation, monitors, the activator; the startup cost is an order of magnitude higher than spawning a sub-agent. If the project is under a day, single-threaded and does not swap models, a sub-agent is enough.

## 2. The advantages of the Trajectory Compiler

The Trajectory Compiler compiles the **process trajectory** "task book → build → receipt → registration → merge → deploy" into a machine-checkable graph, and a poller runs a set of checks over it every minute (p1–p7: receipt format and the three terminal states, the line-ending double rule, the contract baseline, registration and the first-parent chain, deploy drift, secret scan, route liveness), with alerts landing on disk and self-clearing by the ancestor rule; the inducer then grows new checks out of past trajectories.

The difference from ordinary CI: CI checks whether the **code** is right; the Trajectory Compiler checks whether the **process** closed — the receipt arrived but was not registered, registered but not merged, merged but not deployed, deployed but the version truth not updated, an alert raised but nobody handled it. None of these are code errors, yet they are the most common failures inside a fleet.

Five advantages, each corresponding to a class of real incident:

1. **Turning "remembering" into "it goes red when you run it"**. A pitfall written into the rules will certainly recur (two constructors missing the annotation, mixed line endings, the keyword not in the message body); written as a check, the recurrence is caught by the machine within a minute, with nobody having to remember.
2. **The captain also produces false greens, and the machine audits the captain**. The drift alert points out "the integration head has moved forward but production has not followed"; the registration gate points out "the branch's first-parent chain landed on the old head" — exactly the places where the captain, doing the merge and the deploy by hand, is prone to waving itself through.
3. **Alerts have a lifecycle**. Every alert carries a disposition section and a self-clear rule (cleared once the head is contained in LIVE), so they do not pile up into noise nobody reads; the ordering of "alert first, then merge" can also be verified.
4. **Machine-checking receipts takes the reading load off the captain**. A missing terminal state, an empty evidence section, a keyword that does not match, a file path that does not exist — the machine sieves first, and the captain reads only what got through the sieve.
5. **Self-growing**. The inducer derives new checks from "how did it slip through this time" (for example, "a zero-change assertion must first prove the object under test appeared at all"); the check set grows with the case book, and archived evidence satisfies "a rerun proves now, the archive proves then".

6. **It saves tokens, and saves more the longer it runs**. A stable verdict point (10 rounds observed, 10/10 consistent, no counterexample) compiled into a mechanical step costs **zero model tokens**: the registration hash, the three-way file counts, list agreement, line endings, drift — the things that have to be checked on every single change — get their verdict straight from SQL/git/compare; the model only spends tokens on the abnormal branches the inducer marked `llm`, and it receives only that branch's context rather than the whole change's diff. In the observed sample, only 2 of p3's 10 rounds entered the model branch; the other 8 cost zero tokens. Compare with "have the model re-audit every change": a model audit has to read the full diff and the registration, tokens grow linearly with the file count; the Trajectory Compiler freezes that part into a script, and as verdict-point stability rises the model branch keeps narrowing further.

**Measured numbers (this machine, the change-audit flow)**: before compilation, about 12k tokens and minutes per change (the model queries SQL/git step by step and then judges); compiled into a graph, **0 model calls and 0.6 s for an ordinary change**, 48 real runs with zero silent errors; the pre-merge gate graph **1.2 s / 0 calls**. The stop-loss line set at project start was "model calls/tokens down ≥30%"; measured, ordinary changes are down 100%, and only abnormal branches go back to the model. Converted: about 1.2M tokens saved per 100 ordinary changes (at 12k each). **Do not misread this**: what is valuable is "a deterministic graph + calling the model only at the forks", not compiling the graph into a binary — the interpretation overhead of the graph is approximately zero.

The cost: you need a trajectory contract (machine-readable anchors for the receipt / registration / merge / deploy respectively), and the first time you plug it in those anchors have to be filled in; the checks themselves must also pass a discriminability test (has it ever gone red), or the Trajectory Compiler becomes another dashboard that is always green.
