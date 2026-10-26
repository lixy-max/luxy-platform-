# -*- coding: utf-8 -*-
"""sandbox_runner.py —— 在受限子进程中执行已安装能力（被 sandbox_exec 调用）

stdin: JSON {"module": "...", "function": "...", "args": [...], "kwargs": {...}}
stdout: JSON {"ok": true, "result": ...} 或 {"ok": false, "error": "..."}

安全：本文件只做"导入一个函数并调用"，不做任何网络/文件系统操作；
      资源限制由父进程（sandbox_exec）通过 rlimit/timeout 施加。
"""
import importlib
import json
import os
import sys

# P4：只把"本能力安装目录"放进 sys.path（配合 python -S -P，隔离项目 venv/用户 site）
_TARGET = os.environ.get("SANDBOX_TARGET", "")
if _TARGET:
    sys.path.insert(0, _TARGET)


def _safe(o, depth=0):
    """把返回值转成 JSON 友好（限制深度/长度）"""
    if depth > 4:
        return str(o)[:2000]
    if o is None or isinstance(o, (bool, int, float, str)):
        return o if not isinstance(o, str) else o[:4000]
    if isinstance(o, (list, tuple)):
        return [_safe(x, depth + 1) for x in o[:50]]
    if isinstance(o, dict):
        return {str(k)[:80]: _safe(v, depth + 1) for k, v in list(o.items())[:50]}
    return str(o)[:2000]


def main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": "请求 JSON 解析失败: %s" % e}))
        return 2
    module = str(req.get("module") or "")
    function = str(req.get("function") or "")
    args = req.get("args") or []
    kwargs = req.get("kwargs") or {}
    if not module or not function:
        print(json.dumps({"ok": False, "error": "module/function 必填"}))
        return 2
    try:
        mod = importlib.import_module(module)
        fn = getattr(mod, function, None)
        if not callable(fn):
            print(json.dumps({"ok": False, "error": "函数不存在或不可调用: %s.%s" % (module, function)}))
            return 3
        out = fn(*args, **kwargs)
        print(json.dumps({"ok": True, "result": _safe(out)}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:500])},
                         ensure_ascii=False))
        return 4


if __name__ == "__main__":
    sys.exit(main())
