# -*- coding: utf-8 -*-
"""sandbox_exec.py —— P4：已安装能力（ext:）的执行器

职责：
  · 校验能力必须是 external + installed + executable/entry 合法
  · 在**受限子进程**里运行 sandbox_runner.py：
      - PYTHONPATH 指向该能力的 --target 安装目录（只暴露该包）
      - 干净环境（HOME 指向沙箱、清空代理、C.UTF-8）
      - rlimit：地址空间 512MB / CPU 时间 = timeout / 文件描述符 64
      - subprocess timeout（默认 30s）
  · 记录审计 + 能力使用统计（成功/失败）
  · 返回结构化结果（JSON 友好）

诚实边界：
  · 这是"进程级隔离 + 资源限额"，**不是强沙箱**（无 namespace/无网络命名空间；
    运行时代码理论上仍可访问本机网络与文件系统——P4.2 迁移 Docker/独立用户）
  · 只允许调用登记过的 module.function，不做任意代码执行
"""
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SANDBOX_DIR = Path(os.environ.get("SANDBOX_DIR", str(PROJECT_DIR / "data" / "sandbox")))
RUNNER = PROJECT_DIR / "sandbox_runner.py"
PY = os.environ.get("SANDBOX_PY", sys.executable)
DEFAULT_TIMEOUT = int(os.environ.get("SANDBOX_EXEC_TIMEOUT", "30"))
MAX_AS = int(os.environ.get("SANDBOX_EXEC_MAX_AS_MB", "512")) * 1024 * 1024


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _audit(rec):
    try:
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        with open(SANDBOX_DIR / "exec_audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(rec, at=_now()), ensure_ascii=False) + "\n")
    except Exception:
        pass


def _limits(timeout):
    def _set():
        try:
            resource.setrlimit(resource.RLIMIT_AS, (MAX_AS, MAX_AS))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (int(timeout) + 5, int(timeout) + 5))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass
    return _set


def _cap_fields(cap):
    entry = cap.get("entry") or {}
    return {
        "cap_id": cap.get("cap_id"), "kind": cap.get("kind"),
        "installed": bool(cap.get("installed")),
        "install_path": cap.get("install_path"),
        "module": str(entry.get("module") or ""),
        "function": str(entry.get("function") or ""),
        "args_schema": cap.get("args_schema"),
    }


def executable_cap(cap):
    """检查能力是否可执行；返回 (ok, reason)"""
    if not cap:
        return False, "能力不存在"
    if cap.get("kind") != "external":
        return False, "不是 external 能力（LoRA 能力走工蜂）"
    if not cap.get("installed"):
        return False, "未安装（先 /chimera/{id}/install）"
    f = _cap_fields(cap)
    if not f["module"] or not f["function"]:
        return False, "缺少 entry（module.function 未登记）"
    p = Path(str(f["install_path"] or ""))
    if not p.is_dir():
        return False, "安装目录不存在: %s" % p
    return True, "ok"


def execute_capability(cap, args=None, kwargs=None, timeout=None):
    """执行已安装能力；返回 {ok, result?, error?, meta}"""
    ok, why = executable_cap(cap)
    if not ok:
        return {"ok": False, "error": why, "meta": {"cap_id": (cap or {}).get("cap_id")}}
    # ★ P5 治理：KILL 开关 / 日预算 / 每分钟频率
    try:
        import governance as _gov
        _allowed, _why = _gov.check_exec_allowed()
        if not _allowed:
            _gov.audit("exec_blocked", cap_id=(cap or {}).get("cap_id"), reason=_why)
            return {"ok": False, "error": "治理层拒绝: %s" % _why,
                    "meta": {"cap_id": (cap or {}).get("cap_id"), "blocked": True}}
    except Exception:
        pass
    f = _cap_fields(cap)
    timeout = int(timeout or DEFAULT_TIMEOUT)
    _args = list(args or [])
    _kwargs = dict(kwargs or {})
    # 位置参数映射：常见库（如 tabulate）首参是位置参数；args_schema 若声明 positional 则迁移
    _schema = cap.get("args_schema") or {}
    _pos = _schema.get("positional") if isinstance(_schema, dict) else None
    if _pos:
        for k in list(_pos):
            if k in _kwargs:
                _args.insert(0, _kwargs.pop(k))
    elif not _args and "data" in _kwargs:
        _args = [_kwargs.pop("data")]
    req = {"module": f["module"], "function": f["function"],
           "args": _args, "kwargs": _kwargs}
    env = {
        # ★ P4 修复（实测）：原来用 PYTHONPATH + python -I（-I 忽略 PYTHONPATH）
        #   → 实际导入的是项目 venv 里的包（隔离被绕过 ✗）
        #   现在：python -S -P（不加载 site-packages、不把脚本目录放 sys.path）
        #        + SANDBOX_TARGET 由 runner 显式插入 sys.path ✓
        "SANDBOX_TARGET": str(f["install_path"]),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(SANDBOX_DIR / "home"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
        "NO_PROXY": "*", "no_proxy": "*",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    cwd = tempfile.mkdtemp(prefix="sbx_", dir=str(SANDBOX_DIR))
    t0 = time.time()
    try:
        r = subprocess.run([PY, "-S", "-P", str(RUNNER)], input=json.dumps(req),
                           capture_output=True, text=True, timeout=timeout,
                           env=env, cwd=cwd, preexec_fn=_limits(timeout))
    except subprocess.TimeoutExpired:
        dur = round(time.time() - t0, 2)
        _audit({"action": "exec_timeout", "cap_id": f["cap_id"], "timeout": timeout})
        _record_use(f["cap_id"], success=False)
        try:
            import governance as _gov3
            _gov3.record_exec(f["cap_id"], ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "执行超时（%ss）" % timeout,
                "meta": {"cap_id": f["cap_id"], "duration": dur}}
    except Exception as e:
        _audit({"action": "exec_error", "cap_id": f["cap_id"], "error": str(e)[:300]})
        return {"ok": False, "error": "执行异常: %s" % str(e)[:300],
                "meta": {"cap_id": f["cap_id"]}}
    dur = round(time.time() - t0, 2)
    shutil.rmtree(cwd, ignore_errors=True)
    out = (r.stdout or "").strip().splitlines()
    data = {}
    if out:
        try:
            data = json.loads(out[-1])
        except Exception:
            data = {"ok": False, "error": "结果解析失败: %s" % (out[-1][:300])}
    else:
        data = {"ok": False, "error": "无输出（stderr: %s）" % (r.stderr or "")[-300:]}
    if r.returncode != 0 and data.get("ok") is None:
        data = {"ok": False, "error": (r.stderr or "")[-400:]}
    okk = bool(data.get("ok"))
    _audit({"action": "exec", "cap_id": f["cap_id"], "module": f["module"],
            "function": f["function"], "ok": okk, "duration": dur,
            "error": (data.get("error") or "")[:300]})
    _record_use(f["cap_id"], success=okk)
    try:
        import governance as _gov2
        _gov2.record_exec(f["cap_id"], ok=okk)
    except Exception:
        pass
    res = {"ok": okk, "meta": {"cap_id": f["cap_id"], "duration": dur,
                               "module": f["module"], "function": f["function"]}}
    if okk:
        res["result"] = data.get("result")
    else:
        res["error"] = data.get("error") or "未知错误"
    return res


def _record_use(cap_id, success=True):
    try:
        import capabilities as caps
        caps.record_use(cap_id, success=success)
    except Exception:
        pass


def list_executable():
    """列出可执行的 external 能力"""
    out = []
    try:
        import capabilities as caps
        for c in caps.list_capabilities():
            if c.get("kind") == "external" and c.get("installed"):
                ok, why = executable_cap(c)
                out.append({"cap_id": c.get("cap_id"), "executable": ok, "reason": why,
                            "module": (c.get("entry") or {}).get("module"),
                            "function": (c.get("entry") or {}).get("function")})
    except Exception:
        pass
    return out
