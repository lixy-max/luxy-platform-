# -*- coding: utf-8 -*-
"""route_table.py —— S1：级联路由任务表（持久化 · 可检索 · 可勾选）

定位（对齐《目标与方向统一 v2.0》A 决策）：
  · 用户需求 → 14B 主脑生成任务表：哪些任务本地可做 / 云可做 / 需现场下载架构移植
  · 任务表持久化（SQLite），完成一项勾一项
  · 前端可视化（webui/luxy-routes.html）
  · 相似需求可检索复用（bge-m3 向量；无嵌入时回退关键词）

数据：data/route_tables.db
  tables(id, request, created_at, status, items_count, embedding BLOB, meta JSON)
  items(table_id, seq, content, category, target, reason, status, result, updated_at)
"""
import hashlib
import json
import os
import sqlite3
import struct
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ROUTE_TABLE_DB", str(PROJECT_DIR / "data" / "route_tables.db")))

CATEGORIES = ("local", "cloud", "transplant")
STATUSES = ("pending", "running", "done", "failed", "skipped")
MAX_ITEMS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tables (
  id TEXT PRIMARY KEY,
  request TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  items_count INTEGER NOT NULL DEFAULT 0,
  embedding BLOB,
  meta TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS items (
  table_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  content TEXT NOT NULL,
  category TEXT NOT NULL,
  target TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  PRIMARY KEY (table_id, seq)
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _embed(text):
    """任务描述向量（bge-m3）；失败返回 None（回退关键词检索）"""
    try:
        import kb
        v = kb._encode([str(text)], is_query=True)[0]
        return struct.pack("%df" % len(v), *[float(x) for x in v])
    except Exception:
        return None


def _cos(a, b):
    try:
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if not na or not nb:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)
    except Exception:
        return 0.0


def _unpack(blob):
    if not blob:
        return None
    try:
        n = len(blob) // 4
        return struct.unpack("%df" % n, blob)
    except Exception:
        return None


# ───────────────────── 生成任务表 ─────────────────────
def _assets_text(max_caps=16, max_kb=20):
    lines = []
    try:
        kbs = [p.name for p in (PROJECT_DIR / "data" / "kbs").glob("*") if p.is_dir()]
        lines.append("【本地知识库（可 kb:<库名>）】")
        lines.append("- " + "、".join(sorted(kbs)[:max_kb]))
        lines.append("规则：数据读取/核对/检索类任务**优先**用 kb:<库名>；有对应知识库时不要选外部爬虫工具。")
    except Exception:
        pass
    try:
        import capabilities as caps_mod
        caps = caps_mod.list_capabilities()
        # 只列"可执行"的：LoRA（非 disabled/proposed）+ 已安装的外部能力
        usable = []
        for c in caps:
            if c.get("status") in ("disabled", "proposed"):
                continue
            if c.get("kind") == "external":
                if not c.get("installed"):
                    continue
            usable.append(c)
        lines.append("【本地能力（可 capability:<能力id>）】")
        for c in usable[:max_caps]:
            lines.append("- %s | %s | 标签:%s" % (
                c.get("cap_id"), str(c.get("name"))[:28], "、".join((c.get("tags") or [])[:3])))
    except Exception:
        pass
    try:
        import cloud_bridge
        lines.append("【云端】%s" % ("可用" if cloud_bridge.available() else "不可用（无 key）"))
    except Exception:
        lines.append("【云端】未知")
    lines.append("【移植通道】奇美拉：可搜索开源项目并判断 直接移植 / 代码借鉴重建 / 下载模型")
    return "\n".join(lines)


def _extract_json(text):
    txt = str(text or "")
    i, j = txt.find("{"), txt.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(txt[i:j + 1])
        except Exception:
            pass
    if "```" in txt:
        for part in txt.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    return None


def _normalize_item(it):
    if not isinstance(it, dict):
        return None
    content = str(it.get("content") or it.get("task") or "").strip()
    if not content:
        return None
    cat = str(it.get("category") or it.get("type") or "local").strip().lower()
    if cat not in CATEGORIES:
        cat = "local"
    target = str(it.get("target") or "").strip()
    if cat == "local" and not target:
        target = "direct"
    # 主脑常直接填能力 id（lora:xxx / ext:xxx）→ 统一成 capability: 前缀
    if cat == "local" and target.startswith(("lora:", "ext:")):
        target = "capability:" + target
    # target 语义优先：cloud/chimera 强制改类别（主脑偶尔写 local+cloud 混搭）
    _t = target.lower()
    if _t in ("cloud", "cloud:"):
        cat, target = "cloud", "cloud"
    elif _t in ("chimera", "transplant", "chimera:"):
        cat, target = "transplant", "chimera"
    if cat == "cloud" and not target:
        target = "cloud"
    if cat == "transplant" and not target:
        target = "chimera"
    return {"content": content[:300], "category": cat, "target": target[:120],
            "reason": str(it.get("reason") or "")[:200]}


def generate_items(request, max_items=MAX_ITEMS, context=None):
    """调用主脑生成任务表（失败时退化为单条 local/direct）；context=附件/背景"""
    ctx = ("\n【背景/附件】\n%s\n" % str(context)[:1500]) if context else ""
    system = (
        "你是蜂群主脑（14B）的任务规划器。把用户需求拆成 3–10 个可执行子任务，"
        "每个子任务标注执行位置：\n"
        "- local：本地能完成（target 填 能力id=capability:lora:xxx / 知识库=kb:库名 / 直接回答=direct）\n"
        "- cloud：需要云端模型/检索（target=cloud）\n"
        "- transplant：本地没有、需要现场下载开源架构/工具移植（target=chimera）\n"
        "约束：有对应知识库时，解析/核对/检索类任务必须用 kb:<库名>；不要选未安装的外部能力；"
        "target 只能填 direct / kb:<库名> / capability:<能力id> / cloud / chimera。\n"
        "只输出 JSON 对象：{\"items\":[{\"content\":\"...\",\"category\":\"local|cloud|transplant\","
        "\"target\":\"...\",\"reason\":\"...\"}]}，不要解释。\n" + ctx + "\n" + _assets_text())
    import swarm
    raw = swarm.ask_brain(system, "用户需求：" + str(request)[:1500], max_tokens=700)
    data = _extract_json(raw)
    items = []
    if isinstance(data, dict):
        for it in (data.get("items") or [])[:max_items]:
            n = _normalize_item(it)
            if n:
                items.append(n)
    if not items:
        items = [{"content": str(request)[:300], "category": "local", "target": "direct",
                  "reason": "任务表生成失败，退化为直接回答"}]
    return items


# ───────────────────── CRUD ─────────────────────
def create_table(request, items=None, meta=None):
    request = str(request or "").strip()
    if not request:
        raise ValueError("request 不能为空")
    meta = meta or {}
    items = items if items is not None else generate_items(request, context=meta.get("context"))
    items = [n for n in (_normalize_item(it) for it in items) if n][:MAX_ITEMS]
    if not items:
        raise ValueError("任务表为空")
    tid = hashlib.sha1(("%s|%s" % (request, time.time())).encode()).hexdigest()[:12]
    now = _now()
    with _conn() as c:
        c.execute("INSERT INTO tables(id, request, created_at, status, items_count, embedding, meta) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (tid, request, now, "pending", len(items), _embed(request),
                   json.dumps(meta or {}, ensure_ascii=False)))
        for i, it in enumerate(items, 1):
            c.execute("INSERT INTO items(table_id, seq, content, category, target, reason, status, result, updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (tid, i, it["content"], it["category"], it["target"], it["reason"],
                       "pending", "", now))
    return get_table(tid)


def list_tables(limit=30, status=None):
    with _conn() as c:
        if status:
            rows = c.execute("SELECT id, request, created_at, status, items_count, meta "
                             "FROM tables WHERE status=? ORDER BY created_at DESC LIMIT ?",
                             (status, int(limit))).fetchall()
        else:
            rows = c.execute("SELECT id, request, created_at, status, items_count, meta "
                             "FROM tables ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [{"id": r["id"], "request": r["request"], "created_at": r["created_at"],
             "status": r["status"], "items_count": r["items_count"],
             "meta": json.loads(r["meta"] or "{}")} for r in rows]


def get_table(table_id):
    with _conn() as c:
        t = c.execute("SELECT * FROM tables WHERE id=?", (str(table_id),)).fetchone()
        if not t:
            return None
        items = c.execute("SELECT * FROM items WHERE table_id=? ORDER BY seq", (str(table_id),)).fetchall()
    done = sum(1 for x in items if x["status"] == "done")
    return {"id": t["id"], "request": t["request"], "created_at": t["created_at"],
            "status": t["status"], "items_count": t["items_count"],
            "meta": json.loads(t["meta"] or "{}"),
            "progress": {"done": done, "total": len(items),
                         "percent": round(done * 100.0 / len(items), 1) if items else 0.0},
            "items": [{"seq": x["seq"], "content": x["content"], "category": x["category"],
                       "target": x["target"], "reason": x["reason"], "status": x["status"],
                       "result": x["result"], "updated_at": x["updated_at"]} for x in items]}


def check_item(table_id, seq, status="done", result=None):
    if status not in STATUSES:
        raise ValueError("status 非法: %s（可选 %s）" % (status, ",".join(STATUSES)))
    with _conn() as c:
        cur = c.execute("UPDATE items SET status=?, result=?, updated_at=? WHERE table_id=? AND seq=?",
                        (status, "" if result is None else str(result)[:4000], _now(),
                         str(table_id), int(seq)))
        if cur.rowcount == 0:
            raise ValueError("条目不存在: %s#%s" % (table_id, seq))
    _refresh_status(table_id)
    return get_table(table_id)


def _refresh_status(table_id):
    t = get_table(table_id)
    if not t:
        return
    items = t["items"]
    if any(x["status"] == "failed" for x in items):
        st = "failed"
    elif all(x["status"] in ("done", "skipped") for x in items):
        st = "done"
    elif any(x["status"] in ("running", "done") for x in items):
        st = "running"
    else:
        st = "pending"
    with _conn() as c:
        c.execute("UPDATE tables SET status=? WHERE id=?", (st, str(table_id)))


def search_tables(q, limit=10):
    q = str(q or "").strip()
    if not q:
        return []
    rows = []
    with _conn() as c:
        ts = c.execute("SELECT id, request, embedding, items_count, created_at FROM tables "
                       "ORDER BY created_at DESC LIMIT 500").fetchall()
    qv = _unpack(_embed(q))
    scored = []
    for t in ts:
        tv = _unpack(t["embedding"])
        if qv and tv and len(qv) == len(tv):
            scored.append((_cos(qv, tv), t))
        else:
            # 关键词回退
            if q in (t["request"] or ""):
                scored.append((1.0, t))
    scored.sort(key=lambda x: -x[0])
    for s, t in scored[:int(limit)]:
        rows.append({"id": t["id"], "request": t["request"], "score": round(s, 4),
                     "items_count": t["items_count"], "created_at": t["created_at"]})
    return rows


# ───────────────────── 执行 ─────────────────────
def execute_table(table_id, run_cloud=False, run_transplant=False, max_items=None):
    """按任务表执行：local 直接执行；cloud 可选；transplant 可选（生成奇美拉计划）"""
    t = get_table(table_id)
    if not t:
        raise ValueError("任务表不存在: %s" % table_id)
    import swarm
    out = []
    for it in t["items"]:
        if it["status"] in ("done", "skipped"):
            continue
        if max_items is not None and len(out) >= int(max_items):
            break
        seq = it["seq"]
        cat = it["category"]
        content = it["content"]
        target = it["target"]
        try:
            if cat == "local":
                if target.startswith("capability:"):
                    r = swarm.execute({"route": "capability",
                                       "capability_id": target.split(":", 1)[1]}, content)
                elif target.startswith("kb:"):
                    r = swarm.execute({"route": "kb", "kb_names": [target.split(":", 1)[1]]}, content)
                else:
                    r = swarm.execute({"route": "direct"}, content)
                check_item(table_id, seq, "done" if r.get("ok") else "failed",
                           str(r.get("content") or r.get("meta", {}).get("error") or "")[:2000])
                out.append({"seq": seq, "ok": bool(r.get("ok")), "route": r.get("route")})
            elif cat == "cloud":
                if not run_cloud:
                    check_item(table_id, seq, "skipped", "待云端执行（未开启 run_cloud）")
                    out.append({"seq": seq, "ok": False, "skipped": "cloud"})
                    continue
                r = swarm.execute({"route": "cloud"}, content)
                check_item(table_id, seq, "done" if r.get("ok") else "failed",
                           str(r.get("content") or r.get("meta", {}).get("error") or "")[:2000])
                out.append({"seq": seq, "ok": bool(r.get("ok")), "route": "cloud"})
            else:  # transplant
                if not run_transplant:
                    check_item(table_id, seq, "skipped", "待奇美拉移植决策（未开启 run_transplant）")
                    out.append({"seq": seq, "ok": False, "skipped": "transplant"})
                    continue
                import chimera
                plan = chimera.plan(content, top_k=3)
                check_item(table_id, seq, "done",
                           "奇美拉计划 %s：候选 %d" % (plan.get("plan_id"),
                                                     len(plan.get("candidates") or [])))
                out.append({"seq": seq, "ok": True, "route": "chimera",
                            "plan_id": plan.get("plan_id")})
        except Exception as e:
            try:
                check_item(table_id, seq, "failed", "%s: %s" % (type(e).__name__, str(e)[:400]))
            except Exception:
                pass
            out.append({"seq": seq, "ok": False, "error": str(e)[:200]})
    return {"table": get_table(table_id), "executed": out}


def stats():
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM tables").fetchone()["n"]
        items = c.execute("SELECT category, status, COUNT(*) AS n FROM items GROUP BY category, status").fetchall()
    by_cat = {}
    for r in items:
        by_cat.setdefault(r["category"], {})[r["status"]] = r["n"]
    return {"tables": n, "by_category": by_cat}
