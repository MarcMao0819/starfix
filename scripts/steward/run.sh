#!/bin/bash
# 上下文管家外层看护：内层进程退出后等待数秒再拉起；标准输出写入日志。
: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${STEWARD_DATA_DIR:-$FLEET_HOME/steward/data}"
OUT="$DATA/run.out.log"
mkdir -p "$DATA"
export CTXSTEWARD2_LIVE=1
trap 'echo "$(date "+%F %T") run.sh: stopped" >>"$OUT"; exit 0' INT TERM
while true; do
  echo "$(date '+%F %T') run.sh: start steward2 $*" >>"$OUT"
  /usr/bin/python3 -B "$DIR/steward2.py" --loop "$@" >>"$OUT" 2>&1
  rc=$?
  echo "$(date '+%F %T') run.sh: steward2 exited rc=$rc" >>"$OUT"
  [ "$rc" -eq 9 ] && { echo "已有一个管家在跑，run.sh 退出。"; exit 9; }
  sleep 5
done