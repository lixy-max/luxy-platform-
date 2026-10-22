# 顿悟 luxy · 单机模型垂直开发平台
# CPU 可跑（知识库/规则/文档/工作流）；GPU 推理与训练需 --gpus 与 vLLM
FROM python:3.11-slim

LABEL org.opencontainers.image.title="luxy-platform"
LABEL org.opencontainers.image.description="单机模型垂直开发平台：数据→训练→知识库→Agent→工作流"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BACKEND_PORT=8001 \
    WEB_PORT=8080

WORKDIR /app

# 系统依赖：构建 wheel + 健康检查 + 中文 locale
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单，利用层缓存
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# 应用代码
COPY . .

# 运行时数据目录（建议挂载卷，避免写进镜像层）
RUN mkdir -p data chroma_db loras training_data uploads logs training_output outputs \
    && chmod +x install/*.sh scripts/run.sh 2>/dev/null || true

# ★ 非 root 运行（uid 1000，与常见宿主机用户一致，便于挂载卷读写）
RUN useradd -m -u 1000 -s /bin/bash luxy \
    && mkdir -p /app/data /app/chroma_db /app/loras /app/training_data \
                /app/uploads /app/logs /app/training_output /app/outputs /app/models \
    && chown -R luxy:luxy /app

USER luxy

EXPOSE 8001 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health" || exit 1

# 单进程起后端 + 前端（不依赖 systemd）
CMD ["bash", "install/start.sh"]

