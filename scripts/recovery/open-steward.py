#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开一个新标签跑上下文管家（iTerm 重开后用）。"""
import asyncio
import os

from _paths import expand_path, require_fleet_home

require_fleet_home()
STEWARD_DIR = expand_path(os.environ.get('STEWARD_DIR', os.path.join(os.path.dirname(__file__), '..', 'steward')))
RUN_SH = os.path.join(STEWARD_DIR, 'run.sh')

import iterm2  # noqa: E402


async def main(conn):
    app = await iterm2.async_get_app(conn)
    w = app.current_terminal_window
    tab = await w.async_create_tab()
    s = None
    for _ in range(10):
        s = tab.current_session or (tab.sessions[0] if tab.sessions else None)
        if s:
            break
        await asyncio.sleep(0.6)
        tab = (await iterm2.async_get_app(conn)).get_tab_by_id(tab.tab_id) or tab
    await s.async_set_name('上下文管家')
    await asyncio.sleep(1.5)
    await s.async_send_text('bash ' + RUN_SH + '\r')
    await asyncio.sleep(6)
    print('管家标签', s.session_id.upper()[:8])


iterm2.run_until_complete(main)