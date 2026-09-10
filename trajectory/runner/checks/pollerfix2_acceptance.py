#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""POLLERFIX2 独立复核 —— 轨迹线（舰员癸）自己跑，不采信任何转述

三条用例对应 <日期> 体检查出的三个洞：
  ① 字典序游标结构盲区  → 集合差修法是否真的把盲区单捞起来了
  ② 定型闸没继承        → 是否接回 A 门之前
  ③ 「静默即正常」无判别力 → 心跳是否真在跳

**每条都写明它证不了什么**，免得把「我这条绿了」当成「整件事对了」。
只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

PILOT = "${TRAJ_DATA_DIR}/changeset-audit"
ROOT = "${TRAJ_HOME}"
POLLER = os.path.join(ROOT, "runner", "audit-poller.sh")
SETTLE = os.path.join(ROOT, "runner", "checks", "registration_settle.py")
HEARTBEAT = os.path.join(PILOT, ".audit-poller.heartbeat")

# 改前基线：<日期> 12:0x 实测「无判决指纹」的四张真盲区单。
# 写死而不是现算——现算就没有对照，绿了也说明不了是修法起的作用。
BLIND_BEFORE = ["CS-<日期>-0028", "CS-<日期>-0029", "CS-<日期>-0030", "CS-<日期>-0011"]


def fingerprinted() -> set[str]:
    out = set()
    for line in io.open(os.path.join(PILOT, "判决指纹.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("changeset_no"):
            out.add(d["changeset_no"])
    return out


# ── 沙箱自证闸：每次沙箱跑轮询器，前后比生产台账指纹，动了就当场炸 ──
# <日期> 事故：`drift_scan.py` 把台账路径写死在源码里、不看 AUDITPOLLER_PILOT，
# 于是沙箱设了一整圈环境变量，指纹落账仍从后门直通生产，连写 18 条才被发现。
# **根因不是那个后门，是没有任何一处在问「我这一跑到底动了谁」。**
# 隔离是个前提，而前提和判据一样——**没被证伪过的，就不算验过**。
ALERTDIR = os.environ.get("ALERTDIR", "${FLEET_HOME}/<项目>ERP/迁移备份/回执")
GUARDED = ["判决指纹.jsonl", "机检判决.jsonl", "A门判定.jsonl", "判决失效.jsonl",
           "告警撤回留痕.jsonl", "B消费留痕.jsonl"]


def _prod_state() -> dict:
    st = {}
    for f in GUARDED:
        fp = os.path.join(PILOT, f)
        st[f] = hashlib.sha256(io.open(fp, "rb").read()).hexdigest() if os.path.exists(fp) else None
    st["<告警目录文件数>"] = len(os.listdir(ALERTDIR)) if os.path.isdir(ALERTDIR) else None
    return st


def _prod_lines() -> dict:
    out = {}
    for f in GUARDED:
        fp = os.path.join(PILOT, f)
        try:
            out[f] = io.open(fp, encoding="utf-8", errors="replace").read().split("\n")
        except Exception:
            out[f] = []
    return out


def run_sandboxed(argv: list[str], env: dict):
    """沙箱里跑轮询器。**跑完必须证明生产没被碰**，碰了就抛异常停跑。

    但「生产台账变了」不等于「是我写的」——**生产轮询器重挂之后每 60 秒也在写**。
    分不清这两者，闸就会在轮询器活着时天天误报，然后被人关掉；
    分得太松，又会把自己的泄露算到别人头上。
    所以变了之后要**归因**：把新增行拿去比对本次沙箱自己的台账，
    **新增行也出现在沙箱里 = 我写的（真泄露）；只在生产里 = 别人写的（放行并说明）**。
    """
    before, before_lines = _prod_state(), _prod_lines()
    r = subprocess.run(argv, capture_output=True, text=True, env=env)
    after, after_lines = _prod_state(), _prod_lines()
    moved = [k for k in before if before[k] != after[k]]
    if not moved:
        return r
    mine, theirs = [], []
    sandbox_pilot = env.get("AUDITPOLLER_PILOT", "")
    for f in moved:
        if f == "<告警目录文件数>":
            theirs.append(f)
            continue
        added = [l for l in after_lines.get(f, []) if l not in set(before_lines.get(f, []))]
        sb = os.path.join(sandbox_pilot, f)
        sb_txt = io.open(sb, encoding="utf-8", errors="replace").read() if os.path.exists(sb) else ""
        (mine if any(l and l in sb_txt for l in added) else theirs).append(f)
    if mine:
        raise RuntimeError("**沙箱写到生产了**：%s —— 已停跑，先修隔离再谈判据" % "、".join(mine))
    print("  〔闸〕生产台账有变动但归因为**在跑的轮询器**，非本沙箱：%s" % "、".join(theirs),
          file=sys.stderr)
    return r


# 豁免裁定日：<日期>（TRJRULE-20260817 裁一·甲，名单当天 13:34 重建）。
# 判据只管**裁定生效之后**有没有新的豁免单被判——当天及更早的 17 条是「发现问题时正在判的那批」，
# 它们正是画出这条基线的原因，不是违规。
EXEMPT_RULING_DAY = os.environ.get("TRJ_EXEMPT_RULING_DAY", "<日期>")


def case1() -> tuple[bool, list[str]]:
    """① 基线豁免存量，在豁免裁定生效之后不得再被判。

    ## 这条判据今天被我改了两次，第二次才对

    原判据里有半条「09-01 那 7 张盲区单一张不少」。那 7 张当天就判完了，
    **这半条从此不可能红**——每次跑都点个头，不再证明任何事。
    **一条永远绿的判据留在验收里比没有更糟：它让「N 条全过」看起来比实际更有分量。** 摘掉。

    换成「豁免名单 ∩ 指纹台账 = 空」之后**当场翻红 17 条**。查了才知道那 17 条全在
    <日期> 13:26–14:22——**就是发现问题、画出这条基线的那两小时里判的**，是历史不是泄漏。
    一条上线第一天就红、而且红得没道理的判据，下场只有被人忽略，**跟永远绿是同一种废**。
    所以按裁定日切：裁定生效之后再有豁免单被判，才叫泄漏。

    留下的这条**仍然会红**：任何一张豁免单在今天被判，它立刻翻红——
    而那正是 09-01 差点重演的事故（集合差漏了基线豁免，重挂第一拍要刷 307 张）。

    证不了：这些单的代码内容被审过了。本例只管「不该判的没被判」。
    """
    lines = []
    exempt = set()
    for l in io.open(os.path.join(PILOT, ".baseline_exempt"), encoding="utf-8"):
        l = l.strip()
        if l and not l.startswith("#"):
            exempt.add(l.split()[0])
    seen = {}
    for line in io.open(os.path.join(PILOT, "判决指纹.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        no, ts = d.get("changeset_no"), str(d.get("ts", "")).replace("T", " ")
        if no:
            seen.setdefault(no, []).append(ts)
    # 取数哨兵：名单或台账读空，「没有违规」就会**无条件为真**——那不是通过，是没得判。
    if len(exempt) < 300 or len(seen) < 700:
        return False, ["取数哨兵：豁免名单 %d 条、指纹台账 %d 单，低于下限，拒绝出结论"
                       % (len(exempt), len(seen))]
    hist, leak = 0, []
    for no in sorted(exempt & set(seen)):
        for ts in seen[no]:
            if ts[:10] >= EXEMPT_RULING_DAY:
                leak.append("%s@%s" % (no, ts[:19]))
            else:
                hist += 1
    lines.append("豁免名单 %d 条；其中有指纹的属**裁定日(%s)之前**的历史 %d 条，不算违规"
                 % (len(exempt), EXEMPT_RULING_DAY, hist))
    lines.append("裁定生效后被判的豁免单（这才是泄漏）：%s" % (leak[:8] if leak else "无"))
    return (not leak), lines


def case2() -> tuple[bool, list[str]]:
    """② 定型闸是否接回 A 门之前。三层一起看，缺一层都能被糊弄过去。

    证不了：0117 当时那一刻会被判「未定型」。**该时点在生产上已不可复现**——
    登记早已定型，今天跑它只会得 SETTLED。所以行为侧靠模块自检的合成反例，
    实证侧只能验「已定型的单确实放行」这一侧。这条要如实说，不能拿 SETTLED
    冒充「定型闸拦住过 0117」。
    """
    lines, ok = [], True
    src = io.open(POLLER, encoding="utf-8").read() if os.path.exists(POLLER) else ""
    has = "registration_settle" in src
    lines.append("代码层：audit-poller.sh 引用 registration_settle = %s" % has)
    ok &= has
    if has:
        # 这里第一版写错过：拿「registration_settle 这个词在文件里第一次出现的位置」
        # 跟 registration_gate 比。可两个词的首次出现都是**变量定义**（REG_GATE=25 行、
        # REG_SETTLE=26 行），定义顺序跟执行顺序毫无关系，于是判出一个假 FAIL。
        # 又是「只看了模式像不像，没看产出者是谁」那个形状。改成比**调用点**。
        def call_line(var: str) -> int:
            for i, l in enumerate(src.split("\n"), 1):
                if 'python3 "$%s"' % var in l:
                    return i
            return -1
        ls, lg = call_line("REG_SETTLE"), call_line("REG_GATE")
        order = ls > 0 and lg > 0 and ls < lg
        lines.append("顺序：定型闸调用点(%s 行) 在 A 门调用点(%s 行) 之前 = %s"
                     "　※文本判据，定义顺序≠控制流，权威判读归 4C03 互审" % (ls, lg, order))
        ok &= order

    # 行为反例：把定型闸换成「永远 UNSETTLED」的桩，跑一单，
    # **必须停在定型闸之前**——不产生指纹、不落 A 门判定。
    # 只验「已定型放行」那一侧不算数：那一侧不管定型闸有没有真接上都会绿。
    import tempfile
    sandbox = tempfile.mkdtemp()
    stub = os.path.join(sandbox, "always_unsettled.py")
    io.open(stub, "w", encoding="utf-8").write(
        # 退出码必须是 1。桩第一版写了 exit 0，结果走进「exit 0 但输出不是 SETTLED」
        # 那条错误分支，我差点据此说 E060 把未定型错报成 RUNNER_ERR——
        # 核了 registration_settle.py 第 239 行 `return 0 if settled else 1` 才知道
        # **是我的桩不符合契约，不是它的代码有问题**。桩也要照契约写，否则测的是自己造的假象。
        "#!/usr/bin/env python3\nimport sys\nprint('UNSETTLED 自检桩：假装明细还没写完')\nsys.exit(1)\n")
    pilot = os.path.join(sandbox, "pilot")
    os.makedirs(pilot)
    for f in ("判决指纹.jsonl", "机检判决.jsonl", "A门判定.jsonl"):
        io.open(os.path.join(pilot, f), "w", encoding="utf-8").write("")
    # 新版 require_files 把 .baseline_exempt 也列进了前置检查。沙箱不备这个文件，
    # 就会停在 preflight——那时「没落指纹」是被前置检查挡的，**证不了定型闸**。
    # 探针要跟着被测对象的契约走，被测对象加了必需文件，探针也得跟上。
    io.open(os.path.join(pilot, ".baseline_exempt"), "w", encoding="utf-8").write("")
    io.open(os.path.join(pilot, ".audit-poller.cursor"), "w", encoding="utf-8").write("CS-<日期>-0067")
    # 沙箱要有自己的 runtime.db，否则 preflight 就把它挡了——那样「停住了」
    # 是被前置检查停的，不是被定型闸停的，**证不了定型闸**。
    # 第一版就栽在这儿：指纹 0 字节看着像通过，实际测的根本不是要测的那道闸。
    import shutil
    shutil.copy(os.path.join(ROOT, "runtime.db"), env_db := os.path.join(sandbox, "iso.db"))
    env = dict(os.environ,
               AUDITPOLLER_REG_SETTLE=stub,
               AUDITPOLLER_PILOT=pilot,
               AUDITPOLLER_ALERT_DIR=os.path.join(sandbox, "alerts"),
               AUDITPOLLER_WORKDIR=os.path.join(sandbox, "wd"),
               AUDITPOLLER_RUNTIME_DB=env_db,
               TRJ_DB_PATH=env_db,
               TRJ_PILOT_DIR=pilot, TRJ_LEDGER=os.path.join(pilot, "判决指纹.jsonl"),
               TRJ_INVALID=os.path.join(pilot, "判决失效.jsonl"))
    os.makedirs(env["AUDITPOLLER_ALERT_DIR"], exist_ok=True)
    r = run_sandboxed(["bash", POLLER, "--audit-one", "CS-<日期>-0067"], env)
    fpsz = os.path.getsize(os.path.join(pilot, "判决指纹.jsonl"))
    # 未定型是**正常延后**，不是错误：期望「不判、不落指纹、且不报 RUNNER_ERR」。
    # 只验「没落指纹」不够——preflight 挂掉时也没落指纹，那样测的根本不是这道闸。
    out = (r.stdout + r.stderr)
    blocked = fpsz == 0 and "RUNNER_ERR" not in out and r.returncode != 2
    lines.append("行为反例：定型闸桩恒判未定型 → 必须静默延后（不判/不落指纹/不报错）= %s"
                 "（沙箱指纹 %d 字节；rc=%d；输出=%s）"
                 % (blocked, fpsz, r.returncode, (out.strip() or "<空>")[:80]))
    if "AUDITPOLLER_REG_SETTLE" not in src:
        lines.append("　※注入口 AUDITPOLLER_REG_SETTLE 不存在，本反例无从施加——**按不通过计**")
        blocked = False
    ok &= blocked
    p = subprocess.run([sys.executable, SETTLE, "--selftest"], capture_output=True, text=True)
    sok = "自检：全部符合" in p.stdout
    lines.append("行为层：定型闸自检（含未定型反例）= %s" % ("全绿" if sok else "**不绿**"))
    ok &= sok
    p2 = subprocess.run([sys.executable, SETTLE, "CS-<日期>-0117"], capture_output=True, text=True)
    settled = p2.stdout.strip().startswith("SETTLED")
    lines.append("实证层（仅一侧）：0117 现已定型应放行 = %s" % settled)
    ok &= settled
    return ok, lines


def case4(deep: bool = False) -> tuple[bool, list[str]]:
    """④ 基线豁免：三态来回切（默认→空→默认），证明 7 这个数**随名单变化**。

    只验「加名单后是 7」不够——那句话在名单根本没被读到、而集合差恰好也是 7 的
    世界里同样成立。跑成三态才把「碰巧等于 7」的退路堵死。
    这条守的是 <日期> 那次差点重演的事故：集合差漏了基线豁免，
    重挂第一拍会捞 314 张，其中 307 张是早已裁定豁免的历史存量。

    两个探针坑，都是我自己踩过的，写在这里免得后人重踩：
    - **读心跳前必须先删旧心跳**。否则本轮没跑成时读到的是上一轮的值，
      两次得同一个数看着像「开关不生效」，其实是「这一轮根本没写过」。
    - **空名单要用真的空文件，不能用 /dev/null**。对方 require_files 用 `[ -f ]` 判，
      字符设备不是普通文件，会在 preflight 就被挡下——那测的是探针不是被测对象。
    """
    # ── 日常档：纯算术不变量，不跑 poller，毫秒级 ──
    # 门禁的执行成本决定它会不会被绕过：一个要等三分钟的闸，迟早没人跑。
    # 所以拆两档——日常档验「名单该挡的量算得对」，深度档才验「脚本真的照名单办事」。
    # 深度档要跑三轮 poller、其中一轮过 307 张单，只在改动豁免逻辑时才值得付这个钱。
    exempt = set()
    for l in io.open(os.path.join(PILOT, ".baseline_exempt"), encoding="utf-8"):
        l = l.strip()
        if l and not l.startswith("#"):
            exempt.add(l.split()[0])
    fp0 = fingerprinted()
    gap = len(exempt - fp0)
    lines = ["日常档·名单里尚未判过的条数 = %d（这就是名单该挡掉的量）" % gap]
    if not deep:
        if gap == 0:
            return False, lines + ["**此刻无判别力**：名单里已无未判条目，挡与不挡一样，判 N/A 不判绿",
                                   "（要证明脚本真的照名单办事，跑 --deep）"]
        return True, lines + ["深度档未跑：本档只证算术，**未证脚本真的读了名单**。改动豁免逻辑时请跑 --deep"]

    import shutil, tempfile
    sb = tempfile.mkdtemp()
    pilot = os.path.join(sb, "pilot")
    os.makedirs(pilot)
    for f in ("判决指纹.jsonl", ".baseline_exempt"):
        shutil.copy(os.path.join(PILOT, f), pilot)
    for f in ("机检判决.jsonl", "A门判定.jsonl"):
        io.open(os.path.join(pilot, f), "w", encoding="utf-8").write("")
    io.open(os.path.join(pilot, ".audit-poller.cursor"), "w", encoding="utf-8").write("CS-<日期>-0067")
    db = os.path.join(sb, "iso.db")
    shutil.copy(os.path.join(ROOT, "runtime.db"), db)
    stub = os.path.join(sb, "always_unsettled.py")
    io.open(stub, "w", encoding="utf-8").write(
        "#!/usr/bin/env python3\nimport sys\nprint('UNSETTLED 复核桩：一律延后，只数清单长度')\nsys.exit(1)\n")
    empty = os.path.join(sb, "empty_exempt")
    io.open(empty, "w", encoding="utf-8").write("")
    hb = os.path.join(pilot, ".audit-poller.heartbeat")

    def picked(exempt_path):
        if os.path.exists(hb):
            os.remove(hb)                 # 先删：不删就分不清新心跳与残留文件
        env = dict(os.environ, AUDITPOLLER_PILOT=pilot,
                   AUDITPOLLER_ALERT_DIR=os.path.join(sb, "alerts"),
                   AUDITPOLLER_WORKDIR=os.path.join(sb, "wd"),
                   AUDITPOLLER_RUNTIME_DB=db, TRJ_DB_PATH=db,
                   AUDITPOLLER_REG_SETTLE=stub,
                   AUDITPOLLER_BASELINE_EXEMPT=exempt_path,
                   TRJ_PILOT_DIR=pilot, TRJ_LEDGER=os.path.join(pilot, "判决指纹.jsonl"),
                   TRJ_INVALID=os.path.join(pilot, "判决失效.jsonl"))
        os.makedirs(env["AUDITPOLLER_ALERT_DIR"], exist_ok=True)
        run_sandboxed(["bash", POLLER, "--once"], env)
        if not os.path.exists(hb):
            return None                   # 本轮没写心跳 = 没跑成，不许拿旧值顶上
        return json.loads(io.open(hb, encoding="utf-8").read()).get("new_picked")

    # 期望值**不许写死**。第一版写了 7/314——那是重挂前那一刻的数，
    # 而修法生效后那 7 张正好被判掉、数就变了：**一条在修法成功后必然翻红的判据，
    # 本身就是错的判据**。改成现算不变量：两侧的差额必须等于「名单里尚未被判过的条数」。
    exp_gap = gap          # 名单挡掉的正是这些（日常档已算过，不重复取数）
    a = picked(os.path.join(pilot, ".baseline_exempt"))
    b = picked(empty)
    c = picked(os.path.join(pilot, ".baseline_exempt"))
    lines += ["深度档·默认名单 → %s" % a,
              "深度档·名单置空 → %s" % b,
              "深度档·切回默认 → %s" % c]
    if exp_gap == 0:
        lines.append("**本判据此刻无判别力**：名单里已无未判条目，置空与否都一样，判 N/A 不判绿")
        return False, lines
    ok = (a == c) and (b - a == exp_gap)
    lines.append("判据：两侧差额 %s 必须等于 %d，且来回切回得去（%s==%s）"
                 % (b - a if isinstance(a, int) and isinstance(b, int) else "?", exp_gap, a, c))
    return ok, lines


def case5() -> tuple[bool, list[str]]:
    """⑤ 通路三兜底：不进指纹的字段（典型是标题）原地改，必须仍能被捞起重判。

    **为什么正例不用改库就能测**：这条判据的输入不是「有人改了标题」，
    而是「有单的 update_time 晚于扫描水位线、且指纹没变」。既然水位线是参数化的，
    **把水位线回拨到过去**就能合法造出这个输入——用的是库里已有的真实 update_time，
    `${DB_NAME}` 全程只读。
    这一点值得记：**要造某个输入，先看判据真正读的是什么**；
    盯着「标题」就会得出「必须写库」的结论，盯着判据实际读的那两个量就不必。

    证不了：真有人改标题时业务上会发生什么。本例只证通路捞得起来。
    """
    lines = []
    src = io.open(POLLER, encoding="utf-8").read() if os.path.exists(POLLER) else ""
    # 水位线注入口的名字由施工方定，这里按形态发现，不猜死名字
    m = re.findall(r"(AUDITPOLLER_[A-Z0-9_]*(?:UPDATE|WATERMARK|SCAN_MARK)[A-Z0-9_]*)", src)
    if not m:
        return False, ["水位线注入口未出现在脚本里（找过 AUDITPOLLER_*UPDATE*/*WATERMARK*/*SCAN_MARK*）"
                       "——**注入口没有就无从施加两侧检验，按不通过计**，不是「暂时没法测所以先放过」"]
    var = sorted(set(m))[0]
    lines.append("水位线注入口：%s" % var)

    import shutil, tempfile
    sb = tempfile.mkdtemp()
    pilot = os.path.join(sb, "pilot")
    os.makedirs(pilot)
    for f in ("判决指纹.jsonl", ".baseline_exempt"):
        shutil.copy(os.path.join(PILOT, f), pilot)
    for f in ("机检判决.jsonl", "A门判定.jsonl"):
        io.open(os.path.join(pilot, f), "w", encoding="utf-8").write("")
    # 游标推到最大，把「新单通路」这条路堵死——否则捞起来的单分不清是哪条通路捞的。
    io.open(os.path.join(pilot, ".audit-poller.cursor"), "w", encoding="utf-8").write("CS-99999999-9999")
    db = os.path.join(sb, "iso.db")
    shutil.copy(os.path.join(ROOT, "runtime.db"), db)
    mark = os.path.join(sb, "watermark")
    hb = os.path.join(pilot, ".audit-poller.heartbeat")

    def tick(watermark_value):
        io.open(mark, "w", encoding="utf-8").write(watermark_value)
        if os.path.exists(hb):
            os.remove(hb)                 # 先删：不删就分不清新心跳与残留文件
        env = dict(os.environ, AUDITPOLLER_PILOT=pilot,
                   AUDITPOLLER_ALERT_DIR=os.path.join(sb, "alerts"),
                   AUDITPOLLER_WORKDIR=os.path.join(sb, "wd"),
                   AUDITPOLLER_RUNTIME_DB=db, TRJ_DB_PATH=db,
                   AUDITPOLLER_BASELINE_EXEMPT=os.path.join(pilot, ".baseline_exempt"),
                   TRJ_PILOT_DIR=pilot, TRJ_LEDGER=os.path.join(pilot, "判决指纹.jsonl"),
                   TRJ_INVALID=os.path.join(pilot, "判决失效.jsonl"))
        env[var] = mark
        os.makedirs(env["AUDITPOLLER_ALERT_DIR"], exist_ok=True)
        run_sandboxed(["bash", POLLER, "--once"], env)
        if not os.path.exists(hb):
            return None
        return json.loads(io.open(hb, encoding="utf-8").read())

    def sandbox_fp():
        out = set()
        for line in io.open(os.path.join(pilot, "判决指纹.jsonl"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("changeset_no"):
                out.add(d["changeset_no"])
        return out

    # 水位线取「库里最大 update_time 减 1 秒」——只让最后那一批合格，积压小、跑得快。
    # 第一版我写死 18:00，结果 18:00 之后有二十几张单排队，
    # 于是「零改动跑第二拍必须为 0」当场翻红——**红的是我的判据不是它的代码**：
    # 第二拍捞到的是队列里的下一张，那叫排空积压，不叫重判。
    # 不用 DATE_FORMAT：`%` 要穿过 Python 格式化和两层 shell 引号，第一版就是这么写空的。
    # MAX(update_time) 本身就是 'YYYY-MM-DD HH:MM:SS'，少一层转义少一处坑。
    q = "SELECT MAX(update_time) FROM t_code_changeset WHERE is_del=0 AND update_time IS NOT NULL"
    cmd = ("/usr/local/bin/docker exec ${DB_CONTAINER} sh -c "
           "'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" -N -B ${DB_NAME} -e \"%s\"'" % q)
    pr = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    mx = (pr.stdout or "").strip().split("\n")[-1].strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", mx):
        return False, lines + ["取不到库里最大 update_time（得到 %r），拒绝出结论" % mx]
    import datetime as _dt
    wm = (_dt.datetime.strptime(mx, "%Y-%m-%d %H:%M:%S") - _dt.timedelta(seconds=1)
          ).strftime("%Y-%m-%d %H:%M:%S")
    lines.append("库内最大 update_time=%s，水位线取其前 1 秒=%s" % (mx, wm))

    # 正例：水位线回拨 → 必须捞起。用的是库里已有的真实 update_time，**只读**。
    seen, rounds, repeat = set(), [], []
    hb1 = tick(wm)
    n1 = (hb1 or {}).get("update_picked")
    lines.append("正例·水位线回拨 1 秒 → update_picked=%s（须 ≥1）" % n1)
    seen |= sandbox_fp()
    rounds.append(n1)

    # 反例：不做任何改动继续跑，直到不再捞。判据不是「第二拍必须为 0」——
    # 那在有积压时是错的；判据是**同一张单不许被捞两次，且必须收敛到 0**。
    for _ in range(5):
        cur = io.open(mark, encoding="utf-8").read().strip()
        # 变量名不能撞 `hb`——那是心跳**文件路径**。第一版在这里写了 `hb = tick(cur)`，
        # 把路径变量覆盖成了 dict，下一轮 tick() 里 os.path.exists(hb) 直接 TypeError。
        # 与记忆里 MySQL 触发器同名变量遮蔽是同一族：**同名覆盖，读到的不是你以为的那个东西**。
        hb_round = tick(cur)
        n = (hb_round or {}).get("update_picked")
        rounds.append(n)
        now_fp = sandbox_fp()
        again = [x for x in (now_fp & seen) if False]      # 指纹是集合，重复判会再落一行
        seen |= now_fp
        if n == 0:
            break
    # 「必须收敛到 0」**不是活系统上的合法不变量**：实现里遇到未定型候选就不推进水位线
    # （注释写明理由：它在指纹不变时没有别的触发），那是**设计上正确的行为**。
    # 我第一版把它当硬闸，结果卡在一张当时还没定型的单上翻红——红的是判据不是代码。
    # 正确形态是二选一：收敛到 0，**或者**没收敛但确实是被「延后」挡住的。
    blocked_by_defer = bool((hb_round or {}).get("deferred")) if rounds[-1] != 0 else False
    converged = rounds[-1] == 0
    lines.append("连续空跑各拍 update_picked=%s（须收敛到 0，或未收敛但确系被未定型候选挡住）"
                 % rounds)
    if not converged:
        lines.append("　未收敛，挡住它的延后单=%s（非空即为设计行为，不算失败）"
                     % ((hb_round or {}).get("deferred") or "**空——那就是真没推进水位线**"))

    # 「同一张单被判两次」直接数指纹台账里的重复行，比看集合可靠
    from collections import Counter
    cnt = Counter()
    for line in io.open(os.path.join(pilot, "判决指纹.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("changeset_no"):
            cnt[d["changeset_no"]] += 1
    base_cnt = Counter()
    for line in io.open(os.path.join(PILOT, "判决指纹.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("changeset_no"):
            base_cnt[d["changeset_no"]] += 1
    repeat = sorted(k for k, v in cnt.items() if v - base_cnt.get(k, 0) > 1)
    lines.append("被重复重判的单：%s（须为空——同一 update_time 只重判一次）" % (repeat or "无"))

    # 反例二：水位线设到未来，必须零动作
    fwd = tick("<日期> 00:00:00")
    got2 = (fwd or {}).get("update_picked")
    lines.append("反例·水位线设到未来 → update_picked=%s（须 =0）" % got2)

    # 反例三：**首次运行**（状态文件不存在）必须从右界播种，不许把历史全捞一遍。
    # 这一侧是代舵舰长点的，我原本没想到：库里既然有一批 update_time 排着队，
    # 那「第一次跑时水位线初始化成什么」就决定了会不会重演 307 风暴——
    # 若默认 epoch，重挂第一拍就把全库扫一遍，跟基线豁免那次是同一形状。
    # 判据两件事一起看：**捞 0 张** 且 **状态文件被创建成右界**（不是留空、也不是 epoch）。
    if os.path.exists(mark):
        os.remove(mark)
    if os.path.exists(hb):
        os.remove(hb)
    env0 = dict(os.environ, AUDITPOLLER_PILOT=pilot,
                AUDITPOLLER_ALERT_DIR=os.path.join(sb, "alerts"),
                AUDITPOLLER_WORKDIR=os.path.join(sb, "wd"),
                AUDITPOLLER_RUNTIME_DB=db, TRJ_DB_PATH=db,
                AUDITPOLLER_BASELINE_EXEMPT=os.path.join(pilot, ".baseline_exempt"),
                   TRJ_PILOT_DIR=pilot, TRJ_LEDGER=os.path.join(pilot, "判决指纹.jsonl"),
                   TRJ_INVALID=os.path.join(pilot, "判决失效.jsonl"))
    env0[var] = mark
    run_sandboxed(["bash", POLLER, "--once"], env0)
    seeded = io.open(mark, encoding="utf-8").read().strip() if os.path.exists(mark) else None
    hb0 = json.loads(io.open(hb, encoding="utf-8").read()) if os.path.exists(hb) else {}
    got3 = hb0.get("update_picked")
    seed_ok = bool(seeded) and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", seeded) \
        and seeded >= mx
    lines.append("反例·首次运行(删状态文件) → update_picked=%s（须 =0）；播种值=%r（须为右界快照，≥ 库内最大 %s）"
                 % (got3, seeded, mx))

    ok = ((isinstance(n1, int) and n1 >= 1) and (converged or blocked_by_defer)
          and not repeat and got2 == 0 and got3 == 0 and seed_ok)
    return ok, lines


def case6() -> tuple[bool, list[str]]:
    """⑥ 积压窗口里，已成功的单跨拍不得重判（4C03 在 POLLERREV2 抓到的缺陷）。

    **为什么⑤没抓到它**：⑤ 的正例是**单张单独在窗口里**，两拍当然不会重判。
    这个缺陷只在「同一窗口里同时有成功单和延后单」时显形——
    窗口内任一张 DEFERRED 就整窗不推进水位线，下一拍同窗重开，
    已成功那张仍满足条件，于是再跑图、再追一行指纹，**无上限增长**。
    我的夹具里没有积压场景，判据就抓不到只在积压下现形的错。

    今天这句正好反过来出现两次：上午我写了一条**在有积压时根本不成立**的判据（收敛到 0）；
    这里是一条**只在没积压时才被检验**的判据。**夹具的简单程度决定了判据的覆盖上限。**

    构造：定型桩只对 `CS-<日期>-0117` 判 SETTLED，其余一律 UNSETTLED（合法延后）。
    水位线回拨到 0117 的 update_time 前 1 秒，于是窗口 = 1 张成功 + 一批延后。
    连跑两拍、DB 零改动，判据两侧都要：0117 的指纹增量**必须恰好 1**——
    大于 1 是重判（本缺陷），等于 0 说明修法把该判的也跳过了，同样不合格。
    """
    import shutil, tempfile
    lines = []
    sb = tempfile.mkdtemp()
    pilot = os.path.join(sb, "pilot")
    os.makedirs(pilot)
    for f in ("判决指纹.jsonl", ".baseline_exempt"):
        shutil.copy(os.path.join(PILOT, f), pilot)
    for f in ("机检判决.jsonl", "A门判定.jsonl"):
        io.open(os.path.join(pilot, f), "w", encoding="utf-8").write("")
    io.open(os.path.join(pilot, ".audit-poller.cursor"), "w", encoding="utf-8").write("CS-99999999-9999")
    db = os.path.join(sb, "iso.db")
    shutil.copy(os.path.join(ROOT, "runtime.db"), db)
    TARGET = "CS-<日期>-0117"
    stub = os.path.join(sb, "selective_settle.py")
    # 桩要合被测契约：SETTLED→exit 0，UNSETTLED→exit 1。这条我今天已经栽过一次。
    io.open(stub, "w", encoding="utf-8").write(
        "#!/usr/bin/env python3\nimport sys\n"
        "no = [a for a in sys.argv[1:] if not a.startswith('--')]\n"
        "if no and no[0] == %r:\n    print('SETTLED'); sys.exit(0)\n"
        "print('UNSETTLED 积压夹具：本单一律延后'); sys.exit(1)\n" % TARGET)
    mark = os.path.join(sb, "watermark")
    src = io.open(POLLER, encoding="utf-8").read() if os.path.exists(POLLER) else ""
    m = re.findall(r"(AUDITPOLLER_[A-Z0-9_]*(?:UPDATE|WATERMARK|SCAN_MARK)[A-Z0-9_]*)", src)
    if not m:
        return False, ["水位线注入口不存在，无从构造积压窗口——按不通过计"]
    var = sorted(set(m))[0]

    def fp_count(no):
        n = 0
        for line in io.open(os.path.join(pilot, "判决指纹.jsonl"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("changeset_no") == no:
                n += 1
        return n

    def tick():
        env = dict(os.environ, AUDITPOLLER_PILOT=pilot,
                   AUDITPOLLER_ALERT_DIR=os.path.join(sb, "alerts"),
                   AUDITPOLLER_WORKDIR=os.path.join(sb, "wd"),
                   AUDITPOLLER_RUNTIME_DB=db, TRJ_DB_PATH=db,
                   AUDITPOLLER_REG_SETTLE=stub,
                   AUDITPOLLER_BASELINE_EXEMPT=os.path.join(pilot, ".baseline_exempt"),
                   TRJ_PILOT_DIR=pilot, TRJ_LEDGER=os.path.join(pilot, "判决指纹.jsonl"),
                   TRJ_INVALID=os.path.join(pilot, "判决失效.jsonl"))
        env[var] = mark
        os.makedirs(env["AUDITPOLLER_ALERT_DIR"], exist_ok=True)
        run_sandboxed(["bash", POLLER, "--once"], env)

    io.open(mark, "w", encoding="utf-8").write("<日期> 18:30:43")   # 0117 的 update_time 前 1 秒
    base = fp_count(TARGET)
    tick()
    after1 = fp_count(TARGET)
    tick()                        # DB 零改动，水位线沿用（窗口内有延后单，不会推进）
    after2 = fp_count(TARGET)
    lines.append("窗口构造：%s 判 SETTLED，其余一律 UNSETTLED（合法延后）" % TARGET)
    lines.append("%s 指纹条数 起始=%d → 第1拍=%d → 第2拍=%d" % (TARGET, base, after1, after2))
    grew = after2 - base
    lines.append("两拍总增量=%d（须**恰好 1**：>1 是跨拍重判，=0 是该判的被跳过）" % grew)
    return grew == 1, lines


def case3(wait: int) -> tuple[bool, list[str]]:
    """③ 心跳是否真在跳。

    关键不是文件在不在，是 **ts 会不会前进**——不前进的心跳文件和一个残留的旧文件
    长得一模一样，而那正是这条判据要区分的两件事。
    """
    lines = []
    if not os.path.exists(HEARTBEAT):
        return False, ["心跳文件不存在：%s" % HEARTBEAT]
    def read():
        return json.loads(io.open(HEARTBEAT, encoding="utf-8").read())
    a = read()
    lines.append("第一次读：ts=%s ok=%s cycle=%s" % (a.get("ts"), a.get("ok"), a.get("cycle")))
    if "ok" not in a or "ts" not in a:
        return False, lines + ["缺 ok/ts 字段"]
    lines.append("等 %d 秒看它跳没跳……" % wait)
    time.sleep(wait)
    b = read()
    lines.append("第二次读：ts=%s ok=%s cycle=%s" % (b.get("ts"), b.get("ok"), b.get("cycle")))
    moved = b.get("ts") != a.get("ts")
    lines.append("ts 前进 = %s（不前进 = 残留旧文件，不是心跳）" % moved)
    return bool(moved and b.get("ok") is True), lines


def main() -> int:
    ap = argparse.ArgumentParser(description="POLLERFIX2 独立复核")
    ap.add_argument("--wait", type=int, default=75, help="心跳观察秒数（默认 75，覆盖一拍 60s）")
    ap.add_argument("--skip-heartbeat", action="store_true")
    ap.add_argument("--deep", action="store_true",
                    help="深度档：④ 真跑 poller 三态（约 2 分钟）。改动豁免逻辑时必跑")
    a = ap.parse_args()
    allok = True
    ran = 0
    for name, fn in (("① 盲区单被正常通路捞起", case1),
                     ("② 定型闸接回 A 门之前", case2),
                     ("④ 基线豁免（%s）" % ("深度档" if a.deep else "日常档"),
                      lambda: case4(deep=a.deep)),
                     ("⑤ 通路三兜底（不进指纹的原地改）", case5),
                     ("⑥ 积压窗口内成功单跨拍不得重判", case6)):
        ok, lines = fn()
        allok &= ok
        ran += 1
        print("\n%s  %s" % (name, "PASS" if ok else "**FAIL**"))
        for l in lines:
            print("    " + l)
    if not a.skip_heartbeat:
        ok, lines = case3(a.wait)
        allok &= ok
        ran += 1
        print("\n③ 心跳真在跳  %s" % ("PASS" if ok else "**FAIL**"))
        for l in lines:
            print("    " + l)
    # 别写死条数。用例从三条长到六条，这行还在说「三条全过」——
    # **一份说着旧数字的验收结论，本身就是一条没人核对的断言。**
    print("\n复核结论：%d 条跑过，%s" % (ran, "全过" if allok else "**有不过项**"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
