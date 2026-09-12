#!/usr/bin/env python3
"""Read-only run inventory. No semantic grading and no hidden reasoning output."""
import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def summarize(root):
    root=Path(root);root=root/'examiner' if (root/'examiner').exists() else root
    manifest=read(root/'launch.json') or {}
    phases=[];metas=[]
    paths=[root/'L1',*[root/('L2-S'+str(i).zfill(2)) for i in range(1,11)],root/'L3-replay',root/'L4-handoff']
    for p in paths:
        outcome=read(p/'outcome.json')
        if not outcome:continue
        rows=read(p/'rounds.json')
        if rows is None:
            reports=read(p/'phase-reports.json') or read(p/'event-reports.json')
            if reports is not None:rows=[m for report in reports for m in report.get('metas',[])]
            elif 'meta' in outcome:rows=[outcome['meta']]
            else:rows=outcome.get('metas',[])
        metas.extend(rows)
        state=read(p/'state.json') or {}
        trace=p/'trace.jsonl';actions=0;format_errors=0
        if trace.exists():
            for line in trace.read_text().splitlines():
                row=json.loads(line)
                actions+=isinstance(row.get('action'),dict)
                format_errors+=row.get('type')=='candidate_format_error'
        phases.append({'phase':p.name,'rounds':len(rows),'actions':actions,'format_errors':format_errors,
            'reported_call_seconds':round(sum(m.get('seconds',0) for m in rows),3),
            'simulated_dispatches':len(state.get('dispatches',[])),
            'violations':state.get('violations',outcome.get('violations',[]))})
    usage={k:sum((m.get('usage') or {}).get(k,0) or 0 for m in metas)
           for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')}
    depths=[m['reasoning_depth'] for m in metas if m.get('reasoning_depth',{}).get('self_report')]
    inventories=[m.get('tools') for m in metas]
    return {'model_config_id':manifest.get('model_config_id'),'public_model':manifest.get('public_model'),
            'question_source_commit':manifest.get('effective_question_source_commit',manifest.get('question_source_commit')),
            'R12_supplement_present':(root/'R12-supplement/outcome.json').exists(),
            'completed_phases':phases,'completed_phase_count':len(phases),
            'reported_model_calls':len(metas),'reported_call_seconds':round(sum(m.get('seconds',0) for m in metas),3),
            'usage_as_reported_separate_fields':usage,'usage_note':'Cache fields are separate; no cross-provider billing equivalence inferred.',
            'reasoning_self_reports':depths,'explicit_reasoning_effort':manifest.get('reasoning_effort_override'),
            'all_completed_calls_have_empty_native_tools':bool(inventories) and all(x==[] for x in inventories),
            'stage':'INVENTORY_NOT_A_GRADE'}


def main():
    p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--out');a=p.parse_args()
    text=json.dumps(summarize(a.run),ensure_ascii=False,indent=2)+'\n'
    if a.out:Path(a.out).write_text(text)
    else:print(text,end='')


if __name__=='__main__':main()
