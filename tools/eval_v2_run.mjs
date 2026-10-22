// ============================================================================
// EVAL V2 跑批（2026-10-21 · T-5）
//
// 基于 tools/drill_run.mjs ✓（已验证的 UI 驱动 + /chat/tools 精确计数 ✓）
// 新增 V2 需要的 5 件事:
//   ① 读 JSON 任务集（docs/EVAL_SET_V2.json ✓）
//   ② **每题重复 N 次** ✓（默认 3 · stability 档 5 · 测执行稳定性 ✓）
//   ③ **分档记录**（hard/mid/easy/stability ✓）
//   ④ **状态不变量快照**（跑前跑后 ✓ workflow_config.json md5 / KB 块数 / LoRA 列表）
//   ⑤ 支持 `--tier` 过滤 / `--dry-run`
//
// 用法（★ 在 Windows 上跑 ✓ Node + playwright-core ✓）:
//   node tools/eval_v2_run.mjs                              # 全量 35 题 × 3 次
//   node tools/eval_v2_run.mjs --tier hard                  # 只跑 hard 档
//   node tools/eval_v2_run.mjs --limit 5 --dry-run          # 干跑（只列任务）
//   node tools/eval_v2_run.mjs --lora test_3060_xxx --tag v2_lora
//   node tools/eval_v2_run.mjs --arm off                    # A/B: 关闸门⑤
//
// 输出: E:/WSL/_check/eval_v2/out/runs_v2.jsonl（追加 ✓ 每条带 tier/repeat ✓）
//       E:/WSL/_check/eval_v2/out/snapshots.json（状态快照 ✓）
// ============================================================================
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

// ── 常量 ────────────────────────────────────────────────────────────────────
const CHROME = 'C:/Users/A/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const PAGE_URL = 'http://127.0.0.1:8080/index3.html';
const API = 'http://127.0.0.1:8001';
const VLLM = 'http://127.0.0.1:8000';
const PROJECT = '//wsl$/Ubuntu-24.04/home/luxy/paper_lora_project';
const EVAL_SET = process.env.EVAL_SET || (PROJECT + '/docs/EVAL_SET_V2.json');
const OUT_DIR = process.env.EVAL_OUT || 'E:/WSL/_check/eval_v2/out';
const RUNS = path.join(OUT_DIR, 'runs_v2.jsonl');
const SNAPS = path.join(OUT_DIR, 'snapshots.json');

// ── 参数 ────────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const has = (k) => args.includes(k);

const TIER = (getArg('--tier', '') || '').trim();            // 空 = 全档
const LIMIT = parseInt(getArg('--limit', '0'), 10);          // 0 = 不限
const REPEATS = parseInt(getArg('--repeats', '0'), 10);      // 0 = 用任务自带
const LORA = (getArg('--lora', '') || '').trim();
const TAG = (getArg('--tag', 'v2') || 'v2').trim();
const TMO_MIN = parseInt(getArg('--timeout-min', '12'), 10);
const ARM = (getArg('--arm', '') || '').trim();              // '' | 'off'（关闸门⑤）
const DRY = has('--dry-run');
const SKIP_SNAP = has('--no-snapshot');
// ★ T-3（2026-10-21）: KB 开关 —— 'off' 或 '' = 不挂 KB ✓ 其它 = 挂该知识库 ✓
const KB_NAME = (getArg('--kb', 'off') || 'off').trim();
// ★ T-3: KB 相关性门控开关 —— 'off' = 旧行为（无条件注入 ✓ 对照臂 ✓）
const KB_GATE = (getArg('--kb-gate', 'on') || 'on').trim();
// ★ KB 预注入开关（默认关 ✗ 要测 KB 臂必须开 ✓）
const KB_INJECT = (getArg('--kb-inject', 'off') || 'off').trim();

fs.mkdirSync(OUT_DIR, { recursive: true });

// ── 读任务集 ────────────────────────────────────────────────────────────────
function loadTasks() {
  const raw = fs.readFileSync(EVAL_SET, 'utf8');
  const d = JSON.parse(raw);
  return { design: d.design || {}, tasks: d.tasks || [] };
}

const { design, tasks: allTasks } = loadTasks();
let todo = allTasks;
if (TIER) todo = todo.filter(t => t.tier === TIER);
if (LIMIT > 0) todo = todo.slice(0, LIMIT);

console.log('='.repeat(78));
console.log('  EVAL V2 跑批');
console.log('='.repeat(78));
console.log(`  任务集: ${EVAL_SET}`);
console.log(`  设计:   n=${design.n} · ${design.protocol || ''}`);
console.log(`  本次:   ${todo.length} 题${TIER ? `（仅 ${TIER} 档）` : ''} · lora=${LORA || '（不挂）'} · kb=${KB_NAME}(${KB_GATE}/inj=${KB_INJECT}) · tag=${TAG}${ARM ? ' · arm=' + ARM : ''}`);
const byTier = {};
for (const t of todo) byTier[t.tier] = (byTier[t.tier] || 0) + 1;
console.log(`  分档:   ${JSON.stringify(byTier)}`);
const totalRuns = todo.reduce((s, t) => s + (REPEATS > 0 ? REPEATS : (t.repeats || 3)), 0);
console.log(`  总跑次: ${totalRuns}（每题 ${REPEATS > 0 ? REPEATS : '按档 3/5'} 次）`);
if (DRY) {
  console.log('');
  console.log('  [dry-run] 任务清单:');
  for (const t of todo) {
    console.log(`    ${t.id} [${t.tier}] ×${REPEATS > 0 ? REPEATS : (t.repeats || 3)} · ${t.phrase.slice(0, 60)}`);
  }
  process.exit(0);
}

// ── 状态快照（状态不变量 ✓）────────────────────────────────────────────────
async function snapshot() {
  const s = { ts: new Date().toISOString(), checks: {} };
  // ① workflow_config.json md5 ✓（默认配置不许被改 ✗）
  try {
    const p = PROJECT + '/workflow_config.json';
    const buf = fs.readFileSync(p);
    s.checks.workflow_config_md5 = crypto.createHash('md5').update(buf).digest('hex');
  } catch (e) { s.checks.workflow_config_md5 = 'ERR:' + e.message.slice(0, 40); }
  // ② KB 块数 ✓
  try {
    const r = await fetch(API + '/kb/list');
    const d = await r.json();
    const kbs = d.knowledge_bases || [];
    s.checks.kb = Object.fromEntries(kbs.map(k => [k.name, k.total_chunks]));
  } catch (e) { s.checks.kb = 'ERR:' + e.message.slice(0, 40); }
  // ③ LoRA 列表 ✓
  try {
    const r = await fetch(API + '/loras');
    const d = await r.json();
    const items = Array.isArray(d) ? d : (d.loras || d.items || []);
    s.checks.loras = items.map(x => (typeof x === 'string' ? x : (x.name || x.id || '?'))).sort();
  } catch (e) { s.checks.loras = 'ERR:' + e.message.slice(0, 40); }
  return s;
}

// ── 模型健康检查 ✓（长跑会崩 · 别记垃圾数据 ✗）──────────────────────────────
async function waitModelReady(maxMs = 10 * 60 * 1000) {
  const t0 = Date.now();
  let warned = false;
  while (Date.now() - t0 < maxMs) {
    try {
      const r = await fetch(VLLM + '/v1/models', { method: 'GET' });
      if (r.ok) { if (warned) console.log('[v2] 模型已恢复 ✓'); return true; }
    } catch (e) {}
    if (!warned) { console.log('[v2] ⚠ 模型不可用，等待恢复（最多 10 分钟）…'); warned = true; }
    await new Promise(r => setTimeout(r, 15000));
  }
  return false;
}

// ── 跑一条 ✓ ────────────────────────────────────────────────────────────────
async function runOne(browser, task, repeat, arm) {
  const rec = {
    tag: TAG, lora: LORA || null, arm: arm || null, kb_name: KB_NAME, kb_gate: KB_GATE, kb_inject: KB_INJECT,
    id: task.id, source_id: task.source_id, tier: task.tier,
    disc_score: task.disc_score, repeat,
    phrase: task.phrase, started_at: new Date().toISOString(), ok: false,
  };
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  // A/B: arm=off ⇒ 关闸门⑤ ✓
  try {
    const off = arm === 'off';
    await ctx.addInitScript((o) => {
      try { o ? localStorage.setItem('ds_gate5', 'off') : localStorage.removeItem('ds_gate5'); } catch (e) {}
    }, off);
  } catch (e) {}
  // ★ T-3: KB 门控 A/B ✓
  try {
    await ctx.addInitScript((g) => {
      try {
        if (g === 'off') localStorage.setItem('ds_kb_gate', 'off');
        else localStorage.removeItem('ds_kb_gate');
      } catch (e) {}
    }, KB_GATE);
  } catch (e) {}
  // ★ KB 预注入开关 ✓
  try {
    await ctx.addInitScript((v) => {
      try {
        if (v === 'on') localStorage.setItem('ds_kb_inject', 'on');
        else localStorage.removeItem('ds_kb_inject');
      } catch (e) {}
    }, KB_INJECT);
  } catch (e) {}
  const page = await ctx.newPage();
  const net = [];
  const apiCalls = [];
  // ★ API 层工具计数 ✓（比数页面文本准 ✓）
  page.on('response', async r => {
    if (!r.url().includes('/chat/tools')) return;
    try {
      const j = await r.json();
      for (const t of (j.tool_calls || [])) apiCalls.push(t.name || (t.function || {}).name || '?');
    } catch (e) {}
  });
  page.on('response', r => {
    const u = r.url();
    if (u.includes('/chat/tools')) net.push('tools:' + r.status());
    if (u.includes('/chat/completions/stream')) net.push('stream:' + r.status());
    if (u.includes('/loras/load') || u.includes('/vllm/load_lora')) net.push('load:' + r.status());
  });
  try {
    await page.goto(PAGE_URL + '?t=' + Date.now() + '#/chat', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(6500);
    // 选模型 ✓
    const want = LORA ? ('vllm:lora:' + LORA) : 'vllm:base';
    rec.model_pick = await page.evaluate((w) => {
      const s = document.getElementById('chat-model-select');
      if (!s) return null;
      const opt = Array.from(s.options).find(o => o.value === w);
      if (!opt) return { found: false, values: Array.from(s.options).map(o => o.value).slice(0, 12) };
      s.value = w; s.dispatchEvent(new Event('change'));
      return { found: true, value: s.value };
    }, want);
    await page.waitForTimeout(LORA ? 12000 : 800);
    rec.model_value = await page.evaluate(() => {
      const s = document.getElementById('chat-model-select'); return s ? s.value : null;
    });
    // Agent 模式 ✓
    const tog = page.locator('#agent-mode-toggle button[data-mode="agent"]');
    if (await tog.count()) { await tog.click(); await page.waitForTimeout(700); }
    rec.mode = await page.locator('#agent-mode-toggle button.active').getAttribute('data-mode').catch(() => null);
    // ★ T-3: 设置 KB（'off' ⇒ 清空 = 不挂 ✓）
    try {
      const kbWant = (KB_NAME === 'off' || !KB_NAME) ? '' : KB_NAME;
      rec.kb = await page.evaluate((want) => {
        const sel = document.getElementById('chat-kb-select');
        if (!sel) return { found: false };
        sel.value = want;
        sel.dispatchEvent(new Event('change'));
        return { found: true, value: sel.value };
      }, kbWant);
      await page.waitForTimeout(400);
    } catch (e) { rec.kb = { err: String(e).slice(0, 80) }; }
    rec.learn = await page.evaluate(() => { try { return window.__learnPayload ? window.__learnPayload() : null; } catch (e) { return null; } });
    await page.evaluate(() => { try { window.__lastAgentEscalation = null; } catch (e) {} });

    const t0 = Date.now();
    await page.locator('#chat-input').fill(task.phrase);
    await page.locator('#chat-send-btn').click();
    page.on('dialog', async d => { rec.native_dialog = d.message().slice(0, 120); await d.accept().catch(() => {}); });

    const maxMs = TMO_MIN * 60 * 1000;
    let done = false;
    rec.confirms = [];
    while (Date.now() - t0 < maxMs) {
      await page.waitForTimeout(2500);
      const okBtn = page.locator('.agent-modal-backdrop button[data-act="ok"]');
      if (await okBtn.count()) {
        let toolName = '';
        try { toolName = (await page.locator('.agent-modal-backdrop .agent-modal code').first().innerText()).trim(); } catch (e) {}
        rec.confirms.push(toolName || 'unknown');
        await okBtn.first().click().catch(() => {});
        await page.waitForTimeout(800);
        continue;
      }
      const busy = await page.locator('#chat-send-btn').isDisabled().catch(() => true);
      if (!busy) { done = true; break; }
    }
    rec.elapsed_s = Math.round((Date.now() - t0) / 1000);
    rec.finished = done;
    rec.msgs = await page.locator('#chat-messages').innerText().catch(() => '');
    rec.msgs_len = rec.msgs.length;
    // 工具卡 ✓（DOM 直读 ✓ 含状态 ✓）
    rec.ui_cards = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('#chat-messages .agent-tool-card').forEach(c => {
        const t = c.innerText || '';
        const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
        const cls = String(c.className || '');
        let status = 'unknown';
        if (/error|fail|❌/i.test(cls) || /❌/.test(t.slice(0, 4))) status = 'error';
        else if (/running|⏳/i.test(cls) || /正在调用|running/i.test(t.slice(0, 40))) status = 'running';
        else if (/ok|✅/i.test(cls) || /✅/.test(t.slice(0, 4))) status = 'ok';
        const name = (lines[1] || lines[0] || '').slice(0, 60);
        out.push({ name, status, is_notice: /parallel_notice/.test(t) });
      });
      return out;
    }).catch(() => []);
    const realCards = (rec.ui_cards || []).filter(c => !c.is_notice && c.name);
    rec.exec_count = realCards.length;
    rec.exec_tools = [...new Set(realCards.map(x => x.name))];
    rec.exec_ok_tools = [...new Set(realCards.filter(x => x.status === 'ok').map(x => x.name))];
    rec.exec_err_tools = [...new Set(realCards.filter(x => x.status === 'error').map(x => x.name))];
    rec.escalated = await page.evaluate(() => window.__lastAgentEscalation || null);
    rec.api_tools = apiCalls;
    rec.api_uniq_tools = [...new Set(apiCalls)];
    rec.tools = rec.exec_tools;
    rec.net = [...new Set(net)];
    rec.ok = done;
    // ★ V2 新增: 按任务期望算工具命中 ✓（粗判 · 精细判分交给 score 脚本 ✓）
    const want_tools = (task.expect && task.expect.tools_required) || [];
    rec.tools_hit = want_tools.filter(w => rec.exec_tools.includes(w));
    rec.tools_hit_rate = want_tools.length ? rec.tools_hit.length / want_tools.length : null;
  } catch (e) {
    rec.error = String((e && e.message) || e).slice(0, 300);
    try { await page.screenshot({ path: path.join(OUT_DIR, `fail_${TAG}_${task.id}_r${repeat}.png`) }); } catch (e2) {}
  } finally {
    rec.done_at = new Date().toISOString();
    rec.placeholder = (!rec.ok && (rec.api_tools || []).length === 0 &&
                       (Number(rec.elapsed_s || 0) < 3 || !rec.elapsed_s)) ? true : false;
    fs.appendFileSync(RUNS, JSON.stringify(rec) + '\n', 'utf8');
    console.log(`[v2] ${TAG} ${task.id}[${task.tier}] r${repeat} ${rec.ok ? '✓' : '✗'} ` +
                `${rec.elapsed_s || '?'}s 工具=${(rec.exec_tools || []).length}` +
                `${(rec.exec_tools || []).length ? '[' + rec.exec_tools.join(',') + ']' : ''} ` +
                `命中=${(rec.tools_hit || []).length}/${((task.expect || {}).tools_required || []).length} ` +
                `esc=${rec.escalated ? 'yes' : 'no'}`);
    await ctx.close().catch(() => {});
  }
  return rec;
}

// ── 主流程 ──────────────────────────────────────────────────────────────────
const browser = await chromium.launch({ executablePath: CHROME, headless: true });

// ① 跑前快照 ✓
let snapBefore = null;
if (!SKIP_SNAP) {
  snapBefore = await snapshot();
  console.log('');
  console.log('[v2] 跑前快照:');
  console.log('  workflow_config md5:', snapBefore.checks.workflow_config_md5);
  console.log('  KB 块数:', JSON.stringify(snapBefore.checks.kb));
  console.log('  LoRA 数:', Array.isArray(snapBefore.checks.loras) ? snapBefore.checks.loras.length : snapBefore.checks.loras);
}

// ② 逐个任务 × 重复 ✓
let done = 0;
const tStart = Date.now();
for (const task of todo) {
  const reps = REPEATS > 0 ? REPEATS : (task.repeats || 3);
  for (let r = 1; r <= reps; r++) {
    const ready = await waitModelReady();
    if (!ready) {
      console.log(`[v2] ✗ 跳过 ${task.id} r${r}（模型 10 分钟未恢复 ⇒ 不记垃圾数据 ✗）`);
      continue;
    }
    await runOne(browser, task, r, ARM);
    done++;
    const pct = ((done / totalRuns) * 100).toFixed(1);
    const el = Math.round((Date.now() - tStart) / 1000);
    console.log(`[v2] 进度 ${done}/${totalRuns}（${pct}%）· 已用 ${Math.floor(el / 60)}m${el % 60}s`);
  }
}

// ③ 跑后快照 + 不变量检查 ✓
let snapAfter = null;
if (!SKIP_SNAP) {
  snapAfter = await snapshot();
  console.log('');
  console.log('[v2] 跑后快照:');
  console.log('  workflow_config md5:', snapAfter.checks.workflow_config_md5);
  console.log('  KB 块数:', JSON.stringify(snapAfter.checks.kb));
  const inv = {
    workflow_config_unchanged: snapBefore.checks.workflow_config_md5 === snapAfter.checks.workflow_config_md5,
    kb_counts: { before: snapBefore.checks.kb, after: snapAfter.checks.kb },
    loras: { before: snapBefore.checks.loras, after: snapAfter.checks.loras },
  };
  console.log('  ★ 状态不变量: workflow_config 未改 =', inv.workflow_config_unchanged, inv.workflow_config_unchanged ? '✓' : '✗');
  const snapAll = { tag: TAG, lora: LORA || null, arm: ARM || null, before: snapBefore, after: snapAfter, invariant: inv };
  fs.writeFileSync(SNAPS, JSON.stringify(snapAll, null, 2), 'utf8');
  console.log('  快照已存:', SNAPS);
}

await browser.close();
const totalEl = Math.round((Date.now() - tStart) / 1000);
console.log('');
console.log('='.repeat(78));
console.log(`  ✅ 本批完成: ${done} 次跑 · ${Math.floor(totalEl / 60)}m${totalEl % 60}s`);
console.log(`  结果: ${RUNS}`);
console.log('='.repeat(78));
