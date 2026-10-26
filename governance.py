# -*- coding: utf-8 -*-
"""governance.py —— P5 治理层 v1：审计 / 预算熔断 / KILL 开关 / 影子期晋级

设计原则：
  · 所有敏感动作（安装/执行/晋级/冻结）都写**审计**（data/governance/audit.jsonl）
  · 预算：外部能力执行次数按日/分钟限额（默认 500/日、30/分钟）；超限拒绝并审计
  · KILL：全局冻结开关（state.json: frozen=true）→ 执行器/编排直接拒绝
  · 影子期：shadow 能力达到「使用次数 ≥ N 且成功率 ≥ R」才允许晋级 active
  · 状态落盘（可重启保留）；审计追加写（append-only）

诚实边界：这是**应用层治理**，不是安全边界（进程仍可绕过直接调库）；强隔离在 P4.2/Docker。
"""
import json
import os
import time
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
GOV_DIR = Path(os.environ.get("GOVERNANCE_DIR", str(PROJECT_DIR / "data" / "governance")))
STATE_FILE = GOV_DIR / "state.json"
AUDIT_FILE = GOV_DIR / "audit.jsonl"

EXEC_PER_DAY = int(os.environ.get("GOV_EXEC_PER_DAY", "500"))
EXEC_PER_MINUTE = int(os.environ.get("GOV_EXEC_PER_MINUTE", "30"))
PROMOTE_MIN_USES = int(os.environ.get("GOV_PROMOTE_MIN_USES", "10"))
PROMOTE_MIN_RATE = float(os.environ.get("GOV_PROMOTE_MIN_RATE", "0.9"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure():
    GOV_DIR.mkdir(parents=True, exist_ok=True)


def _load_state():
    if STATE_FILE.exists():
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {"frozen": False, "frozen_reason": "", "frozen_at": "",
            "daily": {}, "events": 0, "updated_at": ""}


def _save_state(st):
    _ensure()
    st["updated_at"] = _now()
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def audit(action, **fields):
    """追加审计记录；返回记录"""
    _ensure()
    rec = {"at": _now(), "action": str(action)}
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def read_audit(limit=100):
    if not AUDIT_FILE.exists():
        return []
    out = []
    for ln in AUDIT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out[-int(limit):]


# ───────────────────── KILL 开关 ─────────────────────
def is_frozen():
    st = _load_state()
    return bool(st.get("frozen"))


def freeze(reason="", by="policy"):
    st = _load_state()
    st.update({"frozen": True, "frozen_reason": str(reason)[:200],
               "frozen_at": _now(), "frozen_by": by})
    _save_state(st)
    return audit("freeze", reason=reason, by=by)


def unfreeze(by="policy"):
    st = _load_state()
    st.update({"frozen": False, "frozen_reason": "", "frozen_by": by})
    _save_state(st)
    return audit("unfreeze", by=by)


# ───────────────────── 预算 ─────────────────────
def _today():
    return date.today().isoformat()


def status():
    st = _load_state()
    d = (st.get("daily") or {}).get(_today()) or {}
    return {"frozen": bool(st.get("frozen")),
            "frozen_reason": st.get("frozen_reason") or "",
            "frozen_at": st.get("frozen_at") or "",
            "exec_today": int(d.get("exec", 0)),
            "exec_per_day": EXEC_PER_DAY,
            "exec_per_minute": EXEC_PER_MINUTE,
            "promote_min_uses": PROMOTE_MIN_USES,
            "promote_min_rate": PROMOTE_MIN_RATE,
            "updated_at": st.get("updated_at") or ""}


def _minute_key():
    return time.strftime("%Y-%m-%dT%H:%M")


def check_exec_allowed():
    """外部能力执行前检查：冻结/每分钟/每日"""
    if is_frozen():
        return False, "系统已冻结（KILL 开关打开）"
    st = _load_state()
    d = (st.get("daily") or {}).get(_today()) or {}
    if int(d.get("exec", 0)) >= EXEC_PER_DAY:
        return False, "今日执行预算已用尽（%d/%d）" % (int(d.get("exec", 0)), EXEC_PER_DAY)
    m = (st.get("minute") or {})
    if m.get("key") == _minute_key() and int(m.get("count", 0)) >= EXEC_PER_MINUTE:
        return False, "每分钟执行频率超限（%d/%d）" % (int(m.get("count", 0)), EXEC_PER_MINUTE)
    return True, "ok"


def record_exec(cap_id="", ok=True):
    """记录一次执行（计入预算）"""
    st = _load_state()
    d = st.setdefault("daily", {})
    day = d.setdefault(_today(), {"exec": 0, "fail": 0})
    day["exec"] = int(day.get("exec", 0)) + 1
    if not ok:
        day["fail"] = int(day.get("fail", 0)) + 1
    mk = _minute_key()
    if (st.get("minute") or {}).get("key") != mk:
        st["minute"] = {"key": mk, "count": 0}
    st["minute"]["count"] = int(st["minute"].get("count", 0)) + 1
    st["events"] = int(st.get("events", 0)) + 1
    _save_state(st)
    return audit("exec", cap_id=cap_id, ok=bool(ok))


# ───────────────────── 影子期晋级 ─────────────────────
def promote_ready(min_uses=None, min_rate=None):
    """把满足条件的 shadow 能力晋级 active；返回晋级列表"""
    import capabilities as caps
    min_uses = int(min_uses if min_uses is not None else PROMOTE_MIN_USES)
    min_rate = float(min_rate if min_rate is not None else PROMOTE_MIN_RATE)
    promoted = []
    for c in caps.list_capabilities(status="shadow"):
        uses = int(c.get("uses") or 0)
        rate = c.get("success_rate")
        if uses < min_uses:
            continue
        if rate is None or float(rate) < min_rate:
            continue
        try:
            caps.set_status(c["cap_id"], "active")
            audit("promote", cap_id=c["cap_id"], uses=uses, success_rate=rate,
                  min_uses=min_uses, min_rate=min_rate)
            promoted.append(c["cap_id"])
        except Exception:
            continue
    return promoted

def maybe_promote(cap_id):
    """单能力检查：shadow 且达到阈值 → active；返回是否晋级"""
    import capabilities as caps
    c = caps.get_capability(cap_id)
    if not c or c.get("status") != "shadow":
        return False
    uses = int(c.get("uses") or 0)
    rate = c.get("success_rate")
    if uses < PROMOTE_MIN_USES:
        return False
    if rate is None or float(rate) < PROMOTE_MIN_RATE:
        return False
    try:
        caps.set_status(cap_id, "active")
        audit("promote", cap_id=cap_id, uses=uses, success_rate=rate, auto=True)
        return True
    except Exception:
        return False
