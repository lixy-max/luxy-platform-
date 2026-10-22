# -*- coding: utf-8 -*-
"""
#5 PDF 解析增强的测试：
  1. 双栏感知抽取（纯函数，用合成词框，不需要真 PDF）
  2. OCR 回退只在"前三方案都拿不到文字"时触发（用 spy 观测）
  3. 用 PIL 现场生成"图片型 PDF"，验证 OCR 真能读出字（缺依赖时自动跳过）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _guards  # noqa: F401  项目约定：测试模块统一装载共享状态守卫
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lora_manager as lm  # noqa: E402


def W(text, x0, x1, top):
    """构造一个 pdfplumber 风格的词框。"""
    return {"text": text, "x0": x0, "x1": x1, "top": top}


class TestColumnDetection(unittest.TestCase):
    """_detect_gutter / text_layout_aware 是纯函数 —— 用合成数据测，不需要真 PDF。"""

    def _two_col_lines(self):
        # 左栏 x 60~280，右栏 x 320~540，中缝约 300
        lines = []
        for i in range(10):
            top = i * 12
            lines.append([W(f"L{i}a", 60, 150, top), W(f"L{i}b", 160, 280, top)])
            lines.append([W(f"R{i}a", 320, 420, top + 6), W(f"R{i}b", 430, 540, top + 6)])
        return lines

    def test_detects_gutter_between_columns(self):
        gutter = lm._detect_gutter(self._two_col_lines(), 600)
        self.assertIsNotNone(gutter, "双栏页应能判出中缝")
        self.assertTrue(280 < gutter < 320, gutter)

    def test_single_column_has_no_gutter(self):
        lines = [[W(f"W{i}", 60, 540, i * 12)] for i in range(10)]
        self.assertIsNone(lm._detect_gutter(lines, 600), "单栏不该判出中缝")

    def test_too_few_words_no_gutter(self):
        lines = [[W("a", 60, 150, 0)], [W("b", 320, 420, 6)]]
        self.assertIsNone(lm._detect_gutter(lines, 600), "词太少不做判断")

    def test_left_column_comes_before_right(self):
        out = lm.text_layout_aware(self._two_col_lines(), 600)
        self.assertIsNotNone(out)
        self.assertLess(out.index("L0a"), out.index("R0a"),
                        "左栏应整体排在右栏之前（否则就是交错）")

    def test_full_width_line_flushes_columns(self):
        lines = self._two_col_lines()
        lines.insert(0, [W("BIG", 60, 540, -20)])   # 通栏标题
        out = lm.text_layout_aware(lines, 600)
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("BIG"), out[:60])
        self.assertLess(out.index("L0a"), out.index("R0a"))

    def test_returns_none_when_no_gutter(self):
        lines = [[W(f"W{i}", 60, 540, i * 12)] for i in range(10)]
        self.assertIsNone(lm.text_layout_aware(lines, 600),
                          "单栏应返回 None，让调用方回退 extract_text")


class TestOcrFallback(unittest.TestCase):
    """OCR 只在必要时触发；必要时真能读出字。"""

    def test_image_only_pdf_gets_ocr(self):
        """图片型 PDF（无文字层）—— 这正是多数论文的形态，必须能读出字。"""
        try:
            from PIL import Image, ImageDraw
            import pypdfium2  # noqa
            import rapidocr_onnxruntime  # noqa
        except Exception as e:
            self.skipTest(f"缺少 OCR 依赖，跳过: {e}")
        called = {"n": 0}
        orig = lm._ocr_pdf

        def spy(path):
            called["n"] += 1
            return orig(path)

        with tempfile.TemporaryDirectory() as d:
            img = Image.new("RGB", (1100, 320), "white")
            dr = ImageDraw.Draw(img)
            dr.text((40, 130), "Attention is all you need 2017", fill="black")
            png = os.path.join(d, "p.png")
            img.save(png)
            pdf = os.path.join(d, "scan.pdf")
            Image.open(png).convert("RGB").save(pdf, "PDF", resolution=200)
            lm._ocr_pdf = spy
            try:
                txt = lm.extract_text_from_pdf(pdf)
            finally:
                lm._ocr_pdf = orig
        low = txt.lower()
        self.assertGreater(called["n"], 0, "图片型 PDF 必须走 OCR")
        self.assertTrue(any(k in low for k in ("attention", "need", "2017")),
                        f"OCR 应读出图上文字，实际: {txt[:200]!r}")


if __name__ == "__main__":
    unittest.main()
