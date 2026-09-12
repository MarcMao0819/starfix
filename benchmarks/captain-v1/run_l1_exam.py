#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless L1: same public questions, empty native tools, fresh candidate context."""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

import prepare_grok as entry
import packet
from run_role_exam import candidate_turn


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);a=ap.parse_args()
    root=Path(a.run).resolve();out=root/'examiner/L1'
    if out.exists():raise SystemExit('L1 exists; no silent reset')
    entry.preflight(root)
    m=json.loads((root/'examiner/launch.json').read_text())
    c=entry.read_model(m['credential_source'],m['model_config_id'])
    if {k:c[k] for k in entry.SAFE_MODEL_FIELDS if k in c}!=m['public_model']:raise SystemExit('model drift')
    sid=str(uuid.uuid4());out.mkdir()
    m.update(active_session=sid,phase='L1_RUNNING')
    (root/'examiner/launch.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
    rules=(root/'candidate'/packet.RULES).read_text()
    prompt='本轮先做L1。只根据给定题面输出答案；禁止调用任何宿主工具。后续模拟动作请输出JSON，由考官执行并返回结果。不要给自己评分。\n\n'
    prompt+=(root/'candidate/candidate/L1-题本.md').read_text()
    text,meta=candidate_turn(root,sid,prompt,out/'round-01',c['api_key'],m['model_config_id'],
                             fresh=True,system_prompt=rules,parse_json=False,timeout=600)
    (root/'examiner/L1-answer.md').write_text(text)
    (out/'outcome.json').write_text(json.dumps({'meta':meta,'judgment':'PENDING_REVIEW'},ensure_ascii=False,indent=2))
    (out/'source-hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__),Path(__file__).with_name('run_role_exam.py')]},indent=2))
    m.update(phase='L1_COMPLETED_PENDING_REVIEW')
    (root/'examiner/launch.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
    print(json.dumps({'phase':'L1_COMPLETE','session':sid,'seconds':meta['seconds'],'native_tools':meta['tools']}),flush=True)


if __name__=='__main__':main()
