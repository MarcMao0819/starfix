#!/usr/bin/env python3
"""Isolated native Codex transport for the existing, unchanged captain simulators.

Candidate sessions have their own CODEX_HOME and OS boundary. No examiner rubric,
other candidate answer, or host tool result is injected into the model context.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import packet
import run_role_exam as protocol


def environment(root):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'USER', 'LOGNAME', 'TMPDIR')}
    env.update(HOME=str(root/'client-state'), CODEX_HOME=str(root/'client-state'))
    transport=root/'client-state/transport.json'
    if transport.exists():
        from urllib.parse import urlsplit
        proxy=json.loads(transport.read_text())['proxy']; u=urlsplit(proxy)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or not u.port or u.username or u.password:
            raise ValueError('only an explicit credential-free loopback proxy is permitted')
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY='127.0.0.1,localhost')
    return env


def command(root):
    return ['/usr/bin/sandbox-exec','-f',str(root/'examiner/outer.sb'),str(root/'runtime/codex')]


def preflight(root):
    root=Path(root).resolve()
    packet.verify_packet(root/'candidate',json.loads((root/'examiner/l1-seal.json').read_text()))
    probe=command(root)[:3]+['/usr/bin/head','-c','32']
    targets=[root/'candidate'/packet.RULES,root/'examiner/launch.json',Path(__file__).with_name('examiner')/'cases.json']
    results=[subprocess.run(probe+[str(p)],capture_output=True,timeout=10).returncode for p in targets]
    if results[0] or not all(results[1:]):raise ValueError('OS file isolation control failed')
    x=subprocess.run(command(root)+['debug','prompt-input','Boundary preflight only.'],cwd=root/'candidate',env=environment(root),capture_output=True,text=True,timeout=60)
    if x.returncode:raise ValueError('Codex prompt preflight failed')
    items=json.loads(x.stdout)
    if any('<skills_instructions>' in str(e) or '<INSTRUCTIONS>' in str(e) for e in items):
        raise ValueError('unexpected inherited skills/project instructions')
    (root/'examiner/prompt-preflight.json').write_text(x.stdout)
    report={'harness':'Codex CLI','kernel_file_read_controls':'PASS','candidate_public_read':True,
        'examiner_and_source_answer_read':False,'inherited_skills_or_project_instructions':False,
        'native_tool_boundary':'host execution disabled; every emitted native tool action rejects the turn',
        'network_allowlist_verified':False,'runtime_isolation_verified':False}
    (root/'examiner/preflight.json').write_text(json.dumps(report,indent=2))
    return report


def runtime_metadata(root, sid):
    """Select configuration metadata only; never expose reasoning/session contents."""
    records=[]
    for p in (root/'client-state/sessions').rglob('*'+sid+'*.jsonl'):
        for line in p.open():
            e=json.loads(line)
            if e.get('type')=='turn_context':
                d=e.get('payload',{})
                records.append({k:d.get(k) for k in ('model','effort','approval_policy','sandbox_policy')})
    return records[-1] if records else {}


def candidate_turn(root, sid, prompt, round_dir, key, model, fresh=False, system_prompt=None,
                   parse_json=True, timeout=600):
    root=Path(root).resolve(); manifest=json.loads((root/'examiner/launch.json').read_text())
    if model!=manifest.get('model_config_id') or manifest.get('reasoning_effort_override')!='high':
        raise ValueError('candidate model or frozen high effort mismatch')
    # Same generous cap on every Astra turn, declared before the first question.
    timeout=manifest['per_turn_timeout_seconds']
    if fresh:
        protocol_text=('在动作JSON顶层附加reasoning_depth对象，字段level和basis。' if parse_json else
                       '请在答卷首行输出[REASONING_DEPTH]，紧跟一个仅含level、basis的JSON对象，再正常答题。')
        prompt=protocol.DEPTH_REQUEST+protocol_text+'\n\n'+prompt
    mapping_path=root/'examiner/session-map.json'
    mapping=json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    if not fresh and sid not in mapping:raise ValueError('unknown candidate session; no silent reset')
    args=command(root)+['exec']
    if not fresh:args+=['resume',mapping[sid]]
    args+=['--skip-git-repo-check','--ignore-rules','--json','--model',model,
           '-c','model_reasoning_effort="high"','-']
    round_dir.mkdir()
    (round_dir/'prompt.txt').write_text(prompt)
    began=time.monotonic()
    try:
        x=subprocess.run(args,input=prompt,cwd=root/'candidate',env=environment(root),capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        (round_dir/'transport-error.json').write_text(json.dumps({'type':'TIMEOUT','seconds':timeout,'actions_executed':0}))
        raise
    elapsed=round(time.monotonic()-began,3)
    # Do not persist raw stderr: some providers log response bodies in diagnostics.
    (round_dir/'transport-status.json').write_text(json.dumps({'exit_code':x.returncode,'stderr_bytes':len(x.stderr),'seconds':elapsed}))
    events=[]; messages=[]; native=[]; usage=None; actual_sid=None; failed=False; completed=False
    for line in x.stdout.splitlines():
        if not line.strip():continue
        e=json.loads(line); kind=e.get('type'); item=e.get('item',{})
        if kind=='thread.started':actual_sid=e.get('thread_id');events.append(e)
        elif kind=='turn.completed':usage=e.get('usage');completed=True;events.append(e)
        elif kind in ('error','turn.failed'):failed=True;events.append({'type':kind,'message':'transport failed; no action accepted'})
        elif kind=='item.completed' and item.get('type')=='agent_message':
            value=protocol.public_text(item.get('text',''));messages.append(value)
            events.append({'type':'assistant','message':{'content':[{'type':'text','text':value}]}})
        elif kind in ('item.started','item.completed') and item.get('type')=='error':
            events.append({'type':'cli_error_notice','message':item.get('message','unspecified CLI notice')})
        elif kind in ('item.started','item.completed') and item.get('type') not in ('reasoning','agent_message'):
            native.append(item.get('type'));events.append({'type':'native_tool_attempt','item_type':item.get('type')})
    (round_dir/'response.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    if actual_sid:
        if not fresh and mapping[sid]!=actual_sid:raise ValueError('candidate session identity changed')
        mapping[sid]=actual_sid;mapping_path.write_text(json.dumps(mapping,indent=2))
    if x.returncode or failed or not completed or not actual_sid or not messages or native:
        raise ValueError('candidate transport or native tool boundary failed; no simulated action accepted')
    runtime=runtime_metadata(root,actual_sid)
    if runtime.get('model')!=model or runtime.get('effort')!='high':
        raise ValueError('runtime model/effort verification failed')
    answer=messages[-1]
    (round_dir/'answer.txt').write_text(answer)
    meta={'seconds':elapsed,'tools':[],'native_tool_inventory_verified':False,'session':actual_sid,
          'usage':usage,'modelUsage':None,'stop_reason':'turn.completed','runtime_configuration':runtime,
          'reasoning_depth':{'self_report':None,'harness_configured':'high','configuration_source':'isolated config and explicit CLI flag',
                             'runtime_reported':runtime.get('effort'),'effective_depth_verified':False}}
    (round_dir/'meta.json').write_text(json.dumps(meta,indent=2))
    if not parse_json:
        match=re.search(r'^\[REASONING_DEPTH\]\s*(\{[^\n]*\})',answer,re.M)
        if match:
            try:meta['reasoning_depth']['self_report']=protocol.depth_report(json.loads(match[1]))
            except ValueError:pass
        (round_dir/'meta.json').write_text(json.dumps(meta,indent=2))
        return answer,meta
    try:
        parsed=protocol.parse_actions(answer)
        meta['reasoning_depth']['self_report']=protocol.depth_report(parsed.get('reasoning_depth'))
        (round_dir/'meta.json').write_text(json.dumps(meta,indent=2))
        return parsed,meta
    except (ValueError,TypeError):
        raise protocol.CandidateFormatError('response must be one actions/done/summary JSON object',meta) from None


class Entry:
    SAFE_MODEL_FIELDS=('model','reasoning_effort')
    @staticmethod
    def write_outer_sandbox(root):
        if not (root/'examiner/outer.sb').is_file():raise ValueError('isolation policy missing')
    preflight=staticmethod(preflight)
    @staticmethod
    def read_model(source,model):
        return {'model':model,'reasoning_effort':'high','api_key':'AUTHENTICATED_BY_ISOLATED_CODEX_HOME'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True)
    ap.add_argument('--phase',choices=['l1','s08','s03','l2','watch','all'],default='all')
    a=ap.parse_args();root=Path(a.run).resolve()
    phases={'l1':'run_l1_exam','s08':'run_role_exam','s03':'run_terminal_exam','l2':'run_remaining_l2','watch':'run_watch_exam'}
    preflight(root)
    for name,module in phases.items():
        if a.phase not in ('all',name):continue
        driver=importlib.import_module(module);driver.entry=Entry;driver.candidate_turn=candidate_turn
        sys.argv=[module,'--run',str(root)]
        if name!='l1':sys.argv+=['--resume',json.loads((root/'examiner/launch.json').read_text())['active_session']]
        driver.main()
        print('CODEX PHASE COMPLETE',name,flush=True)


if __name__=='__main__':main()
