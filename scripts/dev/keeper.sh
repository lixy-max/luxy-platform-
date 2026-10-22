#!/usr/bin/env bash
# ============================================================================
# 工作台 keeper —— 两个作用:
#   ① 【占住 WSL 会话】: WSL2 在最后一个会话结束时会把发行版连同服务一起回收
#      （实测: 双击启动器 → 汇总 200/200 → 一两分钟后三端口全 000，dmesg 见 EXT4 unmount/mount）
#   ② 【看护服务】: 后端/前端/底座哪个不在就拉起（开机或崩溃后自动恢复）
#
# 用法: bash scripts/keeper.sh            # 前台常驻（Ctrl-C 退出）
#       nohup bash scripts/keeper.sh &    # 后台
# 注意: 这个脚本本身会一直循环 ⇒ 只要它在，WSL 就不会被回收 ✓
# ============================================================================
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
LOG="${KEEPER_LOG:-/tmp/workbench_keeper.log}"
PY_BIN="${PYBIN:-/home/luxy/.venv/bin/python}"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >/dev/null; }

code() {
  local c
  c=$(curl -s -o /dev/null -w '%{http_code}' -m "${2:-3}" "$1" 2>/dev/null)
  [ -n "$c" ] || c=000
  printf '%s' "$c"
}

log "keeper 启动（pid $$）—— 本进程常驻即可让 WSL 不被回收"

n=0
while true; do
  n=$((n + 1))
  B=$(code "http://127.0.0.1:8001/health" 3)
  F=$(code "http://127.0.0.1:8080/index3.html" 3)
  V=$(code "http://127.0.0.1:8000/v1/models" 4)
  # ★ 2026-10-19: sidecar 由【Windows 计划任务 DSH-BrowserSidecar】看护 ✓
  #   （WSL interop 在此机器不可用 ✗ 无法从这里 powershell 拉起 ✗ 只提示不刷日志 ✓）
  S=$(code "http://127.0.0.1:9223/health" 3)
  if [ "$S" != "200" ] && [ $((n % 30)) -eq 0 ]; then
    log "提示: 浏览器 sidecar 未就绪（9223）→ Windows 侧计划任务 DSH-BrowserSidecar 应负责 ✓ 或手动 node server.mjs"
  fi

  if [ "$B" != "200" ] || [ "$F" != "200" ]; then
    log "服务不全（后端 $B / 前端 $F）→ 调用 start.sh 拉起"
    bash scripts/start.sh >> "$LOG" 2>&1
  elif [ "$V" != "200" ]; then
    # ★ 2026-10-19: 不再自动拉起 vLLM（用户反馈显存被吃满 ✗）
    #   原因: Windows 图形应用(Edge/Steam 等)常占 8~9GB 显存 ✗ 再自动拉 13.9GB 的底座 ⇒ 显存爆满 ✗
    #   改为只提示 ✓ 需要时由用户手动启动（双击启动器 / POST /vllm/start ✓）
    log "底座未运行（显存留给用户 ✓）⇒ 不自动拉起 vLLM ✗（需要时手动启动）"
  else
    [ $((n % 20)) -eq 0 ] && log "健康检查第 $n 轮：后端 $B / 前端 $F / 底座 $V ✓"
  fi
  sleep 20
done
