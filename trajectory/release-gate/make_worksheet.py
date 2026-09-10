#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""<项目>发布管道 · 操作单生成器（第五条轨道一期）。

只出单，不动手。纯只读：

  - 只调用 git 的只读子命令（status/log/diff/show/rev-parse/merge-base/grep/branch）
  - 只对 ${DB_NAME} 库跑 SELECT/SHOW/DESCRIBE
  - 只调用固定路径的 runner/checks/lineending_check.py（本身也是只读脚本）

全部外部调用一律经过本文件里的三个白名单函数（run_git_readonly / run_sql_readonly /
run_lineending_check），三者内部先做只读性校验，校验不过直接抛 GuardBlocked、绝不
执行真实命令。脚本里不出现 merge / package / 重启 / 写库 / 写文件（唯一的写文件动作
是把生成的操作单落到 --out 指定路径，这是本工具的设计输出，不是对被检查系统的写操作）。

依据：${TRAJ_HOME}/release-gate/PIPELINE-SPEC.md 的 A/B/C/D/E 节。

离线登记模式（--registry-json，仅用于验证，见 PIPELINE-SPEC.md §A0）：提供该参数时，A1/A2
所需的登记数据（changeset 行 + 文件明细清单，含 A1 可选的回执目录覆盖）改从这份 JSON 读，
彻底不连 ${DB_NAME}；不提供该参数时，行为与本来一致，照旧查库。生产发单场景一律不传
这个参数，走查库路径。

用法：
    python3 make_worksheet.py --worktree <候选worktree路径> --changeset <CS-单号> [--out <md路径>]
    python3 make_worksheet.py --worktree <fixture路径> --changeset <CS-单号> --registry-json <json路径>  # 仅验证用
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

def _require_env(name):
    """脱敏后这些值必须由环境变量给；缺了报人话，别让它悄悄用空值跑。"""
    import os as _o
    v = _o.environ.get(name)
    if not v:
        raise SystemExit(f"缺少环境变量 {name}。见 trajectory/README.md「怎么挂」")
    return v



# ---------------------------------------------------------------------------
# 固定坐标（与 PIPELINE-SPEC.md 保持逐字一致，改坐标先改规格再改这里）
# ---------------------------------------------------------------------------

INTEGRATION_BRANCH = "feat/<项目>-integration"
INTEGRATION_WORKTREE = "${FLEET_INTEGRATION_REPO}"
DB_CONTAINER = "${DB_CONTAINER}"
DB_NAME = "${DB_NAME}"
DOCKER_BIN = "/usr/local/bin/docker"
RECEIPT_DIR = Path("${FLEET_HOME}/<项目>ERP/迁移备份/回执")
LINEENDING_SCRIPT = Path("${TRAJ_HOME}/runner/checks/lineending_check.py")
LOCK_FILE = Path("${TRAJ_HOME}/release-gate/.locks/CURRENT.lock")
LOCK_TTL_SECONDS_DEFAULT = 1800

BACKEND_JAR = "target/${APP_JAR}"
JDK17_HOME = "${FLEET_INTEGRATION_REPO}/.toolchain/jdk-17.0.20+8/Contents/Home"
JDK11_HOME = "${FLEET_INTEGRATION_REPO}/.toolchain/jdk-11.0.32+9/Contents/Home"
BACKEND_PORT = _require_env("PORT_APP")
FRONTEND_PORT = _require_env("PORT_WEB")
CHANGESET_NO_RE = re.compile(r"^CS-[A-Za-z0-9\-]+$")


# ---------------------------------------------------------------------------
# 只读守卫：所有外部调用的唯一入口
# ---------------------------------------------------------------------------


class GuardBlocked(Exception):
    """白名单函数拒绝执行时抛出——命中即代表"这条命令本可能是写操作，已被拦截"。"""


GUARD_LOG: list[dict[str, str]] = []

# git 只放行这几个只读子命令；不放行 merge/commit/push/checkout/reset/rebase/
# cherry-pick/stash/clean/fetch/pull/apply/am 等一切能改变仓库状态的子命令。
_ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "show", "rev-parse", "merge-base", "grep", "branch"}

# 只读 SQL 起始关键字；命中下列任一写关键字直接拒绝，即使句子以 SELECT 开头也不放行
# （防 "SELECT ...; DROP TABLE ..." 这类拼接注入）。
_SQL_BANNED_RE = re.compile(
    r"(?i)\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|CALL|"
    r"LOCK\s+TABLES|SET\s+PASSWORD|RENAME)\b"
)
_SQL_ALLOWED_START_RE = re.compile(r"(?is)^\s*(SELECT|SHOW|DESCRIBE|DESC)\b")


def _log_guard(kind: str, cmd_repr: str, allowed: bool, reason: str) -> None:
    GUARD_LOG.append(
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": kind,
            "cmd": cmd_repr,
            "decision": "ALLOWED" if allowed else "BLOCKED",
            "reason": reason,
        }
    )


def run_git_readonly(worktree: str, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """唯一允许调用 git 的入口。只放行只读子命令；`branch` 只放行精确的
    `branch --show-current` 一种形态（`branch -D` 之类的删分支操作不因为
    子命令名叫 branch 就蒙混过关）。"""
    if not args:
        _log_guard("git", "git <空参数>", False, "空参数")
        raise GuardBlocked("空 git 命令")
    sub = args[0]
    cmd_repr = "git --no-optional-locks -c core.quotepath=false -C {} {}".format(worktree, " ".join(args))
    if sub not in _ALLOWED_GIT_SUBCOMMANDS:
        _log_guard("git", cmd_repr, False, f"子命令 '{sub}' 不在只读白名单 {sorted(_ALLOWED_GIT_SUBCOMMANDS)} 内")
        raise GuardBlocked(f"拒绝执行非只读 git 子命令: {sub}")
    if sub == "branch" and args != ["branch", "--show-current"]:
        _log_guard("git", cmd_repr, False, "branch 子命令只放行精确的 '--show-current' 形态")
        raise GuardBlocked("拒绝执行非 '--show-current' 的 git branch 调用")
    full = ["git", "--no-optional-locks", "-c", "core.quotepath=false", "-C", worktree, *args]
    _log_guard("git", cmd_repr, True, "只读子命令，放行")
    return subprocess.run(full, capture_output=True, timeout=timeout)


def run_sql_readonly(sql: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """唯一允许查库的入口。只放行以 SELECT/SHOW/DESCRIBE 开头且不含写关键字的语句；
    口令不出容器，读容器内 $MYSQL_ROOT_PASSWORD（照 SKILL.md 连库姿势）。"""
    stripped = sql.strip()
    cmd_repr = f'docker exec {DB_CONTAINER} mysql {DB_NAME} -e "{stripped}"'
    if not _SQL_ALLOWED_START_RE.match(stripped):
        _log_guard("sql", cmd_repr, False, "语句不以 SELECT/SHOW/DESCRIBE 开头，拒绝")
        raise GuardBlocked("拒绝执行非只读 SQL（起始关键字不符）")
    banned = _SQL_BANNED_RE.search(stripped)
    if banned:
        _log_guard("sql", cmd_repr, False, f"命中禁用关键字 '{banned.group(0)}'，拒绝")
        raise GuardBlocked(f"SQL 含禁用写关键字: {banned.group(0)}")
    inner = f'mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" {DB_NAME} -e "{stripped}"'
    full = [DOCKER_BIN, "exec", DB_CONTAINER, "sh", "-c", inner]
    _log_guard("sql", cmd_repr, True, "只读 SELECT/SHOW/DESCRIBE，放行")
    return subprocess.run(full, capture_output=True, timeout=timeout)


def run_lineending_check(worktree: str, base_ref: str, head_ref: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """唯一允许调用外部 Python 脚本的入口；固定死一个路径，不接受任意脚本。"""
    cmd_repr = f"python3 {LINEENDING_SCRIPT} {worktree} {base_ref} {head_ref}"
    if not LINEENDING_SCRIPT.is_file():
        _log_guard("py", cmd_repr, False, "固定脚本路径不存在")
        raise GuardBlocked(f"{LINEENDING_SCRIPT} 不存在")
    _log_guard("py", cmd_repr, True, "固定路径只读脚本，放行")
    return subprocess.run(
        ["python3", str(LINEENDING_SCRIPT), worktree, base_ref, head_ref], capture_output=True, timeout=timeout
    )


def guard_selftest() -> list[str]:
    """故意经同一批白名单函数尝试几个写操作，证明它们真的会被拦截（不是摆设）。
    每次尝试都应该在真正执行任何命令之前就被拒绝——用于收尾验证时把这份日志贴出来。"""
    lines: list[str] = []

    def _expect_blocked(label: str, fn, *fn_args) -> None:
        try:
            fn(*fn_args)
        except GuardBlocked as e:
            lines.append(f"OK  {label} -> 已拦截: {e}")
        else:
            lines.append(f"FAIL {label} -> 未被拦截！这是安全缺口，必须立刻修")

    _expect_blocked("git commit", run_git_readonly, ".", ["commit", "-m", "should-not-run"])
    _expect_blocked("git push", run_git_readonly, ".", ["push"])
    _expect_blocked("git merge --no-ff", run_git_readonly, ".", ["merge", "--no-ff", "HEAD"])
    _expect_blocked("git reset --hard", run_git_readonly, ".", ["reset", "--hard", "HEAD"])
    _expect_blocked("git branch -D", run_git_readonly, ".", ["branch", "-D", "some-branch"])
    _expect_blocked("SQL DELETE", run_sql_readonly, "DELETE FROM t_code_changeset WHERE id=1")
    _expect_blocked("SQL UPDATE", run_sql_readonly, "UPDATE t_code_changeset SET status=1 WHERE id=1")
    _expect_blocked(
        "SQL 拼接注入 (SELECT 开头夹带 DROP)",
        run_sql_readonly,
        "SELECT 1; DROP TABLE t_code_changeset",
    )
    return lines


# ---------------------------------------------------------------------------
# 只读事实获取
# ---------------------------------------------------------------------------


def get_worktree_head(worktree: str) -> str:
    r = run_git_readonly(worktree, ["rev-parse", "HEAD"])
    return r.stdout.decode(errors="replace").strip()


def get_worktree_branch(worktree: str) -> str:
    r = run_git_readonly(worktree, ["branch", "--show-current"])
    return r.stdout.decode(errors="replace").strip()


def get_merge_base(worktree: str, other_branch: str = INTEGRATION_BRANCH) -> Optional[str]:
    r = run_git_readonly(worktree, ["merge-base", other_branch, "HEAD"])
    out = r.stdout.decode(errors="replace").strip()
    return out if r.returncode == 0 and out else None


def get_git_dir(worktree: str) -> str:
    r = run_git_readonly(worktree, ["rev-parse", "--git-dir"])
    return r.stdout.decode(errors="replace").strip()


def get_changed_files(worktree: str, base_ref: str, head_ref: str) -> list[str]:
    r = run_git_readonly(worktree, ["diff", "--name-only", base_ref, head_ref])
    return [l.strip() for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]


def get_changed_status(worktree: str, base_ref: str, head_ref: str) -> list[tuple[str, str]]:
    """返回 [(status, path), ...]；rename/copy 取变更后的路径。"""
    r = run_git_readonly(worktree, ["diff", "--name-status", base_ref, head_ref])
    out = []
    for line in r.stdout.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        out.append((parts[0], parts[-1]))
    return out


def get_commit_subject(worktree: str, ref: str) -> str:
    r = run_git_readonly(worktree, ["show", "-s", "--format=%s", ref])
    return r.stdout.decode(errors="replace").strip()


# ---------------------------------------------------------------------------
# 离线登记模式：--registry-json 加载器（详见 PIPELINE-SPEC.md §A0）
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> dict[str, Any]:
    """把 --registry-json 指向的文件读成内存字典，供 get_changeset /
    get_change_files_from_db / check_a1_changeset_and_verifier 在离线模式下取数据用。
    不做只读守卫（这里没有外部命令可拦，纯本地文件读 + json 解析），格式错直接抛出
    让调用方（main）原样报错退出，不吞异常。

    必需字段：changeset（对象，字段须与 get_changeset 查库分支返回的字典一一对应：
    id/changeset_no/title/change_type/commit_hash/file_count/status/operator_name/
    reviewer_name/remark）、change_files（数组，对应 t_code_change_file 的 file_path 列）。
    可选字段：receipt_dir（字符串，相对路径按本 json 文件所在目录解析成绝对路径；仅用于
    A1 回执查找目录覆盖，不是查库字段，缺省时 A1 仍读真实 RECEIPT_DIR）。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if "changeset" not in data or "change_files" not in data:
        raise ValueError(f"registry-json 缺少必需字段 changeset/change_files: {path}")
    receipt_dir = data.get("receipt_dir")
    if receipt_dir:
        rd = Path(receipt_dir)
        data["receipt_dir"] = str(rd if rd.is_absolute() else (path.resolve().parent / rd))
    return data


# ---------------------------------------------------------------------------
# A1 · changeset 登记 + 校验官判词
# ---------------------------------------------------------------------------


def get_changeset(changeset_no: str, registry: Optional[dict[str, Any]] = None) -> Optional[dict[str, str]]:
    """registry 非空 → 离线登记模式：直接从已加载的 registry-json 里取 changeset 行，
    完全不碰 run_sql_readonly / ${DB_NAME}。只有 registry 为 None（未传 --registry-json，
    生产发单的默认路径）才会走下面的查库分支。"""
    if registry is not None:
        entry = registry.get("changeset")
        if not entry or entry.get("changeset_no") != changeset_no:
            return None
        return {str(k): ("" if v is None else str(v)) for k, v in entry.items()}
    if not CHANGESET_NO_RE.match(changeset_no):
        raise GuardBlocked(f"changeset 号格式不合法，拒绝拼入 SQL: {changeset_no!r}")
    escaped = changeset_no.replace("'", "''")
    sql = (
        "SELECT id,changeset_no,title,change_type,commit_hash,file_count,status,"
        f"operator_name,reviewer_name,remark FROM t_code_changeset WHERE changeset_no='{escaped}'"
    )
    r = run_sql_readonly(sql)
    lines = [l for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    headers = lines[0].split("\t")
    values = lines[1].split("\t")
    return dict(zip(headers, values))


def get_change_files_from_db(changeset_id: str, registry: Optional[dict[str, Any]] = None) -> list[str]:
    """registry 非空 → 离线登记模式：文件明细清单直接取 registry-json 的 change_files
    数组，不查 t_code_change_file。changeset_id 在这条路径下只是透传参数，不做类型校验
    （生产查库分支仍保留 int() 强制转换防注入，行为不变）。"""
    if registry is not None:
        return [str(f) for f in registry.get("change_files", [])]
    cid = int(changeset_id)  # 非数字直接抛异常，天然防注入
    sql = f"SELECT file_path FROM t_code_change_file WHERE changeset_id={cid}"
    r = run_sql_readonly(sql)
    lines = [l for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]
    return lines[1:] if len(lines) > 1 else []


def find_receipt_files(changeset_no: str, receipt_dir: Optional[Path] = None) -> list[Path]:
    """receipt_dir 非空时覆盖默认 RECEIPT_DIR——仅供离线登记模式（--registry-json 的
    可选 receipt_dir 字段）在 fixture 自己的目录下放回执样本用，不影响生产查库路径的
    默认行为（生产路径永远传 None，落回 RECEIPT_DIR）。"""
    base_dir = receipt_dir if receipt_dir is not None else RECEIPT_DIR
    matches = []
    if not base_dir.is_dir():
        return matches
    for p in sorted(base_dir.glob("CS-*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if changeset_no in text:
            matches.append(p)
    return matches


_PENDING_MARKERS = ["待校验官验收", "待验收", "待复核", "待校验", "校验官验收中", "校验官正在"]
_POSITIVE_MARKERS = ["放行", "通过", "批准", "PASS"]
_NEGATIVE_MARKERS = ["打回", "FAIL", "BLOCKED", "拒绝"]

# 结构化判词行（域主 <日期> 对勘裁定，A1 三级判据的第一级）：主窗口在校验官
# 判词落地时会在回执追加一行机器可读记录，形如
#   verified_by=校验官 / verdict=PASS|打回 / anchor=<40位hash> / ts=<时间>
# key 只认 ASCII 标识符，value 取到下一个空白或 '/' 为止（中文值如"校验官"本身
# 不含空白，能被 [^\s/]+ 完整捕获）。
_STRUCTURED_KV_RE = re.compile(r"([A-Za-z_]+)\s*=\s*([^\s/]+)")

# anchor 短 sha 判据（<日期> CS-<日期>-0036 对勘裁定）：判词是人写的，习惯
# 写 9 位短 sha；字面比较会把"同一个提交"误判成"锚点不一致"，拦住合规候选。git
# 短 sha 最短可唯一解析的长度理论上可低至 4，这里取 7（git 默认展示位数，业界惯用
# 的"够短够安全"下限）作为门槛，短于此不进入短 sha 校验路径，直接按不一致处理。
_SHORT_SHA_MIN_LEN = 7


def resolve_short_sha(worktree: str, short_sha: str) -> Optional[str]:
    """把 worktree 里能唯一解析的短 sha 解析成 40 位全 sha；解析失败（歧义或对象
    不存在）返回 None。只调用 run_git_readonly 白名单内的只读 `rev-parse --verify`
    ——这是 A1 anchor 比较认可短 sha 时唯一的判定依据，不允许纯字符串前缀匹配（短
    sha 理论上可能在仓库里撞到别的对象，字面前缀相同不等于就是同一个提交）。"""
    r = run_git_readonly(worktree, ["rev-parse", "--verify", f"{short_sha}^{{commit}}"])
    if r.returncode != 0:
        return None
    resolved = r.stdout.decode(errors="replace").strip()
    return resolved or None


def parse_structured_verifier_line(text: str) -> Optional[dict[str, str]]:
    """逐行扫描，找**最后一条**同时含 'verified_by=' 与 'verdict=' 的行并解析成
    字段字典；找不到返回 None。取最后一条而非第一条，是因为规则本身就是"主窗口在
    判词落地时**追加**一行"——追加意味着它出现在文档末尾，这样才不会被回执正文里
    偶尔提及/引用这个格式的说明性文字（例如样例文档自己的格式说明）误当成真正的
    判词行。存在即代表 A1 应直接采信，不再退化到下面的关键词启发式。"""
    found: Optional[dict[str, str]] = None
    for line in text.splitlines():
        if "verified_by=" in line and "verdict=" in line:
            fields = {m.group(1): m.group(2) for m in _STRUCTURED_KV_RE.finditer(line)}
            if "verdict" in fields:
                found = fields
    return found


def parse_verifier_verdict(text: str) -> dict[str, str]:
    """区分"承建方自报"和"校验官判词"——不接受自报成功是本项目自己的铁律
    （HANDOFF-Fable5-v2 §3.3 第 1 条）。检查顺序很关键：先查 PENDING 标记，
    再查负面，最后才查正面——避免"PASS（...待校验官验收...）"这种同一行里
    正负信号都出现的文本被误判为通过。

    这是 A1 三级判据的第二级（兜底启发式），只在回执里找不到结构化判词行时才
    调用——调用方须把本函数的结论标 confidence=low。"""
    lines = text.splitlines()
    verifier_lines = [l for l in lines if "校验官" in l]
    if not verifier_lines:
        status_lines = [l for l in lines if "状态" in l]
        for l in status_lines:
            if any(p in l for p in _PENDING_MARKERS):
                return {"verdict": "PENDING", "evidence": l.strip()}
        if status_lines:
            return {"verdict": "UNKNOWN", "evidence": status_lines[0].strip()}
        return {"verdict": "UNKNOWN", "evidence": "回执中未找到含'校验官'或'状态'的表述"}
    for l in verifier_lines:
        if any(p in l for p in _PENDING_MARKERS):
            return {"verdict": "PENDING", "evidence": l.strip()}
    for l in verifier_lines:
        if any(p in l for p in _NEGATIVE_MARKERS):
            return {"verdict": "FAIL", "evidence": l.strip()}
    for l in verifier_lines:
        if any(p in l for p in _POSITIVE_MARKERS):
            return {"verdict": "PASS", "evidence": l.strip()}
    return {"verdict": "UNKNOWN", "evidence": "; ".join(l.strip() for l in verifier_lines[:3])}


def decide_a1_verdict(
    text: str, registered_hash: str, worktree: str, receipt_label: str = "(样例)"
) -> dict[str, Any]:
    """A1 三级判据的决策部分，也是 check_a1_changeset_and_verifier 的核心实现。
    除 anchor 走短 sha 兜底路径时会调一次只读 `git rev-parse --verify`（见
    resolve_short_sha）外，不做任何其它文件系统/DB I/O——anchor 字面相等或明确不等
    （非短 sha 候选）两种情况完全不碰 git，仍可对样例回执文本直接单元验证。

    三级：
      ① 结构化判词行存在 → 直接采信（verified_by=校验官 且 verdict=PASS 且 anchor
         与登记 commit_hash 一致 → PASS；verdict=打回 → FAIL；anchor 不一致 → FAIL
         并明确报"判词锚点与登记锚点不一致"）。anchor 一致既接受字面相等，也接受
         判词写的是登记 commit_hash 的短 sha（长度 ≥7 且是其前缀）且经 git 验证能
         唯一解析成同一个提交（<日期> CS-<日期>-0036 对勘裁定：判词是人写的，
         习惯写 9 位短 sha，字面比较会把同一个提交误判成"锚点不一致"，拦住合规候选；
         但短 sha 理论上可能歧义，不接受纯字符串前缀匹配，必须经 git 唯一性验证）。
      ② 无结构化行 → 兜底走关键词启发式，结论标 confidence=low。
      ③ 两级都判不出（启发式仍是 UNKNOWN）→ 保守 FAIL（已含在②的 overall 计算里）。
    """
    registered_hash = (registered_hash or "").strip()
    structured = parse_structured_verifier_line(text)
    if structured is not None:
        verdict_field = structured.get("verdict", "")
        anchor_field = structured.get("anchor", "")
        verified_by_field = structured.get("verified_by", "")
        # verdict 值可能带补充说明（如 "PASS(补正复验通过,2条记档已闭合)"），
        # 判词的主裁决是前缀，括号/顿号后的内容是校验官的补充，不该因此归类失败。
        # 取前缀归一化，但原文完整保留进证据，人复盘时看得到补充说明。
        verdict_raw = verdict_field
        _vf = (verdict_field or "").strip()
        for _p in ("打回", "BLOCKED", "FAIL", "PASS"):
            if _vf.startswith(_p):
                verdict_field = _p
                break

        if verdict_field == "打回":
            return {
                "verdict": "FAIL",
                "confidence": "high",
                "evidence": (
                    f"回执 {receipt_label} 含结构化判词行：verified_by={verified_by_field} / "
                    f"verdict=打回 / anchor={anchor_field} —— 校验官已打回"
                ),
            }
        anchor_note = ""
        if anchor_field and registered_hash and anchor_field != registered_hash:
            is_short_sha_candidate = (
                len(anchor_field) >= _SHORT_SHA_MIN_LEN
                and len(registered_hash) == 40
                and registered_hash.startswith(anchor_field)
            )
            resolved_full = resolve_short_sha(worktree, anchor_field) if is_short_sha_candidate else None
            if resolved_full == registered_hash:
                anchor_note = f"（判词 anchor={anchor_field} 为短 sha，经 git 解析为 {resolved_full} 与登记一致）"
            elif is_short_sha_candidate:
                resolve_detail = (
                    "git rev-parse --verify 未能唯一解析（歧义或对象不存在）"
                    if resolved_full is None
                    else f"git rev-parse --verify 解析为 {resolved_full}，与登记 commit_hash 不同"
                )
                return {
                    "verdict": "FAIL",
                    "confidence": "high",
                    "evidence": (
                        f"回执 {receipt_label} 含结构化判词行，但判词锚点无法核验一致：anchor={anchor_field} "
                        f"疑似登记 commit_hash={registered_hash} 的短 sha，但 {resolve_detail}——短 sha 无法核验"
                        "一致，判词签的不是当前登记的这个提交"
                        "（与 RAMODEL『验收后又追加提交』同款事故的第二道防线）"
                    ),
                }
            else:
                return {
                    "verdict": "FAIL",
                    "confidence": "high",
                    "evidence": (
                        f"回执 {receipt_label} 含结构化判词行，但判词锚点与登记锚点不一致："
                        f"anchor={anchor_field} vs 登记 commit_hash={registered_hash}——判词签的不是"
                        "当前登记的这个提交（与 RAMODEL『验收后又追加提交』同款事故的第二道防线）"
                    ),
                }
        if verified_by_field == "校验官" and verdict_field == "PASS":
            return {
                "verdict": "PASS",
                "confidence": "high",
                "evidence": (
                    f"回执 {receipt_label} 含结构化判词行：verified_by=校验官 / verdict={verdict_raw} / "
                    f"anchor={anchor_field or '(空)'} 与登记 commit_hash 一致{anchor_note}"
                ),
            }
        return {
            "verdict": "FAIL",
            "confidence": "high",
            "evidence": f"回执 {receipt_label} 含结构化判词行但字段不完整/无法归类（{structured}），保守判 FAIL",
        }

    # 兜底：无结构化行，走关键词启发式，结论标 confidence=low
    verdict_info = parse_verifier_verdict(text)
    overall = "PASS" if verdict_info["verdict"] == "PASS" else "FAIL"
    reason_map = {
        "PENDING": "回执状态为承建方自报，明确写待校验官验收/复核——不接受自报成功",
        "FAIL": "校验官判词为打回/FAIL",
        "UNKNOWN": "回执中未能明确解析出校验官判词（需人工确认）",
        "PASS": "校验官判词为通过/放行",
    }
    return {
        "verdict": overall,
        "confidence": "low",
        "evidence": (
            f"回执 {receipt_label}: 「{verdict_info['evidence']}」 —— {reason_map[verdict_info['verdict']]}"
            "（未找到 verified_by= 结构化判词行，本结论来自文本启发式，建议补该行后重跑）"
        ),
    }


def check_a1_changeset_and_verifier(
    worktree: str, changeset_no: str, registry: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    changeset = get_changeset(changeset_no, registry)
    if changeset is None:
        return {
            "check": "A1_changeset_registered_and_verified",
            "verdict": "FAIL",
            "confidence": "n/a",
            "evidence": f"t_code_changeset 中未找到 changeset_no={changeset_no}",
            "changeset": None,
            "receipts": [],
        }
    # 离线登记模式下，registry 可选携带 receipt_dir（load_registry 已解析成绝对路径），
    # 覆盖回执查找目录；不带则落回真实 RECEIPT_DIR——回执查找本来就是文件系统读取，
    # 不是查库，这个覆盖只是为了让 fixture 不必污染共享回执目录就能验出 A1 PASS 分支。
    receipt_dir = Path(registry["receipt_dir"]) if (registry is not None and registry.get("receipt_dir")) else None
    receipts = find_receipt_files(changeset_no, receipt_dir)
    if not receipts:
        return {
            "check": "A1_changeset_registered_and_verified",
            "verdict": "FAIL",
            "confidence": "n/a",
            "evidence": f"changeset 已登记（id={changeset.get('id')}），但回执目录未找到提及 {changeset_no} 的文件——无校验官判词证据",
            "changeset": changeset,
            "receipts": [],
        }
    # [<日期> 修] 多份回执候选时的选取规则。
    # 原实现按 mtime 取"最新修改"的那份——**这是错的**：文件修改时间与
    # "这份回执属不属于本单"毫无关系。实证：主窗口补录 SOFLOW2 的判词行后，
    # 该文件 mtime 变新，于是 0055 的 A1 读到了 SOFLOW2 的判词（锚点是别单的），
    # 判成"判词锚点与登记不一致"——用 A 单的判词去判 B 单。
    # 正解：优先选"判词锚点与本单登记 commit_hash 一致"的那份；都不匹配才回退 mtime。
    receipts_sorted = sorted(receipts, key=lambda p: p.stat().st_mtime, reverse=True)
    registered_hash = changeset.get("commit_hash", "") or ""
    primary = None
    _anchor_pick_note = ""
    for _p in receipts_sorted:
        try:
            _t = _p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _st = parse_structured_verifier_line(_t)
        _a = (_st or {}).get("anchor", "")
        if _a and registered_hash and (registered_hash == _a or registered_hash.startswith(_a)):
            primary = _p
            _anchor_pick_note = f"（扫到 {len(receipts_sorted)} 份提及本单的回执，按判词锚点匹配选中 {_p.name}）"
            break
    if primary is None:
        primary = receipts_sorted[0]
        if len(receipts_sorted) > 1:
            _anchor_pick_note = f"（扫到 {len(receipts_sorted)} 份提及本单的回执，无一份的判词锚点与登记一致，回退取最近修改的 {primary.name}）"
    text = primary.read_text(encoding="utf-8", errors="replace")
    decision = decide_a1_verdict(text, registered_hash, worktree, primary.name)
    if _anchor_pick_note:
        decision["evidence"] = decision["evidence"] + _anchor_pick_note
    return {
        "check": "A1_changeset_registered_and_verified",
        "verdict": decision["verdict"],
        "confidence": decision["confidence"],
        "evidence": decision["evidence"],
        "changeset": changeset,
        "receipts": [str(p) for p in receipts_sorted],
    }


# ---------------------------------------------------------------------------
# A2 · 并线锚点只认登记的 commit_hash（含文件清单交叉核）
# ---------------------------------------------------------------------------

BASELINE_SEARCH_MAX_N = 50


def resolve_registered_baseline(
    worktree: str, head: str, db_files: set[str], max_n: int = BASELINE_SEARCH_MAX_N
) -> dict[str, Any]:
    """基线计算——复用 ${TRAJ_HOME}/graphs/changeset-audit.v5.1.json 里 n9/n9b
    两个节点已经解决过的同一问题（多提交特性分支的基线不是 `^1`），不再另写一套算法。

    [<日期> 三级候选修订] CS-<日期>-0052 实证暴露：旧版只有『^1 快路径 + 沿第一
    父链 head~N 搜索』两段，含 merge commit 的多提交分支第一父链从 head 起怎么搜都到
    不了 merge-base，50 层内必然搜空，把真实交付误判 FAIL；而
    `git diff --name-only merge-base(集成分支, head) head` 与登记明细逐字相等——
    merge-base 是唯一能复现登记明细的基线。改为三级候选按序试，每级都要求『该基线到
    head 的文件集合与登记明细(db_files)逐字相等』：

      ①head^1（单提交快路径）：若 `git diff --name-only head^1 head` 的文件集合
      与登记明细逐字相等 → 直接采用。
      ②merge-base(INTEGRATION_BRANCH, head)（多提交分支标准基线，本次新增）：
      与①同样要求文件集合逐字相等才采用；不匹配或无法解析则跳过，试③。
      ③沿第一父链搜 `head~N`（N=2..max_n，与 n9b 搜索范围一致），找到某个 N 使
      文件集合与登记明细逐字相等 → 采用，返回 resolved_baseline=该 `head~N` 的
      完整 40 位 sha 与命中的候选来源 via。
      三级都不中（或第③级提前越过仓库起点，`rev-parse` 失败）→ 宣告 NOT_FOUND，
      连同已搜索范围和最后一次尝试的文件集合一并返回，供上层拼出双方差集证据。

    这是本函数的唯一职责：找一个能复现登记明细的基线。找不到不代表本函数出错，
    代表数据真的对不上（n9b 的 not_found 分支）——由调用方判 FAIL。

    显式防呆：candidate base 不允许等于 head（<日期> CS-<日期>-0032 实证的
    bug 根因——旧口径 `merge-base(集成分支, HEAD)` 在某些拓扑下会算出等于 HEAD
    自己的 sha，导致 `git diff head head` 必然空）。遇到 base==head 的候选一律跳过，
    不当作可用基线，继续试下一级候选。
    """
    last_base = ""
    last_actual: set[str] = set()

    def try_candidate(base_sha: str) -> tuple[bool, set[str]]:
        """base_sha 为空/等于 head 时视为该候选不可用，返回 (False, set())，
        不更新 last_base/last_actual（退化候选不该盖掉此前更有信息量的尝试记录）。"""
        nonlocal last_base, last_actual
        if not base_sha or base_sha == head:
            return False, set()
        actual = set(get_changed_files(worktree, base_sha, head))
        last_base, last_actual = base_sha, actual
        return actual == db_files, actual

    # ①head^1：单提交快路径
    rp1 = run_git_readonly(worktree, ["rev-parse", f"{head}^1"])
    base1 = rp1.stdout.decode(errors="replace").strip()
    if rp1.returncode == 0 and base1:
        hit, actual1 = try_candidate(base1)
        if hit:
            return {"found": True, "baseline": base1, "via": "^1", "actual_files": actual1}

    # ②merge-base(INTEGRATION_BRANCH, head)：多提交分支标准基线。刻意不用 get_merge_base()
    # 助手函数——它内部对第二个参数硬编码字面 "HEAD"，取的是 worktree 当前实际检出位置，
    # 不是本函数入参的 head。调用方目前恒等（check_a2_anchor 传入的 worktree 检出的就是
    # head 本身），但显式传 head 更严谨——不依赖"worktree 当前检出状态==head"这条隐藏前提，
    # 也不给两次读 HEAD 之间的检出状态漂移留竞态窗口。
    rmb = run_git_readonly(worktree, ["merge-base", INTEGRATION_BRANCH, head])
    mb = rmb.stdout.decode(errors="replace").strip() if rmb.returncode == 0 else ""
    if mb:
        hit, actual_mb = try_candidate(mb)
        if hit:
            return {"found": True, "baseline": mb, "via": "merge-base", "actual_files": actual_mb}

    # ③沿第一父链 head~N (N=2..max_n) 兜底搜索
    tried_n = 1  # ①已相当于 N=1(head^1 == head~1)，搜索范围记录与旧版口径一致
    for n in range(2, max_n + 1):
        rp = run_git_readonly(worktree, ["rev-parse", f"{head}~{n}"])
        base_n = rp.stdout.decode(errors="replace").strip()
        if rp.returncode != 0 or not base_n:
            break  # 越过仓库起点（或 rev-parse 失败），停止搜索
        tried_n = n
        hit, actual_n = try_candidate(base_n)
        if hit:
            return {"found": True, "baseline": base_n, "via": f"~{n}", "actual_files": actual_n}
    return {
        "found": False,
        "searched_n": f"^1,merge-base,~2..{tried_n}" if tried_n > 1 else "^1,merge-base（均未命中；第一父链未搜到 N=2 即越过仓库起点或首次 rev-parse 即失败）",
        "last_baseline": last_base,
        "last_actual_files": last_actual,
    }


def check_a2_anchor(
    worktree: str, changeset: Optional[dict[str, str]], registry: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    if changeset is None:
        return {"check": "A2_anchor_matches_registered_commit_hash", "verdict": "FAIL",
                "evidence": "changeset 未登记，无法比对锚点", "resolved_baseline": None}
    head = get_worktree_head(worktree)
    db_hash = changeset.get("commit_hash", "") or ""
    if head != db_hash:
        return {
            "check": "A2_anchor_matches_registered_commit_hash",
            "verdict": "FAIL",
            "evidence": (
                f"worktree HEAD={head} 与登记 commit_hash={db_hash} 不一致——"
                "候选在验收登记后又被追加/变更了提交（与 <日期> RAMODEL『验后又长三笔』同款教训）"
            ),
            "resolved_baseline": None,
        }
    changeset_id = changeset.get("id", "")
    try:
        db_files = set(get_change_files_from_db(changeset_id, registry)) if changeset_id else set()
    except (GuardBlocked, ValueError):
        db_files = set()
    if not db_files:
        return {
            "check": "A2_anchor_matches_registered_commit_hash",
            "verdict": "FAIL",
            "evidence": f"HEAD 与登记 commit_hash 一致，但 t_code_change_file 里查不到该 changeset 的明细文件，无法交叉核实",
            "resolved_baseline": None,
        }

    resolved = resolve_registered_baseline(worktree, head, db_files)
    if resolved["found"]:
        base = resolved["baseline"]
        # 断言：绝不允许 base 与 head 相同。resolve_registered_baseline 内部已经把
        # base==head 的候选剔除，这里是防御性第二道闸——一旦真的触发说明算法出现了
        # 未预期的路径，直接记 RUNNER_ERR 并判 FAIL，不允许像原 bug 那样静默产出空 diff。
        if base == head:
            return {
                "check": "A2_anchor_matches_registered_commit_hash",
                "verdict": "FAIL",
                "evidence": f"RUNNER_ERR：算出的基线与 HEAD 相同（base=head={head}），拒绝静默产出空 diff，已阻断",
                "resolved_baseline": None,
            }
        via = resolved["via"]
        note_map = {
            "^1": "",
            "merge-base": f"（快路径 sha^1 未命中，多提交分支标准基线 merge-base({INTEGRATION_BRANCH}) 命中）",
        }
        note = note_map.get(via, f"（快路径与 merge-base 均未命中，沿第一父链搜索命中 {via}，多提交特性分支）")
        return {
            "check": "A2_anchor_matches_registered_commit_hash",
            "verdict": "PASS",
            "evidence": (
                f"HEAD={head} 与登记 commit_hash 一致；resolved_baseline={base} via={via}{note}；"
                f"文件清单 {len(resolved['actual_files'])} 个逐字对上"
            ),
            "resolved_baseline": base,
        }

    last_actual = resolved.get("last_actual_files", set())
    only_db = sorted(db_files - last_actual)
    only_actual = sorted(last_actual - db_files)
    return {
        "check": "A2_anchor_matches_registered_commit_hash",
        "verdict": "FAIL",
        "evidence": (
            f"锚点 sha 一致，但三级候选基线(尝试={resolved['searched_n']}) 均找不到能让文件清单与登记明细"
            f"逐字相等的基线（<日期> 校验官裁定的锚点口径要求逐字对上）。以最后尝试的基线 "
            f"{resolved.get('last_baseline') or '(无)'} 为例：仅登记未见于实际改动: {only_db or '无'}；"
            f"仅实际改动未登记: {only_actual or '无'}"
        ),
        "resolved_baseline": None,
    }


# ---------------------------------------------------------------------------
# A3 · premerge-gate.v6 四查（与 ${TRAJ_HOME}/graphs/premerge-gate.v6.json 逐字一致）
# ---------------------------------------------------------------------------


_MIDSTATE_MARKERS = ["CHERRY_PICK_HEAD", "MERGE_HEAD", "REBASE_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"]
# [<日期> 修] 原判据 r"^(<<<<<<<|>>>>>>>|=======)$" 要求整行**恰好等于**那七个字符。
# 用 fixture 实测（F-A3-G3a，把 git 真实写出的冲突块提交进仓库）：
#   ^<<<<<<<$ 命中 0 个文件、^>>>>>>>$ 命中 0 个文件、^=======$ 命中 1 个。
# 因为 git 写出的开头/结尾标记必带标签（`<<<<<<< HEAD` / `>>>>>>> other/side`），
# 带后缀的行与"整行恰好等于"匹配不上——**三个分支里只有中间那行光秃秃的等号在生效**。
# 而 markdown 的 setext 一级标题下划线正是一串 '='，恰好七个时与判据完全重合：
# F-A3-G3b 用一份正常文档实测被判 FAIL，即误报，会把合规候选整个拦下。
#
# 改法：开头/结尾/diff3 基线三种标记一律允许带标签后缀（这样它们才真正生效），
# 同时**移除光秃秃的 `=======`**——真实冲突块必定同时带开头与结尾标记，去掉中间那行
# 不损失检出力（F-A3-G3a 与 F-A3-G2b 实测仍命中），却消掉了与正常文档唯一的撞车面。
# 已知边界：正文里合法展示冲突块示例的教学文档仍会被命中，属可接受的显性误报
# （人一眼能判），不像 setext 标题那样毫无线索。
_CONFLICT_MARKER_RE = r"^(<<<<<<<|>>>>>>>|\|\|\|\|\|\|\|)( .*)?$"


def check_g1_tree_clean(worktree: str) -> dict[str, Any]:
    r = run_git_readonly(worktree, ["status", "--porcelain"])
    lines = [l for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]
    bad = [l for l in lines if not l.startswith("??")]
    ok = len(bad) == 0
    return {
        "check": "g1_tree_clean",
        "verdict": "PASS" if ok else "FAIL",
        "evidence": ("\n".join(lines) if lines else "(status --porcelain 空输出)")
        if not ok
        else (f"{len(lines)} 行未跟踪文件（'??'），无脏改动/冲突码" if lines else "工作区完全干净"),
    }


def check_g2_no_midstate(worktree: str) -> dict[str, Any]:
    gitdir_raw = get_git_dir(worktree)
    gitdir_path = Path(gitdir_raw) if Path(gitdir_raw).is_absolute() else Path(worktree) / gitdir_raw
    present = [m for m in _MIDSTATE_MARKERS if (gitdir_path / m).exists()]
    return {
        "check": "g2_no_midstate",
        "verdict": "FAIL" if present else "PASS",
        "evidence": f"git-dir={gitdir_path}; 存在的中态标记: {present if present else '(无)'}",
    }


def check_g3_no_conflict_markers(worktree: str) -> dict[str, Any]:
    r = run_git_readonly(worktree, ["grep", "-l", "-E", _CONFLICT_MARKER_RE, "--", "."])
    hits = [l for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]
    return {
        "check": "g3_no_conflict_markers",
        "verdict": "FAIL" if hits else "PASS",
        "evidence": ("命中文件: " + ", ".join(hits)) if hits else "全仓无冲突标记命中",
    }


def check_g4_lineending(worktree: str, merge_base: Optional[str], head: str) -> dict[str, Any]:
    if merge_base is None:
        return {"check": "g4_lineending", "verdict": "FAIL", "evidence": "无法计算 merge-base，行尾判据无法执行"}
    r = run_lineending_check(worktree, merge_base, head)
    out = r.stdout.decode(errors="replace")
    m = re.search(r"overall_verdict=(\w+)", out)
    overall = m.group(1) if m else "UNKNOWN"
    # AMBIGUOUS 在生产图里会转 n9_llm 语义裁决；只读生成器不具备语义裁决能力，
    # 按规格遇到 AMBIGUOUS 一律判 FAIL 走人工路，不代为放行。
    verdict = "PASS" if overall == "PASS" else "FAIL"
    return {"check": "g4_lineending", "verdict": verdict, "evidence": out.strip(), "raw_overall": overall}


def check_a3_premerge_gate(worktree: str, merge_base: Optional[str], head: str) -> dict[str, Any]:
    g1 = check_g1_tree_clean(worktree)
    g2 = check_g2_no_midstate(worktree)
    g3 = check_g3_no_conflict_markers(worktree)
    g4 = check_g4_lineending(worktree, merge_base, head)
    subs = [g1, g2, g3, g4]
    overall = "PASS" if all(s["verdict"] == "PASS" for s in subs) else "FAIL"
    return {"check": "A3_premerge_gate_v6", "verdict": overall, "sub": subs}


# ---------------------------------------------------------------------------
# A4 · 新判据：Java 实体新增 private 字段必须有出口（DDL 或 exist=false）
# ---------------------------------------------------------------------------

_FIELD_ADD_RE = re.compile(r"^\+\s*private\s+\S+\s+(\w+)\s*(=.*)?;")
_FIELD_DECL_RE_TMPL = r"private\s+\S+\s+{}\s*(=.*)?;"
_TABLEFIELD_EXISTFALSE_RE = re.compile(r"@TableField\s*\(\s*exist\s*=\s*false\s*\)")
_ADD_COLUMN_RE = re.compile(r"(?i)ADD\s+COLUMN\s+`?(\w+)`?")


def _to_snake_case(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return s.lower()


def _has_existfalse_immediately_above(head_lines: list[str], field_idx: int, max_lookback: int = 6) -> bool:
    """从字段声明行往上找它自己的注解：跳过空行/注释行，遇到注解行('@'开头)就检查是不是
    @TableField(exist=false)，是则命中；遇到任何非注解/非注释/非空的真代码行（比如上一个
    字段自己的声明）立刻停止，不跨过去认前一个字段的注解。

    这是修复过一次的逻辑：最初版本用"往上数 3 行"的固定窗口，在真实历史事故提交
    dfb149d1d（<事故编号> 的引入提交）上被证伪——ProductPrice.java 里新增的
    processTempLabel 字段上方隔着一行 Javadoc 注释、再上方是*上一个字段* productName
    自己的 @TableField(exist=false)，固定窗口会把那个不相关的注解也算进来，导致漏判。
    改成"沿非代码行(空白/注释/注解)持续上溯，遇到真代码行立即停"后，用该历史提交重新
    验证：processTempLabel 正确判定为无注解。
    """
    j = field_idx - 1
    steps = 0
    while j >= 0 and steps < max_lookback:
        stripped = head_lines[j].strip()
        if stripped == "" or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            j -= 1
            steps += 1
            continue
        if stripped.startswith("@"):
            if _TABLEFIELD_EXISTFALSE_RE.search(stripped):
                return True
            j -= 1
            steps += 1
            continue
        break  # 撞到真代码行（如上一个字段的声明），停止上溯，不算它的注解
    return False


def check_a4_entity_existfalse(worktree: str, merge_base: Optional[str], head: str) -> dict[str, Any]:
    if merge_base is None:
        return {"check": "A4_entity_new_field_needs_exit", "verdict": "FAIL",
                "evidence": "无法计算 merge-base，无法扫描", "violations": []}
    changed = get_changed_status(worktree, merge_base, head)
    entity_files = [fp for status, fp in changed if status.startswith("M") and re.search(r"/entity/[^/]+\.java$", fp)]
    if not entity_files:
        return {
            "check": "A4_entity_new_field_needs_exit",
            "verdict": "PASS",
            "evidence": "本批未触及既有实体文件（不适用）",
            "violations": [],
        }

    sql_files = [fp for _status, fp in changed if fp.endswith(".sql")]
    added_columns: set[str] = set()
    for sf in sql_files:
        rc = run_git_readonly(worktree, ["show", f"{head}:{sf}"])
        content = rc.stdout.decode(errors="replace")
        for m in _ADD_COLUMN_RE.finditer(content):
            added_columns.add(m.group(1).lower())

    violations = []
    skipped_rewrite_files = []
    for fp in entity_files:
        # --ignore-cr-at-eol：与 A3.g4/lineending_check.py 同一约定——不带这个 flag，
        # 内容没变只是行尾变了的行也会被 diff 打成"整行删除+整行新增"（3行真实改动
        # 出97行diff的现象），会把字段级"新增"检测彻底冲乱。
        rdiff = run_git_readonly(worktree, ["diff", "--ignore-cr-at-eol", merge_base, head, "--", fp])
        diff_lines = rdiff.stdout.decode(errors="replace").splitlines()
        plus_lines = [l for l in diff_lines if l.startswith("+") and not l.startswith("+++")]

        rhead = run_git_readonly(worktree, ["show", f"{head}:{fp}"])
        head_lines = rhead.stdout.decode(errors="replace").splitlines()

        # 疑似整体重写防呆：常见诱因是行尾被整体转换（CRLF<->LF），此时 git 逐行 diff
        # 会把几乎全文件都标成新增行，字段级"新增"检测在这种输入下不可靠。真实教训：
        # <日期> PTL-P6 一次 DeliveryItem.java 的 CRLF 提交曾把全文件标成"新增"，
        # 被校验官第7条打回、改成只留 3 行真实新增——这种输入交给专门的行尾判据
        # （A3.g4 / lineending_check.py）把关，这里只跳过并记录，不产生字段级误报。
        if head_lines and len(plus_lines) > 0.6 * len(head_lines):
            skipped_rewrite_files.append(fp)
            continue

        added_fields = [m.group(1) for l in plus_lines if (m := _FIELD_ADD_RE.match(l))]
        if not added_fields:
            continue
        for field in added_fields:
            decl_re = re.compile(_FIELD_DECL_RE_TMPL.format(re.escape(field)))
            idx = next((i for i, l in enumerate(head_lines) if decl_re.search(l)), None)
            has_annotation = _has_existfalse_immediately_above(head_lines, idx) if idx is not None else False
            if has_annotation:
                continue
            if _to_snake_case(field) in added_columns:
                continue
            violations.append({"file": fp, "field": field})

    rewrite_note = (
        f"；另有 {len(skipped_rewrite_files)} 个文件疑似整体重写（如 CRLF 转换），"
        f"已跳过细粒度字段扫描、交 A3.g4 行尾判据把关: {skipped_rewrite_files}"
        if skipped_rewrite_files
        else ""
    )
    if violations:
        detail = "; ".join(f"{v['file']}::{v['field']}" for v in violations)
        return {
            "check": "A4_entity_new_field_needs_exit",
            "verdict": "FAIL",
            "evidence": f"新增字段既无 @TableField(exist=false) 也无对应 DDL（与 <事故编号> 同款）: {detail}{rewrite_note}",
            "violations": violations,
        }
    scanned = len(entity_files) - len(skipped_rewrite_files)
    return {
        "check": "A4_entity_new_field_needs_exit",
        "verdict": "PASS",
        "evidence": f"扫描 {scanned} 个修改过的实体文件，新增字段均有 @TableField(exist=false) 或对应 DDL{rewrite_note}",
        "violations": [],
    }


# ---------------------------------------------------------------------------
# A5 · NEEDS_HUMAN 触发：候选含 DB 迁移
# ---------------------------------------------------------------------------


def check_a5_migration_trigger(worktree: str, merge_base: Optional[str], head: str) -> dict[str, Any]:
    if merge_base is None:
        return {"check": "A5_migration_trigger", "triggered": False, "files": [],
                "evidence": "无法计算 merge-base，跳过迁移扫描"}
    files = get_changed_files(worktree, merge_base, head)
    migration_files = [f for f in files if re.match(r"^db/.*\.sql$", f) or "migration" in f.lower()]
    return {
        "check": "A5_migration_trigger",
        "triggered": bool(migration_files),
        "files": migration_files,
        "evidence": f"命中 {len(migration_files)} 个迁移文件: {migration_files}" if migration_files else "未触及 db/*.sql",
    }


# ---------------------------------------------------------------------------
# 触及范围探测 + 串行锁状态（只读展示）
# ---------------------------------------------------------------------------


def detect_touch(files: list[str]) -> dict[str, bool]:
    return {
        "frontend": any(f.startswith("frontend/") for f in files),
        "backend": any(f.startswith("src/main/java/") or f == "pom.xml" for f in files),
    }


def read_lock_status() -> str:
    if not LOCK_FILE.is_file():
        return "当前无锁（`.locks/CURRENT.lock` 不存在），可安全获取"
    try:
        content = LOCK_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"锁文件存在但读取失败: {e}"
    acquired_at = None
    ttl = LOCK_TTL_SECONDS_DEFAULT
    for line in content.splitlines():
        if line.startswith("acquired_at="):
            acquired_at = line.split("=", 1)[1].strip()
        if line.startswith("ttl_seconds="):
            try:
                ttl = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    stale_note = ""
    if acquired_at:
        try:
            acq = datetime.datetime.strptime(acquired_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            age = (datetime.datetime.now(datetime.timezone.utc) - acq).total_seconds()
            stale_note = f"（已持有 {int(age)} 秒，TTL={ttl} 秒，{'已判僵死可清理' if age > ttl else '仍在有效期内，不得抢占'}）"
        except ValueError:
            stale_note = "（acquired_at 格式无法解析，需人工核实）"
    return f"当前存在锁文件{stale_note}：\n```\n{content.strip()}\n```"


# ---------------------------------------------------------------------------
# B5-P · 探针路径反查（三级，见 PIPELINE-SPEC.md §B5-P；<日期> postmortem-0045
# 逐条比对抓到生成器仍在吐占位符后补做）
# ---------------------------------------------------------------------------

# 前端 api 定义文件：本仓库 api 目录是扁平结构（未见嵌套子目录），只匹配
# frontend/src/api/*.ts 一层。
_FRONTEND_API_TS_RE = re.compile(r"^frontend/src/api/([^/]+)\.ts$")

# request.get(...) 调用：允许中间夹一段泛型实参 request.get<Foo>(...)（本仓库
# delivery.ts 的 previewDeliveryBatchAllocation 就是这种写法），只认引号/反引号
# 包起来的第一个实参，不解析后面的 axios config。
_TS_GET_CALL_RE = re.compile(r"request\.get\s*(?:<[^()]*>)?\s*\(\s*(['\"`])((?:\\.|(?!\1).)*)\1")

# Controller 文件：任意深度的 .../controller/<Name>.java。
_CONTROLLER_FILE_RE = re.compile(r"/controller/[^/]+\.java$")

# Service/ServiceImpl 文件（用于同名反查同目录树下的 Controller，见下方
# _derive_controller_candidate）：要求路径里有 /service/ 段，可选再带一层 impl/。
_SERVICE_FILE_RE = re.compile(r"^(?P<pkg>.*)/service/(?:impl/)?(?P<cls>[A-Za-z0-9_]+)\.java$")

_CLASS_DECL_RE = re.compile(r"\bclass\s+\w+\b")
_CLASS_REQUEST_MAPPING_RE = re.compile(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"')
_GET_MAPPING_RE = re.compile(r'@GetMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"')

# vue 文件里 import ... from '@/api/xxx' 的模块名。
_VUE_API_IMPORT_RE = re.compile(r"""from\s+['"]@/api/([A-Za-z0-9_\-]+)['"]""")

# 路径变量：Java 的 {id}、TS 模板字符串里的 ${id}（后者字面上也含 "{id}" 子串，
# 同一个正则可以覆盖两种写法）。命中即视为动态路径，不当静态探针直接给。
_DYNAMIC_PATH_RE = re.compile(r"\{[^{}]*\}")


def _read_file_at_ref(worktree: str, ref: str, path: str) -> Optional[str]:
    """经唯一允许的 git show 入口读 <ref>:<path> 的内容；文件在该 ref 不存在/
    读取失败时返回 None——反查候选允许"猜的位置不存在"，由调用方跳过、不抛异常。"""
    r = run_git_readonly(worktree, ["show", f"{ref}:{path}"])
    if r.returncode != 0:
        return None
    return r.stdout.decode(errors="replace")


def _extract_get_paths_from_ts(content: str) -> list[str]:
    paths = []
    for m in _TS_GET_CALL_RE.finditer(content):
        raw = m.group(2)
        paths.append(raw if raw.startswith("/") else "/" + raw)
    return paths


def _extract_controller_get_paths(content: str) -> tuple[str, list[str]]:
    """返回 (类级前缀, [方法级 GET 路径])。类级前缀取紧邻 class 声明之前注解簇里
    最后一个 @RequestMapping；向上扫描一旦碰到独立一行的 '}'，说明已经越过上一个
    成员，判定该 Controller 没有类级 @RequestMapping（PIPELINE-SPEC.md §B5-P 点名
    的 ProductRepertoryController 同款坑），前缀返回空串——调用方据此直接用方法
    路径本身，不许按类名推前缀。"""
    lines = content.splitlines()
    class_idx = next((i for i, l in enumerate(lines) if _CLASS_DECL_RE.search(l)), len(lines))
    prefix = ""
    for i in range(class_idx - 1, -1, -1):
        m = _CLASS_REQUEST_MAPPING_RE.search(lines[i])
        if m:
            prefix = m.group(1)
            break
        if lines[i].strip() == "}":
            break
    method_paths = [m.group(1) for m in _GET_MAPPING_RE.finditer(content)]
    return prefix, method_paths


def _combine_controller_path(prefix: str, method_path: str) -> str:
    mp = method_path if method_path.startswith("/") else "/" + method_path
    if not prefix:
        return mp
    pre = prefix if prefix.startswith("/") else "/" + prefix
    return pre.rstrip("/") + mp


def _derive_controller_candidate(fp: str) -> Optional[tuple[str, str]]:
    """改动的是 Service/ServiceImpl 而不是 Controller 本身时，按同包同名约定
    （XxxServiceImpl/XxxService -> controller/XxxController.java）猜一个候选
    Controller 文件路径。返回 (候选路径, 剥离出的 domain 名)；剥不出后缀（说明
    这压根不是 Service/ServiceImpl 命名）时返回 None。注意：这里"猜"的只是该看
    哪个文件，不是猜 URL 文本本身——候选文件读不到就此放弃，读到了也只提取它
    里面真实的注解，不拼命名习惯进最终路径。"""
    m = _SERVICE_FILE_RE.match(fp)
    if not m:
        return None
    cls = m.group("cls")
    domain = re.sub(r"(ServiceImpl|Impl|Service)$", "", cls)
    if not domain or domain == cls:
        return None
    return f"{m.group('pkg')}/controller/{domain}Controller.java", domain


def reverse_lookup_probe_paths(worktree: str, head: str, changed_files: list[str]) -> list[dict[str, Any]]:
    """B5-P 三级反查主入口。按序尝试（每个改动文件按自己的形态适用其中一级，互不
    排斥，都能命中就都收），输出已渲染的真实探针路径列表；每条记录：
      path   —— 不含 /api 前缀（渲染 curl 时统一拼 http://.../api + path）
      dynamic —— True 表示路径含未替换的 {id}/${id} 变量，不能直接探，需人工填参
      source —— 人读来源说明（改动文件路径 + 反查方式，用于 curl 行内注释）
      level  —— 1=前端 api 定义 / 2=Controller 注解（含同名 Service->Controller
                扩展）/ 3=vue import 间接反查
    三级都拿不到 -> 返回空列表，由调用方触发 B5 兜底标注，不在这里编造占位符。
    """
    results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    tried_candidates: set[str] = set()

    def _add(path: str, source: str, level: int) -> None:
        if path in seen_paths:
            return
        seen_paths.add(path)
        results.append({
            "path": path,
            "dynamic": bool(_DYNAMIC_PATH_RE.search(path)),
            "source": source,
            "level": level,
        })

    # 第 1/2 级：改动文件本身就是前端 api 定义或 Controller。
    for fp in changed_files:
        if _FRONTEND_API_TS_RE.match(fp):
            content = _read_file_at_ref(worktree, head, fp)
            if content is not None:
                for p in _extract_get_paths_from_ts(content):
                    _add(p, fp, 1)
        elif _CONTROLLER_FILE_RE.search(fp):
            content = _read_file_at_ref(worktree, head, fp)
            if content is not None:
                prefix, method_paths = _extract_controller_get_paths(content)
                for mp in method_paths:
                    _add(_combine_controller_path(prefix, mp), fp, 2)

    # 第 2 级扩展：改动的是 Service/ServiceImpl，Controller 本身没改，按同名约定
    # 反查同目录树下的 Controller 文件（读到真实文件才提取真实注解，见函数注释）。
    for fp in changed_files:
        if _CONTROLLER_FILE_RE.search(fp):
            continue  # 已在上面按直接 Controller 处理过
        derived = _derive_controller_candidate(fp)
        if derived is None:
            continue
        candidate, _domain = derived
        if candidate in tried_candidates:
            continue
        tried_candidates.add(candidate)
        content = _read_file_at_ref(worktree, head, candidate)
        if content is None:
            continue
        prefix, method_paths = _extract_controller_get_paths(content)
        source = f"{candidate}（改动文件 {fp} 按同名 Service→Controller 反查得到，Controller 本身未在本批改动文件清单内）"
        for mp in method_paths:
            _add(_combine_controller_path(prefix, mp), source, 2)

    # 第 3 级：全批改动文件里一个前端 api ts / Controller 都没有时，退到改动的
    # .vue 文件，找它 import 的 @/api/xxx 模块，回到第 1 级解析那个 api 文件
    # （该 api 文件本身可能未被本批改动，这正是"间接"反查的意义）。
    has_direct_backend_or_api = any(
        _FRONTEND_API_TS_RE.match(fp) or _CONTROLLER_FILE_RE.search(fp) for fp in changed_files
    )
    # [<日期> 修] 间接反查改为"总是跑"。
    # 原条件 `not has_direct_backend_or_api and not results` 会在整批里存在
    # 任一 api ts / Controller 时整体跳过 .vue 间接反查——实测三单并集（含
    # api/delivery.ts）只反查出 3 条，sales-order/sales-outbound/collection
    # 等页面的接口全漏；逐个 .vue 单独反查才补齐到 35 条。
    # 缺口的危险在于它不报错、只少给路径：探针照样全绿，少验的那块是沉默的。
    if True:
        for fp in changed_files:
            if not fp.endswith(".vue"):
                continue
            vue_content = _read_file_at_ref(worktree, head, fp)
            if vue_content is None:
                continue
            for mod in _VUE_API_IMPORT_RE.findall(vue_content):
                api_path = f"frontend/src/api/{mod}.ts"
                api_content = _read_file_at_ref(worktree, head, api_path)
                if api_content is None:
                    continue
                source = f"{api_path}（改动文件 {fp} import 的 @/api/{mod} 间接反查得到，该 api 文件本身未在本批改动文件清单内）"
                for p in _extract_get_paths_from_ts(api_content):
                    _add(p, source, 3)

    return results


# ---------------------------------------------------------------------------
# 渲染操作单
# ---------------------------------------------------------------------------


def _verdict_table(rows: list[tuple[str, str, str]]) -> str:
    out = ["| 检查项 | 结论 | 证据 |", "|---|---|---|"]
    for name, verdict, evidence in rows:
        ev = evidence.replace("\n", "<br>").replace("|", "\\|")
        out.append(f"| {name} | **{verdict}** | {ev} |")
    return "\n".join(out)


def render_worksheet(
    worktree: str,
    changeset_no: str,
    a1: dict[str, Any],
    a2: dict[str, Any],
    a3: dict[str, Any],
    a4: dict[str, Any],
    a5: dict[str, Any],
    head: str,
    branch: str,
    merge_base: Optional[str],
    changed_files: list[str],
) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    changeset = a1.get("changeset")
    title = changeset.get("title", "") if changeset else "(未登记)"

    if a5["triggered"]:
        gate = "NEEDS_HUMAN"
    elif all(x["verdict"] == "PASS" for x in (a1, a2, a3, a4)):
        gate = "PASS"
    else:
        gate = "FAIL"

    # B5-P 反查在准入判定之外独立跑（不影响 gate，只影响 B5 段渲染成什么），提前
    # 算好供下方"准入结论"区的降级提示与后面的 B5 段共用同一份结果。
    b5_probes = reverse_lookup_probe_paths(worktree, head, changed_files)

    lines: list[str] = []
    lines.append(f"# 操作单 · {changeset_no}")
    lines.append("")
    lines.append(f"- 生成时间（UTC）：{now}")
    lines.append(f"- 候选 worktree：`{worktree}`")
    lines.append(f"- 候选分支：`{branch}`")
    lines.append(f"- 候选 HEAD：`{head}`")
    lines.append(f"- changeset 标题：{title}")
    lines.append(f"- 集成分支：`{INTEGRATION_BRANCH}`（worktree `{INTEGRATION_WORKTREE}`）")
    lines.append(f"- merge-base：`{merge_base or '(无法计算)'}`")
    lines.append(f"- 改动文件（{len(changed_files)} 个）：" + ("、".join(f"`{f}`" for f in changed_files) if changed_files else "(无)"))
    lines.append("")
    lines.append(f"## 准入结论：**{gate}**")
    lines.append("")

    a3_rows = [(f"A3.{s['check']}", s["verdict"], s["evidence"]) for s in a3["sub"]]
    rows = [
        ("A1 changeset 登记且校验官判词为 PASS", a1["verdict"], a1["evidence"]),
        ("A2 并线锚点=登记 commit_hash（含文件清单交叉核）", a2["verdict"], a2["evidence"]),
        ("A3 premerge-gate.v6 四查", a3["verdict"], "见下方子项"),
        *a3_rows,
        ("A4 实体新增字段无 DDL/exist=false", a4["verdict"], a4["evidence"]),
        ("A5 是否触发 NEEDS_HUMAN（DB 迁移）", "NEEDS_HUMAN" if a5["triggered"] else "未触发", a5["evidence"]),
    ]
    lines.append(_verdict_table(rows))
    lines.append("")
    if a1.get("confidence") == "low":
        lines.append(
            "> **注意（A1 置信度=low）**：本结论来自文本关键词启发式兜底，回执中未找到 "
            "`verified_by=校验官 / verdict=... / anchor=... / ts=...` 结构化判词行。"
            "建议请校验官在回执追加该行后重跑生成器，取得高置信度结论。"
        )
        lines.append("")

    if not b5_probes:
        lines.append(
            "> **降级提示（B5 探针反查失败，PIPELINE-SPEC.md §B5-P）**：三级反查——"
            "前端 api 定义 / Controller 注解（含同名 Service→Controller 扩展）/ vue 间接引用——"
            "对本批改动文件清单**均未命中**，下方 B5 段拿不到已解析的真实探针路径。"
            "**不阻断本单准入**（`gate` 仍按 A1-A5 判），但出单方在执行到 B5 前必须先手工反查真实路由、"
            "补全探针后再执行，禁止直接照抄 B5 段的失败标注当命令跑。"
        )
        lines.append("")

    touch = detect_touch(changed_files)

    # 四段式回填表（候选→逐字命令→原始输出关键行→回读验证证据）的行记录，
    # 每条: name(人读步骤名)/point(机器判决点键，与 kv_template 里的 point= 值一致)/
    # cmd_text(已渲染的真实命令，多行用 \n 分隔，禁止占位符)/kv_template(预填字段名+
    # ? 占位的键值串)/readback_hint(回读验证方法提示，非命令本身)。
    step_records: list[dict[str, str]] = []

    if gate == "NEEDS_HUMAN":
        lines.append("## 转人工兜（不出自动操作单）")
        lines.append("")
        lines.append("候选内含 DB 迁移文件，规格 A5 要求不出自动单，迁移先于重启这条硬约束需要人工在场协调号段与执行顺序：")
        lines.append("")
        for f in a5["files"]:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append("人工需确认：")
        lines.append("1. 迁移号是否与其它在途分支冲突（<日期> V065 撞号教训：两个分支各自独立编号，在各自 worktree 里互相看不见，需主窗口统一发号）。")
        lines.append("2. 该迁移是否已在共享库执行过一次（重复执行是否幂等）。")
        lines.append("3. 备份新鲜度（G7）——迁移前先确认最近一次全库备份的时间。")
        lines.append("4. 确认方案后，按 PIPELINE-SPEC.md §B 执行，迁移步骤插在 package 与 restart 之间，由人工手动跑迁移 SQL 后再继续 restart。")
        lines.append("")
        step_records.append({
            "name": "人工迁移评审",
            "cmd_text": "（无——A5 触发 NEEDS_HUMAN，不渲染自动执行步骤，人工按上方 4 项确认后自行执行）",
            "kv_template": "point=migration_handoff/decision=?/verdict=?",
            "readback_hint": "记录迁移号冲突核实结果/幂等性判断/备份新鲜度确认，以及最终去向（继续走 B 节人工迁移步骤，或打回）",
        })
    elif gate == "FAIL":
        lines.append("## 本单被拒绝，不出执行步骤")
        lines.append("")
        lines.append("以下各项未通过，按规格「任一不过即拒绝出单」，只输出准入报告，不渲染 B 节命令：")
        lines.append("")
        for name, verdict, evidence in rows:
            if verdict == "FAIL":
                lines.append(f"- **{name}**：{evidence}")
        lines.append("")
        lines.append("修复对应问题（或等校验官补出判词）后重跑本生成器。")
        lines.append("")
        step_records.append({
            "name": "处置动作",
            "cmd_text": "（无——准入未过，不渲染执行步骤）",
            "kv_template": "point=disposition/action=?/verdict=?",
            "readback_hint": "记录打回通知/等待校验官补结构化判词行/其它处置的实际去向",
        })
    else:
        lines.append("## B0 · 前置：获取串行锁（人工执行）")
        lines.append("")
        lines.append("锁状态：" + read_lock_status())
        lines.append("")
        b0_lock_cmd = (
            "mkdir -p ${TRAJ_HOME}/release-gate/.locks\n"
            "cat > ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock <<EOF\n"
            f"changeset_no={changeset_no}\n"
            f"worktree={INTEGRATION_WORKTREE}\n"
            "operator=<你的身份标识>\n"
            "acquired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)\n"
            f"ttl_seconds={LOCK_TTL_SECONDS_DEFAULT}\n"
            "EOF"
        )
        lines.append("```bash")
        lines.extend(b0_lock_cmd.splitlines())
        lines.append("```")
        lines.append("")
        lines.append("先跑一遍并线前基点记录（供 D 节回滚使用，把输出抄进下方《实际结果》表格）：")
        b0_base_cmd = f"git --no-optional-locks -C {INTEGRATION_WORKTREE} rev-parse HEAD"
        lines.append("```bash")
        lines.append(b0_base_cmd)
        lines.append("```")
        lines.append("")
        step_records.append({
            "name": "B0 获取锁 + 记录并线前基点",
            "cmd_text": b0_lock_cmd + "\n" + b0_base_cmd,
            "kv_template": "point=lock/lock_written=?/base_sha=?/verdict=?",
            "readback_hint": "回读锁文件内容确认字段齐全；base_sha 填 rev-parse HEAD 的真实输出，供 D 节回滚基点使用",
        })

        lines.append("## B1 · merge --no-ff")
        lines.append("")
        b1_cmd = f"cd {INTEGRATION_WORKTREE}\ngit merge --no-ff {head}   # 只认 commit_hash，不写分支名/HEAD"
        lines.append("```bash")
        lines.extend(b1_cmd.splitlines())
        lines.append("```")
        lines.append("")
        lines.append("**独立回读验证**：")
        b1_verify_cmd = (
            f"git --no-optional-locks -C {INTEGRATION_WORKTREE} log --oneline -3\n"
            f'git --no-optional-locks -C {INTEGRATION_WORKTREE} merge-base --is-ancestor {head} HEAD; echo "IS_ANCESTOR_EXIT=$?"'
        )
        lines.append("```bash")
        lines.extend(b1_verify_cmd.splitlines())
        lines.append("```")
        lines.append(f"期望：`IS_ANCESTOR_EXIT=0`（{head} 确实已在集成线祖先链上）。")
        lines.append("")
        lines.append("**失败回滚**：`MERGE_HEAD` 存在则 `git merge --abort`；已提交但校验不过则回 D 节 `git reset --hard <并线前基点>`。")
        lines.append("")
        step_records.append({
            "name": "B1 merge --no-ff",
            "cmd_text": b1_cmd + "\n" + b1_verify_cmd,
            "kv_template": "point=merge/new_head=?/is_ancestor_exit=?/verdict=?",
            "readback_hint": "new_head 填 log --oneline -3 里的新 HEAD 短 sha；is_ancestor_exit 填 IS_ANCESTOR_EXIT 的真实数值",
        })

        if touch["frontend"]:
            lines.append("## B2 · 前端类型检查（本批触及 frontend/**）")
            lines.append("")
            b2_cmd = f'cd {INTEGRATION_WORKTREE}/frontend\nnpx vue-tsc -b --pretty false\necho "TSC_EXIT=$?"'
            lines.append("```bash")
            lines.extend(b2_cmd.splitlines())
            lines.append("```")
            lines.append("**独立回读验证**：`TSC_EXIT` 必须为 0（真实退出码单独捕获，不接管道尾）；"
                          "若 freeze 巨型文件门禁已知红，`npm run build` 串联会截断后面的菜单三门禁，需单独复跑对应脚本。")
            lines.append("**失败回滚**：回 D 节 `git reset --hard <并线前基点>`，候选打回修复。")
            lines.append("")
            step_records.append({
                "name": "B2 前端类型检查",
                "cmd_text": b2_cmd,
                "kv_template": "point=tsc/tsc_exit=?/verdict=?",
                "readback_hint": "tsc_exit 填 TSC_EXIT 的真实数值（必须单独捕获，不接管道尾）",
            })

        if touch["backend"]:
            lines.append("## B3 · 后端打包（本批触及 src/main/java/** 或 pom.xml）")
            lines.append("")
            b3_cmd = "\n".join([
                f"cd {INTEGRATION_WORKTREE}",
                f"cp {BACKEND_JAR} /tmp/erp-2.0.0-SNAPSHOT-prev-$(git --no-optional-locks rev-parse --short HEAD^1).jar.bak  # 备份当前运行 jar，供失败回滚",
                f'JH17="{JDK17_HOME}"',
                "OPTS=''",
                "for p in api code comp file main model parser processing tree util jvm; do",
                '  OPTS="$OPTS --add-exports=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED --add-opens=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED"',
                "done",
                'JAVA_HOME="$JH17" MAVEN_OPTS="$OPTS" MAVEN_USER_HOME=${FLEET_INTEGRATION_REPO}/.m2repo \\',
                "  ./mvnw -B -Dmaven.repo.local=${FLEET_INTEGRATION_REPO}/.m2repo/repository -DskipTests package",
                'echo "BUILD_EXIT=$?"',
            ])
            lines.append("```bash")
            lines.extend(b3_cmd.splitlines())
            lines.append("```")
            lines.append("**独立回读验证**：")
            b3_verify_cmd = "\n".join([
                "echo $BUILD_EXIT   # 必须=0",
                f"ls -la {BACKEND_JAR}",
                f"shasum -a 256 {BACKEND_JAR}",
            ])
            lines.append("```bash")
            lines.extend(b3_verify_cmd.splitlines())
            lines.append("```")
            lines.append("期望：`BUILD_EXIT=0`；jar mtime 是本次 package 之后；记下 SHA 供 B4 后核对源位=运行位。")
            lines.append("**失败回滚**：不产生新 jar，运行时不受影响；回 D 节 `git reset --hard <并线前基点>`。")
            lines.append("")
            step_records.append({
                "name": "B3 后端打包",
                "cmd_text": b3_cmd + "\n" + b3_verify_cmd,
                "kv_template": "point=package/build_exit=?/jar_sha=?/jar_mtime=?/verdict=?",
                "readback_hint": "build_exit 填 $BUILD_EXIT 真实数值；jar_sha 填 shasum -a 256 输出；jar_mtime 填 ls -la 里的时间",
            })

            lines.append("## B4 · 加固重启（package 成功后必须紧接执行，禁止先建后等——<日期> 5098 事故教训）")
            lines.append("")
            b4_cmd = "\n".join([
                f"cd {INTEGRATION_WORKTREE}",
                f"# 找旧 PID：必须带 LISTEN 过滤，不过滤会把客户端连接 PID 也混进来（<日期> 影子首单实测教训）",
                f"OLD_PID=$(lsof -tnP -iTCP:{BACKEND_PORT} -sTCP:LISTEN)",
                'echo "OLD_PID=$OLD_PID"',
                'OLD_PID_N=$(printf \'%s\\n\' "$OLD_PID" | grep -c .)',
                'if [ "$OLD_PID_N" -gt 1 ]; then',
                '  echo "RUNNER_ERR: lsof 返回 $OLD_PID_N 行 PID，异常，停下人判——不许对多行值直接 kill"',
                "  exit 1",
                "fi",
                'kill "$OLD_PID" 2>/dev/null',
                "for i in $(seq 1 30); do",
                '  kill -0 "$OLD_PID" 2>/dev/null || { echo "DEAD at round $i"; break; }',
                '  if [ "$i" -eq 15 ]; then kill -9 "$OLD_PID"; fi',
                "  sleep 2",
                "done",
                f"lsof -tnP -iTCP:{BACKEND_PORT} -sTCP:LISTEN   # 期望空输出（同样带 LISTEN 过滤）",
                "",
                f'nohup {JDK11_HOME}/bin/java \\',
                f"  -jar {BACKEND_JAR} \\",
                "  --spring.profiles.active=prod,local \\",
                "  --spring.config.additional-location=optional:file:${FLEET_INTEGRATION_REPO}/.local-instance/ \\",
                f"  --server.port={BACKEND_PORT} \\",
                "  --<项目>.medical.enabled=false \\",
                "  --candidate-readonly.enabled=false \\",
                "  --erp.instance.operating-sites.enabled=false \\",
                "  --erp.instance.medical.enabled=false \\",
                "  --erp.instance.rbac.seed=<项目> \\",
                "  > ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log 2>&1 &",
                "NEW_PID=$!",
                'echo "NEW_PID=$NEW_PID"',
            ])
            lines.append("```bash")
            lines.extend(b4_cmd.splitlines())
            lines.append("```")
            lines.append("**独立回读验证（四件套）**：")
            b4_verify_cmd = "\n".join([
                "for i in $(seq 1 60); do",
                f"  CODE=$(curl -s -o /dev/null -w '%{{http_code}}' --noproxy '*' http://127.0.0.1:{BACKEND_PORT}/api/v1/auth/me)",
                '  [ "$CODE" = "401" -o "$CODE" = "200" ] && { echo "PROBE_OK round=$i code=$CODE"; break; }',
                "  sleep 1",
                "done",
                "grep -c ERROR ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log",
                'grep -ci "ambiguous mapping" ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log',
                'ps -o lstart= -p "$NEW_PID"',
                f"stat -f '%Sm' {BACKEND_JAR}",
                f"LISTEN_PID=$(lsof -tnP -iTCP:{BACKEND_PORT} -sTCP:LISTEN)",
                'echo "LISTEN_PID=$LISTEN_PID"',
                '[ "$LISTEN_PID" = "$NEW_PID" ] && echo "PID_MATCH_OK" || echo "PID_MATCH_FAIL listen=$LISTEN_PID new=$NEW_PID"',
            ])
            lines.append("```bash")
            lines.extend(b4_verify_cmd.splitlines())
            lines.append("```")
            lines.append("期望：①探活命中且记录轮次（9/15/8 这类台账数字是**启动探活轮次**，不是 kill 轮次，见 PIPELINE-SPEC.md §B4 节拍实测注记）；②ERROR 计数=0；③ambiguous mapping 命中=0；④jar mtime 早于/约等于 NEW_PID 启动时间；⑤`PID_MATCH_OK`（持有端口的就是本步骤起的 NEW_PID，不等则说明①探到的是旧进程假绿）。")
            lines.append("**失败回滚**：杀新 PID，用 B3 备份的旧 jar 重跑本步骤第二段启动命令；迁移已执行但重启失败属 NEEDS_HUMAN。")
            lines.append("")
            step_records.append({
                "name": "B4 加固重启",
                "cmd_text": b4_cmd + "\n" + b4_verify_cmd,
                "kv_template": (
                    "point=restart/old_pid=?/kill_rounds=?/new_pid=?/startup_s=?/"
                    "error_count=?/ambiguous_mapping=?/jar_sha=?/pid_match=?/verdict=?"
                ),
                "readback_hint": (
                    "old_pid/new_pid 填 OLD_PID/NEW_PID 真实值；kill_rounds 填 DEAD at round 的真实轮次"
                    "（2/16 等，是 kill 侧计数，不是 PROBE_OK 的探活轮次）；startup_s 填探活命中耗时秒数；"
                    "error_count 填 grep -c ERROR 真实数值；ambiguous_mapping 填对应 grep -ci 真实数值；"
                    "jar_sha 填与 B3 记下的 SHA 核对结果；pid_match 填 PID_MATCH_OK/PID_MATCH_FAIL（⑤核对"
                    "LISTEN_PID==NEW_PID 的真实结果，防旧进程假绿）"
                ),
            })

        lines.append("## B5 · 冒烟（走用户同源入口 ${PORT_WEB}，针对本批触及实体加探针）")
        lines.append("")
        b5_login_cmd = "\n".join([
            f"TOKEN=$(curl -s --noproxy '*' -X POST http://127.0.0.1:{FRONTEND_PORT}/api/auth/station-login \\",
            "  -H 'Content-Type: application/json' \\",
            '  -d \'{"stationName":"<项目>管理","pin":"1234"}\'',
        ])
        lines.append("```bash")
        lines.extend(b5_login_cmd.splitlines())
        lines.append("")

        static_probes = [p for p in b5_probes if not p["dynamic"]]
        dynamic_probes = [p for p in b5_probes if p["dynamic"]]
        probe_block_lines: list[str] = []
        if b5_probes:
            probe_block_lines.append("# 下方业务页探针由生成器按 PIPELINE-SPEC.md §B5-P 三级反查渲染（前端 api 定义 / "
                                      "Controller 注解 / vue 间接引用），不是猜的：")
            for p in static_probes:
                probe_block_lines.append(
                    f"curl -s -o /dev/null -w '%{{http_code}}\\n' --noproxy '*' -H \"Authorization: Bearer $TOKEN\" "
                    f"http://127.0.0.1:{FRONTEND_PORT}/api{p['path']}   # 来自 {p['source']}"
                )
            if dynamic_probes:
                probe_block_lines.append("# 以下路径含变量，反查只能给到带占位符的形状，人工需填真实 id 后才能探（不是可直接跑的命令）：")
                for p in dynamic_probes:
                    probe_block_lines.append(f"#   GET /api{p['path']}   # 来自 {p['source']}")
        else:
            probe_block_lines.append(
                'echo "B5_PROBE_LOOKUP_FAILED: 三级反查（前端 api 定义/Controller 注解/vue 间接引用）'
                '对本批改动文件清单均未命中，出单方必须手工反查真实路由后再执行探针，'
                '禁止把这行当探针命令跑" >&2'
            )
            for f in changed_files:
                probe_block_lines.append(f"# touched（反查未覆盖）: {f}")
        lines.extend(probe_block_lines)
        lines.append("```")
        lines.append("**独立回读验证**：每个探针 HTTP code 必须是业务正常码（200/401），不是 5xx（<事故编号> 教训：只探首页不够，必须覆盖本批触及实体的页面查询）。")
        lines.append("**失败回滚**：切回 B3 备份的旧 jar 恢复服务（若无后端变更则前端 HMR 直接回退到并线前基点）；候选记 BLOCKED 退回修复。")
        lines.append("")

        # 批量脚本：逐条 curl（上方）留作"来源可追溯"的文档，一条条复制粘贴不现实
        # （<日期> postmortem-0045 教训：操作者会自己写循环，循环里裸用
        # `for p in $PATHS` 在 zsh 下不分词，19 条挤成一条 URL，首轮探针吐 000——
        # 管道没给错命令，是没给批量手段）。这里另渲染一份可直接执行的批量脚本：
        # 路径进 bash 数组，`for p in "${STATIC_PATHS[@]}"` 是数组展开，不依赖
        # IFS 分词，bash/zsh 下行为一致；不加 `set -u`——macOS 系统自带 /bin/bash
        # 是 3.2（GPLv2 封版），`set -u` 配空数组展开在该版本上会报 unbound
        # variable，反而制造新坑。
        batch_script_lines: list[str] = []
        if b5_probes:
            batch_script_lines.append("#!/bin/bash")
            batch_script_lines.append("# B5 批量探针脚本——路径来源见上方逐条 curl 行内注释；可整段贴入终端跑，")
            batch_script_lines.append("# 也可另存为文件后 bash 执行；数组遍历不依赖 shell 分词，不受执行者默认 shell 是 zsh 影响。")
            batch_script_lines.append("")
            batch_script_lines.append(f'BASE="http://127.0.0.1:{FRONTEND_PORT}/api"')
            batch_script_lines.append('''TOKEN=$(curl -s --noproxy '*' -X POST "$BASE/auth/station-login" -H 'Content-Type: application/json' \\
  -d '{"stationName":"<项目>管理","pin":"1234"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["token"])')
if [ -z "$TOKEN" ]; then echo "LOGIN_FAILED: 未取到 token，后续探针必然 401，先修登录再跑" >&2; exit 1; fi''')
            batch_script_lines.append("STATIC_PATHS=(")
            for p in static_probes:
                batch_script_lines.append(f'  "{p["path"]}"')
            batch_script_lines.append(")")
            batch_script_lines.append("")
            batch_script_lines.append("TOTAL=0")
            batch_script_lines.append("OK=0")
            batch_script_lines.append("FAIL_LIST=()")
            batch_script_lines.append('for p in "${STATIC_PATHS[@]}"; do')
            batch_script_lines.append("  CODE=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -H \"Authorization: Bearer $TOKEN\" \"${BASE}${p}\")")
            batch_script_lines.append("  TOTAL=$((TOTAL + 1))")
            batch_script_lines.append("  printf '%s %s\\n' \"$CODE\" \"$p\"")
            batch_script_lines.append('  case "$CODE" in')
            batch_script_lines.append("    2??) OK=$((OK + 1)) ;;")
            batch_script_lines.append('    *) FAIL_LIST+=("$CODE $p") ;;')
            batch_script_lines.append("  esac")
            batch_script_lines.append("done")
            batch_script_lines.append("")
            batch_script_lines.append('echo "共 ${TOTAL} 条，2xx ${OK} 条"')
            batch_script_lines.append('if [ "${#FAIL_LIST[@]}" -eq 0 ]; then')
            batch_script_lines.append('  echo "非 2xx 清单：（无）"')
            batch_script_lines.append("else")
            batch_script_lines.append('  echo "非 2xx 清单："')
            batch_script_lines.append("  printf '  %s\\n' \"${FAIL_LIST[@]}\"")
            batch_script_lines.append("fi")
            if dynamic_probes:
                batch_script_lines.append("")
                batch_script_lines.append("# 以下路径含变量，需人工填真实 id 后单独探测，不进入上方批量循环：")
                for p in dynamic_probes:
                    batch_script_lines.append(
                        f"#   curl -s -o /dev/null -w '%{{http_code}}\\n' --noproxy '*' "
                        f"\"${{BASE}}{p['path']}\"   # 来自 {p['source']}"
                    )
        else:
            batch_script_lines.append("#!/bin/bash")
            batch_script_lines.append(
                'echo "B5_BATCH_PROBE_UNAVAILABLE: 三级反查（前端 api 定义/Controller 注解/vue 间接引用）'
                '对本批改动文件清单均未命中，没有路径可渲染批量脚本；出单方须先手工反查真实路由、'
                '把路径填进 STATIC_PATHS 后再执行，禁止把本脚本当成可用探针跑" >&2'
            )
            batch_script_lines.append("exit 1")

        lines.append("### B5 批量脚本（可复制成一个文件执行，也可整段贴入终端跑；显式声明 bash，不依赖执行者的默认 shell）")
        lines.append("")
        lines.append("```bash")
        lines.extend(batch_script_lines)
        lines.append("```")
        lines.append("")

        step_records.append({
            "name": "B5 冒烟",
            # 反查成功时逐字命令列直接收录已解析的真实路径（静态路径可执行，动态路径
            # 标注需人工填 id，不冒充可执行命令）；反查全部落空时收录显著的失败标注，
            # 不塞一个假装是真命令的路径占位符（PIPELINE-SPEC.md §B5-P）。批量脚本一并
            # 收进逐字命令列，与上方渲染的正文保持同一份真相。
            "cmd_text": b5_login_cmd + "\n" + "\n".join(probe_block_lines) + "\n" + "\n".join(batch_script_lines),
            "kv_template": "point=smoke/probe_paths=?/http_codes=?/verdict=?",
            "readback_hint": (
                "probe_paths 填实际探测的接口路径列表（分号分隔，含反查列表之外人工补测的动态路径）；"
                "http_codes 填对应 HTTP 状态码列表，顺序与 probe_paths 一一对应；反查失败（上方为 "
                "B5_PROBE_LOOKUP_FAILED）时，probe_paths/http_codes 必须是人工手工反查后实际测的路径，"
                "不能留空或抄反查失败提示"
            ),
        })

        step_records.append({
            "name": "释放锁",
            "cmd_text": f"rm {LOCK_FILE}",
            "kv_template": "point=lock_release/lock_removed=?/verdict=?",
            "readback_hint": f"回读 `test -f {LOCK_FILE}` 或再跑一次锁状态检查，确认锁文件已不存在",
        })

    lines.append("## 《实际结果》回填表格（人工执行后填写；四段式键值串：候选→逐字命令→原始输出关键行→回读验证证据，另加「分歧/备注」列）")
    lines.append("")
    lines.append(
        "> 候选坐标见本单顶部（worktree/分支/HEAD/changeset）。下表每步的键值串字段名已预填、"
        "`?` 处照实填值，不做自由发挥；每步必须含 `point=` 与 `verdict=` 两个字段——这是"
        "worksheet_to_trace.py 转换器识别判决点的唯一依据，缺了就转不出合法轨迹。`verdict` 只能填 "
        "`PASS/FAIL/N/A/AMBIGUOUS` 四选一，不许把分歧说明文字塞进这个字段——分歧说明写在最后一列"
        "「分歧/备注」。"
    )
    lines.append("")
    lines.append(
        "| 步骤 | 逐字命令（已渲染，勿改） | 原始输出关键行（请按键值串填） | 回读验证证据 | "
        "分歧/备注（本步若与单内预期不符，在此写明分歧详情与补救动作；`verdict` 字段仍只填 "
        "PASS/FAIL/N/A/AMBIGUOUS 四选一） |"
    )
    lines.append("|---|---|---|---|---|")
    for s in step_records:
        cmd_cell = s["cmd_text"].replace("\n", "<br>").replace("|", "\\|")
        kv_cell = s["kv_template"].replace("|", "\\|")
        hint_cell = s["readback_hint"].replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {s['name']} | {cmd_cell} | {kv_cell} | {hint_cell} |  |")
    lines.append("")
    lines.append("> 收尾条款：无论 PASS/FAIL/BLOCKED，落盘到 `迁移备份/回执/CS-YYYYMMDD-<单名>.md` 前，本单不算结束。")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="<项目>发布管道操作单生成器（只读，只出单不动手）")
    parser.add_argument("--worktree", required=True, help="候选 worktree 绝对路径")
    parser.add_argument("--changeset", required=True, help="changeset 号，如 CS-<日期>-0020")
    parser.add_argument("--out", default=None, help="操作单输出路径，默认 release-gate/worksheets/<changeset>.md")
    parser.add_argument("--selftest-only", action="store_true", help="只跑只读守卫自检，不生成操作单")
    parser.add_argument(
        "--registry-json",
        default=None,
        help="离线登记模式（仅用于验证，见 PIPELINE-SPEC.md §A0）：A1/A2 登记数据改从此 JSON 读，"
        "完全不连 ${DB_NAME}；不传则行为不变，照旧查库",
    )
    args = parser.parse_args()

    print("=== 只读守卫自检（故意尝试写操作，确认全部被拦截）===", file=sys.stderr)
    for line in guard_selftest():
        print(line, file=sys.stderr)
    print("", file=sys.stderr)

    if args.selftest_only:
        return 0

    worktree = str(Path(args.worktree).resolve())
    if not (Path(worktree) / ".git").exists():
        print(f"错误：{worktree} 不是一个 git worktree（找不到 .git）", file=sys.stderr)
        return 2

    if not CHANGESET_NO_RE.match(args.changeset):
        print(f"错误：changeset 号格式不合法: {args.changeset}", file=sys.stderr)
        return 2

    registry: Optional[dict[str, Any]] = None
    if args.registry_json:
        try:
            registry = load_registry(Path(args.registry_json))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"错误：--registry-json 加载失败: {e}", file=sys.stderr)
            return 2
        print(f"=== 离线登记模式：登记数据取自 {args.registry_json}，本次运行不连 ${DB_NAME} ===", file=sys.stderr)

    try:
        head = get_worktree_head(worktree)
        branch = get_worktree_branch(worktree)
        merge_base = get_merge_base(worktree)

        a1 = check_a1_changeset_and_verifier(worktree, args.changeset, registry)
        a2 = check_a2_anchor(worktree, a1.get("changeset"), registry)
        # A2 内部用 n9b 算法独立解出了一个「diff 后文件集合与登记明细逐字相等」的基线，
        # 这个基线比 merge-base(集成分支, HEAD) 更可信（后者在 <日期>
        # CS-<日期>-0032 上被实证会退化成等于 HEAD 自己，导致下游拿到空 diff、
        # 等于没检查）。优先用它喂给 A3.g4 行尾判据、A4 实体扫描、A5 迁移触发与头部
        # 展示/改动文件清单——这四者原先共用同一个坏掉的 merge_base，<日期>
        # CS-<日期>-UXNAV2（同款 base==head 退化）上实测过 A5 会因此漏判一个真实
        # 存在的 db/migrations/*.sql 文件（该文件不在旧口径算出的空 diff 里），而
        # A5 的作用正是拦住未经人工过目的 DB 迁移，静默漏判是安全缺口，不是可以只记
        # 风险不修的边角料。A2 找不到可用基线时（changeset 未登记/明细缺失/搜索不中）
        # 才退回旧口径。
        effective_base = a2.get("resolved_baseline") or merge_base
        a3 = check_a3_premerge_gate(worktree, effective_base, head)
        a4 = check_a4_entity_existfalse(worktree, effective_base, head)
        a5 = check_a5_migration_trigger(worktree, effective_base, head)
        changed_files = get_changed_files(worktree, effective_base, head) if effective_base else []
    except GuardBlocked as e:
        print(f"内部错误：某个只读检查试图越界被自己的守卫拦截了，这是 bug: {e}", file=sys.stderr)
        return 3

    worksheet = render_worksheet(
        worktree, args.changeset, a1, a2, a3, a4, a5, head, branch, effective_base, changed_files
    )

    out_path = Path(args.out) if args.out else Path(
        f"${TRAJ_HOME}/release-gate/worksheets/{args.changeset}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(worksheet, encoding="utf-8")

    print(f"操作单已写入: {out_path}", file=sys.stderr)
    print("", file=sys.stderr)
    print("=== 只读守卫调用日志（全部调用，含放行与拦截） ===", file=sys.stderr)
    for entry in GUARD_LOG:
        print(f"[{entry['ts']}] {entry['kind']:4s} {entry['decision']:8s} {entry['cmd']}  ({entry['reason']})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
