#!/usr/bin/env bash
# 构建开源发布包（v4.9.7 起）
#
# 设计原则：**发布包 = git archive 快照 + 明确定义的附加物**
#   ① git archive 打底（.gitattributes 的 export-ignore 自动生效）
#   ② 附加物只有两类，且在此脚本里显式列出：
#      - settings.json（脱敏模板）
#      - 运行时数据目录的 .gitkeep（git 不跟踪空目录，但没有它们启动会报错）
#
# 用法：bash scripts/build_release.sh [输出目录]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VER="$(cat VERSION)"
NAME="luxy-platform-${VER}"
OUTDIR="${1:-/mnt/e/WSL/release}"
WORK="$(mktemp -d)"
PKG="${WORK}/${NAME}"

echo "构建 ${NAME} …"
echo "  版本: ${VER}"
echo "  输出: ${OUTDIR}/${NAME}.tar.gz"

# ① git archive 打底
git archive --format=tar --prefix="${NAME}/" HEAD | tar -x -C "$WORK"

# ② 附加物 1/2：脱敏 settings.json
PYBIN="${PYTHON:-python3}"
"$PYBIN" - "$PKG" <<'PY'
import json, pathlib, sys
pkg = pathlib.Path(sys.argv[1])
clean = {
    "base_model_path": "", "lora_dir": "./loras",
    "vllm_chat_api": "http://localhost:8000/v1/chat/completions",
    "lora_manager_api": "http://localhost:8001",
    "browser_sidecar": "http://127.0.0.1:9224",
    "kb_mounted": [], "collect_mode": False,
    "embedding_device": "cpu", "gpu_mode": "auto",
    "providers": {
        "openai": {"api_base": "https://api.deepseek.com/v1",
                   "model": "deepseek-chat", "api_key": ""},
        "ollama": {"api_base": "", "model": ""},
        "vllm": {"api_base": "http://localhost:8000/v1", "model": ""},
    },
}
(pkg / "settings.json").write_text(
    json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
PY

# ③ 附加物 2/2：运行时数据目录（空目录 + .gitkeep）
for d in data chroma_db loras training_data uploads logs training_output outputs models; do
    mkdir -p "${PKG}/${d}"
    touch "${PKG}/${d}/.gitkeep"
done

# ④ 权限
chmod +x "${PKG}"/install/*.sh "${PKG}"/scripts/run.sh 2>/dev/null || true
chmod 644 "${PKG}"/*.json "${PKG}"/*.md 2>/dev/null || true

# ⑤ 打包 + 校验
mkdir -p "$OUTDIR"
TAR="${OUTDIR}/${NAME}.tar.gz"
tar -czf "$TAR" -C "$WORK" "$NAME"
(
  cd "$OUTDIR"
  sha256sum "${NAME}.tar.gz" | awk '{print $1}' > "${NAME}.tar.gz.sha256"
)

ARCHIVED=$(git archive --format=tar HEAD | tar -t | grep -c -v '/$' || true)
PACKED=$(tar -tzf "$TAR" | grep -c -v '/$' || true)

echo ""
echo "✓ ${TAR}"
echo "  大小        : $(du -h "$TAR" | cut -f1)"
echo "  sha256      : $(cat "${TAR}.sha256")"
echo "  git archive : ${ARCHIVED} 个文件"
echo "  发布包      : ${PACKED} 个文件（+$((PACKED - ARCHIVED)) 附加物：settings.json + 9 个 .gitkeep）"
echo "  顶层前缀    : $(tar -tzf "$TAR" | head -1)"

rm -rf "$WORK"
