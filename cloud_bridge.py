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


def ask(prompt, system=None, max_tokens=1200, temperature=0.2, timeout=120):
    """调用云端模型；未配置时抛 RuntimeError（调用方需降级）"""
    cfg = _provider()
    if not cfg.get("api_key"):
        raise RuntimeError("云 provider 未配置 key")
    import requests
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": str(prompt)})
    r = requests.post(cfg["api_base"] + "/chat/completions",
                      headers={"Authorization": "Bearer " + cfg["api_key"],
                               "Content-Type": "application/json"},
                      json={"model": cfg["model"], "messages": messages,
                            "max_tokens": max_tokens, "temperature": temperature},
                      timeout=timeout)
    r.raise_for_status()
    d = r.json()
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
