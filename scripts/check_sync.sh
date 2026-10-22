#!/usr/bin/env bash
# 同步体检：发布包 / 安装目录 / 快照 是否都与仓库 HEAD 一致。
# 用法：bash scripts/check_sync.sh [--fix]     （--fix = 不一致就重建/重解/重同步）
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYBIN="${PYBIN:-$(command -v python3)}"
VER="${VER:-$(ls -1 release/paper-lora-workbench-*.zip 2>/dev/null | sed 's/.*-v\(.*\)\.zip/v\1/' | sort -V | tail -1)}"
ZIP=release/paper-lora-workbench-$VER.zip
DEST="/mnt/e/project test desktop"
INST="$DEST/paper-lora-workbench-$VER"
SNAP=/mnt/e/WSL/_paper_lora_snapshot
FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

echo "=================================================="
echo " 同步体检（比对内容哈希，不看时间）"
echo "=================================================="
echo "  HEAD: $(git log -1 --format='%h %s' | cut -c1-60)"

# 1) 发布包
stale=0
if [ -f "$ZIP" ]; then
  "$PYBIN" - "$ZIP" <<'PYEOF'
import subprocess, sys, zipfile, os, hashlib
z = zipfile.ZipFile(sys.argv[1])
zp = {n.split('/', 1)[1]: z.read(n) for n in z.namelist() if not n.endswith('/') and '/' in n}
head = [h for h in subprocess.check_output(
    ['git', '-c', 'core.quotePath=false', 'ls-tree', '-r', 'HEAD', '--name-only'], text=True).splitlines() if h]
import fnmatch
attr = {}
for ln in open('.gitattributes', encoding='utf-8'):
    parts = ln.split()
    if len(parts) == 2 and parts[1] == 'export-ignore':
        # 容错：.gitattributes 里写 /path 与 path 都合理（git archive 都认），
        # 但 fnmatch 只认后者 —— 实测因此把已正确排除的文件误报成"包里缺少"✗
        attr[parts[0].lstrip('/')] = True
def ignored(p):
    for pat in attr:
        # 目录级规则（archive/）要匹配它下面的所有文件；
        # 实测漏了这一步会把 archive/*.json 误报成"包里缺少"。
        if pat.endswith('/') and p.startswith(pat):
            return True
        if fnmatch.fnmatch(p, pat):
            return True
    return False
expect = {h for h in head if not ignored(h)}
zset = set(zp)
missing = sorted(expect - zset)
extra = sorted(zset - expect)
diff = [k for k, data in zp.items()
        if os.path.exists(k) and hashlib.sha256(open(k, 'rb').read()).hexdigest() != hashlib.sha256(data).hexdigest()]
print(f"  [发布包] 应含 {len(expect)} 项 / 实含 {len(zset)} 项")
if missing: print("    缺少:", missing[:6])
if extra:   print("    多出:", extra[:6])
if diff:    print("    内容过期:", diff[:6])
ok = not (missing or extra or diff)
print("    结论:", "一致 ✓" if ok else "不一致 ✗")
sys.exit(0 if ok else 9)
PYEOF
  [ $? -ne 0 ] && stale=1
else
  echo "  [发布包] ✗ 不存在"; stale=1
fi

# 2) 安装目录
if [ -d "$INST" ]; then
  "$PYBIN" - "$ZIP" "$INST" <<'PYEOF'
import sys, zipfile, os, hashlib
z = zipfile.ZipFile(sys.argv[1]); base = sys.argv[2]
zp = {n.split('/', 1)[1]: z.read(n) for n in z.namelist() if not n.endswith('/') and '/' in n}
bad = [k for k, d in zp.items()
       if not os.path.exists(os.path.join(base, k))
       or hashlib.sha256(open(os.path.join(base, k), 'rb').read()).hexdigest() != hashlib.sha256(d).hexdigest()]
print(f"  [安装目录] {len(zp)} 项 | 不一致/缺失: {bad[:6] or '无 ✓'}")
sys.exit(0 if not bad else 9)
PYEOF
  [ $? -ne 0 ] && stale=1
else
  echo "  [安装目录] ✗ 不存在"; stale=1
fi

# 3) 快照
if [ -d "$SNAP" ]; then
  miss=0
  for f in $(git -c core.quotePath=false ls-files | grep -vE '^(data/|outputs/|uploads/|logs/|cache/|training_data/|__pycache__/|settings\.json)'); do
    [ -f "$f" ] || continue
    if [ ! -f "$SNAP/$f" ] || [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$(sha256sum "$SNAP/$f" | cut -d' ' -f1)" ]; then
      miss=$((miss + 1))
      [ $miss -le 5 ] && echo "  [快照] 不一致: $f"
    fi
  done
  [ $miss -eq 0 ] && echo "  [快照] 全部一致 ✓" || { echo "  [快照] 共 $miss 个文件不一致 ✗"; stale=1; }
else
  echo "  [快照] ✗ 不存在"; stale=1
fi

echo "--------------------------------------------------"
if [ "$stale" = "0" ]; then
  echo "  ✅ 三处都与仓库一致"
else
  if [ "$FIX" = "1" ]; then
    echo "  · 正在修复（重建发布包 -> 重解安装目录 -> 重同步快照）"
    bash scripts/make_release.sh "$VER" >/dev/null 2>&1
    [ -d "$INST" ] && rm -rf "$INST"
    "$PYBIN" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$ZIP" "$DEST"
    for f in $(git -c core.quotePath=false ls-files | grep -vE '^(data/|outputs/|uploads/|logs/|cache/|training_data/|__pycache__/|settings\.json)'); do
      [ -f "$f" ] && { mkdir -p "$SNAP/$(dirname "$f")"; cp -f "$f" "$SNAP/$f"; }
    done
    echo "  ✅ 已修复，请再跑一次本脚本确认"
  else
    echo "  ⚠️  有不同步项 —— 运行 bash scripts/check_sync.sh --fix 一键修复"
    exit 9
  fi
fi
