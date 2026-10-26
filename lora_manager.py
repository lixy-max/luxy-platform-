import uvicorn
import os
import pathlib
import sys
import re
import shutil
import zipfile
import tempfile
import uuid
import json as json_lib
import contextvars
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import vllm_manager
import db
import kb

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any

import workflow_engine as we
from workflow_engine import WorkflowPreflightError
app = FastAPI(title="LoRA 工作台 API")


@app.exception_handler(WorkflowPreflightError)
async def preflight_exception_handler(request, exc):
    """工作流预检失败（拓扑 / 配置 / LoRA 不兼容）-> 400 + 明细。

    以前 workflow_engine 直接 sys.exit(1/2/3)：命令行里没问题，
    但在服务端它是"线程静默结束"，调用方只拿到空结果、不知道发生了什么。
    改成异常后在这里统一转成可读的错误响应。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={
        "detail": exc.message,
        "code": exc.code,
        "details": exc.details,
    })

# CORS 只放行本机页面。
# 原先 allow_origins=["*"] 意味着：你浏览器里打开的任意一个网页，
# 都能读取 127.0.0.1:8001 的响应（包括 /settings），明文 API Key 会因此泄露。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 路径 ============
PROJECT_DIR = Path(__file__).resolve().parent

# ★ 版本号（2026-10-22）：发布包带 VERSION 文件；运行态 /health 与 /version 都返回它
def _read_version():
    try:
        _v = (PROJECT_DIR / "VERSION").read_text(encoding="utf-8").strip()
        return _v or "unknown"
    except Exception:
        return "unknown"


_LUX_VERSION = _read_version()


def models_dir():
    """模型根目录：优先环境变量 MODELS_DIR，其次用户家目录下的 models。

    开源移植用：原来写死 /home/<user>/models，换台机器就找不到模型。
    Path.home() 在本机等于 /home/<user>，所以**本机默认值与原来完全一致**。
    """
    import os as _os
    env = _os.environ.get("MODELS_DIR", "").strip()
    return Path(env) if env else (Path.home() / "models")


def _default_base_model_path():
    """默认底座模型路径（可用 MODELS_DIR / BASE_MODEL_PATH 覆盖）。"""
    import os as _os
    env = _os.environ.get("BASE_MODEL_PATH", "").strip()
    if env:
        return env
    return str(models_dir() / "Qwen2.5-7B-Instruct-AWQ")
SETTINGS_FILE = PROJECT_DIR / "settings.json"
WORKFLOW_CONFIG_FILE = PROJECT_DIR / "workflow_config.json"
LORA_DIR_DEFAULT = Path.home() / "loras"
OUTPUT_DIR = PROJECT_DIR / "outputs"
UPLOAD_DIR = PROJECT_DIR / "uploads"
TRAINING_DATA_DIR = PROJECT_DIR / "training_data"
TRAINING_OUTPUT_DIR = PROJECT_DIR / "training_output"
VENV_PYTHON = Path.home() / ".venv" / "bin" / "python"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TRAINING_DATA_DIR, exist_ok=True)
os.makedirs(TRAINING_OUTPUT_DIR, exist_ok=True)

workflow_tasks = {}
vllm_switch_tasks = {}
training_tasks = {}

# ============ 全局设置 ============
DEFAULT_SETTINGS = {
    "base_model_path": _default_base_model_path(),
    "lora_dir": str(LORA_DIR_DEFAULT),
    "vllm_chat_api": "http://localhost:8000/v1/chat/completions",
    "lora_manager_api": "http://localhost:8001",
    "venv_python": str(VENV_PYTHON),
    "providers": {
        "vllm": {"api_base": "http://127.0.0.1:8000/v1", "model": "", "api_key": ""},
        "ollama": {"api_base": "http://127.0.0.1:11434", "model": "qwen2.5:7b", "api_key": ""},
        "openai": {"api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": ""}
    }
}


# ★ 修（2026-10-22 安全债）：API Key 支持环境变量注入，避免又填回明文 json
#   优先级: <PROVIDER>_API_KEY 环境变量 > settings.json 里的 api_key
_ENV_KEY_MAP = {
    "openai": ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
    "ollama": ("OLLAMA_API_KEY",),
    "vllm": ("VLLM_API_KEY",),
}


def _apply_env_api_keys(cfg: dict) -> dict:
    """把环境变量里的 Key 覆盖到配置（不落盘、不进 git）"""
    import os as _os6
    try:
        prov = cfg.get("providers") or {}
        for name, envs in _ENV_KEY_MAP.items():
            for e in envs:
                v = str(_os6.environ.get(e) or "").strip()
                if v:
                    prov.setdefault(name, {})
                    prov[name]["api_key"] = v
                    prov[name]["key_source"] = "env:" + e
                    break
    except Exception:
        pass
    return cfg


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json_lib.load(f)
            merged = {**DEFAULT_SETTINGS, **data}
            if "providers" in data:
                for pname, pcfg in DEFAULT_SETTINGS.get("providers", {}).items():
                    if pname not in merged["providers"]:
                        merged["providers"][pname] = dict(pcfg)
                    else:
                        merged["providers"][pname] = {**pcfg, **merged["providers"][pname]}
            # ★ 修（2026-10-22 安全债）：Key 支持环境变量注入（不落盘、不进 git）
            return _apply_env_api_keys(merged)


        except Exception:
            pass
    return _apply_env_api_keys(dict(DEFAULT_SETTINGS))


def save_settings(data):
    merged = {**load_settings(), **data}
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json_lib.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def _kb_session_mounts(st, session_id=None):
    """P2.1（2026-10-26）: 会话级 KB 挂载优先，退回全局默认。

    settings.kb_mounts = {"<session_id>": ["kb_a", ...]}  ← 会话隔离
    settings.kb_mounted = [...]                            ← 全局默认（旧行为，兼容）
    """
    st = st or {}
    sid = str(session_id or "").strip()
    if sid:
        _m = (st.get("kb_mounts") or {}).get(sid)
        if isinstance(_m, list) and _m:
            return [str(x) for x in _m if str(x).strip()]
    return [str(x) for x in (st.get("kb_mounted") or []) if str(x).strip()]


def get_lora_dir():
    return Path(load_settings()["lora_dir"])


# ============ 数据模型 ============
class LoadRequest(BaseModel):
    lora_name: str
    lora_path: str | None = None
    force: bool = False


class UnloadRequest(BaseModel):
    lora_name: str


class SettingsModel(BaseModel):
    base_model_path: str | None = None
    lora_dir: str | None = None
    vllm_chat_api: str | None = None
    lora_manager_api: str | None = None
    venv_python: str | None = None


class VllmStartRequest(BaseModel):
    model_path: str
    port: int = 8000
    # None = 未指定，交给 vllm_manager 按权重+KV 需求自动推导
    # （写死 16384 会把自动推导出来的更大窗口盖掉）
    max_model_len: int | None = None
    gpu_memory_utilization: float | None = None


class VllmSwitchRequest(BaseModel):
    model_name: str
    max_model_len: int | None = None
    gpu_memory_utilization: float | None = None


class TrainRequest(BaseModel):
    lora_name: str
    task_desc: str = ""
    format_hint: str = ""
    data_count: int = 100
    base_model: str | None = None
    provider_type: str = "vllm"
    provider_config: dict = {}
    auto_restart_vllm: bool = True
    dataset_file: str | None = None
    review_before_train: bool = False
    # ★ H5-v2: 显式指定 LoRA rank（做"容量墙 vs 数据不足"对照 ✓ GUIDE §1.2）
    #   不传 ⇒ 由 train_lora.recommend_rank(data_count) 自动决定 ✓ 行为不变 ✓
    lora_rank: int | None = None
    lora_epochs: int | None = None


class ConfirmTrainingRequest(BaseModel):
    action: str = "continue"


class SaveDatasetRequest(BaseModel):
    items: list
class ProviderConfig(BaseModel):
    api_base: str = ""
    model: str = ""
    api_key: str = ""


class OpenOutputRequest(BaseModel):
    filename: str | None = None


class HistoryMetaRequest(BaseModel):
    tags: list | None = None
    notes: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    provider: str = "vllm"
    lora: str | None = None
    model_override: str | None = None
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 2048
    kb: dict | None = None
    session_id: str | None = None   # P2.1: 会话级 KB 挂载隔离（不传=旧行为）
    # 学习增强（阶段2）: 不传 = 行为与以前完全一致 ✓
    #   {enabled, timeout_sec, escalate_model, escalate_provider, distill_model}
    learn: dict | None = None


# ============ 对话会话持久化模型 ============
class ChatSessionCreate(BaseModel):
    title: str | None = None
    mode: str = "chat"
    model: str | None = None


class ChatMessageIn(BaseModel):
    role: str
    content: str = ""
    kind: str = "text"
    model: str | None = None
    tool_name: str | None = None
    tool_status: str | None = None
    tool_args: Any = None
    tool_result: Any = None


class ChatAppendRequest(BaseModel):
    messages: list[ChatMessageIn]


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    mode: str | None = None
    model: str | None = None


# ============ 工具 ============
VLLM_BASE = "http://localhost:8000"


def scan_loras():
    lora_dir = get_lora_dir()
    loras = []
    if not lora_dir.exists():
        return loras
    for name in os.listdir(lora_dir):
        path = lora_dir / name
        if path.is_dir() and (path / "adapter_config.json").exists():
            loras.append({"name": name, "path": str(path)})
    return loras


def get_loaded_loras():
    try:
        resp = requests.get(f"{VLLM_BASE}/v1/models", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            return [m["id"] for m in models if not m["id"].startswith("/")]
    except Exception:
        pass
    return []


def get_vllm_base_model():
    try:
        resp = requests.get(f"{VLLM_BASE}/v1/models", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            for m in models:
                if m["id"].startswith("/"):
                    return m["id"]
    except Exception:
        pass
    return None


# ============ PDF 解析 ============
COLUMN_GUTTER_MIN_WORDS = 8      # 词太少不做分栏判断（避免误判）
OCR_MAX_PAGES = 40               # OCR 页数上限（扫描版论文通常 10-20 页）
OCR_DPI = 200


def _lines_from_words(words):
    """把词按 y 聚成行，行内按 x 排序（同一行的判定用 3pt 容差）。"""
    rows = {}
    for w in words:
        rows.setdefault(round(float(w["top"]) / 3.0), []).append(w)
    return [sorted(rows[k], key=lambda w: float(w["x0"])) for k in sorted(rows)]


def _detect_gutter(lines, page_width):
    """找"中缝"：页宽中部做 x 中心直方图，取密度最低的一条**内部**竖带。

    返回 gutter 的 x 坐标；判断不出（单栏 / 词太少 / 低谷不明显）时返回 None。
    **纯函数**，便于单测。

    关键教训（第一版写错的地方）：不能直接取"中部最低 bin"——
      1) 最低点常常落在搜索区间的**边缘**（那里本来就没多少词），不是真中缝；
      2) k 落在边缘时 left/right 之一退化为 0，`<= 0.25 * max(...)` 判据**恒真**，
         于是单栏页也会被判成双栏 → 真实 PDF 上直接掉内容（句数 485→256）。
    所以这里：**只允许内部 bin**、两侧都必须有足量词、并且用 min 而非 max 做比较。
    """
    mids = [(_f(w, "x0") + _f(w, "x1")) / 2.0 for ws in lines for w in ws]
    if len(mids) < COLUMN_GUTTER_MIN_WORDS or not page_width:
        return None
    lo, hi, bins = page_width * 0.32, page_width * 0.68, 18
    step = (hi - lo) / bins
    counts = [0] * bins
    for m in mids:
        if lo <= m < hi:
            counts[int((m - lo) / step)] += 1
    total = sum(counts)
    if total < COLUMN_GUTTER_MIN_WORDS:
        return None
    # 两侧各自至少要有 25% 的词，才可能是"真两栏"
    cands = [k for k in range(1, bins - 1)
             if sum(counts[:k]) >= 0.25 * total and sum(counts[k + 1:]) >= 0.25 * total]
    if not cands:
        return None
    k = min(cands, key=lambda i: counts[i])
    left = sum(counts[:k]) / max(k, 1)
    right = sum(counts[k + 1:]) / max(len(counts) - k - 1, 1)
    # 低谷必须明显：同时低于两侧平均（用 min 更严）
    if left <= 0 or right <= 0 or counts[k] > 0.30 * min(left, right):
        return None
    # 取"空区中心"而不是空区左边缘 —— 否则切分点会落进左栏的词区里，
    # 把左栏右半的词误判到右栏（第一版返回 234 就是这么来的，正确应约 300）
    a = k
    while a - 1 >= 1 and counts[a - 1] <= max(counts[k], 1):
        a -= 1
    b = k
    while b + 1 <= bins - 2 and counts[b + 1] <= max(counts[k], 1):
        b += 1
    return lo + ((a + b) / 2.0 + 0.5) * step


def _f(w, key):
    """词字典里取数值字段（pdfplumber 给的是 float，但容错一下）。"""
    try:
        return float(w.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _join_line(ws):
    return " ".join(str(w.get("text") or "") for w in ws).strip()


def text_layout_aware(lines, page_width):
    """双栏感知拼装：左栏整体 → 右栏整体；跨中缝的通栏行作为分栏边界。

    纯函数（吃 lines + 页宽），返回文本或 None（判不出双栏时让调用方回退 extract_text）。
    """
    gutter = _detect_gutter(lines, page_width)
    if not gutter:
        return None
    left, right, out = [], [], []

    def flush():
        if left:
            out.extend(left)
            left.clear()
        if right:
            out.extend(right)
            right.clear()

    for ws in lines:
        if not ws:
            continue
        spans = any(_f(w, "x0") < gutter < _f(w, "x1") for w in ws)
        mid = sum((_f(w, "x0") + _f(w, "x1")) / 2.0 for w in ws) / len(ws)
        txt = _join_line(ws)
        if not txt:
            continue
        if spans:                    # 通栏行（标题/跨栏表）
            flush()
            out.append(txt)
        elif mid < gutter:
            left.append(txt)
        else:
            right.append(txt)
    flush()
    return "\n".join(out)


LAYOUT_MIN_KEEP_RATIO = 0.95      # 分栏结果不得比原路径短 5% 以上，否则视为掉内容


def _page_text_layout_aware(page):
    """对单页做分栏感知抽取；判不出双栏**或结果缩水**时返回 None（调用方回退 extract_text）。

    "不缩水"守卫是必须的：真实 PDF 上第一版算法会把单栏页误判成双栏并**丢掉一半文字**
    （句数 485→256）。提取结果喂给所有下游环节，宁可不用分栏也不能掉内容。
    """
    try:
        words = page.extract_words(x_tolerance=1.5, y_tolerance=3,
                                   keep_blank_chars=False, use_text_flow=False)
    except Exception:
        return None
    if not words:
        return None
    try:
        width = float(page.width or 0)
    except Exception:
        width = 0.0
    out = text_layout_aware(_lines_from_words(words), width)
    if not out:
        return None
    try:
        plain = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
    except Exception:
        plain = ""
    if plain and len(out) < LAYOUT_MIN_KEEP_RATIO * len(plain):
        return None                      # 掉内容 → 回退原路径
    return out


def _ocr_pdf(pdf_path):
    """方案 D：扫描版 PDF —— pypdfium2 栅格化 + rapidocr 识别。

    只在 A/B/C 都拿不到文字时调用（OCR 慢，普通 PDF 不该走这里）。
    返回 (文本, 错误列表)。
    """
    errors = []
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        return "", [f"pypdfium2 未安装: {e}"]
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        return "", [
            "OCR 引擎未安装（扫描版 PDF 需要）："
            "pip install rapidocr-onnxruntime   原始错误: " + str(e)
        ]
    try:
        engine = RapidOCR()
        doc = pdfium.PdfDocument(str(pdf_path))
        n = min(len(doc), OCR_MAX_PAGES)
        if len(doc) > OCR_MAX_PAGES:
            errors.append(f"OCR 只处理前 {OCR_MAX_PAGES} 页（共 {len(doc)} 页）")
        parts = []
        for i in range(n):
            try:
                img = doc[i].render(scale=OCR_DPI / 72.0).to_pil()
                res = engine(img)
                lines = res[0] if isinstance(res, tuple) else res
                if lines:
                    parts.append("\n".join(str(x[1]) for x in lines if len(x) > 1))
            except Exception as e:
                errors.append(f"OCR 第 {i + 1} 页失败: {type(e).__name__}: {e}")
        return "\n\n".join(parts), errors
    except Exception as e:
        return "", [f"OCR 运行失败: {type(e).__name__}: {e}"]


def extract_text_from_docx(docx_path):
    """从 Word (.docx) 提取纯文本：段落 + 表格（表格按行用 | 连接，保留结构）。

    只支持 .docx（OOXML）。老的二进制 .doc 需要 antiword/LibreOffice，这里不做，
    由 classify_upload() 给出明确报错。
    """
    try:
        import docx  # python-docx
    except ImportError as e:
        raise RuntimeError("python-docx 未安装：pip install python-docx（原始错误: %s）" % e)
    d = docx.Document(str(docx_path))
    parts = []
    for para in d.paragraphs:
        s = (para.text or "").strip()
        if s:
            parts.append(s)
    for ti, table in enumerate(d.tables, 1):
        rows = []
        for row in table.rows:
            cells = [(c.text or "").strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append("[表 %d]\n" % ti + "\n".join(rows))
    return "\n\n".join(parts)


TEXT_EXTS = (".txt", ".md", ".text")
WORD_EXTS = (".docx",)
LEGACY_WORD_EXTS = (".doc",)


def classify_upload(filename):
    """把上传文件名分类：'pdf' | 'text' | 'docx' | 'legacy_doc' | None。

    单独抽出来是为了**可单测**，并把 .doc（老二进制）与 .docx 区分开 ——
    否则用户传 .doc 只看到一句"只支持 .txt/.md/.pdf"，不知道该怎么办。
    """
    name = str(filename or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(WORD_EXTS):
        return "docx"
    if name.endswith(LEGACY_WORD_EXTS):
        return "legacy_doc"
    if name.endswith(TEXT_EXTS):
        return "text"
    return None


def ocr_image_file(img_path):
    """对单张图片做 OCR（粘贴的截图 / 扫描页图片走这里）。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        raise RuntimeError("OCR 引擎未安装：pip install rapidocr-onnxruntime（原始错误: %s）" % e)
    from PIL import Image
    engine = RapidOCR()
    res = engine(Image.open(str(img_path)).convert("RGB"))
    lines = res[0] if isinstance(res, tuple) else res
    return "\n".join(str(x[1]) for x in (lines or []) if len(x) > 1)


def strip_pdf_junk(text):
    """清洗 PDF 提取文本里的**模板垃圾/页眉页脚/乱码行**（规则均来自真实语料统计）。

    返回清洗后的文本。设计原则：**保守优先** —— 宁可少删，也不误删正文。
    """
    if not text:
        return text
    import re as _re
    from collections import Counter

    out_lines = []
    # 1) 已知模板标签 + (cid:NN) 字体垃圾：直接删除（这两类是确定的垃圾）
    t = _re.sub(r"\((?:cid):\d+\)", "", text)
    for tag in ("think", "action", "diagnosis", "thought", "query", "observation",
                "answer", "response", "system", "user", "assistant", "output", "input"):
        t = _re.sub(r"</?%s\s*/?>" % tag, " ", t, flags=_re.I)
        t = _re.sub(r">\s*%s\s*<" % tag, " ", t, flags=_re.I)

    lines = t.splitlines()
    # 2) 统计：重复短行（页眉页脚）、单字母行、纯短数字行
    short_counter = Counter(l.strip() for l in lines if 3 <= len(l.strip()) <= 40)
    single_letter = Counter(l.strip() for l in lines if _re.fullmatch(r"[A-Za-z]", l.strip() or ""))
    numeric = [l.strip() for l in lines if _re.fullmatch(r"\d{1,3}", l.strip() or "")]
    paged = len(numeric) >= 5          # 只有"分页文档"才清纯数字行

    for raw in lines:
        s = raw.strip()
        if not s:
            out_lines.append(raw)
            continue
        if len(s) >= 6 and _re.fullmatch(r"[-_=—=]{6,}", s):            # 分隔线
            continue
        if paged and _re.fullmatch(r"\d{1,3}", s):                       # 页码
            continue
        if 3 <= len(s) <= 40 and short_counter[s] >= 5:                  # 重复页眉页脚
            # 判据来自实测：'Preprint'×41 / 'avg2' / 'erocS' / '0.0' 这类是页眉页脚，
            # 而 'KV Size = 5%'（3 个 token 且含数字）是参数说明，必须保留。
            toks = s.split()
            if len(toks) <= 2 and not _re.search(r"\d", s):
                continue
        if _re.fullmatch(r"[A-Za-z]", s) and single_letter[s] >= 5:      # 孤立单字母
            continue
        toks = s.split()
        if len(toks) >= 8:                                               # 字符级交错乱码
            ones = sum(1 for x in toks if len(x) == 1)
            if ones / len(toks) >= 0.6:
                continue
        out_lines.append(raw)

    cleaned = "\n".join(out_lines)
    cleaned = _re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_text_from_pdf_raw(pdf_path):
    """
    从 PDF 提取纯文本。回退链：pdfplumber → pypdf → pdftotext → OCR。
    返回前统一经 strip_pdf_junk() 清洗（模板标签/页眉页脚/页码/乱码行）。
    全部失败时抛出携带诊断信息的 RuntimeError，不再黑盒吞错。
    pdfplumber 使用 x_tolerance=1.5 以修复英文单词空格丢失问题。
    """
    errors = []

    # 方案 A：pdfplumber
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                try:
                    # 先试"双栏感知"：两栏排版直接 extract_text 会把左右栏交错，
                    # 这正是参考数据出现碎片的来源。判不出双栏（返回 None）再走原路径。
                    t = _page_text_layout_aware(page)
                    if not t:
                        t = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                except Exception as e:
                    errors.append(f"pdfplumber 第 {i} 页: {e}")
                    t = ""
                if t:
                    parts.append(t)
        if parts:
            return "\n\n".join(parts)
        errors.append(f"pdfplumber: 打开了 {page_count} 页，但 0 页有可提取文字（可能是扫描版 PDF）")
    except ImportError as e:
        errors.append(f"pdfplumber 未安装: {e}")
    except Exception as e:
        errors.append(f"pdfplumber 运行失败: {type(e).__name__}: {e}")

    # 方案 B：pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            errors.append("pypdf: PDF 加密，需先解密")
        parts = []
        for i, page in enumerate(reader.pages):
            try:
                t = page.extract_text() or ""
            except Exception as e:
                errors.append(f"pypdf 第 {i} 页: {e}")
                t = ""
            if t:
                parts.append(t)
        if parts:
            return "\n\n".join(parts)
        errors.append(f"pypdf: 读了 {len(reader.pages)} 页，但 0 页有可提取文字")
    except ImportError as e:
        errors.append(f"pypdf 未安装: {e}")
    except Exception as e:
        errors.append(f"pypdf 运行失败: {type(e).__name__}: {e}")

    # 方案 C：pdftotext（poppler-utils）
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
                out_path = tmp.name
            subprocess.run(
                [pdftotext, "-layout", str(pdf_path), out_path],
                check=True, capture_output=True, timeout=120
            )
            with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            os.unlink(out_path)
            if text.strip():
                return text
            errors.append("pdftotext: 提取结果为空白")
        except Exception as e:
            errors.append(f"pdftotext 运行失败: {type(e).__name__}: {e}")
    else:
        errors.append("pdftotext 未安装（可选：sudo apt install poppler-utils）")

    # 方案 D：OCR（扫描版 / 图片型 PDF）
    # 只有前三方案都拿不到文字时才走到这里 —— OCR 慢，不能对普通 PDF 白跑。
    ocr_text, ocr_errors = _ocr_pdf(pdf_path)
    errors.extend(ocr_errors)
    if ocr_text.strip():
        return ocr_text
    errors.append("OCR: 没有识别出文字")

    diag = "\n".join(f"  - {e}" for e in errors)
    raise RuntimeError(
        f"PDF 解析全部失败：{pdf_path}\n"
        f"Python: {sys.executable}\n"
        f"各方案结果：\n{diag}"
    )


# ============ 架构兼容性检查 ============


def extract_text_from_pdf(pdf_path):
    """对外接口：提取 + 清洗（脏数据见 strip_pdf_junk 的注释）。"""
    text = _extract_text_from_pdf_raw(pdf_path)
    try:
        return strip_pdf_junk(text)
    except Exception:
        return text        # 清洗失败不能影响可用性

def _read_config(model_path):
    cfg = os.path.join(model_path, "config.json")
    if not os.path.exists(cfg):
        return None
    try:
        with open(cfg, 'r', encoding='utf-8') as f:
            return json_lib.load(f)
    except Exception:
        return None


def _get_model_hidden_size(model_path):
    config = _read_config(model_path)
    if not config:
        return None
    return config.get("hidden_size")


def _get_lora_weight_hidden_size(lora_path):
    adapter_file = os.path.join(lora_path, "adapter_model.safetensors")
    if not os.path.exists(adapter_file):
        return None
    try:
        from safetensors import safe_open
        with safe_open(adapter_file, framework='pt') as f:
            keys = list(f.keys())
            priority_keys = [
                "q_proj.lora_A", "k_proj.lora_A", "v_proj.lora_A",
                "o_proj.lora_A", "gate_proj.lora_A", "up_proj.lora_A",
            ]
            for pkey in priority_keys:
                for k in keys:
                    if pkey in k and "lora_A" in k:
                        shape = f.get_slice(k).get_shape()
                        return shape[-1]
    except ImportError:
        return None
    except Exception:
        return None
    return None


def check_lora_compatibility(lora_name, lora_path):
    details = {}
    base_model = get_vllm_base_model()
    if not base_model:
        return False, "vLLM 未运行或未加载底座模型，无法检查兼容性", details
    details["vllm_base"] = base_model
    vllm_hidden = _get_model_hidden_size(base_model)
    details["vllm_hidden"] = vllm_hidden
    if not vllm_hidden:
        return False, f"无法读取 vLLM 底座 config.json: {base_model}", details

    lora_hidden = _get_lora_weight_hidden_size(lora_path)
    details["lora_hidden"] = lora_hidden

    if not lora_hidden:
        adapter_cfg = os.path.join(lora_path, "adapter_config.json")
        if os.path.exists(adapter_cfg):
            try:
                with open(adapter_cfg, 'r', encoding='utf-8') as f:
                    cfg = json_lib.load(f)
                declared_base = cfg.get("base_model_name_or_path", "")
                details["declared_base"] = declared_base
                if declared_base and os.path.exists(declared_base):
                    lora_hidden = _get_model_hidden_size(declared_base)
                    details["lora_hidden_from_adapter"] = lora_hidden
            except Exception:
                pass

    if not lora_hidden:
        return False, f"无法确定 LoRA '{lora_name}' 的隐藏维度，可能文件损坏", details

    if lora_hidden != vllm_hidden:
        msg = (
            f"❌ 架构不兼容\n"
            f"该 LoRA '{lora_name}' 是为 hidden_size={lora_hidden} 的模型训练的，\n"
            f"但当前 vLLM 底座 '{os.path.basename(base_model)}' 的 hidden_size={vllm_hidden}。\n\n"
            f"请先切换 vLLM 到与该 LoRA 匹配的底座模型。"
        )
        return False, msg, details

    return True, "兼容", details


# ============ 基础接口 ============
def _mask_providers(providers: dict) -> dict:
    """
    对外接口一律不返回明文 api_key。

    返回空字符串 + api_key_set 标记：
    - 前端输入框留空表示"不修改"，PUT /providers 会保留旧值；
    - 前端判断"是否已配置"要看 api_key_set，不能再用 api_key 的真假；
    - 需要 Key 的服务端流程（如数据生成）由后端自己从 settings 注入，
      浏览器不再需要持有明文 Key。
    """
    out = {}
    for name, cfg in (providers or {}).items():
        item = dict(cfg or {})
        item["api_key_set"] = bool(item.get("api_key"))
        item["api_key"] = ""
        out[name] = item
    return out


# ══════ 安全响应头 + 敏感信息脱敏（2026-10-22 验收发现后新增）══════
# 问题: 缺 nosniff / X-Frame-Options / CSP；且 /settings /models/list /readiness
#       /vllm/status /agent/config 泄露 /home/<user>/... 绝对路径，/browser/status 泄露内网 IP
# 做法: 统一中间件
#   · 安全头：nosniff / DENY / 基础 CSP
#   · JSON 响应体：/home/<user> → ~ ；内网 IP → <sidecar-host>
#   ※ 跳过 SSE（text/event-stream）与二进制，避免破坏流式
_SEC_MW = True


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: http://127.0.0.1:9224; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    except Exception:
        pass
    return response



# ══════ 敏感信息脱敏 + 参数夹取（2026-10-22 验收发现后新增）══════
def _sanitize_paths(obj):
    """把响应里的绝对路径 / 内网 IP 脱敏（验收发现 5 个端点泄露 /home/<user>/...）"""
    import json as _j
    try:
        t = _j.dumps(obj, ensure_ascii=False)
    except Exception:
        return obj
    # ★ 修（v4.9.7 开源）：不绑定具体用户名，任何 /home/<user> 都脱敏
    t = re.sub(r'/home/[A-Za-z0-9._-]+', '~', t)
    t = t.replace('172.20.64.1', '<sidecar-host>')
    t = t.replace('10.255.255.254', '<host>')
    try:
        return _j.loads(t)
    except Exception:
        return obj


def _clamp_int(v, lo, hi, default):
    """整数夹取 —— 消灭 max_tokens/temperature 越界导致的 500"""
    try:
        x = int(v)
    except Exception:
        return default
    return max(lo, min(hi, x))


def _clamp_float(v, lo, hi, default):
    try:
        x = float(v)
    except Exception:
        return default
    return max(lo, min(hi, x))



def _browser_sidecar_base():
    """解析 sidecar 基地址（★ 2026-10-22 修：原默认值 10.255.255.254:9223 是陈旧 IP，空装必失败）

    优先级:
      ① 环境变量 BROWSER_SIDECAR   （显式覆盖，WSL 里通常是 http://172.20.64.1:9224）
      ② settings.json 的 browser_sidecar
      ③ http://127.0.0.1:9224      （单机同 OS 时的合理默认）
    """
    import os as _os2
    v = str(_os2.environ.get("BROWSER_SIDECAR") or "").strip()
    if v:
        return v.rstrip("/")
    try:
        _st = load_settings() or {}
        v2 = str(_st.get("browser_sidecar") or "").strip()
        if v2:
            return v2.rstrip("/")
    except Exception:
        pass
    return "http://127.0.0.1:9224"



@app.get("/version")
def get_version():
    """版本号（发布包 VERSION 文件 + 关键能力清单）"""
    return {"version": _LUX_VERSION, "name": "Luxy 模型垂直开发平台",
            "generation": "v4.9", "build": "2026-10-22"}

@app.get("/health")
def health():
    return {"status": "ok", "version": _LUX_VERSION}


@app.get("/settings")
def get_settings():
    s = load_settings()
    s["providers"] = _mask_providers(s.get("providers", {}))
    return _sanitize_paths(s)


@app.put("/settings")
def update_settings(s: SettingsModel):
    updates = {k: v for k, v in s.model_dump().items() if v is not None}
    return save_settings(updates)


@app.get("/providers")
def get_providers():
    return _mask_providers(load_settings().get("providers", {}))


@app.put("/providers/{name}")
def update_provider(name: str, cfg: ProviderConfig):
    if name not in ("vllm", "ollama", "openai"):
        raise HTTPException(400, f"未知 provider: {name}")
    settings = load_settings()
    providers = settings.get("providers", {})
    old = providers.get(name, {})

    # 掩码值（含 *）视为"未修改"，避免把掩码写进配置文件
    incoming_key = (cfg.api_key or "").strip()
    if "*" in incoming_key:
        incoming_key = ""

    new_cfg = {
        "api_base": cfg.api_base or old.get("api_base", ""),
        "model": cfg.model or old.get("model", ""),
        "api_key": incoming_key or old.get("api_key", ""),
    }
    providers[name] = new_cfg
    save_settings({"providers": providers})
    # 响应同样不回显明文 Key
    return {
        "status": "success",
        "provider": name,
        "config": _mask_providers({name: new_cfg})[name],
    }


# ============ LoRA 管理 ============
@app.get("/loras")
def list_loras():
    loras = scan_loras()
    base_model = get_vllm_base_model()
    vllm_hidden = _get_model_hidden_size(base_model) if base_model else None
    for lora in loras:
        lora_hidden = _get_lora_weight_hidden_size(lora["path"])
        if lora_hidden and vllm_hidden:
            lora["compatible"] = (lora_hidden == vllm_hidden)
            lora["hidden_size"] = lora_hidden
        else:
            lora["compatible"] = None
            lora["hidden_size"] = lora_hidden
    return {"loras": loras, "vllm_hidden_size": vllm_hidden}


@app.get("/loras/loaded")
def list_loaded():
    return {"loaded": get_loaded_loras()}


@app.post("/loras/upload")
async def upload_lora(file: UploadFile = File(...), lora_name: str = Form(...)):
    if not lora_name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="LoRA名称只能包含字母、数字、下划线和中划线")

    lora_dir = get_lora_dir()
    os.makedirs(lora_dir, exist_ok=True)
    target_dir = lora_dir / lora_name
    if target_dir.exists():
        raise HTTPException(status_code=400, detail=f"LoRA '{lora_name}' 已存在，请先删除")

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(target_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="上传的文件不是有效的 ZIP 压缩包")

    if not (target_dir / "adapter_config.json").exists():
        for item in os.listdir(target_dir):
            sub_dir = target_dir / item
            if sub_dir.is_dir() and (sub_dir / "adapter_config.json").exists():
                for sub_item in os.listdir(sub_dir):
                    shutil.move(str(sub_dir / sub_item), str(target_dir))
                os.rmdir(sub_dir)
                break

    if not (target_dir / "adapter_config.json").exists():
        shutil.rmtree(target_dir)
        raise HTTPException(status_code=400, detail="压缩包内未找到有效的 LoRA 适配器文件")

    return {"status": "success", "message": f"LoRA '{lora_name}' 上传成功"}


@app.delete("/loras/{lora_name}")
def delete_lora(lora_name: str):
    if lora_name in get_loaded_loras():
        raise HTTPException(status_code=400, detail=f"LoRA '{lora_name}' 正在被使用，请先卸载")
    target_dir = get_lora_dir() / lora_name
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail=f"LoRA '{lora_name}' 不存在")
    shutil.rmtree(target_dir)
    return {"status": "success", "message": f"LoRA '{lora_name}' 已从磁盘删除"}


@app.post("/loras/load")
def load_lora(req: LoadRequest):
    lora_path = req.lora_path or str(get_lora_dir() / req.lora_name)
    if not os.path.exists(lora_path):
        raise HTTPException(status_code=404, detail=f"路径不存在: {lora_path}")

    if not req.force:
        compatible, message, details = check_lora_compatibility(req.lora_name, lora_path)
        if not compatible:
            raise HTTPException(status_code=400, detail=message)

    # ★ 修（2026-10-22 实测）：前端页面加载时会**连发两次** /loras/load
    #   第一次 200，第二次 vLLM 回 "has already been loaded" ⇒ 400 ✗
    #   而且 vLLM 原始报文直接透传给用户 ✗
    #   修: 先查 /loras/loaded 幂等返回；再对 vLLM 的 already-loaded 兜底识别 ✓
    try:
        _ld = requests.get(f"{VLLM_BASE}/v1/models", timeout=6)
        # vLLM 的 loaded 列表在 /loras/loaded 聚合里；这里用本地聚合优先
        _cur = []
        try:
            _r0 = requests.get(f"http://127.0.0.1:{PORT}/loras/loaded", timeout=6)
            if _r0.status_code == 200:
                _cur = (_r0.json() or {}).get("loaded") or []
        except Exception:
            _cur = []
        if req.lora_name in _cur and not req.force:
            return {"status": "success", "already_loaded": True,
                    "message": f"'{req.lora_name}' 已加载（幂等返回）"}
    except Exception:
        pass

    payload = {"lora_name": req.lora_name, "lora_path": lora_path}
    try:
        resp = requests.post(f"{VLLM_BASE}/v1/load_lora_adapter", json=payload, timeout=30)
        if resp.status_code == 200:
            return {"status": "success", "message": f"'{req.lora_name}' 已加载"}
        # 幂等兜底：vLLM 报 already loaded ⇒ 视作成功（不是用户错误）
        _txt = (resp.text or '')
        if 'already been loaded' in _txt or 'already loaded' in _txt.lower():
            return {"status": "success", "already_loaded": True,
                    "message": f"'{req.lora_name}' 之前已加载（幂等返回）"}
        raise HTTPException(status_code=resp.status_code, detail=_txt)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/loras/unload")
def unload_lora(req: UnloadRequest):
    payload = {"lora_name": req.lora_name}
    try:
        resp = requests.post(f"{VLLM_BASE}/v1/unload_lora_adapter", json=payload, timeout=30)
        if resp.status_code == 200:
            return {"status": "success", "message": f"'{req.lora_name}' 已卸载"}
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 工作流配置（含 schema 版本化 + 导入导出） ============
def normalize_config(cfg: dict) -> dict:
    """补齐缺失字段并升级 schema 版本。所有读取路径都走这里。"""
    cfg = dict(cfg)
    # 1.0 → 2.0 迁移
    if "schema_version" not in cfg:
        if "field_extraction" in cfg:
            nodes = []
            fe = cfg["field_extraction"]
            nodes.append({
                "id": fe.get("id", "extract"), "type": "process",
                "description": "维度抽取", "input_from": [],
                "lora": fe.get("lora"), "prompt": fe.get("prompt", "")
            })
            for n in cfg.get("nodes", []):
                nodes.append({
                    "id": n["id"], "type": "terminal",
                    "description": n.get("description", ""),
                    "input_from": n.get("input_from", []),
                    "lora": n.get("lora"), "prompt": n.get("prompt", "")
                })
            cfg["nodes"] = nodes
            cfg.pop("field_extraction", None)
        cfg["schema_version"] = "2.0"

    cfg.setdefault("workflow_name", "未命名工作流")
    cfg.setdefault("description", "")
    cfg.setdefault("author", "")
    cfg.setdefault("tags", [])
    cfg.setdefault("required_loras", [])
    cfg.setdefault("required_providers", ["vllm"])
    cfg.setdefault("nodes", [])
    cfg.setdefault("canvas_height", 0)   # 画布高度（前端画布主动调整用）

    for node in cfg["nodes"]:
        node.setdefault("position", None)
        node.setdefault("provider", None)         # ★ P1-A 修改：节点级 provider
        node.setdefault("model_override", None)   # ★ P1-A 修改：节点级模型覆盖

    return cfg


def _engine_module():
    """
    懒加载 workflow_engine —— 取它的节点字段/类型注册表（**单一真源**）。
    失败时返回 None，调用方退化为"不做该项校验"，绝不因此让服务起不来。
    """
    import importlib
    try:
        return importlib.import_module("workflow_engine")
    except Exception:
        return None


def engine_lint(cfg: dict):
    """用引擎的 lint_config 校验（未知类型/字段/越界/坏引用）。返回 (errors, warnings)。"""
    eng = _engine_module()
    if not eng or not hasattr(eng, "lint_config"):
        return [], []
    try:
        return eng.lint_config(cfg)
    except Exception as e:
        return [], [f"配置校验失败（{type(e).__name__}: {e}）"]


@app.get("/workflow/node-types")
def workflow_node_types():
    """
    暴露引擎的节点类型注册表 —— 前端编辑器/Agent 都应以它为准，
    不要再各自维护一份字段清单（本会话已因两份清单不同步踩过坑）。
    """
    eng = _engine_module()
    if not eng:
        raise HTTPException(503, "无法加载节点类型注册表（workflow_engine 不可用）")
    types = []
    for name, spec in eng.NODE_TYPE_SPECS.items():
        types.append({"type": name, "label": spec.get("label"), "kind": spec.get("kind"),
                      "output": spec.get("output"), "purpose": spec.get("purpose"),
                      "lora_role": spec.get("lora_role"), "quality": spec.get("quality"),
                      "defaults": spec.get("defaults") or {},
                      "ignored_fields": sorted(spec.get("ignored_fields") or [])})
    return {"types": types, "fields": eng.NODE_FIELD_SPECS}


@app.get("/workflow/validate")
def workflow_validate(name: str = None):
    """按名校验配置：errors（会拒绝启动）/ warnings（仅提示）。前端与 Agent 都能先查再跑。"""
    path = _resolve_workflow_config(name)
    if not path.exists():
        raise HTTPException(404, f"配置文件不存在: {path.name}")
    cfg = normalize_config(json_lib.loads(path.read_text(encoding="utf-8")))
    errors, warnings = engine_lint(cfg)
    warnings = warnings + check_config_compat(cfg)
    return {"name": path.name, "ok": not errors, "errors": errors, "warnings": warnings}


def check_config_compat(cfg: dict) -> list:
    """导入时检查兼容性，返回警告列表"""
    warnings = []
    try:
        installed = {l["name"] for l in scan_loras()}
    except Exception:
        installed = set()
    for lora in cfg.get("required_loras", []):
        if lora not in installed:
            warnings.append(f"缺少 LoRA: {lora}（可先用底座代替）")
    for node in cfg.get("nodes", []):
        lora = node.get("lora")
        if lora and lora not in installed:
            warnings.append(f"节点 {node['id']} 引用了未安装的 LoRA: {lora}")
    return warnings


def _resolve_workflow_config(name):
    """配置名 → PROJECT_DIR 下的安全路径。拒绝目录穿越与非 .json 名。"""
    name = (name or "workflow_config.json").strip() or "workflow_config.json"
    if ("/" in name or "\\" in name or name.startswith(".")
            or not name.endswith(".json") or ".." in name):
        raise HTTPException(400, f"非法配置名: {name}")
    path = (PROJECT_DIR / name).resolve()
    if path.parent != PROJECT_DIR.resolve():
        raise HTTPException(400, f"配置名越界: {name}")
    return path


@app.post("/tools/search_existing")
def tools_search_existing(req: dict = None):
    # ★ 修（2026-10-22 验收）: query 直进 subprocess argv ⇒ 未捕获异常裸 500 ✗
    #   1MB query / list / int 全都 500；正常路径还同步阻塞 150s
    try:
        if not isinstance(req, dict):
            raise HTTPException(400, "payload 必须是 JSON 对象")
        _q = req.get("query", "")
        if not isinstance(_q, str):
            raise HTTPException(400, "query 必须是字符串")
        if len(_q) > 500:
            raise HTTPException(400, "query 太长（上限 500 字符）")
    except HTTPException:
        raise
    except Exception as _qe:
        raise HTTPException(400, "参数校验失败: %s" % _qe)
    """★ 原则二「先找不训」：检索现成模型/LoRA/项目/论文

    body: {query: "function calling", kind: "all|model|lora|repo|paper", limit: 8}
    返回: {known_good, models, repos, papers}
    """
    import subprocess
    req = req or {}
    q = (req.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "需要 query")
    argv = [sys.executable, str(PROJECT_DIR / "tools" / "search_existing.py"), q, "--json"]
    if req.get("kind"):
        argv += ["--kind", str(req["kind"])]
    if req.get("limit"):
        argv += ["--limit", str(int(req["limit"]))]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=150, cwd=str(PROJECT_DIR),
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "检索超时（外网慢）")
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or "")[-400:])
    try:
        return json_lib.loads(r.stdout)
    except Exception as _e:
        raise HTTPException(500, "检索输出解析失败: %s" % _e)


@app.get("/workflow/configs")
def list_workflow_configs():
    """列出可用的工作流配置，供前端切换。"""
    items = []
    for p in sorted(PROJECT_DIR.glob("workflow_config*.json")):
        try:
            cfg = json_lib.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        stat = p.stat()
        items.append({
            "name": p.name,
            "workflow_name": cfg.get("workflow_name") or p.stem,
            # T2 模板画廊要用的元数据：用途、标签、依赖是否满足
            "description": cfg.get("description") or "",
            "tags": cfg.get("tags") or [],
            "author": cfg.get("author") or "",
            "required_loras": cfg.get("required_loras") or [],
            "required_providers": cfg.get("required_providers") or [],
            "nodes": len(cfg.get("nodes") or []),
            "is_default": p.name == "workflow_config.json",
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return {"configs": items, "default": "workflow_config.json"}


@app.get("/workflow/config")
def get_workflow_config(name: str = None):
    path = _resolve_workflow_config(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"配置文件不存在: {path.name}")
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json_lib.load(f)
    return normalize_config(cfg)


@app.put("/workflow/config")
async def update_workflow_config(payload: dict, name: str = None):
    """保存工作流配置。

    ★ 2026-10-22 安全修复（v4.1 全流程测试发现的数据毁灭性漏洞）:
      原实现对 body **不做任何校验**，直接 normalize + 覆盖写盘 ✗
      ⇒ 传 {"nodes": []}（或任何能通过 normalize 的不合法 body）
        就会把 workflow_config.json 覆盖成空 ⇒ 9 个预设节点全部丢失 ✗✗
      ⇒ 而 /workflow/config/import 却有 nodes 校验，两者行为不一致 ✗
    修法:
      ① 校验必须是 dict 且 nodes 是非空 list（空数组明确拒绝）
      ② 复用 import 的 engine_lint 校验（有错误就不写盘）
      ③ **原子写**：先写临时文件再 replace，避免写一半进程崩溃导致文件损坏
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "workflow 配置必须是 JSON 对象")
    if not isinstance(payload.get("nodes"), list):
        raise HTTPException(400, "workflow 配置缺少 nodes 数组（拒绝覆盖，防止清空预设）")
    if len(payload.get("nodes") or []) == 0:
        raise HTTPException(400, "nodes 不能为空数组（会清空全部预设工作流）")
    # ★ 修（2026-10-22 C组实测）：循环依赖保存时不拦，运行时才报 ✗
    try:
        _deps4 = {}
        for _n4 in (payload.get("nodes") or []):
            if isinstance(_n4, dict) and _n4.get("id"):
                _deps4[str(_n4["id"])] = [str(x) for x in (_n4.get("input_from") or [])]
        _st4 = {k: 0 for k in _deps4}
        _cyc4 = []

        def _dfs4(_n):
            _st4[_n] = 1
            for _p in _deps4.get(_n, []):
                if _p not in _deps4:
                    continue
                if _st4.get(_p) == 1:
                    _cyc4.append("%s<->%s" % (_n, _p))
                elif _st4.get(_p) == 0:
                    _dfs4(_p)
            _st4[_n] = 2

        for _k4 in list(_deps4):
            if _st4.get(_k4) == 0:
                _dfs4(_k4)
        if _cyc4:
            raise HTTPException(400, "工作流存在循环依赖，拒绝保存: " + "; ".join(_cyc4[:5]))
    except HTTPException:
        raise
    except Exception as _e4:
        print("[workflow/config] 环检测异常(忽略): %s" % _e4, flush=True)
    cfg = normalize_config(payload)
    try:
        _errors, _lints = engine_lint(cfg)
        if _errors:
            raise HTTPException(400, {"message": "配置校验未通过", "errors": _errors})
    except HTTPException:
        raise
    except Exception:
        _lints = []
    path = _resolve_workflow_config(name)
    # 原子写：临时文件 + replace
    _tmp = path.with_name(path.name + ".tmp")
    with open(_tmp, 'w', encoding='utf-8') as f:
        json_lib.dump(cfg, f, ensure_ascii=False, indent=2)
    _tmp.replace(path)
    return {"status": "success", "message": f"配置已保存到 {path.name}",
            "config": cfg, "name": path.name, "nodes": len(cfg.get("nodes") or [])}


@app.get("/workflow/config/export")
def export_workflow_config(name: str = None):
    """导出**指定**配置（与 GET/PUT /workflow/config 一致地尊重 ?name=）。"""
    path = _resolve_workflow_config(name)
    if not path.exists():
        raise HTTPException(404, f"配置文件不存在: {path.name}")
    cfg = normalize_config(json_lib.loads(path.read_text(encoding="utf-8")))
    cfg["exported_at"] = datetime.now().isoformat()
    cfg["name"] = path.name
    return cfg


@app.post("/workflow/config/import")
async def import_workflow_config(payload: dict, name: str = None):
    """导入到**指定**配置（默认文件仍是 workflow_config.json）。"""
    if "nodes" not in payload:
        raise HTTPException(400, "配置缺少 nodes 字段")
    cfg = normalize_config(payload)
    errors, lint_warnings = engine_lint(cfg)
    if errors:
        raise HTTPException(400, {"message": "配置校验未通过", "errors": errors})
    warnings = lint_warnings + check_config_compat(cfg)
    path = _resolve_workflow_config(name)
    with open(path, 'w', encoding='utf-8') as f:
        json_lib.dump(cfg, f, ensure_ascii=False, indent=2)
    return {"status": "success", "name": path.name, "warnings": warnings, "config": cfg}


# ============ 工作流执行 ============
@app.post("/workflow/run")
async def run_workflow_endpoint(
    file: UploadFile = File(...),
    config_name: str = Form("workflow_config.json"),
    kb_name: str = Form(""),
):
    kind = classify_upload(file.filename)
    if kind == "legacy_doc":
        raise HTTPException(
            status_code=400,
            detail="不支持老版 .doc（二进制格式）。请在 Word 里「另存为」.docx 后重传。",
        )
    if kind is None:
        raise HTTPException(status_code=400, detail="只支持 .txt / .md / .pdf / .docx 文件")
    is_pdf = kind == "pdf"
    is_docx = kind == "docx"
    is_text = kind == "text"
    filename_lower = file.filename.lower()

    settings = load_settings()
    task_id = str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
    with open(save_path, "wb") as f:
        f.write(await file.read())

    # ★ PDF 转文本
    if is_pdf:
        try:
            text = extract_text_from_pdf(str(save_path))
            if not text or len(text.strip()) < 10:
                raise RuntimeError(
                    f"PDF 解析结果为空/过短（{len(text.strip())} 字符），"
                    f"可能是扫描版 PDF，需要先 OCR。文件：{file.filename}"
                )
            txt_path = save_path.with_suffix('.txt')
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(text)
            save_path = txt_path
            print(f"[workflow] PDF→TXT 成功：{len(text)} 字符 → {txt_path}", flush=True)
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"PDF 解析失败：{e}")

    # Word(.docx) 转文本：与 PDF 同一落点（存成 .txt）→ 下游工作流/数据集全不用改
    if is_docx:
        try:
            text = extract_text_from_docx(str(save_path))
            if not text or len(text.strip()) < 10:
                raise RuntimeError(
                    f"Word 解析结果为空/过短（{len(text.strip())} 字符）。文件：{file.filename}"
                )
            txt_path = save_path.with_suffix('.txt')
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(text)
            save_path = txt_path
            print(f"[workflow] DOCX→TXT 成功：{len(text)} 字符 → {txt_path}", flush=True)
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"Word 解析失败：{e}")

    workflow_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "file": file.filename,
        "log": [],
        "result": None,
        "result_file": None,
        "error": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "current_node": None,
        "completed_nodes": [],
        "total_nodes": 0,
    }

    # ★ 同步到数据库（含 config 快照）
    try:
        config_snapshot = None
        cfg_path = PROJECT_DIR / config_name
        if cfg_path.exists():
            with open(cfg_path, 'r', encoding='utf-8') as f:
                config_snapshot = normalize_config(json_lib.load(f))
        db.upsert_task({
            "task_id": task_id,
            "status": "running",
            "file_name": file.filename,
            "file_path": str(save_path),
            "config_snapshot": config_snapshot,
            "started_at": workflow_tasks[task_id]["started_at"],
        })
    except Exception as e:
        print(f"[db] 写任务失败: {e}")

    def append_log(line):
        t = workflow_tasks[task_id]
        t["log"].append(line)
        if len(t["log"]) > 600:
            t["log"] = t["log"][-600:]
        m = re.search(r'\[Node\]\s+(\S+)', line)
        if m:
            t["current_node"] = m.group(1)

    def run_in_background():
        t = workflow_tasks[task_id]
        try:
            venv_py = settings["venv_python"]
            if not os.path.exists(venv_py):
                raise RuntimeError(f"虚拟环境 Python 不存在: {venv_py}")

            engine_path = PROJECT_DIR / "workflow_engine.py"
            if not engine_path.exists():
                raise RuntimeError(f"workflow_engine.py 不存在: {engine_path}")

            config_path = PROJECT_DIR / config_name
            if not config_path.exists():
                raise RuntimeError(f"配置文件不存在: {config_path}")

            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json_lib.load(f)
                t["total_nodes"] = len(cfg.get("nodes", []))
            except Exception:
                t["total_nodes"] = 0

            env = os.environ.copy()
            env["BASE_MODEL_PATH"] = settings["base_model_path"]
            env["VLLM_CHAT_API"] = settings["vllm_chat_api"]
            env["LORA_MANAGER_API"] = settings["lora_manager_api"]
            env["LORA_DIR"] = settings["lora_dir"]
            env["PYTHONIOENCODING"] = "utf-8"
            # ★ P1-A 修改：把 provider 配置传给 workflow_engine
            env["WORKFLOW_PROVIDERS"] = json_lib.dumps(
                settings.get("providers", {}), ensure_ascii=False
            )
            # 本次运行可统一指定知识库（留空则由节点配置 / auto 探测决定）
            if kb_name and kb_name.strip():
                env["WORKFLOW_KB_NAME"] = kb_name.strip()

            cmd = [venv_py, str(engine_path), str(save_path), str(config_path)]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=1,
                cwd=str(PROJECT_DIR),
                env=env
            )
            for line in proc.stdout:
                append_log(line.rstrip())
            proc.wait()

            if proc.returncode != 0:
                t["status"] = "error"
                t["error"] = (
                    f"workflow_engine 退出码: {proc.returncode}\n"
                    f"最后 30 行日志：\n" + "\n".join(t["log"][-30:])
                )
                t["finished_at"] = datetime.now().isoformat()
                return

            outputs = sorted(OUTPUT_DIR.glob("result_*.json"), key=os.path.getmtime, reverse=True)
            if not outputs:
                t["status"] = "error"
                t["error"] = "未找到结果文件"
                t["finished_at"] = datetime.now().isoformat()
                return

            latest = outputs[0]
            with open(latest, 'r', encoding='utf-8') as f:
                data = json_lib.load(f)

            t["status"] = "success"
            t["result_file"] = latest.name
            t["result"] = data
            t["finished_at"] = datetime.now().isoformat()
            t["current_node"] = None

            # ★ G1: 按节点 kb_write 把本次产物【清洗后回流到知识库】（默认全关 ⇒ 不写 ✓）
            #   为什么放在成功分支: 失败运行没有完整轨迹，入库只会污染知识库 ✗
            #   为什么整包 try: 回流失败绝不能让"已经很成功的这次运行"变成失败 ✓
            try:
                import workflow_learn as _wlearn
                _lm = _wlearn.ingest(latest, config_path=config_path)
                t["learn"] = _lm
                if _lm.get("error"):
                    append_log(f"[Learn] 回流失败（不影响本次运行）: {_lm['error']}")
                elif _lm.get("skipped"):
                    append_log(f"[Learn] 未回流: {_lm['skipped']}")
                else:
                    for _kb, _info in (_lm.get("ingested") or {}).items():
                        _add = ((_info.get("info") or {}).get("added_chunks")
                                if isinstance(_info.get("info"), dict) else None)
                        append_log(f"[Learn] 已回流到知识库 {_kb}（mode={_info.get('mode')}"
                                   f"{f'，新增 {_add} 块' if _add is not None else ''}）")
            except Exception as _le:
                t["learn"] = {"error": f"{type(_le).__name__}: {_le}"}
                append_log(f"[Learn] 回流异常（不影响本次运行）: {_le}")

        except subprocess.TimeoutExpired:
            t["status"] = "error"
            t["error"] = "执行超时（30分钟）"
            t["finished_at"] = datetime.now().isoformat()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            t["status"] = "error"
            t["error"] = f"{type(e).__name__}: {e}"
            t["log"].append(f"❌ 后台异常：{tb}")
            t["finished_at"] = datetime.now().isoformat()
            print(f"[workflow] 后台异常:\n{tb}", flush=True)
        finally:
            # ★ 保证任务结束一定入库
            try:
                started = t.get("started_at")
                finished = t.get("finished_at")
                elapsed = None
                if started and finished:
                    try:
                        elapsed = (datetime.fromisoformat(finished) -
                                   datetime.fromisoformat(started)).total_seconds()
                    except Exception:
                        pass
                db.upsert_task({
                    "task_id": task_id,
                    "status": t.get("status"),
                    "result_file": t.get("result_file"),
                    "result_json": t.get("result"),
                    "error": t.get("error"),
                    "log": t.get("log", [])[-200:],
                    "total_nodes": t.get("total_nodes", 0),
                    "elapsed": elapsed,
                    "started_at": started,
                    "finished_at": finished,
                })
            except Exception as e:
                print(f"[db] 同步任务失败: {e}")

    threading.Thread(target=run_in_background, daemon=True).start()
    return {"task_id": task_id, "status": "running", "message": "工作流已启动"}


@app.get("/workflow/status/{task_id}")
def get_workflow_status(task_id: str):
    if task_id not in workflow_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = dict(workflow_tasks[task_id])
    if len(task.get("log", [])) > 300:
        task["log"] = task["log"][-300:]

    # ★★ 修（2026-10-22 C组实测）：任务级 status 报 success，但结果文件里是 partial/failed ✗
    #   实测: v1/v2 两次复现 ⇒ 用户看到"成功"，实际节点有失败/降级 ✗✗
    #   修法: 若有结果文件，读它的 status 并以它为准；同时把差异显式暴露出来 ✓
    try:
        _res_path = None
        for _k in ("result_file", "result", "output_file", "result_path"):
            _v = task.get(_k)
            if _v:
                _p = pathlib.Path(str(_v))
                if not _p.is_absolute():
                    _p = OUTPUT_DIR / _p.name
                if _p.exists():
                    _res_path = _p
                    break
        if _res_path is None:
            # 退化为按 task_id 找 result_*.json
            for _c in sorted(OUTPUT_DIR.glob("result_*.json"), key=os.path.getmtime, reverse=True)[:20]:
                try:
                    import json as _jj
                    _d = _jj.loads(_c.read_text(encoding="utf-8"))
                    if str(_d.get("task_id") or "") == str(task_id):
                        _res_path = _c
                        break
                except Exception:
                    continue
        if _res_path is not None:
            import json as _jj2
            _rd = _jj2.loads(_res_path.read_text(encoding="utf-8"))
            _real = str(_rd.get("status") or "").strip()
            if _real:
                _task_status = str(task.get("status") or "").strip()
                if _real != _task_status:
                    task["status_reported"] = _task_status   # 内存里的原值（保留可追溯）
                    task["status"] = _real                   # ★ 以结果文件为准 ✓
                    task["status_source"] = _res_path.name
                    print("[workflow/status] 状态以结果文件为准: %s → %s (%s)"
                          % (_task_status, _real, _res_path.name), flush=True)
                # 额外把节点级失败暴露出来
                _nf = _rd.get("node_failures") or _rd.get("failures") or []
                if _nf:
                    task["node_failures"] = _nf[:10]
    except Exception as _e5:
        print("[workflow/status] 读结果文件失败(忽略): %s" % _e5, flush=True)

    return task


@app.get("/workflow/latest")
def get_latest_workflow_status():
    """返回最近一次创建的工作流任务（含 error / log），方便命令行排查"""
    if not workflow_tasks:
        raise HTTPException(404, "还没有任何工作流任务（可能后端重启过）")
    latest = max(workflow_tasks.values(), key=lambda t: t["started_at"])
    task = dict(latest)
    if len(task.get("log", [])) > 300:
        task["log"] = task["log"][-300:]
    return task


@app.get("/workflow/list")
def list_workflow_results():
    outputs = sorted(OUTPUT_DIR.glob("result_*.json"), key=os.path.getmtime, reverse=True)
    return {"results": [{"file": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime} for f in outputs[:30]]}


@app.get("/workflow/result/{filename}")
def get_workflow_result(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return json_lib.load(f)


# ============ 共用任务系统（训练/评测/工作流原本各写一份）============
class TaskRegistry:
    """
    统一的任务登记：状态/阶段/日志/错误/时间戳，带简单容量控制。
    只做登记、不做调度 —— 各业务的线程/子进程策略保持不变。
    """

    def __init__(self, kind: str, max_keep: int = 100):
        self.kind = kind
        self.max_keep = max_keep
        self.tasks = {}

    def create(self, task_id=None, **fields):
        task_id = task_id or str(uuid.uuid4())[:8]
        self.tasks[task_id] = {"task_id": task_id, "kind": self.kind, "status": "running",
                               "stage": fields.pop("stage", "准备中"), "error": None,
                               "log": [], "started_at": datetime.now().isoformat(),
                               "finished_at": None, **fields}
        if len(self.tasks) > self.max_keep:
            stale = sorted(self.tasks, key=lambda k: self.tasks[k]["started_at"])
            for k in stale[:len(self.tasks) - self.max_keep]:
                self.tasks.pop(k, None)
        return task_id

    def get(self, task_id):
        return self.tasks.get(task_id)

    def append_log(self, task_id, line):
        t = self.tasks.get(task_id)
        if t is not None:
            t["log"].append(str(line).rstrip())

    def finish(self, task_id, status="success", stage="完成", **extra):
        t = self.tasks.get(task_id)
        if t is not None:
            t.update({"status": status, "stage": stage,
                      "finished_at": datetime.now().isoformat(), **extra})

    def fail(self, task_id, error):
        self.finish(task_id, status="failed", stage="失败", error=str(error)[:2000])

    def list(self):
        return sorted(self.tasks.values(), key=lambda x: x["started_at"], reverse=True)


def _all_task_registries():
    """找出模块里所有任务登记处（含历史遗留的普通 dict），用于统一视图。"""
    found = {}
    for name, value in list(globals().items()):
        if isinstance(value, TaskRegistry):
            found[name] = value
        elif isinstance(value, dict) and name.endswith("_tasks") and name != "eval_tasks":
            found[name] = value
    return found


@app.get("/tasks")
def list_all_tasks(limit: int = 30):
    """
    统一任务视图：训练 / 评测 / 工作流一屏可见。
    Agent 用它回答"现在有什么在跑、跑到哪了"，不必分别查三个端点。
    """
    items = []
    for reg_name, reg in _all_task_registries().items():
        rows = reg.list() if isinstance(reg, TaskRegistry) else sorted(
            reg.values(), key=lambda x: x.get("started_at") or "", reverse=True)
        for row in rows:
            items.append({"registry": reg_name, "kind": row.get("kind") or reg_name.replace("_tasks", ""),
                          "task_id": row.get("task_id"), "status": row.get("status"),
                          "stage": row.get("stage"), "started_at": row.get("started_at"),
                          "finished_at": row.get("finished_at"), "error": row.get("error"),
                          "summary": {k: row.get(k) for k in
                                      ("lora_name", "dataset_file", "model", "total_nodes")
                                      if row.get(k) is not None}})
    items.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return {"tasks": items[:max(1, min(limit, 200))], "count": len(items)}


# ============ 闭环入口：按已上传论文运行 / 导出节点数据 / 质量台账 / 评测 ============
UPLOAD_LIST_EXTS = (".txt", ".md", ".pdf", ".text", ".docx")
_dataset_char_ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


@app.post("/chat/upload")
async def chat_upload(file: UploadFile = File(...)):
    """把文件"放进对话"：存到 uploads/ 并**转换成文本**，返回文件名供 agent 使用。

    与 /workflow/upload 的区别：**不触发任何工作流**，只做入库与文本化。
    - .pdf  → extract_text_from_pdf（含扫描版 OCR 回退）
    - .docx → extract_text_from_docx
    - 图片  → ocr_image_file（粘贴截图/扫描页）
    - .txt/.md/.text → 原样读入
    统一落点：转成同名 .txt，这样下游工作流与 agent 工具都按既有方式使用。
    """
    name = file.filename or ""
    kind = classify_upload(name)
    is_image = name.lower().endswith(IMAGE_EXTS)
    if kind == "legacy_doc":
        raise HTTPException(400, "不支持老版 .doc（二进制格式），请在 Word 里另存为 .docx 后重传")
    if kind is None and not is_image:
        raise HTTPException(400, "只支持 .txt / .md / .pdf / .docx 与常见图片格式")

    task_id = str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{task_id}_{name}"
    with open(save_path, "wb") as f:
        f.write(await file.read())

    try:
        if is_image:
            text = ocr_image_file(str(save_path))
        elif kind == "pdf":
            text = extract_text_from_pdf(str(save_path))
        elif kind == "docx":
            text = extract_text_from_docx(str(save_path))
        else:
            text = save_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"解析失败：{e}")

    out_path = save_path
    if is_image or kind in ("pdf", "docx"):
        out_path = save_path.with_suffix(".txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    if not text.strip():
        raise HTTPException(400, "文件解析后没有文字（可能是空白页或 OCR 未识别出内容）")
    print(f"[chat] 上传成功：{name} → {out_path.name}（{len(text)} 字符）", flush=True)
    return {"status": "success", "filename": out_path.name, "original": name,
            "kind": "image" if is_image else kind, "chars": len(text),
            "preview": text[:300]}


@app.get("/uploads")
def list_uploads():
    """列出已上传的论文 —— Agent 无法上传文件，需要先知道能跑哪些。"""
    items = []
    for f in sorted(UPLOAD_DIR.glob("*")):
        if f.is_file() and f.suffix.lower() in UPLOAD_LIST_EXTS:
            st = f.stat()
            items.append({"name": f.name, "size": st.st_size,
                          "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"uploads": items[:60]}


@app.post("/workflow/run_saved")
async def run_workflow_on_saved(paper_name: str = Form(...),
                                config_name: str = Form("workflow_config.json"),
                                kb_name: str = Form("")):
    """按已上传的论文文件名运行工作流（复用 /workflow/run 的全部逻辑）。"""
    import io
    name = (paper_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, f"非法文件名: {paper_name}")
    path = UPLOAD_DIR / name
    if not path.exists():
        raise HTTPException(404, f"论文文件不存在: {name}（可用 GET /uploads 查看）")
    if path.suffix.lower() not in UPLOAD_LIST_EXTS:
        raise HTTPException(400, "只支持 .txt / .md / .pdf / .docx")
    uf = UploadFile(filename=path.name, file=io.BytesIO(path.read_bytes()))
    return await run_workflow_endpoint(file=uf, config_name=config_name, kb_name=kb_name)


@app.post("/workflow/export_dataset")
def export_workflow_dataset(payload: dict):
    """
    把某次运行里**某个节点**的真实输入/输出导出为一条 sharegpt 样本并追加到数据集。
    这是闭环里"按节点取训练数据"的一环：跨多篇论文反复运行即可累积。
    """
    filename = str(payload.get("filename") or "").strip()
    node_id = str(payload.get("node") or "").strip()
    dataset = str(payload.get("dataset") or "").strip() or (node_id + "_sft")
    if not filename or not node_id:
        raise HTTPException(400, "需要 filename 与 node")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, f"非法文件名: {filename}")
    if not dataset or len(dataset) > 48 or any(c not in _dataset_char_ok for c in dataset):
        raise HTTPException(400, "数据集名只能含字母/数字/下划线/中划线，长度 1-48")
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"结果文件不存在: {filename}")
    data = json_lib.loads(path.read_text(encoding="utf-8"))
    trace = (data.get("_meta", {}).get("traces") or {}).get(node_id)
    if not trace:
        raise HTTPException(404, f"该结果里没有节点 {node_id} 的 trace（旧版本运行的结果不含 trace）")
    ds_path = PROJECT_DIR / "training_data" / (dataset + ".jsonl")
    ds_path.parent.mkdir(parents=True, exist_ok=True)
    before = 0
    if ds_path.exists():                       # 用 with 关闭句柄（此前这里漏了，会出 ResourceWarning）
        with open(ds_path, encoding="utf-8") as f:
            before = sum(1 for _ in f)
    sample = {"conversations": [
        {"from": "system", "value": "你是一个专业的助手，请准确、完整地回答用户的问题。"},
        {"from": "human", "value": trace.get("prompt") or ""},
        {"from": "gpt", "value": trace.get("output") or ""},
    ]}
    with open(ds_path, "a", encoding="utf-8") as f:
        f.write(json_lib.dumps(sample, ensure_ascii=False) + "\n")
    return {"status": "success", "dataset_file": "training_data/" + dataset + ".jsonl",
            "lines_before": before, "lines_after": before + 1,
            "node": node_id, "lora": trace.get("lora"),
            "prompt_chars": trace.get("prompt_chars"), "output_chars": trace.get("output_chars")}


# ---- LoRA 质量台账：让"选哪个 LoRA"有据可依（闭环的⑧）----
def _registry_path():
    return get_lora_dir() / "registry.json"


def load_lora_registry():
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        return json_lib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_lora_registry(reg):
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_lib.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/loras/registry")
def get_lora_registry():
    return {"registry": load_lora_registry()}


# ---- 评测任务化：让 UI/Agent 也能发起评测（闭环的⑥）----
eval_tasks = TaskRegistry("eval")          # S4：改用共用任务系统


class EvalRequest(BaseModel):
    lora_name: str
    dataset_file: str
    limit: int = 20
    workers: int = 3
    with_base: bool = True
    role: str = ""


@app.post("/eval/start")
def eval_start(req: EvalRequest):
    import contextlib
    import io
    import threading

    ds = (req.dataset_file or "").strip()
    ds_path = PROJECT_DIR / ds
    if not ds_path.exists():
        raise HTTPException(400, f"评测集不存在: {ds}")

    task_id = eval_tasks.create(stage="评测中", lora_name=req.lora_name,
                                dataset_file=ds, result=None)

    def run():
        import eval_lora
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                out_file, summary = eval_lora.run_eval(
                    req.lora_name, str(ds_path), limit=max(1, req.limit),
                    workers=max(1, req.workers), with_base=bool(req.with_base))
            agg_lora = (summary.get("aggregate") or {}).get("lora") or {}
            pair = summary.get("pairwise") or {}
            metrics = {"similarity": agg_lora.get("similarity"),
                       "avg_chars": agg_lora.get("avg_chars"), "n": agg_lora.get("n"),
                       "mean_delta": pair.get("mean_delta"),
                       "improved": pair.get("improved"), "worse": pair.get("worse"),
                       "leakage_warning": summary.get("leakage_warning"),
                       "input_overlap_warning": summary.get("input_overlap_warning")}
            reg = load_lora_registry()
            entry = reg.setdefault(req.lora_name, {})
            if req.role:
                entry["role"] = req.role
            entry["base_model"] = summary.get("base_model")
            last = {"at": datetime.now().isoformat(timespec="seconds"),
                    "dataset": summary.get("dataset"), "metrics": metrics}
            entry["last_eval"] = last
            entry["eval_history"] = (entry.get("eval_history") or [])[-4:] + [last]
            save_lora_registry(reg)
            eval_tasks.finish(task_id, result=summary, output_file=out_file)
        except SystemExit as e:
            eval_tasks.fail(task_id, f"评测中止: {e}")
        except Exception as e:
            eval_tasks.fail(task_id, f"{type(e).__name__}: {e}")
        finally:
            t = eval_tasks.get(task_id)
            if t is not None:
                t["log"] = buf.getvalue().splitlines()[-200:]

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id, "status": "running"}


@app.get("/eval/status/{task_id}")
def eval_status(task_id: str):
    t = eval_tasks.get(task_id)
    if not t:
        raise HTTPException(404, "评测任务不存在")
    return t


@app.get("/eval/list")
def eval_list():
    return {"tasks": eval_tasks.list()}


# ---- 质量台账：扫描已有运行结果自动累积（历史运行一并计入，无需额外落盘）----
def quality_thresholds():
    """
    质量判定的阈值**只有一处真源**：`workflow_engine` 里的常量。
    这里以前写死过一份，结果引擎把 grounding 从 0.8 提到 0.9 之后，
    台账接口还在对外报 0.8 —— 用户看到的判据和实际判定的不是一回事。
    """
    eng = _engine_module()
    if not eng:
        return {"fact_retention_min": None, "grounding_min": None,
                "min_chars_per_1k": None}
    return {
        "fact_retention_min": getattr(eng, "QUALITY_FACT_RETENTION_MIN", None),
        "grounding_min": getattr(eng, "QUALITY_GROUNDING_MIN", None),
        "min_chars_per_1k": getattr(eng, "QUALITY_MIN_CHARS_PER_1K", None),
    }


def _quality_stats(vals):
    if not vals:
        return {"n": 0, "min": None, "avg": None, "max": None}
    return {"n": len(vals), "min": round(min(vals), 3),
            "avg": round(sum(vals) / len(vals), 3), "max": round(max(vals), 3)}


@app.get("/quality/summary")
def quality_summary(limit: int = 50, node: str = None):
    """
    质量台账：把最近 limit 次运行结果里的 _meta.quality 聚合起来。
    用途：① 看某节点在不同论文上的质量分布 → 定位"该重训的节点"
          ② 复核判定阈值（现在只有少量样本）
    node 参数可只看某个节点。
    """
    files = sorted(OUTPUT_DIR.glob("result_*.json"),
                   key=lambda x: x.stat().st_mtime, reverse=True)[:max(1, min(limit, 500))]
    rows = []
    for f in files:
        try:
            data = json_lib.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = data.get("_meta") or {}
        warned = {w.get("node") for w in (meta.get("quality_warnings") or [])}
        for nid, q in (meta.get("quality") or {}).items():
            if node and nid != node:
                continue
            rows.append({
                "at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                "result_file": f.name, "node": nid,
                "source": q.get("source"), "eligible": bool(q.get("eligible")),
                "grounding": q.get("grounding"),
                "key_fact_retention": q.get("key_fact_retention"),
                "key_facts": q.get("key_facts"), "key_facts_matched": q.get("key_facts_matched"),
                "chars_per_1k_input": q.get("chars_per_1k_input"),
                "input_chars": q.get("input_chars"), "output_chars": q.get("output_chars"),
                "warning": nid in warned,
            })

    agg = {}
    for r in rows:
        g = agg.setdefault(r["node"], {"runs": 0, "eligible": 0, "warnings": 0,
                                       "groundings": [], "facts": [], "sources": set()})
        g["runs"] += 1
        g["eligible"] += 1 if r["eligible"] else 0
        g["warnings"] += 1 if r["warning"] else 0
        if isinstance(r["grounding"], (int, float)):
            g["groundings"].append(r["grounding"])
        if isinstance(r["key_fact_retention"], (int, float)):
            g["facts"].append(r["key_fact_retention"])
        if r["source"]:
            g["sources"].add(r["source"])

    nodes = []
    for nid, g in agg.items():
        nodes.append({
            "node": nid, "runs": g["runs"], "eligible_runs": g["eligible"],
            "warnings": g["warnings"], "sources": sorted(g["sources"]),
            "judged": "type" in g["sources"],       # 是否受阈值判定（extract 类型）
            "grounding": _quality_stats(g["groundings"]),
            "key_fact_retention": _quality_stats(g["facts"]),
        })
    # 需关注度排序：有告警的、被判定的、样本多的排前面
    nodes.sort(key=lambda x: (-x["warnings"], not x["judged"], -x["runs"], x["node"]))
    return {"thresholds": quality_thresholds(), "scanned": len(files),
            "total_rows": len(rows), "nodes": nodes, "recent": rows[:25]}


# ============ 历史记录 ============
@app.get("/history")
def list_history(limit: int = 30, offset: int = 0, status: str | None = None, q: str | None = None):
    return {"tasks": db.list_tasks(limit=limit, offset=offset, status=status, q=q)}


@app.get("/history/{task_id}")
def get_history(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "历史记录不存在")
    return task


@app.delete("/history/{task_id}")
def delete_history(task_id: str):
    db.delete_task(task_id)
    return {"status": "success", "message": f"已删除 {task_id}"}


@app.patch("/history/{task_id}")
async def update_history_meta(task_id: str, payload: HistoryMetaRequest):
    db.update_task_meta(task_id, tags=payload.tags, notes=payload.notes)
    return {"status": "success"}


@app.post("/history/{task_id}/rerun")
async def rerun_from_history(task_id: str, file: UploadFile = File(...)):
    """用同一条历史记录的配置快照重跑一份新文件"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "历史记录不存在")
    cfg = task.get("config_snapshot")
    if not cfg:
        raise HTTPException(400, "该记录没有配置快照，无法重跑")

    tmp_cfg = PROJECT_DIR / f"workflow_config.rerun_{task_id}.json"
    with open(tmp_cfg, 'w', encoding='utf-8') as f:
        json_lib.dump(cfg, f, ensure_ascii=False, indent=2)

    return await run_workflow_endpoint(file=file, config_name=tmp_cfg.name)


# ============ 打开输出文件夹 ============
@app.post("/workflow/open_output")
def open_output(req: OpenOutputRequest = None):
    # ★ 修（2026-10-22）: 原来空 body 直接在 Linux 调 explorer.exe ⇒ 500 ✗
    #   应给 4xx 明确提示（该功能仅 Windows 可用）
    import sys as _sys2, platform as _plat2
    if not _sys2.platform.startswith("win") and not _plat2.system().lower().startswith("win"):
        raise HTTPException(400, "打开输出目录功能仅 Windows 支持（当前是 %s）" % _plat2.system())
    if req and req.filename:
        if "/" in req.filename or "\\" in req.filename or ".." in req.filename:
            raise HTTPException(400, "非法文件名")
        target = OUTPUT_DIR / req.filename
        if not target.exists():
            raise HTTPException(404, "文件不存在")
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", str(target)], text=True
            ).strip()
            subprocess.Popen(["explorer.exe", f"/select,{win_path}"])
            return {"status": "success", "message": f"已打开 {win_path}"}
        except Exception as e:
            raise HTTPException(500, f"无法打开文件管理器: {e}")
    else:
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", str(OUTPUT_DIR)], text=True
            ).strip()
            subprocess.Popen(["explorer.exe", win_path])
            return {"status": "success", "message": f"已打开 {win_path}"}
        except Exception as e:
            raise HTTPException(500, f"无法打开: {e}")


# ============ 底座模型管理 ============
@app.get("/vllm/status")
def vllm_status(port: int = 8000):
    st = dict(vllm_manager.get_status(port) or {})
    # ★ ui4-④: model_path 常为 None ✗ → 补上权威值，否则界面显示"运行中 · "（空）
    if not st.get("model_path"):
        try:
            st["model_path"] = get_vllm_base_model() or st.get("model_path")
        except Exception:
            pass
    return _sanitize_paths(st)


@app.get("/vllm/logs")
def vllm_logs(lines: int = 200):
    return {"lines": vllm_manager.get_log_tail(lines)}


@app.post("/vllm/start")
def vllm_start(req: VllmStartRequest):
    settings = load_settings()
    ok, msg = vllm_manager.start_vllm(
        model_path=req.model_path,
        port=req.port,
        extra_args={
            "max_model_len": req.max_model_len,
            "gpu_memory_utilization": req.gpu_memory_utilization
        },
        venv_python=settings["venv_python"]
    )
    if not ok:
        raise HTTPException(400, msg)
    save_settings({"base_model_path": req.model_path})
    return {"status": "success", "pid": msg, "message": "vLLM 正在加载，约 30-90 秒后就绪"}


@app.get("/vllm/recommend")
def vllm_recommend(model_name: str = ""):
    """按【KV cache 预算】推荐 max_model_len / gpu_memory_utilization。

    为什么要暴露（真实教训）:
      前端 index3.html 曾自己按【磁盘体积】分档推荐上下文 ✗，
      Qwen3-4B（7.51GB）落到 "<10GB → 4096" 那一档 → 切底座时写进 .vllm.args.json，
      之后 agent 请求 13036 tokens 直接被 vLLM 400 拒绝 ✗
      而【后端早已改成按 KV 预算算】✓（同一个模型算出 32768 ✓）
      ⇒ 同一能力两条实现、只修了一条 = 本项目反复踩的那一类 ✓
      所以前端不再自己猜: 一律问这个端点 ✓
    """
    name = str(model_name or "").strip()
    if not name:
        base = vllm_manager.get_vllm_base_model() if hasattr(vllm_manager, "get_vllm_base_model") else ""
        name = Path(base).name if base else ""
    model_path = os.path.join(str(Path.home()), "models", name)
    if not os.path.isdir(model_path):
        raise HTTPException(404, f"模型不存在: {model_path}")
    auto = vllm_manager.auto_config_for_model(model_path)
    return {
        "model_name": name,
        "size_gb": vllm_manager.estimate_model_size_gb(model_path),
        "max_model_len": int(auto.get("max_model_len") or 0),
        "gpu_memory_utilization": float(auto.get("gpu_memory_utilization") or 0.85),
        "source": "vllm_manager.auto_config_for_model",
    }


@app.post("/vllm/stop")
def vllm_stop():
    vllm_manager.stop_vllm()
    return {"status": "success", "message": "vLLM 已停止"}


@app.post("/vllm/switch")
def vllm_switch(req: VllmSwitchRequest):
    model_path = os.path.join(str(Path.home()), "models", req.model_name)
    if not os.path.exists(model_path):
        raise HTTPException(404, f"模型不存在: {model_path}")

    task_id = str(uuid.uuid4())[:8]
    vllm_switch_tasks[task_id] = {
        "task_id": task_id,
        "status": "starting",
        "model": req.model_name,
        "error": None,
        "started_at": datetime.now().isoformat()
    }

    def run_in_background():
        try:
            settings = load_settings()
            extra_args = {}
            if req.max_model_len:
                extra_args["max_model_len"] = req.max_model_len
            if req.gpu_memory_utilization:
                extra_args["gpu_memory_utilization"] = req.gpu_memory_utilization

            ok, pid_or_err = vllm_manager.start_vllm(
                model_path=model_path,
                port=8000,
                extra_args=extra_args if extra_args else None,
                venv_python=settings["venv_python"]
            )
            if not ok:
                vllm_switch_tasks[task_id]["status"] = "failed"
                vllm_switch_tasks[task_id]["error"] = pid_or_err
                return

            pid = pid_or_err
            status, detail = vllm_manager.wait_for_startup_or_fail(
                port=8000, timeout=180, pid=pid
            )

            if status == 'ready':
                vllm_switch_tasks[task_id]["status"] = "success"
                save_settings({"base_model_path": model_path})
            else:
                vllm_switch_tasks[task_id]["status"] = "failed"
                vllm_switch_tasks[task_id]["error"] = detail
        except Exception as e:
            vllm_switch_tasks[task_id]["status"] = "failed"
            vllm_switch_tasks[task_id]["error"] = str(e)

    threading.Thread(target=run_in_background, daemon=True).start()
    return {"task_id": task_id, "status": "starting"}


@app.get("/vllm/switch/status/{task_id}")
def vllm_switch_status(task_id: str):
    if task_id not in vllm_switch_tasks:
        raise HTTPException(404, "任务不存在")
    return vllm_switch_tasks[task_id]


@app.get("/models")
def list_models():
    """列出本地底座，并标出【当前正在用的那个】。

    ★ ui4-④ 实测修复: 原来直接返回 list_local_models() ✗ 没有 is_current 字段 ✗
      而 /vllm/status 的 model_path 也是 None ✗ → 界面无法判断哪个底座在用 ✓
      → 这里用权威来源 get_vllm_base_model()（直接问 vLLM）比对 ✓ 填 is_current ✓
    """
    models = vllm_manager.list_local_models()
    try:
        cur = get_vllm_base_model() or ""
        cur_name = str(cur).rstrip("/").split("/")[-1]
        for m in models:
            name = str(m.get("name") or "")
            m["is_current"] = bool(cur_name and (name == cur_name or cur_name.endswith(name)))
    except Exception:
        for m in models:
            m.setdefault("is_current", False)
    return {"models": models}


@app.post("/models/upload")
async def upload_model(file: UploadFile = File(...), model_name: str = Form(...)):
    if not model_name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "模型名称只能包含字母、数字、下划线、中划线")

    models_dir = Path.home() / "models"
    models_dir.mkdir(exist_ok=True)
    target_dir = models_dir / model_name
    if target_dir.exists():
        raise HTTPException(400, f"模型 '{model_name}' 已存在")

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "model.zip")
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(target_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "不是有效的 ZIP 压缩包")

    if not (target_dir / "config.json").exists():
        for item in os.listdir(target_dir):
            sub = target_dir / item
            if sub.is_dir() and (sub / "config.json").exists():
                for sub_item in os.listdir(sub):
                    shutil.move(str(sub / sub_item), str(target_dir))
                os.rmdir(sub)
                break

    if not (target_dir / "config.json").exists():
        shutil.rmtree(target_dir)
        raise HTTPException(400, "压缩包内未找到有效的模型（缺 config.json）")

    return {"status": "success", "message": f"模型 '{model_name}' 已上传"}


# ============ 训练模块 ============
def _update_training_stage(task_id, stage, log_msg=None):
    training_tasks[task_id]["stage"] = stage
    if log_msg:
        training_tasks[task_id]["log"].append(log_msg)


def derive_fp16_model_from_vllm():
    try:
        r = requests.get(f"{VLLM_BASE}/v1/models", timeout=3)
        if r.status_code != 200:
            return None
        models = r.json().get("data", [])
        for m in models:
            if m["id"].startswith("/"):
                current_path = m["id"]
                for suffix in ["-AWQ", "-awq", "-GPTQ", "-gptq", "-Int4", "-int4", "-W4A16", "-w4a16"]:
                    if current_path.endswith(suffix):
                        candidate = current_path[:-len(suffix)]
                        for extra in ["-Instruct", "-instruct", ""]:
                            full = candidate + extra
                            if os.path.exists(full):
                                return full
                for extra in ["-Instruct", "-instruct"]:
                    candidate = current_path + extra
                    if os.path.exists(candidate):
                        return candidate
                break
    except Exception:
        pass
    return None


# ★ P-11: 训练任务状态对账（2026-10-15）
#   实测: 训练列表里 swk_persona_v3 显示 running，而进程早已退出 ✗ ⇒ 会误导"是否有训练在跑"的判断 ✓
#   做法: 模块加载（= 后端启动）时对账一次；无 llama-factory 进程 ⇒ 把 running 标为中断 ✓
def _reconcile_training_tasks():
    try:
        import subprocess as _sp
        alive = bool(_sp.run(["pgrep", "-f", "llamafactory-cli train"],
                             capture_output=True, text=True, timeout=5).stdout.strip())
        if alive:
            return 0
        fixed = 0
        for _t in training_tasks.values():
            if (_t or {}).get("status") == "running":
                _t["status"] = "failed"
                _t["stage"] = "已中断（进程不存在，启动时对账 ✓）"
                _t["finished_at"] = _t.get("finished_at") or datetime.now().isoformat()
                fixed += 1
        if fixed:
            print(f"[training] 启动对账: {fixed} 个假 running 任务已标为中断 ✓", flush=True)
        return fixed
    except Exception:
        return 0


_reconcile_training_tasks()


@app.post("/training/start")
def training_start(req: TrainRequest):
    # ★ COLLECT MODE (2026-10-19): 采集/评测期间禁止真实训练 ✗
    #   背景: drill 任务（P1-47 不可能任务 / P1-20 / P1-46）会让 Agent 自主调 start_training ✗
    #         ⇒ 训练抢占显存 ⇒ vLLM 被停 ⇒ 采集中断 ✗（已发生 2 次 ✗）
    #   开关: settings.json 里 "collect_mode": true ⇒ /training/start 返回 403 ✓
    #   恢复: 删掉该字段或设为 false ✓ 然后重启后端 ✓
    try:
        _st = load_settings()
        if _st.get("collect_mode"):
            raise HTTPException(403, "采集/评测模式已开启（collect_mode=true）——禁止启动真实训练 ✗ 请先关闭 collect_mode")
    except HTTPException:
        raise
    except Exception:
        pass
    if not req.lora_name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "LoRA 名称只能包含字母、数字、下划线、中划线")
    if req.data_count < 10 or req.data_count > 5000:
        raise HTTPException(400, "数据条数必须在 10-5000 之间")

    # ★ P-88（2026-10-19）: 前置校验数据来源 ✓ 防止"假任务"✗
    #   实测踩坑: 只传 {"lora_name":"zz","data_count":10} ⇒ 200 ✓ 任务创建 ✓
    #            但 task_desc 空 ⇒ data_generator 立即失败 ✗ ⇒ 任务 failed ✗
    #            且（若失败发生在 stop_vllm 之后）⇒ vLLM 永久停 ✗ 平台不可用 ✗
    _has_ds = bool(req.dataset_file and os.path.exists(req.dataset_file))
    _has_desc = bool((req.task_desc or "").strip())
    if not _has_ds and not _has_desc:
        raise HTTPException(400,
            "缺少训练数据来源：请提供 dataset_file（已有数据集）"
            "或 task_desc（描述要训什么，用于生成数据）✗")
    if req.dataset_file and not _has_ds:
        raise HTTPException(400, f"数据集文件不存在: {req.dataset_file}")
    if not _has_ds and req.provider_type == "vllm":
        # 生成数据需要 vLLM 在跑 ✓（后面阶段2才停 vLLM ✓ 顺序对 ✓）
        try:
            _r = requests.get("http://127.0.0.1:8000/v1/models", timeout=3)
            if _r.status_code != 200:
                raise HTTPException(400, "生成数据需要 vLLM 在跑，但 vLLM 当前未运行 ✗ 请先启动底座，或改用已有 dataset_file")
        except requests.exceptions.RequestException:
            raise HTTPException(400, "生成数据需要 vLLM 在跑，但连不上 8000 端口 ✗ 请先启动底座，或改用已有 dataset_file")

    # ★ P-3: 训练单飞检查（2026-10-15 实测）
    #   并发训练会让 CUDA 上下文崩: drill_v1 与两条 swk_persona_v3 时间重叠 ⇒
    #   CUBLAS_STATUS_INTERNAL_ERROR（后起的那个死 ✗）
    #   train_lora.py 里还有一道进程级 flock（防绕过 API 直跑 ✓）⇒ 双重保险 ✓
    _running = [t for t in training_tasks.values() if (t or {}).get("status") == "running"]
    if _running:
        _r = _running[0]
        raise HTTPException(409,
            f"已有训练在跑（{_r.get('lora_name')} · {_r.get('task_id')} · {_r.get('stage')}）✗ "
            f"并发训练会让 CUDA 上下文崩溃（实测 CUBLAS_STATUS_INTERNAL_ERROR）——"
            f"请等它结束，或先取消它 ✓")

    settings = load_settings()

    # 前端已不再持有明文 Key：这里用 settings 里的 provider 配置补齐，
    # 保证数据生成能拿到 api_base / api_key（浏览器传空值也照常工作）。
    provider_config = dict(req.provider_config or {})
    if req.provider_type in ("openai", "ollama"):
        stored = (settings.get("providers", {}) or {}).get(req.provider_type, {}) or {}
        incoming = dict(provider_config.get(req.provider_type, {}) or {})
        for k in ("api_base", "model", "api_key"):
            if not incoming.get(k):
                incoming[k] = stored.get(k, "")
        if "*" in str(incoming.get("api_key") or ""):
            incoming["api_key"] = stored.get("api_key", "")
        provider_config[req.provider_type] = incoming

    base_model = (req.base_model or "").strip()
    if not base_model:
        base_model = derive_fp16_model_from_vllm()
    if not base_model:
        raise HTTPException(400, "训练底座未指定，且无法从 vLLM 当前模型推导未量化版本。")
    if not os.path.exists(base_model):
        raise HTTPException(400, f"训练底座路径不存在: {base_model}")

    task_id = str(uuid.uuid4())[:8]
    training_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "stage": "准备中",
        "lora_name": req.lora_name,
        "base_model": base_model,
        "log": [f"[初始化] 训练底座: {base_model}"],
        "error": None,
        "lora_path": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None
    }

    def run_in_background():
        # ★ P-88: 跟踪是否停过 vLLM ⇒ finally 里必须恢复 ✓
        _vllm_was_stopped = False
        try:
            venv_py = settings["venv_python"]
            if not os.path.exists(venv_py):
                raise RuntimeError(f"虚拟环境 Python 不存在: {venv_py}")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            # ★ GPU 配置（2026-10-19 重设计 ✓）: 只有【双卡模式】才指定训练卡 ✓
            #   · gpu_mode="auto"（默认 ✓）⇒ **不设置** CUDA_VISIBLE_DEVICES ✓
            #     ⇒ 训练用默认卡 ✓ 单卡用户与改动前完全一致 ✓
            #   · gpu_mode="dual" ⇒ 用 settings.train_gpu ✓
            #   · 环境变量 TRAIN_GPU_INDEX 可强制覆盖 ✓（高级 ✓）
            try:
                import gpu_config as _gcfg
                _gcfgi = _gcfg.get_gpu_config(settings)
                _env_gpu = _gcfg.cuda_device_env(settings, "train")
                _force_t = os.environ.get("TRAIN_GPU_INDEX", "")
                if _force_t:
                    env["CUDA_VISIBLE_DEVICES"] = str(_force_t)
                    print(f"[training] CUDA_VISIBLE_DEVICES={_force_t} ✓（环境变量强制 ✓）", flush=True)
                elif _env_gpu:
                    env.update(_env_gpu)
                    print(f"[training] 双卡模式 ✓ CUDA_VISIBLE_DEVICES={_env_gpu.get('CUDA_VISIBLE_DEVICES')}（训练卡 ✓）", flush=True)
                else:
                    print(f"[training] 单卡模式 ✓ 不指定 GPU（用默认卡 ✓）| {_gcfgi['reason']}", flush=True)
            except Exception as _te:
                print(f"[training] GPU 配置读取失败（用默认 ✓）: {_te}", flush=True)
            # ★ H5-v2 / P-17: 显式超参必须注入在【env 最后一次重置之后】✗
            #   踩过的坑: 注入写在下面那行 env = os.environ.copy() 之前 ⇒ 被覆盖抹掉 ✗
            #   （日志已打印 "显式超参: rank=32" ✓ 但子进程收不到 ✗ ⇒ 产物永远 r=8 ✗）
            try:
                _rank = getattr(req, "lora_rank", None)
                _eps = getattr(req, "lora_epochs", None)
                if _rank:
                    env["TRAIN_RANK"] = str(int(_rank))
                if _eps:
                    env["TRAIN_EPOCHS"] = str(int(_eps))
                if _rank or _eps:
                    print(f"[training] 显式超参: rank={_rank} epochs={_eps} ✓", flush=True)
            except Exception as _he:
                print(f"[training] 超参注入失败(忽略): {_he}", flush=True)

            # ★ 3a：复用已有数据集，或生成新数据集
            if req.dataset_file and os.path.exists(req.dataset_file):
                dataset_file = req.dataset_file
                _update_training_stage(task_id, "复用数据集",
                    f"[阶段1] 使用已有数据集: {dataset_file}")
                training_tasks[task_id]["log"].append(
                    f"[阶段1] 跳过数据生成，使用: {dataset_file}"
                )
            else:
                _update_training_stage(task_id, "生成训练数据", "[阶段1] 开始生成训练数据...")
                dataset_file = str(TRAINING_DATA_DIR / f"{req.lora_name}.jsonl")

                env["DATA_GEN_PROVIDER"] = req.provider_type
                env["DATA_GEN_PROVIDER_CONFIG"] = json_lib.dumps(provider_config, ensure_ascii=False)

                if req.provider_type == "vllm":
                    try:
                        r = requests.get("http://127.0.0.1:8000/v1/models", timeout=3)
                        if r.status_code != 200:
                            raise RuntimeError("vLLM 当前未运行")
                    except requests.exceptions.RequestException:
                        raise RuntimeError("无法连接 vLLM 8000 端口")

                gen_cmd = [
                    venv_py, str(PROJECT_DIR / "data_generator.py"),
                    req.task_desc, req.format_hint, str(req.data_count), dataset_file
                ]
                proc = subprocess.Popen(
                    gen_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', bufsize=1,
                    cwd=str(PROJECT_DIR), env=env
                )
                for line in proc.stdout:
                    training_tasks[task_id]["log"].append(line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError("数据生成失败")

            training_tasks[task_id]["dataset_file"] = dataset_file

            # ★ 3b：如需审查，暂停等用户确认
            if req.review_before_train:
                event = threading.Event()
                training_tasks[task_id]["waiting_event"] = event
                training_tasks[task_id]["status"] = "waiting_review"
                _update_training_stage(task_id, "等待审查",
                    f"[暂停] 数据集就绪，等待你确认。文件: {dataset_file}")
                training_tasks[task_id]["log"].append(
                    "[暂停] 请在页面上编辑数据，然后点'继续训练'。30 分钟无操作自动取消。"
                )
                got = event.wait(timeout=1800)
                training_tasks[task_id].pop("waiting_event", None)
                if not got or training_tasks[task_id].get("cancel"):
                    raise RuntimeError("用户取消或超时")
                training_tasks[task_id]["status"] = "running"
                _update_training_stage(task_id, "继续训练", "[继续] 用户已确认，开始后续流程")

            _update_training_stage(task_id, "释放显存", "[阶段2] 释放 vLLM 显存...")
            # ★ P-83（2026-10-19）: 双卡模式下【不停 vLLM】✗
            #   vLLM 在 3060（GPU 1 ✓）· 训练在 5060Ti（GPU 0 ✓）⇒ 互不冲突 ✓
            #   ⇒ 训练期间平台【照常可用】✓（迭代效率 +20% ✓）
            #   ⇒ 单卡/未配置时仍走老逻辑（停 vLLM ✓）
            _dual_gpu = False
            try:
                # ★ GPU 配置（2026-10-19 重设计 ✓）: 只有显式启用双卡才跳过停 vLLM ✓
                #   · 默认 gpu_mode="auto" ⇒ 单卡 ⇒ **停 vLLM**（原行为 ✓）
                #   · gpu_mode="dual" + ≥2 卡 ⇒ 不停 vLLM ✓
                #   · 配置不全/只有 1 卡 ⇒ 自动退回单卡 ✓
                import gpu_config as _gcfg2
                _cfg2 = _gcfg2.get_gpu_config(settings)
                _dual_gpu = bool(_cfg2["enabled"])
                training_tasks[task_id]["log"].append(
                    "[阶段2] GPU 模式: %s | %s" % (_cfg2["mode"], _cfg2["reason"]))
                if _dual_gpu:
                    training_tasks[task_id]["log"].append(
                        "[阶段2] 双卡模式 ✓ vLLM 继续在另一张卡上服务（训练不停平台 ✓）")
                else:
                    vllm_manager.stop_vllm()
                    _vllm_was_stopped = True          # ★ 只有真停了才标记 ✓（修 bug ✓）
                    training_tasks[task_id]["log"].append("[阶段2] vLLM 已停止")
            except Exception as e:
                training_tasks[task_id]["log"].append(f"[阶段2] GPU 配置异常（按单卡处理 ✓）: {e}")
                _dual_gpu = False
                try:
                    vllm_manager.stop_vllm()
                    _vllm_was_stopped = True
                    training_tasks[task_id]["log"].append("[阶段2] vLLM 已停止（异常路径 ✓）")
                except Exception as e2:
                    training_tasks[task_id]["log"].append(f"[阶段2] 停止 vLLM 也失败（继续）: {e2}")

            _update_training_stage(task_id, "训练 LoRA", "[阶段3] 开始训练 LoRA...")
            output_dir = str(TRAINING_OUTPUT_DIR / req.lora_name)

            train_cmd = [
                venv_py, str(PROJECT_DIR / "train_lora.py"),
                base_model, dataset_file, output_dir, req.lora_name, str(req.data_count)
            ]
            proc = subprocess.Popen(
                train_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', bufsize=1,
                cwd=str(PROJECT_DIR), env=env
            )
            for line in proc.stdout:
                training_tasks[task_id]["log"].append(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("训练失败")

            lora_path = os.path.expanduser(f"~/loras/{req.lora_name}")
            training_tasks[task_id]["lora_path"] = lora_path
            training_tasks[task_id]["log"].append(f"[阶段3] 训练完成，已注册到 {lora_path}")

            if _dual_gpu:
                # ★ P-83: 双卡模式下 vLLM 从没停过 ⇒ 不需要重启 ✓
                training_tasks[task_id]["log"].append(
                    "[阶段4] 双卡模式：vLLM 全程未停 ✓ 无需重启")
            elif req.auto_restart_vllm:
                _update_training_stage(task_id, "重启 vLLM", "[阶段4] 重启 vLLM 恢复推理服务...")
                try:
                    inference_model = settings.get("base_model_path") or base_model
                    ok, pid_or_err = vllm_manager.start_vllm(
                        model_path=inference_model,
                        port=8000,
                        venv_python=venv_py
                    )
                    if ok:
                        training_tasks[task_id]["log"].append(
                            f"[阶段4] vLLM 已重启（PID {pid_or_err}，推理底座 {inference_model}），等待就绪..."
                        )
                        status, detail = vllm_manager.wait_for_startup_or_fail(
                            port=8000, timeout=180, pid=pid_or_err
                        )
                        if status == 'ready':
                            training_tasks[task_id]["log"].append("[阶段4] vLLM 就绪")
                        else:
                            training_tasks[task_id]["log"].append(f"[阶段4] vLLM 启动异常: {detail}")
                    else:
                        training_tasks[task_id]["log"].append(f"[阶段4] vLLM 启动失败: {pid_or_err}")
                except Exception as e:
                    training_tasks[task_id]["log"].append(f"[阶段4] vLLM 重启异常: {e}")
            else:
                training_tasks[task_id]["log"].append("[阶段4] 已跳过 vLLM 重启")

            training_tasks[task_id]["status"] = "success"
            training_tasks[task_id]["stage"] = "完成"
            training_tasks[task_id]["finished_at"] = datetime.now().isoformat()
            training_tasks[task_id]["log"].append("✅ 训练任务全部完成！")

        except Exception as e:
            training_tasks[task_id]["status"] = "failed"
            training_tasks[task_id]["stage"] = "失败"
            training_tasks[task_id]["error"] = str(e)
            training_tasks[task_id]["finished_at"] = datetime.now().isoformat()
            training_tasks[task_id]["log"].append(f"❌ 失败：{e}")
        finally:
            # ★ P-88（2026-10-19）: 只要停过 vLLM 就必须恢复 ✗
            #   实测事故: 任务在 stop_vllm 之后失败 ⇒ vLLM 永久停 ✗ 平台不可用 ✗
            if _vllm_was_stopped:
                try:
                    _up = False
                    try:
                        _rr = requests.get("http://127.0.0.1:8000/v1/models", timeout=3)
                        _up = (_rr.status_code == 200)
                    except Exception:
                        _up = False
                    if not _up:
                        training_tasks[task_id]["log"].append(
                            "[恢复] 检测到 vLLM 已停 ⇒ 自动重启以恢复平台可用性 ✓"
                        )
                        _im = settings.get("base_model_path") or "Qwen2.5-7B-Instruct-AWQ"
                        try:
                            ok2, pid2 = vllm_manager.start_vllm(
                                model_path=_im, port=8000,
                                venv_python=settings.get("venv_python")
                            )
                            if ok2:
                                vllm_manager.wait_for_startup_or_fail(
                                    port=8000, timeout=180, pid=pid2
                                )
                                training_tasks[task_id]["log"].append(
                                    f"[恢复] vLLM 已重启（PID {pid2}）✓"
                                )
                            else:
                                training_tasks[task_id]["log"].append(
                                    f"[恢复] vLLM 重启失败: {pid2} ✗ 请手动启动"
                                )
                        except Exception as _re:
                            training_tasks[task_id]["log"].append(
                                f"[恢复] vLLM 重启异常: {_re} ✗ 请手动启动"
                            )
                except Exception:
                    pass

    threading.Thread(target=run_in_background, daemon=True).start()
    return {"task_id": task_id, "status": "running"}


def _dataset_digest(path, max_scan=60):
    """抽一份数据集摘要（★ 用途/示例任务/角色）—— 治"列表信息不足导致 agent 乱挖"✗。

    实测: 任务问"这些数据集各适合训什么能力"✗ 而接口只给 name/size/lines ✓
      → agent 只能翻源码/数据文件找答案 → 探索 20+ 次 → 超时 → 空产出 ✓（20 条里 4 条）
    而数据里【现成有答案】: conversations[0] 的 system 首句就是能力说明 ✓
      （例「你是论文分析助手，只输出严格 JSON。」= 它就是练"严格 JSON 输出"✓）
    """
    purpose = sample_task = ""
    roles = []
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                n += 1
                if n > max_scan:
                    break
                try:
                    obj = json_lib.loads(line)
                except Exception:
                    continue
                convs = obj.get("conversations") or obj.get("messages") or []
                if not isinstance(convs, list):
                    continue
                for c in convs:
                    if not isinstance(c, dict):
                        continue
                    role = str(c.get("from") or c.get("role") or "").strip().lower()
                    if role and role not in roles:
                        roles.append(role)
                    val = str(c.get("value") or c.get("content") or "").strip()
                    if not purpose and role in ("system",):
                        purpose = val[:80]
                    if not sample_task and role in ("human", "user"):
                        sample_task = val[:60]
                if purpose and sample_task:
                    break
    except Exception:
        pass
    return purpose, sample_task, roles


# ══════════════════════════════════════════════════════════
# ★ P-73 / P1（2026-10-19）数据工具接口 —— 上传数据检测/清洗/排列/切分
#   全部复用 dataset_toolkit（唯一格式真相 ✓）
#   铁律: 永远不覆盖用户原文件 ✓ 只写 _clean/_sorted/_train/_val ✓
# ══════════════════════════════════════════════════════════
def _dz_pick_file(payload: dict | None):
    """从 payload 里解析文件路径 ✓（安全: 必须在 training_data/ 下 ✓）"""
    import pathlib as _pl
    payload = payload or {}
    raw = str(payload.get("file") or payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "缺少 file 参数（数据集文件路径）")
    p = _pl.Path(raw)
    if not p.is_absolute():
        p = TRAINING_DATA_DIR / p.name
    # 安全检查 ✓ 必须在允许目录里 ✓
    try:
        p = p.resolve()
    except Exception:
        raise HTTPException(400, "路径非法")
    allowed_roots = [_pl.Path(TRAINING_DATA_DIR).resolve(), _pl.Path(UPLOAD_DIR).resolve()]
    if not any(str(p).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(403, "只允许 training_data/ 或 uploads/ 下的文件")
    if not p.exists():
        raise HTTPException(404, "文件不存在: %s" % p.name)
    return p


def _dz_safe_out_name(stem: str, suffix: str) -> str:
    """生成安全输出名 ✓（不含路径 ✓）"""
    import re as _re
    safe = _re.sub(r'[^\w\-.]', '_', stem)[:60]
    return safe + suffix


@app.post("/training/dataset/analyze")
async def dz_analyze(payload: dict = None):
    """数据检测 ✓（只读 ✓ 不写盘 ✓）

    body: {"file": "xxx.jsonl"} 或上传后用返回的 saved_as
    returns: {ok, file, total, report, problems, invalid_examples}
    """
    try:
        import dataset_toolkit as _dz
        p = _dz_pick_file(payload)
        records = _dz.parse_any(p)
        if not records:
            return {"ok": False, "error": "解析出 0 条记录（文件可能是空/格式不符）", "file": p.name}
        rep = _dz.report(records)
        return {
            "ok": True,
            "file": p.name,
            "total": len(records),
            "report": rep,
            "analysis": rep.get("dataset", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/training/dataset/upload_and_analyze")
async def dz_upload_and_analyze(file: UploadFile = File(...)):
    """上传数据文件 + 立即检测 ✓（保存原文件到 training_data/ ✓ 然后只读分析 ✓）"""
    try:
        import dataset_toolkit as _dz
        import time as _time
        fname = file.filename or "upload.jsonl"
        ext = pathlib.Path(fname).suffix.lower()
        if ext not in (".jsonl", ".json", ".csv", ".ndjson", ".txt"):
            raise HTTPException(400, "只支持 .jsonl / .json / .csv / .txt")
        stamp = _time.strftime("%Y%m%d_%H%M%S")
        out_name = _dz_safe_out_name(pathlib.Path(fname).stem, "_%s%s" % (stamp, ext))
        out_path = pathlib.Path(TRAINING_DATA_DIR) / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(400, "文件超过 100MB")
        out_path.write_bytes(content)
        records = _dz.parse_any(out_path)
        if not records:
            return {"ok": False, "error": "解析出 0 条记录（格式不符）", "saved_as": out_name}
        rep = _dz.report(records)
        return {
            "ok": True,
            "saved_as": out_name,
            "file": out_name,
            "total": len(records),
            "report": rep,
            "analysis": rep.get("dataset", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/training/dataset/clean")
def dz_clean(payload: dict = None):
    """清洗 ✓（生成新文件 ✓ 绝不覆盖原文件 ✓）

    body: {"file": "...", "opts": {...}, "out_name": "可选"}
    returns: {ok, in_file, out_file, stats, report}
    """
    try:
        import dataset_toolkit as _dz
        payload = payload or {}
        p = _dz_pick_file(payload)
        opts = payload.get("opts") or {}
        if not isinstance(opts, dict):
            opts = {}
        records = _dz.parse_any(p)
        cleaned, stats = _dz.clean(records, opts)
        out_name = str(payload.get("out_name") or "").strip()
        if not out_name:
            out_name = _dz_safe_out_name(p.stem, "_clean.jsonl")
        out_path = pathlib.Path(TRAINING_DATA_DIR) / out_name
        n = _dz.write_jsonl(cleaned, out_path)
        rep = _dz.report(cleaned)
        return {
            "ok": True,
            "in_file": p.name,
            "out_file": out_path.name,
            "out_path": str(out_path),
            "written": n,
            "stats": stats,
            "report": rep,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/training/dataset/preview")
def dz_preview(file: str = "", limit: int = 5):
    """预览 ✓（人类可读 ✓）"""
    try:
        import dataset_toolkit as _dz
        p = _dz_pick_file({"file": file})
        records = _dz.parse_any(p)
        limit = max(1, min(int(limit or 5), 50))
        out = []
        for i, r in enumerate(records[:limit]):
            conv = r.get("conversations") or r.get("messages") or []
            turns = []
            for c in conv[:8]:
                if isinstance(c, dict):
                    turns.append({
                        "from": c.get("from") or c.get("role"),
                        "value": str(c.get("value") or c.get("content") or "")[:200],
                    })
            out.append({
                "idx": i,
                "system": str(r.get("system") or "")[:200],
                "turns": turns,
                "raw_keys": list(r.keys())[:8],
            })
        return {"ok": True, "file": p.name, "total": len(records), "items": out}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/training/dataset/sort")
def dz_sort(payload: dict = None):
    """一键排列 ✓（写新文件 ✓）"""
    try:
        import dataset_toolkit as _dz
        payload = payload or {}
        p = _dz_pick_file(payload)
        by = str(payload.get("by") or "task").strip().lower()
        if by not in ("task", "length", "quality", "random"):
            return {"ok": False, "error": "by 必须是 task/length/quality/random"}
        records = _dz.parse_any(p)
        sorted_rs = _dz.sort_records(records, by)
        out_name = str(payload.get("out_name") or "").strip() or _dz_safe_out_name(p.stem, "_sorted.jsonl")
        out_path = pathlib.Path(TRAINING_DATA_DIR) / out_name
        n = _dz.write_jsonl(sorted_rs, out_path)
        return {"ok": True, "in_file": p.name, "out_file": out_path.name, "by": by, "written": n}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/training/dataset/split")
def dz_split(payload: dict = None):
    """切分 train/val ✓（写两个新文件 ✓ 分层 ✓）"""
    try:
        import dataset_toolkit as _dz
        payload = payload or {}
        p = _dz_pick_file(payload)
        try:
            ratio = float(payload.get("ratio") or 0.1)
        except (ValueError, TypeError):
            ratio = 0.1
        ratio = max(0.01, min(0.5, ratio))
        records = _dz.parse_any(p)
        train, val = _dz.split(records, ratio)
        stem = p.stem
        train_name = str(payload.get("train_name") or "").strip() or _dz_safe_out_name(stem, "_train.jsonl")
        val_name = str(payload.get("val_name") or "").strip() or _dz_safe_out_name(stem, "_val.jsonl")
        n_train = _dz.write_jsonl(train, pathlib.Path(TRAINING_DATA_DIR) / train_name)
        n_val = _dz.write_jsonl(val, pathlib.Path(TRAINING_DATA_DIR) / val_name)
        return {
            "ok": True,
            "in_file": p.name,
            "ratio": ratio,
            "train_file": train_name,
            "val_file": val_name,
            "train_count": n_train,
            "val_count": n_val,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/training/available_datasets")
def training_list_datasets():
    """列出 training_data 目录下所有 jsonl（含"这个集训什么"的摘要 ✓）。"""
    datasets = []
    if TRAINING_DATA_DIR.exists():
        for f in TRAINING_DATA_DIR.glob("*.jsonl"):
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    lines = sum(1 for l in fp if l.strip())
            except Exception:
                lines = 0
            purpose, sample_task, roles = _dataset_digest(f)
            datasets.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "lines": lines,
                # ★ 这三项就是为了回答"适合训什么能力"——不用再去挖文件 ✓
                "purpose": purpose or "（无 system 提示词，看 sample_task 判断）",
                "sample_task": sample_task,
                "roles": roles,
            })
    return {"datasets": sorted(datasets, key=lambda x: x["mtime"], reverse=True)}


@app.post("/training/upload_dataset")
async def training_upload_dataset(file: UploadFile = File(...), name: str = Form(...)):
    """上传 jsonl 数据集"""
    if not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "名称只能包含字母、数字、下划线、中划线")
    if not file.filename.lower().endswith(".jsonl"):
        raise HTTPException(400, "只支持 .jsonl 文件")

    target = TRAINING_DATA_DIR / f"{name}.jsonl"
    if target.exists():
        raise HTTPException(400, f"'{name}.jsonl' 已存在")

    content = await file.read()
    # ★ 修（2026-10-22 D1 实测）：messages→conversations 自动转换
    #   原来上传接受 messages 格式，训练体检 F0 全拒（valid_ratio=0）✗
    #   ⇒ 用户传完才被拒，白费一步 ✗ 修: 上传时就转 ✓
    try:
        import json as _jj
        _ls0 = [x for x in content.decode("utf-8", "replace").split("\n") if x.strip()]
        _ROLE = {"user": "human", "assistant": "gpt", "system": "system"}
        _out0 = []
        _conv_n = 0
        for _l0 in _ls0:
            try:
                _o0 = _jj.loads(_l0)
            except Exception:
                _out0.append(_l0); continue
            if isinstance(_o0, dict) and ("conversations" in _o0 or "from" in _o0):
                _out0.append(_l0); continue
            if isinstance(_o0, dict) and isinstance(_o0.get("messages"), list):
                _cv = [{"from": _ROLE.get(str(_x.get("role") or "user"), "human"),
                        "value": str(_x.get("content") or "")} for _x in _o0["messages"]]
                _out0.append(_jj.dumps({"conversations": _cv}, ensure_ascii=False))
                _conv_n += 1
            else:
                _out0.append(_l0)
        if _conv_n:
            content = ("\n".join(_out0) + "\n").encode("utf-8")
            print("[training/upload] messages→conversations 自动转换 %d 条" % _conv_n, flush=True)
    except Exception as _e0:
        print("[training/upload] 格式转换跳过: %s" % _e0, flush=True)
    try:
        text = content.decode('utf-8')
    except Exception:
        raise HTTPException(400, "文件必须是 UTF-8 编码")

    valid_count = 0
    for i, line in enumerate(text.split('\n')):
        line = line.strip()
        if not line:
            continue
        try:
            d = json_lib.loads(line)
            if "conversations" not in d and "messages" not in d:
                raise ValueError("每行必须包含 'conversations' 或 'messages'")
            valid_count += 1
        except Exception as e:
            raise HTTPException(400, f"第 {i+1} 行格式错误: {e}")

    if valid_count == 0:
        raise HTTPException(400, "数据集为空")

    with open(target, 'wb') as f:
        f.write(content)
    return {"status": "success", "name": name, "path": str(target), "count": valid_count}


@app.get("/training/{task_id}/dataset")
def training_get_dataset(task_id: str):
    """读取数据集供编辑（3b）"""
    if task_id not in training_tasks:
        raise HTTPException(404, "任务不存在")
    task = training_tasks[task_id]
    dataset_file = task.get("dataset_file")
    if not dataset_file or not os.path.exists(dataset_file):
        raise HTTPException(404, "数据集文件不存在")
    items = []
    with open(dataset_file, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                items.append({"idx": i, "data": json_lib.loads(line)})
            except Exception:
                pass
    return {"dataset_file": dataset_file, "items": items}


@app.post("/training/{task_id}/dataset")
def training_save_dataset(task_id: str, req: SaveDatasetRequest):
    """保存编辑后的数据集（3b）"""
    if task_id not in training_tasks:
        raise HTTPException(404, "任务不存在")
    task = training_tasks[task_id]
    dataset_file = task.get("dataset_file")
    if not dataset_file:
        raise HTTPException(400, "任务无关联数据集")
    with open(dataset_file, 'w', encoding='utf-8') as f:
        for item in req.items:
            data = item.get("data") if isinstance(item, dict) and "data" in item else item
            f.write(json_lib.dumps(data, ensure_ascii=False) + "\n")
    return {"status": "success", "count": len(req.items)}


@app.post("/training/{task_id}/confirm")
def training_confirm(task_id: str, req: ConfirmTrainingRequest):
    """3b：确认继续训练 or 取消"""
    if task_id not in training_tasks:
        raise HTTPException(404, "任务不存在")
    task = training_tasks[task_id]
    if task.get("status") != "waiting_review":
        raise HTTPException(400, f"任务状态不是 waiting_review（当前: {task.get('status')}）")
    if req.action == "cancel":
        task["cancel"] = True
    event = task.get("waiting_event")
    if event:
        event.set()
    return {"status": "success", "action": req.action}


@app.get("/training/status/{task_id}")
def training_status(task_id: str):
    if task_id not in training_tasks:
        raise HTTPException(404, "任务不存在")
    task = dict(training_tasks[task_id])
    # ★ 剔除无法 JSON 序列化的对象（threading.Event 等）
    task.pop("waiting_event", None)
    task.pop("cancel", None)
    task["log"] = task["log"][-300:]
    return task



# ============================================================================
# ★ P-5: GPU 清理接口（清孤儿 vLLM 引擎进程 + 等显存回落）
#   为什么要它: 训练失败后每次留 3~5 个 VLLM::EngineCore 孤儿（~15.5GB ✗），
#   下一次启动必然 OOM ⇒ keeper 无限重拉。以前只能人工 nvidia-smi + kill ✓
# ============================================================================
@app.get("/gpu/config")
def get_gpu_config_api():
    """P-102（2026-10-19）: 读 GPU 配置 + 可用卡列表（设置页用 ✓）"""
    try:
        import gpu_config as _gcfg
        _st = load_settings() or {}
        _cfg = _gcfg.get_gpu_config(_st)
        return {
            "ok": True,
            "gpu_mode": _cfg["mode"],
            "inference_gpu": _cfg["inference_gpu"],
            "train_gpu": _cfg["train_gpu"],
            "dual_enabled": _cfg["enabled"],
            "reason": _cfg["reason"],
            "gpus": _gcfg.detect_gpus(),
            "gpu_count": _gcfg.gpu_count(),
            "can_dual": _gcfg.has_multiple_gpus(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "gpus": [], "gpu_count": 0, "can_dual": False}


@app.post("/gpu/config")
def set_gpu_config_api(payload: dict = None):
    """P-102（2026-10-19）: 写 GPU 配置（单卡/双卡切换 ✓）

    body: {"gpu_mode": "auto"|"dual", "inference_gpu": int|null, "train_gpu": int|null}
    ★ 保存后不重启服务 ✓ 下次 vLLM 启动 / 训练启动时生效 ✓
    """
    payload = payload or {}
    try:
        import json as _json
        import gpu_config as _gcfg

        mode = str(payload.get("gpu_mode") or "auto").strip().lower()
        if mode not in ("auto", "dual"):
            return {"ok": False, "error": "gpu_mode 必须是 auto 或 dual（收到 %r）" % mode}

        # 校验卡号 ✓
        _gpus = _gcfg.detect_gpus()
        _idx = set(g["index"] for g in _gpus)
        inf = payload.get("inference_gpu")
        tr = payload.get("train_gpu")
        try:
            inf = int(inf) if inf is not None and str(inf).strip() != "" else None
        except (ValueError, TypeError):
            return {"ok": False, "error": "inference_gpu 必须是整数或 null"}
        try:
            tr = int(tr) if tr is not None and str(tr).strip() != "" else None
        except (ValueError, TypeError):
            return {"ok": False, "error": "train_gpu 必须是整数或 null"}

        if mode == "dual":
            if len(_gpus) < 2:
                return {"ok": False, "error": "双卡模式需要 ≥2 张 GPU，当前只检测到 %d 张" % len(_gpus)}
            if inf is None or tr is None:
                return {"ok": False, "error": "双卡模式必须指定 inference_gpu 和 train_gpu"}
            if inf == tr:
                return {"ok": False, "error": "推理卡和训练卡不能是同一张（都是 GPU%d）" % inf}
            if inf not in _idx or tr not in _idx:
                return {"ok": False, "error": "卡号超出范围（可用: %s）" % sorted(_idx)}
        else:
            # 单卡模式：卡号清空 ✓（避免残留误导 ✓）
            inf = None
            tr = None

        # 读 → 改 → 写 settings.json ✓
        _sp = PROJECT_DIR / "settings.json"
        try:
            _s = _json.loads(_sp.read_text(encoding="utf-8"))
        except Exception:
            _s = {}
        _s["gpu_mode"] = mode
        _s["inference_gpu"] = inf
        _s["train_gpu"] = tr
        # 同步旧字段（兼容仍在读旧字段的代码 ✓）
        if inf is not None:
            _s["vllm_gpu_index"] = inf
        if tr is not None:
            _s["train_gpu_index"] = tr
        _s["dual_gpu_mode"] = (mode == "dual")
        _sp.write_text(_json.dumps(_s, ensure_ascii=False, indent=2), encoding="utf-8")

        _new = _gcfg.get_gpu_config(_s)
        return {
            "ok": True,
            "gpu_mode": _new["mode"],
            "inference_gpu": _new["inference_gpu"],
            "train_gpu": _new["train_gpu"],
            "dual_enabled": _new["enabled"],
            "reason": _new["reason"],
            "note": "已保存 ✓ 下次启动 vLLM / 训练时生效（当前运行中的服务不受影响 ✓）",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/gpu/status")
def gpu_status():
    """P-82（2026-10-19）: 双卡实时状态（训练卡 / vLLM 卡分工 ✓）

    返回每张卡的: 名称/显存/温度/负载/功耗/角色
    + vLLM 运行状态 + 是否有训练在跑
    """
    try:
        import subprocess as _sp
        _st = load_settings() or {}
        # ★ 2026-10-19 重设计: 动态读配置 ✓ 不硬编码卡号 ✓
        try:
            import gpu_config as _gcfg3
            _cfg3 = _gcfg3.get_gpu_config(_st)
        except Exception:
            _cfg3 = {"mode": "auto", "inference_gpu": None, "train_gpu": None,
                     "enabled": False, "reason": "gpu_config 不可用"}
        _vg = _cfg3.get("inference_gpu")
        _tg = _cfg3.get("train_gpu")
        _dual3 = bool(_cfg3.get("enabled"))
        out = _sp.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,"
             "temperature.gpu,utilization.gpu,power.draw,power.limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout
        gpus = []
        for line in (out or "").strip().split("\n"):
            if not line.strip():
                continue
            p = [x.strip() for x in line.split(",")]
            if len(p) < 8:
                continue
            g = {
                "index": int(float(p[0])),
                "name": p[1],
                "mem_used": int(float(p[2])),
                "mem_total": int(float(p[3])),
                "temp": int(float(p[4])),
                "util": int(float(p[5])),
                "power": round(float(p[6]), 1),
                "power_limit": round(float(p[7]), 1),
            }
            # 角色判定（动态 ✓）:
            #   双卡模式 ⇒ 按配置标 vLLM/训练 ✓
            #   单卡模式 ⇒ 不标固定角色 ✗ 只按负载标（忙碌/空闲 ✓）
            if _dual3 and g["index"] == _vg:
                g["role"] = "vLLM 推理"
            elif _dual3 and g["index"] == _tg:
                g["role"] = "训练"
            elif g["util"] > 20:
                g["role"] = "忙碌"
            else:
                g["role"] = "空闲"
            gpus.append(g)
        _running = [t for t in training_tasks.values() if (t or {}).get("status") == "running"]
        _vllm_running = False
        try:
            _vllm_running = bool(vllm_manager.status().get("running"))
        except Exception:
            try:
                import requests as _rq
                _vllm_running = (_rq.get("http://127.0.0.1:8000/v1/models", timeout=2).status_code == 200)
            except Exception:
                _vllm_running = False
        return {
            "gpus": gpus,
            "vllm_gpu": _vg,
            "train_gpu": _tg,
            "gpu_mode": _cfg3.get("mode", "auto"),
            "dual_enabled": _dual3,
            "gpu_config_reason": _cfg3.get("reason", ""),
            "gpu_count": len(gpus),
            "vllm_running": _vllm_running,
            "training_running": bool(_running),
            "training_task": (_running[0].get("lora_name") if _running else None),
        }
    except Exception as e:
        return {"error": str(e), "gpus": []}


@app.post("/gpu/cleanup")
def gpu_cleanup(payload: dict = None):
    import vllm_manager as _vm
    before = _vm.gpu_used_mib()
    killed = _vm.reap_orphan_enginecores()
    released = _vm.wait_for_gpu_release(target_mib=2500, timeout=30)
    after = _vm.gpu_used_mib()
    return {"status": "success", "killed": killed, "before_mib": before, "after_mib": after,
            "released": released,
            "note": "只清【没有对应 entrypoints 进程】的 VLLM::EngineCore 孤儿 ✓ 正常实例不动 ✓"}


@app.get("/training/list")
def training_list():
    result = []
    for tid, t in training_tasks.items():
        result.append({
            "task_id": tid,
            "lora_name": t["lora_name"],
            "status": t["status"],
            "stage": t["stage"],
            "started_at": t["started_at"],
            "finished_at": t["finished_at"]
        })
    return {"tasks": sorted(result, key=lambda x: x["started_at"], reverse=True)[:30]}

# ============ 对话 ============
# ============ 文档助手 P1：Excel 只读 ============
EXCEL_EXTS = (".xlsx", ".xlsm")


def docs_resolve(name):
    """把文档名解析成 uploads/ 内的绝对路径。**拒绝路径穿越**（只允许纯文件名）。"""
    s = str(name or "").strip()
    if not s:
        raise HTTPException(400, "需要 filename")
    if "/" in s or "\\" in s or ".." in s:
        raise HTTPException(400, f"非法文件名: {s}")
    if Path(s).suffix.lower() not in EXCEL_EXTS:
        raise HTTPException(400, "只支持 .xlsx / .xlsm（Excel）")
    path = UPLOAD_DIR / s
    if not path.exists():
        raise HTTPException(404, f"文件不存在: {s}（可用 list_uploads 查看已上传文件）")
    return path


def _open_wb(path):
    try:
        import openpyxl
    except ImportError as e:
        raise HTTPException(500, f"openpyxl 未安装：{e}")
    try:
        return openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(500, f"打开工作簿失败：{type(e).__name__}: {e}")


def list_sheets_of(path):
    """列出所有 sheet 及其规模（行/列）。"""
    wb = _open_wb(path)
    try:
        out = []
        for ws in wb.worksheets:
            out.append({"name": ws.title,
                        "rows": getattr(ws, "max_row", None),
                        "cols": getattr(ws, "max_column", None)})
        return {"sheets": out, "active": out[0]["name"] if out else None}
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _rows_of(ws, limit=None):
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if limit is not None and i >= limit:
            break
        rows.append(["" if c is None else c for c in row])
    return rows


def read_sheet_summary(path, sheet=None, rows=10):
    """读表：sheet 名、表头、前 N 行（默认 10）、以及总行数。"""
    wb = _open_wb(path)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        data = _rows_of(ws, limit=max(int(rows or 10), 1) + 1)
        header = data[0] if data else []
        body = data[1:] if len(data) > 1 else []
        return {"sheet": ws.title,
                "sheets": [w.title for w in wb.worksheets],
                "total_rows": getattr(ws, "max_row", None),
                "total_cols": getattr(ws, "max_column", None),
                "header": [str(h) for h in header],
                "rows": body}
    finally:
        try:
            wb.close()
        except Exception:
            pass


_OPS = ("==", "!=", ">", ">=", "<", "<=", "contains")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def filter_rows_of(path, sheet, column, op="==", value="", limit=50):
    """按列筛选行。column 可以是表头名或列序号(1 起)。"""
    if op not in _OPS:
        raise HTTPException(400, f"op 只支持 {_OPS}")
    wb = _open_wb(path)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        data = _rows_of(ws)
        if not data:
            return {"sheet": ws.title, "matched": 0, "rows": [], "note": "空表"}
        header = [str(h) for h in data[0]]
        idx = None
        if isinstance(column, int) or (isinstance(column, str) and column.isdigit()):
            idx = int(column) - 1
        elif column in header:
            idx = header.index(column)
        else:
            cands = [i for i, h in enumerate(header) if str(column) in h]
            if len(cands) == 1:
                idx = cands[0]
            else:
                return {"sheet": ws.title, "matched": 0, "rows": [],
                        "error": f"找不到列 {column!r}", "header": header}
        out = []
        for r in data[1:]:
            if idx >= len(r):
                continue
            cell = r[idx]
            hit = False
            cn, vn = _num(cell), _num(value)
            if op == "contains":
                hit = str(value) in str(cell)
            elif op in ("==", "!="):
                hit = (str(cell) == str(value)) or (cn is not None and vn is not None and cn == vn)
                if op == "!=":
                    hit = not hit
            elif cn is not None and vn is not None:
                hit = {"<": cn < vn, "<=": cn <= vn, ">": cn > vn, ">=": cn >= vn}[op]
            if hit:
                out.append(r)
                if len(out) >= int(limit or 50):
                    break
        return {"sheet": ws.title, "column": header[idx] if idx < len(header) else column,
                "op": op, "value": value, "matched": len(out), "header": header, "rows": out}
    finally:
        try:
            wb.close()
        except Exception:
            pass


def group_sum_of(path, sheet, group_by, sum_column, limit=30):
    """按 group_by 列分组，对 sum_column 列求和（也给出计数与均值）。"""
    wb = _open_wb(path)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        data = _rows_of(ws)
        if len(data) < 2:
            return {"sheet": ws.title, "groups": [], "note": "表里没有数据行"}
        header = [str(h) for h in data[0]]

        def find(col):
            if isinstance(col, int) or (isinstance(col, str) and col.isdigit()):
                return int(col) - 1
            if col in header:
                return header.index(col)
            cands = [i for i, h in enumerate(header) if str(col) in h]
            return cands[0] if cands else None

        gi, si = find(group_by), find(sum_column)
        if gi is None:
            raise HTTPException(400, f"找不到分组列 {group_by!r}；表头：{header}")
        if si is None:
            raise HTTPException(400, f"找不到求和列 {sum_column!r}；表头：{header}")
        agg = {}
        for r in data[1:]:
            if gi >= len(r) or si >= len(r):
                continue
            k = str(r[gi])
            v = _num(r[si])
            if v is None:
                continue
            a = agg.setdefault(k, {"sum": 0.0, "n": 0})
            a["sum"] += v
            a["n"] += 1
        groups = [{"group": k, "sum": round(a["sum"], 4), "count": a["n"],
                   "avg": round(a["sum"] / a["n"], 4) if a["n"] else None}
                  for k, a in agg.items()]
        groups.sort(key=lambda x: x["sum"], reverse=True)
        return {"sheet": ws.title, "group_by": header[gi], "sum_column": header[si],
                "groups": groups[:int(limit or 30)], "group_count": len(groups)}
    finally:
        try:
            wb.close()
        except Exception:
            pass


@app.get("/docs/sheets")
def docs_sheets(filename: str):
    """列出 Excel 的所有 sheet。"""
    return {"filename": filename, **list_sheets_of(docs_resolve(filename))}


@app.post("/docs/read_sheet")
def docs_read_sheet(payload: dict):
    """读表概览：表头 + 前 N 行。body: {filename, sheet?, rows?}"""
    fn = payload.get("filename")
    return {"filename": fn,
            **read_sheet_summary(docs_resolve(fn), payload.get("sheet"),
                                 payload.get("rows", 10))}


@app.post("/docs/filter_rows")
def docs_filter_rows(payload: dict):
    """筛选：body: {filename, sheet?, column, op?, value?, limit?}"""
    fn = payload.get("filename")
    return {"filename": fn,
            **filter_rows_of(docs_resolve(fn), payload.get("sheet"),
                             payload.get("column"), payload.get("op", "=="),
                             payload.get("value", ""), payload.get("limit", 50))}


@app.post("/docs/group_sum")
def docs_group_sum(payload: dict):
    """分组汇总：body: {filename, sheet?, group_by, sum_column, limit?}"""
    fn = payload.get("filename")
    return {"filename": fn,
            **group_sum_of(docs_resolve(fn), payload.get("sheet"),
                           payload.get("group_by"), payload.get("sum_column"),
                           payload.get("limit", 30))}


# ============ 文档助手 P2：写回（只写新文件）============
WORD_EXTS = (".docx",)


def docs_out_path(name, ext):
    """为新产物在 uploads/ 内生成一个**不冲突**的路径：同名自动加 _1/_2…

    这是本期最重要的安全约束：**任何写操作都不覆盖已有文件**。
    """
    import re as _re
    base = Path(str(name or "result")).stem or "result"
    safe = _re.sub(r"[^\w\-\u4e00-\u9fff]", "_", base)[:40] or "result"
    cand = UPLOAD_DIR / f"{safe}{ext}"
    i = 1
    while cand.exists():
        cand = UPLOAD_DIR / f"{safe}_{i}{ext}"
        i += 1
    return cand


def docs_resolve_doc(name, exts):
    """把文档名解析成 uploads/ 内的路径（可指定允许的扩展名）。拒绝路径穿越。"""
    s = str(name or "").strip()
    if not s:
        raise HTTPException(400, "需要 filename")
    if "/" in s or "\\" in s or ".." in s:
        raise HTTPException(400, f"非法文件名: {s}")
    if Path(s).suffix.lower() not in tuple(exts):
        raise HTTPException(400, f"只支持 {'/'.join(exts)}")
    path = UPLOAD_DIR / s
    if not path.exists():
        raise HTTPException(404, f"文件不存在: {s}（可用 list_uploads 查看）")
    return path


def write_sheet_new(rows, sheet_name="Sheet1", name="导出结果", header=None, columns=None):
    """把若干行写成**新的** .xlsx。返回 {filename, rows, cols}。"""
    # ★ P-49（同上）: rows/header 也可能被 agent 传成 JSON 字符串 ⇒ 先解析成 list
    for _k in ("rows", "header", "columns"):
        _v = locals().get(_k)
        if isinstance(_v, str) and _v.strip():
            try:
                _p = json_lib.loads(_v)
                if isinstance(_p, list):
                    if _k == "rows":
                        rows = _p
                    elif _k == "header":
                        header = _p
                    else:
                        columns = _p
            except Exception:
                pass
    try:
        import openpyxl
    except ImportError as e:
        raise HTTPException(500, f"openpyxl 未安装：{e}")
    if not rows and not header:
        raise HTTPException(400, "没有可写的数据")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = str(sheet_name or "Sheet1")[:31] or "Sheet1"
    if header:
        ws.append([str(h) for h in header])
    cols = list(columns) if columns else None
    for r in rows or []:
        if isinstance(r, dict):
            # 关键：group_sum 这类结果是一组"字典"（{group, sum, count, avg}），
            # 直接 append 字典会让 openpyxl 报错 —— 必须按 columns 挑字段或退化取值。
            vals = [r.get(k) for k in cols] if cols else list(r.values())
            ws.append([("" if c is None else c) for c in vals])
        elif isinstance(r, (list, tuple)):
            ws.append([("" if c is None else c) for c in r])
        else:
            ws.append([r])
    out = docs_out_path(name, ".xlsx")
    try:
        wb.save(str(out))
    except Exception as e:
        raise HTTPException(500, f"写 Excel 失败：{type(e).__name__}: {e}")
    return {"filename": out.name, "rows": ws.max_row, "cols": ws.max_column,
            "note": "已生成新文件（原文件未改动）"}


def read_docx_text(name):
    """读 Word 文本（段落 + 表格），供 agent 先看懂再决定怎么改。"""
    path = docs_resolve_doc(name, WORD_EXTS)
    text = extract_text_from_docx(str(path))
    return {"filename": Path(path).name, "chars": len(text), "text": text[:8000],
            "truncated": len(text) > 8000}


def _looks_like_json_blob(text, head=4000):
    """判断一段文本是不是【一整坨 JSON 数据】而不是正常正文。

    为什么需要（2026-10-11 实测）：agent 会把工作流的结果 JSON 当正文导出 ✗
        export_docx(source="result_20261011_192602.json")
        → title 变成单个 "{" ✓ chars=49354 ✓ paragraphs=507 ✓
          一整坨 JSON 变成了 Word ✗ —— 而工具明明拿到了信号（标题就是 "{"）
          却一声不响地返回 exported:true ✗，于是 agent 报告"已成功导出" ✗。

    判据刻意保守：正常中文/英文正文不会以 { 或 [ 开头 ✓
    """
    s = str(text or "").lstrip()
    if not s or s[0] not in "{[":
        return False
    t = s[:head]
    # 本工作台自己产出的那几种 JSON 结构（工作流结果、文档流程、缓存节点…）
    if any(k in t for k in ('"source_file"', '"final_report"', '"_meta"', '"workflow"',
                            '"nodes"', '"log":', '"steps"', '"trace"')):
        return True
    # 或者整体本就是一个合法 JSON
    try:
        json_lib.loads(s)
        return True
    except Exception:
        return False


_JSON_BLOB_HINT = (
    "这段内容看起来是 JSON 数据（不是正文），已拒绝写入 Word。"
    "常见原因：把工作流结果文件（outputs/result_*.json）当正文传进来了 ✗。"
    "正确做法：① 报告正文取该 JSON 的 final_report 字段；"
    "② 或先用 read_docx / read_sheet 拿到正文再写；"
    "③ 真要保留原始 JSON，请另存为 .txt 并明确说明。"
)


def write_docx_new(title=None, paragraphs=None, table=None, name="导出文档"):
    """写一个新的 .docx：标题 + 段落 + 可选表格。返回 {filename, paragraphs}。"""
    try:
        import docx
    except ImportError as e:
        raise HTTPException(500, f"python-docx 未安装：{e}")
    # 护栏（唯一出口：write_docx / write_docx_new / export_docx 三条路都汇到这里）：
    # 拒绝把"一整坨 JSON"当正文写进 Word，并给出可照做的替代方案。
    _probe = paragraphs
    if isinstance(_probe, (list, tuple)):
        _probe = "\n".join(str(x or "") for x in list(_probe)[:20])
    if _looks_like_json_blob(_probe):
        raise HTTPException(400, _JSON_BLOB_HINT)
    d = docx.Document()
    if title:
        d.add_heading(str(title), level=1)
    n = 0
    # 字符串要按换行拆段：否则 for 会**逐字符**迭代，
    # 生成出"每个字一段"的 Word（实测 export_docx 产出 2410 段，前 3 段是「一、引言」「一」「、」）。
    if isinstance(paragraphs, str):
        paragraphs = [x for x in paragraphs.split("\n") if x.strip()]
        # 正文首行常常就是标题：与已加标题相同时去掉，避免重复（实测出现过）
        if title and paragraphs and paragraphs[0].strip() == str(title).strip():
            paragraphs = paragraphs[1:]
    # Markdown 标题 -> Word 标题样式：
    # 结构规整（structure_fix）输出的是 "## xxx" 这类 Markdown，若直接当普通段落写入，
    # Word 里就没有任何标题层级（实测导出后 Heading 数为 0，用户要的"格式正确"做不到）。
    for para in paragraphs or []:
        s = str(para or "").strip()
        if not s:
            continue
        # ★ 原来这里写的是 `_re_md.match(s) if '_re_md' in globals() else None`
        #   而 `_re_md` 从未定义 —— 那个 `in globals()` 的"防御"让它不报错，
        #   却把"引用了不存在的东西"藏了起来（每次都走 fallback 重新 import re ✓）
        #   pyflakes 一跑就现形（undefined name '_re_md'）→ 直接用顶部 import 的 re ✓
        mh = re.match(r"^(#{1,6})\s+(.*)$", s)
        if mh:
            lvl = min(len(mh.group(1)), 4)
            d.add_heading(mh.group(2).strip(), level=lvl)
        else:
            d.add_paragraph(s)
        n += 1
    # ★ P-49（2026-10-19 实测）: agent 按工具 schema 把 table 传成 **JSON 字符串** ✗
    #   这里原来只认 list ⇒ rows=[] ⇒ **静默丢表**却返回成功 ✗
    #   （实测：practical-2 的 Q5 追踪表 30 行整张丢失，而段落数正常、工具返回 ok ✗）
    #   字符串形态先按 JSON 解析；解析不成才当空（保持原行为，不引入新报错）。
    if isinstance(table, str) and table.strip():
        try:
            _t = json_lib.loads(table)
            if isinstance(_t, list):
                table = _t
        except Exception:
            pass
    if table:
        rows = table if isinstance(table, list) else []
        if rows:
            cols = max(len(r) for r in rows if isinstance(r, (list, tuple)))
            tb = d.add_table(rows=len(rows), cols=cols)
            for i, r in enumerate(rows):
                for j, c in enumerate(r if isinstance(r, (list, tuple)) else [r]):
                    tb.cell(i, j).text = "" if c is None else str(c)
    out = docs_out_path(name, ".docx")
    try:
        d.save(str(out))
    except Exception as e:
        raise HTTPException(500, f"写 Word 失败：{type(e).__name__}: {e}")
    return {"filename": out.name, "paragraphs": n,
            "table_rows": len(table or []) if isinstance(table, list) else 0,
            "note": "已生成新文件（原文件未改动）"}


@app.get("/docs/read_docx")
def docs_read_docx(filename: str):
    """读 Word 文本。"""
    return read_docx_text(filename)


@app.post("/docs/write_sheet")
def docs_write_sheet(payload: dict):
    """导出为新 Excel。body: {rows:[[...]], header?:[...], sheet?:str, name?:str}"""
    return write_sheet_new(payload.get("rows"), payload.get("sheet", "Sheet1"),
                           payload.get("name", "导出结果"), payload.get("header"),
                           payload.get("columns"))


@app.post("/docs/write_docx")
def docs_write_docx(payload: dict):
    """导出为新 Word。body: {title?, paragraphs?:[...], table?:[[...]], name?}"""
    return write_docx_new(payload.get("title"), payload.get("paragraphs"),
                          payload.get("table"), payload.get("name", "导出文档"))


# ============ 文档助手 P3：可复跑的文档流程（步骤脚本）============
DOC_FLOW_DIR = PROJECT_DIR / "doc_flows"
DOC_FLOW_EXTS = (".xlsx", ".xlsm")


def _flow_path(name):
    """流程文件名白名单：只允许 workflow 里那种安全名（无路径分隔符）。"""
    import re as _re
    s = str(name or "").strip()
    if not s or "/" in s or "\\" in s or ".." in s:
        raise HTTPException(400, f"非法流程名: {name}")
    if not s.endswith(".json"):
        s += ".json"
    if not _re.fullmatch(r"workflow_config[\w\-]*\.json|doc_flow[\w\-]*\.json", s):
        raise HTTPException(400, "流程名只能用字母/数字/下划线/中划线，且以 doc_flow 开头")
    return DOC_FLOW_DIR / s


def _lookup(obj, dotted):
    cur = obj
    for part in str(dotted).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def doc_flow_substitute(value, inputs, results):
    """把 args 里的 {{file}} / {{steps.<i>.<path>}} 换成真实值（递归处理 dict/list/str）。

    **未知引用一律报错** —— 静默留空字节会让流程"看起来跑成功但结果是错的"。
    """
    import re as _re
    if isinstance(value, dict):
        return {k: doc_flow_substitute(v, inputs, results) for k, v in value.items()}
    if isinstance(value, list):
        return [doc_flow_substitute(v, inputs, results) for v in value]
    if not isinstance(value, str):
        return value
    m = _re.fullmatch(r"\{\{\s*([^}]+?)\s*\}\}", value)
    if not m:
        return value
    ref = m.group(1)
    try:
        if ref in ("file", "filename"):
            return inputs.get("file") or inputs.get("filename")
        if ref.startswith("steps."):
            rest = ref[len("steps."):]
            idx, _, path = rest.partition(".")
            if not path:
                raise KeyError(ref)
            return _lookup(results[int(idx)], path)
        if ref in inputs:
            return inputs[ref]
        raise KeyError(ref)
    except Exception:
        raise HTTPException(400, f"变量引用无法解析: {{{{{ref}}}}}"
                                 f"（可用: file / inputs 的键 / steps.<序号>.<字段>）")


# 流程里允许调用的工具（**服务端直接复用 P1/P2 的纯函数**，不走 HTTP）
def _ft_list_sheets(a):
    return list_sheets_of(docs_resolve_doc(a.get("filename"), DOC_FLOW_EXTS))


def _ft_read_sheet(a):
    # 同 read_docx：uploads / outputs / 指定 out_dir 都能读
    _resolve_input_file(a.get("filename"), a.get("out_dir"), {".xlsx", ".xlsm"})
    return read_sheet_summary(docs_resolve_doc(a.get("filename"), DOC_FLOW_EXTS),

                              a.get("sheet"), a.get("rows", 10))


def _ft_filter_rows(a):
    return filter_rows_of(docs_resolve_doc(a.get("filename"), DOC_FLOW_EXTS), a.get("sheet"),
                          a.get("column"), a.get("op", "=="), a.get("value", ""),
                          a.get("limit", 50))


def _ft_group_sum(a):
    return group_sum_of(docs_resolve_doc(a.get("filename"), DOC_FLOW_EXTS), a.get("sheet"),
                        a.get("group_by"), a.get("sum_column"), a.get("limit", 30))


def _ft_write_sheet(a):
    d = write_sheet_new(a.get("rows"), a.get("sheet", "Sheet1"),
                        a.get("name", "导出结果"), a.get("header"),
                        a.get("columns"))
    if a.get("out_dir") and isinstance(d, dict):
        import shutil
        fn = d.get("filename") or d.get("name")
        cands = []
        if d.get("path"):
            cands.append(Path(str(d["path"])))
        if fn:
            cands += [Path(OUTPUT_DIR) / fn, Path(PROJECT_DIR) / fn]
        src = next((c for c in cands if c.exists()), None)
        if src is None and fn:
            hits = sorted(Path(PROJECT_DIR).rglob(fn), key=lambda x: -x.stat().st_mtime)
            src = hits[0] if hits else None
        if src is None:
            d["export_error"] = "生成成功但未找到产物文件，未导出到 out_dir"
            return d
        target = _resolve_out_dir(a.get("out_dir")) / src.name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        d["filename"] = src.name
        d["path"] = str(target)
        d["dir_win"] = _win_path(target.parent)
        d["exported"] = target.exists()
    return d


def _ft_read_docx(a):
    """读 Word 正文。

    注意（踩过的坑）：不能先解析路径再只把 .name 传下去 —— 下面的 read_docx_text
    内部只认 uploads/，等于把刚解析出来的目录又丢掉了（表现为 404）。
    这里直接对**解析后的文件**用 python-docx 读，段落与表格都取。
    """
    src = _resolve_input_file(a.get("filename"), a.get("out_dir"), {".docx"})
    try:
        from docx import Document
    except Exception as e:
        raise HTTPException(500, "python-docx 不可用: " + str(e))
    doc = Document(str(src))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    tbl_rows = 0
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(cells))
            tbl_rows += 1
    text = "\n".join(parts)
    return {"filename": src.name, "source": src.name, "chars": len(text),
            "paragraphs": len(parts) - tbl_rows, "table_rows": tbl_rows, "text": text}


def _ft_read_sheet(a):
    """读 Excel 的表头与若干行（uploads / outputs / 指定 out_dir 都能读）。"""
    src = _resolve_input_file(a.get("filename"), a.get("out_dir"), {".xlsx", ".xlsm"})
    try:
        from openpyxl import load_workbook
    except Exception as e:
        raise HTTPException(500, "openpyxl 不可用: " + str(e))
    try:
        wb = load_workbook(str(src), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(500, f"打开工作簿失败：{type(e).__name__}: {e}")
    want = str(a.get("sheet") or "").strip()
    ws = wb[want] if want and want in wb.sheetnames else wb[wb.sheetnames[0]]
    limit = int(a.get("rows") or 10)
    rows, header = [], None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        vals = ["" if v is None else v for v in row]
        if i == 0:
            header = [str(v) for v in vals]
            continue
        if i > limit:
            break
        if any(str(v).strip() for v in vals):
            rows.append(vals)
    wb.close()
    return {"filename": src.name, "sheet": ws.title, "header": header,
            "rows": rows, "row_count": len(rows), "sheets": wb.sheetnames}

def _ft_write_docx(a):
    d = write_docx_new(a.get("title"), a.get("paragraphs"), a.get("table"),
                       a.get("name", "导出文档"))
    if a.get("out_dir") and isinstance(d, dict):
        # 关键：不能假设产物就在 OUTPUT_DIR/<filename> —— 之前那样假设，导致
        # "返回里给了 path，但那个文件根本不存在"（文档任务测试 E1 抓到）。
        # 改为逐个候选找真实产物；找不到就如实写入 export_error，绝不谎报成功。
        import shutil
        fn = d.get("filename") or d.get("name")
        cands = []
        if d.get("path"):
            cands.append(Path(str(d["path"])))
        if fn:
            cands += [Path(OUTPUT_DIR) / fn, Path(PROJECT_DIR) / fn]
        src = next((c for c in cands if c.exists()), None)
        if src is None and fn:
            hits = sorted(Path(PROJECT_DIR).rglob(fn), key=lambda x: -x.stat().st_mtime)
            src = hits[0] if hits else None
        if src is None:
            d["export_error"] = "生成成功但未找到产物文件，未导出到 out_dir"
            return d
        target = _resolve_out_dir(a.get("out_dir")) / src.name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        d["filename"] = src.name
        d["path"] = str(target)
        d["dir_win"] = _win_path(target.parent)
        d["exported"] = target.exists()
        d["note"] = (str(d.get("note", "")) + f"；已导出到 {_win_path(target.parent)}").strip("；")
    return d


# ===== 宏观文档编辑工具（面向"写作/编辑流程"，不是数据库操作）=====
# 用户的诉求（原话）：流程应该是宏观调用的 ——
#   上传文件 -> 格式修正 -> 添加段落/句子 -> 逻辑排查修正 -> 生成结果（一键打开 Word）
# 所以这里提供三个宏观步骤。前两个/第三个中：
#   · format_fix 用**确定性规则**（可复现、无需模型）
#   · add_paragraphs / logic_check 需要大模型，走本地 vLLM（与前端同一路径，已验证）

def _doc_model_id():
    """取 vLLM 当前模型 id（必须是 /v1/models 里真实存在的，写死或 'default' 会 404）。"""
    import requests as _rq
    r = _rq.get("http://127.0.0.1:8000/v1/models", timeout=10).json()
    data = r.get("data") or []
    if not data:
        raise HTTPException(503, "本地推理底座未就绪（/v1/models 为空），请先在底座管理里启动")
    return data[0].get("id")


# ===== 工具内部用哪个模型（跟随 agent 当前选择）=====
# 起因：内置 agent 切到云模型后，工具链本身在云上跑，但每个 *_file 工具**内部**的
# 一次模型调用（_doc_llm）写死指向本地 vLLM -> GPU 打满、且慢得离谱
# （用户看到"GPU 占用满、没有全走 api"）。
# 用 ContextVar 传递当前请求选定的模型，不污染全局，也不影响"文档流程"那种本地用法。
_DOC_LLM_MODEL = contextvars.ContextVar("doc_llm_model", default=None)
_DOC_LLM_STATS = {"cloud": 0, "local": 0}


def set_doc_llm_model(model_id):
    """设置本次请求里工具内部要用的模型（None = 本地 vLLM）。"""
    _DOC_LLM_MODEL.set(model_id or None)


def get_doc_llm_model():
    return _DOC_LLM_MODEL.get()


def _call_cloud_llm(model_id, messages, max_tokens, temperature):
    """按 model_id（如 openai:deepseek-chat）走云 provider。返回纯文本。"""
    import requests as _rq
    pname = str(model_id).split(":", 1)[0]
    cfg = (load_settings().get("providers") or {}).get(pname) or {}
    key = cfg.get("api_key")
    if not key:
        raise HTTPException(400, f"{pname} 未配置 api_key（去「系统设置」填）")
    base = str(cfg.get("api_base") or "").rstrip("/")
    model = str(model_id).split(":", 1)[1] if ":" in str(model_id) else str(cfg.get("model") or "")
    r = _rq.post(base + "/chat/completions",
                 json={"model": model, "messages": messages,
                       "max_tokens": int(max_tokens), "temperature": _clamp_float(float(temperature), 0.0, 2.0, 0.7)},
                 headers={"Authorization": "Bearer " + str(key),
                          "Content-Type": "application/json"}, timeout=300)
    if r.status_code != 200:
        raise HTTPException(502, f"{pname} 返回 HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    return ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()


def _doc_llm(system, user, max_tokens=2048, temperature=0.3):
    """调用模型返回纯文本。

    默认走本地 vLLM；若本次请求指定了模型（agent 选了云模型），就走那个 provider ——
    否则"agent 在云上、工具内部却在本地算"，会把 GPU 打满（实测踩过）。
    """
    import requests as _rq
    _mid = get_doc_llm_model()
    if _mid and not str(_mid).startswith("vllm"):
        _DOC_LLM_STATS["cloud"] += 1
        return _call_cloud_llm(_mid,
                               [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                               max_tokens, temperature)
    _DOC_LLM_STATS["local"] += 1
    body = {
        "model": _doc_model_id(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature, "max_tokens": max_tokens, "stream": False,
    }
    r = _rq.post("http://127.0.0.1:8000/v1/chat/completions", json=body, timeout=600)
    if r.status_code != 200:
        raise HTTPException(502, f"调用本地模型失败：HTTP {r.status_code} {r.text[:200]}")
    d = r.json()
    return ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "").strip()


def _ft_format_fix(a):
    """格式修正（确定性）：去多余空白、统一标点、合并被硬换行切断的句子、压掉空行。"""
    text = str(a.get("text") or "")
    if not text:
        return {"text": "", "changed": 0, "note": "输入为空"}
    before = text
    import re as _re
    # 1) 统一换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 2) 全角空格/制表符 -> 普通空格；行尾空白去掉
    text = text.replace("\u3000", " ").replace("\t", " ")
    text = "\n".join(ln.rstrip() for ln in text.split("\n"))
    # 2b) 行内连续空格压成一个（测试发现：只处理行尾时"这是一段   有很多空格"仍原样保留）
    text = _re.sub(r"[ ]{2,}", " ", text)
    # 2c) 中文之间的空格去掉（多为 PDF 复制产生）
    text = _re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", text)
    # 2d) 中文标点前的空格去掉
    text = _re.sub(r" +(?=[，。；：、？！）】》])", "", text)
    # 3) 合并"被 PDF 硬换行切开"的句子：下一行以小写字母或中文字符开头时接回上一行
    lines = text.split("\n")
    merged = []
    for ln in lines:
        if merged and ln and not _re.match(r"^[#\-*\d（(【\[]", ln) and \
           _re.match(r"^[a-z\u4e00-\u9fff]", ln):
            joiner = "" if _re.search(r"[\u4e00-\u9fff]$", merged[-1]) else " "
            merged[-1] = merged[-1] + joiner + ln.strip()
        else:
            merged.append(ln)
    text = "\n".join(merged)
    # 4) 半角标点 -> 全角（中文语境）；英文句子不动
    def _punct(m):
        return {".": "。", ",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}.get(m.group(0), m.group(0))
    text = _re.sub(r"(?<=[\u4e00-\u9fff])[.,;:?!]", _punct, text)
    # 5) 压掉连续空行（最多保留一个）
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"text": text, "changed": len(text) - len(before),
            "chars": len(text), "note": "已做空白/换行/标点规整"}


def _ft_add_content(a):
    """添加段落/句子：按主题补写，接在原文之后。"""
    text = str(a.get("text") or "")
    topic = str(a.get("topic") or a.get("instruction") or "").strip()
    n = int(a.get("paragraphs") or 1)
    if not topic:
        raise HTTPException(400, "需要 topic（要补写什么主题）")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文写作助手。只输出要新增的段落正文，不要解释、不要加标题、"
              "不要重复已有内容。保持与原文一致的语气与术语。")
    user = (f"已有正文（供你理解上下文，不要复述）：\n{text[:6000]}\n\n"
            f"请围绕「{topic}」补写 {n} 段，每段 3-6 句，直接给出正文。")
    add = _doc_llm(system, user, max_tokens=1500, temperature=0.5)
    return {"text": (text.rstrip() + "\n\n" + add).strip(), "added": add,
            "added_chars": len(add), "topic": topic}


def _ft_logic_check(a):
    """逻辑排查修正：找出前后矛盾/不一致，并给出修正后的正文。"""
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空")
    system = ("你是严谨的中文编辑。任务：①找出正文中前后矛盾、指代不清、因果不成立之处；"
              "②直接给出修正后的完整正文。输出必须是两段，格式严格如下：\n"
              "【问题】每行一条，没有就写「未发现」\n【修正后正文】\n<完整正文>")
    user = f"正文如下：\n\n{text[:12000]}"
    out = _doc_llm(system, user, max_tokens=3000, temperature=0.2)
    issues, fixed = "", out
    if "【修正后正文】" in out:
        parts = out.split("【修正后正文】", 1)
        issues = parts[0].replace("【问题】", "").strip()
        fixed = parts[1].strip()
    return {"text": fixed, "issues": issues,
            "issue_count": 0 if issues.startswith("未发现") else len([x for x in issues.splitlines() if x.strip()]),
            "chars": len(fixed)}




def _ft_structure_fix(a):
    """分段与标题：按语义切段、规整标题层级，只调结构不改内容。长文分块。"""
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文文档结构编辑。任务：把正文按语义重新分段，并识别、规整标题层级"
              "（全文统一用同一种形式）。只调整结构，不改写句子内容。直接输出整理后的正文。")
    out, n = _doc_llm_long(system, text, chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 3000),
                           temperature=float(a.get("temperature") or 0.2))
    return {"text": out, "chars": len(out), "chunks": n}


def _ft_proofread(a):
    """校对纠错：错别字、标点误用、明显语病；只改错，不改风格。长文分块。"""
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文校对。任务：只修正确凿的错误（错别字、标点误用、明显的搭配或语法错误），"
              "不要改变作者风格、不要润色、不要增删内容。"
              "直接输出校对后的完整正文，不要解释。")
    out, n = _doc_llm_long(system, text, chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 3000),
                           temperature=float(a.get("temperature") or 0.1))
    return {"text": out, "chars": len(out), "chunks": n}


def _ft_polish(a):
    """润色改写：通顺、书面化，保持原意与信息量。

    提示词里明确"不得改变语体层级"—— 测试发现只写"更书面化"时，模型会把口语
    改写成文言腔（"此事虽有诸多不便，然终究不可忽视"），越界了。
    """
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文编辑。任务：在不改变原意、不增删事实、**不改变语体层级**的前提下，"
              "让语句更通顺、书面化程度与原文相当（原文是口语就仍是口语，不要改成文言或公文腔）。"
              "直接输出润色后的完整正文，不要解释。")
    out, n = _doc_llm_long(system, text, chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 3000),
                           temperature=float(a.get("temperature") or 0.3))
    return {"text": out, "chars": len(out), "chunks": n}


def _ft_condense(a):
    """精简摘要：压缩冗余，并在末尾给出摘要要点。长文分块后用 reduce 合并。"""
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文编辑。任务：①压缩冗余、删掉重复与空话，但保留全部关键信息与数字；"
              "②最后另起一行给「摘要：」并列出 3-5 条要点。")
    out, n = _doc_llm_long(system, text, merge="reduce",
                           chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 2500),
                           temperature=float(a.get("temperature") or 0.2))
    body, _, digest = out.partition("摘要：")
    return {"text": out, "body": body.strip(), "digest": digest.strip(),
            "chars": len(out), "chunks": n}


def _ft_terminology(a):
    """术语统一：统一同一概念的不同叫法与人称，并列出改了什么。长文分块后 reduce。"""
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    system = ("你是中文编辑。任务：找出同一概念的不同叫法并统一（优先用文中出现最多的写法"
              "或更规范的写法），并统一称谓与人称。"
              "输出格式：第一行「【统一】」，其后每行写一条改动；然后「【正文】」，其后是完整正文。")
    out, n = _doc_llm_long(system, text, merge="reduce",
                           chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 3000),
                           temperature=float(a.get("temperature") or 0.2))
    changes, body = "", out
    if "【正文】" in out:
        head, _, body = out.partition("【正文】")
        changes = head.replace("【统一】", "").strip()
    return {"text": body.strip(), "changes": changes,
            "change_count": len([x for x in changes.splitlines() if x.strip()]),
            "chars": len(body.strip()), "chunks": n}


def _ft_translate(a):
    """整篇翻译（默认译成英文），保留段落结构与标题层级。

    用户要求"分块翻译"：长文档一次性丢给模型会又慢又易重复（实测 3 万字 89 秒、
    输出重复），因此按段落边界分块，并把上一块结尾作为衔接上下文。
    """
    text = str(a.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "输入正文为空（上一步没有内容）")
    target = str(a.get("target") or "英文")
    system = ("你是专业翻译。任务：把正文整体翻译成目标语言，保留原有段落结构、标题层级、"
              "列表与表格文字，术语前后一致。只输出译文，不要解释。")
    suffix = "目标语言：" + target + "\n\n"
    out, n = _doc_llm_long(system, text, per_chunk_suffix=suffix,
                           chunk_chars=int(a.get("chunk_chars") or 2500),
                           max_tokens=int(a.get("max_tokens") or 3000),
                           temperature=float(a.get("temperature") or 0.2))
    return {"text": out, "target": target, "chars": len(out), "chunks": n}






# ===== 长文本：按段落边界分块，再合并（用户要求"分块翻译"；其余长文工具同样受益）=====
# 为什么必须做：实测 3 万字一次性翻译耗时 89 秒且输出重复（模型被超长输入拖垮）。
# 分块还能给每块带上"上一块的结尾"，避免块与块之间语气/术语断裂。

# 分块时我们会在 prompt 里加【第 i/N 段…】【上一段结尾…】这类标记；
# 个别模型会把它们**回显**到输出里，一旦写回文件就变成正文里的垃圾，
# 后续工具读到后会陷入"反复读同一段"的死循环（实测 690 秒无产出）。
_CHUNK_MARKER_RE = None


def _strip_chunk_markers(text):
    """去掉模型回显的分块标记行，只保留真正的正文。"""
    import re as _re
    if not text:
        return text
    out_lines = []
    for ln in str(text).split("\n"):
        s = ln.strip()
        if s.startswith("【第 ") and "段，请只处理这一段】" in s:
            continue
        if s.startswith("【上一段结尾"):
            continue
        if s.startswith("【第 ") and s.endswith("段】"):
            continue
        out_lines.append(ln)
    return "\n".join(out_lines).strip()


def _split_chunks(text, chunk_chars=2500):
    """按段落边界切块，尽量不切断句子；返回块列表。"""
    if chunk_chars <= 0:
        chunk_chars = 2500
    paras = [p for p in str(text).split("\n\n")]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 <= chunk_chars:
            buf = (buf + "\n\n" + p) if buf else p
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        # 单个段落就超长：按句号/换行硬切
        while len(p) > chunk_chars:
            cut = max(p.rfind("。", 0, chunk_chars), p.rfind("\n", 0, chunk_chars),
                      p.rfind(". ", 0, chunk_chars))
            if cut <= 0:
                cut = chunk_chars
            chunks.append(p[:cut + 1])
            p = p[cut + 1:]
        buf = p
    if buf:
        chunks.append(buf)
    return chunks or [str(text)]


def _doc_llm_long(system, text, per_chunk_suffix="", merge="concat",
                  chunk_chars=2500, max_tokens=3000, temperature=0.2,
                  label="处理"):
    """分块调用模型并合并。merge='concat' 直接拼接（翻译/润色/校对）；
    merge='reduce' 先逐块摘要，再用一次调用合并（精简/术语统一这类要全局视角的）。"""
    chunks = _split_chunks(text, chunk_chars)
    if len(chunks) == 1:
        out = _doc_llm(system, (per_chunk_suffix or "") + text, max_tokens=max_tokens,
                       temperature=temperature)
        return _strip_chunk_markers(out), 1
    parts, prev_tail = [], ""
    for i, ch in enumerate(chunks, 1):
        ctx = ""
        if prev_tail:
            ctx = ("【上一段结尾（仅供衔接，不要重复翻译/改写它）】\n" + prev_tail[-400:] + "\n\n")
        user = ctx + (per_chunk_suffix or "") + f"【第 {i}/{len(chunks)} 段，请只处理这一段】\n\n" + ch
        out = _doc_llm(system, user, max_tokens=max_tokens, temperature=temperature)
        parts.append(out.strip())
        prev_tail = out.strip()
    if merge == "reduce":
        joined = "\n\n".join(parts)
        final = _doc_llm("你是中文编辑。把下面已分块处理过的内容统一整理成一份连贯的成品，"
                         "去掉块与块之间的重复，保持结构。只输出成品，不要解释。",
                         joined[:12000], max_tokens=max_tokens, temperature=temperature)
        return _strip_chunk_markers(final), len(chunks)
    return _strip_chunk_markers("\n\n".join(parts)), len(chunks)



# ===== 从 0 创建 / 编辑文档（Word / Excel 都适配）=====
# 用户诉求：既能在已有文档上编辑，也要能"从 0 创建"，并且能指定输出位置（例如 E 盘）。
# 安全：out_dir 只允许"项目目录内"或"/mnt/e 下"（对应 Windows 的 E 盘），并拒绝 .. 穿越。

def _resolve_input_file(name, out_dir=None, exts=None):
    """统一的"找输入文件"逻辑：uploads -> outputs -> 指定目录 -> 项目内兜底。

    为什么需要：create_docx/create_sheet 能写到 E 盘，但 read_docx/read_sheet 只在
    uploads 里找，于是"写得出、读不回"（文档任务测试 F1/I2/J1 连续三轮撞到）。
    这里让读写对称：只要文件在项目目录、outputs 或用户指定的 out_dir 下，都找得到。
    """
    s = str(name or "").strip()
    if not s or ".." in s or "/" in s and s.startswith("/") is False and "\\" in s:
        pass
    base = Path(s).name
    cands = []
    if out_dir:
        try:
            cands.append(_resolve_out_dir(out_dir) / base)
        except HTTPException:
            pass
    cands += [UPLOAD_DIR / base, OUTPUT_DIR / base, Path(PROJECT_DIR) / base]
    for c in cands:
        if c.exists() and c.is_file():
            return c
    hits = sorted(Path(PROJECT_DIR).rglob(base), key=lambda x: -x.stat().st_mtime)
    hits = [h for h in hits if h.is_file() and (not exts or h.suffix.lower() in exts)]
    if hits:
        return hits[0]
    raise HTTPException(404, f"文件不存在: {s}（找过 {', '.join(str(c) for c in cands)}）")


def _resolve_out_dir(out_dir, default=None):
    """把用户给的输出目录解析成安全的绝对路径。拒绝 .. 与系统目录。"""
    base = Path(default) if default else OUTPUT_DIR
    raw = str(out_dir or "").strip()
    if not raw:
        return base
    if ".." in raw:
        raise HTTPException(400, "输出目录不能包含 ..")
    # 允许 Windows 风格 E:\xxx —— 转成 WSL 的 /mnt/e/xxx
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        rest = raw[2:].lstrip("\\/").replace("\\", "/")
        raw = f"/mnt/{drive}/" + rest
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except Exception:
        raise HTTPException(400, f"输出目录无法解析: {out_dir}")
    allowed = [Path(PROJECT_DIR).resolve()]
    try:
        # ★ 修（2026-10-22 硬编码审计）：访问白名单里的 /mnt/e 改为可配置
        #   环境变量 FILE_ACCESS_ALLOW（空格分隔）可追加/覆盖；默认仍放行 /mnt/e（WSL 常见挂载点）
        _allow_env = str(__import__("os").environ.get("FILE_ACCESS_ALLOW") or "").strip()
        _allow_extra = [x for x in _allow_env.split() if x] or ["/mnt/e"]
        for _ap in _allow_extra:
            try:
                allowed.append(Path(_ap).resolve())
            except Exception:
                pass
    except Exception:
        pass
    if not any(p == a or a in p.parents for a in allowed):
        raise HTTPException(400, f"出于安全考虑，只允许写到项目目录或 E 盘（收到: {out_dir}）")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ft_create_docx(a):
    """从 0 创建 Word：给标题 + 正文（或按小节大纲自动展开）。"""
    title = str(a.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "需要 title（文档标题）")
    body = str(a.get("paragraphs") or a.get("body") or "").strip()
    sections = a.get("sections")            # 可选：["一、背景", "二、进展", ...]
    if not body and not sections:
        raise HTTPException(400, "需要 paragraphs（正文）或 sections（小节标题列表）")
    try:
        from docx import Document
        from docx.shared import Pt
    except Exception as e:
        raise HTTPException(500, f"python-docx 不可用: {e}")

    doc = Document()
    h = doc.add_heading(title, level=0)
    if body:
        for para in [x for x in body.split("\n") if x.strip()]:
            doc.add_paragraph(para.strip())
    if isinstance(sections, list):
        for sec in sections:
            doc.add_heading(str(sec), level=1)
            doc.add_paragraph("（待补充）")
    out_dir = _resolve_out_dir(a.get("out_dir"))
    name = str(a.get("name") or title or "新建文档").strip()
    if not name.lower().endswith(".docx"):
        name += ".docx"
    path = out_dir / name
    doc.save(str(path))
    return {"filename": name, "path": str(path),
            "dir_win": _win_path(path.parent), "chars": len(body),
            "sections": len(sections) if isinstance(sections, list) else 0,
            "note": "已创建新文件（不覆盖任何已有文件）"}


def _ft_create_sheet(a):
    """从 0 创建 Excel：给表头与数据行（也可只给表头当模板）。"""
    name = str(a.get("name") or "新建表格").strip()
    sheet = str(a.get("sheet") or "Sheet1")
    headers = a.get("headers") or a.get("columns")
    rows = a.get("rows") or []
    if not headers and not rows:
        raise HTTPException(400, "需要 headers（表头）或 rows（数据行）")
    try:
        from openpyxl import Workbook
    except Exception as e:
        raise HTTPException(500, f"openpyxl 不可用: {e}")
    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    if headers:
        ws.append([str(x) for x in headers])
    n = 0
    for r in rows if isinstance(rows, list) else []:
        ws.append(r if isinstance(r, list) else [r])
        n += 1
    out_dir = _resolve_out_dir(a.get("out_dir"))
    if not name.lower().endswith((".xlsx", ".xlsm")):
        name += ".xlsx"
    path = out_dir / name
    wb.save(str(path))
    # 同时放一份到 uploads/：create 能写到 E 盘，但 read_sheet / list_sheets 只在
    # uploads·outputs·项目内找（不会去扫 E 盘）—— 实测 agent 生成后立刻读回会报"文件不存在"，
    # 它自己都诊断出了这点。这里补一份副本，保证"写到哪都能读回"。
    try:
        import shutil as _sh
        _sh.copy2(path, UPLOAD_DIR / name)
    except Exception:
        pass
    return {"filename": name, "path": str(path), "dir_win": _win_path(path.parent),
            "also_in_uploads": True,
            "sheet": sheet, "rows": n,
            "cols": len(headers) if headers else 0,
            "note": "已创建新文件（不覆盖任何已有文件）"}


def _ft_add_toc(a):
    """给已有的 Word 增加一页目录（插到正文最前面）。

    说明：python-docx 不能直接算出"域"的结果，所以插入两样东西：
      ① 静态条目列表（不更新域也能看到目录内容）
      ② 目录域（Word 里 F9 或右键"更新域"即显示页码）
    并把整页搬到文档最前面。
    """
    src = str(a.get("filename") or "").strip()
    if not src:
        raise HTTPException(400, "需要 filename（要加目录的 Word）")
    cands = []
    if a.get("path"):
        cands.append(Path(str(a["path"])))
    if a.get("out_dir"):
        try:
            cands.append(_resolve_out_dir(a.get("out_dir")) / src)
        except HTTPException:
            pass
    cands += [UPLOAD_DIR / src, OUTPUT_DIR / src]
    p = next((c for c in cands if c.exists() and c.is_file()), None)
    if p is None:
        raise HTTPException(404, "找不到 " + src + "（找过: " + ", ".join(str(c) for c in cands) + "）")
    if p.suffix.lower() != ".docx":
        raise HTTPException(400, "只支持给 .docx 加目录（PDF 是只读格式）")
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.enum.text import WD_BREAK
    except Exception as e:
        raise HTTPException(500, "python-docx 不可用: " + str(e))

    doc = Document(str(p))
    # 幂等：文件开头已经有「目录」就不再叠加一层（此前的行为是再加一页，
    # 反复调用会得到 xxx_带目录_带目录.docx，测试观察项 B2 就是这么发现的）
    existing = [x.text.strip() for x in doc.paragraphs if x.text.strip()][:1]
    already = bool(existing and existing[0] == "目录")
    heads = [x.text.strip() for x in doc.paragraphs
             if x.style and x.style.name and x.style.name.lower().startswith("heading") and x.text.strip()]

    if already:
        return {"filename": p.name, "path": str(p), "dir_win": _win_path(p.parent),
                "headings": len(heads), "entries": heads[:10],
                "placed_first": True, "already_had_toc": True, "skipped": True,
                "note": "文件开头已有「目录」，未重复添加（幂等）"}

    # 逐个创建并【记住对象】—— 不按"最后 N 段"切片（上一版就是那样抓错了原文档的小节标题）
    toc_title = doc.add_paragraph("目录")
    try:
        toc_title.style = doc.styles["Heading 1"]
    except Exception:
        pass
    items = [doc.add_paragraph(h) for h in heads]
    dom = doc.add_paragraph()
    r = dom.add_run()
    # 注意：目录域指令里有 \o \h \z \u，必须用**原始字符串**，否则 \u 会被当 unicode 转义
    fld = r._r.makeelement(qn("w:fldSimple"), {qn("w:instr"): r'TOC \o "1-3" \h \z \u'})
    r._r.append(fld)
    brk = doc.add_paragraph()
    brk.add_run().add_break(WD_BREAK.PAGE)

    # 按顺序搬到最前面：倒序 insert(0) 才能保持原顺序
    created = [toc_title] + items + [dom, brk]
    body = doc.element.body
    for para in reversed(created):
        el = para._p
        body.remove(el)
        body.insert(0, el)

    out_dir = _resolve_out_dir(a.get("out_dir"))
    name = str(a.get("name") or (p.stem + "_带目录")).strip()
    if not name.lower().endswith(".docx"):
        name += ".docx"
    out = out_dir / name
    doc.save(str(out))

    check = [x.text.strip() for x in Document(str(out)).paragraphs if x.text.strip()][:3]
    return {"filename": name, "path": str(out), "dir_win": _win_path(out.parent),
            "headings": len(heads), "entries": heads[:10], "first_paragraphs": check,
            "placed_first": bool(check and check[0] == "目录"),
            "note": "已插入目录页（静态条目 + 目录域，Word 里按 F9 可更新页码）"}


def _win_path(p):
    """把 WSL 路径转成 Windows 路径，便于"一键打开"与用户辨认。"""
    try:
        import subprocess as _sp
        return _sp.check_output(["wslpath", "-w", str(p)], text=True).strip()
    except Exception:
        return str(p)




# ===== 定点编辑（agent 实测缺这一环）=====
# 起因：用自然语言让内置 agent"把作者改成 luxy、单位改成个人"时它做不到 ——
# 19 个文档工具全是**改写类**（润色/校对/术语/逻辑），没有一个能**精确替换指定文字**。
# 而这类"改名/改单位/统一引用格式"的要求，恰恰必须用确定性替换，不能靠模型改写。
# 两个工具的返回值都带 count / replaced 字段，调用方可以据此判断是否真的改到了。

def _ft_replace_text(a):
    """精确文本替换：把 find 换成 replace（可选正则）。

    args:
      text    待处理正文
      find    要查找的内容（字符串；regex=true 时按正则解释）
      replace 替换成什么（正则模式下支持 \\1 反向引用）
      count   最多替换几处，0/缺省 = 全部
      regex   是否按正则匹配（默认 false，按字面量）
    """
    import re as _re
    text = str(a.get("text") or "")
    find = a.get("find")
    if find is None or find == "":
        raise HTTPException(400, "需要 find（要查找的内容）")
    repl = str(a.get("replace") if a.get("replace") is not None else "")
    cnt = int(a.get("count") or 0)
    if not text:
        raise HTTPException(400, "输入正文为空")
    if a.get("replace") is None:
        # 缺 replace 会被当成"删除"，很容易误删整行（实测把「作者：XXX」整行删掉）。
        # 想删除请显式传 replace=""，这里给出明确提示而不是默默删。
        return {"text": text, "found": text.count(str(find)), "replaced": 0, "changed": False,
                "need_replace": True,
                "note": "缺少 replace 参数：未做任何修改。若要删除请显式传 replace=""；"
                        "若要替换请给 replace 的内容。"}
    if a.get("regex"):
        try:
            pat = _re.compile(str(find))
        except Exception as e:
            raise HTTPException(400, "正则不合法：" + str(e))
        hits = len(pat.findall(text))
        out = pat.sub(repl, text, count=cnt if cnt > 0 else 0)
    else:
        hits = text.count(str(find))
        out = text.replace(str(find), repl, cnt if cnt > 0 else -1) if cnt > 0 \
            else text.replace(str(find), repl)
    done = hits if cnt <= 0 else min(hits, cnt)
    return {"text": out, "found": hits, "replaced": done,
            "changed": out != text,
            "note": (f"找到 {hits} 处，替换 {done} 处" if hits else "没找到要替换的内容（find 未命中）")}


def _ft_set_lines(a):
    """定点改行：把第 from 行到第 to 行（1 起、含两端）整段换掉。

    args:
      text      待处理正文
      from      起始行号（1 起）
      to        结束行号（含；缺省=与 from 相同，即只替换一行）
      replacement  新内容（可含换行，会展开成多行）；缺省=删除这些行
    """
    text = str(a.get("text") or "")
    if not text:
        raise HTTPException(400, "输入正文为空")
    lines = text.split("\n")
    try:
        i = int(a.get("from"))
    except Exception:
        raise HTTPException(400, "需要 from（起始行号，1 起）")
    j = int(a.get("to") or i)
    if i < 1 or j < i:
        raise HTTPException(400, f"行号不合法：from={i}, to={j}")
    if i > len(lines):
        raise HTTPException(400, f"起始行 {i} 超出总行数 {len(lines)}")
    j = min(j, len(lines))
    before = lines[i - 1:j]
    repl = a.get("replacement")
    new = [] if repl is None else str(repl).split("\n")
    lines[i - 1:j] = new
    return {"text": "\n".join(lines), "replaced_lines": len(before),
            "new_lines": len(new), "total_lines": len(lines),
            "old_excerpt": "\n".join(before)[:200],
            "note": f"第 {i}~{j} 行已替换（原 {len(before)} 行 -> 新 {len(new)} 行）"}





# ===== 面向文件的编辑工具（给 agent 用，而不是给流程用）=====
# 为什么另做一套：原有文档工具的 text 参数是**正文内容**，设计上由流程的
# {{steps.N.text}} 自动填充；但内置 agent 直接调用时，它天然会传**文件名**
# （它前面刚用过 read_upload(filename)）。实测后果：agent 调
# replace_text(text="pdf1.txt", find="知乎") —— 在一个 7 字符的字符串里找"知乎"，
# found 恒为 0，连试 5 次后放弃。
# 所以给 agent 的契约应当是"给文件名 -> 我读文件 -> 改 -> 存成新文件"。

def _doc_read_text_file(name):
    """读取 uploads/outputs 下的文本文件正文（供面向文件的工具内部使用）。"""
    src = _resolve_input_file(name, None, {".txt", ".md", ".text"})
    return src, src.read_text(encoding="utf-8", errors="ignore")


def _doc_write_text_file(base_name, text, out_name):
    """把编辑结果写回文本文件。

    默认**原地覆盖**（同名），并把原始内容先备份成 <原名>.orig.txt；
    这样链式调用不会滚成 xxx_edited_edited_edited.txt（实测踩过），
    语义也更自然："在这个文件上继续改"。想另存请显式给 out_name。
    """
    import shutil
    raw = str(out_name or "").strip()
    src = UPLOAD_DIR / Path(str(base_name)).name
    if not raw:
        nm = src.name
        bak = UPLOAD_DIR / (src.stem + ".orig" + src.suffix)
        if src.exists() and not bak.exists():
            shutil.copy2(src, bak)
    else:
        nm = raw if raw.lower().endswith((".txt", ".md")) else raw + ".txt"
    dst = UPLOAD_DIR / Path(nm).name
    # 写入前兜底：任何分块标记都不允许落进文件（否则后续工具会陷入读垃圾的死循环）
    text = _strip_chunk_markers(text)
    dst.write_text(text, encoding="utf-8")
    bakname = src.stem + ".orig" + src.suffix
    return {"out_filename": dst.name, "in_place": (not raw),
            "backup": bakname if (UPLOAD_DIR / bakname).exists() else None,
            "out_path": str(dst), "out_dir_win": _win_path(dst.parent)}

def _ft_replace_in_file(a):
    """在【某个文件】里做精确替换，并把结果存成新文本文件。

    args:
      filename  要处理的文件名（如 test1.txt；读 uploads/outputs）
      find      要查找的内容
      replace   替换成什么（缺省=删除）
      count     最多替换几处（缺省=全部）
      regex     是否按正则解释 find（默认 false）
      out_name  结果文件名（缺省 <原名>_edited.txt）
    返回 found / replaced / out_filename / preview（前 200 字），调用方可据此确认结果。
    """
    import re as _re
    fn = str(a.get("filename") or "").strip()
    if not fn:
        raise HTTPException(400, "需要 filename（要处理的文件名）")
    src, text = _doc_read_text_file(fn)
    find = a.get("find")
    if find is None or find == "":
        raise HTTPException(400, "需要 find（要查找的内容）")
    if a.get("replace") is None:
        return {"text": text, "found": text.count(str(find)), "replaced": 0, "changed": False,
                "need_replace": True, "out_filename": src.name, "in_place": False,
                "note": "缺少 replace 参数：未做任何修改（避免误删整行）。删除请显式传 replace=""。"}
    repl = str(a.get("replace") if a.get("replace") is not None else "")
    cnt = int(a.get("count") or 0)
    if a.get("regex"):
        try:
            pat = _re.compile(str(find))
        except Exception as e:
            raise HTTPException(400, "正则不合法：" + str(e))
        hits = len(pat.findall(text))
        out = pat.sub(repl, text, count=cnt if cnt > 0 else 0)
    else:
        hits = text.count(str(find))
        out = text.replace(str(find), repl, cnt if cnt > 0 else -1)
    info = _doc_write_text_file(src.name, out, a.get("out_name"))
    info.update({"found": hits, "replaced": (hits if cnt <= 0 else min(hits, cnt)),
                 "changed": out != text, "chars": len(out),
                 "preview": out[:200],
                 "note": (f"在 {src.name} 里找到 {hits} 处并替换；结果存为 {info['out_filename']}"
                          if hits else f"在 {src.name} 里没找到「{str(find)[:40]}」——未做任何修改")})
    return info


def _ft_set_lines_in_file(a):
    """定点改【某个文件】的第 from~to 行，并存成新文本文件。

    args: filename, from, to?, replacement?, out_name?
      replacement 缺省 = 删除这些行；可含换行（会展开成多行）
    """
    fn = str(a.get("filename") or "").strip()
    if not fn:
        raise HTTPException(400, "需要 filename（要处理的文件名）")
    src, text = _doc_read_text_file(fn)
    lines = text.split("\n")
    try:
        i = int(a.get("from"))
    except Exception:
        raise HTTPException(400, "需要 from（起始行号，1 起）")
    j = min(int(a.get("to") or i), len(lines))
    if i < 1 or j < i:
        raise HTTPException(400, f"行号不合法：from={i}, to={j}（文件共 {len(lines)} 行）")
    old = lines[i - 1:j]
    repl = a.get("replacement")
    lines[i - 1:j] = [] if repl is None else str(repl).split("\n")
    out = "\n".join(lines)
    info = _doc_write_text_file(src.name, out, a.get("out_name"))
    info.update({"replaced_lines": len(old), "total_lines": len(lines),
                 "old_excerpt": "\n".join(old)[:180], "chars": len(out),
                 "preview": out[:200],
                 "note": f"{src.name} 第 {i}~{j} 行已替换（原 {len(old)} 行）；结果存为 {info['out_filename']}"})
    return info


def _ft_export_docx(a):
    """把【某个文本文件】导出成 Word（agent 编辑完的最后一步）。

    args:
      source   文本文件名（如 test1_edited.txt；读 uploads/outputs）
      out_dir  输出目录（缺省项目 outputs；可填 E:\\目录 —— 与 write_docx 同一套安全校验）
      name     输出文件名（缺省 <原名>.docx）
      title    Word 里的文档标题（缺省用正文第一行）
    """
    src_name = str(a.get("source") or a.get("filename") or "").strip()
    if not src_name:
        raise HTTPException(400, "需要 source（要导出的文本文件名）")
    src, text = _doc_read_text_file(src_name)
    if not text.strip():
        raise HTTPException(400, f"{src.name} 是空的，没有内容可导出")
    # 护栏（2026-10-11 实测）：把 result_*.json 当正文导出时，Word 里会是整坨 JSON，
    # 而且标题会变成 JSON 的第一个字符 "{ " ✗ —— 这里直接给明确指引，不要产出垃圾文件。
    if src.suffix.lower() in (".json", ".jsonl"):
        raise HTTPException(400, (
            f"{src.name} 是 JSON 数据文件，不是正文，不能直接导出成 Word ✗。"
            "请改用：① 报告正文取该 JSON 的 final_report 字段（或相应正文键）后用 write_docx；"
            "② 或先 read_docx / read_sheet 取到正文再导出；"
            "③ 若确实要保留原始 JSON，请先另存为 .txt。"
        ))
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    title = str(a.get("title") or (lines[0] if lines else src.stem)).strip()
    nm = str(a.get("name") or "").strip() or (src.stem + ".docx")
    if not nm.lower().endswith(".docx"):
        nm += ".docx"
    r = write_docx_new(title, text, a.get("table"), nm.rsplit(".docx", 1)[0])
    out = {"source": src.name, "title": title, "chars": len(text)}
    out.update({k: r.get(k) for k in ("filename", "paragraphs", "table_rows")})
    # 需要时复制到用户指定目录（与 write_docx 的 out_dir 语义一致）
    if a.get("out_dir"):
        import shutil
        target = _resolve_out_dir(a.get("out_dir")) / (r.get("filename") or nm)
        cands = [Path(OUTPUT_DIR) / (r.get("filename") or nm), Path(PROJECT_DIR) / (r.get("filename") or nm)]
        found = next((c for c in cands if c.exists()), None)
        if found is None:
            hits = sorted(Path(PROJECT_DIR).rglob(r.get("filename") or nm), key=lambda x: -x.stat().st_mtime)
            found = hits[0] if hits else None
        if found is None:
            out["export_error"] = "生成成功但未找到产物，未导出到 out_dir"
            return out
        if found.resolve() != target.resolve():
            shutil.copy2(found, target)
        out["path"] = str(target)
        out["dir_win"] = _win_path(target.parent)
        out["exported"] = target.exists()
        out["note"] = f"已导出到 {_win_path(target.parent)}"
        # 同时留一份在 uploads/，便于后续步骤按文件名读回（同上）
        try:
            import shutil as _sh
            _sh.copy2(target, UPLOAD_DIR / target.name)
            out["also_in_uploads"] = True
        except Exception:
            pass
    else:
        # 没给 out_dir 时本来就在 outputs/，也补一份到 uploads/ 方便统一读回
        try:
            import shutil as _sh
            src = Path(OUTPUT_DIR) / (r.get("filename") or "")
            if src.exists():
                _sh.copy2(src, UPLOAD_DIR / src.name)
                out["also_in_uploads"] = True
        except Exception:
            pass
    return out



DOC_FLOW_TOOLS = {
    "list_sheets": _ft_list_sheets, "read_sheet": _ft_read_sheet,
    "filter_rows": _ft_filter_rows, "group_sum": _ft_group_sum,
    "write_sheet": _ft_write_sheet, "read_docx": _ft_read_docx,
    "write_docx": _ft_write_docx,
    # 宏观编辑步骤（面向写作流程）
    "format_fix": _ft_format_fix, "add_content": _ft_add_content,
    "logic_check": _ft_logic_check,
    # 标准文档编辑流程的其余步骤
    "structure_fix": _ft_structure_fix, "proofread": _ft_proofread,
    "polish": _ft_polish, "condense": _ft_condense,
    "terminology": _ft_terminology, "translate": _ft_translate,
    # 从 0 创建 / 编辑文档
    "create_docx": _ft_create_docx, "create_sheet": _ft_create_sheet,
    "add_toc": _ft_add_toc,
    # 定点编辑（改名/改单位/统一引用格式这类硬要求必须靠它）
    "replace_text": _ft_replace_text, "set_lines": _ft_set_lines,
    # 面向文件的版本（给 agent 直接调用：给文件名 -> 读 -> 改 -> 存回去）
    "replace_in_file": _ft_replace_in_file, "set_lines_in_file": _ft_set_lines_in_file,
    "export_docx": _ft_export_docx,
}


# ===== 单一真源：工具元数据 + 自动派生"面向文件"的版本 =====
# 要解决的问题（用户指明）：
#  ①多真源：新增一个文档工具要改四处（后端表 / 前端 agent 表 / 硬编码提示词 / 知识库），
#    实测后果就是"提示词让用 replace_text，agent 表里却没有它"的死循环。
#  ②契约错位：编辑类工具的 text 参数是**正文**，而模型天然提供**文件名**，
#    实测 agent 会传 text="pdf1.txt"。所以每个"吃正文"的工具都需要一个"吃文件名"的孪生版。
# 方案：后端用一份 DOC_TOOL_META 描述全部工具（名称/说明/参数/是否吃正文），
#   由它**自动派生** <tool>_file 变体（读文件 -> 跑工具 -> 就地写回），
#   并通过 /docs/tool/list 一次性交给前端；前端据此自动注册 agent 工具并生成提示词。

DOC_TOOL_META = {
    # 名字: (中文说明, 参数说明, 是否"吃正文"以派生文件版)
    "list_uploads":   ("列出已上传文件", {}, False),
    "read_upload":    ("读取已上传文件的正文（默认 3000 字，可用 offset 分段读）",
                       {"filename": "文件名", "max_chars": "本次最多返回多少字（默认 3000）",
                        "offset": "从第几个字开始（分段读用）"}, False),
    # ★ P-45（2026-10-19 实测）: read_docx/read_sheet 的 _ft_* 实现支持 out_dir ✓
    #   但元数据没声明 out_dir ✗ ⇒ 工具 schema 里没有它 ⇒ agent 永远读不到 uploads 以外的文件
    #   （典型：浏览器自动化刚下载到 E:\zy.work\from_agent 的 docx，agent 想 read_docx 读不回 ✗）
    "read_docx":      ("读取 Word 正文（段落与表格）",
                       {"filename": "文件名（.docx）",
                        "out_dir": "可选：文件所在目录（如 E:\\zy.work\\from_agent）；缺省在 uploads/outputs 找"}, False),
    "format_fix":     ("格式规范化：空白/换行/标点/合句（确定性规则，不用模型）",
                       {"text": "待规整的正文"}, True),
    "structure_fix":   ("分段与标题：按语义切段、规整标题层级",
                        {"text": "正文"}, True),
    "proofread":      ("校对纠错：错别字/标点/语病，只改错不改风格", {"text": "正文"}, True),
    "polish":         ("润色改写：更通顺书面，保持原意（不改语体层级）", {"text": "正文"}, True),
    "condense":       ("精简摘要：压冗余 + 末尾给 3-5 条要点", {"text": "正文"}, True),
    "logic_check":    ("逻辑核查：找前后矛盾/指代不清并给出修正正文", {"text": "正文"}, True),
    "terminology":    ("术语统一：统一同一概念叫法与人称，并列出改动", {"text": "正文"}, True),
    "add_content":    ("扩写补写：按主题补充段落", {"text": "已有正文", "topic": "补写主题",
                                                 "paragraphs": "补写段数（默认 1）"}, True),
    "translate":      ("整篇翻译：保留结构与标题层级", {"text": "正文", "target": "目标语言（默认英文）"}, True),
    "replace_text":   ("精确替换（直接给正文；agent 请优先用 replace_in_file）",
                       {"text": "正文", "find": "查找内容", "replace": "替换成",
                        "count": "最多几处（0=全部）", "regex": "是否按正则"}, True),
    "set_lines":      ("定点改行（直接给正文；agent 请优先用 set_lines_in_file）",
                       {"text": "正文", "from": "起始行号", "to": "结束行号",
                        "replacement": "新内容（缺省=删除）"}, True),
    "replace_in_file": ("在**某个文件**里精确替换（给文件名；自动备份 .orig 并就地写回）",
                        {"filename": "文件名", "find": "查找内容", "replace": "替换成",
                         "count": "最多几处（0=全部）", "regex": "是否按正则",
                         "out_name": "另存为（缺省=原地覆盖）"}, False),
    "set_lines_in_file": ("定点改**某个文件**的第 from~to 行（给文件名）",
                          {"filename": "文件名", "from": "起始行号", "to": "结束行号",
                           "replacement": "新内容（缺省=删除）", "out_name": "另存为（缺省=原地）"}, False),
    "export_docx":    ("把某个文本文件导出为 Word（可指定 out_dir，如 E 盘目录）",
                       {"source": "文本文件名", "out_dir": "输出目录", "name": "输出文件名",
                        "title": "文档标题"}, False),
    "create_docx":    ("从 0 创建 Word（标题 + 小节）",
                       {"title": "文档标题", "sections": "小节标题数组", "paragraphs": "正文",
                        "name": "输出文件名", "out_dir": "输出目录"}, False),
    "create_sheet":   ("从 0 创建 Excel（表头 + 数据行）",
                       {"name": "输出文件名", "sheet": "工作表名", "headers": "表头数组",
                        "rows": "数据二维数组", "out_dir": "输出目录"}, False),
    "add_toc":        ("给 Word 插入目录页（幂等：已有目录不重复添加）",
                       {"filename": "文件名（.docx）", "name": "输出文件名",
                        "out_dir": "输出目录"}, False),
    "list_sheets":    ("列出 Excel 的工作表", {"filename": "文件名"}, False),
    "read_sheet":     ("读取 Excel 的表头与若干行",
                       {"filename": "文件名", "sheet": "工作表名", "rows": "读取行数",
                        "out_dir": "可选：文件所在目录（如 E:\\目录）；缺省在 uploads/outputs 找"}, False),
    "filter_rows":    ("按条件筛选行",
                       {"filename": "文件名", "sheet": "工作表名", "column": "列名",
                        "op": "比较符", "value": "比较值", "limit": "最多返回"}, False),
    "group_sum":      ("按列分组求和",
                       {"filename": "文件名", "sheet": "工作表名", "group_by": "分组列",
                        "sum_column": "汇总列", "limit": "最多返回"}, False),
    "write_sheet":    ("写 Excel 文件（可指定 out_dir）",
                       {"rows": "数据来源", "name": "输出文件名", "sheet": "工作表名",
                        "header": "表头", "columns": "列名", "out_dir": "输出目录"}, False),
    "write_docx":     ("写 Word 文件（可指定 out_dir）",
                       {"paragraphs": "正文来源", "name": "输出文件名", "title": "文档标题",
                        "table": "表格数据", "out_dir": "输出目录"}, False),
}

# 需要"文件孪生版"的通用工具（吃正文、产出正文）
_FILE_TWIN = {
    "format_fix": "format_fix_file", "structure_fix": "structure_fix_file",
    "proofread": "proofread_file", "polish": "polish_file",
    "condense": "condense_file", "logic_check": "logic_check_file",
    "terminology": "terminology_file", "add_content": "add_content_file",
    "translate": "translate_file",
}


def _make_file_twin(tool_name):
    """为"吃正文"的工具生成一个"吃文件名"的孪生版：读文件 -> 跑工具 -> 就地写回。"""
    def _twin(a):
        fn = str(a.get("filename") or "").strip()
        if not fn:
            raise HTTPException(400, "需要 filename（要处理的文件名）")
        src, text = _doc_read_text_file(fn)
        args = {k: v for k, v in (a or {}).items()
                if k not in ("filename", "out_name", "out_dir")}
        args["text"] = text
        r = DOC_FLOW_TOOLS[tool_name](args)
        out_text = r.get("text") if isinstance(r, dict) else str(r)
        if not isinstance(out_text, str):
            out_text = text
        # 防呆：文件版语言工具若把正文压得很短（<60%），说明它"改写掉了内容"而不是"改写语言"，
        # 必须显式告警 —— 实测 condense_file 会把摘要/英文摘要整块压掉，导出后用户就少了大段内容。
        _before, _after = len(text), len(out_text or "")
        info = _doc_write_text_file(src.name, out_text, a.get("out_name"))
        if _before and _after < _before * 0.6:
            info["warning"] = (f"⚠️ 内容明显变短（{_before} -> {_after} 字，仅 {int(_after*100/_before)}%）："
                               f"{tool_name} 属于改写类工具，可能整块删掉了内容（如摘要/参考文献）。"
                               f"若只想改语言，请改用 proofread_file；原始内容保存在 "
                               f"{info.get('backup') or '备份文件'} 里。")
        # 结果里既保留原工具的统计字段，又加上文件信息（便于 agent 判断）
        merged = {k: v for k, v in (r.items() if isinstance(r, dict) else [])
                  if k not in ("text",)}
        merged.update(info)
        merged["preview"] = out_text[:200]
        merged["note"] = (f"{src.name} 已用 {tool_name} 处理；结果{'就地写回' if info.get('in_place') else '另存为 ' + info['out_filename']}")
        return merged
    _twin.__name__ = "_ft_" + _FILE_TWIN[tool_name]
    _twin.__doc__ = f"{tool_name} 的文件版：给文件名，读文件 -> 处理 -> 就地写回。"
    return _twin


# 注册孪生版
for _base, _alias in _FILE_TWIN.items():
    if _base in DOC_FLOW_TOOLS:
        DOC_FLOW_TOOLS[_alias] = _make_file_twin(_base)
        DOC_TOOL_META[_alias] = (
            f"{DOC_TOOL_META[_base][0]}（**文件版**：给文件名，自动读写文件）",
            {**{k: v for k, v in DOC_TOOL_META[_base][1].items() if k != "text"},
             "filename": "文件名", "out_name": "另存为（缺省=原地覆盖）"},
            False)

# 补齐元数据里未列出但已注册的工具（保证"表里有的都有说明"）
for _n in DOC_FLOW_TOOLS:
    if _n not in DOC_TOOL_META:
        DOC_TOOL_META[_n] = (DOC_FLOW_TOOLS[_n].__doc__ or _n, {}, False)




class DocToolRequest(BaseModel):
    name: str
    args: dict = {}
    # 工具内部若需要再调模型（proofread/terminology/… 都是），用哪个模型。
    # 前端把当前选中的模型 id 传进来，避免"agent 用云、工具内部偷偷用本地"。
    model: str = ""


# ★ 全局仿真节点（2026-10-19）: 任何 agent 需要【计算】时必须走这里 ✓ 禁止心算 ✗
#   它是"可插拔注册表"（见 sim_nodes.py）⇒ 以后加符号数学/物理/绘图等节点只需注册一次 ✓
#   为什么放在后端: ① 确定性可复算 ✓ ② 单一真源 ⇒ 前后端/工作流都用同一套 ✓
class SimRunRequest(BaseModel):
    node: str = ""
    # ★ 容错（实测: 前端 schema 把 args 声明成 string ⇒ 模型传 JSON 字符串 ✗ 422 ✓）
    #   所以这里收 dict|str 两种 ✓ str 时尝试 json 解析 ✓
    args: dict | str = {}


def _sim_coerce_args(v):
    """把 args 归一成 dict ✓ 字符串按 JSON 解析 ✓ 失败返回空 dict（让节点自己报缺参 ✓）"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        t = v.strip()
        if not t:
            return {}
        try:
            import json as _j
            d = _j.loads(t)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


# ★ 递归计算守卫（2026-10-19）: 每个"子代理/工作流节点/云端"作用域都要过这里 ✓
#   设计见 sim_guard.py: 作用域树 + 递归校验 + 自动修复（把算式用仿真节点真算 ✓）
class SimScopeOpen(BaseModel):
    parent: str | None = None
    kind: str = "turn"
    label: str = ""
    meta: dict = {}


class SimGuardText(BaseModel):
    scope_id: str | None = None
    text: str = ""


@app.post("/sim/guard/scope/open")
def sim_guard_open(req: SimScopeOpen):
    import sim_guard as _g
    sid = _g.open_scope(req.parent, kind=req.kind, label=req.label, meta=req.meta or {})
    return {"ok": True, "scope_id": sid}


@app.post("/sim/guard/scope/close")
def sim_guard_close(req: SimGuardText):
    import sim_guard as _g
    if not req.scope_id:
        raise HTTPException(400, "缺少 scope_id")
    return _g.close_scope(req.scope_id)


@app.post("/sim/guard/check")
def sim_guard_check(req: SimGuardText):
    import sim_guard as _g
    if not req.scope_id:
        d = _g.detect_compute(req.text)
        return {"ok": not d["needed"], "detected": d, "scope": None}
    return _g.check_scope(req.scope_id, text=req.text, recursive=True)


@app.post("/sim/guard/repair")
def sim_guard_repair(req: SimGuardText):
    import sim_guard as _g
    return _g.guard_text(req.text, scope_id=req.scope_id)


@app.get("/sim/guard/scopes")
def sim_guard_scopes():
    import sim_guard as _g
    return {"scopes": _g.list_scopes()}


# ★ 浏览器自动化（2026-10-19）: 后端转发到 Node sidecar（Windows 侧 ✓）
#   为什么带一层: ① 工具进 /docs/tool/list 单一真源 ✓ ② 安全策略/审计在后端 ✓ ③ 前端不直连 9223 ✓
class BrowserCallRequest(BaseModel):
    tool: str = ""
    args: dict | str = {}


def _sidecar_token_path():
    """统一 sidecar token 路径解析（不写死任何机器路径 ✓）

    历史: 2026-10-22 硬编码审计已删除全部本机路径（原 /mnt/e/... 等）
    2026-10-23: sidecar 把 token 写到 <项目>/browser_sidecar/.sidecar_token
                （跨 Windows/WSL 共享同一文件；见 browser_sidecar/server.mjs）

    优先级: env BROWSER_SIDECAR_TOKEN
            > <项目>/browser_sidecar/.sidecar_token（首选，__file__ 推导）
            > ~/.luxy/sidecar_token
            > ~/browser_sidecar/.sidecar_token
    """
    import os as _os
    import pathlib as _pl
    env = (_os.environ.get('BROWSER_SIDECAR_TOKEN') or '').strip()
    cands = []
    if env:
        cands.append(_pl.Path(env))
    # ★ 修（2026-10-22 硬编码审计）：原来写死 4 个本机路径 ✗
    #   改为标准位置优先（不写死任何机器路径）：
    #     ① 环境变量 BROWSER_SIDECAR_TOKEN（已在上方）
    #     ② ~/.luxy/sidecar_token        ← 推荐位置（安装脚本会写这里）
    #     ③ ~/browser_sidecar/.sidecar_token
    _home = _pl.Path.home()
      # ★ 2026-10-23: sidecar 跑在 Windows、平台跑在 WSL，两个 home 不是同一位置
      #   ⇒ 用【项目目录】下的 token（两边都能访问同一个文件 ✓）作为首选
    _proj = _pl.Path(__file__).resolve().parent
    cands.append(_proj / "browser_sidecar" / ".sidecar_token")
    cands += [
        _home / '.luxy' / 'sidecar_token',
        _home / 'browser_sidecar' / '.sidecar_token',
    ]
    # 说明: 原来这里还会重复 append ~/browser_sidecar/.sidecar_token
    #       （上方 cands 已含该路径）⇒ 死代码，2026-10-23 删除 ✓
    for p in cands:
        try:
            if p.exists() and p.read_text(encoding='utf-8').strip():
                return p
        except Exception:
            continue
    return _pl.Path(cands[0])


@app.get("/browser/tool/list")
def browser_tool_list():

    import browser_tools as _bt
    return {"count": len(_bt.BROWSER_TOOLS), "tools": _bt.list_tools()}


@app.get("/browser/sidecar-token")
def browser_sidecar_token(request: Request):
    """★ S-2（2026-10-19）: 把 sidecar 的访问 token 给【同源页面】✓
    安全依据: 页面在 127.0.0.1:8080 同源 ✓ 跨站页面读不到（CORS 已白名单 ✓）
    """
    # ★ 修安全漏洞（2026-10-22 验收）: 原本**无鉴权返回明文 token** ✗
    #   sidecar 在 172.20.64.1:9224 可达 ⇒ 同网段任何人可控制浏览器 ✗✗
    try:
        _org = str(request.headers.get("origin") or request.headers.get("referer") or "")
        _ok_org = any(_org.startswith(o) for o in ("http://127.0.0.1:8080", "http://localhost:8080"))
        if not _ok_org:
            print("[browser] 拒绝 token 请求，来源不可信: %s" % _org[:80], flush=True)
            raise HTTPException(403, "只允许同源页面获取 sidecar token ✗")
    except HTTPException:
        raise
    except Exception as _oe:
        raise HTTPException(403, "来源校验失败: %s" % _oe)

    import pathlib as _pl
    p = _sidecar_token_path()
    try:
        return {"ok": True, "token": p.read_text(encoding="utf-8").strip()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "hint": "sidecar 还没启动过（首次启动会生成 token ✓）"}


# ══════════════════════════════════════════════════════════
# ★ Agent 配置接口（2026-10-21 · 前端配置债重构 P-115）
#
# 为什么（用户指出的问题 ✓）:
#   原先 7,500+ 字符配置硬编码在前端 index3.html ✗
#   · 系统提示 3,799 字 ✗ · 工具表 2,910 字 ✗（双份真相 ✗）· 4 个闸门正则 972 字 ✗
# 问题:
#   ① 改提示词要改 HTML ✗（不能热更新/版本化/A-B ✗）
#   ② 工具表两边漂移 ✗（加 academic_search 时改了两处 ✗）
#   ③ 每次改都可能踩 template literal 反引号坑 ✗（P-113 踩过 ✗）
#
# 现在: **单一真相 = 后端** ✓
#   · 系统提示/策略 → agent_config.py ✓
#   · 工具表 → 从 _tool_docs.json **动态生成** ✓ 不写死 ✓
# 前端: fetch 成功就用后端的 ✓ 失败用内置默认 ⚠（双轨 ✓ 离线可跑 ✓）
# ══════════════════════════════════════════════════════════
@app.get("/agent/config")
def agent_config_all():
    """一次给全 ✓（前端首选 ✓ 减少往返 ✓）"""
    import agent_config as _ac
    return _ac.agent_config_payload()


@app.get("/agent/system-prompt")
def agent_system_prompt():
    """只要系统提示 ✓"""
    import agent_config as _ac
    return {"ok": True, "version": _ac.AGENT_PROMPT_VERSION,
            "system_prompt": _ac.AGENT_SYSTEM_PROMPT}


@app.get("/agent/policy")
def agent_policy():
    """只要闸门策略 ✓（route/compute/grounding/smalltalk/sim_tools）"""
    import agent_config as _ac
    return {"ok": True, "version": _ac.AGENT_PROMPT_VERSION,
            "policy": _ac.AGENT_POLICY}


# ══════════════════════════════════════════════════════════
# ★ 云边架构 第1步（2026-10-21）: /orchestrate 编排决策接口
#
# 为什么: 平台作为 **DSH 插件** ✓ ⇒ DSH（云端）需要知道
#   「这个任务，平台能做什么？该怎么做？」
#
# 定位: **决策层**（只出计划 ✓ 不执行 ✓）
#   · 模型路由（本地 7B vs 云端 ✓）
#   · 工具推荐（tool_retriever ✓ 92→8 ✓）
#   · 检索/知识库/仿真/训练需求判断 ✓
#   · 置信度信号（**可验证的外部信号** ✓ 不是模型自评 ✗）
#   · 能力清单（DSH 自我介绍用 ✓ 诚实标注未实现项 ✗）
#
# ⚠ 诚实边界:
#   · 编排是**建议** ✓ 不是强制执行 ✗
#   · 置信度打分器**未接入对话流程** ✗（features.confidence_in_chat=False ✓）
#   · 云端→本地自动执行需 **DSH 侧配合** ✓
# ══════════════════════════════════════════════════════════
class OrchestrateRequest(BaseModel):
    task: str
    context: dict | None = None
    strategy: str = "auto"      # auto | local_only | cloud_first


@app.post("/orchestrate")
def orchestrate_ep(req: OrchestrateRequest):
    """★ DSH 调这个 ⇒ 拿执行计划 ✓

    示例:
      POST /orchestrate
      {"task": "最近大模型置信度校准的论文", "strategy": "auto"}
      ⇒ {ok, route:{model:"cloud",...}, plan:[{action:"retrieve",...}],
         confidence:{level:"medium",signals:[...]}, capabilities:{...}}
    """
    import orchestrator as _orch
    try:
        return _orch.orchestrate(req.task, req.context, req.strategy)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "hint": "orchestrator.py 需在项目根目录 ✓"}


@app.get("/orchestrate/capabilities")
def orchestrate_capabilities():
    """★ 平台能力清单（DSH 用来自我发现 ✓）

    ⚠ **诚实**: 未实现的项明确写 false ✗ 不吹 ✗
    """
    import orchestrator as _orch
    try:
        return {"ok": True, "capabilities": _orch.capabilities()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/agent/tools")
def agent_tools(query: str = ""):
    """只要工具表 ✓（从 _tool_docs.json 动态生成 ✓）

    ★ P-19（2026-10-21）: 支持 ?query= 动态筛选 ✓
      · 不传 query ⇒ 返回全部 92 个 ✓（向后兼容 ✓）
      · 传 query   ⇒ 按相关度选 ≤30 个 ✓（省 47% token ✓）
      · 筛选后 <15 个 ⇒ 自动退回全部 ✓（安全兜底 ✗）
    """
    import agent_config as _ac
    return {"ok": True, "version": _ac.AGENT_PROMPT_VERSION,
            **_ac.agent_tools_payload(query)}


@app.get("/agent/prompt-version")
def agent_prompt_version():
    """版本号 ✓（前端缓存校验用 ✓）"""
    import agent_config as _ac
    return {"ok": True, "version": _ac.AGENT_PROMPT_VERSION}


@app.post("/academic/search")
def academic_search(payload: dict = None):
    """★ Grounding-First（2026-10-21）: 检索优质论文 ✓

    为什么需要（用户想法）:
      模型遇问题应【优先检索优质论文】而不是【幻觉推理】✗
    设计:
      · API 优先（arXiv / CrossRef / Semantic Scholar ✓ 结构化 ✓）
      · 浏览器兜底（API 拿不到才开 ✓ 慢且易坏 ⚠）
      · 返回结构化结果（标题/URL/日期/摘要/作者/DOI ✓ 可引用 ✓）
      · ★ 更重要的是: **检索成功/失败本身就是最可靠的置信度信号** ✓
        找到了 ⇒ 有据可依 ✓ 高置信；找不到 ⇒ 可能幻觉 ✗ 应拒答或标未验证 ✓
    """

    # ★ 修 BUG（2026-10-22 · 用户实测「找 AGI 文献」返回 0 条）
    #   根因: 原实现主源是 arXiv / Semantic Scholar
    #         实测 WSL: arxiv→000(不可达)  semantic scholar→429(限流)
    #         ⇒ academic_search 一直是死的 ✗ 但界面看起来正常 ✗
    #   修法: 优先用实测可达的国内/国际开放源 ✓
    #         api.openalex.org (200 ✓ 2.5亿开放文献 无需key)
    #         www.paper.edu.cn (200 ✓ 中国科技论文在线 开放获取)
    try:
        import academic_sources as _asrc
        _q = ''
        for _k in ('query', 'q', 'keyword', 'text'):
            if _k in locals():
                _q = str(locals()[_k] or '')
                break
        if not _q and isinstance(payload, dict):
            _q = str(payload.get('query') or payload.get('q') or '')
        _lim = 8
        for _k in ('limit', 'top_k', 'max_results'):
            if _k in locals():
                try:
                    _lim = int(locals()[_k] or 8)
                except Exception:
                    pass
                break
        if _q.strip():
            _hits = _asrc.search(_q, _lim)
            if _hits:
                print(f"[academic/search] openalex+cn 命中 {len(_hits)} 条: {_q[:40]}", flush=True)
                return {'ok': True, 'query': _q, 'count': len(_hits),
                        'source': 'openalex+paper.edu.cn',
                        'results': _hits,
                        'note': 'arXiv 在本机不可达，已改用 OpenAlex + 中国科技论文在线 ✓'}
    except Exception as _e:
        print(f"[academic/search] 新源失败(回退旧逻辑): {_e}", flush=True)

    import json as _j
    import subprocess as _sp
    import sys as _sys
    p = payload or {}
    q = str(p.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "query 不能为空")
    src = str(p.get("source") or "all").strip()
    limit = int(p.get("limit") or 5)
    if limit < 1 or limit > 20:
        limit = 5
    # 调 scripts/academic_search.py ✓（WSL 内 python ✓ 不占 GPU ✓）
    py = load_settings().get("venv_python") or _sys.executable
    script = str(PROJECT_DIR / "scripts" / "academic_search.py")
    try:
        r = _sp.run([py, script, q, "--source", src, "--limit", str(limit), "--json"],
                    capture_output=True, text=True, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"检索脚本调用失败: {type(e).__name__}: {e}")
    # 从 stdout 里抠 JSON ✓（脚本会先打印人类可读摘要 ✓）
    out = r.stdout or ""
    items = []
    i, j = out.find("["), out.rfind("]")
    if i >= 0 and j > i:
        try:
            items = _j.loads(out[i:j + 1])
        except Exception:
            items = []
    # ★ 置信度信号（关键 ✓）
    found = len(items)
    confidence = "high" if found >= 3 else ("medium" if found >= 1 else "low")
    return {
        "ok": True,
        "query": q,
        "found": found,
        "items": items[:10],
        # ★ 检索成功/失败 = 置信度信号 ✓
        "grounding_confidence": confidence,
        "advice": ("有据可依 ✓ 回答请带引用 ✓" if found > 0
                   else "⚠ 未找到依据 ⇒ 禁止编造论文名/DOI/作者 ✗ 应标未验证或拒答 ✓"),
        "elapsed_rc": r.returncode,
    }


@app.get("/browser/status")
def browser_status():
    import browser_tools as _bt
    return _bt.status()


# ★ 修 BUG（2026-10-22 · 同一个 browser_open 连发 3 次）
#   浏览器 agent 重复发相同调用 ⇒ 白烧 token + 卡住 ✗
#   修法: 90s 内同 tool + 同 args ⇒ 拦截 + 提示换策略 ✓
_BROWSER_DUP_GUARD: dict = {}


@app.post("/browser/call")
def browser_call(req: BrowserCallRequest, x_sidecar_token: str = Header(default="")):
    """执行一个浏览器动作 ✓ 永远 200 + {ok,...}（失败带 error ✓ 让模型看到原因 ✓）

    ★ P-52 修（2026-10-21）: **必须校验 sidecar token** ✓
    为什么:
      CORS 只阻止"读取"✗ 不阻止"发送"✗
      ⇒ 恶意网页能 POST /browser/call ⇒ **控制用户浏览器** ✗✗
    ⇒ 加 token 校验 ✓（token 从 /browser/sidecar-token 取 ✓ 同源可拿 ✓ 跨站拿不到 ✓）
    ⚠ 容错: token 文件不存在时（sidecar 从没启动过）⇒ 放行 + 警告 ⚠
      （否则首次使用会被锁死 ✗）
    """
    import pathlib as _pl
    _tp = _sidecar_token_path()
    if _tp.exists():
        try:
            _expect = _tp.read_text(encoding="utf-8").strip()
        except Exception:
            _expect = ""
        if _expect:
            _got = str(x_sidecar_token or "").strip()
            if _got != _expect:
                raise HTTPException(403, "P-52: sidecar token 不匹配 ✗（防 CSRF ✓ 从 /browser/sidecar-token 取 ✓）")
    # ★ 重复调用守卫（2026-10-22）—— 必须在 browser_call 函数内 ✓
    try:
        import hashlib as _hl2
        import time as _tm2
        _key2 = _hl2.md5((str(req.tool) + '|' + str(req.args)).encode('utf-8', 'ignore')).hexdigest()
        _now2 = _tm2.time()
        # ★ 修（2026-10-22 C组实测）：幂等/固定参数工具被去重误杀 ✗
        #   close / tabs / snapshot / screenshot / read / status / wait
        #   天然会被重复调用（第二轮必被拦，实测 5 次）⇒ 这类跳过去重 ✓
        # ★ 再修（2026-10-23 实测）：白名单写的是短名，实际工具名带 browser_ 前缀
        #   ⇒ 白名单从未命中，导致连续两次 browser_snapshot / browser_read /
        #   browser_screenshot 被 90 秒去重守卫拦截（"等待后重新快照"流程不可用）
        #   修法：先归一化工具名（去掉 browser_ 前缀）再比对 ✓
        _tool_norm = str(req.tool)
        if _tool_norm.startswith("browser_"):
            _tool_norm = _tool_norm[len("browser_"):]
        _IDEMPOTENT_TOOLS = {"close", "tabs", "snapshot", "screenshot",
                             "read", "status", "wait"}
        _prev2 = None if _tool_norm in _IDEMPOTENT_TOOLS else _BROWSER_DUP_GUARD.get(_key2)
        if _prev2 and (_now2 - _prev2) < 90:
            _left2 = int(90 - (_now2 - _prev2))
            print("[browser] 拦截重复调用: %s (还需 %ss)" % (req.tool, _left2), flush=True)
            return {
                'ok': False, 'tool': req.tool, 'duplicate': True,
                'error': ('⚠ 重复调用已拦截（%d 秒内已发过完全相同的请求）⇒ 请换策略：换 URL / 换选择器 / 换工具 ✗' % _left2),
                'hint': '找文献优先 academic_search 或直接 open 目标 URL，不要先开首页 ✗',
            }
        if len(_BROWSER_DUP_GUARD) > 200:
            for _k2 in [k for k, v in list(_BROWSER_DUP_GUARD.items()) if _now2 - v > 300]:
                _BROWSER_DUP_GUARD.pop(_k2, None)
        _BROWSER_DUP_GUARD[_key2] = _now2
    except Exception as _e2:
        print("[browser] 去重守卫异常(忽略): %s" % _e2, flush=True)

    import browser_tools as _bt
    args = req.args
    if isinstance(args, str):
        try:
            args = _json_parse(args) if False else __import__("json").loads(args or "{}")
        except Exception:
            args = {}
    return _bt.call(req.tool, args if isinstance(args, dict) else {})


# ══════════════════════════════════════════════════════════
# ★ P2-4（2026-10-21）: 工具质量打分
#   为什么: 92 个工具**不知道哪些好用/常失败** ✗
#   数据源: eval runs + trajectories ✓（零 GPU ✓ 全离线 ✓）
#   ⚠ 诚实: 观测统计 ✗ 不是因果分析 ✗
# ══════════════════════════════════════════════════════════
@app.get("/tools/quality")
def tools_quality():
    """工具质量打分 ✓

    返回: {tools_total, by_grade, tools:[{tool,uses,ok_rate,err_rate,avg_elapsed_s,grade,sources}]}
    评级: A(uses>=20 且 ok>=95%) / B / C / D(ok<60%) / ?(样本<3)
    """
    import tool_quality as _tq
    try:
        return {"ok": True, **_tq.report()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/sim/nodes")
def sim_nodes_list(tag: str | None = None):
    """列出可用仿真节点（含参数说明 ✓ 给 agent 与 UI 用 ✓）"""
    import sim_nodes as _sim
    nodes = _sim.list_sim_nodes(tag)
    return {"count": len(nodes), "nodes": nodes}


@app.post("/sim/run")
def sim_run(req: SimRunRequest):
    """执行一个仿真节点 ✓ 永远返回 200 + {ok, ...}（让模型能看到失败原因 ✓ 便于自我纠正 ✓）"""
    import sim_nodes as _sim
    return _sim.run_sim_node(req.node, _sim_coerce_args(req.args))


@app.get("/docs/tool/list")
def docs_tool_list():
    """**唯一真源**：返回全部文档工具的完整元数据。

    前端据此①自动注册 agent 工具 ②生成提示词里的工具清单 ③渲染参数面板；
    知识库生成脚本也读它。这样新增一个工具只需在后端注册一次，
    不会再出现"提示词让用某个工具、而 agent 那张表里没有它"的死循环。
    """
    tools = []
    for name in sorted(DOC_FLOW_TOOLS.keys()):
        desc, params, needs_text = DOC_TOOL_META.get(name, (name, {}, False))
        tools.append({
            "name": name,
            "description": desc,
            "params": [{"name": k, "hint": v} for k, v in (params or {}).items()],
            "takes_text": bool(needs_text),
            "file_oriented": name.endswith("_file") or name in
                             ("read_upload", "read_docx", "export_docx", "list_uploads",
                              "create_docx", "create_sheet", "add_toc", "write_docx",
                              "write_sheet", "list_sheets", "read_sheet", "filter_rows",
                              "group_sum"),
        })
    # ★ 全局仿真节点（2026-10-19）: 也走"唯一真源" ⇒ 自动进 agent 工具表 ✓
    try:
        import sim_nodes as _sim
        _names = ", ".join(sorted(x["name"] for x in _sim.list_sim_nodes())) or "math_eval/stats/ratio"
        tools.append({
            "name": "list_sim_nodes",
            "description": "列出可用的仿真节点（确定性计算工具 ✓ 做任何计算前先看有哪些 ✓）",
            "params": [{"name": "tag", "hint": "可选分类: math / stats"}],
            "takes_text": False, "file_oriented": False,
        })
        tools.append({
            "name": "run_simulation",
            "description": ("调用仿真节点做确定性计算（算术/统计/占比 ✓ 禁止心算 ✗）· 可用节点: " + _names),
            "params": [
                {"name": "node", "hint": "节点名（" + _names + "）"},
                {"name": "args", "hint": '该节点参数对象（对象类型 ✓ 不要传字符串 ✗），如 {"expr":"(1+2)*3"} 或 {"values":[1,2,3]}',
                 "type": "object"},
            ],
            "takes_text": False, "file_oriented": False,
        })
    except Exception as _sim_e:
        print(f"[docs/tool/list] 追加仿真节点工具失败(忽略): {_sim_e}", flush=True)

    # ★ 浏览器自动化工具（2026-10-19）: 同样走单一真源 ✓
    try:
        import browser_tools as _bt
        tools.extend(_bt.list_tools())
    except Exception as _bt_e:
        print(f"[docs/tool/list] 追加浏览器工具失败(忽略): {_bt_e}", flush=True)

    return {"count": len(tools), "tools": tools, "names": [x["name"] for x in tools]}


@app.post("/docs/tool")
def docs_tool_dispatch(req: DocToolRequest):
    """通用分发：按名字调用任一文档工具。

    为什么需要它：文档工具此前只能被"文档流程"内部调用，agent 的工具注册表
    （前端 AGENT_TOOLS）与后端 DOC_FLOW_TOOLS 是**两张独立的表** —— 于是出现
    "提示词让 agent 用 replace_text，但 agent 那张表里没有它 -> 未知工具 -> 死循环重试"。
    有了这个端点，前端注册一次即可调用**全部**文档工具，从源头消掉这个双表问题。
    """
    set_doc_llm_model(req.model or None)      # 本次请求的工具内部模型
    # ★ 名字容错（实测: agent 会传 training_list ✗ 而真名是 train_list / paper_lora_train_list ✓
    #   61 次报错里有一批就是"名字记错"→ 直接 404 → 它换名字反复撞 → 死循环 ✓）
    _want = str(req.name or "").strip()
    fn = DOC_FLOW_TOOLS.get(_want)
    if fn is None and _want:
        _cands = []
        # ① 去/加 paper_lora_ 前缀
        if _want.startswith("paper_lora_"):
            _cands.append(_want[len("paper_lora_"):])
        else:
            _cands.append("paper_lora_" + _want)
        # ② 常见同义（实测踩过的）
        _alias = {"training_list": "train_list", "training_status": "train_status",
                  "loras": "list_loras", "datasets": "list_datasets",
                  "kb_query": "kb_query", "read_sheet": "read_sheet"}
        _cands.append(_alias.get(_want, ""))
        _base = _want[len("paper_lora_"):] if _want.startswith("paper_lora_") else _want
        _cands.append(_alias.get(_base, ""))
        for _c in _cands:
            if _c and _c in DOC_FLOW_TOOLS:
                print(f"[docs/tool] 名字容错: {_want} -> {_c}", flush=True)
                fn = DOC_FLOW_TOOLS[_c]
                break
    if fn is None:
        # ★ 教育式报错（实测: agent 会把【工作台工具名】传进 paper_lora_call ✗
        #   例如 training_list ✓ 而 call 只管【文档/表格类】工具 ✓
        #   ✗ 加 difflib 猜候选会更糟: 它会给出 translate/set_lines 这种不相干的 → 带偏 ✓）
        _wk = ""
        _base = _want[len("paper_lora_"):] if _want.startswith("paper_lora_") else _want
        if any(_base.startswith(p) for p in ("train", "vllm", "kb", "lora", "list_", "status",
                                             "workflow", "tool", "modelscope", "ms_")):
            # ✗ 不要瞎拼工具名（实测我拼出 paper_lora_training_list 是错的 ✗ 反而带偏 ✓）
            _wk = ("这一看是【工作台能力】的名字 —— 它在你的工具清单里有【独立工具】"
                   "（名字通常是 paper_lora_ 开头的近似名，例如训练列表是 paper_lora_train_list），"
                   "请从清单里选对的【直接调用】，不要让 paper_lora_call 去转 ✗。")
        # ★ 修（2026-10-22 v4.2）：/docs/tool/list 宣称的 15 个工具
        #   （list_sim_nodes / run_simulation / 全部 browser_*）不在 DOC_FLOW_TOOLS
        #   ⇒ 派发一律 404 ⇒ agent 按提示词调用必然失败 ✗
        #   兜底 v2：报错前先尝试浏览器工具 / 仿真工具
        _nmF2 = str(req.name or "").strip()
        _aF2 = req.args if isinstance(getattr(req, "args", None), dict) else {}
        if _nmF2.startswith("browser_") or _nmF2.startswith("br_"):
            try:
                import browser_tools as _btF
                for _fnN in ("call_tool", "run_tool", "dispatch", "call"):
                    _fnF = getattr(_btF, _fnN, None)
                    if callable(_fnF):
                        print("[docs/tool] 浏览器工具兜底: %s" % _nmF2, flush=True)
                        return {"ok": True, "tool": _nmF2, "result": _fnF(_nmF2, _aF2)}
            except HTTPException:
                raise
            except Exception as _bF:
                raise HTTPException(500, "浏览器工具 %s 失败: %s" % (_nmF2, _bF))
        if _nmF2 == "list_sim_nodes":
            try:
                import sim_nodes as _snF
                for _fnN2 in ("list_nodes", "get_nodes", "available_nodes"):
                    _f2 = getattr(_snF, _fnN2, None)
                    if callable(_f2):
                        _nd2 = _f2()
                        return {"ok": True, "nodes": _nd2, "count": len(_nd2 or {})}
                _nd3 = getattr(_snF, "NODES", None) or getattr(_snF, "SIM_NODES", {})
                return {"ok": True, "nodes": _nd3, "count": len(_nd3 or {})}
            except Exception as _sF1:
                raise HTTPException(500, "读取仿真节点失败: %s" % _sF1)
        if _nmF2 == "run_simulation":
            _nodeF2 = str(_aF2.get("node") or "").strip()
            if not _nodeF2:
                raise HTTPException(400, "run_simulation 需要 node 参数")
            raise HTTPException(400, "run_simulation 请直接调用 POST /sim/run {node, args}（本派发器只代理文档/浏览器工具）")
        raise HTTPException(404,
            "未知文档工具: " + _want + "。注意 paper_lora_call 只能调用【文档/表格类】工具"
            "（read_docx / write_sheet / read_sheet / group_sum / replace_in_file / export_docx …，"
            "完整清单 GET /docs/tool/list）；训练、底座、知识库、工作流等各有【独立工具】，"
            "应直接调用，不要经过 call。" + _wk)
    try:
        r = fn(req.args or {})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"{req.name} 执行失败：{type(e).__name__}: {e}")
    finally:
        set_doc_llm_model(None)               # 复位，避免影响后续请求
    if isinstance(r, dict) and isinstance(r.get("text"), str) and len(r["text"]) > 6000:
        # 与 read_upload 同样的理由：不要把超大正文一次塞回上下文
        r = dict(r)
        r["text_len"] = len(r["text"])
        r["text"] = r["text"][:6000]
        r["truncated"] = True
        r["note"] = (str(r.get("note", "")) + "；返回正文已截到 6000 字").strip("；")
    return r



def doc_flow_run(flow, inputs):
    """按顺序执行步骤；**出错即停**，每步记录 {tool, ms, ok, error, result}。"""
    import time  # 本模块未在顶层导入 time（测试抓到过这个 NameError）
    steps = (flow or {}).get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise HTTPException(400, "流程里没有 steps")
    results, trace = [], []
    t_all = time.time()
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not step.get("tool"):
            raise HTTPException(400, f"第 {i + 1} 步缺少 tool")
        name = step["tool"]
        fn = DOC_FLOW_TOOLS.get(name)
        # ★ 修（2026-10-22 后端全功能测试）：/docs/tool/list 宣称的工具里
        #   有 15 个（list_sim_nodes / run_simulation / 全部 browser_*）
        #   不在 DOC_FLOW_TOOLS ⇒ 派发一律 404 ⇒ agent 按提示词调用必然失败 ✗
        #   修法：DOC_FLOW_TOOLS 查不到时，依次尝试 browser_tools 和仿真工具
        if fn is None:
            _nmF = str(req.name or "").strip()
            _argsF = req.args if isinstance(getattr(req, "args", None), dict) else {}
            # ① 浏览器工具兜底
            if _nmF.startswith("browser_") or _nmF.startswith("br_"):
                try:
                    import browser_tools as _bt2
                    for _fn_name in ("call_tool", "run_tool", "dispatch", "call"):
                        _fn2 = getattr(_bt2, _fn_name, None)
                        if callable(_fn2):
                            print("[docs/tool] 浏览器工具兜底: %s" % _nmF, flush=True)
                            return {"ok": True, "tool": _nmF, "result": _fn2(_nmF, _argsF)}
                except HTTPException:
                    raise
                except Exception as _bfe:
                    raise HTTPException(500, "浏览器工具 %s 执行失败: %s" % (_nmF, _bfe))
            # ② 仿真工具兜底
            if _nmF in ("list_sim_nodes", "run_simulation"):
                try:
                    import sim_nodes as _sn2
                    if _nmF == "list_sim_nodes":
                        _fns = getattr(_sn2, "list_nodes", None) or getattr(_sn2, "available_nodes", None)
                        _nodes = _fns() if callable(_fns) else getattr(_sn2, "NODES", {})
                        return {"ok": True, "nodes": _nodes, "count": len(_nodes or {})}
                    _nodeF = str(_argsF.get("node") or "").strip()
                    _fnF = getattr(_sn2, "run", None) or getattr(_sn2, "compute", None)
                    if callable(_fnF) and _nodeF:
                        print("[docs/tool] 仿真工具兜底: %s node=%s" % (_nmF, _nodeF), flush=True)
                        return {"ok": True, "result": _fnF(_nodeF, _argsF.get("args") or _argsF)}
                except HTTPException:
                    raise
                except Exception as _sfe:
                    raise HTTPException(500, "仿真工具 %s 执行失败: %s" % (_nmF, _sfe))

        if fn is None:
            raise HTTPException(400, f"第 {i + 1} 步的工具 {name!r} 不支持"
                                     f"（可用: {sorted(DOC_FLOW_TOOLS)}）")
        args = doc_flow_substitute(step.get("args") or {}, inputs, results)
        t0 = time.time()
        try:
            out = fn(args)
        except HTTPException as e:
            trace.append({"step": i + 1, "tool": name, "ok": False,
                          "ms": int((time.time() - t0) * 1000), "error": str(e.detail)})
            raise HTTPException(400, f"第 {i + 1} 步（{name}）失败：{e.detail}")
        except Exception as e:
            trace.append({"step": i + 1, "tool": name, "ok": False,
                          "ms": int((time.time() - t0) * 1000),
                          "error": f"{type(e).__name__}: {e}"})
            raise HTTPException(500, f"第 {i + 1} 步（{name}）异常：{type(e).__name__}: {e}")
        results.append(out)
        trace.append({"step": i + 1, "tool": name, "ok": True,
                      "ms": int((time.time() - t0) * 1000),
                      "summary": (json_lib.dumps(out, ensure_ascii=False)[:160])})
    return {"results": results, "trace": trace,
            "ms": int((time.time() - t_all) * 1000), "steps": len(steps)}


# ============ 反射学习：跑任务 / 判定升级 / 蒸馏入库（2026-10-11）============
# 三个脚本已实测可用（scripts/reflect_run.py / reflect_escalate.py / reflect_distill.py）✓
# 这里只做"起线程 + 汇报状态"的包装 ✓ 不重复实现逻辑 ✓
_REFLECT_JOBS = {}          # job_id -> {state, kind, log[], started_at, finished_at, result, error}
_REFLECT_LOCK = __import__("threading").Lock()
_REFLECT_SEQ = [0]


def _reflect_py():
    import sys as _s
    return _s.executable or "python3"


def _reflect_run_job(job_id, argv):
    """在后台线程里跑一个 reflect 脚本，把输出逐行记进 job。"""
    import subprocess
    job = _REFLECT_JOBS[job_id]
    job["state"] = "running"
    try:
        p = subprocess.Popen(argv, cwd=str(PROJECT_DIR), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", bufsize=1)
        for line in p.stdout:
            job["log"].append(line.rstrip("\n"))
            if len(job["log"]) > 2000:
                del job["log"][:1000]
        rc = p.wait()
        job["state"] = "done" if rc == 0 else "failed"
        job["result"] = {"rc": rc}
    except Exception as e:
        job["state"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished_at"] = __import__("datetime").datetime.now().isoformat()


def _reflect_start(kind, argv):
    with _REFLECT_LOCK:
        _REFLECT_SEQ[0] += 1
        job_id = f"rf{_REFLECT_SEQ[0]:04d}"
        _REFLECT_JOBS[job_id] = {"state": "queued", "kind": kind, "log": [],
                                 "started_at": __import__("datetime").datetime.now().isoformat()}
    import threading
    threading.Thread(target=_reflect_run_job, args=(job_id, argv), daemon=True).start()
    return job_id


# ============ 学习增强（阶段1：设置读写）============
# 为什么放在这里：它属于"对话"这一侧的行为配置 ✓ 与 /chat/* 同族 ✓
# 阶段1 只做【设置读写】✓ 不改任何运行行为 ✓（零风险 ✓ 后面阶段再接超时→接手→入库）
LEARN_CFG_PATH = PROJECT_DIR / "data" / "learning" / "config.json"
LEARN_DEFAULTS = {
    "enabled": False,          # 默认关 ✓
    "sync_train": False,       # 同步训练默认关 ✓（用户要求）
    "train_every": 50,         # 攒够多少条样本训练（条数自己调 ✓）
    "lora_prefix": "auto_learn",
    "base_model": "",          # 留空 = 当前底座
    "kb_mode": "new",          # new | existing（默认新建 ✓ 不污染现有库）
    "kb_name": "learned",      # 单库名（向后兼容 ✓）
    "kb_names": [],            # ★ 多库挂载（2026-10-21）空=用 kb_name ✓
    "timeout_sec": 300,        # 5 分钟 ✓
    "escalate_model": "",      # 留空 = 用默认云模型
    "build_kb": True,          # 采轨迹并提炼入库 ✓
    "distill_model": "",       # 留空 = 用接手模型
    "clean": {                 # 清洗规则（阶段3 用 ✓ 先落配置便于查看）
        "drop_incomplete": True,
        "drop_short_task": 25,
        "drop_long_steps": 12,
        "dedup": True,
        "strip_abs_paths": True,   # 脱敏: 只去掉本机绝对路径 ✓（你选的 1a）
    },
}


def _learn_cfg_read():
    cfg = dict(LEARN_DEFAULTS)
    try:
        if LEARN_CFG_PATH.exists():
            cfg.update(json_lib.loads(LEARN_CFG_PATH.read_text(encoding="utf-8")) or {})
    except Exception:
        pass
    return cfg


@app.get("/chat/learn/config")
def chat_learn_config():
    """读学习增强设置（含默认值 ✓ 前端直接渲染）。"""
    return {"config": _learn_cfg_read(),
            "defaults": LEARN_DEFAULTS,
            "note": "阶段1: 只存设置，尚未改变运行行为；轨迹必须先清洗再提炼入库"}


@app.put("/chat/learn/config")
def chat_learn_config_put(payload: dict = None):
    """写学习增强设置（只接受已知键 ✓ 值做范围收敛 ✓）。"""
    payload = payload or {}
    cfg = _learn_cfg_read()
    int_ranges = {"train_every": (10, 2000), "timeout_sec": (10, 3600)}
    for k in LEARN_DEFAULTS:
        if k not in payload:
            continue
        v = payload[k]
        if k in int_ranges:
            lo, hi = int_ranges[k]
            try:
                v = max(lo, min(hi, int(v)))
            except Exception:
                continue
        elif k == "kb_mode":
            v = "existing" if str(v) == "existing" else "new"
        elif k == "kb_names":
            # ★ 多库挂载（2026-10-21）: 必须是字符串列表 ✓
            if not isinstance(v, list):
                continue
            v = [str(x).strip() for x in v if str(x).strip()][:20]  # 上限 20 个 ✓
        elif k == "clean":
            if not isinstance(v, dict):
                continue
            merged = dict(LEARN_DEFAULTS["clean"])
            merged.update({kk: vv for kk, vv in v.items() if kk in merged})
            v = merged
        cfg[k] = v
    LEARN_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEARN_CFG_PATH.write_text(json_lib.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "success", "config": cfg}


@app.get("/reflect/status")
def reflect_status():
    """反射学习的现状：轨迹/升级/蒸馏三处文件与计数。"""
    traj_dir = PROJECT_DIR / "data" / "trajectories"
    def _count(name):
        f = traj_dir / name
        if not f.exists():
            return 0
        return sum(1 for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
    # 批次进度（reflect_run --task-file 跑的任务文件 + pid 文件 ✓）
    batch = {"running": False, "pid": None, "total": 0, "done": 0}
    try:
        tf = traj_dir / "tasks_100.txt"
        pf = traj_dir / "batch100.pid"
        if tf.exists():
            batch["total"] = sum(1 for x in tf.read_text(encoding="utf-8").splitlines() if x.strip())
        if pf.exists():
            pid = int(pf.read_text().strip() or 0)
            batch["pid"] = pid
            import os as _os
            try:
                _os.kill(pid, 0)
                batch["running"] = True
            except Exception:
                batch["running"] = False
        batch["done"] = max(0, _count("trajectories.jsonl") - 5)   # 批次前已有 5 条
        if batch["done"] > batch["total"]:
            batch["done"] = batch["total"]
    except Exception:
        pass
    distilled = PROJECT_DIR / "agent_kb" / "90_learned.md"
    scripts_ok = {n: (PROJECT_DIR / "scripts" / n).exists()
                  for n in ("reflect_run.py", "reflect_escalate.py", "reflect_distill.py")}
    jobs = sorted(_REFLECT_JOBS.items(), key=lambda kv: kv[0], reverse=True)[:5]
    return {
        "trajectories": _count("trajectories.jsonl"),
        "escalations": _count("escalations.jsonl"),
        "distilled": {"exists": distilled.exists(),
                      "chars": len(distilled.read_text(encoding="utf-8")) if distilled.exists() else 0,
                      "mtime": __import__("datetime").datetime.fromtimestamp(
                          distilled.stat().st_mtime).isoformat() if distilled.exists() else None},
        "scripts": scripts_ok,
        "batch": batch,
        "jobs": [{"id": k, "state": v["state"], "kind": v["kind"],
                  "tail": v["log"][-3:]} for k, v in jobs],
    }


@app.post("/reflect/run")
def reflect_run(payload: dict = None):
    """跑真任务并采轨迹。body: {tasks: ["…", …]} 或 {task: "…"}；也支持 {tasks_from: "文件"}"""
    payload = payload or {}
    argv = [_reflect_py(), str(PROJECT_DIR / "scripts" / "reflect_run.py")]
    if payload.get("tasks"):
        tmp = PROJECT_DIR / "data" / "trajectories" / ".tasks.txt"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("\n".join(str(t) for t in payload["tasks"]), encoding="utf-8")
        argv += ["--task-file", str(tmp)]
    elif payload.get("task"):
        argv += ["--task", str(payload["task"])]
    else:
        raise HTTPException(400, "需要 task 或 tasks")
    if payload.get("limit"):
        argv += ["--limit", str(int(payload["limit"]))]
    argv += ["--timeout", str(int(payload.get("timeout") or 300))]
    return {"job_id": _reflect_start("run", argv), "argv": argv[2:]}


@app.post("/reflect/escalate")
def reflect_escalate(payload: dict = None):
    """判定最新工作流结果并按需升级。body: {task?, result?, dry_run?}"""
    payload = payload or {}
    argv = [_reflect_py(), str(PROJECT_DIR / "scripts" / "reflect_escalate.py")]
    if payload.get("result"):
        argv += ["--result", str(payload["result"])]
    else:
        argv += ["--latest"]
    if payload.get("task"):
        argv += ["--task", str(payload["task"])]
    if payload.get("dry_run"):
        argv += ["--dry-run"]
    argv += ["--timeout", str(int(payload.get("timeout") or 600))]
    return {"job_id": _reflect_start("escalate", argv), "argv": argv[2:]}


@app.post("/reflect/distill")
def reflect_distill(payload: dict = None):
    """蒸馏轨迹 → agent_kb/90_learned.md（秒级 ✓ 同步）。body: {min_count?}"""
    import subprocess
    payload = payload or {}
    argv = [_reflect_py(), str(PROJECT_DIR / "scripts" / "reflect_distill.py")]
    if payload.get("min_count"):
        argv += ["--min-count", str(int(payload["min_count"]))]
    p = subprocess.run(argv, cwd=str(PROJECT_DIR), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return {"rc": p.returncode, "out": (p.stdout or "")[-2000:], "err": (p.stderr or "")[-1000:]}


@app.post("/reflect/ingest")
def reflect_ingest(payload: dict = None):
    """把 agent_kb/ 入库（幂等 ✓ 同名替换 ✓）。body: {kb_name?}"""
    # ★ 修（2026-10-22 验收）: 空 body 会**默认写入 agent_use 库** ✗ 危险默认值
    _p = payload if isinstance(payload, dict) else {}
    if not str(_p.get("kb_name") or "").strip():
        raise HTTPException(400, "kb_name 不能为空（原实现空 body 会默认写 agent_use 库 ✗）")
    payload = payload or {}
    kb_name = str(payload.get("kb_name") or "agent_use")
    try:
        info = kb.ingest_folder(kb_name, str(PROJECT_DIR / "agent_kb"), extensions=[".md"])
        return {"status": "success", "kb": kb_name, "info": info}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"入库失败: {type(e).__name__}: {e}")


@app.get("/reflect/job/{job_id}")
def reflect_job(job_id: str):
    job = _REFLECT_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"没有这个任务: {job_id}")
    return {"id": job_id, **{k: v for k, v in job.items() if k != "log"},
            "log_tail": job["log"][-60:], "log_lines": len(job["log"])}


@app.get("/docs/flows")
def docs_list_flows():
    """列出所有可复跑的文档流程。"""
    DOC_FLOW_DIR.mkdir(exist_ok=True)
    out = []
    for f in sorted(DOC_FLOW_DIR.glob("doc_flow*.json")):
        try:
            d = json_lib.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.name, "title": d.get("name", f.stem),
                        "description": d.get("description", ""),
                        "steps": len(d.get("steps") or []),
                        "tools": [s.get("tool") for s in (d.get("steps") or [])]})
        except Exception as e:
            out.append({"name": f.name, "error": str(e)})
    return {"flows": out, "dir": str(DOC_FLOW_DIR)}


@app.get("/docs/flows/{name}")
def docs_get_flow(name: str):
    path = _flow_path(name)
    if not path.exists():
        raise HTTPException(404, f"流程不存在: {path.name}")
    return {"name": path.name, "flow": json_lib.loads(path.read_text(encoding="utf-8"))}


@app.post("/docs/flows/save")
def docs_save_flow(payload: dict):
    """保存流程。body: {name, flow:{name, description, steps}}"""
    path = _flow_path(payload.get("name"))
    flow = payload.get("flow") or {}
    if not (flow.get("steps") or []):
        raise HTTPException(400, "流程必须至少有 1 个步骤")
    DOC_FLOW_DIR.mkdir(exist_ok=True)
    path.write_text(json_lib.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "success", "name": path.name, "steps": len(flow["steps"])}


@app.post("/docs/flows/run")
def docs_run_flow(payload: dict):
    """跑流程。body: {name?, flow?, inputs:{file?:...}}"""
    flow = payload.get("flow")
    if not flow:
        path = _flow_path(payload.get("name"))
        if not path.exists():
            raise HTTPException(404, f"流程不存在: {path.name}")
        flow = json_lib.loads(path.read_text(encoding="utf-8"))
    return doc_flow_run(flow, payload.get("inputs") or {})


# ===== 从魔搭社区（ModelScope）拉取模型 =====
# 为什么需要：本项目不附带模型权重，而用户拿到的是一个空 models 目录。
# 魔搭是国内最顺手的下载源，modelscope 已在依赖里，所以直接提供"填 repo id 就能下"的入口。
_time = __import__("time")
_MODEL_DL = {"state": "idle", "repo_id": "", "dir": "", "msg": "", "files": 0, "error": None}


@app.get("/models/download/status")
def models_download_status():
    """查询下载进度（下载在后台线程里跑，避免请求超时）。"""
    return dict(_MODEL_DL)


@app.post("/models/download")
def models_download(payload: dict):
    """从魔搭下载模型到 MODELS_DIR。

    body: {repo_id: "Qwen/Qwen2.5-7B-Instruct", local_name?: "Qwen2.5-7B-Instruct",
           allow_patterns?: ["*.json"]（可选，仅用于测试/只取部分文件）}
    """
    repo_id = str((payload or {}).get("repo_id") or "").strip()
    if not repo_id:
        raise HTTPException(400, "需要 repo_id，例如 Qwen/Qwen2.5-7B-Instruct")
    if _MODEL_DL["state"] == "running":
        raise HTTPException(409, f"已有下载在进行：{_MODEL_DL['repo_id']}")

    local_name = str((payload or {}).get("local_name") or "").strip() \
                 or repo_id.split("/")[-1]
    target = models_dir() / local_name
    patterns = (payload or {}).get("allow_patterns") or None

    _MODEL_DL.update({"state": "running", "repo_id": repo_id, "dir": str(target),
                      "msg": "准备中…", "files": 0, "error": None,
                      "total_mb": None, "done_mb": 0, "pct": None, "started": _time.time()})

    # 取仓库总大小（失败就只显示已下载体积，不假装有百分比）
    def _fetch_total():
        try:
            import importlib as _il
            _hub = _il.import_module("modelscope.hub.api")
            HubApi = getattr(_hub, "HubApi")
            files = HubApi().get_model_files(repo_id)
            tot = sum(int((x or {}).get("Size") or 0) for x in files if isinstance(x, dict))
            if tot > 0:
                _MODEL_DL["total_mb"] = round(tot / 1e6, 1)
        except Exception as e:
            print(f"[modelscope] 取总大小失败（将只显示已下载体积）: {e}")

    # 每秒统计目标目录体积 -> 与总大小算出真实百分比
    def _ticker():
        while _MODEL_DL["state"] == "running":
            try:
                if target.exists():
                    done = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
                    n = sum(1 for f in target.rglob("*") if f.is_file())
                    _MODEL_DL["done_mb"] = round(done / 1e6, 1)
                    _MODEL_DL["files"] = n
                    tot = _MODEL_DL.get("total_mb")
                    if tot:
                        _MODEL_DL["pct"] = min(99.9, round(done / 1e6 / tot * 100, 1))
            except Exception:
                pass
            _time.sleep(1)

    import threading
    threading.Thread(target=_fetch_total, daemon=True).start()
    threading.Thread(target=_ticker, daemon=True).start()

    def _work():
        try:
            # 注意：不能写 `from modelscope import snapshot_download` ——
            # 若同进程里先导入过 modelscope.hub.*，顶层 __init__ 可能处于"部分初始化"，
            # 该名字取不到（实测报 ImportError）。直接走规范子模块路径并留兜底。
            try:
                from modelscope.hub.snapshot_download import snapshot_download
            except Exception:
                from modelscope import snapshot_download
            _MODEL_DL["msg"] = "正在下载（大模型可能需要很久）…"
            path = snapshot_download(repo_id, local_dir=str(target),
                                     allow_patterns=patterns)
            n = sum(1 for _ in Path(path).rglob("*") if _.is_file())
            _MODEL_DL.update({"state": "done", "files": n, "pct": 100.0,
                              "msg": f"完成：{n} 个文件 -> {path}",
                              "elapsed_s": round(_time.time() - _MODEL_DL.get("started", _time.time()), 1)})
        except Exception as e:
            _MODEL_DL.update({"state": "error", "error": f"{type(e).__name__}: {e}",
                              "msg": "下载失败"})

    import threading
    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "repo_id": repo_id, "dir": str(target)}


@app.delete("/models/{name}")
def models_delete(name: str, confirm: bool = False):
    """删除 MODELS_DIR 下的一个模型目录。

    安全约束（宁可拒绝也不误删）：
    - 只接受**目录名**（不含路径分隔符、不含 ..）
    - 解析后必须确实位于 MODELS_DIR 之内
    - 必须显式 confirm=true
    - 若该模型正被 vLLM 加载，则拒绝（先停底座再删）
    """
    raw = str(name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(400, f"非法模型名: {name}")
    root = models_dir().resolve()
    target = (root / raw).resolve()
    if root not in target.parents:
        raise HTTPException(400, "拒绝删除：目标不在模型目录内")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, f"模型不存在: {raw}")

    # 正在被 vLLM 使用时拒绝
    try:
        import requests as _rq
        info = _rq.get("http://127.0.0.1:8000/v1/models", timeout=3).json()
        served = [m.get("id", "") for m in info.get("data", [])]
        if any(raw in s for s in served):
            raise HTTPException(409, f"模型 {raw} 正被 vLLM 使用，请先停止底座再删除")
    except HTTPException:
        raise
    except Exception:
        pass      # 底座没起或查询失败，不因此阻断删除

    if not confirm:
        raise HTTPException(400, "需要 confirm=true 才会真正删除")
    import shutil
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    shutil.rmtree(target)
    return {"status": "deleted", "name": raw, "freed_gb": round(size / 1e9, 2)}


# ══════ 幻觉检测独立端点（2026-10-22 · 修流式徽标失效）══════
# 问题: /chat/completions/stream 是 SSE 流式，无法在响应体里带结构化字段
#       ⇒ 普通模式的低置信/幻觉徽标**永远不出现** ✗
# 修法: 前端在流式结束后，把完整回答 POST 到这里 ⇒ 返回检测结果 ⇒ 显示徽标
@app.post("/hallucination/repair")
def hallucination_repair(payload: dict = None):
    """修复幻觉（检测 → 定位 → 修复 → 验证，不达标自动回滚）

    请求: {answer, source_text?, kb_names?, max_rounds?}
    返回: {ok, changed, text, before, after, repairs[], verified, rolled_back}
    """
    p = payload if isinstance(payload, dict) else {}
    ans = str(p.get("answer") or "")
    if not ans.strip():
        raise HTTPException(400, "answer 不能为空")
    if len(ans) > 20000:
        ans = ans[:20000]
    src = str(p.get("source_text") or "")
    # 没有来源时用挂载的知识库现场检索（与 /hallucination/check 一致）
    if not src:
        try:
            import kb as _kbR
            _stR = load_settings() or {}
            _mR = [str(x) for x in (_stR.get("kb_mounted") or []) if str(x).strip()]
            _chR = []
            for _nmR in _mR[:4]:
                try:
                    _rsR, _dgR = _kbR.query_kb_detailed(_nmR, ans[:300], 3)
                    for _rR in (_rsR or []):
                        _tR = str(_rR.get("text") or _rR.get("content") or "")
                        if _tR.strip():
                            _chR.append(_tR[:800])
                except Exception:
                    continue
            src = "\n".join(_chR)[:6000]
        except Exception as _eR:
            print("[halluc/repair] KB 来源检索失败(忽略): %s" % _eR, flush=True)
    try:
        import hallucination_repair as _hr
        rounds = _clamp_int(p.get("max_rounds"), 1, 3, 2)
        r = _hr.repair(ans, source_text=src, max_rounds=rounds,
                       check_network=bool(p.get("check_network", True)))
        print("[halluc/repair] %s→%s issues %s→%s repairs=%d verified=%s%s" % (
            r["before"]["risk"], r["after"]["risk"],
            r["before"]["issues"], r["after"]["issues"],
            len(r.get("repairs") or []), r.get("verified"),
            " (已回滚)" if r.get("rolled_back") else ""), flush=True)
        return r
    except Exception as e:
        print("[halluc/repair] 异常: %s" % e, flush=True)
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@app.post("/hallucination/check")
def hallucination_check(payload: dict = None):
    """对一段回答做 5 层幻觉检测（供流式接口的 UI 用）"""
    p = payload if isinstance(payload, dict) else {}
    ans = str(p.get("answer") or "")
    q = str(p.get("question") or "")
    src = str(p.get("source_text") or "")
    if not ans.strip():
        raise HTTPException(400, "answer 不能为空")
    if len(ans) > 20000:
        ans = ans[:20000]
    # 若没给 source，用挂载的知识库现场检索（与 /chat/tools 同逻辑）
    if not src:
        try:
            import kb as _kb
            _st = load_settings() or {}
            _mounted = [str(x) for x in (_st.get("kb_mounted") or []) if str(x).strip()]
            _chunks = []
            for _nm in _mounted[:4]:
                try:
                    _rs, _dg = _kb.query_kb_detailed(_nm, (q or ans)[:200], 3)
                    for _r in (_rs or []):
                        _t = str(_r.get("text") or _r.get("content") or "")
                        if _t.strip():
                            _chunks.append(_t[:800])
                except Exception:
                    continue
            src = "\n".join(_chunks)[:6000]
        except Exception as _e:
            print("[halluc/check] KB 来源检索失败: %s" % _e, flush=True)
    try:
        import hallucination_guard as _hg
        r = _hg.analyze(ans, tool_results_text="", uploads_dir=str(PROJECT_DIR / "uploads"),
                        source_text=src)
        if r.get("issues"):
            print("[halluc/check] 检出 %d 处: %s" % (
                len(r["issues"]), [x.get("type") for x in r["issues"]][:4]), flush=True)
        return {"ok": True, "hallucination": r, "source_chars": len(src)}
    except Exception as e:
        print("[halluc/check] 检测异常: %s" % e, flush=True)
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@app.get("/readiness")
def readiness():
    """环境自检：告诉用户"现在能干什么、缺什么、怎么补"。

    用途：没有 GPU/没有模型/没有云端 Key 的用户也需要**能启动并看懂下一步**，
    而不是遇到一堆晦涩报错（B4 降级路径）。
    """
    checks = []

    def add(name, ok, detail, fix=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})

    # 1) 依赖
    missing = []
    for mod in ("fastapi", "uvicorn", "requests", "chromadb", "docx", "openpyxl"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    add("运行依赖", not missing,
        "齐全" if not missing else f"缺少: {', '.join(missing)}",
        "pip install -r requirements.txt")

    # 2) 模型目录
    md = models_dir()
    models = [x.name for x in md.iterdir() if x.is_dir()] if md.exists() else []
    add("本地模型", bool(models),
        f"{md} 下有 {len(models)} 个模型" if models else f"{md} 下没有模型",
        "在「底座管理」里从魔搭社区下载，或把已有模型放进该目录（可用 MODELS_DIR 指定别处）")

    # 3) 推理底座
    up = False
    try:
        import requests as _rq
        up = _rq.get("http://127.0.0.1:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        up = False
    add("推理底座 (vLLM :8000)", up,
        "运行中" if up else "未启动",
        "有 GPU：在「底座管理」里启动；无 GPU：仍可用知识库/规则与文档工具（bash scripts/run.sh --no-gpu）")

    # 4) 云端 Key（可选，但没有本地底座时它是唯一的大模型来源）
    key_ok = False
    try:
        s = load_settings()
        for prov in (s.get("providers") or {}).values():
            if isinstance(prov, dict) and str(prov.get("api_key") or "").strip():
                key_ok = True
                break
    except Exception:
        pass
    add("云端 API Key（可选）", key_ok,
        "已配置" if key_ok else "未配置",
        "想用云端大模型时，在「系统设置」里填写（没有本地底座又不配 Key 时，只有知识库/规则与文档工具可用）")

    # 5) 预设
    presets = sorted(x.name for x in PROJECT_DIR.glob("workflow_config*.json"))
    add("预设工作流", bool(presets), f"{len(presets)} 个" if presets else "无",
        "确认 workflow_config.json 存在")

    ok_count = sum(1 for c in checks if c["ok"])
    if up and models:
        mode, hint = "full", "功能完整：分析与文档工作流都可用。"
    elif models and not up:
        mode, hint = "no-gpu", "可启动但没有推理底座：知识库/规则、文档工具（读表/筛选/汇总/导出）仍可用；需要大模型时请启动底座或配置云端 Key。"
    elif (not models) and key_ok:
        mode, hint = "cloud-only", "没有本地模型，但配了云端 Key：可用云端大模型跑流程（注意数据会发给该服务商）。"
    else:
        mode, hint = "minimal", "最小可用：知识库/规则与文档工具可用。要跑大模型流程，请下载本地模型（底座管理→魔搭）或配置云端 Key。"

    return {"ok": ok_count == len(checks), "ok_count": ok_count, "total": len(checks),
            "mode": mode, "hint": hint,
            "models_dir": str(md), "models": models[:10], "checks": checks}


@app.get("/models/list")
def models_list():
    """列出 MODELS_DIR 下已有的模型目录（供界面选择底座）。"""
    d = models_dir()
    items = []
    if d.exists():
        for x in sorted(d.iterdir()):
            if x.is_dir():
                size = sum(f.stat().st_size for f in x.rglob("*") if f.is_file())
                items.append({"name": x.name, "path": str(x), "size_gb": round(size / 1e9, 2)})
    return {"dir": str(d), "models": items}


@app.get("/uploads/text")
def uploads_text(filename: str, max_chars: int = 3000, offset: int = 0):
    """读取已上传文件的**正文**（供 agent 把内容取进上下文）。

    ⚠️ 曾经的真 bug（用户实测）：这里对 .pdf 走了"读原文件"的兜底，而读的是**二进制**，
    于是返回 `%PDF-1.7 ... /FlateDecode ... stream` 这种乱码；模型拿着乱码作答，
    再加上超长内容把上下文撑爆 -> vLLM 400。现在按格式选正确的抽取方式。
    """
    s = str(filename or "").strip()
    if not s or "/" in s or "\\" in s or ".." in s:
        raise HTTPException(400, f"非法文件名: {filename}")
    base = Path(s)
    ext = base.suffix.lower()
    # 读入瘦身：默认 3000 字（原先 12000 会把上下文挤爆，模型随后退化成复述原文）；
    # 需要更多内容时用 offset 分段读，而不是一次灌满。
    n = int(max_chars or 3000)
    off = max(0, int(offset or 0))

    # ① 文本类：直接读（也优先用同名 .txt 转换产物）
    TEXT_EXT = {".txt", ".md", ".text", ".csv", ".json"}
    cands = []
    if ext != ".txt":
        cands.append(UPLOAD_DIR / (base.stem + ".txt"))
    cands.append(UPLOAD_DIR / s)

    for c in cands:
        if not (c.exists() and c.is_file()):
            continue
        # 二进制格式：绝不当文本读
        if c.suffix.lower() not in TEXT_EXT:
            continue
        text = c.read_text(encoding="utf-8", errors="ignore")
        # 兜底防呆：如果内容明显是二进制混进来（PDF 头/大量控制字符），当作失败
        head = text[:8]
        ctrl = sum(1 for ch in text[:2000] if ord(ch) < 9 or (13 < ord(ch) < 32))
        if head.startswith("%PDF") or (text and ctrl / max(1, len(text[:2000])) > 0.05):
            raise HTTPException(415, f"{c.name} 不是纯文本（像是二进制），请用 read_docx / 文档流程读取")
        return {"filename": c.name, "source": base.name, "kind": "text",
                "chars": len(text), "truncated": len(text) > n + off, "offset": off,
                "text": text[off:off + n],
                "note": ("已从第 " + str(off) + " 字开始截取；要看后面的内容请加大 offset") if off else ""}

    # ② Word：用现成的抽取器
    if ext == ".docx":
        src = UPLOAD_DIR / s
        if not src.exists():
            raise HTTPException(404, f"文件不存在: {s}")
        d = read_docx_text(s)
        text = d.get("text") if isinstance(d, dict) else str(d)
        text = text or ""
        return {"filename": s, "source": s, "kind": "docx", "chars": len(text),
                "truncated": len(text) > n + off, "offset": off,
                "text": text[off:off + n],
                "note": ("已从第 " + str(off) + " 字开始截取；要看后面的内容请加大 offset")
                        if off else ""}

    # ③ PDF：真正抽取文本（此前是当二进制读，导致乱码）
    if ext == ".pdf":
        src = UPLOAD_DIR / s
        if not src.exists():
            raise HTTPException(404, f"文件不存在: {s}")
        text = ""
        try:
            import pdfplumber
            with pdfplumber.open(str(src)) as pdf:
                pages = []
                for pg in pdf.pages:
                    pages.append(pg.extract_text() or "")
                    if sum(len(x) for x in pages) > n * 3:
                        break
                text = "\n\n".join(pages)
        except Exception as e1:
            try:
                from pypdf import PdfReader
                text = "\n\n".join((p.extract_text() or "") for p in PdfReader(str(src)).pages)
            except Exception as e2:
                raise HTTPException(500, f"PDF 解析失败：{type(e2).__name__}: {e2}")
        text = text.strip()
        if not text:
            raise HTTPException(422, f"{s} 里没抽到文字（可能是扫描件，请走 OCR 上传路径）")
        return {"filename": s, "source": s, "kind": "pdf_extracted",
                "chars": len(text), "truncated": len(text) > n + off, "offset": off,
                "text": text[off:off + n],
                "note": ("已从第 " + str(off) + " 字开始截取；要看后面的内容请加大 offset")
                        if off else ""}

    # ④ 先区分"文件不存在"与"格式不支持" —— 混在一起会给出误导性的错误
    #    （我第一版就犯了这个错：不存在的 sample.txt 报"不支持把 .txt 当正文读取"）
    if not (UPLOAD_DIR / s).exists():
        raise HTTPException(404, f"文件不存在: {s}（可用 list_uploads 查看已上传文件）")
    hint = {"xlsx": "read_sheet", "xlsm": "read_sheet"}.get(ext.lstrip("."), "read_docx / 文档流程")
    raise HTTPException(415, f"不支持把 {ext or '该格式'} 当正文读取，请改用 {hint}")


@app.get("/roles")
def list_roles():
    """角色表: 哪些 LoRA 是【成品角色】✓ 它自带什么知识库 ✓ 与当前底座兼不兼容 ✓

    ★ 设计: 角色 = LoRA + 知识库，绑定关系写在 loras/registry.json 里
      （每个 LoRA 一个 role / kb 字段 ✓ 加角色只改配置 ✓ 代码不用动 ✓）
      is_checkpoint_of 的（训练中间产物）不算角色 ✓
    """
    on = os.environ.get("LORA_DIR") or str(Path.home() / "loras")
    reg_path = Path(on) / "registry.json"
    reg = {}
    try:
        if reg_path.exists():
            reg = json_lib.loads(reg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        reg = {}
    base_model = get_vllm_base_model()
    vllm_hidden = _get_model_hidden_size(base_model) if base_model else None
    roles = []
    for name, meta in (reg.items() if isinstance(reg, dict) else []):
        # ★ 专用字段 agent_role（不能用 role ✗ 那是质量台账的用途标签:
        #   "t2-FP16" / "test-artifact" / "broken" … 之前误用它 → 16 个 LoRA 全成角色 ✓）
        if not isinstance(meta, dict) or not meta.get("agent_role"):
            continue
        if meta.get("is_checkpoint_of"):
            continue
        path = Path(on) / name
        if not path.is_dir():
            continue          # ★ B: 已归档/不存在的 LoRA 不进角色表 ✓（实测归档后还列着 ✗）
        hidden = _get_lora_weight_hidden_size(str(path))
        roles.append({
            "lora": name,
            "name": str(meta.get("agent_role")),
            "kb": meta.get("agent_kb") or "",
            "desc": meta.get("agent_desc") or "",
            "persona": meta.get("persona") or "",
            "exists": path.is_dir(),
            "hidden_size": hidden,
            "compatible": (hidden == vllm_hidden) if (hidden and vllm_hidden) else None,
        })
    roles.sort(key=lambda r: r["name"])
    return {"roles": roles, "vllm_hidden_size": vllm_hidden}


@app.get("/chat/models")
def chat_models():
    models = []
    base = get_vllm_base_model()
    if base:
        models.append({
            "id": "vllm:base",
            "provider": "vllm", "name": os.path.basename(base),
            "model": None, "lora": None, "group": "本地底座"
        })
    for lora in scan_loras():
        models.append({
            "id": f"vllm:lora:{lora['name']}",
            "provider": "vllm", "name": lora["name"],
            "model": None, "lora": lora["name"], "group": "本地 LoRA"
        })
    settings = load_settings()
    providers = settings.get("providers", {})
    openai_cfg = providers.get("openai", {})
    if openai_cfg.get("api_key"):
        cur = openai_cfg.get("model")
        if cur:
            models.append({
                "id": f"openai:{cur}",
                "provider": "openai", "name": f"{cur}（{openai_cfg.get('api_base', '')[:40]}）",
                "model": cur, "lora": None, "group": "云 API"
            })
    ollama_cfg = providers.get("ollama", {})
    if ollama_cfg.get("api_base"):
        ollama_base = ollama_cfg["api_base"].rstrip("/")
        ollama_models = []
        # 尝试问 Ollama 要真实模型列表（超时短，避免阻塞）
        try:
            r = requests.get(f"{ollama_base}/api/tags", timeout=2)
            if r.status_code == 200:
                tags = r.json().get("models", [])
                ollama_models = [t.get("name") for t in tags if t.get("name")]
        except Exception:
            pass

        # 回退：如果问不到，用配置里的模型名
        if not ollama_models:
            cfg_model = ollama_cfg.get("model")
            if cfg_model:
                ollama_models = [cfg_model]

        for om in ollama_models:
            # 显示时加后缀标明来源
            disp = f"{om}（{ollama_base.replace('http://', '').replace('https://', '')}）"
            models.append({
                "id": f"ollama:{om}",
                "provider": "ollama", "name": disp,
                "model": om, "lora": None, "group": "本地 Ollama"
            })
    return {"models": models}


def _chat_call_vllm(req, messages):
    base = get_vllm_base_model()
    if req.lora:
        model = req.lora
    elif req.model_override:
        model = req.model_override
    elif base:
        model = base
    else:
        raise RuntimeError("vLLM 未运行或无底座模型")

    api_base = load_settings().get(
        "vllm_chat_api", "http://localhost:8000/v1/chat/completions"
    )
    payload = {
        "model": model, "messages": messages,
        "max_tokens": req.max_tokens, "temperature": _clamp_float(getattr(req, "temperature", 0.7), 0.0, 2.0, 0.7),
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        # ★ 实测: 角色对话会连续多轮用同一句式收尾 ✗（训练集模板被学死 ✓）
        #   → 挂 LoRA 时用 repetition_penalty / frequency_penalty 压住 ✓ 两道保险 ✓
        **({"repetition_penalty": 1.12, "frequency_penalty": 0.2} if getattr(req, "lora", None) else {}),
    }
    r = requests.post(api_base, json=payload, timeout=300)
    if r.status_code != 200:
        payload.pop("chat_template_kwargs", None)
        payload.pop("extra_body", None)
        r = requests.post(api_base, json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"vLLM {r.status_code}: {r.text[:200]}")
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    content = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', content,
                     flags=re.DOTALL | re.IGNORECASE).strip()
    return {"content": content, "usage": data.get("usage", {}), "model": model}


def _chat_call_openai(req, messages, cfg):
    api_base = (cfg.get("api_base") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    if not api_base or not api_key:
        raise RuntimeError("云 API 未配置 api_base 或 api_key")
    model = req.model_override or cfg.get("model") or "gpt-4o-mini"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages,
               "max_tokens": req.max_tokens, "temperature": _clamp_float(req.temperature, 0.0, 2.0, 0.7)}
    r = requests.post(f"{api_base}/chat/completions", json=payload,
                      headers=headers, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"云 API {r.status_code}: {r.text[:200]}")
    data = r.json()
    return {"content": data["choices"][0]["message"]["content"],
            "usage": data.get("usage", {}), "model": model}


def _chat_call_ollama(req, messages, cfg):
    api_base = (cfg.get("api_base") or "http://127.0.0.1:11434").rstrip("/")
    model = req.model_override or cfg.get("model") or "qwen2.5:7b"
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": _clamp_float(req.temperature, 0.0, 2.0, 0.7), "num_predict": req.max_tokens}}
    r = requests.post(f"{api_base}/api/chat", json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:200]}")
    data = r.json()
    return {"content": data.get("message", {}).get("content", ""),
            "usage": {}, "model": model}


def _learn_read_timeout(req, default=300):
    """本地模型"多少秒未响应算超时"。

    用 requests 的读取间隔超时实现 —— 语义天然一致 ✓ 不用自己写计时器 ✓
    （requests timeout=(连接, 读取间隔)；读取间隔指"多久没收到新数据"✓）
    """
    try:
        sec = int(((req.learn or {}).get("timeout_sec") or default))
    except Exception:
        sec = default
    return max(5, min(3600, sec))


def _stream_vllm(messages, req):
    base = get_vllm_base_model()
    model = req.lora or req.model_override or base
    if not model:
        yield f"data: {json_lib.dumps({'error': 'vLLM 未运行或无底座模型'})}\n\n"
        return
    api_base = load_settings().get("vllm_chat_api", "http://localhost:8000/v1/chat/completions")
    payload = {
        "model": model, "messages": messages,
        "max_tokens": req.max_tokens, "temperature": _clamp_float(req.temperature, 0.0, 2.0, 0.7),
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        # ★ 实测: 角色对话会连续多轮用同一句式收尾 ✗（训练集模板被学死 ✓）
        #   → 挂 LoRA 时用 repetition_penalty / frequency_penalty 压住 ✓ 两道保险 ✓
        **({"repetition_penalty": 1.12, "frequency_penalty": 0.2} if getattr(req, "lora", None) else {}),
    }
    try:
        with requests.post(api_base, json=payload, stream=True, timeout=(10, _learn_read_timeout(req))) as r:
            if r.status_code != 200:
                payload.pop("chat_template_kwargs", None)
                payload.pop("extra_body", None)
                with requests.post(api_base, json=payload, stream=True, timeout=(10, _learn_read_timeout(req))) as r2:
                    if r2.status_code != 200:
                        yield f"data: {json_lib.dumps({'error': f'vLLM {r2.status_code}'})}\n\n"
                        return
                    r = r2
                    for line in r.iter_lines():
                        if not line: continue
                        s = line.decode('utf-8')
                        if not s.startswith("data: "): continue
                        payload_str = s[6:]
                        if payload_str.strip() == "[DONE]": break
                        try:
                            d = json_lib.loads(payload_str)
                            c = d["choices"][0].get("delta", {}).get("content", "")
                            if c:
                                yield f"data: {json_lib.dumps({'delta': c}, ensure_ascii=False)}\n\n"
                        except Exception: pass
                    yield f"data: {json_lib.dumps({'done': True})}\n\n"
                    return
            for line in r.iter_lines():
                if not line: continue
                s = line.decode('utf-8')
                if not s.startswith("data: "): continue
                payload_str = s[6:]
                if payload_str.strip() == "[DONE]": break
                try:
                    d = json_lib.loads(payload_str)
                    c = d["choices"][0].get("delta", {}).get("content", "")
                    if c:
                        yield f"data: {json_lib.dumps({'delta': c}, ensure_ascii=False)}\n\n"
                except Exception: pass
    except Exception as e:
        yield f"data: {json_lib.dumps({'error': str(e)})}\n\n"
        return
    yield f"data: {json_lib.dumps({'done': True})}\n\n"


def _stream_ollama(messages, req, cfg):
    """Ollama 用 NDJSON 流（不是 SSE），逐行解析"""
    api_base = (cfg.get("api_base") or "http://127.0.0.1:11434").rstrip("/")
    model = req.model_override or cfg.get("model") or "qwen2.5:7b"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": _clamp_float(req.temperature, 0.0, 2.0, 0.7), "num_predict": req.max_tokens},
    }
    try:
        with requests.post(f"{api_base}/api/chat", json=payload, stream=True, timeout=(10, _learn_read_timeout(req))) as r:
            if r.status_code != 200:
                yield f"data: {json_lib.dumps({'error': f'Ollama {r.status_code}: {r.text[:200]}'}, ensure_ascii=False)}\n\n"
                return
            for line in r.iter_lines():
                if not line:
                    continue
                s = line.decode('utf-8')
                try:
                    d = json_lib.loads(s)
                    c = d.get("message", {}).get("content", "")
                    if c:
                        yield f"data: {json_lib.dumps({'delta': c}, ensure_ascii=False)}\n\n"
                    if d.get("done"):
                        break
                except Exception:
                    pass
    except Exception as e:
        yield f"data: {json_lib.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        return
    yield f"data: {json_lib.dumps({'done': True})}\n\n"


def _stream_openai(messages, req, cfg):
    api_base = (cfg.get("api_base") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    if not api_base or not api_key:
        yield f"data: {json_lib.dumps({'error': '云 API 未配置'})}\n\n"
        return
    model = req.model_override or cfg.get("model") or "gpt-4o-mini"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages,
               "max_tokens": req.max_tokens, "temperature": _clamp_float(req.temperature, 0.0, 2.0, 0.7), "stream": True}
    try:
        with requests.post(f"{api_base}/chat/completions", json=payload,
                          headers=headers, stream=True, timeout=(10, _learn_read_timeout(req))) as r:
            if r.status_code != 200:
                yield f"data: {json_lib.dumps({'error': f'云 API {r.status_code}'})}\n\n"
                return
            for line in r.iter_lines():
                if not line: continue
                s = line.decode('utf-8')
                if not s.startswith("data: "): continue
                payload_str = s[6:]
                if payload_str.strip() == "[DONE]": break
                try:
                    d = json_lib.loads(payload_str)
                    delta = d.get("choices", [{}])[0].get("delta", {})
                    c = delta.get("content") or ""
                    if c:
                        yield f"data: {json_lib.dumps({'delta': c}, ensure_ascii=False)}\n\n"
                except Exception: pass
    except Exception as e:
        yield f"data: {json_lib.dumps({'error': str(e)})}\n\n"
        return
    yield f"data: {json_lib.dumps({'done': True})}\n\n"


# ============ 上下文预算管理 ============
# 前端会把整段对话历史都发过来。一旦超过模型上下文窗口，vLLM 会直接 400，
# 用户只看到"请求失败"。这里在调用模型前按预算裁剪：保留 system 与最近若干轮，
# 丢弃更早的消息，并用 SSE notice 明确告知 —— 不静默丢弃。
DEFAULT_CONTEXT_LIMIT = 8192

# 已知云模型的上下文窗口（避免云模型被当成 8k 窗口用）
CLOUD_CONTEXT_LIMITS = {
    "deepseek-chat": 65536, "deepseek-reasoner": 65536,
    "gpt-4o": 128000, "gpt-4o-mini": 128000, "gpt-4.1": 1047576,
    "claude-3-5-sonnet": 200000, "claude-sonnet-4": 200000,
    "gemini-1.5-pro": 1048576, "gemini-2.0-flash": 1048576,
    "qwen-max": 32768, "glm-4": 131072, "moonshot-v1-128k": 131072,
}


def get_vllm_max_model_len(default=DEFAULT_CONTEXT_LIMIT):
    """从 vLLM /v1/models 读真实上下文窗口（服务端权威值，而非本地配置猜测）。"""
    try:
        r = requests.get(f"{VLLM_BASE}/v1/models", timeout=3)
        if r.status_code == 200:
            for m in r.json().get("data", []):
                if str(m.get("id", "")).startswith("/") and m.get("max_model_len"):
                    return int(m["max_model_len"])
    except Exception:
        pass
    return default


def estimate_tokens(text):
    """粗估 token 数，故意偏保守：中英混排按 1.6 字符/token 再放大 10%。"""
    if not text:
        return 0
    return int(len(str(text)) / 1.6 * 1.1) + 4


def _messages_tokens(messages):
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


def fit_messages_to_context(messages, limit, max_tokens):
    """
    按上下文预算裁剪消息。返回 (messages, dropped, used_tokens, truncated)。

    - system 消息永远保留（系统提示词/知识库注入不能被丢掉）
    - 从最早的非 system 消息开始丢，保留最近的对话
    - 若最新一条自身就超预算，则截断它并标记，而不是整条丢弃
    """
    limit = int(limit or DEFAULT_CONTEXT_LIMIT)
    reserve = max(512, min(int(max_tokens or 2048), max(256, limit // 3)))
    budget = max(limit - reserve, 256)

    systems = [m for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]

    kept = []
    used = _messages_tokens(systems)
    truncated = False
    for m in reversed(convo):
        t = estimate_tokens(m.get("content", ""))
        if used + t > budget:
            if kept:
                break
            room = max(budget - used, 128)
            chars = max(int((room - 8) / 1.1 * 1.6), 64)
            m = dict(m)
            m["content"] = "[内容过长，已按上下文预算截断]\n" + str(m.get("content") or "")[:chars]
            t = estimate_tokens(m["content"])
            truncated = True
        kept.append(m)
        used += t
        if used >= budget:
            break
    kept.reverse()
    dropped = len(convo) - len(kept)
    return systems + kept, dropped, used, truncated


def build_context_notices(limit, dropped, truncated):
    notices = []
    if dropped:
        notices.append({"notice": f"上下文预算 {limit} tokens：已自动省略较早的 {dropped} 条消息"})
    if truncated:
        notices.append({"notice": f"最新一条消息超过上下文预算（{limit} tokens），已截断"})
    return notices


def _prepend_events(gen, events):
    """把 notice 事件插到 SSE 流最前面，让用户知道发生了什么。"""
    def wrapped():
        for ev in events:
            yield f"data: {json_lib.dumps(ev, ensure_ascii=False)}\n\n"
        yield from gen
    return wrapped()


@app.get("/chat/context")
def chat_context(provider: str = "vllm"):
    """当前 provider 的上下文窗口，供前端展示与预算计算。

    曾经对所有非 vllm 的 provider 一律回退 8192，导致云模型上"任务被截断"
    （系统提示含 33 个工具说明 + 任务 + 读入内容，8192 装不下）。
    优先顺序：settings.providers.<name>.context_limit -> 已知云模型表 -> 默认值。
    """
    # 前端可能传完整 id（如 openai:deepseek-chat）：先归一化成 provider 名，
    # 否则匹配不到、回退 8192，导致云模型上任务被截断（实测踩过）。
    pname = str(provider or "").split(":")[0].strip()
    if pname == "vllm":
        return {"provider": provider, "max_model_len": get_vllm_max_model_len(), "source": "vllm"}
    cfg = (load_settings().get("providers") or {}).get(pname) or {}
    if not cfg and provider and ":" in str(provider):
        # 形如 openai:<model>：模型名也能用来查窗口表
        model_in_id = str(provider).split(":", 1)[1]
        if model_in_id in CLOUD_CONTEXT_LIMITS:
            return {"provider": pname or provider, "max_model_len": CLOUD_CONTEXT_LIMITS[model_in_id],
                    "source": "known-model"}
    if cfg.get("context_limit"):
        return {"provider": provider, "max_model_len": int(cfg["context_limit"]), "source": "settings"}
    model = str(cfg.get("model") or "")
    if model in CLOUD_CONTEXT_LIMITS:
        return {"provider": provider, "max_model_len": CLOUD_CONTEXT_LIMITS[model], "source": "known-model"}
    return {"provider": provider, "max_model_len": DEFAULT_CONTEXT_LIMIT, "source": "default"}
def _coerce_tool_args(raw):
    """把工具调用的 arguments 稳健地转成 dict。

    实测：不同网关/模型给出的形态五花八门 ——
      · 正常 JSON 字符串
      · 被再编码一次的字符串（里面还有转义引号）
      · 已经是 dict
      · 极端情况下是"双重再编码"
    这里依次尝试，最多解 4 轮；仍失败才原样返回 _raw，便于排查。
    """
    # 注意：本模块把 json 导入了别名（json_lib），直接用 json.loads 会失败 ——
    # 这正是"合法 JSON 字符串也解不开、一律落到 _raw"的原因（实测排查到）。
    _j = globals().get("json_lib") or globals().get("json")
    if _j is None:
        try:
            import json as _j  # type: ignore
        except Exception:
            return {"_raw": raw}
    cur = raw
    for _ in range(4):
        if isinstance(cur, dict):
            return cur
        if not isinstance(cur, str):
            break
        s = cur.strip()
        if not s:
            return {}
        try:
            cur = _j.loads(s)
            continue
        except Exception:
            pass
        # 退一步：把多余的转义去掉再试（例如 \" -> "）
        try:
            cur = _j.loads(s.replace('\\"', '"'))
            continue
        except Exception:
            break
    if isinstance(cur, dict):
        return cur
    return {"_raw": raw}


class ToolChatRequest(BaseModel):
    model: str = "openai:deepseek-chat"
    messages: list = []
    tools: list = []
    tool_choice: str = "auto"
    max_tokens: int = 2048
    temperature: float = 0.3
    kb: dict | None = None   # 修 D1: 指定知识库（原来被静默丢弃）


def _est_tokens(obj) -> int:
    """粗略估算 token 数：汉字/全角按 1 token，其余按 1/4 token。

    为什么不能用 len(json)/4：本项目的 system 提示是【中文】的，
    汉字约 1 token/字，用 /4 会严重低估（实测：前端按 /4 估算后仍 7330 > 4096 被拒 ✗）。
    """
    try:
        txt = obj if isinstance(obj, str) else json_lib.dumps(obj, ensure_ascii=False)
    except Exception:
        return 0
    cjk = 0
    for ch in txt:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
            cjk += 1
    other = len(txt) - cjk
    return int(cjk * 1.0 + other / 4.0) + 8


def _fit_tools_to_context(payload: dict, limit: int, min_tools: int = 6):
    """按窗口把 payload["tools"] 从尾部裁到装得下。返回 (裁掉数, 估算总 token)。"""
    tools = payload.get("tools") or []
    if not tools:
        return 0, _est_tokens(payload.get("messages") or [])
    base = dict(payload)
    base.pop("tools", None)
    base.pop("tool_choice", None)
    overhead = _est_tokens(base)
    budget = max(256, int(limit * 0.92) - overhead - int(payload.get("max_tokens") or 0))
    dropped = 0
    while tools and _est_tokens({"t": tools}) > budget:
        if len(tools) <= min_tools:
            break
        tools = tools[:-1]
        dropped += 1
    payload["tools"] = tools
    return dropped, overhead + _est_tokens({"t": tools})


@app.post("/chat/tools")
def chat_tools(req: ToolChatRequest):
    """原生 function-calling：把 tools 交给模型，返回结构化的 tool_calls。

    为什么需要：内置 agent 此前用"文本协议"（要求模型输出约定的标记再解析），
    本地 Qwen 能用，但云模型（deepseek-chat）会输出另一种带竖线的标记，
    前端解析不了 -> 工具调用丢失 -> 流程中断。
    原生接口由模型/网关保证格式，不再依赖文本解析。
    """

    # ===== 知识库上下文自动注入（2026-10-22 空装演练发现）=====
    # 问题: /chat/tools 原来没有 kb 参数 ⇒ Agent 模式完全用不上知识库
    #       实测: 建成 KB + 挂载 + 检索命中 3/3，模型仍答"我不知道是哪款"
    # 修法: 请求带 kb 就用；没带就自动用 settings.kb_mounted（单机默认全挂）
    _kb_ctx = None
    try:
        _kb_spec = getattr(req, "kb", None)
        _names = []
        if isinstance(_kb_spec, dict):
            _n0 = str(_kb_spec.get("name") or "").strip()
            if _n0:
                _names = [_n0]
        if not _names:
            try:
                _st0 = load_settings() or {}
                _names = _kb_session_mounts(_st0, getattr(req, "session_id", None))
            except Exception:
                _names = []
        _last_user = ""
        try:
            for _mm in reversed(req.messages or []):
                if isinstance(_mm, dict) and _mm.get("role") == "user":
                    _last_user = str(_mm.get("content") or "")
                    break
        except Exception:
            pass
        if _names and _last_user.strip():
            import kb as _kbx
            _top_k = 3
            if isinstance(_kb_spec, dict):
                try:
                    _top_k = _clamp_int(_kb_spec.get("top_k"), 1, 10, 3)
                except Exception:
                    _top_k = 3
            _chunks2 = []
            _used = []
            for _nm2 in _names[:4]:
                try:
                    _rs2, _dg2 = _kbx.query_kb_detailed(_nm2, _last_user[:300], _top_k)
                    for _r2 in (_rs2 or []):
                        _t2 = str(_r2.get("text") or _r2.get("content") or "").strip()
                        if _t2:
                            _chunks2.append(_t2[:900])
                            if _nm2 not in _used:
                                _used.append(_nm2)
                except Exception:
                    continue
            if _chunks2:
                _head = ("【知识库检索结果（必须优先依据这些内容回答；"
                         "若其中没有答案，就明确说知识库里没有，不要编造）】")
                _kb_ctx = _head + "\n来源: " + ", ".join(_used) + "\n\n" + "\n\n---\n\n".join(_chunks2[:6])
                print("[chat/tools] KB 注入: %d 库 %d 片" % (len(_used), len(_chunks2)), flush=True)
            else:
                print("[chat/tools] KB 已挂载但无命中: %s" % _names[:3], flush=True)
    except Exception as _kbe2:
        print("[chat/tools] KB 注入异常(忽略): %s" % _kbe2, flush=True)
    # ★ 修 502（2026-10-22 验收）: messages 为空时原实现仍外发到云
    #   ⇒ openai 返 400 被包成 502 ✗ 应在入口就 400 拒绝
    if not (getattr(req, "messages", None) or []):
        raise HTTPException(400, "messages 不能为空")
    if len(req.messages) > 200:
        raise HTTPException(400, "messages 条数过多（上限 200）")
    import requests as _rq
    import time as _time
    _t0 = _time.time()      # ★ P0-1: 计时（置信度特征 ✓）

    model_id = str(req.model or "")

    # ★ P1-2（2026-10-21）: **模型路由真执行** ✓
    #   问题: 原来 `model` 由前端硬选 ✗ orchestrator 的路由规则只是**建议** ✗
    #   方案: model="auto"/"route"/"" ⇒ **后端按任务自动路由** ✓
    #         明确 model（如 "vllm:base"）⇒ **完全不变** ✓ 不覆盖 ✗
    #   依据: scripts/... model_router.py（13/13 测试 ✓）
    #   ⚠ 规则是启发式 ✗ 不是训练的小模型 ✗
    _router_info = None
    try:
        import model_router as _mr
        if _mr.is_auto(model_id):
            # 可用性（轻量检查 ✓ 不做重探测 ✗）
            _prov_all = load_settings().get("providers") or {}
            _avail = {
                'local': True,
                'cloud': bool((_prov_all.get("openai") or {}).get("api_key")),
            }
            # 本地 vLLM 健康检查（localhost ✓ 快 ✓ 1 秒超时 ⚠）
            try:
                import vllm_manager as _vm
                _avail['local'] = bool(_vm.is_port_responding(8000, timeout=1))
            except Exception as _ve:
                # ⚠ 检查失败 ⇒ 假设本地可用 ✓（保守 ✓ 让路由继续 ✓）
                _avail['local'] = True
                print(f"[model_router] 本地健康检查失败(假设可用 ⚠): {_ve}", flush=True)
            # 任务文本 = 最后一条 user message ✓
            _task_text = ""
            for _m in reversed([x for x in (req.messages or []) if isinstance(x, dict)]):
                if _m.get("role") == "user":
                    _task_text = str(_m.get("content") or "")[:400]
                    break
            _router_info = _mr.resolve(model_id, _task_text, _avail)
            _mr.log_decision(_router_info, _task_text)
            model_id = _router_info["model_id"]
    except Exception as _re:
        print(f"[model_router] 路由异常(降级为原逻辑 ⚠): {_re}", flush=True)

    pname = model_id.split(":", 1)[0] or "vllm"
    cfg = (load_settings().get("providers") or {}).get(pname) or {}
    # 必须**原样透传**消息：assistant 的 tool_calls 与随后的 tool 结果要能对上，
    # 否则云 API 会 400（上一版把消息压成 {role, content}，丢掉了 tool_calls/tool_call_id，
    # 结果模型"反复试探参数"，任务失败）。
    keep_keys = ("role", "content", "name", "tool_calls", "tool_call_id")
    messages = [{k: m.get(k) for k in keep_keys if k in m}
                for m in (req.messages or []) if isinstance(m, dict)]
    for m in messages:
        if "content" not in m or m["content"] is None:
            m["content"] = ""
    # ══════ ★★ sim-router 确定性兜底（chat/tools）（2026-10-22 · 架构缺口修复）══════
    # 问题: 确定性反问只做在 /chat/completions/stream，**Agent 模式(/chat/tools)没有** ✗
    #   实测: Agent 模式问「43223N/45°/2kg 能飞多远」
    #         模型把 F/m 当速度、自设 angle=0，答 **4762 万米** ✗✗
    #   而 sim_router.route() 对这题判定完全正确:
    #         matched=True / can_compute=False / missing=['v0'] ✓
    #   ⇒ 缺的是"接线"，不是 router 能力
    # 修法: 与流式接口同一套逻辑
    #   ① 缺参数 ⇒ **不调模型**，直接返回确定性反问 ✓
    #   ② 参数齐全 ⇒ 调节点算，把结果注入 messages（必须直接使用）
    try:
        import sim_router as _srT
        _qsT = str(_last_user or "").strip()
        if _qsT:
            _rrT = _srT.route(_qsT)
            if _rrT.get("matched"):
                if not _rrT.get("can_compute"):
                    _missT = _rrT.get("missing") or []
                    _replyT = (
                        "这道题**缺少必要参数，无法得出确定结果** ⚠\n\n"
                        + "识别到的题型：" + str(_rrT.get("label")) + "（节点 " + str(_rrT.get("node")) + "）\n"
                        + "缺失参数：" + "、".join(_missT) + "\n\n"
                        + str(_rrT.get("advice", "")).split("⇒")[0].strip() + "\n\n"
                        + "已知参数：" + ", ".join("%s=%s" % (k, v) for k, v in (_rrT.get("params") or {}).items()) + "\n\n"
                        + "请补充上述参数后我再计算。**在没有这些参数的情况下，任何具体数值都是编造的** ✗"
                    )
                    print("[sim-router/tools] ⛔ 缺参数 ⇒ 确定性反问（不调模型）: %s missing=%s"
                          % (_rrT.get("node"), _missT), flush=True)
                    try:
                        import confidence_live as _clT
                        _confT = _clT.estimate(tool_calls=0, finish_reason="stop",
                                               elapsed_s=0.0, error=None, task="compute")
                    except Exception:
                        _confT = {"prob": 0.0, "level": "low"}
                    return {
                        "content": _replyT,
                        "tool_calls": [],
                        "finish_reason": "sim_router_deterministic",
                        "model": "sim_router(deterministic)",
                        "confidence": _confT,
                        "hallucination": {"ok": True, "risk": "low", "issues": [],
                                          "checked": {"by": "sim_router", "node": _rrT.get("node")},
                                          "summary": "确定性反问：缺参数，未调用模型"},
                    }
                else:
                    _bodyT = {"node": _rrT["node"], "args": dict(_rrT.get("params") or {})}
                    if _rrT.get("formula"):
                        _bodyT["args"]["formula"] = _rrT["formula"]
                    _resT = None
                    try:
                        import json as _jT, urllib.request as _uT, os as _osT
                        _portT = int(_osT.environ.get("BACKEND_PORT", "8001"))
                        _rqT = _uT.Request("http://127.0.0.1:%d/sim/run" % _portT,
                                           data=_jT.dumps(_bodyT).encode(),
                                           headers={"Content-Type": "application/json"})
                        with _uT.urlopen(_rqT, timeout=30) as _xT:
                            _resT = _jT.loads(_xT.read().decode())
                    except Exception as _eT:
                        print("[sim-router/tools] 节点调用失败: %s" % _eT, flush=True)
                    if _resT and _resT.get("ok"):
                        _linesT = "  %s(%s) = %s" % (
                            _rrT["node"], _rrT.get("formula") or "",
                            _jT.dumps(_resT.get("result"), ensure_ascii=False))
                        messages = [{"role": "system", "content": (
                            "【仿真节点已计算（确定性结果，必须直接使用，禁止自己算，禁止写近似值）】\n"
                            + _linesT)}] + list(messages)
                        print("[sim-router/tools] 已注入节点结果▸ %s" % _linesT[:120], flush=True)
                    else:
                        print("[sim-router/tools] 参数齐全但节点未返回: %s" % _rrT.get("node"), flush=True)
    except Exception as _sreT:
        print("[sim-router/tools] 异常(忽略): %s" % _sreT, flush=True)


    if _kb_ctx:
        messages = [{"role": "system", "content": _kb_ctx}] + list(messages)
    payload = {"model": "", "messages": messages,
               "max_tokens": _clamp_int(req.max_tokens, 1, 8192, 2048),
               "temperature": _clamp_float(float(req.temperature or 0.3), 0.0, 2.0, 0.7)}
    if req.tools:
        payload["tools"] = req.tools
        payload["tool_choice"] = req.tool_choice or "auto"

    # 上下文预算裁剪（与流式端点同一套逻辑）
    limit = int(chat_context(model_id).get("max_model_len") or DEFAULT_CONTEXT_LIMIT)
    messages, dropped, used, truncated = fit_messages_to_context(messages, limit, req.max_tokens)

    # ★★ 仿真**事前**注入（2026-10-22 · 用户要求：需要就自动路由到对应节点）
    #   为什么改事前：事后改文本失败 —— 模型用 LaTeX 写 \frac{...} ≈ 834.00
    #   ⇒ 正则替换找不到位置 ✗（实测 fixed=None）
    #   事前：先检测 → 先用仿真节点算真值 → 注入 system 强制模型直接用 ✓
    try:
        import sim_guard as _sgi
        _q2 = ""
        try:
            for _m2 in reversed(req.messages or []):
                if isinstance(_m2, dict) and _m2.get("role") == "user":
                    _q2 = str(_m2.get("content") or "")
                    break
        except Exception:
            pass
        _d2 = _sgi.detect_compute(_q2)
        if _d2.get("needed"):
            _vals = []
            for _e2 in (_d2.get("expressions") or [])[:3]:
                try:
                    _r2 = _sgi.repair("计算 " + _e2)
                    for _f2 in (_r2.get("fixes") or []):
                        _vals.append((_e2, _f2.get("display") or _f2.get("value"), _f2.get("node")))
                except Exception:
                    pass
            if _vals:
                _lines2 = "\n".join("  %s = %s（%s 节点已算）" % (_v[0], _v[1], _v[2]) for _v in _vals)
                _sys2 = {"role": "system",
                         "content": "【仿真节点已计算（确定性结果）——必须直接使用下面的值，禁止自己心算，也不要写成近似值】\n" + _lines2}
                payload["messages"] = [_sys2] + list(payload["messages"])
                print("[sim] 事前注入真值▸ %s" % _lines2.replace("\n", " | "), flush=True)
            else:
                print("[sim] 需计算但仿真未产出值: %s" % _d2.get("expressions"), flush=True)
    except Exception as _e2b:
        print("[sim] 事前注入异常(忽略): %s" % _e2b, flush=True)

    payload["messages"] = messages

    # ★ 按上下文窗口裁工具（小底座装不下 88 个工具的 schema；实测 4096 时 5643 tokens 就被拒 ✗）
    _tools_dropped, _est_before = _fit_tools_to_context(payload, limit)
    if _tools_dropped:
        print(f"[chat/tools] 工具表按上下文裁剪: 丢 {_tools_dropped} 个（窗口 {limit}，"
              f"估算 {_est_before} tokens）", flush=True)

    if pname == "openai":
        api_key = cfg.get("api_key")
        if not api_key:
            raise HTTPException(400, "云 provider 未配置 api_key（去「系统设置」填）")
        base = str(cfg.get("api_base") or "https://api.deepseek.com/v1").rstrip("/")
        payload["model"] = model_id.split(":", 1)[1] if ":" in model_id else (cfg.get("model") or "deepseek-chat")
        url = base + "/chat/completions"
        headers = {"Authorization": "Bearer " + str(api_key), "Content-Type": "application/json"}
    else:
        # ★ 原来写的是 VLLM_PORT —— 这个变量根本不存在 → NameError
        #   ⇒ 工作台 agent 一选【本地模型】就 500（云分支正常，所以一直没暴露 ✓）
        #   照别处的写法: 用 settings 的 providers.vllm.api_base，兜底写死 8000 ✓
        base = str(((load_settings().get("providers") or {}).get("vllm") or {})
                   .get("api_base") or "http://127.0.0.1:8000/v1").rstrip("/")
        # ★ T-2B 修复（2026-10-21）: agent 模式原先【只用底座】✗ 完全忽略 LoRA ✗
        #   实测: 前端 nativeToolCall 只传 model 字段（形如 "vllm:lora:<name>" ✓）
        #        ⇒ 老代码一律 get_vllm_base_model() ⇒ 选 LoRA 无效 ✗
        #        ⇒ base 与 LoRA 评测结果完全一样（因为根本是同一个模型 ✗）
        #   修: 从 model_id 解析 LoRA 名 ✓ 与 /chat/completions 的 _chat_call_vllm 保持一致 ✓
        _mid = str(req.model or "")
        if _mid.startswith("vllm:lora:"):
            payload["model"] = _mid.split("vllm:lora:", 1)[1].strip()
            print(f"[chat/tools] 挂 LoRA: {payload['model']}", flush=True)
        elif _mid.startswith("vllm:"):
            payload["model"] = get_vllm_base_model() or ""
        else:
            payload["model"] = get_vllm_base_model() or ""
        url = base + "/chat/completions"
        headers = {"Content-Type": "application/json"}

    try:
        r = _rq.post(url, json=payload, headers=headers, timeout=300)
    except Exception as e:
        raise HTTPException(502, f"调用 {pname} 失败：{type(e).__name__}: {e}")
    # ★ 估算仍可能偏差（不同模型的 tokenizer 不同）→ 用 vLLM 报【真实 token 数】再裁一轮并重试
    #   报错形如: your request has 7330 input tokens，maximum context length is 4096 tokens
    if r.status_code != 200 and "input tokens" in (r.text or "") and payload.get("tools"):
        import re as _re
        m = _re.search(r"has (\d+) input tokens", r.text)
        if m:
            _used_real = int(m.group(1))
            _tools_n = len(payload["tools"])
            _ratio = max(0.05, min(0.95, (limit * 0.85) / max(1, _used_real)))
            _keep = max(6, int(_tools_n * _ratio))
            if _keep < _tools_n:
                payload["tools"] = payload["tools"][:_keep]
                print(f"[chat/tools] 首次被拒（真实 {_used_real} tokens > {limit}）"
                      f"→ 工具 {_tools_n} → {_keep}，重试一次", flush=True)
                try:
                    r = _rq.post(url, json=payload, headers=headers, timeout=300)
                except Exception as e:
                    raise HTTPException(502, f"调用 {pname} 失败：{type(e).__name__}: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"{pname} 返回 HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    choice = (d.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for c in (msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        calls.append({"id": c.get("id") or "", "name": fn.get("name") or "",
                      "args": _coerce_tool_args(fn.get("arguments"))})
    # ★ 调试: 本地分支到底有没有拿到 tool_calls（排查"本地 agent 不调工具"✓）
    print(f"[chat/tools] model={payload['model'][:40]} tools={len(payload.get('tools') or [])} "
          f"→ tool_calls={len(calls)} finish={choice.get('finish_reason')} "
          f"content_len={len(msg.get('content') or '')}", flush=True)
    # ★ 递归守卫（2026-10-19）: 主 agent 路径的产出一律过"计算必须走仿真节点" ✓
    #   做法: 检测文本里的算式 ⇒ 用仿真节点真算 ⇒ 替换成精确值 ✓（不改工具调用 ✓）
    _content_guard = msg.get("content") or ""
    _sim_guard_info = None
    try:
        import sim_guard as _g
        if _content_guard:
            _gd = _g.guard_text(_content_guard)
            if _gd.get("needed"):
                _sim_guard_info = {"needed": True, "note": _gd.get("note")}
                if _gd.get("repaired") and _gd["repaired"] != _content_guard:
                    print(f"[sim_guard] /chat/tools 已复算: "
                          f"{[f.get('expr') for f in (_gd.get('repair') or {}).get('fixes', [])]}", flush=True)
                    _content_guard = _gd["repaired"]
    except Exception as _ge:
        print(f"[sim_guard] /chat/tools 守卫异常(忽略): {_ge}", flush=True)
    # ★ P0-1（2026-10-21）: 实时置信度 ✓
    #   依据: scripts/confidence_scorer.py（已剔除泄漏特征 ✓ 6 个干净特征 ✓）
    #   指标: 离线 ECE 0.0922 / AUROC 0.9828（high 档 78% 样本 100% 准确 ✓）
    #   ⚠ 诚实局限: 实时只能拿 3/6 特征真实值 ✗ 另 3 个是假设 ⚠
    #   ⚠ 不能预测"答错"✗ 只能识别"零工具"✓
    _conf = None
    try:
        import confidence_live as _cl
        _elapsed = _time.time() - _t0
        _conf = _cl.estimate(
                tool_calls=calls,
                finish_reason=(d.get("choices") or [{}])[0].get("finish_reason"),
                elapsed_s=_elapsed,
                error=None,
                task=str((([x for x in (req.messages or []) if isinstance(x, dict) and x.get("role") == "user"] or [{}])[-1].get("content")) or ""),
            )
    except Exception as _ce:
        print(f"[confidence] 计算失败(忽略 ✗): {_ce}", flush=True)

    # ★ L2+L4 幻觉检测（2026-10-22 新加）
    #   今天 4 个真实事故（假论文/编URL/编base64图/点广告）confidence 全没抓到
    #   ⇒ 内容层补检：链接可达性 / 伪图 / 编造文件 / 编造上传声明 / 编造引用
    _hg_result = None
    try:
        import hallucination_guard as _hg
        _tool_txt = ""
        try:
            _tool_txt = __import__('json').dumps(calls, ensure_ascii=False)[:8000]
        except Exception:
            pass
        # ★★ 知识库作为「来源」接入幻觉检测（2026-10-22）
        #   原来 source_text 为空 ⇒ L2b 只能说"数字无来源"，判不了对错 ✗
        #   现在：挂了知识库 ⇒ 检索本问题 ⇒ 检索结果作为可核验来源 ✓
        _kb_source_text = ""
        try:
            _kq = ""
            try:
                for _mm in reversed(req.messages or []):
                    if isinstance(_mm, dict) and _mm.get("role") == "user":
                        _kq = str(_mm.get("content") or "")
                        break
            except Exception:
                pass
            if _kq.strip():
                import kb as _kb
                _st2 = load_settings() or {}
                _mounted = _kb_session_mounts(_st2, getattr(req, "session_id", None))
                _chunks = []
                for _nm in _mounted[:4]:
                    try:
                        _rs, _dg = _kb.query_kb_detailed(_nm, _kq, 3)
                        for _r in (_rs or []):
                            _t = str(_r.get("text") or _r.get("content") or "")
                            if _t.strip():
                                _chunks.append(_t[:800])
                    except Exception:
                        continue
                _kb_source_text = "\n".join(_chunks)[:6000]
                if _kb_source_text:
                    print("[halluc] KB 来源已接入: %d 库 %d 片 %d 字" % (
                        len(_mounted), len(_chunks), len(_kb_source_text)), flush=True)
        except Exception as _kbe:
            print("[halluc] KB 来源检索失败(忽略): %s" % _kbe, flush=True)

        _hg_result = _hg.analyze(
            str(_content_guard or ''),
            tool_results_text=_tool_txt,
            uploads_dir=str(PROJECT_DIR / 'uploads'),
                source_text=_kb_source_text,
        )
        if _hg_result.get('issues'):
            print('[halluc] 检出 %d 处: %s' % (
                len(_hg_result['issues']),
                [x.get('type') for x in _hg_result['issues']][:4]), flush=True)
    except Exception as _hge:
        print('[halluc] 检测异常(忽略): %s' % _hge, flush=True)

    # ★★ 仿真自动路由（2026-10-22 · 用户要求：任何行为都检测仿真，需要就自动路由）
    #   背景：实测「5668768/6786=多少」模型心算答 835（真实 835.3622）✗
    #        sim_guard 早就有 detect_compute/repair，但从没接进 /chat/tools ✗
    #   做法：① 检测本轮是否需要确定性计算
    #         ② 需要 ⇒ 用 math_eval 真算
    #         ③ 答案里的数字与真值不符 ⇒ **自动替换** ✓
    _sg_info = None
    try:
        import sim_guard as _sg
        _q = ""
        try:
            for _mm in reversed(req.messages or []):
                if isinstance(_mm, dict) and _mm.get("role") == "user":
                    _q = str(_mm.get("content") or "")
                    break
        except Exception:
            pass
        _det = _sg.detect_compute((_q + " " + str(_content_guard or ""))[:4000])
        _sg_info = {"needed": _det.get("needed"), "reasons": _det.get("reasons"),
                    "expressions": _det.get("expressions"), "fixed": False}
        if _det.get("needed"):
            print("[sim]-> 需要确定性计算: %s" % _det.get("expressions"), flush=True)
            _rep = _sg.repair(str(_content_guard or ""))
            _fixes = _rep.get("fixes") or []
            if _fixes:
                _new = _rep.get("text") or _rep.get("repaired") or ""
                if _new and _new != _content_guard:
                    _content_guard = _new
                    _sg_info["fixed"] = True
                    print("[sim] 已自动纠正 %d 处: %s" % (
                        len(_fixes), [(f.get("expr"), f.get("display")) for f in _fixes][:3]), flush=True)
                _sg_info["fixes"] = [{"expr": f.get("expr"), "value": f.get("value"),
                                    "display": f.get("display"), "node": f.get("node")} for f in _fixes][:5]
    except Exception as _sge:
        print("[sim] 自动路由异常(忽略): %s" % _sge, flush=True)

    return {"content": _content_guard, "tool_calls": calls,
            "sim_guard": _sim_guard_info,
            "confidence": _conf,
        "hallucination": _hg_result,
            "router": _router_info,
            "provider": pname, "model": payload["model"],
            "context": {"limit": limit, "used": used, "dropped": dropped, "truncated": truncated,
                        "tools_used": len(payload.get("tools") or []),
                        "tools_dropped": _tools_dropped},
            "usage": d.get("usage") or {}}


# ★ 角色片段检索的【主语重排】（实测: 问"你的兵器"却检索出八戒的钉耙 ✗ → 模型照抄 ✓）
SWK_WORDS = ("悟空", "行者", "大聖", "大圣", "老孫", "老孙", "猴王", "孫悟空", "孙悟空",
             "齊天大聖", "齐天大圣", "孫行者", "孙行者", "美猴王")
OTHER_WORDS = ("八戒", "悟能", "老豬", "老猪", "釘鈀", "钉耙", "沙僧", "悟淨", "悟净",
               "捲簾", "卷帘", "月牙鏟", "月牙铲", "三藏", "唐僧", "長老", "长老")


def _rerank_swk(results, query):
    """把"以孙悟空为主语"的片段排前，把"八戒/沙僧为主语"的排后。

    ★ 为什么需要（实测 ✓）: 问"你的兵器是什么"时
      向量检索把【八戒说"老猪使的是钉耙"】排在第一 ✗
      → 模型就答"俺的兵器是钯"✗（它拿错了料 ✓ 不是学错了 ✓）
    """
    out = []
    for r in (results or []):
        txt = str(r.get("text") or "")
        me = sum(1 for w in SWK_WORDS if w in txt)
        other = sum(1 for w in OTHER_WORDS if w in txt)
        # 悟空出现得越多越好 ✓ 只有别人出现则显著降权 ✓
        bonus = 0.25 * min(me, 3) - 0.30 * max(0, other - (1 if me else 0))
        r2 = dict(r)
        r2["rerank_score"] = float(r.get("score") or 0) + bonus
        r2["_me"] = me
        r2["_other"] = other
        out.append(r2)
    out.sort(key=lambda x: -x["rerank_score"])
    return out


def _kb_query_for_role(kb_name, query, top_k):
    """角色问答的检索: 加主语 + 重排（治"拿错料"✗）。"""
    import kb as _kb
    hits = _kb.query_kb(kb_name, "孙悟空 " + str(query), max(3, top_k * 3))
    return _rerank_swk(hits, query)[:top_k]


def _get_lora_story(lora_name: str) -> str:
    """取角色的【剧情设定】（第三层 ✓ 与人格、知识库并列 ✓）。

    ★ 用户点破的关键: "我是谁"是【剧情】✗ 不是背景事实 ✓
      原文里根本没有（是本作二创 ✓）且剧情会发展 ✓
      → 不能训进 LoRA ✗ 必须按对话注入 ✓ 且要可随时改 ✓
    """
    try:
        reg_path = get_lora_dir() / "registry.json"
        if not reg_path.exists():
            return ""
        data = json_lib.loads(reg_path.read_text(encoding="utf-8")) or {}
        rel = str((data.get(lora_name) or {}).get("agent_story_file") or "").strip()
        if not rel:
            return ""
        p = PROJECT_DIR / rel
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _get_lora_persona(lora_name: str) -> str:
    """从 LoRA 质量台账 registry.json 里取该 LoRA 的人格提示词。

    设计: 身份【不靠训练】而靠提示词 ✓（训练集教"怎么说"✓ 这里给"我是谁"✓）
          registry.json 里没有 persona 字段 → 返回空 → 什么都不注入 ✓（零侵入 ✓）
    """
    try:
        reg_path = get_lora_dir() / "registry.json"
        if not reg_path.exists():
            return ""
        data = json_lib.loads(reg_path.read_text(encoding="utf-8")) or {}
        entry = data.get(lora_name) or {}
        return str(entry.get("persona") or "").strip()
    except Exception:
        return ""


PERSONA_MAX_TOKENS = 384        # 聊天口吻的上限（要长回答请显式调大 ✓）
PERSONA_MAX_TEMP = 0.45         # ★ 角色对话的温度上限
                                #   实测: 温度 0.8 时同一问题两次答案完全不同 ✗
                                #   （"师父是谁"一次答唐僧 ✓ 一次答"记不清"✗）→ 事实无稳定认知 ✓
                                #   人格由 LoRA 提供 ✓ 不需要高温来"活" ✓


def _cap_tokens_for_persona(req):
    """挂了 LoRA（= 在跟一个"人"聊天）时把 max_tokens 压到 384 ✓

    ★ 实测: 挂库后小问题也回一大堆（145 字且分点）✗ 不像聊天 ✓
      长度约束主要靠 persona ✓ 这里做一道硬闸 ✓ 不打扰其它场景 ✓
    """
    try:
        if req.lora and getattr(req, "provider", "") == "vllm":
            cur = int(getattr(req, "max_tokens", 0) or 0)
            if cur <= 0 or cur > PERSONA_MAX_TOKENS:
                req.max_tokens = PERSONA_MAX_TOKENS
            # ★ 压温度: 实测 0.8 时事实问答方差极大 ✗（同一问题两次答案不同 ✓）
            try:
                t = float(getattr(req, "temperature", 0) or 0)
                if t <= 0 or t > PERSONA_MAX_TEMP:
                    req.temperature = PERSONA_MAX_TEMP
            except Exception:
                req.temperature = PERSONA_MAX_TEMP
    except Exception:
        pass
    return req


def _inject_persona(messages, req):
    """挂了 LoRA 且该 LoRA 配了 persona → 在最前面插一条 system ✓

    ★ 实测: 上一轮训练集只有"情境→反应"✗ 没有"我是谁"✗
      → 问"你是谁"会乱编（实测编出"云中君"✗）→ 靠 persona 兜住身份 ✓
    """
    if not (req.lora and getattr(req, "provider", "") == "vllm"):
        return messages
    persona = _get_lora_persona(req.lora)
    story = _get_lora_story(req.lora)
    if not persona and not story:
        return messages
    if messages and messages[0].get("role") == "system":
        return messages
    parts = []
    if persona:
        parts.append(persona)
    if story:
        # ★ 剧情层: "我是谁"的答案在这里 ✓（本作二创 ✗ 原文没有 ✓ 可随时改 ✓）
        parts.append("\n【你眼下的处境（这部分是既成事实，不要当成别人的故事）】\n" + story)
    return [{"role": "system", "content": "\n".join(parts)}] + list(messages)


def _learn_verify_local(question, local_text):
    """事后能力校验：本地答完但答案可能错 ⇒ 返回失败原因（None = 校验通过）

    ★ 修 P0-2（2026-10-22 A组实测）：学习增强原来**没有任何能力判定**，
      只在"超时/报错/无输出"时才升级 ⇒ 小模型自信答错永不转云 ✗
    做法（可解释、确定性、不加延迟）:
      ① 从问题里抽算式（sim_guard.detect_compute / extract_expressions）
      ② 用确定性节点算出真值
      ③ 本地答案里的数字与真值不符 ⇒ 判"能力不足"，返回原因
    只做**能验证的**（算术/数值），不猜主观对错 ✓
    """
    try:
        import sim_guard as _sgv
    except Exception:
        return None
    try:
        det = _sgv.detect_compute(str(question or ''))
    except Exception:
        return None
    if not det.get('needed'):
        return None
    exprs = (det.get('expressions') or [])[:3]
    if not exprs:
        return None
    import re as _re5
    # 本地答案里的所有数字（含千分位/小数）
    nums_local = []
    for _m in _re5.finditer(r'-?\d[\d,]*\.?\d*', str(local_text or '')):
        try:
            nums_local.append(float(_m.group(0).replace(',', '')))
        except Exception:
            continue
    # ★ 修（2026-10-22 发布评估实测）：sim_guard **没有** eval_expr/compute ✗
    #   只有 detect_compute / extract_expressions / repair
    #   ⇒ 原来 hasattr 全 False ⇒ 能力判定永不触发 ⇒ 等于死代码 ✗
    #   修: 直接调 sim_nodes.math_eval 真算（已验证：math_eval() 存在且可用）✓
    for expr in exprs:
        true_val = None
        try:
            import sim_nodes as _sn5
            _r5 = _sn5.math_eval({'expr': expr})
            if isinstance(_r5, dict):
                _v5 = _r5.get('value')
                if _v5 is not None:
                    true_val = float(_v5)
        except Exception as _e5:
            true_val = None
            try:
                print("[learn] 能力校验算值失败: %s" % _e5, flush=True)
            except Exception:
                pass
        if true_val is None:
            continue
        # 本地答案里有没有接近真值的数字
        _hit = False
        for _n in nums_local:
            if abs(_n - true_val) <= max(abs(true_val) * 1e-6, 1e-6):
                _hit = True
                break
        if not _hit:
            return ("本地答案里的数值与确定性计算结果不符（%s 的真值约为 %s）⇒ 判断本地能力不足"
                    % (expr.strip()[:60], ('%.10g' % true_val)))
    return None


def _learn_handoff(gen, messages, req, _providers):
    """阶段2：本地流跑不出来（超时/出错/无输出）就交给接手模型，并把答案接着发出去。

    事件约定（前端认这个 ✓）:
      {"handoff": {"from": "本地模型", "to": "接手模型名", "reason": "…"}}   （接手开始）
      {"handoff_done": {"model": "...", "chars": N}}                        （接手结束）
    失败原因如实说明 ✓ 不假装本地成功 ✗
    """
    learn = req.learn or {}
    local_model = req.lora or req.model_override or get_vllm_base_model() or "本地模型"
    esc_model = str(learn.get("escalate_model") or "").strip()
    esc_provider = str(learn.get("escalate_provider") or "openai").strip() or "openai"
    read_to = _learn_read_timeout(req)
    got_any = False
    # ★ A1: 优先用前端上报的真实原因（"本地模型超时（10000ms）"✓）而不是泛泛的"没输出" ✓
    reason = str(learn.get("local_error") or "").strip() or None
    _acc = []          # ★ P0-2: 累积本地输出，供"事后能力校验"用
    try:
        for ev in gen:
            got_any = True
            # 本地自己报的错也算失败 ✓（例如 vLLM 未运行）
            if '"error"' in ev:
                reason = ev.strip()[:200]
                break
            try:
                if len(_acc) < 4000:
                    _acc.append(ev)
            except Exception:
                pass
            yield ev
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"       # requests 读取超时会走到这里 ✓
        try:
            print(f"[learn] 本地流中断: {reason}", flush=True)
        except Exception:
            pass

    if got_any and reason is None:
        # ★★ 修 P0-2（2026-10-22 A组实测）：**"本地答完了" ≠ "答对了"** ✗✗
        #   原来这里直接 return ⇒ 没有任何能力判定 ⇒ 小模型自信答错永不升级 ✗
        #   实测: 本地 3 题错 2 题（诺贝尔奖说"尚未颁发"、987654321×123456789 算错），
        #         learn.enabled=true 仍 **0 次**转手 ✗
        #   修: 加"事后确定性校验" —— 用 sim_guard 真算，与本地答案比对；
        #       不一致 ⇒ 视为"本地能力不足" ⇒ 升级到云 ✓（reason 如实说明差异）
        try:
            _local_txt = ''
            for _ev in _acc:
                try:
                    _p = _ev[6:] if _ev.startswith('data: ') else _ev
                    _o = json_lib.loads(_p)
                    _d = _o.get('delta')
                    if isinstance(_d, str):
                        _local_txt += _d
                    _cc = (_o.get('choices') or [{}])[0].get('delta', {}).get('content')
                    if _cc:
                        _local_txt += _cc
                except Exception:
                    continue
            _q_txt = ' '.join(str(m.get('content') or '') for m in (messages or [])
                              if isinstance(m, dict) and m.get('role') == 'user')
            _vfy = _learn_verify_local(_q_txt, _local_txt)
            if _vfy:
                reason = _vfy
                print("[learn] ⚠ 能力判定未通过 ⇒ 升级: %s" % _vfy[:160], flush=True)
            else:
                return                                # 校验通过 → 不接手 ✓ 省钱 ✓
        except Exception as _vfe:
            print("[learn] 能力校验异常(按通过处理): %s" % _vfe, flush=True)
            return

    if not reason:
        reason = f"本地 {read_to} 秒内没有输出"

    # ★ 修（2026-10-21）：**敏感任务禁止升级到云端** ✗
    #   为什么：学习增强开启时会自动接手 ⇒ 病历/合同/内部资料会离开本机 ✗
    #   和"本地部署、数据不外传"的定位直接矛盾 ✗✗
    #   规则：只要命中敏感词，无论本地是否失败，**都不升级**（如实报失败 ✓）
    _SENSITIVE = ('病历', '病情', '诊断', '用药', '处方', '身份证', '护照', '银行卡',
                  '密码', '工资', '薪资', '合同', '协议', '客户', '内部', '机密',
                  '保密', '隐私', '个人', '住址', '电话', '身份证号')
    _txt = ''
    try:
        _txt = ' '.join(str(m.get('content') or '') for m in (messages or [])
                        if isinstance(m, dict) and m.get('role') == 'user')
    except Exception:
        _txt = ''
    _hit = [k for k in _SENSITIVE if k in _txt]
    if _hit and str(esc_provider or '').lower() not in ('', 'vllm', 'local', 'ollama'):
        print(f"[learn] ⛔ 敏感任务禁止升级到云端: 命中 {_hit[:3]} provider={esc_provider}", flush=True)
        yield "data: " + json_lib.dumps({"error": "本地处理失败，且这是敏感任务 ⇒ 已阻止升级到云端 ⚠ 建议稍后重试或缩短输入"}, ensure_ascii=False) + "\n\n"
        return

    yield "data: " + json_lib.dumps({"handoff": {
        "from": local_model, "to": (esc_model or "默认云模型"),
        "provider": esc_provider, "reason": reason, "timeout_sec": read_to}},
        ensure_ascii=False) + "\n\n"

    # 用接手模型重跑（复用现成的流式函数 ✓ 不重复实现 ✓）
    sub = req.model_copy(deep=True)
    sub.provider = esc_provider
    sub.lora = None
    if esc_model:
        sub.model_override = esc_model
    chars = 0
    _collected = []
    try:
        if esc_provider == "openai":
            sub_gen = _stream_openai(messages, sub, _providers.get("openai", {}))
        elif esc_provider == "ollama":
            sub_gen = _stream_ollama(messages, sub, _providers.get("ollama", {}))
        else:
            sub_gen = _stream_vllm(messages, sub)
        for ev in sub_gen:
            chars += len(ev)
            try:
                _d = json_lib.loads(ev[6:]) if ev.startswith("data: ") else {}
                if isinstance(_d, dict) and _d.get("delta"):
                    _collected.append(str(_d["delta"]))
            except Exception:
                pass
            yield ev
    except Exception as e:
        yield "data: " + json_lib.dumps({"error": f"接手也失败了: {type(e).__name__}: {e}"},
                                        ensure_ascii=False) + "\n\n"
        return
    yield "data: " + json_lib.dumps({"handoff_done": {"model": esc_model or esc_provider,
                                                      "bytes": chars}}, ensure_ascii=False) + "\n\n"

    # ---------- 阶段3: 采轨迹 → 清洗 → 提炼 → 入库（失败不影响对话 ✓ 只记日志 ✓）
    if learn.get("build_kb", True):
        try:
            import sys as _sys, time as _time
            _sys.path.insert(0, str(PROJECT_DIR / "scripts"))
            import learn_sink as _sink
            _txt = "".join(_collected)                    # 上面收集到的正文
            _traj = {
                "task": (messages[-1].get("content") if messages else "") or "",
                "session_id": f"chat-{int(_time.time())}",
                "reason": "completed",
                "error": None,
                "final": _txt,
                "steps": [{"turn": 1, "step": 1, "thinking": "", "text": "",
                           "tool_calls": [{"callId": "cloud", "tool": esc_model or esc_provider,
                                           "input": {}, "status": "completed", "result": _txt[:2000]}],
                           "usage": None}],
                "tools_used": [esc_model or esc_provider],
                "tool_errors": [],
                "usage": {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0},
                "ms": 0, "ts": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                "provider": "learn-handoff", "profile": esc_provider,
                "rc": 0, "diagnostics": [],
            }
            _stats = _sink.sink_session(_traj, learn)
            yield "data: " + json_lib.dumps({"learn_stats": _stats}, ensure_ascii=False) + "\n\n"
            print(f"[learn] 阶段3 结果: {_stats}", flush=True)
        except Exception as _e:
            print(f"[learn] 阶段3 失败（不影响对话）: {type(_e).__name__}: {_e}", flush=True)


@app.post("/chat/completions/stream")
def chat_completions_stream(req: ChatRequest):

    kb_context = []
    # ★ 自动挂载（2026-10-22 空装演练发现）: 普通模式原来**必须手动选库** ✗
    #   Agent 模式已改为自动用 kb_mounted；流式接口这里补上同样的行为 ✓
    _auto_kb_names = []
    if not (req.kb and req.kb.get("name")):
        try:
            _st_auto = load_settings() or {}
            _auto_kb_names = _kb_session_mounts(_st_auto, getattr(req, "session_id", None))
            if _auto_kb_names:
                req.kb = {"name": _auto_kb_names[0], "top_k": 3}
                print("[stream] KB 自动挂载: %s" % _auto_kb_names[:3], flush=True)
        except Exception as _ake:
            print("[stream] KB 自动挂载失败(忽略): %s" % _ake, flush=True)
    if req.kb and req.kb.get("name"):
        try:
            query = req.kb.get("query") or (req.messages[-1].content[:200] if req.messages else "")
            # ★ 挂 LoRA（角色问答）时用【加主语 + 主语重排】的检索 ✓
            if getattr(req, "lora", None):
                try:
                    kb_context = kb.query_kb(req.kb["name"], query, req.kb.get("top_k", 3))
                except Exception as _eKB:
                    # ★★ 修（2026-10-22 逆天 bug）: KB 检索失败**不能中断对话** ✗
                    #   原来直接抛 ValueError ⇒ 用户发一句"你好"都会得到报错 ✗✗
                    #   修: 降级为无知识库回答，并打印告警 ✓
                    print("[stream] ⚠ KB 检索失败，降级为无知识库回答: %s" % _eKB, flush=True)
                    kb_context = ""
                    try:
                        _stBad = load_settings() or {}
                        _badName = str(req.kb.get("name") or "")
                        _mBad = [str(x) for x in (_stBad.get("kb_mounted") or []) if str(x) != _badName]
                        if _mBad != [str(x) for x in (_stBad.get("kb_mounted") or [])]:
                            _stBad["kb_mounted"] = _mBad
                            save_settings(_stBad)
                            print("[stream] 已自动移除失效挂载: %s" % _badName, flush=True)
                    except Exception:
                        pass

                # 多库合并：其余已挂载的库也查一遍
                for _ex in (_auto_kb_names[1:3] if _auto_kb_names else []):
                    try:
                        _extra_txt = kb.query_kb(_ex, query, 2)
                        if _extra_txt:
                            kb_context = (kb_context or "") + "\n\n---\n\n[来自 %s]\n" % _ex + str(_extra_txt)
                    except Exception:
                        pass
            else:
                # ★★ 修（2026-10-22 逆天 bug · else 分支）:
                #   上面只给 LoRA 分支加了 try/except，**普通对话走的是这里** ✗
                #   实测: 悬空挂载时用户发「你好」⇒ 直接返回 "KB 检索失败" ✗✗
                try:
                    kb_context = kb.query_kb(req.kb["name"], query, req.kb.get("top_k", 3))
                except Exception as _eKB2:
                    print("[stream] ⚠ KB 检索失败(else 分支)，降级为无知识库回答: %s" % _eKB2, flush=True)
                    kb_context = ""
                    try:
                        _stB2 = load_settings() or {}
                        _bad2 = str(req.kb.get("name") or "")
                        _mB2 = [str(x) for x in (_stB2.get("kb_mounted") or []) if str(x) != _bad2]
                        if _mB2 != [str(x) for x in (_stB2.get("kb_mounted") or [])]:
                            _stB2["kb_mounted"] = _mB2
                            save_settings(_stB2)
                            print("[stream] 已自动移除失效挂载: %s" % _bad2, flush=True)
                    except Exception:
                        pass
        except Exception as e:
            # ★ PEP 3110: except ... as e 的 e 在退出该块时被删除 ✗
            #   而 err_gen 是延迟执行的 → 执行时 e 已不存在 → NameError → 连接中断 ✗
            #   实测后果: KB 一失败，错误处理自己也炸 ✗ 前端只看到 "network error" ✗
            # ★★ 修（2026-10-22）: 原来这里**直接返回错误 SSE 中断整轮对话** ✗
            #   KB 只是"锦上添花"，它坏了不该让用户连"你好"都问不了 ✗✗
            #   修: 只打印告警，继续无知识库回答 ✓
            print("[stream] ⚠ KB 上下文构建失败，降级为无知识库回答: %s: %s"
                  % (type(e).__name__, e), flush=True)
            kb_context = []
            try:
                _stB3 = load_settings() or {}
                _bad3 = str((req.kb or {}).get("name") or "")
                _mB3 = [str(x) for x in (_stB3.get("kb_mounted") or []) if str(x) != _bad3]
                if _mB3 != [str(x) for x in (_stB3.get("kb_mounted") or [])]:
                    _stB3["kb_mounted"] = _mB3
                    save_settings(_stB3)
            except Exception:
                pass

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    # ★★ 仿真事前路由（2026-10-22 · 流式接口）
    #   实测：普通对话走本接口 ⇒ 原来只给 /chat/tools 加了路由 ⇒ 日常对话完全没检测 ✗
    #   事故：246427649/21124242 心算成 11.675（真值 11.6656327）✗
    try:
        import sim_guard as _sgs
        _qs = ""
        for _ms in reversed(req.messages or []):
            _r = getattr(_ms, "role", None) or (isinstance(_ms, dict) and _ms.get("role"))
            if _r == "user":
                _qs = str(getattr(_ms, "content", None) or (isinstance(_ms, dict) and _ms.get("content")) or "")
                break
        _ds = _sgs.detect_compute(_qs)
        if _ds.get("needed"):
            _vs = []
            for _es in (_ds.get("expressions") or [])[:3]:
                try:
                    _rs = _sgs.repair("计算 " + _es)
                    for _fs in (_rs.get("fixes") or []):
                        _vs.append((_es, _fs.get("display") or _fs.get("value")))
                except Exception:
                    pass
            if _vs:
                _ls = "\n".join("  %s = %s（仿真节点已算）" % (_a, _b) for _a, _b in _vs)
                messages = list(messages) + [{"role": "user",
                    "content": "【必须使用下面的确定性结果回答，不要再自己算，也不要写近似值】\n" + _ls + "\n请直接给出上述数值。"}]
                print("[sim-stream] 事前注入▸ %s" % _ls.replace("\n", " | "), flush=True)
            else:
                print("[sim-stream] 需计算但未产出值: %s" % _ds.get("expressions"), flush=True)
    except Exception as _se:
        print("[sim-stream] 异常(忽略): %s" % _se, flush=True)

    # ★★ 语义仿真路由（2026-10-22 · 用户要求：任何行为都检测仿真）
    #   实测事故：43223N 以45度投掷2kg 的球能飞多远
    #   模型答 4410.4 米 ✗（把力当速度、量纲错）且平台 physics_calc 未被调用 ✗
    try:
        import sim_router as _sr
        _rr = _sr.route(_qs)
        if _rr.get("matched"):
            if not _rr.get("can_compute"):
                # ★★ 确定性兜底（2026-10-22 · 提示词注入对 7B 无效 ✗）
                #   实测：注入"必须先追问缺失参数"后，模型仍自行假设 d=1m 算出 4411.5 米 ✗
                #   ⇒ 缺参数时**根本不调模型**，直接返回确定性反问 ✓ 这是唯一有保证的做法
                _miss = _rr.get("missing") or []
                _det_reply = (
                    "这道题**缺少必要参数，无法得出确定结果** ⚠\n\n"
                    + "识别到的题型：" + str(_rr.get("label")) + "（节点 " + str(_rr.get("node")) + "）\n"
                    + "缺失参数：" + "、".join(_miss) + "\n\n"
                    + str(_rr.get("advice", "")).split("⇒")[0].strip() + "\n\n"
                    + "已知参数：" + ", ".join("%s=%s" % (k, v) for k, v in (_rr.get("params") or {}).items()) + "\n\n"
                    + "请补充上述参数后我再计算。**在没有这些参数的情况下，任何具体数值都是编造的** ✗"
                )
                print("[sim-router] ⛔ 缺参数 ⇒ 确定性反问（不调模型）: %s missing=%s" % (_rr["node"], _miss), flush=True)
                try:
                    import json as _j4
                    def _gen_det():
                        for _ch in _det_reply:
                            yield "data: " + _j4.dumps({"delta": _ch}, ensure_ascii=False) + "\n\n"
                        yield "data: [DONE]\n\n"
                    from fastapi.responses import StreamingResponse as _SR4
                    return _SR4(_gen_det(), media_type="text/event-stream")
                except Exception as _e4:
                    print("[sim-router] 确定性返回失败: %s" % _e4, flush=True)
            else:
                _body = {"node": _rr["node"], "args": dict(_rr.get("params") or {})}
                if _rr.get("formula"):
                    _body["args"]["formula"] = _rr["formula"]
                _res = None
                try:
                    import json as _j3, urllib.request as _u3
                    _rq = _u3.Request("http://127.0.0.1:8001/sim/run", data=_j3.dumps(_body).encode(),
                                     headers={"Content-Type": "application/json"})
                    with _u3.urlopen(_rq, timeout=30) as _x3:
                        _res = _j3.loads(_x3.read().decode())
                except Exception as _e3:
                    print("[sim-router] 节点调用失败: %s" % _e3, flush=True)
                if _res and _res.get("ok"):
                    _lines3 = "  %s(%s) = %s" % (_rr["node"], _rr.get("formula") or "",
                                _j3.dumps(_res.get("result"), ensure_ascii=False))
                    messages = list(messages) + [{"role": "user", "content": (
                        "【仿真节点已计算（确定性结果，必须直接使用，禁止自己算）】\n"
                        + _lines3 + "\n请据此给出答案。")}]
                    print("[sim-router] 已注入节点结果▸ %s" % _lines3[:120], flush=True)
                else:
                    print("[sim-router] 参数齐全但节点未返回: %s" % _rr.get("node"), flush=True)
    except Exception as _sre:
        print("[sim-router] 异常(忽略): %s" % _sre, flush=True)
    messages = _inject_persona(messages, req)     # ★ 挂 LoRA 时自动给身份 ✓
    _cap_tokens_for_persona(req)                  # ★ L3: 聊天口吻的硬闸 ✓
    if kb_context:
        # ★ 实测: 原来的"【知识库参考】[片段 n] 来源: 文件名"会诱发【资料整理模式】✗
        #    → 模型给一大堆分点回答 ✓ 都对但不像聊天 ✓
        #   改成"你记得的事"+ 明确要求用自己话说 ✗ 去掉来源 ✗ 别把它当参考资料 ✓
        kb_block = ("\n\n以下是【你记得的事】（凭这些回答，但要用你自己的话讲，"
                    "不要照读，也不要说\"知识库\"\"资料\"\"片段\"这类词）：\n"
                    + "\n".join(f"- {str(it['text']).strip()}" for it in kb_context))
        found = False
        for m in messages:
            if m["role"] == "system":
                m["content"] += kb_block
                found = True
                break
        if not found:
            messages.insert(0, {"role": "system", "content": kb_block.strip()})

    # 上下文预算裁剪（超窗时 vLLM 会 400，这里提前处理并告知用户）
    # 统一走 chat_context（它会归一化 openai:model 这类 id，并查已知模型窗口表）。
    # 原来这里写死 "非 vllm 一律 8192"，导致云模型上系统提示+任务就超窗被截断。
    ctx_limit = int(chat_context(req.provider).get("max_model_len") or DEFAULT_CONTEXT_LIMIT)
    messages, _dropped, _used, _truncated = fit_messages_to_context(
        messages, ctx_limit, req.max_tokens
    )
    notices = build_context_notices(ctx_limit, _dropped, _truncated)

    if req.provider == "vllm" and req.lora:
        loaded = get_loaded_loras()
        if req.lora not in loaded:
            try:
                lora_path = str(get_lora_dir() / req.lora)
                compatible, msg, _ = check_lora_compatibility(req.lora, lora_path)
                if not compatible:
                    def err_gen():
                        yield f"data: {json_lib.dumps({'error': msg})}\n\n"
                    return StreamingResponse(err_gen(), media_type="text/event-stream")
                r = requests.post(f"{VLLM_BASE}/v1/load_lora_adapter",
                                  json={"lora_name": req.lora, "lora_path": lora_path},
                                  timeout=60)
                if r.status_code != 200:
                    def err_gen():
                        yield f"data: {json_lib.dumps({'error': f'LoRA 加载失败: {r.text[:200]}'})}\n\n"
                    return StreamingResponse(err_gen(), media_type="text/event-stream")
            except Exception as e:
                # ★ 坑（PEP 3110）: except 块结束时 Python 会【删除 e】✗，
                #   而 err_gen 是生成器、要到 StreamingResponse 消费时才执行 →
                #   那时 e 已不存在 → NameError（用户看不到错误信息，只看到崩）
                #   修: 先捕获到独立变量，再用默认参数绑进闭包 ✓
                _load_err = f'{type(e).__name__}: {e}'

                def err_gen(_msg=_load_err):
                    yield f"data: {json_lib.dumps({'error': f'LoRA 加载异常: {_msg}'})}\n\n"
                return StreamingResponse(err_gen(), media_type="text/event-stream")

    if kb_context:
        head = json_lib.dumps({"kb_context": kb_context}, ensure_ascii=False)
        def wrapped():
            for ev in notices:
                yield f"data: {json_lib.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {head}\n\n"
            _providers = load_settings().get("providers", {})
            if req.provider == "vllm":
                # ★★ 修 P0（2026-10-22 A组实测发现）：知识库分支**完全跳过学习增强** ✗✗
                #   原来这里直接 yield from _stream_vllm(...) —— 不读 req.learn ✗
                #   而默认 kb_mounted 有 3 个库并自动挂载
                #   ⇒ 默认配置下：学习增强 / 云端转手 / **敏感词防外传** 全部静默失效 ✗✗✗
                #   实测: 默认挂载 7/7 次无任何 learn 行为；kb_context="" 后立刻转手
                #   修: KB 分支也走 _learn_handoff ✓（等价于无 KB 时的路径）
                _gen_kb = _stream_vllm(messages, req)
                try:
                    _learn_kb = getattr(req, "learn", None) or {}
                    if _learn_kb.get("enabled"):
                        _gen_kb = _learn_handoff(_gen_kb, messages, req, _providers)
                        print("[stream] KB 分支已接入学习增强: enabled=%s force=%s"
                              % (True, bool(_learn_kb.get("force_handoff"))), flush=True)
                except Exception as _elk:
                    print("[stream] KB 分支接入学习增强失败(降级为纯本地): %s" % _elk, flush=True)
                yield from _gen_kb
            elif req.provider == "openai":
                yield from _stream_openai(messages, req, _providers.get("openai", {}))
            elif req.provider == "ollama":
                yield from _stream_ollama(messages, req, _providers.get("ollama", {}))
            else:
                yield f"data: {json_lib.dumps({'error': 'stream 暂不支持该 provider'})}\n\n"
        return StreamingResponse(wrapped(), media_type="text/event-stream")

    _providers = load_settings().get("providers", {})
    if req.provider == "vllm":
        learn = req.learn or {}
        # ★ A1（2026-10-15）: 显式强制接手
        #   为什么需要: agent 模式本地带 88 个工具 schema ⇒ 前端 10s 就超时 ✗，
        #   但前端重放走的是本端点（纯对话、无工具）⇒ 本地常常"秒答"✗
        #   ⇒ _learn_handoff 认为本地跑得好 ⇒ 云不接手 ⇒ 轨迹/入库全丢 ✗（实测踩过）
        #   所以由前端上报"本地已经失败"⇒ 这里直接交给接手模型 ✓ 并如实记原因 ✓
        if learn.get("enabled") and learn.get("force_handoff"):
            def _empty_local():
                if False:      # 生成器不能是空函数体，给个永不执行的 yield ✓
                    yield ""
            gen = _learn_handoff(_empty_local(), messages, req, _providers)
        else:
            gen = _stream_vllm(messages, req)
            if learn.get("enabled"):
                gen = _learn_handoff(gen, messages, req, _providers)
        return StreamingResponse(
            _prepend_events(gen, notices),
            media_type="text/event-stream"
        )
    elif req.provider == "openai":
        return StreamingResponse(
            _prepend_events(_stream_openai(messages, req, _providers.get("openai", {})), notices),
            media_type="text/event-stream"
        )
    elif req.provider == "ollama":
        return StreamingResponse(
            _prepend_events(_stream_ollama(messages, req, _providers.get("ollama", {})), notices),
            media_type="text/event-stream"
        )
    else:
        def err_gen():
            yield f"data: {json_lib.dumps({'error': 'stream 暂不支持该 provider'})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")


@app.post("/chat/completions")
def chat_completions(req: ChatRequest):
    kb_context = []
    if req.kb and req.kb.get("name"):
        try:
            query = req.kb.get("query") or (req.messages[-1].content[:200] if req.messages else "")
            # ★ 挂 LoRA（角色问答）时用【加主语 + 主语重排】的检索 ✓
            if getattr(req, "lora", None):
                kb_context = _kb_query_for_role(req.kb["name"], query, req.kb.get("top_k", 3))
            else:
                kb_context = kb.query_kb(req.kb["name"], query, req.kb.get("top_k", 3))
        except Exception as e:
            raise HTTPException(400, f"KB 检索失败: {e}")

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    # ★★ 仿真事前路由（2026-10-22 · 流式接口）
    #   实测：普通对话走本接口 ⇒ 原来只给 /chat/tools 加了路由 ⇒ 日常对话完全没检测 ✗
    #   事故：246427649/21124242 心算成 11.675（真值 11.6656327）✗
    try:
        import sim_guard as _sgs
        _qs = ""
        for _ms in reversed(req.messages or []):
            _r = getattr(_ms, "role", None) or (isinstance(_ms, dict) and _ms.get("role"))
            if _r == "user":
                _qs = str(getattr(_ms, "content", None) or (isinstance(_ms, dict) and _ms.get("content")) or "")
                break
        _ds = _sgs.detect_compute(_qs)
        if _ds.get("needed"):
            _vs = []
            for _es in (_ds.get("expressions") or [])[:3]:
                try:
                    _rs = _sgs.repair("计算 " + _es)
                    for _fs in (_rs.get("fixes") or []):
                        _vs.append((_es, _fs.get("display") or _fs.get("value")))
                except Exception:
                    pass
            if _vs:
                _ls = "\n".join("  %s = %s（仿真节点已算）" % (_a, _b) for _a, _b in _vs)
                messages = list(messages) + [{"role": "user",
                    "content": "【必须使用下面的确定性结果回答，不要再自己算，也不要写近似值】\n" + _ls + "\n请直接给出上述数值。"}]
                print("[sim-stream] 事前注入▸ %s" % _ls.replace("\n", " | "), flush=True)
            else:
                print("[sim-stream] 需计算但未产出值: %s" % _ds.get("expressions"), flush=True)
    except Exception as _se:
        print("[sim-stream] 异常(忽略): %s" % _se, flush=True)

    messages = _inject_persona(messages, req)     # ★ 挂 LoRA 时自动给身份 ✓
    _cap_tokens_for_persona(req)                  # ★ L3: 聊天口吻的硬闸 ✓
    if kb_context:
        # ★ 实测: 原来的"【知识库参考】[片段 n] 来源: 文件名"会诱发【资料整理模式】✗
        #    → 模型给一大堆分点回答 ✓ 都对但不像聊天 ✓
        #   改成"你记得的事"+ 明确要求用自己话说 ✗ 去掉来源 ✗ 别把它当参考资料 ✓
        kb_block = ("\n\n以下是【你记得的事】（凭这些回答，但要用你自己的话讲，"
                    "不要照读，也不要说\"知识库\"\"资料\"\"片段\"这类词）：\n"
                    + "\n".join(f"- {str(it['text']).strip()}" for it in kb_context))
        found = False
        for m in messages:
            if m["role"] == "system":
                m["content"] += kb_block
                found = True
                break
        if not found:
            messages.insert(0, {"role": "system", "content": kb_block.strip()})

    # 上下文预算裁剪（与流式端点一致）
    # 统一走 chat_context（它会归一化 openai:model 这类 id，并查已知模型窗口表）。
    # 原来这里写死 "非 vllm 一律 8192"，导致云模型上系统提示+任务就超窗被截断。
    ctx_limit = int(chat_context(req.provider).get("max_model_len") or DEFAULT_CONTEXT_LIMIT)
    messages, _dropped, _used, _truncated = fit_messages_to_context(
        messages, ctx_limit, req.max_tokens
    )
    ctx_notices = build_context_notices(ctx_limit, _dropped, _truncated)

    if req.provider == "vllm" and req.lora:
        loaded = get_loaded_loras()
        if req.lora not in loaded:
            try:
                lora_path = str(get_lora_dir() / req.lora)
                compatible, msg, _ = check_lora_compatibility(req.lora, lora_path)
                if not compatible:
                    raise HTTPException(400, msg)
                r = requests.post(f"{VLLM_BASE}/v1/load_lora_adapter",
                                  json={"lora_name": req.lora, "lora_path": lora_path},
                                  timeout=60)
                if r.status_code != 200:
                    raise HTTPException(500, f"LoRA 加载失败: {r.text[:200]}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(500, f"LoRA 加载异常: {e}")

    settings = load_settings()
    providers = settings.get("providers", {})
    try:
        if req.provider == "vllm":
            result = _chat_call_vllm(req, messages)
        elif req.provider == "openai":
            result = _chat_call_openai(req, messages, providers.get("openai", {}))
        elif req.provider == "ollama":
            result = _chat_call_ollama(req, messages, providers.get("ollama", {}))
        else:
            raise HTTPException(400, f"未知 provider: {req.provider}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"LLM 调用失败: {e}")

    return {
        "content": result["content"],
        "usage": result.get("usage", {}),
        "kb_context": kb_context,
        "provider": req.provider,
        "model": result.get("model"),
        "_context": {
            "max_model_len": ctx_limit,
            "used_tokens_estimate": _used,
            "notices": [n["notice"] for n in ctx_notices],
        },
    }

# ============ 知识库 ============
class KBCreateRequest(BaseModel):
    name: str
    description: str = ""
    # P2.2: 动态知识库（会话级临时库）
    dynamic: bool = False
    owner_session: str | None = None
    ttl_hours: int | None = None


class KBQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float | None = None   # None → kb.DEFAULT_MIN_SCORE；低于它的候选会被丢弃


class KBIngestFolderRequest(BaseModel):
    folder_path: str
    extensions: list | None = None


@app.get("/kb/list")
def kb_list():
    return {"knowledge_bases": kb.list_kbs()}


@app.get("/kb/mounted")
def kb_mounted_list():
    """查看当前**挂载了哪些知识库** ✓（多库 ✓）"""
    try:
        st = load_settings()
        mounted = [str(x) for x in (st.get("kb_mounted") or [])]
    except Exception:
        mounted = []
    return {"ok": True, "mounted": mounted, "count": len(mounted)}


class KBMountRequest(BaseModel):
    kb_names: list[str]
    session_id: str | None = None   # P2.1: 不传=全局默认（旧行为）


@app.get("/kb/{kb_name}")
def kb_get(kb_name: str):
    meta = kb.get_kb(kb_name)
    if not meta:
        raise HTTPException(404, f"知识库 '{kb_name}' 不存在")
    return meta


@app.post("/kb/create")
def kb_create(req: KBCreateRequest):
    # ★ 修 500（2026-10-22）: 超长库名会触发 OSError 500
    try:
        _nmC = str(getattr(req, "name", "") or (req.get("name") if isinstance(req, dict) else "") or "")
    except Exception:
        _nmC = ""
    if len(_nmC) > 64:
        raise HTTPException(400, "KB 名不能超过 64 字符（收到 %d）" % len(_nmC))
    if not _nmC.strip():
        raise HTTPException(400, "KB 名不能为空")
    if not req.name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "知识库名称只能包含字母、数字、下划线、中划线")
    try:
        meta = kb.create_kb(req.name, req.description,
                           dynamic=bool(getattr(req, "dynamic", False)),
                           owner_session=getattr(req, "owner_session", None),
                           ttl_hours=getattr(req, "ttl_hours", None))
        try:
            kb._append_audit(req.name, "create",
                             dynamic=bool(getattr(req, "dynamic", False)),
                             session=getattr(req, "owner_session", None))
        except Exception:
            pass
        return {"status": "success", "knowledge_base": meta}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/kb/{kb_name}")
def kb_delete(kb_name: str):
    try:
        kb.delete_kb(kb_name)
        return {"status": "success", "message": f"已删除 {kb_name}"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/kb/{kb_name}/upload_zip")
async def kb_upload_zip(kb_name: str, file: UploadFile = File(...)):
    """上传 zip，内含多个 pdf/txt/md，批量入库"""
    # ★★ 修（2026-10-22 用户实测发现的逆天 bug）
    #   删除知识库时**没有从 settings.kb_mounted 里移除** ⇒ 留下悬空挂载
    #   ⇒ 之后每次对话都检索一个不存在的库 ⇒ ValueError ⇒ **整轮对话崩掉** ✗✗
    #   修: 删除成功后同步清理挂载列表 ✓
    _del_name = str(kb_name)
    try:
        _stD = load_settings() or {}
        _mD = [str(x) for x in (_stD.get("kb_mounted") or []) if str(x).strip()]
        if _del_name in _mD:
            _mD = [x for x in _mD if x != _del_name]
            _stD["kb_mounted"] = _mD
            save_settings(_stD)
            print("[kb/delete] 已从挂载列表移除: %s（剩 %s）" % (_del_name, _mD), flush=True)
    except Exception as _eD:
        print("[kb/delete] 清理挂载列表失败(忽略): %s" % _eD, flush=True)
    if not kb.get_kb(kb_name):
        raise HTTPException(404, f"知识库 '{kb_name}' 不存在")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "只支持 .zip 文件")

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "kb.zip")
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "不是有效的 ZIP 压缩包")

        # 收集所有支持的文件
        exts = {".pdf", ".txt", ".md", ".text"}
        files = []
        for root, _, names in os.walk(extract_dir):
            for n in names:
                fp = os.path.join(root, n)
                suffix = Path(n).suffix.lower()
                # .docx 先转成同名 .txt 再加入 → kb.py 一行都不用改
                if n.lower().endswith(".docx"):
                    try:
                        txt = extract_text_from_docx(fp)
                        if txt and txt.strip():
                            outp = os.path.splitext(fp)[0] + ".txt"
                            with open(outp, "w", encoding="utf-8") as f:
                                f.write(txt)
                            files.append(outp)
                    except Exception as e:
                        print(f"[kb] {n} 转文本失败（跳过）: {e}", flush=True)
                    continue
                if suffix in exts:
                    files.append(fp)

        if not files:
            raise HTTPException(400, "压缩包内未找到任何 .pdf/.txt/.md/.docx 文件")

        try:
            result = kb.add_documents_to_kb(kb_name, files)
            return {"status": "success", **result}
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"入库失败: {e}")


@app.post("/kb/{kb_name}/ingest_folder")
def kb_ingest_folder(kb_name: str, req: KBIngestFolderRequest):
    """从服务器本地文件夹批量导入（如 /mnt/e/papers/）"""
    if not kb.get_kb(kb_name):
        raise HTTPException(404, f"知识库 '{kb_name}' 不存在")
    try:
        result = kb.ingest_folder(kb_name, req.folder_path, req.extensions)
        return {"status": "success", **result}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"导入失败: {e}")


# ==================== 向量模型自选（知识库界面用 ✓）====================
EMBED_PRESETS = [
    {"id": "bge-m3", "label": "bge-m3（多语 · 1024 维 · 通用强）", "path": "bge-m3"},
    {"id": "bge-small-zh-v1.5", "label": "bge-small-zh-v1.5（中文 · 512 维 · 快）",
     "path": "bge-small-zh-v1.5"},
]
_EMBED_DIRS = ("config.json",)


# 能当向量模型用的架构（★ 太宽松会把 Qwen 这类 LLM 也列出来 ✗ 实测踩过 ✓）
_EMBED_ARCHS = ("bert", "roberta", "xlm", "mpnet", "gte", "bge", "e5", "nomic",
                "sentence", "embedding", "minilm", "distilbert", "jina", "qwen3embedding")


def _is_embedding_dir(p):
    """目录是不是一个【向量】模型（不是任意 HF 模型 ✓）。

    ★ 只判 config.json + 权重是不够的 ✗ —— 任何 LLM 目录都满足 ✓
      实测: 列出模型时把 Qwen2.5-0.5B-Instruct 也当成了向量模型 ✗
    现在再看 config.json 的 architectures / model_type 是否像 embedding ✓
    """
    try:
        p = Path(p)
        if not p.is_dir() or not (p / "config.json").exists():
            return False
        if not any((p / w).exists() for w in
                   ("model.safetensors", "pytorch_model.bin", "model.onnx",
                    "openvino_model.bin", "onnx/model.onnx")):
            return False
        try:
            cfg = json_lib.loads((p / "config.json").read_text(encoding="utf-8"))
        except Exception:
            return False
        archs = " ".join([str(x) for x in (cfg.get("architectures") or [])])
        blob = (archs + " " + str(cfg.get("model_type") or "") + " " + p.name).lower()
        # 明确是因果语言模型（LLM）→ 排除 ✗
        if any(t in blob for t in ("causallm", "forcausal", "qwen2", "qwen3", "llama",
                                   "chatglm", "gemma", "mistral", "deepseek")):
            return False
        return any(t in blob for t in _EMBED_ARCHS)
    except Exception:
        return False


@app.get("/settings/embedding")
def get_embedding_settings():
    """K0.5（2026-10-20）: 读 embedding 模型/设备配置（UI 用 ✓）

    ★ 返回可用模型 + 可用设备（CPU / 每个 GPU ✓）
    """
    try:
        import gpu_config as _gcfg
        _st = load_settings() or {}
        _dev = str(_st.get("embedding_device") or "auto").strip().lower()
        # 设备列表 ✓
        devices = [{"value": "auto", "label": "自动（默认 ✓）"},
                   {"value": "cpu", "label": "CPU（不占显存 ✓ 慢 ⚠）"}]
        try:
            for g in _gcfg.detect_gpus():
                devices.append({
                    "value": "cuda:%d" % g["index"],
                    "label": "GPU %d · %s（%.1f GB）" % (
                        g["index"], str(g.get("name", "")).replace("NVIDIA GeForce ", ""),
                        (g.get("mem_total") or 0) / 1024),
                })
        except Exception:
            pass
        # 模型列表 ✓（复用现有 /embedding-models 逻辑 ✓）
        models = []
        try:
            _r = kb_embedding_models()
            models = _r.get("models") or _r.get("items") or []
        except Exception:
            pass
        return {
            "ok": True,
            "device": _dev,
            "devices": devices,
            "models": models,
            "model_env": _st.get("embedding_model") or "",
            "note": "改设备后下次入库/检索时生效 ✓（已加载的会重新加载 ✓）",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "devices": [], "models": []}


@app.post("/settings/embedding")
def set_embedding_settings(payload: dict = None):
    """K0.5（2026-10-20）: 写 embedding 设备/模型配置 ✓

    body: {"device": "auto|cpu|cuda:0|cuda:1", "model": "可选（全局默认模型名 ✓）"}
    """
    payload = payload or {}
    try:
        import json as _json
        _st = load_settings() or {}
        changed = []
        dev = payload.get("device")
        if dev is not None:
            dv = str(dev).strip().lower()
            # 校验 ✓
            allowed = {"auto", "cpu"}
            try:
                import gpu_config as _gcfg
                for g in _gcfg.detect_gpus():
                    allowed.add("cuda:%d" % g["index"])
            except Exception:
                pass
            if dv not in allowed:
                return {"ok": False, "error": "device 必须是 %s 之一" % sorted(allowed)}
            _st["embedding_device"] = dv
            changed.append("device=%s" % dv)
        mdl = payload.get("model")
        if mdl is not None:
            _st["embedding_model"] = str(mdl).strip()
            changed.append("model=%s" % _st["embedding_model"])
        # 写盘 ✓
        _sp = PROJECT_DIR / "settings.json"
        _sp.write_text(_json.dumps(_st, ensure_ascii=False, indent=2), encoding="utf-8")
        # ★ 清 embedding 缓存 ✓ 让新设备/模型立即生效 ✓
        try:
            import kb as _kbmod
            _kbmod._EMBEDDER_CACHE.clear()
        except Exception:
            pass
        return {
            "ok": True,
            "changed": changed,
            "device": _st.get("embedding_device", "auto"),
            "model": _st.get("embedding_model", ""),
            "note": "已保存 ✓ 下次入库/检索时生效 ✓（缓存已清 ✓）",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/embedding-models")
def kb_embedding_models():
    """可用向量模型: 预设 ✓ + ~/models 里探测到的 ✓ + 各库正在用的 ✓。

    ★ 说明: 支持【外部导入】—— 把模型目录放进 ~/models/ 就会出现在这里，
      也可以在选择时直接填任意绝对路径 ✓
    """
    import kb as _kb
    out, seen = [], set()

    def push(mid, label, path, source):
        key = str(mid)
        if key in seen:
            return
        seen.add(key)
        dim = None
        try:
            m = _kb._get_embedder(_kb._resolve_embed_model(mid))
            dim = int(m.get_sentence_embedding_dimension())
        except Exception:
            pass
        out.append({"id": mid, "label": label, "path": path, "source": source, "dim": dim})

    for it in EMBED_PRESETS:
        push(it["path"], it["label"], it["path"], "preset")
    # ~/models 下所有像 embedding 的目录
    try:
        for d in sorted((Path.home() / "models").iterdir()):
            if _is_embedding_dir(d):
                push(d.name, d.name + "（本机）", str(d), "local")
    except Exception:
        pass
    # 各库正在用的（万一不在上面 ✓ 也要能选回去）
    try:
        for kd in sorted((PROJECT_DIR / "data" / "kbs").iterdir()):
            mj = kd / "meta.json"
            if not mj.exists():
                continue
            meta = json_lib.loads(mj.read_text(encoding="utf-8"))
            mid = (meta.get("embedding") or {}).get("model")
            if mid:
                push(mid, mid + "（某库在用）", mid, "inuse")
    except Exception:
        pass
    return {"models": out}


# ══════════════════════════════════════════════════════════
# ★ K1（2026-10-20）: KB 数据检测/清洗/预览/评测（全部非破坏性 ✓ 不动原库 ✓）
#   依据: docs/KB_EVAL_PLAN.md（K-1~K-8 缺口）+ docs/KB_LOCAL_MODEL_FEASIBILITY.md（T1-T4 实测 ✓）
# ══════════════════════════════════════════════════════════
def _kb_docs_paths(kb_name: str) -> list:
    """列出该 KB 已保存的文档路径 ✓（P-56 保存机制 ✓）"""
    import pathlib as _pl
    try:
        import kb as _kb
        d = _pl.Path(_kb._kb_docs_dir(kb_name))
        if d.exists():
            return [p for p in d.iterdir() if p.is_file()]
    except Exception:
        pass
    return []


def _kb_llm(system: str, user: str, max_tokens: int = 300) -> str:
    """调本地 vLLM ✓（K1 评测/增强用 ✓）"""
    try:
        import requests as _rq
        r = _rq.get("http://127.0.0.1:8000/v1/models", timeout=5)
        mid = ((r.json().get("data") or [{}])[0].get("id")) or "default"
        resp = _rq.post("http://127.0.0.1:8000/v1/chat/completions", json={
            "model": mid,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.1,
        }, timeout=120)
        if resp.status_code != 200:
            return ""
        return (((resp.json().get("choices") or [{}])[0].get("message", {}) or {})
                .get("content", "") or "").strip()
    except Exception:
        return ""


@app.post("/kb/{kb_name}/analyze")
async def kb_analyze(kb_name: str, files: list[UploadFile] = File(default=None),
                     payload: dict = None):
    """K1: 入库前检测 ✓（只读 ✓ 不写任何库 ✓）

    两种用法:
      ① 上传文件: multipart files=[...] → 存到临时目录 → 检测 ✓
      ② 检测已有文档: 无 files ⇒ 检测该 KB 已保存的文档 ✓
    """
    try:
        import kb_toolkit as _kt
        import tempfile as _tmp
        import os as _os
        paths = []
        cleanup = []
        if files:
            td = _tmp.mkdtemp(prefix="kb_an_")
            for f in files:
                if not f.filename:
                    continue
                fp = pathlib.Path(td) / pathlib.Path(f.filename).name
                data = await f.read()
                if len(data) > 50 * 1024 * 1024:
                    return {"ok": False, "error": "单文件超过 50MB: %s" % f.filename}
                fp.write_bytes(data)
                paths.append(fp)
                cleanup.append(fp)
        else:
            paths = _kb_docs_paths(kb_name)
            if not paths:
                return {"ok": False, "error": "该库没有已保存文档（P-56 保存机制未启用？）⇒ 请上传文件检测"}

        rep = _kt.analyze_docs(paths)
        rep["ok"] = True
        rep["kb"] = kb_name
        rep["mode"] = "upload" if files else "existing_docs"
        # 清理临时文件 ✓
        for fp in cleanup:
            try:
                _os.unlink(fp)
            except Exception:
                pass
        return rep
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/kb/{kb_name}/clean")
def kb_clean(kb_name: str, payload: dict = None):
    """K1: 清洗 → 建【新库】{kb_name}_clean ✓（**绝不动原库** ✓）

    body: {"opts": {"chunk_size":500,"overlap":50,"dedup_exact":true,...},
           "new_name": "可选"}
    """
    # ★ 修（2026-10-22 v4.2 · KB 不存在 fsclean）:
    #   对不存在的库原返回 200 并在 data/kbs 留脏目录，而且 DELETE 也删不掉 ✗
    #   （实测: a2_no_such_res_zzz 这类脏目录会污染库列表与挂载校验）
    #   修法: 先在文件系统上判断目录是否存在，不存在直接 404，不建任何目录 ✓
    try:
        # 注意: 目录存在不代表库有效（历史脏目录会占位）⇒ 用 meta.json 判断 ✓
        if not (PROJECT_DIR / "data" / "kbs" / str(kb_name) / "meta.json").exists():
            raise HTTPException(404, "知识库 %s 不存在" % kb_name)
    except HTTPException:
        raise
    except Exception:
        pass
    # ★ 修（2026-10-22）: 对不存在的库原返回 200 并在 data/kbs 留脏目录
    #   （而且 DELETE 也删不掉 ⇒ 反复触发会污染库列表与挂载校验）✗
    #   修法: 先判断库是否存在，不存在直接 404，不建任何目录 ✓
    try:
        import kb as _kbC
        _lC = _kbC.list_kbs() if hasattr(_kbC, "list_kbs") else []
        _namesC = set()
        for _x in (_lC or []):
            _namesC.add(_x.get("name") if isinstance(_x, dict) else str(_x))
        if _namesC and kb_name not in _namesC:
            raise HTTPException(404, "知识库 %s 不存在" % kb_name)
    except HTTPException:
        raise
    except Exception:
        pass
    try:
        import kb_toolkit as _kt
        payload = payload or {}
        # 源文档 ✓
        paths = _kb_docs_paths(kb_name)
        if not paths:
            return {"ok": False, "error": "该库没有已保存文档 ⇒ 无法清洗（P-56 机制未启用）"}
        # 新库名 ✓
        new_name = str(payload.get("new_name") or "").strip() or ("%s_clean" % kb_name)
        if not re.match(r'^[A-Za-z0-9_\-\u4e00-\u9fff]{1,40}$', new_name):
            return {"ok": False, "error": "新库名非法（只允许字母数字下划线中划线中文，≤40）"}
        # 清洗 ✓
        opts = payload.get("opts") or {}
        if not isinstance(opts, dict):
            opts = {}
        chunks, stats = _kt.clean_docs(paths, opts)
        if not chunks:
            return {"ok": False, "error": "清洗后 0 块 ⇒ 检查源文档", "stats": stats}
        # 建新库 ✓
        import kb as _kb
        res = _kb.create_kb_from_chunks(
            new_name, chunks,
            description="从 %s 清洗生成（%d 块 ✓ 结构感知 ✓）" % (kb_name, len(chunks)),
            model_ref=payload.get("model_ref"),
        )
        return {
            "ok": True,
            "from_kb": kb_name,
            "new_kb": new_name,
            "stats": stats,
            "created": res,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/kb/{kb_name}/chunks/preview")
def kb_chunks_preview(kb_name: str, limit: int = 10, offset: int = 0):
    """K1: 预览前 N 块 ✓（从 chroma 读 ✓）"""
    try:
        import kb as _kb
        meta = _kb.get_kb(kb_name)
        if not meta:
            return {"ok": False, "error": "知识库不存在"}
        client = _kb._get_chroma()
        try:
            col = client.get_collection(name="kb_%s" % kb_name)
        except Exception as e:
            return {"ok": False, "error": "索引不存在: %s" % e}
        limit = max(1, min(int(limit or 10), 50))
        offset = max(0, int(offset or 0))
        r = col.get(limit=limit, offset=offset, include=["documents", "metadatas"])
        docs = r.get("documents") or []
        metas = r.get("metadatas") or []
        items = []
        for i, (d, m) in enumerate(zip(docs, metas)):
            items.append({
                "i": offset + i,
                "text": (d or "")[:500],
                "chars": len(d or ""),
                "source": (m or {}).get("source", ""),
                "section": (m or {}).get("section", ""),
            })
        return {"ok": True, "kb": kb_name, "total": col.count(),
                "offset": offset, "limit": limit, "items": items}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/kb/{kb_name}/eval")
def kb_eval(kb_name: str, payload: dict = None):
    """K1: 检索评测 ✓（Recall@k / MRR / nDCG@k ✓ 本地模型出题 ✓）

    body: {"n_questions": 20, "top_k": 5}
    """
    try:
        import kb as _kb
        import hashlib as _hl
        payload = payload or {}
        n_q = max(3, min(int(payload.get("n_questions") or 20), 50))
        top_k = max(1, min(int(payload.get("top_k") or 5), 20))
        meta = _kb.get_kb(kb_name)
        if not meta:
            return {"ok": False, "error": "知识库不存在"}
        client = _kb._get_chroma()
        try:
            col = client.get_collection(name="kb_%s" % kb_name)
        except Exception as e:
            return {"ok": False, "error": "索引不存在: %s" % e}
        total = col.count()
        if total < 5:
            return {"ok": False, "error": "块太少（%d < 5）⇒ 无法评测" % total}
        # ① 抽样块 ✓
        import random as _rnd
        allr = col.get(include=["documents", "metadatas"])
        docs = allr.get("documents") or []
        metas = allr.get("metadatas") or []
        idxs = list(range(len(docs)))
        _rnd.Random(42).shuffle(idxs)
        idxs = idxs[:n_q]
        # ② 出题 ✓（本地模型 T4 ✓）
        Q_SYS = ("你是检索评测集生成器。为给定文本块生成 1 个【只有这段才能回答】的具体问题。"
                 "只输出问题本身，不要解释、不要编号。")
        questions = []
        for i in idxs:
            q = _kb_llm(Q_SYS, (docs[i] or "")[:600], max_tokens=80)
            if q and len(q) < 120:
                questions.append({
                    "q": q.strip(),
                    "chunk_hash": _hl.md5((docs[i] or "").encode("utf-8")).hexdigest(),
                    "source": (metas[i] or {}).get("source", ""),
                })
        if not questions:
            return {"ok": False, "error": "出题失败（本地模型不可用？）"}
        # ③ 检索 + 算指标 ✓
        hits = 0
        rr_sum = 0.0
        ndcg_sum = 0.0
        details = []
        for item in questions:
            res, diag = _kb.query_kb_detailed(kb_name, item["q"], top_k=top_k, min_score=-1.0)
            rank = 0
            for ri, r in enumerate(res or []):
                rh = _hl.md5((r.get("text") or r.get("document") or "").encode("utf-8")).hexdigest()
                if rh == item["chunk_hash"]:
                    rank = ri + 1
                    break
            if rank:
                hits += 1
                rr_sum += 1.0 / rank
                ndcg_sum += 1.0 / (1 if rank == 1 else (2 if rank == 2 else (3 if rank == 3 else (4 if rank == 4 else 5))))
            details.append({"q": item["q"], "rank": rank, "source": item["source"]})
        n = len(questions)
        return {
            "ok": True,
            "kb": kb_name,
            "n_questions": n,
            "top_k": top_k,
            "total_chunks": total,
            "recall@k": round(hits / n, 4),
            "mrr": round(rr_sum / n, 4),
            "ndcg@k(近似)": round(ndcg_sum / n, 4),
            "hit@1": round(sum(1 for d in details if d["rank"] == 1) / n, 4),
            "details": details[:20],
            "note": "n<30 时只报差不下显著结论（EVAL_PROTOCOL ✓）",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/kb/{kb_name}/embedding")
def kb_get_embedding(kb_name: str):
    """该库当前用的向量模型与维度（界面显示用 ✓）。"""
    import kb as _kb
    meta = _kb.get_kb(kb_name)
    if not meta:
        raise HTTPException(404, f"知识库不存在: {kb_name}")
    sig = meta.get("embedding") or {}
    return {"kb": kb_name, "model": sig.get("model"), "dim": sig.get("dim"),
            "total_chunks": meta.get("total_chunks"),
            "active": _kb.embed_model_name()}


@app.put("/kb/{kb_name}/embedding")
def kb_set_embedding(kb_name: str, payload: dict = None):
    """给某个库【换向量模型】并【自动重建索引】（维度随之调整 ✓）。

    payload: {"model": "bge-m3" | "bge-small-zh-v1.5" | "/abs/path/to/model",
              "rebuild": true（默认 true ✓ 不重建则新旧维度会不一致 ✓）}
    """
    import kb as _kb
    payload = payload or {}
    want = str(payload.get("model") or "").strip()
    if not want:
        raise HTTPException(400, "缺少 model（可填预设名或任意本地模型目录）")
    meta = _kb.get_kb(kb_name)
    if not meta:
        raise HTTPException(404, f"知识库不存在: {kb_name}")

    # ★ 解析成本地路径 ✗ 否则短名会触发联网下载（实测会卡住 ✓）
    resolved = _kb._resolve_embed_model(want)
    if not _is_embedding_dir(resolved):
        raise HTTPException(400, (
            f"找不到可用的向量模型: {want}（解析为 {resolved}）。"
            f"可以填预设名，或把模型目录放到 ~/models/ 下，或直接给绝对路径。"))
    # 探测维度（顺带把模型加载进缓存 ✓）
    try:
        m = _kb._get_embedder(resolved)
        dim = int(m.get_sentence_embedding_dimension())
    except Exception as e:
        raise HTTPException(500, f"加载向量模型失败: {type(e).__name__}: {e}")

    do_rebuild = payload.get("rebuild", True) is not False
    old = (meta.get("embedding") or {}).get("model")
    if do_rebuild:
        chunks_now = int(meta.get("total_chunks") or 0)
        if chunks_now <= 0:
            # ★ 空库没有向量可重建 ✗（实测会抛"索引里没有任何向量，无从重建"✓）
            #   → 只把记录改成新模型 ✓ 之后入库就按新模型走 ✓
            try:
                meta["embedding"] = {"model": resolved, "dim": dim}
                _kb._save_kb_meta(kb_name, meta)
            except Exception:
                pass
            return {"status": "success", "kb": kb_name, "model": resolved, "dim": dim,
                    "old_model": old, "chunks": 0,
                    "note": "空库：已改记录（无数据可重建 ✓）；之后入库会用新模型"}
        try:
            res = _kb.rebuild_kb_index(kb_name, model_ref=resolved)
        except Exception as e:
            raise HTTPException(500, f"重建索引失败: {type(e).__name__}: {e}")
        return {"status": "success", "kb": kb_name, "model": resolved, "dim": dim,
                "old_model": old, "chunks": (res or {}).get("chunks"),
                "note": "已换模型并重建索引（维度随新模型调整 ✓）"}
    return {"status": "success", "kb": kb_name, "model": resolved, "dim": dim,
            "old_model": old, "note": "只改了记录未重建 ✗ 新旧维度若不同，检索会报错"}


@app.post("/kb/{kb_name}/rebuild")
def kb_rebuild(kb_name: str):
    """
    用**当前** embedding 模型原地重建该库的索引（无需原始文件）。

    换 embedding 模型（环境变量 KB_EMBED_MODEL）之后必须做这一步：旧向量与新模型不在
    同一向量空间，一致性闸门会拒绝查询。重建先把新向量算好写进临时集合，成功才切换，
    失败时旧索引原封不动。
    注意：会真实调用 embedding 模型，大库需要等待（同步执行）。
    """
    try:
        info = kb.rebuild_kb_index(kb_name)
        return {"status": "success", **info}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"重建失败: {e}")


# ══════════════════════════════════════════════════════════
# ★ 多知识库挂载（2026-10-21）
#
# 需求: **可以同时挂载多个知识库** ✓
# 为什么: 一个任务常常横跨多个库 ✗（如"对比清洗前后"要查 ab_clean + ab_raw ✓）
#        原来只能查一个 ✗ ⇒ 得来回切 ✗
#
# 设计:
#   · 一次查多个库 ✓ 每个库各自 top_k ✓
#   · **合并 + 跨库统一排序** ✓（按 score 降序 ✓）
#   · **保留来源标记** ✓（哪条来自哪个库 ✓ 可追溯 ✓）
#   · 单库失败**不影响其他库** ✓（容错 ✓）
#   · 去重 ✓（同样内容从多库来 ⇒ 保留高分那份 ✓）
# ══════════════════════════════════════════════════════════
class KBMultiQueryRequest(BaseModel):
    kb_names: list[str] | None = None    # 不传 ⇒ 用 settings 里挂载的 ✓
    query: str
    top_k: int = 3
    min_score: float | None = None
    merge: bool = True                   # True ⇒ 合并排序 ✓ False ⇒ 分库返回 ✓


@app.post("/kb/multi_query")
def kb_multi_query(req: KBMultiQueryRequest):
    """★ 多知识库同时查询 ✓

    示例:
      POST /kb/multi_query
      {"kb_names": ["ab_clean_20261021", "ab_raw_20261021"],
       "query": "清洗规则", "top_k": 3}
    """
    names = [str(x) for x in (req.kb_names or []) if str(x).strip()]
    if not names:
        # 用 settings 里配置的多库挂载 ✓
        try:
            _st = load_settings()
            names = [str(x) for x in (_st.get("kb_mounted") or []) if str(x).strip()]
        except Exception:
            names = []
    if not names:
        return {"ok": False, "error": "未指定 kb_names 且 settings 无 kb_mounted ⚠",
                "hint": "传 kb_names 列表 ✓ 或用 PUT /kb/mount 挂载多个库 ✓"}

    per_kb = {}
    errors = {}
    merged = []

    for nm in names:
        try:
            results, diag = kb.query_kb_detailed(nm, req.query, int(req.top_k),
                                                 min_score=req.min_score)
            per_kb[nm] = {"count": len(results or []), "diag": diag,
                          "results": results or []}
            for r in (results or []):
                rr = dict(r) if isinstance(r, dict) else {"content": str(r)}
                rr["_kb"] = nm            # ★ 来源标记 ✓
                merged.append(rr)
        except Exception as e:
            errors[nm] = f"{type(e).__name__}: {e}"
            per_kb[nm] = {"count": 0, "error": errors[nm]}

    # 合并排序 + 去重 ✓
    if req.merge:
        # 按 score 降序 ✓（无 score 放最后 ✓）
        merged.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        # 去重 ✓（同 content 的保留第一份=最高分 ✓）
        seen = set()
        deduped = []
        for r in merged:
            key = str(r.get("content") or r.get("text") or "")[:200].strip()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(r)
        merged = deduped

    return {
        "ok": True,
        "query": req.query,
        "kb_names": names,
        "kb_count": len(names),
        "total": len(merged),
        "per_kb": {k: {"count": v.get("count", 0), "error": v.get("error")}
                   for k, v in per_kb.items()},
        "results": merged,
        "errors": errors,
        "merged": bool(req.merge),
    }



@app.put("/kb/mount")
def kb_mount(req: KBMountRequest):
    """★ **挂载多个知识库** ✓（替换式 ✓）

    为什么用 settings: 挂载状态要**持久化** ✓ 重启后还在 ✓
    """
    # ★ 修（2026-10-22）: 挂载前过滤掉不存在的库（防悬空挂载）
    try:
        import kb as _kbM
        _allM = _kbM.list_kbs() if hasattr(_kbM, "list_kbs") else []
        _existM = set()
        for _xk in (_allM or []):
            _existM.add(_xk.get("name") if isinstance(_xk, dict) else str(_xk))
        if _existM:
            _pM = payload if isinstance(payload, dict) else {}
            _wantM = [str(x) for x in (_pM.get("kb_names") or [])]
            _okM = [x for x in _wantM if x in _existM]
            _badM = [x for x in _wantM if x not in _existM]
            if _badM:
                print("[kb/mount] 忽略不存在的库: %s" % _badM, flush=True)
                if isinstance(payload, dict):
                    payload = dict(payload)
                    payload["kb_names"] = _okM
    except Exception as _eM:
        print("[kb/mount] 校验失败(忽略): %s" % _eM, flush=True)
    names = [str(x).strip() for x in (req.kb_names or []) if str(x).strip()]
    # 校验存在性 ✓
    valid, invalid = [], []
    try:
        existing = {d.name for d in (PROJECT_DIR / "data" / "kbs").iterdir() if d.is_dir()}
    except Exception:
        existing = set()
    for nm in names:
        if nm in existing:
            valid.append(nm)
        else:
            invalid.append(nm)

    st = load_settings()
    _sid = str(getattr(req, "session_id", "") or "").strip()
    if _sid:
        _mounts = dict(st.get("kb_mounts") or {})
        _mounts[_sid] = valid
        st["kb_mounts"] = _mounts
        # ★ 修（2026-10-26 实测）：会话挂载**不得**改全局默认 ✗
        #   原来同时写 st["kb_mounted"]=valid ⇒ 会话 A 挂库，会话 B 也看到了（隔离被击穿 ✗）
        _note = "已保存为会话级挂载 settings.kb_mounts[%s] ✓（全局默认不变，其他会话不受影响 ✓）" % _sid
    else:
        st["kb_mounted"] = valid
        _note = "挂载已持久化到 settings.kb_mounted ✓ 多库同时可用 ✓"
    save_settings(st)
    return {"ok": True, "mounted": valid, "invalid": invalid,
            "count": len(valid), "session_id": _sid or None,
            "note": _note}


@app.get("/kb/mounted")
def kb_mounted_list(session_id: str = ""):
    """P2.1: 查看挂载（传 session_id 看会话级，否则看全局默认）"""
    st = load_settings() or {}
    _sid = str(session_id or "").strip()
    if _sid:
        names = (st.get("kb_mounts") or {}).get(_sid) or []
    else:
        names = st.get("kb_mounted") or []
    return {"status": "success", "kb_mounted": [str(x) for x in names],
            "session_id": _sid or None}


@app.post("/kb/dynamic/create")
def kb_dynamic_create(payload: dict = None):
    """P2.2: 创建会话级动态知识库（TTL 默认 24h，owner_session 隔离）"""
    p = payload or {}
    sid = str(p.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "session_id 必填（动态库必须归属一个会话）")
    name = "_dyn__%s__%s" % (re.sub(r"[^0-9A-Za-z_-]", "", sid)[:16] or "s",
                             datetime.now().strftime("%H%M%S%f"))
    try:
        meta = kb.create_kb(name, description=str(p.get("description") or ""),
                            dynamic=True, owner_session=sid,
                            ttl_hours=p.get("ttl_hours"))
        kb._append_audit(name, "create", dynamic=True, session=sid)
        return {"status": "success", "knowledge_base": meta}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/kb/dynamic/{kb_name}/append")
def kb_dynamic_append(kb_name: str, payload: dict = None):
    """P2.2: 向动态库追加会话上下文（工具结果/任务文本/中间产物）"""
    p = payload or {}
    sid = str(p.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "session_id 必填")
    meta = kb.get_kb(kb_name)
    if not meta:
        raise HTTPException(404, "知识库 '%s' 不存在" % kb_name)
    if not kb.is_dynamic(meta):
        raise HTTPException(400, "'%s' 不是动态知识库（拒绝写入）" % kb_name)
    if not kb.check_session(meta, sid):
        raise HTTPException(403, "会话无权写入该动态知识库")
    try:
        res = kb.append_text_dynamic(kb_name, str(p.get("text") or ""),
                                     source=str(p.get("source") or "task"),
                                     session_id=sid)
        return {"status": "success", **(res if isinstance(res, dict) else {}),
                "kb": kb_name, "session_id": sid}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/kb/dynamic/list")
def kb_dynamic_list(session_id: str = ""):
    """P2.2: 列出动态库（传 session_id 只看本会话）"""
    sid = str(session_id or "").strip()
    out = []
    for meta in kb.list_kbs():
        if not kb.is_dynamic(meta):
            continue
        if sid and not kb.check_session(meta, sid):
            continue
        out.append(meta)
    return {"status": "success", "dynamic_kbs": out, "count": len(out)}


@app.post("/kb/dynamic/cleanup")
def kb_dynamic_cleanup(payload: dict = None):
    """P2.2: 清理过期动态库（默认真删；dry_run=true 仅预览）"""
    dry = bool((payload or {}).get("dry_run"))
    removed = kb.cleanup_expired_dynamic(dry_run=dry)
    return {"status": "success", "removed": removed, "count": len(removed), "dry_run": dry}


@app.post("/kb/{kb_name}/query")
def kb_query(kb_name: str, req: KBQueryRequest):
    """
    测试检索效果。

    同时返回 diagnostics：**"0 条结果"必须能说清是为什么**（候选取不到 / 被阈值丢掉 /
    语言不匹配 / 索引为空）。工作流的检索节点会把它写进 `_meta.kb_resolution`，
    并在报告里给出可照做的告警，而不是含糊的"未检索到任何片段"。
    """
    try:
        results, diag = kb.query_kb_detailed(kb_name, req.query, req.top_k,
                                             min_score=req.min_score)
        return {"status": "success", "results": results, "diagnostics": diag}
    except ValueError as e:
        raise HTTPException(400, str(e))

# ============ 对话会话持久化 ============
@app.get("/chat/sessions")
def list_chat_sessions(limit: int = 50, offset: int = 0, q: str | None = None,
                       mode: str | None = None):
    """mode 过滤: 角色对话用 mode="role" ✓ 与普通对话（mode="chat"）天然隔离 ✓"""
    rows = db.list_chat_sessions(limit=limit, offset=offset, q=q)
    if mode:
        rows = [r for r in rows if (r.get("mode") or "chat") == mode]
    return {"sessions": rows}


@app.post("/chat/sessions")
def create_chat_session(req: ChatSessionCreate):
    session = db.create_chat_session(
        title=req.title or "新对话",
        mode=req.mode or "chat",
        model=req.model,
    )
    return {"session": session}


@app.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: str):
    session = db.get_chat_session(session_id, with_messages=True)
    if not session:
        raise HTTPException(404, f"会话不存在: {session_id}")
    return {"session": session}


@app.post("/chat/sessions/{session_id}/messages")
def append_chat_messages(session_id: str, req: ChatAppendRequest):
    try:
        info = db.append_chat_messages(session_id, [m.model_dump() for m in req.messages])
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "status": "success",
        **info,
        "session": db.get_chat_session(session_id, with_messages=False),
    }


@app.patch("/chat/sessions/{session_id}")
def update_chat_session(session_id: str, req: ChatSessionUpdate):
    if not db.get_chat_session(session_id, with_messages=False):
        raise HTTPException(404, f"会话不存在: {session_id}")
    session = db.update_chat_session(
        session_id, title=req.title, mode=req.mode, model=req.model
    )
    return {"session": session}


@app.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: str):
    if not db.delete_chat_session(session_id):
        raise HTTPException(404, f"会话不存在: {session_id}")
    return {"status": "success", "session_id": session_id}


# ============ 启动初始化 ============
db.init_db()
_imported = db.scan_existing_results(OUTPUT_DIR)
if _imported:
    print(f"[db] 首次启动，已导入 {_imported} 条历史结果", flush=True)


if __name__ == "__main__":
    import uvicorn
    # ★ S-3（2026-10-19 安全修复）: 原来 host="0.0.0.0" + 无鉴权 ✗
    #   ⇒ 局域网任意设备可训练/删库/跑工作流 ✗✗
    #   修: 默认只绑 127.0.0.1 ✓ 需要局域网访问时显式 WORKBENCH_HOST=0.0.0.0 ✓（自担风险 ✓）
    _HOST = os.environ.get("WORKBENCH_HOST", "127.0.0.1")
    # ★ F-1（2026-10-19 发布包测试发现 ✗）: 端口原来硬编码 8001 ✗
    #   ⇒ start.sh 的 BACKEND_PORT 环境变量无效 ✗ 无法多实例部署 ✗
    _PORT = int(os.environ.get("BACKEND_PORT", "8001"))
    print(f"[lora_manager] 监听 {_HOST}:{_PORT}（BACKEND_PORT 可覆盖 ✓ 局域网需 WORKBENCH_HOST=0.0.0.0 ✓）", flush=True)
    uvicorn.run(app, host=_HOST, port=_PORT)
