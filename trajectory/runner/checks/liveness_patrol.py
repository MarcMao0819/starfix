#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在产判别力巡检 —— 验「判据是不是还活着」

## 为什么需要它

<日期> 体检发现：最近 200 轮生产判决里，**p1 与 p4 只出过 PASS**，
p2/p3/p5 各只出过 1 次 FAIL。最后一次真 FAIL 是两天前。

> **一个从不失败的检测器，和一个已经坏掉的检测器，从结果上完全看不出来。**

这不是理论顾虑。<日期> 实证过：冲突标记判据 `^(<<<<<<<|>>>>>>>|=======)$`
的三个并列分支里**两个从上线起就是死的**——而它对真冲突照样判 FAIL，
所以从结果上完全看不出异常，藏了很久。

硬约定 5/6/11 要求「判据必须有反例、零反例不许上线」，但那是**上线前跑一次**。
本模块把它变成**在产常设**：定期拿已知病态输入跑真图，断言每个判决点**真的会红**。

与第 13 条的关系：**哨兵管「数字是不是真的」，本巡检管「判据是不是还活着」。**

## 怎么做到不碰生产

- `TRJ_DB_PATH` 指向副本（自生产库在线快照克隆），trace 只落副本
- 独立 workdir，`--run-tag LIVENESS`
- **完全不写** 判决台账 / 告警目录 / 指纹台账 / 游标
- 用图自带的 `--inject <node>.output.<field>=<value>` 设施造病态，**不改被审对象、不改库**

## 判据：每个判决点都要「一红一绿」

只验红不够——一个恒红的判据同样是坏的，而且更吵。所以每条都配干净对照：
**同一张基准单，不注入必须绿；注入对应病态必须红。**

若某判决点造不出红（注入后仍绿），那正是它可能已死的信号，本巡检就该报警——
这是本模块存在的全部意义。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys

TRJ = "${TRAJ_HOME}"
GRAPH = os.path.join(TRJ, "graphs/changeset-audit.v5.4.json")
PATROL_DIR = os.environ.get(
    "PATROL_DIR", "${TRAJ_DATA_DIR}/判别力巡检")
LEDGER = os.path.join(PATROL_DIR, "巡检留痕.jsonl")

# 基准单：必须是一张**当前判 PASS 且跑满 14 节点**的正常单。
# 不写死单号——写死的单会随时间被并线/改动而失效，届时巡检会因为基准单变了而假红。
# 改为运行时从生产判决台账里挑最近一张 overall=PASS 的单。
PILOT = "${TRAJ_DATA_DIR}/changeset-audit"

# 每个判决点的病态注入。value 为 None 表示该点当前无可用注入设施（如实标注，不假装覆盖）。
CASES = [
    ("p1", "锚点非 40 位十六进制",
     ["n1.output.commit_hash=deadbeef"],
     "n2 对 sha40 的机械校验应判 REGEX_FAIL"),
    # [修 <日期>] 初版注入 n4.count=999 → n6 确实判 DEFER（判据活着），
    # 但 v4 起 DEFER 不直接判 FAIL，交 n9b 做基线搜索终裁；n9b 比的是**真实两侧文件集**，
    # 假计数骗不过它 → 终判 PASS。**这是设计的正确行为，不是判据死了。**
    # 要让 p2 真红，必须让两侧文件集真的对不上 → 改注入 n8（git 侧清单删首行）。
    # [修二 <日期>] `n8.drop_first` 单独用仍不红——因为 n6 判的是**三方计数**，
    # 删材料化清单首行不影响计数，n6 判 PASS → `p2_was_defer=False` → n9b 的 FAIL 不写回 p2。
    # **这是正确行为**：p2 问「三方计数对不对」，计数确实是对的。
    # 要打中 p2 必须让计数真不等（n5）**且**文件集真不一致（n8），两者缺一不可：
    #   只改计数 → n9b 拿真实文件集复核，找得到基线 → p2 回 PASS（**假计数骗不过它**，抗噪设计）
    #   计数+文件集都不一致 → n6 DEFER → n9b NOT_FOUND → p2 FAIL
    # 前两次注入没红，全因为我伪造的东西 n9b 根本不看。判别力不但活着，还比我以为的精确。
    ("p2", "三方计数不等 且 文件集真不一致",
     ["n5.output.count=1", "n8.output.drop_first=1"],
     "n6 判 DEFER → n9b 基线搜索 NOT_FOUND → p2 FAIL"),
    ("p3", "DB 明细含 git 中不存在的文件",
     ["n7.output.add_fake=frontend/src/__LIVENESS_PROBE__.vue"],
     "n9 两侧清单比对应判不一致"),
    # [修 <日期>] 初版注入 n10.hex_left3=DEADBEEF **打在了错的字段**：
    # n11 的判据是 `echo "{title}" | grep -E '[åãæÂÃ]'` —— 它看 **title**，不看 hex_left3。
    # 改注入 title 为真实的双重编码乱码形态（08-04 三个 agent 同天中招的那种）。
    ("p4", "标题双重编码乱码（utf8 被当 latin1 写入）",
     ["n10.output.title=è´¨é\u0087\u008f-æµ\u008bè¯\u0095"],
     "n11 编码判据应判 GARBLED"),
    # 抗噪反例（**期望绿**，与其它条相反）：单纯计数造假不得让 p2 变红，
    # 否则 n9b 的基线复核形同虚设。此条若变红，说明抗噪设计被破坏。
    ("p2#抗噪", "只改计数、文件集一致（假计数不得骗过 n9b）",
     ["n5.output.count=1"],
     "n9b 应找到基线 → p2 回 PASS。**此条期望绿，红了才是问题**"),
    ("p5", "行尾判据：无可用注入设施",
     None,
     "n13 的输入来自共用脚本实跑，图未开注入点；已知缺口，见文末"),
]


def pick_baseline() -> str | None:
    """挑一张最近判 PASS 的单当基准。不写死单号——写死会随并线/改动失效。"""
    p = os.path.join(PILOT, "机检判决.jsonl")
    if not os.path.exists(p):
        return None
    best = None
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("run_tag") == "default" and d.get("overall") == "PASS" and not d.get("aborted"):
            best = d.get("changeset_no")
    return best


def prepare_db() -> str:
    """从生产 runtime.db 做在线一致性快照当巡检库。读得到真历史，写只落副本。"""
    os.makedirs(os.path.join(PATROL_DIR, "workdir"), exist_ok=True)
    dst = os.path.join(PATROL_DIR, "runtime-patrol.db")
    src = sqlite3.connect(os.path.join(TRJ, "runtime.db"))
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def run_once(cs: str, injects: list[str] | None, db: str) -> dict:
    env = dict(os.environ)
    env["TRJ_DB_PATH"] = db
    cmd = [sys.executable, os.path.join(TRJ, "runner/run_graph.py"), GRAPH,
           "--changeset", cs, "--workdir", os.path.join(PATROL_DIR, "workdir"),
           "--run-tag", "LIVENESS"]
    for i in (injects or []):
        cmd += ["--inject", i]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=TRJ)
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"overall": "PARSE_ERR", "verdicts": {}, "stderr": p.stderr[-300:]}


STABILITY_RUNS = int(os.environ.get("PATROL_STABILITY_RUNS", "3"))


def stability(cs: str, db: str) -> dict:
    """三连跑稳定性（TRIRULE <日期> 验收要求①）。

    本判据是 p4 病灶的**直接反测**，比单次红绿硬：病灶不是「判错」而是「同输入不同答案」
    ——实测 v5.3 下同单同注入连跑三次得到 NEEDS_HUMAN/PASS/FAIL。
    单次巡检看不出抽签，只有连跑能。
    """
    garbled = [run_once(cs, ["n10.output.title=è´¨éæµè¯"], db).get("verdicts", {}).get("p4")
               for _ in range(STABILITY_RUNS)]
    clean = [run_once(cs, None, db).get("verdicts", {}).get("p4")
             for _ in range(STABILITY_RUNS)]
    return {"garbled_runs": garbled, "clean_runs": clean,
            "garbled_stable_fail": all(v == "FAIL" for v in garbled),
            "clean_stable_pass": all(v == "PASS" for v in clean),
            "ok": all(v == "FAIL" for v in garbled) and all(v == "PASS" for v in clean)}


def patrol(verbose: bool = True) -> dict:
    cs = pick_baseline()
    if not cs:
        return {"ok": False, "error": "找不到可用基准单（生产台账里没有 PASS 单）"}
    db = prepare_db()

    clean = run_once(cs, None, db)
    clean_v = clean.get("verdicts", {})
    results = []
    ok = True

    # 干净对照：全绿，否则基准单本身有问题，后面的红都不可信
    baseline_ok = all(v == "PASS" for v in clean_v.values()) and clean_v
    if not baseline_ok:
        ok = False

    for pt, name, injects, why in CASES:
        if injects is None:
            results.append({"point": pt, "case": name, "status": "NO_FACILITY",
                            "note": why, "ok": None})
            continue
        r = run_once(cs, injects, db)
        expect_green = pt.endswith("#抗噪")
        key = pt.split("#")[0]
        got = r.get("verdicts", {}).get(key)
        if expect_green:
            # 抗噪条：期望仍绿。红了说明抗噪设计被破坏，同样算不通过。
            turned_red = (got == "PASS")
        else:
            turned_red = got not in (None, "PASS")
        results.append({"point": pt, "case": name, "injects": injects,
                        "verdict": got, "status": "RED" if turned_red else "**STILL_GREEN**",
                        "ok": turned_red, "note": why})
        ok &= turned_red

    stab = stability(cs, db)
    ok &= stab["ok"]
    out = {"ok": ok and baseline_ok, "stability": stab, "baseline_unit": cs,
           "baseline_verdicts": clean_v, "baseline_all_pass": baseline_ok,
           "results": results}
    os.makedirs(PATROL_DIR, exist_ok=True)
    io.open(LEDGER, "a", encoding="utf-8", newline="").write(
        json.dumps(out, ensure_ascii=False) + "\n")

    if verbose:
        print("基准单 %s —— 干净对照 %s" % (cs, "全绿 ✓" if baseline_ok else "**未全绿，后续红不可信**"))
        print("  干净对照判决:", clean_v)
        print()
        for r in results:
            if r["status"] == "NO_FACILITY":
                print("  %-3s %-30s **无注入设施** —— %s" % (r["point"], r["case"][:30], r["note"][:50]))
            else:
                print("  %-3s %-30s 注入后 %-6s → %s" %
                      (r["point"], r["case"][:30], r.get("verdict"), r["status"]))
        print("\n  三连跑稳定性（p4 病灶直接反测）：")
        print("    乱码单 %s → %s" % (stab["garbled_runs"], "3×FAIL ✓" if stab["garbled_stable_fail"] else "**不稳定**"))
        print("    干净单 %s → %s" % (stab["clean_runs"], "3×PASS ✓" if stab["clean_stable_pass"] else "**不稳定**"))
        covered = [r for r in results if r["ok"] is not None]
        red = [r for r in results if r["ok"]]
        print("\n判别力：%d/%d 个判决点注入后确实变红；%d 个无注入设施（未覆盖，不算通过）"
              % (len(red), len(covered), len(results) - len(covered)))
        print("巡检结论：%s" % ("通过" if out["ok"] else "**有判决点未变红或基准单不干净——需查**"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="在产判别力巡检（隔离运行，不碰生产）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = patrol(verbose=not a.json)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
