#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tool_retriever.py — 动态关键词工具检索（T-4 · 2026-10-21）

★ 目标: 不再给模型全部 92 个工具 ✗ 而是按用户这句话【动态选】相关工具 ✓
★ 依据: 8,574 字符 ≈ 4,287 token 的 schema 占 32K 上下文 13% ✗
        筛到 30 个省 70% ✓ 筛到 15 个省 83% ✓

★ 设计（域优先，比纯词匹配稳健 ✓）:
  ① 核心工具（总是包含 ✓ 只读/状态类 15 个）
  ② 域工具（按关键词命中域 ⇒ 加该域全部工具 ✓）
  ③ 无域命中 ⇒ 退回全量 ✓（安全兜底 ✗ 不许让模型"看不见"必需工具 ✗）
  ④ 上限 top-K（默认 30 ✓）

★ 与前端的一致性: 前端 index3.html 的 buildToolsSchema 用同一套规则 ✓
   （Python 版用于离线验证工具覆盖率 ✓）
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

# ══════════════════════════════════════════════════════════
# 一、工具分类（按名字 + 描述自动归域 ✓ 手工补核心 ✓）
# ══════════════════════════════════════════════════════════
# 核心工具: 总是包含 ✓（只读 / 状态 / 模型管理 ✓ 不危险 ✓）
CORE_TOOLS = {
    'list_available_models', 'switch_model', 'list_loras', 'load_lora', 'unload_lora',
    'list_knowledge_bases', 'query_knowledge_base', 'get_vllm_status', 'get_settings',
    'list_running_tasks', 'list_history', 'list_workflow_configs', 'list_workflow_nodes',
    'list_sim_nodes', 'run_simulation',
}

# 域定义: 关键词 ⇒ 域 ✓
DOMAIN_KEYWORDS = {
    'workflow': [
        '工作流', '流程', '节点', '配置', 'workflow', 'config', 'node', 'validate',
        '克隆', '副本', '克隆件', '加节点', '删节点', '改节点', '画布', '依赖',
        '跑一下', '运行', '启动流程', '结果', '报告',
        # ★ 2026-10-21 补: V2-25/V2-28 漏的 ✓
        '质量', '汇总', '导出结果', '跑到哪', '完了没', '跑完没', '质量台账',
    ],
    'kb': [
        '知识库', '检索', '入库', '向量', '维度', 'kb', 'knowledge', 'embedding',
        'embed', 'query', '查一下', '搜索', '升维', '降维', '库',
    ],
    'training': [
        '训练', '微调', 'lora', '训练集', '数据集', 'dataset', 'train', 'epoch',
        'rank', '评测', 'eval', 'adapter', '全参', '专家模型',
    ],
    'doc': [
        'docx', 'word', '文档', '论文', '表格', 'excel', 'sheet', '替换', '导出',
        '导入', '目录', '润色', '校对', '纠错', '格式', '排版', '摘要', '翻译',
        '术语', '扩写', '精简', '段落', '标题', '读一下', '前 2000 字',
        # ★ 2026-10-21 补: V2-S05 漏的 ✓
        '文档流程', 'doc flow', '纠错流程', '编辑流程',
        # ★ 2026-10-21 再补: V2-05 漏的 ✓（"4096 全换成 4k"/"按行号"/"只改那一节"）
        '换成', '全换成', '替换成', '按行号', '改那一节', '只改', '前面的正文别动',
        '统一成', '统一格式',
    ],
    'file': [
        '文件', '上传', '读取', '列表', '路径', 'outputs', 'upload', 'file',
        '上传区', '最新那个',
        # ★ 2026-10-21 补: V2-06 测试漏的 ✓
        '录音', '转文字', '音频', '拷贝', '副本', 'md5', '列一下', '有哪些',
    ],
    'system': [
        '状态', '设置', '模型', '底座', '显存', '显卡', 'gpu', '温度', '功耗',
        '版本', '健康', '在跑', '在哪张卡',
    ],
}

# 域 ⇒ 工具名前缀 / 包含词 ✓（用于归类工具 ✓）
DOMAIN_TOOL_PATTERNS = {
    'workflow': ['workflow', 'node', 'validate', 'quality', 'summary', 'results', 'history'],
    'kb': ['knowledge_base', 'kb_', '_kb'],
    'training': ['train', 'dataset', 'eval', 'lora', 'simulation', 'sim_'],
    'doc': ['docx', 'sheet', 'doc_', 'docflow', 'toc', 'polish', 'condense', 'logic',
            'terminology', 'translate', 'add_content', 'format', 'structure',
            'proofread', 'replace', 'set_lines', 'export', 'excel',
            # ★ 2026-10-21 补: V2-S05 漏的 ✓
            'doc_flow', 'docflow', 'read_docx', 'write_docx'],
    'file': ['file', 'upload', 'glob', 'grep', 'uploads', 'read_upload'],
    'system': ['model', 'vllm', 'settings', 'status', 'running', 'history', 'gpu'],
}


def classify_tool(name: str, desc: str = '') -> str:
    """把一个工具归域 ✓（先按名字 ✓ 再按描述 ✓）"""
    n = (name or '').lower()
    d = (desc or '').lower()
    for dom, pats in DOMAIN_TOOL_PATTERNS.items():
        for p in pats:
            if p in n:
                return dom
    for dom, pats in DOMAIN_TOOL_PATTERNS.items():
        for p in pats:
            if p in d:
                return dom
    return 'other'


def detect_domains(query: str) -> set:
    """用户这句话命中哪些域 ✓"""
    q = str(query or '').lower()
    hit = set()
    for dom, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in q:
                hit.add(dom)
                break
    return hit


# ══════════════════════════════════════════════════════════
# 二、选择 ✓
# ══════════════════════════════════════════════════════════
def select_tools(query: str, all_tools: list | dict, top_k: int = 50) -> tuple[list, dict]:
    """动态选工具 ✓

    Args:
        query: 用户这句话 ✓
        all_tools: 全量工具（list of name 或 dict name→info ✓）
        top_k: 上限 ✓

    Returns:
        (selected_names, diag)
    """
    names = list(all_tools.keys()) if isinstance(all_tools, dict) else list(all_tools)
    descs = {}
    if isinstance(all_tools, dict):
        for n, v in all_tools.items():
            descs[n] = (v.get('description') or v.get('desc') or '') if isinstance(v, dict) else ''

    # ① 域检测 ✓
    doms = detect_domains(query)

    # ② 核心工具（存在的 ✓）
    selected = [n for n in names if n in CORE_TOOLS]

    # ③ 域工具 ✓
    dom_added = []
    if doms:
        for n in names:
            if n in selected:
                continue
            if classify_tool(n, descs.get(n, '')) in doms:
                selected.append(n)
                dom_added.append(n)

    # ④ 无域命中 ⇒ 退回全量 ✓（安全兜底 ✗ 不许漏工具 ✗）
    fallback = False
    if not doms:
        selected = list(names)
        fallback = True

    # ⑤ 上限 ✓（超了就截断 ✓ 但保留核心 ✓）
    truncated = []
    if len(selected) > top_k:
        core_part = [n for n in selected if n in CORE_TOOLS]
        rest = [n for n in selected if n not in CORE_TOOLS]
        keep_rest = rest[:max(0, top_k - len(core_part))]
        truncated = rest[len(keep_rest):]
        selected = core_part + keep_rest

    diag = {
        'query': str(query)[:80],
        'domains': sorted(doms),
        'fallback': fallback,
        'core': [n for n in selected if n in CORE_TOOLS],
        'from_domains': [n for n in selected if n not in CORE_TOOLS],
        'total': len(selected),
        'truncated': len(truncated),
        'truncated_names': truncated[:10],
    }
    return selected, diag


# ══════════════════════════════════════════════════════════
# 三、离线验证: V2 任务的期望工具覆盖率 ✓（关键 ✓）
# ══════════════════════════════════════════════════════════
def main() -> int:
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))

    print('=' * 78)
    print('  T-4 动态工具检索 · 离线验证')
    print('=' * 78)

    # ① 载入全量工具 ✓
    tools = {}
    for f in ('agent_kb/_tools.json', 'agent_kb/_tool_docs.json'):
        p = ROOT / f
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding='utf-8'))
        if f.endswith('_tools.json'):
            for k, v in d.items():
                tools[k] = v if isinstance(v, dict) else {}
        else:
            for x in (d.get('tools') or []):
                if isinstance(x, dict) and x.get('name'):
                    tools[x['name']] = x
    # 补前端 AGENT_TOOLS 里的工具名（从 index3.html 抓 ✓）
    try:
        html = (ROOT / 'webui/index3.html').read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'AGENT_TOOLS\.([a-z_0-9]+)\s*=\s*\{', html):
            tools.setdefault(m.group(1), {})
    except Exception:
        pass

    print('  全量工具: **%d 个**' % len(tools))

    # ② 域分布 ✓
    from collections import Counter
    doms = Counter(classify_tool(n, (tools[n] or {}).get('description', '') if isinstance(tools[n], dict) else '')
                   for n in tools)
    print('  域分布: %s' % dict(doms))

    # ③ schema 体积 ✓
    def schema_size(sel):
        s = json.dumps(sel, ensure_ascii=False)
        return len(s)

    all_size = schema_size(list(tools.keys()))
    print('')
    print('  全量 schema: %d 字符 ≈ %d token' % (all_size, all_size // 2))

    # ④ V2 任务覆盖率测试 ✓（关键 ✗ 不能漏必需工具 ✗）
    print('')
    print('=' * 78)
    print('  ★ V2 任务覆盖率测试（关键: 动态筛选不能漏掉必需工具 ✗）')
    print('=' * 78)

    ev2 = ROOT / 'docs/EVAL_SET_V2.json'
    if not ev2.exists():
        print('  X 找不到 EVAL_SET_V2.json')
        return 1
    d = json.loads(ev2.read_text(encoding='utf-8'))
    tasks = d.get('tasks') or []
    print('  V2 任务数: %d' % len(tasks))

    miss_total = 0
    covered = 0
    checkable = 0
    rows = []
    for t in tasks:
        want = (t.get('expect') or {}).get('tools_required') or []
        if not want:
            continue
        checkable += 1
        sel, diag = select_tools(t['phrase'], tools, top_k=50)
        missing = [w for w in want if w not in sel]
        if not missing:
            covered += 1
        else:
            miss_total += len(missing)
        rows.append({
            'id': t['id'], 'tier': t.get('tier'), 'n_want': len(want),
            'n_sel': len(sel), 'missing': missing, 'domains': diag['domains'],
            'fallback': diag['fallback'],
        })

    print('')
    print('  %-10s %-11s %-5s %-5s %-5s %s' % ('ID', 'tier', '期望', '选中', '漏', '命中域'))
    for r in rows:
        print('  %-10s %-11s %-5d %-5d %-5s %s' % (
            r['id'], r['tier'] or '', r['n_want'], r['n_sel'],
            len(r['missing']) if r['missing'] else '✓',
            ','.join(r['domains']) or ('全量兜底' if r['fallback'] else '—')))

    print('')
    print('  ★ 覆盖率: **%d / %d = %.0f%%**' % (covered, checkable, covered / max(checkable, 1) * 100))
    if miss_total:
        print('  ⚠ 漏掉的工具（必须修 ✗）:')
        for r in rows:
            if r['missing']:
                print('    %s: %s' % (r['id'], r['missing']))

    # ⑤ token 节省 ✓
    print('')
    print('=' * 78)
    print('  Token 节省（选中工具数 vs 全量 %d）' % len(tools))
    print('=' * 78)
    if rows:
        avg_sel = sum(r['n_sel'] for r in rows) / len(rows)
        print('  平均选中: %.1f 个（全量 %d）' % (avg_sel, len(tools)))
        print('  ⇒ schema 从 %d 字 ≈ %d token 降到约 %d 字 ≈ %d token（省 %.0f%%）' % (
            all_size, all_size // 2,
            int(all_size * avg_sel / len(tools)),
            int(all_size // 2 * avg_sel / len(tools)),
            (1 - avg_sel / len(tools)) * 100))
    return 0


if __name__ == '__main__':
    sys.exit(main())
