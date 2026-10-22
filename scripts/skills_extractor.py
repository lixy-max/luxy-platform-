#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skills_extractor.py — 轨迹 → 可复用做法（T-1 ★ 核心模块）

★ 依据（文献 + 开源）:
    AWM (arXiv:2409.07429, ICML'25):
      "A workflow is usually a common sub-routine in solving tasks,
       **with example-specific contexts being abstracted out**."
      ⇒ WebArena SOTA 35.6% ✓
    Mem0 (arXiv:2504.19413):
      "**Agent-generated facts are first-class** — when an agent confirms an action,
       that information is now stored with equal weight."
      ⇒ 只存【确认做到】的 ✓
    "From Knowledge to Noise" (REALM 2025) 反面:
      裸存轨迹 ⇒ 噪音 ⇒ 真实任务上性能**下降** ✗
      ⇒ 必须抽象化 + 过滤 ✓

★ 输入（轨迹）:
    {"task": "...", "reason": "completed", "final": "...",
     "steps": [{"tool_calls": [{"tool": "read", "input": {...}, "status": "completed",
                               "result": "..."}]}],
     "tools_used": [...], "tool_errors": [...]}

★ 输出（做法）:
    {"task_type": "改文档指定章节的数字",
     "trigger": ["换数字", "改章节", "只改一节"],
     "steps": ["① 定位章节起止行号", "② 只在该范围替换", "③ 校验前面正文未变"],
     "tools_ok": ["read", "write"],
     "pitfalls": ["按行号改要先确认行号"],
     "evidence": ["session-xxx"],
     "uses": 1, "success": 1}

用法:
    # 从轨迹批量提取（要 LLM）
    python scripts/skills_extractor.py --traj data/trajectories/trajectories.round2.jsonl \\
        --out data/learning/skills/skills.jsonl --limit 30

    # 规则模式（不调 LLM · 测管道 ✓）
    python scripts/skills_extractor.py --traj ... --out ... --no-llm

    # 自测
    python scripts/skills_extractor.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.request
from datetime import datetime

# ══════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════
# ★ provider 占位工具不是真工具 ✗（实测: tool_calls[].tool = "openai" 是云端兜底 ✗）
PROVIDER_TOOLS = {'openai', 'cloud', 'vllm', 'anthropic', 'deepseek', 'minimax', 'gemini'}

# 免责模板（命中 ⇒ 不是成功轨迹 ✗）
DISCLAIMER_RE = re.compile(
    r'(我无法|无法直接|无法为您|无法完成|我没有(可用的)?工具|没有可用工具|'
    r'我这边(也|都|还)?(没有|无法|拿不到|不能|没法)|'
    r'当前(环境|会话)(中)?(没有|未)|未连接|没有连接到|'
    r'请(先)?(补充|提供|告诉|明确|确认)|抱歉[，,]?\s*我|作为(一个)?(AI|语言模型))')

# 做法字段 ✓
SKILL_FIELDS = ('task_type', 'trigger', 'steps', 'tools_ok', 'pitfalls')

# 本地 vLLM
VLLM_URL = os.environ.get('VLLM_URL', 'http://127.0.0.1:8000/v1/chat/completions')
VLLM_MODELS = os.environ.get('VLLM_MODELS_URL', 'http://127.0.0.1:8000/v1/models')


# ══════════════════════════════════════════════════════════
# 一、入口收窄（L1-a ✓）
# ══════════════════════════════════════════════════════════
def collect_tool_calls(traj: dict) -> list[dict]:
    """从 steps 里抽【真工具调用】✓（排除 provider 占位 ✗）"""
    out = []
    for st in (traj.get('steps') or []):
        if not isinstance(st, dict):
            continue
        for tc in (st.get('tool_calls') or []):
            if not isinstance(tc, dict):
                continue
            name = str(tc.get('tool') or '').strip()
            if not name or name.lower() in PROVIDER_TOOLS:
                continue
            out.append({
                'tool': name,
                'input': tc.get('input') or {},
                'status': tc.get('status') or '',
                'result': str(tc.get('result') or '')[:1200],
            })
    return out


def is_success_trajectory(traj: dict) -> tuple[bool, str]:
    """只收【真做到】的轨迹 ✓（Mem0: agent 确认的动作 first-class）

    Returns: (ok, reason)
    """
    if not isinstance(traj, dict):
        return False, '不是 dict'

    # ① 状态必须完成 ✓
    reason = traj.get('reason')
    if reason not in (None, 'completed', 'success', 'done'):
        return False, 'reason=%s（未完成 ✗）' % reason
    if traj.get('error'):
        return False, '有 error ✗'

    # ② 必须有【真】工具调用 ✓（provider 占位不算 ✗）
    tcs = collect_tool_calls(traj)
    if not tcs:
        return False, '零真工具调用 ✗（provider 占位不算 ✓）'

    # ③ 成功状态的工具调用 ≥ 1 ✓
    ok_calls = [t for t in tcs if t['status'] in ('completed', 'success', 'ok', '')]
    if not ok_calls:
        return False, '所有工具调用都失败 ✗'

    # ④ 最终答案不能是免责 ✗
    final = str(traj.get('final') or '').strip()
    if not final:
        return False, 'final 为空 ✗'
    hits = DISCLAIMER_RE.findall(final)
    if hits:
        return False, 'final 含免责（命中 %d 处）✗' % len(hits)

    # ⑤ 答案不能太短（< 20 字 ⇒ 没信息 ✗）
    if len(final) < 20:
        return False, 'final 过短（%d 字）✗' % len(final)

    return True, 'ok（%d 个真工具 ✓ · final %d 字）' % (len(ok_calls), len(final))


# ══════════════════════════════════════════════════════════
# 二、★ 抽象化（L1-b · 核心 ✓）
# ══════════════════════════════════════════════════════════
EXTRACT_SYS = """你是【经验提炼器】。把下面这条【成功轨迹】提炼成【可复用的做法】。

★ 最重要的要求（AWM 论文核心）：
  **把所有"只属于这次任务"的具体内容抽象掉** ✗
  ✗ 不要出现具体文件名（如 HANDOVER.md / extract_v1.jsonl）
  ✗ 不要出现具体数字（如 4096 / 4k / 33 个）
  ✗ 不要出现具体知识库名 / LoRA 名 / 路径
  ✓ 要说"哪一类任务"和"怎么做这一类任务"

★ 输出 JSON（只输出这个，不要别的内容）：
{
  "task_type": "任务类型（≤15 字，动词开头，如「改文档指定章节的数字」）",
  "trigger": ["触发词1", "触发词2", "触发词3"],
  "steps": ["① 第一步（可套到同类任务）", "② 第二步", "③ 第三步"],
  "tools_ok": ["实测可用的工具名"],
  "pitfalls": ["这条轨迹里真实踩过的坑（没有就 []）"]
}

★ 判定标准：
  - steps 必须 2~6 步 ✓ 太少=太泛 ✗ 太多=太具体 ✗
  - trigger 是用户可能怎么说这件事的关键词 ✓
  - tools_ok 只写这条轨迹里真的调用成功的工具 ✓
"""


def build_extract_input(traj: dict, max_calls: int = 12) -> str:
    """构造给 LLM 的输入（已脱敏 · 突出可抽象的信息 ✓）"""
    tcs = collect_tool_calls(traj)
    lines = []
    lines.append('【任务】%s' % str(traj.get('task') or '')[:400])
    lines.append('')
    lines.append('【工具调用序列】')
    for i, t in enumerate(tcs[:max_calls]):
        args = json.dumps(t.get('input') or {}, ensure_ascii=False)[:200]
        lines.append('  %d. %s(%s) → %s' % (
            i + 1, t['tool'], args,
            t['result'][:200].replace('\n', ' ')))
    if len(tcs) > max_calls:
        lines.append('  ...（还有 %d 个调用）' % (len(tcs) - max_calls))
    lines.append('')
    lines.append('【最终答案】')
    lines.append(str(traj.get('final') or '')[:1200])
    # 工具错误 ⇒ pitfalls 素材 ✓
    errs = traj.get('tool_errors') or []
    if errs:
        lines.append('')
        lines.append('【踩过的错】')
        for e in errs[:5]:
            lines.append('  - %s' % str(e)[:150])
    return '\n'.join(lines)


def _llm_call(system: str, user: str, max_tokens: int = 800, timeout: int = 120) -> str:
    """调本地 vLLM ✓"""
    try:
        req = urllib.request.Request(VLLM_MODELS, method='GET')
        with urllib.request.urlopen(req, timeout=5) as r:
            mid = ((json.loads(r.read().decode()).get('data') or [{}])[0].get('id')) or 'default'
    except Exception:
        mid = 'default'
    try:
        payload = json.dumps({
            'model': mid,
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': user}],
            'max_tokens': max_tokens,
            'temperature': 0.1,
        }).encode('utf-8')
        req = urllib.request.Request(VLLM_URL, data=payload,
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        return ((d.get('choices') or [{}])[0].get('message', {}).get('content') or '').strip()
    except Exception:
        return ''


def repair_json(text: str) -> dict:
    """从 LLM 输出里抠 JSON ✓（复用 P3 的修复思路 ✓）"""
    if not text:
        return {}
    t = text.strip()
    # 去 markdown 围栏 ✓
    t = re.sub(r'^```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    # 抠第一个 { 到最后一个 } ✓
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j <= i:
        return {}
    t = t[i:j + 1]
    try:
        return json.loads(t)
    except Exception:
        # 去尾逗号 ✓
        t2 = re.sub(r',\s*([}\]])', r'\1', t)
        try:
            return json.loads(t2)
        except Exception:
            return {}


def validate_skill(d: dict) -> tuple[bool, str]:
    """校验一条做法 ✓（对齐 L1-c 质量闸门）"""
    if not isinstance(d, dict) or not d:
        return False, '空/非 dict ✗'
    tt = str(d.get('task_type') or '').strip()
    if not tt:
        return False, 'task_type 为空 ✗'
    if len(tt) > 30:
        return False, 'task_type 太长（%d 字）✗' % len(tt)
    steps = [str(s).strip() for s in (d.get('steps') or []) if str(s).strip()]
    if not steps:
        return False, '无 steps ✗'
    if len(steps) < 2:
        return False, 'steps 太少（%d < 2）⇒ 太泛 ✗' % len(steps)
    if len(steps) > 8:
        return False, 'steps 太多（%d > 8）⇒ 太具体 ✗' % len(steps)
    # 免责内容 ⇒ 不是做法 ✗
    blob = ' '.join(steps) + ' ' + tt
    if DISCLAIMER_RE.search(blob):
        return False, '做法里含免责内容 ✗'
    # 具体文件名/路径泄漏 ⇒ 没抽象干净 ✗
    if re.search(r'\.(md|jsonl|json|py|txt|docx|xlsx)\b', blob):
        return False, '做法里残留具体文件名 ✗（没抽象干净）'
    if re.search(r'[A-Za-z]:\\\\|/home/|/mnt/', blob):
        return False, '做法里残留绝对路径 ✗'
    return True, 'ok（%d 步 · task_type=%s）' % (len(steps), tt)


def extract_skill(traj: dict, use_llm: bool = True) -> dict | None:
    """一条轨迹 → 一条做法 ✓（核心 ✓）"""
    ok, why = is_success_trajectory(traj)
    if not ok:
        return None
    tcs = collect_tool_calls(traj)
    if not use_llm:
        # 规则模式（测管道 ✓ 不做真抽象 ✗）
        return {
            'task_type': '（规则模式未抽象）',
            'trigger': [],
            'steps': ['① ' + ' → '.join(t['tool'] for t in tcs[:6])],
            'tools_ok': list(dict.fromkeys(t['tool'] for t in tcs)),
            'pitfalls': [],
            'evidence': [traj.get('session_id') or ''],
        }

    out = _llm_call(EXTRACT_SYS, build_extract_input(traj), max_tokens=900)
    d = repair_json(out)
    ok2, why2 = validate_skill(d)
    if not ok2:
        return {'_error': why2, '_raw': out[:300],
                'evidence': [traj.get('session_id') or '']}
    skill = {
        'task_type': str(d.get('task_type')).strip(),
        'trigger': [str(x).strip() for x in (d.get('trigger') or []) if str(x).strip()][:6],
        'steps': [str(x).strip() for x in (d.get('steps') or []) if str(x).strip()],
        'tools_ok': [str(x).strip() for x in (d.get('tools_ok') or []) if str(x).strip()][:10],
        'pitfalls': [str(x).strip() for x in (d.get('pitfalls') or []) if str(x).strip()][:5],
        'evidence': [traj.get('session_id') or ''],
    }
    # ★ 交叉校验: tools_ok 必须与实测一致 ✓（LLM 可能瞎写 ✗）
    real = set(t['tool'] for t in tcs)
    skill['tools_ok'] = [t for t in skill['tools_ok'] if t in real] or sorted(real)[:6]
    return skill


# ══════════════════════════════════════════════════════════
# 三、做法 → chunk（L1-d ✓ 1 做法 = 1 chunk · 块首回填 ✓）
# ══════════════════════════════════════════════════════════
def skill_to_markdown(skill: dict, idx: int = 1) -> str:
    """一条做法 → 一个 markdown 块 ✓（块首就是 task_type + 触发词 ✓ 检索第一眼看到 ✓）"""
    tt = skill.get('task_type') or '（未分类）'
    trig = ' / '.join(skill.get('trigger') or []) or '（无）'
    steps = skill.get('steps') or []
    tools = ' / '.join(skill.get('tools_ok') or []) or '（无）'
    pits = skill.get('pitfalls') or []
    ev = skill.get('evidence') or []

    lines = [
        '# 做法 %d：%s' % (idx, tt),
        '',
        '**触发词**：%s' % trig,
        '',
        '**步骤**：',
    ]
    for s in steps:
        lines.append('- %s' % s)
    lines.append('')
    lines.append('**可用工具**：%s' % tools)
    if pits:
        lines.append('')
        lines.append('**注意事项**：')
        for p in pits:
            lines.append('- ⚠ %s' % p)
    if ev:
        lines.append('')
        lines.append('*证据：%s*' % ', '.join(ev[:3]))
    return '\n'.join(lines)


def skills_to_chunks(skills: list[dict]) -> list[dict]:
    """做法列表 → chunk 列表 ✓（供 KB 入库 ✓）"""
    chunks = []
    for i, sk in enumerate(skills, 1):
        md = skill_to_markdown(sk, i)
        chunks.append({
            'text': md,
            'source': 'learned_skills.md',
            'section': sk.get('task_type') or '',
            'skill_idx': i,
            'task_type': sk.get('task_type') or '',
            'trigger': sk.get('trigger') or [],
        })
    return chunks


# ══════════════════════════════════════════════════════════
# 四、去重 + 合并（L1-c 的一部分 ✓）
# ══════════════════════════════════════════════════════════
def _norm(s: str) -> str:
    return re.sub(r'\s+', '', str(s or '')).lower()


def dedupe_skills(skills: list[dict]) -> tuple[list[dict], int]:
    """按 task_type 合并 ✓（同类型 ⇒ 合并 evidence · 合并 steps 去重 ✓）"""
    merged: dict[str, dict] = {}
    dropped = 0
    for sk in skills:
        key = _norm(sk.get('task_type') or '')
        if not key:
            dropped += 1
            continue
        if key not in merged:
            merged[key] = dict(sk)
            merged[key]['uses'] = 1
            continue
        # ★ 2026-10-21 修: 合并前必须【工具集有交集】✓
        #   实测: LLM 会把不同轨迹误标成同一 task_type ✗
        #   ⇒ 硬合并会串触发词（做法 20 触发词变成"工具调用/知识库检索/总结要点" ✗）
        _prev_tools = set(merged[key].get('tools_ok') or [])
        _new_tools = set(sk.get('tools_ok') or [])
        if _prev_tools and _new_tools and not (_prev_tools & _new_tools):
            # 工具完全不相交 ⇒ 不是同一个做法 ⇒ 另起一个（加后缀 ✓）
            k2 = key + '#2'
            n = 2
            while k2 in merged:
                n += 1
                k2 = key + '#%d' % n
            merged[k2] = dict(sk)
            merged[k2]['uses'] = 1
            merged[k2]['task_type'] = str(merged[k2].get('task_type') or '') + '（变体%d）' % n
            continue
        # 合并 ✓
        m = merged[key]
        m['uses'] = m.get('uses', 1) + 1
        for k in ('trigger', 'steps', 'tools_ok', 'pitfalls', 'evidence'):
            cur = m.get(k) or []
            for x in (sk.get(k) or []):
                if x not in cur:
                    cur.append(x)
            m[k] = cur
        dropped += 1
    # ★ 2026-10-21 改进: 步骤去重（近重复合并 ✗ 原来会同一做法有 4+3 步重叠 ✗）
    def _step_key(step: str) -> str:
        """步骤指纹：去掉序号/工具名/标点后取前 18 字 ✓"""
        t = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩\s\-\*\d\.、]+', '', str(step or ''))
        t = re.sub(r'`[^`]*`', '', t)          # 去工具名 ✓
        t = re.sub(r'[\s，。；：、,.;:!?！？（）()\[\]【】]', '', t)
        return t[:18].lower()

    for m in merged.values():
        steps = m.get('steps') or []
        seen, uniq = set(), []
        for st in steps:
            k = _step_key(st)
            if not k:
                continue
            # 近重复：key 互相包含也算重复 ✓
            dup = False
            for k2 in seen:
                if k == k2 or (len(k) > 8 and (k in k2 or k2 in k)) or (len(k2) > 8 and (k in k2 or k2 in k)):
                    dup = True
                    break
            if not dup:
                seen.add(k)
                uniq.append(st)
        m['steps'] = uniq[:8]
        # 合并后步数 < 2 ⇒ 太泛 ⇒ 删掉 ✓
        if len(m['steps']) < 2:
            m['_drop'] = True
    merged = {k: v for k, v in merged.items() if not v.get('_drop')}
    return list(merged.values()), dropped


# ══════════════════════════════════════════════════════════
# 五、批量主流程
# ══════════════════════════════════════════════════════════
def load_trajs(path: pathlib.Path) -> list[dict]:
    out = []
    with path.open(encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='轨迹 → 可复用做法（T-1）')
    ap.add_argument('--traj', default='', help='轨迹 jsonl 文件')
    ap.add_argument('--out', default='data/learning/skills/skills.jsonl', help='输出路径')
    ap.add_argument('--md', default='', help='另存 markdown（可选）')
    ap.add_argument('--limit', type=int, default=30, help='最多处理多少条轨迹')
    ap.add_argument('--no-llm', action='store_true', help='规则模式（不调 LLM · 测管道）')
    ap.add_argument('--selftest', action='store_true', help='自测')
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not a.traj:
        print('X 需要 --traj 或 --selftest')
        return 1

    src = pathlib.Path(a.traj)
    if not src.is_absolute():
        src = pathlib.Path('') / src
    if not src.exists():
        print('X 轨迹文件不存在: %s' % src)
        return 1

    trajs = load_trajs(src)
    print('=' * 70)
    print('  轨迹 → 可复用做法（T-1）')
    print('=' * 70)
    print('  源文件: %s' % src)
    print('  轨迹数: %d' % len(trajs))
    print('  模式:   %s' % ('规则（不调 LLM）' if a.no_llm else 'LLM 抽象化'))
    print('')

    kept, rejected = [], []
    for i, tj in enumerate(trajs):
        if len(kept) >= a.limit:
            break
        ok, why = is_success_trajectory(tj)
        if not ok:
            rejected.append((tj.get('session_id') or '?', why))
            continue
        sk = extract_skill(tj, use_llm=not a.no_llm)
        if sk is None:
            rejected.append((tj.get('session_id') or '?', 'extract 返回 None'))
        elif '_error' in sk:
            rejected.append((tj.get('session_id') or '?', '做法不合格: ' + sk['_error']))
        else:
            kept.append(sk)

    print('  提取成功: %d 条 · 拒绝: %d 条' % (len(kept), len(rejected)))
    print('')
    if rejected[:10]:
        print('  拒绝原因（前 10）:')
        for sid, why in rejected[:10]:
            print('    %-28s %s' % (str(sid)[:28], why))

    merged, dropped = dedupe_skills(kept)
    print('')
    print('  去重合并: %d → %d 条（合并掉 %d）' % (len(kept), len(merged), dropped))

    # 写盘 ✓
    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = pathlib.Path('') / out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8') as f:
        for sk in merged:
            f.write(json.dumps(sk, ensure_ascii=False) + '\n')
    print('')
    print('  ✓ 已写: %s（%d 条）' % (out, len(merged)))

    # markdown ✓
    if a.md:
        mdp = pathlib.Path(a.md)
        if not mdp.is_absolute():
            mdp = pathlib.Path('') / mdp
        mdp.parent.mkdir(parents=True, exist_ok=True)
        mdp.write_text(
            '# 从真实轨迹提炼的做法（自动生成 · 勿手改）\n\n'
            '> 生成时间: %s · 来源: %s · %d 条做法\n\n---\n\n' % (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), src.name, len(merged)) +
            '\n\n---\n\n'.join(skill_to_markdown(sk, i + 1) for i, sk in enumerate(merged)) + '\n',
            encoding='utf-8')
        print('  ✓ markdown: %s' % mdp)

    # 展示 ✓
    print('')
    print('  做法预览（前 3 条）:')
    for sk in merged[:3]:
        print('')
        print('    ── %s（%d 步）──' % (sk.get('task_type'), len(sk.get('steps') or [])))
        print('       触发词: %s' % ' / '.join(sk.get('trigger') or [])[:60])
        for s in (sk.get('steps') or [])[:4]:
            print('       - %s' % s[:80])
        if sk.get('tools_ok'):
            print('       工具: %s' % ' / '.join(sk['tools_ok'])[:60])

    return 0


# ══════════════════════════════════════════════════════════
# 六、自测
# ══════════════════════════════════════════════════════════
def selftest() -> int:
    print('=' * 70)
    print('  skills_extractor 自测')
    print('=' * 70)

    # ① 真成功轨迹 ✓
    good = {
        'task': '在 agent_use 知识库里检索「工具调用要遵守什么」，总结三条要点',
        'session_id': 'session-test-1',
        'reason': 'completed',
        'final': '三条要点：1) 先读规则再动手 2) 每次工具调用要校验返回 3) 失败要换角度重试。',
        'steps': [{'tool_calls': [
            {'tool': 'paper_lora_kb_info', 'input': {'name': 'agent_use'}, 'status': 'completed', 'result': '...'},
            {'tool': 'paper_lora_kb_query', 'input': {'q': '工具调用要遵守什么'}, 'status': 'completed', 'result': '命中 5 条'},
        ]}],
        'tool_errors': [],
    }
    ok, why = is_success_trajectory(good)
    print('  [1] 真成功轨迹: %s — %s' % ('✓ 通过' if ok else '✗ 拒绝', why))

    # ② 空转轨迹（provider 占位）✗
    bad1 = {
        'task': '诊断底座', 'session_id': 'session-bad-1', 'reason': 'completed',
        'final': '我无法直接执行，因为没有可用工具。',
        'steps': [{'tool_calls': [{'tool': 'openai', 'input': {}, 'status': 'completed', 'result': 'PLAN: []'}]}],
    }
    ok1, why1 = is_success_trajectory(bad1)
    print('  [2] 空转轨迹:   %s — %s' % ('✓ 拒绝' if not ok1 else '✗ 应拒绝', why1))

    # ③ 免责轨迹 ✗
    bad2 = dict(good)
    bad2['final'] = '我这边没有名为 xxx 的可用工具或库，无法直接执行检索。'
    ok2, why2 = is_success_trajectory(bad2)
    print('  [3] 免责轨迹:   %s — %s' % ('✓ 拒绝' if not ok2 else '✗ 应拒绝', why2))

    # ④ 校验: 好做法 ✓
    s_ok = {'task_type': '知识库检索并总结要点',
            'trigger': ['检索', '总结要点'],
            'steps': ['① 查库信息确认存在', '② 检索相关经验', '③ 总结成 3 条'],
            'tools_ok': ['paper_lora_kb_info', 'paper_lora_kb_query'],
            'pitfalls': ['检索前要先确认库名']}
    v1, w1 = validate_skill(s_ok)
    print('  [4] 好做法:     %s — %s' % ('✓ 通过' if v1 else '✗ 拒绝', w1))

    # ⑤ 校验: 泛做法（1 步）✗
    s_bad = dict(s_ok); s_bad['steps'] = ['① 用工具做完']
    v2, w2 = validate_skill(s_bad)
    print('  [5] 太泛做法:   %s — %s' % ('✓ 拒绝' if not v2 else '✗ 应拒绝', w2))

    # ⑥ 校验: 泄漏具体文件名 ✗
    s_bad2 = dict(s_ok); s_bad2['steps'] = ['① 打开 extract_v1.jsonl', '② 读取内容']
    v3, w3 = validate_skill(s_bad2)
    print('  [6] 未抽象:     %s — %s' % ('✓ 拒绝' if not v3 else '✗ 应拒绝', w3))

    # ⑦ 去重合并 ✓
    merged, dropped = dedupe_skills([s_ok, dict(s_ok, trigger=['检索']), {**s_ok, 'task_type': '另一个任务'}])
    print('  [7] 去重:       %s — 3 → %d 条（合并 %d）' % ('✓' if len(merged) == 2 else '✗', len(merged), dropped))

    # ⑧ markdown ✓
    md = skill_to_markdown(s_ok, 1)
    ok8 = ('做法 1' in md) and ('触发词' in md) and ('步骤' in md) and ('可用工具' in md)
    print('  [8] markdown:   %s（%d 字）' % ('✓' if ok8 else '✗', len(md)))

    # ⑨ JSON 修复 ✓
    d = repair_json('```json\n{"task_type":"x","steps":["a","b",]}\n```')
    print('  [9] JSON 修复:  %s（%s）' % ('✓' if d.get('task_type') == 'x' else '✗', d))

    total = sum([ok, not ok1, not ok2, v1, not v2, not v3, len(merged) == 2, ok8, d.get('task_type') == 'x'])
    print('')
    print('  ⇒ %d / 9 通过' % total)
    return 0 if total == 9 else 1


if __name__ == '__main__':
    sys.exit(main())
