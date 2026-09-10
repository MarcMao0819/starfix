#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复放运行器（通用化版）：按 flow 名把 graphs/*.json 分派到对应执行器逐节点复放执行。

用法：
  python3 runner/run_graph.py <graph.json> --input <key>=<value> [--input ...] \
      --workdir <dir> [--inject <node>.output.<field>=<value> ...] [--run-tag <标签>]

  兼容旧用法：--changeset CS-XXXXXXXX-XXXX 等价于 --input changeset_no=CS-XXXXXXXX-XXXX。

只用 Python 3 标准库。三个 flow 共享同一套基础设施（BaseRunner）：
  - render/contract_ok/check_readonly（只读白名单守卫）
  - finish_node（SQLite trace_runs + run_evidence 双表留痕）
  - call_fallback/translate（claude -p --model sonnet 兜底，180s 超时，解析失败记 NEEDS_HUMAN）
  - 偏离检测三层（任一触发即兜底，回应设计文档 P1 补强）：
      ① 节点输出不符 output_contract  ② 分支谓词全不命中(no_match)  ③ 工具报错(rc!=0)

flow == "changeset-audit" 时走 ChangesetAuditRunner：do_n1..do_n14 + do_n9b 逻辑与 v4 版
本完全一致（未改一行判决逻辑），只是把公共基础设施提取到 BaseRunner，用于回归验证。
[v5.1] 两处真实缺陷修复，仅动 n1/n9b 两处、其余节点逐字不变：
  ①n9b 基线搜索原来只有『沿第一父链 sha~N(N=1..50)』一条兜底路径，含 merge commit 的
  多提交特性分支第一父链走不到 merge-base，50 层内必然搜空，把真实交付误判 FAIL
  (CS-<日期>-0052 实证：merge-base(feat/<项目>-integration, 锚点) 与 DB 明细逐字相等，
  但第一父链 1..50 全落空)。改为三级候选按序试(①sha^1 ②merge-base(feat/<项目>-integration,
  sha) ③沿第一父链 sha~N)，每级都做 base==head 退化断言，命中记录来源 via。
  ②新增入口类型短路 n1b(n1 之后、n2 之前)：change_type==data_migration 且 branch 不在
  git 中且明细无代码文件扩展名(三条同时成立，保守边界)时，整单直判 N/A，不再往后跑、
  不调模型——避免非代码交付(如纯数据迁移)撞上代码核验图五点全 NEEDS_HUMAN 还中止
  (CS-<日期>-0050 实证)。n1 的 SQL 相应加两列(change_type/branch)，output_contract
  放宽 commit_hash 段以接受 NULL(数据迁移单不挂 commit)，但格式仍然严格校验，不接受
  乱码/半截行。
[v5.2] n1b 由"数据迁移短路"升级为"入口分诊"：按序判三类，命中即分流、不再走 p1-p5。
  类型A(非代码交付)＝v5.1 原判据逐字保留。类型B(已并线，本次新增)＝
  git merge-base --is-ancestor <n1.commit_hash> <MERGE_BASE_REF_BRANCH> 成立(登记锚点
  已在集成线上)，命中后必查 runtime.db 的 trace_runs：查到该 changeset_no 并线前已有
  完整判决轮次(含 n14 记录) -> B-1 overall=SKIPPED_ALREADY_MERGED，证据引用那次
  run_id 与复算出的判词("以并线前判词为准")，这是消噪音主路径——已并线单复扫时 n9b
  的 merge-base 候选会因 base==head 退化被跳过，^1/~N 也可能因分支已并入集成线而搜不到
  与登记明细逐字相等的基线，产生纯噪音误报(CS-<日期>-0052 实证：并线后重跑 v5.1 图
  p2/p3 直接 FAIL 且白调一次模型)；查无历史 -> B-2 overall=ALERT_MERGED_WITHOUT_AUDIT，
  绝不静默跳过——该单已并线但机检从未审过，疑似绕过机检并线。类型C(预留)本次不实现
  具体判据，只留扩展点。三类都不调模型、aborted=false、SQLite 照常留痕。n2..n14/n9_llm/
  n14_llm/verdict_rules 逐字未动，n1/n9b 不再改动。

flow == "premerge-gate" 时走 PremergeGateRunner：g1(脏树)/g2(git中态)/g3(冲突标记)/g4(行尾)
四个判决点，其中 g2 的标记文件路径改用 n4 实测的 git-dir 动态拼接（而非图里硬编码的
<基座项目>-erp 绝对路径模板），使其对任意仓库/worktree 都成立——这是本轮病态仓库验证暴露
的问题，属于运行器实现修正，不改变图 JSON 本身对 g1/g3/g4 的既有判据。

flow == "receipt-compliance" 时走 ReceiptComplianceRunner：先跑分型节点(t0)拿 doc_type，
再对 e1/e2/r1-r6 逐点跑判决脚本(exec:logic，cmd_template 即 "python3 <脚本> <point> <file>"
形态，脚本自身输出就是 "verdict=" 结尾的摘要，无需额外归一化)；classification.gates 定义
的不适用判决点仍执行该步骤取证据，只是判决强制记 N/A；r6(n9)的 no_match 指向具名 llm 节点
(n9_llm) 时走模型兜底（覆盖 AMBIGUOUS 等机械判不了的取值）。

flow == "cleanup-audit" 时走 CleanupAuditRunner：c1(临时库残留)/c2(临时账号残留)/c3(僵尸授权)/
c4(隔离实例残留)/c5(worktree积压) 五个判决点，图节点 n2-n6 的 cmd_template 均为"取数据"命令
(docker exec mysql -N -e "SELECT..." / lsof / git worktree list)，本身不含 point=/verdict=
字样——判决逻辑(阈值比较、豁免名单、配对账号反查)由本 runner 在 Python 侧对原始输出解析后
计算，与 PremergeGateRunner 对 g1(`git status --porcelain`原始输出)算 tracked_dirty/conflict
是同一模式。c5 的图 cmd_template 是一整段含 `$()` 命令替换+管道的 bash for 循环，
check_readonly 的只读守卫无法安全解析这种复合脚本，因此分解成逐条独立过闸的简单只读 git
调用(worktree list / merge-base / diff --name-only)，与 changeset-audit 的 n9b 基线搜索
同一处理方式。该图同时支持自检模式(prefix_re 限自建前缀+严阈值)与巡检模式(全局前缀+宽阈值)——
两种模式共用同一套节点实现，仅输入参数不同。
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

# [<日期> 隔离接缝] 大批量试跑必须与生产**物理隔离**：本文件的 trace_runs 不只是留痕，
# 还是 n1b 类型B 判据的输入（「并线前是否已有完整判决轮次」）。试跑若写进生产库，
# 会让此后的生产轮次把试跑轮当成历史判词——本该报 ALERT_MERGED_WITHOUT_AUDIT 的单变成
# SKIPPED_ALREADY_MERGED，**等于用观察行为改变了被观察对象**。
# 环境变量 TRJ_DB_PATH 覆盖落盘位置；不设时行为与此前逐字相同。
DB_PATH = os.environ.get("TRJ_DB_PATH", "${TRAJ_HOME}/runtime.db")
REPO_ROOT = "${TRAJ_HOME}/"

# --------------------------------------------------------------------------
# readonly_guard: 命令前缀只读白名单
# --------------------------------------------------------------------------

BASH_KEYWORDS = {"for", "do", "done", "in", "then", "fi", "if", "while", "true", "false"}
# head 用于 premerge-gate g3 的 "git grep ... | head -20" 管道尾段；其余为既有只读工具。
# [cleanup-audit] lsof(c4 端口扫描)/ps(c4 逐 pid 查运行时长)，均为只读只查系统状态。
SIMPLE_COMMANDS = {"echo", "test", "diff", "grep", "wc", "sort", "sh", "head", "lsof", "ps"}
ENV_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=\S*$')
SELECT_RE = re.compile(r'-e\s+"SELECT\b', re.IGNORECASE)
# [通用化] 原仅允许 cat-file/diff/show/rev-parse，且不认识 --no-optional-locks 前缀标志。
# premerge-gate 全部节点都带 --no-optional-locks，且新增用到 status(g1)/grep(g3)/merge-base(seq7)
# 三个子命令——均为只读操作，补入白名单。新增的可选组只在“存在时才匹配”，不影响
# changeset-audit 既有命令（那批命令从不带 --no-optional-locks）。
# [行尾判据 v5/v6] 新增 log：p5/g4 的 llm 兜底节点需要把提交信息喂给模型辅助判断
# "是行尾修复还是事故"，取提交信息(git log --format=%B / %H%x09%s)是只读操作。
# -c 分组也放宽为可重复(-c core.quotepath=false 之外，判据脚本自身内部还会再传一次
# -c，此处放宽的是 run_graph.py 自身直接拼的 cmd_template，两者互不影响)。
# [cleanup-audit] 新增 worktree：c5(worktree积压) 需要 `git worktree list --porcelain`
# 只读枚举 worktree，只读操作。
GIT_SUBCMD_RE = re.compile(
    r'^git(\s+--no-optional-locks)?(\s+-C\s+\S+)?(\s+-c\s+\S+)?\s+'
    r'(cat-file|diff|show|rev-parse|status|grep|merge-base|log|worktree)\b'
)
# [通用化] receipt-compliance 全部节点是 "python3 <仓库内脚本> <point> <file>" 形态；
# 只放行脚本路径落在本仓库(${TRAJ_HOME}/)内的调用，防止任意 python3 命令绕过只读闸。
PY3_REPO_SCRIPT_PREFIX = REPO_ROOT


def split_top_level(cmd: str):
    """把命令按顶层(不在引号内)的 && || | ; 切分成段。"""
    segments = []
    buf = []
    quote = None
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if cmd[i:i + 2] in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in ("|", ";"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _leading_word(segment: str) -> str:
    m = re.match(r'^(\S+)', segment)
    return m.group(1) if m else ""


def check_readonly(cmd: str):
    """校验命令是否落在只读白名单内。返回 (ok, reason)。"""
    if not cmd or not cmd.strip():
        return False, "空命令"
    for seg in split_top_level(cmd):
        word = _leading_word(seg)
        base = os.path.basename(word)
        if base in BASH_KEYWORDS:
            if base == "do":
                rest = seg[len(word):].strip()
                if rest:
                    ok, reason = check_readonly(rest)
                    if not ok:
                        return False, reason
            continue
        if ENV_ASSIGN_RE.match(word):
            rest = seg[len(word):].strip()
            if rest:
                ok, reason = check_readonly(rest)
                if not ok:
                    return False, reason
            continue
        if base == "docker":
            if "exec" not in seg or "sh" not in seg or "-c" not in seg:
                return False, f"docker 命令形态不符白名单: {seg[:80]}"
            if not SELECT_RE.search(seg):
                return False, f"docker 内 mysql -e 载荷未以 SELECT 开头: {seg[:80]}"
            continue
        if base == "git":
            if not GIT_SUBCMD_RE.match(seg):
                return False, f"git 子命令不在白名单(cat-file/diff/show/rev-parse/status/grep/merge-base): {seg[:80]}"
            continue
        if base == "python3":
            try:
                parts = shlex.split(seg)
            except ValueError:
                return False, f"python3 命令引号无法解析: {seg[:80]}"
            script = parts[1] if len(parts) > 1 else ""
            if not script.startswith(PY3_REPO_SCRIPT_PREFIX):
                return False, f"python3 命令未指向仓库内脚本({PY3_REPO_SCRIPT_PREFIX}): {seg[:80]}"
            continue
        if base in SIMPLE_COMMANDS:
            continue
        return False, f"命令前缀不在只读白名单: {seg[:80]}"
    return True, ""


# --------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------

def render(template: str, mapping: dict) -> str:
    """把 {name} 占位符替换为值，再把字面转义的 {{ }} 还原为 { }（如 n2 的 {{40}}）。"""
    s = template
    for k, v in mapping.items():
        s = s.replace("{" + k + "}", str(v))
    return s.replace("{{", "{").replace("}}", "}")


def contract_ok(contract: str, digest: str) -> bool:
    """output_contract DSL 仅三种：regex:<正则>、nonempty、int>=0。"""
    if contract is None:
        return False
    if contract.startswith("regex:"):
        pattern = contract[len("regex:"):]
        return re.match(pattern, digest) is not None
    if contract == "nonempty":
        return len(digest.strip()) > 0
    if contract == "int>=0":
        try:
            return int(digest.strip()) >= 0
        except ValueError:
            return False
    return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# 行尾判据(p5/g4)共用：解析 runner/checks/lineending_check.py 的逐行输出
# --------------------------------------------------------------------------

LINEENDING_LINE_RE = re.compile(
    r'^status=(?P<status>\S+)\ before_cr=(?P<before_cr>\S+)\ after_cr=(?P<after_cr>\S+)\ '
    r'(?:before_lines=(?P<before_lines>\S+)\ after_lines=(?P<after_lines>\S+)\ )?'
    r'added_crlf=(?P<added_crlf>\S+)\ deleted_crlf=(?P<deleted_crlf>\S+)\ '
    r'(?:abs=(?P<abs>\S+)\ )?'
    r'verdict=(?P<verdict>\S+)\ path=(?P<path>.*)$'
)


def parse_lineending_output(out: str):
    """把 lineending_check.py 的逐文件行(不含末尾 overall_verdict= 汇总行)解析成字典列表。"""
    records = []
    for line in out.split("\n"):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("overall_verdict="):
            continue
        m = LINEENDING_LINE_RE.match(line)
        if m:
            records.append(m.groupdict())
    return records


# [轨迹约定10] 运行器是多张图共用的，节点实现与图的 cmd_template 是耦合的——就地改实现
# 会让还在跑旧图(v4/旧v5)的生产调用当场失配。用 cmd_template 里是否出现 lineending_check.py
# 作为特征分派新旧两套 n13/n9 实现，保证旧图原样跑通、判决与升级前一致。
LINEENDING_SCRIPT_MARKER = "lineending_check.py"


def uses_new_lineending_check(node: dict) -> bool:
    return LINEENDING_SCRIPT_MARKER in (node.get("cmd_template") or "")


def validate_record_list_shape(raw):
    """校验上游节点(n13/n9)的 output 是否为「list[dict(含 verdict 键)]」。
    绝不对调用方裸抛异常——返回 (clean_records, error_samples)：
      - raw 根本不是 list(如 n13 走了 fallback、resolve() 退化返回 digest 字符串)：
        clean=[]，error_samples 记类型摘要
      - raw 是 list 但含非 dict / 缺 verdict 键的元素：该元素计入 error_samples 并跳过，
        其余合法元素仍返回在 clean 里(不因个别脏元素整体判死)
    """
    if not isinstance(raw, list):
        return [], [f"RUNNER_ERR: 期望 list，实际类型={type(raw).__name__} 值摘要={str(raw)[:200]}"]
    clean = []
    errors = []
    for item in raw:
        if isinstance(item, dict) and "verdict" in item:
            clean.append(item)
        else:
            errors.append(f"RUNNER_ERR: 非法元素 类型={type(item).__name__} 值摘要={str(item)[:200]}")
    return clean, errors


# --------------------------------------------------------------------------
# cleanup-audit(c1-c5) 共用解析小工具
# --------------------------------------------------------------------------

def split_by_markers(lines, markers):
    """按字面 marker 行(如 __GRANTS__/__SCHEMATA__)把 `mysql -N` 一次多语句执行的多个
    结果集顺序输出切成多段(每个 marker 行本身不计入任何一段)。"""
    sections = [[]]
    for line in lines:
        if line.strip() in markers:
            sections.append([])
        else:
            sections[-1].append(line)
    return sections


def parse_int_list(raw):
    """把 CLI 传入的 '${PORT_APP},${PORT_WEB},...' 或单个整数字符串解析成 int 列表；已是 list(程序化
    调用场景)则逐项转 int。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw]
    raw = str(raw).strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_port_ranges(port_lo_raw, port_hi_raw):
    """port_lo/port_hi 支持标量(单区间，如巡检模式收窄到某个子段)或逗号分隔/list 的等长
    并行区间(如生产 java 段+node 段两个区间)，统一返回 [(lo,hi), ...]。"""
    is_multi_lo = isinstance(port_lo_raw, list) or "," in str(port_lo_raw)
    is_multi_hi = isinstance(port_hi_raw, list) or "," in str(port_hi_raw)
    los = parse_int_list(port_lo_raw) if is_multi_lo else [int(port_lo_raw)]
    his = parse_int_list(port_hi_raw) if is_multi_hi else [int(port_hi_raw)]
    if len(los) != len(his):
        raise ValueError(f"port_lo/port_hi 长度不一致: {los} vs {his}")
    return list(zip(los, his))


ETIME_RE = re.compile(r'^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$')


def parse_etime_hours(etime):
    """把 `ps -o etime=` 输出([[DD-]HH:]MM:SS，例如 00:04:34 / 1-22:50:44)解析成小时数
    (float)；格式不识别时返回 None(不裸抛异常，调用方按"未知运行时长"处理)。"""
    if not etime:
        return None
    m = ETIME_RE.match(etime.strip())
    if not m:
        return None
    days, hours, minutes, seconds = m.groups()
    return int(days or 0) * 24 + int(hours or 0) + int(minutes) / 60.0 + int(seconds) / 3600.0


LSOF_LISTEN_RE = re.compile(
    r'^(?P<cmd>\S+)\s+(?P<pid>\d+)\s+(?P<user>\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<name>\S+)'
)
PORT_SUFFIX_RE = re.compile(r':(\d+)$')


def unescape_mysql_db_pattern(db):
    """`mysql.db`.Db 列存的是 GRANT 目标的 LIKE 模式，字面下划线/百分号会被 MySQL 转义成
    \\_ / \\%；而 `mysql -N` 客户端自身输出时又会把数据里的每个反斜杠再转义(打印)成两个，
    所以经 subprocess 读到的原始字段值里，一个字面下划线实际前面带的是两个反斜杠字符。
    两步都要撤销才能还原出真实库名，否则任何库名含下划线的正常授权(例如 ${DB_NAME})
    都会被误判成"目标库不存在"的僵尸授权/未在用账号——这不是边界情况，是本项目几乎全部
    库名(drill_x/e2e_ds7_app/${DB_NAME}...)都会踩中的必现 bug。"""
    return db.replace("\\\\", "\\").replace("\\_", "_").replace("\\%", "%")


def parse_lsof_listen(out):
    """解析 `lsof -nP -iTCP -sTCP:LISTEN` 输出，返回 [{"cmd","pid","user","port"}, ...]。
    跳过表头行、以及少数 NAME 字段解析不出末尾端口号的行(不裸抛异常，静默跳过——个别行
    的格式意外不应让整个 c4 判据崩溃，与约定10"元素级形状校验"同一原则)。"""
    results = []
    for line in out.split("\n"):
        if not line.strip() or line.startswith("COMMAND"):
            continue
        m = LSOF_LISTEN_RE.match(line)
        if not m:
            continue
        pm = PORT_SUFFIX_RE.search(m.group("name"))
        if not pm:
            continue
        results.append({
            "cmd": m.group("cmd"), "pid": int(m.group("pid")),
            "user": m.group("user"), "port": int(pm.group(1)),
        })
    return results


# --------------------------------------------------------------------------
# BaseRunner：四个 flow 共享的基础设施
# --------------------------------------------------------------------------

class BaseRunner:
    def __init__(self, graph: dict, input_vars: dict, workdir: str, run_tag: str, injections: dict):
        self.graph = graph
        self.nodes_by_id = {n["id"]: n for n in graph["nodes"]}
        self.input = dict(input_vars)
        self.workdir = workdir
        self.run_tag = run_tag
        self.injections = injections
        self.run_id = uuid.uuid4().hex[:16]
        self.results = {}          # node_id -> dict
        self.step_digests = []     # ["n1: ...", "n2: ...", ...]
        self.verdicts = {}         # 判决点键 -> PASS/FAIL/N/A/NEEDS_HUMAN/DEFER
        self.model_calls = 0
        self.db_rows = 0
        self.aborted = False
        self.conn = sqlite3.connect(DB_PATH)
        # trace_runs 表的关联键：changeset-audit 用 changeset_no；其它 flow 无该字段时
        # 退化取 receipt/worktree，都没有则用全部 input 拼一个稳定 key——不改表结构。
        self.trace_key = (
            self.input.get("changeset_no")
            or self.input.get("receipt")
            or self.input.get("worktree")
            or "|".join(f"{k}={v}" for k, v in sorted(self.input.items()))
        )

    # ---- 基础设施 ----
    def node(self, node_id: str) -> dict:
        return self.nodes_by_id[node_id]

    def resolve(self, path: str):
        """解析 'input.x' / 'nX.output' / 'nX.output.field' 形式的绑定来源。"""
        parts = path.split(".")
        if parts[0] == "input":
            return self.input.get(parts[1])
        node_id = parts[0]
        r = self.results.get(node_id)
        if not r:
            return None
        if len(parts) == 2:
            return r["list"] if r.get("list") is not None else r.get("digest")
        field = parts[2]
        return r.get("fields", {}).get(field)

    def apply_injection(self, node_id: str, fields: dict) -> dict:
        inj = self.injections.get(node_id, {})
        for k, v in inj.items():
            if k == "drop_first":
                continue
            fields[k] = v
        return fields

    def run_shell(self, cmd: str, timeout: int = 30):
        try:
            p = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "TIMEOUT"

    def finish_node(self, node_id, raw, digest, fields, list_, status, verdict, cmd, duration_ms):
        self.results[node_id] = {
            "raw": raw, "digest": digest, "fields": fields or {}, "list": list_,
            "status": status, "verdict": verdict, "cmd": cmd,
        }
        self.step_digests.append(f"{node_id}: {digest}")
        ts = now_iso()
        cmd_sha = hashlib.sha256((cmd or "").encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT INTO trace_runs (run_id, run_tag, changeset_no, node_id, status, output_digest, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (self.run_id, self.run_tag, self.trace_key, node_id, status, digest, ts),
        )
        self.conn.execute(
            "INSERT INTO run_evidence (run_id, node_id, cmd_sha256, digest, duration_ms, ts) "
            "VALUES (?,?,?,?,?,?)",
            (self.run_id, node_id, cmd_sha, digest, duration_ms, ts),
        )
        self.conn.commit()
        self.db_rows += 2

    # ---- fallback 兜底调模型 ----
    def call_fallback(self, node: dict, raw_output: str, contract_str: str) -> dict:
        self.model_calls += 1
        node_id = node.get("id", "?")
        comment = node.get("comment", "") or node.get("purpose", "")
        ambiguous = node.get("ambiguous_examples")
        ambiguous_str = json.dumps(ambiguous, ensure_ascii=False) if ambiguous else "无"
        digest_list_str = "; ".join(self.step_digests) if self.step_digests else "无"
        prompt = (
            "你是确定性工作流的兜底裁决员。"
            f"流程={self.graph.get('flow', '?')}，当前节点={node_id}+{comment}，"
            f"契约={contract_str}，"
            f"实际输出={(raw_output or '')[:500]}，"
            f"此前各步摘要={digest_list_str}，"
            f"参考样例={ambiguous_str}。"
            '请只输出 JSON：{"pass":true/false,"reason":"中文一句话"}'
        )
        try:
            p = subprocess.run(["claude", "-p", "--model", "sonnet", prompt],
                                capture_output=True, text=True, timeout=180)
            stdout = p.stdout.strip()
            m = re.search(r"\{.*\}", stdout, re.DOTALL)
            if m:
                obj = json.loads(m.group(0))
                if "pass" in obj:
                    return {"pass": bool(obj["pass"]), "reason": obj.get("reason", ""),
                             "parse_ok": True, "raw": stdout}
            return {"pass": None, "reason": "PARSE_FAIL", "parse_ok": False, "raw": stdout}
        except subprocess.TimeoutExpired:
            return {"pass": None, "reason": "TIMEOUT", "parse_ok": False, "raw": ""}
        except FileNotFoundError:
            return {"pass": None, "reason": "CLAUDE_CLI_NOT_FOUND", "parse_ok": False, "raw": ""}
        except Exception as e:  # noqa: BLE001
            return {"pass": None, "reason": f"EXCEPTION:{e}", "parse_ok": False, "raw": ""}

    @staticmethod
    def translate(fb: dict) -> str:
        if not fb.get("parse_ok"):
            return "NEEDS_HUMAN"
        return "PASS" if fb["pass"] else "FAIL"

    # ---- 三种失败落地方式(通用，供各 flow 的节点实现调用) ----
    def _tool_fallback(self, node_id, node, cmd, raw_output, t0):
        fb = self.call_fallback(node, raw_output, node.get("output_contract", ""))
        status = self.translate(fb)
        digest = f"[FALLBACK:{node_id}] pass={fb.get('pass')} reason={fb.get('reason')}"
        dur = int((time.time() - t0) * 1000)
        self.finish_node(node_id, raw_output, digest, {}, None, status, status, cmd, dur)

    def _blocked_fallback(self, node_id, node, missing_desc, t0=None):
        t0 = t0 if t0 is not None else time.time()
        self._tool_fallback(node_id, node, "", f"BLOCKED: {missing_desc}", t0)

    def _branch_fallback(self, node_id, node, cmd, raw_output, t0, verdict_point):
        fb = self.call_fallback(node, raw_output, node.get("output_contract", ""))
        verdict = self.translate(fb)
        self.verdicts[verdict_point] = verdict
        digest = f"[FALLBACK:{node_id}] pass={fb.get('pass')} reason={fb.get('reason')}"
        dur = int((time.time() - t0) * 1000)
        self.finish_node(node_id, raw_output, digest, {}, None, verdict, verdict, cmd, dur)

    def llm_route(self, llm_node_id, calling_node, context_raw):
        """通用具名 llm 节点兜底：某判决节点的 no_match 指向一个 type=="llm" 的节点时走这里
        （如 receipt-compliance 的 n9(r6) -> n9_llm）。与 changeset-audit 里 n9 手写调用
        n9_llm 的方式等价，只是抽成通用方法供其它 flow 复用。"""
        llm_node = self.node(llm_node_id)
        t1 = time.time()
        fb = self.call_fallback(llm_node, context_raw, calling_node.get("output_contract", ""))
        verdict = self.translate(fb)
        vp = llm_node.get("verdict_point") or calling_node.get("verdict_point")
        if vp:
            self.verdicts[vp] = verdict
        digest = f"[LLM] pass={fb.get('pass')} reason={fb.get('reason')}"
        dur = int((time.time() - t1) * 1000)
        self.finish_node(llm_node_id, context_raw, digest, {}, None, verdict, verdict, None, dur)
        return verdict

    def overall(self) -> str:
        """通用兜底版本：任一 FAIL -> FAIL；任一 None/NEEDS_HUMAN/DEFER -> NEEDS_HUMAN；
        N/A 视为满足，不计入 FAIL/NEEDS_HUMAN。子类可按需覆盖（ChangesetAuditRunner 覆盖为
        逐字保留旧版 p1..p5 固定顺序实现，避免任何行为漂移）。"""
        vals = list(self.verdicts.values())
        if any(v == "FAIL" for v in vals):
            return "FAIL"
        if any(v is None or v in ("NEEDS_HUMAN", "DEFER") for v in vals):
            return "NEEDS_HUMAN"
        return "PASS"


# --------------------------------------------------------------------------
# [v5.1] n1b 入口类型短路共用：可识别的"代码文件"扩展名白名单(小写，含点)。
# 刻意不含 .sql/.tsv/.csv/.xlsx 等数据/快照类扩展名——data_migration 类交付的典型明细
# 正是这些格式(CS-<日期>-0050 实证：.tsv + .sql 表快照)，若把 .sql 也算作"代码"，
# 短路的第三条判据永远不成立，短路就形同虚设。
# --------------------------------------------------------------------------
CODE_EXTENSIONS = {
    ".java", ".ts", ".tsx", ".js", ".jsx", ".vue", ".py", ".go", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".sh", ".yml", ".yaml", ".json", ".xml",
    ".html", ".htm", ".css", ".scss", ".less", ".kt", ".swift", ".rs", ".md",
    ".properties", ".gradle", ".mjs", ".cjs",
}


# --------------------------------------------------------------------------
# ChangesetAuditRunner：changeset-audit.v4 专用实现，逐字保留旧版判决逻辑(回归基准)
# --------------------------------------------------------------------------

class ChangesetAuditRunner(BaseRunner):
    """v4 图（16 节点：n1..n14 + n9b）。方法体与通用化之前的 run_graph.py 完全一致，
    只是把公共基础设施（guard/render/contract/DB落盘/兜底调模型）移到了 BaseRunner。"""

    # ---- 节点实现 ----
    def do_n1(self):
        node = self.node("n1")
        t0 = time.time()
        changeset_no = self.input["changeset_no"]
        cmd = render(node["cmd_template"], {"changeset_no": changeset_no})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n1", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n1", node, cmd, out + err, t0)
            return
        lines = [l for l in out.split("\n") if l != ""]
        if len(lines) < 2:
            self._tool_fallback("n1", node, cmd, out, t0)
            return
        header = lines[0].split("\t")
        row = lines[1].split("\t")
        fields = dict(zip(header, row))
        fields = self.apply_injection("n1", fields)
        digest = "\t".join(f"{h}={fields.get(h, '')}" for h in header)
        if not contract_ok(node["output_contract"], digest):
            # [AUDITFIX <日期>] 此前只把原始表格喂给兜底模型，模型于是抱怨「字段值前
            # 没有 id=/changeset_no= 前缀」——那个前缀本来就是上面这行自己拼的，不是真违约；
            # 真违约是字段值本身(0008 是 commit_hash 只有 9 位)。判词指错方向，直接导致这批
            # 告警被误诊成「执行器崩溃」，白排查一轮。两者都给，模型才看得到真因。
            ctx = "[运行器转换后、待校验的值]\n%s\n\n[原始查询输出]\n%s" % (digest, out)
            self._tool_fallback("n1", node, cmd, ctx, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n1", out, digest, fields, None, "OK", None, cmd, dur)

    def do_n1b(self):
        """[v5.2 升级：n1b 入口分诊] 在 n1 之后、n2 之前，按序判三类，命中即分流、不再往后
        跑 p1-p5：
          类型 A(非代码交付，[v5.1]原判据逐字保留)：change_type=='data_migration' 且 branch
            在 git 中不存在 且 明细无代码扩展名 -> 整单判 N/A。
          类型 B(已并线，[v5.2]本次新增)：git merge-base --is-ancestor <n1.commit_hash>
            <集成线HEAD(MERGE_BASE_REF_BRANCH)> 成立(锚点已在集成线上，说明该单的代码已
            经并线) -> 再查 runtime.db 的 trace_runs 找并线前是否已有完整判决轮次(含 n14
            节点记录)：有 -> B-1 overall=SKIPPED_ALREADY_MERGED(证据引用那次 run_id 与其
            判词，"以并线前判词为准"，这是消噪音的主路径——复扫已并线单时 n9b 的 merge-base
            候选会退化成 base==head 被跳过，^1/~N 候选也可能因分支已被并入集成线而搜不到
            与登记明细逐字相等的基线，产生纯噪音误报，见 CS-<日期>-0052 实证：已并线后
            重跑 v5.1 图 p2/p3 直接 FAIL 且白调一次模型)；无 -> B-2
            overall=ALERT_MERGED_WITHOUT_AUDIT，绝不静默跳过——该单已并线但机检从未审过，
            疑似绕过机检并线，必须报警而非放行。
          类型 C(预留扩展点，本次不实现具体判据)：留空，直接落到 CONTINUE。
        三类都不调模型、aborted=false、SQLite 照常留痕。都不命中 -> CONTINUE，从 n2 起走
        完整核验(与 v5.1 完全一致)。
        返回值：命中类型 A -> "N/A"；命中类型 B-1 -> "SKIPPED_ALREADY_MERGED"；命中类型
        B-2 -> "ALERT_MERGED_WITHOUT_AUDIT"；都不命中 -> None(继续正常流程)。
        """
        t0 = time.time()

        # ---------- 类型 A：非代码交付([v5.1]原判据，逐字保留，仅由 bool 返回值改为分诊标签) ----------
        change_type = self.resolve("n1.output.change_type")
        a_note = f"type_a_skip(change_type={change_type})"
        if change_type == "data_migration":
            branch = self.resolve("n1.output.branch")
            cid = self.resolve("n1.output.id")

            branch_missing = True
            if branch:
                rev_cmd = f"git -C ${FLEET_INTEGRATION_REPO} rev-parse --verify --quiet {shlex.quote(branch)}"
                ok, _reason = check_readonly(rev_cmd)
                if ok:
                    rc, _out, _err = self.run_shell(rev_cmd)
                    branch_missing = (rc != 0)
                # 守卫拦下：无法证实 branch 存在，保守按"未证实存在"处理，branch_missing 维持 True

            no_code_ext = False  # 保守默认：查不到明细就不能断言"无代码扩展名"，不短路
            ext_note = "no_changeset_id"
            if cid is not None:
                cmd = render(self.node("n7")["cmd_template"], {"changeset_id": cid})
                ok2, reason2 = check_readonly(cmd)
                if ok2:
                    rc2, out2, _err2 = self.run_shell(cmd)
                    if rc2 == 0:
                        files = [l for l in out2.split("\n") if l.strip() != ""]
                        exts = {os.path.splitext(f)[1].lower() for f in files}
                        hit = sorted(exts & CODE_EXTENSIONS)
                        no_code_ext = not hit
                        ext_note = f"{len(files)}_files exts={sorted(exts)[:10]} code_ext_hit={hit}"
                    else:
                        ext_note = "SQL_FAIL"
                else:
                    ext_note = f"GUARD_BLOCKED:{reason2}"

            if branch_missing and no_code_ext:
                digest = (
                    f"SHORT_CIRCUIT change_type=data_migration branch={branch}(not_in_git) file_check={ext_note}"
                )
                dur = int((time.time() - t0) * 1000)
                self.finish_node("n1b", "", digest, {"decision": "SHORT_CIRCUIT", "type": "A"}, None,
                                  "SHORT_CIRCUIT", None, None, dur)
                return "N/A"
            a_note = (
                f"type_a_no_hit(branch_missing={branch_missing} no_code_ext={no_code_ext} file_check={ext_note})"
            )

        # ---------- 类型 B：已并线([v5.2]本次新增) ----------
        sha = self.resolve("n1.output.commit_hash")
        b_hit, b_decision, b_digest, b_fields = self._n1b_check_type_b(sha)
        if b_hit:
            dur = int((time.time() - t0) * 1000)
            fields = dict(b_fields)
            fields["decision"] = b_decision
            fields["type"] = "B"
            self.finish_node("n1b", "", b_digest, fields, None, b_decision, None, None, dur)
            return b_decision

        # ---------- 类型 C：预留扩展点，本次不实现具体判据 ----------
        # TODO(扩展点，未实现)：未来如需新增其它入口级分流判据(如"已作废/已回滚单"等)，
        # 在此按同样的"命中即分流、不命中则往下"的模式插入，返回值加一个新的分诊标签即可，
        # 不需要改 run()/overall() 的调度逻辑(两者已按分诊标签通用处理，见下)。
        c_note = "type_c_not_implemented(reserved)"

        dur = int((time.time() - t0) * 1000)
        digest = f"CONTINUE {a_note}; type_b={b_digest}; {c_note}"
        self.finish_node("n1b", "", digest, {"decision": "CONTINUE"}, None, "CONTINUE", None, None, dur)
        return None

    def _n1b_check_type_b(self, sha):
        """[v5.2 新增] 类型 B 判据：git merge-base --is-ancestor <登记锚点=sha>
        <集成线HEAD=MERGE_BASE_REF_BRANCH> 成立即"已并线"(锚点已在集成线上)。命中后必须
        再查 runtime.db 的 trace_runs——绝不能命中就静默判定，那样"带外并线、从未审过"的
        单子会被静默放行，正是这道分诊要堵的洞：
          - 存在该 changeset_no 并线前产生的完整判决轮次(定义：trace_runs 里有该单的
            node_id='n1' 记录且其 commit_hash 与本次登记锚点 sha 完全一致——changeset 的
            登记锚点可能在生命周期内被改写(实证：CS-<日期>-0054 首次登记为 commit
            2a62ca82...file_count=4，后来被改登记为 fb0f66a7...file_count=2，两者是不同
            commit；若不核对 sha 一致，会把"针对旧 commit 的判词"错当成"针对当前登记
            commit 的判词"引用，判词与实际登记状态对不上)，且同一 run_id 下还有
            node_id='n14' 的记录(p5 的决策节点，只有 n1 通过校验且未被 n1b 短路时才会跑
            到，标志这轮走完了机械核验) -> B-1，取满足条件里时间最新的一轮，逐点在该
            run_id 内找最后写入的判决值复算 overall，返回 SKIPPED_ALREADY_MERGED，证据带
            prior_run_id/prior_ts/prior_overall/prior_verdicts。
          - 查无这样的轮次(从未审过，或历史轮次审的都是已被改写掉的旧 commit) -> B-2，
            返回 ALERT_MERGED_WITHOUT_AUDIT，证据写明疑似绕过机检。
        返回 (hit, decision, digest_detail, fields)。sha 为空/守卫拦下/命令报错/确认不是
        祖先(未并线，正常场景)都返回 hit=False(不命中类型 B，调用方继续判类型 C/CONTINUE)。
        """
        if not sha:
            return False, None, "sha_unavailable(not_checked)", {}
        cmd = f"git -C ${FLEET_INTEGRATION_REPO} merge-base --is-ancestor {shlex.quote(sha)} {self.MERGE_BASE_REF_BRANCH}"
        ok, reason = check_readonly(cmd)
        if not ok:
            return False, None, f"GUARD_BLOCKED:{reason}", {}
        rc, out, err = self.run_shell(cmd)
        # rc==0: sha 是 ref 的祖先(已并线)；rc==1: 不是祖先(未并线，正常场景，非错误)；
        # 其它 rc(如 sha 非法对象): 命令本身出错，保守按"不命中"处理，不当作已并线。
        if rc not in (0, 1):
            return False, None, f"MERGE_BASE_CMD_ERR rc={rc} out={out[:200]} err={err[:200]}", {}
        if rc == 1:
            return False, None, f"not_merged(sha={sha} ref={self.MERGE_BASE_REF_BRANCH})", {}

        prior = self._lookup_prior_complete_run(self.input["changeset_no"], sha)
        if prior is not None:
            run_id, ts, verdicts, overall = prior
            verdicts_flat = ",".join(f"{k}:{verdicts[k]}" for k in ("p1", "p2", "p3", "p4", "p5"))
            digest = (
                f"SKIPPED_ALREADY_MERGED sha={sha} ref={self.MERGE_BASE_REF_BRANCH} "
                f"prior_run_id={run_id} prior_ts={ts} prior_overall={overall} prior_verdicts={verdicts_flat} "
                f"(以并线前判词为准，且该轮审的正是当前登记 commit={sha})"
            )
            fields = {
                "prior_run_id": run_id, "prior_ts": ts, "prior_overall": overall,
                **{f"prior_{k}": v for k, v in verdicts.items()},
            }
            return True, "SKIPPED_ALREADY_MERGED", digest, fields
        digest = (
            f"ALERT_MERGED_WITHOUT_AUDIT sha={sha} ref={self.MERGE_BASE_REF_BRANCH} "
            f"reason=该单已并线但机检从未针对当前登记commit={sha}审过(无历史轮次，或历史轮次审的是已被改写掉的旧commit)，疑似绕过机检并线"
        )
        return True, "ALERT_MERGED_WITHOUT_AUDIT", digest, {}

    def _lookup_prior_complete_run(self, changeset_no, sha):
        """[v5.2 新增] 在 trace_runs 里找该 changeset_no 针对当前登记锚点 sha 曾经产生过的、
        走完机械核验拿到终态判决的历史轮次。两条件都要满足：①该 run 的 n1 记录里
        commit_hash 字面等于本次登记锚点 sha(用 LIKE '%commit_hash=<sha>%' 匹配 n1 digest
        的 'id=..\\tcommit_hash=<sha>\\tfile_count=..' 键值对片段——sha 是 40 位十六进制，
        碰撞概率可忽略)——防止 changeset 登记锚点被后续改写(如变基/修正提交)后，把针对
        旧 commit 的判词错当成针对当前登记状态的判词引用；②该 run 含 node_id='n14' 的
        记录(p5 的决策节点，标志这轮走完了机械核验，不是被 n1 拒绝或被 n1b 短路的半截轮
        次)。两条件都满足的轮次里取时间最新的一轮(ts DESC)；逐点(p1..p5)在该 run_id 内
        取该点候选节点里最后写入(ts 最大)的 status 作为判决值——与 ChangesetAuditRunner
        运行时 self.verdicts 的写入顺序语义一致(如 p2 先由 n6 写 DEFER，若触发 n9b 则被
        其改写为 PASS/FAIL，取最新即取到 n9b 的终值；p2 若从未触发 n9b(n6 直接 MATCH)则
        只有 n6 一条记录，取到的就是它)。查无 -> 返回 None(调用方按 B-2 ALERT 处理，
        不静默跳过)。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT t1.run_id, t14.ts FROM trace_runs t1 "
            "JOIN trace_runs t14 ON t14.run_id = t1.run_id AND t14.node_id = 'n14' "
            "WHERE t1.changeset_no = ? AND t1.node_id = 'n1' AND t1.output_digest LIKE ? "
            "ORDER BY t14.ts DESC LIMIT 1",
            (changeset_no, f"%commit_hash={sha}%"),
        )
        row = cur.fetchone()
        if row is None:
            return None
        run_id, ts = row
        point_nodes = {
            "p1": ["n2"],
            "p2": ["n6", "n9b"],
            "p3": ["n9", "n9b", "n9_llm"],
            "p4": ["n11"],
            "p5": ["n14", "n14_llm"],
        }
        verdicts = {}
        for point, candidates in point_nodes.items():
            placeholders = ",".join("?" for _ in candidates)
            cur.execute(
                f"SELECT status FROM trace_runs WHERE run_id=? AND node_id IN ({placeholders}) "
                f"ORDER BY ts DESC LIMIT 1",
                (run_id, *candidates),
            )
            r = cur.fetchone()
            verdicts[point] = r[0] if r else None
        vals = list(verdicts.values())
        if any(v == "FAIL" for v in vals):
            overall = "FAIL"
        elif any(v is None or v in ("NEEDS_HUMAN", "DEFER") for v in vals):
            overall = "NEEDS_HUMAN"
        else:
            overall = "PASS"
        return run_id, ts, verdicts, overall

    def do_n2(self):
        node = self.node("n2")
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        if sha is None:
            self._branch_fallback("n2", node, "", "BLOCKED: n1.commit_hash unavailable", t0, "p1")
            return
        cmd = render(node["cmd_template"], {"sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n2", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "p1")
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._branch_fallback("n2", node, cmd, out + err, t0, "p1")
            return
        token = out.strip()
        dur = int((time.time() - t0) * 1000)
        if token == "REGEX_OK":
            self.verdicts["p1"] = "PASS"
            self.finish_node("n2", out, token, {}, None, "PASS", "PASS", cmd, dur)
        elif token == "REGEX_FAIL":
            self.verdicts["p1"] = "FAIL"
            self.finish_node("n2", out, token, {}, None, "FAIL", "FAIL", cmd, dur)
        else:
            self._branch_fallback("n2", node, cmd, out, t0, "p1")

    def do_n3(self):
        node = self.node("n3")
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        if sha is None:
            self._blocked_fallback("n3", node, "n1.commit_hash unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n3", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n3", node, cmd, out + err, t0)
            return
        digest = out.strip()
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n3", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n3", out, digest, {}, None, "OK", None, cmd, dur)

    def do_n4(self):
        node = self.node("n4")
        t0 = time.time()
        cid = self.resolve("n1.output.id")
        if cid is None:
            self._blocked_fallback("n4", node, "n1.id unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"changeset_id": cid})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n4", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n4", node, cmd, out + err, t0)
            return
        num = out.strip().split()[0] if out.strip() else ""
        fields = {"count": num}
        fields = self.apply_injection("n4", fields)
        digest = f"count={fields['count']}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n4", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n4", out, digest, fields, None, "OK", None, cmd, dur)

    def do_n5(self):
        node = self.node("n5")
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        if sha is None:
            self._blocked_fallback("n5", node, "n1.commit_hash unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n5", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n5", node, cmd, out + err, t0)
            return
        num = out.strip().split()[0] if out.strip() else ""
        fields = {"count": num}
        fields = self.apply_injection("n5", fields)
        digest = f"count={fields['count']}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n5", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n5", out, digest, fields, None, "OK", None, cmd, dur)

    def do_n6(self):
        node = self.node("n6")
        t0 = time.time()
        a = self.resolve("n1.output.file_count")
        b = self.resolve("n4.output.count")
        c = self.resolve("n5.output.count")
        if a is None or b is None or c is None:
            self._branch_fallback("n6", node, "", f"BLOCKED: missing inputs a={a} b={b} c={c}", t0, "p2")
            return
        cmd = render(node["cmd_template"], {
            "db_changeset_file_count": a, "db_change_file_count": b, "git_count": c,
        })
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n6", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "p2")
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._branch_fallback("n6", node, cmd, out + err, t0, "p2")
            return
        token = out.strip()
        dur = int((time.time() - t0) * 1000)
        if token == "MATCH":
            digest = f"PASS (db_changeset_file_count={a} db_change_file_count={b} git_count={c})"
            self.verdicts["p2"] = "PASS"
            self.finish_node("n6", out, digest, {}, None, "PASS", "PASS", cmd, dur)
        elif token == "MISMATCH":
            # v4: 三方计数不等不再直判 FAIL，挂起 DEFER，交 n9b 做多提交分支基线搜索终裁
            digest = f"DEFER (db_changeset_file_count={a} db_change_file_count={b} git_count={c})"
            self.verdicts["p2"] = "DEFER"
            self.finish_node("n6", out, digest, {}, None, "DEFER", "DEFER", cmd, dur)
        else:
            self._branch_fallback("n6", node, cmd, out, t0, "p2")

    def do_n7(self):
        node = self.node("n7")
        t0 = time.time()
        cid = self.resolve("n1.output.id")
        path = os.path.join(self.workdir, f"n7_{self.input['changeset_no']}.txt")
        if cid is None:
            if os.path.exists(path):
                os.remove(path)
            self._blocked_fallback("n7", node, "n1.id unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"changeset_id": cid})
        ok, reason = check_readonly(cmd)
        if not ok:
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n7", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n7", node, cmd, out + err, t0)
            return
        lines = sorted(l for l in out.split("\n") if l.strip() != "")
        # 注入点：n7.output.add_fake=<路径> -> 材料化清单多一行假文件（模拟 DB 明细含 git 中不存在的文件）
        inj = self.injections.get("n7", {})
        if inj.get("add_fake"):
            lines = sorted(lines + [inj["add_fake"]])
        preview = "; ".join(lines[:5])
        digest = f"{len(lines)} rows sorted; {preview}"
        if not contract_ok(node["output_contract"], digest):
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n7", node, cmd, out, t0)
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n7", out, digest, {"count": str(len(lines))}, lines, "OK", None, cmd, dur)

    def do_n8(self):
        node = self.node("n8")
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        path = os.path.join(self.workdir, f"n8_{self.input['changeset_no']}.txt")
        if sha is None:
            if os.path.exists(path):
                os.remove(path)
            self._blocked_fallback("n8", node, "n1.commit_hash unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n8", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n8", node, cmd, out + err, t0)
            return
        lines = sorted(l for l in out.split("\n") if l.strip() != "")
        # 特殊注入：n8.output.drop_first=1 -> 材料化前删清单首行（模拟数据漂移）
        inj = self.injections.get("n8", {})
        if inj.get("drop_first") == "1" and lines:
            lines = lines[1:]
        preview = "; ".join(lines[:5])
        digest = f"{len(lines)} rows sorted; {preview}"
        if not contract_ok(node["output_contract"], digest):
            if os.path.exists(path):
                os.remove(path)
            self._tool_fallback("n8", node, cmd, out, t0)
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n8", out, digest, {"count": str(len(lines))}, lines, "OK", None, cmd, dur)

    def do_n9(self):
        node = self.node("n9")
        t0 = time.time()
        changeset_no = self.input["changeset_no"]
        cmd = render(node["cmd_template"], {"workdir": self.workdir, "changeset_no": changeset_no})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n9", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "p3")
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            # 命令报错(如文件不存在) -> 通用 fallback，不走 n9_llm
            self._branch_fallback("n9", node, cmd, out + err, t0, "p3")
            return
        line_count = int(out.strip().split()[0]) if out.strip() else 0
        p2_defer = self.verdicts.get("p2") == "DEFER"
        if line_count == 0 and not p2_defer:
            # 原快路径不变：good_signal 且 p2 非 DEFER 直接 PASS(单提交特性分支单零变化)
            digest = "diff_line_count=0;identical file lists"
            dur = int((time.time() - t0) * 1000)
            self.verdicts["p3"] = "PASS"
            self.finish_node("n9", out, digest, {"line_count": "0"}, None, "PASS", "PASS", cmd, dur)
            return
        # bad_signal 或 p2==DEFER：先取详情记入 n9 自身证据，再转交 n9b 做多提交基线搜索
        n7path = os.path.join(self.workdir, f"n7_{changeset_no}.txt")
        n8path = os.path.join(self.workdir, f"n8_{changeset_no}.txt")
        detail_cmd = f"diff {n7path} {n8path}"
        detail = ""
        ok2, _ = check_readonly(detail_cmd)
        if ok2:
            _, out2, _ = self.run_shell(detail_cmd)
            detail = out2.strip()
        digest = f"diff_line_count={line_count};{detail[:300]}"
        self.finish_node("n9", out, digest, {"line_count": str(line_count)}, None,
                          "ROUTED_TO_N9B", None, cmd, int((time.time() - t0) * 1000))
        found = self.do_n9b()
        if found:
            return
        # n9b 全部候选基线都不中：p3 最终兜底到 n9_llm 语义判断（p2 已由 n9b 内部改判）
        llm_node = self.node("n9_llm")
        context_raw = f"diff_line_count={line_count}\n{detail}"
        t1 = time.time()
        fb = self.call_fallback(llm_node, context_raw, node["output_contract"])
        verdict = self.translate(fb)
        self.verdicts["p3"] = verdict
        llm_digest = f"[LLM] pass={fb.get('pass')} reason={fb.get('reason')}"
        dur = int((time.time() - t1) * 1000)
        self.finish_node("n9_llm", context_raw, llm_digest, {}, None, verdict, verdict, None, dur)

    # [v5.1] merge-base 的参照分支：本仓库多提交特性分支的标准并线目标。硬编码是有意的——
    # 这是 n9b 的第②级候选，不是"猜一个分支"，是舰队裁定的多提交分支标准基线坐标。
    MERGE_BASE_REF_BRANCH = "feat/<项目>-integration"

    def do_n9b(self) -> bool:
        """[v5.1 重写] 多提交特性分支基线搜索(exec:logic)。触发：n9 bad_signal 或 p2==DEFER。
        三级候选基线，按序试，每级都要求「该基线到锚点(sha)的文件集合与 DB 明细集合逐字相等」：
          ①sha^1（单提交快路径，保持现有行为）
          ②merge-base(feat/<项目>-integration, sha)（多提交分支标准基线，本次新增：
             CS-<日期>-0052 实证——该分支含 merge commit，第一父链从 sha 起怎么搜都到不了
             这个 merge-base，50 层内必然落空，误判 FAIL；而 merge-base 本身与 DB 明细
             115 个文件逐字相等，是唯一能复现登记明细的基线）
          ③沿第一父链 sha~N（N=1..50）兜底搜索（保持现有）
        每级候选解出基线 sha 后都做 base==head 断言：与锚点相同则跳过该候选，不当作命中
        （共享对象库里 merge-base 会退化成 HEAD，这坑已记档——<日期> CS-<日期>-0032）。
        命中 -> p2=PASS、p3=PASS，证据写明 resolved_baseline 与来源 via(^1/merge-base/~N)。
        三级全不中 -> p2 若原为 DEFER 则改判 FAIL(若已是 PASS 则维持不变)，p3 留给调用方
        转交 n9_llm；证据附与 merge-base 候选(若曾解出)两侧文件集合的差集，供人工排查。
        返回 True 表示命中(p2/p3 均已判 PASS)，False 表示未命中。
        """
        node = self.node("n9b")
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        db_files = self.resolve("n7.output")
        p2_was_defer = self.verdicts.get("p2") == "DEFER"
        if sha is None or db_files is None:
            dur = int((time.time() - t0) * 1000)
            digest = f"NOT_FOUND: missing sha or n7 file list (sha={sha is not None}, db_files={db_files is not None})"
            if p2_was_defer:
                self.verdicts["p2"] = "FAIL"
            self.finish_node("n9b", "", digest, {}, None, "FAIL", "FAIL", None, dur)
            return False
        db_set = set(db_files)
        inj8 = self.injections.get("n8", {})

        def resolve_ref(cmd: str):
            """跑一条只读 git 命令拿裸 sha；守卫拦下/命令报错都返回 None(候选不可用，不裸抛)。"""
            ok, _reason = check_readonly(cmd)
            if not ok:
                return None
            rc, out, _err = self.run_shell(cmd)
            if rc != 0:
                return None
            resolved = out.strip()
            return resolved or None

        def diff_lines(baseline_sha: str):
            """取 baseline_sha..sha 的改动文件集合(已排序、已套用 n8 的 drop_first 注入语义，
            与旧版 ladder 对同一注入点的处理保持一致)；守卫拦下/命令报错返回 None。"""
            diff_cmd = f"git -C ${FLEET_INTEGRATION_REPO} -c core.quotepath=false diff --name-only {baseline_sha} {sha}"
            ok2, _reason2 = check_readonly(diff_cmd)
            if not ok2:
                return None
            rc2, out2, _err2 = self.run_shell(diff_cmd)
            if rc2 != 0:
                return None
            lines = sorted(l for l in out2.split("\n") if l.strip() != "")
            if inj8.get("drop_first") == "1" and lines:
                lines = lines[1:]
            return lines

        found_via = None
        found_baseline = None
        found_lines = None
        mb_baseline = None   # merge-base 候选解出的基线(若曾解出)，FAIL 时供两侧差集证据用
        mb_lines = None
        searched_upto = 0    # 第三级 ~N 实测搜到的深度，用于 NOT_FOUND 摘要

        # ① sha^1：单提交快路径
        baseline1 = resolve_ref(f"git -C ${FLEET_INTEGRATION_REPO} rev-parse {sha}^1")
        if baseline1 is not None and baseline1 != sha:
            lines1 = diff_lines(baseline1)
            if lines1 is not None and set(lines1) == db_set:
                found_via, found_baseline, found_lines = "^1", baseline1, lines1

        # ② merge-base(feat/<项目>-integration, sha)：多提交分支标准基线
        if found_via is None:
            mb_baseline = resolve_ref(
                f"git -C ${FLEET_INTEGRATION_REPO} merge-base {self.MERGE_BASE_REF_BRANCH} {sha}"
            )
            if mb_baseline is not None and mb_baseline != sha:
                mb_lines = diff_lines(mb_baseline)
                if mb_lines is not None and set(mb_lines) == db_set:
                    found_via, found_baseline, found_lines = "merge-base", mb_baseline, mb_lines
            else:
                mb_baseline = None  # 无法解析/退化成 HEAD，不作为可比对候选留证

        # ③ 沿第一父链 sha~N (N=1..50) 兜底搜索
        if found_via is None:
            for n in range(1, 51):
                baseline_n = resolve_ref(f"git -C ${FLEET_INTEGRATION_REPO} rev-parse {sha}~{n}")
                if baseline_n is None:
                    break  # 越过仓库起点，停搜
                searched_upto = n
                if baseline_n == sha:
                    continue  # 退化候选，防御性保留(理论上不会发生在纯第一父链上)
                lines_n = diff_lines(baseline_n)
                if lines_n is None:
                    continue
                if set(lines_n) == db_set:
                    found_via, found_baseline, found_lines = f"~{n}", baseline_n, lines_n
                    break

        dur = int((time.time() - t0) * 1000)
        if found_via is not None:
            digest = f"BASELINE_FOUND resolved_baseline={found_baseline} via={found_via} git_count={len(found_lines)}"
            self.verdicts["p2"] = "PASS"
            self.verdicts["p3"] = "PASS"
            self.finish_node(
                "n9b", "", digest,
                {"resolved_baseline": found_baseline, "via": found_via, "git_count": str(len(found_lines))},
                None, "PASS", "PASS", None, dur,
            )
            return True

        diff_note = ""
        if mb_lines is not None:
            only_db = sorted(db_set - set(mb_lines))
            only_git = sorted(set(mb_lines) - db_set)
            diff_note = (
                f" vs_merge_base={mb_baseline} only_in_db={len(only_db)}({','.join(only_db[:10])}) "
                f"only_in_git={len(only_git)}({','.join(only_git[:10])})"
            )
        digest = f"NOT_FOUND tried=^1,merge-base,~1..{searched_upto}{diff_note}"
        if p2_was_defer:
            self.verdicts["p2"] = "FAIL"
        self.finish_node("n9b", "", digest, {}, None, "FAIL", "FAIL", None, dur)
        return False

    def do_n10(self):
        node = self.node("n10")
        t0 = time.time()
        changeset_no = self.input["changeset_no"]
        cmd = render(node["cmd_template"], {"changeset_no": changeset_no})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n10", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n10", node, cmd, out + err, t0)
            return
        lines = [l for l in out.split("\n") if l != ""]
        if len(lines) < 2:
            self._tool_fallback("n10", node, cmd, out, t0)
            return
        row = lines[1].split("\t")
        fields = {"title": row[0], "hex_left3": row[1] if len(row) > 1 else ""}
        fields = self.apply_injection("n10", fields)
        digest = f"title={fields['title']}\thex_left3={fields['hex_left3']}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n10", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n10", out, digest, fields, None, "OK", None, cmd, dur)

    def do_n11(self):
        node = self.node("n11")
        t0 = time.time()
        title = self.resolve("n10.output.title")
        if title is None:
            self._branch_fallback("n11", node, "", "BLOCKED: n10.title unavailable", t0, "p4")
            return
        cmd = render(node["cmd_template"], {"title": title})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n11", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "p4")
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._branch_fallback("n11", node, cmd, out + err, t0, "p4")
            return
        token = out.strip()
        dur = int((time.time() - t0) * 1000)
        if token == "CLEAN":
            self.verdicts["p4"] = "PASS"
            self.finish_node("n11", out, token, {}, None, "PASS", "PASS", cmd, dur)
        elif token == "GARBLED":
            self.verdicts["p4"] = "FAIL"
            self.finish_node("n11", out, token, {}, None, "FAIL", "FAIL", cmd, dur)
        else:
            self._branch_fallback("n11", node, cmd, out, t0, "p4")

    def do_n12(self):
        node = self.node("n12")
        t0 = time.time()
        cid = self.resolve("n1.output.id")
        if cid is None:
            self._blocked_fallback("n12", node, "n1.id unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"changeset_id": cid})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n12", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n12", node, cmd, out + err, t0)
            return
        lines = [l for l in out.split("\n") if l.strip() != ""]
        preview = "; ".join(lines[:5])
        digest = f"{len(lines)} non-delete files: {preview}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n12", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n12", out, digest, {"count": str(len(lines))}, lines, "OK", None, cmd, dur)

    def do_n13(self):
        """[轨迹约定10] 按 n13 自身 cmd_template 的形状分派：图里若已换成调用
        lineending_check.py(v5+) 走新实现；仍是旧的逐文件 for 循环模板(v4 及更早，
        生产轮询器目前还在用)走旧实现——旧图必须原样跑通、判决与升级前一致。"""
        node = self.node("n13")
        if uses_new_lineending_check(node):
            self._do_n13_new(node)
        else:
            self._do_n13_legacy(node)

    def _do_n13_new(self, node):
        """[v5] 行尾判据不再是"每文件绝对 CR 计数"，改为调用两图共用的
        runner/checks/lineending_check.py，一次性拿到该 commit 相对父提交({sha}^1)
        的全部改动文件的 before/after/added_crlf/deleted_crlf/verdict。"""
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        if sha is None:
            self._blocked_fallback("n13", node, "n1.commit_hash unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n13", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd, timeout=60)
        if rc != 0:
            self._tool_fallback("n13", node, cmd, out + err, t0)
            return
        records = parse_lineending_output(out)
        checked = [r for r in records if r["verdict"] != "SKIP"]
        fail = [r for r in checked if r["verdict"] == "FAIL"]
        ambiguous = [r for r in checked if r["verdict"] == "AMBIGUOUS"]
        digest = (
            f"{len(checked)} files checked; "
            f"fail={len(fail)}({','.join(r['path'] for r in fail) or 'none'}); "
            f"ambiguous={len(ambiguous)}({','.join(r['path'] for r in ambiguous) or 'none'})"
        )
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n13", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n13", out, digest, {"checked": str(len(checked))}, records, "OK", None, cmd, dur)

    def _do_n13_legacy(self, node):
        """[v4 及更早，逐字保留升级前实现] 每文件绝对 CR 计数：对 n12(DB非删除文件清单)
        逐个文件在 {sha} 下取内容用 grep -c 统计 CR 行数。生产轮询器仍在用这套(v4图)，
        必须原样可跑、判决不变。"""
        t0 = time.time()
        sha = self.resolve("n1.output.commit_hash")
        files = self.resolve("n12.output")
        if sha is None or files is None:
            self._blocked_fallback("n13", node, f"missing sha or file list (sha={sha is not None},"
                                                  f" files={files is not None})", t0)
            return
        if len(files) == 0:
            digest = "0 files checked; files_with_cr>0:none"
            dur = int((time.time() - t0) * 1000)
            self.finish_node("n13", "", digest, {"total": "0"}, [], "OK", None, None, dur)
            return
        quoted = " ".join(shlex.quote(f) for f in files)
        for_body = node["cmd_template"].replace("<non-delete files>", quoted)
        for_body = render(for_body, {"sha": sha})
        ok, reason = check_readonly(for_body)
        if not ok:
            self._tool_fallback("n13", node, for_body, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(for_body, timeout=60)
        if rc != 0:
            self._tool_fallback("n13", node, for_body, out + err, t0)
            return
        count_lines = [l.strip() for l in out.split("\n") if l.strip() != ""]
        counts = []
        for i, f in enumerate(files):
            c = count_lines[i] if i < len(count_lines) else "0"
            try:
                cval = int(c)
            except ValueError:
                cval = 0
            counts.append((f, cval))
        bad = [f for f, c in counts if c > 0]
        digest = f"{len(files)} files checked; files_with_cr>0:{','.join(bad) if bad else 'none'}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n13", node, for_body, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n13", out, digest, {"total": str(len(files))}, counts, "OK", None, for_body, dur)

    def do_n14(self):
        """[轨迹约定10] 与 do_n13 用同一个特征(n13 的 cmd_template 形状)分派聚合逻辑，
        保证旧图(v4)的 n13(元组列表)与新图(v5+)的 n13(字典列表)走各自匹配的聚合实现。"""
        n13_node = self.node("n13")
        if uses_new_lineending_check(n13_node):
            self._do_n14_new()
        else:
            self._do_n14_legacy()

    def _p5_registered_details(self):
        """只读 SQL 侧行尾指标；已并线锚也不依赖 Git 工作树。"""
        changeset_id = self.resolve("n1.output.id")
        if changeset_id is None:
            return None, "changeset_id_unavailable"
        cmd = (
            "/usr/local/bin/docker exec ${DB_CONTAINER} sh -c "
            "'mysql --max-allowed-packet=64M -uroot -p\"$MYSQL_ROOT_PASSWORD\" --default-character-set=utf8mb4 "
            "${DB_NAME} -N -e \"SELECT file_path,change_action,"
            "before_content IS NULL,after_content IS NULL,"
            "LENGTH(before_content)-LENGTH(REPLACE(before_content,CHAR(13),SUBSTRING(CHAR(0),1,0))),"
            "LENGTH(before_content)-LENGTH(REPLACE(before_content,CHAR(10),SUBSTRING(CHAR(0),1,0))),"
            "RIGHT(before_content,1)=CHAR(10),LENGTH(before_content),"
            "LENGTH(after_content)-LENGTH(REPLACE(after_content,CHAR(13),SUBSTRING(CHAR(0),1,0))),"
            "LENGTH(after_content)-LENGTH(REPLACE(after_content,CHAR(10),SUBSTRING(CHAR(0),1,0))),"
            "RIGHT(after_content,1)=CHAR(10),LENGTH(after_content) "
            f"FROM t_code_change_file WHERE changeset_id={changeset_id} AND is_del=0 ORDER BY id\"'"
        )
        ok, reason = check_readonly(cmd)
        if not ok:
            return None, "GUARD_BLOCKED:" + reason
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            filtered_err = "\n".join(
                line for line in err.splitlines()
                if "Using a password on the command line" not in line
            ).strip()
            return None, f"SQL_ERR rc={rc} err={(filtered_err or '(no non-warning stderr)')[:160]}"

        def metrics(null_flag, values, path, side):
            if null_flag == "1":
                return None, None
            if null_flag != "0":
                return None, f"NULL_FLAG:{side}:{path}"
            try:
                cr, lf, ends_lf, length = (int(value) for value in values)
            except ValueError:
                return None, f"METRIC_PARSE:{side}:{path}"
            return {"cr": cr, "lf": lf, "ends_lf": ends_lf, "length": length}, None

        details = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 12:
                return None, "ROW_SHAPE:" + line[:160]
            path, action, before_null, after_null = parts[:4]
            before, before_error = metrics(before_null, parts[4:8], path, "before")
            after, after_error = metrics(after_null, parts[8:12], path, "after")
            if before_error:
                return None, before_error
            if after_error:
                return None, after_error
            details.append({"path": path, "action": action, "before": before, "after": after})
        return details, None

    @staticmethod
    def _p5_metrics(content):
        if isinstance(content, (bytes, bytearray)):
            return {"cr": content.count(b"\r"), "lines": len(content.splitlines())}
        if not isinstance(content, dict):
            raise TypeError(f"p5 metrics must be bytes or dict, got {type(content).__name__}")
        cr = int(content["cr"])
        lf = int(content["lf"])
        ends_lf = int(content["ends_lf"])
        length = int(content["length"])
        return {"cr": cr, "lines": lf + (1 if length > 0 and ends_lf != 1 else 0)}

    def _do_n14_new(self):
        """判决点 p5 的纯机械差值规则（P5RULE）。

        只读 ``t_code_change_file`` 的登记 before/after 正文，故已并线单也可复验：
        ADD 一律 LF；MODIFY/RENAME 的 before 若全行 CRLF，则 CR 差必须等于行数差；
        若全行 LF，两侧 CR 必须为 0；其余混写 NEEDS_HUMAN。任何可计算的不符均为
        FAIL 并点名文件与数字；不调用 n14_llm，也不读取 Git 工作树。
        """
        node = self.node("n14")
        t0 = time.time()
        details, error = self._p5_registered_details()
        if details is None:
            digest = "point=p5 verdict=NEEDS_HUMAN registered_detail_unavailable:" + error
            self.verdicts["p5"] = "NEEDS_HUMAN"
            self.finish_node("n14", "", digest, {}, None, "NEEDS_HUMAN", "NEEDS_HUMAN", None,
                             int((time.time() - t0) * 1000))
            return

        outcomes = []
        failures = []
        needs_human = []
        for detail in details:
            path = detail["path"]
            action = detail["action"]
            before = detail["before"]
            after = detail["after"]
            outcome = {"path": path, "action": action}
            if action == "DELETE":
                outcome.update({"rule": "DELETE_SKIP", "verdict": "SKIP"})
                outcomes.append(outcome)
                continue
            if after is None:
                outcome.update({"rule": "AFTER_CONTENT_MISSING", "verdict": "NEEDS_HUMAN"})
                outcomes.append(outcome)
                needs_human.append(outcome)
                continue
            after_metrics = self._p5_metrics(after)
            outcome.update({"after_cr": after_metrics["cr"], "after_lines": after_metrics["lines"]})
            if action == "ADD":
                outcome.update({"rule": "ADD_LF", "verdict": "PASS" if after_metrics["cr"] == 0 else "FAIL"})
                outcomes.append(outcome)
                if outcome["verdict"] == "FAIL":
                    failures.append(outcome)
                continue
            if action not in ("MODIFY", "RENAME") or before is None:
                outcome.update({"rule": "BEFORE_CONTENT_MISSING_OR_ACTION_UNKNOWN", "verdict": "NEEDS_HUMAN"})
                outcomes.append(outcome)
                needs_human.append(outcome)
                continue
            before_metrics = self._p5_metrics(before)
            outcome.update({"before_cr": before_metrics["cr"], "before_lines": before_metrics["lines"],
                            "cr_delta": after_metrics["cr"] - before_metrics["cr"],
                            "line_delta": after_metrics["lines"] - before_metrics["lines"]})
            if before_metrics["cr"] == 0:
                outcome.update({"rule": "LF", "verdict": "PASS" if after_metrics["cr"] == 0 else "FAIL"})
            elif before_metrics["cr"] == before_metrics["lines"]:
                outcome.update({"rule": "CRLF_DELTA", "verdict": "PASS" if (
                    outcome["cr_delta"] == outcome["line_delta"]
                    and after_metrics["cr"] == after_metrics["lines"]
                ) else "FAIL"})
            else:
                outcome.update({"rule": "MIXED", "verdict": "NEEDS_HUMAN"})
            outcomes.append(outcome)
            if outcome["verdict"] == "FAIL":
                failures.append(outcome)
            elif outcome["verdict"] == "NEEDS_HUMAN":
                needs_human.append(outcome)

        def describe(item):
            return (
                f"{item['path']}[{item['rule']};action={item.get('action')};before_cr={item.get('before_cr')};"
                f"after_cr={item.get('after_cr')};before_lines={item.get('before_lines')};"
                f"after_lines={item.get('after_lines')};cr_delta={item.get('cr_delta')};"
                f"line_delta={item.get('line_delta')}]"
            )

        dur = int((time.time() - t0) * 1000)
        raw_outcomes = json.dumps(outcomes, ensure_ascii=False, sort_keys=True)
        if failures:
            digest = "point=p5 verdict=FAIL " + "; ".join(describe(item) for item in failures)
            self.verdicts["p5"] = "FAIL"
            self.finish_node("n14", raw_outcomes, digest, {}, outcomes, "FAIL", "FAIL", None, dur)
            return
        if needs_human:
            digest = "point=p5 verdict=NEEDS_HUMAN " + "; ".join(describe(item) for item in needs_human)
            self.verdicts["p5"] = "NEEDS_HUMAN"
            self.finish_node("n14", raw_outcomes, digest, {}, outcomes,
                             "NEEDS_HUMAN", "NEEDS_HUMAN", None, dur)
            return
        checked = [item for item in outcomes if item["verdict"] != "SKIP"]
        digest = f"point=p5 verdict=PASS {len(checked)}/{len(outcomes)} registered_content_difference_rule"
        self.verdicts["p5"] = "PASS"
        self.finish_node("n14", raw_outcomes, digest, {}, outcomes, "PASS", "PASS", None, dur)

    def _do_n14_legacy(self):
        """[v4 及更早，逐字保留升级前实现] n13 产出的是 (file, cr_count) 元组列表，
        任一 cr_count>0 -> FAIL，全 0 -> PASS。生产轮询器仍在用这套(v4图)，必须原样
        可跑、判决不变。同样做元素级形状校验(约定10)，避免非预期形状裸抛异常。"""
        node = self.node("n14")
        t0 = time.time()
        raw = self.resolve("n13.output")
        if not isinstance(raw, list):
            digest = f"RUNNER_ERR: 期望 list，实际类型={type(raw).__name__} 值摘要={str(raw)[:200]}"
            self._branch_fallback("n14", node, "", digest, t0, "p5")
            return
        counts = []
        shape_errors = []
        for item in raw:
            if (isinstance(item, tuple) or isinstance(item, list)) and len(item) == 2:
                counts.append((item[0], item[1]))
            else:
                shape_errors.append(f"RUNNER_ERR: 非法元素 类型={type(item).__name__} 值摘要={str(item)[:200]}")
        if not counts and shape_errors:
            digest = "RUNNER_ERR: n13 output malformed; " + "; ".join(shape_errors[:3])
            self._branch_fallback("n14", node, "", digest, t0, "p5")
            return
        bad = [f for f, c in counts if c > 0]
        dur = int((time.time() - t0) * 1000)
        shape_note = f" shape_errors={len(shape_errors)}" if shape_errors else ""
        if not bad:
            digest = f"PASS: 0/{len(counts)} files have CR>0{shape_note}"  # secret-scan: allow
            self.verdicts["p5"] = "PASS"
            self.finish_node("n14", "", digest, {}, None, "PASS", "PASS", None, dur)
        else:
            digest = f"FAIL: files_with_cr>0: {', '.join(bad)}{shape_note}"
            self.verdicts["p5"] = "FAIL"
            self.finish_node("n14", "", digest, {}, None, "FAIL", "FAIL", None, dur)

    # ---- 主流程 ----
    def run(self):
        self.do_n1()
        if self.results["n1"]["status"] != "OK":
            # [AUDITFIX <日期>] n1 是取数节点、不是判决点：它失败只说明「审不了」，
            # 不说明「审出问题」。此前沿用兜底给的 verdict，于是登记不合规(如
            # CS-<日期>-0008 的 9 位短哈希)被判成五探针全 FAIL——那是假 FAIL，
            # 会消耗人工兜底信用。一律降 NEEDS_HUMAN；真因完整留在 [FALLBACK:n1] digest。
            for p in ("p1", "p2", "p3", "p4", "p5"):
                self.verdicts[p] = "NEEDS_HUMAN"
            self.aborted = True
            return
        triage = self.do_n1b()
        if triage is not None:
            # 已并线单的 p1-p4 保持入口分诊原判；P5RULE 只用登记正文复核 p5，
            # 不读取工作树，也不因“已并线”放弃行尾机械验法。
            if triage in ("SKIPPED_ALREADY_MERGED", "ALERT_MERGED_WITHOUT_AUDIT"):
                for p in ("p1", "p2", "p3", "p4"):
                    self.verdicts[p] = triage
                self._do_n14_new()
                self.aborted = False
                return
            for p in ("p1", "p2", "p3", "p4", "p5"):
                self.verdicts[p] = triage
            self.aborted = False
            return
        self.do_n2()
        self.do_n3()
        self.do_n4()
        self.do_n5()
        self.do_n6()
        self.do_n7()
        self.do_n8()
        self.do_n9()
        self.do_n10()
        self.do_n11()
        self.do_n12()
        # P5RULE 新图的 p5 只读登记正文，n13 的 Git 行尾采样既不再参与判定，
        # 也不应因采样异常调模型；旧图继续走原 n13/n14，保持历史轨迹不变。
        if uses_new_lineending_check(self.node("n13")):
            self._do_n14_new()
        else:
            self.do_n13()
            self.do_n14()

    def overall(self) -> str:
        vals = [self.verdicts.get(p) for p in ("p1", "p2", "p3", "p4", "p5")]
        # [v5.1/v5.2] n1b 入口分诊命中时 p1..p5 全部置同一个分诊标签：整单直接采用该标签，
        # 不落入下面的 FAIL/NEEDS_HUMAN/PASS 三段判断（否则全同值会被 any(...) 两条都判
        # False 而误落到 PASS）。三个标签互斥(n1b 只会命中其一)，逐个判等价于按 n1b 的
        # 分诊结果直通。
        for triage_label in ("N/A", "SKIPPED_ALREADY_MERGED", "ALERT_MERGED_WITHOUT_AUDIT"):
            if all(v == triage_label for v in vals):
                return triage_label
        # P5RULE：已并线单仍单独跑 p5；p1-p4 的入口分诊结论保持原样。
        # p5 通过时 overall 沿用该入口结论；p5 红/需人工则以更严格的 p5 结果收束。
        for triage_label in ("SKIPPED_ALREADY_MERGED", "ALERT_MERGED_WITHOUT_AUDIT"):
            if all(v == triage_label for v in vals[:4]) and vals[4] == "PASS":
                return triage_label
        if any(v == "FAIL" for v in vals):
            return "FAIL"
        # DEFER 理论上总会被 n9b 终裁掉，这里是防御性兜底：万一有遗留 DEFER，不能被当成 PASS 静默放过
        if any(v is None or v == "NEEDS_HUMAN" or v == "DEFER" for v in vals):
            return "NEEDS_HUMAN"
        return "PASS"


# --------------------------------------------------------------------------
# PremergeGateRunner：premerge-gate.v2 实现（g1 脏树 / g2 git中态 / g3 冲突标记 / g4 行尾）
# --------------------------------------------------------------------------

MARKER_FILES = ["CHERRY_PICK_HEAD", "MERGE_HEAD", "REBASE_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"]
CONFLICT_CODES = {"UU", "AA", "DD", "AU", "UA", "UD", "DU"}


class PremergeGateRunner(BaseRunner):
    def do_n1(self):
        node = self.node("n1")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n1", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n1", node, cmd, out + err, t0)
            return
        digest = out.strip()
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n1", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n1", out, digest, {"branch": digest}, None, "OK", None, cmd, dur)

    def do_n2(self):
        node = self.node("n2")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n2", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n2", node, cmd, out + err, t0)
            return
        sha = out.strip()
        digest = f"head={sha}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n2", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n2", out, digest, {"head": sha}, None, "OK", None, cmd, dur)

    def do_n3(self):
        """判决点 g1（脏树）：解析 git status --porcelain 输出统计三类行数。"""
        node = self.node("n3")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n3", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "g1")
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._branch_fallback("n3", node, cmd, out + err, t0, "g1")
            return
        tracked_dirty = untracked = conflict = 0
        for line in out.split("\n"):
            if len(line) < 2:
                continue
            code = line[0:2]
            if code in CONFLICT_CODES:
                conflict += 1
            elif code == "??":
                untracked += 1
            else:
                tracked_dirty += 1
        digest = f"tracked_dirty={tracked_dirty} untracked={untracked} conflict={conflict}"
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n3", node, cmd, out, t0, "g1")
            return
        dur = int((time.time() - t0) * 1000)
        if conflict > 0 or tracked_dirty > 0:
            self.verdicts["g1"] = "FAIL"
            self.finish_node("n3", out, digest, {}, None, "FAIL", "FAIL", cmd, dur)
        else:
            self.verdicts["g1"] = "PASS"
            self.finish_node("n3", out, digest, {}, None, "PASS", "PASS", cmd, dur)

    def do_n4(self):
        node = self.node("n4")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n4", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n4", node, cmd, out + err, t0)
            return
        digest = out.strip()
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n4", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n4", out, digest, {"git_dir": digest}, None, "OK", None, cmd, dur)

    def do_n5(self):
        """判决点 g2（git 中态）。[运行器实现修正] 图 JSON 里的 cmd_template 把标记文件路径
        硬编码成 ${FLEET_INTEGRATION_REPO}/.git/worktrees/{worktree}/<marker>，只对 <基座项目>-erp
        的真实 linked worktree 成立；本实现改用 n4 实测的 git-dir（对普通仓库/任意 worktree
        都成立：普通仓库 git-dir 就是 <repo>/.git，linked worktree 则是
        <主仓>/.git/worktrees/<name>）逐个 marker 单独跑 `test -e`，与 g2 判据本意
        （检查 CHERRY_PICK_HEAD/MERGE_HEAD/REBASE_HEAD/BISECT_LOG/rebase-merge/rebase-apply
        任一标记文件是否存在于 git-dir 下）一致，只是让实现对任意仓库路径都成立。"""
        node = self.node("n5")
        t0 = time.time()
        git_dir = self.resolve("n4.output.git_dir")
        worktree = self.input.get("worktree")
        if git_dir is None:
            self._branch_fallback("n5", node, "", "BLOCKED: n4.git_dir unavailable", t0, "g2")
            return
        if not os.path.isabs(git_dir):
            git_dir = os.path.normpath(os.path.join(worktree or "", git_dir))
        found = []
        cmds_run = []
        for marker in MARKER_FILES:
            marker_path = os.path.join(git_dir, marker)
            cmd = f"test -e {shlex.quote(marker_path)}"
            cmds_run.append(cmd)
            ok, reason = check_readonly(cmd)
            if not ok:
                self._branch_fallback("n5", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "g2")
                return
            rc, out, err = self.run_shell(cmd)
            if rc == 0:
                found.append(marker)
        digest = f"markers_found={','.join(found) if found else 'none'}"
        dur = int((time.time() - t0) * 1000)
        cmd_joined = "; ".join(cmds_run)
        if found:
            self.verdicts["g2"] = "FAIL"
            self.finish_node("n5", "", digest, {}, None, "FAIL", "FAIL", cmd_joined, dur)
        else:
            self.verdicts["g2"] = "PASS"
            self.finish_node("n5", "", digest, {}, None, "PASS", "PASS", cmd_joined, dur)

    def do_n6(self):
        """判决点 g3（冲突标记）：git grep -l 命中冲突标记的文件数(管道 head -20 截断，
        实际统计以截断后可见的行数为准，与图定义一致)。"""
        node = self.node("n6")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n6", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "g3")
            return
        rc, out, err = self.run_shell(cmd)
        if rc not in (0, 1):
            self._branch_fallback("n6", node, cmd, out + err, t0, "g3")
            return
        lines = [l for l in out.split("\n") if l.strip()]
        count = len(lines)
        digest = f"conflict_files={count}"
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n6", node, cmd, out, t0, "g3")
            return
        dur = int((time.time() - t0) * 1000)
        if count > 0:
            self.verdicts["g3"] = "FAIL"
            self.finish_node("n6", out, digest, {}, None, "FAIL", "FAIL", cmd, dur)
        else:
            self.verdicts["g3"] = "PASS"
            self.finish_node("n6", out, digest, {}, None, "PASS", "PASS", cmd, dur)

    def do_n7(self):
        node = self.node("n7")
        t0 = time.time()
        worktree = self.input.get("worktree")
        cmd = render(node["cmd_template"], {"worktree": worktree})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n7", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n7", node, cmd, out + err, t0)
            return
        sha = out.strip()
        digest = f"merge_base={sha}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n7", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n7", out, digest, {"merge_base": sha}, None, "OK", None, cmd, dur)

    def do_n8(self):
        node = self.node("n8")
        t0 = time.time()
        worktree = self.input.get("worktree")
        sha = self.resolve("n7.output.merge_base")
        if sha is None:
            self._blocked_fallback("n8", node, "n7.merge_base unavailable", t0)
            return
        cmd = render(node["cmd_template"], {"worktree": worktree, "sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback("n8", node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback("n8", node, cmd, out + err, t0)
            return
        lines = [l for l in out.split("\n") if l.strip() != ""]
        digest = f"changed_files={len(lines)}"
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback("n8", node, cmd, out, t0)
            return
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n8", out, digest, {"count": str(len(lines))}, lines, "OK", None, cmd, dur)

    def do_n9(self):
        """[轨迹约定10] 按 n9 自身 cmd_template 的形状分派：图里若已换成调用
        lineending_check.py(v6+) 走新实现；仍是旧的逐文件 for 循环模板(v5 及更早)走旧
        实现——旧图必须原样跑通、判决与升级前一致。"""
        node = self.node("n9")
        if uses_new_lineending_check(node):
            self._do_n9_new(node)
        else:
            self._do_n9_legacy(node)

    def _do_n9_new(self, node):
        """判决点 g4（行尾）：[v6] 调用两图共用的 runner/checks/lineending_check.py，
        一次性拿到该 worktree 相对 merge-base 的全部改动文件的行尾判据。任一文件 FAIL
        (新增文件带CR) -> g4 FAIL；无 FAIL 但有 AMBIGUOUS(存量文件行尾计数对不上公式，
        可能是破坏也可能是行尾还原的合规修复) -> 转交 n9_llm 语义裁决(context 带
        merge-base..HEAD 的提交日志，提示模型提交信息里的"行尾/CRLF/LF/还原/dos2unix"
        等字样只是弱信号，不能仅凭关键词判 PASS，仍须结合 diff 语义判断有无夹带内容
        变更)；都没有 -> PASS。[轨迹约定10] records 逐元素也做 dict 形状校验，
        非法元素记 RUNNER_ERR 落值摘要，绝不裸抛异常。"""
        t0 = time.time()
        worktree = self.input.get("worktree")
        sha = self.resolve("n7.output.merge_base")
        if sha is None:
            self._branch_fallback("n9", node, "", "BLOCKED: n7.merge_base unavailable", t0, "g4")
            return
        cmd = render(node["cmd_template"], {"worktree": worktree, "sha": sha})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n9", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "g4")
            return
        rc, out, err = self.run_shell(cmd, timeout=60)
        if rc != 0:
            self._branch_fallback("n9", node, cmd, out + err, t0, "g4")
            return
        parsed = parse_lineending_output(out)
        records, shape_errors = validate_record_list_shape(parsed)
        if not records and shape_errors:
            digest = "RUNNER_ERR: lineending_check.py output malformed; " + "; ".join(shape_errors[:3])
            self._branch_fallback("n9", node, cmd, out, t0, "g4")
            return
        checked = [r for r in records if r["verdict"] != "SKIP"]
        fail = [r for r in checked if r["verdict"] == "FAIL"]
        ambiguous = [r for r in checked if r["verdict"] == "AMBIGUOUS"]
        shape_note = f" shape_errors={len(shape_errors)}" if shape_errors else ""
        digest = (
            f"point=g4 checked={len(checked)} "
            f"fail={len(fail)}({','.join(r['path'] for r in fail) or 'none'}) "
            f"ambiguous={len(ambiguous)}({','.join(r['path'] for r in ambiguous) or 'none'}){shape_note}"
        )
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n9", node, cmd, out, t0, "g4")
            return
        dur = int((time.time() - t0) * 1000)
        if fail:
            self.verdicts["g4"] = "FAIL"
            self.finish_node("n9", out, digest, {}, records, "FAIL", "FAIL", cmd, dur)
            return
        if ambiguous:
            self.finish_node("n9", out, digest, {}, records, "ROUTED_TO_LLM", None, cmd, dur)
            log_cmd = (
                f"git --no-optional-locks -C {worktree} -c core.quotepath=false "
                f"log --format=%H%x09%s {sha}..HEAD"
            )
            commit_log = ""
            ok2, _reason2 = check_readonly(log_cmd)
            if ok2:
                rc2, log_out, _log_err = self.run_shell(log_cmd)
                if rc2 == 0:
                    commit_log = log_out.strip()[:300]
            detail_lines = [
                f"{r['path']}: before_cr={r['before_cr']} after_cr={r['after_cr']} "
                f"added_crlf={r['added_crlf']} deleted_crlf={r['deleted_crlf']}"
                for r in ambiguous
            ]
            context_raw = f"commit_log(merge_base..HEAD)={commit_log}\n" + "\n".join(detail_lines)
            self.llm_route("n9_llm", node, context_raw)
            return
        self.verdicts["g4"] = "PASS"
        self.finish_node("n9", out, digest, {}, records, "PASS", "PASS", cmd, dur)

    def _do_n9_legacy(self, node):
        """[v5 及更早，逐字保留升级前实现] map 节点，聚合结果本身即判决：对 n8(改动
        文件清单)逐个文件在 HEAD 下取内容统计 CR 行数，任一 >0 -> FAIL。"""
        t0 = time.time()
        worktree = self.input.get("worktree")
        files = self.resolve("n8.output")
        if files is None:
            self._branch_fallback("n9", node, "", "BLOCKED: n8.output unavailable", t0, "g4")
            return
        if len(files) == 0:
            digest = "0 files checked; files_with_cr>0:none"
            dur = int((time.time() - t0) * 1000)
            self.verdicts["g4"] = "PASS"
            self.finish_node("n9", "", digest, {}, [], "PASS", "PASS", None, dur)
            return
        quoted = " ".join(shlex.quote(f) for f in files)
        body = node["cmd_template"].replace("<changed_files>", quoted)
        body = render(body, {"worktree": worktree})
        ok, reason = check_readonly(body)
        if not ok:
            self._branch_fallback("n9", node, body, f"GUARD_BLOCKED:{reason}", t0, "g4")
            return
        rc, out, err = self.run_shell(body, timeout=60)
        if rc != 0:
            self._branch_fallback("n9", node, body, out + err, t0, "g4")
            return
        count_lines = [l.strip() for l in out.split("\n") if l.strip() != ""]
        counts = []
        for i, f in enumerate(files):
            c = count_lines[i] if i < len(count_lines) else "0"
            try:
                cval = int(c)
            except ValueError:
                cval = 0
            counts.append((f, cval))
        bad = [f for f, c in counts if c > 0]
        digest = f"{len(files)} files checked; files_with_cr>0:{','.join(bad) if bad else 'none'}"
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n9", node, body, out, t0, "g4")
            return
        dur = int((time.time() - t0) * 1000)
        if bad:
            self.verdicts["g4"] = "FAIL"
            self.finish_node("n9", out, digest, {}, counts, "FAIL", "FAIL", body, dur)
        else:
            self.verdicts["g4"] = "PASS"
            self.finish_node("n9", out, digest, {}, counts, "PASS", "PASS", body, dur)

    def run(self):
        self.do_n1()
        self.do_n2()
        self.do_n3()
        self.do_n4()
        self.do_n5()
        self.do_n6()
        self.do_n7()
        self.do_n8()
        self.do_n9()

    def overall(self) -> str:
        vals = [self.verdicts.get(p) for p in ("g1", "g2", "g3", "g4")]
        if any(v == "FAIL" for v in vals):
            return "FAIL"
        if any(v is None or v == "NEEDS_HUMAN" for v in vals):
            return "NEEDS_HUMAN"
        return "PASS"


# --------------------------------------------------------------------------
# ReceiptComplianceRunner：receipt-compliance.v2 实现（分型 t0 + e1/e2/r1-r6）
# --------------------------------------------------------------------------

class ReceiptComplianceRunner(BaseRunner):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.doc_type = None

    def do_classification(self):
        cls = self.graph["classification"]
        node_id = cls["decision_node"]
        node = self.node(node_id)
        t0 = time.time()
        receipt = self.input.get("receipt")
        cmd = render(node["cmd_template"], {"receipt": receipt})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._tool_fallback(node_id, node, cmd, f"GUARD_BLOCKED:{reason}", t0)
            return None
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            self._tool_fallback(node_id, node, cmd, out + err, t0)
            return None
        digest = out.strip()
        if not contract_ok(node["output_contract"], digest):
            self._tool_fallback(node_id, node, cmd, out, t0)
            return None
        key = cls.get("own_output_key", "type")
        m = re.search(rf'{re.escape(key)}=(\w+)$', digest)
        doc_type = m.group(1) if m else None
        dur = int((time.time() - t0) * 1000)
        fields = {key: doc_type} if doc_type else {}
        self.finish_node(node_id, out, digest, fields, None, "OK", None, cmd, dur)
        return doc_type

    def run_branch_node(self, node, doc_type, gates):
        node_id = node["id"]
        vp = node.get("verdict_point")
        t0 = time.time()
        # 分型闸门：不适用的判决点仍执行该步骤取证据(同构)，只是判决强制记 N/A。
        gated_off = bool(vp) and vp in gates and doc_type not in gates[vp]
        receipt = self.input.get("receipt")
        cmd = render(node["cmd_template"], {"receipt": receipt})

        def record_na(raw, reason_digest):
            dur = int((time.time() - t0) * 1000)
            self.verdicts[vp] = "N/A"
            self.finish_node(node_id, raw, reason_digest, {}, None, "N/A", "N/A", cmd, dur)

        ok, reason = check_readonly(cmd)
        if not ok:
            if gated_off:
                record_na("", f"N/A(gated;guard_blocked:{reason})")
                return
            self._branch_fallback(node_id, node, cmd, f"GUARD_BLOCKED:{reason}", t0, vp)
            return
        rc, out, err = self.run_shell(cmd)
        if rc != 0:
            if gated_off:
                record_na(out + err, "N/A(gated;tool_error)")
                return
            self._branch_fallback(node_id, node, cmd, out + err, t0, vp)
            return
        digest = out.strip()
        if not contract_ok(node["output_contract"], digest):
            if gated_off:
                record_na(out, "N/A(gated;contract_fail)")
                return
            self._branch_fallback(node_id, node, cmd, out, t0, vp)
            return
        dur = int((time.time() - t0) * 1000)
        if gated_off:
            self.verdicts[vp] = "N/A"
            self.finish_node(node_id, out, digest, {}, None, "N/A", "N/A", cmd, dur)
            return
        # 未被分型闸门排除：走机械 cases/no_match 判决(三层偏离检测的第②层：分支谓词全不命中)
        m = re.search(r'verdict=(.+)$', digest)
        token = m.group(1) if m else None
        cases = node.get("cases", {})
        if token is not None and token in cases:
            final = cases[token]
            if vp:
                self.verdicts[vp] = final
            self.finish_node(node_id, out, digest, {}, None, final, final, cmd, dur)
            return
        no_match = node.get("no_match")
        if no_match and no_match != "fallback" and no_match in self.nodes_by_id \
                and self.nodes_by_id[no_match].get("type") == "llm":
            # 机械判据判不了(如 AMBIGUOUS)，先记本节点自身证据，再转交具名 llm 节点兜底
            self.finish_node(node_id, out, digest, {}, None, "ROUTED_TO_LLM", None, cmd, dur)
            self.llm_route(no_match, node, digest)
            return
        # 通用兜底(无具名 llm 节点，或结果既不在 cases 也未指向 llm 节点)
        self._branch_fallback(node_id, node, cmd, out, t0, vp)

    def run(self):
        cls = self.graph["classification"]
        cls_node_id = cls["decision_node"]
        gates = cls.get("gates", {})
        doc_type = self.do_classification()
        self.doc_type = doc_type
        if doc_type is None:
            # 分型失败：无法判断闸门适用性，退化为不闸门跑完全部步骤(仍同构、仍留证据)。
            gates = {}
        for node in self.graph["nodes"]:
            if node["id"] == cls_node_id or node.get("type") == "llm":
                continue
            self.run_branch_node(node, doc_type, gates)

    def overall(self) -> str:
        vals = list(self.verdicts.values())
        if any(v == "FAIL" for v in vals):
            return "FAIL"
        if any(v is None or v == "NEEDS_HUMAN" for v in vals):
            return "NEEDS_HUMAN"
        return "PASS"


# --------------------------------------------------------------------------
# CleanupAuditRunner：cleanup-audit.v1 实现（c1临时库残留/c2临时账号残留/c3僵尸授权/
# c4隔离实例残留/c5worktree积压）。图节点 n2-n6 的 cmd_template 只负责"取数据"
# (docker exec mysql -N -e "SELECT..." / lsof / git worktree list)，不含 point=/
# verdict= 字样——判决逻辑(阈值比较/豁免名单/配对账号反查)在本 runner 用 Python 对原始
# 输出解析后计算，与 PremergeGateRunner 对 g1 原始 `git status --porcelain` 输出算
# tracked_dirty/conflict 是同一模式，不是"改判决逻辑"，只是把归纳产物里"判据描述文字"
# 落成可执行代码。
# --------------------------------------------------------------------------

class CleanupAuditRunner(BaseRunner):
    def do_n1(self):
        """[运行器实现] 图节点 n1 对应轨迹里每轮开头的环境操作/观测记录(input.mutation
        字段的叙述)，18 轮里内容各不相同(建库/建账号/起停端口进程/纯观测)，本身不是可
        复用的判决或工具命令，只是采集时为了可复现而记录的 fixture 铺垫动作——不是
        "收尾体检"本身的判决步骤(5个判决点c1-c5全在n2-n6)。生产复放时按 NO-OP 处理，
        不执行任何命令。"""
        node = self.node("n1")
        t0 = time.time()
        scope = self.input.get("scope", "")
        digest = f"NO-OP: cleanup-audit invocation start (scope={scope})"
        dur = int((time.time() - t0) * 1000)
        self.finish_node("n1", "", digest, {}, None, "OK", None, None, dur)

    def do_n2(self):
        """判决点 c1（临时库残留）：库名匹配 prefix_re 且无活跃会话且实际滞留天数 >
        stale_days -> FAIL。告警三要素(活跃连接数/实际滞留天数/反查出的配对授权账号)
        全部来自本节点自身两段 SELECT 的原始输出：第一段给 min_create/active_sessions，
        第二段(mysql.db WHERE Db REGEXP prefix_re)就是配对账号反查，不需要额外查询。"""
        node = self.node("n2")
        t0 = time.time()
        prefix_re = self.input.get("prefix_re")
        stale_days = float(self.input.get("stale_days"))
        cmd = render(node["cmd_template"], {"prefix_re": prefix_re})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n2", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "c1")
            return
        rc, out, err = self.run_shell(cmd, timeout=30)
        if rc != 0:
            self._branch_fallback("n2", node, cmd, out + err, t0, "c1")
            return
        lines = [l for l in out.split("\n") if l.strip() != ""]
        sections = split_by_markers(lines, {"__GRANTS__"})
        schema_rows = sections[0] if sections else []
        grant_rows = sections[1] if len(sections) > 1 else []
        grants_by_db = {}
        for row in grant_rows:
            parts = row.split("\t")
            if len(parts) >= 2:
                grants_by_db.setdefault(unescape_mysql_db_pattern(parts[0]), []).append(parts[1])
        now = datetime.now(timezone.utc)
        entries = []
        residual = []
        for row in schema_rows:
            parts = row.split("\t")
            if len(parts) < 3:
                continue
            db, min_create, active_sessions = parts[0], parts[1], parts[2]
            try:
                active_n = int(active_sessions)
            except ValueError:
                active_n = 0
            actual_stale_days = None
            try:
                ct = datetime.strptime(min_create, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                actual_stale_days = (now - ct).total_seconds() / 86400.0
            except ValueError:
                pass
            paired = grants_by_db.get(db, [])
            paired_str = ",".join(paired) if paired else "none"
            is_stale = actual_stale_days is not None and actual_stale_days > stale_days
            is_residual = active_n == 0 and is_stale
            if actual_stale_days is not None:
                entry = (
                    f"{db}:min_create={min_create}:active_sessions={active_n}:"
                    f"actual_stale_days={actual_stale_days:.4f}:stale_days_threshold={stale_days:g}:"
                    f"paired_grant_account={paired_str}:"
                    f"stale_gt_{stale_days:g}d={'YES(超期)' if is_stale else 'NO(未超期)'}"
                )
            else:
                entry = f"{db}:PARSE_ERROR:min_create={min_create}"
            entries.append(entry)
            if is_residual:
                residual.append(entry)
        scanned = len(entries)
        residual_count = len(residual)
        detail = "|".join(entries) if entries else "none"
        verdict = "FAIL" if residual_count > 0 else "PASS"
        digest = (
            f"point=c1/prefix_re={prefix_re}/scanned={scanned}/residual_count={residual_count}/"
            f"detail={detail}/verdict={verdict}"
        )
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n2", node, cmd, digest, t0, "c1")
            return
        dur = int((time.time() - t0) * 1000)
        self.verdicts["c1"] = verdict
        self.finish_node("n2", out, digest, {}, None, verdict, verdict, cmd, dur)

    def do_n3(self):
        """判决点 c2（临时账号残留）：名字匹配 prefix_re 且(全部授权指向的库都不存在 或
        无任何授权) -> FAIL；至少一个授权库存在(在用) -> PASS。"""
        node = self.node("n3")
        t0 = time.time()
        prefix_re = self.input.get("prefix_re")
        cmd = render(node["cmd_template"], {"prefix_re": prefix_re})
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n3", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "c2")
            return
        rc, out, err = self.run_shell(cmd, timeout=30)
        if rc != 0:
            self._branch_fallback("n3", node, cmd, out + err, t0, "c2")
            return
        lines = [l for l in out.split("\n") if l.strip() != ""]
        sections = split_by_markers(lines, {"__GRANTS__", "__SCHEMATA__"})
        user_rows = sections[0] if len(sections) > 0 else []
        grant_rows = sections[1] if len(sections) > 1 else []
        schema_rows = sections[2] if len(sections) > 2 else []
        existing_schemas = {r.strip() for r in schema_rows if r.strip()}
        grants_by_user = {}
        for row in grant_rows:
            parts = row.split("\t")
            if len(parts) >= 2:
                grants_by_user.setdefault(parts[1], []).append(unescape_mysql_db_pattern(parts[0]))
        users = []
        seen_users = set()
        for row in user_rows:
            parts = row.split("\t")
            if not parts or not parts[0] or parts[0] in seen_users:
                continue
            seen_users.add(parts[0])
            users.append(parts[0])
        entries = []
        residual = []
        for u in users:
            grants = grants_by_user.get(u, [])
            existing = [db for db in grants if db in existing_schemas]
            in_use = len(existing) > 0
            grants_str = ",".join(grants) if grants else "none"
            entry = (
                f"{u}:grants={grants_str}:target_exists={'YES' if existing else 'NO'}:"
                f"in_use={'YES' if in_use else 'NO'}"
            )
            entries.append(entry)
            if not in_use:
                residual.append(entry)
        scanned = len(entries)
        residual_count = len(residual)
        detail = "|".join(entries) if entries else "none"
        verdict = "FAIL" if residual_count > 0 else "PASS"
        digest = (
            f"point=c2/prefix_re={prefix_re}/scanned={scanned}/residual_count={residual_count}/"
            f"detail={detail}/verdict={verdict}"
        )
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n3", node, cmd, digest, t0, "c2")
            return
        dur = int((time.time() - t0) * 1000)
        self.verdicts["c2"] = verdict
        self.finish_node("n3", out, digest, {}, None, verdict, verdict, cmd, dur)

    def do_n4(self):
        """判决点 c3（僵尸授权）：mysql.db 授权指向不存在的库 -> FAIL。全局扫描、不按
        prefix_re 过滤(僵尸授权对任何库都是风险，任务定义本身未提 scope 限定)；digest
        里的 scope= 字段只回显当前 prefix_re 输入作审计留痕，不是过滤条件。"""
        node = self.node("n4")
        t0 = time.time()
        prefix_re = self.input.get("prefix_re")
        cmd = node["cmd_template"]
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n4", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "c3")
            return
        rc, out, err = self.run_shell(cmd, timeout=30)
        if rc != 0:
            self._branch_fallback("n4", node, cmd, out + err, t0, "c3")
            return
        lines = [l for l in out.split("\n") if l.strip() != ""]
        sections = split_by_markers(lines, {"__SCHEMATA__"})
        grant_rows = sections[0] if sections else []
        schema_rows = sections[1] if len(sections) > 1 else []
        existing_schemas = {r.strip() for r in schema_rows if r.strip()}
        zombies = []
        for row in grant_rows:
            parts = row.split("\t")
            if len(parts) < 2:
                continue
            db, user = unescape_mysql_db_pattern(parts[0]), parts[1]
            if db not in existing_schemas:
                zombies.append(f"{db}:{user}")
        zombie_count = len(zombies)
        zombies_str = ",".join(zombies) if zombies else "none"
        verdict = "FAIL" if zombie_count > 0 else "PASS"
        digest = f"point=c3/scope={prefix_re}/zombie_count={zombie_count}/zombies={zombies_str}/verdict={verdict}"
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n4", node, cmd, digest, t0, "c3")
            return
        dur = int((time.time() - t0) * 1000)
        self.verdicts["c3"] = verdict
        self.finish_node("n4", out, digest, {}, None, verdict, verdict, cmd, dur)

    def do_n5(self):
        """判决点 c4（隔离实例残留）：端口落 port_lo-port_hi 段 且 运行时长 >
        runtime_hours，且先过豁免名单(port_exempt 命中的端口永不入判，无论运行时长多长)
        -> FAIL。告警带(端口/进程名/运行时长)。[运行器实现] 图 cmd_template 是不带任何
        输入变量的全局 `lsof` 扫描，范围/豁免/阈值过滤全部在本方法用 Python 完成(与
        PremergeGateRunner 对 g1/g3 原始输出算判决同一模式)；ps -o etime= 逐 pid 查
        运行时长，两条都是只读系统状态查询。"""
        node = self.node("n5")
        t0 = time.time()
        cmd = node["cmd_template"]
        ok, reason = check_readonly(cmd)
        if not ok:
            self._branch_fallback("n5", node, cmd, f"GUARD_BLOCKED:{reason}", t0, "c4")
            return
        rc, out, err = self.run_shell(cmd, timeout=30)
        if rc != 0:
            self._branch_fallback("n5", node, cmd, out + err, t0, "c4")
            return
        ranges = parse_port_ranges(self.input.get("port_lo"), self.input.get("port_hi"))
        exempt = parse_int_list(self.input.get("port_exempt"))
        runtime_hours_threshold = float(self.input.get("runtime_hours"))
        listeners = parse_lsof_listen(out)
        seen = set()
        in_range = []
        for lst in listeners:
            key = (lst["pid"], lst["port"])
            if key in seen:
                continue
            seen.add(key)
            if any(lo <= lst["port"] <= hi for lo, hi in ranges):
                in_range.append(lst)
        exempted_hits = [l for l in in_range if l["port"] in exempt]
        candidates = [l for l in in_range if l["port"] not in exempt]
        detected = []
        residual = []
        for l in candidates:
            ps_cmd = f"ps -o etime= -p {l['pid']}"
            ok2, reason2 = check_readonly(ps_cmd)
            etime = ""
            if ok2:
                rc2, ps_out, _ = self.run_shell(ps_cmd, timeout=10)
                if rc2 == 0:
                    etime = ps_out.strip()
            hours = parse_etime_hours(etime)
            entry = f"{l['cmd']}:{l['pid']}:{l['port']}:elapsed={etime or 'unknown'}"
            detected.append(entry)
            if hours is not None and hours > runtime_hours_threshold:
                residual.append(entry)
        range_str = ",".join(f"{lo}-{hi}" for lo, hi in ranges)
        exempt_str = ",".join(str(p) for p in exempt) if exempt else "none"
        detected_str = ",".join(detected) if detected else "none"
        exempt_hit_str = (
            ",".join(f"{l['cmd']}:{l['pid']}:{l['port']}" for l in exempted_hits)
            if exempted_hits else "none"
        )
        residual_count = len(residual)
        verdict = "FAIL" if residual_count > 0 else "PASS"
        digest = (
            f"point=c4/range={range_str}/exempt={exempt_str}/detected={detected_str}/"
            f"exempt_hit={exempt_hit_str}/runtime_hours_threshold={runtime_hours_threshold:g}/"
            f"residual_gt_{runtime_hours_threshold:g}h_count={residual_count}/verdict={verdict}"
        )
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n5", node, cmd, digest, t0, "c4")
            return
        dur = int((time.time() - t0) * 1000)
        self.verdicts["c4"] = verdict
        self.finish_node("n5", out, digest, {}, None, verdict, verdict, cmd, dur)

    def do_n6(self):
        """判决点 c5（worktree 积压）：相对 feat/<项目>-integration 零 diff 的可回收
        worktree 数 ≥ backlog_threshold -> FAIL。[运行器实现] 图 cmd_template 记录的是
        一整段含 `$()` 命令替换+管道的 bash for 循环，check_readonly 的只读守卫无法安全
        解析这种复合脚本是否只读(与 changeset-audit 的 n9b 多提交基线搜索同理)，因此
        分解成逐条独立过闸的简单 git 只读调用(worktree list / merge-base / diff)，
        语义与图记录的循环完全一致，只是不再拼成一整条不透明的 shell 脚本执行。"""
        node = self.node("n6")
        t0 = time.time()
        worktree_root = self.input.get("worktree_root")
        backlog_threshold = int(self.input.get("backlog_threshold"))
        list_cmd = f"git --no-optional-locks -C {worktree_root} worktree list --porcelain"
        ok, reason = check_readonly(list_cmd)
        if not ok:
            self._branch_fallback("n6", node, list_cmd, f"GUARD_BLOCKED:{reason}", t0, "c5")
            return
        rc, out, err = self.run_shell(list_cmd, timeout=30)
        if rc != 0:
            self._branch_fallback("n6", node, list_cmd, out + err, t0, "c5")
            return
        branches = []
        for line in out.split("\n"):
            if line.startswith("branch "):
                ref = line[len("branch "):].strip()
                branches.append(ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref)
        total = 0
        reclaimable = 0
        for br in branches:
            mb_cmd = f"git --no-optional-locks -C {worktree_root} merge-base {shlex.quote(br)} feat/<项目>-integration"
            ok2, _reason2 = check_readonly(mb_cmd)
            if not ok2:
                continue
            rc2, mb_out, _ = self.run_shell(mb_cmd, timeout=15)
            mb = mb_out.strip()
            if rc2 != 0 or not mb:
                continue
            total += 1
            diff_cmd = f"git --no-optional-locks -C {worktree_root} diff --name-only {mb} {shlex.quote(br)}"
            ok3, _reason3 = check_readonly(diff_cmd)
            if not ok3:
                continue
            rc3, diff_out, _ = self.run_shell(diff_cmd, timeout=15)
            if rc3 != 0:
                continue
            if len([l for l in diff_out.split("\n") if l.strip() != ""]) == 0:
                reclaimable += 1
        verdict = "FAIL" if reclaimable >= backlog_threshold else "PASS"
        digest = (
            f"point=c5/total_worktrees={total}/reclaimable_zero_diff={reclaimable}/"
            f"backlog_threshold={backlog_threshold}/verdict={verdict}"
        )
        if not contract_ok(node["output_contract"], digest):
            self._branch_fallback("n6", node, list_cmd, digest, t0, "c5")
            return
        dur = int((time.time() - t0) * 1000)
        self.verdicts["c5"] = verdict
        self.finish_node("n6", out, digest, {}, None, verdict, verdict, list_cmd, dur)

    def run(self):
        self.do_n1()
        self.do_n2()
        self.do_n3()
        self.do_n4()
        self.do_n5()
        self.do_n6()

    def overall(self) -> str:
        vals = [self.verdicts.get(p) for p in ("c1", "c2", "c3", "c4", "c5")]
        if any(v == "FAIL" for v in vals):
            return "FAIL"
        if any(v is None or v == "NEEDS_HUMAN" for v in vals):
            return "NEEDS_HUMAN"
        return "PASS"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

FLOW_RUNNERS = {
    "changeset-audit": ChangesetAuditRunner,
    "premerge-gate": PremergeGateRunner,
    "receipt-compliance": ReceiptComplianceRunner,
    "cleanup-audit": CleanupAuditRunner,
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trace_runs ("
        "run_id TEXT, run_tag TEXT, changeset_no TEXT, node_id TEXT, "
        "status TEXT, output_digest TEXT, ts TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_evidence ("
        "run_id TEXT, node_id TEXT, cmd_sha256 TEXT, digest TEXT, "
        "duration_ms INTEGER, ts TEXT)"
    )
    conn.commit()
    conn.close()


def parse_injections(inject_args):
    injections = {}
    pat = re.compile(r'^([A-Za-z0-9_]+)\.output\.([A-Za-z0-9_]+)=(.*)$')
    for s in inject_args:
        m = pat.match(s)
        if not m:
            raise SystemExit(f"--inject 格式非法: {s} (期望 <node>.output.<field>=<value>)")
        node_id, field, value = m.group(1), m.group(2), m.group(3)
        injections.setdefault(node_id, {})[field] = value
    return injections


def parse_inputs(changeset, input_args):
    """--input <key>=<value>（可重复）；--changeset 是兼容别名，等价于 --input changeset_no=..."""
    inputs = {}
    if changeset:
        inputs["changeset_no"] = changeset
    for s in input_args:
        if "=" not in s:
            raise SystemExit(f"--input 格式非法: {s} (期望 <key>=<value>)")
        k, v = s.split("=", 1)
        inputs[k] = v
    return inputs


def parse_args():
    ap = argparse.ArgumentParser(description="复放运行器(通用化)：按图 flow 名分派到对应执行器")
    ap.add_argument("graph", help="工作流图 JSON 路径")
    ap.add_argument("--changeset", default=None,
                     help="兼容别名，等价于 --input changeset_no=<value>")
    ap.add_argument("--input", action="append", default=[],
                     help="<key>=<value>，可重复；例如 --input worktree=/path --input receipt=/path")
    ap.add_argument("--workdir", required=True, help="materialize 输出与中间文件目录")
    ap.add_argument("--inject", action="append", default=[],
                     help="模拟数据漂移: <node>.output.<field>=<value>，可重复")
    ap.add_argument("--run-tag", default="default", help="本轮运行标签")
    return ap.parse_args()


def main():
    args = parse_args()
    with open(args.graph, encoding="utf-8") as f:
        graph = json.load(f)
    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    init_db()
    injections = parse_injections(args.inject)
    inputs = parse_inputs(args.changeset, args.input)

    flow = graph.get("flow")
    runner_cls = FLOW_RUNNERS.get(flow)
    if runner_cls is None:
        raise SystemExit(f"未知 flow: {flow!r}（仅支持 {sorted(FLOW_RUNNERS)}）")

    runner = runner_cls(graph, inputs, workdir, args.run_tag, injections)
    t0 = time.time()
    runner.run()
    wall = time.time() - t0

    summary = {
        "run_id": runner.run_id,
        "run_tag": args.run_tag,
        "flow": flow,
        "changeset_no": inputs.get("changeset_no"),
        "verdicts": runner.verdicts,
        "overall": runner.overall(),
        "model_calls": runner.model_calls,
        "db_rows_written": runner.db_rows,
        "wall_clock_sec": round(wall, 3),
        "aborted": runner.aborted,
    }
    if getattr(runner, "doc_type", None) is not None:
        summary["doc_type"] = runner.doc_type
    print(json.dumps(summary, ensure_ascii=False))
    runner.conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
