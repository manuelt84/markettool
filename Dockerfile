FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl gcc libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 🚫 PASO 1: Copiar .pt PRIMERO antes de cualquier pip install
COPY patrones.pt ruido.pt ./

# 🚫 PASO 2: Crear dirs y copiar config YOLO
RUN mkdir -p /app/models/torch /app/models/easyocr /app/models/yolo /root/.ultralytics
COPY .ultralytics.yaml /root/.ultralytics/settings.yaml

# 🚫 PASO 3: Env vars OFFLINE ANTES de ultralytics pip install
ENV TORCH_HOME=/app/models/torch
ENV EASY_OCR_MODEL_DIR=/app/models/easyocr
ENV YOLO_CACHE_DIR=/app/models/yolo
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

# 🚫 PASO 4: pip install con modelos ya presentes
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel
RUN python -m pip install --no-cache-dir torch==2.2.2+cu121 torchvision==0.17.2+cu121 torchaudio==2.2.2 --extra-index-url https://download.pytorch.org/whl/cu121
RUN python -m pip install --no-cache-dir -r requirements.txt

# 🚫 PASO 4b: pip install completado
# Nota: easyocr models se descargan on-demand (lazy loading)
#       pero inician en background para la mayoría de casos
# Esto mantiene imagen Docker pequeña (~1GB en lugar de ~1.2GB)

# Install Playwright browsers at build time so runtime doesn't need downloads
RUN python -m playwright install --with-deps

# 🚫 PASO 5: Validar modelos estan presentes
RUN [ -f /app/patrones.pt ] && [ -f /app/ruido.pt ] && ls -lh /app/*.pt && echo "✅ Models present" || (echo "❌ Models missing" && exit 1)

# PASO 6: Copiar codigo
COPY . .

EXPOSE 8080
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV MALLOC_TRIM_THRESHOLD_=100000
ENV MALLOC_MMAP_THRESHOLD_=100000

# 🔐 THREAD SAFETY: Enable ProcessPoolExecutor with spawn context (safer than fork with gRPC)
# spawn creates fresh Python interpreter, avoiding fork() issues with gRPC threads
ENV ANALYSIS_PRED_USE_PROCESS=true
ENV PYTHONMALLOC=pymalloc

# CACHE WARMUP: Optimize GCP downloads with higher concurrency (I/O-bound)
ENV CACHE_WARMUP_CONCURRENCY=16

# GCS UPLOAD PARALLELISM: Increase for 455+ files
# 200 concurrent uploads to GCP bucket (default was 30)
# GCP bucket can handle 10,000 ops/sec, so 200 is safe
ENV UPLOAD_SEM=200

# GCP/gRPC thread safety settings
ENV GRPC_PYTHON_BUILD_WITH_CYTHON=false
ENV GRPC_WORKER_THREADS=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
	CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["python", "-m", "markettool.bootstrap"]
