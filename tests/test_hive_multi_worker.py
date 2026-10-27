# -*- coding: utf-8 -*-
"""S26 多节点工蜂调度 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_multi_worker as mw
import hive_node_registry as nr


class FakeResp:
    def __init__(self, status=200, body=b'{"data":[{"id":"served-model"}]}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


NODES = [
    {"id": "w1", "type": "vllm", "url": "http://127.0.0.1:8002", "family": "qwen2.5",
     "max_loras": 2, "loaded_loras": [], "cost_score": 1.0, "priority": 1,
     "capabilities": ["text", "json"], "status": "healthy", "served_model": "m7b"},
    {"id": "w2", "type": "mock", "url": "http://127.0.0.1:8003", "family": "qwen2.5",
     "max_loras": 1, "loaded_loras": [], "cost_score": 2.0, "priority": 2,
     "capabilities": ["text", "json", "1.5b"], "status": "healthy", "served_model": "mock"},
    {"id": "cloud", "type": "cloud", "url": "", "family": "*", "max_loras": 999,
     "loaded_loras": [], "cost_score": 8.0, "priority": 9,
     "capabilities": ["text", "vision"], "status": "healthy", "served_model": "cloud"},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s26_"))
        self.patches = [
            mock.patch.object(nr, "NODES_FILE", self.tmp / "nodes.json"),
            mock.patch.object(nr, "_health_cache", {}),
            mock.patch.object(mw, "_rr", {"i": 0}),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        (self.tmp / "nodes.json").write_text(json.dumps(NODES, ensure_ascii=False),
                                             encoding="utf-8")
        mw.reset_stats()

    def tearDown(self):
        pass


class TestNodeRegistry(Base):
    def test_register_update_capacity(self):
        r = nr.register_node({"id": "w3", "url": "http://127.0.0.1:8004", "max_loras": 3})
        self.assertTrue(r["ok"])
        self.assertIsNotNone(nr.get_node("w3"))
        nr.set_loaded_loras("w3", ["a", "b"])
        self.assertEqual(nr.capacity(nr.get_node("w3")), 1)

    def test_health_check(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            res = nr.health_check({**NODES[0], "id": "w1"})
        self.assertTrue(res["healthy"])
        self.assertEqual(res.get("served_model"), "served-model")
        with mock.patch("urllib.request.urlopen", side_effect=Exception("boom")):
            nr._health_cache.clear()
            res2 = nr.health_check({**NODES[0], "id": "w1"})
        self.assertFalse(res2["healthy"])

    def test_available_nodes(self):
        nr._health_cache["w1"] = (9999999999, {"healthy": True})
        nr._health_cache["w2"] = (9999999999, {"healthy": True})
        caps = nr.available_nodes(capability="1.5b")
        self.assertEqual([n["id"] for n in caps], ["w2"])
        nr.set_loaded_loras("w2", ["x"])
        caps2 = nr.available_nodes(capability="1.5b")
        self.assertEqual(caps2, [])


class TestPick(Base):
    def test_pick_cost_prefers_w1(self):
        with (mock.patch.object(nr, "available_nodes", return_value=[dict(n) for n in NODES[:2]]),
              mock.patch.object(nr, "get_node", return_value=dict(NODES[2]))):
            sel = mw.pick(capability="json", policy="cost")
        self.assertTrue(sel["ok"])
        self.assertEqual(sel["node"]["id"], "w1")

    def test_pick_capability_15b(self):
        with (mock.patch.object(nr, "available_nodes", return_value=[dict(NODES[1])]),
              mock.patch.object(nr, "get_node", return_value=dict(NODES[2]))):
            sel = mw.pick(capability="1.5b", policy="balanced")
        self.assertEqual(sel["node"]["id"], "w2")

    def test_pick_cloud_fallback(self):
        with (mock.patch.object(nr, "available_nodes", return_value=[]),
              mock.patch.object(nr, "get_node", return_value=dict(NODES[2]))):
            sel = mw.pick(capability="vision")
        self.assertTrue(sel["ok"])
        self.assertEqual(sel["node"]["id"], "cloud")

    def test_pick_no_node(self):
        with (mock.patch.object(nr, "available_nodes", return_value=[]),
              mock.patch.object(nr, "get_node", return_value=None)):
            sel = mw.pick(include_cloud=False)
        self.assertFalse(sel["ok"])


class TestRouteRun(Base):
    def test_success_first(self):
        with (mock.patch.object(mw, "pick",
                                return_value={"ok": True, "node": dict(NODES[0])}),
              mock.patch.object(mw, "run_node",
                                return_value={"ok": True, "node": "w1", "text": "hi",
                                              "latency_ms": 12})):
            r = mw.route_run("任务")
        self.assertTrue(r["ok"])
        self.assertEqual(r["node"], "w1")
        self.assertEqual(mw.stats()["total"], 1)

    def test_failover_second_node(self):
        seq = [{"ok": True, "node": dict(NODES[0])},
               {"ok": True, "node": dict(NODES[1])}]
        runs = [{"ok": False, "node": "w1", "error": "down", "latency_ms": 5},
                {"ok": True, "node": "w2", "text": "ok", "latency_ms": 9}]
        with (mock.patch.object(mw, "pick", side_effect=seq),
              mock.patch.object(mw, "run_node", side_effect=runs),
              mock.patch.object(nr, "update_node", return_value={"ok": True})):
            r = mw.route_run("任务")
        self.assertTrue(r["ok"])
        self.assertEqual(r["node"], "w2")
        self.assertEqual(len(r["attempts"]), 2)
        self.assertEqual(mw.stats()["failovers"], 1)

    def test_all_fail_need_cloud(self):
        with (mock.patch.object(mw, "pick",
                                return_value={"ok": True, "node": dict(NODES[0])}),
              mock.patch.object(mw, "run_node",
                                return_value={"ok": False, "node": "w1", "error": "boom"}),
              mock.patch.object(nr, "update_node", return_value={"ok": True})):
            r = mw.route_run("任务", max_attempts=2)
        self.assertFalse(r["ok"])
        self.assertTrue(r["need_cloud"])

    def test_cloud_node_returns_need_cloud(self):
        with mock.patch.object(mw, "pick",
                               return_value={"ok": True, "node": dict(NODES[2])}):
            r = mw.route_run("任务")
        self.assertFalse(r["ok"])
        self.assertTrue(r["need_cloud"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
