# -*- coding: utf-8 -*-
"""P2.1/P2.2 动态知识库 + 隔离 + TTL + 配额 测试。

不加载真实 embedding / 不碰真实 Chroma（全部打桩），只验证：
  · 动态库 meta 字段（tier/owner_session/ttl_hours/expires_at）
  · 隔离：check_session（动态库只对 owner 会话可见/可写；静态库不限制）
  · TTL：is_expired / cleanup_expired_dynamic（先 dry_run 再真删）
  · 配额：超过 KB_MAX_CHUNKS 拒绝
  · 审计：append 事件写入 audit.jsonl
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import kb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class FakeCollection:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.metadata = {"hnsw:space": "cosine"}

    def add(self, ids=None, documents=None, embeddings=None, metadatas=None):
        self.added.append({"ids": list(ids or []), "documents": list(documents or [])})

    def delete(self, where=None):
        self.deleted.append(where)

    def count(self):
        return sum(len(a["ids"]) for a in self.added)


class FakeClient:
    def __init__(self):
        self.colls = {}

    def get_collection(self, name):
        if name not in self.colls:
            raise ValueError("no collection: %s" % name)
        return self.colls[name]

    def create_collection(self, name, metadata=None):
        self.colls[name] = FakeCollection()
        return self.colls[name]

    def delete_collection(self, name):
        self.colls.pop(name, None)


class FakeEnvMixin:
    """把 chroma/embedding 全打桩，让 append_text_dynamic 可在纯单测里跑"""
    def _start_fakes(self):
        self.client = FakeClient()
        patches = [
            mock.patch.object(kb, "_get_chroma", return_value=self.client),
            mock.patch.object(kb, "_encode",
                              side_effect=lambda texts, model_ref=None: [[0.1] * 8 for _ in texts]),
            mock.patch.object(kb, "embed_signature", return_value={"model": "fake", "dim": 8}),
            mock.patch.object(kb, "_kb_model_for_use", return_value="fake"),
            mock.patch.object(kb, "check_embedding_consistency", return_value=(True, "")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


def _meta_dynamic(expires_at=None, owner="s1"):
    now = datetime.now()
    return {
        "name": "x", "tier": "dynamic", "owner_session": owner,
        "ttl_hours": 2,
        "expires_at": (expires_at or (now + timedelta(hours=2))).isoformat(),
    }


class TestDynamicMeta(unittest.TestCase):
    NAME = "_dyn_ut_meta"

    def tearDown(self):
        try:
            kb.delete_kb(self.NAME)
        except Exception:
            pass

    def test_create_dynamic_meta(self):
        m = kb.create_kb(self.NAME, description="ut", dynamic=True,
                         owner_session="s1", ttl_hours=2)
        self.assertEqual(m.get("tier"), "dynamic")
        self.assertEqual(m.get("owner_session"), "s1")
        self.assertEqual(m.get("ttl_hours"), 2)
        self.assertTrue(m.get("expires_at"))
        self.assertTrue(kb.is_dynamic(m))
        self.assertFalse(kb.is_expired(m))
        self.assertTrue(kb.check_session(m, "s1"))
        self.assertFalse(kb.check_session(m, "s2"))

    def test_static_kb_not_dynamic(self):
        m = kb.create_kb(self.NAME)
        self.assertFalse(kb.is_dynamic(m))
        self.assertFalse(kb.is_expired(m))
        self.assertTrue(kb.check_session(m, "anyone"))

    def test_ttl_clamped(self):
        m = kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=999999)
        self.assertLessEqual(int(m.get("ttl_hours") or 0), 24 * 30)


class TestExpiry(unittest.TestCase):
    def test_expired_meta(self):
        m = _meta_dynamic(expires_at=datetime.now() - timedelta(hours=1))
        self.assertTrue(kb.is_expired(m))

    def test_not_expired(self):
        self.assertFalse(kb.is_expired(_meta_dynamic()))

    def test_static_never_expired(self):
        self.assertFalse(kb.is_expired({"name": "x"}))


class TestCleanup(unittest.TestCase):
    NAME = "_dyn_ut_cleanup"

    def tearDown(self):
        try:
            kb.delete_kb(self.NAME)
        except Exception:
            pass

    def test_cleanup_dry_run_then_real(self):
        m = kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=1)
        m = kb.get_kb(self.NAME)
        m["expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat()
        kb._save_kb_meta(self.NAME, m)
        dry = kb.cleanup_expired_dynamic(dry_run=True)
        self.assertIn(self.NAME, dry)
        self.assertIsNotNone(kb.get_kb(self.NAME), "dry_run 不应真删")
        real = kb.cleanup_expired_dynamic(dry_run=False)
        self.assertIn(self.NAME, real)
        self.assertIsNone(kb.get_kb(self.NAME), "真清理后应已删除")


class TestAppendAndQuota(unittest.TestCase, FakeEnvMixin):
    NAME = "_dyn_ut_append"

    def setUp(self):
        self._start_fakes()

    def tearDown(self):
        try:
            kb.delete_kb(self.NAME)
        except Exception:
            pass

    def test_append_dynamic_and_audit(self):
        kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=2)
        res = kb.append_text_dynamic(self.NAME, "这是一段用于测试动态知识库追加的文本内容。" * 3,
                                     source="task", session_id="s1")
        self.assertGreater(res.get("added_chunks", 0), 0)
        audit = Path(kb.KB_DIR) / self.NAME / "audit.jsonl"
        self.assertTrue(audit.exists())
        self.assertIn("append", audit.read_text(encoding="utf-8"))

    def test_append_wrong_session_rejected(self):
        kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=2)
        with self.assertRaises(ValueError):
            kb.append_text_dynamic(self.NAME, "x" * 50, session_id="s2")

    def test_append_static_rejected(self):
        kb.create_kb(self.NAME)
        with self.assertRaises(ValueError):
            kb.append_text_dynamic(self.NAME, "x" * 50, session_id="s1")

    def test_quota_exceeded(self):
        kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=2)
        with mock.patch.object(kb, "KB_MAX_CHUNKS", 1):
            with self.assertRaises(ValueError):
                kb.append_text_dynamic(self.NAME, "很长的测试文本。" * 400, session_id="s1")

    def test_append_expired_rejected(self):
        kb.create_kb(self.NAME, dynamic=True, owner_session="s1", ttl_hours=1)
        m = kb.get_kb(self.NAME)
        m["expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat()
        kb._save_kb_meta(self.NAME, m)
        with self.assertRaises(ValueError):
            kb.append_text_dynamic(self.NAME, "x" * 50, session_id="s1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
