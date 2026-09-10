#!/usr/bin/env python3
import sys, re

def read_lines(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read().split('\n')

FILE_EXT = r'(java|vue|ts|js|sql|md|yml|yaml|json|cjs|sh|py|css)'

def check_t0(path):
    lines = read_lines(path)
    text = '\n'.join(lines)
    n = len(lines) if (lines and lines[-1] != '') else len(lines) - 1
    name = path.split('/')[-1]
    name_pattern = bool(re.search(r'步骤|日志', name))
    has_verdict_section = bool(re.search(r'^#{1,6}\s*(结论|状态)\b', text, re.M)) or bool(re.search(r'结论[:：]|状态[:：]', text))
    has_cs_no = bool(re.search(r'CS-\d{8}-\d{4}', text))
    has_file_table = bool(re.search(r'^\|.*\.' + FILE_EXT + r'.*\|', text, re.M)) or bool(re.search(r'^\s*[-*]\s+.*\.' + FILE_EXT, text, re.M))
    if name_pattern or (n > 800 and not has_verdict_section):
        doc_type = 'process_log'
    elif has_cs_no and has_file_table:
        doc_type = 'code_delivery'
    else:
        doc_type = 'nondelivery'
    print(f"name_pattern={name_pattern}/lines={n}/has_verdict_section={has_verdict_section}/has_cs_no={has_cs_no}/has_file_table={has_file_table}/type={doc_type}")

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
