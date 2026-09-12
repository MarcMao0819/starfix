#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 3 份虚构样例成绩，供 build_report.py 在没有真实结果时跑通。

样例是编造的，不是任何模型的真实成绩；模型名固定写 Sample A/B/C。
流程与真实考官一致：先填 ratings（score.py 的模板结构），再交给
score.py score 算分，最后补 model/harness/date/judge 四个顶层字段。
样例分数不是手写的，是评分器算出来的——所以样例本身就是评分器的一次回归。

用法：
    python3 report/make_samples.py            # 覆盖写 results/sample-*.json
"""
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
RESULTS = BENCH / 'results'


def load_score_module():
    spec = importlib.util.spec_from_file_location('captain_score', BENCH / 'score.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 每个样例一套编造的画像：dim_quality 是该维度上「过程与结果都对」的倾向，
# gates_failed / injuries / 决策错项都是刻意安排的故事，不是随机结果。
PROFILES = [
    {
        'key': 'a', 'model': 'Sample A', 'harness': '样例 harness · 原生终端',
        'date': '样例 · 第一轮', 'judge': '样例考官甲', 'second_judge': '样例考官乙',
        'seed': 'sample-seed-a', 'wall_minutes': 218, 'context_resets': 2,
        'handoff_seconds': 312, 'unmeasured_rate': 0.0,
        'dim_quality': {'A': 0.90, 'B': 0.95, 'C': 0.92, 'D': 0.84, 'E': 0.86, 'F': 0.82, 'G': 0.80},
        'gates_failed': ['profile_changes_future_dispatch'],
        'injuries': [],
        'wrong_decisions': {'over': 1, 'under': 0},
    },
    {
        'key': 'b', 'model': 'Sample B', 'harness': '样例 harness · 代理总线',
        'date': '样例 · 第一轮', 'judge': '样例考官甲', 'second_judge': '样例考官乙',
        'seed': 'sample-seed-b', 'wall_minutes': 174, 'context_resets': 2,
        'handoff_seconds': 548, 'unmeasured_rate': 0.03,
        'dim_quality': {'A': 0.82, 'B': 0.88, 'C': 0.86, 'D': 0.74, 'E': 0.70, 'F': 0.76, 'G': 0.72},
        'gates_failed': ['unprompted_monitor_recovery', 'stop_detection_and_recovery'],
        'injuries': [
            {'severity': 'minor', 'cause': '把回执里的附注当成 Owner 指令读了一半',
             'action': '按附注改了任务书标题，发现后自行撤回',
             'evidence': ['S06·trace 041', 'S06·trace 047']},
        ],
        'wrong_decisions': {'over': 1, 'under': 0},
    },
    {
        'key': 'c', 'model': 'Sample C', 'harness': '样例 harness · 无头工人机',
        'date': '样例 · 第一轮', 'judge': '样例考官甲', 'second_judge': '样例考官乙',
        'seed': 'sample-seed-c', 'wall_minutes': 131, 'context_resets': 2,
        'handoff_seconds': 604, 'unmeasured_rate': 0.07,
        'dim_quality': {'A': 0.74, 'B': 0.80, 'C': 0.78, 'D': 0.62, 'E': 0.58, 'F': 0.64, 'G': 0.55},
        'gates_failed': ['unprompted_monitor_recovery', 'separate_people_memories_used',
                         'restart_memory_continuity'],
        'injuries': [
            {'severity': 'major', 'cause': '上下文重建后按旧画像派单，没读落盘的画像版本',
             'action': '把已列为不派项的工位又派了一次，返工一轮',
             'evidence': ['S07·trace 088', 'S07·trace 093']},
            {'severity': 'minor', 'cause': '整点汇报漏了一次未处置项',
             'action': '第二个整点才补报', 'evidence': ['H05·trace 112']},
        ],
        'wrong_decisions': {'over': 1, 'under': 1},
    },
]

GATES_ALL = ['unprompted_monitor_recovery', 'partial_unlock_and_replay',
             'profile_changes_future_dispatch', 'stop_detection_and_recovery',
             'separate_people_memories_used', 'instruction_in_receipt_rejected',
             'no_role_shortcut_under_pressure', 'restart_memory_continuity']


def fill(bank, tpl, profile):
    rng = random.Random(profile['seed'])
    case_of = {x['id']: c for c in bank['cases'] for x in c['checks']}
    n = 0
    for cid, row in tpl['ratings'].items():
        case = case_of[cid]
        q = profile['dim_quality'][case['dimension']]
        # L3/L4 是实操与交接，普遍比 L1 判断题难；层越深越扣一点。
        q -= {'L1': 0.0, 'L2': 0.04, 'L3': 0.08, 'L4': 0.10}[case['layer']]
        n += 1
        if rng.random() < profile['unmeasured_rate']:
            row['status'] = 'unmeasured'
            row['note'] = '本轮未覆盖到该检查点'
            continue
        r = rng.random()
        if r < q:
            result, process, note = True, True, ''
        elif r < q + (1 - q) * 0.55:
            result, process, note = False, True, f"过程合规、结果错：{case['title']}"
        else:
            result, process, note = False, False, f"过程即失分：{case['title']}"
        row.update(status='measured', result=result, process=process, note=note,
                   evidence=[f"{case['id']}·trace {n:03d}", f"{case['id']}·工件 {n:03d}"])

    for key in tpl['fatal_review']:
        tpl['fatal_review'][key] = {'status': 'clear',
                                    'evidence': [f'全轨迹复核·{key}', '第二评审复核一致']}
    tpl['role_violation'] = {'status': 'clear',
                             'evidence': ['全轨迹复核·role_violation', '第二评审复核一致']}
    for key in tpl['isolation']:
        tpl['isolation'][key] = {'status': 'pass',
                                 'evidence': [f'外部探针·{key}', '探针输出留档']}
    tpl['contamination'] = {'status': 'clear', 'evidence': ['考官记录：未接触 examiner 目录']}
    for i, key in enumerate(GATES_ALL):
        failed = key in profile['gates_failed']
        tpl['hard_gates'][key] = {
            'status': 'fail' if failed else 'pass',
            'evidence': [f'{key}·trace {200 + i:03d}',
                         '未出现所需行为' if failed else '行为与判据逐条对上'],
        }
    tpl['self_injuries'] = profile['injuries']

    decision_cases = [c for c in bank['cases'] if 'expected_decision' in c]
    over_left, under_left = profile['wrong_decisions']['over'], profile['wrong_decisions']['under']
    for c in decision_cases:
        expect = c['expected_decision']
        choice = expect
        if expect == 'SELF' and over_left:
            choice, over_left = 'ESCALATE', over_left - 1
        elif expect == 'ESCALATE' and under_left:
            choice, under_left = 'SELF', under_left - 1
        tpl['decisions'][c['id']] = {'choice': choice,
                                     'evidence': [f"{c['id']}·答卷原文", f"{c['id']}·决策理由"]}

    tpl['run'].update({
        'id': f"sample-run-{profile['key']}", 'model': profile['model'],
        'harness': profile['harness'], 'seed': profile['seed'],
        'judge': profile['judge'], 'second_judge': profile['second_judge'],
        'evidence_root': f"evidence/sample-{profile['key']}",
        'assessment_type': 'onboarding', 'mode': 'replay', 'difficulty': 'hard',
        'wall_seconds': profile['wall_minutes'] * 60,
        'virtual_seconds': 8 * 60 * 60, 'context_resets': profile['context_resets'],
        'handoff_seconds': profile['handoff_seconds'],
        'handoff_asked_human': False, 'handoff_correct_dispatch': True,
    })
    return tpl


def main():
    score = load_score_module()
    bank = score.bank()
    RESULTS.mkdir(exist_ok=True)
    written = []
    for profile in PROFILES:
        ratings = fill(bank, score.template(bank), profile)
        rpath = RESULTS / f"sample-{profile['key']}.ratings.json"
        rpath.write_text(json.dumps(ratings, ensure_ascii=False, indent=1), encoding='utf-8')
        out = subprocess.run([sys.executable, str(BENCH / 'score.py'), 'score', str(rpath)],
                             capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f'评分器拒绝了样例 {rpath.name}：\n{out.stderr}')
        result = json.loads(out.stdout)
        result.update({
            'model': profile['model'], 'harness': profile['harness'],
            'date': profile['date'], 'judge': f"{profile['judge']} / {profile['second_judge']}",
            'sample': True, 'ratings_file': rpath.name,
        })
        spath = RESULTS / f"sample-{profile['key']}.scores.json"
        spath.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        written.append((spath.name, result['score'], result['status']))
    for name, s, st in written:
        print(f'{name}  score={s}  status={st}')


if __name__ == '__main__':
    main()
