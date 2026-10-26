# -*- coding: utf-8 -*-
"""P4 沙箱执行器测试：入口校验 / 调用 / 位置参数 / 超时 / 隔离（真子进程，不联网）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sandbox_exec as se

MODULE_SRC = '''
def run(x=None):
    return {"x": x, "ok": True}

def add(a, b):
    return a + b

def boom():
    raise ValueError("bomb")

def sleepy():
    import time
    time.sleep(3)
    return "done"
'''


class TestSandboxExec(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="exec_ut_"))
        self.target = self.tmp / "target"
        self.target.mkdir()
        (self.target / "mymod.py").write_text(MODULE_SRC, encoding="utf-8")
        self._orig = (se.SANDBOX_DIR,)
        se.SANDBOX_DIR = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, se, "SANDBOX_DIR", self._orig[0])

    def _cap(self, module="mymod", function="run", installed=True):
        return {"cap_id": "ext:test:mymod", "kind": "external", "installed": installed,
                "install_path": str(self.target),
                "entry": {"module": module, "function": function},
                "args_schema": {"positional": ["data"]}}

    def test_execute_ok_positional(self):
        r = se.execute_capability(self._cap(), kwargs={"data": 5})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["result"]["x"], 5)

    def test_execute_add(self):
        r = se.execute_capability(self._cap(function="add"), args=[2, 3])
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], 5)

    def test_bad_function(self):
        r = se.execute_capability(self._cap(function="nope"))
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["error"])

    def test_timeout(self):
        r = se.execute_capability(self._cap(function="sleepy"), timeout=1)
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])

    def test_not_installed(self):
        r = se.execute_capability(self._cap(installed=False))
        self.assertFalse(r["ok"])
        self.assertIn("未安装", r["error"])

    def test_not_external(self):
        cap = self._cap()
        cap["kind"] = "lora"
        r = se.execute_capability(cap)
        self.assertFalse(r["ok"])
        self.assertIn("external", r["error"])

    def test_missing_target(self):
        cap = self._cap()
        cap["install_path"] = str(self.tmp / "nope")
        ok, why = se.executable_cap(cap)
        self.assertFalse(ok)
        self.assertIn("安装目录", why)

    def test_isolation_no_venv_packages(self):
        # requests 在项目 venv 里，但不在沙箱 target 里 → 必须导入失败（证明 -S -P 隔离）
        cap = self._cap(module="requests", function="get")
        r = se.execute_capability(cap, args=["https://example.com"], timeout=10)
        self.assertFalse(r["ok"], r)
        self.assertIn("ModuleNotFoundError", str(r.get("error")))

    def test_list_executable(self):
        fake_caps = [{"cap_id": "ext:test:mymod", "kind": "external", "installed": True,
                      "install_path": str(self.target),
                      "entry": {"module": "mymod", "function": "run"}}]
        with mock.patch("capabilities.list_capabilities", return_value=fake_caps):
            rows = se.list_executable()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["executable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
