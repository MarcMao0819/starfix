#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警一键处置 —— 供菜单栏 app 调用

## 为什么不做「一个按钮关掉」

一键关闭而不记原因，正是给 B 立两条不变式要防的东西：**告警消费闭环变成告警消音器**。
所以这里没有「关闭」这个动作，只有**四种带含义的处置**，每种写不同的处置段、
落不同的台账字段。点一下仍然只是一下，但那一下说明了「为什么可以不管它」。

| 动作 | 语义 | 谁该用 |
|---|---|---|
| `confirm` | 人工确认放行：我看过了，判词属实但无需行动 | Owner / 主窗口 |
| `rework`  | 转承建方返工：登记或交付有问题，要人改 | Owner / 主窗口 |
| `trace`   | 转轨迹线自查：判据病或执行器病，不该占业务队列 | Owner / 主窗口 |
| `later`   | 记为已知、稍后处理：不关闭，只是别再当新问题 | Owner |

`later` **刻意不算已处置**——它写的段落标题不含「处置」二字，
未处置计数仍会算上它。否则「稍后」就成了不留痕的关闭。

## 落点（v2 修正）

初版只把处置段**追加进已有的** `回执/机检告警-<单号>.md`，以为「目录被监听」就够了。
**不够**：全天验证过的可靠通知通道只有两条——**新文件落盘** 与 **SendMessage**。
往已有文件尾部追加两条都不占，写了等于没通知（<日期> 实测：Owner 点了六张，
主窗口一张都没看到）。

改为三处同时落：
1. 处置段追加进原告警文件（保持单张告警的完整历史）
2. **另落一份新文件** `回执/告警处置-<单号>-<动作>.md` —— 新文件才会被监听到
3. `告警处置台账.jsonl` 供机器统计

「新文件」这一条是给**需要别人接手**的动作准备的（`rework` 转承建方、`trace` 转轨迹线）。
`confirm`（我看过了没事）与 `later`（稍后）不需要惊动别人，只写 1 与 3。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import sys

ALERTDIR = os.environ.get("ALERTDIR", "${FLEET_HOME}/<项目>ERP/迁移备份/回执")
LEDGER = os.environ.get("DISPOSE_LEDGER",
                        "${TRAJ_DATA_DIR}/changeset-audit/告警处置台账.jsonl")

# 哪些动作需要**别人接手** —— 这些才落新文件通知。
# confirm/later 是「我自己消化了」，不该给别人制造一个新文件要看。
HANDOFF_ACTIONS = {"rework": "承建方", "trace": "轨迹编译线（舰员癸）"}

ACTIONS = {
    "confirm": ("## 人工确认放行（{ts} · {actor}）",
                "判词属实，但经人工判断**无需进一步行动**，本告警关闭。\n"
                "> 依据是「看过并判断无需行动」，**不是「机器判错了」**——原判决原样保留在 `机检判决.jsonl`。",
                True),
    "rework":  ("## 转承建方返工（{ts} · {actor}）",
                "登记或交付确有问题，**需承建方修改**。本告警在返工完成前保持打开。\n"
                "> 承建方修完后锚点/明细变化会触发漂移通路自动重审，无需人工重跑。",
                True),
    "trace":   ("## 转轨迹线自查（{ts} · {actor}）",
                "判定为**判据病或执行器病**——问题在机检侧，不在被审对象，**不占业务人工队列**。\n"
                "> 已转轨迹编译线（舰员癸）自查。",
                True),
    "later":   ("## 已知·稍后处理（{ts} · {actor}）",
                "已被人看到并记录，**但尚未处置**。本条**仍计入未处置数**——\n"
                "> 「稍后」不等于「关闭」。若它变成不留痕的关闭，未处置计数就失去意义了。",
                False),
}


def dispose(changeset_no: str, action: str, actor: str, note: str = "") -> dict:
    if action not in ACTIONS:
        raise ValueError("未知处置动作: %s（可选 %s）" % (action, "/".join(ACTIONS)))
    fn = os.path.join(ALERTDIR, "机检告警-%s.md" % changeset_no)
    if not os.path.exists(fn):
        raise LookupError("找不到告警文件: %s" % fn)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head, body, counts_as_handled = ACTIONS[action]
    sec = "\n" + head.format(ts=ts, actor=actor) + "\n\n" + body + "\n"
    if note:
        sec += "\n**补充说明**：%s\n" % note
    io.open(fn, "a", encoding="utf-8", newline="").write(sec)
    handoff_file = None
    if action in HANDOFF_ACTIONS:
        # 新文件才会被监听到。文件名带动作，接手方一眼知道是不是自己的。
        handoff_file = os.path.join(ALERTDIR, "告警处置-%s-%s.md" % (changeset_no, action))
        io.open(handoff_file, "w", encoding="utf-8", newline="").write(
            "# 告警处置移交 · %s → %s\n\n"
            "- 变更单：`%s`\n- 处置动作：`%s`\n- 处置人：%s\n- 时间：%s\n"
            "- 原告警：`机检告警-%s.md`（处置段已追加在其尾部）\n\n"
            "%s\n%s"
            % (changeset_no, HANDOFF_ACTIONS[action], changeset_no, action, actor, ts,
               changeset_no, body,
               ("\n**补充说明**：%s\n" % note) if note else ""))
    rec = {"ts": ts, "changeset_no": changeset_no, "action": action, "actor": actor,
           "note": note, "counts_as_handled": counts_as_handled, "file": fn,
           "handoff_file": handoff_file}
    io.open(LEDGER, "a", encoding="utf-8", newline="").write(
        json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def pending_count() -> int:
    """未处置数。判据与 status_snapshot 一致：文件里没有任何 `^## ` 段即未处置。
    注意 `later` 写的段落标题是「已知·稍后处理」，**含 `## ` 所以会被算成已处置**——
    这与模块文档说的「later 不算已处置」矛盾，故此处显式排除它。"""
    n = 0
    for f in sorted(os.listdir(ALERTDIR)):
        if not (f.startswith("机检告警-") and f.endswith(".md")):
            continue
        s = io.open(os.path.join(ALERTDIR, f), encoding="utf-8", errors="replace").read()
        secs = re.findall(r"^## (.+)$", s, re.M)
        real = [x for x in secs if not x.startswith("已知·稍后处理")]
        if not real:
            n += 1
    return n


def selftest() -> int:
    """正反例：四种动作各写各的段、later 不计入已处置。用临时目录，不碰生产。"""
    import tempfile
    global ALERTDIR, LEDGER
    tmp = tempfile.mkdtemp()
    ALERTDIR, LEDGER = tmp, os.path.join(tmp, "ledger.jsonl")
    ok = True
    for act, (head, _b, handled) in ACTIONS.items():
        cs = "CS-TEST-%s" % act
        io.open(os.path.join(tmp, "机检告警-%s.md" % cs), "w", encoding="utf-8").write(
            "# 机检告警 - %s - overall=FAIL\n- 正文\n" % cs)
        r = dispose(cs, act, "自检")
        s = io.open(os.path.join(tmp, "机检告警-%s.md" % cs), encoding="utf-8").read()
        wrote = head.split("（")[0] in s
        # 移交类动作必须另落新文件；非移交类必须**不**落，免得给人凭空造待办
        want_handoff = act in HANDOFF_ACTIONS
        got_handoff = r["handoff_file"] is not None and os.path.exists(r["handoff_file"])
        good = wrote and r["counts_as_handled"] == handled and got_handoff == want_handoff
        ok &= good
        print("  %-8s 段落写入=%-5s 计入已处置=%-5s 移交新文件=%-5s(期望%-5s) %s"
              % (act, wrote, handled, got_handoff, want_handoff, "OK" if good else "**不符**"))
    pc = pending_count()
    good = (pc == 1)          # 只有 later 那张仍算未处置
    ok &= good
    print("\n  未处置计数=%d（期望 1：只有 later 那张不算已处置） %s" % (pc, "OK" if good else "**不符**"))
    # 反例：不存在的单必须抛错，不许静默成功
    try:
        dispose("CS-NOT-EXIST", "confirm", "自检")
        print("  反例·不存在的单 **未抛错**"); ok = False
    except LookupError:
        print("  反例·不存在的单 正确抛 LookupError  OK")
    # 反例：未知动作必须抛错
    try:
        dispose("CS-TEST-confirm", "close", "自检")
        print("  反例·未知动作 **未抛错**"); ok = False
    except ValueError:
        print("  反例·未知动作 正确抛 ValueError  OK")
    print("\n自检：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="告警一键处置")
    ap.add_argument("changeset_no", nargs="?")
    ap.add_argument("--action", choices=list(ACTIONS))
    ap.add_argument("--actor", default="Owner")
    ap.add_argument("--note", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.changeset_no or not a.action:
        ap.error("需要 changeset_no 与 --action")
    r = dispose(a.changeset_no, a.action, a.actor, a.note)
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
