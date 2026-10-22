#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档处理任务测试台（可重复运行）。

用法：
    python tools/doc_task_battery.py            # 跑一轮
    python tools/doc_task_battery.py --rounds 3 # 跑三轮，逐轮汇总

设计要点（都是踩过坑后定的）：
  · **负向用例**（空文本 / 不存在的文件 / 越权目录…）用 expect_raise 标记，
    抛出预期异常记 PASS —— 早期版本把"抛出异常"一律记 FAIL，于是 5 个正确行为被误报为失败。
  · 正向用例的断言写成函数，断言失败记 CHECK（行为与预期不同，需要人判断），
    抛非预期异常才记 FAIL。三者区分开，报告才不会自欺。
  · 产物统一落在 E:\\project test desktop\\doc_tests\\，可直接打开核对。
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lora_manager as lm  # noqa: E402

T = lm.DOC_FLOW_TOOLS
DST = "E:\\project test desktop\\doc_tests"
OUT = Path("/mnt/e/project test desktop/doc_tests")
ROUND = {"n": 0}


def run(results, name, fn, expect=None, expect_raise=False):
    t0 = time.time()
    try:
        r = fn()
        ms = int((time.time() - t0) * 1000)
        if expect_raise:
            results.append((name, "FAIL", ms, "预期报错但成功了"))
        elif expect is None:
            results.append((name, "PASS", ms, "（无断言，仅执行）"))
        elif expect(r):
            results.append((name, "PASS", ms, json.dumps(r, ensure_ascii=False)[:110]))
        else:
            results.append((name, "CHECK", ms, json.dumps(r, ensure_ascii=False)[:110]))
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        msg = f"{type(e).__name__}: {str(e)[:110]}"
        if expect_raise:
            results.append((name, "PASS", ms, "按预期报错 -> " + msg))
        else:
            results.append((name, "FAIL", ms, msg))


def extra_round(results, tag):
    """扩充用例：往返校验、六工具分块、硬切路径、Excel 往返、命名冲突、保真与混排。

    为什么加这些：前面的用例只验证"调用成功"，但用户真正关心的是
    **写出去的内容对不对、读回来是否一致、长文是否真的分了块**。
    """
    dst = DST
    out = OUT

    # ---------- F. 往返校验（写出去 -> 读回来，比对内容）----------
    def rt_docx():
        T["create_docx"]({"title": f"往返_{tag}", "out_dir": dst, "name": f"rt_{tag}.docx",
                          "paragraphs": "第一段：项目背景。\n第二段：方法与数据。\n第三段：结论。"})
        # 读回时必须给 out_dir：文件在 E 盘，而 read 工具按设计只搜
        # uploads / outputs / 指定目录 / 项目内（不会全盘扫 E 盘 —— 那太慢）
        return T["read_docx"]({"filename": f"rt_{tag}.docx", "out_dir": dst})
    run(results, f"F1 Word 往返：读回内容含写入的三段 [{tag}]", rt_docx,
        lambda r: all(k in (r.get("text") or "") for k in ("项目背景", "方法与数据", "结论")))

    def rt_sheet():
        T["create_sheet"]({"name": f"rt_{tag}.xlsx", "out_dir": dst, "sheet": "数据",
                           "headers": ["名称", "数量"], "rows": [["甲", 10], ["乙", 20]]})
        # 用**文档流程工具**读取（低层 read_sheet_summary 只认 uploads 里的名字，
        # 读不到写到 E 盘的产物 —— 上一版就是踩了这个）
        return T["read_sheet"]({"filename": f"rt_{tag}.xlsx",
                                "sheet": "数据", "rows": 10, "out_dir": dst})
    run(results, f"F2 Excel 往返：读回表头与两行 [{tag}]", rt_sheet,
        lambda r: ("名称" in str(r.get("header") or r)) and ("甲" in json.dumps(r, ensure_ascii=False)))

    run(results, f"F3 表格写入 Word（write_docx 带 table）[{tag}]",
        lambda: T["write_docx"]({"name": f"tbl_{tag}.docx", "title": "带表格",
                                 "paragraphs": "见下表。",
                                 "table": [["部门", "金额"], ["市场", 250], ["研发", 180]],
                                 "out_dir": dst}),
        lambda r: r.get("table_rows") == 3 and Path(r.get("path") or "/x").exists())

    # ---------- G. 六工具全覆盖分块（长文）----------
    # 分块断言要成立，输入必须**明显**超过 chunk_chars（默认 2500）。
    # 这个错我犯了三次（估算字数总是不准：1300 / 4028 / 4588 字）。
    # 现在改成"拼到够长为止"，不再依赖任何估算。
    _sent = ("第 {i} 节。本节讨论项目背景、方法、数据与结论。为了让每段都足够长，这里补充较长的"
             "描述性文字，说明该阶段的起因、目标、输入、产出、风险点与后续计划，并给出必要的"
             "举例与解释，使整段明显超过分块阈值，从而确保分块逻辑真的被触发。")
    _paras = []
    while len("\n\n".join(_paras)) < 7000:
        _paras.append(_sent.format(i=len(_paras) + 1))
    L = "\n\n".join(_paras)
    assert len(L) >= 7000, f"分块用例的输入太短（{len(L)} 字），断言会误报"
    print(f"    （分块用例输入 {len(L)} 字 / {len(_paras)} 段，阈值 2500 -> 预期多块）")
    run(results, f"G1 translate 分块 [{tag}]",
        lambda: T["translate"]({"text": L, "target": "英文"}),
        lambda r: r.get("chunks", 0) >= 2)
    run(results, f"G2 polish 分块 [{tag}]",
        lambda: T["polish"]({"text": L}), lambda r: r.get("chunks", 0) >= 2)
    run(results, f"G3 proofread 分块 [{tag}]",
        lambda: T["proofread"]({"text": L}), lambda r: r.get("chunks", 0) >= 2)
    run(results, f"G4 structure_fix 分块 [{tag}]",
        lambda: T["structure_fix"]({"text": L}), lambda r: r.get("chunks", 0) >= 2)
    run(results, f"G5 condense 分块(reduce) [{tag}]",
        lambda: T["condense"]({"text": L}), lambda r: r.get("chunks", 0) >= 2)
    run(results, f"G6 terminology 分块(reduce) [{tag}]",
        lambda: T["terminology"]({"text": L}), lambda r: r.get("chunks", 0) >= 2)

    # ---------- H. 硬切路径与特殊输入 ----------
    hard = "这是一整段没有任何空行的超长文字。" * 400      # 约 6400 字，无段落边界
    run(results, f"H1 无空行长文（走句号硬切）[{tag}]",
        lambda: T["translate"]({"text": hard, "target": "英文", "chunk_chars": 1000}),
        lambda r: r.get("chunks", 0) >= 2)
    run(results, f"H2 中英混排翻译 [{tag}]",
        lambda: T["translate"]({"text": "本项目使用 LoRA 与 vLLM 推理，token 成本下降 76%。",
                                "target": "英文"}),
        lambda r: "LoRA" in r["text"] or "lora" in r["text"].lower())
    run(results, f"H3 emoji 与特殊符号不崩 [{tag}]",
        lambda: T["format_fix"]({"text": "完成 ✅ 进度 80% ⚠️ 注意：① ② ③   ——测试"}),
        lambda r: "✅" in r["text"] and "80%" in r["text"])
    run(results, f"H4 纯空白输入应报错 [{tag}]",
        lambda: T["polish"]({"text": "   \n\n\t  "}), expect_raise=True)
    run(results, f"H5 超长文件名 [{tag}]",
        lambda: T["create_docx"]({"title": "长名", "out_dir": dst, "paragraphs": "x",
                                  "name": ("很长的文件名" * 12) + ".docx"}),
        None)

    # ---------- I. 保真与一致性 ----------
    run(results, f"I1 condense 必须保留数字 [{tag}]",
        lambda: T["condense"]({"text": "2026 年营收 1234 万元，同比增长 56%。成本 789 万元。"
                                       "利润 445 万元。这些数字必须保留。" * 3}),
        lambda r: sum(1 for n in ("1234", "56", "789", "445") if n in r["text"]) >= 2)
    run(results, f"I2 同名创建（观察是否覆盖）[{tag}]",
        lambda: (T["create_docx"]({"title": "同名", "out_dir": dst, "paragraphs": "第一次",
                                   "name": f"dup_{tag}.docx"}),
                 T["create_docx"]({"title": "同名", "out_dir": dst, "paragraphs": "第二次",
                                   "name": f"dup_{tag}.docx"}),
                 T["read_docx"]({"filename": f"dup_{tag}.docx", "out_dir": dst}))[2],
        None)
    run(results, f"I3 目录幂等性（加两次）[{tag}]",
        lambda: (T["create_docx"]({"title": "幂等", "out_dir": dst, "name": f"idem_{tag}.docx",
                                   "sections": ["一、A", "二、B"]}),
                 T["add_toc"]({"filename": f"idem_{tag}.docx", "out_dir": dst}),
                 T["add_toc"]({"filename": f"idem_{tag}_带目录.docx", "out_dir": dst}))[2],
        lambda r: r.get("placed_first") is True)

    # ---------- J. 多配置/多格式 ----------
    run(results, f"J1 一次创建 3 个小节的文档并核对标题 [{tag}]",
        lambda: (T["create_docx"]({"title": "多节", "out_dir": dst, "name": f"multi_{tag}.docx",
                                   "sections": ["一、甲", "二、乙", "三、丙"]}),
                 T["read_docx"]({"filename": f"multi_{tag}.docx", "out_dir": dst}))[1],
        lambda r: all(k in (r.get("text") or "") for k in ("甲", "乙", "丙")))
    run(results, f"J2 输出目录为相对路径（应被安全处理）[{tag}]",
        lambda: T["create_docx"]({"title": "相对", "paragraphs": "x", "out_dir": "outputs/subdoc"}),
        lambda r: Path(r["path"]).exists())
    return results


def one_round():
    results = []
    R = ROUND["n"]
    tag = f"r{R}"

    # A 从 0 创建
    run(results, f"A1 create_docx 中文标题+4小节 [{tag}]",
        lambda: T["create_docx"]({"title": f"季度总结_{tag}", "out_dir": DST,
                                  "name": f"季度总结_{tag}.docx",
                                  "sections": ["一、概览", "二、数据", "三、问题", "四、计划"]}),
        lambda r: Path(r["path"]).exists() and r["sections"] == 4)
    run(results, f"A2 create_sheet 表头+3行 [{tag}]",
        lambda: T["create_sheet"]({"name": f"台账_{tag}.xlsx", "out_dir": DST, "sheet": "台账",
                                   "headers": ["日期", "事项", "金额", "状态"],
                                   "rows": [["2026-10-01", "采购", 1200, "完成"],
                                            ["2026-10-02", "差旅", 800, "进行中"],
                                            ["2026-10-03", "培训", 0, "计划"]]}),
        lambda r: r["rows"] == 3 and r["cols"] == 4 and Path(r["path"]).exists())
    run(results, f"A3 文件名含空格与中文 [{tag}]",
        lambda: T["create_docx"]({"title": "带 空格 标题", "out_dir": DST,
                                  "name": f"测试 文档 {tag}.docx", "paragraphs": "第一段。\n第二段。"}),
        lambda r: Path(r["path"]).exists())
    run(results, f"A4 只给标题（应报错）[{tag}]",
        lambda: T["create_docx"]({"title": "空", "out_dir": DST}), expect_raise=True)

    # B 目录
    run(results, f"B1 add_toc 目录须在最前 [{tag}]",
        lambda: T["add_toc"]({"filename": f"季度总结_{tag}.docx", "out_dir": DST}),
        lambda r: r.get("placed_first") is True and r["headings"] == 4)
    run(results, f"B2 重复加目录（观察）[{tag}]",
        lambda: T["add_toc"]({"filename": f"季度总结_{tag}_带目录.docx", "out_dir": DST}))

    # C 语言处理
    messy = "这是一段   有很多空格的文字 。它还有半角标点,比如这个,和这个."
    run(results, f"C1 format_fix 空白/标点/合句 [{tag}]",
        lambda: T["format_fix"]({"text": messy}),
        lambda r: "  " not in r["text"] and "，" in r["text"])
    run(results, f"C2 proofread 错别字 [{tag}]",
        lambda: T["proofread"]({"text": "我们需要按装新的服务器。既使预算紧张，也要保证进度。"}),
        lambda r: len(r["text"]) > 5)
    run(results, f"C3 polish 口语不应变文言 [{tag}]",
        lambda: T["polish"]({"text": "这个东西吧，就是有点那个，反正你懂的，我们得弄一下它才行。"}),
        lambda r: not any(w in r["text"] for w in ("然则", "此事", "之", "矣", "焉")))
    run(results, f"C4 condense 出摘要 [{tag}]",
        lambda: T["condense"]({"text": "本季度我们完成了大量工作。" * 30}),
        lambda r: ("摘要" in r["text"]) or bool(r.get("digest")))
    run(results, f"C5 terminology 统一称谓 [{tag}]",
        lambda: T["terminology"]({"text": "公司今年增长快。企业要扩产。集团已批预算。"}),
        lambda r: len(r["text"]) > 5)
    run(results, f"C6 translate 中译英 [{tag}]",
        lambda: T["translate"]({"text": "本项目旨在降低云端 token 消耗。", "target": "英文"}),
        lambda r: sum(1 for c in r["text"] if c.isascii() and c.isalpha()) > 10)
    # 注意：这里必须真的超过 chunk_chars（默认 2500），否则只会走单块、
    # 断言 chunks>=2 必然失败 —— 早期版本输入仅约 1300 字，三轮都被误判成 CHECK。
    long_input = "\n\n".join(
        f"第 {i} 节。这是用于测试分块的长文本内容，涉及项目背景、方法、数据与结论。"
        f"为了让每段足够长，这里补充一些描述性文字，说明该阶段的目标、输入、产出与风险点。"
        for i in range(40))
    run(results, f"C7 长文分块翻译（40 段 / {len(long_input)} 字）[{tag}]",
        lambda: T["translate"]({"text": long_input, "target": "英文"}),
        lambda r: r.get("chunks", 0) >= 2 and r["chars"] > 200)

    # D 边界
    run(results, f"D1 空文本应报错 [{tag}]", lambda: T["proofread"]({"text": ""}), expect_raise=True)
    run(results, f"D2 文件不存在应报错 [{tag}]",
        lambda: T["add_toc"]({"filename": "根本没这个.docx", "out_dir": DST}), expect_raise=True)
    run(results, f"D3 xlsx 当正文读应报错 [{tag}]",
        lambda: lm.uploads_text("按部门汇总.xlsx"), expect_raise=True)
    run(results, f"D4 out_dir 越权应报错 [{tag}]",
        lambda: T["create_docx"]({"title": "x", "paragraphs": "y", "out_dir": "/etc"}),
        expect_raise=True)

    # E 端到端（含导出必须真实存在 —— 这里曾是"谎报成功"的 bug）
    def e2e():
        T["create_docx"]({"title": f"端到端_{tag}", "out_dir": DST, "name": f"e2e_{tag}.docx",
                          "sections": ["一、背景", "二、结论"]})
        b = T["add_content"]({"text": "端到端测试文档。", "topic": "测试流程", "paragraphs": 1})
        c = T["format_fix"]({"text": b["text"]})
        d = T["add_toc"]({"filename": f"e2e_{tag}.docx", "out_dir": DST})
        e = T["write_docx"]({"name": f"e2e_result_{tag}.docx", "title": "结果",
                             "paragraphs": c["text"], "out_dir": DST})
        return {"toc_first": d.get("placed_first"), "written": e.get("filename"),
                "exported": e.get("exported"), "exists": Path(e.get("path") or "/x").exists()}
    run(results, f"E1 端到端 + 导出必须真实存在 [{tag}]", e2e,
        lambda r: r["exists"] and r.get("exported") is True and r["toc_first"] is True)

    # 扩充用例（往返校验 / 六工具分块 / 硬切 / 保真 / 多格式）
    extra_round(results, tag)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    allr = []
    for i in range(1, args.rounds + 1):
        ROUND["n"] = i
        print(f"\n================ 第 {i} 轮 ================")
        res = one_round()
        allr += res
        c = Counter(s for _, s, _, _ in res)
        print(f"  本轮：PASS {c.get('PASS',0)} / CHECK {c.get('CHECK',0)} / FAIL {c.get('FAIL',0)}")
        for n, s, ms, d in res:
            if s != "PASS":
                print(f"    [{s}] {n}  {ms}ms  {d}")
    print("\n" + "=" * 80)
    c = Counter(s for _, s, _, _ in allr)
    print(f"合计 {len(allr)} 项：PASS {c.get('PASS',0)} / CHECK {c.get('CHECK',0)} / FAIL {c.get('FAIL',0)}")
    if c.get("FAIL"):
        print("失败清单:")
        for n, s, ms, d in allr:
            if s == "FAIL":
                print(f"  ✗ {n}  {d}")
    if OUT.exists():
        print(f"\nE 盘产物目录: {OUT}（{len(list(OUT.iterdir()))} 个文件）")
    return 0 if not c.get("FAIL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
