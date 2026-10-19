"""Run original StarFix graph logic with Windows dependencies and host adjudication."""
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
from platform_bridge import install


def sha(value):
    return hashlib.sha256(value).hexdigest()


def input_fingerprint(inputs):
    parts = [json.dumps(inputs, sort_keys=True).encode()]
    if inputs.get('receipt'):
        parts.append(Path(inputs['receipt']).read_bytes())
    if inputs.get('worktree'):
        for args in (['rev-parse', 'HEAD'], ['status', '--porcelain'], ['diff', '--binary'], ['diff', '--cached', '--binary']):
            p = subprocess.run(['git', '--no-optional-locks', '-C', inputs['worktree'], *args], capture_output=True, timeout=30)
            if p.returncode:
                raise ValueError('Cannot fingerprint target repository')
            parts.append(p.stdout)
    return sha(b'\0'.join(parts))


def execute(request):
    source = Path(request['sourceRoot']).resolve()
    trajectory = Path(request.get('trajectoryRoot') or source / 'trajectory').resolve()
    workdir = Path(request['workdir']).resolve()
    graph_path = Path(request['graph']).resolve()
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    inputs = dict(request['inputs'])
    for key in ('worktree', 'receipt'):
        if key in inputs:
            inputs[key] = Path(inputs[key]).resolve().as_posix()
    if graph['flow'] == 'premerge-gate' and inputs.get('base'):
        resolved = subprocess.run(['git', '-C', inputs['worktree'], 'rev-parse', '--verify', '--end-of-options', inputs['base'] + '^{commit}'], capture_output=True, text=True, timeout=15)
        if resolved.returncode:
            raise ValueError('Base commit cannot be resolved')
        inputs['base'] = resolved.stdout.strip()
        # Replace the author's project-specific branch, not the check itself.
        for node in graph['nodes']:
            template = node.get('cmd_template', '')
            if 'merge-base' in template and 'feat/<项目>-integration' in template:
                node['cmd_template'] = template.replace('feat/<项目>-integration', inputs['base'])

    bash = request.get('bash')
    if not bash or not Path(bash).is_file():
        raise ValueError('Configured Bash executable is unavailable')
    install(bash, request.get('opencode'), request.get('model'))
    before = input_fingerprint(inputs)
    implementation = [Path(__file__), trajectory / 'runner/run_graph.py', *sorted((trajectory / 'runner/checks').glob('*.py'))]
    code_hash = sha(b'\0'.join(p.read_bytes() for p in implementation))
    graph_hash = sha(json.dumps(graph, sort_keys=True).encode())
    database = Path(request.get('database') or workdir / 'runtime.db').resolve()
    os.environ['TRJ_DB_PATH'] = str(database)
    sys.path.insert(0, str(trajectory / 'runner'))
    spec = importlib.util.spec_from_file_location('starfix_original_runner', trajectory / 'runner/run_graph.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    # Keep the original read-only command allowlist, but parse quoted path/config
    # tokens correctly. The upstream \S+ matcher rejects normal Windows paths.
    token = r'''(?:'[^']*'|"[^"]*"|[^\s'"]+)+'''
    original.GIT_SUBCMD_RE = re.compile(
        r'^git(\s+--no-optional-locks)?(\s+-C\s+' + token + r')?(\s+-c\s+' + token + r')?\s+'
        r'(cat-file|diff|show|rev-parse|status|grep|merge-base|log|worktree)\b')

    def render(template, mapping):
        for key, value in mapping.items():
            marker = '{' + key + '}'
            quoted = shlex.quote(str(value))
            # Templates mix quoted and unquoted placeholders. Quote each value
            # once so paths with spaces/metacharacters never become shell syntax.
            for form in ('"' + marker + '"', "'" + marker + "'", marker):
                template = template.replace(form, quoted)
        return template.replace('{{', '{').replace('}}', '}')

    def run_shell(self, command, timeout=30):
        tokens = shlex.split(command)
        if len(tokens) == 3 and tokens[:2] == ['test', '-e']:
            # Original runner constructs native Windows marker paths outside render().
            return (0 if Path(tokens[2]).exists() else 1), '', ''
        env = dict(os.environ, TRAJ_HOME=trajectory.as_posix(), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0')
        prelude = 'python3() { ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"; };\n'
        try:
            p = subprocess.run([bash, '--noprofile', '--norc', '-c', prelude + command], cwd=workdir,
                               env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return -1, '', 'TIMEOUT'

    original.render = render
    original.BaseRunner.run_shell = run_shell
    original.init_db()
    runner = original.FLOW_RUNNERS[graph['flow']](graph, inputs, str(workdir), request.get('runTag', 'opencode'), original.parse_injections(request.get('inject', [])))
    try:
        runner.run()
        after = input_fingerprint(inputs)
        result = {'flow': graph['flow'], 'overall': runner.overall(),
                  'verdicts': runner.verdicts, 'modelCalls': runner.model_calls, 'inputsChanged': before != after,
                  'inputFingerprint': before, 'graphFingerprint': graph_hash, 'implementationFingerprint': code_hash,
                  'evidenceRows': runner.db_rows, 'aborted': runner.aborted,
                  'steps': runner.results, 'database': str(database)}
        (workdir / 'effective-graph.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    finally:
        runner.conn.close()


if __name__ == '__main__':
    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        print(json.dumps(execute(request), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'overall': 'ERROR', 'error': str(error), 'modelCalls': 0}, ensure_ascii=False))
        sys.exit(1)
