# 界面美化方案（UI Redesign · 2026-10-19）

> 目标：**去掉表情包图标** ✓ 换成**开源图标库** ✓ 并整体提升视觉一致性 ✓
> 方法：全部**实测取证** ✓ 不凭感觉 ✓
> 原则：**离线可用** ✓（延续 CDN 本地化策略 ✗ 不引外链 ✗）· **零构建** ✓（仍是原生 JS ✓）· **可回滚** ✓

---

## 一、现状实测

### 1.1 表情用量（1211 个 · 78 种）
| 文件 | 数量 | 说明 |
|---|---|---|
| `index3.html` | **826** ✗ | 大部分是**注释/日志里的 ✓✗→★**（不是 UI 图标 ✓） |
| `browser_page.js` | 171 ✗ | 同上 + 真图标（🖼️截图/🖱️指针/📥下载…） |
| `rolechat.js` / `learn.js` / `reflect.js` | 51 / 39 / 29 | 真图标较多 |
| `pages/*.js` | 5~14 | 少量 |

**区分**（重要 ✓）：
- **注释/日志里的文字标记**（`✓`×371 `✗`×159 `→`×99 `★`×97 `⇒`×70）⇒ **不是 UI 图标** ✓ **保留** ✓（它们只是开发注释 ✓ 不显示给用户 ✓）
- **真·UI 图标**（约 **130 个** ✓）⇒ **必须换掉** ✗ 例如：
  `📚` 知识库 · `⚙` 设置 · `📋` 历史 · `📊` 质量 · `📄` 文档 · `🚀` 训练 · `➕` 添加
  `🤖` Agent · `☁` 云 · `🎭` 角色 · `📥` 导入 · `🗑` 删除 · `💾` 保存 · `📦` 发布 · `🔄` 刷新 · `💬` 对话

### 1.2 现有设计基础（还不错 ✓）
```
CSS 变量: 141 个 ✓（--c-* 颜色体系 ✓ + --c-dark-* 深色主题 ✓）
主题: data-theme 属性 ✓（light/dark ✓）
CSS 规则: 113 条 ✓（偏少 ✗ 没有组件层 ✗）
内联 style: 365 处 ✗（风格不统一的主要原因 ✗）
UI 元素: 按钮 107 · 弹窗 35 · 输入框 63 · 下拉 19 · 标签页 15 · 表格 1 · 卡片 0 ✗
```

### 1.3 关键约束
- ✅ **离线可用**：所有资源必须 vendored（已有 `webui/vendor/` ✓ 机制 ✓）
- ✅ **零构建**：不能用 npm 打包（项目是原生 HTML/JS ✓）
- ⚠ **测试依赖**：`tests/frontend/run.js` 有 **5 条断言硬编码表情** ✗（如 `/✅ extract_v2/`）⇒ 换图标时必须同步更新 ✓
- ✅ **E2E 兜底**：`tools/e2e_smoke.mjs` 10 条 ✓ 换完必须仍 10/10 ✓

---

## 二、图标库选型（开源 ✓）

| 库 | 许可 | 图标数 | 风格 | 引入成本 | 评价 |
|---|---|---|---|---|---|
| **Lucide** ⭐ | **ISC/MIT** ✓ | 1,600+ | 描边 2px 圆角 ✓ 现代 ✓ | 中（SVG ✓ 可做 sprite ✓） | **首选** ✓ 与现有简洁风格最搭 ✓ |
| Tabler Icons | MIT ✓ | 5,900+ | 描边 ✓ | 中 | 备选 ✓（更全 ✓） |
| Heroicons | MIT ✓ | 300+ | 描边/实心两版 ✓ | 低 | 够用但偏少 ✗ |
| Phosphor | MIT ✓ | 9,000+ | 6 种字重 ✓ | 中 | 太花 ✗ 本工具不需要 ✓ |
| Bootstrap Icons | MIT ✓ | 2,000+ | 实心偏多 ✗ | 低 | 风格不搭 ✗ |
| Font Awesome Free | CC BY 4.0 ⚠ | 2,000+ | 混合 ✓ | 低 | 许可要求署名 ⚠ 不选 ✗ |

### ✅ 推荐方案：**Lucide + SVG Sprite（零 JS 依赖 ✓）**

```
webui/vendor/icons.svg        ← 一个文件 · 只放用到的 ~130 个 symbol ✓（约 40~60 KB ✓）
用法:
  <svg class="icon"><use href="vendor/icons.svg#database"></use></svg>

优点:
  ① 零 JavaScript 依赖 ✓（一个 <use> 就够 ✓）
  ② 颜色跟随文字 ✓（currentColor ✓ 深浅色主题自动适配 ✓）
  ③ 离线可用 ✓ 可长缓存 ✓（跟 vendor 策略一致 ✓）
  ④ 按需添加 ✓（只放用到的 ✓ 不背全库 ✗）
  ⑤ 无 npm/构建 ✓ 纯静态 ✓
缺点:
  ⚠ <use href> 跨文件在部分旧浏览器有兼容问题（实测现代 Chromium/Edge 正常 ✓）
  ⇒ 兜底方案：生成 icon-*.js 内联 sprite（同文件内 ✓ 兼容性最好 ✓）
```

**实施细节**：
1. 从 Lucide 官方 npm 包/仓库取 **SVG 路径数据** ✓（ISC 许可 ✓ 可商用 ✓ 需保留 LICENSE 文件 ✓）
2. 写脚本 `scripts/build_icons.py` ✓：按**映射表**（见 §五）生成 `webui/vendor/icons.svg` ✓
3. 保留 `webui/vendor/LICENSE-lucide.txt` ✓（合规 ✓）

---

## 三、设计 Token 体系（在现有 141 变量上扩展）

### 3.1 现状：只有颜色 ✓ 缺其它 ✗
```css
/* 已有 ✓ */
--c-primary: #007bff;  --c-bg-light: #f8f9fa;  --c-dark-bg-body: #121417;  ...
```

### 3.2 新增 Token（统一视觉的关键 ✓）
```css
:root {
  /* 间距（8px 网格 ✓） */
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px;  --sp-4: 16px;
  --sp-5: 24px; --sp-6: 32px; --sp-7: 48px;
  /* 圆角 */
  --r-sm: 6px;  --r-md: 8px;  --r-lg: 12px;  --r-xl: 16px;  --r-full: 9999px;
  /* 阴影（3 档 ✓ 不要花哨 ✗） */
  --sh-sm: 0 1px 2px rgba(0,0,0,.06);
  --sh-md: 0 2px 8px rgba(0,0,0,.08);
  --sh-lg: 0 8px 24px rgba(0,0,0,.12);
  /* 字体 */
  --fs-xs: 11px; --fs-sm: 12px; --fs-md: 14px; --fs-lg: 16px; --fs-xl: 20px; --fs-2xl: 26px;
  --lh-tight: 1.3; --lh-normal: 1.55;
  --fw-normal: 400; --fw-medium: 500; --fw-bold: 600;
  /* 过渡 */
  --tr-fast: 120ms ease; --tr-mid: 200ms ease;
  /* 图标尺寸 */
  --icon-sm: 14px; --icon-md: 16px; --icon-lg: 20px; --icon-xl: 24px;
  /* 层级 */
  --z-dropdown: 100; --z-modal: 1000; --z-toast: 2000;
}
/* 图标基类 */
.icon { width: var(--icon-md); height: var(--icon-md); display: inline-block;
        vertical-align: -2px; stroke: currentColor; stroke-width: 2;
        fill: none; stroke-linecap: round; stroke-linejoin: round; flex: 0 0 auto; }
.icon-sm { width: var(--icon-sm); height: var(--icon-sm); }
.icon-lg { width: var(--icon-lg); height: var(--icon-lg); }
```

---

## 四、组件层（消除 365 处内联样式 ✗）

新建 `webui/styles.css`（~600 行 ✓ 替代现在散落的样式 ✓）：

| 组件 | 类名 | 现状 | 目标 |
|---|---|---|---|
| 按钮 | `.btn` `.btn-primary/secondary/ghost/danger` `.btn-sm/lg` | 107 个各自写 style ✗ | 一套系统 ✓ |
| 卡片 | `.card` `.card-header` `.card-body` | **0 个** ✗（全是 div+style ✗） | 统一卡片 ✓ |
| 徽章 | `.badge` `.badge-success/warn/danger/info` | 0 个 ✗ | 状态色统一 ✓ |
| 表格 | `.table` | 1 个 ✗ 裸 table | 斑马纹/悬停/紧凑 ✓ |
| 输入 | `.input` `.select` `.textarea` `.field` | 63+19 个裸元素 ✗ | 统一聚焦环 ✓ |
| 弹窗 | `.modal` `.modal-backdrop` `.modal-header/body/footer` | 35 个 ✗ | 统一 ✓ |
| 提示 | `.toast` `.tooltip` | 已有 ✓ 但样式杂 | 统一 ✓ |
| 空态 | `.empty` | 无 ✗ | 加 ✓ |
| 加载 | `.spinner` `.skeleton` | 无 ✗ | 加 ✓ |

---

## 五、表情 → 图标映射表（130 个 ✓ 核心部分）

| 现在 ✗ | 换成（Lucide id ✓） | 用在哪 |
|---|---|---|
| 📚 | `library` / `book-open` | 知识库 tab/页 |
| ⚙ | `settings` | 设置 tab/页 |
| 📋 | `clipboard-list` | 历史记录 tab |
| 📊 | `bar-chart-3` | 质量报告 |
| 📄 | `file-text` | 文档分析/结果 |
| 🚀 | `rocket` | 训练 tab |
| ➕ | `plus` | 新建/添加 |
| 🤖 | `bot` | Agent 模式 |
| ☁ | `cloud` | 云模型 |
| 🎭 | `drama` / `users` | 角色对话 |
| 📥 | `download` / `import` | 导入 |
| 📤 | `upload` / `export` | 导出/上传 |
| 🗑 | `trash-2` | 删除 |
| 💾 | `save` | 保存 |
| 📦 | `package` | 发布包 |
| 🔄 | `refresh-cw` | 刷新 |
| 💬 | `message-square` | 对话 |
| ✓（UI 里 ✓ 状态） | `check` | 成功状态 |
| ✗（UI 里 ✗ 状态） | `x` | 失败状态 |
| ⚠ | `alert-triangle` | 警告 |
| 🔍 | `search` | 搜索 |
| 🖼️ | `image` | 截图 |
| 🖱️ | `mouse-pointer` | 指针 |
| 🎯 | `target` | 目标 |
| ⏱ | `timer` | 耗时 |
| 📈 | `trending-up` | 趋势 |
| 🔧 | `wrench` | 工具 |
| 🧩 | `puzzle` | 插件 |
| 🔬 | `microscope` | 分析 |
| 🏠 | `home` | 首页 |
| 🌐 | `globe` | 浏览器自动化 |
| 📎 | `paperclip` | 附件 |
| ⬆/⬇ | `chevron-up` / `chevron-down` | 折叠 |
| ◀/▶ | `chevron-left` / `chevron-right` | 翻页 |
| ✕（关闭） | `x` | 关闭弹窗 |
| 🔔 | `bell` | 通知 |
| 🗂 | `folder` | 目录 |
| 💡 | `lightbulb` | 提示 |

> 完整映射（130+ 条 ✓）在实施时由脚本维护 ✓ `scripts/icons_map.json` ✓

---

## 六、分阶段实施（4 个阶段 · 每个独立可交付 ✓）

### Phase U0 · 基础设施（~2 小时）
| 项 | 内容 |
|---|---|
| ① 图标库 | 下载 Lucide SVG 数据 → `scripts/build_icons.py` 按映射生成 `webui/vendor/icons.svg`（只含有用的 ~130 个 ✓）+ LICENSE ✓ |
| ② 设计 token | 在 `index3.html` 的 `:root` 里加 §3.2 的 token ✓ + `.icon` 基类 ✓ |
| ③ 组件 CSS | 新建 `webui/styles.css`（§四 组件 ✓ 分批加 ✓） |
| 验证 | E2E 10/10 ✓ · 截图对比（改造前后 ✓） |

### Phase U1 · 换图标（~3 小时 · 核心 ✓）
| 步 | 内容 |
|---|---|
| ① | 按映射表**批量替换** UI 表情 → `<svg class="icon"><use href="#id"/></svg>` ✓（脚本做 ✓ 人工抽检 ✓） |
| ② | **不替换**注释/日志里的 ✓✗→★ ✓（它们是开发标记 ✓ 不显示 ✓） |
| ③ | 更新 5 条**依赖表情的测试** ✓（`/✅ extract_v2/` → 改成检查 `badge-success` 或文本 ✓） |
| ④ | 每换一个页面 → E2E + 截图 ✓ |
| 验证 | E2E 10/10 ✓ · 375+ 断言 ✓ · 12 个页面全部截图检查 ✓ |

### Phase U2 · 视觉统一（~4 小时）
| 项 | 内容 |
|---|---|
| ① 按钮 | 107 个 → 统一 `.btn` 变体 ✓（删内联 style ✓） |
| ② 卡片 | 主要区块 div+style → `.card` ✓ |
| ③ 表格/输入/下拉 | 统一 `.table` / `.input` / `.select` ✓ |
| ④ 弹窗 | 35 个 → 统一 `.modal` ✓ |
| ⑤ 空态/加载态 | 加 `.empty` / `.skeleton` ✓ |
| ⑥ 间距节奏 | 用 `--sp-*` 替换散落数字 ✓ |
| 验证 | 每类组件改完 → 截图 + E2E ✓ |

### Phase U3 · 打磨（~3 小时）
| 项 | 内容 |
|---|---|
| ① 导航重组 | 2 级 12 tab → **侧边栏分组**（对话 / 平台 / 系统 ✓） |
| ② 深色主题审计 | 已有 ✓ 但补漏（表格/弹窗/输入 ✓） |
| ③ a11y | `aria-label`（116 按钮 ✗ 现 0 个）· 焦点环 ✓ · 对比度 ≥ 4.5:1 ✓ |
| ④ 动效 | 只加 `--tr-fast` 级别 ✓ 不加花哨 ✗ |
| ⑤ 响应式 | 关键 3 页（对话/KB/浏览 ✓） |
| 验证 | E2E + axe 检查 + 截图 ✓ |

**总工时 ≈ 12 小时**（分 4 次 ✓ 每次可停 ✓）

---

## 七、验证方式（每个阶段都要做 ✓）

```bash
# ① 静态
node tools/check_js_syntax.js
node tests/frontend/run.js            # 必须 375/375（换了图标后改为 ≥375 ✓）
# ② E2E
node tools/e2e_smoke.mjs              # 必须 10/10
# ③ ★ 截图对比（新增 ✓ 用 Playwright）
node tools/ui_snapshot.mjs            # 12 个页面 × 深浅色 = 24 张截图 ✓ 放 docs/ui_snapshots/
# ④ 人工看
#    打开 http://127.0.0.1:8080/index3.html 逐页扫一遍 ✓
```

## 八、风险与回滚

| 风险 | 应对 |
|---|---|
| 图标 `<use>` 跨文件兼容 ⚠ | 兜底：生成**内联 sprite**（`icon-sprite.js` ✓） |
| 换图标破坏 5 条测试 ✗ | 同步改断言 ✓（改前先跑一遍记录基线 ✓） |
| 365 处内联 style 清理风险 ✗ | **逐页**改 ✓ 一页一提交 ✓ 截图对比 ✓ |
| 视觉改多了用户不习惯 ⚠ | 保守 ✓ 只统一不重设计 ✓（品牌色/布局不动 ✓） |
| 深色主题漏改 ✗ | 每个组件改完**深浅色都看** ✓ |

## 九、明确不做的（✗）

| 不做 | 理由 |
|---|---|
| 引入 Tailwind/Bootstrap | 需构建链 ✗ 收益低 ✓ |
| 换框架（Vue/React） | 存量 8,594 行原生 DOM ✗ 风险极高 ✗ |
| 大改品牌色/布局 | 用户已习惯 ✓ 只统一不重设计 ✓ |
| 动画花活 | 工具型产品 ✓ 克制 ✓ |
| 换字体（引 webfont） | 离线可用优先 ✓ 用系统字体栈 ✓ |

## 十、建议执行顺序

```
第 1 次（U0+U1 的图标部分，~4 小时）:
  → 收益最直观（表情包没了 ✓）· 风险低 ✓ · 立刻能看到效果 ✓

第 2 次（U1 收尾 + U2 组件统一，~5 小时）:
  → 一致性大幅提升 ✓ 但改动面大 ⚠ 一页一提交 ✓

第 3 次（U3 打磨，~3 小时）:
  → 导航/a11y/响应式 ✓
```

**每阶段结束都能停 ✓ 都能截图给你确认 ✓**
