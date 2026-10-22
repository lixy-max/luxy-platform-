// 用 Playwright Core 驱动本机 Edge 给界面截图（修正：先点父级"工具"标签，二级导航才可见）
const path = require('path');
const fs = require('fs');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.env.UI_URL || 'http://127.0.0.1:8080/index3.html';
const OUT = process.env.SHOT_DIR || path.join(__dirname, 'shots');

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = require('playwright-core');
  const logs = [];
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  page.on('console', m => logs.push(`[console.${m.type()}] ${m.text()}`));
  page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));
  page.on('response', r => { if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url().slice(0,150)}`); });

  const shot = async (n) => { const p = path.join(OUT, n + '.png'); await page.screenshot({ path: p }); console.log('SHOT ' + p); };
  const clickText = async (txt) => {
    const el = await page.$(`text=${txt}`);
    if (!el) { logs.push(`[warn] 找不到可点文本: ${txt}`); return false; }
    try { await el.click({ timeout: 8000 }); return true; }
    catch (e) { logs.push(`[warn] 点击失败 ${txt}: ${e.message.split('\n')[0]}`); return false; }
  };

  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2500);
    await shot('01-initial');

    // ① 先点父级"工具"标签（二级导航才会显示）
    const toolTab = await page.$('.tab:not(.sub)');
    const tabs = await page.$$eval('.tab', els => els.map(e => e.textContent.trim() + '|' + (e.className||'')));
    logs.push('[tabs] ' + JSON.stringify(tabs));
    // 逐个尝试点击含"工具"的顶层标签
    const toolCandidates = await page.$$('.tab');
    for (const t of toolCandidates) {
      const tx = (await t.textContent()) || '';
      const cls = (await t.getAttribute('class')) || '';
      if (tx.indexOf('工具') >= 0 && cls.indexOf('sub') < 0) {
        try { await t.click(); logs.push('[ok] 已点开"工具"标签'); } catch (e) { logs.push('[warn] 工具标签点击失败'); }
        break;
      }
    }
    await page.waitForTimeout(1500);
    await shot('02-tools-tab');

    // ② 点二级导航"编辑工作流"
    await clickText('📝 编辑工作流');
    await page.waitForTimeout(2500);
    await shot('03-editflow');

    // ③ 页面在不在、关键元素在不在
    const probe = await page.evaluate(() => {
      const q = (s) => !!document.querySelector(s);
      const vis = (s) => { const e = document.querySelector(s); if (!e) return false;
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      return {
        pageEditflow: q('#page-editflow'), pageVisible: vis('#page-editflow'),
        toolbar: q('#page-editflow .wf-toolbar'), svg: q('#docflow-svg'),
        nodeLib: q('#df-node-lib'), param: q('#df-param'), steps: q('#df-steps'),
        addBtn: q('[onclick="openAddDocStepModal()"]'),
        flowSelect: q('#df-preset-select'),
        flowOptions: (document.querySelector('#df-preset-select') || {}).length || 0,
      };
    });
    logs.push('[probe] ' + JSON.stringify(probe));
  } catch (e) {
    logs.push('SCRIPT_ERROR: ' + e.message.split('\n')[0]);
    try { await shot('99-error'); } catch (_) {}
  } finally {
    console.log('---- CONSOLE / ERRORS / PROBE ----');
    logs.forEach(l => console.log(l));
    await browser.close();
  }
})();
