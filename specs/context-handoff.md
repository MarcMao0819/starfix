# 上下文水位与交接（Context Handoff）

**语言 / Language：** [中文](context-handoff.md) · [English](context-handoff.en.md)

舰长本窗口的上下文用量是关键指标：到 95% 必须已经有一份能让接手者续上的交接文档在盘上，并且压缩一结束就被自动读回。三段机制，全部不靠「记得」。

## 一、指标源

Claude Code 每次渲染状态栏把 JSON 传给状态栏脚本，其中 `context_window.used_percentage / remaining_percentage / context_window_size` 与 `session_id`。状态栏脚本追加一段（best-effort，出错静默）把它们写到 `${CTX_SNAPSHOT_DIR:-~/.claude/ctx-snapshot}/<session_id>.json`。这就是水位表。

## 二、95% 触发（模型侧）

`scripts/hooks/ctx-watch.sh <session_id> 95 60` 作为 Monitor 常挂；≥95% 打印一行 `CTX95 …` 事件进舰长窗口。舰长收到后立即：
1. 在 `${FLEET_HOME}/handoff/HANDOFF-<session_id>.md` 顶部手写段填「当前判断 / 在飞与判据 / 待答 / 刚发生的教训 / 下一步三件 / 风险与备份 / 坐标」（模板 `templates/06-交接HANDOFF.md`）。
2. 跑 `scripts/hooks/handoff-snapshot.sh` 让机械段（激活器 list、待答、台账尾、监听清单）覆盖到分隔线以下。
3. 收尾三件照常；然后允许压缩发生（或主动 `/compact`）。

## 三、压缩前兜底与压缩后回读（hook 侧）

| hook | matcher | 做什么 | 判据 |
|---|---|---|---|
| PreCompact | 全部（manual/auto） | 机械写 `HANDOFF-<sid>.md` 的快照段，刷新 `HANDOFF-latest.md` 软链；不阻止压缩 | 舰长来不及手写也有快照 |
| SessionStart | `compact|resume` | 若存在本会话的 HANDOFF，把手写段 + 快照前 160 行打到 stdout → 注入上下文 | 压缩后第一眼就是交接，不靠模型记得去读 |
| PostCompact | 全部 | 把压缩摘要追加到 `handoff/compact-log.md` | 事后能审「压缩丢了什么」 |

按 `session_id` 分文件：别的会话压缩时不会误读舰长的交接。hook 不继承 shell rc，本机包装脚本要显式 `export FLEET_HOME` 等再 exec 仓内脚本；样例 `examples/settings.hooks.sample.json`。

## 四、判别力

- 两态自证：把快照文件里的 `used_percentage` 改成 96 → 事件必须出；改回 50 → 不出。
- SessionStart 脚本用假 stdin（含真实 session_id）跑一遍，stdout 必须含 `<handoff` 与手写段；换一个不存在的 session_id 必须零输出。
- 交接文档的判据只有一条：接手者 10 分钟内不问人能正确派出第一单。
