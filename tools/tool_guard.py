# -*- coding: utf-8 -*-
"""tool_guard.py —— 工具可执行性硬门（修幻影工具：37/68 有文档无 handler）

数据来源：docs/PHANTOM_TOOLS_20261022.json（实测 37 个工具"有文档、没 handler"）
用途：
  · /agent/tools 标记/过滤不可执行工具（executable 字段 / executable_only 参数）
  · /agent/tools/health 返回健康度
  · 其他模块可 is_executable(name) 做硬门
"""
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PHANTOM_FILE = PROJECT_DIR / "docs" / "PHANTOM_TOOLS_20261022.json"


def load_phantom():
    """返回 phantom 工具名集合（文件缺失则空集）"""
    try:
        d = json.loads(PHANTOM_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in (d.get("tools") or []))
    except Exception:
        return set()


def is_executable(name, phantom=None):
    phantom = load_phantom() if phantom is None else phantom
    return str(name) not in phantom


def filter_tools(tools, executable_only=False):
    """tools: dict[name, meta] → 过滤/标注"""
    phantom = load_phantom()
    out = {}
    for name, meta in (tools or {}).items():
        m = dict(meta) if isinstance(meta, dict) else {"desc": str(meta)}
        m["executable"] = is_executable(name, phantom)
        if executable_only and not m["executable"]:
            continue
        out[name] = m
    return out


def _doc_names():
    try:
        docs = json.loads((PROJECT_DIR / "agent_kb" / "_tool_docs.json").read_text(encoding="utf-8"))
        return set((docs.get("tools") or {}).keys())
    except Exception:
        return set()


def health():
    """★ 修（2026-10-26）：phantom 名单是历史快照（37 个），当前文档只有 68 个工具，
    其中 8 个仍在文档里 → 不能直接用历史名单长度算"幻影数"。
    phantom_count  = 当前文档中仍属幻影的工具数（真实可修复项）
    phantom_historical = 历史名单长度（保留追溯）"""
    phantom = load_phantom()
    names = _doc_names()
    total = len(names)
    effective = phantom & names
    return {"total_documented": total,
            "phantom_count": len(effective),
            "phantom_historical": len(phantom),
            "executable_count": max(0, total - len(effective)),
            "phantom_tools": sorted(effective)[:50],
            "source": str(PHANTOM_FILE)}
