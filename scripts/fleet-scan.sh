#!/bin/bash
# 舰队扫描：iTerm2 全窗口/标签/会话的 签号·会话ID·名称·模型·状态·任务线索
# 落盘: $FLEET_SNAPSHOT（默认 $FLEET_HOME/fleet-snapshot.txt，覆盖式，恒指最新）
set -uo pipefail

: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"
SNAP="${FLEET_SNAPSHOT:-$FLEET_HOME/fleet-snapshot.txt}"
TMP="/tmp/fleet-scan-raw.$$"

osascript > "$TMP" <<'APPLESCRIPT'
tell application "iTerm2"
  set out to ""
  set wIdx to 0
  repeat with w in windows
    set wIdx to wIdx + 1
    set tIdx to 0
    repeat with t in tabs of w
      set tIdx to tIdx + 1
      repeat with s in sessions of t
        set sName to name of s
        set sId to id of s
        set sText to text of s
        set tailLen to 400
        if (length of sText) < tailLen then set tailLen to length of sText
        set sTail to text ((length of sText) - tailLen + 1) thru -1 of sText
        set AppleScript's text item delimiters to linefeed
        set tParts to text items of sTail
        set AppleScript's text item delimiters to " "
        set flat to tParts as string
        set AppleScript's text item delimiters to ""
        set out to out & "@@W" & wIdx & "T" & tIdx & "|" & sId & "|" & sName & "|" & flat & linefeed
      end repeat
    end repeat
  end repeat
  return out
end tell
APPLESCRIPT

{
  echo "# 舰队快照 $(date '+%Y-%m-%d %H:%M:%S')"
  echo "# 签位 | 会话ID | 会话名 | 模型 | 状态 | 任务线索"
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in @@*) ;; *) continue ;; esac
    pos=${line%%|*}; rest=${line#*|}
    sid=${rest%%|*}; rest=${rest#*|}
    name=${rest%%|*}; tail=${rest#*|}
    pos=${pos#@@}
    model=$(printf '%s' "$tail" | grep -oE 'gpt-[0-9.]+-[a-z]+ [a-z]+( fast)?' | tail -1)
    if [ -z "$model" ]; then
      case "$name" in
        *claude*) model="claude" ;;
        *"<模型C>"*)   model="<模型C>/DS" ;;
        *)        model="?" ;;
      esac
    fi
    if printf '%s' "$tail" | grep -q 'esc to interrupt'; then state="Working"
    elif printf '%s' "$tail" | grep -q 'at capacity'; then state="容量满"
    else state="空闲"; fi
    clue=$(printf '%s' "$tail" | grep -oE '任务书/[^ ]+\.md' | tail -1)
    printf '%s | %s | %s | %s | %s | %s\n' "$pos" "$sid" "$name" "$model" "$state" "${clue:--}"
  done < "$TMP"
} | tee "$SNAP"
rm -f "$TMP"
