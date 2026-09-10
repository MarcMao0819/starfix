#!/bin/bash
# PostCompact hook：把压缩摘要留档（审计压缩丢了什么）。永远 exit 0。
set -u
in="$(cat)"
[ -z "${FLEET_HOME:-}" ] && exit 0
HD="$FLEET_HOME/handoff"; mkdir -p "$HD"
# 注意：python3 - 的 stdin 被 heredoc 占用，JSON 走环境变量
IN="$in" HD="$HD" python3 - <<'PY' 2>>"$HD/hook.log"
import sys, json, datetime, os
d = json.loads(os.environ.get("IN") or "{}"); s = (d.get("compact_summary") or "")
with open(os.path.join(os.environ["HD"], "compact-log.md"), "a", encoding="utf-8") as f:
    f.write(f"\n## {datetime.datetime.now():%F %T} trigger={d.get('trigger')} sid={d.get('session_id')}\n{s[:4000]}\n")
PY
exit 0
