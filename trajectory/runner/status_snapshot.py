#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轨迹审计线状态快照 —— 供 macOS 菜单栏 app 读取

## 为什么是「快照 JSON + app 读文件」而不是 app 直接查

app 里不放任何判据。它只渲染这份 JSON。
理由：判据全在 `runner/checks/` 且有自检与正反例；若把「怎么算积压」「什么叫健康」
抄进 Swift，就多出一份没有自检、会和判据侧长歪的实现——今天已经有六次
「判据对、适用域错」的教训，不再给自己造第七个。

## 健康判据（三态，不是二态）

- `OK`     ：轮询器活、零积压、错误日志无新增
- `WARN`   ：有积压 或 有未处置告警堆积
- `DOWN`   ：轮询器不在 或 错误日志有新增字节
- `UNKNOWN`：**取数失败**。不许伪装成 OK——
  「查不到」和「没问题」是两回事，这条今天在 B 路由上刚栽过（三态压成两态）。

只用 Python 3 标准库。
"""

from __future__ import annotations

import collections
import datetime
import io
import json
import os
import re
import subprocess
import sys

PILOT = "${TRAJ_DATA_DIR}/changeset-audit"
ALERTDIR = "${FLEET_HOME}/<项目>ERP/迁移备份/回执"
OUT = os.environ.get("TRJ_STATUS_JSON",
                     "${TRAJ_DATA_DIR}/changeset-audit/状态快照.json")
ERRLOG_BASELINE = {".runner-err.log": 2557, ".drift-err.log": 0}   # 已知良性大小

# 轮询器候选脚本名（新→旧）。换代时往这里加一行，不要改判据形状。
POLLER_SCRIPTS = ("audit-poller.sh", "notify-poller.sh")
# 基线豁免：id < 3247 的历史存量按 <日期> 裁定不追审，不计入未审面。
BASELINE_ID = int(os.environ.get("TRJ_BASELINE_ID", "3247"))
STD_NO = re.compile(r"^CS-\d{8}-\d{4}$")
# 计划内停机标记。内容 {"until":"YYYY-mm-dd HH:MM:SS","why":"...","by":"..."}
MAINT_FILE = os.environ.get("TRJ_MAINT_FILE",
                            "${TRAJ_DATA_DIR}/changeset-audit/.audit-poller.maint")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "checks"))
import measure_guard as guard_mod          # noqa: E402


def read_text(path: str) -> str | None:
    try:
        return io.open(path, encoding="utf-8").read().strip() or None
    except Exception:
        return None


def db_changesets() -> list[tuple[int, str]] | None:
    """全库登记单 (id, 单号)。取不到返回 None——**不返回空表**，
    空表会被下游当成「一单都没有」，那正是把取数失败伪装成好消息。"""
    rc, out = sh('/usr/local/bin/docker exec ${DB_CONTAINER} sh -c '
                 '\'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
                 '-N -B ${DB_NAME} -e "SELECT id, changeset_no FROM t_code_changeset '
                 'WHERE is_del=0 ORDER BY id"\' 2>/dev/null')
    if rc != 0 or not out:
        return None
    rows = []
    for line in out.split("\n"):
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) == 2 and parts[0].strip().isdigit():
            rows.append((int(parts[0]), parts[1]))
    return rows or None


def fingerprinted() -> set[str] | None:
    """判决指纹台账覆盖到的单号。这是「判过」的唯一权威留痕。"""
    fp = os.path.join(PILOT, "判决指纹.jsonl")
    if not os.path.exists(fp):
        return None
    out = set()
    for line in io.open(fp, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("changeset_no"):
            out.add(d["changeset_no"])
    return out or None



def sh(cmd: str) -> tuple[int, str]:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def jsonl_since(path: str, since: str) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = str(d.get("ts", ""))
        # 台账里有两种时间戳写法（空格与 T 分隔）。统一成可比形式再比——
        # 08-18 我就是拿空格格式去比 T 格式，把历史条目算成了「昨晚新增」。
        if ts.replace("T", " ") >= since:
            out.append(d)
    return out


def build() -> dict:
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    since_24h = (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    s: dict = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "problems": []}

    # ── 轮询器 ──
    # 判据**不绑死单个脚本名**。<日期>：轮询器正体从 notify-poller.sh 换成
    # audit-poller.sh（整机重启后 4C03 重建），而这里的 pgrep 还写着旧名字，
    # 于是菜单栏对 Owner 报了一上午假 DOWN——真相是它 11:46 起一直在跑。
    # 「进程叫什么名字」是**别人可以改而我不会知道**的东西，不配当判据的唯一支点。
    # 只认**常驻循环**那一个进程。<日期> 连栽两次，两次都是假绿：
    # ① `pgrep -f audit-poller.sh` 会把我自己验收脚本的 `--once` 沙箱跑也认领，
    #    于是停机期间只要我在跑复核，看板就报「生产轮询器活着」——我的测试骗了我的监控。
    # ② 修①时我写了 `pgrep -af`。**macOS 的 `-a` 不是 Linux 的「显示完整命令行」**，
    #    它是「把 pgrep 自己的祖先进程也纳入匹配」——于是它匹配到了**正在执行这条命令的
    #    那个 shell**（它的命令行里当然含 'audit-poller.sh'），恒返回一个 pid，永远 alive。
    #    **修一个假绿，造出一个更彻底的假绿**，而且这次连「有没有进程」都不看了。
    # 改用 ps 取完整命令行自己过滤：既能看到参数，也不依赖 pgrep 各平台的参数语义。
    ONESHOT = ("--once", "--audit-one")
    rc, out = sh("/bin/ps -Ao pid,command || true")
    pid = script = None
    for line in (out or "").split("\n"):
        m = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not m:
            continue
        cmd = m.group(2)
        # 必须是「解释器 + 脚本路径」的形态；ps/grep 自己那几行命令行里也含脚本名，得排掉
        # 脚本名从 POLLER_SCRIPTS 生成，**不写死**——写死了自检就换不了名字，
        # 于是「有进程在跑必须探到」这条正例只能靠生产轮询器，又回到「自检依赖生产健康」。
        names = "|".join(re.escape(x) for x in POLLER_SCRIPTS)
        mm = re.search(r"(?:^|/)(?:bash|sh|zsh)\s+(\S*/)?(%s)\b" % names, cmd)
        if not mm:
            continue
        if any(f in cmd for f in ONESHOT):      # 一次性调用不是常驻轮询器
            continue
        pid, script = m.group(1), mm.group(2)
        break
    s["poller_pid"], s["poller_script"] = pid, script
    s["poller_alive"] = bool(pid)
    if not pid:
        s["problems"].append("轮询器不在运行（已找过 %s）" % "、".join(POLLER_SCRIPTS))

    # ── 未审面：集合差，不看别人的游标文件 ──
    # 旧写法是 `.last_id` 与库 MAX(id) 相减。轮询器换代后游标换成了
    # `.audit-poller.cursor`（存单号不存 id），`.last_id` 就此冻结在 4008，
    # 于是这里恒报「积压 2」——两个数都真，只是它们量的不是同一件事。
    # **游标文件是轮询器的私产**，拿它当我的判据，等于把我的正确性押在别人的实现细节上。
    # 改成我自己算得出、且换谁来轮询都成立的口径：
    #   未审 = 库里 id≥基线 的标准单号  −  判决指纹台账已覆盖的单号
    # 指纹台账是「判过」的唯一权威留痕；没有指纹 = 没判过，且**漂移通路也看不见它**
    # （漂移靠比对旧指纹，无旧指纹即无从比对），所以这个差集就是真正的盲区面。
    rows = db_changesets()
    s["db_total"] = len(rows) if rows is not None else None
    s["db_max_id"] = max(i for i, _ in rows) if rows else None
    fp = fingerprinted()
    cur_no = read_text(os.path.join(PILOT, ".audit-poller.cursor"))
    s["cursor_no"] = cur_no
    s["cursor"] = next((i for i, n in (rows or []) if n == cur_no), None)

    if rows is None or fp is None:
        s["backlog"], s["backlog_units"] = None, []
        s["problems"].append("取数失败：库清单或判决指纹台账读不到")
    else:
        g = guard_mod.MeasureGuard()
        g.rowcount("库登记单清单", got=len(rows), want_at_least=500)
        g.rowcount("判决指纹台账", got=len(fp), want_at_least=500)
        g.sentinel("已知已判单在指纹台账内", got=("CS-<日期>-0067" in fp), want=True)
        # 用 report() 而不是 require()：require() 抛的是 SystemExit（继承 BaseException），
        # `except Exception` **接不住**，进程会直接退出、快照根本不落盘——
        # 那就成了「哨兵一响，看板从有数变成没数，还没人知道为什么」。
        # 这里要的是把「拒绝出数」这件事**写进快照**，让它在菜单栏上看得见。
        old_out, sys.stdout = sys.stdout, sys.stderr   # 哨兵报告走 stderr，不污染 --print 的 JSON
        try:
            trustworthy = g.report()
        finally:
            sys.stdout = old_out
        if trustworthy:
            un = [(i, n) for i, n in rows
                  if i >= BASELINE_ID and STD_NO.match(n) and n not in fp]
            s["backlog"] = len(un)
            s["backlog_units"] = [n for _, n in un]
            if un:
                s["problems"].append("未审 %d 单（无判决指纹，漂移通路也看不见）" % len(un))
        else:
            # 哨兵挂了就**拒绝出数**，不许「先出个数再说」——那正是 08-17 三次假结论的共同做法
            s["backlog"], s["backlog_units"] = None, []
            s["problems"].append("测量哨兵未通过，拒绝出数（详见 stderr）")
    maxid = s["db_max_id"]

    # ── 错误日志（比基线，不比绝对值）──
    errs = {}
    for f, base in ERRLOG_BASELINE.items():
        fp = os.path.join(PILOT, f)
        sz = os.path.getsize(fp) if os.path.exists(fp) else 0
        errs[f] = {"size": sz, "baseline": base, "grown": sz > base}
        if sz > base:
            s["problems"].append("%s 增长 %d 字节（执行器报错）" % (f, sz - base))
    s["errlogs"] = errs

    # ── 轮询器自己记的 RUNNER_ERR 台账 ──
    # 新轮询器的 stdout 协议是「静默即正常」，错误只落这本台账。
    # 不接进来的话，「一切正常」和「每拍都在同一处报错」在看板上长得一模一样。
    rerr = jsonl_since(os.path.join(PILOT, ".audit-poller-runner-err.jsonl"), since_24h)
    s["runner_err_24h"] = len(rerr)
    s["runner_err_last"] = ({"cs": rerr[-1].get("changeset_no"),
                             "stage": rerr[-1].get("stage"),
                             "reason": str(rerr[-1].get("reason"))[:160],
                             "ts": rerr[-1].get("ts")} if rerr else None)
    if rerr:
        s["problems"].append("轮询器 24 小时内报错 %d 次（末次 %s:%s）"
                             % (len(rerr), rerr[-1].get("changeset_no"), rerr[-1].get("stage")))

    # ── 近 24 小时活动 ──
    # 「近 24 小时判了多少」以**判决指纹台账**为准，不以新单队列为准。
    # 新单队列.jsonl 是旧轮询器写的，新轮询器不写——从 09-01 08:49 起它就冻住了，
    # 而我原来的 new_units_24h 正是读它，于是那一格会永远停在换代那一刻的数。
    # 判决指纹是「判过」的唯一权威留痕，动作发生它就长，换谁来轮询都成立。
    # 口径注：台账里早期条目用 UTC(+00:00)、近期用本地(+08:00)，近 24 小时窗内均为本地，
    # 不影响这个数；跨更长窗口比对时须先归一时区。
    fpj = jsonl_since(os.path.join(PILOT, "判决指纹.jsonl"), since_24h)
    s["judged_24h"] = len(fpj)
    s["judged_recent"] = [x.get("changeset_no") for x in fpj][-8:]
    gate = jsonl_since(os.path.join(PILOT, "A门判定.jsonl"), since_24h)
    s["gate_24h"] = dict(collections.Counter(x.get("verdict") for x in gate))
    s["gate_hold_24h"] = [{"cs": x["changeset_no"], "failed": x.get("failed", [])}
                          for x in gate if x.get("verdict") == "HOLD"]
    # ── B 自动归档：以**告警文件里的归档段**为准，不以留痕台账为准 ──
    # <日期> 实测：新轮询器直接调 alert_consumer，而写 `B消费留痕.jsonl` 那段
    # 原本在旧轮询器的 b_consume 函数里，换代时没带过来。
    # 结果是归档照常发生（文件里有批次号），台账却从 08-31 23:02 起不再增长。
    # 我原来读台账，于是这一格从今天起会永远显示旧数——**又一个「量的东西已经不在那儿了」**。
    # 改成数归档段本身（那是归档真正发生过的凭据），并把台账断更单独报出来，不让它悄悄烂掉。
    arch_today, arch_files = 0, []
    if os.path.isdir(ALERTDIR):
        for f in sorted(os.listdir(ALERTDIR)):
            if not (f.startswith("机检告警-") and f.endswith(".md")):
                continue
            txt = io.open(os.path.join(ALERTDIR, f), encoding="utf-8", errors="replace").read()
            if re.search(r"^## 归档（%s" % re.escape(today), txt, re.M):
                arch_today += 1
                arch_files.append(f[len("机检告警-"):-3])
    s["b_archived_today"] = arch_today
    s["b_archived_units"] = arch_files
    # 断更判据必须**同窗口比同窗口**。第一版拿「今日归档数」比「近 24 小时台账条数」，
    # 而 24 小时窗口会把昨晚 23:02 那条捞进来，于是台账明明今天一条没写，判据却说不断更。
    # 今天第四次栽在「两个数量的不是同一段时间/同一件事」上了。
    bc_today = [x for x in jsonl_since(os.path.join(PILOT, "B消费留痕.jsonl"), today)
                if str(x.get("ts", "")).startswith(today)]
    s["b_ledger_today"] = sum(len(x.get("b_consume", {}).get("archived", [])) for x in bc_today)
    s["b_ledger_stale"] = arch_today > 0 and s["b_ledger_today"] == 0
    # 不在这里单独报 problem：下面的「台账断更总检」用更准的参照点（本轮启动时刻）统一报，
    # 两处各报一次会让同一件事在菜单栏上出现两行。
    s["drift_24h"] = len(jsonl_since(os.path.join(PILOT, "判决失效.jsonl"), since_24h))

    # ── 台账断更总检（<日期> 换代后新增）──
    # 三本台账的写入逻辑原本在旧轮询器的函数里，换代时没带过来：动作照常发生，台账不再长。
    # 这类「数字还在、但已经不跟着世界变」的格子最危险——它不会报错，只会慢慢变成谎话。
    #
    # 判据要三态，不许压成两态。第一版我用「今天有没有写过」判，结果 08:49（旧轮询器
    # 咽气前）写的那笔也算「今天」，两本已死的台账被判成活的。参照点改成
    # **本轮轮询器的启动时刻**（锁文件 mtime），问的才是「现在这个轮询器写不写它」。
    # 而且没写不等于坏了——事件型台账没触发就不该写。所以：
    #   已写 / 未写但触发确实发生过 = 断更 / 未写且无触发 = **判不了**，如实标 UNKNOWN。
    lock_mt = None
    lk = os.path.join(PILOT, ".audit-poller.lock")
    if os.path.exists(lk):
        lock_mt = os.path.getmtime(lk)
    hb = {}
    try:
        hb = json.loads(io.open(os.path.join(PILOT, ".audit-poller.heartbeat"),
                                encoding="utf-8").read())
    except Exception:
        pass
    # 「动作发生过吗」必须和「台账写没写」量同一段时间。
    # 第一版触发数按**今天**算、台账按**本轮启动后**算——于是 13:12 那次作废（上一轮轮询器干的）
    # 让「本轮有触发」成立，而本轮其实一次作废都没发生，看板当场误报断更。
    # 今天第 N 次栽在「两个数量的不是同一段时间」，所以这次两边都钉在**本轮启动时刻**上。
    def _since_start(pattern: str) -> int:
        n = 0
        if not os.path.isdir(ALERTDIR) or lock_mt is None:
            return n
        for f in sorted(os.listdir(ALERTDIR)):
            if not (f.startswith("机检告警-") and f.endswith(".md")):
                continue
            t = io.open(os.path.join(ALERTDIR, f), encoding="utf-8", errors="replace").read()
            for m in re.finditer(pattern, t):
                try:
                    ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if ts.timestamp() >= lock_mt:
                    n += 1
        return n

    sup_since = _since_start(r"判决已更新，本告警作废（(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    arch_since = _since_start(r"^## 归档（(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    s["supersede_since_start"], s["archived_since_start"] = sup_since, arch_since
    # 台账 -> 「本轮启动后这个动作发生过吗」
    # `新单队列.jsonl` **不在此列**：代舵舰长 <日期> 裁定正式废弃、不回填
    # （唯一消费者是本文件的 new_units_24h，已改读判决指纹；心跳按拍记 new_picked/deferred/err）。
    # 已废弃的东西继续挂在断更检里，就会在菜单栏上留一条永远亮着的黄灯——
    # **红久了等于没红**，那正是这套看板要防的东西。结案就要结干净，不留纪念品。
    triggers = {
        "B消费留痕.jsonl":  arch_since > 0,
        "告警撤回留痕.jsonl": sup_since > 0,
        "登记定型闸.jsonl":  bool(hb.get("deferred")),
    }
    led = {}
    for f, triggered in triggers.items():
        fp2 = os.path.join(PILOT, f)
        if not os.path.exists(fp2):
            led[f] = "缺失"
            continue
        mt = os.path.getmtime(fp2)
        if lock_mt is None:
            led[f] = "UNKNOWN·测不到轮询器启动时刻"
        elif mt >= lock_mt:
            led[f] = "本轮已写"
        elif triggered:
            led[f] = "**断更**（本轮有触发却没写）"
        else:
            led[f] = "UNKNOWN·本轮无触发，判不了"
    s["ledger_status"] = led
    dead = [f for f, v in led.items() if v.startswith("**断更**")]
    s["ledgers_broken"] = dead
    if dead:
        s["problems"].append("台账断更（换代漏项）：%s" % "、".join(dead))

    # ── 判决分布（今日）──
    vd = []
    vp = os.path.join(PILOT, "机检判决.jsonl")
    if os.path.exists(vp):
        for line in io.open(vp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            vd.append(d)
    s["verdicts_total"] = len(vd)
    s["last_verdict"] = ({"cs": vd[-1].get("changeset_no"),
                          "overall": vd[-1].get("overall") or vd[-1].get("graph")}
                         if vd else None)

    # ── 未处置告警（这是当初会烂掉的那个数）──
    pending, handled = [], 0
    if os.path.isdir(ALERTDIR):
        for f in sorted(os.listdir(ALERTDIR)):
            if not (f.startswith("机检告警-") and f.endswith(".md")):
                continue
            txt = io.open(os.path.join(ALERTDIR, f), encoding="utf-8", errors="replace").read()
            if re.search(r"^## ", txt, re.M):
                handled += 1
            else:
                pending.append(f[len("机检告警-"):-3])
    # 未处置告警带上**机器建议**（B 路由）。让人点的时候有依据，不是瞎点。
    # 建议只是建议：route 只看机械事实，判不了业务对错，最终由人定。
    advice = {}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "checks"))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import alert_consumer as ac      # noqa: E402
        import drift_scan as ds          # noqa: E402
        st = ds.current_state()
        for cs in pending:
            u = st.get(cs)
            if not u:
                advice[cs] = {"route": "?", "why": "查无登记"}
                continue
            m, mw = ac.merge_state(u["commit_hash"])
            r = ds.recheck_consistency(u["commit_hash"], set(x for x, _ in u["manifest"]),
                                       u.get("change_type", ""))
            ov, ab = ac.last_verdict(cs)
            rt, why = ac.route(m, r["verdict"], ov, ab)
            advice[cs] = {"route": rt, "why": why[:120], "merge": mw,
                          "recheck": r["verdict"], "orig": ov}
    except Exception as e:      # 建议算不出不影响主状态——但要标出来，不许静默当没有
        s["advice_error"] = "%s: %s" % (type(e).__name__, e)
    s["alerts_total"] = handled + len(pending)
    s["alerts_handled"] = handled
    s["alerts_pending"] = pending
    s["alerts_advice"] = advice

    # ── 轨迹线待办：被 trace 转给舰员癸、且尚未写自查结论的 ──
    # <日期> 教训：`trace` 动作原本**没有任何投递机制**——写完文件就结束了，
    # 舰员癸不会醒、不会看、没人通知。Owner 点了六张，主窗口没收到，**舰员癸也没收到**。
    # 「转轨迹线自查」等于扔进空气里。把它做成 app 上看得见的一格，
    # 至少「欠着几张」是可见的，不再是黑洞。
    trace_open = []
    if os.path.isdir(ALERTDIR):
        for f in sorted(os.listdir(ALERTDIR)):
            if not (f.startswith("告警处置-") and f.endswith("-trace.md")):
                continue
            txt = io.open(os.path.join(ALERTDIR, f), encoding="utf-8", errors="replace").read()
            if "## 轨迹线自查结论" not in txt:      # 没写结论 = 还欠着
                trace_open.append(f[len("告警处置-"):-len("-trace.md")])
    s["trace_queue"] = trace_open
    if trace_open:
        s["problems"].append("轨迹线待办 %d 张（trace 转来未结）" % len(trace_open))
    if len(pending) >= 5:
        s["problems"].append("未处置告警 %d 张" % len(pending))

    # ── 计划内停机：把「没人管」和「有人正在管」分开 ──
    # ⑧路施工要停机（正在跑的 bash 被编辑会按字节偏移读到错位内容，必须停→改→验→重挂）。
    # 停机期间报 DOWN 是**事实正确**的，但整个施工窗口一直红，红久了就等于没红——
    # 下次真出事时这盏灯已经不说话了。
    # **但停机标记必须会过期**：一个不过期的「已知问题」标记就是一个永久静音开关，
    # 那正是 B 门当初立两条不变式要防的东西（告警消费闭环变成告警消音器）。
    # 过期即自动落回 DOWN，且把「过期多久还没重挂」明写出来。
    s["maintenance"] = None
    mt = read_text(MAINT_FILE)
    if mt:
        try:
            m = json.loads(mt)
            until = datetime.datetime.strptime(m["until"], "%Y-%m-%d %H:%M:%S")
            m["expired"] = now > until
            m["minutes_left"] = int((until - now).total_seconds() // 60)
            s["maintenance"] = m
        except Exception as e:
            s["problems"].append("停机标记读不动，按无标记处理：%s" % e)

    mn = s["maintenance"]
    planned_down = bool(mn) and not mn["expired"] and not s["poller_alive"]
    if planned_down:
        s["problems"].insert(0, "计划内停机中：%s（还剩 %d 分钟到期）"
                             % (mn.get("why", "未写原因"), mn["minutes_left"]))
        # 停机是「有人在管的降级」，不是「没人管的故障」：用 WARN 而不是 DOWN。
        # 用 WARN 而不新造一个 MAINT 态，是因为 app 端对没见过的态一律渲染成灰问号，
        # 那会把「有人在管」显示成「我不知道」——比黄灯更糟。真要单列一态得改 Swift 并重装，
        # 而 Owner 此刻正开着这个 app，不值得为一格颜色冒重装的险。
    if bool(mn) and mn["expired"] and not s["poller_alive"]:
        s["problems"].insert(0, "**停机标记已过期 %d 分钟，⑧路仍未重挂**" % -mn["minutes_left"])

    # ── 健康四态。取数失败不许伪装成 OK ──
    if s["backlog"] is None or maxid is None:
        s["health"] = "UNKNOWN"
    elif planned_down:
        s["health"] = "WARN"
    elif not s["poller_alive"] or any(v["grown"] for v in errs.values()):
        s["health"] = "DOWN"
    elif s["problems"]:
        s["health"] = "WARN"
    else:
        s["health"] = "OK"
    return s


def selftest() -> int:
    """两条新判据的正反例。**每条都要答得出「什么情况下它会红」。**

    <日期> 这两条判据同时失灵过一次：探活盯着旧脚本名 `notify-poller.sh`，
    积压读着旧游标 `.last_id`。两个数都不是乱写的，只是量的东西已经不在那儿了。
    自检就是逼自己每次都回答一遍：**它现在还量得到东西吗。**
    """
    import tempfile
    global POLLER_SCRIPTS, OUT, MAINT_FILE
    tmp = tempfile.mkdtemp()
    OUT = os.path.join(tmp, "snap.json")                    # 不碰生产快照
    # 停机标记也必须**一开始就**指向临时位置。第一版只在停机四例那里才改，
    # 前面三例仍读生产 marker——于是我 12:19 往生产写了个真停机标记之后，
    # 「名字对不上必须报 DOWN」这条当场变成 WARN 红了。
    # 又是「自检把生产此刻的状态当前提」，一小时内同一形状第二次：
    # 上次修的是进程那一处，**没有把它当成一类去修**，于是它换个地方原样复发。
    MAINT_FILE = os.path.join(tmp, "maint-none")            # 不存在 = 无停机标记
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print("  %-34s %s %s" % (name, "OK" if cond else "**不符**", detail))

    # 探活的正例**不能拿生产轮询器当前提**。第一版就是这么写的，结果舰长为施工停了
    # ⑧路，自检立刻红——红的不是判据，是我把「生产此刻健康」写进了自检的前提。
    # 自检要能在任何时刻跑，所以正例改成自己起一个可控的假进程。
    keep = POLLER_SCRIPTS
    fake_dir = tempfile.mkdtemp()
    fake = os.path.join(fake_dir, "轨迹自检-假轮询器.sh")
    io.open(fake, "w", encoding="utf-8").write("#!/bin/bash\nsleep 60\n")
    proc = subprocess.Popen(["bash", fake],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        POLLER_SCRIPTS = ("轨迹自检-假轮询器.sh",)
        base = build()
        check("正例·有进程在跑就必须探到", base["poller_alive"] and base["poller_script"],
              "script=%s pid=%s" % (base["poller_script"], base["poller_pid"]))
        check("正例·未审面出得了数", isinstance(base["backlog"], int),
              "未审=%s" % base["backlog"])

        # 反例：一次性调用（--once）不许被当成常驻轮询器。
        # 这正是今天那次假绿——我自己的验收脚本在跑 --once，看板就说生产轮询器活着。
        POLLER_SCRIPTS = ("轨迹自检-假轮询器.sh",)
        one = subprocess.Popen(["bash", fake, "--once"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.terminate(); proc.wait()          # 先停掉常驻那只，只留 --once 这只
        oneshot = build()
        one.terminate(); one.wait()
        proc = subprocess.Popen(["bash", fake],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        check("反例·--once 一次性调用不算常驻轮询器",
              not oneshot["poller_alive"], "poller_alive=%s" % oneshot["poller_alive"])

        POLLER_SCRIPTS = ("绝无此进程-selftest.sh",)
        neg = build()
    finally:
        POLLER_SCRIPTS = keep
        proc.terminate()
        proc.wait()
    check("反例·名字对不上必须报 DOWN",
          (not neg["poller_alive"]) and neg["health"] == "DOWN",
          "health=%s" % neg["health"])

    keepdb = globals()["db_changesets"]
    globals()["db_changesets"] = lambda: [(1, "CS-<日期>-0001")]      # 取数只回 1 条
    neg2 = build()
    globals()["db_changesets"] = keepdb
    check("反例·哨兵挂了必须拒绝出数，不是出 0",
          neg2["backlog"] is None and any("拒绝出数" in x for x in neg2["problems"]),
          "backlog=%r health=%s" % (neg2["backlog"], neg2["health"]))
    check("反例·拒绝出数时健康必须是 UNKNOWN 不是 OK",
          neg2["health"] == "UNKNOWN", "health=%s" % neg2["health"])

    # ── 停机态四例。核心问题：**这个标记能不能把真故障盖住？** ──
    MAINT_FILE = os.path.join(tmp, "maint")
    fut = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    past = (datetime.datetime.now() - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    def with_maint(until, alive):
        """alive=True 同样不靠生产轮询器，起自己的假进程，自检才随时可跑。"""
        io.open(MAINT_FILE, "w", encoding="utf-8").write(
            json.dumps({"until": until, "why": "自检", "by": "selftest"}))
        global POLLER_SCRIPTS
        k = POLLER_SCRIPTS
        pr = None
        try:
            if alive:
                pr = subprocess.Popen(["bash", fake],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                POLLER_SCRIPTS = ("轨迹自检-假轮询器.sh",)
            else:
                POLLER_SCRIPTS = ("绝无此进程-selftest.sh",)
            return build()
        finally:
            POLLER_SCRIPTS = k
            if pr is not None:
                pr.terminate()
                pr.wait()

    m1 = with_maint(fut, alive=False)
    check("正例·停机标记有效+轮询器停 → WARN 不是 DOWN",
          m1["health"] == "WARN" and any("计划内停机中" in x for x in m1["problems"]),
          "health=%s" % m1["health"])
    m2 = with_maint(past, alive=False)
    check("反例·标记过期 → 必须落回 DOWN，不许永久静音",
          m2["health"] == "DOWN" and any("已过期" in x for x in m2["problems"]),
          "health=%s" % m2["health"])
    m3 = with_maint(fut, alive=True)
    check("反例·轮询器在跑时标记不生效，不许无故变黄",
          not any("计划内停机中" in x for x in m3["problems"]),
          "health=%s" % m3["health"])
    keepdb2 = globals()["db_changesets"]
    globals()["db_changesets"] = lambda: None
    m4 = with_maint(fut, alive=False)
    globals()["db_changesets"] = keepdb2
    check("反例·停机标记盖不住取数失败 → 仍是 UNKNOWN",
          m4["health"] == "UNKNOWN", "health=%s" % m4["health"])

    print("\n自检：%s" % ("全部符合" if ok else "**有不符项**"))
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    s = build()
    tmp = OUT + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1))
    os.replace(tmp, OUT)          # 原子替换，app 不会读到写一半的文件
    if "--print" in sys.argv:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print("%s  health=%s  积压=%s  未处置告警=%d" %
              (s["generated_at"], s["health"], s["backlog"], len(s["alerts_pending"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
