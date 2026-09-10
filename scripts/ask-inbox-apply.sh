#!/bin/bash
# ⑰ 请示收件箱机械落库：每行 {"qid","answer",...} → task-activator.py ask answer；输出一行通知给舰长
: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"
SCRIPTS_DIR="${FLEET_SCRIPTS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
INBOX="${FLEET_ASK_INBOX:-$FLEET_HOME/ask-inbox.jsonl}"
ACT="${SCRIPTS_DIR}/task-activator.py"
[ -f "$ACT" ] || { echo "找不到 task-activator.py：${ACT}（设 FLEET_SCRIPTS_DIR 指向 scripts/ 目录）"; exit 2; }
export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 PYTHONIOENCODING=utf-8
tail -n0 -F "${INBOX}" 2>/dev/null | while IFS= read -r line; do
  qid=$(printf '%s' "$line" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("qid",""))' 2>/dev/null)
  ans=$(printf '%s' "$line" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("answer",""))' 2>/dev/null)
  [ -z "$qid" ] && { echo "📥 收件箱坏行：$line"; continue; }
  out=$(python3 "${ACT}" ask answer "$qid" --answer "$ans" 2>&1 | tail -1)
  python3 "${ACT}" ask board >/dev/null 2>&1
  printf '%s' "$line" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("📥 浮窗答复已落库｜%s=%s"%(d.get("qid"),d.get("answer")))'
  echo "   ↳ $out"
done
