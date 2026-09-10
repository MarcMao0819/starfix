#!/usr/bin/env python3
"""轮询<项目>ERP飞书各会话新消息，打印新收到的人类回复（供 Monitor 捕获）。
会话登记表: feishu-chats.json  游标: feishu-cursor.json"""
import json, os, time, urllib.request
import fcntl, sys

# <日期> 单实例锁：跨会话监听复活曾致三实例并跑抢游标吃消息；第二实例直接退出
# <日期> 分线监听：FEISHU_POLL_CURSOR 指定独立游标文件（锁随游标文件走，允许不同游标的实例并存）；
# FEISHU_POLL_ONLY / FEISHU_POLL_EXCLUDE 逗号分隔会话名，只轮询/排除这些会话
_D0 = os.path.dirname(os.path.abspath(__file__))
_CURSOR_PATH = os.environ.get('FEISHU_POLL_CURSOR') or os.path.join(_D0, 'feishu-cursor.json')
_LOCK = open(_CURSOR_PATH + '.lock', 'w')
try:
    fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print('[poll] 已有实例在跑，本实例退出', flush=True)
    sys.exit(0)


# ── 配置来源：环境变量 + $FLEET_HOME/im-contacts.json ──────────────────
# 仓里不留真实应用 ID / 收件人 ID / 密钥；缺配置时报人话不抛栈。
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import im_config as _imcfg
_CFG = _imcfg.load_or_exit()
APP_ID = _CFG['app_id']
CC_ID = _CFG['cc_id']
CONTACTS = _CFG['contacts']
TITLES = _CFG['titles']


def secret():
    """应用密钥：由 im_config 从 IM_APP_SECRET / app_secret_file 取，不落仓库。"""
    return _CFG['app_secret']

_SECRET_FILE = os.environ.get('FEISHU_POLL_SECRET_FILE')  # 分线：另一应用的密钥文件（如<基座项目>机器人）
D = os.path.dirname(os.path.abspath(__file__))
CHATS = os.environ.get('FEISHU_POLL_CHATS') or os.path.join(D, 'feishu-chats.json')
CURSOR = _CURSOR_PATH
ONLY = {x.strip() for x in os.environ.get('FEISHU_POLL_ONLY','').split(',') if x.strip()}
EXCLUDE = {x.strip() for x in os.environ.get('FEISHU_POLL_EXCLUDE','').split(',') if x.strip()}

sys.path.insert(0, D)
from feishu_net import api_request

def api(path, payload=None, token=None):
    # 直连优先，失败自动走136隧道（见 feishu_net.py）
    return api_request(path, payload=payload, token=token)

def main():
    errs = 0
    while True:
        try:
            tok = api('/auth/v3/tenant_access_token/internal',
                      {'app_id': APP_ID, 'app_secret': secret()})['tenant_access_token']
            chats = json.load(open(CHATS)) if os.path.exists(CHATS) else {}
            cur = json.load(open(CURSOR)) if os.path.exists(CURSOR) else {}
            for name, chat_id in chats.items():
                if (ONLY and name not in ONLY) or (name in EXCLUDE):
                    continue
                d = api(f'/im/v1/messages?container_id_type=chat&container_id={chat_id}'
                        f'&page_size=20&sort_type=ByCreateTimeDesc', token=tok)
                items = (d.get('data') or {}).get('items') or []
                last = cur.get(chat_id, '0')
                new = [m for m in items
                       if m.get('create_time', '0') > last
                       and (m.get('sender') or {}).get('sender_type') != 'app']
                for m in sorted(new, key=lambda x: x['create_time']):
                    mt = m.get('msg_type', '?')
                    try:
                        c = json.loads(m.get('body', {}).get('content', '{}'))
                        if mt == 'text':
                            body = c.get('text', '')
                        elif mt == 'post':
                            # 图文消息：抽出全部文字段并标注图片数
                            parts, imgs = [], 0
                            for line in c.get('content', []):
                                for el in line:
                                    if el.get('tag') == 'text':
                                        parts.append(el.get('text', ''))
                                    elif el.get('tag') == 'img':
                                        imgs += 1
                            body = '[图文]' + ''.join(parts) + (f'（附{imgs}图，待拉取）' if imgs else '')
                        elif mt in ('image', 'file', 'media'):
                            fname = c.get('file_name', '')
                            body = f'[{mt}{"·"+fname if fname else ""}，待拉取]'
                        else:
                            body = f'[{mt}消息]'
                    except Exception:
                        body = '[解析失败:' + mt + ']'
                    print(f'【飞书回复·{name}】{body}', flush=True)
                if items:
                    cur[chat_id] = max(m.get('create_time', '0') for m in items)
            json.dump(cur, open(CURSOR, 'w'))
            errs = 0
        except Exception as e:
            errs += 1
            if errs >= 3:
                print(f'[poll-err x{errs}] {e}', flush=True)
        time.sleep(90)

if __name__ == '__main__':
    main()
