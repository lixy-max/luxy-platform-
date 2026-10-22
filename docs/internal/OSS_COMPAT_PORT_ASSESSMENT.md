# 兼容性 + 移植评估：浏览器自动化开源项目（仅分析）

> 日期: 2026-10-19 · 性质: **评估报告**（不实现 ✗）
> 方法: GitHub API 取【硬事实】（许可证/星数/语言/版本/更新时间 ✓）+ README 原文功能 ✓
> ⚠ "这个开源项目"你没点名 ⇒ 我按最贴合你需求的顺序评 3 个主要候选 ✓ 你指哪个我再深挖 ✓

---

## 一、候选硬事实（GitHub API 实测 ✓ 非猜测）

| 项目 | 许可证 | ★ | 语言 | 最新版 | 最近更新 | 形态 |
|---|---|---|---|---|---|---|
| **nanobrowser/nanobrowser** | **Apache-2.0** ✓ | 14,110 | TypeScript | **v0.3.0** | 2026-10-15 ✓ | **Chrome/Edge 扩展**（侧边栏 agent ✓） |
| **microsoft/playwright-mcp** | **Apache-2.0** ✓ | 38,314 | TypeScript | **v0.0.83** | 2026-10-16 ✓ | **npm 包**（39 个文件 ✓ 轻量 ✓ MCP server ✓） |
| **bytedance/UI-TARS-desktop** | **Apache-2.0** ✓ | 39,312 | TypeScript | v0.3.1 | 2026-10-09 | **Electron 桌面 app**（视觉驱动 ✓） |
| （参考）browser-use/browser-use | MIT ✓ | 118,374 | Python | 活跃 | 2026-10-17 | 库（Python）✓ |

```
★ 许可证结论: 三者全部 Apache-2.0 ✓（browser-use MIT ✓）
  ⇒ 可商用 ✓ 可修改 ✓ 可闭源分发 ✓（只需保留 LICENSE/NOTICE 与变更声明 ✓）
  ⇒ 【移植/集成 无法律障碍 ✓✓】—— 这是最关键的前置条件 ✓
```

## 二、需求 ↔ Nanobrowser 能力覆盖矩阵（README 原文 ✓）

| 你的需求 | Nanobrowser 是否已有 | 证据（README 原文） |
|---|---|---|
| ① 工具栏新增「浏览自动化」页 | ✗（它是独立扩展 ✗ 没有"我们的工具栏"） | 它自己就是浏览器扩展 ✓ |
| ② 可单独选模型（≠全局） | **✓✓ 完全一致** | "Your choice of model: cloud or local. Pick the model, and how hard it thinks, **for each chat**" |
| ③ 浏览器 skill: 截图/下载/指针 | **✓✓ 大部分有** | 截图 ✓ "attach a screenshot of the current tab"·下载 ✓ "save its work, like a CSV table or a Markdown report, to your Downloads folder"·点击/输入/切标签 ✓ "reads the pages, clicks, types and moves between tabs" |
| ④ 搜索框输网址 → **Edge** 打开 | **✓ 支持 Edge** | "Supported: **Chrome and Edge**"（但它是"在你当前标签页动"✗ 不是"输入网址新建 ✓ 需我们补 ✓） |
| ⑤ 浏览器里出现**浮窗** | **✓✓ 就是它的形态** | "Chat in the **side panel**"（侧边栏 ✓ 还有 tab group ✓） |
| ⑥ 勾对话框自然语言操作 | **✓✓ 核心功能** | "say what you need in plain words" |
| （加分）安全 | **✓✓ 比我们想的还全** | Ask/Auto 模式 ✓ 每站 Allow/Ask/Block ✓ "asks you before it submits, sends, deletes or uploads" ✓ "reads text on a web page as information, never as orders"（防提示注入 ✓） |

⇒ **Nanobrowser 已经实现了你需求的 ②③⑤⑥ 和大部分安全设计 ✓✓**，缺的只是 ①（我们的平台入口）和 ④ 的"网址搜索新建" ✓

## 三、Nanobrowser 兼容性评估（与我们现有平台）

| 维度 | 结论 | 说明 |
|---|---|---|
| **技术栈** | ✗ **不兼容** | 它是 **Chrome MV3 扩展（TypeScript/React）** ✗；我们是 **单文件 HTML 原生 JS + Python 后端** ✗ ⇒ 代码不能直接嵌 ✓ |
| **运行环境** | ✓ 可共存 | 扩展跑在用户 Edge 里 ✓ 我们的页面跑 localhost:8080 ✓ 两者可同时存在 ✓ |
| **通信方式** | ⚠ 受限 | 扩展默认**不暴露 API** ✗ ⇒ 我们的后端无法直接驱动它 ✗（除非改它的源码加消息接口 ✗） |
| **数据/凭据** | ✓ 各自独立 | 它的 key 存浏览器本地 ✓ 我们的 key 存 settings ✓ 不冲突 ✓ |
| **许可证** | ✓ Apache-2.0 | 可改可集成 ✓ |
| **维护活跃度** | ✓ 活跃 | 10-15 更新 ✓ v0.3.0 ✓ 143 天前成立的项目 ✓ |
| **用户安装成本** | ⚠ 中 | 用户要装扩展 ✓（或开发者模式侧载 ✓） |

### 兼容性总评
```
✗ 作为"代码库"与我们不兼容（语言/形态/UI 全不同 ✗）
✓ 作为"产品"可共存（同一台机器、同一个 Edge ✓）
⚠ 作为"组件"难直接驱动（无对外 API ✗）
⇒ 结论: 不适合直接集成；适合【借鉴设计 + 我们自研薄层】✓✓
```

## 四、Nanobrowser 移植评估（模块级）

| 模块 | 内容 | 可移植性 | 移植成本 | 建议 |
|---|---|---|---|---|
| **侧边栏 UI**（对话/任务/历史） | React 组件 ✗ | ✗ 低（React→原生 JS 重写 ✗） | 5~7 天 ✗ | **不移植 ✗ 我们已有前端 ✓ 加个 tab 更快 ✓** |
| **agent 循环**（多步规划/执行/观测） | TypeScript ✓ | ⚠ 中（逻辑可读 ✓ 但依赖其消息协议 ✗） | 4~6 天 ✗ | **不移植 ✗ 我们的 agent 循环已成熟（35 工具+闸门 ✓）** |
| **动作执行层**（点击/输入/滚动/标签切换） | chrome.debugger / content script ✓ | ⚠ 中 | 3~5 天 | **只借鉴 ✓ 我们直接用 Playwright（更稳 ✓）** |
| **★ 权限与安全模型**（Ask/Auto · 每站 Allow/Ask/Block · 敏感动作拦 ✓） | 设计 ✓ | **✓✓ 高（纯设计 ✓ 代码量小 ✓）** | **0.5 天 ✓✓** | **★ 移植这个 ✓✓（最有价值 ✓）** |
| **★ 上下文压缩**（长任务旧步骤自动摘要 ✓） | 设计 ✓ | ✓ 高 | 1 天 ✓ | **移植 ✓**（我们也有 MAX_ITER ✗ 但没有摘要 ✓） |
| 多任务并行（每任务一个 tab group ✓） | 设计 ✓ | ⚠ 中 | 2 天 | 第二阶段 ✓ |
| Memory / 快捷指令 / 语音 | 设计 ✓ | ✓ 高（可选 ✓） | 1~2 天 | 低优先 ✓ |
| 下载/上传/截图封装 | 小 ✓ | ✓ 高（我们 Playwright 有 ✓） | 0.5 天 | 用 Playwright ✓ |

### 移植总评
```
✗ 全量移植（5~7 天+ 且技术栈错配 ✗）= 不划算 ✗
✓✓ 移植【设计】而非代码（安全模型 0.5 天 + 上下文压缩 1 天 ✓）= 高性价比 ✓
   —— 这正是我们自己已经证明过的模式（我们抄的是 BFCL 的判分口径 ✓ 不是抄代码 ✓）
```

## 五、Playwright MCP 评估（我们真正要集成的"手" ✓）

| 维度 | 结论 | 依据 |
|---|---|---|
| 兼容性 | **✓✓ 极好** | 它是 **npm 包**（39 个文件 ✓ `npx @playwright/mcp@latest` ✓）⇒ **不需要移植 ✓✓ 直接当 sidecar 起 ✓** |
| 驱动 Edge | **✓✓ 官方支持** | "Connect to a running Chrome or Edge by its channel name"·channels 含 **msedge** ✓·扩展模式 ✓ 复用登录态 ✓ |
| 工具集 | **✓✓ 最全** | 导航/交互/表单/键鼠/标签页/对话框/上传/等待/网络/存储/控制台/截图/代码执行/追踪/断言/PDF/录屏 ✓ |
| 移植必要性 | **✗ 不需要移植** | 直接 `npx` 用 ✓（Apache-2.0 ✓ 也可 fork 改 ✗ 但没必要 ✗） |
| 风险 | ⚠ 版本 v0.0.83（新 ✓ 接口可能变 ✗） | 建议**锁版本**（package.json pin ✓） |

```
⇒ 结论: Playwright MCP = 我们的"手" ✓ 零移植成本 ✓（这就是我上轮说的 Node sidecar ✓ 现在有硬事实支撑 ✓✓）
```

## 六、UI-TARS Desktop 评估（浮窗体验参考）

| 维度 | 结论 |
|---|---|
| 兼容性 | ✗ 整套 Electron 桌面 app ✗ 与我们的 Web 平台不兼容 ✓ |
| 移植 | ✗ 代价最大（4~6 天+ 且要发安装包 ✗） |
| 值得借鉴 | ✓ **always-on-top 浮窗** + **视觉兜底**（DOM 拿不到时用截图+坐标点击 ✓）= 我们的"兜底路径" ✓ |
| 许可 | Apache-2.0 ✓ |

## 七、综合结论：推荐组合（既有硬事实 ✓ 又成本最低 ✓）

```
【方案 B · 借鉴设计 + 自研薄层】✓✓ 推荐
  前端: 我们的 index3.html 新增「浏览自动化」tab ✓（模型独立选择 ✓ 网址输入 ✓ 对话框 ✓ 技能面板 ✓）
  浮窗: 阶段① Edge 侧边栏/应用窗口 ✓（0 开发 ✓）→ 阶段② 扩展浮层（借鉴 Nanobrowser 侧边栏设计 ✓）
  驱动: Node sidecar ✓ 用 Playwright（--cdp-endpoint=msedge 或 --extension 连 Edge ✓✓）
  安全: ★ 移植 Nanobrowser 的 Ask/Auto + 每站 Allow/Ask/Block 设计 ✓（0.5 天 ✓ 最有价值 ✓）
  上下文: 借鉴其"长任务旧步骤自动摘要" ✓（1 天 ✓）
  MX: 不移植 Nanobrowser 代码 ✗ · 不引入 Electron ✗ · 只锁版本用 Playwright ✓

【备选 · 方案 A】 让用户直接装 Nanobrowser（免费 ✓ 立刻可用 ✓）
  我们平台只做"结果对接"（它下载的 CSV/MD → 我们 KB ingest ✓）
  ⇒ 零开发 ✓ 但拿不到"平台内闭环"（模型/判分/KB 联动 ✗）
  ⇒ 适合"先给用户用起来"的过渡 ✓

【不推荐 · 方案 C】 全量移植 Nanobrowser ✗（技术栈错配 ✓ 5~7 天+ ✓ 我们已有 agent 框架 ✓）
```

## 八、移植工作量与风险

| 项 | 工作量 | 风险 |
|---|---|---|
| 前端新 tab（模型/网址/对话/技能） | 1.5 天 | 低（复用现有组件 ✓） |
| Node sidecar + Playwright 连 Edge | 1 天 | 中（Edge 远程调试需用户允许 ✓ 一次性 ✓ · Playwright 版本锁 ✓） |
| 浏览器工具（10 个 ✓ 注册进 /docs/tool/list ✓） | 1.5 天 | 低（官方能力 ✓） |
| ★ 安全模型（Ask/Auto + 站点规则 + 二次确认 ✓） | 0.5~1 天 | 低（纯设计 ✓ 有现成范式 ✓） |
| 上下文压缩（旧步骤摘要 ✓） | 1 天 | 低 |
| 判分器接入（浏览器任务也能判 ✓） | 0.5 天 | 低 |
| **合计（P1 可用版）** | **≈5 天** | 主要风险=安全边界 ✓ |

## 九、需要你确认的两件事（否则我只能按默认假设做 ✓）

```
1. "这个开源项目"具体指哪个？—— 我默认按【Nanobrowser 借鉴 + Playwright 集成】写的 ✓
   （若是 UI-TARS 或 mcp-chrome ✓ 我再出对应深度评估 ✓）
2. 浮窗形态: Edge 侧边栏（0 开发 ✓）/ 扩展浮层（+3~5 天 ✗）/ 桌面置顶窗（+4~6 天 ✗）
```

## 十、参考来源（可点 ✓）

- Nanobrowser 仓库（Apache-2.0 · v0.3.0）: https://github.com/nanobrowser/nanobrowser
- Nanobrowser 商店页: https://chromewebstore.google.com/detail/nanobrowser-ai-web-agent/imbddededgmcgfhfpcjmijokokekbkal
- Playwright MCP 仓库: https://github.com/microsoft/playwright-mcp
- Playwright 官方「连接已运行浏览器」（msedge 通道 / 扩展模式）: https://playwright.dev/mcp/configuration/browser-extension
- UI-TARS Desktop 仓库: https://github.com/bytedance/UI-TARS-desktop
- browser-use 仓库: https://github.com/browser-use/browser-use
