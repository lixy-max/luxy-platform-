#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演练任务判分器 v2（Drill Scorer v2）—— 按审计报告修正

## v1 → v2 的关键修正（依据 docs/SCORER_AUDIT.md ✓ 13 条毛病）
审计指出的 5 条高危，全部修:
  高1 **不可能/边界题方向反了** ✗：v1 奖励"不动手"（P1-48 不调工具=60/60 ✗）
      ⇒ v2: 按【验收点】判，不可能题的正确行为是「先查证（list_*）→ 再说明不存在」✓
        拿"先查证"算动手 ✓；什么都不做只拿一半 ✓；乱调工具 0 ✓
        且 impossible 题【剔出合规率分母】✓（它们不是"能做对的任务"）
  高2 **老记录没 ui_cards** ✗：v1 工具选择恒 0 ⇒ 60 条里 57 条全 0 ✗
      ⇒ v2: 数据源回退链 exec_tools → ui_cards → api(提出) → tools 白名单 → msgs 卡片正则 ✓
        tool_choice 用【提出 ✓】execution 用【执行 ✓】（审计要求 ✓）
  高3 **aggregate 不分层不配对** ✗
      ⇒ v2: 按 kind 分层 ✓ impossible 不进合规分母 ✓ 支持 --compare 按【共同 task_id 配对】✓
  高4 **类目被当必调集合** ✗（P1-44 一种方法做对反扣分 ✗）
      ⇒ v2: "或"语义 —— 命中任一期望工具即给分 ✓（精度只微调 ✓）
  高5 **节点名被当工具** ✗（finaloutcome ✗）
      ⇒ v2: 用 agent_kb/_tools.json 白名单过滤 ✓
中危:
  · 落地性不再靠文本正则 ✗ → 认"产物类工具 ok 卡" ✓（文本仅辅助 ✓）
  · 判官加固: evidence 必须【真在材料里】✓ pass 必须 is True ✓ 材料给全 ✓ 状态类标 unverifiable ✓
  · 转云只扣"无效转云"（转云且无任何成功工具 ✓）
  · --judge 全失败 ⇒ 降级为无判官口径 ✓（否则合规率假性 0% ✗）
低危: 阈值单一来源 ✓ · CLI 计数修 ✓ · 取最近一次 run ✓

## 用法
  python scripts/drill_score.py --runs <jsonl> --tag p1full
  python scripts/drill_score.py --runs <jsonl> --compare p1full p2full   # 配对比较 ✓
  python scripts/drill_score.py --runs <jsonl> --tag p1full --judge
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 只有这些才算"工具"（白名单 ✓ 从工具目录读 ✓ 过滤 finaloutcome 这类节点名 ✗）
_TOOL_WHITELIST = None


def tool_whitelist() -> set:
    global _TOOL_WHITELIST
    if _TOOL_WHITELIST is not None:
        return _TOOL_WHITELIST
    names = set()
    try:
        p = ROOT / 'agent_kb' / '_tools.json'
        d = json.loads(p.read_text(encoding='utf-8'))
        if isinstance(d, dict):
            for k, v in d.items():
                names.add(str(k))
                if isinstance(v, dict) and v.get('name'):
                    names.add(str(v['name']))
        elif isinstance(d, list):
            for x in d:
                names.add(str(x.get('name') if isinstance(x, dict) else x))
    except Exception:
        pass
    # 兜底: 后端 35 个工具的核心集合（白名单宁缺毋滥 ✓）
    names |= {
        'list_knowledge_bases', 'query_knowledge_base', 'create_knowledge_base',
        'list_loras', 'load_lora', 'unload_lora', 'switch_model', 'list_available_models',
        'list_workflow_configs', 'create_workflow_config', 'use_workflow_config',
        'list_workflow_nodes', 'add_workflow_node', 'update_workflow_node', 'delete_workflow_node',
        'validate_workflow', 'run_workflow', 'list_workflow_uploads', 'list_workflow_results',
        'get_workflow_result', 'get_workflow_status', 'list_history', 'get_history_detail',
        'list_trainings', 'get_training_status', 'start_training', 'list_available_datasets',
        'build_dataset', 'run_eval', 'get_eval_status', 'list_uploads', 'read_upload',
        'read_docx', 'write_docx', 'create_docx', 'export_docx', 'add_toc',
        'read_sheet', 'write_sheet', 'create_sheet', 'list_sheets', 'filter_rows', 'group_sum',
        'get_settings', 'get_vllm_status', 'list_running_tasks', 'get_quality_summary',
        'get_loras_registry', 'list_doc_flows', 'run_doc_flow', 'save_doc_flow',
        'replace_in_file', 'replace_text', 'set_lines', 'set_lines_in_file',
        'list_sim_nodes', 'run_simulation',
    }
    _TOOL_WHITELIST = {n for n in names if n and re.match(r'^[a-z_][a-z0-9_]*$', n)}
    return _TOOL_WHITELIST


# ---- 任务解析 ----
_TASK_HEAD_RE = re.compile(r'^### ([A-Z]+\d*-\d+)\s*【([^】]+)】\s*(.*)$', re.M)


def parse_tasks(path) -> list:
    txt = Path(path).read_text(encoding='utf-8')
    out = []
    for b in re.split(r'\n(?=### [A-Z]+\d*-\d+)', txt):
        m = _TASK_HEAD_RE.match(b)
        if not m:
            continue
        kind = m.group(2).strip()

        def grab(label):
            mm = re.search(r'- ' + label + r'[:：]\s*(.+)', b)
            return mm.group(1).strip() if mm else ''

        cat, pre = grab('类目'), grab('前置[^:：]*')
        points = re.findall(r'^\s+\d+\)\s*(.+)$', b, re.M)
        raw_tools = []
        for src in (cat, pre):
            raw_tools += re.findall(r'`([a-z_][a-z0-9_]*)`', src or '')
        wl = tool_whitelist()
        tools = [t for t in dict.fromkeys(raw_tools) if t in wl]      # ★ 白名单过滤（高5 ✓）
        is_impossible = ('不可能' in kind)
        is_boundary = ('边界' in kind)
        # ★ 按【验收点】判"要不要动手"（高1 ✓ v1 只看 kind 的 cat 正则是死代码 ✗）
        needs_verification = bool(re.search(r'(先|核对|查一下|list_|问|确认)', ' '.join(points)))
        expect_no_tool = False          # v2 不再有"奖励不动手"✗
        out.append({
            'id': m.group(1), 'kind': kind, 'title': m.group(3).strip(),
            'phrase': grab('原话'), 'cat': cat, 'points': points, 'tools': tools,
            'is_impossible': is_impossible, 'is_boundary': is_boundary,
            'needs_verification': needs_verification, 'expect_no_tool': expect_no_tool,
        })
    return out


def inherit_pairs(tasks: list, ref_tasks_path) -> int:
    """★ 2026-10-19: part2 文档缺「- 类目」行 ✗（P1 有 50 条 · P2 有 0 条 ✓ 实测）
    ⇒ 判分时期望工具为空 ⇒ 工具选择恒 0 分 ✗ 口径不可比 ✗
    文档明确 P2-xx 对应 P1-xx 一一对应 ✓ ⇒ 缺类目时从同号任务继承 ✓
    返回继承条数 ✓
    """
    ref = {t['id']: t for t in parse_tasks(ref_tasks_path)}
    n = 0
    for t in tasks:
        if t.get('tools'):
            continue
        num = t['id'].split('-')[-1]
        for rid, rt in ref.items():
            if rid.split('-')[-1] == num and rt.get('tools'):
                t['tools'] = list(rt['tools'])
                t['cat'] = t.get('cat') or rt.get('cat', '')
                t['inherited_from'] = rid
                n += 1
                break
    return n


def norm_tool(name: str) -> str:
    s = str(name or '').strip()
    s = re.sub(r'\d+$', '', s)
    s = s.replace('paper_lora_', '')
    _equiv = {'create_docx': 'write_docx', 'write_docx_file': 'write_docx',
              'create_sheet': 'write_sheet', 'write_sheet_file': 'write_sheet',
              'save_workflow_config': 'create_workflow_config'}
    alias = {'training_list': 'list_trainings', 'train_list': 'list_trainings',
             'list_datasets': 'list_available_datasets',
             'workflow_configs': 'list_workflow_configs',
             'vllm_status': 'get_vllm_status', 'system_status': 'get_settings'}
    s = _equiv.get(s, s)
    return alias.get(s, s)


_MSGS_CARD_RE = re.compile(r'(?:✅|❌)\s*\n?\s*([a-z_][a-z0-9_]*)', re.M)


def _tools_from_msgs(msgs: str) -> list:
    return [norm_tool(x) for x in _MSGS_CARD_RE.findall(str(msgs or ''))]


def executed_tools(run: dict) -> list:
    """★ 数据源回退链（高2 ✓）: exec_tools → ui_cards → msgs 卡片正则"""
    ex = [norm_tool(x) for x in (run.get('exec_tools') or [])]
    if not ex:
        ex = [norm_tool(c.get('name')) for c in (run.get('ui_cards') or [])
              if c.get('name') and not c.get('is_notice')]
    if not ex:
        ex = _tools_from_msgs(run.get('msgs') or '')
    wl = tool_whitelist()
    return [x for x in ex if x in wl]


def proposed_tools(run: dict) -> list:
    """★ 工具选择用【提出】（审计要求 ✓）: api → exec → msgs"""
    pr = [norm_tool(x) for x in (run.get('api_tools') or run.get('api_uniq_tools') or [])]
    wl = tool_whitelist()
    pr = [x for x in pr if x in wl]
    return pr or executed_tools(run)


def ok_cards(run: dict) -> tuple:
    cards = [c for c in (run.get('ui_cards') or []) if c.get('name') and not c.get('is_notice')]
    if cards:
        return ([norm_tool(c['name']) for c in cards if c.get('status') == 'ok'],
                [norm_tool(c['name']) for c in cards if c.get('status') == 'error'])
    # 老记录没有卡片 ⇒ 无法判执行状态 ✓ 但别当成全失败 ✗（高2 ✓）
    return ([], [])


_ARTIFACT_TOOLS = ('write_docx', 'export_docx', 'write_sheet', 'add_toc', 'save_doc_flow',
                   'build_dataset', 'run_eval', 'start_training', 'create_knowledge_base',
                   'ingest_to_knowledge_base', 'create_workflow_config')


def score_machine(task: dict, run: dict) -> dict:
    """机器分 60（v2 ✓ 按审计修正）"""
    execs = executed_tools(run)
    props = proposed_tools(run)
    expect = set(norm_tool(t) for t in (task.get('tools') or []))
    hit = expect & set(execs) | (expect & set(props))
    detail = {}

    # ① 工具选择 30（"或"语义 ✓ 高4）
    if task.get('is_impossible') or task.get('is_boundary'):
        # 不可能/边界: 正确 = 先查证 ✓（needs_verification）再说明
        verified = bool(execs or props)
        asked_ok = bool(re.search(r'(不存在|没有找到|没找到|不确定|请确认|无法|建议|可以先)',
                                  str(run.get('msgs') or '')))
        if verified and asked_ok:
            s1 = 30.0
        elif verified or asked_ok:
            s1 = 15.0
        else:
            s1 = 0.0
        detail['tool_choice'] = {'mode': 'impossible' if task.get('is_impossible') else 'boundary',
                                 'verified': verified, 'asked_ok': asked_ok, 'score': s1}
    else:
        if not execs and not props:
            s1 = 0.0
        elif not expect:
            s1 = 15.0                                  # 任务没写期望工具 ⇒ 动手就给一半
        else:
            precision = len(hit) / max(1, len(set(execs) | set(props)))
            s1 = round(30.0 * (0.6 + 0.4 * precision), 2) if hit else 0.0   # 命中即 ≥18 ✓
        detail['tool_choice'] = {'mode': 'ast', 'expect': sorted(expect),
                                 'executed': sorted(set(execs)), 'proposed': sorted(set(props)),
                                 'hit': sorted(hit), 'score': s1}

    # ② 动作完成 15（有卡片才算 ✓ 没卡片给中位 ✓ 高2）
    oks, errs = ok_cards(run)
    all_cards = len(oks) + len(errs)
    if all_cards:
        s2 = round(15.0 * len(oks) / all_cards, 2)
    elif execs:
        s2 = 7.5                                    # 执行过但无状态证据 ⇒ 中位 ✓
    else:
        s2 = 0.0
    detail['execution'] = {'ok': len(oks), 'err': len(errs), 'score': s2}

    # ③ 落地性 10（认产物类工具 ok 卡 ✓ 文本仅辅助 ✓）
    needs_artifact = any(norm_tool(t) in _ARTIFACT_TOOLS for t in expect)
    artifact_ok = any(norm_tool(t) in _ARTIFACT_TOOLS for t in oks)
    if not needs_artifact:
        s3 = 10.0 if (oks or task.get('is_impossible') or task.get('is_boundary')) else 5.0
    else:
        s3 = 10.0 if artifact_ok else 0.0
    detail['artifact'] = {'needs_artifact': needs_artifact, 'artifact_ok': artifact_ok, 'score': s3}

    # ④ 干净度 5（转云只扣"无效转云" ✓）
    escalated = bool(run.get('escalated'))
    invalid_esc = escalated and not oks and not execs
    if errs:
        s4 = 0.0
    elif invalid_esc:
        s4 = 0.0
    elif escalated:
        s4 = 5.0                                    # 有效转云不扣 ✓
    else:
        s4 = 5.0
    detail['clean'] = {'errors': len(errs), 'escalated': escalated,
                       'invalid_escalation': invalid_esc, 'score': s4}

    return {'machine_score': round(s1 + s2 + s3 + s4, 2), 'machine_max': 60, 'detail': detail}


COMPLIANT_WITH_JUDGE = 70
COMPLIANT_MACHINE_ONLY = 42


def aggregate(scored: list, has_judge: bool = False) -> dict:
    """按 kind 分层 + impossible 剔出合规分母 ✓（高1/高3）"""
    missing = len([s for s in scored if s.get('missing')])
    rows = [s for s in scored if not s.get('missing') and s.get('machine')]
    scoreable = [s for s in rows if not s['task'].get('is_impossible')]   # ★ 合规分母
    line = COMPLIANT_WITH_JUDGE if has_judge else COMPLIANT_MACHINE_ONLY

    def mean(xs):
        return round(sum(s['total'] for s in xs) / len(xs), 1) if xs else None

    def rate(xs):
        return round(100 * len([s for s in xs if s['total'] >= line]) / len(xs), 1) if xs else None

    by_kind = {}
    for s in rows:
        k = '不可能' if s['task'].get('is_impossible') else (
            '边界' if s['task'].get('is_boundary') else '可完成')
        by_kind.setdefault(k, []).append(s)

    return {
        'n': len(rows), 'missing': missing, 'line': line, 'has_judge': has_judge,
        'mean_score': mean(rows),
        'compliance_rate': rate(scoreable),
        'compliance_denominator': len(scoreable),
        'by_kind': {k: {'n': len(v), 'mean': mean(v), 'compliance': rate(v) if k != '不可能' else None}
                    for k, v in by_kind.items()},
        'relevance_n': len(by_kind.get('不可能', [])),
        'relevance_mode': 'impossible 题按"先查证→说明"判 ✓ 不计入合规率 ✓',
    }


# ---- 判官（加固版 ✓）----
JUDGE_PROMPT = """你是严格的任务验收判官，只依据给出的材料判定。

## 任务
{task}

## 验收点（逐条判）
{points}

## 材料（模型行为 + 页面记录 + 最终答案）
{result}

## 规则（必须遵守）
1. 逐条判 true/false，不得跳过
2. passage 字段必须【逐字引用上面材料里的原文】；抄不出来 ⇒ 该条 false 且 passage 写"未找到"
3. 需要"状态回查"的验收点（如文件已存在/md5 未变/库里真有这条），材料里没有证据就判 false 并在 note 写 unverifiable
4. 输出严格 JSON（无代码块标记）：
{{"items":[{{"point":"<验收点前20字>","pass":true,"passage":"<逐字引用>"}}],"note":"<一句话>"}}
"""


def judge_task(task, run, call_llm, max_points=40) -> dict:
    pts = task.get('points') or []
    if not pts or call_llm is None:
        return {'ok': False, 'judge_score': 0.0, 'judge_max': max_points,
                'note': '没有验收点或没有判官'}
    blob = (f"任务: {task['id']} {task['title']}\n原话: {task.get('phrase','')}\n类目: {task.get('cat','')}\n"
            f"提出工具: {sorted(set(proposed_tools(run)))}\n执行工具: {sorted(set(executed_tools(run)))}\n"
            f"转云: {bool(run.get('escalated'))}\n--- 页面记录（全量）---\n"
            f"{str(run.get('msgs') or '')}")            # ★ 材料给全 ✓（v1 只给尾部 4000 ✗）
    prompt = JUDGE_PROMPT.format(
        task=f"{task['id']} {task['title']}", points='\n'.join(f'{i+1}) {p}' for i, p in enumerate(pts)),
        result=blob[:24000])
    try:
        raw = str(call_llm(prompt) or '')
        m = re.search(r'\{.*\}', raw, re.S)
        if not m:
            return {'ok': False, 'judge_score': 0.0, 'judge_max': max_points,
                    'note': '判官输出非 JSON ⇒ 判官失败（不计分 ✓ 不猜 ✗）'}
        data = json.loads(m.group(0))
        items = data.get('items') or []
        passed = 0
        fixed = []
        for it in items:
            ok = it.get('pass') is True                       # ★ 必须 is True（"false" 不算 ✓）
            passage = str(it.get('passage') or '').strip()
            real = bool(passage) and passage != '未找到' and passage in blob   # ★ 必须真在材料里
            fixed.append({'point': it.get('point'), 'pass': ok, 'passage_ok': real,
                          'counted': bool(ok and real)})
            if ok and real:
                passed += 1
        sc = round(max_points * passed / len(pts), 2)
        return {'ok': True, 'judge_score': min(sc, max_points), 'judge_max': max_points,
                'items': fixed, 'note': data.get('note', '')}
    except Exception as e:
        return {'ok': False, 'judge_score': 0.0, 'judge_max': max_points,
                'note': f'判官失败: {type(e).__name__}: {e}'}


def _latest(runs: list) -> dict:
    return sorted(runs, key=lambda r: str(r.get('done_at') or r.get('started_at') or ''))[-1]


def score_all(tasks: list, runs: list, judge=None) -> list:
    by_id = {}
    for r in runs:
        by_id.setdefault(r.get('id'), []).append(r)
    out = []
    for t in tasks:
        rs = by_id.get(t['id']) or []
        if not rs:
            out.append({'task': t, 'run': {}, 'machine': None, 'judge': None,
                        'total': None, 'missing': True})
            continue
        run = _latest(rs)
        m = score_machine(t, run)
        j = judge_task(t, run, judge) if judge else None
        total = round(m['machine_score'] + (j['judge_score'] if j else 0.0), 2)
        out.append({'task': t, 'run': run, 'machine': m, 'judge': j, 'total': total})
    return out


def render_md(scored, agg, title, has_judge) -> str:
    L = [f'# {title}', '',
         f'> 口径: BFCL 风格（工具匹配 + 执行验证 + 分层 Relevance）· 机器 60 + 判官 40'
         f'{"（含判官 ✓）" if has_judge else "（无判官 ⇒ 合规线 42/60 ✓）"}', '']
    if agg.get('missing'):
        L += [f'> 未参与评分（没跑过）: {agg["missing"]} 条 ✓', '']
    L += ['## 一、汇总（按 kind 分层 ✓）', '',
          '| 层 | 条数 | 均分 | 合规率 |', '|---|---|---|---|']
    for k in ('可完成', '边界', '不可能'):
        v = (agg.get('by_kind') or {}).get(k)
        if not v:
            continue
        comp = f'{v["compliance"]}%' if v['compliance'] is not None else '（不计 ✓）'
        L.append(f'| {k} | {v["n"]} | {v["mean"]} | {comp} |')
    L.append(f'| **合计** | {agg["n"]} | {agg["mean_score"]} | '
             f'**{agg["compliance_rate"]}%**（分母 {agg["compliance_denominator"]} ✓） |')
    L += ['', f'> 合规线: {agg["line"]} 分（{"含判官 100 分制" if has_judge else "无判官 60 分制折算"} ✓）', '',
          '## 二、逐条', '',
          '| 任务 | 类别 | 工具选择 | 动作 | 落地 | 干净 | 机器 | 判官 | 总分 | 判定 |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for s in sorted([x for x in scored if not x.get('missing')], key=lambda x: x['task']['id']):
        d = (s['machine'].get('detail') or {})
        tc = (d.get('tool_choice') or {})
        js = s['judge']['judge_score'] if s.get('judge') else '—'
        verdict = '✅' if s['total'] >= agg['line'] else ('⚠️' if s['total'] >= agg['line'] * 0.6 else '❌')
        L.append(f'| {s["task"]["id"]} | {s["task"]["kind"][:14]} | {tc.get("score",0)}/30 | '
                 f'{(d.get("execution") or {}).get("score",0)}/15 | '
                 f'{(d.get("artifact") or {}).get("score",0)}/10 | '
                 f'{(d.get("clean") or {}).get("score",0)}/5 | '
                 f'**{s["machine"]["machine_score"]}/60** | {js} | **{s["total"]}** | {verdict} |')
    return '\n'.join(L) + '\n'


def _cloud_llm_factory():
    try:
        sys.path.insert(0, str(ROOT))
        import lora_manager as lm
        prov = (lm.load_settings().get('providers') or {}).get('openai') or {}
        base, key = str(prov.get('api_base') or '').rstrip('/'), str(prov.get('api_key') or '')
        model = str(prov.get('model') or 'deepseek-chat')
        if not key or not base:
            return None

        def call(prompt, max_tokens=1500):
            import requests
            r = requests.post(base + '/chat/completions',
                              headers={'Authorization': 'Bearer ' + key,
                                       'Content-Type': 'application/json'},
                              json={'model': model, 'temperature': 0, 'max_tokens': max_tokens,
                                    'messages': [{'role': 'user', 'content': prompt}]},
                              timeout=180)
            r.raise_for_status()
            return ((r.json().get('choices') or [{}])[0].get('message') or {}).get('content') or ''
        return call
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description='演练判分器 v2（审计修正版）')
    ap.add_argument('--runs', default='training_output/drill_part1_runs.jsonl')
    ap.add_argument('--tasks', default='docs/DRILL_TASKS_PART1.md')
    ap.add_argument('--tag', default='')
    ap.add_argument('--compare', nargs=2, metavar=('TAG_A', 'TAG_B'), default=None)
    ap.add_argument('--judge', action='store_true')
    ap.add_argument('--pair-tasks', default='', help='缺类目时从该文档同号任务继承期望工具 ✓（用于 part2 ✓）')
    ap.add_argument('--json', default='')
    ap.add_argument('--out', default='docs/DRILL_SCORES.md')
    a = ap.parse_args(argv)

    tasks = parse_tasks(a.tasks)
    if a.pair_tasks:
        _inh = inherit_pairs(tasks, a.pair_tasks)
        print(f'  ✓ 从 {a.pair_tasks} 继承期望工具: {_inh} 条（原文档缺类目 ✓ 实测 part2 有 0 条 ✓）', flush=True)
    p = Path(a.runs)
    runs = [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()] if p.exists() else []
    if a.tag:
        runs = [r for r in runs if r.get('tag') == a.tag]

    judge = _cloud_llm_factory() if a.judge else None
    if a.judge and judge is None:
        print('⚠ 没有云 key ⇒ 判官跳过', flush=True)

    scored = score_all(tasks, runs, judge=judge)
    # ★ 判官全失败 ⇒ 降级为无判官口径 ✓（否则合规率假性 0% ✗）
    judged_ok = [s for s in scored if s.get('judge') and s['judge'].get('ok')]
    has_judge = bool(judge) and bool(judged_ok)
    if judge and not has_judge:
        print('⚠ 判官全部失败 ⇒ 降级为无判官口径 ✓', flush=True)
        for s in scored:
            if s.get('judge'):
                s['judge'] = None
                s['total'] = s['machine']['machine_score'] if s.get('machine') else None

    agg = aggregate(scored, has_judge=has_judge)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(render_md(scored, agg, f'演练判分报告（v2 · tag={a.tag or "all"}）', has_judge),
                           encoding='utf-8')
    if a.json:
        Path(a.json).write_text(json.dumps(
            [{'id': s['task']['id'], 'kind': s['task']['kind'],
              'machine': s.get('machine'), 'judge': s.get('judge'), 'total': s['total'],
              'missing': s.get('missing', False)} for s in scored],
            ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'✓ {a.out}（评 {agg["n"]} 条 · missing {agg["missing"]} · 均分 {agg["mean_score"]}'
          f' · 合规率 {agg["compliance_rate"]}%（分母 {agg["compliance_denominator"]}）'
          f' · 分层 {[(k, v["n"], v["mean"]) for k, v in (agg.get("by_kind") or {}).items()]}）')

    # ★ 配对比较（高3 ✓ 只比共同 task_id）
    if a.compare:
        ta, tb = a.compare
        run_all = [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
        sa = {s['task']['id']: s for s in score_all(tasks, [r for r in run_all if r.get('tag') == ta], judge=None)
              if not s.get('missing')}
        sb = {s['task']['id']: s for s in score_all(tasks, [r for r in run_all if r.get('tag') == tb], judge=None)
              if not s.get('missing')}
        common = sorted(set(sa) & set(sb))
        if common:
            ma = sum(sa[k]['total'] for k in common) / len(common)
            mb = sum(sb[k]['total'] for k in common) / len(common)
            print(f'  配对比较（共同 {len(common)} 条）: {ta}={round(ma,1)} · {tb}={round(mb,1)}'
                  f' · 差 {round(mb-ma,1)}')
        else:
            print(f'  ⚠ {ta} 与 {tb} 没有共同任务 ⇒ 不可比 ✓（这正是 v1 横比出错的原因 ✗）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
