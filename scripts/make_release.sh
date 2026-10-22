#!/usr/bin/env bash
# 打发布包：只含"运行所需的最小集合" —— 源码 + 测试 + 两个预设工作流 + 文档 + 脚本
# 关键保证：**不含**论文 PDF、上传文件、输出结果、数据集、模型权重、LoRA、密钥、内部报告
# 用法：bash scripts/make_release.sh [版本号]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VER="${1:-v0.2.0}"
NAME="paper-lora-workbench-$VER"
OUT="$ROOT/release"
mkdir -p "$OUT"

echo "== 发布前自检 =="
fail=0
for f in requirements.txt README.md; do
  if [ ! -e "$f" ]; then echo "  [缺] $f（必须补）"; fail=1; else echo "  [有] $f"; fi
done
if [ -e LICENSE ]; then
  echo "  [有] LICENSE"
else
  echo "  [警告] 缺少 LICENSE —— 公开前必须补（否则法律上他人不可使用）"
fi
# ★ P-26: 只看【已入库】的预设（演练会在磁盘上造一堆 workflow_config_* ✗ 不该卡发布 ✗）
presets=$(git ls-files "workflow_config*.json" 2>/dev/null | wc -l)
echo "  预设工作流数量: $presets（要求 = 2）"
[ "$presets" = "2" ] || { echo "  [警告] 预设工作流不是 2 个"; fail=1; }
# 只扫**被 git 追踪**的文件 —— 那才是会进包的东西（settings.json 未被追踪，不会进包）
if git grep -qE "sk-[A-Za-z0-9]{16,}" HEAD -- . 2>/dev/null; then
  echo "  [危险] 被追踪文件里检出疑似明文密钥"; fail=1
else
  echo "  [安全] 被追踪文件中未检出明文密钥"
fi
# 工作区有明文密钥时提醒（不进包，但应尽快改成环境变量）
if [ -e settings.json ] && grep -qE "sk-[A-Za-z0-9]{16,}" settings.json 2>/dev/null; then
  echo "  [提醒] 本地 settings.json 含明文密钥（未被追踪、不会进包，但建议改用环境变量）"
fi
[ "$fail" = "0" ] || { echo; echo "自检未通过，停止打包（先修上面带 [缺]/[危险] 的项）"; exit 1; }

echo
echo "== 知识库卫生检查（防止把测试用/示例库当成正式内容）=="
if [ -d data/kbs ]; then
  PYBIN="${PY:-$(command -v python3 || command -v python)}"
  "$PYBIN" - <<'KBCHECK'
from pathlib import Path
import json
root = Path('data/kbs')
suspicious = []
for d in sorted(root.iterdir()):
    m = d / 'meta.json'
    if not d.is_dir() or not m.exists():
        continue
    try:
        info = json.loads(m.read_text(encoding='utf-8'))
    except Exception:
        continue
    name = str(info.get('name') or d.name)
    desc = str(info.get('description') or '')
    docs = [str(x.get('name')) for x in (info.get('documents') or [])]
    flag = (any(k in name.lower() for k in ('test', 'demo', 'sample', 'tmp', 'temp'))
            or '测试' in desc or '示例' in desc)
    print(f"  KB {name}: {info.get('total_chunks')} chunks, 文档 {docs[:3]}{'   <== 疑似测试库' if flag else ''}")
    if flag:
        suspicious.append(name)
if suspicious:
    print(f"  [警告] 发现疑似测试用知识库 {suspicious} —— 发布/交付前请确认是否需要删除")
else:
    print('  [通过] 未发现疑似测试用知识库')
KBCHECK
else
  echo "  没有 data/kbs（干净）"
fi

echo
echo "== 用 git archive 打包（只含被追踪文件 => 天然排除数据/权重/密钥）=="
git archive --format=zip --prefix="$NAME/" -o "$OUT/$NAME.zip" HEAD

echo
echo "== 包内容抽查 =="
PYBIN="${PY:-$(command -v python3 || command -v python)}"
"$PYBIN" - "$OUT/$NAME.zip" <<'PYEOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
print(f"  文件数: {len(names)}  压缩包: {round(len(open(sys.argv[1],'rb').read())/1024/1024,2)} MB")
bad_dirs = ("papers/", "uploads/", "outputs/", "training_data/", "loras/",
            "data/", "cache/", "training_output/", ".git/")
hits = [n for n in names if any(d in n for d in bad_dirs)]
print("  数据/权重文件:", "无 ✓" if not hits else f"发现 {len(hits)} 个 ✗ -> {hits[:5]}")
secret = [n for n in names if n.endswith("settings.json")]
print("  settings.json:", "未包含 ✓" if not secret else "被包含 ✗")
wf = sorted(n.split("/")[-1] for n in names if "/workflow_config" in n or n.endswith("workflow_config.json"))
print(f"  预设工作流: {len(wf)} 个 -> {wf}")
PYEOF

echo
echo "== 制品与仓库一致性检查 =="
"$PYBIN" - "$OUT/$NAME.zip" <<'PYEOF2'
import subprocess, sys, zipfile, re
z = zipfile.ZipFile(sys.argv[1])
names = [n for n in z.namelist() if not n.endswith('/')]
head = set(subprocess.check_output(['git', '-c', 'core.quotePath=false',
                                      'ls-tree', '-r', 'HEAD', '--name-only'], text=True).splitlines())
extra = [n for n in names if n.split('/', 1)[-1] not in head]
# 只按明确的垃圾特征判：含中文标点、含 '=='、或含空格。
# 不用长度规则 —— 实测它会把合法文件（workflow_config_fast_analyze_data.json，39 字符）误判成垃圾。
bad = [n for n in names
       if re.search(r'[\uff08\uff09\u3001\uff0c\u3002\uff01\uff1f\uff1b\uff1a]', n)
       or '==' in n.split('/')[-1] or ' ' in n.split('/')[-1]]
print(f"  包内文件数: {len(names)}")
if extra:
    print("  ✗ 包内存在不在 git HEAD 的文件（说明打包混入了工作区垃圾）:")
    for e in extra[:10]:
        print("     ", e)
    sys.exit(3)
if bad:
    print("  ✗ 包内存在可疑文件名（像是一句话/含中文标点）:")
    for b in bad[:10]:
        print("     ", b)
    sys.exit(4)
print("  ✓ 制品与仓库一致，且无可疑文件名")
PYEOF2
if [ $? -ne 0 ]; then echo "发布闸门未通过，制品已生成但不建议发布"; exit 5; fi

echo "产物: $OUT/$NAME.zip"
echo "解压后：bash scripts/setup.sh && bash scripts/run.sh"

# ===== 发布前闸门：密钥 / 禁用文件 / 包内敏感物 =====
if [ -x "$(dirname "$0")/check_no_secrets.sh" ]; then
  echo
  echo "== 发布前闸门 =="
  "$(dirname "$0")/check_no_secrets.sh" "${ZIP:-}" || { echo "闸门未通过，发布中止 ✗"; exit 1; }
fi
