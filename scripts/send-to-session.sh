#!/bin/bash
# send-to-session.sh —— 向另一个终端会话（iTerm2）投递一条消息，两步式 + 读回确认。
#
# 为什么要两步：iTerm2 的 `write text ... newline NO` 只把字塞进输入行，不提交；
# 补一个回车才算发出去。首发回车经常不吃，所以要读回确认。
#
# 为什么要读回：**落盘≠送到**。盲补回车曾把对方正在打的半截消息强行提交出去，
# 所以补回车前必须确认输入行里停的是我们自己的关键词，是别的内容就绝不回车——
# 宁可不投，不可误发。
#
# 前置条件（缺一不可，离线环境跑不了，这也是本脚本提供 --dry-run 的原因）：
#   1) macOS + iTerm2 正在运行，且目标会话仍存在；
#   2) iTerm2 已开启 Python/AppleScript API：Preferences → General → Magic →
#      Enable Python API（首次调用会弹授权框，需人工点同意）；
#   3) 调用方进程有「自动化」权限（系统设置 → 隐私与安全性 → 自动化 → 允许控制 iTerm2）；
#   4) 会话 ID 必须是现查的（fleet-scan.sh 可列出），**不要手抄**——
#      抄错的 ID 会表现为「对方忙」，白投一轮。
#
# 用法：
#   send-to-session.sh <session_id> <message> <keyword>
#   send-to-session.sh --dry-run <session_id> <message> <keyword>
#
#   <keyword> 是 <message> 的字面子串，用于补回车前的读回确认；
#   不是子串会直接 KW_NOT_IN_MSG_ABORT（否则读回永远失败 → 无限重投）。
#
# 退出码：0=已送达  3=会话不存在/参数不合法  其他=投递未确认

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi

SID="$1"; MSG="$2"; KW="$3"

if [ "$DRY" = "1" ]; then
  # 只打印将要做的三步，不调用任何 osascript，不碰任何终端
  echo "[dry-run] 目标会话 : $SID"
  echo "[dry-run] 关键词   : $KW"
  case "$MSG" in *"$KW"*) echo "[dry-run] 关键词是消息子串 : 是（读回确认可用）";;
                       *) echo "[dry-run] 关键词是消息子串 : 否 → 实投会 KW_NOT_IN_MSG_ABORT";; esac
  echo "[dry-run] 将执行三步："
  echo "[dry-run]   ① 读回目标输入行，确认空闲（最多等 30s；非空=对方在打字，放弃）"
  echo "[dry-run]   ② write text «消息» newline NO   —— 只填入，不提交"
  echo "[dry-run]   ③ 读回确认输入行里是本条关键词后，补 character id 13 提交；"
  echo "[dry-run]      读回不到关键词则不补回车（宁可不投，不可误发）"
  echo "[dry-run] 消息首行 : $(printf '%s' "$MSG" | head -1 | cut -c1-60)"
  echo "[dry-run] 未触碰任何终端。"
  exit 0
fi



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

composer_line() { read_tail | /usr/bin/grep -E '❯|›|»' | tail -1; }
# 占位灰字白名单：codex 空输入框会显示提示文字，视同空
is_idle_line() {
  local L="$1"
  # <日期>：<某 CLI> 的输入行带右侧边框 │（"│ ❯    …    │"），空提示行以边框结尾也视同空
  printf '%s' "$L" | /usr/bin/grep -qE '(❯|›|»)[[:space:]]*│?[[:space:]]*$' && return 0
  printf '%s' "$L" | /usr/bin/grep -qE 'Implement {feature}|Ask Codex to do anything|Find and fix a bug in @filename|Write tests for @filename|Run /review on my current changes|Summarize recent commits|Explain this codebase|Use /skills to list available skills' && return 0
  return 1
}

# ⓪ 会话存在性：ID 对不上时 read_tail 为空（活会话至少有提示符），与「忙」分开报
# <日期> 战例27：主窗手写 UUID 后缀，三单被当 COMPOSER_BUSY 空投一轮
if [ -z "$(read_tail)" ]; then echo "SESSION_NOT_FOUND sid=$SID"; exit 3; fi

# ⓪ 关键词必须是消息的字面子串，否则读回确认永远失败→循环重投（真实事故：同一条给同一舰员排队 3 次）
case "$MSG" in *"$KW"*) ;; *) echo "KW_NOT_IN_MSG_ABORT"; exit 3;; esac

# ① 空闲检查（双采样版）：两次采样均为空/占位且内容一致才算空闲
for i in $(seq 1 15); do
  A=$(composer_line); sleep 2; B=$(composer_line)
  if [ "$A" = "$B" ] && is_idle_line "$A"; then IDLE=1; break; fi
  IDLE=0
done
if [ "${IDLE:-0}" != "1" ]; then echo "COMPOSER_BUSY_ABORT"; exit 2; fi

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
  end tell
  return "NOT_FOUND"
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
  end tell
  return "NOT_FOUND"
end run
AS
# ③ 读回确认；补 CR 仅当输入行里还是我们自己的关键词
for i in 1 2 3; do
  sleep 2
  TAIL=$(read_tail)
  printf '%s' "$TAIL" | /usr/bin/grep -q "$KW" && {
    CL=$(printf '%s' "$TAIL" | /usr/bin/grep '❯' | tail -1)
    if printf '%s' "$CL" | /usr/bin/grep -q "$KW"; then
      # 关键词还停在输入行=未提交，且是我们自己的文本 → 补一次 CR
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
      continue
    fi
    echo "DELIVERED attempt=$i"; exit 0
  }
  # 关键词整屏找不到（被清屏/滚走）——不盲补 CR，直接报未确认
done
echo "UNCONFIRMED_NO_BLIND_CR"
exit 1
