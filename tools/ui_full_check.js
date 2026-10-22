// 全站自检 + 内置 Agent 真跑一遍
// 不依赖"看图"：靠 DOM 文本、元素可见性、以及全程捕获的 console/pageerror/HTTP 4xx
const path = require('path');
const fs = require('fs');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.env.UI_URL || 'http://127.0.0.1:8080/index3.html';
const OUT = process.env.SHOT_DIR || path.join(__dirname, 'shots');

const PAGES = [
  ['chat', '💬 对话'], ['config', '🧩 分析工作流'], ['kb', '📚 知识库'],
  ['train', '🚀 LoRA 训练'], ['history', '📜 历史'], ['editflow', '📝 编辑工作流'],
  ['lora', '🧱 LoRA 管理'], ['base', '🎛️ 底座管理'], ['settings', '⚙️ 系统设置'],
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = require('playwright-core');
  const errors = [];
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });
  page.on('response', r => { const u = r.url();
    if (r.status() >= 400 && u.indexOf('favicon') < 0) errors.push(`http ${r.status()} ${u.slice(0,130)}`); });

  const errCount = () => errors.length;
  const shot = (n) => page.screenshot({ path: path.join(OUT, n + '.png') });

  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2500);

    // === 第一部分：逐页巡视 ===
    console.log('==== 逐页巡视 ====');
    for (const [key, label] of PAGES) {
      const before = errCount();
      const tab = `.tab.sub[data-page="${key}"], .tab[data-page="${key}"]`;
      try {
        const el = await page.$(tab);
        if (!el) { console.log(`  ✗ ${label}: 找不到导航项`); continue; }
        // 先点开父级"工具"（二级导航才可见）
        const cls = (await el.getAttribute('class')) || '';
        if (cls.indexOf('sub') >= 0) {
          for (const t of await page.$$('.tab')) {
            const tx = (await t.textContent()) || ''; const c = (await t.getAttribute('class')) || '';
            if (tx.indexOf('工具') >= 0 && c.indexOf('sub') < 0) { await t.click().catch(()=>{}); break; }
          }
          await page.waitForTimeout(400);
        }
        await el.click({ timeout: 8000 });
        await page.waitForTimeout(1800);
      } catch (e) { console.log(`  ✗ ${label}: 点击失败 ${e.message.split('\n')[0]}`); continue; }

      // 基本探针：该页可见 + 页面内元素数量 + 是否有明显空页
      const info = await page.evaluate((k) => {
        const p = document.querySelector('#page-' + k);
        if (!p) return { exists: false };
        const r = p.getBoundingClientRect();
        const txt = (p.innerText || '').replace(/\s+/g, ' ').trim();
        return { exists: true, visible: r.width > 0 && r.height > 0,
                 nodes: p.querySelectorAll('*').length,
                 buttons: p.querySelectorAll('button').length,
                 textLen: txt.length, head: txt.slice(0, 110) };
      }, key);
      const newErr = errCount() - before;
      const ok = info.exists && info.visible && info.nodes > 20;
      console.log(`  ${ok ? '✓' : '✗'} ${label}: 可见=${info.visible} 元素=${info.nodes} 按钮=${info.buttons} 文本=${info.textLen} 新报错=${newErr}`);
      if (!ok || newErr) console.log(`      首段文字: ${info.head}`);
      await shot('page-' + key);
    }

    // === 第二部分：内置 Agent 真跑 ===
    console.log('');
    console.log('==== 内置 Agent 真跑 ====');
    const beforeAgent = errCount();
    try {
      // 回到对话页
      const chatTab = await page.$('.tab[data-page="chat"]');
      if (chatTab) { await chatTab.click(); await page.waitForTimeout(1200); }

      // 切到 Agent 模式
      const agentBtn = await page.$('#agent-mode-toggle button[data-mode="agent"]');
      if (agentBtn) { await agentBtn.click(); await page.waitForTimeout(800); console.log('  ✓ 已切到 Agent 模式'); }
      else console.log('  ✗ 找不到 Agent 模式开关');

      // 选模型（若下拉为空则说明 vLLM 没起来）
      const modelOpts = await page.$$eval('#chat-model-select option', els => els.map(e => e.value));
      console.log('  可选模型数:', modelOpts.length);
      if (modelOpts.length) await page.selectOption('#chat-model-select', modelOpts[0]);

      const task = '列一下当前工作流有哪些节点，然后看看 uploads 里有哪些文件';
      await page.fill('#chat-input', task);
      console.log('  已输入任务:', task.slice(0, 40));
      await page.click('#chat-send-btn');
      console.log('  已发送，等待 agent 跑完…');

      // 轮询等待：出现工具卡片或回答，且发送按钮恢复可用
      let waited = 0, done = false;
      while (waited < 120000) {
        await page.waitForTimeout(5000); waited += 5000;
        const st = await page.evaluate(() => {
          const box = document.querySelector('#chat-messages');
          const txt = box ? (box.innerText || '') : '';
          const cards = box ? box.querySelectorAll('.agent-tool-card').length : 0;
          const btn = document.querySelector('#chat-send-btn');
          return { cards, len: txt.length, busy: btn ? btn.disabled : false, tail: txt.slice(-260) };
        });
        console.log(`    [${waited/1000}s] 工具卡片=${st.cards} 文本=${st.len} 发送键禁用=${st.busy}`);
        if (!st.busy && st.len > 0) { done = true;
          console.log('  ---- 对话尾部 ----'); console.log('  ' + st.tail.replace(/\n/g, '\n  '));
          break; }
      }
      console.log(done ? '  ✓ agent 已返回' : '  ✗ 120s 内未见完成');
      await shot('agent-run');
    } catch (e) {
      console.log('  ✗ Agent 环节出错: ' + e.message.split('\n')[0]);
      try { await shot('agent-error'); } catch (_) {}
    }
    const agentErr = errCount() - beforeAgent;
    console.log('  Agent 环节新报错数:', agentErr);

    console.log('');
    console.log('==== 汇总 ====');
    console.log('  全过程捕获到的错误总数:', errors.length);
    const uniq = Array.from(new Set(errors));
    uniq.slice(0, 15).forEach(e => console.log('  ! ' + e));
  } catch (e) {
    console.log('SCRIPT_ERROR: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
  }
})();
