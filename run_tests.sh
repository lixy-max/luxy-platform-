#!/usr/bin/env bash
# ★ 修（2026-10-22）：环境里只有 python3，没有 python ✗
# 优先：显式 PYTHON > 本地 .venv > PATH 上的 python3
if [ -n "${PYTHON:-}" ]; then
  PY_BIN="$PYTHON"
elif [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
elif [ -x "$HOME/.venv/bin/python" ]; then
  PY_BIN="$HOME/.venv/bin/python"
else
  PY_BIN="$(command -v python3 || command -v python)"
fi

# 一键测试入口。
#
#   后端：标准库 unittest + FastAPI TestClient（无需安装 pytest）
#   前端：Node 脚本，抽出 index3.html 的内联 script 做语法与行为测试
#
# 注意：WSL 里没装 node，所以前端部分需要在 **Windows 侧** 运行：
#   node "tests/frontend/run.js"   # 在 Windows 侧执行
set -u
cd "$(dirname "$0")"
PY=${PY:-$PY_BIN}
rc=0

echo "=== 后端单元测试 (unittest) ==="
if command -v "$PY" >/dev/null 2>&1 || [ -x "$PY" ]; then
  "$PY" -m unittest discover -s tests -t . -v || rc=1
else
  echo "找不到 python: $PY"
  rc=1
fi

echo
echo "=== 前端测试 (node) ==="
# ★ SKIP_FE_TESTS=1 时跳过（CI 用：Linux 上的 vm 环境与浏览器差异大，
#   该 harness 是为 Windows/WSL 侧 node 设计的；详见 tests/frontend/README.md）
if [ "${SKIP_FE_TESTS:-0}" = "1" ]; then
  echo "SKIP_FE_TESTS=1 —— 跳过前端测试（后端测试照常）"
elif command -v node >/dev/null 2>&1; then
  node tests/frontend/run.js || rc=1
else
  echo "WSL 内没有 node —— 请在 Windows 侧执行："
  echo '  node "tests/frontend/run.js"   # 在 Windows 侧执行'
fi

echo
if [ "$rc" = "0" ]; then echo "全部通过 ✅"; else echo "有失败项 ❌"; fi
exit $rc
