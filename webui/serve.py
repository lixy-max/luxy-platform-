#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态服务（webui）：【禁止缓存】。

为什么需要（真实教训）:
  · 用 `python -m http.server` 时浏览器会按 Last-Modified 缓存 index3.html / *.js
  · 于是【改了前端 → 用户看到的还是旧版本】→ 把已经修好的 bug 当成"还在"
    （实测: 用户报"选完模型还提示请先选择模型"，实际是页面缓存了 value='' 的旧版）
修法: 自己起一个静态服务，对所有响应加 `Cache-Control: no-store`，
      并忽略 If-Modified-Since（不返回 304）。

用法: python webui/serve.py [port]        # 默认 8080
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEBUI = Path(__file__).resolve().parent
ROOT = WEBUI.parent


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # ★ F-4（2026-10-19 前端优化）: 分级缓存 —— 原来全站 no-store ✗ 每次重下 458KB ✗
        #   · HTML（入口）: no-store ✓ 保证改了立刻生效 ✓
        #   · vendor/* / *.js / *.css（带 ?v= 版本 ✓）: 长缓存 ✓ 二次加载 ~0ms ✓
        _path = (self.path or '').split('?')[0]
        if _path.startswith('/vendor/') or _path.endswith(('.js', '.css')):
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        else:
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def send_head(self):
        # 忽略条件请求 ✗ 永远发完整内容（避免 304 让浏览器用旧副本）
        self.headers.replace_header('If-Modified-Since', '') if 'If-Modified-Since' in self.headers else None
        if 'If-None-Match' in self.headers:
            del self.headers['If-None-Match']
        return super().send_head()

    # ★ 2026-10-19: 入口重定向 —— 目录里只有 index3.html ✓
    #   原来 / 会返回【目录列表】✗ /index.html 会 404 ✗
    #   用户用旧书签打开就会以为"前端没同步"✗（真实报障 ✓）
    ENTRY = '/index3.html'

    def _redirect_entry(self):
        self.send_response(302)
        self.send_header('Location', self.ENTRY)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        p = (self.path or '').split('?', 1)[0]
        if p in ('/', '/index.html', '/index.htm', '/index3', '/index3.html/'):
            return self._redirect_entry()
        return super().do_GET()

    def do_HEAD(self):
        p = (self.path or '').split('?', 1)[0]
        if p in ('/', '/index.html', '/index.htm', '/index3', '/index3.html/'):
            self.send_response(302)
            self.send_header('Location', self.ENTRY)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        return super().do_HEAD()

    def log_message(self, fmt, *args):
        pass          # 静音（前端轮询很多，日志没意义）


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    handler = partial(NoCacheHandler, directory=str(WEBUI))
    srv = ThreadingHTTPServer(('0.0.0.0', port), handler)
    print(f'webui 静态服务（no-store）: http://127.0.0.1:{port}/index3.html', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
