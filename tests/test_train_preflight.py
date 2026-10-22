#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""train_lora 的数据前置闸门 + 训练锁 单测（P-3/P-4/P-7）。

只测纯逻辑 ✓ 不真训练 ✓ 不碰知识库 ✓
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import train_lora as tl  # noqa: E402


def _sample(system: str, human: str, gpt: str):
    """★ 2026-10-22: 训练数据格式要求顶层 system + conversations 以 human 开头
    （旧写法把 system 放进 conversations[0] ⇒ F1-F8 判不合格）
    """
    return {"system": system,
            "conversations": [{"from": "human", "value": human},
                              {"from": "gpt", "value": gpt}]}


class TestPreflight(unittest.TestCase):
    def _write(self, rows):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "ds.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p, d

    def test_rejects_truncated_dataset(self):
        """★ 复现实测问题: system 就有 5000 字（> cutoff 2048）⇒ 必须拒绝"""
        rows = [_sample("工" * 5000, "把结果导出来", "好") for _ in range(10)]
        p, d = self._write(rows)
        with self.assertRaises(tl.DataPreflightError) as cm:
            tl.data_preflight(p, cutoff_len=2048, output_dir=d)
        # ★ 2026-10-22: system 5000 字现在先被 Q1/Q2 拦截（比"截断"更早）
        _msg = str(cm.exception)
        self.assertTrue(("截断" in _msg) or ("system" in _msg) or ("格式" in _msg), _msg[:200])
        rep = json.loads(Path(d, "data_report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["verdict"], "reject")
        # ★ 同上：被 Q1/Q2 拒时未必走截断统计路径
        self.assertTrue(rep.get("problems") or rep.get("over_cutoff_ratio", 0) > 0.9,
                        "应记录拒绝原因: %s" % rep.get("problems"))

    def test_rejects_system_heavy(self):
        """★ system【绝对过长】才拒（>600 token ✓）——
        为什么不是"占比"✗: 短样本里 system 占比天然高，按占比重会误杀合格数据 ✗
        （2026-10-15 实测踩过: 瘦身 system 215 字符 / 样本 268 token ⇒ 占比 60.8% 被误拒 ✗）"""
        rows = [_sample("角" * 3000, "短问题", "答" * 20) for _ in range(10)]
        p, d = self._write(rows)
        with self.assertRaises(tl.DataPreflightError) as cm:
            tl.data_preflight(p, cutoff_len=8192, output_dir=d)
        self.assertIn("system", str(cm.exception))

    def test_passes_slim_data(self):
        rows = [_sample("你是助手。", "把结果导出来给我", "已导出到 outputs/x.docx ✓" * 3)
                for _ in range(12)]
        p, d = self._write(rows)
        rep = tl.data_preflight(p, cutoff_len=2048, output_dir=d)
        self.assertEqual(rep["verdict"], "pass")
        self.assertEqual(rep["problems"], [])


    def test_short_samples_with_short_system_pass(self):
        """★ 踩过的坑: 第一版只看 system【占比】⇒ 短样本里占比天然很高 ⇒ 误杀合格数据 ✗"""
        rows = [_sample("你是助手。", "把结果导出来给我", "好") for _ in range(12)]
        p, d = self._write(rows)
        rep = tl.data_preflight(p, cutoff_len=2048, output_dir=d)
        self.assertEqual(rep["verdict"], "pass")
        self.assertLess(rep.get("system_tokens_avg", 0), 600)

    def test_rejects_tiny_dataset(self):
        rows = [_sample("你是助手。", "干活", "好") for _ in range(3)]
        p, _ = self._write(rows)
        with self.assertRaises(tl.DataPreflightError):
            tl.data_preflight(p, cutoff_len=2048)

    def test_env_override_allows_truncation(self):
        rows = [_sample("工" * 5000, "把结果导出来", "好") for _ in range(10)]
        p, d = self._write(rows)
        os.environ["TRAIN_ALLOW_TRUNCATION"] = "1"
        os.environ["TRAIN_ALLOW_SYSTEM_HEAVY"] = "1"
        try:
            rep = tl.data_preflight(p, cutoff_len=2048, output_dir=d)
            self.assertEqual(rep["verdict"], "pass")
        finally:
            os.environ.pop("TRAIN_ALLOW_TRUNCATION", None)
            os.environ.pop("TRAIN_ALLOW_SYSTEM_HEAVY", None)

    def test_est_tokens_cjk(self):
        self.assertEqual(tl._est_tokens("中文"), 2)
        self.assertEqual(tl._est_tokens("abcd"), 1)


class TestTrainLock(unittest.TestCase):
    def setUp(self):
        # ★ 用独立临时锁文件（不与真实训练 / 上一次测试进程撞车 ✓）
        self._tmp = tempfile.mkdtemp()
        os.environ["TRAIN_LOCK_PATH"] = os.path.join(self._tmp, "train.lock")

    def tearDown(self):
        os.environ.pop("TRAIN_LOCK_PATH", None)

    def test_lock_is_exclusive(self):
        """★ 抢第二次必须失败（这就是 P-3 的单飞保护 ✓）"""
        fd1 = tl.acquire_train_lock()
        try:
            with self.assertRaises(RuntimeError) as cm:
                tl.acquire_train_lock()
            self.assertIn("已有训练在跑", str(cm.exception))
        finally:
            tl._release_train_lock(fd1)
        fd2 = tl.acquire_train_lock()      # 释放后可再抢 ✓
        tl._release_train_lock(fd2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
