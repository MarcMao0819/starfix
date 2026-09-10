#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入库前的凭据扫描 —— **先证仪器，再报结论**

## 为什么单独做成一个带自检的模块

<日期> 入库时我临时拿正则扫了一遍，报「干净」。事后按自己当天立的规矩回头验，
**那套模式漏掉 4 种真凭据形态**（无引号赋值、环境变量字面量、`export` 赋值、`token: "ghp_..."`）。
也就是说：如果那 13 个文件里真有个 token，**我会照样报干净并提交**。
代舵舰长同一天在自己那边犯了同样的错，漏的是另外三种。

两条规矩（当天由这两次共同得出）：
- **没证明过会红的扫描，零命中说明不了任何事。**
- **命中太多的扫描，等于没扫**——噪声会把人训练成自动驳回，第 N 次真的也被驳回，
  而且过程中还获得了「我扫过了」的踏实感，比没扫更危险。
  （前两版模式一次给出 28 条命中，全是 `--promotion-gate` / `-poller` 之类长选项误伤。）   # secret-scan: allow

所以本模块的规矩是：**`--selftest` 不过，就不许用它的扫描结论。**

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys

# 值里出现这些，说明它是「引用」不是「字面量」——不算泄露
INDIRECTION = ("$", "os.environ", "process.env", "System.getenv", "getenv", "os.getenv")

RULES = [
    # 口令：键名像 pass/pwd，值是字面量（带不带引号都要抓）
    # 键名**不收裸 `pass`**。`PASS` 在本仓是判决词不是口令键：
    # 轮询器里 `PASS:PASS|N/A:PASS|SKIPPED_ALREADY_MERGED:PASS)` 这种 case 模式串被误报，
    # 结果是**装机方在生产代码里加了一条 allow 注释把我的闸消音**——
    # 误报的代价从来不是「多看一眼」，是**让别人学会关掉它**。
    ("明文口令", re.compile(
        r"(?i)\b(?:password|passwd|pwd|passphrase)\b\s*[:=]\s*['\"]?([^\s'\";#,)]{4,})")),
    # 环境风格赋值：KEY 里含 PASS/PWD/SECRET/TOKEN，值是字面量
    # 分隔符必须同时认 `=` 和 `:`。第一版只认 `=`，于是 `DB_PASS: Fake-Pw-Selftest` 整个漏掉——   # secret-scan: allow
    # 而 YAML/属性文件里口令恰恰是冒号写法。**自检当场把这条抓出来了。**
    # 顺带记一笔为什么「明文口令」那条也接不住它：`DB_PASS` 里 PASS 前面是下划线，
    # `\bpass\b` 的词边界不成立。**键名是拼接的时候，词边界会骗人。**
    ("环境赋值字面量", re.compile(
        r"(?i)\b(?:export\s+)?[A-Z0-9_]*(?:PASS|PWD|SECRET|TOKEN|APIKEY|API_KEY)[A-Z0-9_]*\s*[:=]\s*"
        r"['\"]?([^\s'\";#,)]{5,})")),
    # 密钥/令牌：键名像 secret/api_key/token
    ("密钥或令牌", re.compile(
        r"(?i)\b(?:secret|api[_-]?key|access[_-]?token|token)\b\s*[:=]\s*['\"]?([^\s'\";#,)]{8,})")),
    # 供应商前缀，键名无关
    ("供应商令牌前缀", re.compile(r"\b(ghp_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})")),
    # mysql -p 紧跟字面口令。**大小写敏感**：-p 是口令、-P 是端口，混了就把端口号报成口令。
    # 前面不能是字母或连字符，否则 --promotion-gate / -poller 全中招。   # secret-scan: allow
    # 字符集要含 `-`：真口令带连字符很常见（自检夹具换成假值后当场漏掉，就是这个原因）。
    # 加 `-` 不会误伤长选项——前面的 (?<![-\w]) 已经保证 `-p` 不是 `--promotion` 的一截。
    ("mysql -p 字面口令", re.compile(r"(?<![-\w])-p(?![\"'$\s])([A-Za-z0-9_!@#.-]{5,})")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# **夹具里一律用一眼可辨的假值。** 第一版我图省事，直接把真实的 MySQL root 口令
# 当样本写进来并提交了——扫描器自己成了泄露源。更难看的是：我当时**跑过自扫、
# 看见它报了 17 条命中，然后照样提交**。
# 「跑了检查」和「按检查结果行动」是两件事，我做了前一件就当自己做完了。
TRUE_FORMS = [
    'password = "Fake-Pw-Selftest"',   # secret-scan: allow
    'mysql -uroot -pFake-Pw-Selftest db',   # secret-scan: allow
    'DB_PASS: Fake-Pw-Selftest',   # secret-scan: allow
    'DB_PASS: Fake-Pw-Unquoted',   # secret-scan: allow
    'export MYSQL_PWD=Fake-Pw-Unquoted',   # secret-scan: allow
    'MYSQL_ROOT_PASSWORD=Fake-Pw-Unquoted',   # secret-scan: allow
    "api_key = 'sk-FAKESELFTESTKEY01'",   # secret-scan: allow
    'token: "ghp_FAKESELFTESTTOKEN01"',   # secret-scan: allow
    '-----BEGIN RSA PRIVATE KEY-----',   # secret-scan: allow
]
FALSE_FORMS = [
    'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" db',   # secret-scan: allow
    'mysql -uroot -p "$PW" db',   # secret-scan: allow
    'python3 x.py --promotion-gate',   # secret-scan: allow
    'bash audit-poller.sh --once',   # secret-scan: allow
    'mysql -h127.0.0.1 -P3306 db',   # secret-scan: allow
    'PASSWORD_ENV_VAR = os.environ.get("MYSQL_ROOT_PASSWORD")',   # secret-scan: allow
    'CS-<日期>-POLLERFIX5',   # secret-scan: allow
    'LEDGER = os.environ.get("B_CONSUME_LEDGER", default)',
    'PASS:PASS|N/A:PASS|SKIPPED_ALREADY_MERGED:PASS)',   # secret-scan: allow
    'case "$overall:$gate_verdict" in',   # secret-scan: allow
]


# 行内豁免标记。**扫描器自己的夹具必然会命中自己**——夹具不被命中，自检就没意义。
# 所以需要一个**显式、可见、逐行**的豁免，而不是「整个文件跳过」那种一豁一大片的写法。
# 代价心里要有数：任何豁免都是绕过闸的口子，所以它必须留在代码里被人看见，
# 且自检里要有一条反例证明**没标记的行不会被顺带豁免**。
ALLOW_MARK = "secret-scan: allow"


def scan_text(s: str) -> list[tuple[str, str]]:
    hits = []
    lines = s.split("\n")
    starts, pos = [], 0
    for ln in lines:                      # 每行起始偏移，用来把命中定位回行
        starts.append(pos)
        pos += len(ln) + 1
    import bisect
    for name, pat in RULES:
        for m in pat.finditer(s):
            val = m.group(1) if m.groups() else m.group(0)
            if any(t in val for t in INDIRECTION) or "." in val:
                continue          # 值是引用/带点的标识符，不是字面口令
            if "|" in val:
                continue          # 值里有 `|` 是枚举/模式串（case 分支、正则），不是口令
            i = bisect.bisect_right(starts, m.start()) - 1
            if 0 <= i < len(lines) and ALLOW_MARK in lines[i]:
                continue          # 本行显式豁免
            hits.append((name, m.group(0)[:70]))
    return hits


def selftest() -> int:
    ok = True
    print("【真凭据形态必须全红】")
    for s in TRUE_FORMS:
        h = scan_text(s)
        ok &= bool(h)
        print("  %-48s %s" % (s[:48], [n for n, _ in h] or "**漏**"))
    print("\n【易混形态必须全不红】")
    for s in FALSE_FORMS:
        h = scan_text(s)
        ok &= not h
        print("  %-48s %s" % (s[:48], "不报 OK" if not h else "**误报** %s" % [n for n, _ in h]))
    print("\n【行内豁免：只豁免标了的那一行】")
    marked = 'password = "Fake-Pw-Selftest"   # ' + ALLOW_MARK   # secret-scan: allow
    unmarked = 'password = "Fake-Pw-Selftest"'   # secret-scan: allow
    a, b = scan_text(marked), scan_text(unmarked)
    good = (not a) and bool(b)
    ok &= good
    print("  标了豁免的行 → %s（须不报）" % ([n for n, _ in a] or "不报 OK"))
    print("  没标的同样内容 → %s（须照报，豁免不许外溢）" % ([n for n, _ in b] or "**漏**"))

    print("\n自检：%s" % ("仪器合格，扫描结论可用" if ok else "**仪器不合格，其扫描结论一律作废**"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="入库前凭据扫描（先证仪器再报结论）")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.files:
        ap.error("需要文件清单")
    if selftest_quiet() != 0:
        print("**自检未过，拒绝出扫描结论**")
        return 2
    total = 0
    for f in a.files:
        if not os.path.isfile(f):
            continue
        try:
            s = io.open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for name, snip in scan_text(s):
            total += 1
            print("  %s  [%s]  %s" % (f, name, snip))
    print("命中 %d 条%s" % (total, "" if total else "（仪器已自证合格，此 0 有意义）"))
    return 1 if total else 0


def selftest_quiet() -> int:
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return selftest()


if __name__ == "__main__":
    sys.exit(main())
