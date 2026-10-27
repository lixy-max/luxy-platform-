# -*- coding: utf-8 -*-
"""S28 · 治理层（Governance）

"自己长手脚"必须可控：
- 审批门禁：自修改/新增能力/晋级/训练入队等高风险动作需审批（低风险可策略自动批准）
- 预算：云端调用/训练时长日预算；超限拒绝
- 权限/数据边界：高敏感数据能力必须人工审批
- 熔断/杀开关：kill switch 开启后禁止所有自修改动作
- 审计：data/hive/gov/audit.jsonl 全量留痕

策略文件：data/hive/gov/policy.json
"""
import json
import os
import pathlib
import threading
import time
import uuid

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
GOV_DIR = pathlib.Path(os.environ.get("HIVE_GOV_DIR", str(PROJECT_DIR / "data" / "hive" / "gov")))
POLICY_FILE = GOV_DIR / "policy.json"
APPROVALS_FILE = GOV_DIR / "approvals.json"
BUDGET_FILE = GOV_DIR / "budget.json"
AUDIT_FILE = GOV_DIR / "audit.jsonl"

DEFAULT_POLICY = {
    "version": 1,
    "kill_switch": False,
    "require_approval": ["capability_import", "promote_active", "train_enqueue",
                         "node_register", "cloud_high_risk"],
    "auto_approve_risk": ["low"],          # 低风险自动批准（受控自主）
    "daily_budget": {"cloud_calls": 200, "train_minutes": 60},
    "high_risk_keywords": ["敏感", "医疗", "财务", "合同", "法规", "删除", "上传外部",
                           "密钥", "密码", "身份证", "银行卡"],
    "sensitivity_high_requires_approval": True,
}
_lock = threading.RLock()


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return json.loads(json.dumps(default))


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_policy():
    p = _load_json(POLICY_FILE, DEFAULT_POLICY)
    for k, v in DEFAULT_POLICY.items():
        p.setdefault(k, v)
    return p


def save_policy(policy):
    with _lock:
        _save_json(POLICY_FILE, policy)
    return {"ok": True, "policy": policy}


def kill_switch(on):
    with _lock:
        p = load_policy()
        p["kill_switch"] = bool(on)
        _save_json(POLICY_FILE, p)
        audit({"event": "kill_switch", "on": bool(on)})
    return {"ok": True, "kill_switch": bool(on)}


def is_killed():
    return bool(load_policy().get("kill_switch"))


def _today():
    return time.strftime("%Y-%m-%d")


def audit(event):
    try:
        GOV_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "date": _today()}
        rec.update(event)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:
        return None


def _load_budget():
    b = _load_json(BUDGET_FILE, {"date": _today(), "cloud_calls": 0, "train_minutes": 0.0})
    if b.get("date") != _today():
        b = {"date": _today(), "cloud_calls": 0, "train_minutes": 0.0}
    return b


def check_budget(kind="cloud_calls", amount=1):
    """检查预算：返回 allowed/used/limit"""
    policy = load_policy()
    limit = float(policy.get("daily_budget", {}).get(kind, 0) or 0)
    b = _load_budget()
    used = float(b.get(kind, 0) or 0)
    allowed = (limit <= 0) or (used + amount <= limit)
    return {"ok": True, "allowed": bool(allowed), "kind": kind, "used": used,
            "limit": limit, "requested": amount,
            "reason": "" if allowed else "预算超限: %s %.1f+%.1f > %.1f" % (kind, used, amount, limit)}


def record_spend(kind="cloud_calls", amount=1):
    with _lock:
        b = _load_budget()
        b[kind] = float(b.get(kind, 0) or 0) + float(amount)
        _save_json(BUDGET_FILE, b)
    return {"ok": True, "budget": b}


def classify_risk(action, payload=None):
    """动作风险分级：low / medium / high（S28：工具类低风险可策略自动批准）"""
    p = load_policy()
    payload = payload or {}
    text = json.dumps(payload, ensure_ascii=False)[:4000]
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    sensitivity = str((payload.get("data") or {}).get("sensitivity")
                      or (manifest.get("data") or {}).get("sensitivity")
                      or "").lower()
    mtype = str(manifest.get("type") or "")
    if sensitivity == "high":
        return "high"
    for kw in p.get("high_risk_keywords") or []:
        if kw in text:
            return "high"
    if action == "capability_import":
        if mtype in ("tool", "workflow", "knowledge_pack") and sensitivity in ("", "low"):
            return "low"
        return "medium"
    if action in ("promote_active", "train_enqueue", "node_register", "cloud_high_risk"):
        return "medium"
    return "low"


def _load_approvals():
    return _load_json(APPROVALS_FILE, {})


def _save_approvals(d):
    _save_json(APPROVALS_FILE, d)


def request(action, payload=None, actor="auto", risk=None):
    """申请执行一个受治理动作；返回 allowed/status/approval_id"""
    policy = load_policy()
    if policy.get("kill_switch"):
        audit({"event": "denied", "action": action, "reason": "kill_switch", "actor": actor})
        return {"ok": False, "allowed": False, "status": "blocked_kill_switch",
                "reason": "熔断/杀开关已开启，禁止自修改动作"}
    risk = risk or classify_risk(action, payload)
    needs = action in (policy.get("require_approval") or [])
    auto_ok = risk in (policy.get("auto_approve_risk") or [])
    if (not needs) or auto_ok:
        audit({"event": "allowed", "action": action, "risk": risk,
               "mode": "auto" if needs else "no_approval_required", "actor": actor})
        return {"ok": True, "allowed": True, "status": "allowed", "risk": risk,
                "mode": "auto_approve" if needs else "direct"}
    # 已批准的同动作/同内容 → 直接放行（审批复用）
    digest = str(payload or "")[:500]
    for a in (_load_approvals().values()):
        if (a.get("status") == "approved" and a.get("action") == action
                and a.get("payload_digest") == digest):
            audit({"event": "allowed", "action": action, "risk": risk,
                   "mode": "approved_reuse", "approval_id": a.get("id")})
            return {"ok": True, "allowed": True, "status": "allowed", "risk": risk,
                    "mode": "approved_reuse", "approval_id": a.get("id")}
    aid = "ap_%s" % uuid.uuid4().hex[:10]
    with _lock:
        d = _load_approvals()
        d[aid] = {"id": aid, "action": action, "risk": risk, "actor": actor,
                  "payload_digest": str(payload or "")[:500],
                  "status": "pending", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _save_approvals(d)
    audit({"event": "approval_requested", "action": action, "risk": risk,
           "approval_id": aid, "actor": actor})
    return {"ok": True, "allowed": False, "status": "pending_approval",
            "approval_id": aid, "risk": risk,
            "reason": "风险=%s，需人工审批（POST /hive/gov/approve）" % risk}


def approve(approval_id, approver="human", note=""):
    with _lock:
        d = _load_approvals()
        a = d.get(approval_id)
        if not a:
            return {"ok": False, "error": "审批不存在: %s" % approval_id}
        if a.get("status") != "pending":
            return {"ok": False, "error": "审批状态非 pending: %s" % a.get("status")}
        a.update({"status": "approved", "approver": approver, "note": note,
                  "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        _save_approvals(d)
    audit({"event": "approved", "approval_id": approval_id, "approver": approver,
           "note": note})
    return {"ok": True, "approval": a}


def reject(approval_id, approver="human", note=""):
    with _lock:
        d = _load_approvals()
        a = d.get(approval_id)
        if not a:
            return {"ok": False, "error": "审批不存在: %s" % approval_id}
        a.update({"status": "rejected", "approver": approver, "note": note,
                  "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        _save_approvals(d)
    audit({"event": "rejected", "approval_id": approval_id, "approver": approver,
           "note": note})
    return {"ok": True, "approval": a}


def get_approval(approval_id):
    return _load_approvals().get(approval_id)


def pending(limit=50):
    rows = [a for a in _load_approvals().values() if a.get("status") == "pending"]
    return sorted(rows, key=lambda a: a.get("created_at") or "")[:limit]


def stats():
    policy = load_policy()
    budget = _load_budget()
    approvals = _load_approvals()
    audit_n = 0
    try:
        if AUDIT_FILE.exists():
            audit_n = sum(1 for _ in open(AUDIT_FILE, encoding="utf-8"))
    except Exception:
        pass
    return {"kill_switch": bool(policy.get("kill_switch")),
            "policy": {"require_approval": policy.get("require_approval"),
                       "auto_approve_risk": policy.get("auto_approve_risk"),
                       "daily_budget": policy.get("daily_budget")},
            "budget": budget,
            "approvals": {"total": len(approvals),
                          "pending": sum(1 for a in approvals.values()
                                         if a.get("status") == "pending"),
                          "approved": sum(1 for a in approvals.values()
                                          if a.get("status") == "approved"),
                          "rejected": sum(1 for a in approvals.values()
                                          if a.get("status") == "rejected")},
            "audit_entries": audit_n,
            "files": {"policy": str(POLICY_FILE), "approvals": str(APPROVALS_FILE),
                      "budget": str(BUDGET_FILE), "audit": str(AUDIT_FILE)}}
