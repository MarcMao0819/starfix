#!/bin/bash
# SessionStart hook（matcher: compact|resume）：把本会话的交接文档打到 stdout → Claude Code 注入为上下文。
# 只在存在 HANDOFF-<session_id>.md 时输出；别的会话不受影响。永远 exit 0。
# 注入内容：分隔线以上的手写段全文（舰长的判断不截断）＋机械快照前 FLEET_HANDOFF_INJECT_LINES 行（默认 80），
# 并要求接手的模型先用 Read 把全文读完再动手。
set -u
in="$(cat)"
sid="$(printf '%s' "$in" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
src="$(printf '%s' "$in" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("source",""))' 2>/dev/null)"
[ -z "${FLEET_HOME:-}" ] || [ -z "$sid" ] && exit 0
F="$FLEET_HOME/handoff/HANDOFF-$sid.md"
[ -f "$F" ] || exit 0
LIM="${FLEET_HANDOFF_INJECT_LINES:-80}"
SEP='<!-- ===== 以下为机械快照，脚本每次覆盖；以上为舰长手写判断 ===== -->'
echo "<handoff source=\"$src\" file=\"$F\">"
echo "你刚经历上下文压缩/恢复。下面是压缩前落盘的交接文档：手写段全文 + 机械快照前 $LIM 行。先用 Read 把 $F 全文读完，再按舰长规程 §1「上任第一动作」续接：先核激活器与在飞单，再处置待答决策。"
if /usr/bin/grep -qF "$SEP" "$F"; then
  awk -v sep="$SEP" -v lim="$LIM" 'index($0, sep) == 1 { after = 1; print; next } !after { print; next } after && n < lim { print; n++ }' "$F"
else
  head -n "$((LIM + 160))" "$F"
fi
echo "</handoff>"
exit 0
