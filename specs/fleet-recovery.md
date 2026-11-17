# 舰队恢复（Fleet Recovery）

**语言 / Language：** [中文](fleet-recovery.md) · [English](fleet-recovery.en.md)

合盖唤醒、整机重启、iTerm 整个没了，是三种不同局面。统一入口是 `scripts/recovery/recover-fleet.sh`（要先 `export FLEET_HOME`）。

## 三种局面

| 局面 | 现象 | 做法 |
|---|---|---|
| 合盖唤醒 | iTerm 标签都在，Claude 仍在跑 | `recover-fleet.sh status` 看一眼；一般只需重挂监听 |
| 重启后标签还在 | 标签在，里面停在 shell 或 Claude 没了 | `recover-fleet.sh up` → 对「命令行」用 `resume-inplace.py`，必要时重起管家 |
| 标签全没 | iTerm 重开或窗口被关光 | `recover-fleet.sh up` → 对「标签不在」用 `restore-fleet.py` 新开；**先认领后新开**（`adopt-open.py`） |

## 两条铁律

1. **先认领后新开**：登记表里的窗口号不在了，但同一会话已在别的标签里开着时，只改登记，不再开第二份（`adopt-open.py`）。
2. **读不出状态就停**：认不出标签里是 Claude、命令行还是别的，脚本不动那个标签，等人眼看一眼再跑。

## 相关文件

- 登记表：`$FLEET_HOME/recovery/fleet-table.json`（样例 `scripts/recovery/fleet-table.sample.json`）
- 角色与启动参数：`$FLEET_HOME/recovery/fleet-roster.json`（样例 `fleet-roster.sample.json`）
- 恢复日志：`$FLEET_HOME/recovery/recover-last.txt`
- 上下文管家：`scripts/steward/`（配置在 `$FLEET_HOME/steward/targets.json`）

演练：`DRY=1 recover-fleet.sh up` 只打印将执行的动作。