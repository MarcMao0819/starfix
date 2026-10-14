#!/usr/bin/env python3
"""ctx-handoff-guard.py —— 舰长上下文水位守卫：到阈值自动交接，不靠模型记得。

一个脚本挂三个 Claude Code hook 事件（stdin 是 hook JSON）：
  PostToolUse / UserPromptSubmit：读状态栏写的用量快照；本「压缩周期」第一次到阈值（默认 85%）时
      ① 不经模型、机械跑 handoff-snapshot.sh，把激活器/待答/台账/监听写进 HANDOFF-<session_id>.md 的快照段；
      ② 往模型上下文插一条指令：立刻补写该文件顶部手写段；到第二档（默认 92%）再快照、再提醒一次。
  Stop：已经触发过、但手写段（分隔线以上）在触发之后没改过 → 拦一次（stop_hook_active 为真时不再拦）。
用量回落到「阈值 - 回落差」以下（通常是压缩之后）视为新周期，状态清零，下次到阈值重新触发。

只对名单里的会话生效：名单文件每行一个 iTerm 会话 UUID 或 Claude session_id（# 开头为注释）；
不在名单的会话零输出。任何异常都静默退出 0（只记 hook.log），绝不影响工具调用。

环境变量（都可选，除 FLEET_HOME）：
  FLEET_HOME                 交接文件目录的上级（HANDOFF 写在 $FLEET_HOME/handoff/）
  CTX_SNAPSHOT_DIR           用量快照目录，默认 ~/.claude/ctx-snapshot
  CTX_GUARD_LIST             会话名单，默认 ~/.claude/hooks/ctx-guard-sessions.txt
  CTX_GUARD_STATE_DIR        状态目录，默认同 CTX_SNAPSHOT_DIR
  CTX_HANDOFF_THRESHOLD      第一档，默认 85
  CTX_HANDOFF_THRESHOLD2     第二档，默认 92
  CTX_HANDOFF_RESET_GAP      回落差，默认 25（用量 < 阈值-25 视为已压缩、新周期）
"""
import json
import os
import subprocess
import sys
import time

SEP = '<!-- ===== 以下为机械快照，脚本每次覆盖；以上为舰长手写判断 ===== -->'
HERE = os.path.dirname(os.path.abspath(__file__))


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def log(fleet_home, msg):
    try:
        hd = os.path.join(fleet_home, 'handoff')
        os.makedirs(hd, exist_ok=True)
        with open(os.path.join(hd, 'hook.log'), 'a', encoding='utf-8') as f:
            f.write(time.strftime('%F %T') + ' ctx-guard ' + msg + '\n')
    except Exception:
        pass


def in_list(sid):
    path = os.environ.get('CTX_GUARD_LIST', os.path.expanduser('~/.claude/hooks/ctx-guard-sessions.txt'))
    try:
        entries = set()
        for line in open(path, encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line:
                entries.add(line.split()[0])
    except OSError:
        return False
    iterm = os.environ.get('ITERM_SESSION_ID', '')
    iterm_uuid = iterm.split(':', 1)[-1] if iterm else ''
    return sid in entries or (iterm_uuid and iterm_uuid in entries)


def read_used(sid):
    d = os.environ.get('CTX_SNAPSHOT_DIR', os.path.expanduser('~/.claude/ctx-snapshot'))
    try:
        data = json.load(open(os.path.join(d, sid + '.json'), encoding='utf-8'))
        used = (data.get('context_window') or {}).get('used_percentage')
        return int(used) if used is not None else None
    except Exception:
        return None


def state_path(sid):
    d = os.environ.get('CTX_GUARD_STATE_DIR') or os.environ.get('CTX_SNAPSHOT_DIR') or os.path.expanduser('~/.claude/ctx-snapshot')
    return os.path.join(d, sid + '.guard.json')


def load_state(sid):
    try:
        return json.load(open(state_path(sid), encoding='utf-8'))
    except Exception:
        return {}


def save_state(sid, st):
    p = state_path(sid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, p)


def heartbeat(sid, event):
    """守卫自身在跑的证据：<sid>.guard.seen 最多每 60 秒刷新一次（守卫失效时看它停没停）。"""
    p = state_path(sid)[:-len('.json')] + '.seen'
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 60:
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(time.strftime('%F %T') + ' ' + event + '\n')
    except Exception:
        pass


def handoff_file(fleet_home, sid):
    return os.path.join(fleet_home, 'handoff', 'HANDOFF-' + sid + '.md')


def manual_section_digest(path):
    """手写段（分隔线以上）的内容指纹；文件不存在返回空串。"""
    try:
        text = open(path, encoding='utf-8').read()
    except OSError:
        return ''
    head = text.split(SEP, 1)[0]
    import hashlib
    return hashlib.sha256(head.encode('utf-8')).hexdigest()


def run_snapshot(fleet_home, sid):
    hd = os.path.join(fleet_home, 'handoff')
    os.makedirs(hd, exist_ok=True)
    env = dict(os.environ)
    env['FLEET_HANDOFF'] = handoff_file(fleet_home, sid)
    try:
        r = subprocess.run(['bash', os.path.join(HERE, 'handoff-snapshot.sh')], env=env,
                           capture_output=True, text=True, timeout=40)
        ok = r.returncode == 0
    except Exception as exc:  # 快照失败也要提醒模型，只是提示里写明
        log(fleet_home, f'snapshot error sid={sid} {exc!r}')
        ok = False
    try:
        link = os.path.join(hd, 'HANDOFF-latest.md')
        if os.path.islink(link) or not os.path.exists(link):
            if os.path.islink(link):
                os.unlink(link)
            os.symlink(env['FLEET_HANDOFF'], link)
    except Exception:
        pass
    return ok


def emit_context(event, text):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}, ensure_ascii=False))


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw or '{}')
    except Exception:
        return 0
    sid = data.get('session_id') or ''
    event = data.get('hook_event_name') or ''
    fleet_home = os.environ.get('FLEET_HOME', '')
    if not sid or not fleet_home or not in_list(sid):
        return 0
    heartbeat(sid, event)

    th1 = env_int('CTX_HANDOFF_THRESHOLD', 85)
    th2 = env_int('CTX_HANDOFF_THRESHOLD2', 92)
    gap = env_int('CTX_HANDOFF_RESET_GAP', 25)
    st = load_state(sid)
    used = read_used(sid)
    path = handoff_file(fleet_home, sid)

    if event == 'Stop':
        if data.get('stop_hook_active'):
            return 0
        if st.get('level', 0) >= th1 and not st.get('stop_blocked'):
            if manual_section_digest(path) == st.get('manual_digest_at_trigger', ''):
                st['stop_blocked'] = True
                save_state(sid, st)
                log(fleet_home, f'stop blocked sid={sid} used={used}')
                print(json.dumps({'decision': 'block', 'reason': (
                    f'上下文已到 {st.get("used_at_trigger")}%：交接文件 {path} 顶部手写段在触发之后还没更新。'
                    '先把手写段补齐（现状一句话 / 在飞与判据 / 待答 / 刚发生的教训 / 下一步三件 / 风险与备份 / 坐标），'
                    '写完再结束本轮。')}, ensure_ascii=False))
        return 0

    if event not in ('PostToolUse', 'UserPromptSubmit') or used is None:
        return 0

    level = st.get('level', 0)
    if used < th1 - gap and level:
        log(fleet_home, f'reset sid={sid} used={used} (prev level {level})')
        st = {}
        save_state(sid, st)
        level = 0

    target = th2 if used >= th2 else (th1 if used >= th1 else 0)
    if not target or target <= level:
        return 0

    ok = run_snapshot(fleet_home, sid)
    st.update({'level': target, 'used_at_trigger': used, 'triggered_at': time.strftime('%F %T'),
               'manual_digest_at_trigger': manual_section_digest(path), 'stop_blocked': False})
    save_state(sid, st)
    log(fleet_home, f'trigger level={target} sid={sid} used={used} snapshot_ok={ok}')
    snap = f'机械快照已自动写入 {path}' if ok else f'机械快照自动写入失败（见 {os.path.join(fleet_home, "handoff", "hook.log")}），请手动跑 handoff-snapshot.sh'
    urgency = '第二档，压缩随时可能发生' if target >= th2 else '第一档'
    emit_context(event, (
        f'⚠ 上下文用量 {used}%（{urgency}，阈值 {target}%）。{snap}。'
        f'现在立刻更新这份文件分隔线以上的手写段：现状一句话、在飞与判据、待答、刚发生的教训、下一步三件、风险与备份、坐标；'
        '写完再继续手上的事。压缩后会自动把这份交接读回上下文。'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
