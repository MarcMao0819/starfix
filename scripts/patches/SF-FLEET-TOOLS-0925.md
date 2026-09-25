# SF-FLEET-TOOLS-0925 · StarFix 可审阅增量

现役 `scripts/send-to-session.sh` 在本单开始前已有别人的未提交改动，因此本分支只提交本单新增的分类器与**相对当时现役文件**的脚本增量，不把既有 WIP 作为本单提交。

- 增量基线 SHA-256：`54ac8a52c7b7806911ae9116b043caa4d2add7585d79f6d14d6d9858574589fc`
- 原子替换后的现役 SHA-256：`62e4c81dc25a1b3b8ca9874ca327ddae0d2b3829b76da1677e541ad14e2f2f85`
- 增量补丁：[SF-FLEET-TOOLS-0925-send.delta.patch](SF-FLEET-TOOLS-0925-send.delta.patch)。它用于审核本单变化；只对上述精确基线适用，不应直接应用到仓库旧 HEAD。公开版把本机默认路径改写为 `$HOME`，因此补丁重放结果不应拿来核对现役文件 SHA。
- [session-state.py](../session-state.py) 与已安装的 `$HOME/fleet-data/scripts/session-state.py` 内容一致。`fleet-data` 下的 probe、idle-watch 与测试/回执不属于本 Git 仓库；现役路径及证据见 `$HOME/fleet-data/receipts/SF-FLEET-TOOLS-0925-回执.md`。

所有投递回放都使用文本夹具和假会话，未向现役舰员窗口发送测试消息。
