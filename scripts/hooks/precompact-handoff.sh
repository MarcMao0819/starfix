#!/bin/bash
# PreCompact hook：压缩前机械落盘本会话交接快照。stdin=Claude Code hook JSON（session_id/trigger/transcript_path）。
# 永远 exit 0（不阻止压缩）；失败只记日志。需要 FLEET_HOME（由本机包装脚本或全局环境提供）。
set -u
in="$(cat)"
sid="$(printf '%s' "$in" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
trig="$(printf '%s' "$in" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("trigger",""))' 2>/dev/null)"
[ -z "${FLEET_HOME:-}" ] && exit 0
[ -z "$sid" ] && exit 0
HD="$FLEET_HOME/handoff"; mkdir -p "$HD"
export FLEET_HANDOFF="$HD/HANDOFF-$sid.md"
bash "$(dirname "$0")/handoff-snapshot.sh" >>"$HD/hook.log" 2>&1 || echo "$(date '+%F %T') precompact snapshot failed sid=$sid" >>"$HD/hook.log"
ln -sfn "$FLEET_HANDOFF" "$HD/HANDOFF-latest.md"
echo "$(date '+%F %T') precompact trigger=$trig sid=$sid -> $FLEET_HANDOFF" >>"$HD/hook.log"
exit 0
