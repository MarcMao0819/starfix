#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""往指定 iTerm 标签里敲一行并回车（只用于停在命令行的标签）。用法：send-text.py <窗口号前8位> <命令>"""
import asyncio
import sys

from _paths import require_fleet_home

require_fleet_home()
if len(sys.argv) < 3:
    raise SystemExit('用法：send-text.py <窗口号前8位> <命令>')
SID, TEXT = sys.argv[1].upper(), sys.argv[2]
import iterm2  # noqa: E402


async def main(conn):
    app = await iterm2.async_get_app(conn)
    s = next(
        (
            x
            for w in app.terminal_windows
            for t in w.tabs
            for x in t.sessions
            if x.session_id.upper().startswith(SID)
        ),
        None,
    )
    if not s:
        print('NOT_FOUND')
        return
    c = await s.async_get_screen_contents()
    ls = [c.line(i).string.replace('\x00', ' ').rstrip() for i in range(c.number_of_lines)]
    ls = [l for l in ls if l.strip()]
    if not ls or not ls[-1].rstrip().endswith(('%', '$')):
        print('SKIP 不在命令行')
        return
    await s.async_send_text(TEXT + '\r')
    await asyncio.sleep(4)
    c = await s.async_get_screen_contents()
    print(
        [c.line(i).string.replace('\x00', ' ').rstrip() for i in range(c.number_of_lines) if c.line(i).string.strip()][
            -1
        ][:120]
    )


iterm2.run_until_complete(main)