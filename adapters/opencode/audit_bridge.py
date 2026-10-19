"""Materialize upstream audit programs with explicit platform/dependency bindings."""
import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import re
import shlex
import subprocess
import sys


def prepare(request):
    source = Path(request['sourceRoot']) / 'trajectory'
    home = Path(request['home']).resolve()
    root = home / 'audit' / 'source'
    data = home / 'audit' / 'data'
    pilot = data / 'changeset-audit'
    alerts = home / 'audit' / 'alerts'
    for directory in (root, pilot, alerts, home / 'audit' / 'tmp'):
        directory.mkdir(parents=True, exist_ok=True)
    config = request['config']
    replacements = {
        **config.get('pathBindings', {}),
        '${FLEET_HOME}/<项目>ERP/迁移备份/回执': alerts.as_posix(),
        '${TRAJ_HOME}': root.as_posix(), '${TRAJ_DATA_DIR}': data.as_posix(),
        '${FLEET_HOME}': home.as_posix(), '${FLEET_INTEGRATION_REPO}': Path(request['directory']).as_posix(),
        '/usr/local/bin/docker': 'docker',
    }
    for marker, key in (('${DB_CONTAINER}', 'container'), ('${DB_NAME}', 'database'), ('feat/<项目>-integration', 'baseRef')):
        if key in config:
            replacements[marker] = config[key]

    def bind(value):
        for old, new in replacements.items():
            value = value.replace('git -C ' + old, 'git -C ' + shlex.quote(new))
            value = value.replace(old, new)
        return value

    class BindConstants(ast.NodeTransformer):
        def visit_Call(self, node):
            snapshot = config.get('snapshot')
            if file.name == 'status_snapshot.py' and snapshot and isinstance(node.func, ast.Attribute) and node.func.attr == 'rowcount' and node.args and isinstance(node.args[0], ast.Constant):
                field = {'库登记单清单': 'minimumRegistrations', '判决指纹台账': 'minimumFingerprints'}.get(node.args[0].value)
                if field:
                    for keyword in node.keywords:
                        if keyword.arg == 'want_at_least': keyword.value = ast.Constant(snapshot[field])
            return self.generic_visit(node)

        def visit_JoinedStr(self, node):
            values = []
            for value in node.values:
                marker = '${' + value.value.id + '}' if isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name) else None
                if marker in replacements and values and isinstance(values[-1], ast.Constant) and values[-1].value.endswith('$'):
                    prefix = values[-1].value[:-1]
                    replacement = shlex.quote(replacements[marker]) if prefix.endswith('git -C ') else replacements[marker]
                    values[-1] = ast.Constant(prefix + replacement)
                else: values.append(value)
            node.values = values
            return self.generic_visit(node)

        def visit_Assign(self, node):
            if any(isinstance(t, ast.Name) and t.id == 'GIT_SUBCMD_RE' for t in node.targets):
                # Same read-only subcommands; only tokenization changes so a
                # correctly quoted Windows work directory is not rejected.
                token = r'''(?:'[^']*'|"[^"]*"|[^\s'"]+)+'''
                expression = r'^git(\s+--no-optional-locks)?(\s+-C\s+' + token + r')?(\s+-c\s+' + token + r')?\s+(cat-file|diff|show|rev-parse|status|grep|merge-base|log|worktree)\b'
                node.value = ast.Call(func=ast.Attribute(value=ast.Name(id='re', ctx=ast.Load()), attr='compile', ctx=ast.Load()), args=[ast.Constant(expression)], keywords=[])
            return self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                if file.name == 'status_snapshot.py' and config.get('snapshot') and node.value == 'CS-<日期>-0067':
                    return ast.copy_location(ast.Constant(config['snapshot']['sentinelChangeset']), node)
                return ast.copy_location(ast.Constant(bind(node.value)), node)
            return node

    def write_bound(destination, content):
        if isinstance(content, str): content = content.encode('utf-8')
        if destination.exists() and destination.read_bytes() == content:
            return
        # Never rewrite a script being read by an existing poller. The original
        # shlock remains the authority for allowing a second process to start.
        if (pilot / '.audit-poller.lock').exists():
            raise RuntimeError('Stop the audit poller before changing its prepared source')
        destination.write_bytes(content)

    manifest = []
    for file in [*source.rglob('*'), source.parent / 'scripts' / 'task-activator.py']:
        if not file.is_file() or '__pycache__' in file.parts:
            continue
        relative = file.relative_to(source) if file.is_relative_to(source) else Path('../scripts/task-activator.py')
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix not in ('.py', '.sh', '.json'):
            write_bound(destination, file.read_bytes())
            manifest.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            continue
        text = file.read_text(encoding='utf-8')
        if file.suffix == '.py':
            # Parse constants rather than replacing raw code or inventing new
            # gates. Domain functions/control flow come from the upstream AST.
            text = ast.unparse(BindConstants().visit(ast.parse(text))) + '\n'
        elif file.suffix == '.json':
            def walk(value):
                if isinstance(value, str): return bind(value)
                if isinstance(value, list): return [walk(v) for v in value]
                if isinstance(value, dict): return {k: walk(v) for k, v in value.items()}
                return value
            text = json.dumps(walk(json.loads(text)), ensure_ascii=False, indent=2)
        else:
            text = bind(text).replace('/usr/bin/shlock', 'starfix_shlock')
        write_bound(destination, text)
        manifest.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    (home / 'audit' / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for name in ('判决指纹.jsonl', '.baseline_exempt'):
        with (pilot / name).open('a', encoding='utf-8'): pass
    cursor = pilot / '.audit-poller.cursor'
    if not cursor.exists():
        # A fresh installation scans from the minimum lexical cursor; this is
        # not a fake audited changeset or a waiver of historical registrations.
        cursor.write_text('CS-00000000-0000\n', encoding='utf-8')
    env = {
        'FLEET_HOME': home.as_posix(), 'FLEET_INTEGRATION_REPO': Path(request['directory']).as_posix(),
        'TRAJ_HOME': root.as_posix(), 'TRAJ_DATA_DIR': data.as_posix(),
        'TRJ_DB_PATH': (home / 'audit' / 'runtime.db').as_posix(),
        'TRJ_PILOT_DIR': pilot.as_posix(), 'AUDITPOLLER_PILOT': pilot.as_posix(),
        'AUDITPOLLER_ROOT': root.as_posix(), 'AUDITPOLLER_RUNTIME_DB': (home / 'audit' / 'runtime.db').as_posix(),
        'AUDITPOLLER_ALERT_DIR': alerts.as_posix(), 'ALERTDIR': alerts.as_posix(),
        'AUDITPOLLER_WORKDIR': (home / 'audit' / 'workdir').as_posix(),
        'AUDITPOLLER_DOCKER_BIN': 'docker',
        'TRJ_STATUS_JSON': (home / 'audit' / 'status.json').as_posix(),
        'STARFIX_AUDIT_REQUEST': str(Path(request['requestFile']).resolve()),
        'STARFIX_AUDIT_BRIDGE': Path(__file__).resolve().as_posix(),
        'STARFIX_PYTHON': sys.executable.replace('\\', '/'),
        'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'TMPDIR': (home / 'audit' / 'tmp').as_posix(), 'TEMP': str(home / 'audit' / 'tmp'), 'TMP': str(home / 'audit' / 'tmp'),
    }
    for key, variable in (('container', 'DB_CONTAINER'), ('database', 'DB_NAME'), ('baseRef', 'GATE_MERGE_REF'), ('baselineId', 'TRJ_BASELINE_ID')):
        if key in config:
            env[variable] = str(config[key])
    env.update(config.get('env', {}))
    shim = home / 'audit' / 'shims'
    shim.mkdir(exist_ok=True)
    (shim / 'sitecustomize.py').write_text('from audit_bridge import install_environment\ninstall_environment()\n', encoding='utf-8')
    env['PYTHONPATH'] = os.pathsep.join([str(shim), str(Path(__file__).parent), os.environ.get('PYTHONPATH', '')])
    docker = shim / 'docker'
    docker.write_text('#!/usr/bin/env bash\n"$STARFIX_PYTHON" -B "$STARFIX_AUDIT_BRIDGE" --docker "$@"\n', encoding='utf-8', newline='\n')
    prelude = shim / 'environment.sh'
    prelude.write_text('python3() { "$STARFIX_PYTHON" -B "$@"; }; export -f python3\n'
                       'sqlite3() { python3 "$STARFIX_AUDIT_BRIDGE" --sqlite "$@"; }; export -f sqlite3\n'
                       'starfix_shlock() { python3 "$STARFIX_AUDIT_BRIDGE" --shlock "$@"; }; export -f starfix_shlock\n', encoding='utf-8', newline='\n')
    env['BASH_ENV'] = prelude.as_posix()
    env['PATH'] = str(shim) + os.pathsep + os.environ.get('PATH', '')
    old = os.environ.copy()
    try:
        os.environ.update(env)
        module = runpy.run_path(str(root / 'runner' / 'run_graph.py'), run_name='upstream_audit_init')
        module['init_db']()
    finally:
        os.environ.clear(); os.environ.update(old)
    return {'root': str(root), 'env': env, 'sourceFiles': len(manifest)}


def install_environment():
    # Native Python's CRLF output otherwise changes Bash command substitutions
    # and turns a valid original verdict into a mismatching protocol token.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', newline='\n')
    request = json.loads(Path(os.environ['STARFIX_AUDIT_REQUEST']).read_text(encoding='utf-8'))
    from platform_bridge import install
    native = install(request['bash'], request.get('opencode'), request.get('model'))
    platform_run = subprocess.run

    def run(args, *extra, **kwargs):
        if os.name == 'nt' and isinstance(args, str) and args.startswith('/bin/ps -Ao pid,command'):
            import ctypes
            probe = native(['powershell.exe', '-NoProfile', '-Command',
                            '[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress'],
                           capture_output=True, text=True)
            rows = json.loads(probe.stdout) if probe.returncode == 0 else []
            lines = []
            shell32 = ctypes.WinDLL('shell32')
            kernel32 = ctypes.WinDLL('kernel32')
            shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            expected = Path(request['home']) / 'audit' / 'source' / 'runner' / 'audit-poller.sh'
            for row in rows:
                command = row.get('CommandLine') or ''
                if not command: continue
                count = ctypes.c_int()
                argv = shell32.CommandLineToArgvW(command, ctypes.byref(count))
                if not argv: continue
                try: values = [argv[i] for i in range(count.value)]
                finally: kernel32.LocalFree(argv)
                if len(values) < 2 or Path(values[0]).name.lower() not in ('bash.exe', 'sh.exe', 'zsh.exe'): continue
                if Path(values[1]).resolve() != expected.resolve(): continue
                # Normalize the proven executable/script pair for upstream ps
                # parsing. Another fleet or a script path with spaces must not
                # create a false positive/negative. Preserve --once exclusions.
                lines.append(str(row['ProcessId']) + ' bash audit-poller.sh ' + ' '.join(values[2:]))
            return subprocess.CompletedProcess(args, probe.returncode, '\n'.join(lines), probe.stderr)
        if isinstance(args, str) and kwargs.get('shell'):
            kwargs['shell'] = False
            args = [request['bash'], '-c', args]
        elif isinstance(args, (tuple, list)) and args and args[0] in ('docker', '/usr/local/bin/docker'):
            args = [*request['config']['dockerCommand'], *args[1:]]
        return platform_run(args, *extra, **kwargs)
    subprocess.run = run
    return native


def shlock(args, release=False):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', required=True)
    parser.add_argument('-f', required=True)
    options = parser.parse_args(args)
    target = Path(options.f)
    # Serialize stale-PID reclamation among local adapters before replacing the
    # original PID file. A live/reused PID remains locked, as in upstream shlock.
    with open(str(target) + '.guard', 'a+b') as guard:
        if os.name == 'nt':
            import msvcrt
            guard.write(b'0'); guard.flush(); guard.seek(0)
            try: msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: return 1
        if target.exists():
            pid = target.read_text().strip()
            if not pid.isdigit(): return 1
            request = json.loads(Path(os.environ['STARFIX_AUDIT_REQUEST']).read_text(encoding='utf-8'))
            check = subprocess.run([request['bash'], '-c', 'kill -0 "$1" 2>/dev/null', 'starfix-lock', pid])
            if check.returncode == 0: return 1
            target.unlink()
        if release: return 0
        try:
            with target.open('x') as stream: stream.write(options.p + '\n')
        except FileExistsError: return 1
    return 0


if __name__ == '__main__':
    if sys.argv[1] == '--release-lock':
        sys.exit(shlock(['-p', '0', '-f', sys.argv[2]], release=True))
    if sys.argv[1] == '--sqlite':
        import sqlite3
        # Upstream's poller uses sqlite3 <existing-db> <SELECT> for exact trace
        # confirmation. Python's installed SQLite engine replaces that binary.
        with sqlite3.connect(Path(sys.argv[2]).resolve().as_uri() + '?mode=rw', uri=True) as db:
            for row in db.execute(sys.argv[3]):
                print('|'.join('' if value is None else str(value) for value in row))
        sys.exit(0)
    if sys.argv[1] == '--shlock':
        sys.exit(shlock(sys.argv[2:]))
    if sys.argv[1] == '--docker':
        request = json.loads(Path(os.environ['STARFIX_AUDIT_REQUEST']).read_text(encoding='utf-8'))
        sys.exit(subprocess.run([*request['config']['dockerCommand'], *sys.argv[2:]]).returncode)
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps(prepare(request)))
