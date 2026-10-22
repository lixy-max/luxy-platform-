#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仿真节点插件: 单位换算（2026-10-21）

★ 用户想法: "用绝对公式约束模型边界" ✓
   单位换算 = 纯确定性 ✓ 有精确换算因子 ✓ 模型心算极易错 ✗
★ 设计: 先转成 SI 基准单位 → 再转到目标单位 ✓（两步法减少错误 ✓）
★ 温度是仿射变换（不是比例 ✗）⇒ 单独处理 ✓
"""

# ══════════════════════════════════════════════════════════
# 换算表: 单位名 → 到 SI 基准的因子（乘以它 = 变SI ✓）
# ══════════════════════════════════════════════════════════
LENGTH = {  # SI 基准: m
    'mm': 0.001, 'cm': 0.01, 'dm': 0.1, 'm': 1.0, 'km': 1000.0,
    'um': 1e-6, 'nm': 1e-9,
    'inch': 0.0254, 'in': 0.0254, 'ft': 0.3048, 'foot': 0.3048, 'feet': 0.3048,
    'yd': 0.9144, 'mile': 1609.344, 'mi': 1609.344,
    'nmi': 1852.0, 'ly': 9.4607304725808e15,
}
MASS = {  # SI 基准: kg
    'mg': 1e-6, 'g': 0.001, 'kg': 1.0, 't': 1000.0, 'ton': 1000.0,
    'lb': 0.45359237, 'lbs': 0.45359237, 'oz': 0.028349523125,
    'jin': 0.5, 'liang': 0.05,          # 市斤/市两 ✓
}
AREA = {  # SI 基准: m²
    'mm2': 1e-6, 'cm2': 1e-4, 'm2': 1.0, 'km2': 1e6,
    'ha': 1e4, 'hectare': 1e4,
    'acre': 4046.8564224, 'mu': 666.6666666666667,   # 亩 ✓
    'ft2': 0.09290304, 'in2': 0.00064516,
}
VOLUME = {  # SI 基准: m³
    'ml': 1e-6, 'l': 0.001, 'm3': 1.0,
    'cm3': 1e-6, 'dm3': 0.001,
    'gal_us': 0.003785411784, 'gal_uk': 0.00454609,
    'ft3': 0.028316846592, 'in3': 1.6387064e-5,
}
SPEED = {  # SI 基准: m/s
    'm/s': 1.0, 'mps': 1.0, 'km/h': 1 / 3.6, 'kph': 1 / 3.6,
    'mph': 0.44704, 'knot': 0.5144444444444444, 'kt': 0.5144444444444444,
}
PRESSURE = {  # SI 基准: Pa
    'pa': 1.0, 'kpa': 1000.0, 'mpa': 1e6, 'gpa': 1e9,
    'bar': 1e5, 'mbar': 100.0, 'atm': 101325.0,
    'mmhg': 133.322387415, 'torr': 133.32236842105263,
    'psi': 6894.757293168,
}
ENERGY = {  # SI 基准: J
    'j': 1.0, 'kj': 1000.0, 'mj': 1e6,
    'cal': 4.184, 'kcal': 4184.0,
    'wh': 3600.0, 'kwh': 3.6e6,
    'ev': 1.602176634e-19,
}
POWER = {  # SI 基准: W
    'w': 1.0, 'kw': 1000.0, 'mw': 1e6,
    'hp': 745.6998715822702, 'ps': 735.49875,   # 英制马力 / 公制马力 ✓
}
TIME = {  # SI 基准: s
    'ms': 0.001, 's': 1.0, 'sec': 1.0, 'min': 60.0, 'h': 3600.0,
    'hour': 3600.0, 'd': 86400.0, 'day': 86400.0, 'week': 604800.0,
    'year': 31557600.0,     # 365.25 天 ✓
}
DATA = {  # SI 基准: byte
    'b': 1.0, 'byte': 1.0, 'kb': 1024.0, 'mb': 1024.0 ** 2,
    'gb': 1024.0 ** 3, 'tb': 1024.0 ** 4,
    'kib': 1024.0, 'mib': 1024.0 ** 2, 'gib': 1024.0 ** 3,
}

DIMENSIONS = {
    'length': ('长度', LENGTH),
    'mass': ('质量', MASS),
    'area': ('面积', AREA),
    'volume': ('体积', VOLUME),
    'speed': ('速度', SPEED),
    'pressure': ('压力/压强', PRESSURE),
    'energy': ('能量', ENERGY),
    'power': ('功率', POWER),
    'time': ('时间', TIME),
    'data': ('数据量', DATA),
}


# ══════════════════════════════════════════════════════════
# 温度（仿射变换 ✓ 不是比例 ✗）
# ══════════════════════════════════════════════════════════
def _temp_to_c(v: float, unit: str) -> float:
    u = unit.lower()
    if u in ('c', 'celsius', '℃', '摄氏度'):
        return v
    if u in ('f', 'fahrenheit', '℉', '华氏度'):
        return (v - 32.0) * 5.0 / 9.0
    if u in ('k', 'kelvin', '开尔文'):
        return v - 273.15
    raise ValueError('不支持的温度单位: %r（支持 c/f/k ✓）' % unit)


def _temp_from_c(c: float, unit: str) -> float:
    u = unit.lower()
    if u in ('c', 'celsius', '℃', '摄氏度'):
        return c
    if u in ('f', 'fahrenheit', '℉', '华氏度'):
        return c * 9.0 / 5.0 + 32.0
    if u in ('k', 'kelvin', '开尔文'):
        return c + 273.15
    raise ValueError('不支持的温度单位: %r（支持 c/f/k ✓）' % unit)


def unit_convert(args: dict) -> dict:
    """单位换算 ✓"""
    value = args.get('value')
    if value is None:
        raise ValueError('缺少 value ✓')
    try:
        v = float(value)
    except Exception:
        raise ValueError('value 必须是数字（拿到 %r）✗' % (value,))
    frm = str(args.get('from') or args.get('from_unit') or '').strip()
    to = str(args.get('to') or args.get('to_unit') or '').strip()
    dim = str(args.get('dimension') or '').strip().lower()
    if not frm or not to:
        raise ValueError('需要 from 和 to ✓（如 {"value":1,"from":"km","to":"m"}）')

    # —— 温度优先 ✓ ——
    t_units = ('c', 'celsius', '℃', '摄氏度', 'f', 'fahrenheit', '℉', '华氏度',
               'k', 'kelvin', '开尔文')
    if frm.lower() in t_units or to.lower() in t_units:
        c = _temp_to_c(v, frm)
        out = _temp_from_c(c, to)
        return {'value': out, 'from': frm, 'to': to, 'dimension': 'temperature',
                'formula': '%s → ℃ → %s（仿射变换 ✓ 非比例 ✗）' % (frm, to)}

    # —— 找维度 ✓（不给 dimension 就自动找 ✓）——
    cands = []
    if dim and dim in DIMENSIONS:
        cands = [(dim, DIMENSIONS[dim][1])]
    else:
        cands = [(k, tbl) for k, (_, tbl) in DIMENSIONS.items()]
    for dname, tbl in cands:
        f_ok = frm in tbl or frm.lower() in tbl
        t_ok = to in tbl or to.lower() in tbl
        if f_ok and t_ok:
            f_key = frm if frm in tbl else frm.lower()
            t_key = to if to in tbl else to.lower()
            base = v * tbl[f_key]
            out = base / tbl[t_key]
            return {
                'value': out,
                'from': frm, 'to': to, 'dimension': dname,
                'formula': '%g %s × %g = %g（SI基准）÷ %g = %g %s' % (
                    v, frm, tbl[f_key], base, tbl[t_key], out, to),
            }

    # —— 都找不到 ⇒ 给出所有支持的单位 ✓（可自我纠正 ✓）——
    supported = {d: sorted(t.keys()) for d, (_, t) in DIMENSIONS.items()}
    supported['temperature'] = ['c', 'f', 'k']
    raise ValueError('无法换算 %r → %r ✗ 可用单位: %s' % (
        frm, to, {k: v[:8] for k, v in supported.items()}))


def register(reg):
    reg('unit_convert',
        label='单位换算',
        description=('确定性单位换算 ✓ 长度/质量/面积/体积/速度/压力/能量/功率/时间/数据量/温度。'
                     '用法: {"value":100,"from":"km","to":"m"} ✓ '
                     '★ 任何单位换算必须走这里 ✗ 禁止心算 ✗'),
        params={'value': 'number（数值 ✓）',
                'from': 'string（源单位 ✓ 如 km）',
                'to': 'string（目标单位 ✓ 如 m）',
                'dimension': 'string 可选（length/mass/area/volume/speed/pressure/energy/power/time/data）'},
        handler=unit_convert,
        tags=['math', 'units', 'physics'])
