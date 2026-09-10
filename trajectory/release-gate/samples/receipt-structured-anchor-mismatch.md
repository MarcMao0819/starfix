# CS-DEMO-ANCHORMISMATCH — 样例回执（A1 结构化判词行·锚点不一致场景）

> 本文件是 release-gate 的测试样例，不是真实回执，仅用于验证 A1 三级判据的第一级
> （结构化判词行）在 `anchor` 与登记 `commit_hash` 不一致场景下判 FAIL（锚点不一致）。
> 刻意放在 `release-gate/samples/` 下，不进真实回执目录
> `${FLEET_HOME}/<项目>ERP/迁移备份/回执/`。

- **状态**：PASS（完工交付；合并归主窗口）——承建方自报文本，验证时不应影响判决

## 校验官判词（结构化，机器可读）

verified_by=校验官 / verdict=PASS / anchor=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa / ts=<日期>T12:10:00Z

> 本样例的 anchor 是刻意造的假 40 位 hex（全 a），模拟"校验官判词签的是另一个提交，
> 与当前登记 commit_hash 不一致"——对应 <日期> RAMODEL「验收后又追加提交」同款
> 第二道防线场景。
