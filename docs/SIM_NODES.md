# 全局仿真节点（Global Simulation Nodes）

> 一句话：**任何 agent 需要「算」的时候，必须调用仿真节点，禁止心算** ✗
> 目的：把计算从模型脑子里搬到**确定性执行器**里 ⇒ 数值可复算、可追溯、无幻觉 ✓

---

## 一、为什么需要它

模型做算术/统计时常犯三类错 ✗：
```
① 算错但不自知（7B 更明显 ✓ 实测给它 23/37 它心算 62.16% 这次碰巧对 ✓ 换个数就飘 ✗）
② 编造中间数（"大约是 62.2%" 其实没算过 ✓）
③ 单位/百分点混淆（百分点 vs 百分比增长 ✗）
```
仿真节点把这类工作变成**纯函数** ✓：同输入必得同输出 ✓ 输出附带完整中间量 ✓ 可复算 ✓

## 二、强制机制（三层，缺一不可 ✓）

```
① 提示层（buildAgentSystemPrompt 的【计算纪律】）
   "任何计算/统计/换算必须先调 run_simulation，禁止心算；没经过仿真节点的数值不要写进答案"
② 工具层（/docs/tool/list = 唯一真源 ✓ 自动进 agent 工具表）
   list_sim_nodes · run_simulation
③ 闸门层（前端 agent 循环末尾的质量闸门 ④）
   用户话里有计算意图（计算/求和/平均/方差/百分比/占比/换算…）
   但本轮【没有成功执行过】仿真节点 ⇒ 判定不合格 ⇒ 触发云接手 ✓
   ★ 为什么是"成功执行过"而不是"尝试过": 实测模型传错参数失败后会转心算 ✗
     （它试了 ratio 但参数错 ⇒ 失败 ⇒ 自己心算了 62.16% ✗）⇒ 只看尝试过会被绕过 ✗
```

## 三、内置节点（3 个）

| 节点 | 干什么 | 主要参数 | 例 |
|---|---|---|---|
| `math_eval` | 安全算数学表达式（+ - * / // % **、abs/round/min/max/sqrt/log/exp/sin/cos/tan/pow、常量 pi/e/tau） | `expr` | `{"expr": "(1+2)*3/sqrt(16)"}` → 2.25 |
| `stats` | 描述统计：count/sum/mean/median/min/max/stdev/var/p25/p75/p90（线性插值 ✓ 可复算） | `values`（列表或 "1,2,3"） | `{"values":[1,2,3,4]}` → mean 2.5 / stdev 1.291 |
| `ratio` | 占比/比率：a 占 b 的百分比；也容错接收 `expr` | `a` `b` `digits`（或 `expr`） | `{"a":23,"b":37}` → 62.162% |

## 四、★ 怎么加新的仿真节点（给后续扩展留的接口 ✓）

**方式一：代码里注册（同进程 ✓ 适合内部节点）**
```python
from sim_nodes import register_sim_node

def unit_convert(args):
    table = {'km_m': 1000, 'kg_g': 1000, 'h_s': 3600}
    k = f"{args['from']}_{args['to']}"
    if k not in table:
        raise ValueError(f'不支持的换算 {k}，可用: {list(table)}')
    return {'value': float(args['value']) * table[k], 'rule': k}

register_sim_node(
    'unit_convert',
    label='单位换算',
    description='长度 km↔m / 重量 kg↔g / 时间 h↔s 换算',   # ★ 会发给模型 ✓ 写清"能算什么" ✓
    params={'value': 'number · 数值', 'from': 'string · 源单位', 'to': 'string · 目标单位'},
    handler=unit_convert,
    tags=['units'],
)
```

**方式二：插件文件（不改主代码 ✓ 推荐给"以后加的节点"）**
```python
# sim_plugins/units.py
def register(reg):           # 启动时自动加载 ✓ 函数名必须是 register ✓
    reg('unit_convert', label='单位换算', description='...',
        params={'value': 'number', 'from': 'string', 'to': 'string'},
        handler=lambda a: {'value': float(a['value']) * 1000})
```
> 说明：`sim_plugins/` 目录在启动时被扫描 ✓ 单个插件抛错只记录不拖垮启动 ✓

**新节点自动获得的能力**
```
✓ 出现在 GET /sim/nodes（前端/agent 都能看到 ✓）
✓ 出现在 /docs/tool/list ⇒ 自动进 agent 工具表（唯一真源 ✓ 无需改前端 ✗）
✓ 出现在 run_simulation 的工具说明里（模型会看到可用节点清单 ✓）
✓ 失败时自动返回 hint_params / hint_usage（模型可自我纠正 ✓）
✓ 入参长度/数值/可序列化检查（拒绝 inf/nan ✗）
```

## 五、安全约束（必须守住 ✓）

```
✓ math_eval 走 AST 白名单：无 import / 无属性访问 / 无下标 / 无 lambda / 无推导式
✓ 表达式 ≤ 300 字符 · 指数 ≤ 100 · 统计个数 ≤ 10 万
✓ 结果必须有限（拒 inf/nan ✗）且 |x| ≤ 1e18
✓ 节点必须无副作用（不读文件/不联网/不改全局状态 ✓）
✓ 节点内部异常 ⇒ 返回 {ok:false, error}（不抛 500 ✓ 让模型看到原因 ✓）
```

## 六、接口

```
GET  /sim/nodes[?tag=math]     → {"count":N,"nodes":[{name,label,description,params,tags}]}
POST /sim/run                  → {"ok":true,"node":..,"label":..,"result":{..}}
                                 或 {"ok":false,"error":..,"hint_params":{..},"hint_usage":..}
```
agent 侧两个工具：`list_sim_nodes`（看有哪些）· `run_simulation(node, args)`（执行 ✓）

## 七、测试

```
python -m unittest tests.test_sim_nodes -v
覆盖: 安全性(注入/属性/下标/lambda/除零/超长/超大指数 ✓)
      正确性(math_eval/stats/ratio ✓ 含 CSV 字符串 ✓)
      注册表(注册/重复/非法名/覆盖/public_meta 隐藏 handler ✓)
      插件(加载成功 ✓ 坏插件只报错不崩 ✓)
      接入(/sim/nodes · /sim/run · /docs/tool/list ✓)
```

---

# 九、递归计算守卫（Recursive Compute Guard · 2026-10-19）

> 仿真节点会算 ✓，但**强制**只做在一条路上（前端 agent 循环 ✗）—— 这不够 ✓
> 真实系统里 LLM 调用是多层的：主 agent → 工作流节点（= 子代理 ✓）→ 子节点 → 云端接手 …
> **任意一层**都可能心算 ✗ ⇒ 必须**递归**检测 + **动态**加载节点 + 全局强制 ✓

## 9.1 作用域树（scope tree）

```
turn(主 agent)
 ├─ subagent(工作流节点 A)   ← 子代理 ✓
 │    └─ subagent(节点 A.1)  ← 子代理的子代理 ✓
 └─ subagent(工作流节点 B)
```

- `POST /sim/guard/scope/open {parent?, kind, label}` → `scope_id`
- `POST /sim/guard/scope/close {scope_id}` → **递归校验整棵子树** ✓
- `POST /sim/guard/check {scope_id?, text}` → 只报不改 ✓
- `POST /sim/guard/repair {scope_id?, text}` → 检测 + **真算 + 替换数字** ✓
- `GET  /sim/guard/scopes` → 调试看树 ✓

## 9.2 递归语义（关键 ✓）

```
满足条件: 该作用域【自己】或【任一后代】成功用过仿真节点 ✓
  理由: 子代理算过了 ⇒ 父层不必重算 ✓（这就是"递归"✓）
违规: 某层的 LLM 输出里有计算意图/算式，且其子树里没人算过 ⇒ 违规 ✓ 并报出是哪一层 ✗
注意: "尝试过"不算 ✓ 必须成功（失败/参数错的调用不计入 ✓ 实测会被绕过 ✗）
```

## 9.3 强制修复（不是只报警 ✗）

`repair(text)` 会:
```
① 抽出文本里的算式（支持全角 ＋－＊／ ✓ 排除日期 2026-10-19 / 版本 v0.2.0 / 时间 14:30 ✓）
② 动态挑一个"能算表达式"的节点（从 sim_nodes 注册表现取 ✓ 不写死 ✓）
③ 真的算 ✓ 并把"= 21.14"替换成"= 22.28571429" ✓
```
⇒ 即使模型心算了，**最终交付的数字也是算过的** ✓（可复算 ✓ 可追溯 ✓）

## 9.4 接入点（所有 LLM 路径 ✓）

| 路径 | 覆盖方式 |
|---|---|
| 主 agent（`/chat/tools`） | 返回前 `guard_text` ✓（改用修复后的文本 ✓ 附 `sim_guard` 字段） |
| 工作流节点（= **子代理** ✓） | `workflow_engine.call_llm()` 三条 provider 分支统一包 `_sim_guard_wrap` ✓ |
| 云端接手 / 其它调用方 | 走 `call_llm`（同上 ✓）或自行调 `/sim/guard/*` ✓ |
| 前端 agent 工具闸门 | 已在（计算意图 + 没成功算过 ⇒ 判不合格 ⇒ 转云 ✓） |
| 流式 `/chat/completions/stream` | **已知边界** ✗ 流式改写风险高 ⇒ 由前端闸门兜 ✓（记录在案 ✓） |

## 9.5 动态加载

```
节点集合运行时从 sim_nodes.SIM_NODES 读取 ✓
新增节点: 注册一次（代码 register_sim_node 或 sim_plugins/*.py ✓）
   ⇒ /sim/nodes ✓ /docs/tool/list ✓ run_simulation 工具说明 ✓ 守卫挑节点 ✓ 全部自动生效 ✓
```

## 9.6 测试

```
python -m unittest tests.test_sim_guard -v   # 19 项 ✓
覆盖: 检测(算式/意图/全角/日期版本排除 ✓) · 修复(替换真值/多算式/无算式不动 ✓)
      递归(子算过父满足 ✓ 都没算违规 ✓ 三层 ✓ 非递归模式 ✓ 失败调用不算 ✓)
      动态(节点必须来自注册表 ✓ 没有可算节点时如实 ok=False 不崩 ✓)
```
