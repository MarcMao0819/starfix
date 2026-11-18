# -*- coding: utf-8 -*-
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import board
import server

DEMO = os.path.join(HERE, 'demo')
SERVER = os.path.join(HERE, 'server.py')
INDEX = os.path.join(HERE, 'index.html')
HOST = '127.0.0.1'


def build(root, now=None, demo=False):
    return board.build_board(
        root,
        os.path.join(root, 'task-activator.json'),
        os.path.join(root, 'fleet-snapshot.txt'),
        os.path.join(root, 'steward', 'data', 'status.json'),
        now=now,
        demo=demo,
    )


def has_cjk(value):
    return any('\u4e00' <= ch <= '\u9fff' for ch in (value or ''))


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)


class BoardData(unittest.TestCase):
    def test_demo_shape_statuses_and_conclusions(self):
        data = build(DEMO)
        self.assertTrue(data['activator_present'])
        self.assertEqual(data['captain']['role'], '舰长')
        self.assertEqual(data['captain']['watermark'], 28)
        self.assertEqual(data['captain']['awaiting_ids'], ['T-B1', 'T-B2', 'T-B3'])
        self.assertEqual(data['captain']['pending_questions'], 3)
        self.assertEqual(data['captain']['pending_question_ids'], ['Q-C', 'Q-A', 'Q-B'])
        self.assertNotIn('task_title', data['captain'])
        self.assertNotIn('others', data['captain'])
        self.assertEqual([person['role'] for person in data['crew']], ['舰员甲', '舰员乙', '舰员丙'])
        by_role = {person['role']: person for person in data['crew']}
        jia, yi, bing = by_role['舰员甲'], by_role['舰员乙'], by_role['舰员丙']
        self.assertEqual(jia['task_id'], 'T-A2')
        self.assertEqual(jia['status'], '施工中')
        self.assertEqual(jia['watermark'], 64)
        self.assertEqual({item['id']: item['status'] for item in jia['others']}, {'T-A1': '已完成', 'T-A3': '已完成'})
        self.assertEqual(yi['status'], '已交回执待验收')
        self.assertEqual(yi['watermark'], 41)
        self.assertEqual({yi['task_id']} | {item['id'] for item in yi['others']}, {'T-B1', 'T-B2', 'T-B3'})
        self.assertEqual(bing['task_id'], 'T-C1')
        self.assertEqual(bing['status'], '卡住')
        self.assertIn('Q-A', bing['stuck_on'])
        self.assertEqual(bing['watermark'], 12)
        self.assertEqual(bing['others'][0]['id'], 'T-C2')
        self.assertEqual(bing['others'][0]['status'], '待开工')
        self.assertEqual([row['qid'] for row in data['questions']], ['Q-C', 'Q-A', 'Q-B', 'Q-D'])
        self.assertTrue(all(row['unanswered'] for row in data['questions'][:3]))
        self.assertFalse(data['questions'][3]['unanswered'])
        self.assertEqual(data['questions'][0]['tasks'], ['T-C1', 'T-B3'])
        self.assertEqual(data['questions'][0]['path'], 'task-activator.json')
        receipts = {item['ref']: item['conclusion'] for item in data['exchanges'] if item['kind'] == 'receipt'}
        self.assertEqual(receipts, {'T-A1': 'PASS', 'T-A3': 'PASS', 'T-B1': 'PASS', 'T-B2': 'FAIL', 'T-B3': 'BLOCKED'})
        self.assertTrue(all(item['direction'] == 'in' for item in data['exchanges'] if item['kind'] == 'receipt'))
        books = [item for item in data['exchanges'] if item['kind'] == 'dispatch']
        self.assertEqual(len(books), 8)
        self.assertTrue(all(item['preview'] and not item['file_missing'] for item in books))
        done = {item['ref'] for item in data['exchanges'] if item['kind'] == 'acceptance'}
        self.assertEqual(done, {'T-A1', 'T-A3'})
        self.assertEqual(len([item for item in data['exchanges'] if item['kind'] == 'question']), 4)
        self.assertEqual([item['ref'] for item in data['exchanges'] if item['kind'] == 'answer'], ['Q-D'])
        moments = [item['at'] for item in data['exchanges'] if item['at'] is not None]
        self.assertEqual(moments, sorted(moments, reverse=True))
        self.assertEqual(len(data['windows']), 4)
        self.assertEqual(data['windows'][1]['state'], 'Working')
        self.assertEqual(data['windows'][0]['seat_en'], 'Window A')
        self.assertIn('file_missing', data['exchanges'][0])

    def test_three_conclusions_on_the_first_line(self):
        self.assertEqual(board.conclusion_of('PASS 做完了\n后文'), 'PASS')
        self.assertEqual(board.conclusion_of('FAIL 没过\n'), 'FAIL')
        self.assertEqual(board.conclusion_of('BLOCKED 等拍板\n'), 'BLOCKED')
        self.assertEqual(board.conclusion_of('终态：PASS\n'), 'PASS')
        self.assertEqual(board.conclusion_of('结论: FAIL 不行\n'), 'FAIL')
        self.assertEqual(board.conclusion_of('状态：BLOCKED\n'), 'BLOCKED')
        self.assertEqual(board.conclusion_of('\n\n通过 可以收\n'), 'PASS')
        self.assertEqual(board.conclusion_of('失败 不能收\n'), 'FAIL')
        self.assertEqual(board.conclusion_of('受阻 等栏目\n'), 'BLOCKED')
        self.assertEqual(board.conclusion_of('还在写\nPASS 做完了\n'), '')
        self.assertEqual(board.conclusion_of('PARTIAL 做了一半\n'), '')

    def test_fiction_stamp_uses_the_local_day(self):
        now = datetime.now().astimezone().replace(hour=15, minute=0, second=0, microsecond=0)
        got = board.parse_stamp('<日期> 09:10', now.timestamp())
        expect = now.replace(hour=9, minute=10)
        self.assertEqual(int(got), int(expect.timestamp()))
        earlier = board.parse_stamp('<前日> 16:00', now.timestamp())
        self.assertEqual(int(earlier), int((now - timedelta(days=1)).replace(hour=16, minute=0).timestamp()))

    def test_elapsed_from_fiction_and_from_mtime(self):
        afternoon = datetime.now().astimezone().replace(hour=15, minute=0, second=0, microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            write_json(os.path.join(tmp, 'task-activator.json'), {
                'tasks': [{
                    'id': 'T-1', 'title': '一笔', 'status': '施工中', 'owner': '舰员甲',
                    'book': '${FLEET_HOME}/book.md', 'receipt': 'receipt.md', 'blocker': '',
                    'created': '<日期> 09:10', 'updated': '<日期> 09:10',
                    'history': [['<日期> 09:10', '施工中', '派单']],
                }],
                'decisions': [],
            })
            book = os.path.join(tmp, 'book.md')
            receipt = os.path.join(tmp, 'receipt.md')
            with open(book, 'w', encoding='utf-8') as handle:
                handle.write('# 任务书\n\n把按钮挪到中间。\n\n时间：<日期> 08:00\n')
            with open(receipt, 'w', encoding='utf-8') as handle:
                handle.write('PASS 挪好了。\n\n时间：<日期> 12:00\n')
            now = afternoon.timestamp()
            os.utime(book, (now - 3 * 3600, now - 3 * 3600))
            os.utime(receipt, (now - 3600, now - 3600))
            data = build(tmp, now=now)
            self.assertEqual(data['crew'][0]['status'], '已交回执待验收')
            self.assertEqual(data['crew'][0]['conclusion'], 'PASS')
            self.assertAlmostEqual(data['crew'][0]['elapsed_seconds'], 3600, delta=2)
            dispatch = next(item for item in data['exchanges'] if item['kind'] == 'dispatch')
            handed = next(item for item in data['exchanges'] if item['kind'] == 'receipt')
            self.assertAlmostEqual(dispatch['at'], now - 3 * 3600, delta=2)
            self.assertAlmostEqual(handed['at'], now - 3600, delta=2)
            self.assertEqual(handed['conclusion'], 'PASS')
            self.assertFalse(dispatch['file_missing'])
            shown = build(tmp, now=now, demo=True)
            written_book = board.parse_stamp('<日期> 08:00', now)
            written_receipt = board.parse_stamp('<日期> 12:00', now)
            demo_dispatch = next(item for item in shown['exchanges'] if item['kind'] == 'dispatch')
            demo_receipt = next(item for item in shown['exchanges'] if item['kind'] == 'receipt')
            self.assertAlmostEqual(demo_dispatch['at'], written_book, delta=2)
            self.assertAlmostEqual(demo_receipt['at'], written_receipt, delta=2)
            self.assertAlmostEqual(shown['crew'][0]['elapsed_seconds'], now - written_receipt, delta=2)

    def test_demo_dispatch_before_receipt_before_acceptance(self):
        now = datetime.now().astimezone().replace(microsecond=0).timestamp()
        data = build(DEMO, now=now, demo=True)
        grouped = {}
        for item in data['exchanges']:
            if item['kind'] in ('dispatch', 'receipt', 'acceptance'):
                grouped.setdefault(item['ref'], {})[item['kind']] = item['at']
        self.assertGreaterEqual(len(grouped), 8)
        saw_receipt = False
        saw_accept = False
        for ref, kinds in grouped.items():
            self.assertIsNotNone(kinds.get('dispatch'), ref)
            if 'receipt' in kinds:
                saw_receipt = True
                self.assertLess(kinds['dispatch'], kinds['receipt'], ref)
            if 'acceptance' in kinds:
                saw_accept = True
                self.assertIn('receipt', kinds, ref)
                self.assertLess(kinds['receipt'], kinds['acceptance'], ref)
        self.assertTrue(saw_receipt)
        self.assertTrue(saw_accept)
        dispatch = grouped['T-A1']['dispatch']
        self.assertNotAlmostEqual(dispatch, os.path.getmtime(os.path.join(DEMO, 'tasks', 'T-A1.md')), delta=1)

    def test_demo_english_fields_have_no_chinese(self):
        data = build(DEMO, demo=True)
        for person in data['crew']:
            self.assertTrue(person['task_title_en'])
            self.assertFalse(has_cjk(person['task_title_en']))
            if person['note']:
                self.assertFalse(has_cjk(person['note_en']))
            if person['stuck_on'] and person['stuck_on'] != '等舰长验收':
                self.assertNotIn('Owner', person['stuck_on'])
                self.assertIn('负责人', person['stuck_on'])
                self.assertFalse(has_cjk(person['stuck_on_en']))
            for item in person['others']:
                self.assertFalse(has_cjk(item['title_en']))
        for row in data['questions']:
            self.assertFalse(has_cjk(row['question_en']))
        for row in data['windows']:
            self.assertFalse(has_cjk(row['seat_en']))
            self.assertFalse(has_cjk(row['name_en']))
            self.assertFalse(has_cjk(row['state_en']))
        for item in data['exchanges']:
            if item.get('title_en'):
                self.assertFalse(has_cjk(item['title_en']))
            if item['kind'] in ('question', 'acceptance'):
                self.assertFalse(has_cjk(item.get('detail_en')))
            if item['kind'] in ('dispatch', 'receipt'):
                self.assertFalse(has_cjk(item.get('preview_en')))

    def test_missing_and_broken_files_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = board.build_board(tmp, os.path.join(tmp, 'no.json'), None, None)
            self.assertFalse(data['activator_present'])
            self.assertIsNone(data['captain'])
            self.assertEqual(data['crew'], [])
            self.assertEqual(data['questions'], [])
            self.assertEqual(data['exchanges'], [])
            self.assertIsNone(data['windows'])
            with open(os.path.join(tmp, 'task-activator.json'), 'w', encoding='utf-8') as handle:
                handle.write('{')
            with open(os.path.join(tmp, 'fleet-snapshot.txt'), 'w', encoding='utf-8') as handle:
                handle.write('# 注释\n坏行\n窗甲 | seat-a | 舰长 | model-a | 空闲 | -\n')
            with open(os.path.join(tmp, 'status.json'), 'w', encoding='utf-8') as handle:
                handle.write('{no')
            data = board.build_board(
                tmp,
                os.path.join(tmp, 'task-activator.json'),
                os.path.join(tmp, 'fleet-snapshot.txt'),
                os.path.join(tmp, 'status.json'),
            )
            self.assertFalse(data['activator_present'])
            self.assertEqual(len(data['windows']), 1)
            self.assertEqual(data['windows'][0]['name'], '舰长')
            self.assertIsNone(data['captain'])

    def test_does_not_read_outside_the_fleet_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'root')
            os.makedirs(root)
            secret = os.path.join(tmp, 'secret.md')
            with open(secret, 'w', encoding='utf-8') as handle:
                handle.write('PASS 不应该被读到\n')
            write_json(os.path.join(root, 'task-activator.json'), {
                'tasks': [{
                    'id': 'T-1', 'title': '外面的文件', 'status': '施工中', 'owner': '舰员甲',
                    'book': secret, 'receipt': secret, 'blocker': '',
                }],
                'decisions': [],
            })
            data = build(root)
            self.assertEqual(data['crew'][0]['status'], '施工中')
            self.assertNotIn('receipt', [item['kind'] for item in data['exchanges']])
            dispatch = next(item for item in data['exchanges'] if item['kind'] == 'dispatch')
            self.assertTrue(dispatch['file_missing'])
            self.assertNotIn('不应该', dispatch['preview'])
            self.assertNotIn(secret, json.dumps(data, ensure_ascii=False))

    def test_page_does_not_load_anything_remote(self):
        with open(INDEX, encoding='utf-8') as handle:
            html = handle.read()
        self.assertNotIn('http://', html)
        self.assertNotIn('https://', html)
        self.assertNotIn('<script src', html)
        self.assertNotIn('innerHTML', html)
        self.assertNotIn('timeZone', html)
        self.assertIn('awaiting_ids', html)
        for name in ('board.py', 'index.html', 'README.md', 'README.en.md'):
            with open(os.path.join(HERE, name), encoding='utf-8') as handle:
                blob = handle.read()
            # 时间一律用本机钟：不许写死时区名，也不许写死固定偏移。
            self.assertIsNone(re.search(r'\b(Asia|Europe|America|Africa|Australia|Pacific)/[A-Z]', blob), name)
            self.assertNotIn('timedelta(hours=', blob, name)


class BoardServer(unittest.TestCase):
    def test_refuses_without_fleet_home(self):
        env = os.environ.copy()
        env.pop('FLEET_HOME', None)
        env['PYTHONUNBUFFERED'] = '1'
        proc = subprocess.run(
            [sys.executable, SERVER, '--port', '9'],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('缺少环境变量 FLEET_HOME', proc.stderr)
        self.assertNotIn('Traceback', proc.stderr + proc.stdout)

    def test_bad_port_exits_with_one_sentence(self):
        env = os.environ.copy()
        env['FLEET_HOME'] = DEMO
        env['FLEET_BOARD_PORT'] = 'nope'
        env['PYTHONUNBUFFERED'] = '1'
        proc = subprocess.run(
            [sys.executable, SERVER],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('FLEET_BOARD_PORT', proc.stderr)
        self.assertNotIn('Traceback', proc.stderr + proc.stdout)

    def test_demo_serves_without_fleet_home(self):
        env = os.environ.copy()
        env['FLEET_HOME'] = os.path.join(tempfile.gettempdir(), 'fleet-board-not-a-real-home')
        env['PYTHONUNBUFFERED'] = '1'
        paths = None
        old = os.environ.get('FLEET_HOME')
        os.environ['FLEET_HOME'] = env['FLEET_HOME']
        try:
            paths = server.runtime_paths(True)
        finally:
            if old is None:
                os.environ.pop('FLEET_HOME', None)
            else:
                os.environ['FLEET_HOME'] = old
        self.assertTrue(paths['root'].endswith(os.path.join('scripts', 'fleet-board', 'demo')) or paths['root'].endswith('demo'))
        self.assertNotIn('not-a-real-home', paths['root'])
        proc = subprocess.Popen(
            [sys.executable, SERVER, '--demo', '--port', '0'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 5)
            line = proc.stdout.readline() if ready else ''
            self.assertTrue(line.startswith('PORT '), line)
            port = int(line.split()[1])
            with urllib.request.urlopen('http://%s:%s/api/board' % (HOST, port), timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            self.assertTrue(data['activator_present'])
            self.assertEqual(len(data['crew']), 3)
            self.assertEqual(data['captain']['awaiting_ids'], ['T-B1', 'T-B2', 'T-B3'])
            handed = next(item for item in data['exchanges'] if item['kind'] == 'receipt' and item['ref'] == 'T-A1')
            self.assertNotAlmostEqual(handed['at'], os.path.getmtime(os.path.join(DEMO, 'receipts', 'T-A1.md')), delta=1)
            with urllib.request.urlopen('http://%s:%s/' % (HOST, port), timeout=5) as resp:
                html = resp.read().decode('utf-8')
            self.assertIn('舰队看板', html)
            request = urllib.request.Request(
                'http://%s:%s/api/board' % (HOST, port), data=b'{}', method='POST')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 405)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
