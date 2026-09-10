#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大批量试跑结果分析 · TRJ-PROD ①

按任务书口径出数：总量、判决分布、耗时与模型调用、错误日志新增、
新暴露的失败形态**按三病因分类**（判据病 / 登记病 / 执行器病）、FAIL 单抽样清单。

病因分类不是靠猜，是靠**判词里的可判定特征**：
  执行器病 = aborted=true 或 graph_exit!=0 或 overall in (RUNNER_ERR, PARSE_ERR)
             —— 图没跑完，跟被审对象无关
  登记病   = 复核 FAIL 且理由指向登记内容（锚点格式/不存在/基线对不上/路径转义）
  判据病   = 复核 FAIL 但人工已知判据本身有缺陷的形态（本轮：跨仓、免锚点）
             —— 两处已于 08-17 修复，若仍出现即为**回归**，单独标出
无法归类的一律进「待人判」，**不塞进任何一类凑数**。
"""

from __future__ import annotations

import collections
import io
import json
import sys

CAT_RUNNER = "执行器病"
CAT_REG = "登记病"
CAT_RULE = "判据病"
CAT_UNK = "待人判"


CHANGE_TYPES: dict[str, str] = {}


def load_change_types() -> dict[str, str]:
    """现查库拿 change_type。分类必须基于事实，不能靠「空锚点大概就是免锚点单」这种推断。"""
    import subprocess
    out = subprocess.run(
        ["/usr/local/bin/docker", "exec", "${DB_CONTAINER}", "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 -N -B ${DB_NAME} '
         '-e "SELECT changeset_no, IFNULL(change_type,\'\') FROM t_code_changeset WHERE is_del=0"'],
        capture_output=True, text=True).stdout
    d = {}
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            d[parts[0].strip()] = parts[1].strip()
    return d


def classify(r: dict) -> tuple[str, str]:
    """返回 (病因类, 形态标签)。只对「需要注意」的单分类；正常单返回 ('', '')。"""
    ov = r.get("overall")
    rc = r.get("recheck_verdict")
    reason = r.get("recheck", "")

    if r.get("aborted") or r.get("graph_exit") not in (0, None) or ov in ("RUNNER_ERR", "PARSE_ERR"):
        return CAT_RUNNER, "图未跑完(aborted/exit≠0)"

    if rc == "FAIL":
        if "锚点在已知仓库中均不存在" in reason:
            return CAT_REG, "锚点解析不开(全仓皆无)"
        if "登记未填 commit_hash" in reason:
            # [修正 <日期>] 初版把这一条一律记成判据病，理由是「08-17 已加免锚点豁免」。
            # **贴错了**：豁免只对 change_type=data_migration 成立，普通单空锚点判 FAIL 本就是对的
            # ——这正是该判据自检里那条反例（「豁免不许泛化到其它变更类型」）守的东西，
            # 我却在分析侧自己把它泛化了一遍。改为查真实 change_type 分流：
            #   data_migration 还判 FAIL -> 真回归（本该 N/A）
            #   其余             -> 登记病·未填锚点
            ct = (CHANGE_TYPES.get(r.get("changeset_no")) or "").strip()
            if ct == "data_migration":
                return CAT_RULE, "免锚点单仍判 FAIL —— **真回归**，08-17 豁免未生效"
            return CAT_REG, "登记未填锚点(change_type=%s，不属免锚点类型)" % (ct or "?")
        if "第一父链" in reason:
            if '"' in reason:
                return CAT_REG, "路径 git 转义残留(引号开头)"
            return CAT_REG, "基线与明细对不上(锚点填错/跨提交)"
        return CAT_UNK, "复核 FAIL 但理由未归类"

    if ov in ("FAIL", "NEEDS_HUMAN"):
        return CAT_UNK, "图判 %s 但复核 %s" % (ov, rc)
    return "", ""


def main() -> int:
    path = sys.argv[1]
    rows = [json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]
    CHANGE_TYPES.update(load_change_types())
    stops = [r for r in rows if r.get("changeset_no") == "__BUDGET_STOP__"]
    rows = [r for r in rows if r.get("changeset_no") != "__BUDGET_STOP__"]

    print("## 总量")
    print("- 实跑 **%d** 单" % len(rows))
    for s in stops:
        print("- ⚠ **预算截断**：%s，未跑 %d 单（样例 %s）"
              % (s["reason"], s["unrun_count"], "、".join(s.get("unrun_sample", [])[:5])))
    if not stops:
        print("- 无截断，全量跑完")

    print("\n## 图判决分布")
    for k, v in collections.Counter(r.get("overall") for r in rows).most_common():
        print("- `%s` %d 单（%.1f%%）" % (k, v, 100.0 * v / len(rows)))

    print("\n## 登记自洽性复核分布")
    for k, v in collections.Counter(r.get("recheck_verdict") for r in rows).most_common():
        print("- `%s` %d 单（%.1f%%）" % (k, v, 100.0 * v / len(rows)))

    walls = [float(r.get("graph_wall") or 0) for r in rows]
    mcalls = [int(r.get("model_calls") or 0) for r in rows]
    walls_sorted = sorted(walls)
    print("\n## 耗时与模型调用")
    print("- 总耗时 %.0f 秒（%.1f 分钟），单单均值 %.2fs，中位 %.2fs，最慢 %.1fs"
          % (sum(walls), sum(walls) / 60, sum(walls) / len(walls),
             walls_sorted[len(walls_sorted) // 2], max(walls)))
    print("- **模型调用合计 %d 次**，涉及 %d 单（%.1f%%）；其余 %d 单纯机械判定"
          % (sum(mcalls), sum(1 for m in mcalls if m), 100.0 * sum(1 for m in mcalls if m) / len(rows),
             sum(1 for m in mcalls if not m)))
    slow = sorted(rows, key=lambda r: -float(r.get("graph_wall") or 0))[:5]
    print("- 最慢 5 单：%s" % "、".join(
        "%s(%.0fs,model=%s)" % (r["changeset_no"], float(r.get("graph_wall") or 0), r.get("model_calls"))
        for r in slow))

    print("\n## 新暴露的失败形态（按三病因分类）")
    buckets: dict[str, dict[str, list[str]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        cat, tag = classify(r)
        if cat:
            buckets[cat][tag].append(r["changeset_no"])
    if not buckets:
        print("- 无")
    for cat in (CAT_REG, CAT_RULE, CAT_RUNNER, CAT_UNK):
        if cat not in buckets:
            continue
        total = sum(len(v) for v in buckets[cat].values())
        print("\n### %s（%d 单）" % (cat, total))
        for tag, units in sorted(buckets[cat].items(), key=lambda kv: -len(kv[1])):
            print("- **%s**：%d 单 —— %s%s"
                  % (tag, len(units), "、".join(units[:8]), " …" if len(units) > 8 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
