# Monitor Specification (Monitors)

**语言 / Language:** [中文](monitors.md) · [English](monitors.en.md)

The captain's senses. Three principles: **report only the dangerous side, report only state transitions, never poll**. Before a monitor is mounted it must be able to answer four questions: has it ever gone red? under what condition would it go red? who closes it out? how is it remounted after a session restart?

## 1. The must-be-mounted list (checked during the captain's first actions on taking office)

| # | Type | Trigger | Reports | Does not report | End condition |
|---|---|---|---|---|---|
| ① | Receipt monitor | New/updated file in the receipt directory | The file name | The content (the captain reads it) | Always mounted |
| ② | Inbox apply | An answer from the decision panel appended to `ask-inbox.jsonl` | `Q<NN>=<answer>` + which tasks it unblocked | Historical answers (append-only, by cursor) | Always mounted |
| ③ | Crew disconnect watchdog | Terminal session state transition (Working→Idle/gone) | The transition itself | State on every tick | Always mounted |
| ④ | Stall sentinel | An in-flight task silent > 60 min | Task ID + how long it has been silent | Completed tasks / tasks waiting in the queue (owner recorded as "queue→window" does not go on the list) | Always mounted |
| ⑤ | IM human-reply polling | A new human message in a stakeholder conversation (cursor continuation, ONLY/EXCLUDE split) | Speaker + body | The bot's own messages | Always mounted |
| ⑥ | Remote per-person monitor | Polling one stakeholder through a jump host | As above | — | Stops when that engagement ends |
| ⑦ | Channel guard | Tunnel/proxy probe failing **twice in a row** | The rebuild action + result | A single jitter | Always mounted |
| ⑧ | Machine-check poller | version truth ≠ integration head (drift), a gate red, a route 404 | Alert written to disk + self-clear by the ancestor rule | Expected drift inside a deploy window (the main window just files a note) | Always mounted |
| ⑨ | Business health | The key business heartbeat (pull/schedule/write-back) | **Dangerous side only**: FAILED ≥3 in a row, stalled >10 min, a status dangerously rewritten | Absence type (it should have happened and did not) is recorded as information only | Always mounted |
| ⑩ | Hourly report | Every hour | In progress / pending (ready vs waiting on a decision) / done, receipt arrived but not handled ⚠, ready but not dispatched | — | Always mounted |
| ⑪ | Timed reminder | A specified moment (the low-traffic deploy window, the morning question batch) | The verbatim checklist of what is due | — | One-shot |
| ⑫ | Usage gate | Model usage ≥95% or a non-GO state | A stop-dispatching notice | Normal usage | Always mounted |
| ⑬ | Waiter type | Someone's first login / a file appearing | Once | Afterwards | One-shot |
| ⑭ | Away-mode relay | The Owner's IM direct-message conversation while away | Human messages | — | Stops at "I'm back" |

## 2. Discriminability rules

- Do a "two-state self-proof" before mounting: make it go red once (create the condition), then make it go green once. A monitor with a constant output is blindness.
- The observable must change with the input: when the `tail -3` sampling window cannot cover the newline, it matches the old line forever (a real incident).
- "No news" is not "nothing wrong": absence-type signals are marked separately and never mixed in with the dangerous side.
- Machine-check alerts must be able to self-clear (the ancestor rule: clear once the alert's head is contained in LIVE), otherwise alerts pile up and stop being read.

## 3. Operating rules

- Monitoring, waiting and triggering the next action always use Monitor-type tools; polling with Bash `sleep` is banned.
- Too high an output rate gets suppressed (events merged); a noisy monitor gets its filter narrowed before it is mounted.
- Name every monitor with its number + version (e.g. `⑧machine-check poller(v6)`), and write the mount command into `monitors-latest.json` so it can be remounted after a session restart.
- When a monitor finds a problem: if it can be dispatched, dispatch a crew member to fix it, do not wait for the Owner; only the three kinds of escalation items go on the panel.
- Long or multi-agent runs pass the usage gate every ~5 min; when it hits the ceiling, stop dispatching and wait for the reset.

## 4. Additional monitors for away mode (OA)

- Added while away: polling the Owner's direct-message conversation (⑭) plus panel-answer apply (②), two lines; answers from both lines go into the same inbox, first come first landed.
- The dangerous side of business health (⑨) is pushed to IM as well; information-level entries go only into the terminal ledger.
