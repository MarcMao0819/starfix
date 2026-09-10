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
