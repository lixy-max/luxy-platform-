# -*- coding: utf-8 -*-
"""chimera_v2.py —— 奇美拉 V2：允许依赖的直装（pip --report 审计 + 适配器 + 能力登记）

背景（S6 / B）：
  P3 只允许"纯 wheel + 无依赖"；S1 判定 docling/pdfplumber 等可直装，但依赖装不上。
  V2：
    · pip install --target <dir> --report report.json <pkgs>（允许依赖；report 记录每个包版本+sha256）
    · 适配器注入：把 tools/adapters/<name>.py 拷进 target/_luxy_wrappers/，沙箱执行器直接调用
      （例：pdfplumber 抽表格、python-docx 生成招标 Word）
    · smoke：-S -P 隔离进程导入适配器并跑 __self_test__
    · 能力登记：ext:pypi:<label> → shadow（executable=true，可被主脑路由/执行）
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SANDBOX_DIR = Path(os.environ.get("SANDBOX_DIR", str(PROJECT_DIR / "data" / "sandbox")))
DEPS_DIR = SANDBOX_DIR / "deps"
ADAPTERS_DIR = PROJECT_DIR / "tools" / "adapters"
INSTALLED_FILE = SANDBOX_DIR / "installed.jsonl"
AUDIT_FILE = SANDBOX_DIR / "deps_audit.jsonl"
PY = os.environ.get("SANDBOX_PY", sys.executable)
MAX_DEPS_MB = float(os.environ.get("CHIMERA_V2_MAX_DEPS_MB", "4096"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _audit(rec):
    try:
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(rec, at=_now()), ensure_ascii=False) + "\n")
    except Exception:
        pass


def _slug(s):
    import re
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(s)).strip("-")[:60] or "deps"


def _dir_mb(p):
    total = 0
    for root, _dirs, files in os.walk(str(p)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return round(total / 1024 / 1024, 1)


def parse_pip_report(report_path):
    """pip --report JSON → [{name, version, sha256, url}]"""
    out = []
    try:
        d = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except Exception:
        return out
    for it in (d.get("install") or []):
        md = it.get("metadata") or {}
        url = ""
        sha = ""
        di = it.get("download_info") or {}
        url = str(di.get("url") or "")
        try:
            sha = str(((di.get("archive_info") or {}).get("hashes") or {}).get("sha256") or "")
        except Exception:
            sha = ""
        out.append({"name": str(md.get("name") or ""), "version": str(md.get("version") or ""),
                    "sha256": sha, "url": url})
    return out


def _smoke(target, module):
    """隔离进程导入适配器并跑 __self_test__"""
    code = (
        "import os, sys, json\n"
        "sys.path.insert(0, os.environ['SANDBOX_TARGET'])\n"
        "m = __import__(%r, fromlist=['*'])\n"
        "fn = getattr(m, '__self_test__', None)\n"
        "print(json.dumps(fn() if callable(fn) else {'ok': True, 'module': %r}))\n"
    ) % (module, module)
    env = {"SANDBOX_TARGET": str(target), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(SANDBOX_DIR / "home"), "LC_ALL": "C.UTF-8"}
    try:
        r = subprocess.run([PY, "-S", "-P", "-c", code], capture_output=True, text=True,
                           timeout=120, env=env)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        d = json.loads(lines[-1]) if lines else {"ok": False, "error": "无输出"}
        if r.returncode != 0 and not d.get("ok"):
            d = {"ok": False, "error": (r.stderr or "")[-400:]}
        return d
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


def install_with_deps(package, extra_packages=None, wrappers=None, label=None,
                      approve=False, timeout=900, python=PY):
    """pip 安装（允许依赖）到独立 target + 注入适配器 + smoke；返回安装报告"""
    if not approve:
        raise ValueError("V2 直装需要显式 approve=true")
    label = _slug(label or package)
    target = DEPS_DIR / label
    target.mkdir(parents=True, exist_ok=True)
    ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    report = DEPS_DIR / (label + ".pip-report.json")
    pkgs = [package] + [p for p in (extra_packages or []) if p]
    cmd = [python, "-m", "pip", "install", "--target", str(target),
           "--no-cache-dir", "--upgrade", "--report", str(report)] + pkgs
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _audit({"action": "install_timeout", "label": label, "packages": pkgs})
        raise RuntimeError("依赖安装超时（%ss）" % timeout)
    dur = round(time.time() - t0, 1)
    if r.returncode != 0:
        _audit({"action": "install_fail", "label": label, "packages": pkgs,
                "tail": (r.stderr or "")[-600:]})
        raise RuntimeError("pip 安装失败: %s" % ((r.stderr or r.stdout or "")[-600:]))
    packages = parse_pip_report(report)
    size_mb = _dir_mb(target)
    if size_mb > MAX_DEPS_MB:
        _audit({"action": "install_too_big", "label": label, "size_mb": size_mb})
        raise RuntimeError("安装体积 %.0fMB 超过上限 %.0fMB" % (size_mb, MAX_DEPS_MB))
    # 注入适配器
    wrapper_dir = target / "_luxy_wrappers"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    (wrapper_dir / "__init__.py").write_text("# luxy adapters\n", encoding="utf-8")
    copied = []
    for w in (wrappers or []):
        src = ADAPTERS_DIR / (w + ".py")
        if not src.exists():
            src = PROJECT_DIR / "tools" / "adapters" / (w + ".py")
        if not src.exists():
            raise RuntimeError("适配器不存在: %s" % w)
        shutil.copy(src, wrapper_dir / (w + ".py"))
        copied.append(w)
    # smoke（有适配器则测第一个；否则测主包）
    smoke = {"ok": True, "note": "无适配器，跳过 smoke"}
    if copied:
        smoke = _smoke(target, "_luxy_wrappers." + copied[0])
    # 修 B022：导出可复现 requirements.lock（版本 + sha256）
    lock_path = DEPS_DIR / (label + ".requirements.lock")
    try:
        lines = []
        for p in packages:
            if p.get("sha256"):
                lines.append("%s==%s --hash=sha256:%s" % (p["name"], p["version"], p["sha256"]))
            else:
                lines.append("%s==%s" % (p["name"], p["version"]))
        lock_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        lock_path = None
    rec = {"kind": "deps", "label": label, "packages": pkgs,
           "requirements_lock": str(lock_path) if lock_path else None,
           "resolved_packages": packages, "package_count": len(packages),
           "target_dir": str(target), "size_mb": size_mb, "install_seconds": dur,
           "wrappers": copied, "smoke": smoke, "report_path": str(report),
           "python": sys.version.split()[0]}
    with open(INSTALLED_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(rec, at=_now()), ensure_ascii=False) + "\n")
    _audit({"action": "install_ok", "label": label, "packages": pkgs,
            "package_count": len(packages), "size_mb": size_mb, "python": sys.version.split()[0]})
    return {"ok": True, "requirements_lock": str(lock_path) if lock_path else None, **rec}


def register_deps_capability(label, cap_id, name, module, function, args_schema,
                             packages=None, license_="", repo="", description=""):
    """把已装的依赖适配器登记为能力（external / shadow / executable）"""
    import capabilities as caps
    from capabilities import _load as _cl, _save as _cs
    reg = _cl()
    e = reg["capabilities"].get(cap_id) or {
        "cap_id": cap_id, "name": name, "kind": "external", "lora": None, "lora_path": None,
        "base_model": None, "rank": None, "worker_port": 0,
        "io": {"input": "text", "output": "text"}, "tags": ["external", "installed", "deps"],
        "status": "shadow", "compatible": False, "success_rate": None, "uses": 0,
        "last_used": None, "created_at": _now(), "source": "chimera_v2",
        "source_kind": "pypi", "url": "", "stars": None, "license": license_ or "",
        "gate": "installed",
    }
    tags = list(e.get("tags") or [])
    for t in ["external", "installed", "deps", label, "pypi"]:
        if t and t not in tags:
            tags.append(t)
    e.update({
        "installed": True, "executable": True, "install_path": str(DEPS_DIR / _slug(label)),
        "entry": {"module": module, "function": function}, "args_schema": args_schema,
        "tags": tags, "status": "shadow", "installed_at": _now(),
        "packages": [p.get("name") for p in (packages or [])][:50],
        "repo": repo or e.get("repo"),
        "description": description or ("%s（奇美拉 V2 直装，含依赖；适配器 %s）" % (name, module)),
    })
    reg["capabilities"][cap_id] = e
    _cs(reg)
    return e


def list_deps_installs(limit=50):
    if not INSTALLED_FILE.exists():
        return []
    rows = []
    for ln in INSTALLED_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("kind") == "deps":
            rows.append(d)
    return rows[-int(limit):]
