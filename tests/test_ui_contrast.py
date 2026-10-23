"""UI 对比度回归测试（2026-10-23 实测 bug）

背景（真实事故）:
  用户在知识库导入 3 篇 PDF 后跑「检索评测」，结果区的灰色说明文字
  （`--c-text-3`）完全看不见 —— 底色是浅蓝 `#e7f1ff`（--c-info-bg），
  字是 `rgba(255,255,255,0.40)`（白色 40% 透明），对比度仅 1.05:1。

根因:
  `--c-text-2` / `--c-text-3` / `--c-surface-2` 这三个变量被写成
  **暗色主题专用值**（白色半透明）却放在 `:root`（浅色默认）里，
  且 `[data-theme="dark"]` 没有重映射它们 ⇒ 浅色模式下所有使用处
  （知识库评测/分块预览/GPU 卡片/拖拽区…）白字落在浅色底上不可见。

修复约定（本测试固化）:
  1. `:root` 里的 --c-text-2 / --c-text-3 必须是**深灰**（对浅色底对比度 ≥ 4.5:1，WCAG AA）
  2. 暗色值必须放在 `--c-dark-*` 调色板，并在 `[data-theme="dark"]` 里用 var() 重映射
  3. 暗色块禁止裸 hex（既有约定 m5-dark-block-no-raw-hex 的前端版本）
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "webui" / "index3.html").read_text(encoding="utf-8")

REQUIRED_AA = 4.5  # WCAG AA 正文标准


def _block(selector: str) -> str:
    """取 CSS 块内容（选择器后第一个 { 到配对的 }）。"""
    m = re.search(re.escape(selector) + r"\{(.*?)\n\}", HTML, re.S)
    return m.group(1) if m else ""


def _var(block: str, name: str) -> str:
    m = re.search(re.escape(name) + r":\s*([^;]+);", block)
    return (m.group(1) if m else "").strip()


def _parse_color(css: str):
    """解析 #rgb / #rrggbb / rgb() / rgba() → (r,g,b,a)。"""
    css = css.strip()
    m = re.match(r"#([0-9a-fA-F]{3,6})\b", css)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    m = re.match(r"rgba?\(([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\)", css)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4) or 1.0)
    raise AssertionError("无法解析颜色: %r" % css)


def _luminance(rgb) -> float:
    r, g, b = (v / 255.0 for v in rgb[:3])
    def f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG 对比度（fg 为半透明时先与 bg 做 alpha 混合）。"""
    fr, fg_, fb, fa = _parse_color(fg)
    br, bg_, bb, _ = _parse_color(bg)
    if fa < 1.0:  # 半透明文字混到底色上
        fr = fr * fa + br * (1 - fa)
        fg_ = fg_ * fa + bg_ * (1 - fa)
        fb = fb * fa + bb * (1 - fa)
    l1, l2 = _luminance((fr, fg_, fb)), _luminance((br, bg_, bb))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class UIContrastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = _block(":root")
        cls.dark = _block('[data-theme="dark"]')
        cls.info_bg = _var(cls.root, "--c-info-bg") or "#e7f1ff"
        cls.white = "#ffffff"

    def test_root_block_found(self):
        self.assertTrue(self.root, "index3.html 找不到 :root 块")

    def test_text2_light_is_dark_enough(self):
        v = _var(self.root, "--c-text-2")
        self.assertTrue(v, "缺少 --c-text-2 定义")
        self.assertGreaterEqual(
            contrast(v, self.info_bg), REQUIRED_AA,
            "--c-text-2=%s 在评测面板浅蓝底 %s 上对比度不足 %.2f:1" %
            (v, self.info_bg, contrast(v, self.info_bg)))
        self.assertGreaterEqual(
            contrast(v, self.white), REQUIRED_AA,
            "--c-text-2=%s 在白底上对比度不足 %.2f:1" % (v, contrast(v, self.white)))

    def test_text3_light_is_dark_enough(self):
        v = _var(self.root, "--c-text-3")
        self.assertTrue(v, "缺少 --c-text-3 定义")
        self.assertGreaterEqual(
            contrast(v, self.info_bg), REQUIRED_AA,
            "--c-text-3=%s 在评测面板浅蓝底 %s 上对比度不足 %.2f:1" %
            (v, self.info_bg, contrast(v, self.info_bg)))
        self.assertGreaterEqual(
            contrast(v, self.white), REQUIRED_AA,
            "--c-text-3=%s 在白底上对比度不足 %.2f:1" % (v, contrast(v, self.white)))

    def test_light_values_are_not_white(self):
        """浅色主题变量不允许是白色/白色半透明（这正是本次事故根因）。"""
        for name in ("--c-text-2", "--c-text-3", "--c-surface-2"):
            v = _var(self.root, name)
            self.assertTrue(v, "缺少 %s 定义" % name)
            r, g, b, _ = _parse_color(v)
            self.assertFalse(
                r >= 250 and g >= 250 and b >= 250,
                "%s=%s 是白色 ⇒ 浅色模式下会与浅色底融在一起（本次 bug）" % (name, v))

    def test_dark_theme_remaps_text_and_surface(self):
        for name in ("--c-text-2", "--c-text-3", "--c-surface-2"):
            dark_val = _var(self.dark, name)
            palette_name = "--c-dark-" + name[len("--c-"):]
            self.assertIn(
                "var(%s)" % palette_name, dark_val,
                "暗色主题必须把 %s 重映射到 %s（实际: %r）" % (name, palette_name, dark_val))
            self.assertTrue(_var(self.root, palette_name),
                            "调色板缺少 %s 定义" % palette_name)

    def test_dark_block_no_raw_hex(self):
        raws = re.findall(r"#[0-9a-fA-F]{3,6}\b", self.dark)
        self.assertEqual(raws, [], "暗色块不允许裸 hex（应只用 var 令牌）: %s" % raws)


if __name__ == "__main__":
    unittest.main()
