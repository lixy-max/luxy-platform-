# -*- coding: utf-8 -*-
"""route_table.py —— S2：级联路由任务表 v2（架构闭环）

v1 → v2 增强（对齐《目标与方向统一 v2.0》）：
  · 任务依赖 depends_on：拓扑执行，前序结果作为后续输入（参数传递）
  · 失败升级：local 失败 → 可升级 cloud → 可升级 transplant（奇美拉计划），记录 attempts
  · 产物：全部完成后生成 markdown 产物 outputs/route_tables/<id>.md，可查看/下载
  · 复用闭环：相似任务检索 + clone 复用历史任务表
  · 执行只跑依赖就绪的项；依赖失败 → 该项 skipped（不假装完成）

数据：data/route_tables.db
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
ARTIFACT_DIR = PROJECT_DIR / "outputs" / "route_tables"

CATEGORIES = ("local", "cloud", "transplant")
STATUSES = ("pending", "running", "done", "failed", "skipped")
TERMINAL = ("done", "failed", "skipped")
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
  depends_on TEXT DEFAULT '[]',
  attempts TEXT DEFAULT '[]',
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
    # 迁移：v1 库补 depends_on / attempts
    cols = {r["name"] for r in c.execute("PRAGMA table_info(items)").fetchall()}
    if "depends_on" not in cols:
        c.execute("ALTER TABLE items ADD COLUMN depends_on TEXT DEFAULT '[]'")
    if "attempts" not in cols:
        c.execute("ALTER TABLE items ADD COLUMN attempts TEXT DEFAULT '[]'")
    c.commit()
    return c


def _embed(text):
    try:
        import kb
        v = kb._encode([str(text)], is_query=True)[0]
        return struct.pack("%df" % len(v), *[float(x) for x in v])
    except Exception:
        return None


def _unpack(blob):
    if not blob:
        return None
    try:
        return struct.unpack("%df" % (len(blob) // 4), blob)
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


# ───────────────────── 生成任务表 ─────────────────────
def _assets_text(max_caps=16, max_kb=20):
    lines = []
    try:
        kbs = [p.name for p in (PROJECT_DIR / "data" / "kbs").glob("*") if p.is_dir()]
        lines.append("【本地知识库（可 kb:<库名>）】")
        lines.append("- " + "、".join(sorted(kbs)[:max_kb]))
        lines.append("规则：数据读取/核对/检索类任务优先用 kb:<库名>；有对应知识库时不要选外部爬虫工具。")
    except Exception:
        pass
    try:
        import capabilities as caps_mod
        caps = caps_mod.list_capabilities()
        usable = []
        for c in caps:
            if c.get("status") in ("disabled", "proposed"):
                continue
            if c.get("kind") == "external" and not c.get("installed"):
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


def _norm_depends(v, seq):
    """依赖解析：支持 [1,2] / "prev" / "1,2"；只允许比当前序号小的项"""
    out = []
    if v is None:
        return out
    if isinstance(v, str):
        v = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
    if not isinstance(v, (list, tuple)):
        return out
    for x in v:
        try:
            s = int(x)
            if 1 <= s < seq and s not in out:
                out.append(s)
        except Exception:
            continue
    return out


def _normalize_item(it, seq=1):
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
    if cat == "local" and target.startswith(("lora:", "ext:")):
        target = "capability:" + target
    _t = target.lower()
    if _t in ("cloud", "cloud:"):
        cat, target = "cloud", "cloud"
    elif _t in ("chimera", "transplant", "chimera:"):
        cat, target = "transplant", "chimera"
    if cat == "cloud" and not target:
        target = "cloud"
    if cat == "transplant" and not target:
        target = "chimera"
    _reason = str(it.get("reason") or "")
    # ★ S12：图谱/多模态检测 → 强制云端（架构 D 项）
    try:
        import modality_router as _mr
        _d = _mr.detect(content + " " + target)
        if _d.get("kinds"):
            cat = "cloud"
            target = "cloud:" + "+".join(_d["kinds"])
            _reason = (_reason + "；" if _reason else "") + "检测到%s需求→云端" % "/".join(_d["kinds"])
    except Exception:
        pass
    return {"content": content[:300], "category": cat, "target": target[:120],
            "reason": _reason[:200],
            "depends_on": _norm_depends(it.get("depends_on") or it.get("deps"), seq)}


def generate_items(request, max_items=MAX_ITEMS, context=None):
    """主脑生成任务表（含依赖）；失败退化单条"""
    ctx = ("\n【背景/附件】\n%s\n" % str(context)[:1500]) if context else ""
    system = (
        "你是蜂群主脑（14B）的任务规划器。把用户需求拆成 3–8 个可执行子任务，"
        "每个子任务标注执行位置：\n"
        "- local：本地完成（target 填 capability:<能力id> / kb:<库名> / direct）\n"
        "- cloud：需要云端模型/检索（target=cloud）\n"
        "- transplant：本地没有、需现场下载开源架构/工具移植（target=chimera）\n"
        "依赖：用 depends_on 填前置子任务序号数组（如 [1,2]；无依赖填 []）。\n"
        "约束：有对应知识库时，解析/核对/检索类任务必须用 kb:<库名>；不要选未安装的外部能力；"
        "target 只能填 direct / kb:<库名> / capability:<能力id> / cloud / chimera。\n"
        "只输出 JSON：{\"items\":[{\"content\":\"...\",\"category\":\"local|cloud|transplant\","
        "\"target\":\"...\",\"depends_on\":[1],\"reason\":\"...\"}]}，不要解释。\n" + ctx + "\n" + _assets_text())
    import swarm
    raw = swarm.ask_brain(system, "用户需求：" + str(request)[:1500], max_tokens=800)
    data = _extract_json(raw)
    items = []
    if isinstance(data, dict):
        for i, it in enumerate((data.get("items") or [])[:max_items], 1):
            n = _normalize_item(it, i)
            if n:
                items.append(n)
    if not items:
        items = [{"content": str(request)[:300], "category": "local", "target": "direct",
                  "reason": "任务表生成失败，退化为直接回答", "depends_on": []}]
    # ★ S12：请求整体含图谱/多模态但 items 未涉云 → 补一条云端项
    try:
        import modality_router as _mr
        _d = _mr.detect(str(request) + " " + str(context or ""))
        if _d.get("kinds") and not any(i.get("category") == "cloud" for i in items):
            _rt = _mr.route(str(request))
            items.append({"content": "处理整体需求中的%s部分：%s" % (
                "/".join(_d["kinds"]), str(request)[:200]),
                "category": "cloud",
                "target": _rt.get("target") or ("cloud:" + "+".join(_d["kinds"])),
                "reason": "检测到%s需求→云端（架构路由）" % "/".join(_d["kinds"]),
                "depends_on": []})
    except Exception:
        pass
    return items


# ───────────────────── CRUD ─────────────────────
def _insert_item(c, tid, seq, it):
    c.execute("INSERT INTO items(table_id, seq, content, category, target, reason, status, result, "
              "updated_at, depends_on, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (tid, seq, it["content"], it["category"], it["target"], it["reason"],
               "pending", "", _now(), json.dumps(it.get("depends_on") or []), "[]"))


def create_table(request, items=None, meta=None):
    request = str(request or "").strip()
    if not request:
        raise ValueError("request 不能为空")
    meta = meta or {}
    reuse = {"attempted": False}
    if items is None and os.environ.get("WORKFLOW_REUSE", "1") == "1":
        # ★ S5 命中即执行：相似任务已有沉淀工作流 → 直接建"复用工作流"任务表（不调主脑）
        try:
            import trajectory_distill as _td
            _hits = _td.search_workflows(request, limit=1)
            _thr = float(os.environ.get("WORKFLOW_REUSE_MIN", "0.65"))
            if _hits and float(_hits[0].get("score") or 0) >= _thr:
                _hit = _hits[0]
                reuse = {"attempted": True, "hit": _hit, "threshold": _thr}
                items = [{
                    "content": "复用已沉淀工作流：%s（相似度 %.2f）" % (
                        _hit.get("workflow_name") or _hit.get("wf_id"), float(_hit.get("score") or 0)),
                    "category": "local", "target": "workflow:%s" % _hit.get("wf_id"),
                    "reason": "命中已蒸馏工作流，自动执行本地路径",
                    "depends_on": [],
                }]
                meta["reuse_workflow"] = {"wf_id": _hit.get("wf_id"), "score": _hit.get("score"),
                                          "path": _hit.get("path"),
                                          "workflow_name": _hit.get("workflow_name")}
        except Exception as _e:
            reuse = {"attempted": True, "error": str(_e)[:200]}
    if items is None:
        items = generate_items(request, context=meta.get("context"))
    items = [n for n in (_normalize_item(it, i) for i, it in enumerate(items, 1)) if n][:MAX_ITEMS]
    if not items:
        raise ValueError("任务表为空")
    tid = hashlib.sha1(("%s|%s" % (request, time.time())).encode()).hexdigest()[:12]
    # 相似历史（复用提示）
    try:
        sim = [x for x in search_tables(request, limit=3) if x["id"] != tid]
    except Exception:
        sim = []
    meta = dict(meta)
    meta.setdefault("similar", sim)
    with _conn() as c:
        c.execute("INSERT INTO tables(id, request, created_at, status, items_count, embedding, meta) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (tid, request, _now(), "pending", len(items), _embed(request),
                   json.dumps(meta, ensure_ascii=False)))
        for i, it in enumerate(items, 1):
            _insert_item(c, tid, i, it)
    return get_table(tid)


def list_tables(limit=30, status=None):
    with _conn() as c:
        if status:
            rows = c.execute("SELECT id, request, created_at, status, items_count, meta FROM tables "
                             "WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, int(limit))).fetchall()
        else:
            rows = c.execute("SELECT id, request, created_at, status, items_count, meta FROM tables "
                             "ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
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
    meta = json.loads(t["meta"] or "{}")
    return {"id": t["id"], "request": t["request"], "created_at": t["created_at"],
            "status": t["status"], "items_count": t["items_count"], "meta": meta,
            "progress": {"done": done, "total": len(items),
                         "percent": round(done * 100.0 / len(items), 1) if items else 0.0},
            "artifact": meta.get("artifact"),
            "items": [{"seq": x["seq"], "content": x["content"], "category": x["category"],
                       "target": x["target"], "reason": x["reason"], "status": x["status"],
                       "result": x["result"], "updated_at": x["updated_at"],
                       "depends_on": json.loads(x["depends_on"] or "[]"),
                       "attempts": json.loads(x["attempts"] or "[]")} for x in items]}


def _set_item(c, tid, seq, **kw):
    fields = []
    vals = []
    for k in ("status", "result", "attempts", "updated_at"):
        if k in kw:
            fields.append("%s=?" % k)
            vals.append(kw[k])
    if not fields:
        return
    vals.extend([str(tid), int(seq)])
    c.execute("UPDATE items SET %s WHERE table_id=? AND seq=?" % ", ".join(fields), vals)


def check_item(table_id, seq, status="done", result=None):
    if status not in STATUSES:
        raise ValueError("status 非法: %s" % status)
    with _conn() as c:
        cur = c.execute("UPDATE items SET status=?, result=?, updated_at=? WHERE table_id=? AND seq=?",
                        (status, "" if result is None else str(result)[:6000], _now(),
                         str(table_id), int(seq)))
        if cur.rowcount == 0:
            raise ValueError("条目不存在: %s#%s" % (table_id, seq))
    _refresh_status(table_id)
    return get_table(table_id)


def _refresh_status(table_id):
    t = get_table(table_id)
    if not t:
        return None
    items = t["items"]
    if items and all(x["status"] in TERMINAL for x in items):
        st = "failed" if any(x["status"] == "failed" for x in items) else "done"
    elif any(x["status"] in TERMINAL for x in items) or any(x["status"] == "running" for x in items):
        st = "running"
    else:
        st = "pending"
    with _conn() as c:
        c.execute("UPDATE tables SET status=? WHERE id=?", (st, str(table_id)))
    return st


def search_tables(q, limit=10):
    q = str(q or "").strip()
    if not q:
        return []
    with _conn() as c:
        ts = c.execute("SELECT id, request, embedding, items_count, created_at FROM tables "
                       "ORDER BY created_at DESC LIMIT 500").fetchall()
    qv = _unpack(_embed(q))
    scored = []
    for t in ts:
        tv = _unpack(t["embedding"])
        if qv and tv and len(qv) == len(tv):
            scored.append((_cos(qv, tv), t))
        elif q in (t["request"] or ""):
            scored.append((1.0, t))
    scored.sort(key=lambda x: -x[0])
    return [{"id": t["id"], "request": t["request"], "score": round(s, 4),
             "items_count": t["items_count"], "created_at": t["created_at"]}
            for s, t in scored[:int(limit)]]


def clone_table(table_id, request=None):
    """复用历史任务表（重置状态，生成新表；meta.clone_of 记录来源）"""
    src = get_table(table_id)
    if not src:
        raise ValueError("任务表不存在: %s" % table_id)
    items = [{"content": x["content"], "category": x["category"], "target": x["target"],
              "reason": x["reason"], "depends_on": x["depends_on"]} for x in src["items"]]
    return create_table(request or src["request"], items=items,
                        meta={"clone_of": table_id, "context": src["meta"].get("context")})


def similar(table_id, limit=5):
    t = get_table(table_id)
    if not t:
        return []
    return [x for x in search_tables(t["request"], limit=limit + 1) if x["id"] != table_id][:limit]


# ───────────────────── 执行（依赖 + 参数传递 + 升级） ─────────────────────
def _dep_context(items, deps):
    parts = []
    for d in deps:
        it = items.get(d)
        if it and it.get("result"):
            parts.append("第%d步结果：\n%s" % (d, str(it["result"])[:1200]))
    return "\n\n".join(parts)


def _local_execute(item, ctx):
    """执行 local 项；返回 (ok, content, target_used)"""
    import swarm
    target = item["target"]
    content = item["content"]
    if target.startswith("workflow:"):
        # ★ S5：命中即执行——运行已沉淀工作流
        try:
            import trajectory_distill as _td
            wf_id = target.split(":", 1)[1]
            r = _td.run_workflow(wf_id, input_text=(content + ("\n\n前序结果：\n" + ctx if ctx else "")))
            if r.get("ok"):
                try:
                    _td.record_reuse(wf_id, task=content, channel="task_table")
                except Exception:
                    pass
            return bool(r.get("ok")), str(r.get("content") or r.get("error") or ""), "workflow"
        except Exception as e:
            return False, "工作流执行异常: %s" % str(e)[:300], "workflow"
    if target.startswith("capability:"):
        cap_id = target.split(":", 1)[1]
        dec = {"route": "capability", "capability_id": cap_id}
        # 传参：外部工具若需二维数据，用前序结果按行构造（架构闭环的参数传递 v1）
        try:
            import capabilities as caps
            cap = caps.get_capability(cap_id)
        except Exception:
            cap = None
        if cap and cap.get("kind") == "external":
            schema = cap.get("args_schema") or {}
            if isinstance(schema, dict) and (schema.get("positional") or []) == ["data"]:
                lines = [ln.strip() for ln in str(ctx).splitlines() if ln.strip()][:20]
                dec["capability_input"] = {"data": [[ln[:120]] for ln in lines] or [["（无输入）"]],
                                           "headers": ["内容"]}
        r = swarm.execute(dec, (content + ("\n\n前序结果：\n" + ctx if ctx else "")))
        return bool(r.get("ok")), str(r.get("content") or r.get("meta", {}).get("error") or ""), "local"
    if target.startswith("kb:"):
        r = swarm.execute({"route": "kb", "kb_names": [target.split(":", 1)[1]]},
                          content + ("\n\n前序结果：\n" + ctx[:1500] if ctx else ""))
        return bool(r.get("ok")), str(r.get("content") or ""), "local"
    r = swarm.execute({"route": "direct"},
                      content + ("\n\n前序结果：\n" + ctx[:1500] if ctx else ""))
    return bool(r.get("ok")), str(r.get("content") or ""), "local"


def _escalate(item, ctx, to):
    """失败升级：cloud / transplant"""
    import swarm
    content = item["content"] + ("\n\n前序结果：\n" + ctx[:1500] if ctx else "")
    if to == "cloud":
        try:
            r = swarm.execute({"route": "cloud"}, content)
            return bool(r.get("ok")), str(r.get("content") or ""), "escalated_cloud"
        except Exception as e:
            return False, "云升级失败: %s" % str(e)[:200], "escalated_cloud"
    if to == "transplant":
        try:
            import chimera
            plan = chimera.plan(item["content"], top_k=3)
            return True, "已生成奇美拉移植计划 %s（候选 %d）" % (
                plan.get("plan_id"), len(plan.get("candidates") or [])), "escalated_transplant"
        except Exception as e:
            return False, "移植升级失败: %s" % str(e)[:200], "escalated_transplant"
    return False, "未知升级目标", to


def execute_table(table_id, run_cloud=False, run_transplant=False,
                  escalate_cloud=False, escalate_transplant=False, max_items=None):
    """依赖拓扑执行：只跑依赖就绪的项；支持失败升级；完成后生成产物"""
    t = get_table(table_id)
    if not t:
        raise ValueError("任务表不存在: %s" % table_id)
    items = {x["seq"]: dict(x) for x in t["items"]}
    executed = []
    limit = int(max_items) if max_items is not None else 10 ** 6

    def _terminal(seq):
        return items[seq]["status"] in TERMINAL

    progress = True
    while progress and len(executed) < limit:
        progress = False
        for seq in sorted(items):
            if len(executed) >= limit:
                break
            it = items[seq]
            if it["status"] != "pending":
                continue
            deps = it.get("depends_on") or []
            if any(items[d]["status"] in ("failed", "skipped") for d in deps if d in items):
                with _conn() as c:
                    _set_item(c, table_id, seq, status="skipped", updated_at=_now(),
                              result="前置任务未完成（依赖：%s）" % deps)
                items[seq]["status"] = "skipped"
                executed.append({"seq": seq, "ok": False, "skipped": "dep"})
                progress = True
                continue
            if not all(items[d]["status"] in ("done", "skipped") for d in deps if d in items):
                continue  # 等依赖
            ctx = _dep_context(items, deps)
            cat = it["category"]
            target = it["target"]
            attempts = list(it.get("attempts") or [])
            ok = False
            result = ""
            try:
                if cat == "local":
                    ok, result, how = _local_execute(it, ctx)
                    attempts.append({"try": "local", "ok": ok, "at": _now()})
                elif cat == "cloud":
                    if not run_cloud:
                        with _conn() as c:
                            _set_item(c, table_id, seq, status="skipped", updated_at=_now(),
                                      result="待云端执行（未开启 run_cloud）")
                        items[seq]["status"] = "skipped"
                        executed.append({"seq": seq, "ok": False, "skipped": "cloud"})
                        progress = True
                        continue
                    # ★ S12：cloud:<kinds> 多模态能力检查（无能力则明确跳过，不假装处理）
                    _kinds = []
                    if target.startswith("cloud:"):
                        _kinds = [x for x in target.split(":", 1)[1].replace(";", "+").split("+") if x]
                    _prefix = ""
                    if _kinds:
                        try:
                            import modality_router as _mr
                            _blk = _mr.blocked_reason(_kinds)
                        except Exception:
                            _blk = None
                        if _blk:
                            with _conn() as c:
                                _set_item(c, table_id, seq, status="skipped", updated_at=_now(),
                                          result="多模态/图谱云端不可用：%s" % _blk)
                            items[seq]["status"] = "skipped"
                            executed.append({"seq": seq, "ok": False, "skipped": "modality"})
                            progress = True
                            continue
                        _prefix = "（架构路由：本任务涉及%s，按云端能力处理）\n" % "/".join(_kinds)
                    import swarm
                    r = swarm.execute({"route": "cloud"},
                                      _prefix + it["content"] + ("\n\n前序结果：\n" + ctx[:1500] if ctx else ""))
                    ok, result = bool(r.get("ok")), str(r.get("content") or "")
                    attempts.append({"try": "cloud", "ok": ok, "at": _now()})
                else:  # transplant
                    if not run_transplant:
                        with _conn() as c:
                            _set_item(c, table_id, seq, status="skipped", updated_at=_now(),
                                      result="待奇美拉移植决策（未开启 run_transplant）")
                        items[seq]["status"] = "skipped"
                        executed.append({"seq": seq, "ok": False, "skipped": "transplant"})
                        progress = True
                        continue
                    import chimera
                    plan = chimera.plan(it["content"], top_k=3)
                    ok = True
                    result = "奇美拉计划 %s：候选 %d" % (plan.get("plan_id"),
                                                     len(plan.get("candidates") or []))
                    attempts.append({"try": "transplant", "ok": ok, "at": _now()})
                # 失败升级
                if not ok and escalate_cloud:
                    ok2, result2, how2 = _escalate(it, ctx, "cloud")
                    attempts.append({"try": how2, "ok": ok2, "at": _now()})
                    if ok2:
                        ok, result = True, "[升级云端] " + result2
                if not ok and escalate_transplant:
                    ok3, result3, how3 = _escalate(it, ctx, "transplant")
                    attempts.append({"try": how3, "ok": ok3, "at": _now()})
                    if ok3:
                        ok, result = True, "[升级移植] " + result3
            except Exception as e:
                ok = False
                result = "%s: %s" % (type(e).__name__, str(e)[:400])
                attempts.append({"try": "exception", "ok": False, "at": _now()})
            st = "done" if ok else "failed"
            with _conn() as c:
                _set_item(c, table_id, seq, status=st, result=result[:6000],
                          updated_at=_now(), attempts=json.dumps(attempts, ensure_ascii=False))
            items[seq].update({"status": st, "result": result, "attempts": attempts})
            executed.append({"seq": seq, "ok": ok, "route": cat, "attempts": attempts})
            progress = True

    st = _refresh_status(table_id)
    t2 = get_table(table_id)
    if t2 and t2["items"] and all(x["status"] in TERMINAL for x in t2["items"]):
        _finalize_artifact(table_id, t2)
        t2 = get_table(table_id)
    return {"table": t2, "executed": executed, "status": st}


def _finalize_artifact(table_id, t):
    """生成任务表产物（markdown），写入 outputs/route_tables/ 并记录到 meta"""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# 任务表产物 · %s" % t["request"], "",
             "- 表 ID：%s" % t["id"], "- 生成时间：%s" % _now(),
             "- 状态：%s（完成 %d/%d）" % (t["status"], t["progress"]["done"], t["progress"]["total"]),
             ""]
    for it in t["items"]:
        lines.append("## 第 %d 项 [%s] %s" % (it["seq"], it["category"], it["target"]))
        lines.append("")
        lines.append("- 任务：%s" % it["content"])
        if it.get("depends_on"):
            lines.append("- 依赖：%s" % it["depends_on"])
        lines.append("- 状态：%s" % it["status"])
        if it.get("attempts"):
            lines.append("- 执行轨迹：%s" % json.dumps(it["attempts"], ensure_ascii=False))
        lines.append("")
        lines.append("**结果**：")
        lines.append("")
        lines.append("```")
        lines.append(str(it.get("result") or "")[:4000])
        lines.append("```")
        lines.append("")
    path = ARTIFACT_DIR / ("%s.md" % table_id)
    path.write_text("\n".join(lines), encoding="utf-8")
    meta = dict(t.get("meta") or {})
    meta["artifact"] = str(path)
    with _conn() as c:
        c.execute("UPDATE tables SET meta=? WHERE id=?",
                  (json.dumps(meta, ensure_ascii=False), str(table_id)))
    return str(path)


def get_artifact(table_id):
    t = get_table(table_id)
    if not t:
        raise ValueError("任务表不存在: %s" % table_id)
    p = (t.get("meta") or {}).get("artifact")
    if not p or not Path(p).exists():
        return {"exists": False, "path": None, "content": ""}
    return {"exists": True, "path": p, "content": Path(p).read_text(encoding="utf-8")[:20000]}


def stats():
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM tables").fetchone()["n"]
        items = c.execute("SELECT category, status, COUNT(*) AS n FROM items GROUP BY category, status").fetchall()
    by_cat = {}
    for r in items:
        by_cat.setdefault(r["category"], {})[r["status"]] = r["n"]
    return {"tables": n, "by_category": by_cat}
