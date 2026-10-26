# -*- coding: utf-8 -*-
"""用 DeepSeek 生成"任务→工作流 JSON"作者样本（LoRA 训练数据）"""
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '/home/luxy/paper_lora_project')
import cloud_workflow_builder as cwb

OUT_DIR = pathlib.Path('/home/luxy/paper_lora_project/training_data/workflow_authoring')
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOOLS = ("write_sheet: 写 Excel（rows 二维数组/header/name/sheet）；"
         "read_sheet: 读 Excel（filename/sheet/rows）；"
         "paper_lora_kb_query: 查知识库（kb_name/query/top_k，仅在已有库时用）；"
         "paper_lora_list_loras / paper_lora_train_list / paper_lora_list_datasets: 平台查询")

TASKS = [
    "做一个自动识别用户意图生成采购清单 Excel 的本地工作流：来源=本地 Excel+京东/淘宝/阿里巴巴；目标=云端生成的采购清单样式",
    "做一个把每周工作内容汇总成周报 Excel 的本地工作流：输入=零散记录，输出=周报表格（任务/完成度/风险）",
    "做一个自动解析招标文件并生成 PDF 要点清单与 Word 摘要的工作流",
    "做一个供应商比价工作流：读取报价 Excel，按单价/交期/质保排序并输出比价表 Excel",
    "做一个库存盘点工作流：读取盘点 Excel，计算差异金额并输出差异表 Excel",
    "做一个培训签到工作流：根据名单生成签到表 Excel（姓名/部门/签到时间/备注）",
    "做一个数据清洗工作流：读取 CSV/Excel，去重/补空值/格式化日期，输出清洗后 Excel",
    "做一个会议纪要工作流：根据录音转写文本生成纪要 Word（结论/待办/责任人）",
    "做一个论文信息抽取工作流：从 PDF 抽取标题/作者/方法/结论并输出表格 Excel",
    "做一个合同条款核对工作流：读取合同文本，对照检查清单生成核对表 Excel",
    "做一个项目进度跟踪工作流：读取任务列表，生成带进度/风险/负责人的跟踪表 Excel",
    "做一个发票信息汇总工作流：从多张发票文本抽取金额/税号/日期，汇总成 Excel",
]

PROMPT = """你是云端工作流架构师。只输出一个 JSON 对象（不要解释、不要 markdown）。
允许节点类型：input / process / aggregate / tool。
允许 tool 节点：{tools}
约束：
1) 第一个节点 input（id=n0），最后 aggregate；
2) tool 节点 write_sheet 的 tool_args 必须含 rows（二维数组示例数据）、header、name、sheet；
3) process 节点 prompt 用 {{n0}} 引用上游；每个节点必须有 id/type/input_from/position；
4) 节点字段尽量精简（不要 config/name 等额外字段）；
5) 输出格式严格如下：
{{"workflow_name":"...","description":"...","nodes":[{{"id":"n0","type":"input","input_from":[],"position":{{"x":0,"y":0}}}},{{"id":"n1","type":"process","input_from":["n0"],"prompt":"...","provider":"vllm","output_format":"text","max_tokens":800,"temperature":0.2,"position":{{"x":220,"y":0}}}},{{"id":"n2","type":"tool","input_from":["n1"],"tool":"write_sheet","tool_args":{{"rows":[["1","示例"]],"header":["序号","名称"],"name":"输出","sheet":"Sheet1"}},"tool_timeout":60,"position":{{"x":440,"y":0}}}},{{"id":"n3","type":"aggregate","input_from":["n2"],"prompt":"汇总：{{n2}}","provider":"vllm","output_format":"text","max_tokens":1000,"temperature":0.2,"position":{{"x":660,"y":0}}}}],"golden_purchase_list":{{"columns":["序号","名称"],"rows":[["1","示例"]],"notes":"示例目标结果"}}}}
任务：{task}"""


def main():
    samples = []
    for i, task in enumerate(TASKS, 1):
        prompt = PROMPT.format(tools=TOOLS, task=task)
        try:
            content, info = cwb._cloud_ask(prompt, max_tokens=4096, timeout=240)
            data = cwb._extract_json(content)
            if not data:
                # 重试一次（更强约束）
                content2, info2 = cwb._cloud_ask(
                    prompt + "\n\n注意：上一次输出不是合法 JSON。请直接输出 JSON 对象（以 { 开头，以 } 结尾），不要解释。",
                    max_tokens=4096, timeout=240)
                data = cwb._extract_json(content2)
                info = info2
            if not data:
                print(' [%02d] ✗ JSON 解析失败（len=%d）' % (i, len(content)))
                continue
            # 只保留工作流设计 + 目标清单，作为 assistant 答案
            answer = json.dumps({
                "workflow_name": data.get("workflow_name"),
                "description": data.get("description"),
                "nodes": data.get("nodes"),
                "golden_purchase_list": data.get("golden_purchase_list"),
            }, ensure_ascii=False)
            samples.append({
                "conversations": [
                    {"role": "user",
                     "content": "任务：%s\n请设计一个可在本地执行的工作流（input/process/tool/aggregate），"
                                "并在 write_sheet 的 tool_args 里给出目标数据 rows。" % task},
                    {"role": "assistant", "content": answer},
                ],
                "metadata": {"task": task, "kind": "workflow_authoring",
                             "cloud": info, "target_model": cwb.TARGET_MODEL_REQUESTED},
            })
            nodes = data.get("nodes") or []
            tools = [n.get("tool") for n in nodes if n.get("type") == "tool"]
            print(' [%02d] ✓ %s | nodes=%d tools=%s | %.1fs'
                  % (i, data.get("workflow_name", "")[:30], len(nodes), tools,
                     info.get("duration") or 0))
        except Exception as e:
            print(' [%02d] ✗ %s: %s' % (i, type(e).__name__, str(e)[:150]))
        time.sleep(1)

    path = OUT_DIR / "workflow_author_v1_all.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n",
                    encoding='utf-8')
    # 划分 train/eval（最后 2 条做 eval）
    n_eval = 2 if len(samples) > 4 else 1
    train, evals = samples[:-n_eval], samples[-n_eval:]
    (OUT_DIR / "workflow_author_v1_train.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in train) + "\n", encoding='utf-8')
    (OUT_DIR / "workflow_author_v1_eval.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in evals) + "\n", encoding='utf-8')
    print('\n数据集: %d 样本 (%d train / %d eval) → %s'
          % (len(samples), len(train), len(evals), path))


if __name__ == "__main__":
    main()
