#!/usr/bin/env bash
# Luxy 平台 · 停止
#
# ★ 安全约定（2026-10-22 修）：
#   多实例是宣称支持的能力 ⇒ 停止**必须显式指定端口**，不再用宽泛 pkill 收网 ✗
#   否则从副本目录执行会误杀主实例（实测踩中）
#
# 用法:
#   BACKEND_PORT=8001 WEB_PORT=8080 bash install/stop.sh     # 停指定端口（推荐）
#   bash install/stop.sh --all                               # 停本机所有 luxy 实例（需二次确认）
#   bash install/stop.sh                                     # 交互确认后停默认 8001/8080
set -u

die() { echo "[luxy] $*" >&2; exit 2; }

MODE="${1:-}"
BACKEND_PORT="${BACKEND_PORT:-}"
WEB_PORT="${WEB_PORT:-}"

# ── --all：显式要求全部停止 ────────────────────────────────
if [ "$MODE" = "--all" ]; then
  echo "[luxy] ⚠ --all 会停止本机**所有** luxy 后端/前端进程"
  printf "[luxy] 确认继续？输入 yes: "
  read -r ans
  [ "$ans" = "yes" ] || die "已取消"
  echo "[luxy] 停止全部 luxy 实例…"
  pkill -f 'paper_lora_project/lora_manager.py' 2>/dev/null
  pkill -f 'luxy-v4[0-9]*-run/lora_manager.py' 2>/dev/null
  pkill -f 'luxy-v4[0-9]*-run/webui/serve.py' 2>/dev/null
  sleep 2
  pkill -9 -f 'paper_lora_project/lora_manager.py' 2>/dev/null
  pkill -9 -f 'luxy-v4[0-9]*-run/lora_manager.py' 2>/dev/null
  echo "[luxy] 完成（未触碰 vLLM）"
  exit 0
fi

# ── 无端口参数：要求确认（默认端口只是提示，不直接干）──────
if [ -z "$BACKEND_PORT" ] && [ -z "$WEB_PORT" ]; then
  echo "[luxy] ⚠ 未指定 BACKEND_PORT / WEB_PORT"
  echo "[luxy]   多实例环境下直接停默认端口可能误杀主实例"
  echo "[luxy]   推荐: BACKEND_PORT=8005 WEB_PORT=8085 bash install/stop.sh"
  printf "[luxy] 仍要停默认 8001/8080？输入 yes: "
  read -r ans
  [ "$ans" = "yes" ] || die "已取消（未停止任何进程）"
  BACKEND_PORT=8001
  WEB_PORT=8080
fi

kill_by_port() {
  local port="$1" name="$2"
  local pids
  pids=$(ss -lntp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | sort -u)
  if [ -z "$pids" ]; then
    echo "[luxy] $name 未在 :$port 监听"
    return 1
  fi
  # 只杀**确认属于 luxy** 的进程（命令行含 lora_manager.py 或 webui/serve.py）
  local killed=""
  for p in $pids; do
    cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
    case "$cmd" in
      *lora_manager.py*|*webui/serve.py*|*"serve.py"*)
        kill -TERM "$p" 2>/dev/null && killed="$killed $p"
        ;;
      *)
        echo "[luxy] ⚠ :$port 的 pid $p 不像 luxy 进程，跳过（$cmd）"
        ;;
    esac
  done
  [ -n "$killed" ] || { echo "[luxy] :$port 无 luxy 进程"; return 1; }
  sleep 2
  for p in $killed; do kill -9 "$p" 2>/dev/null; done
  echo "[luxy] $name 已停（:$port，pid$killed）"
  return 0
}

RC=0
[ -n "${BACKEND_PORT:-}" ] && { kill_by_port "$BACKEND_PORT" "后端" || RC=1; }
[ -n "${WEB_PORT:-}" ] && { kill_by_port "$WEB_PORT" "前端" || RC=1; }

echo "[luxy] 提示: vLLM 由平台内部管理（POST /vllm/stop）；强停: pkill -f vllm.entrypoints"
exit $RC
