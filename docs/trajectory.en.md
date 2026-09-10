# Trajectory Compiler · Logic and Principles

**语言 / Language:** [中文](trajectory.md) · [English](trajectory.en.md)

![Trajectory compiler](img/en/06-trajectory.svg)

## 1. What it solves

The fleet produces dozens of receipts, registrations, merges and deploys every day. The captain checking each one by hand is not realistic, and handing all of it to a model produces "always green". What the Trajectory Compiler does: **record every step a human (or a model) walks through while auditing one change as a trajectory, compile the stable steps into a graph the machine can replay, and leave only the unstable branches to the model.**

## 2. Three parts

| Part | Input | Output | Key rule |
|---|---|---|---|
| Inducer `inducer/` | Historical audit trajectories (each step `kind=sql/git/compare`, each step must emit `verdict=`) | The workflow graph `graphs/<flow>.vN.json`: nodes (seq, kind, branch) + the verdict rules `verdict_rules` (p1…) | Each verdict point records a "stability": 10 rounds observed, 10/10 consistent and no counterexample → `mechanical` (no model needed); otherwise `llm` (only the abnormal branch goes to the model, with its context attached) |
| Runner `runner/run_graph.py` | The graph + one new change (registration number / deploy / route) | Executes node by node; mechanical verdict points emit a verdict directly; abnormal branches are routed to the model | The order of verdict points is fixed (p1..p5) to avoid behavioural drift; one triage label per run |
| Poller `runner/audit-poller.sh` | Every minute, scans new registrations, version truth, routes | Alerts written to the receipt directory + a heartbeat (pid/started) | Alerts carry a disposition section; drift alerts self-clear by the ancestor rule (the head is already in LIVE); unhandled alerts are re-evaluated for routing on every tick (gate B) |

## 3. The seven verdict points (the changeset-audit graph)

| Point | What it checks | When it goes red |
|---|---|---|
| p1 | The commit hash in the registration is well-formed and exists in the repository | A hand-written/truncated hash, an unpushed commit |
| p2 | The three-way file counts agree: registration table / file detail table / `git diff` | A file missing from the registration, an over-registration |
| p3 | The file-path list on the DB side = the git side (line-by-line diff) | A path renamed without syncing, a smuggled-in file |
| p4 | The title has no GBK/UTF-8 mis-decoding garbage | Garbled terminal paste written into the database |
| p5 | The line-ending double rule: a new file must carry no CR; for an existing CRLF file, look at the delta, not the absolute value | An editor changed the line endings, a bulk script dropped CRLF |
| p6 | version truth (LIVE) ≠ integration head → deploy drift | Merged but not deployed, deployed but version truth not written |
| p7 | Interface route liveness (404 consumption) | The menu is there, the route is gone |

## 4. The master rule: "verify only the expected direction" is the common factor behind every pitfall

Every false alarm, missed alarm and false green over two days fits the same mould: **asserting only that "what should have happened did happen", never asserting that "what should not have happened did not"** (or the other way round). So every criterion is paired with a reverse assertion, every fixture batch includes a "pre-existing disease, innocent this time" sample, every green is asked "did it actually check, or was the input empty so it checked nothing", and every red is asked "which rule caught it, and have the other branches ever matched at all".

## 5. The difference from CI

CI checks whether the code is right; the Trajectory Compiler checks **whether the process closed**: the receipt arrived but was not registered, registered but not merged, merged but not deployed, deployed but the version truth not updated, an alert raised but nobody handled it. It audits the captain too: merging and deploying are done by the captain's own hand, which is where self-approval is easiest.

## 6. Token economy

- **Measured numbers (this machine, the change-audit flow)**: before compilation, about 12k tokens and minutes per change (the model queries SQL/git step by step and then judges); compiled into a graph, **0 model calls and 0.6 s for an ordinary change**, 48 real runs with zero silent errors; the pre-merge gate graph **1.2 s / 0 calls**. The stop-loss line set at project start was "model calls/tokens down ≥30%"; measured, ordinary changes are down 100%, and only abnormal branches go back to the model. Converted: about 1.2M tokens saved per 100 ordinary changes (at 12k each). **Do not misread this**: what is valuable is "a deterministic graph + calling the model only at the forks", not compiling the graph into a binary — the interpretation overhead of the graph is approximately zero.


- Mechanical verdict points (`type: mechanical`) replay without calling the model: p1/p2/p4/p5/p6/p7 cost zero tokens per change; the poller running every minute costs nothing either.
- A `type: llm` verdict point enters the model only on the abnormal branch, and carries only that branch's context (for example the differing lines between the two file sets at p3), not the whole change's diff. In the observed sample, 2 of p3's 10 rounds entered the model.
- By contrast: having the model re-audit every change means reading the full diff + registration + receipt, tokens grow linearly with the file count, and the "always green" risk is higher.
- Verdict-point stability is computed by the inducer, not decided by a human: a verdict point is demoted to mechanical only after the mechanical rule agrees with the actual verdict for several rounds in a row with no counterexample; a counterexample promotes it back to `llm`. So the token-saving boundary expands and contracts with the data automatically.

## 7. What to prepare when plugging in a new flow

1. Make every step of that flow emit a `verdict=` field (the trajectory contract).
2. Run 10+ rounds so the inducer can give verdict-point stabilities.
3. Turn the stable verdict points mechanical and mark the rest `llm`; write one reverse fixture per verdict point.
4. Mount it into the poller, land alerts in the receipt directory, and define the self-clear rule.
