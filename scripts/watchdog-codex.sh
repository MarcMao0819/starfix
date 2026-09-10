#!/bin/bash
# Codex 断流看门狗（<日期> 主窗重建，交接文档 §五.1 写法）
# 名单：watchdog-codex.txt，每行 "会话ID 名称"。每 5 分钟 osascript 读各工位屏幕尾部，
# 状态跃迁才报（OK→DISC / OK→GONE），避免刷屏。主窗派单/收单时维护名单。
# <日期> WDLIMIT：尾部匹配 usage limit / purchase more credits / try again at，
# 输出用量上限事件（含 try again at 原文），同窗 6 小时去重（<日期> 主窗调：30 分钟太吵）。断流/消失逻辑不变。
D="$(cd "$(dirname "$0")" && pwd)"
LIST="${D}/watchdog-codex.txt"
STATE="${D}/.watchdog-codex-state"
LIMIT_STATE="${D}/.watchdog-codex-limit-state"
ONCE=0
if [ "${1:-}" = "--once" ]; then
  ONCE=1
fi
touch "${STATE}"
touch "${LIMIT_STATE}"
while true; do
  while read -r sid name; do
    [ -z "${sid}" ] && continue
    SCREEN=$(osascript - "${sid}" <<'AS' 2>/dev/null
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
  return "SESSION_GONE"
end run
AS
)
    TAILPART=$(printf '%s' "${SCREEN}" | tail -14)
    cur="OK"
    if [ "${SCREEN}" = "SESSION_GONE" ] || [ -z "${SCREEN}" ]; then
      cur="GONE"
    elif printf '%s' "${TAILPART}" | /usr/bin/grep -q "stream disconnected" && ! printf '%s' "${TAILPART}" | /usr/bin/grep -q "Working ("; then
      cur="DISC"
    fi
    prev=$(/usr/bin/grep "^${sid}=" "${STATE}" | cut -d= -f2)
    prev=${prev:-OK}
    if [ "${cur}" != "${prev}" ]; then
      if [ "${cur}" = "DISC" ]; then
        echo "⚠断流【${name} ${sid:0:8}】stream disconnected 且无 Working，需推续工"
      elif [ "${cur}" = "GONE" ]; then
        echo "⚠工位消失【${name} ${sid:0:8}】osascript 找不到该会话"
      else
        echo "✓恢复【${name} ${sid:0:8}】回到运行态"
      fi
      /usr/bin/grep -v "^${sid}=" "${STATE}" > "${STATE}.tmp" 2>/dev/null; mv "${STATE}.tmp" "${STATE}"
      echo "${sid}=${cur}" >> "${STATE}"
    fi
    if [ "${cur}" != "GONE" ] && printf '%s' "${TAILPART}" | /usr/bin/grep -qiE 'usage limit|purchase more credits|try again at'; then
      again=$(printf '%s' "${TAILPART}" | /usr/bin/grep -oiE 'try again at[[:print:]]+' | /usr/bin/sed -n '1p' | /usr/bin/sed 's/[[:space:]]*$//')
      if [ -z "${again}" ]; then
        again="未抓到"
      fi
      slot="${name%%-*}"
      now=$(date +%s)
      last=$(/usr/bin/grep "^${sid}=" "${LIMIT_STATE}" | cut -d= -f2)
      last=${last:-0}
      DEDUP_SECONDS=21600   # 非端口：21600 秒 = 6 小时，同窗用量上限事件的去重窗口
      if [ $((now - last)) -ge "$DEDUP_SECONDS" ]; then
        echo "⚠codex 用量上限【${slot}】恢复时间：${again}"
        /usr/bin/grep -v "^${sid}=" "${LIMIT_STATE}" > "${LIMIT_STATE}.tmp" 2>/dev/null; mv "${LIMIT_STATE}.tmp" "${LIMIT_STATE}"
        echo "${sid}=${now}" >> "${LIMIT_STATE}"
      fi
    fi
  done < "${LIST}"
  if [ "${ONCE}" = "1" ]; then
    break
  fi
  sleep 300
done
