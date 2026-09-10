# 任务激活器（task-activator）

**语言 / Language：** [中文](task-activator.md) · [English](task-activator.en.md)

舰长的唯一任务真相表。它把「派过什么、卡在哪、回执到没到、Owner 答了什么」从舰长脑子里搬到文件里；整点报和停工哨兵都读它。

## 一、数据模型（`task-activator.json`）

```json
{
  "tasks": [{
    "id": "CODE", "title": "一句话", "status": "待开工|施工中|已完成",
    "owner": "舰员乙|舰员庚|队列→窗", "worktree": "feat/xxx",
    "book": "${FLEET_HOME}/CS-<日期>-<代号>-任务书.md",
    "receipt": "${FLEET_HOME}/回执/CS-<日期>-<代号>.md",
    "blocker": "卡什么（等谁/等哪个决策）", "note": "进展一句话",
    "evidence": "完成证据（并线 sha / 登记号 / 上线版本）",
    "updated": "YYYY-MM-DD HH:MM"
  }],
  "dropped": [ ...同结构 + reason ],
  "decisions": [{
    "qid": "Q01", "question": "题干含 A/B/C 与推荐", "who": "Owner",
    "tasks": ["CODE"], "answer": "", "ts": ""
  }]
}
```

- `status` 只有三态；「卡口径」不是状态，是 `blocker` 非空的待开工。
- `owner` 写「队列→窗」表示排队未派，停工哨兵不把它算停工。
- 回执状态由脚本读回执文件首段的 `终态：PASS/FAIL/BLOCKED` 自动识别；回执已到但任务没动 → 整点报标 ⚠。

## 二、命令

```
task-activator.py add <ID> "<title>" [--book ...] [--receipt ...]
task-activator.py set <ID> <状态> [--owner] [--worktree] [--note] [--blocker] [--evidence]
task-activator.py drop <ID> --reason "..."
task-activator.py list | board
task-activator.py ask add Q<NN> "<题干>" --tasks A,B --who Owner
task-activator.py ask answer Q<NN> --answer "<答复>"      # 通常由收件箱脚本调用
task-activator.py ask list | board | detail Q<NN> | recommend
```

环境变量 `ACTIVATOR_JSON` 指向副本可做测试，不传时用正式路径。

## 三、决策闭环

1. 舰长 `ask add`：题干必须是确认题（A/B/C + 推荐），并列出答复后解锁的任务。
2. 面板（浮窗）展示未答问题；Owner 点答 → 追加一行到 `ask-inbox.jsonl`。
3. `ask-inbox-apply.sh`（`tail -F`）读到新行 → `ask answer` → 任务 blocker 清空 → 通知舰长。
4. **收件箱只追加**：原地改写会让 `tail -F` 重放全部历史答复、冲掉所有任务 blocker（真实事故）。脱敏须先停监听、再改、再重挂。
5. 答复里若含凭据类内容，落库前抹值，只留「已答」。

## 四、整点报与哨兵

- 整点报：施工中 N · 待开工 N（可开工/卡口径）· 已完成 N；施工中逐条带 owner 与时长；卡口径按卡谁分组；回执已到未处置 ⚠；可开工未派提醒；末尾带铁律一行。
- 停工哨兵读激活器出名单：施工中且回执静默 >60 min 报警；真人任务（屏幕=真人）标注不算停工。
