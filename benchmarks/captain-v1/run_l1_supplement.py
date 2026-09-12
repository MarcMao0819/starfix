#!/usr/bin/env python3
"""Fresh candidate supplement containing only L1 cases absent from a frozen bank."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import packet
import prepare_grok as entry
from run_role_exam import candidate_turn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--previous-bank', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--source-config', required=True)
    ap.add_argument('--binary', required=True)
    ap.add_argument('--prepared', action='store_true')
    args = ap.parse_args()
    root = Path(args.run).resolve()
    current = Path(__file__).with_name('examiner') / 'cases.json'
    previous = json.loads(Path(args.previous_bank).read_text())
    bank = json.loads(current.read_text())
    old = {c['id']: c for c in previous['cases']}
    if set(old) - {c['id'] for c in bank['cases']}:
        raise SystemExit('Existing cases removed; cumulative scoring requires separate review')
    changed = [c['id'] for c in bank['cases'] if c['id'] in old and c != old[c['id']]]
    if changed:
        raise SystemExit('Existing cases changed; a missing-case supplement cannot silently replace them')
    added = [c for c in bank['cases'] if c['id'] not in old]
    if not added or any(c['layer'] != 'L1' for c in added):
        raise SystemExit('This controller requires new L1 cases only')
    if not args.prepared:
        entry.prepare(root, args.model, args.source_config, args.binary)
    entry.preflight(root)
    manifest_path = root / 'examiner/launch.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['model_config_id'] != args.model:
        raise SystemExit('Prepared model differs from requested candidate')
    selected = entry.read_model(manifest['credential_source'], args.model)
    if {k: selected[k] for k in entry.SAFE_MODEL_FIELDS if k in selected} != manifest['public_model']:
        raise SystemExit('Model configuration drift')
    out = root / 'examiner/L1-supplement'
    out.mkdir()
    shutil.copyfile(current, out / 'cases-current.json')
    shutil.copyfile(args.previous_bank, out / 'cases-previous.json')
    # Deliberate allowlist: no checks, expected decisions, mappings or source commentary.
    public = '\n\n'.join(f"## {c['id']} · {c['title']}\n\n{c['prompt']}" for c in added)
    prompt = ('本轮是新增题目的独立补测。只根据给定题面及统一规则，逐题按统一答案格式作答。'
              '禁止调用宿主工具。不要给自己评分，不引用任何旧会话或其他模型答案。\n\n' + public)
    sid = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    scope = {'case_ids': [c['id'] for c in added],
             'check_ids': [x['id'] for c in added for x in c['checks']],
             'previous_checks': sum(len(c['checks']) for c in previous['cases']),
             'current_checks': sum(len(c['checks']) for c in bank['cases']),
             'previous_bank_sha256': hashlib.sha256(Path(args.previous_bank).read_bytes()).hexdigest(),
             'current_bank_sha256': hashlib.sha256(current.read_bytes()).hexdigest(),
             'started_at': started, 'kind': 'fresh_context_missing_cases_supplement'}
    (out / 'scope.json').write_text(json.dumps(scope, ensure_ascii=False, indent=2))
    manifest.update(phase='L1_SUPPLEMENT_RUNNING', active_session=sid)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    rules = (root / 'candidate' / packet.RULES).read_text()
    answer, meta = candidate_turn(root, sid, prompt, out / 'round-01', selected['api_key'],
                                  args.model, fresh=True, system_prompt=rules,
                                  parse_json=False, timeout=600)
    (out / 'answer.md').write_text(answer)
    outcome = {'meta': meta, 'started_at': started,
               'completed_at': datetime.now(timezone.utc).isoformat(),
               'judgment': 'PENDING_REVIEW'}
    (out / 'outcome.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
    manifest.update(phase='L1_SUPPLEMENT_COMPLETED_PENDING_REVIEW')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'phase': manifest['phase'], 'case_count': len(added),
                      'seconds': meta['seconds'], 'native_tools': meta['tools']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
