# 轨迹归纳报告 — changeset-audit v1

数据来源: `${TRAJ_DATA_DIR}/changeset-audit/round-01..10-CS-<日期>-0055..0064.jsonl`（10 条真实执行轨迹，每条 16 行 = 1 input + 14 step + 1 verdict）
生成命令: `python3 inducer/induce.py "${TRAJ_DATA_DIR}/changeset-audit" graphs/changeset-audit.v1.json`
输出图: `graphs/changeset-audit.v1.json`（15 节点：tool×8、branch×5、map×1、llm×1）

## 1. 每个 seq 的稳定性统计表

"同构 n/10" = 把变更单号/sha/changeset_id 等易变量遮蔽后，10 轮的 cmd 文本逐字比对相同的轮数。全部 14 步在本数据集里都是 10/10（无 needs_review）。

| seq | kind | 节点类型 | 同构 n/10 | 变量 (bindings) | output_contract |
|---|---|---|---|---|---|
| 1 | sql | tool | 10/10 | `changeset_no` ← `input.changeset_no` | `regex:^id=\d+\tchangeset_no=CS-\d{8}-\d{4}\ttitle=.+\tcommit_hash=[0-9a-f]{40}\tfile_count=\d+$` |
| 2 | compare | **branch** (→p1) | 10/10 | `sha` ← `n1.output.commit_hash` | `regex:^REGEX_OK$` |
| 3 | git | tool | 10/10 | `sha` ← `n1.output.commit_hash` | `regex:^commit$` |
| 4 | sql | tool | 10/10 | `changeset_id` ← `n1.output.id` | `regex:^count=\d+$` |
| 5 | git | tool | 10/10 | `sha` ← `n1.output.commit_hash` | `regex:^count=\d+$` |
| 6 | compare | **branch** (→p2) | 10/10 | `db_changeset_file_count` ← `n1.output.file_count`；`db_change_file_count` ← `n4.output.count`；`git_count` ← `n5.output.count`（三者数值全部相等，见 §3 说明） | `regex:^PASS \(db_changeset_file_count=\d+ db_change_file_count=\d+ git_count=\d+\)$` |
| 7 | sql | tool | 10/10 | `changeset_id` ← `n1.output.id` | `regex:^\d+ rows sorted;.*$` |
| 8 | git | tool | 10/10 | `sha` ← `n1.output.commit_hash` | `regex:^\d+ rows sorted;.*$` |
| 9 | compare | **branch** (→p3, **llm 分支**) | 10/10 | `changeset_no` ← `input.changeset_no`；`db_files` ← `n7.output`(结构绑定)；`git_files` ← `n8.output`(结构绑定) | `regex:^diff_line_count=\d+;.*$` |
| 10 | sql | tool | 10/10 | `changeset_no` ← `input.changeset_no` | `regex:^title=.+\thex_left3=[0-9A-F]{6}$` |
| 11 | compare | **branch** (→p4) | 10/10 | `title` ← `n10.output.title`（按名匹配，见 §3 说明） | `nonempty` |
| 12 | sql | tool | 10/10 | `changeset_id` ← `n1.output.id` | `regex:^\d+ non-delete files:.*$` |
| 13 | git | **map** | 10/10 | `sha` ← `n1.output.commit_hash`；`list_binding` ← `n12.output` | `regex:^\d+ files checked; files_with_cr>\d+:.*$` |
| 14 | compare | **branch** (→p5) | 10/10 | (无模板变量，`depends_on: [n13]` 靠 cmd 文本里的 "STEP13" 引用识别) | `nonempty` |

节点总数 15（14 个步骤节点 + 1 个 `n9_llm` 决策节点）。UNRESOLVED 绑定总数 = **0**。

## 2. 判决点 (p1-p5) 归纳结果

5 个 `compare` 步骤（seq 2/6/9/11/14）与 5 个判决点一一对应（按 seq 出现顺序）：

| point | 决定节点 | 依赖步骤 | 类型 | 10轮一致性 |
|---|---|---|---|---|
| p1 | n2 | n1,n2,n3 (commit_hash 格式+存在性) | mechanical | 10/10 |
| p2 | n6 | n4,n5,n6 (三方文件数链式相等) | mechanical | 10/10 |
| p3 | n9 | n7,n8,n9 (文件路径清单 diff) | **llm** | 10/10（但见下方说明） |
| p4 | n11 | n10,n11 (标题乱码检测) | mechanical | 10/10 |
| p5 | n14 | n12,n13,n14 (CRLF 检查) | mechanical | 10/10 |

**为什么 p3 是 llm 而不是机械规则**：单看这 10 轮的数字，"n9 的 diff 行数 == 0 → PASS" 这条机械规则和真实判决 100% 吻合，没有反例。但其中 2 轮（round1、round3）diff 行数非零（10、4）却仍然是因为 git 对非 ASCII 文件名做八进制转义显示（`core.quotepath`）导致的假阳性——两侧其实是同一批文件，fail_detail 原文分别写着 "Same 15 files present on both sides" 和 "Same 2 files present"。这说明"diff 行数是否为 0"这个数字信号本身不足以下判断，需要读 diff 内容语义才能分辨"真的少了文件"还是"只是路径编码显示方式不同"。因此图里把 p3 的非零分支单独路由到 `n9_llm` 节点，而不是写死"diff>0 就 FAIL"。

判决规则归纳方法：14 步里的 5 个 `compare` 步骤按 seq 顺序与 p1..p5 一一对应，脚本用全部 10 轮数据核对这个假设（核对每个 compare 步骤的"好/坏信号"与对应 p_i 是否 100% 一致），核对失败会直接报错终止而不是静默接受。另外用两个可从数据验证的锚点交叉核实分组边界：p3 的 fail_detail 内容与 n9 自身 output_digest 高度重合，p5 的 fail_detail 与 n14 自身 output_digest 高度重合。

## 3. 绑定归因里的两个技术细节（非 UNRESOLVED，但值得说明）

- **n6 的三个计数变量**：`db_changeset_file_count`(n1.file_count)、`db_change_file_count`(n4.count)、`git_count`(n5.count) 在全部 10 轮里数值都相等（PASS 的前提就是三者相等），单纯"按值匹配"无法区分该绑定到哪个来源——三个候选都对得上。归纳器用两个信号解决：(a) `test A -eq B -a B -eq C` 语句本身的结构决定了 3 个语义槽位（第2、3槽位合并成同一个变量），槽位名来自 compare 步骤自身输出里的叙述性括注 `(db_changeset_file_count=.. db_change_file_count=.. git_count=..)`（按位置对应，不按值，因为值全部相等会导致按值对应塌缩成同一个名字）；(b) 同一节点内不同变量优先不复用已经分配给别的变量的来源（贪心占位），配合"更早出现的来源优先"的默认顺序，取得 n1→n4→n5 这个符合语义的分配。绑定结果里保留了 `ambiguous_candidates` 字段如实记录候选集合，供人工复核。
- **n9/n10 的 `changeset_no`**：候选来源同时包含 `input.changeset_no` 和 `n1.output.changeset_no`（因为 n1 自己的 SQL 结果也回显了 changeset_no），归纳器优先选择"更根源"的 `input.changeset_no`，同样保留 `ambiguous_candidates` 供复核。
- **n11 的 `title`**：轨迹里这一步的 cmd 原文是 `echo "$TITLE" | grep ...`，`$TITLE` 是字面 shell 变量引用，轨迹记录器没有把它展开成真实标题文本（类似 seq13 用字面占位符 `<non-delete files>` 代表循环列表，没有展开成真实文件名）。因此这个绑定不是"按值匹配"，而是"按字段名匹配"：在更早步骤的输出字段里找名字为 `title` 的字段，命中 `n10.output.title`。这也是为什么用"回填 cmd_template + bindings 应该等于真实 cmd"做自检时，n11 这一项必然对不上（回填出的是真实标题文本，原始 cmd 永远是字面 `$TITLE`）——这是原始数据的记录方式决定的，不是归纳错误。

## 4. UNRESOLVED / needs_review 清单

**空。** 14 个步骤、全部变量、5 个判决点均在 10 轮数据下归因成功且 10/10 一致，无 UNRESOLVED，无 needs_review。

## 5. 自洽性验证（对生成的图做的额外交叉核验，非任务要求的验证命令，但强烈建议保留）

1. **output_contract 自洽**：对全部 15 个节点、10 轮共 140 条真实 output_digest 逐条用图里的 `output_contract` 校验，全部匹配。
2. **cmd_template + bindings 回填自洽**：对全部 tool/branch 节点（n1-n12, n14），用图里的 `cmd_template` 和 `bindings` 回填每一轮的实际变量值，与真实轨迹里记录的原始 `cmd` 逐字比对，130 组回填全部一致（唯一例外是 n11 的 10 轮，原因见 §3 第三点，是原始数据的字面占位符导致，非归纳错误）。
