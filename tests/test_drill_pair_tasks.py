"""part2 缺类目 ⇒ 从同号 part1 继承期望工具（2026-10-19 实测缺陷 ✓）"""
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


class PairInheritTests(unittest.TestCase):
    def test_p1_has_categories(self):
        t = D.parse_tasks(_fixture('DRILL_TASKS_PART1.md'))
        self.assertGreaterEqual(len([x for x in t if x['tools']]), 40)

    def test_p2_missing_categories(self):
        """记录缺陷事实: part2 文档缺类目行 ✓（若以后补了该条会失败 ⇒ 提醒更新 ✓）"""
        t = D.parse_tasks(_fixture('DRILL_TASKS_PART2.md'))
        self.assertEqual(len([x for x in t if x['tools']]), 0,
                         'part2 已补类目行？⇒ 可移除 inherit_pairs 兼容 ✓ 但请先确认 ✓')

    def test_inherit_fills_tools(self):
        t = D.parse_tasks(_fixture('DRILL_TASKS_PART2.md'))
        n = D.inherit_pairs(t, _fixture('DRILL_TASKS_PART1.md'))
        self.assertGreaterEqual(n, 40)
        self.assertGreaterEqual(len([x for x in t if x['tools']]), 40)
        self.assertTrue(any(x.get('inherited_from') for x in t))


if __name__ == '__main__':
    unittest.main()
