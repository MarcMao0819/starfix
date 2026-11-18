# -*- coding: utf-8 -*-
"""本机回环上的只读看板。没设 FLEET_HOME 又不带 --demo 就退出。"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import board


def demo_dir():
    return os.path.join(HERE, 'demo')


def steward_status_path(root):
    override = os.environ.get('STEWARD_DATA_DIR')
    if override:
        return os.path.join(board.expand_path(override, root), 'status.json')
    targets = os.path.join(root, 'steward', 'targets.json')
    if os.path.isfile(targets):
        try:
            with open(targets, encoding='utf-8-sig') as handle:
                cfg = json.load(handle)
            data_dir = cfg.get('data_dir') if isinstance(cfg, dict) else None
            if isinstance(data_dir, str) and data_dir.strip():
                return os.path.join(board.expand_path(data_dir, root), 'status.json')
        except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
            pass
    return os.path.join(root, 'steward', 'data', 'status.json')


def runtime_paths(demo):
    if demo:
        root = demo_dir()
        return {
            'root': root,
            'activator': os.path.join(root, 'task-activator.json'),
            'snapshot': os.path.join(root, 'fleet-snapshot.txt'),
            'steward': os.path.join(root, 'steward', 'data', 'status.json'),
            'demo': True,
        }
    root = os.environ.get('FLEET_HOME')
    if not root:
        return None
    return {
        'root': root,
        'activator': os.environ.get('ACTIVATOR_JSON') or os.path.join(root, 'task-activator.json'),
        'snapshot': os.environ.get('FLEET_SNAPSHOT') or os.path.join(root, 'fleet-snapshot.txt'),
        'steward': steward_status_path(root),
        'demo': False,
    }


def choose_port(explicit):
    if explicit is not None:
        if explicit < 0 or explicit > 65535:
            sys.stderr.write('端口号不在可用范围内，看板没能启动。\n')
            return None
        return explicit
    raw = os.environ.get('FLEET_BOARD_PORT', '8765')
    if not str(raw).isdigit():
        sys.stderr.write('FLEET_BOARD_PORT 不是端口号，看板没能启动。\n')
        return None
    port = int(raw)
    if port > 65535:
        sys.stderr.write('端口号不在可用范围内，看板没能启动。\n')
        return None
    return port


class Handler(BaseHTTPRequestHandler):
    paths = None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if path == '/api/board':
            self._send_json()
            return
        if path in ('/', '/index.html'):
            self._send_file(os.path.join(HERE, 'index.html'), 'text/html; charset=utf-8')
            return
        self.send_error(404)

    def do_POST(self):
        self.send_error(405)

    def do_PUT(self):
        self.send_error(405)

    def do_DELETE(self):
        self.send_error(405)

    def log_message(self, fmt, *args):
        return

    def _send_json(self):
        paths = self.paths
        payload = board.build_board(
            paths['root'], paths['activator'], paths['snapshot'], paths['steward'],
            demo=bool(paths.get('demo')))
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send(200, 'application/json; charset=utf-8', body)

    def _send_file(self, path, content_type):
        try:
            with open(path, 'rb') as handle:
                body = handle.read()
        except OSError:
            self.send_error(404)
            return
        self._send(200, content_type, body)

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description='本机回环上的只读舰队看板')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int)
    args = parser.parse_args(argv)
    paths = runtime_paths(args.demo)
    if paths is None:
        sys.stderr.write('缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md\n')
        return 1
    port = choose_port(args.port)
    if port is None:
        return 1
    Handler.paths = paths
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        sys.stderr.write('本机回环地址上的端口打不开，看板没能启动。\n')
        return 1
    httpd.daemon_threads = True
    bound = httpd.server_address[1]
    print('PORT %s' % bound, flush=True)
    print('http://127.0.0.1:%s' % bound, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
