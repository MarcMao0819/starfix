# 牵星 · StarFix

**语言 / Language：** [中文](README.md) · [English](README.en.md)

> 郑和舰队靠牵星板测星定位；AI 舰队靠事实定位。

**一个长期在线的 AI 会话（舰长）指挥一支终端 AI 舰队：派单、验收、并线、部署、对接业务人员、离席接力，并把判断沉淀成可考核的规程。** 这不是框架代码，是在真实项目里连续跑过数周的作战规程 + 支撑脚本。

## 5 分钟跑起来

只需 tmux + python3：

```bash
export FLEET_HOME=/tmp/fleet
bash quickstart/demo.sh
```

15 步闭环：派单 → 投递 → 承建 → 回执 → 验收 → 请示 → 答复 → 解锁。教程：[quickstart/README.md](quickstart/README.md)。

## 它长什么样

![一张单的生命周期](docs/img/02-task-lifecycle.svg)

![决策闭环](docs/img/03-decision-loop.svg)

全部机制（角色、感官、上下文交接、轨迹编译、一天的时间线）见 [docs/mechanism.md](docs/mechanism.md)。

## 舰长 Benchmark · 三模型实测

同一题库（captain-v1，157 检查点）、同一考官、四层全测：

| 模型 | 平台 | 综合 | K1 – K9 | 结论 |
|---|---|---:|---|---|
| GPT-6 Astra | Codex CLI 0.153.4 · 原生隔离会话 | **85.6** | 88 / 81 / 96 / 100 / 57 / 83 / 83 / 87 / 97 | 未通过（否决未命中） |
| grok-4.6 | Grok CLI 1.0.25 · headless | **82.2** | 82 / 60 / 96 / 92 / 62 / 75 / 100 / 91 / 97 | 未通过（否决未命中） |
| DeepSeek V4.1 Flash | Grok CLI 1.0.25 / provider: deepseek-flash | **76.2** | 74 / 62 / 79 / 88 / 55 / 100 / 75 / 91 / 78 | 未通过（否决未命中） |

![九项能力](docs/img/bench/bench-abilities.svg)

![综合分与四层](docs/img/bench/bench-overall.svg)

综合分不是唯一指标：九项能力（K1 角色纪律 20%、K2 舰员画像与派单 15%、K3 工具缺失自恢复 10%、K4 任务书质量 10%、K5 长时运转与交接 10%、K6–K9 见题库）分别打分；困难版阈值为综合 ≥90、K1 ≥95、其余 ≥85，三者目前均未通过。Grok 4.6 与 GPT-6 Astra 为 S04 模拟器误拒校准版（原始分保留）。完整交互结果页（排行榜、雷达图、逐模型评审卡、失分明细）：**https://marcmao0819.github.io/starfix/bench/** （源码 `benchmarks/captain-v1/report/`，一条命令重生成）。原始记录、评审摘要与校准说明：[`benchmarks/captain-v1/results/records/`](benchmarks/captain-v1/results/records/)；出题与评分方法：[`benchmarks/captain-v1/README.md`](benchmarks/captain-v1/README.md)。

## 从哪里读

| 想知道 | 去这里 |
|---|---|
| 舰长为什么能自决、哪三类事必须上升 | [doctrine/00 舰长之魂](doctrine/00-README.md) |
| 上任第一动作与全部规程 | [skill/SKILL.md](skill/SKILL.md) |
| 舰队为什么优于「agent 自己开子代理」；轨迹编译省多少 token | [docs/why.md](docs/why.md) |
| 监听 / 终端投递 / 任务激活器 / 离席 / 记忆 / 上下文交接 | [specs/](specs/) |
| 判断怎么错过、工具怎么骗人 | [doctrine/02 战例集](doctrine/02-试错战例集.md) · [doctrine/04 踩坑指南](doctrine/04-踩坑指南.md) |
| 舰长该怎么考 | [doctrine/03](doctrine/03-舰长考核与benchmark方向.md) · [benchmarks/captain-v1](benchmarks/captain-v1/README.md) |
| 机器替舰长审流程 | [docs/trajectory.md](docs/trajectory.md) · [trajectory/](trajectory/) |
| 脚本与环境变量 | [scripts/README-env.md](scripts/README-env.md) |
| 术语中英对照 | [docs/glossary.md](docs/glossary.md) |

## 适用人群

- **驻场交付工程师（FDE）**：一个人带一支 AI 舰队在客户现场并行推多条线，要派单、验收、对接客户各岗位、把决策送到客户老板手里、留下可审计的痕迹、随时能交接。
- 独立开发者与小团队技术负责人：想让多个 AI 会话长期协作而不失控。

## 六条原则

1. 舰长只做判断，不写业务代码；舰员失败是常态，舰长自伤才是事故。
2. 回执不是事实，实物才是；每条判据都要答得出「什么情况下它会红」。
3. 只在三类事上停下问指挥官：不可逆生产面、只有人类知道的业务事实、对外发布。
4. 所有待决问题进决策面板，答复即解锁任务。
5. 监听只报危险侧、只报状态跃迁、不轮询。
6. 教训当天变成战例和门禁，过夜就丢。

## 分享前

`tools/scrub-gate.sh` 必须绿（词表与盐在仓外，缺词表自动降级只跑结构型判据）。

## 作者

**三娃老爸**（GitHub MarcMao0819，小红书 三娃老爸 · 294613559）。这套机制、规程和每一条判断都是他在真实项目里定下来的；舰长（Claude 会话）在他指挥下执行搬运、脱敏、绘图与提交。有问题可以来问，觉得有用求个关注。

MIT，见 [LICENSE](LICENSE)。
