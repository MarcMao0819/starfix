#!/usr/bin/env bash
# demo.sh —— 10 分钟闭环演示：派单 → 投递 → 承建 → 回执 → 验收 → 请示 → 答复 → 解锁。
#
# 跑完你会看见一整条链路，全部落在一个临时目录里，跑完自动清干净：
#   任务激活器（scripts/task-activator.py）      —— 任务与决策的唯一真相表
#   两步投递（quickstart/send-to-tmux.sh）       —— 往别人的输入行写东西的安全协议
#   假舰员（quickstart/fake-crew.sh）            —— 站真实 Codex/Claude 会话的位置
#   回执监听（quickstart/receipt-watch.sh）      —— 事件送到舰长面前，不用翻目录
#   决策面板（quickstart/ask-cli.py）            —— 站请示浮窗的位置
#   收件箱落库（scripts/ask-inbox-apply.sh）     —— 答复变成任务解锁
#
# 用法：bash quickstart/demo.sh
# 前置：tmux、python3、bash；不需要 iTerm2、不需要网络、不需要任何账号。
#
# 环境变量：
#   QS_KEEP=1          跑完保留临时目录（默认删）
#   QS_DEMO_TMPDIR     临时目录建在哪，默认 $TMPDIR
#   QS_TMUX_SESSION / QS_TMUX_SOCKET   演示用的 tmux 会话名与 socket 名
#
# 注意：demo **总是自己建一个临时 FLEET_HOME**，不用你环境里的那个。
# 演示会往激活器里写测试任务，串进真实舰队的数据里就是污染。
set -euo pipefail
shopt -s nullglob

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPTS_DIR="$REPO/scripts"

SESSION="${QS_TMUX_SESSION:-starfix-quickstart}"
SOCK="${QS_TMUX_SOCKET:-starfix-quickstart}"
export QS_TMUX_SOCKET="$SOCK"

CODE="${QS_DEMO_CODE:-DEMO01}"
CODE2="${QS_DEMO_CODE2:-DEMO02}"
KW="CS-$CODE"
QID="${QS_DEMO_QID:-Q01}"

STEP=0
FLEET_HOME=""
WATCH_PID=""
APPLY_PID=""

step() { STEP=$((STEP + 1)); printf '\n✔ 步骤 %d：%s\n' "$STEP" "$*"; }
info() { printf '   %s\n' "$*"; }
fail() { printf '\n✘ 第 %d 步失败：%s\n' "$STEP" "$*" >&2; exit 1; }

kill_bg() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  pkill -P "$pid" >/dev/null 2>&1 || true   # 先杀子进程（tail -F 会活过父进程，然后一直盯着已删目录）
  kill "$pid" >/dev/null 2>&1 || true
  wait "$pid" 2>/dev/null || true           # 收尸，顺便挡掉 shell 的 "Terminated" 噪音
}

cleanup() {
  local rc=$?
  kill_bg "$APPLY_PID"
  kill_bg "$WATCH_PID"
  if command -v tmux >/dev/null 2>&1; then
    tmux -L "$SOCK" kill-session -t "$SESSION" >/dev/null 2>&1 || true
    tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true
  fi
  if [ -n "$FLEET_HOME" ] && [ "${QS_KEEP:-0}" != "1" ]; then
    case "$FLEET_HOME" in
      */starfix-qs.*) rm -rf "$FLEET_HOME";;
      *) printf '   （目录名不像 demo 建的，没敢删：%s）\n' "$FLEET_HOME";;
    esac
  fi
  exit "$rc"
}
trap cleanup EXIT

printf '═══ 牵星 StarFix · 最小闭环演示 ═══\n'

# ── 1 ─────────────────────────────────────────────────────────────────
step "前置检查"
for c in tmux python3; do
  command -v "$c" >/dev/null 2>&1 || fail "缺 ${c}。macOS: brew install ${c}；Debian/Ubuntu: apt install $c"
done
[ -f "$SCRIPTS_DIR/task-activator.py" ] || fail "找不到 $SCRIPTS_DIR/task-activator.py（demo 必须在仓库里跑）"
[ -f "$SCRIPTS_DIR/ask-inbox-apply.sh" ] || fail "找不到 $SCRIPTS_DIR/ask-inbox-apply.sh"
info "tmux $(tmux -V | awk '{print $2}')｜python3 $(python3 -c 'import platform;print(platform.python_version())')"

# ── 2 ─────────────────────────────────────────────────────────────────
step "建临时舰队目录（不碰你自己的 FLEET_HOME）"
TMPROOT="${QS_DEMO_TMPDIR:-${TMPDIR:-/tmp}}"; TMPROOT="${TMPROOT%/}"
FLEET_HOME="$(mktemp -d "$TMPROOT/starfix-qs.XXXXXX")"
export FLEET_HOME
export FLEET_RECEIPT_DIR="$FLEET_HOME/receipts"
export ACTIVATOR_JSON="$FLEET_HOME/task-activator.json"
export FLEET_ASK_INBOX="$FLEET_HOME/ask-inbox.jsonl"
export FLEET_DISPATCH_LOG="$FLEET_HOME/dispatch.log"
export FLEET_ASK_PANEL="$FLEET_HOME/请示台.md"
mkdir -p "$FLEET_RECEIPT_DIR"
: > "$FLEET_ASK_INBOX"
# scripts/ask-inbox-apply.sh 会 cd 到 FLEET_SCRIPTS_DIR，然后在**那个目录**里
# tail ask-inbox.jsonl、跑 task-activator.py。所以工作目录必须同时有这两样：
# 收件箱在 FLEET_HOME，就把激活器软链过来。
export FLEET_SCRIPTS_DIR="$FLEET_HOME"
ln -sf "$SCRIPTS_DIR/task-activator.py" "$FLEET_HOME/task-activator.py"
info "FLEET_HOME=$FLEET_HOME"

# ── 3 ─────────────────────────────────────────────────────────────────
step "起 tmux 会话，pane 里跑假舰员"
tmux -L "$SOCK" kill-session -t "$SESSION" >/dev/null 2>&1 || true
tmux -L "$SOCK" new-session -d -s "$SESSION" -x 240 -y 50 \
  "FLEET_HOME='$FLEET_HOME' FLEET_RECEIPT_DIR='$FLEET_RECEIPT_DIR' exec bash '$HERE/fake-crew.sh'"
PANE="$(tmux -L "$SOCK" list-panes -t "$SESSION" -F '#{pane_id}' | head -1)"
[ -n "$PANE" ] || fail "tmux 会话起来了但取不到 pane id"
info "session=${SESSION}（socket ${SOCK}）｜pane=${PANE}　← pane id 只从这里复制，不手写"

# ── 4 ─────────────────────────────────────────────────────────────────
step "写任务书，登记进激活器"
BOOK="$FLEET_HOME/CS-$CODE-任务书.md"
RECEIPT="$FLEET_RECEIPT_DIR/CS-$CODE.md"
cat > "$BOOK" <<BOOK_EOF
# CS-$CODE 演示单：把闭环跑通（关键词 \`$KW\`）

## 硬边界
- 只许写 \$FLEET_HOME 下的文件；其余一律只读。

## 要做的
1. 读这份任务书 → 验证：能取到下面的回执路径与关键词
2. 落一份回执 → 验证：文件存在且首屏有终态

## 终态
回执：$RECEIPT
关键词：$KW
三态（PASS/FAIL/BLOCKED）皆落盘；落盘后回到空闲提示符等下一单。
BOOK_EOF
python3 "$SCRIPTS_DIR/task-activator.py" add "$CODE" "演示单：跑通最小闭环" \
  --owner crew-a1 --book "$BOOK" --receipt "$RECEIPT" --status 施工中 | sed 's/^/   /' \
  || fail "登记 $CODE 失败（激活器有个钩子：没写 --owner 的单不许进施工中）"
python3 "$SCRIPTS_DIR/task-activator.py" add "$CODE2" "演示单：等指挥官答 $QID 才能开工" | sed 's/^/   /' \
  || fail "登记 $CODE2 失败"
info "任务书：$BOOK"

# ── 5 ─────────────────────────────────────────────────────────────────
step "挂回执监听（只报新落盘的，不报基线）"
"$HERE/receipt-watch.sh" --expect 1 --timeout 40 > "$FLEET_HOME/receipt-watch.log" 2>&1 &
WATCH_PID=$!
for _ in 1 2 3 4 5; do
  if grep -q '回执监听已挂' "$FLEET_HOME/receipt-watch.log" 2>/dev/null; then break; fi
  sleep 1
done
grep -q '回执监听已挂' "$FLEET_HOME/receipt-watch.log" 2>/dev/null || fail "回执监听没起来，见 $FLEET_HOME/receipt-watch.log"
sed 's/^/   /' "$FLEET_HOME/receipt-watch.log"

# ── 6 ─────────────────────────────────────────────────────────────────
step "两步投递：把任务书路径写进舰员的输入行"
info "关键词 $KW 是正文（任务书路径）的字面子串 → 读回确认才可能命中"
if ! "$HERE/send-to-tmux.sh" "$PANE" "$BOOK" "$KW" | sed 's/^/   /'; then
  fail "投递未确认；按协议宁可不投也不盲补回车，看上面的终态码"
fi

# ── 7 ─────────────────────────────────────────────────────────────────
step "等回执落盘（监听自己会收尾）"
if ! wait "$WATCH_PID"; then
  WATCH_PID=""
  sed 's/^/   /' "$FLEET_HOME/receipt-watch.log"
  fail "回执没在时限内落盘"
fi
WATCH_PID=""
sed 's/^/   /' "$FLEET_HOME/receipt-watch.log"
[ -f "$RECEIPT" ] || fail "监听说收到了，但 $RECEIPT 不存在——回执不是事实，实物才是"
info "回执首屏：$(awk '/终态/{print;exit}' "$RECEIPT")"

# ── 8 ─────────────────────────────────────────────────────────────────
step "验收：激活器判已完成（它会自己去读回执的终态）"
python3 "$SCRIPTS_DIR/task-activator.py" set "$CODE" 已完成 --note "闭环演示完成" | sed 's/^/   /' \
  || fail "激活器拒绝判完成（它会去读回执终态，PASS 才放行）"
# 先把输出落进变量再判：`python3 … | grep -q` 一命中就早退，python 吃到 SIGPIPE，
# 在 pipefail 下会把整条管道判成失败——断言反而成了假红的来源。
LIST_OUT="$(python3 "$SCRIPTS_DIR/task-activator.py" list)"
DONE_BLOCK="$(printf '%s\n' "$LIST_OUT" | awk '/^## 已完成/{f=1;next} /^## /{f=0} f')"
printf '%s\n' "$DONE_BLOCK" | grep -q -- "$CODE" || fail "$CODE 没落到「已完成」"

# ── 9 ─────────────────────────────────────────────────────────────────
step "提一道确认题给指挥官（答了就解锁 ${CODE2}）"
python3 "$SCRIPTS_DIR/task-activator.py" ask add "$QID" \
  "$CODE2 的回执要不要也走同一个监听？A=走同一个（推荐，少一套基建）／B=单独挂一个／C=先不做" \
  --tasks "$CODE2" --who Owner | sed 's/^/   /' \
  || fail "提问失败"
LINE2="$(python3 "$SCRIPTS_DIR/task-activator.py" list | grep -- "$CODE2" || true)"
printf '%s\n' "$LINE2" | grep -q '卡：' \
  || fail "提问后 $CODE2 应该被标成卡口径，但没有——这条判据不会随输入变化就等于没写"
info "此刻 ${CODE2}：$(printf '%s' "$LINE2" | sed 's/^ *//')"

# ── 10 ────────────────────────────────────────────────────────────────
step "挂收件箱落库（tail -n0 只认新行，必须先挂再答）"
bash "$SCRIPTS_DIR/ask-inbox-apply.sh" > "$FLEET_HOME/ask-apply.log" 2>&1 &
APPLY_PID=$!
sleep 2
kill -0 "$APPLY_PID" 2>/dev/null || fail "收件箱落库脚本没起来，见 $FLEET_HOME/ask-apply.log"
info "已挂：scripts/ask-inbox-apply.sh（原封不动复用，没改一行）"

# ── 11 ────────────────────────────────────────────────────────────────
step "决策面板：看未答的题（这是浮窗的命令行替身）"
python3 "$HERE/ask-cli.py" list | sed 's/^/   /' || fail "决策面板列不出未答题"

# ── 12 ────────────────────────────────────────────────────────────────
step "指挥官答复：只往收件箱追加一行"
python3 "$HERE/ask-cli.py" answer "$QID" A | sed 's/^/   /' || fail "答复没能追加进收件箱"
LINES=$(wc -l < "$FLEET_ASK_INBOX" | tr -d ' ')
[ "$LINES" = "1" ] || fail "收件箱应该只有 1 行，实际 $LINES 行（只追加被破坏了？）"
info "收件箱内容：$(cat "$FLEET_ASK_INBOX")"

# ── 13 ────────────────────────────────────────────────────────────────
step "答复落库 → 任务解锁"
UNLOCKED=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  LINE2="$(python3 "$SCRIPTS_DIR/task-activator.py" list | grep -- "$CODE2" || true)"
  # 判据两条同时成立：任务还在（不是被 grep 空吃掉了）＋ 那一行不再带「卡：」
  if [ -n "$LINE2" ] && ! printf '%s\n' "$LINE2" | grep -q '卡：'; then UNLOCKED=1; break; fi
  sleep 1
done
sed 's/^/   /' "$FLEET_HOME/ask-apply.log"
[ "$UNLOCKED" = "1" ] || fail "$CODE2 的 blocker 没被清掉；收件箱落库这一环断了"
info "解锁后：$(printf '%s' "$LINE2" | sed 's/^ *//')"

# ── 14 ────────────────────────────────────────────────────────────────
step "最终盘点：task-activator.py list"
python3 "$SCRIPTS_DIR/task-activator.py" list | sed 's/^/   /'

# ── 15 ────────────────────────────────────────────────────────────────
step "收工清理"
kill_bg "$APPLY_PID"; APPLY_PID=""
tmux -L "$SOCK" kill-session -t "$SESSION" >/dev/null 2>&1 || true
tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true
if tmux -L "$SOCK" has-session -t "$SESSION" 2>/dev/null; then
  fail "tmux 会话 $SESSION 还在，没清干净"
fi
info "tmux 无残留（socket $SOCK 上已无 ${SESSION}）"
if [ "${QS_KEEP:-0}" = "1" ]; then
  info "临时目录保留：$FLEET_HOME"
else
  info "临时目录将删除：${FLEET_HOME}（要留就 QS_KEEP=1 再跑）"
fi

printf '\n🎉 闭环跑通：派单 → 投递 → 承建 → 回执 → 验收 → 请示 → 答复 → 解锁。\n'
printf '   下一步看 quickstart/README.md：每个文件对应正式版哪个部件、怎么换成真实舰员。\n'
