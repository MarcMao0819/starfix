#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选 B · 告警消费闭环（ABGO/TRIRULE-20260817 授权）

## 它解决什么

08-17 效果复盘查出：**告警有产出、没有消费闭环**，处置率一度 7/32——
25 张报出来就没有下文。这是三天里**唯一一条会自我恶化的病**：报得越准，积压越快。

B 不产生新判断，它给已有判断**装出口**。

## 三条路由

| 路由 | 条件 | 去向 |
|---|---|---|
| `ARCHIVE` | 已并线 **且** 登记自洽复核 PASS/N/A | 机器自动归档，不进人视野 |
| `HUMAN_REG` | 复核 FAIL | 留人工·登记病 |
| `HUMAN_PENDING` | 确未并线 且 复核不 FAIL | 留人工·待并线 |
| `HUMAN_UNKNOWN` | 并线状态判不了 且 复核不 FAIL | 留人工·判不了 |

口径不是新定的——`ARCHIVE` 就是主窗口 <日期> 定的
「已并线两种落点一律转自洽性复核，复核 PASS 静默」，B 只是把它做成常设出口。

## 并线状态必须是三态，不能压成两态

第一版路由把它压成「已并线 / 未并线」，结果 48 张里 **19 张全被标成「未并线」**，
而真相是：

- **14 张空锚点** —— 没有锚点就**无从判断**是否并线，不是「未并线」
- **`CS-<日期>-0017`** —— 锚点在 `<项目>ERP` 仓，**那个仓根本没有集成线分支**，
  `merge-base` 命令失败被当成了「未并线」

修正后实测分布：`MERGED 29 · UNKNOWN 19 · NOT_MERGED 0`——**一张「确未并线」都没有**。
把「判不了」混进「未并线」，等于给人一个不存在的行动项（去催他并线）。

> 这个错**测量哨兵没拦住**：取数没坏，是语义压错了。
> 第 13 条管「数字是不是真的」，不管「这个数字是不是我以为的那个意思」。

## 归档不等于判对

`ARCHIVE` 的依据是「**现在自洽**」，**不是「当初判对」**。原判决原样保留在
`机检判决.jsonl`，归档只是撤掉它对人的行动要求。这条与 08-17 D1 批量归档同一口径。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drift_scan as ds          # noqa: E402
import measure_guard as mg       # noqa: E402

REF = os.environ.get("GATE_MERGE_REF", "feat/<项目>-integration")
ALERTDIR = os.environ.get("ALERTDIR", "${FLEET_HOME}/<项目>ERP/迁移备份/回执")
# 与 drift_scan 共用同一个注入口名。**<日期> 第二次同型泄漏**：
# 我下午刚把 B 留痕挪进本模块，`LEDGER` 默认落在这个**写死的** PILOT 上，
# 于是沙箱设了一圈环境变量，B 留痕又从这里直通生产——
# **我在修一个后门的同一天，亲手造了第二个同形状的后门。**
# 这次它在第一次跑就被沙箱自证闸抓住（生产只多了 1 行），不是第 18 条。
# 这正是那条修法要证的：根因不是某个后门，是没人问「我这一跑动了谁」。
PILOT = os.environ.get("TRJ_PILOT_DIR",
                       "${TRAJ_DATA_DIR}/changeset-audit")
# B 消费留痕。**写在执行者身上，不寄存在调用方。**
# <日期> 教训：这本台账原先由旧轮询器的 b_consume 函数写；换成新轮询器后，
# 归档动作照常发生（告警文件里都有批次段），台账却从 08-31 23:02 起再没长过——
# 留痕写在调用方，等于把「这件事发生过」的证据托管给一个随时会被换掉的人。
# 挪到这里之后，证据的生命周期与动作本身绑定，再换几次轮询器也丢不了。
LEDGER = os.environ.get("B_CONSUME_LEDGER", os.path.join(PILOT, "B消费留痕.jsonl"))
SUPERSEDE_LEDGER = os.environ.get("SUPERSEDE_LEDGER",
                                  os.path.join(PILOT, "告警撤回留痕.jsonl"))

# 新判决良性 + A门 PASS 才允许作废旧告警。三元组照抄轮询器原实现，不放宽。
SUPERSEDE_BENIGN = {"PASS", "N/A", "SKIPPED_ALREADY_MERGED"}
SUPERSEDE_MARK = "<!-- AUDITPOLLER:DECISION_SUPERSEDED -->"
SUPERSEDE_HEAD = "## 判决已更新，本告警作废"

MERGED, NOT_MERGED, UNKNOWN = "MERGED", "NOT_MERGED", "UNKNOWN"


# ── 纯判据 ────────────────────────────────────────────────────────────

# 「无需人行动」的终态判决集合。共同点：**它们都不是「审出问题」**——
#   ALERT_MERGED_WITHOUT_AUDIT / SKIPPED_ALREADY_MERGED：已并线，能不能放行已无意义
#   N/A：入口分诊判的「非代码交付」，本来就没有可审的东西
#   PASS：审过且通过
# [修二 <日期>] 初版漏了 N/A 与 PASS，导致 3 张 N/A 单被当成「实质判决」挡在归档外——
# **过度纠正也是错**：把良性终态误判成需人处置，与漏掉真失败是同一枚硬币的两面。
NO_ACTION_VERDICTS = {"ALERT_MERGED_WITHOUT_AUDIT", "SKIPPED_ALREADY_MERGED", "N/A", "PASS"}


def route(merge_state: str, recheck_verdict: str,
          orig_verdict: str = "", aborted: bool = False) -> tuple[str, str]:
    """三态并线 × 复核结论 × **原判** → 路由。纯函数，便于逐分支造正反例。

    [修 <日期>] 第一版只看 (并线, 复核) 就归档，**从不看原判**——
    归档预演当场抓到：`CS-<日期>-0010` 原判 `FAIL`、`aborted=True`，
    但它已并线且登记自洽，于是被路由成 ARCHIVE。
    **登记自洽 ≠ 审计通过。** 08-13 那条静默口径是专门给两种「已并线落点」定的，
    我把它悄悄放宽到了任意判决——那等于让 B 自动消掉真实的审计失败，
    是这个模块最危险的失败模式。
    """
    if aborted:
        return "TRJ_EXEC", "执行器未跑完（aborted）—— 执行器病，回轨迹线，不进业务人工队列"
    if recheck_verdict == "FAIL":
        return "HUMAN_REG", "登记自洽复核 FAIL —— 登记病，必须人判"
    if orig_verdict and orig_verdict not in NO_ACTION_VERDICTS:
        return "HUMAN_SUBSTANTIVE", ("原判 %s 是实质判决，不在 08-13 静默口径内 —— "
                                     "登记自洽不代表审计通过" % orig_verdict)
    if merge_state == MERGED:
        return "ARCHIVE", "已并线 且 原判属无需行动终态 且 复核 %s —— 按 08-13 口径归档" % recheck_verdict
    if merge_state == NOT_MERGED:
        return "HUMAN_PENDING", "确未并线 —— 等并线后自然复审，人只需知情"
    return "HUMAN_UNKNOWN", "并线状态判不了 —— 不得伪装成「待并线」给人假行动项"


# ── 读态薄壳 ──────────────────────────────────────────────────────────

def merge_state(sha: str) -> tuple[str, str]:
    """三态。把「判不了」与「没并线」分开——合并它们会造出不存在的行动项。"""
    if not sha:
        return UNKNOWN, "无锚点，无从判断"
    repo = ds.resolve_repo(sha)
    if repo is None:
        return UNKNOWN, "锚点在已知仓库中解析不开"
    if subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", REF],
                      capture_output=True).returncode != 0:
        return UNKNOWN, "仓库 %s 没有 %s 这条线" % (os.path.basename(repo), REF)
    rc = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", sha, REF],
                        capture_output=True).returncode
    return (MERGED, "已并线") if rc == 0 else (NOT_MERGED, "确未并线")


def last_verdict(cs: str) -> tuple[str, bool]:
    """取该单**最近一次**生产判决与 aborted 标志。台账是 append-only，最后一条为准。"""
    ov, ab = "", False
    path = os.path.join(PILOT, "机检判决.jsonl")
    if not os.path.exists(path):
        return ov, ab
    for line in io.open(path, encoding="utf-8"):
        if '"%s"' % cs not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("changeset_no") != cs:
            continue
        ov = d.get("overall") or d.get("graph") or ov
        ab = bool(d.get("aborted"))
    return ov, ab


def plan(units: list[str]) -> list[dict]:
    st = ds.current_state()
    mg.guard.rowcount("current_state 取数", len(st), mg.FULL_DB_MIN_ROWS)
    out = []
    for cs in units:
        s = st.get(cs)
        if not s:
            out.append({"cs": cs, "route": "HUMAN_UNKNOWN", "why": "查无登记",
                        "merge": UNKNOWN, "recheck": "?"})
            continue
        m, mwhy = merge_state(s["commit_hash"])
        r = ds.recheck_consistency(s["commit_hash"], set(p for p, _ in s["manifest"]),
                                   s.get("change_type", ""))
        ov, ab = last_verdict(cs)
        rt, why = route(m, r["verdict"], ov, ab)
        out.append({"cs": cs, "route": rt, "why": why, "merge": m, "merge_why": mwhy,
                    "recheck": r["verdict"], "recheck_why": r["reason"][:160]})
    mg.guard.not_all_same("路由结果不得全同", [x["route"] for x in out])
    mg.guard.require()
    return out


# ── 归档动作 ──────────────────────────────────────────────────────────

ARCHIVE_SEC = """
## 归档（{ts} · B 告警消费闭环 · 批次 `{rid}`）

- **路由**：`ARCHIVE` —— {why}
- **并线状态**：{mwhy}　·　**登记自洽复核**：`{rv}` —— {rwhy}
- **依据是「现在自洽」，不是「当初判对」**。原判决（overall={orig}）原样保留在
  `机检判决.jsonl`，归档只撤掉它对人的行动要求，不推翻任何结论。
- 口径来源：主窗口 <日期>「已并线 + 复核 PASS 即静默」，B 将其做成常设出口。
- **文件不删**，本段即归档标记。
"""


def supersede(changeset_no: str, source: str, overall: str, gate_verdict: str,
              run_id: str, dry: bool = True) -> dict:
    """判决变好之后，把旧告警标注为作废。**动作与留痕一起住在这里。**

    ## 为什么搬到本模块

    原实现在 `audit-poller.sh` 里。轮询器已经把留痕和动作放在了一起（它的注释写得对：
    证据属于改变告警的那个动作），但**两者同住在一个会被整体替换的层里，只是死得更整齐一点**——
    <日期> 换代时，B 留痕正是这样连人带账一起丢的。
    完整形是：**留痕要与动作同生命周期，而两者都应落在换代时能存活的那一层。**
    `alert_consumer` 管的就是告警生命周期（已管归档终态、管「不得覆盖已有处置段」），
    且这轮换代它一字未改仍照常被调用——它是被证明扛得住换代的那一层。

    ## 判据（照抄原实现，一条不放宽）

    - 新判决良性且 A 门 PASS 才作废；否则一律不动。
    - 告警文件不存在 → 无事可做。
    - 已有作废标记或标题 → 不重复追加。
    - 旧判决本来就良性 → 不作废（它当初不该是告警，作废它等于掩盖一个别的问题）。
    - **只追加不改写**：原正文与此前所有人工处置段全部保留。
    """
    res = {"changeset_no": changeset_no, "acted": False, "why": "", "dry": dry}
    if overall not in SUPERSEDE_BENIGN or gate_verdict != "PASS":
        res["why"] = "新判决非良性或 A 门未过，不作废"
        return res
    fn = os.path.join(ALERTDIR, "机检告警-%s.md" % changeset_no)
    if not os.path.exists(fn):
        res["why"] = "无告警文件，无事可做"
        return res
    txt = io.open(fn, encoding="utf-8", newline="").read()
    if SUPERSEDE_MARK in txt or re.search(r"^%s" % re.escape(SUPERSEDE_HEAD), txt, re.M):
        res["why"] = "已作废过，不重复追加"
        return res
    m = re.search(r"overall=(\S+)", txt.split("\n")[0] if txt else "")
    if not m:
        res["why"] = "首行读不出 overall，**拒绝动它**"
        return res
    old = m.group(1)
    if old in SUPERSEDE_BENIGN:
        res["why"] = "旧判决(%s)本来就良性，不作废" % old
        return res
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    res.update({"old": old, "new": overall, "run_id": run_id})
    if dry:
        res["why"] = "预演：条件满足，真跑会作废"
        return res
    io.open(fn, "a", encoding="utf-8", newline="").write(
        "\n%s（%s · 机器自动）\n\n%s\n"
        "- 旧判决：`%s`　→　**新判决：`%s`**（run `%s`）\n"
        "- 来源：`%s`。新判决已通过 exact 指纹增行成功门，本告警不再需要人处理。\n"
        "> 这是**标注不是删除**：原告警正文与此前所有处置段全部保留。\n"
        "> 撤回依据是「判决变了」，**不是「当初判错了」**。\n"
        % (SUPERSEDE_HEAD, ts, SUPERSEDE_MARK, old, overall, run_id, source))
    io.open(SUPERSEDE_LEDGER, "a", encoding="utf-8", newline="").write(json.dumps(
        {"ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
         "changeset_no": changeset_no, "old": old, "new": overall,
         "run_id": run_id, "action": "SUPERSEDED"},
        ensure_ascii=False, separators=(",", ":")) + "\n")
    res.update({"acted": True, "why": "已标注作废并留痕"})
    return res


def archive(rows: list[dict], dry: bool = True) -> dict:
    todo = [r for r in rows if r["route"] == "ARCHIVE"]
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rid = "BCONSUME-" + hashlib.sha256(
        ("|".join(sorted(r["cs"] for r in todo)) + ts[:10]).encode()).hexdigest()[:8]
    done, skipped = [], []
    for r in todo:
        fn = os.path.join(ALERTDIR, "机检告警-%s.md" % r["cs"])
        if not os.path.exists(fn):
            skipped.append((r["cs"], "无告警文件（判决在台账里，无需归档动作）"))
            continue
        s = io.open(fn, encoding="utf-8", newline="").read()
        # 认**任何**已有处置段，不只认「## 归档（」——0017 等单已被人工确认关闭，
        # 只认自己写的标记会重复追加，把别人的处置结论淹在下面。
        if re.search(r"^## ", s, re.M):
            head = re.search(r"^## (.+)$", s, re.M)
            skipped.append((r["cs"], "已有处置段：%s" % (head.group(1)[:24] if head else "?")))
            continue
        m = re.search(r"overall=(\S+)", s)
        if not dry:
            io.open(fn, "a", encoding="utf-8", newline="").write(ARCHIVE_SEC.format(
                ts=ts, rid=rid, why=r["why"], mwhy=r.get("merge_why", "-"),
                rv=r["recheck"], rwhy=r.get("recheck_why", "-"),
                orig=m.group(1) if m else "?"))
        done.append(r["cs"])
    res = {"run_id": rid, "ts": ts, "archived": done, "skipped": skipped, "dry": dry}
    # 只有真写那一侧才留痕：预演记成归档，台账就成了「打算做的事」而不是「做过的事」。
    # todo 为空表示这批压根没有该归档的单，不值得记一行。
    if not dry and todo:
        io.open(LEDGER, "a", encoding="utf-8", newline="").write(json.dumps(
            {"ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             # 轮询器一次只送一张，保持既有单条形态；批量时置 None，不硬塞第一张冒充全体
             "changeset_no": done[0] if len(done) == 1 else None,
             "b_consume": res}, ensure_ascii=False) + "\n")
    return res


# ── 自检：每条路由正反例（硬约定 5/6/11）──────────────────────────────

CASES = [
    ("ARCHIVE",       "正例·已并线+复核PASS",        (MERGED, "PASS")),
    ("ARCHIVE",       "正例·已并线+复核N/A(免锚点)",  (MERGED, "N/A")),
    ("HUMAN_REG",     "正例·复核FAIL(已并线也不放行)", (MERGED, "FAIL")),
    ("HUMAN_REG",     "正例·复核FAIL+判不了",         (UNKNOWN, "FAIL")),
    ("HUMAN_PENDING", "正例·确未并线+复核PASS",       (NOT_MERGED, "PASS")),
    ("HUMAN_UNKNOWN", "正例·判不了+复核PASS",         (UNKNOWN, "PASS")),
    ("HUMAN_UNKNOWN", "正例·判不了+复核N/A",          (UNKNOWN, "N/A")),
    # 反例形态：FAIL 优先级最高，不得被并线状态盖过
    ("HUMAN_REG",     "反例·确未并线+复核FAIL仍归登记病", (NOT_MERGED, "FAIL")),
]

# [<日期>] 原判维度的逐分支取证 —— 归档预演抓到「登记自洽≠审计通过」后补
CASES_V = [
    ("ARCHIVE",           "正例·原判ALERT_MERGED_WITHOUT_AUDIT", (MERGED, "PASS", "ALERT_MERGED_WITHOUT_AUDIT", False)),
    ("ARCHIVE",           "正例·原判SKIPPED_ALREADY_MERGED",     (MERGED, "PASS", "SKIPPED_ALREADY_MERGED", False)),
    ("HUMAN_SUBSTANTIVE", "**正例·原判FAIL(CS-<日期>-0010形态)不得归档**", (MERGED, "PASS", "FAIL", False)),
    ("HUMAN_SUBSTANTIVE", "正例·原判NEEDS_HUMAN不得归档",        (MERGED, "PASS", "NEEDS_HUMAN", False)),
    ("TRJ_EXEC",          "正例·aborted优先级最高，回轨迹线",      (MERGED, "PASS", "ALERT_MERGED_WITHOUT_AUDIT", True)),
    ("ARCHIVE",           "反例·原判为空(无台账记录)不阻断归档",    (MERGED, "N/A", "", False)),
    ("ARCHIVE",           "反例·原判N/A(非代码交付)是良性终态，应归档", (MERGED, "N/A", "N/A", False)),
    ("ARCHIVE",           "反例·原判PASS 应归档",                 (MERGED, "PASS", "PASS", False)),
    ("HUMAN_SUBSTANTIVE", "正例·原判RUNNER_ERR 不得归档",          (MERGED, "PASS", "RUNNER_ERR", False)),
]


def selftest() -> int:
    ok = True
    hits = collections.Counter()
    for want, name, (m, v) in CASES:
        got, why = route(m, v)
        good = got == want
        ok &= good
        hits[got] += 1
        print("  %-46s → %-14s 期望 %-14s %s" % (name[:46], got, want, "OK" if good else "**不符**"))
    print()
    for want, name, args in CASES_V:
        got, why = route(*args)
        good = got == want
        ok &= good
        hits[got] += 1
        print("  %-46s → %-18s 期望 %-18s %s" % (name[:46], got, want, "OK" if good else "**不符**"))
    print("\n逐路由覆盖（硬约定 11：零命中的分支等于没取证）：")
    for r in ("ARCHIVE", "HUMAN_REG", "HUMAN_PENDING", "HUMAN_UNKNOWN",
              "HUMAN_SUBSTANTIVE", "TRJ_EXEC"):
        n = hits.get(r, 0)
        ok &= n > 0
        print("    %-14s 取证 %d 例  %s" % (r, n, "OK" if n else "**零取证**"))
    # 关键不变式一：复核 FAIL 在任何并线状态下都不许被归档
    for m in (MERGED, NOT_MERGED, UNKNOWN):
        r, _ = route(m, "FAIL")
        good = r != "ARCHIVE"
        ok &= good
        print("    不变式·复核FAIL在 %-11s 下不得归档 → %-18s %s" % (m, r, "OK" if good else "**破**"))
    # 关键不变式二：**实质判决在任何并线/复核组合下都不许被归档**（本次抓到的那个洞）
    for m in (MERGED, NOT_MERGED, UNKNOWN):
        for v in ("PASS", "N/A"):
            for ovd in ("FAIL", "NEEDS_HUMAN", "RUNNER_ERR"):
                r, _ = route(m, v, ovd, False)
                good = r != "ARCHIVE"
                ok &= good
                if not good:
                    print("    **不变式破**：原判%s 在 %s/%s 下被归档" % (ovd, m, v))
    print("    不变式·实质判决(FAIL/NEEDS_HUMAN/RUNNER_ERR)在全部 9 种组合下不得归档  %s"
          % ("OK" if ok else "**破**"))
    # ── 留痕两侧（<日期> 新增）──
    # 判据要答得出「什么情况下它会红」：预演也写＝红；真写却不写＝红。
    # 用临时目录，不碰生产台账。
    import tempfile
    global ALERTDIR, LEDGER
    keep_a, keep_l = ALERTDIR, LEDGER
    tmp = tempfile.mkdtemp()
    ALERTDIR, LEDGER = tmp, os.path.join(tmp, "ledger.jsonl")
    cs = "CS-TEST-LEDGER"
    rows = [{"cs": cs, "route": "ARCHIVE", "why": "自检", "merge_why": "-",
             "recheck": "PASS", "recheck_why": "-"}]

    def fresh():
        io.open(os.path.join(tmp, "机检告警-%s.md" % cs), "w", encoding="utf-8").write(
            "# 机检告警 - %s - overall=ALERT_MERGED_WITHOUT_AUDIT\n- 正文\n" % cs)

    def ledger_lines():
        return len([l for l in io.open(LEDGER, encoding="utf-8")]) if os.path.exists(LEDGER) else 0

    fresh()
    archive(rows, dry=True)
    n_dry = ledger_lines()
    good = (n_dry == 0)
    ok &= good
    print("\n  反例·预演(dry)不得留痕          台账 %d 行（期望 0） %s"
          % (n_dry, "OK" if good else "**不符**"))

    fresh()
    archive(rows, dry=False)
    n_commit = ledger_lines()
    good = (n_commit == 1)
    ok &= good
    print("  正例·真写(--commit)必须留痕      台账 %d 行（期望 1） %s"
          % (n_commit, "OK" if good else "**不符**"))

    # 反例：这一批没有该归档的单（todo 为空）时不许凭空记一行
    archive([{"cs": cs, "route": "HUMAN_REG", "why": "-", "recheck": "PASS"}], dry=False)
    good = (ledger_lines() == 1)
    ok &= good
    print("  反例·无可归档单时不得凭空记账    台账 %d 行（期望仍 1） %s"
          % (ledger_lines(), "OK" if good else "**不符**"))
    ALERTDIR, LEDGER = keep_a, keep_l

    # ── 作废动作八例（<日期> 下沉时新增）──
    # 每条都要答得出「什么情况下它会红」：不作废的六种情形逐条造出来，
    # 而不是只证「能作废」——**只验会作废那一侧的话，一个无条件作废的实现也会全绿。**
    global SUPERSEDE_LEDGER
    ALERTDIR, LEDGER = tmp, os.path.join(tmp, "ledger2.jsonl")
    SUPERSEDE_LEDGER = os.path.join(tmp, "supersede.jsonl")

    def mk(no, head):
        io.open(os.path.join(tmp, "机检告警-%s.md" % no), "w", encoding="utf-8").write(head)

    def led():
        return len(io.open(SUPERSEDE_LEDGER, encoding="utf-8").readlines()) \
            if os.path.exists(SUPERSEDE_LEDGER) else 0

    def sup(name, no, head, overall, gate, want_acted, want_led_delta, dry=False):
        nonlocal_ok = True
        if head is not None:
            mk(no, head)
        b = led()
        r = supersede(no, "TEST", overall, gate, "runid1", dry=dry)
        d = led() - b
        good = (r["acted"] == want_acted) and (d == want_led_delta)
        print("  %-40s acted=%-5s 台账+%d  %s  %s"
              % (name[:40], r["acted"], d, "OK" if good else "**不符**", r["why"][:26]))
        return good

    A = "# 机检告警 - %s - overall=ALERT_MERGED_WITHOUT_AUDIT\n- 正文\n"
    ok &= sup("正例·旧ALERT+新PASS+A门PASS → 作废并留痕", "CS-S-1", A % "CS-S-1", "PASS", "PASS", True, 1)
    ok &= sup("反例·同一张再来一次 → 不重复追加", "CS-S-1", None, "PASS", "PASS", False, 0)
    ok &= sup("反例·新判决 FAIL → 不作废", "CS-S-2", A % "CS-S-2", "FAIL", "PASS", False, 0)
    ok &= sup("反例·A门 HOLD → 不作废", "CS-S-3", A % "CS-S-3", "PASS", "HOLD", False, 0)
    ok &= sup("反例·旧判决本就良性 → 不作废", "CS-S-4",
              "# 机检告警 - CS-S-4 - overall=PASS\n", "PASS", "PASS", False, 0)
    ok &= sup("反例·首行无 overall → 拒绝动它", "CS-S-5", "# 没有判决字段\n", "PASS", "PASS", False, 0)
    ok &= sup("反例·无告警文件 → 不动不报错", "CS-S-NONE", None, "PASS", "PASS", False, 0)
    ok &= sup("反例·预演不落任何字节", "CS-S-6", A % "CS-S-6", "PASS", "PASS", False, 0, dry=True)
    # 人工处置段必须原样保留——作废是追加，不是覆盖
    mk("CS-S-7", A % "CS-S-7" + "\n## 人工确认放行（Owner）\n- 我看过了\n")
    supersede("CS-S-7", "TEST", "PASS", "PASS", "runid2", dry=False)
    t = io.open(os.path.join(tmp, "机检告警-CS-S-7.md"), encoding="utf-8").read()
    good = ("## 人工确认放行（Owner）" in t) and (SUPERSEDE_MARK in t)
    ok &= good
    print("  %-40s %s" % ("正例·人工处置段必须原样保留", "OK" if good else "**被覆盖了**"))

    print("\n自检：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="告警消费闭环（B）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--plan", help="单号清单文件（每行一个），输出路由计划")
    ap.add_argument("--archive", help="同 --plan，但执行归档")
    ap.add_argument("--commit", action="store_true", help="与 --archive 连用：真写入（默认只预演）")
    ap.add_argument("--supersede", metavar="CS", help="判决变好后作废旧告警")
    ap.add_argument("--overall", default="", help="与 --supersede 连用：新判决")
    ap.add_argument("--gate", default="", help="与 --supersede 连用：A门判定")
    ap.add_argument("--run-id", default="", help="与 --supersede 连用：新判决 run_id")
    ap.add_argument("--source", default="", help="与 --supersede 连用：来源通路")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.supersede:
        r = supersede(a.supersede, a.source, a.overall, a.gate, a.run_id, dry=not a.commit)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    src = a.plan or a.archive
    if not src:
        ap.error("需要 --plan 或 --archive")
    units = [l.strip() for l in io.open(src, encoding="utf-8") if l.strip()]
    rows = plan(units)
    if a.plan:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    res = archive(rows, dry=not a.commit)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
