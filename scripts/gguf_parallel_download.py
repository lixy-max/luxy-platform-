#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多连接 range 下载 GGUF（hf-mirror 单连接慢，4+ 连接可到 40MB/s）"""
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

URL = "https://hf-mirror.com/mradermacher/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B.Q3_K_M.gguf"
OUT = Path("/home/luxy/models/Qwen3.8-27B-GGUF/Qwen3.8-27B.Q3_K_M.gguf")
SIZE = 13500737248  # bytes (from HF API)
CHUNK = 64 * 1024 * 1024
THREADS = 8
LOG = Path("/home/luxy/paper_lora_project/logs/gguf_parallel_download.log")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_range(start, end, part_path, idx):
    for attempt in range(6):
        try:
            req = urllib.request.Request(URL, headers={"Range": "bytes=%d-%d" % (start, end),
                                                       "User-Agent": "curl/8.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(part_path, "wb") as f:
                while True:
                    buf = r.read(1024 * 1024)
                    if not buf:
                        break
                    f.write(buf)
            got = os.path.getsize(part_path)
            if got == (end - start + 1):
                return
            log("  分段 %d 大小不符 %d/%d，重试" % (idx, got, end - start + 1))
        except Exception as e:
            log("  分段 %d 第 %d 次失败: %s" % (idx, attempt + 1, str(e)[:120]))
            time.sleep(3)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists() and OUT.stat().st_size == SIZE:
        log("已存在完整文件: %s" % OUT)
        return
    parts_dir = OUT.parent / ".parts"
    parts_dir.mkdir(exist_ok=True)
    chunks = []
    pos = 0
    idx = 0
    while pos < SIZE:
        end = min(pos + CHUNK - 1, SIZE - 1)
        chunks.append((idx, pos, end))
        idx += 1
        pos = end + 1
    log("总大小 %.2f GB, 分 %d 段, 线程 %d" % (SIZE / 1e9, len(chunks), THREADS))

    pending = [c for c in chunks
               if not ((parts_dir / ("p%d" % c[0])).exists()
                       and (parts_dir / ("p%d" % c[0])).stat().st_size == c[2] - c[1] + 1)]
    log("待下载分段: %d / %d" % (len(pending), len(chunks)))

    lock = threading.Lock()
    done = [0]
    t0 = time.time()

    def worker(q):
        while True:
            with lock:
                if not q:
                    return
                idx, s, e = q.pop(0)
            fetch_range(s, e, parts_dir / ("p%d" % idx), idx)
            with lock:
                done[0] += 1
                if done[0] % 5 == 0 or done[0] == len(pending):
                    mc = done[0] * CHUNK / 1e6
                    el = max(1, time.time() - t0)
                    log("进度 %d/%d 段, %.1f MB/s" % (done[0], len(pending), mc / el))

    q = list(pending)
    ths = [threading.Thread(target=worker, args=(q,), daemon=True) for _ in range(THREADS)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    log("合并分段…")
    with open(OUT, "wb") as out:
        for idx, s, e in chunks:
            pp = parts_dir / ("p%d" % idx)
            with open(pp, "rb") as f:
                out.write(f.read())
    ok = OUT.stat().st_size == SIZE
    log("完成: %s (%d bytes, 期望 %d, %s)" % (OUT, OUT.stat().st_size, SIZE, "OK" if ok else "大小不符"))
    if ok:
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
