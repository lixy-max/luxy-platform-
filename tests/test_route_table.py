# -*- coding: utf-8 -*-
"""S1 级联路由任务表测试（不加载 bge-m3、不调主脑）"""
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

ITEMS = [
    {"content": "解析玩具清单 Excel", "category": "local", "target": "kb:qiaojiao_toys_2025", "reason": "本地 KB"},
    {"content": "核对招标条款", "category": "cloud", "target": "cloud", "reason": "需要云端模型"},
    {"content": "下载招标文档生成工具", "category": "transplant", "target": "chimera", "reason": "本地无此工具"},
]


class TestRouteTable(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt_ut_"))
        self._orig = rt.DB_PATH
        rt.DB_PATH = self.tmp / "rt.db"
        self._embed = mock.patch.object(rt, "_embed", return_value=None)
        self._embed.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig)
        self.addCleanup(self._embed.stop)

    def test_create_and_get(self):
        t = rt.create_table("整理玩具清单", items=ITEMS)
        self.assertTrue(t["id"])
        self.assertEqual(t["items_count"], 3)
        self.assertEqual(t["progress"]["total"], 3)
        self.assertEqual(t["items"][0]["category"], "local")
        self.assertEqual(rt.get_table(t["id"])["request"], "整理玩具清单")

    def test_list_tables(self):
        rt.create_table("需求A", items=ITEMS)
        rt.create_table("需求B", items=ITEMS[:1])
        rows = rt.list_tables()
        self.assertGreaterEqual(len(rows), 2)

    def test_check_item(self):
        t = rt.create_table("需求A", items=ITEMS)
        t2 = rt.check_item(t["id"], 1, "done", "已解析 90 行")
        self.assertEqual(t2["items"][0]["status"], "done")
        self.assertEqual(t2["progress"]["done"], 1)
        self.assertEqual(t2["status"], "running")
        # 完成全部 → done
        rt.check_item(t["id"], 2, "skipped")
        t3 = rt.check_item(t["id"], 3, "done")
        self.assertEqual(t3["status"], "done")

    def test_check_invalid(self):
        t = rt.create_table("需求A", items=ITEMS)
        with self.assertRaises(ValueError):
            rt.check_item(t["id"], 1, "nope")
        with self.assertRaises(ValueError):
            rt.check_item(t["id"], 99, "done")

    def test_search_keyword_fallback(self):
        rt.create_table("整理玩具采购清单", items=ITEMS)
        rt.create_table("写法律合同", items=ITEMS[:1])
        rs = rt.search_tables("玩具")
        self.assertTrue(rs)
        self.assertIn("玩具", rs[0]["request"])

    def test_execute_local_and_skip_others(self):
        t = rt.create_table("需求A", items=ITEMS)
        fake = {"ok": True, "content": "执行结果", "route": "kb", "meta": {}}
        with mock.patch("swarm.execute", return_value=fake) as m:
            r = rt.execute_table(t["id"], run_cloud=False, run_transplant=False)
        self.assertEqual(len(r["executed"]), 3)
        done = [x for x in r["executed"] if x.get("ok")]
        skipped = [x for x in r["executed"] if x.get("skipped")]
        self.assertEqual(len(done), 1)
        self.assertEqual(len(skipped), 2)
        self.assertTrue(m.called)
        t2 = rt.get_table(t["id"])
        self.assertEqual(t2["items"][0]["status"], "done")
        self.assertEqual(t2["items"][1]["status"], "skipped")

    def test_generate_items_parses_brain_json(self):
        raw = json.dumps({"items": [{"content": "查知识库", "category": "local", "target": "kb:bcpcn"},
                                    {"content": "云端总结", "category": "cloud"}]}, ensure_ascii=False)
        with mock.patch("swarm.ask_brain", return_value=raw):
            items = rt.generate_items("测试需求")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["category"], "local")
        self.assertEqual(items[1]["category"], "cloud")
        self.assertEqual(items[1]["target"], "cloud")

    def test_generate_items_fallback(self):
        with mock.patch("swarm.ask_brain", return_value="不是 JSON"):
            items = rt.generate_items("测试需求")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "local")

    def test_stats(self):
        rt.create_table("需求A", items=ITEMS)
        s = rt.stats()
        self.assertEqual(s["tables"], 1)
        self.assertIn("local", s["by_category"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
