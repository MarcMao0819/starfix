# 操作单 · CS-DEMO-FULLTABLE

- 生成时间（UTC）：<日期> 05:25:07Z
- 候选 worktree：`${FLEET_INTEGRATION_REPO}`
- 候选分支：`feat/<示例分支>`
- 候选 HEAD：`6f8d0325f8f994ce77f08d24a6ec42b13dc0c118`
- changeset 标题：演示：全段落渲染
- 集成分支：`feat/<项目>-integration`（worktree `${FLEET_INTEGRATION_REPO}`）
- merge-base：`dd77ec72a147525cf2a4d75a98bcae1c449454fe`
- 改动文件（2 个）：`frontend/src/views/sales-order/index.vue`、`src/main/java/com/<基座项目>/demo/entity/Demo.java`

## 准入结论：**PASS**

| 检查项 | 结论 | 证据 |
|---|---|---|
| A1 changeset 登记且校验官判词为 PASS | **PASS** | (演示用途，手工置 PASS——真实候选此项因回执缺结构化判词行而 FAIL，见报告说明) |
| A2 并线锚点=登记 commit_hash（含文件清单交叉核） | **PASS** | (演示用途，手工置 PASS) |
| A3 premerge-gate.v6 四查 | **PASS** | 见下方子项 |
| A3.g1_tree_clean | **PASS** | (演示) |
| A3.g2_no_midstate | **PASS** | (演示) |
| A3.g3_no_conflict_markers | **PASS** | (演示) |
| A3.g4_lineending | **PASS** | (演示) |
| A4 实体新增字段无 DDL/exist=false | **PASS** | (演示用途，手工置 PASS) |
| A5 是否触发 NEEDS_HUMAN（DB 迁移） | **未触发** | 未触及 db/*.sql |

## B0 · 前置：获取串行锁（人工执行）

锁状态：当前无锁（`.locks/CURRENT.lock` 不存在），可安全获取

```bash
mkdir -p ${TRAJ_HOME}/release-gate/.locks
cat > ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock <<EOF
changeset_no=CS-DEMO-FULLTABLE
worktree=${FLEET_INTEGRATION_REPO}
operator=<你的身份标识>
acquired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ttl_seconds=1800
EOF
```

先跑一遍并线前基点记录（供 D 节回滚使用，把输出抄进下方《实际结果》表格）：
```bash
git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} rev-parse HEAD
```

## B1 · merge --no-ff

```bash
cd ${FLEET_INTEGRATION_REPO}
git merge --no-ff 6f8d0325f8f994ce77f08d24a6ec42b13dc0c118   # 只认 commit_hash，不写分支名/HEAD
```

**独立回读验证**：
```bash
git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} log --oneline -3
git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} merge-base --is-ancestor 6f8d0325f8f994ce77f08d24a6ec42b13dc0c118 HEAD; echo "IS_ANCESTOR_EXIT=$?"
```
期望：`IS_ANCESTOR_EXIT=0`（6f8d0325f8f994ce77f08d24a6ec42b13dc0c118 确实已在集成线祖先链上）。

**失败回滚**：`MERGE_HEAD` 存在则 `git merge --abort`；已提交但校验不过则回 D 节 `git reset --hard <并线前基点>`。

## B2 · 前端类型检查（本批触及 frontend/**）

```bash
cd ${FLEET_INTEGRATION_REPO}/frontend
npx vue-tsc -b --pretty false
echo "TSC_EXIT=$?"
```
**独立回读验证**：`TSC_EXIT` 必须为 0（真实退出码单独捕获，不接管道尾）；若 freeze 巨型文件门禁已知红，`npm run build` 串联会截断后面的菜单三门禁，需单独复跑对应脚本。
**失败回滚**：回 D 节 `git reset --hard <并线前基点>`，候选打回修复。

## B3 · 后端打包（本批触及 src/main/java/** 或 pom.xml）

```bash
cd ${FLEET_INTEGRATION_REPO}
cp target/${APP_JAR} /tmp/erp-2.0.0-SNAPSHOT-prev-$(git --no-optional-locks rev-parse --short HEAD^1).jar.bak  # 备份当前运行 jar，供失败回滚
JH17="${FLEET_INTEGRATION_REPO}/.toolchain/jdk-17.0.20+8/Contents/Home"
OPTS=''
for p in api code comp file main model parser processing tree util jvm; do
  OPTS="$OPTS --add-exports=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED --add-opens=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED"
done
JAVA_HOME="$JH17" MAVEN_OPTS="$OPTS" MAVEN_USER_HOME=${FLEET_INTEGRATION_REPO}/.m2repo \
  ./mvnw -B -Dmaven.repo.local=${FLEET_INTEGRATION_REPO}/.m2repo/repository -DskipTests package
echo "BUILD_EXIT=$?"
```
**独立回读验证**：
```bash
echo $BUILD_EXIT   # 必须=0
ls -la target/${APP_JAR}
shasum -a 256 target/${APP_JAR}
```
期望：`BUILD_EXIT=0`；jar mtime 是本次 package 之后；记下 SHA 供 B4 后核对源位=运行位。
**失败回滚**：不产生新 jar，运行时不受影响；回 D 节 `git reset --hard <并线前基点>`。

## B4 · 加固重启（package 成功后必须紧接执行，禁止先建后等——<日期> 5098 事故教训）

```bash
cd ${FLEET_INTEGRATION_REPO}
OLD_PID=$(lsof -tnP -iTCP:${PORT_APP} -sTCP:LISTEN)
echo "OLD_PID=$OLD_PID"
kill "$OLD_PID" 2>/dev/null
for i in $(seq 1 30); do
  kill -0 "$OLD_PID" 2>/dev/null || { echo "DEAD at round $i"; break; }
  if [ "$i" -eq 15 ]; then kill -9 "$OLD_PID"; fi
  sleep 2
done
lsof -i :${PORT_APP}   # 期望空输出

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
  > .local-instance/logs/backend-host.log 2>&1 &
NEW_PID=$!
echo "NEW_PID=$NEW_PID"
```
**独立回读验证（四件套）**：
```bash
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' http://127.0.0.1:${PORT_APP}/api/v1/auth/me)
  [ "$CODE" = "401" -o "$CODE" = "200" ] && { echo "PROBE_OK round=$i code=$CODE"; break; }
  sleep 1
done
grep -c ERROR .local-instance/logs/backend-host.log
grep -ci "ambiguous mapping" .local-instance/logs/backend-host.log
ps -o lstart= -p "$NEW_PID"
stat -f '%Sm' target/${APP_JAR}
```
期望：①探活命中且记录轮次（9/15/8 这类台账数字是**启动探活轮次**，不是 kill 轮次，见 PIPELINE-SPEC.md §B4 节拍实测注记）；②ERROR 计数=0；③ambiguous mapping 命中=0；④jar mtime 早于/约等于 NEW_PID 启动时间。
**失败回滚**：杀新 PID，用 B3 备份的旧 jar 重跑本步骤第二段启动命令；迁移已执行但重启失败属 NEEDS_HUMAN。

## B5 · 冒烟（走用户同源入口 ${PORT_WEB}，针对本批触及实体加探针）

```bash
curl -s --noproxy '*' -X POST http://127.0.0.1:${PORT_WEB}/api/auth/station-login \
  -H 'Content-Type: application/json' \
  -d '{"stationName":"<项目>管理","pin":"1234"}'

# 按下方改动文件清单反查对应页面/接口，逐条探针（示例，人工按实际路由补全，见 PIPELINE-SPEC.md §B5）：
# touched: frontend/src/views/sales-order/index.vue
# touched: src/main/java/com/<基座项目>/demo/entity/Demo.java
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:${PORT_WEB}/api/<按上面改动文件反查的接口路径>
```
**独立回读验证**：每个探针 HTTP code 必须是业务正常码（200/401），不是 5xx（<事故编号> 教训：只探首页不够，必须覆盖本批触及实体的页面查询）。
**失败回滚**：切回 B3 备份的旧 jar 恢复服务（若无后端变更则前端 HMR 直接回退到并线前基点）；候选记 BLOCKED 退回修复。

## 《实际结果》回填表格（人工执行后填写；四段式键值串：候选→逐字命令→原始输出关键行→回读验证证据）

> 候选坐标见本单顶部（worktree/分支/HEAD/changeset）。下表每步的键值串字段名已预填、`?` 处照实填值，不做自由发挥；每步必须含 `point=` 与 `verdict=` 两个字段——这是worksheet_to_trace.py 转换器识别判决点的唯一依据，缺了就转不出合法轨迹。

| 步骤 | 逐字命令（已渲染，勿改） | 原始输出关键行（请按键值串填） | 回读验证证据 |
|---|---|---|---|
| B0 获取锁 + 记录并线前基点 | mkdir -p ${TRAJ_HOME}/release-gate/.locks<br>cat > ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock <<EOF<br>changeset_no=CS-DEMO-FULLTABLE<br>worktree=${FLEET_INTEGRATION_REPO}<br>operator=<你的身份标识><br>acquired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)<br>ttl_seconds=1800<br>EOF<br>git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} rev-parse HEAD | point=lock/lock_written=?/base_sha=?/verdict=? | 回读锁文件内容确认字段齐全；base_sha 填 rev-parse HEAD 的真实输出，供 D 节回滚基点使用 |
| B1 merge --no-ff | cd ${FLEET_INTEGRATION_REPO}<br>git merge --no-ff 6f8d0325f8f994ce77f08d24a6ec42b13dc0c118   # 只认 commit_hash，不写分支名/HEAD<br>git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} log --oneline -3<br>git --no-optional-locks -C ${FLEET_INTEGRATION_REPO} merge-base --is-ancestor 6f8d0325f8f994ce77f08d24a6ec42b13dc0c118 HEAD; echo "IS_ANCESTOR_EXIT=$?" | point=merge/new_head=?/is_ancestor_exit=?/verdict=? | new_head 填 log --oneline -3 里的新 HEAD 短 sha；is_ancestor_exit 填 IS_ANCESTOR_EXIT 的真实数值 |
| B2 前端类型检查 | cd ${FLEET_INTEGRATION_REPO}/frontend<br>npx vue-tsc -b --pretty false<br>echo "TSC_EXIT=$?" | point=tsc/tsc_exit=?/verdict=? | tsc_exit 填 TSC_EXIT 的真实数值（必须单独捕获，不接管道尾） |
| B3 后端打包 | cd ${FLEET_INTEGRATION_REPO}<br>cp target/${APP_JAR} /tmp/erp-2.0.0-SNAPSHOT-prev-$(git --no-optional-locks rev-parse --short HEAD^1).jar.bak  # 备份当前运行 jar，供失败回滚<br>JH17="${FLEET_INTEGRATION_REPO}/.toolchain/jdk-17.0.20+8/Contents/Home"<br>OPTS=''<br>for p in api code comp file main model parser processing tree util jvm; do<br>  OPTS="$OPTS --add-exports=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED --add-opens=jdk.compiler/com.sun.tools.javac.$p=ALL-UNNAMED"<br>done<br>JAVA_HOME="$JH17" MAVEN_OPTS="$OPTS" MAVEN_USER_HOME=${FLEET_INTEGRATION_REPO}/.m2repo \<br>  ./mvnw -B -Dmaven.repo.local=${FLEET_INTEGRATION_REPO}/.m2repo/repository -DskipTests package<br>echo "BUILD_EXIT=$?"<br>echo $BUILD_EXIT   # 必须=0<br>ls -la target/${APP_JAR}<br>shasum -a 256 target/${APP_JAR} | point=package/build_exit=?/jar_sha=?/jar_mtime=?/verdict=? | build_exit 填 $BUILD_EXIT 真实数值；jar_sha 填 shasum -a 256 输出；jar_mtime 填 ls -la 里的时间 |
| B4 加固重启 | cd ${FLEET_INTEGRATION_REPO}<br>OLD_PID=$(lsof -tnP -iTCP:${PORT_APP} -sTCP:LISTEN)<br>echo "OLD_PID=$OLD_PID"<br>kill "$OLD_PID" 2>/dev/null<br>for i in $(seq 1 30); do<br>  kill -0 "$OLD_PID" 2>/dev/null \|\| { echo "DEAD at round $i"; break; }<br>  if [ "$i" -eq 15 ]; then kill -9 "$OLD_PID"; fi<br>  sleep 2<br>done<br>lsof -i :${PORT_APP}   # 期望空输出<br><br>nohup ${FLEET_INTEGRATION_REPO}/.toolchain/jdk-11.0.32+9/Contents/Home/bin/java \<br>  -jar target/${APP_JAR} \<br>  --spring.profiles.active=prod,local \<br>  --spring.config.additional-location=optional:file:${FLEET_INTEGRATION_REPO}/.local-instance/ \<br>  --server.port=${PORT_APP} \<br>  --<项目>.medical.enabled=false \<br>  --candidate-readonly.enabled=false \<br>  --erp.instance.operating-sites.enabled=false \<br>  --erp.instance.medical.enabled=false \<br>  --erp.instance.rbac.seed=<项目> \<br>  > .local-instance/logs/backend-host.log 2>&1 &<br>NEW_PID=$!<br>echo "NEW_PID=$NEW_PID"<br>for i in $(seq 1 30); do<br>  CODE=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' http://127.0.0.1:${PORT_APP}/api/v1/auth/me)<br>  [ "$CODE" = "401" -o "$CODE" = "200" ] && { echo "PROBE_OK round=$i code=$CODE"; break; }<br>  sleep 1<br>done<br>grep -c ERROR .local-instance/logs/backend-host.log<br>grep -ci "ambiguous mapping" .local-instance/logs/backend-host.log<br>ps -o lstart= -p "$NEW_PID"<br>stat -f '%Sm' target/${APP_JAR} | point=restart/old_pid=?/kill_rounds=?/new_pid=?/startup_s=?/error_count=?/ambiguous_mapping=?/jar_sha=?/verdict=? | old_pid/new_pid 填 OLD_PID/NEW_PID 真实值；kill_rounds 填 DEAD at round 的真实轮次（2/16 等，是 kill 侧计数，不是 PROBE_OK 的探活轮次）；startup_s 填探活命中耗时秒数；error_count 填 grep -c ERROR 真实数值；ambiguous_mapping 填对应 grep -ci 真实数值；jar_sha 填与 B3 记下的 SHA 核对结果 |
| B5 冒烟 | curl -s --noproxy '*' -X POST http://127.0.0.1:${PORT_WEB}/api/auth/station-login \<br>  -H 'Content-Type: application/json' \<br>  -d '{"stationName":"<项目>管理","pin":"1234"}' | point=smoke/probe_paths=?/http_codes=?/verdict=? | probe_paths 填实际探测的接口路径列表（分号分隔）；http_codes 填对应 HTTP 状态码列表，顺序与 probe_paths 一一对应 |
| 释放锁 | rm ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock | point=lock_release/lock_removed=?/verdict=? | 回读 `test -f ${TRAJ_HOME}/release-gate/.locks/CURRENT.lock` 或再跑一次锁状态检查，确认锁文件已不存在 |

> 收尾条款：无论 PASS/FAIL/BLOCKED，落盘到 `迁移备份/回执/CS-YYYYMMDD-<单名>.md` 前，本单不算结束。
