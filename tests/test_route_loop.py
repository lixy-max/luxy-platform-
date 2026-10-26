# -*- coding: utf-8 -*-
"""S2 架构闭环测试：依赖拓扑 / 参数传递 / 失败升级 / 产物 / 复用"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import route_table as rt


def _fake_ok(content="ok", route="direct"):
    return {"ok": True, "content": content, "route": route, "meta": {}}


def _fake_fail(err="执行失败"):
    return {"ok": False, "content": "", "route": "capability", "meta": {"error": err}}


class TestDependencyLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt_loop_"))
        self._orig_db, self._orig_art = rt.DB_PATH, rt.ARTIFACT_DIR
        rt.DB_PATH = self.tmp / "rt.db"
        rt.ARTIFACT_DIR = self.tmp / "artifacts"
        self._embed = mock.patch.object(rt, "_embed", return_value=None)
        self._embed.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig_db)
        self.addCleanup(setattr, rt, "ARTIFACT_DIR", self._orig_art)
        self.addCleanup(self._embed.stop)

    def _items(self):
        return [
            {"content": "解析清单", "category": "local", "target": "kb:bcpcn", "depends_on": []},
            {"content": "排成表格", "category": "local",
             "target": "capability:ext:github:astanin-python-tabulate", "depends_on": [1]},
            {"content": "写结论", "category": "local", "target": "direct", "depends_on": [2]},
        ]

    def test_dependency_order_and_context(self):
        t = rt.create_table("测试", items=self._items())
        calls = []

        def fake_execute(dec, task, kb_entries=None):
            calls.append((dec.get("route"), dec.get("capability_id"), task))
            if dec.get("route") == "capability":
                # 验证参数传递：外部工具收到 capability_input.data
                assert dec.get("capability_input"), "外部工具应有 capability_input"
                return _fake_ok("| 表格 |", "capability")
            return _fake_ok("结果-%d" % len(calls), dec.get("route"))

        fake_cap = {"cap_id": "ext:github:astanin-python-tabulate", "kind": "external",
                    "installed": True, "args_schema": {"positional": ["data"]}}
        with mock.patch("swarm.execute", side_effect=fake_execute), \
             mock.patch("capabilities.get_capability", return_value=fake_cap):
            r = rt.execute_table(t["id"])
        self.assertEqual(r["table"]["status"], "done")
        self.assertEqual(len(calls), 3)
        # 顺序必须 1→2→3
        routes = [c[0] for c in calls]
        self.assertEqual(routes, ["kb", "capability", "direct"])
        # 第 2、3 步应带前序结果（参数/上下文传递）
        self.assertIn("前序结果", calls[1][2])
        self.assertIn("前序结果", calls[2][2])
        arts = rt.get_artifact(t["id"])
        self.assertTrue(arts["exists"])
        self.assertIn("任务表产物", arts["content"])

    def test_dep_skip_when_dependency_fails(self):
        items = [
            {"content": "会失败", "category": "local", "target": "direct", "depends_on": []},
            {"content": "依赖项", "category": "local", "target": "direct", "depends_on": [1]},
        ]
        t = rt.create_table("测试", items=items)
        with mock.patch("swarm.execute", return_value=_fake_fail()):
            r = rt.execute_table(t["id"])
        self.assertEqual(r["table"]["items"][0]["status"], "failed")
        self.assertEqual(r["table"]["items"][1]["status"], "skipped")
        self.assertIn("前置任务未完成", r["table"]["items"][1]["result"])

    def test_escalate_cloud(self):
        items = [{"content": "本地会失败", "category": "local", "target": "direct", "depends_on": []}]
        t = rt.create_table("测试", items=items)

        def fake_execute(dec, task, kb_entries=None):
            if dec.get("route") == "direct":
                return _fake_fail()
            return _fake_ok("[云端] 答案", "cloud")

        with mock.patch("swarm.execute", side_effect=fake_execute):
            r = rt.execute_table(t["id"], escalate_cloud=True)
        it = r["table"]["items"][0]
        self.assertEqual(it["status"], "done")
        self.assertIn("升级云端", it["result"])
        self.assertEqual(it["attempts"][0]["try"], "local")
        self.assertEqual(it["attempts"][1]["try"], "escalated_cloud")

    def test_escalate_transplant(self):
        items = [{"content": "本地缺工具", "category": "local", "target": "direct", "depends_on": []}]
        t = rt.create_table("测试", items=items)
        with mock.patch("swarm.execute", return_value=_fake_fail()), \
             mock.patch("chimera.plan", return_value={"plan_id": "p1", "candidates": [1, 2]}):
            r = rt.execute_table(t["id"], escalate_transplant=True)
        it = r["table"]["items"][0]
        self.assertEqual(it["status"], "done")
        self.assertIn("移植", it["result"])

    def test_clone_and_similar(self):
        t = rt.create_table("整理玩具采购清单", items=self._items())
        clone = rt.clone_table(t["id"])
        self.assertNotEqual(clone["id"], t["id"])
        self.assertEqual(clone["status"], "pending")
        self.assertEqual(clone["meta"].get("clone_of"), t["id"])
        sim = rt.similar(t["id"])
        self.assertTrue(any(x["id"] == clone["id"] for x in sim))

    def test_execute_max_items_stops(self):
        t = rt.create_table("测试", items=self._items())
        with mock.patch("swarm.execute", return_value=_fake_ok()):
            r = rt.execute_table(t["id"], max_items=1)
        self.assertEqual(len(r["executed"]), 1)
        self.assertEqual(r["table"]["progress"]["done"], 1)

    def test_cloud_skipped_without_flag(self):
        items = [{"content": "上云", "category": "cloud", "target": "cloud", "depends_on": []}]
        t = rt.create_table("测试", items=items)
        r = rt.execute_table(t["id"])
        self.assertEqual(r["table"]["items"][0]["status"], "skipped")

    def test_artifact_partial_not_generated(self):
        t = rt.create_table("测试", items=self._items())
        with mock.patch("swarm.execute", return_value=_fake_ok()):
            rt.execute_table(t["id"], max_items=1)
        art = rt.get_artifact(t["id"])
        self.assertFalse(art["exists"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
