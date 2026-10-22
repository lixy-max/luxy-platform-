# 发布包 v3.2（`v0.3.6`）· 2026-10-19

> 本版 = **界面美化全套改造**（U0~U3）的集成发布
> 产物：`release/paper-lora-workbench-v0.3.6.zip`（1.56 MB · 290 文件 · 闸门通过 ✓）
> 备份：`D:\paper-lora-backups\paper-lora-workbench-v0.3.6_2026-10-19.zip`（md5 校验 ✓）

## 一、本版核心：界面美化（相对 v3.1）

### 🎨 图标系统（U0 · 表情包全面替换 ✓）
| 项 | 内容 |
|---|---|
| 图标库 | **Lucide**（ISC 许可 ✓ 可商用 ✓）· **128 图标** · SVG sprite **28 KB** |
| 引入方式 | `webui/vendor/icons.svg`（`<use href>` ✓ 零 JS 依赖 ✓ 离线 ✓） |
| 颜色适配 | `currentColor` ✓ 深浅主题自动适配 ✓ |
| 许可文件 | `webui/vendor/LICENSE-lucide.txt` ✓（合规 ✓） |
| 替换数量 | **257 处** UI 表情 → 图标 ✓（HTML 222 · JS 18 · pages 17） |

### 🧩 组件层（U2）
| 项 | 内容 |
|---|---|
| 组件类 | `.btn`（统一 7×14px/14px ✓）· `.card` · `.badge`（4 色）· `.table` · `.input/.select/.textarea` · `.empty` · `.spinner` |
| 工具类 | **33 个**（`.text-sm/.text-xs/.text-muted/.hidden/.flex/.gap-*/.mt-*` …） |
| 设计 token | 补齐 **13 个**（`--fs-*` `--sh-*` `--tr-*` `--r-full` 等 ✓）· 未定义引用 **0** ✓ |
| 统一改造 | 5 张卡片 → `.card` ✓ · **66 个表单控件**（48 input + 18 select）加类 ✓ · 38 处内联样式清理 ✓ |

### ♿ 无障碍（U3-a11y · 从 0 到全覆盖 ✓）
| 项 | 前 | 后 |
|---|---|---|
| `aria-hidden`（装饰图标） | 0 | **217** ✓ |
| `aria-label`（图标按钮） | 0 | **4** ✓ |
| `role=tab/tablist/tabpanel` | 0 | **17 / 1 / 10** ✓ |
| `aria-live`（toast） | 0 | **1** ✓ |
| 键盘操作 | 不可用 | **Enter/Space 切 tab** ✓ |
| 焦点样式 | 无 | `:focus-visible` 蓝环 ✓ |

### 📱 响应式 + 深色（U3-A+B）
- **6 个断点**（1100 / 900 / 760 / 600 / reduced-motion ✓）
- 三档实测**无横向溢出** ✓（desktop 1500 / tablet 1000 / mobile 600 ✓）
- 移动端字号 16px（防 iOS 缩放 ✓）· 导航自动换行 ✓ · 表格横向滚动 ✓
- 深色主题：新组件（card/input/table/empty）补漏 ✓
- `prefers-reduced-motion` ✓（尊重系统动效偏好 ✓）

## 二、新环境验证（干净目录 ✓ 备用端口 ✓ 未占 GPU ✓）

```
解压: 290 文件 ✓
测试: 828 tests OK (skipped=1) ✓（干净环境 ⇒ 包自洽 ✓）
启动: bash scripts/start.sh --no-gpu（BACKEND_PORT=18001 FRONTEND_PORT=18080）
  后端 2 秒就绪 ✓
  /health {"status":"ok"} ✓ · index3.html 200 ✓
  图标 sprite: 200 · 28 KB · 128 symbols ✓
  pages/kb.js: 200 · 11 KB ✓
  aria 属性: 255 ✓ · @media 断点: 6 ✓
清理: 测试实例全停 ✓ · 主服务未受影响 ✓
```

## 三、⚠ 本版修掉的一个自己引入的问题

```
我在 U3-A 里给深色 CSS 写了裸 hex（#17191d 等 3 处 ✗）
⇒ 被项目自己的守护测试抓到（test_palette_colors_are_not_reintroduced_as_raw_hex ✓）
⇒ 已移除 ✓ 改回纯 var(--c-dark-*) ✓ 后端 828 OK ✓
教训: 改 CSS 后必须跑【后端】测试（不只是前端断言 ✗）—— 我又漏了一次 ✓ 已改正 ✓
```

## 四、启动方式（同 v3.1）

```bash
bash scripts/setup.sh && bash scripts/run.sh
# 或
bash scripts/start.sh            # 含推理底座（需 GPU ✓）
bash scripts/start.sh --no-gpu   # 仅 KB/规则模式（不占 GPU ✓）
```
**端口可覆盖**：`BACKEND_PORT` / `FRONTEND_PORT` / `VLLM_PORT` ✓
**局域网**：需显式 `WORKBENCH_HOST=0.0.0.0` ✓（默认仅本机 ✓）

## 五、已知限制

1. **导航仍是 12 个 tab 的二级结构** ⚠（侧边栏重组进行中 ⏳）
2. **agent-module 未拆分**（3,600 行 IIFE ✗ 计划见 `docs/AGENT_MODULE_SPLIT_PLAN.md`）
3. **browser_page.js 里 13 处技能图标仍是 emoji** ⚠（数据形式 ✓ 需单独改造 ✓）
4. **fetch 迁移 28/164** ⚠（其余按页逐步 ✓）

## 六、备份

```
D:\paper-lora-backups\
  ├─ paper-lora-workbench-v0.3.6_2026-10-19.zip   （1.56 MB · md5 校验 ✓）
  └─ docs_2026-10-19\                             （关键文档 ✓）
```
