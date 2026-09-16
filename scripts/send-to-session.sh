#!/bin/bash
# send-to-session.sh —— 向另一个 iTerm2 会话两步式投递消息，并做折行容忍读回。
#
# 空闲识别用 CLI 数据表：名称 <TAB> 识别特征 <TAB> 空占位正则。
# 读回前压平 ANSI/框线/空白；目标尾部已有同关键词时幂等返回，不重投。

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi

SID="${1:-}"
MSG="${2:-}"
KW="${3:-}"
DISPATCH_LOG="${DISPATCH_LOG:-$HOME/fleet-data/dispatch.log}"

cli_rules() {
  cat <<'RULES'
opencode	• OpenCode [0-9]+\.|┃[[:space:]]*Build ·	ctrl\+p commands|• OpenCode [0-9]+\.|┃[[:space:]]*(Build ·|Build anything)?[[:space:]]*│?[[:space:]]*$
deepseek	DeepSeek V4|│[[:space:]]*❯	(❯[[:space:]]*Build[[:space:]]+anything[[:space:]]*│?[[:space:]]*$)|(❯[[:space:]]*│?[[:space:]]*$)
codex	❯|›|»	(^|[[:space:]])(Ask Codex to do anything|Implement \{feature\}|Find and fix a bug in @filename|Write tests for @filename|Run /review on my current changes|Summarize recent commits|Explain this codebase|Use /skills to list available skills)([[:space:]]|$)|Find and fix a bug in @filename|Write tests for @filename|Run /review on my current changes|Summarize recent commits|Explain this codebase|Use /skills to list available skills
shared-prompt	❯|›|»	(❯|›|»)[[:space:]]*│?[[:space:]]*$
RULES
}

read_tail() {
  osascript - "$SID" <<'AS'
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is (item 1 of argv) then return (text of s)
        end repeat
      end repeat
    end repeat
  end tell
end run
AS
}

normalize_text() {
  python3 -c '
import re, sys
text = sys.stdin.read()
text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)?", "", text)
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
text = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
text = re.sub(r"[│┃┏┓└┘─━]+", "", text)
sys.stdout.write("".join(text.split()))
'
}

log_dispatch() {
  local state="$1"
  mkdir -p "$(dirname "$DISPATCH_LOG")"
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SID" "$KW" "$state" >> "$DISPATCH_LOG"
}

detect_cli() {
  local rule_name recognizer unused
  while IFS=$'\t' read -r rule_name recognizer unused; do
    [ -n "$recognizer" ] || continue
    printf '%s\n' "$TAIL" | /usr/bin/grep -v '^[[:space:]]*$' | tail -5 | /usr/bin/grep -qE "$recognizer" && { printf '%s\n' "$rule_name"; return 0; }
  done < <(cli_rules)
  printf '%s\n' "shared-prompt"
}

idle_regex_for() {
  local rule_name recognizer idle
  while IFS=$'\t' read -r rule_name recognizer idle; do
    [ "$rule_name" = "$1" ] || continue
    printf '%s\n' "$idle"
    return 0
  done < <(cli_rules)
  printf '%s\n' '(❯|›|»)[[:space:]]*│?[[:space:]]*$'
}

composer_region() {
  case "$CLI" in
    codex)
      printf '%s\n' "$TAIL" | tail -8 | /usr/bin/grep -E 'Ask Codex to do anything|Implement \{feature\}|Find and fix a bug in @filename|Write tests for @filename|Run /review on my current changes|Summarize recent commits|Explain this codebase|Use /skills to list available skills' | tail -1
      ;;
    deepseek)
      printf '%s\n' "$TAIL" | tail -8 | /usr/bin/grep -E '❯' | tail -1
      ;;
    opencode)
      printf '%s\n' "$TAIL" | tail -12 | /usr/bin/grep -E '┃[[:space:]]*(Build ·|Build anything)|ctrl\+p commands|• OpenCode [0-9]+\.' | tail -1
      ;;
    *)
      printf '%s\n' "$TAIL" | tail -8 | /usr/bin/grep -E '❯|›|»' | tail -1
      ;;
  esac
}

if [ -z "$SID" ] || [ -z "$MSG" ] || [ -z "$KW" ]; then
  echo "USAGE: $0 [--dry-run] <session_id> <message> <keyword>"
  exit 3
fi

if [ "$DRY" = "1" ]; then
  echo "[dry-run] 目标会话 : $SID"
  echo "[dry-run] 关键词   : $KW"
  case "$MSG" in *"$KW"*) echo "[dry-run] 关键词是消息子串 : 是";;
                       *) echo "[dry-run] 关键词是消息子串 : 否 → KW_NOT_IN_MSG_ABORT";; esac
  echo "[dry-run] 读回将压平折行/空白/框线后做字面匹配"
  echo "[dry-run] 幂等：目标尾部已含关键词时不再重投"
  exit 0
fi

TAIL="$(read_tail)"
if [ -z "$TAIL" ]; then
  echo "SESSION_NOT_FOUND sid=$SID"
  exit 3
fi

case "$MSG" in *"$KW"*) ;; *) echo "KW_NOT_IN_MSG_ABORT"; exit 3;; esac

TAIL_FLAT="$(printf '%s' "$TAIL" | normalize_text)"
KW_FLAT="$(printf '%s' "$KW" | normalize_text)"

if [[ "$TAIL_FLAT" == *"$KW_FLAT"* ]]; then
  echo "ALREADY_DELIVERED sid=$SID keyword=$KW"
  log_dispatch "already-delivered"
  exit 0
fi

CLI="$(detect_cli)"
IDLE_REGEX="$(idle_regex_for "$CLI")"

IDLE=0
for attempt in $(seq 1 15); do
  TAIL="$(read_tail)"
  A="$(composer_region)"
  sleep 2
  TAIL="$(read_tail)"
  B="$(composer_region)"
  if [ "$A" = "$B" ] && printf '%s' "$A" | /usr/bin/grep -qE "$IDLE_REGEX"; then
    IDLE=1
    break
  fi
  IDLE=0
done
if [ "$IDLE" != "1" ]; then
  echo "COMPOSER_BUSY_ABORT cli=$CLI"
  exit 2
fi

osascript - "$SID" "$MSG" <<'AS'
on run argv
  set targetId to item 1 of argv
  set msg to item 2 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is targetId then
            tell s to write text msg newline NO
            return "STEP1_OK"
          end if
        end repeat
      end repeat
    end repeat
    return "NOT_FOUND"
  end tell
end run
AS

osascript - "$SID" <<'AS'
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is (item 1 of argv) then
            tell s to write text (character id 13) newline NO
            return "STEP2_OK"
          end if
        end repeat
      end repeat
    end repeat
    return "NOT_FOUND"
  end tell
end run
AS

sleep 2
TAIL="$(read_tail)"
TAIL_FLAT="$(printf '%s' "$TAIL" | normalize_text)"
if [[ "$TAIL_FLAT" == *"$KW_FLAT"* ]]; then
  echo "DELIVERED attempt=1 cli=$CLI"
  log_dispatch "delivered"
  exit 0
fi

COMPOSER_FLAT="$(composer_region | normalize_text)"
if [[ "$COMPOSER_FLAT" == *"$KW_FLAT"* ]]; then
  osascript - "$SID" <<'AS' >/dev/null
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is (item 1 of argv) then tell s to write text (character id 13) newline NO
        end repeat
      end repeat
    end repeat
  end tell
end run
AS
  sleep 2
  TAIL="$(read_tail)"
  TAIL_FLAT="$(printf '%s' "$TAIL" | normalize_text)"
fi

if [[ "$TAIL_FLAT" == *"$KW_FLAT"* ]]; then
  echo "DELIVERED attempt=2 cli=$CLI"
  log_dispatch "delivered"
  exit 0
fi

echo "UNCONFIRMED_NO_BLIND_CR cli=$CLI keyword=$KW"
exit 1
