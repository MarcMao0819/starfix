# RESULT-B — 对拍实验 B 组（预编译执行图路径）

## 状态
PASS

## 三个问题的答案

1. **`<title>`**：`对拍实验夹具页 DP-001`
2. **class=`marker` 元素文本**：`MARKER-VALUE-7391`
3. **截图**：已生成，`ls -la` 原始输出：
```
-rw-r--r--  1 owner  staff  17230  8月 14 17:13 ${TRAJ_HOME}/skillc/duipai/shot-B.png
```
（同目录下最终状态）：
```
total 120
drwxr-xr-x  6 owner  staff    192  8月 14 17:13 .
drwxr-xr-x  6 owner  staff    192  8月 14 17:10 ..
-rw-r--r--  1 owner  staff    258  8月 14 17:10 fixture.html
-rw-r--r--  1 owner  staff  13269  8月 14 17:10 prologue.json
-rw-r--r--  1 owner  staff  17230  8月 14 17:13 shot-A.png
-rw-r--r--  1 owner  staff  17230  8月 14 17:13 shot-B.png
```

## 执行过的每一条 shell 命令（按顺序）

1. **[序幕 n2]** Preamble 大块脚本（gstack-update-check / session touch / gstack-config get × 多个 / git branch / repo-mode / telemetry 相关 echo 串）—— 原样执行，见下方逐块记录。
2. **[序幕 n4]** Artifacts Sync 大块脚本（gbrain 检测 / brain-sync 模式判断 / echo ARTIFACTS_SYNC 状态）—— 原样执行。
3. **[序幕 n5]** 
   ```
   "$_BRAIN_CONFIG_BIN" set artifacts_sync_mode <choice>
   "$_BRAIN_CONFIG_BIN" set artifacts_sync_mode_prompted true
   ```
4. **[序幕 n6]**
   ```
   "~/.claude/skills/gstack/bin/gstack-brain-sync" --discover-new 2>/dev/null || true
   "~/.claude/skills/gstack/bin/gstack-brain-sync" --once 2>/dev/null || true
   ```
5. **[序幕 n10]** SETUP 检查块（`git rev-parse --show-toplevel` + 判断 `browse/dist/browse` 是否存在可执行）。
6. **[序幕 n11]** bun 安装检查块（`command -v bun` → 若无则下载 install 脚本校验 checksum → 安装）。
7. `command -v bun; echo "bun_check_exit:$?"` —— **自行加的排查命令**（非编译产物里的块），确认当前 shell PATH 下确实没有 bun。
8. `ls -la ~/.bun/bin/ ...` / `find ~ -maxdepth 4 -iname "bun" -type f` / `brew list bun` / `echo $PATH` —— **自行加的排查命令**，定位到 `~/.bun/bin/bun` 实际存在但不在 PATH 里。
9. `export PATH="$HOME/.bun/bin:$PATH"` + `"$B" goto "file://${TRAJ_HOME}/skillc/duipai/fixture.html"` —— **自行加的修复步骤**，把 bun 加入 PATH 后重跑 goto。
10. `"$B" js "document.title"` 和 `"$B" js "document.querySelector('.marker') ? ... : 'NOT_FOUND'"` —— 任务本身的取值命令（PATH 已带 bun）。
11. `"$B" screenshot "${TRAJ_HOME}/skillc/duipai/shot-B.png"` + `ls -la` 核验。
12. **[尾声 n8]** `gstack-learnings-log '{"skill":"SKILL_NAME",...}'` 原样执行。
13. **[尾声 n8 复测]** 同一命令加 `2>&1` 重跑一次，想看有没有 stderr（仍是排查，非任务必需，但直接源于 n8 本身失败后的排错，未偏离"如实记录"精神）。
14. `ls -la ~/.claude/skills/gstack/bin/gstack-learnings-log` + `type ...` —— 排查 n8 为何 127。
15. `head -1 ~/.claude/skills/gstack/bin/gstack-learnings-log` —— 排查 shebang。
16. Read 该脚本源码（非 shell 命令，用 Read 工具看内容，确认它内部调用了 `bun -e`）。
17. **[尾声 n9]** Telemetry 大块脚本（`_TEL_END`/`_TEL_DUR` 计算 + `gstack-timeline-log` + analytics 追加 + `gstack-telemetry-log`）原样执行。
18. `ls -la "${TRAJ_HOME}/skillc/duipai/"` —— 最终核验目录状态。

## 序幕命令块逐块执行结果

| id | heading | 结果 | 说明 |
|----|---------|------|------|
| n2 | Preamble (run first) | **成功**（exit 0） | 输出中带 `UPGRADE_AVAILABLE 1.42.2.0 1.62.0.0`（当前 skill 版本落后），其余全部正常打印（BRANCH/PROACTIVE/TELEMETRY等） |
| n4 | Artifacts Sync (skill start) | **成功**（exit 0） | 输出 `ARTIFACTS_SYNC: off`，gbrain 未配置，分支判断走到 else 分支 |
| n5 | Artifacts Sync (skill start) | **失败**（exit 1） | 原文命令本身含未替换的模板占位符 `<choice>`，逐字执行会被 shell 当作从名为 `choice` 的文件重定向输入，报错：`(eval):3: parse error near '\n'`。这是一条"选择分支"模板行，不是可直接执行的具体命令 |
| n6 | Artifacts Sync (skill start) | **表面成功**（exit 0），实质空跑 | `_BRAIN_CONFIG_BIN`/其他变量在这个新 shell 里已不存在，且路径字符串里的 `~` 在双引号内不会展开，所以 `"~/.claude/skills/gstack/bin/gstack-brain-sync"` 实际上是一个不存在的字面路径；两条命令都因为有 `\|\| true` 兜底而"成功"退出，但本质是空操作 |
| n10 | SETUP (run this check BEFORE any browse command) | **成功**（exit 0），但判定有误导性 | 输出 `READY: ${FLEET_HOME}/.claude/skills/gstack/browse/dist/browse` —— 该检查只验证了二进制文件"存在且可执行"，并未验证它能不能真的跑起来（后面证明它启动时需要 `bun` 在 PATH 里，而当时 PATH 里没有） |
| n11 | SETUP (run this check BEFORE any browse command) | **失败**（exit 1） | `command -v bun` 判定 bun 不在 PATH（真实情况：bun 二进制其实装在 `~/.bun/bin/bun`，只是没加进 PATH），于是走进下载分支；下载的 bun.sh 安装脚本实际 SHA-256 为 `04882bf4...`，与脚本里硬编码的期望值 `bab8acfb...` 不一致，直接报错退出：`ERROR: bun install script checksum mismatch`。这条块自身设计成"校验失败就 exit 1，不继续"，没有兜底 |

## 尾声命令块逐块执行结果

| id | heading | 结果 | 说明 |
|----|---------|------|------|
| n8 | Operational Self-Improvement | **失败**（exit 127） | 命令本身也是模板，含未替换占位符 `SKILL_NAME`/`SHORT_KEY`/`DESCRIPTION`/`N`（`"confidence":N` 不是合法 JSON，`N` 也不是合法整数）。实际执行卡在脚本内部调用 `bun -e "..."` 做 JSON 校验，而这次调用没有把 `~/.bun/bin` 加进 PATH，所以 `bun` 命令本身就找不到，`set -euo pipefail` 下整体以 127 退出。即使补上 PATH，占位符本身也过不了脚本内的 JSON.parse/字段校验 |
| n9 | Telemetry (run last) | **表面成功**（exit 0），实质数据是垃圾 | `_TEL_START`、`_SESSION_ID`、`_TEL` 都是 n2 块里在另一个独立 shell 中设置的，本次调用里全部未定义；`_TEL_DUR` 因此算出的是当前时间戳减空值的畸形结果；`[ "$_TEL" != "off" ]` 因为 `_TEL` 为空也判定为真，于是往 analytics 文件里追加了一条 session/duration 字段全是垃圾值的记录。命令没有报错，但写入的数据不可信 |

## 需要我自己发明步骤的地方（编译产物没覆盖到的缺口）

1. **PATH 里没有 bun，但 n10 的"READY"判定没有发现这一点。** n10 只做了"文件存在且可执行位"检查，没有实际尝试运行 browse 二进制。真正执行 `"$B" goto ...` 时才报错 `[browse] Executable not found in $PATH: "bun"`，说明 browse 这个编译后的二进制启动时依赖 bun 运行时在 PATH 里，但序幕命令没有任何一步去验证或保证这一点。
2. **n11（自动装 bun）本身失败了（checksum mismatch），且失败后序幕脚本没有任何后续兜底或提示"改用已有 bun"。** 我自己排查发现机器上其实已经装了一份 bun（`~/.bun/bin/bun`，63MB），只是没进当前 shell 的 PATH。这是我自己想到并执行的排查+修复：`export PATH="$HOME/.bun/bin:$PATH"`。编译产物里没有任何一步引导到这个修复方式（既没有检查 `~/.bun/bin` 是否已存在，也没有提示手动加 PATH）。
3. **n5、n8 两个块本质是"未渲染的模板行"，不是可直接执行的命令**（分别含 `<choice>` 和 `SKILL_NAME`/`SHORT_KEY`/`DESCRIPTION`/`N` 占位符）。按"原样执行"的要求跑了，两者都按预期报错。编译产物没有给出"如何选择/填充这些占位符"的判断逻辑，只留了裸模板。
4. **跨 block 的变量状态失效是本组反复出现的模式**（n6 的 `_BRAIN_CONFIG_BIN`、n9 的 `_TEL_START`/`_SESSION_ID`/`_TEL`），因为每个 cmd 块在独立 shell 里跑，前一块设的变量到下一块already不存在。多数情况下靠 `\|\| true` 或空值兜底"安静地"跑通，不报错，但产生的是空操作或垃圾数据，而不是真失败——这类"假成功"本身也是需要人工识别的缺口，不是命令行报错能直接暴露的。
5. 除以上 4 点外，任务本身要问的三个问题（title / marker 文本 / 截图）用 `$B js ...` 和 `$B screenshot ...` 直接可以做，没有需要额外发明的地方；只在 goto 报错后临时加了 3 条排查命令（`command -v bun`、`ls ~/.bun/bin`、`echo $PATH`）来定位问题根因。
