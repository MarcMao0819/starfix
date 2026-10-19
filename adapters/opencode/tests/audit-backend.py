"""Synthetic data-migration registration; never talks to a real database."""
import sys
import os
import json


def private_changeset_no():
    input_path = os.environ.get('STARFIX_PRIVATE_TEST_INPUTS')
    if not input_path:
        print('STARFIX_PRIVATE_TEST_INPUTS is required', file=sys.stderr)
        sys.exit(64)
    try:
        with open(input_path, encoding='utf-8') as input_file:
            inputs = json.load(input_file)
    except (OSError, json.JSONDecodeError):
        print('STARFIX_PRIVATE_TEST_INPUTS is unreadable or invalid', file=sys.stderr)
        sys.exit(64)
    changeset_no = inputs.get('changesetNo') if isinstance(inputs, dict) else None
    if not isinstance(changeset_no, str) or not changeset_no:
        print('STARFIX_PRIVATE_TEST_INPUTS has invalid structure', file=sys.stderr)
        sys.exit(64)
    return changeset_no

changeset_no = private_changeset_no()
sql = ' '.join(sys.argv[1:])
if 'SELECT IFNULL(commit_hash' in sql:
    print('NULL\t0')
elif 'SELECT COUNT(f.id)' in sql:
    print('0')
elif 'SELECT IFNULL(NULLIF(commit_hash' in sql:
    print('<EMPTY>\t0\tdata_migration')
elif 'SELECT id, changeset_no, title, commit_hash, file_count, change_type, branch' in sql:
    print('id\tchangeset_no\ttitle\tcommit_hash\tfile_count\tchange_type\tbranch')
    print(f'1\t{changeset_no}\tfixture\tNULL\t0\tdata_migration\tNULL')
elif 'SELECT s.changeset_no, IFNULL(s.commit_hash' in sql:
    print(f'{changeset_no}\t\t' + ('changed.csv\tADD' if os.environ.get('STARFIX_FIXTURE_DRIFT') else '\t') + '\tdata_migration')
elif 'SELECT id, changeset_no FROM' in sql:
    print(f'1\t{changeset_no}')
elif any(query in sql for query in ('SELECT file_path', 'SELECT f.file_path', 'SELECT f.change_action')):
    pass
else:
    print('Unknown synthetic SQL: ' + sql, file=sys.stderr)
    sys.exit(43)
