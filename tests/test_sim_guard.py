#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""递归计算守卫（sim_guard）单元测试

覆盖:
  ① 检测: 算式 / 计算意图 / 结果数 / 不该触发的文本 ✓
  ② 修复: 把心算数字替换成真算值 ✓ 多算式 ✓ 不误改无算式处 ✓
  ③ 递归作用域: 子代理算过 ⇒ 父层满足 ✓ 都没算 ⇒ 违规 ✓ 多层 ✓
  ④ 动态加载: 修复用的节点从注册表现取 ✓（不写死 ✓）
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sim_guard  # noqa: E402


class DetectTests(unittest.TestCase):
    def test_expression_detected(self):
        d = sim_guard.detect_compute('平均是 (12+18+25+31+44+9+17)/7 = 21.14 秒')
        self.assertTrue(d['needed'], d)
        self.assertIn('(12+18+25+31+44+9+17)/7', d['expressions'])

    def test_intent_word_detected(self):
        d = sim_guard.detect_compute('我算了一下，通过率大约是 62%')
        self.assertTrue(d['needed'], d)

    def test_plain_text_not_detected(self):
        d = sim_guard.detect_compute('这个工作流包含 3 个节点，名字叫 input_paper')
        self.assertFalse(d['needed'], d)

    def test_ratio_expression(self):
        d = sim_guard.detect_compute('通过率 23/37*100 ≈ 62.16%')
        self.assertTrue(d['needed'], d)
        self.assertIn('23/37*100', d['expressions'])

    def test_fullwidth_normalized(self):
        exprs = sim_guard.extract_expressions('也就是（12＋18）÷3')
        self.assertTrue(exprs)
        self.assertIn('/', exprs[0])

    def test_extract_multiple(self):
        exprs = sim_guard.extract_expressions('a=1+2 b=3*4 c=(5-1)/2')
        self.assertGreaterEqual(len(exprs), 2)


class RepairTests(unittest.TestCase):
    def setUp(self):
        sim_guard.reset()

    def test_repair_replaces_wrong_number(self):
        r = sim_guard.repair('平均耗时 (12+18+25+31+44+9+17)/7 = 21.14 秒')
        self.assertTrue(r['ok'], r)
        self.assertIn('22.28571429', r['repaired'])
        self.assertNotIn('21.14', r['repaired'])

    def test_repair_ratio(self):
        r = sim_guard.repair('通过率 (23/37)*100 ≈ 62.16%')
        self.assertTrue(r['ok'], r)
        self.assertIn('62.16216216', r['repaired'])

    def test_repair_no_expression_untouched(self):
        r = sim_guard.repair('这段没有算式，只有数字 123')
        self.assertEqual(r['repaired'], '这段没有算式，只有数字 123')

    def test_repair_records_sim_use(self):
        sid = sim_guard.open_scope(None, kind='turn')
        sim_guard.repair('(1+2)*3 = 99', scope_id=sid)
        chk = sim_guard.check_scope(sid, text='(1+2)*3')
        self.assertTrue(chk['sim_ok'], chk)

    def test_guard_text_skips_when_already_used(self):
        sid = sim_guard.open_scope(None, kind='turn')
        sim_guard.record_sim_use(sid, 'math_eval', True)
        out = sim_guard.guard_text('平均 (1+2+3)/3 = 2', scope_id=sid)
        self.assertEqual(out['repaired'], '平均 (1+2+3)/3 = 2')   # 已算过 ⇒ 不改 ✓


class RecursiveScopeTests(unittest.TestCase):
    def setUp(self):
        sim_guard.reset()

    def test_child_sim_use_satisfies_parent(self):
        p = sim_guard.open_scope(None, kind='turn', label='父')
        c = sim_guard.open_scope(p, kind='subagent', label='子')
        sim_guard.record_sim_use(c, 'math_eval', True)
        sim_guard.record_llm_output(p, '平均 (1+2+3)/3 = 2')
        chk = sim_guard.check_scope(p, recursive=True)
        self.assertTrue(chk['ok'], chk)
        self.assertEqual(chk['violations'], [])

    def test_no_one_used_sim_violates(self):
        p = sim_guard.open_scope(None, kind='turn')
        c = sim_guard.open_scope(p, kind='subagent')
        sim_guard.record_llm_output(c, '通过率 23/37*100 ≈ 62%')
        chk = sim_guard.check_scope(p, recursive=True)
        self.assertFalse(chk['ok'])
        self.assertTrue(chk['violations'])

    def test_grandchild_satisfies_root(self):
        p = sim_guard.open_scope(None, kind='turn')
        c = sim_guard.open_scope(p, kind='subagent')
        g = sim_guard.open_scope(c, kind='subagent')
        sim_guard.record_sim_use(g, 'stats', True)
        sim_guard.record_llm_output(p, '均值 (1+2)/2 = 1.5')
        self.assertTrue(sim_guard.check_scope(p)['ok'])

    def test_non_recursive_only_self(self):
        p = sim_guard.open_scope(None, kind='turn')
        c = sim_guard.open_scope(p, kind='subagent')
        sim_guard.record_llm_output(c, '23/37*100 ≈ 62%')
        chk = sim_guard.check_scope(p, recursive=False)
        self.assertTrue(chk['ok'])          # 只看自己 ⇒ 自己没输出 ⇒ 不违规 ✓

    def test_failed_sim_use_does_not_count(self):
        sid = sim_guard.open_scope(None, kind='turn')
        sim_guard.record_sim_use(sid, 'math_eval', False)     # 失败 ✗ 不算 ✓
        sim_guard.record_llm_output(sid, '平均 (1+2)/2 = 1.5')
        self.assertFalse(sim_guard.check_scope(sid)['ok'])

    def test_close_scope_reports(self):
        sid = sim_guard.open_scope(None, kind='turn')
        sim_guard.record_llm_output(sid, '算一下 1+2+3')
        res = sim_guard.close_scope(sid)
        self.assertFalse(res['ok'])


class DynamicNodeTests(unittest.TestCase):
    def test_repair_uses_registered_node(self):
        """修复用的节点必须是注册表里真实存在的 ✓（动态 ✓ 不写死 ✓）"""
        import sim_nodes
        r = sim_guard.repair('(1+1)*2 = 99')
        self.assertIn(r['sim_node'], [x['name'] for x in sim_nodes.list_sim_nodes()])

    def test_repair_returns_ok_false_without_eval_node(self):
        """把能算表达式的节点临时移走 ⇒ 应如实返回 ok=False ✗ 不崩 ✓"""
        import sim_nodes
        backup = dict(sim_nodes.SIM_NODES)
        try:
            sim_nodes.SIM_NODES.clear()
            r = sim_guard.repair('(1+1)*2 = 99')
            self.assertFalse(r['ok'])
            self.assertFalse(r['fixes'])
        finally:
            sim_nodes.SIM_NODES.update(backup)


if __name__ == '__main__':
    unittest.main()
