FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Official prebuilt CPU llama-server; it picks the matching libggml-cpu-*.so at runtime (haswell on the i5-4590).
ARG LLAMA_CPP_BUILD=b12521
RUN mkdir -p /opt/llama \
    && curl -fsSL "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_BUILD}/llama-${LLAMA_CPP_BUILD}-bin-ubuntu-x64.tar.gz" \
       | tar -xz -C /opt/llama --strip-components=1 \
    && test -x /opt/llama/llama-server
ENV LD_LIBRARY_PATH=/opt/llama
WORKDIR /app
COPY requirements.txt requirements-ml.txt ./
# EmbeddingGemma-2 needs torchvision; 0.29 is the newest CPU build and requires torch 2.14.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.14.*" "torchvision==0.29.*" \
    && pip install --no-cache-dir -r requirements-ml.txt
COPY app ./app
COPY web ./web
COPY scripts ./scripts
ENV DATA_DIR=/data HF_HOME=/data/models PYTHONUNBUFFERED=1 TZ=Europe/Istanbul
EXPOSE 8080
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
