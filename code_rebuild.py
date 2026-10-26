# -*- coding: utf-8 -*-
"""code_rebuild.py —— 奇美拉 V2 第二档：代码借鉴 / 本地重建（rebuild）

适用：候选项目不能直接安装（需 Docker/服务化/重依赖/Go 后端），但其中某些纯逻辑值得借鉴。
流程（v1）：
  1. fetch_files：按文件拉取候选仓库源码（raw.githubusercontent，main/master 自适应）+ LICENSE
  2. analyze：AST 分析（imports/函数/类、重依赖识别、许可、规模）
  3. plan：给出"借哪些/重写什么/需要什么依赖/风险/工作量"的重建计划
  4. make_adapter：生成轻量本地实现（优先 14B 生成，失败回退内置模板），写入 tools/ported/<slug>/
  5. test：隔离进程导入自测
  6. register：登记 ext:rebuild:<slug> 能力（source=rebuild，executable=true）
产物：data/chimera/rebuilds/<slug>/（原始文件+分析+计划）、tools/ported/<slug>/（适配器）
"""
import ast
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
REBUILDS_DIR = PROJECT_DIR / "data" / "chimera" / "rebuilds"
PORTED_DIR = PROJECT_DIR / "tools" / "ported"
REBUILD_LOG = PROJECT_DIR / "data" / "chimera" / "rebuilds.jsonl"
PY = os.environ.get("SANDBOX_PY", sys.executable)

HEAVY_IMPORTS = {"torch", "numpy", "networkx", "tiktoken", "transformers", "tensorflow", "jax",
                 "openai", "anthropic", "elasticsearch", "pymilvus", "pymongo", "redis",
                 "selenium", "playwright", "cv2", "PIL", "scipy", "pandas", "faiss", "llama_index",
                 "langchain", "sentence_transformers", "docling", "unstructured"}
LICENSE_ALLOW = ("mit", "apache", "bsd", "isc", "mpl")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _slug(s):
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(s)).strip("-")[:70] or "rebuild"


def _http(url, timeout=30, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "luxy-chimera-rebuild/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if binary else r.read().decode(errors="ignore")


def _fetch_source(repo, path, timeout=15):
    """多镜像抓取单个文件：raw → jsDelivr → GitHub API(base64)"""
    import base64
    branches = ("main", "master")
    for br in branches:
        try:
            return _http("https://raw.githubusercontent.com/%s/%s/%s" % (repo, br, path),
                         timeout=timeout), "raw:%s" % br
        except Exception:
            continue
    for br in branches:
        try:
            return _http("https://cdn.jsdelivr.net/gh/%s@%s/%s" % (repo, br, path),
                         timeout=timeout), "jsdelivr:%s" % br
        except Exception:
            continue
    for br in branches:
        try:
            import json as _json
            d = _json.loads(_http(
                "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, br),
                timeout=timeout))
            if d.get("content"):
                return base64.b64decode(d["content"]).decode(errors="ignore"), "github_api:%s" % br
        except Exception:
            continue
    return None, None


def fetch_files(repo, paths, dest=None):
    """按文件抓取候选仓库源码 + LICENSE（多镜像回退：raw/jsDelivr/GitHub API）"""
    repo = str(repo).strip().strip("/")
    slug = _slug(repo.replace("/", "-"))
    root = Path(dest) if dest else (REBUILDS_DIR / slug)
    src_dir = root / "original"
    src_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in paths:
        data, used = _fetch_source(repo, p)
        if data is None:
            files.append({"path": p, "ok": False})
            continue
        out = src_dir / Path(p).name
        out.write_text(data, encoding="utf-8")
        files.append({"path": p, "ok": True, "local": str(out), "bytes": len(data),
                      "branch": used})
    lic = None
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
        txt, used = _fetch_source(repo, name)
        if txt:
            (root / "LICENSE").write_text(txt, encoding="utf-8")
            lic = {"file": name, "head": txt[:200], "via": used}
            break
    meta = {"repo": repo, "slug": slug, "root": str(root), "files": files,
            "license": lic, "fetched_at": _now()}
    (root / "fetch.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def analyze(root):
    """AST 分析已抓取源码：imports、函数/类、重依赖、许可"""
    root = Path(root)
    py_files = sorted((root / "original").glob("*.py")) if (root / "original").exists() else []
    imports, heavy, funcs, classes = set(), set(), [], []
    lines = 0
    for f in py_files:
        src = f.read_text(encoding="utf-8", errors="ignore")
        lines += len(src.splitlines())
        try:
            tree = ast.parse(src)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append({"name": node.name, "file": f.name,
                              "lineno": getattr(node, "lineno", 0),
                              "args": len(getattr(node.args, "args", []))})
            elif isinstance(node, ast.ClassDef):
                classes.append({"name": node.name, "file": f.name,
                                "lineno": getattr(node, "lineno", 0)})
    heavy = sorted(imports & HEAVY_IMPORTS)
    lic_txt = ""
    try:
        lic_txt = (root / "LICENSE").read_text(encoding="utf-8", errors="ignore")[:400]
    except Exception:
        pass
    lic_kind = "unknown"
    low = lic_txt.lower()
    for k in LICENSE_ALLOW:
        if k in low:
            lic_kind = k
            break
    return {"py_files": len(py_files), "lines": lines, "imports": sorted(imports),
            "heavy_imports": heavy, "functions": funcs[:60], "classes": classes[:30],
            "license_kind": lic_kind, "license_head": lic_txt[:200]}


def plan_rebuild(meta, analysis, goal=""):
    """重建计划：借什么/重写什么/依赖/风险/工作量"""
    pure_funcs = analysis.get("functions") or []
    heavy = analysis.get("heavy_imports") or []
    lic = analysis.get("license_kind")
    steps = [
        "1) 只借鉴纯算法/数据处理逻辑（不搬框架、不引服务依赖）",
        "2) 本地用标准库重写最小实现（保持输入输出契约）",
        "3) 隔离进程自测（无重依赖）",
        "4) 登记为外部能力（source=rebuild，shadow）并做端到端验证",
    ]
    risks = []
    if lic in ("unknown", None, ""):
        risks.append("许可不明：禁止自动移植，需人工确认")
    elif lic in ("gpl", "agpl"):
        risks.append("copyleft 许可：只能借鉴思路/独立重写，不能复制代码")
    if heavy:
        risks.append("源码依赖重库：%s（只借逻辑，不引入依赖）" % "、".join(heavy[:6]))
    effort = "低" if len(pure_funcs) <= 20 else ("中" if len(pure_funcs) <= 80 else "高")
    return {
        "goal": goal, "license": lic,
        "decision": "borrow_rebuild" if lic not in ("unknown",) else "needs_review",
        "borrow_candidates": [f for f in pure_funcs if any(
            k in f["name"].lower() for k in ("chunk", "split", "parse", "clean", "token", "merge",
                                             "extract", "normalize", "convert", "render"))][:20],
        "all_functions": len(pure_funcs), "heavy_imports": heavy,
        "steps": steps, "risks": risks, "effort": effort,
        "suggested_style": "standalone wrapper（纯函数 + 最小依赖）",
    }


_TEMPLATE_CHUNKER = '''# -*- coding: utf-8 -*-
"""重建适配器：递归字符分块（思路借鉴 LightRAG chunker/recursive_character，独立重写）

来源：https://github.com/HKUDS/LightRAG （MIT License，见 ATTRIBUTION.md）
本文件为**独立最小实现**：仅标准库，无外部依赖。
"""


def chunk(text, chunk_size=500, overlap=50, separators=None):
    """把长文本按分隔符优先级切成带重叠的块。

    思路（借鉴）：优先按段落/句子切分，再合并到不超过 chunk_size；
    相邻块之间保留 overlap 字符重叠，避免语义截断。
    """
    text = str(text or "")
    chunk_size = max(50, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size // 2))
    seps = separators or ["\\n\\n", "\\n", "。", "！", "？", "；", "，", " "]

    def _split(t):
        # 先按最高优先级分隔符切；若切不动（无分隔符）则硬切
        for sep in seps:
            if sep and sep in t:
                parts = [p for p in t.split(sep) if p]
                out = []
                for p in parts:
                    piece = p + sep
                    if len(piece) <= chunk_size:
                        out.append(piece)
                    else:
                        out.extend([piece[i:i + chunk_size]
                                    for i in range(0, len(piece), chunk_size)])
                return out
        return [t[i:i + chunk_size] for i in range(0, len(t), max(1, chunk_size - overlap))]

    pieces = _split(text)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > chunk_size:
            chunks.append(cur)
            cur = (cur[-overlap:] if overlap else "") + p
        else:
            cur += p
    if cur.strip():
        chunks.append(cur)
    return {"chunks": [c for c in chunks if c.strip()], "count": len([c for c in chunks if c.strip()]),
            "chunk_size": chunk_size, "overlap": overlap}


def __self_test__():
    r = chunk("第一段。第二段！第三段？" * 30, chunk_size=40, overlap=5)
    return {"ok": r["count"] > 0, "chunks": r["count"]}
'''


def make_adapter(meta, plan, name="chunker", entry="chunk", adapter_code=None, use_llm=False):
    """生成适配器目录（wrapper.py + ATTRIBUTION.md + LICENSE），并做导入自测"""
    slug = meta["slug"]
    out = PORTED_DIR / (slug + "-" + _slug(name))
    out.mkdir(parents=True, exist_ok=True)
    code = adapter_code
    source = "template"
    if not code and use_llm:
        code, source = _llm_adapter(meta, plan)
    if not code:
        code = _TEMPLATE_CHUNKER
        source = "template"
    try:
        compile(code, "wrapper.py", "exec")
    except SyntaxError as e:
        code = _TEMPLATE_CHUNKER
        source = "template(fallback: %s)" % str(e)[:80]
    (out / "wrapper.py").write_text(code, encoding="utf-8")
    lic = meta.get("license") or {}
    attr = ["# 代码借鉴与许可说明", "",
            "- 来源仓库：https://github.com/%s" % meta.get("repo"),
            "- 许可：%s" % (plan.get("license") or "unknown"),
            "- 借鉴文件：%s" % "、".join(f.get("path") for f in meta.get("files") or [] if f.get("ok")),
            "- 方式：**独立重写最小实现**（思路借鉴，未整文件复制框架代码）",
            "- 生成：%s by code_rebuild v1" % _now(), "",
            "原仓库许可文本见本目录 LICENSE。"]
    (out / "ATTRIBUTION.md").write_text("\n".join(attr), encoding="utf-8")
    lic_txt = lic.get("head") or ""
    if lic_txt:
        (out / "LICENSE").write_text(lic_txt, encoding="utf-8")
    # 隔离自测（-S -P，只放本目录）
    env = {"SANDBOX_TARGET": str(out), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(PORTED_DIR / ".home"), "LC_ALL": "C.UTF-8"}
    test_code = (
        "import os, sys, json\n"
        "sys.path.insert(0, os.environ['SANDBOX_TARGET'])\n"
        "import wrapper\n"
        "fn = getattr(wrapper, %r, None)\n"
        "r = fn('测试文本。' * 80) if callable(fn) else None\n"
        "print(json.dumps({'ok': callable(fn), 'result_type': type(r).__name__,\n"
        "                  'count': (r or {}).get('count') if isinstance(r, dict) else None},\n"
        "                 ensure_ascii=False))\n"
    ) % (entry,)
    try:
        t = subprocess.run([PY, "-S", "-P", "-c", test_code], capture_output=True, text=True,
                           timeout=120, env=env)
        lines = [x for x in (t.stdout or "").strip().splitlines() if x.strip()]
        smoke = json.loads(lines[-1]) if lines else {"ok": False, "stderr": (t.stderr or "")[-300:]}
    except Exception as e:
        smoke = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    return {"adapter_dir": str(out), "wrapper": str(out / "wrapper.py"),
            "attribution": str(out / "ATTRIBUTION.md"), "code_source": source,
            "smoke": smoke, "entry": entry}


def _llm_adapter(meta, plan):
    """让 14B 写适配器（失败返回 (None, reason)）"""
    try:
        import swarm
        snippets = []
        for f in (meta.get("files") or [])[:2]:
            if f.get("ok"):
                try:
                    snippets.append("# %s\n%s" % (f["path"],
                                                  Path(f["local"]).read_text(encoding="utf-8", errors="ignore")[:2500]))
                except Exception:
                    pass
        prompt = ("你是代码重建工程师。参考候选仓库（只借鉴思路，不要整文件复制）的片段，"
                  "用**仅标准库**写一个 Python 适配器 wrapper.py：暴露函数 %s，"
                  "实现目标：%s。代码必须可直接 import、无外部依赖，并含 __self_test__()。"
                  "只输出 Python 代码，不要解释。\n\n参考片段：\n%s"
                  % ("chunk(text, chunk_size=500, overlap=50)", plan.get("goal") or "递归字符分块",
                     "\n\n".join(snippets)[:6000]))
        code = swarm.ask_brain("你是严谨的代码工程师，只输出可运行 Python 代码。", prompt,
                               max_tokens=1600)
        code = str(code or "").strip()
        if "```" in code:
            code = code.split("```")[1]
            if code.startswith("python"):
                code = code[6:]
        if "def chunk" in code or "def run" in code:
            compile(code, "llm_adapter.py", "exec")
            return code, "llm(14B)"
    except Exception as e:
        return None, "llm_failed: %s" % str(e)[:120]
    return None, "llm_no_valid_code"


def register_rebuild_capability(meta, adapter, name, cap_id, args_schema, goal=""):
    """登记 ext:rebuild:<slug> 能力（install_path=适配器目录，走 sandbox_exec）"""
    import capabilities as caps
    from capabilities import _load as _cl, _save as _cs
    reg = _cl()
    e = reg["capabilities"].get(cap_id) or {
        "cap_id": cap_id, "name": name, "kind": "external", "lora": None, "lora_path": None,
        "base_model": None, "rank": None, "worker_port": 0,
        "io": {"input": "text", "output": "text"}, "tags": ["external", "installed", "rebuild"],
        "status": "shadow", "compatible": False, "success_rate": None, "uses": 0,
        "last_used": None, "created_at": _now(), "source": "code_rebuild",
        "source_kind": "github", "url": "https://github.com/%s" % meta.get("repo"),
        "stars": None, "license": str((meta.get("license") or {}).get("file") or ""),
        "gate": "installed",
    }
    tags = list(e.get("tags") or [])
    for t in ["external", "installed", "rebuild", meta.get("repo", "").split("/")[-1]]:
        if t and t not in tags:
            tags.append(t)
    e.update({"installed": True, "executable": True,
              "install_path": adapter["adapter_dir"],
              "entry": {"module": "wrapper", "function": adapter.get("entry") or "chunk"},
              "args_schema": args_schema, "tags": tags, "status": "shadow",
              "installed_at": _now(), "description": goal or ("借鉴重建：%s" % meta.get("repo")),
              "attribution": adapter.get("attribution")})
    reg["capabilities"][cap_id] = e
    _cs(reg)
    return e


def rebuild(repo, files, goal="", name=None, entry="chunk", cap_id=None, args_schema=None,
            approve=False, use_llm=False, adapter_code=None):
    """完整重建：抓取→分析→计划→适配器→自测→登记"""
    if not approve:
        raise ValueError("代码重建需要显式 approve=true")
    meta = fetch_files(repo, files)
    root = Path(meta["root"])
    analysis = analyze(root)
    plan = plan_rebuild(meta, analysis, goal=goal)
    (root / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if plan["decision"] != "borrow_rebuild":
        return {"ok": False, "reason": "许可不明，需人工确认（%s）" % plan.get("license"),
                "meta": meta, "analysis": analysis, "plan": plan}
    adapter = make_adapter(meta, plan, name=name or "chunker", entry=entry,
                           adapter_code=adapter_code, use_llm=use_llm)
    cap = None
    if adapter["smoke"].get("ok") and cap_id:
        cap = register_rebuild_capability(
            meta, adapter, name=name or "rebuild-chunker", cap_id=cap_id,
            args_schema=args_schema or {"positional": ["text"],
                                        "chunk_size": "块大小（可选）", "overlap": "重叠（可选）"},
            goal=goal)
    rec = {"repo": repo, "files": [f.get("path") for f in meta.get("files") or []],
           "goal": goal, "plan": plan, "adapter": adapter,
           "capability_id": (cap or {}).get("cap_id"), "at": _now()}
    REBUILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(REBUILD_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": bool(adapter["smoke"].get("ok")), **rec}


def list_rebuilds(limit=30):
    if not REBUILD_LOG.exists():
        return []
    rows = []
    for ln in REBUILD_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows[-int(limit):]
