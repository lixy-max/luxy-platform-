# -*- coding: utf-8 -*-
"""
共享状态守卫：一次测试运行不许改动项目里的真实数据。

为什么需要（本会话两次真实事故，都是"共享状态没隔离"）：
1. 有用例不带 ?name= 调 POST /workflow/config/import → normalize_config 重写发布配置，
   抹掉 9 个节点的 DAG 布局坐标、并把 CRLF 改成 LF。**静默损坏**。
2. 质量测试没隔离缓存 → extract 节点（默认 cache=true）读到真实缓存里的旧输出，
   于是"编造句能拿 grounding 1.0"这个**错误结论**被写进文档和测试（真值 0.3235）。

教训：共享状态只要有一处没隔离，就会产生"假读数"，而**假读数比没有读数更糟** ——
它会让你把错误结论当成发现。所以每个测试模块结束时要逐项核对真实数据没被动过。
"""
import hashlib
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def _count(pattern):
    return len(list(PROJECT.glob(pattern)))


def _sha(rel):
    p = PROJECT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _size(rel):
    p = PROJECT / rel
    return p.stat().st_size if p.exists() else None


def snapshot_shared_state():
    """拍一张真实数据快照（只读）。"""
    return {
        "outputs/result_*.json": _count("outputs/result_*.json"),
        "cache/nodes/*": _count("cache/nodes/*"),
        "training_data/*.jsonl": _count("training_data/*.jsonl"),
        "uploads/*": _count("uploads/*"),
        "settings.json": _sha("settings.json"),
        "workflow_config.json": _sha("workflow_config.json"),
        "data/workbench.db": _size("data/workbench.db"),
    }


_HINTS = {
    "outputs/result_*.json": "测试不该产出真实结果文件 → patch we.OUTPUT_DIR / lora_manager.OUTPUT_DIR 到临时目录",
    "cache/nodes/*": "测试不该写真实缓存 → patch we.CACHE_ENABLED=False（引擎已有该开关）",
    "training_data/*.jsonl": "测试不该写真实数据集 → patch PROJECT_DIR / TRAINING_DATA 到临时目录",
    "uploads/*": "测试不该往真实 uploads/ 放文件",
    "settings.json": "测试不该改写用户的 Key 文件 → 把设置文件路径 patch 到临时目录",
    "workflow_config.json": "写配置的端点必须带 ?name= 指向临时文件",
    "data/workbench.db": "测试不该写用户的历史库 → 把 DB 路径 patch 到临时文件",
}


def diff_shared_state(before, after=None):
    after = after if after is not None else snapshot_shared_state()
    return [(k, before[k], after.get(k), _HINTS.get(k, ""))
            for k in before if before[k] != after.get(k)]


def assert_shared_state_unchanged(before, module_name=""):
    changed = diff_shared_state(before)
    if changed:
        lines = [f"❌ {module_name or '测试'} 改动了项目的真实数据（共享状态未隔离）："]
        for k, b, a, hint in changed:
            lines.append(f"   - {k}: {b} → {a}")
            if hint:
                lines.append(f"     → {hint}")
        raise AssertionError("\n".join(lines))
