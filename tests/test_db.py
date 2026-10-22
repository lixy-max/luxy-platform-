# -*- coding: utf-8 -*-
"""SQLite 持久化层测试：全部跑在临时库上，不碰 data/workbench.db。"""
import json
import tempfile
import time
import unittest
from pathlib import Path

import db



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class DbTestCase(unittest.TestCase):
    def setUp(self):
        self._orig = db.DB_PATH
        self.tmp = Path(tempfile.mkdtemp(prefix="lora_db_test_"))
        db.DB_PATH = self.tmp / "t.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig


class TestTaskCrud(DbTestCase):
    def _task(self, **kw):
        base = {
            "task_id": "t1", "status": "success", "file_name": "paper.pdf",
            "result_file": "result_x.json", "total_nodes": 3, "elapsed": 1.5,
            "started_at": "2026-01-01T00:00:00", "finished_at": "2026-01-01T00:00:02",
        }
        base.update(kw)
        return base

    def test_roundtrip(self):
        db.upsert_task(self._task())
        got = db.get_task("t1")
        self.assertEqual(got["file_name"], "paper.pdf")
        self.assertEqual(got["total_nodes"], 3)

    def test_upsert_updates_existing(self):
        db.upsert_task(self._task())
        db.upsert_task(self._task(status="failed"))
        self.assertEqual(db.get_task("t1")["status"], "failed")
        self.assertEqual(len(db.list_tasks()), 1)

    def test_json_fields_are_serialized_and_parsed(self):
        db.upsert_task(self._task(config_snapshot={"a": 1}, result_json={"nodes": {"x": 1}},
                                  log=["l1", "l2"], tags=["tag1"]))
        got = db.get_task("t1")
        self.assertEqual(got["config_snapshot"], {"a": 1})
        self.assertEqual(got["result_json"]["nodes"]["x"], 1)
        self.assertEqual(got["log"], ["l1", "l2"])
        self.assertEqual(got["tags"], ["tag1"])

    def test_get_missing_returns_none(self):
        self.assertIsNone(db.get_task("nope"))

    def test_list_filter_by_status(self):
        db.upsert_task(self._task(task_id="a", status="success"))
        db.upsert_task(self._task(task_id="b", status="failed"))
        self.assertEqual([t["task_id"] for t in db.list_tasks(status="failed")], ["b"])

    def test_list_search_by_name_and_notes(self):
        db.upsert_task(self._task(task_id="a", file_name="attention.pdf"))
        db.upsert_task(self._task(task_id="b", file_name="other.pdf"))
        db.update_task_meta("b", notes="关于注意力机制")
        hits = {t["task_id"] for t in db.list_tasks(q="注意力")}
        self.assertIn("b", hits)

    def test_list_limit_offset(self):
        for i in range(5):
            db.upsert_task(self._task(task_id=f"t{i}", started_at=f"2026-01-0{i+1}T00:00:00"))
        self.assertEqual(len(db.list_tasks(limit=2)), 2)
        self.assertEqual(len(db.list_tasks(limit=10, offset=4)), 1)

    def test_update_meta(self):
        db.upsert_task(self._task())
        db.update_task_meta("t1", tags=["x"], notes="备注")
        got = db.get_task("t1")
        self.assertEqual(got["tags"], ["x"])
        self.assertEqual(got["notes"], "备注")

    def test_delete(self):
        db.upsert_task(self._task())
        db.delete_task("t1")
        self.assertIsNone(db.get_task("t1"))


class TestScanExistingResults(DbTestCase):
    def test_imports_result_files(self):
        outdir = self.tmp / "outputs"
        outdir.mkdir()
        (outdir / "result_20260101_000000.json").write_text(json.dumps({
            "source_file": "paper.pdf",
            "workflow": "w",
            "nodes": {"a": 1},
            "final_report": "报告",
        }, ensure_ascii=False), encoding="utf-8")

        n = db.scan_existing_results(outdir)
        self.assertEqual(n, 1)
        tasks = db.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["file_name"], "paper.pdf")

    def test_second_scan_is_idempotent(self):
        outdir = self.tmp / "outputs"
        outdir.mkdir()
        (outdir / "result_20260101_000000.json").write_text(
            json.dumps({"source_file": "p.pdf", "nodes": {}}), encoding="utf-8")
        db.scan_existing_results(outdir)
        db.scan_existing_results(outdir)
        self.assertEqual(len(db.list_tasks()), 1)


class TestChatSessions(DbTestCase):
    def test_create_and_get(self):
        s = db.create_chat_session(mode="agent", model="vllm:base")
        self.assertTrue(s["session_id"])
        self.assertEqual(s["mode"], "agent")
        self.assertEqual(s["model"], "vllm:base")
        self.assertEqual(s["message_count"], 0)
        self.assertEqual(db.get_chat_session(s["session_id"])["messages"], [])

    def test_get_missing_returns_none(self):
        self.assertIsNone(db.get_chat_session("nope"))

    def test_append_updates_count_and_seq(self):
        s = db.create_chat_session()
        info = db.append_chat_messages(s["session_id"], [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "在的"},
        ])
        self.assertEqual(info["appended"], 2)
        self.assertEqual(info["message_count"], 2)
        msgs = db.get_chat_session(s["session_id"])["messages"]
        self.assertEqual([m["content"] for m in msgs], ["你好", "在的"])
        self.assertEqual([m["seq"] for m in msgs], [1, 2])

    def test_append_is_incremental(self):
        s = db.create_chat_session()
        db.append_chat_messages(s["session_id"], [{"role": "user", "content": "a"}])
        db.append_chat_messages(s["session_id"], [{"role": "assistant", "content": "b"}])
        msgs = db.get_chat_session(s["session_id"])["messages"]
        self.assertEqual([m["content"] for m in msgs], ["a", "b"])
        self.assertEqual([m["seq"] for m in msgs], [1, 2])

    def test_auto_title_from_first_user_message(self):
        s = db.create_chat_session()
        db.append_chat_messages(s["session_id"], [{"role": "user", "content": "帮我训练一个 LoRA"}])
        self.assertEqual(db.get_chat_session(s["session_id"])["title"], "帮我训练一个 LoRA")

    def test_auto_title_truncates_long_text(self):
        s = db.create_chat_session()
        db.append_chat_messages(s["session_id"], [{"role": "user", "content": "一二三四五六七八九十" * 5}])
        title = db.get_chat_session(s["session_id"])["title"]
        self.assertTrue(title.endswith("…"))
        self.assertLessEqual(len(title), 25)

    def test_explicit_title_is_not_overwritten(self):
        s = db.create_chat_session(title="我的会话")
        db.append_chat_messages(s["session_id"], [{"role": "user", "content": "x"}])
        self.assertEqual(db.get_chat_session(s["session_id"])["title"], "我的会话")

    def test_tool_fields_json_roundtrip(self):
        s = db.create_chat_session(mode="agent")
        db.append_chat_messages(s["session_id"], [{
            "role": "tool", "kind": "tool", "tool_name": "start_training",
            "tool_status": "ok", "tool_args": {"lora_name": "x"},
            "tool_result": {"task_id": "abc", "nested": {"k": [1, 2]}},
        }])
        m = db.get_chat_session(s["session_id"])["messages"][0]
        self.assertEqual(m["kind"], "tool")
        self.assertEqual(m["tool_name"], "start_training")
        self.assertEqual(m["tool_args"], {"lora_name": "x"})
        self.assertEqual(m["tool_result"]["nested"]["k"], [1, 2])

    def test_append_to_missing_session_raises(self):
        with self.assertRaises(ValueError):
            db.append_chat_messages("nope", [{"role": "user", "content": "x"}])

    def test_rename_and_mode_update(self):
        s = db.create_chat_session()
        out = db.update_chat_session(s["session_id"], title="改名了", mode="agent")
        self.assertEqual(out["title"], "改名了")
        self.assertEqual(out["mode"], "agent")

    def test_update_without_fields_returns_session(self):
        s = db.create_chat_session()
        out = db.update_chat_session(s["session_id"])
        self.assertEqual(out["session_id"], s["session_id"])

    def test_list_orders_by_updated_at_desc(self):
        a = db.create_chat_session()
        time.sleep(0.01)
        db.create_chat_session()
        time.sleep(0.01)
        db.append_chat_messages(a["session_id"], [{"role": "user", "content": "later"}])
        self.assertEqual(db.list_chat_sessions()[0]["session_id"], a["session_id"])

    def test_list_filter_by_title(self):
        a = db.create_chat_session(title="论文方法抽取")
        db.create_chat_session(title="别的会话")
        hits = db.list_chat_sessions(q="论文")
        self.assertEqual([x["session_id"] for x in hits], [a["session_id"]])

    def test_delete_removes_messages_too(self):
        s = db.create_chat_session()
        db.append_chat_messages(s["session_id"], [{"role": "user", "content": "x"}])
        self.assertTrue(db.delete_chat_session(s["session_id"]))
        self.assertIsNone(db.get_chat_session(s["session_id"]))
        self.assertFalse(db.delete_chat_session(s["session_id"]))


if __name__ == "__main__":
    unittest.main()
