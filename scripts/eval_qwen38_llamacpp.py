#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3.8-27B GGUF 单卡测试（llama.cpp llama-server，RTX 5060 Ti 16GB）"""
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
OUT = ROOT / 'data' / 'qwen38_llamacpp_eval.json'
LOG = ROOT / 'logs' / 'qwen38_llamacpp.log'
PORT = 8016
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
            b = e.read().decode()[:1000]
        except Exception:
            b = ''
        return e.code, {'error': b}, round(time.time() - t0, 2)
    except Exception as e:
        return None, {'error': '%s: %s' % (type(e).__name__, str(e)[:300])}, round(time.time() - t0, 2)


def vram():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total',
                              '--format=csv,noheader'], capture_output=True, text=True).stdout
        return out.strip().replace('\n', ' | ')
    except Exception:
        return '?'


def wait_ready(proc, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False, round(time.time() - t0, 1)
        st, _, _ = http('GET', 'http://127.0.0.1:%d/v1/models' % PORT, timeout=3)
        if st == 200:
            return True, round(time.time() - t0, 1)
        time.sleep(3)
    return False, round(time.time() - t0, 1)


def launch(ngl=99, ctx=4096):
    args = [BIN, '-m', GGUF, '-a', 'qwen38-27b', '--host', '127.0.0.1', '--port', str(PORT),
            '-ngl', str(ngl), '-c', str(ctx), '-t', '6', '--no-webui', '--jinja', '-fa', 'on']
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='0')
    log('启动 llama-server: -ngl %s -c %s' % (ngl, ctx))
    logf = open(ROOT / 'logs' / ('llamacpp_%d.log' % PORT), 'wb')
    return subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env), logf


def stop(proc, logf):
    try:
        proc.terminate()
        proc.wait(timeout=30)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        logf.close()
    except Exception:
        pass
    time.sleep(3)


def tail(n=1600):
    try:
        return (ROOT / 'logs' / ('llamacpp_%d.log' % PORT)).read_bytes()[-n:].decode(errors='ignore')
    except Exception:
        return ''


def native(prompt, n_predict=128):
    body = {'prompt': prompt, 'n_predict': n_predict, 'temperature': 0,
            'cache_prompt': False, 'stream': False}
    st, d, dur = http('POST', 'http://127.0.0.1:%d/completion' % PORT, body)
    timings = (d or {}).get('timings') or {}
    content = (d or {}).get('content') or ''
    return {'http': st, 'duration': dur,
            'prompt_per_s': round(timings.get('prompt_per_second') or 0, 1),
            'predicted_per_s': round(timings.get('predicted_per_second') or 0, 1),
            'predicted_n': timings.get('predicted_n'),
            'content_head': content[:200],
            'error': (d or {}).get('error') if isinstance(d, dict) else None}


def openai_chat(prompt, n=128):
    body = {'model': 'qwen38-27b', 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': n, 'temperature': 0}
    st, d, dur = http('POST', 'http://127.0.0.1:%d/v1/chat/completions' % PORT, body)
    content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    usage = (d or {}).get('usage') or {}
    return {'http': st, 'duration': dur, 'usage': usage, 'content_head': content[:200],
            'error': (d or {}).get('error') if isinstance(d, dict) else None}


def main():
    res = {'model': GGUF, 'quant': 'GGUF Q3_K_M 13.5GB', 'gpu': 'RTX 5060 Ti 16GB',
           'runtime': 'llama.cpp b11913 CUDA 13.4', 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    log('模型: %s' % GGUF)
    log('初始 GPU: %s' % vram())

    attempt = None
    for ngl in (99, 60, 45, 30):
        proc, logf = launch(ngl=ngl)
        ok, dur = wait_ready(proc, timeout=600)
        log('  ngl=%d: ready=%s startup=%ss gpu=%s' % (ngl, ok, dur, vram()))
        if ok:
            attempt = ngl
            res['ready'] = True
            res['ngl'] = ngl
            res['startup_s'] = dur
            res['gpu_after'] = vram()
            # 速度
            runs = [native('用一句话介绍你自己', 64) for _ in range(2)]
            run128 = native('请用大约 150 字介绍蜂巢架构', 128)
            run256 = native('请用大约 400 字介绍多 LoRA 动态加载的原理和实现', 256)
            oa = openai_chat('用一句话介绍你自己', 64)
            res.update({'runs64': runs, 'run128': run128, 'run256': run256, 'openai': oa})
            for name, r in (('run64#1', runs[0]), ('run64#2', runs[1]),
                            ('run128', run128), ('run256', run256)):
                log('  %s: http=%s pred=%s tok %.1fs = %.1f tok/s | prompt %.1f tok/s' % (
                    name, r['http'], r['predicted_n'], r['duration'],
                    r['predicted_per_s'], r['prompt_per_s']))
            log('  openai: http=%s %s' % (oa['http'], (oa.get('content_head') or '')[:60]))
            stop(proc, logf)
            break
        stop(proc, logf)
        if attempt is None:
            res.setdefault('fail_tails', []).append({'ngl': ngl, 'tail': tail()})

    if attempt is None:
        res['ready'] = False
        res['gpu_after'] = vram()

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    log('结果 → %s' % OUT)
    log('DONE')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('FATAL %s: %s' % (type(e).__name__, str(e)[:500]))
        raise
