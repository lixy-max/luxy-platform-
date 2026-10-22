# 可行性分析：前端「浏览器自动化」页 + Edge 浮窗对话操作

> 日期: 2026-10-19 · 性质: **可行性分析**（只分析 ✓ 不实现 ✗）
> 用户需求原文要点: ① 工具栏新增"浏览自动化"界面 ② 可单独选模型 ③ 浏览器工具 skill（截图/下载/指针操作…）
> ④ 顶部搜索框输网址直接用 **Edge** 打开 ⑤ 浏览器界面里出现**浮窗** ⑥ 浮窗里**对话框**自然语言操作浏览器
> 方法: 先搜开源方案做类比（附来源 ✓）→ 再逐条映射 → 三方案对比 → 推荐 + 分期 + 风险

---

## 一、结论先行

```
✅ 技术上【完全可行】✓ 且有官方能力直接支撑（Playwright 原生支持 Edge 通道 + 扩展模式 ✓）
✅ 我们已有底座: 前端页面框架 ✓ 模型选择 ✓ 工具注册单一真源 ✓ 判分器 ✓ 浏览器驱动经验（playwright-core ✓）
⚠ 真正的难点不是"能不能做"，而是 ①浮窗形态怎么选 ②安全（驱动用户真实 Edge = 高风险 ✗）
⇒ 建议: A 方案起步（2~3 天出可用版 ✓）+ 浮窗先用"Edge 侧边栏/应用窗口" ✓ 后续再决定是否上扩展/桌面壳
```

## 二、需求逐条 → 实现映射（每条都有依据 ✓）

| # | 你的需求 | 实现方式 | 依据/风险 |
|---|---|---|---|
| 1 | 工具栏新增「浏览自动化」页 | 现有 `index3.html` 加一个 tab（与 11 个现有页同构 ✓） | 低风险 ✓ |
| 2 | 可单独选模型（≠ 全局） | 复用现有模型下拉 + 每请求带 `model` ✓（`/chat/tools` 已支持 ✓） | 零开发 ✓ |
| 3 | 浏览器工具 skill（截图/下载/指针） | **Playwright 现成**: `screenshot` / 下载（`Browser.setDownloadBehavior`）/ `mouse.move|down|up` / 点击 / 输入 / 键盘 / 标签页 / 上传 / 对话框 ✓ | ★ 官方工具文档已列全 ✓ 不自创 ✓ |
| 4 | 搜索框输网址 → 用 **Edge** 打开 | **Playwright 官方通道**: `--cdp-endpoint=chrome` 支持 `msedge` ✓（只需 Edge 允许远程调试 ✓）或扩展模式 ✓ | ★ 官方明确支持 ✓ |
| 5 | 浏览器界面里出现**浮窗** | 三选一: (a) Edge「安装为应用」窗口/侧边栏 ✓零开发 (b) 写 Edge 扩展浮层 ✓体验好 (c) Tauri/Electron 置顶壳 ✓最强 | ⚠ 这是唯一需要**产品决策**的点 |
| 6 | 浮窗里对话框自然语言操作 | 现有 agent 循环（`/chat/tools` + 工具闸门 + 质量闸门 ✓）原样复用 ✓ + 新工具注册 ✓ | 复用 ✓ |
| 7 | （隐含）安全: 在用户真实浏览器里动 | **最大风险** ✗ 提示注入 + 已登录账号 + 误操作 ⇒ 域白名单 + 只读默认 + 危险动作二次确认 + cookie 不进模型 | ★ 必修 ✓ |

## 三、开源方案类比（四类代表 ✓ 都有公开来源）

| 类别 | 代表项目 | 形态 | 优点 | 缺点 | 来源 |
|---|---|---|---|---|---|
| **A. 浏览器扩展** | **Nanobrowser** | Edge/Chrome 扩展 ✓ 侧边栏/浮层 agent ✓ | 天然"浮在浏览器里"✓ 复用登录态/cookies ✓✓ 可分发给用户 ✓ | MV3 限制 ✗（Service Worker 生命周期/后台被回收 ✗）· 每浏览器单独适配 ✓ | [Chrome 商店](https://chromewebstore.google.com/detail/nanobrowser-ai-web-agent/imbddededgmcgfhfpcjmijokokekbkal) · [Firefox 版](https://addons.mozilla.org/ko/firefox/addon/nanobrowser-ai-web-agent/) |
| **B. 扩展即 MCP** | **mcp-chrome** | 扩展把浏览器暴露成 MCP 工具 ✓ 由外部 agent 驱动 ✓ | 后端/前端可复用现有 agent 循环 ✓ 扩展只当"手" ✓ | 用户要装扩展 ✓ 生态较新 ✓ | [Chrome 商店](https://chromewebstore.google.com/detail/mcp-chrome-extension-ai-b/mnchdcikbochaacmcfohjibjkhdbaahe) |
| **C. 桌面浮窗 Agent** | **UI-TARS Desktop**（字节） | Electron 桌面 app ✓ 控制桌面/浏览器/终端 ✓ **视觉驱动**（截图→点击 ✓） | 浮窗形态最自由 ✓ always-on-top ✓ 视觉方案不依赖 DOM ✓ 通用性最强 ✓ | 引入桌面壳 ✗ 分发/签名/更新 ✗ 视觉方案慢且贵 ✗ | [架构解析](https://xuqi2024.github.io/2026/09/10/2026-09-10-ui-tars-desktop-multimodal-agent-stack-architecture-deep-dive/) · [Repo 介绍](https://arifsolmaz.github.io/repo/2026/02/08/ui-tars-desktop/) |
| **D. 库 + 官方 MCP** | **Playwright MCP**（微软） | 官方 MCP server ✓ 通过 CDP/扩展连接**已运行的** Edge/Chrome ✓ | **官方直供** ✓ 工具集最全 ✓ 支持 `msedge` 通道 ✓ 扩展模式复用登录态 ✓ | 需要"浮窗 UI"自己搭 ✗ | [连接浏览器（官方）](https://playwright.dev/mcp/configuration/browser-extension) |

### 类比结论
```
"浮窗体验" → 学 A（Nanobrowser）或 C（UI-TARS）
"驱动 Edge + 工具集" → 直接吃 D（Playwright 官方 ✓ 支持 msedge 通道与扩展模式 ✓✓）
"我们自己的 agent 循环/模型选择/判分器" → 复用现有平台 ✓（这是我们的独特资产 ✓）
⇒ 最优组合: D 当"手" + 我们现有前端当"脑与脸" + 浮窗形态按阶段演进 ✓
```

## 四、三种可行架构对比

| 维度 | **A. 现有页面 + Edge 侧边栏/应用窗**（推荐起步） | **B. Edge 扩展浮层** | **C. Tauri/Electron 置顶壳** |
|---|---|---|---|
| 浮窗体验 | 中（Edge 侧边栏 ✓ 或"安装为应用"小窗 ✓） | **高**（页面内浮动按钮+对话框 ✓） | **最高**（真·always-on-top 浮在所有窗口上 ✓） |
| 开发量 | **2~3 天** ✓ | +3~5 天 ✗ | +4~6 天 ✗ |
| 复用现有前端 | **100%** ✓ | 部分 ✗（扩展里要重建 UI 或嵌 iframe ✓） | 高 ✓（内嵌页面 ✓） |
| 驱动 Edge | Playwright `msedge` 通道 ✓ / 扩展模式 ✓ | 扩展内 `chrome.debugger` ✓ 或 content script ✓ | Playwright CDP ✓ |
| 分发 | 只要访问 localhost ✓ | 需上架/侧载扩展 ✗ | 需安装包 ✗ |
| 安全控制 | 我们完全掌控 ✓ | 扩展权限=大 ✗（`<all_urls>` 需谨慎 ✗） | 完全掌控 ✓ |
| 推荐度 | **✅ 先做这个** | ⚠ 第二阶段（若要"贴页面"体验 ✓） | ⚠ 若要"浮在所有应用上"✓ |

## 五、推荐架构（A 方案，边做边升级）

```
┌────────────── 前端（现有 index3.html 新增 tab）──────────────┐
│ ① 模型选择（独立于全局 ✓） ② 网址搜索框（回车 ⇒ 后端打开 Edge）│
│ ③ 技能面板（截图/下载/指针/点击/输入/滚动/标签页 ✓）            │
│ ④ 对话框（自然语言 ⇒ /chat/tools ⇒ 浏览器工具 ✓）              │
└───────────────────────────┬───────────────────────────────┘
                            │ HTTP（现有后端）
┌───────────────────────────▼───────────────────────────────┐
│ 后端（lora_manager.py 新增 /browser/* 工具 + 会话管理）       │
│  · Playwright(Python) connect_over_cdp("http://127.0.0.1:9222")│
│    或官方 MCP client 连接 --extension 模式 ✓                  │
│  · 每个会话: 1 browser context ✓ 可指定下载目录 ✓ 截图存文件 ✓ │
│  · 工具注册进 /docs/tool/list ⇒ 自动进 agent ✓（单一真源 ✓）   │
└───────────────────────────┬───────────────────────────────┘
                            │ CDP / 扩展
                   ┌────────▼────────┐
                   │ 用户的 Edge（真实  │
                   │ 登录态 ✓ 已装扩展 ✓)│
                   └─────────────────┘
浮窗形态（阶段演进）: ① Edge 侧边栏/应用窗口 ✓ → ② 扩展浮层 ✓ → ③ Tauri 置顶窗 ✓
```

### 浏览器工具清单（按需求 ✓ 全部有 Playwright 对应能力 ✓）
```
browser_open(url)        导航          browser_screenshot()    截图（存文件 ✓ 供人看/判分 ✓）
browser_snapshot()       可交互元素清单（ref ✓ 不看整页 HTML ✗ 省 token）
browser_click(ref)       点击         browser_move(x,y)       指针移动 ✓（用户要的"指针操作"✓）
browser_fill(ref,text)   输入         browser_press(key)      键盘 ✓
browser_scroll(dir,px)   滚动         browser_tabs(action)    标签页列表/切换/关闭 ✓
browser_download(url|ref)下载         browser_upload(ref,file) 上传 ✓
browser_wait(text|ms)    等待         browser_pdf()           导出 PDF ✓
browser_close()          关闭会话
```
★ 关键设计（来自我们踩过的坑 ✓）:
- **别把整页 DOM 塞给模型** ✗ ⇒ 用"可交互元素清单 + ref" ✓（Playwright MCP 的 snapshot 思路 ✓）
- 每个动作带**回执**（成功/失败 + 关键状态 ✓）⇒ 判分器可判 ✓
- 动作确定性 ⇒ 与"仿真节点"同族 ✓ 递归守卫同样适用（网页上的数字也必须走仿真节点算 ✓）

## 六、风险与对策（安全是最大项 ✗）

| 风险 | 严重度 | 对策 |
|---|---|---|
| **提示注入**（页面文本冒充指令 ✗） | ★★★ | 页面内容一律打"外部不可信内容"标记 ✓ 敏感动作一律二次确认 ✓ 域白名单 ✓ |
| **已登录账号被误操作** ✗ | ★★★ | 默认**只读模式** ✓ 写操作（提交/下单/删除）一律确认 ✓ 第一版禁用 ✗ |
| 凭据泄露 | ★★★ | 复用登录态但**绝不把 cookie/密码进模型上下文** ✓ 只让模型看"元素 ref" ✓ |
| 反爬/被限制 | ★★ | 低频 ✓ 遵守 ToS/robots ✓ 不并发猛刷 ✓ |
| token 成本（截图/元素清单） | ★★ | 元素清单只给可见区 ✓ 截图默认不自动进上下文 ✓（只在需要时给 ✓） |
| 浏览器状态被搞乱（用户自己的标签页 ✓） | ★★ | 默认**新开独立标签** ✓ 不碰用户已有标签 ✓ 会话结束不自动关闭用户标签 ✓ |
| MV3 扩展被回收（B 方案） | ★ | 关键逻辑放后端 ✓ 扩展只当"手" ✓ |

## 七、工作量与分期（若批准）

| 阶段 | 内容 | 工作量 | 验收 |
|---|---|---|---|
| **P1** | Edge 联动: `-cdp-endpoint=msedge` 连接 + `/browser/open` + 截图/点击/输入 4 个工具 + 前端新 tab（模型选择 + 网址框 + 对话框 ✓） | **2~3 天** | 输入网址 ⇒ Edge 打开 ✓ 对话"截图" ✓ "点登录按钮" ✓ |
| **P2** | 完整技能（下载/上传/键盘/标签页/滚动/元素清单 ✓）+ 浮窗（Edge 侧边栏或应用窗口 ✓） | 2 天 | 10 个工具全通 ✓ 侧边栏可用 ✓ |
| **P3** | 安全三件套（域白名单 + 只读默认 + 二次确认 ✓）+ 判分器接入（浏览器任务也能判 ✓） | 2 天 | 恶意提示注入样本不执行 ✓ |
| **P4**（可选） | 扩展浮层 ✓ 或 Tauri 置顶窗 ✓ 或"网页→KB"闭环 ✓ | 3~5 天 | 按需 |

## 八、Go / No-Go

```
✅ GO —— 可行性高（官方能力齐备 ✓ 我们底座齐备 ✓ 有 4 类成熟方案可类比 ✓）
✅ 建议以 A 方案起步（2~3 天出可用版 ✓）· 浮窗先用 Edge 侧边栏/应用窗口 ✓
✅ 安全必须与功能同批交付（不能先上功能再补安全 ✗）
⚠ 唯一需要你决策: 浮窗形态（侧边栏 / 扩展浮层 / 桌面置顶窗 ✓ 三者体验与成本差异大 ✓）
```

## 九、参考来源

- Playwright 官方「连接已运行的浏览器」（支持 msedge 通道 / 扩展模式 / CDP）: https://playwright.dev/mcp/configuration/browser-extension
- Nanobrowser（浏览器扩展型 AI Web Agent）: https://chromewebstore.google.com/detail/nanobrowser-ai-web-agent/imbddededgmcgfhfpcjmijokokekbkal
- mcp-chrome（扩展即 MCP 服务器）: https://chromewebstore.google.com/detail/mcp-chrome-extension-ai-b/mnchdcikbochaacmcfohjibjkhdbaahe
- UI-TARS Desktop（字节，桌面浮窗 + 视觉驱动）: https://xuqi2024.github.io/2026/09/10/2026-09-10-ui-tars-desktop-multimodal-agent-stack-architecture-deep-dive/
- browser-use（库形态）: https://github.com/browser-use/browser-use
- Stagehand（act/extract/observe 范式）: https://docs.stagehand.dev/v3/references/stagehand

## 十、技术前提实测（2026-10-19 ✓ 不猜）

```
✅ Edge 存在: /mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe ✓
✅ Playwright 浏览器引擎已装（Windows 侧）: ms-playwright/chromium-1243 ✓ ffmpeg ✓ …
   ⇒ 我们已有的 drill_run.mjs 就是用它驱动过 200+ 任务 ✓
✗ WSL 后端 venv 里【没有 python playwright】✗
   若要在后端直接驱动，需要 pip install playwright + 下载引擎（~几百 MB ✗ 且可能撞网络 ✗）
✅ 9222 调试端口当前未开（预期 ✓ 需用户在 Edge 允许远程调试 ✓ 一次性 ✓）
```

### ★ 因此调整实现方式（重要 ✓）
```
原计划: 后端(Python) 直接 import playwright 驱动 Edge ✗（要装新依赖 ✗）
改为:   【Node sidecar】✓✓
  · 后端(Python) 起/管一个 Node 进程（或复用现有 Windows node ✓）
  · Node 侧用我们【已验证可用】的 playwright-core + ms-playwright 引擎 ✓
  · 通过 `chromium.connectOverCDP('http://127.0.0.1:9222')` 连用户 Edge ✓
    或 playwright.channel='msedge' 直接拉起 ✓
  · 后端与 sidecar 用 HTTP/JSON 通信 ✓（与现有 /docs/* 风格一致 ✓）
  · 好处: 零新 Python 依赖 ✓ 复用已验证引擎 ✓ 还能用 Windows 的 Edge ✓
  · 代价: 多一个常驻 Node 进程 ✓（可被 keeper 一起看护 ✓）
```
