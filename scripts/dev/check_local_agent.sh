#!/usr/bin/env bash
# ============================================================================
# 本地 Agent 路径冒烟测试
#
# 为什么有这个（血的教训）:
#   2026-10-13 实测发现"通过 LoRA 平台 + 本地底座驱动 agent"这条路【根本走不通】，
#   而且四处问题【都是极底层、一测就现形】的，却藏了很久:
#     ① lora_manager.py /chat/tools 本地分支写了不存在的 VLLM_PORT → NameError → 500
#     ② vLLM 启动参数缺 --enable-auto-tool-choice / --tool-call-parser → 带 tools 的请求被拒
#     ③ 前端 agent 对本地走"文本协议"（要求模型输出 <tool_call> 标记）→ 本地 Qwen 不打 → 调用丢失
#     ④ 前端 buildToolsSchema 只列文档工具 → 20+ 个 agent 工具根本没进 tools
#   根因不是运气差: 【这些路径从来没被端到端跑过】—— 云分支一直正常，把本地的问题全遮住了。
#   ⇒ 这个脚本就是护栏: 【一条命令，检查本地 agent 路径的关键环节】✓
#
# 用法: bash scripts/check_local_agent.sh
#   只做静态 + 接口检查，不需要浏览器；有 FAIL 就退出码 1。
# ============================================================================
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYBIN:-/home/luxy/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
API="${API_BASE:-http://127.0.0.1:8001}"
VLLM="${VLLM_BASE:-http://127.0.0.1:8000}"

PASS=0; FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '    %s\n' "$1"; }

echo "=================================================="
echo " 本地 Agent 路径冒烟测试"
echo "=================================================="

# ---------- 静态检查: 源码里该有的东西还在不在 ----------
echo "[1/5] 源码静态检查"

grep -q 'enable-auto-tool-choice' vllm_manager.py \
  && ok "vllm_manager.py 带 --enable-auto-tool-choice" \
  || bad "vllm_manager.py 【缺 --enable-auto-tool-choice】→ 带 tools 的请求会被 vLLM 拒（400）"

grep -q 'tool-call-parser' vllm_manager.py \
  && ok "vllm_manager.py 带 --tool-call-parser" \
  || bad "vllm_manager.py 【缺 --tool-call-parser】→ 模型无法返回结构化 tool_calls"

# ★ 只看【非注释行】✗ 否则会匹配到解释这个坑的注释（实测误报过一次 ✓）
if grep -nE '^[^#]*VLLM_PORT' lora_manager.py | grep -v '^\s*#' > /dev/null 2>&1; then
  bad "lora_manager.py 【又出现未定义的 VLLM_PORT】→ 本地分支会 NameError"
else
  ok "lora_manager.py 没有未定义的 VLLM_PORT"
fi

grep -q "const API_BASE = 'http://127.0.0.1:8001'" webui/index3.html \
  && ok "webui API_BASE 用 127.0.0.1" \
  || bad "webui API_BASE 【不是 127.0.0.1】→ 浏览器里 localhost 可能解析成 IPv6 ::1（时通时不通）"

grep -q 'AGENT_TOOL_DOCS' webui/index3.html \
  && ok "前端有 AGENT_TOOL_DOCS（agent 工具会进原生 schema）" \
  || bad "前端【缺 AGENT_TOOL_DOCS】→ buildToolsSchema 只列文档工具 → 模型没工具可调"

grep -q "_agentToolSchema().forEach" webui/index3.html \
  && ok "buildToolsSchema 合并了 agent 工具" \
  || bad "buildToolsSchema 【没合并 agent 工具】"

grep -q "const cloud = true" webui/index3.html \
  && ok "前端 agent 本地也走原生 function-calling" \
  || info "提示: 若改回 isCloudModel 分支，本地会走文本协议（本地 Qwen 常不打标记 → 工具调用丢失）"

# ---------- 接口检查 ----------
echo "[2/5] 服务可达性"
H=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$API/health" 2>/dev/null)
[ "$H" = "200" ] && ok "后端 $API 可达" || { bad "后端 $API 不可达（$H）→ 先跑 scripts/start.sh"; }

V=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$VLLM/v1/models" 2>/dev/null)
[ "$V" = "200" ] && ok "本地 vLLM $VLLM 可达" || bad "本地 vLLM 不可达（$V）→ 底座没起"

# ---------- 运行进程: 参数真的带上了吗（源码对了不代表进程对 ✓）----------
echo "[3/5] 运行中的 vLLM 命令行"
if pgrep -af 'vllm.entrypoints' > /dev/null 2>&1; then
  CMD=$(pgrep -af 'vllm.entrypoints' | head -1)
  echo "$CMD" | grep -q 'enable-auto-tool-choice' \
    && ok "运行的 vLLM 带 --enable-auto-tool-choice" \
    || bad "【运行的 vLLM 没带该参数】→ 改了源码但没重启 vLLM（这就是最容易漏的一步）"
  echo "$CMD" | grep -q 'tool-call-parser' \
    && ok "运行的 vLLM 带 --tool-call-parser" \
    || bad "【运行的 vLLM 没带 --tool-call-parser】"
else
  bad "没有 vllm.entrypoints 进程"
fi

# ---------- 端到端: 本地模型 + tools 真的能返回 tool_calls 吗 ----------
echo "[4/5] 端到端: 本地模型 + 工具（最关键 ✓）"
if [ "$H" = "200" ]; then
  _E2E_OUT=$("$PY" - <<PYEOF
import json, sys, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
API = "$API"
tools = [{'type': 'function', 'function': {
    'name': 'list_knowledge_bases', 'description': '列出所有知识库',
    'parameters': {'type': 'object', 'properties': {}, 'required': []}}}]
body = {'model': 'vllm:base',
        'messages': [{'role': 'user', 'content': '看一下现在有哪些知识库。请调用工具查询。'}],
        'tools': tools, 'tool_choice': 'auto', 'max_tokens': 300, 'temperature': 0.2}
r = urllib.request.Request(API + '/chat/tools', data=json.dumps(body).encode(),
                           headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(r, timeout=300) as f:
        d = json.loads(f.read().decode())
    tc = d.get('tool_calls') or []
    if tc:
        print(f'  \033[32m✓\033[0m /chat/tools(本地) 返回 tool_calls={len(tc)}: {[c.get("name") for c in tc]}')
    else:
        print(f'  \033[31m✗\033[0m /chat/tools(本地) 200 但 【tool_calls=0】'
              f'（content={str(d.get("content"))[:60]!r}）→ 本地模型拿不到工具')
except urllib.error.HTTPError as e:
    print(f'  \033[31m✗\033[0m /chat/tools(本地) HTTP {e.code}: '
          f'{e.read().decode("utf-8", errors="replace")[:160]}')
except Exception as e:
    print(f'  \033[31m✗\033[0m /chat/tools(本地) {type(e).__name__}: {str(e)[:120]}')
PYEOF
)
  printf '%s\n' "$_E2E_OUT"
  if printf '%s' "$_E2E_OUT" | grep -q 'tool_calls='; then
    if printf '%s' "$_E2E_OUT" | grep -q 'tool_calls=0'; then
      FAIL=$((FAIL+1))
    else
      PASS=$((PASS+1))
    fi
  else
    FAIL=$((FAIL+1))
  fi
else
  info "后端不可达，跳过"
fi

# ---------- 前端门禁（顺带跑一遍 ✓）----------
echo "[5/5] 前端与后端门禁"
if command -v node > /dev/null 2>&1; then
  node tools/check_js_syntax.js > /dev/null 2>&1 && ok "JS 语法检查通过" || bad "JS 语法检查失败"
else
  info "本机没有 node，跳过 JS 语法检查（Windows 侧可跑）"
fi

echo
echo "=================================================="
echo " 静态检查: 通过 $PASS · 失败 $FAIL"
echo "=================================================="
if [ "$FAIL" -gt 0 ]; then
  echo " ✗ 有失败项 —— 本地 agent 路径可能已坏，按上面提示修"
  echo "   常见原因: ① 改了源码没重启 ② vLLM 没带 tool 参数 ③ 前端 schema 没含 agent 工具"
  exit 1
fi
echo " ✓ 本地 agent 路径的关键环节都在"
echo "   （最后一步建议用界面实测一次: 💬对话 → 选「不挂 LoRA·用当前底座」→ 开 Agent → 问「列出所有知识库」）"
