# -*- coding: utf-8 -*-
"""S7 模型导入测试：设备评估（显存/量化）/ 元数据解析 / 下载链路（打桩）/ 注册"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_import as mi


def _meta(weight_bytes, hidden=4096, layers=32, vocab=152064, license_="apache-2.0"):
    return {"repo_id": "test/model", "source": "modelscope", "name": "model",
            "license": license_, "downloads": 100,
            "files": [{"path": "model.safetensors", "size": weight_bytes}],
            "weight_bytes": weight_bytes, "total_bytes": weight_bytes,
            "config": {"hidden_size": hidden, "num_hidden_layers": layers,
                       "vocab_size": vocab, "max_position_embeddings": 4096}}


class TestEstimate(unittest.TestCase):
    def test_params_estimate(self):
        m = _meta(14 * 1024 ** 3)
        est = mi.estimate(m)
        # 12*32*4096^2 + 152064*4096 ≈ 6.9B
        self.assertGreater(est["params_b"], 6.0)
        self.assertLess(est["params_b"], 8.0)
        self.assertEqual(est["params_source"], "config 估算")

    def test_7b_quantized_fits_single(self):
        # 14GB fp16 权重 → fp16 需双卡/云端；int8/int4 单卡可部署
        est = mi.estimate(_meta(14 * 1024 ** 3))
        self.assertIn(est["fits"]["fp16"], ("dual_tp2", "cloud_only"))
        self.assertIn(est["fits"]["int8"], ("single_16g", "single_12g"))
        self.assertEqual(est["best_mode"], "int8")
        self.assertIn("int8", est["quantization"])

    def test_small_fp16_fits(self):
        # 2.9GB fp16 ≈ 1.5B → fp16 单卡
        est = mi.estimate(_meta(2.9 * 1024 ** 3, hidden=1536, layers=28, vocab=151936))
        self.assertEqual(est["best_mode"], "fp16")
        self.assertEqual(est["verdict"], "single_16g")

    def test_huge_model_cloud_only(self):
        est = mi.estimate(_meta(140 * 1024 ** 3, hidden=8192, layers=80, vocab=152064))
        self.assertEqual(est["verdict"], "cloud_only")
        self.assertIn("云端", est["quantization"])

    def test_license_note(self):
        est = mi.estimate(_meta(2 * 1024 ** 3, license_="agpl-3.0"))
        self.assertTrue(any("许可" in n for n in est["notes"]))

    def test_min_overhead(self):
        est = mi.estimate(_meta(1024 ** 3))
        self.assertGreaterEqual(est["modes"]["fp16"]["overhead_gb"], 1.0)


class TestFetchAndImport(unittest.TestCase):
    def test_fetch_meta_modelscope(self):
        with mock.patch.object(mi, "_get_json", side_effect=[
                {"Data": {"Name": "M", "License": "apache-2.0", "Downloads": 5}},
                {"Data": {"Files": [{"Name": "model.safetensors", "Size": 1000},
                                    {"Name": "config.json", "Size": 10}]}}]), \
             mock.patch.object(mi, "_get_text", side_effect=Exception("no config")):
            meta = mi.fetch_meta("a/b", source="modelscope")
        self.assertEqual(meta["license"], "apache-2.0")
        self.assertEqual(meta["weight_bytes"], 1000)

    def test_fetch_meta_bad_source(self):
        with self.assertRaises(ValueError):
            mi.fetch_meta("a/b", source="nope")

    def test_import_requires_approve(self):
        with self.assertRaises(ValueError):
            mi.import_model("a/b", approve=False)

    def test_import_cloud_only_rejected(self):
        est = {"verdict": "cloud_only", "repo_id": "a/b"}
        with mock.patch.object(mi, "assess", return_value=est):
            r = mi.import_model("a/b", approve=True)
        self.assertFalse(r["ok"])
        self.assertIn("cloud_only", str(r))

    def test_import_flow(self):
        est = {"verdict": "single_16g", "best_mode": "fp16", "repo_id": "a/b",
               "name": "b", "weight_gb": 3.0, "fits": {"fp16": "single_16g"}}
        with mock.patch.object(mi, "assess", return_value=est), \
             mock.patch.object(mi, "download", return_value={"path": "/tmp/x", "dir": "/tmp/x",
                                                             "download_seconds": 1.0}), \
             mock.patch.object(mi, "register_model", return_value={"model_id": "b-fp16"}), \
             mock.patch.object(mi, "_append_import") as m, \
             mock.patch("pathlib.Path.rglob", return_value=[]):
            r = mi.import_model("a/b", approve=True, role="small_candidate")
        self.assertTrue(r["ok"])
        self.assertEqual(r["model"]["model_id"], "b-fp16")
        self.assertTrue(m.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
