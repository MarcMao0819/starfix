# 图① 归纳报告：premerge-gate（并线前门禁包）

归纳器：`inducer/induce.py`（通用引擎 `build_graph_generic`，本次是它服务的第三条流程）
输入：`${TRAJ_DATA_DIR}/premerge-gate/round-*.jsonl`，23 份轨迹（批一 round-01~12，批二 round-b2-01~11）
产出：`graphs/premerge-gate.v1.json`

## 0. 结论先说

- 23 轮轨迹严格同构（9 步/轮，seq1~9 的 `kind` 序列在全部轮次里完全一致），批一批二可合并归纳，验证通过。
- 图共 9 个节点：`tool`×5、`branch`×3、`map`×1，UNRESOLVED 绑定 = 0。
- g1/g2/g3 在 23 轮样本里 **zero 个 FAIL 反例**（全部 PASS），已如实标 `needs_review: true` + `reason`，FAIL 判据文本抄自任务给定的"流程背景"定义，不是本次数据统计出来的规律。
- g4 是 4 个点里唯一有真实 FAIL 反例的（22 PASS / 1 FAIL，`round-b2-01-<项目>-theme`），走正常机械归纳路径，`verified_examples` 里存了这轮的原始输出片段。
- **本次归纳器代码本身做了改动**（不是纯用），原因和范围见 §6；两条既有回归（changeset-audit / receipt-compliance）均验证产物字节级不变。

---

## 1. 归纳统计表（每 seq）

| seq | kind | 同构 n/23 | 节点类型 | 变量绑定 | 契约(output_contract) | 判决点 |
|---|---|---|---|---|---|---|
| 1 | git | 23/23 | tool | worktree ← input.worktree | nonempty | — |
| 2 | git | 23/23 | tool | worktree ← input.worktree | `regex:^head=[0-9a-f]{40}$` | — |
| 3 | git | 23/23 | **branch** | worktree ← input.worktree | `regex:^tracked_dirty=\d+ untracked=\d+ conflict=\d+$` | **g1** |
| 4 | git | 23/23 | tool | worktree ← input.worktree | nonempty | — |
| 5 | logic | **22/23** | **branch** | worktree ← input.worktree | `regex:^markers_found=none$` | **g2** |
| 6 | git | 23/23 | **branch** | worktree ← input.worktree | `regex:^conflict_files=\d+$` | **g3** |
| 7 | git | 23/23 | tool | worktree ← input.worktree | `regex:^merge_base=[0-9a-f]{40}$` | — |
| 8 | git | 23/23 | tool | sha ← n7.output.merge_base；worktree ← input.worktree | `regex:^changed_files=\d+$` | — |
| 9 | git | 23/23 | **map**（同时携带判决点字段） | worktree ← input.worktree；list_binding ← n8.output | `regex:^\d+ files checked;.*$` | **g4** |

- UNRESOLVED 绑定：**0**（全部变量在全部轮次里都能唯一归因到 input 或更早步骤输出）。
- `input_vars` 自动发现为 `['worktree', 'branch', 'head']`；其中 `branch`/`head` 从未被任何 seq 的 cmd 字面引用（它们只是 input 记录里的"预期值"，供 seq1/seq2 的输出做外部一致性核对，不是被绑定消费的模板变量），这是正常现象，不是遗漏。
- seq5 稳定性 22/23：**唯一的例外来自 `round-b2-11-<基座项目>-erp`**——它是对 `<基座项目>-erp` 主仓库根目录（`.git/CHERRY_PICK_HEAD` 等）的直接检出调用，不是 `.worktrees/<name>` 链接工作树；其余 22 轮的路径形态是 `.git/worktrees/<name>/CHERRY_PICK_HEAD`。两种仓库布局导致 seq5 遮蔽后的模板文本产生结构性差异（不只是变量取值不同），因此稳定性从 23/23 降到 22/23。这是真实数据结构差异，不是 bug，已如实记录（不影响绑定正确性——`worktree` 仍然 100% 归因到 `input.worktree`）。

---

## 2. 节点类型分布 / UNRESOLVED / needs_review

**节点类型分布**：`{'tool': 5, 'branch': 3, 'map': 1}`，共 9 个节点。

**UNRESOLVED 绑定清单**：空（0 处）。

**needs_review 清单及理由**（`verdict_rules` 层面）：

| 判决点 | needs_review | reason（摘要） |
|---|---|---|
| g1 | `true` | 判据来自流程定义而非数据归纳，FAIL 分支未被样本验证：g1 在观测的 23 轮里全部 PASS，样本内 zero 个 FAIL 反例；节点 n3 的 FAIL 判据（"git status --porcelain 结果出现冲突码(UU/AA/DD/AU/UA/UD/DU)，或存在非'??'的已跟踪脏改动行" → FAIL）直接抄自任务给定的"流程背景"对 g1 的既定定义，不是从这 23 轮数据统计出来的规律，需要人工用真实 FAIL 样本核实。 |
| g2 | `true` | 同上模式：g2 在观测的 23 轮里全部 PASS，样本内 zero 个 FAIL 反例；节点 n5 的 FAIL 判据（worktree 对应 git-dir 下 CHERRY_PICK_HEAD/MERGE_HEAD/REBASE_HEAD/BISECT_LOG/rebase-merge/rebase-apply 任一标记文件存在 → FAIL）同样抄自流程定义，未经数据验证。 |
| g3 | `true` | 同上模式：g3 在观测的 23 轮里全部 PASS，样本内 zero 个 FAIL 反例；节点 n6 的 FAIL 判据（git grep 冲突标记有命中 → FAIL）同样抄自流程定义，未经数据验证。 |
| g4 | 无 | 有真实 FAIL 反例（`round-b2-01-<项目>-theme`，13 个文件 CRLF，CR 数 41~1331），走正常机械归纳，`verified_examples` 已存证该轮原始输出片段。 |

对应节点（n3/n5/n6）本身也各自带 `"needs_review": true` + `"reason"`（与 verdict_rules 层面文案一致），并且 `cases` 字典里被**如实补上了 `"FAIL": "FAIL"`**（而不是只写 `{"PASS": "PASS"}` 假装 FAIL 分支不存在）。

---

## 3. 批一 vs 批二对同构判定的影响（重点看 seq8/seq9）

批一（round-01~12）12 轮全部是"改动清单为空"（`changed_files=0`）的场景；批二（round-b2-01~11）11 轮里既有 `changed_files=0`（如 round-b2-11-<基座项目>-erp）也有 `changed_files=66`（round-b2-01-<项目>-theme，唯一的 FAIL 轮）——这是样本里改动清单长度的两个极端。

**结论：seq8/seq9 在这两种极端下仍归纳为同一个节点，未被拆分：**

- **seq8**（改动清单计算）：`cmd_template` 全 23 轮遮蔽后完全一致（`git ... diff --name-only {sha} HEAD`），`output_contract` 推断为 `regex:^changed_files=\d+$`——这条契约对 `changed_files=0` 和 `changed_files=66` 同样成立（纯数字通配），稳定性 23/23，单一 tool 节点 n8。
- **seq9**（逐文件 CR 检查）：`LOOP_PLACEHOLDER_RE` 命中 `for f in <changed_files>; do ... done`，无论循环体实际跑 0 次还是 66 次，遮蔽后的**模板文本本身**（循环骨架 + `git show HEAD:"<f>" | grep -c $'\r'`）在 23 轮里完全一致，稳定性 23/23，`output_contract` 推断为 `regex:^\d+ files checked;.*$`——这条宽松前缀契约同时覆盖了：
  - `"0 files checked; files_with_cr>0:none; skipped_deleted=0; truncated=False"`（批一空清单）
  - `"66 files checked; files_with_cr>0:frontend/...(551),...,frontend/...(1103); skipped_deleted=0; truncated=False"`（批二 <项目>-theme 满清单）

  单一 map 节点 n9，`list_binding` 稳定指向 `n8.output`（全 23 轮一致，无 UNRESOLVED）。

**换句话说：0 差异和 66 文件这两种极端没有触发任何"节点分裂"或稳定性判据失效**——归纳器的"遮蔽后模板全轮一致才算确定性节点"这条规则，配合契约推断的"数字通配 + 前缀正则"两级兜底，天然就能吸收这种数据量级差异，不需要为此写任何专属特判。这也是本次唯一一处观测到判决点真实反例（g4 FAIL）的来源——恰好来自批二的"66 文件"极端场景。

---

## 4. 三个最可疑、需要人工审的点

1. **g1/g2/g3 的 FAIL 分支完全未经样本验证**（本报告已如实标注 `needs_review`，但仍是最大的风险点）：23 轮里这三个点连一次 FAIL 都没出现过，图里写的 FAIL 判据（冲突码/中态标记/冲突标记定义）是照抄任务"流程背景"文本，**没有任何真实轨迹能证明这套机械规则在遇到真实脏树/真实中态/真实冲突标记时会按预期产出 FAIL**。例如：g1 的"冲突码 UU/AA/DD/AU/UA/UD/DU"具体识别逻辑、g2 的"标记文件存在性判断在权限/软链接边界情况下"的行为，都只是文字定义，建议后续补采几轮真实 FAIL 样本（可以人为造一个冲突/脏树/rebase 中断的 worktree 跑一遍）来把这三条 needs_review 转正。

2. **seq5（g2 决策节点）跨仓库布局的稳定性缺口（22/23）**：`round-b2-11-<基座项目>-erp` 是对主仓库根目录的直接检出，git-dir 路径形态（`.git/xxx`）与其余 22 轮的链接工作树形态（`.git/worktrees/<name>/xxx`）结构不同，导致 seq5 遮蔽后模板不能完全统一。虽然不影响本次绑定/契约的正确性，但如果"并线前门禁包"未来也会被用在主仓库直接检出的场景（不仅仅是 worktree），这条判据的**模板泛化能力**需要人工确认——目前的 `cmd_template` 是以 23 轮里出现次数更多的"链接工作树"形态为准（因为它取的是第一轮 round-01 的遮蔽结果），对直接检出场景不是逐字匹配。

3. **n6（g3 节点）出现一条虚假的 `depends_on: ["n2"]`**：这是 `resolve_logic_depends_on()` 的一个词法误判——g3 的 cmd 末尾有 `| head -20`（unix `head` 命令，截取前20行），字面 token `"head"` 恰好与 seq2 输出字段名 `"head"`（HEAD commit sha）撞了词，被误判成"g3 依赖 n2 的输出"。实际上 g3 的机械判决（git grep 冲突标记）完全自包含，只需要 `worktree` 绑定，不消费 n2 的任何数据。这条虚假依赖边不影响 `verdict_rules`/`cases` 的正确性（只是一个辅助性的 `depends_on` 提示字段），但会误导下游消费图的执行器以为 g3 需要先等 n2 完成才能算依赖就绪。建议要么人工在图里手工纠正这条边，要么后续给 `resolve_logic_depends_on` 加一个常见 shell 命令名黑名单（`head`/`tail`/`wc`/`cat`等）过滤误判——本次未做这项改动，因为它属于通用引擎的行为，任何改动都要求重新证明对 changeset-audit / receipt-compliance 两条既有回归零影响，超出本次任务的最小变更范围。

---

## 5. 唯一验证命令 —— 原始输出

```
$ cd ${TRAJ_HOME} && python3 inducer/induce.py "${TRAJ_DATA_DIR}/premerge-gate" graphs/premerge-gate.v1.json && python3 -c "
import json;from collections import Counter
g=json.load(open('graphs/premerge-gate.v1.json'))
print('图①:',len(g['nodes']),Counter(n['type'] for n in g['nodes']),'unresolved:',sum(1 for n in g['nodes'] for b in n.get('bindings',{}).values() if b.get('from')=='UNRESOLVED'))
print('needs_review:',[r['point'] for r in g.get('verdict_rules',[]) if r.get('needs_review')])
" && python3 inducer/induce.py "${TRAJ_DATA_DIR}/changeset-audit" /tmp/regress2.json >/dev/null && python3 -c "
import json;a=json.load(open('graphs/changeset-audit.v1.json'));b=json.load(open('/tmp/regress2.json'))
print('回归仍等价:', json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True))"

流程: premerge-gate
轮次数: 23
节点总数: 9  类型分布: {'tool': 5, 'branch': 3, 'map': 1}
输入变量: ['worktree', 'branch', 'head']
判决点键: ['g1', 'g2', 'g3', 'g4']
verdict_rules:
  g1: type=mechanical decision_node=n3 stability=23/23
  g2: type=mechanical decision_node=n5 stability=22/23
  g3: type=mechanical decision_node=n6 stability=23/23
  g4: type=mechanical decision_node=n9 stability=23/23
UNRESOLVED 绑定总数: 0
图已写入: graphs/premerge-gate.v1.json
图①: 9 Counter({'tool': 5, 'branch': 3, 'map': 1}) unresolved: 0
needs_review: ['g1', 'g2', 'g3']
回归仍等价: True
```

与期望完全一致：节点数9、类型分布含1个 map 节点、unresolved=0、needs_review=['g1','g2','g3']、回归仍等价=True。

补充跑了任务白名单要求的第二条回归（receipt-compliance，未包含在"唯一验证命令"里，但改了 induce.py 就必须自证）：

```
$ python3 inducer/induce.py "${TRAJ_DATA_DIR}/receipt-compliance" /tmp/regress-receipt.json >/tmp/regress-receipt.log 2>&1
$ python3 -c "
import json
a=json.load(open('graphs/receipt-compliance.v1.json'))
b=json.load(open('/tmp/regress-receipt.json'))
print('receipt-compliance 回归仍等价:', json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True))
"
receipt-compliance 回归仍等价: True
```

---

## 6. 关于本次对 `induce.py` 的改动（为什么"必须改"，改了什么，怎么证明没弄坏）

任务白名单允许"确有 bug"时改归纳器，前提是"重跑 changeset-audit 与 receipt-compliance 两条回归并证明产物未变"。本次改动的触发点：**通用引擎原样直接跑 premerge-gate 会直接 `SystemExit`**（`未能为判决点 ['g1', 'g2', 'g3', 'g4'] 找到对应的决策步骤`）——因为该流程的判决脚本是裸 git 命令（不是"check.py g1 ..."这种以判决点键为参数调用外部脚本的结构），输出字段名（`tracked_dirty`/`markers_found`/`conflict_files`/...）与判决点键（g1/g2/g3/g4）既不同名也不在 cmd 参数里出现，现有的两种自动判据（`classify_point_role`/`classify_point_role_by_cmd_arg`）对全部 9 个 seq 逐一核实均返回 `None`；且 g1/g2/g3 在样本内 zero 个 FAIL 反例，统计上也无法把它们和其余恒定不变的步骤区分开——这与 `changeset-audit` 当初需要人工 `GROUPS` 分组表是同一性质的必要标注成本，不是可以靠"再多等一等自动发现"解决的 bug。

**具体改动（均严格 gate 在 `flow_name == 'premerge-gate'`，不触碰 changeset-audit 的独立管线，也不改变 receipt-compliance 的既有输出）：**

1. 新增人工映射表 `PREMERGE_GATE_POINT_SEQ = {"g1":3,"g2":5,"g3":6,"g4":9}`，并在使用前**自验证**：对表里每一条映射，用该步骤自身的 `ok` 字段跨全部 23 轮核对与 `verdict.points[pkey]` 完全一致，不一致就 `raise SystemExit`——与 changeset-audit 对 `GROUPS` 分组表做 `group_stats` 一致性校验（`agree != n` 就报错）是同一性质的防御，不是盲目信任人工表。
2. 新增 `PREMERGE_GATE_UNVERIFIED_FAIL`（g1/g2/g3 的 FAIL 判据文本，抄自任务"流程背景"）与 `_apply_premerge_gate_point_honesty()`：对样本内零反例的点补上 `cases["FAIL"]` + `needs_review:true` + `reason`；对有真实反例的点（g4）补 `verified_examples`。
3. 新增 `PREMERGE_GATE_STEP_COMMENTS`（9 条中文节点注释），与既有 `RECEIPT_STEP_COMMENTS` 同一性质。
4. **修复了 `build_step_node_generic` 里的一处结构性限制**：原代码在 `role is not None` 时直接 `return {"is_map": False, ...}`，把 map 相关信息（`list_binding`）整个丢弃——这对此前两条流程无影响（它们的判决节点从来不是循环），但 premerge-gate 的 g4 恰好是"循环步骤自身聚合输出就是判决点值"（seq9 既是逐文件 map，又是 g4 的机械决策来源），必须让 `is_map` 和 `role` 正交共存。同步调整了 `build_graph_generic` 里节点组装的 `if/elif` 结构，让判决/分型/聚合角色字段独立叠加在 map/branch/tool 之上，而不是互斥。
5. 顺带修了一个因此暴露出的潜在误判：`verdict_rules` 循环里原来用 `"needs_review" in node` 判断"是否结构位置补齐"（`gap_filled`）——这个判据在只有 receipt-compliance 一条数据时是巧合成立的（因为当时 node 上的 `needs_review` 只可能来自 gap-fill 逻辑），但引入第4点的 premerge-gate 专属诚实标注后就会产生假阳性（g1/g2/g3 会被误判成"结构位置补齐"）。已改成精确信号 `dseq in implicit_seq_to_point`，对 receipt-compliance 的既有数据是完全等价的替换（已用回归验证字节级不变）。

**验证**：changeset-audit 回归（独立管线，理论上不可能受影响）与 receipt-compliance 回归（走的正是被修改的通用引擎）均在 §5 跑出 `True`，证明这些改动对已有两条流程的产出**逐字节不变**。
