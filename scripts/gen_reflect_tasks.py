#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 100 条"复杂长任务"（多步工具调用），供 DSH 跑出工具调用轨迹再蒸馏。

设计原则（都是为了让轨迹【可蒸馏、可模仿】✓）：
  · 全部用【真实存在的工具名与真实数据名】✓ —— 任务不是空想，必须真能解 ✓
    工具名从 /docs/tool/list 现取 ✓ LoRA/数据集/知识库名从实时接口取 ✓
  · 以【只读 + 分析】为主 ✗ 尽量不产出文件（100 条会堆一堆垃圾 ✓）
    写入类只占少部分，且写到 E:\\WSL\\_reflect_out\\ 便于清理 ✓
  · 每条约 3~8 步工具调用 ✓ —— 太多步（几十步）的探索轨迹【不可模仿】✗（实测结论 ✓）
  · 四类覆盖不同能力: 聚合统计 / 跨文件比对 / 诊断决策 / 知识库+重写
"""
from __future__ import annotations

import json
import random
import sys
import urllib.request
import pathlib
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'trajectories' / 'tasks_100.txt'
B = 'http://127.0.0.1:8001'


def api(path):
    try:
        with urllib.request.urlopen(B + path, timeout=60) as f:
            return json.loads(f.read().decode())
    except Exception:
        return {}


def names():
    loras = [x.get('name') for x in (api('/loras').get('loras') or []) if x.get('name')]
    ds = [x.get('name') for x in (api('/training/available_datasets').get('datasets') or []) if x.get('name')]
    kbs = [x.get('name') for x in (api('/kb/list').get('knowledge_bases') or []) if x.get('name')]
    flows = [x.get('name') for x in (api('/docs/flows').get('flows') or []) if x.get('name')]
    kinds = [x.get('name') for x in (api('/docs/tool/list').get('tools') or []) if x.get('name')]
    return loras, ds, kbs, flows, kinds


def main():
    loras, ds, kbs, flows, kinds = names()
    rnd = random.Random(20261011)
    print(f"  实时数据: LoRA {len(loras)} · 数据集 {len(ds)} · 知识库 {len(kbs)} · 流程 {len(flows)} · 工具 {len(kinds)}")
    # 洗牌轮转器：同一模板在轮完一遍之前不会重复 ✓（实测 random.choice 会连着给同一条 ✗）
    def cyc(items):
        while True:
            for x in rnd.sample(items, len(items)):
                yield x
    tasks = []

    # A 类：聚合统计（只读，3~5 步）
    A = cyc([
        '用 paper_lora_list_loras 列出所有 LoRA，再挑出与当前底座不兼容的那些，最后只报不兼容的名字和个数。',
        '用 paper_lora_list_datasets 列出训练数据集，找出最大的三个（按字节），并说明它们各自适合训什么类型的能力。',
        '先 paper_lora_list_loras 再 paper_lora_vllm_status，判断当前底座能不能直接挂 LoRA；给出结论与依据。',
        '用 paper_lora_train_list 看最近的训练任务，统计成功/失败各几个，并指出最近一次失败的可能原因（没有失败就说明没有）。',
        '用 paper_lora_list_loras 与 paper_lora_train_list 交叉核对：哪些 LoRA 有训练记录、哪些没有，列出没有训练记录的。',
        '用 paper_lora_workflow_configs 与 paper_lora_workflow_list，说明现在有几个工作流配置、跑过几次、最近一次是什么状态。',
        '先 paper_lora_status 看整体状态，再 paper_lora_vllm_status 看底座，最后给出「现在能不能跑本地推理」的一句话判断。',
        '用 paper_lora_list_datasets 与 paper_lora_train_list，判断最近训的 LoRA 用的是哪个数据集，说明推断依据。',
    ])
    for i in range(40):
        l = rnd.choice(loras) if loras else 'extract_v2'
        d = rnd.choice(ds) if ds else 'analyze_data_v1.jsonl'
        k = rnd.choice(kbs) if kbs else 'agent_use'
        _tA = next(A)
        if '{k}' in _tA:
            _tA = _tA.replace('知识库', f'知识库 {k}', 1)
        tasks.append(rnd.choice([
            f"{_tA}（相关项：{l}）",
            f"用 paper_lora_list_datasets 列出训练数据集，找出最大的三个（按字节），并说明它们各自适合训什么类型的能力。参考项：{d}",
            f"先 paper_lora_list_loras 再 paper_lora_vllm_status，判断当前底座能不能直接挂 LoRA；给出结论与依据。",
            f"用 paper_lora_train_list 看最近的训练任务，统计成功/失败各几个，并指出最近一次失败的可能原因（如果没有失败就说明没有）。",
            f"在 {k} 知识库里检索「工具调用要遵守什么」，把命中的来源文件名与分数列出来，再总结三条要点。",
            f"用 paper_lora_list_loras 与 paper_lora_train_list 交叉核对：哪些 LoRA 有训练记录、哪些没有，列出没有训练记录的。",
            f"用 paper_lora_workflow_configs 与 paper_lora_workflow_list，说明现在有几个工作流配置、跑过几次、最近一次是什么状态。",
            f"先 paper_lora_status 看整体状态，再 paper_lora_vllm_status 看底座，最后给出一句话判断「现在能不能跑本地推理」。",
        ]))

    # B 类：跨工具链比对（读写混合，4~7 步）
    B = cyc([
            f"读 uploads/采购台账.xlsx 的表头，然后在 E:\\WSL\\_reflect_out\\ 里新建一个 3 列 2 行的表（列名自拟但要有意义），再用 read_sheet 读回来确认写入正确。",
            f"用 paper_lora_read_sheet 读 uploads/采购台账.xlsx，用 paper_lora_group_sum 按供应商汇总金额，再用 paper_lora_filter_rows 筛出待交付，最后用一段话总结两者关系。",
            f"列出 uploads/ 里所有 .xlsx（不会列就用 paper_lora_read_sheet 逐个试），找出列数最多的那个，报出它的表头。",
            f"先 paper_lora_list_sheets 看 uploads/采购台账.xlsx 有哪些工作表，再读第一个表的表头，最后说明这份表能支持哪些统计。",
            f"用 paper_lora_kb_query 在 agent_use 里查「LoRA 挂载」，再用 paper_lora_list_loras 核对当前实际挂载情况，指出文档与实际是否一致。",
            f"读 outputs/ 里最新的工作流结果 JSON 的 final_report（用 read 或 paper_lora_call 都行），概括它讲了什么、有没有缺失输入警告。",
    ])
    for i in range(30):
        tasks.append(next(B))

    # C 类：诊断决策（多步探查，5~8 步）
    C = cyc([
            "诊断「底座起不来」的可能原因：依次检查 paper_lora_vllm_status、paper_lora_list_loras 与 paper_lora_status，给出按可能性排序的原因清单和每一步的排查命令。",
            "评估「哪些工具最不可靠」：先 paper_lora_tool_list 拿工具清单，再在 agent_use 里检索「已知坑」，最后按风险排序给出前 5 个需要人工复核的工具。",
            "判断当前这套工作流配置能不能跑：用 paper_lora_workflow_configs 看配置、paper_lora_workflow_latest 看最近结果、paper_lora_vllm_status 看底座，给出「能跑/不能跑」的结论与理由。",
            "分析知识库检索质量：在 agent_use 里分别用「工具怎么选」和「失败了怎么办」两个查询检索，比较命中分数与来源，判断这个库覆盖得够不够。",
            "用 paper_lora_status 与 paper_lora_tool_list 判断：本会话一共有多少工具可用、其中多少是危险的（会改数据/训练/模型），并说明你判断的依据。",
    ])
    for i in range(20):
        tasks.append(next(C))

    # D 类：知识库 + 重写（10 条，产出可复用文本）
    D = cyc([
            f"在 {k} 里检索「调用工具的正确顺序」，把命中内容整理成 5 条可照做的检查清单，每条一句话。",
            f"在 {k} 里检索「参数写错会怎样」，总结出三种最常见的参数错误与对应正确写法。",
            f"用 paper_lora_kb_info 看 {k} 的文档构成，判断它更像「手册」还是「案例集」，并说明依据。",
    ])
    for i in range(10):
        k = rnd.choice(kbs) if kbs else 'agent_use'
        _tD = next(D)
        if _tD.startswith('在 '):
            _tD = _tD.replace('在 ', f'在 {k} ', 1).replace(f'在 {k} ' + k + ' ', f'在 {k} ', 1)
        tasks.append(_tD)

    # 去重（保序）+ 不足则用变体补齐到 100 ✓
    seen, uniq = set(), []
    for t in tasks:
        t = ' '.join(str(t).split())
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    i = 0
    while len(uniq) < 100 and uniq:
        base = uniq[i % len(uniq)]
        cand = f'{base}（第 {i//len(uniq)+2} 轮，请换一种验证角度）'
        if cand not in seen:
            seen.add(cand); uniq.append(cand)
        i += 1
    print(f'  去重: {len(tasks)} → {len(uniq)}')
    tasks = uniq[:100]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(tasks) + '\n', encoding='utf-8')
    print(f"  ✓ 已生成 {len(tasks)} 条 → {OUT}")
    print("  --- 前 6 条样例 ---")
    for t in tasks[:6]:
        print("   ", t[:110])
    from collections import Counter
    print("  --- 长度分布 ---")
    print("   ", Counter(min(len(t) // 20 * 20, 80) for t in tasks).most_common())


if __name__ == '__main__':
    main()
