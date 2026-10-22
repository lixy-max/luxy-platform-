#!/usr/bin/env bash
# 一键发布同步：重建发布包 -> 重解安装目录 -> 同步快照 -> 体检。
# 顺序很关键：make_release.sh 会重写 .gitattributes（export-ignore 列表自动生成），
# 所以必须【先打包、后同步快照】，否则快照里的 .gitattributes 永远落后一版。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYBIN="${PYBIN:-$(command -v python3)}"
DEST="/mnt/e/project test desktop"
VER="${VER:-v0.2.0}"                 # 可用 VER=vX.Y.Z 覆盖
INST="$DEST/paper-lora-workbench-$VER"
SNAP=/mnt/e/WSL/_paper_lora_snapshot

# 守卫：make_release.sh 打包的是 git HEAD，所以【未提交的改动不会进包】。
# 实测踩过：改完 check_sync.sh 没提交就打包 -> 包里是旧版 -> "内容过期"。
dirty=$(git status --porcelain --untracked-files=no | head -5)
if [ -n "$dirty" ]; then
  echo "✗ 工作区有未提交的改动，请先提交再发布（发布包只含 HEAD 的内容）："
  echo "$dirty" | sed 's/^/    /'
  exit 2
fi

echo "== 1/4 重建发布包 =="
bash scripts/make_release.sh "$VER" || exit 1

echo "== 2/4 重解安装目录（$INST）=="
[ -d "$INST" ] && rm -rf "$INST"
"$PYBIN" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
           release/paper-lora-workbench-$VER.zip "$DEST"
echo "   $(find "$INST" -type f | wc -l) 个文件"
# 实测缺失（2026-10-11）：原来只解压、不拷贝 zip ✗ → Windows 目录里一直没有 zip，
# 而 zip 才是"要发出去的那个文件"。补上，并做字节一致性校验。
cp -f "release/paper-lora-workbench-$VER.zip" "$DEST/"
if cmp -s "release/paper-lora-workbench-$VER.zip" "$DEST/paper-lora-workbench-$VER.zip"; then
  echo "   zip 已拷贝: $DEST/paper-lora-workbench-$VER.zip（字节一致 ✓）"
else
  echo "   ✗ zip 拷贝校验失败"; exit 1
fi

echo "== 3/4 同步快照（$SNAP）=="
for f in $(git -c core.quotePath=false ls-files | grep -vE '^(data/|outputs/|uploads/|logs/|cache/|training_data/|__pycache__/|settings\.json)'); do
  [ -f "$f" ] && { mkdir -p "$SNAP/$(dirname "$f")"; cp -f "$f" "$SNAP/$f"; }
done
echo "   $(find "$SNAP" -type f | wc -l) 个文件"

echo "== 4/4 体检 =="
bash scripts/check_sync.sh
