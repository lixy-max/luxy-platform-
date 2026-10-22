#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 agent 工具目录（agent_kb/10_tool_catalog.md）——**从 index3.html 抽取**，不手工维护。

为什么要生成而不是手写：工具会变，手写的目录必然过期；
目录一旦过期，小模型就会调用不存在的工具或漏参数。
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "webui" / "index3.html").read_text(encoding="utf-8")

# 领域分组（按名字前缀归类，方便小模型按意图找工具）
GROUPS = [
    ("工作流：查看与配置", ("list_workflow", "validate_workflow")),
    ("工作流：节点编辑", ("add_workflow_node", "update_workflow_node", "delete_workflow_node")),
    ("工作流：运行与结果", ("run_workflow", "get_workflow_status", "get_workflow_result",
                            "list_workflow_results", "list_workflow_uploads")),
    ("训练", ("start_training", "get_training_status", "list_trainings", "build_dataset",
              "list_available_datasets", "list_available_models")),
    ("评测", ("run_eval", "get_eval_status")),
    ("LoRA 管理", ("list_loras", "get_loras_registry", "load_lora", "unload_lora")),
    ("知识库", ("list_knowledge_bases", "create_knowledge_base", "query_knowledge_base")),
    ("文档助手：Excel", ("list_sheets", "read_sheet", "filter_rows", "group_sum", "write_sheet")),
    ("文档助手：Word", ("read_docx", "write_docx")),
    ("文档助手：流程", ("list_doc_flows", "run_doc_flow", "save_doc_flow")),
    ("文件与历史", ("list_uploads", "list_history", "get_history_detail")),
    ("质量与系统", ("get_quality_summary", "get_settings", "get_vllm_status",
                    "switch_model", "list_running_tasks")),
]


def _strip_comments(s):
    """去掉 // 行注释。

    为什么必须做：块边界落在“下一个 AGENT_TOOLS.”之前，而两段代码之间常有注释，
    例如 `// 写操作：dangerous:true → 系统会弹窗…`。直接对整块做正则会把注释里的
    字样当成属性，导致 read_docx / group_sum 被误标为危险工具（实测踩过）。
    """
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in s.splitlines())


def extract():
    tools = {}
    for m in re.finditer(r"AGENT_TOOLS\.([a-z_0-9]+)\s*=\s*\{", HTML):
        name = m.group(1)
        nxt = HTML.find("AGENT_TOOLS.", m.end())
        block = HTML[m.end(): nxt if nxt > 0 else m.end() + 2500][:2500]
        code = _strip_comments(block)

        keys = set()
        for pm in re.finditer(r"pickArg\(\s*args\s*,\s*\[([^\]]*)\]", code):
            keys |= set(re.findall(r"['\"]([A-Za-z_0-9]+)['\"]", pm.group(1)))
        keys |= set(re.findall(r"args\.([A-Za-z_0-9]+)", code))

        ep = sorted(set(re.findall(r"API_BASE\}/([A-Za-z0-9_/{}$.-]*)", code)))

        # 危险标记：只看对象开头的属性区（首 200 字符），且已去注释
        head = code.strip()[:200]
        dangerous = bool(re.search(r"dangerous\s*:\s*true", head))

        # HTTP 方法：取首个 fetch 调用点之后最近的 method:，否则按有无 body: 推断 POST
        method = "GET"
        fm = re.search(r"fetch\(\s*`\$\{API_BASE\}", code)
        if fm:
            tail = code[fm.end():]
            mm = re.search(r"method\s*:\s*['\"]([A-Z]+)['\"]", tail)
            if mm:
                method = mm.group(1)
            elif re.search(r"\bbody\s*:", tail[:800]):
                method = "POST"

        # ★ P-45b（2026-10-19）: 通用分发 handler（`handler: (args) => callDocTool('<name>', …)`）
        #   块里没有 fetch ✗ ⇒ 上面会把它判成 GET ✗（实测 test_write_tools_are_post 立刻变红 ✓）。
        #   这类工具统一走 POST /docs/tool（后端唯一分发端点）。
        if re.search(r"callDocTool\(\s*['\"]", code):
            ep = ["docs/tool"]
            method = "POST"

        tools[name] = {
            "dangerous": dangerous,
            "args": sorted(keys),
            "endpoint": ep[0] if ep else "",
            "method": method,
        }
    return tools


def main():
    tools = extract()
    lines = ["# 工具目录（自动生成，请勿手改）",
             "",
             f"本项目共有 **{len(tools)}** 个 agent 工具，其中 **"
             f"{sum(1 for t in tools.values() if t['dangerous'])}** 个属于“会改数据/训练/模型”的危险操作"
             "（调用前系统会弹窗让人确认）。",
             "",
             "> 本文件由 `scripts/gen_agent_kb.py` 从 `webui/index3.html` 抽取生成。",
             "",
             "**参数写法**：下面 `args` 列出的是**可接受的参数名**（同一个参数的别名都列出，"
             "任选其一即可）。标 `—` 表示无需参数或参数名不固定，请先调用只读工具确认。",
             ""]
    used = set()
    for title, names in GROUPS:
        items = [(n, tools[n]) for n in names if n in tools]
        if not items:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 工具 | 危险 | 参数 |")
        lines.append("|---|---|---|")
        for n, t in items:
            used.add(n)
            a = ", ".join(f"`{k}`" for k in t["args"][:8]) or "—"
            lines.append(f"| `{n}` | {'⚠ 是' if t['dangerous'] else '否'} | {a} |")
        lines.append("")
    rest = sorted(set(tools) - used)
    if rest:
        lines.append("## 其它")
        lines.append("")
        lines.append("| 工具 | 危险 | 参数 |")
        lines.append("|---|---|---|")
        for n in rest:
            t = tools[n]
            a = ", ".join(f"`{k}`" for k in t["args"][:8]) or "—"
            lines.append(f"| `{n}` | {'⚠ 是' if t['dangerous'] else '否'} | {a} |")
        lines.append("")

    out = ROOT / "agent_kb" / "10_tool_catalog.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  已生成 {out.relative_to(ROOT)}：{len(tools)} 个工具")

    manifest = ROOT / "agent_kb" / "_tools.json"
    manifest.write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已生成 {manifest.relative_to(ROOT)}（供其它脚本复用）")


if __name__ == "__main__":
    main()
