#!/usr/bin/env python3
"""词表型判据：把仓内每行的候选 token 求 HMAC，与词表比对。

单独成文件而不是塞进 scrub-gate.sh 的 heredoc——heredoc 里再嵌 heredoc 容易被
提前终止（实测踩过：内层的终止符把外层也一起结束，编辑静默没落）。

输入（环境变量）：
  SCRUB_ROOT        要扫的仓根
  SCRUB_TERMS_FILE  词表（每行 HMAC-SHA256 前 16 位）
  SCRUB_SALT        盐（不入仓）

输出：每个命中一行 `  路径:行号  （命中词表；本文件不回显命中词）`；无命中则不输出。
**刻意不回显命中的词**：门禁的输出会进日志和回执，回显等于又抄了一份。
"""
import hashlib
import hmac
import os
import pathlib
import re
import sys

root = pathlib.Path(os.environ['SCRUB_ROOT'])
terms_file = pathlib.Path(os.environ['SCRUB_TERMS_FILE'])
salt = os.environ.get('SCRUB_SALT')
if not salt:
    print('词表存在但没给盐（SCRUB_SALT）', file=sys.stderr)
    sys.exit(2)

wanted = {l.strip() for l in terms_file.read_text(encoding='utf-8').splitlines()
          if l.strip() and not l.startswith('#')}


def mac(token):
    return hmac.new(salt.encode(), token.encode('utf-8'), hashlib.sha256).hexdigest()[:16]


CJK = re.compile(r'[一-鿿]+')
WORD = re.compile(r'[A-Za-z][A-Za-z0-9_-]{1,}')

bad = []
for p in sorted(root.rglob('*')):
    if not p.is_file():
        continue
    rel = p.relative_to(root).as_posix()
    # 门禁自身与词表相关文件跳过：它们要写得出这些形状才能干活
    if rel.startswith('.git/') or rel.startswith('tools/scrub-terms') \
            or rel in ('tools/scrub-gate.sh', 'tools/scrub_terms_check.py'):
        continue
    try:
        text = p.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        continue                                   # 二进制/读不了的跳过
    for lineno, line in enumerate(text.splitlines(), 1):
        cand = set()
        # 中文名/公司名没有分词边界，用 2–4 字滑窗把所有可能的名字都试一遍
        for run in CJK.findall(line):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    cand.add(run[i:i + n])
        # ASCII 词：整词 + 按 - / _ 切开的子词都要比。
        # 只比整词的话，词表里加了 marc 也抓不到 marc-b1（两者哈希不同）。
        for w in WORD.findall(line):
            cand.add(w)
            cand.update(part for part in re.split(r'[-_]', w) if len(part) > 1)
        if any(mac(tok) in wanted for tok in cand):
            bad.append(f'  {rel}:{lineno}  （命中词表；本文件不回显命中词）')

print('\n'.join(bad))
