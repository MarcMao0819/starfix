#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人闸与守卫的双通道标注：机械宽网出候选 + 模型编译期逐条裁决。

## 为什么不再用正则判人闸

正则漏检过 8 道人闸，browse 在 active 期间跨过 6 道要求停下问人的关卡。
**漏检人闸不是少跑一步，是未经授权替人做了决定**——这是整个系统的信任边界。

补正则也不行：三种守卫写法已经出现（纯大写令牌行尾冒号 / 令牌嵌句中 / 令牌带参数
含空格小写），每补一种都是一次未经验证的猜测。根本原因是
**用穷举去逼近开放集合本身就不成立**——自造 fixture 全过、真 skill 一上就露。

## 双通道的分工依据

    机械擅长「找出所有长得像的」，模型擅长「判断这一条是指令还是描述」。

所以：
- **通道A 宽网**：只求召回不求精度，**刻意不加排除式**——排除是模型的活
- **通道B 模型裁决**：编译期一次性逐条裁，裁「否」必须带原文引用理由

编译期模型成本随便花（一份 skill 标一次，之后每次调用零成本），但**每条裁决必须
带出处**——不可回溯的裁决等于没有裁决，出了事无法归因就无法修。

## fail-closed：所有分支默认方向都是「停」

停是可补的，越权是不可逆的。逐阶段见 `FAILCLOSED` 表与 `adjudicate_guard()`。

设计说明全文：`${FLEET_HOME}/<项目>ERP/迁移备份/设计说明-人闸双通道标注-20260814.md`

只用 Python 3 标准库。本模块**不执行任何 skill 命令**，只读文本、产标注。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

SCHEMA = "gate-annotation/v1"

# ---------------------------------------------------------------------------
# 通道A：机械宽网
# ---------------------------------------------------------------------------
#
# **要召回不要精度**。宁可把工具清单条目、否定句、语义说明全捞进来，也不能漏掉
# 一道真人闸——漏检的代价是越权，误检的代价只是多一条待裁决。
WIDE_NET_RE = re.compile(
    r"AskUserQuestion"
    r"|\bSTOP\b|\bBLOCKED\b|stop-gate"
    r"|ask (?:the )?user|tell (?:the )?user|wait for (?:the )?user"
    r"|confirm with|approval|authori[sz]e"
    r"|停下等|等待用户|征得同意|请示|需(?:用户|人工)确认|转人工|交人工|人工放行",
    re.I)

# 守卫候选：条件句里被反引号/引号包裹的记号。**不限定纯大写**——
# 形态3（`UPGRADE_AVAILABLE <old> <new>`，带参数含空格小写）就是被旧正则漏掉的。
GUARD_CAND_RE = re.compile(
    r"(?:^|\s)(?:If|IF|When|若|如果|当)\b[^\n]*?[`‘“]([^`’”\n]{2,60})[`’”]",
    re.I)

# 回显令牌（供 emitted_by 引用复核用）：块里 echo 出的字面记号
ECHO_RE = re.compile(r"""echo\s+["']?([A-Za-z_][A-Za-z0-9_]{2,40})""")

FAILCLOSED = {
    "wide_net_miss": "执行期兜底再扫一次窄网，命中候选集外的疑似形态一律停",
    "model_error": "该候选判 GATE（停）",
    "no_evidence_quote": "拒收该条裁决，退回判 GATE",
    "guard_unparsed": "视为无条件人闸（每次都停）",
    "emitted_by_unverified": "标为运行时解析（Owner <日期> 修订；原为降级无条件人闸）",
    "fingerprint_mismatch": "整份标注失效，退回读原文",
}


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def in_fence_map(lines: list[str]) -> list[bool]:
    """标出每行是否在代码围栏内。围栏内的行不参与散文级的人闸/守卫识别。"""
    out, inf = [], False
    for l in lines:
        if l.strip().startswith("```"):
            inf = not inf
            out.append(True)          # 围栏定界行本身也算围栏内
            continue
        out.append(inf)
    return out


def wide_net(text: str) -> list[dict]:
    """通道A：扫出全部人闸候选。**不做任何排除**。"""
    lines = text.split("\n")
    fence = in_fence_map(lines)
    cands = []
    for i, l in enumerate(lines):
        if fence[i] or not l.strip():
            continue
        if WIDE_NET_RE.search(l):
            g = GUARD_CAND_RE.search(l)
            cands.append({
                "id": "c%d" % (len(cands) + 1),
                "source": {"line": i + 1, "text": l.strip()[:300]},
                "guard_hint": g.group(1).strip() if g else None,
            })
    return cands


def emitted_tokens(text: str) -> dict[str, str]:
    """全文扫出「哪个围栏块回显了哪个记号」，供 emitted_by 引用复核。"""
    lines = text.split("\n")
    out: dict[str, str] = {}
    inf, start, cur = False, 0, []
    bid = 0
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            if not inf:
                inf, start, cur = True, i, []
            else:
                inf = False
                bid += 1
                for tok in ECHO_RE.findall("\n".join(cur)):
                    out.setdefault(tok, "blk%d@L%d" % (bid, start + 1))
            continue
        if inf:
            cur.append(l)
    return out


# ---------------------------------------------------------------------------
# 通道B 的机械复核部分（模型裁决之后跑，不依赖模型自觉）
# ---------------------------------------------------------------------------

def verify_adjudication(ann: dict, text: str) -> dict:
    """对模型裁决做机械复核，**不信自觉**。

    复核三件事，任一不过即按 fail-closed 降级：
      1. 裁 NOT_GATE 必须带 evidence_quote，且该引用要能在原文里找到
      2. guard 若声明了 token，必须真有某个块回显它（第一性原则：意义由引用决定）
      3. 裁决必须覆盖全部候选，不许漏条
    """
    emitted = emitted_tokens(text)
    issues, downgraded = [], []
    seen = set()
    for c in ann.get("candidates", []):
        cid = c.get("id")
        seen.add(cid)
        v = c.get("verdict")
        if v == "NOT_GATE":
            q = (c.get("evidence_quote") or "").strip()
            if not q:
                c["verdict"] = "GATE"
                c["_downgrade_reason"] = "裁 NOT_GATE 但未给原文引用 → " + FAILCLOSED["no_evidence_quote"]
                downgraded.append(cid)
            elif q[:40] not in text:
                c["verdict"] = "GATE"
                c["_downgrade_reason"] = "原文引用在正文中找不到（引用不实）→ 退回判 GATE"
                downgraded.append(cid)
        g = c.get("guard") or {}
        tok = g.get("token")
        if c.get("verdict") == "GATE":
            if not tok:
                g["kind"] = g.get("kind") or "none"
                c["blocking_unconditional"] = True
            elif tok not in emitted:
                # [Owner <日期> 修订] 原规则「静态复核不过 → 降级为无条件人闸」实测
                # 代价过大：browse 的 UPGRADE_AVAILABLE 运行时确实会出现，但来源是程序
                # stdout（`echo "$_UPD"`）而非字面 echo，静态扫不到，于是被降成每次都停
                # ——执行块数从 10 掉到 1，编译产物基本失去价值。
                #
                # 改为标「运行时解析」。**这不是放松安全下限**：执行器本就按真实输出求值，
                # 运行时若该令牌始终没出现，守卫仍然不成立、该闸照样停。区别只是把判定
                # 挪到信息更全的时点，而不是因为「静态看不见」就永久卡死。
                g["resolution"] = "runtime"
                g["static_emitter"] = None
                c["blocking_unconditional"] = False
                c["_note"] = ("守卫令牌 %r 静态扫不到回显块（多为程序 stdout），"
                              "转运行时解析；运行时无该令牌则照常停。" % tok)
                downgraded.append(cid)
            else:
                g["emitted_by"] = emitted[tok]
                c["blocking_unconditional"] = False
            c["guard"] = g
    return {"downgraded": downgraded, "issues": issues, "covered": len(seen)}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="人闸双通道标注（通道A 宽网 + 复核）")
    ap.add_argument("--skill", required=True, help="skill 原文路径")
    ap.add_argument("--wide-net", action="store_true", help="只跑通道A，输出候选")
    ap.add_argument("--verify", help="对已有标注文件做机械复核")
    a = ap.parse_args()
    text = open(a.skill, encoding="utf-8", errors="replace").read()

    if a.verify:
        ann = json.load(open(a.verify, encoding="utf-8"))
        if ann["skill"]["source_sha256"] != sha(text):
            print(json.dumps({"verdict": "FINGERPRINT_MISMATCH",
                              "action": FAILCLOSED["fingerprint_mismatch"]}, ensure_ascii=False))
            return 2
        r = verify_adjudication(ann, text)
        json.dump(ann, open(a.verify, "w", encoding="utf-8", newline=""),
                  ensure_ascii=False, indent=1)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    cands = wide_net(text)
    out = {
        "schema": SCHEMA,
        "skill": {"path": a.skill, "source_sha256": sha(text),
                  "source_lines": len(text.split("\n"))},
        "channel_a": {"rule": "宽网只求召回不求精度，刻意不加排除式",
                      "candidate_count": len(cands)},
        "emitted_index": emitted_tokens(text),
        "candidates": cands,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# 编译期授权（v2）：授权在编译期一次性拿到，执行期零常规人闸
# ---------------------------------------------------------------------------
#
# 起因（Owner <日期>）：这些授权本该在用户设计/编译 skill 时就问掉，编译器做完之后
# 执行期不该再要授权。**skill 原文自己就证明了这一点**——7 道闸每道后面都跟着一个标记
# 文件（touch ~/.gstack/.telemetry-prompted 之类），第 239 行明写 `This only happens
# once per project`。它们设计上就是一次性的；之所以还赖在执行路径里每次查一遍，
# **只是因为 skill 没有编译期**，无处安放「一次性的事」。
#
# 编译粒度是 **(skill × 作用域) 的首次**，不是 skill 的首次——因为编译期可能缺上下文：
# 「要不要往这个项目的 CLAUDE.md 注入路由」执行时有上下文，编译期没有。
# 所以 project 类的问题**在该项目首次使用时**问，那就是该项目的编译期。

SCOPES = ("global", "project", "session")

# 应答通道白名单：只认面向人的通道
HUMAN_CHANNELS = ("AskUserQuestion", "tty-prompt")

# 作用域信号：原文含这些字样却标 global 的，机械打回
PROJECT_SIGNALS = ("per project", "per-project", "once per project",
                   "$SLUG", "$(pwd)", "basename", "本项目", "该项目")


def verify_authorizations(auths: list[dict], skill_sha: str) -> dict:
    """授权记录校验。**这是机器闸，不是文档承诺。**

    最要紧的一条（<日期> 评审必改项）：
    **授权问题的应答方必须是人，任何 agent 在任何阶段不得代答。**

    为什么现在就写死：这条今天看理所当然，但编译自动化铺开之后，**一定会出现 agent
    顺手替答的诱惑**——尤其批量编译几十份 skill、每份卡在几个问题上的时候。底线必须在
    诱惑出现之前立在代码里，而不是靠当时的自觉。

    agent 替人授权是越权里最难追查的一种——**它留下的记录看起来和真授权一模一样**。
    所以身份字段必须落盘、必须可审计，缺一即拒收。
    """
    ok, rejected = [], []
    for a in auths:
        aid = a.get("id", "?")
        by = a.get("answered_by") or {}
        why = None
        if by.get("principal_type") != "human":
            why = "应答方 principal_type=%r 不是 human —— **agent 不得代答授权问题**" % by.get("principal_type")
        elif by.get("channel") not in HUMAN_CHANNELS:
            why = "应答通道 %r 不在面向人的白名单 %s 内" % (by.get("channel"), list(HUMAN_CHANNELS))
        elif not by.get("id"):
            why = "缺应答者身份 id，无法审计"
        elif not by.get("attested_at"):
            why = "缺应答时间戳，无法审计"
        elif a.get("scope") not in SCOPES:
            why = "作用域 %r 非法" % a.get("scope")
        elif a.get("scope") == "project" and not a.get("scope_key"):
            why = "project 作用域必须带 scope_key（否则会跨项目误用授权）"
        elif not (a.get("consequence") or "").strip():
            why = "缺后果说明 —— **授权的前提是知情**，不说后果等于没问"
        elif a.get("reversible") is None:
            why = "缺可逆性标注"
        elif a.get("scope") == "global" and not (a.get("binds") or {}).get("question_sha256"):
            # global 作用域按**问题**绑定，不按 skill 绑定
            why = "global 作用域缺 question_sha256，无法跨 skill 复用"
        elif a.get("scope") != "global" and (a.get("binds") or {}).get("skill_sha256") != skill_sha:
            # [<日期> 修] 原实现一律要求 binds.skill_sha256 匹配，导致**同一个全局问题
            # 每换一份 skill 就要重问一次**。实证：codex 的 6 道编译期可答闸里有 4 道与
            # browse 完全相同（二者共享 gstack 序幕），Owner 已经答过，却因指纹不符要重答。
            #
            # 正确语义按作用域分：
            #   global  —— 是**用户级偏好**（要不要开遥测、要不要自动升级），
            #               按 question_sha256 绑定，跨 skill 复用；skill 原文改动不影响它
            #   project —— 按 question_sha256 + scope_key 绑定
            #   session —— 按 skill 指纹绑定（原语义）
            # **这不是放松校验**：global 授权仍然绑问题指纹，问题文本一改即失效。
            why = "绑定的 skill 指纹与当前原文不符 —— 授权已过期"
        else:
            # 作用域机械复核：原文含 project 信号却标 global 的，打回
            q = (a.get("source") or {}).get("quote", "") + " " + (a.get("question") or "")
            if a.get("scope") == "global" and any(sig in q for sig in PROJECT_SIGNALS):
                why = "原文含 project 作用域信号却标成 global —— 会导致跨项目误用授权"
        if why:
            rejected.append({"id": aid, "reason": why,
                             "effect": "按 fail-closed 判为未授权，执行期照常停"})
        else:
            ok.append(aid)
    return {"accepted": ok, "rejected": rejected,
            "coverage_note": "被拒的授权一律视为不存在，其对应人闸在执行期照常停"}
