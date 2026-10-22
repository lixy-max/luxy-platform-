#!/usr/bin/env bash
# 停止工作台：前端 -> 后端 -> vLLM 底座（按需）
# 用法：
#   bash scripts/stop.sh            # 停前端+后端，询问式跳过底座（默认保留底座）
#   bash scripts/stop.sh --all      # 连 vLLM 底座一起停
#   bash scripts/stop.sh --dry-run  # 只显示会停哪些进程，不动手（安全自检用）
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
VLLM_PORT="${VLLM_PORT:-8000}"
STOP_VLLM=0; DRY=0
for a in "$@"; do
  case "$a" in
    --all) STOP_VLLM=1 ;;
    --dry-run) DRY=1 ;;
  esac
done

say() { printf '  %s\n' "$1"; }
killp() {   # killp <匹配模式> <说明>
  local pat="$1" desc="$2" pids
  # 排除自身：pgrep -f 的模式字符串也出现在本脚本自己的命令行里，会匹配到自己
  me=$$
  pids=$(pgrep -f "$pat" 2>/dev/null | while read -r q; do [ "$q" != "$me" ] && echo "$q"; done || true)
  if [ -z "$pids" ]; then say "· $desc 未在运行"; return; fi
  if [ "$DRY" = "1" ]; then
    say "· 将停止 $desc（pid: $(echo "$pids" | tr '\n' ' ')）"
  else
    kill $pids 2>/dev/null
    for i in $(seq 1 10); do sleep 1; pgrep -f "$pat" >/dev/null 2>&1 || break; done
    if pgrep -f "$pat" >/dev/null 2>&1; then
      pkill -9 -f "$pat" 2>/dev/null; say "· $desc 已强制停止"
    else
      say "· $desc 已停止"
    fi
  fi
}

echo "=================================================="
echo " 论文分析工作台 · 停止$([ "$DRY" = "1" ] && echo '（演练模式，不会真的停）')"
echo "=================================================="
killp "http.server $FRONTEND_PORT" "前端（:$FRONTEND_PORT）"
killp "python lora_manager.py" "后端（:$BACKEND_PORT）"
if [ "$STOP_VLLM" = "1" ]; then
  killp "vllm.entrypoints" "推理底座 vLLM（:$VLLM_PORT）"
  killp "api_server" "推理底座 vLLM（备用匹配）"
else
  say "· 推理底座保留（要一起停用：bash scripts/stop.sh --all）"
fi
echo
echo "  再次启动：bash scripts/start.sh"
echo "=================================================="
