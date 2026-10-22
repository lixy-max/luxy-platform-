// 从 runs.jsonl 生成 part1 完整结果表（50 条）+ 归类统计 ✓
import fs from 'node:fs';

const RUNS = 'E:/WSL/_check/drill/out/runs.jsonl';
const OUT = 'E:/WSL/_check/drill/out/PART1_RESULTS.md';
const rows = fs.readFileSync(RUNS, 'utf8').trim().split('\n').map(l => JSON.parse(l));

// 写操作工具集（与 index3.html 的 AGENT_WRITE_TOOLS 对齐 ✓）
const WRITE = new Set(['add_workflow_node','update_workflow_node','delete_workflow_node',
  'create_workflow_config','use_workflow_config','run_workflow','start_training','build_dataset',
  'run_eval','load_lora','unload_lora','switch_model','write_docx','write_sheet','export_docx',
  'save_doc_flow','run_doc_flow','create_knowledge_base','replace_in_file','replace_text',
  'set_lines','set_lines_in_file','delete_knowledge_base','save_workflow_config','import_workflow_config']);

for (const r of rows) {
  r.tools = [...String(r.msgs || '').matchAll(/✅\s*([a-z_][a-z0-9_]*)/g)].map(m => m[1]);
  r.uniq = [...new Set(r.tools)];
  r.writes = r.uniq.filter(t => WRITE.has(t));
  r.verdict = r.writes.length ? '✓ 有写操作' : (r.uniq.length ? '△ 只读' : '✗ 未动手');
  r.kindShort = /不可能/.test(r.kind || '') ? '不可能' : (/边界/.test(r.kind || '') ? '边界' : '可完成');
}
const n = rows.length;
const pct = (a, b) => `${a}/${b} = ${Math.round(100 * a / Math.max(1, b))}%`;
const esc = rows.filter(r => r.escalated).length;
const hasTool = rows.filter(r => r.uniq.length).length;
const hasWrite = rows.filter(r => r.writes.length).length;
const onlyRead = rows.filter(r => r.uniq.length && !r.writes.length).length;
const noTool = rows.filter(r => !r.uniq.length).length;
const avg = Math.round(rows.reduce((s, r) => s + (r.elapsed_s || 0), 0) / Math.max(1, n));
const by = k => rows.filter(r => r.kindShort === k);

const L = [];
L.push('# 演练 part1 结果（50 条实跑 · 真页面 + 真工具 + 真后端）', '');
L.push('> 跑手: `E:\\WSL\\_check\\drill\\run.mjs`（每条新建会话 ✓ 不挂 LoRA ✓ 学习增强开 ✓ 目标库 test_use_agent ✓）');
L.push('> 工具调用口径: 页面工具卡「✅ 工具名」精确计数 ✓（不看文本措辞 ✗）');
L.push('> 运行时间: 2026-10-15 · 底座 Qwen2.5-7B-Instruct-AWQ · 学习增强超时 90s', '');
L.push('## 一、总览', '');
L.push('| 指标 | 结果 |');
L.push('|---|---|');
L.push(`| 完成 | ${n}/${n} ✓ |`);
L.push(`| **有工具调用** | ${pct(hasTool, n)} |`);
L.push(`| **零工具调用（只说不做）** | ${pct(noTool, n)} ✗ |`);
L.push(`| 只读（读了但没动手） | ${pct(onlyRead, n)} |`);
L.push(`| **有写操作（真的改了东西）** | ${pct(hasWrite, n)} ✗✗ |`);
L.push(`| 云接手（质量闸门 ✓） | ${pct(esc, n)} |`);
L.push(`| 平均耗时 | ${avg}s |`);
for (const k of ['可完成', '边界', '不可能']) {
  const g = by(k);
  if (!g.length) continue;
  L.push(`| ── ${k} ${g.length} 条：有写操作 / 零工具 / 云接手 | ` +
    `${g.filter(r => r.writes.length).length} / ${g.filter(r => !r.uniq.length).length} / ${g.filter(r => r.escalated).length} |`);
}
L.push('', '## 二、逐条明细', '');
L.push('| # | 类别 | 原话（截断） | 耗时 | 工具调用 | 工具名 | 云接手 | 判定 |');
L.push('|---|---|---|---|---|---|---|---|');
for (const r of rows) {
  const ph = String(r.phrase || '').replace(/\|/g, '/').slice(0, 30);
  L.push(`| ${r.id} | ${r.kindShort} | ${ph} | ${r.elapsed_s}s | ${r.tools.length} | ` +
    `${r.uniq.join(' · ') || '—'} | ${r.escalated ? '✓' : ''} | ${r.verdict} |`);
}
L.push('', '## 三、失败模式归类（用实测数据填）', '');
const noToolList = rows.filter(r => !r.uniq.length).map(r => r.id);
const readOnlyList = rows.filter(r => r.uniq.length && !r.writes.length).map(r => r.id);
const writeList = rows.filter(r => r.writes.length).map(r => `${r.id}(${r.writes.join('/')})`);
L.push(`- **A 只说不做（0 工具）${noToolList.length} 条**: ${noToolList.join(' ')}`);
L.push(`- **B 只读不动手 ${readOnlyList.length} 条**: ${readOnlyList.join(' ')}`);
L.push(`- **C 真的动手 ${writeList.length} 条**: ${writeList.join(' ')}`);
L.push(`- **D 云接手 ${esc} 条**: ${rows.filter(r => r.escalated).map(r => r.id).join(' ')}`);
L.push('', '> ★ 结论：A+B = ' + (noToolList.length + readOnlyList.length) + '/' + n +
  '（' + Math.round(100 * (noToolList.length + readOnlyList.length) / n) + '%）都是"没有落到写操作"✗');
L.push('> 这正是 v2 训练要治的第一号病 ✓（详见 `docs/DRILL_FINDINGS.md` F1）', '');

fs.writeFileSync(OUT, L.join('\n'), 'utf8');
console.log(`✓ 生成 ${OUT}（${L.length} 行）`);
console.log(`  总览: 有工具 ${hasTool}/${n} · 零工具 ${noTool}/${n} · 只读 ${onlyRead}/${n} · 有写操作 ${hasWrite}/${n} · 云接手 ${esc}/${n} · 平均 ${avg}s`);
