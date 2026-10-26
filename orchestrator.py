#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""orchestrator.py — 平台编排决策层（2026-10-21 · 云边架构第 1 步）

## 定位
平台作为 **DSH 插件** ✓ ⇒ DSH（云端）需要知道：
  「这个任务，平台能做什么？该怎么做？」

⇒ 本模块是**决策层**（不执行 ✓ 只出计划 ✓）
   实际执行仍由 DSH 或平台原有接口完成 ✓

## 输入
  {task: "用户要做什么", context?: {...}, strategy?: "auto"|"local_only"|"cloud_first"}

## 输出（执行计划 ✓）
  {
    ok: true,
    task: "...",
    route: {model: "local_7b"|"cloud", why: "..."},       ← 模型路由
    plan: [                                                 ← 分步计划
      {step: 1, action: "retrieve", target: "academic_search", params: {...}, reason: "..."},
      {step: 2, action: "call_tool", target: "list_loras", params: {}, reason: "..."},
      ...
    ],
    capabilities: {...},                                    ← 平台能力清单
    confidence: {level: "high"|"medium"|"low", signals: [...]},
    warnings: [...]
  }

## 依据
  · 工具路由: scripts/tool_retriever.py ✓（T-4 验证 ✓ 省 47% token）
  · Grounding: scripts/academic_search.py ✓（实测可用 ✓）
  · 仿真约束: sim_nodes.py ✓（6 节点 ✓ 41/41 ✓）
  · 模型路由: settings.json ✓（vllm 本地 / openai 云端）
  · 置信度: 见 docs/CONFIDENCE_SCORER_DESIGN.md ✓
"""
from __future__ import annotations

import os

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TOOL_DOCS = ROOT / 'agent_kb' / '_tool_docs.json'   # ★ 唯一真相 ✓


# ══════════════════════════════════════════════════════════
# 能力清单（DSH 用来自我介绍 ✓）
# ══════════════════════════════════════════════════════════
def capabilities() -> dict:
    """平台能力的**真实**清单 ✓（不吹 ✗ 有的写有 ✓ 没有的写没有 ✗）"""
    caps = {
        'tools': 0,
        'sim_nodes': [],
        'models': {},
        'kb': 0,
        'lora': 0,
        'features': {},
    }
    # 工具数
    try:
        d = json.loads((TOOL_DOCS).read_text(encoding='utf-8'))
        caps['tools'] = len(d.get('tools') or {})
    except Exception:
        pass
    # 仿真节点
    try:
        sys.path.insert(0, str(ROOT))
        import sim_nodes
        sim_nodes.load_plugin_dir()
        caps['sim_nodes'] = sorted(sim_nodes.SIM_NODES.keys())
    except Exception:
        pass
    # 模型
    try:
        st = json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))
        caps['models'] = {
            'local_vllm': str((st.get('providers') or {}).get('vllm', {}).get('api_base') or ''),
            'cloud': str((st.get('providers') or {}).get('openai', {}).get('model') or ''),
            'inference_gpu': st.get('inference_gpu'),
            'train_gpu': st.get('train_gpu'),
            'gpu_mode': st.get('gpu_mode'),
        }
    except Exception:
        pass
    # KB / LoRA（★ P1-4: 给库清单 ✓）
    try:
        _kbs = list_kbs()
        caps['kb'] = len(_kbs)
        caps['kb_names'] = [k['name'] for k in _kbs[:12]]
    except Exception:
        pass
    try:
        # ★ 修（2026-10-22 实测）：原来读「项目内 ./loras」⇒ 报 3，真实 /loras 有 44 ✗
        #   改为按 settings.lora_dir（与 /loras 端点同一真源）
        try:
            import json as _j1
            _st1 = _j1.loads((pathlib.Path(__file__).resolve().parent / 'settings.json').read_text(encoding='utf-8'))
        except Exception:
            _st1 = {}
        _ldir = (os.environ.get('LORA_DIR') or _st1.get('lora_dir') or '')
        if _ldir:
            _lp = pathlib.Path(os.path.expanduser(os.path.expandvars(str(_ldir))))
            if not _lp.is_absolute():
                _lp = pathlib.Path(__file__).resolve().parent / _lp
        else:
            _lp = pathlib.Path(__file__).resolve().parent / 'loras'
        caps['lora'] = len([x for x in _lp.glob('*') if x.is_dir()])
    except Exception:
        pass
    # 功能开关（真实状态 ✓）
    caps['features'] = {
        'tool_routing': (ROOT / 'scripts/tool_retriever.py').exists(),
        'grounding_search': (ROOT / 'scripts/academic_search.py').exists(),
        'simulation_constraint': bool(caps['sim_nodes']),
        'kb_cleaning': (ROOT / 'kb_toolkit.py').exists(),
        'lora_training': (ROOT / 'train_lora.py').exists(),
        'confidence_scorer': (ROOT / 'scripts/confidence_scorer.py').exists(),
        'confidence_in_chat': False,      # ⚠ 诚实: 未接入对话 ✗
        'cloud_to_local_orchestration': False,  # ⚠ 诚实: 本接口是第一版 ✓
    }
    return caps


# ══════════════════════════════════════════════════════════
# ① 模型路由（本地 vs 云端）
# ══════════════════════════════════════════════════════════
CLOUD_HINTS = re.compile(
    r'(最新|最近|2025|2026|state[- ]of[- ]the[- ]art|论文|paper|研究|文献|'
    r'arXiv|DOI|引用|citation|综述|survey|benchmark|'
    r'复杂推理|多步|规划|设计一个|分析.*趋势)', re.I)
LOCAL_HINTS = re.compile(
    r'(列出|列一下|查一下|看一下|有哪些|几个|当前|现在|状态|配置|设置|'
    r'读一下|打开|写一个|新建|删掉|跑一下|执行|启动|停掉)', re.I)


def route_model(task: str, strategy: str = 'auto') -> dict:
    """决定用本地还是云端 ✓"""
    if strategy == 'local_only':
        return {'model': 'local_7b', 'why': 'strategy=local_only 强制本地 ✓'}
    if strategy == 'cloud_first':
        return {'model': 'cloud', 'why': 'strategy=cloud_first 强制云端 ✓'}
    cloud_hit = bool(CLOUD_HINTS.search(task))
    local_hit = bool(LOCAL_HINTS.search(task))
    # ★ Bug2 修: 训练任务单独说明 ✓
    if _is_train_task(task):
        return {'model': 'local_7b',
                'why': '训练任务 ⇒ **必须本地** ✗（数据不出机器 ✓ 且要用 GPU ✓）'}
    if cloud_hit and not local_hit:
        return {'model': 'cloud',
                'why': '任务含云端特征（学术/最新/复杂推理）⇒ 云端更强 ✓'}
    if local_hit and not cloud_hit:
        return {'model': 'local_7b',
                'why': '任务含本地特征（平台操作/查询）⇒ 本地够用 ✓ 且不传数据 ✓'}
    if cloud_hit and local_hit:
        return {'model': 'cloud',
                'why': '任务同时含两类特征 ⇒ 优先云端 ✓（本地能力边界 ⚠）'}
    return {'model': 'local_7b',
            'why': '无明显特征 ⇒ 默认本地 ✓（数据不外传 ✓ 且快 ✓）'}


# ══════════════════════════════════════════════════════════
# ①b 知识库自动选择（P1-4 · 2026-10-21）
# ══════════════════════════════════════════════════════════
KB_ROOT = ROOT / 'data/kbs'

# ★ P1-4 fix（2026-10-21）: 中英文别名 ✓
#   问题: 库名是英文 xiyouji ✗ 任务是中文"西游记" ✗ ⇒ 匹配不上 ✗
KB_ALIASES = {
    'xiyouji': ['西游记', '西游', '孙悟空', '悟空', '唐僧', '八戒', '沙僧'],
    'learned_skills': ['做法库', '学到的做法', '做法', '技能', '经验'],
    'learned_chat': ['学到的对话', '聊天记录'],
    'learned_chat2': ['学到的对话2'],
    '答辩录音': ['答辩', '录音', '答辩稿'],
    'agent_use': ['agent 用法', '智能体用法'],
    'agent_use_ctrl': ['agent 控制'],
    'ab_clean_20261021': ['清洗后', '清洗结果'],
    'ab_raw_20261021': ['清洗前', '原始'],
    'k1_test_clean': ['k1清洗'],
    'k1_test': ['k1'],
    'test_use_agent': ['测试用agent'],
    'poetry_knowledge_base': ['诗词', '古诗', '诗歌'],
    'minimax_h3_prompt': ['提示词', 'prompt 库'],
    'reconstructed_kb_new': ['重建的新库'],
    'reconstructed_kb': ['重建库'],
}


def list_kbs() -> list[dict]:
    """扫描磁盘上的知识库 ✓（零 HTTP ✓ 快 ✓）"""
    out = []
    if not KB_ROOT.exists():
        return out
    for d in sorted(KB_ROOT.iterdir()):
        if not d.is_dir():
            continue
        n_chunks = 0
        # 找块文件 ✓（多种命名都可能 ⚠）
        for pat in ('chunks*.json', '*.jsonl', 'index*.json'):
            for f in d.glob(pat):
                try:
                    txt = f.read_text(encoding='utf-8', errors='replace')
                    import json as _j
                    try:
                        data = _j.loads(txt)
                        if isinstance(data, list):
                            n_chunks += len(data)
                        elif isinstance(data, dict):
                            n_chunks += len(data.get('chunks') or data.get('data') or [])
                    except Exception:
                        n_chunks += 1
                except Exception:
                    pass
        out.append({'name': d.name, 'path': str(d), 'chunks': n_chunks})
    return out


def select_kb(task: str) -> dict:
    """★ 按任务自动选最相关的知识库 ✓

    规则（保守 ✓）:
      ① 库名出现在任务里 ⇒ 直接命中 ✓（最强信号 ✓）
      ② 任务关键词与库名有字符重叠 ✓（弱信号 ⚠）
      ③ 都不中 ⇒ **不瞎选** ✗ 返回 None ✓ 并建议建库 ✓
    """
    kbs = list_kbs()
    if not kbs:
        return {'kb': None, 'why': '磁盘上没有任何知识库 ⚠', 'suggest_create': True}

    t = task.lower()
    # ① 库名直接命中 ✓
    for kb in kbs:
        nm = kb['name']
        if nm and nm.lower() in t:
            return {'kb': nm, 'chunks': kb['chunks'],
                    'why': '任务里直接提到了库名 **%s** ✓' % nm,
                    'suggest_create': False}

    # ①b 中文别名命中 ✓（★ P1-4 fix）
    for kb in kbs:
        nm = kb['name']
        for alias in (KB_ALIASES.get(nm) or []):
            if alias in t:
                return {'kb': nm, 'chunks': kb['chunks'],
                        'why': '任务提到中文别名「%s」⇒ 命中库 **%s** ✓' % (alias, nm),
                        'suggest_create': False}

    # ② 中文关键词重叠 ✓（按 2-gram 粗匹配 ⚠）
    best, best_score = None, 0.0
    for kb in kbs:
        nm = kb['name']
        if not nm or nm.startswith('_'):
            continue
        # 库名拆词 ✓（下划线/数字分隔 ✓）
        parts = [p for p in nm.replace('-', '_').split('_') if len(p) >= 2]
        hit = sum(1 for p in parts if p.lower() in t)
        score = hit / max(len(parts), 1)
        # 无 chunks 的库降权 ⚠
        if kb['chunks'] == 0:
            score *= 0.5
        if score > best_score:
            best, best_score = kb, score

    if best is not None and best_score >= 0.5:
        return {'kb': best['name'], 'chunks': best['chunks'],
                'why': '库名关键词与任务匹配（%.0f%% ✓）' % (best_score * 100),
                'suggest_create': False}

    return {'kb': None,
            'why': '**没有匹配的知识库** ⚠（最接近: %s）' % (best['name'] if best else '无'),
            'candidates': [k['name'] for k in kbs[:5]],
            'suggest_create': True}


def select_kbs(task: str, max_n: int = 3) -> dict:
    """★ 多知识库挂载（2026-10-21）

    需求: **可以同时挂载多个知识库** ✓
    与 select_kb 的区别:
      · select_kb  = 选**最相关的 1 个**（兼容旧逻辑 ✓）
      · select_kbs = 选**多个**（top-N ✓ 按相关度排序 ✓）

    返回:
      {'kbs': [{'name','chunks','score','why'}], 'suggest_create': bool, 'why': str}
    """
    kbs = list_kbs()
    if not kbs:
        return {'kbs': [], 'why': '磁盘上没有任何知识库 ⚠', 'suggest_create': True}

    t = task.lower()
    scored = []
    for kb in kbs:
        nm = kb['name'] or ''
        if not nm or nm.startswith('_'):
            continue
        score = 0.0
        why = ''
        # ① 库名直接命中 ✓（最高分 ✓）
        if nm.lower() in t:
            score = 1.0
            why = '库名直接命中'
        else:
            # ② 中文别名命中 ✓
            for alias in (KB_ALIASES.get(nm) or []):
                if alias in t:
                    score = max(score, 0.9)
                    why = '别名「%s」命中' % alias
                    break
            # ③ 库名关键词重叠 ✓
            if score == 0.0:
                parts = [p for p in nm.replace('-', '_').split('_') if len(p) >= 2]
                hit = sum(1 for p in parts if p.lower() in t)
                if hit:
                    score = 0.5 + 0.3 * (hit / max(len(parts), 1))
                    why = '关键词 %d/%d 命中' % (hit, len(parts))
        if score > 0:
            scored.append({'name': nm, 'chunks': kb['chunks'],
                           'score': round(score, 3), 'why': why})

    if not scored:
        return {'kbs': [],
                'why': '**没有匹配的知识库** ⚠',
                'candidates': [k['name'] for k in kbs[:5]],
                'suggest_create': True}

    scored.sort(key=lambda x: (-x['score'], -(x['chunks'] or 0)))
    picked = scored[:max_n]
    return {'kbs': picked, 'suggest_create': False,
            'why': '匹配 %d 个库 ⇒ 挂载前 %d 个 ✓' % (len(scored), len(picked)),
            'total_matched': len(scored)}


# ══════════════════════════════════════════════════════════
# ② 执行计划生成
# ══════════════════════════════════════════════════════════
COMPUTE_HINTS = re.compile(
    r'(计算|算一下|算算|求一下|求和|合计|平均|均值|方差|标准差|百分比|占比|'
    r'比率|增长率|换算|四舍五入|除以|乘以|加总|统计|分布|分位数)', re.I)
# ★ P1-4 fix: 扩充（加"里有什么/讲了什么/内容/资料" ✓）
KB_HINTS = re.compile(
    r'(知识库|文档|资料|检索.*库|库.*里|kb|'
    r'里有什么|里面有什么|讲了什么|写了什么|说了什么|'
    r'内容是什么|包含什么|里面是)', re.I)
TRAIN_HINTS = re.compile(r'(训练|微调|finetune|fine-tune|训一个|训个)', re.I)
# ★ Bug1 修（2026-10-21）: "列出所有 LoRA" 不是训练任务 ✗
#   规则: 如果句子里是【查询类动词】+【LoRA/模型/训练】⇒ 不是训练 ✓
_QUERY_VERB = re.compile(
    r'(列出|列一下|查看|看看|有哪些|几个|当前|现在|状态|配置|挂载了哪些|'
    r'列表|显示|查询一下有哪些)', re.I)
_QUERY_OBJECT = re.compile(r'(LoRA|lora|模型|训练任务|训练记录|数据集)', re.I)


# ★ S16（修 B040）：外部来源/云端模型/网页采集 → 应识别为云端需求
_EXTERNAL_HINTS = re.compile(
    r'(京东|淘宝|天猫|阿里巴巴|1688|拼多多|电商平台|购物平台|购物网站|'
    r'网页抓取|爬取|爬虫|抓取网页|浏览器抓取|联网搜索|'
    r'deepseek|DeepSeek|GPT|OpenAI|Claude|Kimi|GLM|大模型API|云端模型|调用云端)')
try:
    CLOUD_HINTS = re.compile(CLOUD_HINTS.pattern + '|' + _EXTERNAL_HINTS.pattern)
except Exception:
    pass


def _is_train_task(task: str) -> bool:
    """★ Bug1 修: 判断是否**真的**训练任务 ✓"""
    if not TRAIN_HINTS.search(task):
        return False
    # 查询类: "列出所有 LoRA" / "当前有哪些训练任务" ⇒ 不是训练 ✗
    if _QUERY_VERB.search(task) and _QUERY_OBJECT.search(task):
        return False
    return True
# ★ 2026-10-21: 检索分三类 ✓（原来只有学术 ✗）
#   用户要求: **模型优先通过浏览器自动寻找依赖而不是纯推理** ✓
ACADEMIC_HINTS = re.compile(
    r'(论文|paper|研究|文献|arXiv|DOI|引用|citation|综述|survey|benchmark|理论|公式推导)', re.I)
# 依赖类: 装/版本/API/文档 ⇒ **必须开浏览器查官方** ✗ 不许凭记忆 ✗
DEPENDENCY_HINTS = re.compile(
    r'(安装|依赖|版本|升级|降级|npm|pip|conda|yarn|pnpm|package|包管理|'
    r'npmjs|pypi|github|仓库|repo|轮子|wheel|requirements|'
    r'API|SDK|接口文档|官方文档|docs|用法|怎么用|怎么调用|参数说明|'
    r'报错|错误码|error code|traceback|兼容性)', re.I)
# 通用网页: 最新/现状/价格/官网 ⇒ 开浏览器查 ✓
WEB_HINTS = re.compile(
    r'(最新|现在|目前|当前.*版本|官网|网址|网站|下载地址|'
    r'发布|release|更新日志|changelog|教程|指南|guide|'
    r'搜索|搜一下|查一下.*网|百度|谷歌|google|bing)', re.I)
# 汇总（保持向后兼容 ✓）
RETRIEVE_HINTS = re.compile(
    ACADEMIC_HINTS.pattern + r'|' + DEPENDENCY_HINTS.pattern + r'|' + WEB_HINTS.pattern, re.I)
TOOL_HINTS = re.compile(
    r'(列出|列一下|查一下|看一下|有哪些|几个|当前|现在|状态|配置|设置|'
    r'读一下|打开|写一个|新建|删掉|跑一下|执行|启动|停掉|工具|'
    r'工作流|workflow|采购清单|表格生成|生成表格|excel|xlsx|蒸馏|搭建)', re.I)


def build_plan(task: str, context: dict | None = None) -> list[dict]:
    """生成分步执行计划 ✓（按优先级排序 ✓）"""
    plan = []
    step = 0

    # ① 检索（Grounding · ★ 2026-10-21 扩展为三类 ✓）
    #   ★ 用户要求: **模型优先通过浏览器自动寻找依赖而不是纯推理** ✓
    #   原则: 不知道 ⇒ **先查** ✗ 不许凭记忆编 ✗

    # ①a 依赖类（装/版本/API/报错）⇒ **必须开浏览器查官方** ✗✗
    if DEPENDENCY_HINTS.search(task):
        step += 1
        plan.append({
            'step': step, 'action': 'browser_ground', 'target': 'browser_open',
            'params': {
                'query': task[:200],
                'prefer_sites': ['npmjs.com', 'pypi.org', 'github.com',
                                 'docs.python.org', 'nodejs.org', 'developer.mozilla.org'],
                'flow': ['browser_open', 'browser_read', 'browser_find'],
            },
            'reason': '**依赖/版本/API 类问题 ⇒ 必须开浏览器查官方文档** ✗ '
                      '不许凭记忆编版本号/API 签名 ✗（编错的代价 > 查的代价 ✗）',
            'hard_constraint': True,
        })

    # ①b 学术类 ⇒ 调 academic_search ✓
    if ACADEMIC_HINTS.search(task):
        step += 1
        plan.append({
            'step': step, 'action': 'retrieve', 'target': 'academic_search',
            'params': {'query': task[:200], 'source': 'all', 'limit': 5},
            'reason': '任务涉及学术事实 ⇒ 必须先检索 ✗ 禁止幻觉推理 ✗',
            'hard_constraint': True,
        })

    # ①c 通用网页类 ⇒ 开浏览器查 ✓
    if WEB_HINTS.search(task) and not DEPENDENCY_HINTS.search(task):
        step += 1
        plan.append({
            'step': step, 'action': 'browser_ground', 'target': 'browser_open',
            'params': {
                'query': task[:200],
                'flow': ['browser_open', 'browser_read'],
            },
            'reason': '任务涉及时效性/官方信息 ⇒ 开浏览器查 ✓ 不许凭记忆 ✗',
            'hard_constraint': True,
        })

    # ② 知识库（★ P1-4: 自动选库 ✓）
    #   ★ fix: **库名/别名命中就直接触发** ✓ 不依赖 KB_HINTS ✗
    # ★ 多库挂载（2026-10-21）: 优先多库 ✓ 退回单库 ✓
    _msel = select_kbs(task)
    _sel_early = select_kb(task)
    if KB_HINTS.search(task) or _sel_early.get('kb') or _msel.get('kbs'):
        step += 1
        if _msel.get('kbs'):
            _names = [k['name'] for k in _msel['kbs']]
            plan.append({
                'step': step, 'action': 'kb_query',
                'target': 'multi_query_knowledge_bases',
                'params': {'kb_names': _names, 'query': task[:200], 'top_k': 3},
                'reason': '任务涉及知识库 ⇒ **同时挂载查 %d 个库** ✓ %s' % (
                    len(_names), _msel.get('why')),
                'kb_selection': _msel,
            })
            sel = {'kb': _names[0], 'why': _msel.get('why')}
        elif _sel_early.get('kb'):
            sel = _sel_early
            plan.append({
                'step': step, 'action': 'kb_query', 'target': 'query_knowledge_base',
                'params': {'kb_name': sel['kb'], 'query': task[:200], 'top_k': 3},
                'reason': '任务涉及知识库 ⇒ 查库 ✓ %s' % sel.get('why'),
                'kb_selection': sel,
            })
        else:
            # 没有匹配库 ⇒ **建议建库** ✓（不是瞎查 ✗）
            sel = {'kb': None, 'why': _sel_early.get('why') or '无匹配库 ⚠',
                   'suggest_create': True}
            plan.append({
                'step': step, 'action': 'kb_create_and_query',
                'target': 'create_knowledge_base',
                'params': {'name': '<按任务命名>', 'query': task[:200]},
                'reason': '任务涉及知识库但**没有匹配的库** ⚠ ⇒ '
                          '先建库再查 ✓（不瞎选库 ✗）%s' % sel.get('why'),
                'kb_selection': sel,
                'hard_constraint': True,
            })

    # ③ 工具（按相关度选 ✓）
    if TOOL_HINTS.search(task):
        try:
            sys.path.insert(0, str(ROOT / 'scripts'))
            import tool_retriever as _tr
            d = json.loads((TOOL_DOCS).read_text(encoding='utf-8'))
            names = list((d.get('tools') or {}).keys())
            sel, diag = _tr.select_tools(task, names, top_k=8)
            picked = []
            for x in (sel or []):
                if isinstance(x, str):
                    picked.append(x)
                elif isinstance(x, dict):
                    nm = x.get('name')
                    if nm:
                        picked.append(nm)
            # ★ S16（修 B039）：工作流/Excel 意图 → 必备工具置顶，避免被相关度截断丢弃
            _author_hint = re.search(
                r'(工作流|workflow|自动化|搭建|蒸馏|采购清单|excel|xlsx|表格生成|生成表格|导出表格)',
                task or '', re.I)
            if _author_hint:
                _need = [t for t in ("create_workflow_config", "add_workflow_node",
                                     "validate_workflow", "run_workflow", "write_sheet",
                                     "read_sheet", "list_workflow_nodes")
                         if t in names]
                picked = _need + [t for t in picked if t not in _need]
                diag = dict(diag or {})
                diag["author_tools_forced"] = _need
            step += 1
            plan.append({
                'step': step, 'action': 'call_tool', 'target': picked[:15],
                'params': {},
                'reason': '任务涉及平台操作 ⇒ 推荐这些工具 ✓（%s相关度排序 ✓）'
                          % ('工作流/Excel 必备工具已置顶，' if _author_hint else ''),
                'diag': diag,
            })
        except Exception as e:
            step += 1
            plan.append({'step': step, 'action': 'call_tool', 'target': '（工具检索失败 ⚠）',
                         'params': {}, 'reason': str(e)[:100]})

    # ④ 计算（必须走仿真 ✓）
    if COMPUTE_HINTS.search(task):
        step += 1
        plan.append({
            'step': step, 'action': 'simulate', 'target': 'run_simulation',
            'params': {'node': 'math_eval', 'args': {'expr': '<待填>'}},
            'reason': '任务涉及计算 ⇒ 必须走仿真节点 ✗ 禁止心算 ✗',
        })

    # ⑤ 训练（★ Bug1 修: 用 _is_train_task ✓ 不用裸 TRAIN_HINTS ✗）
    if _is_train_task(task):
        step += 1
        plan.append({
            'step': step, 'action': 'train', 'target': 'train_lora.py',
            'params': {'dataset_file': '<需指定>', 'lora_name': '<自动生成>'},
            'reason': '任务涉及训练 ⇒ 走平台训练流水线 ✓（有格式/前置/单飞保护 ✓）',
        })

    # ⑥ 兜底
    if not plan:
        step += 1
        plan.append({'step': step, 'action': 'direct_answer', 'target': None,
                     'params': {}, 'reason': '无特殊需求 ⇒ 直接回答 ✓'})

    return plan


# ══════════════════════════════════════════════════════════
# ③ 置信度信号（来自 docs/CONFIDENCE_SCORER_DESIGN.md 的"外部信号"组 ✓）
# ══════════════════════════════════════════════════════════
def confidence_signals(task: str, plan: list[dict]) -> dict:
    """给 DSH 一个**可验证**的置信度信号 ✓（不是模型自评 ✗）"""
    signals = []
    # ① 有检索步骤 ⇒ 有外部依据 ✓
    has_retrieve = any(p.get('action') in ('retrieve', 'browser_ground') for p in plan)
    has_browser = any(p.get('action') == 'browser_ground' for p in plan)
    hard = [p for p in plan if p.get('hard_constraint')]
    signals.append({'name': 'has_grounding_step', 'value': has_retrieve,
                    'why': '有检索步骤 ⇒ 有外部依据 ✓'})
    signals.append({'name': 'has_browser_ground', 'value': has_browser,
                    'why': '依赖/时效类 ⇒ 开浏览器查官方 ✓（不是纯推理 ✓）'})
    signals.append({'name': 'hard_constraint_count', 'value': len(hard),
                    'why': '强制约束步数 ✓ 必须执行完才能下结论 ✗'})
    # ② 有计算步骤 ⇒ 走确定性执行器 ✓
    has_sim = any(p.get('action') == 'simulate' for p in plan)
    signals.append({'name': 'has_deterministic_compute', 'value': has_sim,
                    'why': '计算走仿真节点 ✓ 不是心算 ✓'})
    # ③ 工具候选数 ✓
    n_tools = 0
    for p in plan:
        if p.get('action') == 'call_tool' and isinstance(p.get('target'), list):
            n_tools = len(p['target'])
    signals.append({'name': 'tool_candidates', 'value': n_tools,
                    'why': '候选工具数 ✓ 过多/过少都可疑 ⚠'})
    # ④ 平台能力是否覆盖 ✓
    caps = capabilities()
    covered = bool(plan) and not any('失败' in str(p.get('target') or '') for p in plan)
    signals.append({'name': 'platform_covered', 'value': covered,
                    'why': '平台的规划能力覆盖该任务 ✓'})

    # 综合判断（保守 ✓）
    if not covered:
        level = 'low'
        why = '平台无法可靠覆盖 ⚠ 建议云端接手或拒答 ✗'
    elif hard and not (has_retrieve or has_sim):
        level = 'low'
        why = '有强制检索要求但计划未含检索步 ⇒ 不可下结论 ✗'
    elif has_retrieve or has_sim:
        level = 'medium'
        why = '有外部依据/确定性执行器 ✓ 但**未接入对话流程** ⚠ 见 capabilities.features.confidence_in_chat'
    else:
        level = 'medium'
        why = '无强信号 ⚠ 默认中等 ✓'
    return {'level': level, 'why': why, 'signals': signals}


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
def orchestrate(task: str, context: dict | None = None,
                strategy: str = 'auto') -> dict:
    """DSH 调这个 ⇒ 拿执行计划 ✓"""
    t = str(task or '').strip()
    if not t:
        return {'ok': False, 'error': 'task 不能为空'}
    plan = build_plan(t, context)
    route = route_model(t, strategy)
    conf = confidence_signals(t, plan)
    caps = capabilities()
    warnings = [
        '⚠ 本接口是**第一版编排** ✓ 只出计划不执行 ✗',
        '⚠ 置信度打分器**未接入对话流程** ✗（只在此处给信号 ✓）',
        '⚠ 云端→本地的**自动执行**需 DSH 侧配合 ✓（平台已暴露接口 ✓）',
    ]
    return {
        'ok': True,
        'task': t,
        'strategy': strategy,
        'route': route,
        'plan': plan,
        'confidence': conf,
        'capabilities': caps,
        'warnings': warnings,
    }


if __name__ == '__main__':
    q = ' '.join(sys.argv[1:]) or '列出平台所有 LoRA 并分析最近论文趋势'
    print(json.dumps(orchestrate(q), ensure_ascii=False, indent=2))
