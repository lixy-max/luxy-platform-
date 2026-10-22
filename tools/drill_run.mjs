// 演练跑手（part1 / core20）— 用真页面驱动 agent（工具只在浏览器里能执行 ✓）
// 用法: node run.mjs --ids 1,3,5 --lora drill_v1p --tag arm2 [--timeout-min 12]
//   --ids   指定任务号（逗号分隔 ✓ 不给则用 --from/--to 区间 ✓）
//   --lora  挂载的 LoRA 名（不给则不挂 = vllm:base ✓）
//   --tag   记录标签（arm0/arm1/arm2 ✓ 用于对比统计 ✓）
// 输出: E:\WSL\_check\drill\out\runs.jsonl（追加 ✓ 每条带 tag ✓）
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Users/A/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const PAGE_URL = 'http://127.0.0.1:8080/index3.html';
const TASKS_MD_DEFAULT = '//wsl$/Ubuntu-24.04/home/luxy/paper_lora_project/docs/DRILL_TASKS_PART1.md';
const OUT_DIR = 'E:/WSL/_check/drill/out';
const RUNS = path.join(OUT_DIR, 'runs.jsonl');

const args = process.argv.slice(2);
const getArg = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
// ★ 支持 part2（--tasks + --prefix）✓ 以及不指定 ids 时跑全量 ✓
const TASKS_MD = getArg('--tasks', TASKS_MD_DEFAULT);
const PREFIX = getArg('--prefix', 'P1');
const IDS = (getArg('--ids', '') || '').split(',').map(s => s.trim()).filter(Boolean);
const FROM = parseInt(getArg('--from', '1'), 10);
const TO = parseInt(getArg('--to', '999'), 10);
const LORA = (getArg('--lora', '') || '').trim();          // 空 = 不挂 ✓
const TAG = (getArg('--tag', 'drill') || 'drill').trim();
const TMO_MIN = parseInt(getArg('--timeout-min', '12'), 10);
// ★ A/B 混跑（2026-10-19）: 同一任务连续跑两臂（off/on ✓ 相邻时间 ✓）
//   ⇒ 控制 run-level 漂移 ✓（这是"分两次跑"做不到的 ✓ 也是 P-59 的根因 ✗）
const AB_MODE = args.includes('--ab');
const AB_TAG_OFF = getArg('--ab-off-tag', 'ab_off');
const AB_TAG_ON = getArg('--ab-on-tag', 'ab_on');

fs.mkdirSync(OUT_DIR, { recursive: true });

function parseTasks(mdPath) {
  const txt = fs.readFileSync(mdPath, 'utf8');
  const out = [];
  for (const b of txt.split(/\n(?=### [A-Z]+\d*-\d+)/)) {
    const m = b.match(/^### ([A-Z]+\d*-\d+)\s*【([^】]+)】\s*(.*)$/m);
    if (!m) continue;
    const ph = b.match(/- 原话:\s*(.+)/);
    const pts = [...b.matchAll(/^\s+\d\)\s*(.+)$/gm)].map(x => x[1].trim());
    out.push({ id: m[1], kind: m[2], title: m[3].trim(), phrase: ph ? ph[1].trim() : '', points: pts });
  }
  return out;
}

const all = parseTasks(TASKS_MD);
const todo = IDS.length
  ? all.filter(t => IDS.some(i => t.id === PREFIX + '-' + String(i).padStart(2, '0')))
  : all.filter(t => { const n = parseInt(String(t.id).split('-')[1], 10); return n >= FROM && n <= TO; });
console.log(`[drill] tag=${TAG} lora=${LORA || '（不挂）'} · 本批 ${todo.length} 条: ${todo.map(t => t.id).join(',')}`);
if (!todo.length) { console.log('[drill] 没有任务 → 退出'); process.exit(1); }

const browser = await chromium.launch({ executablePath: CHROME, headless: true });

// ★ 模型健康检查（2026-10-19 实测教训）:
//   vLLM 长跑会崩（显存/长上下文）✗ 而跑批会照样把"3 秒 0 工具"记成数据 ✗
//   ⇒ 每条开始前先探活 ✓ 挂了就等它恢复 ✓ 等不到就【跳过该条并不记录】✗ 避免污染数据 ✓
async function waitModelReady(maxMs = 10 * 60 * 1000) {
  const t0 = Date.now();
  let warned = false;
  while (Date.now() - t0 < maxMs) {
    try {
      const r = await fetch('http://127.0.0.1:8000/v1/models', { method: 'GET' });
      if (r.ok) {
        if (warned) console.log('[drill] 模型已恢复 ✓');
        return true;
      }
    } catch (e) {}
    if (!warned) { console.log('[drill] ⚠ 模型不可用，等待恢复（最多 10 分钟）…'); warned = true; }
    await new Promise(r => setTimeout(r, 15000));
  }
  return false;
}

// A/B 模式: 每个任务排成 off/on 两个 job ✓（交替 ✓ 相邻时间 ✓）
const jobs = [];
for (const t of todo) {
  if (AB_MODE) { jobs.push({ t, arm: 'off' }); jobs.push({ t, arm: 'on' }); }
  else jobs.push({ t, arm: null });
}
for (const job of jobs) {
  const t = job.t;

  const ready = await waitModelReady();
  if (!ready) { console.log(`[drill] ✗ 跳过 ${t.id}（模型 10 分钟未恢复，不记垃圾数据 ✗）`); continue; }
  const rec = { tag: (job.arm ? (job.arm === 'off' ? AB_TAG_OFF : AB_TAG_ON) : TAG), lora: LORA || null, id: t.id, kind: t.kind, title: t.title,
                phrase: t.phrase, started_at: new Date().toISOString(), ok: false };
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  // ★ A/B: arm=off ⇒ 关闭闸门⑤ ✓ arm=on ⇒ 保持默认开 ✓
  if (job.arm) {
    try {
      await ctx.addInitScript((off) => { try { off ? localStorage.setItem('ds_gate5','off') : localStorage.removeItem('ds_gate5'); } catch (e) {} }, job.arm === 'off');
    } catch (e) { console.error('[ab] 注入开关失败:', e.message); }
  }
  const page = await ctx.newPage();
  const net = [];
  // ★ P0-2（2026-10-18）: 工具调用改为【API 层】计数 ✓
  //   原因: 数页面文本「✅ 工具名」受渲染/历史截断影响 ⇒ 会系统性少算 ✗
  //        （实测: 页面上 tool_calls 明明有 ✓ 但文本计数为 0 ✗）
  //   做法: 监听 /chat/tools 响应 ✓ 累加每轮 tool_calls ✓ 同时保留 UI 计数做交叉校验 ✓
  const apiCalls = [];
  page.on('response', async r => {
    if (!r.url().includes('/chat/tools')) return;
    try {
      const j = await r.json();
      for (const t of (j.tool_calls || [])) apiCalls.push(t.name || (t.function || {}).name || '?');
    } catch (e) { /* 非 JSON ✓ 忽略 */ }
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
    // 选模型: 有 LoRA 则挂 LoRA，否则用底座 ✓
    const want = LORA ? ('vllm:lora:' + LORA) : 'vllm:base';
    const picked = await page.evaluate((w) => {
      const s = document.getElementById('chat-model-select');
      if (!s) return null;
      const opt = Array.from(s.options).find(o => o.value === w);
      if (!opt) return { found: false, values: Array.from(s.options).map(o => o.value).slice(0, 12) };
      s.value = w;
      s.dispatchEvent(new Event('change'));
      return { found: true, value: s.value };
    }, want);
    rec.model_pick = picked;
    await page.waitForTimeout(LORA ? 12000 : 800);   // 挂 LoRA 要等加载 ✓
    rec.model_value = await page.evaluate(() => { const s = document.getElementById('chat-model-select'); return s ? s.value : null; });
    rec.lora_loaded = await page.evaluate(() => { try { return !!window.__lastLoraLoaded; } catch (e) { return null; } });
    // Agent 模式
    const tog = page.locator('#agent-mode-toggle button[data-mode="agent"]');
    if (await tog.count()) { await tog.click(); await page.waitForTimeout(700); }
    rec.mode = await page.locator('#agent-mode-toggle button.active').getAttribute('data-mode').catch(() => null);
    rec.learn = await page.evaluate(() => { try { return window.__learnPayload ? window.__learnPayload() : null; } catch (e) { return null; } });
    await page.evaluate(() => { try { window.__lastAgentEscalation = null; } catch (e) {} });

    const t0 = Date.now();
    await page.locator('#chat-input').fill(t.phrase);
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
    // ★ 度量修复（2026-10-18 · failure-miner 指出）:
    //   ① 原先 msgs 用 .slice(-6000) 截头 ⇒ 长会话丢最早的工具卡 ✗ ⇒ 改为全量存 ✓
    //   ② 原先 ui_tools 用正则只匹配「✅」⇒ 【失败卡(❌)全漏】✗ 还把正文符号误判 ✗
    //   ③ 改为【从 DOM 直读 .agent-tool-card】✓ 名字 + 状态都拿 ✓
    rec.msgs = await page.locator('#chat-messages').innerText().catch(() => '');
    rec.msgs_len = rec.msgs.length;
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
        // ★ 名字在【第二行】（第一行是 ✅/❌ ✓ 实测踩过 ✗）
        const name = (lines[1] || lines[0] || '').slice(0, 60);
        out.push({ name: name, status: status, is_notice: /parallel_notice/.test(t) });
      });
      return out;
    }).catch(() => []);
    // ★ 真实执行数 = 卡片数（排除通知卡 ✓）；名字列表也排除通知卡 ✓
    const _realCards = (rec.ui_cards || []).filter(c => !c.is_notice && c.name);
    rec.exec_count = _realCards.length;
    rec.exec_tools = [...new Set(_realCards.map(x => x.name))];
    rec.exec_ok_tools = [...new Set(_realCards.filter(x => x.status === 'ok').map(x => x.name))];
    rec.exec_err_tools = [...new Set(_realCards.filter(x => x.status === 'error').map(x => x.name))];
    rec.ui_card_names = rec.exec_tools;
    rec.ui_card_ok = rec.exec_ok_tools;
    rec.escalated = await page.evaluate(() => window.__lastAgentEscalation || null);
    // ★ 口径分层（关键 ✓）:
    //   api_tools   = 模型【提出】的调用（可能被并行吞 ✗ P-22 已修 ✓）
    //   exec_count  = 页面【真的画了卡 = 真的执行了】（主口径 ✓）
    rec.api_tools = apiCalls;
    rec.api_uniq_tools = [...new Set(apiCalls)];
    rec.tools = rec.exec_tools;
    rec.uniq_tools = rec.exec_tools;
    rec.net = [...new Set(net)];
    rec.ok = done;
  } catch (e) {
    rec.error = String((e && e.message) || e).slice(0, 300);
    try { await page.screenshot({ path: path.join(OUT_DIR, `fail_${TAG}_${t.id}.png`) }); } catch (e2) {}
  } finally {
    rec.done_at = new Date().toISOString();
    // ★ P-60 加固（2026-10-19 子代理发现占位行 ✗）: 标记瞬时占位/失败行 ✓
    //   判定: 没成功 + 没提出任何工具 + 耗时<3秒或无耗时 ⇒ 占位行 ✓
    //   ⇒ 判分器排除 ✓ 重跑器自动重跑 ✓（不再静默丢任务 ✗）
    {
      const _el = Number(rec.elapsed_s || 0);
      rec.placeholder = (!rec.ok && (rec.api_tools || []).length === 0 &&
                         (_el < 3 || !rec.elapsed_s)) ? true : false;
    }
    fs.appendFileSync(RUNS, JSON.stringify(rec) + '\n', 'utf8');
    console.log(`[drill] ${TAG} ${t.id} ${rec.ok ? '✓' : '✗'} ${rec.elapsed_s || '?'}s ` +
                `model=${rec.model_value || '-'} 提出=${(rec.api_tools || []).length} 执行=${(rec.exec_tools || []).length}${('[' + (rec.exec_tools || []).join(',') + ']')} ` +
                `esc=${rec.escalated ? 'yes' : 'no'} :: ${t.phrase.slice(0, 26)}`);
    await ctx.close().catch(() => {});
  }
}

await browser.close();
console.log(`[drill] 本批完成 → ${RUNS}`);
