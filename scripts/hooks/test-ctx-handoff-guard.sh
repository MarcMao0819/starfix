#!/bin/bash
# test-ctx-handoff-guard.sh —— ctx-handoff-guard.py 与 sessionstart-handoff.sh 的两态自证（全部在临时目录里跑）。
# 用法：bash test-ctx-handoff-guard.sh   退出码 0＝全部通过；任一用例失败打印 FAIL 并以 1 退出。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export FLEET_HOME="$T/fleet"; mkdir -p "$FLEET_HOME/handoff"
export CTX_SNAPSHOT_DIR="$T/snap"; mkdir -p "$CTX_SNAPSHOT_DIR"
export CTX_GUARD_STATE_DIR="$T/state"
export CTX_GUARD_LIST="$T/list.txt"
export ACTIVATOR_JSON="$T/activator.json"; echo '{"tasks":[],"dropped":[],"decisions":[]}' > "$ACTIVATOR_JSON"
export FLEET_LEDGER="$T/ledger.md"; echo "台账样例" > "$FLEET_LEDGER"
export FLEET_SNAPSHOT_MONITORS="$T/monitors.json"; echo '{}' > "$FLEET_SNAPSHOT_MONITORS"
unset ITERM_SESSION_ID
SID="test-sid-0001"; G="$HERE/ctx-handoff-guard.py"; fail=0
snap() { printf '{"context_window":{"used_percentage":%s}}' "$1" > "$CTX_SNAPSHOT_DIR/$SID.json"; }
run() { printf '{"session_id":"%s","hook_event_name":"%s","stop_hook_active":%s}' "$SID" "$1" "${2:-false}" | python3 "$G"; }
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }

snap 86
out="$(run PostToolUse)"; check "不在名单零输出" '[ -z "$out" ]'
echo "$SID  # 测试会话" > "$CTX_GUARD_LIST"
snap 50; out="$(run PostToolUse)"; check "50% 不触发" '[ -z "$out" ]'
snap 86; out="$(run PostToolUse)"
check "86% 触发并提示" 'printf "%s" "$out" | /usr/bin/grep -q "上下文用量 86%"'
check "86% 机械快照落盘" '[ -f "$FLEET_HOME/handoff/HANDOFF-$SID.md" ] && /usr/bin/grep -q "以下为机械快照" "$FLEET_HOME/handoff/HANDOFF-$SID.md"'
snap 87; out="$(run PostToolUse)"; check "同一周期不重复提醒" '[ -z "$out" ]'
out="$(run Stop false)"; check "手写段未更新→Stop 拦一次" 'printf "%s" "$out" | /usr/bin/grep -q "\"decision\": \"block\""'
out="$(run Stop false)"; check "Stop 只拦一次" '[ -z "$out" ]'
out="$(run Stop true)"; check "stop_hook_active 不拦" '[ -z "$out" ]'
snap 93; out="$(run UserPromptSubmit)"; check "93% 第二档再提醒" 'printf "%s" "$out" | /usr/bin/grep -q "第二档"'
snap 40; out="$(run PostToolUse)"; check "压缩后回落不出声" '[ -z "$out" ]'
check "回落后状态清零" '[ "$(python3 -c "import json;print(json.load(open(\"$CTX_GUARD_STATE_DIR/$SID.guard.json\")).get(\"level\",0))")" = "0" ]'
snap 86; out="$(run PostToolUse)"; check "新周期再次触发" 'printf "%s" "$out" | /usr/bin/grep -q "上下文用量 86%"'
python3 - "$FLEET_HOME/handoff/HANDOFF-$SID.md" <<'PY'
import sys
p=sys.argv[1]; t=open(p,encoding='utf-8').read()
open(p,'w',encoding='utf-8').write(t.replace('# HANDOFF','# HANDOFF（已补写手写段）',1))
PY
out="$(run Stop false)"; check "手写段已更新→Stop 不拦" '[ -z "$out" ]'
# 名单按 iTerm 会话 UUID 命中
echo "AAAA-ITERM-UUID" > "$CTX_GUARD_LIST"; rm -f "$CTX_GUARD_STATE_DIR/$SID.guard.json"
out="$(ITERM_SESSION_ID="w0t0p0:AAAA-ITERM-UUID" bash -c 'printf "{\"session_id\":\"'$SID'\",\"hook_event_name\":\"PostToolUse\"}" | python3 "'$G'"')"
check "按 iTerm UUID 命中名单" 'printf "%s" "$out" | /usr/bin/grep -q "上下文用量 86%"'
# SessionStart 注入：手写段全文 + 快照前 N 行；不存在的会话零输出
out="$(printf '{"session_id":"%s","source":"compact"}' "$SID" | FLEET_HANDOFF_INJECT_LINES=3 bash "$HERE/sessionstart-handoff.sh")"
check "SessionStart 注入含手写段" 'printf "%s" "$out" | /usr/bin/grep -q "已补写手写段"'
check "SessionStart 快照按行数截断" '[ "$(printf "%s\n" "$out" | sed -n "/以下为机械快照/,\$p" | wc -l | tr -d " ")" -le 5 ]'
out="$(printf '{"session_id":"no-such-sid","source":"compact"}' | bash "$HERE/sessionstart-handoff.sh")"
check "SessionStart 无交接文件零输出" '[ -z "$out" ]'
exit $fail
