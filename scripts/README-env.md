# 环境变量清单

**语言 / Language：** [中文](README-env.md) · [English](README-env.en.md)

脚本里**不留任何绝对路径、真实 ID 和密钥**，全部从这里读。

分两类，用法完全不同：

- **必填、无默认值** —— 缺了脚本立刻退出并说明缺哪个。
  故意不给默认值：默认值会让脚本在别人的机器上**安静地读写错地方**，
  报错反而是最便宜的结果（见 `doctrine/02-试错战例集.md` 里「有缺省的配置才危险」那条）。
- **有默认值** —— 不设也能跑，默认值一律落在 `$FLEET_HOME` 下面。

---

## 一、必填

| 变量 | 含义 | 缺了会怎样 |
|---|---|---|
| `FLEET_HOME` | 舰队工作目录：激活器 json、请示台、快照、归档都在它下面 | 所有脚本退出并打印「缺少环境变量 FLEET_HOME」，退出码 `1`（Python 脚本）/ `2`（relay） |

```bash
export FLEET_HOME="$HOME/fleet-data"     # 目录不存在时脚本会自己建文件，但目录要先有
mkdir -p "$FLEET_HOME"
```

## 二、路径类（有默认值）

| 变量 | 默认值 | 用在哪 |
|---|---|---|
| `ACTIVATOR_JSON` | `$FLEET_HOME/task-activator.json` | 任务激活器的数据文件；跑测试时指到副本，别动正式的 |
| `FLEET_ASK_PANEL` | `$FLEET_HOME/请示台.md` | `task-activator.py` 生成的请示台 |
| `FLEET_ASK_INBOX` | `$FLEET_HOME/ask-inbox.jsonl` | 浮窗回填的答复收件箱 |
| `FLEET_DISPATCH_LOG` | `$FLEET_HOME/dispatch.log` | 派单流水 |
| `FLEET_WATCHDOG_TXT` | `$FLEET_HOME/watchdog.txt` | 看门狗读的会话清单 |
| `FLEET_SNAPSHOT` | `$FLEET_HOME/fleet-snapshot.txt` | `fleet-scan.sh` 的舰队快照（覆盖式，恒指最新） |
| `FLEET_SCRIPTS_DIR` | 脚本自身所在目录 | `ask-inbox-apply.sh` 的工作目录 |
| `FLEET_INTEGRATION_REPO` | 空 | 集成分支所在的 git 仓；不设则跳过并线状态检查 |
| `IM_ARCHIVE_DIR` | `$FLEET_HOME/im-archive` | IM 会话归档落盘目录 |
| `FLEET_ACTIVATOR_JSON` | `$FLEET_HOME/task-activator.json` | 浮窗（Swift）读的激活器文件 |

## 三、IM 接力

收件人和应用配置集中在一个 JSON 里，**那个文件不进仓库**。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IM_CONTACTS_JSON` | `$FLEET_HOME/im-contacts.json` | 应用 ID、密钥来源、收件人表、抄送人；格式见 `examples/im-contacts.sample.json` |
| `IM_APP_ID` | 取 JSON 里的 `app_id` | 环境变量优先 |
| `IM_APP_SECRET` | 取 JSON 里的 `app_secret_file` → `app_secret` | **推荐用环境变量**，别把密钥写进配置文件 |
| `SECRETS_DIR` | 无 | 样例里 `app_secret_file` 用它拼路径；不设会报「环境变量 SECRETS_DIR 没有设置，路径展不开」 |
| `PORT_IM_TUNNEL` | `8443` | 直连失败时回退的本地 SSH 隧道端口 |
| `SSH_USER` / `HOST_LAN` | 无 | 只出现在注释里，说明隧道怎么建；脚本不读它们 |

密钥优先级：`IM_APP_SECRET` 环境变量 → JSON 的 `app_secret_file` → JSON 的 `app_secret`（明文，不建议）。

```bash
export IM_APP_SECRET="$(cat ~/.secrets/im-app-secret)"   # 推荐
```

## 四、行为开关

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ACTIVATOR_STALL_H` | `6` | 任务多少小时无终态算「停滞」，看门狗据此告警 |

## 五、最小可跑示例

```bash
export FLEET_HOME="$HOME/fleet-data"
mkdir -p "$FLEET_HOME"

python3 scripts/task-activator.py add T1 "第一条任务" --owner 舰员乙
python3 scripts/task-activator.py list
python3 scripts/task-activator.py report
```

IM 接力要多两步：

```bash
cp examples/im-contacts.sample.json "$FLEET_HOME/im-contacts.json"
# 编辑它：填上真实的 app_id 与各角色的用户 ID
export IM_APP_SECRET="……"
python3 scripts/relay/im-send.py Owner "测试消息"
```

## 六、怎么确认配错了

所有配置错误都会打印**一句人话 + 怎么修**，不会抛 Python 堆栈。常见几条：

```
缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md
配置错误：找不到 IM 配置文件：/path/im-contacts.json
配置错误：app_secret_file 里的环境变量 SECRETS_DIR 没有设置，路径展不开：${SECRETS_DIR}/im-app-secret
```

看到堆栈说明是真的出 bug 了，不是没配——这两种要能一眼分开。
