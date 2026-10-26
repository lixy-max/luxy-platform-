# -*- coding: utf-8 -*-
"""capabilities.py —— 工蜂能力注册表（P2.3）

设计要点（2026-10-26）：
  · **一个 LoRA = 一个单步能力**（能力解耦）；多步任务由主脑编排
  · 注册表 = 唯一真相：data/capabilities/registry.json
  · 能力条目字段：cap_id / name / description / lora / worker_port / io / status /
    version / tags / success_rate / uses / last_used / created_at
  · 能力装载：调用工蜂 vLLM /v1/load_lora_adapter（动态装载/卸载）
  · 路由：match(query) 用名称/描述/标签关键词打分（轻量，不引入 embedding 依赖）
  · **不自动训练**：训练由飞轮/人工触发；本模块只登记与调度
"""
import json
import os
import re
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = Path(os.environ.get(
    "CAPABILITY_REGISTRY",
    str(PROJECT_DIR / "data" / "capabilities" / "registry.json")))
LORA_DIR = PROJECT_DIR / "loras"
# P2.3: 训练产物目录也作为 LoRA 来源（训练成功后即可注册为单步能力）
TRAINING_DIR = PROJECT_DIR / "training_output"
DEFAULT_WORKER_PORT = int(os.environ.get("WORKER_PORT", "8002"))
WORKER_BASE_MODEL = os.environ.get("WORKER_BASE_MODEL", "Qwen2.5-7B-Instruct")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load():
    if REGISTRY_PATH.exists():
        try:
            d = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("capabilities"), dict):
                return d
        except Exception:
            pass
    return {"version": 1, "updated_at": _now(), "capabilities": {}}


def _save(reg):
    reg["updated_at"] = _now()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)
    return reg


def list_capabilities(status=None):
    reg = _load()
    caps = list(reg["capabilities"].values())
    if status:
        caps = [c for c in caps if c.get("status") == status]
    return sorted(caps, key=lambda c: (c.get("status") != "active", c.get("cap_id", "")))


def get_capability(cap_id):
    """按 cap_id 查能力；容忍裸名（如 t1_law_lora_v1 → lora:t1_law_lora_v1）"""
    caps = _load()["capabilities"]
    cid = str(cap_id or "").strip()
    if cid in caps:
        return caps[cid]
    if ("lora:" + cid) in caps:
        return caps["lora:" + cid]
    low = cid.lower()
    for k, v in caps.items():
        if k.lower() == low or str(v.get("name") or "").lower() == low:
            return v
        if k.lower() == ("lora:" + low):
            return v
    return None


def _slug(s):
    return re.sub(r"[^0-9A-Za-z_\-]+", "-", str(s)).strip("-")[:64]


# 名称 → 领域标签（用于能力路由；扩展时在这里加）
_DOMAIN_HINTS = {
    "agri": ["农业", "农学", "种植", "施肥"],
    "law": ["法律", "法规", "合同", "诉讼"],
    "med": ["医学", "医疗", "诊疗", "用药"],
    "medical": ["医学", "医疗", "诊疗", "用药"],
    "cs": ["计算机", "客服", "编程"],
    "poetry": ["诗歌", "文学", "古诗"],
    "semantic": ["语义理解", "语义", "相似度", "分类"],
    "secretary": ["办公", "秘书", "公文", "写作"],
    "paper": ["论文", "学术", "文献"],
    "math": ["数学", "计算"],
    "trans": ["翻译"],
    "code": ["代码", "编程"],
    "expert": ["专家"],
    "table": ["表格", "表格化", "对齐", "格式化"],
    "tabular": ["表格", "表格化", "对齐"],
    "pdf": ["PDF", "文档", "抽取", "解析"],
    "browser": ["浏览器", "网页", "自动化"],
    "ocr": ["OCR", "文字识别", "图像"],
    "format": ["格式化", "排版"],
    "parse": ["解析", "抽取"],
    "convert": ["转换", "格式转换"],
    "image": ["图像", "图片"],
    "audio": ["音频", "语音"],
    "video": ["视频"],
    "crawl": ["爬虫", "抓取", "网页"],
}


def _guess_tags(dir_name):
    """从 LoRA 目录名推断领域标签（如 agri_expert_v1 → 农业/农学/种植/施肥）"""
    toks = [t for t in str(dir_name or "").lower().replace("-", "_").split("_") if t]
    tags = []
    for t in toks:
        for kw, vals in _DOMAIN_HINTS.items():
            if kw == t or (len(t) > 3 and kw in t):
                tags.extend(vals)
    if not tags:
        tags = [str(dir_name)]
    # 去重保序
    out, seen = [], set()
    for x in tags:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def tags_for_text(text):
    """从任意文本（包名/描述/别名）推断中文领域标签（用于外部能力路由）"""
    t = str(text or "").lower()
    tags = []
    for kw, vals in _DOMAIN_HINTS.items():
        if kw and kw in t:
            for v in vals:
                if v not in tags:
                    tags.append(v)
    return tags


def _readme_snippet(d, limit=160):
    """读 LoRA 目录 README 的前若干行作为能力描述补充（去 front-matter / HTML 注释）"""
    for name in ("README.md", "README.txt"):
        f = d / name
        if not f.exists():
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
        raw = txt.splitlines()
        # 去 YAML front-matter（README 顶部 --- ... ---）
        if raw and raw[0].strip() == "---":
            try:
                endi = next(i for i in range(1, len(raw)) if raw[i].strip() == "---")
                raw = raw[endi + 1:]
            except StopIteration:
                pass
        lines = [ln.strip() for ln in raw if ln.strip() and not ln.strip().startswith("#")]
        if lines:
            return " ".join(lines)[:limit]
    return ""


def _scan_loras(roots=None):
    """扫描 LoRA 目录（loras/ + training_output/），只收根目录带 adapter_config 的

    training_output/<run>/ 的根目录才是最终 LoRA；checkpoint-XX 子目录跳过。
    """
    out = []
    roots = roots or [LORA_DIR, TRAINING_DIR]
    for root in roots:
        if not Path(root).is_dir():
            continue
        for d in sorted(Path(root).iterdir()):
            if not d.is_dir():
                continue
            cfg = d / "adapter_config.json"
            weight = d / "adapter_model.safetensors"
            if not cfg.exists() or not weight.exists():
                continue
            base = ""
            r = 16
            try:
                c = json.loads(cfg.read_text(encoding="utf-8"))
                base = str(c.get("base_model_name_or_path") or "")
                r = int(c.get("r") or 16)
            except Exception:
                pass
            out.append({
                "dir": d.name,
                "path": str(d),
                "root": Path(root).name,
                "base_model": base,
                "rank": r,
                "size_mb": round(weight.stat().st_size / 1e6) if weight.exists() else 0,
                "tags": _guess_tags(d.name),
                "readme": _readme_snippet(d),
            })
    return out


def register_from_loras(worker_port=None, overwrite=False):
    """把 loras/ 里的 LoRA 登记为单步能力（已存在则跳过，overwrite=True 更新路径/端口）"""
    reg = _load()
    caps = reg["capabilities"]
    port = int(worker_port or DEFAULT_WORKER_PORT)
    added, updated = [], []
    for item in _scan_loras():
        cap_id = "lora:" + _slug(item["dir"])
        if cap_id in caps and not overwrite:
            continue
        _tags = list(item.get("tags") or [])
        _base = str(item.get("base_model") or "")
        _compat = (not WORKER_BASE_MODEL) or (WORKER_BASE_MODEL.lower() in _base.lower())
        if not _compat:
            _tags = _tags + ["incompatible"]
        _desc = "单步能力（LoRA）：%s，rank=%d" % (item["dir"], item["rank"])
        if item.get("readme"):
            _desc += "；" + str(item["readme"])[:120]
        if _tags:
            _desc += "；标签：" + "、".join(_tags[:8])
        entry = {
            "cap_id": cap_id,
            "name": item["dir"],
            "description": _desc,
            "kind": "lora",
            "lora": item["dir"],
            "lora_path": item["path"],
            "base_model": item["base_model"],
            "rank": item["rank"],
            "worker_port": port,
            "io": {"input": "text", "output": "text"},
            "tags": ["lora", "single-step"] + _tags,
            "status": "shadow" if _compat else "disabled",   # 基座不兼容直接 disabled
            "compatible": _compat,
            "version": "v1",
            "success_rate": None,
            "uses": 0,
            "last_used": None,
            "created_at": _now(),
            "source": "scan:loras",
        }
        if cap_id in caps:
            caps[cap_id].update({k: entry[k] for k in ("name", "description", "lora_path", "worker_port", "base_model", "rank", "tags", "io", "kind", "compatible", "status")})
            updated.append(cap_id)
        else:
            caps[cap_id] = entry
            added.append(cap_id)
    _save(reg)
    return {"added": added, "updated": updated, "total": len(caps)}


def register_proposed_capability(cand, gap="", plan_id=""):
    """奇美拉闭环：把外部候选自动入库为能力（status=proposed，不参与路由）

    等 P3 沙箱安装 + 影子验证后，再晋级 shadow/active。
    """
    reg = _load()
    caps = reg["capabilities"]
    src = str(cand.get("source") or "?")
    cid = "ext:%s:%s" % (src, _slug(cand.get("id") or ""))
    now = _now()
    if cid in caps:
        caps[cid].update({"last_seen": now, "gap": str(gap)[:120],
                          "plan_id": str(plan_id), "gate": str(cand.get("gate") or "")})
        _save(reg)
        return caps[cid]
    entry = {
        "cap_id": cid,
        "name": str(cand.get("id") or ""),
        "description": "外部候选（奇美拉）：%s；⭐%s；许可=%s；缺口=%s" % (
            str(cand.get("desc") or "")[:100], cand.get("stars"),
            cand.get("license") or "unknown", str(gap)[:60]),
        "kind": "external",
        "lora": None, "lora_path": None, "base_model": None, "rank": None,
        "worker_port": 0,
        "io": {"input": "text", "output": "text"},
        "tags": ["external", "chimera", "proposed"],
        "status": "proposed",
        "compatible": False,
        "success_rate": None, "uses": 0, "last_used": None,
        "created_at": now, "source": "chimera",
        "source_kind": src, "url": cand.get("url") or "",
        "stars": cand.get("stars"), "license": cand.get("license") or "",
        "gap": str(gap)[:120], "plan_id": str(plan_id),
        "gate": str(cand.get("gate") or ""),
    }
    caps[cid] = entry
    _save(reg)
    return entry


def _tokens(s):
    s = str(s or "").lower()
    toks = set(re.findall(r"[a-z0-9_]{2,}", s))
    toks |= set(re.findall(r"[\u4e00-\u9fff]{2,}", s))
    return toks


def match(query, top_k=3, only_active=False):
    """按名称/描述/标签关键词匹配能力（轻量路由；不依赖 embedding）"""
    q = _tokens(query)
    if not q:
        return []
    caps = list_capabilities(status="active" if only_active else None)
    if not only_active:
        # 只路由 active/shadow；proposed（外部候选）与 disabled 一律不参与 ✓
        caps = [c for c in caps if c.get("status") in ("active", "shadow")]
    scored = []
    for c in caps:
        text = " ".join([str(c.get("name") or ""), str(c.get("description") or ""),
                         " ".join(c.get("tags") or [])])
        t = _tokens(text)
        if not t:
            continue
        hit = len(q & t)
        if hit <= 0:
            continue
        # 名称命中加权；成功率/使用量做轻微加权
        name_hit = 1 if (q & _tokens(c.get("name"))) else 0
        score = hit + 1.5 * name_hit
        sr = c.get("success_rate")
        if isinstance(sr, (int, float)):
            score += sr * 0.5
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], x[1].get("cap_id", "")))
    return [dict(c, match_score=round(s, 2)) for s, c in scored[:int(top_k)]]


def set_status(cap_id, status):
    reg = _load()
    c = reg["capabilities"].get(str(cap_id))
    if not c:
        raise ValueError("capability 不存在: %s" % cap_id)
    if status not in ("proposed", "approved_pending_install", "shadow", "active", "disabled"):
        raise ValueError("status 必须是 proposed|approved_pending_install|shadow|active|disabled")
    c["status"] = status
    c["updated_at"] = _now()
    _save(reg)
    return c


def record_use(cap_id, success=True):
    reg = _load()
    c = reg["capabilities"].get(str(cap_id))
    if not c:
        raise ValueError("capability 不存在: %s" % cap_id)
    n = int(c.get("uses") or 0)
    sr = c.get("success_rate")
    sr = 1.0 if sr is None else float(sr)
    new_sr = (sr * n + (1.0 if success else 0.0)) / (n + 1) if n > 0 else (1.0 if success else 0.0)
    c["uses"] = n + 1
    c["success_rate"] = round(new_sr, 4)
    c["last_used"] = _now()
    _save(reg)
    # P5：影子期达标自动晋级（默认 10 次且成功率 ≥0.9；可用 env 调整）
    try:
        import governance as _gov
        _gov.maybe_promote(cap_id)
    except Exception:
        pass
    return c


def summary():
    caps = list_capabilities()
    by = {}
    for c in caps:
        by[c.get("status") or "unknown"] = by.get(c.get("status") or "unknown", 0) + 1
    return {"total": len(caps), "by_status": by,
            "active": [c["cap_id"] for c in caps if c.get("status") == "active"],
            "registry_path": str(REGISTRY_PATH)}
