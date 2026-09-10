#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轨迹归纳器 (通用版)

把任意流程的一批真实执行轨迹 (round-*.jsonl，每份 = 1 input + N step + 1 verdict)
自动提炼成一张确定性工作流图 (JSON)。只用 Python 3 标准库，不装任何依赖。

用法:
    python3 induce.py <轨迹目录> <输出图路径> [--flow <流程名>]

flow 名默认取轨迹目录的 basename (不传 --flow 时)。flow == 'changeset-audit' 时走
历史专用管线 (build_graph_changeset_audit，逻辑与产出跟老版本完全一致，用于回归)；
其余任意 flow 走通用引擎 (build_graph_generic)。

通用化要点 (相对老版本):
    - 输入变量名不再写死 changeset_no，从 input 行的键自动推断 (discover_input_vars)
    - verdict 判决点键不再写死 p1..p5，从 verdict.points 的键动态发现
      (discover_verdict_points)，changeset-audit 管线额外用它做一致性断言
    - 新增分型输出识别 (discover_classification_fields + classify_classification_role)：
      verdict 里除 pass/points/fail_detail 外的字段 (例如 doc_type) 被识别为"分型输出"，
      写入图顶层 classification 段
    - 新增 N/A 判决值支持：某判决点在部分轮次为 "N/A" 时，尝试从同轮其它字段(通常是
      分型字段)里找到与 N/A 完全相关的取值，标记 conditional + applicable_when；找不到
      完美相关时诚实标记 unresolved/needs_review，不瞎猜
    - 变量遮蔽对齐、稳定判据 (遮蔽后模板 n/N 一致才算确定性节点)、变量绑定归因
      (10/10 或 N/N 全轮一致才算绑定成功，否则 UNRESOLVED)、机械/llm 判决路由这些
      能力保持不变；本版本沿用原实现，未发现代码中有 "≥80%" 这类比例阈值，仍要求
      遮蔽后模板全轮 (N/N) 完全一致才判定为确定性节点

算法总览 (对应任务书 Step 2a-2d，changeset-audit 管线不变，通用引擎沿用同一套原则):
    a) 变量遮蔽对齐: 对每个 seq 的全部轮次 cmd 遮蔽易变量后逐字比对
    b) 稳定判据: 全轮相同 -> 确定性 tool 节点; 含循环占位符 -> map 节点;
       结构性判决 (compare / 输出直接给出判决点值) -> branch 节点
    c) 变量绑定归因: 每个模板变量必须能归因到 input 或更早步骤的 output 字段
       (按值跨全部轮次匹配, 全轮成立才算绑定成功, 否则标 UNRESOLVED)
    d) 判决逻辑归纳: 判决点与哪些步骤输出相关，归纳机械规则；有反例或语义需要解读的
       标记为 llm 决策节点
"""
import sys
import os
import re
import json
import glob


# ---------------------------------------------------------------------------
# 变量识别正则 (结构性 "形状" 探测，不是某个流程专属：sha40/数据库数值id/链式相等/
# shell变量引用 这些形状在很多脚本化流程里都可能出现，保留供任意流程复用)
# ---------------------------------------------------------------------------
CSNO_RE = re.compile(r'CS-\d{8}-\d{4}')
SHA40_RE = re.compile(r'\b[0-9a-f]{40}\b')
CHANGESET_ID_RE = re.compile(r'changeset_id=(\d+)')
# "test A -eq B -a C -eq D" 是一种连续相等性检查的常见 shell 写法 (A==B==...==D 链式对比)
TESTEQ_RE = re.compile(r'test (\d+) -eq (\d+) -a (\d+) -eq (\d+)')
# compare 步骤输出里常见的 "(name1=val1 name2=val2 name3=val3)" 叙述性括注，
# 用于给 TESTEQ_RE 捕获的数字槽位恢复语义变量名
NARRATION_RE = re.compile(r'\((\w+)=(\d+)\s+(\w+)=(\d+)\s+(\w+)=(\d+)\)')
# map 节点里的字面循环占位符，例如 "for f in <non-delete files>; do ... done"
LOOP_PLACEHOLDER_RE = re.compile(r'for\s+\w+\s+in\s+<([^>]+)>;\s*do\b.*\bdone\b')
# compare 步骤里引用"前面某步算出的量"的 shell 变量写法，例如 echo "$TITLE"
# (只在 kind==compare 时启用，避免误伤 sql 步骤里恒定不变的 $MYSQL_ROOT_PASSWORD)
DOLLAR_VAR_RE = re.compile(r'\$([A-Z][A-Z0-9_]*)\b')
# 叙述性 cmd 里直接点名"依赖 STEP13 的结果"这种自然语言引用
STEP_MENTION_RE = re.compile(r'\bSTEP(\d+)\b')

# 字段分隔: 原版只按 '\t' 切分 (changeset-audit 的 SQL 行输出是 tab 分隔)。通用化后
# 还要支持 "; " 分隔以及 "/" 分隔 (receipt-compliance 先后出现过两种 output_digest
# 惯例: 早期用 "; "，当前用 "/"，例如 "status_found=True/hedge_found=False/verdict=PASS")。
# "/" 只在紧跟一个形如 "key=" 的小写 snake_case 标识符时才当分隔符 (用零宽先行断言
# 判断)，避免把文件路径里的 "/" (例如 "docs/02-xxx/README.md") 误切开——已核对
# changeset-audit 全部 output_digest 都不含 "/[a-z_][a-z0-9_]*=" 这种形态，新增这个
# 分隔符不改变 changeset-audit 路径的既有行为。
_FIELD_SPLIT_RE = re.compile(r'[\t;]\s*|/(?=[a-z_][a-z0-9_]*=)')


def load_rounds(traj_dir):
    """
    读取 round-*.jsonl。每份轨迹须是 input + N个step(seq 1..N 连续) + verdict，
    N (步骤数) 在同一目录内的全部轮次间必须一致；不同流程(目录)的 N 可以不同——
    这是相对老版本的通用化: 老版本写死 16 行(input+14 step+verdict)，只服务
    changeset-audit；receipt-compliance 是 21 行(input+19 step+verdict)。
    """
    files = sorted(glob.glob(os.path.join(traj_dir, 'round-*.jsonl')))
    if not files:
        raise SystemExit(f"未在 {traj_dir} 找到 round-*.jsonl 轨迹文件")
    rounds = []
    n_steps = None
    for fp in files:
        with open(fp, encoding='utf-8') as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        recs = [json.loads(ln) for ln in lines]
        if len(recs) < 3:
            raise SystemExit(f"{fp}: 至少需要 input+1 step+verdict，实际 {len(recs)} 行")
        if recs[0]['type'] != 'input':
            raise SystemExit(f"{fp}: 首行必须是 input")
        if recs[-1]['type'] != 'verdict':
            raise SystemExit(f"{fp}: 末行必须是 verdict")
        steps = {}
        for r in recs[1:-1]:
            if r['type'] != 'step':
                raise SystemExit(f"{fp}: 中间行必须是 step, got {r}")
            steps[r['seq']] = r
        this_n = len(steps)
        if n_steps is None:
            n_steps = this_n
        elif this_n != n_steps:
            raise SystemExit(f"{fp}: 步骤数 {this_n} 与同目录其它轮次 {n_steps} 不一致")
        if sorted(steps.keys()) != list(range(1, this_n + 1)):
            raise SystemExit(f"{fp}: seq 必须恰好覆盖 1..{this_n}, got {sorted(steps.keys())}")
        rounds.append({'file': fp, 'input': recs[0], 'steps': steps, 'verdict': recs[-1]})
    return rounds


def parse_fields(digest):
    """把 output_digest 解析成 key=value 字段字典 (按 tab 或 '; ' 切分，宽容: 非
    key=value 的片段忽略)。"""
    fields = {}
    for part in _FIELD_SPLIT_RE.split(digest):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            k = k.strip()
            if k and k not in fields:
                fields[k] = v.strip()
    return fields


# ---------------------------------------------------------------------------
# cmd 遮蔽 + 变量槽位提取 (结构性正则部分保持不变；通用引擎在此基础上叠加一层
# "已知输入变量字面值" 遮蔽，见 build_step_node_generic)
# ---------------------------------------------------------------------------

def mask_and_extract(cmd, kind):
    """
    对单条 cmd 做变量遮蔽，返回 (masked_template, slots, structural_vars)。
    slots: 有序列表 [(var_name, raw_value), ...]，同名变量出现多次时只记一次
    (模板里会复用同一个占位符)——这些走"按值匹配"的绑定归因。
    structural_vars: 只在 kind=='compare' 时探测的 "$VARNAME" 引用 (例如 $TITLE)，
    trajectory 里没有记录它实际展开成什么值，只能按"字段名"去匹配更早步骤的输出
    字段名，走"按名匹配"的绑定归因 (与 slots 分开处理)。
    """
    slots = []  # 保序，去重按 var_name
    seen = {}

    def add_slot(name, value):
        if name in seen:
            return seen[name]
        seen[name] = value
        slots.append((name, value))
        return value

    # 先把 cmd 里本来就存在的花括号转义成双花括号 (例如 grep 的 '{40}' 数量词)，
    # 这样后面注入的单花括号 "{varname}" 占位符才是唯一、无歧义的模板变量标记，
    # 不会和字面出现的 "{40}" 这类正则量词混在一起。
    masked = cmd.replace('{', '{{').replace('}', '}}')

    # 1) changeset_id=NNN (数据库数值 id, 出现在 SQL WHERE 中)
    m = CHANGESET_ID_RE.search(masked)
    if m:
        add_slot('changeset_id', m.group(1))
        masked = CHANGESET_ID_RE.sub('changeset_id={changeset_id}', masked)

    # 2) test A -eq B -a C -eq D 链式相等性检查 (在做 changeset_no/sha 遮蔽之前处理，
    #    避免和其它数字遮蔽规则冲突；这类语句里的数字都是独立的计数值)
    m = TESTEQ_RE.search(masked)
    if m:
        a, b, c, d = m.groups()
        # 尝试用同一步骤输出里的叙述性括注 "(name1=v1 name2=v2 name3=v3)" 恢复语义变量名
        # (由调用方在拿到 output_digest 后二次调用 rename_testeq_slots 完成，这里先给
        # 通用占位名 v1/v2/v3/v4，v2==v3 时合并成同一个占位符)
        name_a, name_b, name_c, name_d = 'v1', 'v2', 'v2' if b == c else 'v3', ('v3' if b == c else 'v4')
        add_slot(name_a, a)
        add_slot(name_b, b)
        if name_c != name_b:
            add_slot(name_c, c)
        add_slot(name_d, d)
        masked = TESTEQ_RE.sub(
            'test {%s} -eq {%s} -a {%s} -eq {%s}' % (name_a, name_b, name_c, name_d),
            masked, count=1,
        )

    # 3) 变更单号 CS-\d{8}-\d{4} (可能出现多次，例如既在 SQL WHERE 又在 scratch 文件名里)
    m = CSNO_RE.search(masked)
    if m:
        add_slot('changeset_no', m.group(0))
        masked = CSNO_RE.sub('{changeset_no}', masked)

    # 4) 40 位十六进制 sha (可能出现多次，例如 diff A^1 A)
    m = SHA40_RE.search(masked)
    if m:
        add_slot('sha', m.group(0))
        masked = SHA40_RE.sub('{sha}', masked)

    # 5) compare 步骤里的 "$VARNAME" 引用 (只在 kind==compare 时启用，避免误伤
    #    sql 步骤里恒定不变的 $MYSQL_ROOT_PASSWORD)
    structural_vars = []
    if kind == 'compare':
        m = DOLLAR_VAR_RE.search(masked)
        if m:
            varname = m.group(1).lower()
            masked = DOLLAR_VAR_RE.sub('{' + varname + '}', masked, count=1)
            structural_vars.append(varname)

    return masked, slots, structural_vars


def rename_testeq_slots(slots, output_digest):
    """
    用本步骤自身 output_digest 里的叙述性括注 "(name1=v1 name2=v2 name3=v3)"
    把 v1/v2/v3 这类占位名换成有语义的变量名 (例如 db_changeset_file_count)。

    注意: 这里必须按"位置"对应 (test 语句里从左到右第1/2/3个槽位 <-> 括注里从左到右
    第1/2/3个命名量)，不能按"值"对应——因为这3个量在全部轮次里数值都相等
    (file_count == change_file_count == git_count 才会 PASS)，按值匹配会导致
    3个变量名全部塌缩成同一个 (dict 以值为 key 时后写覆盖前写)。
    只在 slots 里存在 v1/v2/v3 时生效, 否则原样返回。
    """
    names = {s[0] for s in slots}
    if not ({'v1', 'v2'} & names):
        return slots
    m = NARRATION_RE.search(output_digest)
    if not m:
        return slots
    n1, _val1, n2, _val2, n3, _val3 = m.groups()
    pos_map = {'v1': n1, 'v2': n2, 'v3': n3}
    return [(pos_map.get(var, var), val) for var, val in slots]


# ---------------------------------------------------------------------------
# 变量绑定归因: 按值跨全部轮次匹配到 input 或更早步骤的 output 字段
# ---------------------------------------------------------------------------

def _origin_sort_key(path):
    """按 seq 号从小到大排序 (更早出现的来源更"根源")，input.* 排在最前面。"""
    if path.startswith('input.'):
        return (-1, path)
    m = re.match(r'n(\d+)\.', path)
    return (int(m.group(1)) if m else 999, path)


def resolve_binding(var_name, per_round_values, per_round_known, exclude=()):
    """
    var_name: 变量名
    per_round_values: [round_idx -> raw_value]
    per_round_known: [round_idx -> {value_str: [origin_path, ...]}]  (只含更早步骤/input)
    exclude: 本节点里已经被其它变量占用的 origin_path 集合 (同一节点内不同变量优先
             不复用同一个来源——若某几个来源在全部轮次里数值都相等而无法靠值区分，
             这是唯一能把它们分开的信号)
    返回: {"from": origin_path} 或 {"from": "UNRESOLVED", "detail": "..."}
    要求全部轮次一致地指向同一个 origin_path。
    """
    candidate_set = None
    for ridx, val in per_round_values.items():
        origins = set(per_round_known[ridx].get(val, []))
        if not origins:
            return {"from": "UNRESOLVED", "detail": f"round {ridx+1}: 值 {val!r} 未在 input/更早步骤输出中找到"}
        candidate_set = origins if candidate_set is None else (candidate_set & origins)
    if not candidate_set:
        return {"from": "UNRESOLVED", "detail": "全部轮次里没有共同的归因来源 (候选来源在各轮不一致)"}
    narrowed = candidate_set - set(exclude)
    pool = narrowed if narrowed else candidate_set
    chosen = sorted(pool, key=_origin_sort_key)[0]
    result = {"from": chosen}
    if len(candidate_set) > 1:
        result["ambiguous_candidates"] = sorted(candidate_set, key=_origin_sort_key)
    return result


# ---------------------------------------------------------------------------
# output_contract 归纳 (简单 DSL 字符串)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(SHA40_RE.pattern + r'|' + CSNO_RE.pattern + r'|\d+')


def _tokenize_to_regex(s):
    """
    把一个字符串转成一条 regex：识别出的变量 token (sha40/变更单号/数字) 换成对应的
    正则片段，其余字面文本用 re.escape 转义 (否则输出里的 "(" ")" 这类字符会被
    误当成正则语法而不是字面字符，写出一条永远匹配不上真实输出的假契约)。
    """
    parts = []
    last = 0
    for m in _TOKEN_RE.finditer(s):
        parts.append(re.escape(s[last:m.start()]))
        tok = m.group(0)
        if re.fullmatch(SHA40_RE.pattern, tok):
            parts.append(r'[0-9a-f]{40}')
        elif re.fullmatch(CSNO_RE.pattern, tok):
            parts.append(r'CS-\d{8}-\d{4}')
        else:
            parts.append(r'\d+')
        last = m.end()
    parts.append(re.escape(s[last:]))
    return ''.join(parts)


def _masked_key(s):
    """仅用于跨轮"整串结构是否一致"的判断 key（把变量 token 归一化，不用于生成正则）。"""
    s = SHA40_RE.sub('<SHA40>', s)
    s = CSNO_RE.sub('<CSNO>', s)
    s = re.sub(r'\d+', '<N>', s)
    return s


def infer_contract(outputs):
    """
    从实际输出归纳一个简单契约字符串。分层尝试:
    1) 纯数字 -> int>=0
    2) tab 分隔的 key=value 结构，且每列 key 恒定 -> 拼出逐列 regex
    3) 遮蔽后的整串结构在全部轮次里完全一致 -> 用该串生成 regex (字面文本转义、变量还原成 \\d+/hex/CSNO)
    4) 按 "; " 切分找最长公共前缀 token 序列 -> "regex:^<前缀>.*$"
    5) 兜底 -> "nonempty"
    """
    if all(re.fullmatch(r'\d+', o) for o in outputs):
        return "int>=0"

    split_outputs = [o.split('\t') for o in outputs]
    if len({len(p) for p in split_outputs}) == 1 and len(split_outputs[0]) > 1:
        cols = list(zip(*split_outputs))
        parts = []
        ok = True
        for col in cols:
            keys = set()
            vals = []
            for cell in col:
                if '=' not in cell:
                    ok = False
                    break
                k, v = cell.split('=', 1)
                keys.add(k)
                vals.append(v)
            if not ok or len(keys) != 1:
                ok = False
                break
            key = keys.pop()
            if all(re.fullmatch(r'[0-9a-f]{40}', v) for v in vals):
                vp = r'[0-9a-f]{40}'
            elif all(re.fullmatch(r'\d+', v) for v in vals):
                vp = r'\d+'
            elif all(re.fullmatch(r'CS-\d{8}-\d{4}', v) for v in vals):
                vp = r'CS-\d{8}-\d{4}'
            elif all(re.fullmatch(r'[0-9A-F]{6}', v) for v in vals):
                vp = r'[0-9A-F]{6}'
            else:
                vp = r'.+'
            parts.append(f'{re.escape(key)}={vp}')
        if ok:
            return 'regex:^' + r'\t'.join(parts) + '$'

    masked_keys = [_masked_key(o) for o in outputs]
    if len(set(masked_keys)) == 1:
        return 'regex:^' + _tokenize_to_regex(outputs[0]) + '$'

    # 按 ": " 或 "; " 之后的空格切分 (覆盖 "N rows sorted; first=..." 和
    # "N non-delete files: docs/..." 两种叙述风格)
    split_re = re.compile(r'(?<=[:;]) ')
    token_lists = [split_re.split(o) for o in outputs]
    masked_token_lists = [split_re.split(mk) for mk in masked_keys]
    min_len = min(len(t) for t in token_lists)
    prefix = []
    for i in range(min_len):
        col_masked = [t[i] for t in masked_token_lists]
        if len(set(col_masked)) == 1:
            prefix.append(_tokenize_to_regex(token_lists[0][i]))
        else:
            break
    if prefix:
        # 每个 token 本身已经带着它末尾的分隔符(":"或";"，来自 split_re 的 lookbehind)，
        # 拼回去只需要用单个空格，不能再手动插一次分隔符 (否则会重复出现 ";;")
        return 'regex:^' + ' '.join(prefix) + '.*$'

    return "nonempty"


# ---------------------------------------------------------------------------
# 通用发现: 输入变量 / 判决点键 / 分型字段 (不写死任何流程专属的名字/数量)
# ---------------------------------------------------------------------------

def discover_input_vars(rounds):
    """input 行的键 (排除 type/ts) 自动推断为输入变量名，保留原始出现顺序，校验
    全部轮次键集合一致。"""
    keys = None
    for r in rounds:
        k = [kk for kk in r['input'].keys() if kk not in ('type', 'ts')]
        if keys is None:
            keys = k
        elif k != keys:
            raise SystemExit(f"input 行字段不一致: {keys} vs {k} ({r['file']})")
    return keys


def discover_verdict_points(rounds):
    """动态发现判决点键 (p1..pN / r1..rN / g1..gN 等)，校验全部轮次键集合一致，
    按字母前缀+数字大小排序 (而不是字典序，避免 p10 排到 p2 前面)。"""
    keys = None
    for r in rounds:
        k = set(r['verdict']['points'].keys())
        if keys is None:
            keys = k
        elif k != keys:
            raise SystemExit(f"verdict.points 键不一致: {sorted(keys)} vs {sorted(k)} ({r['file']})")

    def sort_key(k):
        m = re.match(r'^([A-Za-z]+)(\d+)$', k)
        return (m.group(1), int(m.group(2))) if m else (k, -1)

    return sorted(keys, key=sort_key)


def discover_classification_fields(rounds):
    """verdict 里除 type(记录类型标记)/pass/points/fail_detail 外的额外字段
    (例如 doc_type) 识别为"分型输出"，校验全部轮次键集合一致。"""
    reserved = {'type', 'pass', 'points', 'fail_detail'}
    keys = None
    for r in rounds:
        k = sorted(kk for kk in r['verdict'].keys() if kk not in reserved)
        if keys is None:
            keys = k
        elif k != keys:
            raise SystemExit(f"verdict 分型字段不一致: {keys} vs {k} ({r['file']})")
    return keys


# ===========================================================================
# changeset-audit 专属管线 (与老版本 induce.py 的产出保持等价，作为回归基线)
# ===========================================================================

STEP_COMMENTS = {
    1: "按变更单号查询变更记录基本信息(内部id/标题/提交哈希/文件数)，是后续所有校验的起点",
    2: "校验 commit_hash 是否符合40位十六进制格式(SHA-1)",
    3: "确认该 commit_hash 在本地 git 仓库中确实存在，且对象类型是 commit",
    4: "从变更文件明细表按 changeset_id 统计未删除的文件行数",
    5: "用 git diff 统计该 commit 相对父提交实际改动的文件数",
    6: "三方文件数核对: changeset.file_count / t_code_change_file 计数 / git diff 计数是否链式相等",
    7: "从变更文件明细表按 changeset_id 取出全部文件路径(已排序)",
    8: "用 git diff 取出该 commit 实际改动的文件路径(已排序)",
    9: "逐行 diff 对比 DB 侧文件路径清单与 git 侧文件路径清单",
    10: "取变更记录标题及标题前3字节的十六进制，用于乱码检测",
    11: "检查标题是否包含典型 GBK/UTF-8 错解码乱码字符",
    12: "从变更文件明细表取非 DELETE 类型的文件路径清单(供逐文件 CRLF 检查使用)",
    13: "对每个非删除文件，取其在该 commit 下的内容，统计 CR(\\r) 出现次数",
    14: "汇总 step13 的逐文件 CR 计数，只要有文件 CR>0 就判 FAIL 并列出问题文件",
}

# 5 组语义分组：把 14 个 step 划分到 p1..p5。
# 说明: 原始轨迹的 verdict.points 没有逐点语义标签，无法纯靠数据结构自动推出
# 例如 step3 该归入哪一组 (step3 与 step2 之间没有直接的数据流依赖边，二者都只是
# 各自独立消费 step1 的输出，是兄弟关系而非依赖关系)。这里的分组是人工阅读全部
# 轮次真实 cmd/output 内容后确定的语义边界，并用两条可从数据验证的锚点核对:
#   - p3 <-> step9: fail_detail 文本与 step9 自身 output_digest 内容高度重合
#   - p5 <-> step14: fail_detail 文本与 step14 自身 output_digest 内容高度重合
# 其余分组按 "compare 步骤在 seq 序列中出现的次序 = 1..5 对应 p1..p5" 这一
# 结构性规律确定 (14 步里恰好有 5 个 compare 步骤: seq 2,6,9,11,14)。
# build_graph_changeset_audit() 里会用全部轮次数据校验这个假设 (核对 compare 步骤的
# PASS/FAIL 是否与对应 p_i 100% 一致)，校验失败会中止并报错，而不是静默接受。
# 这套分组表以及下面的判决点键集合是这一个流程的人工领域知识，不做成"通用"发现——
# 通用化体现在: 键集合本身用 discover_verdict_points() 动态取得后与这张表断言一致，
# 表本身过时/流程变化时会直接报错，而不是默默产出错误的图。
GROUPS = [
    (1, [1, 2, 3]),
    (2, [4, 5, 6]),
    (3, [7, 8, 9]),
    (4, [10, 11]),
    (5, [12, 13, 14]),
]
COMPARE_SEQS = [2, 6, 9, 11, 14]


def good_signal(text):
    """从 compare 步骤的 output_digest 里粗判这轮是"好"结果还是"坏"结果。"""
    head = text.strip()
    for good_kw in ('REGEX_OK', 'PASS', 'MATCH', 'CLEAN', 'commit'):
        if head == good_kw or head.startswith(good_kw + ' ') or head.startswith(good_kw + '('):
            return True
    for bad_kw in ('REGEX_FAIL', 'FAIL', 'MISMATCH', 'GARBLED'):
        if head == bad_kw or head.startswith(bad_kw + ' ') or head.startswith(bad_kw + '('):
            return False
    # verdict=PASS / verdict=FAIL 形式 (step14)
    m = re.search(r'verdict=(PASS|FAIL)', head)
    if m:
        return m.group(1) == 'PASS'
    return None


def build_step_node(seq, rounds, known_values_per_round):
    """构建单个 seq 对应的节点信息 (changeset-audit 专属)，同时把该 seq 的输出字段
    登记进 known_values_per_round。"""
    kind = rounds[0]['steps'][seq]['kind']
    raw_cmds = [r['steps'][seq]['cmd'] for r in rounds]
    raw_outputs = [r['steps'][seq]['output_digest'] for r in rounds]

    masked_list = []
    slots_list = []
    structural_vars_list = []
    for cmd in raw_cmds:
        masked, slots, structural_vars = mask_and_extract(cmd, kind)
        masked_list.append(masked)
        slots_list.append(slots)
        structural_vars_list.append(structural_vars)

    # 用本步骤自身输出的叙述性括注，把链式相等判断里的 v1..v4 占位名换成语义名
    renamed_slots_list = [
        rename_testeq_slots(slots, out) for slots, out in zip(slots_list, raw_outputs)
    ]

    stable = len(set(masked_list)) == 1
    n_stable = sum(1 for m in masked_list if m == masked_list[0])

    is_map = bool(LOOP_PLACEHOLDER_RE.search(raw_cmds[0]))
    is_branch = (kind == 'compare')

    # 汇总变量名集合 (取第一轮的顺序为准，假设跨轮变量集合一致)
    var_names = [name for name, _ in renamed_slots_list[0]]
    for rs in renamed_slots_list[1:]:
        this_names = [name for name, _ in rs]
        if this_names != var_names:
            raise SystemExit(f"seq{seq}: 各轮变量槽位不一致 {var_names} vs {this_names}")
    structural_names = structural_vars_list[0]
    for sv in structural_vars_list[1:]:
        if sv != structural_names:
            raise SystemExit(f"seq{seq}: 各轮 structural_vars 不一致 {structural_names} vs {sv}")

    # cmd_template 里出现的占位符名字必须和 bindings 的 key 保持一致：如果链式相等
    # 判断 (seq6) 把 v1/v2/v3 重命名成了语义名 (db_changeset_file_count 等)，模板
    # 文本里的 {v1}/{v2}/{v3} 也要同步替换，否则模板占位符和 bindings key 对不上，
    # 消费图的执行器无法回填。
    cmd_template = masked_list[0]
    for (orig_name, _), (new_name, _) in zip(slots_list[0], renamed_slots_list[0]):
        if orig_name != new_name:
            cmd_template = cmd_template.replace('{' + orig_name + '}', '{' + new_name + '}')

    # 按值绑定：同一节点内不同变量优先不复用同一个来源 (used_origins)。这是唯一能把
    # "全部轮次数值都相等、按值无法区分"的变量 (例如 seq6 的三个计数) 分开的信号。
    bindings = {}
    used_origins = set()
    for var in var_names:
        per_round_values = {}
        for ridx, rs in enumerate(renamed_slots_list):
            d = dict(rs)
            per_round_values[ridx] = d[var]
        binding = resolve_binding(var, per_round_values, known_values_per_round, exclude=used_origins)
        bindings[var] = binding
        if binding.get("from") not in (None, "UNRESOLVED"):
            used_origins.add(binding["from"])

    # 按名绑定 (structural_vars, 例如 seq11 的 $TITLE): trajectory 没有记录它实际
    # 展开的值，只能拿变量名去匹配更早步骤输出字段的 key 名 (字段名是 schema，
    # 在各轮之间本就恒定，因此逐轮核验只是确认这个 schema 事实、不是按值归纳)。
    for var in structural_names:
        origin = None
        for s in range(1, seq):
            if var in parse_fields(rounds[0]['steps'][s]['output_digest']):
                origin = s
        if origin is None:
            bindings[var] = {"from": "UNRESOLVED", "detail": f"未找到字段名 '{var}' 匹配的更早步骤输出"}
            continue
        all_rounds_ok = all(
            var in parse_fields(rounds[ridx]['steps'][origin]['output_digest'])
            for ridx in range(len(rounds))
        )
        bindings[var] = (
            {"from": f"n{origin}.output.{var}"} if all_rounds_ok
            else {"from": "UNRESOLVED", "detail": f"字段 '{var}' 未在全部轮次的 n{origin} 输出里出现"}
        )

    # map 节点的 list_binding: 匹配循环占位符描述文字到更早步骤输出里含同样描述的字段
    list_binding = None
    if is_map:
        placeholder_desc = LOOP_PLACEHOLDER_RE.search(raw_cmds[0]).group(1)  # e.g. "non-delete files"
        list_binding = None
        for ridx in range(len(rounds)):
            found = None
            for s in range(1, seq):
                out = rounds[ridx]['steps'][s]['output_digest']
                if placeholder_desc in out:
                    found = f"n{s}.output"
            if found is None:
                list_binding = {"from": "UNRESOLVED", "detail": f"round {ridx+1}: 未找到含 '{placeholder_desc}' 描述的更早步骤输出"}
                break
            if list_binding is None:
                list_binding = {"from": found}
            elif list_binding.get("from") != found:
                list_binding = {"from": "UNRESOLVED", "detail": "各轮匹配到不同的来源步骤"}
                break

    # seq9 (文件路径 diff) 的两个"文件"输入并非字面值绑定，而是结构性绑定到最近的
    # 更早 sql/git 步骤 (它们各自输出 "N rows sorted" 形态的文件清单)。这是唯一
    # 一处需要按"最近同形态前驱步骤"做结构绑定、而非按值匹配的情况，故单独处理。
    struct_note = None
    if kind == 'compare' and 'diff' in raw_cmds[0] and 'db_files' in raw_cmds[0] and 'git_files' in raw_cmds[0]:
        db_src = git_src = None
        for s in range(seq - 1, 0, -1):
            k = rounds[0]['steps'][s]['kind']
            out0 = rounds[0]['steps'][s]['output_digest']
            if 'rows sorted' in out0:
                if k == 'sql' and db_src is None:
                    db_src = f"n{s}.output"
                if k == 'git' and git_src is None:
                    git_src = f"n{s}.output"
        bindings['db_files'] = {"from": db_src or "UNRESOLVED"}
        bindings['git_files'] = {"from": git_src or "UNRESOLVED"}
        struct_note = "db_files/git_files 为结构性绑定(按 kind+输出形态匹配最近前驱步骤)，非按值匹配"

    # cmd 里直接点名 "STEPn" 这种自然语言依赖引用 (seq14 的 cmd 是叙述性伪代码，
    # 不是真的 shell 命令，靠这个文本线索标出它依赖 n13 的输出)
    step_mentions = sorted({int(x) for x in STEP_MENTION_RE.findall(raw_cmds[0])})
    depends_on = [f"n{s}" for s in step_mentions if s != seq]

    contract = infer_contract(raw_outputs)

    # 登记本步骤输出字段供更晚步骤做值归因 (只登记本步骤，调用方按 seq 顺序推进)
    for ridx, r in enumerate(rounds):
        out = r['steps'][seq]['output_digest']
        fields = parse_fields(out)
        for k, v in fields.items():
            known_values_per_round[ridx].setdefault(v, []).append(f"n{seq}.output.{k}")

    return {
        "seq": seq,
        "kind": kind,
        "is_map": is_map,
        "is_branch": is_branch,
        "cmd_template": cmd_template,
        "stability": f"{n_stable}/{len(rounds)}",
        "depends_on": depends_on,
        "bindings": bindings,
        "list_binding": list_binding,
        "struct_note": struct_note,
        "output_contract": contract,
        "raw_outputs": raw_outputs,
    }


def build_graph_changeset_audit(rounds):
    n = len(rounds)
    input_vars = discover_input_vars(rounds)  # 通用化: 自动推断，不再写死 ["changeset_no"]
    known_values_per_round = [dict() for _ in range(n)]
    for ridx, r in enumerate(rounds):
        for var in input_vars:
            known_values_per_round[ridx][str(r['input'][var])] = [f"input.{var}"]

    # 通用化: 判决点键集合动态发现，并与本流程的人工分组表 GROUPS 断言一致
    # (GROUPS 本身仍是人工阅读全部轮次真实数据后确定的分组知识，见模块顶部注释，
    # 无法纯统计复现，但键集合是否还是 p1..p5 是可以且应该动态校验的，一旦流程的
    # verdict schema 变化，这里会直接报错而不是默默产出过时的图)
    discovered_points = discover_verdict_points(rounds)
    expected_points = [f"p{pnum}" for pnum, _ in GROUPS]
    if discovered_points != expected_points:
        raise SystemExit(
            f"changeset-audit 的 verdict.points 键集合已变化: 数据里是 {discovered_points}，"
            f"但 GROUPS 人工分组表仍是 {expected_points}，需要人工重新核对分组"
        )

    step_nodes = {}
    for seq in range(1, 15):
        step_nodes[seq] = build_step_node(seq, rounds, known_values_per_round)

    # ---- 校验 5 组语义分组假设 ----
    verdicts = [r['verdict']['points'] for r in rounds]
    fail_details = [r['verdict'].get('fail_detail', '') for r in rounds]

    def point_detail(fd, p):
        for chunk in fd.split(' | '):
            chunk = chunk.strip()
            if chunk.startswith(p + ':'):
                return chunk[len(p) + 1:].strip()
        return ''

    group_stats = {}
    for gi, (pnum, seqs) in enumerate(GROUPS):
        pkey = f"p{pnum}"
        compare_seq = [s for s in seqs if s in COMPARE_SEQS]
        assert len(compare_seq) == 1, f"分组 {seqs} 里应恰好含1个 compare 步骤"
        compare_seq = compare_seq[0]
        agree = 0
        mismatches = []
        for ridx in range(n):
            sig = good_signal(rounds[ridx]['steps'][compare_seq]['output_digest'])
            expect_pass = (verdicts[ridx][pkey] == 'PASS')
            if sig is None:
                mismatches.append(f"round{ridx+1}: 无法从 step{compare_seq} 输出判断好坏信号")
                continue
            if sig == expect_pass:
                agree += 1
            else:
                mismatches.append(f"round{ridx+1}: step{compare_seq} 信号={sig} 但 {pkey}={verdicts[ridx][pkey]}")
        if agree != n:
            raise SystemExit(
                f"分组假设校验失败: {pkey} <-> step{compare_seq} 只有 {agree}/{n} 轮一致: {mismatches}"
            )
        group_stats[pkey] = {
            "seqs": seqs,
            "compare_seq": compare_seq,
            "agreement": f"{agree}/{n}",
        }

    # ---- llm 判决点探测: 扫描每个 p_i 的 FAIL 轮 fail_detail，寻找"两侧其实是同一批文件/
    # 数据"这类暗示"机械规则可能误判"的解读性文本 ----
    AMBIGUITY_RE = re.compile(r'[Ss]ame \d+ files?\b')
    llm_points = {}
    for pnum, seqs in GROUPS:
        pkey = f"p{pnum}"
        ambiguous_rounds = []
        for ridx in range(n):
            if verdicts[ridx][pkey] == 'FAIL':
                detail = point_detail(fail_details[ridx], pkey)
                if AMBIGUITY_RE.search(detail):
                    ambiguous_rounds.append((ridx + 1, detail))
        if ambiguous_rounds:
            llm_points[pkey] = ambiguous_rounds

    # ---- 组装 nodes ----
    nodes = []
    for seq in range(1, 15):
        sn = step_nodes[seq]
        node = {
            "id": f"n{seq}",
            "seq": seq,
            "kind": sn["kind"],
            "comment": STEP_COMMENTS[seq],
            "stability": sn["stability"],
        }
        if sn["is_map"]:
            node["type"] = "map"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["list_binding"] = sn["list_binding"]
            node["output_contract"] = sn["output_contract"]
            node["on_fail"] = "fallback"
        elif sn["is_branch"]:
            node["type"] = "branch"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["output_contract"] = sn["output_contract"]
            pkey_for_seq = None
            for pnum, seqs in GROUPS:
                if seq in seqs and seq == group_stats[f"p{pnum}"]["compare_seq"]:
                    pkey_for_seq = f"p{pnum}"
            node["verdict_point"] = pkey_for_seq
            if sn.get("struct_note"):
                node["note"] = sn["struct_note"]
            if pkey_for_seq in llm_points:
                node["predicate"] = "mechanical_signal_from_output(good_signal)"
                node["cases"] = {"good_signal": "PASS"}
                node["no_match"] = f"n{seq}_llm"
            else:
                node["predicate"] = "mechanical_signal_from_output(good_signal)"
                node["cases"] = {"good_signal": "PASS", "bad_signal": "FAIL"}
                node["no_match"] = "fallback"
        else:
            node["type"] = "tool"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["output_contract"] = sn["output_contract"]
            node["on_fail"] = "fallback"
        if sn.get("depends_on"):
            node["depends_on"] = sn["depends_on"]
        nodes.append(node)

    # 为需要 llm 判决的分支追加专门的 llm 节点
    llm_node_comments = {
        "p3": (
            "当 DB 侧与 git 侧的文件路径清单逐行 diff 结果非空时，机械规则(diff行数==0才PASS)"
            "无法区分'两侧文件集合真的不一致'与'仅因 git core.quotepath 对非ASCII文件名做"
            "八进制转义显示、或因此引起的排序位移导致的假阳性非零 diff'。"
            "10轮里出现2轮这类假阳性(round1 diff=10, round3 diff=4，fail_detail 都明确写着"
            "'Same N files present'即两侧文件集合相同)，故该分支移交模型判断，而不是写死"
            "'diff>0就FAIL'的机械规则。"
        ),
    }
    for pkey, ambiguous_rounds in llm_points.items():
        compare_seq = group_stats[pkey]["compare_seq"]
        llm_id = f"n{compare_seq}_llm"
        nodes.append({
            "id": llm_id,
            "type": "llm",
            "verdict_point": pkey,
            "comment": llm_node_comments.get(pkey, "该判决点在观测数据中出现语义歧义，需模型解读后再下结论"),
            "purpose": (
                f"解读 n{compare_seq} 的非零diff/异常输出，判断是否为真实数据不一致，"
                f"给出 {pkey} 的最终 PASS/FAIL 及理由"
            ),
            "context": [f"n{s}.output" for s in dict(GROUPS)[int(pkey[1:])]] + ["input.changeset_no"],
            "ambiguous_examples": [
                {"round": rnd, "fail_detail": detail} for rnd, detail in ambiguous_rounds
            ],
        })

    # ---- verdict_rules ----
    verdict_rules = []
    for pnum, seqs in GROUPS:
        pkey = f"p{pnum}"
        gs = group_stats[pkey]
        is_llm = pkey in llm_points
        rule = {
            "point": pkey,
            "depends_on": [f"n{s}" for s in seqs],
            "decision_node": f"n{gs['compare_seq']}",
            "stability": gs["agreement"],
        }
        if is_llm:
            rule["type"] = "llm"
            rule["comment"] = (
                f"{pkey} 的机械规则(n{gs['compare_seq']} 输出的好信号==PASS)在观测的 "
                f"{n} 轮里与实际判决 {gs['agreement']} 一致，但存在 "
                f"{len(llm_points[pkey])} 轮的 FAIL 是由编码/展示差异导致的假阳性"
                f"(fail_detail 明确写出两侧文件集合相同)。因此非零/异常分支移交 "
                f"n{gs['compare_seq']}_llm 模型判断，不写死机械 FAIL 规则。"
            )
            rule["mechanical_shortcut"] = f"n{gs['compare_seq']} 输出为 0-diff/好信号 时直接 PASS，无需模型"
            rule["llm_node"] = f"n{gs['compare_seq']}_llm"
        else:
            rule["type"] = "mechanical"
            rule["comment"] = (
                f"{pkey} 完全由 n{gs['compare_seq']} 的输出机械决定，{n} 轮观测 "
                f"{gs['agreement']} 一致，无反例，无需模型介入。"
            )
        verdict_rules.append(rule)

    graph = {
        "version": "v1",
        "flow": "changeset-audit",
        "input_vars": input_vars,
        "nodes": nodes,
        "verdict_rules": verdict_rules,
        "fallback": {
            "action": "spawn_model",
            "context": ["run_history", "current_node", "matched_trajectory_ref"],
        },
    }
    stats = {
        "group_stats": group_stats,
        "llm_points": llm_points,
        "step_nodes": step_nodes,
    }
    return graph, stats


def print_stats(rounds, graph, stats):
    n = len(rounds)
    node_types = {}
    for node in graph['nodes']:
        node_types[node['type']] = node_types.get(node['type'], 0) + 1
    print(f"轮次数: {n}")
    print(f"节点总数: {len(graph['nodes'])}  类型分布: {node_types}")
    print("每个 seq 的稳定性 (遮蔽后模板一致 n/N):")
    for seq in range(1, 15):
        sn = stats['step_nodes'][seq]
        unresolved = [k for k, v in sn['bindings'].items() if v.get('from') == 'UNRESOLVED']
        flag = f"  [UNRESOLVED: {unresolved}]" if unresolved else ""
        print(f"  seq{seq:>2} kind={sn['kind']:<8} stability={sn['stability']:<5} vars={list(sn['bindings'].keys())}{flag}")
    print("verdict_rules:")
    for rule in graph['verdict_rules']:
        print(f"  {rule['point']}: type={rule['type']} decision_node={rule['decision_node']} stability={rule['stability']}")
    unresolved_total = sum(
        1 for node in graph['nodes'] for b in node.get('bindings', {}).values() if b.get('from') == 'UNRESOLVED'
    )
    unresolved_total += sum(
        1 for node in graph['nodes']
        if node.get('list_binding', {}).get('from') == 'UNRESOLVED'
    )
    print(f"UNRESOLVED 绑定总数: {unresolved_total}")


# ===========================================================================
# 通用引擎 (适用于 changeset-audit 之外的任意流程)
#
# 与 changeset-audit 专属管线的核心区别: 判决点/分型/终判聚合这几类"决策类"节点
# 不再要求 kind=='compare'，而是通过"该步骤输出的某个字段值，在全部轮次都与
# verdict 里对应的值完全一致"这条统计校验来识别角色 —— 谁是判决点n的决策节点、
# 谁是分型节点、谁是终判聚合节点，全部由数据自己证明，而不是靠 kind 名字或位置。
# ===========================================================================

def classify_point_role(seq, rounds, point_keys):
    """若该 seq 输出字段里恰好命中一个判决点键，且该字段值在全部轮次都与
    verdict.points[该键] 完全一致，判定该 seq 是这个判决点的机械判决节点。"""
    fields0 = parse_fields(rounds[0]['steps'][seq]['output_digest'])
    hit = set(fields0.keys()) & set(point_keys)
    if len(hit) != 1:
        return None
    pkey = hit.pop()
    for r in rounds:
        f = parse_fields(r['steps'][seq]['output_digest'])
        if f.get(pkey) != r['verdict']['points'].get(pkey):
            return None
    return pkey


_WORD_RE = re.compile(r'\w+')


def classify_point_role_by_cmd_arg(seq, rounds, point_keys):
    """
    classify_point_role 的补充判据: 有些流程的判决脚本统一只输出一个通用字段名
    (例如 'verdict')，判决点的身份只体现在 cmd 的参数里 (例如
    "check.py r1 receipt.md" 里的 'r1')，而不是输出字段名本身。
    这种情况下: cmd 的参数整词命中且只命中一个判决点键，且该步骤输出的 'verdict'
    字段在 verdict.points[该键] 不是 N/A 的轮次里全部与之一致 (N/A 的轮次可能是被
    更上层的分型条件覆盖——那是"条件适用"的推导范围，不在这里校验，也不要求这个
    判决脚本自己也算出 N/A)，就判定该 seq 是这个判决点的决策节点。
    """
    cmd0 = rounds[0]['steps'][seq]['cmd']
    hit = set(_WORD_RE.findall(cmd0)) & set(point_keys)
    if len(hit) != 1:
        return None
    pkey = hit.pop()
    for r in rounds:
        top = r['verdict']['points'].get(pkey)
        if top == 'N/A':
            continue
        f = parse_fields(r['steps'][seq]['output_digest'])
        if f.get('verdict') != top:
            return None
    return pkey


def classify_point_role_by_digest_convention(seq, rounds, point_keys):
    """
    classify_point_role/classify_point_role_by_cmd_arg 的第三种补充判据，落地
    TRAJECTORY-CONTRACT.md 约定1: 判决步骤的 output_digest 必须含 'point=<判决点键>'
    与 'verdict=<PASS|FAIL|N/A|AMBIGUOUS>' 两个字段。

    这条判据专门覆盖前两种判据都找不到匹配的情况: 判决脚本是裸命令(不是
    "check.py <point> ..."这种以判决点键为cmd参数调用外部脚本的结构)，输出字段名
    (例如 tracked_dirty/markers_found/conflict_files)与判决点键(g1/g2/g3/...)既
    不同名、也不在cmd参数里出现——只要采集时遵守约定1，任何这样的流程都能被自动
    发现，不再需要为该流程写一张人工判决点映射表。

    与其它两种判据一样只做"发现"，不做语义解读: 'point' 字段值须在全部轮次一致
    (同一 seq 只能是同一个判决点)，'verdict' 字段(N/A 轮次跳过)须与该轮 verdict.
    points[pkey] 逐轮一致，全部满足才采信，否则返回 None(不强行凑数)。
    """
    fields0 = parse_fields(rounds[0]['steps'][seq]['output_digest'])
    pkey = fields0.get('point')
    if pkey not in point_keys:
        return None
    for r in rounds:
        f = parse_fields(r['steps'][seq]['output_digest'])
        if f.get('point') != pkey:
            return None
        top = r['verdict']['points'].get(pkey)
        if top == 'N/A':
            continue
        if f.get('verdict') != top:
            return None
    return pkey


def gap_fill_orphan_points(point_keys, claimed_seq_by_key, orphan_seqs):
    """
    有些判决点没有专属的决策步骤，而是直接由相邻的普通工具步骤隐式决定 (例如某流程
    的 e2/r2/r4 并没有自己的判决脚本调用，只是紧跟在对应的 grep 统计步骤后面，由
    外层聚合逻辑直接读取该步骤的计数值判断)。这里按 point_keys 的顺序，把两个相邻
    "已认领判决点"之间的未认领判决点，和同一 seq 区间内的孤儿工具步骤按出现顺序一一
    配对；配对不上(数量对不齐)的一律标记为待人工复核，不猜测映射关系。
    返回 (filled: {point_key: seq}, unresolved: [point_key, ...])。
    """
    filled = {}
    unresolved = []
    i = 0
    while i < len(point_keys):
        if point_keys[i] in claimed_seq_by_key:
            i += 1
            continue
        j = i
        while j < len(point_keys) and point_keys[j] not in claimed_seq_by_key:
            j += 1
        prev_seq = 0
        for p in point_keys[:i]:
            if p in claimed_seq_by_key:
                prev_seq = max(prev_seq, claimed_seq_by_key[p])
        next_seq = None
        for p in point_keys[j:]:
            if p in claimed_seq_by_key:
                next_seq = claimed_seq_by_key[p]
                break
        seqs_in_gap = [s for s in orphan_seqs if s > prev_seq and (next_seq is None or s < next_seq)]
        gap_keys = point_keys[i:j]
        if len(seqs_in_gap) == len(gap_keys):
            for key, seq in zip(gap_keys, seqs_in_gap):
                filled[key] = seq
        else:
            unresolved.extend(gap_keys)
        i = j
    return filled, unresolved


def classify_classification_role(seq, rounds, classification_fields):
    """若该 seq 输出的某个字段值，在全部轮次都与 verdict 里某个分型字段的值逐轮
    相等，判定该 seq 是该分型字段的判定节点 (字段名本身可以不同，例如输出字段叫
    'type' 而 verdict 分型字段叫 'doc_type'——匹配按值不按名，因此不写死名字)。"""
    fields0 = parse_fields(rounds[0]['steps'][seq]['output_digest'])
    for key in fields0:
        for cf in classification_fields:
            vals_step = [parse_fields(r['steps'][seq]['output_digest']).get(key) for r in rounds]
            vals_verdict = [str(r['verdict'].get(cf)) for r in rounds]
            if all(v is not None for v in vals_step) and vals_step == vals_verdict:
                return cf, key
    return None


def classify_aggregator_role(seq, rounds):
    """若该 seq 输出的某个字段值，在全部轮次都与 verdict['pass'] 逐轮相等
    (字符串化后比较)，判定该 seq 是终判聚合节点。"""
    fields0 = parse_fields(rounds[0]['steps'][seq]['output_digest'])
    for key in fields0:
        vals_step = [parse_fields(r['steps'][seq]['output_digest']).get(key) for r in rounds]
        vals_verdict = [str(r['verdict'].get('pass')) for r in rounds]
        if all(v is not None for v in vals_step) and vals_step == vals_verdict:
            return key
    return None


_LOGIC_TOKEN_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_LOGIC_RESERVED_TOKENS = {
    'AND', 'OR', 'NOT', 'else', 'all', 'any', 'True', 'False', 'PASS', 'FAIL', 'N', 'A',
    # 'head': 常见 shell 工具名(例如管道尾部的 "| head -20" 截断)，与"某步骤输出字段
    # 恰好也叫 head(例如 premerge-gate 记录 HEAD commit sha 的 head=<sha> 字段)"纯属
    # 词法巧合，不是真实的数据依赖引用。历史上曾在 premerge-gate.v1 图里造成 n6 的
    # 虚假 depends_on:n2(<日期> 人工审图发现并删除，见 premerge-gate.v4.json
    # revision_log v2)；这里在通用引擎里根治，不必每次重新归纳后再靠人工审图删一遍。
    'head',
}


def resolve_logic_depends_on(cmd_text, seq, rounds, point_decision_seq_by_key):
    """
    从 kind=='logic' 这类伪代码步骤的文本里抽取它引用的标识符，反查这些标识符最早
    由哪个更早步骤的输出字段产生，从而推出 depends_on。两级匹配:
    1) 标识符与某更早步骤的输出字段名字面相等 (例如 hash40_count / branch_hits)
    2) 标识符形如 has_X 且去掉 has_ 前缀后是某更早步骤输出字段名的子串 (例如
       has_cs_no -> cs_no -> cs_no_matches)，用于匹配"派生布尔量"这种自然语言命名
       (只在唯一命中时采信，出现多个候选时视为无法确定，不猜)
    另外: 标识符字面等于 'points' (verdict 判决点容器的字段名，这是轨迹格式本身的
    schema 约定，不是某个流程的业务内容) 时，展开为全部已识别判决点的决策节点
    (用于终判聚合步骤这种"依赖全部判决点"的情况)。
    找不到匹配的标识符静默跳过——logic 节点不做逐变量绑定校验，只做尽力而为的依赖
    推断，这与 changeset-audit 里 n14/n9b 只有 depends_on、没有逐变量 bindings 的
    先例是一致的。
    """
    field_origin = {}
    for s in range(1, seq):
        for k in parse_fields(rounds[0]['steps'][s]['output_digest']):
            field_origin.setdefault(k, s)

    deps = set()
    for tok in _LOGIC_TOKEN_RE.findall(cmd_text):
        if tok in _LOGIC_RESERVED_TOKENS:
            continue
        if tok == 'points':
            deps.update(point_decision_seq_by_key.values())
            continue
        if tok in field_origin:
            deps.add(field_origin[tok])
            continue
        if tok.startswith('has_'):
            stripped = tok[len('has_'):]
            matches = {s for k, s in field_origin.items() if stripped and stripped in k}
            if len(matches) == 1:
                deps.add(matches.pop())
    return sorted(deps)


def find_na_condition(point_key, rounds, classification_fields, classification_values):
    """
    在判决点存在 N/A 轮次时，尝试从分型字段里找到与 N/A 完全相关的取值: 只要 N/A 轮次
    的取值集合与非 N/A 轮次的取值集合完全不相交 (无论各自集合有几个不同取值)，就认为
    找到完美相关——例如 N/A 恰好发生在 doc_type∈{process_log, nondelivery} 的轮次，
    非 N/A 恰好发生在 doc_type=code_delivery 的轮次，两边不相交即可采信，不要求
    "N/A 侧只能有一个取值"这么严格。找不到不相交的字段时诚实返回 unresolved 标记，
    交由人工复核，不瞎猜。
    """
    na_idx = [i for i, r in enumerate(rounds) if r['verdict']['points'][point_key] == 'N/A']
    if not na_idx:
        return None
    non_na_idx = [i for i in range(len(rounds)) if i not in na_idx]
    for cf in classification_fields:
        vals = classification_values[cf]
        na_vals = {vals[i] for i in na_idx}
        non_na_vals = {vals[i] for i in non_na_idx}
        if not (na_vals & non_na_vals):
            return {
                "field": cf,
                "na_when": sorted(na_vals),
                "applicable_when": sorted(non_na_vals),
            }
    return {"unresolved": True, "na_rounds": [i + 1 for i in na_idx]}


def build_step_node_generic(seq, rounds, known_values_per_round, point_keys, role):
    """
    构建单个 seq 对应的节点信息 (通用引擎)。role 由调用方 (build_graph_generic 的
    第一遍扫描) 预先算好传入: None 表示普通工具/循环节点; ('point', key) /
    ('classification', field, own_key) / ('aggregate', own_key) 表示决策类节点。
    """
    kind = rounds[0]['steps'][seq]['kind']
    raw_cmds = [r['steps'][seq]['cmd'] for r in rounds]
    raw_outputs = [r['steps'][seq]['output_digest'] for r in rounds]
    is_map = bool(LOOP_PLACEHOLDER_RE.search(raw_cmds[0]))

    # 遮蔽: 对全部节点一视同仁，无论是不是判决/分型/聚合角色。changeset-audit 的
    # exec:logic 节点 (如 n14) cmd 是纯伪代码、不含任何轮次相关值，遮蔽后自然原样
    # 不变；但很多流程 (例如 receipt-compliance) 的判决脚本调用本身就是真实可执行
    # 命令且直接引用了输入变量 (例如 "check.py r1 <receipt文件名>")，同样需要遮蔽
    # 才能得到跨轮稳定的模板和正确的 bindings，不能因为它"顺便也是判决点"就跳过。
    # 复用 mask_and_extract 做结构性遮蔽，再叠加一层"已知输入变量字面值"的通用遮蔽——
    # 把当前流程自动发现的每个输入变量在这一轮的实际字符串值，作为字面子串去匹配并
    # 替换成 {var}。这是取代 changeset-audit 专属 CSNO_RE (写死"变更单号"这个*形状*)
    # 的通用做法：不关心变量的形状，只要知道它在 input 行里的值，就能把它从 cmd 文本
    # 里认出来，对任何流程都适用。兜底再试一次"取 basename"：有的流程 cmd 里只引用
    # 输入变量的文件名部分而非完整路径 (例如 receipt-compliance 的 check.py 调用只
    # 传 basename)，命中的还是同一个变量、同一个完整值，绑定按完整值归因不受影响。
    masked_list = []
    slots_list = []
    for ridx, cmd in enumerate(raw_cmds):
        masked, slots, _structural_vars = mask_and_extract(cmd, kind)
        seen = {name for name, _ in slots}
        round_input = rounds[ridx]['input']
        for var in round_input:
            if var in ('type', 'ts') or var in seen:
                continue
            val = round_input[var]
            if not isinstance(val, str) or not val:
                continue
            if val in masked:
                masked = masked.replace(val, '{' + var + '}')
                slots.append((var, val))
                seen.add(var)
                continue
            base = os.path.basename(val)
            if base and base != val and base in masked:
                masked = masked.replace(base, '{' + var + '}')
                slots.append((var, val))
                seen.add(var)
        masked_list.append(masked)
        slots_list.append(slots)

    # 变量名跨轮不一致时的兜底: basename 兜底匹配是"巧合子串命中"，同一个输入变量在
    # 不同样本里可能因为路径结构不同(例如混入了自建 fixture，其 git-dir 是相对路径
    # ".git"，不像真实 linked worktree 的 git-dir 那样天然嵌有 worktree 目录名)导致
    # 有的轮次命中、有的轮次不命中。这种不一致不该让整个归纳中止——只保留全部轮次都
    # 一致探测到的变量(交集)，命中不齐的变量放弃遮蔽、原样回退成字面值(不强行绑定，
    # 也不假装能通用参数化)，让该节点如实呈现较低的模板稳定性，而不是报错崩溃。
    common_names = {name for name, _ in slots_list[0]}
    for sl in slots_list[1:]:
        common_names &= {name for name, _ in sl}
    if any({name for name, _ in sl} != common_names for sl in slots_list):
        for ridx in range(len(slots_list)):
            kept = []
            for name, val in slots_list[ridx]:
                if name in common_names:
                    kept.append((name, val))
                else:
                    masked_list[ridx] = masked_list[ridx].replace('{' + name + '}', val)
            slots_list[ridx] = kept

    n_stable = sum(1 for m in masked_list if m == masked_list[0])

    var_names = [name for name, _ in slots_list[0]]
    for sl in slots_list[1:]:
        this_names = [name for name, _ in sl]
        if this_names != var_names:
            raise SystemExit(f"seq{seq}: 各轮变量槽位不一致 {var_names} vs {this_names}")

    bindings = {}
    used_origins = set()
    for var in var_names:
        per_round_values = {ridx: dict(sl)[var] for ridx, sl in enumerate(slots_list)}
        binding = resolve_binding(var, per_round_values, known_values_per_round, exclude=used_origins)
        bindings[var] = binding
        if binding.get("from") not in (None, "UNRESOLVED"):
            used_origins.add(binding["from"])

    contract = infer_contract(raw_outputs)

    for ridx, r in enumerate(rounds):
        out = r['steps'][seq]['output_digest']
        fields = parse_fields(out)
        for k, v in fields.items():
            known_values_per_round[ridx].setdefault(v, []).append(f"n{seq}.output.{k}")

    list_binding = None
    if is_map:
        placeholder_desc = LOOP_PLACEHOLDER_RE.search(raw_cmds[0]).group(1)
        for ridx in range(len(rounds)):
            found = None
            for s in range(1, seq):
                out = rounds[ridx]['steps'][s]['output_digest']
                if placeholder_desc in out:
                    found = f"n{s}.output"
            if found is None:
                list_binding = {"from": "UNRESOLVED", "detail": f"round {ridx+1}: 未找到含 '{placeholder_desc}' 描述的更早步骤输出"}
                break
            if list_binding is None:
                list_binding = {"from": found}
            elif list_binding.get("from") != found:
                list_binding = {"from": "UNRESOLVED", "detail": "各轮匹配到不同的来源步骤"}
                break

    # 判决/分型/聚合类节点: depends_on 另由 resolve_logic_depends_on 在调用方补算(它
    # 扫描的是这里算出的 masked cmd_template，用于发现跨步骤的字段名引用；本流程这类
    # 判决脚本大多是自包含的单步调用，不引用其它步骤输出字段，扫描结果为空是符合实际
    # 的，不是 bug)。is_map 与 role 在这里正交保留、不互斥: 绝大多数流程的判决脚本步骤
    # 本身不是循环(is_map 恒为 False)，但 premerge-gate 的 g4 是个例外——它的循环步骤
    # (seq9)自身聚合输出就直接是判决点的值，需要同时携带 map 的 list_binding 和 role
    # 的判决点信息，因此不能像老代码那样在 role is not None 时把 is_map 强制写死成
    # False、list_binding 直接丢弃(那样会让 seq9 在图里既不是 map 节点也没有列表来源)。
    return {
        "seq": seq, "kind": kind, "is_map": is_map, "role": role,
        "cmd_template": masked_list[0], "stability": f"{n_stable}/{len(rounds)}",
        "bindings": bindings, "list_binding": list_binding,
        "output_contract": contract, "raw_outputs": raw_outputs,
    }


def auto_comment(sn):
    """任意流程的通用兜底注释 (没有专属 STEP_COMMENTS 表时使用)。"""
    role = sn.get("role")
    seq = sn["seq"]
    if role and role[0] == 'classification':
        return f"分型判断步骤(seq{seq})：依据前序检索/统计结果计算文档或数据的类型，决定后续判决点是否适用"
    if role and role[0] == 'point':
        return f"判决点{role[1]}的机械判决步骤(seq{seq})：依据前序统计结果计算该判决点的 PASS/FAIL/N-A"
    if role and role[0] == 'aggregate':
        return f"终判汇总步骤(seq{seq})：汇总全部判决点计算整体是否合规"
    if sn.get("is_map"):
        return f"对列表逐项执行相同检查的循环步骤(seq{seq})"
    return f"工具调用步骤(seq{seq})，kind={sn['kind']}：对输入执行检索/统计并产出结构化结果"


#: 机械判决的合法终值。观测到的判决点取值只要不是这两个 (也不是 N/A)，就说明
#: 产生这个值的判决脚本自己也没能力给出确定的 PASS/FAIL —— 这类"第三态"终值
#: (例如 AMBIGUOUS/DEFER 等，命名不写死) 一律自动路由到 llm 兜底节点，不由本归纳器
#: 杜撰机械规则去猜它该算 PASS 还是 FAIL。
_MECHANICAL_TERMINALS = {'PASS', 'FAIL'}


def _fill_point_branch_fields(node, pkey, rounds, cond):
    """
    填充判决点决策节点的 predicate/cases。cond 是调用方预先算好的
    find_na_condition 结果 (或 None)——N/A 条件适用是"判决点"层面的事实，统一在
    build_graph_generic 里对全部判决点算一次，这里只管消费。
    - 观测到的终值只要是 PASS/FAIL，直接写成机械 case。
    - 观测到其它任何非 PASS/FAIL/N/A 的终值 (例如 AMBIGUOUS)，一律记入 cases 并路由到
      llm 兜底节点——这是判决脚本自己产出的"第三态"，不是本归纳器强行分出来的桶。
    返回 llm_values: 需要 llm 兜底的终值列表 (可能为空)。
    """
    node["predicate"] = "mechanical_signal_from_own_output(point_key)"
    observed = sorted({r['verdict']['points'][pkey] for r in rounds})
    cases = {}
    llm_values = []
    for v in observed:
        if v == 'N/A':
            continue
        if v in _MECHANICAL_TERMINALS:
            cases[v] = v
        else:
            llm_values.append(v)
    if cond or 'N/A' in observed:
        cases['N/A'] = 'N/A'
    node["cases"] = cases
    node["no_match"] = "fallback"
    return llm_values


def _build_generic_llm_node(llm_id, pkey, decision_seq, rounds, llm_values):
    """
    通用 llm 兜底节点: 收集判决脚本自己判定为"第三态"(llm_values 里任一取值) 的样本轮次，
    只用轨迹里已有的 output_digest/verdict 字段拼 ambiguous_examples，不读取原始文档/
    回执内容。这套机制对任意流程都适用: 只要判决脚本自己产出了 PASS/FAIL/N-A 之外的
    终值，就说明该脚本自身也认为这类样本无法自行裁定，理应移交模型/人工复核。
    """
    values_set = sorted(set(llm_values))
    examples = []
    for ridx, r in enumerate(rounds):
        val = r['verdict']['points'].get(pkey)
        if val in llm_values:
            examples.append({
                "round": ridx + 1,
                "input": {k: v for k, v in r['input'].items() if k not in ('type', 'ts')},
                "step_output": r['steps'][decision_seq]['output_digest'],
                "mechanical_verdict": val,
                "fail_detail": r['verdict'].get('fail_detail'),
            })
    return {
        "id": llm_id,
        "type": "llm",
        "verdict_point": pkey,
        "comment": (
            f"判决点 {pkey} 的机械判决脚本(n{decision_seq})自己在观测样本里就产出过 {values_set} "
            f"这类非 PASS/FAIL/N-A 的终值——这不是本归纳器强行分出来的桶，是轨迹数据本身记录的"
            f"脚本自我判断结果，说明这类样本连机械脚本自己都认为无法直接裁定，需要移交模型/人工"
            f"复核，而不是由本归纳器杜撰一条机械规则把它们并入 PASS 或 FAIL。"
        ),
        "purpose": f"解读 n{decision_seq} 输出为 {values_set} 的样本，判断 {pkey} 的最终 PASS/FAIL，并给出理由",
        "context": [f"n{decision_seq}.output"],
        "ambiguous_examples": examples,
    }


# ---------------------------------------------------------------------------
# receipt-compliance 专属域标注: 中文节点注释 (人工阅读12轮真实轨迹后确定，与
# changeset-audit 的 STEP_COMMENTS 是同一性质的一次性标注成本) + r6 的 llm 兜底
# 覆写 (人工审阅回执原文后确认的语义边界，不是从12轮统计里能直接推出的反例，见
# RECEIPT_R6_RULE_COMMENT 里的详细说明)。
# ---------------------------------------------------------------------------

RECEIPT_STEP_COMMENTS = {
    1: (
        "分型判断(t0)：综合文件名是否符合变更单号模式(name_pattern)、正文是否含变更单号(has_cs_no)、"
        "是否含改动文件清单表(has_file_table)、是否含验收/判决小节(has_verdict_section)，计算出"
        "type ∈ {process_log(过程记录/非回执类文档), nondelivery(非交付类回执，如请示/说明), "
        "code_delivery(代码交付类回执)}。分型结果决定 r1-r6 各点是否适用(process_log 全部不适用，"
        "nondelivery 下 r2/r3/r4 不适用)"
    ),
    2: (
        "判决点e1：统计某类不应出现的模式命中次数(hits)，hits=0 时PASS(与doc_type无关，各类回执均适用)。"
        "该步骤调用外部判决脚本 check.py，脚本内部具体识别规则未纳入本次归纳范围"
    ),
    3: "统计回执文本中CR(\\r)字符出现次数，供判决点e2使用——e2 未见专属决策步骤，由本步骤的计数值隐式决定(见下方 needs_review 标注)",
    4: "判决点r1：状态字段(status_found)存在且无含糊措辞(hedge_found)-> PASS，否则 FAIL(与doc_type无关，两类回执均适用)",
    5: "统计回执中出现的40位十六进制commit hash个数，供判决点r2使用——r2 未见专属决策步骤，由本步骤的计数值隐式决定(见下方 needs_review 标注)",
    6: "判决点r3(仅code_delivery适用)：同时命中分支(has_branch)与工作树(has_worktree)关键字 -> PASS，否则 FAIL；process_log/nondelivery类型记N/A",
    7: "统计回执中出现的具体代码文件路径个数，供判决点r4使用——r4 未见专属决策步骤，由本步骤的计数值隐式决定(见下方 needs_review 标注)",
    8: "判决点r5：存在代码围栏(has_fence)或图片/截图引用(has_image)之一 -> PASS，否则 FAIL(与doc_type无关，两类回执均适用)",
    9: (
        "判决点r6：检测是否命中打回/退回/整改类小节标题(trigger)，未命中 -> N/A；命中后进一步检查该"
        "小节内是否给出新的commit哈希(has_sha)或提交引用(has_commit_ref)——脚本自身在两者皆无时直接"
        "判定 AMBIGUOUS(而不是强行判FAIL)，这是判决脚本自带的'说不准就交出来'终值，不是本归纳器"
        "分出来的桶。样本CS-<日期>-REVASSOC(round6)命中trigger但缺锚点，脚本判AMBIGUOUS，见"
        "n9_llm 兜底节点"
    ),
}


# ---------------------------------------------------------------------------
# premerge-gate 专属域标注: 中文节点注释(人工阅读轨迹后确定，与 RECEIPT_STEP_
# COMMENTS/changeset-audit 的 STEP_COMMENTS 是同一性质的一次性标注成本，纯文档，
# 不影响判决逻辑)。
#
# 历史债务(TRAJECTORY-CONTRACT.md 技术债章节)已还清: 原来这里还有一张人工判决点
# 映射表(PREMERGE_GATE_POINT_SEQ)和一张"样本内从未观测到FAIL"的诚实标注表
# (PREMERGE_GATE_UNVERIFIED_FAIL)，因为旧版23轮轨迹的判决步骤 digest 只有统计值
# 没有判决信号(例如 "tracked_dirty=0 untracked=3 conflict=0")，g1/g2/g3 又零FAIL
# 反例，两种自动判据(classify_point_role/classify_point_role_by_cmd_arg)都找不到
# 判决点与步骤的关联，只能人工核对"流程背景"写死映射表。
# 按约定1重采(digest 补 point=/verdict= 字段)、补充5个自建病态fixture反例后，
# 第三种自动判据 classify_point_role_by_digest_convention 能直接从数据里读出
# 判决点与步骤的关联，g1/g2/g3 也有了真实FAIL反例，人工映射表和诚实标注表均已
# 删除，不再需要 flow_name=='premerge-gate' 的特判。
PREMERGE_GATE_STEP_COMMENTS = {
    1: "查询 worktree 当前所在分支名(branch)，供绑定归因和一致性核对使用",
    2: "查询 worktree 当前 HEAD commit sha(head)，供绑定归因和一致性核对使用",
    3: (
        "判决点g1(树干净)：git status --porcelain 统计已跟踪脏改动(tracked_dirty)/未跟踪"
        "(untracked)/冲突(conflict)行数——conflict>0(冲突码 UU/AA/DD/AU/UA/UD/DU)或 "
        "tracked_dirty>0(非'??'的已跟踪脏改动) -> FAIL，否则(仅'??'或空) -> PASS。"
        "digest 按约定1携带 point=g1/verdict= 字段，判决点与步骤的关联由数据自动定位"
    ),
    4: "查询该 worktree 对应的 git 目录路径(git_dir)，供 seq5 中态标记检查拼接标记文件路径使用",
    5: (
        "判决点g2(git中态)：检查 CHERRY_PICK_HEAD/MERGE_HEAD/REBASE_HEAD/BISECT_LOG/"
        "rebase-merge/rebase-apply 任一标记文件是否存在于 git-dir 下，任一存在 -> FAIL，"
        "全部不存在 -> PASS。digest 按约定1携带 point=g2/verdict= 字段"
    ),
    6: (
        "判决点g3(冲突标记)：git grep 冲突标记(^<<<<<<<|^>>>>>>>|^=======)有命中 -> FAIL，"
        "无命中 -> PASS。digest 按约定1携带 point=g3/verdict= 字段"
    ),
    7: "相对 feat/<项目>-integration 取该 worktree HEAD 的合并基点(merge_base)，供 seq8 改动清单计算使用",
    8: "相对 merge-base 用 git diff --name-only 取改动文件清单及计数(changed_files)，供 seq9 逐文件行尾检查的列表来源",
    9: (
        "判决点g4(行尾)：对 seq8 改动清单中的每个文件在 HEAD 下取内容，统计 CR(\\r) 出现次数，"
        "任一文件 CR>0 -> FAIL，全部为0 -> PASS。既是逐文件循环的 map 节点，其自身聚合结果"
        "(digest 里 point=g4/verdict= 字段)同时就是g4的机械判决来源，与 changeset-audit 把"
        "'循环(seq13)'和'聚合判决(seq14)'拆成两个独立步骤不同"
    ),
}


# ---------------------------------------------------------------------------
# cleanup-audit 专属域标注: 中文节点注释(人工阅读18轮真实轨迹后确定，与
# PREMERGE_GATE_STEP_COMMENTS/RECEIPT_STEP_COMMENTS/changeset-audit 的 STEP_COMMENTS
# 是同一性质的一次性标注成本，纯文档，不影响判决逻辑)。这是第一个从采集起就全程遵守
# TRAJECTORY-CONTRACT.md 全部约定的流程：判决步骤 digest 全带 point=/verdict= 字段，
# c1-c5 全部由 classify_point_role_by_digest_convention 自动定位，无需人工判决点映射表；
# 阈值(stale_days/runtime_hours/backlog_threshold)全部参数化，c1/c4/c5 均有真实
# FAIL/PASS 两态反例，UNRESOLVED 绑定 0，无 needs_review。
# ---------------------------------------------------------------------------
CLEANUP_AUDIT_STEP_COMMENTS = {
    1: (
        "环境铺垫/观测记录(非判决步骤)：本轮实际执行的环境操作叙述(input.mutation 字段)，"
        "18轮内容各不相同(建库/建账号/起停端口进程/纯观测)，是采集时为了可复现而记录的"
        "fixture 铺垫动作，不是'收尾体检'本身的判决步骤——真正的5个判决点(c1-c5)在下面"
        "n2-n6。生产复放时本节点按 NO-OP 处理，不执行任何命令"
    ),
    2: (
        "判决点c1(临时库残留)：库名匹配 prefix_re 且无活跃会话(active_sessions=0)且实际"
        "滞留天数 > stale_days -> FAIL，否则(不匹配/有活跃会话/未超期) -> PASS。告警须带"
        "三要素：活跃连接数(active_sessions)/实际滞留天数(actual_stale_days)/反查出的"
        "配对授权账号(paired_grant_account，来自本节点自身第二段 mysql.db 查询，无需"
        "额外查询)。stale_days 参数化：自检模式用严阈值(如2天甚至0天)快速取得FAIL态，"
        "巡检模式可放宽阈值扫描全局积压"
    ),
    3: (
        "判决点c2(临时账号残留)：账号名匹配 prefix_re 且(全部授权指向的库都不存在 或 "
        "无任何授权) -> FAIL；至少一个授权库存在(在用) -> PASS"
    ),
    4: (
        "判决点c3(僵尸授权)：mysql.db 授权(Db,User)指向的库在 information_schema."
        "SCHEMATA 里不存在 -> 该条授权判为僵尸 -> FAIL。全局扫描、不按 prefix_re 过滤"
        "(僵尸授权对任何库都是风险，不限于自建前缀)；digest 里的 scope= 字段只回显当前"
        "prefix_re 输入作审计留痕，不是过滤条件"
    ),
    5: (
        "判决点c4(隔离实例残留)：监听端口落在 port_lo-port_hi 段 且 运行时长 > "
        "runtime_hours，且先过豁免名单(${PORT_APP}/${PORT_WEB}/8793/8794/${PORT_PANEL}/8777 永不入判，"
        "命中即跳过、不论运行时长多长) -> FAIL。告警须带三要素：端口/进程名/运行时长。"
        "port_lo/port_hi 支持单区间(巡检收窄验证用)或并行多区间(生产 java 段+node 段"
        "并行)"
    ),
    6: (
        "判决点c5(worktree积压)：相对 feat/<项目>-integration 零diff的可回收 worktree "
        "数 >= backlog_threshold -> FAIL，否则 PASS。backlog_threshold 参数化：自检"
        "模式放宽阈值可验证通过态(生产真实积压数不会因验证而被误判)，巡检模式用生产"
        "实际阈值"
    ),
}


# ---------------------------------------------------------------------------
# 分型 criteria 叙述文案 / 生成用 revision_log: 按流程注册的领域叙述文本，用字典
# 查表(dict.get(flow_name))代替按流程名逐一条件分支——性质与 STEP_COMMENTS 相同
# (一次性人工标注成本，不含决策逻辑)，只是换了种不需要逐个判断流程名的写法。
# ---------------------------------------------------------------------------
_CLASSIFICATION_CRITERIA_BY_FLOW = {
    'receipt-compliance': (
        "由 n{seq}(t0) 内部综合 name_pattern/has_cs_no/has_file_table/has_verdict_section 计算，"
        "具体布尔组合未在轨迹内单独暴露(判决脚本内部逻辑)，只能确认三种取值与最终 r1-r6 的适用"
        "范围强相关(process_log 时 r1-r6 全部N/A；nondelivery 时仅 r2/r3/r4 记N/A；code_delivery "
        "时全部适用)，具体分型布尔式建议人工核实 check.py 源码"
    ),
}

# ---------------------------------------------------------------------------
# 顶层 conventions 段(通用机制): 按流程注册"给人看的使用须知"(只读性质/多模态用法/
# 安全约束这类不属于任何单个节点、面向整张图的说明)，同样是数据表查表、不含判决逻辑，
# 未注册的流程不产出此段(graph.get('conventions') 为 None，不影响既有3条流程)。
# ---------------------------------------------------------------------------
_CONVENTIONS_BY_FLOW = {
    'cleanup-audit': {
        "readonly": (
            "本图全部判决步骤(c1-c5)对生产资源只读——docker exec mysql 只执行 SELECT，"
            "lsof/ps 只查系统状态，git worktree/merge-base/diff 只读枚举，均不执行任何"
            "写入/删除/授权变更。清理动作(DROP DATABASE/DROP USER/kill进程/worktree "
            "remove)不在本图范围内，需要人工或另一条流程在看到本图 FAIL 后决策执行"
        ),
        "dual_mode": (
            "同一张图靠输入参数支持两种用法：① 自检模式——prefix_re 限自建前缀"
            "(如本会话临时资源前缀)+ 严阈值(stale_days/runtime_hours 调小甚至为0)，"
            "会话收尾时自查有没有忘记清理；② 巡检模式——prefix_re 覆盖全部约定前缀"
            "(如 e2e|drill|tmp|test)+ 宽阈值，定期扫描全局积压。两种模式共用完全相同"
            "的判决逻辑，仅输入参数不同，不需要为巡检模式另开一张图"
        ),
        "no_bare_grep": (
            "存在性检查一律用 /usr/bin/grep 绝对路径或 Python 正则实现，正则禁用 "
            "\\w/\\d/\\s(本机交互 shell 的 grep 是 ugrep 函数包装，与 /bin/bash -c 子"
            "进程里的系统 grep 行为不一致)；git grep 是 git 子命令例外，不受影响"
        ),
        "port_exempt": (
            "c4 豁免名单：${PORT_APP}(ERP后端)/${PORT_WEB}(常驻前端)/8793/8794(<项目>MCP桥)/"
            "${PORT_PANEL}/8777(报告服务)——命中这些端口永不入判，不论运行时长多长；豁免判断"
            "先于运行时长阈值比较执行，即使 runtime_hours=0 这类最严阈值也不例外"
        ),
    },
}

_REVISION_LOG_BY_FLOW = {
    'receipt-compliance': [{
        "rev": "v1",
        "date": "<日期>",
        "by": "通用化归纳器(inducer/induce.py 通用引擎)自动产出",
        "changes": (
            "对 receipt-compliance 当前 12 轮轨迹(9步/轮，check.py 判决脚本结构)归纳产出。"
            "t0 分型(process_log/nondelivery/code_delivery)与 r1-r6 的条件适用均由算法从数据"
            "统计发现(N/A 与 doc_type 的相关性在样本内无反例)；e2/r2/r4 没有专属判决步骤，由"
            "算法按结构位置从相邻孤儿工具步骤补齐，具体判决门槛未经数据统计验证，标记"
            "needs_review；r6 判决脚本自身会产出 AMBIGUOUS 终值(样本 CS-<日期>-REVASSOC 命中)，"
            "已按通用规则自动路由到 llm 兜底节点，不属于本归纳器手工覆写。"
        ),
    }],
    'cleanup-audit': [{
        "rev": "v1",
        "date": "<日期>",
        "by": "通用化归纳器(inducer/induce.py 通用引擎)自动产出",
        "changes": (
            "对 cleanup-audit 当前18轮轨迹(6步/轮：1个环境铺垫步骤 + c1-c5五个判决步骤)"
            "归纳产出。这是第一个从采集起就全程遵守 TRAJECTORY-CONTRACT.md 全部约定的"
            "流程：判决步骤 digest 全带 point=/verdict= 字段(约定1)，c1-c5 全部由"
            "classify_point_role_by_digest_convention 自动定位，无需人工判决点映射表；"
            "阈值(stale_days/runtime_hours/backlog_threshold)全部参数化(约定7)，c1/c4"
            "各有真实 FAIL/PASS 两态反例(约定5)，c5 在 backlog_threshold=100 时观测到"
            "真实 PASS 态；UNRESOLVED 绑定 0，无 needs_review。运行器(runner/run_graph.py"
            "的 CleanupAuditRunner)fixture 验证额外发现并修复一个真实 bug：mysql.db.Db "
            "列存的是 LIKE 模式，字面下划线会被 MySQL 转义(且 mysql 客户端输出时又将反斜杠"
            "再转义一次)，不还原会把几乎任何正常库名(如 ${DB_NAME})误判成僵尸授权/"
            "目标不存在，已在 unescape_mysql_db_pattern 中修复。"
        ),
    }],
}


def _build_classification_section(field, seq, own_key, classification_values, verdict_rules, flow_name):
    values = sorted(set(classification_values[field]))
    gates = {}
    for rule in verdict_rules:
        aw = rule.get("applicable_when")
        if aw and field in aw:
            gates[rule["point"]] = aw[field]
    section = {
        "field": field,
        "decision_node": f"n{seq}",
        "own_output_key": own_key,
        "values": values,
        "gates": gates,
    }
    criteria_tpl = _CLASSIFICATION_CRITERIA_BY_FLOW.get(flow_name)
    if criteria_tpl:
        section["criteria"] = criteria_tpl.format(seq=seq)
    return section


def build_graph_generic(rounds, flow_name):
    n = len(rounds)
    input_vars = discover_input_vars(rounds)
    point_keys = discover_verdict_points(rounds)
    classification_fields = discover_classification_fields(rounds)
    n_steps = len(rounds[0]['steps'])

    known_values_per_round = [dict() for _ in range(n)]
    for ridx, r in enumerate(rounds):
        for var in input_vars:
            known_values_per_round[ridx][str(r['input'][var])] = [f"input.{var}"]

    # 第一遍: 角色识别 (map 节点直接跳过角色判断，与 changeset-audit 精神一致)。
    # 判决点决策节点先按两种判据识别: (a) 输出字段名直接就是判决点键 (老式)
    # (b) cmd 参数整词命中判决点键、输出用通用 'verdict' 字段 (新式，见
    # classify_point_role_by_cmd_arg)。既不是判决点、也不是分型/聚合节点的普通
    # 步骤记入 plain_tool_seqs，供随后 gap_fill_orphan_points 尝试补齐"没有专属
    # 决策步骤、由相邻工具步骤隐式决定"的判决点。
    point_decision_seq_by_key = {}
    classification_seq = None
    classification_field = None
    classification_own_key = None
    aggregate_seq = None
    role_by_seq = {}
    plain_tool_seqs = []
    for seq in range(1, n_steps + 1):
        is_loop = bool(LOOP_PLACEHOLDER_RE.search(rounds[0]['steps'][seq]['cmd']))
        # 判决点识别放在 map/循环判断之前: 绝大多数流程的判决脚本步骤本身不是循环，
        # 但有的流程(例如 premerge-gate 的 g4)循环步骤自身的聚合输出就直接是判决点
        # 的值——这类"既是循环、又是判决点"的步骤如果先被 is_loop 短路成 None，就永远
        # 没有机会被识别为判决点，只能靠人工映射表硬编码。三种判据依次尝试: (a) 输出
        # 字段名直接就是判决点键 (b) cmd 参数整词命中判决点键 (c) digest 遵守
        # TRAJECTORY-CONTRACT.md 约定1 的 point=/verdict= 字段(见
        # classify_point_role_by_digest_convention)。
        pkey = (
            classify_point_role(seq, rounds, point_keys)
            or classify_point_role_by_cmd_arg(seq, rounds, point_keys)
            or classify_point_role_by_digest_convention(seq, rounds, point_keys)
        )
        if pkey:
            point_decision_seq_by_key[pkey] = seq
            role_by_seq[seq] = ('point', pkey)
            continue
        if is_loop:
            role_by_seq[seq] = None
            continue
        crole = classify_classification_role(seq, rounds, classification_fields)
        if crole:
            classification_seq = seq
            classification_field, classification_own_key = crole
            role_by_seq[seq] = ('classification', crole[0], crole[1])
            continue
        akey = classify_aggregator_role(seq, rounds)
        if akey:
            aggregate_seq = seq
            role_by_seq[seq] = ('aggregate', akey)
        else:
            role_by_seq[seq] = None
            plain_tool_seqs.append(seq)

    implicit_points, gap_unresolved = gap_fill_orphan_points(point_keys, point_decision_seq_by_key, plain_tool_seqs)
    for pkey, seq in implicit_points.items():
        point_decision_seq_by_key[pkey] = seq
        plain_tool_seqs.remove(seq)
        # 注意: role_by_seq[seq] 保持 None —— 这类步骤本身仍是普通工具节点(grep等)，
        # 走正常的 tool 节点构建路径(有自己的 bindings/output_contract)，只是额外在
        # verdict_rules/node 里被标注为某判决点的隐式决策来源(见下方 needs_review)。

    missing_points = [p for p in point_keys if p not in point_decision_seq_by_key]
    if missing_points:
        raise SystemExit(
            f"未能为判决点 {missing_points} 找到对应的决策步骤(既没有专属决策节点，也无法用结构性"
            f"补齐规则从孤儿工具步骤配齐)，需要人工核对轨迹结构。补齐失败详情: {gap_unresolved}"
        )

    # 第二遍: 构建全部节点
    step_nodes = {}
    for seq in range(1, n_steps + 1):
        step_nodes[seq] = build_step_node_generic(seq, rounds, known_values_per_round, point_keys, role_by_seq[seq])

    # 补充 depends_on 推断 (logic 角色节点专属)
    for seq, sn in step_nodes.items():
        if sn["role"] is not None:
            sn["depends_on"] = [f"n{s}" for s in resolve_logic_depends_on(
                sn["cmd_template"], seq, rounds, point_decision_seq_by_key,
            )]
        else:
            sn.setdefault("depends_on", [])

    classification_values = {
        cf: [str(r['verdict'].get(cf)) for r in rounds] for cf in classification_fields
    }

    # N/A 条件适用: 对全部判决点统一计算 (不管它的决策节点是 logic 步骤还是结构位置
    # 补齐的普通工具步骤)，因为"是否被分型条件门控"是判决点层面的事实，与决策节点
    # 本身是什么类型的步骤无关——e2/r2/r4 虽然没有专属判决步骤，但 r2/r4 的 N/A 一样
    # 与 doc_type 完全相关，不能因为决策节点是普通工具步骤就漏掉这条门控关系。
    na_conditions = {pkey: find_na_condition(pkey, rounds, classification_fields, classification_values) for pkey in point_keys}

    flow_step_comments = {
        'receipt-compliance': RECEIPT_STEP_COMMENTS,
        'premerge-gate': PREMERGE_GATE_STEP_COMMENTS,
        'cleanup-audit': CLEANUP_AUDIT_STEP_COMMENTS,
    }
    comments = flow_step_comments.get(flow_name, {})
    implicit_seq_to_point = {seq: pkey for pkey, seq in implicit_points.items()}

    nodes = []
    llm_value_map = {}
    for seq in range(1, n_steps + 1):
        sn = step_nodes[seq]
        node = {
            "id": f"n{seq}", "seq": seq, "kind": sn["kind"],
            "comment": comments.get(seq) or auto_comment(sn),
            "stability": sn["stability"],
        }
        if sn["is_map"]:
            node["type"] = "map"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["list_binding"] = sn["list_binding"]
            node["output_contract"] = sn["output_contract"]
            node["on_fail"] = "fallback"
        elif sn["role"] is not None:
            node["type"] = "branch"
            node["exec"] = "logic"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["output_contract"] = sn["output_contract"]
        else:
            node["type"] = "tool"
            node["cmd_template"] = sn["cmd_template"]
            node["bindings"] = sn["bindings"]
            node["output_contract"] = sn["output_contract"]
            node["on_fail"] = "fallback"

        # 判决/分型/聚合角色字段: 与上面 node["type"] 走 map/branch/tool 哪个分支正交
        # 独立附加，不再用 elif 排他——绝大多数流程的判决脚本步骤本身不是循环
        # (is_map 恒为 False)，角色字段只会出现在 branch 节点上，效果与老代码完全一致;
        # 但 premerge-gate 的 g4 是个例外: 它的循环步骤(seq9)自身聚合输出就直接是判决
        # 点的值(与 changeset-audit 把"循环"和"聚合判决"拆成两个独立步骤不同)，需要
        # 在已经附加了 map 专属字段(list_binding等)之后，继续叠加判决点字段，因此不能
        # 用 elif 排他。
        if sn["role"] is not None:
            if sn["depends_on"]:
                node["depends_on"] = sn["depends_on"]
            role = sn["role"]
            if role[0] == 'point':
                pkey = role[1]
                node["verdict_point"] = pkey
                cond = na_conditions.get(pkey)
                llm_values = _fill_point_branch_fields(node, pkey, rounds, cond)
                if cond and not cond.get("unresolved") and classification_seq:
                    deps = set(int(x[1:]) for x in node.get("depends_on", []))
                    deps.add(classification_seq)
                    node["depends_on"] = [f"n{s}" for s in sorted(deps)]
                if llm_values:
                    llm_value_map[pkey] = (seq, llm_values)
            elif role[0] == 'classification':
                cf = role[1]
                node["classification_field"] = cf
                node["cases"] = {v: v for v in sorted(set(classification_values[cf]))}
            elif role[0] == 'aggregate':
                node["aggregate_of"] = [f"n{s}" for s in sorted(point_decision_seq_by_key.values())]
        elif seq in implicit_seq_to_point:
            # 没有专属决策步骤的判决点，靠结构位置从这个普通工具步骤补齐——具体的
            # 数值门槛(例如 count>0 才 PASS)在样本内从未观测到反例(该计数值恒为
            # 能导向 PASS 的取值)，无法从数据统计验证，如实标注待人工核实，不假装
            # 已验证。这与"是否被 doc_type 门控为 N/A"是两件独立的事，后者(如果有)
            # 仍按 na_conditions 正常算出并加进 depends_on。
            pkey = implicit_seq_to_point[seq]
            node["verdict_point"] = pkey
            cond = na_conditions.get(pkey)
            if cond and not cond.get("unresolved") and classification_seq:
                node["depends_on"] = [f"n{classification_seq}"]
            node["needs_review"] = (
                f"判决点 {pkey} 没有专属的判决步骤，是按结构位置(相邻已认领判决点之间的孤儿"
                f"工具步骤)补齐推断出来的决策来源；样本内该步骤自身的计数值从未取到过会导向"
                f"FAIL 的取值，无法从当前 {n} 轮数据统计验证真实的机械判决门槛，仅能类推自"
                f"字段名的常识含义，建议人工核实"
            )
        nodes.append(node)

    # 通用 llm 兜底节点: 判决点自己的判决脚本产出过 PASS/FAIL/N-A 之外的终值(例如
    # AMBIGUOUS)时自动生成，不针对具体流程写死
    llm_nodes = []
    for pkey, (dseq, llm_values) in llm_value_map.items():
        llm_id = f"n{dseq}_llm"
        node = next(nd for nd in nodes if nd["id"] == f"n{dseq}")
        node["no_match"] = llm_id
        llm_nodes.append(_build_generic_llm_node(llm_id, pkey, dseq, rounds, llm_values))
    nodes.extend(llm_nodes)

    # verdict_rules
    verdict_rules = []
    for pkey in point_keys:
        dseq = point_decision_seq_by_key[pkey]
        node = next(nd for nd in nodes if nd["id"] == f"n{dseq}")
        rule_depends = sorted(set(int(x[1:]) for x in node.get("depends_on", [])) | {dseq})
        rule = {
            "point": pkey,
            "depends_on": [f"n{s}" for s in rule_depends],
            "decision_node": f"n{dseq}",
            "stability": node["stability"],
        }
        if pkey in llm_value_map:
            _, llm_values = llm_value_map[pkey]
            values_set = sorted(set(llm_values))
            rule["type"] = "llm"
            rule["comment"] = (
                f"{pkey} 的判决脚本(n{dseq})自己在观测样本里产出过 {values_set} 这类非 PASS/FAIL/N-A "
                f"的终值——这是轨迹数据本身记录的脚本自我判断结果，不是本归纳器分出来的桶，说明这类"
                f"样本连机械脚本自己都认为无法直接裁定。因此该终值分支移交 n{dseq}_llm 模型/人工复核，"
                f"不由本归纳器杜撰机械规则把它们并入 PASS 或 FAIL。"
            )
            rule["mechanical_shortcut"] = f"n{dseq} 输出为 PASS/FAIL 时直接采信，无需模型"
            rule["llm_node"] = f"n{dseq}_llm"
            verdict_rules.append(rule)
            continue

        # 非 llm 分支: doc_type 门控条件(cond) 与"决策节点是结构位置补齐而非专属判决
        # 步骤"(gap_filled) 是两件独立的事实，可能同时成立(例如 r2/r4)，也可能只有
        # 一项成立(r1/r3/r5 只有 cond；e2 只有 gap_filled)，这里分别判断后合并成一句
        # comment，而不是互斥的 if/elif，避免漏报其中一项。
        rule["type"] = "mechanical"
        cond = na_conditions.get(pkey)
        gap_filled = dseq in implicit_seq_to_point
        comment_parts = []
        if cond and not cond.get("unresolved"):
            rule["conditional"] = True
            rule["applicable_when"] = {cond["field"]: cond["applicable_when"]}
            comment_parts.append(
                f"{pkey} 仅在 {cond['field']}∈{cond['applicable_when']} 时适用"
                f"({cond['field']}∈{cond['na_when']} 时记 N/A)"
            )
        elif cond and cond.get("unresolved"):
            rule["conditional"] = True
            rule["needs_review"] = f"存在 N/A 轮次({cond['na_rounds']})但未找到与之完美相关的分型字段，需要人工核对"
            comment_parts.append(f"{pkey} 存在 N/A 轮次，但自动归纳未能找到完美相关的适用条件")
        if gap_filled:
            rule["conditional"] = True
            struct_note = f"{pkey} 没有专属判决步骤，决策来源由结构位置(相邻孤儿工具步骤)补齐推断，具体判决门槛未经数据统计验证"
            existing_review = rule.get("needs_review")
            rule["needs_review"] = (existing_review + "；" + struct_note) if existing_review else struct_note
            comment_parts.append(struct_note)
        if not comment_parts:
            comment_parts.append(
                f"{pkey} 完全由 {rule['decision_node']} 的输出机械决定，{n} 轮观测 {node['stability']} 一致，无反例"
            )
        no_model_claim = not gap_filled
        rule["comment"] = "；".join(comment_parts) + ("，无需模型介入。" if no_model_claim else "。")
        verdict_rules.append(rule)

    graph = {
        "version": "v1",
        "flow": flow_name,
        "input_vars": input_vars,
        "nodes": nodes,
        "verdict_rules": verdict_rules,
        "fallback": {
            "action": "spawn_model",
            "context": ["run_history", "current_node", "matched_trajectory_ref"],
        },
    }
    if classification_field:
        graph["classification"] = _build_classification_section(
            classification_field, classification_seq, classification_own_key,
            classification_values, verdict_rules, flow_name,
        )
    if aggregate_seq:
        graph["overall_verdict"] = {
            "decision_node": f"n{aggregate_seq}",
            "rule": f"pass = all(point != FAIL for point in {point_keys})",
            "comment": f"n{aggregate_seq} 汇总全部判决点，任一为 FAIL 即整单不合规(N/A 与 PASS 均不算不合规)。",
        }
    if flow_name in _REVISION_LOG_BY_FLOW:
        graph["revision_log"] = _REVISION_LOG_BY_FLOW[flow_name]
    if flow_name in _CONVENTIONS_BY_FLOW:
        graph["conventions"] = _CONVENTIONS_BY_FLOW[flow_name]

    stats = {
        "input_vars": input_vars,
        "point_keys": point_keys,
        "classification_fields": classification_fields,
        "point_decision_seq_by_key": point_decision_seq_by_key,
        "implicit_points": implicit_points,
        "step_nodes": step_nodes,
    }
    return graph, stats


def print_stats_generic(rounds, graph, stats):
    n = len(rounds)
    node_types = {}
    for node in graph['nodes']:
        node_types[node['type']] = node_types.get(node['type'], 0) + 1
    print(f"流程: {graph['flow']}")
    print(f"轮次数: {n}")
    print(f"节点总数: {len(graph['nodes'])}  类型分布: {node_types}")
    print(f"输入变量: {stats['input_vars']}")
    print(f"判决点键: {stats['point_keys']}")
    if stats['classification_fields']:
        print(f"分型字段: {stats['classification_fields']}")
    print("verdict_rules:")
    for rule in graph['verdict_rules']:
        extra = ""
        if rule.get('conditional'):
            extra = f" conditional={rule.get('applicable_when') or rule.get('needs_review')}"
        print(f"  {rule['point']}: type={rule.get('type','?')} decision_node={rule['decision_node']} stability={rule['stability']}{extra}")
    unresolved_total = sum(
        1 for node in graph['nodes'] for b in node.get('bindings', {}).values() if b.get('from') == 'UNRESOLVED'
    )
    unresolved_total += sum(
        1 for node in graph['nodes']
        if isinstance(node.get('list_binding'), dict) and node.get('list_binding', {}).get('from') == 'UNRESOLVED'
    )
    print(f"UNRESOLVED 绑定总数: {unresolved_total}")


def write_graph(graph, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
        f.write('\n')


def main():
    if len(sys.argv) < 3:
        print("用法: python3 induce.py <轨迹目录> <输出图路径> [--flow <流程名>]", file=sys.stderr)
        sys.exit(1)
    traj_dir, out_path = sys.argv[1], sys.argv[2]
    cli_flow = None
    rest = sys.argv[3:]
    i = 0
    while i < len(rest):
        if rest[i] == '--flow' and i + 1 < len(rest):
            cli_flow = rest[i + 1]
            i += 2
        else:
            print(f"未知参数: {rest[i]}", file=sys.stderr)
            sys.exit(1)

    flow_name = cli_flow or os.path.basename(os.path.normpath(traj_dir))
    rounds = load_rounds(traj_dir)

    if flow_name == 'changeset-audit':
        graph, stats = build_graph_changeset_audit(rounds)
        write_graph(graph, out_path)
        print_stats(rounds, graph, stats)
    else:
        graph, stats = build_graph_generic(rounds, flow_name)
        write_graph(graph, out_path)
        print_stats_generic(rounds, graph, stats)
    print(f"图已写入: {out_path}")


if __name__ == '__main__':
    main()
