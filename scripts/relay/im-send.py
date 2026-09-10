#!/usr/bin/env python3
"""<项目>ERP飞书发信（Owner <日期> 机制：质量→<质量负责人1> 生产→<生产负责人1> 每条自动抄送Owner）。
用法: python3 feishu-send.py <收件人名> "<白话内容>"
收件人表在下方 CONTACTS，<质量负责人1>/<生产负责人1> open_id 待补。"""
import sys, json, subprocess, urllib.request


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

# 称谓（Owner <日期> 定）：发消息抬头一律用尊称，不用"X工"

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from feishu_net import api_request

def api(path, payload, token=None):
    # 直连优先失败走136隧道（feishu_net.py）；瞬时抖动重试3次
    import time
    for attempt in range(3):
        try:
            return api_request(path, payload=payload, token=token)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))

def main():
    name, text = sys.argv[1], sys.argv[2]
    oid = CONTACTS.get(name)
    assert oid, f'{name} 的 open_id 未配置'
    tok = api('/auth/v3/tenant_access_token/internal', {'app_id': APP_ID, 'app_secret': secret()})['tenant_access_token']
    def send(to, body):
        r = api('/im/v1/messages?receive_id_type=open_id',
                {'receive_id': to, 'msg_type': 'text', 'content': json.dumps({'text': body}, ensure_ascii=False)}, tok)
        print(to[:12], r.get('code'), r.get('msg'))
        chat = (r.get('data') or {}).get('chat_id')
        if chat:
            import os
            cf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu-chats.json')
            chats = json.load(open(cf)) if os.path.exists(cf) else {}
            who = name if to == oid else 'Owner'
            if chats.get(who) != chat:
                chats[who] = chat
                json.dump(chats, open(cf, 'w'), ensure_ascii=False, indent=1)
        return r.get('code') == 0
    ok = send(oid, text)
    if ok and oid != CC_ID:
        send(CC_ID, f'【抄送】发给{name}的消息：\n{text}')

if __name__ == '__main__':
    main()
