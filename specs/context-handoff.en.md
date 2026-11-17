# Context Watermark and Handoff (Context Handoff)

**语言 / Language:** [中文](context-handoff.md) · [English](context-handoff.en.md)

The context usage of the captain's own window is a key indicator: by 85% there must already be a handoff document on disk that lets a successor carry on, and it must be read back automatically the moment compaction finishes. Three mechanisms, none of which rely on "remembering".

## 1. The indicator source

Every time Claude Code renders the status line it passes JSON to the status-line script, including `context_window.used_percentage / remaining_percentage / context_window_size` and `session_id`. The status-line script appends a section (best-effort, silent on error) that writes them to `${CTX_SNAPSHOT_DIR:-~/.claude/ctx-snapshot}/<session_id>.json`. That is the watermark table.

## 2. The 85% automatic trigger (hook side, not model memory)

`scripts/hooks/ctx-handoff-guard.py` is wired to PostToolUse (matcher `*`), UserPromptSubmit and Stop (a local wrapper exports `FLEET_HOME` and execs it). It only acts for sessions listed in `${CTX_GUARD_LIST:-~/.claude/hooks/ctx-guard-sessions.txt}` (one iTerm session UUID or session_id per line):
1. First time in a "compaction cycle" that usage reaches 85% (`CTX_HANDOFF_THRESHOLD`): run `handoff-snapshot.sh` without the model (activator, pending decisions, ledger tail, monitor list into the snapshot part of `${FLEET_HOME}/handoff/HANDOFF-<session_id>.md`, refresh `HANDOFF-latest.md`), then inject an instruction into the model context: update the hand-written top section now.
2. At 92% (`CTX_HANDOFF_THRESHOLD2`) snapshot and remind again.
3. Stop: if triggered but the hand-written section has not changed since the trigger, block once (never when `stop_hook_active`).
4. When usage falls below threshold minus 25 (`CTX_HANDOFF_RESET_GAP`, usually after compaction) a new cycle starts.
5. The guard proves it is alive by refreshing `<session_id>.guard.seen` at most every 60 seconds.

`scripts/hooks/ctx-watch.sh <session_id> 85 60` remains available as a Monitor side path, but is no longer the main path.

## 3. Pre-compaction safety net and post-compaction read-back (hook side)

| hook | matcher | What it does | Criterion |
|---|---|---|---|
| PreCompact | all (manual/auto) | Mechanically writes the snapshot section of `HANDOFF-<sid>.md` and refreshes the `HANDOFF-latest.md` symlink; does not block compaction | Even if the captain has no time to hand-write, there is a snapshot |
| SessionStart | `compact|resume` | If a HANDOFF exists for this session, print the whole hand-written section above the separator + the first 80 snapshot lines (`FLEET_HANDOFF_INJECT_LINES`) to stdout → injected into the context, asking the model to Read the full file first | The first thing seen after compaction is the handoff; it does not depend on the model remembering to read it |
| PostCompact | all | Appends the compaction summary to `handoff/compact-log.md` | Afterwards you can audit "what compaction lost" |

Files are split by `session_id`: when another session compacts it will not read the captain's handoff by mistake. Hooks do not inherit the shell rc, so a local wrapper script must explicitly `export FLEET_HOME` and so on before exec'ing the in-repo script; sample in `examples/settings.hooks.sample.json`.

## 4. Discriminability

- Two-state self-proof: `scripts/hooks/test-ctx-handoff-guard.sh` runs 17 cases in a temp directory (50% silent, 86% fires and writes the snapshot, no repeat within a cycle, Stop blocks once and only once, 92% second level, reset after usage drops then fires again, allowlist match by iTerm UUID, SessionStart injects the whole hand-written section and truncates the snapshot, no handoff file means no output); the same suite with the threshold set to 99 must fail.
- Run the SessionStart script once with fake stdin (containing a real session_id); stdout must contain `<handoff` and the hand-written section; with a session_id that does not exist it must produce zero output.
- The handoff document has exactly one criterion: the successor can correctly dispatch the first task within 10 minutes without asking anyone.

## 5. Handoff at a token line (a steward outside the session)

The guard in section 2 lives inside the session. With a million-token window 85% comes far too late, so a resident program outside the session also watches the snapshot table, with one state machine per managed session:

1. **Over the line**: the token count or the percentage in the snapshot passes its line, and the session has been active in the last few minutes (an idle session is not interrupted; it is handled the next time it wakes).
2. **Ask for the card**: type one message into its input box asking it to finalise the hand-written part of the handoff card and then reply with an agreed word on a line of its own.
3. **Check two things**: the hand-written part really changed after the notice (compare digests, and it is not the empty template), and the agreed word appears on screen after the notice. Proceed only when both hold.
4. **Clear the session**: send the clear command only when the input box is empty and the turn has ended; then wait for the session id to change.
5. **Tell the new session to read the card**: give it its role and the old card's path, ask it to reply with an agreed word once it has read it, then carry on.
6. **Stop whenever a step is uncertain**: record a "needs a human" entry; do not resend and do not guess.

Rules for setting the line:

- The line can be set per session; the default sits below the vendor's long-context surcharge threshold.
- When a session is first brought under management, run it once with a generous line, measure where it lands after the handoff, and only then decide whether a lower line fits.
- The steward writes its own heartbeat, and the daily routine check reads it. Before changing its configuration, confirm no session is in the middle of a handoff.

Checklist for the hand-written part of the card (attached to the notice; no length limit): the current state; every piece of work in flight (who, which step, the criterion, the next step, the deadline); what the owner decided in this session (with the original words); what was tried and did not work; what was promised to others and what is awaited from them; what this session only just worked out; paths to evidence and receipts; how to re-attach timers and monitors; and whatever is still valid from the previous card, carried over.

The reference implementation lives under `scripts/steward/` (`steward2.py`, `run.sh`, `targets.sample.json`).
