#!/usr/bin/env node
// 前端 E2E 冒烟测试（Phase 3-1 · 2026-10-19）
//
// ★ 设计红线（用户明确要求注意显存 ✓）：
//    · headless Chromium + `--disable-gpu` ⇒ **零显存占用** ✓
//    · **不调用任何 LLM** ✗（vLLM 正被跑批占用 ✓ 显存只剩 ~676MB ✗）
//    · **不触发浏览器 sidecar** ✗（Edge 硬件加速可能占显存 → 可能 OOM vLLM ✗）
//    ⇒ 只测纯前端 UI/渲染/路由/XSS ✓
//
// 用法:
//   node tools/e2e_smoke.mjs                 # 跑全部
//   node tools/e2e_smoke.mjs --json out.json # 结果写 JSON
//
// 依赖: playwright-core（复用 E:\WSL\_check\drill\node_modules ✓ 不用 npm install ✓）
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const PW_CORE = process.env.PW_CORE || 'E:/WSL/_check/drill/node_modules/playwright-core';
const { chromium } = require(PW_CORE);

const BASE = process.env.FRONTEND_BASE || 'http://127.0.0.1:8080';
const API = process.env.API_BASE || 'http://127.0.0.1:8001';
const STAMP = Date.now();

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail == null ? '' : String(detail).slice(0, 300) });
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${detail ? '  -> ' + String(detail).slice(0, 160) : ''}`);
}

async function main() {
  console.log(`[e2e] 启动 headless Chromium（--disable-gpu ✓ 零显存 ✓）`);
  // ★ 版本不匹配修复（2026-10-19）: playwright-core 要 headless_shell-1248 ✗ 本机只有 1243 ✓
  //   ⇒ 显式指定本机已装的可执行文件 ✓（仍然是 headless +++ --disable-gpu ⇒ 零显存 ✓）
  const CANDIDATES = [
    process.env.PW_EXE,
    'C:/Users/A/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe',
    'C:/Users/A/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe',
    'C:/Users/A/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe',
  ].filter(Boolean);
  let exe = null;
  for (const c of CANDIDATES) { try { if (fs.existsSync(c)) { exe = c; break; } } catch (e) {} }
  console.log(`[e2e] 可执行: ${exe || '(默认)'}`);
  const browser = await chromium.launch({
    headless: true,
    executablePath: exe || undefined,
    args: ['--disable-gpu', '--disable-software-rasterizer', '--no-sandbox',
           '--disable-dev-shm-usage', '--disable-accelerated-2d-canvas'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 200)));

  // ---- 1. 首屏加载 ----
  const t0 = Date.now();
  await page.goto(`${BASE}/index3.html?t=${STAMP}#/chat`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);
  const loadMs = Date.now() - t0;
  check('e2e-01-page-load', (await page.title()).includes('LoRA'), `title=${await page.title()} · ${loadMs}ms`);
  // ★ 2026-10-19: vLLM 关闭时页面会请求 vllm 相关端点 ⇒ 400/502 噪音 ✗ 不是前端 bug ✓
  //   只把「真正的 JS 错误」当失败 ✓（资源加载失败/HTTP 状态噪音只记录 ✓）
  const realErrors = consoleErrors.filter(t =>
    !/Failed to load resource|status of (400|404|500|502|503)/i.test(String(t)));
  check('e2e-02-no-console-errors', realErrors.length === 0,
        (realErrors.slice(0, 3).join(' | ') || '无 JS 错误 ✓') +
        (consoleErrors.length ? `（忽略 ${consoleErrors.length} 条资源/HTTP 噪音 ✓）` : ''));

  // ---- 3. 12 个页面容器存在 ----
  const pages = await page.evaluate(() => [...document.querySelectorAll('.page')].map(p => p.id));
  check('e2e-03-pages-exist', pages.length >= 12, `${pages.length} 个: ${pages.slice(0, 6).join(',')}…`);

  // ---- 4. hash 路由切换（抽 6 页）----
  const routes = ['#/chat', '#/tools/kb', '#/tools/train', '#/tools/history', '#/tools/settings', '#/tools/browser'];
  let routeOk = 0;
  const routeDetail = [];
  for (const r of routes) {
    try {
      await page.goto(`${BASE}/index3.html?t=${STAMP}#x${Math.random()}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await page.evaluate((hash) => { location.hash = hash; }, r);
      await page.waitForTimeout(500);
      const vis = await page.evaluate((id) => {
        const el = document.getElementById(id);
        if (!el) return false;
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden';
      }, 'page-' + (r.split('/').pop()));
      if (vis) routeOk++;
      else routeDetail.push(r);
    } catch (e) { routeDetail.push(r + ':' + String(e).slice(0, 40)); }
  }
  check('e2e-04-hash-routing', routeOk === routes.length, `${routeOk}/${routes.length}${routeDetail.length ? ' 失败: ' + routeDetail.join(',') : ''}`);

  // ---- 5. markdown 渲染（正常内容 ✓）----
  const md = await page.evaluate(() => {
    if (typeof window.renderMarkdown !== 'function') return null;
    return window.renderMarkdown('**加粗** 和 `代码`\n\n| a | b |\n|---|---|\n| 1 | 2 |');
  });
  check('e2e-05-markdown-render', md && md.includes('<strong>') && md.includes('<code>') && md.includes('<table'),
        md ? md.slice(0, 120) : 'renderMarkdown 不存在');

  // ---- 6. XSS 拦截（4 载荷 ✓）----
  const xss = await page.evaluate(() => {
    if (typeof window.renderMarkdown !== 'function') return null;
    const payloads = [
      '<img src=x onerror="window.__E2E_X1=1">',
      '<script>window.__E2E_X2=1</script>',
      '<a href="javascript:window.__E2E_X3=1">c</a>',
      '<svg onload="window.__E2E_X4=1"></svg>',
    ];
    const out = payloads.map(p => {
      const h = window.renderMarkdown(p);
      return { hasScript: /<script/i.test(h), hasOn: /onerror|onload/i.test(h), hasJs: /javascript:/i.test(h) };
    });
    return { out, flags: [!!window.__E2E_X1, !!window.__E2E_X2, !!window.__E2E_X3, !!window.__E2E_X4] };
  });
  const xssClean = xss && xss.out.every(o => !o.hasScript && !o.hasOn && !o.hasJs) && xss.flags.every(f => !f);
  check('e2e-06-xss-blocked', xssClean, xss ? JSON.stringify(xss).slice(0, 200) : '无法测试');

  // ---- 7. 导航 tab 可点（工具组）----
  let navOk = false;
  try {
    await page.goto(`${BASE}/index3.html?t=${STAMP}#/chat`, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForTimeout(800);
    await page.locator('#tab-tools').click({ timeout: 5000 });
    await page.waitForTimeout(600);
    navOk = await page.locator('#tools-subnav').isVisible().catch(() => false);
  } catch (e) { /* ignore */ }
  check('e2e-07-nav-tools', navOk, navOk ? '工具二级导航可见 ✓' : '点击工具 tab 后二级导航不可见');

  // ---- 8. 后端连通（页面上下文 fetch，零 LLM ✓）----
  const api = await page.evaluate(async (apiBase) => {
    try {
      const r = await fetch(apiBase + '/docs/tool/list', { cache: 'no-store' });
      const d = await r.json();
      return { ok: r.ok, count: d.count, hasBrowser: (d.tools || []).some(t => t.name === 'browser_open') };
    } catch (e) { return { ok: false, error: String(e).slice(0, 80) }; }
  }, API);
  check('e2e-08-api-tools', api.ok && api.count >= 48 && api.hasBrowser, JSON.stringify(api));

  // ---- 9. 浏览自动化页 UI（★ 只查元素存在，不触发任何浏览器动作 ✗）----
  await page.goto(`${BASE}/index3.html?t=${STAMP}#/tools/browser`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(3000);
  const brUI = await page.evaluate(() => ({
    page: !!document.getElementById('page-browser'),
    model: !!document.getElementById('br-model'),
    url: !!document.getElementById('br-url'),
    skills: (document.getElementById('br-skills') || {}).children ? document.getElementById('br-skills').children.length : -1,
    messages: !!document.getElementById('br-messages'),
    shot: !!document.getElementById('br-shot'),
    status: (document.getElementById('br-status') || {}).textContent || '',
  }));
  check('e2e-09-browser-page-ui', brUI.page && brUI.model && brUI.url && brUI.skills === 7 && brUI.messages,
        JSON.stringify(brUI).slice(0, 220));

  // ---- 10. 主题切换 + localStorage ----
  const theme = await page.evaluate(() => {
    const before = document.documentElement.getAttribute('data-theme') || '';
    try { if (typeof toggleTheme === 'function') toggleTheme(); } catch (e) { return { ok: false, err: String(e).slice(0, 80) }; }
    const after = document.documentElement.getAttribute('data-theme') || '';
    try { if (typeof toggleTheme === 'function') toggleTheme(); } catch (e) {}
    return { ok: true, before, after };
  });
  check('e2e-10-theme-toggle', theme.ok && theme.before !== theme.after, JSON.stringify(theme));

  // ---- 汇总 ----
  const pass = results.filter(r => r.ok).length;
  console.log(`\n[e2e] ${pass}/${results.length} 通过`);
  const outJson = { at: new Date().toISOString(), base: BASE, results, consoleErrors: consoleErrors.slice(0, 10) };
  const jsonIdx = process.argv.indexOf('--json');
  if (jsonIdx > 0 && process.argv[jsonIdx + 1]) {
    fs.writeFileSync(process.argv[jsonIdx + 1], JSON.stringify(outJson, null, 1));
    console.log(`[e2e] JSON → ${process.argv[jsonIdx + 1]}`);
  }
  await browser.close();
  process.exit(pass === results.length ? 0 : 1);
}

main().catch(e => { console.error('[e2e] 崩溃:', e); process.exit(2); });
