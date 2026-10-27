#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SGLang 兼容评测：启动/对话/JSON Schema/LoRA（对比 vLLM 基线）"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path('/home/luxy/paper_lora_project')
VENV_SGLANG = Path('/home/luxy/venvs/sglang-eval/bin/python')
VENV_MAIN = Path('/home/luxy/.venv/bin/python')
MODEL_1B = '/home/luxy/models/Qwen2.5-1.5B-Instruct'
ADAPTER = '/home/luxy/loras/workflow_author_v1'
OUT = ROOT / 'data' / 'sglang_compat_eval.json'
LOG = ROOT / 'logs' / 'sglang_compat_eval.log'
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


def http(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode()[:500]
        except Exception:
            b = ''
        return e.code, {'error': b}, round(time.time() - t0, 2)
    except Exception as e:
        return None, {'error': '%s: %s' % (type(e).__name__, str(e)[:200])}, round(time.time() - t0, 2)


def wait_ready(base, proc, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            return False, round(time.time() - t0, 1)
        try:
            st, _, _ = http('GET', base + '/v1/models', timeout=3)
            if st == 200:
                return True, round(time.time() - t0, 1)
        except Exception:
            pass
        time.sleep(2)
    return False, round(time.time() - t0, 1)


def stop_proc(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=20)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    time.sleep(3)


def chat(base, model, prompt, max_tokens=128, response_format=None):
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens, 'temperature': 0}
    if response_format:
        body['response_format'] = response_format
    st, d, dur = http('POST', base + '/v1/chat/completions', body, timeout=180)
    content = ''
    if isinstance(d, dict):
        content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    usage = (d or {}).get('usage') or {}
    return st, content, usage, dur, d


SCHEMA = {'type': 'object', 'properties': {
    'target': {'type': 'string', 'enum': ['local', 'cloud', 'sim']},
    'confidence': {'type': 'number'},
    'reason': {'type': 'string'}},
    'required': ['target', 'confidence', 'reason'], 'additionalProperties': False}
PROMPT_JSON = ('把用户请求路由到 local/cloud/sim 之一。用户：帮我压测 500 并发并拦截脏数据。'
               '只输出 JSON：{"target":...,"confidence":...,"reason":...}')


def run_suite(base, model, tag, lora_model=None):
    r = {'tag': tag}
    st, content, usage, dur, _ = chat(base, model, '只回复两个字：OK', max_tokens=16)
    r['basic'] = {'http': st, 'duration': dur, 'content': content[:40],
                  'completion_tokens': (usage or {}).get('completion_tokens')}
    # 吞吐：128 输出 tokens
    st, content, usage, dur, _ = chat(base, model, '请用 100 字介绍你自己', max_tokens=128)
    ct = (usage or {}).get('completion_tokens') or 0
    r['throughput'] = {'http': st, 'duration': dur, 'completion_tokens': ct,
                       'tokens_per_s': round(ct / dur, 1) if dur else None}
    # JSON schema
    ok, errs = 0, []
    for _ in range(3):
        st, content, usage, dur, _ = chat(base, model, PROMPT_JSON, max_tokens=120,
                                          response_format={'type': 'json_schema',
                                                           'json_schema': {'name': 'route',
                                                                           'schema': SCHEMA}})
        try:
            d = json.loads(content)
            if d.get('target') in ('local', 'cloud', 'sim') and 'confidence' in d:
                ok += 1
            else:
                errs.append(str(d)[:120])
        except Exception as e:
            errs.append('%s | %s' % (str(e)[:80], content[:120]))
    r['json_schema'] = {'ok': ok, 'runs': 3, 'rate': round(ok / 3, 2), 'errors': errs[:2],
                        'supported': st == 200}
    # LoRA（若提供）
    if lora_model:
        st, content, usage, dur, d = chat(base, lora_model, '设计一个采购清单工作流：任务→JSON', max_tokens=200)
        r['lora'] = {'http': st, 'content_head': content[:160],
                     'error': (d or {}).get('error') if isinstance(d, dict) else None}
    return r


def start_sglang(port, lora=False):
    args = [str(VENV_SGLANG), '-m', 'sglang.launch_server',
            '--model-path', MODEL_1B, '--host', '127.0.0.1', '--port', str(port),
            '--mem-fraction-static', '0.55', '--context-length', '4096',
            '--disable-cuda-graph']
    if lora:
        args += ['--enable-lora', '--lora-paths', 'workflow_author=' + ADAPTER,
                 '--max-loras-per-batch', '2']
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='1',
               PATH=str(VENV_SGLANG.parent) + ':/usr/local/bin:/usr/bin:/bin',
               SGLANG_ENABLE_JIT_DEEPGEMM='0', SGLANG_DISABLE_DEEPGEMM='1')
    logf = open(ROOT / 'logs' / ('sglang_%d.log' % port), 'wb')
    return subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env), logf


def start_vllm(port, lora=False):
    args = [str(VENV_MAIN), '-m', 'vllm.entrypoints.openai.api_server',
            '--model', MODEL_1B, '--served-model-name', 'base-15b',
            '--host', '127.0.0.1', '--port', str(port),
            '--max-model-len', '4096', '--gpu-memory-utilization', '0.55',
            '--dtype', 'half', '--enforce-eager']
    if lora:
        args += ['--enable-lora', '--max-loras', '2', '--max-lora-rank', '64',
                 '--lora-modules', 'workflow_author=' + ADAPTER]
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='1', VLLM_USE_FLASHINFER_SAMPLER='0',
               VLLM_ALLOW_RUNTIME_LORA_UPDATING='1',
               PATH=str(VENV_MAIN.parent) + ':/usr/local/bin:/usr/bin:/bin')
    logf = open(ROOT / 'logs' / ('vllm_%d.log' % port), 'wb')
    return subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env), logf


def main():
    import sglang  # noqa: F401
    res = {'sglang_version': getattr(sglang, '__version__', '?'),
           'model': MODEL_1B, 'gpu': 'RTX 3060 (GPU1)', 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    # ① SGLang 基础
    log('启动 SGLang（无 LoRA）…')
    proc, logf = start_sglang(8010)
    ok, dur = wait_ready('http://127.0.0.1:8010', proc, timeout=300)
    res['sglang_startup_s'] = dur
    res['sglang_ready'] = ok
    if ok:
        res['sglang_base'] = run_suite('http://127.0.0.1:8010', 'default', 'sglang-base')
    else:
        tail = ''
        try:
            tail = (ROOT / 'logs' / 'sglang_8010.log').read_bytes()[-1500:].decode(errors='ignore')
        except Exception:
            pass
        res['sglang_error_tail'] = tail
    stop_proc(proc)
    try:
        logf.close()
    except Exception:
        pass

    # ② SGLang + LoRA
    log('启动 SGLang（LoRA）…')
    proc, logf = start_sglang(8010, lora=True)
    ok2, dur2 = wait_ready('http://127.0.0.1:8010', proc, timeout=300)
    res['sglang_lora_startup_s'] = dur2
    res['sglang_lora_ready'] = ok2
    if ok2:
        res['sglang_lora'] = run_suite('http://127.0.0.1:8010', 'base-15b', 'sglang-lora',
                                       lora_model='workflow_author')
    else:
        try:
            res['sglang_lora_error_tail'] = (ROOT / 'logs' / 'sglang_8010.log').read_bytes()[-1500:].decode(errors='ignore')
        except Exception:
            pass
    stop_proc(proc)
    try:
        logf.close()
    except Exception:
        pass

    # ③ vLLM 基线（同样条件）
    log('启动 vLLM 基线…')
    proc, logf = start_vllm(8011)
    okv, durv = wait_ready('http://127.0.0.1:8011', proc, timeout=300)
    res['vllm_startup_s'] = durv
    res['vllm_ready'] = okv
    if okv:
        res['vllm_base'] = run_suite('http://127.0.0.1:8011', 'base-15b', 'vllm-base')
    stop_proc(proc)
    try:
        logf.close()
    except Exception:
        pass

    log('启动 vLLM + LoRA…')
    proc, logf = start_vllm(8011, lora=True)
    okv2, durv2 = wait_ready('http://127.0.0.1:8011', proc, timeout=300)
    res['vllm_lora_startup_s'] = durv2
    res['vllm_lora_ready'] = okv2
    if okv2:
        res['vllm_lora'] = run_suite('http://127.0.0.1:8011', 'base-15b', 'vllm-lora',
                                     lora_model='workflow_author')
    stop_proc(proc)
    try:
        logf.close()
    except Exception:
        pass

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    log('评测结果 → %s' % OUT)
    log(json.dumps({k: res.get(k) for k in ('sglang_ready', 'sglang_startup_s',
                                   'sglang_lora_ready', 'vllm_ready', 'vllm_startup_s')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
