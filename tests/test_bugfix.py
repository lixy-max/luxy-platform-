# -*- coding: utf-8 -*-
"""Bug 修复回归测试：vLLM stale pid（PID 复用）/ stop 快速返回 / 工具硬门 / 云审缓存 / 能力筛选"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))

import vllm_manager  # noqa: E402
import tool_guard  # noqa: E402
import swarm  # noqa: E402


class TestVllmStalePid(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vllm_ut_"))
        self._orig = vllm_manager.PID_FILE
        vllm_manager.PID_FILE = self.tmp / ".vllm.pid"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, vllm_manager, "PID_FILE", self._orig)

    def test_pid_is_vllm_false_for_non_vllm(self):
        self.assertFalse(vllm_manager._pid_is_vllm(os.getpid()))
        self.assertFalse(vllm_manager._pid_is_vllm(999999))

    def test_is_running_rejects_reused_pid(self):
        # 用当前进程 pid 写 PID 文件：进程活着但不是 vLLM → 必须判定 False 并清理
        vllm_manager.PID_FILE.write_text(str(os.getpid()))
        with mock.patch.object(vllm_manager, "find_vllm_pids", return_value=[]):
            running, pid = vllm_manager.is_running()
        self.assertFalse(running)
        self.assertIsNone(pid)
        self.assertFalse(vllm_manager.PID_FILE.exists())

    def test_is_running_dead_pid_cleans_file(self):
        vllm_manager.PID_FILE.write_text("999999")
        running, pid = vllm_manager.is_running()
        self.assertFalse(running)
        self.assertFalse(vllm_manager.PID_FILE.exists())

    def test_stop_when_not_running_is_fast(self):
        with mock.patch.object(vllm_manager, "is_running", return_value=(False, None)), \
             mock.patch.object(vllm_manager, "is_port_responding", return_value=False):
            t0 = time.time()
            ok = vllm_manager.stop_vllm()
            dt = time.time() - t0
        self.assertTrue(ok)
        self.assertLess(dt, 2.0, "未运行时 stop 应快速返回（实际 %.2fs）" % dt)


class TestToolGuard(unittest.TestCase):
    def test_phantom_loaded(self):
        phantom = tool_guard.load_phantom()
        self.assertGreater(len(phantom), 0)
        self.assertIn("browser_open", phantom)

    def test_is_executable(self):
        phantom = tool_guard.load_phantom()
        name = sorted(phantom)[0]
        self.assertFalse(tool_guard.is_executable(name))
        self.assertTrue(tool_guard.is_executable("definitely_not_a_real_tool_xyz"))

    def test_filter_tools(self):
        name = sorted(tool_guard.load_phantom())[0]
        tools = {name: {"desc": "x"}, "real_tool_xyz": {"desc": "y"}}
        ann = tool_guard.filter_tools(tools)
        self.assertFalse(ann[name]["executable"])
        self.assertTrue(ann["real_tool_xyz"]["executable"])
        only = tool_guard.filter_tools(tools, executable_only=True)
        self.assertNotIn(name, only)
        self.assertIn("real_tool_xyz", only)

    def test_health(self):
        h = tool_guard.health()
        self.assertIn("total_documented", h)
        self.assertIn("phantom_count", h)
        self.assertGreaterEqual(h["phantom_count"], 1)


class TestCapSelect(unittest.TestCase):
    CAPS = [
        {"cap_id": "lora:agri_expert_v1", "name": "agri_expert_v1", "kind": "lora",
         "tags": ["lora", "农业", "农学", "种植", "施肥"], "status": "active"},
        {"cap_id": "lora:t1_law_lora_v1", "name": "t1_law_lora_v1", "kind": "lora",
         "tags": ["lora", "法律"], "status": "shadow"},
        {"cap_id": "ext:github:astanin-python-tabulate", "name": "tabulate", "kind": "external",
         "installed": True, "tags": ["external", "表格"], "status": "shadow"},
    ]

    def test_select_relevant_first(self):
        picked = swarm.select_caps("玉米缺氮怎么施肥", self.CAPS, limit=2)
        self.assertEqual(picked[0]["cap_id"], "lora:agri_expert_v1")

    def test_installed_external_boosted(self):
        picked = swarm.select_caps("把数据排成表格", self.CAPS, limit=2)
        self.assertIn("ext:github:astanin-python-tabulate", [c["cap_id"] for c in picked])

    def test_limit(self):
        picked = swarm.select_caps("随便问问", self.CAPS, limit=2)
        self.assertLessEqual(len(picked), 2)

    def test_prompt_shrinks_with_task(self):
        many = [dict(c, cap_id="lora:test_%d" % i) for i, c in enumerate(self.CAPS * 10)]
        full = swarm.build_prompt(many, [("bcpcn", "x")])
        slim = swarm.build_prompt(many, [("bcpcn", "x")], task="玉米缺氮")
        self.assertLess(len(slim), len(full))


class TestCloudReviewCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ccache_ut_"))
        import chimera
        self.ch = chimera
        self._orig = (chimera.REVIEW_CACHE_FILE, chimera.CHIMERA_DIR)
        chimera.REVIEW_CACHE_FILE = self.tmp / "review_cache.json"
        chimera.CHIMERA_DIR = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, chimera, "REVIEW_CACHE_FILE", self._orig[0])
        self.addCleanup(setattr, chimera, "CHIMERA_DIR", self._orig[1])

    def test_second_call_cached(self):
        calls = []

        def fake_ask(prompt, system=None, max_tokens=None):
            calls.append(1)
            return '{"recommend":["a/b"],"verdict":"approve","concerns":[],"license_notes":"ok","sandbox_requirements":[]}'

        cands = [{"id": "a/b", "stars": 1000, "license": "mit", "source": "github"}]
        with mock.patch("cloud_bridge.available", return_value=True), \
             mock.patch("cloud_bridge.ask", side_effect=fake_ask):
            r1 = self.ch.cloud_review("gap-x", cands)
            r2 = self.ch.cloud_review("gap-x", cands)
        self.assertEqual(r1.get("status"), "ok")
        self.assertEqual(len(calls), 1, "第二次应命中缓存（实际调用 %d 次）" % len(calls))
        self.assertTrue(r2.get("cached"))

    def test_cache_ttl_expiry(self):
        calls = []

        def fake_ask(prompt, system=None, max_tokens=None):
            calls.append(1)
            return '{"verdict":"approve","recommend":[]}'

        with mock.patch("cloud_bridge.available", return_value=True), \
             mock.patch("cloud_bridge.ask", side_effect=fake_ask), \
             mock.patch.object(self.ch, "REVIEW_CACHE_TTL", -1):
            self.ch.cloud_review("gap-y", [{"id": "a/b"}])
            self.ch.cloud_review("gap-y", [{"id": "a/b"}])
        self.assertEqual(len(calls), 2, "TTL 过期应重新调用")



class TestStalePidPermission(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vllm_pid_"))
        self._orig = vllm_manager.PID_FILE
        vllm_manager.PID_FILE = self.tmp / ".vllm.pid"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, vllm_manager, "PID_FILE", self._orig)

    def test_pid1_permission_error_is_stale(self):
        # pid=1 属 root：os.kill(pid,0) 抛 PermissionError → 必须视为 stale 并清理
        vllm_manager.PID_FILE.write_text("1")
        with mock.patch.object(vllm_manager, "find_vllm_pids", return_value=[]):
            running, pid = vllm_manager.is_running()
        self.assertFalse(running)
        self.assertIsNone(pid)
        self.assertFalse(vllm_manager.PID_FILE.exists())


class TestCacheKeyStability(unittest.TestCase):
    def test_key_ignores_stars(self):
        import chimera
        c1 = [{"id": "a/b", "stars": 100, "license": "mit"}]
        c2 = [{"id": "a/b", "stars": 999, "license": "mit"}]
        self.assertEqual(chimera._review_cache_key("g", c1), chimera._review_cache_key("g", c2))

    def test_key_changes_with_ids(self):
        import chimera
        self.assertNotEqual(chimera._review_cache_key("g", [{"id": "a/b"}]),
                            chimera._review_cache_key("g", [{"id": "a/c"}]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
