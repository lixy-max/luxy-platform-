import json, sys, importlib.util, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
spec = importlib.util.spec_from_file_location('ds', str(__import__('pathlib').Path(__file__).resolve().parent / 'drill_score.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
t1 = {t['id']: t for t in D.parse_tasks(str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'tests/fixtures/DRILL_TASKS_PART1.md'))}
_t2list = D.parse_tasks(str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'tests/fixtures/DRILL_TASKS_PART2.md'))
_inherit = D.inherit_pairs(_t2list, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'tests/fixtures/DRILL_TASKS_PART1.md'))  # ★ 2026-10-19: part2 缺类目 ✓ 从同号继承 ✓
t2 = {t['id']: t for t in _t2list}
runs = [json.loads(l) for l in open('/tmp/runs_p1p2.jsonl', encoding='utf-8') if l.strip()]
by = {}
for r in runs:
    by.setdefault(r.get('tag'), {}).setdefault(r['id'], []).append(r)
def sc(tag, tasks, tid):
    rs = by.get(tag, {}).get(tid) or []
    if not rs or tid not in tasks: return None
    return D.score_machine(tasks[tid], D._latest(rs))
pairs = []
for i in range(1, 51):
    a, b = f'P1-{i:02d}', f'P2-{i:02d}'
    sa, sb = sc('p1full', t1, a), sc('p2final', t2, b)
    if sa and sb:
        pairs.append({'p1': a, 'p2': b, 's1': sa['machine_score'], 's2': sb['machine_score'],
                      'e1': len(D.executed_tools(by['p1full'][a][-1])),
                      'e2': len(D.executed_tools(by['p2final'][b][-1])),
                      'imp': t1[a]['is_impossible'], 'bnd': t1[a]['is_boundary'],
                      's1d': sa['detail'], 's2d': sb['detail']})
print(f'  配对 {len(pairs)}/50')
comp = [p for p in pairs if not p['imp'] and not p['bnd']]
print(f'  【可完成】{len(comp)} 对')
if comp:
    m1 = sum(p['s1'] for p in comp)/len(comp); m2 = sum(p['s2'] for p in comp)/len(comp)
    e1 = sum(p['e1'] for p in comp)/len(comp); e2 = sum(p['e2'] for p in comp)/len(comp)
    diffs = [p['s2']-p['s1'] for p in comp]
    print(f'  P1 均分 {m1:.1f} · P2 均分 {m2:.1f} · 差 {m2-m1:+.1f}')
    print(f'  P1 平均执行工具 {e1:.1f} · P2 {e2:.1f}')
    print(f'  逐对差: stdev {statistics.stdev(diffs):.1f} · 中位 {statistics.median(diffs):+.1f}')
    print(f'  |差|>15 的有 {len([d for d in diffs if abs(d)>15])} 对')
    down = [p for p in comp if p['s2']-p['s1'] < -15]
    up = [p for p in comp if p['s2']-p['s1'] > 15]
    print('  大幅下降:', ', '.join(f"{p['p1']}→{p['p2']}({p['s1']:.0f}→{p['s2']:.0f})" for p in down) or '无 ✓')
    print('  大幅上升:', ', '.join(f"{p['p1']}→{p['p2']}({p['s1']:.0f}→{p['s2']:.0f})" for p in up) or '无 ✓')
    # 还手率: P1 做对而 P2 做错 / 反之
    a_ok = sum(1 for p in comp if p['s1'] >= 42); b_ok = sum(1 for p in comp if p['s2'] >= 42)
    both_ok = sum(1 for p in comp if p['s1'] >= 42 and p['s2'] >= 42)
    both_bad = sum(1 for p in comp if p['s1'] < 42 and p['s2'] < 42)
    print(f'  合规(≥42): P1 {a_ok}/{len(comp)} · P2 {b_ok}/{len(comp)} · 都对 {both_ok} · 都错 {both_bad} · 只对一次 {len(comp)-both_ok-both_bad}')
json.dump(pairs, open('/tmp/pairs.json', 'w'), ensure_ascii=False)
