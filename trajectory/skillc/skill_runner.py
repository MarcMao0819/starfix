#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill workflow 执行器：按守卫执行编译产物，**模型只收结果**。

## 为什么有这个文件（对拍实验的直接产物）

<日期> 第一次对拍：功能等价成立（两条路径的截图 sha256 逐字节相同），但
**token 一分钱没省**（103,052 vs 101,771，工具调用还多 69%）。根因不在编译本身，
在我把 212 行命令原文**当文本喂给了模型让它执行**——模型仍在循环里，该推理的一样
没少，还多了排错。

> 结论：**要省钱，编译产物就不能交给模型；必须由运行时直接执行，只把结果报给模型。**

本文件就是那个运行时。

## 与生产运行器的关系：复用，不新造，不改它一个字节

按裁定②「运行时直接执行的架构不要新造」，本执行器 **import** 生产运行器
`runner/run_graph.py` 的既有原语（只读守卫 `check_readonly`、留痕库路径、时间戳），
**不修改该文件**（它正被轮询器调用，硬约定 10：动共用件必须副本改+原子换；这里的
最优解是根本不动）。

## 控制流：对拍实证补上的那一半

第一次对拍的图错是**块间控制流被拍平**：browse 的 SETUP 段里 n10/n11 是 if/else
——n10 回显 `READY` 就意味着不必执行 n11 的装 bun 块。拍平成顺序后，B 组无条件跑了
那个有害块，装 bun 失败后连 goto 都炸了。

更严重的是守卫底下第一步是「Tell the user … Then STOP and wait」——**一道人闸**。
拍平执行不但多跑了块，还跨过了要求停下等用户批准的关卡。

所以本执行器的三条硬规矩：

1. **守卫为假即跳过**，不执行、不报错，记 `SKIPPED_BY_GUARD`
2. **命中人闸即停**，整条流程挂起等人，绝不自动放行（比多跑一个块严重得多）
3. **同一个 shell 会话贯穿全流程**——skill 原文假设连续 shell（`$B`、`_SESSION_ID`
   这些变量跨块使用）。对拍实证：拆成独立子进程会让遥测块写入垃圾数据，而且**读
   原文的 A 组同样中招**，说明这是 skill 自身的既存缺陷；执行器用单会话把它修好。

## 输出契约：模型只收结果

`run()` 返回一份**摘要**（每块的状态与关键输出行），不返回命令原文。这是省 token
的全部来源——序幕那 281 行命令一个字都不进模型上下文。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runner"))
try:
    from run_graph import check_readonly, now_iso     # 复用生产原语
except Exception:                                     # 生产文件不可用时不阻塞，但要显式降级
    check_readonly = None
    from datetime import datetime, timezone

    def now_iso():
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

SKIPPED = "SKIPPED_BY_GUARD"
HALTED = "HALTED_AT_HUMAN_GATE"


class ShellSession:
    """一个贯穿全流程的 shell 会话。

    对拍实证：skill 假设连续 shell（`$B` 在 SETUP 块定义、在后续块使用；遥测块要复用
    preamble 的 `_SESSION_ID`/`_TEL`）。拆成独立子进程会让这些变量全部丢失——遥测块
    因此**表面成功、实际写入垃圾数据**（`_TEL_DUR` 算出约 17.8 亿秒）。
    **读原文的 A 组同样中招**，所以这是 skill 的既存缺陷，不是编译引入的。

    做法：把每块写进同一个脚本文件累积执行，用哨兵行分隔输出。简单、可审计、
    不依赖交互式 pty。
    """

    def __init__(self, cwd: str | None = None):
        self.cwd = cwd or os.getcwd()
        self.prelude: list[str] = []          # 已成功执行过的块，作为后续块的前缀

    def run(self, cmd: str, timeout: int = 180) -> tuple[int, str, str]:
        script = "\n".join(self.prelude + [cmd])
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            f.write(script)
            path = f.name
        try:
            p = subprocess.run(["/bin/bash", path], capture_output=True, text=True,
                               cwd=self.cwd, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "TIMEOUT after %ds" % timeout
        finally:
            os.unlink(path)

    def commit(self, cmd: str) -> None:
        """把一块并入会话前缀，使其定义的变量对后续块可见。"""
        self.prelude.append(cmd)


NOT_CMD = "NOT_A_COMMAND"


def parses_as_shell(cmd: str) -> tuple[bool, str]:
    """`bash -n` 语法预检：这块到底是不是命令。

    真数据实证（codex，<日期>）：26 个必跑块里 7 个失败，**其中 4 个根本不是命令**
    ——`D<N> — <one-line question title>` 是决策简报格式规范、`WIP: <描述>` 是提交信息
    模板、`A) Review the diff...` 是 AskUserQuestion 选项模板、`# Chosen mode: ...` 是
    纯注释块。它们只是恰好被写在 ``` 围栏里。browse 上只咬了 2 次，codex 咬 4 次。

    **真正的风险不是失败，是万一某段散文恰好是合法 shell 就会被执行。** 语法预检是
    「真跑过才算数」最安全的形式：只解析、不执行，不猜内容语义，也不需要任何关于
    「命令长什么样」的假设。

    不解析即判 NOT_A_COMMAND 并跳过——**跳过是可补的，误执行是不可逆的**（与守卫
    求值「未知令牌一律判不成立」同源）。
    """
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                     encoding="utf-8", newline="\n") as f:
        f.write(cmd)
        path = f.name
    try:
        p = subprocess.run(["/bin/bash", "-n", path], capture_output=True, text=True)
        return p.returncode == 0, (p.stderr or "").strip().split("\n")[0][:160]
    finally:
        os.unlink(path)


COND_RE = re.compile(r"[`‘“]([A-Za-z_][A-Za-z0-9_]{1,40})[`’”]\s*(?:is|==|=|为)\s*[`‘“]([^`’”]{1,30})[`’”]")
KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]{1,40})\s*:\s*(\S.*?)\s*$")


def parse_conditions(raw: str) -> list[tuple[str, str]]:
    """从模型标注的条件原文里解析「键=期望值」合取式。

    实证（browse 行155）：条件是 `TEL_PROMPTED` is `no` AND `LAKE_INTRO` is `yes`，
    而实测环境 LAKE_INTRO=no ⇒ **条件为假、该闸不该触发**。原求值只看令牌出没出现，
    于是误停——**方向是安全的（过度停），但让编译产物失去价值**。

    模型已经把条件原文逐字留在 guard.raw 里，直接机械解析即可，不必再叫模型。
    解析不出来就退回「令牌出现即成立」的老语义——那是过度停的方向，安全。
    """
    return COND_RE.findall(raw or "")


def output_kv(text: str) -> dict:
    """从块输出里抽 `KEY: value` 形态的键值对（skill 的状态回显惯例）。"""
    kv = {}
    for l in (text or "").split("\n"):
        m = KV_RE.match(l)
        if m:
            kv.setdefault(m.group(1), m.group(2).strip())
    return kv


def eval_guards(guards: list[str], emitted: set[str],
                conds: list[tuple[str, str]] | None = None,
                kv: dict | None = None) -> tuple[bool, str]:
    """守卫求值：全部成立才执行（AND）。

    守卫名与前序分支块回显的令牌比对。**未知令牌一律判不成立**——宁可跳过也不
    误执行，这与「跨过人闸比多跑一块严重」同源：执行是不可逆的，跳过是可补的。
    """
    # 有可解析的条件时按「键=期望值」求值，比令牌出现精确
    if conds:
        kv = kv or {}
        for k, want in conds:
            got = kv.get(k)
            if got is None:
                return False, "条件键 %r 未在输出中出现" % k
            if got.strip().strip("`").lower() != want.strip().lower():
                return False, "条件 %s=%s 不等于期望 %s" % (k, got, want)
        return True, "全部条件成立（%s）" % "; ".join("%s=%s" % c for c in conds)
    for g in guards:
        key = g.strip()
        if key not in emitted:
            return False, "守卫 %r 未成立（已回显令牌: %s）" % (
                key, ", ".join(sorted(emitted)) or "无")
    return True, "全部守卫成立" if guards else "无守卫"


def run_blocks(blocks: list[dict], cwd: str | None = None,
               allow_human_gate: bool = False) -> dict:
    """按序执行，守卫为假跳过，人闸即停。返回给模型的**只有摘要**。"""
    sess = ShellSession(cwd)
    emitted: set[str] = set()
    kvpool: dict = {}
    denied_region = False
    results = []
    halted = None

    for i, b in enumerate(blocks):
        bid = "b%d" % (i + 1)
        if b["kind"] == "human_gate":
            # v2：编译期已取得的授权直接消解该闸——授权在编译期一次性拿到，
            # 执行期零**常规**人闸。未被授权覆盖的形态仍照常停（fail-closed 兜底不撤）。
            auth = (b.get("authorization") or {})
            ans = auth.get("answer")
            if ans == "no":
                # [<日期> 安全修复] 原实现把 yes 与 no **一视同仁地消解掉该闸**，
                # 于是「用户拒绝」被当成「已授权、继续」——后续实现该动作的块照跑。
                # 实测确有越权：Owner 答「artifacts 同步不授权」，紧随的
                # `gstack-brain-sync --discover-new` 仍以 OK 执行。**没造成实际损害
                # 只因该工具自己有开关兜住——那是运气，不是设计。**
                #
                # 修法：拒绝即进入「被拒区」，其后的块一律跳过直到下一道闸；唯一例外是
                # 原文显式标了 `Always run (regardless of choice)` 的块——**那是原文
                # 自己给出的判据，不是我猜的**。
                denied_region = True
                results.append({"id": bid, "kind": "human_gate",
                                "status": "DECLINED_AT_COMPILE",
                                "why": "编译期用户拒绝（%s=no，应答人 %s）；其后非 Always-run 块一律跳过"
                                       % (auth.get("id"), (auth.get("answered_by") or {}).get("id"))})
                continue
            if ans == "yes":
                denied_region = False
                results.append({"id": bid, "kind": "human_gate",
                                "status": "AUTHORIZED_AT_COMPILE",
                                "why": "编译期已授权（%s=yes，应答人 %s）"
                                       % (auth.get("id"), (auth.get("answered_by") or {}).get("id"))})
                continue
            ok, why = eval_guards(b.get("guards", []), emitted,
                                  parse_conditions(b.get("raw_cond", "")), kvpool)
            if not ok:
                results.append({"id": bid, "kind": "human_gate", "status": SKIPPED,
                                "why": why})
                continue
            results.append({"id": bid, "kind": "human_gate", "status": HALTED,
                            "text": b["text"], "why": "命中人闸，须人工放行"})
            if not allow_human_gate:
                halted = bid
                break
            continue

        if denied_region and not b.get("always_run"):
            results.append({"id": bid, "kind": b["kind"], "status": "DENIED_BY_COMPILE_ANSWER",
                            "why": "前一道闸被用户拒绝，且本块未标 Always run"})
            continue

        # 普通块的守卫也走条件求值（原来只有人闸走，属遗漏）
        ok, why = eval_guards(b.get("guards", []), emitted,
                              parse_conditions(b.get("raw_cond", "")), kvpool)
        if not ok:
            results.append({"id": bid, "kind": b["kind"], "status": SKIPPED, "why": why})
            continue

        cmd = b["cmd"]
        parses, perr = parses_as_shell(cmd)
        if not parses:
            results.append({"id": bid, "kind": b["kind"], "status": NOT_CMD,
                            "why": "bash -n 不解析，判定该围栏不是命令：" + perr})
            continue
        if check_readonly is not None and b.get("readonly_required"):
            allowed, reason = check_readonly(cmd)
            if not allowed:
                results.append({"id": bid, "kind": b["kind"], "status": "GUARD_BLOCKED",
                                "why": reason})
                continue

        t0 = time.time()
        rc, out, err = sess.run(cmd)
        dur = int((time.time() - t0) * 1000)
        if rc == 0:
            sess.commit(cmd)          # 只有成功的块才并入会话，避免污染后续
        # 回显令牌入池，供后续守卫求值
        for tok in b.get("emits", []):
            if tok in out:
                emitted.add(tok)
        kvpool.update(output_kv(out))
        tail = [l for l in (out or "").strip().split("\n") if l.strip()][-3:]
        results.append({"id": bid, "kind": b["kind"], "status": "OK" if rc == 0 else "FAILED",
                        "rc": rc, "ms": dur, "out_tail": tail,
                        "err_tail": [l for l in (err or "").strip().split("\n") if l.strip()][-2:],
                        "emitted": sorted(t for t in b.get("emits", []) if t in out)})

    # 【必备字段】未覆盖清单（<日期> 裁定升格）。
    # 起因：codex 摘要停在 b9，Step 0.4/0.5/0.6 的产出从未进入摘要，乙窗看到的是一份
    # **看起来完整、实则截断**的状态，于是填空而不是回去读原文——三答案两处偏离正典。
    # **截断的摘要比没有摘要更危险**：没有摘要模型会去读原文，有一份权威模样的残缺摘要
    # 模型会自创。所以产物必须机械声明自己没覆盖什么。
    # 这是**机械字段不是解释性文字**，不违反防作者偏差要求。
    done_ids = {r["id"] for r in results}
    uncovered = []
    for i, b in enumerate(blocks):
        bid = "b%d" % (i + 1)
        if bid in done_ids:
            continue
        uncovered.append({"id": bid, "kind": b.get("kind"),
                          "line": b.get("line") or b.get("line_start")})

    return {
        "ts": now_iso(),
        "blocks_total": len(blocks),
        "uncovered": uncovered,
        "uncovered_count": len(uncovered),
        "coverage_warning": ("执行未走完：以下必跑项未执行，消费方必须自行完成或停下问人"
                             if uncovered else None),
        "executed": sum(1 for r in results if r["status"] == "OK"),
        "failed": sum(1 for r in results if r["status"] == "FAILED"),
        "skipped_by_guard": sum(1 for r in results if r["status"] == SKIPPED),
        "not_a_command": sum(1 for r in results if r["status"] == NOT_CMD),
        "authorized_at_compile": sum(1 for r in results if r["status"] == "AUTHORIZED_AT_COMPILE"),
        "declined_at_compile": sum(1 for r in results if r["status"] == "DECLINED_AT_COMPILE"),
        "denied_blocks": sum(1 for r in results if r["status"] == "DENIED_BY_COMPILE_ANSWER"),
        "halted_at": halted,
        "emitted_tokens": sorted(emitted),
        "results": results,
    }




# ---------------------------------------------------------------------------
# 金丝雀：每次执行落对拍可比日志，分歧即自动退 active（<日期> 裁定的三重保险②③）
# ---------------------------------------------------------------------------
#
# 主窗口放行 browse 进 active 时随行三条保险：
#   ① 漂移自动退 invalidated、原文即权威（已由 skill_graph.apply_update 实现）
#   ② 首周金丝雀：每次编译执行落**对拍可比日志**，任何分歧自动退 active 并直投主窗口
#   ③ 执行日志保留可回放
#
# 「分歧」的判据必须写死，否则金丝雀会变成天天误报的警报器：
#   **比**  块级状态序列 [(块号, 类型, 状态)] + 守卫决策（回显令牌集合）
#   **不比** 耗时、输出正文、失败块的错误文本——这些本来就会变，比了必然天天报
#
# 判据只盯「哪些块跑了、哪些被守卫挡了、分支往哪边走了」。这三样一变，就意味着
# 编译产物的行为变了，必须退回原文。

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def behavior_key(result: dict) -> dict:
    """从执行结果里抽出**可比的行为指纹**（刻意不含时间与正文）。"""
    return {
        "sequence": [[r["id"], r["kind"], r["status"]] for r in result["results"]],
        "emitted_tokens": result["emitted_tokens"],
        "halted_at": result["halted_at"],
    }


def canary_check(skill: str, result: dict, baseline_path: str | None = None) -> dict:
    """与基线比对。**首次执行即写基线**；此后每次比对，不一致即判分歧。"""
    os.makedirs(os.path.join(LOG_DIR, skill), exist_ok=True)
    base_p = baseline_path or os.path.join(LOG_DIR, skill, "baseline.json")
    key = behavior_key(result)

    if not os.path.exists(base_p):
        with open(base_p, "w", encoding="utf-8", newline="") as f:
            json.dump({"ts": result["ts"], "key": key}, f, ensure_ascii=False, indent=1)
        return {"verdict": "BASELINE_WRITTEN", "diffs": []}

    base = json.load(open(base_p, encoding="utf-8"))["key"]
    diffs = []
    if base["sequence"] != key["sequence"]:
        bs = {tuple(x[:2]): x[2] for x in base["sequence"]}
        ks = {tuple(x[:2]): x[2] for x in key["sequence"]}
        for k in sorted(set(bs) | set(ks)):
            if bs.get(k) != ks.get(k):
                diffs.append("块 %s(%s) 状态 %s → %s" % (k[0], k[1], bs.get(k, "无"), ks.get(k, "无")))
    if base["emitted_tokens"] != key["emitted_tokens"]:
        diffs.append("守卫决策变了：回显令牌 %s → %s"
                     % (base["emitted_tokens"], key["emitted_tokens"]))
    if base["halted_at"] != key["halted_at"]:
        diffs.append("人闸停顿点变了：%s → %s" % (base["halted_at"], key["halted_at"]))
    return {"verdict": "DIVERGED" if diffs else "MATCH", "diffs": diffs}


def log_run(skill: str, result: dict, canary: dict) -> str:
    """执行日志落盘（保险③：保留可回放）。含全部块级结果，不做裁剪。"""
    os.makedirs(os.path.join(LOG_DIR, skill), exist_ok=True)
    # 用结果里的时间戳做文件名，避免引入 Date.now 之类的不可复现来源
    stamp = result["ts"].replace(":", "").replace("-", "").replace("+", "_")
    p = os.path.join(LOG_DIR, skill, "run-%s.json" % stamp)
    with open(p, "w", encoding="utf-8", newline="") as f:
        json.dump({"skill": skill, "result": result, "canary": canary},
                  f, ensure_ascii=False, indent=1)
    return p


def demote_to_invalidated(graph_path: str, reason: str) -> bool:
    """分歧即自动退 active。**退回后原文重新成为权威**，最坏退化成今天的行为。"""
    if not os.path.exists(graph_path):
        return False
    g = json.load(open(graph_path, encoding="utf-8"))
    if g.get("state") != "active":
        return False
    g["state"] = "invalidated"
    g.setdefault("revision_log", []).append({
        "ts": now_iso(), "event": "CANARY_DIVERGED_DEMOTED", "reason": reason,
        "note": "金丝雀比对出分歧，已自动退 active；原文重新成为权威，须人工复核后才可重新放行",
    })
    with open(graph_path, "w", encoding="utf-8", newline="") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
#
# [<日期> 修] 入口必须放在文件**最末**。此前金丝雀函数被追加在
# `if __name__ == "__main__"` 之后——Python 按顺序执行顶层语句，脚本方式运行时
# 入口会在那些函数定义之前就执行完，一旦 main() 用到它们就是 NameError。
# 当时没炸只因 main() 还没用到，属于「现在能跑、接线即炸」的雷。

def main() -> int:
    ap = argparse.ArgumentParser(description="skill workflow 执行器（模型只收结果）")
    ap.add_argument("--blocks", required=True, help="extract_blocks 产出的 JSON")
    ap.add_argument("--cwd")
    ap.add_argument("--allow-human-gate", action="store_true",
                    help="人闸放行（仅供测试；正式使用一律停下等人）")
    ap.add_argument("--skill", help="skill 名；给了就落金丝雀日志并比对基线")
    ap.add_argument("--graph", help="编译产物路径；金丝雀判分歧时自动退 active")
    a = ap.parse_args()
    blocks = json.load(open(a.blocks, encoding="utf-8"))
    r = run_blocks(blocks, a.cwd, a.allow_human_gate)

    canary = {"verdict": "NOT_CHECKED", "diffs": []}
    if a.skill:
        canary = canary_check(a.skill, r)
        log = log_run(a.skill, r, canary)
        r["canary"] = canary
        r["log"] = log
        if canary["verdict"] == "DIVERGED" and a.graph:
            demoted = demote_to_invalidated(a.graph, "; ".join(canary["diffs"])[:400])
            r["demoted_to_invalidated"] = demoted

    print(json.dumps(r, ensure_ascii=False, indent=1))
    if canary["verdict"] == "DIVERGED":
        return 2
    return 0 if r["halted_at"] is None and r["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# 第四关常设判据：授权极性断言（<日期> 主窗口裁定，随 browse 放行生效）
# ---------------------------------------------------------------------------
#
# 起因：执行器曾把「用户拒绝」当成「已授权」，被拒的 artifacts 同步块照跑。
# 当时我只对出事的那一条（b15）做了验证——**主窗口裁定：每条授权记录都要做，
# 不是只做出过事的那条**。
#
# 双极性断言（缺一不可）：
#   DENIED  → 该闸关联的后续块**必不执行**，且标了 Always run 的块**必照跑**
#   GRANTED → 该闸关联的后续块**必执行**
#
# 只验一个方向不够：只验「拒绝时没跑」查不出「同意时也没跑」（那会让授权形同虚设）；
# 只验「同意时跑了」查不出越权。这与本线一贯的「只验期望方向不验反向」同源。

def authorization_polarity_assert(blocks: list[dict], result: dict) -> dict:
    """对每条授权记录做双极性断言。返回逐条结论，任一条不过即整关不过。"""
    st = {r["id"]: r["status"] for r in result["results"]}
    order = [("b%d" % (i + 1), b) for i, b in enumerate(blocks)]
    findings, failed = [], []

    for idx, (bid, b) in enumerate(order):
        if b.get("kind") != "human_gate":
            continue
        auth = b.get("authorization") or {}
        ans = auth.get("answer")
        if ans not in ("yes", "no"):
            continue
        # 该闸「关联的后续块」= 到下一道闸为止
        region = []
        for j in range(idx + 1, len(order)):
            if order[j][1].get("kind") == "human_gate":
                break
            region.append(order[j])

        always = [(i, x) for i, x in region if x.get("always_run")]
        normal = [(i, x) for i, x in region if not x.get("always_run")]

        if ans == "no":
            bad_ran = [i for i, _ in normal if st.get(i) not in
                       ("DENIED_BY_COMPILE_ANSWER", "SKIPPED_BY_GUARD", "NOT_A_COMMAND")]
            bad_skipped = [i for i, _ in always if st.get(i) == "DENIED_BY_COMPILE_ANSWER"]
            ok = not bad_ran and not bad_skipped
            findings.append({
                "gate": bid, "auth": auth.get("id"), "polarity": "DENIED",
                "region": [i for i, _ in region], "always_run": [i for i, _ in always],
                "verdict": "PASS" if ok else "FAIL",
                "detail": ("被拒后仍执行的块: %s" % bad_ran if bad_ran else "") +
                          ("; always-run 块被误拒: %s" % bad_skipped if bad_skipped else "")
                          or "被拒区无越权执行，always-run 照跑",
            })
        else:
            bad = [i for i, _ in normal if st.get(i) == "DENIED_BY_COMPILE_ANSWER"]
            ok = not bad
            findings.append({
                "gate": bid, "auth": auth.get("id"), "polarity": "GRANTED",
                "region": [i for i, _ in region],
                "verdict": "PASS" if ok else "FAIL",
                "detail": ("已授权却被当作被拒跳过的块: %s" % bad) if bad
                          else "已授权，关联块未被误拒",
            })
        if findings[-1]["verdict"] == "FAIL":
            failed.append(bid)

    return {"assertions": findings, "failed_gates": failed,
            "gate4_polarity": "PASS" if not failed else "FAIL"}
