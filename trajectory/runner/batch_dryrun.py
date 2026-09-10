#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史全量大批量试跑（观察模式）· TRJ-PROD ①

## 隔离怎么做到的（这是本脚本存在的主要理由）

试跑必须与生产**物理隔离**，而隔离点不止「别写生产告警目录」那一处：

| 隔离对象 | 手段 |
|---|---|
| SQLite `trace_runs` | `TRJ_DB_PATH` 指向副本。**这不只是留痕**——n1b 的类型B 判据要读它判「并线前有没有完整判决轮次」。试跑写进生产库，会让此后的生产轮次把试跑轮当历史判词，本该 ALERT 的单变成 SKIPPED，**等于用观察行为改变了被观察对象** |
| 副本的内容 | 用 `sqlite3.backup()` 从生产库做在线一致性快照，**保留全部历史**——否则 n1b 找不到任何历史判词，每张已并线单都会假报 ALERT，试跑分布整个失真 |
| `workdir` 材料化文件 | 独立 workdir |
| 判决 / 告警 | 只落本目录 jsonl，**不碰生产 `机检判决.jsonl` / `回执/` 告警目录**，不触发人工兜流程 |
| 游标 `.last_id` / 指纹台账 | 全程不调用 `drift_scan --record`、不动 `.last_id`。`--recheck` 是纯读 |

生产轮询器**照常跑**，两侧不共享任何可写路径。

## 模型调用不静默截断

`--model-budget` 是**硬上限**：到顶即停，把「还剩几单没跑」写进结果文件并在摘要里显式报出。
按硬约定「不许静默截断」——砍了多少必须看得见，否则报告读起来像跑全了。

## 可续跑

已有结果的单直接跳过（按 changeset_no 去重），中断后重跑本脚本即可接上。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time

TRJ = "${TRAJ_HOME}"
GRAPH = os.path.join(TRJ, "graphs/changeset-audit.v5.2.json")


def load_done(path: str) -> set[str]:
    done = set()
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["changeset_no"])
            except Exception:
                pass
    return done


def run_one(cs: str, outdir: str, errlog: str) -> dict:
    env = dict(os.environ)
    env["TRJ_DB_PATH"] = os.path.join(outdir, "runtime-dryrun.db")
    t0 = time.time()
    with io.open(errlog, "a", encoding="utf-8") as ef:
        p = subprocess.run(
            [sys.executable, os.path.join(TRJ, "runner/run_graph.py"), GRAPH,
             "--changeset", cs, "--workdir", os.path.join(outdir, "workdir"),
             "--run-tag", "TRJ-PROD-DRYRUN"],
            cwd=TRJ, capture_output=True, text=True, env=env)
        if p.stderr.strip():
            ef.write("### %s\n%s\n" % (cs, p.stderr))
    rec: dict = {"changeset_no": cs}
    last = [l for l in p.stdout.splitlines() if l.strip()]
    if last:
        try:
            rec.update(json.loads(last[-1]))
        except Exception:
            rec["overall"] = "PARSE_ERR"
            rec["raw_tail"] = last[-1][:300]
    else:
        rec["overall"] = "RUNNER_ERR"
        rec["stderr_head"] = p.stderr.strip()[:300]
    rec["graph_exit"] = p.returncode
    rec["graph_wall"] = round(time.time() - t0, 3)

    # 登记自洽性复核：纯读，不写任何台账
    r = subprocess.run([sys.executable, os.path.join(TRJ, "runner/drift_scan.py"),
                        "--recheck", cs], cwd=TRJ, capture_output=True, text=True)
    line = (r.stdout or "").strip().splitlines()
    rec["recheck"] = line[0][:400] if line else "(无输出)"
    rec["recheck_verdict"] = (
        "PASS" if "recheck=PASS" in rec["recheck"] else
        "N/A" if "recheck=N/A" in rec["recheck"] else
        "FAIL" if "recheck=FAIL" in rec["recheck"] else "?")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="历史全量试跑（观察模式）")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--model-budget", type=int, default=120,
                    help="累计模型调用硬上限；到顶即停并显式报出未跑数")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 单（调试用）")
    a = ap.parse_args()

    outdir = a.outdir
    units = [l.strip() for l in io.open(os.path.join(outdir, "全量单号.txt"),
                                        encoding="utf-8") if l.strip()]
    if a.limit:
        units = units[:a.limit]
    resfile = os.path.join(outdir, "试跑结果.jsonl")
    errlog = os.path.join(outdir, "试跑-runner-err.log")
    done = load_done(resfile)
    todo = [u for u in units if u not in done]
    print("总量 %d，已完成 %d，本轮待跑 %d" % (len(units), len(done), len(todo)), flush=True)

    spent = 0
    ran = 0
    t0 = time.time()
    for i, cs in enumerate(todo, 1):
        if spent >= a.model_budget:
            left = len(todo) - i + 1
            note = {"changeset_no": "__BUDGET_STOP__", "reason": "模型调用达上限 %d" % a.model_budget,
                    "unrun_count": left, "unrun_sample": todo[i - 1:i + 9]}
            with io.open(resfile, "a", encoding="utf-8") as f:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")
            print("**模型预算到顶，停。未跑 %d 单（已写进结果文件，不静默截断）**" % left, flush=True)
            break
        rec = run_one(cs, outdir, errlog)
        spent += int(rec.get("model_calls") or 0)
        ran += 1
        with io.open(resfile, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if i % 25 == 0 or rec.get("model_calls"):
            print("  [%d/%d] %s → %s / recheck=%s  模型累计 %d  用时 %.0fs"
                  % (i, len(todo), cs, rec.get("overall"), rec.get("recheck_verdict"),
                     spent, time.time() - t0), flush=True)
    print("本轮跑完 %d 单，模型累计调用 %d，总耗时 %.0fs" % (ran, spent, time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
