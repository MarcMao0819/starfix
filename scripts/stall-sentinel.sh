#!/bin/bash
# 停工哨兵（主窗建）：在飞批次静默超过阈值即报警。
# 主名单由 sentinel-roster.py 从任务激活器只读生成；退休的在飞清单若仍存在则只作可选追加。
# 有动静=worktree 新提交 或 工作区文件改动 或 回执更新。静默>60分钟报一次，之后每60分钟重复。
# 修（代舵舰员）：新条目曾按目录旧基线起算，派单几分钟即误报（当日三次）。
# 现引入首见时间 .stall-seen：静默基线 = max(最后活动, 本条目首次出现时刻)，新条目自派单起算。
D="$(cd "$(dirname "$0")" && pwd)"
LEGACY_LIST="${STALL_LEGACY_LIST:-${D}/在飞清单.txt}"
STATE="${STALL_STATE:-${D}/.stall-state}"
SEEN="${STALL_SEEN:-${D}/.stall-seen}"
THRESH=3600
ONCE=0

case "${1:-}" in
  '') ;;
  --once) ONCE=1 ;;
  *) echo "用法：$0 [--once]" >&2; exit 2 ;;
esac

touch "${STATE}" "${SEEN}"

run_round() {
  local roster now
  roster=$(/usr/bin/mktemp "${TMPDIR:-/tmp}/stall-roster.XXXXXX") || {
    echo "⛔停工哨兵：无法创建名单临时文件" >&2
    return 2
  }
  if ! python3 "${D}/sentinel-roster.py" > "${roster}"; then
    rm -f "${roster}"
    echo "⛔停工哨兵：任务激活器名单读取失败" >&2
    return 2
  fi
  # 兼容未退休的旧清单；名称（首列）相同的只保留 roster 的第一条。
  if [ -s "${LEGACY_LIST}" ]; then
    cat "${LEGACY_LIST}" >> "${roster}"
  fi
  now=$(date +%s)
  /usr/bin/awk -F'|' '
    /^[[:space:]]*($|#)/ { next }
    NF < 2 { next }
    !seen[$1]++ { print }
  ' "${roster}" | while IFS='|' read -r name wt receipt; do
    # 无路径的畸形行（缺 | 分隔）同样跳过，避免进算术。
    [ -z "${wt}" ] && [ -z "${receipt}" ] && continue
    latest=0
    if [ -d "${wt}/.git" ] || [ -f "${wt}/.git" ]; then
      c=$(git -C "${wt}" log -1 --format=%ct 2>/dev/null); [ -n "${c}" ] && [ "${c}" -gt "${latest}" ] && latest=${c}
    fi
    # 活动信号源可以是普通目录（如互审证据目录，只读作业不动 worktree）：按最新文件 mtime 计。
    if [ -d "${wt}" ]; then
      m=$(find "${wt}" -type f -not -path "*/.git/*" -exec stat -f %m {} + 2>/dev/null | /usr/bin/sort -rn | head -1)
      [ -n "${m}" ] && [ "${m}" -gt "${latest}" ] && latest=${m}
    fi
    if [ -f "${receipt}" ]; then
      r=$(stat -f %m "${receipt}" 2>/dev/null); [ -n "${r}" ] && [ "${r}" -gt "${latest}" ] && latest=${r}
    fi
    # 首见时间：新条目自本刻起算，避免拿目录旧基线误报。
    first_seen=$(/usr/bin/grep "^${name}=" "${SEEN}" | cut -d= -f2)
    if [ -z "${first_seen}" ]; then
      echo "${name}=${now}" >> "${SEEN}"
      first_seen=${now}
    fi
    [ "${latest}" -eq 0 ] && latest=${first_seen}
    [ "${first_seen}" -gt "${latest}" ] && latest=${first_seen}
    age=$(( now - latest ))
    if [ "${age}" -gt "${THRESH}" ]; then
      last_alert=$(/usr/bin/grep "^${name}=" "${STATE}" | cut -d= -f2)
      last_alert=${last_alert:-0}
      if [ $(( now - last_alert )) -gt "${THRESH}" ]; then
        echo "⚠停工嫌疑【${name}】已静默 $(( age / 60 )) 分钟（无提交/无文件改动/回执未更新）"
        /usr/bin/grep -v "^${name}=" "${STATE}" > "${STATE}.tmp" 2>/dev/null; mv "${STATE}.tmp" "${STATE}"
        echo "${name}=${now}" >> "${STATE}"
      fi
    fi
  done
  rm -f "${roster}"
}

while true; do
  run_round || exit $?
  [ "${ONCE}" -eq 1 ] && exit 0
  sleep 600
done
