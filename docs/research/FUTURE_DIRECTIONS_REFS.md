# 未来方向借鉴资料（2026-10-21）

> 对应 `docs/ARCHITECTURE_REPORT_V2.md` 第五节的 V5.0 / V6.0
> 原则：**借机制，不引框架**（同 `docs/OPEN_SOURCE_REFERENCE.md` ✓）

---

## 一、V5.0 边缘全量微调（DeepSpeed ZeRO-3）

### 核心资料
| 资料 | 用途 |
|---|---|
| **DeepSpeed ZeRO-Offload** 官方 → https://www.deepspeed.ai/tutorials/zero-offload/ | 参数/梯度/优化器 offload 到 CPU ✓ |
| **ZeRO-Infinity** 论文（arXiv:2104.07857）| offload 到 NVMe ✓ 更大模型 ✓ |
| HF Transformers DeepSpeed 集成指南 | `deepspeed` 配置注入 ✓ |

### 关键技术点
```json
{
  "zero_optimization": {
    "stage": 3,
    "offload_param":    {"device": "cpu"},
    "offload_optimizer": {"device": "cpu"},
    "overlap_comm": true,
    "contiguous_gradients": true
  }
}
```
**显存估算（4B fp16 全参）**：
```
模型 8GB + 梯度 8GB + 优化器(Adam fp32) 32GB = 48GB ✗
⇒ 必须 offload ✗ 需要 30GB+ 内存 ✓（本机 32GB ⚠ 紧）
⇒ 训练速度: PCIe 4.0 x4 瓶颈 ⇒ 估计极慢 ⚠（可能 10x 慢 ✗）
```

### ⚠ 诚实建议
```
本机 32GB 内存 + PCIe x4 ⇒ 4B 全参微调**理论可行但极慢** ⚠
更实际的选择:
  · 7B AWQ + LoRA rank 128 ✓（显存省 ✓ 效果接近全参 ⚠ 待验证）
  · 或改用 **gradient checkpointing + 更小 batch** ⚠
⇒ 建议 V5.0 **延后** ✓ 先把 V4.0 飞轮闭环做完 ✓
```

---

## 二、V6.0 自造子代理（云端写代码注册工具）

### ★ 核心资料（几乎就是 V6.0 的方案 ✓）

**MetaAgent: Toward Self-Evolving Agent via Tool Meta-Learning**
🔗 https://arxiv.org/abs/2508.00271

```
做法: agent 通过"工具元学习"自我演化
  · 遇到缺工具 ⇒ 自动创建/组合工具 ✓
  · 创建后自测 ⇒ 验证有效才注册 ✓
  · 注册进工具库 ⇒ 后续任务可复用 ✓
⇒ **与 V6.0 目标完全一致** ✓ 可直接借鉴流程 ✓
```

**CAlita: Adaptive LLM-based Iterative Task Automation**
🔗 https://pypi.org/project/calita/
```
做法: LLM 迭代式任务自动化 + 工具扩展 ✓
可借鉴: 迭代循环 + 工具边界判断 ✓
```

### 本平台已有地基（V6.0 其实不缺基础 ✓）
| 资产 | 位置 | 状态 |
|---|---|---|
| 工具注册 API | `POST /docs/tool` · `_tool_docs.json` | ✅ 已有 |
| 工具文档系统 | `agent_kb/tool_docs.md`（765 行）| ✅ 已有 |
| 前端工具注册表 | `AGENT_TOOL_ENDPOINTS`（54 个）| ✅ 已有 |
| 云端 escalation | `escalateAgentToCloud()` | ✅ 已有 |
| 代码执行 | 无沙箱 ❌ | ❌ 缺 |

### V6.0 缺的三块（按难度）
```
① 代码沙箱 ✗（安全第一 ⚠ 可用子进程 + 资源限制 ✓ 或容器 ✓）
② 自动测试 ✗（生成工具后跑自测 ✓ 可复用 V2 评测框架 ✓）
③ 注册后验证 ✗（A/B 验证新工具是否真有用 ✓ 可复用 eval_v2 ✓）
```

### ⚠ 诚实建议
```
V6.0 是**最有想象力但最难**的方向 ⚠
· 地基已有 ✓（工具注册 ✓ 文档 ✓ escalation ✓）
· 真正难的是: 代码沙箱安全 ✗ + 生成代码质量 ✗ + 验证成本 ⚠
⇒ 建议: 先做"半自动"版本 ✓
   云端生成工具代码 → **人工审核** → 注册 ✓
   （不做全自动 ✗ 避免安全风险 ⚠）
```

---

## 三、其他可参考（本项目相关但未排期）

| 方向 | 资料 | 用途 |
|---|---|---|
| **Bootstrap CI** | Efron & Tibshirani (1993) · scipy `bootstrap` | 补 V3.0 缺的置信区间 ✓ |
| **评测集轮换** | `docs/EVAL_V2_DESIGN.md` 已有思路 ✓ | 防过拟合评测集 ✓ |
| **全参 vs LoRA** | "LoRA Learns Less and Forgets Less" (arXiv:2405.09673) | 解释为什么 LoRA 收益有限 ✓ |
| **检索 vs 参数化** | SCAFFOLD (arXiv:2609.05511) ✗ 选 parametric | 印证我们的"检索有害"发现 ✓ |
| **技能库** | Voyager (arXiv:2305.16291) · AWM (arXiv:2409.07429) | 技能抽象与检索 ✓ |

---

## 四、优先级建议（诚实 ✓）

```
1. **先把 V4.0 飞轮闭环做完** ✓（期 2 训练固化 + 期 3 自动化）
   · 不需要新文献 ✓ flywheel.py 已有 ✓ 缺的是闭环验证 ⚠
2. **补 Bootstrap CI** ✓（0.5h · 补 V3.0 的缺口 ✓ 简单 ✓）
3. **V6.0 半自动版** ✓（云端生成 + 人工审核 + 注册 ✓ 地基已有 ✓）
4. **V5.0 全参微调** ⏳ 最后（本机条件勉强 ⚠ 建议 7B+LoRA 替代 ✓）
```

---

*2026-10-21 · 借鉴资料 · 结论: V6.0 有直接对口的 MetaAgent ✓ V5.0 本机条件勉强建议延后 ⚠*
