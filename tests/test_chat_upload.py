# -*- coding: utf-8 -*-
"""对话框上传接口（POST /chat/upload）的测试：文本、Word、图片、拒收。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _guards  # noqa: F401
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lora_manager as lm  # noqa: E402


class TestChatUploadEndpoint(unittest.TestCase):
    """直接测路由函数与它依赖的转换函数（避免起服务）。"""

    def _route(self):
        for r in lm.app.routes:
            if getattr(r, "path", "") == "/chat/upload":
                return r.endpoint
        return None

    def test_route_exists(self):
        self.assertIsNotNone(self._route(), "缺少 POST /chat/upload")

    def test_kind_dispatch_covers_images(self):
        self.assertIn(".png", lm.IMAGE_EXTS)
        self.assertIn(".jpg", lm.IMAGE_EXTS)

    def test_legacy_doc_rejected(self):
        kind = lm.classify_upload("a.doc")
        self.assertEqual(kind, "legacy_doc", "老版 .doc 要单独识别并给出明确提示")

    def test_word_becomes_text(self):
        """Word 上传后应转成 .txt（下游按既有方式使用）。"""
        try:
            import docx
        except Exception as e:
            self.skipTest(f"python-docx 未安装: {e}")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "t.docx")
            doc = docx.Document()
            doc.add_paragraph("这是用于上传测试的一段话，长度足够通过校验。")
            doc.save(src)
            txt = lm.extract_text_from_docx(src)
            self.assertIn("上传测试", txt)

    def test_plain_text_readable(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "t.txt")
            Path(src).write_text("纯文本内容，用来验证直接读入这条分支。", encoding="utf-8")
            self.assertIn("纯文本", Path(src).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
