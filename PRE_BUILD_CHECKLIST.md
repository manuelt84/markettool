# PRE-BUILD CHECKLIST

Antes de ejecutar `docker build`, verifica estos puntos:

## 📋 Archivos Presentes

```bash
ls -lh $MARKETTOOL_DIR/
```

Debe mostrar:
- [ ] ✅ `patrones.pt` (~150+ MB)
- [ ] ✅ `ruido.pt` (~150+ MB)
- [ ] ✅ `.ultralytics.yaml`
- [ ] ✅ `Dockerfile`
- [ ] ✅ `MarketTool.py`
- [ ] ✅ `requirements.txt`

## 🔍 Código - Verificaciones Críticas

### 1. MarketTool.py - _APP_ROOT definido

```bash
grep -n "_APP_ROOT = " MarketTool.py
```

Debe retornar línea similar a:
```
_APP_ROOT = Path(__file__).parent.absolute()
```

### 2. MarketTool.py - Lazy loading functions

```bash
grep -n "def get_modelo_patrones" MarketTool.py
grep -n "def get_modelo_ruido" MarketTool.py
```

Ambas deben existir (no usar `YOLO()` directamente).

### 3. MarketTool.py - _load_yolo_model usando absolute path

```bash
grep -n "model_file = _APP_ROOT" MarketTool.py
```

Debe estar presente para resolver rutas absolutas.

### 4. No hay YOLO() calls en module level

```bash
grep -n "^YOLO(" MarketTool.py
grep -n "^modelo_patrones = " MarketTool.py
grep -n "^modelo_ruido = " MarketTool.py
```

Debe retornar vacío (sin matches a nivel de módulo).

### 5. Dict logging.basicConfig() aparece solo una vez

```bash
grep -c "logging.basicConfig" MarketTool.py
```

Debe retornar `1` (exactamente una vez).

### 6. No hay datetime.utcnow()

```bash
grep "utcnow()" MarketTool.py
```

Debe retornar vacío (todos reemplazados por `datetime.now(UTC)`).

## 🐳 Dockerfile - Verificaciones

### 1. COPY models antes de COPY .

```bash
grep -n "^COPY" Dockerfile | head -5
```

Primer COPY debe ser `patrones.pt ruido.pt` (mejor caching layer).

### 2. Validation RUN está presente

```bash
grep -n "if \[ ! -f /app/patrones.pt \]" Dockerfile
```

Debe existir para fallo si modelos no copian correctamente.

### 3. Environment variables YOLO

```bash
grep -n "YOLO_CACHE" Dockerfile
```

Debe estar: `ENV YOLO_CACHE=/app/models/yolo`

### 4. COPY .ultralytics.yaml

```bash
grep -n ".ultralytics.yaml" Dockerfile
```

Debe existir para aplicar configuración YOLO.

### 5. Base image correcto

```bash
grep "^FROM " Dockerfile
```

Debe ser `python:3.12-slim` o compatible.

## ⚙️ .ultralytics.yaml - Verificaciones

### 1. File exists

```bash
ls -l .ultralytics.yaml
```

### 2. Key settings present

```bash
grep -E "analytics:|api_server:|weights_dir:" .ultralytics.yaml
```

Debe incluir:
- `analytics: false`
- `api_server: ''` (empty)
- `weights_dir: /app/models/yolo`

## 🔗 Dependencies - Verificaciones

### 1. Ultralytics en requirements.txt

```bash
grep -i ultralytics requirements.txt
```

### 2. PyTorch en requirements.txt

```bash
grep -i torch requirements.txt
```

Puede ser `torch` o especificado con versión/backend.

## 🚀 Build Command

Una vez pasadas todas las verificaciones:

```bash
cd /path/to/MarketTool
docker build -t markettool:latest . --no-cache
```

El `--no-cache` es importante la primera vez para asegurar que no usa layers viejos.

## 📊 Expected Build Output

Debe contener:
```
Step X/Y : COPY patrones.pt ruido.pt ./
 ---> [hash]

Step X+1/Y : RUN if [ ! -f /app/patrones.pt ]; then...
 ---> Running in [container]
[validation output]
```

Si ves `Downloading yolov8n.pt` o similar = **PROBLEMA**, detén build y verifica:
1. Modelos están en directorio actual?
2. .ultralytics.yaml es válido?
3. YOLO_CACHE env var en Dockerfile?

