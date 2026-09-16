#!/bin/bash
# send-to-session.sh —— 向另一个 iTerm2 会话两步式投递消息，并做折行容忍读回。
#
# 为什么要两步：iTerm2 的 `write text ... newline NO` 只填入输入行，不提交；
# 补回车才算真正发送。发送后必须读回确认，避免输入区仍停留未提交文本时误判成功。
#
# 空闲识别用 CLI 数据表：名称 <TAB> 识别特征 <TAB> 空占位正则。
# 读回前压平 ANSI/框线/空白，因此关键词跨终端折行也能确认。
# 幂等只按“整条消息 + 近期尾巴”判断；历史里有同关键词不会封死新消息复投。
#
# 前置条件：
#   1) macOS + iTerm2 正在运行，且目标会话仍存在；
#   2) iTerm2 已开启 Python/AppleScript API；
#   3) 调用方进程有「自动化」权限；
#   4) 会话 ID 必须现查，不要手抄旧 ID。
#
# 用法：
#   send-to-session.sh <session_id> <message> <keyword>
#   send-to-session.sh --dry-run <session_id> <message> <keyword>
#   send-to-session.sh --self-test
#
# 测试投递保护：消息含“投递自测：”或“TEST:”时视为测试投递。
# 默认只能投 tab 标题带 [TEST] 的窗口；对生产窗口放行必须显式 --allow-live。
#
# 退出码：0=已送达/已确认重复/自测通过；3=参数或目标不合法；2=忙；
#         1=读回未确认；4=测试消息命中生产窗口且未 --allow-live。

DRY=0
ALLOW_LIVE=0
while [ "$#" -gt 0 ]; do
  case "${1:-}" in
    --dry-run) DRY=1; shift;;
    --allow-live) ALLOW_LIVE=1; shift;;
    --self-test) SELF_TEST_REQUESTED=1; shift;;
    *) break;;
  esac
done

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

RECENT_LINES=30

recent_tail_text() {
  printf '%s\n' "$1" | tail -n "$RECENT_LINES"
}

message_in_recent_tail() {
  local recent message
  recent="$(printf '%s\n' "$1" | normalize_text)"
  message="$(printf '%s\n' "$2" | normalize_text)"
  [ -n "$message" ] && [[ "$recent" == *"$message"* ]]
}

is_test_message() {
  case "$1" in *'投递自测：'*|*'TEST:'*) return 0;; *) return 1;; esac
}

is_test_window_title() {
  case "$1" in *'[TEST]'*|*'[test]'*) return 0;; *) return 1;; esac
}

read_title() {
  osascript - "$SID" <<'AS'
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is (item 1 of argv) then return (name of t)
        end repeat
      end repeat
    end repeat
  end tell
end run
AS
}

guard_live_test_delivery() {
  is_test_message "$MSG" || return 0
  [ "$ALLOW_LIVE" = "1" ] && return 0
  local title
  title="$(read_title)"
  if is_test_window_title "$title"; then return 0; fi
  echo "LIVE_TEST_DELIVERY_BLOCKED sid=$SID title=$title"
  echo "use --allow-live only after confirming this is not a real crew window"
  return 4
}

self_test() {
  local history_kw recent_msg
  history_kw='历史：SF-X 回执'
  recent_msg='投递自测：SF-X 回执 R2'
  local full_tail=$'SF-X 回执\n旧状态行\n'"$recent_msg"$'\n状态行'
  if message_in_recent_tail "$full_tail" "$recent_msg"; then
    echo 'GREEN_RECENT_FULL_MESSAGE_IDEMPOTENT_PASS'
  else
    echo 'GREEN_RECENT_FULL_MESSAGE_IDEMPOTENT_FAIL'
    return 1
  fi
  # Old implementation checked the whole transcript for bare KW. The new matcher
  # must not treat this as already-delivered when the full message is absent.
  local old_full_flat="$(printf '%s\n' "$full_tail" | normalize_text)"
  local old_kw_flat="$(printf '%s\n' 'SF-X 回执' | normalize_text)"
  if [[ "$old_full_flat" == *"$old_kw_flat"* ]]; then
    echo 'RED_HISTORY_KEYWORD_ONLY_OLD_RULE_WOULD_BLOCK_PASS'
  else
    echo 'RED_HISTORY_KEYWORD_ONLY_OLD_RULE_WOULD_BLOCK_FAIL'
    return 1
  fi
  if ! message_in_recent_tail "$full_tail" '投递自测：全新消息 SF-X 回执 R2'; then
    echo 'GREEN_HISTORY_ONLY_ALLOWS_NEW_MESSAGE_PASS'
  else
    echo 'GREEN_HISTORY_ONLY_ALLOWS_NEW_MESSAGE_FAIL'
    return 1
  fi
  if is_test_message "$recent_msg" && ! is_test_message 'SF-X 回执'; then
    echo 'GREEN_TEST_MESSAGE_MARKER_DETECTED_PASS'
  else
    echo 'GREEN_TEST_MESSAGE_MARKER_DETECTED_FAIL'
    return 1
  fi
  if is_test_window_title 'timonus测试 [TEST]' && ! is_test_window_title 'timonus二副'; then
    echo 'GREEN_TEST_WINDOW_TITLE_GATE_PASS'
  else
    echo 'GREEN_TEST_WINDOW_TITLE_GATE_FAIL'
    return 1
  fi
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

if [ "${SELF_TEST_REQUESTED:-0}" = "1" ]; then
  self_test
  exit $?
fi

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

guard_live_test_delivery || exit $?

TAIL="$(read_tail)"
if [ -z "$TAIL" ]; then
  echo "SESSION_NOT_FOUND sid=$SID"
  exit 3
fi

case "$MSG" in *"$KW"*) ;; *) echo "KW_NOT_IN_MSG_ABORT"; exit 3;; esac

MSG_FLAT="$(printf '%s' "$MSG" | normalize_text)"
KW_FLAT="$(printf '%s' "$KW" | normalize_text)"
RECENT_FLAT="$(recent_tail_text "$TAIL" | normalize_text)"

if message_in_recent_tail "$TAIL" "$MSG"; then
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
RECENT_FLAT="$(recent_tail_text "$TAIL" | normalize_text)"
if message_in_recent_tail "$TAIL" "$MSG"; then
  echo "DELIVERED attempt=1 cli=$CLI"
  log_dispatch "delivered"
  exit 0
fi

COMPOSER_FLAT="$(composer_region | normalize_text)"
if [[ "$COMPOSER_FLAT" == *"$MSG_FLAT"* ]]; then
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
  RECENT_FLAT="$(recent_tail_text "$TAIL" | normalize_text)"
fi

if message_in_recent_tail "$TAIL" "$MSG"; then
  echo "DELIVERED attempt=2 cli=$CLI"
  log_dispatch "delivered"
  exit 0
fi

echo "UNCONFIRMED_NO_BLIND_CR cli=$CLI keyword=$KW"
exit 1
