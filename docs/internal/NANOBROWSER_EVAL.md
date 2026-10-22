# Nanobrowser 具体评估 + 最终形态预测

> 日期: 2026-10-19 · 性质: **专项评估 + 形态预测**（不实现 ✗）
> 事实来源: GitHub API 实测（仓库/文件树/package.json/releases ✓）· README 原文 ✓ · nanobrowser.ai ✓
> 预测部分明确标注【预测 ⚠】—— 与事实分开 ✓

---

## 一、项目画像（硬事实 ✓ 全部来自 GitHub API）

| 项 | 值 |
|---|---|
| 仓库 | **nanobrowser/nanobrowser** ✓ |
| 定位（原文） | "Open-source AI agent for web automation. **Use your real, logged-in browser** to automate daily tasks." |
| 许可证 | **Apache-2.0** ✓（可商用 ✓ 可改 ✓ 可闭源分发 ✓） |
| 星 / Fork / Open issues | **14,110** / 1,625 / 70 ✓ |
| 语言 / 框架 | **TypeScript** · **WXT 0.21**（浏览器扩展框架）· React 18 · Vite 8 · Tailwind 3 |
| LLM 接入 | **Vercel AI SDK**（`ai@7` + `@ai-sdk/anthropic|openai|google` ✓ 多 provider ✓） |
| 浏览器驱动 | **puppeteer-core ^25.13** ✓（走 CDP ✓ 不是 Playwright ✗） |
| 存储 | `idb`（IndexedDB ✓）+ `zod` 校验 ✓ |
| 规模 | **490 文件**（src 367 ✓）· 25 贡献者 · 16 个 release ✓ |
| 创建 / 最近 push | 2024-12-31 / **2026-10-15** ✓ 未归档 ✓ |
| 主页 | nanobrowser.ai ✓ |
| Topics | agent · browser-automation · browser-use · **cdp** · chrome-extension · **playwright** · **puppeteer** · **n8n** · web-scraping |

## 二、架构拆解（基于真实文件树 ✓ 推断部分已标注）

```
src/ (367 文件)
├── background/        92 文件  ← MV3 Service Worker（消息路由/持久化/生命周期 ✓ ★ MV3 的痛点都在这）
├── browser-control/   86 文件  ← ★★ 浏览器控制层（CDP 封装 / 元素定位 / 动作执行）
├── agent/             35 文件  ← ★★ agent 循环，且带 20 个测试 ✓✓
│    ├── loop.ts · context.ts · compaction.ts · elision.ts · overflow-recovery.ts
│    ├── injection-context.ts（★ 提示注入防护！）· records.ts · restore.ts · request.ts
│    └── __tests__/（loop/context/compaction/elision/overflow-recovery/injection-context/… 20+ ✓）
├── side-panel/        28 文件  ← 侧边栏 UI（React ✓ = 用户要的"浮窗+对话框"✓）
├── llm/               26 文件  ← 多 provider 适配（AI SDK ✓）
├── extension/         29 文件 · ui/ 22 · options/ 20 · store/ 17 · test-panel/ 12
entrypoints/  8 文件  ← background.ts / sidepanel/index.html / permission/index.html ✓
fixtures/    78 文件  ← 测试夹具（说明工程化程度高 ✓）
```

### 三个"最值钱"的模块（对我们 ✓✓）
| 模块 | 为什么值钱 | 对应我们的缺口 |
|---|---|---|
| `browser-control/`（86 文件 ✓） | 成熟的元素定位/动作执行/CDP 封装 ✓ | 我们 **0 个浏览器工具** ✗ |
| `agent/compaction.ts` + `elision.ts` + `overflow-recovery.ts` | **上下文压缩与溢出恢复** ✓（长任务不崩 ✓） | 我们只有 `MAX_ITER` 硬上限 ✗ 没有摘要 ✓ |
| `agent/injection-context.ts` | **提示注入防护**（把页面文本标为"信息不是指令"✓） | 我们完全没有 ✗（这是驱动真实浏览器最大的风险 ✗） |

## 三、功能清单（README 原文 ✓ 与你的需求对照）

| 能力 | Nanobrowser | 你的需求 |
|---|---|---|
| 侧边栏对话 | ✓ "Chat in the side panel" | **⑤⑥ 完全对应** ✓✓ |
| 每对话独立选模型 | ✓ "Pick the model… for each chat" | **② 完全对应** ✓✓ |
| 点击/输入/切标签 | ✓ "reads pages, clicks, types and moves between tabs" | ③ ✓ |
| 截图/上传/下载 | ✓ screenshot / upload files / save CSV·MD to Downloads | **③ 完全对应** ✓✓ |
| Edge 支持 | ✓ "Supported: Chrome and Edge" | **④ 对应** ✓（但它是"在当前页动"✗ 我们要"输网址新建"✗ 需我们补 ✓） |
| 权限模型 | ✓ **Ask/Auto + 每站 Allow/Ask/Block** + 提交/删除/上传前询问 ✓ | 安全三件套 ✓✓ **直接抄设计** ✓ |
| 防提示注入 | ✓ "reads text on a web page as information, never as orders" | ★ 我们最需要 ✓ |
| 多任务并行 | ✓ 每任务独立 tab group ✓ | 第二阶段 ✓ |
| 长任务记忆/压缩 | ✓ 旧步骤自动摘要 ✓ · Memory ✓ | ★ 我们要补 ✓ |
| 语音/快捷指令/历史 | ✓ | 低优先 ✓ |
| **对外 API** | ✗ **没有**（扩展内部消息 ✓ 不对外 ✗） | ⚠ 决定"能不能直接集成"✗ |

## 四、技术栈评估

```
✓ 优点: 现代栈（TS+React+WXT+Vite ✓）· AI SDK 多 provider ✓ · 有 20+ 单测 ✓ · Apache-2.0 ✓
⚠ 风险:
  · 用 puppeteer-core（非 Playwright ✗）⇒ 与我们已验证的 Playwright 经验错配 ⚠
  · MV3 Service Worker 生命周期被浏览器回收 ✗（它有 92 文件 background 在处理 ✓ 说明这确实是难点 ✓）
  · 不支持 Firefox/Safari ✗（README 原文 ✓）· Chromium 系非目标浏览器未测试 ✗
  · release 节奏不均 ⚠（v0.1.13 2025-11-22 → v0.2.0 2026-10-02 隔 10 个月 ✓ 然后 v0.3.0 两周后 ✓）
    ⇒ 说明是"个人/小团队主导项目" ✓（25 贡献者 ✓ 70 open issues ✓）⇒ 长期维护有不确定性 ⚠
```

## 五、成熟度评级

| 维度 | 评级 | 依据 |
|---|---|---|
| 功能完备度 | ★★★★☆ | 你的需求 ②③⑤⑥ 全有 ✓ + 权限/压缩/记忆 ✓ |
| 代码工程化 | ★★★★☆ | 367 文件 · 20 agent 测试 · fixtures 78 · lint/type-check ✓ |
| 社区与活跃 | ★★★★☆ | 14.1k★ · 1.6k fork · 10-15 仍在更新 ✓ |
| 长期维护风险 | ★★★☆☆ | 小团队 + release 节奏不均 ⚠ |
| 与我们架构兼容 | ★★☆☆☆ | 技术栈完全不同 ✗ 无对外 API ✗ |
| **可借鉴价值** | **★★★★★** | **设计模式 + 测试用例思路 = 直接能用 ✓✓** |

## 六、兼容性与移植结论（具体到动作 ✓）

```
✗ 全量移植: 不可行（TS/React/MV3/WXT → 我们单文件 HTML + Python ✗ 5~7 天+ ✗）
✗ 直接集成: 不可行（无对外 API ✗ 除非改它源码加消息桥 ✗）
✓✓ 借鉴 + 自研薄层: 推荐
   移植【设计】清单（0.5~2 天可完成 ✓）:
     1. 权限模型: Ask/Auto + 每站 Allow/Ask/Block + 敏感动作（提交/删除/上传/支付）前置确认 ✓（0.5 天）
     2. 注入防护: 页面文本一律"外部内容"标记 + 动作白名单 + 域白名单 ✓（0.5 天）
     3. 上下文管理: 步骤摘要（compaction）+ 元素清单精简（elision）+ 溢出恢复 ✓（1 天）
     4. 元素定位策略: 优先可访问性属性（role/name）而非 DOM 路径 ✓（借鉴其 browser-control 思路 ✓）
   移植【代码】清单: 无（✗ 语言不通 ✓ 且我们已有 Playwright ✓）
   替代驱动: 我们用自己的 Playwright（Node sidecar ✓）+ `--cdp-endpoint=msedge` 连 Edge ✓
```

## 七、最终形态预测

### 7.1 Nanobrowser 自身会变成什么【预测 ⚠】

```
依据（已发生的事实 ✓）: v0.3.0 新增/强化了权限模型、多任务 tab group、memory、上下文压缩 ✓
                         topics 里已出现 cdp/playwright/puppeteer/n8n（说明在往"被外部编排"方向走 ✓）

【预测】三种分叉:
 ① 乐观（40%）: 成为"浏览器里的 agent 运行时" ✓
    · 提供 MCP/API 被外部工具调用（n8n/Zapier/我们的平台 ✓）
    · 模型本地化（Ollama/llama.cpp ✓）· 企业策略管理 ✓
    · 定位: 像"浏览器里的 n8n + agent" ✓
 ② 中性（45%）: 保持"个人用的浏览器 AI 助手" ✓
    · 功能更全但不开 API ✗ · 靠 14k 星的口碑增长 ✓
    · 被 OpenAI Operator / Claude for Chrome 等官方产品挤压 ⚠
 ③ 悲观（15%）: 维护停滞或被收购/改名 ✓
    · 触发信号: 3 个月无 push ✗ 或 open issues > 150 ✗
⇒ 对我们的含义: 无论哪种分叉，【设计可借鉴 ✓ 但不该把它当依赖 ✗】（无 API + 小团队 ⚠）
```

### 7.2 我们平台的最终形态【预测 ⚠ —— 这是重点】

```
【近期 · 1~2 周】浏览器执行器上线
  形态: 平台里多一个「浏览自动化」页 ✓（模型独立选择 ✓ 网址输入 ✓ 对话 ✓ 技能面板 ✓）
  驱动: Node sidecar + Playwright 连 Edge（--cdp-endpoint=msedge ✓）
  能力: 10 个浏览器工具（open/click/fill/read/screenshot/download/upload/move/wait/close ✓）
  安全: 借鉴 Nanobrowser 的 Ask/Auto + 站点规则 ✓
  验收: 抓 3 个真实页面 → 清洗 → 入 KB ✓（这是"网页→知识库"闭环 ✓）

【中期 · 1~2 月】浮窗化 + 任务化
  形态: Edge 侧边栏/扩展浮层 ✓（贴页面 ✓ 随时唤起 ✓）
  能力: 任务队列（多任务并行 ✓ 每任务一个标签组 ✓ 借鉴 ✓）· 上下文压缩 ✓
  联动: 浏览器结果自动进 KB ✓ 任务可被判分器判 ✓ 可被工作流引擎调度 ✓

【终局 · 6~12 月】多执行器 Agent OS（我们这条路线的自然终点）
  ┌──────────── 统一的一层 ────────────┐
  │  递归守卫（计算/动作必须先走执行器 ✓）│
  │  判分器（AST+执行+Relevance ✓）     │
  │  知识库（成功经验沉淀 ✓）            │
  └───────────────┬──────────────────┘
     执行器（每个都是"确定性工具 + 可插拔"✓）:
     · 本机执行器: 35 工具（模型/LoRA/KB/工作流/训练/文档 ✓）
     · 计算执行器: 仿真节点（math/stats/ratio + 插件 ✓）
     · 浏览器执行器: 新（10 工具 + 浮窗 ✓）
     · 云执行器: 云模型兜底（含 PLAN 执行 ✓ 待做 P-15 ✓）
  ⇒ 产品形态预测: 【平台 + 扩展混合】✓（平台管资产/评估/知识沉淀 ✓ 扩展管"贴页面的手"✓）
     而不是纯 Web 平台 ✗ 也不是纯桌面 OS ✗ —— 因为浏览器能力天然长在浏览器里 ✓
```

### 7.3 预测校准点（可观测信号 ✓ 三个月后回看）

| 信号 | 若达标 ⇒ 预测成立 | 若不达标 ⇒ 该调整 |
|---|---|---|
| 浏览器任务成功率（判分器口径） | ≥60%（3 个月内 ✓） | <30% ⇒ 说明"视觉/DOM 方案"还不成熟 ⇒ 退回归档 ✓ |
| 每任务平均动作数 | ≤8 步（太多 ⇒ 成本失控 ✗） | >15 步 ⇒ 要强引导或换模型 ✓ |
| 安全事件（误操作/注入成功） | **0** | 出现 1 次 ⇒ 立刻回滚为只读 ✓✓ |
| 用户使用频次 | 每周 ≥3 次 ✓ | 不用 ⇒ 说明不是刚需 ⇒ 降级 ✓ |
| 与现有 4 个执行器的联动 | "网页→KB" 闭环跑通 ✓ | 跑不通 ⇒ 只是玩具 ✓ |

## 八、建议（明确 ✓）

```
✅ 采纳 Nanobrowser 的【设计】= 权限模型 + 注入防护 + 上下文压缩 + 元素定位策略 ✓（2 天 ✓ 最高性价比）
✗ 不移植它的代码 ✗ 不依赖它作组件 ✗（无 API + 小团队 + 技术栈错配 ✓）
✅ 驱动用我们已验证的 Playwright（Node sidecar ✓ 连 Edge ✓）
✅ 产品形态走【平台 + 扩展混合】✓（先 Web 页 + Edge 侧边栏 ✓ 后续再做浮层扩展 ✓）
```

## 九、参考来源

- 仓库（Apache-2.0 · v0.3.0）: https://github.com/nanobrowser/nanobrowser
- 主页: https://nanobrowser.ai
- 商店页: https://chromewebstore.google.com/detail/nanobrowser-ai-web-agent/imbddededgmcgfhfpcjmijokokekbkal
- Playwright 连已运行浏览器（msedge 通道 ✓）: https://playwright.dev/mcp/configuration/browser-extension
