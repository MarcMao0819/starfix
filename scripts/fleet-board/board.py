# -*- coding: utf-8 -*-
"""把舰队目录里已有的文件收成看板数据。只读，不写任何文件。"""
import importlib.util
import json
import os
import re
import time
from datetime import datetime, timedelta

CONCLUSIONS = ('PASS', 'FAIL', 'BLOCKED')
RANK = {'施工中': 0, '已交回执待验收': 1, '卡住': 2, '待开工': 3, '已完成': 4}
CREW_ORDER = ['舰员甲', '舰员乙', '舰员丙', '舰员丁', '舰员戊', '舰员己', '舰员庚', '舰员辛', '舰员壬']
_ROLE = {
    'captain': '舰长',
    '主窗': '舰长',
    '主窗口': '舰长',
    'main window': '舰长',
    'crew a': '舰员甲',
    'crew b': '舰员乙',
    'crew c': '舰员丙',
    'crew d': '舰员丁',
    'crew e': '舰员戊',
    'crew f': '舰员己',
    'crew g': '舰员庚',
    'crew h': '舰员辛',
    'crew i': '舰员壬',
}
_REAL = re.compile(r'^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$')
_FICTION = re.compile(r'^(<日期>|<前日>)\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$')
_WRITTEN = re.compile(r'^时间[：:]\s*((?:<日期>|<前日>)\s+\d{1,2}:\d{2}(?::\d{2})?)\s*$')
_LABEL = re.compile(r'^(?:当前)?(?:终态|结论|状态)\s*[：:]\s*')
_CODE = re.compile(r'(PASS|FAIL|BLOCKED)\b', re.IGNORECASE)


def _load_expand():
    path = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'steward', '_fleet_paths.py'))
    spec = importlib.util.spec_from_file_location('fleet_board_paths', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.expand_path


expand_path = _load_expand()


def text(value):
    if value is None:
        return ''
    return str(value).strip()


def canon_role(name):
    raw = text(name)
    if not raw:
        return ''
    return _ROLE.get(raw.lower(), raw)


def local_now(now):
    """本机钟。naive 的 fromtimestamp 再 astimezone，跟 datetime.now().astimezone() 同一时区。"""
    return datetime.fromtimestamp(float(now)).astimezone()


def parse_stamp(value, now):
    """真实时间按本机钟。`<日期>` 是今天，`<前日>` 是昨天。"""
    raw = text(value)
    if not raw:
        return None
    matched = _REAL.match(raw)
    if matched:
        year, month, day, hour, minute, second = matched.groups()
        try:
            parsed = datetime(
                int(year), int(month), int(day),
                int(hour or 0), int(minute or 0), int(second or 0),
            ).astimezone()
        except ValueError:
            return None
        return parsed.timestamp()
    matched = _FICTION.match(raw)
    if not matched:
        return None
    day_token, hour, minute, second = matched.groups()
    hour, minute, second = int(hour), int(minute), int(second or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    parsed = local_now(now).replace(hour=hour, minute=minute, second=second, microsecond=0)
    if day_token == '<前日>':
        parsed -= timedelta(days=1)
    return parsed.timestamp()


def written_stamp(blob, now):
    """演示文件里写明的时间。真实目录不走这里。"""
    for raw in (blob or '').splitlines():
        matched = _WRITTEN.match(raw.strip())
        if matched:
            return parse_stamp(matched.group(1), now)
    return None


def doc_clock(info, now, demo):
    if not info:
        return None
    if demo:
        stamp = written_stamp(info.get('text'), now)
        if stamp is not None:
            return stamp
    return info.get('mtime')


def conclusion_of(blob):
    """只认第一条有字的行：通过、失败、受阻。开头空行跳过。"""
    line = ''
    for raw in (blob or '').splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line:
        return ''
    line = line.strip('*_`"\' ')
    line = _LABEL.sub('', line).strip('*_`"\' ')
    matched = _CODE.match(line)
    if matched:
        return matched.group(1).upper()
    if line.startswith('通过'):
        return 'PASS'
    if line.startswith('失败'):
        return 'FAIL'
    if line.startswith('受阻'):
        return 'BLOCKED'
    return ''


def preview_text(blob, limit=8):
    if not blob:
        return ''
    chunk = '\n'.join(blob.splitlines()[:limit])
    if len(chunk) > 600:
        chunk = chunk[:600]
    return chunk


def locate(root, value):
    """路径必须落在舰队目录里面。跑出去的不读，也不把原路径带回页面。"""
    if not root or not isinstance(value, str) or not value.strip():
        return None
    try:
        expanded = expand_path(value.strip(), root)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(expanded, str) or not expanded:
        return None
    root_real = os.path.realpath(root)
    full_real = os.path.realpath(expanded)
    try:
        if os.path.commonpath([root_real, full_real]) != root_real:
            return None
    except ValueError:
        return None
    return full_real


def relative_to_root(root, full):
    if not full:
        return ''
    try:
        rel = os.path.relpath(full, os.path.realpath(root))
    except ValueError:
        return ''
    if rel.startswith('..') or os.path.isabs(rel):
        return ''
    return rel


def english_beside(root, full):
    if not full or not full.endswith('.md'):
        return None
    sibling = os.path.realpath(full[:-3] + '.en.md')
    root_real = os.path.realpath(root)
    try:
        if os.path.commonpath([root_real, sibling]) != root_real or not os.path.isfile(sibling):
            return None
        with open(sibling, encoding='utf-8-sig', errors='replace') as handle:
            return handle.read(4096)
    except (OSError, ValueError):
        return None


def file_head(root, value):
    full = locate(root, value)
    rel = relative_to_root(root, full)
    empty = {'path': rel, 'text': None, 'mtime': None, 'text_en': None}
    if not full or not os.path.isfile(full):
        return empty
    try:
        with open(full, encoding='utf-8-sig', errors='replace') as handle:
            blob = handle.read(4096)
        return {
            'path': rel,
            'text': blob,
            'mtime': os.path.getmtime(full),
            'text_en': english_beside(root, full),
        }
    except OSError:
        return empty


def cached_head(root, cache, value):
    key = value if isinstance(value, str) else ''
    if key not in cache:
        cache[key] = file_head(root, key)
    return cache[key]


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8-sig') as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_snapshot(blob):
    rows = []
    for line in (blob or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        parts = [part.strip() for part in line.split(' | ')]
        if len(parts) < 6:
            continue
        rows.append({
            'seat': parts[0],
            'session': parts[1],
            'name': parts[2],
            'model': parts[3],
            'state': parts[4],
            'clue': ' | '.join(parts[5:]),
        })
    return rows


def attach_window_copy(rows, path):
    if not rows or not path.endswith('.txt'):
        return rows
    alt = path[:-4] + '.en.txt'
    if not os.path.isfile(alt):
        return rows
    try:
        with open(alt, encoding='utf-8-sig', errors='replace') as handle:
            extra = parse_snapshot(handle.read())
    except OSError:
        return rows
    for row, copy in zip(rows, extra):
        row['seat_en'] = copy['seat']
        row['name_en'] = copy['name']
        row['state_en'] = copy['state']
        row['clue_en'] = copy['clue']
    return rows


def load_windows(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as handle:
            rows = parse_snapshot(handle.read())
    except OSError:
        return None
    return attach_window_copy(rows, path)


def as_percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def load_watermarks(path):
    data = load_json(path)
    if data is None:
        return None
    targets = data.get('targets')
    if not isinstance(targets, dict):
        return {}
    marks = {}
    for key, info in targets.items():
        if not isinstance(info, dict):
            continue
        role = canon_role(info.get('role') or key)
        percent = as_percent(info.get('used_percentage'))
        if role and percent is not None:
            marks[role] = percent
    return marks


def activator_name(path, root):
    full = locate(root, path) if path else None
    if full:
        return relative_to_root(root, full)
    base = os.path.basename(path or '')
    return base


def elapsed(since, now):
    if since is None:
        return None
    return max(0, int(now - since))


def history_rows(task):
    rows = task.get('history')
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, (list, tuple)) and len(row) >= 2]


def entered_at(task, raw_status, now):
    found = None
    for row in history_rows(task):
        if text(row[1]) == raw_status:
            stamp = parse_stamp(row[0], now)
            if stamp is not None:
                found = stamp
    if found is not None:
        return found
    return parse_stamp(task.get('updated'), now) or parse_stamp(task.get('created'), now)


def history_note(task, raw_status):
    note = ''
    for row in history_rows(task):
        if text(row[1]) == raw_status and len(row) >= 3:
            note = text(row[2]) or note
    return note


def display_status(raw, conclusion, blocker):
    if raw == '已完成':
        return '已完成'
    if conclusion in CONCLUSIONS:
        return '已交回执待验收'
    if blocker:
        return '卡住'
    if raw in ('待开工', '施工中'):
        return raw
    return raw or '待开工'


def status_since(task, status, raw, receipt_mtime, now):
    if status == '已交回执待验收':
        if receipt_mtime:
            return receipt_mtime
        return entered_at(task, raw or '施工中', now)
    if status == '卡住':
        return parse_stamp(task.get('updated'), now) or parse_stamp(task.get('created'), now)
    return entered_at(task, raw or status, now)


def stuck_text(status, blocker):
    if blocker and status != '已完成':
        return blocker
    if status == '已交回执待验收':
        return '等舰长验收'
    return ''


def task_ids(item):
    raw = item.get('tasks')
    if not isinstance(raw, list):
        return []
    return [text(one) for one in raw if text(one)]


def sort_tasks(items):
    return sorted(items, key=lambda item: (
        RANK.get(item['status'], 9),
        -(item['elapsed_seconds'] or 0),
        item['id'],
    ))


def brief(item):
    return {
        'id': item['id'],
        'title': item['title'],
        'title_en': item.get('title_en') or '',
        'status': item['status'],
        'conclusion': item['conclusion'],
    }


def copy_en(event, info, title_en='', detail_en=''):
    if title_en:
        event['title_en'] = title_en
    if detail_en:
        event['detail_en'] = detail_en
    blob = (info or {}).get('text_en') if isinstance(info, dict) else None
    if blob:
        event['preview_en'] = preview_text(blob)


def make_event(**kwargs):
    event = {
        'id': '',
        'at': None,
        'direction': 'out',
        'kind': '',
        'who': '',
        'ref': '',
        'title': '',
        'conclusion': '',
        'detail': '',
        'path': '',
        'preview': '',
        'file_missing': False,
    }
    event.update(kwargs)
    return event


def fill_doc(event, root, cache, values):
    remembered = ''
    for value in values:
        if not text(value):
            continue
        info = cached_head(root, cache, value)
        if info['text'] is not None:
            event['path'] = info['path']
            event['preview'] = preview_text(info['text'])
            event['file_missing'] = False
            return info
        if info['path'] and not remembered:
            remembered = info['path']
    event['path'] = remembered
    event['preview'] = ''
    event['file_missing'] = True
    return None


def build_views(tasks, root, cache, now, demo=False):
    views = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = text(task.get('id'))
        if not tid:
            continue
        raw = text(task.get('status'))
        blocker = text(task.get('blocker'))
        receipt = cached_head(root, cache, task.get('receipt'))
        conclusion = conclusion_of(receipt['text']) if receipt['text'] is not None else ''
        receipt_at = doc_clock(receipt, now, demo) if conclusion else None
        status = display_status(raw, conclusion, blocker)
        stuck = stuck_text(status, blocker)
        views.append({
            'id': tid,
            'title': text(task.get('title')),
            'title_en': text(task.get('title_en')),
            'owner': text(task.get('owner')) or '未指定',
            'status': status,
            'raw': raw,
            'conclusion': conclusion,
            'elapsed_seconds': elapsed(status_since(task, status, raw, receipt_at, now), now),
            'stuck_on': stuck,
            'stuck_on_en': text(task.get('blocker_en')) if stuck and stuck == blocker else '',
            'note': text(task.get('note')),
            'note_en': text(task.get('note_en')),
            'task': task,
        })
    return views


def build_questions(items, now, path_name):
    pending = []
    done = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        qid = text(item.get('qid')) or ('ask-%s' % (index + 1))
        answer = text(item.get('answer'))
        unanswered = not answer
        body_parts = [text(item.get('question')), text(item.get('detail')), text(item.get('recommend'))]
        en_parts = [text(item.get('question_en')), text(item.get('detail_en')), text(item.get('recommend_en'))]
        asked = parse_stamp(item.get('asked_at'), now)
        row = {
            'qid': qid,
            'question': text(item.get('question')),
            'question_en': text(item.get('question_en')),
            'body': '\n'.join(part for part in body_parts if part),
            'body_en': '\n'.join(part for part in en_parts if part),
            'who': text(item.get('who')) or 'Owner',
            'tasks': task_ids(item),
            'answer': answer,
            'unanswered': unanswered,
            'elapsed_seconds': elapsed(asked, now) if unanswered else None,
            'path': path_name,
            '_asked': asked if asked is not None else 0,
            '_answered': parse_stamp(item.get('answered_at'), now) or 0,
            '_len': len(task_ids(item)),
        }
        (pending if unanswered else done).append(row)
    pending.sort(key=lambda row: (-row['_len'], row['_asked'], row['qid']))
    done.sort(key=lambda row: (-row['_answered'], row['qid']))
    return pending + done


def public_question(row):
    return {
        'qid': row['qid'],
        'question': row['question'],
        'question_en': row.get('question_en') or '',
        'body': row['body'],
        'body_en': row.get('body_en') or '',
        'who': row['who'],
        'tasks': row['tasks'],
        'answer': row['answer'],
        'unanswered': row['unanswered'],
        'elapsed_seconds': row['elapsed_seconds'],
        'path': row['path'],
    }


def person_from(role, primary, others, marks, pending_questions=None):
    person = {
        'role': role,
        'task_id': primary['id'] if primary else '',
        'task_title': primary['title'] if primary else '',
        'task_title_en': (primary.get('title_en') or '') if primary else '',
        'status': primary['status'] if primary else '待开工',
        'elapsed_seconds': primary['elapsed_seconds'] if primary else None,
        'stuck_on': primary['stuck_on'] if primary else '',
        'stuck_on_en': (primary.get('stuck_on_en') or '') if primary else '',
        'note': primary['note'] if primary else '',
        'note_en': (primary.get('note_en') or '') if primary else '',
        'conclusion': primary['conclusion'] if primary else '',
        'watermark': (marks or {}).get(canon_role(role)),
        'others': [brief(item) for item in others],
    }
    if pending_questions is not None:
        person['pending_questions'] = pending_questions
    return person


def build_captain(views, questions, marks):
    """舰长卡只留两笔数和单号，不把舰员的明细再抄一遍。"""
    awaiting = sorted(item['id'] for item in views if item['status'] == '已交回执待验收')
    pending_ids = [row['qid'] for row in questions if row['unanswered']]
    return {
        'role': '舰长',
        'watermark': (marks or {}).get('舰长'),
        'awaiting_ids': awaiting,
        'pending_questions': len(pending_ids),
        'pending_question_ids': pending_ids,
    }


def build_people(views, questions, marks):
    captain = build_captain(views, questions, marks)
    groups = {}
    for item in views:
        groups.setdefault(item['owner'], []).append(item)

    def crew_key(name):
        if name in CREW_ORDER:
            return (0, CREW_ORDER.index(name), name)
        return (1, 0, name)

    crew = []
    for name in sorted(groups, key=crew_key):
        ordered = sort_tasks(groups[name])
        crew.append(person_from(name, ordered[0], ordered[1:], marks))
    return captain, crew


def build_exchanges(tasks, questions, root, cache, now, path_name, demo=False):
    events = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = text(task.get('id'))
        if not tid:
            continue
        title = text(task.get('title'))
        title_en = text(task.get('title_en'))
        owner = text(task.get('owner')) or '未指定'
        raw = text(task.get('status'))
        dispatch = make_event(
            id='dispatch:' + tid, direction='out', kind='dispatch', who=owner,
            ref=tid, title=title,
        )
        info = fill_doc(dispatch, root, cache, [task.get('book')])
        dispatch['at'] = doc_clock(info, now, demo) or parse_stamp(task.get('created'), now) or entered_at(task, '待开工', now)
        copy_en(dispatch, info, title_en)
        events.append(dispatch)

        receipt = cached_head(root, cache, task.get('receipt'))
        if receipt['text'] is not None:
            handed = make_event(
                id='receipt:' + tid, at=doc_clock(receipt, now, demo), direction='in', kind='receipt',
                who=owner, ref=tid, title=title, conclusion=conclusion_of(receipt['text']),
                path=receipt['path'], preview=preview_text(receipt['text']), file_missing=False,
            )
            copy_en(handed, receipt, title_en)
            events.append(handed)

        if raw == '已完成':
            detail = text(task.get('evidence')) or text(task.get('note')) or history_note(task, '已完成')
            detail_en = text(task.get('evidence_en')) or text(task.get('note_en'))
            accepted = make_event(
                id='acceptance:' + tid, direction='out', kind='acceptance', who=owner,
                ref=tid, title=title, detail=detail,
                at=parse_stamp(task.get('updated'), now) or entered_at(task, '已完成', now),
            )
            accepted_doc = fill_doc(accepted, root, cache, [task.get('receipt'), task.get('book')])
            copy_en(accepted, accepted_doc, title_en, detail_en)
            events.append(accepted)

    for row in questions:
        asked = make_event(
            id='question:' + row['qid'], at=row.get('_asked') or None,
            direction='out', kind='question', who=row['who'], ref=row['qid'],
            detail=row['body'], path=path_name,
        )
        copy_en(asked, None, detail_en=row.get('body_en') or '')
        events.append(asked)
        if not row['unanswered']:
            events.append(make_event(
                id='answer:' + row['qid'], at=row.get('_answered') or None,
                direction='in', kind='answer', who=row['who'], ref=row['qid'],
                detail=row['answer'], path=path_name,
            ))
    events.sort(key=lambda item: (item['at'] is None, -(item['at'] or 0), item['id']))
    return events


def build_board(root, activator, snapshot, steward, now=None, demo=False):
    now = time.time() if now is None else float(now)
    data = load_json(activator)
    present = data is not None
    tasks = data.get('tasks') if present else []
    decisions = data.get('decisions') if present else []
    if not isinstance(tasks, list):
        tasks = []
    if not isinstance(decisions, list):
        decisions = []
    cache = {}
    path_name = activator_name(activator, root) if present else ''
    views = build_views(tasks, root, cache, now, demo) if present else []
    questions = build_questions(decisions, now, path_name) if present else []
    captain, crew = build_people(views, questions, load_watermarks(steward)) if present else (None, [])
    exchanges = build_exchanges(tasks, questions, root, cache, now, path_name, demo) if present else []
    return {
        'activator_present': present,
        'captain': captain,
        'crew': crew,
        'questions': [public_question(row) for row in questions],
        'exchanges': exchanges,
        'windows': load_windows(snapshot),
    }
