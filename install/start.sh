#!/usr/bin/env bash
# Luxy 平台 · 一键启动（后端 + 前端）
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
BACKEND_PORT="${BACKEND_PORT:-8001}"
WEB_PORT="${WEB_PORT:-8080}"
cd "$HERE"
mkdir -p logs data loras training_data uploads chroma_db training_output
echo "[luxy] 启动后端 :$BACKEND_PORT  ($HERE)"
setsid nohup env BACKEND_PORT="$BACKEND_PORT" "$PY" lora_manager.py \
  > "$HERE/logs/backend.out" 2>&1 < /dev/null &
echo "[luxy] 启动前端 :$WEB_PORT"
setsid nohup "$PY" "$HERE/webui/serve.py" "$WEB_PORT" \
  > "$HERE/logs/web.out" 2>&1 < /dev/null &
sleep 3
for i in $(seq 1 20); do
  code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/health" 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    echo "[luxy] ✅ 后端就绪 → http://127.0.0.1:$BACKEND_PORT"
    echo "[luxy] ✅ 前端       → http://127.0.0.1:$WEB_PORT/index3.html"
    exit 0
  fi
  sleep 1
done
echo "[luxy] ⚠ 后端 20 秒内未就绪，看 logs/backend.out"
exit 1
