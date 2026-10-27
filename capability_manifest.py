# -*- coding: utf-8 -*-
"""S24 · 云端能力说明书（Capability Manifest）

云端教师/代理产出"新手脚"时必须按本 schema 提交提案；本地 importer 解析并决策：
  直接移植（direct） / 兼容借鉴重建（rebuild） / 下载微调训练（train） / 拒绝（deny）

评分：接口 30 / 运行时 25 / 许可 15 / 安全 15 / 性能 15 = 100
  ≥75 direct；50~75 rebuild；30~50 train；<30 deny
"""
import json
import os
import pathlib
import re
import time

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
MANIFEST_DIR = pathlib.Path(os.environ.get(
    "HIVE_MANIFEST_DIR", str(PROJECT_DIR / "data" / "hive" / "manifests")))

REQUIRED = ["manifest_version", "capability_id", "name", "type", "interface",
            "runtime", "license", "tests"]
VALID_TYPES = ("tool", "model", "lora", "workflow", "knowledge_pack", "service")
INTERFACE_PROTOCOLS = ("openapi", "mcp", "http", "python", "grpc", "cli", "vllm")
GOOD_LICENSES = ("apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause", "isc")
WEAK_LICENSES = ("lgpl-3.0", "lgpl-2.1", "mpl-2.0", "gpl-3.0", "gpl-2.0")

LOCAL_ENV = {
    "os": "linux",
    "python": "3.12",
    "gpu": {"gpu0": {"name": "RTX 5060 Ti", "vram_gb": 16.0, "arch": "sm_120"},
            "gpu1": {"name": "RTX 3060", "vram_gb": 12.0, "arch": "sm_86"}},
    "vllm": "0.11.0",
    "docker": False,
}


def validate_manifest(m):
    errs = []
    if not isinstance(m, dict):
        return ["Manifest 不是对象"]
    for k in REQUIRED:
        if k not in m or m[k] in (None, "", {}):
            errs.append("缺少字段: " + k)
    if m.get("type") and m["type"] not in VALID_TYPES:
        errs.append("type 非法: %r（可选 %s）" % (m["type"], list(VALID_TYPES)))
    iface = m.get("interface") or {}
    if isinstance(iface, dict):
        proto = str(iface.get("protocol") or "").lower()
        if proto and proto not in INTERFACE_PROTOCOLS:
            errs.append("interface.protocol 不支持: %r" % proto)
        if not iface.get("io_schema") and not iface.get("spec_url"):
            errs.append("interface 需要 io_schema 或 spec_url")
    else:
        errs.append("interface 必须是对象")
    rt = m.get("runtime") or {}
    if isinstance(rt, dict):
        if rt.get("os") and "linux" not in str(rt.get("os")).lower():
            errs.append("runtime.os 非 linux（当前只支持 WSL/Linux）")
        gpu = rt.get("gpu") or {}
        if isinstance(gpu, dict) and gpu.get("vram_gb") and float(gpu["vram_gb"]) > 16:
            errs.append("runtime.gpu.vram_gb 超过单卡上限 16GB")
    lic = str(m.get("license") or "").lower()
    if not lic:
        errs.append("缺少 license")
    tests = m.get("tests") or {}
    if isinstance(tests, dict) and not (tests.get("smoke") or tests.get("eval")):
        errs.append("tests 需要 smoke 或 eval")
    return errs


def score_compat(m, env=None):
    env = env or LOCAL_ENV
    bd = {"interface": 0, "runtime": 0, "license": 0, "security": 0, "performance": 0}
    notes = []
    # 接口（30）
    iface = m.get("interface") or {}
    proto = str(iface.get("protocol") or "").lower()
    if proto in INTERFACE_PROTOCOLS:
        bd["interface"] += 15
    else:
        notes.append("协议不在支持列表")
    if iface.get("io_schema"):
        bd["interface"] += 10
    elif iface.get("spec_url"):
        bd["interface"] += 6
    if str(iface.get("version") or "").strip():
        bd["interface"] += 5
    # 运行时（25）
    rt = m.get("runtime") or {}
    if "linux" in str(rt.get("os") or "linux").lower():
        bd["runtime"] += 8
    py = str(rt.get("python") or "3.12")
    if re.search(r"3\.1[0-2]", py) or py in ("", "any"):
        bd["runtime"] += 6
    deps = rt.get("deps") or []
    if isinstance(deps, list) and len(deps) <= 15:
        bd["runtime"] += 5
    gpu = rt.get("gpu") or {}
    vram = float(gpu.get("vram_gb") or 0) if isinstance(gpu, dict) else 0
    if vram <= 0 or vram <= 12:
        bd["runtime"] += 6
    elif vram <= 16:
        bd["runtime"] += 3
    else:
        notes.append("显存需求超过本地单卡")
    # 许可（15）
    lic = str(m.get("license") or "").lower()
    if lic in GOOD_LICENSES:
        bd["license"] += 15
    elif lic in WEAK_LICENSES:
        bd["license"] += 8
        notes.append("弱 copyleft，可借鉴重建但不建议直接分发")
    elif lic:
        bd["license"] += 4
    # 安全（15）
    envv = m.get("env") or {}
    secrets = envv.get("secrets") or []
    sens = str((m.get("data") or {}).get("sensitivity") or "low").lower()
    bd["security"] += 8
    if sens in ("low", "medium"):
        bd["security"] += 5
    elif sens == "high":
        bd["security"] += 1
        notes.append("高敏感数据，需额外审批")
    if not secrets:
        bd["security"] += 2
    # 性能（15）
    tests = m.get("tests") or {}
    ev = tests.get("eval") or {}
    metric = (ev.get("metric") if isinstance(ev, dict) else "") or ""
    thr = float((ev.get("threshold") if isinstance(ev, dict) else 0) or 0)
    if metric and thr >= 0.7:
        bd["performance"] += 15
    elif metric:
        bd["performance"] += 8
    else:
        bd["performance"] += 4
    total = sum(bd.values())
    if total >= 75:
        decision = "direct"
    elif total >= 50:
        decision = "rebuild"
    elif total >= 30:
        decision = "train"
    else:
        decision = "deny"
    return {"score": total, "breakdown": bd, "decision": decision,
            "thresholds": {"direct": 75, "rebuild": 50, "train": 30},
            "notes": notes}


def absorb(manifest, dry_run=True):
    """吸收一个能力包：validate → score → (dry_run 只出计划) → 注册 shadow/pending"""
    errs = validate_manifest(manifest)
    out = {"ok": False, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "capability_id": (manifest or {}).get("capability_id"),
           "dry_run": bool(dry_run)}
    if errs:
        out.update({"validation_errors": errs,
                    "decision": "deny", "reason": "Manifest 校验失败"})
        return out
    sc = score_compat(manifest)
    out["score"] = sc
    steps = []
    decision = sc["decision"]
    steps.append("① Manifest 校验通过")
    steps.append("② 兼容评分 %d/100（接口%d/运行时%d/许可%d/安全%d/性能%d）→ %s"
                 % (sc["score"], sc["breakdown"]["interface"],
                    sc["breakdown"]["runtime"], sc["breakdown"]["license"],
                    sc["breakdown"]["security"], sc["breakdown"]["performance"],
                    decision))
    if decision == "deny":
        out.update({"decision": "deny", "steps": steps,
                    "ok": False, "reason": "兼容性不足（<30），拒绝吸收"})
        return out
    if decision == "direct":
        steps.append("③ [沙箱] 直接移植：拉取/安装依赖 → smoke test（S25 实现）")
    elif decision == "rebuild":
        steps.append("③ [沙箱] 兼容借鉴重建：生成适配层/重写工作流 → smoke test（S25 实现）")
    else:
        steps.append("③ [沙箱] 下载模型/LoRA 后本地微调/蒸馏 → 评测（S25 实现）")
    steps.append("④ 评测门禁（质量/schema/延迟）通过后登记 shadow")
    steps.append("⑤ 灰度/晋级（S22 热加载流水线）+ 能力注册表置 active")
    out.update({"decision": decision, "steps": steps, "ok": True})
    if dry_run:
        out["reason"] = "dry_run：仅输出吸收计划，不落盘"
        return out
    # 落盘 Manifest
    try:
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        path = MANIFEST_DIR / ("%s.json" % re.sub(r"[^\w.-]+", "_", str(manifest["capability_id"])))
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        out["manifest_path"] = str(path)
    except Exception as e:
        out["manifest_error"] = str(e)[:200]
    # 注册能力（shadow，等待沙箱/评测）
    try:
        import capability_registry as cr
        kind = {"tool": "tool", "model": "model", "lora": "lora",
                "workflow": "workflow", "knowledge_pack": "knowledge",
                "service": "tool"}.get(manifest.get("type"), "tool")
        tags = list((manifest.get("capability_tags") or []) or
                    re.findall(r"[a-z0-9_]{2,}", str(manifest.get("name") or "").lower()))[:8]
        r = cr.register({"cap_id": "absorbed." + str(manifest["capability_id"]),
                         "kind": kind, "name": manifest.get("name"),
                         "status": "shadow", "scope": "local", "tags": tags,
                         "io": (manifest.get("interface") or {}).get("io_schema") or {},
                         "requires": {"runtime": manifest.get("runtime"),
                                      "license": manifest.get("license")},
                         "evidence": {"compat_score": sc["score"],
                                      "decision": decision,
                                      "eval": (manifest.get("tests") or {}).get("eval")},
                         "limits": {"sensitivity": (manifest.get("data") or {}).get("sensitivity")},
                         "mount": {"pending_sandbox": True},
                         "version": str(manifest.get("version") or "v1"),
                         "source": "manifest_importer"})
        out["registered"] = r
    except Exception as e:
        out["register_error"] = str(e)[:200]
    return out


def list_manifests(limit=20):
    out = []
    try:
        for p in sorted(MANIFEST_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime,
                        reverse=True)[:limit]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"file": p.name, "capability_id": d.get("capability_id"),
                            "name": d.get("name"), "type": d.get("type")})
            except Exception:
                continue
    except Exception:
        pass
    return out


def stats():
    return {"manifest_dir": str(MANIFEST_DIR),
            "manifests": len(list_manifests(100)),
            "protocols": list(INTERFACE_PROTOCOLS),
            "licenses_good": list(GOOD_LICENSES),
            "thresholds": {"direct": 75, "rebuild": 50, "train": 30},
            "local_env": LOCAL_ENV}
