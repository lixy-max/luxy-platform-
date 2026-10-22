// 自动重跑包装器（P-60 加固 · 2026-10-19）
// 用法: node run_retry.mjs <run.mjs 的原始参数...>  （原样透传 ✓）
// 逻辑: 跑一批 → 扫该 tag 的 placeholder 行 → 重跑这些任务（最多 2 轮）→ 仍失败才放弃
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const OUT_DIR = 'E:/WSL/_check/drill/out';
const RUNS = path.join(OUT_DIR, 'runs.jsonl');
const args = process.argv.slice(2);
const getArg = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const TAG = (getArg('--tag', 'drill') || 'drill').trim();

function readTag() {
  const latest = new Map();
  try {
    for (const line of fs.readFileSync(RUNS, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      let r; try { r = JSON.parse(line); } catch (e) { continue; }
      if (r.tag === TAG) {
        const prev = latest.get(r.id);
        // 有效的覆盖无效的；同有效性取后写 ✓
        if (!prev || !r.placeholder || prev.placeholder) latest.set(r.id, r);
      }
    }
  } catch (e) {}
  return latest;
}

function runOnce(extraArgs, label) {
  console.log(`\n[retry] ===== ${label} =====`);
  const r = spawnSync(process.execPath, [path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), 'run.mjs'), ...args, ...extraArgs],
                      { stdio: 'inherit' });
  return r.status === 0;
}

runOnce([], '第 1 轮');
for (let round = 1; round <= 2; round++) {
  const recs = readTag();
  const bad = [...recs.values()].filter(r => r.placeholder).map(r => r.id);
  const total = recs.size;
  console.log(`\n[retry] 第 ${round} 轮检查: 记录 ${total} 条 · 占位行 ${bad.length} 条${bad.length ? ' → ' + bad.join(',') : ' ✓'}`);
  if (!bad.length) break;
  if (bad.length > 40) { console.log('[retry] 占位行过多（>40）⇒ 疑似环境问题，停止重跑 ✗'); break; }
  runOnce(['--ids', bad.join(',')], `重跑 ${bad.length} 条（第 ${round} 轮）`);
}
const fin = readTag();
const stillBad = [...fin.values()].filter(r => r.placeholder).length;
const valid = fin.size - stillBad;
console.log(`\n[retry] 完成: 有效 ${valid}/${fin.size} 条 · 仍占位 ${stillBad} 条`);
process.exit(stillBad > 0 ? 3 : 0);
