# quickstart · Run the whole loop in 10 minutes

**语言 / Language:** [中文](README.md) · [English](README.en.md)

The 牵星 StarFix playbook is written for "one AI captain commanding a fleet of terminal AI crew
members", and the full version depends on external parts like iTerm2, the ask panel and IM.
This folder is the **minimum runnable version**: with nothing but `tmux` + `python3` you can run
the complete loop by hand in ten minutes on macOS or Linux, and see exactly where every part
sits and what its interface looks like.

One command:

```bash
bash quickstart/demo.sh
```

When it finishes you will have watched this chain go a full circle (about 15 seconds):

```
dispatch → two-step delivery → crew builds → receipt lands → monitor fires → captain accepts
                                                                  ↓
                    question raised → decision panel → Owner answers → inbox → task unblocked
```

Everything lands in one temp directory and is cleaned up automatically when the run ends; it
**does not touch your own `FLEET_HOME`**, and needs no network, no accounts, no iTerm2.

---

## 1. Prerequisites

| Requirement | Note |
|---|---|
| `tmux` | macOS: `brew install tmux`; Debian/Ubuntu: `sudo apt install tmux` |
| `python3` | 3.6+ is enough, standard library only |
| `bash` | 3.2 works too (the one macOS ships is 3.2; the scripts deliberately avoid bash 4 features like associative arrays) |

Not needed: iTerm2, AppleScript, an IM app, a database, the network.

## 2. Run demo.sh

```bash
git clone <this repo>            # or just go into the copy you already have
cd starfix
bash quickstart/demo.sh
```

Every step prints `✔ 步骤 N：…`; if any step fails it prints `✘` with the reason immediately and
exits non-zero. To keep the temp directory and poke around in it yourself, add `QS_KEEP=1`:

```bash
QS_KEEP=1 bash quickstart/demo.sh
```

The fifteen things the demo does are the things a captain really does in a day:

| Step | What it does | Parts used |
|---|---|---|
| 1–2 | Prerequisite check, create the temp fleet directory | — |
| 3 | Start a tmux session, run a "fake crew member" in the pane | `fake-crew.sh` |
| 4 | Write the task book, register it in the activator (including the captain hook: a task with no owner may not enter in-progress) | `scripts/task-activator.py` |
| 5 | Attach the receipt monitor (reports only newly landed ones) | `receipt-watch.sh` |
| 6 | Two-step delivery: write → read back the keyword → only then send the newline | `send-to-tmux.sh` |
| 7 | Wait for the receipt to land, then **go back and verify the artifact** (the monitor saying it arrived does not count; the file being there does) | — |
| 8 | Acceptance rules it done (the activator reads the receipt's terminal state itself; anything but PASS does not pass) | `task-activator.py set` |
| 9 | Raise one confirmation question; the task gets marked "blocked on a ruling" | `task-activator.py ask add` |
| 10 | Attach the monitor that commits inbox answers | `scripts/ask-inbox-apply.sh` |
| 11–12 | Decision panel lists the unanswered questions, Owner answers (one appended line only) | `ask-cli.py` |
| 13 | Answer is committed → blocker cleared → task unblocked | the two above |
| 14–15 | Final inventory, knock-off cleanup and a check that tmux has no leftovers | — |

## 3. Which part of the full version each file stands in for

| quickstart | full version | difference |
|---|---|---|
| `send-to-tmux.sh` | `scripts/send-to-session.sh` (iTerm2) + `specs/delivery-terminal.en.md` §2 | The same two-step protocol, with only four primitives swapped out: read the input line / write text / read back / send the newline. The iTerm2 version uses AppleScript, this one uses `tmux capture-pane` / `send-keys` |
| `fake-crew.sh` | a real terminal AI session (Codex CLI, Claude Code, a headless worker) + `templates/02-回执.en.md` | Here the "do the work" step is `sleep 2`; a real crew member has a model doing it. The contract — take one line, write the receipt, return to an idle prompt — is exactly the same |
| `receipt-watch.sh` | `specs/monitors.en.md` ① the receipt monitor | The full version relies on the harness's Monitor-type tool; this is a 1-second polling fallback (one of the four fallback steps in the playbook) |
| `ask-cli.py` | `scripts/askpanel/` (the macOS ask panel), `specs/away-mode.en.md` (IM relay) | The panel is replaced by a command line; the inbox contract is identical: one JSON line, append-only |
| `demo.sh` | `skill/SKILL.en.md` §1 first actions on taking office + the README's "one task, from dispatch to production" | Compresses nine steps into fifteen visible ones, dropping merge and deploy (those two need a real repo) |
| — | `scripts/task-activator.py`, `scripts/ask-inbox-apply.sh` | **Used exactly as-is**; the demo copies none of their logic |

## 4. Swap the fake crew member for a real Codex / Claude session

The fake crew member's only job is to "stand in a terminal waiting for one line of input". A real
crew member does the same thing, so swapping it in takes only three changes:

1. **Start the session**. Replace what runs in the pane with your CLI:

   ```bash
   tmux new-session -d -s crew-a1 'codex'      # or 'claude', or whatever your crew CLI is
   tmux list-panes -a -F '#{pane_id} #{session_name}'   # copy the pane id from here, never type it by hand
   ```

   > Session IDs / pane IDs are **only ever copied from the listing**. A hand-typed ID usually
   > shows up as "the other side is busy" rather than "does not exist", so you silently drop a
   > delivery into the void — this is a hard rule in `specs/delivery-terminal.en.md` §3.

2. **Tune the idle criterion**. `send-to-tmux.sh` treats "the cursor line looks like an empty
   prompt" as idle, and the default regex covers lines ending in `❯ › » $ # >`. If your CLI's
   input line looks different (a right-hand border, say, or a grey placeholder hint inside an
   empty box), set `QS_IDLE_RE`:

   ```bash
   QS_IDLE_RE='(❯|›)[[:space:]]*│?[[:space:]]*$' \
     bash quickstart/send-to-tmux.sh %7 "$BOOK" CS-DEMO01
   ```

   Use `--dry-run` first to print the four steps without touching the terminal; only deliver for
   real once you have confirmed the arguments are right.

3. **The task book carries the full closing clause**. A real crew member will not guess what you
   want; the task book must spell out verbatim: the absolute receipt path, that all three
   terminal states (PASS/FAIL/BLOCKED) land on disk, the keyword, the target duration, and how
   to notify the captain once it lands. Template in `templates/01-任务书.en.md`.
   **The keyword must be a literal substring of the delivered message** — if it is not in the
   message the read-back can never match, and the same task will queue up again and again.
   What the demo does can be copied verbatim: use the task ID (`CS-DEMO01`) as the keyword, and
   make the message the task-book path, which contains the task ID.

All four terminal states of a failed delivery have an explicit exit; none of them are silent:

| Output | Exit code | Meaning |
|---|---|---|
| `DELIVERED` | 0 | Written, the newline was consumed, the keyword left the input line |
| `KW_NOT_IN_MSG_ABORT` | 3 | The keyword is not a substring of the message; refused outright (the terminal is not even touched) |
| `MSG_MULTILINE_ABORT` | 3 | The message contains a newline, which would be taken as Enter and submit early; refused |
| `PANE_NOT_FOUND` | 3 | The pane does not exist (reported separately from "busy", so you do not drop another delivery into the void) |
| `COMPOSER_BUSY_ABORT` | 2 | The other side's input line did not go idle within 30 seconds (usually a human typing) |
| `KW_MISMATCH_ABORT` / `UNCONFIRMED_NO_BLIND_CR` | 1 | What is in the input line is not our keyword → **never send the newline**. Rather not deliver than misdeliver |

## 5. Swap ask-cli for the ask panel / IM

The inbox is the only contract, one JSON object per line, **append-only**:

```json
{"qid": "Q01", "answer": "A", "ts": "…", "via": "ask-cli"}
```

So an ask panel, an IM bot, a web form, even `echo >>` — anything can be the panel, as long as it
appends a line in the same format to `$FLEET_ASK_INBOX`. The back half (`ask-inbox-apply.sh` picks
it up → calls `task-activator.py ask answer` → clears the blocker → notifies the captain) needs no
changes at all.

Two you will trip over:

- **Append-only**. Rewriting in place makes `tail -F` replay every historical answer and flush the blockers on every task at once (a real incident).
  To scrub it, stop the monitor first, then edit, then re-attach.
- **Attach before answering**. `ask-inbox-apply.sh` uses `tail -n0 -F` and only sees lines added after it attached; answering before attaching = that answer never gets committed.

`ask-inbox-apply.sh` will `cd` into `FLEET_SCRIPTS_DIR` and then `tail ask-inbox.jsonl` and run
`task-activator.py` **in that directory**. So that directory must hold both of those things.
What the demo does: point `FLEET_SCRIPTS_DIR` at `FLEET_HOME`, then symlink `task-activator.py` into it.

## 6. Environment variables

Required, with **no default** (a default would make the scripts silently read and write in the wrong place on someone else's machine, and an error is the cheapest outcome):

| Variable | Meaning |
|---|---|
| `FLEET_HOME` | The fleet working directory. Without it every script prints one plain sentence and exits |

Everything else has a default:

| Variable | Default | Used by |
|---|---|---|
| `FLEET_RECEIPT_DIR` | `$FLEET_HOME/receipts` | Receipt directory (`fake-crew.sh` writes it, `receipt-watch.sh` watches it) |
| `FLEET_ASK_INBOX` | `$FLEET_HOME/ask-inbox.jsonl` | Answer inbox (`ask-cli.py`) |
| `ACTIVATOR_JSON` | `$FLEET_HOME/task-activator.json` | Activator data file, passed straight through to `task-activator.py` |
| `QS_ACTIVATOR_PY` | `<repo root>/scripts/task-activator.py` | Activator script path (`ask-cli.py`) |
| `QS_TMUX_SOCKET` | empty (use the tmux default socket) | The socket name for `tmux -L` |
| `QS_IDLE_RE` | ends with `(❯｜›｜»｜$｜#｜>)` | The ERE that decides "the input line is idle" |
| `QS_IDLE_TIMEOUT` | `30` | Upper bound in seconds on waiting for the other side to go idle |
| `QS_WATCH_INTERVAL` | `1` | Receipt monitor polling interval (seconds) |
| `QS_CREW_NAME` / `QS_CREW_PROMPT` / `QS_CREW_WORK_SECONDS` | `crew-a1` / `crew> ` / `2` | The fake crew member's code, prompt and simulated duration |
| `QS_KEEP` | `0` | `1` = keep the temp directory after the demo finishes |
| `QS_DEMO_TMPDIR` | `$TMPDIR` | Where the temp fleet directory gets created |

The remaining variables (IM relay, watchdog and so on) are in `scripts/README-env.en.md`.

## 7. How to prove to yourself that this thing is alive

The most important rule in the playbook: **a criterion must be able to answer "under what
condition would it go red?"**. The ones here can:

```bash
# Counterexample: the keyword is not a substring of the message → the terminal is not even touched, refused outright
bash quickstart/send-to-tmux.sh --dry-run %0 "/path/CS-DEMO01-任务书.md" "a word not in the body"
# → KW_NOT_IN_MSG_ABORT, exit code 3

# Positive anchor: same pane, same message, only the keyword swapped for a real substring
bash quickstart/send-to-tmux.sh %0 "/path/CS-DEMO01-任务书.md" "CS-DEMO01"
# → DELIVERED, exit code 0
```

Counterexample and positive anchor have to be run as a pair: seeing "it went red" on its own
proves nothing, because red might just mean the environment is broken; only when swapping out
that half of the criterion in the same environment turns it green does red mean the criterion
caused it. The demo follows this internally too — step 9 first asserts "after the question is
raised the task really is marked blocked on a ruling", then step 13 asserts "after the answer
that mark is gone"; only once both states have been verified is this criterion more than
decoration.

## 8. Known boundaries (deliberately not done)

- **Delivery only supports a single-line message**. Multiple lines get taken as Enter by `send-keys` and submit early, so they are refused outright.
  The proper practice was always "give the task-book path as the message, do not paste the whole task book".
- **The receipt monitor only does 1-second polling**. `fswatch`/`inotify` use less power, but that branch cannot run — and therefore cannot be verified — on a machine
  without fswatch; better to keep one path that can be verified than a branch that "looks supported but nobody has ever actually run it".
  To switch: replace the `sleep` in `receipt-watch.sh` with `fswatch -1 "$DIR"`; the scanning logic does not need to change.
- **No merge and no deploy** (steps ⑦ and ⑧ of the README's "lifecycle of a task"): those two need a real repo and real services, which do not belong in a demo.
- **The fake crew member does not read the task book body and does not check the read whitelist**. It demonstrates the contract only, not build discipline.
- The demo **always creates its own temp `FLEET_HOME`** and never uses the one in your environment — a demo task leaking into a real fleet's data is contamination.

Two cross-platform pitfalls we already hit; watch for them when copying these scripts:

- The `bash` macOS ships is 3.2, which has **no associative arrays** (`declare -A` is a straight syntax error).
- Under a Chinese locale, bash 3.2 counts a multi-byte character immediately following a variable name as part of the name
  (`"$SESSION（socket）"` → reports `SESSION…: unbound variable`). Always write `${VAR}` when a variable is followed by Chinese text.

---

## English

**What this is.** StarFix is a playbook for one long-lived AI "captain" session commanding a
fleet of terminal AI workers. The full setup leans on iTerm2, a native ask-panel and an IM relay.
This folder is the **minimum runnable version**: with only `tmux` and `python3` you can run the
whole loop end to end in about fifteen seconds and see every moving part.

```bash
bash quickstart/demo.sh        # macOS or Linux; add QS_KEEP=1 to keep the temp dir
```

**The loop.** dispatch → two-step delivery → worker builds → receipt lands → monitor fires →
captain accepts → question raised → decision panel → owner answers → inbox → task unblocked.

**The files.**

| file | stands in for | note |
|---|---|---|
| `send-to-tmux.sh` | `scripts/send-to-session.sh` (iTerm2) | same two-step protocol, four primitives swapped to `tmux capture-pane` / `send-keys` |
| `fake-crew.sh` | a real Codex / Claude terminal session | only the "do the work" step is faked (`sleep 2`); the contract is identical |
| `receipt-watch.sh` | the receipt monitor in `specs/monitors.md` | 1s polling fallback |
| `ask-cli.py` | the macOS ask-panel / IM relay | the inbox contract (one appended JSON line) is the real one |
| `demo.sh` | `skill/SKILL.md` §1 plus the task lifecycle | fifteen visible steps, no merge/deploy |
| — | `scripts/task-activator.py`, `scripts/ask-inbox-apply.sh` | reused as-is, no logic copied |

**The one rule that matters most.** Never blind-press Enter in someone else's terminal.
Write the text, read the input line back, and only if your own keyword is sitting there do you
send the newline. The keyword must be a literal substring of the message — otherwise the
read-back can never match and the same task queues up forever. Rather not deliver than misdeliver.

**Requirements.** `tmux`, `python3` (stdlib only), `bash` 3.2+. No network, no accounts,
no iTerm2. `FLEET_HOME` is required and has **no default**: a default would silently read and
write in the wrong place on someone else's machine, and an error message is the cheapest outcome.
