#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P-13 单测: reflect 超时/中断时【打捞答案】✓（不跑 DSH ✗ 纯事件流 ✓）"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import reflect_run as rr  # noqa: E402


def _steps_with_text(txt):
    return [
        {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
        {"type": "text", "text": "先看看现状……"},
        {"type": "tool_call", "callId": "c1", "tool": "list_loras", "input": {}},
        {"type": "tool_result", "callId": "c1", "status": "completed", "result": "ok"},
        {"type": "status", "phase": "step_end", "usage": {"inputTokens": 10, "outputTokens": 5}},
        {"type": "text", "text": txt},
    ]


class TestSalvage(unittest.TestCase):
    def test_salvages_when_final_missing(self):
        """★ 复现实测问题: 超时导致 final 事件缺失 ⇒ 必须从中间 text 事件捞回答案 ✓"""
        events = _steps_with_text("结论：当前底座是 Qwen2.5-7B-Instruct-AWQ，可以挂 7B 的 LoRA。")
        traj = rr.build_trajectory("测试任务", events, [], {"rc": -9, "ms": 1000, "provider": "x"})
        self.assertEqual(traj["final"], "结论：当前底座是 Qwen2.5-7B-Instruct-AWQ，可以挂 7B 的 LoRA。")
        self.assertTrue(traj["final_salvaged"])
        self.assertTrue(any("打捞" in d for d in traj["diagnostics"]))

    def test_normal_final_not_salvaged(self):
        events = _steps_with_text("中间文本") + [{"type": "final", "text": "这是正常结束的答案"}]
        traj = rr.build_trajectory("测试任务", events, [], {"rc": 0})
        self.assertEqual(traj["final"], "这是正常结束的答案")
        self.assertFalse(traj["final_salvaged"])

    def test_empty_events_no_crash(self):
        traj = rr.build_trajectory("测试任务", [], [], {"rc": -9})
        self.assertEqual(traj["final"], "")
        self.assertFalse(traj["final_salvaged"])

    def test_steps_text_fallback(self):
        """没有独立 text 事件时，从 step.text 里捞 ✓"""
        events = [{"type": "status", "phase": "step_start", "turn": 1, "step": 1},
                  {"type": "text", "text": "只有这一步的正文"}]
        traj = rr.build_trajectory("t", events, [], {"rc": -9})
        self.assertIn("正文", traj["final"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
