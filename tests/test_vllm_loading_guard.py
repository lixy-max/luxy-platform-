"""vLLM 启动循环修复的回归测试（2026-10-19 ✓）

背景: keeper 每 20s 探活 ⇒ 加载要 30~90s ⇒ 判"未就绪" ⇒ 调 start ⇒ 内部先 stop
       ⇒ 把正在加载的实例杀掉 ⇒ 死循环 ✗（vllm.log 5 次启动 + leaked semaphore ✓）
修法: 存在【年轻实例（<180s）且端口未通】⇒ 判定加载中 ✓ 不杀 ✗
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vllm_manager as V  # noqa: E402


class LoadingGuardTests(unittest.TestCase):
    def test_grace_constant(self):
        self.assertGreaterEqual(V.LOADING_GRACE_SEC, 120)
        self.assertLessEqual(V.LOADING_GRACE_SEC, 300)

    def test_proc_age_of_self(self):
        import os
        age = V._proc_age_sec(os.getpid())
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)

    def test_proc_age_bad_pid(self):
        self.assertIn(V._proc_age_sec(999999999), (None, 0))

    def test_loading_in_progress_no_process(self):
        # 没有进程时不应误判"加载中" ✗
        real_is_running = V.is_running
        try:
            V.is_running = lambda: (False, None)
            loading, pid = V.loading_in_progress()
            self.assertFalse(loading)
            self.assertIsNone(pid)
        finally:
            V.is_running = real_is_running

    def test_loading_in_progress_young_and_port_down(self):
        import os
        real_is_running, real_port = V.is_running, V.is_port_responding
        try:
            V.is_running = lambda: (True, os.getpid())          # 用自己当"年轻进程" ✓
            V.is_port_responding = lambda port=8000, timeout=1: False
            loading, pid = V.loading_in_progress()
            self.assertTrue(loading, "年轻实例 + 端口未通 ⇒ 应判加载中 ✓")
            self.assertEqual(pid, os.getpid())
        finally:
            V.is_running, V.is_port_responding = real_is_running, real_port

    def test_loading_in_progress_port_up_means_ready(self):
        import os
        real_is_running, real_port = V.is_running, V.is_port_responding
        try:
            V.is_running = lambda: (True, os.getpid())
            V.is_port_responding = lambda port=8000, timeout=1: True
            loading, _ = V.loading_in_progress()
            self.assertFalse(loading, "端口已通 ⇒ 不算加载中 ✓")
        finally:
            V.is_running, V.is_port_responding = real_is_running, real_port


if __name__ == "__main__":
    unittest.main()
