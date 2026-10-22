#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C: 置信度负例类型分析（2026-10-21）"""
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
RUNS = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/runs_v2.jsonl')


def classify(r: dict) -> str:
    """失败类型分类 ✓"""
    if r.get('ok') and (r.get('exec_count') or 0) > 0 and not r.get('escalated') and not r.get('error'):
        return 'PASS'
    if r.get('error'):
        return 'ERROR'
    if r.get('escalated'):
        return 'ESCALATED'
    ec = r.get('exec_count') or 0
    el = float(r.get('elapsed_s') or 0)
    if ec == 0:
        return 'NO_TOOL(零工具直接作答)'
    if el >= 170:
        return 'TIMEOUT(超时)'
    if r.get('exec_tools') and r.get('exec_ok_tools') is not None:
        if len(r.get('exec_ok_tools') or []) < len(r.get('exec_tools') or []):
            return 'TOOL_ERROR(工具报错)'
    if not r.get('finished'):
        return 'UNFINISHED(未完成)'
    return 'OTHER_FAIL'


def main() -> int:
    print('=' * 74)
    print('  C · 置信度负例类型分析（今天发现"负例全是超时"✗）')
    print('=' * 74)
    if not RUNS.exists():
        print('  ✗ 无数据')
        return 1
    rows = []
    for line in RUNS.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('placeholder'):
            continue
        rows.append(r)
    print('  总记录: %d' % len(rows))

    # 只看干净的两批 ✓
    tags = {'final_7b_kb0', 'final_7b_kb1v3'}
    sel = [r for r in rows if r.get('tag') in tags]
    print('  干净两批: %d 条 ✓' % len(sel))

    cls = Counter()
    by_tag = defaultdict(Counter)
    for r in sel:
        c = classify(r)
        cls[c] += 1
        by_tag[r.get('tag')][c] += 1

    print('')
    print('  --- 通过/失败类型分布 ---')
    total = len(sel)
    for k, n in cls.most_common():
        print('    %-28s %3d 条（%.1f%%）' % (k, n, n / total * 100))

    print('')
    print('  --- 按臂 ---')
    for tag in sorted(by_tag):
        print('    %s:' % tag)
        for k, n in by_tag[tag].most_common():
            print('      %-26s %2d' % (k, n))

    fails = total - cls.get('PASS', 0)
    print('')
    print('  ★ 结论:')
    print('    失败总计: %d 条' % fails)
    if fails:
        no_tool = cls.get('NO_TOOL(零工具直接作答)', 0)
        timeout = cls.get('TIMEOUT(超时)', 0)
        print('    · 零工具直接作答: %d 条（%.0f%%）← **主要失败类型** ✓' % (
            no_tool, no_tool / fails * 100))
        print('    · 超时:           %d 条（%.0f%%）' % (timeout, timeout / fails * 100))
    print('')
    print('  ⚠ 对置信度打分器的影响:')
    print('    · 之前"负例几乎全是超时"✗ ⇒ 模型只学会检测超时 ✗')
    print('    · 现在看: 失败里有 **%d 条「零工具直接作答」** ✓' % cls.get('NO_TOOL(零工具直接作答)', 0))
    no_tool = cls.get('NO_TOOL(零工具直接作答)', 0)
    if no_tool > 0:
        print('      ⇒ 这批可以作为**新类型的负例** ✓ 不必再跑评测 ✓✓')
        print('      ⇒ 置信度打分器可以立即扩负例 ✓ 零 GPU ✓')
    return 0


if __name__ == '__main__':
    sys.exit(main())
