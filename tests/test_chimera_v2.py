# -*- coding: utf-8 -*-
"""S6 奇美拉 V2 直装测试：pip report 解析 / 依赖安装（打桩）/ 适配器注入 / 能力登记"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chimera_v2 as cv2

REPORT = {"install": [
    {"metadata": {"name": "pdfplumber", "version": "0.11.0"},
     "download_info": {"url": "https://files/x.whl",
                       "archive_info": {"hashes": {"sha256": "abc123"}}}},
    {"metadata": {"name": "pdfminer.six", "version": "2026.1"},
     "download_info": {"url": "https://files/y.whl",
                       "archive_info": {"hashes": {"sha256": "def456"}}}},
]}


class TestChimeraV2(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v2_ut_"))
        self._orig = (cv2.DEPS_DIR, cv2.ADAPTERS_DIR, cv2.INSTALLED_FILE, cv2.AUDIT_FILE)
        cv2.DEPS_DIR = self.tmp / "deps"
        cv2.ADAPTERS_DIR = self.tmp / "adapters"
        cv2.INSTALLED_FILE = self.tmp / "installed.jsonl"
        cv2.AUDIT_FILE = self.tmp / "audit.jsonl"
        cv2.ADAPTERS_DIR.mkdir(parents=True)
        (cv2.ADAPTERS_DIR / "w.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in zip(("DEPS_DIR", "ADAPTERS_DIR", "INSTALLED_FILE", "AUDIT_FILE"), self._orig):
            self.addCleanup(setattr, cv2, k, v)

    def test_parse_report(self):
        p = self.tmp / "report.json"
        p.write_text(json.dumps(REPORT), encoding="utf-8")
        rows = cv2.parse_pip_report(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "pdfplumber")
        self.assertEqual(rows[0]["sha256"], "abc123")

    def test_requires_approve(self):
        with self.assertRaises(ValueError):
            cv2.install_with_deps("pdfplumber", approve=False)

    def test_install_success(self):
        def fake_run(cmd, **kw):
            # 模拟 pip：把 report 写出来
            ridx = cmd.index("--report")
            Path(cmd[ridx + 1]).write_text(json.dumps(REPORT), encoding="utf-8")
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(cv2, "_smoke", return_value={"ok": True}):
            r = cv2.install_with_deps("pdfplumber", wrappers=["w"], approve=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["package_count"], 2)
        self.assertEqual(r["wrappers"], ["w"])
        target = cv2.DEPS_DIR / "pdfplumber"
        self.assertTrue((target / "_luxy_wrappers" / "w.py").exists())
        self.assertTrue(cv2.INSTALLED_FILE.exists())
        self.assertEqual(len(cv2.list_deps_installs()), 1)

    def test_install_fail(self):
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=1, stdout="", stderr="boom")):
            with self.assertRaises(RuntimeError):
                cv2.install_with_deps("x", approve=True)

    def test_install_size_guard(self):
        def fake_run(cmd, **kw):
            ridx = cmd.index("--report")
            Path(cmd[ridx + 1]).write_text(json.dumps(REPORT), encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(cv2, "_dir_mb", return_value=99999), \
             mock.patch.object(cv2, "_smoke", return_value={"ok": True}):
            with self.assertRaises(RuntimeError):
                cv2.install_with_deps("x", approve=True)

    def test_register_capability(self):
        fake_reg = {"capabilities": {}}
        with mock.patch("capabilities._load", return_value=fake_reg), \
             mock.patch("capabilities._save"):
            e = cv2.register_deps_capability(
                "pdfplumber", "ext:pypi:pdfplumber", "pdfplumber",
                "_luxy_wrappers.pdfplumber_wrap", "extract",
                {"positional": ["pdf_path"]}, packages=[{"name": "pdfplumber"}],
                license_="mit", repo="jsvine/pdfplumber")
        self.assertTrue(e["installed"])
        self.assertTrue(e["executable"])
        self.assertEqual(e["entry"]["module"], "_luxy_wrappers.pdfplumber_wrap")
        self.assertEqual(e["status"], "shadow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
