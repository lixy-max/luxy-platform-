#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3.8-27B GGUF 单卡测试（SGLang --quantization gguf，16GB 5060Ti）"""
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
MODEL_DIR = Path('/home/luxy/models/Qwen3.8-27B-GGUF')
ROOT = Path('/home/luxy/paper_lora_project')
OUT = ROOT / 'data' / 'qwen38_gguf_singlecard_eval.json'
LOG = ROOT / 'logs' / 'qwen38_gguf_singlecard.log'
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


def gpu_mem():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total',
                              '--format=csv,noheader'], capture_output=True, text=True).stdout
        return out.strip().replace('\n', ' | ')
    except Exception:
        return '?'


def chat(port, prompt, max_tokens=128, thinking=False):
    body = {'model': 'qwen38-27b',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens, 'temperature': 0,
            'chat_template_kwargs': {'enable_thinking': thinking}}
    st, d, dur = http('POST', 'http://127.0.0.1:%d/v1/chat/completions' % port, body)
    content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    usage = (d or {}).get('usage') or {}
    ct = usage.get('completion_tokens') or 0
    return {'http': st, 'duration': dur, 'completion_tokens': ct,
            'tokens_per_s': round(ct / dur, 1) if dur else None,
            'content_head': content[:200],
            'error': (d or {}).get('error') if isinstance(d, dict) else None}


def launch(port, offload_gb=None, mem_frac=0.88, ctx=4096):
    model_path = str(MODEL_DIR / 'Qwen3.8-27B.Q3_K_M.gguf')
    args = [str(PY), '-m', 'sglang.launch_server',
            '--model-path', model_path, '--served-model-name', 'qwen38-27b',
            '--host', '127.0.0.1', '--port', str(port),
            '--quantization', 'gguf',
            '--context-length', str(ctx),
            '--mem-fraction-static', str(mem_frac),
            '--max-running-requests', '1',
            '--disable-cuda-graph', '--disable-radix-cache']
    if offload_gb is not None:
        args += ['--cpu-offload-gb', str(offload_gb)]
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='0',
               PATH=str(VENV) + ':/usr/local/bin:/usr/bin:/bin',
               SGLANG_ENABLE_JIT_DEEPGEMM='0', SGLANG_DISABLE_DEEPGEMM='1',
               SGLANG_JIT_DEEPGEMM_PRECOMPILE='0')
    logf = open(ROOT / 'logs' / ('qwen38_gguf_sglang_%d.log' % port), 'wb')
    log('启动: offload=%s mem_frac=%s ctx=%s' % (offload_gb, mem_frac, ctx))
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


def tail(port, n=1800):
    try:
        return (ROOT / 'logs' / ('qwen38_gguf_sglang_%d.log' % port)).read_bytes()[-n:].decode(errors='ignore')
    except Exception:
        return ''


def main():
    res = {'model': str(MODEL_DIR / 'Qwen3.8-27B.Q3_K_M.gguf'),
           'quant': 'GGUF Q3_K_M (13.5GB)', 'gpu': 'RTX 5060 Ti 16GB',
           'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    log('模型: %s' % res['model'])

    for label, off in (('no_offload', None), ('offload2', 2)):
        proc, logf = launch(8014, offload_gb=off)
        ok, dur = wait_ready(proc, 8014, timeout=900)
        key = label
        res[key] = {'ready': ok, 'startup_s': dur,
                    'error_tail': '' if ok else tail(8014)}
        log('%s: ready=%s startup=%ss gpu=%s' % (label, ok, dur, gpu_mem()))
        if ok:
            runs = []
            for i in range(3):
                r = chat(8014, '用一句话介绍你自己', 128)
                runs.append(r)
                log('  run%d: http=%s tokens=%s %.1fs = %s tok/s' % (
                    i, r['http'], r['completion_tokens'], r['duration'], r['tokens_per_s']))
            r256 = chat(8014, '请用大约 150 字介绍蜂巢架构', 256)
            log('  run256: http=%s tokens=%s %.1fs = %s tok/s' % (
                r256['http'], r256['completion_tokens'], r256['duration'], r256['tokens_per_s']))
            # 路由 JSON 质量抽查
            rjson = chat(8014, '把请求路由到 local/cloud/sim 之一。用户：用500并发压测并拦截脏数据。'
                               '只输出 JSON：{"target":..,"confidence":..}', 160)
            res[key]['runs_128'] = runs
            res[key]['run_256'] = r256
            res[key]['route_json'] = rjson
            res[key]['gpu_after'] = gpu_mem()
            stop(proc, logf)
            break
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
