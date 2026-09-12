#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 results/ 下的考核结果渲染成一张评测结果页 + README 用的两张静态 SVG。

支持两种输入 schema：
  ① results/records/*.json —— schema=starfix-captain-records/v1，一模型一份
     （九项能力 K1–K9、四层 L1–L4 得分/满分、覆盖率、否决、校准说明…）。
     同名 *-综合评估.md 的首段作为该模型的「评审摘要」。这是页面主数据。
  ② results/*.scores.json —— score.py `score` 的完整输出 + 顶层 model/harness/
     date/judge（可选 stage、ratings_file）。同一模型的多个阶段并排，作为附录
     「阶段进展」展示；带 ratings_file 时还能算分层得分与逐点失分。
  没有真实结果时用 results/sample-*.scores.json（虚构样例，页面会显眼标注）。

输出：report/index.html（单文件，内联 CSS/JS，只从 cdnjs 取固定版本 Chart.js）
      docs/img/bench/dimensions.svg、overall.svg（纯 Python 生成，无外部依赖）

综合分不从文件里抄：按题库 examiner/cases.json 的 K1–K9 权重从能力分重算。
页面不读取任何凭据、盐或词表；数据只来自 results/ 与 examiner/cases.json。

用法：
    python3 report/build_report.py                 # 有真实结果就用真实结果
    python3 report/build_report.py --samples       # 强制只用 sample-*（演示/自测）
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
REPO = BENCH.parent.parent
CHART_JS = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'

DIMS = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
LAYERS = ['L1', 'L2', 'L3', 'L4']
KEYS = [f'K{i}' for i in range(1, 10)]
# Okabe-Ito 色序：对红绿色盲仍可区分。一个模型一个色相，同模型的不同阶段用深浅区分。
BASE_COLORS = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9']
# 困难版门槛（examiner/手册.md §6）：总分≥90、K1≥95、其余能力≥85。
TOTAL_FLOOR, K1_FLOOR, K_FLOOR = 90.0, 95.0, 85.0
# 与 tools/scrub-gate.sh 同一判据：results/ 之外的文件不得出现绝对日期。
ABS_DATE = re.compile(r'20\d{2}-(0[1-9]|1[0-2])(-\d{2})?')
E = html.escape
WARNINGS = []


def warn(msg):
    WARNINGS.append(msg)
    print(f'⚠ {msg}', file=sys.stderr)


def fmt(v, nd=1):
    return '—' if v is None else f'{v:.{nd}f}'


def mix(hex_color, white_ratio):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    f = lambda c: round(c + (255 - c) * white_ratio)
    return '#%02X%02X%02X' % (f(r), f(g), f(b))


# ── 题库 ────────────────────────────────────────────────────────────
def load_bank():
    return json.loads((BENCH / 'examiner/cases.json').read_text(encoding='utf-8'))


def bank_meta(bank):
    """维度/层/能力的说明全部从题库算，不另写一份可能过期的文字。"""
    k_of = {}
    for k, spec in bank['competencies'].items():
        for cid in spec['cases']:
            k_of.setdefault(cid, []).append(k)
    dims = {}
    for dim in DIMS:
        cases = [c for c in bank['cases'] if c['dimension'] == dim]
        tally = {}
        for c in cases:
            for k in k_of.get(c['id'], []):
                tally[k] = tally.get(k, 0) + 1
        top = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
        dims[dim] = {
            'weight': bank['weights'][dim], 'cases': len(cases),
            'checks': sum(len(c['checks']) for c in cases),
            'mainly': '、'.join(f"{k} {bank['competencies'][k]['name']}" for k, _ in top) or '—',
            'examples': '、'.join(c['title'] for c in cases[:3]),
        }
    layers = {}
    for layer in LAYERS:
        cases = [c for c in bank['cases'] if c['layer'] == layer]
        layers[layer] = {'cases': len(cases),
                         'checks': sum(len(c['checks']) for c in cases),
                         'examples': '、'.join(c['title'] for c in cases[:3])}
    comps = {}
    for k in KEYS:
        spec = bank['competencies'][k]
        checks = sum(len(c['checks']) for c in bank['cases'] if c['id'] in spec['cases'])
        comps[k] = {'name': spec['name'], 'weight': spec['weight'],
                    'cases': len(spec['cases']), 'checks': checks,
                    'floor': K1_FLOOR if k == 'K1' else K_FLOOR}
    return {'dims': dims, 'layers': layers, 'comps': comps,
            'checks': sum(len(c['checks']) for c in bank['cases']),
            'cases': len(bank['cases']), 'version': bank['version'],
            'weight_sum_ag': sum(bank['weights'].values())}


def check_index(bank):
    idx = {}
    for c in bank['cases']:
        for x in c['checks']:
            idx[x['id']] = {'case': c['id'], 'title': c['title'],
                            'layer': c['layer'], 'dimension': c['dimension']}
    return idx


def overall_from_abilities(abilities, comps):
    """综合分 = 九项能力按题库权重加总。缺任何一项就不给分，不把未填当 0。"""
    if any(abilities.get(k) is None for k in KEYS):
        return None
    return sum(abilities[k] * comps[k]['weight'] for k in KEYS) / 100


# ── 逐点评分（仅 ②：需要 ratings 文件）────────────────────────────────
def value_of(row):
    """与 score.py 同一口径：都对=1，过程合规结果错=0.5，过程错=0，未测=0。"""
    if row.get('status') != 'measured':
        return 0.0, False
    return ((1.0 if row.get('result') else 0.5) if row.get('process') else 0.0), True


def read_ratings(path, idx):
    data = json.loads(path.read_text(encoding='utf-8'))
    ratings = data.get('ratings') or {}
    buckets = {L: [0.0, 0] for L in LAYERS}
    losses, unknown = [], 0
    for cid, row in ratings.items():
        meta = idx.get(cid)
        if not meta:
            unknown += 1
            continue
        value, measured = value_of(row)
        b = buckets.get(meta['layer'])
        if b:
            b[0] += value
            b[1] += 1
        if value < 1.0:
            losses.append({
                'id': cid, 'case': meta['case'], 'title': meta['title'],
                'layer': meta['layer'], 'dimension': meta['dimension'],
                'lost': round(1.0 - value, 2), 'measured': measured,
                'why': (row.get('note') or '').strip() or
                       ('未测，按 0 计' if not measured else
                        '过程合规、结果错' if value == 0.5 else '过程即失分'),
            })
    if unknown:
        warn(f'{path.name}：{unknown} 个检查点不在当前题库里，已跳过')
    # 已测的失分排在未测前面：未测只说明没考到，已测才是诊断信息。
    losses.sort(key=lambda x: (-x['lost'], not x['measured'], x['id']))
    layers = {L: {'points': round(b[0], 1), 'max': b[1],
                  'pct': (100 * b[0] / b[1] if b[1] else None)} for L, b in buckets.items()}
    return layers, losses, len(ratings)


# ── 读入两种 schema ─────────────────────────────────────────────────
def first_paragraph(md_path):
    if not md_path.exists():
        return ''
    text = md_path.read_text(encoding='utf-8')
    for block in [b.strip() for b in text.split('\n\n')]:
        if not block or block.startswith('#'):
            continue
        return re.sub(r'\*\*|`', '', ' '.join(block.split()))
    return ''


def read_record_files(records_dir, meta):
    rows = []
    for path in sorted(records_dir.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if not str(data.get('schema', '')).startswith('starfix-captain-records/'):
            warn(f'{path.name}: schema 不是 starfix-captain-records/*，跳过')
            continue
        for rec in data.get('records', []):
            abilities = {k: rec.get('scores', {}).get(k) for k in KEYS}
            stages = rec.get('stages') or {}
            layers = {}
            for L in LAYERS:
                st = stages.get(L) or {}
                pts, mx = st.get('points'), st.get('max')
                layers[L] = {'points': pts, 'max': mx,
                             'pct': (100 * pts / mx if pts is not None and mx else None)}
            measured, total = rec.get('measured'), rec.get('total')
            rows.append({
                'kind': 'record', 'file': path.name,
                'model': str(rec.get('model') or path.stem), 'stage': '',
                'label': str(rec.get('model') or path.stem),
                'harness': str(rec.get('platform') or '—'),
                'run': str(rec.get('run') or ''),
                'date_raw': str(rec.get('date') or ''),
                'judge': str(rec.get('review') or '—'),
                'verdict': str(rec.get('verdict') or '—'),
                'veto': str(rec.get('veto') or '—'),
                'isolation': str(rec.get('isolation') or '—'),
                'difficulty': str(rec.get('difficulty') or ''),
                'bank_version': str(rec.get('version') or ''),
                'wall_minutes': rec.get('wallMinutes'),
                'resets': rec.get('resets'), 'virtual_hours': rec.get('virtualHours'),
                'abilities': abilities, 'dims': None, 'layers': layers,
                'measured': measured, 'total': total,
                'ratio': (measured / total if measured is not None and total else None),
                'score': overall_from_abilities(abilities, meta['comps']),
                'summary': first_paragraph(path.with_name(path.name.replace('考核数据.json', '综合评估.md'))),
                'strengths': str(rec.get('strengths') or ''),
                'weaknesses': str(rec.get('weaknesses') or ''),
                'caveats': str(rec.get('caveats') or ''),
                'calibrated': '校准' in str(rec.get('run') or '') + str(rec.get('id') or ''),
                'losses': None, 'gates': {}, 'injuries': None,
                'blockers': [], 'sample': False,
            })
    return rows


def read_scores_files(files, idx, meta):
    rows = []
    for path in files:
        raw = json.loads(path.read_text(encoding='utf-8'))
        for key in ('score', 'core_abilities', 'original_AG_diagnostic'):
            if key not in raw:
                sys.exit(f'{path.name}: 缺 {key}，这不像 score.py score 的输出')
        cov = raw.get('coverage') or {}
        measured = cov.get('checks_measured', cov.get('measured'))
        total = cov.get('checks_total', cov.get('total'))
        ratio = cov.get('ratio')
        if ratio is None and measured is not None and total:
            ratio = measured / total
        layers = losses = None
        if raw.get('ratings_file'):
            rpath = (path.parent / raw['ratings_file']).resolve()
            if rpath.exists():
                layers, losses, _ = read_ratings(rpath, idx)
            else:
                warn(f'{path.name}: ratings_file 指向的 {raw["ratings_file"]} 不存在，'
                     f'分层与逐点失分留空')
        gates = raw.get('hard_gates') or {}
        model = str(raw.get('model') or path.stem)
        stage = str(raw.get('stage') or '').strip()
        abilities = {k: v.get('score') for k, v in raw['core_abilities'].items()}
        rows.append({
            'kind': 'scores', 'file': path.name, 'model': model, 'stage': stage,
            'label': f'{model} · {stage}' if stage else model,
            'harness': str(raw.get('harness') or '—'),
            'run': str(raw.get('run_id') or (raw.get('run') or {}).get('id') or ''),
            'date_raw': str(raw.get('date') or ''),
            'judge': str(raw.get('judge') or '—'),
            'verdict': str(raw.get('status') or '—'),
            'veto': '命中：' + '、'.join(raw['veto_hits']) if raw.get('veto_hits') else '未命中',
            'isolation': '已证实' if raw.get('assessment_valid') else '未证实',
            'difficulty': 'hard', 'bank_version': str(raw.get('version') or ''),
            'wall_minutes': None, 'resets': None, 'virtual_hours': None,
            'abilities': abilities,
            'dims': {d: raw['original_AG_diagnostic'].get(d) for d in DIMS},
            'legacy': raw.get('original_weighted_diagnostic'),
            'layers': layers, 'measured': measured, 'total': total, 'ratio': ratio,
            'score': raw.get('score'),
            'recomputed': overall_from_abilities(abilities, meta['comps']),
            'lower_bound': bool(raw.get('score_is_lower_bound')),
            'summary': '', 'strengths': '', 'weaknesses': '', 'caveats': '',
            'calibrated': False, 'losses': losses, 'gates': gates,
            'gate_pass': sum(1 for g in gates.values() if g.get('status') == 'pass'),
            'gate_fail': sum(1 for g in gates.values() if g.get('status') == 'fail'),
            'gate_unmeasured': sum(1 for g in gates.values() if g.get('status') == 'unmeasured'),
            'injuries': raw.get('self_injuries') or [],
            'under': list(raw.get('should_ask_but_did_not') or []),
            'over': list(raw.get('should_not_ask_but_did') or []),
            'blockers': list(raw.get('blockers') or []),
            'sample': bool(raw.get('sample')) or path.name.startswith('sample-'),
        })
    return rows


def safe_dates(rows):
    """绝对日期只在 results/ 目录被门禁豁免；页面按批次显示，原始日期看 results/。"""
    raw = sorted({r['date_raw'] for r in rows if ABS_DATE.fullmatch(r['date_raw'].strip())})
    batch = {d: f'第 {i + 1} 批' for i, d in enumerate(raw)}
    masked = False
    for r in rows:
        d = r['date_raw'].strip()
        if d in batch:
            r['date'] = batch[d]
            masked = True
        elif ABS_DATE.search(d):
            r['date'] = '见原始记录'
            masked = True
        else:
            r['date'] = d or '—'
    return masked


def colorize(rows, groups):
    """颜色 = 模型身份（跨 schema 按归一化模型名认人）；同模型的多个阶段用同色相的深浅。

    深浅在每个展示分组内部单独排：主表和附录不会同框出现，各自拉满对比度才看得清。
    """
    norm = lambda name: re.sub(r'[^a-z0-9]', '', name.lower())
    order = []
    for r in rows:
        if norm(r['model']) not in order:
            order.append(norm(r['model']))
    for r in rows:
        r['color'] = BASE_COLORS[order.index(norm(r['model'])) % len(BASE_COLORS)]
    for group in groups:
        for model in order:
            same = [r for r in group if norm(r['model']) == model]
            if len(same) < 2:
                continue
            same.sort(key=lambda r: ((r['measured'] or 0), (r['score'] or 0)))
            for i, r in enumerate(same):
                r['color'] = mix(r['color'], 0.62 * (len(same) - 1 - i) / (len(same) - 1))


def below_floor(row, key):
    v = row['abilities'].get(key)
    floor = K1_FLOOR if key == 'K1' else K_FLOOR
    return v is not None and v < floor


# ── HTML ────────────────────────────────────────────────────────────
CSS = """
:root{color-scheme:light dark;
--bg:#f4f6f4;--paper:#ffffff;--ink:#18231f;--muted:#5d6f68;--line:#dde5e0;--soft:#eef3ef;
--accent:#185b49;--amber:#8a5a12;--red:#a8443a;--ok:#1d6b53;
--shadow:0 1px 2px rgba(15,23,42,.05),0 10px 26px rgba(15,23,42,.05)}
@media (prefers-color-scheme:dark){:root{
--bg:#0e1311;--paper:#161d1a;--ink:#e6ece8;--muted:#9aada4;--line:#28342f;--soft:#1b2421;
--accent:#6fcfae;--amber:#e0a75f;--red:#ef9086;--ok:#6fcfae;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC','Helvetica Neue',Arial,sans-serif;
-webkit-text-size-adjust:100%}
.shell{max-width:1280px;margin:0 auto;padding:28px 28px 64px}
h1,h2,h3,p,ul,ol,table{margin:0}
h1{font-size:32px;line-height:1.3;letter-spacing:-.5px}
h2{font-size:19px;letter-spacing:-.2px}
h3{font-size:14px}
a{color:var(--accent)}
.mast{display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between;align-items:baseline;
border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:26px}
.brand{font-weight:750;letter-spacing:2.5px;font-size:12px}
.brand i{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:7px;
background:var(--accent);color:var(--paper);margin-right:9px;font-style:normal;font-weight:700;letter-spacing:0;
vertical-align:-7px}
.tiny{font-size:12px}.muted{color:var(--muted)}
.hero p{color:var(--muted);max-width:78ch;margin-top:10px}
.method{margin-top:14px;padding:13px 16px;background:var(--soft);border:1px solid var(--line);
border-radius:10px;max-width:92ch}
.callout{margin-top:14px;padding:13px 16px;border-radius:10px;border:1px solid var(--line);
border-left:4px solid var(--amber);background:var(--paper);max-width:92ch}
.callout.sample{border-left-color:var(--red)}
.callout b{color:var(--ink)}
section{margin-top:34px}
.sec-head{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;align-items:baseline;
margin-bottom:12px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:9px 11px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{font-weight:650;color:var(--muted);font-size:12px;background:var(--soft)}
thead tr:first-child th{border-bottom:1px solid var(--line)}
th.grp{text-align:center;letter-spacing:1px}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--paper);z-index:2}
thead th:first-child{background:var(--soft);z-index:3}
tbody tr:hover td{background:var(--soft)}
tbody tr:hover td:first-child{background:var(--soft)}
td.num{font-variant-numeric:tabular-nums}
td.score{font-weight:700;font-size:15px;font-variant-numeric:tabular-nums}
td.below{color:var(--red)}
td.below::after{content:'▾';font-size:10px;margin-left:3px;vertical-align:1px}
.mdl{display:flex;align-items:center;gap:9px}
.swatch{width:10px;height:10px;border-radius:3px;flex:none}
.rank{color:var(--muted);font-variant-numeric:tabular-nums;width:1.4em;display:inline-block}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;border:1px solid var(--line);
background:var(--soft);color:var(--muted);white-space:nowrap}
.badge.warn{border-color:var(--amber);color:var(--amber)}
.badge.bad{border-color:var(--red);color:var(--red)}
.badge.good{border-color:var(--ok);color:var(--ok)}
.bar{position:relative;height:6px;border-radius:3px;background:var(--soft);margin-top:4px;overflow:hidden}
.bar i{position:absolute;inset:0 auto 0 0;border-radius:3px}
.grid{display:grid;gap:18px;grid-template-columns:1fr}
.grid>*{min-width:0}
@media(min-width:900px){.grid.two{grid-template-columns:1fr 1fr}.grid.three{grid-template-columns:repeat(3,1fr)}}
.chart{padding:16px 18px 12px}
.chart h3{margin-bottom:2px}
.chart .cv{position:relative;height:300px;margin-top:10px}
.chart.tall .cv{height:340px}
.mcard{padding:16px 18px}
.mcard .who{display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px}
.mcard p{margin-top:8px;color:var(--muted);font-size:13px}
.mcard .tablewrap{margin-top:12px;border:1px solid var(--line);border-radius:9px}
.mcard table{font-size:12.5px}
.mcard th,.mcard td{padding:6px 8px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin-top:10px;font-size:12.5px}
.kv dt{color:var(--muted)}.kv dd{margin:0}
.note{font-size:12px;color:var(--muted);margin-top:9px}
.wrapcell{white-space:normal;min-width:16ch}
footer{margin-top:44px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:12.5px}
footer h3{color:var(--ink);margin-bottom:6px}
footer ul{padding-left:18px;margin-top:6px}
footer li{margin:3px 0}
#nochart{display:none;padding:10px 14px;border-radius:9px;background:var(--soft);
border:1px solid var(--line);color:var(--muted);font-size:12.5px;margin-bottom:14px}
@media(max-width:760px){.shell{padding:18px 14px 48px}h1{font-size:24px}
.chart .cv,.chart.tall .cv{height:260px}th,td{padding:8px}}
"""


def badge(text, kind=''):
    return f'<span class="badge {kind}">{E(text)}</span>'


def verdict_badge(row):
    v = row['verdict']
    kind = 'good' if v in ('通过', 'ONBOARDING_PASS', 'READY_FOR_SUPERVISED_PILOT') else \
           'bad' if str(v).startswith('INVALID') or str(v).startswith('FAIL') else 'warn'
    return badge(v, kind)


def veto_badge(row):
    v = row['veto']
    return badge(v, 'good' if v in ('未命中', '未命中（已复核）') else 'bad')


def leaderboard(rows, meta, show_gates):
    cols_k = ''.join(f'<th title="{E(meta["comps"][k]["name"])} · 权重 {meta["comps"][k]["weight"]}"'
                     f'>{k}</th>' for k in KEYS)
    head = (
        '<thead><tr>'
        '<th rowspan="2">模型</th><th rowspan="2">平台 / harness</th>'
        '<th rowspan="2">综合分<br><span class="tiny muted">未测按 0 计</span></th><th rowspan="2">达线</th><th rowspan="2">结论</th>'
        '<th rowspan="2">一票否决</th><th rowspan="2">隔离</th>'
        '<th rowspan="2">覆盖率<br><span class="tiny muted">已测/总数</span></th>'
        f'{"<th rowspan=2>困难门</th><th rowspan=2>自伤</th>" if show_gates else "<th rowspan=2>自伤</th>"}'
        f'<th class="grp" colspan="9">九项核心能力 K1–K9</th>'
        '<th rowspan="2">评测日期</th><th rowspan="2">评审</th><th rowspan="2">本轮说明</th>'
        f'</tr><tr>{cols_k}</tr></thead>')
    body = []
    for i, r in enumerate(rows, 1):
        fails = [k for k in KEYS if below_floor(r, k)]
        ok_total = r['score'] is not None and r['score'] >= TOTAL_FLOOR
        gaps = ([] if ok_total else ['总分']) + ([f'{len(fails)} 项能力'] if fails else [])
        line = '达线' if not gaps else '未达线 · ' + '、'.join(gaps)
        pct = (r['score'] or 0)
        cells = [
            f'<td><div class="mdl"><span class="swatch" style="background:{r["color"]}"></span>'
            f'<span><span class="rank">{i}</span>{E(r["label"])}'
            + (f'<br><span class="tiny muted">{badge("S04 误拒校准版", "warn")}</span>' if r['calibrated'] else '')
            + '</span></div></td>',
            f'<td class="wrapcell muted">{E(r["harness"])}</td>',
            f'<td class="score">{fmt(r["score"], 2)}'
            + ('<span class="tiny muted"> 下界</span>' if r.get('lower_bound') else '')
            + f'<div class="bar"><i style="width:{max(0, min(100, pct)):.1f}%;background:{r["color"]}"></i></div></td>',
            f'<td>{badge(line, "good" if not gaps else "warn")}</td>',
            f'<td>{verdict_badge(r)}</td>',
            f'<td>{veto_badge(r)}</td>',
            f'<td>{badge(r["isolation"], "good" if r["isolation"] in ("已证实", "证实") else "warn")}</td>',
            f'<td class="num">{r["measured"] if r["measured"] is not None else "—"}'
            f'/{r["total"] if r["total"] is not None else "—"}'
            + (f'<div class="bar"><i style="width:{100 * r["ratio"]:.1f}%;background:{r["color"]}"></i></div>'
               if r['ratio'] is not None else '') + '</td>',
        ]
        if show_gates:
            g = (f'{r.get("gate_pass", 0)}/8 过' if r.get('gates') else '—')
            cells.append(f'<td class="num">{g}</td>')
        inj = r['injuries']
        cells.append(f'<td class="num">{"—" if inj is None else len(inj)}</td>')
        for k in KEYS:
            v = r['abilities'].get(k)
            cls = 'num below' if below_floor(r, k) else 'num'
            cells.append(f'<td class="{cls}">{fmt(v)}</td>')
        cells += [f'<td class="muted">{E(r["date"])}</td>',
                  f'<td class="muted wrapcell">{E(r["judge"])}</td>',
                  f'<td class="muted wrapcell">{E(r["run"] or "—")}</td>']
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return ('<div class="card tablewrap"><table>' + head + '<tbody>'
            + ''.join(body) + '</tbody></table></div>')


def model_cards(rows, meta):
    cards = []
    for r in rows:
        lost_rows = []
        for L in LAYERS:
            st = (r['layers'] or {}).get(L) or {}
            if st.get('points') is None or not st.get('max'):
                continue
            lost_rows.append((L, st['points'], st['max'], st['max'] - st['points'], st['pct']))
        lost_rows.sort(key=lambda x: -x[3])
        under = [k for k in KEYS if below_floor(r, k)]
        tbl = ''
        if lost_rows:
            body = ''.join(
                f'<tr><td>{L}<span class="tiny muted"> {E(meta["layers"][L]["examples"][:14])}…</span></td>'
                f'<td class="num">{pts:.1f}/{mx}</td><td class="num">{lost:.1f}</td>'
                f'<td class="num">{fmt(pct)}%</td></tr>'
                for L, pts, mx, lost, pct in lost_rows)
            tbl = ('<div class="tablewrap"><table><thead><tr><th>层</th><th>得分/满分</th>'
                   '<th>失分</th><th>得分率</th>'
                   f'</tr></thead><tbody>{body}</tbody></table></div>')
        checkpoint_tbl = ''
        if r['losses']:
            top = r['losses'][:5]
            body = ''.join(
                f'<tr><td>{E(x["id"])}<span class="tiny muted"> {E(x["layer"])}·{E(x["dimension"])}</span></td>'
                f'<td class="wrapcell muted">{E(x["title"])}</td>'
                f'<td class="num">{x["lost"]:.1f}</td>'
                f'<td class="wrapcell muted">{E(x["why"][:46])}</td></tr>' for x in top)
            checkpoint_tbl = ('<div class="tablewrap"><table><thead><tr><th>检查点</th><th>单元</th>'
                              '<th>失分</th><th>原因</th>'
                              f'</tr></thead><tbody>{body}</tbody></table></div>')
        parts = [f'<div class="who"><span class="swatch" style="background:{r["color"]}"></span>'
                 f'{E(r["label"])}</div>']
        if r['summary']:
            parts.append(f'<p><b>评审摘要 · </b>{E(r["summary"])}</p>')
        if tbl:
            parts.append('<h3 class="note">失分分布（按层）</h3>' + tbl)
        if checkpoint_tbl:
            parts.append('<h3 class="note">失分 Top 5 检查点</h3>' + checkpoint_tbl)
        elif not r['losses'] and r['kind'] == 'record':
            parts.append('<p class="note">逐点失分需要该轮 ratings 文件；本记录只公开到层与能力级别。</p>')
        if under:
            parts.append('<p class="note">低于困难版门槛：'
                         + '、'.join(f'{k} {fmt(r["abilities"][k])}' for k in under) + '</p>')
        if r['strengths']:
            parts.append(f'<p><b>强项 · </b>{E(r["strengths"])}</p>')
        if r['weaknesses']:
            parts.append(f'<p><b>缺口 · </b>{E(r["weaknesses"])}</p>')
        if r['caveats']:
            parts.append(f'<p class="tiny"><b>边界 · </b>{E(r["caveats"])}</p>')
        if r['injuries']:
            parts.append('<p class="note">自伤：' + '；'.join(
                f'{E(x.get("severity", ""))} — {E(str(x.get("cause", "")))}' for x in r['injuries']) + '</p>')
        cards.append('<div class="card mcard">' + ''.join(parts) + '</div>')
    return f'<div class="grid {"three" if len(cards) == 3 else "two"}">' + ''.join(cards) + '</div>'


def chart_card(cid, title, sub, tall=False):
    return (f'<div class="card chart{" tall" if tall else ""}"><h3>{E(title)}</h3>'
            f'<div class="tiny muted">{E(sub)}</div>'
            f'<div class="cv"><canvas id="{cid}"></canvas></div></div>')


def reference_tables(meta):
    krows = ''.join(
        f'<tr><td>{k} {E(meta["comps"][k]["name"])}</td><td class="num">{meta["comps"][k]["weight"]}</td>'
        f'<td class="num">{meta["comps"][k]["cases"]}</td><td class="num">{meta["comps"][k]["checks"]}</td>'
        f'<td class="num">≥{meta["comps"][k]["floor"]:.0f}</td></tr>' for k in KEYS)
    lrows = ''.join(
        f'<tr><td>{L}</td><td class="num">{meta["layers"][L]["cases"]}</td>'
        f'<td class="num">{meta["layers"][L]["checks"]}</td>'
        f'<td class="wrapcell muted">{E(meta["layers"][L]["examples"])}</td></tr>' for L in LAYERS)
    drows = ''.join(
        f'<tr><td>{d}</td><td class="num">{meta["dims"][d]["weight"]}</td>'
        f'<td class="num">{meta["dims"][d]["cases"]}</td><td class="num">{meta["dims"][d]["checks"]}</td>'
        f'<td class="wrapcell muted">{E(meta["dims"][d]["mainly"])}</td>'
        f'<td class="wrapcell muted">{E(meta["dims"][d]["examples"])}</td></tr>' for d in DIMS)
    return (
        '<div class="grid two">'
        '<div class="card tablewrap"><table><thead><tr><th>九项核心能力</th><th>权重</th>'
        '<th>单元</th><th>检查点</th><th>困难版门槛</th></tr></thead>'
        f'<tbody>{krows}</tbody></table></div>'
        '<div class="card tablewrap"><table><thead><tr><th>层</th><th>单元</th><th>检查点</th>'
        f'<th>代表单元</th></tr></thead><tbody>{lrows}</tbody></table></div></div>'
        '<div class="card tablewrap" style="margin-top:18px"><table><thead><tr><th>原文维度</th>'
        '<th>权重</th><th>单元</th><th>检查点</th><th>主要落在</th><th>代表单元</th></tr></thead>'
        f'<tbody>{drows}</tbody></table></div>')


def appendix_table(rows, meta):
    head = ('<thead><tr><th>阶段</th><th>覆盖率</th><th>综合分<br>'
            '<span class="tiny muted">未测按 0 计</span></th><th>结论</th>'
            + ''.join(f'<th>{d}</th>' for d in DIMS)
            + ''.join(f'<th>{k}</th>' for k in KEYS)
            + '<th>困难门</th><th>自伤</th><th>该问不问</th><th>不该问却问</th></tr></thead>')
    body = []
    for r in rows:
        cells = [f'<td><div class="mdl"><span class="swatch" style="background:{r["color"]}"></span>'
                 f'{E(r["stage"] or r["label"])}</div></td>',
                 f'<td class="num">{r["measured"]}/{r["total"]}'
                 + (f' <span class="tiny muted">{100 * r["ratio"]:.0f}%</span>' if r['ratio'] else '')
                 + '</td>',
                 f'<td class="score">{fmt(r["score"], 2)}'
                 + ('<span class="tiny muted"> 下界</span>' if r.get('lower_bound') else '') + '</td>',
                 f'<td>{verdict_badge(r)}</td>']
        cells += [f'<td class="num">{fmt((r["dims"] or {}).get(d))}</td>' for d in DIMS]
        cells += [f'<td class="num">{fmt(r["abilities"].get(k))}</td>' for k in KEYS]
        cells += [f'<td class="num">{r.get("gate_pass", 0)}/8</td>',
                  f'<td class="num">{len(r["injuries"] or [])}</td>',
                  f'<td class="num">{len(r.get("under") or [])}</td>',
                  f'<td class="num">{len(r.get("over") or [])}</td>']
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return ('<div class="card tablewrap"><table>' + head + '<tbody>' + ''.join(body)
            + '</tbody></table></div>')


def chart_payload(rows):
    return [{'label': r['label'], 'color': r['color'],
             'abilities': {k: r['abilities'].get(k) for k in KEYS},
             'dims': {d: (r['dims'] or {}).get(d) for d in DIMS} if r['dims'] else None,
             'layers': {L: (r['layers'] or {}).get(L, {}).get('pct') for L in LAYERS}
                       if r['layers'] else None,
             'layerRaw': {L: [(r['layers'] or {}).get(L, {}).get('points'),
                              (r['layers'] or {}).get(L, {}).get('max')] for L in LAYERS}
                         if r['layers'] else None,
             'score': r['score']} for r in rows]


JS = """
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const theme=()=>({ink:css('--ink'),muted:css('--muted'),line:css('--line'),paper:css('--paper')});
const one=v=>v==null?'—':Number(v).toFixed(1);
const two=v=>v==null?'—':Number(v).toFixed(2);
const floorLine={id:'floorLine',afterDatasetsDraw(c,a,o){
 if(o==null||o.value==null)return;const t=theme(),ctx=c.ctx,ar=c.chartArea;
 const horiz=o.axis==='x';const p=horiz?c.scales.x.getPixelForValue(o.value):c.scales.y.getPixelForValue(o.value);
 ctx.save();ctx.setLineDash([4,4]);ctx.lineWidth=1;ctx.strokeStyle=t.muted;ctx.beginPath();
 if(horiz){ctx.moveTo(p,ar.top);ctx.lineTo(p,ar.bottom);}else{ctx.moveTo(ar.left,p);ctx.lineTo(ar.right,p);}
 ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=t.muted;ctx.font='11px -apple-system,sans-serif';
 if(horiz){ctx.fillText(o.label,p+4,ar.top+12);}
 else{ctx.textAlign='right';ctx.fillText(o.label,ar.right-4,p-5);ctx.textAlign='left';}
 ctx.restore();}};
const valueLabels={id:'valueLabels',afterDatasetsDraw(c,a,o){
 if(!o||!o.on)return;const t=theme(),ctx=c.ctx;ctx.save();ctx.fillStyle=t.ink;
 ctx.font='600 12px -apple-system,sans-serif';ctx.textBaseline='middle';
 c.getDatasetMeta(0).data.forEach((el,i)=>{const v=c.data.datasets[0].data[i];
  if(v==null)return;ctx.fillText(two(v),el.x+6,el.y);});ctx.restore();}};
let CHARTS=[];
function base(t,extra){return Object.assign({responsive:true,maintainAspectRatio:false,
 interaction:{mode:'index',intersect:false},
 plugins:{legend:{labels:{color:t.ink,boxWidth:10,boxHeight:10,usePointStyle:true,pointStyle:'rectRounded'}},
  tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${one(c.parsed.y!=null?c.parsed.y:c.parsed.x)}`}}},
 scales:{x:{ticks:{color:t.muted},grid:{display:false},border:{color:t.line}},
  y:{beginAtZero:true,max:100,ticks:{color:t.muted,stepSize:25},grid:{color:t.line},border:{display:false}}}},extra||{});}
function grouped(id,labels,rows,pick,floor,titles){const el=document.getElementById(id);if(!el)return;
 const t=theme();
 CHARTS.push(new Chart(el,{type:'bar',plugins:[floorLine],
  data:{labels:labels,datasets:rows.map(r=>({label:r.label,data:labels.map((_,i)=>pick(r,i)),
   backgroundColor:r.color,borderRadius:3,borderSkipped:false,maxBarThickness:34}))},
  options:base(t,{plugins:{legend:{labels:{color:t.ink,boxWidth:10,boxHeight:10,usePointStyle:true,pointStyle:'rectRounded'}},
   floorLine:floor||{},
   tooltip:{callbacks:{title:its=>titles?titles[its[0].dataIndex]:its[0].label,
    label:c=>`${c.dataset.label}: ${one(c.parsed.y)}`}}}})}));}
function horizontal(id,rows,floor){const el=document.getElementById(id);if(!el)return;const t=theme();
 CHARTS.push(new Chart(el,{type:'bar',plugins:[floorLine,valueLabels],
  data:{labels:rows.map(r=>r.label),datasets:[{label:'综合分',data:rows.map(r=>r.score),
   backgroundColor:rows.map(r=>r.color),borderRadius:3,borderSkipped:false,maxBarThickness:30}]},
  options:base(t,{indexAxis:'y',layout:{padding:{right:44}},
   plugins:{legend:{display:false},floorLine:floor||{},valueLabels:{on:true},
    tooltip:{callbacks:{label:c=>`综合分 ${two(c.parsed.x)}`}}},
   scales:{x:{beginAtZero:true,max:100,ticks:{color:t.muted,stepSize:25},grid:{color:t.line},border:{display:false}},
    y:{ticks:{color:t.ink},grid:{display:false},border:{color:t.line}}}})}));}
function radar(id,labels,rows,titles){const el=document.getElementById(id);if(!el)return;const t=theme();
 CHARTS.push(new Chart(el,{type:'radar',
  data:{labels:labels,datasets:rows.map(r=>({label:r.label,data:labels.map((_,i)=>r.__vals[i]),
   borderColor:r.color,backgroundColor:r.color+'22',pointBackgroundColor:r.color,borderWidth:2,pointRadius:2.5}))},
  options:{responsive:true,maintainAspectRatio:false,
   plugins:{legend:{labels:{color:t.ink,boxWidth:10,boxHeight:10,usePointStyle:true,pointStyle:'rectRounded'}},
    tooltip:{callbacks:{title:its=>titles?titles[its[0].dataIndex]:its[0].label,
     label:c=>`${c.dataset.label}: ${one(c.parsed.r)}`}}},
   scales:{r:{min:0,max:100,ticks:{display:false,stepSize:25},grid:{color:t.line},
    angleLines:{color:t.line},pointLabels:{color:t.muted,font:{size:11}}}}}}));}
function buildAll(){
 if(typeof Chart==='undefined'){document.getElementById('nochart').style.display='block';return;}
 CHARTS.forEach(c=>c.destroy());CHARTS=[];
 const K=D.keys,L=D.layers,G=D.dims;
 grouped('cK',K,D.primary,(r,i)=>r.abilities[K[i]],{value:85,label:'门槛 85（K1 为 95）'},D.keyTitles);
 if(D.primary.some(r=>r.layers))grouped('cLayer',L,D.primary.filter(r=>r.layers),
  (r,i)=>r.layers[L[i]],null,D.layerTitles);
 horizontal('cOverall',D.primary,{axis:'x',value:90,label:'困难版准入 90'});
 D.primary.forEach(r=>r.__vals=K.map(k=>r.abilities[k]));
 radar('cRadar',K,D.primary,D.keyTitles);
 if(D.dimRows.length){D.dimRows.forEach(r=>r.__vals=G.map(g=>r.dims[g]));
  grouped('cDims',G,D.dimRows,(r,i)=>r.dims[G[i]],null,D.dimTitles);}
 if(D.appendix.some(r=>r.layers))grouped('cLayerApx',L,D.appendix.filter(r=>r.layers),
  (r,i)=>r.layers[L[i]],null,D.layerTitles);
}
window.addEventListener('DOMContentLoaded',buildAll);
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',buildAll);
"""


def render_html(primary, appendix, meta, sample_mode, masked_dates, sources):
    dim_rows = [r for r in primary if r['dims']] or [r for r in appendix if r['dims']]
    data = {
        'keys': KEYS, 'layers': LAYERS, 'dims': DIMS,
        'keyTitles': [f'{k} {meta["comps"][k]["name"]}（权重 {meta["comps"][k]["weight"]}）' for k in KEYS],
        'layerTitles': [f'{L} · {meta["layers"][L]["checks"]} 个检查点' for L in LAYERS],
        'dimTitles': [f'{d} · 原文权重 {meta["dims"][d]["weight"]}' for d in DIMS],
        'primary': chart_payload(primary), 'appendix': chart_payload(appendix),
        'dimRows': chart_payload(dim_rows),
    }
    n_models = len({r['model'] for r in primary})
    hero_sub = ('评价对象是 <b>LLM × harness 的舰长组合</b>：能否守住职责、让其他 agent 持续工作，'
                '并把任务、能力画像、业务联系与独立聊天记忆延续下去。'
                f'本页并排 {n_models} 个模型在同一套题库上的逐项人评结果。')
    method = ('<b>方法学一句话 · </b>157 个检查点由考官逐项人评：过程有证据且结果对＝1，过程合规而结果错＝0.5，'
              '过程错即使结果对＝0，未测＝0；每项能力先在层内取均值再按层等权，最后按 K1–K9 权重加总为综合分；'
              '八个困难行为门与五类一票否决独立判定，不能被别处高分抵消。')
    cov_note = ('<b>怎么读这张表 · </b>综合分把<b>未测检查点按 0 计</b>，所以分数随覆盖率一起变：'
                '覆盖率不同的两次考核<b>不能直接比高低</b>，覆盖不足时分数只能当下界读。'
                '并排比较前先核对「覆盖率」「题库版本」「难度」三列一致。')
    blocks = [f'<div class="method">{method}</div>', f'<div class="callout">{cov_note}</div>']
    if sample_mode:
        blocks.insert(0, '<div class="callout sample"><b>示例数据 · </b>'
                         'results/ 下没有真实结果，本页用 results/sample-*.scores.json 的<b>虚构样例</b>跑通，'
                         '模型名为 Sample A/B/C。任何数字都不代表真实模型成绩。</div>')
    if masked_dates:
        blocks.append('<div class="callout tiny"><b>日期口径 · </b>开源门禁只豁免 results/ 目录里的绝对日期，'
                      '本页因此按批次显示；精确评测日期以 results/ 原始记录为准。</div>')

    cards = [chart_card('cK', '九项核心能力 K1–K9', '同一能力项三模型并排，0–100 固定刻度，虚线是困难版 85 门槛（K1 为 95）'),
             chart_card('cOverall', '综合分', '九项能力按题库权重加总；虚线是困难版准入 90')]
    if any(r['layers'] for r in primary):
        cards.append(chart_card('cLayer', '四层得分率 L1–L4', 'L1 判断题 / L2 单场实操 / L3 八小时值守 / L4 十分钟接手'))
    cards.append(chart_card('cRadar', '能力画像（雷达）', '九项能力同一刻度；形状比绝对值更能看出偏科'))
    charts_html = '<div class="grid two">' + ''.join(cards) + '</div>'

    dims_section = ''
    if dim_rows:
        where = '主数据' if dim_rows and dim_rows[0] in primary else '附录阶段结果'
        dims_section = (
            '<section><div class="sec-head"><h2>原文七维 A–G 诊断</h2>'
            f'<span class="tiny muted">来自{where}；A–G 权重合计 {meta["weight_sum_ag"]}，'
            '归一化后只作诊断，不参与综合分</span></div>'
            + chart_card('cDims', '七维 A–G', '原文维度与九项能力是同一批检查点的两种切法，不能相加'))

    apx = ''
    if appendix:
        model = appendix[0]['model']
        apx_charts = ('<div class="grid two">'
                      + chart_card('cLayerApx', '阶段四层得分率', '按该轮 ratings 逐点重算；分母是该轮自己的检查点集合')
                      + '</div>') if any(r['layers'] for r in appendix) else ''
        apx = ('<section><div class="sec-head"><h2>附录 · 阶段进展</h2>'
               f'<span class="tiny muted">同一模型（{E(model)}）在三个评测阶段的累计结果，'
               '不是三个模型；覆盖率一路上升，分数随之上升</span></div>'
               + appendix_table(appendix, meta)
               + '<p class="note">这三份是 score.py 的完整输出，题库快照为 142 项（同一版本字符串下的补题前快照），'
               '与主数据的 157 项不能直接比。</p>'
               + apx_charts
               + '<div style="margin-top:18px">' + model_cards(appendix, meta) + '</div></section>')

    footer = (
        '<footer><h3>数据来源与口径</h3><ul>'
        f'<li><b>题库</b>：examiner/cases.json v{meta["version"]}，{meta["cases"]} 个单元、{meta["checks"]} 个检查点；'
        + '层分布 ' + '、'.join(f'{L} {meta["layers"][L]["checks"]}' for L in LAYERS) + '。</li>'
        '<li><b>评分规则</b>：1 / 0.5 / 0 / 未测=0；能力内部按层均分后等权，再按 K1–K9 权重（'
        + '、'.join(f'{k} {meta["comps"][k]["weight"]}' for k in KEYS) + '）加总。'
        f'原文 A–G 权重合计 {meta["weight_sum_ag"]}，归一化后只作诊断输出。</li>'
        '<li><b>综合分</b>：本页不抄文件里的总分，按题库权重从九项能力重算；与记录册公布值一致。</li>'
        '<li><b>门槛</b>：困难版总分≥90、K1≥95、其余能力≥85、八个困难行为门全过、检查点全测，'
        '未达线的分项在表里标▾。</li>'
        f'<li><b>源文件</b>：{E("、".join(sources))}。</li>'
        '<li><b>盐与词表无关</b>：本页由 report/build_report.py 从 results/ 与 examiner/cases.json 生成，'
        '不读取也不嵌入任何脱敏盐或词表（tools/scrub-terms*、本机 .starfix 目录均未访问），不含任何凭据。</li>'
        '<li><b>外部资源</b>：只从 cdnjs 加载固定版本 Chart.js 画图；断网时图表消失，表格里的数值仍然完整。</li>'
        '<li><b>版本比较</b>：题库版本字符串在补题前后分别对应 142 项和 157 项，比较时必须同时记录提交号/哈希，'
        '不能只按版本字符串排序。</li>'
        '<li><b>结论边界</b>：评分器只做算术，不验证证据真伪；模拟入职考通过也不证明真实舰员吞吐、'
        '真实 8 小时耐久或生产授权。公开题库是开发集，正式排名需封存实质新变体。</li>'
        '</ul></footer>')

    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>舰长 Benchmark · 评测结果</title>
<meta name="description" content="StarFix 舰长 benchmark v{meta['version']} 困难版的模型评测结果。">
<style>{CSS}</style>
</head><body><div class="shell">
<div class="mast"><div class="brand"><i>SF</i>STARFIX · 舰长 BENCHMARK</div>
<div class="tiny muted">题库 v{meta['version']} · {meta['cases']} 单元 / {meta['checks']} 检查点 · 困难版</div></div>
<div class="hero"><h1>舰长考核 · 评测结果</h1><p>{hero_sub}</p></div>
{''.join(blocks)}
<section><div class="sec-head"><h2>排行榜</h2>
<span class="tiny muted">横向可滚动；▾ = 低于困难版门槛</span></div>
{leaderboard(primary, meta, any(r.get('gates') for r in primary))}
<p class="note">综合分把未测按 0 计，覆盖率不同不可直接比高低；「达线」只查数值门槛，考官结论另见「结论」列。</p>
</section>
<section><div class="sec-head"><h2>分项对照</h2>
<span class="tiny muted">固定 0–100 刻度 · 模型固定配色 · 分项 1 位小数、综合分 2 位</span></div>
<div id="nochart">图表库没加载（离线或 CDN 不可达）。全部数值在上下方的表格里，不影响阅读。</div>
{charts_html}</section>
{dims_section}
<section><div class="sec-head"><h2>逐模型评审</h2>
<span class="tiny muted">摘要引自各自的综合评估；失分按层与检查点定位</span></div>
{model_cards(primary, meta)}</section>
{apx}
<section><div class="sec-head"><h2>题库结构</h2>
<span class="tiny muted">下表由 examiner/cases.json 直接算出</span></div>
{reference_tables(meta)}</section>
{footer}
</div>
<script src="{CHART_JS}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>const D={payload};{JS}</script>
</body></html>
"""


# ── 静态 SVG（供 README 嵌入；风格对齐 docs/img/gen_svg.py）──────────
SVG_W = 1000
SC = {'ink': '#1B1F27', 'sub': '#6B7280', 'line': '#9AA3B2', 'grid': '#E3E7EE',
      'acc': '#2563EB', 'bg': '#FFFFFF', 'card': '#F8FAFC'}
SVG_FONT = ("-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
            "'Noto Sans CJK SC','Helvetica Neue',Arial,sans-serif")


def svg_open(height, title, sub):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {height}" '
            f'width="{SVG_W}" height="{height}" font-family="{SVG_FONT}">'
            '<defs><filter id="sh" x="-5%" y="-5%" width="110%" height="120%">'
            '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#0F172A" flood-opacity="0.08"/>'
            '</filter></defs>'
            f'<rect width="{SVG_W}" height="{height}" fill="{SC["bg"]}"/>'
            f'<text x="24" y="30" font-size="17" font-weight="700" fill="{SC["ink"]}">{E(title)}</text>'
            f'<text x="24" y="50" font-size="12" fill="{SC["sub"]}">{E(sub)}</text>')


def text_w(text, size):
    """粗略字宽：CJK 按 1 个字宽，西文按 0.55。用来排图例、判断要不要截断。"""
    return size * sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in text)


def clip_text(text, size, limit):
    if text_w(text, size) <= limit:
        return text
    out = ''
    for ch in text:
        if text_w(out + ch, size) > limit - size:
            break
        out += ch
    return out + '…'


def svg_legend(rows, x, y):
    out, cx = [], x
    for r in rows:
        out.append(f'<rect x="{cx}" y="{y - 9}" width="10" height="10" rx="2.5" fill="{r["svg_color"]}"/>')
        label = clip_text(r['label'], 11.5, 190)
        out.append(f'<text x="{cx + 15}" y="{y}" font-size="11.5" fill="{SC["sub"]}">{E(label)}</text>')
        cx += 30 + text_w(label, 11.5)
    return ''.join(out)


def svg_grouped(path, title, sub, labels, sublabels, rows, values, note):
    """分组柱状图：labels 是横轴分组，rows 是并排的模型。"""
    top, bottom, left, right = 104, 76, 46, 24
    ph = 236
    height = top + ph + bottom
    o = [svg_open(height, title, sub)]
    o.append(f'<rect x="24" y="{top - 18}" width="{SVG_W - 48}" height="{ph + 44}" rx="12" '
             f'fill="{SC["bg"]}" stroke="{SC["grid"]}" filter="url(#sh)"/>')
    o.append(svg_legend(rows, 46, top - 32))
    for g in range(0, 101, 25):
        y = top + ph - ph * g / 100
        o.append(f'<line x1="{left + 18}" y1="{y:.1f}" x2="{SVG_W - right - 12}" y2="{y:.1f}" '
                 f'stroke="{SC["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{left + 8}" y="{y + 4:.1f}" font-size="10.5" fill="{SC["sub"]}" '
                 f'text-anchor="end">{g}</text>')
    plot_left, plot_right = left + 18, SVG_W - right - 12
    gw = (plot_right - plot_left) / len(labels)
    bw = min(22.0, (gw - 14) / len(rows))
    for gi, key in enumerate(labels):
        gx = plot_left + gw * gi
        for ri, r in enumerate(rows):
            v = values(r, key)
            if v is None:
                continue
            h = ph * max(0.0, min(100.0, v)) / 100
            x = gx + (gw - bw * len(rows)) / 2 + bw * ri
            o.append(f'<rect x="{x:.1f}" y="{top + ph - h:.1f}" width="{bw - 2:.1f}" height="{h:.1f}" '
                     f'rx="2.5" fill="{r["svg_color"]}"/>')
        o.append(f'<text x="{gx + gw / 2:.1f}" y="{top + ph + 18}" font-size="12" font-weight="600" '
                 f'fill="{SC["ink"]}" text-anchor="middle">{E(key)}</text>')
        if sublabels:
            o.append(f'<text x="{gx + gw / 2:.1f}" y="{top + ph + 34}" font-size="10" '
                     f'fill="{SC["sub"]}" text-anchor="middle">'
                     f'{E(clip_text(sublabels[gi], 10, gw - 6))}</text>')
    o.append(f'<line x1="{plot_left}" y1="{top + ph}" x2="{plot_right}" y2="{top + ph}" '
             f'stroke="{SC["line"]}" stroke-width="1"/>')
    o.append(f'<text x="24" y="{height - 14}" font-size="11" fill="{SC["sub"]}">{E(note)}</text>')
    o.append('</svg>')
    path.write_text(''.join(o), encoding='utf-8')
    return path


def svg_overall(path, title, sub, rows, note, floor=TOTAL_FLOOR):
    top, rowh = 96, 44
    height = top + rowh * len(rows) + 56
    o = [svg_open(height, title, sub)]
    o.append(f'<rect x="24" y="{top - 20}" width="{SVG_W - 48}" height="{rowh * len(rows) + 26}" rx="12" '
             f'fill="{SC["bg"]}" stroke="{SC["grid"]}" filter="url(#sh)"/>')
    bar_left, bar_right = 214, SVG_W - 96
    span = bar_right - bar_left
    fx = bar_left + span * floor / 100
    o.append(f'<line x1="{fx:.1f}" y1="{top - 8}" x2="{fx:.1f}" y2="{top + rowh * len(rows) - 8}" '
             f'stroke="{SC["line"]}" stroke-width="1" stroke-dasharray="4 4"/>')
    o.append(f'<text x="{fx + 5:.1f}" y="{top - 12}" font-size="10.5" fill="{SC["sub"]}">'
             f'困难版准入 {floor:.0f}</text>')
    for i, r in enumerate(rows):
        y = top + rowh * i
        o.append(f'<text x="46" y="{y + 14}" font-size="13" font-weight="600" fill="{SC["ink"]}">'
                 f'{E(r["label"])}</text>')
        o.append(f'<text x="46" y="{y + 29}" font-size="10.5" fill="{SC["sub"]}">{E(r["svg_sub"])}</text>')
        o.append(f'<rect x="{bar_left}" y="{y + 6}" width="{span}" height="16" rx="4" fill="{SC["card"]}"/>')
        v = r['score'] or 0
        o.append(f'<rect x="{bar_left}" y="{y + 6}" width="{span * max(0, min(100, v)) / 100:.1f}" '
                 f'height="16" rx="4" fill="{r["svg_color"]}"/>')
        o.append(f'<text x="{bar_right + 10}" y="{y + 19}" font-size="13" font-weight="700" '
                 f'fill="{SC["ink"]}">{fmt(v, 2)}</text>')
    o.append(f'<text x="24" y="{height - 16}" font-size="11" fill="{SC["sub"]}">{E(note)}</text>')
    o.append('</svg>')
    path.write_text(''.join(o), encoding='utf-8')
    return path


SVG_RAMP = [0.0, 0.32, 0.58, 0.74, 0.84]


def svg_prepare(rows):
    for i, r in enumerate(rows):
        r['svg_color'] = mix(SC['acc'], SVG_RAMP[min(i, len(SVG_RAMP) - 1)])
        cov = (f'{r["measured"]}/{r["total"]} 检查点'
               if r['measured'] is not None and r['total'] else '覆盖率未记录')
        r['svg_sub'] = clip_text(f'{cov} · {r["verdict"]}', 10.5, 160)


def main():
    ap = argparse.ArgumentParser(description='生成舰长 benchmark 的评测结果页与静态 SVG')
    ap.add_argument('--results', default=str(BENCH / 'results'), help='结果目录')
    ap.add_argument('--out', default=str(HERE / 'index.html'), help='输出 HTML')
    ap.add_argument('--svg-dir', default=str(REPO / 'docs/img/bench'), help='SVG 输出目录')
    ap.add_argument('--samples', action='store_true', help='强制只用 sample-*（演示/自测）')
    a = ap.parse_args()

    bank = load_bank()
    meta = bank_meta(bank)
    idx = check_index(bank)
    results = Path(a.results)
    if not results.is_dir():
        sys.exit(f'结果目录不存在：{results}')
    score_files = sorted(results.glob('*.scores.json'))
    real_files = [f for f in score_files if not f.name.startswith('sample-')]
    sample_files = [f for f in score_files if f.name.startswith('sample-')]
    records_dir = results / 'records'

    if a.samples:
        if not sample_files:
            sys.exit('--samples 指定了样例，但 results/ 下没有 sample-*.scores.json')
        primary, appendix = read_scores_files(sample_files, idx, meta), []
    else:
        record_rows = read_record_files(records_dir, meta) if records_dir.is_dir() else []
        real_rows = read_scores_files(real_files, idx, meta)
        if record_rows:
            primary, appendix = record_rows, real_rows
        elif real_rows:
            primary, appendix = real_rows, []
        elif sample_files:
            primary, appendix = read_scores_files(sample_files, idx, meta), []
        else:
            sys.exit(f'{results} 下没有可用结果（先跑 report/make_samples.py 造样例）')

    primary.sort(key=lambda r: -(r['score'] if r['score'] is not None else -1))
    appendix.sort(key=lambda r: ((r['measured'] or 0), r['score'] or 0))
    sample_mode = bool(primary) and all(r['sample'] for r in primary)
    masked = safe_dates(primary + appendix)
    colorize(primary + appendix, [primary, appendix])
    sources = [f'results/{r["file"]}' if r['kind'] == 'scores' else f'results/records/{r["file"]}'
               for r in primary + appendix]

    out = Path(a.out)
    out.write_text(render_html(primary, appendix, meta, sample_mode, masked, sources), encoding='utf-8')

    svg_prepare(primary)
    svg_dir = Path(a.svg_dir)
    svg_dir.mkdir(parents=True, exist_ok=True)
    has_dims = all(r['dims'] for r in primary)
    if has_dims:
        labels, subs = DIMS, [f'权重 {meta["dims"][d]["weight"]}' for d in DIMS]
        pick = lambda r, key: (r['dims'] or {}).get(key)
        title, sub = ('舰长考核 · 原文七维 A–G',
                      f'{len(primary)} 个结果并排；0–100 固定刻度，权重合计 {meta["weight_sum_ag"]} 归一化后作诊断')
    else:
        labels = KEYS
        subs = [meta['comps'][k]['name'] for k in KEYS]
        pick = lambda r, key: r['abilities'].get(key)
        title, sub = ('舰长考核 · 九项核心能力 K1–K9',
                      f'{len(primary)} 个模型并排；0–100 固定刻度，困难版门槛 85（K1 为 95）')
    p1 = svg_grouped(svg_dir / 'dimensions.svg', title, sub, labels, subs, primary, pick,
                     f'题库 v{meta["version"]}·{meta["checks"]} 检查点；未测按 0 计，覆盖率不同不可直接比高低。')
    p2 = svg_overall(svg_dir / 'overall.svg', '舰长考核 · 综合分',
                     '九项能力按题库权重加总；三条都是困难版未通过的校准记录',
                     primary, f'题库 v{meta["version"]}·{meta["checks"]} 检查点；'
                              f'综合分把未测按 0 计，比较前先核对覆盖率与题库快照。')

    print(f'写出 {out}（主数据 {len(primary)} 行，附录 {len(appendix)} 行'
          f'{"，示例数据" if sample_mode else ""}）')
    print(f'写出 {p1}')
    print(f'写出 {p2}')
    if WARNINGS:
        print(f'（{len(WARNINGS)} 条提醒见上）')


if __name__ == '__main__':
    main()
