#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登记表里的窗口不在了，但同一会话已在别的标签里开着：把登记改过去，避免开第二份。
用法：adopt-open.py [--dry] [fleet-table.json 路径]"""
import asyncio
import glob
import json
import os
import sys
import time

from _paths import expand_path, require_fleet_home

DRY = '--dry' in sys.argv
args = [a for a in sys.argv[1:] if a != '--dry']
require_fleet_home()
home = os.environ['FLEET_HOME']
default_table = os.path.join(home, 'recovery', 'fleet-table.json')
TABLE = expand_path(args[0]) if args else os.environ.get('FLEET_RECOVERY_TABLE', default_table)
SNAP = os.path.join(
    expand_path(os.environ.get('CTX_SNAPSHOT_DIR', '${CTX_SNAPSHOT_DIR}')),
    'by-iterm',
)
live = set()

import iterm2  # noqa: E402


async def main(conn):
    app = await iterm2.async_get_app(conn)
    for w in app.terminal_windows:
        for t in w.tabs:
            for s in t.sessions:
                live.add(s.session_id.upper())


iterm2.run_until_complete(main)

holder = {}
for f in glob.glob(os.path.join(SNAP, '*.json')):
    win = os.path.basename(f)[:-5].upper()
    if win not in live:
        continue
    try:
        sid = json.load(open(f, encoding='utf-8')).get('session_id', '')
    except Exception:
        continue
    if sid and (sid not in holder or os.path.getmtime(f) > holder[sid][1]):
        holder[sid] = (win, os.path.getmtime(f))

table = json.load(open(TABLE, encoding='utf-8'))
changed = False
for key, v in table.items():
    if key.startswith('_') or not isinstance(v, dict):
        continue
    if (v.get('iterm') or '').upper() in live:
        continue
    sid = v.get('session', '')
    prefix = (v.get('iterm') or '')[:8]
    for f in glob.glob(os.path.join(SNAP, prefix + '*.json')) + glob.glob(
        os.path.join(SNAP, prefix.lower() + '*.json')
    ):
        try:
            sid = json.load(open(f, encoding='utf-8')).get('session_id', '') or sid
        except Exception:
            pass
    if sid in holder:
        win = holder[sid][0]
        print(
            '%s 已在标签 %s 里开着，登记%s改成它'
            % (v.get('name', key), win[:8], '会' if DRY else '已')
        )
        if not DRY:
            v.update(old=(v.get('iterm') or '')[:8], iterm=win, session=sid, at=time.strftime('%m-%d %H:%M'))
            changed = True
if changed:
    json.dump(table, open(TABLE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)