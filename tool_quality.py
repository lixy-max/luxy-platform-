#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tool_quality.py — 工具质量打分（P2-4 · 2026-10-21）

## 为什么
  92 个工具，**不知道哪些好用、哪些常失败** ✗
  ⇒ 训练数据/工具检索/提示词都缺"质量"这个维度 ✗
  ⇒ 本模块从**真实数据**算工具质量 ✓ 不推测 ✗

## 数据源（零 GPU ✓ 全部离线 ✓）
  ① `_check/eval_v2/out/runs_v2.jsonl`
     · exec_tools（调用过的工具 ✓）
     · exec_ok_tools（成功的工具 ✓）
  ② `data/trajectories/*.jsonl`
     · steps[].tool_calls[].tool / status ✓

## 指标（每个工具 ✓）
  · uses        被调用次数 ✓
  · ok_rate     成功率 ✓（有结果的 / 总调用 ✓）
  · err_rate    出错率 ✓
  · avg_elapsed 平均耗时（eval 源有 ✓）
  · sources     数据来源标记 ✓

## 评级
  A: uses>=20 且 ok_rate>=0.95 ✓（可信 ✓）
  B: uses>=5 且 ok_rate>=0.80 ✓
  C: ok_rate>=0.60 或有数据但少 ✓
  D: ok_rate<0.60 ✗（有问题 ⚠）
  ?: 无数据（未测试 ⚠）

## ⚠ 诚实边界
  · 这是**观测统计** ✗ 不是因果分析 ✗
  · 成功率高 ≠ 工具好 ✗（可能只是用得少 ✓）
  · 样本少的工具**不评级** ✓ 标 '?'
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
RUNS = pathlib.Path(str(pathlib.Path(__file__).resolve().parent / 'runs'))
TRAJ_DIR = ROOT / 'data' / 'trajectories'


def _iter_runs():
    if not RUNS.exists():
        return
    for line in RUNS.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def _iter_trajs():
    if not TRAJ_DIR.exists():
        return
    for f in sorted(TRAJ_DIR.glob('*.jsonl')):
        for line in f.open(encoding='utf-8', errors='replace'):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


import datetime as _dt
import os as _os

# ★ P0-4（2026-10-21）: 时间窗口 ✓
#   原来混新旧数据 ✗ ⇒ D 级结论不可靠 ✗
DEFAULT_DAYS = int(_os.environ.get('TOOL_QUALITY_DAYS') or 7)


def _parse_ts(s) -> _dt.datetime | None:
    """解析时间戳 ✓（多种格式 ✓）"""
    t = str(s or '').strip()
    if not t:
        return None
    t = t.replace('Z', '+00:00')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            d = _dt.datetime.strptime(t, fmt)
            if d.tzinfo is not None:
                d = d.replace(tzinfo=None) - (d.utcoffset() or _dt.timedelta())
            return d
        except Exception:
            continue
    return None


def _cutoff(days: int) -> _dt.datetime:
    return _dt.datetime.now() - _dt.timedelta(days=days)


def collect(days: int | None = None) -> dict:
    """收集每个工具的调用统计 ✓

    days: 只统计最近 N 天 ✓（None=全部 ✓）
    """
    days = DEFAULT_DAYS if days is None else days
    cutoff = _cutoff(days) if days and days > 0 else None
    st = defaultdict(lambda: {
        'uses': 0, 'ok': 0, 'err': 0,
        'elapsed': [], 'sources': set(),
    })

    # 时间范围记录 ✓（诚实 ✓ 显示用了哪些数据 ✓）
    ranges = {'eval': [None, None], 'trajectory': [None, None]}
    skipped = {'eval': 0, 'trajectory': 0}

    def _mark(src, d):
        if d is None:
            return
        lo, hi = ranges[src]
        ranges[src][0] = d if lo is None or d < lo else lo
        ranges[src][1] = d if hi is None or d > hi else hi

    # ① eval runs ✓
    for r in _iter_runs():
        if r.get('placeholder'):
            continue
        _ts = _parse_ts(r.get('started_at') or r.get('ts') or r.get('ended_at'))
        if cutoff is not None and _ts is not None and _ts < cutoff:
            skipped['eval'] += 1
            continue
        _mark('eval', _ts)
        tools = [str(t) for t in (r.get('exec_tools') or []) if t]
        # ★ 修（2026-10-21）: 用 exec_err_tools 判定失败 ✓
        #   为什么: exec_ok_tools = "有 UI 卡片且 ok" ✗
        #   没有卡片的工具（list_running_tasks 等）永远不算 ok ✗
        #   ⇒ 会把好工具误判成失败 ✗✗
        errs = set(str(t) for t in (r.get('exec_err_tools') or []) if t)
        oks_ui = set(str(t) for t in (r.get('exec_ok_tools') or []) if t)
        el = float(r.get('elapsed_s') or 0)
        for t in tools:
            d = st[t]
            d['uses'] += 1
            if t in errs:
                d['err'] += 1            # 明确报错 ✓
            elif t in oks_ui:
                d['ok'] += 1             # UI 确认 ok ✓
            else:
                # ⚠ 既不在 err 也不在 ok_ui ⇒ **无 UI 卡片** ⚠
                #   保守: 算**成功** ✓（因为没有明确错误 ✓）
                #   但记录这个来源 ⚠（诚实 ✓）
                d['ok'] += 1
                d.setdefault('no_ui_card', 0)
                d['no_ui_card'] += 1
            if el > 0:
                d['elapsed'].append(el / max(len(tools), 1))
            d['sources'].add('eval')

    # ② trajectories ✓
    for tr in _iter_trajs():
        _ts = _parse_ts(tr.get('ts'))
        if cutoff is not None and _ts is not None and _ts < cutoff:
            skipped['trajectory'] += 1
            continue
        _mark('trajectory', _ts)
        for step in (tr.get('steps') or []):
            if not isinstance(step, dict):
                continue
            for tc in (step.get('tool_calls') or []):
                if not isinstance(tc, dict):
                    continue
                tool = str(tc.get('tool') or '').strip()
                if not tool:
                    continue
                # 剥 paper_lora_ 前缀 ✓（统一成平台工具名 ✓）
                base = tool[len('paper_lora_'):] if tool.startswith('paper_lora_') else tool
                status = str(tc.get('status') or '').lower()
                res = str(tc.get('result') or '')
                d = st[base]
                d['uses'] += 1
                ok = (status in ('completed', 'success', 'ok')) and bool(res) \
                    and not res.lower().startswith('error')
                if ok:
                    d['ok'] += 1
                else:
                    d['err'] += 1
                d['sources'].add('trajectory')

    st['__meta__'] = {'ranges': ranges, 'skipped': skipped, 'days': days}
    return st


# ★ DSH 自带工具（**不是平台工具** ✗ 打分时排除 ✓）
DSH_BUILTINS = {
    'read', 'write', 'edit', 'pwsh', 'grep', 'glob', 'task', 'todo_write',
    'web_search', 'web_fetch', 'present', 'ask_user_question', 'subagent',
    'spawn_teammate', 'send_message', 'wait_agent', 'list_agents',
    'team_task_create', 'team_task_get', 'team_task_list', 'team_task_update',
    'create_goal', 'get_goal', 'update_goal', 'job_list', 'job_output', 'job_kill',
    'skill', 'workflow', 'exit_plan_mode', 'read_image', 'modlens_read_image',
    'call',                 # 插件的通用兜底工具 ✓ 不是平台工具 ✗
    'dup_guard',            # 平台内部守卫 ✗ 不该算工具 ✓
    'browser_open', 'browser_snapshot', 'browser_click', 'browser_fill',
    'browser_read', 'browser_screenshot', 'browser_download', 'browser_upload',
    'browser_move', 'browser_wait', 'browser_tabs', 'browser_close', 'browser_scroll',
    'browser_find',
}


def grade(uses: int, ok_rate: float) -> str:
    """评级 ✓（保守 ✓ 样本少不给 A ✗）"""
    if uses == 0:
        return '?'
    if uses < 3:
        return '?'          # 样本太少 ⇒ 不评级 ✓
    if uses >= 20 and ok_rate >= 0.95:
        return 'A'
    if uses >= 5 and ok_rate >= 0.80:
        return 'B'
    if ok_rate >= 0.60:
        return 'C'
    return 'D'


def report() -> dict:
    st = collect()
    meta = st.pop('__meta__', {})
    # ★ 过滤 DSH 内置 + 非平台工具 ✓
    st = {k: v for k, v in st.items() if k not in DSH_BUILTINS}
    out = []
    for name, d in st.items():
        uses = d['uses']
        ok_rate = (d['ok'] / uses) if uses else 0.0
        err_rate = (d['err'] / uses) if uses else 0.0
        el = d['elapsed']
        out.append({
            'tool': name,
            'uses': uses,
            'ok': d['ok'],
            'err': d['err'],
            'ok_rate': round(ok_rate, 4),
            'err_rate': round(err_rate, 4),
            'avg_elapsed_s': round(sum(el) / len(el), 2) if el else None,
            'grade': grade(uses, ok_rate),
            'sources': sorted(d['sources']),
            # ⚠ 多少个调用是"没 UI 卡片但无明确错误"⇒ 算成功 ✓
            'no_ui_card': d.get('no_ui_card', 0),
        })
    # 排序: 先按 uses 降序 ✓ 再按 ok_rate ✓
    out.sort(key=lambda x: (-x['uses'], -x['ok_rate']))
    # 汇总 ✓
    by_grade = defaultdict(int)
    for r in out:
        by_grade[r['grade']] += 1
    # 数据新鲜度 ✓（诚实 ✓）
    _rng = meta.get('ranges') or {}
    _fresh = {}
    for src, (lo, hi) in _rng.items():
        _fresh[src] = {
            'from': lo.strftime('%Y-%m-%d %H:%M') if lo else None,
            'to': hi.strftime('%Y-%m-%d %H:%M') if hi else None,
            'skipped_old': (meta.get('skipped') or {}).get(src, 0),
        }
    warnings = []
    if meta.get('days'):
        warnings.append('只统计最近 %d 天 ✓（可用 TOOL_QUALITY_DAYS 环境变量调整）' % meta['days'])
    tr = _fresh.get('trajectory') or {}
    if tr.get('from') and tr['from'] < '2026-10-15':
        warnings.append('⚠ 轨迹数据偏旧（%s 起）⇒ 反映的是历史状态 ✗ 不代表当前 ✗' % tr['from'])
    if not _fresh.get('eval', {}).get('from'):
        warnings.append('⚠ 没有 eval 数据（只用了轨迹 ⚠ 结论偏旧）')
    return {
        'tools_total': len(out),
        'by_grade': dict(sorted(by_grade.items())),
        'tools': out,
        'data_freshness': _fresh,
        'warnings': warnings,
        'note': ('观测统计 ✗ 不是因果分析 ✗ · '
                 'uses<3 不评级(?) ✓ · 成功率高≠工具好 ✗ · '
                 '**结论的有效期 = 数据的时间范围** ✓'),
    }


if __name__ == '__main__':
    r = report()
    print('  数据时间范围（★ P0-4: 只统计新数据 ✓）:')
    for src, f in (r.get('data_freshness') or {}).items():
        print('    %-12s %s → %s（跳过旧数据 %d 条）' % (
            src, f.get('from'), f.get('to'), f.get('skipped_old', 0)))
    for w in (r.get('warnings') or []):
        print('    %s' % w)
    print('')
    print('  工具总数（有数据）: %d' % r['tools_total'])
    print('  评级分布: %s' % r['by_grade'])
    print('')
    print('  Top 20（按使用次数 ✓）:')
    print('  %-30s %6s %8s %8s %6s' % ('tool', 'uses', 'ok_rate', 'err_rate', 'grade'))
    for t in r['tools'][:20]:
        print('  %-30s %6d %7.1f%% %7.1f%% %6s' % (
            t['tool'], t['uses'], t['ok_rate'] * 100, t['err_rate'] * 100, t['grade']))
    print('')
    print('  ⚠ 低质量（D 级）:')
    ds = [t for t in r['tools'] if t['grade'] == 'D']
    if ds:
        for t in ds[:10]:
            print('    %-30s uses=%d ok_rate=%.1f%%' % (t['tool'], t['uses'], t['ok_rate'] * 100))
    else:
        print('    （无 ✓）')
    print('')
    print('  ⚠ 样本不足（? 级）: %d 个' % len([t for t in r['tools'] if t['grade'] == '?']))
