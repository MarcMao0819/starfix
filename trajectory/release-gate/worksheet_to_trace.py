#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""操作单 → 轨迹 jsonl 转换器（第五条轨道二期衔接）。

只读一份"已回填"的操作单 Markdown（`make_worksheet.py` 产出、人工执行 B 节命令后
按四段式键值串回填的《实际结果》表格），产出符合
`${TRAJ_HOME}/TRAJECTORY-CONTRACT.md` 约定 1 的轨迹 jsonl：
一条 input 行（候选坐标与参数）+ 每步一条 step 行（cmd/output_digest）+ 一条
verdict 行（各步判决点的 PASS/FAIL 汇总）。

只做文本解析，不调用 git/SQL/子进程，也不读操作单文件之外的任何东西——本工具
本身是二期"影子模式"里把人工回填的操作单接上轨迹归纳管线的唯一桥梁，不是又一个
执行器。

遇到仍是 `?` 占位、缺 `point=`/`verdict=` 字段、verdict 取值不在
PASS|FAIL|N/A|AMBIGUOUS 之内、或表格结构解析不出 4 列的行 —— 一律报错并点名
具体是哪一步、哪个字段，绝不产出残缺/伪造轨迹（这是 <日期> 归纳器把摘要里的
占位符 `<non-delete files>` 当成命令原文、产出"看着对、实际跑不了"的假图那次
教训的镜像：本工具反过来在源头挡住占位符流入轨迹）。

用法：
    python3 worksheet_to_trace.py <已回填的操作单.md> <输出.jsonl>
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any


class WorksheetParseError(Exception):
    """任何解析失败都用这个类型抛出；消息里必须点名具体步骤名 + 具体字段。"""


# TRAJECTORY-CONTRACT.md 约定 1：判决步骤的 verdict 取值只允许这四种。
_ALLOWED_VERDICTS = {"PASS", "FAIL", "N/A", "AMBIGUOUS"}

# 键值串按 '/' 切分，但只在 '/' 紧跟一个形如 'key=' 的小写标识符时才当分隔符
# （与 inducer/induce.py 的 _FIELD_SPLIT_RE 同一约定），避免把字段值里本身含 '/'
# 的内容（例如 probe_paths 里的接口路径 "/api/xxx/page"）切碎。
_FIELD_SPLIT_RE = re.compile(r"/(?=[a-z_][a-z0-9_]*=)")

_HEADER_FIELD_RES: dict[str, re.Pattern[str]] = {
    "worktree": re.compile(r"^- 候选 worktree：`(.*)`$"),
    "branch": re.compile(r"^- 候选分支：`(.*)`$"),
    "head": re.compile(r"^- 候选 HEAD：`(.*)`$"),
    "merge_base": re.compile(r"^- merge-base：`(.*)`$"),
    "title": re.compile(r"^- changeset 标题：(.*)$"),
}
_TITLE_LINE_RE = re.compile(r"^# 操作单 · (\S+)$")


def parse_header(lines: list[str]) -> dict[str, str]:
    """从操作单顶部的元数据块提取候选坐标（worktree/分支/HEAD/merge-base/标题/
    changeset 号）。这些字段由生成器渲染，不属于人工回填区，原样采信。"""
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        m = _TITLE_LINE_RE.match(stripped)
        if m:
            fields["changeset_no"] = m.group(1)
            continue
        for key, rx in _HEADER_FIELD_RES.items():
            m2 = rx.match(stripped)
            if m2:
                fields[key] = m2.group(1)
    return fields


def find_result_table_rows(text: str) -> list[str]:
    """定位《实际结果》回填表格一节，返回它的数据行（跳过表头行与分隔行）。"""
    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.strip().startswith("## 《实际结果》回填表格"):
            start = i
            break
    if start is None:
        raise WorksheetParseError("未找到《实际结果》回填表格一节标题（## 《实际结果》回填表格...），无法定位回填表")

    table_lines: list[str] = []
    in_table = False
    for l in lines[start + 1 :]:
        stripped = l.strip()
        if stripped.startswith("|"):
            in_table = True
            table_lines.append(stripped)
            continue
        if in_table:
            break  # 表格已开始过、遇到第一条非 '|' 开头的行即表示表格结束

    if len(table_lines) < 3:  # 表头 + 分隔行 + 至少 1 条数据行
        raise WorksheetParseError("《实际结果》回填表格没有数据行（只找到表头/分隔行），本单尚未回填任何一步")

    _header, _sep, *data_rows = table_lines
    return data_rows


def split_table_row(row: str) -> list[str]:
    """按 markdown 表格语法切分一行；先把转义的 '\\|'（字面竖线）临时占位，避免
    被误当成列分隔符，切完再还原。"""
    placeholder = "\x00ESCAPED_PIPE\x00"
    protected = row.replace("\\|", placeholder)
    cells = [c.strip() for c in protected.strip().strip("|").split("|")]
    return [c.replace(placeholder, "|") for c in cells]


def unescape_cell(cell: str) -> str:
    """还原生成器写入单元格时做的换行转义（'<br>' -> 真实换行）。"""
    return cell.replace("<br>", "\n")


def parse_kv_cell(cell: str, step_name: str) -> dict[str, str]:
    """解析"原始输出关键行"列的键值串，校验 point=/verdict= 齐全、
    verdict 取值合法、且没有遗留的 '?' 占位符。"""
    fields: dict[str, str] = {}
    for part in _FIELD_SPLIT_RE.split(cell):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise WorksheetParseError(f"步骤「{step_name}」：键值串片段无法解析（缺 '='）：{part!r}")
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise WorksheetParseError(f"步骤「{step_name}」：键值串片段缺字段名：{part!r}")
        if v == "?":
            raise WorksheetParseError(f"步骤「{step_name}」：字段 '{k}' 仍是占位符 '?'，尚未回填")
        if k in fields:
            raise WorksheetParseError(f"步骤「{step_name}」：字段 '{k}' 在同一步键值串里重复出现")
        fields[k] = v

    if "point" not in fields:
        raise WorksheetParseError(f"步骤「{step_name}」：键值串缺少 'point=' 字段（TRAJECTORY-CONTRACT.md 约定1要求）")
    if "verdict" not in fields:
        raise WorksheetParseError(f"步骤「{step_name}」：键值串缺少 'verdict=' 字段（TRAJECTORY-CONTRACT.md 约定1要求）")
    if fields["verdict"] not in _ALLOWED_VERDICTS:
        raise WorksheetParseError(
            f"步骤「{step_name}」：verdict 取值 {fields['verdict']!r} 不在允许集合 "
            f"{sorted(_ALLOWED_VERDICTS)} 内（TRAJECTORY-CONTRACT.md 约定1；'BLOCKED' 等是回执层面的处置状态，"
            "不是本行的判决点 verdict，请改填 PASS/FAIL）。若要说明分歧，请写在『分歧/备注』列，"
            "不要写进 verdict"
        )
    return fields


_KIND_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\b"), "git"),
    (re.compile(r"\bcurl\b"), "http"),
    (re.compile(r"\bmvnw\b|\bmvn\b"), "build"),
    (re.compile(r"vue-tsc"), "build"),
    (re.compile(r"^rm\b|^mkdir\b|^cat\b", re.MULTILINE), "fs"),
]


def infer_kind(cmd: str) -> str:
    """给 step 的 kind 字段做个粗分类（git/http/build/fs/shell），纯展示性质，
    不影响 point/verdict 的判定逻辑——归纳器只依赖 output_digest 里的
    point=/verdict= 字段，不依赖 kind 取值。"""
    for rx, kind in _KIND_RULES:
        if rx.search(cmd):
            return kind
    return "shell"


def parse_data_row(row: str) -> dict[str, Any]:
    """解析一行数据。表格 4 列（旧单，无「分歧/备注」列）或 5 列（新单，<日期>
    起新增「分歧/备注」列）都接受，兼容已回填的旧形态操作单，不强制补列。"""
    cells = split_table_row(row)
    if len(cells) == 5:
        step_name_cell, cmd_cell, kv_cell, _hint_cell, divergence_cell = cells
    elif len(cells) == 4:
        step_name_cell, cmd_cell, kv_cell, _hint_cell = cells
        divergence_cell = ""
    else:
        raise WorksheetParseError(f"回填表格一行的列数不是 4 或 5（实际 {len(cells)} 列）：{row!r}")
    step_name = unescape_cell(step_name_cell)
    if not step_name:
        raise WorksheetParseError(f"某一行的步骤名列为空：{row!r}")

    kv_fields = parse_kv_cell(kv_cell, step_name)

    cmd_text = unescape_cell(cmd_cell)
    if cmd_text.strip() == "?":
        raise WorksheetParseError(f"步骤「{step_name}」：命令列仍是占位符 '?'，未回填/未渲染")
    if not cmd_text.strip():
        raise WorksheetParseError(f"步骤「{step_name}」：命令列为空")

    result: dict[str, Any] = {
        "step_name": step_name,
        "cmd": cmd_text,
        "kv_fields": kv_fields,
        "output_digest": kv_cell,
    }
    divergence_text = unescape_cell(divergence_cell).strip()
    if divergence_text:
        result["divergence"] = divergence_text
    return result


def build_trace(header: dict[str, str], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    input_record: dict[str, Any] = {"type": "input"}
    for key in ("changeset_no", "worktree", "branch", "head", "merge_base"):
        if key in header:
            input_record[key] = header[key]
    input_record["ts"] = now

    records: list[dict[str, Any]] = [input_record]
    points: dict[str, str] = {}
    fail_notes: list[str] = []

    for seq, step in enumerate(steps, start=1):
        point = step["kv_fields"]["point"]
        verdict = step["kv_fields"]["verdict"]
        if point in points:
            raise WorksheetParseError(f"判决点 'point={point}' 在多个步骤里重复出现（重复步骤：「{step['step_name']}」），每步的 point 必须唯一")
        points[point] = verdict
        if verdict != "PASS":
            fail_notes.append(f"{point}: {step['step_name']} -> {verdict}")
        step_record: dict[str, Any] = {
            "type": "step",
            "seq": seq,
            "kind": infer_kind(step["cmd"]),
            "cmd": step["cmd"],
            "ok": verdict == "PASS",
            "output_digest": step["output_digest"],
        }
        if "divergence" in step:
            # 「分歧/备注」列的原样文本，不参与 verdict 解析，只作为该 step 的旁注。
            step_record["divergence"] = step["divergence"]
        records.append(step_record)

    overall_pass = bool(points) and all(v == "PASS" for v in points.values())
    records.append(
        {
            "type": "verdict",
            "pass": overall_pass,
            "points": points,
            "fail_detail": " | ".join(fail_notes),
        }
    )
    return records


def convert(worksheet_path: Path) -> list[dict[str, Any]]:
    text = worksheet_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    header = parse_header(lines)
    for required in ("changeset_no", "worktree", "head"):
        if required not in header:
            raise WorksheetParseError(f"操作单头部缺少必需字段 '{required}'（正常情况下应由 make_worksheet.py 渲染出，检查是否是完整操作单文件）")

    data_rows = find_result_table_rows(text)
    steps = [parse_data_row(row) for row in data_rows]
    return build_trace(header, steps)


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：python3 worksheet_to_trace.py <已回填的操作单.md> <输出.jsonl>", file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    if not src.is_file():
        print(f"错误：找不到操作单文件 {src}", file=sys.stderr)
        return 2

    try:
        records = convert(src)
    except WorksheetParseError as e:
        print(f"转换失败：{e}", file=sys.stderr)
        return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_steps = len(records) - 2
    print(f"已产出 {len(records)} 行轨迹（1 input + {n_steps} step + 1 verdict）：{dst}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
