#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare a fresh, restricted Grok L1 entry. No inference during preparation."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import packet

ROOT = Path(__file__).resolve().parent
SAFE_MODEL_FIELDS = ('model', 'base_url', 'name', 'api_backend', 'context_window',
                     'max_completion_tokens', 'temperature', 'top_p', 'reasoning_effort')


def read_model(config, model_id):
    """Deliberately limited scalar parser; reject unfamiliar TOML rather than guess."""
    text = Path(config).read_text(encoding='utf-8')
    identifier='(?:'+re.escape(model_id)+'|'+re.escape(json.dumps(model_id))+')'
    match = re.search(r'^\[model\.' + identifier + r'\][ \t]*\n(.*?)(?=^\[|\Z)', text, re.M | re.S)
    if not match:
        # Built-in Grok models are registered in the local model catalogue, not custom TOML.
        home=Path(config).parent
        cache=json.loads((home/'models_cache.json').read_text()) if (home/'models_cache.json').exists() else {}
        info=cache.get('models',{}).get(model_id,{}).get('info',{})
        if info.get('id')!=model_id or info.get('model_family')!='xai':
            raise ValueError('requested model ID is not registered in the local Grok config/catalogue')
        credentials=json.loads((home/'auth.json').read_text())
        active=[v for v in credentials.values() if v.get('auth_mode')=='oidc' and isinstance(v.get('key'),str)]
        if len(active)!=1:raise ValueError('native Grok credential selection must be unambiguous')
        result={k:info[k] for k in SAFE_MODEL_FIELDS if info.get(k) is not None}
        u=urlsplit(result.get('base_url',''))
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('native model endpoint is not a safe explicit HTTPS URL')
        result['api_key']=active[0]['key']
        return result
    result = {}
    for line in match.group(1).splitlines():
        m = re.match(r'^\s*([A-Za-z_]+)\s*=\s*(.*)', line)
        if not m or m[1] not in (*SAFE_MODEL_FIELDS, 'api_key', 'env_key'):
            continue
        try:
            decoder = json.JSONDecoder()
            value, end = decoder.raw_decode(m[2])
            tail = m[2][end:].strip()
            if tail and not tail.startswith('#'):
                raise ValueError()
            result[m[1]] = value
        except (ValueError, TypeError):
            raise ValueError('model config contains an unsupported scalar; secret values are not printed') from None
    if not isinstance(result.get('model'), str) or not isinstance(result.get('base_url'), str):
        raise ValueError('model and base_url must be explicit strings')
    url = urlsplit(result['base_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('this launcher requires an explicit HTTPS provider endpoint without embedded credentials')
    return result


def clean_env(client_home, key=None):
    allowed = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TERM', 'TERM_PROGRAM',
               'COLORTERM', 'TMPDIR', 'SHELL', 'SSH_AUTH_SOCK')
    env = {k: v for k, v in os.environ.items() if k in allowed and k != 'SSH_AUTH_SOCK'}
    env.update(GROK_HOME=str(client_home), GROK_MEMORY='0', GROK_SUBAGENTS='0',
               GROK_MANAGED_MCPS_ENABLED='0', GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED='0',
               GROK_DEFAULT_SELECTED_PERMISSION='reject', GROK_WEB_FETCH='0')
    transport=Path(client_home)/'transport.json'
    if transport.exists():
        proxy=json.loads(transport.read_text())['proxy']
        u=urlsplit(proxy)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or not u.port or u.username or u.password or u.query or u.fragment:
            raise ValueError('exam transport must reference an explicit local HTTP proxy without credentials')
        env.update(HTTP_PROXY=proxy,HTTPS_PROXY=proxy,http_proxy=proxy,https_proxy=proxy,
                   NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    for vendor in ('CLAUDE', 'CURSOR', 'CODEX'):
        for field in ('SKILLS', 'RULES', 'AGENTS', 'MCPS', 'HOOKS', 'SESSIONS'):
            env[f'GROK_{vendor}_{field}_ENABLED'] = '0'
    if key:
        env['STARFIX_CANDIDATE_API_KEY'] = key
    return env


def prepare(out, model_id, config, binary):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', model_id):
        raise ValueError('invalid model ID')
    config = Path(config).resolve(); binary = Path(binary).resolve()
    selected = read_model(config, model_id)
    public_model = {k: selected[k] for k in SAFE_MODEL_FIELDS if k in selected}
    out = Path(out).resolve()
    if out.exists():
        raise ValueError('run directory already exists; use a fresh path')
    # ROOT is inside the question repository. Never export the runtime there.
    repo = next((p for p in (ROOT, *ROOT.parents) if (p / '.git').exists()), ROOT)
    if packet.inside(out, repo):
        raise ValueError('exam run must be outside the question repository')
    out.mkdir(parents=True)
    examiner = out / 'examiner'; examiner.mkdir(mode=0o700)
    candidate = out / 'candidate'
    seal = packet.export_packet(candidate, examiner / 'l1-seal.json', 'l1')
    packet.verify_packet(candidate, seal)
    client = out / 'client-state'; client.mkdir(mode=0o700)
    runtime = out / 'runtime'; runtime.mkdir()
    shutil.copyfile(binary, runtime / 'grok')
    (runtime / 'grok').chmod(0o700)
    cfg = '[model.' + json.dumps(model_id) + ']\n'
    for k, v in public_model.items():
        cfg += k + ' = ' + json.dumps(v, ensure_ascii=False) + '\n'
    cfg += 'env_key = "STARFIX_CANDIDATE_API_KEY"\n'
    cfg += '\n[models]\ndefault = ' + json.dumps(model_id) + '\n'
    cfg += '\n[cli]\nauto_update = false\nuse_leader = false\n'
    cfg += '\n[memory]\nenabled = false\n[subagents]\nenabled = false\n'
    cfg += '\n[features]\nremote_fetch = false\nmanaged_config = false\ntelemetry = false\ncodebase_indexing = false\nlsp_tools = false\nbackend_tools = false\n'
    cfg += '\n[managed_mcps]\nenabled = false\ngateway_tools_enabled = false\n'
    cfg += '\n[session]\nload_envrc = false\n'
    cfg += '\n[shell_environment_policy]\ninherit = "core"\nignore_default_excludes = false\n'
    cfg += '\n[permission]\nrules = [\n'
    for tool in ('bash', 'read', 'edit', 'grep', 'mcp', 'webfetch', 'websearch'):
        cfg += '{ action = "deny", tool = ' + json.dumps(tool) + ' },\n'
    cfg += ']\n'
    for vendor in ('claude', 'cursor', 'codex'):
        cfg += '\n[compat.' + vendor + ']\n'
        cfg += '\n'.join(k + ' = false' for k in ('skills','rules','agents','mcps','hooks','sessions')) + '\n'
    (client / 'config.toml').write_text(cfg, encoding='utf-8')
    profile = '[profiles.captain-exam]\nextends = "strict"\nrestrict_network = true\n'
    profile += 'read_only = ' + json.dumps([str(runtime)], ensure_ascii=False) + '\n'
    profile += 'deny = ' + json.dumps([str(Path.home()), str(repo), str(examiner)], ensure_ascii=False) + '\n'
    (client / 'sandbox.toml').write_text(profile, encoding='utf-8')
    manifest = {'model_config_id': model_id, 'public_model': public_model,
                'credential_source': str(config), 'runtime_isolation_verified': False,
                'phase': 'L1_PREFLIGHT_ONLY', 'candidate_context': 'fresh',
                'network_note': 'macOS sandbox child-network restriction is not sufficient; shell/web/MCP are separately disabled'}
    (examiner / 'launch.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    script = '#!/bin/sh\nset -eu\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'prepare_grok.py')) + ' launch --run ' + shlex.quote(str(out)) + '\n'
    (out / 'start-l1.sh').write_text(script, encoding='utf-8'); (out / 'start-l1.sh').chmod(0o700)
    write_outer_sandbox(out)
    preflight(out)
    return {'start_command': 'bash ' + shlex.quote(str(out / 'start-l1.sh')),
            'model': model_id, 'started': False, 'runtime_isolation_verified': False}


def discovery(out):
    out = Path(out).resolve(); client = out / 'client-state'
    p = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(out / 'examiner/outer.sb'),
                        str(out / 'runtime/grok'), '--sandbox', 'off', 'inspect', '--json'], cwd=out / 'candidate',
                       env=clean_env(client), capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise ValueError('Grok discovery preflight failed; no model was started')
    return json.loads(p.stdout)


def write_outer_sandbox(out):
    out = Path(out).resolve()
    def sub(p): return '(subpath ' + json.dumps(str(p)) + ')'
    candidate, client, runtime = (out / x for x in ('candidate', 'client-state', 'runtime'))
    exclusions = ' '.join('(require-not ' + sub(p) + ')' for p in (candidate, client, runtime))
    profile = '(version 1)\n(allow default)\n'
    profile += '(deny file-read* file-write* ' + sub(Path.home()) + ')\n'
    profile += '(deny file-read* file-write* (require-all ' + sub(Path('/private/tmp')) + ' ' + exclusions + '))\n'
    profile += '(deny file-read* file-write* ' + sub(out / 'examiner') + ')\n'
    # Headless cwd validation stats its parents. This grants metadata, never directory contents.
    profile += '(allow file-read-metadata (literal "/private/tmp") (literal ' + json.dumps(str(out)) + '))\n'
    profile += '(deny file-write* ' + sub(runtime) + ')\n'
    profile += '(deny process-exec)\n(allow process-exec (literal ' + json.dumps(str(runtime / 'grok')) + ') (literal "/usr/bin/head"))\n'
    profile += '(deny network-outbound (remote unix-socket))\n'
    # DNS uses this local socket on macOS; keep all other Unix sockets denied.
    profile += '(allow network-outbound (literal "/private/var/run/mDNSResponder"))\n'
    (out / 'examiner/outer.sb').write_text(profile, encoding='utf-8')


def network_preflight(out):
    out = Path(out).resolve()
    manifest = json.loads((out / 'examiner/launch.json').read_text(encoding='utf-8'))
    url = manifest['public_model']['base_url'].rstrip('/') + '/models'
    # Only the examiner's probe may execute curl. Candidate executable grants do not change.
    policy = (out / 'examiner/outer.sb').read_text(encoding='utf-8')
    probe = out / 'examiner/network-probe.sb'
    probe.write_text(policy + '\n(allow process-exec (literal "/usr/bin/curl"))\n', encoding='utf-8')
    p = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(probe), '/usr/bin/curl', '-q', '-sS',
                        '--connect-timeout', '25', '--max-time', '40', '-o', '/dev/null',
                        '-w', '%{http_code}', url], capture_output=True, text=True, timeout=45,
                       env=clean_env(out / 'client-state'))
    code = int(p.stdout.strip()) if p.stdout.strip().isdigit() else 0
    result = {'transport': 'PASS' if p.returncode == 0 and 200 <= code < 500 else 'FAIL',
              'http_status': code, 'curl_exit': p.returncode,
              'authentication_tested': False, 'inference_started': False}
    (out / 'examiner/network-preflight.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['transport'] != 'PASS':
        raise ValueError('provider DNS/TLS preflight failed before candidate inference')
    return result


def preflight(out):
    out = Path(out).resolve()
    seal = json.loads((out / 'examiner/l1-seal.json').read_text(encoding='utf-8'))
    packet.verify_packet(out / 'candidate', seal)
    d = discovery(out)
    def active(kind):
        return [x for x in (d.get(kind) or []) if x.get('disabled') is not True
                and x.get('enabled') is not False and x.get('compatibilityStatus') != 'disabled']
    if active('mcpServers') or active('projectInstructions') or active('hooks') or active('plugins') or d.get('configWarnings'):
        raise ValueError('unexpected active MCP/instructions/hooks/plugins/config warnings; no candidate launched')
    # Metadata-only report: never persist discovered secrets or full config.
    prefix = ['/usr/bin/sandbox-exec', '-f', str(out / 'examiner/outer.sb'), '/usr/bin/head', '-c', '32']
    probe_public = subprocess.run([*prefix, str(out / 'candidate' / packet.RULES)], capture_output=True, timeout=10)
    probe_private = subprocess.run([*prefix, str(out / 'examiner/l1-seal.json')], capture_output=True, timeout=10)
    probe_answer = subprocess.run([*prefix, str(ROOT / 'examiner/cases.json')], capture_output=True, timeout=10)
    if probe_public.returncode or not probe_public.stdout or probe_private.returncode == 0 or probe_answer.returncode == 0:
        raise ValueError('kernel file isolation positive/negative control failed')
    network = network_preflight(out)
    report = {'grok_version': d.get('grokVersion'), 'active_mcp_servers': len(active('mcpServers')),
              'active_project_instructions': len(active('projectInstructions')),
              'active_hooks': len(active('hooks')), 'active_plugins': len(active('plugins')),
              'config_warnings': len(d.get('configWarnings') or []),
              'discovery_preflight': 'PASS', 'kernel_file_read_controls': 'PASS',
              'provider_transport': network,
              'candidate_public_read': True, 'examiner_and_source_answer_read': False,
              'runtime_isolation_verified': False,
              'remaining': 'verify candidate session tool inventory and model identity before treating results as a valid blind run'}
    (out / 'examiner/preflight.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def launch(out):
    out = Path(out).resolve(); preflight(out)
    manifest = json.loads((out / 'examiner/launch.json').read_text(encoding='utf-8'))
    current = read_model(manifest['credential_source'], manifest['model_config_id'])
    if {k: current[k] for k in SAFE_MODEL_FIELDS if k in current} != manifest['public_model']:
        raise ValueError('model configuration changed; prepare a new run')
    key = current.get('api_key')
    if not key:
        names = current.get('env_key', [])
        if isinstance(names, str): names = [names]
        key = next((os.environ[n] for n in names if os.environ.get(n)), None)
    if not isinstance(key, str) or not key:
        raise ValueError('configured provider credential unavailable; no credential printed')
    candidate = out / 'candidate'
    rules = (candidate / packet.RULES).read_text(encoding='utf-8')
    question = '本轮先做L1。只根据给定题面输出答案；禁止调用任何宿主工具。后续模拟动作请输出JSON，由考官执行并返回结果。不要给自己评分。\n\n'
    question += (candidate / 'candidate/L1-题本.md').read_text(encoding='utf-8')
    argv = ['/usr/bin/sandbox-exec', '-f', str(out / 'examiner/outer.sb'),
            str(out / 'runtime/grok'), '--cwd', str(candidate), '--model', manifest['model_config_id'],
            '--sandbox', 'off', '--permission-mode', 'dontAsk', '--no-subagents',
            '--disable-web-search', '--system-prompt-override', rules, question]
    os.chdir(candidate)
    os.execve(argv[0], argv, clean_env(out / 'client-state', key))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--model', required=True); p.add_argument('--out', required=True)
    p.add_argument('--source-config', default=str(Path.home() / '.grok/config.toml'))
    p.add_argument('--binary', default=shutil.which('grok'))
    p = sub.add_parser('preflight'); p.add_argument('--run', required=True)
    p = sub.add_parser('launch'); p.add_argument('--run', required=True)
    a = parser.parse_args()
    try:
        if a.cmd == 'prepare': result = prepare(a.out, a.model, a.source_config, a.binary)
        elif a.cmd == 'preflight': result = preflight(a.run)
        else: launch(a.run); return
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        parser.exit(2, 'Preparation/launch rejected. No secrets printed; verify model ID, paths and isolated configuration privately.\n')


if __name__ == '__main__': main()
