#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局仿真节点注册表（Global Simulation Nodes）

## 目的
任何 agent（对话/agent 模式/工作流）需要【计算】时，必须调用仿真节点，禁止心算 ✗
⇒ 消除数值幻觉 ✓ 所有数字都可复算、可追溯 ✓

## 为什么叫"仿真节点"
它把"计算"从模型脑子里搬到**确定性的执行器**里 ✓
每个节点 = 一个纯函数式的确定性算子（同输入必得同输出 ✓ 不做采样 ✓ 不调模型 ✓）
所以它更像"仿真/模拟"而不是"推理" ✓

## 可插拔接口（给后续扩展留的 ✓）
新增一个仿真节点只需两步:
  1. 写一个 handler:  def my_node(args: dict) -> dict  （入参/出参都必须是 JSON 可序列化 ✓）
  2. 注册:
        from sim_nodes import register_sim_node
        register_sim_node(
            'my_node',                       # 唯一名字（建议全小写+下划线 ✓）
            label='我的仿真节点',
            description='做什么（会作为工具说明发给模型 ✓ 要写清"能算什么"）',
            params={'x': 'number', 'y': 'number'},   # 参数名 → 类型/说明
            handler=my_node,
            tags=['math'],                   # 可选分类
        )
  也可以把 .py 放进 `sim_plugins/` 目录 ✓ 里面定义 `def register(reg)` ✓ 启动时自动加载 ✓
  例:
        # sim_plugins/units.py
        def register(reg):
            reg('unit_convert', label='单位换算', description='把长度/重量换算',
                params={'value': 'number', 'from': 'string', 'to': 'string'},
                handler=lambda a: {'value': a['value'] * 1000})

## 安全约束（必须守住 ✓）
- math_eval 走 **AST 白名单**，绝不 eval 任意代码 ✗（无 import / 无属性访问 / 无下标 / 无 lambda ✓）
- 所有节点都有入参上限（长度/个数 ✓）与结果有限性检查（拒绝 inf/nan ✓）
- 节点不允许有副作用（不读文件/不联网/不改全局状态 ✓）
"""
from __future__ import annotations

import ast
import json
import math
import pathlib
import sys
from typing import Any, Callable

__all__ = [
    'SIM_NODES', 'register_sim_node', 'list_sim_nodes', 'get_sim_node',
    'run_sim_node', 'load_plugin_dir', 'SIM_PLUGIN_DIR',
]

SIM_PLUGIN_DIR = pathlib.Path(__file__).with_name('sim_plugins')

# name -> 元数据（含 handler ✓ handler 不出现在对外 JSON 里 ✓）
SIM_NODES: dict[str, dict] = {}

# 单个节点的安全上限
MAX_EXPR_LEN = 300
MAX_STATS_N = 100_000
MAX_ABS_VALUE = 1e18


def register_sim_node(name: str, *, label: str = '', description: str = '',
                      params: dict | None = None,
                      handler: Callable[[dict], dict] | None = None,
                      tags: list | None = None,
                      overwrite: bool = False) -> dict:
    """注册一个仿真节点 ✓ 返回它的元数据（不含 handler ✗）"""
    nm = str(name or '').strip()
    if not nm or not nm.replace('_', '').replace('-', '').isalnum():
        raise ValueError('仿真节点名只能包含字母/数字/下划线/中划线')
    if not callable(handler):
        raise ValueError(f'仿真节点 {nm} 缺少可调用的 handler')
    if nm in SIM_NODES and not overwrite:
        raise ValueError(f'仿真节点 {nm} 已存在（如需替换请 overwrite=True）')
    meta = {
        'name': nm,
        'label': label or nm,
        'description': description or label or nm,
        'params': dict(params or {}),
        'tags': list(tags or []),
        'handler': handler,
    }
    SIM_NODES[nm] = meta
    return public_meta(meta)


def public_meta(meta: dict) -> dict:
    """对外暴露的元数据（去掉 handler ✓ 不可序列化且不该外泄 ✓）"""
    return {k: v for k, v in meta.items() if k != 'handler'}


def list_sim_nodes(tag: str | None = None) -> list:
    out = []
    for nm in sorted(SIM_NODES):
        meta = SIM_NODES[nm]
        if tag and tag not in (meta.get('tags') or []):
            continue
        out.append(public_meta(meta))
    return out


def get_sim_node(name: str) -> dict | None:
    return SIM_NODES.get(str(name or '').strip())


def run_sim_node(name: str, args: dict | None = None) -> dict:
    """执行一个仿真节点 ✓ 统一返回 {ok, node, result} 或 {ok: False, error} ✓"""
    nm = str(name or '').strip()
    meta = SIM_NODES.get(nm)
    if not meta:
        return {'ok': False, 'node': nm,
                'error': f'未知仿真节点 {nm!r}，可用: {", ".join(sorted(SIM_NODES))}'}
    payload = dict(args or {})
    try:
        result = meta['handler'](payload)
    except Exception as e:                      # 节点内部错误 ⇒ 如实回报 ✓ 不抛 500
        return {'ok': False, 'node': nm,
                'error': f'{type(e).__name__}: {e}',
                # ★ 把该节点的正确参数形状一起返回 ✓ 模型可据此自我纠正 ✓
                'hint_params': dict(meta.get('params') or {}),
                'hint_usage': '请按 hint_params 传 args；也可以用 math_eval 传 {"expr": "..."} ✓'}
    if not isinstance(result, dict):
        result = {'value': result}
    # 结果必须 JSON 可序列化 ✓ 且不得含 inf/nan（前端 JSON.parse 会炸 ✗）
    _assert_finite(result)
    return {'ok': True, 'node': nm, 'label': meta.get('label') or nm, 'result': result}


def _assert_finite(obj, path=''):
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f'结果含非法数值（inf/nan）: {path or "result"}')
        if abs(obj) > MAX_ABS_VALUE:
            raise ValueError(f'结果超出安全范围（|x|>{MAX_ABS_VALUE:g}）: {path or "result"}')
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_finite(v, f'{path}.{k}' if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_finite(v, f'{path}[{i}]')
    elif isinstance(obj, (str, int, bool)) or obj is None:
        return
    else:
        raise ValueError(f'结果含不可序列化类型 {type(obj).__name__}: {path or "result"}')


# ---------------------------------------------------------------------------
# 内置节点 1: math_eval（安全表达式求值 ✓ AST 白名单）
# ---------------------------------------------------------------------------
_ALLOWED_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_ALLOWED_FUNCS = {
    'abs': (abs, 1, 1), 'round': (round, 1, 2), 'min': (min, 1, 16),
    'max': (max, 1, 16), 'sqrt': (math.sqrt, 1, 1), 'log': (math.log, 1, 2),
    'exp': (math.exp, 1, 1), 'sin': (math.sin, 1, 1), 'cos': (math.cos, 1, 1),
    'tan': (math.tan, 1, 1), 'pow': (pow, 2, 2),
}
_ALLOWED_CONST = {'pi': math.pi, 'e': math.e, 'tau': math.tau}


def _eval_ast(node):
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError('只允许数字常量')
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CONST:
            return _ALLOWED_CONST[node.id]
        raise ValueError(f'不允许的标识符 {node.id!r}（只允许 pi/e/tau）')
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BIN):
            raise ValueError(f'不允许的运算符 {type(node.op).__name__}')
        a, b = _eval_ast(node.left), _eval_ast(node.right)
        if isinstance(node.op, ast.Pow):
            if isinstance(b, (int, float)) and abs(b) > 100:
                raise ValueError('指数过大（|exp| 必须 ≤ 100）')
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if b == 0:
                raise ValueError('除数为 0')
        return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b,
                ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                ast.FloorDiv: lambda: a // b, ast.Mod: lambda: a % b,
                ast.Pow: lambda: a ** b}[type(node.op)]()
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise ValueError(f'不允许的一元运算符 {type(node.op).__name__}')
        v = _eval_ast(node.operand)
        return +v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError('只允许调用 abs/round/min/max/sqrt/log/exp/sin/cos/tan/pow')
        fn, lo, hi = _ALLOWED_FUNCS[node.func.id]
        if node.keywords:
            raise ValueError('不允许关键字参数')
        if not (lo <= len(node.args) <= hi):
            raise ValueError(f'{node.func.id} 需要 {lo}~{hi} 个参数')
        return fn(*[_eval_ast(a) for a in node.args])
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_ast(e) for e in node.elts]
    raise ValueError(f'不允许的语法 {type(node).__name__}')


def math_eval(args: dict) -> dict:
    """安全地算一个数学表达式 ✓ 例: {"expr": "(1+2)*3/sqrt(16)"} → 2.25 ✓"""
    expr = str(((args or {}).get('expr') or '')).strip()
    if not expr:
        raise ValueError('缺少 expr（要计算的表达式）')
    if len(expr) > MAX_EXPR_LEN:
        raise ValueError(f'表达式过长（>{MAX_EXPR_LEN} 字符）')
    tree = ast.parse(expr, mode='eval')
    value = _eval_ast(tree)
    if isinstance(value, list):
        return {'expr': expr, 'values': [float(v) for v in value]}
    v = float(value)
    if not math.isfinite(v):
        raise ValueError('结果不是有限数')
    return {'expr': expr, 'value': v,
            'display': (f'{v:.10g}' if not float(v).is_integer() else f'{int(v)}')}


# ---------------------------------------------------------------------------
# 内置节点 2: stats（描述统计 ✓ 全部可复算）
# ---------------------------------------------------------------------------
def _to_numbers(values: Any) -> list:
    if isinstance(values, str):
        raw = values.replace('，', ',').replace(';', ',').replace(' ', ',')
        values = [x for x in raw.split(',') if x.strip()]
    if not isinstance(values, (list, tuple)):
        raise ValueError('values 必须是数字列表（或逗号分隔的字符串）')
    out = []
    for x in values:
        if isinstance(x, bool):
            raise ValueError('values 里不允许布尔值')
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            raise ValueError(f'values 里有非数字: {x!r}')
    if not out:
        raise ValueError('values 为空')
    if len(out) > MAX_STATS_N:
        raise ValueError(f'values 太多（>{MAX_STATS_N}）')
    if any(not math.isfinite(v) for v in out):
        raise ValueError('values 里有 inf/nan')
    return out


def _percentile(sorted_vals: list, p: float) -> float:
    """线性插值分位数 ✓（与 numpy 默认 linear 一致 ✓ 便于复算）"""
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sorted_vals[int(k)])
    frac = k - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def stats(args: dict) -> dict:
    """描述统计 ✓ 例: {"values":[1,2,3,4]} → mean=2.5 median=2.5 stdev=1.29… ✓"""
    vals = _to_numbers((args or {}).get('values'))
    s = sorted(vals)
    n = len(vals)
    total = math.fsum(vals)                     # fsum 比 sum 更精确 ✓ 可复算 ✓
    mean = total / n
    var = math.fsum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    return {
        'count': n, 'sum': total, 'mean': mean, 'median': _percentile(s, 50),
        'min': s[0], 'max': s[-1], 'stdev': math.sqrt(var) if n > 1 else 0.0,
        'var': var, 'p25': _percentile(s, 25), 'p75': _percentile(s, 75),
        'p90': _percentile(s, 90),
    }


# ---------------------------------------------------------------------------
# 内置节点 3: ratio（占比/比率/增长率 ✓ 注意"百分点"与"百分比增长"不同 ✓）
# ---------------------------------------------------------------------------
def ratio(args: dict) -> dict:
    """比率计算 ✓ {"a":30,"b":200} → percent=15（表示 a 占 b 的 15%）✓

    容错（实测: 模型会给 ratio 传 {"expr": "(23/37)*100"} ✗）:
      没有 a/b 但有 expr ⇒ 直接把表达式当"百分比"算出来 ✓（模型少一次试错 ✓）
    """
    args = args or {}
    if (args.get('a') is None or args.get('b') is None) and args.get('expr'):
        expr = str(args.get('expr')).strip()
        val = float(_eval_ast(ast.parse(expr, mode='eval')))
        return {'expr': expr, 'percent': val, 'rounded': round(val, 6),
                'note': '按 expr 直接求值（未走 a/b 形式 ✓）'}
    if args.get('a') is None or args.get('b') is None:
        raise ValueError('ratio 需要 a 与 b（如 {"a":23,"b":37}）；若只有表达式请用 math_eval')
    a = float(args.get('a'))
    b = float(args.get('b'))
    digits = int((args or {}).get('digits') or 4)
    if b == 0:
        raise ValueError('b 不能为 0')
    pct = a / b * 100.0
    return {'a': a, 'b': b, 'percent': pct,
            'rounded': round(pct, max(0, min(10, digits))),
            'ratio': a / b}


# ---------------------------------------------------------------------------
# 插件加载（给后续扩展留的接口 ✓）
# ---------------------------------------------------------------------------
def load_plugin_dir(dirpath=None) -> list:
    """扫描插件目录 ✓ 每个 .py 里应有 `def register(reg)` ✓ reg 就是 register_sim_node ✓"""
    import importlib.util

    d = pathlib.Path(dirpath or SIM_PLUGIN_DIR)
    loaded, errors = [], []
    if not d.exists():
        return []
    sys.path.insert(0, str(d))
    try:
        for f in sorted(d.glob('*.py')):
            if f.name.startswith('_'):
                continue
            modname = f'sim_plugin_{f.stem}'
            try:
                spec = importlib.util.spec_from_file_location(modname, f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, 'register'):
                    errors.append(f'{f.name}: 缺少 register(reg) 函数')
                    continue
                mod.register(register_sim_node)
                loaded.append(f.name)
            except Exception as e:
                errors.append(f'{f.name}: {type(e).__name__}: {e}')
    finally:
        try:
            sys.path.remove(str(d))
        except ValueError:
            pass
    return [{'file': x} for x in loaded] + [{'file': e, 'error': True} for e in errors]


# ---------------------------------------------------------------------------
# 内置注册（import 时完成 ✓）
# ---------------------------------------------------------------------------
register_sim_node(
    'math_eval',
    label='数值表达式求值',
    description='安全地计算一个数学表达式（+ - * / // % ** 与 abs/round/min/max/sqrt/log/exp/sin/cos/tan/pow，常量 pi/e/tau）',
    params={'expr': 'string · 要计算的表达式，如 "(1+2)*3/sqrt(16)"'},
    handler=math_eval, tags=['math'],
)
register_sim_node(
    'stats',
    label='描述统计',
    description='对一串数字算 count/sum/mean/median/min/max/stdev/var/p25/p75/p90（线性插值分位数 ✓ 可复算）',
    params={'values': 'number[] | string · 数字列表，或逗号分隔字符串 "1,2,3"'},
    handler=stats, tags=['stats'],
)
register_sim_node(
    'ratio',
    label='占比/比率',
    description='算 a 占 b 的百分比与比率（a/b*100 ✓ b 不可为 0 ✓）',
    params={'a': 'number · 分子', 'b': 'number · 分母', 'digits': 'number · 百分比保留几位小数（默认 4）'},
    handler=ratio, tags=['math', 'stats'],
)
load_plugin_dir()
