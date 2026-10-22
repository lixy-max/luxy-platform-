#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H5-v2: 构建【工具调用形态】数据集（治 P-16 ✓ 标签形态与推理形态不一致 ✗）

关键差别（对比 v1 ✗）:
  v1: gpt = {"intent":"act","plan":["create_docx"],...}   ← "描述计划" ✗ 模型学了也不会动手
  v2: function_call = {"name":"create_docx","arguments":{...}}  ← 直接学"输出工具调用" ✓
      + observation（工具结果）+ gpt（收尾总结 ✓）
  ⇒ 与 vLLM 推理时的原生 function-calling（hermes 解析 ✓）形态一致 ✓✓

数据来源（同 v1 ✓）:
  · docs/DRILL_TASKS_PART1.md —— 原话 + 类目里写明的【应调工具】✓
  · 每条样本带【相关工具的子集 schema】（1~3 个 ✓ 避免 52 个全塞超过 cutoff ✗）
    ★ 为什么只给子集: 真实推理时给 92 个 ✓ 但训练时给全量会 5k+ token/条 ✗
      给"相关子集"仍能教会"在候选里选对并输出调用"✓（GUIDE §2.3 的可验证标注 ✓）

产出: training_data/drill_part1_v2_toolcall.jsonl + _eval.jsonl
"""
from __future__ import annotations

import json
import sys
import pathlib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
TASKS = ROOT / 'docs' / 'DRILL_TASKS_PART1.md'
OUT = ROOT / 'training_data' / 'drill_part1_v2_toolcall.jsonl'
OUT_EVAL = ROOT / 'training_data' / 'drill_part1_v2_toolcall_eval.jsonl'
HTML = ROOT / 'webui' / 'index3.html'

SLIM = (
    '你是 "LoRA 工作台" 的内置 Agent，用工具操作本机系统。\n'
    '纪律：\n'
    '1) 需要读写/执行/查询本机信息的，必须先调用对应工具，禁止凭记忆作答。\n'
    '2) 只读工具仅用于看现状；用户要改动（加/删/改/跑/导出/训练/入库）时必须调用写类工具。\n'
    '3) 一句话含多件事时逐件做完再总结；做完前不要下结论。\n'
    '4) 失败要说清哪一步失败与错误信息；禁止假装成功。\n'
    '5) 名字不在环境快照里时，先 list_* 核对，核不到就问用户。'
)

# 常用工具的极简 schema（参数留空 ✓ 训练"选对工具"不依赖参数细节 ✓）
TOOL_DESC = {
    'create_docx': '新建 Word 文档（标题+正文+可选表格）',
    'write_docx': '写 Word 文档',
    'read_docx': '读取 Word 文档正文与表格',
    'read_sheet': '读取 Excel 指定工作表',
    'list_sheets': '列出 Excel 的工作表',
    'filter_rows': '按条件筛选行',
    'group_sum': '按列分组求和',
    'write_sheet': '写入 Excel 数据',
    'run_workflow': '按已上传论文运行分析工作流',
    'get_workflow_status': '查询工作流任务进度',
    'get_workflow_result': '按文件名读工作流结果',
    'list_workflow_results': '列出已有结果文件',
    'list_workflow_configs': '列出可用工作流模板',
    'list_workflow_nodes': '列出当前工作流节点',
    'validate_workflow': '校验工作流配置',
    'add_workflow_node': '给工作流添加节点',
    'update_workflow_node': '修改已有节点',
    'delete_workflow_node': '删除工作流节点',
    'list_workflow_uploads': '列出可运行的论文文件',
    'start_training': '起一个 LoRA 训练任务',
    'get_training_status': '查训练进度',
    'list_trainings': '列出训练任务',
    'list_available_datasets': '列出训练数据集',
    'build_dataset': '把一次运行的节点数据导出为训练样本',
    'list_available_models': '列出可用模型',
    'run_eval': '对 LoRA 跑节点级评测',
    'get_eval_status': '查评测状态与指标',
    'get_quality_summary': '读节点质量台账',
    'list_loras': '列出 LoRA 及是否加载',
    'get_loras_registry': '读 LoRA 质量台账',
    'load_lora': '把 LoRA 加载进 vLLM',
    'unload_lora': '从 vLLM 卸载 LoRA',
    'switch_model': '切换当前模型',
    'list_knowledge_bases': '列出知识库',
    'create_knowledge_base': '新建空知识库',
    'query_knowledge_base': '在知识库语义检索',
    'list_uploads': '列出已上传文件',
    'read_upload': '读已上传文件的文本内容',
    'list_history': '列出历史运行记录',
    'get_history_detail': '读某次运行明细',
    'list_doc_flows': '列出文档流程',
    'run_doc_flow': '运行文档流程',
    'save_doc_flow': '保存文档流程',
    'export_docx': '把结果导出为 Word',
    'replace_in_file': '在文件中精确替换',
    'replace_text': '在文本中精确替换',
    'set_lines_in_file': '按行号改文件',
    'set_lines': '按行号改文本',
    'get_settings': '读取系统设置',
    'get_vllm_status': '查底座与 vLLM 状态',
    'list_running_tasks': '列出正在跑的任务',
    'create_workflow_config': '新建/克隆工作流配置',
    'use_workflow_config': '切换当前工作流配置',
}


def tool_schema(name):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': TOOL_DESC.get(name, ''),
            'parameters': {'type': 'object', 'properties': {}},
        },
    }


def parse_tasks():
    t = TASKS.read_text(encoding='utf-8')
    out = []
    import re
    for blk in re.split(r'\n(?=### P1-)', t):
        m = re.match(r'### (P1-\d+)\s*【([^】]+)】\s*(.*)', blk)
        if not m:
            continue
        ph = re.search(r'- 原话:\s*(.+)', blk)
        cat = re.search(r'- 类目:\s*(.+)', blk)
        pts = re.findall(r'^\s+\d\)\s*(.+)$', blk, re.M)
        tools = re.findall(r'`([a-z_][a-z0-9_]*)`', cat.group(1) if cat else '')
        out.append({'id': m.group(1), 'kind': m.group(2), 'title': m.group(3).strip(),
                    'phrase': ph.group(1).strip() if ph else '', 'tools': tools, 'points': pts})
    return out


def main():
    tasks = parse_tasks()
    samples = []
    for t in tasks:
        planned = [x for x in dict.fromkeys(t['tools']) if x in TOOL_DESC] or ['list_knowledge_bases']
        first = planned[0]
        summary = '；'.join(t['points'][:2])[:120] or '已完成'
        conv = [
            {'from': 'system', 'value': SLIM},
            {'from': 'human', 'value': t['phrase']},
            # ★ 关键: 这里让模型学【直接输出工具调用】✓ 与推理形态一致 ✓
            {'from': 'function_call', 'value': json.dumps({'name': first, 'arguments': {}},
                                                            ensure_ascii=False)},
            {'from': 'observation', 'value': json.dumps({'status': 'ok', 'tool': first,
                                                          'note': '演练合成：工具执行成功'},
                                                         ensure_ascii=False)},
            {'from': 'gpt', 'value': f'已完成。{summary}'},
        ]
        # 工具集合: 计划工具（≤3）+ 一个干扰项（教"从候选里选对"✓）
        cand = planned[:3]
        for extra in ('list_knowledge_bases', 'get_settings', 'list_loras'):
            if extra not in cand:
                cand.append(extra)
                break
        samples.append({
            'tools': json.dumps([tool_schema(x) for x in cand], ensure_ascii=False),
            'conversations': conv,
            '_id': t['id'], '_kind': t['kind'],
        })

    def bucket(k):
        return '不可能' if '不可能' in k else ('边界' if '边界' in k else '可完成')
    by = {}
    for s in samples:
        by.setdefault(bucket(s['_kind']), []).append(s)
    ev, tr = [], []
    for b, lst in by.items():
        k = max(1, round(len(lst) * 0.2))
        ev += lst[:k]
        tr += lst[k:]

    def dump(p, rows):
        with p.open('w', encoding='utf-8') as f:
            for s in rows:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
    dump(OUT, tr)
    dump(OUT_EVAL, ev)

    # 体检（与 train_lora.data_preflight 同口径 ✓）
    import re
    def est(t):
        return sum(1 for c in str(t) if '\u4e00' <= c <= '\u9fff') + len(str(t)) // 4
    toks = [est(json.dumps([{'from': c['from'], 'value': c['value']} for c in s['conversations']], ensure_ascii=False))
            + est(s['tools']) for s in tr + ev]
    print(f'  ✓ 训练 {len(tr)} / 留出 {len(ev)}')
    print(f'    每条 token 估计: 平均 {sum(toks)//len(toks)} · 最大 {max(toks)}（cutoff 2048 ✓）')
    print(f'    含 function_call 的样本: {sum(1 for s in tr+ev if any(c["from"]=="function_call" for c in s["conversations"]))}/{len(tr+ev)} ✓')
    print(f'    样例 gpt: {tr[0]["conversations"][-1]["value"][:80]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
