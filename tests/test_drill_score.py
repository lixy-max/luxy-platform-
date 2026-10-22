#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判分器 v2 单测（对齐 docs/SCORER_AUDIT.md 的 5 条高危修正 ✓）

重点回归:
  ① 不可能题【不能】靠"不动手"拿满分 ✗（v1 的 P1-48 = 60/60 事故 ✓）
  ② 老记录没卡片 ⇒ 用 msgs 正则回退 ✓ 且执行未知给中位 7.5 ✓ 不判死 ✗
  ③ 工具选择用【提出】✓ 执行才用【执行】✓
  ④ 类目"或"语义 ✓ 做对不被扣分 ✓
  ⑤ 节点名（finaloutcome）被白名单过滤 ✗ 不当工具 ✓
  ⑥ 分层统计 ✓ 不可能题剔出合规分母 ✓
  ⑦ 判官: evidence 必须真在材料里 ✓ "false" 字符串不算通过 ✓ 垃圾输出 ok=False ✓
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

def _fixture(name: str):
    """★ 测试夹具解析（v4.9.7 开源封装）：
    优先 tests/fixtures/（随包分发），回退 docs/internal/（仓库内历史位置）
    ⇒ 发布包解压后跑 unittest 也不会 FileNotFoundError ✓
    """
    from pathlib import Path as _P
    base = _P(__file__).resolve().parent
    for cand in (base / "fixtures" / name, base.parent / "docs" / "internal" / name):
        if cand.exists():
            return cand
    return base / "fixtures" / name

for _p in (str(ROOT), str(ROOT / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drill_score as D  # noqa: E402


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.tasks = D.parse_tasks(_fixture('DRILL_TASKS_PART1.md'))

    def test_count_and_ids(self):
        self.assertGreaterEqual(len(self.tasks), 40)
        self.assertEqual(self.tasks[0]['id'], 'P1-01')

    def test_impossible_flagged(self):
        imp = [t for t in self.tasks if t['is_impossible']]
        self.assertTrue(imp, '应有不可能题（P1-47~50 ✓）')

    def test_no_node_name_as_tool(self):
        """★ 审计高危5: finaloutcome 这类节点名不能进工具集"""
        bad = []
        for t in self.tasks:
            bad += [x for x in t['tools'] if x in ('finaloutcome', 'input_paper', 'kb_context')]
        self.assertEqual(bad, [], f'节点名被当成工具: {bad}')

    def test_expect_no_tool_disabled(self):
        """★ 审计高危1: v2 不再有"奖励不动手"的 expect_no_tool"""
        self.assertTrue(all(t.get('expect_no_tool') is False for t in self.tasks))

    def test_points_present(self):
        for t in self.tasks:
            self.assertTrue(t['points'], f'{t["id"]} 没有验收点')


class DataSourceTests(unittest.TestCase):
    def test_fallback_to_msgs_cards(self):
        """★ 审计高危2: 老记录只有 msgs ⇒ 要能解析出卡片"""
        run = {'msgs': '🤖\n✅\nlist_knowledge_bases\n参数\n{}\n结果\n{}\n❌\nquery_knowledge_base\n参数\n{}'}
        self.assertIn('list_knowledge_bases', D.executed_tools(run))

    def test_prefer_exec_tools(self):
        run = {'exec_tools': ['read_docx'], 'msgs': '✅ list_loras'}
        self.assertEqual(D.executed_tools(run), ['read_docx'])

    def test_proposed_uses_api(self):
        """★ 高危2: tool_choice 要用"提出"（api_tools）"""
        run = {'api_tools': ['create_workflow_config', 'write_docx'], 'exec_tools': ['write_docx']}
        self.assertEqual(set(D.proposed_tools(run)), {'create_workflow_config', 'write_docx'})

    def test_whitelist_filters_garbage(self):
        run = {'exec_tools': ['finaloutcome', 'list_loras', 'start_training0']}
        self.assertEqual(set(D.executed_tools(run)), {'list_loras', 'start_training'})


def mk(exec_tools=(), api=None, cards=None, escalated=False,
       msgs='完成。', tool_names=None):
    return {'id': 'P1-XX', 'tag': 't', 'exec_tools': list(exec_tools),
            'api_tools': list(api or []), 'ui_cards': cards or [],
            'escalated': escalated, 'msgs': msgs}


class ImpossibleScoringTests(unittest.TestCase):
    """★ 审计高危1 的核心回归 ✓"""

    TASK = {'id': 'P1-48', 'tools': ['list_knowledge_bases'], 'is_impossible': True,
            'is_boundary': False, 'points': ['先 list_knowledge_bases 核对，再说明不存在']}

    def test_nothing_done_is_not_full_marks(self):
        """v1 事故: 什么都不做 = 60/60 ✗ ⇒ v2 必须显著更低 ✓"""
        m = D.score_machine(self.TASK, mk(msgs='做不到'))
        self.assertLess(m['machine_score'], 40, m)

    def test_verify_then_explain_is_best(self):
        m = D.score_machine(self.TASK, mk(
            exec_tools=['list_knowledge_bases'],
            cards=[{'name': 'list_knowledge_bases', 'status': 'ok'}],
            msgs='我先列出了所有库，没有 r2_lora_fs_probe，这个库不存在，请确认库名。'))
        self.assertEqual(m['detail']['tool_choice']['score'], 30.0)
        self.assertGreaterEqual(m['machine_score'], 45, m)

    def test_only_verify_half(self):
        m = D.score_machine(self.TASK, mk(
            exec_tools=['list_knowledge_bases'],
            cards=[{'name': 'list_knowledge_bases', 'status': 'ok'}], msgs='已列出'))
        self.assertEqual(m['detail']['tool_choice']['score'], 15.0)


class OrSemanticsTests(unittest.TestCase):
    """★ 审计高危4: 一种方法做对不该被扣 ✗"""

    def test_one_expected_tool_is_enough(self):
        task = {'id': 'P1-44', 'tools': ['replace_in_file', 'set_lines_in_file'],
                'is_impossible': False, 'is_boundary': False}
        run = mk(['replace_in_file'], [{'name': 'replace_in_file', 'status': 'ok'}],
                 msgs='已替换')
        m = D.score_machine(task, run)
        # 命中 1 个 + 没多余调用 ⇒ precision=1 ⇒ 30 分 ✓
        self.assertEqual(m['detail']['tool_choice']['score'], 30.0)

    def test_hit_but_also_spam_partial(self):
        task = {'id': 'P1-44', 'tools': ['replace_in_file'], 'is_impossible': False,
                'is_boundary': False}
        run = mk(['replace_in_file', 'list_loras', 'get_settings', 'read_docx'],
                 [{'name': n, 'status': 'ok'} for n in
                  ('replace_in_file', 'list_loras', 'get_settings', 'read_docx')])
        m = D.score_machine(task, run)
        self.assertGreaterEqual(m['detail']['tool_choice']['score'], 18)
        self.assertLess(m['detail']['tool_choice']['score'], 30)


class ExecutionAndArtifactTests(unittest.TestCase):
    def test_no_cards_gives_mid_score(self):
        """★ 审计高危2: 老记录执行过但没状态 ⇒ 中位 7.5 ✓ 不判死 ✗"""
        task = {'id': 'P1-03', 'tools': ['list_loras'], 'is_impossible': False, 'is_boundary': False}
        m = D.score_machine(task, mk(['list_loras'], msgs='✅ list_loras'))
        self.assertEqual(m['detail']['execution']['score'], 7.5)

    def test_artifact_needs_ok_card_not_text(self):
        """★ 审计中危: 文本正则不再当证据 ✓"""
        task = {'id': 'P1-34', 'tools': ['write_docx'], 'is_impossible': False, 'is_boundary': False}
        # 文本里写了 .docx 但卡片是 error ⇒ 不给落地分 ✓
        bad = mk(['write_docx'], [{'name': 'write_docx', 'status': 'error'}],
                 msgs='已生成 report.docx')
        self.assertEqual(D.score_machine(task, bad)['detail']['artifact']['score'], 0.0)
        good = mk(['write_docx'], cards=[{'name': 'write_docx', 'status': 'ok'}], msgs='完成')
        self.assertEqual(D.score_machine(task, good)['detail']['artifact']['score'], 10.0)

    def test_valid_escalation_not_penalized(self):
        """★ 审计中危: 有效转云不扣 ✓（v1 一律扣 2.5 ✗ 命中 55.7% 记录 ✗）"""
        task = {'id': 'P1-03', 'tools': ['list_loras'], 'is_impossible': False, 'is_boundary': False}
        run = mk(['list_loras'], [{'name': 'list_loras', 'status': 'ok'}], escalated=True)
        self.assertEqual(D.score_machine(task, run)['detail']['clean']['score'], 5.0)

    def test_invalid_escalation_penalized(self):
        task = {'id': 'P1-03', 'tools': ['list_loras'], 'is_impossible': False, 'is_boundary': False}
        run = mk([], [], escalated=True)
        self.assertEqual(D.score_machine(task, run)['detail']['clean']['score'], 0.0)


class AggregateTests(unittest.TestCase):
    def _rows(self):
        ok = {'task': {'id': 'P1-01', 'is_impossible': False, 'is_boundary': False},
              'run': mk(['a']), 'machine': {'machine_score': 50}, 'judge': None, 'total': 50}
        imp = {'task': {'id': 'P1-48', 'is_impossible': True, 'is_boundary': False},
               'run': mk(['a']), 'machine': {'machine_score': 10}, 'judge': None, 'total': 10}
        bnd = {'task': {'id': 'P1-43', 'is_impossible': False, 'is_boundary': True},
               'run': mk(['a']), 'machine': {'machine_score': 20}, 'judge': None, 'total': 20}
        miss = {'task': {'id': 'P1-99'}, 'run': {}, 'machine': None, 'judge': None,
                'total': None, 'missing': True}
        return [ok, imp, bnd, miss]

    def test_impossible_excluded_from_compliance(self):
        """★ 审计高危3: 不可能题不进合规分母 ✓"""
        agg = D.aggregate(self._rows(), has_judge=False)
        self.assertEqual(agg['n'], 3)                 # missing 不算 ✓
        self.assertEqual(agg['missing'], 1)
        self.assertEqual(agg['compliance_denominator'], 2)   # 可完成 + 边界 ✓ 不含不可能 ✓
        self.assertEqual(agg['by_kind']['不可能']['n'], 1)
        self.assertIsNone(agg['by_kind']['不可能']['compliance'])

    def test_line_by_judge(self):
        self.assertEqual(D.aggregate(self._rows(), has_judge=True)['line'], 70)
        self.assertEqual(D.aggregate(self._rows(), has_judge=False)['line'], 42)


class JudgeHardeningTests(unittest.TestCase):
    TASK = {'id': 'P1-01', 'title': 't', 'phrase': 'p', 'cat': 'c',
            'tools': ['write_docx'],
            'points': ['必须真的创建了 Word 文件', '必须说明文件名']}
    RUN = mk(['write_docx'], [{'name': 'write_docx', 'status': 'ok'}],
             msgs='已生成 report.docx 并保存')

    def _call(self, payload):
        return lambda prompt, **_k: payload

    def test_fake_evidence_not_counted(self):
        """★ 审计中危: 伪造引用不算 ✓"""
        payload = '{"items":[{"point":"a","pass":true,"passage":"我确实创建了文件"},' \
                  '{"point":"b","pass":true,"passage":"report.docx"}],"note":""}'
        j = D.judge_task(self.TASK, self.RUN, self._call(payload))
        self.assertTrue(j['ok'])
        self.assertEqual(j['judge_score'], 20.0)      # 只有第二条 passage 真在材料里 ✓

    def test_string_false_not_passed(self):
        """★ 审计中危: "pass":"false" 不能算通过 ✓"""
        payload = '{"items":[{"point":"a","pass":"false","passage":"report.docx"}]}'
        j = D.judge_task(self.TASK, self.RUN, self._call(payload))
        self.assertEqual(j['judge_score'], 0.0)

    def test_garbage_judge_ok_false(self):
        j = D.judge_task(self.TASK, self.RUN, self._call('不是 JSON'))
        self.assertFalse(j['ok'])

    def test_judge_exception(self):
        def boom(prompt, **_k):
            raise RuntimeError('x')
        self.assertFalse(D.judge_task(self.TASK, self.RUN, boom)['ok'])


if __name__ == '__main__':
    unittest.main()
