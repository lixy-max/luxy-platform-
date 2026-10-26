# -*- coding: utf-8 -*-
"""cloud_bridge.py —— 云端导师调用桥（可选）

用途：奇美拉云审 / 主脑 cloud 路由。key 未配置时 available()=False（调用方诚实降级）。
配置来源（优先级）：环境变量 CLOUD_* > settings.json providers.openai > providers 里第一个有 key 的。
"""
import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS = PROJECT_DIR / "settings.json"


def _provider():
    base = os.environ.get("CLOUD_API_BASE") or ""
    key = os.environ.get("CLOUD_API_KEY") or ""
    model = os.environ.get("CLOUD_MODEL") or ""
    if key and base:
        return {"api_base": base, "api_key": key, "model": model or "deepseek-chat"}
    try:
        st = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    prov = st.get("providers") or {}
    # 优先 openai，其次任意有 key 的
    order = ["openai"] + [k for k in prov if k != "openai"]
    for name in order:
        cfg = prov.get(name) or {}
        k = str(cfg.get("api_key") or "").strip()
        b = str(cfg.get("api_base") or "").strip()
        if k and b and "127.0.0.1" not in b and "localhost" not in b:
            return {"api_base": b.rstrip("/"), "api_key": k,
                    "model": str(cfg.get("model") or "deepseek-chat")}
    return {}


def available():
    return bool(_provider().get("api_key"))


def ask(prompt, system=None, max_tokens=2000, temperature=None, timeout=180, retries=4):
    """调用云端模型；未配置时抛 RuntimeError（调用方需降级）

    · 兼容 Kimi K2：该系列只接受 temperature=1（不传则用 API 默认）
    · 429 限流：自动退避重试（1s/2s/4s）
    """
    cfg = _provider()
    if not cfg.get("api_key"):
        raise RuntimeError("云 provider 未配置 key")
    import time
    import requests
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": str(prompt)})
    body = {"model": cfg["model"], "messages": messages, "max_tokens": max_tokens}
    _m = str(cfg["model"]).lower()
    if "kimi-k2" in _m or "moonshot" in _m:
        body["temperature"] = 1          # K2 系列只允许 1
    elif temperature is not None:
        body["temperature"] = temperature
    else:
        body["temperature"] = 0.2
    last_err = None
    for attempt in range(int(retries)):
        r = requests.post(cfg["api_base"] + "/chat/completions",
                          headers={"Authorization": "Bearer " + cfg["api_key"],
                                   "Content-Type": "application/json"},
                          json=body, timeout=timeout)
        if r.status_code == 429:
            last_err = "429 rate limit"
            time.sleep(min(30, 5 * (2 ** attempt)))   # 5s/10s/20s/30s（Kimi RPM 限流较严）
            continue
        r.raise_for_status()
        d = r.json()
        msg = ((d.get("choices") or [{}])[0].get("message") or {})
        content = str(msg.get("content") or "").strip()
        if content:
            return content
        # Kimi K2 是思考模型：token 预算太小时 content 可能为空，用 reasoning 兜底
        reasoning = str(msg.get("reasoning_content") or "").strip()
        if reasoning:
            return reasoning[-1500:]
        return ""
    raise RuntimeError("云端限流重试失败: %s" % last_err)
