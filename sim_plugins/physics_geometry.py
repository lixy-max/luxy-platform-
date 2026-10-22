#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仿真节点插件: 物理公式 + 几何（2026-10-21）

★ 用户想法: "更多仿真加入可以让模型拥有更准确的物理世界判断"
★ 设计: 每个公式都是**教科书标准式** ✓ 常数用 CODATA/SI 定义值 ✓
★ ★ 重要: 公式错了比幻觉更糟 ✗（"自信地错"✗）⇒ 每一条都要能溯源 ✓
"""

# ══════════════════════════════════════════════════════════
# 物理常数（SI 定义值 ✓）
# ══════════════════════════════════════════════════════════
G = 9.80665                 # 标准重力加速度 m/s²（ISO 80000-3 定义值 ✓）
C = 299792458.0             # 光速 m/s（SI 定义值 ✓）
GRAV = 6.67430e-11          # 引力常数 m³/(kg·s²)（CODATA 2018 ✓）
AVOGADRO = 6.02214076e23    # 阿伏伽德罗常数 mol⁻¹（SI 定义值 ✓）
BOLTZMANN = 1.380649e-23    # 玻尔兹曼常数 J/K（SI 定义值 ✓）
R_GAS = 8.314462618         # 气体常数 J/(mol·K) ✓


def _num(args: dict, key: str, default=None) -> float:
    v = args.get(key, default)
    if v is None:
        raise ValueError('缺少参数 %s ✓' % key)
    try:
        return float(v)
    except Exception:
        raise ValueError('参数 %s 必须是数字（拿到 %r）✗' % (key, v))


# ══════════════════════════════════════════════════════════
# ① 运动学 + 动力学 ✓
# ══════════════════════════════════════════════════════════
def physics_calc(args: dict) -> dict:
    """物理公式计算器 ✓（按 formula 名分发 ✓）"""
    f = str(args.get('formula') or '').strip().lower()
    if not f:
        raise ValueError(
            '缺少 formula ✓ 可用: %s' % ', '.join(sorted([
                'speed', 'acceleration', 'force', 'work', 'kinetic_energy',
                'potential_energy', 'power', 'density', 'pressure',
                'buoyancy', 'ohm', 'momentum', 'centripetal_acceleration',
                'free_fall_time', 'projectile_range',
            ])))

    # 速度 = 距离 / 时间 ✓
    if f in ('speed', 'velocity', 'v_equal_s_over_t'):
        s, t = _num(args, 'distance'), _num(args, 'time')
        if t == 0:
            raise ValueError('时间不能为 0 ✗')
        return {'formula': 'v = s / t', 'value': s / t, 'unit': 'm/s',
                'note': '平均速度 ✓ 非瞬时 ✓'}

    # 加速度 = 速度变化 / 时间 ✓
    if f in ('acceleration', 'accel'):
        v0, v1, t = _num(args, 'v0', 0.0), _num(args, 'v1', None), _num(args, 'time')
        if t == 0:
            raise ValueError('时间不能为 0 ✗')
        return {'formula': 'a = (v1 − v0) / t', 'value': (v1 - v0) / t, 'unit': 'm/s²'}

    # 牛顿第二定律 F = m·a ✓
    if f in ('force', 'newton_second'):
        m, a = _num(args, 'mass'), _num(args, 'acceleration')
        return {'formula': 'F = m · a', 'value': m * a, 'unit': 'N',
                'note': '合外力 ✓ 方向同加速度 ✓'}

    # 功 W = F·s·cosθ ✓
    if f in ('work',):
        F, s = _num(args, 'force'), _num(args, 'distance')
        import math
        theta = float(args.get('angle_deg', 0.0) or 0.0)
        return {'formula': 'W = F · s · cosθ', 'value': F * s * math.cos(math.radians(theta)),
                'unit': 'J', 'theta_deg': theta,
                'note': 'θ=0 时退化为 W=F·s ✓'}

    # 动能 KE = ½mv² ✓
    if f in ('kinetic_energy', 'ke'):
        m, v = _num(args, 'mass'), _num(args, 'speed')
        return {'formula': 'KE = ½ · m · v²', 'value': 0.5 * m * v * v, 'unit': 'J'}

    # 重力势能 PE = m·g·h ✓
    if f in ('potential_energy', 'pe', 'gravitational_potential_energy'):
        m = _num(args, 'mass')
        h = _num(args, 'height')
        g = float(args.get('g', G) or G)
        return {'formula': 'PE = m · g · h', 'value': m * g * h, 'unit': 'J',
                'g': g, 'note': 'g 默认 %.5f（标准重力 ✓）' % G}

    # 功率 P = W / t ✓
    if f in ('power',):
        W = _num(args, 'work')
        t = _num(args, 'time')
        if t == 0:
            raise ValueError('时间不能为 0 ✗')
        return {'formula': 'P = W / t', 'value': W / t, 'unit': 'W'}

    # 密度 ρ = m / V ✓
    if f in ('density',):
        m, V = _num(args, 'mass'), _num(args, 'volume')
        if V == 0:
            raise ValueError('体积不能为 0 ✗')
        return {'formula': 'ρ = m / V', 'value': m / V, 'unit': 'kg/m³'}

    # 压强 p = F / A ✓
    if f in ('pressure',):
        F, A = _num(args, 'force'), _num(args, 'area')
        if A == 0:
            raise ValueError('面积不能为 0 ✗')
        return {'formula': 'p = F / A', 'value': F / A, 'unit': 'Pa'}

    # 浮力 F = ρ_fluid · g · V_displaced ✓（阿基米德原理 ✓）
    if f in ('buoyancy', 'archimedes'):
        rho = _num(args, 'fluid_density')
        V = _num(args, 'displaced_volume')
        g = float(args.get('g', G) or G)
        return {'formula': 'F_buoy = ρ_fluid · g · V_displaced',
                'value': rho * g * V, 'unit': 'N', 'g': g,
                'note': '阿基米德原理 ✓ 等于排开流体的重力 ✓'}

    # 欧姆定律 U = I·R ✓ / P = U·I ✓
    if f in ('ohm', 'ohms_law'):
        I, R = _num(args, 'current'), _num(args, 'resistance')
        U = I * R
        return {'formula': 'U = I · R', 'value': U, 'unit': 'V',
                'power': U * I, 'power_unit': 'W',
                'note': '同时给出 P = U·I ✓'}

    # 动量 p = m·v ✓
    if f in ('momentum',):
        m, v = _num(args, 'mass'), _num(args, 'speed')
        return {'formula': 'p = m · v', 'value': m * v, 'unit': 'kg·m/s'}

    # 向心加速度 a = v² / r ✓
    if f in ('centripetal_acceleration', 'centripetal'):
        v, r = _num(args, 'speed'), _num(args, 'radius')
        if r == 0:
            raise ValueError('半径不能为 0 ✗')
        return {'formula': 'a = v² / r', 'value': v * v / r, 'unit': 'm/s²'}

    # 自由落体时间 t = sqrt(2h/g) ✓
    if f in ('free_fall_time',):
        h = _num(args, 'height')
        g = float(args.get('g', G) or G)
        import math
        if h < 0:
            raise ValueError('高度不能为负 ✗')
        return {'formula': 't = √(2h / g)', 'value': math.sqrt(2.0 * h / g),
                'unit': 's', 'g': g, 'note': '初速为 0 ✓ 忽略空气阻力 ✓'}

    # 抛体射程 R = v²·sin(2θ)/g ✓
    if f in ('projectile_range',):
        v = _num(args, 'speed')
        import math
        theta = float(args.get('angle_deg', 45.0) or 45.0)
        g = float(args.get('g', G) or G)
        return {'formula': 'R = v² · sin(2θ) / g',
                'value': v * v * math.sin(math.radians(2 * theta)) / g,
                'unit': 'm', 'theta_deg': theta, 'g': g,
                'note': '同一高度起落 ✓ 忽略空气阻力 ✓'}

    raise ValueError('未知公式 %r ✗ 可用: speed/acceleration/force/work/'
                     'kinetic_energy/potential_energy/power/density/pressure/'
                     'buoyancy/ohm/momentum/centripetal_acceleration/'
                     'free_fall_time/projectile_range' % f)


# ══════════════════════════════════════════════════════════
# ② 几何 ✓
# ══════════════════════════════════════════════════════════
def geometry_calc(args: dict) -> dict:
    """几何计算 ✓"""
    import math
    shape = str(args.get('shape') or '').strip().lower()
    PI = math.pi
    if not shape:
        raise ValueError('缺少 shape ✓ 可用: circle/rectangle/triangle/sphere/cylinder/cone/'
                         'trapezoid/parallelogram')

    if shape in ('circle', '圆'):
        r = _num(args, 'radius')
        return {'shape': 'circle', 'circumference': 2 * PI * r, 'area': PI * r * r,
                'formulas': ['C = 2πr', 'A = πr²'], 'radius': r}

    if shape in ('rectangle', 'rect', '矩形', '长方形'):
        w, h = _num(args, 'width'), _num(args, 'height')
        return {'shape': 'rectangle', 'perimeter': 2 * (w + h), 'area': w * h,
                'diagonal': math.hypot(w, h),
                'formulas': ['P = 2(w+h)', 'A = w·h', 'd = √(w²+h²)']}

    if shape in ('triangle', '三角形'):
        b, h = _num(args, 'base'), _num(args, 'height')
        out = {'shape': 'triangle', 'area': 0.5 * b * h,
               'formulas': ['A = ½·b·h']}
        # 可选: 三边求周长 + 海伦公式 ✓
        a_ = args.get('a'); b_ = args.get('b'); c_ = args.get('c')
        if a_ is not None and b_ is not None and c_ is not None:
            a_, b_, c_ = float(a_), float(b_), float(c_)
            if a_ + b_ > c_ and a_ + c_ > b_ and b_ + c_ > a_:
                s = (a_ + b_ + c_) / 2
                out['perimeter'] = a_ + b_ + c_
                out['area_heron'] = math.sqrt(max(0.0, s * (s - a_) * (s - b_) * (s - c_)))
                out['formulas'].append('A = √(s(s−a)(s−b)(s−c))（海伦 ✓）')
            else:
                out['warning'] = '三边不构成三角形 ✗（两边之和必须大于第三边 ✓）'
        return out

    if shape in ('sphere', '球'):
        r = _num(args, 'radius')
        return {'shape': 'sphere', 'volume': 4.0 / 3.0 * PI * r ** 3,
                'surface_area': 4 * PI * r * r,
                'formulas': ['V = 4/3·πr³', 'A = 4πr²'], 'radius': r}

    if shape in ('cylinder', '圆柱'):
        r = _num(args, 'radius')
        h = _num(args, 'height')
        return {'shape': 'cylinder', 'volume': PI * r * r * h,
                'lateral_area': 2 * PI * r * h,
                'total_area': 2 * PI * r * h + 2 * PI * r * r,
                'formulas': ['V = πr²h', '侧面积 = 2πrh', '全面积 = 2πrh + 2πr²']}

    if shape in ('cone', '圆锥'):
        r = _num(args, 'radius')
        h = _num(args, 'height')
        l = math.hypot(r, h)          # 母线 ✓
        return {'shape': 'cone', 'volume': PI * r * r * h / 3.0,
                'lateral_area': PI * r * l,
                'total_area': PI * r * l + PI * r * r,
                'slant_height': l,
                'formulas': ['V = 1/3·πr²h', 'l = √(r²+h²)', '侧面积 = πrl']}

    if shape in ('trapezoid', '梯形'):
        a_, b_, h = _num(args, 'base_a'), _num(args, 'base_b'), _num(args, 'height')
        return {'shape': 'trapezoid', 'area': 0.5 * (a_ + b_) * h,
                'formulas': ['A = ½(a+b)h']}

    if shape in ('parallelogram', '平行四边形'):
        b, h = _num(args, 'base'), _num(args, 'height')
        return {'shape': 'parallelogram', 'area': b * h, 'formulas': ['A = b·h']}

    raise ValueError('未知形状 %r ✗ 可用: circle/rectangle/triangle/sphere/cylinder/cone/'
                     'trapezoid/parallelogram' % shape)


def register(reg):
    reg('physics_calc',
        label='物理公式计算',
        description=('按标准物理公式确定性计算 ✓ 支持: 速度/加速度/牛顿第二定律/功/动能/'
                     '重力势能/功率/密度/压强/浮力(阿基米德)/欧姆定律/动量/向心加速度/'
                     '自由落体时间/抛体射程。'
                     '★ 物理量计算必须走这里 ✗ 禁止心算 ✗ 常数用 SI 定义值 ✓'),
        params={'formula': 'string（公式名 ✓ 如 force）',
                'mass': 'number · distance: number · time: number · speed: number · '
                        'acceleration: number · force: number · work: number · '
                        'volume: number · area: number · height: number · '
                        'radius: number · fluid_density: number · '
                        'displaced_volume: number · current: number · '
                        'resistance: number · v0/v1: number · angle_deg: number'},
        handler=physics_calc,
        tags=['math', 'physics'])

    reg('geometry_calc',
        label='几何计算',
        description=('确定性几何计算 ✓ 支持: 圆/矩形/三角形(含海伦公式)/球/圆柱/圆锥/'
                     '梯形/平行四边形 的周长/面积/体积/表面积/对角线/母线。'
                     '★ 几何计算必须走这里 ✗ 禁止心算 ✗'),
        params={'shape': 'string（形状名 ✓ 如 circle）',
                'radius': 'number · width: number · height: number · base: number · '
                          'base_a/base_b: number · a/b/c: number（三边 ✓）'},
        handler=geometry_calc,
        tags=['math', 'geometry'])
