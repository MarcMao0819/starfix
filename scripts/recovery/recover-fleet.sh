#!/bin/bash
# 恢复舰队与书记。请在 iTerm 新标签运行。
#   recover-fleet.sh           只看现状
#   recover-fleet.sh up        拉回窗口与管家；舰队仍停着
#   recover-fleet.sh resume    先 up，再给舰长复工令
#   DRY=1 recover-fleet.sh up  演练：只打印
: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STARFIX_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
T="$SCRIPT_DIR"
SEND="$STARFIX_ROOT/scripts/send-to-session.sh"
PY=/usr/bin/python3
LOG="${FLEET_RECOVERY_LOG:-$FLEET_HOME/recovery/recover-last.txt}"
TABLE="${FLEET_RECOVERY_TABLE:-$FLEET_HOME/recovery/fleet-table.json}"
KEY="${RECOVERY_KEY:-RECOVER-PLACEHOLDER}"
ME="$(echo "${ITERM_SESSION_ID#*:}" | tr 'a-z' 'A-Z')"
MODE="${1:-status}"

case "$MODE" in status|up|resume) ;; *) sed -n '2,12p' "$0"; exit 1 ;; esac
[ "$MODE" = status ] && LOG=/dev/null
say() { echo "$*" | tee -a "$LOG"; }
run() { if [ -n "$DRY" ]; then say "   [演练，没做] $*"; else "$@" 2>&1 | tee -a "$LOG"; fi; }

mkdir -p "$(dirname "$LOG")" "$(dirname "$TABLE")"
[ "$MODE" != status ] && : > "$LOG"
say "模式 $MODE${DRY:+（演练）}｜本机钟 $(date '+%H:%M %Z')"

if ! ps -axo comm | /usr/bin/grep -q "iTerm.app/Contents/MacOS/iTerm2$"; then
  say "== iTerm 没开"
  [ "$MODE" = status ] && { say "   用 recover-fleet.sh up 来开"; exit 0; }
  run open -a iTerm
  [ -z "$DRY" ] && sleep 8
fi

if [ -n "${BOARD_CHECK_URL:-}" ]; then
  if curl -s -m 3 -o /dev/null "$BOARD_CHECK_URL"; then
    say "== 看板：在跑"
  else
    say "== 看板：没在跑（设了 BOARD_CHECK_URL 但未响应）"
  fi
fi

if [ "$MODE" = status ] || [ -n "$DRY" ]; then
  run "$PY" "$T/adopt-open.py" --dry
else
  run "$PY" "$T/adopt-open.py"
fi

MAP="$($PY - "$TABLE" <<'E'
import json, sys
for k, v in json.load(open(sys.argv[1], encoding='utf-8')).items():
    if k.startswith('_'):
        continue
    print((v.get('iterm') or '')[:8].upper(), k)
E
)"
key_of() { echo "$MAP" | awk -v s="$1" '$1==s {print $2}'; }
SEC_SID="$(echo "$MAP" | awk '$2=="secretary" {print $1}')"

say "== 各标签现状"
LIST="$($PY "$T/resume-inplace.py" --list 2>&1)"
echo "$LIST" | cut -c1-90 | tee -a "$LOG"
STATES="$(echo "$LIST" | awk 'length($1)==8 && $1 !~ /[^0-9A-F]/ { st=$3; l=$0; sub(/[ \t]+$/,"",l); if (st!="标签不在" && l ~ /[%$]$/) st="命令行"; print $1, $2, st }')"
SEC_STATE="$(echo "$STATES" | awk -v s="$SEC_SID" '$1==s {print $3}')"
if [ -z "$SEC_STATE" ] && [ -f "$TABLE" ]; then
  say "★各标签的现状没读出来。什么都没动。等半分钟再跑一次。"
  exit 1
fi
MYKEY="$(key_of "${ME:0:8}")"
if [ "$MODE" != status ] && [ -n "$MYKEY" ] && [ "$MYKEY" != secretary ]; then
  say "★请在 iTerm 里开一个新标签再跑 recover-fleet.sh。什么都没动。"
  exit 1
fi
RESUMED="$FLEET_HOME/recovery/.recover-resumed"
[ "$MODE" != status ] && : > "$RESUMED"

if [ "$MODE" != status ]; then
  say "== 把没在跑的拉回来"
  echo "$STATES" | while read -r sid role state; do
    key="$(key_of "$sid")"
    [ -z "$key" ] || [ "$key" = secretary ] && continue
    case "$state" in
      Claude在跑) ;;
      命令行)   say "-- ${role}：原地续上"; run "$PY" "$T/resume-inplace.py" "$sid" < /dev/null; echo "$key" >> "$RESUMED" ;;
      标签不在) say "-- ${role}：开新标签续上"; run "$PY" "$T/restore-fleet.py" "$key" < /dev/null; echo "$key" >> "$RESUMED" ;;
      *)        say "-- ${role}：认不出状态（${state}），没动" ;;
    esac
  done

  if ps -axo command | /usr/bin/grep -q "[s]teward2.py --loop"; then
    say "== 上下文管家：在跑"
  else
    say "== 上下文管家：停了，开新标签重起"
    run "$PY" "$T/open-steward.py" < /dev/null
  fi
fi

dispatch_msg() {
  local sid="$1" file="$2"
  [ -z "$sid" ] && return
  if [ -n "$DRY" ]; then
    say "   [演练，没做] 投递到 ${sid}"
    return
  fi
  kw="$(head -1 "$file" | cut -c1-20)"
  run "$SEND" "$sid" "$(cat "$file")" "$kw"
}

if [ "$MODE" != status ] && [ -s "$RESUMED" ]; then
  say "== 叫刚拉起来的窗口重挂监听"
  sed "s/__KEY__/$KEY/" "$T/msg-relisten.txt" > "$T/.msg-relisten-now.txt"
  while read -r key; do
    [ "$MODE" = resume ] && [ "$key" != relay ] && continue
    sid="$($PY -c "import json;print(json.load(open('$TABLE',encoding='utf-8')).get('$key',{}).get('iterm','')[:8].upper())")"
    dispatch_msg "$sid" "$T/.msg-relisten-now.txt"
  done < "$RESUMED"
fi

if [ "$MODE" = resume ]; then
  say "== 给舰长复工令"
  sed "s/__KEY__/$KEY/" "$T/msg-resume-captains.txt" > "$T/.msg-resume-now.txt"
  $PY - "$TABLE" <<'E' | while read -r sid key; do
import json, sys
for k, v in json.load(open(sys.argv[1], encoding='utf-8')).items():
    if k.startswith('_') or k in ('secretary', 'relay'):
        continue
    print((v.get('iterm') or '')[:8].upper(), k)
E
    dispatch_msg "$sid" "$T/.msg-resume-now.txt"
  done
fi

[ "$MODE" = status ] && { say "== 书记：${SEC_STATE:-未知}"; exit 0; }

sed -e "s/__KEY__/$KEY/" -e "s/__MODE__/$MODE/" "$T/msg-recover-secretary.txt" > "$T/.msg-recover-now.txt"
if [ "$SEC_STATE" = 命令行 ] && [ "${ME:0:8}" != "$SEC_SID" ]; then
  say "== 书记：原地续上"
  run "$PY" "$T/resume-inplace.py" "$SEC_SID" < /dev/null
  SEC_STATE=Claude在跑
fi
if [ "$SEC_STATE" = Claude在跑 ]; then
  say "== 书记：在跑，发恢复检查"
  dispatch_msg "$SEC_SID" "$T/.msg-recover-now.txt"
  say "完。记录在 $LOG"
  exit 0
fi

say "★书记状态「${SEC_STATE}」认不准，没动它。请切到那个标签处理后重跑。"
exit 1