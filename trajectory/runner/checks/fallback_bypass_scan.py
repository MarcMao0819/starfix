# -*- coding: utf-8 -*-
"""兜底旁路扫描 —— 找「机械判据算得出、却仍在调模型」的节点

## 由来

<日期> 在产判别力巡检抓到：p4 的判决**取决于模型当天怎么想**（同输入三跑三结果）。
根因不是模型不好，是 n11 的机械判据 `grep -E` **命中时把匹配行也打进了 stdout**，
token 变成 `<乱码原文>\nGARBLED`，两个分支都匹配不上 → 静默转兜底 → 模型接管。
更糟的是模型判词「hex_left3 是「质量」二字 UTF-8 被误按 Latin-1」然后 **pass=True**
——**认出了乱码成因，然后放行它**。

主窗把它归入战例9 同族（`grep -c || echo 0` 双输出炸门）：**存在性检查一律 -q，只要退出码。**

## 判据（两条形状），以及它自己被修了两次


v1 误报 n6（用 test 不产 stdout，我只看「&&echo||echo」模式没看产出者是谁）。
v2 误报 n14（契约是 nonempty 但**该节点根本不调 contract_ok**，判决由运行器逻辑直接产出，
   契约是死字段不参与判决）。
所以形状②必须再收一层：**宽契约只有在「该节点确实用 contract_ok 把关」时才构成问题**。
判据 = 契约 nonempty + 绑判决点 + 实现里对该节点调了 contract_ok。
"""
import json, io, re, sys, os

NOISY = re.compile(r"\b(grep|egrep|fgrep|find|ls|cat|awk|sed|head|tail|wc|git\s+grep)\b")
SILENT = re.compile(r"\bgrep\s+-[a-zA-Z]*q|\b-l\b|>\s*/dev/null")
SRC = io.open("runner/run_graph.py", encoding="utf-8").read()

def uses_contract(node_id, runner_hint):
    """该节点的实现里是否真的调了 contract_ok。粗判：在 def do_<id> 段落内出现。"""
    m = re.search(r"def do_%s\b" % re.escape(node_id), SRC)
    if not m:
        return None                      # 找不到实现（可能是通用节点走 llm_route/generic）
    seg = SRC[m.start(): m.start() + 3000]
    nxt = re.search(r"\n    def do_", seg[10:])
    if nxt:
        seg = seg[: nxt.start() + 10]
    return "contract_ok" in seg

GATE = "--promotion-gate" in sys.argv
FILES = [a for a in sys.argv[1:] if not a.startswith("--")]
gate_fail = 0

for gf in FILES:
    g = json.load(io.open(gf, encoding="utf-8"))
    rows = []
    for n in g.get("nodes", []):
        c, ct, vp, nid = n.get("cmd_template") or "", n.get("output_contract"), n.get("verdict_point"), n["id"]
        issues = []
        if re.search(r"&&\s*echo\s+\S+\s*\|\|\s*echo\s+\S+", c):
            noisy = NOISY.search(c)
            if noisy and not SILENT.search(c):
                issues.append("形状①输出被 %s 污染" % noisy.group(1))
        if ct == "nonempty" and vp:
            uc = uses_contract(nid, g)
            if uc is True:
                issues.append("形状②宽契约**且实现确实用 contract_ok 把关**")
            elif uc is False:
                pass                     # 契约是死字段，不参与判决 → 非问题
            else:
                issues.append("形状②宽契约，实现未找到（走通用节点，需人看）")
        if issues:
            rows.append((nid, vp, " ; ".join(issues)))
    print("── %s (%s): %s" % (os.path.basename(gf), g.get("version", "?"),
                              "无命中 ✓" if not rows else "%d 处" % len(rows)))
    for nid, vp, iss in rows:
        print("   %-5s %-6s %s" % (nid, str(vp), iss))
    # 转正闸：图文件里 known_gaps 非空 → 拒绝启用。清单是闸不是备注。
    kg = g.get("known_gaps") or []
    if kg:
        print("   **known_gaps %d 条未清 —— 转正闸拒绝启用**" % len(kg))
        for k in kg:
            print("     %s/%s %s" % (k.get("node"), k.get("verdict_point"), k.get("kind")))
    if GATE and (rows or kg):
        gate_fail += 1
        print("   → 转正闸：**拒绝**（扫描命中 %d、未清缺口 %d）" % (len(rows), len(kg)))
    elif GATE:
        print("   → 转正闸：放行 ✓")

if GATE:
    sys.exit(1 if gate_fail else 0)


def _gate_selftest() -> int:
    """闸自检：**判词对不算数，退出码必须也对**。

    <日期> 教训：本闸首次验证时我用 `... | tail -2` 看结果，
    判词打印「拒绝」而 `$?` 是 0 —— **管道最后一个命令(tail)的退出码盖过了脚本的**。
    差点把一个假绿的闸当成好的上线。这正是「自检必须是闸不是报告」的原样重演：
    **报告说拒绝，退出码说放行，调用方信的是退出码。**
    """
    import subprocess, os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    cases = [("graphs/changeset-audit.v5.4.json", 0, "在产件无缺口，须放行"),
             ("graphs/premerge-gate.v7.json", 1, "有 known_gaps，须拒绝"),
             ("graphs/cleanup-audit.v1.json", 1, "有 known_gaps，须拒绝")]
    ok = True
    for gf, want, why in cases:
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--promotion-gate", os.path.join(root, gf)],
                           capture_output=True, text=True, cwd=root)
        good = (p.returncode != 0) == (want != 0)
        ok &= good
        print("  %-34s 退出码=%d 期望%s %s  (%s)"
              % (gf.split("/")[-1], p.returncode, "非0" if want else "0",
                 "OK" if good else "**不符**", why))
    print("闸自检：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1


if "--gate-selftest" in sys.argv:
    sys.exit(_gate_selftest())
