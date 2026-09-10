#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选 A · 登记质量前置门（ABGO-20260817 授权开编）

## 它解决什么

现有审计是**事后**的：登记写完 → 轮询器发现 → 跑图 → 报警 → 人处置。
实测这条链有多长：`CS-<日期>-0008` 从登记到被修隔了 **2 天**；
<跳板机> 侧那 7 行转义损坏路径**躺了快两个月没人知道**。

本门把同一批判据**前移到登记那一刻**：登记方当场看到 `HOLD` 并改，
而不是两天后被人追。

## 九条判据

| 判据 | 内容 | 反例来源（今天的真病例） |
|---|---|---|
| A1 | `commit_hash` 是 40 位十六进制，或 `change_type=data_migration` 免锚点 | `CS-<日期>-0008`(9位)、`CS-<日期>-0108/0112` |
| A2 | 锚点在已知仓库清单里解析得开 | `CS-<日期>-0017`(在<项目>ERP仓不在<基座项目>-erp) |
| A3 | `file_path` 不得是 git 转义残留（引号包裹 / 八进制串） | `CS-<日期>-0017`、`CS-<日期>-0099`、<跳板机> 侧 7 行 |
| A4 | `file_count` == 明细行数，且两者不得同时为 0 却声称有交付 | 全库 31 单明细 0 行、2 单 file_count>0 无明细 |
| A5 | 锚点第一父链上存在基线，其 diff 文件集与明细逐字相等 | `CS-<日期>-0052/0053`(锚点填成 docs 提交) |
| A6 | **登记时锚点尚未并线**（先登记后并线的铁序） | `CS-<日期>-0004` |
| A7 | 单号符合 `CS-YYYYMMDD-NNNN`（历史例外名单除外） | 历史非标单号 |
| A8 | `candidate-readonly` 测试不得含写型调用或 SQL | E2EGATE 两处写型 E2E 门 |
| A9 | 明细行哈希按 `change_action` 完整，并抽样重算 `after_content` | `CS-<日期>-0009` 23 行前后哈希全空 |

**A2/A5 不重写**——它们就是 `drift_scan.resolve_repo` / `recheck_consistency`，直接复用。
新写的 A1/A3/A4/A6/A7/A8/A9 均有独立正反例；A8 优先走 Java 注解/调用节点解析，缺少可选解析器时才退回受限正则。

## A6 只在「登记那一刻」成立

A6 判的是「此刻锚点是否已在集成线上」。**对历史单它必然全部命中**（它们早就并线了），
所以 A6 **不参与回溯体检**，只在前置门实时调用时有意义。
这不是缺陷，是判据的适用边界——写在这里免得日后有人拿它去扫存量然后惊慌。

**同理，轮询器侧调用也必须 `--no-a6`**：轮询器看到单子时它可能早已并线，
A6 会把正常单误判成违反铁序（接线试跑当场实测：`CS-<日期>-0011` 这种正常单被 A6 判 HOLD）。
A6 只有在**真正的登记时点**调用才成立——那要等登记侧 MCP 工具化
（`TRIRULE-20260817` 已记为 B 落地后的候选）。

## 形态：先只告警，不硬拦

按投产方案里我自己的建议：**先上「只告警」形态**。判据误报会当场卡住登记方，
而误报率要用真实数据测过才能谈硬拦。本模块只输出判定，不阻断任何东西。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import drift_scan as ds          # noqa: E402  复用 resolve_repo / recheck_consistency

try:
    import javalang
except ImportError:  # 可选依赖；部署环境没有时仍以受限正则 fail-closed 扫描。
    javalang = None

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
NULLISH = ("", "NULL", "null", "\\N", "None")

# git 转义残留的两种形态：
#   ① 标准形态：整条被双引号包裹（git 只在转义了内容时才加引号）
#   ② 损坏形态：引号还在但反斜杠被写入链吞掉，退化成裸八进制数字串
#      （本机实测 2 行、<跳板机> 实测 7 行，见 08-17 记档）
ESCAPED_QUOTED_RE = re.compile(r'^".*"$')
OCTAL_RUN_RE = re.compile(r"(?:\\[0-7]{3}){2,}|[0-7]{9,}")

MERGE_BASE_REF = os.environ.get("GATE_MERGE_REF", "feat/<项目>-integration")
A8_BASELINE_ANCHOR = "7a93b2dc26b1451cff267e87e37a4e812f73d677"
A8_BASELINE_TESTS = (
    "src/test/java/com/<基座项目>/system/service/impl/UserServiceImplRbacBaselineE2eTest.java",
    "src/test/java/com/<基座项目>/device/quality/authorization/DeviceQualityReleaseAuthorizationMysqlContractTest.java",
)
A8_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "tests", "a8")
A8_TEST_PATH_RE = re.compile(r"^src/test/java/.+\.java$")
A8_WRITE_METHODS = {"insert", "update", "save", "delete", "saveOrUpdate", "executeUpdate", "batchUpdate"}
A8_WRITE_CALL_RE = re.compile(
    r"\.\s*(insert|update|save|delete|saveOrUpdate|executeUpdate|batchUpdate|createOrUpdate\w*)\s*\(")
A8_SQL_LITERAL_RE = re.compile(r"[\"']\s*(INSERT|UPDATE|DELETE)\s+", re.IGNORECASE)

# [修 <日期>] 原来只有一个白名单，把「免锚点」和「免明细」混成了一件事。
# 全库体检暴露：change_type=verification 共 3 单、**全部零明细**（标题是「集成线-L3验证/
# 复跑/三跑」，验证类交付本来就不产生代码文件），但它们**都有 40 位合规锚点**——
# 若并进同一个白名单，等于连锚点要求一起免掉，那是放水。两件事必须分开：
# A7 单号规范：`变更与错误追踪接入-v1.0.md` §三①「changeset_no = CS-YYYYMMDD-NNNN，当日递增」。
# 例外名单参数化（硬约定 7）：全库现有 7 个非标单号，逐张追改会打断已建立的交叉引用
# （回执/告警文件名、判决台账、指纹台账、trace_runs），而单独改一张也不解决类别问题。
# 正解是**对新单机械拦截**，历史存量记例外。名单来源：<日期> 全库普查。
CHANGESET_NO_RE = re.compile(r"^CS-\d{8}-\d{4}$")
NO_FORMAT_EXCEPTIONS = set(x for x in os.environ.get("GATE_NO_EXCEPTIONS",
    "REG-SEED-V1-20260804,SITE-MASTER-V1-20260804,S1-DUAL-CUST-V1-20260804,"
    "CS-<日期>-LC2A-LUNA1,CS-<日期>-UXNAV2,CS-<日期>-ARCOSTYLE,"
    "CS-<日期>-QM-P0B").split(",") if x)

ANCHORLESS_TYPES = set(x for x in os.environ.get(
    "GATE_ANCHORLESS_TYPES", "data_migration").split(",") if x)          # 免锚点
DETAILLESS_TYPES = set(x for x in os.environ.get(
    "GATE_DETAILLESS_TYPES", "data_migration,verification").split(",") if x)  # 免明细
# 白名单参数化（硬约定 7）：日后再冒出天生无文件的类别，改环境变量即可，不用改码。
#
# **未纳入 config**：全库 21 单里 14 单零明细，但配置改动通常是要落文件的，
# 「过半零明细」更像登记缺口而不是类别特性。纳不纳入属业务口径，不由我定，已上报。


# ── 纯判据：只吃数据、不读库不跑 git，便于逐条造正反例 ────────────────

def a1_anchor_format(commit_hash: str, change_type: str) -> tuple[bool, str]:
    h = (commit_hash or "").strip()
    if h in NULLISH:
        if change_type in ANCHORLESS_TYPES:
            return True, "免锚点类型 %s，允许空锚点" % change_type
        return False, "未填 commit_hash，且 change_type=%s 不属免锚点类型" % (change_type or "?")
    if not SHA40_RE.match(h):
        return False, "commit_hash=%r 长度 %d，非 40 位十六进制" % (h, len(h))
    return True, "40 位合规"


def a3_path_shape(paths: list[str]) -> tuple[bool, str]:
    bad = []
    for p in paths:
        s = (p or "").strip()
        if ESCAPED_QUOTED_RE.match(s) or OCTAL_RUN_RE.search(s):
            bad.append(s[:60])
    if bad:
        return False, "疑似 git 转义残留路径 %d 条：%s" % (len(bad), "; ".join(bad[:3]))
    return True, "路径形态正常"


def a4_count_match(file_count, detail_count: int, change_type: str) -> tuple[bool, str]:
    try:
        fc = int(str(file_count).strip())
    except (TypeError, ValueError):
        return False, "file_count=%r 不是整数" % (file_count,)
    if fc != detail_count:
        return False, "file_count=%d 与明细行数 %d 不符" % (fc, detail_count)
    if fc == 0 and change_type not in DETAILLESS_TYPES:
        return False, "明细 0 行，且 change_type=%s 不属免明细类型" % (change_type or "?")
    return True, "file_count=%d 与明细行数相符" % fc


def a7_no_format(changeset_no: str) -> tuple[bool, str]:
    """单号规范。例外名单里的历史单放行，但**理由写明是例外不是合规**。"""
    if CHANGESET_NO_RE.match(changeset_no or ""):
        return True, "单号合规"
    if changeset_no in NO_FORMAT_EXCEPTIONS:
        return True, "单号 %s 不合 CS-YYYYMMDD-NNNN，但在例外名单内（历史存量，不追改）" % changeset_no
    return False, "单号 %s 不合规范 CS-YYYYMMDD-NNNN，且不在例外名单内" % changeset_no


def a6_not_yet_merged(is_ancestor: bool | None) -> tuple[bool, str]:
    """is_ancestor: 锚点此刻是否已是集成线祖先。None = 判不了（无锚点/解析不开）。"""
    if is_ancestor is None:
        return True, "N/A（无可判锚点，交 A1/A2 处理）"
    if is_ancestor:
        return False, "登记时锚点已在 %s 上——先并线后登记，违反铁序" % MERGE_BASE_REF
    return True, "锚点尚未并线，顺序合规"


def _strip_java_comments(source: str) -> str:
    """移除 Java 注释但保留换行，避免 A8 把注释/样例字符串当作 profile 或写型。"""
    return re.sub(r"//[^\n]*|/\*.*?\*/",
                  lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S)


def _a8_profile_by_regex(source: str) -> bool:
    cleaned = _strip_java_comments(source)
    for match in re.finditer(r"@ActiveProfiles\s*\((.*?)\)", cleaned, re.S):
        if re.search(r"[\"']candidate-readonly[\"']", match.group(1)):
            return True
    return False


def _a8_write_by_regex(source: str) -> tuple[int, str] | None:
    cleaned = _strip_java_comments(source)
    for line_no, line in enumerate(cleaned.splitlines(), start=1):
        call = A8_WRITE_CALL_RE.search(line)
        if call:
            return line_no, call.group(0).strip()
        sql = A8_SQL_LITERAL_RE.search(line)
        if sql:
            return line_no, "%s SQL" % sql.group(1).upper()
    return None


def _a8_scan_with_javalang(source: str) -> tuple[bool, tuple[int, str] | None]:
    """返回 candidate-readonly profile 是否存在及首个写型节点；解析失败由调用方退回正则。"""
    tree = javalang.parse.parse(source)
    profile = False
    for _, annotation in tree.filter(javalang.tree.Annotation):
        if annotation.name == "ActiveProfiles" and "candidate-readonly" in str(annotation.element):
            profile = True
            break
    if not profile:
        return False, None

    hits: list[tuple[int, str]] = []
    for _, invocation in tree.filter(javalang.tree.MethodInvocation):
        member = invocation.member or ""
        if member in A8_WRITE_METHODS or member.startswith("createOrUpdate"):
            hits.append((getattr(invocation.position, "line", 0) or 0, ".%s(" % member))
    for _, literal in tree.filter(javalang.tree.Literal):
        value = literal.value or ""
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        if re.match(r"\s*(INSERT|UPDATE|DELETE)\s+", value, re.I):
            hits.append((getattr(literal.position, "line", 0) or 0,
                         "%s SQL" % value.lstrip().split(None, 1)[0].upper()))
    return True, min(hits, key=lambda item: item[0]) if hits else None


def a8_candidate_readonly_write(source: str) -> tuple[bool, str]:
    """A8 单文件判据的职责边界。

    入口只对登记明细中的 ``src/test/java/**/*.java`` 调用：判定“声明
    candidate-readonly 且含写操作”。``Helper.method(mapper, ...)`` 的传参写法不在
    A8 职责内，归 ERP 入账锁契约门处理。本机无 ``javalang`` 时永远
    ``parser=regex``，AST 分支未启用；``verify(mock).insert(...)`` 会安全侧误红，
    因为 Mockito 断言本身不写库。
    """
    if javalang is not None:
        try:
            profile, hit = _a8_scan_with_javalang(source)
            parser = "javalang"
        except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError, TypeError, ValueError):
            profile, hit, parser = _a8_profile_by_regex(source), _a8_write_by_regex(source), "regex"
    else:
        profile, hit, parser = _a8_profile_by_regex(source), _a8_write_by_regex(source), "regex"
    if not profile:
        return True, "parser=%s；@ActiveProfiles 未含 candidate-readonly" % parser
    if hit is None:
        return True, "parser=%s；candidate-readonly 未见写型" % parser
    return False, "parser=%s；candidate-readonly 写型首命中 line %d: %s" % (parser, hit[0], hit[1])


def _git_show_source(repo: str, commit_hash: str, path: str) -> tuple[str | None, str | None]:
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (commit_hash, path)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None, (p.stderr.strip() or "git show 失败")
    return p.stdout, None


def _hash_empty(value) -> bool:
    return value is None or str(value).strip() in NULLISH


def a9_required_hash_fields(change_action: str) -> tuple[str, ...]:
    """ADD 只要求 after；DELETE 只要求 before；MODIFY/RENAME 前后皆要。未知动作 fail-closed 按 MODIFY。"""
    action = (change_action or "").strip().upper()
    if action == "ADD":
        return ("content_hash_after",)
    if action == "DELETE":
        return ("content_hash_before",)
    return ("content_hash_before", "content_hash_after")


def a9_hash_complete(changeset_no: str, rows: list[dict]) -> tuple[bool, str]:
    """纯判据：按 change_action 检查哈希空/非空。不读库。"""
    if not rows:
        return True, "N/A（无明细行，交 A4 处理）"
    missing = []
    for row in rows:
        need = a9_required_hash_fields(row.get("change_action"))
        lack = [field for field in need if _hash_empty(row.get(field))]
        if lack:
            missing.append("%s %s 缺 %s" % (
                changeset_no, row.get("file_path") or "?", ",".join(lack)))
    if missing:
        return False, "明细哈希不完整 %d 行：%s" % (len(missing), "；".join(missing))
    return True, "明细哈希按 change_action 完整（%d 行）" % len(rows)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_hex(data: bytes) -> str:
    return hashlib.sha1(("blob %d\0" % len(data)).encode("ascii") + data).hexdigest()


def a9_infer_hash_algo(pairs: list[tuple[bytes, str]]) -> str:
    """从既有非空哈希反推：sha256(content) 或 git blob sha1。"""
    sha_ok = 0
    blob_ok = 0
    for data, recorded in pairs:
        want = (recorded or "").strip().lower()
        if not want or not data:
            continue
        if _sha256_hex(data) == want:
            sha_ok += 1
        if _git_blob_hex(data) == want:
            blob_ok += 1
    if sha_ok == 0 and blob_ok == 0:
        return "unknown"
    return "sha256" if sha_ok >= blob_ok else "git-blob"


def a9_rehash(data: bytes, algo: str) -> str:
    if algo == "git-blob":
        return _git_blob_hex(data)
    return _sha256_hex(data)


def a9_sample_rehash(rows: list[dict], limit: int = 3) -> tuple[bool, str]:
    """抽样 ≤3 行：用反推出的算法重算 after_content，比对 content_hash_after。"""
    candidates = []
    for row in rows:
        data = row.get("after_content_bytes")
        digest = row.get("content_hash_after")
        if _hash_empty(digest) or not data:
            continue
        candidates.append(row)
        if len(candidates) >= limit:
            break
    if not candidates:
        return True, "N/A（无带 after_content 的非空 content_hash_after 可抽样）"
    pairs = [(row["after_content_bytes"], row["content_hash_after"]) for row in candidates]
    algo = a9_infer_hash_algo(pairs)
    if algo == "unknown":
        names = "; ".join("%s" % (row.get("file_path") or "?") for row in candidates)
        return False, "抽样 %d 行无法反推哈希算法（既非 sha256(content) 也非 git blob）：%s" % (
            len(candidates), names)
    bad = []
    for row in candidates:
        got = a9_rehash(row["after_content_bytes"], algo)
        want = (row.get("content_hash_after") or "").strip().lower()
        if got != want:
            bad.append("%s 登记 %s 重算 %s" % (row.get("file_path") or "?", want, got))
    if bad:
        return False, "抽样重算不一致 algo=%s：%s" % (algo, "；".join(bad))
    return True, "抽样 %d 行 algo=%s 与 content_hash_after 一致" % (len(candidates), algo)


def a9_check(changeset_no: str) -> tuple[bool, str]:
    """A9 读库薄壳：先按 change_action 判空，再抽样重算。"""
    rows = fetch_hash_rows(changeset_no)
    ok, why = a9_hash_complete(changeset_no, rows)
    if not ok:
        return ok, why
    ok2, why2 = a9_sample_rehash(rows)
    if not ok2:
        return ok2, why2
    if why2.startswith("N/A"):
        return True, why
    return True, "%s；%s" % (why, why2)


def a8_registered_tests(commit_hash: str, paths: list[str]) -> tuple[bool, str]:
    test_paths = [p for p in paths if A8_TEST_PATH_RE.match(p)]
    parser = "javalang" if javalang else "regex"
    if not test_paths:
        return True, "N/A（登记无 src/test/java/*.java 明细；parser=%s）" % parser
    repo = ds.resolve_repo(commit_hash) if commit_hash and commit_hash not in NULLISH else None
    if repo is None:
        return True, "N/A（锚点不可解析，交 A1/A2 处理；parser=%s）" % parser
    for path in test_paths:
        source, error = _git_show_source(repo, commit_hash, path)
        if error:
            return False, "%s：无法读取锚点源码：%s" % (path, error[:120])
        ok, why = a8_candidate_readonly_write(source or "")
        if not ok:
            return False, "%s：%s" % (path, why)
    return True, "扫描 %d 个测试明细均未命中 candidate-readonly 写型；parser=%s" % (len(test_paths), parser)


def _a8_fixture_source(path: str) -> tuple[str | None, str | None]:
    repo = ds.resolve_repo(A8_BASELINE_ANCHOR)
    if repo is None:
        return None, "基线锚未解析到仓库"
    return _git_show_source(repo, A8_BASELINE_ANCHOR, path)


def _a8_fixture_check(path: str, corrected: bool = False) -> tuple[bool, str]:
    source, error = _a8_fixture_source(path)
    if error:
        return False, error
    if corrected:
        source = (source or "").replace("candidate-readonly", "candidate-write")
    return a8_candidate_readonly_write(source or "")


def _a8_negative_fixture_check(relative_path: str) -> tuple[bool, str]:
    with open(os.path.join(A8_FIXTURES_DIR, relative_path), encoding="utf-8") as f:
        return a8_candidate_readonly_write(f.read())


# ── 读库/跑 git 的薄壳 ──────────────────────────────────────────────

def _mysql(sql: str) -> str:
    inner = ('mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
             '-N -B ${DB_NAME} -e "%s"' % sql.replace('"', '\\"'))
    p = subprocess.run(["/usr/local/bin/docker", "exec", "${DB_CONTAINER}", "sh", "-c", inner],
                       capture_output=True, text=True)
    return p.stdout


def fetch(changeset_no: str) -> dict:
    # [修 <日期>] 原来对整段输出 .strip() 再切——**锚点为空时输出行以制表符开头，
    # .strip() 会把那个空字段连同前导 tab 一起吃掉，后面字段整体左移一格**：
    # 实测 34 单被读成 commit_hash='1'、file_count='config'，四条判据一起假 HOLD。
    # 双保险：① 用非空哨兵 <EMPTY> 让首字段永不为空；② 只去行尾，不去行首。
    raw = _mysql("SELECT IFNULL(NULLIF(commit_hash,''),'<EMPTY>'), file_count, "
                 "IFNULL(change_type,'') "
                 "FROM t_code_changeset WHERE changeset_no='%s' AND is_del=0" % changeset_no)
    lines = [l for l in raw.split("\n") if l.strip() != ""]
    if not lines:
        raise LookupError("查无此单: %s" % changeset_no)
    parts = lines[0].rstrip("\r\n").split("\t")
    if parts and parts[0] == "<EMPTY>":
        parts[0] = ""
    paths = [l.strip() for l in _mysql(
        "SELECT f.file_path FROM t_code_change_file f JOIN t_code_changeset c ON f.changeset_id=c.id "
        "WHERE c.changeset_no='%s' AND c.is_del=0 AND f.is_del=0" % changeset_no).split("\n") if l.strip()]
    return {"commit_hash": parts[0], "file_count": parts[1] if len(parts) > 1 else "0",
            "change_type": parts[2] if len(parts) > 2 else "", "paths": paths}


def fetch_hash_rows(changeset_no: str) -> list[dict]:
    """A9 专用：动作、路径、前后哈希、HEX(after_content)。不改 fetch()，以免动 A1–A8 取数。"""
    raw = _mysql(
        "SELECT f.change_action, f.file_path, "
        "IFNULL(f.content_hash_before,''), IFNULL(f.content_hash_after,''), "
        "IFNULL(HEX(f.after_content),'') "
        "FROM t_code_change_file f JOIN t_code_changeset c ON f.changeset_id=c.id "
        "WHERE c.changeset_no='%s' AND c.is_del=0 AND f.is_del=0" % changeset_no)
    rows = []
    for line in raw.split("\n"):
        if line.strip() == "":
            continue
        parts = line.rstrip("\r\n").split("\t")
        hex_body = parts[4] if len(parts) > 4 else ""
        try:
            body = bytes.fromhex(hex_body) if hex_body else b""
        except ValueError:
            body = b""
        rows.append({
            "change_action": parts[0] if parts else "",
            "file_path": parts[1] if len(parts) > 1 else "",
            "content_hash_before": parts[2] if len(parts) > 2 else "",
            "content_hash_after": parts[3] if len(parts) > 3 else "",
            "after_content_bytes": body,
        })
    return rows


def is_ancestor(commit_hash: str) -> bool | None:
    repo = ds.resolve_repo(commit_hash) if commit_hash and commit_hash not in NULLISH else None
    if repo is None:
        return None
    r = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", commit_hash, MERGE_BASE_REF],
                       capture_output=True, text=True)
    return r.returncode == 0


def evaluate(changeset_no: str, include_a6: bool = True) -> dict:
    d = fetch(changeset_no)
    res = []
    ok1, w1 = a1_anchor_format(d["commit_hash"], d["change_type"]); res.append(("A1", ok1, w1))

    h = d["commit_hash"].strip()
    if h in NULLISH:
        res.append(("A2", True, "N/A（无锚点，交 A1 判）"))
    else:
        repo = ds.resolve_repo(h)
        res.append(("A2", repo is not None,
                    ("解析到仓库 %s" % repo) if repo else "锚点在已知仓库中均不存在"))

    ok3, w3 = a3_path_shape(d["paths"]); res.append(("A3", ok3, w3))
    ok4, w4 = a4_count_match(d["file_count"], len(d["paths"]), d["change_type"]); res.append(("A4", ok4, w4))

    r5 = ds.recheck_consistency(h, set(d["paths"]), d["change_type"])
    res.append(("A5", r5["verdict"] in ("PASS", "N/A"), "%s: %s" % (r5["verdict"], r5["reason"][:120])))

    if include_a6:
        ok6, w6 = a6_not_yet_merged(is_ancestor(h)); res.append(("A6", ok6, w6))
    ok7, w7 = a7_no_format(changeset_no); res.append(("A7", ok7, w7))
    ok8, w8 = a8_registered_tests(h, d["paths"]); res.append(("A8", ok8, w8))
    ok9, w9 = a9_check(changeset_no); res.append(("A9", ok9, w9))

    failed = [c for c, ok, _ in res if not ok]
    return {"changeset_no": changeset_no, "verdict": "PASS" if not failed else "HOLD",
            "failed": failed, "detail": res}


# ── 自检：每条判据正反例都要有（硬约定 5/6/11）────────────────────────
# 正例多数取自今天的真病例。**其中几单今天已被修好**（0008/0017/0099/0052/0053），
# 所以用的是**记档下来的当时值**，不是现库值——现库已查不到那些形态了。
# 这正是硬约定 9 要的「存量带病」场景：判据必须认得出病，哪怕病已经被治好。

CASES = [
    # (判据, 用例名, 调用, 期望)
    ("A1", "正例·9位短哈希(CS-<日期>-0008 当时值)", lambda: a1_anchor_format("c157643bb", "bugfix"), False),
    ("A1", "正例·9位短哈希(CS-<日期>-0108 当时值)", lambda: a1_anchor_format("1d04e36e9", "feature"), False),
    ("A1", "正例·空锚点且非免锚点类型", lambda: a1_anchor_format("", "config"), False),
    ("A1", "反例·标准40位", lambda: a1_anchor_format("c157643bbfef4f5356cc0455fd3cb1dcf0462c10", "bugfix"), True),
    ("A1", "反例·data_migration 空锚点(合法免锚点)", lambda: a1_anchor_format("NULL", "data_migration"), True),

    ("A3", "正例·引号包裹的转义路径(0017 当时值)",
     lambda: a3_path_shape(['"350277201347247273345244207344273275/scripts/kd-purchdocs-import.py"']), False),
    ("A3", "正例·反斜杠八进制标准形态(git 默认输出)",
     lambda: a3_path_shape([r'"\350\277\201\347\247\273/scripts/x.py"']), False),
    ("A3", "反例·正常中文路径(修复后的真值，不得误杀)",
     lambda: a3_path_shape(["迁移备份/scripts/kd-purchdocs-import.py"]), True),
    ("A3", "反例·含数字的正常路径(不得把 02- 当八进制残留)",
     lambda: a3_path_shape(["docs/02-接口契约/PRD/README.md"]), True),
    ("A3", "反例·纯英文路径", lambda: a3_path_shape(["frontend/src/views/delivery/index.vue"]), True),

    ("A4", "正例·file_count>0 但明细 0(全库实测 2 单)", lambda: a4_count_match("7", 0, "feature"), False),
    ("A4", "正例·数量不符", lambda: a4_count_match("7", 5, "feature"), False),
    ("A4", "正例·明细 0 且非免明细类型(全库 31 单)", lambda: a4_count_match("0", 0, "config"), False),
    ("A4", "反例·数量相符", lambda: a4_count_match("9", 9, "bugfix"), True),
    ("A4", "反例·data_migration 明细 0(合法免明细)", lambda: a4_count_match("0", 0, "data_migration"), True),
    ("A4", "反例·verification 明细 0(全库3单实测，验证类天生无文件)",
     lambda: a4_count_match("0", 0, "verification"), True),
    ("A4", "正例·config 明细 0(**不在免明细白名单**，仍须 HOLD)",
     lambda: a4_count_match("0", 0, "config"), False),
    ("A1", "反例·verification 有锚点(免明细≠免锚点，锚点照查)",
     lambda: a1_anchor_format("1c64354ceb29ef" + "0"*26, "verification"), True),
    ("A1", "正例·verification 空锚点(免明细不代表免锚点)",
     lambda: a1_anchor_format("", "verification"), False),

    ("A6", "正例·登记时已并线(CS-<日期>-0004 形态)", lambda: a6_not_yet_merged(True), False),
    ("A6", "反例·尚未并线(正常顺序)", lambda: a6_not_yet_merged(False), True),
    ("A6", "反例·无可判锚点(交 A1/A2，不在此处误杀)", lambda: a6_not_yet_merged(None), True),

    ("A7", "正例·非标单号且不在例外名单", lambda: a7_no_format("QM-FOO-BAR"), False),
    ("A7", "正例·像但不是(位数不对)", lambda: a7_no_format("CS-<日期>-016"), False),
    ("A7", "反例·标准单号", lambda: a7_no_format("CS-<日期>-0016"), True),
    ("A7", "反例·例外名单内的历史单(CS-<日期>-QM-P0B)", lambda: a7_no_format("CS-<日期>-QM-P0B"), True),
    ("A7", "反例·例外名单内的历史单(REG-SEED-V1)", lambda: a7_no_format("REG-SEED-V1-20260804"), True),

    ("A8", "正例·基线 RBAC 写型 candidate-readonly", lambda: _a8_fixture_check(A8_BASELINE_TESTS[0]), False),
    ("A8", "正例·基线 AUTHREMIND 写型 candidate-readonly", lambda: _a8_fixture_check(A8_BASELINE_TESTS[1]), False),
    ("A8", "反例·CandidateReadonlyHealthControllerTest 只读", lambda: _a8_negative_fixture_check("CandidateReadonlyHealthControllerTest.java"), True),
    ("A8", "反例·LedgerHarness 只读", lambda: _a8_negative_fixture_check("sales/ledger/LedgerHarness.java"), True),
    ("A8", "反例·修正后 RBAC candidate-write", lambda: _a8_fixture_check(A8_BASELINE_TESTS[0], corrected=True), True),
    ("A8", "反例·修正后 AUTHREMIND candidate-write", lambda: _a8_fixture_check(A8_BASELINE_TESTS[1], corrected=True), True),

    ("A9", "正例·ADD 缺 after 哈希(0009 形态)",
     lambda: a9_hash_complete("CS-X", [{"change_action": "ADD", "file_path": "a.java",
                                       "content_hash_after": ""}]), False),
    ("A9", "正例·MODIFY 前后皆空(0009 形态)",
     lambda: a9_hash_complete("CS-X", [{"change_action": "MODIFY", "file_path": "a.java",
                                       "content_hash_before": "", "content_hash_after": ""}]), False),
    ("A9", "正例·DELETE 缺 before 哈希",
     lambda: a9_hash_complete("CS-X", [{"change_action": "DELETE", "file_path": "a.java",
                                       "content_hash_before": ""}]), False),
    ("A9", "正例·抽样哈希与 after_content 不一致",
     lambda: a9_sample_rehash([{"file_path": "a.java", "content_hash_after": "0" * 64,
                               "after_content_bytes": b"hello"}]), False),
    ("A9", "反例·ADD 仅 after 非空(before 空正常)",
     lambda: a9_hash_complete("CS-X", [{"change_action": "ADD", "file_path": "a.java",
                                       "content_hash_after": "ab" * 32}]), True),
    ("A9", "反例·MODIFY 前后皆非空",
     lambda: a9_hash_complete("CS-X", [{"change_action": "MODIFY", "file_path": "a.java",
                                       "content_hash_before": "ab" * 32,
                                       "content_hash_after": "cd" * 32}]), True),
    ("A9", "反例·抽样 sha256(content) 一致",
     lambda: a9_sample_rehash([{"file_path": "a.java",
                               "content_hash_after": _sha256_hex(b"hello"),
                               "after_content_bytes": b"hello"}]), True),
]


def selftest() -> int:
    ok = True
    hits: dict[str, list[int]] = {}
    for code, name, fn, want in CASES:
        got, why = fn()
        good = (got == want)
        ok &= good
        hits.setdefault(code, [0, 0])
        hits[code][0 if want else 1] += 1
        print("  %-3s %-46s → %-5s 期望 %-5s %s" % (code, name[:46], got, want, "OK" if good else "**不符**"))
        if not good:
            print("        判词: %s" % why)
    print("\n逐判据正反例覆盖（硬约定 11：任一极为 0 即该分支未取证）：")
    for code in ("A1", "A3", "A4", "A6", "A7", "A8", "A9"):
        pos, neg = hits.get(code, [0, 0])[1], hits.get(code, [0, 0])[0]
        mark = "OK" if pos and neg else "**缺一极**"
        print("    %s  正例(应 HOLD) %d 个 / 反例(应放行) %d 个  %s" % (code, pos, neg, mark))
        ok &= bool(pos and neg)
    print("\n注：A2/A5 不在此处重复取证——它们复用 drift_scan 的既有实现，")
    print("    其正反例在 `drift_scan.py --recheck-selftest`（11 例，含多仓与免锚点各自两极）。")
    print("\n自检：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="登记质量前置门（只判定，不阻断）")
    ap.add_argument("changeset_no", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="对全库回溯体检（自动排除 A6）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-a6", action="store_true",
                    help="排除 A6。**调用方不是「登记那一刻」时必须加**——"
                         "A6 判的是登记时锚点是否已并线，轮询器侧调用时单子可能早已合入，会误报")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.sweep:
        nos = [l.strip() for l in _mysql(
            "SELECT changeset_no FROM t_code_changeset WHERE is_del=0 ORDER BY id").split("\n") if l.strip()]
        # [<日期>] 出数前先证明取数工具没坏。三次假结论(判据病16单/HOLD63单/误报率0)
        # 全是测量工具坏了而非判据坏了，且三次的假结果都比真结果更整齐。见 measure_guard 模块文档。
        import measure_guard as mg
        mg.guard.rowcount("全库取数条数", len(nos), mg.FULL_DB_MIN_ROWS)
        probe = []
        for cs, (want, _why) in mg.SENTINELS.items():
            try:
                got = evaluate(cs, include_a6=False)["verdict"]
            except LookupError:
                got = "NOT_FOUND"
            probe.append(got)
            mg.guard.sentinel("哨兵 " + cs, got, want)
        mg.guard.not_all_same("哨兵判定不得全同", probe)
        mg.guard.require()
        out = []
        for no in nos:
            try:
                out.append(evaluate(no, include_a6=False))   # A6 对历史单必然全中，见模块文档
            except LookupError:
                continue
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if not a.changeset_no:
        ap.error("需要 changeset_no")
    r = evaluate(a.changeset_no, include_a6=not a.no_a6)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print("%s %s%s" % (r["verdict"], a.changeset_no,
                           ("  未过: " + ",".join(r["failed"])) if r["failed"] else ""))
        for c, ok, why in r["detail"]:
            print("  %-3s %-5s %s" % (c, "PASS" if ok else "HOLD", why))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
