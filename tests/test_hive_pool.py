# -*- coding: utf-8 -*-
"""S19 Worker Pool + LoRA Router + Registry 单元测试（Mock 网络，不依赖 GPU）"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_lora_registry as reg
import hive_worker_pool as pool

BASE7B = "/home/luxy/models/Qwen2.5-7B-Instruct-AWQ"


class TestRegistry(unittest.TestCase):
    def test_compatibility_filter(self):
        items = reg.list_loras(worker_base=BASE7B, compatible_only=True)
        ids = {e["id"] for e in items}
        self.assertIn("json_extract", ids)
        self.assertIn("semantic_intent_v1", ids)
        self.assertNotIn("workflow_author_v1", ids)   # 1.5B，与 7B 不兼容
        for e in items:
            self.assertEqual(e["family"], "qwen2.5-7b")
            self.assertEqual(e["status"], "active")

    def test_pick_capability(self):
        e = reg.pick(["json"], worker_base=BASE7B)
        self.assertEqual(e["id"], "json_extract")
        e2 = reg.pick(["intent", "classify"], worker_base=BASE7B)
        self.assertEqual(e2["id"], "semantic_intent_v1")
        e3 = reg.pick(["agri"], worker_base=BASE7B)
        self.assertEqual(e3["id"], "agri_expert_v1")

    def test_pick_no_match(self):
        self.assertIsNone(reg.pick(["nonexistent_cap"], worker_base=BASE7B))

    def test_broken_entries(self):
        items = reg.list_loras(worker_base=None, compatible_only=False)
        by_id = {e["id"]: e for e in items}
        self.assertEqual(by_id["swk_persona_v1"]["status"], "broken")
        self.assertEqual(by_id["workflow_author_v1"]["status"], "shadow")  # compatible=false

    def test_summary(self):
        s = reg.summary(worker_base=BASE7B)
        self.assertGreater(s["total"], 5)
        self.assertGreater(s["compatible"], 2)


class TestPool(unittest.TestCase):
    def setUp(self):
        pool.reset_state()
        self._max = pool.MAX_LORAS
        pool.MAX_LORAS = 2
        self.addCleanup(setattr, pool, "MAX_LORAS", self._max)

    def _ids(self, loaded=()):
        return [BASE7B] + list(loaded), ""

    def test_worker_status(self):
        with mock.patch.object(pool, "_served_ids", return_value=self._ids(["json_extract"])):
            st = pool.worker_status()
        self.assertTrue(st["ready"])
        self.assertEqual(st["base_model"], BASE7B)
        self.assertEqual(st["loaded"], ["json_extract"])
        self.assertEqual(st["slots_free"], 1)

    def test_route_base_when_no_capability(self):
        with mock.patch.object(pool, "_served_ids", return_value=self._ids()), \
             mock.patch.object(pool, "_load") as ml:
            r = pool.route("普通任务，没有明显能力标签")
        self.assertTrue(r["ok"])
        self.assertEqual(r["lora_id"], "base")
        ml.assert_not_called()

    def test_route_loads_lora(self):
        calls = []

        def fake_load(lid):
            calls.append(lid)
            return True, "", 0.5
        with mock.patch.object(pool, "_served_ids", return_value=self._ids()), \
             mock.patch.object(pool, "_load", side_effect=fake_load):
            r = pool.route("把结果输出成 json 结构化字段")
        self.assertTrue(r["ok"])
        self.assertEqual(r["lora_id"], "json_extract")
        self.assertTrue(r["loaded_now"])
        self.assertEqual(calls, ["json_extract"])

    def test_lru_eviction(self):
        state = {"loaded": ["json_extract", "semantic_intent_v1"], "order": ["json_extract", "semantic_intent_v1"]}
        unloaded, loaded = [], []

        def fake_served(*a, **k):
            return [BASE7B] + list(state["loaded"]), ""

        def fake_load(lid):
            loaded.append(lid)
            state["loaded"].append(lid)
            return True, "", 0.3

        def fake_unload(lid):
            unloaded.append(lid)
            if lid in state["loaded"]:
                state["loaded"].remove(lid)
            return True, "", 0.1
        with mock.patch.object(pool, "_served_ids", side_effect=fake_served), \
             mock.patch.object(pool, "_load", side_effect=fake_load), \
             mock.patch.object(pool, "_unload", side_effect=fake_unload):
            # 先让 LRU 记录已加载状态
            pool.worker_status()
            r = pool.route("农业作物病虫害识别")   # → agri_expert_v1（未加载）
        self.assertTrue(r["ok"])
        self.assertEqual(r["lora_id"], "agri_expert_v1")
        self.assertTrue(r["evicted"])
        self.assertEqual(r["evicted"], ["json_extract"])
        self.assertEqual(loaded, ["agri_expert_v1"])

    def test_route_load_failure(self):
        with mock.patch.object(pool, "_served_ids", return_value=self._ids()), \
             mock.patch.object(pool, "_load", return_value=(False, "boom", 0.2)):
            r = pool.route("输出 json")
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])

    def test_run_builds_chat_request(self):
        fake_resp = mock.Mock(status_code=200)
        fake_resp.json.return_value = {"choices": [{"message": {"content": "结果OK"}}],
                                       "usage": {"completion_tokens": 5}}
        posted = {}

        def fake_post(url, json=None, timeout=None):
            posted["url"] = url
            posted["json"] = json
            return fake_resp
        with mock.patch.object(pool, "_served_ids", return_value=self._ids()), \
             mock.patch.object(pool, "_load", return_value=(True, "", 0.5)), \
             mock.patch("requests.post", side_effect=fake_post):
            r = pool.run("把结果输出成 json", max_tokens=16)
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "结果OK")
        self.assertIn("/v1/chat/completions", posted["url"])
        self.assertEqual(posted["json"]["model"], "json_extract")
        self.assertEqual(posted["json"]["max_tokens"], 16)

    def test_run_worker_offline(self):
        with mock.patch.object(pool, "_served_ids", return_value=([], "conn refused")):
            r = pool.run("输出 json")
        self.assertFalse(r["ok"])
        self.assertIn("worker 不可用", r["error"])

    def test_load_payload(self):
        fake_resp = mock.Mock(status_code=200)
        posted = {}

        def fake_post(url, json=None, timeout=None):
            posted["url"] = url
            posted["json"] = json
            return fake_resp
        with mock.patch("requests.post", side_effect=fake_post):
            ok, err, lat = pool._load("json_extract")
        self.assertTrue(ok)
        self.assertIn("/v1/load_lora_adapter", posted["url"])
        self.assertEqual(posted["json"]["lora_name"], "json_extract")
        self.assertIn("/home/luxy/loras/json_extract", posted["json"]["lora_path"])

    def test_concurrent_routes_no_oversubscribe(self):
        state = {"loaded": [], "loads": [], "unloads": []}

        def fake_served(*a, **k):
            return [BASE7B] + list(state["loaded"]), ""

        def fake_load(lid):
            import time as _t
            _t.sleep(0.01)
            state["loaded"].append(lid)
            state["loads"].append(lid)
            return True, "", 0.01

        def fake_unload(lid):
            if lid in state["loaded"]:
                state["loaded"].remove(lid)
            state["unloads"].append(lid)
            return True, "", 0.01

        reqs = ["输出 json", "意图识别", "农业作物", "人格风格", "输出 json",
                "意图识别", "农业作物", "人格风格", "输出 json", "意图识别"]
        with mock.patch.object(pool, "_served_ids", side_effect=fake_served), \
             mock.patch.object(pool, "_load", side_effect=fake_load), \
             mock.patch.object(pool, "_unload", side_effect=fake_unload):
            ths = [threading.Thread(target=pool.route, args=(q,)) for q in reqs]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            st = pool.worker_status()
        self.assertLessEqual(len(state["loaded"]), pool.MAX_LORAS)
        self.assertLessEqual(st["slots_free"], pool.MAX_LORAS)
        self.assertGreater(len(state["loads"]), 0)

    def test_pool_status_structure(self):
        with mock.patch.object(pool, "_served_ids", return_value=self._ids(["json_extract"])):
            st = pool.pool_status()
        for k in ("ready", "base_model", "loaded", "max_loras", "slots_free",
                  "lru_order", "stats", "registry"):
            self.assertIn(k, st)




class TestRaceRetry(unittest.TestCase):
    def setUp(self):
        pool.reset_state()
        self._max = pool.MAX_LORAS
        pool.MAX_LORAS = 2
        self.addCleanup(setattr, pool, "MAX_LORAS", self._max)

    def _ids(self, loaded=()):
        return [BASE7B] + list(loaded), ""

    def test_404_retry_after_race(self):
        ok_resp = mock.Mock(status_code=200)
        ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}],
                                     "usage": {"completion_tokens": 3}}
        bad_resp = mock.Mock(status_code=404, text='{"error":{"message":"The model `json_extract` does not exist."}}')
        calls = {"n": 0}

        def fake_post(url, json=None, timeout=None):
            calls["n"] += 1
            return bad_resp if calls["n"] == 1 else ok_resp
        with mock.patch.object(pool, "_served_ids", return_value=self._ids()), \
             mock.patch.object(pool, "_load", return_value=(True, "", 0.1)), \
             mock.patch("requests.post", side_effect=fake_post):
            r = pool.run("输出 json", max_tokens=8)
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("retried"))
        self.assertGreaterEqual(calls["n"], 2)

    def test_eviction_counts_unloads(self):
        state = {"loaded": ["json_extract", "semantic_intent_v1"]}

        def fake_served(*a, **k):
            return [BASE7B] + list(state["loaded"]), ""

        def fake_load(lid):
            state["loaded"].append(lid)
            return True, "", 0.05

        def fake_unload(lid):
            if lid in state["loaded"]:
                state["loaded"].remove(lid)
            return True, "", 0.05
        with mock.patch.object(pool, "_served_ids", side_effect=fake_served), \
             mock.patch.object(pool, "_load", side_effect=fake_load), \
             mock.patch.object(pool, "_unload", side_effect=fake_unload):
            pool.worker_status()
            r = pool.route("农业作物病虫害")
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(pool._stats["unloads"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
