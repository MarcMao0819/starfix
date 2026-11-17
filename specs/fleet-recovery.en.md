# Fleet recovery

**语言 / Language：** [中文](fleet-recovery.md) · [English](fleet-recovery.en.md)

Lid wake, full machine restart, and iTerm gone entirely are three different situations. The single entry point is `scripts/recovery/recover-fleet.sh` (after `export FLEET_HOME`).

## Three situations

| Situation | What you see | What to run |
|---|---|---|
| Lid wake | iTerm tabs still there, Claude still running | `recover-fleet.sh status`; usually re-attach monitors only |
| Restart, tabs remain | Tab exists but shell idle or Claude gone | `recover-fleet.sh up` → `resume-inplace.py` for “shell prompt”; restart steward if needed |
| All tabs gone | iTerm recreated or windows closed | `recover-fleet.sh up` → `restore-fleet.py` for missing tabs; **adopt before opening** (`adopt-open.py`) |

## Two hard rules

1. **Adopt before opening a new tab**: if the registered window id is gone but the same session is already open elsewhere, update the registry only (`adopt-open.py`).
2. **Stop when state is unreadable**: if the script cannot tell Claude vs shell vs other, it leaves that tab alone until a human looks.

## Related files

- Registry: `$FLEET_HOME/recovery/fleet-table.json` (sample `scripts/recovery/fleet-table.sample.json`)
- Roles and resume flags: `$FLEET_HOME/recovery/fleet-roster.json` (sample `fleet-roster.sample.json`)
- Recovery log: `$FLEET_HOME/recovery/recover-last.txt`
- Context steward: `scripts/steward/` (config at `$FLEET_HOME/steward/targets.json`)

Dry run: `DRY=1 recover-fleet.sh up` prints actions without executing them.