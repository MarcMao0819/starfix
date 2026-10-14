# 上下文水位与交接（Context Handoff）

**语言 / Language：** [中文](context-handoff.md) · [English](context-handoff.en.md)

舰长本窗口的上下文用量是关键指标：到 85% 必须已经有一份能让接手者续上的交接文档在盘上，并且压缩一结束就被自动读回。三段机制，全部不靠「记得」。

## 一、指标源

Claude Code 每次渲染状态栏把 JSON 传给状态栏脚本，其中 `context_window.used_percentage / remaining_percentage / context_window_size` 与 `session_id`。状态栏脚本追加一段（best-effort，出错静默）把它们写到 `${CTX_SNAPSHOT_DIR:-~/.claude/ctx-snapshot}/<session_id>.json`。这就是水位表。

## 二、85% 自动触发（hook 侧，不靠模型记得）

`scripts/hooks/ctx-handoff-guard.py` 挂在 PostToolUse（matcher `*`）、UserPromptSubmit、Stop 三个事件上（本机包装脚本 export `FLEET_HOME` 后 exec），只对名单里的会话生效（`${CTX_GUARD_LIST:-~/.claude/hooks/ctx-guard-sessions.txt}`，每行一个 iTerm 会话 UUID 或 session_id）：
1. 本「压缩周期」第一次到 85%（`CTX_HANDOFF_THRESHOLD`）：不经模型先跑 `handoff-snapshot.sh`，把激活器、待答、台账尾、监听清单写进 `${FLEET_HOME}/handoff/HANDOFF-<session_id>.md` 的快照段，刷新 `HANDOFF-latest.md`；再往模型上下文插一条指令：立刻补写顶部手写段（现状一句话 / 在飞与判据 / 待答 / 刚发生的教训 / 下一步三件 / 风险与备份 / 坐标，模板 `templates/06-交接HANDOFF.md`）。
2. 到 92%（`CTX_HANDOFF_THRESHOLD2`）再快照、再提醒一次（第二档，压缩随时可能发生）。
3. Stop：已触发、但手写段在触发后没改过 → 拦一次（`stop_hook_active` 为真不再拦），逼模型先写交接再结束本轮。
4. 用量回落到阈值减 25（`CTX_HANDOFF_RESET_GAP`）以下（通常是压缩之后）视为新周期，状态清零。
5. 守卫自证在跑：每次被调用最多每 60 秒刷新 `<session_id>.guard.seen`；看它停没停就知道守卫有没有失效。

`scripts/hooks/ctx-watch.sh <session_id> 85 60` 仍可作 Monitor 旁路使用，但不再是主路径。

## 三、压缩前兜底与压缩后回读（hook 侧）

| hook | matcher | 做什么 | 判据 |
|---|---|---|---|
| PreCompact | 全部（manual/auto） | 机械写 `HANDOFF-<sid>.md` 的快照段，刷新 `HANDOFF-latest.md` 软链；不阻止压缩 | 舰长来不及手写也有快照 |
| SessionStart | `compact|resume` | 若存在本会话的 HANDOFF，把分隔线以上手写段全文 + 快照前 80 行（`FLEET_HANDOFF_INJECT_LINES`）打到 stdout → 注入上下文，并要求先用 Read 读完全文 | 压缩后第一眼就是交接，手写段不被截断，不靠模型记得去读 |
| PostCompact | 全部 | 把压缩摘要追加到 `handoff/compact-log.md` | 事后能审「压缩丢了什么」 |

按 `session_id` 分文件：别的会话压缩时不会误读舰长的交接。hook 不继承 shell rc，本机包装脚本要显式 `export FLEET_HOME` 等再 exec 仓内脚本；样例 `examples/settings.hooks.sample.json`。

## 四、判别力

- 两态自证：`scripts/hooks/test-ctx-handoff-guard.sh` 在临时目录里跑 17 例（50% 不出声、86% 触发并落快照、同周期不重复、Stop 拦一次且只拦一次、92% 第二档、回落清零后再触发、按 iTerm UUID 命中名单、SessionStart 注入手写段全文且快照按行截断、无交接文件零输出）；把阈值改成 99 跑同一套必须变红。
- SessionStart 脚本用假 stdin（含真实 session_id）跑一遍，stdout 必须含 `<handoff` 与手写段；换一个不存在的 session_id 必须零输出。
- 交接文档的判据只有一条：接手者 10 分钟内不问人能正确派出第一单。
