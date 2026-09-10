#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从任务激活器导出停工哨兵的机器任务名单；只读，不改 JSON。"""
from __future__ import annotations

import json
import os
import re
import sys


BASE = os.environ.get("FLEET_HOME")
if not BASE:
    raise SystemExit("缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md")
ROOT = os.path.dirname(BASE)
DEFAULT_JSON = os.environ.get("ACTIVATOR_JSON", os.path.join(BASE, "task-activator.json"))
MACHINE_OWNER = re.compile(os.environ.get("FLEET_MACHINE_OWNER_RE", r"^(?:crew-[a-z0-9]+|bot-[a-z0-9]+)$"))


def receipt_path(raw: str) -> str:
    """把激活器中的回执相对路径补到迁移备份根，不改原记录。"""
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    if raw.startswith("迁移备份/"):
        return os.path.normpath(os.path.join(ROOT, raw))
    return os.path.normpath(os.path.join(BASE, raw))


def main() -> int:
    json_path = os.environ.get("ACTIVATOR_JSON", DEFAULT_JSON)
    try:
        with open(json_path, encoding="utf-8") as handle:
            tasks = json.load(handle).get("tasks", [])
    except (OSError, json.JSONDecodeError) as exc:
        print(f"激活器读取失败：{exc}", file=sys.stderr)
        return 2

    for task in tasks:
        owner = (task.get("owner") or "").strip()
        if task.get("status") != "施工中" or not MACHINE_OWNER.fullmatch(owner):
            continue
        task_id = str(task.get("id") or "").strip()
        worktree = (task.get("worktree") or "").strip()
        receipt = receipt_path((task.get("receipt") or "").strip())
        if not worktree and not receipt:
            print(f"缺路径：{task_id}", file=sys.stderr)
            continue
        print(f"{task_id}({owner})|{worktree}|{receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
