# -*- coding: utf-8 -*-
"""S26 验证用 mock 工蜂节点（诚实标注：非真实模型）

用途：在没有第二块 GPU 的情况下验证"多节点调度/容量/故障转移"架构。
接口对齐 vLLM 的最小面：/health、/v1/models、/mock/generate。
真实 1.5B 工蜂上线后，用同 schema 注册替换即可（type=vllm）。
"""
import json
import os
import time

from fastapi import FastAPI, Request
import uvicorn

NODE_ID = os.environ.get("MOCK_NODE_ID", "w2")
PORT = int(os.environ.get("MOCK_PORT", "8003"))
SERVED = os.environ.get("MOCK_SERVED_MODEL", "mock-1.5b")
app = FastAPI(title="Hive Mock Worker")


@app.get("/health")
def health():
    return {"ok": True, "node": NODE_ID, "served_model": SERVED,
            "mock": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": SERVED, "object": "model",
                                        "owned_by": "hive-mock"}]}


@app.post("/mock/generate")
async def generate(payload: dict):
    req = str(payload.get("request") or "")[:200]
    max_tokens = int(payload.get("max_tokens") or 64)
    t0 = time.time()
    text = ("[%s/mock] 已收到任务：%s（max_tokens=%d，本回复为架构验证用合成文本）"
            % (NODE_ID, req[:80], max_tokens))
    return {"ok": True, "node": NODE_ID, "text": text,
            "latency_ms": round((time.time() - t0) * 1000, 1), "mock": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
