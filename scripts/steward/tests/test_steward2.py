# -*- coding: utf-8 -*-
"""上下文管家 v2 测试：全部用假的窗口对象，不连 iTerm、不往任何真实窗口打字。

跑法：/usr/bin/python3 -B -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import steward2  # noqa: E402

WIDTH = 100
A = '1111AAAA'
B = '2222BBBB'
C = '3333CCCC'
X = '4444DDDD'
GOOD_CARD = '# 交接卡\n' + '在飞：任务甲换版等回执；判据：冒烟通过；下一步：催回执；监听：重挂飞书轮询。' * 4


def wrap(prefix, text):
    out, first = [], True
    while True:
        out.append((prefix if first else '  ') + text[:WIDTH - 2])
        text, first = text[WIDTH - 2:], False
        if not text:
            return out


class Clock(object):
    def __init__(self):
        self.t = 1800000000.0

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


class FakeWindow(object):
    """一个假的 Claude Code 窗口：历史区、转圈、输入框、状态栏；回车把输入框里的字提交。"""

    def __init__(self, env, iterm, sid):
        self.env, self.iterm, self.sid = env, iterm, sid
        self.history = ['⏺ 之前的工作输出', '']
        self.draft, self.queue, self.sent = '', [], []
        self.busy = self.dialog = self.gone = False
        self.show_queue = True
        self.swallow_text = self.swallow_enter = False
        self.clear_works, self.clears = True, 0
        self.inject = None

    def render(self):
        lines = list(self.history)
        if self.busy:
            lines += ['', '✳ Wibbling… (27s · ↓ 505 tokens)']
        if self.show_queue:
            for item in self.queue:
                lines += wrap('  ', item)
        lines.append('')
        if self.dialog:
            lines += [' Do you want to proceed?', ' ❯ 1. Yes', '   2. No', ' Esc to cancel']
        else:
            lines += ['─' * WIDTH] + wrap('❯ ', self.draft) + ['─' * WIDTH]
        return lines + ['  [Opus 5.5] │ fake │ ⏱️  1h', '  ⏵⏵ auto mode on', '']

    def send(self, text):
        self.sent.append(text)
        if text == '\r':
            return self.enter()
        if not self.swallow_text:
            self.draft += text
            if self.inject:
                self.draft, self.inject = self.draft + self.inject, None

    def enter(self):
        if self.swallow_enter or not self.draft:
            return
        text, self.draft = self.draft, ''
        if text.strip() == '/clear':
            self.clears += 1
            if self.clear_works:
                self.sid = 'new%d-%s' % (self.clears, self.iterm[:4].lower())
                self.history = ['  Claude Code（新会话）', '']
                self.env.snap(self.iterm, self.sid, percent=None)
        elif self.busy:
            self.queue.append(text)
        else:
            self.history += wrap('❯ ', text) + ['']

    def flush_queue(self):
        self.busy = False
        for item in self.queue:
            self.history += wrap('❯ ', item) + ['']
        self.queue = []

    def say(self, text):
        self.history += ['⏺ ' + line if i == 0 else '  ' + line for i, line in enumerate(text.split('\n'))] + ['']

    def typed(self, needle):
        return [s for s in self.sent if needle in s]

    def clear_typed(self):
        return [s for s in self.sent if s.strip() == '/clear']


class FakeTerm(object):
    def __init__(self):
        self.windows, self.broken, self.explode, self.forbid_send = {}, set(), set(), False

    async def read(self, iterm):
        if iterm in self.explode:
            raise RuntimeError('意料之外的错')
        if iterm in self.broken:
            raise steward2.TermError('boom')
        window = self.windows.get(iterm)
        return None if window is None or window.gone else window.render()

    async def send(self, iterm, text):
        if self.forbid_send:
            raise AssertionError('dry-run 里不许打字')
        window = self.windows.get(iterm)
        if window is None or window.gone:
            raise steward2.TermError('SESSION_NOT_FOUND')
        window.send(text)


class Env(object):
    def __init__(self, test):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['FLEET_HOME'] = self.tmp.name
        test.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.clock, self.term = Clock(), FakeTerm()
        self.paths = {name: os.path.join(root, name) for name in ('snap', 'handoff', 'data')}
        for path in self.paths.values():
            os.makedirs(path)
        self.registry = os.path.join(root, 'fleet-sessions.json')
        self.roster({'captain': A, 'crewa': B})
        self.targets = {'captain': {'role': '主舰长'}, 'crewa': {}, 'crewb': {'iterm_session': X, 'role': '舰员乙'}}
        self.defaults = {'threshold': {'percent': 85}, 'idle_trigger': None}
        self._steward = None

    def roster(self, mapping):
        crew = {k: v for k, v in mapping.items() if k != 'captain'}
        with open(self.registry, 'w', encoding='utf-8') as stream:
            json.dump({'last_restore': {'captain': mapping.get('captain'), 'crew': crew},
                       'crew': [{'key': 'crewa', 'tab_name': '舰员甲线'}]}, stream, ensure_ascii=False)

    def config(self):
        return {'registry': self.registry, 'snapshot_dir': self.paths['snap'], 'handoff_dir': self.paths['handoff'],
                'data_dir': self.paths['data'], 'defaults': self.defaults, 'targets': self.targets,
                'timing': dict(steward2.TIMING), 'limits': dict(steward2.LIMITS)}

    def steward(self, fresh=False, **kwargs):
        if fresh or kwargs or self._steward is None:
            self._steward = steward2.Steward(self.config(), self.term, now=self.clock.now, sleep=self.clock.sleep, **kwargs)
        return self._steward

    def snap(self, iterm, sid, percent=50, tokens=None, age=0):
        if tokens is None and percent is not None:
            tokens = int(percent * 1e4)
        usage = {'input_tokens': 2, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': (tokens or 2) - 2}
        with open(os.path.join(self.paths['snap'], iterm + '.json'), 'w', encoding='utf-8') as stream:
            json.dump({'updated_at': self.clock.t - age, 'session_id': sid, 'iterm_session': iterm,
                       'context_window': {'context_window_size': 1000000, 'used_percentage': percent,
                                          'current_usage': usage if percent is not None else None}}, stream)

    def add(self, iterm, sid, percent=50, **kwargs):
        window = self.term.windows[iterm] = FakeWindow(self, iterm, sid)
        self.snap(iterm, sid, percent, **kwargs)
        return window

    def card_path(self, sid):
        return os.path.join(self.paths['handoff'], 'HANDOFF-%s.md' % sid)

    def card(self, sid, manual, mechanical='生成时间：x\n## 任务激活器\n...'):
        with open(self.card_path(sid), 'w', encoding='utf-8') as stream:
            stream.write(manual + '\n' + steward2.SEP + '\n' + mechanical)

    async def tick(self, times=1, step=10, **kwargs):
        for _ in range(times):
            await self.steward(**kwargs).tick()
            self.clock.t += step

    def state(self, key, dry=False):
        path = os.path.join(self.paths['data'], 'dryrun' if dry else '', 'state-%s.json' % key)
        with open(path, encoding='utf-8') as stream:
            return json.load(stream)

    def phase(self, key):
        return self.state(key)['phase']

    def alerts(self, key=None):
        path = os.path.join(self.paths['data'], 'alerts.json')
        data = json.load(open(path, encoding='utf-8'))['alerts'] if os.path.exists(path) else {}
        return data if key is None else sorted(data.get(key, {}))

    def log(self, dry=False):
        path = os.path.join(self.paths['data'], 'dryrun' if dry else '', 'steward.log')
        return open(path, encoding='utf-8').read() if os.path.exists(path) else ''


# ---------------------------------------------------------------- 读屏解析

class ScreenParsing(unittest.TestCase):
    def view(self, **attrs):
        window = FakeWindow(None, A, 's')
        for name, value in attrs.items():
            setattr(window, name, value)
        return steward2.View(window.render())

    def test_idle_empty_box(self):
        view = self.view()
        self.assertTrue(view.ok and view.empty and view.idle)
        self.assertEqual(view.draft, '')

    def test_spinner_means_busy_but_done_line_is_idle(self):
        self.assertFalse(self.view(busy=True).idle)
        done = self.view(history=['⏺ 做完了', '', '✻ Churned for 8m 5s · done 16:20 · 2 shells still running'])
        self.assertTrue(done.idle)

    def test_spinner_still_seen_behind_many_queued_lines(self):
        view = self.view(busy=True, queue=['排队消息 %d' % i for i in range(12)])
        self.assertTrue(view.busy)
        self.assertFalse(view.idle)

    def test_wrapped_draft_is_read_whole(self):
        text = '别人没发出的字' * 30
        view = self.view(draft=text)
        self.assertFalse(view.empty)
        self.assertEqual(steward2.norm(view.draft), text)

    def test_placeholders_count_as_empty_but_queue_hint_is_not_idle(self):
        self.assertTrue(self.view(draft='Try "fix lint errors"').empty)
        queued = self.view(draft='Press up to edit queued messages')
        self.assertTrue(queued.empty)
        self.assertFalse(queued.idle)

    def test_permission_dialog_is_unreadable(self):
        view = self.view(dialog=True)
        self.assertFalse(view.ok)
        self.assertFalse(view.idle)

    def test_quoted_frame_in_history_is_not_the_input_box(self):
        quoted = ['⏺ Bash(peek.py 20 5555EEEE)', '  ⎿  ' + '─' * 60, '     ❯ ', '     ' + '─' * 60, '']
        view = self.view(history=quoted, dialog=True)
        self.assertFalse(view.ok)

    def test_real_screen_shape(self):
        lines = ['⏺ 回复正文', '', '✻ Baked for 15s · done 15:51 · 3 shells still running', '  ', '─' * 150, '❯ ', '─' * 150,
                 '  [Opus 5.5] │ 名字 │ ⏱️  72h 14m', '  上下文 ████░░░░░░ 37%', '  ⏵⏵ auto mode on · 3 shells · ← 2 agents']
        view = steward2.View(lines)
        self.assertTrue(view.ok and view.idle and view.draft == '')

    def test_standalone_token_only_after_the_notice(self):
        def check(history):
            window = FakeWindow(None, A, 's')
            window.history = history
            return steward2.View(window.render()).standalone_after(['CTX2#abc123'], 'HANDOFF-READY')
        notice = wrap('❯ ', '[CTX2#abc123] 上下文管家：' + '请定稿' * 40 + '写完单独回一行 HANDOFF-READY（整行只有这一个词），然后停手等我。')
        self.assertFalse(check(notice))                                             # 通知自己不算
        self.assertFalse(check(['⏺ HANDOFF-READY', ''] + notice))                   # 通知之前的不算
        self.assertFalse(check(notice + ['⏺ 我等会儿回 HANDOFF-READY 给你']))        # 夹在句子里不算
        self.assertFalse(check(['⏺ HANDOFF-READY']))                                # 没有通知记号不算
        self.assertTrue(check(notice + ['⏺ HANDOFF-READY']))
        self.assertTrue(check(notice + ['⏺ 卡写好了。', '', '  HANDOFF-READY', '']))


class CardChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'HANDOFF-x.md')

    def write(self, manual):
        with open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(manual + '\n' + steward2.SEP + '\n机械快照 %s' % os.urandom(4).hex())

    def test_missing_card_is_not_written(self):
        self.assertFalse(steward2.card_written(self.path, '', 100))

    def test_only_mechanical_part_changed_is_not_written(self):
        self.write(GOOD_CARD)
        baseline = steward2.manual_digest(self.path)
        self.write(GOOD_CARD)
        self.assertFalse(steward2.card_written(self.path, baseline, 100))

    def test_template_or_tiny_manual_is_not_written(self):
        self.write('# HANDOFF · 舰长交接（手写段）\n\n- 当前判断：<舰长在自动建卡占位段填写>\n' + '占位' * 80)
        self.assertFalse(steward2.card_written(self.path, '', 100))
        self.write('好了')
        self.assertFalse(steward2.card_written(self.path, '', 100))

    def test_changed_manual_is_written(self):
        self.write(GOOD_CARD)
        baseline = steward2.manual_digest(self.path)
        self.write(GOOD_CARD + '\n定稿完成')
        self.assertTrue(steward2.card_written(self.path, baseline, 100))


# ---------------------------------------------------------------- 状态机

class Machine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = Env(self)
        self.addCleanup(lambda: self.assertNotIn('iterm2', sys.modules))  # 测试全程不碰真实 iTerm

    async def to_notified(self, iterm=A, sid='old-a', key='captain', percent=86):
        env = self.env
        window = env.term.windows.get(iterm) or env.add(iterm, sid, percent)
        env.card(sid, GOOD_CARD)
        await env.tick()
        self.assertEqual(env.phase(key), 'NOTIFIED')
        return window

    def finalize(self, window, sid='old-a'):
        self.env.card(sid, GOOD_CARD + '\n定稿：%s' % self.env.clock.t)
        window.say('卡已定稿。\nHANDOFF-READY')

    # ---- 每条边
    async def test_full_cycle_walks_every_edge(self):
        env = self.env
        window = await self.to_notified()
        notice = window.typed('：本会话上下文已用')
        self.assertEqual(len(notice), 1)
        self.assertIn(env.card_path('old-a'), notice[0])
        self.assertIn('HANDOFF-READY', notice[0])
        self.finalize(window)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'READY')
        self.assertEqual(window.clear_typed(), [])
        await env.tick()
        self.assertEqual(window.clear_typed(), ['/clear'])
        self.assertEqual(window.clears, 1)
        await env.tick()                                   # 快照里会话号变了 → CLEARED → 同一轮叫新会话读卡
        self.assertEqual(env.phase('captain'), 'KICKED')
        kick = window.typed('先完整读旧卡')
        self.assertEqual(len(kick), 1)
        self.assertIn('你是「主舰长」', kick[0])
        self.assertIn(env.card_path('old-a'), kick[0])     # 读的是旧会话那张卡
        self.assertIn('TAKEOVER-DONE', kick[0])
        window.say('TAKEOVER-DONE')
        await env.tick()
        state = env.state('captain')
        self.assertEqual((state['phase'], state['session_id']), ('IDLE', window.sid))
        self.assertIsNotNone(state['last_handoff_at'])
        self.assertEqual(env.alerts(), {})
        for event in ('NOTIFIED', 'READY', 'CLEARED', 'KICKED', 'HANDOFF_COMPLETE'):
            self.assertIn('[captain] ' + event, env.log())

    async def test_kicked_returns_to_idle_after_5_minutes_without_done(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'KICKED')
        await env.tick(29)
        self.assertEqual(env.phase('captain'), 'KICKED')
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'IDLE')

    async def test_same_session_not_retriggered_within_10_minutes(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick(3)
        window.say('TAKEOVER-DONE')
        await env.tick()
        env.card(window.sid, GOOD_CARD)
        for _ in range(9):                                 # 新会话马上又到线：10 分钟内不触发
            env.snap(A, window.sid, 90)
            await env.tick(step=60)
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)
        for _ in range(3):
            env.snap(A, window.sid, 90)
            await env.tick(step=60)
        self.assertEqual(len(window.typed('：本会话上下文已用')), 2)

    # ---- 阈值
    async def test_below_threshold_nothing_is_typed(self):
        env = self.env
        window = env.add(A, 'old-a', 84)
        await env.tick(3)
        self.assertEqual(window.sent, [])
        self.assertEqual(env.phase('captain'), 'IDLE')

    async def test_token_threshold_and_per_target_override(self):
        env = self.env
        env.targets['captain'] = {'threshold': {'tokens': 600000}}
        env.targets['crewa'] = {'threshold': {'percent': 50}}
        captain, crewa = env.add(A, 'old-a', 61), env.add(B, 'old-b', 49)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(env.phase('crewa'), 'IDLE')
        self.assertEqual(crewa.sent, [])
        self.assertTrue(captain.sent)

    async def test_new_session_without_usage_does_not_trigger(self):
        env = self.env
        window = env.add(A, 'old-a', None)
        await env.tick(2)
        self.assertEqual(window.sent, [])

    async def test_idle_trigger_is_opt_in(self):
        env = self.env
        window = env.add(A, 'old-a', 30, age=45 * 60)
        await env.tick(2)
        self.assertEqual(window.sent, [])                  # 默认关：闲置 45 分钟、30 万 token 也不动
        env.defaults['idle_trigger'] = {'min_tokens': 200000, 'idle_minutes': 40}
        env.snap(A, 'old-a', 30, age=45 * 60)
        await env.tick(fresh=True)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertIn('趁缓存还热', window.typed('交接程序')[0])

    # ---- 快照过期不告警
    async def test_stale_snapshot_logs_once_and_never_alerts(self):
        env = self.env
        window = env.add(A, 'old-a', 95, age=200)
        await env.tick(5, step=60)
        self.assertEqual(window.sent, [])
        self.assertEqual(env.alerts(), {})
        self.assertEqual(env.log().count('SNAPSHOT_STALE'), 1)
        self.assertEqual(env.phase('captain'), 'IDLE')

    async def test_unreadable_snapshot_is_only_logged(self):
        env = self.env
        window = env.add(A, 'old-a', 95)
        with open(os.path.join(env.paths['snap'], A + '.json'), 'w') as stream:
            stream.write('{broken')
        await env.tick(3)
        self.assertEqual(window.sent, [])
        self.assertEqual(env.alerts(), {})
        self.assertEqual(env.log().count('[captain] SNAPSHOT_UNREADABLE'), 1)

    # ---- 红线：没有卡、没有 READY 绝不清
    async def never_clears(self, prepare):
        env = self.env
        window = await self.to_notified()
        prepare(window)
        await env.tick(50)                                 # 500 秒，还没到催的时间
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(window.clear_typed(), [])
        self.assertEqual(window.clears, 0)

    async def test_ready_said_but_card_untouched_never_clears(self):
        await self.never_clears(lambda window: window.say('HANDOFF-READY'))

    async def test_card_changed_but_no_ready_never_clears(self):
        await self.never_clears(lambda window: self.env.card('old-a', GOOD_CARD + '\n改了'))

    async def test_only_mechanical_part_changed_never_clears(self):
        def prepare(window):
            self.env.card('old-a', GOOD_CARD, mechanical='钩子刚刷新的机械快照')
            window.say('HANDOFF-READY')
        await self.never_clears(prepare)

    async def test_template_card_never_clears(self):
        def prepare(window):
            self.env.card('old-a', '# HANDOFF\n- 当前判断：<舰长在自动建卡占位段填写>\n' + '占位' * 100)
            window.say('HANDOFF-READY')
        await self.never_clears(prepare)

    async def test_ready_only_in_input_box_never_clears(self):
        def prepare(window):
            self.env.card('old-a', GOOD_CARD + '\n改了')
            window.draft = 'HANDOFF-READY'
        await self.never_clears(prepare)

    async def test_ready_inside_a_sentence_never_clears(self):
        def prepare(window):
            self.env.card('old-a', GOOD_CARD + '\n改了')
            window.say('我写完会回 HANDOFF-READY 的，稍等')
        await self.never_clears(prepare)

    async def test_card_reverted_after_ready_holds_the_clear(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'READY')
        env.card('old-a', GOOD_CARD)                       # 手写段又变回通知前的样子
        await env.tick(5)
        self.assertEqual(window.clear_typed(), [])
        os.remove(env.card_path('old-a'))                  # 卡没了
        await env.tick(5)
        self.assertEqual(window.clear_typed(), [])

    # ---- 催一次、30 分钟标要人看
    async def test_remind_once_at_10_minutes_then_needs_human_at_30(self):
        env = self.env
        window = await self.to_notified()
        await env.tick(58)
        self.assertEqual(window.typed('催一次'), [])
        await env.tick(4)
        self.assertEqual(len(window.typed('催一次')), 1)
        for _ in range(110):
            env.snap(A, 'old-a', 90)
            await env.tick()
        self.assertEqual(len(window.typed('催一次')), 1)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        for _ in range(12):
            env.snap(A, 'old-a', 90)
            await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(env.alerts('captain'), ['NO_READY'])
        self.assertEqual(window.clear_typed(), [])
        for _ in range(28):                                # 冷却 30 分钟内不再通知
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)
        for _ in range(4):
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(len(window.typed('：本会话上下文已用')), 2)
        self.assertEqual(env.alerts('captain'), ['NO_READY'])  # 还没 READY，告警留着给人看
        self.finalize(window)
        await env.tick()
        self.assertEqual(env.alerts(), {})

    # ---- /clear 只在空闲、空输入框、没弹窗时发
    async def test_clear_waits_until_the_turn_is_over(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.busy = True
        await env.tick(6)
        self.assertEqual(env.phase('captain'), 'READY')
        self.assertEqual(window.clear_typed(), [])
        window.busy, window.dialog = False, True
        await env.tick(6)
        self.assertEqual(window.clear_typed(), [])
        window.dialog, window.draft = False, '舰员刚打了一半的话'
        sent_before = list(window.sent)
        await env.tick(6)
        self.assertEqual(window.sent, sent_before)         # 输入框有字：一个键都不发
        self.assertEqual(window.draft, '舰员刚打了一半的话')
        window.draft = ''
        await env.tick()
        self.assertEqual(window.clear_typed(), ['/clear'])

    async def test_clear_not_entered_when_foreign_text_lands_in_the_box(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick()
        window.inject = '舰员同时打进来的字'
        await env.tick()
        self.assertEqual(window.clear_typed(), ['/clear'])
        self.assertEqual(window.sent[-1], '/clear')        # 没有补回车
        self.assertEqual(window.clears, 0)
        self.assertIn('CLEAR_TYPED_UNVERIFIED', env.log())

    async def test_unconfirmed_clear_is_never_blindly_resent(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.clear_works = False                          # 回车按了，但会话号没变，快照也没刷新
        await env.tick(40)
        self.assertEqual(window.clear_typed(), ['/clear'])
        self.assertEqual(env.alerts('captain'), ['CLEAR_UNCONFIRMED'])
        self.assertEqual(env.phase('captain'), 'READY')
        env.snap(A, 'old-a', 88)                            # 之后状态栏刷新过、还是旧会话：证明没清成，才重试
        window.clear_works = True
        await env.tick(15)
        self.assertEqual(window.clear_typed(), ['/clear', '/clear'])
        await env.tick(2)
        self.assertEqual(env.phase('captain'), 'KICKED')
        self.assertEqual(env.alerts(), {})

    async def test_our_own_clear_left_in_the_box_gets_its_enter(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.swallow_enter = True
        await env.tick(3)
        self.assertEqual(window.draft, '/clear')
        window.swallow_enter = False
        await env.tick(8)
        self.assertEqual(window.clear_typed(), ['/clear'])  # 没重打，只补了回车
        self.assertEqual(window.clears, 1)

    async def test_late_session_change_after_clear_still_gets_the_kick(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.clear_works = False
        await env.tick(12)
        self.assertEqual(env.alerts('captain'), ['CLEAR_UNCONFIRMED'])
        window.history = ['  Claude Code（新会话）', '']
        window.sid = 'late-new'
        env.snap(A, 'late-new', None)                       # 会话号过了 60 秒才变
        await env.tick()
        self.assertEqual(env.phase('captain'), 'KICKED')
        self.assertEqual(env.alerts(), {})

    async def test_ready_that_never_clears_is_flagged_after_30_minutes(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.busy = True
        for _ in range(185):
            env.snap(A, 'old-a', 90)
            await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(env.alerts('captain'), ['CLEAR_STUCK'])
        self.assertEqual(window.clear_typed(), [])

    async def test_compaction_during_handoff_cancels_the_cycle(self):
        env = self.env
        window = await self.to_notified()
        env.snap(A, 'old-a', 3)                             # Claude Code 自己先压缩了
        self.finalize(window)
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(window.clear_typed(), [])
        self.assertIn('COMPACTED_DURING_HANDOFF', env.log())

    # ---- 投递不确定 → 重试，不停摆
    async def test_unconfirmed_notice_retries_with_enter_only(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.swallow_enter = True
        await env.tick()
        state = env.state('captain')
        self.assertEqual((state['phase'], state['fail_count']), ('IDLE', 1))
        self.assertEqual(env.alerts(), {})
        window.swallow_enter = False
        for _ in range(3):
            env.snap(A, 'old-a', 86)
            await env.tick(step=60)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)  # 字没重打，只补回车

    async def test_our_unsent_notice_gets_its_enter_even_after_snapshot_goes_stale(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.swallow_enter = True
        await env.tick()
        self.assertIn('交接程序', window.draft)             # 我们的字躺在输入框里
        window.swallow_enter = False
        env.snap(A, 'old-a', 86, age=300)                    # 目标闲着，快照过期了
        await env.tick(6, step=60)
        self.assertEqual(window.draft, '')
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)

    async def test_vanished_unconfirmed_notice_is_not_resent_while_stale(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.swallow_text = True                           # 字没落进输入框，屏上哪儿都没有
        await env.tick()
        window.swallow_text = False
        env.snap(A, 'old-a', 86, age=300)                    # 目标闲下来了，快照过期
        await env.tick(6, step=60)                           # 过期就不算到线：不发新的
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(env.alerts(), {})

    async def test_swallowed_text_is_retyped_after_cooldown(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.swallow_text = True
        await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertNotIn('\r', window.sent)                 # 没见字落进输入框，不按回车
        window.swallow_text = False
        env.snap(A, 'old-a', 86)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')      # 冷却中
        for _ in range(3):
            env.snap(A, 'old-a', 86)
            await env.tick(step=60)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')

    async def test_queued_notice_is_confirmed_late_without_duplicate(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.busy, window.show_queue = True, False        # 目标在忙：消息排队了，但屏上看不到
        await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        window.flush_queue()
        for _ in range(3):
            env.snap(A, 'old-a', 86)
            await env.tick(step=60)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(len(window.typed('：本会话上下文已用')), 1)
        self.assertIn('"late": true', env.log())

    async def test_busy_target_takes_the_notice_into_its_queue(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        window.busy = True                                  # 排队区看得到：算送达
        await env.tick()
        self.assertEqual(env.phase('captain'), 'NOTIFIED')

    async def test_repeated_failures_flag_the_target_and_keep_going(self):
        env = self.env
        captain, crewa = env.add(A, 'old-a', 90), env.add(B, 'old-b', 90)
        env.card('old-a', GOOD_CARD)
        env.card('old-b', GOOD_CARD)
        env.term.broken.add(A)                              # 主舰长那个窗读屏一直出错
        for _ in range(14):
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(env.phase('captain'), 'IDLE')      # 没停摆，状态也没乱
        self.assertEqual(env.alerts('captain'), ['DELIVERY_FAILING'])
        self.assertEqual(captain.sent, [])
        self.assertEqual(env.phase('crewa'), 'NOTIFIED')   # 别的目标照常
        env.term.broken.clear()
        for _ in range(32):                                 # 冷却过后自己恢复，告警自动消
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(env.alerts('captain'), [])

    # ---- 输入框被占
    async def test_foreign_draft_is_left_alone_and_flagged_after_10_minutes(self):
        env = self.env
        window = env.add(A, 'old-a', 90)
        env.card('old-a', GOOD_CARD)
        window.draft = '别人打了一半还没发'
        for _ in range(9):
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(window.sent, [])
        self.assertEqual(env.alerts(), {})
        for _ in range(3):
            env.snap(A, 'old-a', 90)
            await env.tick(step=60)
        self.assertEqual(window.sent, [])
        self.assertEqual(window.draft, '别人打了一半还没发')
        self.assertEqual(env.alerts('captain'), ['INPUT_OCCUPIED'])
        self.assertEqual(env.phase('captain'), 'IDLE')
        window.draft = ''
        env.snap(A, 'old-a', 90)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(env.alerts(), {})

    # ---- 会话号意外变化
    async def test_manual_clear_while_idle_adopts_the_new_session(self):
        env = self.env
        window = env.add(A, 'old-a', 40)
        await env.tick()
        env.snap(A, 'manual-new', None)
        await env.tick()
        state = env.state('captain')
        self.assertEqual((state['phase'], state['session_id']), ('IDLE', 'manual-new'))
        self.assertEqual(window.sent, [])

    async def test_manual_clear_while_notified_goes_back_to_idle(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        env.snap(A, 'manual-new', None)                     # 人自己 /clear 了
        window.history = ['  Claude Code（新会话）', '']
        await env.tick(3)
        state = env.state('captain')
        self.assertEqual((state['phase'], state['session_id']), ('IDLE', 'manual-new'))
        self.assertEqual(window.clear_typed(), [])
        self.assertEqual(window.typed('先完整读旧卡'), [])
        self.assertIn('EXTERNAL_SESSION_CHANGE', env.log())

    async def test_manual_clear_while_ready_before_our_clear_goes_back_to_idle(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.busy = True
        await env.tick(2)
        self.assertEqual(env.phase('captain'), 'READY')
        env.snap(A, 'manual-new', None)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(window.typed('先完整读旧卡'), [])

    async def test_session_change_while_kicked_adopts_it(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'KICKED')
        env.snap(A, 'again-new', None)
        await env.tick()
        state = env.state('captain')
        self.assertEqual((state['phase'], state['session_id']), ('IDLE', 'again-new'))

    # ---- 目标窗消失
    async def test_window_gone_does_not_stop_anything(self):
        env = self.env
        captain, crewa = env.add(A, 'old-a', 90), env.add(B, 'old-b', 90)
        env.card('old-a', GOOD_CARD)
        env.card('old-b', GOOD_CARD)
        captain.gone = True
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(captain.sent, [])
        self.assertEqual(env.phase('crewa'), 'NOTIFIED')
        self.assertIn('"result": "NO_WINDOW"', env.log())

    async def test_window_gone_while_notified_times_out_cleanly(self):
        env = self.env
        window = await self.to_notified()
        window.gone = True
        for _ in range(185):
            env.snap(A, 'old-a', 90)
            await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertIn('NO_READY', env.alerts('captain'))
        self.assertEqual(env.log().count('WINDOW_MISSING'), 1)
        self.assertEqual(window.clear_typed(), [])

    async def test_target_missing_from_roster_is_skipped(self):
        env = self.env
        env.roster({'captain': A})                          # 名册里没有 crewa
        env.add(A, 'old-a', 40)
        await env.tick(2)
        self.assertEqual(env.log().count('[crewa] TARGET_UNRESOLVED'), 1)
        self.assertEqual(env.phase('captain'), 'IDLE')

    # ---- 名册换了窗口号
    async def test_roster_moves_window_same_session_continues_there(self):
        env = self.env
        old = await self.to_notified()
        new = env.add(C, 'old-a', 90)                        # 同一会话在新标签里续上
        new.history = list(old.history)
        env.roster({'captain': C, 'crewa': B})
        self.finalize(new)
        await env.tick(3)
        self.assertEqual(env.phase('captain'), 'KICKED')
        self.assertEqual(new.clear_typed(), ['/clear'])
        self.assertEqual(old.clear_typed(), [])           # 旧窗一个字都没再收到
        self.assertIn('WINDOW_MOVED', env.log())
        self.assertEqual(env.state('captain')['iterm'], C)

    async def test_roster_moves_window_other_session_restarts_idle(self):
        env = self.env
        old = await self.to_notified()
        new = env.add(C, 'other-session', 20)
        env.roster({'captain': C, 'crewa': B})
        await env.tick(2)
        state = env.state('captain')
        self.assertEqual((state['phase'], state['session_id'], state['iterm']), ('IDLE', 'other-session', C))
        self.assertEqual(new.sent, [])

    # ---- 多目标互不影响
    async def test_targets_are_independent(self):
        env = self.env
        captain = await self.to_notified()                   # 主舰长一直不回 READY
        crewa = env.add(B, 'old-b', 90)
        crewb = env.add(X, 'old-x', 10)
        env.card('old-b', GOOD_CARD)
        await env.tick()
        self.assertEqual(env.phase('crewa'), 'NOTIFIED')
        self.finalize(crewa, 'old-b')
        await env.tick(3)
        crewa.say('TAKEOVER-DONE')
        await env.tick()
        self.assertEqual(env.phase('crewa'), 'IDLE')
        self.assertEqual(crewa.clears, 1)
        self.assertIn('你是「舰员甲线」', crewa.typed('先完整读旧卡')[0])  # 角色名取名册标签名
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.assertEqual(captain.clears, 0)
        self.assertEqual(crewb.sent, [])                      # 没到线的不碰
        status = json.load(open(os.path.join(env.paths['data'], 'status.json'), encoding='utf-8'))
        self.assertEqual(sorted(status['targets']), ['captain', 'crewa', 'crewb'])
        self.assertEqual(status['targets']['crewb']['iterm'], X)
        self.assertIn('heartbeat', status)

    async def test_error_in_one_target_does_not_block_the_next(self):
        env = self.env
        env.add(A, 'old-a', 90)
        crewa = env.add(B, 'old-b', 90)
        env.card('old-a', GOOD_CARD)
        env.card('old-b', GOOD_CARD)
        env.term.explode.add(A)                              # 主舰长这一步抛了个没料到的异常
        await env.tick()
        self.assertIn('[captain] ERROR', env.log())
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual(env.phase('crewa'), 'NOTIFIED')
        self.assertTrue(crewa.sent)

    async def test_only_limits_to_one_target(self):
        env = self.env
        captain, crewa = env.add(A, 'old-a', 90), env.add(B, 'old-b', 90)
        env.card('old-a', GOOD_CARD)
        env.card('old-b', GOOD_CARD)
        await env.tick(only='crewa')
        self.assertEqual(captain.sent, [])
        self.assertTrue(crewa.sent)

    # ---- 外层重起后从 state 接着走
    async def test_restart_between_every_tick_still_completes(self):
        env = self.env
        window = env.add(A, 'old-a', 86)
        env.card('old-a', GOOD_CARD)
        await env.tick(fresh=True)
        self.assertEqual(env.phase('captain'), 'NOTIFIED')
        self.finalize(window)
        await env.tick(fresh=True)
        self.assertEqual(env.phase('captain'), 'READY')
        await env.tick(fresh=True)
        await env.tick(fresh=True)
        self.assertEqual(env.phase('captain'), 'KICKED')
        window.say('TAKEOVER-DONE')
        await env.tick(fresh=True)
        self.assertEqual(env.phase('captain'), 'IDLE')
        self.assertEqual((window.clears, len(window.typed('：本会话上下文已用')), len(window.typed('先完整读旧卡'))), (1, 1, 1))

    async def test_restart_right_after_typing_clear_does_not_type_it_again(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        window.clear_works = False
        await env.tick(2)
        self.assertEqual(window.clear_typed(), ['/clear'])
        await env.tick(20, fresh=True)
        self.assertEqual(window.clear_typed(), ['/clear'])

    async def test_corrupt_state_file_starts_from_idle(self):
        env = self.env
        env.add(A, 'old-a', 40)
        with open(os.path.join(env.paths['data'], 'state-captain.json'), 'w') as stream:
            stream.write('{half')
        await env.tick()
        self.assertEqual(env.phase('captain'), 'IDLE')

    # ---- dry-run
    async def test_dry_run_never_types_and_keeps_live_state_clean(self):
        env = self.env
        window = env.add(A, 'old-a', 90)
        env.card('old-a', GOOD_CARD)
        env.term.forbid_send = True
        await env.tick(3, dry_run=True, probe=True)
        self.assertEqual(window.sent, [])
        self.assertIn('WOULD_TYPE', env.log(dry=True))
        self.assertIn(env.card_path('old-a'), env.log(dry=True))
        self.assertEqual(env.state('captain', dry=True)['phase'], 'IDLE')
        self.assertFalse(os.path.exists(os.path.join(env.paths['data'], 'state-captain.json')))
        status = json.load(open(os.path.join(env.paths['data'], 'dryrun', 'status.json'), encoding='utf-8'))
        captain = status['targets']['captain']
        self.assertEqual((status['mode'], captain['would_trigger'], captain['screen'], captain['card_exists']), ('dry-run', True, 'ok', True))

    async def test_dry_run_would_type_clear_but_does_not(self):
        env = self.env
        window = await self.to_notified()
        self.finalize(window)
        await env.tick()
        self.assertEqual(env.phase('captain'), 'READY')
        os.makedirs(os.path.join(env.paths['data'], 'dryrun'))
        os.replace(os.path.join(env.paths['data'], 'state-captain.json'), os.path.join(env.paths['data'], 'dryrun', 'state-captain.json'))
        env.term.forbid_send = True
        await env.tick(3, dry_run=True)
        self.assertEqual(window.clear_typed(), [])
        self.assertIn('"kind": "clear"', env.log(dry=True))


class Entry(unittest.TestCase):
    def test_refuses_to_run_live_without_the_interlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['FLEET_HOME'] = tmp
            path = os.path.join(tmp, 'targets.json')
            with open(path, 'w') as stream:
                json.dump({'registry': tmp + '/r.json', 'snapshot_dir': tmp, 'handoff_dir': tmp, 'data_dir': tmp + '/data', 'targets': {'captain': {}}}, stream)
            saved = os.environ.pop('CTXSTEWARD2_LIVE', None)
            try:
                self.assertEqual(steward2.main(['--once', '--config', path]), 2)
            finally:
                if saved is not None:
                    os.environ['CTXSTEWARD2_LIVE'] = saved
            self.assertFalse(os.path.exists(tmp + '/data'))
            self.assertNotIn('iterm2', sys.modules)

    def test_sample_config_loads(self):
        import json, tempfile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(root, 'targets.sample.json')
        os.environ.setdefault('FLEET_HOME', tempfile.mkdtemp())
        cfg = steward2.load_config(sample)
        self.assertIn('captain', cfg['targets'])
        self.assertEqual(cfg['defaults']['threshold']['percent'], 85)



class FleetHomeRequired(unittest.TestCase):
    def test_main_exits_without_fleet_home(self):
        saved = dict(os.environ)
        try:
            os.environ.pop('FLEET_HOME', None)
            os.environ.pop('CTXSTEWARD2_LIVE', None)
            with self.assertRaises(SystemExit) as ctx:
                steward2.main(['--dry-run', '--once', '--config', '/dev/null'])
            self.assertIn('FLEET_HOME', str(ctx.exception))
        finally:
            os.environ.clear()
            os.environ.update(saved)

if __name__ == '__main__':
    unittest.main()
