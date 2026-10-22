#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sim_router.py — 语义仿真路由（自然语言 → 仿真节点 + 参数抽取）

## 为什么需要
sim_guard.detect_compute 只会找「显式算式」(a/b, a*b) ✓
遇到自然语言物理题/统计题 **完全不会路由** ✗
实测事故：
  Q: 43223 N 的力以 45 度投掷 2kg 的球能飞多远
  A: 4410.4 米  ✗✗ 物理错误（把力当速度、量纲不对）
  平台明明有 physics_calc（描述里写着"抛体射程"）却没被调用 ✗

## 设计原则
1. **关键词 + 正则分类**（不靠模型猜）→ 稳定、可测
2. **参数抽取**：抽得到才算；抽不到 ⇒ **返回 missing + 必须反问** ✓
3. **绝不硬编**：缺参数时不允许模型自己造假设（这是本次事故的根源）

## 用法
  import sim_router as sr
  r = sr.route("43223 N 的力以 45 度投掷 2kg 的球能飞多远")
  # {'matched':True,'node':'physics_calc','formula':'projectile_range',
  #  'params':{'force':43223,'mass':2,'angle_deg':45},
  #  'missing':['v0'],'can_compute':False,'advice':'...'}
"""
import re

# ══════════ 规则表：关键词 → 节点 + 公式 + 必填参数 ══════════
RULES = [
    {
        'node': 'physics_calc', 'formula': 'projectile_range',
        'kw': ('抛体', '抛出', '投掷', '抛射', '斜抛', '平抛', '射程', '能飞多远', '飞多远'),
        'need': ('v0', 'angle_deg'),
        'opt': ('force', 'mass'),
        'label': '抛体射程',
        'formula_text': 'R = v0^2 * sin(2*angle) / g',
        'missing_hint': '需要初速度 v0（米/秒）和抛射角度；只给力无法求速度 —— 还需作用时间或作用距离',
    },
    {
        'node': 'physics_calc', 'formula': 'free_fall_time',
        'kw': ('自由落体', '自由落下', '下落时间', '落地时间', '下落多'),
        'need': ('height',),
        'opt': (),
        'label': '自由落体',
        'formula_text': 't = sqrt(2*h/g)',
        'missing_hint': '需要下落高度 h（米）',
    },
    {
        'node': 'physics_calc', 'formula': 'force',
        'kw': ('牛顿第二定律', '合力', '加速度', 'F=ma', '受力'),
        'need': ('mass', 'acceleration'),
        'opt': ('force',),
        'label': '牛顿第二定律',
        'formula_text': 'F = m * a',
        'missing_hint': '需要质量 m（kg）和加速度 a（m/s²）',
    },
    {
        'node': 'physics_calc', 'formula': 'kinetic_energy',
        'kw': ('动能',),
        'need': ('mass', 'speed'),
        'opt': (),
        'label': '动能',
        'formula_text': 'Ek = 0.5*m*v^2',
        'missing_hint': '需要质量 m（kg）和速度 v（m/s）',
    },
    {
        'node': 'physics_calc', 'formula': 'gravitational_potential',
        'kw': ('重力势能', '势能'),
        'need': ('mass', 'height'),
        'opt': (),
        'label': '重力势能',
        'formula_text': 'Ep = m*g*h',
        'missing_hint': '需要质量 m（kg）和高度 h（m）',
    },
    {
        'node': 'unit_convert',
        'kw': ('等于多少', '换算', '转换成', '折合', '相当于多少', '多少米', '多少公里',
               '多少千米', '多少厘米', '多少毫米', '多少克', '多少千克', '多少吨'),
        'need': (), 'opt': (),
        'label': '单位换算',
        'formula_text': '确定性换算',
        'missing_hint': '需要明确的"数值+单位 → 目标单位"',
    },
    {
        'node': 'stats',
        'kw': ('平均值', '平均数', '中位数', '标准差', '方差', '总和', '求和'),
        'need': (), 'opt': (),
        'label': '统计',
        'formula_text': '确定性统计',
        'missing_hint': '需要明确的数据列表',
    },
    {
        'node': 'ratio',
        'kw': ('占比', '百分比', '比率', '比例是', '通过率', '成功率'),
        'need': (), 'opt': (),
        'label': '占比',
        'formula_text': 'part / total * 100%',
        'missing_hint': '需要分子与分母',
    },
]

# ══════════ 参数抽取 ══════════
NUM = r'([-+]?\d+(?:\.\d+)?)'

PARAM_PATTERNS = {
    'force': [NUM + r'\s*(?:N|牛顿|牛)\b'],
    'mass': [NUM + r'\s*(?:kg|千克|公斤|KG|Kg)'],
    'height': [NUM + r'\s*(?:m|米|公尺)高', r'高度\s*' + NUM, NUM + r'\s*米(?:的)?高'],
    'speed': [NUM + r'\s*(?:m/s|米/秒|米每秒)'],
    'v0': [r'(?:初速度|初始速度|v0|v₀)\s*[=:：为]?\s*' + NUM,
           NUM + r'\s*(?:m/s|米/秒|米每秒)'],
    'angle_deg': [r'(\d+(?:\.\d+)?)\s*(?:度|°|deg)'],
    'acceleration': [NUM + r'\s*(?:m/s²|m/s2|米/秒²|米每二次方秒)'],
}

# 力的单位是 N 时，"抛出/投掷"里的角度不要误当 force
def extract_params(text):
    t = str(text or '')
    out = {}
    for k, pats in PARAM_PATTERNS.items():
        for p in pats:
            m = re.search(p, t, re.I)
            if m:
                try:
                    out[k] = float(m.group(1))
                except Exception:
                    pass
                break
    # 角度合理性：0-90
    if 'angle_deg' in out and not (0 <= out['angle_deg'] <= 90):
        out.pop('angle_deg', None)
    return out


def classify(text):
    """返回命中的规则（按关键词命中数排序）"""
    t = str(text or '')
    hits = []
    for r in RULES:
        n = sum(1 for k in r['kw'] if k in t)
        if n:
            hits.append((n, r))
    hits.sort(key=lambda x: -x[0])
    return [r for _, r in hits]


_EXPR_PRIORITY = re.compile(r'\d[\d.\s]*[+\-*/^][\d.\s(]')


def route(text):
    """语义路由：返回 {matched, node, formula, params, missing, can_compute, advice}"""
    t = str(text or '')
    # 显式算式（a/b、a*b）优先交给 sim_guard，别被"等于多少"误判成单位换算
    if _EXPR_PRIORITY.search(t):
        return {'matched': False, 'node': None, 'reason': '含显式算式，交给 sim_guard'}
    cands = classify(t)
    if not cands:
        return {'matched': False, 'node': None, 'reason': '无仿真规则命中'}
    r = cands[0]
    params = extract_params(t)
    need = list(r.get('need') or [])
    missing = [k for k in need if k not in params]
    can = len(missing) == 0
    advice = ''
    if not can:
        advice = ('【仿真路由】本题属于「%s」，已匹配节点 %s，但缺少参数：%s。'
                  '%s。⇒ 必须向用户**追问这些参数**，禁止自行假设数值硬算 ✗'
                  % (r['label'], r['node'], ', '.join(missing), r.get('missing_hint', '')))
    else:
        advice = ('【仿真路由】本题属于「%s」，参数齐全 %s ⇒ 必须调用节点 %s（formula=%s），'
                  '公式 %s，禁止心算 ✗'
                  % (r['label'], params, r['node'], r.get('formula', ''), r.get('formula_text', '')))
    return {
        'matched': True,
        'node': r['node'],
        'formula': r.get('formula'),
        'label': r['label'],
        'params': params,
        'missing': missing,
        'can_compute': can,
        'formula_text': r.get('formula_text', ''),
        'advice': advice,
        'candidates': [{'node': x['node'], 'label': x['label']} for x in cands[:3]],
    }


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    CASES = [
        '43223 N 的力以 45 度投掷 2kg 的球能飞多远',
        '以 20 m/s 的初速度、45 度抛出一个球，射程多少',
        '质量 2kg 从 10 米高自由落体，多久落地',
        '3 公里等于多少米',
        '这组数据 1 2 3 4 5 的平均值',
        '246427649/21124242 等于多少',
    ]
    for c in CASES:
        r = route(c)
        print('Q: %s' % c[:40])
        if not r['matched']:
            print('   → 未命中规则（保持原逻辑）')
        else:
            print('   → node=%s can_compute=%s missing=%s' % (
                r['node'], r['can_compute'], r['missing']))
            print('     params=%s' % r['params'])
        print('')
