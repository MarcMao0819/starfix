#!/usr/bin/env bash
# fake-crew.sh —— 一个假舰员，用来在没有真实 AI 会话的机器上跑通闭环。
#
# 它替代的是正式版里的一个终端 AI 会话（Codex CLI / Claude Code / 无头工人机）：
# 在 tmux pane 里等一行输入 → 干活 → 按 templates/02 把回执落盘 → 回到等待。
# 真实舰员和它的唯一区别是「干活」那一步是模型在做，其余契约完全一样：
#   收一行任务书路径 → 从任务书里取回执路径与关键词 → 任何终态都落盘 → 回到空闲提示符。
#
# 用法：fake-crew.sh          （通常由 tmux 起，见 demo.sh）
#
# 环境变量：
#   FLEET_HOME              必填，无默认值
#   FLEET_RECEIPT_DIR       回执目录，默认 $FLEET_HOME/receipts
#   QS_CREW_NAME            舰员代号，默认 crew-a1
#   QS_CREW_PROMPT          提示符，默认 "crew> "（send-to-tmux 的空闲判据认它）
#   QS_CREW_WORK_SECONDS    模拟承建耗时，默认 2
set -euo pipefail
shopt -s nullglob

: "${FLEET_HOME:?缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md}"
RECEIPT_DIR="${FLEET_RECEIPT_DIR:-$FLEET_HOME/receipts}"
CREW="${QS_CREW_NAME:-crew-a1}"
PROMPT="${QS_CREW_PROMPT:-crew> }"
WORK_SECONDS="${QS_CREW_WORK_SECONDS:-2}"

mkdir -p "$RECEIPT_DIR"

echo "🧑‍🚀 假舰员 $CREW 上岗。回执目录：$RECEIPT_DIR"
echo "   等一行任务书绝对路径；输入 quit 下班。"

write_receipt() {
  local book="$1" receipt="$2" kw="$3"
  local ts; ts=$(date '+%F %H:%M')
  cat > "$receipt" <<RECEIPT_EOF
# $(basename "$book" .md) 回执（关键词 \`$kw\`）

## ★ 终态：PASS

按任务书执行完毕；本回执由 quickstart 的假舰员生成，用于演示闭环，不代表真实施工。

| 项 | 结果 |
|---|---|
| 舰员 | $CREW |
| 任务书 | $book |
| 落盘时间 | $ts |
| 门禁 | 未跑（演示件） |
| 红线 | 只写了 \$FLEET_HOME 下的文件，未碰任何其它路径 |

## 一、改了什么

演示件：没有改任何代码，只把这份回执写到 \`$receipt\`。

## 二、判据的判别力

| # | 变异 | 该红的红了 | 证据 |
|---|---|:---:|---|
| ① | 把关键词从投递正文里拿掉 | ✅ send-to-tmux 直接 KW_NOT_IN_MSG_ABORT | 见 quickstart/README.md 反例一节 |

## 三、实测证据

收到任务书路径：\`$book\`，模拟承建 ${WORK_SECONDS}s 后落盘。

## 四、残留风险

假舰员不读任务书正文、不校验白名单；换成真实舰员时这两条必须自己做。
RECEIPT_EOF
}

while true; do
  printf '%s' "$PROMPT"
  if ! IFS= read -r line; then echo; break; fi
  line="${line%$'\r'}"
  case "$line" in
    '' ) continue;;
    quit|exit ) echo "🧑‍🚀 $CREW 下班。"; break;;
  esac

  book="$line"
  if [ ! -f "$book" ]; then
    echo "⚠ 找不到任务书：${book}（收到的是这一行；真实舰员此时应落一份 BLOCKED 回执）"
    continue
  fi

  echo "📄 收到任务书：$book"
  echo "⏳ 模拟承建 ${WORK_SECONDS}s ..."
  sleep "$WORK_SECONDS"

  # 回执路径与关键词从任务书里取（正式版同理：收尾条款逐字带这两项）
  kw=$(sed -n 's/^关键词[：:][[:space:]]*//p' "$book" | head -1)
  receipt=$(sed -n 's/^回执[：:][[:space:]]*//p' "$book" | head -1)
  base=$(basename "$book" .md); base="${base%-任务书}"
  kw="${kw:-$base}"
  receipt="${receipt:-$RECEIPT_DIR/$base.md}"

  mkdir -p "$(dirname "$receipt")"
  write_receipt "$book" "$receipt" "$kw"
  echo "✅ 回执已落盘（终态 PASS，关键词 ${kw}）：$receipt"
done
