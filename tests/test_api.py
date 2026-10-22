# -*- coding: utf-8 -*-
"""
API 层测试：用 FastAPI TestClient 走真实的中间件栈。

两个关键前提（很重要，否则会污染真实数据）：
1. lora_manager 在 **import 时** 会执行 db.init_db() 并扫描 outputs/ 入库，
   所以必须先改写 db.DB_PATH，再 import lora_manager；
2. 所有涉及 settings 的用例都指向临时 settings.json，绝不碰真实配置文件。

注意：本文件**故意只测试校验失败路径**。任何"合法参数 + 合法条数"的
/training/start 都会真的启动 GPU 训练，绝不能出现在单元测试里。
"""
import hashlib
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import db

_TMP = Path(tempfile.mkdtemp(prefix="lora_api_test_"))
db.DB_PATH = _TMP / "test_workbench.db"      # 必须在 import lora_manager 之前

import lora_manager                            # noqa: E402

# ★ 发布配置的字节级快照：任何测试都不许改它。
#   为什么要有这条：曾经有用例不带 ?name= 调 POST /workflow/config/import，
#   端点缺省就写 workflow_config.json，而 normalize_config 会 setdefault("position", None)
#   + 用 open(...,'w') 写 LF —— 于是"跑一遍测试"就悄悄抹掉了 9 个节点的 DAG 布局坐标，
#   还把文件的 CRLF 改成 LF。这类损坏是**静默**的，只有测试自己盯着才防得住。
_SHIPPED_CFG = lora_manager.PROJECT_DIR / "workflow_config.json"
_SHIPPED_CFG_SHA = (hashlib.sha256(_SHIPPED_CFG.read_bytes()).hexdigest()
                    if _SHIPPED_CFG.exists() else None)


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)
    if _SHIPPED_CFG_SHA is None:
        return
    now = hashlib.sha256(_SHIPPED_CFG.read_bytes()).hexdigest()
    if now != _SHIPPED_CFG_SHA:
        raise AssertionError(
            "有测试改写了随项目发布的 %s！写配置的端点必须带 ?name= 指向临时文件，"
            "否则会归一化重写发布配置（丢 position、改行尾）。" % _SHIPPED_CFG.name)
from fastapi.testclient import TestClient      # noqa: E402

FAKE_KEY = "sk-test-dummy-key-not-real-000000"
NEW_KEY = "sk-test-dummy-key-not-real-000000"


def _write_settings(path, openai_key=FAKE_KEY):
    path.write_text(json.dumps({
        "base_model_path": "/tmp/fake-base",
        "lora_dir": str(_TMP / "loras"),
        "vllm_chat_api": "http://localhost:8000/v1/chat/completions",
        "lora_manager_api": "http://localhost:8001",
        "venv_python": "/tmp/python",
        "providers": {
            "vllm": {"api_base": "http://127.0.0.1:8000/v1", "model": "", "api_key": ""},
            "ollama": {"api_base": "http://127.0.0.1:11434", "model": "qwen2.5:7b", "api_key": ""},
            "openai": {"api_base": "https://api.deepseek.com/v1",
                       "model": "deepseek-chat", "api_key": openai_key},
        },
    }, ensure_ascii=False), encoding="utf-8")



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()

class ApiTestCase(unittest.TestCase):
    """每个用例一份临时 settings.json。"""

    def setUp(self):
        # 让本文件不依赖测试执行顺序（test_db 也会改 db.DB_PATH）
        db.DB_PATH = _TMP / "test_workbench.db"
        name = self.id().split(".")[-1]
        self.settings_path = _TMP / f"settings_{name}.json"
        _write_settings(self.settings_path)
        self._orig = lora_manager.SETTINGS_FILE
        lora_manager.SETTINGS_FILE = self.settings_path
        self.client = TestClient(lora_manager.app)

    def tearDown(self):
        lora_manager.SETTINGS_FILE = self._orig

    def stored_key(self):
        data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        return data["providers"]["openai"]["api_key"]


class TestHealth(ApiTestCase):
    def test_health_ok(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")
        # ★ 2026-10-22: /health 现在带 version 字段（发布包 VERSION）
        self.assertIn("version", r.json())


class TestKeyMasking(ApiTestCase):
    def test_settings_hides_plaintext_key(self):
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(FAKE_KEY, r.text)
        self.assertNotIn(FAKE_KEY[6:-4], r.text)   # 中段特征串也不能出现
        self.assertEqual(r.json()["providers"]["openai"]["api_key"], "")

    def test_providers_hides_plaintext_key(self):
        r = self.client.get("/providers")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(FAKE_KEY, r.text)
        self.assertEqual(r.json()["openai"]["api_key"], "")

    def test_configured_flag_true_when_key_present(self):
        r = self.client.get("/providers")
        self.assertTrue(r.json()["openai"]["api_key_set"])

    def test_configured_flag_false_when_key_absent(self):
        _write_settings(self.settings_path, openai_key="")
        r = self.client.get("/providers")
        self.assertFalse(r.json()["openai"]["api_key_set"])
        self.assertEqual(r.json()["vllm"]["api_key_set"], False)

    def test_non_secret_fields_still_returned(self):
        r = self.client.get("/providers")
        self.assertEqual(r.json()["openai"]["api_base"], "https://api.deepseek.com/v1")
        self.assertEqual(r.json()["openai"]["model"], "deepseek-chat")

    def test_put_with_masked_value_does_not_overwrite(self):
        r = self.client.put("/providers/openai", json={
            "api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "api_key": "sk-test-dummy-key-not-real-000000",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_key(), FAKE_KEY)
        self.assertNotIn(FAKE_KEY, r.text)
        self.assertNotIn("*", self.stored_key())

    def test_put_with_empty_value_does_not_overwrite(self):
        r = self.client.put("/providers/openai", json={
            "api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "api_key": "",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_key(), FAKE_KEY)

    def test_put_with_new_key_updates(self):
        r = self.client.put("/providers/openai", json={
            "api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat",
            "api_key": NEW_KEY,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertNotIn(NEW_KEY, r.text)          # 响应也不能回显明文
        self.assertTrue(r.json()["config"]["api_key_set"])

    def test_put_unknown_provider_rejected(self):
        r = self.client.put("/providers/bogus", json={"api_base": "x"})
        self.assertEqual(r.status_code, 400)

    def test_put_settings_does_not_wipe_providers(self):
        r = self.client.put("/settings", json={"base_model_path": "/tmp/other"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_key(), FAKE_KEY)
        self.assertEqual(r.json()["base_model_path"], "/tmp/other")


class TestCors(ApiTestCase):
    def test_browser_origin_from_other_site_is_denied(self):
        r = self.client.get("/health", headers={"Origin": "http://evil.example.com"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_lan_origin_is_denied(self):
        r = self.client.get("/health", headers={"Origin": "http://192.168.1.50:8080"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_localhost_origin_allowed(self):
        r = self.client.get("/health", headers={"Origin": "http://localhost:8080"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:8080")

    def test_127_origin_allowed(self):
        r = self.client.get("/health", headers={"Origin": "http://127.0.0.1:8080"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://127.0.0.1:8080")

    def test_other_local_port_allowed(self):
        r = self.client.get("/health", headers={"Origin": "http://localhost:9999"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:9999")


class TestProvidersEndpointSecurity(ApiTestCase):
    def test_preflight_from_evil_origin_denied(self):
        r = self.client.options("/providers/openai", headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertNotEqual(r.headers.get("access-control-allow-origin"), "*")
        self.assertNotEqual(r.headers.get("access-control-allow-origin"), "http://evil.example.com")


class TestTrainingValidation(ApiTestCase):
    """只覆盖校验失败路径 —— 成功路径会真的开训。"""

    def _post(self, payload):
        return self.client.post("/training/start", json=payload)

    def test_missing_lora_name_is_422(self):
        r = self._post({"task_desc": "x", "data_count": 100})
        self.assertEqual(r.status_code, 422)

    def test_lora_name_wrong_type_is_422(self):
        r = self._post({"lora_name": 123, "data_count": 100})
        self.assertEqual(r.status_code, 422)

    def test_invalid_lora_name_chars_is_400(self):
        r = self._post({"lora_name": "bad name!", "data_count": 100})
        self.assertEqual(r.status_code, 400)

    def test_data_count_too_small_is_400(self):
        r = self._post({"lora_name": "ok_name", "data_count": 5})
        self.assertEqual(r.status_code, 400)
        self.assertIn("10-5000", r.text)

    def test_data_count_too_large_is_400(self):
        r = self._post({"lora_name": "ok_name", "data_count": 99999})
        self.assertEqual(r.status_code, 400)

    def test_data_count_not_integer_is_422(self):
        r = self._post({"lora_name": "ok_name", "data_count": "abc"})
        self.assertEqual(r.status_code, 422)

    def test_null_base_model_passes_validation(self):
        """回归：base_model 显式传 null 曾经直接 422（pydantic 的 str 不接受 None）。"""
        r = self._post({"lora_name": "ok_name", "data_count": 5, "base_model": None})
        self.assertEqual(r.status_code, 400)          # 卡在条数校验，说明校验层已放行 null
        self.assertIn("10-5000", r.text)

    def test_empty_base_model_passes_validation(self):
        r = self._post({"lora_name": "ok_name", "data_count": 5, "base_model": ""})
        self.assertEqual(r.status_code, 400)

    def test_agent_payload_shape_passes_validation(self):
        """Agent 实际发出的完整请求体形状（条数改成 5 以便停在校验层）。"""
        r = self._post({
            "lora_name": "test_agent_v1", "task_desc": "x", "format_hint": "",
            "data_count": 5, "base_model": "/opt/models/Qwen3-4B-Instruct",
            "provider_type": "vllm", "provider_config": {}, "auto_restart_vllm": True,
            "dataset_file": None, "review_before_train": False,
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("10-5000", r.text)

    def test_review_flag_wrong_type_is_422(self):
        r = self._post({"lora_name": "ok_name", "data_count": 100,
                        "review_before_train": "yes"})
        self.assertNotEqual(r.status_code, 422)       # pydantic 会强制转换，不应报 422


class TestReadOnlyEndpoints(ApiTestCase):
    def test_loras_listing_shape(self):
        r = self.client.get("/loras")
        self.assertEqual(r.status_code, 200)
        self.assertIn("loras", r.json())

    def test_vllm_status_shape(self):
        r = self.client.get("/vllm/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("running", r.json())

    def test_kb_list_shape(self):
        r = self.client.get("/kb/list")
        self.assertEqual(r.status_code, 200)


class TestChatSessionEndpoints(ApiTestCase):
    def _new_session(self, **kw):
        r = self.client.post("/chat/sessions", json=kw)
        self.assertEqual(r.status_code, 200)
        return r.json()["session"]["session_id"]

    def test_full_lifecycle(self):
        sid = self._new_session(mode="agent", model="vllm:base")
        r = self.client.get(f"/chat/sessions/{sid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["session"]["messages"], [])
        self.assertEqual(r.json()["session"]["mode"], "agent")

        r = self.client.post(f"/chat/sessions/{sid}/messages", json={"messages": [
            {"role": "user", "content": "帮我训练一个 LoRA"},
            {"role": "assistant", "content": "好的，正在处理"},
            {"role": "tool", "kind": "tool", "tool_name": "start_training",
             "tool_status": "ok", "tool_args": {"lora_name": "x"}, "tool_result": {"task_id": "t"}},
        ]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["appended"], 3)
        self.assertEqual(r.json()["title"], "帮我训练一个 LoRA")   # 自动标题

        msgs = self.client.get(f"/chat/sessions/{sid}").json()["session"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "tool"])
        self.assertEqual(msgs[2]["tool_args"], {"lora_name": "x"})
        self.assertEqual(msgs[2]["tool_result"], {"task_id": "t"})
        self.assertEqual(msgs[2]["kind"], "tool")

        r = self.client.patch(f"/chat/sessions/{sid}", json={"title": "重命名后"})
        self.assertEqual(r.json()["session"]["title"], "重命名后")
        self.assertEqual(
            self.client.get(f"/chat/sessions/{sid}").json()["session"]["title"], "重命名后")

        self.assertEqual(self.client.delete(f"/chat/sessions/{sid}").status_code, 200)
        self.assertEqual(self.client.get(f"/chat/sessions/{sid}").status_code, 404)

    def test_session_appears_in_listing(self):
        sid = self._new_session()
        ids = [s["session_id"] for s in self.client.get("/chat/sessions").json()["sessions"]]
        self.assertIn(sid, ids)

    def test_unknown_session_is_404(self):
        self.assertEqual(self.client.get("/chat/sessions/nope").status_code, 404)
        self.assertEqual(
            self.client.post("/chat/sessions/nope/messages",
                             json={"messages": [{"role": "user", "content": "x"}]}).status_code, 404)
        self.assertEqual(self.client.patch("/chat/sessions/nope", json={"title": "x"}).status_code, 404)
        self.assertEqual(self.client.delete("/chat/sessions/nope").status_code, 404)

    def test_message_requires_role(self):
        sid = self._new_session()
        r = self.client.post(f"/chat/sessions/{sid}/messages",
                             json={"messages": [{"content": "缺少 role"}]})
        self.assertEqual(r.status_code, 422)

    def test_tool_result_accepts_non_dict(self):
        sid = self._new_session()
        r = self.client.post(f"/chat/sessions/{sid}/messages", json={"messages": [
            {"role": "tool", "kind": "tool", "tool_name": "t", "tool_result": [1, 2, 3]},
        ]})
        self.assertEqual(r.status_code, 200)
        msgs = self.client.get(f"/chat/sessions/{sid}").json()["session"]["messages"]
        self.assertEqual(msgs[0]["tool_result"], [1, 2, 3])

    def test_messages_persist_across_requests(self):
        sid = self._new_session()
        self.client.post(f"/chat/sessions/{sid}/messages",
                         json={"messages": [{"role": "user", "content": "hi"}]})
        again = self.client.get(f"/chat/sessions/{sid}").json()["session"]
        self.assertEqual(again["message_count"], 1)
        self.assertEqual(again["messages"][0]["content"], "hi")

    def test_append_to_new_session_is_incremental(self):
        sid = self._new_session()
        self.client.post(f"/chat/sessions/{sid}/messages",
                         json={"messages": [{"role": "user", "content": "1"}]})
        self.client.post(f"/chat/sessions/{sid}/messages",
                         json={"messages": [{"role": "assistant", "content": "2"}]})
        msgs = self.client.get(f"/chat/sessions/{sid}").json()["session"]["messages"]
        self.assertEqual([m["content"] for m in msgs], ["1", "2"])


class TestContextEndpoint(ApiTestCase):
    def test_vllm_context_returns_positive_limit(self):
        r = self.client.get("/chat/context")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["provider"], "vllm")
        self.assertGreater(d["max_model_len"], 0)
        # vLLM 未运行时回退到保守默认值，也必须是正数
        self.assertIn("source", d)

    def test_known_cloud_model_gets_real_window_and_unknown_falls_back(self):
        """已知云模型要给真实上下文窗口；未知 provider 才回退保守默认值。

        曾经对所有非 vllm 的 provider 一律回退 8192，导致云模型上"任务被截断"
        （系统提示含 33 个工具说明 + 任务 + 读入内容，8192 装不下）。
        """
        known = self.client.get("/chat/context?provider=openai").json()
        self.assertEqual(known["provider"], "openai")
        self.assertGreaterEqual(known["max_model_len"], 32768)
        self.assertIn(known["source"], ("known-model", "settings"))

        unknown = self.client.get("/chat/context?provider=nonexistent").json()
        self.assertEqual(unknown["source"], "default")
        self.assertEqual(unknown["max_model_len"], 8192)


    def test_list_configs(self):
        r = self.client.get("/workflow/configs")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("configs", data)
        self.assertIn("default", data)
        for c in data["configs"]:
            self.assertTrue(c["name"].endswith(".json"))
            self.assertIn("workflow_name", c)
            self.assertIn("nodes", c)

    def test_list_configs_exposes_template_metadata(self):
        """T2 模板画廊要用的元数据：用途、标签、依赖是否满足。"""
        cfgs = self.client.get("/workflow/configs").json()["configs"]
        for c in cfgs:
            for k in ("description", "tags", "author", "required_loras", "required_providers"):
                self.assertIn(k, c, c["name"])
            self.assertIsInstance(c["tags"], list)
            self.assertIsInstance(c["required_loras"], list)
        # 随项目发布的配置必须自描述，否则画廊里是一张空白卡片
        d = [c for c in cfgs if c["name"] == "workflow_config.json"][0]
        self.assertTrue(d["description"], "发布配置缺 description")
        self.assertIsInstance(d.get("required_loras"), list,
                              "发布配置应有 required_loras 字段（4 节点示例为空 list 合法）")

    def test_read_default_by_name(self):
        r = self.client.get("/workflow/config", params={"name": "workflow_config.json"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("nodes", r.json())

    def test_listing_follows_the_workflow_config_naming_convention(self):
        """
        列表只 glob `workflow_config*.json` —— 这是**刻意**的：项目根还有
        `settings.json` 等无关 JSON，不能一并列出来。
        代价是文件名必须符合这个模式，否则会出现"能按名读写、界面上永远看不到"的
        配置（T2 实测克隆时踩到，前端已加同名校验）。
        """
        name = "zz_not_listed_probe.json"
        path = lora_manager.PROJECT_DIR / name
        try:
            self.client.put("/workflow/config", params={"name": name},
                            json={"workflow_name": "不该被列出", "nodes": [
                  {"id": "n1", "type": "input", "input_from": []}]})  # ★ 2026-10-22: 空 nodes 现在被 400 拒绝（防清空预设）
            listed = [c["name"] for c in
                      self.client.get("/workflow/configs").json()["configs"]]
            self.assertNotIn(name, listed, "不符合命名约定的配置不应出现在列表里")
            # 但它确实能按名读到 —— 这正是"危险"之处，所以前端必须拦住这种名字
            self.assertEqual(self.client.get("/workflow/config",
                                             params={"name": name}).status_code, 200)
        finally:
            try:
                path.unlink()
            except Exception:
                pass

    def test_traversal_rejected(self):
        for bad in ("../settings.json", "..%2Fsettings.json", "/etc/passwd",
                    "sub/config.json", "config.txt", ".hidden.json"):
            r = self.client.get("/workflow/config", params={"name": bad})
            self.assertEqual(r.status_code, 400, f"{bad} 应被拒绝，实际 {r.status_code}")

    def test_missing_config_is_404(self):
        r = self.client.get("/workflow/config", params={"name": "workflow_config_nope.json"})
        self.assertEqual(r.status_code, 404)

    def test_write_and_read_named_config(self):
        name = "workflow_config_apitest.json"
        path = lora_manager.PROJECT_DIR / name
        try:
            payload = {"workflow_name": "接口测试用", "nodes": [
                {"id": "in", "type": "input", "input_from": []}]}
            w = self.client.put("/workflow/config", params={"name": name}, json=payload)
            self.assertEqual(w.status_code, 200)
            self.assertEqual(w.json()["name"], name)

            r = self.client.get("/workflow/config", params={"name": name})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["workflow_name"], "接口测试用")

            names = [c["name"] for c in self.client.get("/workflow/configs").json()["configs"]]
            self.assertIn(name, names)
        finally:
            if path.exists():
                path.unlink()

    def test_write_traversal_rejected(self):
        r = self.client.put("/workflow/config", params={"name": "../evil.json"},
                            json={"nodes": []})
        self.assertEqual(r.status_code, 400)



class TestLoopEndpoints(ApiTestCase):
    """
    闭环入口的**拒绝路径**（不触发真实运行/评测 —— 与 /training/start 同一约定）。
    正常路径的验证放在真实运行脚本里做。
    """

    def test_list_uploads(self):
        r = self.client.get("/uploads")
        self.assertEqual(r.status_code, 200)
        self.assertIn("uploads", r.json())

    def test_registry_endpoint(self):
        r = self.client.get("/loras/registry")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json().get("registry"), dict)

    def test_eval_list(self):
        self.assertEqual(self.client.get("/eval/list").status_code, 200)

    def test_eval_status_missing_is_404(self):
        self.assertEqual(self.client.get("/eval/status/nope").status_code, 404)

    def test_eval_start_missing_dataset_is_400(self):
        r = self.client.post("/eval/start", json={
            "lora_name": "x", "dataset_file": "training_data/__nope__.jsonl"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不存在", r.json()["detail"])

    def test_run_saved_rejects_traversal(self):
        for bad in ("../settings.json", "..%2Fx.txt", "sub/x.txt"):
            r = self.client.post("/workflow/run_saved", data={"paper_name": bad})
            self.assertEqual(r.status_code, 400, f"{bad} 应被拒绝")

    def test_run_saved_missing_file_is_404(self):
        r = self.client.post("/workflow/run_saved", data={"paper_name": "__nope__.txt"})
        self.assertEqual(r.status_code, 404)

    def test_export_dataset_requires_fields(self):
        self.assertEqual(self.client.post("/workflow/export_dataset", json={}).status_code, 400)

    def test_export_dataset_rejects_traversal(self):
        r = self.client.post("/workflow/export_dataset",
                             json={"filename": "../settings.json", "node": "e"})
        self.assertEqual(r.status_code, 400)

    def test_export_dataset_rejects_bad_dataset_name(self):
        r = self.client.post("/workflow/export_dataset",
                             json={"filename": "result_x.json", "node": "e",
                                   "dataset": "../evil"})
        self.assertEqual(r.status_code, 400)

    def test_export_dataset_missing_result_is_404(self):
        r = self.client.post("/workflow/export_dataset",
                             json={"filename": "result___nope__.json", "node": "e"})
        self.assertEqual(r.status_code, 404)

    def test_export_dataset_happy_path(self):
        import json as _json
        out_dir = _TMP / "outputs_export"
        out_dir.mkdir(exist_ok=True)
        proj = _TMP / "proj_export"
        (proj / "training_data").mkdir(parents=True, exist_ok=True)
        result = {"workflow_name": "t", "nodes": {"e": "抽取结果"},
                  "_meta": {"traces": {"e": {"prompt": "抽取：论文正文", "prompt_chars": 8,
                                             "output": "抽取结果", "output_chars": 4,
                                             "lora": "extract_v2"}}}}
        (out_dir / "result_test.json").write_text(_json.dumps(result, ensure_ascii=False),
                                                  encoding="utf-8")
        with mock.patch.object(lora_manager, "OUTPUT_DIR", out_dir), \
             mock.patch.object(lora_manager, "PROJECT_DIR", proj):
            r = self.client.post("/workflow/export_dataset",
                                 json={"filename": "result_test.json", "node": "e",
                                       "dataset": "loop_test"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["lines_after"], 1)
            ds = proj / "training_data" / "loop_test.jsonl"
            self.assertTrue(ds.exists())
            rows = [_json.loads(x) for x in ds.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([c["from"] for c in rows[0]["conversations"]],
                             ["system", "human", "gpt"])
            self.assertEqual(rows[0]["conversations"][1]["value"], "抽取：论文正文")
            # 再导出一次应追加为第 2 行
            r2 = self.client.post("/workflow/export_dataset",
                                  json={"filename": "result_test.json", "node": "e",
                                        "dataset": "loop_test"})
            self.assertEqual(r2.json()["lines_after"], 2)

    def test_export_dataset_missing_trace_is_404(self):
        import json as _json
        out_dir = _TMP / "outputs_export2"
        out_dir.mkdir(exist_ok=True)
        (out_dir / "result_notrace.json").write_text(
            _json.dumps({"nodes": {}, "_meta": {}}), encoding="utf-8")
        with mock.patch.object(lora_manager, "OUTPUT_DIR", out_dir):
            r = self.client.post("/workflow/export_dataset",
                                 json={"filename": "result_notrace.json", "node": "e"})
            self.assertEqual(r.status_code, 404)
            self.assertIn("trace", r.json()["detail"])



class TestQualitySummary(ApiTestCase):
    """质量台账：扫描历次运行结果自动累积，按节点聚合。"""

    def test_thresholds_reported_match_the_engine(self):
        """
        阈值只能有**一处真源**。这里以前在 lora_manager 里又写死了一份，结果引擎把
        grounding 从 0.8 提到 0.9 之后，台账接口还在对外报 0.8 —— 用户看到的判据
        和实际判定的不是一回事。现在从引擎读，这条测试守住它。
        """
        import workflow_engine as eng
        th = self.client.get("/quality/summary").json()["thresholds"]
        self.assertEqual(th["fact_retention_min"], eng.QUALITY_FACT_RETENTION_MIN)
        self.assertEqual(th["grounding_min"], eng.QUALITY_GROUNDING_MIN)
        self.assertEqual(th["min_chars_per_1k"], eng.QUALITY_MIN_CHARS_PER_1K)
        # 三个值都要是真值，不能是 None（None 说明引擎没导入成功，等于没守住）
        for k, v in th.items():
            self.assertIsNotNone(v, k)


    def _write_results(self):
        import json as _json
        out_dir = _TMP / "outputs_quality"
        out_dir.mkdir(exist_ok=True)
        for i, (node, q, warned) in enumerate([
            ("extract_method", {"source": "type", "eligible": True, "grounding": 1.0,
                                "key_fact_retention": 1.0, "key_facts": 19,
                                "key_facts_matched": 19, "input_chars": 39500,
                                "output_chars": 1400, "chars_per_1k_input": 36.6}, False),
            ("extract_method", {"source": "type", "eligible": True, "grounding": 0.42,
                                "key_fact_retention": 0.16, "key_facts": 19,
                                "key_facts_matched": 3, "input_chars": 39500,
                                "output_chars": 900, "chars_per_1k_input": 22.8}, True),
            ("finaloutcome", {"source": "text-output", "eligible": True, "grounding": 0.31,
                              "key_fact_retention": 0.74, "key_facts": 20,
                              "key_facts_matched": 15, "input_chars": 5000,
                              "output_chars": 3400, "chars_per_1k_input": 680.0}, False),
        ]):
            meta = {"quality": {node: q}}
            if warned:
                meta["quality_warnings"] = [{"node": node, "reason": "低"}]
            (out_dir / f"result_q{i}.json").write_text(
                _json.dumps({"nodes": {}, "_meta": meta}, ensure_ascii=False), encoding="utf-8")
        return out_dir

    def test_aggregates_by_node(self):
        out_dir = self._write_results()
        with mock.patch.object(lora_manager, "OUTPUT_DIR", out_dir):
            r = self.client.get("/quality/summary")
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertIn("thresholds", d)
            self.assertEqual(d["total_rows"], 3)
            by = {n["node"]: n for n in d["nodes"]}
            self.assertEqual(by["extract_method"]["runs"], 2)
            self.assertEqual(by["extract_method"]["warnings"], 1)
            self.assertTrue(by["extract_method"]["judged"])
            self.assertAlmostEqual(by["extract_method"]["grounding"]["avg"], 0.71, places=2)
            self.assertAlmostEqual(by["extract_method"]["grounding"]["min"], 0.42, places=2)
            self.assertAlmostEqual(by["extract_method"]["key_fact_retention"]["min"], 0.16, places=2)
            # 改写型节点：有样本但不受判定
            self.assertFalse(by["finaloutcome"]["judged"])
            self.assertEqual(by["finaloutcome"]["warnings"], 0)
            # 有告警的节点排在前面
            self.assertEqual(d["nodes"][0]["node"], "extract_method")

    def test_node_filter(self):
        out_dir = self._write_results()
        with mock.patch.object(lora_manager, "OUTPUT_DIR", out_dir):
            d = self.client.get("/quality/summary", params={"node": "finaloutcome"}).json()
            self.assertEqual([n["node"] for n in d["nodes"]], ["finaloutcome"])
            self.assertEqual(d["total_rows"], 1)

    def test_empty_dir_is_ok(self):
        empty = _TMP / "outputs_quality_empty"
        empty.mkdir(exist_ok=True)
        with mock.patch.object(lora_manager, "OUTPUT_DIR", empty):
            d = self.client.get("/quality/summary").json()
            self.assertEqual(d["nodes"], [])
            self.assertEqual(d["total_rows"], 0)

    def test_null_metrics_do_not_crash(self):
        """没有数字/缩写的输出 → key_fact_retention 为 None，聚合不能崩。"""
        import json as _json
        out_dir = _TMP / "outputs_quality_none"
        out_dir.mkdir(exist_ok=True)
        (out_dir / "result_n.json").write_text(_json.dumps({"_meta": {"quality": {
            "e": {"source": "type", "eligible": True, "grounding": 1.0,
                  "key_fact_retention": None, "key_facts": 0, "key_facts_matched": 0}}}},
            ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(lora_manager, "OUTPUT_DIR", out_dir):
            d = self.client.get("/quality/summary").json()
            self.assertEqual(d["nodes"][0]["key_fact_retention"]["n"], 0)
            self.assertIsNone(d["nodes"][0]["key_fact_retention"]["avg"])



class TestRegistryAndTasks(ApiTestCase):
    """S3：引擎注册表是唯一真源；S4：共用任务系统与统一任务视图。"""

    def test_node_types_endpoint_exposes_registry(self):
        r = self.client.get("/workflow/node-types")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertIn("fields", d)
        self.assertIn("types", d)
        names = {t["type"] for t in d["types"]}
        # 注册表是引擎那份，必须含任务类型与管道类型
        self.assertIn("extract", names)
        self.assertIn("aggregate", names)
        ext = next(t for t in d["types"] if t["type"] == "extract")
        self.assertEqual(ext["output"], "text")
        self.assertEqual(ext["quality"], "grounding")
        self.assertTrue(ext["defaults"].get("cache"))
        # 字段表也要在
        self.assertIn("chunk_workers", d["fields"])
        self.assertIn("on_error", d["fields"])

    def test_validate_reports_engine_lint(self):
        import json as _json
        name = "workflow_config_validatetest.json"
        path = lora_manager.PROJECT_DIR / name
        try:
            path.write_text(_json.dumps({"workflow_name": "t", "nodes": [
                {"id": "in", "type": "input", "input_from": []},
                {"id": "x", "type": "magic_type", "input_from": ["ghost"]}]},
                ensure_ascii=False), encoding="utf-8")
            r = self.client.get("/workflow/validate", params={"name": name})
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertFalse(d["ok"])
            joined = " ".join(d["errors"])
            self.assertIn("magic_type", joined)      # 未知类型
            self.assertIn("ghost", joined)           # 坏引用
        finally:
            if path.exists():
                path.unlink()

    def test_validate_ok_for_shipped_config(self):
        d = self.client.get("/workflow/validate",
                            params={"name": "workflow_config.json"}).json()
        self.assertTrue(d["ok"], d["errors"])
        self.assertEqual(d["errors"], [])

    def test_validate_missing_is_404(self):
        self.assertEqual(self.client.get("/workflow/validate",
                                         params={"name": "nope.json"}).status_code, 404)

    def test_validate_rejects_traversal(self):
        r = self.client.get("/workflow/validate", params={"name": "../settings.json"})
        self.assertEqual(r.status_code, 400)

    def test_import_rejects_invalid_config(self):
        # 显式给 name：不给就会落到**默认配置**上。这条用例当前因为校验失败而
        # 在写盘前就 400 了，但只要哪天校验放宽，它就会改写发布配置。
        r = self.client.post("/workflow/config/import",
                             params={"name": "workflow_config_apitest_bad.json"},
                             json={"nodes": [{"id": "x", "type": "nope"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("errors", r.json()["detail"])

    def test_tasks_view_lists_registries(self):
        r = self.client.get("/tasks")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertIn("tasks", d)
        self.assertIn("count", d)

    def test_task_registry_semantics(self):
        reg = lora_manager.TaskRegistry("unit", max_keep=3)
        tid = reg.create(stage="跑", foo=1)
        self.assertEqual(reg.get(tid)["status"], "running")
        self.assertEqual(reg.get(tid)["kind"], "unit")
        self.assertEqual(reg.get(tid)["foo"], 1)
        reg.append_log(tid, "一行日志")
        self.assertEqual(reg.get(tid)["log"], ["一行日志"])
        reg.finish(tid, extra_field="x")
        self.assertEqual(reg.get(tid)["status"], "success")
        self.assertIsNotNone(reg.get(tid)["finished_at"])
        self.assertEqual(reg.get(tid)["extra_field"], "x")
        tid2 = reg.create()
        reg.fail(tid2, "炸了")
        self.assertEqual(reg.get(tid2)["status"], "failed")
        self.assertIn("炸了", reg.get(tid2)["error"])
        self.assertEqual(reg.list()[0]["task_id"], tid2)      # 最新在前
        self.assertIsNone(reg.get("nope"))
        reg.append_log("nope", "x")                            # 不存在的任务不炸
        for _ in range(6):
            reg.create()
        self.assertLessEqual(len(reg.tasks), 3)                # 容量控制



class TestNamedConfigExportImport(ApiTestCase):
    """S7：export/import 必须和 GET/PUT 一样尊重 ?name=，否则会悄悄操作默认配置。"""

    def test_export_honours_name(self):
        import json as _json
        name = "workflow_config_exporttest.json"
        path = lora_manager.PROJECT_DIR / name
        try:
            path.write_text(_json.dumps({"workflow_name": "导出用", "nodes": [
                {"id": "in", "type": "input", "input_from": []}]}, ensure_ascii=False),
                encoding="utf-8")
            r = self.client.get("/workflow/config/export", params={"name": name})
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertEqual(d["name"], name)
            self.assertEqual(d["workflow_name"], "导出用")
        finally:
            if path.exists():
                path.unlink()

    def test_export_default_still_works(self):
        r = self.client.get("/workflow/config/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("nodes", r.json())

    def test_export_missing_is_404_and_traversal_400(self):
        self.assertEqual(self.client.get("/workflow/config/export",
                                         params={"name": "nope.json"}).status_code, 404)
        self.assertEqual(self.client.get("/workflow/config/export",
                                         params={"name": "../settings.json"}).status_code, 400)

    def test_import_writes_to_named_config(self):
        name = "workflow_config_importtest.json"
        path = lora_manager.PROJECT_DIR / name
        try:
            r = self.client.post("/workflow/config/import", params={"name": name}, json={
                "workflow_name": "导入用", "nodes": [
                    {"id": "in", "type": "input", "input_from": []}]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["name"], name)
            self.assertTrue(path.exists())
            # 默认配置不能被这次导入污染
            default = self.client.get("/workflow/config").json()
            self.assertNotEqual(default.get("workflow_name"), "导入用")
        finally:
            if path.exists():
                path.unlink()

    def test_import_traversal_rejected(self):
        r = self.client.post("/workflow/config/import", params={"name": "../evil.json"},
                             json={"nodes": [{"id": "in", "type": "input"}]})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
