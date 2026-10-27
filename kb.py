# kb.py —— 知识库：ChromaDB + BGE 向量化 + 文档分块
import os
import json
import shutil
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timedelta
import re

# 语言判定只用于"为什么没命中"的诊断。度量模块不可用时退化为"不判断"，
# 绝不能因为诊断功能把检索本身搞崩。
try:
    from text_metrics import language_of
except Exception:                      # pragma: no cover
    def language_of(text):
        return "unknown"

PROJECT_DIR = Path(__file__).resolve().parent
KB_DIR = PROJECT_DIR / "data" / "kbs"
KB_INDEX_DIR = PROJECT_DIR / "data" / "chroma"

os.makedirs(KB_DIR, exist_ok=True)
os.makedirs(KB_INDEX_DIR, exist_ok=True)

_chroma_client = None
_embedder = None
_embedder_ref = None          # 实际加载的模型标识（本地路径或 HF 名）——一致性校验要用
# 默认用**多语** bge-m3：本项目的核心场景是"中文 query 打英文论文"，单语中文模型
# 在这个场景下相似度明显偏低（实测同一库同一 query：zh→en 最高 0.49 vs 0.55+）。
# 单语 bge-small-zh-v1.5 仍然可用：KB_EMBED_MODEL=BAAI/bge-small-zh-v1.5
# ⚠️ 换模型后**必须重建索引**（rebuild_kb_index / POST /kb/<name>/rebuild），
#    否则一致性闸门会拒绝查询（这是刻意的，见 check_embedding_consistency）。
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
EMBED_MODEL_NAME = DEFAULT_EMBED_MODEL      # 兼容旧名；请用 embed_model_name()

# 换 embedding 模型的唯一入口：环境变量 KB_EMBED_MODEL（名字或绝对路径）。
# 想用多语模型（中文 query 打英文语料）：KB_EMBED_MODEL=BAAI/bge-m3 或
# KB_EMBED_MODEL=~/models/bge-m3。换完**必须重建索引**（rebuild_kb_index）。
# 之所以要留这个口子：换模型此前只能改代码，而改代码意味着"临时改一下"会成为常态。

# 检索结果的最低相似度：低于它的候选会被丢弃。
# ⚠️ 这个"丢弃"必须是**可见**的（见 query_kb_detailed 的 diagnostics）：
# 实测中文 query 打英文语料时 bge-small-zh 给的是**负分**（-0.028），会被整个丢掉，
# 旧实现只回一个空列表，在日志里看着像"这个库是空的" —— 完全误导。
DEFAULT_MIN_SCORE = 0.1

# ── P2.1/P2.2（2026-10-26）：动态知识库 + 隔离 + 配额 + 审计 ──
#   动态库：会话/任务级临时库，带 TTL、owner_session、expires_at
#   隔离：动态库只对创建它的会话可见可写（静态库不限制）
#   配额：单库 chunk 上限（env KB_MAX_CHUNKS，默认 5 万）
KB_DYNAMIC_TIER = "dynamic"
KB_DEFAULT_TTL_HOURS = int(os.environ.get("KB_DEFAULT_TTL_HOURS", "24") or 24)
KB_MAX_CHUNKS = int(os.environ.get("KB_MAX_CHUNKS", "50000") or 50000)

def embed_model_name():
    """当前配置的 embedding 模型（环境变量 KB_EMBED_MODEL 优先）。"""
    return (os.environ.get("KB_EMBED_MODEL") or "").strip() or DEFAULT_EMBED_MODEL


def _candidate_dirs(model_ref):
    """
    把"一个模型标识"展开成本机上可能存在的目录，逐个探测。
    显式路径 > 本机路径 > ~/models/<basename> > 项目目录 > ModelScope 缓存。
    """
    ref = str(model_ref or "")
    base = os.path.basename(ref.rstrip("/\\")) or ref
    out = []
    explicit = (os.environ.get("KB_EMBED_MODEL_PATH") or "").strip()
    if explicit:
        out.append(Path(explicit))
    if os.path.isabs(ref):
        out.append(Path(ref))
    out += [
        Path.home() / "models" / base,
        PROJECT_DIR / base,
        Path.home() / ".cache" / "modelscope" / "hub" / "models" / ref,
    ]
    return out


def _is_model_dir(p):
    return (p / "config.json").exists() and (
        (p / "model.safetensors").exists() or (p / "pytorch_model.bin").exists()
    )


def _resolve_embed_model(model_ref=None):
    """返回实际可用的模型路径或名称。优先本地目录，找不到再回退到 HF 名称（需联网）。

    ★ 传入 model_ref 时也要解析 ✗（实测: meta 里记的短名 "bge-small-zh-v1.5"
      被直接当成 HF 名 → sentence-transformers 去 huggingface.co 下载 ✗
      → 网络不可达 → 反复重试卡住 ✓）
    """
    name = (str(model_ref).strip() if model_ref else "") or embed_model_name()
    for p in _candidate_dirs(name):
        if _is_model_dir(p):
            return str(p)
    return name


def _get_chroma():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=str(KB_INDEX_DIR))
    return _chroma_client


_EMBEDDER_CACHE = {}          # model_ref -> SentenceTransformer（★ 按模型缓存 ✓）
_EMBEDDER_LOCK = threading.RLock()  # 修 meta tensor 并发踩踏


def _get_embedder(model_ref=None):
    """线程安全入口：同一时刻只允许一个模型加载（防 meta tensor 踩踏）"""
    with _EMBEDDER_LOCK:
        return _get_embedder_unlocked(model_ref)


def _get_embedder_unlocked(model_ref=None):
    """按【模型】缓存 embedding 实例（★ 每个 KB 的模型可以不同 ✓）。

    ✗ 原来只缓存一个全局实例 ✓ → 全局环境变量一变，另一批库就报"维度不一致"✗
      实测: agent_use / agent_use_ctrl / minimax_h3_prompt 等 11 个库是 bge-m3(1024)，
            xiyouji 是 bge-small-zh(512) ✗ 而 KB_EMBED_MODEL 只有一个 ✓
            → 设成哪个都会打坏另一批 ✓
    ✓ 现在: 传 model_ref 就用它 ✓ 各模型的实例都缓存着 ✓ 互不干扰 ✓
    """
    global _embedder, _embedder_ref
    # ★ 必须过 _resolve_embed_model 解析本地路径 ✗ 否则短名会触发联网下载（会卡住 ✓）
    ref = _resolve_embed_model(model_ref)
    if ref in _EMBEDDER_CACHE:
        _embedder = _EMBEDDER_CACHE[ref]
        _embedder_ref = ref
        return _embedder
    from sentence_transformers import SentenceTransformer
    model_ref = ref
    source = "本地" if os.path.isdir(str(model_ref)) else "远程（需要联网下载）"
    print(f"[kb] 加载 embedding 模型: {model_ref}（{source}）", flush=True)
    # ★ P2-5 提前实现（2026-10-12 实测: 未指定 device 时默认抢 GPU ✗
    #   在 GPU 被占满时直接报 CUDA error ✗ 把 KB 入库打挂 ✓）
    #   KB_EMBED_DEVICE: auto（默认，保持原行为）/ cpu / cuda / cuda:N
    # ★ K0.5（2026-10-20）: 改为【settings.embedding_device 优先】✓ 环境变量兜底 ✓
    #   ⇒ 用户可在 UI 里选 CPU / GPU0 / GPU1 ✓ 不用改环境变量 ✓
    _dev = ""
    try:
        _st = {}
        _stp = os.path.join(PROJECT_DIR, "settings.json")
        if os.path.exists(_stp):
            import json as _json
            _st = _json.loads(open(_stp, encoding="utf-8").read())
        _dev = str(_st.get("embedding_device") or "").strip().lower()
    except Exception:
        _dev = ""
    if not _dev:
        _dev = (os.environ.get("KB_EMBED_DEVICE") or "auto").strip().lower()
    _kw = {} if _dev in ("", "auto") else {"device": _dev}
    # ★ 修 BUG（2026-10-21 P4）: **必须关掉 low_cpu_mem_usage** ✗
    #   症状: 多库切换模型报 NotImplementedError: Cannot copy out of meta tensor ✗
    #   根因: transformers 新版 + accelerate 存在时默认 low_cpu_mem_usage=True
    #         ⇒ 权重先建在 meta device（无真实数据）⇒ .to(cpu) 搬运必失败 ✗
    #   ⇒ 显式传 False + 强制 CPU 落地 ✓ 多模型共存不再污染 ✓
    _kw.setdefault("model_kwargs", {})["low_cpu_mem_usage"] = False
    if _kw.get("device"):
        print(f"[kb] embedding device={_dev}（settings/环境变量 ✓）", flush=True)
    # ★ 修 BUG（2026-10-21 P4）: 加载兜底 ✓ meta tensor 失败 ⇒ 降级纯 CPU 重试 ✓
    try:
        _embedder = SentenceTransformer(model_ref, **_kw)
    except Exception as _e_load:
        _es = str(_e_load)
        if 'meta tensor' in _es or 'meta device' in _es:
            print(f"[kb] ⚠ 模型加载踩 meta tensor ⇒ 降级纯CPU重试: {_es[:80]}", flush=True)
            _kw2 = {}
            _kw2["device"] = "cpu"
            _kw2["model_kwargs"] = {"low_cpu_mem_usage": False}
            _embedder = SentenceTransformer(model_ref, **_kw2)
        else:
            raise
    _embedder_ref = model_ref
    _EMBEDDER_CACHE[ref] = _embedder
    return _embedder


def _loaded_model_ref():
    """当前实际生效的模型标识（未加载时按解析结果推算，不触发加载）。"""
    return _embedder_ref or _resolve_embed_model()


def _text_prefixes(model_ref=None):
    """
    返回 (查询侧前缀, 文档侧前缀)。**按模型家族显式登记，不认识的一律不加** ——
    宁可保持现状，也不猜一个可能把向量空间搞坏的前缀。

    ⚠️ 文档侧前缀和查询侧一样重要：e5 系列要求文档加 "passage: "、查询加 "query: "，
    只给查询加前缀会让两侧落在**不同的向量空间**里，检索质量无声下降。
    换 embedding 模型时，这里是必须同步检查的第一处。
    """
    low = str(model_ref or _loaded_model_ref()).lower()
    if "bge" in low and "m3" in low:
        return "", ""                                  # bge-m3 不需要任何指令前缀
    if "bge" in low and "zh" in low:
        if "v1.5" in low:
            return "", ""                              # v1.5 不需要（见坑清单 #14）
        return "为这个句子生成表示以用于检索相关文章：", ""   # v1.0 中文指令
    if "e5-" in low or low.startswith("e5"):
        return "query: ", "passage: "
    return "", ""


def _query_prefix(model_ref=None):
    """查询侧前缀（保留此函数名，供既有调用与测试使用）。"""
    return _text_prefixes(model_ref)[0]


def _encode(texts, is_query=False, model_ref=None):
    """
    统一的向量化入口。返回 list[list[float]]。
    两侧各自按模型家族决定是否加指令前缀（bge 系列不加，e5 系列两侧都加）。
    """
    model = _get_embedder(model_ref)
    qp, pp = _text_prefixes()
    prefix = qp if is_query else pp
    items = [prefix + str(t) for t in texts] if prefix else list(texts)
    return model.encode(
        items, normalize_embeddings=True, show_progress_bar=False
    ).tolist()


def embed_signature(model_ref=None):
    """
    当前生效的向量化签名 {model, dim}，写进 meta.json。
    目的：防止"换了 embedding 模型却继续往同一个索引里塞"造成静默降质
    （512 / 768 / 1024 维混用，或同维度但向量空间不兼容）。
    本地路径与 HF 名都取 basename，两种来源得到同一个名字。
    """
    model = _get_embedder(model_ref)
    try:
        dim = int(model.get_sentence_embedding_dimension())
    except Exception:
        dim = None
    ref = str(model_ref or _loaded_model_ref()).rstrip("/\\")
    # 传入 model_ref 时它可能是 HF 名或路径，统一取 basename ✓ 与 meta 里记的写法一致 ✓
    if os.path.isdir(str(ref)) or "/" in str(ref) or os.sep in str(ref):
        ref = os.path.basename(str(ref).rstrip("/\\")) or ref
    return {"model": ref, "dim": dim}


def _collection_dim(collection):
    """从索引里读一条真实向量拿到实际维度（读不到返回 None）。"""
    try:
        res = collection.get(limit=1, include=["embeddings"])
        embs = res.get("embeddings") if isinstance(res, dict) else None
        if embs is None:
            return None
        if hasattr(embs, "tolist"):
            embs = embs.tolist()
        if len(embs) == 0:
            return None
        return len(embs[0])
    except Exception:
        return None


COLLECTION_SPACE = "cosine"      # 新建集合一律用 cosine：语义明确，1-dist 就是余弦


def _collection_space(collection):
    """集合的距离度量。读不到时按 Chroma 的默认值 l2 处理。"""
    try:
        sp = ((collection.configuration or {}).get("hnsw") or {}).get("space")
        if sp:
            return str(sp).lower()
    except Exception:
        pass
    try:
        sp = (collection.metadata or {}).get("hnsw:space")
        if sp:
            return str(sp).lower()
    except Exception:
        pass
    return "l2"


def _distance_to_score(dist, space):
    """
    Chroma 的 distance → 相似度。**这里是本文件最容易出错的一处。**

    ⚠️ 实测踩到的真 bug：所有集合都用 `score = 1 - dist`，但集合是 `hnsw:space=l2`
    （Chroma 默认）。而归一化向量的 L2² 距离 = 2 - 2·cos ⇒ 真实余弦 = 1 - dist/2。
    用错公式时算出来的是 `2cos - 1`，后果有两层：
      · **读数全错**：真实余弦 0.49 被报成 -0.03，报告/日志里的 score 全是假的；
      · **静默丢结果**：`score < 0.1` 实际等价于"余弦 < 0.55"，把 0.1~0.55 之间的
        命中全部丢掉 —— 中英文都一样，和语言没关系（我一开始误判成"跨语言能力弱"）。
    """
    if dist is None:
        return None
    if space == "l2":
        return 1.0 - dist / 2.0
    return 1.0 - dist


def check_embedding_consistency(kb_name, meta, collection=None):
    """
    索引一致性闸门：**写入与查询都必须先过这里**。返回 (ok, message)。

    两层校验，因为单靠任何一层都会漏：
    1) 维度以**索引里的真实向量**为准 —— meta.json 丢了也照样拦得住，
       且能抓到"512 维的库被 768 维模型查询"这种必然报错/错乱的情况；
    2) 模型名以 meta 记录为准 —— 同维度不同模型（如 bge-small 换 bge-base）
       维度完全一样，只有名字能发现。
    """
    # ★ 关键: 基准必须是【这个库该用的模型】✗ 不是全局环境变量的 ✓
    #   实测: agent_use 等 11 个库是 bge-m3(1024)，xiyouji 是 bge-small(512) ✗
    #   而全局只有一个 KB_EMBED_MODEL → 用全局判会让 11 个库全被误拦 ✓
    now = embed_signature(_kb_model_for_use(kb_name))
    recorded = meta.get("embedding") or {}

    if collection is not None:
        actual = _collection_dim(collection)
        if actual is not None and now.get("dim") and actual != now["dim"]:
            return False, (
                f"知识库 '{kb_name}' 的索引是 {actual} 维的，"
                f"当前 embedding 模型 {now.get('model')} 是 {now.get('dim')} 维 —— "
                f"维度不一致，继续用只会得到完全错乱的检索结果。"
                f"解决方式：切回建库时用的 embedding 模型，或原地重建索引"
                f"（设 KB_EMBED_MODEL 后用 rebuild_kb_index / POST /kb/<name>/rebuild）。"
            )

    if recorded.get("model") and now.get("model") and recorded["model"] != now["model"]:
        return False, (
            f"知识库 '{kb_name}' 由 embedding 模型 '{recorded['model']}'"
            f"（{recorded.get('dim')} 维）建立，当前模型是 '{now['model']}'"
            f"（{now.get('dim')} 维）—— 即使维度相同，向量空间也不兼容，"
            f"检索结果会静默失真。"
            f"解决方式：切回原模型，或原地重建索引"
            f"（设 KB_EMBED_MODEL 后用 rebuild_kb_index / POST /kb/<name>/rebuild）。"
        )
    return True, None

# ============ 分块 ============
def _split_sentences(text):
    import re
    # ★★ 修（2026-10-22 B组实测）：原来要求标点后必须有空白 `\s+`
    #   中文「第一句。第二句。」句号后无空格 ⇒ 正则不触发 ⇒ 整篇变一个块 ✗✗
    #   实测: 2184 字无标点长文 → 1 块；1677 字中文 → 1 块
    #   修: 去掉空白要求，按中英文标点切；额外切分号/换行 ✓
    parts = re.split(r'(?<=[。！？；;.!?…])\s*|\n+', text)
    return [p.strip() for p in parts if p.strip()]


def split_text(text, chunk_size=1200, overlap=200):
    """段落优先分块 + 【真重叠】（清单 P2-1 ✓ 原来 overlap 是摆设参数 ✗）。

    实测为什么要重叠: 一句话/一个结论被切在两个块之间时，
    只召回其中一块就拿不到完整语义 ✓（对角色问答尤其明显 ✓）
    """
    _raw = _split_text_raw(text, chunk_size)
    if overlap <= 0 or len(_raw) <= 1:
        return _raw
    out = []
    for i, c in enumerate(_raw):
        # ★ 修（2026-10-22）：原子块（代码/表格）**不加重叠** ✗
        #   否则上一块的尾巴会被复制进下一块 ⇒ ``` 围栏数量变奇数、表格行重复 ✗
        _c_atomic = c.lstrip().startswith('```') or c.lstrip().startswith('|')
        _prev_atomic = (i > 0 and (_raw[i - 1].lstrip().startswith('```')
                                   or _raw[i - 1].lstrip().startswith('|')))
        if i == 0 or _c_atomic or _prev_atomic:
            out.append(c)
            continue
        # ★ 重叠只能填【剩余空间】✗ 否则块长会超出 chunk_size（实测被测试抓住 ✓）
        room = max(0, chunk_size - len(c))
        if not room:
            out.append(c)
            continue
        tail = _raw[i - 1][-min(overlap, room):]
        if tail and not c.startswith(tail):
            out.append(tail + c)
        else:
            out.append(c)
    return out


def _split_text_raw(text, chunk_size=500):
    """分块（2026-10-22 重写：修 B 组实测的 3 个切分缺陷）

    修的三处:
      ① 中文句子切分（在 _split_sentences 里修）
      ② **代码块 / 表格原子保护** —— 原来 ``` 被从中间劈开、表格切碎丢表头 ✗
      ③ **超长硬切兜底** —— 原来单句超长就原样成块（实测 2184/2399 字）✗
    """
    if not text:
        return []

    # ② 先把「围栏代码块」和「markdown 表格」切成原子块（内部不按段落切）
    blocks = []
    lines = text.split('\n')
    i = 0
    buf = []
    while i < len(lines):
        ln = lines[i]
        # 围栏代码块 ```...```
        if ln.lstrip().startswith('```'):
            if buf:
                blocks.append('\n'.join(buf)); buf = []
            fence = []
            fence.append(ln)
            i += 1
            while i < len(lines):
                fence.append(lines[i])
                if lines[i].lstrip().startswith('```'):
                    i += 1
                    break
                i += 1
            blocks.append('\n'.join(fence))   # 整块代码 = 一个原子块 ✓
            continue
        # markdown 表格（连续 | 开头行）
        if ln.lstrip().startswith('|'):
            if buf:
                blocks.append('\n'.join(buf)); buf = []
            tbl = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                tbl.append(lines[i]); i += 1
            blocks.append('\n'.join(tbl))     # 整张表 = 一个原子块（含表头）✓
            continue
        buf.append(ln)
        i += 1
    if buf:
        blocks.append('\n'.join(buf))

    # 再按空行拆段落（原子块内部不拆）
    paragraphs = []
    for b in blocks:
        if b.lstrip().startswith('```') or b.lstrip().startswith('|'):
            paragraphs.append(b.strip())        # 原子块直接成段
        else:
            for p in b.split('\n\n'):
                if p.strip():
                    paragraphs.append(p.strip())

    def _hard_split(t, limit):
        """③ 超长硬切兜底：先按句切，仍超长则按字符硬切"""
        out = []
        if len(t) <= limit:
            return [t]
        sents = _split_sentences(t)
        cur = ''
        for sent in sents:
            if len(sent) > limit:
                if cur:
                    out.append(cur); cur = ''
                for k in range(0, len(sent), limit):
                    piece = sent[k:k + limit]
                    if len(piece) >= limit:
                        out.append(piece)
                    else:
                        cur = piece
                continue
            if len(cur) + len(sent) + 1 <= limit:
                cur = (cur + ' ' + sent).strip() if cur else sent
            else:
                if cur:
                    out.append(cur)
                cur = sent
        if cur:
            out.append(cur)
        return out

    chunks = []
    buffer = ''
    for para in paragraphs:
        # ★ 修（2026-10-22 三次修复）：原子块（代码/表格）**必须直接成块**，
        #   不能在这里被 _hard_split 无脑切断（那样围栏/表头就毁了 ✗）
        #   交给下面的类型化切分处理 ✓
        _is_atomic = para.lstrip().startswith('```') or para.lstrip().startswith('|')
        if _is_atomic:
            if buffer:
                chunks.append(buffer); buffer = ''
            chunks.append(para)
            continue
        if len(buffer) + len(para) + 2 <= chunk_size:
            buffer = (buffer + '\n\n' + para).strip() if buffer else para
        else:
            if buffer:
                chunks.append(buffer); buffer = ''
            if len(para) > chunk_size:
                chunks.extend(_hard_split(para, chunk_size))   # ③ 硬切
            else:
                buffer = para
    if buffer:
        chunks.append(buffer)

    # ★ 修（2026-10-22 二次修复）：原子块（代码/表格）超长时**不能无脑硬切**
    #   否则 ``` 围栏被劈开（实测 count=3 奇数）、表格丢表头 ✗
    #   按类型专门处理:
    #     代码块 → 切行后每片重新包 ``` 围栏
    #     表格   → 切行后每片重复表头
    limit = int(chunk_size * 1.25)
    final = []
    for c in chunks:
        if len(c) <= limit:
            final.append(c)
            continue
        ls = c.split('\n')
        if c.lstrip().startswith('```'):
            # 代码块：去掉围栏行，切行，每片重包
            body = [x for x in ls if not x.lstrip().startswith('```')]
            cur, cur_len = [], 0
            for ln in body:
                if cur_len + len(ln) + 1 > chunk_size and cur:
                    final.append('```\n' + '\n'.join(cur) + '\n```')
                    cur, cur_len = [], 0
                cur.append(ln)
                cur_len += len(ln) + 1
            if cur:
                final.append('```\n' + '\n'.join(cur) + '\n```')
        elif c.lstrip().startswith('|'):
            # 表格：前两行是表头+分隔，切数据行时每片重复表头
            header = ls[:2] if len(ls) >= 2 else ls[:1]
            data = ls[2:] if len(ls) >= 2 else []
            cur, cur_len = [], 0
            for ln in data:
                if cur_len + len(ln) + 1 > chunk_size and cur:
                    final.append('\n'.join(header + cur))
                    cur, cur_len = [], 0
                cur.append(ln)
                cur_len += len(ln) + 1
            if cur:
                final.append('\n'.join(header + cur))
        else:
            final.extend(_hard_split(c, chunk_size))
    return [c for c in final if c.strip()]


# ============ 元数据 ============
def list_kbs():
    kbs = []
    for kb_dir in KB_DIR.iterdir():
        if not kb_dir.is_dir():
            continue
        meta_file = kb_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            with open(meta_file, 'r', encoding='utf-8') as f:
                kbs.append(json.load(f))
        except Exception:
            continue
    return sorted(kbs, key=lambda x: x.get("created_at", ""), reverse=True)


def get_kb(name):
    meta_file = KB_DIR / name / "meta.json"
    if not meta_file.exists():
        return None
    with open(meta_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_kb_meta(name, meta):
    kb_dir = KB_DIR / name
    kb_dir.mkdir(parents=True, exist_ok=True)
    with open(kb_dir / "meta.json", 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def create_kb(name, description="", tier=None, dynamic=False, owner_session=None, ttl_hours=None):
    """建库。P2 扩展：dynamic/owner_session/ttl_hours（全部向后兼容 ✓）

    · dynamic=True  → tier="dynamic"，写 owner_session / ttl_hours / expires_at
    · 静态库行为与原来完全一致（不写任何新字段）
    """
    if get_kb(name):
        raise ValueError(f"知识库 '{name}' 已存在")
    meta = {
        "name": name,
        "description": description,
        "documents": [],
        "total_chunks": 0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    if dynamic:
        _ttl = int(ttl_hours if ttl_hours is not None else KB_DEFAULT_TTL_HOURS)
        _ttl = max(1, min(_ttl, 24 * 30))          # 1 小时 ~ 30 天
        meta.update({
            "tier": KB_DYNAMIC_TIER,
            "owner_session": str(owner_session or ""),
            "ttl_hours": _ttl,
            "expires_at": (datetime.now() + timedelta(hours=_ttl)).isoformat(),
        })
    _save_kb_meta(name, meta)
    return meta


    # 档位参数（阶段1 加的 ✓）: heavy=bge-m3 1024 维 / light=bge-small-zh 512 维
    _tier = tier if tier in ("heavy", "light") else None
def create_kb_from_chunks(kb_name, chunks, description="", model_ref=None):
    """K1（2026-10-20）: 用【给定的 chunks】直接建库 ✓
    （供"清洗后建新库"用 ✓ 不经过文件切块 ✓）

    Args:
        kb_name: 新库名（必须不存在 ✓）
        chunks: [{"text","source","section",...}, ...]
        model_ref: embedding 模型（None ⇒ 用该库默认 ✓）

    Returns:
        dict: {ok, kb, chunks, dimensions, embedding}
    """
    if get_kb(kb_name):
        raise ValueError("知识库 '%s' 已存在（不覆盖 ✓）" % kb_name)
    if not chunks:
        raise ValueError("chunks 为空")
    ref = _resolve_embed_model(model_ref or _kb_model_for_use(None))
    create_kb(kb_name, description=description or "清洗后建库")
    meta = get_kb(kb_name)
    client = _get_chroma()
    try:
        client.delete_collection(name="kb_%s" % kb_name)
    except Exception:
        pass
    collection = client.create_collection(
        name="kb_%s" % kb_name, metadata={"hnsw:space": COLLECTION_SPACE})
    # 记 embedding 签名 ✓
    meta["embedding"] = embed_signature(ref)
    # 批量编码 ✓
    texts = [c.get("text", "") for c in chunks]
    try:
        vecs = _encode(texts, is_query=False, model_ref=ref)
    except Exception as e:
        raise RuntimeError("编码失败: %s" % e)
    batch = 64
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        metas = []
        for k, c in enumerate(part):
            metas.append({
                "source": str(c.get("source", ""))[:200],
                "section": str(c.get("section", ""))[:200],
                "idx": i + k,
                "chars": len(c.get("text", "")),
            })
        collection.add(
            ids=[("%s_c%d" % (kb_name, i + k)) for k in range(len(part))],
            documents=[c.get("text", "") for c in part],
            embeddings=vecs[i:i + batch],
            metadatas=metas,
        )
    # 更新 meta ✓
    meta["total_chunks"] = len(chunks)
    docs = {}
    for c in chunks:
        src = str(c.get("source", ""))
        if src not in docs:
            docs[src] = {"name": src, "chunks": 0, "chars": 0,
                         "added_at": datetime.now().isoformat()}
        docs[src]["chunks"] += 1
        docs[src]["chars"] += len(c.get("text", ""))
    meta["documents"] = list(docs.values())
    _save_kb_meta(kb_name, meta)
    return {
        "ok": True,
        "kb": kb_name,
        "chunks": len(chunks),
        "dimensions": meta.get("embedding", {}).get("dim"),
        "embedding": meta.get("embedding", {}).get("model"),
    }


def delete_kb(name):
    if not get_kb(name):
        raise ValueError(f"知识库 '{name}' 不存在")
    try:
        client = _get_chroma()
        client.delete_collection(name=f"kb_{name}")
    except Exception:
        pass
    shutil.rmtree(KB_DIR / name, ignore_errors=True)



# ── P2.1/P2.2（2026-10-26）：动态库 helper（隔离 / TTL / 审计 / 配额）──
def _append_audit(kb_name, action, **fields):
    """写审计记录到 data/kbs/<name>/audit.jsonl（写入/清理/生命周期事件）"""
    try:
        d = KB_DIR / kb_name
        d.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.now().isoformat(), "action": str(action)}
        rec.update({k: v for k, v in fields.items() if v is not None})
        with open(d / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def is_dynamic(meta):
    """是否动态库（会话/任务级临时库）"""
    return bool(meta) and str(meta.get("tier") or "") == KB_DYNAMIC_TIER


def is_expired(meta, now=None):
    """动态库是否已过期（静态库永不过期）"""
    if not is_dynamic(meta):
        return False
    exp = meta.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(str(exp)) <= (now or datetime.now())
    except Exception:
        return False


def check_session(meta, session_id):
    """动态库只对创建它的会话可见/可写；静态库不限制。"""
    if not is_dynamic(meta):
        return True
    owner = str(meta.get("owner_session") or "")
    if not owner:
        return True
    return str(session_id or "") == owner


def cleanup_expired_dynamic(dry_run=False):
    """清理过期动态库；返回被清理（或将被清理）的库名列表"""
    removed = []
    for meta in list_kbs():
        if is_expired(meta):
            nm = str(meta.get("name") or "")
            if not nm:
                continue
            removed.append(nm)
            if not dry_run:
                try:
                    delete_kb(nm)
                except Exception:
                    pass
    return removed


def append_text_dynamic(kb_name, text, source="task", session_id=None):
    """向动态库追加一段文本（作为会话上下文）。

    隔离：非动态库拒绝；owner_session 不匹配拒绝；过期拒绝。
    配额：超过 KB_MAX_CHUNKS 拒绝。
    审计：audit.jsonl 记录 append 事件。
    """
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError("知识库 '%s' 不存在" % kb_name)
    if not is_dynamic(meta):
        raise ValueError("'%s' 不是动态知识库（拒绝写入）" % kb_name)
    if is_expired(meta):
        raise ValueError("动态知识库 '%s' 已过期" % kb_name)
    if not check_session(meta, session_id):
        raise ValueError("会话无权写入动态知识库 '%s'" % kb_name)
    text = (text or "").strip()
    if len(text) < 10:
        return {"added_chunks": 0, "reason": "内容过短"}
    _proj = len(split_text(text))
    if int(meta.get("total_chunks") or 0) + _proj > KB_MAX_CHUNKS:
        raise ValueError("动态知识库超出配额: 上限 %d chunks（当前 %d，本次 %d）"
                         % (KB_MAX_CHUNKS, int(meta.get("total_chunks") or 0), _proj))
    docs_dir = KB_DIR / kb_name / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(source))[:30] or "task"
    fname = "dyn_%s_%s.txt" % (datetime.now().strftime("%Y%m%d_%H%M%S_%f"), safe)
    fp = docs_dir / fname
    fp.write_text(text, encoding="utf-8")
    res = add_documents_to_kb(kb_name, [str(fp)])
    _append_audit(kb_name, "append", source=source, session=session_id,
                  added_chunks=res.get("added_chunks") if isinstance(res, dict) else None)
    return res


# ============ 文件抽取 ============
def _extract_file_text(path):
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        import importlib
        lm = importlib.import_module("lora_manager")
        return lm.extract_text_from_pdf(str(p))
    elif suffix in (".txt", ".md", ".text", ".json"):
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    raise ValueError(f"不支持的文件类型: {suffix}")


# ============ 加入文档 ============
def delete_by_source(kb_name, source_name, dry_run=False):
    """按来源文件名【逐条删除】该文档的所有分块（清单 P2-4 ✓ P4"遗忘"的前置）。

    原来只能整库删 ✗ 或靠重入覆盖 ✓ → 无法"忘掉某一件事" ✓
    返回被删的块数（dry_run=True 时只数不删 ✓）
    """
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError(f"知识库不存在: {kb_name}")
    # ★ 集合名就是 f"kb_{kb_name}" ✓（与 add_documents_to_kb 一致 ✓）
    #   ✗ 我原来写的 _coll_name 根本不存在 → NameError（实测踩到 ✓）
    coll = _get_chroma().get_collection(name=f"kb_{kb_name}")
    got = coll.get(where={"source": source_name}, include=[])
    ids = got.get("ids") or []
    if not dry_run and ids:
        coll.delete(ids=ids)
        meta["documents"] = [d for d in (meta.get("documents") or [])
                             if d.get("name") != source_name]
        meta["total_chunks"] = sum(int(d.get("chunks") or 0) for d in meta["documents"])
        _save_kb_meta(kb_name, meta)
    return {"source": source_name, "chunks": len(ids), "dry_run": bool(dry_run)}


# ══════════════════════════════════════════════════════════════
# P-56（2026-10-19）: KB 可重建性保证
#   问题: data/kbs/<name>/ 只有 meta.json ✗ 没有文档正文 ✗
#        ⇒ chroma 坏/丢 ⇒ 知识库无法恢复 ✗✗
#   修: 入库时把【抽取后的正文】另存到 data/kbs/<name>/docs/ ✓
#       重建时可从 docs/ 还原（不需要原始文件 ✓ 也不需要旧 chroma ✓）
# ══════════════════════════════════════════════════════════════
def _kb_docs_dir(kb_name):
    """文档正文目录（P-56 ✓）"""
    d = KB_DIR / kb_name / "docs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_doc_source(kb_name, doc_name, text):
    """保存文档正文以便重建（P-56 ✓ 失败不阻塞入库 ✓）"""
    try:
        d = _kb_docs_dir(kb_name)
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        fp = d / (h + ".txt")
        if not fp.exists():
            fp.write_text(text, encoding="utf-8")
        idx = d / "_index.json"
        m = {}
        if idx.exists():
            try:
                m = json.loads(idx.read_text(encoding="utf-8"))
            except Exception:
                m = {}
        if not isinstance(m, dict):
            m = {}
        m[doc_name] = h + ".txt"
        idx.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        return fp
    except Exception:
        return None


def list_saved_docs(kb_name):
    """列出已保存的文档正文（P-56 ✓ 给"可重建性"检查用 ✓）"""
    d = KB_DIR / kb_name / "docs"
    if not d.exists():
        return []
    out = []
    for fp in sorted(d.glob("*.txt")):
        try:
            out.append({"file": fp.name, "chars": len(fp.read_text(encoding="utf-8"))})
        except Exception:
            out.append({"file": fp.name, "chars": 0})
    return out


def rebuild_from_docs(kb_name, batch_size=32, progress_callback=None, model_ref=None):
    """从 docs/ 里的正文重建索引（P-56 ✓ 不需要原始文件 ✓ 不需要旧 chroma ✓）"""
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError("知识库 '%s' 不存在" % kb_name)
    d = KB_DIR / kb_name / "docs"
    if not d.exists():
        raise ValueError("知识库 '%s' 没有保存的文档正文（data/kbs/%s/docs/ 不存在）✗ "
                         "无法从零重建 —— 这是 P-56 修复前入库的老库 ⚠ 请重新上传文档" % (kb_name, kb_name))
    files = sorted(d.glob("*.txt"))
    if not files:
        raise ValueError("知识库 '%s' 的 docs/ 目录为空 ✗ 无法重建" % kb_name)

    ids, docs, mds = [], [], []
    for fi, fp in enumerate(files):
        if progress_callback:
            progress_callback(fi, len(files), "读取 %s" % fp.name)
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        chunks = split_text(text)
        if not chunks:
            continue
        doc_id = fp.stem
        for j, ch in enumerate(chunks):
            ids.append("%s_%d" % (doc_id, j))
            docs.append(ch)
            mds.append({"source": fp.name, "chunk_idx": j, "doc_id": doc_id})

    if not ids:
        raise ValueError("知识库 '%s' 的 docs/ 里没有可用正文 ✗" % kb_name)

    client = _get_chroma()
    tmp_name = "kb_%s__from_docs_tmp" % kb_name
    try:
        client.delete_collection(name=tmp_name)
    except Exception:
        pass
    tmp = client.create_collection(name=tmp_name, metadata={"hnsw:space": COLLECTION_SPACE})
    total = len(ids)
    try:
        for i in range(0, total, batch_size):
            embs = _encode(docs[i:i + batch_size], model_ref=_resolve_embed_model(model_ref))
            tmp.add(ids=ids[i:i + batch_size], documents=docs[i:i + batch_size],
                    embeddings=embs, metadatas=mds[i:i + batch_size])
            if progress_callback:
                progress_callback(min(i + batch_size, total), total, "重新向量化")
    except Exception as e:
        try:
            client.delete_collection(name=tmp_name)
        except Exception:
            pass
        raise ValueError("从 docs 重建失败（旧索引未改动）: %s" % e)

    sig = embed_signature(model_ref)
    try:
        client.delete_collection(name="kb_%s" % kb_name)
    except Exception:
        pass
    tmp.modify(name="kb_%s" % kb_name)
    meta["embedding"] = sig
    meta["updated_at"] = datetime.now().isoformat()
    meta["rebuilt_from"] = "docs"
    _save_kb_meta(kb_name, meta)
    return {"kb": kb_name, "chunks": total, "embedding": sig, "from": "docs"}


def add_documents_to_kb(kb_name, file_paths, progress_callback=None):
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError(f"知识库 '{kb_name}' 不存在")

    client = _get_chroma()

    # 手动传向量，不用 ChromaDB 的 embedding_function，兼容所有版本
    try:
        collection = client.get_collection(name=f"kb_{kb_name}")
    except Exception:
        collection = client.create_collection(
            name=f"kb_{kb_name}", metadata={"hnsw:space": COLLECTION_SPACE})

    # 一致性闸门：换了 embedding 模型还往同一个索引里塞 → 索引被静默污染，
    # 之后所有检索都不可信，而且**不会报错**。宁可直接拒绝。
    ok, why = check_embedding_consistency(kb_name, meta, collection)
    if not ok:
        raise ValueError(why)
    # ★ 只在【还没记过模型】时补记 ✓ 且用【该库该用的模型】✗ 不能用全局 env 的 ✓
    #   （实测: 这里用 embed_signature() 把 agent_use 记成了 bge-small/512 ✗ 它本是 bge-m3/1024 ✓）
    if not (meta.get("embedding") or {}).get("model"):
        meta["embedding"] = embed_signature(_kb_model_for_use(kb_name))

    total = len(file_paths)
    added_chunks = 0
    added_docs = []
    skipped = []

    for i, fp in enumerate(file_paths):
        if progress_callback:
            progress_callback(i, total, f"处理 {Path(fp).name}")
        try:
            text = _extract_file_text(fp)
            if not text or len(text.strip()) < 10:   # ★ 修（B组实测）：原来 <50 字整篇丢弃（28字通知/13字标题全丢）✗ 降到 10
                skipped.append({"file": Path(fp).name, "reason": "内容过短"})
                continue
            chunks = split_text(text)
            if not chunks:
                skipped.append({"file": Path(fp).name, "reason": "分块后为空"})
                continue
            # ★ P2.1: 配额强校验（防单库无限膨胀 ✓）
            _used = int(meta.get("total_chunks") or 0) + added_chunks
            if _used + len(chunks) > KB_MAX_CHUNKS:
                raise ValueError("知识库超出配额: 上限 %d chunks（当前 %d，本次 %d）"
                                 % (KB_MAX_CHUNKS, _used, len(chunks)))
            doc_name = Path(fp).name
            # ★ P-56（2026-10-19）: 保存正文到 data/kbs/<name>/docs/ ✓
            #   目的: chroma 坏了/丢了也能重建 ✓（原来只有 meta.json ✗ 无正文 ✗）
            _save_doc_source(kb_name, doc_name, text)
            # ★ P2-3: 原来是 md5(文件路径) ✗ → 换路径/改名同内容会重复入库 ✓
            #   改成【内容哈希】✓ 同内容换名也算同一个文档 ✓
            _fp_obj = Path(fp)          # ★ fp 是字符串 ✗ 直接 .exists()/.read_bytes() 会报
            _raw_bytes = _fp_obj.read_bytes() if _fp_obj.exists() else b""
            doc_id = hashlib.md5(_raw_bytes).hexdigest()[:12]
            # 【幂等修复·2026-10-11】同名替换语义：先删掉这个文件在集合里的旧块 ✗
            # 为什么：原来只有 collection.add，旧块不删；而 meta 又是纯 extend ✗
            # → 同一批文件入库 10 次后，agent_use 里每个文件出现 10 条（实测 64 条目 vs 8 文件名 ✗），
            #   检索时同一片段被重复命中，白占上下文。
            # 用 where 按 source（文件名）删，比按 id 前缀重建更稳 ✓
            _replaced = False
            try:
                collection.delete(where={"source": doc_name})
                _replaced = True
            except Exception as _e:
                # 删不掉不是致命错误（可能是首入库），但要记进 skipped 便于排查 ✓
                skipped.append({"file": doc_name, "reason": f"旧块清理跳过: {type(_e).__name__}"})
            ids = [f"{doc_id}_{j}" for j in range(len(chunks))]
            metadatas = [
                {"source": doc_name, "chunk_idx": j, "doc_id": doc_id}
                for j in range(len(chunks))
            ]
            embeddings = _encode(chunks, model_ref=_kb_model_for_use(kb_name))
            collection.add(
                ids=ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            added_chunks += len(chunks)
            added_docs.append({
                "name": doc_name,
                "chunks": len(chunks),
                "chars": len(text),
                "added_at": datetime.now().isoformat(),
            })
        except Exception as e:
            # ★ P2.1: 配额错误必须向上抛（不能吞进 skipped —— 否则无限膨胀静默通过 ✗）
            if "超出配额" in str(e):
                raise
            skipped.append({"file": Path(fp).name, "reason": str(e)})

    if progress_callback:
        progress_callback(total, total, "完成")

    # 【幂等修复】同名替换：先把这批文件在 meta 里的旧条目剔除，再 extend
    _names = {d["name"] for d in added_docs}
    if _names:
        meta["documents"] = [d for d in meta["documents"] if d.get("name") not in _names]
    meta["documents"].extend(added_docs)
    # 分块数按 meta 重算（原来 += 会随重复入库虚高 ✗）
    meta["total_chunks"] = sum(int(d.get("chunks") or 0) for d in meta["documents"])
    meta["updated_at"] = datetime.now().isoformat()
    _save_kb_meta(kb_name, meta)

    return {
        "added_docs": len(added_docs),
        "added_chunks": added_chunks,
        "skipped": skipped,
    }


def ingest_folder(kb_name, folder_path, extensions=None):
    if extensions is None:
        extensions = [".pdf", ".txt", ".md", ".text"]
    folder = Path(folder_path)
    if not folder.exists():
        raise ValueError(f"路径不存在: {folder_path}")
    files = []
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in extensions:
            files.append(str(f))
    if not files:
        return {"added_docs": 0, "added_chunks": 0, "skipped": []}
    return add_documents_to_kb(kb_name, files)


# ============ 检索 ============
_LANG_LABEL = {"zh": "中文", "en": "英文", "mixed": "中英混合", "unknown": "未知"}
_corpus_lang_cache = {}


def _corpus_language(kb_name, collection, meta, sample=5):
    """
    抽样判断语料主体语言。**只服务于"为什么没命中"的诊断，不参与任何打分。**
    任何失败都退化成 unknown —— 诊断信息绝不能把检索本身搞崩。
    """
    key = (kb_name, meta.get("total_chunks"))
    if key in _corpus_lang_cache:
        return _corpus_lang_cache[key]
    lang = "unknown"
    try:
        res = collection.get(limit=sample, include=["documents"])
        docs = (res or {}).get("documents") or []
        votes = {}
        for d in docs:
            lg = language_of(d[:500] if isinstance(d, str) else "")
            if lg in ("zh", "en"):
                votes[lg] = votes.get(lg, 0) + 1
        if votes:
            lang = max(votes.items(), key=lambda kv: kv[1])[0]
    except Exception:
        lang = "unknown"
    _corpus_lang_cache[key] = lang
    return lang


def _no_hit_hint(kb_name, diag):
    """把"0 条结果"翻译成一句能照做的话 —— 本次修复的重点。"""
    n, best, ms = diag["candidates"], diag["best_score"], diag["min_score"]
    ref = str(_loaded_model_ref() or "").rstrip("/\\")
    model = os.path.basename(ref) or ref
    if n == 0:
        return (f"知识库 '{kb_name}' 的索引里没有任何向量 —— 该往这个库里入库文档，"
                f"而不是换 query。")
    base = f"向量检索返回了 {n} 个候选，但全部低于阈值 {ms}（最高分 {best}）"
    ql, cl = diag["query_lang"], diag["corpus_lang"]
    if diag["lang_mismatch"]:
        return (base + f"；查询是{_LANG_LABEL[ql]}、语料主体是{_LANG_LABEL[cl]}，"
                f"跨语言本身会压低相似度。解决方式（按优先级）："
                f"① 把节点的 query_template 改成与语料同语言（英文语料就用英文查询）；"
                f"② 确认当前 embedding 模型是否多语（当前 '{model}'）—— 若仍是单语中文"
                f"模型，设 KB_EMBED_MODEL 换多语模型，再调 rebuild_kb_index 重建索引；"
                f"③ 确认能接受弱相关片段后，才调低该节点的 min_score。")
    if cl in ("zh", "en") and ql in ("zh", "en"):
        return (base + f"；查询与语料语言一致（{_LANG_LABEL[cl]}），"
                       f"更可能是 query 与库内容主题不相关，或该换一个 embedding 模型。")
    return (base + f"；语言是否匹配无法判断"
                   f"（查询 {_LANG_LABEL.get(ql, ql)} / 语料 {_LANG_LABEL.get(cl, cl)}）。")
def _kb_model_for_use(kb_name):
    """这个 KB 该用的 embedding 模型: 【meta 里记的】优先 ✓ 没有则用环境变量 ✓。

    ★ 为什么必须这样（实测 ✓）: agent_use 等 11 个库是 bge-m3(1024)，
      xiyouji 是 bge-small-zh(512)，而全局只有一个 KB_EMBED_MODEL ✗
      → 设成哪个都会让另一批库报"维度不一致" ✓
    """
    try:
        meta = get_kb(kb_name) or {}
        m = (meta.get("embedding") or {}).get("model")
        if m:
            return str(m)
    except Exception:
        pass
    return None          # None → _get_embedder 回退到环境变量 ✓


def _clamp_topk(top_k, lo=1, hi=50):
    """★ 修 500（2026-10-22 验收）: top_k<=0 会让 chromadb 抛 TypeError ⇒ 500 ✗
    统一夹到 [1,50]，非法值回落 5"""
    try:
        v = int(top_k)
    except Exception:
        return 5
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def query_kb_detailed(kb_name, query, top_k=5, min_score=None):
    """
    语义检索。返回 (results, diagnostics)。

    为什么要有 diagnostics：**"0 条结果"必须能说清是为什么**。
    旧实现把低于 min_score 的候选静默丢掉后只回一个空列表，调用方只能猜；
    实测中文 query 打英文语料时整个候选集都是负分（-0.028）被全部丢掉，
    在日志里看起来就像"这个知识库是空的" —— 这是个完全误导的结论。
    """
    top_k = _clamp_topk(top_k)
    if min_score is None:
        min_score = DEFAULT_MIN_SCORE
    min_score = float(min_score)
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    client = _get_chroma()
    try:
        collection = client.get_collection(name=f"kb_{kb_name}")
    except Exception as e:
        # ★ 修 BUG（2026-10-21）: 索引不存在 ⇒ **自动重建** ✓ 再重试 ✓
        #   原来直接报错 ✗ ⇒ 库目录存在但索引没建 ⇒ 查询**完全不可用** ✗
        #   为什么能自动重建: 块数据在库目录里 ✓ 无需原始文件 ✓
        print(f"[kb] '{kb_name}' 索引不存在 ⇒ 尝试自动重建: {e}", flush=True)
        rebuilt = False
        try:
            info = rebuild_kb_index(kb_name)
            print(f"[kb] '{kb_name}' 自动重建成功: {info}", flush=True)
            rebuilt = True
        except Exception as e2:
            # 重建失败 ⇒ 尝试从 docs/ 恢复 ✓
            try:
                info2 = rebuild_from_docs(kb_name)
                print(f"[kb] '{kb_name}' 从 docs/ 重建成功: {info2}", flush=True)
                rebuilt = True
            except Exception as e3:
                raise ValueError(
                    f"知识库 '{kb_name}' 索引不存在且**自动重建失败**: "
                    f"rebuild_kb_index={type(e2).__name__}: {e2}; "
                    f"rebuild_from_docs={type(e3).__name__}: {e3}"
                )
        if not rebuilt:
            raise ValueError(f"知识库 '{kb_name}' 索引不存在且无法重建")
        # 重试 ✓
        try:
            collection = client.get_collection(name=f"kb_{kb_name}")
        except Exception as e4:
            raise ValueError(f"知识库 '{kb_name}' 重建后仍无索引: {e4}")

    # 一致性闸门：模型不匹配时明确报错，而不是返回一堆"看起来正常、其实错乱"的结果
    ok, why = check_embedding_consistency(kb_name, meta, collection)
    if not ok:
        raise ValueError(why)

    # 手动算 query 向量，绕过 ChromaDB 的 embedding_function
    query_vec = _encode([query], is_query=True, model_ref=_kb_model_for_use(kb_name))[0]
    results = collection.query(query_embeddings=[query_vec], n_results=top_k)

    space = _collection_space(collection)
    out, scores = [], []
    if results and results.get("documents"):
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
        dists = results["distances"][0] if results.get("distances") else [None] * len(docs)
        for doc, mt, dist in zip(docs, metas, dists):
            score = _distance_to_score(dist, space)
            if score is not None:
                scores.append(score)
            # 低于阈值丢弃（跨语言匹配时 bge-small-zh 容易出负分）。
            # 丢了几条、最高分多少，全部记进 diagnostics —— 不许静默。
            if score is not None and score < min_score:
                continue
            out.append({
                "text": doc,
                "source": mt.get("source", "?"),
                "chunk_idx": mt.get("chunk_idx", 0),
                "score": score,
            })

    diag = {
        "kb": kb_name,
        "top_k": top_k,
        "min_score": min_score,
        "candidates": len(scores),
        "kept": len(out),
        "dropped": len(scores) - len(out),
        "best_score": round(max(scores), 4) if scores else None,
        "worst_score": round(min(scores), 4) if scores else None,
        "space": space,
        "query_lang": language_of(query),
        "corpus_lang": None,
        "lang_mismatch": False,
        "hint": None,
    }
    if not out:
        diag["corpus_lang"] = _corpus_language(kb_name, collection, meta)
        ql, cl = diag["query_lang"], diag["corpus_lang"]
        diag["lang_mismatch"] = bool(ql in ("zh", "en") and cl in ("zh", "en") and ql != cl)
        diag["hint"] = _no_hit_hint(kb_name, diag)
    return out, diag


def rebuild_kb_index(kb_name, batch_size=32, progress_callback=None, model_ref=None):
    """
    用**当前** embedding 模型重建该知识库的索引。

    为什么需要它：换 embedding 模型（`KB_EMBED_MODEL`）之后，旧向量和新模型不在同一个
    向量空间里，一致性闸门会拒绝查询。原先的建议是"新建知识库再入库"，但那要求原始
    文件还在手上；这里直接从索引里把 chunk 文本读出来重算向量 —— **不需要原始文件**。

    安全性：先把新向量全部算好写进临时集合，**成功后才**删旧集合改名。
    中途任何失败，旧索引原封不动（宁可不换，也不能把知识库弄丢）。
    """
    meta = get_kb(kb_name)
    if not meta:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    client = _get_chroma()
    try:
        old = client.get_collection(name=f"kb_{kb_name}")
    except Exception as e:
        # ★ P-56（2026-10-19）: 索引没了 ⇒ 尝试从 docs/ 重建 ✓ 而不是直接报错 ✗
        try:
            return rebuild_from_docs(kb_name, batch_size=batch_size,
                                     progress_callback=progress_callback,
                                     model_ref=model_ref)
        except Exception as e2:
            raise ValueError(
                "知识库 '%s' 索引不存在: %s；且从 docs/ 重建也失败: %s" % (kb_name, e, e2))

    ids, docs, mds = [], [], []
    offset = 0
    while True:
        got = old.get(limit=500, offset=offset, include=["documents", "metadatas"])
        got = got or {}
        b_ids = list(got.get("ids") or [])
        if not b_ids:
            break
        ids += b_ids
        docs += list(got.get("documents") or [])
        mds += list(got.get("metadatas") or [])
        offset += len(b_ids)
        if len(b_ids) < 500:
            break

    if not ids:
        raise ValueError(f"知识库 '{kb_name}' 的索引里没有任何向量，无从重建")

    total = len(ids)
    new_embs = []
    for i in range(0, total, batch_size):
        new_embs += _encode(docs[i:i + batch_size],
                           model_ref=_resolve_embed_model(model_ref))
        if progress_callback:
            progress_callback(min(i + batch_size, total), total, "重新向量化")

    tmp_name = f"kb_{kb_name}__rebuild_tmp"
    try:
        client.delete_collection(name=tmp_name)
    except Exception:
        pass
    tmp = client.create_collection(name=tmp_name,
                                   metadata={"hnsw:space": COLLECTION_SPACE})
    try:
        for i in range(0, total, 256):
            tmp.add(ids=ids[i:i + 256], documents=docs[i:i + 256],
                    embeddings=new_embs[i:i + 256], metadatas=mds[i:i + 256])
    except Exception as e:
        try:
            client.delete_collection(name=tmp_name)
        except Exception:
            pass
        raise ValueError(f"重建失败（旧索引未改动）: {e}")

    sig = embed_signature(model_ref)
    try:
        client.delete_collection(name=f"kb_{kb_name}")
        tmp.modify(name=f"kb_{kb_name}")
    except Exception as e:
        raise ValueError(
            f"新索引已建好但切换失败（数据在集合 '{tmp_name}' 里，没有丢）: {e}")

    meta["embedding"] = sig
    meta["updated_at"] = datetime.now().isoformat()
    meta["rebuilt_at"] = datetime.now().isoformat()
    _save_kb_meta(kb_name, meta)
    return {"kb": kb_name, "chunks": total, "embedding": sig}


def query_kb(kb_name, query, top_k=5, min_score=None):
    """兼容旧签名：只要结果列表。需要知道"为什么没命中"请用 query_kb_detailed()。"""
    return query_kb_detailed(kb_name, query, top_k=top_k, min_score=min_score)[0]