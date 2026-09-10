# 归纳报告：通用化 induce.py + 图②(回执合规) 归纳

## 0. 重要前提：轨迹数据在任务执行期间发生了变化，必须先说明

任务书读取白名单里列出的 `receipt-compliance/round-*.jsonl` 在本次任务**执行过程中被外部进程重写了一次**：

- 第一次读取(约 18:17-18:18)：每份 21 行(1 input + 19 step + 1 verdict)，判决点只有 `r1..r6`，`doc_type` 只有 `nondelivery`/`code_delivery` 两种，round5(UNITFIX) 命中"打回"但机械规则误判 `r6=FAIL`。
- 归纳脚本第一次真正运行前又重新读取(文件 mtime 18:32:49-18:32:52，明显晚于第一次读取)：每份变成 11 行(1 input + 9 step + 1 verdict)，判决点变成 `e1,e2,r1..r6`(8个)，`doc_type` 新增第三种 `process_log`，round5(UNITFIX) 现在正确判 `r6=N/A`，而 round6(REVASSOC) 命中"打回"且判决脚本自己产出了 `AMBIGUOUS`(而不是 FAIL)。

也就是说：**任务书原文描述的"UNITFIX 是误判 FAIL 的边界案例"这个具体事实，在归纳时已经不成立**——底层的 check.py 判决脚本显然已经被同步改进过，UNITFIX 现在被正确处理为 N/A。真正需要 llm 兜底的样本变成了 REVASSOC(判决脚本自己给出 `AMBIGUOUS` 终值)。

**本报告和图②严格按归纳脚本实际读到的、当前这一版数据来写**，不会为了迎合任务书原文而编造 UNITFIX 仍是 FAIL 误判的说法。这是"验证后再下结论"的要求——任务书原文关于 UNITFIX 的具体断言已经被数据本身推翻，只保留了它想要的**结构性意图**(r6 需要 llm 兜底、不能对"命中打回词但缺锚点"写死机械规则)，这一意图在新数据里由 `AMBIGUOUS` 终值原生满足。

---

## 1. induce.py 通用化改动清单

### 1.1 CLI 与流程识别
- `python3 induce.py <轨迹目录> <输出图路径> [--flow <流程名>]`；不传 `--flow` 时按轨迹目录 basename 自动识别流程名。
- `flow == 'changeset-audit'` 时走 `build_graph_changeset_audit`(老逻辑原样保留，作为回归基线)；其余流程一律走 `build_graph_generic`(新通用引擎)。

### 1.2 输入变量自动推断
- `discover_input_vars(rounds)`：input 行除 `type`/`ts` 外的键即输入变量，按原始出现顺序，校验全部轮次键集合一致。
- changeset-audit 路径也改用这个函数(不再写死 `["changeset_no"]")，功能等价(该流程本来就只有一个输入变量)。

### 1.3 判决点键动态发现
- `discover_verdict_points(rounds)`：从 `verdict.points` 的键动态发现，按"字母前缀+数字"排序(避免 p10 排到 p2 前面)。
- changeset-audit 路径新增一条断言：动态发现的键集合必须等于 `GROUPS` 人工分组表隐含的 `p1..p5`，不等就直接报错——`GROUPS` 本身仍是人工阅读全部轮次数据后确定的语义分组知识(这一点无法纯统计复现，老版本的说明文字已经讲得很清楚)，通用化体现在"键集合是否还是 p1..p5"这件事从写死变成了运行时校验。

### 1.4 分型输出(classification)识别 —— 新增能力
- `discover_classification_fields(rounds)`：verdict 里除 `pass`/`points`/`fail_detail`/`type`(记录类型标记) 外的额外字段即分型字段(例如 `doc_type`)。
- `classify_classification_role(seq, rounds, classification_fields)`：某步骤输出的某个字段值，在全部轮次都与某个分型字段的值逐轮相等，即判定该步骤是分型决策节点——**按值匹配，不按字段名匹配**(receipt-compliance 里输出字段叫 `type`，verdict 分型字段叫 `doc_type`，名字不同但值相同，靠值关联识别出来)。
- 识别结果写入图顶层 `classification` 段：`field`/`decision_node`/`own_output_key`/`values`/`gates`(从 verdict_rules 里 `conditional`+`applicable_when` 反查哪些点被这个分型字段门控)/`criteria`(人工核实提示)。

### 1.5 N/A 判决值支持 —— 新增能力
- `find_na_condition(point_key, rounds, classification_fields, classification_values)`：某判决点存在 N/A 轮次时，检查是否存在某个分型字段，使得 "N/A 轮次的取值集合" 与 "非 N/A 轮次的取值集合" 完全不相交(允许两侧各自有多个不同取值，只要求不相交——这是相对最初实现的一个修正，见下文 1.5.1)。找到就标 `conditional: true` + `applicable_when`；找不到就诚实标 `unresolved`/`needs_review`，不瞎猜。
- **1.5.1 修正记录**：最初实现要求"N/A 侧只能有且只有一个取值"才采信，这在真实数据里跑不通——receipt-compliance 的 `r2`/`r4` 在 `process_log` 和 `nondelivery` **两种**doc_type 下都是 N/A，只有 `code_delivery` 下才适用，N/A 侧有 2 个不同取值。放宽成"只要求两侧集合不相交"后，r1/r2/r3/r4/r5 的条件适用全部正确推出(见第 3 节)。

### 1.6 判决点决策节点识别 —— 双判据，新增 cmd 参数判据
- `classify_point_role`(老判据，字段名匹配)：某步骤输出字段名直接就是判决点键(例如输出里字面有 `"p3"`)，且值在全部轮次都与 verdict.points 一致。
- `classify_point_role_by_cmd_arg`(**新增判据**)：有些流程的判决脚本统一只输出通用字段名(receipt-compliance 全部用 `verdict=`)，判决点身份体现在 **cmd 参数**里(例如 `check.py r1 receipt.md` 的 `r1`)。命中判据：cmd 参数整词命中且只命中一个判决点键，且该步骤输出的 `verdict` 字段，在 `verdict.points[该键]` **不是 N/A 的轮次里**全部与之一致(N/A 轮次可能被更上层分型条件覆盖，不强求这一步骤自己也算出 N/A——那正是条件适用要单独推导的部分)。

### 1.7 结构位置补齐 —— 新增能力，本次归纳的关键发现
- `gap_fill_orphan_points(point_keys, claimed_seq_by_key, orphan_seqs)`：有些判决点**没有专属决策步骤**，直接由相邻的普通工具步骤隐式决定(receipt-compliance 的 `e2`/`r2`/`r4` 就是这样：只有 `cr_count`/`sha40_count`/`file_path_count` 这三个 grep 计数步骤，没有对应的 `check.py e2/r2/r4` 调用)。
- 规则：按 `point_keys` 排序后的顺序，把两个相邻"已认领判决点"之间的未认领判决点，和同一 seq 区间内的孤儿工具步骤按出现顺序一一配对；**数量对不上就不猜**，标记 `needs_review`，不产出错误映射。
- 这类节点仍按普通 tool 节点构建(有自己的 bindings/output_contract)，只是额外标注 `verdict_point` + `needs_review`，如实说明"具体判决门槛(例如 count>0 才 PASS)未经数据统计验证"——因为样本内这三个计数值从未取到过会导向 FAIL 的取值，无法验证真实门槛，只能类推自字段名的常识含义。

### 1.8 判决脚本自带"第三态"终值的通用 llm 路由 —— 新增能力，替代了最初设想的手工覆写
- 任务书原本要求为 `r6` **手工设计**一套"命中打回词+有追加->PASS机械快路径 / 命中打回词+无追加->转llm / 未命中->N/A"的三路覆写。
- 但当前真实数据显示：`r6` 的判决脚本自己已经会产出 `AMBIGUOUS` 这个第三态终值(不是 PASS/FAIL/N/A)，这就是任务书想要的语义边界，只是判决脚本自己已经实现了，不需要本归纳器手工覆写。
- 于是把这条能力做成**通用**规则：`_fill_point_branch_fields` 遇到任何非 `{PASS, FAIL}` 且非 `N/A` 的终值，一律记入 `cases` 并路由到自动生成的 llm 节点(`_build_generic_llm_node`)，`ambiguous_examples` 只取轨迹里已有的 output_digest/verdict 字段拼出来，不读回执原文。这条规则不针对 receipt-compliance 写死，任何流程只要判决脚本自己产出了 PASS/FAIL/N-A 之外的值都会触发。

### 1.9 变量遮蔽通用化
- `parse_fields` 的字段分隔符从"只认 tab"扩展为"tab 或 `; ` 或 `/`(仅当 `/` 后紧跟 `[a-z_][a-z0-9_]*=` 形态的小写字段名时才当分隔符，用零宽先行断言判断，避免把文件路径 `docs/02-xxx/README.md` 误切开)"。已用脚本核对 changeset-audit 全部 output_digest 都不含 `/[a-z_]+=` 这种形态，新增分隔符不影响该流程行为(已跑 `grep` 验证，见下方"回归结果")。
- 新增"已知输入变量字面值遮蔽"：把当前流程自动发现的每个输入变量在这一轮的实际值，作为字面子串去匹配并替换成 `{var}`，取代 changeset-audit 专属的 `CSNO_RE`(写死"变更单号"这个*形状*)。同时支持 **basename 兜底**：cmd 里如果只引用了输入变量的文件名部分(而非完整路径)，也能识别(receipt-compliance 的 `check.py` 调用只传 basename)。
- 这一遮蔽逻辑现在对**全部**节点一视同仁(包括判决/分型角色节点)——最初实现里判决角色节点被跳过遮蔽(沿用 changeset-audit 里 `exec:logic` 节点是纯伪代码、不含轮次相关值的假设)，但 receipt-compliance 的判决脚本调用是真实可执行命令、确实引用了输入变量，跳过遮蔽会导致这些节点的 `stability` 被误判成 1/12(因为每轮 cmd 里的文件名不同、原始文本逐字比较必然不同)。已修正为统一遮蔽后，全部节点 stability 正确显示 12/12。

### 1.10 保持不变的能力
- 变量遮蔽结构性正则(`CSNO_RE`/`SHA40_RE`/`CHANGESET_ID_RE`/`TESTEQ_RE`/`DOLLAR_VAR_RE`/`STEP_MENTION_RE`)一字未改。
- 变量绑定归因 `resolve_binding`：仍要求全部轮次一致指向同一个 origin_path 才算绑定成功，否则 UNRESOLVED，逻辑未改。
- `output_contract` 归纳 `infer_contract`：分层尝试逻辑未改。
- **稳定判据**：翻遍全文没有找到"≥80% 视为确定性"这类比例阈值，老版本要求"遮蔽后模板全轮(N/N)完全一致"才算确定性节点，本次沿用同一标准，未新增按比例放宽的逻辑——如果任务书这条描述来自别处的口径，这里如实说明现状，没有杜撰一个新阈值。

---

## 2. changeset-audit 回归结果：**逐字节等价**

```
diff /tmp/baseline-audit.json /tmp/regress-audit.json
```
输出为空，即通用化后的脚本对 changeset-audit 10 轮轨迹产出的图，与通用化之前跑一遍存下的基线**逐字节完全相同**(15 节点，tool8/branch5/map1/llm1，UNRESOLVED=0，5 条 verdict_rules 内容逐字一致)。过程中一度因为两处笔误(注释里"10轮"被误改成"轮次"；`_fill_point_branch_fields` 一度误写成 `return llm_values, llm_values`)导致输出有差异或直接崩溃，均已定位修复并重新验证到逐字节等价为止。

---

## 3. 图②(receipt-compliance) 归纳统计表

轮次数：12(round01-round12)。每轮 9 步(seq1-9)，全部轮次步骤数一致。

| seq | kind | 同构(遮蔽后模板一致) | 类型 | 变量/绑定 | output_contract | 判决点 | 备注 |
|---|---|---|---|---|---|---|---|
| n1 | logic | 12/12 | branch(分型) | receipt <- input.receipt | nonempty | — | t0，`type`字段值与`doc_type`逐轮相等，按值匹配识别为分型节点 |
| n2 | logic | 12/12 | branch | receipt <- input.receipt | `regex:^hits=\d+/verdict=PASS$` | e1 | cmd 参数含"e1"整词，按 cmd-arg 判据识别 |
| n3 | grep | 12/12 | tool | receipt <- input.receipt | `regex:^cr_count=\d+$` | e2(结构补齐) | 没有专属判决步骤，needs_review |
| n4 | logic | 12/12 | branch | receipt <- input.receipt | nonempty | r1 | cmd-arg 判据 |
| n5 | grep | 12/12 | tool | receipt <- input.receipt | `regex:^sha\d+_count=\d+$` | r2(结构补齐) | needs_review；契约里"40"被误当成变量数字(见第4节可疑点) |
| n6 | logic | 12/12 | branch | receipt <- input.receipt | nonempty | r3 | cmd-arg 判据 |
| n7 | grep | 12/12 | tool | receipt <- input.receipt | `regex:^file_path_count=\d+$` | r4(结构补齐) | needs_review |
| n8 | logic | 12/12 | branch | receipt <- input.receipt | nonempty | r5 | cmd-arg 判据 |
| n9 | logic | 12/12 | branch | receipt <- input.receipt | nonempty | r6 | cmd-arg 判据；产出过 AMBIGUOUS，自动路由到 n9_llm |
| n9_llm | — | — | llm | — | — | r6 | 通用第三态终值路由，非手工覆写 |

**节点类型分布**：`branch:6`(n1,n2,n4,n6,n8,n9)、`tool:3`(n3,n5,n7)、`llm:1`(n9_llm)，共 10 节点。

**变量与绑定**：全部 9 个真实步骤只有一个绑定变量 `receipt`，均归因到 `input.receipt`(10/10 即 12/12 一致，无 UNRESOLVED)。`lines` 是 input 行里的第二个字段，自动列入 `input_vars`，但没有任何 cmd 引用它，不产生绑定(如实反映：不是所有发现的输入变量都一定被消费)。

**UNRESOLVED 绑定总数：0**。

---

## 4. 判决点归纳依据 + N/A 条件适用推导结果

### 4.1 决策节点识别依据
- e1/r1/r3/r5/r6：cmd 参数里整词命中判决点键(例如 `check.py r1 ...`)，且该步骤输出的通用 `verdict` 字段在"该点非 N/A 的轮次"里全部与 `verdict.points` 一致。
- t0：输出字段 `type` 的值与 `verdict.doc_type` 逐轮相等(按值不按名匹配)。
- e2/r2/r4：**没有专属决策步骤**。按 `point_keys` 排序后的位置，在两个相邻已认领判决点之间找孤儿工具步骤一一配对：`e1(n2)…gap…r1(n4)` 之间只有 1 个孤儿步骤 `n3(cr_count)`，配给 `e2`；`r1(n4)…gap…r3(n6)` 之间只有 1 个孤儿步骤 `n5(sha40_count)`，配给 `r2`；`r3(n6)…gap…r5(n8)` 之间只有 1 个孤儿步骤 `n7(file_path_count)`，配给 `r4`。数量严格一一对应(1个缺口配1个孤儿步骤)，没有出现对不上的情况。

### 4.2 N/A 条件适用推导结果(核心验证目标)

| 判决点 | N/A 轮次 | N/A 侧 doc_type 取值 | 非N/A侧 doc_type 取值 | 是否不相交 | 推导结果 |
|---|---|---|---|---|---|
| e1 | 无 | — | — | — | 不设条件(全部轮次均适用) |
| e2 | 无 | — | — | — | 不设条件(全部轮次均适用) |
| r1 | round01(process_log) | {process_log} | {code_delivery, nondelivery} | 是 | `applicable_when: doc_type∈[code_delivery, nondelivery]` |
| r2 | round01,02,04(process_log/nondelivery) | {process_log, nondelivery} | {code_delivery} | 是 | `applicable_when: doc_type∈[code_delivery]` |
| r3 | round01,02,04 | {process_log, nondelivery} | {code_delivery} | 是 | `applicable_when: doc_type∈[code_delivery]` |
| r4 | round01,02,04 | {process_log, nondelivery} | {code_delivery} | 是 | `applicable_when: doc_type∈[code_delivery]` |
| r5 | round01(process_log) | {process_log} | {code_delivery, nondelivery} | 是 | `applicable_when: doc_type∈[code_delivery, nondelivery]` |
| r6 | round01,02,03,04,05,07-12(11轮) | {process_log, nondelivery, code_delivery} | {code_delivery}(仅round06) | **否，有交集(code_delivery 两侧都出现)** | 找不到完美相关的分型字段，**不是 doc_type 门控**——r6 的 N/A/AMBIGUOUS 由它自己内部的 `trigger` 字段决定，与文档类型无关，这是符合预期的正确结果，不是算法缺陷(见下方第5节可疑点①的说明) |

结论：**r1/r5 与 r2/r3/r4 的适用条件都被正确推出，且方向正确**——r1/r5 只在 `process_log` 下不适用(过程记录类文档不需要状态声明/证据佐证)，r2/r3/r4 在 `process_log` 和 `nondelivery` 下都不适用(只有真正的代码交付才需要提交哈希/分支说明/文件路径)。这与人工读一遍数据得出的理解完全吻合。

---

## 5. 需要人工审的 3 个可疑点

**①（最可疑）r6 的"N/A vs 条件适用"检测报告了 unresolved，但这不是缺陷，是正确的诚实报告。** — 归纳器如实说明"r6 没有找到与 N/A 完美相关的分型字段"，因为 r6 的 N/A/AMBIGUOUS 由它自己的 `trigger`(是否命中打回类小节标题)决定，跟 `doc_type` 无关，这是两种不同性质的"不适用"条件混在一个判决点键集合里。算法没有崩，只是提醒使用者"这个点的适用条件不是分型门控"，需要人工确认这个理解正确、不需要额外处理。

**②（次可疑）e2/r2/r4 的机械判决门槛完全没有被数据统计验证过，是结构位置+常识类推出来的。** — 12 轮样本里 `cr_count` 恒为 0、`sha40_count` 恒 ≥1、`file_path_count` 恒 ≥1，从未出现过会导向 FAIL 的取值。归纳器只能确认"这三个点存在、且决策来源是这三个 grep 步骤"，但无法从数据里验证真实的判决公式(例如到底是 `count>0` 还是其它门槛，是否有上限)。这三条规则目前完全基于字段名的常识含义(类推自旧版本 19 步设计里同名判决点的语义)，**必须人工核实 check.py 源码**才能确认。

**③（数据漂移本身）任务书原文描述的 UNITFIX 案例已经被上游数据变更推翻，图②的 llm 兜底样本换成了 REVASSOC。** — 见第 0 节。这不是归纳算法的问题，而是外部数据在任务执行期间发生了变化。需要人工确认：(a) 这次数据重写是否是预期内的迭代(比如有人在同步改进 check.py 判决脚本并重新生成了轨迹)；(b) 图②里 `n9_llm` 引用的 `ambiguous_examples`(REVASSOC, round6) 是否就是当前应该作为归纳依据的正确样本，而不是任务书原文里提到的 UNITFIX。

附带一个次要的技术瑕疵：`n5` 的 `output_contract` 是 `regex:^sha\d+_count=\d+$`，其中字段名本身固定的 `40`(即 `sha40_count`)被 `infer_contract` 的数字通用化逻辑误当成了可变部分，导致契约比实际应有的宽松(会放行 `sha99_count=` 这种不存在的字段名)。这是 `infer_contract` 通用算法的固有局限(它不知道数字出现在 key 里还是 value 里)，不是本次改动引入的新 bug，但值得记录。

---

## 6. 唯一验证命令原始输出

```
cd ${TRAJ_HOME} && python3 inducer/induce.py "${TRAJ_DATA_DIR}/changeset-audit" /tmp/regress-audit.json && python3 -c "import json;g=json.load(open('/tmp/regress-audit.json'));from collections import Counter;print('回归 changeset-audit:',len(g['nodes']),Counter(n['type'] for n in g['nodes']))" && python3 inducer/induce.py "${TRAJ_DATA_DIR}/receipt-compliance" graphs/receipt-compliance.v1.json && python3 -c "import json;g=json.load(open('graphs/receipt-compliance.v1.json'));from collections import Counter;print('图②:',len(g['nodes']),Counter(n['type'] for n in g['nodes']),'unresolved:',sum(1 for n in g['nodes'] for b in n.get('bindings',{}).values() if b.get('from')=='UNRESOLVED'))"
```

原始输出：

```
轮次数: 10
节点总数: 15  类型分布: {'tool': 8, 'branch': 5, 'map': 1, 'llm': 1}
每个 seq 的稳定性 (遮蔽后模板一致 n/N):
  seq 1 kind=sql      stability=10/10 vars=['changeset_no']
  seq 2 kind=compare  stability=10/10 vars=['sha']
  seq 3 kind=git      stability=10/10 vars=['sha']
  seq 4 kind=sql      stability=10/10 vars=['changeset_id']
  seq 5 kind=git      stability=10/10 vars=['sha']
  seq 6 kind=compare  stability=10/10 vars=['db_changeset_file_count', 'db_change_file_count', 'git_count']
  seq 7 kind=sql      stability=10/10 vars=['changeset_id']
  seq 8 kind=git      stability=10/10 vars=['sha']
  seq 9 kind=compare  stability=10/10 vars=['changeset_no', 'db_files', 'git_files']
  seq10 kind=sql      stability=10/10 vars=['changeset_no']
  seq11 kind=compare  stability=10/10 vars=['title']
  seq12 kind=sql      stability=10/10 vars=['changeset_id']
  seq13 kind=git      stability=10/10 vars=['sha']
  seq14 kind=compare  stability=10/10 vars=[]
verdict_rules:
  p1: type=mechanical decision_node=n2 stability=10/10
  p2: type=mechanical decision_node=n6 stability=10/10
  p3: type=llm decision_node=n9 stability=10/10
  p4: type=mechanical decision_node=n11 stability=10/10
  p5: type=mechanical decision_node=n14 stability=10/10
UNRESOLVED 绑定总数: 0
图已写入: /tmp/regress-audit.json
回归 changeset-audit: 15 Counter({'tool': 8, 'branch': 5, 'map': 1, 'llm': 1})
流程: receipt-compliance
轮次数: 12
节点总数: 10  类型分布: {'branch': 6, 'tool': 3, 'llm': 1}
输入变量: ['receipt', 'lines']
判决点键: ['e1', 'e2', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6']
分型字段: ['doc_type']
verdict_rules:
  e1: type=mechanical decision_node=n2 stability=12/12
  e2: type=mechanical decision_node=n3 stability=12/12 conditional=e2 没有专属判决步骤，决策来源由结构位置(相邻孤儿工具步骤)补齐推断，具体判决门槛未经数据统计验证
  r1: type=mechanical decision_node=n4 stability=12/12 conditional={'doc_type': ['code_delivery', 'nondelivery']}
  r2: type=mechanical decision_node=n5 stability=12/12 conditional={'doc_type': ['code_delivery']}
  r3: type=mechanical decision_node=n6 stability=12/12 conditional={'doc_type': ['code_delivery']}
  r4: type=mechanical decision_node=n7 stability=12/12 conditional={'doc_type': ['code_delivery']}
  r5: type=mechanical decision_node=n8 stability=12/12 conditional={'doc_type': ['code_delivery', 'nondelivery']}
  r6: type=llm decision_node=n9 stability=12/12
UNRESOLVED 绑定总数: 0
图已写入: graphs/receipt-compliance.v1.json
图②: 10 Counter({'branch': 6, 'tool': 3, 'llm': 1}) unresolved: 0
```

回归行(第一行 `回归 changeset-audit:`)显示 **15 节点，tool8/branch5/map1/llm1**，与期望完全一致；额外做了逐字节 diff 对比通用化前的基线输出，结果为空(逐字节相同)。

图②行(`图②:`)显示 **10 节点，branch6/tool3/llm1，unresolved=0**。
