#!/bin/bash
# handoff-snapshot.sh —— 不经模型、纯机械地生成交接快照（舰长写不及时的兜底；PreCompact hook 与 CTX95 事件都调用它）。
# 输出到 ${FLEET_HANDOFF:-$FLEET_HOME/HANDOFF-latest.md} 的「机械快照」段；模型写的「当前判断」段放在同一文件顶部，本脚本只覆盖分隔线以下。
set -u
: "${FLEET_HOME:?缺少环境变量 FLEET_HOME}"
OUT="${FLEET_HANDOFF:-$FLEET_HOME/HANDOFF-latest.md}"
ACT="${ACTIVATOR_JSON:-$FLEET_HOME/task-activator.json}"
LEDGER="${FLEET_LEDGER:-$FLEET_HOME/台账.md}"
MON="${FLEET_SNAPSHOT_MONITORS:-$FLEET_HOME/monitors-latest.json}"
SCRIPTS="${FLEET_SCRIPTS_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SEP='<!-- ===== 以下为机械快照，脚本每次覆盖；以上为舰长手写判断 ===== -->'
tmp="$(mktemp)"
{
  if [ -f "$OUT" ] && /usr/bin/grep -q "$SEP" "$OUT"; then
    sed "/$SEP/q" "$OUT" | sed '$d'
  else
    printf '# HANDOFF · 舰长交接（手写段）\n\n- 当前判断：<舰长在 CTX95 事件后填写>\n- 下一步：<…>\n- 风险与备份：<…>\n\n'
  fi
  echo "$SEP"
  echo "生成时间：$(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "## 任务激活器"
  if [ -f "$ACT" ]; then ACTIVATOR_JSON="$ACT" python3 "$SCRIPTS/task-activator.py" list 2>/dev/null || echo "(激活器 list 失败)"; else echo "(无激活器文件 $ACT)"; fi
  echo
  echo "## 待答决策"
  if [ -f "$ACT" ]; then ACTIVATOR_JSON="$ACT" python3 "$SCRIPTS/task-activator.py" ask list 2>/dev/null | /usr/bin/grep -v '已答' || echo "(无)"; fi
  echo
  echo "## 台账最近 30 行"
  [ -f "$LEDGER" ] && tail -n 30 "$LEDGER" || echo "(无台账 $LEDGER)"
  echo
  echo "## 监听清单（重挂用）"
  if [ -f "$MON" ]; then python3 -c "
import json;d=json.load(open('$MON'))
for k,v in (d.items() if isinstance(d,dict) else enumerate(d)): print('-',str(k)[:80])" 2>/dev/null; else echo "(无 $MON)"; fi
} > "$tmp" && mv "$tmp" "$OUT" && echo "HANDOFF 写入 $OUT ($(wc -l < "$OUT") 行)"
