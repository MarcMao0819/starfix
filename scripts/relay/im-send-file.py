# <日期> 应用分线：FEISHU_APP_ID / FEISHU_SECRET_FILE / FEISHU_CHATS 环境变量可切到<基座项目>机器人
import os as _os_env
#!/usr/bin/env python3
"""向 feishu-chats.json 中的一个会话上传并发送文件。

用法：python3 feishu-send-file.py <会话名> <本地文件路径>
"""
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid


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

HERE = os.path.dirname(os.path.abspath(__file__))
CHATS_FILE = _os_env.environ.get('FEISHU_CHATS') or os.path.join(HERE, 'feishu-chats.json')

sys.path.insert(0, HERE)
import feishu_net


def api(path, payload=None, token=None):
    """Use the shared JSON network layer with the same transient retry policy as feishu-send.py."""
    for attempt in range(3):
        try:
            return feishu_net.api_request(path, payload=payload, token=token)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def file_type(path):
    return {
        '.xlsx': 'xlsx',
        '.docx': 'docx',
        '.pdf': 'pdf',
    }.get(os.path.splitext(path)[1].lower(), 'stream')


def multipart_body(path):
    """Build the Feishu im/v1/files body without putting file bytes on stdout or logs."""
    boundary = '----<项目>-' + uuid.uuid4().hex
    filename = os.path.basename(path).replace('"', "'").replace('\r', '').replace('\n', '')
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    chunks = [
        ('--%s\r\nContent-Disposition: form-data; name="file_type"\r\n\r\n%s\r\n'
         % (boundary, file_type(path))).encode(),
        ('--%s\r\nContent-Disposition: form-data; name="file_name"\r\n\r\n%s\r\n'
         % (boundary, filename)).encode(),
        ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
         'Content-Type: %s\r\n\r\n' % (boundary, filename, content_type)).encode(),
    ]
    with open(path, 'rb') as handle:
        chunks.extend((handle.read(), b'\r\n', ('--%s--\r\n' % boundary).encode()))
    return boundary, b''.join(chunks)


def multipart_request(path, token, timeout=15):
    """Upload with feishu_net's direct-first / <跳板机>-tunnel fallback because api_request is JSON-only."""
    boundary, body = multipart_body(path)
    headers = {
        'Content-Type': 'multipart/form-data; boundary=' + boundary,
        'Authorization': 'Bearer ' + token,
    }
    api_path = '/open-apis/im/v1/files'
    try:
        request = urllib.request.Request('https://' + feishu_net.HOST + api_path, data=body, headers=headers)
        return json.load(urllib.request.urlopen(request, timeout=timeout))
    except OSError:
        pass

    connection = feishu_net._tunnel_conn(timeout)
    try:
        connection.request('POST', api_path, body=body, headers=headers)
        return json.loads(connection.getresponse().read())
    finally:
        connection.close()


def upload(path, token):
    for attempt in range(3):
        try:
            return multipart_request(path, token)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def main(argv):
    if len(argv) != 3:
        raise SystemExit('用法: python3 feishu-send-file.py <会话名> <本地文件路径>')
    chat_name, local_path = argv[1], argv[2]
    if not os.path.isfile(local_path):
        raise SystemExit('本地文件不存在: ' + local_path)
    with open(CHATS_FILE, encoding='utf-8') as handle:
        chats = json.load(handle)
    chat_id = chats.get(chat_name)
    if not chat_id:
        raise SystemExit('不存在的会话名: ' + chat_name)

    token_response = api('/auth/v3/tenant_access_token/internal',
                         {'app_id': APP_ID, 'app_secret': secret()})
    token = token_response.get('tenant_access_token')
    if not token:
        raise SystemExit('tenant_access_token 获取失败')
    uploaded = upload(local_path, token)
    if uploaded.get('code') != 0 or not (uploaded.get('data') or {}).get('file_key'):
        raise SystemExit('文件上传失败: code=%s msg=%s' % (uploaded.get('code'), uploaded.get('msg')))

    response = api('/im/v1/messages?receive_id_type=chat_id', {
        'receive_id': chat_id,
        'msg_type': 'file',
        'content': json.dumps({'file_key': uploaded['data']['file_key']}, ensure_ascii=False),
    }, token)
    print(chat_id, response.get('code'), response.get('msg'))
    return 0 if response.get('code') == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
