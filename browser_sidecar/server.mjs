// 浏览器自动化 sidecar（Node ✓ 运行在 Windows ✓ 因为只有 Windows 有 Edge 和 node ✗ WSL 没有 ✗）
//
// 设计依据: docs/BROWSER_EXEC_PLAN.md Step 1
//   · 优先连【用户已开的 Edge】(CDP 9222 ✓ 复用登录态 ✓)；连不上则自己拉起 msedge ✓
//   · 只新开 tab ✓ 绝不碰用户已有标签 ✗
//   · 元素清单只给【可见+可交互】≤80 个 ✓ 用 role+name+ref ✓ 不返回整页 HTML ✗（省 token ✓）
//   · 页面文本包 <<<EXTERNAL_CONTENT>>> 标记 ✓（防提示注入 ✓ 借鉴 Nanobrowser injection-context ✓）
//   · 截图/下载落在本机 shots/ downloads/ ✓ 上传只允许 uploads/ 下的文件 ✓
//
// 运行: node server.mjs            （默认 9223 端口）
// 自检: node server.mjs --selftest （打开 example.com → 快照 → 截图 → 打印结果 ✓）
//
// 依赖: playwright-core（复用 drill 目录的 ✓ 可用 PW_CORE 环境变量指定路径 ✓）
import http from 'node:http';
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 9224);
const CDP = process.env.CDP_ENDPOINT || 'http://127.0.0.1:9222';
const PLAYWRIGHT_CORE = process.env.PW_CORE
  || 'E:/WSL/_check/drill/node_modules/playwright-core';
const SHOT_DIR = process.env.SHOT_DIR || path.join(HERE, 'shots');
const DL_DIR = process.env.DL_DIR || path.join(HERE, 'downloads');
const UL_DIR = process.env.UL_DIR || path.join(HERE, 'uploads');
const MAX_REFS = Number(process.env.MAX_REFS || 80);

// ★ S-2（2026-10-19 安全修复）: 原来 CORS '*' + 无鉴权 ✗
//   ⇒ 用户 Edge 打开任意恶意网页就能 POST 本机 9223 用真实登录态操作浏览器 ✗✗
//   修: ① 共享 token（文件 .sidecar_token ✓ 后端转发给页面 ✓）② Origin 白名单 ✓
const ORIGIN_WHITELIST = new Set(['http://127.0.0.1:8080', 'http://localhost:8080']);
  // ★ 2026-10-23 实测修（token 路径跨 OS 不一致）:
  //   第一次修把 token 写到 os.homedir()/.luxy —— 但 sidecar 跑在 Windows
  //   （C:\Users\<user>），平台跑在 WSL（/home/<user>）⇒ 两个 home 不是同一个地方 ✗
  //   正确做法: token 放【项目目录】browser_sidecar/.sidecar_token
  //     · Windows 侧 node 经 \\wsl.localhost\... 写
  //     · WSL 侧平台经 /home/... 读 → 同一个文件 ✓
  //   TOKEN_FILE 环境变量仍可覆盖（特殊部署）✓
  const LEGACY_TOKEN_FILE = path.join(HERE, '.sidecar_token');
  const TOKEN_FILE = process.env.TOKEN_FILE || LEGACY_TOKEN_FILE;
  let SIDECAR_TOKEN = '';
  try { SIDECAR_TOKEN = fs.readFileSync(TOKEN_FILE, 'utf8').trim(); } catch (e) {}
  if (!SIDECAR_TOKEN) SIDECAR_TOKEN = crypto.randomBytes(24).toString('hex');
  try {
    fs.mkdirSync(path.dirname(TOKEN_FILE), { recursive: true });
    fs.writeFileSync(TOKEN_FILE, SIDECAR_TOKEN, { mode: 0o600 });
    console.log('[sidecar] 访问 token →', TOKEN_FILE);
  } catch (e) {
    console.log('[sidecar] 写 token 失败:', String((e && e.message) || e));
  }
function corsOrigin(req) {
  // ★ 修（2026-10-22 v4.2 测试）: 原来只白名单 127.0.0.1:8080
  //   ⇒ 其他端口（如隔离实例 :8082/:8083）拿到 ACAO:'null' 一律被浏览器拦 ✗
  //   修法: 放行所有 http://127.0.0.1:<port> / http://localhost:<port>
  //   （sidecar 本身仍要求 X-Sidecar-Token，放行 CORS 不等于放行操作 ✓）
  const o = (req && req.headers && req.headers.origin) || '';
  if (/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(o)) return o;
  return 'null';
}
function tokenOk(req, u) {
  const h = (req.headers && (req.headers['x-sidecar-token'] || '')) || '';
  const q = u ? (u.searchParams.get('token') || '') : '';
  return h === SIDECAR_TOKEN || q === SIDECAR_TOKEN;
}

for (const d of [SHOT_DIR, DL_DIR, UL_DIR]) fs.mkdirSync(d, { recursive: true });

// ---- playwright-core 加载（ESM 下从指定路径 require ✓）----
const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright-core'));
  console.log('[sidecar] playwright-core ✓ 本地模块');
} catch (e) {
  try {
    ({ chromium } = require(PLAYWRIGHT_CORE));
    console.log('[sidecar] playwright-core ✓ 从', PLAYWRIGHT_CORE);
  } catch (e2) {
    console.error('[sidecar] ✗ 找不到 playwright-core:', e2.message);
    process.exit(1);
  }
}

// ---- 浏览器会话（单实例 ✓ 串行动作 ✓）----
const state = { browser: null, context: null, page: null, mode: null, target: null };
let queue = Promise.resolve();
const serialize = (fn) => { const p = queue.then(fn, fn); queue = p.catch(() => {}); return p; };

async function ensureBrowser() {
  if (state.browser && state.browser.isConnected()) return;
  // ① 优先连用户的 Edge（复用登录态 ✓）
  try {
    state.browser = await chromium.connectOverCDP(CDP, { timeout: 5000 });
    state.mode = 'cdp';
    attachBrowserWatch();
    // 复用已有 context ✓ 但只开新页 ✓
    state.context = state.browser.contexts()[0] || await state.browser.newContext();
    console.log('[sidecar] ✓ 已连上已运行的浏览器（CDP）');
    return;
  } catch (e) {
    console.log('[sidecar] CDP 连不上 ⇒ 自己拉起 Edge（兜底 ✓）:', String(e.message).slice(0, 80));
  }
  // ② 兜底: 自己拉起 Edge
  const _launchArgs = ['--start-maximized'];
  if (process.env.IGNORE_HTTPS !== '0') _launchArgs.push('--ignore-certificate-errors');
  state.browser = await chromium.launch({ channel: 'msedge', headless: false, args: _launchArgs });
  state.mode = 'launch';
  attachBrowserWatch();
  state.context = await state.browser.newContext({
    viewport: { width: 1440, height: 900 },
    // ★ 修（实测 2026-10-23）：学校等站点证书过期时 page.goto 直接失败
    //   默认关闭校验由 IGNORE_HTTPS=1 控制（本机个人自动化工具 ✓ 默认开启）
    ignoreHTTPSErrors: process.env.IGNORE_HTTPS !== '0',
  });
  console.log('[sidecar] ✓ 已拉起 msedge');
}

// ---- 超时保护（防止 Edge 关闭时挂死整个请求）----
function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label || 'operation'} 超时 ${ms}ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function ensurePage() {
  await ensureBrowser();
  if (state.page && !state.page.isClosed()) return state.page;
  state.page = await state.context.newPage();   // ★ 只新开 ✓
  await state.page.bringToFront().catch(() => {});
  return state.page;
}

// ---- 站点策略（安全 ✓）----
function policyFor(url) {
  const def = { default: 'ask', sites: {} };
  let p = def;
  try { p = { ...def, ...JSON.parse(fs.readFileSync(path.join(HERE, 'policy.json'), 'utf8')) }; } catch (e) {}
  try {
    const host = new URL(url).hostname;
    for (const [k, v] of Object.entries(p.sites || {})) {
      if (host === k || host.endsWith('.' + k)) return v;
    }
  } catch (e) {}
  return p.default;
}

// ---- 快照: 只收可见+可交互 ✓ 给 role+name+ref ✓ ----
const SNAP_FN = (maxRefs) => {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) return false;
    return true;   // ★ P-42（2026-10-19）: 收【全页】元素 ✓ 原来只收视口附近 ✗ ⇒ 下方区块看不到 ✗
  };
  const pick = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') ||
      ({ a: 'link', button: 'button', input: 'input', textarea: 'textbox', select: 'combobox' }[tag] || '');
    const type = (el.getAttribute('type') || '').toLowerCase();
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder ||
      el.getAttribute('title') || el.getAttribute('name') || '').trim().slice(0, 40);
    const isPassword = type === 'password';
    const href = tag === 'a' ? (el.getAttribute('href') || '').slice(0, 120) : '';
    // ★ 语义标注（2026-10-19 实测: 模型把密码框当登录按钮 ✗ 需要明确告诉它 ✓）
    let kind = 'other';
    if (isPassword) kind = 'password';                       // 密码框 ✓
    else if (tag === 'a' && href && !href.startsWith('#') && !href.startsWith('javascript')) kind = 'link';  // 会导航的链接 ✓
    else if ((tag === 'input' && (type === 'submit' || type === 'button')) ||
             (tag === 'button' && (el.type === 'submit' || !el.type)) ||
             (tag === 'input' && type === 'image')) kind = 'submit';   // 提交按钮 ✓
    else if (tag === 'button' || role === 'button') kind = 'button';
    else if (tag === 'input' || tag === 'textarea') kind = 'text';
    else if (tag === 'select') kind = 'select';
    return { tag, role, name, kind, isPassword, href };
  };
  const sels = 'a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true],[onclick]';
  const all = [...document.querySelectorAll(sels)].filter(isVisible);
  const out = [];
  // ★ 关键修复（2026-10-19 实测）: ref 编号必须【稳定】✓
  //   原来每次快照都 removeAttribute + 重新 r1..rN ✗
  //   ⇒ 模型拿上一次的 ref 点新快照 ⇒ 点到别的元素 ✗（实测点错课程 ✓）
  //   现在: 元素已有的 data-ds-ref 保留 ✓ 只有新元素才分配新号 ✓ 下一个计数器全局递增 ✓
  let n = 0;
  for (const el of all) {
    if (out.length >= maxRefs) break;
    const info = pick(el);
    if (!info.name && !info.role) continue;
    let ref = el.getAttribute('data-ds-ref');
    if (!ref) {
      ref = 'r' + (++n);
      el.setAttribute('data-ds-ref', ref);
    }
    out.push({ ref, ...info, value: info.isPassword ? '[password]' : undefined });
  }
  const text = (document.body ? document.body.innerText : '').replace(/\s+\n/g, '\n').trim().slice(0, 12000);   // ★ P-44: 3000→12000 ✓ 原来正文被截断 ✗
  return { url: location.href, title: document.title, refs: out, text };
};

async function doSnapshot() {
  const page = await ensurePage();
  const snap = await page.evaluate(SNAP_FN, MAX_REFS);
  // ★ 外部内容标记（防提示注入 ✓）
  rememberRefs(snap.refs);   // ★ 记住 ref 映射 ✓ 供 ref 过期时找回 ✓
  const wrapped = `<<<EXTERNAL_CONTENT>>>\n${snap.text}\n<<<END_EXTERNAL_CONTENT>>>`;
  // ★ 语义摘要 + 表单提示（2026-10-19: 模型需要明确知道"哪个是密码框/哪个是登录按钮"✓）
  const byKind = (k) => (snap.refs || []).filter(r => r.kind === k);
  const pw = byKind('password');
  const subs = byKind('submit');
  const hints = [];
  if (pw.length && subs.length) {
    hints.push(`【登录表单】密码框: ${pw.map(r => r.ref + '(' + r.name + ')').join(', ')} · ` +
               `提交按钮: ${subs.map(r => r.ref + '(' + r.name + ')').join(', ')} · ` +
               `正确流程: 先 fill 文本框，再点【submit】按钮 ✓ 密码框不要点 ✗`);
  }
  const semantic = {
    password: pw.map(r => r.ref),
    submit: subs.map(r => r.ref),
    links: byKind('link').length,
    text_inputs: byKind('text').length,
    hints,
  };
  return { ok: true, url: snap.url, title: snap.title, refs: snap.refs, text: wrapped,
           refs_count: snap.refs.length, semantic };
}

// ---- ref 记忆（2026-10-19）----
//   ★ 实测事故: 表单提交/点击跳转后 refs 全失效 ✗ 模型再填下一个框就"找不到 ref" ✗
//   ⇒ 记住 ref → {tag, role, name} ✓ 找不到时【自动重新快照】并按 role+name 找回 ✓
const refMemory = new Map();   // ref -> {tag, role, name}
function rememberRefs(refs) {
  refMemory.clear();
  for (const r of (refs || [])) refMemory.set(r.ref, { tag: r.tag, role: r.role, name: r.name, href: r.href || '' });
}
async function locateByRef(ref) {
  const page = await ensurePage();
  let loc = page.locator(`[data-ds-ref="${ref}"]`);
  if (await loc.count()) return loc.first();
  // ② 找不到 ⇒ 重新快照 ✓ 再按 role+name 找回 ✓
  const old = refMemory.get(ref);
  const snap = await doSnapshot();
  rememberRefs(snap.refs);
  if (old) {
    const cand = (snap.refs || []).find(r => r.role === old.role && r.name === old.name);
    if (cand) {
      const l2 = page.locator(`[data-ds-ref="${cand.ref}"]`);
      if (await l2.count()) return l2.first();
    }
  }
  throw new Error(`找不到 ref=${ref}（页面已变化 ✓ 已重新快照 · 新 refs: ` +
    (snap.refs || []).slice(0, 8).map(r => r.ref + ':' + r.name).join(', ') + '）请用新 ref 重试 ✓');
}

// ---- 动作 ----
const ACT = {
  async open(args) {
    const url = String(args.url || '').trim();
    if (!url) throw new Error('缺少 url');
    const pol = policyFor(url);
    if (pol === 'block') throw new Error(`站点被策略禁止（${new URL(url).hostname} ⇒ block ✗）`);
    const page = await ensurePage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(600);
    const snap = await doSnapshot();
    return { ok: true, policy: pol, title: snap.title, url: snap.url, refs: snap.refs,
             refs_count: snap.refs_count, semantic: snap.semantic };
  },
  async snapshot() { return doSnapshot(); },
  async click(args) {
    const ref = String(args.ref || '');
    if (!ref) throw new Error('缺少 ref（先 snapshot 拿 ref ✓）');
    const page = await ensurePage();
    const loc = await locateByRef(ref);
    const before = page.url();
    await loc.click({ timeout: 15000 });
    await page.waitForTimeout(900);
    let urlChanged = page.url() !== before;
    // ★ 2026-10-19 实测: 菜单/父级 <a> 点击常常不导航 ✗（Drupal 响应式菜单 ✓）
    //   ⇒ 若没跳转 且 该元素有 href ⇒ 【自动 fallback 直接 goto href】✓✓
    //   这样模型点"Home"即使菜单失效也真能回到首页 ✓
    let viaHref = false;
    if (!urlChanged) {
      const info = refMemory.get(ref);
      const href = info && info.href;
      if (href && !href.startsWith('#') && !href.startsWith('javascript')) {
        try {
          const target = href.startsWith('http') ? href : new URL(href, page.url()).toString();
          await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 30000 });
          await page.waitForTimeout(700);
          urlChanged = page.url() !== before;
          viaHref = true;
        } catch (e) {}
      }
    }
    const st = { ok: true, url_changed: urlChanged, via_href: viaHref, url: page.url(), title: await page.title() };
    try { const snap = await doSnapshot(); st.refs = snap.refs; st.refs_count = snap.refs_count; } catch (e) {}
    return st;
  },
  async fill(args) {
    const ref = String(args.ref || '');
    if (!ref) throw new Error('缺少 ref');
    if (args.ref && /password/i.test(String(args.text || ''))) { /* 不拦 ✓ 但日志不落 */ }
    const page = await ensurePage();
    const loc = await locateByRef(ref);
    await loc.fill(String(args.text ?? ''), { timeout: 15000 });
    // ★ 2026-10-19 实测事故: 模型给用户名框传 submit:true ⇒ 回车提交 ⇒ 页面跳转 ⇒ 密码框 ref 失效 ✗
    //   ⇒ 不再自动提交 ✗ 提交必须靠【显式 browser_click 提交按钮】✓
    //   （若用户真的要回车，另有 browser_press 类动作，但表单登录一律走点击 ✓）
    const st = { ok: true, submitted: false };
    try {
      // 回填最新 refs（页面可能已变 ✓ 让模型拿到新鲜清单 ✓）
      const snap = await doSnapshot();
      st.url = snap.url; st.title = snap.title; st.refs = snap.refs; st.refs_count = snap.refs_count;
    } catch (e) {}
    return st;
  },
  async read(args) {
    const page = await ensurePage();
    let text = '';
    if (args.ref) {
      text = await page.locator(`[data-ds-ref="${args.ref}"]`).first().innerText({ timeout: 10000 }).catch(() => '');
    } else if (args.selector) {
      text = await page.locator(String(args.selector)).first().innerText({ timeout: 10000 }).catch(() => '');
    } else {
      text = await page.evaluate(() => document.body ? document.body.innerText : '');
    }
    const tables = await page.evaluate(() => [...document.querySelectorAll('table')].slice(0, 3).map(tb =>
      [...tb.querySelectorAll('tr')].slice(0, 20).map(tr =>
        [...tr.querySelectorAll('th,td')].map(td => (td.innerText || '').trim().slice(0, 60)))));
    return { ok: true, text: `<<<EXTERNAL_CONTENT>>>\n${String(text).slice(0, 8000)}\n<<<END_EXTERNAL_CONTENT>>>`, tables };
  },
  async screenshot(args) {
    const page = await ensurePage();
    const f = path.join(SHOT_DIR, `shot_${Date.now()}.png`);
    await page.screenshot({ path: f, fullPage: !!args.fullPage });
    // ★ 附带 HTTP 预览地址 ✓（页面 http:// 无法直接读 file:// ✗）
    return { ok: true, path: f, bytes: fs.statSync(f).size,
             url: `http://127.0.0.1:${PORT}/file?name=${encodeURIComponent(path.basename(f))}` };
  },
  async download(args) {
    const page = await ensurePage();
    if (args.url) {
      const resp = await page.request.get(String(args.url), { timeout: 60000 });
      if (!resp.ok()) throw new Error(`下载失败 HTTP ${resp.status()}`);
      const f = path.join(DL_DIR, `dl_${Date.now()}_${(String(args.url).split('/').pop() || 'file').slice(0, 40)}`);
      fs.writeFileSync(f, await resp.body());
      return { ok: true, path: f, bytes: fs.statSync(f).size };
    }
    const ref = String(args.ref || '');
    if (!ref) throw new Error('需要 ref 或 url');
    const dlPromise = page.waitForEvent('download', { timeout: 60000 });
    await page.locator(`[data-ds-ref="${ref}"]`).first().click({ timeout: 15000 });
    const dl = await dlPromise;
    const f = path.join(DL_DIR, dl.suggestedFilename() || `dl_${Date.now()}`);
    await dl.saveAs(f);
    return { ok: true, path: f, bytes: fs.statSync(f).size };
  },
  async upload(args) {
    const ref = String(args.ref || '');
    const p = String(args.path || '');
    if (!ref || !p) throw new Error('需要 ref 与 path');
    // ★ 安全: 只允许 uploads 目录下的文件 ✓
    const abs = path.resolve(p);
    if (!abs.startsWith(path.resolve(UL_DIR))) throw new Error(`拒绝：上传文件必须在 ${UL_DIR} 下 ✗`);
    const page = await ensurePage();
    await page.locator(`[data-ds-ref="${ref}"]`).first().setInputFiles(abs, { timeout: 20000 });
    return { ok: true, path: abs };
  },
  // ★ P-43（2026-10-19）: 缺 scroll ✗ ⇒ 模型说"滚动到下方"却无工具可用 ✗
  async scroll(args) {
    const page = await ensurePage();
    const dir = String(args.dir || 'down');
    const px = Math.min(5000, Math.abs(Number(args.px || 800)));
    const before = await page.evaluate(() => window.scrollY);
    if (dir === 'top') await page.evaluate(() => window.scrollTo(0, 0));
    else if (dir === 'bottom') await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    else await page.evaluate((dy) => window.scrollBy(0, dy), dir === 'up' ? -px : px);
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => window.scrollY);
    const st = { ok: true, dir, scrolled: after - before, scrollY: after };
    try { const snap = await doSnapshot(); st.refs = snap.refs; st.refs_count = snap.refs_count; st.semantic = snap.semantic; } catch (e) {}
    return st;
  },
  async move(args) {
    const page = await ensurePage();
    await page.mouse.move(Number(args.x || 0), Number(args.y || 0), { steps: 8 });
    return { ok: true, x: Number(args.x || 0), y: Number(args.y || 0) };
  },
  async wait(args) {
    const page = await ensurePage();
    if (args.text) await page.getByText(String(args.text), { exact: false }).first().waitFor({ timeout: 30000 });
    else await page.waitForTimeout(Math.min(60000, Number(args.ms || 1000)));
    return { ok: true };
  },
  async tabs(args) {
    const pages = state.context ? state.context.pages() : [];
    const action = String(args.action || 'list');
    if (action === 'list') return { ok: true, tabs: pages.map((p, i) => ({ i, url: p.url().slice(0, 120) })) };
    if (action === 'switch') {
      const i = Number(args.index || 0);
      if (!pages[i]) throw new Error('标签页不存在');
      state.page = pages[i];
      await state.page.bringToFront();
      return { ok: true, url: state.page.url() };
    }
    if (action === 'close') {
      const i = Number(args.index ?? -1);
      const target = i >= 0 ? pages[i] : state.page;
      if (target) await target.close();
      state.page = null;
      return { ok: true };
    }
    throw new Error('未知 action');
  },
  async close() {
    // ★ 修（实测 2026-10-23）：原实现直接 await browser.close()，
    //   Edge 有 beforeunload/恢复提示时可能挂死 ⇒ 平台侧 90s 超时 ✗
    //   现在每步都带超时，超时也强制清空状态并返回 ok ✓
    const notes = [];
    if (state.page && !state.page.isClosed()) {
      try { await withTimeout(state.page.close(), 5000, 'page.close'); }
      catch (e) { notes.push('page.close: ' + String(e.message).slice(0, 60)); }
    }
    if (state.mode === 'launch' && state.browser) {
      try { await withTimeout(state.browser.close(), 8000, 'browser.close'); }
      catch (e) { notes.push('browser.close: ' + String(e.message).slice(0, 60)); }
    }
    // CDP 模式不动用户的浏览器 ✓
    const b = state.browser;
    state.page = null; state.browser = null; state.context = null; state.mode = null;
    return { ok: true, forced: notes.length > 0, notes };
  },
};

// ---- HTTP 服务 ----
function json(res, code, obj) {
  const body = JSON.stringify(obj, null, 1);
  const _o = (res && res.__cors) || 'null';
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': _o,
    'Vary': 'Origin',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, X-Sidecar-Token',
    'Access-Control-Max-Age': '600',
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const route = u.pathname;
  // ★ S-2: CORS 上下文 + 鉴权（/health 与 OPTIONS 放行 ✓ 其余必须 token ✓）
  const __orig = corsOrigin(req);
  res.__cors = __orig;
  try {
    if (req.method === 'OPTIONS') {
      res.writeHead(200, {
        'Access-Control-Allow-Origin': __orig || 'null',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Sidecar-Token',
        'Access-Control-Max-Age': '600',
        'Vary': 'Origin',
      });
      return res.end('{}');
    }
    if (route !== '/health' && route !== '/actions' && !tokenOk(req, u)) {
      return json(res, 401, { ok: false, error: '未授权（缺少/错误 X-Sidecar-Token ✗）' });
    }
    if (route === '/health') {
      return json(res, 200, { ok: true, connected: !!(state.browser && state.browser.isConnected()),
                              mode: state.mode, page: state.page ? state.page.url() : null, port: PORT });
    }
    if (route === '/actions') return json(res, 200, { ok: true, actions: Object.keys(ACT) });
    // ★ 文件服务（2026-10-19）: 截图预览用 ✓
    //   为什么需要: 页面是 http:// ✓ 拿不到本地 file:// 路径 ✗ ⇒ 必须走 HTTP ✓
    //   安全: 只允许 shots/ 下的文件名（basename ✓ 防路径穿越 ✗）
    if (route === '/file' && req.method === 'GET') {
      const name = path.basename(u.searchParams.get('name') || '');
      const f = path.join(SHOT_DIR, name);
      if (!name || !fs.existsSync(f)) return json(res, 404, { ok: false, error: '文件不存在' });
      const ext = path.extname(f).toLowerCase();
      const ct = ext === '.png' ? 'image/png' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': ct, 'Access-Control-Allow-Origin': corsOrigin(req) || 'null', 'Vary': 'Origin', 'Cache-Control': 'no-store' });
      fs.createReadStream(f).pipe(res);
      return;
    }
    // POST /act/<name>  {args}
    if (req.method === 'POST' && route.startsWith('/act/')) {
      // ★ 兼容两种命名（browser_open / open ✓ 实测前端会带前缀 ✗）
      const raw = route.slice(5);
      const name = raw.replace(/^browser_/, '');
      const fn = ACT[name];
      if (!fn) return json(res, 404, { ok: false, error: `未知动作 ${name}（可用: ${Object.keys(ACT)}）` });
      let body = '';
      for await (const chunk of req) body += chunk;
      let args = {};
      try { args = body ? JSON.parse(body) : {}; } catch (e) { return json(res, 400, { ok: false, error: 'body 不是 JSON' }); }
      const out = await serialize(() => fn(args || {}));
      return json(res, 200, { ok: true, ...out });
    }
    return json(res, 404, { ok: false, error: 'not found' });
  } catch (e) {
    return json(res, 200, { ok: false, error: `${e && e.message ? e.message : e}` });   // 永远 200 ✓ 让模型看到原因 ✓
  }
});

// ★ P-57（2026-10-19 实测）: sidecar 反复无声退出 ✗
//   根因: Edge 被关闭 / CDP 断开 / 异步操作抛错 ⇒ unhandledRejection ⇒ node 直接退出 ✗✗
//   修: ① 兜住 unhandledRejection/uncaughtException ✓ 只记日志不退出 ✓
//      ② browser 断开时清理引用 ✓ 下次请求自动重连 ✓
process.on('unhandledRejection', (e) => {
  console.error('[sidecar] ⚠ 未处理的拒绝（已兜住 ✓ 不退出）:', e && e.message ? e.message : e);
});
process.on('uncaughtException', (e) => {
  console.error('[sidecar] ⚠ 未捕获异常（已兜住 ✓ 不退出）:', e && e.message ? e.message : e);
});
function attachBrowserWatch() {
  try {
    if (state.browser && state.browser.on) {
      state.browser.on('disconnected', () => {
        console.log('[sidecar] 浏览器已断开 ⇒ 清理引用（下次请求自动重连 ✓）');
        state.browser = null; state.context = null; state.page = null; state.mode = null;
      });
    }
  } catch (e) {}
}

// ---- 自检 ----
async function selftest() {
  console.log('[selftest] 打开 example.com …');
  const opened = await ACT.open({ url: 'https://example.com' });
  console.log('[selftest] title =', opened.title, '· refs =', opened.refs_count);
  const shot = await ACT.screenshot({});
  console.log('[selftest] 截图 =', shot.path, '·', shot.bytes, 'bytes');
  const snap = await ACT.snapshot();
  console.log('[selftest] 快照前 120 字:', JSON.stringify(snap.text).slice(0, 160));
  await ACT.close();
  console.log('[selftest] ✓ 通过');
}

if (process.argv.includes('--selftest')) {
  selftest().then(() => process.exit(0)).catch(e => { console.error('[selftest] ✗', e); process.exit(1); });
} else {
  // ★ 绑定 127.0.0.1（安全 ✓）: 只有本机进程能访问
  //   前端页面【跑在 Windows 的浏览器里】⇒ 它能直连 127.0.0.1:9223 ✓
  //   后端在 WSL 里 ✗ 连不到（WSL2↔Windows 边界 + 防火墙 ✗）⇒ 前端直连是正解 ✓
  // ★ 修（2026-10-21）: 原来写死 '127.0.0.1' ⇒ WSL 里的后端永远连不上 ✗
  //   实测: WSL→172.20.64.1:9224 / 127.0.0.1:9224 全部 000（连接失败）
  //   修法: HOST 环境变量可配（默认仍 127.0.0.1，安全 ✓）
  //         需 WSL 访问时: set HOST=0.0.0.0
  const HOST = process.env.HOST || '127.0.0.1';
  server.listen(PORT, HOST, () => {
    console.log(`[sidecar] 浏览器自动化 sidecar 已启动: http://${HOST}:${PORT}`);
    console.log(`[sidecar] 连接目标: ${CDP}（失败则拉起 msedge ✓）· 截图: ${SHOT_DIR}`);
  });

// ★ 2026-10-23 实测修：端口被占用时必须退出，否则会留下"僵尸实例" ✗
//   现象: 计划任务重复启动 → 第二个实例写了自己的 token 后端口绑定失败，
//         但进程不退出 ⇒ 文件里的 token 与实际服务的 token 不一致，
//         平台调用全部 401（实测踩到）
server.on('error', (e) => {
  if (e && e.code === 'EADDRINUSE') {
    console.error('[sidecar] ✗ 端口 ' + PORT + ' 已被占用 ⇒ 本进程退出（避免 token 不一致）');
  } else {
    console.error('[sidecar] ✗ 服务错误:', String((e && e.message) || e));
  }
  process.exit(1);
});

}
