# 评估：把 DSH（DeepSeek Harness）插件移植到本工作台的内置 Agent

> 结论先说：**代码层面不能直接移植；机制层面可借鉴，但性价比最高的不是"抄插件格式"，
> 而是先做「单一真源」+ 接入 MCP。**
> 本文为**只读调研**结论（未改动、未安装任何东西）。

## 一、DSH 插件到底是什么（有文件为证）

| 项 | 事实 | 证据（本机 npx 缓存安装目录） |
|---|---|---|
| 框架 | **Cordis** —— 一个插件框架，不是 DSH 自创 | `node_modules/@deepseek-ai/cordis-plugin-loader`、`-group`、`-include`、`-timer` |
| 插件形态 | **npm 包**，`package.json` 里声明 `dsh.bundle.patch` | `dsh-agent-preset/skills/cordis-plugin-development/templates/mcp/package.json` |
| 兼容性 | 检查插件对 `@deepseek-ai/dsh*` 的 **peer 依赖**与运行时版本是否匹配 | `dsh-app-boot/lib/types/plugin-compatibility.d.ts` -> `evaluatePluginCompatibility()` |
| 两类插件 | **Host 插件**（`index.js`，Node 侧，可注册工具/服务/事件）与 **Client 插件**（`client.js`，浏览器侧，向 Slot 注册 UI） | `references/host-plugin.md`、`references/ui-plugin.md`、`templates/decoration/`（4 文件） |
| 装载方式 | 通过 **`plugin_manager` 工具的 `install_bundle`**，不允许手写 profile 配置 | `SKILL.md` |
| MCP 支持 | 一个**只有配置**的 bundle（两份文件）即可接任意 MCP 服务器，自动获得 `mcp__<serverName>__<tool>` 工具 | `templates/mcp/cordis.patch.yml` + `@deepseek-ai/dsh-mcp-client` |

MCP bundle 的清单原文（极简）：

```json
{
  "name": "@local/demo-mcp",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```

## 二、本工作台的 Agent 是什么（实测）

```
webui/index3.html = 7942 行 · 150 个函数 · 79 个 window 导出 · 4 个 <script> 块
工具注册 = const AGENT_TOOLS = {};  逐条 AGENT_TOOLS.名字 = { dangerous, handler }
派发点   = 只有一处 const tool = AGENT_TOOLS[call.name];   (index3.html:7749)
提示词   = buildAgentSystemPrompt() 里【硬编码字面量】逐条列出工具
外部脚本 = 有 2 个 <script src=>，但无 type="module"、无动态 import
后端     = lora_manager.py 单文件，零插件机制
```

## 三、可行性判定

### 3.1 直接移植 DSH 插件：不可行

| 障碍 | 原因 |
|---|---|
| 运行时不同 | DSH 插件是 Node 进程内的 Cordis 包；本工作台是"浏览器内 JS 对象 + FastAPI" |
| peer 依赖锁死 | 插件声明依赖 `@deepseek-ai/dsh*`，装载前严格校验运行时版本 |
| Client 插件需打包 | 由 DSH 的 bundler 构建并经 HMR 分发；本页面没有构建步骤 |
| 配置方言 | `cordis.patch.yml` 是 Cordis loader 的 patch 方言（含 `!!js` 引用凭据等） |

### 3.2 可行且值得的两条路

**路线① 移植"机制"而非"代码"（中等工作量）**

- 插件 = 一个目录，内含 `plugin.json`（名字/版本/是否危险/参数 schema）
- 前端启动时 `fetch('/plugins/index.json')`，逐个注入 `<script src="/plugins/<name>/client.js">`，
  该脚本调用 `registerAgentTool(name, def)` 注册工具
- `AGENT_TOOLS` 与**提示词里的工具清单**都从注册表**自动生成** —— 顺带消除当前的"三处硬编码"
- 后端增加 `plugins/` 目录扫描（`importlib`），让插件可以提供 REST 端点

**路线② 用 MCP 做互操作（推荐）**

- 本工作台的 Agent 当 **MCP 客户端**：加"MCP 服务器"配置即可调用任何 MCP 工具
- 本工作台的 45 个工具当 **MCP 服务器**：DSH 用纯配置 bundle 就能直接调它们
- 好处：**不必移植任何代码**，用标准协议立刻互通

## 四、建议的实施顺序

1. **先把工具注册表改成单一真源**（`AGENT_TOOLS` 生成提示词与 KB）
   —— 这是当前最痛的结构问题：加一个工具要同时改注册表、提示词、知识库三处。
2. **加 MCP 客户端支持** —— 用标准协议获得插件生态，比自造插件格式划算。
3. 若确实需要自有插件体系，再照 Cordis 的**分层思路**做轻量版
   （Host/Client 分离、清单 + 加载器、兼容性校验），**不必兼容其方言**。

## 五、硬走移植的兼容性风险

| 风险 | 说明 |
|---|---|
| 安全模型不同 | DSH 插件默认需审批、凭据用 `!!js` 引用不落文本；本工作台若直接 `eval` 插件代码等于任意代码执行，必须加白名单/签名/目录限制 |
| 无版本校验 | DSH 靠 peer 依赖挡不兼容；缺此机制时插件升级会静默损坏 |
| 浏览器端无隔离 | Client 插件与页面同源同上下文，可读写全部界面与 localStorage（DSH 用 Slot API 收窄了权限面） |
| 无热更新链 | 缺 bundler + HMR，改插件需手动刷新（可接受） |

## 六、一句话结论

> "能不能把 DSH 插件移植给我的内置 agent？"
> **代码层面不能**（Node/Cordis 的 npm 包，绑死 DSH 运行时与打包链）；
> **机制层面可以**，但**优先做「单一真源 + MCP」**：
> 前者解决现有结构痛点，后者用标准协议直接获得插件生态。

---

# 附录：深挖调研补充（第二轮，仍为只读）

> 本节修正了初版的两处猜测错误，并补充了带文件路径的精确事实。
> 证据根目录：`$N = <npx 缓存>\node_modules`、`$P = C:\Users\A\.dsh\profiles\web\node_modules`
> （后者是**本机真实 profile**，装了 6 个第三方插件）。
> 注意：`$N` 是 npm 发行包，**不是源码仓**（没有 apps/ 与 packages/）→ 实现细节只能靠 README + .d.ts。

## 更正 1：没有 `plugin.json`

初版建议"插件 = 目录 + `plugin.json`"是**错的**。
全树 grep `plugin.json` **零命中**；声明点只有两个，都在 **`package.json`** 里：

- `dsh.bundle.patch`（Host 侧 bundle 的 patch 文件路径，字符串或数组）
- `dsh.client.external`（客户端需要额外模块表项）

权威类型文件（**这是最值得抄的一份**）：

`$N\@deepseek-ai\dsh-package-manifest\lib\types\types.d.ts`

```ts
export interface DshManifest {
    manifestVersion?: 1;
    bundle?: DshBundleManifest;
    profile?: DshProfileManifest;
    client?: DshClientManifest;
}
export interface DshBundleManifest { patch: string | string[]; }
export interface DshProfileManifest { bundles?: string[]; }
export interface DshClientManifest {
    platform: string;       // Web 消费者选 "web"
    inject?: string[];      // 信息性包名依赖，**不是** cordis 注入
    immediately?: boolean;  // 启动第一阶段注册屏障
    external?: string[];    // 超出基线的精确模块表请求
}
```

显示元数据（**不激活插件**即可读）：顶层 `icon` 路径（svg/png/jpeg/webp ≤256KiB）+ `locale/<lang>.json` 里的 `{meta:{title,description}}`。
校验是**真做**的（非空谈），例如 `dsh-app-boot\lib\index.js:497`：

```js
if (!Array.isArray(declared) || !declared.every((file) => typeof file === "string"))
    throw new Error("dsh.bundle.patch must be a file path or a list of file paths");
```

## 更正 2：插件代码形态（无装饰器）

`$N\@deepseek-ai\cordis\lib\types\registry.d.ts:48-81`：

```ts
export type Plugin<T = any> = Plugin.Function<T> | Plugin.Constructor<T> | Plugin.Object<T>;
namespace Plugin {
  interface Base<T> { name?: string; Config?: StandardSchemaV1<any,T>;
                      inject?: Inject; provide?: string|string[]; intercept?: Dict<boolean>; }
  interface Function<T>    { (ctx: Context, config: T): any }
  interface Constructor<T> { new (ctx: Context, config: T): any }
  interface Object<T>      { apply(ctx: Context, config: T): any }
}
```

即：**一个普通 JS 模块**，导出 `apply(ctx, config)`（或类 / `{apply}`），可选 `name` / `inject` / `provide` / `Config`。
（代码库里唯一的装饰器 `@Remote({mode:'stream'})` 是给 RPC 方法用的，不是插件声明。）

## 关键事实一：DSH 是 Cordis 的 vendored fork —— 而 Cordis 是独立 MIT 包

- `$N\@deepseek-ai\cordis\package.json`：`"name": "@deepseek-ai/cordis", "version": "4.0.4"`，
  `repository.directory = "vendor/cordis"`，作者 Shigma（Koishi 作者）；
  `@deepseek-ai/schemastery` 也是 Koishi 的 schema 库。
- 只有 `cordis` 与 `schemastery` 随包发布 `src/`，其余包只有 `lib/*.js` + `.d.ts`。
- **含义**：插件/DI 内核本身**不是 DSH 专有**，可独立使用 → 这是"可移植"的核心依据。

## 关键事实二：贡献点都是 ctx 上的普通服务（可逐条对应实现）

| 能力 | 服务/API | 证据 |
|---|---|---|
| **注册 Agent 工具** | `ctx.tools.register(definition)` | `dsh-tools\lib\types\index.d.ts:636`；真实外部插件 `$P\dsh-comfyui-canvas\lib\index.js` 有 **27 处**调用（如 :671 `comfyui_read_workflow`）|
| 工具限制/守卫 | `restrict()` :644 / `guard()` :655 / `get()` :690 / `execute()` :765 | 同上 |
| **加界面** | `ctx.slots.register({name}, Component)` + `ctx.slots.inject(slot, …)` | `dsh-client-ui-slots\lib\types\index.d.ts`（`SlotMap{}` 与 `SlotFactoryMap{}` 是**空的**，靠声明合并由各 UI 包扩展 → 槽位词表**不集中**，需 `Slots.listSubTree` 运行时枚举）|
| 真实槽位名 | `sidebar.brand.mark/name`、`conversation.view`、`conversation.session.header.utilities`、`conversation.composer.dock`、`settings.section`、`shell.overlay` | canvas `lib\client.js:1336-1364`；`dsh-client-ui-brand-official\lib\client.js:36-38` |
| **斜杠命令** | `CommandRuntime.register/list/execute`（`extends TypertRemoteService` → 浏览器可经 RPC 调） | `dsh-commands\lib\types\index.d.ts:80,94,106,142` |
| **钩入 agent 循环** | `ctx.on(...)`；事件名有实证 | `agent/created`、`agent/pre-step`、`agent/request`、`agent/assistant-stream`；`tools/pre-execute|execute|post-execute|result`；瀑布族约定"不拥有决策就必须 `return next()`" |
| **注入系统提示词/KB** | `ctx.systemPrompt.section({name,order,text})` | `dsh-system-prompt\lib\types\index.d.ts:47,239,292` |
| 聊天行渲染器 | `ctx.uiConversation.events.register()` + `conversation.chat.node` 槽 | practices.md:37 |
| 工具结果渲染器 | 工具定义里的 `output.render` | canvas `index.js:676` |
| 配置 | 插件导出 `Config`（schemastery 校验，**启动前**校验） | registry.d.ts:55-56；canvas `index.js:36` 用 `z.object({...}).volatile()` |
| **MCP 服务器** | **纯配置 bundle，零代码** | `templates\mcp\cordis.patch.yml`：insert 一行 `name: '@deepseek-ai/dsh-mcp-client'` + `config{serverName,transport,url,failOnStartupError}` |

## 关键事实三：patch 层组合模型（这是最值得借鉴的"机制"）

`cordis.patch.yml` 是一个 **YAML patch 方言**：

- `insert: [rows]` 追加行；若给了 `id`（指向一个 `group: true` 的行）则插进该组内
- 带 `id` 且无 `insert` 的 patch = **覆盖那一行**；给出的字段替换字段，而 **`config` 是整体替换、绝不深合并**
- 行字段：`id` / `name`（包标识）/ `config` / `disabled` / `inject` / `intercept` / `isolate`
- `disabled` 可为 bool / null / `!!js` 表达式（**每次挂载决策都会重新求值**）
- `!!js` 标量在声明的注入激活后、针对该插件的 ctx 求值（如 `!!js dshHomePath('sessions')`）
- 组合顺序：各 bundle 的 patch（按 `dsh.profile.bundles` 顺序）→ profile 自己的 `cordis.patch.yml` → `--patch` 覆盖层

本机真实 profile 的 `package.json`：

```json
"dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app",
  "@zhangyi/dsh-llm-ollama","@liustack/modlens","dsh-ollama-vision-bridge",
  "dsh-fetch-timeouts","dsh-comfyui-canvas"], "patchReload": "live" } }
```

而 `profiles\web\cordis.yml` 内容字面就是 `[]`，注释写着：
> "dsh profile root — an empty entry list. The tree is composed as patches… **Edit cordis.patch.yml, not this file.**"

## 关键事实四：三个运行平面（安全边界很关键）

| 平面 | 事实 |
|---|---|
| **Host 半** | 与 agent 核**同一个 Node 进程**，且 README 明说：**"installed Host code executes in-process outside the workspace sandbox"** ✗ |
| **浏览器半** | 独立产物：`dsh.client.platform = 'web'` → 构建到 `lib/client.js` → Host 经 HTTP 提供 `/plugins/<pkg>/client.js` → 由 `window.__ModuleLoader__.load({id, factory})` 装载（lazy-CJS：跑 bundle 只注册 factory，副作用在物化时才发生）|
| **模块表** | 冻结的 `PLATFORM_MODULES` 只含 React / Cordis / 若干静态 UI 库；**其余必须声明在 `dsh.client.external`**。真实插件 `dsh-comfyui-canvas\lib\client.js` 只 require 了 `react` 一个模块 |
| **UI↔后端** | ① Typert **Remote RPC**（Connection `/api` FetchHandler 上的 WebSocket upgrade；每次一元调用返回 `RemoteResult<T>`）；② `/plugins` 取码 + `/plugins/events` SSE 报重建；③ 启动图 `window.__DSH_BOOT__`（`<` 已转义以防插件字符串逃出 script 元素）|

## 关键事实五：HMR 的真实条件（本机做不到）

- Host 半**轮询 stat** 每个包的 `lib/client.js`（`pollIntervalMs` 默认 500ms），用 **mtime/ctime/size** 判定修订号（**不算内容哈希**）
- 真正重建的是**外部的** `tsdown --watch`：每个客户端包 `"bundle": "tsdown"` / `"watch": "tsdown --watch"`；
  需要 `pnpm run dev:web`
- 本机这份是**npm 发行包** ✗ **没有构建管线** ✗ → **`pnpm run dev:web` 指向的源码仓并不存在** ✗✓
  ⇒ **在这台机器上无法重建客户端插件** ✓
- 失败**不回滚**；重载会丢插件内的 React state（会话/工作区/连接状态保留）；移除或替换 bootstrap **需要刷新页面**

## 关键事实六：最好的端到端范本是一个**外部作者**的插件

`$P\dsh-comfyui-canvas\`（`dsh-comfyui-canvas@0.1.13`，github.com/wbin0001/dsh-comfyui-canvas）——
**它就是这个 GUI 里的画布标签页本身** ✓✓。文件布局：

```
package.json   type:module; main lib/index.js; exports {".":…,"./client":"lib/client.js",…}
               peerDependencies: @deepseek-ai/dsh-tools >=0.1.1-rc.2（+settings 为 optional）
               scripts: check = node --check lib/index.js && node --check lib/client.js
               dsh: { client:{platform:"web", inject:["@deepseek-ai/dsh-client-ui-settings"]},
                      bundle:{patch:"./cordis.patch.yml"} }
cordis.patch.yml   insert: - id: comfyui-canvas / name: dsh-comfyui-canvas
lib/index.js   102KB Host 半：inject=['tools','settings','shell','skills']，27× ctx.tools.register
lib/client.js   70KB 浏览器半：只 require "react"；注册进 conversation.view /
               conversation.session.header.utilities / settings.section
```

**两点重要认知**：
1. **画布/分屏标签不是 DSH 的原生概念** ✗ —— DSH 里没有任何 canvas 包；那个分屏是**这个第三方插件自己用 DOM 属性实现的** ✓（它甚至注入了 `<style>` 与 MutationObserver 看门狗，比官方指引里的"不要在组件外写 DOM"更激进 → 说明**客户端插件的 DOM 并没有被真正围栏**，被围栏的只有模块表 ✓）
2. 因此"**外部项目采用该契约**"已被**实际证明** ✓✓ —— 但代价是：它必须自己实现分屏、自己处理重载、自己声明 peer 依赖 ✓

## 可移植 vs 不可移植（明确标注为推断）

**可移植（小且可分离）**
1. `dsh-package-manifest\lib\types\types.d.ts` —— 清单接口（**直接照抄**）
2. **patch 层组合模型**（有序层 / 按 id 覆盖 / config 整体替换）
3. **插件形态**（`apply(ctx, config)` + `name`/`inject`/`Config`）与 **Cordis 本身**（MIT、独立包、非 DSH 专有）
4. 工具 / 槽位 / 命令 / 提示词这几个**注册表**（都是 ctx 上的普通服务接口）

**不可移植（DSH 专有）**
- `window.__DSH_BOOT__` 启动图、`/plugins` 与 `/plugins/events` 路由
- Typert Remote 的 WebSocket 网关
- 冻结的 `PLATFORM_MODULES` 模块表与 tsdown 的 stamping 约定

**结论（与初版一致，依据更硬）**：
独立 agent 项目**低成本采用 cordis + 清单/patch 模型**是可行的 ✓；
但**复用某个真实的 DSH 插件**则必须**重新实现那套宿主契约**（bundle 提供、启动图、槽位注册表、Remote RPC）✗。
⇒ 仍然推荐"**先单一真源 + MCP**"，而不是移植插件 ✓✓。

## 未能确定的部分（诚实清单）

1. tsdown 的 **Client preset** 本身不随包发布（只能看到 `"bundle":"tsdown"`）
2. Web shell 源码缺失（`dsh-web-frontend` 只有压缩后的 `dist/assets/index-*.js`）
3. DSH 各包**没有 `src/`**（只有 cordis 与 schemastery 有）→ Loader 内部、客户端模块组合、插件管理操作都只能依据 README + `.d.ts`
4. profile 里 `"patchReload": "live"` 的**读取方**未在发行包中找到（推断为 profile 级实时 patch 开关）
5. `@deepseek-ai/dsh-client-ui-dockkit` 被模块表引用但**没有独立包目录** → 分屏/停靠 API 无法在本机查看
6. 未运行任何 DSH 进程，**加载顺序 / HMR 交换 / 级联**均来自文档，未经运行时验证
