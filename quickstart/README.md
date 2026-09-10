# quickstart · 10 分钟把闭环跑起来

**语言 / Language：** [中文](README.md) · [English](README.en.md)

牵星 StarFix 的规程是给「一个 AI 舰长指挥一支终端 AI 舰队」写的，正式版依赖 iTerm2、
浮窗、IM 这些外部件。这个目录是**最小可跑版**：只要有 `tmux` + `python3`，
在 macOS 或 Linux 上十分钟就能亲手跑一遍完整闭环，看清楚每个部件在哪、接口长什么样。

一条命令：

```bash
bash quickstart/demo.sh
```

跑完你会看见这条链路走完一整圈（约 15 秒）：

```
派单 → 两步投递 → 舰员承建 → 回执落盘 → 监听报事件 → 舰长验收
                                            ↓
                          提确认题 → 决策面板 → 指挥官答复 → 收件箱 → 任务解锁
```

全程落在一个临时目录里，跑完自动清干净；**不碰你自己的 `FLEET_HOME`**，
不需要网络、账号、iTerm2。

---

## 一、前置

| 要求 | 说明 |
|---|---|
| `tmux` | macOS：`brew install tmux`；Debian/Ubuntu：`sudo apt install tmux` |
| `python3` | 3.6+ 即可，只用标准库 |
| `bash` | 3.2 也行（macOS 自带的就是 3.2，脚本里刻意避开了关联数组这类 bash 4 特性） |

不需要：iTerm2、AppleScript、IM 应用、数据库、网络。

## 二、跑 demo.sh

```bash
git clone <本仓库>            # 或者直接进你已经有的这份仓库
cd starfix
bash quickstart/demo.sh
```

每一步都会打印 `✔ 步骤 N：…`，任一步失败立刻 `✘` 说明原因并以非 0 退出。
想留下临时目录自己翻，加 `QS_KEEP=1`：

```bash
QS_KEEP=1 bash quickstart/demo.sh
```

demo 干的十五件事，就是舰长一天里真实干的事：

| 步 | 干什么 | 用到的件 |
|---|---|---|
| 1–2 | 前置检查、建临时舰队目录 | — |
| 3 | 起 tmux 会话，pane 里跑一个「假舰员」 | `fake-crew.sh` |
| 4 | 写任务书、登进激活器（含舰长钩子：没写 owner 的单不许进施工中） | `scripts/task-activator.py` |
| 5 | 挂回执监听（只报新落盘的） | `receipt-watch.sh` |
| 6 | 两步投递：写入 → 读回关键词 → 才补回车 | `send-to-tmux.sh` |
| 7 | 等回执落盘，并**回头核实物**（监听说到了不算，文件在才算） | — |
| 8 | 验收判完成（激活器自己去读回执终态，不是 PASS 不放行） | `task-activator.py set` |
| 9 | 提一道确认题，任务被标成「卡口径」 | `task-activator.py ask add` |
| 10 | 挂收件箱落库监听 | `scripts/ask-inbox-apply.sh` |
| 11–12 | 决策面板列未答题、指挥官答复（只追加一行） | `ask-cli.py` |
| 13 | 答复落库 → blocker 清空 → 任务解锁 | 上面两件 |
| 14–15 | 最终盘点、收工清理并核验 tmux 无残留 | — |

## 三、每个文件对应正式版的哪个部件

| quickstart | 正式版 | 差在哪 |
|---|---|---|
| `send-to-tmux.sh` | `scripts/send-to-session.sh`（iTerm2）+ `specs/delivery-terminal.md` §二 | 同一套两步协议，只换了四个原语：读输入行 / 写文本 / 读回 / 补回车。iTerm2 版用 AppleScript，这版用 `tmux capture-pane` / `send-keys` |
| `fake-crew.sh` | 一个真实的终端 AI 会话（Codex CLI、Claude Code、无头工人机）+ `templates/02-回执.md` | 「干活」那一步这里是 `sleep 2`，真实舰员是模型在做。收一行、写回执、回到空闲提示符这套契约完全一样 |
| `receipt-watch.sh` | `specs/monitors.md` ① 回执监听 | 正式版靠 harness 的 Monitor 类工具；这里是 1 秒轮询的兜底实现（规程里的兜底四序之一） |
| `ask-cli.py` | `scripts/askpanel/`（macOS 请示浮窗）、`specs/away-mode.md`（IM 接力） | 面板换成了命令行，收件箱契约一模一样：一行 JSON、只追加 |
| `demo.sh` | `skill/SKILL.md` §1 上任第一动作 + README「一张单从派出到上线」 | 把九步压成十五个可见步骤，去掉了并线与部署（那两步需要真仓库） |
| — | `scripts/task-activator.py`、`scripts/ask-inbox-apply.sh` | **原封不动直接用**，demo 没有复制它们的任何逻辑 |

## 四、把假舰员换成真实的 Codex / Claude 会话

假舰员唯一的作用是「站在终端里等一行输入」。真实舰员做的是同一件事，
所以换起来只有三处要改：

1. **起会话**。把 pane 里跑的东西换成你的 CLI：

   ```bash
   tmux new-session -d -s crew-a1 'codex'      # 或 'claude'，或任何你的舰员 CLI
   tmux list-panes -a -F '#{pane_id} #{session_name}'   # pane id 从这里复制，别手写
   ```

   > 会话 ID/pane ID **只从清单复制**。手写的 ID 常常表现成「对方忙」而不是「不存在」，
   > 于是静默空投一轮——这条在 `specs/delivery-terminal.md` §三 里是硬规矩。

2. **调空闲判据**。`send-to-tmux.sh` 认「光标那一行像个空提示符」为空闲，
   默认正则覆盖 `❯ › » $ # >` 结尾。你的 CLI 输入行长得不一样（比如带右边框、
   或者空框里有灰色占位提示），就设 `QS_IDLE_RE`：

   ```bash
   QS_IDLE_RE='(❯|›)[[:space:]]*│?[[:space:]]*$' \
     bash quickstart/send-to-tmux.sh %7 "$BOOK" CS-DEMO01
   ```

   先用 `--dry-run` 打印四步、不碰终端，确认参数对了再实投。

3. **任务书带全收尾条款**。真实舰员不会猜你要什么，任务书里必须逐字写清：
   回执绝对路径、三态（PASS/FAIL/BLOCKED）皆落盘、关键词、目标时长、
   落盘后怎么通知舰长。模板在 `templates/01-任务书.md`。
   **关键词必须是投递正文的字面子串**——不在正文，读回永远不命中，同一单会反复排队。
   demo 里的做法可以直接抄：把单号（`CS-DEMO01`）当关键词，正文就是含单号的任务书路径。

投递失败的四种终态都有明确出口，不会静默：

| 输出 | 退出码 | 含义 |
|---|---|---|
| `DELIVERED` | 0 | 写进去了，回车吃了，关键词离开输入行 |
| `KW_NOT_IN_MSG_ABORT` | 3 | 关键词不是正文子串，直接拒绝（连终端都不碰） |
| `MSG_MULTILINE_ABORT` | 3 | 正文有换行，会被当回车提前提交，拒绝 |
| `PANE_NOT_FOUND` | 3 | pane 不存在（和「忙」分开报，别再空投一轮） |
| `COMPOSER_BUSY_ABORT` | 2 | 对方输入行 30 秒内没空闲下来（多半是人在打字） |
| `KW_MISMATCH_ABORT` / `UNCONFIRMED_NO_BLIND_CR` | 1 | 输入行里不是我们的关键词 → **绝不补回车**。宁可不投，不可误发 |

## 五、把 ask-cli 换成浮窗 / IM

收件箱是唯一的契约，一行一条 JSON，**只追加**：

```json
{"qid": "Q01", "answer": "A", "ts": "…", "via": "ask-cli"}
```

所以浮窗、IM 机器人、网页表单、甚至 `echo >>`，谁都能当面板，只要往
`$FLEET_ASK_INBOX` 追加同格式的一行。后半截（`ask-inbox-apply.sh` 读到 → 调
`task-activator.py ask answer` → 清 blocker → 通知舰长）完全不用改。

两条会踩的：

- **只追加**。原地改写会让 `tail -F` 重放全部历史答复、把所有任务的 blocker 一次冲掉（真实事故）。
  要脱敏就先停监听、再改、再重挂。
- **先挂后答**。`ask-inbox-apply.sh` 用 `tail -n0 -F`，只认挂上之后的新行；先答后挂 = 那条答复永远不落库。

`ask-inbox-apply.sh` 会 `cd` 到 `FLEET_SCRIPTS_DIR`，然后在**那个目录**里
`tail ask-inbox.jsonl`、跑 `task-activator.py`。所以那个目录必须同时有这两样。
demo 的做法是：`FLEET_SCRIPTS_DIR` 指向 `FLEET_HOME`，再把 `task-activator.py` 软链过去。

## 六、环境变量

必填，**没有默认值**（默认值会让脚本在别人的机器上安静地读写错地方，报错是最便宜的结果）：

| 变量 | 含义 |
|---|---|
| `FLEET_HOME` | 舰队工作目录。缺了每个脚本都会打印一句人话然后退出 |

其余都有默认值：

| 变量 | 默认 | 用在哪 |
|---|---|---|
| `FLEET_RECEIPT_DIR` | `$FLEET_HOME/receipts` | 回执目录（`fake-crew.sh` 写、`receipt-watch.sh` 看） |
| `FLEET_ASK_INBOX` | `$FLEET_HOME/ask-inbox.jsonl` | 答复收件箱（`ask-cli.py`） |
| `ACTIVATOR_JSON` | `$FLEET_HOME/task-activator.json` | 激活器数据文件，透传给 `task-activator.py` |
| `QS_ACTIVATOR_PY` | `<仓根>/scripts/task-activator.py` | 激活器脚本路径（`ask-cli.py`） |
| `QS_TMUX_SOCKET` | 空（用 tmux 默认 socket） | `tmux -L` 的 socket 名 |
| `QS_IDLE_RE` | `(❯｜›｜»｜$｜#｜>)` 结尾 | 判「输入行空闲」的 ERE |
| `QS_IDLE_TIMEOUT` | `30` | 等对方空闲的秒数上限 |
| `QS_WATCH_INTERVAL` | `1` | 回执监听轮询间隔（秒） |
| `QS_CREW_NAME` / `QS_CREW_PROMPT` / `QS_CREW_WORK_SECONDS` | `crew-a1` / `crew> ` / `2` | 假舰员的代号、提示符、模拟耗时 |
| `QS_KEEP` | `0` | `1` = demo 跑完保留临时目录 |
| `QS_DEMO_TMPDIR` | `$TMPDIR` | 临时舰队目录建在哪 |

其余变量（IM 接力、看门狗等）见 `scripts/README-env.md`。

## 七、怎么自证这套东西是活的

规程里最要紧的一条：**判据必须答得出「什么情况下它会红」**。这里的都能答：

```bash
# 反例：关键词不是正文子串 → 连终端都不碰，直接拒绝
bash quickstart/send-to-tmux.sh --dry-run %0 "/path/CS-DEMO01-任务书.md" "不在正文的词"
# → KW_NOT_IN_MSG_ABORT，退出码 3

# 正例锚：同一个 pane、同一条正文，只把关键词换成真子串
bash quickstart/send-to-tmux.sh %0 "/path/CS-DEMO01-任务书.md" "CS-DEMO01"
# → DELIVERED，退出码 0
```

反例和正例锚要成对跑：只看见「红了」证明不了什么，红可能是环境坏了；
同一环境下换掉那半条判据就变绿，才说明红是判据引起的。
demo 内部也照这个来——第 9 步先断言「提问后任务确实被标成卡口径」，
第 13 步再断言「答复后那个标记消失」；两态都验过，这条判据才不是摆设。

## 八、已知边界（故意不做的）

- **投递只支持单行正文**。多行会被 `send-keys` 当回车提前提交，所以直接拒绝。
  正式做法本来就是「正文给任务书路径，不贴任务书全文」。
- **回执监听只做 1 秒轮询**。`fswatch`/`inotify` 更省电，但那条分支在没装 fswatch 的机器上
  跑不到、也就验不了；宁可留一条能验的路径，也不留一条「看起来支持、实际没人跑过」的分支。
  要换：把 `receipt-watch.sh` 里的 `sleep` 换成 `fswatch -1 "$DIR"`，扫描逻辑不用动。
- **不含并线与部署**（README「一张单的生命周期」⑦⑧步）：那两步要真仓库、真服务，不适合放进 demo。
- **假舰员不读任务书正文、不校验白名单**。它只演示契约，不演示施工纪律。
- demo **总是自己建临时 `FLEET_HOME`**，不会用你环境里的那个——演示任务串进真实舰队的数据里就是污染。

踩过的两个跨平台坑，抄这些脚本时注意：

- macOS 自带 `bash` 是 3.2，**没有关联数组**（`declare -A` 直接语法报错）。
- 在中文 locale 下，bash 3.2 会把紧跟在变量名后面的多字节字符算进变量名里
  （`"$SESSION（socket）"` → 报 `SESSION…: unbound variable`）。变量后面接中文一律写 `${VAR}`。

---

## English

**What this is.** StarFix is a playbook for one long-lived AI "captain" session commanding a
fleet of terminal AI workers. The full setup leans on iTerm2, a native ask-panel and an IM relay.
This folder is the **minimum runnable version**: with only `tmux` and `python3` you can run the
whole loop end to end in about fifteen seconds and see every moving part.

```bash
bash quickstart/demo.sh        # macOS or Linux; add QS_KEEP=1 to keep the temp dir
```

**The loop.** dispatch → two-step delivery → worker builds → receipt lands → monitor fires →
captain accepts → question raised → decision panel → owner answers → inbox → task unblocked.

**The files.**

| file | stands in for | note |
|---|---|---|
| `send-to-tmux.sh` | `scripts/send-to-session.sh` (iTerm2) | same two-step protocol, four primitives swapped to `tmux capture-pane` / `send-keys` |
| `fake-crew.sh` | a real Codex / Claude terminal session | only the "do the work" step is faked (`sleep 2`); the contract is identical |
| `receipt-watch.sh` | the receipt monitor in `specs/monitors.md` | 1s polling fallback |
| `ask-cli.py` | the macOS ask-panel / IM relay | the inbox contract (one appended JSON line) is the real one |
| `demo.sh` | `skill/SKILL.md` §1 plus the task lifecycle | fifteen visible steps, no merge/deploy |
| — | `scripts/task-activator.py`, `scripts/ask-inbox-apply.sh` | reused as-is, no logic copied |

**The one rule that matters most.** Never blind-press Enter in someone else's terminal.
Write the text, read the input line back, and only if your own keyword is sitting there do you
send the newline. The keyword must be a literal substring of the message — otherwise the
read-back can never match and the same task queues up forever. Rather not deliver than misdeliver.

**Requirements.** `tmux`, `python3` (stdlib only), `bash` 3.2+. No network, no accounts,
no iTerm2. `FLEET_HOME` is required and has **no default**: a default would silently read and
write in the wrong place on someone else's machine, and an error message is the cheapest outcome.
