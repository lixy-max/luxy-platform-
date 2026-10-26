# -*- coding: utf-8 -*-
"""奇美拉自动云审（P6）：plan → Kimi 批量评审 → 政策+云双闸门 → 自动定级 → 合格者 dry-run

设计：
  · 只调用 **一次** 云端（批量评审全部候选），节省 Kimi RPM=3 的配额
  · 最终定级 = 政策闸门（许可/stars/来源） ∩ 云审结论
      - auto_approved：政策通过 且（云审 approve 或 云审 recommend 含该候选）
      - rejected：云审 reject
      - needs_review：其余（政策不通过 / 云审 need_more / 云审不可用）
  · auto_approved 的能力自动从 proposed → approved_pending_install（仍不自动安装）
  · 对 auto_approved 且满足 P3 条件（纯 wheel/无依赖）的候选自动做 dry-run（不安装）
  · 全部写入 data/chimera/reviews.jsonl（审计可追溯）
"""
import json
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REVIEWS_FILE = PROJECT_DIR / "data" / "chimera" / "reviews.jsonl"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure():
    REVIEWS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _save_review(rec):
    try:
        _ensure()
        with open(REVIEWS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def list_reviews(limit=50):
    if not REVIEWS_FILE.exists():
        return []
    out = []
    for ln in REVIEWS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out[-int(limit):]


def _norm_ids(x):
    ids = set()
    for v in (x or []):
        v = str(v or "").strip()
        if not v:
            continue
        ids.add(v)
        ids.add(v.split("/")[-1])          # owner/repo → repo
    return ids


def apply_cloud_review(plan):
    """把 plan 里的 cloud_review 应用到候选，返回最终定级统计"""
    import chimera as _chim
    cands = plan.get("candidates") or []
    review = plan.get("cloud_review") or {}
    status = review.get("status")
    rv = review.get("review") if isinstance(review.get("review"), dict) else {}
    verdict = str(rv.get("verdict") or "").lower()
    rec_ids = _norm_ids(rv.get("recommend"))
    # 云审"有效"才算数：status=ok 且 verdict 合法或 recommend 非空；
    # 否则（限流导致 reasoning 兜底/解析失败）→ 回退政策闸门，诚实标注
    cloud_ok = (status == "ok") and (verdict in ("approve", "reject", "need_more") or bool(rec_ids))
    if status == "ok" and not cloud_ok:
        status = "unusable"

    final = {"auto_approved": [], "needs_review": [], "rejected": []}
    for c in cands:
        gate = str(c.get("gate") or "needs_review")
        ids = _norm_ids([c.get("id"), c.get("candidate_id")])
        recommended = bool(ids & rec_ids)
        if cloud_ok:
            if verdict == "reject" and not recommended:
                f = "rejected"
            elif gate == "auto_approved" and (verdict == "approve" or recommended):
                f = "auto_approved"
            else:
                f = "needs_review"
        else:
            # 云端不可用 → 退回政策闸门（诚实标注）
            f = gate
        c["final_gate"] = f
        c["cloud_verdict"] = verdict if cloud_ok else None
        c["cloud_recommended"] = recommended
        final[f].append(c.get("candidate_id"))

    # 自动晋级：auto_approved 的能力 proposed → approved_pending_install
    promoted = []
    try:
        import capabilities as caps
        for c in cands:
            if c.get("final_gate") != "auto_approved":
                continue
            cid = "ext:%s:%s" % (str(c.get("source") or "?"),
                                 _slug(c.get("id") or ""))
            cap = caps.get_capability(cid)
            if cap and cap.get("status") == "proposed":
                caps.set_status(cid, "approved_pending_install")
                promoted.append(cid)
    except Exception:
        pass

    decision = {
        "cloud_status": status,
        "verdict": verdict if cloud_ok else "unavailable",
        "concerns": rv.get("concerns") or [],
        "license_notes": rv.get("license_notes") or "",
        "sandbox_requirements": rv.get("sandbox_requirements") or [],
        "counts": {k: len(v) for k, v in final.items()},
        "promoted_capabilities": promoted,
    }
    plan["cloud_decision"] = decision
    plan["final_gates"] = final
    plan["applied_at"] = _now()
    _save_review({"kind": "plan_review", "gap": plan.get("gap"),
                  "plan_id": plan.get("plan_id"), **decision, "at": _now()})
    return decision


def _slug(s):
    import re
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(s)).strip("-")[:80] or "cand"


def auto_dry_run(plan):
    """对 auto_approved 候选做安装前评估（不安装）；返回结果列表"""
    import sandbox_installer as si
    out = []
    for c in plan.get("candidates") or []:
        if c.get("final_gate") != "auto_approved":
            continue
        try:
            dr = si.dry_run(c)
            out.append({"candidate_id": c.get("candidate_id"), "repo": c.get("id"),
                        "can_install": bool(dr.get("can_install")),
                        "risks": dr.get("risks") or [],
                        "pypi": ((dr.get("checks") or {}).get("pypi") or {}).get("package")})
        except Exception as e:
            out.append({"candidate_id": c.get("candidate_id"), "repo": c.get("id"),
                        "can_install": False, "error": str(e)[:160]})
    plan["dry_runs"] = out
    _save_review({"kind": "dry_run", "gap": plan.get("gap"), "plan_id": plan.get("plan_id"),
                  "results": out, "at": _now()})
    return out


def auto_review(gap, top_k=5, do_dry_run=True):
    """完整自动云审流程：plan → 云审 → 双闸门定级 → 自动晋级 → 合格者 dry-run

    ★ 优化：最终计划回写 chimera plan 缓存 → 同缺口重复调用 ~0.1s（跳过网络）。
    """
    import chimera as _chim
    plan = _chim.plan(gap, top_k=top_k)
    _cached = bool(plan.get("cached_plan"))
    decision = apply_cloud_review(plan)
    if do_dry_run and not (_cached and plan.get("dry_runs")):
        auto_dry_run(plan)
    plan["auto_reviewed"] = True
    try:
        _chim._plan_cache_put(_chim._plan_cache_key(str(gap or "").strip(), top_k), plan)
    except Exception:
        pass
    return plan


def review_one(candidate):
    """单候选云审（供 /chimera/{id}/review 用）"""
    import chimera as _chim
    try:
        review = _chim.cloud_review(str(candidate.get("gap") or "single candidate"), [candidate])
    except Exception as e:
        review = {"status": "error", "reason": str(e)[:200]}
    rv = review.get("review") if isinstance(review.get("review"), dict) else {}
    verdict = str(rv.get("verdict") or "").lower()
    rec = _norm_ids(rv.get("recommend"))
    ids = _norm_ids([candidate.get("id"), candidate.get("candidate_id")])
    gate = str(candidate.get("gate") or "needs_review")
    _cloud_ok = (review.get("status") == "ok"
                 and (verdict in ("approve", "reject", "need_more") or bool(rec)))
    if _cloud_ok:
        if verdict == "reject" and not (ids & rec):
            final = "rejected"
        elif gate == "auto_approved" and (verdict == "approve" or (ids & rec)):
            final = "auto_approved"
        else:
            final = "needs_review"
    else:
        final = gate
    rec_out = {"candidate_id": candidate.get("candidate_id"), "repo": candidate.get("id"),
               "cloud_status": review.get("status"), "verdict": verdict or None,
               "final_gate": final, "at": _now()}
    _save_review({"kind": "single_review", **rec_out})
    return rec_out
