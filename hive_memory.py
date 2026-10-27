# -*- coding: utf-8 -*-
"""S23 · 主脑路由临时知识库（上下文记录）

目标：把中枢的**路由决策 + 执行结果**沉淀为"临时知识库"（TTL 过期自动清理），
      下次同类请求可检索历史记录作为上下文注入，形成短期经验记忆。

两套存储（互补）：
1) SQLite（快）：data/hive/memory.db —— 每次决策/结果立即落库，支持关键词检索/统计/清理
2) 动态知识库（语义，复用平台 kb.py，TTL 过期）：异步写入，检索走 bge-m3 语义
   - 开关：HIVE_MEMORY_SEMANTIC=1（默认 0，避免给每次决策增加 embedding 延迟）
   - KB 名：HIVE_MEMORY_KB=hive_route_memory；TTL：HIVE_MEMORY_TTL_H=24

注入：hive_orchestrator.decide 在 LLM 前取 context_for(request) 注入 user 消息；
      决策/兜底后 record()；失败不阻塞主流程（全部 try/except）。
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MEM_DB = Path(os.environ.get("HIVE_MEMORY_DB", str(PROJECT_DIR / "data" / "hive" / "memory.db")))
KB_NAME = os.environ.get("HIVE_MEMORY_KB", "hive_route_memory")
TTL_H = float(os.environ.get("HIVE_MEMORY_TTL_H", "24"))
SEMANTIC = os.environ.get("HIVE_MEMORY_SEMANTIC", "0") in ("1", "true", "True")
ENABLED = os.environ.get("HIVE_MEMORY_ENABLED", "1") in ("1", "true", "True")
MAX_ROWS = int(os.environ.get("HIVE_MEMORY_MAX_ROWS", "5000"))

_lock = threading.RLock()
_kb_lock = threading.Lock()
_stats = {"records": 0, "searches": 0, "semantic_hits": 0, "kb_writes": 0,
          "kb_errors": 0, "last_error": ""}


def _conn():
    MEM_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(MEM_DB), timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, kind TEXT, request TEXT, target TEXT, confidence REAL,
        why TEXT, answer TEXT, tags TEXT, session TEXT, expires_at REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON memories(ts)")
    return c


def _expiry():
    return time.time() + TTL_H * 3600


def cleanup():
    """清理过期记录（TTL）"""
    try:
        with _lock, _conn() as c:
            n = c.execute("DELETE FROM memories WHERE expires_at < ?", (time.time(),)).rowcount
            c.commit()
        return {"deleted": n}
    except Exception as e:
        return {"deleted": 0, "error": str(e)[:200]}


def _kb_ensure():
    """确保动态知识库存在（TTL 过期则重建；加锁防并发建库竞态）"""
    import kb
    with _kb_lock:
        meta = kb.get_kb(KB_NAME)
        if not meta:
            try:
                kb.create_kb(KB_NAME,
                             description="蜂巢中枢路由临时上下文（TTL %dh）" % int(TTL_H),
                             dynamic=True, ttl_hours=int(TTL_H))
            except Exception:
                pass   # 并发下可能已被其他线程创建
            return True
        if kb.is_expired(meta):
            try:
                kb.delete_kb(KB_NAME)
                kb.create_kb(KB_NAME,
                             description="蜂巢中枢路由临时上下文（TTL %dh）" % int(TTL_H),
                             dynamic=True, ttl_hours=int(TTL_H))
            except Exception:
                pass
        return True


def _kb_append_async(text, source="hive_router"):
    """异步写入动态 KB（不阻塞决策）"""
    def _run():
        try:
            _kb_ensure()
            import kb
            kb.append_text_dynamic(KB_NAME, text, source=source)
            with _lock:
                _stats["kb_writes"] += 1
        except Exception as e:
            with _lock:
                _stats["kb_errors"] += 1
                _stats["last_error"] = "kb: " + str(e)[:200]
    threading.Thread(target=_run, daemon=True).start()


def _fmt_text(request, decision=None, answer="", kind="route"):
    d = decision or {}
    lines = ["【任务】%s" % str(request or "")[:400]]
    if d:
        lines.append("【路由】target=%s confidence=%s type=%s" % (
            d.get("target"), d.get("confidence"), d.get("task_type")))
        if d.get("intent_summary"):
            lines.append("【意图】%s" % str(d["intent_summary"])[:200])
        r = d.get("routing") or {}
        if r.get("why"):
            lines.append("【原因】%s" % str(r["why"])[:200])
        c = d.get("cloud") or {}
        if c.get("provider"):
            lines.append("【云端】%s/%s" % (c.get("provider"), c.get("model")))
        if d.get("reason_trace"):
            lines.append("【轨迹】%s" % "；".join(map(str, d["reason_trace"][:4]))[:300])
    if answer:
        lines.append("【结果】%s" % str(answer)[:600])
    return "\n".join(lines)


def record(request, decision=None, answer="", kind="route", session="", semantic=None):
    """记录一次路由/执行（SQLite 立即写 + 可选异步动态 KB）"""
    if not ENABLED:
        return {"ok": False, "disabled": True}
    try:
        text = _fmt_text(request, decision, answer, kind)
        tags = json.dumps({"kind": kind, "target": (decision or {}).get("target"),
                           "task_type": (decision or {}).get("task_type")},
                          ensure_ascii=False)
        with _lock, _conn() as c:
            cur = c.execute(
                "INSERT INTO memories(ts,kind,request,target,confidence,why,answer,tags,session,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (time.time(), kind, str(request or "")[:1000],
                 (decision or {}).get("target"),
                 float((decision or {}).get("confidence") or 0),
                 str(((decision or {}).get("routing") or {}).get("why") or "")[:500],
                 str(answer or "")[:2000], tags, session or "", _expiry()))
            rid = cur.lastrowid
            if rid and rid % 200 == 0:   # 定期裁剪
                c.execute("DELETE FROM memories WHERE id < ?", (rid - MAX_ROWS,))
            c.commit()
        with _lock:
            _stats["records"] += 1
        use_sem = SEMANTIC if semantic is None else bool(semantic)
        if use_sem:
            _kb_append_async(text)
        return {"ok": True, "id": rid, "semantic": use_sem}
    except Exception as e:
        with _lock:
            _stats["last_error"] = "record: " + str(e)[:200]
        return {"ok": False, "error": str(e)[:300]}


def _tokens(text):
    """中英混合分词：英文/数字按词；中文按 2-gram（足够做关键词召回）"""
    import re as _re
    s = str(text or "")
    words = _re.findall(r"[A-Za-z0-9_\.-]{2,}", s)
    cjk = _re.findall(r"[\u4e00-\u9fff]+", s)
    for seg in cjk:
        if len(seg) <= 2:
            words.append(seg)
        else:
            words.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    out = []
    for w in words:
        w = w.lower().strip()
        if w and w not in out:
            out.append(w)
    return out[:24]


def _search_sqlite(query, limit):
    """关键词召回：token 重叠打分（最近 300 条内），无命中回退最近记录"""
    toks = _tokens(query)
    try:
        with _lock, _conn() as c:
            rows = c.execute(
                "SELECT request,target,confidence,why,answer,ts FROM memories "
                "WHERE expires_at > ? ORDER BY ts DESC LIMIT 300",
                (time.time(),)).fetchall()
        scored = []
        for r in rows:
            hay = ("%s %s %s" % (r[0] or "", r[3] or "", r[4] or "")).lower()
            score = sum(1 for t in toks if t in hay) if toks else 0
            if toks and score <= 0:
                continue
            scored.append((score, r[5] or 0, r))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [{"request": r[0], "target": r[1], "confidence": r[2], "why": r[3],
                 "answer": r[4], "ts": r[5], "score": sc, "source": "sqlite"}
                for sc, _, r in scored[:int(limit)]]
    except Exception:
        return []


def _search_semantic(query, limit):
    try:
        import kb
        if not kb.get_kb(KB_NAME):
            return []
        res, diag = kb.query_kb_detailed(KB_NAME, query, top_k=int(limit))
        out = []
        for item in (res or []):
            snippet = str(item.get("text") or "")
            out.append({"request": snippet[:400], "target": None, "confidence": None,
                        "why": "", "answer": "", "ts": None,
                        "score": item.get("score"), "kb_source": item.get("source"),
                        "source": "kb_semantic"})
        return out
    except Exception as e:
        with _lock:
            _stats["last_error"] = "semantic: " + str(e)[:200]
        return []


def search(query, limit=3, semantic=None):
    use_sem = SEMANTIC if semantic is None else bool(semantic)
    with _lock:
        _stats["searches"] += 1
    rows = []
    if use_sem:
        rows = _search_semantic(query, limit)
        if rows:
            with _lock:
                _stats["semantic_hits"] += 1
    if not rows:
        rows = _search_sqlite(query, limit)
    return rows


def context_for(request, limit=3, max_chars=700, semantic=None):
    """构造注入中枢 prompt 的历史上下文（无记录返回 ""）"""
    if not ENABLED:
        return ""
    rows = search(request, limit=limit, semantic=semantic)
    if not rows:
        return ""
    lines = []
    for r in rows:
        s = "· [%s] 请求=%s" % (r.get("source"), str(r.get("request"))[:120])
        if r.get("target"):
            s += " → target=%s" % r["target"]
        if r.get("why"):
            s += "｜原因=%s" % str(r["why"])[:80]
        lines.append(s)
    text = "\n".join(lines)
    return ("【历史相关路由/执行记录（临时知识库，仅供参考）】\n" + text)[:max_chars]


def stats():
    try:
        with _lock, _conn() as c:
            total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            alive = c.execute("SELECT COUNT(*) FROM memories WHERE expires_at > ?",
                              (time.time(),)).fetchone()[0]
            targets = c.execute("SELECT target, COUNT(*) FROM memories GROUP BY target").fetchall()
    except Exception:
        total = alive = 0
        targets = []
    kb_meta = {}
    try:
        import kb
        meta = kb.get_kb(KB_NAME) or {}
        kb_meta = {"name": KB_NAME, "chunks": meta.get("total_chunks"),
                   "tier": meta.get("tier"), "created_at": meta.get("created_at"),
                   "expires_at": meta.get("expires_at")}
    except Exception:
        kb_meta = {"name": KB_NAME, "unavailable": True}
    return {"enabled": ENABLED, "semantic": SEMANTIC, "ttl_h": TTL_H,
            "db": str(MEM_DB), "total": total, "alive": alive,
            "targets": {t: n for t, n in targets}, "kb": kb_meta,
            "stats": dict(_stats)}


def clear():
    try:
        with _lock, _conn() as c:
            n = c.execute("DELETE FROM memories").rowcount
            c.commit()
        try:
            import kb
            if kb.get_kb(KB_NAME):
                kb.delete_kb(KB_NAME)
        except Exception:
            pass
        return {"ok": True, "deleted": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
