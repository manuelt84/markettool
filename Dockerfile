FROM python:3.12-slim

# Instalar dependencias del sistema para OCR y visión por computadora
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libglib2.0-0 libsm6 libxext6 libxrender-dev libgl1-mesa-glx \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requirements antes para aprovechar cache
COPY requirements.txt .

# Instalar PyTorch con soporte CUDA (cu121) + resto de dependencias
RUN pip install --no-cache-dir torch==2.2.2+cu121 torchvision==0.17.2+cu121 torchaudio==2.2.2 \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
    && pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Exponer puertos
EXPOSE 8080 443

# Comando por defecto
CMD ["python", "MarketTool.py", "--host", "0.0.0.0", "--port", "8080"]
