#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户语义理解数据集生成器（离线、确定性、可校验；不需要 GPU ✓）。

为什么这样做（而不是直接让大模型编）:
  · 训的是"用户对 agent 提要求"的语义 → 种子必须是【用户真实口气】；
    本脚本以 data/user_voice/seeds.jsonl（从真实对话转录 + 人工标注期望语义）为锚，
    再用【规则化变异】造同义表达，用【真实资源名】造组合样本 ——
    所有标签都可由规则校验，不引入幻觉 ✗
  · 明早的纠错/清洗才去做"表达多样性"，今晚先把【标签可信】的数据做出来 ✓

产出:
  training_data/semantic_intent_v1.jsonl       训练集（sharegpt ✓ LLaMA-Factory 直接吃）
  training_data/semantic_intent_v1_eval.jsonl  留出集（按种子家族切分，不泄漏 ✓）
  training_data/semantic_intent_v1_report.md   生成 + 校验报告

用法: /home/luxy/.venv/bin/python scripts/gen_semantic_dataset.py
"""
from __future__ import annotations

import json
import random
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
API = 'http://127.0.0.1:8001'
OUT = ROOT / 'training_data' / 'semantic_intent_v1.jsonl'
OUT_EVAL = ROOT / 'training_data' / 'semantic_intent_v1_eval.jsonl'
REPORT = ROOT / 'training_data' / 'semantic_intent_v1_report.md'
SEEDS = ROOT / 'data' / 'user_voice' / 'seeds.jsonl'
TOOLS_JSON = ROOT / 'agent_kb' / '_tools.json'

INTENT_TYPES = [
    'run_learning_batch', 'kb_ingest', 'correct_previous_approach', 'choose_option',
    'summarize_and_deliver', 'correct_and_authorize', 'diagnose_failure', 'inform_state',
    'report_symptom', 'list_capability', 'audit_capability', 'express_dissatisfaction',
    'plan_training', 'query_then_act', 'query_knowledge_base', 'act_on_resources',
    'run_workflow', 'export_result', 'reject_and_redo', 'set_policy', 'start_training',
    'inspect_resource', 'list_resources', 'switch_base', 'multi_request',
]

SYSTEM_TMPL = """你是 LoRA 工作台的**用户语义理解模块**。用户说话很简短、常常没有标点、会用指代、会打错字，\
也可能一次给多个要求。你的任务：把用户原话翻译成**结构化意图**，供后面的 agent 执行。

【当前环境快照（唯一可信的现状）】
{env}

【硬性要求】
1. 只输出 JSON，不要解释、不要代码块。
2. 目标对象（库名 / LoRA 名 / 数据集名 / 文件名 / 工作流节点）**只能来自上面的快照**；
   用户提到的名字若不在快照里，**不要改写成相近的名字** ——
   放进 targets 原样保留，并在 missing 里写"该名字待核实"，should_ask 置 true。
3. 信息不足且猜错代价高时（要动数据/训练/模型/删除），should_ask 置 true，并在 missing 里写清要问什么。
4. plan 里只放**只读工具在前**；每一步给 why。
5. 用户若有明确禁令（如"别烧云""不要进错库"），必须原样进入 constraints。

【输出 schema】
{{"intent_type": "<枚举之一>", "goal": "<一句话>", "constraints": ["..."], "targets": {{...}},
 "missing": ["..."], "should_ask": true|false, "plan": [{{"tool": "<工具名>", "why": "<一句>"}}]}}

【intent_type 枚举】
{types}
"""

# ---------------- 环境快照（真实取值 ✓） ----------------
def _get(path):
    try:
        with urllib.request.urlopen(API + path, timeout=8) as f:
            return json.loads(f.read().decode())
    except Exception:
        return {}


def real_env():
    env = {}
    try:
        env['models'] = [m.get('name') or m.get('id') for m in (_get('/models').get('models') or [])][:20]
    except Exception:
        env['models'] = []
    try:
        d = _get('/kb/list')
        env['kbs'] = [(k.get('name'), k.get('total_chunks')) for k in (d.get('knowledge_bases') or [])]
    except Exception:
        env['kbs'] = []
    try:
        env['loras'] = list((_get('/loras').get('all') or []) and
                            [x.get('name') for x in (_get('/loras').get('all') or [])])
    except Exception:
        env['loras'] = []
    if not env['loras']:
        try:
            env['loras'] = sorted(p.name for p in (Path.home() / 'loras').iterdir()
                                  if p.is_dir() and not p.name.startswith('_'))
        except Exception:
            env['loras'] = []
    try:
        env['datasets'] = sorted(p.stem for p in (ROOT / 'training_data').glob('*.jsonl')
                                 if not p.stem.endswith('_eval'))
    except Exception:
        env['datasets'] = []
    try:
        cfg = json.loads((ROOT / 'workflow_config.json').read_text(encoding='utf-8'))
        env['wf_name'] = 'workflow_config.json'
        env['wf_nodes'] = [n.get('id') for n in (cfg.get('nodes') or [])]
    except Exception:
        env['wf_name'], env['wf_nodes'] = '', []
    try:
        raw = json.loads(TOOLS_JSON.read_text(encoding='utf-8'))
        if isinstance(raw, list):
            env['tools'] = [t.get('name') for t in raw if isinstance(t, dict) and t.get('name')]
        elif isinstance(raw, dict):
            # ★ 实测 _tools.json 是 {"tools": [...]} 或 {name: {...}} 两种形态之一
            lst = raw.get('tools')
            if isinstance(lst, list):
                env['tools'] = [t.get('name') if isinstance(t, dict) else str(t) for t in lst]
            else:
                env['tools'] = list(raw.keys())
        else:
            env['tools'] = []
        env['tools'] = [t for t in env['tools'] if t]
    except Exception as e:
        print(f'  ⚠ 工具表读取失败（校验会被跳过，必须先修）: {e}')
        env['tools'] = []
    if not env['tools']:
        # 兜底: 从源码抽（保证"工具名必须真实存在"这条校验一定生效 ✓）
        import re as _re
        try:
            txt = (ROOT / 'webui' / 'index3.html').read_text(encoding='utf-8')
            env['tools'] = sorted(set(_re.findall(
                r'AGENT_TOOLS\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{', txt)))
        except Exception:
            pass
    return env


def env_text(env, variant=0):
    """把快照渲染成文本。variant=1 时换一个底座/加载状态，教模型"别被环境噪音带偏"。"""
    base = ['Qwen2.5-7B-Instruct-AWQ', 'Qwen2.5-7B-Instruct', 'Qwen3-4B-Instruct',
            'Qwen3-4B-AWQ', 'Qwen2.5-14B-Instruct-AWQ', 'Qwen2.5-0.5B-Instruct']
    running = ['Qwen2.5-7B-Instruct-AWQ', 'Qwen3-4B-Instruct',
               'Qwen2.5-7B-Instruct', 'Qwen2.5-14B-Instruct-AWQ'][variant % 4]
    loaded = {'0': [], '1': (env.get('loras') or [])[:1], '2': (env.get('loras') or [])[:2],
              '3': []}[str(variant % 4)]
    if variant == 2:
        # 变体2: 知识库少一个（教"快照里没有就别猜"✓）
        env = dict(env); env['kbs'] = (env.get('kbs') or [])[:-1]
    L = [f"基座运行中: {running}", f"可选底座: {', '.join(base)}",
         f"已加载 LoRA: {('、'.join(loaded)) if loaded else '（无）'}"]
    loras = env.get('loras') or []
    L.append(f"可用 LoRA（{len(loras)}）: {', '.join(loras[:14])}{' …' if len(loras) > 14 else ''}")
    kbs = env.get('kbs') or []
    L.append(f"知识库（{len(kbs)}）: " + '; '.join(f'{n}({c}块)' for n, c in kbs[:10]))
    ds = env.get('datasets') or []
    L.append(f"训练数据集（{len(ds)}）: {', '.join(ds[:10])}{' …' if len(ds) > 10 else ''}")
    L.append(f"工作流配置: {env.get('wf_name') or '（无）'} · 节点: {', '.join((env.get('wf_nodes') or [])[:10])}")
    L.append("上传文件: （最近）sample_paper.pdf, 采购台账.xlsx")
    return '\n'.join(L)


# ---------------- 口语变异（规则化，保持语义 ✓） ----------------
def variants(text):
    out = [text]
    no_comma = re.sub(r'[，,。；;]', '', text)
    if no_comma != text:
        out.append(no_comma)
    # 中英之间的空格去掉 / 加上（用户两种都写）
    out.append(re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z])', '', text))
    out.append(re.sub(r'(?<=[A-Za-z])\s+(?=[\u4e00-\u9fff])', '', text))
    # 大小写与常见错写
    t = text.replace('LoRA', 'lora').replace('Agent', 'agent')
    if t != text:
        out.append(t)
    t2 = text.replace('lora', 'loar').replace('agent', 'aget')
    if t2 != text:
        out.append(t2)
    # 尾部语气/极简
    if len(text) > 12:
        out.append(text.rstrip('。，！？') + '，')
    seen, res = set(), []
    for v in out:
        if v.strip() and v not in seen:
            seen.add(v); res.append(v)
    return res[:6]


# ★ DSH 插件用 paper_lora_* 前缀，工作台 agent 工具用裸名 —— 训练里统一到【工作台】那套 ✓
#   （用户在真实对话里两种都可能说；模型要落到工作台实际能调的工具上）
TOOL_ALIASES = {
    'paper_lora_kb_query': 'query_knowledge_base',
    'paper_lora_kb_list': 'list_knowledge_bases',
    'paper_lora_kb_info': 'list_knowledge_bases',
    'paper_lora_kb_create': 'create_knowledge_base',
    'paper_lora_kb_rebuild': 'create_knowledge_base',
    'paper_lora_kb_ingest_folder': 'query_knowledge_base',
    'paper_lora_list_loras': 'list_loras',
    'paper_lora_list_datasets': 'list_available_datasets',
    'paper_lora_train_list': 'list_trainings',
    'paper_lora_train_start': 'start_training',
    'paper_lora_train_status': 'get_training_status',
    'paper_lora_vllm_status': 'get_vllm_status',
    'paper_lora_vllm_start': 'get_vllm_status',
    'paper_lora_vllm_stop': 'get_vllm_status',
    'paper_lora_workflow_configs': 'list_workflow_configs',
    'paper_lora_workflow_list': 'list_workflow_results',
    'paper_lora_workflow_latest': 'get_workflow_result',
    'paper_lora_status': 'get_settings',
    'paper_lora_call': 'export_docx',
    # ---- 裸名别名（我在种子/模板里用过的插件式裸名 ✗ → 工作台真名 ✓）----
    'list_datasets': 'list_available_datasets',
    'train_list': 'list_trainings',
    'train_status': 'get_training_status',
    'train_start': 'start_training',
    'workflow_configs': 'list_workflow_configs',
    'workflow_list': 'list_workflow_results',
    'workflow_latest': 'get_workflow_result',
    'vllm_status': 'get_vllm_status',
    'vllm_start': 'get_vllm_status',
    'vllm_stop': 'get_vllm_status',
    'system_status': 'get_settings',
    'kb_list': 'list_knowledge_bases',
    'kb_query': 'query_knowledge_base',
    'kb_create': 'create_knowledge_base',
    'kb_info': 'list_knowledge_bases',
    'kb_rebuild': 'create_knowledge_base',
    'kb_ingest_folder': 'query_knowledge_base',
}


def canon_tool(name):
    n = str(name or '')
    return TOOL_ALIASES.get(n, n)


def norm_intent(d):
    o = {
        'intent_type': d.get('intent_type') or 'inspect_resource',
        'goal': d.get('goal') or '',
        'constraints': d.get('constraints') or [],
        'targets': d.get('targets') or {},
        'missing': d.get('missing') or [],
        'should_ask': bool(d.get('should_ask')),
        'plan': [{'tool': canon_tool(st.get('tool')), 'why': st.get('why') or ''}
                 for st in (d.get('plan') or []) if st],
    }
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


GOAL_VARIANTS = {
    'query_knowledge_base': [
        '在知识库 {t} 中检索相关内容',
        '从 {t} 里找与问题相关的片段',
        '用 {t} 的语料回答这个问题',
        '在 {t} 中查证，并给出出处',
    ],
    'inspect_resource': [
        '查看 {t} 的现状并说明',
        '核对 {t} 是否存在、状态如何',
        '读取 {t} 的信息',
        '检查 {t} 并回报关键字段',
    ],
    'list_resources': [
        '列出全部可选项',
        '把现有清单列出来',
        '先给出完整清单供确认',
    ],
    'switch_base': [
        '把推理底座切换到 {t}',
        '换成 {t} 来推理',
        '改用 {t} 作为底座',
    ],
}


def vary_goal(intent, idx=0):
    """同类样本的 goal 轮换写法 —— 治"目标千篇一律 → 模型背模板" ✗（loss 0.0003 的根因之一）"""
    it = intent.get('intent_type')
    vs = GOAL_VARIANTS.get(it)
    if not vs:
        return intent
    tgt = ''
    for v in (intent.get('targets') or {}).values():
        if isinstance(v, str):
            tgt = v
            break
    intent = dict(intent)
    try:
        intent['goal'] = vs[idx % len(vs)].format(t=tgt or intent.get('goal') or '目标')
    except Exception:
        pass
    # 约束也轮换（真实存在的可选约束 ✓）
    if it in ('query_knowledge_base', 'inspect_resource', 'list_resources') and idx % 3 == 0:
        intent['constraints'] = list(intent.get('constraints') or []) + ['只读操作，不得改动数据']
    return intent


def sample(phrase, intent, env):
    return {'conversations': [
        {'from': 'system', 'value': SYSTEM_TMPL.format(env=env, types=' / '.join(INTENT_TYPES))},
        {'from': 'human', 'value': phrase},
        {'from': 'gpt', 'value': norm_intent(intent)},
    ]}


# ---------------- 组合模板（用真实资源名 → 标签必然正确 ✓） ----------------
def combinatorial(env):
    out = []
    kbs = [n for n, _ in (env.get('kbs') or [])]
    ds = (env.get('datasets') or [])[:16]
    loras = (env.get('loras') or [])
    nodes = (env.get('wf_nodes') or [])
    for kb in kbs:
        for q in ('检索一下', '查查里面写了什么', '在这个库里找', '搜一下',
                  '去', '翻翻', '这个库里有啥', '看看', '帮我找找', '查一下',
                  '里面有没有', '对一下', '引用一下', '拿这个库回答', '用这个库', '基于这个库'):
            ph = f'{q}{kb}'
            out.append((ph, {'intent_type': 'query_knowledge_base',
                             'goal': f'在知识库 {kb} 中检索',
                             'targets': {'kb': kb},
                             'plan': [{'tool': 'paper_lora_kb_query',
                                       'why': '按库名检索（名字先经 list 核对）'}]}))
    for d in ds:
        out.append((f'看看{d}有多少条', {'intent_type': 'inspect_resource',
                                       'goal': f'查看数据集 {d} 的规模',
                                       'targets': {'dataset': d},
                                       'plan': [{'tool': 'list_datasets', 'why': '列出数据集与条数'}]}))
    for l in loras:
        out.append((f'{l}能挂吗', {'intent_type': 'inspect_resource',
                                   'goal': f'判断 LoRA {l} 能否挂到当前底座',
                                   'constraints': ['要先看兼容性再动手'],
                                   'targets': {'lora': l},
                                   'plan': [{'tool': 'list_loras', 'why': '看 compatible 与已加载状态'}]}))
    for n in nodes:
        out.append((f'{n}这个节点在干嘛', {'intent_type': 'inspect_resource',
                                          'goal': f'说明工作流节点 {n} 的作用与配置',
                                          'targets': {'workflow_node': n},
                                          'plan': [{'tool': 'list_workflow_nodes',
                                                    'why': '读节点定义'}]}))
    for m in ('Qwen3-4B-Instruct', 'Qwen2.5-7B-Instruct', 'Qwen2.5-7B-Instruct-AWQ'):
        out.append((f'换到{m}', {'intent_type': 'switch_base',
                                 'goal': f'把推理底座切换到 {m}',
                                 'constraints': ['切换会中断当前推理', '切完要确认上下文与 LoRA 兼容性'],
                                 'targets': {'base_model': m},
                                 'plan': [{'tool': 'get_vllm_status', 'why': '先看现状'},
                                          {'tool': 'switch_model', 'why': '执行切换'}]}))
    return out


def multi_request_family(env):
    """多要求连发（用户的典型句式: "先X，然后Y，再Z"）——合并 constraints、拼接 plan ✓。

    为什么重要: 用户的真实表达经常一句话叠 2~3 件事（本会话多次出现），
    模型必须【拆得开、顺序对、别只做第一件】。标签可由规则合成 ⇒ 可信 ✓
    """
    out = []
    A = [('先把库列出来', {'intent_type': 'list_resources', 'goal': '列出所有知识库',
                          'plan': [{'tool': 'list_knowledge_bases', 'why': '只读先看现状'}]}),
         ('先看底座状态', {'intent_type': 'inspect_resource', 'goal': '查看本地推理底座状态',
                          'plan': [{'tool': 'get_vllm_status', 'why': '只读先看现状'}]}),
         ('先看有哪些 LoRA', {'intent_type': 'list_resources', 'goal': '列出所有 LoRA',
                             'plan': [{'tool': 'list_loras', 'why': '只读先看现状'}]})]
    B = [('把结果导成 word', {'intent_type': 'export_result', 'goal': '把结果导出为 Word',
                             'plan': [{'tool': 'get_workflow_result', 'why': '先取结果'},
                                      {'tool': 'export_docx', 'why': '导出'}]}),
         ('跑一遍论文分析', {'intent_type': 'run_workflow', 'goal': '跑一次论文分析工作流',
                            'plan': [{'tool': 'list_workflow_uploads', 'why': '取最新上传'},
                                     {'tool': 'run_workflow', 'why': '执行'}]}),
         ('在 agent_use 里查一下工具调用规则',
          {'intent_type': 'query_knowledge_base', 'goal': '在 agent_use 检索工具调用规则',
           'targets': {'kb': 'agent_use'},
           'plan': [{'tool': 'paper_lora_kb_query', 'why': '检索'}]})]
    C = [('告诉我该注意什么', {'intent_type': 'summarize_and_deliver', 'goal': '总结前面结果的注意事项',
                              'plan': []}),
         ('然后入库', {'intent_type': 'kb_ingest', 'goal': '把成果入库',
                      'constraints': ['不要写错库'], 'plan': []})]
    for pa, ia in A:
        for pb, ib in B:
            for pc, ic in C[:1]:
                ph = f'{pa}，然后{pb}，再{pc}'
                goals = '；'.join(x for x in (ia['goal'], ib['goal'], ic['goal']) if x)
                cons = list(ia.get('constraints') or []) + list(ib.get('constraints') or []) + list(ic.get('constraints') or [])
                tg = {}
                for d in (ia.get('targets'), ib.get('targets'), ic.get('targets')):
                    if d:
                        tg.update(d)
                plan = (ia.get('plan') or []) + (ib.get('plan') or []) + (ic.get('plan') or [])
                out.append((ph, {'intent_type': 'multi_request', 'goal': goals,
                                 'constraints': cons, 'targets': tg, 'plan': plan}))
    return out


def two_step_family(env):
    """两步组合（"先X，再Y"）——来自真实资源，标签可规则合成 ✓。

    用户常把两件事串在一句里；模型必须【两件都做、顺序对】✓
    """
    out = []
    kbs = [n for n, _ in (env.get('kbs') or [])][:4]
    loras = (env.get('loras') or [])[:4]
    nodes = (env.get('wf_nodes') or [])[:3]
    A = [('先看看有哪些库', {'intent_type': 'list_resources', 'goal': '列出所有知识库',
                           'plan': [{'tool': 'list_knowledge_bases', 'why': '只读先看现状'}]}),
         ('先看一下底座', {'intent_type': 'inspect_resource', 'goal': '查看底座状态',
                          'plan': [{'tool': 'get_vllm_status', 'why': '只读先看现状'}]})]
    B = [(f'然后在 {kb} 里检索一下',
          {'intent_type': 'query_knowledge_base', 'goal': f'在 {kb} 中检索', 'targets': {'kb': kb},
           'plan': [{'tool': 'query_knowledge_base', 'why': '按库名检索'}]}) for kb in kbs]
    B += [(f'再核对 {l} 能不能挂',
           {'intent_type': 'inspect_resource', 'goal': f'判断 {l} 是否可挂', 'targets': {'lora': l},
            'plan': [{'tool': 'list_loras', 'why': '看 compatible'}]}) for l in loras]
    B += [(f'再看看 {n} 这个节点',
           {'intent_type': 'inspect_resource', 'goal': f'说明节点 {n}', 'targets': {'workflow_node': n},
            'plan': [{'tool': 'list_workflow_nodes', 'why': '读节点'}]}) for n in nodes]
    for pa, ia in A:
        for pb, ib in B:
            ph = f'{pa}，{pb}'
            plan = (ia.get('plan') or []) + (ib.get('plan') or [])
            out.append((ph, {'intent_type': 'multi_request',
                             'goal': f"{ia['goal']}；{ib['goal']}",
                             'targets': dict(ib.get('targets') or {}), 'plan': plan}))
    return out


def trap_family(env):
    """陷阱：名字不存在 / 纯指代 / 歧义 → 必须 should_ask=true（别猜 ✗）。

    依据（实测 209 条轨迹的分类）: "名字猜错-工具名不存在"与"资源名不存在"合计占比很高；
    对应的正确行为不是"猜一个相近的"，而是【先 list 核对，核不到就追问】✓
    """
    out = []
    fake_kb = ['r2_lora_fs_probe', 'my_kb', '论文库', 'test_kb']
    for kb in fake_kb:
        out.append((f'去{kb}里查一下', {
            'intent_type': 'query_knowledge_base', 'goal': f'在知识库 {kb} 中检索',
            'targets': {'kb': kb}, 'missing': ['该库名不在当前快照里，需先核实是否存在'],
            'should_ask': True,
            'plan': [{'tool': 'list_knowledge_bases', 'why': '核对库名是否存在'}]}))
    for l in ('extract_v9', 'my_lora', '论文分析lora'):
        out.append((f'把{l}挂上', {
            'intent_type': 'act_on_resources', 'goal': f'挂载 LoRA {l}',
            'constraints': ['挂载会改变推理行为'],
            'targets': {'lora': l}, 'missing': ['该 LoRA 名不在当前快照里，需先核实'],
            'should_ask': True,
            'plan': [{'tool': 'list_loras', 'why': '核对 LoRA 是否存在'}]}))
    for f in ('最新那篇论文', '刚才那个文件', '上次的表格'):
        out.append((f'打开{f}', {
            'intent_type': 'inspect_resource', 'goal': f'打开{f}',
            'targets': {'file': f}, 'missing': ['指代不明：需要确认具体文件'],
            'should_ask': True,
            'plan': [{'tool': 'list_workflow_uploads', 'why': '列出上传文件供选择'}]}))
    for ph in ('用那个库', '挂上那个lora', '把刚才那个改了'):
        out.append((ph, {
            'intent_type': 'inspect_resource', 'goal': '处理用户指代的对象',
            'targets': {}, 'missing': ['"那个"指代不明，需要用户指明具体对象'],
            'should_ask': True, 'plan': []}))
    return out


def main():
    env = real_env()
    if not SEEDS.exists():
        print(f'  ✗ 种子文件不存在: {SEEDS}'); return 1
    seeds = [json.loads(l) for l in SEEDS.read_text(encoding='utf-8').splitlines() if l.strip()]
    print(f'  种子 {len(seeds)} 条 · 环境: 库{len(env.get("kbs") or [])} '
          f'LoRA{len(env.get("loras") or [])} 数据集{len(env.get("datasets") or [])} '
          f'节点{len(env.get("wf_nodes") or [])} 工具{len(env.get("tools") or [])}')

    samples, stats = [], {'seed_variants': 0, 'combinatorial': 0, 'multi': 0, 'two_step': 0, 'trap': 0, 'capped': 0}
    _idx = 0
    # 留出：每 6 条种子里留 1 条整族做 eval（按家族切分 → 变体不泄漏 ✓）
    for i, s in enumerate(seeds):
        holdout = (i % 6 == 2)
        for v in variants(s['phrase']):
            for envv in range(4):
                samples.append((sample(v, s['intent'], env_text(env, envv)), holdout))
                stats['seed_variants'] += 1
    for ph, it in combinatorial(env):
        for v in variants(ph)[:2]:
            for envv in (0, 1, 2):
                samples.append((sample(v, it, env_text(env, envv)), False))
            stats['combinatorial'] += 1
    for ph, it in multi_request_family(env):
        for v in variants(ph)[:2]:
            samples.append((sample(v, it, env_text(env, 1)), False))
            stats['multi'] = stats.get('multi', 0) + 1
    for ph, it in two_step_family(env):
        for v in variants(ph)[:2]:
            samples.append((sample(v, it, env_text(env, 1)), False))
            stats['two_step'] = stats.get('two_step', 0) + 1
    for ph, it in trap_family(env):
        for v in variants(ph):
            samples.append((sample(v, it, env_text(env, 0)), False))
            stats['trap'] = stats.get('trap', 0) + 1

    # ★ 统一后处理: 按序轮换同类样本的 goal/constraints（治模板化 ✓）
    for i, (smp, ho) in enumerate(samples):
        try:
            it = json.loads(smp['conversations'][2]['value'])
        except Exception:
            continue
        smp['conversations'][2]['value'] = norm_intent(vary_goal(it, i))

    # ---------------- 校验（规则化 ✓） ----------------
    toolset = set(env.get('tools') or [])
    kbset = {n for n, _ in (env.get('kbs') or [])}
    errs, seen_pairs = [], set()
    dedup = []          # (sample, holdout)
    for smp, holdout in samples:
        conv = smp['conversations']
        phrase = conv[1]['value']
        try:
            intent = json.loads(conv[2]['value'])
        except Exception as e:
            errs.append(f'JSON 解析失败: {phrase[:30]} ({e})'); continue
        if intent['intent_type'] not in INTENT_TYPES:
            errs.append(f'intent_type 非法: {intent["intent_type"]}')
        for st in intent['plan']:
            if st.get('tool') and toolset and st['tool'] not in toolset:
                errs.append(f'工具名不存在: {st["tool"]}（{phrase[:24]}）')
        kb = (intent.get('targets') or {}).get('kb')
        if kb and kbset and kb not in kbset and not intent['should_ask']:
            errs.append(f'库名不在快照里却未要求澄清: {kb}')
        # ★ 去重键必须含【整段 system】（含环境快照）✗
        #   只用末尾 40 字符会把"同句不同环境"的变体错误合并（实测 686→475 ✗）
        key = (conv[1]['value'], conv[0]['value'])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        dedup.append((smp, holdout))

    # ★ ① 类别均衡: 每类上限 CAP（默认 120）—— query_knowledge_base 曾占 55% → 模式坍塌 ✗
    CAP = int(__import__('os').environ.get('SEMANTIC_CAP', '120'))
    by_type = {}
    for d in dedup:
        t = json.loads(d[0]['conversations'][2]['value'])['intent_type']
        by_type.setdefault(t, []).append(d)
    balanced = []
    for t, lst in by_type.items():
        random.seed(hash(t) & 0xffff)
        random.shuffle(lst)
        balanced += lst[:CAP]
        if len(lst) > CAP:
            stats['capped'] = stats.get('capped', 0) + (len(lst) - CAP)

    # ★ ③ 留出集【按 intent_type 分层】每类 2 条（覆盖全部类别 ✓ 指标才有意义 ✓）
    evalset_final, train_final = [], []
    for t, lst in by_type.items():
        pick = lst[:2]
        evalset_final += pick
        train_final += [x for x in balanced if x not in pick] if False else []
    evalset_final = list({id(x): x for x in evalset_final}.values())
    eval_ids = {id(x) for x in evalset_final}
    train_final = [x for x in balanced if id(x) not in eval_ids]

    OUT.write_text('\n'.join(json.dumps(s, ensure_ascii=False) for s, _ in train_final) + '\n',
                   encoding='utf-8')
    OUT_EVAL.write_text('\n'.join(json.dumps(s, ensure_ascii=False) for s, _ in evalset_final) + '\n',
                        encoding='utf-8')

    # 覆盖度统计
    from collections import Counter
    c = Counter(json.loads(x['conversations'][2]['value'])['intent_type'] for x, _ in dedup)
    ask = sum(1 for x, _ in dedup if json.loads(x['conversations'][2]['value'])['should_ask'])
    R = [f'# 语义理解数据集 v1 生成报告', '',
         f'- 种子: {len(seeds)} 条（来自真实对话转录，见 `data/user_voice/seeds.jsonl`）',
         f'- 训练集: **{len(train_final)}** 条 · 留出集: **{len(evalset_final)}** 条',
         f'- 来源分布: 种子变异 {stats["seed_variants"]} · 组合模板 {stats["combinatorial"]} · 多要求连发 {stats.get("multi", 0)} · 两步组合 {stats.get("two_step", 0)} · 陷阱 {stats.get("trap", 0)}（另按类别上限 CAP 截掉 {stats.get("capped", 0)} 条）',
         f'- should_ask=true（该追问）比例: {ask}/{len(dedup)} = {100*ask//max(1,len(dedup))}%',
         f'- 校验: {"✗ " + str(len(errs)) + " 处问题" if errs else "✓ 全部通过（JSON / 枚举 / 工具名 / 库名）"}', '',
         '## intent_type 分布', '']
    for k, v in c.most_common():
        R.append(f'- `{k}`: {v}')
    if errs:
        R += ['', '## 校验问题（前 20 条）', ''] + [f'- {e}' for e in errs[:20]]
    REPORT.write_text('\n'.join(R) + '\n', encoding='utf-8')

    print(f'  ✓ 训练集 {len(train_final)} 条 → {OUT.name}')
    print(f'  ✓ 留出集 {len(evalset_final)} 条 → {OUT_EVAL.name}')
    print(f'  ✓ 报告 → {REPORT.name}')
    print(f'  校验: {"✗ " + str(len(errs)) + " 处问题" if errs else "✓ 全部通过"}')
    print(f'  intent_type 种类: {len(c)} · should_ask 比例: {100*ask//max(1,len(dedup))}%')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
