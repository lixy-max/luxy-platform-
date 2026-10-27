#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3.8-27B AWQ-INT4 单卡启动测试（16GB 5060Ti）：能否起 + 速率"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VENV = Path('/home/luxy/venvs/sglang-eval/bin')
PY = VENV / 'python'
MODEL = '/home/luxy/models/Qwen3.8-27B-AWQ-INT4-text'
ROOT = Path('/home/luxy/paper_lora_project')
OUT = ROOT / 'data' / 'qwen38_singlecard_eval.json'
LOG = ROOT / 'logs' / 'qwen38_singlecard.log'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def http(method, url, body=None, timeout=60):
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


def wait_ready(proc, port, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False, round(time.time() - t0, 1)
        st, _, _ = http('GET', 'http://127.0.0.1:%d/v1/models' % port, timeout=3)
        if st == 200:
            return True, round(time.time() - t0, 1)
        time.sleep(3)
    return False, round(time.time() - t0, 1)


def vram():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total',
                              '--format=csv,noheader'], capture_output=True, text=True).stdout
        return out.strip().replace('\n', ' | ')
    except Exception:
        return '?'


def chat(port, prompt, max_tokens=128, enable_thinking=False):
    body = {'model': 'qwen38-27b',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens, 'temperature': 0,
            'chat_template_kwargs': {'enable_thinking': enable_thinking}}
    st, d, dur = http('POST', 'http://127.0.0.1:%d/v1/chat/completions' % port, body, timeout=600)
    content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    usage = (d or {}).get('usage') or {}
    ct = usage.get('completion_tokens') or 0
    return {'http': st, 'duration': dur, 'completion_tokens': ct,
            'tokens_per_s': round(ct / dur, 1) if dur else None,
            'content_head': content[:160],
            'error': (d or {}).get('error') if isinstance(d, dict) else None}


def launch(port, offload_gb=None, mem_frac=0.80, ctx=4096, extra=None):
    args = [str(PY), '-m', 'sglang.launch_server',
            '--model-path', MODEL, '--served-model-name', 'qwen38-27b',
            '--host', '127.0.0.1', '--port', str(port),
            '--quantization', 'compressed-tensors',
            '--context-length', str(ctx),
            '--mem-fraction-static', str(mem_frac),
            '--max-running-requests', '1',
            '--disable-cuda-graph', '--disable-radix-cache']
    if offload_gb is not None:
        args += ['--cpu-offload-gb', str(offload_gb)]
    if extra:
        args += extra
    # PATH 里可能含带空格的 Windows 路径 → 给子进程用干净 PATH
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='0',
               PATH=str(VENV) + ':/usr/local/bin:/usr/bin:/bin',
               # ★ sm_120(5060Ti) 上 DeepGEMM JIT 会崩 → 关闭
               SGLANG_ENABLE_JIT_DEEPGEMM='0',
               SGLANG_DISABLE_DEEPGEMM='1',
               SGLANG_JIT_DEEPGEMM_PRECOMPILE='0',
               CUDA_DEVICE_MAX_CONNECTIONS='1')
    logf = open(ROOT / 'logs' / ('qwen38_sglang_%d.log' % port), 'wb')
    log('启动: offload=%sGB mem_frac=%s ctx=%s' % (offload_gb, mem_frac, ctx))
    log('命令: ' + ' '.join(args))
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


def tail_log(port, n=1200):
    p = ROOT / 'logs' / ('qwen38_sglang_%d.log' % port)
    try:
        return p.read_bytes()[-n:].decode(errors='ignore')
    except Exception:
        return ''


def main():
    res = {'model': MODEL, 'gpu': 'RTX 5060 Ti 16GB (single card)',
           'quant': 'AWQ-INT4 (compressed-tensors)', 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    log('模型: %s' % MODEL)

    # 尝试 0：单卡不 offload（预期权重 17.65GB > 16GB → OOM）
    proc, logf = launch(8012, offload_gb=None, mem_frac=0.85)
    ok, dur = wait_ready(proc, 8012, timeout=420)
    res['no_offload'] = {'ready': ok, 'startup_s': dur,
                        'error_tail': '' if ok else tail_log(8012)[-1500:]}
    stop(proc, logf)
    log('尝试0（无 offload）: ready=%s startup=%ss' % (ok, dur))
    if ok:
        r = [chat(8012, '用一句话介绍你自己', 128) for _ in range(3)]
        res['no_offload']['runs'] = r
        stop(proc, logf)

    # 尝试 1：cpu-offload-gb 6
    proc, logf = launch(8012, offload_gb=6, mem_frac=0.80)
    ok2, dur2 = wait_ready(proc, 8012, timeout=900)
    res['offload6'] = {'ready': ok2, 'startup_s': dur2, 'vram': vram(),
                      'error_tail': '' if ok2 else tail_log(8012)[-2000:]}
    log('尝试1（offload 6GB）: ready=%s startup=%ss vram=%s' % (ok2, dur2, vram()))
    if ok2:
        runs = []
        for i in range(3):
            r = chat(8012, '用一句话介绍你自己', 128)
            runs.append(r)
            log('  run%d: http=%s tokens=%s %.1fs = %s tok/s' % (
                i, r['http'], r['completion_tokens'], r['duration'], r['tokens_per_s']))
        r256 = chat(8012, '请用大约 150 字介绍一下蜂巢架构', 256)
        log('  run256: http=%s tokens=%s %.1fs = %s tok/s' % (
            r256['http'], r256['completion_tokens'], r256['duration'], r256['tokens_per_s']))
        res['offload6']['runs_128'] = runs
        res['offload6']['run_256'] = r256
        res['offload6']['vram_after'] = vram()
    stop(proc, logf)

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    log('结果 → %s' % OUT)
    log('DONE')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log('FATAL %s: %s' % (type(e).__name__, str(e)[:500]))
        raise
