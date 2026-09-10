#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Examiner-side, allowlist-only candidate packet export. Not an OS sandbox."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = 'candidate/系统提示词.md'
TOOLS = 'candidate/L2-操作说明.md'
STAGES = {
    'bootstrap': [RULES],
    'l1': [RULES, 'candidate/L1-题本.md'],
    'l2': [RULES, TOOLS, 'candidate/任务素材.md'],
    'l3': [RULES, TOOLS],
    'l4': [RULES],
}


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def plain_file(path, root):
    if not inside(path, root):
        raise ValueError('path outside allowlisted root')
    cur = path
    while cur != root:
        if cur.is_symlink():
            raise ValueError('symlink forbidden: ' + str(cur))
        cur = cur.parent
    if not path.is_file():
        raise ValueError('allowlisted file is missing or not regular')
    if path.stat().st_nlink != 1:
        raise ValueError('hardlinked source forbidden')
    return path.read_bytes()


def export_packet(destination, seal_path, stage, source=ROOT):
    source = Path(source).resolve()
    # Check before resolve: a preexisting symlink is not a fresh output directory.
    destination, seal_path = Path(destination), Path(seal_path)
    if destination.exists() or destination.is_symlink() or seal_path.exists() or seal_path.is_symlink():
        raise ValueError('output packet and examiner seal must be new paths')
    destination, seal_path = destination.resolve(), seal_path.resolve()
    repository = next((p for p in (source, *source.parents) if (p / '.git').exists()), source)
    if inside(destination, repository) or inside(repository, destination):
        raise ValueError('candidate export must be outside the source repository subtree')
    if inside(seal_path, destination):
        raise ValueError('examiner seal must remain outside candidate packet')
    # Pre-read every source; never recursively copy the repository or its Git history.
    content = {rel: plain_file(source / rel, source) for rel in STAGES[stage]}
    destination.mkdir(parents=True, exist_ok=False)
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    for rel, data in content.items():
        p = destination / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as f:
            f.write(data)
    seal = {'schema': 1, 'stage': stage,
            'files': {rel: hashlib.sha256(data).hexdigest() for rel, data in content.items()},
            'notice': 'This proves packet contents only; candidate tool/process isolation must be tested separately.'}
    with seal_path.open('x', encoding='utf-8') as f:
        json.dump(seal, f, ensure_ascii=False, indent=2)
    return seal


def verify_packet(destination, seal):
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError('packet root must be a real directory')
    destination = destination.resolve()
    if seal.get('schema') != 1 or seal.get('stage') not in STAGES:
        raise ValueError('invalid examiner seal')
    expected = set(STAGES[seal['stage']])
    if set(seal.get('files', {})) != expected:
        raise ValueError('seal itself violates the public-file allowlist')
    actual, directories = set(), set()
    for p in destination.rglob('*'):
        if p.is_symlink():
            raise ValueError('symlink in exported packet')
        rel = p.relative_to(destination).as_posix()
        if p.is_dir():
            directories.add(rel)
        else:
            actual.add(rel)
    expected_dirs = {str(Path(rel).parent) for rel in expected}
    if actual != expected or directories != expected_dirs:
        raise ValueError('extra or missing packet content; Git/history/answers are not allowed')
    for rel in expected:
        data = plain_file(destination / rel, destination)
        if hashlib.sha256(data).hexdigest() != seal['files'][rel]:
            raise ValueError('packet content changed: ' + rel)
    return {'packet_verified': True, 'stage': seal['stage'], 'files': len(expected),
            'runtime_isolation_verified': False}


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    x = sub.add_parser('export'); x.add_argument('--stage', choices=STAGES, required=True)
    x.add_argument('--out', required=True); x.add_argument('--seal', required=True)
    x = sub.add_parser('verify'); x.add_argument('--packet', required=True); x.add_argument('--seal', required=True)
    a = p.parse_args()
    try:
        if a.cmd == 'export':
            s = export_packet(a.out, a.seal, a.stage)
            result = verify_packet(a.out, s)
        else:
            result = verify_packet(a.packet, json.loads(Path(a.seal).read_text(encoding='utf-8')))
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as e:
        p.error(str(e))


if __name__ == '__main__':
    main()
