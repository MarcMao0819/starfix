#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登记定型判据：开审前判断一张变更单的登记「写完了没有」。

## 为什么要有这个判据（<日期> 实测两例）

轮询器取 `id > 游标` 的新单，**INSERT 一落地就可见**，但承建方的登记不是一次原子写完的：

| 单号 | 入队时刻 | 入队时看到的 | 登记定型时刻 | 差 |
|---|---|---|---|---|
| CS-<日期>-0010 | 13:19:08 | `commit_hash=d791dada9`（9 位） | 13:19:36 | **28 秒** |
| CS-<日期>-0009 | 12:46:20 | 明细行 0 条（`file_count=9`） | 12:46:30 | **10 秒** |

两单当时都判了 FAIL。**判决对它当时读到的内容是对的，问题是它读到的是半成品。**
这跟硬约定 12（判决绑输入指纹）是同一个病的两头：12 管「审完之后被改」，
本判据管「审之前还没写完」。

## 判据（两条并列，按硬约定 11 逐分支单独取证）

- **S1 锚点未定型**：`commit_hash` 有值但**不是 40 位十六进制**。
  短哈希是登记中途的形态（先写 `git rev-parse --short`，后补全）。
  `NULL`/空**不算**未定型——n1 契约本就允许 `change_type=data_migration` 的单不挂锚点，
  把 NULL 判成未定型会让这类单每次都白等一轮。
- **S2 明细未写入**：`file_count > 0` 但明细行数为 **0**。

**没有 S3「明细写了一半」**（`0 < detail < file_count`）。那种形态本轮一例都没观测到，
而 n6 本来就判这个三方计数比对——加一条没有反例的分支等于给判据塞死代码，
按硬约定 6「零反例的分支不许上线」不写。已知局限，见文末。

## 等不到就照审，不是死等

重试用尽仍未定型 → **照常开审**。理由：不审比审出假 FAIL 更糟——不审就没有任何记录。
配合同批的执行器修复（n1 取数失败一律降 NEEDS_HUMAN，不再产 FAIL），
CS-<日期>-0008 这种**永久性**短哈希登记（至今仍是 9 位，不是竞态）会稳定落到
NEEDS_HUMAN 交人工，既不误判也不丢单。

## 阈值参数化（硬约定 7）

`SETTLE_WAIT_SEC` / `SETTLE_MAX_TRIES` 走环境变量，不硬编码——否则自检时造不出
「等一轮就定型」和「等到头也不定型」两种态。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

DOCKER = "/usr/local/bin/docker"
CONTAINER = "${DB_CONTAINER}"
DBNAME = "${DB_NAME}"

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

DEFAULT_WAIT_SEC = float(os.environ.get("SETTLE_WAIT_SEC", "30"))
DEFAULT_MAX_TRIES = int(os.environ.get("SETTLE_MAX_TRIES", "2"))

# 主表里表示「没有锚点」的合法终态值。注意 mysql 批处理模式把 SQL NULL 打成字面 "NULL"。
NULLISH = ("", "NULL", "null", "\\N", "None")


def evaluate_settle(row: dict, detail_count: int) -> tuple[bool, list[str]]:
    """纯函数：给定主表行与明细行数，判断登记是否已定型。

    row 需含 `commit_hash` / `file_count`。返回 (settled, reasons)；
    settled=True 时 reasons 为空。**不读库、不睡眠**，便于逐分支造例取证。
    """
    reasons: list[str] = []

    commit_hash = str(row.get("commit_hash", "") or "").strip()
    if commit_hash not in NULLISH and not SHA40_RE.match(commit_hash):
        reasons.append(
            "S1 锚点未定型: commit_hash=%r 长度 %d，既非 40 位十六进制也非 NULL"
            % (commit_hash, len(commit_hash))
        )

    try:
        file_count = int(str(row.get("file_count", "")).strip())
    except (TypeError, ValueError):
        file_count = 0
    if file_count > 0 and detail_count == 0:
        reasons.append(
            "S2 明细未写入: file_count=%d 但 t_code_change_file 行数为 0" % file_count
        )

    return (not reasons), reasons


# ── 以下是读库的薄壳，判据本身在上面那个纯函数里 ─────────────────────────

def _mysql(sql: str) -> str:
    inner = (
        'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
        "-N -B %s -e \"%s\"" % (DBNAME, sql.replace('"', '\\"'))
    )
    p = subprocess.run([DOCKER, "exec", CONTAINER, "sh", "-c", inner],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("mysql 查询失败: %s" % (p.stderr.strip()[:200]))
    return p.stdout


def fetch(changeset_no: str) -> tuple[dict, int]:
    """取主表行与明细行数。查不到单 -> 抛 LookupError（调用方按「还没写进来」处理）。"""
    out = _mysql(
        "SELECT IFNULL(commit_hash,'NULL'), file_count FROM t_code_changeset "
        "WHERE changeset_no='%s' AND is_del=0" % changeset_no
    ).strip()
    if not out:
        raise LookupError("主表查无此单: %s" % changeset_no)
    parts = out.split("\n")[0].split("\t")
    row = {"commit_hash": parts[0], "file_count": parts[1] if len(parts) > 1 else "0"}

    cnt = _mysql(
        "SELECT COUNT(f.id) FROM t_code_changeset c LEFT JOIN t_code_change_file f "
        "ON f.changeset_id=c.id WHERE c.changeset_no='%s' AND c.is_del=0" % changeset_no
    ).strip()
    detail = int(cnt.split("\n")[0]) if cnt else 0
    return row, detail


def wait_until_settled(changeset_no: str, wait_sec: float, max_tries: int,
                       verbose: bool = True) -> tuple[bool, list[str]]:
    """轮询到定型或次数用尽。**用尽也返回，由调用方照常开审**（见模块文档）。"""
    reasons: list[str] = []
    for attempt in range(1, max_tries + 1):
        try:
            row, detail = fetch(changeset_no)
        except LookupError as e:
            reasons = ["S0 主表尚无此单: %s" % e]
        else:
            settled, reasons = evaluate_settle(row, detail)
            if settled:
                if verbose and attempt > 1:
                    print("SETTLED after %d tries" % attempt, file=sys.stderr)
                return True, []
        if attempt < max_tries:
            if verbose:
                print("UNSETTLED (%d/%d): %s —— 等 %.0fs 重取"
                      % (attempt, max_tries, "; ".join(reasons), wait_sec), file=sys.stderr)
            time.sleep(wait_sec)
    return False, reasons


# ── 自检：每条判据的正反两极都要有例（硬约定 5/6/11）────────────────────

SELFTEST_CASES = [
    # (用例名, row, detail_count, 期望 settled, 期望命中的分支标记)
    ("S1 正例·9 位短哈希（CS-0010 入队瞬间的真实形态）",
     {"commit_hash": "d791dada9", "file_count": "7"}, 7, False, "S1"),
    ("S1 正例·10 位短哈希（CS-0008 至今的真实形态）",
     {"commit_hash": "c157643bb", "file_count": "9"}, 9, False, "S1"),
    ("S1 反例·标准 40 位（正常单，不得触发）",
     {"commit_hash": "d791dada9845759000aa529ba59c703e5f50c202", "file_count": "7"}, 7, True, None),
    ("S1 反例·NULL 锚点 + 明细齐（data_migration 单，合法终态，不得死等）",
     {"commit_hash": "NULL", "file_count": "3"}, 3, True, None),
    ("S1 反例·空串锚点 + 明细齐（同上，另一种落库形态）",
     {"commit_hash": "", "file_count": "3"}, 3, True, None),

    ("S2 正例·明细 0 行（CS-0009 入队瞬间的真实形态）",
     {"commit_hash": "bfeb30dfabe9e05d25ec292da0757a3b30e400ca", "file_count": "9"}, 0, False, "S2"),
    ("S2 反例·明细齐（正常单，不得触发）",
     {"commit_hash": "bfeb30dfabe9e05d25ec292da0757a3b30e400ca", "file_count": "9"}, 9, True, None),
    ("S2 反例·file_count=0 且明细 0（空单，不得死等）",
     {"commit_hash": "bfeb30dfabe9e05d25ec292da0757a3b30e400ca", "file_count": "0"}, 0, True, None),
    ("S2 反例·明细多于登记（数量不符是 n6 的活，不归本判据管）",
     {"commit_hash": "bfeb30dfabe9e05d25ec292da0757a3b30e400ca", "file_count": "2"}, 9, True, None),
    ("S2 反例·明细少于登记但非 0（无反例样本，本判据刻意不管，交 n6）",
     {"commit_hash": "bfeb30dfabe9e05d25ec292da0757a3b30e400ca", "file_count": "9"}, 4, True, None),

    ("S1+S2 同时命中（两条分支互不遮蔽）",
     {"commit_hash": "c157643bb", "file_count": "9"}, 0, False, "S1+S2"),
]


def selftest() -> int:
    ok = True
    hit_counter: dict[str, int] = {"S1": 0, "S2": 0}
    for name, row, detail, want_settled, want_branch in SELFTEST_CASES:
        got_settled, reasons = evaluate_settle(row, detail)
        blob = " ".join(reasons)
        got_branches = [b for b in ("S1", "S2") if b + " " in blob]
        for b in got_branches:
            hit_counter[b] += 1
        good = (got_settled == want_settled)
        if want_branch and want_branch != "S1+S2":
            good = good and got_branches == [want_branch]
        elif want_branch == "S1+S2":
            good = good and got_branches == ["S1", "S2"]
        elif want_branch is None:
            good = good and not got_branches
        ok &= good
        print("  %-58s settled=%-5s 命中=%-8s %s"
              % (name[:58], got_settled, ",".join(got_branches) or "-",
                 "OK" if good else "**不符**"))
        if not good:
            print("        理由: %s" % blob)

    print("\n逐分支命中统计（硬约定 11：命中数为 0 的分支必须查清是没造到还是不成立）:")
    for b, n in hit_counter.items():
        mark = "OK" if n > 0 else "**零命中——分支可能是死的**"
        print("    %s 命中 %d 次  %s" % (b, n, mark))
        ok &= n > 0

    print("\n自检：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="登记定型判据")
    ap.add_argument("changeset_no", nargs="?")
    ap.add_argument("--wait-sec", type=float, default=DEFAULT_WAIT_SEC)
    ap.add_argument("--max-tries", type=int, default=DEFAULT_MAX_TRIES)
    ap.add_argument("--check-only", action="store_true", help="只判一次，不等待")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.changeset_no:
        ap.error("需要 changeset_no")

    if a.check_only:
        try:
            row, detail = fetch(a.changeset_no)
        except LookupError as e:
            print("UNSETTLED S0 %s" % e)
            return 1
        settled, reasons = evaluate_settle(row, detail)
    else:
        settled, reasons = wait_until_settled(a.changeset_no, a.wait_sec, a.max_tries)

    print("SETTLED" if settled else "UNSETTLED " + "; ".join(reasons))
    return 0 if settled else 1


if __name__ == "__main__":
    sys.exit(main())
