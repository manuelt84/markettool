FROM python:3.12-slim

# Paquetes del sistema para OpenCV/EasyOCR (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc \
    libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependencias Python (mejor cache)
COPY requirements.txt .

# ✅ Importante: actualizar pip + setuptools + wheel primero
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Instalar PyTorch separado (con índice especial)
RUN python -m pip install --no-cache-dir \
    torch==2.2.2+cu121 torchvision==0.17.2+cu121 torchaudio==2.2.2 \
    --extra-index-url https://download.pytorch.org/whl/cu121

# Instalar el resto de dependencias
RUN python -m pip install --no-cache-dir -r requirements.txt

# ✅ MEJORA: Copiar modelos YOLO PRIMERO (cachea correctamente)
COPY patrones.pt ruido.pt ./

# ✅ Copiar configuración YOLO (para NO descargar de internet)
COPY .ultralytics.yaml /root/.ultralytics/.ultralytics.yaml

# ✅ MEJORA: Crear directorio de modelos para EasyOCR/Torch
RUN mkdir -p /app/models/torch /app/models/easyocr /root/.ultralytics

# Copiar el resto del código
COPY . .

# ✅ Validar que los modelos existen
RUN if [ ! -f /app/patrones.pt ]; then echo "ERROR: patrones.pt no encontrado en /app"; exit 1; fi && \
    if [ ! -f /app/ruido.pt ]; then echo "ERROR: ruido.pt no encontrado en /app"; exit 1; fi && \
    echo "✅ Modelos YOLO encontrados correctamente" && \
    ls -lh /app/*.pt

EXPOSE 8080
ENV PORT=8080

# Optimizaciones Python para alto rendimiento
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1
ENV PYTHONDONTWRITEBYTECODE=1

# Optimización de GC para workloads con alta concurrencia
ENV MALLOC_TRIM_THRESHOLD_=100000
ENV MALLOC_MMAP_THRESHOLD_=100000

# ✅ Cache de modelos (para evitar descargas)
ENV TORCH_HOME=/app/models/torch
ENV EASY_OCR_MODEL_DIR=/app/models/easyocr

# ✅ CRÍTICO: Desabilitar descargas automáticas de YOLO
ENV YOLO_ENV_VARS_SERVER=https://disabled
ENV YOLO_CACHE=/app/models/yolo
RUN mkdir -p /app/models/yolo

CMD ["python", "MarketTool.py", "--host", "0.0.0.0", "--port", "8080"]
