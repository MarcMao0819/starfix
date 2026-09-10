# Context Watermark and Handoff (Context Handoff)

**语言 / Language:** [中文](context-handoff.md) · [English](context-handoff.en.md)

The context usage of the captain's own window is a key indicator: by 95% there must already be a handoff document on disk that lets a successor carry on, and it must be read back automatically the moment compaction finishes. Three mechanisms, none of which rely on "remembering".

## 1. The indicator source

Every time Claude Code renders the status line it passes JSON to the status-line script, including `context_window.used_percentage / remaining_percentage / context_window_size` and `session_id`. The status-line script appends a section (best-effort, silent on error) that writes them to `${CTX_SNAPSHOT_DIR:-~/.claude/ctx-snapshot}/<session_id>.json`. That is the watermark table.

## 2. The 95% trigger (model side)

`scripts/hooks/ctx-watch.sh <session_id> 95 60` stays mounted as a Monitor; at ≥95% it prints one `CTX95 …` event into the captain's window. On receiving it the captain immediately:
1. Fills in the hand-written section at the top of `${FLEET_HOME}/handoff/HANDOFF-<session_id>.md`: "current judgement / in flight & criteria / pending decisions / the lesson that just happened / next three steps / risks & backups / coordinates" (template `templates/06-交接HANDOFF.en.md`).
2. Runs `scripts/hooks/handoff-snapshot.sh` so the mechanical section (activator list, pending questions, ledger tail, monitor list) overwrites everything below the separator line.
3. Does the three closeout items as usual; then lets compaction happen (or triggers `/compact`).

## 3. Pre-compaction safety net and post-compaction read-back (hook side)

| hook | matcher | What it does | Criterion |
|---|---|---|---|
| PreCompact | all (manual/auto) | Mechanically writes the snapshot section of `HANDOFF-<sid>.md` and refreshes the `HANDOFF-latest.md` symlink; does not block compaction | Even if the captain has no time to hand-write, there is a snapshot |
| SessionStart | `compact|resume` | If a HANDOFF exists for this session, print the hand-written section + the first 160 lines of the snapshot to stdout → injected into the context | The first thing seen after compaction is the handoff; it does not depend on the model remembering to read it |
| PostCompact | all | Appends the compaction summary to `handoff/compact-log.md` | Afterwards you can audit "what compaction lost" |

Files are split by `session_id`: when another session compacts it will not read the captain's handoff by mistake. Hooks do not inherit the shell rc, so a local wrapper script must explicitly `export FLEET_HOME` and so on before exec'ing the in-repo script; sample in `examples/settings.hooks.sample.json`.

## 4. Discriminability

- Two-state self-proof: change `used_percentage` in the snapshot file to 96 → the event must fire; change it back to 50 → it must not.
- Run the SessionStart script once with fake stdin (containing a real session_id); stdout must contain `<handoff` and the hand-written section; with a session_id that does not exist it must produce zero output.
- The handoff document has exactly one criterion: the successor can correctly dispatch the first task within 10 minutes without asking anyone.
