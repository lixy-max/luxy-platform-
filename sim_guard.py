#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""递归计算守卫（Recursive Compute Guard）

## 要解决的问题
仿真节点（sim_nodes）已经能算 ✓，但**强制**只在一条路上（前端 agent 循环 ✗）。
真实系统里 LLM 调用有很多层 ✓：
    主 agent 回合
      ├─ 工作流节点（= 子代理 ✓）
      │    ├─ 子节点
      │    └─ 子子节点
      ├─ 云端接手（cloud handoff）
      └─ 文档助手 / 其它调用方
任意一层遇到计算都可能心算 ✗ ⇒ 必须**递归**地检测并强制走仿真节点 ✓

## 设计
1. **作用域树**（scope tree）
   open_scope(parent) → scope_id；每个作用域可记录: LLM 输出文本 / 仿真节点使用
   关闭时做**递归校验** ✓：本作用域或任一后代成功用过仿真节点 ⇒ 满足 ✓（子代理算过了 = 父层不必重算 ✓）
   任何一层有计算意图却没人用 ⇒ 违规 ✓（明确报出是哪一层 ✗）

2. **动态加载的节点集合**
   节点从 sim_nodes.SIM_NODES 运行时读取 ✓ 增删节点无需改本模块 ✓
   检测/修复只用"当前注册表里真实存在的节点"来算 ✓（find_node_for(expr) ✓）

3. **强制（不是只报警 ✗）**
   repair(text) 会把文本里出现的算式抽出来 ✓ 真的调用仿真节点 ✓ 把数字替换成精确值 ✓
   ⇒ 即使模型心算了，最终交付的数字也是"算过"的 ✓（可复算 ✓）

## 与 sim_nodes 的分工
sim_nodes = 会算的"计算器"（确定性算子 ✓）
sim_guard = 管着"谁必须算、算过没有、没算就补上"的"纪律执行器" ✓
"""
from __future__ import annotations

import re
import threading
import time
import uuid

__all__ = [
    'open_scope', 'record_llm_output', 'record_sim_use', 'check_scope',
    'close_scope', 'list_scopes', 'reset',
    'detect_compute', 'extract_expressions', 'repair', 'guard_text',
]

_LOCK = threading.RLock()
_SCOPES: dict[str, dict] = {}
_MAX_SCOPES = 2000                      # 防内存泄漏 ✓（超了就丢最旧的已关闭 ✓）

# ---- 计算意图检测（保守 ✓ 宁可多拦也别放过心算 ✗）----
_COMPUTE_VERBS = re.compile(
    r'(计算|算一下|算算|求一下|求和|合计|总共|总数|平均|均值|中位|方差|标准差|百分比|占比|比率|'
    r'增长率|换算|四舍五入|除以|乘以|加总|统计|分位数|概率|通过率|占比|差值|倍数|比例)')
# 文本里的"算式"：含运算符且含数字 ✓（如 (12+18)/7 · 23/37*100 · 0.85*16-4.5）
# 算式:
#   · 2 个数的 + - * / 只在【不是日期/版本号】时才算 ✓（2026-10-19 ✗ 版本换行 ✗）
#   · 3 个以上数时允许减号 ✓（(12+18+25)/7 ✓）
#   · 支持全角 ＋－＊／＝ ✓（实测模型会输出全角 ✗）
_EXPR_RE = re.compile(
    r'[（(]?\d+(?:\.\d+)?[）)]?\s*[+＋*/×÷^%]\s*[（(]?\d+(?:\.\d+)?[）)]?'
    r'(?:\s*[+＋\-－*/×÷^%]\s*[（(]?\d+(?:\.\d+)?[）)]?)*'
    r'|[（(]?\d+(?:\.\d+)?[）)]?\s*[-－]\s*[（(]?\d+(?:\.\d+)?[）)]?'
    r'(?:\s*[+＋\-－*/×÷^%]\s*[（(]?\d+(?:\.\d+)?[）)]?)+')
# 单独出现的"结果数"（≈ xx 或 = xx ✓）也算主张了一个计算结果 ✓
_RESULT_RE = re.compile(r'(?:≈|≃|=|等于|约为|约)\s*([\d.]+)\s*(%|％|倍|秒|个|条|块|元|次)?')
_NUM_RE = re.compile(r'\d')
# 不是算式的情形（实测踩过: 日期 2026-10-19 被当成减法 ✗ 版本 1.2.3 被当成小数 ✗）
_NOT_EXPR_RE = re.compile(
    r'^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}'      # 日期
    r'|v?\d+(?:\.\d+){2,}'                                                # 版本号
    r'|\d{1,2}:\d{2}(?::\d{2})?)$')                                       # 时间


def _is_expr(e: str) -> bool:
    """这个"算式"其实是日期/版本/时间吗 ✗（是 ⇒ 不算算式 ✓）"""
    x = str(e or '').strip()
    return bool(x) and not _NOT_EXPR_RE.match(x)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def open_scope(parent: str | None = None, kind: str = 'turn', label: str = '', meta: dict | None = None) -> str:
    """新开一个作用域 ✓ parent 不存在则当作根 ✓ 返回 scope_id"""
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        pid = parent if (parent and parent in _SCOPES) else None
        _SCOPES[sid] = {
            'id': sid, 'parent': pid, 'kind': str(kind or 'turn'),
            'label': str(label or ''), 'meta': dict(meta or {}),
            'opened_at': _now(), 'closed': False,
            'llm_outputs': [], 'sim_uses': [], 'errors': [],
        }
        if len(_SCOPES) > _MAX_SCOPES:
            for k in [x for x, v in _SCOPES.items() if v.get('closed')][:500]:
                _SCOPES.pop(k, None)
    return sid


def record_llm_output(scope_id: str, text: str) -> None:
    """记录某作用域产生的 LLM 输出（用于检测计算 ✓）"""
    if not scope_id or text is None:
        return
    with _LOCK:
        sc = _SCOPES.get(scope_id)
        if sc is not None and not sc['closed']:
            sc['llm_outputs'].append(str(text)[:20000])


def record_sim_use(scope_id: str, node: str = '', ok: bool = True) -> None:
    """记录某作用域用过仿真节点 ✓ 只有 ok=True 才算"算过" ✓"""
    if not scope_id:
        return
    with _LOCK:
        sc = _SCOPES.get(scope_id)
        if sc is None or sc['closed']:
            return
        sc['sim_uses'].append({'node': str(node), 'ok': bool(ok), 'at': _now()})
        if not ok:
            sc['errors'].append(str(node))


def _descendants(sid: str) -> list:
    out, stack = [], [sid]
    while stack:
        cur = stack.pop()
        for k, v in list(_SCOPES.items()):
            if v.get('parent') == cur:
                out.append(k)
                stack.append(k)
    return out


def _scope_ok(sid: str) -> bool:
    """该作用域或任一后代成功用过仿真节点 ✓"""
    queue = [sid] + _descendants(sid)
    for k in queue:
        sc = _SCOPES.get(k)
        if sc and any(u.get('ok') for u in sc.get('sim_uses') or []):
            return True
    return False


def _own_compute(sid: str) -> list:
    """该作用域自己的 LLM 输出里出现的计算需求 ✓"""
    sc = _SCOPES.get(sid) or {}
    hits = []
    for t in sc.get('llm_outputs') or []:
        d = detect_compute(t)
        if d['needed']:
            hits.append(d)
    return hits


def check_scope(scope_id: str, text: str | None = None, recursive: bool = True) -> dict:
    """校验一个作用域 ✓ 返回 {ok, violations[], sim_ok, checked_scopes}"""
    with _LOCK:
        if scope_id not in _SCOPES:
            return {'ok': False, 'error': f'未知作用域 {scope_id}', 'violations': [], 'sim_ok': False}
        # ★ 带 text 的 check 顺便记录下来 ✓ 这样父层后续递归校验能看到子层输出 ✓
        try:
            if text:
                record_llm_output(scope_id, text)
        except Exception:
            pass
        targets = [scope_id] + (_descendants(scope_id) if recursive else [])
        violations = []
        for sid in targets:
            sc = _SCOPES.get(sid) or {}
            dets = _own_compute(sid)
            if text and sid == scope_id:
                d = detect_compute(text)
                if d['needed']:
                    dets.append(d)
            if not dets:
                continue
            if _scope_ok(sid):          # 自己或后代算过 ✓ 满足
                continue
            violations.append({
                'scope': sid, 'kind': sc.get('kind'), 'label': sc.get('label'),
                'reasons': sorted({r for d in dets for r in d['reasons']})[:6],
                'expressions': sorted({e for d in dets for e in d['expressions']})[:6],
            })
        return {'ok': not violations, 'sim_ok': _scope_ok(scope_id),
                'violations': violations, 'checked_scopes': len(targets)}


def close_scope(scope_id: str) -> dict:
    """关闭作用域并递归校验 ✓ 返回校验结果"""
    res = check_scope(scope_id, recursive=True)
    with _LOCK:
        sc = _SCOPES.get(scope_id)
        if sc is not None:
            sc['closed'] = True
            sc['closed_at'] = _now()
            sc['result'] = {'ok': res['ok'], 'violations': res['violations']}
    return res


def list_scopes() -> list:
    with _LOCK:
        return [{k: v for k, v in sc.items() if k != 'llm_outputs'} for sc in _SCOPES.values()]


def reset() -> None:
    with _LOCK:
        _SCOPES.clear()


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------
def detect_compute(text: str) -> dict:
    """判断一段文本是否"主张了计算结果" ✓
    返回 {needed, reasons[], expressions[]}
    """
    t = str(text or '')
    reasons, exprs = [], []
    if _COMPUTE_VERBS.search(t):
        reasons.append('含计算意图词')
    for m in _EXPR_RE.finditer(t):
        e = m.group(0).strip()
        if any(op in e for op in ('+', '-', '*', '/', '×', '÷', '^', '%', '＋', '－', '＊', '／')):
            ne = _norm_expr(e)
            if _is_expr(ne):                      # ★ 排除日期/版本/时间 ✓
                exprs.append(ne)
    if exprs:
        reasons.append('含算式')
    # 只有"结果数"且没有算式 ⇒ 也可能是心算 ✓（例如"通过率为 62.16%"）
    if not exprs and reasons and _RESULT_RE.search(t):
        reasons.append('含结果数值')
    needed = bool(reasons) and (bool(exprs) or bool(_RESULT_RE.search(t)) or '含计算意图词' in reasons)
    return {'needed': needed, 'reasons': sorted(set(reasons)), 'expressions': sorted(set(exprs))[:10]}


def _norm_expr(e: str) -> str:
    s = str(e).strip()
    s = (s.replace('×', '*').replace('÷', '/').replace('（', '(').replace('）', ')')
          .replace('＋', '+').replace('－', '-').replace('＊', '*').replace('／', '/'))
    s = re.sub(r'\s+', '', s)
    return s


def extract_expressions(text: str) -> list:
    """抽出文本里可复算的算式 ✓"""
    out = []
    for m in _EXPR_RE.finditer(str(text or '')):
        e = _norm_expr(m.group(0))
        if re.search(r'[+\-*/^%]', e) and re.search(r'\d', e) and _is_expr(e):
            out.append(e)
    return list(dict.fromkeys(out))[:10]


# ---------------------------------------------------------------------------
# 强制修复：把算式拿去真的算 ✓ 替换文本里的数字 ✓
# ---------------------------------------------------------------------------
def _find_eval_node(expr: str) -> str | None:
    """动态挑节点 ✓（不写死 ✓）：能算表达式的节点（优先 math_eval ✓）"""
    import sim_nodes
    names = [x['name'] for x in sim_nodes.list_sim_nodes()]
    for cand in ('math_eval', 'expression', 'calculator', 'calc'):
        if cand in names:
            return cand
    for n in names:
        keys = set((sim_nodes.get_sim_node(n) or {}).get('params') or {})
        if keys & {'expr', 'expression'}:
            return n
    return None


def repair(text: str, scope_id: str | None = None, max_fixes: int = 8) -> dict:
    """把文本里的算式用仿真节点真算一遍 ✓ 并把"= 数字/≈ 数字"替换成精确值 ✓

    返回 {repaired, ok, fixes:[{expr, value, display, replaced}], sim_node, note}
    """
    import sim_nodes
    t = str(text or '')
    exprs = extract_expressions(t)
    if not exprs:
        return {'repaired': t, 'ok': True, 'fixes': [], 'sim_node': None,
                'note': '没有可复算的算式（无需修复 ✓）'}
    node = _find_eval_node(exprs[0])
    if not node:
        return {'repaired': t, 'ok': False, 'fixes': [], 'sim_node': None,
                'note': '注册表里没有能算表达式的仿真节点 ✗'}
    fixes, out = [], t
    for expr in exprs[:max_fixes]:
        r = sim_nodes.run_sim_node(node, {'expr': expr})
        if not r.get('ok'):
            fixes.append({'expr': expr, 'error': r.get('error')})
            continue
        res = r.get('result') or {}
        val = res.get('value')
        if val is None:
            continue
        display = res.get('display') or f'{val:.10g}'
        fixes.append({'expr': expr, 'value': val, 'display': display, 'node': node})
        if scope_id:
            record_sim_use(scope_id, node, True)
        # 只替换"紧跟在该算式后面"的结果数 ✓（避免误改别处 ✗）
        pat = re.compile(re.escape(expr).replace(r'\ ', r'\s*') + r'\s*(?:≈|≃|=|等于)\s*([\d.]+)')
        m = pat.search(out)
        if m:
            out = out[:m.start(1)] + str(display) + out[m.end(1):]
            fixes[-1]['replaced'] = True
    ok = all('error' not in f for f in fixes) if fixes else True
    return {'repaired': out, 'ok': ok, 'fixes': fixes, 'sim_node': node,
            'note': f'用 {node} 复算了 {len([f for f in fixes if "value" in f])} 个算式 ✓'}


def guard_text(text: str, scope_id: str | None = None) -> dict:
    """一步到位：检测 → 需要且没算过 ⇒ 修复 ✓ 返回 {needed, repaired, check}"""
    det = detect_compute(text)
    if not det['needed']:
        return {'needed': False, 'repaired': text, 'check': None}
    check = check_scope(scope_id, text=text) if scope_id else None
    if check and check.get('sim_ok'):
        return {'needed': True, 'repaired': text, 'check': check,
                'note': '本作用域或子代理已经用过仿真节点 ✓ 无需修复'}
    rep = repair(text, scope_id=scope_id)
    return {'needed': True, 'repaired': rep['repaired'], 'check': check or rep.get('check'),
            'repair': rep, 'note': rep.get('note')}
