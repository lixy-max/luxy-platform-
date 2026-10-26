# -*- coding: utf-8 -*-
"""P3 沙箱安装器测试：dry-run 策略闸门 + install（全打桩，不真装包）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sandbox_installer as si

CAND = {"candidate_id": "c1", "id": "astanin/python-tabulate", "source": "github",
        "stars": 2000, "license": "mit"}

META_OK = {"package": "tabulate", "version": "0.10.0", "summary": "table", "license": "MIT",
           "hard_deps_count": 0, "pure_wheel": True,
           "file": {"filename": "tabulate-0.10.0-py3-none-any.whl",
                    "url": "https://files.pythonhosted.org/x.whl",
                    "sha256": "f0b0622e" * 8, "size": 39814, "kind": "bdist_wheel"},
           "requires_dist": []}
GH_OK = {"id": "astanin/python-tabulate", "stars": 2000, "license": "mit",
         "archived": False, "pushed_at": "2026-09-01", "description": "", "language": "Python"}


class TestDryRun(unittest.TestCase):
    def test_map_repo(self):
        names = si.map_repo_to_pypi("astanin/python-tabulate")
        self.assertIn("tabulate", names)
        self.assertIn("python-tabulate", names)

    def test_can_install_pure_wheel(self):
        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", side_effect=lambda p: META_OK if p == "tabulate" else None):
            plan = si.dry_run(CAND)
        self.assertTrue(plan["can_install"])
        self.assertEqual(plan["install_ir"][0]["package"], "tabulate")
        self.assertEqual(plan["install_ir"][0]["sha256"], META_OK["file"]["sha256"])

    def test_reject_non_pure(self):
        meta = dict(META_OK, pure_wheel=False)
        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", return_value=meta):
            plan = si.dry_run(CAND)
        self.assertFalse(plan["can_install"])
        self.assertTrue(any("纯 Python wheel" in r for r in plan["risks"]))

    def test_reject_hard_deps(self):
        meta = dict(META_OK, hard_deps_count=2)
        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", return_value=meta):
            plan = si.dry_run(CAND)
        self.assertFalse(plan["can_install"])
        self.assertTrue(any("必需依赖" in r for r in plan["risks"]))

    def test_reject_unknown_license(self):
        gh = dict(GH_OK, license="")
        meta = dict(META_OK, license="")
        cand = dict(CAND, license="")
        with mock.patch.object(si, "github_repo", return_value=gh), \
             mock.patch.object(si, "pypi_meta", return_value=meta):
            plan = si.dry_run(cand)
        self.assertFalse(plan["can_install"])
        self.assertTrue(any("许可" in r for r in plan["risks"]))

    def test_reject_archived(self):
        gh = dict(GH_OK, archived=True)
        with mock.patch.object(si, "github_repo", return_value=gh), \
             mock.patch.object(si, "pypi_meta", return_value=META_OK):
            plan = si.dry_run(CAND)
        self.assertFalse(plan["can_install"])
        self.assertTrue(any("归档" in r for r in plan["risks"]))

    def test_no_pypi(self):
        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", return_value=None):
            plan = si.dry_run(CAND)
        self.assertFalse(plan["can_install"])
        self.assertTrue(any("PyPI" in r for r in plan["risks"]))


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sandbox_ut_"))
        self._orig = (si.SANDBOX_DIR, si.INSTALLED_FILE)
        si.SANDBOX_DIR = self.tmp
        si.INSTALLED_FILE = self.tmp / "installed.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, si, "SANDBOX_DIR", self._orig[0])
        self.addCleanup(setattr, si, "INSTALLED_FILE", self._orig[1])

    def test_approve_required(self):
        res = si.install(CAND, approve=False)
        self.assertFalse(res["ok"])
        self.assertIn("approve", res["error"])

    def test_install_ok_mocked(self):
        target_holder = {}

        class FakeRun:
            def __init__(self): self.calls = []
            def __call__(self, cmd, **kw):
                self.calls.append(cmd)
                if "pip" in cmd:
                    # 模拟 pip 安装：往 target 写一个文件
                    tgt = Path(cmd[cmd.index("--target") + 1])
                    tgt.mkdir(parents=True, exist_ok=True)
                    (tgt / "tabulate.py").write_text("# fake")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(returncode=0,
                                 stdout=json.dumps({"version": "0.10.0", "ok": True}),
                                 stderr="")

        fake = FakeRun()
        cap_entries = {}

        class FakeCaps:
            @staticmethod
            def get_capability(cid): return cap_entries.get(cid)
            @staticmethod
            def _load(): return {"version": 1, "capabilities": cap_entries}
            @staticmethod
            def _save(reg): cap_entries.update(reg["capabilities"])

        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", side_effect=lambda p: META_OK if p == "tabulate" else None), \
             mock.patch.object(si.subprocess, "run", side_effect=fake), \
             mock.patch.dict(sys.modules, {"capabilities": FakeCaps}):
            res = si.install(CAND, approve=True)
        self.assertTrue(res["ok"], res.get("error"))
        inst = res["installed"]
        self.assertEqual(inst["package"], "tabulate")
        self.assertEqual(inst["version"], "0.10.0")
        self.assertTrue(inst["smoke"]["ok"])
        self.assertTrue(inst["artifact_sha256"])
        self.assertEqual(inst["capability_id"], "ext:github:astanin-python-tabulate")
        self.assertTrue(si.INSTALLED_FILE.exists())
        rows = [json.loads(x) for x in si.INSTALLED_FILE.read_text().splitlines() if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["package"], "tabulate")

    def test_install_rejected_by_gate(self):
        with mock.patch.object(si, "github_repo", return_value=GH_OK), \
             mock.patch.object(si, "pypi_meta", return_value=None):
            res = si.install(CAND, approve=True)
        self.assertFalse(res["ok"])
        self.assertIn("不满足安装条件", res["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
