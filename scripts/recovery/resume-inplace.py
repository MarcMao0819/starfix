#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在原 iTerm 标签里按原会话续接 Claude。
用法：resume-inplace.py --list
      resume-inplace.py <窗口号前8位> …"""
import asyncio
import glob
import json
import os
import re
import shlex
import sys
import time

from _paths import expand_path, require_fleet_home

require_fleet_home()
home = os.environ['FLEET_HOME']
ROSTER = expand_path(os.environ.get('FLEET_ROSTER_JSON', os.path.join(home, 'recovery', 'fleet-roster.json')))
TABLE_PATH = expand_path(os.environ.get('FLEET_RECOVERY_TABLE', os.path.join(home, 'recovery', 'fleet-table.json')))
SNAP = os.path.join(expand_path(os.environ.get('CTX_SNAPSHOT_DIR', '${CTX_SNAPSHOT_DIR}')), 'by-iterm')


def load_roster():
    data = json.load(open(ROSTER, encoding='utf-8'))
    out = {}
    for key, item in data.items():
        if key.startswith('_') or not isinstance(item, dict):
            continue
        out[key] = (item['name'], expand_path(item['workdir']), list(item.get('resume_args') or []))
    return out


def table_by_iterm():
    try:
        st = json.load(open(TABLE_PATH, encoding='utf-8'))
    except Exception:
        return {}
    tbl = {}
    for key, val in st.items():
        if key.startswith('_') or not isinstance(val, dict):
            continue
        iterm = (val.get('iterm') or '')[:8].upper()
        if not iterm:
            continue
        name = val.get('name') or key
        parts = shlex.split(val.get('cmd') or '')
        cwd = home
        flags = []
        if len(parts) >= 4 and parts[0] == 'cd' and '--resume' in parts:
            cwd = parts[1]
            idx = parts.index('--resume')
            flags = parts[idx + 2 :] if idx + 2 < len(parts) else []
        roster = load_roster().get(key)
        if roster:
            name, cwd, flags = roster[0], roster[1], roster[2]
        tbl[iterm] = (name, cwd, flags)
    return tbl


def build_table():
    tbl = table_by_iterm()
    if tbl:
        return tbl
    roster = load_roster()
    try:
        st = json.load(open(TABLE_PATH, encoding='utf-8'))
    except Exception:
        return {}
    out = {}
    for key, val in st.items():
        if key.startswith('_') or not isinstance(val, dict):
            continue
        iterm = (val.get('iterm') or '')[:8].upper()
        if len(iterm) != 8 or key not in roster:
            continue
        out[iterm] = (roster[key][0], roster[key][1], roster[key][2])
    return out


TABLE = build_table()


def snapshot(sid8):
    for f in glob.glob(os.path.join(SNAP, '*.json')):
        if os.path.basename(f).upper().startswith(sid8):
            try:
                d = json.load(open(f, encoding='utf-8'))
                return d.get('session_id', ''), int((time.time() - os.path.getmtime(f)) / 60)
            except Exception:
                pass
    return '', None


import iterm2  # noqa: E402


async def lines(s):
    c = await s.async_get_screen_contents()
    ls = [c.line(i).string.replace('\x00', ' ').rstrip() for i in range(c.number_of_lines)]
    return [l for l in ls if l.strip()]


async def main(conn):
    app = await iterm2.async_get_app(conn)
    sess = {x.session_id.upper()[:8]: x for w in app.terminal_windows for t in w.tabs for x in t.sessions}
    if '--list' in sys.argv:
        for k, (role, cwd, flags) in TABLE.items():
            s = sess.get(k)
            cur, age = snapshot(k)
            if not s:
                print(k, role, '标签不在')
                continue
            ls = await lines(s)
            tail = '\n'.join(ls[-8:])
            state = (
                'Claude在跑'
                if re.search(r'\[[^\]]+\] │ ', tail)
                else ('命令行' if ls and ls[-1].rstrip().endswith(('%', '$')) else '其他')
            )
            print(
                k,
                role,
                state,
                '会话',
                cur[:8] or '无',
                '快照%s分前' % age if age is not None else '快照未知',
                '|',
                (ls[-1] if ls else '')[-40:],
            )
        return
    for k in [a.upper() for a in sys.argv[1:] if not a.startswith('-')]:
        role, cwd, flags = TABLE.get(k, ('?', home, []))
        s = sess.get(k)
        cur, _ = snapshot(k)
        if not s or not cur:
            print(k, role, 'SKIP 标签或会话号找不到')
            continue
        ls = await lines(s)
        if not ls or not ls[-1].rstrip().endswith(('%', '$')):
            print(k, role, 'SKIP 不在命令行')
            continue
        cmd = 'cd ' + shlex.quote(cwd) + ' && claude --resume ' + cur + ''.join(' ' + shlex.quote(f) for f in flags)
        await s.async_send_text(cmd + '\r')
        ok = False
        for _ in range(110):
            await asyncio.sleep(1)
            txt = '\n'.join((await lines(s))[-30:])
            if 'trust this folder' in txt and 'Enter to confirm' in txt:
                sel = [l for l in txt.splitlines() if l.strip().startswith('❯')]
                await s.async_send_text('\r' if sel and 'Yes, I trust' in sel[-1] else '\x1b[B')
                await asyncio.sleep(2 if sel and 'Yes, I trust' in sel[-1] else 1)
                continue
            if re.search(r'\[[^\]]+\] │ ', txt) and '❯' in txt:
                ok = True
                break
        txt = '\n'.join((await lines(s))[-12:])
        mode = 'bypass' if 'bypass permissions' in txt else ('auto' if 'auto mode' in txt else '?')
        print(k, role, 'OK' if ok else 'UNSURE', mode)


iterm2.run_until_complete(main)