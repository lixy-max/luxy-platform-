#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S24 验收：能力基线库 20 任务判定 + KB 分层挂载 + Manifest 吸收 + decide 集成"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s24_acceptance.json")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RESULTS = []


def call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:600]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, round((time.time() - t0) * 1000)


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "pass": bool(ok), "detail": str(detail)[:400]})
    print("%s %-40s %s" % ("✅" if ok else "❌", name, str(detail)[:120]))


TASKS = [
    ("把这份表格按单价排序并导出 Excel", "local"),
    ("读取本地 Excel 采购清单并汇总", "local"),
    ("生成采购清单（来源本地供应商清单）", "local"),
    ("做一份供应商比价表", "local"),
    ("把结果输出成 json 结构化字段", "local"),
    ("识别这句话的意图并分类", "local"),
    ("分析一下这篇论文的方法", "local"),
    ("把合同和法规逐条比对给出风险", "cloud"),
    ("农业作物病虫害怎么防治", "local"),
    ("医疗指南：儿童发热怎么处理", "local"),
    ("用500并发压测路由系统", "sim"),
    ("注入脏数据测试知识库隔离", "sim"),
    ("搭建一个采购工作流", "local"),
    ("训练一个 LoRA 模型", "local"),
    ("读取 PDF 并抽取字段", "local"),
    ("分析这张图片里的内容", "cloud"),
    ("查一下2026最新论文并分析趋势", "local"),
    ("联网搜索今天的新闻", "local"),
    ("读取供应商清单 Excel", "local"),
    ("写一份岩土工程勘察报告", "unknown"),
]

MANIFEST = {
    "manifest_version": "1.0", "capability_id": "tool.supplier_compare_probe.v1",
    "name": "供应商比价能力包（验收样例）", "type": "tool",
    "source": {"provider": "github", "repo": "demo/supplier", "license": "Apache-2.0"},
    "interface": {"protocol": "openapi", "version": "1.0",
                  "io_schema": {"input": "xlsx", "output": "xlsx"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": ["pandas"],
                "gpu": {"vram_gb": 0}, "disk_gb": 1},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": ["supplier_list.xlsx"], "sensitivity": "medium"},
    "license": "Apache-2.0",
    "tests": {"smoke": ["import ok"], "eval": {"metric": "accuracy", "threshold": 0.8}},
    "fallback": {"strategy": "cloud"},
}


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "classification": []}

    # ① 能力基线库 seed + 规模
    st, seed, _ = call("POST", "/hive/capability/seed", {})
    st, stats, _ = call("GET", "/hive/capability/stats")
    check("能力基线库 seed", st == 200 and stats.get("total", 0) >= 80,
          "total=%s kinds=%s" % (stats.get("total"), stats.get("by_kind")))
    res["seed"] = seed
    res["stats"] = stats

    # ② 20 任务判定
    ok = 0
    for task, exp in TASKS:
        st, p, _ = call("GET", "/hive/capability/query?task=" + urllib.request.quote(task))
        got = p.get("verdict")
        hit = got == exp
        ok += hit
        res["classification"].append({"task": task, "expect": exp, "got": got,
                                      "coverage": p.get("coverage"),
                                      "gap_tags": p.get("gap_tags"), "hit": hit})
        print(("✅" if hit else "❌"), "%-38s got=%-8s exp=%-8s gaps=%s"
              % (task[:36], got, exp, p.get("gap_tags")))
    acc = ok / len(TASKS)
    check("20 任务判定准确率 ≥85%", acc >= 0.85, "%d/20 = %.0f%%" % (ok, acc * 100))
    res["accuracy"] = acc

    # ③ KB 分层挂载 + ACL
    st, ctx1, _ = call("GET", "/hive/kb/context?task=" + urllib.request.quote(
        "生成采购清单，来源本地供应商清单") + "&worker=w1")
    c = ctx1.get("context") or {}
    mount = c.get("mount") or {}
    l1 = mount.get("l1") or []
    data_ids = mount.get("data") or []
    check("KB 分层：L0/L0.5 必读", "L0" in mount.get("must_read", [])
          and "L0.5" in mount.get("must_read", []),
          "must_read=%s" % mount.get("must_read"))
    check("KB 分层：采购域 L1 挂载", "qiaojiao_toys_2025_v2" in l1, "L1=%s" % l1)
    check("L3 数据源（供应商清单）", "local_supplier_list" in data_ids, "data=%s" % data_ids)
    st, ctx2, _ = call("GET", "/hive/kb/context?task=" + urllib.request.quote(
        "生成采购清单") + "&worker=no_worker")
    c2 = ctx2.get("context") or {}
    dropped = (c2.get("mount") or {}).get("dropped_by_acl") or []
    data2 = (c2.get("layers") or {}).get("L3_data", {}).get("sources") or []
    check("工蜂 ACL 隔离", bool(dropped) and not data2, "dropped=%s data=%s" % (dropped, data2))
    res["kb_context"] = {"w1": c, "no_worker": c2}

    # ④ Manifest 校验/评分/吸收
    st, val, _ = call("POST", "/hive/manifest/validate", {"manifest": MANIFEST})
    score = val.get("score") or {}
    check("Manifest 校验+兼容评分", st == 200 and score.get("decision") == "direct",
          "score=%s decision=%s" % (score.get("score"), score.get("decision")))
    st, ab, _ = call("POST", "/hive/manifest/absorb", {"manifest": MANIFEST, "dry_run": False})
    check("Manifest 吸收→注册 shadow", ab.get("ok") and ab.get("registered", {}).get("ok"),
          "decision=%s path=%s" % (ab.get("decision"), ab.get("manifest_path")))
    res["manifest"] = {"validate": val, "absorb": ab}
    st, lst, _ = call("GET", "/hive/manifest/list")
    check("Manifest 落盘可列出", any(m.get("capability_id") == MANIFEST["capability_id"]
                                    for m in (lst.get("manifests") or [])),
          "count=%s" % len(lst.get("manifests") or []))

    # ⑤ decide 集成（capability_plan 出现在响应）
    st, dec, wall = call("POST", "/hive/decide",
                         {"request": "把表格按单价排序并导出 Excel", "use_llm": True},
                         timeout=300)
    cp = dec.get("capability_plan") or {}
    meta = dec.get("meta") or {}
    check("decide 集成 capability_plan", cp.get("verdict") == "local"
          and meta.get("capability_verdict") == "local",
          "verdict=%s coverage=%s %.1fs" % (cp.get("verdict"), cp.get("coverage"), wall / 1000))
    res["decide"] = {"capability_plan": cp, "meta": {
        k: meta.get(k) for k in ("capability_verdict", "capability_coverage", "gap_tags")}}

    # ⑥ 数据源注册
    st, ds, _ = call("POST", "/hive/kb/data_sources",
                     {"source": {"id": "s24_probe_src", "name": "S24 验收数据源",
                                 "type": "excel", "path": "uploads/",
                                 "schema": {"columns": ["a", "b"]}, "acl": ["w1"],
                                 "sensitivity": "low"}})
    st, dslist, _ = call("GET", "/hive/kb/data_sources?worker=w1")
    check("L3 数据源注册", ds.get("ok") and any(
        d.get("id") == "s24_probe_src" for d in (dslist.get("sources") or [])),
        "sources=%s" % len(dslist.get("sources") or []))
    res["data_sources"] = dslist

    passed = sum(1 for r in RESULTS if r["pass"])
    totaln = len(RESULTS)
    print("\n=== S24 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
