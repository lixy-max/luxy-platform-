#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测环境快照/对比（T3 · 原则三 M4 环境隔离 ✓）2026-10-19

解决 h322 那次的问题：base 臂自己漂移 −7.5（t=−2.30 显著 ✗）
原因：跑批会改平台状态（workflow configs / KB / LoRA / 模型 ✓）⇒ 前后不可比 ✗

用法:
  python scripts/eval_env.py snapshot --label before_h322
  ... 跑批 ...
  python scripts/eval_env.py snapshot --label after_h322
  python scripts/eval_env.py diff before_h322 after_h322

输出: data/eval_snapshots/<label>.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / 'data' / 'eval_snapshots'
API = 'http://127.0.0.1:8001'
VLLM = 'http://127.0.0.1:8000'


def _get(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except Exception:
        return '?'


def take_snapshot() -> dict:
    snap = {'ts': datetime.now().isoformat(timespec='seconds')}

    # 1 工作流配置（最容易被跑批改 ✓）
    cfgs = {}
    for p in sorted(ROOT.glob('workflow_config*.json')):
        cfgs[p.name] = _sha(p)
    snap['workflow_configs'] = cfgs

    # 2 KB（名字 + 块数）
    kbs = _get(f'{API}/kb/list') or {}
    snap['kbs'] = sorted([{'name': k.get('name'), 'chunks': k.get('total_chunks', k.get('count', 0))}
                          for k in (kbs.get('knowledge_bases') or [])], key=lambda x: x['name'] or '')

    # 3 LoRA 列表
    loras = _get(f'{API}/loras') or {}
    lst = loras.get('loras') if isinstance(loras, dict) else (loras if isinstance(loras, list) else [])
    snap['loras'] = sorted([x.get('name') if isinstance(x, dict) else str(x) for x in (lst or [])])

    # 4 vLLM 当前模型（含 LoRA 挂载 ✓）
    models = _get(f'{VLLM}/v1/models') or {}
    snap['vllm_models'] = sorted([m.get('id') for m in (models.get('data') or [])])

    # 5 训练任务数
    tr = _get(f'{API}/training/list') or {}
    tasks = tr.get('tasks') if isinstance(tr, dict) else (tr if isinstance(tr, list) else [])
    snap['training_tasks'] = len(tasks or [])

    # 6 磁盘数据文件哈希（数据集 / 学习产物 ✓）
    files = {}
    for pat in ('training_data/*.jsonl', 'agent_kb/90_learned.md'):
        for p in ROOT.glob(pat):
            files[str(p.relative_to(ROOT))] = _sha(p)
    snap['data_files'] = files

    return snap


def diff_snaps(a: dict, b: dict) -> dict:
    out = {'workflow_configs': {}, 'kbs': {}, 'loras': {}, 'vllm_models': {}, 'data_files': {}, 'other': {}}

    # workflow configs
    ka, kb = set(a.get('workflow_configs') or {}), set(b.get('workflow_configs') or {})
    for n in sorted(ka - kb):
        out['workflow_configs'][n] = '被删除 ✗'
    for n in sorted(kb - ka):
        out['workflow_configs'][n] = '新增 ✓'
    for n in sorted(ka & kb):
        if a['workflow_configs'][n] != b['workflow_configs'][n]:
            out['workflow_configs'][n] = '内容变了 ✗'

    # KB
    ad = {x['name']: x['chunks'] for x in (a.get('kbs') or [])}
    bd = {x['name']: x['chunks'] for x in (b.get('kbs') or [])}
    for n in sorted(set(ad) | set(bd)):
        if ad.get(n) != bd.get(n):
            out['kbs'][n] = f"{ad.get(n)} → {bd.get(n)}"

    # LoRA / 模型 / 数据文件
    for key in ('loras', 'vllm_models'):
        sa, sb = set(a.get(key) or []), set(b.get(key) or [])
        if sa != sb:
            out[key] = {'removed': sorted(sa - sb), 'added': sorted(sb - sa)}
    fa, fb = a.get('data_files') or {}, b.get('data_files') or {}
    for n in sorted(set(fa) | set(fb)):
        if fa.get(n) != fb.get(n):
            out['data_files'][n] = '变了'

    if a.get('training_tasks') != b.get('training_tasks'):
        out['other']['training_tasks'] = f"{a.get('training_tasks')} → {b.get('training_tasks')}"

    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('snapshot')
    s.add_argument('--label', required=True)
    d = sub.add_parser('diff')
    d.add_argument('a')
    d.add_argument('b')
    args = ap.parse_args()

    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    if args.cmd == 'snapshot':
        snap = take_snapshot()
        p = SNAP_DIR / f'{args.label}.json'
        p.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'✓ 快照已存: {p}')
        print(f'  workflow configs: {len(snap["workflow_configs"])} 个')
        print(f'  KB: {len(snap["kbs"])} 个 · LoRA: {len(snap["loras"])} 个')
        print(f'  vLLM 模型: {len(snap["vllm_models"])} 个')
        print(f'  数据文件: {len(snap["data_files"])} 个')
        return 0

    pa, pb = SNAP_DIR / f'{args.a}.json', SNAP_DIR / f'{args.b}.json'
    for p in (pa, pb):
        if not p.exists():
            print(f'✗ 快照不存在: {p}')
            return 1
    a = json.loads(pa.read_text(encoding='utf-8'))
    b = json.loads(pb.read_text(encoding='utf-8'))
    out = diff_snaps(a, b)

    total = sum(len(v) if isinstance(v, dict) else 0 for v in out.values())
    print(f'\n=== 环境漂移检查: {args.a} → {args.b} ===')
    if total == 0:
        print('  ✅ 环境无变化 ✓ 两次跑批可比 ✓')
        return 0
    print(f'  ⚠ 检测到 {total} 处变化 ✗ 两次跑批【不可直接比】⚠')
    for k, v in out.items():
        if v:
            print(f'  [{k}]')
            if isinstance(v, dict):
                for name, st in list(v.items())[:10]:
                    print(f'    · {name}: {st}')
    print('\n  ⇒ 建议：还原到同一状态再跑（或记录漂移并在报告里说明 ⚠）')
    return 2


if __name__ == '__main__':
    sys.exit(main())
