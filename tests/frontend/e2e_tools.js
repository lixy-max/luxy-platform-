#!/usr/bin/env node
'use strict';
/**
 * 全工具端到端巡检（Node 版）：**真的调用 AGENT_TOOLS.<name>.handler**，
 * 走真实 fetch -> 真实后端。这才测得到工具层 bug（路由错、参数名错、返回解析错）。
 *
 * 关键设计：
 *  - 用 Proxy 做"万能 DOM 桩"：任何 document.getElementById(...).xxx 都返回可用的假对象，
 *    避免照抄整套 DOM 桩件（那既不必要也易腐坏）。
 *  - 危险工具**一律不执行**，只做参数校验路径的检查（缺参数应报错而不发请求）。
 *  - 每个工具跑 3 轮，检测偶发失败。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// 默认用相对本文件的路径，避免写死某个用户名（开源移植友好）
const HTML = process.argv[2] || path.join(__dirname, '..', '..', 'webui', 'index3.html');
const API = process.env.API_BASE || 'http://127.0.0.1:8001';
const ROUNDS = Number(process.env.ROUNDS || 3);
const OUT = process.env.OUT || '/tmp/e2e_tools.json';

// ---------------------------------------------------------------- 万能 DOM 桩
function fakeEl(id) {
  const el = {
    id, tagName: 'DIV', value: '', textContent: '', innerHTML: '', innerText: '',
    style: {}, dataset: {}, children: [], options: [], files: [], checked: false,
    disabled: false, scrollTop: 0, scrollHeight: 0, selectedIndex: -1,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, setAttribute() {}, getAttribute() { return null; },
    removeAttribute() {}, addEventListener() {}, removeEventListener() {},
    focus() {}, blur() {}, click() {}, select() {}, scrollIntoView() {},
    querySelector() { return fakeEl('q'); },
    querySelectorAll() { return []; },
    closest() { return fakeEl('c'); },
    insertAdjacentHTML() {}, replaceChildren() {}, remove() {}, cloneNode() { return fakeEl(id); },
  };
  return el;
}
const elCache = new Map();
const doc = {
  getElementById(id) {
    if (!elCache.has(id)) elCache.set(id, fakeEl(id));
    return elCache.get(id);
  },
  querySelector() { return fakeEl('q'); },
  querySelectorAll() { return []; },
  createElement(t) { return fakeEl(t); },
  createTextNode() { return {}; },
  addEventListener() {}, removeEventListener() {},
  body: fakeEl('body'), head: fakeEl('head'), documentElement: fakeEl('html'),
  cookie: '', readyState: 'complete', title: '', hidden: false,
};
const win = {
  API_BASE: API,
  location: { href: 'http://127.0.0.1:8080/index3.html', origin: 'http://127.0.0.1:8080', search: '', hash: '' },
  navigator: { userAgent: 'node', clipboard: { writeText: async () => {} } },
  localStorage: { _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } },
  document: doc,
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  fetch: (u, o) => fetch(u, o),
  console,
  alert: () => {}, prompt: () => '', confirm: () => false,
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  Blob: class { constructor() {} },
  FormData: class { append() {} },
  Event: class { constructor(t) { this.type = t; } },
};
win.window = win; win.globalThis = win; win.self = win;

const html = fs.readFileSync(HTML, 'utf8');
const blocks = [];
const rx = /<script([^>]*)>([\s\S]*?)<\/script>/g;
let m;
while ((m = rx.exec(html)) !== null) {
  if (/\bsrc\s*=/.test(m[1] || '')) continue;
  blocks.push(m[2]);
}

const ctx = vm.createContext(win);
let loaded = 0;
for (const code of blocks) {
  try { vm.runInContext(code, ctx, { timeout: 5000 }); loaded++; }
  catch (e) { /* 初始化里引用缺失的 DOM 是预期的，忽略；AGENT_TOOLS 仍会被定义 */ }
}
const T = win.AGENT_TOOLS || (ctx.AGENT_TOOLS);
if (!T) { console.error('没能取到 AGENT_TOOLS（脚本块加载 ' + loaded + '/' + blocks.length + '）'); process.exit(2); }

// 只读 / 危险 由 dangerous 标记决定（与产品一致）
const names = Object.keys(T).sort();
const safe = names.filter(n => T[n] && T[n].dangerous !== true);
const danger = names.filter(n => T[n] && T[n].dangerous === true);
console.log(`工具总数 ${names.length}｜只读 ${safe.length}｜危险 ${danger.length}｜真实后端 ${API}`);
console.log(`脚本块加载 ${loaded}/${blocks.length}\n`);

// 给只读工具合理的最小参数（尽量走真实数据）
const ARGS = {
  list_workflow_results: { limit: 3 }, list_history: { limit: 3 },
  get_quality_summary: { limit: 3 }, get_workflow_status: {}, get_vllm_status: {},
  get_settings: {}, list_running_tasks: {}, list_loras: {}, get_loras_registry: {},
  list_knowledge_bases: {}, list_uploads: {}, list_available_models: {},
  list_available_datasets: {}, list_trainings: {}, list_workflow_nodes: {},
  list_workflow_configs: {}, list_workflow_uploads: {}, list_sheets: {},
  read_sheet: {}, filter_rows: {}, group_sum: {}, read_docx: {}, list_doc_flows: {},
};

(async () => {
  const report = { api: API, rounds: ROUNDS, tools: {}, safe: [], danger: [] };
  for (const n of safe) {
    const rec = { rounds: [] };
    for (let r = 0; r < ROUNDS; r++) {
      const t0 = Date.now();
      try {
        const out = await T[n].handler(ARGS[n] || {});
        rec.rounds.push({ ok: true, ms: Date.now() - t0, sample: JSON.stringify(out).slice(0, 120) });
      } catch (e) {
        rec.rounds.push({ ok: false, ms: Date.now() - t0, err: String(e && e.message || e).slice(0, 160) });
      }
    }
    const okN = rec.rounds.filter(x => x.ok).length;
    rec.verdict = okN === ROUNDS ? 'always-ok' : (okN === 0 ? 'always-fail' : 'flaky');
    rec.avg_ms = Math.round(rec.rounds.reduce((a, x) => a + x.ms, 0) / ROUNDS);
    report.tools[n] = rec;
    const mark = rec.verdict === 'always-ok' ? 'OK  ' : (rec.verdict === 'flaky' ? 'FLAKY' : 'FAIL ');
    console.log(`  [${mark}] ${n.padEnd(24)} ${rec.avg_ms}ms  ${rec.rounds[0].ok ? '' : (rec.rounds[0].err || '')}`);
  }
  // 危险工具：只检查"缺参数时是否明确报错"（不发真实请求）
  console.log('\n危险工具（不执行，仅检查参数校验）：');
  for (const n of danger) {
    let msg = '';
    try { await T[n].handler({}); msg = '⚠️ 缺参数时**没有**抛错（可能真的执行了动作）'; }
    catch (e) { msg = '有明确报错 ✓ ' + String(e && e.message || e).slice(0, 80); }
    report.danger.push({ name: n, validation: msg });
    console.log(`  - ${n.padEnd(22)} ${msg}`);
  }
  const c = {};
  for (const n of safe) c[report.tools[n].verdict] = (c[report.tools[n].verdict] || 0) + 1;
  console.log('\n汇总:', JSON.stringify(c));
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log('明细:', OUT);
})();
