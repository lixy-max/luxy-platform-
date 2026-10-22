#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局仿真节点（sim_nodes）单元测试

覆盖四类:
  ① 安全性: 绝不执行任意代码（AST 白名单 ✓ 拒绝 import/属性/下标/lambda ✓）
  ② 正确性: math_eval / stats / ratio 的数值必须可复算 ✓
  ③ 注册表: 注册/列出/覆盖/未知节点 ✓ 插件目录加载 ✓
  ④ 接入: /sim/nodes · /sim/run · /docs/tool/list 里必须有仿真工具 ✓
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sim_nodes  # noqa: E402


class SimNodeSafetyTests(unittest.TestCase):
    """安全性：模型/用户给什么都不能变成代码执行 ✓"""

    def _run(self, expr):
        return sim_nodes.run_sim_node('math_eval', {'expr': expr})

    def test_basic_arithmetic(self):
        r = self._run('(1 + 2) * 3')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['result']['value'], 9.0)

    def test_sqrt_and_pi(self):
        r = self._run('sqrt(16) + pi - pi')
        self.assertTrue(r['ok'], r)
        self.assertAlmostEqual(r['result']['value'], 4.0, places=6)

    def test_power(self):
        r = self._run('2 ** 10')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['result']['value'], 1024.0)

    def test_rejects_import(self):
        r = self._run('__import__("os").system("id")')
        self.assertFalse(r['ok'])
        # 实现用"只允许调用 abs/..."表述 ⇒ 断言宽松些 ✓（关键是"被拒绝"✓ 不是措辞 ✗）
        self.assertTrue(('不允许' in r['error']) or ('只允许' in r['error']), r['error'])

    def test_rejects_attribute(self):
        r = self._run('(1).__class__')
        self.assertFalse(r['ok'])

    def test_rejects_subscript_and_lambda(self):
        for expr in ('[1,2][0]', '(lambda x: x+1)(2)', '1 if 1 else 2'):
            r = self._run(expr)
            self.assertFalse(r['ok'], f'{expr} 应被拒绝')

    def test_rejects_division_by_zero(self):
        r = self._run('1 / 0')
        self.assertFalse(r['ok'])
        self.assertIn('除数', r['error'])

    def test_rejects_too_long(self):
        r = self._run('1+' * 200 + '1')
        self.assertFalse(r['ok'])

    def test_rejects_huge_power(self):
        r = self._run('2 ** 999')
        self.assertFalse(r['ok'])


class SimNodeMathTests(unittest.TestCase):
    """正确性：数值必须可复算 ✓"""

    def test_stats_basic(self):
        r = sim_nodes.run_sim_node('stats', {'values': [1, 2, 3, 4]})
        self.assertTrue(r['ok'], r)
        s = r['result']
        self.assertEqual(s['count'], 4)
        self.assertAlmostEqual(s['sum'], 10.0)
        self.assertAlmostEqual(s['mean'], 2.5)
        self.assertAlmostEqual(s['median'], 2.5)
        self.assertAlmostEqual(s['stdev'], 1.2909944487358056, places=9)
        self.assertAlmostEqual(s['p25'], 1.75)
        self.assertAlmostEqual(s['p75'], 3.25)

    def test_stats_accepts_csv_string(self):
        r = sim_nodes.run_sim_node('stats', {'values': '1,2,3,4'})
        self.assertTrue(r['ok'], r)
        self.assertAlmostEqual(r['result']['mean'], 2.5)

    def test_stats_rejects_empty_and_bool(self):
        self.assertFalse(sim_nodes.run_sim_node('stats', {'values': []})['ok'])
        self.assertFalse(sim_nodes.run_sim_node('stats', {'values': [True]})['ok'])

    def test_ratio_percent(self):
        r = sim_nodes.run_sim_node('ratio', {'a': 30, 'b': 200})
        self.assertTrue(r['ok'], r)
        self.assertAlmostEqual(r['result']['percent'], 15.0)

    def test_ratio_accepts_expr(self):
        r = sim_nodes.run_sim_node('ratio', {'expr': '(23/37)*100'})
        self.assertTrue(r['ok'], r)
        self.assertAlmostEqual(r['result']['percent'], 23 / 37 * 100, places=6)

    def test_ratio_zero_denominator(self):
        r = sim_nodes.run_sim_node('ratio', {'a': 1, 'b': 0})
        self.assertFalse(r['ok'])

    def test_unknown_node_error_has_hint(self):
        r = sim_nodes.run_sim_node('nginx', {})
        self.assertFalse(r['ok'])
        self.assertIn('未知仿真节点', r['error'])

    def test_ratio_missing_params_returns_hint(self):
        r = sim_nodes.run_sim_node('ratio', {})
        self.assertFalse(r['ok'])
        self.assertIn('hint_params', r)
        self.assertIn('a', r['hint_params'])


class SimNodeRegistryTests(unittest.TestCase):
    """注册表：扩展接口必须好使 ✓"""

    def test_list_contains_builtins(self):
        names = {x['name'] for x in sim_nodes.list_sim_nodes()}
        self.assertTrue({'math_eval', 'stats', 'ratio'} <= names)

    def test_register_and_run_custom(self):
        sim_nodes.register_sim_node(
            'test_double', label='测试双倍', description='c = a*2',
            params={'a': 'number'},
            handler=lambda args: {'c': float(args.get('a') or 0) * 2},
            overwrite=True)
        r = sim_nodes.run_sim_node('test_double', {'a': 21})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['result']['c'], 42.0)

    def test_register_rejects_bad_name(self):
        with self.assertRaises(ValueError):
            sim_nodes.register_sim_node('bad name!', handler=lambda a: {})

    def test_register_requires_handler(self):
        with self.assertRaises(ValueError):
            sim_nodes.register_sim_node('test_nohandler')

    def test_duplicate_register_raises(self):
        with self.assertRaises(ValueError):
            sim_nodes.register_sim_node('math_eval', handler=lambda a: {})

    def test_public_meta_hides_handler(self):
        meta = sim_nodes.list_sim_nodes()[0]
        self.assertNotIn('handler', meta)
        self.assertIn('name', meta)

    def test_plugin_dir_loads(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, 'my_plugin.py').write_text(
                "def register(reg):\n"
                "    reg('plugin_triple', label='插件三倍', description='x*3',\n"
                "        params={'x': 'number'},\n"
                "        handler=lambda a: {'v': float(a.get('x') or 0) * 3},\n"
                "        overwrite=True)\n", encoding='utf-8')
            loaded = sim_nodes.load_plugin_dir(d)
            self.assertTrue(loaded, loaded)
            self.assertTrue(any(x.get('file') == 'my_plugin.py' for x in loaded))
            r = sim_nodes.run_sim_node('plugin_triple', {'x': 3})
            self.assertTrue(r['ok'], r)
            self.assertEqual(r['result']['v'], 9.0)

    def test_bad_plugin_reported_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, 'broken.py').write_text("raise RuntimeError('boom')\n", encoding='utf-8')
            loaded = sim_nodes.load_plugin_dir(d)
            self.assertTrue(loaded)
            self.assertTrue(any(isinstance(x, dict) and x.get('error') for x in loaded))


class SimNodeEndpointTests(unittest.TestCase):
    """接入：REST 端点与"唯一真源"工具清单 ✓"""

    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            import lora_manager
            cls.client = TestClient(lora_manager.app)
        except Exception as e:              # 环境不全都算跳过 ✓ 不制造假失败 ✗
            cls.client = None
            cls.reason = f'{type(e).__name__}: {e}'

    def test_sim_nodes_endpoint(self):
        if self.client is None:
            self.skipTest(getattr(self, 'reason', 'no client'))
        r = self.client.get('/sim/nodes')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreaterEqual(data['count'], 3)
        names = {x['name'] for x in data['nodes']}
        self.assertIn('math_eval', names)

    def test_sim_run_endpoint(self):
        if self.client is None:
            self.skipTest(getattr(self, 'reason', 'no client'))
        r = self.client.post('/sim/run', json={'node': 'math_eval', 'args': {'expr': '6*7'}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        self.assertEqual(r.json()['result']['value'], 42.0)

    def test_sim_run_failure_is_200_with_error(self):
        if self.client is None:
            self.skipTest(getattr(self, 'reason', 'no client'))
        r = self.client.post('/sim/run', json={'node': 'nope', 'args': {}})
        self.assertEqual(r.status_code, 200)          # 让模型能看到原因 ✓ 不抛 500 ✓
        self.assertFalse(r.json()['ok'])

    def test_tool_list_contains_sim_tools(self):
        if self.client is None:
            self.skipTest(getattr(self, 'reason', 'no client'))
        data = self.client.get('/docs/tool/list').json()
        names = {x['name'] for x in data['tools']}
        self.assertIn('list_sim_nodes', names)
        self.assertIn('run_simulation', names)


if __name__ == '__main__':
    unittest.main()
