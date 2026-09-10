#!/usr/bin/env bash
# receipt-watch.sh —— 回执目录监听：有新回执落盘就打印一行事件。
#
# 它替代的是正式版里的「回执监听」（specs/monitors.md ①）：舰长不去翻目录，
# 事件自己送到面前。监听的三条硬规矩这里也照做：
#   只报状态跃迁（新落盘的才报，启动时已经在的算基线，不报）；
#   有收尾条件（--expect 收够就退出，--timeout 到点就以非 0 退出）；
#   不静默（超时也要说清等的是什么、等了多久）。
#
# 用法：receipt-watch.sh [--expect N] [--timeout S] [--interval S] [--dir 目录]
#   --expect   收到 N 份新回执就退出（默认 0 = 一直看，靠 --timeout 收尾）
#   --timeout  秒，默认 60；到点仍不够 N 份 → 退出码 4
#   --interval 轮询间隔秒，默认 1
#
# 环境变量：
#   FLEET_HOME          必填，无默认值
#   FLEET_RECEIPT_DIR   回执目录，默认 $FLEET_HOME/receipts
#   QS_WATCH_INTERVAL   轮询间隔，默认 1
#
# 退出码：0=收够了 / 到点正常收尾　4=到点还没收够　3=参数或目录不合法
#
# 说明：这里只实现 1 秒轮询。fswatch/inotify 版本更省电，但那条分支在没装
# fswatch 的机器上跑不到、也就验不了；宁可留一条能验的路径，也不留一条
# 「看起来支持、实际没人跑过」的分支。要换事件驱动：把 sleep 那一行换成
# `fswatch -1 "$DIR"` 即可，扫描逻辑不用动。
set -euo pipefail
shopt -s nullglob

: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"

DIR="${FLEET_RECEIPT_DIR:-$FLEET_HOME/receipts}"
EXPECT=0
TIMEOUT=60
INTERVAL="${QS_WATCH_INTERVAL:-1}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --expect)   EXPECT="${2:?--expect 后面要跟数字}"; shift 2;;
    --timeout)  TIMEOUT="${2:?--timeout 后面要跟秒数}"; shift 2;;
    --interval) INTERVAL="${2:?--interval 后面要跟秒数}"; shift 2;;
    --dir)      DIR="${2:?--dir 后面要跟目录}"; shift 2;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0;;
    *) echo "不认识的参数：$1" >&2; exit 3;;
  esac
done

mkdir -p "$DIR"

terminal_state() {
  # 用 awk 不用 sed：回执里的「终态：」是中文，带多字节字符的 bracket 表达式
  # （形如 [终状]、[：:]）在不同平台的 sed 上行为不一致，会按字节切开误配。
  local f="$1" s
  s=$(awk '/终态|状态|结论/ { if (match($0, /PASS|FAIL|BLOCKED|PARTIAL/)) { print substr($0, RSTART, RLENGTH); exit } }' "$f" 2>/dev/null)
  echo "${s:-未知}"
}

# 基线：启动时已经在的不算事件（只报状态跃迁）
# 用「换行分隔的字符串」当集合，不用 declare -A：macOS 自带的 bash 是 3.2，
# 关联数组会直接语法报错。跨平台脚本里这是最常见的一脚踩空。
SEEN=$'\n'
BASE_N=0
seen_add() { SEEN="${SEEN}$1"$'\n'; }
seen_has() { case "$SEEN" in *$'\n'"$1"$'\n'*) return 0;; *) return 1;; esac; }
for f in "$DIR"/*; do
  [ -f "$f" ] || continue
  seen_add "$f"
  BASE_N=$((BASE_N + 1))
done
echo "👀 回执监听已挂｜目录 ${DIR}｜基线 ${BASE_N} 个文件｜等 ${EXPECT} 份新回执，最多 ${TIMEOUT}s"

DEADLINE=$(( $(date +%s) + TIMEOUT ))
GOT=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  for f in "$DIR"/*; do
    [ -f "$f" ] || continue
    if ! seen_has "$f"; then
      seen_add "$f"
      GOT=$((GOT + 1))
      echo "📥 新回执｜$(basename "$f")｜终态=$(terminal_state "$f")｜$f"
    fi
  done
  if [ "$EXPECT" -gt 0 ] && [ "$GOT" -ge "$EXPECT" ]; then
    echo "✅ 收够 $GOT/$EXPECT 份，监听收尾。"
    exit 0
  fi
  sleep "$INTERVAL"
done

if [ "$EXPECT" -gt 0 ]; then
  echo "⏰ 超时 ${TIMEOUT}s：只等到 $GOT/$EXPECT 份新回执（目录 ${DIR}）。" >&2
  exit 4
fi
echo "⏰ 到点收尾：共 $GOT 份新回执。"
exit 0
