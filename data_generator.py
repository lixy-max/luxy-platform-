#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据用户需求，用指定的 LLM provider 生成 ShareGPT 训练数据"""
import os
import re
import sys
import json
import time
import hashlib
import requests
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROVIDER_TYPE = os.environ.get("DATA_GEN_PROVIDER", "vllm")
PROVIDER_CONFIG = json.loads(os.environ.get("DATA_GEN_PROVIDER_CONFIG", "{}"))


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _as_str(v):
    """把可能是 dict/list/None 的值统一转成字符串"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


# ============ vLLM 查询 ============
def get_vllm_model_info(api_base, preferred_model=None):
    try:
        r = requests.get(f"{api_base}/models", timeout=5)
        if r.status_code != 200:
            return preferred_model, None
        models = r.json().get("data", [])
        if not models:
            return preferred_model, None
        chosen = None
        if preferred_model:
            for m in models:
                if m["id"] == preferred_model:
                    chosen = m
                    break
        if not chosen:
            for m in models:
                if m["id"].startswith("/"):
                    chosen = m
                    break
        if not chosen:
            chosen = models[0]
        return chosen["id"], chosen.get("max_model_len")
    except Exception:
        return preferred_model, None


def call_vllm(messages, max_tokens, temperature):
    cfg = PROVIDER_CONFIG.get("vllm", {})
    api_base = cfg.get("api_base", "http://127.0.0.1:8000/v1").rstrip("/")
    preferred = cfg.get("model")

    model, max_model_len = get_vllm_model_info(api_base, preferred)
    if not model:
        raise RuntimeError(f"无法从 vLLM 获取可用模型")

    if max_model_len:
        safe_max_tokens = min(max_tokens, max_model_len - 1024)
        if safe_max_tokens < 256:
            safe_max_tokens = max(256, max_model_len // 2)
    else:
        safe_max_tokens = max_tokens

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": safe_max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    r = requests.post(f"{api_base}/chat/completions", json=payload, timeout=600)
    if r.status_code != 200:
        if "chat_template_kwargs" in r.text or "enable_thinking" in r.text or r.status_code == 400:
            payload.pop("chat_template_kwargs", None)
            payload.pop("extra_body", None)
            r = requests.post(f"{api_base}/chat/completions", json=payload, timeout=600)
        if r.status_code != 200:
            raise RuntimeError(f"vLLM 调用失败 {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def call_ollama(messages, max_tokens, temperature):
    cfg = PROVIDER_CONFIG.get("ollama", {})
    api_base = cfg.get("api_base", "http://127.0.0.1:11434").rstrip("/")
    model = cfg.get("model", "qwen2.5:7b")
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": temperature, "num_predict": max_tokens}}
    r = requests.post(f"{api_base}/api/chat", json=payload, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Ollama 调用失败 {r.status_code}: {r.text[:300]}")
    return r.json()["message"]["content"]


def call_openai(messages, max_tokens, temperature):
    cfg = PROVIDER_CONFIG.get("openai", {})
    api_base = cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
    model = cfg.get("model", "gpt-4o-mini")
    api_key = cfg.get("api_key", "")
    if not api_key:
        raise RuntimeError("云端 API Key 未配置")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    r = requests.post(f"{api_base}/chat/completions", json=payload, headers=headers, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"云 API 调用失败 {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def call_llm(messages, max_tokens=4096, temperature=0.8):
    if PROVIDER_TYPE == "vllm":
        return call_vllm(messages, max_tokens, temperature)
    elif PROVIDER_TYPE == "ollama":
        return call_ollama(messages, max_tokens, temperature)
    elif PROVIDER_TYPE == "openai":
        return call_openai(messages, max_tokens, temperature)
    raise RuntimeError(f"未知 provider: {PROVIDER_TYPE}")


# ============ 输出清洗 ============
def strip_thinking(raw):
    if not raw:
        return raw
    raw = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r'```think\b[^`]*```', '', raw, flags=re.DOTALL | re.IGNORECASE)
    if '<think' in raw.lower():
        idx = raw.lower().rfind('</think')
        if idx != -1:
            end_idx = raw.find('>', idx)
            if end_idx != -1:
                raw = raw[end_idx + 1:]
    prefixes = [
        r'^\s*好的[，,]\s*',
        r'^\s*以下是.*?[：:]\s*',
        r'^\s*这里是.*?[：:]\s*',
        r'^\s*根据.*?[，,].*?[：:]\s*',
        r'^\s*以下是.*?数组[：:]\s*',
    ]
    for p in prefixes:
        raw = re.sub(p, '', raw, count=1)
    return raw.strip()


def repair_json_array(s):
    s = s.replace('“', '"').replace('”', '"')
    s = s.replace('‘', "'").replace('’', "'")
    if re.match(r'^\[\s*["\']', s):
        first_colon = s.find(':')
        if first_colon != -1 and first_colon < 200:
            m = re.match(r'^(\[\s*)', s)
            if m:
                s = m.group(1) + '{' + s[m.end():]
    s = re.sub(r',\s*\]', ']', s)
    s = re.sub(r',\s*}', '}', s)
    return s


def parse_json_array(raw, debug=True):
    if not raw:
        if debug:
            log("  ⚠️ 模型返回为空")
        return []

    cleaned = strip_thinking(raw)

    if debug and cleaned != raw:
        log(f"  [debug] 已剥离思考块/前缀，剩余前 150 字符: {cleaned[:150]!r}")
    elif debug:
        log(f"  [debug] 原始输出前 150 字符: {cleaned[:150]!r}")

    cleaned = re.sub(r'```(?:json)?\s*', '', cleaned).replace('```', '')

    start = cleaned.find('[')
    end = cleaned.rfind(']')

    if start == -1:
        if debug:
            log("  ⚠️ 输出中找不到 '['")
        return []
    if end == -1 or end <= start:
        if debug:
            log("  ⚠️ 输出中找不到匹配的 ']'，可能被 max_tokens 截断")
        return []

    json_str = cleaned[start:end + 1]

    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return result
        if debug:
            log(f"  ⚠️ 解析出的是 {type(result).__name__}，不是数组")
        return []
    except json.JSONDecodeError as e:
        if debug:
            log(f"  ⚠️ 首次 JSON 解析失败: {e}")

    repaired = repair_json_array(json_str)
    if debug and repaired != json_str:
        log(f"  [debug] 已尝试修复，前 150 字符: {repaired[:150]!r}")

    try:
        result = json.loads(repaired)
        if isinstance(result, list):
            if debug:
                log(f"  ✅ 修复后解析成功，共 {len(result)} 条")
            return result
        return []
    except json.JSONDecodeError as e:
        if debug:
            log(f"  ❌ 修复后仍失败: {e}")
        return []


def deduplicate(items):
    seen = set()
    out = []
    for it in items:
        instr = _as_str(it.get("instruction", ""))
        outp = _as_str(it.get("output", ""))
        key = hashlib.md5((instr + outp).encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def generate_batch(task_desc, format_hint, batch_size, seed, max_tokens=6144):
    system_prompt = """你是一个训练数据生成专家。你的任务是根据用户的需求，生成高质量的指令-输出训练样本。

输出格式要求：严格输出 JSON 数组，不要任何额外解释。每个元素形如：
{"instruction": "用户问题或指令", "output": "助手应该给出的回答"}

严格规则：
1. 直接输出 JSON 数组本身，第一个字符必须是 [，最后一个字符必须是 ]
2. 不要输出思考过程、不要输出解释、不要输出 Markdown 代码块
3. 不要使用中文引号，只使用英文半角引号 " 和 '
4. 不要在 JSON 前后添加任何文字
5. output 字段必须是字符串，不能是嵌套对象
"""
    user_prompt = f"""请根据以下需求生成 {batch_size} 条训练样本。

【任务需求】
{task_desc}

【输出格式提示】
{format_hint}

【本批次随机种子】
{seed}

直接输出 JSON 数组，第一个字符必须是 [。"""
    raw = call_llm(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        max_tokens=max_tokens, temperature=0.7
    )
    return parse_json_array(raw, debug=True)


def generate_dataset_v1(task_desc, format_hint, total_count, output_file, batch_size=3):
    """旧版生成（Alpaca 格式 ✗ 有 system 位置 bug ✗ 仅保留用于回退 ✓）"""
    log(f"开始生成 {total_count} 条训练数据，每批 {batch_size} 条")
    log(f"Provider: {PROVIDER_TYPE}")

    per_batch_max_tokens = 6144

    all_items = []
    batches = (total_count + batch_size - 1) // batch_size

    for i in range(batches):
        this_batch = min(batch_size, total_count - len(all_items))
        log(f"批次 {i+1}/{batches}：生成 {this_batch} 条...")
        try:
            items = generate_batch(task_desc, format_hint, this_batch, seed=i,
                                   max_tokens=per_batch_max_tokens)
            all_items.extend(items)
            log(f"  ✅ 本批生成 {len(items)} 条，累计 {len(all_items)} 条")
        except Exception as e:
            log(f"  ❌ 批次失败: {e}")
        time.sleep(0.5)

    all_items = deduplicate(all_items)[:total_count]
    log(f"去重后剩余 {len(all_items)} 条")

    conversations = []
    for it in all_items:
        if not isinstance(it, dict):
            continue
        instr = _as_str(it.get("instruction", "")).strip()
        outp = _as_str(it.get("output", "")).strip()
        if not instr or not outp:
            continue
        conversations.append({
            "conversations": [
                {"from": "system", "value": "你是一个专业的助手，请准确、完整地回答用户的问题。"},
                {"from": "human", "value": instr},
                {"from": "gpt", "value": outp}
            ]
        })

    if len(conversations) == 0:
        raise RuntimeError(
            f"数据生成完全失败：0 条有效样本。\n"
            f"Provider: {PROVIDER_TYPE}\n"
            f"请检查上方批次日志里的具体报错和 [debug] 输出。"
        )

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        for c in conversations:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    log(f"数据集已保存: {output_file}（{len(conversations)} 条）")
    return len(conversations)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("用法: python data_generator.py <任务描述> <格式提示> <条数> <输出文件>")
        sys.exit(1)
    generate_dataset(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])

# ══════════════════════════════════════════════════════════
# ★ P-73 / P3（2026-10-19）: 结构化生成 + 强制校验 + 自动修复
#   核心修复: system 是顶层独立字段 ✓（旧代码塞进 conversations[0] ✗）
#   格式规范: 复用 dataset_toolkit（唯一真相 ✓）
# ══════════════════════════════════════════════════════════

# ── 严格 schema 提示词（改造点 A ✓）──
STRUCT_SYSTEM_PROMPT = """你是一个训练数据生成专家。必须**严格**输出下述 JSON 结构，一个字段都不能少、不能改。

RESPONSE_SCHEMA = {
  "samples": [
    {
      "system":   "系统提示（≤80 字符 ✓ 只放角色与纪律 ✓ 越短越好 ✗）",
      "human":    "用户这句话（自然口语 ✓）",
      "tool_calls": [                       // 可为 [] ✓ 不需要工具时留空
        {"name": "工具名（必须在给定清单内）", "arguments": {...}}
      ],
      "observations": [                     // 与 tool_calls 一一对应 ✓
        {...工具返回的 JSON...}
      ],
      "final":    "模型最终回答（有工具调用时要总结结果 ✓）"
    }
  ]
}

## 硬性规则（违反即不合格 ✗）
1. system 是**独立字段** ✗ 不能放进对话里 ✗
2. 对话顺序必须是 human → 每个 tool_call 后紧跟对应 observation → 最后 final ✓
3. tool_calls 里 name 必须在工具清单内 ✓ arguments 必须是 JSON 对象 ✓
4. 不要 markdown 代码块 ✗ 不要解释 ✗ 只输出 JSON ✓
5. system ≤ 400 字符（占整条样本 < 20% ✓ 重要 ✗ 别写长）

## 示例（照这个写 ✓）
{"samples":[{"system":"你是 LoRA 工作台 Agent。需要本机信息先调工具。","human":"最近的结果文件列一下","tool_calls":[{"name":"list_workflow_uploads","arguments":{}}],"observations":[{"files":["a.docx"]}],"final":"最近的结果是 a.docx。"}]}
"""


def build_struct_prompt(task_desc, format_hint, batch_size, seed, tools_hint=""):
    """构建结构化生成提示词（改造点 A ✓）"""
    user = f"""请根据以下需求生成 {batch_size} 条训练样本。

【任务需求】
{task_desc}

【输出格式提示】
{format_hint or "（无额外要求）"}

【可用工具】（如果需求需要工具调用 ✓ 从这些里选 ✓ 不要编造 ✗）
{tools_hint or "（本次不需要工具调用 ⇒ tool_calls/observations 都填 [] ✓）"}

【本批次随机种子】
{seed}

直接输出 JSON 对象（第一个字符必须是 {{ 最后一个字符必须是 }} ✓）。
不要输出 [ ] 数组形式的旧格式 ✗ 必须用 {{"samples":[...]}} ✓。
"""
    return [
        {"role": "system", "content": STRUCT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def repair_llm_json(raw):
    """JSON 修复器（改造点 B ✓）— 处理本地小模型常见输出错误

    修复:
      ① 去 markdown 代码块包裹 ✓
      ② 去前后解释文字 ✓
      ③ 单引号转双引号（只在 JSON 结构里 ✓）
      ④ 尾随逗号 ✓
      ⑤ 截断的 JSON（尝试补全括号 ✓）
    """
    if not raw:
        return ""
    t = str(raw)

    # ① 去 markdown 代码块 ✓
    import re as _re
    t = _re.sub(r'^\s*```(?:json|JSON)?\s*', '', t)
    t = _re.sub(r'\s*```\s*$', '', t)
    t = t.strip()

    # ② 找第一个 { 或 [ ✓ 到最后一个 } 或 ] ✓
    for open_ch, close_ch in (('{', '}'), ('[', ']')):
        i = t.find(open_ch)
        j = t.rfind(close_ch)
        if i >= 0 and j > i:
            t = t[i:j + 1]
            break

    # ③ 尾随逗号 ✓
    t = _re.sub(r',\s*([}\]])', r'\1', t)

    # ④ 中文引号 → 英文引号（只在结构位置 ✓ 简化处理 ✓）
    t = t.replace('“', '"').replace('”', '"')
    t = t.replace('‘', "'").replace('’', "'")

    # ⑤ 截断修复: 括号不平衡时补全 ✓
    try:
        json.loads(t)
        return t
    except Exception:
        pass
    # 统计未闭合括号 ✓
    depth_curly = 0
    depth_square = 0
    in_str = False
    esc = False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth_curly += 1
        elif ch == '}':
            depth_curly = max(0, depth_curly - 1)
        elif ch == '[':
            depth_square += 1
        elif ch == ']':
            depth_square = max(0, depth_square - 1)
    if in_str:
        t += '"'
    t += ']' * depth_square
    t += '}' * depth_curly
    # 再清尾随逗号 ✓
    t = _re.sub(r',\s*([}\]])', r'\1', t)
    return t


def parse_structured(raw, expect_count=None):
    """解析结构化输出 ✓（支持 {"samples":[...]} 或直接数组 ✓）

    Returns:
        (samples: list[dict], meta: dict)
    """
    t = repair_llm_json(raw)
    if not t:
        return [], {"error": "空输出"}
    try:
        d = json.loads(t)
    except Exception as e:
        return [], {"error": "JSON 解析失败: %s" % str(e)[:120]}
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)], {"format": "array"}
    if isinstance(d, dict):
        for k in ("samples", "data", "items", "records"):
            if isinstance(d.get(k), list):
                return [x for x in d[k] if isinstance(x, dict)], {"format": k}
        # 单条 ✓
        if any(k in d for k in ("human", "instruction", "system")):
            return [d], {"format": "single"}
    return [], {"error": "结构不符（既不是数组也不是 samples 对象）"}


def sample_to_sharegpt(sm):
    """把结构化样本 → 标准 sharegpt record ✓（★ 核心修复 ✓）

    ★ 关键: system 提到【顶层独立字段】✗ 不塞进 conversations ✓

    输入: {system?, human, tool_calls?, observations?, final, instruction?, output?}
    输出: {system?, tools?, conversations: [human, function_call, observation, ..., gpt]}
    """
    if not isinstance(sm, dict):
        return None
    out = {}
    # ① system 顶层 ✓
    system = _as_str(sm.get("system", "")).strip()
    if system:
        out["system"] = system

    # ② 兼容旧字段（instruction/output ✓）
    human = _as_str(sm.get("human", "")).strip() or _as_str(sm.get("instruction", "")).strip()
    final = _as_str(sm.get("final", "")).strip() or _as_str(sm.get("output", "")).strip()
    if not human or not final:
        return None

    conv = [{"from": "human", "value": human}]

    # ③ tool_calls + observations ✓
    tcs = sm.get("tool_calls") or []
    obs = sm.get("observations") or []
    if isinstance(tcs, list) and tcs:
        for k, tc in enumerate(tcs):
            if not isinstance(tc, dict):
                continue
            name = _as_str(tc.get("name", "")).strip()
            args = tc.get("arguments")
            if not isinstance(args, dict):
                args = {}
            if not name:
                continue
            conv.append({
                "from": "function_call",
                "value": json.dumps({"name": name, "arguments": args}, ensure_ascii=False),
            })
            # 对应 observation ✓
            ob = obs[k] if (isinstance(obs, list) and k < len(obs)) else {}
            if not isinstance(ob, (dict, list)):
                ob = {"result": ob}
            conv.append({
                "from": "observation",
                "value": json.dumps(ob, ensure_ascii=False),
            })

    # ④ 末位 gpt ✓
    conv.append({"from": "gpt", "value": final})

    # ⑤ ★ P-73/P3: 自动裁剪 system 到占比 < 20% ✓
    #    原因: 生成时常出现 system 长/对话短 ⇒ Q1 不过 ✗
    #    公式: sys/(sys+other) < 0.2 ⇒ sys < other*0.25
    if out.get("system"):
        _other = sum(len(str(c.get("value", ""))) for c in conv)
        _max_sys = max(20, int(_other * 0.25))
        if len(out["system"]) > _max_sys:
            out["system"] = out["system"][:_max_sys].rstrip()

    out["conversations"] = conv
    return out


def validate_record_dz(record):
    """用 dataset_toolkit 校验 ✓（唯一真相 ✓）"""
    try:
        import dataset_toolkit as _dz
        ok, probs = _dz.validate(record, _dz.load_tool_whitelist())
        return ok, probs
    except Exception as e:
        return True, []   # dataset_toolkit 不可用 ⇒ 不阻塞 ✓


def generate_batch_v2(task_desc, format_hint, batch_size, seed, max_tokens=6144, tools_hint=""):
    """新版单批生成 ✓（结构化 + 修复 + 校验 ✓）

    Returns:
        (valid_records, stats)   stats = {parsed, invalid, problems}
    """
    msgs = build_struct_prompt(task_desc, format_hint, batch_size, seed, tools_hint)
    raw = call_llm(msgs, max_tokens=max_tokens, temperature=0.7)
    samples, meta = parse_structured(raw, expect_count=batch_size)
    valid = []
    problems = {}
    for sm in samples:
        rec = sample_to_sharegpt(sm)
        if not rec:
            problems["结构转换失败"] = problems.get("结构转换失败", 0) + 1
            continue
        ok, probs = validate_record_dz(rec)
        if ok:
            valid.append(rec)
        else:
            for pr in probs:
                key = pr.split(":")[0]
                problems[key] = problems.get(key, 0) + 1
    return valid, {
        "raw_len": len(raw or ""),
        "parsed": len(samples),
        "valid": len(valid),
        "problems": problems,
        "meta": meta,
    }


def generate_dataset_v2(task_desc, format_hint, total_count, output_file,
                        batch_size=3, tools_hint="", max_retry=2,
                        fail_rate_limit=0.10, log_fn=None):
    """新版整集生成 ✓（改造点 B 失败率控制 ✓）

    失败率 > fail_rate_limit ⇒ **报错停止** ✗（不静默 ✗ — v3-2-0 的教训 ✓）

    Returns:
        {"ok": bool, "written": int, "stats": {...}}
    """
    logf = log_fn or log
    logf("开始生成 %d 条（结构化模式 ✓ 每批 %d 条）" % (total_count, batch_size))
    logf("Provider: %s" % PROVIDER_TYPE)

    all_records = []
    total_parsed = 0
    total_valid = 0
    all_problems = {}
    batches = (total_count + batch_size - 1) // batch_size

    for i in range(batches):
        this_batch = min(batch_size, total_count - len(all_records))
        if this_batch <= 0:
            break
        logf("批次 %d/%d：目标 %d 条…" % (i + 1, batches, this_batch))
        got = []
        for attempt in range(1 + max_retry):
            try:
                recs, st = generate_batch_v2(task_desc, format_hint, this_batch,
                                             seed=i * 100 + attempt, tools_hint=tools_hint)
                total_parsed += st.get("parsed", 0)
                total_valid += st.get("valid", 0)
                for k, v in (st.get("problems") or {}).items():
                    all_problems[k] = all_problems.get(k, 0) + v
                if recs:
                    got = recs
                    logf("  尝试 %d：解析 %d → 合格 %d ✓" % (attempt + 1, st.get("parsed", 0), st.get("valid", 0)))
                    break
                logf("  尝试 %d：解析 %d → 合格 0 ✗ 重试…" % (attempt + 1, st.get("parsed", 0)))
            except Exception as e:
                logf("  尝试 %d 异常：%s" % (attempt + 1, str(e)[:120]))
        all_records.extend(got)
        logf("  累计 %d/%d 条" % (len(all_records), total_count))
        time.sleep(0.3)

    # ── 失败率检查 ✓（关键 ✓）──
    if total_parsed > 0:
        fail_rate = 1.0 - (total_valid / total_parsed)
    else:
        fail_rate = 1.0
    logf("生成统计：解析 %d · 合格 %d · 失败率 %.1f%%" % (
        total_parsed, total_valid, fail_rate * 100))
    if all_problems:
        logf("问题分布：%s" % all_problems)

    if fail_rate > fail_rate_limit:
        raise RuntimeError(
            "生成失败率 %.1f%% > 阈值 %.0f%% ⇒ 停止（数据质量不过关 ✗ 不静默 ✗）\n"
            "问题分布：%s\n"
            "建议：降低单批条数 / 换更强 Provider / 检查提示词"
            % (fail_rate * 100, fail_rate_limit * 100, all_problems))

    if not all_records:
        raise RuntimeError("生成完全失败：0 条有效样本 ✗")

    # ── 去重 ✓ ──
    try:
        import dataset_toolkit as _dz
        cleaned, cstats = _dz.clean(all_records, {"dedup_exact": True, "fix_format": False})
    except Exception:
        cleaned, cstats = all_records, {"input": len(all_records), "output": len(all_records)}

    # ── 写文件 ✓ ──
    os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        for r in cleaned[:total_count]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    logf("数据集已保存: %s（%d 条 ✓ 结构化模式）" % (output_file, min(len(cleaned), total_count)))
    return {
        "ok": True,
        "written": min(len(cleaned), total_count),
        "stats": {
            "parsed": total_parsed,
            "valid": total_valid,
            "fail_rate": round(fail_rate, 4),
            "problems": all_problems,
            "dedup": cstats,
        },
    }

# ══════════════════════════════════════════════════════════
# ★ P-73 / P3（2026-10-19）: generate_dataset 接线 → 默认走 v2 ✓
# ══════════════════════════════════════════════════════════
def generate_dataset(task_desc, format_hint, total_count, output_file,
                     batch_size=3, use_v2=True, tools_hint="", log_fn=None, **kw):
    """统一入口 ✓

    use_v2=True（默认 ✓）⇒ 结构化生成 + 强制校验 + 失败率控制 ✓
    use_v2=False        ⇒ 旧版（Alpaca ✗ 有 bug ✗ 仅回退用 ✓）
    """
    if use_v2:
        return generate_dataset_v2(
            task_desc, format_hint, total_count, output_file,
            batch_size=batch_size, tools_hint=tools_hint, log_fn=log_fn,
        )
    return generate_dataset_v1(task_desc, format_hint, total_count, output_file, batch_size=batch_size)
