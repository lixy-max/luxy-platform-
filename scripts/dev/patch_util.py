#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_util.py — 安全补丁工具（修 P-89 · 2026-10-21）

★ P-89 问题: 一次性补丁脚本里 `sys.exit(1)` 若在 `write_text()` 之前执行
   ⇒ 前面所有改动静默丢失 ✗（看不到报错 ✗ 白改 ✗）

★ 本工具的三条保证:
   ① **立即写盘** —— 每处 edit 立刻落盘 ✓ 不批到末尾 ✗
   ② **自动备份** —— 改动前 .bak_<ts> ✓ 可随时恢复 ✓
   ③ **验证+回滚** —— 语法检查失败 ⇒ 自动回滚 ✓ 不留半成品 ✗

用法:
    from patch_util import Patcher
    p = Patcher('/home/luxy/paper_lora_project/lora_manager.py')
    p.replace('old text', 'new text')          # 立即写盘 ✓
    p.replace_after('anchor', 'insert text')   # 锚点后插入 ✓
    p.verify()                                 # 语法检查（失败自动回滚 ✓）
    print(p.summary())

    # 上下文管理器（异常时自动回滚 ✓）
    with Patcher(path) as p:
        p.replace('a', 'b')
        # 抛异常 ⇒ 自动回滚 ✓
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


class PatchError(RuntimeError):
    """补丁失败 ✓"""


class Patcher:
    """安全补丁工具 ✓（立即写盘 + 备份 + 验证 + 回滚 ✓）"""

    def __init__(self, path: str | pathlib.Path, backup: bool = True):
        self.path = pathlib.Path(path)
        if not self.path.exists():
            raise PatchError('文件不存在: %s' % self.path)
        self.orig = self.path.read_text(encoding='utf-8', errors='replace')
        self.text = self.orig
        self.backup_path = None
        self.edits: list[dict] = []
        self.rolled_back = False
        self._bak_name = '%s.bak_%s' % (self.path.name, time.strftime('%Y%m%d_%H%M%S'))
        if backup:
            self.backup_path = self.path.parent / self._bak_name
            try:
                shutil.copy2(self.path, self.backup_path)
            except Exception as e:
                raise PatchError('备份失败: %s' % e)

    # ── 核心：改一处立刻写盘 ✓ ────────────────────────────────
    def _apply(self, desc: str, new_text: str) -> None:
        if new_text == self.text:
            self.edits.append({'desc': desc, 'changed': False})
            return
        self.text = new_text
        # ★ 立即写盘 ✓ 不批到末尾 ✗（P-89 核心修复 ✓）
        self.path.write_text(self.text, encoding='utf-8')
        self.edits.append({'desc': desc, 'changed': True})

    # ── 替换（唯一匹配 ✓ 不唯一就报错 ✓）────────────────────
    def replace(self, old: str, new: str, count: int = 1) -> 'Patcher':
        n = self.text.count(old)
        if n == 0:
            raise PatchError('找不到目标文本（前 80 字）: %r' % old[:80])
        if count == 1 and n > 1:
            raise PatchError('目标文本出现 %d 次（要求唯一 ✗）: %r' % (n, old[:60]))
        self._apply('replace: %r → %r' % (old[:40], new[:40]), self.text.replace(old, new, count))
        return self

    def replace_multi(self, old: str, new: str) -> 'Patcher':
        n = self.text.count(old)
        if n == 0:
            raise PatchError('找不到: %r' % old[:80])
        self._apply('replace_multi ×%d: %r' % (n, old[:40]), self.text.replace(old, new))
        return self

    def replace_regex(self, pattern: str, repl: str, flags: int = 0) -> 'Patcher':
        rx = re.compile(pattern, flags)
        if not rx.search(self.text):
            raise PatchError('正则无匹配: %r' % pattern[:80])
        self._apply('regex: %r' % pattern[:40], rx.sub(repl, self.text))
        return self

    def insert_after(self, anchor: str, text: str) -> 'Patcher':
        i = self.text.find(anchor)
        if i < 0:
            raise PatchError('锚点不存在: %r' % anchor[:80])
        pos = i + len(anchor)
        self._apply('insert_after: %r' % anchor[:40], self.text[:pos] + text + self.text[pos:])
        return self

    def insert_before(self, anchor: str, text: str) -> 'Patcher':
        i = self.text.find(anchor)
        if i < 0:
            raise PatchError('锚点不存在: %r' % anchor[:80])
        self._apply('insert_before: %r' % anchor[:40], self.text[:i] + text + self.text[i:])
        return self

    # ── 验证 ✓（失败 ⇒ 回滚 ✓）──────────────────────────────
    def verify(self, kind: str | None = None) -> bool:
        k = kind or self.path.suffix.lstrip('.').lower()
        try:
            if k in ('py',):
                ast.parse(self.text)
            elif k in ('json',):
                json.loads(self.text)
            elif k in ('js', 'mjs', 'ts'):
                # JS 语法要外部 node --check ✓（此处只做括号平衡的粗检 ✓）
                for ch_open, ch_close in (('{', '}'), ('[', ']'), ('(', ')')):
                    if self.text.count(ch_open) != self.text.count(ch_close):
                        raise PatchError('括号不平衡: %s%s (%d vs %d)' % (
                            ch_open, ch_close, self.text.count(ch_open), self.text.count(ch_close)))
            return True
        except Exception as e:
            self.rollback('验证失败: %s' % e)
            raise PatchError('验证失败已回滚 ✗: %s' % e)

    def rollback(self, reason: str = '') -> None:
        if self.rolled_back:
            return
        self.path.write_text(self.orig, encoding='utf-8')
        self.rolled_back = True
        print('  ↩ 已回滚: %s（%s）' % (self.path.name, reason or '手动'))

    def summary(self) -> str:
        changed = [e for e in self.edits if e.get('changed')]
        parts = ['%s: %d 处改动' % (self.path.name, len(changed))]
        if self.backup_path and self.backup_path.exists():
            parts.append('备份 %s' % self.backup_path.name)
        if self.rolled_back:
            parts.append('⚠ 已回滚')
        return ' · '.join(parts)

    # ── 上下文管理器 ✓（异常自动回滚 ✓）───────────────────
    def __enter__(self) -> 'Patcher':
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.rollback('异常: %s' % exc)
            return False      # 不吞异常 ✓
        return False


# ══════════════════════════════════════════════════════════
# 自测 ✓
# ══════════════════════════════════════════════════════════
def _selftest() -> int:
    import tempfile
    print('=' * 66)
    print('  patch_util 自测')
    print('=' * 66)
    ok = 0
    total = 0
    d = pathlib.Path(tempfile.mkdtemp())

    def case(name, fn):
        nonlocal ok, total
        total += 1
        try:
            fn()
            ok += 1
            print('  ✓ [%d] %s' % (total, name))
        except Exception as e:
            print('  ✗ [%d] %s → %s' % (total, name, e))

    def t1():
        f = d / 'a.py'
        f.write_text('x = 1\ny = 2\n', encoding='utf-8')
        with Patcher(f) as p:
            p.replace('x = 1', 'x = 99')
            p.verify()
        assert 'x = 99' in f.read_text(encoding='utf-8'), '未生效 ✗'
    case('基本替换 + 立即写盘', t1)

    def t2():
        f = d / 'b.py'
        f.write_text('a = 1\n', encoding='utf-8')
        before = f.read_text(encoding='utf-8')
        try:
            p = Patcher(f)
            p.replace('a = 1', 'a = 2')
            p.replace('不存在的内容', 'x')      # 会失败 ✓
        except PatchError:
            pass
        # ★ 关键: 第一处改动已落盘 ✓（不是静默丢失 ✓）
        assert 'a = 2' in f.read_text(encoding='utf-8'), '第一处改动丢失 ✗（P-89 未修 ✗）'
    case('★ 中途失败 ⇒ 前面改动不丢（P-89 核心 ✓）', t2)

    def t3():
        f = d / 'c.py'
        f.write_text('z = 1\n', encoding='utf-8')
        before = f.read_text(encoding='utf-8')
        try:
            with Patcher(f) as p:
                p.replace('z = 1', 'z = 999')
                raise RuntimeError('模拟异常')
        except RuntimeError:
            pass
        assert f.read_text(encoding='utf-8') == before, '异常未回滚 ✗'
    case('异常 ⇒ 自动回滚', t3)

    def t4():
        f = d / 'd.py'
        f.write_text('q = (1\n', encoding='utf-8')   # 故意语法错
        before = f.read_text(encoding='utf-8')
        try:
            with Patcher(f) as p:
                p.replace('q = (1', 'q = (2')
                p.verify()
        except PatchError:
            pass
        assert f.read_text(encoding='utf-8') == before, '验证失败未回滚 ✗'
    case('验证失败 ⇒ 回滚', t4)

    def t5():
        f = d / 'e.py'
        f.write_text('k = 1\nm = 2\n', encoding='utf-8')
        with Patcher(f) as p:
            p.insert_after('k = 1\n', 'k2 = 10\n')
            p.verify()
        t = f.read_text(encoding='utf-8')
        assert 'k2 = 10' in t and t.index('k2') < t.index('m'), '插入位置错 ✗'
    case('锚点后插入', t5)

    def t6():
        f = d / 'f.py'
        f.write_text('dup = 1\ndup = 1\n', encoding='utf-8')
        try:
            p = Patcher(f)
            p.replace('dup = 1', 'dup = 2')     # 出现 2 次 ✗ 应报错 ✓
            raise AssertionError('应报错但没报 ✗')
        except PatchError:
            pass
    case('唯一性检查（多次出现报错 ✓）', t6)

    def t7():
        f = d / 'g.json'
        f.write_text('{"a": 1}', encoding='utf-8')
        with Patcher(f) as p:
            p.replace('{"a": 1}', '{"a": 2}')
            p.verify()
        assert '"a": 2' in f.read_text(encoding='utf-8')
    case('JSON 验证', t7)

    def t8():
        f = d / 'h.py'
        f.write_text('v = 1\n', encoding='utf-8')
        p = Patcher(f)
        p.replace('v = 1', 'v = 2')
        assert p.backup_path and p.backup_path.exists(), '没备份 ✗'
        assert 'v = 1' in p.backup_path.read_text(encoding='utf-8'), '备份内容不对 ✗'
    case('自动备份', t8)

    print('')
    print('  ⇒ %d / %d 通过%s' % (ok, total, ' ✓' if ok == total else ' ✗'))
    return 0 if ok == total else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    print('用法: python scripts/patch_util.py --selftest')
    print('或在代码里: from patch_util import Patcher')
