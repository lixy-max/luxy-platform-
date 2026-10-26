# -*- coding: utf-8 -*-
"""wish_test.py —— 许愿测试（闭环验收脚本）

给主脑发真实请求（18 个愿望），检验蜂群 + 奇美拉闭环：
  direct / kb / LoRA 能力 / 外部工具 / 多步 / 奇美拉缺口
用法：
  python scripts/wish_test.py            # 全部 18 个
  python scripts/wish_test.py --round 1  # 第一轮 12 个
  python scripts/wish_test.py --round 2  # 第二轮 6 个
退出码：0=全部通过；1=有失败
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

B = 'http://127.0.0.1:8001'

WISHES1 = [
    ("用两句话解释什么是 RAG（检索增强生成）", ("direct",), ("检索", "RAG", "知识")),
    ("帮我写一段 50 字左右的项目周报", ("direct",), ("周报", "本周", "完成", "项目")),
    ("BCPCN 协议里 Hub 和 Switch 有什么区别？", ("kb", "kb+capability"), ("Hub", "Switch", "广播", "转发")),
    ("平台怎么启动一个 LoRA 训练任务？", ("kb", "kb+capability"), ("start_training", "训练", "任务", "数据集")),
    ("玉米缺氮有什么表现？", ("capability", "kb+capability", "multi"), ("氮", "叶", "黄", "矮")),
    ("劳动合同的违约金条款怎么写才有效？", ("capability", "kb+capability", "multi"), ("违约金", "劳动", "条款", "赔偿")),
    ("把 A=1、B=2 两行数据排成表格（用 tabulate 工具）", ("capability", "multi"), ("|", "A", "B", "表格")),
    ("先查 BCPCN 知识库里路由器是什么，再用农学模型总结成两条要点", ("kb+capability", "multi"), ("路由", "要点", "农")),
    ("帮我从 PDF 里抽取表格数据（本地没有这个能力）", ("chimera", "capability"), ("候选", "pdfplumber", "camelot", "needs_review", "auto_approved", "奇美拉")),
    ("帮我做一个语音转文字的本地工具", ("chimera", "capability"), ("候选", "speech", "语音", "auto_approved", "奇美拉")),
    ("把苹果3、香蕉5的数据用 tabulate 排成表格，并解释这个表说明什么", ("multi", "capability"), ("苹果", "香蕉", "表", "说明")),
    ("用不存在的天气工具帮我查明天成都天气", ("direct", "chimera"), ("天气", "成都", "无法", "没有")),
]

WISHES2 = [
    ("帮我从 PDF 抽表格，选一个最省事的开源方案并给出安装计划",
     ("chimera",), ("候选", "pdfplumber", "camelot", "gate", "needs_review", "auto_approved")),
    ("把 BCPCN 知识库里路由器的内容用农学模型总结，再用 tabulate 排成表格",
     ("multi", "kb+capability"), ("路由", "表", "|")),
    ("先查平台怎么训练 LoRA，再调用农学 LoRA 给一个施肥建议示例",
     ("multi", "kb+capability"), ("训练", "start_training", "施肥", "氮")),
    ("把这个项目的三件事排成表格：知识库隔离、动态库、能力注册表",
     ("capability", "multi"), ("|", "知识库", "动态", "能力")),
    ("我需要一个能识别图片里文字的工具，本地没有，给个方案",
     ("chimera",), ("候选", "OCR", "ocr", "auto_approved", "needs_review")),
    ("用 tabulate 把 [['季度','销量'],['Q1',100],['Q2',150]] 渲染成 grid 表格",
     ("capability", "multi"), ("|", "季度", "Q1", "Q2")),
]


def _post(payload, timeout=600):
    req = urllib.request.Request(B + '/chat/completions', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def run_wish(task):
    t0 = time.time()
    try:
        d = _post({'auto_orchestrate': True, 'messages': [{'role': 'user', 'content': task}]})
        return d, time.time() - t0, None
    except urllib.error.HTTPError as e:
        return {}, time.time() - t0, 'HTTP %d' % e.code
    except Exception as e:
        return {}, time.time() - t0, '%s: %s' % (type(e).__name__, str(e)[:120])


def run(wishes, label):
    print('══ %s（%d 个愿望）══' % (label, len(wishes)))
    rows = []
    for i, (task, exp, kws) in enumerate(wishes, 1):
        d, dur, err = run_wish(task)
        route = d.get('route') or 'ERR'
        content = str(d.get('content') or '')
        ok = (route in exp) and any(str(k).lower() in content.lower() for k in kws) and not err
        rows.append({"i": i, "task": task, "route": route, "expected": list(exp),
                     "duration": round(dur, 2), "ok": ok, "error": err,
                     "content": content[:300],
                     "meta": {k: v for k, v in (d.get('meta') or {}).items()
                              if k in ('steps', 'capability_id', 'kind', 'hits', 'plan')}})
        print('  [%s] %02d route=%-14s %5.1fs 期望=%s' % (
            '✓' if ok else '✗', i, route, dur, ','.join(exp)))
        if not ok:
            print('       内容: %s' % content[:160].replace('\n', ' '))
    passed = sum(1 for r in rows if r['ok'])
    print('  通过: %d/%d = %.1f%% | 平均 %.1fs' % (
        passed, len(rows), passed / len(rows) * 100, sum(r['duration'] for r in rows) / len(rows)))
    return rows, passed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--round', choices=['1', '2', 'all'], default='all')
    ap.add_argument('--out', default='/tmp/wish_final.json')
    args = ap.parse_args()
    all_rows = []
    if args.round in ('1', 'all'):
        rows, _ = run(WISHES1, '第一轮')
        all_rows.extend(rows)
    if args.round in ('2', 'all'):
        rows, _ = run(WISHES2, '第二轮')
        all_rows.extend(rows)
    total = len(all_rows)
    passed = sum(1 for r in all_rows if r['ok'])
    print('\n══ 总计 ══')
    print('  通过: %d/%d = %.1f%%' % (passed, total, passed / total * 100 if total else 0))
    try:
        open(args.out, 'w', encoding='utf-8').write(json.dumps(all_rows, ensure_ascii=False, indent=2))
        print('  结果: %s' % args.out)
    except Exception:
        pass
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
