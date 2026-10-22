#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
import random
import requests
import text_metrics as tm
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = SCRIPT_DIR
OUTPUT_DIR = os.path.join(PROJECT_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEFAULT_CONFIG = os.path.join(PROJECT_DIR, "workflow_config.json")

VLLM_CHAT_API = os.environ.get("VLLM_CHAT_API", "http://127.0.0.1:8000/v1/chat/completions")
LORA_MANAGER_API = os.environ.get("LORA_MANAGER_API", "http://127.0.0.1:8001")
ENV_BASE_MODEL = os.environ.get("BASE_MODEL_PATH", "")
# 知识库名的兜底来源：后端在 /workflow/run 时可注入，CLI 也可手动 export
ENV_KB_NAME = os.environ.get("WORKFLOW_KB_NAME", "").strip()

# provider 配置由 lora_manager 通过环境变量注入
try:
    PROVIDER_CONFIGS = json.loads(os.environ.get("WORKFLOW_PROVIDERS", "{}"))
except Exception:
    PROVIDER_CONFIGS = {}

# 全局用量统计
USAGE_STATS = {
    "total_calls": 0,
    "by_provider": {},
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _record_usage(provider, prompt_tokens=0, completion_tokens=0, total_tokens=0):
    USAGE_STATS["total_calls"] += 1
    p = USAGE_STATS["by_provider"].setdefault(provider, {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0
    })
    p["calls"] += 1
    p["prompt_tokens"] += prompt_tokens or 0
    p["completion_tokens"] += completion_tokens or 0
    p["total_tokens"] += total_tokens or (prompt_tokens + completion_tokens or 0)


# ============ vLLM 模型查询 ============
_vllm_models_cache = {"ts": 0, "base": None}


def get_vllm_base_model():
    now = time.time()
    if now - _vllm_models_cache["ts"] < 30 and _vllm_models_cache["base"]:
        return _vllm_models_cache["base"]
    try:
        api_base = VLLM_CHAT_API.rsplit("/v1/", 1)[0]
        r = requests.get(f"{api_base}/v1/models", timeout=5)
        if r.status_code == 200:
            models = r.json().get("data", [])
            for m in models:
                if m["id"].startswith("/"):
                    _vllm_models_cache["ts"] = now
                    _vllm_models_cache["base"] = m["id"]
                    return m["id"]
    except Exception:
        pass
    return ENV_BASE_MODEL


# ============ 文本清洗 ============
def strip_think(raw):
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
        else:
            start_idx = raw.lower().find('<think')
            if start_idx != -1:
                gt_idx = raw.find('>', start_idx)
                if gt_idx != -1:
                    raw = raw[gt_idx + 1:]
    prefixes = [
        r'^\s*好的[，,].*?[：:]\s*',
        r'^\s*以下是.*?[：:]\s*',
        r'^\s*这里是.*?[：:]\s*',
        r'^\s*根据.*?[，,].*?[：:]\s*',
    ]
    for p in prefixes:
        raw = re.sub(p, '', raw, count=1, flags=re.DOTALL)
    if '\n' not in raw and '\\n' in raw:
        raw = raw.replace('\\n', '\n')
    return raw.strip()


def clean_text_output(raw):
    if not raw:
        return ""
    raw = strip_think(raw)
    raw = re.sub(r'```(?:json|markdown|text)?\s*', '', raw)
    raw = raw.replace('```', '')
    return raw.strip()


# ============ LoRA 路由 ============
class WorkflowPreflightError(Exception):
    """工作流"预检未通过"（拓扑排序 / 配置校验 / LoRA 预检）。

    为什么要有这个异常：这三类失败原先直接 `sys.exit(1/2/3)` ——
    那是"命令行脚本"的做法，一旦 run_workflow 被当库函数用
    （后端线程、agent 工具、单元测试），调用方拿到的是**进程/线程退出**，
    而不是一个可以捕获、可以上报、可以恢复的错误。
    实测：在"底座是小模型、工作流引用不兼容 LoRA"时，测试直接吃到 SystemExit: 3。
    """

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = list(details or [])


class LoraRouter:
    def __init__(self):
        self.lock = threading.Lock()      # 并发执行节点时保护 check-then-load
        self.swaps = []                   # 每次真正加载 LoRA 的记录（耗时）
        self.currently_loaded = self._get_loaded()

    def refresh(self):
        """
        以服务端为准重同步快照（成本 1 次 HTTP）。
        快照一定会过期：用户可能在前端加载/卸载，或有另一个任务并发加载，
        vLLM 还会按 max_cpu_loras 自行换出适配器。
        """
        with self.lock:
            self.currently_loaded = self._get_loaded()
            return len(self.currently_loaded)

    def invalidate(self, lora_name):
        """把某条记录踢出快照 —— 服务端已经不认它了（被换出/卸载）。"""
        with self.lock:
            self.currently_loaded.discard(lora_name)

    def _get_loaded(self):
        try:
            r = requests.get(f"{LORA_MANAGER_API}/loras/loaded", timeout=5)
            return set(r.json().get("loaded", []))
        except Exception:
            return set()

    def ensure(self, lora_name):
        with self.lock:
            return self._ensure_locked(lora_name)

    def _ensure_locked(self, lora_name):
        if lora_name is None:
            return False, "使用底座"
        if lora_name in self.currently_loaded:
            return False, f"LoRA '{lora_name}' 已在显存"
        t0 = time.time()
        try:
            r = requests.post(
                f"{LORA_MANAGER_API}/loras/load",
                json={"lora_name": lora_name},
                timeout=60
            )
            if r.status_code == 200:
                self.currently_loaded.add(lora_name)
                el = round(time.time() - t0, 2)
                self.swaps.append({"lora": lora_name, "sec": el})
                return True, f"已加载 LoRA '{lora_name}'（{el}s）"
            else:
                try:
                    err = r.json().get("detail", r.text)
                except Exception:
                    err = r.text
                # 幂等：外部（UI / 并发任务）可能已经加载过，我的快照过期了。
                # 这不是失败 —— 否则会产生"LoRA 明明可用，节点却失败"的假失败。
                low = str(err).lower()
                if "already been loaded" in low or "already exists" in low:
                    self.currently_loaded.add(lora_name)
                    return True, f"LoRA '{lora_name}' 已在显存（外部已加载，按幂等处理）"
                return False, f"加载失败: {err[:300]}"
        except Exception as e:
            return False, f"加载异常: {e}"


# ============ JSON 解析 ============
def parse_json_response(raw):
    if not raw:
        return {}
    cleaned = strip_think(raw)
    cleaned = re.sub(r'```(?:json)?\s*', '', cleaned)
    cleaned = cleaned.replace('```', '')
    stripped = cleaned.strip()

    # 顶层 JSON 数组也是合法输出（实测模型会这么答）：包一层 items 返回。
    # 若顶层明显是数组却残缺，**显式失败**——绝不退回解析内部对象，
    # 否则会静默返回数组里的一小部分内容，比报错更危险。
    if stripped.startswith("["):
        end = stripped.rfind("]")
        if end != -1:
            try:
                arr = json.loads(stripped[:end + 1])
            except json.JSONDecodeError:
                arr = None
            if isinstance(arr, list):
                return {"items": arr, "_was_array": True}
        return {"_raw": raw, "_parse_error": True}

    start, end = stripped.find('{'), stripped.rfind('}')
    if start == -1 or end == -1:
        return {"_raw": raw}
    try:
        parsed = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError:
        return {"_raw": raw, "_parse_error": True}
    if not isinstance(parsed, dict):
        return {"items": parsed, "_was_array": True}
    return parsed


# ============ LLM 调用（多 provider） ============
def call_vllm(prompt, lora=None, system=None, model_override=None,
              max_tokens=2048, temperature=0.3, timeout=300):
    if model_override:
        model = model_override
    elif lora:
        model = lora
    else:
        model = get_vllm_base_model()
        if not model:
            return "[错误] 无法获取 vLLM 当前底座模型"

    if system is None:
        system = "你是专业的学术论文分析助手，请按要求输出。"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    try:
        r = requests.post(VLLM_CHAT_API, json=payload, timeout=timeout)
        if r.status_code != 200:
            payload.pop("chat_template_kwargs", None)
            payload.pop("extra_body", None)
            r = requests.post(VLLM_CHAT_API, json=payload, timeout=timeout)
        if r.status_code != 200:
            return f"[错误 {r.status_code}] {r.text[:200]}"
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
        _record_usage("vllm",
                      usage.get("prompt_tokens", 0),
                      usage.get("completion_tokens", 0),
                      usage.get("total_tokens", 0))
        return strip_think(raw)
    except Exception as e:
        return f"[调用异常] {e}"


def call_openai(prompt, lora=None, system=None, model_override=None,
                max_tokens=2048, temperature=0.3, timeout=300):
    cfg = PROVIDER_CONFIGS.get("openai", {}) or {}
    api_base = (cfg.get("api_base") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    if not api_base:
        return "[错误] 云 API 未配置 api_base，请到系统设置填写"
    if not api_key:
        return "[错误] 云 API 未配置 API Key，请到系统设置填写"

    model = model_override or cfg.get("model") or "gpt-4o-mini"
    if system is None:
        system = "你是专业的学术论文分析助手，请按要求输出。"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(f"{api_base}/chat/completions", json=payload,
                          headers=headers, timeout=timeout)
        if r.status_code != 200:
            return f"[云 API 错误 {r.status_code}] {r.text[:200]}"
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
        _record_usage("openai",
                      usage.get("prompt_tokens", 0),
                      usage.get("completion_tokens", 0),
                      usage.get("total_tokens", 0))
        return strip_think(raw)
    except Exception as e:
        return f"[云 API 调用异常] {e}"


def call_ollama(prompt, lora=None, system=None, model_override=None,
                max_tokens=2048, temperature=0.3, timeout=300):
    cfg = PROVIDER_CONFIGS.get("ollama", {}) or {}
    api_base = (cfg.get("api_base") or "http://127.0.0.1:11434").rstrip("/")
    model = model_override or cfg.get("model") or "qwen2.5:7b"
    if system is None:
        system = "你是专业的学术论文分析助手，请按要求输出。"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    try:
        r = requests.post(f"{api_base}/api/chat", json=payload, timeout=timeout)
        if r.status_code != 200:
            return f"[Ollama 错误 {r.status_code}] {r.text[:200]}"
        data = r.json()
        raw = data.get("message", {}).get("content", "")
        _record_usage("ollama")
        return strip_think(raw)
    except Exception as e:
        return f"[Ollama 调用异常] {e}"


def _sim_guard_wrap(out, label=""):
    """★ 递归守卫（2026-10-19）: 把工作流节点（= 子代理 ✓）的产出过一遍计算守卫

    为什么在工作流引擎做: 所有节点的 LLM 调用都收敛到 call_llm ✓
      在这里包一层 ⇒ 任何层级的子代理都覆盖到 ✓（递归 ✓）
    做法: 检测算式 ⇒ 真算 ⇒ 替换数字 ✓；失败不阻断（保留原文 ✓ 只记日志 ✓）
    """
    try:
        import sim_guard as _sg
        if not isinstance(out, str) or not out:
            return out
        sid = _sg.open_scope(None, kind="workflow_node", label=label or "")
        try:
            res = _sg.guard_text(out, scope_id=sid)
            if res.get("needed"):
                rep = res.get("repaired") or out
                fixes = (res.get("repair") or {}).get("fixes") or []
                if rep != out:
                    log(f"  ⚖️ 计算守卫: 已用仿真节点复算 {len([f for f in fixes if 'value' in f])} 个算式 ✓")
                return rep
            return out
        finally:
            _sg.close_scope(sid)
    except Exception as e:
        print(f"[sim_guard] 工作流守卫异常(忽略): {e}", flush=True)
        return out


def call_llm(prompt, lora=None, system=None, provider=None, model_override=None,
             max_tokens=2048, temperature=0.3, timeout=300):
    """
    统一入口。
    - provider: None/vllm → 走本地 vLLM（默认）
                openai  → 走云 API
                ollama  → 走本地 Ollama
    - lora: 只在 vllm 下生效
    - model_override: 覆盖 provider 默认模型
    - timeout: 单次请求超时秒数（可由节点配置覆盖）

    注意：本函数**失败时返回错误字符串**而不是抛异常（历史行为）。
    流程编排请使用 call_llm_strict()，它会把这些字符串转成异常。
    """
    provider = (provider or "vllm").lower()
    # ★ 递归守卫: 每条分支的返回值都过 _sim_guard_wrap ✓
    if provider == "vllm":
        return _sim_guard_wrap(
            call_vllm(prompt, lora, system, model_override, max_tokens, temperature, timeout),
            label="vllm")
    elif provider == "openai":
        return _sim_guard_wrap(
            call_openai(prompt, lora, system, model_override, max_tokens, temperature, timeout),
            label="openai")
    elif provider == "ollama":
        return _sim_guard_wrap(
            call_ollama(prompt, lora, system, model_override, max_tokens, temperature, timeout),
            label="ollama")
    else:
        return f"[错误] 未知 provider: {provider}"


# ============ 失败语义：把"错误字符串"升级为异常 ============
class LLMCallError(RuntimeError):
    """LLM 调用失败（网络/超时/非 200/未配置等）。"""


# call_* 系列在失败时会返回这类前缀的字符串。历史实现把它们当正文继续跑，
# 于是"vLLM 超时"会被当成分析结果写进报告 —— 这是最危险的一类静默失败。
_LLM_ERROR_MARKERS = (
    "[错误",
    "[调用异常",
    "[云 API 错误",
    "[云 API 调用异常",
    "[Ollama 错误",
    "[Ollama 调用异常",
)


def looks_like_llm_error(text):
    """判断一段模型输出其实是错误提示而不是内容。"""
    if not isinstance(text, str):
        return True
    stripped = text.strip()
    if not stripped:
        return True
    return stripped.startswith(_LLM_ERROR_MARKERS)


def looks_like_missing_lora_error(text):
    """
    这段报错是不是"适配器没在服务端注册"（而不是模型能力问题）。
    实测 vLLM 的响应体：
      [错误 404] {"error":{"message":"The model `x` does not exist.",
                  "type":"NotFoundError","param":null,"code":404}}
    只有确实指定了 lora 时才该走重载路径（调用方负责判断），这里只做文本识别。
    """
    if not isinstance(text, str):
        return False
    low = text.lower()
    if "does not exist" in low or "notfounderror" in low:
        return True
    if "lora adapter" in low and ("not found" in low or "not loaded" in low):
        return True
    return False


def call_llm_strict(prompt, **kwargs):
    """
    编排用的调用入口：失败即抛 LLMCallError，交给节点级重试/错误策略处理。
    这样错误不可能再被"当成内容"流到下游。
    """
    out = call_llm(prompt, **kwargs)
    if looks_like_llm_error(out):
        detail = out.strip() if isinstance(out, str) else repr(out)
        raise LLMCallError(detail or "模型返回空内容")
    return out


# ============ 输入组装 / prompt 渲染 ============
def build_node_input(node, context):
    result = {}
    for src in node.get("input_from", []):
        if src == "full_text":
            result["full_text"] = context["full_text"]
        elif "." in src:
            parent, key = src.split(".", 1)
            if parent in context["nodes"]:
                val = context["nodes"][parent]
                if isinstance(val, dict):
                    result[f"{parent}.{key}"] = val.get(key, "")
                else:
                    result[f"{parent}.{key}"] = str(val)
        else:
            val = context["nodes"].get(src, "")
            if isinstance(val, dict):
                result[src] = json.dumps(val, ensure_ascii=False, indent=2)
            else:
                result[src] = str(val)
    return result


def render_prompt(template, values):
    output = template
    for k, v in values.items():
        output = output.replace("{" + k + "}", str(v))
    if "{输入}" in output or "{input}" in output:
        all_upstream = "\n\n---\n\n".join(
            f"【{k}】\n{v}" for k, v in values.items() if not k.startswith("_")
        )
        output = output.replace("{输入}", all_upstream).replace("{input}", all_upstream)
    return output


def topological_sort(nodes):
    node_map = {n["id"]: n for n in nodes}
    deps = {n["id"]: [s.split(".")[0] for s in n.get("input_from", []) if s != "full_text"]
            for n in nodes}
    deps = {k: list(set(v)) for k, v in deps.items()}

    visited = {}
    result = []

    def dfs(nid, stack):
        if visited.get(nid) == 2:
            return
        if visited.get(nid) == 1:
            raise RuntimeError(f"检测到循环依赖: {' -> '.join(stack + [nid])}")
        visited[nid] = 1
        for up in deps.get(nid, []):
            if up in node_map:
                dfs(up, stack + [nid])
        visited[nid] = 2
        result.append(node_map[nid])

    for n in nodes:
        dfs(n["id"], [])
    return result


def migrate_old_config(config):
    if "nodes" in config and config["nodes"] and config["nodes"][0].get("type"):
        return config
    new_nodes = []
    fe = config.get("field_extraction")
    if fe:
        new_nodes.append({
            "id": fe["id"], "type": "process", "description": "维度抽取",
            "input_from": [], "lora": fe.get("lora"), "prompt": fe.get("prompt", "")
        })
    for n in config.get("nodes", []):
        new_nodes.append({
            "id": n["id"], "type": "terminal",
            "description": n.get("description", ""),
            "input_from": n.get("input_from", []),
            "lora": n.get("lora"), "prompt": n.get("prompt", "")
        })
    return {
        "workflow_name": config.get("workflow_name", "未命名工作流"),
        "nodes": new_nodes, "_migrated_from_old": True
    }


AGGREGATE_SYSTEM_PROMPT = (
    '你是一个文本整合助手。请把用户提供的多份分析结果，'
    '整合成一份通顺、连贯的中文纯文本报告。\n'
    '严格要求：\n'
    '1. 直接输出纯文本，绝对不要 JSON\n'
    '2. 不要使用 Markdown 代码块\n'
    '3. 保留原文中的重要观点，去掉重复\n'
    '4. 使用自然段落组织内容，可用小标题（如"一、方法论分析"）\n'
    '5. 不要输出"好的"、"以下是"等开场白\n'
    '6. 直接开始写正文'
)


# ============ 知识库名解析 ============
# 节点里的 kb_name 可以是具体名字，也可以是 auto/空 —— 后者在运行时解析：
#   1) 节点显式写了名字        → 用它
#   2) 环境变量 WORKFLOW_KB_NAME（后端 /workflow/run 注入，CLI 也可 export）→ 用它
#   3) auto / 留空            → 向 lora_manager 查询现有知识库：
#                               只有一个就直接用；多个用"最近更新的"并列出其他候选
# 无论走哪条，都会把"实际用了哪个、为什么"写进日志与结果元数据（_meta.kb_resolution），
# 绝不静默换库。
AUTO_KB_TOKENS = {"", "auto", "__auto__", "*", "自动", "（自动"}


def list_available_kbs():
    """返回 [(name, updated_at)]，按 updated_at 倒序；查询失败返回 []。"""
    try:
        r = requests.get(f"{LORA_MANAGER_API}/kb/list", timeout=10)
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("knowledge_bases") or []
        pairs = [
            (str(k.get("name") or ""), str(k.get("updated_at") or k.get("created_at") or ""))
            for k in items if k.get("name")
        ]
        pairs.sort(key=lambda p: p[1], reverse=True)
        return pairs
    except Exception:
        return []


def resolve_kb_name(node):
    """
    解析检索节点该用哪个知识库。
    返回 (kb_name, source, candidates)；source ∈ node|env|auto-single|auto-latest|none
    """
    requested = str(node.get("kb_name") or "").strip()
    if requested and requested.lower() not in AUTO_KB_TOKENS:
        return requested, "node", [requested]
    if ENV_KB_NAME:
        return ENV_KB_NAME, "env", [ENV_KB_NAME]
    pairs = list_available_kbs()
    names = [n for n, _ in pairs]
    if len(names) == 1:
        return names[0], "auto-single", names
    if names:
        return names[0], "auto-latest", names
    return "", "none", []


# ============ 节点结果缓存 ============
# 动机：调下游提示词时反复重跑同一篇论文，本地抽取（4 万字约 1 分钟）被重复劳动。
# 键 = 提示词（已含替换后的输入）+ 影响输出的节点配置；改任何一项都会自动失效。
CACHE_DIR = os.path.join(PROJECT_DIR, "cache", "nodes")
# 节点 trace 的截断长度：够导出训练样本，又不至于让结果文件爆掉
TRACE_PROMPT_CHARS = 6000
TRACE_OUTPUT_CHARS = 4000
# 质量指标只观测、不自动判定：中文同域文本的字符二元组重合度天然偏高，
# 编造内容（如"量子纠缠/火星数据集"）与稀疏注意力正文仍可 > 0.6 重合，
# 直接拿阈值判定会给虚假的安心感。阈值需用真实标注数据标定后再启用。
# ============ A1/A2：运行级超时 与 云端重试策略 ============
# A1：一次运行的墙钟上限（秒）。节点级 timeout 管"单个请求"，运行级管"整条流水线"——
#     云端某节点卡住时，没有运行级上限会一直拖。配置项 run_timeout 可覆盖。
RUN_TIMEOUT_DEFAULT = 1800.0
# A2：重试等待 = min(base * 2^attempt, 上限) ± 抖动。线性等待在连续限流下会一直撞墙。
RETRY_MAX_WAIT = 60.0
RETRY_JITTER = 0.3
# 可重试的 HTTP 状态：限流 / 冲突 / 服务端错误
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529}
# 错误串里的状态码形如 `[云 API 错误 429] ...` / `[错误 500] ...`
_STATUS_RE = re.compile(r"^\[(?:云 API )?错误 (\d{3})\]")

QUALITY_NOTE = "grounding/fact_retention 仅对 extract 类型参与判定，其余类型只观测"

# 已标定阈值（依据见 QUALITY_NOTE 上方说明；仅作用于 extract 类型且输入足够长的节点）
QUALITY_FACT_RETENTION_MIN = 0.9
# 8 篇真实论文标定（域内 4 + 域外 4）：可溯源率 min 0.9956 / avg 0.9989 —— 余量很大。
# 原值 0.8 是怎么定的：当时以为"编造句能拿 1.0"，所以把阈值压得很低。**那个结论是错的**
# —— 它是测试缓存污染的产物（见 tests/test_quality.py 里的 cache 修复）。
# 实际复算：换词汇的编造 0.3235、原文词汇拼凑 0.6087、跨文档错配 0.8987。
# 取 0.9：真实抽取仍有 0.0956 的余量，同时把"用原文词汇但不是原文事实"那一段也圈进来。
QUALITY_GROUNDING_MIN = 0.9

# 第三条：抽取充分度（每千字输入至少产出多少字）。
# 为什么必须有它：grounding 与事实保留都只回答"输出里的东西是不是来自原文"，
# 对**"输出太少"完全无感**。实测把一份 1455 字的合格抽取截断到 200 字
# （每千字 5.0），两项指标仍是 1.0/1.0 → 判定为"通过"。而"4 万字只输出 94 字"
# 正是本项目已知的欠抽取故障（长文没开分块）。标定依据：8 篇正常抽取实测
# 每千字 23.1~37.6，而欠抽故障是 2.4 → 取 8 作地板，两侧都有很大余量。
QUALITY_MIN_CHARS_PER_1K = 8.0
# 输入太短时这个比值没有意义（一句话的输入随便产出几个字都能过），不参与判定
QUALITY_SUFFICIENCY_MIN_INPUT = 5000
# 第四条：**无出处事实的绝对条数**。
# 为什么比率不够：掺 1~2 个假数字时保留率仍有 0.9643 / 0.90，从 0.9 阈值下溜过去
# （实测）。而"事实没有出处"这件事本身是可数的，比比率敏感得多。
# 标定：17 次真实抽取的未匹配事实数**全是 0**（min=max=0）；
#       往一份真实抽取里掺 1/2/5 个假数字 → 未匹配 1/3/6 条。
#       取 ≥2（只容忍 1 条格式差异）：真实 0 通过，掺 2 条即告警。
# 仅当事实总数 ≥5 时才判：事实太少时比率判据已足够，再加绝对条数只会添噪。
QUALITY_MAX_UNMATCHED_FACTS = 2
QUALITY_UNMATCHED_MIN_FACTS = 5
CACHE_ENABLED = os.environ.get("WORKFLOW_CACHE", "1") != "0"


def node_cache_key(node, prompt, ntype):
    """
    缓存键 = 提示词 + **实际影响输出的有效参数**。
    刻意用"生效后的取值"而非原始字段：process+output_format=text 与 extract 类型
    运行时行为一致，就应当能共用缓存；反之参数一变键就变，绝不会误用旧结果。
    """
    as_text = node_output_is_text(node, ntype)
    gen = node_generation_options(node, ntype)
    material = {
        "prompt": prompt,
        "output": "text" if as_text else "json",
        "system": gen.get("system"),
        "max_tokens": gen.get("max_tokens"),
        "temperature": gen.get("temperature"),
        "lora": node.get("lora"),
        "provider": node.get("provider"),
        "model_override": node.get("model_override"),
        "chunk_size": node.get("chunk_size"),
        "chunk_overlap": node.get("chunk_overlap"),
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def cache_load(key):
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if "parsed" in data else None
    except Exception:
        return None                                       # 坏缓存当作未命中，不阻断运行


def cache_store(key, parsed, chunk_info, attempts):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = os.path.join(CACHE_DIR, f".{key}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"parsed": parsed, "chunking": chunk_info or {},
                       "attempts": attempts,
                       "created_at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False)
        os.replace(tmp, os.path.join(CACHE_DIR, f"{key}.json"))   # 原子替换，避免半截文件
    except Exception:
        pass                                              # 缓存失败不影响主流程


# ============ 节点字段注册表 / 类型 / 校验 ============
# 节点类型定义：类型即任务契约（label/输出形态/默认字段/LoRA 角色）。
# 关键点：选一个类型就自动带上它该有的默认字段，用户不必手填 8 个字段。
NODE_TYPE_SPECS = {
    "input": {
        "label": "📥 输入", "kind": "source", "output": "text",
        "purpose": "读取上传文件全文（每个工作流只能有 1 个）",
        "ignored_fields": {"prompt", "lora", "output_format", "chunk_size", "cache"},
        "defaults": {},
    },
    "retrieval": {
        "label": "📚 检索", "kind": "tool", "output": "text",
        "purpose": "知识库语义检索，结果注入下游提示词",
        "ignored_fields": {"lora", "output_format", "chunk_size", "chunk_workers", "cache"},
        "defaults": {"top_k": 5, "kb_name": "", "min_score": 0.1},
    },
    "process": {
        "label": "⚙️ 中间", "kind": "llm", "output": "json",
        "purpose": "处理上游数据并以 JSON 交给下游",
        "defaults": {},
    },
    "terminal": {
        "label": "📊 终端", "kind": "llm", "output": "json",
        "purpose": "接收上游数据做分析（JSON 输出）",
        "defaults": {},
    },
    "aggregate": {
        "label": "📦 整合", "kind": "llm", "output": "text",
        "purpose": "汇总多路结果，输出纯文本报告",
        "defaults": {"max_tokens": 4096, "temperature": 0.5},
    },
    # ---- 语义任务节点（D2）：类型即任务 ----
    "extract": {
        "label": "🧩 抽取", "kind": "llm", "output": "text",
        "lora_role": "extractor",
        "purpose": "从原文逐字抽取（可溯源）；长文自动分块并发并缓存结果",
        "quality": "grounding",       # 该类型的输出要算"可溯源率"
        "ignored_fields": set(),
        "defaults": {"output_format": "text", "chunk_size": 3000, "chunk_overlap": 500,
                     "chunk_workers": 4, "cache": True, "temperature": 0.0},
    },
    "analyze": {
        "label": "🔬 分析", "kind": "llm", "output": "json",
        "lora_role": "analyzer",
        "purpose": "对上游抽取结果做结构化分析（JSON 输出）",
        "ignored_fields": set(),
        "defaults": {},
    },
}
NODE_TYPES = tuple(NODE_TYPE_SPECS)


def node_type_spec(ntype):
    return NODE_TYPE_SPECS.get(ntype) or {}


def apply_type_defaults(node):
    """按节点类型补齐默认字段（不改动已显式给出的值）。类型即契约的落地点。"""
    spec = node_type_spec(node.get("type", "process"))
    for key, val in (spec.get("defaults") or {}).items():
        if key not in node or node[key] is None:
            node[key] = val
    return node



def _f(kind, default=None, **kw):
    spec = {"type": kind, "default": default}
    spec.update(kw)
    return spec


NODE_FIELD_SPECS = {
    "id": _f("str", None, required=True),
    "type": _f("str", "process", choices=list(NODE_TYPES)),
    "description": _f("str", ""),
    "input_from": _f("list", []),
    "prompt": _f("str", ""),
    "lora": _f("str", None),
    "provider": _f("str", None, choices=[None, "vllm", "ollama", "openai"]),
    "model_override": _f("str", None),
    "position": _f("dict", None),
    # 画布节点尺寸（第 7 项：节点可拖拽调大小）。注册在这里是为了满足
    # TestFrontendFieldConsistency：前端保存的字段必须是引擎认识的。
    "w": _f("int", 160, min=40, max=2000),
    "h": _f("int", 50, min=20, max=2000),
    "output_format": _f("str", "json", choices=["json", "text"]),
    "chunk_size": _f("int", 0, min=0),
    "chunk_overlap": _f("int", 0, min=0),
    "chunk_workers": _f("int", 1, min=1, max=32),
    "cache": _f("bool", False),
    "max_tokens": _f("int", None, min=1),
    "temperature": _f("float", None, min=0.0, max=2.0),
    "retry": _f("int", 2, min=0, max=10),
    "retry_delay": _f("float", 2.0, min=0.0),
    "timeout": _f("int", 300, min=5),
    "on_error": _f("str", None, choices=[None, "mark", "skip", "fail_fast"]),
    "kb_name": _f("str", ""),
    "top_k": _f("int", 5, min=1, max=50),
    # 归一化向量的余弦相似度 ∈ [-1, 1]：跨语言场景整批候选可能是负分，
    # 下界必须允许负数，否则"调低阈值"这个逃生口形同虚设。
    "min_score": _f("float", 0.1, min=-1.0, max=1.0),
    "query_template": _f("str", ""),
    # ★ G1: 节点产物回流知识库（默认 None = 关闭，完全向后兼容 ✓）
    #   形如 {"mode": "append|replace", "target": "workflow_learned"}
    #   为什么放这里: 引擎只认识 NODE_FIELD_SPECS 里的字段（TestFrontendFieldConsistency），
    #   不注册的话，配置里带 kb_write 会被判为未知字段而拒绝 ✗
    #   为什么默认关: 冒然入库会把 KB 撑爆；且 agent_use 是宝贵资产，默认拒写（见 workflow_learn.py）
    "kb_write": _f("dict", None),
}

_KIND_CHECK = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
}


def lint_config(cfg):
    """
    配置校验。返回 (errors, warnings)。
    errors 会导致拒绝启动（未知类型、类型错、取值越界、引用不存在的节点）；
    warnings 只提示（未知字段、无效组合）。
    """
    errors, warnings = [], []
    nodes = cfg.get("nodes") or []
    if not nodes:
        errors.append("配置里没有任何节点")
        return errors, warnings

    ids = [n.get("id") for n in nodes if n.get("id")]
    if len(ids) != len(set(ids)):
        errors.append("节点 id 存在重复")

    for n in nodes:
        nid = n.get("id") or "(无 id)"
        if not n.get("id"):
            errors.append("存在没有 id 的节点")
        ntype = n.get("type", "process")
        if ntype not in NODE_TYPES:
            errors.append(f"节点 {nid} 类型未知: '{ntype}'（可选 {', '.join(NODE_TYPES)}）")
        for key, val in n.items():
            spec = NODE_FIELD_SPECS.get(key)
            if spec is None:
                warnings.append(f"节点 {nid} 含未知字段 '{key}'（引擎会忽略它）")
                continue
            if val is None:
                continue
            if not _KIND_CHECK[spec["type"]](val):
                errors.append(f"节点 {nid} 的 {key} 应为 {spec['type']}，实际 {type(val).__name__}")
                continue
            if "choices" in spec and val not in spec["choices"]:
                errors.append(f"节点 {nid} 的 {key} 取值非法: {val!r}（可选 {spec['choices']}）")
            if spec["type"] in ("int", "float"):
                if "min" in spec and val < spec["min"]:
                    errors.append(f"节点 {nid} 的 {key}={val} 小于下限 {spec['min']}")
                if "max" in spec and val > spec["max"]:
                    errors.append(f"节点 {nid} 的 {key}={val} 大于上限 {spec['max']}")

        # 组合合理性 + 类型契约（只提示，不拦截）
        if int(n.get("chunk_workers") or 1) > 1 and not int(n.get("chunk_size") or 0):
            warnings.append(f"节点 {nid} 设了 chunk_workers 但未开 chunk_size，不会并发")
        if n.get("cache") and float(n.get("temperature") or 0) > 0:
            warnings.append(f"节点 {nid} 开了缓存但 temperature={n.get('temperature')}，"
                            f"会把一次随机结果固化下来")
        spec = node_type_spec(ntype)
        for f in sorted(spec.get("ignored_fields") or set()):
            if n.get(f):
                warnings.append(f"节点 {nid}（{ntype}）设了 {f}，该类型下不生效")
        if ntype in ("extract", "analyze") and not n.get("lora"):
            warnings.append(f"节点 {nid}（{ntype}）没有指定 LoRA，将用底座执行")

    idset = set(ids)
    for n in nodes:
        for src in (n.get("input_from") or []):
            parent = str(src).split(".", 1)[0]
            if parent != "full_text" and parent not in idset:
                errors.append(f"节点 {n.get('id')} 引用了不存在的节点 '{src}'")
    return errors, warnings


def preflight_loras(cfg, log=None):
    """
    跑前 LoRA 预检：节点引用的 LoRA 是否已安装、是否与当前底座兼容。
    返回 (errors, warnings, info)。errors 非空时应拒绝启动 ——
    否则要么等到节点执行时才失败，要么用底座产出"看着像 LoRA 结果"的内容。
    """
    emit = log or (lambda m: None)
    need = {}
    for n in cfg.get("nodes") or []:
        lora = n.get("lora")
        provider = str(n.get("provider") or "vllm").lower()
        if lora and provider == "vllm":
            need.setdefault(lora, []).append(n.get("id"))

    if not need:
        return [], [], {"needed": []}
    try:
        r = requests.get(f"{LORA_MANAGER_API}/loras", timeout=15)
        data = r.json()
    except Exception as e:
        return [], [f"LoRA 预检跳过（后端不可达: {e}）"], {"needed": sorted(need)}

    installed = {l.get("name"): l for l in data.get("loras", [])}
    vllm_hidden = data.get("vllm_hidden_size")
    errors, warnings = [], []
    for name, node_ids in sorted(need.items()):
        where = "、".join(str(i) for i in node_ids)
        meta = installed.get(name)
        if meta is None:
            errors.append(f"LoRA '{name}' 未安装（被节点 {where} 引用）")
        elif meta.get("compatible") is False:
            errors.append(f"LoRA '{name}' 与当前底座不兼容"
                          f"（hidden={meta.get('hidden_size')} vs 底座 {vllm_hidden}）"
                          f"，被节点 {where} 引用")
        elif meta.get("compatible") is None:
            warnings.append(f"LoRA '{name}' 兼容性未知（读不到 hidden_size），节点 {where}")
    # 质量台账：这个 LoRA 到底好不好（"高精度"选型依据，不再是黑盒）
    registry, registry_ok = {}, False
    try:
        rr = requests.get(f"{LORA_MANAGER_API}/loras/registry", timeout=10)
        payload = rr.json()
        if isinstance(payload, dict) and "registry" in payload:
            registry = payload["registry"] or {}
            registry_ok = True          # 接口可用：即使台账为空也要提示"没有评测记录"
    except Exception:
        registry_ok = False
    metrics_info = {}
    if registry_ok:
        for name in sorted(need):
            entry = registry.get(name) or {}
            m = (entry.get("last_eval") or {}).get("metrics") or {}
            metrics_info[name] = m
            if not m:
                warnings.append(f"LoRA '{name}' 没有评测记录，精度无据可依"
                                f"（建议先跑 run_eval）")
            elif m.get("similarity") is not None and m["similarity"] < 0.3:
                warnings.append(f"LoRA '{name}' 最近评测 similarity={m['similarity']} 偏低")

    info = {"needed": sorted(need), "vllm_hidden_size": vllm_hidden,
            "installed": sorted(installed), "metrics": metrics_info}
    return errors, warnings, info


def schedule_by_lora(nodes):
    """
    LoRA 感知调度：在不破坏依赖的前提下，尽量把同 LoRA 的节点排在一起，
    减少 LoRA 换入换出（max_loras=2 时换入成本很实在）。
    做法：每次从"依赖已满足"的节点里，优先挑与上一个执行节点同 LoRA 的那个。
    """
    remaining = list(nodes)
    done, result, current = set(), [], None
    while remaining:
        ready = [n for n in remaining
                 if all((str(s).split(".", 1)[0] in done) or str(s) == "full_text"
                        for s in (n.get("input_from") or []))]
        if not ready:
            result.extend(remaining)          # 理论不可达（有环）：保底不丢节点
            break
        pick = next((n for n in ready if (n.get("lora") or None) == current), ready[0])
        result.append(pick)
        remaining.remove(pick)
        done.add(pick.get("id"))
        current = pick.get("lora") or None
    return result


def count_lora_swaps(nodes):
    """按给定顺序执行时，需要换入 LoRA 的次数（用于对比调度效果）。"""
    swaps, current = 0, None
    for n in nodes:
        lora = n.get("lora") or None
        if lora != current:
            if lora is not None:
                swaps += 1
            current = lora
    return swaps


# ============ 节点执行策略与失败传播 ============
# 设计目标：任何节点失败都必须"响"，绝不能再被当成正文流到下游。
# 旧实现把 {"_error": ...} 直接塞进 context，下游 build_node_input 会把它
# json.dumps 成文本喂给 LLM，于是"vLLM 超时"最终变成报告里一段像模像样的论述。
DEFAULT_NODE_POLICY = {
    "on_error": "mark",     # mark=继续但显式标注 | skip=跳过本节点 | fail_fast=整条中止
    "retry": 2,             # 失败重试次数（总尝试 = retry + 1）
    "retry_delay": 2.0,     # 重试基础间隔（秒），按次数线性放大
    "timeout": 300,         # 单次请求超时（秒）
}


def node_policy(node, config=None):
    """合并默认策略 / 工作流级默认 / 节点级覆盖，并做类型收敛。"""
    pol = dict(DEFAULT_NODE_POLICY)
    cfg_default = (config or {}).get("on_error_default")
    if cfg_default in ("mark", "skip", "fail_fast"):
        pol["on_error"] = cfg_default
    for k in ("on_error", "retry", "retry_delay", "timeout"):
        if node.get(k) is not None:
            pol[k] = node[k]
    try:
        pol["retry"] = max(0, int(pol["retry"]))
    except (TypeError, ValueError):
        pol["retry"] = DEFAULT_NODE_POLICY["retry"]
    try:
        pol["retry_delay"] = max(0.0, float(pol["retry_delay"]))
    except (TypeError, ValueError):
        pol["retry_delay"] = DEFAULT_NODE_POLICY["retry_delay"]
    try:
        pol["timeout"] = max(5, int(pol["timeout"]))
    except (TypeError, ValueError):
        pol["timeout"] = DEFAULT_NODE_POLICY["timeout"]
    if pol["on_error"] not in ("mark", "skip", "fail_fast"):
        pol["on_error"] = DEFAULT_NODE_POLICY["on_error"]
    return pol


def node_failure(context, nid):
    """该节点是否记录了失败，返回错误文本或 None。"""
    val = context["nodes"].get(nid)
    if isinstance(val, dict) and val.get("_error"):
        return str(val["_error"])
    return None


def node_skipped(context, nid):
    val = context["nodes"].get(nid)
    return isinstance(val, dict) and bool(val.get("_skipped"))


def upstream_problems(node, context):
    """
    收集上游的失败/缺失情况，返回 [(来源, 原因)]。
    用于在 prompt 里显式告知模型"这些数据不可用，不要编"。
    """
    problems = []
    for src in node.get("input_from", []):
        if src == "full_text":
            continue
        parent, _, key = src.partition(".")
        if parent not in context["nodes"]:
            problems.append((src, "上游节点未执行（可能已被跳过）"))
            continue
        err = node_failure(context, parent)
        if err:
            problems.append((src, f"上游节点 {parent} 失败：{err}"))
            continue
        if node_skipped(context, parent):
            problems.append((src, f"上游节点 {parent} 因上游失败被跳过"))
            continue
        if key:
            val = context["nodes"][parent]
            if isinstance(val, dict) and not val.get(key):
                problems.append((src, f"字段 {key} 为空（可能因 JSON 解析失败）"))
    return problems


def build_missing_notice(problems):
    """把缺失输入写成一段让模型无法忽略的警告，附在 prompt 末尾。"""
    if not problems:
        return ""
    lines = "\n".join(f"- {src}：{why}" for src, why in problems)
    return (
        "\n\n【⚠️ 缺失输入警告】以下输入不可用或为空：\n" + lines +
        "\n处理要求：不要凭空推测、不要补全这些内容。若某部分依赖上述输入，"
        "请在该部分直接写明“该部分因上游数据缺失而未能分析”，其余部分照常完成。"
    )


def build_report_header(failures, degraded):
    """有失败/降级时，给最终报告加一个不可忽略的头部声明。"""
    if not failures and not degraded:
        return ""
    head = ["> ⚠️ **本报告基于不完整输入生成，引用结论前请核对：**"]
    for f in failures:
        head.append(f"> - 节点 `{f['node']}` 执行失败（尝试 {f.get('attempts', 1)} 次）：{f['error']}")
    for d in degraded:
        head.append(f"> - 节点 `{d['node']}` 降级：{d['reason']}")
    head.append("")
    head.append("")
    return "\n".join(head)


# ============ 分块抽取（map-reduce）============
# 动机：抽取式 LoRA 是按 ~3000 字窗口训练的，直接喂 4 万字全文时会"按窗口规模作答"
# （实测 39705 字论文只输出 94 字）。分块逐段抽取再合并，实测输出 7046 字且事实全对。
def split_into_chunks(text, chunk_size, overlap):
    """按段落边界切块，避免把句子切断。"""
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]
    step = max(chunk_size - overlap, chunk_size // 2)
    chunks, start = [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            cut = text.rfind("\n\n", start, end)
            if cut != -1 and cut > start + chunk_size // 2:
                end = cut
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def split_prompt_into_chunks(prompt, values, chunk_size, overlap):
    """
    找出提示词里最大的那个输入值，按块切分，并**重建每个块对应的提示词**。
    返回 (chunk_prompts, 说明)；chunk_prompts 为 None 表示不分块。

    做成"模板无关"：不假设占位符叫什么名字，只要最大输入值原样出现在提示词里，
    就把它替换成每一块；否则明确说明原因并退回单次调用（不猜）。
    """
    if chunk_size <= 0:
        return None, "未启用分块"
    biggest = ""
    for v in values.values():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if len(s) > len(biggest):
            biggest = s
    if len(biggest) <= chunk_size:
        return None, f"最大输入 {len(biggest)} 字 ≤ 阈值 {chunk_size}，单次调用"
    if biggest not in prompt:
        return None, "最大输入未原样出现在提示词中，无法安全分块（退回单次调用）"
    chunks = split_into_chunks(biggest, chunk_size, overlap)
    return ([prompt.replace(biggest, c) for c in chunks],
            f"输入 {len(biggest)} 字 → 切 {len(chunks)} 块（块长 {chunk_size}/重叠 {overlap}）")


def _norm_line(s):
    return re.sub(r"\s+", "", str(s)).lower()


def merge_text_outputs(parts):
    """文本合并：按行去重拼接（相邻块有重叠，同一句会被重复抽出）。"""
    seen, out = set(), []
    for p in parts:
        for line in str(p).splitlines():
            s = line.strip()
            if not s:
                continue
            key = _norm_line(s)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return "\n".join(out)


def merge_sentences(parts):
    """
    按句去重拼接（用于 JSON 里的字符串字段）。
    句边界：中文标点、换行；英文句点要求后跟空格，避免切坏 3.5 / U.S. 这类。
    """
    seen, out = set(), []
    for p in parts:
        for s in re.split(r"(?<=[。！？；])|(?<=[.!?])\s+|\n+", str(p)):
            s = s.strip()
            if not s:
                continue
            key = _norm_line(s)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    merged = ""
    for s in out:
        if merged and merged[-1].isascii() and s[:1].isascii():
            merged += " "                       # 英文句子之间补空格
        merged += s
    return merged


def merge_json_outputs(objs):
    """
    JSON 合并：list 取有序并集、str 按句去重拼接、标量保留首个非空值。
    标量冲突**不静默**：记入 conflicts 供人工核对。
    返回 (merged, conflicts, stats)
    """
    merged, conflicts = {}, []
    stats = {"list_union": 0, "text_merge": 0, "scalar_conflict": 0}
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k, v in obj.items():
            if k not in merged:
                merged[k] = v
                continue
            cur = merged[k]
            if isinstance(v, list) and isinstance(cur, list):
                seen, union = set(), []
                for item in list(cur) + list(v):
                    key = _norm_line(json.dumps(item, ensure_ascii=False))
                    if key not in seen:
                        seen.add(key)
                        union.append(item)
                merged[k] = union
                stats["list_union"] += 1
            elif isinstance(v, str) and isinstance(cur, str):
                merged[k] = merge_sentences([cur, v])
                stats["text_merge"] += 1
            elif v is None:
                continue
            elif cur is None:
                merged[k] = v
            elif v != cur:
                conflicts.append({"field": k, "kept": cur, "other": v})
                stats["scalar_conflict"] += 1
    return merged, conflicts, stats


def node_output_is_text(node, ntype):
    """
    该节点是否输出纯文本（不做 JSON 校验）。
    优先看类型契约（extract/aggregate/input/retrieval 都是文本），
    其余类型允许用 output_format="text" 显式选择。
    """
    if node_type_spec(ntype).get("output") == "text":
        return True
    return str(node.get("output_format") or "").strip().lower() == "text"


def node_generation_options(node, ntype):
    """文本节点的生成参数：aggregate 沿用原样；文本抽取默认低温（抽取要可复现）。"""
    if ntype == "aggregate":
        # 类型注册表已声明 aggregate 的 defaults 是 max_tokens=4096/temperature=0.5，
        # 但这里过去写死 4096 且不读节点值 —— 契约与实现不一致（节点上改了不生效）。
        return {"system": AGGREGATE_SYSTEM_PROMPT,
                "max_tokens": int(node.get("max_tokens") or 4096),
                "temperature": float(node.get("temperature")
                                     if node.get("temperature") is not None else 0.5)}
    opts = {"system": None,
            "max_tokens": int(node.get("max_tokens") or 1024),
            "temperature": float(node.get("temperature") if node.get("temperature") is not None else 0.0)}
    return opts


def classify_retryable(err_text):
    """
    判断一个错误串是否值得重试。返回 (可重试?, 人话原因)。

    可重试：限流/服务端错误（408/409/425/429/5xx/529）、网络异常与超时。
    不可重试：400/401/403/404/413/422 等 —— 请求本身有问题（提示词过长、参数非法、
    鉴权失败）。实测 422 无脑重试 3 次依然失败，只是白等。
    """
    if not isinstance(err_text, str) or not err_text.strip():
        return False, "非文本或空错误"
    m = _STATUS_RE.match(err_text.strip())
    if m:
        code = int(m.group(1))
        if code in RETRYABLE_STATUS:
            return True, f"HTTP {code}（限流或服务端问题）"
        return False, f"HTTP {code}（请求本身有问题，重试无用）"
    low = err_text.lower()
    if "调用异常" in err_text or "timeout" in low or "timed out" in low:
        return True, "网络异常或超时"
    # 认不出来就保守重试：只在**明确识别**出客户端错误（4xx）时才快速失败，
    # 否则会改变原有的"遇错重试"行为（既有测试就是用"第一次超时"这类通用串）。
    return True, "未识别的错误（保守起见仍重试）"


def retry_delay_for(attempt, base, rnd=None):
    """
    第 attempt 次重试前的等待秒数：指数退避 + ±抖动（attempt 从 0 起）。
    上限 RETRY_MAX_WAIT；base=0 时返回 0（测试里用得上）。
    """
    rnd = rnd or random.random
    wait = min(max(0.0, float(base)) * (2 ** attempt), RETRY_MAX_WAIT)
    if wait <= 0:
        return 0.0
    jitter = wait * RETRY_JITTER * (2 * rnd() - 1)
    return round(max(0.0, wait + jitter), 2)


def llm_call_with_retry(as_text, prompt, lora, provider, model_override, policy,
                        emit=None, gen=None, router=None):
    """
    执行一次 LLM 调用（文本节点只取正文；JSON 节点还要校验可解析），含重试。
    返回 (parsed, last_error, parse_failed, attempts)。
    parsed 为 None 且 last_error 非空 → 彻底失败。
    router: 传入 LoraRouter 时，"适配器已被换出"的 404 会自动重载并重试一次。
    """
    emit = emit or (lambda m: None)
    gen = gen or {}
    attempts, parsed, last_error, parse_failed = 0, None, None, False
    reloaded = False          # 本次调用内是否已经为"适配器被换出"重载过
    for attempt in range(policy["retry"] + 1):
        attempts = attempt + 1
        try:
            if as_text:
                raw = call_llm_strict(
                    prompt, lora=lora, system=gen.get("system"),
                    provider=provider, model_override=model_override,
                    max_tokens=gen.get("max_tokens", 1024),
                    temperature=gen.get("temperature", 0.0),
                    timeout=policy["timeout"],
                )
                parsed = clean_text_output(raw)
                if not parsed.strip():
                    raise LLMCallError("模型返回空内容")
            else:
                raw = call_llm_strict(
                    prompt, lora=lora, provider=provider,
                    model_override=model_override, timeout=policy["timeout"],
                )
                candidate = parse_json_response(raw)
                if candidate.get("_parse_error") or "_raw" in candidate:
                    last_error = "模型返回的内容不是合法 JSON"
                    parse_failed = True
                    parsed = candidate
                    if attempt < policy["retry"]:
                        wait = retry_delay_for(attempt, policy["retry_delay"])
                        emit(f"  ↻ JSON 解析失败，{wait:.1f}s 后重试"
                             f"（{attempts}/{policy['retry'] + 1}）")
                        time.sleep(wait)
                        continue
                    break
                parsed = candidate
            last_error = None
            parse_failed = False
            break
        except LLMCallError as e:
            last_error, parse_failed, parsed = str(e), False, None
            # 适配器已被换出/卸载：vLLM 回 404 "The model `x` does not exist."
            # 快照说"已在显存"、服务端却已经不认 —— 用户在前端卸载、并发任务、
            # 或 vLLM 按 max_cpu_loras 换出都会这样。这不是模型能力问题，
            # 以服务端为准重载一次即可，否则明明可用的 LoRA 会让整个节点失败。
            if (not reloaded and router is not None and lora
                    and looks_like_missing_lora_error(last_error)):
                reloaded = True
                try:
                    router.invalidate(lora)
                    ok, note = router.ensure(lora)
                except Exception as e2:          # 恢复动作本身绝不能再掩盖真实错误
                    ok, note = False, f"重载异常: {e2}"
                emit(f"  ↻ {note}")
                if ok:
                    continue                     # 重载成功 → 立即重试，不占用重试预算
            if attempt < policy["retry"]:
                ok_retry, why = classify_retryable(last_error)
                if not ok_retry:
                    # 请求本身有问题：重试只会白等（实测 422 重试 3 次仍失败）
                    emit(f"  ⛔ 不重试：{why} → {last_error[:80]}")
                    break
                wait = retry_delay_for(attempt, policy["retry_delay"])
                emit(f"  ↻ 调用失败（{why}），{wait:.1f}s 后重试"
                     f"（{attempts}/{policy['retry'] + 1}）：{last_error[:60]}")
                time.sleep(wait)
        except Exception as e:
            last_error, parse_failed, parsed = f"{type(e).__name__}: {e}", False, None
            if attempt < policy["retry"]:
                ok_retry, why = classify_retryable(last_error)
                if not ok_retry:
                    emit(f"  ⛔ 不重试：{why} → {last_error[:80]}")
                    break
                wait = retry_delay_for(attempt, policy["retry_delay"])
                emit(f"  ↻ 异常（{why}），{wait:.1f}s 后重试：{last_error[:60]}")
                time.sleep(wait)
    return parsed, last_error, parse_failed, attempts


def run_workflow(file_path, config_path=None):
    if config_path is None:
        config_path = DEFAULT_CONFIG
    if not os.path.exists(config_path):
        log(f"配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    config = migrate_old_config(config)
    if config.get("_migrated_from_old"):
        log("⚠️ 检测到旧格式配置，已自动迁移到新格式")

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        full_text = f.read()

    log(f"工作流启动: {config.get('workflow_name', '未命名')}")
    log(f"输入文件: {os.path.basename(file_path)} ({len(full_text)} 字符)")
    log(f"vLLM 当前底座: {get_vllm_base_model() or '(未知)'}")

    # 显示可用的 provider
    available_providers = ["vllm"]
    if PROVIDER_CONFIGS.get("openai", {}).get("api_key"):
        available_providers.append("openai")
    if PROVIDER_CONFIGS.get("ollama", {}).get("api_base"):
        available_providers.append("ollama")
    log(f"可用 providers: {', '.join(available_providers)}")

    context = {"full_text": full_text, "nodes": {}}
    router = LoraRouter()
    log_list = []
    failures = []        # 节点级失败记录
    degraded = []        # 节点级降级记录（模型答了但内容不可用）
    missing_inputs = {}  # 节点 -> 缺失的上游输入列表
    kb_resolution = {}   # 节点 -> 知识库解析结果（实际用了哪个、为什么）
    chunking = {}        # 节点 -> 分块抽取统计（块数/失败块/合并情况）
    traces = {}          # 节点 -> 该节点的真实输入/输出（供导出训练数据）
    node_stats = {}      # 节点 -> 耗时/产出/用量（O2：耗时面板）
    quality = {}         # 节点 -> 质量指标（抽取类节点算可溯源率）
    quality_warnings = []  # 可溯源率偏低的节点（进报告头 + 供 Agent 判断是否要重训）
    abort_reason = None  # fail_fast / 运行超时 中止时记录原因
    abort_kind = None    # None | "fail_fast" | "timeout"
    run_timeout = float(config.get("run_timeout") or RUN_TIMEOUT_DEFAULT)
    run_started = time.time()
    log(f"运行级超时: {run_timeout:g}s（配置项 run_timeout 可改）")

    try:
        ordered_nodes = topological_sort(config["nodes"])
    except Exception as e:
        log(f"❌ 拓扑排序失败: {e}")
        raise WorkflowPreflightError(1, f"拓扑排序失败: {e}")

    # 配置校验：errors 直接拒绝启动（未知类型/非法取值/引用不存在的节点）
    config_errors, config_warnings = lint_config(config)
    for w in config_warnings:
        log(f"  ⚠️ 配置: {w}")
    if config_errors:
        log("❌ 配置校验未通过，拒绝启动：")
        for e in config_errors:
            log(f"    - {e}")
        raise WorkflowPreflightError(2, "配置校验未通过", config_errors)

    # LoRA 预检：未安装 / 与底座不兼容 → 拒绝启动（否则会白等到节点执行时才失败）
    lora_errors, lora_warnings, lora_info = preflight_loras(config, log)
    for w in lora_warnings:
        log(f"  ⚠️ {w}")
    if lora_errors:
        log("❌ LoRA 预检未通过，拒绝启动：")
        for e in lora_errors:
            log(f"    - {e}")
        log(f"    当前底座 hidden_size={lora_info.get('vllm_hidden_size')}，"
            f"已安装 {len(lora_info.get('installed') or [])} 个适配器")
        raise WorkflowPreflightError(3, "LoRA 预检未通过", lora_errors)
    if lora_info.get("needed"):
        log(f"  ✓ LoRA 预检通过: {', '.join(lora_info['needed'])}")

    # LoRA 感知调度：不破坏依赖的前提下把同 LoRA 的节点排在一起，减少换入换出
    naively = count_lora_swaps(ordered_nodes)
    scheduled = schedule_by_lora(ordered_nodes)
    planned = count_lora_swaps(scheduled)
    if planned < naively:
        log(f"  🔁 LoRA 感知调度：换入次数 {naively} → {planned}")

    total = len(scheduled)
    log(f"共 {total} 个节点，按拓扑顺序执行")

    def execute_node(node, idx, total):
        """
        节点执行体（S2 从 run_workflow 内联循环中抽出，行为保持不变）。
        返回 None 表示继续下一个节点；返回 ("abort", reason) 表示中止工作流。
        通过闭包读写 context/failures/degraded/chunking/quality/traces/log_list。
        """
        node = apply_type_defaults(node)      # 类型即契约：按类型补齐默认字段
        nid = node["id"]
        ntype = node.get("type", "process")
        log(f"[Node] {nid} ({ntype}) - {node.get('description', '')} [{idx}/{total}]")

        # 未知节点类型必须显式失败：以前会静默按 process 处理
        if ntype not in NODE_TYPES:
            err = f"未知节点类型 '{ntype}'（可选 {', '.join(NODE_TYPES)}）"
            log(f"  ❌ {err}")
            log_list.append(f"[{nid}] 失败: {err}")
            context["nodes"][nid] = {"_error": err, "_failed": True}
            failures.append({"node": nid, "stage": "config", "error": err, "attempts": 1})
            if node_policy(node, config)["on_error"] == "fail_fast":
                log(f"  ⛔ on_error=fail_fast，中止工作流")
                return ("abort", err)
            return None

        if ntype == "input":
            context["nodes"][nid] = full_text
            log(f"  ✅ 输入节点，读取全文 {len(full_text)} 字符")
            log_list.append(f"[{nid}] 输入节点，全文 {len(full_text)} 字符")
            return None
        # ★ 检索节点：不走 LLM，调用知识库
        if ntype == "retrieval":
            policy = node_policy(node, config)
            top_k = int(node.get("top_k") or 5)
            kb_name, kb_source, kb_candidates = resolve_kb_name(node)
            if not kb_name:
                msg = ("没有可用知识库：请在节点里选择、或设置环境变量 WORKFLOW_KB_NAME、"
                       "或先在「知识库」页创建一个")
                log(f"  ❌ {msg}")
                context["nodes"][nid] = {"_error": msg, "_failed": True}
                failures.append({"node": nid, "stage": "retrieval", "error": msg, "attempts": 1})
                log_list.append(f"[{nid}] 失败: {msg}")
                if policy["on_error"] == "fail_fast":
                    log(f"  ⛔ on_error=fail_fast，中止工作流")
                    return ("abort", msg)
                return None

            kb_resolution[nid] = {
                "requested": node.get("kb_name") or "",
                "resolved": kb_name,
                "source": kb_source,
                "candidates": kb_candidates,
            }
            log(f"  知识库: '{kb_name}'（来源: {kb_source}）")
            log_list.append(f"[{nid}] 使用知识库 '{kb_name}'（{kb_source}）")
            if kb_source == "auto-latest" and len(kb_candidates) > 1:
                others = [n for n in kb_candidates if n != kb_name]
                log(f"  ⚠️ 当前有多个知识库，已选用最近更新的 '{kb_name}'；其他候选: {', '.join(others)}")
                log_list.append(f"[{nid}] 多库候选: {', '.join(kb_candidates)}")
            if kb_source in ("node", "env"):
                existing = [n for n, _ in list_available_kbs()]
                if existing and kb_name not in existing:
                    log(f"  ⚠️ 知识库 '{kb_name}' 不在当前列表中，现有: {', '.join(existing)}")
                    log_list.append(f"[{nid}] ⚠️ '{kb_name}' 不存在，现有: {', '.join(existing)}")

            try:
                values = build_node_input(node, context)
                query_template = node.get("query_template") or "{full_text}"
                query_text = render_prompt(query_template, values).strip()
                # 如果模板没渲染出东西，就用全文前 500 字兜底
                if not query_text or query_text == "{full_text}":
                    query_text = full_text[:500]

                min_score = node.get("min_score")
                if min_score is None:
                    min_score = 0.1
                r = requests.post(
                    f"{LORA_MANAGER_API}/kb/{kb_name}/query",
                    json={"query": query_text, "top_k": top_k,
                          "min_score": float(min_score)},
                    timeout=min(policy["timeout"], 120)
                )
                if r.status_code != 200:
                    raise RuntimeError(f"KB 检索失败 {r.status_code}: {r.text[:200]}")
                payload = r.json()
                items = payload.get("results", [])
                # 检索诊断：到底为什么没命中（候选被阈值丢了？语言不匹配？索引是空的？）
                diag = payload.get("diagnostics") or {}
                if diag:
                    kb_resolution[nid]["retrieval"] = {
                        k: diag.get(k) for k in
                        ("candidates", "kept", "dropped", "best_score", "min_score",
                         "query_lang", "corpus_lang", "lang_mismatch")
                    }

                if not items:
                    # 检索为空不是错误，但属于"内容不可用"，必须让下游知道；
                    # 而且**必须说清原因** —— 旧实现在这里一律写"未检索到任何片段"，
                    # 把"候选全被阈值丢掉"说成了"库里没有"，直接把人引向错的方向。
                    reason = diag.get("hint") or f"知识库 {kb_name} 未检索到任何片段"
                    log(f"  ⚠️ {reason}")
                    log_list.append(f"[{nid}] ⚠️ {reason[:300]}")
                    degraded.append({"node": nid, "reason": reason})
                    # 进 prompt 的只放"给模型看的那句话"：上面这段完整诊断是给**操作者**的
                    # （已进日志/降级/报告头/_meta），塞进提示词反而有被模型抄进报告的风险。
                    formatted = ("（知识库中没有找到相关内容 —— 不要凭空推测，"
                                 "只能依据已提供的材料作答）")
                    log(f"  ⚠️ 0 个片段（已记为降级），输出 {len(formatted)} 字符")
                    log_list.append(f"[{nid}] 0 个片段（降级）")
                else:
                    formatted = "\n\n".join(
                        f"【片段 {i+1}】来源: {it.get('source','?')} "
                        f"(chunk {it.get('chunk_idx',0)}, score {it.get('score',0):.3f})\n"
                        f"{it.get('text','')}"
                        for i, it in enumerate(items)
                    )
                    log(f"  ✅ 检索到 {len(items)} 个片段，输出 {len(formatted)} 字符")
                    log_list.append(f"[{nid}] 检索到 {len(items)} 个片段（KB: {kb_name}）")

                context["nodes"][nid] = formatted
            except Exception as e:
                log(f"  ❌ 检索失败: {e}")
                log_list.append(f"[{nid}] 失败: {e}")
                context["nodes"][nid] = {"_error": str(e), "_failed": True}
                failures.append({"node": nid, "stage": "retrieval",
                                 "error": str(e), "attempts": 1})
                if policy["on_error"] == "fail_fast":
                    log(f"  ⛔ on_error=fail_fast，中止工作流")
                    return ("abort", str(e))
            return None

        policy = node_policy(node, config)

        # ① 上游失败传播：先判断输入是否完整
        problems = upstream_problems(node, context)
        if problems:
            missing_inputs[nid] = [src for src, _ in problems]
            for src, why in problems:
                log(f"  ⚠️ 缺失输入 {src}：{why}")
                log_list.append(f"[{nid}] 缺失输入 {src}：{why}")
            if policy["on_error"] == "skip":
                context["nodes"][nid] = {"_skipped": True, "_reason": "上游输入缺失"}
                log(f"  ⏭️ on_error=skip，跳过本节点")
                log_list.append(f"[{nid}] 已跳过（上游输入缺失）")
                return None

        # 解析节点级 provider
        provider = node.get("provider") or None  # None → vllm
        model_override = node.get("model_override") or None
        effective_provider = (provider or "vllm").lower()

        # LoRA 只在 vllm 下生效
        if effective_provider == "vllm":
            lora = node.get("lora")
        else:
            lora = None
            if node.get("lora"):
                log(f"  ⚠️ 节点选了非 vLLM provider，已忽略 LoRA '{node.get('lora')}'")

        if effective_provider == "vllm":
            switched, msg = router.ensure(lora)
            log_list.append(f"[{nid}] {msg}")
            log(f"  {msg}")
            # ② LoRA 加载失败必须中断该节点：否则会用底座产出"看着像 LoRA 结果"的内容
            if lora and str(msg).startswith(("加载失败", "加载异常")):
                err = f"LoRA '{lora}' 加载失败：{msg}"
                log(f"  ❌ {err}")
                log_list.append(f"[{nid}] 失败: {err}")
                context["nodes"][nid] = {"_error": err, "_failed": True}
                failures.append({"node": nid, "stage": "lora", "error": err, "attempts": 1})
                if policy["on_error"] == "fail_fast":
                    log(f"  ⛔ on_error=fail_fast，中止工作流")
                    return ("abort", err)
                return None
        else:
            provider_tag = f"🌐 {effective_provider}"
            if model_override:
                provider_tag += f" / {model_override}"
            log(f"  {provider_tag}")
            log_list.append(f"[{nid}] {provider_tag}")

        values = build_node_input(node, context)
        prompt = render_prompt(node.get("prompt", ""), values) + build_missing_notice(problems)

        # ③ 调用（可选分块）；文本节点取正文，JSON 节点还要确认解析成功
        attempts = 0
        raw = None
        parsed = None
        last_error = None
        parse_failed = False
        as_text = node_output_is_text(node, ntype)
        gen = node_generation_options(node, ntype)
        chunk_size = int(node.get("chunk_size") or 0)
        chunk_overlap = int(node.get("chunk_overlap") or 0)
        chunk_workers = max(1, int(node.get("chunk_workers") or 1))
        chunk_prompts, chunk_note = split_prompt_into_chunks(
            prompt, values, chunk_size, chunk_overlap)

        # 缓存：命中则跳过全部调用（键含替换后的输入与影响输出的配置，改配置自动失效）
        cache_key, cache_hit = None, None
        if CACHE_ENABLED and node.get("cache"):
            cache_key = node_cache_key(node, prompt, ntype)
            cache_hit = cache_load(cache_key)

        if cache_hit is not None:
            parsed = cache_hit.get("parsed")
            last_error, parse_failed = None, False
            attempts = int(cache_hit.get("attempts") or 1)
            if cache_hit.get("chunking"):
                chunking[nid] = dict(cache_hit["chunking"])
            log(f"  ♻️ 命中缓存（{cache_key}），跳过调用")
            log_list.append(f"[{nid}] 命中缓存 {cache_key}")
        elif chunk_prompts is None:
            if chunk_size > 0:
                log(f"  🧩 {chunk_note}")
                log_list.append(f"[{nid}] {chunk_note}")
            parsed, last_error, parse_failed, attempts = llm_call_with_retry(
                as_text, prompt, lora, provider, model_override, policy, log, gen,
                router=router)
        else:
            # —— 分块抽取：逐块调用后合并（长论文必须走这条路，否则会被压成极短输出）
            log(f"  🧩 分块抽取：{chunk_note}")
            log_list.append(f"[{nid}] {chunk_note}")
            outs, failed_chunks, parse_fail_chunks = [], [], 0
            results = []
            if chunk_workers > 1 and len(chunk_prompts) > 1:
                log(f"  ⚡ {chunk_workers} 路并发提交（vLLM 会自动批处理）")
                results = [None] * len(chunk_prompts)
                with concurrent.futures.ThreadPoolExecutor(max_workers=chunk_workers) as pool:
                    fut2idx = {
                        pool.submit(llm_call_with_retry, as_text, cp, lora, provider,
                                    model_override, policy, None, gen, router): i
                        for i, cp in enumerate(chunk_prompts)}
                    for fut in concurrent.futures.as_completed(fut2idx):
                        i = fut2idx[fut]
                        try:
                            results[i] = fut.result()
                        except Exception as e:
                            results[i] = (None, f"{type(e).__name__}: {e}", False, 1)
                results = [r if r is not None else (None, "未返回结果", False, 1)
                           for r in results]
            else:
                for cp in chunk_prompts:
                    try:
                        results.append(llm_call_with_retry(
                            as_text, cp, lora, provider, model_override, policy, log, gen,
                            router=router))
                    except Exception as e:
                        results.append((None, f"{type(e).__name__}: {e}", False, 1))

            for ci, (p_i, e_i, pf_i, a_i) in enumerate(results, 1):
                # 取各块的最大尝试次数：累加会得出"重试 N 次"这种误导性结论
                attempts = max(attempts, a_i)
                if e_i and p_i is None:
                    failed_chunks.append({"chunk": ci, "error": e_i})
                    log(f"    ❌ 第 {ci}/{len(chunk_prompts)} 块失败：{e_i[:70]}")
                    log_list.append(f"[{nid}] 块 {ci} 失败：{e_i[:120]}")
                    continue
                parse_fail_chunks += 1 if pf_i else 0
                outs.append(p_i)
                if chunk_workers == 1:      # 并发时不再逐块刷屏，块数与成功数见汇总
                    log(f"    ✓ 第 {ci}/{len(chunk_prompts)} 块完成（{len(str(p_i))} 字符）")

            chunk_info = {"chunks": len(chunk_prompts), "ok": len(outs),
                          "workers": chunk_workers,
                          "failed_chunks": [f["chunk"] for f in failed_chunks],
                          "errors": [f["error"][:200] for f in failed_chunks]}
            chunking[nid] = chunk_info
            if chunk_workers > 1:
                log(f"  ⚡ 并发完成：{len(outs)}/{len(chunk_prompts)} 块成功"
                    f"{'，' + str(len(failed_chunks)) + ' 块失败' if failed_chunks else ''}")

            if not outs:
                last_error = (f"全部 {len(chunk_prompts)} 个分块都失败："
                              f"{failed_chunks[0]['error'][:120]}")
                parsed = None
            elif as_text:
                parsed = merge_text_outputs([str(o) for o in outs])
                last_error, parse_failed = None, False
            else:
                good = [o for o in outs if isinstance(o, dict) and "_raw" not in o]
                bad = [o for o in outs if isinstance(o, dict) and "_raw" in o]
                if good:
                    merged, conflicts, mstats = merge_json_outputs(good)
                    chunk_info.update({"merge": mstats, "conflicts": conflicts[:20]})
                    parsed = merged
                    parse_failed = bool(bad) or parse_fail_chunks > 0
                    last_error = None
                    if bad:
                        log(f"  ⚠️ {len(bad)}/{len(outs)} 块未产出合法 JSON，已并入文本兜底")
                else:
                    merged = merge_text_outputs([str(o.get("_raw", "")) for o in outs])
                    parsed = {"_raw": merged, "_parse_error": True}
                    parse_failed, last_error = True, "模型返回的内容不是合法 JSON"

            if outs and failed_chunks:
                reason = f"{len(failed_chunks)}/{len(chunk_prompts)} 个分块调用失败，结果不完整"
                degraded.append({"node": nid, "reason": reason})
                log(f"  ⚠️ {reason}")
                log_list.append(f"[{nid}] 降级：{reason}")

        # 成功才写缓存：失败与解析失败都不缓存，否则会把一次坏结果固化下来
        if cache_key and cache_hit is None and parsed is not None and not parse_failed:
            cache_store(cache_key, parsed, chunking.get(nid), attempts)

        if last_error and parsed is None:
            # 调用彻底失败
            log(f"  ❌ 节点执行失败（尝试 {attempts} 次）: {last_error}")
            log_list.append(f"[{nid}] 失败（{attempts} 次）: {last_error}")
            context["nodes"][nid] = {"_error": last_error, "_failed": True, "_attempts": attempts}
            failures.append({"node": nid, "stage": "llm",
                             "error": last_error, "attempts": attempts})
            if policy["on_error"] == "fail_fast":
                log(f"  ⛔ on_error=fail_fast，中止工作流")
                return ("abort", last_error)
        elif parse_failed:
            # 调用成功但内容不可用：保留原文供 {输入} 兜底，同时记为降级
            context["nodes"][nid] = parsed
            reason = f"JSON 解析失败（尝试 {attempts} 次），已保留原始文本"
            degraded.append({"node": nid, "reason": reason})
            log(f"  ⚠️ {reason}")
            log_list.append(f"[{nid}] 降级: {reason}")
        else:
            context["nodes"][nid] = parsed
            size = len(str(parsed))
            kind = "（纯文本）" if as_text else ""
            # attempts 是"最多尝试次数"：分块时取各块最大值，不累加（累加会读成"重试 N 次"）
            retry_note = f"（最多重试 {attempts - 1} 次）" if attempts > 1 else ""
            chunk_note_short = f"，分块 {len(chunk_prompts)} 块" if chunk_prompts else ""
            log(f"  ✅ 完成{kind}{chunk_note_short}，输出 {size} 字符{retry_note}")
            log_list.append(f"[{nid}] 输出 {size} 字符{kind}{chunk_note_short}{retry_note}")
            # 节点 trace：这是"按节点取训练数据"的原料（prompt 截断存储，避免结果文件过大）
            traces[nid] = {
                "prompt": prompt[:TRACE_PROMPT_CHARS],
                "prompt_chars": len(prompt),
                "prompt_truncated": len(prompt) > TRACE_PROMPT_CHARS,
                # 用 JSON 文本而不是 Python repr —— 否则导出训练数据会教模型输出 {'a': 1}
                "output": (json.dumps(parsed, ensure_ascii=False, indent=2)
                           if isinstance(parsed, (dict, list)) else str(parsed))[:TRACE_OUTPUT_CHARS],
                "output_chars": size,
                "lora": lora,
                "provider": effective_provider,
                "chunks": (chunking.get(nid) or {}).get("chunks"),
            }

            # 节点级质量：只记录、不自动判定（阈值需标定，见 QUALITY_NOTE）。
            # 适用范围 = 类型声明了 quality，或"输出文本且确实有输入材料"——
            # 后者是为了让现有配置（process + output_format=text）也能产出标定样本。
            if node_type_spec(ntype).get("quality") == "grounding" or (as_text and values):
                source_text = "\n".join(str(v) for v in values.values())
                q = tm.grounding_report(str(parsed), source_text)
                q["source"] = ("type" if node_type_spec(ntype).get("quality") == "grounding"
                               else "text-output")
                q["eligible"] = len(source_text) >= 200
                quality[nid] = q
                fr = ("—" if q["key_fact_retention"] is None
                      else f"{q['key_fact_retention']}（{q['key_facts_matched']}/{q['key_facts']}）")
                gdisp = (f"{q['grounding']}" if q["grounding"] is not None
                         else f"不适用（{q['output_language']}→{q['source_language']} 跨语言）")
                log(f"  📊 质量观测：关键事实保留 {fr}，可溯源率 {gdisp}，"
                    f"每千字输入产出 {q['chars_per_1k_input']} 字")

                # 仅对"抽取型"节点判定：改写型（汇总/分析）低分是正常的，套同一标准会误报
                if q["source"] == "type" and q["eligible"]:
                    rt = q["key_fact_retention"]
                    bad_fact = rt is not None and rt < QUALITY_FACT_RETENTION_MIN
                    # 跨语言时 grounding 为 None → 该条不适用，不能当 0 分处理
                    bad_ground = (q["grounding"] is not None
                                  and q["grounding"] < QUALITY_GROUNDING_MIN)
                    bad_short = (q["input_chars"] >= QUALITY_SUFFICIENCY_MIN_INPUT
                                 and q["chars_per_1k_input"] < QUALITY_MIN_CHARS_PER_1K)
                    unmatched = q["key_facts"] - q["key_facts_matched"]
                    bad_unmatched = (q["key_facts"] >= QUALITY_UNMATCHED_MIN_FACTS
                                     and unmatched >= QUALITY_MAX_UNMATCHED_FACTS)
                    if bad_fact or bad_ground or bad_short or bad_unmatched:
                        # 逐条列出**到底哪一条**不达标，别让人去猜
                        why = []
                        if bad_fact:
                            why.append(f"关键事实保留 {rt} < {QUALITY_FACT_RETENTION_MIN}")
                        if bad_ground:
                            why.append(f"可溯源率 {q['grounding']} < {QUALITY_GROUNDING_MIN}")
                        if bad_unmatched:
                            why.append(
                                f"{unmatched} 个数字/缩写在输入里找不到出处"
                                f"（只容忍 {QUALITY_MAX_UNMATCHED_FACTS - 1} 条格式差异）"
                                f"→ 疑似编造，必须回查原文")
                        if bad_short:
                            why.append(
                                f"每千字输入只产出 {q['chars_per_1k_input']} 字 < "
                                f"{QUALITY_MIN_CHARS_PER_1K}（欠抽取：长文必须开分块，"
                                f"当前输入 {q['input_chars']} 字只输出了 {q['output_chars']} 字）")
                        reason = (f"节点 {nid} 抽取质量不达标：" + "；".join(why)
                                  + " → 建议用 build_dataset 导出该节点数据后重训")
                        quality_warnings.append({"node": nid, "reason": reason, "metrics": q})
                        log(f"  ⚠️ {reason}")
                        log_list.append(f"[{nid}] 质量：{reason}")

        time.sleep(0.3)

    def _usage_total():
        return sum(v.get("total_tokens", 0) for v in USAGE_STATS["by_provider"].values())

    for idx, node in enumerate(scheduled, 1):
        # A1：运行级墙钟检查（节点级 timeout 管单次请求，这里管整条流水线）
        _elapsed = time.time() - run_started
        if run_timeout > 0 and _elapsed > run_timeout:
            abort_reason = (f"运行超时：已用 {_elapsed:.0f}s，超过 run_timeout={run_timeout:g}s"
                            "（已完成节点的结果已保留）")
            abort_kind = "timeout"
            log(f"⏱️ {abort_reason}")
            log_list.append(f"[运行] {abort_reason}")
            break
        # O2：在**派发处**计时而不改 execute_node 的 5 个 return 分支（低风险）。
        # token 用量取"调用前后的差值"—— USAGE_STATS 是全局累加的，没有节点维度。
        _nid = node.get("id")
        _t0, _tok0 = time.time(), _usage_total()
        signal = execute_node(node, idx, total)
        _out = context["nodes"].get(_nid)
        if isinstance(_out, dict) and _out.get("_failed"):
            _st = "failed"
        elif any(d.get("node") == _nid for d in degraded):
            _st = "degraded"
        elif _out is None:
            _st = "skipped"          # 被上游失败传播或策略跳过
        else:
            _st = "ok"
        _chars = len(_out) if isinstance(_out, str) else (
            len(json.dumps(_out, ensure_ascii=False)) if isinstance(_out, dict) else 0)
        node_stats[_nid] = {
            "sec": round(time.time() - _t0, 2),
            "type": node.get("type"),
            "lora": node.get("lora"),
            "status": _st,
            "chars_out": _chars,
            "tokens": _usage_total() - _tok0,
        }
        if signal:                      # ("abort", reason)
            abort_reason = signal[1]
            abort_kind = "fail_fast"
            break
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(OUTPUT_DIR, f"result_{ts}.json")

    final_report = None
    for node in reversed(ordered_nodes):
        if node.get("type") == "aggregate":
            v = context["nodes"].get(node["id"])
            if isinstance(v, str) and v.strip():
                final_report = v
            break

    # ④ 有失败/降级时：报告必须自带警告头；连报告都没产出就显式说明
    had_report = bool(final_report)          # 先记下"汇总节点究竟有没有产出"
    header = build_report_header(failures, degraded)
    if header:
        if final_report:
            final_report = header + final_report
        else:
            final_report = header + "（汇总节点未能产出报告，详见 _meta.failures）"

    # 运行超时：报告必须自带说明，否则读者会把不完整的报告当成完整的
    if abort_kind == "timeout":
        thead = (f"> ⏱️ **本次运行因超时中止**（{abort_reason}）。"
                 "以下内容只覆盖已完成的节点，不是完整分析。\n\n")
        final_report = (thead + final_report) if final_report else (thead + "（未产出报告）")

    # 抽取质量不达标同样要显形（与"依赖缺失/降级"分开表述，语义更准）
    if quality_warnings:
        qhead = ("> ⚠️ **以下节点抽取质量不达标，引用其中数据前请回查原文：**\n"
                 + "".join(f"> - {w['reason']}\n" for w in quality_warnings) + "\n")
        final_report = (qhead + final_report) if final_report else (qhead + "（未产出报告）")

    status = "failed" if (failures and not had_report) else (
        "partial" if (failures or degraded or quality_warnings
                      or abort_kind == "timeout") else "success")

    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            "source_file": os.path.basename(file_path),
            "workflow": config.get("workflow_name", "未命名"),
            "status": status,
            "log": log_list,
            "nodes": context["nodes"],
            "final_report": final_report,
            "_meta": {
                "usage": USAGE_STATS,
                "config_warnings": config_warnings,
                "lora_preflight": lora_info,
                "lora_swaps": getattr(router, "swaps", []),
                "lora_swaps_planned": planned,
                "lora_swaps_naive": naively,
                "providers_used": list(USAGE_STATS["by_provider"].keys()),
                "failures": failures,
                "degraded": degraded,
                "missing_inputs": missing_inputs,
                "kb_resolution": kb_resolution,
                "chunking": chunking,
                "traces": traces,
                "quality": quality,
                "node_stats": node_stats,
                "quality_warnings": quality_warnings,
                "trace_note": f"prompt/output 分别截断至 {TRACE_PROMPT_CHARS}/{TRACE_OUTPUT_CHARS} 字符",
                "aborted": abort_reason,
                "abort_kind": abort_kind,
                "run_timeout": run_timeout,
                "run_elapsed": round(time.time() - run_started, 1),
                "run_timed_out": abort_kind == "timeout",
                "node_policy_default": DEFAULT_NODE_POLICY,
            }
        }, f, ensure_ascii=False, indent=2)

    if abort_kind == "timeout":
        log(f"⏱️ 本次运行因超时中止（已用 {round(time.time() - run_started, 1)}s），"
            f"状态 {status}；已完成节点的结果已保留")
    if failures or degraded:
        log(f"⚠️ 本次运行存在问题：{len(failures)} 个节点失败、{len(degraded)} 个节点降级"
            + ("（已按 fail_fast 中止）" if abort_kind == "fail_fast" else ""))
        for fx in failures:
            log(f"   ❌ {fx['node']}（{fx['stage']}）: {str(fx['error'])[:100]}")
        for dx in degraded:
            log(f"   ⚠️ {dx['node']}: {dx['reason']}")
    log(f"工作流完成（状态 {status}），结果保存到 {out_file}")
    if USAGE_STATS["total_calls"]:
        log(f"LLM 调用统计: {json.dumps(USAGE_STATS['by_provider'], ensure_ascii=False)}")
    return out_file


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python workflow_engine.py <论文文件路径> [配置文件路径]")
        sys.exit(1)
    try:
        run_workflow(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    except WorkflowPreflightError as e:
        # 保持命令行行为：预检失败仍然以对应码退出（1 拓扑 / 2 配置 / 3 LoRA）
        print(f"❌ {e.message}")
        for d in e.details:
            print(f"    - {d}")
        sys.exit(e.code)