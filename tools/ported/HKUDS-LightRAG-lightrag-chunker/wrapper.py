# -*- coding: utf-8 -*-
"""重建适配器：递归字符分块（思路借鉴 LightRAG chunker/recursive_character，独立重写）

来源：https://github.com/HKUDS/LightRAG （MIT License，见 ATTRIBUTION.md）
本文件为**独立最小实现**：仅标准库，无外部依赖。
"""


def chunk(text, chunk_size=500, overlap=50, separators=None):
    """把长文本按分隔符优先级切成带重叠的块。

    思路（借鉴）：优先按段落/句子切分，再合并到不超过 chunk_size；
    相邻块之间保留 overlap 字符重叠，避免语义截断。
    """
    text = str(text or "")
    chunk_size = max(50, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size // 2))
    seps = separators or ["\n\n", "\n", "。", "！", "？", "；", "，", " "]

    def _split(t):
        # 先按最高优先级分隔符切；若切不动（无分隔符）则硬切
        for sep in seps:
            if sep and sep in t:
                parts = [p for p in t.split(sep) if p]
                out = []
                for p in parts:
                    piece = p + sep
                    if len(piece) <= chunk_size:
                        out.append(piece)
                    else:
                        out.extend([piece[i:i + chunk_size]
                                    for i in range(0, len(piece), chunk_size)])
                return out
        return [t[i:i + chunk_size] for i in range(0, len(t), max(1, chunk_size - overlap))]

    pieces = _split(text)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > chunk_size:
            chunks.append(cur)
            cur = (cur[-overlap:] if overlap else "") + p
        else:
            cur += p
    if cur.strip():
        chunks.append(cur)
    return {"chunks": [c for c in chunks if c.strip()], "count": len([c for c in chunks if c.strip()]),
            "chunk_size": chunk_size, "overlap": overlap}


def __self_test__():
    r = chunk("第一段。第二段！第三段？" * 30, chunk_size=40, overlap=5)
    return {"ok": r["count"] > 0, "chunks": r["count"]}
