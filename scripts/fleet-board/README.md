# 舰队看板

只读的本机页面。看三件事：谁在做什么，舰长和舰员的往来，还没答的问题。页面上没有会改文件的按钮，也没有会往终端打字的按钮。

## 怎么起

没设 `FLEET_HOME` 又不带 `--demo`，程序一句话退出。

```bash
export FLEET_HOME=<舰队目录>
python3 scripts/fleet-board/server.py
```

不读真实目录、只用样例：

```bash
python3 scripts/fleet-board/server.py --demo
```

样例在 `scripts/fleet-board/demo/`。任务表沿用正式文件名，所以这份样例要单独放进版本库。

终端会打出本机回环地址。端口默认 `8765`，用 `FLEET_BOARD_PORT` 改。只监听回环地址。

页面每隔几秒自己重新取数。浅色和深色跟随系统。右上角可以切英文。样例的标题、问题、窗口名和卡住的原因跟着一起切。

## 读哪些文件

- 任务和待定问题：`$FLEET_HOME/task-activator.json`，也认 `ACTIVATOR_JSON`。
- 任务书、回执：任务里的 `book`、`receipt`。接真实目录时，时间用文件修改时间。演示模式改读文件里写明的 `时间：<日期> HH:MM` 或 `时间：<前日> HH:MM`。回执结论只看第一条有字的行。
- 窗口现状：`$FLEET_HOME/fleet-snapshot.txt`，也认 `FLEET_SNAPSHOT`。没有这个文件就不显示这一栏。
- 上下文水位：管家的 `status.json`。先看 `STEWARD_DATA_DIR`，再看 `$FLEET_HOME/steward/targets.json` 里的 `data_dir`，否则是 `$FLEET_HOME/steward/data/status.json`。用里面的 `used_percentage`。没有就不显示水位。

任务书和回执如果跑出舰队目录，那一条不读，写「没有数据」。

激活器缺了或读不出来，上面三块写「没有数据」，窗口栏有文件就照常显示。

## 往来怎么来的

不另写流水，从已有字段推：

- 派出任务书：演示模式用任务书里写明的时间；接真实目录用文件修改时间。文件没有就用 `created`。
- 交回执：回执文件在才算。演示模式用回执里写明的时间，接真实目录用修改时间。结论只认首行的通过、失败、受阻。
- 验收：`status` 已是已完成。时间用 `updated`，没有就用 `history` 里最后一条已完成。结论文字用 `evidence`，没有就用 `note`，再没有就用 `history` 的说明。
- 待定问题：`decisions` 的 `asked_at`、`answer`、`answered_at`、`tasks`。`answer` 空的排在前面。

人物卡上的状态：已完成优先；否则回执首行已有三种结论之一，就是已交回执待验收；否则 `blocker` 非空就是卡住；否则用原来的待开工或施工中。

在这个状态多久：已交回执从回执时间起算（演示模式用写明的时间，否则用修改时间）；卡住从 `updated` 起算；其它从 `history` 里最后一次进入该状态起算，没有就用 `updated`，再没有就用 `created`。`<日期>`、`<前日>` 按本机的今天和昨天理解。

舰长那张卡只写待验收几张、等负责人定几个，各带单号。明细在舰员卡和下面两栏。舰员卡按 `owner` 归人，先看施工中，再看已交回执、卡住、待开工、已完成。

## 哪些没做

- 不改任何文件，不往终端送字。
- 不读收件箱流水，也不读派单流水。答复只认激活器里已经写下的 `answer`。
- 放弃的单（`dropped`）不显示。
- 回执上除通过、失败、受阻以外的写法，不当成已交回执。
- 水位不直接读管家的原始会话快照。对不上人，就只认 `status.json`。
- 窗口栏不并进人物卡。快照里没有稳定的角色字段。
- 舰长自己正在做哪张单，没有单独字段。

## 测试

```bash
python3 -m unittest scripts/fleet-board/test_board.py
```
