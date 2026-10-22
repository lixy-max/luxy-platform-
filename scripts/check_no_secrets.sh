#!/usr/bin/env bash
# 发布前闸门：确保【要发布的文件】里没有密钥、私钥、数据与模型产物。
# 用法：scripts/check_no_secrets.sh [发布包.zip]
# 退出码非 0 表示不合格，make_release.sh 会因此中止。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAIL=0

PATTERNS='sk-[A-Za-z0-9_-]{16,}|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}'
AUTHORS='"(api_key|apikey|token|secret|password|access_key)"[[:space:]]*:[[:space:]]*"[^"]{12,}"'

# 解压出来的发布包**不是 git 仓库** —— 直接用 git ls-files 会 fatal 然后"空过"，
# 那是假通过。所以：在 git 仓库里扫已跟踪文件；否则退化为扫当前目录下所有文件。
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "== 扫描已跟踪文件（git 仓库）=="
  FILE_LIST=$(git ls-files)
else
  echo "== 扫描目录内所有文件（非 git 仓库，如解压后的发布包）=="
  FILE_LIST=$(find . -type f -not -path './.git/*' -not -path './node_modules/*' 2>/dev/null | sed 's|^\./||')
fi
if [ -z "$FILE_LIST" ]; then echo "  ✗ 没有可扫描的文件（路径不对？）"; exit 1; fi
echo "  待扫描文件数: $(printf '%s\n' "$FILE_LIST" | wc -l)"

echo "== 扫描已跟踪文件 =="
# 占位符/测试值不算密钥（避免误报）：YOUR_ / TEST / DUMMY / FAKE / EXAMPLE / xxx / placeholder
PLACEHOLDER='YOUR_|YOURKEY|TEST|DUMMY|FAKE|EXAMPLE|PLACEHOLDER|CHANGEME|xxxx|XXXX'
HITS=$(printf '%s\n' "$FILE_LIST" | tr '\n' '\0' | xargs -0 grep -IlE "$PATTERNS|$AUTHORS" 2>/dev/null \
        | while read -r f; do
            if grep -viE "$PLACEHOLDER" "$f" | grep -qE "$PATTERNS|$AUTHORS"; then echo "$f"; fi
          done || true)
if [ -n "$HITS" ]; then
  echo "  ✗ 以下已跟踪文件疑似含密钥："; echo "$HITS" | sed 's/^/    /'
  FAIL=1
else
  echo "  ✓ 未发现密钥"
fi

echo "== 扫描禁用文件类型（不应入库）=="
BAD=$(printf '%s\n' "$FILE_LIST" | grep -Ei '\.(safetensors|pt|pth|bin|ckpt|gguf)$|^data/|^uploads/|^models/|^release/|(^|/)settings\.json$|(^|/)\.env$' || true)
if [ -n "$BAD" ]; then
  echo "  ✗ 以下文件不应被发布："; echo "$BAD" | sed 's/^/    /'
  FAIL=1
else
  echo "  ✓ 无禁用文件"
fi

# 可选：检查发布包
if [ $# -ge 1 ] && [ -f "$1" ]; then
  echo "== 扫描发布包 $1 =="
  TMP=$(mktemp -d)
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$1" "$TMP" 2>/dev/null \
    || { echo "  ✗ 解包失败（可能不是合法 zip）"; rm -rf "$TMP"; exit 1; }
  H2=$(grep -rIlE "$PATTERNS|$AUTHORS" "$TMP" 2>/dev/null \
        | while read -r f; do
            if grep -viE "$PLACEHOLDER" "$f" | grep -qE "$PATTERNS|$AUTHORS"; then echo "$f"; fi
          done || true)
  B2=$(find "$TMP" -type f | grep -Ei '\.(safetensors|pt|pth|bin|ckpt|gguf)$|settings\.json$|/\.env$' || true)
  if [ -n "$H2" ] || [ -n "$B2" ]; then
    echo "  ✗ 包内含敏感内容："; { echo "$H2"; echo "$B2"; } | sed 's/^/    /'
    FAIL=1
  else
    echo "  ✓ 包内干净"
  fi
  rm -rf "$TMP"
fi

if [ "$FAIL" = "1" ]; then echo; echo "闸门未通过 ✗"; exit 1; fi
echo; echo "闸门通过 ✓"
