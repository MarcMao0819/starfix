#!/usr/bin/env python3
"""Read-only iTerm text classifier for fleet probes, delivery, and idle watch.

Only terminal status/composer lines decide the state. Unknown layouts stay UNKNOWN.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)?)")
BRAILLE = re.compile(r"[\u2800-\u28ff]")
WAIT = re.compile(r"^\s*[•✢]\s*Waiting for background terminal\b", re.I)
CODEX_WORK = re.compile(r"^\s*[•✢]\s*(?:Working|Thinking)\b", re.I)
CODEX_DONE = re.compile(r"^\s*(?:[•✢]\s*)?Worked for\b.*\bdone\s+\d{1,2}:\d{2}", re.I)
CLAUDE_DONE = re.compile(r"^\s*✻\s*(?:Worked|Baked|Cooked|Churned|Brewed) for\b.*\bdone\s+\d{1,2}:\d{2}", re.I)
CLAUDE_WORK = re.compile(r"^\s*[·✻✳]\s*[A-Za-z][A-Za-z ]{1,30}…\s*\((?:running|\d+s)\b", re.I)
OPEN_WORK = re.compile(r"^\s*[┃│]?\s*(?:Working|Thinking|Generating)\b", re.I)
UPDATE = re.compile(r"\b(?:update available|new version available|update codex|install update|update now|press enter to update)\b|(?:更新可用|现在更新|立即更新)", re.I)
UPDATE_CHOICE = re.compile(r"(?:^\s*[1-9][.)]\s*|\b(?:later|skip|cancel|yes|no|enter|update now)\b|稍后|跳过|取消|更新)", re.I)
UPDATE_1 = re.compile(r"^\s*[›»❯]?\s*1\.\s*Update now\b", re.I)
UPDATE_2 = re.compile(r"^\s*2\.\s*Skip\b", re.I)
UPDATE_3 = re.compile(r"^\s*3\.\s*Skip until next version\b", re.I)
PROMPT = re.compile(r"^\s*([❯›»])\s*(.*)$")
PLACEHOLDER = re.compile(r"^(?:Ask Codex to do anything|Ask anything|Build anything|Build ·|ctrl\+p commands|Run a command|Run /connect|Press up to edit queued messages)\b", re.I)
DONE_TIME = re.compile(r"\bdone\s+(\d{1,2}:\d{2}(?:\s*[AP]M)?)\b", re.I)


def classify(raw: str, cli_hint: str = "auto") -> dict[str, str | bool]:
    text = ANSI.sub("", raw).replace("\r", "\n").replace("\xa0", " ")
    lines = text.splitlines()
    statuses: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines):
        if WAIT.search(line):
            statuses.append((index, "WAITING", "codex", line.strip()))
        elif CODEX_WORK.search(line):
            statuses.append((index, "WORKING", "codex", line.strip()))
        elif CODEX_DONE.search(line):
            statuses.append((index, "IDLE", "codex", line.strip()))
        elif CLAUDE_DONE.search(line):
            statuses.append((index, "IDLE", "claude", line.strip()))
        elif CLAUDE_WORK.search(line):
            statuses.append((index, "WORKING", "claude", line.strip()))
        elif OPEN_WORK.search(line) and ("┃" in line or "│" in line):
            statuses.append((index, "WORKING", "opencode", line.strip()))
    last = statuses[-1] if statuses else None
    update_options = [next((i for i, line in enumerate(lines) if pattern.search(line)), -1)
                      for pattern in (UPDATE_1, UPDATE_2, UPDATE_3)]
    has_update_options = update_options[0] >= 0 and update_options[0] < update_options[1] < update_options[2]
    cli = cli_hint if cli_hint != "auto" else (
        last[2] if last else
        "codex" if has_update_options else
        "claude" if "⏵⏵ auto mode" in text[-3000:] or "CLAUDE.md" in text[-3000:] else
        "codex" if "Ask Codex to do anything" in text[-3000:] else
        "opencode" if "OpenCode" in text[-3000:] else "unknown")
    prompt_rows: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = PROMPT.match(line)
        if match:
            value = BRAILLE.sub("", match.group(2)).strip(" │┃\t")
            prompt_rows.append((index, value))
    prompt = prompt_rows[-1] if prompt_rows else None
    # A prompt in the terminal footer can follow the status row. It may be empty
    # while a turn is Working; the status row still wins in that case.
    def footer_line(line: str) -> bool:
        value = line.strip()
        return (not value or bool(re.match(r"^[─━│┃┄╌]{5,}$", value))
                or value.startswith(("⏵⏵", "← ", "上下文", "用量", "✓", "CLAUDE.md"))
                or bool(re.match(r"^\d+ CLAUDE\.md|^\[[^]]+\]\s*│", value))
                or bool(re.search(r"\bContext \d+% left\b", value)))

    prompt_current = bool(prompt and (last is None or prompt[0] >= last[0])
                          and all(footer_line(line) for line in lines[prompt[0] + 1 :]))
    has_draft = bool(prompt_current and prompt and prompt[1] and not PLACEHOLDER.search(prompt[1]))
    def unclassified_after_status() -> bool:
        if not last or not prompt:
            return False
        detail_indent: int | None = None
        detail_open = False
        detail_seen = False
        for line in lines[last[0] + 1 : prompt[0]]:
            if not line.strip():
                detail_open = False
                continue
            if re.match(r"^\s*[─━│┃]", line):
                detail_open = False
                continue
            branch = re.match(r"^([ \t]*)└(?:[ \t]|$)", line)
            if branch and not detail_seen:
                detail_indent = len(branch.group(1))
                detail_open = True
                detail_seen = True
                continue
            if detail_open and detail_indent is not None:
                indent = len(line) - len(line.lstrip(" \t"))
                if indent > detail_indent:
                    continue  # Physical wrap of the immediately preceding └ detail.
            return True
        return False

    stale_working = bool(last and last[1] in ("WORKING", "WAITING")
                         and prompt_current and unclassified_after_status())
    recent = [(i, line) for i, line in enumerate(lines) if line.strip()]
    update_rows = [(i, line) for i, line in recent if UPDATE.search(line)]
    update_current = bool((has_update_options and (last is None or update_options[2] > last[0]))
                          or (update_rows and (last is None or update_rows[-1][0] > last[0])
                              and any(UPDATE_CHOICE.search(line) for i, line in recent if i >= update_rows[-1][0])))
    if update_current:
        state, reason = "UNKNOWN", "update prompt: do not send or choose"
    elif has_draft:
        state, reason = "DRAFT_PRESENT", "current composer has text"
    elif stale_working:
        state, reason = "UNKNOWN", "working row followed by unclassified output"
    elif last:
        state, reason = last[1], "last terminal status"
    elif prompt_current and prompt and (not prompt[1] or PLACEHOLDER.search(prompt[1])) and cli != "unknown":
        state, reason = "IDLE", "current empty composer"
    else:
        state, reason = "UNKNOWN", "no trusted current status/composer"
    done_match = DONE_TIME.search(last[3]) if last and last[1] == "IDLE" else None
    return {"state": state, "cli": cli, "reason": reason,
            "blocker": "UPDATE_PROMPT" if update_current else "",
            "done_time": done_match.group(1) if done_match else "",
            "has_draft": has_draft,
            "status_line": last[3] if last else ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", choices=("auto", "codex", "claude", "opencode", "deepseek", "shared-prompt"), default="auto")
    parser.add_argument("--format", choices=("state", "cli", "blocker", "json", "tsv"), default="state")
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    try:
        raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    except OSError as exc:
        print("UNKNOWN", file=sys.stdout)
        print("session-state read error: " + str(exc), file=sys.stderr)
        return 1
    result = classify(raw, args.cli)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False))
    elif args.format == "cli":
        print(result["cli"])
    elif args.format == "blocker":
        print(result["blocker"])
    elif args.format == "tsv":
        print("\t".join((str(result["state"]), str(result["cli"]), str(result["done_time"]))))
    else:
        print(result["state"])
    return 0 if result["state"] != "UNKNOWN" else 3


if __name__ == "__main__":
    raise SystemExit(main())
