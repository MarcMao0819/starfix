#!/bin/bash
# terminal-probe.sh —— 舰长上任第一问：我在什么终端里、能用哪些通道碰到其他 agent。
# 只读探测，不写任何文件；把输出贴进会话清单/快照。
echo "== 我在哪"
echo "TERM_PROGRAM=${TERM_PROGRAM:-unknown} TERM=${TERM:-unknown}"
[ -n "${TMUX:-}" ] && echo "tmux: 是（$TMUX）" || echo "tmux: 否"
[ -n "${STY:-}" ] && echo "screen: 是（$STY）" || echo "screen: 否"
[ -n "${SSH_CONNECTION:-}" ] && echo "远程 ssh 会话: 是" || echo "远程 ssh 会话: 否"
echo "== 可用通道"
command -v osascript >/dev/null 2>&1 && echo "osascript: 有（可驱动 iTerm2/Terminal AppleScript）" || echo "osascript: 无"
command -v tmux >/dev/null 2>&1 && echo "tmux: 有（send-keys/capture-pane 可用）" || echo "tmux: 无"
command -v wezterm >/dev/null 2>&1 && echo "wezterm cli: 有" || echo "wezterm cli: 无"
if [ -d "$HOME/Library/Application Support/iTerm2" ]; then echo "iTerm2: 已安装（Python API 需在偏好设置开启）"; else echo "iTerm2: 未见"; fi
[ -d /tmp/cc-socks ] && echo "同机 agent 总线: 见 /tmp/cc-socks（Claude 会话可用 SendMessage）" || echo "同机 agent 总线: 未见"
echo "== 建议"
echo "1) Claude↔Claude：总线；2) CLI agent 在图形终端：终端自动化 API 两步投递；3) 在 tmux 里：send-keys + capture-pane 两步；4) 都没有：文件信箱。"
echo "每个舰员一条推荐通道写进会话清单；本脚本不落盘。"
