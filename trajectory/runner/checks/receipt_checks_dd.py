#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回执合规判据 · DD 族适配（抖店舰队回执）。

与 runner/checks/receipt_checks.py 的关系：**只改分型 check_t0，e1/e2/r1-r6 逐字照抄**。
生产件 receipt_checks.py 一个字节不动（约定 10：改共用件必须新建副本，禁活体编辑）。

## 为什么要单独一份
DD 系回执用 <跳板机> 侧 `t_code_changeset` 的自增 id，正文写 `changeset_id=<数字>` 单独一行，
**没有** `CS-YYYYMMDD-NNNN` 单号。原分型要求 `has_cs_no`，DD 回执一律落到 nondelivery，
于是 r2/r3/r4（锚点 sha / worktree+分支 / 文件清单）三个判决点全被门控掉、根本吃不到。

## 分型改了什么，以及为什么不照抄「放宽 has_cs_no」了事
发单方建议：code_delivery = (CS 单号 或 changeset_id 行) 且 has_file_table。
**这样改会留下一个死分支**：`has_file_table` 的判据是「列表项/表格行里含合法文件路径」，
而 r4 的判据是「全文含合法文件路径」——**前者成立必然蕴含后者成立**。也就是说，凡是能
被分型认成 code_delivery 的文档，r4 必 PASS，**r4 的 FAIL 分支结构上不可达**。
（这一条对现有 v2 同样成立，是既有图的一个死分支；v2 是生产件，本单不碰，仅记录上报。）

按硬约定 11「含或分支的判据必须逐分支单独取证」与硬约定 6「零反例的分支不许上线」，
本实现把两者**解耦**：
  - 分型问的是「这份回执**自称**是代码交付吗」→ 看**清单区段标记**（改动文件/新建文件/
    文件清单… 独占一行的小节标题），不要求区段里真有合法路径；
  - r4 问的是「它**给没给出**文件路径证据」→ 照旧全文找合法路径。
解耦之后，「自称交付但一条路径都没给」这种回执才能被分型收进来并被 r4 判 FAIL——
这正是 r4 存在的意义。DD-95 fixture 就是这个形态的反例。

兼容性：`has_file_table` 仍保留在析取式里，所以老形态（有路径列表、无小节标题）照旧成立。
"""

import sys, re

def read_lines(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read().split('\n')

FILE_EXT = r'(java|vue|ts|js|sql|md|yml|yaml|json|cjs|sh|py|css)'

# 「清单区段标记」：独占一行的小节标题，允许 markdown 井号与可选冒号。
# 刻意**不**要求区段里含合法文件路径——那样会让 r4 的 FAIL 分支不可达（见模块文档）。
MANIFEST_HEADING = r'(改动文件|新建文件|变更文件|交付文件|文件清单|改动清单)'

def check_t0(path):
    lines = read_lines(path)
    text = '\n'.join(lines)
    n = len(lines) if (lines and lines[-1] != '') else len(lines) - 1
    name = path.split('/')[-1]
    name_pattern = bool(re.search(r'步骤|日志', name))
    has_verdict_section = bool(re.search(r'^#{1,6}\s*(结论|状态)\b', text, re.M)) or bool(re.search(r'结论[:：]|状态[:：]', text))
    has_cs_no = bool(re.search(r'CS-\d{8}-\d{4}', text))
    # DD 族：<跳板机> 侧 t_code_changeset 自增 id，独占一行
    has_changeset_id = bool(re.search(r'^changeset_id=\d+\s*$', text, re.M))
    has_file_table = bool(re.search(r'^\|.*\.' + FILE_EXT + r'.*\|', text, re.M)) or bool(re.search(r'^\s*[-*]\s+.*\.' + FILE_EXT, text, re.M))
    has_manifest_heading = bool(re.search(r'^#{0,6}\s*' + MANIFEST_HEADING + r'\s*[:：]?\s*$', text, re.M))
    id_ok = has_cs_no or has_changeset_id
    manifest_ok = has_file_table or has_manifest_heading
    if name_pattern or (n > 800 and not has_verdict_section):
        doc_type = 'process_log'
    elif id_ok and manifest_ok:
        doc_type = 'code_delivery'
    else:
        doc_type = 'nondelivery'
    print(f"name_pattern={name_pattern}/lines={n}/has_verdict_section={has_verdict_section}/has_cs_no={has_cs_no}/has_changeset_id={has_changeset_id}/has_file_table={has_file_table}/has_manifest_heading={has_manifest_heading}/type={doc_type}")

def check_e1(path):
    text = '\n'.join(read_lines(path))
    hits = re.findall(r'[åãæÂÃ]', text)
    print(f"hits={len(hits)}/verdict={'FAIL' if hits else 'PASS'}")

def check_r1(path):
    text = '\n'.join(read_lines(path))
    status_re = r'\b(PASS|FAIL|BLOCKED|请示|IN_PROGRESS|PARTIAL-HANDOFF|PARTIAL|AWAITING_APPROVAL|AWAITING_MARC_APPROVAL)\b'
    hedge_re = r'基本完成|应该没问题|大概可以|初步完成'
    status_found = bool(re.search(status_re, text))
    hedge_found = bool(re.search(hedge_re, text))
    verdict = 'PASS' if (status_found and not hedge_found) else 'FAIL'
    print(f"status_found={status_found}/hedge_found={hedge_found}/verdict={verdict}")

def check_r2(path):
    text = '\n'.join(read_lines(path))
    hits = re.findall(r'\b[0-9a-f]{40}\b', text)
    print(f"sha40_count={len(hits)}/verdict={'PASS' if hits else 'FAIL'}")

def check_r3(path):
    text = '\n'.join(read_lines(path))
    has_branch = bool(re.search(r'分支|branch', text, re.I))
    has_worktree = bool(re.search(r'工作树|worktree', text, re.I))
    verdict = 'PASS' if (has_branch and has_worktree) else 'FAIL'
    print(f"has_branch={has_branch}/has_worktree={has_worktree}/verdict={verdict}")

def check_r4(path):
    text = '\n'.join(read_lines(path))
    hits = re.findall(r'[\w./\-]+\.' + FILE_EXT, text)
    print(f"file_path_count={len(hits)}/verdict={'PASS' if hits else 'FAIL'}")

def check_r5(path):
    text = '\n'.join(read_lines(path))
    has_fence = '```' in text
    has_image = bool(re.search(r'\.png|\.jpg|\.jpeg|!\[', text, re.I))
    verdict = 'PASS' if (has_fence or has_image) else 'FAIL'
    print(f"has_fence={has_fence}/has_image={has_image}/verdict={verdict}")

def check_r6(path):
    lines = read_lines(path)
    heading_re = re.compile(r'^#{1,6}\s*.*(打回修复|返修|真点修复)')
    bold_re = re.compile(r'^\s*\*\*[^*]*(打回修复|返修|真点修复)[^*]*\*\*')
    status_re = re.compile(r'(状态|Status)\s*[:：]\s*.*打回')
    heading_line = None
    status_hit = False
    for i, line in enumerate(lines):
        if heading_line is None and (heading_re.search(line) or bold_re.search(line)):
            heading_line = i
        if status_re.search(line):
            status_hit = True
    if heading_line is None and not status_hit:
        print("trigger=False/verdict=N/A")
        return
    if heading_line is not None:
        section_end = len(lines)
        for j in range(heading_line + 1, len(lines)):
            if re.match(r'^#{1,6}\s', lines[j]):
                section_end = j
                break
        section_text = '\n'.join(lines[heading_line:section_end])
        has_sha = bool(re.search(r'\b[0-9a-f]{40}\b', section_text))
        has_commit_ref = bool(re.search(r'新提交|新的?commit|新commit', section_text, re.I))
        verdict = 'PASS' if (has_sha or has_commit_ref) else 'AMBIGUOUS'
        print(f"trigger=True/reason=heading@line{heading_line+1}/has_sha={has_sha}/has_commit_ref={has_commit_ref}/verdict={verdict}")
    else:
        print("trigger=True/reason=status_field_only/has_section=False/verdict=FAIL")


def check_e2(path):
    # 行尾体检：CR 计数。用 Python 直接读字节，彻底避开本机 grep 包装(ugrep)造成的假阴性
    with open(path, 'rb') as f:
        data = f.read()
    cr = data.count(b'\r')
    print(f"cr_count={cr}/verdict={'PASS' if cr == 0 else 'FAIL'}")

if __name__ == '__main__':
    mode = sys.argv[1]
    path = sys.argv[2]
    globals()[f'check_{mode}'](path)
