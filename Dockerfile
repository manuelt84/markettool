FROM python:3.12-slim

# Paquetes del sistema para OpenCV/EasyOCR (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc \
    libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala primero PyTorch (mejor cache) y luego el resto
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
    torch==2.2.2+cu121 torchvision==0.17.2+cu121 torchaudio==2.2.2 \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
 && pip install --no-cache-dir -r requirements.txt

# Código
COPY . .

EXPOSE 8080
ENV PORT=8080

CMD ["python", "MarketTool.py", "--host", "0.0.0.0", "--port", "8080"]
