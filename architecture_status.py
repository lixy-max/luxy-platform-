# -*- coding: utf-8 -*-
"""architecture_status.py —— S12：总架构完成度自检

按用户确认的架构逐项检查（真实数据/服务探测，不靠主观判断）：
  1 主脑路由（14B 服务可用）
  2 级联路由任务表（3 类执行体/依赖/产物）
  3 轨迹蒸馏（工作流/工具节点/数据集/复用）
  4 奇美拉三档（直装/重建/模型导入 + 云端审）
  5 云端（provider 配置 + 调用记录）
  6 图谱/多模态检测→云端路由（modality_router）
  7 本地知识库（KB + 表格结构化）
  8 进化指标（快照/趋势）
  9 治理与 Bug 台账
返回每项 pass/partial/missing + 证据 + 缺口，并给总分。
"""
import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _check_brain():
    base = os.environ.get("MAIN_BASE", "http://127.0.0.1:8000").rstrip("/")
    try:
        import requests
        r = requests.get(base + "/v1/models", timeout=4)
        if r.status_code == 200:
            data = r.json().get("data") or []
            mid = data[0].get("id") if data else "?"
            return "pass", {"endpoint": base + "/v1/models", "model": mid}
        return "partial", {"endpoint": base + "/v1/models", "status": r.status_code}
    except Exception as e:
        return "missing", {"endpoint": base + "/v1/models", "error": str(e)[:150]}


def _check_route_table():
    try:
        import sqlite3
        from route_table import DB_PATH as RT_DB
        tables, done, cats = 0, 0, set()
        if Path(RT_DB).exists():
            c = sqlite3.connect(str(RT_DB))
            c.row_factory = sqlite3.Row
            tables = c.execute("SELECT COUNT(*) n FROM tables").fetchone()["n"]
            done = c.execute("SELECT COUNT(*) n FROM tables WHERE status='done'").fetchone()["n"]
            for r in c.execute("SELECT DISTINCT category FROM items"):
                cats.add(r["category"])
            c.close()
        artifacts = len(list((PROJECT_DIR / "outputs" / "route_tables").glob("*.md")))
        status = "pass" if tables and {"local", "cloud", "transplant"} <= cats else (
            "partial" if tables else "missing")
        return status, {"tables": tables, "done": done, "categories": sorted(cats),
                        "artifacts_md": artifacts}
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_distill():
    try:
        import trajectory_distill as td
        wfs = td.list_workflows(limit=1000)
        tool_nodes = sum((w.get("tool_nodes") or 0) for w in wfs)
        datasets = len(list((PROJECT_DIR / "training_data" / "distilled").glob("*_train.jsonl")))
        reuse = 0
        p = PROJECT_DIR / "data" / "distilled" / "reuse_log.jsonl"
        if p.exists():
            reuse = len([1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])
        ok = bool(wfs)
        return ("pass" if ok and tool_nodes > 0 else ("partial" if ok else "missing")), {
            "workflows": len(wfs), "tool_nodes": tool_nodes,
            "datasets": datasets, "reuse_records": reuse,
            "semantic_steps": True}
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_chimera():
    ev = {}
    try:
        import capabilities as caps
        rows = caps.list_capabilities()
        ext = [c for c in rows if c.get("kind") == "external"]
        ev["external_caps"] = len(ext)
        ev["external_executable"] = sum(1 for c in ext if c.get("executable"))
    except Exception as e:
        ev["caps_error"] = str(e)[:120]
    try:
        import code_rebuild as crb
        ev["rebuilds"] = len(crb.list_rebuilds())
    except Exception as e:
        ev["rebuild_error"] = str(e)[:120]
    try:
        import model_import as mi
        ev["model_imports"] = len(mi.list_imports())
    except Exception as e:
        ev["model_import_error"] = str(e)[:120]
    ok = (ev.get("external_executable", 0) or 0) > 0 and (ev.get("rebuilds", 0) or 0) > 0 \
        and (ev.get("model_imports", 0) or 0) > 0
    return ("pass" if ok else "partial"), ev


def _check_cloud():
    try:
        import cloud_bridge as cb
        import modality_router as mr
        provs = mr.list_providers()
        ev = {"providers": [p["name"] for p in provs], "provider_count": len(provs)}
        try:
            base = cb._provider()
            ev["active_provider"] = bool(base)
            ev["model"] = base.get("model") if base else None
        except Exception:
            ev["active_provider"] = False
        usage = PROJECT_DIR / "data" / "cloud" / "usage.jsonl"
        ev["usage_calls"] = sum(1 for ln in usage.read_text(encoding="utf-8").splitlines() if ln.strip()) \
            if usage.exists() else 0
        ok = ev["provider_count"] > 0
        return ("pass" if ok else "missing"), ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_modality():
    try:
        import modality_router as mr
        s = mr.summary()
        graph_route = mr.route("把这份资料做知识图谱/关系抽取")
        vision_route = mr.route("识别这张图片/扫描件里的文字")
        ev = {"modalities_supported": s["modalities_supported"],
              "graph_route": graph_route.get("route"),
              "vision_route": vision_route.get("route"),
              "vision_advice": vision_route.get("advice")}
        # 图谱能路由到云端 + 视觉有明确阻断提示 → pass（接口已预留）
        if graph_route.get("route") != "cloud":
            return "missing", ev
        if vision_route.get("route") == "cloud":
            return "pass", ev
        ev["gap"] = "视觉/音频/视频云端 provider 未配置（接口已留，需配 modalities）"
        return "partial", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_kb():
    try:
        dirs = []
        try:
            import kb as _kb
            if hasattr(_kb, "list_kbs"):
                dirs = [k.get("name") if isinstance(k, dict) else str(k) for k in _kb.list_kbs()]
        except Exception:
            pass
        if not dirs:
            for base in (PROJECT_DIR / "data" / "knowledge_base", PROJECT_DIR / "knowledge_base",
                         PROJECT_DIR / "data" / "kb"):
                if base.exists():
                    dirs += [d.name for d in base.iterdir() if d.is_dir()]
        import table_store as ts
        tables = ts.list_tables()
        ok = bool(dirs) or bool(tables)
        return ("pass" if ok else "partial"), {"kb_dirs": len(dirs), "table_kbs": len(tables),
                                               "table_rows": sum((t.get("n_rows") or 0) for t in tables)}
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_metrics():
    try:
        import evolution_metrics as em
        snaps = 0
        if em.SNAPSHOT_FILE.exists():
            snaps = len([1 for ln in em.SNAPSHOT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()])
        import bug_ledger as bl
        st = bl.stats()
        m = em.current()
        return ("pass" if snaps > 0 else "partial"), {
            "snapshots": snaps, "kpis": m.get("kpis"), "bugs": st.get("by_status")}
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_governance():
    try:
        import governance as gov
        st = gov.status()
        return "pass", {"frozen": st.get("frozen"), "exec_today": st.get("exec_today"),
                        "exec_per_day": st.get("exec_per_day")}
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_conversation_isolation():
    """任务表按对话隔离：conversation_id 列存在、可过滤、存在多对话数据（S14）"""
    try:
        import sqlite3
        from route_table import DB_PATH as RT_DB
        if not Path(RT_DB).exists():
            return "missing", {"error": "任务表库不存在"}
        c = sqlite3.connect(str(RT_DB))
        c.row_factory = sqlite3.Row
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tables)")}
        if "conversation_id" not in cols:
            c.close()
            return "missing", {"error": "缺少 conversation_id 列"}
        convs = [dict(r) for r in c.execute(
            "SELECT conversation_id, COUNT(*) n FROM tables GROUP BY conversation_id "
            "ORDER BY n DESC LIMIT 10")]
        total = c.execute("SELECT COUNT(*) n FROM tables").fetchone()["n"]
        c.close()
        named = [x for x in convs if x["conversation_id"]]
        ev = {"tables": total, "conversations": len(convs),
              "named_conversations": len(named),
              "sample": [{"conv": (x["conversation_id"] or "")[:16], "tables": x["n"]}
                         for x in convs[:4]]}
        if named:
            return "pass", ev
        ev["gap"] = "尚无具名对话数据（老表都在默认对话 ''）"
        return "partial", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_cloud_build_distill():
    """缺失工作流 → 云端搭建 → 本地沉淀（S15）：构建记录 + cloud_built 索引 + 本地产物"""
    try:
        ev = {}
        log = PROJECT_DIR / "data" / "cloud" / "workflow_builds.jsonl"
        builds = 0
        if log.exists():
            builds = len([1 for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()])
        ev["cloud_builds"] = builds
        idx = PROJECT_DIR / "data" / "distilled" / "workflows.jsonl"
        cloud_wfs, golden = [], 0
        if idx.exists():
            for ln in idx.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("source") == "cloud_built":
                    cloud_wfs.append(r)
                    if r.get("golden_path") and (PROJECT_DIR / r["golden_path"]).exists()                             if not str(r.get("golden_path")).startswith("/") else True:
                        golden += 1
        ev["cloud_built_workflows"] = len(cloud_wfs)
        ev["with_golden"] = golden
        xlsx = list((PROJECT_DIR / "uploads").glob("*.xlsx")) if (PROJECT_DIR / "uploads").exists() else []
        ev["local_xlsx_outputs"] = len(xlsx)
        if cloud_wfs and builds:
            return "pass", ev
        ev["gap"] = "尚无云端搭建记录/沉淀工作流（或 golden 缺失）"
        return "partial", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_hive_router():
    """S18：蜂巢中枢三路由（/hive/decide + JSON 决策审计）"""
    try:
        mod = PROJECT_DIR / "hive_orchestrator.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        audit = PROJECT_DIR / "data" / "hive" / "decisions.jsonl"
        ev = {"module": mod.exists(), "api": ('"/hive/decide"' in lm) or ("/hive/decide" in lm),
              "audit_exists": audit.exists()}
        targets = []
        decisions = 0
        if audit.exists():
            import json as _j
            for ln in audit.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]:
                try:
                    d = _j.loads(ln)
                except Exception:
                    continue
                decisions += 1
                t = ((d.get("decision") or {}).get("target"))
                if t:
                    targets.append(t)
        ev["decisions"] = decisions
        ev["targets"] = sorted(set(targets))
        ev["sim_rules"] = ("SIM_KEYWORDS" in (mod.read_text(encoding="utf-8", errors="ignore")
                                             if mod.exists() else ""))
        if ev["module"] and ev["api"] and decisions >= 3 and len(set(targets)) >= 2:
            return "pass", ev
        if ev["module"] and ev["api"]:
            ev["gap"] = "已有 /hive/decide，但决策审计样本不足（需 ≥3 条且覆盖 ≥2 类 target）"
            return "partial", ev
        ev["gap"] = "S18 中枢模块或接口未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_hive_pool():
    """S19：Worker Pool + LoRA Router（热加载/LRU/能力路由）"""
    try:
        pool = PROJECT_DIR / "hive_worker_pool.py"
        reg = PROJECT_DIR / "hive_lora_registry.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        ev = {"pool_module": pool.exists(), "registry_module": reg.exists(),
              "api": "/hive/route_local" in lm and "/hive/pool" in lm}
        live = PROJECT_DIR / "data" / "hive" / "s19_live_test.json"
        if live.exists():
            d = json.loads(live.read_text(encoding="utf-8"))
            con = d.get("concurrency") or {}
            ev["concurrency_ok"] = con.get("ok")
            ev["concurrency_total"] = con.get("total")
            ev["hot_load_s"] = [c.get("decision", {}).get("load_latency_s")
                                for c in (d.get("cases") or [])]
            post = d.get("pool_after") or {}
            ev["evictions"] = (post.get("stats") or {}).get("evictions")
        if ev["pool_module"] and ev["registry_module"] and ev["api"]:
            if ev.get("concurrency_ok") and ev.get("concurrency_total") and \
                    ev["concurrency_ok"] >= max(10, int(ev["concurrency_total"] * 0.9)):
                return "pass", ev
            ev["gap"] = "模块/接口已就绪，但缺少 20 并发实测证据（或成功率<90%）"
            return "partial", ev
        ev["gap"] = "S19 Worker Pool/LoRA Router 未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_hive_failover():
    """S20：Failover 阈值 + 云端教师 + 通用轨迹回收"""
    try:
        fo = PROJECT_DIR / "hive_failover.py"
        tj = PROJECT_DIR / "hive_trajectory.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        ev = {"failover_module": fo.exists(), "trajectory_module": tj.exists(),
              "api": "/hive/run_failover" in lm and "/hive/trajectories" in lm}
        live = PROJECT_DIR / "data" / "hive" / "s20_live_test.json"
        if live.exists():
            d = json.loads(live.read_text(encoding="utf-8"))
            cases = d.get("cases") or []
            ev["cases"] = len(cases)
            ev["routes"] = sorted({(c.get("result") or {}).get("route") for c in cases})
            ts = d.get("trajectories_stats") or {}
            ev["trajectories"] = ts.get("total")
            ev["providers"] = ts.get("by_provider")
            fs = d.get("stats_final") or {}
            ev["escalations"] = fs.get("escalations")
            ev["cloud_ok"] = fs.get("cloud_ok")
        ok = (ev["failover_module"] and ev["trajectory_module"] and ev["api"])
        if ok and (ev.get("trajectories") or 0) >= 1 and ev.get("cloud_ok"):
            return "pass", ev
        if ok:
            ev["gap"] = "模块/接口就绪，但缺少云端教师+轨迹回收实测证据"
            return "partial", ev
        ev["gap"] = "S20 Failover/轨迹回收未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_hive_autotune():
    """S21：轨迹质检 + 训练调度（3060→5060 回退）+ 阈值自动训练"""
    try:
        qa = PROJECT_DIR / "hive_quality.py"
        tr = PROJECT_DIR / "hive_trainer.py"
        au = PROJECT_DIR / "hive_autotune.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        ev = {"quality": qa.exists(), "trainer": tr.exists(), "autotune": au.exists(),
              "api": "/hive/autotune/trigger" in lm and "/hive/train/plan" in lm}
        live = PROJECT_DIR / "data" / "hive" / "s21_live_test.json"
        if live.exists():
            d = json.loads(live.read_text(encoding="utf-8"))
            q = d.get("quality") or {}
            ev["quality_passed"] = q.get("passed")
            ev["quality_total"] = q.get("total")
            c = d.get("autotune_check") or {}
            ev["eligible"] = [x.get("task_type") for x in (c.get("eligible") or [])]
            ev["plan_3060"] = (c.get("plan") or {}).get("action")
            fb = (d.get("plan_5060_fallback") or {}).get("plan") or {}
            ev["plan_5060"] = fb.get("action")
            ev["fallback_used"] = fb.get("fallback_used")
            j = d.get("autotune_trigger") or {}
            ev["train_status"] = j.get("status")
            ev["adapter_exists"] = (j.get("trainer") or {}).get("adapter_exists")
            ev["worker_restored"] = ((d.get("after") or {}).get("worker_http") == 200)
        ok = ev["quality"] and ev["trainer"] and ev["autotune"] and ev["api"]
        if ok and (ev.get("train_status") == "done") and ev.get("adapter_exists") \
                and ev.get("worker_restored"):
            return "pass", ev
        if ok:
            ev["gap"] = "模块/接口就绪，但缺少自动训练+恢复的实测证据"
            return "partial", ev
        ev["gap"] = "S21 质检/训练调度未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}



def _check_hive_hotload():
    """S22：热加载流水线（shadow/灰度/回滚）"""
    try:
        hl = PROJECT_DIR / "hive_hotload.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        ev = {"module": hl.exists(),
              "api": "/hive/hotload/register" in lm and "/hive/hotload/rollback" in lm}
        live = PROJECT_DIR / "data" / "hive" / "s22_live_test.json"
        if live.exists():
            d = json.loads(live.read_text(encoding="utf-8"))
            ev["eval_passed"] = (d.get("evaluate") or {}).get("passed")
            cr = d.get("canary_route") or {}
            ev["canary_ratio"] = cr.get("ratio")
            ev["promote_active"] = ((d.get("promote_active") or {}).get("candidate") or {}).get("status")
            ev["rollback"] = ((d.get("rollback") or {}).get("candidate") or {}).get("status")
            ev["rejected_broken"] = (d.get("register_broken") or {}).get("ok") is False
        if ev["module"] and ev["api"]:
            if ev.get("eval_passed") and ev.get("promote_active") == "active" \
                    and ev.get("rollback") == "rolled_back" and ev.get("rejected_broken"):
                return "pass", ev
            ev["gap"] = "热加载模块/接口就绪，但缺少 shadow→灰度→晋级→回滚完整实测证据"
            return "partial", ev
        ev["gap"] = "S22 热加载流水线未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


def _check_hive_sim():
    """S22：仿真节点（压测/合成数据/脏数据隔离）"""
    try:
        mod = PROJECT_DIR / "hive_sim.py"
        lm = (PROJECT_DIR / "lora_manager.py").read_text(encoding="utf-8", errors="ignore")
        ev = {"module": mod.exists(),
              "api": "/hive/sim/run" in lm and "/hive/sim/reports" in lm}
        live = PROJECT_DIR / "data" / "hive" / "s22_live_test.json"
        if live.exists():
            d = json.loads(live.read_text(encoding="utf-8"))
            tr = (d.get("traffic") or {}).get("traffic") or {}
            ev["qps"] = tr.get("qps")
            ev["requests"] = tr.get("requests")
            ev["error_rate"] = tr.get("error_rate")
            dr = (d.get("dirty") or {}).get("dirty") or {}
            ev["dirty_injected"] = dr.get("injected")
            ev["dirty_quarantined"] = dr.get("quarantined")
            ev["kb_written"] = dr.get("kb_written")
        if ev["module"] and ev["api"]:
            ok_dirty = ev.get("dirty_injected") and \
                ev.get("dirty_quarantined") == ev.get("dirty_injected") and \
                ev.get("kb_written") is False
            ok_traffic = (ev.get("requests") or 0) > 100 and (ev.get("error_rate") or 0) == 0
            if ok_dirty and ok_traffic:
                return "pass", ev
            ev["gap"] = "仿真模块/接口就绪，但缺少压测/脏数据隔离合格证据"
            return "partial", ev
        ev["gap"] = "S22 仿真节点未就绪"
        return "missing", ev
    except Exception as e:
        return "missing", {"error": str(e)[:200]}


CHECKS = [
    ("brain", "主脑路由（14B 可用）", _check_brain),
    ("route_table", "级联路由任务表（本地/云端/移植）", _check_route_table),
    ("distill", "轨迹蒸馏（工作流/工具节点/数据集/复用）", _check_distill),
    ("chimera", "奇美拉三档（直装/重建/模型导入）", _check_chimera),
    ("cloud", "云端 provider 与调用", _check_cloud),
    ("modality", "图谱/多模态 → 云端路由", _check_modality),
    ("kb", "本地知识库（KB/表格）", _check_kb),
    ("metrics", "进化指标", _check_metrics),
    ("governance", "治理与台账", _check_governance),
    ("conversation", "任务表对话隔离", _check_conversation_isolation),
    ("cloud_build", "缺失工作流→云端搭建→本地沉淀", _check_cloud_build_distill),
    ("hive_router", "蜂巢中枢三路由（S18：Local/Cloud/Sim JSON 决策）", _check_hive_router),
    ("hive_pool", "Worker Pool + LoRA Router（S19：热加载/LRU/能力路由）", _check_hive_pool),
    ("hive_failover", "Failover 阈值 + 云端教师 + 轨迹回收（S20）", _check_hive_failover),
    ("hive_autotune", "轨迹质检 + 训练调度 + 阈值自动训练（S21）", _check_hive_autotune),
    ("hive_hotload", "热加载流水线（S22：shadow/灰度/回滚）", _check_hive_hotload),
    ("hive_sim", "仿真节点（S22：压测/合成数据/脏数据隔离）", _check_hive_sim),
]


def status(checks=None):
    """总架构自检；checks 可只跑子集"""
    want = set(checks or [c[0] for c in CHECKS])
    rows = []
    count = {"pass": 0, "partial": 0, "missing": 0}
    for cid, name, fn in CHECKS:
        if cid not in want:
            continue
        try:
            st, ev = fn()
        except Exception as e:
            st, ev = "missing", {"error": "%s: %s" % (type(e).__name__, str(e)[:150])}
        if st not in count:
            st = "partial"
        count[st] += 1
        gap = ""
        if st != "pass":
            gap = str(ev.get("gap") or ev.get("error") or ev.get("advice")
                      or "接口存在但数据/配置不足")
        rows.append({"id": cid, "name": name, "status": st, "evidence": ev, "gap": gap[:200]})
    total = len(rows) or 1
    score = round((count["pass"] + 0.5 * count["partial"]) / total, 3)
    return {"at": _now(), "overall": {"score": score, **count, "total": len(rows)},
            "checks": rows}


def summary_text():
    st = status()
    o = st["overall"]
    lines = ["【总架构完成度】%s  得分 %.0f%%（pass %d / partial %d / missing %d）" % (
        st["at"], o["score"] * 100, o["pass"], o["partial"], o["missing"])]
    for c in st["checks"]:
        icon = {"pass": "✅", "partial": "⚠️", "missing": "❌"}[c["status"]]
        lines.append("%s %s %s" % (icon, c["name"], ("| 缺口: " + c["gap"]) if c["gap"] else ""))
    return "\n".join(lines)
