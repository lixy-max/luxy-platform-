#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3.8-27B GGUF 单卡可用性测试：thinking 开关 / 路由 JSON / 中文质量"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path('/home/luxy/paper_lora_project')
GGUF = '/home/luxy/models/Qwen3.8-27B-GGUF/Qwen3.8-27B.Q3_K_M.gguf'
BIN = '/home/luxy/llama.cpp/llama-b11913/llama-server'
OUT = ROOT / 'data' / 'qwen38_llamacpp_quality.json'
LOG = ROOT / 'logs' / 'qwen38_llamacpp_quality.log'
PORT = 8017
os.environ['LD_LIBRARY_PATH'] = ('/home/luxy/llama.cpp/llama-b11913:'
                                 '/home/luxy/llama.cpp/cudart-llama-b11913-bin-ubuntu-cuda-13.4-x64:'
                                 '/usr/lib/wsl/lib:'
                                 + os.environ.get('LD_LIBRARY_PATH', ''))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def http(method, url, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode()[:800]
        except Exception:
            b = ''
        return e.code, {'error': b}, round(time.time() - t0, 2)
    except Exception as e:
        return None, {'error': '%s: %s' % (type(e).__name__, str(e)[:300])}, round(time.time() - t0, 2)


def wait_ready(proc, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        st, _, _ = http('GET', 'http://127.0.0.1:%d/v1/models' % PORT, timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def chat(prompt, n=256, thinking=False):
    body = {'model': 'qwen38-27b', 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': n, 'temperature': 0,
            'chat_template_kwargs': {'enable_thinking': thinking}}
    st, d, dur = http('POST', 'http://127.0.0.1:%d/v1/chat/completions' % PORT, body)
    msg = ((d.get('choices') or [{}])[0].get('message') or {})
    content = msg.get('content') or ''
    reasoning = msg.get('reasoning_content') or ''
    usage = (d or {}).get('usage') or {}
    ct = usage.get('completion_tokens') or 0
    return {'http': st, 'duration': dur, 'completion_tokens': ct,
            'tokens_per_s': round(ct / dur, 1) if dur else None,
            'content': content[:800], 'reasoning_head': reasoning[:200],
            'content_len': len(content), 'error': (d or {}).get('error')}


def main():
    res = {'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    args = [BIN, '-m', GGUF, '-a', 'qwen38-27b', '--host', '127.0.0.1', '--port', str(PORT),
            '-ngl', '99', '-c', '4096', '-t', '6', '--no-webui', '--jinja', '-fa', 'on']
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='0')
    log('启动 llama-server …')
    logf = open(ROOT / 'logs' / ('llamacpp_q_%d.log' % PORT), 'wb')
    proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env)
    t0 = time.time()
    ok = wait_ready(proc)
    res['ready'] = ok
    res['startup_s'] = round(time.time() - t0, 1)
    log('ready=%s startup=%ss' % (ok, res['startup_s']))
    if ok:
        # 1) 非 thinking：中文回答
        r1 = chat('用一句话介绍你自己', 128, thinking=False)
        log('非thinking: http=%s tokens=%s %.2fs = %s tok/s | content_len=%d' % (
            r1['http'], r1['completion_tokens'], r1['duration'], r1['tokens_per_s'], r1['content_len']))
        log('  content: %s' % r1['content'][:120].replace('\n', ' '))
        # 2) thinking 默认
        r2 = chat('9.11 和 9.9 哪个大？只回答结论', 256, thinking=True)
        log('thinking: http=%s tokens=%s %.2fs = %s tok/s | content_len=%d reasoning_len=%d' % (
            r2['http'], r2['completion_tokens'], r2['duration'], r2['tokens_per_s'],
            r2['content_len'], len(r2.get('reasoning_head') or '')))
        # 3) 路由 JSON 质量
        prompt = ('你是蜂巢中枢调度器。把用户请求路由到 local/cloud/sim 之一，只输出 JSON：'
                  '{"target":"local|cloud|sim","confidence":0.0,"reason":"..."}。'
                  '用户请求：用 500 并发压测路由系统并注入脏数据。')
        r3 = chat(prompt, 256, thinking=False)
        raw = r3['content'].strip()
        parsed = None
        try:
            s = raw[raw.find('{'):raw.rfind('}') + 1]
            parsed = json.loads(s)
        except Exception as e:
            parsed = {'_parse_error': str(e)[:120]}
        log('路由JSON: http=%s tokens=%s %.2fs | parsed=%s' % (
            r3['http'], r3['completion_tokens'], r3['duration'],
            json.dumps(parsed, ensure_ascii=False)[:200]))
        res.update({'no_thinking': r1, 'thinking': r2, 'route': r3,
                    'route_parsed': parsed})
    try:
        proc.terminate()
        proc.wait(timeout=30)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    logf.close()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    log('结果 → %s' % OUT)
    log('DONE')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('FATAL %s: %s' % (type(e).__name__, str(e)[:500]))
        raise
