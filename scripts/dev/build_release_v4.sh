#!/usr/bin/env bash
# 构建 v4 发布包（2026-10-22）
set -eu
SRC=/home/luxy/paper_lora_project
OUT=/mnt/e/WSL/release/luxy-v4
TAR=/mnt/e/WSL/release/luxy-platform-v4.tar.gz

echo "=== 0. PROJECT_DIR 定义检查（决定隔离是否可行）==="
grep -n 'PROJECT_DIR *=' "$SRC/lora_manager.py" | head -2

echo ""
echo "=== 1. 清理并创建输出目录 ==="
rm -rf "$OUT"
mkdir -p "$OUT"/{webui,vendor,agent_kb,scripts,docs,sim_plugins,roles,browser_sidecar,install}

echo "=== 2. 拷贝顶层 Python 模块 ==="
cd "$SRC"
for f in *.py; do
  cp "$f" "$OUT/"
done
ls -1 "$OUT"/*.py | wc -l | sed 's/^/  已拷贝 py: /'

echo "=== 3. 拷贝 webui（排除 .bak 与开发页）==="
cp webui/index3.html webui/api.js webui/browser_page.js webui/learn.js "$OUT/webui/" 2>/dev/null || true
[ -f webui/serve.py ] && cp webui/serve.py "$OUT/webui/"
[ -d webui/vendor ] && cp -r webui/vendor "$OUT/webui/vendor"
ls -1 "$OUT/webui" | sed 's/^/  /'

echo "=== 4. 拷贝 agent_kb（工具文档）/ sim_plugins / roles ==="
cp -r agent_kb/. "$OUT/agent_kb/" 2>/dev/null || true
[ -d sim_plugins ] && cp -r sim_plugins/. "$OUT/sim_plugins/" 2>/dev/null || true
[ -d roles ] && cp -r roles/. "$OUT/roles/" 2>/dev/null || true

echo "=== 5. 拷贝 scripts（49 个）==="
cp scripts/*.py "$OUT/scripts/" 2>/dev/null || true
ls -1 "$OUT/scripts"/*.py 2>/dev/null | wc -l | sed 's/^/  已拷贝: /'

echo "=== 6. 拷贝 browser_sidecar（不带 node_modules）==="
for f in server.mjs package.json policy.json; do
  [ -f "browser_sidecar/$f" ] && cp "browser_sidecar/$f" "$OUT/browser_sidecar/"
done
ls -1 "$OUT/browser_sidecar" | sed 's/^/  /'

echo "=== 7. requirements + docs ==="
cp requirements.txt "$OUT/" 2>/dev/null || echo "  ⚠ requirements.txt 缺失"
for d in WORK_DISCIPLINE.md WORK_HANDOVER_20261022.md; do
  [ -f "docs/$d" ] && cp "docs/$d" "$OUT/docs/"
done

echo "=== 8. 生成本地默认配置（空数据）==="
mkdir -p "$OUT/data"
cat > "$OUT/data/.gitkeep" <<'EOF'
EOF
# 最小 settings.json（可被 UI 覆盖）
cat > "$OUT/settings.json" <<'JSON'
{
  "base_model_path": "/home/luxy/models/Qwen2.5-7B-Instruct-AWQ",
  "lora_dir": "./loras",
  "vllm_chat_api": "http://localhost:8000/v1/chat/completions",
  "lora_manager_api": "http://localhost:8001",
  "kb_mounted": [],
  "collect_mode": false,
  "embedding_device": "cpu",
  "gpu_mode": "single",
  "vllm_gpu_index": 0
}
JSON
mkdir -p "$OUT"/{loras,training_data,uploads,logs,chroma_db,training_output}

echo "=== 9. 写启动脚本 ==="
cat > "$OUT/install/start.sh" <<'SH'
#!/usr/bin/env bash
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
PORT="${BACKEND_PORT:-8001}"
cd "$HERE"
mkdir -p logs data loras training_data uploads chroma_db training_output
echo "[luxy] backend on :$PORT  (dir=$HERE)"
exec "$PY" lora_manager.py
SH
chmod +x "$OUT/install/start.sh"

cat > "$OUT/install/serve_web.sh" <<'SH'
#!/usr/bin/env bash
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
PORT="${WEB_PORT:-8080}"
cd "$HERE/webui"
echo "[luxy] web on :$PORT  (dir=$HERE/webui)"
exec "$PY" -m http.server "$PORT"
SH
chmod +x "$OUT/install/serve_web.sh"

echo "=== 10. 打包 ==="
cd /mnt/e/WSL/release
tar -czf "$TAR" luxy-v4
ls -lh "$TAR" | awk '{print "  tar: "$9" ("$5")"}'
echo ""
echo "=== 包内容统计 ==="
echo "  py:        $(ls -1 "$OUT"/*.py 2>/dev/null | wc -l)"
echo "  webui:     $(ls -1 "$OUT/webui" 2>/dev/null | wc -l) 项"
echo "  scripts:   $(ls -1 "$OUT/scripts"/*.py 2>/dev/null | wc -l)"
echo "  总大小:    $(du -sh "$OUT" | cut -f1)"
