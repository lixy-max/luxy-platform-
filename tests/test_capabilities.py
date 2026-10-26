# -*- coding: utf-8 -*-
"""P2.3 能力注册表测试：扫描注册 / 兼容性过滤 / 路由匹配 / 状态 / 使用统计。"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="cap_ut_"))
os.environ["CAPABILITY_REGISTRY"] = str(_TMP / "registry.json")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import capabilities  # noqa: E402
importlib.reload(capabilities)


def _mk_lora(root, name, base):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "adapter_config.json").write_text(json.dumps({
        "base_model_name_or_path": base, "r": 16, "lora_alpha": 32,
        "target_modules": ["q_proj", "v_proj"], "peft_type": "LORA",
    }), encoding="utf-8")
    (d / "adapter_model.safetensors").write_bytes(b"\x00" * 1024)
    return d


class TestCapabilities(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cap_root_", dir=str(_TMP)))
        self.loras = self.root / "loras"
        self.training = self.root / "training_output"
        self.loras.mkdir()
        self.training.mkdir()
        capabilities.LORA_DIR = self.loras
        capabilities.TRAINING_DIR = self.training
        capabilities.REGISTRY_PATH = self.root / "registry.json"
        capabilities.WORKER_BASE_MODEL = "Qwen2.5-7B-Instruct"
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, capabilities, "LORA_DIR", capabilities.LORA_DIR)

    def _register(self):
        return capabilities.register_from_loras(worker_port=8002, overwrite=True)

    def test_scan_and_register(self):
        _mk_lora(self.training, "agri_expert_v1", "/home/luxy/models/Qwen2.5-7B-Instruct")
        _mk_lora(self.training, "law_lora", "/home/luxy/models/Qwen2.5-7B-Instruct")
        res = self._register()
        self.assertEqual(res["total"], 2)
        caps = capabilities.list_capabilities()
        self.assertEqual(len(caps), 2)
        for c in caps:
            self.assertEqual(c["status"], "shadow")
            self.assertTrue(c["compatible"])
            self.assertEqual(c["worker_port"], 8002)

    def test_incompatible_disabled(self):
        _mk_lora(self.loras, "old_qwen3_lora", "/home/luxy/models/Qwen3-4B-Instruct")
        self._register()
        cap = capabilities.get_capability("lora:old_qwen3_lora")
        self.assertFalse(cap["compatible"])
        self.assertEqual(cap["status"], "disabled")
        self.assertIn("incompatible", cap["tags"])

    def test_match_by_domain(self):
        _mk_lora(self.training, "agri_expert_v1", "/home/luxy/models/Qwen2.5-7B-Instruct")
        _mk_lora(self.training, "t1_law_lora_v1", "/home/luxy/models/Qwen2.5-7B-Instruct")
        self._register()
        hits = capabilities.match("农学", top_k=2)
        self.assertTrue(hits, "农学 应匹配 agri_expert_v1")
        self.assertEqual(hits[0]["cap_id"], "lora:agri_expert_v1")
        hits2 = capabilities.match("法律", top_k=2)
        self.assertEqual(hits2[0]["cap_id"], "lora:t1_law_lora_v1")
        self.assertEqual(capabilities.match("完全不存在的领域xyz", top_k=2), [])

    def test_match_excludes_disabled(self):
        _mk_lora(self.loras, "old_qwen3_lora", "/home/luxy/models/Qwen3-4B-Instruct")
        self._register()
        self.assertEqual(capabilities.match("old", top_k=2), [])

    def test_status_and_record_use(self):
        _mk_lora(self.training, "agri_expert_v1", "/home/luxy/models/Qwen2.5-7B-Instruct")
        self._register()
        cid = "lora:agri_expert_v1"
        c = capabilities.set_status(cid, "active")
        self.assertEqual(c["status"], "active")
        c = capabilities.record_use(cid, success=True)
        self.assertEqual(c["uses"], 1)
        self.assertEqual(c["success_rate"], 1.0)
        c = capabilities.record_use(cid, success=False)
        self.assertEqual(c["uses"], 2)
        self.assertAlmostEqual(c["success_rate"], 0.5, places=3)
        with self.assertRaises(ValueError):
            capabilities.set_status(cid, "bogus")
        with self.assertRaises(ValueError):
            capabilities.record_use("no:such", success=True)

    def test_summary(self):
        _mk_lora(self.training, "agri_expert_v1", "/home/luxy/models/Qwen2.5-7B-Instruct")
        self._register()
        s = capabilities.summary()
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["by_status"].get("shadow"), 1)
        self.assertIn("registry_path", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
