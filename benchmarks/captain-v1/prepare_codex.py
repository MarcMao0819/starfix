#!/usr/bin/env python3
"""Prepare an isolated Astra/high native Codex exam; never copies user settings/history."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import packet
import prepare_grok

DISABLED_FEATURES=('apps','browser_use','browser_use_external','computer_use','code_mode','code_mode_host',
    'hooks','plugins','remote_plugin','memories','multi_agent','multi_agent_v2','shell_tool','unified_exec',
    'shell_snapshot','skill_search','sleep_tool','goals','view_image','image_generation',
    'workspace_dependencies','unbounded_connection_retries')


def prepare(out,binary,auth_home,model,proxy=None):
    if not model or not isinstance(model,str):raise ValueError("explicit model ID required")
    root=Path(out).resolve();binary=Path(binary).resolve();auth_home=Path(auth_home).resolve()
    if root.exists():raise ValueError('exam directory must be new')
    repo=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
    if packet.inside(root,repo):raise ValueError('exam must be outside the source repository')
    root.mkdir(mode=0o700)
    for n in ('examiner','runtime','client-state'):(root/n).mkdir(mode=0o700)
    packet.export_packet(root/'candidate',root/'examiner/l1-seal.json','l1')
    shutil.copy2(binary,root/'runtime/codex')
    # Account login and public model catalogue only; no global config, memories or sessions.
    for n in ('auth.json','models_cache.json'):
        shutil.copy2(auth_home/n,root/'client-state'/n);(root/'client-state'/n).chmod(0o600)
    cfg='model = '+json.dumps(model)+'\n'+'''model_reasoning_effort = "high"
service_tier = "default"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
project_doc_max_bytes = 0
personality = "none"
model_reasoning_summary = "none"
'''
    cfg+='model_instructions_file = '+json.dumps(str(root/'candidate'/packet.RULES))+'\n[features]\n'
    cfg+=''.join(f+' = false\n' for f in DISABLED_FEATURES)+'skip_host_skill_discovery = true\n'
    # Native CLI requires SKILL.md file paths for disabling its bundled skills.
    for name in ('imagegen','openai-docs','plugin-creator','skill-creator','skill-installer'):
        cfg+='\n[[skills.config]]\npath = '+json.dumps(str(root/'client-state/skills/.system'/name/'SKILL.md'))+'\nenabled = false\n'
    (root/'client-state/config.toml').write_text(cfg)
    if proxy:(root/'client-state/transport.json').write_text(json.dumps({'proxy':proxy}))
    prepare_grok.write_outer_sandbox(root)
    policy=root/'examiner/outer.sb';policy.write_text(policy.read_text().replace(str(root/'runtime/grok'),str(root/'runtime/codex')))
    version=subprocess.check_output([str(binary),'--version'],text=True).strip()
    manifest={'model_config_id':model,'public_model':{'model':model,'reasoning_effort':'high'},
        'harness':version,'reasoning_effort_override':'high','phase':'PREPARED',
        'credential_source':'isolated ChatGPT login','per_turn_timeout_seconds':600,
        'native_tool_policy':'no host tools; abort on native tool execution',
        'question_bank_sha256':hashlib.sha256((Path(__file__).parent/'examiner/cases.json').read_bytes()).hexdigest(),
        'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
        'comparability_notes':['native Codex differs from Grok harness','600-second cap on every turn; Grok used 150 with one documented recovery',
                               'no hard cumulative output token cap; single-reviewer calibration']}
    (root/'examiner/launch.json').write_text(json.dumps(manifest,indent=2))
    from run_codex_exam import preflight
    return preflight(root)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--binary',required=True)
    ap.add_argument('--auth-home',required=True);ap.add_argument('--model',required=True);ap.add_argument('--proxy');a=ap.parse_args()
    print(json.dumps(prepare(a.out,a.binary,a.auth_home,a.model,a.proxy),indent=2))


if __name__=='__main__':main()
