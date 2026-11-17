# -*- coding: utf-8 -*-
"""舰队路径：FLEET_HOME 必填，配置里的占位符在此展开。"""
import os


def require_fleet_home():
    home = os.environ.get('FLEET_HOME')
    if not home:
        raise SystemExit('缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md')
    return home


def _user_home():
    return os.path.expanduser('~')


def expand_path(value, fleet_home=None):
    if not value or not isinstance(value, str):
        return value
    fleet_home = fleet_home or require_fleet_home()
    out = (
        value.replace('${FLEET_HOME}', fleet_home).replace('$FLEET_HOME', fleet_home)
        .replace('${HOME}', _user_home()).replace('$HOME', _user_home())
    )
    snap = os.environ.get(
        'CTX_SNAPSHOT_DIR',
        os.path.join(_user_home(), '.claude', 'ctx-snapshot'),
    )
    out = out.replace('${CTX_SNAPSHOT_DIR}', snap).replace('$CTX_SNAPSHOT_DIR', snap)
    handoff = os.environ.get('FLEET_HANDOFF_DIR', os.path.join(fleet_home, 'handoff'))
    out = out.replace('${FLEET_HANDOFF_DIR}', handoff).replace('$FLEET_HANDOFF_DIR', handoff)
    if not os.path.isabs(out):
        out = os.path.join(fleet_home, out)
    return os.path.normpath(out)