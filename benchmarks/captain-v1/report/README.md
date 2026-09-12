# 评测结果页生成器

把考官评完的成绩渲染成一张可公开的结果页，以及 README 能直接嵌的两张静态 SVG。
页面本身不做评分，只做展示：所有分数来自 `results/`，所有题库口径来自 `examiner/cases.json`。

| 产物 | 说明 |
|---|---|
| `report/index.html` | 单文件结果页。内联 CSS/JS，只从 cdnjs 取固定版本 Chart.js；断网时图表消失，表格数值仍完整可读。 |
| `docs/img/bench/dimensions.svg` | 分组柱状图（主数据是记录 schema 时画九项能力 K1–K9，是 score.py 输出时画原文七维 A–G）。 |
| `docs/img/bench/overall.svg` | 综合分横条图。 |
| `report/preview.png` | 结果页整页截图，给不方便打开 HTML 的人看。 |

```sh
python3 report/build_report.py            # 有真实结果就用真实结果
python3 report/build_report.py --samples  # 强制用虚构样例跑通（页面会显眼标注「示例数据」）
python3 report/make_samples.py            # 重新生成 3 份虚构样例
```

## 从答卷到页面

```
考官评分            评分器              结果目录                   本工具
ratings.json  →  score.py score  →  results/*.scores.json  →  build_report.py  →  index.html
                                    results/records/*.json                        + 两张 SVG
```

1. **评分**：`python3 score.py template --out ratings.json` 拿模板，逐检查点填 `status/result/process/evidence`，
   再填红线、隔离、困难门、裁决与 run 身份。评分器只做算术，证据真伪由考官负责。
2. **算分**：`python3 score.py score ratings.json > results/<模型>.scores.json`。
3. **补元数据**：在那份 JSON 的**顶层**加 `model`、`harness`、`date`、`judge`；同一模型分阶段考的再加 `stage`。
   想让页面画出分层得分与逐点失分，再加 `ratings_file`（相对 `results/` 的路径，指向该轮 ratings）。
4. **出页面**：`python3 report/build_report.py`。

## 两种输入 schema

工具同时认两种文件，可以共存：

**① `results/records/*.json`（`schema=starfix-captain-records/v1`）—— 页面主数据**

记录册导出的格式，一个模型一份。用到的字段：

| 字段 | 用途 |
|---|---|
| `model` / `platform` / `run` / `date` / `review` | 模型名、harness、本轮说明、日期、评审方式 |
| `scores.K1…K9` | 九项能力分；**综合分由本工具按题库权重重算**，不从文件里抄 |
| `stages.L1…L4.{points,max}` | 四层得分/满分，页面换算成得分率 |
| `measured` / `total` | 覆盖率 |
| `verdict` / `veto` / `isolation` | 结论、一票否决、隔离是否证实 |
| `strengths` / `weaknesses` / `caveats` | 逐模型评审卡里的强项、缺口、边界 |

同目录下同名的 `*-综合评估.md` 的**首段**会被引成该模型的「评审摘要」
（命名规则：`X-考核数据.json` ↔ `X-综合评估.md`）。

**② `results/*.scores.json`（`score.py score` 的完整输出 + 顶层元数据）**

有记录文件时，这类文件作为附录「阶段进展」展示（同一模型的多个阶段并排）；
没有记录文件时它就是主数据。比记录 schema 多两样东西：原文七维 A–G 诊断分，
以及（给了 `ratings_file` 时）分层得分率与**失分 Top 5 检查点**。

## 加第 4 个模型

- **走记录 schema**：把该模型的 `X-考核数据.json`（可选 `X-综合评估.md`）放进 `results/records/`，重跑
  `build_report.py`。排行榜、四张图、评审卡、两张 SVG 都会自动多出一行/一组，不用改代码。
- **走 score.py schema**：把 `<模型>.scores.json` 放进 `results/`，顶层补 `model/harness/date/judge`
  （分阶段的再加 `stage`、`ratings_file`），重跑。
- **配色**：颜色按模型身份分配（色盲友好的 Okabe-Ito 色序，跨两种 schema 按归一化模型名认人），
  同一模型的多个阶段用同色相的深浅，覆盖率低的浅。第 4 个模型自动取色序里的下一个颜色，最多 6 个。
- **排序**：主表按综合分降序，附录按覆盖率升序（阶段推进的顺序）。

## 几条必须知道的口径

- **综合分随覆盖率变**：未测检查点按 0 计。覆盖率不同的两次考核不能直接比高低，覆盖不足时分数只是下界。
  页面在表头、提示条和 SVG 注脚三处都写了这句。
- **综合分不抄文件**：按 `examiner/cases.json` 的 K1–K9 权重从能力分重算，和记录册公布值一致。
  这样记录里万一有笔误，页面会和它对不上，而不是照抄。
- **题库快照**：版本字符串同为 1.1.0，补题前后分别是 142 项和 157 项。附录里的阶段结果是 142 项快照，
  和主数据的 157 项不能直接比。比较时必须同时记录提交号/哈希。
- **日期**：开源门禁 `tools/scrub-gate.sh` 只豁免 `results/` 目录里的绝对日期，页面在 `report/` 下，
  所以 `index.html` 按「第 N 批」显示，精确日期以 `results/` 原始记录为准。
  `build_report.py` 遇到绝对日期会在 stderr 提醒并自动改成批次，不会把日期写进页面。
- **不碰盐与词表**：本工具只读 `results/` 与 `examiner/cases.json`，不读取、不嵌入任何脱敏盐或词表，
  产物里没有凭据。
- **Chart.js 固定版本**：只允许 `https://cdnjs.cloudflare.com/.../Chart.js/4.4.1/chart.umd.min.js` 一个外部资源。
  升级版本只改 `build_report.py` 顶部的 `CHART_JS` 常量。

## 样例

`results/sample-*.scores.json` 是 `make_samples.py` 造的 3 份**虚构**样例（模型名 Sample A/B/C），
分数由 `score.py` 真算出来，只为让没有真实结果的人也能跑通。
只要 `results/` 里有真实结果，样例就不会进页面；真用到样例时页面顶部会有红色「示例数据」提示条。

## 在 README 里嵌 SVG

```md
![舰长考核 · 九项核心能力](docs/img/bench/dimensions.svg)
![舰长考核 · 综合分](docs/img/bench/overall.svg)
```

## 自证

```sh
python3 report/build_report.py                                   # 生成
python3 -c "import xml.dom.minidom as m; m.parse('../../docs/img/bench/dimensions.svg')"   # SVG 良构
python3 -m unittest discover -s tests -v                         # 评分器回归仍绿
export SCRUB_SALT="$(cat "$HOME/.starfix/scrub-salt")"; bash ../../tools/scrub-gate.sh
```
