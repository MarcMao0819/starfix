#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
留痕导出器 (db_to_trace.py)

把 runner (runner/run_graph.py) 已经积累在 runtime.db::trace_runs 里的节点级运行
留痕，转成符合 TRAJECTORY-CONTRACT.md 约定1 的 round-*.jsonl 轨迹文件 —— 不重新
执行任何业务动作，只读 runtime.db，只用 Python 3 标准库。

背景 (TRAJECTORY-CONTRACT.md "剩余一处" 章节): changeset-audit 是最早采的流程，
它的 round-*.jsonl 是当年人工现场采集的，digest 里没有 point=/verdict= 字段，
通用归纳引擎吃不下，只能靠专用管线 build_graph_changeset_audit。但 trace_runs
表已经按节点记录了 215 轮真实运行的 output_digest，而图 JSON 里每个节点都带
verdict_point 字段——两边一拼，就能在不碰 runner/run_graph.py 的前提下，合成
符合约定1 的轨迹，本脚本就是这道拼接。

用法:
    python3 db_to_trace.py --db runtime.db --graph <图.json> --flow <流程名> \
        --out-dir <轨迹目录> [--run-tag <过滤子串>] [--limit N]

设计要点:
    - node_id -> verdict_point 映射、节点执行顺序，全部从 --graph 读取，不写死
      任何流程专属的节点名/点位名。
    - “核心节点”定义为 seq 字段是整数的节点：changeset-audit.v4.json 里 n9b(seq
      ="9b")、n9_llm(无 seq) 这类只在特定分支下才会触发的附加节点被排除在外——
      同一批导出轨迹的步骤数必须严格一致 (induce.py::load_rounds 的硬性要求)，
      而这些附加节点是否出现取决于每一轮的实际路由结果，天然做不到跨轮一致。
    - 流程归属判定: 用核心序列第一个节点的 output_contract (若为 "regex:..." 形式)
      去匹配 trace_runs 里该节点的 output_digest，完全从图数据本身推导，不依赖
      任何硬编码的流程专属字符串。
    - 完整性过滤 (两道，都会被统计并跳过，不静默产出残缺轨迹):
        1) 核心节点集合必须完整出现在该轮留痕里 (运行中途 FALLBACK/失败退出的
           轮次会缺节点，例如卡在 n13 报语法错误，n14 就不会有记录)。
        2) 每个带 verdict_point 的核心节点，其 status 必须是 PASS/FAIL/N/A/
           AMBIGUOUS 之一 (约定1 要求的合法判决终值)。changeset-audit.v4 在 n6/n9
           引入的 DEFER/ROUTED_TO_N9B/NOMATCH_ROUTED_TO_LLM 等中间路由态不是终值，
           要靠 n9b/n9_llm(附加节点)才能消歧——本导出器的目标是 14 核心节点的
           v1 等价结构，无法表达这类扩展分支，遇到就诚实跳过，不假装能解读。
    - cmd 渲染: 按图节点的 bindings 声明 (input.<var> / n<seq>.output.<field>)
      从本轮已解析的更早步骤 digest 里取值填入 cmd_template；解不出的占位符原样
      保留 (如实标注不可得，不编造)。
    - output_digest: 非判决节点原样保留；判决节点 (有 verdict_point 的核心节点)
      按约定1 补上 "point=<点位>" 与 "verdict=<判决>" 两个字段，原始 digest 内容
      逐字保留在中间——三段用 tab 分隔 (而非任务描述里字面的"/")，原因见
      render 处的行内注释: induce.py::parse_fields 的字段切分正则对 "/" 只在其
      后紧跟"小写snake_case key="时才切分，changeset-audit 的原始 digest 是自由
      文本(如 "REGEX_OK"/"CLEAN")不满足这个形状，用 "/" 会导致 point 字段解析出
      "p1/REGEX_OK" 这类脏值、判决点身份识别失败；tab 无条件切分且已核对判决节点
      原始 digest 均不含字面 tab，不影响"原始内容逐字保留"。
    - input 行标 "source": "runtime_db_export"，注明这是从留痕导出、非现场采集。
"""
import argparse
import json
import os
import re
import sqlite3
import sys


# ---------------------------------------------------------------------------
# 图读取: 核心节点序列 / 判决点映射 / 入口契约
# ---------------------------------------------------------------------------

def load_graph(graph_path):
    with open(graph_path, encoding='utf-8') as f:
        return json.load(f)


def core_nodes_from_graph(graph):
    """核心节点: seq 字段是整数的节点，按 seq 升序排列。排除只在特定分支下才会
    触发的附加节点 (例如 changeset-audit.v4 的 n9b/seq="9b"、n9_llm/无 seq)。"""
    nodes = [n for n in graph.get('nodes', []) if isinstance(n.get('seq'), int)]
    nodes.sort(key=lambda n: n['seq'])
    return nodes


def entry_contract_regex(core_nodes):
    """核心序列第一个节点的 output_contract，若是 'regex:' 前缀则编译成正则，
    用于从 trace_runs 里识别属于本流程的 run_id —— 完全从图数据推导，不依赖任何
    流程专属的硬编码字符串。图未提供可用契约时返回 None，调用方退化为
    '全部 run_id 都当候选'。"""
    if not core_nodes:
        return None
    contract = core_nodes[0].get('output_contract', '')
    if not isinstance(contract, str) or not contract.startswith('regex:'):
        return None
    try:
        return re.compile(contract[len('regex:'):])
    except re.error:
        return None


# ---------------------------------------------------------------------------
# digest 解析 / cmd_template 渲染 (按图声明的 bindings 路径解值，解不出就留占位符)
# ---------------------------------------------------------------------------

def parse_digest_fields(digest):
    """从 output_digest 里解析 key=value 字段 (按 tab 切分，changeset-audit 全部
    结构化工具输出都是 tab 分隔；非 key=value 的片段忽略)。"""
    fields = {}
    for part in digest.split('\t'):
        if '=' in part:
            k, v = part.split('=', 1)
            k = k.strip()
            if k and k not in fields:
                fields[k] = v.strip()
    return fields


_SOURCE_INPUT_RE = re.compile(r'^input\.(\w+)$')
_SOURCE_FIELD_RE = re.compile(r'^n(\w+)\.output\.(\w+)$')
_SOURCE_WHOLE_RE = re.compile(r'^n(\w+)\.output$')


def resolve_binding_value(source, known_input, digests_so_far):
    """按图节点声明的 bindings[var]['from'] 路径解出实际值。source 形如
    'input.changeset_no' / 'n1.output.id' / 'n7.output'。解不出时返回 None，
    调用方据此保留模板占位符原样，不编造值。"""
    if not source or source == 'UNRESOLVED':
        return None
    m = _SOURCE_INPUT_RE.match(source)
    if m:
        return known_input.get(m.group(1))
    m = _SOURCE_FIELD_RE.match(source)
    if m:
        digest = digests_so_far.get('n' + m.group(1))
        return None if digest is None else parse_digest_fields(digest).get(m.group(2))
    m = _SOURCE_WHOLE_RE.match(source)
    if m:
        return digests_so_far.get('n' + m.group(1))
    return None


class _SafeDict(dict):
    """format_map 的容错字典: 缺失的 key 原样吐回 "{key}" 文本，而不是抛
    KeyError —— 这就是"解不出的占位符如实标注、不编造"的实现方式。"""

    def __missing__(self, key):
        return '{' + key + '}'


def render_cmd(cmd_template, node, known_input, digests_so_far):
    """渲染 cmd_template: 能从本轮数据解出的 binding 填真实值，解不出的占位符
    原样保留。图里已有的字面双花括号 (例如 grep 的 {{40}} 数量词) 走 str.format_map
    原生语义还原成单花括号，无需额外处理。"""
    bindings = node.get('bindings') or {}
    values = {}
    for var, spec in bindings.items():
        source = spec.get('from') if isinstance(spec, dict) else None
        val = resolve_binding_value(source, known_input, digests_so_far)
        if val is not None:
            values[var] = val
    try:
        return cmd_template.format_map(_SafeDict(values))
    except (KeyError, ValueError, IndexError):
        return cmd_template


# ---------------------------------------------------------------------------
# trace_runs 读取 / 候选流程归属判定 / 完整性过滤
# ---------------------------------------------------------------------------

_TERMINAL_VALUES = {'PASS', 'FAIL', 'N/A', 'AMBIGUOUS'}


def fetch_candidate_run_ids(cur, entry_node_id, entry_re):
    """流程归属判定: entry_re 非空时，只有该 run 在 entry_node_id 上的
    output_digest 命中入口契约正则才算候选 (候选 != 完整——是否节点齐全在后面
    单独过滤并计入统计)。entry_re 为 None (图未提供可用契约) 时退化为全体
    run_id 都当候选。"""
    if entry_re is not None:
        cur.execute(
            "SELECT run_id, output_digest FROM trace_runs WHERE node_id=?",
            (entry_node_id,),
        )
        return [rid for rid, digest in cur.fetchall() if entry_re.match(digest or '')]
    cur.execute("SELECT DISTINCT run_id FROM trace_runs")
    return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(
        description="把 runtime.db::trace_runs 的节点级运行留痕导出成符合 TRAJECTORY-CONTRACT.md 约定1 的 round-*.jsonl 轨迹文件",
    )
    ap.add_argument('--db', required=True, help='runtime.db 路径 (只读打开)')
    ap.add_argument('--graph', required=True, help='用作节点顺序/判决点映射来源的图 JSON')
    ap.add_argument('--flow', required=True, help='要导出的流程名 (与图的 flow 字段做一致性提示，不强制)')
    ap.add_argument('--out-dir', required=True, help='导出的轨迹目录')
    ap.add_argument('--run-tag', default=None, help='run_tag 子串过滤 (可选)')
    ap.add_argument('--limit', type=int, default=None, help='最多导出多少轮 (可选，默认导出全部合格轮次)')
    args = ap.parse_args()

    graph = load_graph(args.graph)
    if graph.get('flow') and graph['flow'] != args.flow:
        print(f"提示: 图声明的 flow='{graph['flow']}' 与 --flow '{args.flow}' 不同，"
              f"继续执行 (图只作为节点结构/映射来源，不强制流程名一致)", file=sys.stderr)

    core_nodes = core_nodes_from_graph(graph)
    if not core_nodes:
        print(f"图 {args.graph} 没有任何 seq 为整数的核心节点，无法导出", file=sys.stderr)
        sys.exit(1)
    node_order = [n['id'] for n in core_nodes]
    core_id_set = set(node_order)

    point_map = {}          # node_id -> point_key (仅处理单一字符串的 verdict_point)
    for n in core_nodes:
        vp = n.get('verdict_point')
        if isinstance(vp, str):
            point_map[n['id']] = vp
        elif vp is not None:
            print(f"节点 {n['id']} 的 verdict_point 是 {vp!r} (非单一字符串)，"
                  f"本导出器只处理单点位判决节点，跳过该节点的 point/verdict 标注", file=sys.stderr)
    point_decision_node = {pkey: nid for nid, pkey in point_map.items()}

    input_vars = graph.get('input_vars') or []
    if input_vars != ['changeset_no']:
        print(f"警告: 图声明的 input_vars={input_vars}，本导出器目前只支持单一 "
              f"changeset_no 输入变量 (对应 trace_runs.changeset_no 列)，按此假设继续执行",
              file=sys.stderr)
    input_var_name = input_vars[0] if input_vars else 'changeset_no'

    entry_node_id = core_nodes[0]['id']
    entry_re = entry_contract_regex(core_nodes)

    db_uri = f"file:{os.path.abspath(args.db)}?mode=ro"
    con = sqlite3.connect(db_uri, uri=True)
    cur = con.cursor()

    candidate_run_ids = sorted(set(fetch_candidate_run_ids(cur, entry_node_id, entry_re)))

    skip_missing_nodes = []      # [(run_id, [missing_node_ids])]
    skip_nonterminal = []        # [(run_id, [(node_id, status)])]
    skip_run_tag_filtered = 0
    exportable = []              # [(run_id, ts_min, changeset_no, run_tag, by_node)]

    for rid in candidate_run_ids:
        cur.execute(
            "SELECT node_id, status, output_digest, ts, changeset_no, run_tag FROM trace_runs WHERE run_id=?",
            (rid,),
        )
        rows = cur.fetchall()
        by_node = {r[0]: r for r in rows}
        missing = sorted(core_id_set - set(by_node.keys()))
        if missing:
            skip_missing_nodes.append((rid, missing))
            continue
        nonterminal = [
            (nid, by_node[nid][1]) for nid in node_order
            if nid in point_map and by_node[nid][1] not in _TERMINAL_VALUES
        ]
        if nonterminal:
            skip_nonterminal.append((rid, nonterminal))
            continue
        run_tag = rows[0][5]
        if args.run_tag and args.run_tag not in (run_tag or ''):
            skip_run_tag_filtered += 1
            continue
        changeset_no = rows[0][4]
        ts_min = min(r[3] for r in rows)
        exportable.append((rid, ts_min, changeset_no, run_tag, by_node))

    exportable.sort(key=lambda x: x[1])
    total_qualified = len(exportable)
    if args.limit is not None:
        exportable = exportable[:args.limit]

    os.makedirs(args.out_dir, exist_ok=True)
    exported_files = []
    for idx, (rid, ts_min, changeset_no, run_tag, by_node) in enumerate(exportable, start=1):
        known_input = {input_var_name: changeset_no}
        digests_so_far = {}
        step_lines = []
        points = {}
        for node in core_nodes:
            nid = node['id']
            _node_id, status, digest_raw, ts, _cs, _tag = by_node[nid]
            cmd_rendered = render_cmd(node.get('cmd_template', ''), node, known_input, digests_so_far)
            digests_so_far[nid] = digest_raw
            pkey = point_map.get(nid)
            if pkey:
                # 用 tab 分隔 point=/<原始digest>/verdict= 三段: induce.py::parse_fields
                # 的字段切分正则 (_FIELD_SPLIT_RE) 对 "/" 只在其后紧跟"小写snake_case
                # key="时才切分(适配premerge-gate/cleanup-audit那类每段本身就是
                # key=value的digest，例如"tracked_dirty=1/verdict=FAIL")；changeset-audit
                # 的原始 digest 是历史遗留的自由文本(如"REGEX_OK"/"CLEAN"/"PASS (a=1 b=2)")，
                # 不满足这个形状，若沿用"/"分隔，"point="与紧跟其后的原始文本会被解析成
                # 同一个字段(值里带斜杠)，判决点身份识别会失败。tab 不受内容形状限制、
                # 无条件切分，且已核对全部判决节点的原始 digest 都不含字面 tab，改用 tab
                # 不会破坏"原始 digest 内容逐字保留"这条要求。
                digest_out = f"point={pkey}\t{digest_raw}\tverdict={status}"
                points[pkey] = status
            else:
                digest_out = digest_raw
            step_lines.append({
                "type": "step",
                "seq": node['seq'],
                "kind": node.get('kind', ''),
                "cmd": cmd_rendered,
                "ok": True,
                "output_digest": digest_out,
            })

        def _point_sort_key(pk):
            m = re.match(r'^([A-Za-z]+)(\d+)$', pk)
            return (m.group(1), int(m.group(2))) if m else (pk, -1)

        fail_parts = []
        for pkey in sorted(points, key=_point_sort_key):
            if points[pkey] == 'FAIL':
                nid = point_decision_node.get(pkey)
                fail_parts.append(f"{pkey}: {digests_so_far.get(nid, '')}")
        fail_detail = " | ".join(fail_parts)
        overall_pass = all(v != 'FAIL' for v in points.values())

        input_line = {
            "type": "input",
            input_var_name: changeset_no,
            "ts": ts_min,
            "source": "runtime_db_export",
        }
        verdict_line = {
            "type": "verdict",
            "pass": overall_pass,
            "points": points,
            "fail_detail": fail_detail,
        }

        out_path = os.path.join(args.out_dir, f"round-{idx:03d}-{changeset_no}.jsonl")
        with open(out_path, 'w', encoding='utf-8') as f:
            for rec in [input_line] + step_lines + [verdict_line]:
                f.write(json.dumps(rec, ensure_ascii=False))
                f.write('\n')
        exported_files.append(out_path)

    # ---- 统计报告 (原始数据，供收尾验证粘贴) ----
    print(f"入口节点: {entry_node_id}  核心节点序列: {node_order}")
    print(f"判决点映射: {point_map}")
    print(f"候选轮次 (流程归属匹配 entry_contract): {len(candidate_run_ids)}")
    print(f"  - 跳过(节点不齐全，缺: 详见下方): {len(skip_missing_nodes)}")
    print(f"  - 跳过(判决节点处于非终值路由态，如 DEFER/ROUTED_TO_*，超出本导出器能表达的范围): {len(skip_nonterminal)}")
    if args.run_tag:
        print(f"  - 跳过(run_tag 不含 '{args.run_tag}'): {skip_run_tag_filtered}")
    print(f"完整性过滤后合格轮次: {total_qualified}")
    if args.limit is not None:
        print(f"--limit {args.limit} 生效，实际导出: {len(exported_files)}")
    else:
        print(f"实际导出: {len(exported_files)}")
    print("残缺跳过明细 (节点不齐全):")
    for rid, missing in skip_missing_nodes:
        print(f"  run_id={rid} 缺节点: {missing}")
    print("残缺跳过明细 (判决节点非终值路由态):")
    for rid, detail in skip_nonterminal:
        print(f"  run_id={rid} {detail}")
    print(f"已写入 {len(exported_files)} 个轨迹文件到 {args.out_dir}")


if __name__ == '__main__':
    main()
