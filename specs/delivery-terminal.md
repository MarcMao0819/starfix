# 终端互动协议（给其他 agent 投递任务）

**语言 / Language：** [中文](delivery-terminal.md) · [English](delivery-terminal.en.md)

舰长在终端里一上来的第一个问题不是「派什么」，而是**「我在什么终端里、其他 agent 在什么终端里、我能用哪条通道碰到它们」**。终端应用、复用器、IPC 总线各不相同；iTerm2 只是其中一种实现，不能当前提。

## 零、上任第一问：探测

跑 `scripts/terminal-probe.sh`，它回答：
- 我自己：`TERM_PROGRAM`（iTerm.app / Apple_Terminal / vscode / WezTerm / Windows Terminal …）、是否在 tmux/screen 里、是否有 `osascript`、iTerm2 Python API 是否可用、是否有同机 agent 总线。
- 其他 agent：每个舰员的 harness（Codex CLI / Claude Code / 无头批处理）与它所在的终端（会话 ID、tmux pane、远程主机）。
- 结论：每个舰员一条「推荐通道」，写进会话清单（`fleet-scan.sh` 输出）。

探测结果写进 `monitors-latest.json` 旁的 `fleet-snapshot`，接任者不必重探。

## 一、通道矩阵

| 通道 | 适用 | 送达判据 | 风险 |
|---|---|---|---|
| 同机 agent 总线（如 SendMessage） | 同 harness 的 agent 之间 | 消息进对方历史区 | 无误发风险，首选 |
| 终端自动化 API（iTerm2 Python API / AppleScript、WezTerm CLI、Windows Terminal + SendKeys） | CLI 型 agent 在图形终端里 | 两步投递读回确认 | **可能误发到人正在打字的终端** |
| 复用器（tmux `send-keys` / `capture-pane`，screen `stuff`） | 任何跑在 tmux/screen 里的 CLI | 同两步（capture-pane 读回） | 同上 |
| 文件信箱 | 对方没有可用 API，但会轮询目录 | 对方回执落盘 | 慢、需对方配合 |
| 批处理入口 | 无头工人机 | `-o` 捕获输出文件 | 无交互 |
| 人肉中转 | 都不行 | 台账记「人肉投递」 | 最后手段 |

## 二、两步投递（所有「往别人输入行写东西」的通道通用）

1. **写入前**：读对方输入行，必须空闲（提示符后无内容，占位灰字视同空），最多等 30 s，否则 `COMPOSER_BUSY_ABORT`。
2. **写文本**：只写入，不提交。
3. **读回确认**：输入行必须含**我们自己的关键词**才补回车。输入行是别的内容（人在打字）绝不回车 → `KW_MISMATCH_ABORT`。
4. **关键词守卫**：关键词必须是正文字面子串，否则脚本直接拒绝（`KW_NOT_IN_MSG_ABORT`）。不在正文=读回永不命中=同一单反复排队。
5. **提交后读屏**：确认对方转为 Working；`UNCONFIRMED` 时先读屏核是否已排队，勿重发。
6. 投递失败不许静默：终态写台账。

## 三、会话标识

- 会话 ID/pane ID **只从清单文件复制，禁凭前 8 位手写**；错 ID 的报错形态常是「忙」而不是「不存在」，会静默空投。
- 主窗自己的 ID 写进任务书收尾条款；变更须同步规程与在飞任务书。

## 四、内容规则

- 首行一句话说清是什么单；正文含任务书绝对路径、执行要求、回执路径、关键词、目标时长；不贴任务书全文；写「按一次执行」保证幂等。

## 五、参考实现：iTerm2

`scripts/send-to-session.sh <SESSION-UUID> "<正文>" <关键词>`：iTerm2 Python API/AppleScript 驱动的两步投递；前置=iTerm2 开启 Python API 与自动化权限。`--dry-run` 只打印三步不碰终端，上任自检用。换终端时照本协议重写「读输入行 / 写文本 / 读回 / 补回车」四个原语即可。
