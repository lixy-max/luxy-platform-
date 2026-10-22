#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learn_sink 的守卫 + P-1/P-2/P-9 修复测试（不碰真实知识库 ✓）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import learn_sink as ls  # noqa: E402

EMPTY = '''# 从真实轨迹蒸馏出的经验（自动生成，勿手改）

> 由 `scripts/reflect_distill.py` 从 `data/trajectories/*.jsonl` 生成 ✓
> 来源: 0 条直接轨迹 + 0 条升级记录

## 一、可用的工具调用序列（成功轨迹蒸馏）

（还没有足够轨迹 ✓ 先跑 `scripts/reflect_run.py` 攒几条）
## 三、踩过的坑与正确做法（按错误类型归类）

（还没有报错记录 ✓）
'''

REAL = '''# 从真实轨迹蒸馏出的经验（自动生成，勿手改）

## 一、可用的工具调用序列（成功轨迹蒸馏）

| 序列 | 次数 | 成功率 |
|---|---|---|
| list_knowledge_bases → query_knowledge_base | 7 | 100% |
'''


class TestEmptyDistillGuard(unittest.TestCase):
    def test_detects_empty_template(self):
        self.assertTrue(ls.is_empty_distill(EMPTY))

    def test_accepts_real_content(self):
        self.assertFalse(ls.is_empty_distill(REAL))

    def test_handles_none_and_garbage(self):
        self.assertFalse(ls.is_empty_distill(None))
        self.assertFalse(ls.is_empty_distill('随便一句话'))


    def test_header_only_table_is_still_empty(self):
        """★ 踩过的坑: 空模板带一行表头 ⇒ 旧判据误判为"有数据" ✗"""
        tpl = EMPTY + '\n| 工具 | 成功 | 失败 |\n'
        self.assertTrue(ls.is_empty_distill(tpl))

    def test_table_with_separator_and_rows_is_real(self):
        tpl = EMPTY + '\n| 序列 | 次数 |\n|---|---|\n| a → b | 7 |\n'
        self.assertFalse(ls.is_empty_distill(tpl))

    def test_clean_one_still_works(self):
        traj = {'reason': 'completed', 'task': 'x' * 40, 'final': 'ok', 'steps': []}
        keep, cleaned, why = ls.clean_one(traj, {})
        self.assertTrue(keep)
        self.assertIsNone(why)


# ============ P-2: 判碎片按【内容】不按【长度】 ============
class TestShortTaskIntent(unittest.TestCase):
    def _keep(self, task, rules=None):
        traj = {'reason': 'completed', 'task': task, 'final': '结论正文', 'steps': []}
        return ls.clean_one(traj, rules or {})[0]

    def test_short_but_intent_kept(self):
        """实测证据里的短口气 ⇒ 必须保留 ✓（旧规则按 25 字全丢 ✗）"""
        for t in ('把结果导出来给我', '这个不对，重做', '刚才那个跑完没',
                  '再来100条', '用本地做', '已经起了', '秒没了，'):
            self.assertTrue(self._keep(t), f'应保留: {t}')

    def test_pure_symbols_dropped(self):
        for t in ('……', '?', '？？？', '！', '。，、', '   '):
            self.assertFalse(self._keep(t), f'应丢弃: {t!r}')

    def test_single_char_without_context_dropped(self):
        self.assertFalse(self._keep('a'))

    def test_threshold_8_is_the_fallback(self):
        self.assertFalse(self._keep('abcdefg', {'drop_short_task': 8}))    # 7 < 8 ⇒ 丢
        self.assertTrue(self._keep('abcdefgh', {'drop_short_task': 8}))    # 8 >= 8 ⇒ 留
        self.assertFalse(self._keep('abcdefgh', {'drop_short_task': 9}))   # 阈值读配置 ✓


class TestSeedsRetention(unittest.TestCase):
    """P-2 验收: 用真实语料 seeds.jsonl（23 条用户原话）⇒ 保留率 ≥ 90% ✓"""
    SEEDS = ROOT / 'data' / 'user_voice' / 'seeds.jsonl'

    def test_retention_at_least_90_percent(self):
        if not self.SEEDS.exists():
            self.skipTest('seeds.jsonl 不存在')
        rows = [json.loads(x) for x in self.SEEDS.read_text(encoding='utf-8').splitlines()
                if x.strip()]
        self.assertGreaterEqual(len(rows), 20)
        kept, dropped = 0, []
        for r in rows:
            traj = {'reason': 'completed', 'task': r.get('phrase'), 'final': '结论正文',
                    'steps': []}
            k, _c, why = ls.clean_one(traj, {'drop_short_task': 8})
            if k:
                kept += 1
            else:
                dropped.append((r.get('phrase'), why))
        rate = kept / len(rows)
        self.assertGreaterEqual(rate, 0.9, f'保留率 {rate:.1%} ({kept}/{len(rows)}) 丢弃={dropped}')


# ============ P-1: 自产条目（不再依赖 reflect_distill 的形状 ✓） ============
class TestSelfEntry(unittest.TestCase):
    TRAJ = {
        'task': '把结果导出来给我',
        'final': ('先确认上一轮工作流已经跑完。然后用 export_docx 导出 Word。'
                  '注意导出路径要放在项目目录下，不要写进 WSL 路径。'
                  '如果需要其它格式，再调用 export_excel。'),
        'planned_tools': [],
        'steps': [{'tool_calls': [{'tool': 'cloud-model', 'input': {},
                                   'status': 'completed', 'result': '正文'}]}],
        'tools_used': ['cloud-model'],
        'reason': 'completed',
    }

    def test_entry_shape(self):
        entry = ls.build_entry(self.TRAJ)
        self.assertTrue(entry.startswith('## 问：把结果导出来给我'))
        self.assertIn('答：', entry)
        self.assertIn('要点：', entry)
        self.assertIn('工具计划：无', entry)

    def test_entry_capped_at_400(self):
        self.assertLessEqual(len(ls.build_entry(self.TRAJ)), 400)
        long_traj = dict(self.TRAJ, task='这是一条很长的任务描述' * 10,
                         final='很长的结论句子。' * 200)
        entry = ls.build_entry(long_traj)
        self.assertTrue(entry)
        self.assertLessEqual(len(entry), 400)

    def test_no_body_means_empty_entry(self):
        self.assertEqual(ls.build_entry({'task': 'x', 'final': ''}), '')
        self.assertEqual(ls.build_entry({'task': 'x', 'final': '...'}), '')

    def test_append_keeps_last_n_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'learned_tmp.md'
            for i in range(35):
                ls.append_entry(p, f'## 问：t{i}\n答：a{i}\n工具计划：无')
            entries = ls.load_entries(p)
            self.assertEqual(len(entries), ls.ENTRY_KEEP)
            text = p.read_text(encoding='utf-8')
            self.assertIn('## 问：t34', text)
            self.assertNotIn('## 问：t4\n', text)

    def test_legacy_empty_template_is_not_kept(self):
        """★ 迁移坑: 旧文件里是 reflect_distill 的 641 字符空模板 ⇒ 不能原样带回去入库 ✗"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'learned_x.md'
            p.write_text(EMPTY, encoding='utf-8')
            n = ls.append_entry(p, '## 问：新条目\n答：正文一句话\n工具计划：无')
            self.assertEqual(n, 1)
            text = p.read_text(encoding='utf-8')
            self.assertNotIn('还没有足够轨迹', text)
            self.assertIn('## 问：新条目', text)

    def test_foreign_producer_blocks_are_preserved(self):
        """★ workflow_learn.py 也写 learned_<target>.md（同命名不同格式）⇒ 不能删它的内容 ✗"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'learned_wf.md'
            p.write_text('# 工作流学习：demo\n\n## 一、本次要点（可照做）\n\n- 本次运行无失败\n',
                         encoding='utf-8')
            n = ls.append_entry(p, '## 问：任务A\n答：正文一句话\n工具计划：无')
            self.assertEqual(n, 1)
            text = p.read_text(encoding='utf-8')
            self.assertIn('# 工作流学习：demo', text)
            self.assertIn('## 问：任务A', text)
            self.assertEqual(len(ls.load_entries(p)), 1)


# ============ P-9: 解析并保存 planned_tools ============
class TestPlannedTools(unittest.TestCase):
    def test_parses_and_strips_plan_line(self):
        traj = {'reason': 'completed', 'task': '挂载 extract_v2',
                'final': 'PLAN: ["list_loras","load_lora"]\n先列出 LoRA，再挂载。',
                'steps': []}
        keep, cleaned, why = ls.clean_one(traj, {})
        self.assertTrue(keep)
        self.assertIsNone(why)
        self.assertEqual(cleaned['planned_tools'], ['list_loras', 'load_lora'])
        self.assertNotIn('PLAN', cleaned['final'])
        self.assertIn('先列出 LoRA', cleaned['final'])
        self.assertIn('list_loras → load_lora', ls.build_entry(cleaned))

    def test_tolerant_forms(self):
        cases = [
            ("PLAN: ['a', 'b']\n正文", ['a', 'b']),
            ('PLAN：[ "x" , "y" ]\n正文', ['x', 'y']),
            ('PLAN: [a, b]\n正文', ['a', 'b']),
            ('\n\nPLAN: ["m"]\n正文', ['m']),
            ('plan: ["p","q"]\n正文', ['p', 'q']),
        ]
        for final, want in cases:
            tools, body = ls.parse_planned_tools(final)
            self.assertEqual(tools, want, final)
            self.assertNotIn('PLAN', body.upper())

    def test_plan_in_middle_of_body_is_not_stripped(self):
        tools, body = ls.parse_planned_tools('先说明。\nPLAN: ["a"]\n结论')
        self.assertEqual(tools, [])
        self.assertIn('PLAN', body)


# ============ P-1 端到端: 单步 handoff 轨迹 ⇒ 条目落盘 + 入库（临时目录 + 假 kb ✓） ============
class TestSinkSessionHandoff(unittest.TestCase):
    def _run(self, traj, kb_name='tmp_task14_selftest'):
        import kb as kbmod
        calls = {}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            old = (ls.LEARN_DIR, ls.TRAJ_DIR, ls.CLEAN_DIR)
            ls.LEARN_DIR, ls.TRAJ_DIR, ls.CLEAN_DIR = d, d / 'trajectories', d / 'cleaned'
            orig_add = getattr(kbmod, 'add_documents_to_kb', None)
            orig_new = getattr(kbmod, 'create_kb', None)
            kbmod.add_documents_to_kb = lambda name, paths, **kw: (
                calls.update(kb=name, paths=list(paths)) or {'blocks': 3})

            def _exists(*a, **k):
                raise ValueError('已存在')
            kbmod.create_kb = _exists
            try:
                stats = ls.sink_session(traj, {'kb_name': kb_name, 'kb_mode': 'new',
                                               'clean': {'drop_short_task': 8}})
                md = d / f'learned_{kb_name}.md'
                return stats, calls, md.read_text(encoding='utf-8') if md.exists() else None
            finally:
                ls.LEARN_DIR, ls.TRAJ_DIR, ls.CLEAN_DIR = old
                if orig_add is not None:
                    kbmod.add_documents_to_kb = orig_add
                if orig_new is not None:
                    kbmod.create_kb = orig_new

    HANDOFF = {
        'task': '把结果导出来给我',
        'session_id': 'chat-123',
        'reason': 'completed',
        'error': None,
        'final': ('先确认上一轮工作流结果已经生成。然后用 export_docx 导出 Word。'
                  '注意路径放在项目目录下。'),
        'steps': [{'turn': 1, 'step': 1, 'thinking': '', 'text': '',
                   'tool_calls': [{'callId': 'cloud', 'tool': 'cloud-model',
                                   'input': {}, 'status': 'completed', 'result': '正文'}]}],
        'tools_used': ['cloud-model'],
    }

    def test_single_step_handoff_now_ingests(self):
        """★ P-1 核心: 旧实现这条轨迹出 641 字符空模板 ⇒ 永不入库 ✗ 现在必须产条目 ✓"""
        stats, calls, md = self._run(self.HANDOFF)
        self.assertEqual(stats['kept'], 1)
        self.assertGreater(stats['entry_chars'], 0)
        self.assertLessEqual(stats['entry_chars'], 400)
        self.assertNotIn('skipped_ingest', stats)
        self.assertEqual(stats['entries_kept'], 1)
        self.assertIsNotNone(md)
        self.assertIn('## 问：把结果导出来给我', md)
        self.assertEqual(calls.get('kb'), 'tmp_task14_selftest')

    def test_empty_entry_is_not_ingested(self):
        traj = dict(self.HANDOFF, final='...')
        stats, calls, md = self._run(traj)
        self.assertTrue(stats.get('skipped_ingest'))
        self.assertNotIn('kb', calls)
        self.assertIsNone(md)


if __name__ == '__main__':
    unittest.main(verbosity=2)
