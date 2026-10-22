#!/usr/bin/env bash
# 一键启动：vLLM 底座 + 后端 + 前端（幂等：已在运行的就跳过，不会重复启动）
# 用法：
#   bash scripts/start.sh              # 全量启动（含推理底座）
#   bash scripts/start.sh --no-gpu     # 不起底座（仅 KB/规则模式）
#   bash scripts/start.sh --no-browser # 不尝试打开浏览器
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# 找到可用的 Python：venv 可能在项目内、也可能在家目录（本机实际是 $HOME）。
# 之前只试了 $ROOT/.venv，找不到就退回 python3，而非交互 shell 里 python3 不在 PATH 上，
# 于是"找不到 Python"直接退出 —— 这是个纯路径假设错误。
PY=""
for cand in "${VENV:-}" "$ROOT/.venv/bin/python" "$HOME/.venv/bin/python" \
            "$ROOT/venv/bin/python" "$(command -v python3 || true)" "$(command -v python || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] && { PY="$cand"; break; }
done
[ -n "$PY" ] || { echo "  ✗ 找不到可用的 Python（试过项目 .venv、~/​.venv、PATH）"; exit 1; }

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
VLLM_PORT="${VLLM_PORT:-8000}"
# 默认模型路径跟着当前用户的家目录走，不写死具体用户名
# 默认底座（跟着本机实际在用的那个走；换模型时可 export VLLM_MODEL 覆盖）
VLLM_MODEL="${VLLM_MODEL:-$HOME/models/Qwen2.5-7B-Instruct-AWQ}"

# ★★ KB 的 embedding 必须与【建索引时用的模型】一致 ✗ 否则查询直接报"维度不一致" ✗
#    实测事故: 这里没带 KB_EMBED_MODEL → 后端用默认 bge-m3(1024) ✗
#    而 xiyouji 的索引是 bge-small-zh(512) ✗ → 挂知识库的对话全部失败 ✓
#    （更坑的是: 当时流式端点的错误处理自己也炸 → 前端只显示 network error ✓）
KB_EMBED_MODEL="${KB_EMBED_MODEL:-$HOME/models/bge-small-zh-v1.5}"
# vLLM 占着 GPU，embedding 走 CPU 免得 OOM
KB_EMBED_DEVICE="${KB_EMBED_DEVICE:-cpu}"
export KB_EMBED_MODEL KB_EMBED_DEVICE

NO_GPU=0; OPEN_BROWSER=1
for a in "$@"; do
  case "$a" in
    --no-gpu) NO_GPU=1 ;;
    --no-browser) OPEN_BROWSER=0 ;;
  esac
done

mkdir -p logs
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
info() { printf '  · %s\n' "$1"; }
# ★ 注意（实测踩过）: curl 在【连接失败时自己就会输出 000】，
#   再写 `|| echo 000` 就会得到两个 000 → 汇总里显示成 "000000" ✗
code() {
  local c
  c=$(curl -s -o /dev/null -w '%{http_code}' -m "${2:-3}" "$1" 2>/dev/null)
  [ -n "$c" ] || c=000
  printf '%s' "$c"
}

echo "=================================================="
echo " 论文分析工作台 · 启动"
echo "=================================================="

# ---------- 0. 环境预检 ----------
echo "[0/4] 环境预检"
ok "Python: $PY"
for d in outputs cache/nodes uploads training_data data logs; do mkdir -p "$d"; done
ok "运行目录就绪"
info "后端 :$BACKEND_PORT  前端 :$FRONTEND_PORT  底座 :$VLLM_PORT"
info "KB embedding: $KB_EMBED_MODEL (device=$KB_EMBED_DEVICE)"
info "底座模型: $VLLM_MODEL"

# ---------- 1. 后端 ----------
echo "[1/4] 后端"
if [ "$(code "http://127.0.0.1:$BACKEND_PORT/health")" = "200" ]; then
  ok "后端已在运行"
else
  # ★ 环境变量必须显式传给后端 ✓（setsid + 非交互 shell 不继承外层 export ✗ 实测踩过 ✓）
  # ★ F-1（2026-10-19 发布包测试发现 ✗）: BACKEND_PORT 必须传 ✓ 否则后端永远绑 8001 ✗
  setsid nohup env \
    KB_EMBED_MODEL="$KB_EMBED_MODEL" \
    KB_EMBED_DEVICE="$KB_EMBED_DEVICE" \
    BACKEND_PORT="$BACKEND_PORT" \
    WORKBENCH_HOST="${WORKBENCH_HOST:-127.0.0.1}" \
    "$PY" lora_manager.py > logs/backend.log 2>&1 < /dev/null &
  BACK_PID=$!
  ready=0
  for i in $(seq 1 25); do
    sleep 1
    if [ "$(code "http://127.0.0.1:$BACKEND_PORT/health" 2)" = "200" ]; then
      ok "后端就绪（${i}s，pid $BACK_PID）"; ready=1; break
    fi
  done
  if [ "$ready" = "0" ]; then
    bad "后端 25 秒内未就绪，看 logs/backend.log 末尾："
    tail -6 logs/backend.log 2>/dev/null | sed 's/^/      /'
    exit 1
  fi
fi

# ---------- 2. 前端 ----------
echo "[2/4] 前端"
if [ "$(code "http://127.0.0.1:$FRONTEND_PORT/index3.html")" = "200" ]; then
  ok "前端已在运行"
else
  (cd webui && setsid nohup "$PY" serve.py "$FRONTEND_PORT" > ../logs/frontend.log 2>&1 < /dev/null &)
  ready=0
  for i in $(seq 1 10); do
    sleep 1
    if [ "$(code "http://127.0.0.1:$FRONTEND_PORT/index3.html" 2)" = "200" ]; then
      ok "前端就绪（${i}s）"; ready=1; break
    fi
  done
  [ "$ready" = "1" ] || bad "前端未就绪，看 logs/frontend.log"
fi

# ---------- 3. 推理底座 ----------
echo "[3/4] 推理底座 vLLM"
if [ "$NO_GPU" = "1" ]; then
  info "已指定 --no-gpu，跳过"
elif [ "$(code "http://127.0.0.1:$VLLM_PORT/v1/models")" = "200" ]; then
  model=$(curl -s -m 5 "http://127.0.0.1:$VLLM_PORT/v1/models" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print((d.get("data") or [{}])[0].get("id","?"))' 2>/dev/null || echo "?")
  ok "底座已在运行，模型：$model"
else
  info "底座未启动，正在通过后端接口拉起（模型 $VLLM_MODEL）…"
  # 关键：**不阻塞等待**。底座冷启动可能几分钟，启动器卡在那里既没必要也让人以为死机
  # （用户反馈"要这么久"，根因就是这里原来轮询 180 次 x 2 秒 = 最多 360 秒）。
  # 现在只提交请求 + 快速探测一次，其余交给后台轮询与「底座管理」页显示进度。
  curl -s -m 20 -X POST "http://127.0.0.1:$BACKEND_PORT/vllm/start" \
       -H 'Content-Type: application/json' \
       -d "{\"model_path\":\"$VLLM_MODEL\",\"port\":$VLLM_PORT}" >/dev/null 2>&1
  sleep 3
  if [ "$(code "http://127.0.0.1:$VLLM_PORT/v1/models" 2)" = "200" ]; then
    ok "底座就绪（很快）"
  else
    info "底座正在后台加载，稍等 1~3 分钟即可用"
    info "进度查看：网页「🎛️ 底座管理」页，或 tail -f logs/backend.log | grep -i vllm"
    info "（本步骤不再等待，先给你能用的地址）"
    # 后台记录一次最终结果，便于事后查看
    ( for i in $(seq 1 90); do
        sleep 4
        [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$VLLM_PORT/v1/models" 2>/dev/null || echo 000)" = "200" ] && {
          echo "$(date '+%H:%M:%S') 底座就绪" >> logs/vllm_wait.log; exit 0; }
      done
      echo "$(date '+%H:%M:%S') 底座 6 分钟内未就绪（模型路径或显存需检查）" >> logs/vllm_wait.log
    ) >/dev/null 2>&1 &
  fi
fi

# ---------- 4. 汇总 ----------
echo "[4/4] 汇总"
URL="http://127.0.0.1:$FRONTEND_PORT/index3.html"
printf '  后端  %s  %s\n' "$(code "http://127.0.0.1:$BACKEND_PORT/health" 3)" "http://127.0.0.1:$BACKEND_PORT"
printf '  前端  %s  %s\n' "$(code "http://127.0.0.1:$FRONTEND_PORT/index3.html" 3)" "$URL"
_vcode=$(code "http://127.0.0.1:$VLLM_PORT/v1/models" 3)
if [ "$_vcode" = "200" ]; then
  printf '  底座  %s  %s\n' "$_vcode" "http://127.0.0.1:$VLLM_PORT"
else
  printf '  底座  %s  %s  （正在加载，1~3 分钟；进度看「🎛️ 底座管理」页）\n' "$_vcode" "http://127.0.0.1:$VLLM_PORT"
fi
# ---------- 3.5 KB 自检（★ 静默坏掉最难查 ✗ 实测吃过这个亏 ✓）
echo "[自检] 知识库检索"
KBCHK=$(curl -s -m 60 -X POST "http://127.0.0.1:$BACKEND_PORT/kb/xiyouji/query" \
        -H 'Content-Type: application/json' \
        -d '{"query":"孙悟空 师父","top_k":2}' 2>/dev/null || echo '')
if printf '%s' "$KBCHK" | grep -q '"results"'; then
  ok "知识库可检索"
else
  bad "知识库检索失败 —— 多半是 KB_EMBED_MODEL 与索引维度不符"
  info "当前 KB_EMBED_MODEL=$KB_EMBED_MODEL；若改过它，需要重建索引："
  info "  curl -X POST http://127.0.0.1:$BACKEND_PORT/kb/<库名>/rebuild"
fi
# LoRA 加载状态（vLLM 重启后必然清空 ✗ 页面会自动重挂 ✓ 这里只提示）
LOADED=$(curl -s -m 10 "http://127.0.0.1:$BACKEND_PORT/loras/loaded" 2>/dev/null || echo '')
if printf '%s' "$LOADED" | grep -q 'swk'; then
  ok "LoRA 已加载"
else
  info "vLLM 里暂无 LoRA（打开页面会自动重挂 ✓ 或在角色页选一次角色）"
fi

echo
echo "  打开：$URL"

if [ "$OPEN_BROWSER" = "1" ]; then
  if command -v wslview >/dev/null 2>&1; then wslview "$URL" >/dev/null 2>&1 && ok "已在浏览器打开"
  elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$URL" >/dev/null 2>&1 && ok "已在浏览器打开"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 && ok "已在浏览器打开"
  else info "没找到打开浏览器的方式，请手动访问上面的地址"
  fi
fi
echo
echo "  停止：bash scripts/stop.sh      查看日志：tail -f logs/backend.log"
echo "=================================================="
