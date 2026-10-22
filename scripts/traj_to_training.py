#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""traj_to_training.py — 真实轨迹 → 训练数据（P1-1 · 2026-10-21）

## 为什么不用 LLM 蒸馏 ✓✓
  轨迹里**本来就有**真实工具调用 ✓：
    steps[i].tool_calls[j] = {tool, input, result, status} ✓
  ⇒ 直接转成 sharegpt 格式 ✓ **真实数据** ✓ 不是合成 ✗
  ⇒ 零 API 成本 ✓ 零幻觉风险 ✓

## 输出格式（满足格式闸门 ✓）
  conversations = [
    {from: human, value: task},                        # idx 0 ✓ human ✓
    {from: function_call, value: '{"name":..,"arguments":{..}}'},  # idx 1 ✓
    {from: observation, value: result},                # idx 2 ✓
    ...（可多轮 ✓）
    {from: gpt, value: final},                         # 最后 ✓ gpt ✓
  ]
  system: 顶层 ✓
  tools: JSON 字符串 ✓

## 过滤（保质量 ✓）
  · 必须有 ≥1 个成功的 tool_call ✓
  · 必须有 final 答案 ✓
  · observation 不能是空/错误 ✓
  · 去重（task 相同只留一条 ✓）
  · 长度限制（总字符 ≤ 6000 ✓ p95 内 ✓）
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent   # ★ 修: 脚本在 scripts/ ⇒ 上两级才是项目根 ✓
TRAJ_DIR = ROOT / 'data' / 'trajectories'
OUT = ROOT / 'training_data' / 'flywheel_toolcall_v1.jsonl'

SYSTEM = ('你是顿悟（Luxy）平台的专家工具秘书。'
          '遇到平台相关任务时，优先调用工具获取真实数据，不要凭记忆编造。'
          '计算类问题必须用仿真节点，学术/依赖类必须先检索或开浏览器查证。')

# 工具白名单（92 个工具的 paper_lora_ 前缀版本 + 原名 ✓）
def load_tool_whitelist() -> set:
    try:
        d = json.loads((ROOT / 'agent_kb' / '_tool_docs.json').read_text(encoding='utf-8'))
        names = set((d.get('tools') or {}).keys())
        return names
    except Exception:
        return set()


BAD_RESULT = re.compile(
    r'^(?:error|failed|Error|Traceback|None|null|\s*$)'
    r'|(?:not found|Connection refused|timeout|超时|失败|不存在)', re.I)


def norm_task(s: str) -> str:
    return re.sub(r'\s+', '', str(s or ''))[:60]


def clean_result(s) -> str:
    if s is None:
        return ''
    if not isinstance(s, str):
        try:
            s = json.dumps(s, ensure_ascii=False)
        except Exception:
            s = str(s)
    s = s.strip()
    # 截断超长结果 ✓（保留前 1200 字 ✓）
    if len(s) > 1200:
        s = s[:1200] + '…（截断）'
    return s


def convert_one(traj: dict, whitelist: set, name_map: dict | None = None) -> dict | None:
    """一条轨迹 ⇒ 一条训练样本 ✓ 不合格返回 None ✓"""
    task = str(traj.get('task') or '').strip()
    final = str(traj.get('final') or '').strip()
    steps = traj.get('steps') or []

    if not task or not final:
        return None
    if len(task) < 8 or len(task) > 800:
        return None

    conv = [{'from': 'human', 'value': task}]
    n_ok_tools = 0

    for st in steps:
        if not isinstance(st, dict):
            continue
        # ① assistant 的文本（如果有 ✓ 作为过渡 ✓ 但格式闸门只认 gpt/function_call ✗）
        #   ⇒ 文本只在**没有 tool_calls** 时用 ✓ 且有最终答案 ✓
        tcs = st.get('tool_calls') or []
        if not tcs:
            continue
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            tool = str(tc.get('tool') or '').strip()
            if not tool:
                continue
            # 白名单校验（容忍 paper_lora_ 前缀 ✓）
            base = tool[len('paper_lora_'):] if tool.startswith('paper_lora_') else tool
            # ★ 修 R-7（2026-10-21）: **先查工具名映射** ✓
            #   实测: 轨迹里叫 kb_info ✗ 白名单里叫 get_knowledge_base ✓
            #   原来不查映射 ⇒ **589 条全不合格** ✗✗
            if name_map and base not in whitelist:
                base = name_map.get(base, base)
            if whitelist and base not in whitelist:
                continue
            args = tc.get('input')
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            result = clean_result(tc.get('result'))
            if not result or BAD_RESULT.search(result):
                continue
            status = str(tc.get('status') or '').lower()
            if status and status not in ('completed', 'success', 'ok'):
                continue
            # ② function_call ✓
            conv.append({'from': 'function_call',
                         'value': json.dumps({'name': base, 'arguments': args},
                                             ensure_ascii=False)})
            # ③ observation ✓
            conv.append({'from': 'observation', 'value': result})
            n_ok_tools += 1
            if len(conv) >= 12:      # 最多 5 轮工具调用 ✓（防超长 ✓）
                break
        if len(conv) >= 12:
            break

    if n_ok_tools < 1:
        return None

    # ④ 最后必须是 gpt ✓
    conv.append({'from': 'gpt', 'value': final})

    # ⑤ F2 校验（★ 2026-10-21 修: 原来**写反了** ✗✗）
    #   实际构建: [0]human [1]function_call [2]observation ...
    #   ⇒ **偶数位**应 human/observation ✓ **奇数位**应 gpt/function_call ✓
    #   原来写成 偶数=gpt ✗ 奇数=observation ✗ ⇒ **每个样本都被拒** ✗✗
    for i, c in enumerate(conv):
        if i % 2 == 0 and c['from'] not in ('human', 'observation'):
            return None
        if i % 2 == 1 and c['from'] not in ('gpt', 'function_call'):
            return None

    # ⑥ F4: 最后必须是 gpt ✓
    if conv[-1]['from'] != 'gpt':
        return None

    # ⑦ 长度限制 ✓
    total = sum(len(c['value']) for c in conv)
    if total > 6000:
        return None

    rec = {
        'system': SYSTEM,
        'conversations': conv,
        'tools': '[]',      # 空工具表 ⇒ 模型只学"该调什么"不学 schema ✓
        'meta': {
            'source': 'real_trajectory',
            'session_id': str(traj.get('session_id') or '')[:60],
            'n_tool_calls': n_ok_tools,
            'ts': str(traj.get('ts') or ''),
            'provider': str(traj.get('provider') or ''),
        },
    }
    return rec


def main() -> int:
    whitelist = load_tool_whitelist()
    # ★ 修 R-7: 加载工具名映射 ✓
    name_map = {}
    try:
        _mp = ROOT / "data" / "flywheel" / "tool_name_map.json"
        if _mp.exists():
            name_map = json.loads(_mp.read_text(encoding="utf-8")).get("name_map") or {}
            print("    工具名映射: %d 个 ✓" % len(name_map))
    except Exception as _em:
        print("    ⚠ 映射加载失败: %s" % _em)
    print('  工具白名单: %d 个' % len(whitelist))

    files = sorted(TRAJ_DIR.glob('*.jsonl'))
    print('  轨迹文件: %d 个' % len(files))

    seen = set()
    out = []
    stats = {'total': 0, 'bad': 0, 'dup': 0, 'ok': 0, 'tools': 0}

    for f in files:
        for line in f.open(encoding='utf-8', errors='replace'):
            line = line.strip()
            if not line:
                continue
            try:
                traj = json.loads(line)
            except Exception:
                continue
            stats['total'] += 1
            try:
                rec = convert_one(traj, whitelist)
            except Exception:
                rec = None
            if rec is None:
                stats['bad'] += 1
                continue
            key = norm_task(traj.get('task'))
            if key in seen:
                stats['dup'] += 1
                continue
            seen.add(key)
            out.append(rec)
            stats['ok'] += 1
            stats['tools'] += rec['meta']['n_tool_calls']

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as fo:
        for r in out:
            fo.write(json.dumps(r, ensure_ascii=False) + '\n')

    print('')
    print('  ============ 结果 ============')
    print('    轨迹总数:   %d' % stats['total'])
    print('    不合格:     %d' % stats['bad'])
    print('    重复:       %d' % stats['dup'])
    print('    **合格样本: %d** ✓' % stats['ok'])
    if out:
        print('    平均工具数: %.1f' % (stats['tools'] / stats['ok']))
    print('    产物: %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
