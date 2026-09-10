# Memory System Specification (Memory System)

**语言 / Language:** [中文](memory-system.md) · [English](memory-system.en.md)

The captain will be compacted, will be replaced, and will have several windows open at once. What is in your head is not memory; what is in a file is. The whole memory system has six layers, each answering a different question; mixing layers is the common disease (writing project facts into the doctrine, writing a line into the ledger, writing credentials into any layer at all).

## 1. The six layers

| Layer | Files | Answers what | Auto-loaded | Maintained by |
|---|---|---|---|---|
| 1 Index | `MEMORY.md` | "What memories exist" — one line each: title + file + a one-line hook | Yes (injected each session) | The captain, adding a line after writing a fact file |
| 2 Fact files | `memory/*.md` (frontmatter: `type: user / feedback / project / reference`) | "The conclusion about one thing + Why + How to apply" | No (recalled via the index) | The captain |
| 3 Strategy / doctrine | `doctrine/00–04` | "How to judge, how I am examined, how tools lie" | Required reading in the first actions on taking office | The captain (cases filed the same day) |
| 4 Profiles | `crew/<code>.md`, `people/<role>.md` | "Who this agent / this person is — strengths / line / open promises" | Read before dispatching / before messaging | The captain (updated after taking in a receipt / after a conversation) |
| 5 Ledger and activator | `ledger-*.md`, `task-activator.json`, `receipts/` | "What was done today, where the tasks are, who answered what" | No (grep on demand) | Scripts + the captain |
| 6 Annexes | `MEMORY-archive.md`, `MEMORY-optin-*.md`, the module index | "Low-activity / already-released / off-line entries" | **No**, only read when the Owner says so | The captain (moved out when the index tops out, the original text kept verbatim) |

## 2. Writing rules

- **One file, one fact**; before writing, check whether a file on the same subject exists — if it does, update it rather than creating a new one, and delete what turned out wrong.
- `feedback` entries must carry **Why** and **How to apply**; `project` entries convert relative dates into absolute dates.
- Do not write down what the repository already records (code structure, git history, CLAUDE.md); when the Owner asks for that kind of thing, ask "what is the non-obvious part" and record that instead.
- **The index holds one-line pointers only**, never content; when the index tops out, move whole low-activity entries into an annex and leave one line in the index saying "only read when reading the archive".
- Names become roles, project nouns are minimised, and **credentials never enter any layer** (even a trace saying "the value has been blanked" is a single sentence at most).
- The daily report is a single file updated by overwrite (`daily_report_latest.md`) and does not go into the index body.

## 3. Recall rules

- Session start: the index enters the context automatically; the first actions on taking office read the doctrine and the latest 10 cases.
- Before acting: recall the relevant fact files via the index hooks; read the crew profile before dispatching; read the stakeholder profile before messaging.
- A recalled memory was "true when written": before citing a file/function/switch name it matched, verify it still exists.
- Annexes: read only when the Owner says "read the annex / read the archive"; entries that become active again move back into the index.

## 4. The growth loop

Judgement overturned / a lucky guess / a default line overruled → file it in `02-试错战例集` the same day (the first-person process) → elevate it into a rule in the skill (the statute) → whatever can become a gate becomes a gate ("remembering" is not remembering, "it goes red when you run it" is).

## 5. Scrubbing and open-sourcing

- The `doctrine/` in this repo is a scrubbed sample of the strategic layer; the fact layer, the profile layer and the ledger layer **do not go into the repo** (`.gitignore` already excludes runtime files).
- Run `tools/scrub-gate.sh` before sharing any layer.
