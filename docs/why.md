# 为什么是舰队，不是子代理；为什么要轨迹编译

**语言 / Language：** [中文](why.md) · [English](why.en.md)

## 一、StarFix 舰队 vs agent 自己开子代理

「一个 agent 自己 spawn 子代理」是当下最常见的多代理形态：父代理派一个短命子代理去做一件事，结果回到父上下文。它适合单次检索、一次性小任务。StarFix 解决的是另一类问题：**一个项目要连续跑几周、几十条线并行、换人换模型、还要让人随时能接管**。差异在八个地方：

| 维度 | 子代理（父 spawn） | StarFix 舰队 | 为什么重要 |
|---|---|---|---|
| 生命周期 | 短命，随父轮次结束 | 舰员是常驻会话，能跑几小时、跨天，自己压缩上下文 | 真实开发单常常 2–4 小时；子代理跑不完就断 |
| 上下文 | 继承或共享父上下文，结果只回父 | 每个舰员独立上下文；事实靠**回执文件 + 激活器**外置 | 父上下文一压缩，子代理的过程就没了；文件不会 |
| 模型与 harness | 只能用父的模型/额度/工具 | 混编：Codex 承建、Claude 复审、无头工人机跑批；按画像路由 | 跨模型互审才是独立审查；同模型自审=自己审自己；额度可分摊 |
| 隔离 | 共享父的 cwd/权限/凭据 | 每单独立 worktree、一次性库、独立进程、只读原件 | 子代理串写父目录是真实事故来源 |
| 可见与可接管 | 人看不见子代理在做什么 | 舰员就是一个终端窗口，人随时能看、能打断、能接手 | Owner 不用信任「黑盒」；出事能人肉兜底 |
| 事实与审计 | 父代理转述子代理的结论 | 回执三态落盘、登记入库、门禁机检；「回执不是事实，实物才是」 | 转述是所有假绿的温床 |
| 身份与成长 | 即用即弃，没有画像 | 舰员有长期代号、画像（擅长/失败模式/吞吐）、评价四数 | 派单精度来自画像，不来自印象 |
| 指挥者本身 | 父代理既做又管，容易「图快自己干」 | 舰长只做判断：派单/复审/验收/并线/部署；自己干活=违例 | 判断与执行分离，判断才不会被执行的沉没成本绑架 |

一句话：子代理是**函数调用**，舰队是**组织**。函数调用回一个值；组织留下的是回执、登记、画像、战例，换掉任何一个人（包括舰长）都还在。

代价也要写清楚：舰队需要终端自动化、监听、激活器这些基建；启动成本比 spawn 一个子代理高一个数量级。项目不到一天、单线、不换模型，用子代理就够。

## 二、轨迹编译（Trajectory Compiler）的优势

轨迹编译把「任务书 → 承建 → 回执 → 登记 → 并线 → 部署」这条**流程轨迹**编译成机器可检查的图，由轮询器每分钟跑一组检查（p1–p7：回执格式与三态、行尾双规则、契约基线、登记与第一父链、部署漂移、秘密扫描、路由存活），告警落盘、按祖先判据自清，归纳器再从历史轨迹里长出新检查。

它和普通 CI 的区别：CI 检查**代码**对不对；轨迹编译检查**流程**有没有闭合——回执到了没登记、登记了没并线、并线了没部署、部署了版本真相没更新、告警发了没人处置。这些都不是代码错误，却是舰队里最常见的失效。

优势五条，每条都对应一类真实事故：

1. **把「记得」变成「跑一下会红」**。规程里写过的坑一定复发（双构造函数缺注解、行尾混用、关键词不在正文）；写成检查项后，复发在一分钟内被机器抓住，不靠任何人记得。
2. **舰长也会假绿，机检审舰长**。漂移告警会指出「集成头已推进但生产没跟上」；登记门会指出「分支第一父链落到旧头」——这些都是舰长亲手做并线部署时容易自我放行的地方。
3. **告警有生命周期**。每条告警带处置段和自清判据（head 已包含在 LIVE 中即清），不会越积越多变成没人看的噪音；「先报警后并线」的时序也能验证。
4. **回执机检卸掉舰长的阅读负担**。三态缺失、证据段为空、关键词不匹配、文件路径不存在，机器先筛一遍，舰长只读通过筛子的。
5. **自成长**。归纳器从「这次是怎么漏的」归纳新检查（例如「零变化断言必须先证明被测对象出现过」），检查集随战例增长；证据留档满足「重跑证现在、留档证当时」。

6. **省 token，而且越跑越省**。稳定判点（10 轮观测 10/10 一致、无反例）编译成机械步骤后**零模型 token**：登记哈希、三方文件数、清单一致、行尾、漂移这些每张单都要查的事，由 SQL/git/compare 直接出 verdict；模型只在归纳器标为 `llm` 的异常分支上花 token，并且只拿到该分支的上下文而不是整张单的 diff。观测样本里 p3 的 10 轮只有 2 轮进模型分支，其余 8 轮零 token。对比「每张单都让模型重审一遍」：模型审要读完整 diff 与登记，token 随文件数线性增长；轨迹编译把这部分固定成脚本，随判点稳定度提高，模型分支还在继续收窄。

**实测数字（本机，变更单核对流程）**：编译前每单约 1.2 万 token、分钟级（模型逐步查 SQL/git 再判）；编译成图后**常规单 0 次模型调用、0.6 秒**，48 轮真实运行零静默错误；并线前门禁图 **1.2 秒 / 0 调用**。立项时的止损线是「模型调用/token 降 ≥30%」，实测常规单降 100%，只有异常分支才回到模型。折算：每 100 张常规单省约 120 万 token（按每单 1.2 万计）。**别误解**：值钱的是「确定性图 + 岔路口才调模型」，不是把图编译成二进制——图的解释开销近似为零。

代价：需要一份轨迹契约（回执/登记/并线/部署各自的机器可读锚点），第一次接入要把这些锚点补齐；检查项自身也要过判别力测试（它红过吗），否则轨迹编译会变成另一个永远绿的仪表盘。

---

## English summary

**Fleet vs. spawned sub-agents.** A spawned sub-agent is a function call: short-lived, sharing the parent's context, model, quota, cwd and permissions, invisible to humans, and its result survives only as the parent's paraphrase. StarFix treats workers as an organization: long-lived terminal sessions with independent context, mixed models/harnesses routed by profile, per-task isolation (worktree, throwaway DB, separate process), human-visible and human-takeover-able, with facts externalized into receipts, registrations and profiles — and a captain that only judges (dispatch/review/accept/merge/deploy) and never "just does it quickly". The cost is infrastructure; for a one-day single-thread task, a sub-agent is enough.

**Trajectory Compiler.** It compiles the *process* trajectory (task book → build → receipt → registration → merge → deploy) into a machine-checkable graph and polls it every minute (receipt format, line endings, contract baseline, registration/first-parent, deploy drift, secret scan, route liveness). Unlike CI, which checks code, it checks whether the process closed — and it audits the captain too. Lessons become checks that "go red in a minute" instead of rules someone has to remember; alerts carry a lifecycle and self-clear; an inducer grows new checks from past failures.
