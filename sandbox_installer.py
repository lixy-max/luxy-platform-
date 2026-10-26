# -*- coding: utf-8 -*-
"""sandbox_installer.py —— 奇美拉 P3 v1：沙箱安装器（只处理"纯 wheel、无依赖、许可允许"的候选）

安全边界（P3 v1 刻意保守）：
  · **只装纯 Python wheel**（文件名含 py3-none-any / py2.py3-none-any）
  · **--no-deps**（不装依赖）；有必需依赖的包 → 拒绝（后续 P3.2 再支持）
  · **sha256 锁定**（--require-hashes + requirements 文件）
  · **--target 目录隔离**（不污染系统/项目 venv），运行时不联网
  · 先 dry-run 出计划，install 必须显式 approve=true
  · 安装后做 import smoke；成功才登记为 shadow 能力（executable=False：暂不自动路由）
  · 所有步骤写 audit jsonl

不做什么（诚实）：
  · 不 git clone / 不执行仓库脚本 / 不装 C 扩展 / 不装依赖
  · WSL 不是强隔离（同 VM）；这是"包级隔离"，不是安全沙箱（后续 P4 Docker）
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SANDBOX_DIR = Path(os.environ.get("SANDBOX_DIR", str(PROJECT_DIR / "data" / "sandbox")))
INSTALLED_FILE = SANDBOX_DIR / "installed.jsonl"
PY = os.environ.get("SANDBOX_PY", sys.executable)
PYPI_API = "https://pypi.org/pypi/%s/json"

LICENSE_ALLOW = ("mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "mpl-2.0",
                 "python software foundation license", "psf")
PURE_TAG_HINTS = ("py3-none-any", "py2.py3-none-any")
MAX_WHEEL_MB = float(os.environ.get("SANDBOX_MAX_WHEEL_MB", "50"))
# 已知包的调用契约（P4 v1；未知包不自动执行，只能显式传 args）
KNOWN_ARGS_SCHEMA = {
    "tabulate": {"positional": ["data"],
                 "data": "二维数组（list[list]）—— 位置参数",
                 "headers": "表头 list[str]（可选）",
                 "tablefmt": "表格样式（可选，如 grid/pipe）"},
}
INSTALL_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "180"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure():
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)


def _audit(rec):
    try:
        _ensure()
        with open(SANDBOX_DIR / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(rec, at=_now()), ensure_ascii=False) + "\n")
    except Exception:
        pass


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "luxy-chimera-sandbox/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _module_guess(pkg):
    """PyPI 包名 → import 名（启发式：-/_ 统一为 _；常见映射补丁）"""
    p = str(pkg or "").strip().lower().replace("-", "_")
    return p


# ───────────────────── 元数据 ─────────────────────
def pypi_meta(package):
    """取 PyPI 元数据；不存在返回 None"""
    try:
        d = _get_json(PYPI_API % package)
    except Exception:
        return None
    info = d.get("info") or {}
    urls = d.get("urls") or []
    wheels = [u for u in urls if str(u.get("filename") or "").endswith(".whl")]
    pure = [u for u in wheels
            if any(h in str(u.get("filename")) for h in PURE_TAG_HINTS)]
    pick = None
    for u in urls:
        if u.get("packagetype") == "bdist_wheel" and u.get("filename") == (pure[0].get("filename") if pure else None):
            pick = u
            break
    if pick is None and wheels:
        pick = wheels[0]
    deps_raw = info.get("requires_dist") or []
    # 只数"硬依赖"（不带 extra 标记）
    hard_deps = [x for x in deps_raw if "extra ==" not in str(x)]
    lic = str(info.get("license") or "").strip()
    classifiers = info.get("classifiers") or []
    if not lic:
        for c in classifiers:
            if str(c).lower().startswith("license ::"):
                lic = str(c).split("::")[-1].strip()
                break
    return {
        "package": package,
        "version": info.get("version"),
        "summary": str(info.get("summary") or "")[:200],
        "license": lic,
        "license_raw_spdx": info.get("license_expression") or "",
        "requires_dist": deps_raw,
        "hard_deps_count": len(hard_deps),
        "file": ({"filename": pick.get("filename"),
                  "url": pick.get("url"),
                  "sha256": (pick.get("digests") or {}).get("sha256"),
                  "size": pick.get("size"),
                  "kind": pick.get("packagetype")} if pick else None),
        "pure_wheel": bool(pure),
        "homepage": info.get("home_page") or "",
    }


def github_repo(repo_id):
    """GitHub 仓库元数据（stars/license/archived/pushed）"""
    try:
        d = _get_json("https://api.github.com/repos/%s" % repo_id)
    except Exception:
        return None
    lic = d.get("license") or {}
    return {"id": d.get("full_name"), "stars": d.get("stargazers_count"),
            "license": str(lic.get("spdx_id") or "").lower(),
            "archived": bool(d.get("archived")), "pushed_at": d.get("pushed_at"),
            "description": str(d.get("description") or "")[:200],
            "language": d.get("language")}


def map_repo_to_pypi(repo_id):
    """GitHub repo → PyPI 包名（启发式尝试若干候选名）"""
    name = str(repo_id or "").split("/")[-1].strip().lower()
    cands = [name, name.replace("_", "-"), re.sub(r"^(python|py)[-_]", "", name)]
    for c in list(cands):
        if c and c not in cands[:1]:
            cands.append(c.replace("-", "_"))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ───────────────────── 策略闸门 ─────────────────────
def _license_ok(lic):
    t = str(lic or "").strip().lower()
    if not t:
        return False, "许可字段为空"
    return any(a in t for a in LICENSE_ALLOW), "许可=%s" % lic


def dry_run(candidate, package=None):
    """对候选做安装前评估，返回计划（不执行任何安装）"""
    rid = str(candidate.get("id") or "")
    cand = {"candidate_id": candidate.get("candidate_id"), "repo": rid,
            "source": candidate.get("source"), "stars": candidate.get("stars"),
            "license": candidate.get("license") or ""}
    checks, risks = {}, []

    gh = github_repo(rid) if cand["source"] == "github" and rid else None
    if gh:
        checks["github"] = {"stars": gh.get("stars"), "archived": gh.get("archived"),
                            "pushed_at": gh.get("pushed_at"), "license": gh.get("license")}
        if gh.get("archived"):
            risks.append("仓库已归档（archived）")
        lic_ok, why = _license_ok(gh.get("license") or cand["license"])
        checks["license"] = {"ok": lic_ok, "detail": why}
        if not lic_ok:
            risks.append("许可不可自动批准：%s" % why)
    else:
        lic_ok, why = _license_ok(cand["license"])
        checks["license"] = {"ok": lic_ok, "detail": why}
        if not lic_ok:
            risks.append("许可不可自动批准：%s" % why)

    # PyPI 映射
    pypi_info = None
    tried = []
    names = [package] if package else map_repo_to_pypi(rid)
    for nm in names:
        tried.append(nm)
        meta = pypi_meta(nm)
        if meta:
            pypi_info = meta
            break
    if pypi_info:
        checks["pypi"] = {k: pypi_info.get(k) for k in
                          ("package", "version", "license", "hard_deps_count", "pure_wheel", "summary")}
        checks["pypi"]["file"] = pypi_info.get("file")
        if not pypi_info.get("pure_wheel"):
            risks.append("不是纯 Python wheel（含 C 扩展/平台 wheel）→ P3 v1 不装")
        if int(pypi_info.get("hard_deps_count") or 0) > 0:
            risks.append("有 %d 个必需依赖（--no-deps 不满足）→ P3 v1 不装"
                         % pypi_info.get("hard_deps_count"))
        f = pypi_info.get("file") or {}
        if not f.get("sha256"):
            risks.append("拿不到 wheel sha256 → 不能锁定")
        size_mb = float(f.get("size") or 0) / 1e6
        if size_mb > MAX_WHEEL_MB:
            risks.append("wheel 过大（%.1fMB > %.0fMB）" % (size_mb, MAX_WHEEL_MB))
        pypi_lic_ok, _ = _license_ok(pypi_info.get("license") or cand["license"])
        checks["pypi_license_ok"] = pypi_lic_ok
    else:
        risks.append("PyPI 上找不到对应包（P3 v1 只支持 PyPI 纯 wheel）")

    can_install = bool(pypi_info and pypi_info.get("pure_wheel") and (pypi_info.get("file") or {}).get("sha256")
                       and int(pypi_info.get("hard_deps_count") or 0) == 0
                       and not (gh and gh.get("archived"))
                       and not any("许可不可自动批准" in r for r in risks)
                       and not any("wheel 过大" in r for r in risks))
    plan = {
        "candidate": cand,
        "checks": checks,
        "risks": risks,
        "pypi_tried": tried,
        "install_ir": ([{"op": "pip_install",
                         "package": pypi_info["package"], "version": pypi_info["version"],
                         "wheel": (pypi_info.get("file") or {}).get("filename"),
                         "sha256": (pypi_info.get("file") or {}).get("sha256"),
                         "isolation": "pip --target（独立目录，不污染环境）",
                         "hash_lock": "--require-hashes",
                         "no_deps": True}]
                       if pypi_info else []),
        "sandbox": {"target_dir": str(SANDBOX_DIR / "site-packages" / _slug(rid or "cand")),
                    "runtime_network": False, "install_timeout_sec": INSTALL_TIMEOUT,
                    "isolation_level": "包级隔离（非强沙箱；WSL 同 VM）"},
        "can_install": can_install,
        "next": ("可执行 /chimera/{id}/install {approve:true}" if can_install
                 else "不满足 P3 v1 安装条件（见 risks）"),
    }
    return plan


def _slug(s):
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(s)).strip("-")[:80] or "cand"


def introspect_entry(target_dir, module):
    """安装后探测可用入口：优先 与模块/包同名的公共 callable，其次 run/main/convert/format"""
    code = (
        "import importlib, json, sys\n"
        "m = importlib.import_module(%r)\n"
        "names = [n for n in dir(m) if not n.startswith('_') and callable(getattr(m, n, None))]\n"
        "print(json.dumps({'names': names[:80]}))\n" % module
    )
    env = {"SANDBOX_TARGET": str(target_dir), "PATH": os.environ.get("PATH", ""),
           "HOME": str(SANDBOX_DIR / "home"), "LC_ALL": "C.UTF-8"}
    try:
        r = subprocess.run([PY, "-S", "-P", "-c",
                            "import os,sys; sys.path.insert(0, os.environ['SANDBOX_TARGET']);" + code],
                           capture_output=True, text=True, timeout=30, env=env)
        names = json.loads((r.stdout or "{}").strip().splitlines()[-1]).get("names") or []
    except Exception:
        return {"module": None, "function": None, "candidates": []}
    base = str(module or "")
    preferred = [base, base.replace("_", ""), "run", "main", "convert", "format", "render"]
    fn = None
    for p in preferred:
        if p in names:
            fn = p
            break
    if fn is None and names:
        fn = names[0]
    return {"module": base, "function": fn, "candidates": names[:20]}


# ───────────────────── 安装（显式 approve） ─────────────────────
def install(candidate, approve=False, package=None, target_dir=None):
    """执行安装；必须 approve=True。返回结果 dict"""
    if not approve:
        return {"ok": False, "error": "未批准：install 需显式 approve=true（政策闸门）"}
    plan = dry_run(candidate, package=package)
    if not plan.get("can_install"):
        return {"ok": False, "error": "不满足安装条件", "risks": plan.get("risks"),
                "checks": plan.get("checks")}
    ir = (plan.get("install_ir") or [{}])[0]
    pkg, ver, sha = ir.get("package"), ir.get("version"), ir.get("sha256")
    rid = str(candidate.get("id") or pkg)
    tgt = Path(target_dir or (SANDBOX_DIR / "site-packages" / _slug(rid)))
    _ensure()
    if tgt.exists():
        shutil.rmtree(tgt, ignore_errors=True)
    tgt.mkdir(parents=True, exist_ok=True)
    req = SANDBOX_DIR / ("req_%s.txt" % _slug(rid))
    req.write_text("%s==%s --hash=sha256:%s\n" % (pkg, ver, sha), encoding="utf-8")
    cmd = [PY, "-m", "pip", "install", "--no-deps", "--require-hashes",
           "--no-compile", "--disable-pip-version-check", "--target", str(tgt),
           "-r", str(req)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        _audit({"action": "install_timeout", "candidate": rid, "package": pkg})
        return {"ok": False, "error": "安装超时（%ss）" % INSTALL_TIMEOUT, "plan": plan}
    dur = round(time.time() - t0, 1)
    if r.returncode != 0:
        _audit({"action": "install_fail", "candidate": rid, "package": pkg,
                "rc": r.returncode, "stderr": (r.stderr or "")[-500:]})
        return {"ok": False, "error": "pip 安装失败 rc=%d" % r.returncode,
                "stderr": (r.stderr or "")[-800:], "plan": plan}

    # 包内容 hash（对安装后的目录做清单 hash，作为"制品指纹"）
    try:
        h = hashlib.sha256()
        files = sorted(p for p in tgt.rglob("*") if p.is_file())
        for p in files:
            h.update(str(p.relative_to(tgt)).encode())
            h.update(p.read_bytes())
        artifact_sha = h.hexdigest()
    except Exception:
        artifact_sha = ""

    # import smoke（隔离运行：PYTHONPATH=target，清除代理，禁网由流程保证）
    mod = _module_guess(pkg)
    smoke = {"module": mod, "ok": False, "version": None, "error": None}
    try:
        env = {"PYTHONPATH": str(tgt), "PATH": os.environ.get("PATH", ""),
               "HOME": str(SANDBOX_DIR / "home"), "NO_PROXY": "*", "http_proxy": "", "https_proxy": ""}
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        code = ("import importlib, json, sys\n"
                "m = importlib.import_module(%r)\n"
                "print(json.dumps({'version': str(getattr(m, '__version__', '')), 'ok': True}))\n" % mod)
        sr = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=60, env=env)
        if sr.returncode == 0:
            smoke.update(json.loads(sr.stdout.strip().splitlines()[-1]))
        else:
            smoke["error"] = (sr.stderr or "")[-300:]
    except Exception as e:
        smoke["error"] = str(e)[:200]

    entry = introspect_entry(tgt, mod) if smoke.get("ok") else {"module": mod, "function": None,
                                                                  "candidates": []}
    args_schema = KNOWN_ARGS_SCHEMA.get(pkg)
    rec = {"candidate_id": candidate.get("candidate_id"), "repo": rid,
           "package": pkg, "version": ver, "wheel_sha256": sha, "artifact_sha256": artifact_sha,
           "target_dir": str(tgt), "install_seconds": dur, "smoke": smoke,
           "entry": entry, "args_schema": args_schema,
           "python": sys.version.split()[0], "at": _now()}

    # 登记为 shadow 能力（executable=False：暂不自动路由）
    cap_id = None
    try:
        import capabilities as caps
        from capabilities import _load as _cl, _save as _cs
        cid = "ext:github:%s" % _slug(rid)
        reg = _cl()
        e = reg["capabilities"].get(cid)
        if e is None:
            # 未走奇美拉自动入库（例如手工候选）→ 这里补登记
            e = {
                "cap_id": cid, "name": str(candidate.get("name") or rid),
                "kind": "external", "lora": None, "lora_path": None, "base_model": None,
                "rank": None, "worker_port": 0, "io": {"input": "text", "output": "text"},
                "tags": ["external", "installed", "sandbox"],
                "status": "shadow", "compatible": False,
                "success_rate": None, "uses": 0, "last_used": None,
                "created_at": _now(), "source": "sandbox_installer",
                "source_kind": "github", "url": "", "stars": None, "license": "",
                "gate": "installed",
            }
            reg["capabilities"][cid] = e
        _executable = bool(entry.get("function"))
        _tags = list(e.get("tags") or [])
        for t in ["external", "installed", "sandbox", pkg, mod]:
            if t and t not in _tags:
                _tags.append(t)
        try:
            import capabilities as _caps2
            _summary = str(((plan.get("checks") or {}).get("pypi") or {}).get("summary") or "")
            for t in _caps2.tags_for_text(pkg + " " + _summary):
                if t not in _tags:
                    _tags.append(t)
        except Exception:
            pass
        e.update({"installed": True, "install_path": str(tgt), "executable": _executable,
                  "entry": {"module": entry.get("module"), "function": entry.get("function")},
                  "args_schema": args_schema, "tags": _tags,
                  "version": ver, "wheel_sha256": sha, "artifact_sha256": artifact_sha,
                  "smoke_ok": bool(smoke.get("ok")), "installed_at": _now(),
                  "status": "shadow",
                  "description": "%s（已沙箱安装，shadow；%s）" % (
                      pkg, ("可执行 " + str(entry.get("function")) if _executable
                            else "无自动入口，仅显式 args 调用"))})
        _cs(reg)
        cap_id = cid
    except Exception:
        pass
    rec["capability_id"] = cap_id
    try:
        with open(INSTALLED_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _audit({"action": "install_ok", "candidate": rid, "package": pkg, "version": ver,
            "artifact_sha256": artifact_sha, "smoke_ok": smoke.get("ok"), "capability_id": cap_id})
    return {"ok": True, "plan": plan, "installed": rec}


def list_installed(limit=100):
    if not INSTALLED_FILE.exists():
        return []
    out = []
    for ln in INSTALLED_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out[-int(limit):]
