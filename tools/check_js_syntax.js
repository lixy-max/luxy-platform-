#!/usr/bin/env node
// 真正的语法门：把 webui/index3.html 里的每段 <script> 抽出来，用 vm.Script 解析。
// 为什么需要：前端断言只做**源码字符串匹配**，写坏 JS 语法它照样通过 ——
// 本会话就因此把 buildAgentSystemPrompt 的模板字符串弄断（反引号提前闭合），
// 导致整个 agent 模块崩掉、而门禁全绿。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = process.argv[2] || path.join(__dirname, '..', 'webui', 'index3.html');
const html = fs.readFileSync(file, 'utf8');
const blocks = [];
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html))) {
  const attrs = m[1] || '';
  if (/\bsrc\s*=/.test(attrs)) continue;                 // 外部脚本跳过
  if (/type\s*=\s*["']?(application\/json|text\/template)/i.test(attrs)) continue;
  blocks.push({ attrs: attrs.trim(), code: m[2], index: m.index });
}

let bad = 0;
blocks.forEach((b, i) => {
  try {
    new vm.Script(b.code, { filename: `${path.basename(file)}#script${i + 1}` });
    console.log(`  ✓ script#${i + 1} 解析通过（${b.code.length} 字符）`);
  } catch (e) {
    bad++;
    console.log(`  ✗ script#${i + 1} 语法错误: ${e.message}`);
    // 给出出错行在整份 HTML 里的近似行号，便于定位
    const lineInHtml = html.slice(0, b.index).split('\n').length;
    console.log(`      （该 script 块从 HTML 第 ${lineInHtml} 行开始）`);
  }
});
console.log(bad ? `\n✗ 共 ${bad} 段脚本有语法错误` : `\n✓ 全部 ${blocks.length} 段脚本语法正确`);
process.exit(bad ? 1 : 0);
