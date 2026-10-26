# -*- coding: utf-8 -*-
"""基准库（model_registry）测试：种子/扫描/状态/可调配/可挂 LoRA/主脑 prompt"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_registry  # noqa: E402


class TestModelRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mr_ut_"))
        self._orig = (model_registry.REGISTRY_PATH, model_registry.MODELS_DIR)
        model_registry.REGISTRY_PATH = self.tmp / "registry.json"
        model_registry.MODELS_DIR = self.tmp / "models"
        model_registry.MODELS_DIR.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, model_registry, "REGISTRY_PATH", self._orig[0])
        self.addCleanup(setattr, model_registry, "MODELS_DIR", self._orig[1])

    def test_seed_and_list(self):
        reg = model_registry.seed()
        self.assertGreaterEqual(len(reg["models"]), 6)
        models = model_registry.list_models()
        self.assertTrue(any(m["model_id"] == "qwen2.5-14b-awq" for m in models))
        main = model_registry.get_model("qwen2.5-14b-awq")
        self.assertEqual(main["role"], "main_brain")
        self.assertFalse(main["lora"]["supported"])
        worker = model_registry.get_model("qwen2.5-7b-awq")
        self.assertTrue(worker["lora"]["supported"])
        self.assertEqual(worker["lora"]["max_loras"], 2)
        self.assertIn("单步专业能力（多 LoRA）", worker["boundaries"]["can"])
        self.assertTrue(any("复杂多步" in x for x in worker["boundaries"]["cannot"]))

    def test_dispatchable(self):
        model_registry.seed()
        model_registry.set_status("qwen2.5-14b-awq", "running")
        model_registry.set_status("qwen2.5-7b-awq", "running")
        model_registry.set_status("gemma-2-27b-gptq", "unavailable")
        ids = [m["model_id"] for m in model_registry.dispatchable()]
        self.assertIn("qwen2.5-14b-awq", ids)
        self.assertIn("qwen2.5-7b-awq", ids)
        self.assertNotIn("gemma-2-27b-gptq", ids)

    def test_set_status_unknown(self):
        with self.assertRaises(ValueError):
            model_registry.set_status("no-such-model", "running")

    def test_lora_mountable_uses_capabilities(self):
        model_registry.seed()
        fake_caps = [
            {"cap_id": "lora:agri_expert_v1", "name": "agri_expert_v1", "status": "shadow",
             "compatible": True, "base_model": "/home/luxy/models/Qwen2.5-7B-Instruct", "rank": 16},
            {"cap_id": "lora:old", "name": "old", "status": "disabled",
             "compatible": False, "base_model": "/home/luxy/models/Qwen3-4B-Instruct", "rank": 16},
        ]
        with mock.patch("capabilities.list_capabilities", return_value=fake_caps):
            loras = model_registry.lora_mountable("qwen2.5-7b-awq")
        self.assertEqual(len(loras), 1)
        self.assertEqual(loras[0]["cap_id"], "lora:agri_expert_v1")
        # 主脑不支持 LoRA → 空
        self.assertEqual(model_registry.lora_mountable("qwen2.5-14b-awq"), [])

    def test_prompt_text(self):
        model_registry.seed()
        model_registry.set_status("qwen2.5-14b-awq", "running")
        model_registry.set_status("qwen2.5-7b-awq", "running")
        txt = model_registry.prompt_text()
        self.assertIn("qwen2.5-14b-awq", txt)
        self.assertIn("主脑", txt)
        self.assertIn("不可挂 LoRA", txt)
        self.assertIn("可挂 LoRA", txt)

    def test_scan_updates_exists(self):
        model_registry.seed()
        # 造一个存在的模型目录
        d = model_registry.MODELS_DIR / "Qwen2.5-14B-Instruct-AWQ"
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"max_position_embeddings": 32768,
                                                   "architectures": ["Qwen2ForCausalLM"]}))
        (d / "model.safetensors").write_bytes(b"\x00" * 1024)
        reg = model_registry.scan(probe_ports=())
        m = reg["models"]["qwen2.5-14b-awq"]
        self.assertTrue(m["exists"])
        self.assertGreater(m["size_gb_on_disk"], 0)

    def test_summary(self):
        model_registry.seed()
        s = model_registry.summary()
        self.assertGreaterEqual(s["total"], 6)
        self.assertIn("main_brain", s["by_role"])
        self.assertIn("worker", s["by_role"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
