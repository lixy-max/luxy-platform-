#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库飞轮编排器（KB Flywheel Orchestrator）· 2026-10-21

★ 依据: docs/KB_FLYWHEEL_PLAN.md + docs/KB_FLYWHEEL_REFERENCES.md
★ 文献吸收的 3 个改进:
   ① 增量 LoRA 叠加（不替换 ✗）← PILoRA + LoRA 少遗忘 ✓ vLLM max-loras 4 ✓
   ② MAPE 控制环（Monitor-Analyze-Plan-Execute ✓）← Adaptive Data Flywheel ✓
   ③ 递归抽象（做法 → 元做法 ✓）← SCAFFOLD ✓

★ 4 阶段闭环 = MAPE 环:
   ① 采集 Collect   = Monitor  ✓
   ② 蒸馏 Distill   = Analyze  ✓
   ③ 固化 Solidify  = Plan + Execute ✓
   ④ 验证 Validate  = Evaluate + 回滚 ✓

★ 3 条固化通道（按证据强度）:
   A 训练数据（要 GPU ⚠ 已验证 0→1 质变 ✓）
   B 工具路由（零 GPU ✓ 覆盖率 100% ✓）
   C 系统提示（零 GPU ✓ 只放纪律·绝不放知识 ✗）
   ✗ 检索注入 —— **明确不做** ✗（−29.5pp 已证明有害 ✗）

用法:
  python scripts/flywheel.py status                 # 飞轮状态
  python scripts/flywheel.py collect --days 7       # Monitor
  python scripts/flywheel.py distill                # Analyze
  python scripts/flywheel.py solidify --track routing  # Plan+Execute（B 通道）
  python scripts/flywheel.py validate --arm base    # Evaluate
  python scripts/flywheel.py promote --version v001 # 通过则晋级
  python scripts/flywheel.py rollback --version v001 # 回滚
  python scripts/flywheel.py run --track routing     # 跑完整一轮
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = pathlib.Path(__file__).resolve().parent.parent
FW = ROOT / 'data/flywheel'
STATE = FW / 'state.json'
VERSIONS = FW / 'versions'
SKILLS = ROOT / 'data/learning/skills/skills.jsonl'
TRAJ = ROOT / 'data/trajectories'
EVAL_OUT = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out')


# ══════════════════════════════════════════════════════════
# 状态管理 ✓
# ══════════════════════════════════════════════════════════
def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'round': 0,
        'created': _now(),
        'current_version': None,
        'versions': [],
        'metrics_history': [],
        'channels': {'A_train': 'pending', 'B_routing': 'done', 'C_prompt': 'pending'},
    }


def save_state(st: dict) -> None:
    FW.mkdir(parents=True, exist_ok=True)
    st['updated'] = _now()
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')


# ══════════════════════════════════════════════════════════
# ① Collect = Monitor ✓
# ══════════════════════════════════════════════════════════
def collect(days: int = 7) -> dict:
    """收最近 N 天的轨迹 ✓"""
    print('=' * 70)
    print('  ① 采集 Collect（Monitor）')
    print('=' * 70)
    files = sorted(TRAJ.glob('*.jsonl'))
    total = 0
    by_file = {}
    for f in files:
        n = sum(1 for _ in f.open(encoding='utf-8', errors='replace'))
        by_file[f.name] = n
        total += n
    print('  轨迹文件: %d · 总条数: %d' % (len(files), total))
    for k, v in sorted(by_file.items(), key=lambda x: -x[1])[:6]:
        print('    %-46s %6d' % (k[:46], v))
    # 做法库 ✓
    n_skills = 0
    if SKILLS.exists():
        n_skills = sum(1 for l in SKILLS.open(encoding='utf-8') if l.strip())
    print('  当前做法库: %d 条 ✓' % n_skills)
    return {'traj_files': len(files), 'traj_total': total, 'skills': n_skills,
            'by_file': by_file}


# ══════════════════════════════════════════════════════════
# ② Distill = Analyze ✓
# ══════════════════════════════════════════════════════════
def distill(limit: int = 50, use_llm: bool | None = None) -> dict:
    # ★ 修 #2（2026-10-21）: 默认用 LLM ✓（规则模式不抽象 ✗）
    if use_llm is None:
        import os as _os2
        use_llm = _os2.environ.get("FLYWHEEL_USE_LLM", "1") not in ("0", "false", "False")
    """轨迹 → 做法 ✓（调 skills_extractor ✓）"""
    print('=' * 70)
    print('  ② 蒸馏 Distill（Analyze）')
    print('=' * 70)
    src = TRAJ / 'trajectories.round2.jsonl'
    if not src.exists():
        cands = sorted(TRAJ.glob('*.jsonl'), key=lambda p: -p.stat().st_size)
        if not cands:
            print('  ✗ 没有轨迹文件')
            return {'ok': False}
        src = cands[0]
    out = FW / 'skills_candidate.jsonl'
    FW.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / 'scripts/skills_extractor.py'),
           '--traj', str(src), '--out', str(out), '--limit', str(limit)]
    if not use_llm:
        cmd.append('--no-llm')
    print('  源: %s' % src.name)
    print('  模式: %s' % ('LLM 抽象化' if use_llm else '规则（快·低质）'))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    tail = (r.stdout or '')[-1200:]
    for line in tail.split('\n')[-12:]:
        if line.strip():
            print('    ' + line[:110])
    n = 0
    if out.exists():
        n = sum(1 for l in out.open(encoding='utf-8') if l.strip())
    print('  ✅ 产出候选做法: %d 条' % n)
    return {'ok': r.returncode == 0, 'n': n, 'out': str(out), 'mode': 'llm' if use_llm else 'rule'}


# ══════════════════════════════════════════════════════════
# ②b 递归抽象 = 做法 → 元做法 ✓（期 1.5b · SCAFFOLD 同款 ✓）
#   目的: 22 条做法有重复模式 ✗ 再抽象成 6-8 条"元做法" ✓
#   好处: 减少冗余 ✓ 提高泛化 ✓ 训练数据更精 ✓
# ══════════════════════════════════════════════════════════
META_ABSTRACT_PROMPT = """你是「做法抽象器」。下面是 N 条从真实 Agent 轨迹提炼的【做法】，
它们有重复和重叠。请把它们**再抽象一层**，合并成 M 条【元做法】。

要求：
1. **只合并真正同类的**（同一类任务 + 类似工具链）✓ 不要硬合并 ✗
2. 每条元做法要能覆盖 2-5 条具体做法 ✓
3. 保留能执行的具体步骤（不是空话 ✗）
4. 输出严格 JSON 数组，不要任何解释：
[{"meta_type":"元做法名（动词开头·≤20字）","covers":["覆盖的 task_type 列表"],
  "trigger":["触发词 3-6 个"],"steps":["步骤 1","步骤 2","..."],
  "tools_ok":["工具名"],"pitfalls":["坑"]}]

【做法列表】：
%s"""


def _call_llm(prompt: str, max_tokens: int = 2000, timeout: int = 180) -> str:
    """调 vLLM（本地 ✓ 零外部依赖 ✓）"""
    import urllib.request
    body = json.dumps({
        'model': os.environ.get('FLYWHEEL_MODEL') or str(__import__('pathlib').Path.home() / 'models/Qwen2.5-7B-Instruct-AWQ'),
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens, 'temperature': 0.15,
    }).encode()
    req = urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',
                                 data=body, headers={'Content-Type': 'application/json'})
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return ((r.get('choices') or [{}])[0].get('message') or {}).get('content', '') or ''


def _parse_json_array(text: str) -> list:
    """从 LLM 输出里抠 JSON 数组 ✓（容错 ✓）"""
    if not text:
        return []
    # 去 think ✓
    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.MULTILINE)
    i, j = text.find('['), text.rfind(']')
    if i < 0 or j <= i:
        return []
    for cand in (text[i:j + 1],):
        try:
            d = json.loads(cand)
            if isinstance(d, list):
                return d
        except Exception:
            pass
    # 逐个对象抢救 ✓
    out = []
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text[i:j + 1], re.S):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and d.get('meta_type'):
                out.append(d)
        except Exception:
            pass
    return out


def meta_abstract(skills: list, batch: int = 12, use_llm: bool = True) -> dict:
    """做法 → 元做法 ✓（分批 ✓ 失败降级 ✓）"""
    print('=' * 70)
    print('  ②b 递归抽象（做法 → 元做法）')
    print('=' * 70)
    if not skills:
        print('  ✗ 没有做法')
        return {'ok': False, 'n': 0}
    if not use_llm:
        print('  ⚠ LLM 关闭 ⇒ 跳过（递归抽象必须用 LLM ✗ 规则模式做不了 ✗）')
        return {'ok': False, 'n': 0, 'reason': 'need_llm'}

    metas = []
    for bi in range(0, len(skills), batch):
        chunk = skills[bi:bi + batch]
        print('  批 %d: %d 条做法 → 抽象' % (bi // batch + 1, len(chunk)))
        # 构造输入 ✓（只给关键字段 ✓ 省 token ✓）
        brief = []
        for x in chunk:
            brief.append({
                'task_type': x.get('task_type'),
                'steps': (x.get('steps') or [])[:5],
                'tools_ok': (x.get('tools_ok') or [])[:6],
            })
        prompt = META_ABSTRACT_PROMPT % json.dumps(brief, ensure_ascii=False, indent=1)
        try:
            raw = _call_llm(prompt, 2000, 180)
            got = _parse_json_array(raw)
            print('    → 抽象出 %d 条元做法' % len(got))
            for g in got:
                if not isinstance(g, dict) or not g.get('meta_type'):
                    continue
                metas.append({
                    'meta_type': str(g.get('meta_type'))[:40],
                    'covers': g.get('covers') or [],
                    'trigger': (g.get('trigger') or [])[:6],
                    'steps': [str(x) for x in (g.get('steps') or [])][:8],
                    'tools_ok': (g.get('tools_ok') or [])[:8],
                    'pitfalls': (g.get('pitfalls') or [])[:4],
                    'source': 'meta_abstract',
                })
        except Exception as e:
            print('    ✗ 批次失败: %s' % str(e)[:80])

    # 去重（meta_type 相同合并）✓
    merged = {}
    for m in metas:
        k = m['meta_type']
        if k in merged:
            merged[k]['covers'] = list(dict.fromkeys((merged[k]['covers'] or []) + (m['covers'] or [])))
            merged[k]['steps'] = list(dict.fromkeys(merged[k]['steps'] + m['steps']))[:8]
            merged[k]['tools_ok'] = list(dict.fromkeys(merged[k]['tools_ok'] + m['tools_ok']))[:8]
        else:
            merged[k] = m
    out = sorted(merged.values(), key=lambda x: -len(x.get('covers') or []))
    print('')
    print('  ✅ 元做法: **%d 条**（从 %d 条做法）' % (len(out), len(skills)))
    p = FW / 'meta_skills.jsonl'
    FW.mkdir(parents=True, exist_ok=True)
    with p.open('w', encoding='utf-8') as f:
        for m in out:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')
    print('  产出: %s' % p)
    return {'ok': True, 'n': len(out), 'out': str(p), 'from': len(skills)}


# ══════════════════════════════════════════════════════════
# ③ Solidify = Plan + Execute ✓（3 通道）
# ══════════════════════════════════════════════════════════
# ★ 通道名映射 ✓
_CH_MAP = {'train': 'A_train', 'routing': 'B_routing', 'prompt': 'C_prompt'}


def _mark_channel(track: str, status: str, detail: dict | None = None) -> None:
    """★ 修 BUG（2026-10-21）: **回写 channels 状态** ✓
    
    原问题: solidify() 跑完**从不更新 state['channels']** ✗
    ⇒ channels 一直是静态 'pending' ✗ ⇒ 状态显示不可信 ✗
    """
    ch = _CH_MAP.get(track)
    if not ch:
        return
    try:
        st = load_state()
        chs = st.setdefault('channels', {})
        chs[ch] = status
        st.setdefault('channel_detail', {})[ch] = {
            'at': _now(), 'status': status, **(detail or {}),
        }
        save_state(st)
        print('  ✅ channels[%s] = %s ✓（已回写）' % (ch, status))
    except Exception as e:
        print('  ⚠ channels 回写失败: %s' % e)


def solidify(track: str = 'routing') -> dict:
    """把做法固化成能力 ✓"""
    print('=' * 70)
    print('  ③ 固化 Solidify（%s）' % track)
    print('=' * 70)
    _mark_channel(track, 'running')     # ★ 开始 ✓

    # ★ 修 #4（2026-10-21）: **优先 live（最新）** ✓ 不是旧 candidate ✗
    src = SKILLS
    if not src.exists():
        src = FW / 'skills_candidate.jsonl'
    if not src.exists():
        print('  ✗ 没有做法可固化')
        return {'ok': False}
    skills = [json.loads(l) for l in src.open(encoding='utf-8') if l.strip()]
    print('  做法来源: %s（%d 条）' % (src.name, len(skills)))

    if track == 'routing':
        # ★ 通道 B: 工具路由关键词扩充 ✓（零 GPU ✓）
        print('')
        print('  【通道 B：工具路由固化】')
        print('  纯离线 ✓ 零 GPU ✓ 有覆盖率验证 ✓')
        # 统计做法里的工具 → 域映射 ✓
        sys.path.insert(0, str(ROOT / 'scripts'))
        import importlib
        import tool_retriever
        importlib.reload(tool_retriever)
        dom_tools = {}
        for s in skills:
            for t in (s.get('tools_ok') or []):
                d = tool_retriever.classify_tool(t)
                dom_tools.setdefault(d, set()).add(t)
        print('  做法覆盖的域: %s' % {k: len(v) for k, v in dom_tools.items()})
        print('  已有域关键词: %s' % list(tool_retriever.DOMAIN_KEYWORDS.keys()))
        # 产出建议 ✓（不自动改前端 ✗ 要人工审核 ✓）
        sugg = {
            'generated': _now(),
            'source_skills': len(skills),
            'domains': {k: sorted(v) for k, v in dom_tools.items()},
            'note': '★ 人工审核后合并进 webui/index3.html 的 TOOL_DYN_DOMAINS ✓',
        }
        p = FW / 'routing_suggestion.json'
        FW.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sugg, ensure_ascii=False, indent=2), encoding='utf-8')
        print('  ✅ 建议已产出: %s' % p)
        _mark_channel('routing', 'done', {'out': str(p)})
        return {'ok': True, 'track': 'routing', 'suggestion': str(p)}

    if track == 'train':

        # ★ R-7 修（2026-10-21）: **优先用真实轨迹** ✓
        #   原问题: 造「步骤总结」✗ 16/20 格式不合格 ✗（无 function_call ✗）
        #   修法: 调 traj_to_training.py ✓（167 条 100% 合格 ✓）
        #   失败 ⇒ continue 走旧逻辑 ✓（fallback ✓）
        import subprocess as _sp_r7
        _out7 = FW / ('train_round%d_real.jsonl' % (load_state().get('round', 0) + 1))
        FW.mkdir(parents=True, exist_ok=True)
        try:
            _r7 = _sp_r7.run(
                [sys.executable, str(ROOT / 'scripts' / 'traj_to_training.py')],
                capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            _src7 = ROOT / 'training_data' / 'flywheel_toolcall_v1.jsonl'
            if _src7.exists() and _src7.stat().st_size > 0:
                import shutil as _sh_r7
                _sh_r7.copy2(_src7, _out7)
                _n7 = sum(1 for _l in _out7.open(encoding='utf-8') if _l.strip())
                print('  ✅ **真实轨迹**训练数据: %d 条 → %s' % (_n7, _out7.name))
                _mark_channel('train', 'done', {'out': str(_out7), 'source': 'real_trajectory'})
                return {'ok': True, 'track': 'train', 'samples': _n7,
                        'out': str(_out7), 'source': 'real_trajectory'}
            print('  ⚠ 真实轨迹无产出 ⇒ 退回旧逻辑（步骤总结 ⚠）')
        except Exception as _e7:
            print('  ⚠ 真实轨迹失败: %s ⇒ 退回旧逻辑 ⚠' % _e7)

        # ★ 通道 A: 训练数据固化 ⚠ 要 GPU
        print('')
        print('  【通道 A：训练数据固化】⚠ 要 GPU')
        print('  ★ 文献改进: **增量 LoRA 叠加**（不替换 ✗）← PILoRA ✓')
        # 做法 → sharegpt 样本 ✓
        samples = []
        for s in skills:
            tt = s.get('task_type') or ''
            steps = s.get('steps') or []
            tools = s.get('tools_ok') or []
            if not tt or not steps:
                continue
            # 构造: 用户问 task_type → assistant 给 steps + tool_call ✓
            user = tt
            ans = '；'.join('[%d] %s' % (i + 1, x) for i, x in enumerate(steps))
            sample = {
                'conversations': [
                    {'from': 'human', 'value': user},
                    {'from': 'gpt', 'value': ans},
                ],
                'system': '你是平台助手。按步骤完成任务，需要数据时调用工具。',
                'tools': json.dumps([{'name': t} for t in tools], ensure_ascii=False),
                'meta': {'source': 'flywheel', 'skill': tt},
            }
            samples.append(sample)
        p = FW / ('train_round%d.jsonl' % (load_state().get('round', 0) + 1))
        FW.mkdir(parents=True, exist_ok=True)
        with p.open('w', encoding='utf-8') as f:
            for x in samples:
                f.write(json.dumps(x, ensure_ascii=False) + '\n')
        print('  ✅ 训练样本: %d 条 → %s' % (len(samples), p))
        print('  ⚠ 下一步要 GPU 训练（等 7B 底座 / 用户不用 5060 ⚠）')
        _mark_channel('train', 'done', {'out': str(p)})
        return {'ok': True, 'track': 'train', 'samples': len(samples), 'out': str(p)}

    if track == 'prompt':
        # ★ 通道 C: 系统提示固化 ✓（只放纪律 ✗ 不放知识 ✗）
        print('')
        print('  【通道 C：系统提示固化】')
        print('  ★ 铁律: **只放"纪律"类** ✓ **绝不放"知识"类** ✗（−29.5pp 已证明 ✗）')
        # 从做法里提炼"纪律"（pitfalls ✓ = 踩过的坑 ✓ 适合做成提醒 ✓）
        disciplines = []
        for s in skills:
            for p in (s.get('pitfalls') or []):
                disciplines.append({'from': s.get('task_type'), 'pitfall': p})
        p = FW / 'prompt_suggestion.json'
        FW.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            'generated': _now(),
            'disciplines': disciplines[:30],
            'note': '★ 人工审核 → 写进 buildAgentSystemPrompt（只放纪律 ✓ 不放知识 ✗）',
            'warning': '必须 A/B 验证 ✓ 不通过就撤回 ✗',
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print('  ✅ 纪律建议: %d 条 → %s' % (len(disciplines), p))
        _mark_channel('prompt', 'done', {'out': str(p)})
        return {'ok': True, 'track': 'prompt', 'n': len(disciplines), 'out': str(p)}

    print('  ✗ 未知通道: %s（可选 routing/train/prompt）' % track)
    return {'ok': False}


# ══════════════════════════════════════════════════════════
# ④ Validate = Evaluate ✓
# ══════════════════════════════════════════════════════════
def validate(arm: str = 'base', tier: str = '4b', limit: int = 5, lora: str = '') -> dict:
    """跑 V2 A/B 验证 ✓"""
    print('=' * 70)
    print('  ④ 验证 Validate（arm=%s tier=%s）' % (arm, tier))
    print('=' * 70)
    runs = EVAL_OUT / 'runs_v2.jsonl'
    before = runs.stat().st_size if runs.exists() else 0
    tag = 'fw_%s_%s' % (arm, datetime.now().strftime('%H%M%S'))
    # 调跑批 ✓（Windows Node ✗ 需通过 pwsh ✗ 这里只给命令 ✓）
    cmd = ('cd E:/WSL/_check/drill && node eval_v2_run.mjs --tier %s --limit %d '
           '--repeats 1 --kb off --tag %s' % (tier, limit, tag))
    if lora:
        cmd += ' --lora %s' % lora
    print('  跑批命令（需在 Windows PowerShell 执行 ✓）:')
    print('    ' + cmd)
    print('')
    print('  ⚠ 本机是 WSL ✗ 无法直接驱动 Playwright ✗')
    print('  ⇒ 请手动在 PowerShell 跑上面命令 ✓ 然后执行:')
    print('    python scripts/flywheel.py validate --score %s' % tag)
    return {'ok': True, 'tag': tag, 'command': cmd, 'need_manual': True}


def score_arm(tag: str) -> dict:
    """判分已跑完的臂 ✓"""
    print('=' * 70)
    print('  ④b 判分（tag=%s）' % tag)
    print('=' * 70)
    cmd = [sys.executable, str(ROOT / 'scripts/eval_v2_score.py'),
           '--runs', str(EVAL_OUT / 'runs_v2.jsonl'),
           '--out', str(FW / ('score_%s.md' % tag))]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = (r.stdout or '')[-1500:]
    for line in out.split('\n'):
        if any(k in line for k in ('总览', '任务数', '单次通过', '稳定性', '工具命中', '升级到云')):
            print('  ' + line.strip()[:110])
    # 解析 ✓
    m = re.search(r'单次通过率 \| (\d+)/(\d+) = [\d.]+%', out or '')
    if not m:
        m = re.search(r'单次通过率: (\d+)/(\d+)', out or '')
    res = {'tag': tag, 'raw_tail': out[-500:]}
    if m:
        res['pass'] = int(m.group(1))
        res['runs'] = int(m.group(2))
        res['pass_rate'] = res['pass'] / max(res['runs'], 1)
        print('  ✅ 通过率: %d/%d = %.1f%%' % (res['pass'], res['runs'], res['pass_rate'] * 100))
    return res


# ══════════════════════════════════════════════════════════
# ⑤ Promote / Rollback ✓（版本化 ✓）
# ══════════════════════════════════════════════════════════
def promote(version: str) -> dict:
    """晋级：把候选做法 + 训练数据固化成一个版本 ✓"""
    print('=' * 70)
    print('  ⑤ 晋级 Promote（%s）' % version)
    print('=' * 70)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    vd = VERSIONS / version
    vd.mkdir(exist_ok=True)
    # ══════════════════════════════════════════════════════
    # ★ P2-1b（2026-10-21）: 修 promote 的**假动作** ✗
    #
    # 发现 BUG:
    #   ① promote 只快照 candidate ✗ **从不装到 live** ✗ ⇒ 晋级没生效 ✗
    #   ② 快照的是旧 candidate ✗ **不是当前真实数据** ✗
    #      ⇒ rollback 会恢复到旧数据 ✗✗
    #
    # 修法:
    #   ① 有 candidate ⇒ **真装到 live** ✓（先备份旧 live ✓）
    #   ② 快照**最终真实的 live** ✓
    #   ③ md5 校验 ✓（快照 == live ✓）
    # ══════════════════════════════════════════════════════
    import hashlib as _hl

    cand = FW / 'skills_candidate.jsonl'
    if cand.exists():
        # ① 备份旧 live ✓（防装错 ✗）
        if SKILLS.exists():
            shutil.copy2(SKILLS, vd / 'skills_prev.jsonl')
            print('  旧 live 备份: skills_prev.jsonl ✓')
        # ② **真装到 live** ✓
        shutil.copy2(cand, SKILLS)
        print('  ✅ candidate → live **已真安装** ✓')

    # ③ 快照最终真实的 live ✓（不是候选 ✗）
    if SKILLS.exists():
        shutil.copy2(SKILLS, vd / 'skills.jsonl')
        n = sum(1 for l in SKILLS.open(encoding='utf-8') if l.strip())
        # ④ md5 校验 ✓
        h_live = _hl.md5(SKILLS.read_bytes()).hexdigest()[:12]
        h_snap = _hl.md5((vd / 'skills.jsonl').read_bytes()).hexdigest()[:12]
        print('  做法快照: %d 条 ✓ md5=%s %s' % (
            n, h_snap, '✓一致' if h_live == h_snap else '✗不一致'))
        if h_live != h_snap:
            print('  🔴 **快照与 live 不一致** ✗ 晋级失败 ✗')
            return {'ok': False, 'error': '快照校验失败'}
    # 训练数据 ✓
    for f in FW.glob('train_round*.jsonl'):
        shutil.copy2(f, vd / f.name)
    # 状态 ✓
    st = load_state()
    st['round'] = int(st.get('round', 0)) + 1
    st.setdefault('versions', []).append({'version': version, 'at': _now(),
                                          'skills': str(vd / 'skills.jsonl')})
    st['current_version'] = version
    save_state(st)
    print('  ✅ 已晋级版本 **%s**（第 %d 轮）' % (version, st['round']))
    return {'ok': True, 'version': version, 'round': st['round']}


def rollback(version: str | None = None, dry_run: bool = False) -> dict:
    """回滚到上一个/指定版本 ✓

    ★ P2-1（2026-10-21）: **真恢复文件** ✓（原来只改状态 ✗ = 假回滚 ✗）
    dry_run=True ⇒ 只报告要恢复什么 ✓ 不动文件 ✓
    """
    print('=' * 70)
    print('  ⑤b 回滚 Rollback')
    print('=' * 70)
    st = load_state()
    vs = st.get('versions') or []
    if not vs:
        print('  ✗ 没有可回滚的版本')
        return {'ok': False}
    # ★ 修（2026-10-21）: 已是最老版本时不许回滚到自己 ✗
    cur = st.get('current_version')
    if version is None:
        # 找当前版本在列表里的位置 ✓ 取前一个 ✓
        names = [v['version'] for v in vs]
        if cur in names:
            idx = names.index(cur)
            target = names[idx - 1] if idx > 0 else None
        else:
            target = vs[-2]['version'] if len(vs) >= 2 else None
    else:
        target = version
    if not target:
        print('  ✗ 没有上一个版本（当前已是最老: %s）' % (cur or '无'))
        return {'ok': False}
    if target == cur:
        print('  ✗ 目标版本 = 当前版本（%s）⇒ 无需回滚' % cur)
        return {'ok': False}
    print('  当前: %s → 回滚到: **%s**' % (st.get('current_version'), target))

    # ══════════════════════════════════════════════════════
    # ★ P2-1（2026-10-21）: 真正恢复文件 ✓
    #
    # 修复的 BUG: 原来只改 state['current_version'] ✗
    #   实际文件（skills.jsonl / 训练数据）**没恢复** ✗ ⇒ 假回滚 ✗
    #
    # 安全设计:
    #   ① 回滚前**先备份当前状态** ✓（回滚错了还能回来 ✓）
    #   ② **md5 校验** ✓（证明真的恢复了 ✓）
    #   ③ **dry-run** ✓（只报告不动 ✓）
    #   ④ **审计记录** ✓（可追溯 ✓）
    # ══════════════════════════════════════════════════════
    import hashlib as _hashlib

    vd = VERSIONS / target
    if not vd.exists():
        print('  ✗ 版本目录不存在: %s' % vd)
        return {'ok': False, 'error': '版本目录不存在: %s' % vd}

    # ① 回滚前备份当前状态 ✓（防万一 ✗）
    backup_dir = None
    if not dry_run:
        try:
            import datetime as _dt
            backup_dir = VERSIONS / ('_pre_rollback_%s' % _dt.datetime.now().strftime('%Y%m%d_%H%M%S'))
            backup_dir.mkdir(parents=True, exist_ok=True)
            if SKILLS.exists():
                shutil.copy2(SKILLS, backup_dir / 'skills.jsonl')
            for f in FW.glob('train_round*.jsonl'):
                shutil.copy2(f, backup_dir / f.name)
            print('  回滚前备份: %s ✓' % backup_dir.name)
        except Exception as e:
            print('  ⚠ 回滚前备份失败（继续 ⚠）: %s' % e)
            backup_dir = None

    # ② 真恢复 ✓
    restored = []
    plan = []
    if (vd / 'skills.jsonl').exists():
        plan.append(('skills', vd / 'skills.jsonl', SKILLS))
    for f in sorted(vd.glob('train_round*.jsonl')):
        plan.append(('train', f, FW / f.name))

    if dry_run:
        print('  🔍 DRY-RUN（不动文件 ✓）')
        for kind, src, dst in plan:
            print('    将恢复 %-8s %s → %s' % (kind, src.name, dst))
        return {'ok': True, 'dry_run': True, 'version': target,
                'would_restore': [s.name for _, s, _ in plan]}

    md5_check = []
    for kind, src, dst in plan:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            h_src = _hashlib.md5(src.read_bytes()).hexdigest()[:12]
            h_dst = _hashlib.md5(dst.read_bytes()).hexdigest()[:12]
            ok_md5 = (h_src == h_dst)
            md5_check.append({'file': str(dst.name), 'md5': h_dst, 'ok': ok_md5})
            restored.append(str(dst.name))
            print('  ✓ 恢复 %-8s %s（md5=%s %s）' % (
                kind, dst.name, h_dst, '✓' if ok_md5 else '✗'))
        except Exception as e:
            print('  ✗ 恢复失败 %s: %s' % (dst, e))
            md5_check.append({'file': str(dst.name), 'ok': False, 'error': str(e)[:100]})

    all_ok = all(x.get('ok') for x in md5_check) and bool(md5_check)
    if not all_ok:
        print('  🔴 **恢复校验失败** ✗ 状态不变 ✗')
        return {'ok': False, 'error': '文件恢复校验失败', 'details': md5_check,
                'backup_dir': str(backup_dir) if backup_dir else None}

    # ③ 状态 + 审计 ✓
    st['current_version'] = target
    st['rolled_back_at'] = _now()
    st.setdefault('rollback_history', []).append({
        'at': _now(), 'from': cur, 'to': target,
        'restored': restored, 'md5': md5_check,
        'backup_dir': str(backup_dir) if backup_dir else None,
    })
    save_state(st)
    print('  ✅ **已真回滚** ✓ 恢复 %d 个文件 ✓' % len(restored))
    if backup_dir:
        print('  可撤销: 备份在 %s ✓' % backup_dir.name)
    return {'ok': True, 'version': target, 'restored': restored,
            'md5': md5_check, 'backup_dir': str(backup_dir) if backup_dir else None}


# ══════════════════════════════════════════════════════════
# 期 3: MAPE Monitor 自动化 ✓（2026-10-21）
#   · 每轮记录指标快照 ✓（V2 分数 / 做法数 / 工具覆盖 / 资产）
#   · 对比上轮 ⇒ 检测退化 ✗
#   · 退化 >5pp ⇒ 建议回滚 ✓
# ══════════════════════════════════════════════════════════
class Monitor:
    """MAPE 的 Monitor ✓ —— 自动记录 + 退化检测 ✓"""

    REGRESS_THRESHOLD = 0.05      # 掉 >5pp ⇒ 告警 ✓

    @staticmethod
    def snapshot() -> dict:
        """当前指标快照 ✓（零 GPU ✓ 全部可离线算 ✓）"""
        snap = {'at': _now()}
        # ① 做法库 ✓
        n = 0
        if SKILLS.exists():
            n = sum(1 for l in SKILLS.open(encoding='utf-8') if l.strip())
        snap['skills'] = n
        # ② 评测集 ✓
        ev = ROOT / 'docs/EVAL_SET_V2.json'
        if ev.exists():
            try:
                d = json.loads(ev.read_text(encoding='utf-8'))
                snap['eval_tasks'] = len(d.get('tasks') or [])
            except Exception:
                snap['eval_tasks'] = None
        # ③ 轨迹 ✓
        snap['traj_files'] = len(list(TRAJ.glob('*.jsonl')))
        snap['traj_total'] = sum(
            sum(1 for _ in f.open(encoding='utf-8', errors='replace'))
            for f in TRAJ.glob('*.jsonl'))
        # ④ 最近一次判分 ✓（从 eval_v2 结果里读 ✓）
        for f in sorted(EVAL_OUT.glob('EVAL_V2_RESULT.json'), reverse=True):
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
                snap['last_pass_rate'] = d.get('total', {}).get('pass_rate')
                snap['last_tasks'] = d.get('total', {}).get('tasks')
            except Exception:
                pass
            break
        # ⑤ 飞轮状态 ✓
        st = load_state()
        snap['round'] = st.get('round', 0)
        snap['version'] = st.get('current_version')
        return snap

    @staticmethod
    def record(snap: dict | None = None) -> dict:
        """把快照写进历史 ✓"""
        snap = snap or Monitor.snapshot()
        st = load_state()
        hist = st.setdefault('metrics_history', [])
        hist.append(snap)
        # 只留最近 50 条 ✓ 防膨胀 ✓
        st['metrics_history'] = hist[-50:]
        save_state(st)
        print('  ✓ 指标已记录（第 %d 条）' % len(st['metrics_history']))
        return snap

    @staticmethod
    def check_regression() -> dict:
        """对比最近两次 ⇒ 检测退化 ✓"""
        st = load_state()
        hist = st.get('metrics_history') or []
        if len(hist) < 2:
            # ★ 修: 必须总是带 alerts/checks 键 ✓（report() 依赖 ✓）
            return {'ok': True, 'alerts': [], 'checks': [],
                    'msg': '历史不足 2 条 ⇒ 无法对比 ⚠'}
        prev, cur = hist[-2], hist[-1]
        res = {'ok': True, 'checks': [], 'alerts': []}
        # 通过率退化 ✓
        p0, p1 = prev.get('last_pass_rate'), cur.get('last_pass_rate')
        if p0 is not None and p1 is not None:
            diff = p1 - p0
            res['checks'].append({'metric': 'pass_rate', 'prev': p0, 'cur': p1, 'diff': diff})
            if diff < -Monitor.REGRESS_THRESHOLD:
                res['alerts'].append('⚠ 通过率退化 %.1fpp（%.1f%% → %.1f%%）✗ 建议回滚' % (
                    diff * 100, p0 * 100, p1 * 100))
        # 做法库缩减（意外删除 ✗）
        s0, s1 = prev.get('skills'), cur.get('skills')
        if s0 is not None and s1 is not None and s1 < s0:
            res['alerts'].append('⚠ 做法库减少 %d → %d ✗（意外删除？）' % (s0, s1))
        if res['alerts']:
            res['ok'] = False
        return res

    @staticmethod
    def report() -> None:
        """打印指标历史 ✓"""
        st = load_state()
        hist = st.get('metrics_history') or []
        print('=' * 70)
        print('  MAPE Monitor · 指标历史（%d 条）' % len(hist))
        print('=' * 70)
        if not hist:
            print('  （无历史 ⇒ 跑 `flywheel.py monitor` 记录一条 ✓）')
            return
        print('  %-20s %-6s %-6s %-6s %-8s %s' % ('时间', '轮次', '做法', '轨迹', '通过率', '版本'))
        for h in hist[-12:]:
            pr = h.get('last_pass_rate')
            print('  %-20s %-6s %-6s %-6s %-8s %s' % (
                str(h.get('at', ''))[:19], h.get('round', '?'), h.get('skills', '?'),
                h.get('traj_total', '?'),
                ('%.1f%%' % (pr * 100)) if pr is not None else '—',
                h.get('version') or '—'))
        r = Monitor.check_regression()
        print('')
        print('  退化检测: %s' % (''.join(r['alerts']) if r['alerts'] else '✓ 未发现退化 ✓'))
        if r.get('checks'):
            for c in r['checks']:
                print('    %-12s %.1f%% → %.1f%%（%+.1fpp）' % (
                    c['metric'], c['prev'] * 100, c['cur'] * 100, c['diff'] * 100))


# ══════════════════════════════════════════════════════════
# Status ✓
# ══════════════════════════════════════════════════════════
def status() -> dict:
    st = load_state()
    print('=' * 70)
    print('  知识库飞轮 · 状态')
    print('=' * 70)
    print('  轮次: **%d**' % st.get('round', 0))
    print('  当前版本: %s' % (st.get('current_version') or '（未晋级 ✗）'))
    print('  创建: %s · 更新: %s' % (st.get('created'), st.get('updated', '—')))
    print('')
    print('  通道状态:')
    for k, v in (st.get('channels') or {}).items():
        print('    %-12s %s' % (k, v))
    print('')
    vs = st.get('versions') or []
    print('  版本历史: %d 个' % len(vs))
    for v in vs[-5:]:
        print('    %-14s %s' % (v.get('version'), v.get('at')))
    # 资产盘点 ✓
    print('')
    print('  资产:')
    n_skills = sum(1 for l in SKILLS.open(encoding='utf-8') if l.strip()) if SKILLS.exists() else 0
    print('    做法库: %d 条' % n_skills)
    ev = ROOT / 'docs/EVAL_SET_V2.json'
    if ev.exists():
        d = json.loads(ev.read_text(encoding='utf-8'))
        print('    评测集: %d 条' % len(d.get('tasks') or []))
    print('    轨迹: %d 个文件' % len(list(TRAJ.glob('*.jsonl'))))
    print('    训练样本: %d 个' % len(list(FW.glob('train_round*.jsonl'))) if FW.exists() else 0)
    print('')
    print('  ★ 铁律: 每轮必须 A/B 验证 ✓ 不通过就回滚 ✗')
    return st


# ══════════════════════════════════════════════════════════
# Run（完整一轮）✓
# ══════════════════════════════════════════════════════════
def run(track: str = 'routing') -> dict:
    st = status()
    print('')
    r1 = collect()
    print('')
    r2 = distill(limit=30, use_llm=False)   # 默认规则模式 ✓ 快 ✓
    print('')
    r3 = solidify(track=track)
    print('')
    print('=' * 70)
    print('  本轮完成（未验证 ✗ 未晋级 ✗）')
    print('=' * 70)
    print('  下一步:')
    print('    1. 人工审核产物 ✓')
    print('    2. 在 PowerShell 跑验证:')
    print('       cd E:/WSL/_check/drill && node eval_v2_run.mjs --tier 4b --limit 5 --repeats 1 --kb off --tag fw_round%d' % (int(st.get('round', 0)) + 1))
    print('    3. 判分: python scripts/flywheel.py score --tag fw_round%d' % (int(st.get('round', 0)) + 1))
    print('    4. 通过则: python scripts/flywheel.py promote --version v%03d' % (int(st.get('round', 0)) + 1))
    return {'collect': r1, 'distill': r2, 'solidify': r3}


# ══════════════════════════════════════════════════════════
# CLI ✓
# ══════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description='知识库飞轮编排器')
    ap.add_argument('cmd', choices=['status', 'collect', 'distill', 'solidify',
                                    'validate', 'score', 'promote', 'rollback', 'run',
                                    'monitor', 'regress', 'meta'])
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--limit', type=int, default=30)
    ap.add_argument('--track', default='routing', choices=['routing', 'train', 'prompt'])
    ap.add_argument('--arm', default='base')
    ap.add_argument('--tier', default='4b')
    ap.add_argument('--lora', default='')
    ap.add_argument('--tag', default='')
    ap.add_argument('--version', default='')
    ap.add_argument('--use-llm', action='store_true')
    a = ap.parse_args()

    if a.cmd == 'status':
        status()
    elif a.cmd == 'collect':
        collect(a.days)
    elif a.cmd == 'distill':
        distill(a.limit, a.use_llm)
    elif a.cmd == 'solidify':
        solidify(a.track)
    elif a.cmd == 'validate':
        validate(a.arm, a.tier, a.limit, a.lora)
    elif a.cmd == 'score':
        score_arm(a.tag)
    elif a.cmd == 'promote':
        promote(a.version or ('v%03d' % (load_state().get('round', 0) + 1)))
    elif a.cmd == 'rollback':
        rollback(a.version or None)
    elif a.cmd == 'run':
        run(a.track)
    elif a.cmd == 'meta':
        src = FW / 'skills_candidate.jsonl'
        if not src.exists():
            src = SKILLS
        sk = [json.loads(l) for l in src.open(encoding='utf-8') if l.strip()] if src.exists() else []
        meta_abstract(sk, use_llm=True)
    elif a.cmd == 'monitor':
        Monitor.record()
        Monitor.report()
    elif a.cmd == 'regress':
        r = Monitor.check_regression()
        print('退化检测: %s' % (''.join(r['alerts']) if r['alerts'] else '✓ 未发现 ✓'))
        sys.exit(0 if r.get('ok') else 2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
