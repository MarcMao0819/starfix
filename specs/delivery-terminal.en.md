# Terminal Interaction Protocol (delivering tasks to other agents)

**语言 / Language:** [中文](delivery-terminal.md) · [English](delivery-terminal.en.md)

The captain's very first question in a terminal is not "what do I dispatch", it is **"what terminal am I in, what terminals are the other agents in, which channel can I use to reach them"**. Terminal applications, multiplexers and IPC buses all differ; iTerm2 is only one implementation and cannot be assumed.

## 0. The first question on taking office: probe

Run `scripts/terminal-probe.sh`. It answers:
- Myself: `TERM_PROGRAM` (iTerm.app / Apple_Terminal / vscode / WezTerm / Windows Terminal …), whether I am inside tmux/screen, whether `osascript` exists, whether the iTerm2 Python API is available, whether there is a same-host agent bus.
- The other agents: each crew member's harness (Codex CLI / Claude Code / headless batch) and the terminal it lives in (session ID, tmux pane, remote host).
- Conclusion: one "recommended channel" per crew member, written into the session list (the output of `fleet-scan.sh`).

The probe result is written to the `fleet-snapshot` next to `monitors-latest.json`, so the successor need not probe again.

## 1. The channel matrix

| Channel | Applies to | Delivery criterion | Risk |
|---|---|---|---|
| Same-host agent bus (e.g. SendMessage) | Between agents on the same harness | The message enters the other side's history | No misdelivery risk; first choice |
| Terminal automation API (iTerm2 Python API / AppleScript, WezTerm CLI, Windows Terminal + SendKeys) | CLI-type agents in a graphical terminal | Two-step delivery with read-back confirmation | **Can misdeliver into a terminal where a human is typing** |
| Multiplexer (tmux `send-keys` / `capture-pane`, screen `stuff`) | Any CLI running inside tmux/screen | Same two steps (read back with capture-pane) | As above |
| File mailbox | The other side has no usable API but polls a directory | The other side's receipt lands on disk | Slow; needs cooperation |
| Batch entry point | Headless workers | `-o` captures the output file | No interaction |
| Human relay | Nothing else works | The ledger records "delivered by hand" | Last resort |

## 2. Two-step delivery (common to every channel that "writes into someone else's input line")

1. **Before writing**: read the other side's input line; it must be idle (nothing after the prompt; grey placeholder text counts as empty), wait at most 30 s, otherwise `COMPOSER_BUSY_ABORT`.
2. **Write the text**: write only, do not submit.
3. **Read-back confirmation**: the input line must contain **our own keyword** before you send the newline. If the input line holds anything else (a human is typing), never press Enter → `KW_MISMATCH_ABORT`.
4. **Keyword guard**: the keyword must be a literal substring of the message body, otherwise the script refuses outright (`KW_NOT_IN_MSG_ABORT`). Not in the body = the read-back never matches = the same task queues up over and over.
5. **Read the screen after submitting**: confirm the other side has turned to Working; on `UNCONFIRMED`, read the screen first to see whether it is already queued, do not resend.
6. A failed delivery may not be silent: write the terminal state into the ledger.

## 3. Session identifiers

- Session IDs / pane IDs are **copied from the list file only; never hand-typed from the first 8 characters**; the error shape from a wrong ID is usually "busy" rather than "does not exist", so delivery silently goes nowhere.
- The main window's own ID goes into the closing clause of the task book; a change must be synced into the rules and every in-flight task book.

## 4. Content rules

- The first line says in one sentence what task this is; the body contains the task book's absolute path, the execution requirements, the receipt path, the keyword and the target duration; do not paste the full task book; write "execute once" to guarantee idempotency.

## 5. Reference implementation: iTerm2

`scripts/send-to-session.sh <SESSION-UUID> "<body>" <keyword>`: two-step delivery driven by the iTerm2 Python API / AppleScript; prerequisites = the iTerm2 Python API enabled and automation permission granted. `--dry-run` prints the three steps without touching the terminal, for the self-check on taking office. To move to another terminal, just reimplement the four primitives of this protocol — read the input line / write the text / read back / send the newline.
