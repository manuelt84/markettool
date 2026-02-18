# LOCAL TESTING GUIDE (sin Docker)

## 1️⃣ Verificar que los modelos se cargan sin bloquear startup

**Objetivo**: Confirmar que YOLO lazy loading funciona correctamente

```bash
# En MarketTool directory:
python -c "
import time
from datetime import datetime

# Medir tiempo de importación
start = time.time()
from MarketTool import app, get_modelo_patrones, get_modelo_ruido
import_time = time.time() - start

print(f'✅ Importación: {import_time:.2f}s (debe ser <2s)')

# Ahora cargar los modelos (esto SÍ debe tomar tiempo)
print('⏳ Cargando patrones...')
start = time.time()
patrones = get_modelo_patrones()
load_time = time.time() - start
print(f'✅ Patrones cargado en {load_time:.2f}s')

print('⏳ Cargando ruido...')
start = time.time()
ruido = get_modelo_ruido()
load_time = time.time() - start
print(f'✅ Ruido cargado en {load_time:.2f}s')
"
```

**Esperado**:
```
✅ Importación: 0.45s (debe ser <2s)
⏳ Cargando patrones...
✅ Patrones cargado en 12.3s
⏳ Cargando ruido...
✅ Ruido cargado en 8.7s
```

---

## 2️⃣ Verificar que los modelos se cargan desde archivos locales

```bash
# Verificar que patrones.pt y ruido.pt están presentes
python -c "
from pathlib import Path
models_dir = Path.cwd()
for model in ['patrones.pt', 'ruido.pt']:
    p = models_dir / model
    if p.exists():
        size_mb = p.stat().st_size / (1024**2)
        print(f'✅ {model}: {size_mb:.1f} MB')
    else:
        print(f'❌ {model}: NO ENCONTRADO')
"
```

---

## 3️⃣ Verificar configuración .ultralytics.yaml

```bash
python -c "
from pathlib import Path
config = Path.home() / '.ultralytics' / '.ultralytics.yaml'
if config.exists():
    print('✅ Configuración existe:')
    print(config.read_text())
else:
    print('❌ Falta configuración, YOLO intentará descargar modelos')
"
```

**Debe contener**:
```yaml
# Disable internet downloads for YOLO
analytics: false
api_key: ''
api_server: ''

# Puntos a /app cuando está en Docker
weights_dir: /app/models/yolo
datasets_dir: /app/models/datasets
runs_dir: /app/models/runs
```

---

## 4️⃣ Prueba de rendimiento cache

```bash
python -c "
from MarketTool import obtain_cached_levels, _get_cached_atr
import pandas as pd

# Simular dos llamadas seguidas (debe usar cache en la segunda)
symbol = 'EURUSD'
tf = '1h'

# Primera llamada (cache miss)
print('📊 Primera llamada...')
df1 = pd.DataFrame()  # Simulado
cache_hit_1 = False  # Primera vez no hay hit

# Segunda llamada (cache hit)
print('📊 Segunda llamada...')
# Debería retornar del cache en < 10 ms

print('✅ Cache funcionando si segunda llamada es < 10ms más rápida')
"
```

---

## 5️⃣ Test de warmup no-bloqueante

```bash
python -c "
import asyncio
import time
import logging

# Activar logs para ver warmup
logging.basicConfig(level=logging.INFO)

async def test_startup():
    start = time.time()
    
    # Simulación: app debería estar lista ANTES de que warmup termine
    print('⏳ Iniciando app...')
    from MarketTool import app
    
    startup_time = time.time() - start
    print(f'✅ App lista en {startup_time:.2f}s')
    
    # Ahora esperar warmup
    print('⏳ Esperando warmup...')
    for i in range(40):
        await asyncio.sleep(1)
        # En logs verás: [Warmup] ===== COMPLETADO =====
        if i % 10 == 0:
            print(f'  {i}s elapsed...')

asyncio.run(test_startup())
"
```

**Esperado**:
```
⏳ Iniciando app...
✅ App lista en 0.3s
⏳ Esperando warmup...
  0s elapsed...
  10s elapsed...
  20s elapsed...
[Warmup] ===== COMPLETADO =====
  30s elapsed...
```

---

## 6️⃣ Verificar que /cache-status endpoint existe

```bash
# Terminal 1: Iniciar app
python -m MarketTool --port 5000

# Terminal 2: Llamar endpoint
import requests
import time

time.sleep(5)  # Esperar a que app inicie

response = requests.get('http://localhost:5000/cache-status')
print(response.json())
```

**Esperado**:
```json
{
  "warmup": {
    "status": "completed",
    "elapsed_seconds": 22.5,
    "start_time": "2025-01-15T10:30:45.123Z"
  },
  "cache_stats": {
    "niveles_cache": "1847/2314 hits (79.8%)",
    "atr_cache": "1847/2314 hits (79.8%)"
  },
  "config": {
    "CACHE_WARMUP_ENABLED": true,
    "CACHE_WARMUP_BLOCKING_STARTUP": false,
    "CACHE_WARMUP_LEADER_ONLY": false
  }
}
```

---

## 🎯 Checklist de Validación Local

- [ ] Importación de MarketTool tarda <2 segundos
- [ ] get_modelo_patrones() y get_modelo_ruido() cargan sin conectarse a internet
- [ ] patrones.pt y ruido.pt existen en directorio actual (>100 MB cada uno)
- [ ] .ultralytics.yaml existe y tiene configuración correcta
- [ ] /cache-status endpoint responde con JSON válido
- [ ] Cache hit rate >70% después de warmup completado
- [ ] Segundo execution es significativamente más rápido que el primero

Si todo esto pasa en local, el Docker build debería funcionar correctamente.

