#!/usr/bin/env bash
# send-to-tmux.sh —— tmux 版两步投递（specs/delivery-terminal.md「复用器」通道的最小实现）
#
# 和 scripts/send-to-session.sh（iTerm2 参考实现）是同一套协议，只换了四个原语：
#   读输入行 = tmux capture-pane（只读光标所在那一行）
#   写文本   = tmux send-keys -l（字面写入，不带回车）
#   读回确认 = 再读一次光标行，里面必须停着我们自己的关键词
#   补回车   = tmux send-keys Enter
#
# 为什么只读「光标所在那一行」而不是整屏：整屏 grep 会把历史输出里出现过的同名
# 关键词当成「写进去了」，那是假绿。光标行就是对方的输入行，写没写进去它最诚实。
#
# 为什么要读回：**落盘≠送到**。盲补回车会把对方正在打的半截内容强行提交出去，
# 所以补回车前必须确认输入行里停的是我们自己的关键词——宁可不投，不可误发。
#
# 用法：
#   send-to-tmux.sh [--dry-run] <pane> "<正文>" <关键词>
#
#   <pane>    tmux 的 target-pane 写法（%3、或 会话:窗口.窗格）。
#             **从 tmux list-panes 的输出复制，不要手写**——错的 pane 在很多 tmux
#             版本上表现为「静默投到别处/什么都没发生」，而不是报错。
#   <正文>    单行。多行正文里的换行会被 send-keys 当回车提交，直接拒绝。
#   <关键词>  必须是 <正文> 的字面子串；不是子串直接 KW_NOT_IN_MSG_ABORT
#             （不在正文 = 读回永不命中 = 同一单反复排队，真实事故）。
#
# 环境变量（都有默认值，清单见 quickstart/README.md）：
#   QS_TMUX_SOCKET   tmux -L 用的 socket 名；不设则用 tmux 默认 socket
#   QS_IDLE_RE       判「输入行空闲」的 ERE，默认匹配常见提示符结尾
#   QS_IDLE_TIMEOUT  等对方空闲的秒数上限，默认 30
#
# 退出码：0=已送达　1=写进去了但没确认提交　2=对方输入行忙　3=参数/pane 不合法
set -euo pipefail
shopt -s nullglob

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
if [ "$#" -ne 3 ]; then
  echo "用法：send-to-tmux.sh [--dry-run] <pane> \"<正文>\" <关键词>" >&2
  echo "  pane 从 tmux list-panes -a -F '#{pane_id} #{session_name}' 里复制" >&2
  exit 3
fi
PANE="$1"; MSG="$2"; KW="$3"

IDLE_TIMEOUT="${QS_IDLE_TIMEOUT:-30}"
IDLE_RE="${QS_IDLE_RE:-}"
if [ -z "$IDLE_RE" ]; then IDLE_RE='(❯|›|»|\$|#|>)[[:space:]]*$'; fi

TMUX_CMD=(tmux)
if [ -n "${QS_TMUX_SOCKET:-}" ]; then TMUX_CMD=(tmux -L "$QS_TMUX_SOCKET"); fi

# ⓪ 纯参数判据先过：不碰任何终端就能判的错，绝不留到碰了终端之后再判。
case "$MSG" in *"$KW"*) KW_OK=1;; *) KW_OK=0;; esac
case "$MSG" in *$'\n'*) MULTILINE=1;; *) MULTILINE=0;; esac

if [ "$DRY" = "1" ]; then
  echo "[dry-run] 目标 pane : $PANE"
  echo "[dry-run] 关键词   : $KW"
  if [ "$KW_OK" = 1 ]; then
    echo "[dry-run] 关键词是正文子串 : 是（读回确认可用）"
  else
    echo "[dry-run] 关键词是正文子串 : 否 → 实投会 KW_NOT_IN_MSG_ABORT"
  fi
  echo "[dry-run] 正文是单行 : $([ "$MULTILINE" = 0 ] && echo 是 || echo "否 → 实投会 MSG_MULTILINE_ABORT")"
  echo "[dry-run] 将执行四步："
  echo "[dry-run]   ① capture-pane 读光标行，必须空闲（最多等 ${IDLE_TIMEOUT}s；非空=对方在打字，放弃）"
  echo "[dry-run]   ② send-keys -l «正文»            —— 只写入，不提交"
  echo "[dry-run]   ③ capture-pane 读回，光标行含本条关键词才继续"
  echo "[dry-run]   ④ send-keys Enter 提交；读回不到关键词则不补回车"
  echo "[dry-run] 正文首行 : $(printf '%s' "$MSG" | head -1 | cut -c1-60)"
  echo "[dry-run] 未触碰任何终端。"
  if [ "$KW_OK" != 1 ]; then echo "KW_NOT_IN_MSG_ABORT"; exit 3; fi
  if [ "$MULTILINE" = 1 ]; then echo "MSG_MULTILINE_ABORT"; exit 3; fi
  exit 0
fi

if [ "$KW_OK" != 1 ]; then
  echo "KW_NOT_IN_MSG_ABORT 关键词「${KW}」不是正文的字面子串；读回确认永远不会命中，拒绝投递。"
  exit 3
fi
if [ "$MULTILINE" = 1 ]; then
  echo "MSG_MULTILINE_ABORT 正文含换行；send-keys 会把换行当回车提前提交，拒绝投递。"
  exit 3
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "环境缺 tmux：本脚本靠 tmux capture-pane / send-keys 工作。装完再跑。" >&2
  exit 3
fi

# ⓪ pane 存在性：先解析成 pane_id，再回列表里核一遍。
# 只信 display-message 不够——目标写错时有的 tmux 版本会回退到「当前 pane」，
# 那就是静默空投；回列表里核一遍才能把「不存在」和「忙」分开报。
PID=$("${TMUX_CMD[@]}" display-message -p -t "$PANE" '#{pane_id}' 2>/dev/null || true)
if [ -z "$PID" ] || ! "${TMUX_CMD[@]}" list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx -- "$PID"; then
  echo "PANE_NOT_FOUND pane=${PANE}（从 tmux list-panes -a -F '#{pane_id} #{session_name}' 复制，别手写）"
  exit 3
fi

cursor_line() {
  local cy
  cy=$("${TMUX_CMD[@]}" display-message -p -t "$PID" '#{cursor_y}' 2>/dev/null || true)
  if [ -z "$cy" ]; then echo ""; return 0; fi
  "${TMUX_CMD[@]}" capture-pane -p -t "$PID" -S "$cy" -E "$cy" 2>/dev/null || true
}
is_idle() { printf '%s' "$1" | grep -qE "$IDLE_RE"; }

# ① 空闲检查（双采样）：两次采样一致且都像空提示符，才算对方没在打字。
# 单次采样会在对方正好刷屏的那一瞬间判成空闲。
DEADLINE=$(( $(date +%s) + IDLE_TIMEOUT ))
IDLE=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  A=$(cursor_line); sleep 1; B=$(cursor_line)
  if [ "$A" = "$B" ] && is_idle "$A"; then IDLE=1; break; fi
done
if [ "$IDLE" != 1 ]; then
  echo "COMPOSER_BUSY_ABORT pane=$PID 输入行 ${IDLE_TIMEOUT}s 内没空闲下来（当前：$(cursor_line)）"
  exit 2
fi

# ② 只写入，不提交
"${TMUX_CMD[@]}" send-keys -t "$PID" -l -- "$MSG"

# ③ 读回确认：输入行里停的必须是我们自己的关键词
sleep 1
LINE=$(cursor_line)
case "$LINE" in
  *"$KW"*) ;;
  *) echo "KW_MISMATCH_ABORT 输入行里不是本条关键词，不补回车（宁可不投，不可误发）：$LINE"; exit 1;;
esac

# ④ 补回车提交
"${TMUX_CMD[@]}" send-keys -t "$PID" Enter

# ⑤ 提交后读屏：关键词离开输入行=已提交。还停着就是没吃这一下回车。
for i in 1 2 3; do
  sleep 1
  LINE=$(cursor_line)
  case "$LINE" in
    *"$KW"*) ;;
    *) echo "DELIVERED pane=$PID attempt=$i"; exit 0;;
  esac
done
echo "UNCONFIRMED_NO_BLIND_CR pane=$PID 关键词仍停在输入行；不盲补回车，请人工读屏后再决定。"
exit 1
