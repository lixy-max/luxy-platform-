#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一次工作流运行的产物，按节点配置【回流到知识库】（CHAIN_AUDIT.md 的 G1）。

背景（实测 2026-10-15）:
  workflow_engine 对知识库只有【读】—— `GET /kb/list`(557) 与 `POST /kb/{name}/query`(1514)；
  `/workflow/run`（lora_manager.py:1160）全程不碰 kb./learn_sink。
  ⇒ 跑 100 次工作流，知识库一点不变 ✗（"实时知识库学习"这条链没接上）

本模块补上这条接线:
  1. 读结果文件（含 `_meta.traces`：每个节点的【真实输入/输出】✓ 引擎已落盘）
  2. 取配置里 `kb_write` 不为 off 的节点（**默认 off ⇒ 完全向后兼容** ✓）
  3. 清洗（去绝对路径 / 压空白 / 截断）→ 汇成一份 Markdown，**可照做的要点放最前面**
  4. 入库到目标库（默认 `workflow_learned`）
     ★ 默认【拒写 agent_use】✗ —— 它是工具调用经验库（宝贵资产），
       混入工作流长文会"进得去查不到"（已实测 5/5 检索落空 ✓）

用法:
  # 当库用（lora_manager 在 /workflow/run 成功后调用）
  python -c "import workflow_learn as w; print(w.ingest('outputs/result_x.json', 'workflow_config.json'))"
  # 当 CLI 用（可 --dry-run 只看会写什么）
  python workflow_learn.py --result outputs/result_x.json --config workflow_config.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent
LEARN_DIR = ROOT / 'data' / 'workflow_learn'
DEFAULT_TARGET = 'workflow_learned'
GUARDED_KBS = {'agent_use'}          # 宝贵资产：默认拒写 ✓
ALLOW_GUARDED_ENV = 'WORKFLOW_LEARN_ALLOW_AGENT_USE'
MAX_SECTIONS = 20                    # 每个目标库的文档最多保留最近 N 次运行
IN_LIMIT, OUT_LIMIT = 1200, 1600     # 单节点输入/输出摘录上限（字符）

_ABS_PATH_RE = re.compile(
    r'(?:/home/[\w.\-/]+|/mnt/[a-z]/[\w.\-/]+|/tmp/[\w.\-/]+|[A-Za-z]:\\\\?[\w.\\\-/]+)')


def _clean(text, limit: int = 0) -> str:
    """去绝对路径（本机信息/隐私 + 换机器即失效）、压空白、按需截断。"""
    t = str(text or '')
    t = _ABS_PATH_RE.sub('<path>', t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    if limit and len(t) > limit:
        t = t[:limit] + f'…（截断，原文 {len(t)} 字）'
    return t


def _guard(kb: str) -> None:
    if kb in GUARDED_KBS and os.environ.get(ALLOW_GUARDED_ENV) != '1':
        raise ValueError(
            f"拒绝写入 {kb}：它是工具调用经验库（宝贵资产）。"
            f"工作流产物应写自己的库（默认 {DEFAULT_TARGET}）；"
            f"确需写入请显式设 {ALLOW_GUARDED_ENV}=1")


def plan_targets(config: dict) -> dict:
    """从配置里取"要回流的节点" ⇒ {node_id: (mode, target)}；默认空（全 off ✓）。"""
    out = {}
    for n in (config or {}).get('nodes') or []:
        cfg = n.get('kb_write')
        if not isinstance(cfg, dict):
            continue
        mode = str(cfg.get('mode') or 'off').lower()
        if mode not in ('append', 'replace'):
            continue
        target = str(cfg.get('target') or DEFAULT_TARGET).strip() or DEFAULT_TARGET
        _guard(target)
        out[str(n.get('id'))] = (mode, target)
    return out


def build_docs(result: dict, config: dict, run_id: str) -> dict:
    """按目标库分组生成 Markdown ⇒ {target: md}。"""
    targets = plan_targets(config)
    if not targets:
        return {}
    traces = ((result.get('_meta') or {}).get('traces') or {})
    nodes = result.get('nodes') or {}
    by_target = {}
    for nid, (mode, target) in targets.items():
        by_target.setdefault(target, []).append((nid, mode))

    docs = {}
    for target, items in by_target.items():
        L = [f'# 工作流学习：{_clean(result.get("workflow") or "未命名")}', '',
             f'> 来源运行: `{run_id}` · 源文件: {_clean(result.get("source_file"))} · '
             f'状态: {result.get("status")} · 生成: {time.strftime("%Y-%m-%d %H:%M")}',
             '> 本文由工作流产物自动回流（节点 `kb_write` 开启）——「要点」在前，轨迹摘录在后 ✓', '']
        # ① 可照做的要点（失败/降级/告警 ⇒ 下次怎么避免）
        meta = result.get('_meta') or {}
        pts = []
        for f in (meta.get('failures') or []):
            pts.append(f"节点 `{f.get('node')}` 在 {f.get('stage')} 阶段失败："
                       f"{_clean(f.get('error') or f.get('reason'), 160)} —— 下次先核对该节点依赖与参数")
        for d in (meta.get('degraded') or []):
            pts.append(f"节点 `{d.get('node')}` 降级：{_clean(d.get('reason'), 160)}")
        for w in (meta.get('config_warnings') or []):
            pts.append(f"配置告警：{_clean(w, 160)}")
        L += ['## 一、本次要点（可照做）', '']
        L += [f'- {p}' for p in pts] or ['- 本次运行无失败/降级记录 ✓']
        final = _clean(result.get('final_report'), 2000)
        if final:
            L += ['', '## 二、最终报告（摘要）', '', final]
        # ② 节点轨迹
        L += ['', '## 三、节点轨迹（仅 kb_write 启用的节点）', '']
        for nid, mode in items:
            tr = traces.get(nid) or {}
            node = next((n for n in ((config or {}).get('nodes') or []) if n.get('id') == nid), {})
            L += [f'### 节点 `{nid}`（{node.get("type")} · lora={node.get("lora")} · mode={mode}）', '',
                  '**输入摘要**', '', '```', _clean(tr.get('input') or nodes.get(nid) or '', IN_LIMIT), '```', '',
                  '**输出摘要**', '', '```', _clean(tr.get('output') or '', OUT_LIMIT), '```', '']
        docs[target] = '\n'.join(L) + '\n'
    return docs


def _write_learn_file(target: str, md: str, mode: str) -> Path:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    p = LEARN_DIR / f'learned_{target}.md'
    if mode == 'replace' or not p.exists():
        p.write_text(md, encoding='utf-8')
        return p
    # append: 追加一节，并只保留最近 MAX_SECTIONS 节（避免无限增长 ✗）
    old = p.read_text(encoding='utf-8')
    parts = old.split('\n---\n')
    parts = ([parts[0]] + parts[1:]) if len(parts) > 1 else [old]
    body = '\n---\n'.join(parts[-(MAX_SECTIONS - 1):])
    p.write_text(body.rstrip() + '\n\n---\n\n' + md, encoding='utf-8')
    return p


def ingest(result_path, config_path=None, dry_run: bool = False) -> dict:
    """入口：读结果 + 配置 → 生成文档 → 入库。返回摘要（含 error 字段而不是抛异常 ✓）。"""
    summary = {'result': str(result_path), 'targets': {}, 'ingested': {}, 'dry_run': bool(dry_run)}
    try:
        rp = Path(result_path)
        if not rp.is_absolute():
            rp = ROOT / rp
        result = json.loads(rp.read_text(encoding='utf-8'))
        cfg = {}
        if config_path:
            cp = Path(config_path)
            if not cp.is_absolute():
                cp = ROOT / cp
            if cp.exists():
                cfg = json.loads(cp.read_text(encoding='utf-8'))
        targets = plan_targets(cfg)
        summary['targets'] = {k: {'mode': v[0], 'target': v[1]} for k, v in targets.items()}
        if not targets:
            summary['skipped'] = '配置里没有节点开启 kb_write（默认全 off ✓）'
            return summary
        docs = build_docs(result, cfg, run_id=rp.name)
        summary['chars'] = {k: len(v) for k, v in docs.items()}
        if dry_run:
            summary['preview'] = {k: v[:600] for k, v in docs.items()}
            return summary
        import kb as kbmod
        for target, md in docs.items():
            modes = {m for _, (m, t) in targets.items() if t == target}
            mode = 'replace' if 'replace' in modes else 'append'
            fp = _write_learn_file(target, md, mode)
            try:
                kbmod.create_kb(target, description='工作流产物回流（自动生成 ✓）')
            except TypeError:
                try:
                    kbmod.create_kb(target, description='工作流产物回流（自动生成 ✓）')
                except Exception:
                    pass
            except ValueError:
                pass                      # 已存在 ✓
            except Exception:
                pass
            info = kbmod.add_documents_to_kb(target, [str(fp)])
            summary['ingested'][target] = {'file': fp.name, 'mode': mode, 'info': info}
    except Exception as e:
        summary['error'] = f'{type(e).__name__}: {e}'
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description='把工作流产物回流到知识库（G1）')
    ap.add_argument('--result', required=True, help='outputs/result_*.json')
    ap.add_argument('--config', default='', help='workflow_config*.json（提供才知道哪些节点开 kb_write）')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    s = ingest(a.result, a.config or None, dry_run=a.dry_run)
    print(json.dumps(s, ensure_ascii=False, indent=1)[:4000])
    return 0 if not s.get('error') else 1


if __name__ == '__main__':
    sys.exit(main())
