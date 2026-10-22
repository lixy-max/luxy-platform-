#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent 工具选择回归测试（A/B）：
  A 组：只给工具清单（基线）
  B 组：额外注入 agent_use 知识库的 top-k 片段（模拟项目的 KB 注入）
评分：每题给出"期望工具集合"（可辩护的多个答案都算对，与上次口径一致）。

为什么做 A/B：知识库有没有用，只有对照才知道。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent   # 从脚本位置推导，便于移植
VLLM = "http://127.0.0.1:8000/v1/chat/completions"


def _model_id():
    """vLLM 需要**实际部署的模型名**；用错名字会 404。这里动态取。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=10) as r:
            d = json.loads(r.read())
        return d["data"][0]["id"]
    except Exception:
        import os
        env = os.environ.get('BASE_MODEL_PATH', '').strip()
        if env:
            return env
        return str(Path.home() / 'models' / 'Qwen3-4B-Instruct')


MODEL_ID = _model_id()
KB_API = "http://127.0.0.1:8001/kb/agent_use/query"

HTML = (ROOT / "webui" / "index3.html").read_text(encoding="utf-8")
TOOLS = json.loads((ROOT / "agent_kb" / "_tools.json").read_text(encoding="utf-8"))

# ---- 测试集：自然语言 -> 期望工具（可辩护的多个都算对）----
CASES = [
    # 按规则 0，先 list_workflow_configs 再 run_workflow 是可辩护的（原来只给 run_workflow，评分偏严）
    ("我想跑一下论文分析的工作流", ["run_workflow", "list_workflow_configs"]),
    ("有哪些工作流模板可以选", ["list_workflow_configs"]),
    ("上次那个任务跑完了吗", ["get_workflow_status", "list_running_tasks"]),
    ("把上次分析的报告拿给我看看", ["get_workflow_result", "list_workflow_results"]),
    ("之前跑过哪些结果", ["list_workflow_results"]),
    ("有哪些论文可以分析", ["list_workflow_uploads"]),
    ("这个工作流配置有没有问题", ["validate_workflow"]),
    ("工作流里有哪些节点", ["list_workflow_nodes"]),
    ("给工作流加一个分析节点", ["add_workflow_node"]),
    ("把刚才那个节点删掉", ["delete_workflow_node", "list_workflow_nodes"]),
    ("改一下某个节点的提示词", ["update_workflow_node", "list_workflow_nodes"]),
    # "我想训练一个 LoRA"是明确请求；按"对象与动作明确就直接执行"的规则，
    # start_training 同样合理（原期望只给 list_*，与本项目自己的规则矛盾）
    ("我想训练一个 LoRA", ["list_available_datasets", "list_available_models", "start_training"]),
    ("训练进度怎么样了", ["get_training_status", "list_trainings"]),
    ("从上次的工作流结果造一份训练集", ["get_workflow_result", "list_workflow_results", "build_dataset"]),
    ("有哪些数据集能用", ["list_available_datasets"]),
    ("机器上有哪些底座模型", ["list_available_models"]),
    ("评测一下 dimensions_v1", ["run_eval"]),
    ("评测跑完了吗", ["get_eval_status"]),
    ("现在有哪些 LoRA", ["list_loras", "get_loras_registry"]),
    ("把 dimensions_v1 挂上去用", ["load_lora"]),
    ("卸载掉当前 LoRA", ["unload_lora"]),
    ("把这份 Excel 按部门汇总金额", ["group_sum", "list_uploads", "read_sheet"]),
    ("这张表有哪些工作表", ["list_sheets", "list_uploads"]),
    ("看看这张表的前十行", ["read_sheet", "list_uploads"]),
    ("筛出金额大于 100 的行", ["filter_rows", "list_uploads", "read_sheet"]),
    # 原题以"汇总"开头、以"导出"结尾，两种答案都能自辩 -> 收紧为明确"导出保存"，
    # 并把 group_sum 也列为可接受（这是出题问题，不是模型问题）
    ("把筛选后的结果导出保存成新的 Excel 文件", ["write_sheet", "group_sum"]),
    ("读一下这份 Word 的正文", ["read_docx"]),
    ("把结论整理成一份新 Word", ["write_docx"]),
    ("有哪些文档流程可以跑", ["list_doc_flows"]),
    ("跑一下按部门汇总那个流程", ["run_doc_flow", "list_doc_flows"]),
    ("把这套操作存成一个流程", ["save_doc_flow"]),
    ("知识库里有哪些资料", ["list_knowledge_bases"]),
    ("帮我查一下知识库里关于训练数据的内容", ["query_knowledge_base"]),
    ("新建一个知识库叫 test", ["create_knowledge_base"]),
    ("刚才那次分析质量怎么样", ["get_quality_summary"]),
    ("服务正常吗，模型在跑吗", ["get_vllm_status"]),
    ("换成另一个底座模型", ["switch_model"]),
    ("看看历史会话", ["list_history"]),
    ("打开那次对话的详情", ["get_history_detail", "list_history"]),
    ("现在有哪些任务在跑", ["list_running_tasks"]),
    ("把上传的文件列一下", ["list_uploads"]),
    ("当前配置是什么", ["get_settings"]),
    # ---- 复杂任务（复合要求）：只考"第一步该调什么" ----
    ("把最近一次论文分析结果的 extract_method 节点数据造成训练集，再训一个 LoRA",
     ["list_workflow_results", "get_workflow_result", "build_dataset", "list_available_datasets"]),
    ("这张 Excel 按部门汇总金额导出新表，再把结论整理成一份 Word",
     ["list_uploads", "read_sheet", "group_sum", "list_sheets"]),
    ("评测一下，如果分数不如上次就换个底座模型重训",
     ["get_eval_status", "run_eval", "list_loras", "list_available_models"]),
    ("帮我把知识库和 LoRA 都列出来，看看哪些能用",
     ["list_knowledge_bases", "list_loras", "get_loras_registry"]),
    ("工作流跑到一半失败了，帮我看看怎么回事",
     ["get_workflow_status", "list_running_tasks", "get_workflow_result", "list_workflow_results"]),
    ("训练完成之后自动挂载 LoRA 并跑一遍评测",
     ["get_training_status", "list_trainings"]),
    ("对比两个预设工作流哪个更省 token",
     ["list_workflow_configs", "list_workflow_results", "get_workflow_result"]),
    ("把这份 Word 里的表格抽出来存成 Excel",
     ["read_docx", "list_uploads", "write_sheet"]),
    ("先看看有哪些论文，挑一篇最长的做完整分析",
     ["list_workflow_uploads", "list_uploads"]),
    ("把刚才筛选出来的行按地区再汇总一次，然后存成新文件",
     ["filter_rows", "group_sum", "read_sheet", "list_uploads"]),
    # ===== 新增：文档编辑工作流 / 上传读正文 / 分析工作流 =====
    ('用文档编辑工作流把上传的表按部门汇总再导出', ['run_doc_flow', 'list_doc_flows']),
    ('帮我跑一下那个文档流程', ['run_doc_flow', 'list_doc_flows']),
    ('有哪些文档编辑流程可以用', ['list_doc_flows']),
    ('这个文档流程每一步都做什么', ['list_doc_flows']),
    ('把上传的 txt 内容读出来看看', ['read_upload', 'list_uploads']),
    ('新做一个文档流程，先读表再导出', ['save_doc_flow', 'list_doc_flows']),
    ('用分析工作流分析一下最新上传的论文', ['run_workflow', 'list_workflow_configs']),
    ('把文档流程的结果导出成 Excel', ['run_doc_flow', 'list_doc_flows', 'write_sheet']),

]


DECISION_RULES = """【决策规则（务必遵守）】
0. 先看现状再动手：用户提到具体名字（配置/数据集/LoRA/文件/节点）而不确定它是否存在，
   或说"上次那个/刚才的/现在的"，或涉及训练/评测/挂载/切换/删除 —— 先调用只读查询
   （list_workflow_configs / list_workflow_uploads / list_workflow_results /
   list_available_datasets / list_loras / list_available_models / list_knowledge_bases /
   list_uploads / list_running_tasks）。
1. 复合任务只输出第一步：出现"然后/再/之后/完成后/并且"时，只输出第一步调用，且通常是只读查询。
2. 近义工具只看宾语：挂/启用 LoRA = load_lora；换底座 = switch_model；卸载 = unload_lora；
   查/检索知识库内容 = query_knowledge_base；列出知识库 = list_knowledge_bases；
   发起评测 = run_eval；查评测进度 = get_eval_status；看分析质量 = get_quality_summary；
   有哪些论文 = list_workflow_uploads；有哪些数据集 = list_available_datasets；
   看历史会话 = list_history；取工作流结果 = get_workflow_result；
   导出/存文件 = write_sheet / write_docx（只算不存是 group_sum / filter_rows）。
3. 绝不编造工具名或参数值；不确定就先 list。
"""

# ---- 逐工具多种说法（自动展开成用例；每题期望该工具，ALT 里是同样可辩护的备选）----
ALT = {
    "run_workflow": ["list_workflow_configs"],
    "run_doc_flow": ["list_doc_flows"],
    "start_training": ["list_available_datasets", "list_available_models"],
    "build_dataset": ["get_workflow_result", "list_workflow_results"],
    "switch_model": ["list_available_models"],
    "load_lora": ["list_loras"],
    "unload_lora": ["list_loras"],
    "run_eval": ["list_loras"],
    "query_knowledge_base": ["list_knowledge_bases"],
    "read_sheet": ["list_uploads"],
    "write_sheet": ["group_sum", "filter_rows"],
    "read_docx": ["list_uploads"],
    "write_docx": ["read_docx"],
    "group_sum": ["read_sheet", "list_uploads"],
    "filter_rows": ["read_sheet", "list_uploads"],
    "list_sheets": ["list_uploads"],
    "delete_workflow_node": ["list_workflow_nodes"],
    "update_workflow_node": ["list_workflow_nodes"],
    "add_workflow_node": ["list_workflow_nodes"],
    "validate_workflow": ["list_workflow_configs"],
    "get_workflow_result": ["list_workflow_results"],
    "get_workflow_status": ["list_running_tasks"],
    "get_training_status": ["list_trainings", "list_running_tasks"],
    "get_eval_status": ["list_running_tasks"],
    "get_history_detail": ["list_history"],
}

PHRASINGS = {
    "list_workflow_configs": ["有哪些工作流模板", "工作流预设都有什么", "把这个项目的工作流配置列一下"],
    "list_workflow_nodes": ["工作流里有哪些节点", "看看这个工作流包含哪些节点"],
    "list_workflow_results": ["之前跑过哪些结果", "结果文件都列出来", "最近的工作流结果有哪些"],
    "list_workflow_uploads": ["有哪些论文可以分析", "能分析的论文文件列一下", "系统里放了哪些论文"],
    "validate_workflow": ["这个工作流配置有没有问题", "检查一下工作流配置对不对", "帮我校验工作流"],
    "add_workflow_node": ["给工作流加一个分析节点", "新增一个抽取节点", "往工作流里插一个节点"],
    "update_workflow_node": ["改一下某个节点的提示词", "修改这个节点的参数", "把节点温度调低一点"],
    "delete_workflow_node": ["把刚才那个节点删掉", "移除工作流里的某个节点", "删掉多余的节点"],
    "run_workflow": ["跑一下论文分析工作流", "开始分析这篇论文", "用预设流程分析一下"],
    "get_workflow_status": ["上次那个任务跑完了吗", "工作流跑到哪了", "分析进度怎么样"],
    "get_workflow_result": ["把上次分析的报告拿给我看看", "上次那个工作流的结果在哪", "取一下分析结果文件"],
    "list_available_datasets": ["有哪些数据集能用", "训练数据都有哪些", "数据集列表"],
    "list_available_models": ["机器上有哪些底座模型", "可以用哪些基座模型", "模型目录里有什么"],
    "build_dataset": ["从上次的工作流结果造一份训练集", "用节点数据生成数据集", "把抽取结果做成训练数据"],
    "start_training": ["我想训练一个 LoRA", "开始训一个抽取模型", "跑一次 LoRA 训练"],
    "get_training_status": ["训练进度怎么样了", "LoRA 训完了吗", "训练任务现在什么状态"],
    "list_trainings": ["历史训练任务有哪些", "之前训过哪些 LoRA 任务"],
    "run_eval": ["评测一下 dimensions_v1", "给这个 LoRA 跑个评测", "测一下抽取效果"],
    "get_eval_status": ["评测跑完了吗", "评测结果出来没", "评测任务进度"],
    "list_loras": ["现在有哪些 LoRA", "LoRA 都有什么", "可用的适配器列一下"],
    "get_loras_registry": ["看一下 LoRA 注册表", "注册表里有哪些 LoRA，哪些不可用"],
    "load_lora": ["把 dimensions_v1 挂上去用", "启用 dimensions_v1 这个 LoRA", "加载 LoRA"],
    "unload_lora": ["卸载掉当前 LoRA", "把挂载的 LoRA 摘下来", "取消加载 LoRA"],
    "switch_model": ["换成另一个底座模型", "切到 14B 那个模型", "换基座模型"],
    "list_knowledge_bases": ["知识库里有哪些资料", "知识库都列一下", "有几个知识库"],
    "query_knowledge_base": ["帮我查一下知识库里关于训练数据的内容", "检索一下知识库里的资料", "在知识库里找找相关内容"],
    "create_knowledge_base": ["新建一个知识库叫 test", "我想建个知识库", "创建一个新的知识库"],
    "list_uploads": ["把上传的文件列一下", "上传目录里都有什么", "我传过哪些文件"],
    "list_history": ["看看历史会话", "以前的对话记录", "列出最近的会话"],
    "get_history_detail": ["打开那次对话的详情", "看看那条会话的具体内容", "取一下某个会话的详情"],
    "list_running_tasks": ["现在有哪些任务在跑", "有没有正在执行的任务", "当前任务队列"],
    "get_quality_summary": ["刚才那次分析质量怎么样", "报告可信吗，有没有编造", "看下质检结果"],
    "get_vllm_status": ["服务正常吗，模型在跑吗", "推理底座起来了吗", "vLLM 状态"],
    "get_settings": ["当前配置是什么", "看看设置项", "配置怎么设的"],
    "list_sheets": ["这张表有哪些工作表", "Excel 里有几个 sheet", "列出所有工作表"],
    "read_sheet": ["看看这张表的前十行", "读一下表头和前几行", "这张表长什么样"],
    "filter_rows": ["筛出金额大于 100 的行", "把满足条件的行过滤出来", "筛选出北京的数据"],
    "group_sum": ["把这份 Excel 按部门汇总金额", "按地区统计一下总和", "分组求和"],
    "write_sheet": ["把结果导出保存成新的 Excel", "生成一个新的表格文件", "把数据写成 xlsx"],
    "read_docx": ["读一下这份 Word 的正文", "看看这个 docx 里写了什么", "读取 Word 文档内容"],
    "write_docx": ["把结论整理成一份新 Word", "生成一份 docx 报告", "写成 Word 文件"],
    "list_doc_flows": ["有哪些文档流程可以跑", "文档流程都列一下", "可复跑的文档流程有哪些"],
    "save_doc_flow": ["把这套操作存成一个流程", "保存成文档流程", "把这几步存为流程"],
}

CASES += [(ph, [tool] + ALT.get(tool, [])) for tool, phs in PHRASINGS.items() for ph in phs]


PROMPT_ELLIPSIS = '【动词 + 知识库 的省略句式】\n只要句子里出现"知识库/库/资料库"并且动词是"查/搜/找/检索/翻/看看里边"，\n**不论后面跟的是什么名词**（策略/方法/内容/蒸馏/训练数据…），一律选 query_knowledge_base。\n只有"有哪些库/建库/开库"这种才是 list_knowledge_bases / create_knowledge_base。\n'

PROMPT_GLOSSARY = '【"流程"这个词要按宾语判断】\n- 宾语是"论文/文章/分析/文献" → 论文工作流：run_workflow / list_workflow_configs / list_workflow_nodes\n- 宾语是"文档/Excel/表格/Word/汇总导出" → 文档流程：run_doc_flow / list_doc_flows\n- 只说"有哪些流程/流程清单"且未提文档 → 默认论文工作流：list_workflow_configs\n【同义词对照（用户会用不同词说同一件事，按右边选工具）】\n- 适配器 / adapter / 适配层 = LoRA → list_loras / get_loras_registry / load_lora / unload_lora\n- 基座 / 底座 / 基础模型 / base model = 推理底座 → list_available_models / switch_model\n- 语料 / 训练数据 / 训练集 = 数据集 → list_available_datasets / build_dataset\n- 固化 / 落成 / 存成 / 保存为 ……流程 = save_doc_flow\n- 启用 / 应用 / 装载 LoRA = load_lora；撤下 / 停用 = unload_lora\n- 校验 / 检查 / 合规 / 有没有写错 = validate_workflow\n- 测一测 / 评估 / 跑个指标 = run_eval；进度 / 跑完没 = get_eval_status\n- 可靠性 / 有没有编造 / 引用对得上吗 = get_quality_summary\n- 开库 / 建库 / 新资料库 = create_knowledge_base；搜 / 找 / 检索内容 = query_knowledge_base\n- 标签页 / sheet / 工作表 = list_sheets；表头 / 内容 / 长什么样 = read_sheet\n- 存成新表 / 输出 xlsx = write_sheet；输出 word/docx = write_docx\n'

PROMPT_CLARIFY_EXTRA = '【冷门说法的判据（按实测错例整理，务必照此选择）】\n- 句中出现"流程"且指一个已存在的文档流程（如"跑一下按部门汇总那个流程"）→ run_doc_flow；\n  不要因为句中有"汇总"就选 group_sum。\n- "校验工作流 / 配置有没有问题 / 检查工作流" → validate_workflow（不是 get_quality_summary）。\n- 出现"节点"且要"改/调"（如把节点温度调低）→ update_workflow_node；只看节点才是 list_workflow_nodes。\n- "分析进度 / 工作流跑到哪" → get_workflow_status；"训练进度"才是 get_training_status。\n- "训过哪些 LoRA 任务 / 历史训练任务" → list_trainings；"有哪些 LoRA"才是 list_loras。\n- "测一下抽取效果 / 测测效果" → run_eval；get_quality_summary 是看分析报告质量。\n- "这张表长什么样 / 表里是什么" → read_sheet；"有哪些工作表(sheet)"才是 list_sheets。\n'

PROMPT_CLARIFY = '【易混工具说明（务必据此选择）】\n- 换**底座模型** → switch_model；**挂载/启用**某个 LoRA → load_lora（看到"LoRA"就用 load_lora）；\n  卸载 LoRA → unload_lora。\n- **查/检索**知识库里的内容 → query_knowledge_base；**列出**知识库 → list_knowledge_bases；\n  看**分析报告质量**（有没有编造）→ get_quality_summary（与知识库无关）。\n- **发起**评测 → run_eval；查评测**进度** → get_eval_status。\n- "**有哪些**论文" → list_workflow_uploads；"**有哪些**数据集" → list_available_datasets。\n- 出现"**导出/保存成文件/写成 xlsx|docx**" → write_sheet / write_docx（只算不存是 group_sum / filter_rows）。\n- 句子里有"**流程**"且指一个已存在的文档流程 → run_doc_flow；取**工作流结果** → get_workflow_result；\n  看**历史会话** → list_history。\n【复合任务】\n用户话里出现"然后/再/之后/完成后/并且"时，**只输出第一步**的调用，通常是一个只读查询\n（如 get_training_status / list_workflow_results / get_eval_status）。\n【但不要过度使用"先查"】\n用户给了明确的对象与动作（如"加一个节点""看看前十行""挂上 dimensions_v1""导出保存"）时，\n**直接执行**对应工具，不要先去 list。\n'


import os
_HOLDOUT = os.environ.get("HOLDOUT_FILE", "")
if _HOLDOUT and Path(_HOLDOUT).exists():
    _d = json.loads(Path(_HOLDOUT).read_text(encoding="utf-8"))
    CASES = [(q, exp) for q, exp in _d["cases"]]
    print(f"[留出集] {len(CASES)} 条（来自 {Path(_HOLDOUT).name}）")


def build_prompt(question, kb_block="", with_rules=False):
    lines = ["你是本项目的工具调用 agent。根据用户要求选择最合适的一个工具调用。",
             "只输出如下格式，不要多余文字：",
             '<tool_call>{"name": "工具名", "args": {...}}</tool_call>', ""]
    if with_rules:
        lines += [DECISION_RULES, ""]
    if kb_block:
        lines += ["【可参考的工具使用手册片段】", kb_block, ""]
    lines += [PROMPT_CLARIFY, PROMPT_CLARIFY_EXTRA, PROMPT_GLOSSARY, PROMPT_ELLIPSIS, ""]        # 与前端真实提示词保持一致的补充说明
    lines.append("【可用工具】（危险操作会二次确认，标 ⚠）")
    for name, t in sorted(TOOLS.items()):
        arg = ",".join(t.get("args", [])[:6])
        flag = " ⚠" if t.get("dangerous") else ""
        lines.append(f"- {name}({arg}){flag}")
    lines += ["", f"用户：{question}", "你的工具调用："]
    return "\n".join(lines)


def call_llm(prompt, max_tokens=180, temperature=0.0):
    body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": temperature}).encode()
    req = urllib.request.Request(VLLM, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def parse_tool(text):
    """解析工具名。注意要**剥掉括号**：小模型常写 list_uploads()，
    那是答对了，不能因为多了括号就判错（第一版就犯了这个错）。"""
    m = re.search(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", text or "")
    raw = m.group(1) if m else (text or "")
    name = None
    try:
        name = json.loads(raw).get("name")
    except Exception:
        m2 = re.search(r'"name"\s*:\s*"([A-Za-z_0-9]+)', raw)
        name = m2.group(1) if m2 else None
    if not name:
        # 退化：文本里出现的第一个已知工具名（容忍 name(args) 写法）
        for cand in sorted(TOOLS, key=len, reverse=True):
            if re.search(r"\b" + re.escape(cand) + r"\b", raw):
                name = cand
                break
    if isinstance(name, str):
        name = re.sub(r"\(.*$", "", name).strip()   # 去掉 ( 及其后
    return name


def kb_lookup(question, k=3):
    try:
        body = json.dumps({"query": question, "top_k": k}).encode()
        req = urllib.request.Request(KB_API, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        res = d.get("results") or d.get("chunks") or []
        return "\n".join(str(x.get("text") or x.get("content") or "")[:400] for x in res)
    except Exception as e:
        return f"（KB 检索失败：{e}）"


def run_arm(use_kb, with_rules=False):
    ok = 0
    misses = []
    t0 = time.time()
    for q, expected in CASES:
        kb = kb_lookup(q) if use_kb else ""
        got = parse_tool(call_llm(build_prompt(q, kb, with_rules)))
        if got in expected:
            ok += 1
        else:
            misses.append((q, expected, got))
    return ok, len(CASES), misses, time.time() - t0


if __name__ == "__main__":
    import sys as _sys
    if "--holdout" in _sys.argv or "--b-only" in _sys.argv:
        arms = [("B 工具清单 + 知识库 top-3", True, False)]
    else:
        arms = [("A 仅工具清单", False, False),
                ("B 工具清单 + 知识库 top-3", True, False),
                ("C 工具清单 + 知识库 + 决策规则", True, True)]
    res = {}
    print(f"测试集 {len(CASES)} 题 | 工具 {len(TOOLS)} 个 | 模型 = {MODEL_ID}")
    for label, use_kb, use_rules in arms:
        print(f"\n=== {label} ===")
        ok, n, miss, el = run_arm(use_kb, use_rules)
        res[label] = (ok, n, miss)
        print(f"  准确率 {ok}/{n} = {ok/n*100:.1f}%  用时 {el:.0f}s")
        for q, e, g in miss:
            print(f"    x {q[:26]:<28} 期望 {e} 实得 {g}")
    print("\n=== 三组对照 ===")
    for label, (ok, n, _) in res.items():
        print(f"  {label:<34} {ok}/{n} = {ok/n*100:.1f}%")
    out = ROOT / "agent_kb" / "_last_eval.json"
    out.write_text(json.dumps({
        "cases": len(CASES),
        "arms": {k: {"ok": v[0], "misses": v[2]} for k, v in res.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  明细: {out}")
