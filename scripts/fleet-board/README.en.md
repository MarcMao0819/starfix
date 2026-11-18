# Fleet board

A view-only page on this machine. It shows three things: who is doing what, the exchanges between the captain and the crew members, and the questions still waiting on the Owner. The page has no control that writes a file and none that types into a terminal.

## How to start

If `FLEET_HOME` is unset and `--demo` is not passed, the program exits with one sentence.

```bash
export FLEET_HOME=<fleet directory>
python3 scripts/fleet-board/server.py
```

Sample data, without a real fleet directory:

```bash
python3 scripts/fleet-board/server.py --demo
```

The sample lives in `scripts/fleet-board/demo/`. The task table keeps the real filename, so that sample is tracked on its own.

The terminal prints the loopback address. The port defaults to `8765` and can be changed with `FLEET_BOARD_PORT`. It binds the loopback address only.

The page fetches again every few seconds. Light and dark follow the system. The top-right control switches the language. In the sample, titles, questions, window names, and the waiting text switch with it.

## Files it reads

- Tasks and open questions: `$FLEET_HOME/task-activator.json`, or `ACTIVATOR_JSON`.
- Task books and receipts: the `book` and `receipt` fields. A real directory uses the file modification time. Demo mode reads a written line, `时间：<日期> HH:MM` or `时间：<前日> HH:MM`. A receipt conclusion is the first non-empty line only.
- Windows: `$FLEET_HOME/fleet-snapshot.txt`, or `FLEET_SNAPSHOT`. If that file is absent, the section is hidden.
- Context watermark: the steward `status.json`. `STEWARD_DATA_DIR` wins, then `data_dir` in `$FLEET_HOME/steward/targets.json`, otherwise `$FLEET_HOME/steward/data/status.json`. The field used is `used_percentage`. If the file is absent, the context watermark is hidden.

A task book or receipt that resolves outside the fleet directory is not read. That row says "no data".

If the activator is missing or unreadable, the three sections say "no data". The windows section still shows when its file is present.

## Where the exchanges come from

Nothing new is written. The rows are derived from fields that already exist:

- Dispatch a task book: demo mode uses the time written in the task book. A real directory uses the modification time. If the file is absent, the time is `created`.
- Hand in a receipt: only when the receipt file exists. Demo mode uses the time written in the receipt. A real directory uses the modification time. The conclusion is pass, fail, or blocked, and only on the first non-empty line.
- Acceptance: `status` is already done. The time is `updated`, or the last `history` row whose state is done. The wording is `evidence`, then `note`, then the `history` note.
- Questions waiting on the Owner: `asked_at`, `answer`, `answered_at`, and `tasks` on `decisions`. Rows with an empty `answer` come first.

Status on a person card: done wins; otherwise a receipt whose first line is one of the three conclusions is "receipt awaiting acceptance"; otherwise a non-empty `blocker` is "waiting on a decision"; otherwise the stored state, pending or in progress.

Time in that state: a receipt awaiting acceptance counts from the receipt time (the written time in demo mode, otherwise the modification time); waiting on a decision counts from `updated`; anything else counts from the last `history` row that entered that state, then `updated`, then `created`. `<日期>` and `<前日>` mean today and yesterday on this machine's clock.

The captain card shows only two counts, receipts awaiting acceptance and questions waiting on the Owner, each with its task IDs. The detail stays on the crew cards and in the two sections below. Crew cards group by `owner`: in progress, then receipt awaiting acceptance, then waiting on a decision, then pending, then done.

## What this version does not do

- It does not change files and it does not type into a terminal.
- It does not read the inbox log or the dispatch log. An answer counts only when `answer` is already stored on the activator.
- Dropped tasks (`dropped`) are not shown.
- A receipt wording other than pass, fail, or blocked does not count as a receipt awaiting acceptance.
- The context watermark is not read from the steward's raw session snapshots. Those do not identify a person, so only `status.json` is used.
- The windows section is not merged into the person cards. The snapshot has no stable role field.
- There is no separate field for the task the captain is doing right now.

## Tests

```bash
python3 -m unittest scripts/fleet-board/test_board.py
```
