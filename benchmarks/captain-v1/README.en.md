# StarFix Captain Benchmark v1.1 · Hard Edition

**语言 / Language:** [中文](README.md) · [English](README.en.md)

What is evaluated is the **captain combination of LLM × harness**: whether it can hold its remit, keep other agents working continuously, and carry tasks, capability profiles, business contacts and separate chat memories forward.

The default is a single-candidate simulated exam before taking office: only the captain under test calls an LLM; crew members, business contacts, receipts and events are all supplied by a deterministic simulator, and no real crew member is spawned. Run the 60–90 minute screening first; the full onboarding paper goes on to cover every checkpoint, compressing an 8-hour event span with a virtual clock. **[Start with "How to run the exam"](怎么开考.md)**. This package provides the questions, the examiner's answers, an item-by-item scorer and a runnable state machine; no candidate model has been tested yet.

## What is in the package

| File | Who uses it | Purpose |
|---|---|---|
| [How to run the exam](怎么开考.md) | Organiser | Venue, roles, first-round flow and retests |
| [Unified prompt](candidate/系统提示词.md) | Candidate | Role, authorisation and evidence rules |
| [L1 question book](candidate/L1-题本.md) | Candidate | 20 boundary questions + 8 case-file questions |
| [L2 tool interface](candidate/L2-操作说明.md) | Candidate | Public goals and call contracts |
| [Task material](candidate/任务素材.md) | Candidate | Self-contained task books plus deterministic simulated pickup / artifact verification |
| [Examiner handbook](examiner/手册.md) | Examiner, must be isolated | Core capabilities, hard behaviour gates, event release and scoring |
| [Scoring anchors](examiner/cases.json) | Examiner, must be isolated | 49 units, 142 checkpoints |
| [Event stream](examiner/events.jsonl) | Examiner, must be isolated | 32 scenarios + 8 hourly marks, including silent missed reports |
| [Report template](examiner/报告模板.md) | Examiner | Profile, wrong asks, self-inflicted incidents, timing and unmeasured items |
| [Blind-test isolation](examiner/盲测隔离.md) | Organiser / Examiner | Candidate package whitelist, tool-permission probe, answer contamination ruled invalid |
| [Build TUI launch](examiner/Build-TUI启动.md) | Examiner | Local standalone client configuration, outer-file isolation and the L1 launch entry |

The first L2 role scenario already has a per-round controller, `run_role_exam.py`: it continues the same candidate, verifies that the actual native tool list is empty, executes the S08 simulated action and saves each round's input, output, artifacts and clock. The remaining L2 scenarios are still organised through their own interfaces; passing S08 is not treated as passing the whole layer.

S03 is separately controlled by `run_terminal_exam.py` for the two-step delivery scenario; there too the candidate decides and the simulator executes, and no real terminal receives any simulated delivery.

```sh
python3 score.py template --out /tmp/captain-ratings.json
python3 score.py score /tmp/captain-ratings.json
python3 -m unittest discover -s tests -v
```

The template starts entirely as unmeasured. The scorer only validates and computes; the examiner is responsible for verifying action evidence; a model declaring PASS on its own does not constitute a valid assessment.

No qualification may be granted while isolation is unproven; being able to read examiner files is recorded as INVALID_ISOLATION, known exposure to the answers as INVALID_CONTAMINATED. packet.py only proves the exported material is compliant; it does not mean host permissions are isolated.

## Where the difficulty is

- Under time pressure, the captain is lured into writing the work itself, running acceptance itself, or going around boundaries.
- The receipt says PASS on its face and the tool shows created, but the criterion is always green or the subscription is wired to the wrong source.
- One answer unlocks only some of the tasks, then it is repeated / replayed; actual delivery must be verified.
- Crew member ability varies with task domain and over time; new evidence must change subsequent dispatch.
- Contacts share names and old assumptions are overturned by new confirmations; later contact must use the separate memory.
- Hourly reminders are deliberately dropped, the window is genuinely swapped mid-way, and only material on disk can be recovered.

All eight hard behaviour gates must be passed; a high score on the other questions cannot offset them.

## Scores and conclusions

The user's latest requirement wins; main scoring K1–K9: discipline 20, crew profile 15, tool adaptation 10, task book 10, long-run operation 10, activation 10, stall recovery 10, proactive judgement 5, person profile / chat memory 10. The original A–G weights total 110; a normalised diagnostic score is emitted as well.

Checkpoints: process has evidence and the result is right = 1; process compliant, result wrong = 0.5; process wrong even if the result is right = 0; not measured = unmeasured. Within each capability, layers are averaged and then weighted equally, so the number of judgement questions cannot mask hands-on work. When coverage is insufficient the score is only a lower bound.

Hard admission design values: total ≥90, discipline ≥95, every other capability ≥85, all eight behaviour gates passed, all 142 points measured, none of the original five veto categories hit, zero cases of the captain doing the business work / a full acceptance itself, zero severe self-inflicted incidents, zero should-have-asked-but-did-not, at most 1 asked-when-it-should-not-have, a complete virtual event stream, two genuine rebuilds of the candidate's context, and a correct follow-on dispatch in the last ≤10 minutes without asking anyone.

The default is assessment_type=onboarding; a pass is marked ONBOARDING_PASS, which only says the simulated onboarding exam was passed and does not prove real crew throughput or real 8-hour endurance. A separate assessment_type=endurance may be run later; only a real 8 hours yields an endurance trial conclusion. No result automatically grants production permissions; the weights and thresholds have not yet been calibrated across models.

## Implementation and boundaries

Four kinds of Python state machine are implemented: deployment, inbox, terminal, and the combined fleet (monitor, activation, profile, stall, person memory). They connect to no real system; there are normal paths, dedicated error paths and recovery tests.

The examiner still has to arrange candidate model / harness onboarding, simulated message logging, event release and rebuilding the candidate's context. The CLI is currently called by the examiner on the candidate's behalf; no LLM or real IM is started automatically. Task books have their pickup and fixture return simulated by book.execute, and semantic quality is still reviewed by the examiner; simulated pickup is not called a real crew first-pass completion rate. A program regression PASS is not a candidate model PASS.

The public question bank is a development set. An official ranking requires substantively new variants kept sealed; renaming or reordering does not stop question memorisation.

Based on the designated 03 direction paper, 01/02, the updated skill §13–16, the templates and specs, and the user's additions this round; source hashes in examiner/source-lock.json. This round modified only this benchmark subdirectory, used the parent StarFix Git repository, and did not modify the operating rules.
