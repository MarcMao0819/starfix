#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the eight not-yet-tested L2 cases, with no grader feedback to the candidate."""
import argparse
import json
import hashlib
import time
import uuid
from pathlib import Path

import prepare_grok as entry
import suite_world as world
import sandbox
from run_role_exam import candidate_turn, CandidateFormatError


CASES=['S01','S02','S04','S05','S06','S07','S09','S10']


def react(root,sid,state,prompt,out,tag,key,model,round_limit=16,fresh=False,rules=None):
    pending=state.pop('_pending_tool_results',[])
    if pending and not fresh:prompt+='\n上一阶段最后一批工具的实际结果：'+json.dumps(pending,ensure_ascii=False)
    records=[];metas=[];wall_begin=time.monotonic();submitted=False;summary=''
    responses=[]
    for n in range(1,round_limit+1):
        print(f'{tag} round {n}: thinking',flush=True)
        try:
            d,meta=candidate_turn(root,sid,prompt,out/f'{tag}-round-{n:02}',key,model,
                                  fresh=fresh and n==1,system_prompt=rules if fresh and n==1 else None)
        except CandidateFormatError as exc:
            metas.append(exc.meta)
            with (out/'trace.jsonl').open('a') as f:f.write(json.dumps({'phase':tag,'round':n,'type':'candidate_format_error','actions_executed':0})+'\n')
            prompt='FORMAT_ERROR：上轮没有执行任何动作。仅接受一个actions/done/summary JSON对象，不接受附加正文、多个JSON、你生成的system_reminder/user_query或工具结果。请重发本轮动作。'
            continue
        metas.append(meta);responses=[]
        if state['scenario']=='fleet':state['current_wall_latency']=round(time.monotonic()-wall_begin,3)
        for action in d['actions']:
            state,result=world.step(state,action)
            rec={'phase':tag,'round':n,'action':action,'response':result,
                 'virtual_seconds':state.get('virtual_seconds',0),'wall_since_phase':round(time.monotonic()-wall_begin,3)}
            records.append(rec);responses.append(sandbox.public_response(result))
            with (out/'trace.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
            print(' ',action.get('op'),'OK' if result.get('ok') else 'REJECTED',flush=True)
        (out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        summary=d.get('summary','')
        if d['done']:submitted=True;break
        prompt='当前工具结果如下；继续本阶段，仍只输出动作JSON。当前无法推进且需要新外部事件时done=true。\n'+json.dumps(responses,ensure_ascii=False)
    state['_pending_tool_results']=responses
    return state,{'phase':tag,'rounds':len(metas),'wall_seconds':round(time.monotonic()-wall_begin,3),
                  'submitted':submitted,'summary':summary,'native_tool_counts':[len(m['tools']) for m in metas],
                  'metas':metas,'actions':len(records)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--resume',required=True);p.add_argument('--from-case',choices=CASES)
    a=p.parse_args();root=Path(a.run).resolve();entry.write_outer_sandbox(root);entry.preflight(root)
    m=json.loads((root/'examiner/launch.json').read_text());cfg=entry.read_model(m['credential_source'],m['model_config_id'])
    if {k:cfg[k] for k in entry.SAFE_MODEL_FIELDS if k in cfg}!=m['public_model']:raise SystemExit('model drift')
    key=cfg.get('api_key');assert isinstance(key,str) and key
    sid=a.resume;cases=CASES[CASES.index(a.from_case):] if a.from_case else CASES
    rules=(root/'candidate/candidate/系统提示词.md').read_text(encoding='utf-8')
    for case in cases:
        out=root/'examiner'/('L2-'+case)
        if out.exists():raise SystemExit(f'{case} already exists; no silent reset')
        out.mkdir();s,frames=world.l2_setup(case);phase_reports=[]
        (out/'initial-state.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
        (out/'source-hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(world.__file__),Path(sandbox.__file__)]},indent=2))
        for i,frame in enumerate(frames):
            if case=='S06' and i==4:
                checkpoint=out/'before-host-restart.json'
                checkpoint.write_text(json.dumps(s,ensure_ascii=False,indent=2))
                s=json.loads(checkpoint.read_text())
            world.l2_transition(case,i,s);fresh=case=='S10' and i==3
            if fresh:sid=str(uuid.uuid4())
            prefix=f'独立场景{case}，阶段{i+1}。本场状态以工具读取为准，不沿用其他场景的工位状态。\n' if i==0 else f'{case}新的事件：\n'
            if fresh:prefix+='候选上下文已真正重建。你只有本轮规则和持久化入口/bench/index.json，不再获得旧聊天；可检索本轮自己保存的人员/舰员记录。\n'
            s,report=react(root,sid,s,prefix+frame,out,f'phase-{i:02}',key,m['model_config_id'],fresh=fresh,rules=rules)
            phase_reports.append(report)
            (out/'phase-reports.json').write_text(json.dumps(phase_reports,ensure_ascii=False,indent=2))
        result={'case':case,'phases':len(frames),'all_phases_submitted':all(x['submitted'] for x in phase_reports),
                'actions':sum(x['actions'] for x in phase_reports),'rounds':sum(x['rounds'] for x in phase_reports),
                'wall_seconds':round(sum(x['wall_seconds'] for x in phase_reports),3),'active_session':sid,
                'violations':s.get('violations',[]),'context_resets':1 if case=='S10' else 0,'judgment':'PENDING_EXAMINER_REVIEW'}
        (out/'outcome.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        m.update(phase=case+'_COMPLETED_PENDING_REVIEW',active_session=sid);(root/'examiner/launch.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
        print('CASE COMPLETE',json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
