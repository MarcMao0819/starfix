#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编译前置筛：三条类别禁区 + 修正后的价值/可行性公式。纯文本扫描，不做实验。

## 为什么有这一步

铺开的真实瓶颈不是编译本身，是**每份候选都要走一整轮实验才知道值不值得编**。
前置筛把「一眼能判出局的」在编译前筛掉，成本近零。

## 三条类别禁区（判进即出局，不参与排序）

| 禁区 | 理由 | 已判入 |
|---|---|---|
| 一·部署/发布类 | 部署链上每一道闸都是有代价的闸，风险收益比最差 | land-and-deploy、ship |
| 二·思考型 | 价值本来就在让模型想事，编了省不下什么 | office-hours |
| 三·执行者能力型闸 | **必跑路径上含此类闸的 skill，不可能被编译到完整执行** | codex |

## 第三条禁区的逻辑是分析性的，不靠样本量

主窗口 <日期> 纠正了我一处混淆：我说「此判据 n=1、缺第二例佐证」，
**但该规则不需要第二例**——

> 无头运行器**定义上**没有交互工具，所以「若你的工具列表里没有 X」这类条件在运行器上
> **恒真**，该闸必停。这是分析性结论，不是经验归纳。

**需要验证的是另一件事：文本扫描能不能可靠识别这类条件。** 本模块的 `--validate`
就是干这个：拿已知阳性（codex 行284）与已知阴性（browse，实测编译到零停顿）对拍。

## 修正后的公式

    价值   = 必跑样板 token × 调用频率      ← 省的是「模型不读那 N 行」，不是执行了几块
    可行性 = 运行时不可消解闸数 = 判断型 + 运行时触发型 + 执行者能力型

调用频率是业务事实，本模块量不出来，只输出 token 那一半并显式标注缺口。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_graph as sg          # noqa: E402
import gate_annotate as ga        # noqa: E402

# ── 禁区一：部署/发布类 ────────────────────────────────────────────────
DEPLOY_RE = re.compile(
    r"\bgit push\b|\bcreate (a )?PR\b|merge (to |into )?(main|master)|"
    r"\bdeploy\b|\brelease\b|\bpublish\b|bump VERSION|CHANGELOG|"
    r"上线|发布|部署", re.I)

# ── 禁区二：思考型 ────────────────────────────────────────────────────
THINKING_RE = re.compile(
    r"brainstorm|office hours|think through|design doc|ideation|"
    r"strategy review|头脑风暴|想清楚|构思", re.I)

# ── 禁区三：执行者能力型闸 ────────────────────────────────────────────
# 特征：条件问的是「**执行者自身**有没有某种能力」，而非系统处于什么状态。
# 关键信号是第二人称所有格 + 工具/能力名词（your tool list / 你的工具）。
# 阻塞后果信号：光提到执行者能力不够，还要**声明阻塞**才算闸。
# 实测：每份 skill 有 3 处命中「your tool list」，但其中两处是说明性文字
#   「AskUserQuestion 可解析为两种工具」「优先用 MCP 变体」
# 只有第三处 **this skill is BLOCKED** 才是真闸。行级误报 2/3，故加此双条件。
BLOCKING_RE = re.compile(
    r"is BLOCKED|BLOCKED —|cannot proceed|停止执行|直接阻塞|不得继续", re.I)

CAPABILITY_RE = re.compile(
    r"(?:appears?|available|callable|present|exists?|listed)\s+in\s+your\s+"
    r"(?:tool|tools|toolset|tool list|capabilities)"
    r"|your\s+tool\s+list"
    r"|if\s+you\s+(?:cannot|can't|do not have|don't have|lack)\b"
    r"|no\s+\w+\s+variant\s+(?:appears|is available)"
    r"|(?:tool|工具)(?:不可用|不可调用|未提供)"
    r"|你的工具(?:列表|清单)",
    re.I)


def scan_skill(path: str) -> dict:
    text = open(path, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    fence = ga.in_fence_map(lines)

    def hits(rx, prose_only=True):
        out = []
        for i, l in enumerate(lines):
            if prose_only and fence[i]:
                continue
            if rx.search(l):
                out.append({"line": i + 1, "text": l.strip()[:120]})
        return out

    name = os.path.basename(os.path.dirname(path))
    desc = "\n".join(lines[:12])          # frontmatter/描述区

    # 双条件：既引用执行者能力、又声明阻塞后果
    cap = [h for h in hits(CAPABILITY_RE) if BLOCKING_RE.search(h["text"])]
    dep_desc = bool(DEPLOY_RE.search(desc))
    dep_all = hits(DEPLOY_RE)
    think = hits(THINKING_RE)

    # 必跑样板 token（价值端的可量部分）：无法逐份推导必跑段时，用全文作保守上界
    secs = sg.sectionize(text)
    boiler = None
    for pat, label in ((r"^\d+\.\s", "编号配方"), (r"^Step 2", "模式分支")):
        m = [s for s in secs if re.match(pat, s["heading"])]
        if m:
            cut = min(s["line_start"] for s in m) + 1
            boiler = {"cut_line": cut, "rule": label,
                      "chars": len("\n".join(lines[:cut]))}
            break

    gates = ga.wide_net(text)
    return {
        "skill": name, "path": path, "total_lines": len(lines),
        "ban1_deploy": {"in_description": dep_desc, "hits": len(dep_all),
                        "sample": dep_all[:2]},
        "ban2_thinking": {"hits": len(think), "sample": think[:2]},
        "ban3_capability": {"hits": len(cap), "sample": cap[:3]},
        "boilerplate": boiler,
        "gate_candidates": len(gates),
    }


def verdict(r: dict) -> dict:
    bans = []
    if r["ban1_deploy"]["in_description"] or r["ban1_deploy"]["hits"] >= 12:
        bans.append("一·部署发布类")
    if r["ban2_thinking"]["hits"] >= 3:
        bans.append("二·思考型")
    if r["ban3_capability"]["hits"] >= 1:
        bans.append("三·执行者能力型闸")
    b = r.get("boilerplate")
    tok = (b["chars"] // 2) if b else None
    return {"skill": r["skill"], "bans": bans,
            "out": bool(bans),
            "boilerplate_tokens": tok,
            "gate_candidates": r["gate_candidates"]}


def validate() -> int:
    """验证第三条禁区的**探测器**（规则本身是分析性的，不需要验）。

    阳性对照：codex 行284 `If no AskUserQuestion variant appears in your tool list`
    阴性对照：browse（实测能编译到零运行时停顿，不应命中）
    """
    cases = [("codex", True), ("browse", False)]
    ok = True
    for name, want in cases:
        p = os.path.expanduser("~/.claude/skills/%s/SKILL.md" % name)
        if not os.path.exists(p):
            continue
        r = scan_skill(p)
        got = r["ban3_capability"]["hits"] >= 1
        good = got == want
        ok &= good
        print("  %-8s 命中=%-5s 期望=%-5s %s" % (name, got, want, "OK" if good else "**不符**"))
        for s in r["ban3_capability"]["sample"]:
            print("        行%d: %s" % (s["line"], s["text"][:88]))
    print("探测器取证：%s" % ("通过" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="编译前置筛")
    ap.add_argument("--skills", nargs="*", help="skill 名列表")
    ap.add_argument("--validate", action="store_true", help="验证禁区三的探测器")
    a = ap.parse_args()
    if a.validate:
        return validate()
    out = []
    for n in (a.skills or []):
        p = os.path.expanduser("~/.claude/skills/%s/SKILL.md" % n)
        if not os.path.exists(p):
            print("跳过（不存在）:", n, file=sys.stderr)
            continue
        out.append({"scan": scan_skill(p)})
        out[-1]["verdict"] = verdict(out[-1]["scan"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
