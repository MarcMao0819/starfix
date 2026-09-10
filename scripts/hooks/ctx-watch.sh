#!/bin/bash
# ctx-watch.sh —— 盯本会话上下文用量，≥阈值时输出一行事件（给 Monitor 类工具或哨兵消费）。
# 指标源：状态栏捕获脚本写的 ${CTX_SNAPSHOT_DIR}/<session_id>.json（字段 context_window.used_percentage）。
# 用法：ctx-watch.sh <session_id> [阈值=95] [间隔秒=30]
# 输出：到阈值打印 "CTX95 <session_id> used=<n>% remaining=<m>% at=<time>" 并退出 0；文件缺失/字段缺失只等待不报。
SID="${1:?用法: ctx-watch.sh <session_id> [阈值] [间隔秒]}"; TH="${2:-95}"; IV="${3:-30}"
DIR="${CTX_SNAPSHOT_DIR:-$HOME/.claude/ctx-snapshot}"; F="$DIR/$SID.json"
while true; do
  if [ -f "$F" ]; then
    used=$(python3 -c "import json,sys;d=json.load(open('$F'));print(int(d.get('context_window',{}).get('used_percentage',-1)))" 2>/dev/null || echo -1)
    if [ "${used:-(-1)}" -ge "$TH" ] 2>/dev/null; then
      rem=$((100-used)); echo "CTX${TH} $SID used=${used}% remaining=${rem}% at=$(date '+%H:%M:%S')"; exit 0
    fi
  fi
  sleep "$IV"
done
