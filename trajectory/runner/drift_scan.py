#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审后漂移扫描：判决绑锚点指纹，登记一变旧判决即失效并重排队。

问题来源（<日期> 漏报抽样报告 §五/§六）：轮询器只取 `id > 游标` 的新单，
**从不回看已判的单**。于是「机器审完之后登记被改」这件事完全不可见——89 单里
10 单发生过（锚点变 7 单、锚点+明细同变 3 单）。机器当时的判决对它当时看到的
内容是正确的，问题是**判决审完就失效了，而失效这件事没人知道**。

典型形态（CS-<日期>-0054 实证）：轮询判 DEFER（登记 4 文件、git 只有 2）→
有人把 2 行明细软删、file_count 改成 2 → 复跑即 PASS。无论动机是否正当（多数
是正当的返工重登），**改完没有重审**这件事本身就是缺口。

## 指纹口径
fingerprint = sha256( commit_hash + "\\n" + 每行 "<file_path>\\t<change_action>" 排序后 )
明细一律过滤 `is_del=0`——与图 n4/n7/n12 的取数口径逐字一致（图里本来就带这个
条件，是我 08-13 普查时漏了，导致当时把 0054 的明细漂移少算了一档）。

## 两个分支必须各自取证（硬约定 11）
失效判据含「或」：**锚点变** 或 **明细变**。只要一个分支能兜住结果，另一个是死是活
从整体判决上看不出来。故 --selftest 用受控样本分别单独触发两个分支，并附一个
两者都不变的样本反验不误报。

## 留痕
- 台账 `判决指纹.jsonl`：每次判决落一行（changeset_no / commit_hash / fingerprint / overall / run_id / ts）
- 失效 `判决失效.jsonl`：漂移一行（旧锚点→新锚点、旧指纹→新指纹、变化类型、检出时间）
  **绝不静默覆盖台账**——台账追加写，历史条目原样保留。

## 用法
    drift_scan.py --scan                 扫描并输出需重排队的单号（每行一个），失效留痕自动落盘
    drift_scan.py --record <no> --overall <v> --run-id <id>   判完记指纹
    drift_scan.py --backfill             用机器留痕里「判决当时的锚点」种子化台账（仅首次）
    drift_scan.py --selftest             逐分支取证（不碰真台账）

只用 Python 3 标准库。对数据库只跑 SELECT。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

# ── 路径全部可注入。**<日期> 事故：这里原本是五个写死的常量。** ──
# 轮询器把「指纹落账」委托给本模块（`--record`），而本模块不看轮询器的
# AUDITPOLLER_PILOT。于是别人设了一整圈沙箱环境变量，唯独指纹台账从这个后门
# 直通生产——我连着往生产 `判决指纹.jsonl` 写了 18 条才发现。
# 更该记的是：我嘴上说了一整天「全隔离沙箱」，**从没验证过它真的隔离**。
# 一个从没被证伪过的前提，和一条从没红过的判据，是同一种东西。
PILOT_DIR = os.environ.get("TRJ_PILOT_DIR",
                           "${TRAJ_DATA_DIR}/changeset-audit")
LEDGER = os.environ.get("TRJ_LEDGER", os.path.join(PILOT_DIR, "判决指纹.jsonl"))
INVALID = os.environ.get("TRJ_INVALID", os.path.join(PILOT_DIR, "判决失效.jsonl"))
EXCLUDE = os.environ.get("TRJ_EXCLUDE", os.path.join(PILOT_DIR, ".drift_exclude"))
RUNTIME_DB = os.environ.get("TRJ_DB_PATH", "${TRAJ_HOME}/runtime.db")
DOCKER = "/usr/local/bin/docker"
CONTAINER = "${DB_CONTAINER}"
DB = "${DB_NAME}"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def mysql(sql: str) -> list[list[str]]:
    """只读查询。口令留在容器环境变量里，不出现在命令行。"""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise RuntimeError("drift_scan 只允许 SELECT：" + sql[:60])
    inner = ('mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
             + DB + " -N -B -e " + json.dumps(sql))
    r = subprocess.run([DOCKER, "exec", CONTAINER, "sh", "-c", inner],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        if not line or line.startswith("mysql:"):
            continue
        out.append(line.split("\t"))
    return out


def fingerprint(commit_hash: str, manifest: list[tuple[str, str]]) -> str:
    body = commit_hash + "\n" + "\n".join(
        "%s\t%s" % (p, a) for p, a in sorted(manifest))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def current_state() -> dict[str, dict]:
    """现库状态：每单的锚点 + 明细（过滤软删，与图取数口径一致）。"""
    rows = mysql(
        "SELECT s.changeset_no, IFNULL(s.commit_hash,''), IFNULL(f.file_path,''), "
        "IFNULL(f.change_action,''), IFNULL(s.change_type,'') FROM t_code_changeset s "
        "LEFT JOIN t_code_change_file f ON f.changeset_id=s.id AND f.is_del=0 "
        "ORDER BY s.changeset_no, f.file_path")
    st: dict[str, dict] = {}
    for r in rows:
        if len(r) < 4:
            continue
        no, sha, path, act = r[0], r[1], r[2], r[3]
        ctype = r[4] if len(r) > 4 else ""
        d = st.setdefault(no, {"commit_hash": sha, "manifest": [], "change_type": ctype})
        if path:
            d["manifest"].append((path, act))
    for no, d in st.items():
        d["fingerprint"] = fingerprint(d["commit_hash"], d["manifest"])
        d["file_count"] = len(d["manifest"])
    return st


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def append_jsonl(path: str, obj: dict) -> None:
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def latest_ledger() -> dict[str, dict]:
    """台账是追加写的，同一单可能多条；取每单最后一条为当前基准。"""
    cur: dict[str, dict] = {}
    for e in read_jsonl(LEDGER):
        no = e.get("changeset_no")
        if no:
            cur[no] = e
    return cur


def load_exclude() -> dict[str, str]:
    """排除名单：`<changeset_no>  # 理由`，命中者记失效但不重排队。"""
    ex: dict[str, str] = {}
    if not os.path.exists(EXCLUDE):
        return ex
    with open(EXCLUDE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("#", 1)
            ex[parts[0].strip()] = parts[1].strip() if len(parts) > 1 else ""
    return ex


def scan(emit: bool = True, dry_run: bool = False) -> list[str]:
    """比对台账与现库，落失效留痕，返回需重排队的单号。

    `dry_run=True` 只看不写。**这个开关是必须的**：--scan 有副作用（写失效台账）却
    长得像个查看命令，我自己就手工跑了一次用来"看看有哪些"，结果轮询器随后又检出
    同一批，失效台账里出现 17 单 ×2 = 34 行重复记录（记录本身没错，是同一处漂移被
    两个执行方各记一次）。想看就用 --dry-run。
    """
    base = latest_ledger()
    st = current_state()
    ex = load_exclude()
    requeue = []
    for no, cur in sorted(st.items()):
        old = base.get(no)
        if not old:
            continue                      # 从未判过，走轮询器的新单通路，不归本扫描管

        # 回填条目没有指纹（历史明细无从还原），必须退化成「锚点 + file_count」比对。
        # [<日期> 修] 原实现只比指纹，回填条目的空指纹与任何真指纹都不等，
        # 首次扫描把 120 单全报成漂移——真名单只有 10 单。这是「判据对空值的行为
        # 没验过」的老毛病：文档里写了退化口径，代码里没实现。
        old_fp = old.get("fingerprint") or ""
        anchor_changed = old.get("commit_hash") != cur["commit_hash"]
        if not old_fp:
            count_changed = old.get("file_count") != cur["file_count"]
            if not anchor_changed and not count_changed:
                continue
            kind = (["锚点"] if anchor_changed else []) + (["明细"] if count_changed else [])
        else:
            if old_fp == cur["fingerprint"]:
                continue
            kind = []
            if anchor_changed:
                kind.append("锚点")
            if old.get("file_count") != cur["file_count"] or not anchor_changed:
                kind.append("明细")
        rec = {
            "ts": now_iso(),
            "changeset_no": no,
            "event": "VERDICT_INVALIDATED",
            "change_kind": "+".join(kind) or "未知",
            "old_commit_hash": old.get("commit_hash", ""),
            "new_commit_hash": cur["commit_hash"],
            "old_fingerprint": old.get("fingerprint", ""),
            "new_fingerprint": cur["fingerprint"],
            "old_file_count": old.get("file_count"),
            "new_file_count": cur["file_count"],
            "old_verdict": old.get("overall", ""),
            "old_verdict_ts": old.get("ts", ""),
            "detected_by": "drift_scan.py",
        }
        if no in ex:
            rec["requeue"] = False
            rec["suppress_reason"] = ex[no] or "在 .drift_exclude 名单内"
            # 基准推进到新指纹，避免同一处漂移每轮重复刷失效记录；
            # 该单已留痕一次，后续再变还会再报。
            if dry_run:
                requeue.append(no + "  [排除名单,不重排队]")
                continue
            append_jsonl(INVALID, rec)
            append_jsonl(LEDGER, {
                "ts": now_iso(), "changeset_no": no,
                "commit_hash": cur["commit_hash"], "fingerprint": cur["fingerprint"],
                "file_count": cur["file_count"], "overall": "SUPPRESSED_NOT_REQUEUED",
                "run_id": "", "note": rec["suppress_reason"]})
            continue
        rec["requeue"] = True
        if not dry_run:
            append_jsonl(INVALID, rec)
        requeue.append(no)
    if emit:
        for no in requeue:
            print(no)
    return requeue


def record(no: str, overall: str, run_id: str) -> None:
    st = current_state().get(no)
    if not st:
        print("NOT_FOUND %s" % no, file=sys.stderr)
        return
    append_jsonl(LEDGER, {
        "ts": now_iso(), "changeset_no": no,
        "commit_hash": st["commit_hash"], "fingerprint": st["fingerprint"],
        "file_count": st["file_count"], "overall": overall, "run_id": run_id})


def backfill() -> int:
    """用机器留痕里「判决当时读到的锚点」种子化台账。

    刻意**不**用现库状态种子化——那会把已经发生的漂移一笔抹平，等于假设问题不存在。
    历史条目的明细无从还原，故 fingerprint 置空、只按 commit_hash + file_count 比对；
    重排队一轮之后，该单就有了完整指纹。
    """
    if not os.path.exists(RUNTIME_DB):
        print("runtime.db 不存在，无法回填", file=sys.stderr)
        return 0
    c = sqlite3.connect(RUNTIME_DB)
    seen: dict[str, tuple[str, int, str]] = {}
    for cs, ts, dg in c.execute(
            "select changeset_no,ts,output_digest from trace_runs "
            "where run_tag='default' and node_id='n1' order by ts"):
        m = re.search(r"commit_hash=([0-9a-f]{40})", dg)
        fc = re.search(r"file_count=(\d+)", dg)
        if m:
            seen[cs] = (m.group(1), int(fc.group(1)) if fc else -1, ts)
    n = 0
    for cs, (sha, fcount, ts) in sorted(seen.items()):
        append_jsonl(LEDGER, {
            "ts": ts, "changeset_no": cs, "commit_hash": sha,
            "fingerprint": "", "file_count": fcount,
            "overall": "BACKFILLED_FROM_TRACE", "run_id": "",
            "note": "由 runtime.db 的 n1 留痕回填：判决当时读到的锚点与 file_count"})
        n += 1
    return n


# [<日期> 修一·多仓解析] 原来写死单仓，于是**登记正确但提交在另一个仓**的单必然被判
# 「锚点在仓库中不存在」——实证 CS-<日期>-0017：锚点 e6a6bcec9e3a 真实存在于
# ${FLEET_HOME}/<项目>ERP，提交标题与登记标题逐字对应、改动文件恰好就是登记那一个。
# 判据把一条完全正确的登记报成了缺陷。改为按仓库清单逐个解析，命中即用该仓做基线搜索。
# 仓库清单参数化（硬约定 7）：环境变量 DRIFT_REPOS 以冒号分隔覆盖。
REPOS = [x for x in os.environ.get(
    "DRIFT_REPOS", "${FLEET_INTEGRATION_REPO}:${FLEET_HOME}/<项目>ERP").split(":") if x]
REPO = REPOS[0]                   # 兼容既有引用；新代码一律走 REPOS
MAX_BASELINE_DEPTH = 200          # 实测有 126 条提交合并登记的单，50 层会误判

# [<日期> 修二·免锚点豁免] 与执行图 n1 的 output_contract 逐字对齐——那里 commit_hash
# 写的是 ([0-9a-f]{40}|NULL)，契约注释原话「change_type=data_migration 的登记单不挂 commit」。
# 此前复核对空锚点无条件判 FAIL，与 n1 形成**同一系统两套口径**：跑图时承认免锚点、复核时
# 不承认，这类单每复核一次就假 FAIL 一次（实证 CS-<日期>-0061/0062，属持续产噪音的活缺陷）。
ANCHORLESS_CHANGE_TYPES = set(x for x in os.environ.get(
    "GATE_ANCHORLESS_TYPES", "data_migration").split(",") if x)

# [<日期> 空明细短路] 大批量试跑列的判据改进项 #1，此处落地。
# 病象：明细为空时，下面那个循环会**走满 200 层第一父链**去找「diff 等于空集」的基线，
# 必然找不到，最后报「第一父链 1..200 层内无任何基线与登记明细逐字相等」——
# **真相是「登记根本没写明细」**。判词指错方向，与 08-15 那次 n1 判词误导同类。
# 而且它对**天生无文件的交付类型**是纯误报：实测 change_type=verification 共 3 单
# （标题「集成线-L3验证/复跑/三跑」），有 40 位合规锚点、按定义不产生代码文件，
# 却因明细为空被判 FAIL。
# 免明细类型与免锚点类型是两件事，白名单分开（与 registration_gate 同源，同名环境变量）。
DETAILLESS_CHANGE_TYPES = set(x for x in os.environ.get(
    "GATE_DETAILLESS_TYPES", "data_migration,verification").split(",") if x)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, "--no-optional-locks", "-c", "core.quotepath=false", *args],
        capture_output=True, text=True)


def resolve_repo(commit_hash: str) -> "str | None":
    """按仓库清单逐个解析锚点，返回第一个解析得开该提交的仓；都解析不开返回 None。"""
    for repo in REPOS:
        if not os.path.isdir(os.path.join(repo, ".git")):
            continue
        if _git(repo, "cat-file", "-e", commit_hash + "^{commit}").returncode == 0:
            return repo
    return None


def recheck_consistency(commit_hash: str, manifest_paths: set[str],
                        change_type: str = "") -> dict:
    """登记自洽性复核：锚点解析得开吗？明细与锚点对得上吗？

    **与「能不能放行并线」是两个问题。** 已并线的单，放行与否已无意义，但登记是否
    自洽仍然要成立——漂移重排队要问的正是后者。图 v5.2 的入口分诊会把已并线单
    整单 SKIP 掉（实测 0054 五个判决点全 SKIPPED_ALREADY_MERGED），所以漂移通路
    不能只靠跑图，否则重排队等于走过场。

    基线口径与图 n9b 一致：沿第一父链找第一个 diff 文件集合与登记明细**逐字相等**
    的基线；必须带 core.quotepath=false，否则中文路径被转义成八进制会假报不一致。
    """
    if not commit_hash:
        if change_type in ANCHORLESS_CHANGE_TYPES:
            return {"verdict": "N/A",
                    "reason": "change_type=%s 免锚点（与执行图 n1 契约一致），无锚点可复核"
                              % change_type,
                    "baseline": ""}
        return {"verdict": "FAIL", "reason": "登记未填 commit_hash", "baseline": ""}
    if not manifest_paths:
        if change_type in DETAILLESS_CHANGE_TYPES:
            return {"verdict": "N/A",
                    "reason": "change_type=%s 免明细（按定义不产生代码文件），无明细可比对"
                              % change_type,
                    "baseline": ""}
        return {"verdict": "FAIL",
                "reason": "登记无文件明细（0 行）——不是基线找不到，是登记没写",
                "baseline": ""}
    repo = resolve_repo(commit_hash)
    if repo is None:
        return {"verdict": "FAIL",
                "reason": "锚点在已知仓库中均不存在: %s（已查 %s）"
                          % (commit_hash[:12], "、".join(REPOS)),
                "baseline": ""}
    last = set()
    for n in range(1, MAX_BASELINE_DEPTH + 1):
        d = _git(repo, "diff", "--name-only", "%s~%d" % (commit_hash, n), commit_hash)
        if d.returncode != 0:
            break                       # 父链到头
        got = set(x for x in d.stdout.splitlines() if x.strip())
        if got == manifest_paths:
            return {"verdict": "PASS",
                    "reason": "基线 ~%d 与登记明细逐字相等（仓库 %s）" % (n, repo),
                    "baseline": "%s~%d" % (commit_hash, n)}
        last = got
    only_db = sorted(manifest_paths - last)
    only_git = sorted(last - manifest_paths)
    return {"verdict": "FAIL",
            "reason": ("第一父链 1..%d 层内无任何基线与登记明细逐字相等；"
                       "以最后一层为例，仅登记未见于改动: %s；仅改动未登记: %s"
                       % (MAX_BASELINE_DEPTH, only_db or "无", only_git or "无")),
            "baseline": ""}


def recheck(no: str) -> int:
    st = current_state().get(no)
    if not st:
        print("NOT_FOUND %s" % no, file=sys.stderr)
        return 2
    res = recheck_consistency(st["commit_hash"], set(p for p, _ in st["manifest"]),
                              st.get("change_type", ""))
    print("recheck=%s changeset=%s anchor=%s files=%d baseline=%s reason=%s"
          % (res["verdict"], no, st["commit_hash"][:12], st["file_count"],
             res["baseline"] or "-", res["reason"]))
    # N/A（免锚点）与 PASS 同样属「无需人介入」，返回 0 让轮询器静默——
    # 免锚点单本来就没有可复核的锚点，判它 FAIL 等于每轮制造一条假告警。
    return 0 if res["verdict"] in ("PASS", "N/A") else 1


def recheck_selftest() -> int:
    """自洽性复核的两态取证：真实一致样本判 PASS，三种病态各判 FAIL。

    按硬约定 6，零反例的分支不许上线——这里逐个分支造出反例。
    """
    cases = []
    st = current_state()
    ok_no = "CS-<日期>-0048"        # 已被人工修正回自洽状态，作正例
    if ok_no in st:
        cases.append(("真实自洽样本", st[ok_no]["commit_hash"],
                      set(p for p, _ in st[ok_no]["manifest"]), "PASS"))
        real_sha = st[ok_no]["commit_hash"]
        real_files = set(p for p, _ in st[ok_no]["manifest"])
        cases.append(("明细少一个", real_sha, set(sorted(real_files)[1:]), "FAIL"))
        cases.append(("明细多一个幽灵", real_sha,
                      real_files | {"frontend/src/NoSuchGhost.vue"}, "FAIL"))
    cases.append(("锚点不存在", "0" * 40, {"a.java"}, "FAIL"))
    cases.append(("锚点为空", "", {"a.java"}, "FAIL"))

    # [<日期> 修二] 免锚点豁免逐分支取证（硬约定 11：并列条件必须逐支单独取证）
    cases.append(("免锚点·正例", "", set(), "N/A", "data_migration"))
    # [<日期>] 空明细短路逐分支取证
    cases.append(("空明细·免明细类型", "0" * 40, set(), "N/A", "verification"))
    cases.append(("空明细·普通类型仍 FAIL", "0" * 40, set(), "FAIL", "feature"))
    if ok_no in st:   # 反例：有明细的单不得被短路吞掉，照常走完整比对
        cases.append(("空明细·反例有明细", st[ok_no]["commit_hash"],
                      set(p for p, _ in st[ok_no]["manifest"]), "PASS", "feature"))
    # 反例一：豁免**不许泛化**到其它变更类型——空锚点的普通单仍须 FAIL
    cases.append(("免锚点·反例普通单", "", {"a.java"}, "FAIL", "feature"))
    # 反例二：豁免**不许吞掉**有锚点的 data_migration 单——照常走完整复核
    if ok_no in st:
        cases.append(("免锚点·反例带锚点", st[ok_no]["commit_hash"],
                      set(p for p, _ in st[ok_no]["manifest"]), "PASS", "data_migration"))

    ok = True
    for case in cases:
        name, sha, man, want = case[:4]
        ctype = case[4] if len(case) > 4 else ""
        got = recheck_consistency(sha, man, ctype)["verdict"]
        good = got == want
        ok &= good
        print("  %-20s → %-4s 期望 %-4s %s" % (name, got, want, "OK" if good else "**不符**"))

    # [<日期> 修一] 多仓解析逐分支取证：解析层单独验，不与明细比对混在一起
    print("  ── 多仓锚点解析 ──")
    repo_cases = [
        ("正例·跨仓锚点(CS-<日期>-0017)", "e6a6bcec9e3afac871bb0098c30793e58be87c4d",
         "${FLEET_HOME}/<项目>ERP"),
        ("反例·全零锚点(各仓都无)", "0" * 40, None),
    ]
    if ok_no in st:
        repo_cases.insert(1, ("反例·本仓锚点(仍落首选仓，多仓改动不得改变结论)",
                              st[ok_no]["commit_hash"], "${FLEET_INTEGRATION_REPO}"))
    for name, sha, want_repo in repo_cases:
        got_repo = resolve_repo(sha)
        good = got_repo == want_repo
        ok &= good
        print("  %-42s → %-22s 期望 %-22s %s"
              % (name, got_repo or "None", want_repo or "None", "OK" if good else "**不符**"))

    print("自洽性复核取证：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def selftest() -> int:
    """逐分支取证（硬约定 11）：锚点变 / 明细变 / 两者都不变，各自单独验。

    只在内存里造样本，不碰真台账、不碰数据库。
    """
    M = [("a.java", "MODIFY"), ("b.vue", "ADD")]
    base_fp = fingerprint("aaaa", M)
    cases = [
        ("仅锚点变",   "bbbb", M,                                   True,  "锚点"),
        ("仅明细变",   "aaaa", M + [("c.ts", "ADD")],               True,  "明细"),
        ("明细改动作", "aaaa", [("a.java", "DELETE"), ("b.vue", "ADD")], True, "明细"),
        ("两者都不变", "aaaa", list(reversed(M)),                   False, ""),
    ]
    ok = True
    for name, sha, man, should_fire, want_kind in cases:
        fp = fingerprint(sha, man)
        fired = (fp != base_fp)
        kind = []
        if sha != "aaaa":
            kind.append("锚点")
        if len(man) != len(M) or (fired and "锚点" not in kind):
            kind.append("明细")
        got_kind = "+".join(kind)
        good = (fired == should_fire) and (not should_fire or want_kind in got_kind)
        ok &= good
        print("  %-10s 触发=%-5s 类型=%-6s 期望触发=%-5s → %s"
              % (name, fired, got_kind or "-", should_fire, "OK" if good else "**不符**"))
    print("逐分支取证：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="审后漂移扫描（判决绑锚点指纹）")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只看不写（不落失效留痕）")
    ap.add_argument("--record")
    ap.add_argument("--overall", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--recheck", help="登记自洽性复核（锚点可解析 + 明细与锚点逐字对上）")
    ap.add_argument("--recheck-selftest", action="store_true")
    a = ap.parse_args()
    if a.recheck_selftest:
        return recheck_selftest()
    if a.recheck:
        return recheck(a.recheck)
    if a.selftest:
        return selftest()
    if a.backfill:
        print("回填台账条目数: %d" % backfill())
        return 0
    if a.record:
        record(a.record, a.overall, a.run_id)
        return 0
    if a.scan:
        scan(dry_run=a.dry_run)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
