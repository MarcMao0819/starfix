#!/usr/bin/env bash
# scrub-gate.sh —— 开源仓敏感内容门禁。命中任一即 exit 1 并打印 文件:行。
#
# 为什么要有这道门：脱敏是一次性动作，门禁是持续判据。没有门，下一次往仓里
# 加一个文件就可能把内网 IP、真人名、绝对路径重新带进来，而 code review 看不出来。
#
# 两类判据：
#   ① 结构型——正则能直接认出来的（IM 标识、内网 IP、家目录、会话 UUID、日期、代号）。
#   ② 词表型——认不出形状、只能靠清单的（真人名、公司名、产品名）。
#      词表存 **HMAC-SHA256(盐, 词) 前 16 位**，盐与词表都**不入仓**。
#
#      为什么不是裸 sha1：短字符串的裸哈希可被字典还原。实测用 22 万条常见姓名/
#      地名候选，几秒就从旧的 sha1 词表 37 条里还原出 13 条（9 个人名 + 4 个地名）。
#      「哈希了所以能公开」对**可枚举的短明文**不成立——判断一个派生值能否公开，
#      要问的是"原文空间有多大"，不是"用没用单向函数"。
#
#      没有词表也能用：缺文件时只跑结构型判据并明确提示，退出码按结构型结果。
#      外部贡献者因此仍有全部结构判据可用，只是少了人名/公司名那一层。
#
# 用 /usr/bin/grep 而不是交互 shell 里的 grep：后者可能是被包装过的函数，
# 与子进程行为不一致，做过假阴性（判"没命中"其实是没扫到）。
#
# 用法：
#   tools/scrub-gate.sh [仓根]                 默认仓根 = 本脚本的上级目录
#   SCRUB_TERMS=<词表路径>                     默认 $HOME/.starfix/scrub-terms.hmac
#   SCRUB_SALT=<盐>                            有词表时必须给；盐在词表同目录的 scrub-salt
#
#   export SCRUB_SALT="$(cat "$HOME/.starfix/scrub-salt")"
#   tools/scrub-gate.sh
#
# 退出码：0=干净  1=有命中  2=用不了（缺依赖/路径不对/有词表但没给盐）

set -uo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# 词表默认放本机中性目录，可用 SCRUB_TERMS 指到别处。
# ★ 默认路径刻意**不含任何项目名**：本脚本是门禁唯一不扫自己的文件
#   （下面 --exclude=scrub-gate.sh），路径里若写了敏感词，门禁反而看不见。
TERMS="${SCRUB_TERMS:-$HOME/.starfix/scrub-terms.hmac}"
GREP=/usr/bin/grep

[ -d "$ROOT" ] || { echo "仓根不存在：$ROOT"; exit 2; }
command -v python3 >/dev/null || { echo "需要 python3 做 HMAC 比对"; exit 2; }

hits=0

# ── ① 结构型判据 ─────────────────────────────────────────────────────
# 说明：模式写成 ERE。--binary-files=without-match 避免二进制里的巧合命中。
declare -a PATTERNS=(
  'ou_[0-9a-zA-Z]{20,}'
  'oc_[0-9a-zA-Z]{20,}'
  'cli_[0-9a-zA-Z]{6,}'
  '192\.168\.[0-9]+\.[0-9]+'
  '/Users/[a-z][a-z0-9_-]*'
  '[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}'
  '20[0-9]{2}-(0[1-9]|1[0-2])(-[0-9]{2})?'
  'CS-20[0-9]{6}-'
  '(^|[^A-Za-z0-9_])(W[0-9]+T[0-9]+|astra|terra)([^A-Za-z0-9_]|$)'
)
declare -a PATNAMES=(
  'IM 用户 open_id'
  'IM 群 chat_id'
  'IM 应用 cli_ID'
  '内网 IP'
  '绝对家目录'
  '会话 UUID'
  '绝对日期（使用周期）'
  '带日期的内部登记号'
  '舰员真实代号'
)

# 豁免：benchmarks/captain-v1/results/ 下的评测记录允许绝对日期（评测日期是公开元数据）
DATE_EXEMPT_RE="benchmarks/captain-v1/(results|report)/|docs/img/bench/|docs/bench/"
for i in "${!PATTERNS[@]}"; do
  # 排除门禁自身与词表样例：它们必须写得出这些模式才能干活
  out=$("$GREP" -REn --binary-files=without-match \
        --exclude-dir=.git --exclude=scrub-gate.sh --exclude='scrub-terms*' \
        "${PATTERNS[$i]}" "$ROOT" 2>/dev/null || true)
  if [ "${PATNAMES[$i]}" = "绝对日期（使用周期）" ] && [ -n "$out" ]; then
    out=$(printf '%s\n' "$out" | "$GREP" -Ev "^$ROOT/($DATE_EXEMPT_RE)" || true)
  fi
  if [ -n "$out" ]; then
    echo "★ 命中【${PATNAMES[$i]}】："
    printf '%s\n' "$out" | sed "s|^$ROOT/|  |"
    hits=$((hits + 1))
  fi
done

# ── ①b 端口字面判据 ──────────────────────────────────────────────────
# 代码里写死 5 位端口是"能跑但换台机器就错"的典型，且端口本身也算环境信息。
# 允许豁免：同一行写 `非端口` 说明它是时长/其它数值（如 21600 秒 = 6 小时）。
port_out=$("$GREP" -REn --binary-files=without-match \
  --include=*.py --include=*.sh --include=*.swift \
  --exclude-dir=.git --exclude=scrub-gate.sh \
  '\b(1[0-9]{4}|2[0-9]{4})\b' "$ROOT" 2>/dev/null | "$GREP" -v '非端口' || true)
if [ -n "$port_out" ]; then
  echo "★ 命中【代码里的 5 位端口字面】（应改成环境变量；确属时长请在同行标注「非端口」）："
  printf '%s\n' "$port_out" | sed "s|^$ROOT/|  |"
  hits=$((hits + 1))
fi

# ── ② 词表型判据（HMAC 比对；缺词表则降级为只跑结构型）──────────────────
TERMS_CHECKED=0
if [ ! -f "$TERMS" ]; then
  echo "词表缺失：只跑结构型判据（缺的是 ${TERMS}）"
  echo "  —— 人名/公司名那一层没查。要启用：设 SCRUB_TERMS 指向词表，并 export SCRUB_SALT。"
else
  if [ -z "${SCRUB_SALT:-}" ]; then
    echo "词表存在但没给盐（SCRUB_SALT）。词表是加盐 HMAC，没有盐没法比对。"
    echo "  export SCRUB_SALT=\"\$(cat \"\$HOME/.starfix/scrub-salt\")\"   # 盐不入仓"
    echo "  或者：unset/移走词表，门禁会降级为只跑结构型判据。"
    exit 2
  fi
  py_out=$(SCRUB_ROOT="$ROOT" SCRUB_TERMS_FILE="$TERMS" python3 "$(dirname "${BASH_SOURCE[0]}")/scrub_terms_check.py" || true)
  if [ -n "$py_out" ]; then
    echo "★ 命中【词表（真人名/公司名/产品名）】："
    printf '%s\n' "$py_out"
    hits=$((hits + 1))
  fi
  TERMS_CHECKED=1
fi

# ── 判定 ─────────────────────────────────────────────────────────────
if [ "$hits" -gt 0 ]; then
  echo
  echo "门禁 FAIL：$hits 类命中。仓内仍有未脱敏内容，不能公开。"
  exit 1
fi
STRUCT_N=$(( ${#PATTERNS[@]} + 1 ))
if [ "$TERMS_CHECKED" = "1" ]; then
  echo "门禁 PASS：结构型 $STRUCT_N 类（含端口字面）+ 词表 $("$GREP" -cvE '^#|^$' "$TERMS") 条，均无命中。"
else
  echo "门禁 PASS（降级）：结构型 $STRUCT_N 类无命中；词表未加载，人名/公司名那一层**没查**。"
fi
exit 0
