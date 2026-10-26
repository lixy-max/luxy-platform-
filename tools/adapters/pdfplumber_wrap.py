# -*- coding: utf-8 -*-
"""pdfplumber 适配器：PDF 文本/表格抽取（奇美拉 V2 直装后由沙箱执行器调用）

被 sandbox_runner 以 module="_luxy_wrappers.pdfplumber_wrap", function="extract" 调用。
"""
import json


def extract(pdf_path, max_pages=10, want_tables=True, want_text=True):
    """抽取 PDF 文本与表格；返回 JSON 友好 dict"""
    import pdfplumber
    out = {"pdf": str(pdf_path), "pages": 0, "tables": [], "text_preview": ""}
    texts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        out["pages"] = len(pdf.pages)
        n = min(len(pdf.pages), max(1, int(max_pages)))
        for i in range(n):
            page = pdf.pages[i]
            if want_text:
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t.strip()[:1200])
            if want_tables:
                try:
                    for tb in page.extract_tables() or []:
                        if tb:
                            out["tables"].append({"page": i + 1,
                                                  "rows": [[("" if c is None else str(c))[:200] for c in row]
                                                           for row in tb[:40]]})
                except Exception:
                    pass
    out["text_preview"] = "\n\n".join(texts)[:3000]
    return out


def __self_test__():
    import pdfplumber  # noqa: F401
    return {"ok": True, "module": pdfplumber.__name__,
            "version": getattr(pdfplumber, "__version__", "")}
