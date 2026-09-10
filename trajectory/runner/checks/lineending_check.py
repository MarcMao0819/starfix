#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行尾（CRLF/LF）判据：区分“存量行尾被误翻”与“内容改动、存量行尾未动”。

域主口径（<日期> 立，纠正两笔误报 CS-<日期>-0055 / feat/<项目>-theme 后改写）：
旧判据「改后任一文件 CR>0 即 FAIL」检的是文件的**绝对状态**，不是**这次改动**——
漏报了整体 CRLF→LF 转换（改后 CR=0 反而全绿，这才是最该抓的破坏）、
误报了行尾已经存在的文件正常改几行内容（存量 CR 计数没被这次改动碰过）。

新判据对每个改动文件：
  - base 侧存在该文件（modify/rename）：
        改后CR == 改前CR + 新增行中CRLF数 - 删除行中CRLF数  -> PASS（存量行尾未动）
        不等                                                -> AMBIGUOUS（交给调用方 llm 兜底裁决是破坏还是修复）
  - base 侧不存在该文件（新增文件，项目为 LF 基线）：
        文件含 CR -> FAIL；不含 CR -> PASS
  - 该文件在 head 侧不存在（删除）：SKIP（不参与判决，无内容可查）

关键实现决策——`git diff` 必须带 `--ignore-cr-at-eol`：
若不带这个 flag，整篇 CRLF→LF 转换会让每一行都在字节层面产生差异（旧内容带 \r、
新内容不带），git 逐行 diff 引擎因此把全部行都标成“删除旧行+新增新行”（背景材料
里“3行改动出97行diff”说的就是这个现象）。此时 deleted_crlf 会恰好等于 before_cr、
added_crlf=0，代入公式得 before_cr + 0 - before_cr = 0 = after_cr——**公式反而算出
PASS**，等于没堵住这个洞。`--ignore-cr-at-eol` 让 git 在做行匹配时忽略行尾的 \r，
使得“内容没变、只是行尾变了”的行被识别为未改动（不出现在 +/- 里），于是
before_cr!=0 而 added_crlf=deleted_crlf=0，代入公式 before_cr != after_cr(0)，
判 AMBIGUOUS——这才是 F1/F2 两个整体转换 fixture 必须给出的结果。
对真正被编辑的行（内容确有变化），--ignore-cr-at-eol 只影响“是否算作变动行”的
判定，不影响该行本身在 diff 里打印出来的真实字节（该行原有的 \r 状态照样如实体现
在 +/- 输出里），因此公式对“正常编辑一个存量 CRLF/混合行尾文件”的场景不受影响
（CS-<日期>-0055: 223+11-2=232，逐字吻合）。

只用 Python 3 标准库（subprocess 调用只读 git 子命令：diff --name-status /
diff --ignore-cr-at-eol / show / cat-file -e，全部带 --no-optional-locks）。

CLI:
    python3 lineending_check.py <repo> <base_ref> <head_ref>

stdout：每个改动文件一行
    status=<A|M|D|R100|...> before_cr=<int|NA> after_cr=<int|NA>
    before_lines=<int|NA> after_lines=<int|NA> added_crlf=<int|NA> deleted_crlf=<int|NA>
    abs=<PASS|FAIL> verdict=<PASS|FAIL|AMBIGUOUS|SKIP> path=<path>
最后一行总判：
    overall_verdict=<PASS|FAIL|AMBIGUOUS> checked=<n> fail=<n> ambiguous=<n> skipped=<n>
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional


def _git(repo: str, *args: str):
    """只读 git 调用：一律带 --no-optional-locks 与 -c core.quotepath=false（消除中文等非 ASCII
    路径被 git 默认八进制转义导致的假阳性/路径读取失败，与本仓库 n5/n8 既有约定一致）。
    返回 (returncode, stdout_bytes, stderr_bytes)。"""
    p = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.quotepath=false", "-C", repo, *args],
        capture_output=True,
    )
    return p.returncode, p.stdout, p.stderr


def _git_text(repo: str, *args: str):
    rc, out, err = _git(repo, *args)
    return rc, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def blob_metrics_at(repo: str, ref: str, path: str) -> Optional[dict]:
    """Git 原始字节的 CR 数与 splitlines 行数；文件不存在则 None。

    行数与登记 p5 同口径：``bytes.splitlines()`` 不把尾 LF 算作空行。
    """
    rc, out, _err = _git(repo, "show", f"{ref}:{path}")
    if rc != 0:
        return None
    return {"cr": out.count(b"\r"), "lines": len(out.splitlines())}


def absolute_verdict(status: str, before: Optional[dict], after: Optional[dict]) -> str:
    """绝对补齿：原先全 CRLF / 全 LF 的文件改后不得留下混写。"""
    if status.startswith("D"):
        return "PASS"
    if after is None:
        return "FAIL"
    if status.startswith("A"):
        return "PASS" if after["cr"] == 0 else "FAIL"
    if before is None:
        return "FAIL"
    if before["cr"] == before["lines"]:
        return "PASS" if after["cr"] == after["lines"] else "FAIL"
    if before["cr"] == 0:
        return "PASS" if after["cr"] == 0 else "FAIL"
    # 历史混写仍由差值法给 AMBIGUOUS，不能凭本绝对规则假定它是本次造成。
    return "PASS"


def diff_crlf_line_counts(repo: str, base_ref: str, head_ref: str, old_path: str, new_path: str):
    """扫描 `git diff --ignore-cr-at-eol <base> <head> -- <file>` 的原始字节输出，统计：
      added_crlf   = 以 '+' (非 '+++') 开头且行尾带 \\r 的行数
      deleted_crlf = 以 '-' (非 '---') 开头且行尾带 \\r 的行数
    --ignore-cr-at-eol 让"仅行尾变化、内容未变"的行不出现在 +/- 里（见模块 docstring）。
    """
    paths = [old_path] if old_path == new_path else [old_path, new_path]
    rc, out, _err = _git(repo, "diff", "--no-color", "--ignore-cr-at-eol", "-M",
                          base_ref, head_ref, "--", *paths)
    added = deleted = 0
    if rc != 0:
        return added, deleted
    for line in out.split(b"\n"):
        if line.startswith(b"+++") or line.startswith(b"---"):
            continue
        if line.startswith(b"+"):
            if line.endswith(b"\r"):
                added += 1
        elif line.startswith(b"-"):
            if line.endswith(b"\r"):
                deleted += 1
    return added, deleted


def list_changes(repo: str, base_ref: str, head_ref: str):
    """返回 [(status, old_path, new_path), ...]；-M 做改名检测(status 形如 M/A/D/R100)。"""
    rc, out, err = _git_text(repo, "diff", "--no-color", "--ignore-cr-at-eol", "-M",
                              "--name-status", base_ref, head_ref)
    if rc != 0:
        raise RuntimeError(f"git diff --name-status 失败: {err.strip()}")
    changes = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            old_path, new_path = parts[1], parts[2]
        else:
            old_path = new_path = parts[1]
        changes.append((status, old_path, new_path))
    return changes


def check_file(repo: str, base_ref: str, head_ref: str, status: str, old_path: str, new_path: str) -> dict:
    if status.startswith("D"):
        return {"path": new_path, "status": status, "before_cr": None, "after_cr": None,
                 "before_lines": None, "after_lines": None, "added_crlf": None, "deleted_crlf": None,
                 "abs": "PASS", "verdict": "SKIP"}
    if status.startswith("A"):
        after = blob_metrics_at(repo, head_ref, new_path)
        if after is None:
            # head 侧理应存在却读不到内容：判不了，交调用方兜底
            return {"path": new_path, "status": status, "before_cr": None, "after_cr": None,
                     "before_lines": None, "after_lines": None, "added_crlf": None, "deleted_crlf": None,
                     "abs": "FAIL", "verdict": "AMBIGUOUS"}
        abs_check = absolute_verdict(status, None, after)
        verdict = "FAIL" if after["cr"] > 0 or abs_check == "FAIL" else "PASS"
        return {"path": new_path, "status": status, "before_cr": None, "after_cr": after["cr"],
                 "before_lines": None, "after_lines": after["lines"], "added_crlf": None,
                 "deleted_crlf": None, "abs": abs_check, "verdict": verdict}
    # M / R / C：base 侧存在
    before = blob_metrics_at(repo, base_ref, old_path)
    after = blob_metrics_at(repo, head_ref, new_path)
    if before is None or after is None:
        # 图声明是 modify/rename 但某一侧实际读不到内容：数据不自洽，转 AMBIGUOUS 兜底
        return {"path": new_path, "status": status,
                 "before_cr": None if before is None else before["cr"],
                 "after_cr": None if after is None else after["cr"],
                 "before_lines": None if before is None else before["lines"],
                 "after_lines": None if after is None else after["lines"],
                 "added_crlf": None, "deleted_crlf": None, "abs": "FAIL", "verdict": "AMBIGUOUS"}
    added_crlf, deleted_crlf = diff_crlf_line_counts(repo, base_ref, head_ref, old_path, new_path)
    expected_after = before["cr"] + added_crlf - deleted_crlf
    abs_check = absolute_verdict(status, before, after)
    verdict = "PASS" if expected_after == after["cr"] else "AMBIGUOUS"
    if abs_check == "FAIL":
        verdict = "FAIL"
    return {"path": new_path, "status": status, "before_cr": before["cr"], "after_cr": after["cr"],
             "before_lines": before["lines"], "after_lines": after["lines"], "added_crlf": added_crlf,
             "deleted_crlf": deleted_crlf, "abs": abs_check, "verdict": verdict}


def run(repo: str, base_ref: str, head_ref: str) -> dict:
    changes = list_changes(repo, base_ref, head_ref)
    records = [check_file(repo, base_ref, head_ref, status, old, new) for status, old, new in changes]
    checked = [r for r in records if r["verdict"] != "SKIP"]
    fail = [r for r in checked if r["verdict"] == "FAIL"]
    ambiguous = [r for r in checked if r["verdict"] == "AMBIGUOUS"]
    if fail:
        overall = "FAIL"
    elif ambiguous:
        overall = "AMBIGUOUS"
    else:
        overall = "PASS"
    return {
        "records": records, "overall": overall, "checked": len(checked),
        "fail": len(fail), "ambiguous": len(ambiguous), "skipped": len(records) - len(checked),
    }


def _fmt(v) -> str:
    return "NA" if v is None else str(v)


def main() -> int:
    if len(sys.argv) != 4:
        print("用法: python3 lineending_check.py <repo> <base_ref> <head_ref>", file=sys.stderr)
        return 2
    repo, base_ref, head_ref = sys.argv[1], sys.argv[2], sys.argv[3]
    result = run(repo, base_ref, head_ref)
    for r in result["records"]:
        print(
            f"status={r['status']} before_cr={_fmt(r['before_cr'])} after_cr={_fmt(r['after_cr'])} "
            f"before_lines={_fmt(r['before_lines'])} after_lines={_fmt(r['after_lines'])} "
            f"added_crlf={_fmt(r['added_crlf'])} deleted_crlf={_fmt(r['deleted_crlf'])} abs={r['abs']} "
            f"verdict={r['verdict']} path={r['path']}"
        )
    print(
        f"overall_verdict={result['overall']} checked={result['checked']} "
        f"fail={result['fail']} ambiguous={result['ambiguous']} skipped={result['skipped']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
