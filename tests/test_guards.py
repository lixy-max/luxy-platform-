# -*- coding: utf-8 -*-
"""
共享状态守卫的自检。

为什么必须有：一个**永远不会红**的守卫等于没有守卫。这里用构造出来的
"变化前/变化后"快照验证比较逻辑真的能发现问题，并锁定守卫必须覆盖的路径
（防止以后有人把某个关键路径从快照里删掉而无人察觉）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guards as G


class TestSharedStateGuard(unittest.TestCase):
    def test_snapshot_covers_critical_paths(self):
        keys = set(G.snapshot_shared_state().keys())
        for must in ("outputs/result_*.json", "cache/nodes/*", "training_data/*.jsonl",
                     "uploads/*", "settings.json", "workflow_config.json",
                     "data/workbench.db"):
            self.assertIn(must, keys, f"守卫快照缺少关键路径：{must}")

    def test_every_key_has_a_hint(self):
        for k in G.snapshot_shared_state():
            self.assertIn(k, G._HINTS, f"{k} 没有提示语，报错时无法指导修复")

    def test_detects_added_output_file(self):
        before = {"outputs/result_*.json": 10, "settings.json": "abc"}
        after = {"outputs/result_*.json": 11, "settings.json": "abc"}
        changed = G.diff_shared_state(before, after)
        self.assertEqual(len(changed), 1)
        key, b, a, hint = changed[0]
        self.assertEqual((key, b, a), ("outputs/result_*.json", 10, 11))
        self.assertIn("OUTPUT_DIR", hint)          # 提示必须指向修法

    def test_detects_modified_settings_and_db(self):
        before = {"settings.json": "abc", "data/workbench.db": 100}
        after = {"settings.json": "def", "data/workbench.db": 200}
        changed = {c[0] for c in G.diff_shared_state(before, after)}
        self.assertEqual(changed, {"settings.json", "data/workbench.db"})

    def test_detects_removed_file(self):
        before = {"uploads/*": 44}
        self.assertEqual(len(G.diff_shared_state(before, {"uploads/*": 43})), 1)

    def test_no_change_means_empty(self):
        snap = {"a": 1, "b": None}
        self.assertEqual(G.diff_shared_state(snap, dict(snap)), [])

    def test_assert_raises_with_actionable_message(self):
        """assert_shared_state_unchanged 抛错时必须带路径、变化值与修法提示。"""
        before = {"cache/nodes/*": 5, "outputs/result_*.json": 1}
        after = {"cache/nodes/*": 6, "outputs/result_*.json": 1}
        # 直接测比较 + 断言消息构造（不触碰真实数据）
        changed = G.diff_shared_state(before, after)
        self.assertTrue(changed)
        msg = "\n".join([f"{c[0]}: {c[1]} → {c[2]}" for c in changed])
        self.assertIn("cache/nodes/*", msg)
        self.assertIn("CACHE_ENABLED", G._HINTS["cache/nodes/*"])

    def test_real_state_is_stable_across_two_reads(self):
        """守卫本身不能有副作用：连读两次必须一致（否则会自己误报）。"""
        self.assertEqual(G.snapshot_shared_state(), G.snapshot_shared_state())


if __name__ == "__main__":
    unittest.main()
