#!/usr/bin/env python3
"""飞书API请求器：直连优先，失败自动回退<跳板机> SSH 隧道（127.0.0.1:${PORT_IM_TUNNEL} → open.feishu.cn:443）。
背景：<日期> 本机 clash 对 open.feishu.cn 的 TLS 被本地秒掐（见记忆 feishu-api-clash-direct-rule），
隧道由主窗维护：ssh -N -L 127.0.0.1:${PORT_IM_TUNNEL}:open.feishu.cn:443 ${SSH_USER}@${HOST_LAN}
本模块被 feishu-poll.py / feishu-send.py / feishu-fetch-media.py 共用。"""
import json, os, ssl, socket, http.client, urllib.request

# 隧道本地端口：默认 8443，可用 PORT_IM_TUNNEL 覆盖
TUNNEL = ('127.0.0.1', int(os.environ.get('PORT_IM_TUNNEL', '8443')))
HOST = 'open.feishu.cn'


def _tunnel_conn(timeout):
    ctx = ssl.create_default_context()
    sock = socket.create_connection(TUNNEL, timeout=timeout)
    ssock = ctx.wrap_socket(sock, server_hostname=HOST)
    conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    conn.sock = ssock
    return conn


def api_request(path, payload=None, token=None, timeout=15):
    """POST(带payload)/GET(不带) open.feishu.cn/open-apis+path，返回解析后的JSON。"""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    try:
        req = urllib.request.Request('https://' + HOST + '/open-apis' + path,
                                     data=body, headers=headers)
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except OSError:
        pass
    conn = _tunnel_conn(timeout)
    try:
        conn.request('POST' if body is not None else 'GET', '/open-apis' + path,
                     body=body, headers=headers)
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def api_download(path, token, timeout=30):
    """GET 二进制资源，返回 (bytes, content_type)。"""
    headers = {'Authorization': 'Bearer ' + token}
    try:
        req = urllib.request.Request('https://' + HOST + path, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read(), resp.headers.get('Content-Type', '')
    except OSError:
        pass
    conn = _tunnel_conn(timeout)
    try:
        conn.request('GET', path, headers=headers)
        resp = conn.getresponse()
        return resp.read(), resp.getheader('Content-Type', '')
    finally:
        conn.close()
