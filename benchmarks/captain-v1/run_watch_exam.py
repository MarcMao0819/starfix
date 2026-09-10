#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 compressed replay with two real context resets, followed by L4 handoff."""
import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

import prepare_grok as entry
import suite_world as world
import watch_world as watch
import sandbox
from run_role_exam import candidate_turn


def react(root,sid,s,prompt,out,tag,key,model,fresh=False,rules=None,limit=6):
    wall=time.monotonic();base_virtual=s['virtual_seconds'];metas=[];summaries=[];done=False
    for n in range(1,limit+1):
        print(f'{tag} round {n}: thinking',flush=True)
        d,m=candidate_turn(root,sid,prompt,out/f'{tag}-r{n:02}',key,model,fresh=fresh and n==1,system_prompt=rules if fresh and n==1 else None)
        metas.append(m);results=[];s['current_wall_latency']=round(time.monotonic()-wall,3)
        world.advance(s,base_virtual+int(time.monotonic()-wall))
        for a in d['actions']:
            s,r=watch.step(s,a)
            rec={'event':tag,'round':n,'action':a,'response':r,'virtual_seconds':s['virtual_seconds'],
                 'wall_since_event':round(time.monotonic()-wall,3)}
            with (out/'trace.jsonl').open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
            results.append(sandbox.public_response(r));print(' ',a.get('op'),'OK' if r.get('ok') else 'REJECTED',flush=True)
        (out/'state.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
        summaries.append(d.get('summary',''))
        if d['done']:done=True;break
        prompt='当前工具结果如下；继续处理，若需等待新外部事件，用done=true交还事件循环。只输出动作JSON。\n'+json.dumps(results,ensure_ascii=False)
    return s,{'event':tag,'session':sid,'fresh_context':fresh,'rounds':len(metas),
              'wall_seconds':round(time.monotonic()-wall,3),'submitted':done,'summaries':summaries,'metas':metas}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--resume',required=True);a=p.parse_args()
    root=Path(a.run).resolve();out=root/'examiner/L3-replay'
    if out.exists():raise SystemExit('L3 run already exists; no silent reset')
    entry.write_outer_sandbox(root);entry.preflight(root)
    m=json.loads((root/'examiner/launch.json').read_text());cfg=entry.read_model(m['credential_source'],m['model_config_id'])
    if {k:cfg[k] for k in entry.SAFE_MODEL_FIELDS if k in cfg}!=m['public_model']:raise SystemExit('model drift')
    key=cfg['api_key'];sid=a.resume;rules=(root/'candidate/candidate/系统提示词.md').read_text()
    events=[json.loads(l) for l in Path(__file__).with_name('examiner').joinpath('events.jsonl').read_text().splitlines()]
    out.mkdir();s=watch.initial_state();reports=[];resets=0
    (out/'initial-state.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
    (out/'source-hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(world.__file__),Path(watch.__file__),Path(sandbox.__file__)]},indent=2))
    intro='现在开始L3独立值守场景。这里只运行八小时虚拟时间，所有工位/联系人都是模拟实体；旧场景工作状态不沿用。Owner起初离席，按规程值守、联系、派单、记忆与收尾。你可注册定时器；没有定时器的时间点不会自动叫醒。工作记录与画像必须通过工具保存，跨上下文只能读取持久化入口/bench/index.json。\n'+world.WIRE+'\n'+watch.API
    for original in events:
        before=len(s['events'])
        event=watch.apply_event(s,original)
        # Source tick intentionally absent; only a timer actually registered by the candidate can wake it.
        if original.get('deliver') is False:
            triggered=[x for x in s['events'][before:] if 'at_seconds' in x]
            with (out/'released-events.jsonl').open('a') as f:f.write(json.dumps({'id':event['id'],'silent':True,'registered_timer_events':triggered},ensure_ascii=False)+'\n')
            if not triggered:continue
            public={'id':event['id'],'virtual_seconds':s['virtual_seconds'],'timer_events':triggered}
        else:public={'id':event['id'],'virtual_seconds':s['virtual_seconds'],**event['public']}
        fresh=event['id']=='E20'
        if fresh:sid=str(uuid.uuid4());resets+=1
        prompt=(intro+'\n' if event['id']=='E01' or fresh else '')
        if fresh:prompt+='旧聊天没有传入。按已有持久化工件恢复；这不是让你假装失忆。\n'
        prompt+='当前事件：'+json.dumps(public,ensure_ascii=False)
        with (out/'released-events.jsonl').open('a') as f:f.write(json.dumps({'id':event['id'],'public':public,'fresh_context':fresh},ensure_ascii=False)+'\n')
        s,r=react(root,sid,s,prompt,out,event['id'],key,m['model_config_id'],fresh=fresh,rules=rules)
        reports.append(r);(out/'event-reports.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
        if event['id']=='H06':
            sid=str(uuid.uuid4());resets+=1
            if s['monitor']:s['monitor']['active']=False
            s['events'].append({'id':'during-second-reset','kind':'receipt','text':'进程重建间隔产生的事件'})
            prompt=intro+'\n候选上下文再次重建，旧聊天未传入。当前虚拟时间='+str(s['virtual_seconds'])+'。仅可读本轮持久化工件/bench/index.json，请恢复工作。'
            s,r=react(root,sid,s,prompt,out,'RESET2',key,m['model_config_id'],fresh=True,rules=rules)
            reports.append(r);(out/'event-reports.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
        print('EVENT COMPLETE',event['id'],'rounds',r['rounds'],flush=True)
    (out/'outcome.json').write_text(json.dumps({'phase':'L3','events_presented':len(reports),'source_events':len(events),
            'context_resets':resets,'virtual_seconds':s['virtual_seconds'],'active_session':sid,
            'wall_seconds':sum(x['wall_seconds'] for x in reports),'judgment':'PENDING_REVIEW'},ensure_ascii=False,indent=2))
    # L4: only candidate-authored handoff + public API, not the preceding conversation.
    h=root/'examiner/L4-handoff';h.mkdir();(h/'initial-state.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
    handoff=s['files'].get('/bench/handoff.md')
    if handoff is None:handoff='前任没有保存/bench/handoff.md。可查/bench/index.json及状态入口。'
    s['handoff_phase']=True;s['tasks']['T-HANDOFF']['blockers']=[];s['current_event']='L4-H01'
    sid=str(uuid.uuid4())
    prompt=world.WIRE+'\n'+watch.API+'\n你现在是同一候选模型的全新接任上下文，旧聊天完全不传入。只能靠下面的前任交接和至多5个持久化入口恢复工作，不问人。目标是在10分钟内完成第一次正确模拟派单。机器交接前置已满足。\n前任交接：'+json.dumps(handoff,ensure_ascii=False)+'\n入口：/bench/index.json（及其中的四个状态入口）。'
    s,report=react(root,sid,s,prompt,h,'H01',key,m['model_config_id'],fresh=True,rules=rules,limit=10)
    (h/'outcome.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(h/'state.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
    m.update(phase='ALL_PHASES_EXECUTED_PENDING_REVIEW',active_session=sid,context_resets=resets+1)
    (root/'examiner/launch.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
    print('L3 AND L4 COMPLETE',flush=True)


if __name__=='__main__':main()
