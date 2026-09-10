#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill → workflow 编排器的承重层：切段 / 指纹 / 变更侦测 / 结构校验。

## 第一性原则：意义由引用决定，不由形态决定

**一个令牌有没有意义，取决于有没有守卫引用它**——不取决于它长什么样、它所在的块有没有
if/else 结构、它是不是全大写。

这条是踩出来的。序幕块无条件回显 18 个状态标签（BRANCH/TELEMETRY/LEARNINGS…），被误判
成分支。我第一次的修法是**猜结构**：「必须有 if/else 且令牌数 2-4」。它修好了序幕，却把
单令牌分类器（`echo "NEEDS_SETUP"`）判成普通命令，**导致守卫永不成立，把已经通过的执行期
夹具打回了**——是跑回归才发现的。

猜结构错在方向：形态是表象，**引用关系才是事实**。改成「只保留被某个守卫引用的令牌」之后，
两个问题一起消失，且不需要任何关于「分支长什么样」的假设。

推广开来：凡是要判断某个提取物「是不是重要的」，先找**谁在用它**，不要看它像什么。

## 同源纪律：比该比的（分歧判据）

金丝雀比对**只比决策不比表象**：比块级状态序列、守卫决策、人闸停顿点；不比耗时、输出正文、
错误文本。后者本来就会变，比了必然天天报，而**一个天天报的警报器会教会所有人忽略警报**。

这与 <日期> 漂移重排队的告警降噪是同一条纪律：当时 11 条无行动价值的告警淹没了真正
有行动价值的「登记不自洽」，那不只是吵，是信号路由错位。

## 这是干什么的

今天 skill 的用法是：模型每次调用都把整份文本读一遍，再决定怎么做。实测手上的
skill 有 900–3000 行，`ship` 一份就 3057 行。而其中真正确定性的可执行步骤只占
25%–51%（按行计）——**模型为了执行那三分之一，每次都要读完全部**。

编排器要做的是把确定性步骤编译成可直接执行的图，判断点仍留给模型。所以省下的
不是「那 33% 的行」，而是「为了执行 33% 而读完 100% 」这件事本身；判断点用到的
散文改成**按需引用**（节点只存行号区间，触发时才取那一段）。

## 承重的设计决定：节点必须记来源（provenance）

每个节点记住自己是从 skill 原文的哪一节、哪几行编译来的。这一条是**增量更新能否
成立的全部前提**——没有它，skill 文本一改就只能整份重编。

## 三态，不许只有两态

    draft        刚编译出来，**不投用**
    active       验证过，正常调用走它，原文作为回退
    invalidated  原文指纹变了，自动退回读原文，并排队重编

最坏情况退化成今天的行为（读文本执行），**不会错**。这是本设计的安全底线：
编译产物永远只是快路径，原文永远是权威。

## 为什么变更侦测要按「节」而不是按整份文件

整份哈希一变就全废，等于每改一个错别字就重编三千行。按节哈希才能定位到「哪几个
节点受影响」，只重编那几个。

节的匹配**不能靠行号**——插入几行会让后面所有行号平移。匹配靠「标题文本 + 层级」，
行号是匹配之后才重算的。

## 已知结构缺陷：skill 不一定是线性流程（<日期> 拿真 skill 实测发现）

本编译器把节点拉成**平铺列表**，隐含假设「skill 是一条从头走到尾的流程」。实测
`browse` 推翻了这个假设——它的真实结构是三段：

    序幕   每次必跑的样板（前置检查 / 同步 / SETUP）      6 节点 / 242 行
    目录   **配方菜单，按任务选其中一条用，不是顺序执行**  22 节点 / 466 行
    尾声   每次必跑的收尾（遥测 / 自改进留痕）            2 节点 / 39 行

平铺列表表达不了「目录里选一个」这件事。正确的形态应是：序幕线性 → **一个判断点
决定选哪条配方** → 该配方（参数绑定后即确定性）→ 尾声线性。

**这个缺陷不影响价值判断，反而让它更清楚**：序幕+尾声共 281 行（全文 31%）是
**每次调用都要跑的纯样板**，而今天为了执行这 281 行，模型要读完 911 行。这部分是
编译的必赢区；配方目录是选择性的，省的是「不必读那 21 条用不上的配方」。

未修，因为要先由对拍实验判定整条链成不成立——先证明能省，再谈怎么把结构表达对。

## 与既有硬约定的对应

- 约定 6（零反例不许上线）→ `validate()` 里 tool 节点必须有可执行命令，**摘要式占位符
  一律拒收**。归纳器把摘要当命令原文产出「看着对、实际跑不了」的伪代码，是真出过的事故。
- 约定 11（含或分支必须逐分支取证）→ branch 节点必须枚举全部出口且含失败支，
  只有 PASS 没有 FAIL 的分支判据会把判决悄悄交给模型。
- 约定 12（判决绑输入指纹）→ 图绑 skill 原文指纹，原文一改判决即失效。
  这里的「判决」就是编译产物本身。

只用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

SCHEMA = "skill-workflow/v1"

# 可执行围栏的语言标记；空标记按可执行处理（skill 里大量裸 ``` 块是 shell）
EXEC_LANGS = {"", "bash", "sh", "shell", "zsh", "console"}

# 判断点信号：命中即认为该节含需要模型裁量的内容
JUDGMENT_RE = re.compile(
    r"AskUserQuestion|问用户|请示|由你定|你决定|由你判断|taste|judgment|"
    r"视情况|酌情|自行判断", re.I)

# 占位符启发式。**只作提示，不作判据**——真数据实测原版 `<[^>]{2,40}>` 会把 shell 的
# 输入重定向（`wc -l < "$f" 2>…`）和 heredoc 里的 HTML（`<div class="tweet">`）全判成占位符。
# 收紧成两种真正的元变量写法：无空格的 `<metavar>`、纯小写词组的 `<some description here>`。
# 两者都要求 `<` 后紧跟小写字母，从而天然避开 `< "file"` 这种重定向。
PLACEHOLDER_RE = re.compile(
    r"<[a-z][a-z0-9_\-]{1,30}>"          # <sel> <choice> <changeset-no>
    r"|<[a-z][a-z ]{3,50}>"               # <concise description of what changed>
    r"|\{\{[^}]+\}\}"
    r"|＜[^＞]+＞")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 切段
# ---------------------------------------------------------------------------

def sectionize(text: str) -> list[dict]:
    """按 markdown 标题切段，**围栏内的 `#` 行不算标题**。

    为什么要围栏消隐：skill 大量引用命令原始输出，输出里 `## HEAD`、`## warnings`
    这类行以 `#` 开头却不是文档结构。同款缺陷在回执判据上真实误伤过（切段提前收尾，
    节内的锚点被切在外面，判据据此误判）。
    """
    lines = text.split("\n")
    marks: list[tuple[int, int, str]] = []   # (行号0基, 层级, 标题文本)
    in_fence = False
    for i, l in enumerate(lines):
        if l.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", l)
        if m:
            marks.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[dict] = []
    if not marks or marks[0][0] > 0:
        sections.append({"level": 0, "heading": "(前言)", "line_start": 0,
                         "line_end": (marks[0][0] - 1) if marks else len(lines) - 1})
    for idx, (ln, lvl, head) in enumerate(marks):
        end = (marks[idx + 1][0] - 1) if idx + 1 < len(marks) else len(lines) - 1
        sections.append({"level": lvl, "heading": head, "line_start": ln, "line_end": end})

    for k, s in enumerate(sections):
        body = "\n".join(lines[s["line_start"]:s["line_end"] + 1])
        s["id"] = "s%d" % (k + 1)
        s["sha256"] = sha(body)
        s["lines"] = s["line_end"] - s["line_start"] + 1
        s.update(measure(body))
    return sections


def measure(body: str) -> dict:
    """量一节里有多少可执行块、多少判断点。用于决定该节编译成什么类型的节点。"""
    exec_lines = judgment_hits = 0
    in_fence = False
    lang = ""
    for l in body.split("\n"):
        st = l.strip()
        if st.startswith("```"):
            if not in_fence:
                in_fence, lang = True, st[3:].strip().lower()
            else:
                in_fence, lang = False, ""
            continue
        if in_fence:
            if lang in EXEC_LANGS:
                exec_lines += 1
            continue
        if JUDGMENT_RE.search(l):
            judgment_hits += 1
    return {"exec_lines": exec_lines, "judgment_hits": judgment_hits}


def extract_commands(body: str) -> list[str]:
    """取一节里的可执行围栏原文。**整段取，不做摘要**——摘要就是约定 6 的事故来源。"""
    out: list[str] = []
    cur: list[str] = []
    in_fence = False
    lang = ""
    for l in body.split("\n"):
        st = l.strip()
        if st.startswith("```"):
            if not in_fence:
                in_fence, lang, cur = True, st[3:].strip().lower(), []
            else:
                if lang in EXEC_LANGS and cur:
                    out.append("\n".join(cur))
                in_fence, lang = False, ""
            continue
        if in_fence:
            cur.append(l)
    return out



# ---------------------------------------------------------------------------
# 控制流提取（<日期> 立，对拍实验实证后补）
# ---------------------------------------------------------------------------
#
# 对拍实证的教训：把围栏拉成平铺列表会**丢掉块之间的控制流**。browse 的 SETUP 段
# 里 n10/n11 是 if/else——n10 回显 READY 就意味着不必执行 n11 的装 bun 块。编译成
# 顺序后，B 组无条件跑了那个有害块，装 bun 失败后连 goto 都炸了。
#
# 更严重的是：守卫底下第一步写着「Tell the user ... Then STOP and wait」——**那是
# 一道人闸**。平铺执行不但多跑了块，还直接跨过了要求停下等用户批准的关卡。
#
# 原文里控制流的三个机械信号（browse SETUP 段实证）：
#
#     ```bash
#     ... echo "READY: $B" ... echo "NEEDS_SETUP" ...     ← 回显离散令牌 = 分支节点
#     ```
#     If `NEEDS_SETUP`:                                    ← 散文守卫，开作用域
#     1. Tell the user: "..." Then STOP and wait.           ← 人闸
#     3. If `bun` is not installed:
#        ```bash                                           ← **缩进**在守卫下 = 继承守卫
#        ...
#        ```
#
# 缩进是承重信号，原实现完全忽略了它。

# 分支：围栏里 echo 出的字面令牌（形如 echo "READY: $x" / echo 'NEEDS_SETUP'）
ECHO_TOKEN_RE = re.compile(r"""echo\s+["']([A-Z][A-Z0-9_]{2,30})(?::|["'\s])""")

# 守卫：`If \`TOKEN\`:` / `If TOKEN:` / `若 X：` / `如果 X：`
GUARD_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:If|IF|若|如果|当)\s+[`\u2018\u2019\u201c\u201d]?"
    # [<日期> 修] 量词由 {1,40} 改 {0,40}：原版最少要求两个字符，
    # 单字母守卫 `If A:` / `If B:`（用户选项分支）匹配不上，导致其作用域内的
    # 人闸变成"无守卫"，执行器每次都停在那里。而 A/B 是人的选择，机械上永不成立，
    # 正确行为是**跳过**该分支，不是停住。
    r"([A-Za-z_][A-Za-z0-9_ \-]{0,40})[`\u2018\u2019\u201c\u201d]?\s*[:：]")

# 守卫第二形态（<日期> codex 实证）：令牌嵌在句中、冒号在句尾。
#   `If the output contains ` + 反引号 + `AUTH_FAILED` + 反引号 + `, stop and tell the user:`
# 与第一形态是**同一个概念的不同写法**，不是新形态：守卫仍然是「某个被回显过的令牌」。
# 按第一性原则处理——**认令牌不认句式**：句中出现反引号包裹的全大写令牌即为候选，
# 最终是否成立由「守卫相关性过滤」用引用关系裁决（该令牌必须真被某个块回显）。
GUARD_INLINE_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:If|IF|若|如果|当)\b[^\n]*?"
    r"[`\u2018\u201c]([A-Z][A-Z0-9_]{2,30})[`\u2019\u201d]")

# 人闸：要求停下等人的措辞。**命中即不许自动执行**——跨过人闸比多跑一个块严重得多。
HUMAN_GATE_RE = re.compile(
    # [<日期> 修] 补 AskUserQuestion / BLOCKED / STOP 等形态。
    # **原版漏检严重**：codex 必跑段内有 7 处 AskUserQuestion，全是「停下问人」的点，
    # 执行器一路跑了过去；其中一处原文明写「若不可用则 BLOCKED，停下报告」。
    # 漏检人闸比多跑一个块严重得多——它是**未经授权就替人做了决定**。
    # 注意 "ask the user" 匹配不到 "AskUserQuestion"（无空格），必须单列。
    # 裸 STOP / BLOCKED 已撤：那是同一次修改里我自己的过度扩张，把「状态定义说明」
    # （- **BLOCKED** — cannot proceed）和「语义说明」都判成了人闸。
    r"AskUserQuestion|STOP and wait|Then STOP|stop-gate|"
    r"ask the user|Tell the user|wait for (?:the )?user|"
    r"停下等|等待用户|征得同意|请示后|需用户确认|需人工确认|交人工|转人工", re.I)

# 「无论选哪个都要跑」的显式标记。原文里明确写着（browse 行 138/176/194/259）：
#   Always run (regardless of choice):
# 它是**原文自己给出的判据**——用户拒绝某道闸之后，这类块仍然要跑（多为落标记文件、
# 清理临时状态），不属于被拒绝的动作。有它才能在「拒绝即跳过后续」时不误伤。
ALWAYS_RUN_RE = re.compile(r"Always run|无论选哪个|regardless of choice|不论选择", re.I)

# 人闸的**排除式**：命中下列形态即便含 AskUserQuestion 也不是人闸。
# 依据仍是第一性原则——看它是**指令**还是**描述**：
#   `- AskUserQuestion`            工具清单条目，无动词
#   `Do NOT use AskUserQuestion`   明令不要用
#   `... subordinate to ... AskUserQuestion gates ...`  语义说明
HUMAN_GATE_EXCLUDE_RE = re.compile(
    r"^\s*[-*]\s*`?AskUserQuestion`?\s*$"          # 裸列表项
    r"|Do NOT use|不要使用|禁用|不得使用"
    r"|subordinate to|takes precedence|satisfies ", re.I)


def extract_blocks(text: str) -> list[dict]:
    """按行扫出围栏块，**带缩进与守卫作用域**。

    守卫作用域规则（机械，不猜语义）：
      - 一行命中 GUARD_RE 时，以该行缩进为基准开一个作用域
      - 后续缩进**严格大于**基准的内容属于该作用域
      - 缩进回到基准或更浅时作用域关闭
    嵌套守卫按栈叠加（全部成立才执行）。
    """
    lines = text.split("\n")
    blocks: list[dict] = []
    stack: list[dict] = []          # 守卫栈: {indent, cond}
    in_fence = False
    lang = ""
    cur: list[str] = []
    start = 0
    fence_indent = 0
    pending_gate = None
    always_run = False          # 最近一次 Always run 标记是否仍然有效

    for i, l in enumerate(lines):
        st = l.strip()
        indent = len(l) - len(l.lstrip())

        if not in_fence:
            # 关闭守卫作用域。
            # [<日期> 修] 原规则「缩进 <= 基准即关闭」是错的：markdown 里守卫底下
            # 的内容常常是**同缩进的列表项**（`If X:` 在缩进 0，紧跟的 `1. Tell the user`
            # 也在缩进 0），于是守卫在下一行就被弹掉，实测 browse 的 NEEDS_SETUP 守卫
            # 一次都没生效。改成：守卫延续到 ①markdown 标题 ②同级或更浅的新守卫
            # ③缩进不深于基准、且**不是列表项/续行**的顶格散文 —— 才算出了作用域。
            if st and stack:
                is_heading = st.startswith("#")
                is_list = bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s", l))
                is_guard = bool(GUARD_RE.match(l))
                while stack and (
                        is_heading
                        or (indent <= stack[-1]["indent"] and is_guard)
                        or (indent <= stack[-1]["indent"] and not is_list)):
                    stack.pop()
            if ALWAYS_RUN_RE.search(l):
                always_run = True
            elif st and not st.startswith("```") and re.match(r"^#{1,6}\s", l):
                always_run = False       # 新标题即失效
            g = GUARD_RE.match(l)
            if not g:
                g = GUARD_INLINE_RE.match(l)
            if g:
                cond = g.group(1).strip()
                stack.append({"indent": indent, "cond": cond})
                # 第二形态常是「命中即停」的单行守卫（If 输出含 X, stop and tell...），
                # 守卫与人闸在同一行。此时要把这一行本身也当人闸登记，否则守卫开了
                # 作用域却没有任何东西落在里面。
                if HUMAN_GATE_RE.search(l) and not HUMAN_GATE_EXCLUDE_RE.search(l):
                    blocks.append({"kind": "human_gate", "text": st[:200],
                                   "line": i + 1, "guards": [x["cond"] for x in stack]})
                continue
            if st and HUMAN_GATE_RE.search(l) and not HUMAN_GATE_EXCLUDE_RE.search(l):
                pending_gate = {"text": st[:200], "line": i + 1,
                                "guards": [x["cond"] for x in stack]}
                blocks.append({"kind": "human_gate", **pending_gate})
                continue

        if st.startswith("```"):
            if not in_fence:
                in_fence, lang, cur, start, fence_indent = True, st[3:].strip().lower(), [], i, indent
            else:
                if lang in EXEC_LANGS and cur:
                    body = "\n".join(cur)
                    toks = sorted(set(ECHO_TOKEN_RE.findall(body)))
                    # 定型延后到全文扫完（见函数末尾的守卫相关性过滤）。
                    # [<日期> 修二] 上一版判据「必须有 if/else + 令牌数 2-4」是**猜结构**，
                    # 它把单令牌分类器（`echo "NEEDS_SETUP"`）判成普通命令，导致守卫永不成立，
                    # 打回了已过的执行期夹具（守卫为真→必须执行，实测退化成跳过）。
                    # 正确规则不用猜结构：**一个令牌有没有意义，取决于有没有守卫引用它**。
                    # 序幕那 18 个状态标签没有任何守卫引用，天然无害；NEEDS_SETUP 被守卫
                    # 引用，就留下。数据驱动，两个问题一起解决。
                    # [已废] 只有「if/else 结构 + 少数互斥令牌」才算分支。
                    # 原判据「回显>=2个令牌即分支」把 browse 的 preamble 误判成分支：
                    # 那块无条件回显 18 个状态标签（BRANCH/TELEMETRY/LEARNINGS…），
                    # 它是状态输出不是分岔。误判的后果不只是标错——这些令牌会进
                    # 令牌池，**可能错误地满足后续守卫**，让本该跳过的块跑起来。
                    blocks.append({
                        "kind": "cmd",     # 定型延后
                        "cmd": body, "line_start": start + 1, "line_end": i + 1,
                        "indent": fence_indent,
                        "guards": [x["cond"] for x in stack],
                        "emits": toks,
                        "always_run": always_run,
                    })
                    always_run = False   # 标记只覆盖紧随其后的块
                in_fence, lang = False, ""
            continue
        if in_fence:
            cur.append(l)

    # 守卫相关性过滤：只有被某个守卫引用的令牌才留下，才算分支。
    guard_names = {g for b in blocks for g in b.get("guards", [])}
    for b in blocks:
        if b["kind"] == "human_gate":
            continue
        rel = [t for t in b.get("emits", []) if t in guard_names]
        b["emits"] = rel
        b["kind"] = "branch" if rel else "cmd"
    return blocks

# ---------------------------------------------------------------------------
# 编译（机械部分：不调模型）
# ---------------------------------------------------------------------------

def compile_skill(path: str) -> dict:
    """把 skill 原文编译成 draft 态的 workflow。

    机械部分只做能确定的事：切段、取命令、按信号给节点定型。真正需要语义判断的
    （某条命令的成功判据是什么、分支条件怎么写）留给后续的模型细化步骤，节点上
    以 `needs_refine` 标出——**没细化过的节点不许进 active 态**。
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    sections = sectionize(text)

    nodes: list[dict] = []
    for s in sections:
        body = "\n".join(lines[s["line_start"]:s["line_end"] + 1])
        cmds = extract_commands(body)
        if s["judgment_hits"] > 0:
            nodes.append({
                "id": "n%d" % (len(nodes) + 1), "type": "judgment",
                "from_section": s["id"], "from_lines": [s["line_start"] + 1, s["line_end"] + 1],
                "heading": s["heading"],
                # 散文不内联，只存引用：判断点触发时才按行号区间取原文
                "prose_ref": {"path": path, "lines": [s["line_start"] + 1, s["line_end"] + 1]},
                "needs_refine": True,
            })
        elif cmds:
            for j, c in enumerate(cmds):
                nodes.append({
                    # 机械层**只认到候选**：真数据里大量围栏是帮助文本/样例输出/配置示例，
                    # 光看围栏分不出「要执行的命令」和「展示的输出」。定型交给细化步骤，
                    # 且必须真跑过才准升 tool（约定 6 的本意是"真跑过才算数"，不是正则猜）。
                    "id": "n%d" % (len(nodes) + 1), "type": "candidate",
                    "from_section": s["id"], "from_lines": [s["line_start"] + 1, s["line_end"] + 1],
                    "heading": s["heading"], "cmd": c,
                    "output_contract": None,      # 待细化
                    "cases": None,                # 待细化
                    "needs_refine": True,
                })
        # 纯散文节不产出节点：它是判断点的资料，不是步骤

    return {
        "schema": SCHEMA,
        "skill": {"name": os.path.basename(os.path.dirname(path)) or os.path.basename(path),
                  "path": path, "source_sha256": sha(text), "source_lines": len(lines)},
        "sections": sections,
        "nodes": nodes,
        "state": "draft",
        "validation": {"structural": None, "dry_run": None},
        "revision_log": [{"ts": now_iso(), "event": "COMPILED", "note": "机械编译，节点待细化"}],
    }


# ---------------------------------------------------------------------------
# 结构校验（约定 6 / 11 的机器闸）
# ---------------------------------------------------------------------------

def validate(g: dict) -> dict:
    """结构校验。**这是闸不是提示**：不过就不许进 active 态。"""
    errs: list[str] = []
    warns: list[str] = []
    sec_ids = {s["id"] for s in g["sections"]}

    for n in g["nodes"]:
        nid = n["id"]
        if n.get("from_section") not in sec_ids:
            errs.append("%s 的来源节 %s 不存在（provenance 断链，增量更新会失准）"
                        % (nid, n.get("from_section")))
        if n["type"] == "candidate":
            # 候选还没定型，不构成错误；只提示占位符嫌疑供细化时优先看
            m = PLACEHOLDER_RE.search(n.get("cmd") or "")
            if m:
                warns.append("%s 候选疑含占位符 %r，细化时需确认是否可执行"
                             % (nid, m.group(0)[:40]))
        if n["type"] == "tool":
            cmd = (n.get("cmd") or "").strip()
            if not cmd:
                errs.append("%s 是 tool 节点却没有命令" % nid)
            elif PLACEHOLDER_RE.search(cmd):
                # 已定型为可执行却仍含占位符 → 这才是约定 6 的事故形态
                errs.append("%s 已定型为可执行却含占位符：%s"
                            % (nid, cmd.strip().split("\n")[0][:80]))
            if not n.get("smoke_ok"):
                errs.append("%s 未经真跑验证（smoke_ok 缺失），不许升 tool" % nid)
            if n.get("cases") is None:
                warns.append("%s 尚未细化出成功判据" % nid)
        if n["type"] == "branch":
            cases = n.get("cases") or {}
            # 约定 11：只有 PASS 没有失败支 → 失败会落 no_match 兜给模型，
            # 判决被模型随机性接管，且该分支永远取不到反例
            if not cases:
                errs.append("%s 是 branch 节点却没有枚举出口" % nid)
            elif not ({"FAIL", "FALSE", "NO"} & {str(k).upper() for k in cases}):
                errs.append("%s 只枚举了通过支、没有失败支（失败将被兜给模型，"
                            "该分支拿不到反例）" % nid)
        if n["type"] == "judgment":
            if not n.get("prose_ref"):
                errs.append("%s 是判断节点却没有原文引用，触发时无资料可取" % nid)

    unrefined = [n["id"] for n in g["nodes"] if n.get("needs_refine")]
    ok = not errs
    return {"ok": ok, "errors": errs, "warnings": warns,
            "unrefined": unrefined,
            "promotable": ok and not unrefined,
            "checked_at": now_iso()}


# ---------------------------------------------------------------------------
# 变更侦测与增量更新（机制二）
# ---------------------------------------------------------------------------

def match_sections(old: list[dict], new: list[dict]) -> dict:
    """跨版本匹配节。**按「层级+标题」匹配，不按行号**——插入几行会让后面所有行号平移。

    同名同级重复出现时按出现次序配对，避免把第 2 个「## 验证」错配到第 1 个上。
    """
    def key(s):
        return (s["level"], s["heading"])

    buckets: dict[tuple, list[dict]] = {}
    for s in old:
        buckets.setdefault(key(s), []).append(s)

    pairs, added = [], []
    used = set()
    for s in new:
        cand = buckets.get(key(s), [])
        hit = None
        for c in cand:
            if id(c) not in used:
                hit = c
                break
        if hit is None:
            added.append(s)
        else:
            used.add(id(hit))
            pairs.append((hit, s))
    removed = [s for s in old if id(s) not in used]
    return {"pairs": pairs, "added": added, "removed": removed}


def detect_changes(g: dict, current_path: str | None = None) -> dict:
    """比对编译产物与 skill 原文现状，算出哪些节变了、哪些节点因此受影响。"""
    path = current_path or g["skill"]["path"]
    text = open(path, encoding="utf-8", errors="replace").read()
    cur_sha = sha(text)
    if cur_sha == g["skill"]["source_sha256"]:
        return {"changed": False, "reason": "原文指纹未变", "source_sha256": cur_sha}

    new_sections = sectionize(text)
    m = match_sections(g["sections"], new_sections)
    modified = [(o, n) for o, n in m["pairs"] if o["sha256"] != n["sha256"]]

    dirty_secs = {o["id"] for o, _ in modified} | {s["id"] for s in m["removed"]}
    affected = [n["id"] for n in g["nodes"] if n.get("from_section") in dirty_secs]

    return {
        "changed": True,
        "source_sha256": cur_sha,
        "sections_modified": [{"id": o["id"], "heading": o["heading"]} for o, _ in modified],
        "sections_added": [{"heading": s["heading"], "level": s["level"]} for s in m["added"]],
        "sections_removed": [{"id": s["id"], "heading": s["heading"]} for s in m["removed"]],
        "sections_unchanged": len(m["pairs"]) - len(modified),
        "affected_nodes": affected,
        # 新增节要产出新节点，属于必须重编的部分
        "needs_new_nodes": len(m["added"]) > 0,
    }


def apply_update(g: dict, current_path: str | None = None) -> dict:
    """按变更内容更新编译产物。

    **不是整份重编**：未变的节，其节点原样保留（含已细化好的判据）；只有受影响的
    节点被打回 draft 待重编。这正是 provenance 存在的意义。

    但更新之后**整张图仍要过一遍结构校验**——局部改动可能破坏整体自洽（例如某节被
    删除，指向它的节点就成了断链）。局部重编 + 全局复验，是省成本又不牺牲正确性的
    唯一组合。
    """
    path = current_path or g["skill"]["path"]
    d = detect_changes(g, path)
    if not d["changed"]:
        return {"updated": False, "detect": d, "graph": g}

    text = open(path, encoding="utf-8", errors="replace").read()
    new_sections = sectionize(text)
    m = match_sections(g["sections"], new_sections)

    # 旧节 id → 新节（保持 id 稳定，避免节点 provenance 全部失配）
    id_map = {}
    for o, n in m["pairs"]:
        n["id"] = o["id"]
        id_map[o["id"]] = n
    next_no = len(g["sections"]) + 1
    for s in m["added"]:
        s["id"] = "s%d" % next_no
        next_no += 1

    removed_ids = {x["id"] for x in m["removed"]}
    # 被删节的节点是「删除」不是「打回」——两者互斥。
    # [<日期> 修] 原实现把 detect 算出的 affected_nodes 直接当 stale，而 affected
    # 本就包含被删节的节点，于是同一批节点同时出现在「打回待重编」和「已删除」两个
    # 清单里。台账这样记就是假的：一个节点不可能既要重编又已不存在。
    dropped_ids = {n["id"] for n in g["nodes"] if n.get("from_section") in removed_ids}
    dirty = set(d["affected_nodes"]) - dropped_ids
    kept, dropped = [], []
    for n in g["nodes"]:
        src = n.get("from_section")
        if src in removed_ids:
            dropped.append(n["id"])
            continue
        if n["id"] in dirty:
            n["needs_refine"] = True
            n["cases"] = None
            n["output_contract"] = None
            n["_stale"] = True          # 待重编标记
        # 行号一律按新版本重算（插入几行会让旧行号全部失准）
        if src in id_map:
            ns = id_map[src]
            n["from_lines"] = [ns["line_start"] + 1, ns["line_end"] + 1]
            if n["type"] == "judgment" and n.get("prose_ref"):
                n["prose_ref"]["lines"] = [ns["line_start"] + 1, ns["line_end"] + 1]
        kept.append(n)

    ordered = [id_map[s["id"]] if s["id"] in id_map else s
               for s in sorted(new_sections, key=lambda x: x["line_start"])]
    for a, b in zip(ordered, sorted(new_sections, key=lambda x: x["line_start"])):
        a.update({k: b[k] for k in ("line_start", "line_end", "sha256", "lines",
                                    "exec_lines", "judgment_hits")})

    g["sections"] = ordered
    g["nodes"] = kept
    g["skill"]["source_sha256"] = d["source_sha256"]
    g["skill"]["source_lines"] = len(text.split("\n"))
    # 只要有节点被打回或新增节未编译，就退出 active——原文重新成为权威
    g["state"] = "invalidated" if (dirty or d["needs_new_nodes"] or dropped) else g["state"]
    g["revision_log"].append({
        "ts": now_iso(), "event": "SOURCE_CHANGED",
        "sections_modified": [s["heading"] for s in d["sections_modified"]],
        "sections_added": [s["heading"] for s in d["sections_added"]],
        "sections_removed": [s["heading"] for s in d["sections_removed"]],
        "nodes_marked_stale": sorted(dirty),
        "nodes_dropped": dropped,
        "note": "受影响节点已打回待重编；未变节的节点与其已细化判据原样保留",
    })
    g["validation"] = validate(g)
    return {"updated": True, "detect": d, "graph": g,
            "stale_nodes": sorted(dirty), "dropped_nodes": dropped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="skill → workflow 编排器（承重层）")
    ap.add_argument("--compile", help="skill 原文路径")
    ap.add_argument("--out", help="编译产物落盘路径")
    ap.add_argument("--validate", help="校验已有编译产物")
    ap.add_argument("--detect", help="侦测原文变更（传编译产物路径）")
    ap.add_argument("--update", help="按变更增量更新（传编译产物路径）")
    ap.add_argument("--source", help="覆盖原文路径（用于测试）")
    a = ap.parse_args()

    if a.compile:
        g = compile_skill(a.compile)
        g["validation"] = validate(g)
        s = json.dumps(g, ensure_ascii=False, indent=1)
        if a.out:
            open(a.out, "w", encoding="utf-8", newline="").write(s + "\n")
            print("已编译 → %s" % a.out)
            print("  节 %d / 节点 %d（候选/命令 %d, 判断 %d）"
                  % (len(g["sections"]), len(g["nodes"]),
                     sum(1 for n in g["nodes"] if n["type"] in ("tool", "candidate")),
                     sum(1 for n in g["nodes"] if n["type"] == "judgment")))
            v = g["validation"]
            print("  结构校验 %s；可投用 %s；待细化节点 %d"
                  % ("通过" if v["ok"] else "不通过", v["promotable"], len(v["unrefined"])))
            for e in v["errors"][:5]:
                print("   错误: %s" % e)
        else:
            print(s)
        return 0

    if a.validate:
        g = json.load(open(a.validate, encoding="utf-8"))
        v = validate(g)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0 if v["ok"] else 1

    if a.detect:
        g = json.load(open(a.detect, encoding="utf-8"))
        print(json.dumps(detect_changes(g, a.source), ensure_ascii=False, indent=1))
        return 0

    if a.update:
        g = json.load(open(a.update, encoding="utf-8"))
        r = apply_update(g, a.source)
        if r["updated"]:
            open(a.update, "w", encoding="utf-8", newline="").write(
                json.dumps(r["graph"], ensure_ascii=False, indent=1) + "\n")
        print(json.dumps({k: v for k, v in r.items() if k != "graph"},
                         ensure_ascii=False, indent=1))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# 控制流的病态 fixture 取证（硬约定 6：零反例的分支不许接线）
# ---------------------------------------------------------------------------

CF_FIXTURES = [
    ("守卫为真→执行", """
```bash
echo "NEEDS_SETUP"
```
If `NEEDS_SETUP`:
1. 干活
   ```bash
   echo doing-setup
   ```
""", {"guarded_cmds": 1, "gates": 0}),

    ("守卫为假→跳过（对拍失败的那个形态）", """
```bash
echo "READY: /path/to/bin"
```
If `NEEDS_SETUP`:
1. 干活
   ```bash
   echo doing-setup
   ```
""", {"guarded_cmds": 1, "gates": 0}),

    ("人闸必须被识别", """
If `NEEDS_SETUP`:
1. Tell the user: "OK to proceed?" Then STOP and wait.
""", {"guarded_cmds": 0, "gates": 1}),

    ("嵌套守卫→两层都记上", """
If `OUTER`:
1. 外层
   If `INNER`:
   2. 内层
      ```bash
      echo nested
      ```
""", {"guarded_cmds": 1, "gates": 0, "depth": 2}),

    ("标题关闭作用域→不误伤（反向）", """
If `NEEDS_SETUP`:
1. 受守卫的活
   ```bash
   echo guarded
   ```

## 另一节

```bash
echo unguarded
```
""", {"guarded_cmds": 1, "unguarded_cmds": 1, "gates": 0}),
]


def cf_selftest() -> int:
    """逐形态取证。**含一条反向**：标题之后的块不许被守卫误伤。"""
    ok = True
    for name, src, want in CF_FIXTURES:
        bs = extract_blocks(src)
        cmds = [b for b in bs if b["kind"] in ("cmd", "branch")]
        gates = [b for b in bs if b["kind"] == "human_gate"]
        guarded = [b for b in cmds if b["guards"]]
        unguarded = [b for b in cmds if not b["guards"] and b["kind"] == "cmd"]
        maxdepth = max([len(b["guards"]) for b in cmds + gates] or [0])
        good = (len(guarded) == want.get("guarded_cmds", 0)
                and len(gates) == want.get("gates", 0))
        if "unguarded_cmds" in want:
            good = good and len(unguarded) == want["unguarded_cmds"]
        if "depth" in want:
            good = good and maxdepth == want["depth"]
        ok &= good
        print("  %-30s 受守卫块%d 人闸%d 无守卫块%d 最深%d → %s"
              % (name, len(guarded), len(gates), len(unguarded), maxdepth,
                 "OK" if good else "**不符**"))
    print("控制流逐形态取证：%s" % ("全部符合" if ok else "有不符项"))
    return 0 if ok else 1
