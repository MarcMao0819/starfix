#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""iTerm 标签全没之后，为各角色新开标签并按原会话续接。
用法：restore-fleet.py --list
      restore-fleet.py <key> [<key> …]"""
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
STATE = expand_path(os.environ.get('FLEET_RECOVERY_TABLE', os.path.join(home, 'recovery', 'fleet-table.json')))
SNAP = os.path.join(expand_path(os.environ.get('CTX_SNAPSHOT_DIR', '${CTX_SNAPSHOT_DIR}')), 'by-iterm')


def load_table():
    data = json.load(open(ROSTER, encoding='utf-8'))
    out = {}
    for key, item in data.items():
        if key.startswith('_') or not isinstance(item, dict):
            continue
        out[key] = (item['name'], '占位窗口', expand_path(item['workdir']), list(item.get('resume_args') or []))
    return out


TABLE = load_table()


def state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def last_window(key):
    return (state().get(key) or {}).get('iterm', TABLE[key][1])[:8].upper()


def snapshot(win8):
    for f in glob.glob(os.path.join(SNAP, '*.json')):
        if os.path.basename(f).upper().startswith(win8):
            try:
                return (
                    json.load(open(f, encoding='utf-8')).get('session_id', ''),
                    int((time.time() - os.path.getmtime(f)) / 60),
                )
            except Exception:
                pass
    return '', None


import iterm2  # noqa: E402


async def lines(s):
    c = await s.async_get_screen_contents()
    ls = [c.line(i).string.replace('\x00', ' ').rstrip() for i in range(c.number_of_lines)]
    return [l for l in ls if l.strip()]


async def main(conn):
    if '--list' in sys.argv:
        for k in TABLE:
            cur, age = snapshot(last_window(k))
            print(k, TABLE[k][0], '上次窗口', last_window(k), '会话', cur[:8] or '找不到', '快照 %s 分钟前' % age)
        return
    app = await iterm2.async_get_app(conn)
    window = app.current_terminal_window or await iterm2.Window.async_create(conn)
    st = state()
    for k in sys.argv[1:]:
        if k.startswith('-'):
            continue
        if k not in TABLE:
            print(k, 'SKIP 未知角色')
            continue
        name, _, cwd, flags = TABLE[k]
        old = last_window(k)
        cur, _ = snapshot(old)
        if not cur:
            print(k, name, 'SKIP 会话号找不到')
            continue
        tab = await window.async_create_tab()
        s = None
        for _ in range(10):
            s = tab.current_session or (tab.sessions[0] if tab.sessions else None)
            if s:
                break
            await asyncio.sleep(0.6)
            tab = (await iterm2.async_get_app(conn)).get_tab_by_id(tab.tab_id) or tab
        if not s:
            print(k, name, 'SKIP 新标签没起来')
            continue
        await s.async_set_name(name)
        await asyncio.sleep(1.5)
        cmd = 'cd ' + shlex.quote(cwd) + ' && claude --resume ' + cur + ''.join(' ' + shlex.quote(f) for f in flags)
        await s.async_send_text(cmd + '\r')
        ok = False
        for _ in range(110):
            await asyncio.sleep(1)
            txt = '\n'.join((await lines(s))[-30:])
            if 'trust this folder' in txt and 'Enter to confirm' in txt:
                sel = [l for l in txt.splitlines() if l.strip().startswith('❯')]
                yes = bool(sel) and 'Yes, I trust' in sel[-1]
                await s.async_send_text('\r' if yes else '\x1b[B')
                await asyncio.sleep(2 if yes else 1)
                continue
            if re.search(r'\[[^\]]+\] │ ', txt) and '❯' in txt:
                ok = True
                break
        txt = '\n'.join((await lines(s))[-12:])
        mode = 'bypass' if 'bypass permissions' in txt else ('auto' if 'auto mode' in txt else '?')
        st[k] = {
            'name': name,
            'iterm': s.session_id.upper(),
            'old': old,
            'session': cur,
            'cmd': cmd,
            'at': time.strftime('%m-%d %H:%M'),
            'ok': ok,
        }
        json.dump(st, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(k, name, 'OK' if ok else 'UNSURE', mode, '新窗口', s.session_id.upper()[:8])


iterm2.run_until_complete(main)