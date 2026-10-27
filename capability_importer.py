# -*- coding: utf-8 -*-
"""S25 · 能力包吸收器（Manifest Importer + 沙箱验证）

承接 S24：Manifest 只做校验/评分/注册 shadow；S25 让它真正"装进来"：
  validate → score(decision) → **沙箱**（写文件/隔离执行/静态安全扫描）
  → 门禁（smoke / eval / 延迟 / 安全）→ 通过则注册 active；失败则记录并拒绝
  → 三种吸收模式：
      direct  : 直接移植（内联 Python 工具 + smoke 测试，沙箱真实运行）
      rebuild : 兼容借鉴重建（接口不兼容 → 生成适配层/工作流骨架 + lint 门禁）
      train   : 模型/LoRA（→ 交给 S21 训练调度出计划，状态 pending_training）

产物：
  data/hive/sandbox/<capability_id>/        沙箱目录（代码/工作流/运行日志）
  data/hive/imports/<capability_id>.json    吸收记录（门禁/证据/状态）
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
SANDBOX_DIR = pathlib.Path(os.environ.get("HIVE_SANDBOX_DIR", str(PROJECT_DIR / "data" / "hive" / "sandbox")))
IMPORT_DIR = pathlib.Path(os.environ.get("HIVE_IMPORT_DIR", str(PROJECT_DIR / "data" / "hive" / "imports")))
MANIFEST_DIR = pathlib.Path(os.environ.get("HIVE_MANIFEST_DIR", str(PROJECT_DIR / "data" / "hive" / "manifests")))

BANNED_PATTERNS = [
    (r"\bsubprocess\b", "禁止 subprocess（沙箱内不允许起子进程）"),
    (r"\bos\.system\b", "禁止 os.system"),
    (r"\bos\.popen\b", "禁止 os.popen"),
    (r"\brm\s+-rf\b", "危险命令 rm -rf"),
    (r"\bshutil\.rmtree\b", "禁止 rmtree"),
    (r"\b__import__\s*\(", "禁止动态 __import__"),
    (r"\beval\s*\(", "禁止 eval"),
    (r"\bexec\s*\(", "禁止 exec"),
    (r"\bsocket\b", "禁止 socket/网络"),
    (r"\brequests\.", "沙箱内禁止网络请求"),
    (r"(api[_-]?key|secret|token)\s*=\s*['\"][A-Za-z0-9_\-]{20,}", "疑似硬编码密钥"),
]
ALLOWED_MODES = ("auto", "direct", "rebuild", "train")


def _safe_path(name):
    p = str(name).replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/") or not p.strip():
        return None
    return p


def _write_record(cap_id, rec):
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", str(cap_id))
    path = IMPORT_DIR / (safe + ".json")
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def list_imports(limit=20):
    try:
        files = sorted(IMPORT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return []
    out = []
    for p in files[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _security_scan(files):
    issues = []
    for path, content in files.items():
        for pat, why in BANNED_PATTERNS:
            if re.search(pat, content or "", re.I):
                issues.append({"file": path, "pattern": pat, "why": why})
    return issues


def _deny_record(manifest, reason, extra=None):
    rec = {"ok": False, "capability_id": (manifest or {}).get("capability_id"),
           "decision": "deny", "reason": reason,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if extra:
        rec.update(extra)
    rec["record_path"] = _write_record(rec["capability_id"], rec)
    return rec


def import_manifest(manifest, mode="auto", dry_run=False, sandbox_timeout=15):
    """吸收一个能力包（含沙箱验证/门禁/注册）"""
    t0 = time.time()
    rec = {"ok": False, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "capability_id": (manifest or {}).get("capability_id"),
           "mode_requested": mode, "dry_run": bool(dry_run), "gates": {}}
    if mode not in ALLOWED_MODES:
        return _deny_record(manifest, "mode 非法: %r（可选 %s）" % (mode, ALLOWED_MODES))
    try:
        import capability_manifest as cm
        import capability_registry as cr
    except Exception as e:
        return _deny_record(manifest, "依赖模块不可用: %s" % str(e)[:150])
    errs = cm.validate_manifest(manifest)
    if errs:
        return _deny_record(manifest, "Manifest 校验失败", {"validation_errors": errs})
    score = cm.score_compat(manifest)
    rec["score"] = score
    decision = mode if mode != "auto" else score["decision"]
    if decision == "deny":
        return _deny_record(manifest, "兼容评分 <30，拒绝吸收",
                            {"score": score, "record": rec})
    rec["decision"] = decision
    cap_id = str(manifest["capability_id"])
    safe = re.sub(r"[^\w.\-]+", "_", cap_id)
    sdir = SANDBOX_DIR / safe
    rec["sandbox"] = str(sdir)

    # ── 沙箱：写文件（路径安全校验）──
    sandbox = manifest.get("sandbox") or {}
    files = {}
    for art in (sandbox.get("artifacts") or []):
        rel = _safe_path((art or {}).get("path"))
        if not rel:
            return _deny_record(manifest, "artifact 路径非法: %r" % ((art or {}).get("path")),
                                {"record": rec})
        files[rel] = str((art or {}).get("content") or "")
    entry = str(sandbox.get("entrypoint") or "")
    if decision == "direct" and (not files or not entry):
        return _deny_record(manifest, "direct 模式需要 sandbox.artifacts + entrypoint",
                            {"record": rec})
    rec["gates"]["security"] = {"pass": True, "issues": []}
    if files:
        issues = _security_scan(files)
        rec["gates"]["security"] = {"pass": not issues, "issues": issues[:5]}
        if issues:
            rec["ok"] = False
            rec["status"] = "rejected"
            rec["reason"] = "安全门禁未通过（静态扫描）"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            # 注册为 broken 以留痕
            rec["registered"] = cr.register({
                "cap_id": "absorbed." + cap_id, "kind": _kind_of(manifest),
                "name": manifest.get("name"), "status": "broken", "scope": "local",
                "tags": list(manifest.get("capability_tags") or []),
                "evidence": {"import": rec["gates"], "decision": decision},
                "source": "manifest_importer"})
            rec["record_path"] = _write_record(cap_id, rec)
            return rec

    if dry_run:
        rec.update({"ok": True, "status": "planned", "decision": decision,
                    "reason": "dry_run：不写沙箱/不注册"})
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["record_path"] = _write_record(cap_id, rec)
        return rec

    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)
    sdir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        fp = sdir / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")

    # ── 门禁：smoke / eval / latency ──
    gates = rec["gates"]
    gates["schema"] = {"pass": True,
                       "note": "interface.io_schema 存在" if
                               (manifest.get("interface") or {}).get("io_schema") else "缺 io_schema"}
    if decision == "direct" and (sandbox.get("smoke") or (manifest.get("tests") or {}).get("smoke")):
        smoke_cases = sandbox.get("smoke") or []
        if not smoke_cases:
            smoke_cases = [{"input": {}, "expect_contains": ""}]
        smoke_res, latency_ms = _run_direct_tool(sdir, entry, smoke_cases, sandbox_timeout)
        gates["smoke"] = smoke_res
        lat_thr = float(sandbox.get("latency_threshold_ms") or 2000)
        gates["latency"] = {"pass": latency_ms <= lat_thr, "ms": latency_ms, "threshold_ms": lat_thr}
        ev = (sandbox.get("eval") or (manifest.get("tests") or {}).get("eval") or {})
        if ev and ev.get("cases"):
            eval_res = _run_eval(sdir, entry, ev, sandbox_timeout)
            gates["eval"] = eval_res
        all_pass = all(g.get("pass", True) for g in gates.values())
        if all_pass:
            return _register_and_finish(manifest, cap_id, rec, gates, sdir, "active",
                                        "direct 吸收成功：沙箱+门禁全通过", t0)
        rec.update({"ok": False, "status": "rejected",
                    "reason": "门禁未通过: " + ", ".join(k for k, g in gates.items()
                                                       if not g.get("pass", True))})
        rec["registered"] = cr.register({
            "cap_id": "absorbed." + cap_id, "kind": _kind_of(manifest),
            "name": manifest.get("name"), "status": "broken", "scope": "local",
            "tags": list(manifest.get("capability_tags") or []),
            "evidence": {"gates": gates, "decision": decision},
            "source": "manifest_importer"})
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["record_path"] = _write_record(cap_id, rec)
        return rec

    if decision == "rebuild":
        adapter, wf = _gen_rebuild_assets(manifest)
        (sdir / "adapter.py").write_text(adapter, encoding="utf-8")
        (sdir / "workflow.json").write_text(json.dumps(wf, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        lint_ok, lint_errs = True, []
        try:
            import workflow_engine as we
            errs, _warns = we.lint_config(wf)
            lint_ok = not errs
            lint_errs = errs
        except Exception as e:
            lint_ok = False
            lint_errs = [str(e)[:150]]
        gates["lint"] = {"pass": lint_ok, "errors": lint_errs[:5]}
        if lint_ok:
            return _register_and_finish(
                manifest, cap_id, rec, gates, sdir, "shadow",
                "rebuild：适配层/工作流骨架已生成并通过 lint（待开发完善+评测后晋级）", t0)
        rec.update({"ok": False, "status": "rejected", "reason": "lint 未通过"})
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["record_path"] = _write_record(cap_id, rec)
        return rec

    # train 模式：交给 S21 训练调度出计划（不真实起训）
    gates["dataset"] = {"pass": bool(manifest.get("data") or manifest.get("runtime")),
                        "note": "data/runtime 需求已声明"}
    plan = {}
    try:
        import hive_trainer as tr
        gpu = manifest.get("runtime", {}).get("gpu") or {}
        need = float(gpu.get("vram_gb") or 6.0) or 6.0
        plan = tr.plan_training(need)
    except Exception as e:
        plan = {"action": "unknown", "error": str(e)[:150]}
    rec["train_plan"] = plan
    return _register_and_finish(manifest, cap_id, rec, gates, sdir, "pending_training",
                                "train：训练计划已生成（S21 调度），待训练/评测后晋级", t0)


def _kind_of(manifest):
    return {"tool": "tool", "model": "model", "lora": "lora", "workflow": "workflow",
            "knowledge_pack": "knowledge", "service": "tool"}.get(
        str(manifest.get("type")), "tool")


def _register_and_finish(manifest, cap_id, rec, gates, sdir, status, message, t0):
    try:
        import capability_registry as cr
        kind = _kind_of(manifest)
        tags = list(manifest.get("capability_tags") or [])
        if not tags:
            tags = re.findall(r"[a-zA-Z0-9_]{2,}", str(manifest.get("name") or "").lower())[:6]
        reg = cr.register({
            "cap_id": "absorbed." + cap_id, "kind": kind, "name": manifest.get("name"),
            "status": status, "scope": "local", "tags": tags,
            "io": (manifest.get("interface") or {}).get("io_schema") or {},
            "requires": {"runtime": manifest.get("runtime"),
                         "license": manifest.get("license")},
            "evidence": {"gates": gates, "score": rec.get("score", {}).get("score"),
                         "sandbox": str(sdir)},
            "limits": {"sensitivity": (manifest.get("data") or {}).get("sensitivity")},
            "mount": {"sandbox": str(sdir), "ready": status == "active"},
            "version": str(manifest.get("version") or "v1"),
            "source": "manifest_importer"})
    except Exception as e:
        reg = {"ok": False, "error": str(e)[:200]}
    rec.update({"ok": status in ("active", "shadow", "pending_training"),
                "status": status, "reason": message, "registered": reg,
                "gates": gates})
    rec["elapsed_s"] = round(time.time() - t0, 2)
    # 落盘 Manifest
    try:
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        mpath = MANIFEST_DIR / (re.sub(r"[^\w.\-]+", "_", cap_id) + ".json")
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        rec["manifest_path"] = str(mpath)
    except Exception:
        pass
    rec["record_path"] = _write_record(cap_id, rec)
    return rec


def _runner_script(entry, payload, expect_contains=None):
    """在沙箱目录执行 entrypoint.run(payload)，输出 JSON 结果"""
    return (
        "import json, sys, importlib.util, time\n"
        "spec = importlib.util.spec_from_file_location('_tool', %r)\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "payload = json.loads(sys.argv[1])\n"
        "t0 = time.time()\n"
        "out = mod.run(payload)\n"
        "print('__HIVE_RESULT__' + json.dumps({'output': out, 'latency_ms': (time.time()-t0)*1000}, ensure_ascii=False))\n"
    ) % entry


def _run_direct_tool(sdir, entry, cases, timeout):
    results = []
    latency_ms = 0.0
    for c in cases:
        payload = json.dumps((c or {}).get("input") or {}, ensure_ascii=False)
        script = _runner_script(entry, payload)
        rc, out, err = _run_python(sdir, script, payload, timeout)
        parsed = _parse_result(out)
        got = json.dumps(parsed.get("output") if parsed else None, ensure_ascii=False)
        expect = str((c or {}).get("expect_contains") or "")
        ok = rc == 0 and parsed is not None and (expect in got if expect else parsed is not None)
        results.append({"ok": ok, "got": got[:300], "expect_contains": expect,
                        "stderr": err[-300:]})
        if parsed and parsed.get("latency_ms"):
            latency_ms = max(latency_ms, float(parsed["latency_ms"]))
    return {"pass": all(r["ok"] for r in results), "cases": results}, round(latency_ms, 2)


def _run_eval(sdir, entry, ev, timeout):
    metric = str(ev.get("metric") or "accuracy")
    thr = float(ev.get("threshold") or 0.8)
    ok_n = 0
    rows = []
    for c in (ev.get("cases") or [])[:20]:
        payload = json.dumps((c or {}).get("input") or {}, ensure_ascii=False)
        rc, out, err = _run_python(sdir, _runner_script(entry, payload), payload, timeout)
        parsed = _parse_result(out)
        got = parsed.get("output") if parsed else None
        want = (c or {}).get("expect")
        ok = rc == 0 and got == want
        ok_n += 1 if ok else 0
        rows.append({"ok": ok, "got": str(got)[:200], "expect": str(want)[:200]})
    acc = round(ok_n / max(1, len(rows)), 3)
    return {"pass": acc >= thr, "metric": metric, "score": acc,
            "threshold": thr, "cases": rows[:10]}


def _run_python(cwd, script, payload_json="{}", timeout=15):
    """在沙箱子目录执行 python 脚本（隔离 env，超时击杀）"""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin",
           "PYTHONPATH": "", "HOME": str(cwd), "LANG": "C.UTF-8"}
    try:
        p = subprocess.run([sys.executable, "-I", "-c", script, payload_json],
                           cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, p.stdout[-4000:], p.stderr[-2000:]
    except subprocess.TimeoutExpired:
        return -9, "", "timeout"
    except Exception as e:
        return -1, "", str(e)[:300]


def _parse_result(stdout):
    for ln in str(stdout or "").splitlines():
        if ln.startswith("__HIVE_RESULT__"):
            try:
                return json.loads(ln[len("__HIVE_RESULT__"):])
            except Exception:
                return None
    return None


def _gen_rebuild_assets(manifest):
    """生成适配层 + 工作流骨架（用于不兼容接口的借鉴重建）"""
    cap = manifest.get("capability_id")
    name = manifest.get("name") or cap
    adapter = (
        '"""S25 rebuild adapter: %s\n\n'
        "接口不兼容：原协议=%s；本地用 workflow/工具链重建。\n"
        "TODO(S25+): 在此实现输入适配 → 平台工具调用。\n"
        '"""\n\n'
        "def run(payload):\n"
        "    # 占位：由开发者按工作流骨架实现\n"
        "    return {'ok': False, 'need_impl': True, 'input': payload}\n"
    ) % (name, (manifest.get("interface") or {}).get("protocol"))
    wf = {
        "workflow_name": "rebuild_" + str(cap),
        "schema_version": "2.0",
        "nodes": [
            {"id": "n0", "type": "input", "input_from": [], "position": {"x": 0, "y": 0}},
            {"id": "n1", "type": "process", "input_from": ["n0"],
             "prompt": "适配/重建说明：" + str((manifest.get("interface") or {}).get("protocol")),
             "provider": "vllm", "output_format": "text", "max_tokens": 800,
             "temperature": 0.2, "position": {"x": 240, "y": 0}},
            {"id": "n2", "type": "aggregate", "input_from": ["n1"], "prompt": "汇总 {n1}",
             "provider": "vllm", "output_format": "text", "max_tokens": 800,
             "temperature": 0.2, "position": {"x": 480, "y": 0}},
        ],
    }
    return adapter, wf


def run_absorbed(cap_id, payload):
    """执行已吸收的 direct 能力（沙箱内真实运行）"""
    try:
        import capability_registry as cr
        entry = cr.get("absorbed." + str(cap_id)) or cr.get(cap_id)
        if not entry:
            return {"ok": False, "error": "未找到能力: %s" % cap_id}
        if entry.get("status") != "active":
            return {"ok": False, "error": "能力非 active（当前 %r）" % entry.get("status")}
        mount = entry.get("mount") or {}
        sdir = pathlib.Path(mount.get("sandbox") or "")
        if not sdir.is_dir():
            return {"ok": False, "error": "沙箱目录不存在: %s" % sdir}
        # entrypoint 从 manifest 读取
        mpath = MANIFEST_DIR / (re.sub(r"[^\w.\-]+", "_", str(cap_id)) + ".json")
        entrypoint = "tool.py"
        if mpath.exists():
            try:
                m = json.loads(mpath.read_text(encoding="utf-8"))
                entrypoint = (m.get("sandbox") or {}).get("entrypoint") or entrypoint
            except Exception:
                pass
        if not (sdir / entrypoint).exists():
            return {"ok": False, "error": "entrypoint 不存在: %s" % (sdir / entrypoint)}
        t0 = time.time()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        rc, out, err = _run_python(sdir, _runner_script(entrypoint, payload_json),
                                   payload_json, 15)
        parsed = _parse_result(out)
        if rc != 0 or not parsed:
            return {"ok": False, "error": (err or "执行失败")[-400:], "rc": rc}
        return {"ok": True, "capability_id": cap_id, "output": parsed.get("output"),
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "sandbox": str(sdir)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


def stats():
    return {"sandbox_dir": str(SANDBOX_DIR), "import_dir": str(IMPORT_DIR),
            "imports": len(list_imports(200)),
            "modes": list(ALLOWED_MODES),
            "banned_patterns": len(BANNED_PATTERNS)}
