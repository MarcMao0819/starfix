#!/usr/bin/env python3
"""飞书对话全量归档：把 feishu-chats.json 里每个会话的完整历史导出为
迁移备份/飞书对话/<姓名>.md（幂等全量重写，含图片/文件的 message_id 供 feishu-fetch-media.py 拉取）。

立此脚本的原因（<日期>）：员工在飞书里给的事实与我方许下的承诺，此前只活在
⑤路轮询器的瞬时输出和当时舰长的上下文里，项目里没有它的留痕层——<质量负责人1> 08-21 说的
「UDI 由生产部自打标贴」被记忆里的旧假设盖了 12 天，差点把 UDI v2 整条线带偏。
对话必须是项目的一等信息源：晨检/日结跑一次本脚本，对账表按档案逐条结案。
用法: python3 feishu-archive.py            # 全部会话
      python3 feishu-archive.py <质量负责人1>      # 只导某人
"""
import json, sys, os, datetime
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
from feishu_net import api_request

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

OUT = os.environ.get('IM_ARCHIVE_DIR') or os.path.join(_imcfg.fleet_home(), 'im-archive')


def render(it):
    mt = it.get('msg_type')
    body = (it.get('body') or {}).get('content', '')
    try:
        c = json.loads(body)
    except Exception:
        c = {}
    if mt == 'text':
        return c.get('text', '')
    if mt == 'post':
        parts = []
        for para in (c.get('content') or []):
            for el in para:
                if el.get('tag') == 'text':
                    parts.append(el.get('text', ''))
        return '[图文]' + ''.join(parts)
    if mt in ('image', 'file', 'media'):
        fn = c.get('file_name')
        return f'[{mt} message_id={it["message_id"]}' + (f' {fn}' if fn else '') + ']'
    return f'[{mt}]'


def main():
    only = set(sys.argv[1:])
    tok = api_request('/auth/v3/tenant_access_token/internal',
                      {'app_id': APP_ID, 'app_secret': secret()}).get('tenant_access_token')
    if not tok:
        raise SystemExit('token 获取失败')
    chats = json.load(open(os.path.join(D, 'feishu-chats.json'), encoding='utf-8'))
    os.makedirs(OUT, exist_ok=True)
    for name, chat in chats.items():
        if only and name not in only:
            continue
        msgs, pt = [], ''
        while True:
            q = (f'/im/v1/messages?container_id_type=chat&container_id={chat}'
                 f'&sort_type=ByCreateTimeAsc&page_size=50' + (f'&page_token={pt}' if pt else ''))
            data = (api_request(q, token=tok).get('data') or {})
            for it in data.get('items') or []:
                ts = datetime.datetime.fromtimestamp(int(it['create_time']) / 1000).strftime('%Y-%m-%d %H:%M')
                who = name if (it.get('sender') or {}).get('sender_type') == 'user' else '机器人(我方)'
                msgs.append((ts, who, render(it)))
            if not data.get('has_more'):
                break
            pt = data.get('page_token', '')
        path = os.path.join(OUT, f'{name}.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'# 飞书对话档案 · {name}（chat {chat}）\n\n导出 {datetime.datetime.now():%Y-%m-%d %H:%M}，共 {len(msgs)} 条；'
                    f'机器人=<项目>ERP飞书助手（Owner授权的舰长代理）。图片/文件按 message_id 可用 feishu-fetch-media.py 拉取。\n\n')
            for ts, who, text in msgs:
                f.write(f'**{ts} {who}**：{text}\n\n')
        human = sum(1 for m in msgs if m[1] != '机器人(我方)')
        print(f'{name}: {len(msgs)}条(对方{human}) -> {path}')


if __name__ == '__main__':
    main()
