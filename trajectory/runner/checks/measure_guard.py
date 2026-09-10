#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量哨兵：**出数之前先证明取数工具没坏**

## 为什么有这个模块

<日期> 一天之内，同一个错犯了三次：

| # | 假结论 | 真因 | 假结论长什么样 |
|---|---|---|---|
| 1 | 「判据病 16 单」 | 分析分类器把 data_migration 豁免泛化了 | 一整类都归到同一个病因 |
| 2 | 「HOLD 63 单（11.9%）」 | `fetch()` 的 `.strip()` 吃掉前导空字段，字段整体左移 | 34 单四条判据齐挂 |
| 3 | 「误报率 0/42」 | 逐单拼的 SQL 在 shell 里坏掉返回空串，判空逻辑把全部归进「无明细」 | 42 单全是同一形态 |

三次都**不是判据坏了，是测量工具坏了**。而三次的假结果都比真结果**更整齐**——
空查询给出「全都无明细」，错位读数给出「四条判据齐挂」。

> **干净就是失败的形状。**

判据侧早有纪律（硬约定 5/6/11：每条分支正反例齐全，A 门 23 例、复核 14 例）。
**测量侧一例都没有。** 三次出错的全是测量侧。这个模块把同一条纪律搬过来。

## 怎么用

```python
from measure_guard import guard
guard.rowcount("全库单数", got=len(rows), want_at_least=500)
guard.sentinel("A门-正常单", got=evaluate("CS-<日期>-0011")["verdict"], want="PASS")
# 一批样本全落同一个值时，先证明工具还分得开两态，再谈这个一致算不算数
guard.discriminates("图判决工具两态自证",
                    ("0067", run("CS-<日期>-0067"), "PASS"),
                    ("0117", run("CS-<日期>-0117"), "ALERT_MERGED_WITHOUT_AUDIT"))
guard.report()           # 返回布尔，由调用方决定失效动作
```

> `require()` 抛的是 `SystemExit`（`BaseException` 子类），调用方的 `except Exception`
> **接不住**——哨兵一响不是「拦住」而是进程静默退出。除非你就想要那个行为，
> 否则用 `report()`，把失效方向显式写在调用侧。

哨兵值都是**当天人工逐条核实过**的，不是从别处抄的期望值。
哨兵挂掉时不许「先出数再说」——那正是三次错误的共同做法。
"""

from __future__ import annotations

import os


# 「整齐可疑」判据的适用规模下限（硬约定 7 参数化）。低于此数时全同是正常现象。
MIN_N_FOR_VARIETY = int(os.environ.get("GUARD_MIN_N_FOR_VARIETY", "10"))


class MeasureGuard:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def rowcount(self, what: str, got: int, want_at_least: int) -> None:
        """取数条数下限。查询坏掉最常见的表现就是返回空/极少行。"""
        ok = got >= want_at_least
        self.checks.append((what, ok, "取到 %d 条（下限 %d）" % (got, want_at_least)))

    def sentinel(self, what: str, got, want) -> None:
        """已知答案的样本。答案不符 = 工具坏了，不是数据变了。"""
        ok = (got == want)
        self.checks.append((what, ok, "得到 %r，期望 %r" % (got, want)))

    def not_all_same(self, what: str, values: list, min_n: int = MIN_N_FOR_VARIETY) -> None:
        """整齐即可疑：一批样本若全部落到同一个值，多半是取数坏了而不是世界这么干净。

        **但小样本全同是正常的**——[修 <日期>] 本判据第一次真开火就是误拦：
        B 消费线跑 4 张同类单（都是 ALERT+已并线+复核PASS），4 个样本全落 ARCHIVE，
        判据判「工具不可信」拒绝出数。那 4 张本来就该同路由，**同质是事实不是故障**。
        加适用规模下限：样本数 < min_n 时本判据判 N/A，不参与结论。
        这与 A6 的时点边界是同一类修正——**判据本身对，但有适用域，不写下来就会误伤**。
        """
        uniq = set(map(str, values))
        if len(values) < min_n:
            self.checks.append((what, True,
                               "%d 个样本 < 下限 %d，样本太小不足以判「整齐可疑」，本判据 N/A"
                               % (len(values), min_n)))
            return
        ok = len(uniq) > 1
        self.checks.append((what, ok, "%d 个样本落在 %d 个不同值上" % (len(values), len(uniq))))

    def discriminates(self, what: str, probe_a: tuple, probe_b: tuple) -> None:
        """一致性结果的解药：**先证明工具还分得开两态**。

        `not_all_same` 只指得出「整齐可疑」，指不出该怀疑谁——
        整齐既可能是工具坏了，也可能是这批样本本身同质。分辨办法只有一个：
        拿两个**已知答案不同**的输入过同一套取数工具，看它还给不给得出不同答案。
        给不出 = 工具坏了，此前那批一致结果一律作废；
        给得出 = 一致是样本的性质，可以照常出数。

        实测用法（<日期>）：抽样 10 张历史存量单，10/10 全落 ALERT，整齐可疑。
        于是拿 `CS-<日期>-0067`（已知 PASS）与 `CS-<日期>-0117`（已知 ALERT）
        过**同一沙箱同一条命令**，得 PASS / ALERT 两态——才敢说那 10 张的整齐是真的。

        探针各是 `(标签, 实得值, 期望值)`。三条都要成立才算通过：
        两个探针各自答对，**且两者的期望值本身不同**——期望值相同的「两态探针」
        不是两态探针，它恒真，等于没验。
        """
        (na, ga, wa), (nb, gb, wb) = probe_a, probe_b
        two_state = (wa != wb)
        ok = two_state and ga == wa and gb == wb
        self.checks.append((what, ok,
                            "%s 得 %r(期望 %r)；%s 得 %r(期望 %r)%s"
                            % (na, ga, wa, nb, gb, wb,
                               "" if two_state else "　——两探针期望值相同，这不是两态探针")))

    def report(self) -> bool:
        allok = True
        print("── 测量哨兵 ──")
        for what, ok, detail in self.checks:
            allok &= ok
            print("  %-28s %-4s %s" % (what[:28], "OK" if ok else "**坏**", detail))
        print("  哨兵结论：%s" % ("工具可信，可以出数" if allok else "**工具不可信，拒绝出数**"))
        return allok

    def require(self) -> None:
        if not self.report():
            raise SystemExit("测量哨兵未通过——按 08-17 三次教训，此时出的数一律不可信，不出。")


guard = MeasureGuard()


def selftest() -> int:
    """哨兵自己的正反例。**守卫也要证明自己会红**——一个永远绿的哨兵是最坏的哨兵，
    它让所有人以为取数验过了。"""
    cases = []

    def case(name, build_fn, want_ok):
        g = MeasureGuard()
        build_fn(g)
        got_ok = all(ok for _, ok, _ in g.checks)
        cases.append((name, got_ok == want_ok, "得 %s 期望 %s" % (got_ok, want_ok)))

    case("正例·行数达标", lambda g: g.rowcount("x", got=600, want_at_least=500), True)
    case("反例·行数不足必须红", lambda g: g.rowcount("x", got=3, want_at_least=500), False)
    case("正例·哨兵值对上", lambda g: g.sentinel("x", got="PASS", want="PASS"), True)
    case("反例·哨兵值不符必须红", lambda g: g.sentinel("x", got="HOLD", want="PASS"), False)
    case("反例·大样本全同必须红",
         lambda g: g.not_all_same("x", ["A"] * 20), False)
    case("正例·大样本有差异",
         lambda g: g.not_all_same("x", ["A"] * 19 + ["B"]), True)
    case("正例·小样本全同判 N/A 不误伤",
         lambda g: g.not_all_same("x", ["A"] * 4), True)
    case("正例·两态自证：两探针各自答对且期望不同",
         lambda g: g.discriminates("x", ("a", "PASS", "PASS"), ("b", "ALERT", "ALERT")), True)
    case("反例·工具恒返回同一值必须红",
         lambda g: g.discriminates("x", ("a", "PASS", "PASS"), ("b", "PASS", "ALERT")), False)
    case("反例·两探针期望相同=假两态，必须红",
         lambda g: g.discriminates("x", ("a", "PASS", "PASS"), ("b", "PASS", "PASS")), False)

    ok = True
    for name, good, detail in cases:
        ok &= good
        print("  %-34s %-6s %s" % (name[:34], "OK" if good else "**不符**", detail))
    print("\n自检：%s" % ("全部符合" if ok else "**有不符项**"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(selftest())


# ── 本线常用哨兵（值均为 <日期> 人工逐条核实） ──────────────────────
SENTINELS = {
    # 单号 -> (A门判定, 说明)
    "CS-<日期>-0011": ("PASS", "正常单：40位锚点、23文件与明细相符、已并线"),
    "CS-<日期>-0032": ("HOLD", "空锚点 feature：A1 与 A5 应同时挂"),
    "CS-<日期>-0019": ("PASS", "verification 零明细：应被免明细白名单放行"),
    "CS-<日期>-0008": ("PASS", "曾是9位短哈希，主窗口已补全40位——现应放行"),
}
FULL_DB_MIN_ROWS = 500          # 全库单数下限（实际 531，留裕量防真删单误触）
