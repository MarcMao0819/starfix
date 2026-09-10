# <项目>发布管道规格 · v0.1（第五条轨道一期）

> 定位：本规格 + 配套的 `make_worksheet.py` 是「第五条轨道」的一期交付——**纯只读、零风险**。
> 台账 617 行零个代码块，记的是决策与结果，不是逐步命令+输出的结构化轨迹；可解析轨迹不足 10 条，不满足建图前置闸。
> 所以本期只产出「人可读规格」+「操作单生成器」：生成器只读候选、跑准入检查、吐出一份带真实命令的操作单，**人照单执行、回填结果**；不建图、不执行任何写操作。
> 二期：影子跑积累真轨迹。三期：轨迹达标后才编译成图（接倒数机检两条线：premerge-gate 系列 / changeset-audit 系列）。
> **<日期> 域主对勘已接上二期衔接件**：《实际结果》回填表格改为四段式键值串（候选→逐字命令→原始输出关键行→回读验证证据，见 §F），新增只读转换器 `worksheet_to_trace.py` 把已回填的操作单直接转成符合 TRAJECTORY-CONTRACT.md 约定1的轨迹 jsonl——一期出的单，回填完不再是纯文本记录，本身就是二期轨迹的原始素材。
> **<日期> 影子首单（shadow-01-SORETURN）暴露两个缺陷已修**：①B4 取 PID 命令 `lsof -ti :port` 缺 LISTEN 过滤，把客户端连接 PID 也混进来导致误杀失效，构成运行中 JVM 脚下换 jar 的 5098 雷（见 §B4 修正来源）；②回填表原来只有 `verdict=` 一个出口，人分歧说明无处安放被迫塞进 verdict 值，转换器因此拒收——回填表新增第 5 列「分歧/备注」承接分歧说明，`verdict` 仍只填四选一（见 §F）。

## 来源与口径优先级

1. `${FLEET_HOME}/.claude/skills/<项目>-rebuild/SKILL.md`（**当前项目坐标权威口径**，与下列旧文档冲突时以它为准）
2. `${FLEET_HOME}/<项目>ERP/迁移备份/HANDOFF-20260807-主窗口交接.md`（当前集成线关键坐标/凭据）
3. `${FLEET_HOME}/<项目>ERP/fable 5/决策台账-20260802-Fable5代决.md`（617 行，事故与裁定的原始记录）
4. `${FLEET_HOME}/<项目>ERP/HANDOFF-Fable5-20260802-v2.md`（<日期> 老<项目>交接，**多数命令已随 <日期> 老<项目>退役失效**，见文末附录）

当前集成线坐标速查（权威=SKILL.md + HANDOFF-20260807）：

| 项 | 值 |
|---|---|
| 代码根 | `${FLEET_INTEGRATION_REPO}`，集成分支 `feat/<项目>-integration`，worktree `${FLEET_INTEGRATION_REPO}` |
| 后端 | ${PORT_APP}（宿主机 `nohup java -jar`，非 launchd） |
| 前端 | ${PORT_WEB}（vite dev 直吃集成线源码，HMR 即生效；带 `/api` 前缀走代理） |
| DB | `${DB_NAME}`，容器 `${DB_CONTAINER}`，`127.0.0.1:${PORT_DB}`，root 口令读容器内 `$MYSQL_ROOT_PASSWORD`，不出容器 |
| 回执目录 | `${FLEET_HOME}/<项目>ERP/迁移备份/回执/CS-YYYYMMDD-*.md` |
| 机检 | 并线门禁 `premerge-gate.v6`（图 `${TRAJ_HOME}/graphs/premerge-gate.v6.json`）、轮询器 `changeset-audit.v5`（PID 98325，**禁碰**） |

---

## A0. 离线登记模式（`--registry-json`，仅用于验证）

**动机**：A1/A2 两项准入判据要查 `${DB_NAME}`（`t_code_changeset` / `t_code_change_file`）；但这是一个多会话共享的生产性库，**造病态/干净 fixture 绝不能往里写记录**（会污染共享数据）。为了能在不碰这张共享库的前提下，把 A1-A5 五项判据的报警方向与不误报方向都在受控样本上验一遍（<日期> fixture 验证补课），给 `make_worksheet.py` 加了这个离线登记模式。

**用法**：`--registry-json <文件路径>`。

- **提供该参数**：A1（`get_changeset`）与 A2（`get_change_files_from_db`）需要的登记数据改从这份 JSON 读，`run_sql_readonly` 全程不被调用，本次运行完全不连 `${DB_NAME}`。
- **不提供该参数**（默认，生产发单场景一律走这条）：行为与本参数加入前逐字一致，照旧查库。生产出单唯一走查库路径，**这个开关只用于验证**，不作为发单常规选项。

**价值不止于造样本**：它让 A1/A2 的判据逻辑可以完全脱离数据库单独测试，不用真造一条 changeset 记录、不用等库里有真实数据才能验某个分支。A3/A4/A5 三项判据本来就是纯 git 只读操作，不查库，不受这个开关影响。

**JSON 结构**（字段与 `get_changeset` 查库分支返回的字典、`get_change_files_from_db` 返回的清单逐一对应）：

```json
{
  "changeset": {
    "id": "9001",
    "changeset_no": "CS-FIXTURE-CLEAN",
    "title": "fixture：干净候选",
    "change_type": "feature",
    "commit_hash": "<候选分支真实 HEAD 的 40 位 sha>",
    "file_count": "1",
    "status": "1",
    "operator_name": "fixture",
    "reviewer_name": "",
    "remark": ""
  },
  "change_files": [
    "docs/notes.md"
  ],
  "receipt_dir": "receipts"
}
```

- `changeset`（必需，对象）：字段名与 `t_code_changeset` 查询返回的列一一对应（`SELECT id,changeset_no,title,change_type,commit_hash,file_count,status,operator_name,reviewer_name,remark`）。`get_changeset(changeset_no, registry)` 只在 `entry.changeset_no` 与命令行 `--changeset` 逐字相等时才返回该行，否则返回 `None`（与查库分支"查不到就 None"语义一致）。
- `change_files`（必需，数组）：对应 `t_code_change_file.file_path` 列的清单，供 A2 的文件清单交叉核使用。
- `receipt_dir`（**可选，非查库字段**）：覆盖 A1 回执查找目录（默认 `RECEIPT_DIR` 即 `迁移备份/回执/`）。相对路径按这份 JSON 文件自身所在目录解析成绝对路径。这个字段存在的唯一原因是回执查找本身是文件系统读取、不是查库——离线模式如果不连带覆盖这个目录，A1 在 fixture 上永远只能验出 FAIL（回执目录找不到 fixture 的 changeset_no），验不出 PASS 分支；给这个可选覆盖，fixture 才能在自己目录下放一份回执样本、验出 A1 PASS，而不必污染共享回执目录或共享库。缺省（不设置）时 A1 仍读真实 `RECEIPT_DIR`，与查库路径的默认行为一致。

**用途边界**：本开关只服务于 `release-gate/testfixtures/` 下的受控验证，不接受任何生产发单场景传入；`make_worksheet.py` 不会自动判断"是不是生产"，纪律靠人工遵守——正式发单命令永远不带 `--registry-json`。

---

## A. 准入检查（全部只读，任一不过即拒绝出单）

准入是五项独立检查，逐项给 PASS/FAIL/NEEDS_HUMAN；**只要有一项 FAIL，生成器拒绝出操作单**（只输出准入报告本身）；**只要 A5 触发 NEEDS_HUMAN，直接转人工兜，不判其余项也不出自动单**（迁移优先于其它一切判断，因为迁移必须先于重启这条硬约束本身就要求人工在场协调）。

### A1 · changeset 已登记且校验官判词为 PASS

- **登记**：`t_code_changeset`（库 `${DB_NAME}`）按 `changeset_no` 能查到行，且 `commit_hash` 非空。
- **校验官判词 ≠ DB 字段**：实测 `t_code_changeset.reviewer_name` 全表 0 行非空（<日期> 查证），校验官判词**不落在这张表**，落在回执目录的 Markdown 文件里（`迁移备份/回执/CS-YYYYMMDD-*.md`）。生成器按 `changeset_no` 全文匹配回执目录找到对应文件。

- **判词来源分三级，结构化优先，启发式兜底，两级都判不出则保守拒绝**（<日期> 域主对勘裁定，取代此前"只有关键词启发式"的单一判法）：

  **① 首选：结构化判词行**——主窗口在校验官判词落地时，会在对应回执**追加**一行机器可读记录：
  ```
  verified_by=校验官 / verdict=PASS|打回 / anchor=<40位hash> / ts=<时间>
  ```
  `anchor` 建议写 40 位全 sha（更精确、无歧义）；判据同时容忍 ≥7 位短 sha（判词是人写的，习惯手写短 sha），但短 sha 不接受纯字符串前缀比较——生成器会用 `git rev-parse --verify <短sha>^{commit}` 在候选 worktree 里验证它能唯一解析成登记 `commit_hash` 指向的那个提交，解析失败（歧义或对象不存在）或解析结果与登记不符仍判 **FAIL**（<日期> CS-<日期>-0036 对勘裁定：`anchor=6a99450ee` 这类 9 位短 sha 曾被字面比较误判成"锚点不一致"，拦住合规候选）。
  只要回执里存在这一行（按"追加"语义取回执中**最后一条**同时含 `verified_by=` 与 `verdict=` 的行，避免正文里说明格式的引用文字被误当成真判词），**直接采信，不再退化到关键词启发式**：
  - `verdict=打回` → A1 **FAIL**（校验官已打回，判定优先于锚点核对）。
  - `anchor` 与登记 `commit_hash` **既不逐字相等、也不是能经 git 唯一解析验证一致的短 sha** → A1 **FAIL**，明确报「判词锚点与登记锚点不一致」——这正是 <日期>「RAMODEL 验收后又长三笔新提交」那类事故的第二道防线：判词签的是某个提交，登记/并线用的却是另一个提交，不能因为都叫同一个 changeset 号就当成一回事。
  - `verified_by=校验官` 且 `verdict=PASS` 且 `anchor` 与登记 `commit_hash` **一致**（逐字相等，或短 sha 经 git 验证唯一解析成同一提交）→ A1 **PASS**。
  - 结构化行存在但字段不完整/无法归入以上三类（例如 `verified_by` 不是"校验官"）→ 保守判 **FAIL**。
  - 实测：`CS-<日期>-SOPERF2.md`（changeset `CS-<日期>-0032`）回执里已经有一条真实的结构化判词行 `verified_by=校验官 / verdict=PASS / anchor=be64711d14f0f5540cac7d091d69a656b93aab58 / ts=<日期>T14:4x`，本期生成器对此按新规直接采信判 **PASS**（`confidence=high`），不再走下面的启发式。

  **② 兜底：关键词启发式**——回执里**没有**上述结构化行时，才退回沿用原有的关键词启发式（见下段「必须区分承建方自报 vs 校验官判词」）。启发式给出的结论**必须标注 `confidence=low`**，操作单里要在 A1 结论下方显著提示「该结论来自文本启发式，建议补 `verified_by=` 行」，提醒人工尽快让校验官把判词写成结构化行，逐步淘汰启发式判据。

  **③ 两级都判不出**（无结构化行，启发式也解析不出明确的正/负向表述）→ **UNKNOWN → A1 FAIL**（保守，不误放行，宁可错杀不可错放）。

- **必须区分承建方自报 vs 校验官判词**（这条只在②兜底的启发式层面适用——结构化行本身就是校验官判词，不存在"谁说的"这个问题）：项目自己的铁律写得很清楚——「不接受自报成功。子代理说 PASS 一律当待核实的声明」（HANDOFF-Fable5-v2 §3.3 第 1 条）。回执里出现 `PASS（完工交付，**待校验官验收**；合并归主窗口）` 这类文本，是承建方自报，不是校验官判词，**必须判 FAIL**，不能因为文本里出现了字面的「PASS」就放行。
  - 实测反例：`CS-<日期>-SOOWN.md`（changeset `CS-<日期>-0020`）状态行写的正是「PASS（完工交付，**待校验官验收**；合并归主窗口）」，且回执里**没有**结构化判词行——本期生成器走②兜底启发式，判 **FAIL**、`confidence=low`，理由「仅承建方自报，回执未见校验官独立判词」。这不是误判，是这条检查存在的意义。
  - 判为校验官已放行的标准：回执文本里出现「校验官」+ 放行类动词（放行/PASS/通过/批准）且**不与**「待校验官验收/待复核/待验收」等待决措辞同段出现；或明确写「校验官…FAIL/打回」则判 FAIL。
- **PASS 判据**：DB 有登记 **且**（结构化判词行判 PASS，**或** 无结构化行时兜底启发式判出正向）。
- **FAIL 判据**：DB 无登记；或有登记但回执缺失/未找到；或结构化行判 FAIL/锚点不一致；或兜底启发式判定为承建方自报/校验官打回/UNKNOWN。

### A2 · 并线锚点只认登记的 commit_hash（含文件清单交叉核）

**教训来源**：<日期> 11:0x「b1 回归真相反转」——RAMODEL 分支验收并线后又长出三笔新提交（`669b8f522` 等），主窗口当时并的是登记头 `24e1c8d92`，导致「验过的」和「并的」不是同一个东西，弹窗功能因此回归性丢失，靠事后单摘 `669b8f522` 补救。项目自此立新规：「G1/D5 交付后若再追加提交须重新报头，否则以 changeset 登记 hash 为并线终点」。

- 检查①：`git --no-optional-locks -C <worktree> rev-parse HEAD` 必须**逐字等于** `t_code_changeset.commit_hash`。不等 → FAIL（候选在验收后又被追加了提交，登记已过时）。
- 检查②（changeset 锚点口径，<日期> 校验官裁定）：`git diff <merge_base> <commit_hash> --name-only` 算出的改动文件集合，必须与 `t_code_change_file` 里该 changeset 的明细文件集合**逐字对上**（互为子集也不行，必须集合相等）。不等 → FAIL，附双方差集（登记多出的/实际多出的文件各列出来），理由「changeset 明细与实际改动不一致，登记已失去追溯价值」。
  - 背景：<日期> 机检连抓三单「幻行」——D5 的 0008/0011/0012 把从未提交的 `frontend/components.d.ts` 登进了明细，纯靠 DB 记录会被这类幻行污染；校验官已建议「登记明细以 `git show --name-only <commit>` 为准」，本条检查就是把这个建议做成机械闸。
- 迁移号冲突的历史教训（**记档但不在本期机械检查，列为已知盲区**）：<日期> V065 撞号事故——BOM 分支与 iso-queue 分支各自独立编号 V065，在各自 worktree 里互相看不见，先合并的占号。本条目前无法从单一候选的只读视角侦测（需要跨候选扫描所有在途分支的迁移号），留给 A5 的人工兜底处理；二期若积累到足够轨迹，可在放 D-栏另开一条「迁移号全局唯一性」检查。

### A3 · 候选 worktree 过 premerge-gate.v6（四查全 PASS）

`premerge-gate.v6`（图 `${TRAJ_HOME}/graphs/premerge-gate.v6.json`）四个判决点，本生成器逐一复算（只读 git 命令，与图里的 `cmd_template` 逐字一致）：

| 判决点 | 检查内容 | 命令 |
|---|---|---|
| g1 树干净 | `git status --porcelain` 里非 `??`（即已跟踪的脏改动）或冲突码（UU/AA/DD/AU/UA/UD/DU）行数 > 0 → FAIL | `git --no-optional-locks -C <worktree> status --porcelain` |
| g2 无中态 | `CHERRY_PICK_HEAD` / `MERGE_HEAD` / `REBASE_HEAD` / `BISECT_LOG` / `rebase-merge` / `rebase-apply` 任一存在于 git-dir → FAIL | 先 `git --no-optional-locks -C <worktree> rev-parse --git-dir` 拿到 git-dir，再逐个 `test -e <git-dir>/<标记文件>` |
| g3 无冲突标记 | 全仓 `grep` 冲突标记行（`^<<<<<<<`/`^>>>>>>>`/`^=======`）有命中 → FAIL | `git --no-optional-locks -C <worktree> grep -l -E "^(<<<<<<<|>>>>>>>|=======)$" -- .` |
| g4 行尾 | 相对 `merge-base(feat/<项目>-integration, HEAD)` 逐改动文件跑行尾判据；FAIL/AMBIGUOUS 任一非全 PASS 都不放行（AMBIGUOUS 本身按 v6 设计需转人判，只读生成器遇到即判 FAIL 走人工路） | `python3 ${TRAJ_HOME}/runner/checks/lineending_check.py <worktree> <merge_base> HEAD` |

四查全 PASS 才算 A3 通过；任一 FAIL（含 g4 的 AMBIGUOUS）→ A3 FAIL，附具体判决点与证据行。

### A4 · 新判据：Java 实体新增 private 字段必须有出口（DDL 或 exist=false）

**教训来源（线上事故 <事故编号>，<日期> 11:0x）**：PTL P4/P5 批次给 `ProductPrice`/`ProcessRouteNew`/`Recipe` 三个实体加了 `processTempLabel` 字段，漏了 `@TableField(exist = false)`，编译绿、`vue-tsc` 绿，MyBatis-Plus 运行时按字段名拼 SQL 引用了不存在的列，第三次重启起报价/工艺路线/配方三页 `5001`。台账原话教训：「①编译+vue-tsc 拦不住此类雷 ②已把『实体新增字段无 DDL 且无 exist=false』作为机检候选判据交 舰员丁」——本条就是把这句话落成的机械检查。

- 扫描范围：`git diff --name-status <merge_base> HEAD` 里路径匹配 `src/main/java/.../entity/*.java` 且状态为 `M`（修改既有实体，不含全新文件——全新表本来就没有「漏 DDL」这回事）的文件。
- 对每个此类文件：在 HEAD 版本内容里，找 diff 新增的（`git diff` 输出中以 `+` 开头、非 `+++`）`private <类型> <字段名>;` 声明行。
- 对每个新增字段，检查其在 HEAD 内容里**紧邻上方**（含注释跳过后 1~2 行内）是否有 `@TableField(exist = false)` 或 `@TableField(exist=false)`（宽松空格）：
  - 有 → 该字段视为非持久化派生字段，OK。
  - 无 → 检查本 changeset 涉及的 `.sql`/`db/**` 变更文件里是否有 `ADD COLUMN` 语句，列名与字段名的 snake_case 形式匹配：
    - 有 → 视为字段有对应 DDL，OK。
    - 无 → **FAIL**，输出「实体 `<文件>` 新增字段 `<字段名>` 既无 `@TableField(exist=false)` 也无对应 DDL——与 <事故编号> 同款」。
- 本批未触及 Java 实体文件 → 本条判 PASS（不适用）。

### A5 · NEEDS_HUMAN 触发：候选含 DB 迁移

- 触发条件：`git diff --name-only <merge_base> HEAD` 里任意路径匹配 `^db/.*\.sql$`（覆盖 `db/migrations|migration|ddl|dml|manual` 等既有子目录，仓库里迁移文件全部平铺在 `db/` 下，如 `db/v163_packaging_stock_in_two_stage.sql`）。
- 命中 → 整单判 **NEEDS_HUMAN**，不出自动操作单，只输出：
  1. 命中的迁移文件清单
  2. 人工必须确认的事项（迁移号全局唯一性——对照 V065 撞号教训主窗口统一发号；迁移是否已在库执行过一次以判断幂等；备份新鲜度 G7）
  3. 提示：迁移必须先于重启（HANDOFF-20260807 §2：「加列后旧 jar 无感、新 jar 缺列即挂」）——一旦人工确认迁移方案，走 B 节但迁移步骤单独插在 package 与 restart 之间，由人工手动执行迁移 SQL 后再继续。
- 未命中 → 不触发，继续走 A1-A4。

---

## B. 执行步骤（逐字命令 + 每步独立回读验证 + 回滚）

**仅当 A1-A4 全 PASS（A5 未触发）时，生成器才渲染本节的操作单**，命令按候选实际路径/sha 渲染好真值。

### B0 · 前置：获取串行锁（人工执行，见 E 节）

### B1 · merge --no-ff

**命令**（在集成线 worktree 内）：
```bash
cd ${FLEET_INTEGRATION_REPO}
git --no-optional-locks fetch . <候选分支>:<候选分支>   # 若候选是独立 worktree 的本地分支，直接引用即可，无需 fetch
git merge --no-ff <commit_hash（=changeset 登记的锚点，非分支名）>
```
> 只认 `commit_hash`：merge 目标写**具体 sha**，不写分支名/`HEAD`——防止候选在你读它和你合并它之间又被追加提交（A2 教训）。

**独立回读验证**（不许只看 merge 命令自身的退出码）：
```bash
git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} log --oneline -3
git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} log --format='%H' -1
```
期望：新 HEAD 是一个 `Merge commit '<commit_hash 前缀>'...` 提交，且 `git merge-base --is-ancestor <commit_hash> HEAD` 退出码 0（锚点确实已在集成线祖先链上——不是"看起来合了"）。

**失败回滚**：merge 冲突或校验不过 → `git merge --abort`（仅在 `MERGE_HEAD` 存在时，即真处在合并中态才执行）；若已提交但回读验证不过 → `git reset --hard <B0前记录的并线前基点>`（见 D 节）。

### B2 · 前端类型检查（候选触及 `frontend/**` 时执行）

**命令**：
```bash
cd ${FLEET_INTEGRATION_REPO}/frontend
npx vue-tsc -b --pretty false
```
**独立回读验证**：命令真实退出码（**单独捕获，不用管道尾**，如 `echo $?`），必须为 0；巨型文件冻结门/菜单三门禁若被 `npm run build` 串联短路，按 SKILL.md 陷阱条款单独复跑对应脚本，不拿 `npm run build` 整体退出码当数（陷阱条款：<日期> 校验官发现，freeze 门一红整条 `&&` 链后面的菜单三门禁等于没跑）。
**失败回滚**：类型错误 → 不重启、不影响运行中实例（前端是 vite 源码直跑，merge 前的运行时不受此步骤影响）；回到 B1 前的并线前基点 `git reset --hard`，把 merge 一并撤销，候选打回。

### B3 · 后端打包（候选触及 `src/main/java/**` 或 `pom.xml` 时执行）

**命令**（JDK17 编译，Lombok 需要临时开 `jdk.compiler`——照 SKILL.md 逐字抄）：
```bash
cd ${FLEET_INTEGRATION_REPO}
JH17=${FLEET_INTEGRATION_REPO}/.toolchain/jdk-17.0.20+8/Contents/Home
OPTS=''
for p in api code comp file main model parser processing tree util jvm; do
  OPTS="$OPTS --add-exports=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED --add-opens=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED"
done
JAVA_HOME="$JH17" MAVEN_OPTS="$OPTS" MAVEN_USER_HOME=${FLEET_INTEGRATION_REPO}/.m2repo \
  ./mvnw -B -Dmaven.repo.local=${FLEET_INTEGRATION_REPO}/.m2repo/repository -DskipTests package
echo "BUILD_EXIT=$?"
```
**独立回读验证**（不许只看管道尾）：
```bash
echo $BUILD_EXIT        # 必须=0，真实退出码单独捕获，不接在任何管道后面判断
ls -la target/${APP_JAR}
shasum -a 256 target/${APP_JAR}
```
期望：`BUILD_EXIT=0`；jar mtime 是本次 package 之后的时间（不是历史遗留 jar）；记下 SHA 供 B4 重启后核对源位=运行位（台账 <日期> 部署先例：「jar SHA 源/运行位一致」）。
**失败回滚**：package 失败不产生新 jar，运行中实例不受影响；回 D 节，`git reset --hard` 到并线前基点，候选打回并附编译错误摘要。

### B4 · 加固重启（候选触及后端时，package 成功后**必须紧接执行**，禁止"先建后等"）

**教训来源（<日期> 09:15，昨夜 5098 事故）**：8-6 18:32 在运行中的 JVM 脚下重建了 target jar，老 JVM 懒加载类失败，几小时后（次日）才在真实请求触发时报 5098——现象滞后到看起来像是无关的新故障。台账定的新规原文：「jar 重建与重启必须连贯执行——禁止『先建后等』，运行中 JVM 脚下换 jar = 数小时后随机 5098」。**本步骤必须紧跟在 B3 成功之后执行，中间不许插入其它候选的 merge/package**（这也是 E 节要一个全局串行锁的直接原因）。

**修正来源（<日期> 影子首单 shadow-01-SORETURN 域主实测，5098 雷的另一半根因）**：本步骤①原写的 `lsof -ti :${PORT_APP}` **未限定 LISTEN**，域主实际执行时该命令把 vite 到 ${PORT_APP} 的**客户端连接 PID** 一并返回，多行值进 `kill` 导致参数全失效——老 JVM 未死仍占端口（探活拿到的 401 是**老进程**答的，构成假绿），新 JVM 撞端口即死，而 B3 已把 jar 换掉，构成运行中 JVM 脚下换 jar 的同款 5098 雷。HANDOFF 原口径本来就要求带 LISTEN 过滤，是生成器抄漏了。修法两条：①取 PID 与验端口空一律改用 `lsof -tnP -iTCP:<port> -sTCP:LISTEN`，若返回多行视为异常，停下报 `RUNNER_ERR` 让人判，不许直接对多行 `kill`；②探活成功后必须再核对持有端口的 PID 就是本步骤起的 `NEW_PID`，不等则判 FAIL（防旧进程假绿）。

**命令**（照 HANDOFF-20260807 §2 逐字，<日期>/06/07 多次实战验证过的加固流程）：
```bash
cd ${FLEET_INTEGRATION_REPO}

# 1. 找旧 PID（必须带 LISTEN 过滤——不过滤会把 vite 到 ${PORT_APP} 的客户端连接 PID 也
#    一并返回，<日期> 影子首单实测教训，见上方修正来源）
OLD_PID=$(lsof -tnP -iTCP:${PORT_APP} -sTCP:LISTEN)
echo "OLD_PID=$OLD_PID"
OLD_PID_N=$(printf '%s\n' "$OLD_PID" | grep -c .)
if [ "$OLD_PID_N" -gt 1 ]; then
  echo "RUNNER_ERR: lsof 返回 $OLD_PID_N 行 PID，异常，停下人判——不许对多行值直接 kill"
  exit 1
fi

# 2. kill 旧进程，循环 30×2s 等死透，第15轮 kill -9（台账明确记过：旧进程有几次需要 kill -9 才死透，不能假设 SIGTERM 会成功）
kill "$OLD_PID" 2>/dev/null
for i in $(seq 1 30); do
  kill -0 "$OLD_PID" 2>/dev/null || { echo "DEAD at round $i"; break; }
  if [ "$i" -eq 15 ]; then kill -9 "$OLD_PID"; fi
  sleep 2
done

# 3. lsof 验端口空（同样带 LISTEN 过滤，理由同①——不过滤会把仍存活的客户端连接
#    误判成"端口未空"）
lsof -tnP -iTCP:${PORT_APP} -sTCP:LISTEN   # 期望：空输出

# 4. worktree 内起（.local-instance 覆盖优先级高于环境变量，两道加固见 SKILL.md §四）
nohup ${FLEET_INTEGRATION_REPO}/.toolchain/jdk-11.0.32+9/Contents/Home/bin/java \
  -jar target/${APP_JAR} \
  --spring.profiles.active=prod,local \
  --spring.config.additional-location=optional:file:${FLEET_INTEGRATION_REPO}/.local-instance/ \
  --server.port=${PORT_APP} \
  --<项目>.medical.enabled=false \
  --candidate-readonly.enabled=false \
  --erp.instance.operating-sites.enabled=false \
  --erp.instance.medical.enabled=false \
  --erp.instance.rbac.seed=<项目> \
  > ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log 2>&1 &
NEW_PID=$!
echo "NEW_PID=$NEW_PID"
```

**独立回读验证**（四件套，逐条落证据，不许只看进程存不存在）：
```bash
# ① 新 PID 探活：循环等监听，探到 200/401 视为装配成功（上限 60 轮，<日期>
#   域主实测补充：30 轮的旧上限在实测中不够用，按实测口径改为 60，避免探活还没
#   收敛就被循环上限打断误判成失败）
for i in $(seq 1 60); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' http://127.0.0.1:${PORT_APP}/api/v1/auth/me)
  [ "$CODE" = "401" -o "$CODE" = "200" ] && { echo "PROBE_OK round=$i code=$CODE"; break; }
  sleep 1
done

# ② ERROR 计数：日志里必须为 0
grep -c ERROR ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log

# ③ 无路由二义（ambiguous mapping）：日志里搜这个关键字必须 0 命中
grep -ci "ambiguous mapping" ${FLEET_INTEGRATION_REPO}/.local-instance/logs/backend-host.log

# ④ jar mtime 晚于进程启动时间（防止起的是旧 jar）
ps -o lstart= -p "$NEW_PID"
stat -f '%Sm' target/${APP_JAR}

# ⑤ 探活成功后核对持有端口的就是本步骤起的新 PID，防旧进程假绿（同款 LISTEN 过滤）
LISTEN_PID=$(lsof -tnP -iTCP:${PORT_APP} -sTCP:LISTEN)
echo "LISTEN_PID=$LISTEN_PID"
[ "$LISTEN_PID" = "$NEW_PID" ] && echo "PID_MATCH_OK" || echo "PID_MATCH_FAIL listen=$LISTEN_PID new=$NEW_PID"
```
期望：①探活命中且轮次记录（台账里历次重启记录的 9、15、8 等轮次，是**启动后探活轮次**——新进程起来后循环等 `curl` 探到 200/401 花了几轮，不是"杀旧进程花了几轮"，两者是这条命令时序里的两个不同计数器，不能混着看）；②`ERROR` 计数=0；③`ambiguous mapping` 命中=0；④jar mtime 早于或约等于 `NEW_PID` 启动时间（说明起的确实是刚打的包，不是残留旧 jar）；⑤`PID_MATCH_OK`，即 `LISTEN_PID` 逐字等于 `NEW_PID`（不等 → 判 FAIL，说明①探到的 200/401 是旧进程答的假绿，新进程根本没能绑上端口）。

**节拍实测注记（域主 <日期> 对勘补充）**：kill 侧是另一套独立计数——今天五次重启里，第一次优雅 kill 在 `round 2` 即 `DEAD`（年轻 JVM 应声而死），**后四次全部在 `round 16` 才 `DEAD`**，也就是第 15 轮 `kill -9` 强杀之后再等一轮才真正死透。所以"16 轮死透"正是 15 轮强杀生效的结果——不是杀早了，也不是杀晚了；第 15 轮升级为 `kill -9` 这条阈值**本次保留不动**。既然优雅 kill 对运行较久的 JVM 从未成功过（5 次里 4 次都扛到强杀才死），升级点**未来可考虑前移到第 5-8 轮**，省下 15-20 秒的等待；但影子模式期间先按现有的第 15 轮观察、积累数据，等轨迹够了再调阈值，本次改动不直接下调这个数字。本地开发实例强杀无在飞事务需要保护，代价可忽略。

**失败回滚**：
- 新进程起不来/探活超时/ERROR>0/`PID_MATCH_FAIL`（含①的 RUNNER_ERR：`OLD_PID` 多行） → 立刻用同样的 kill 流程杀掉新 PID，用**上一个已知良好的 jar 备份**（B3 前应先 `cp target/${APP_JAR} <备份路径>-<旧sha>.jar.bak`，见 D 节）重复本步骤的第 4 步启动旧 jar，验证恢复到位后再处理候选（打回或人工介入）。
- 迁移已执行但重启失败 → 这是最难回滚的情形，NEEDS_HUMAN（迁移通常不可逆），转人工按 D 节的迁移专属回滚处理。

### B5 · 冒烟（走用户同源入口，针对本批触及实体加探针）

**教训来源（同 A4，<事故编号> 教训②）**：「重启后冒烟必须覆盖本批触及实体的页面查询（本次已做，今后入重启规程）」——只探首页/登录不够，报价/工艺路线/配方三页当时就是首页探活全绿但业务页 5001。

**命令模板**（${PORT_WEB} vite 代理链，带 `/api` 前缀，禁止只探直连 ${PORT_APP}——HANDOFF-20260807 §2 强调走同源入口）：
```bash
# 登录（工位登录）
curl -s --noproxy '*' -X POST http://127.0.0.1:${PORT_WEB}/api/auth/station-login \
  -H 'Content-Type: application/json' \
  -d '{"stationName":"<项目>管理","pin":"1234"}'

# 通用页面/接口探针（示例，按本批 touched 的实体替换）
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:${PORT_WEB}/api/<本批涉及的接口路径>

# <事故编号> 同款教训的探针示例（有类似实体改动时照抄）
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:${PORT_WEB}/api/product-price/page
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:${PORT_WEB}/api/process-route/page
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:${PORT_WEB}/api/recipe/page
```
**独立回读验证**：每个探针的 HTTP code 必须是业务正常码（200/401，视鉴权状态），**不是 5001/500**；生成器会为本批 changeset 涉及的每个 entity/controller 反查对应的 `page`/`detail` 类接口路径列进操作单（人工确认路径是否齐全，本期生成器只做启发式匹配，不保证穷尽）。
**失败回滚**：探针 5xx → 立刻按 B4 的回滚流程切回旧 jar；已确认是本批引入的缺陷 → 记 changeset 回执 BLOCKED，候选退回修复。

---

## C. 防御性发布三则（已逐条折进 B 节，此处汇总重申）

1. **每步写后独立回读验证**：B1-B5 每一步都有单独的验证命令，不依赖上一步命令自身的输出判断，也不依赖"看起来跑完了"。
2. **禁止用管道尾的退出码判成败**：B3/B4 的关键判据（`BUILD_EXIT`、探活 code、`ERROR`/`ambiguous mapping` 计数）全部单独捕获成变量或单独跑一条命令，不写 `cmd1 | cmd2 && echo OK` 这种会被中间管道吞掉真实退出码的写法（HANDOFF-20260807 §0 明确点名："改公共方法签名必须 package 级编译（compile 不编译测试）"同类精神——**验证要对应到能证明问题的粒度，不能图省事读一个综合信号**）。
3. **证据行落回执**：每步执行完，人工把回读验证的原始输出（不是转述）贴进本单对应的《实际结果》表格，最终连同 changeset 号一起落 `迁移备份/回执/CS-YYYYMMDD-<单名>.md`（收尾条款：无论 PASS/FAIL/BLOCKED，落盘前不算结束）。**《实际结果》表格自 <日期> 起采用四段式键值串**（候选→逐字命令→原始输出关键行→回读验证证据），见 §F。

---

## D. 回滚

### D-1 · 并线前基点

- **定义**：B1 执行前，`git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} rev-parse HEAD` 的输出，即"这次并线开始前，集成线本来在哪"。
- **记录方式**：操作单会在 B1 之前专列一步要求人工先跑这条命令并把输出**抄进操作单顶部**（生成器渲染时会预留占位，因为这个值只有在真正执行时才能取到，生成器只读阶段拿到的是"当前"HEAD，作为参考基点写入操作单，执行时人工需在动手前再核一次，防止两次读取之间又被别的候选抢先并线——见 E 节串行锁）。
- **恢复方式**：`git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} reset --hard <并线前基点sha>`——仅在确认没有其它候选已经在此基点之后又合入过东西时执行（先 `git log <基点>..HEAD --oneline` 核一遍，混进了别的候选就不能简单 reset，需人工判断）。

### D-2 · 各步失败分别回滚到哪

| 步骤失败 | 回滚目标 |
|---|---|
| B1 merge 冲突/校验不过 | `git merge --abort`（中态存在时）或 `reset --hard` 到 D-1 基点 |
| B2 前端类型检查不过 | `reset --hard` 到 D-1 基点（撤销 merge，候选打回修复） |
| B3 打包失败 | 不产生新 jar，运行时不受影响；`reset --hard` 到 D-1 基点 |
| B4 重启验证不过（未执行迁移） | kill 新进程 → 用 B3 前备份的旧 jar 重跑 B4 启动步骤 → 验证恢复 → 之后再 `reset --hard` 到 D-1 基点打回候选 |
| B4 重启验证不过（已执行迁移） | **NEEDS_HUMAN**——迁移多为加列/建表，通常不能简单回滚；人工按迁移自身的反向 DDL 或从 G7 新鲜备份恢复库，再决定候选去留 |
| B5 冒烟发现业务缺陷 | 同 B4 未执行迁移的情形：切回旧 jar 恢复服务，候选记 BLOCKED 退回修复 |

### D-3 · jar 备份约定

B3 打包前，人工需先把当前运行的 jar 备份一份（`cp target/${APP_JAR} <备份目录>/erp-2.0.0-SNAPSHOT-<旧HEAD短sha>.jar.bak`），供 B4/B5 失败时的快速回滚使用。生成器在操作单里会渲染这一步，但不会代为执行（写操作）。

---

## E. 串行锁

**约束来源**：B4 的"禁止先建后等"教训要求 package 与 restart 之间不能被别的候选插入；且集成线 worktree、${PORT_APP} 端口、共享库 `${DB_NAME}` 都是唯一的，两个候选同时跑 B 节必然互相踩踏。

- **锁文件位置**：`${TRAJ_HOME}/release-gate/.locks/CURRENT.lock`（全局单锁，不按候选分文件——本条款是"同时只跑一个候选"，不是"每候选一把锁"）。
- **内容格式**（人工用一条命令写入，示例）：
  ```bash
  mkdir -p ${TRAJ_HOME}/release-gate/.locks
  cat > ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock <<EOF
  changeset_no=<单号>
  worktree=${FLEET_INTEGRATION_REPO}
  operator=<你的身份标识>
  acquired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  ttl_seconds=1800
  EOF
  ```
- **TTL**：1800 秒（30 分钟）——与本项目其它发布锁的既有惯例一致（B 节全程实测耗时：package 约 54 秒+重启 10 秒量级+人工执行与回读，30 分钟对单个候选足够宽裕又不会长期占死）。
- **僵死锁判定**：`now - acquired_at > ttl_seconds` → 判僵死，可由后来者人工确认候选早已收尾（查回执目录有没有对应 changeset 的终态回执）后 `rm` 该锁文件重新获取。**生成器只读锁状态用于在操作单里提示"当前是否有人在跑"，不会自动获取/释放/删除锁**（写操作，属于人工步骤）。
- **释放**：B 节全部步骤（含冒烟）完成、回执落盘后，人工 `rm ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock`。

---

## F. 二期衔接：回填表 → 轨迹 jsonl（`worksheet_to_trace.py`）

**定位**：一期（本规格 + `make_worksheet.py`）只出操作单、不建图；二期要靠人工照单执行后回填的《实际结果》积累真轨迹。这一节把两者接上——回填表格本身就按轨迹归纳器能吃的形状设计，`worksheet_to_trace.py` 是把已回填的操作单原样转成合规轨迹的唯一只读转换器。

### 回填表格的四段式键值串

《实际结果》表格（B 节末尾，操作单每步一行）不再是自由文本，而是五列固定形状（<日期> 影子首单对勘新增第 5 列「分歧/备注」）：

| 步骤 | 逐字命令（已渲染，勿改） | 原始输出关键行（请按键值串填） | 回读验证证据 | 分歧/备注 |
|---|---|---|---|---|
| B4 加固重启 | `<真实命令>` | `point=restart/old_pid=?/kill_rounds=?/new_pid=?/startup_s=?/error_count=?/ambiguous_mapping=?/jar_sha=?/verdict=?` | `<该步的回读验证方法>` | 本步若与单内预期不符，在此写明分歧详情与补救动作；`verdict` 字段仍只填 `PASS/FAIL/N/A/AMBIGUOUS` 四选一 |

- **候选**坐标（worktree/分支/HEAD/changeset/merge-base）已经在操作单顶部渲染，不重复放进每一行。
- **逐字命令**列由生成器渲染，人工不改；里面的 shell 变量（`$OLD_PID`/`$BUILD_EXIT` 等）是真实脚本的一部分，不是禁止的占位符——禁止的是像 `<真实命令>` 这种从未被替换成实际值的字面占位符。
- **原始输出关键行**列预填字段名+`?` 占位，人工只填值，不许自由发挥；每步必须含 `point=<步骤ID>` 与 `verdict=<PASS|FAIL>` 两个字段（对应 TRAJECTORY-CONTRACT.md 约定1）。B0-B5 各步的 `point` 取值：`lock`/`merge`/`tsc`/`package`/`restart`/`smoke`/`lock_release`。**`verdict` 只能取 `PASS|FAIL|N/A|AMBIGUOUS` 四选一，遇到分歧不许把说明文字塞进这个字段**（<日期> 影子首单实测教训：域主曾把整段分歧说明塞进 verdict 值，导致转换器拒收——转换器拒得对，分歧详情有专门的列）。
- **回读验证证据**列是生成器预填的提示文字（去哪条独立回读命令里取值），不是命令本身。
- **分歧/备注**列（第 5 列，<日期> 新增）：本步执行结果若与单内预期不符（如命令本身有缺陷、需要临场补救），把分歧详情与补救动作写在这一列，不占用 `verdict`。`worksheet_to_trace.py` 会把这一列的文本原样写进该 step 记录的 `divergence` 字段，不参与 `verdict` 解析；本列留空是允许的（无分歧时留空或省略）。

B5（冒烟）逐字命令列现由生成器按 §B5-P 的三级反查自动渲染业务页探针（登录探针始终确定性收录，业务页探针视反查结果而定；反查全部落空时逐字命令列改为显著的失败标注，不是假装是真命令的路径占位符）；实际测了哪些业务页路径、结果如何，仍由 `probe_paths=`/`http_codes=` 两个字段如实记录（人工执行后可能覆盖反查列表之外的路径，如动态路径填了真实 id）。

### `worksheet_to_trace.py`：只读转换器

```bash
python3 worksheet_to_trace.py <已回填的操作单.md> <输出.jsonl>
```

- 只解析传入的操作单 Markdown 文本，不调用 git/SQL/子进程。
- 回填表格是 4 列（旧单）或 5 列（新单，多一列「分歧/备注」）皆可解析，兼容已回填的旧形态操作单，不强制补列。
- 产出 1 条 `input` 行（候选坐标）+ 每步 1 条 `step` 行（`cmd`/`output_digest`/`divergence`，后者仅在「分歧/备注」列非空时才写入该字段）+ 1 条 `verdict` 行（`points` 汇总 + `pass` + `fail_detail`），字段形状与 `inducer/induce.py` 消费的 `round-*.jsonl` 一致。
- 任何一步仍是 `?` 占位、缺 `point=`/`verdict=`、或 `verdict` 取值不在 `PASS|FAIL|N/A|AMBIGUOUS` 内 → **报错并点名具体步骤+字段**，不产出残缺轨迹（呼应 TRAJECTORY-CONTRACT.md 硬约定6"归纳出的图必须真跑过才算数"背后的教训——占位符一旦混进轨迹，下游会产出"看着对、实际跑不了"的假判据）。`verdict` 值本身若像是被塞进了大段分歧说明（不属于四选一枚举）→ 报错并提示「若要说明分歧，请写在『分歧/备注』列，不要写进 verdict」（<日期> 影子首单实测教训的直接镜像）。

### 二期路径

多份已回填操作单 → 各自转成 `round-*.jsonl` → 放进同一目录 → 交给 `inducer/induce.py` 按 TRAJECTORY-CONTRACT.md 约定 1（`point=`/`verdict=` 齐全）自动发现判决点，产出确定性图，无需再为这条新流程手写人工判决点映射表——这正是约定 1 在 `premerge-gate` 上已经验证过的路径（见 `inducer/induction-report-premerge.md`）。

---

## 附录：HANDOFF-Fable5-20260802-v2.md 中不适用于当前<基座项目>线的内容

老<项目>实例已于 **<日期>** 退役删库（决策台账原文：「老的那个可以退役了删库」，终备份 `迁移备份/退役归档-${DB_NAME_LEGACY}-20260805.sql.gz`，`DROP DATABASE ${DB_NAME_LEGACY}`，`launchctl` 卸载相关 plist）。因此 HANDOFF-Fable5-v2 里下列内容**只作历史参照，不能照抄进本规格执行**：

| 旧文档内容 | 为何不适用 |
|---|---|
| 后端 8080 / 前端 8792 | 老 <项目>-erp 实例端口，已随退役停用；现行是 ${PORT_APP} / ${PORT_WEB}（新集成线） |
| DB `${DB_NAME_LEGACY}`，容器 `<项目>-m2-mysql-a65`，端口 23316 | 库已 `DROP DATABASE`，容器已停；现行是 `${DB_NAME}` / `${DB_CONTAINER}` / ${PORT_DB} |
| `export JAVA_HOME=$PWD/.tools/jdk-21.0.11+10` + `mvn -o clean package -pl backend/app -am` | 老仓库是 `backend/app` 子模块布局、纯 JDK21；现行 `<基座项目>-erp` 是单模块根 `pom.xml`（`src/main/java` 直接在根），且编译需 JDK17（Lombok 需临时开 `jdk.compiler`）、运行需 JDK11（JDK17 下 MyBatis 3.5.4 的 OGNL 对 `LambdaQueryWrapper` 查询会空指针）——这条编译/运行分离约束是老文档写成时还没踩出来的坑，照抄会直接线上炸 |
| `launchctl kickstart -k gui/501/com.<项目>.erp.local.runtime` + `sed` 改 `.cache/runtime/start-<项目>-erp-v046.sh` 里的 jar 名 | 老实例用 launchd 管理进程；现行是手工 `nohup java -jar` + 显式 kill/等死透/重启的加固流程（本规格 B4），没有 plist、没有 `launchctl` |
| `docker exec -i <项目>-m2-mysql-a65 sh -c 'mysql ... ${DB_NAME_LEGACY}' < <迁移文件>` | 容器名、库名都对应已退役的老实例；现行迁移走 `${DB_CONTAINER}` 容器内 `${DB_NAME}` 库 |
| 18081 隔离冒烟端口 | 老实例的隔离端口约定；现行端口分配纪律（<日期> 立）是后端 18095+、前端代理 15180+，${PORT_WEB}/${PORT_APP} 是 Owner 现场常驻实例，隔离验证不得占用 |
| 用钥匙串 `<项目>-erp-jwt-secret` 为 Owner 账号签 15 分钟短效 token（决策台账 <日期>「建仓」节） | 这是老 <项目>-erp 实例在 Owner 工位密码 401 失效时的一次性应急手段，绑定老实例自己的 JWT 密钥；现行集成线用工位登录 `<项目>管理/1234` 测试账号（SKILL.md），不需要、也不应该在只读发布管道里复刻"代签身份 token"这种触碰认证密钥的操作 |
| §三编队名册（T2/舰员己/舰员戊/舰员庚 等 iTerm2 会话 ID） | 纯组织调度信息，非管道命令，但同样是 <日期> 那批会话的快照，早已被 <日期> 的舰队通道表（HANDOFF-20260807 §3）取代，按新表联系 |

保留仍然有效的部分（v2 文档自己也说明"上午版仅『五、常用命令』仍有效"，但连这份也需按上表逐条甄别）：多代理协作纪律的**方法论**（测试作者与实现作者分离、四道闸+金丝雀的数据写库套路、不接受自报成功）跨越了老/新实例的变迁，在当前线依然适用，本规格 A1/C 节直接继承了这些方法论，只是命令层面换成了新坐标。


## B1-F · 预期冲突与预批融合（<日期> 立，管道首次遇到手工融合候选）

有些候选**必然冲突**（如同一文件被多单并行改动后的融合体 vs 老基线）。这类候选不能等 `merge` 炸了再临时想办法，必须在**准入阶段**就带着融合预案进来。

### 准入前置（缺一不可）
- 声明 `expected_conflict=true`，并给出**冲突面**（预期冲突的文件清单）
- **融合成果已预制**并有明确坐标（文件路径或提交锚点）
- **融合方案已获校验官预批**，预批记录可核（谁批的、批的是哪几条指引）

### B1 执行（分叉写法）
| 情形 | 期望 | 键值串 |
|---|---|---|
| 常规候选 | merge 干净 | `point=merge/expected_conflict=false/conflict_files=0/verdict=?` |
| 预期冲突候选 | merge **报冲突**（不报反而可疑，说明冲突面判断错了）→ 用预制融合成果解冲突 → 提交 | `point=merge/expected_conflict=true/conflict_files=?/fusion_source=?/verdict=?` |

### B1-F 融合完整性复验（**本节的要害，不可省**）
融合最大的风险**不是冲突没解决**——那是显性的，解不完 git 不让你提交；
**而是解冲突时静默丢掉了某一方的改动**——融合体照样能编译、能跑、能通过所有下游检查，只是某一单的功能悄悄没了。

所以融合后必须**逐来源断言**，不能笼统说"融合完成"：

```
对每个来源单的锚点 S：
  git diff S <融合结果> -- <S 改动的文件清单>
  期望：为空，或仅含可解释的融合调整（需逐条说明）
```

键值串：`point=fusion_check/sources_expected=?/sources_verified=?/missing_from=?/verdict=?`
- `sources_expected` 填参与融合的全部来源单（锚点或单号，分号分隔）
- `sources_verified` 填逐个核过且改动确实在的
- `missing_from` 填**融合后发现缺失的来源单**——**此字段非空即判 FAIL**
- `verdict` 仅在 `missing_from` 为空且校验官预批的复验点全过时才填 PASS

> 这是总纲「只验期望方向、不验反向」在融合场景的落地：只验"冲突解决了"是期望方向，**还要验"七单的东西一个都没丢"**这个反向。


## B5-P · 探针路径必须反查，禁猜测（<日期> 立，影子单4 实证；<日期> 补：生成器已实现自动反查）

**规矩**：B5 的探针路径**一律从前端 api 定义反查**（`frontend/src/api/*.ts` 里的实际请求路径），或从改动的 Controller 逐个提取 `@RequestMapping` + `@GetMapping`。**禁止按命名习惯猜测**。

- **实证（影子单4）**：首轮按习惯猜 `/list`，探针**全 404**；改从前端 api 定义反查得到 `/page` 后全绿。猜测不但白跑一轮，更危险的是——**404 是显性的还算走运，若猜到一个恰好存在但不相干的路径，就会拿"别处的 200"当本批的通过证据**。
- **另一个坑（影子单1 实证）**：Controller 可能**没有类级 `@RequestMapping`**（如 `ProductRepertoryController`），此时接口路径就是方法上的裸路径（`/api/getProductRep`），按"类名推前缀"必错。

**实现现状（<日期> postmortem-0045 逐条比对后补做，`make_worksheet.py` 的 `reverse_lookup_probe_paths`）**：生成器现按三级反查（按序尝试，每个改动文件按其形态适用其中一级，不互斥）自动渲染探针清单，不再吐 `<按上面改动文件反查的接口路径>` 这种字面占位符：

1. **前端 api 定义**（改动文件命中 `frontend/src/api/*.ts`）：解析文件内 `request.get(...)` 调用的请求路径（含模板字符串），带 `${...}`/`{...}` 变量的路径标注"需人工填真实 id"，不当静态路径直接给。
2. **Controller 注解组合**（改动文件命中 `**/controller/*.java`）：提取类级 `@RequestMapping` 与方法级 `@GetMapping`，组合成完整路径；类级注解不存在时（`ProductRepertoryController` 同款坑）直接用方法裸路径，不按类名推前缀。同一级另有一个扩展：改动文件是 `**/service/**/*Service.java` 或 `**/service/**/*ServiceImpl.java`（Controller 本身未改动，只改了它背后的 Service）时，按同包同名约定（`XxxServiceImpl`/`XxxService` → `controller/XxxController.java`）反查同目录树下的 Controller 文件，读到真实存在的文件才提取注解，读不到就此候选放弃、不产生路径——这条扩展不是"按类名猜 URL"，猜的只是"该看哪个文件"，路径文本仍然全部来自读到的真实注解。
3. **间接反查**：改动文件全批都拿不到前端 api ts/Controller 时，对改动的 `.vue` 文件解析其 `import ... from '@/api/xxx'`，回到第 1 级解析该 api 文件（即便该 api 文件本身未被本批改动）。

**兜底**：三级都取不到时，B5 段不吐占位符，改成显著的失败标注（大写 `B5_PROBE_LOOKUP_FAILED` 提示行），并在**准入结论**区加一条降级提示——不阻断准入（`gate` 仍按 A1-A5 判），但让人一眼看到这单的探针不完整，出单方必须手工反查后才能执行 B5。

**批量脚本**（<日期> 补，postmortem-0045 逐条比对暴露的可用性欠账）：反查成功时 B5 段除保留逐条 curl（含来源注释，供追溯每条路径出自哪个改动文件）外，额外渲染一个可直接执行的批量脚本——静态路径进 bash 数组、`for p in "${STATIC_PATHS[@]}"` 遍历，逐条打印状态码+路径，收尾汇总"共 N 条，2xx M 条，非 2xx 清单"；带变量的动态路径单独列在脚本尾部注释区，标注需人工填参，不进入批量循环。批量脚本必须可移植、禁依赖分词——显式 `#!/bin/bash`，路径一律走数组/heredoc 逐行读，禁止 `for p in $PATHS` 这类未加引号的裸展开（zsh 下不分词，会把多条路径拼成一条 URL，<日期> postmortem-0045 首轮探针即因此吐出一次 `000`）；反查全部落空时批量脚本段同样吐 `B5_BATCH_PROBE_UNAVAILABLE` 失败标注 + `exit 1`，不渲染空脚本。

**已知残留缺口**（下一笔欠账，未覆盖不代表允许猜，遇到时仍按"反查不到就手工补"处理）：改动文件只有 entity（无 service/controller/api 同批变更）；Controller 用了多行 `@RequestMapping`；前端用裸 `fetch()`/非 `request.` 封装发起请求；`@RequestMapping` 用 `method=RequestMethod.GET` 而非 `@GetMapping`。


## B5-N · 否定式断言必须配肯定式对照（<日期> 立，老严提示提炼为一般形式）

**规矩**：凡验证点是**否定式**的（"某个东西不应出现"），**必须配一条肯定式对照断言**，两条同时成立才算通过。

**为什么**：否定条件天然会被"整个机制坏掉"满足。
- 例：静默化类修复验「触发失败请求 → 全局弹窗不出现」——**弹窗组件整个挂掉、或错误压根没传到前端，弹窗一样不出现**，否定条件照样成立，而功能其实是坏的。
- 配上肯定式「绿条文案正确显示为『正在生成』」，才能证明**错误确实到达了前端、只是被正确地静默化了**。

**写法**：两条断言各自独立回填 verdict，整步 `verdict` 仅在两条同时为 true 时填 PASS。

与 `B5-P`（探针路径禁猜测）、老严 S1/S2 双条件同源，均为总纲「只验期望方向、不验反向」的落地。


## X · 操作者即兴必须披露（<日期> 立，影子单6 实证；与主窗口共同定案）

**规矩**：照单执行时，操作者的**任何即兴动作**——加参数、加请求头、换路径、改命令——**必须记入分歧列**。**自认是常识也不豁免。**

**为什么**（这条比缺陷本身值钱）：

> **操作者的经验会掩盖管道的缺陷。**

- **案例（影子单6）**：B5 探针当时不带登录态，**照单执行必然全部 401**。但操作者是老手，下意识补了鉴权头去跑，于是拿到 19/19 全 200——**管道"探针跑不通"这个缺陷被经验补上了，没暴露**。缺陷直到后续复核代码时才被发现，据此影子计数由 3/8 退回 2/8。
- 反过来看更危险：换个不熟的人照单跑，会看到一片 401，**却分不清是管道漏了认证、还是接口真坏了**。
- 同批的 `postmortem-0045` 对照单亦然：探针同样是带 token 跑的，**不能当作"单面可裸跑"的证据**。

**方法论根源**：影子期验的是「管道出的单，人照着执行不会出问题」。**若只看"结果对不对"、不看"过程中人补了什么"，验证结论会系统性虚高**——这是总纲「只验期望方向、不验反向」在验证流程自身上的一次复现：**我们验了管道，却没验"验管道的过程"**。

**执行**：分歧列里写清「即兴内容 + 为什么补 + 不补会怎样」。这类记录不必然算管道分歧（要看是管道漏了还是执行环境特殊），但**必须可见**，由出单方判定归属。


## OPS · 轮询器游标：停机前记、重挂后核（<日期> 立，我的错误描述换来的）

**我说错过一句**：先前我告诉主窗口「游标是 `WHERE id > last_id`，停机期间新落的单重挂后会自动补扫，**不漏审、不用手动报单号**」。
**这句话不可靠**——我只验了**取数 SQL**，没验**游标何时写入**就下了结论。主窗口实测：被杀批次**没有**自动重扫，是手工把 `.last_id` 拨回 3328 才触发的。

（代码阅读上 `echo "$MAXID" > "$STATE"` 位于 `done` 之后、按批写一次；与实测不吻合，说明还有我没看到的细节。**以实测为准。**）

**所以规矩不依赖对语义的推断**：

1. **停机前**：记下 `.last_id` 当前值（连同时间戳写进停机记录）
2. **重挂后**：核对停机窗内的登记区间是否都被扫过——**不是假设它会自动补，而是查一遍**
3. 发现缺口：手工把游标拨回停机前的值，触发重扫（重复审计无害，漏审有害）
4. **在途被杀的那批一律视为"结论过期"**，以重扫为准；停机窗内已落的告警文件同样作废

**教训归位**：这是「只验期望方向、不验反向」在运维上的一次复现——**我验了"能取到新单"，没验"取过的会不会被跳过"**。凡涉及游标/水位/断点续传的机制，**两个方向都要验**：取得到 + 不漏掉。


## B5-X · 可执行围栏的唯一标记 + 报数口径（<日期> 立，执行方与出单方各认一半）

### 一、可整段执行的围栏，以 `#!/bin/bash` 标头为**唯一**标记
一张单里可能有多个代码围栏（本单有 7 个）：说明片段、逐条 curl 参考、批量脚本、回填模板……**只有带 `#!/bin/bash` 标头的那一段是"可整段复制执行"的**。
- **执行方**：按围栏切分后只取带该标头的段落，**禁止把全部围栏拼接执行**——实证（0055）：拼接执行失败两次（一次 120s 超时掐断、一次语法错），按标头切分后一次通过
- **出单方**：可执行脚本必须带该标头；**非执行用途的围栏一律不带**，避免歧义

### 二、报数口径：`grep -c` 计的是**行数**，不是探针数
**我犯过**（0055）：报"21 条带认证探针"，实为 `grep -c 'Authorization'` 的**行数** 21 = 逐条 curl 区 20 行 + 批量脚本内 1 行；**可执行的静态探针实为 `STATIC_PATHS` 数组的 19 条**，与执行方跑出的 19/19 一致——**执行方的数字是对的，我的是错的**。

**口径固定**：
- **探针条数**一律以 `STATIC_PATHS` 数组元素数为准
- **动态路径条数**单独计，不并入静态数
- 自查报数**禁止直接用 `grep -c` 的行数当语义单元数**——同一语义单元可能出现在多处（逐条区 + 批量脚本 + 注释示例）

**教训**：这与"只验期望方向不验反向"同族——**我验了"有认证头"，没验"数出来的是不是探针本身"**。凡报数，先问一句：**这个数的单位是什么？**
