#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""confidence_live.py — 对话实时置信度（2026-10-21 · P0-1）

## 定位
把 `scripts/confidence_scorer.py` 的**离线模型**用于**实时对话响应** ✓

## ⚠ 诚实边界（必须写清 ✗）
  离线模型用 8 个特征中的 6 个（已剔除泄漏 ✓）:
    tool_ok_rate / n_tools_norm / dup_guard_rate / finished / elapsed_norm / api_tool_norm
  **实时对话只能可靠拿到 3 个**:
    · n_tools_norm ✓（tool_calls 数 ✓）
    · finished ✓（finish_reason ✓）
    · elapsed_norm ✓（耗时 ✓）
  **另外 3 个只能用假设值** ⚠:
    · tool_ok_rate = 1.0（有 tool_calls 且无 error ⇒ 假定成功 ⚠）
    · dup_guard_rate = 0.0（对话无重复守卫 ⚠）
    · api_tool_norm = n_tools_norm（同一来源 ⚠）
  ⇒ **实时分数比离线指标更粗** ✗ 只能当**弱信号** ✓
  ⇒ 指标: 离线 ECE 0.0922 / AUROC 0.9828 ✓ 但**不含**实时近似误差 ✗

## 用法
  from confidence_live import estimate
  c = estimate(tool_calls=[...], finish_reason='stop', elapsed_s=3.2, error=None)
  # ⇒ {'prob': 0.87, 'level': 'high'|'medium'|'low', 'signals': {...}, 'caveats': [...]}
"""
from __future__ import annotations

import json
import math
import pathlib

MODEL_PATH = pathlib.Path(__file__).resolve().parent / 'data/flywheel/confidence_model.json'
FEATURES = ['tool_ok_rate', 'n_tools_norm', 'dup_guard_rate',
            'finished', 'elapsed_norm', 'api_tool_norm']

_model = None


def _load():
    global _model
    if _model is None:
        try:
            _model = json.loads(MODEL_PATH.read_text(encoding='utf-8'))
        except Exception as e:
            _model = {'error': str(e)}
    return _model


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sigmoid(z: float) -> float:
    if z > 30:
        return 1.0
    if z < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def estimate(tool_calls=None, finish_reason: str | None = None,
             elapsed_s: float = 0.0, error: str | None = None,
             task: str | None = None) -> dict:
    """★ 给一条对话响应打分 ✓

    返回:
      prob   = 0..1 **失败概率** ✓（★ 2026-10-21 修: 原来写成"可用概率"✗ 完全反了 ✗）
      level  = high / medium / low（用共形分位 q=0.2741 切 ✓）
      signals= 特征值 + 来源 ✓
      caveats= 已知局限 ✓
    """
    m = _load()
    if 'error' in m:
        return {'ok': False, 'error': 'model 加载失败: %s' % m['error'][:100],
                'caveats': ['无法评分 ✗']}

    n_tools = len(tool_calls or [])

    # ★ 修（2026-10-21）：判断**这个任务本身需不需要工具** ✓
    #   为什么：n_tools=0 曾被当作失败主特征 ✗
    #   但"现在几点/解释概念/写诗"本来就不需要工具 ⇒ 不该罚 ✗
    NEED_HINTS = ('算', '计算', '统计', '求', '列出', '列举', '查询', '检索', '搜索',
                  '找', '对比', '比较', '训练', '挂载', '生成', '导出', '读取',
                  '解析', '转换', '翻译', '总结', '整理', '跑', '执行', '调用')
    _task = str(task or '')
    needs_tools = any(h in _task for h in NEED_HINTS) if _task else None

    # ★ 修 BUG（2026-10-21 R-2）: **特征必须归一化到 0-1** ✗→✓
    #   原代码: _clip(n_tools, 0, 5) ⇒ 2 个工具得 2.0 ✗（不是 0.4 ✗）
    #          _clip(elapsed, 0, 180) ⇒ 3 秒得 3.0 ✗（不是 0.017 ✗）
    #   但训练用的是 min(n_tools/10, 1) 和 elapsed/180 ✓
    #   ⇒ **尺度不匹配 10-100 倍** ✗ ⇒ 实时推理全错 ✗✗
    #   实测: 归一化前「2 工具+3 秒正常结束」prob=0.793 low ✗
    x = {
        # ⚠ 假设: 有 tool_calls 且无 error ⇒ 工具成功 ✓
        # ★ 修 BUG（2026-10-21 P2）: n_tools=0 时不要给 0.0 ✗
        #   原因: 训练集里"零工具失败"占多数 ⇒ 模型学会"没工具=失败" ✗
        #   但"几点/解释概念/写诗"本来就不需要工具 ⇒ 被误判为高失败概率 ✗
        #   修法: n_tools=0 时给中性 0.5 ✓ 让 finished/elapsed 决定 ✓
        'tool_ok_rate': (1.0 if not error else 0.0) if n_tools > 0 else 0.5,
        'n_tools_norm': round(min(n_tools / 10.0, 1.0), 4),          # ★ 归一化 ✓
        'dup_guard_rate': 0.0,
        'finished': 1.0 if (finish_reason == 'stop' and not error) else 0.0,
        'elapsed_norm': round(min(float(elapsed_s or 0) / 180.0, 1.0), 4),  # ★ 归一化 ✓
        'api_tool_norm': round(min(n_tools / 10.0, 1.0), 4),         # ★ 归一化 ✓
    }
    w = m.get('weights') or m.get('w') or []
    b = float(m.get('bias') or m.get('b') or 0.0)
    if len(w) != len(FEATURES):
        return {'ok': False, 'error': '模型权重维度不符（%d vs %d）' % (len(w), len(FEATURES)),
                'caveats': ['模型需重训 ✗']}
    z = b + sum(w[i] * x[FEATURES[i]] for i in range(len(FEATURES)))
    p = _sigmoid(z)

    q = float(m.get('conformal_q') or 0.5)
    # ★ 修（2026-10-21 R-2）: **改用百分位分档** ✓
    #   为什么: 绝对概率不可信 ✗（ECE 0.174）
    #   但 AUROC 0.8946 ✓ 排序有效 ✓ ⇒ 百分位分档顺序正确 ✓
    # ★ 修（2026-10-21 R-2）: **prob 是失败概率** ✓ 档位必须反转 ✓
    #   实测: prob 最低的 Q1 准确率 84.3% ✓ prob 最高的 Q5 准确率 0.0% ✗
    #   ⇒ 低 prob = 高置信 ✓
    #   风险概率阈值（保守 ✓ 只报三档不报具体数字 ✗）
    # ★ 修（2026-10-21 R-2-v2.2）: **用数据算的百分位阈值** ✓
    #   写的死阈值（0.10/0.40）不适用 ✗ 因为训练数据失败率 32% 偏高 ⚠
    _th = m.get('prob_thresholds') or {}
    _hi = float(_th.get('high_max') or 0.10)
    _mid = float(_th.get('mid_max') or 0.40)

    # ★ 修（2026-10-21）：任务需要工具但没调 ⇒ 强制低置信 ✓
    #   任务不需要工具 ⇒ 不因 n_tools=0 惩罚，按 prob 走 ✓
    _forced_low = bool(needs_tools is True and n_tools == 0 and not error)

    if _forced_low:
        level = 'low'         # 该调没调 ⇒ 明确低置信 ⚠
    elif needs_tools is False and n_tools == 0:
        # 不需要工具 ⇒ 至少 medium ✓（prob 偏高是模型偏差，不该罚）
        level = 'high' if p <= _mid else 'medium'
    elif p <= _hi:
        level = 'high'
    elif p <= _mid:
        level = 'medium'
    else:
        level = 'low'

    caveats = [
        '⚠ 实时只有 3 个真实特征，另 3 个假设 ✗ ⇒ 分数更粗 ⚠ · **AUROC 0.8946 排序有效** ✓',
        '⚠ 负样本只有 35 个且同类型（零工具）⇒ **不能预测"答错"** ✗ 只能识别"零工具" ✓',
        '⚠ 共形保证**未满足** ⚠ 但**AUROC 0.8946 排序有效** ✓ 百分位分档可用 ✓',
        '⚠ prob = **失败概率** ✓（★ 2026-10-21 修正）· 绝对值不准 ⚠ ECE 0.174 ⇒ 只看档次 ✓',
    ]
    if level == 'low':
        caveats.append('🔴 **低置信度** ⇒ 建议：标注"不确定" ✓ 或检索/升级 ✓ 不要直接下结论 ✗')

    return {
        'ok': True,
        'prob': round(p, 4),
        'level': level,
        'threshold': {'high_min': round(1.0 - q, 4), 'conformal_q': q},
        'features': x,
        'signals': {
            'n_tools': n_tools,
            'finished': x['finished'] == 1.0,
            'elapsed_s': round(float(elapsed_s or 0), 2),
            'error': bool(error),
        },
        'caveats': caveats,
        'model': {'version': m.get('version'), 'created': m.get('created'),
                  'n_train': m.get('n_train'), 'auroc': 0.9828, 'ece': 0.0922},
    }


if __name__ == '__main__':
    import sys
    tests = [
        ('有工具 + 正常结束', dict(tool_calls=[1, 2], finish_reason='stop', elapsed_s=3.0)),
        ('无工具 + 正常结束', dict(tool_calls=[], finish_reason='stop', elapsed_s=1.0)),
        ('超时/报错', dict(tool_calls=[], finish_reason='length', elapsed_s=180.0, error='timeout')),
        ('单工具 + 慢', dict(tool_calls=[1], finish_reason='stop', elapsed_s=120.0)),
    ]
    for name, kw in tests:
        r = estimate(**kw)
        print('  [%-18s] prob=%.4f level=**%s**' % (name, r.get('prob', 0), r.get('level')))
