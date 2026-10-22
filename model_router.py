#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_router.py — 模型路由真执行（P1-2 · 2026-10-21）

## 问题
  orchestrator.route_model() 只是**建议** ✓ 没人执行 ✗
  `model` 由**前端硬选** ✗ ⇒ "自动路由" 是假的 ✗

## 方案（★ opt-in ✓ 不破坏现有行为 ✗）
  ① 前端传 `model="auto"`（或 "route" / ""）⇒ **后端自动路由** ✓
  ② 传明确 model（如 "vllm:base" / "openai:deepseek-chat"）⇒ **完全尊重** ✓ 不覆盖 ✗
  ③ 决策复用 orchestrator.route_model() ✓（已有规则 ✓）
  ④ **审计** ✓（记录每次路由决策 ✓ 可追溯 ✓）
  ⑤ **fallback** ✓（本地不可用 ⇒ 云端 ✓；云端不可用 ⇒ 本地 ✓）

## ⚠ 诚实边界
  · 路由规则是**启发式** ✗ 不是训练出来的小模型 ✗
  · 未做 A/B 验证路由质量 ✗
  · 只验证"决策 + 执行"链路通 ✓
"""
from __future__ import annotations

import sys

# 复用 orchestrator 的路由规则 ✓
try:
    from orchestrator import route_model, _is_train_task
except Exception:  # pragma: no cover
    def route_model(task, strategy='auto'):
        return {'model': 'local_7b', 'why': 'orchestrator 不可用 ⇒ 默认本地 ⚠'}

    def _is_train_task(task):
        return False


AUTO_TOKENS = ('auto', 'route', 'router', 'default', '')


def is_auto(model_id: str) -> bool:
    """判断是否要求自动路由 ✓"""
    m = str(model_id or '').strip().lower()
    if m in AUTO_TOKENS:
        return True
    # 也支持 "auto:anything" ✓
    return m.split(':', 1)[0] in ('auto', 'route', 'router')


def resolve(model_id: str, task: str, available: dict | None = None) -> dict:
    """★ 决定用哪个 provider ✓

    参数:
      model_id: 前端传的 model 字段 ✓
      task:     任务文本（通常是最后一条 user message ✓）
      available: {'local': True/False, 'cloud': True/False} 可用性 ✓

    返回:
      {'provider': 'vllm'|'openai', 'model_id': '...', 'route': {...}, 'why': '...'}
    """
    avail = {'local': True, 'cloud': True}
    if available:
        avail.update({k: bool(v) for k, v in available.items() if k in ('local', 'cloud')})

    mid = str(model_id or '').strip()

    # ① 明确指定 ⇒ 尊重 ✓（不覆盖用户选择 ✗）
    if not is_auto(mid):
        pname = mid.split(':', 1)[0] or 'vllm'
        # 但要在不可用时 fallback ✓
        if pname == 'vllm' and not avail['local'] and avail['cloud']:
            return {'provider': 'openai', 'model_id': 'openai:deepseek-chat',
                    'why': '用户指定本地但**本地不可用** ⚠ ⇒ fallback 云端 ✓',
                    'fallback': True, 'explicit': True}
        if pname == 'openai' and not avail['cloud'] and avail['local']:
            return {'provider': 'vllm', 'model_id': 'vllm:base',
                    'why': '用户指定云端但**云端不可用** ⚠ ⇒ fallback 本地 ✓',
                    'fallback': True, 'explicit': True}
        return {'provider': pname, 'model_id': mid,
                'why': '用户明确指定 **%s** ✓ 不覆盖 ✗' % pname,
                'fallback': False, 'explicit': True}

    # ② 自动路由 ✓
    r = route_model(task)
    want = r.get('model')  # 'local_7b' | 'cloud'
    if want == 'cloud' and avail['cloud']:
        return {'provider': 'openai', 'model_id': 'openai:deepseek-chat',
                'route': r, 'why': '**自动路由 → 云端** ✓ %s' % r.get('why'),
                'fallback': False, 'explicit': False}
    if want == 'cloud' and not avail['cloud'] and avail['local']:
        return {'provider': 'vllm', 'model_id': 'vllm:base',
                'route': r, 'why': '规则选云端但云端不可用 ⚠ ⇒ fallback 本地 ✓',
                'fallback': True, 'explicit': False}
    if want == 'local_7b' and avail['local']:
        return {'provider': 'vllm', 'model_id': 'vllm:base',
                'route': r, 'why': '**自动路由 → 本地** ✓ %s' % r.get('why'),
                'fallback': False, 'explicit': False}
    if want == 'local_7b' and not avail['local'] and avail['cloud']:
        return {'provider': 'openai', 'model_id': 'openai:deepseek-chat',
                'route': r, 'why': '规则选本地但本地不可用 ⚠ ⇒ fallback 云端 ✓',
                'fallback': True, 'explicit': False}

    # ③ 都不可用 ✗
    return {'provider': 'vllm', 'model_id': 'vllm:base',
            'why': '⚠ 本地和云端都不可用 ⇒ 仍试本地（会报错 ✓）',
            'fallback': True, 'explicit': False, 'error': 'no_provider_available'}


_LOG = []


def log_decision(res: dict, task: str = '') -> None:
    """审计 ✓（内存 + 可选落盘 ✓）"""
    import datetime
    rec = {
        'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'task': (task or '')[:120],
        'provider': res.get('provider'),
        'model_id': res.get('model_id'),
        'why': res.get('why'),
        'fallback': res.get('fallback'),
        'explicit': res.get('explicit'),
    }
    _LOG.append(rec)
    if len(_LOG) > 500:
        del _LOG[:200]
    print('[model_router] %s → %s（%s）' % (
        '显式' if rec['explicit'] else '自动',
        rec['provider'], str(rec['why'])[:80]), flush=True)


def recent(n: int = 20) -> list:
    """最近的路由决策 ✓"""
    return _LOG[-n:]


if __name__ == '__main__':
    import json
    cases = [
        ('auto', '最近大模型置信度校准的论文', {'local': True, 'cloud': True}),
        ('auto', '列出平台所有 LoRA', {'local': True, 'cloud': True}),
        ('auto', '帮我算一下 1+1', {'local': True, 'cloud': True}),
        ('', '训练一个工具调用 LoRA', {'local': True, 'cloud': True}),
        ('vllm:base', '随便', {'local': True, 'cloud': True}),
        ('auto', '最近论文', {'local': True, 'cloud': False}),
        ('auto', '列出 LoRA', {'local': False, 'cloud': True}),
        ('vllm:base', '随便', {'local': False, 'cloud': True}),
    ]
    for mid, task, avail in cases:
        r = resolve(mid, task, avail)
        print('  model=%-12r task=%-28r avail=%s' % (mid, task[:26], avail))
        print('     ⇒ provider=%-7s model_id=%-22s fallback=%s explicit=%s' % (
            r['provider'], r['model_id'], r.get('fallback'), r.get('explicit')))
        print('     why=%s' % str(r.get('why'))[:100])
        print('')
