# Task Activator (task-activator)

**语言 / Language:** [中文](task-activator.md) · [English](task-activator.en.md)

The captain's single table of task truth. It moves "what was dispatched, where it is stuck, has the receipt arrived, what did the Owner answer" out of the captain's head and into a file; both the hourly report and the stall sentinel read it.

## I. Data model (`task-activator.json`)

```json
{
  "tasks": [{
    "id": "CODE", "title": "one line", "status": "pending|in progress|done",
    "owner": "Crew B|Crew G|queue→window", "worktree": "feat/xxx",
    "book": "${FLEET_HOME}/CS-<date>-<code>-task.md",
    "receipt": "${FLEET_HOME}/receipts/CS-<date>-<code>.md",
    "blocker": "what it is stuck on (waiting on whom / on which decision)", "note": "progress in one line",
    "evidence": "completion evidence (merge sha / registration number / released version)",
    "updated": "YYYY-MM-DD HH:MM"
  }],
  "dropped": [ ...same shape + reason ],
  "decisions": [{
    "qid": "Q01", "question": "the question, with A/B/C and a recommendation", "who": "Owner",
    "tasks": ["CODE"], "answer": "", "ts": ""
  }]
}
```

- `status` has only three states; "waiting on a decision" is not a status — it is a pending task with a non-empty `blocker`.
- An `owner` written as "queue→window" means queued but not dispatched; the stall sentinel does not count it as stalled.
- The receipt status is detected automatically by the script from the `终态：PASS/FAIL/BLOCKED` line in the receipt file's first section; a receipt that has arrived while the task has not moved → flagged ⚠ in the hourly report.

## II. Commands

```
task-activator.py add <ID> "<title>" [--book ...] [--receipt ...]
task-activator.py set <ID> <status> [--owner] [--worktree] [--note] [--blocker] [--evidence]
task-activator.py drop <ID> --reason "..."
task-activator.py list | board
task-activator.py ask add Q<NN> "<question>" --tasks A,B --who Owner
task-activator.py ask answer Q<NN> --answer "<answer>"      # normally called by the inbox script
task-activator.py ask list | board | detail Q<NN> | recommend
```

The environment variable `ACTIVATOR_JSON` can point at a copy for testing; without it, the real path is used.

## III. The decision loop

1. The captain runs `ask add`: the question must be a confirmation question (A/B/C + a recommendation), and it lists the tasks that an answer unblocks.
2. The panel (ask panel) shows the unanswered questions; the Owner taps an answer → one line is appended to `ask-inbox.jsonl`.
3. `ask-inbox-apply.sh` (`tail -F`) reads the new line → `ask answer` → the task's blocker is cleared → the captain is notified.
4. **The inbox is append-only**: rewriting it in place makes `tail -F` replay every historical answer and flush every task's blocker (a real incident). Scrubbing means stopping the monitor first, then editing, then remounting.
5. If an answer contains credential-type content, blank the value before it lands, keeping only "answered".

## IV. The hourly report and the sentinel

- Hourly report: in progress N · pending N (ready / waiting on a decision) · done N; each in-progress item with its owner and elapsed time; items waiting on a decision grouped by who they are waiting on; receipt arrived but not handled ⚠; a reminder for anything ready but not dispatched; one iron-law line at the end.
- The stall sentinel takes its list from the activator: in progress and the receipt silent >60 min raises an alert; tasks belonging to a real human (screen = a human) are annotated and do not count as stalled.
