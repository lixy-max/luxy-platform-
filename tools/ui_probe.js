// 深度交互探针：真的点按钮、断言元素、捕获控制台报错
// 目的：不依赖"看图"，也能自己验证编辑工作流是否可操作（今天用户发现的 5 个 bug 都属这类）
const path = require('path');
const fs = require('fs');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const URL = process.env.UI_URL || 'http://127.0.0.1:8080/index3.html';
const OUT = process.env.SHOT_DIR || path.join(__dirname, 'shots');

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = require('playwright-core');
  const errors = [];
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400 && r.url().indexOf('favicon') < 0) errors.push(`http ${r.status()} ${r.url().slice(0,140)}`); });
  const shot = async (n) => { await page.screenshot({ path: path.join(OUT, n + '.png') }); };
  const R = {};
  const mark = (k, v) => { R[k] = v; console.log(`  ${v === true ? '✓' : (v === false ? '✗' : '·')} ${k}: ${JSON.stringify(v)}`); };

  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);
    // 进"工具" -> 编辑工作流
    for (const t of await page.$$('.tab')) {
      const tx = (await t.textContent()) || '';
      const cls = (await t.getAttribute('class')) || '';
      if (tx.indexOf('工具') >= 0 && cls.indexOf('sub') < 0) { await t.click(); break; }
    }
    await page.waitForTimeout(800);
    await page.click('text=📝 编辑工作流');
    await page.waitForTimeout(2000);
    mark('进入编辑工作流页', await page.isVisible('#page-editflow'));
    // 节点库是进页面后异步渲染的。这里用与 diag_lib.js 相同、已验证可靠的写法：
    // 等待出现按钮 -> 直接在页面内读取（不要用 $$eval 的返回值做多行 mark）
    let libInfo = { buttons: -1, macros: [] };
    try {
      await page.waitForFunction(
        () => { const b = document.querySelectorAll('#df-node-lib button'); return b.length > 0; },
        { timeout: 15000 });
      libInfo = await page.evaluate(() => {
        const box = document.getElementById('df-node-lib');
        const txt = box ? box.textContent : '';
        return {
          buttons: box ? box.querySelectorAll('button').length : -1,
          // 节点库已改为标准文档编辑流程，正面显示中文阶段名（不再用 ①②③）
          macros: ['导入文档', '格式规范化', '分段与标题', '校对纠错', '润色改写',
                    '精简摘要', '逻辑核查', '术语统一', '扩写补写', '整篇翻译', '导出 Word']
                    .filter(k => txt.indexOf(k) >= 0),
        };
      });
    } catch (e) { libInfo = { buttons: -1, macros: [], err: String(e).slice(0, 80) }; }
    mark('节点库按钮数', libInfo.buttons);
    mark('节点库含宏观五段', libInfo.macros);

    // ① 载入一个预设（保证画布上有节点）
    const opts = await page.$$eval('#df-preset-select option', els => els.map(e => e.value));
    mark('流程下拉选项数', opts.length);
    if (opts.length) {
      await page.selectOption('#df-preset-select', opts[0]);
      await page.waitForTimeout(2500);
      const nodeCount = await page.$$eval('#docflow-svg g', gs => gs.length);
      mark('载入预设后画布节点组数', nodeCount);
      mark('步骤清单已渲染', await page.isVisible('#df-steps'));
      await shot('10-preset-loaded');
    }

    // ② 点画布节点 -> 参数面板应出现字段
    const nodes = await page.$$('#docflow-svg g rect');
    mark('画布 rect 数', nodes.length);
    if (nodes.length) {
      await nodes[1].click({ force: true });
      await page.waitForTimeout(1200);
      const paramText = (await page.textContent('#df-param').catch(() => '')) || '';
      mark('参数面板有内容', paramText.trim().length > 10);
      mark('参数面板字段标签', ['文件','工作表','分组列','汇总列','列','条件','数据来源','输出文件名']
        .filter(k => paramText.indexOf(k) >= 0));
      mark('必填提示存在', paramText.indexOf('必填') >= 0);
      await shot('11-node-selected');
    }

    // ③ ➕ 添加步骤：应弹窗
    await page.click('[onclick="openAddDocStepModal()"]');
    await page.waitForTimeout(900);
    mark('添加步骤弹窗可见', await page.isVisible('#add-doc-step-modal'));
    const items = await page.$$eval('#add-doc-step-list button', b => b.map(x => x.textContent.trim().slice(0, 30)));
    mark('弹窗里的工具数', items.length);
    await shot('12-add-modal');
    // 选一个工具（group_sum）
    const idx = items.findIndex(t => t.indexOf('group_sum') >= 0);
    if (idx >= 0) {
      await page.click(`#add-doc-step-list button:nth-child(${idx + 1})`);
      await page.waitForTimeout(1800);
      mark('弹窗已关闭', !(await page.isVisible('#add-doc-step-modal')));
      const after = await page.$$eval('#docflow-svg g', gs => gs.length);
      mark('加节点后画布组数', after);
      mark('参数面板显示新节点参数', ((await page.textContent('#df-param').catch(() => '')) || '').indexOf('group_sum') >= 0);
      await shot('13-node-added');
    }

    // ④ 绿点存在（拖拽建立引用的前提）
    mark('画布上绿点(圆)数', await page.$$eval('#docflow-svg circle', cs => cs.length));

    // ⑤ 拖绿点 -> 另一个节点（建立引用）
    // 用类名精确取【绿点】(输出) 与【目标节点】，避免上次抓到蓝点导致误判
    const outs = await page.$$('#docflow-svg .doc-dot-out');
    const rects = await page.$$('#docflow-svg g rect');
    mark('绿点(输出)数', outs.length);
    if (outs.length >= 1 && rects.length >= 2) {
      // 拖第 1 个绿点 -> 最后一个节点
      const a = await outs[0].boundingBox();
      const b = await rects[rects.length - 1].boundingBox();
      if (a && b) {
        await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
        await page.mouse.down();
        await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
        await page.mouse.up();
        await page.waitForTimeout(1500);
        const ptext = (await page.textContent('#df-param').catch(() => '')) || '';
        const accepted = ptext.indexOf('steps.') >= 0;
        // 新规则：只有 write_sheet/write_docx 承接上一步数据；拖到读/算类节点会被明确拒绝，
        // 那也是正确行为。所以这里把"有引用"或"出现拒绝原因"都算通过。
        const refused = /不能直接用第|输入是文件/.test((await page.textContent('body').catch(()=>'')) || '');
        mark('拖拽结果(建立引用或明确拒绝)', accepted || refused);
        await shot('14-drag-link');
      }
    }

    const _pt = (await page.textContent('#df-param').catch(()=>''))||'';
    console.log('---- 参数面板全文 ----'); console.log(_pt.trim().slice(0,600));
    mark('页面报错总数', errors.length);
  } catch (e) {
    console.log('  ✗ SCRIPT_ERROR: ' + e.message.split('\n')[0]);
    try { await shot('98-script-error'); } catch (_) {}
  } finally {
    console.log('---- 捕获到的错误 ----');
    if (!errors.length) console.log('  （无）✓');
    errors.slice(0, 12).forEach(e => console.log('  ' + e));
    await browser.close();
  }
})();
