# -*- coding: utf-8 -*-
"""agent_trace.py —— 记录 agent（DSH/平台）工作轨迹，供本地模型学习

轨迹格式与 data/trajectories/*.jsonl 兼容（trajectory_distill.iter_trajectories 可直接读）：
{"task":..., "reason":"completed", "final":..., "tools_used":[...], "tool_errors":[...],
 "steps":[{"turn":1,"text":"思考","tool_calls":[{"tool":"...","input":{...},
           "result":"...","status":"completed"}],"usage":{}}], ...}
用途：① 蒸馏共享工作流 ② 生成 LoRA 训练数据（sharegpt）③ 沉淀"如何搭建工作流"的示范
"""
import json
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TRAJ_DIR = PROJECT_DIR / "data" / "trajectories"


class AgentTrace:
    def __init__(self, task, source="agent_trace", model="deepseek-flash"):
        self.task = str(task or "")
        self.source = source
        self.model = model
        self.steps = []
        self.tools_used = []
        self.tool_errors = []
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._t0 = time.time()

    def step(self, thought, tool, tool_input=None, result="", ok=True, status=None):
        """记录一步：思考 + 工具调用 + 结果"""
        st = status or ("completed" if ok else "failed")
        rec = {"tool": str(tool), "input": tool_input or {},
               "result": str(result)[:4000], "status": st}
        self.tools_used.append(str(tool))
        if not ok:
            self.tool_errors.append(str(tool))
        self.steps.append({"turn": len(self.steps) + 1,
                           "text": str(thought or "")[:2000],
                           "tool_calls": [rec], "usage": {}})
        return rec

    def finish(self, final, reason="completed"):
        self.final = str(final)[:2000]
        self.reason = reason
        return self.record()

    def record(self):
        return {
            "task": self.task,
            "reason": getattr(self, "reason", "completed"),
            "final": getattr(self, "final", ""),
            "steps": self.steps,
            "tools_used": self.tools_used,
            "tool_errors": self.tool_errors,
            "source": self.source,
            "agent": {"model": self.model, "duration": round(time.time() - self._t0, 1)},
            "created_at": self.created_at,
        }

    def save(self, tag="agent_workflow"):
        TRAJ_DIR.mkdir(parents=True, exist_ok=True)
        path = TRAJ_DIR / ("archived_%s_%s.jsonl" % (tag, time.strftime("%Y%m%d_%H%M%S")))
        path.write_text(json.dumps(self.record(), ensure_ascii=False) + "\n", encoding="utf-8")
        return path
