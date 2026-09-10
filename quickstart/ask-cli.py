#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ask-cli.py —— 没有浮窗时的决策面板替身（quickstart）。

它替代的是正式版里的 scripts/askpanel/（macOS 请示浮窗）：
指挥官看未答题 → 点一下 → **只追加一行**到 ask-inbox.jsonl；
之后由 scripts/ask-inbox-apply.sh 读到这一行、调 task-activator.py ask answer 落库解锁。

三条边界，换成浮窗/IM 机器人时也一样：
  1. 决策题的真相在激活器里，不在这里。列表由 task-activator.py ask list 出，
     本脚本只做筛选显示，不自己解析 task-activator.json——两份解析迟早会打架。
  2. 收件箱**只追加**。原地改写会让 ask-inbox-apply.sh 的 tail -F 重放全部历史答复、
     冲掉所有任务的 blocker（真实事故）。
  3. 落库不是本脚本干的。answer 只负责把一行 JSON 追加进收件箱，
     解锁由 ask-inbox-apply.sh + task-activator.py 完成，链路和浮窗完全一致。

用法：
  ask-cli.py list                                   列出未答的 Q
  ask-cli.py answer Q01 A                           追加一行答复到收件箱
  ask-cli.py add Q01 "<题干+A/B/C+推荐>" --tasks T1,T2 [--who Owner]

环境变量：
  FLEET_HOME        必填，无默认值
  FLEET_ASK_INBOX   收件箱，默认 $FLEET_HOME/ask-inbox.jsonl
  ACTIVATOR_JSON    激活器数据文件（透传给 task-activator.py，默认 $FLEET_HOME/task-activator.json）
  QS_ACTIVATOR_PY   task-activator.py 路径，默认 <仓根>/scripts/task-activator.py
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

FLEET_HOME = os.environ.get('FLEET_HOME')
if not FLEET_HOME:
    raise SystemExit('缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md')

INBOX = os.environ.get('FLEET_ASK_INBOX', os.path.join(FLEET_HOME, 'ask-inbox.jsonl'))
ACTIVATOR = os.environ.get(
    'QS_ACTIVATOR_PY',
    str(pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'task-activator.py'),
)


def activator(*args):
    """调 task-activator.py（ACTIVATOR_JSON 等环境变量原样透传给子进程）。"""
    if not os.path.exists(ACTIVATOR):
        raise SystemExit(f'找不到任务激活器：{ACTIVATOR}\n用 QS_ACTIVATOR_PY 指到 scripts/task-activator.py')
    r = subprocess.run([sys.executable, ACTIVATOR, *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f'任务激活器返回 {r.returncode}：{" ".join(args)}')
    return r.stdout


def unanswered():
    """从 `ask list` 的输出里挑出未答题。返回 [(编号, 摘要行, 题干行)]。"""
    lines = activator('ask', 'list').splitlines()
    out = []
    for i, ln in enumerate(lines):
        if '⏳待答' not in ln:
            continue
        qid = ln.strip().split('|', 1)[0].strip()
        detail = lines[i + 1].rstrip() if i + 1 < len(lines) and '⏳待答' not in lines[i + 1] and '✅已答' not in lines[i + 1] else ''
        out.append((qid, ln.rstrip(), detail))
    return out


def cmd_list(_a):
    qs = unanswered()
    if not qs:
        print('决策面板已清零：没有未答的题。')
        return
    print(f'待答 {len(qs)} 道（答复用：ask-cli.py answer <编号> <答复>）')
    for _qid, head, detail in qs:
        print(head)
        if detail:
            print(detail)


def cmd_answer(a):
    qids = [q[0] for q in unanswered()]
    if a.qid not in qids:
        raise SystemExit(
            f'决策面板里没有待答的 {a.qid}（当前待答：{"、".join(qids) or "无"}）。\n'
            '编号写错就会答到空处；先跑 ask-cli.py list 看编号。'
        )
    line = json.dumps(
        {
            'qid': a.qid,
            'answer': a.answer,
            'ts': datetime.datetime.now().strftime('%F %H:%M:%S'),
            'via': 'ask-cli',
        },
        ensure_ascii=False,
    )
    # 只追加：'a' 模式 + 一行一条。任何形式的重写都不许出现在这里。
    os.makedirs(os.path.dirname(INBOX) or '.', exist_ok=True)
    with open(INBOX, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(f'📤 已追加到收件箱：{INBOX}')
    print(f'   {line}')
    print('   落库与解锁由 scripts/ask-inbox-apply.sh 接手（它必须先挂着，tail -n0 只认新行）。')


def cmd_add(a):
    args = ['ask', 'add', a.qid, a.question]
    if a.tasks:
        args += ['--tasks', a.tasks]
    if a.who:
        args += ['--who', a.who]
    sys.stdout.write(activator(*args))


p = argparse.ArgumentParser(description='决策面板替身（浮窗的命令行版）')
sp = p.add_subparsers(dest='cmd')
sp.add_parser('list', help='列出未答的决策题')
x = sp.add_parser('answer', help='追加一行答复到收件箱（只追加）')
x.add_argument('qid')
x.add_argument('answer')
x = sp.add_parser('add', help='新增一道决策题（转交 task-activator.py ask add）')
x.add_argument('qid')
x.add_argument('question')
x.add_argument('--tasks')
x.add_argument('--who')

a = p.parse_args()
{'list': cmd_list, 'answer': cmd_answer, 'add': cmd_add}.get(a.cmd, lambda _: p.print_help())(a)
