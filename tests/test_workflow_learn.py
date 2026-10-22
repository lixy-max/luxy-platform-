#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow_learn（G1）的单元测试 —— 只测纯逻辑，不碰真实知识库 ✓。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import workflow_learn as wl  # noqa: E402


class TestClean(unittest.TestCase):
    def test_strips_absolute_paths(self):
        t = wl._clean('读 /home/luxy/paper_lora_project/outputs/x.json 和 E:\\WSL\\a.bat 还有 /mnt/e/1.txt')
        self.assertNotIn('/home/luxy', t)
        self.assertNotIn('/mnt/e', t)
        self.assertIn('<path>', t)

    def test_collapses_blank_lines_and_truncates(self):
        t = wl._clean('a\n\n\n\nb' + 'x' * 100, limit=10)
        self.assertLess(len(t), 80)
        self.assertIn('截断', t)


class TestPlanTargets(unittest.TestCase):
    def test_default_is_off(self):
        cfg = {'nodes': [{'id': 'a'}, {'id': 'b', 'kb_write': {'mode': 'off'}}]}
        self.assertEqual(wl.plan_targets(cfg), {})

    def test_respects_append_and_target(self):
        cfg = {'nodes': [{'id': 'a', 'kb_write': {'mode': 'append', 'target': 'wf_demo'}},
                         {'id': 'b', 'kb_write': {'mode': 'replace'}}]}
        got = wl.plan_targets(cfg)
        self.assertEqual(got['a'], ('append', 'wf_demo'))
        self.assertEqual(got['b'], ('replace', wl.DEFAULT_TARGET))

    def test_guards_agent_use(self):
        cfg = {'nodes': [{'id': 'a', 'kb_write': {'mode': 'append', 'target': 'agent_use'}}]}
        with self.assertRaises(ValueError):
            wl.plan_targets(cfg)
        os.environ[wl.ALLOW_GUARDED_ENV] = '1'
        try:
            self.assertIn('a', wl.plan_targets(cfg))
        finally:
            os.environ.pop(wl.ALLOW_GUARDED_ENV, None)


class TestBuildDocs(unittest.TestCase):
    def setUp(self):
        self.cfg = {'workflow_name': 'demo',
                    'nodes': [{'id': 'n1', 'type': 'extract', 'lora': 'extract_v2',
                               'kb_write': {'mode': 'append', 'target': 'wf_demo'}},
                              {'id': 'n2', 'type': 'terminal'}]}
        self.res = {'workflow': 'demo', 'source_file': 'p.pdf', 'status': 'success',
                    'nodes': {'n1': 'OUT1'},
                    '_meta': {'traces': {'n1': {'input': 'IN1', 'output': 'OUT1'}},
                              'failures': [{'node': 'n2', 'stage': 'llm', 'error': 'timeout'}]}}

    def test_only_enabled_nodes_and_grouping(self):
        docs = wl.build_docs(self.res, self.cfg, 'result_x.json')
        self.assertEqual(list(docs), ['wf_demo'])
        md = docs['wf_demo']
        self.assertIn('n1', md)
        self.assertNotIn('### 节点 `n2`', md)      # 未开启 ⇒ 不进轨迹 ✓
        self.assertIn('IN1', md)
        self.assertIn('本次要点', md)
        self.assertIn('n2', md)                    # 但失败要点里要提到 ✓

    def test_noop_when_all_off(self):
        cfg = {'nodes': [{'id': 'n1', 'type': 'extract'}]}
        self.assertEqual(wl.build_docs(self.res, cfg, 'r.json'), {})


class TestIngestDryRun(unittest.TestCase):
    def test_dry_run_does_not_touch_kb(self):
        with tempfile.TemporaryDirectory() as d:
            rp = Path(d) / 'result_t.json'
            cp = Path(d) / 'cfg.json'
            rp.write_text(json.dumps({'workflow': 'x', 'status': 'success', 'nodes': {},
                                      '_meta': {'traces': {}}}), encoding='utf-8')
            cp.write_text(json.dumps({'nodes': [{'id': 'n1', 'kb_write': {'mode': 'append',
                                                                        'target': 'wf_demo'}}]}),
                          encoding='utf-8')
            s = wl.ingest(rp, cp, dry_run=True)
            self.assertNotIn('error', s)
            self.assertIn('wf_demo', s.get('preview', {}))
            self.assertEqual(s.get('ingested'), {})

    def test_skips_when_nothing_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            rp = Path(d) / 'r.json'
            rp.write_text(json.dumps({'workflow': 'x', '_meta': {}}), encoding='utf-8')
            s = wl.ingest(rp, None, dry_run=True)
            self.assertIn('skipped', s)


if __name__ == '__main__':
    unittest.main(verbosity=2)
