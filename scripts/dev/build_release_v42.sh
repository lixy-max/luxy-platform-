#!/usr/bin/env bash
# 构建 v4.2 发布包（修复 v4.1 缺 8 个前端 JS 的致命问题）
set -eu
SRC=/home/luxy/paper_lora_project
OUT=/mnt/e/WSL/release/luxy-v42
TAR=/mnt/e/WSL/release/luxy-platform-v4.2.tar.gz

rm -rf "$OUT"; mkdir -p "$OUT"/{webui/pages,agent_kb,scripts,docs,sim_plugins,roles,browser_sidecar,install}

echo "=== 1. Python 模块 ==="
cd "$SRC"
cp *.py "$OUT/"
echo "  py: $(ls -1 "$OUT"/*.py | wc -l)"

echo "=== 2. webui（★ 这次拷全：所有 js + pages/ + vendor/）==="
cp webui/index3.html "$OUT/webui/"
cp webui/*.js "$OUT/webui/" 2>/dev/null || true
cp -r webui/pages/. "$OUT/webui/pages/"
[ -d webui/vendor ] && cp -r webui/vendor "$OUT/webui/vendor"
[ -f webui/serve.py ] && cp webui/serve.py "$OUT/webui/"
# 不拷: *.bak / luxy-*.html（开发调试页）/ __pycache__
echo "  webui 根: $(ls -1 "$OUT/webui"/*.html "$OUT/webui"/*.js 2>/dev/null | wc -l) 个 html/js"
echo "  pages/:   $(ls -1 "$OUT/webui/pages"/*.js 2>/dev/null | wc -l) 个 js"
echo "  vendor/:  $(ls -1 "$OUT/webui/vendor" 2>/dev/null | wc -l) 项"

echo "=== 3. ★ 依赖自检：index3.html 引用的本地脚本是否都在包里 ==="
PY_BIN=${PYTHON:-/home/luxy/.venv/bin/python}
$PY_BIN - <<'PYEOF'
import pathlib, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = pathlib.Path('/mnt/e/WSL/release/luxy-v42/webui')
h = (OUT / 'index3.html').read_text(encoding='utf-8')
missing = []
for src in re.findall(r'<script[^>]+src="([^"]+)"', h):
    if src.startswith('http'):
        continue
    rel = src.split('?')[0].lstrip('./')
    if not (OUT / rel).exists():
        missing.append(src)
print('  引用 %d 个本地脚本 · 缺失 %d' % (len(re.findall(r'<script[^>]+src="', h)), len(missing)))
for m in missing:
    print('    ✗ %s' % m)
if not missing:
    print('  ✓ 全部依赖齐全')
PYEOF

echo "=== 4. 其他资源 ==="
cp -r agent_kb/. "$OUT/agent_kb/" 2>/dev/null || true
[ -d sim_plugins ] && cp -r sim_plugins/. "$OUT/sim_plugins/"
[ -d roles ] && cp -r roles/. "$OUT/roles/"
cp scripts/*.py "$OUT/scripts/" 2>/dev/null || true
for f in server.mjs package.json policy.json; do
  [ -f "browser_sidecar/$f" ] && cp "browser_sidecar/$f" "$OUT/browser_sidecar/"
done
cp requirements.txt "$OUT/"
[ -f workflow_config.json ] && cp workflow_config.json "$OUT/"
[ -f agent_config.json ] && cp agent_config.json "$OUT/"
cp docs/WORK_DISCIPLINE.md docs/WORK_HANDOVER_20261022.md "$OUT/docs/" 2>/dev/null || true

echo "=== 5. settings.json（空装默认，sidecar 地址修正）==="
cat > "$OUT/settings.json" <<'JSON'
{
  "base_model_path": "",
  "lora_dir": "./loras",
  "vllm_chat_api": "http://localhost:8000/v1/chat/completions",
  "lora_manager_api": "http://localhost:8001",
  "browser_sidecar": "http://127.0.0.1:9224",
  "kb_mounted": [],
  "collect_mode": false,
  "embedding_device": "cpu",
  "gpu_mode": "single",
  "vllm_gpu_index": 0
}
JSON
mkdir -p "$OUT"/{loras,training_data,uploads,logs,chroma_db,training_output,data}

echo "=== 6. 安装文档 ==="
cp /mnt/e/WSL/_check/INSTALL_template.md "$OUT/docs/INSTALL.md" 2>/dev/null || echo "  ⚠ INSTALL 模板缺失"

echo "=== 7. 打包 ==="
cd /mnt/e/WSL/release
tar -czf "$TAR" luxy-v42
ls -lh "$TAR" | awk '{print "  "$9" ("$5")"}'
echo "  总大小: $(du -sh "$OUT" | cut -f1)"
