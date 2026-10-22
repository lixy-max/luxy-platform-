# Office/Word 能力：走 MCP，别自己重写

## 事实（本机实测）

- 本机 DSH profile 里装的是 **`dsh-plugin-wps-office@0.1.0`**。
- 它的 README 明说：**"这是一个独立的 DSH 适配层。它不复制 WPS MCP、WPS 加载项或上游 Skills，
  而是通过 DSH 自带的 MCP Client 启动现有 `wps-office-mcp`，并补充一份适配 DSH 工具命名的轻量 Skill。"**
- 工具暴露形式：`mcp__wps-office__<上游工具名>`（用 serverName 前缀避免冲突）。
- 上游 `lc2panda/wps-skills` 提供 **200+ 个专业工具**；适配层本身只有一个 `skill.js` + `SKILL.md` 与补丁行。
- 前提条件：WPS Office（含加载项）、已构建的上游 checkout、Node 22.19+/24+。
- DSH 还自带 `dsh-skill-office`、`dsh-office-to-pdf`、`libreoffice-kit`、
  `dsh-client-ui-sidebar-documentpreview`、`dsh-client-file-upload`。

## 对本项目的含义

1. **不需要抄代码**：那个插件是 MCP 适配层，真正的能力在上游 MCP Server 里；
   我们与它"兼容"的方式是**接同一个协议**，而不是复制它的实现。
2. **两条互通的路线**（都在既有评估文档里写过，此处落到 Office 场景）：
   - **当 MCP 客户端**：本项目内置 agent 增加 MCP 客户端能力 -> 直接获得
     `mcp__wps-office__*` 那 200+ 个文档工具（Word/Excel/PPT 自动化、目录、页眉页脚、
     格式与引用规范等）。这**直接补上当前"工具链不全"**的问题。
   - **当 MCP 服务器**：把本项目已有的 33 个文档工具暴露成 MCP Server ->
     DSH（外部 harness）可以直接调用它们，本项目不必改造 agent。
3. **优先级**：先做"当客户端"（收益最大，直接扩工具链）；"当服务器"是互操作与展示价值。

## 待确认（决定怎么动手）

- 这台机器是否装了 **WPS Office**？是否愿意按上游说明装 **WPS 加载项**并构建
  `lc2panda/wps-skills`？（没有 WPS 的话，可退而用 `libreoffice-kit` / `dsh-office-to-pdf`
  这类自带能力，或自己实现 docx 侧能力——本项目已有 python-docx 基础。）
- 若不引入 WPS：本项目现有的 python-docx / openpyxl 工具链可以继续自建，
  但要把"目录/页眉页脚/样式/引用规范/PDF 导出"这些补齐（对照上游那 200+ 工具的能力清单）。

## 相关文档

- `docs/DSH_PLUGIN_PORTING_ASSESSMENT.md` —— 插件体系与可移植性评估（含 MCP 路线）
- `docs/BASELINE.md` —— 当前工具数与规模基线
