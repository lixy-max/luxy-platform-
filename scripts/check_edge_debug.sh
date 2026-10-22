#!/usr/bin/env bash
# 检查 Edge 远程调试是否就绪（浏览器自动化的前提 ✓）
set -u
echo "=== Edge 远程调试检查 ==="
echo -n "  9222 调试端口: "
V=$(curl -s -m 3 http://127.0.0.1:9222/json/version 2>/dev/null)
if [ -n "$V" ]; then
  echo "✓ 已连上"
  echo "$V" | head -c 400 | sed 's/^/    /'
else
  echo "✗ 未开"
  cat <<'TIP'
  ⇒ 请在 Edge 里做一次（只需一次 ✓）:
     地址栏输入  edge://inspect/#remote-debugging
     勾选 "Allow remote debugging for this browser instance" ✓
     （Playwright 官方支持按通道名连 msedge ✓ 无需特殊启动参数 ✓）
TIP
fi
echo
echo -n "  playwright-core: "
if [ -d node_modules/playwright-core ] || [ -d /mnt/e/WSL/_check/drill/node_modules/playwright-core ]; then
  echo "✓ 已装（复用 drill 的 ✓）"
else
  echo "✗ 未装（在 browser_sidecar/ 里 npm i playwright-core ✓）"
fi
echo -n "  Edge 可执行文件: "
if [ -f "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" ]; then
  echo "✓"
else
  echo "✗（用 channel:msedge 兜底 ✓）"
fi
