#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""上下文管家 v2：每个舰长一台状态机，到线 → 通知写交接卡 → 核卡和 HANDOFF-READY → /clear → 叫新会话读卡。

规格：specs/context-handoff.md。运行：
  steward2.py --loop            常驻（由 run.sh 看着；要 CTXSTEWARD2_LIVE=1 才肯往窗口打字）
  steward2.py --once            跑一轮
  steward2.py --dry-run ...     只读屏、只算，把本来会打的字写进日志，不往任何窗口打字
  steward2.py --only <key> ...  只管一个目标
只用标准库；iterm2 模块只在真正连 iTerm 时才导入（/usr/bin/python3 装了它）。
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
import time

from _fleet_paths import expand_path, require_fleet_home

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = '<!-- ===== 以下为机械快照，脚本每次覆盖；以上为舰长手写判断 ===== -->'
CARD_TEMPLATE_MARK = '<舰长在自动建卡占位段填写>'
READY_TOKEN = 'HANDOFF-READY'
DONE_TOKEN = 'TAKEOVER-DONE'
CARD_GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '交接卡写法.md')

TIMING = {  # 秒；targets.json 的 "timing" 可逐项覆盖
    'tick_idle': 60, 'tick_active': 10,
    'snapshot_max_age': 180,
    'remind_after': 600, 'notified_timeout': 1800,
    'ready_timeout': 1800, 'clear_confirm': 60, 'clear_retry_margin': 20,
    'cleared_timeout': 1800, 'kicked_timeout': 300,
    'retrigger_guard': 600, 'giveup_cooldown': 1800,
    'retry_cooldown': 120, 'max_failures': 5,
    'draft_alert': 600, 'dryrun_cooldown': 600,
    'type_settle': 1.0, 'confirm_poll': 2.0, 'confirm_polls': 6,
}
LIMITS = {'history_lines': 1000, 'min_manual_chars': 100}


class TermError(Exception):
    """读屏/打字通道本身出错（不是窗口不存在）。"""


# ---------------------------------------------------------------- 读屏解析（纯文本，可单测）

# 真实输入框顶格画：上下各一条横线，中间 ❯ 开头。历史区里工具输出引用的别人屏幕都带缩进，不会被认成输入框。
RULE = re.compile(r'^[─━═]{8,}')
PROMPT = re.compile(r'^❯(?:[ \t ](.*))?$')
BUSY = re.compile(r'^[·✢✳✶✻✽*] [^\s…]+(?: [^\s…]+){0,2}…')
DIALOG = re.compile(r'Enter to select|Enter to confirm|Esc to cancel|Do you want to')
TRY_HINT = re.compile(r'^Try\s+"[^"\n]*"$')
QUEUED_HINT = 'Press up to edit queued messages'


def norm(text):
    return re.sub(r'\s+', '', text or '')


class View(object):
    """一次读屏的解析结果。ok=False 表示认不出输入框（弹窗、非 Claude 界面等），此时一律不打字。"""

    def __init__(self, lines):
        lines = list(lines)
        while lines and not lines[-1].strip():
            lines.pop()
        self.lines = lines
        self.ok = False
        self.history, self.below, self.draft = lines, [], ''
        self.empty = self.queued = self.busy = False
        rules = [i for i, line in enumerate(lines) if RULE.match(line)]
        self.dialog = False
        if len(rules) < 2:
            return
        top, bottom = rules[-2], rules[-1]  # 只认最靠下的那一对横线，且下面只剩状态栏
        if not (2 <= bottom - top <= 40 and len(lines) - bottom <= 25 and PROMPT.match(lines[top + 1])):
            return
        self.dialog = any(DIALOG.search(line) for line in lines[max(0, top - 15):top] + lines[bottom + 1:])
        parts = [(PROMPT.match(lines[top + 1]).group(1) or '').strip()]
        parts += [line.strip(' \t │') for line in lines[top + 2:bottom]]
        self.draft = '\n'.join(parts).strip()
        self.queued = self.draft.startswith(QUEUED_HINT)
        self.empty = self.draft == '' or self.queued or TRY_HINT.match(self.draft) is not None
        self.history, self.below = lines[:top], lines[bottom + 1:]
        # 转圈那行顶格画（✳ Xxx… (27s …)）；正文都带缩进或 ⏺/❯ 开头，所以往上多看几行也不会认错
        self.busy = any(BUSY.match(line) for line in lines[max(0, top - 30):top])
        self.ok = True

    @property
    def idle(self):
        """这一轮已结束：认得出输入框、没在跑、没有弹窗、没有排队消息。"""
        return self.ok and not self.busy and not self.dialog and not self.queued

    def seen_outside(self, keys):
        """这些记号有没有出现在输入框以外（历史区或排队区）——出现了就是已经发出去了。"""
        return any(k in norm(line) for line in self.history + self.below for k in keys)

    def standalone_after(self, keys, token):
        """历史区里，在带记号的那条通知之后，有没有单独一行 token。"""
        anchor = next((i for i, line in enumerate(self.history) if any(k in norm(line) for k in keys)), None)
        if anchor is None:
            return False
        pattern = re.compile(r'^\s*(?:⏺\s*)?[`*]*' + re.escape(token) + r'[`*]*[\s。.]*$')
        return any(pattern.match(line) for line in self.history[anchor + 1:])


# ---------------------------------------------------------------- 交接卡

def manual_section(path):
    try:
        with open(path, encoding='utf-8') as stream:
            return stream.read().split(SEP, 1)[0]
    except (OSError, UnicodeDecodeError):
        return None


def manual_digest(path):
    text = manual_section(path)
    return '' if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest()


def card_written(path, baseline, min_chars):
    """手写段在通知之后改过，且不是空的、不是自动建卡的占位段。"""
    text = manual_section(path)
    if text is None or CARD_TEMPLATE_MARK in text or len(text.strip()) < min_chars:
        return False
    return hashlib.sha256(text.encode('utf-8')).hexdigest() != baseline


# ---------------------------------------------------------------- 小工具

def atomic_write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.tmp.%d' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def short(value):
    return value[:8] if isinstance(value, str) else value


def load_config(path):
    fleet_home = require_fleet_home()
    cfg = read_json(path)
    if not isinstance(cfg, dict) or not isinstance(cfg.get('targets'), dict):
        raise SystemExit('配置读不了：' + path)
    for name in ('registry', 'snapshot_dir', 'handoff_dir', 'data_dir'):
        raw = cfg.get(name)
        if not raw:
            raise SystemExit('配置项 %s 缺失' % name)
        cfg[name] = expand_path(raw, fleet_home)
        if not os.path.isabs(cfg[name]):
            raise SystemExit('配置项 %s 展开后仍非绝对路径' % name)
    guide = cfg.get('card_guide') or os.environ.get('STEWARD_CARD_GUIDE')
    if guide:
        cfg['card_guide'] = expand_path(guide, fleet_home)
    else:
        cfg['card_guide'] = os.path.join(HERE, '交接卡写法.md')
    global CARD_GUIDE
    CARD_GUIDE = cfg['card_guide']
    if os.environ.get('STEWARD_DATA_DIR'):
        cfg['data_dir'] = expand_path(os.environ['STEWARD_DATA_DIR'], fleet_home)
    cfg['timing'] = dict(TIMING, **(cfg.get('timing') or {}))
    cfg['limits'] = dict(LIMITS, **(cfg.get('limits') or {}))
    return cfg


# ---------------------------------------------------------------- 状态机

class Steward(object):
    def __init__(self, cfg, term, dry_run=False, only=None, probe=False, now=time.time, sleep=asyncio.sleep):
        self.cfg, self.term, self.dry_run, self.only, self.probe = cfg, term, dry_run, only, probe
        self.now, self.sleep = now, sleep
        self.t = cfg['timing']
        self.data = os.path.join(cfg['data_dir'], 'dryrun') if dry_run else cfg['data_dir']
        self.status = {}
        self._reads = self._read_errors = 0
        self.term_errors = 0  # 连续「一轮里读屏全失败」的次数，给外层决定要不要退出重连

    # ---- 存储和日志
    def log(self, key, event, **fields):
        os.makedirs(self.data, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.now()))
        extra = (' ' + json.dumps(fields, ensure_ascii=False, sort_keys=True)) if fields else ''
        with open(os.path.join(self.data, 'steward.log'), 'a', encoding='utf-8') as stream:
            stream.write('%s [%s] %s%s\n' % (stamp, key, event, extra))

    def once(self, st, key, flag, event, **fields):
        """同一种情况只在刚出现时记一行，免得每分钟刷日志。"""
        if not st.get(flag):
            st[flag] = True
            self.log(key, event, **fields)

    def state_path(self, key):
        return os.path.join(self.data, 'state-%s.json' % key)

    def load_state(self, key):
        st = read_json(self.state_path(key))
        if not isinstance(st, dict) or st.get('phase') not in ('IDLE', 'NOTIFIED', 'READY', 'CLEARED', 'KICKED'):
            st = {'key': key, 'phase': 'IDLE', 'session_id': None, 'cycle': None}
        return st

    def save_state(self, key, st):
        atomic_write(self.state_path(key), st)

    def alerts(self):
        data = read_json(os.path.join(self.data, 'alerts.json'))
        return data.get('alerts', {}) if isinstance(data, dict) else {}

    def _write_alerts(self, alerts):
        atomic_write(os.path.join(self.data, 'alerts.json'), {'updated_at': self.now(), 'alerts': alerts})

    def alert(self, key, reason, detail):
        alerts = self.alerts()
        if reason not in alerts.get(key, {}):
            alerts.setdefault(key, {})[reason] = {'since': self.now(), 'detail': detail}
            self._write_alerts(alerts)
            self.log(key, 'NEEDS_HUMAN', reason=reason, detail=detail)

    def resolve(self, key, *reasons):
        alerts = self.alerts()
        mine = alerts.get(key, {})
        gone = [r for r in list(mine) if not reasons or r in reasons]
        if gone:
            for reason in gone:
                del mine[reason]
            if not mine:
                alerts.pop(key, None)
            self._write_alerts(alerts)
            self.log(key, 'ALERT_RESOLVED', reasons=gone)

    # ---- 目标、快照
    def targets(self):
        """名册实时取；窗口号变了这里自然跟着变。"""
        registry = read_json(self.cfg['registry']) or {}
        restore = registry.get('last_restore') or {}
        crew = restore.get('crew') or {}
        tabs = {c.get('key'): c.get('tab_name') for c in registry.get('crew') or [] if isinstance(c, dict)}
        defaults = self.cfg.get('defaults') or {}
        out = {}
        for key, item in self.cfg['targets'].items():
            if self.only and key != self.only:
                continue
            item = item or {}
            iterm = item.get('iterm_session') or (restore.get('captain') if key == 'captain' else crew.get(key))
            out[key] = {
                'iterm': iterm.upper() if isinstance(iterm, str) and iterm else None,
                'role': item.get('role') or tabs.get(key) or key,
                'threshold': item.get('threshold') or defaults.get('threshold') or {'percent': 85},
                'idle_trigger': item.get('idle_trigger', defaults.get('idle_trigger')),
            }
        return out

    def snapshot(self, iterm):
        snap = read_json(os.path.join(self.cfg['snapshot_dir'], iterm + '.json'))
        if not isinstance(snap, dict) or not isinstance(snap.get('session_id'), str):
            return None
        if not re.match(r'^[A-Za-z0-9_-]+$', snap['session_id']) or not isinstance(snap.get('updated_at'), (int, float)):
            return None
        if (snap.get('iterm_session') or '').upper() != iterm:
            return None
        return snap

    @staticmethod
    def usage(snap):
        window = snap.get('context_window') or {}
        percent = window.get('used_percentage')
        percent = float(percent) if isinstance(percent, (int, float)) else None
        current = window.get('current_usage') or {}
        tokens = sum(int(current.get(k) or 0) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))
        if not tokens and percent is not None and window.get('context_window_size'):
            tokens = int(window['context_window_size'] * percent / 100)
        return percent, (tokens or None)

    def trigger(self, tgt, percent, tokens, age):
        threshold = tgt['threshold']
        if -5 <= age <= self.t['snapshot_max_age']:
            if threshold.get('percent') is not None and percent is not None and percent >= threshold['percent']:
                return 'threshold'
            if threshold.get('tokens') is not None and tokens is not None and tokens >= threshold['tokens']:
                return 'threshold'
        idle = tgt.get('idle_trigger')  # 可选：闲置到点且用量过线，抢在 Claude Code 自己的闲置压缩（约 54 分钟）之前交接
        if idle and tokens is not None and tokens >= idle['min_tokens'] \
                and idle['idle_minutes'] * 60 <= age < idle.get('max_idle_minutes', 53) * 60:
            return 'idle'
        return None

    def card_path(self, session_id):
        return os.path.join(self.cfg['handoff_dir'], 'HANDOFF-%s.md' % session_id)

    # ---- 读屏、打字
    async def view(self, iterm):
        """None＝窗口不在；否则 View。通道出错抛 TermError。"""
        self._reads += 1
        try:
            lines = await self.term.read(iterm)
        except TermError:
            self._read_errors += 1
            raise
        return None if lines is None else View([line.replace('\x00', ' ') for line in lines])

    async def deliver(self, key, st, cyc, kind, make_text, allow_new=True):
        """照 send-to-session.sh 的做法往目标窗发一条消息。返回 DELIVERED / DRAFT / NO_WINDOW / UNREADABLE / UNCONFIRMED / DRYRUN / SKIP。"""
        iterm = st['iterm']
        tried = cyc.setdefault('tried', {}).setdefault(kind, [])
        view = await self.view(iterm)
        if view is None:
            return 'NO_WINDOW'
        if not view.ok:
            return 'UNREADABLE'
        if tried and view.seen_outside(tried):  # 上回没确认的那条其实已经发出去了
            return self._delivered(key, cyc, kind, [k for k in tried if view.seen_outside([k])][0], late=True)
        leftover = next((k for k in tried if k in norm(view.draft)), None)
        if leftover:  # 输入框里是我们自己上回没发出去的字：补回车
            if self.dry_run:
                return 'DRYRUN'
            await self.term.send(iterm, '\r')
            return await self._confirm(key, st, cyc, kind, leftover)
        if not allow_new:  # 只是回来收拾上回没确认的那条：屏上已经没有它了，记号作废，不发新的
            del tried[:]
            return 'SKIP'
        self.note_draft(key, st, not view.empty)
        if not view.empty:
            return 'DRAFT'
        mark = 'CTX2#' + secrets.token_hex(3)
        text = make_text('[%s]' % mark)
        if self.dry_run:
            self.log(key, 'WOULD_TYPE', kind=kind, text=text)
            return 'DRYRUN'
        tried.append(mark)
        self.save_state(key, st)  # 先落意图再打字，重起后还认得这条
        self.log(key, 'TYPE', kind=kind, mark=mark)
        await self.term.send(iterm, text)
        await self.sleep(self.t['type_settle'])
        view = await self.view(iterm)
        if view is not None and view.ok and view.seen_outside([mark]) and mark not in norm(view.draft):
            return self._delivered(key, cyc, kind, mark)
        if view is None or not view.ok or mark not in norm(view.draft):
            return 'UNCONFIRMED'  # 没看到字落在输入框里，不补回车
        await self.term.send(iterm, '\r')
        return await self._confirm(key, st, cyc, kind, mark)

    async def _confirm(self, key, st, cyc, kind, mark):
        for _ in range(int(self.t['confirm_polls'])):
            await self.sleep(self.t['confirm_poll'])
            view = await self.view(st['iterm'])
            if view is None or not view.ok:
                continue
            in_draft = mark in norm(view.draft)
            if view.seen_outside([mark]) and not in_draft:
                return self._delivered(key, cyc, kind, mark)
            if in_draft:
                await self.term.send(st['iterm'], '\r')
        return 'UNCONFIRMED'

    def _delivered(self, key, cyc, kind, mark, late=False):
        cyc.setdefault('marks', {}).setdefault(kind, []).append(mark)
        self.log(key, 'DELIVERED', kind=kind, mark=mark, late=late)
        return 'DELIVERED'

    def note_draft(self, key, st, occupied):
        """输入框里有别人没发出的字：不动它；连续 10 分钟都这样就记告警。"""
        if not occupied:
            if st.pop('draft_since', None) is not None:
                self.resolve(key, 'INPUT_OCCUPIED')
            return
        st.setdefault('draft_since', self.now())
        if self.now() - st['draft_since'] >= self.t['draft_alert']:
            self.alert(key, 'INPUT_OCCUPIED', '输入框被占：目标窗输入框里一直有没发出的字，管家没动它')

    def failed(self, key, st, result):
        """这一轮算没做：冷却后重试；连续失败到上限就标「要人看」，状态不动、别的目标照常。"""
        if result in ('DRAFT', 'SKIP'):
            return
        if result == 'DRYRUN':
            st['cooldown_until'] = self.now() + self.t['dryrun_cooldown']
            return
        st['fail_count'] = st.get('fail_count', 0) + 1
        st['cooldown_until'] = self.now() + self.t['retry_cooldown']
        self.log(key, 'ATTEMPT_FAILED', phase=st['phase'], result=result, fail_count=st['fail_count'])
        if st['fail_count'] >= self.t['max_failures']:
            self.alert(key, 'DELIVERY_FAILING', '连续 %d 次没能把话送进目标窗（最后一次：%s），阶段 %s' % (st['fail_count'], result, st['phase']))
            st['fail_count'] = 0
            st['cooldown_until'] = self.now() + self.t['giveup_cooldown']

    def succeeded(self, key, st):
        st['fail_count'] = 0
        st.pop('cooldown_until', None)
        self.resolve(key, 'DELIVERY_FAILING')

    def to_idle(self, key, st, session_id, guard=0, cooldown=0):
        st.update(phase='IDLE', session_id=session_id, cycle=None, fail_count=0)
        for name in ('draft_since', 'cooldown_until', 'guard_until', 'stale_logged', 'window_logged'):
            st.pop(name, None)
        if guard:
            st['guard_until'] = self.now() + guard
        if cooldown:
            st['cooldown_until'] = self.now() + cooldown

    # ---- 一轮
    async def tick(self):
        """所有目标各走一步。返回是否有目标在交接途中（外层据此决定下一轮等多久）。"""
        self._reads = self._read_errors = 0
        active = False
        targets = self.targets()
        for key, tgt in targets.items():
            st = self.load_state(key)
            info = {'iterm': tgt['iterm'], 'role': tgt['role'], 'threshold': tgt['threshold']}
            try:
                await self.step(key, tgt, st, info)
            except Exception as error:  # 一个目标出错不拖累别的
                self.log(key, 'ERROR', error='%s: %s' % (type(error).__name__, error))
                self.failed(key, st, 'ERROR')
                info['error'] = type(error).__name__
            self.save_state(key, st)
            info.update(phase=st['phase'], session_id=st.get('session_id'), last_handoff_at=st.get('last_handoff_at'),
                        cooldown_until=st.get('cooldown_until'), needs_human=sorted(self.alerts().get(key, {})))
            self.status[key] = info
            active = active or st['phase'] != 'IDLE'
        for key in [k for k in self.status if k not in targets]:
            del self.status[key]
        atomic_write(os.path.join(self.data, 'status.json'), {
            'heartbeat': self.now(), 'pid': os.getpid(), 'mode': 'dry-run' if self.dry_run else 'live', 'targets': self.status})
        self.term_errors = self.term_errors + 1 if self._reads and self._reads == self._read_errors else 0
        return active

    async def step(self, key, tgt, st, info):
        now, t = self.now(), self.t
        iterm = tgt['iterm']
        if not iterm:
            self.once(st, key, 'unresolved_logged', 'TARGET_UNRESOLVED')
            info['window'] = 'unresolved'
            return
        st.pop('unresolved_logged', None)
        if st.get('iterm') and st['iterm'] != iterm:
            self.log(key, 'WINDOW_MOVED', old=short(st['iterm']), new=short(iterm))
            st.pop('window_logged', None)
        st['iterm'] = iterm
        snap = self.snapshot(iterm)
        if snap is None:
            self.once(st, key, 'snapshot_logged', 'SNAPSHOT_UNREADABLE', iterm=short(iterm))
            info['snapshot'] = 'unreadable'
            if self.probe:
                await self.probe_screen(key, st, info)
            return
        st.pop('snapshot_logged', None)
        sid = snap['session_id']
        percent, tokens = self.usage(snap)
        age = now - snap['updated_at']
        card = self.card_path(sid)
        text = manual_section(card)
        info.update(used_percentage=percent, tokens=tokens, snapshot_age=round(age), card=card,
                    card_exists=text is not None, manual_chars=len(text.strip()) if text is not None else 0)
        cyc = st.get('cycle') or {}

        # 会话号变了：谁换的
        if st['phase'] == 'IDLE':
            if st.get('session_id') and st['session_id'] != sid:
                self.log(key, 'SESSION_ADOPTED', old=short(st['session_id']), new=short(sid))
                self.resolve(key)
                self.to_idle(key, st, sid)
            st['session_id'] = sid
        elif st['phase'] in ('NOTIFIED', 'READY') and sid != cyc['old_session']:
            if st['phase'] == 'READY' and cyc.get('clear_sent_at'):
                cyc.update(new_session=sid, cleared_at=now)
                st.update(phase='CLEARED', session_id=sid, last_handoff_at=now)
                self.succeeded(key, st)
                self.resolve(key, 'CLEAR_UNCONFIRMED', 'NO_READY')
                self.log(key, 'CLEARED', old=short(cyc['old_session']), new=short(sid))
            else:
                self.log(key, 'EXTERNAL_SESSION_CHANGE', phase=st['phase'], old=short(cyc['old_session']), new=short(sid))
                self.resolve(key)
                self.to_idle(key, st, sid)
        elif st['phase'] == 'CLEARED' and sid != cyc['old_session']:
            cyc['new_session'] = st['session_id'] = sid
        elif st['phase'] == 'KICKED' and sid != cyc['new_session']:
            self.log(key, 'EXTERNAL_SESSION_CHANGE', phase='KICKED', old=short(cyc['new_session']), new=short(sid))
            self.to_idle(key, st, sid)

        phase = st['phase']
        cooling = now < st.get('cooldown_until', 0)
        fired = self.trigger(tgt, percent, tokens, age)
        info['would_trigger'] = bool(fired)
        if phase == 'IDLE':
            fresh = -5 <= age <= t['snapshot_max_age']
            if not fresh and not fired:
                self.once(st, key, 'stale_logged', 'SNAPSHOT_STALE', age=round(age))  # 只记日志，不往任何窗口发告警
            else:
                st.pop('stale_logged', None)
            # 上回打了通知但没确认：哪怕现在快照过期了，也要回去看一眼（已送达就认、还躺在输入框里就补回车）
            pending = bool((st.get('cycle') or {}).get('tried', {}).get('notice'))
            if (fired or pending) and not cooling and now >= st.get('guard_until', 0):
                await self.do_notify(key, tgt, st, sid, percent, tokens, fired)
            elif fresh and not fired and not pending:  # 用量确实在线下了：上一轮没走完的东西作废
                self.resolve(key, 'NO_READY', 'DELIVERY_FAILING', 'CLEAR_STUCK', 'CLEAR_UNCONFIRMED', 'INPUT_OCCUPIED')
                st['cycle'] = None
                st.pop('draft_since', None)
        elif phase == 'NOTIFIED':
            await self.wait_ready(key, tgt, st, cyc, tokens, age, cooling)
        elif phase == 'READY':
            await self.do_clear(key, st, cyc, snap, cooling)
        elif phase == 'CLEARED':
            await self.do_kick(key, tgt, st, cyc, cooling)
        elif phase == 'KICKED':
            await self.wait_takeover(key, st, cyc)
        if self.probe and 'screen' not in info:
            await self.probe_screen(key, st, info)

    async def probe_screen(self, key, st, info):
        try:
            view = await self.view(st['iterm'])
        except TermError as error:
            info['screen'] = 'error'
            self.log(key, 'SCREEN_ERROR', error=str(error))
            return
        info['window'] = 'missing' if view is None else 'present'
        info['screen'] = 'missing' if view is None else ('ok' if view.ok else 'unreadable')
        if view is not None and view.ok:
            info.update(screen_idle=view.idle, input_empty=view.empty, screen_lines=len(view.lines))

    async def do_notify(self, key, tgt, st, sid, percent, tokens, fired):
        cyc = st.get('cycle')
        if not cyc or cyc.get('old_session') != sid:
            cyc = st['cycle'] = {'old_session': sid, 'card': self.card_path(sid), 'trigger': fired}
        if not cyc.get('tried', {}).get('notice'):
            cyc['baseline'] = manual_digest(cyc['card'])  # 基线取在第一次打字之前
        amount = ('%g%%' % percent if percent is not None else '') + ('（约 %.1f 万 token）' % (tokens / 1e4) if tokens else '')
        why = '已闲置较久、趁缓存还热先交接' if fired == 'idle' else '到交接线了'
        result = await self.deliver(key, st, cyc, 'notice', lambda mark: (
            '%s 交接程序（自动消息，照做即可，不用调任何技能）：本会话上下文已用 %s，%s。请现在把交接卡手写段定稿：%s（文件没有就新建；先读 %s，照里面的清单写，宁可多写，上一张卡里还有效的照搬过来；至少写清在飞的事、判据、下一步、监听怎么重挂；'
            '分隔线以下的机械快照不用管）。手写段必须在本通知之后有改动。写完单独回一行 %s（整行只有这一个词），然后停手等我；之后我替你发 /clear，新会话我会叫它读这张卡。'
            % (mark, amount, why, cyc['card'], CARD_GUIDE, READY_TOKEN)), allow_new=bool(fired))
        if result != 'DELIVERED':
            return self.failed(key, st, result)
        cyc.update(notified_at=self.now(), tokens_at_notify=tokens)
        st['phase'] = 'NOTIFIED'
        self.succeeded(key, st)
        self.log(key, 'NOTIFIED', session=short(sid), trigger=fired, used=percent, tokens=tokens)

    async def ready_evidence(self, st, cyc):
        """两个条件：卡的手写段在通知后改过；屏幕历史里、通知之后有单独一行 HANDOFF-READY。同时把这次读屏还回去。"""
        view = await self.view(st['iterm'])
        written = card_written(cyc['card'], cyc['baseline'], self.cfg['limits']['min_manual_chars'])
        said = view is not None and view.ok and view.standalone_after(cyc.get('marks', {}).get('notice', []), READY_TOKEN)
        return view, written, said

    async def wait_ready(self, key, tgt, st, cyc, tokens, age, cooling):
        now, t = self.now(), self.t
        before = cyc.get('tokens_at_notify')
        if age <= t['snapshot_max_age'] and tokens and before and tokens < before * 0.5:
            self.log(key, 'COMPACTED_DURING_HANDOFF', before=before, now=tokens)  # Claude Code 自己先压缩了，这轮作罢
            return self.to_idle(key, st, cyc['old_session'], guard=t['retrigger_guard'])
        view, written, said = await self.ready_evidence(st, cyc)
        if view is None:
            self.once(st, key, 'window_logged', 'WINDOW_MISSING', iterm=short(st['iterm']))
        else:
            st.pop('window_logged', None)
        if written and said:
            cyc['ready_at'] = now
            st['phase'] = 'READY'
            self.resolve(key, 'NO_READY')
            return self.log(key, 'READY', session=short(cyc['old_session']))
        waited = now - cyc['notified_at']
        if waited >= t['notified_timeout']:
            self.alert(key, 'NO_READY', '通知后 %d 分钟没等到交接卡定稿和 HANDOFF-READY（卡改过=%s，见到READY=%s）' % (waited // 60, written, said))
            return self.to_idle(key, st, cyc['old_session'], cooldown=t['giveup_cooldown'])
        if waited >= t['remind_after'] and not cyc.get('reminded_at') and not cooling:
            result = await self.deliver(key, st, cyc, 'remind', lambda mark: (
                '%s 交接程序催一次（自动消息，不用调任何技能）：交接卡 %s 的手写段还没见更新，或者还没见到单独一行 %s（整行只有这一个词）的回复。定稿后请单独回这一行，然后停手等我清会话。'
                % (mark, cyc['card'], READY_TOKEN)))
            if result == 'DELIVERED':
                cyc['reminded_at'] = now
                self.succeeded(key, st)
            else:
                self.failed(key, st, result)

    async def do_clear(self, key, st, cyc, snap, cooling):
        now, t = self.now(), self.t
        if now - cyc['ready_at'] >= t['ready_timeout']:
            self.alert(key, 'CLEAR_STUCK', 'READY 后 %d 分钟没能清会话（目标窗一直没空下来，或清的结果不明）' % ((now - cyc['ready_at']) // 60))
            return self.to_idle(key, st, cyc['old_session'], cooldown=t['giveup_cooldown'])
        sent = cyc.get('clear_sent_at')
        if sent:
            if now - sent < t['clear_confirm']:
                return  # 等快照里的会话号变
            if snap['updated_at'] > sent + t['clear_retry_margin']:
                # 发过之后状态栏又刷新过、会话号还是旧的：这次没清成，可以重试
                self.log(key, 'CLEAR_NOT_APPLIED', session=short(cyc['old_session']))
                cyc['clear_sent_at'] = None
                self.failed(key, st, 'CLEAR_NOT_APPLIED')
                return
            self.once(cyc, key, 'unconfirmed_logged', 'CLEAR_UNCONFIRMED')
            self.alert(key, 'CLEAR_UNCONFIRMED', '发了 /clear 但 60 秒内没见会话号变，也没有新快照能证明没清成；不重发，等快照')
            if self.clear_in_box(await self.view(st['iterm'])) and not self.dry_run:
                await self.term.send(st['iterm'], '\r')  # 我们自己打的 /clear 还躺在输入框里：补回车
                cyc['clear_sent_at'] = now
            return
        if cooling:
            return
        # 红线：就在这一次读屏上，卡改过、READY 那行还在通知之后、这一轮已结束、输入框为空，才发 /clear
        view, written, said = await self.ready_evidence(st, cyc)
        if view is None:
            return self.failed(key, st, 'NO_WINDOW')
        if not view.ok or not view.idle:
            return
        self.note_draft(key, st, not view.empty)
        if view.draft != '' or not (written and said):
            if not (written and said):
                self.once(cyc, key, 'evidence_logged', 'CLEAR_HELD_EVIDENCE_GONE', written=written, said=said)
            return
        if self.dry_run:
            self.log(key, 'WOULD_TYPE', kind='clear', text='/clear')
            return self.failed(key, st, 'DRYRUN')
        cyc['clear_sent_at'] = now
        self.save_state(key, st)
        self.log(key, 'TYPE', kind='clear', session=short(cyc['old_session']))
        await self.term.send(st['iterm'], '/clear')
        await self.sleep(t['type_settle'])
        if not self.clear_in_box(await self.view(st['iterm'])):
            return self.log(key, 'CLEAR_TYPED_UNVERIFIED')  # 输入框里不是干净的 /clear：不按回车
        await self.term.send(st['iterm'], '\r')

    @staticmethod
    def clear_in_box(view):
        """输入框里是不是只有我们打的 /clear（斜杠菜单弹出来、认不出横线时，退一步只看 ❯ 那一行）。"""
        if view is None:
            return False
        if view.ok:
            return norm(view.draft) == '/clear'
        return any(re.match(r'^❯\s*/clear\s*$', line) for line in view.lines[-40:])

    async def do_kick(self, key, tgt, st, cyc, cooling):
        now, t = self.now(), self.t
        if now - cyc['cleared_at'] >= t['cleared_timeout']:
            self.alert(key, 'KICK_FAILED', '清会话后 %d 分钟没能把读卡的话送进新会话；旧卡在 %s' % ((now - cyc['cleared_at']) // 60, cyc['card']))
            return self.to_idle(key, st, cyc['new_session'], guard=t['retrigger_guard'])
        if cooling:
            return
        result = await self.deliver(key, st, cyc, 'kick', lambda mark: (
            '%s 交接程序（自动消息，照做即可，不用调任何技能）：你是「%s」。上一会话上下文到线，已写好交接卡并清了会话。先完整读旧卡 %s 分隔线以上的手写段（分隔线以下是机械快照，要用再看），'
            '按卡重挂监听、接着干在飞的事。读完单独回一行 %s（整行只有这一个词），再往下做。' % (mark, tgt['role'], cyc['card'], DONE_TOKEN)))
        if result != 'DELIVERED':
            return self.failed(key, st, result)
        cyc['kicked_at'] = self.now()
        st['phase'] = 'KICKED'
        self.succeeded(key, st)
        self.log(key, 'KICKED', new=short(cyc['new_session']), card=cyc['card'])

    async def wait_takeover(self, key, st, cyc):
        view = await self.view(st['iterm'])
        done = view is not None and view.ok and view.standalone_after(cyc.get('marks', {}).get('kick', []), DONE_TOKEN)
        if done or self.now() - cyc['kicked_at'] >= self.t['kicked_timeout']:
            self.log(key, 'HANDOFF_COMPLETE', takeover_done=done, new=short(cyc['new_session']))
            self.resolve(key)
            self.to_idle(key, st, cyc['new_session'], guard=self.t['retrigger_guard'])


# ---------------------------------------------------------------- 真实 iTerm 通道

class ItermTerm(object):
    """iterm2 Python 接口。allow_write=False 时任何打字都直接拒绝（dry-run 的第二道闸）。"""

    def __init__(self, connection, allow_write, history_lines):
        import iterm2
        self.iterm2, self.connection, self.allow_write, self.history_lines = iterm2, connection, allow_write, history_lines

    async def _session(self, iterm):
        app = await self.iterm2.async_get_app(self.connection)
        await app.async_refresh()
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id.upper() == iterm:
                        return session
        return None

    async def read(self, iterm):
        try:
            session = await self._session(iterm)
            if session is None:
                return None
            async with self.iterm2.Transaction(self.connection):
                info = await session.async_get_line_info()
                have = info.scrollback_buffer_height + info.mutable_area_height
                want = min(self.history_lines, have)
                contents = await session.async_get_contents(info.overflow + have - want, want)
        except Exception as error:
            raise TermError('%s: %s' % (type(error).__name__, error))
        lines = [c.string for c in contents]
        for i in range(len(lines) - 1, -1, -1):  # 输入框里的灰字（建议）不算草稿：去掉 faint 字符
            if lines[i].lstrip('  ').startswith('❯'):
                kept, x = [], 0
                while True:
                    try:
                        char = contents[i].string_at(x)
                    except IndexError:
                        break
                    if not getattr(contents[i].style_at(x), 'faint', False):
                        kept.append(char)
                    x += 1
                lines[i] = ''.join(kept)
                break
        return lines

    async def send(self, iterm, text):
        if not self.allow_write:
            raise RuntimeError('WRITE_FORBIDDEN_IN_DRY_RUN')
        try:
            session = await self._session(iterm)
            if session is None:
                raise TermError('SESSION_NOT_FOUND')
            await session.async_send_text(text)
        except TermError:
            raise
        except Exception as error:
            raise TermError('%s: %s' % (type(error).__name__, error))


# ---------------------------------------------------------------- 入口

def print_table(steward):
    print('%-11s %-8s %-8s %-14s %-6s %-9s %-5s %-10s %s' % ('目标', '窗口', '会话', '用量', '快照', '状态', '触发', '读屏', '卡'))
    for key, info in steward.status.items():
        tokens = info.get('tokens')
        used = ('%g%%' % info['used_percentage'] if info.get('used_percentage') is not None else '?') + (' %dk' % (tokens // 1000) if tokens else '')
        card = ('有，手写段 %d 字' % info['manual_chars']) if info.get('card_exists') else ('无' if 'card_exists' in info else '?')
        print('%-11s %-8s %-8s %-14s %-6s %-9s %-5s %-10s %s' % (
            key, short(info.get('iterm')) or '?', short(info.get('session_id')) or '?', used,
            ('%ds' % info['snapshot_age']) if 'snapshot_age' in info else '?', info.get('phase'),
            '会' if info.get('would_trigger') else '不会', info.get('screen', '-'), card))


def main(argv=None):
    parser = argparse.ArgumentParser(description='上下文管家 v2')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--loop', action='store_true', help='常驻')
    mode.add_argument('--once', action='store_true', help='跑一轮')
    parser.add_argument('--dry-run', action='store_true', help='只读屏、只算，不往任何窗口打字')
    parser.add_argument('--only', metavar='KEY', help='只管一个目标')
    parser.add_argument('--config', default=os.environ.get('STEWARD_TARGETS_JSON') or '')
    args = parser.parse_args(argv)
    require_fleet_home()
    config_path = args.config or os.path.join(os.environ['FLEET_HOME'], 'steward', 'targets.json')
    cfg = load_config(config_path)
    if args.only and args.only not in cfg['targets']:
        raise SystemExit('targets.json 里没有这个目标：' + args.only)
    if not args.dry_run and os.environ.get('CTXSTEWARD2_LIVE') != '1':
        print('拒绝启动：不带 --dry-run 会往舰长窗口打字。真要上线请用 run.sh（它会设 CTXSTEWARD2_LIVE=1）。')
        return 2
    lock = None
    if not args.dry_run:  # 真跑只许一个实例，免得两个管家往同一个窗重复打字
        os.makedirs(cfg['data_dir'], exist_ok=True)
        lock = open(os.path.join(cfg['data_dir'], 'steward.lock'), 'a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('已有一个管家在跑，本进程退出。')
            return 9
    import iterm2

    async def run(connection):
        term = ItermTerm(connection, allow_write=not args.dry_run, history_lines=cfg['limits']['history_lines'])
        steward = Steward(cfg, term, dry_run=args.dry_run, only=args.only, probe=args.once)
        steward.log('-', 'START', mode='dry-run' if args.dry_run else 'live', loop=args.loop, only=args.only, pid=os.getpid())
        while True:
            active = await steward.tick()
            if args.once:
                print_table(steward)
                return
            if steward.term_errors >= 2:
                steward.log('-', 'EXIT_TERM_UNREACHABLE')
                raise SystemExit(3)  # 读屏通道断了：退出，交给 run.sh 重起重连
            await asyncio.sleep(steward.t['tick_active'] if active else steward.t['tick_idle'])

    iterm2.run_until_complete(run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
