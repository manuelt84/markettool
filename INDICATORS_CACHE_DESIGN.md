# 📊 INDICATORS CACHE SYSTEM - Design Document

## 🎯 PROBLEMA

**Cálculos repetitivos costosos:**
- 50 activos × 8 temporalidades = **400 combinaciones**
- Cada una calcula: RSI, MACD, Bollinger, ATR, Soportes/Resistencias, Ponderaciones
- **Tiempo total: ~30 minutos por ejecución**
- Los datos históricos ya calculados **no cambian**, solo se agregan nuevas velas

## ✅ SOLUCIÓN

### Caché de Indicadores Procesados

```
┌──────────────────────────────┐
│  Históricos en GCS           │  ← Ya existe (PHASE 2 completado)
│  historicos/EURUSD__1day.json│
└──────────────────────────────┘
                ↓
┌──────────────────────────────┐
│  Indicadores Calculados      │  ← NUEVO
│  indicators/EURUSD__1day.json│
│  {                            │
│    "last_update": "2026-02-11T10:00:00Z",
│    "data_hash": "abc123...",  │
│    "indicators": {            │
│      "rsi": [...],            │
│      "macd": [...],           │
│      "soportes": [...],       │
│      ...                      │
│    }                          │
│  }                            │
└──────────────────────────────┘
```

### Flujo de Cálculo Inteligente

```python
def calcular_indicadores_con_cache(symbol, tf, df_historicos):
    # 1. Obtener caché de indicadores (si existe)
    cached = load_indicators_from_gcs(symbol, tf)
    
    if cached is None:
        # Primera vez: calcular todo
        indicators = calcular_indicadores_completo(df_historicos, tf)
        save_indicators_to_gcs(symbol, tf, indicators)
        return indicators
    
    # 2. Validar si los datos históricos cambiaron
    cached_hash = cached['data_hash']
    current_hash = hash_dataframe(df_historicos)
    
    if cached_hash == current_hash:
        # Datos idénticos: reutilizar 100% del caché
        return cached['indicators']
    
    # 3. Calcular solo las velas nuevas (incremental)
    cached_rows = len(cached['indicators']['close'])
    new_rows = len(df_historicos)
    
    if new_rows > cached_rows:
        # Hay velas nuevas: calcular solo las últimas
        # IMPORTANTE: los indicadores con window necesitan context
        window = definir_window(tf)
        
        # Tomar últimas (window + new_bars) para recalcular correctamente
        context_start = max(0, cached_rows - window)
        df_to_calc = df_historicos.iloc[context_start:]
        
        # Calcular indicadores solo para el segmento
        partial = calcular_indicadores_completo(df_to_calc, tf)
        
        # Combinar: mantener cache antiguo + nuevos valores
        indicators = merge_indicators(cached['indicators'], partial, cached_rows)
        
        # Guardar actualizado
        save_indicators_to_gcs(symbol, tf, indicators, df_historicos)
        return indicators
    else:
        # Los datos se redujeron (raro): recalcular todo
        indicators = calcular_indicadores_completo(df_historicos, tf)
        save_indicators_to_gcs(symbol, tf, indicators)
        return indicators
```

## 🏗️ ARQUITECTURA

### Estructura GCS

```
gs://markettool/
├── historicos/
│   └── EURUSD__1day.json          (datos OHLCV raw)
│
├── indicators/                     ← NUEVO
│   └── EURUSD__1day.json          (indicadores calculados)
│
└── metadata/
    └── indicators_metadata/        ← NUEVO
        └── EURUSD__1day            (Firestore doc)
```

### Metadata en Firestore

**Collection: `indicators_metadata`**

```json
{
  "doc_id": "EURUSD__1day",
  "symbol": "EURUSD",
  "timeframe": "1day",
  "gcs_path": "gs://markettool/indicators/EURUSD__1day.json",
  "last_update_utc": "2026-02-11T10:00:00Z",
  "data_hash": "sha256:abc123...",
  "rows_count": 500,
  "indicators_list": ["rsi", "macd", "bollinger", "atr", "soportes", "resistencias"],
  "calc_duration_ms": 1234,
  "ttl_hours": 4,
  "is_valid": true
}
```

### Indicadores Cacheados (JSON)

```json
{
  "metadata": {
    "symbol": "EURUSD",
    "timeframe": "1day",
    "last_update_utc": "2026-02-11T10:00:00Z",
    "data_hash": "sha256:abc123...",
    "rows_count": 500,
    "calc_duration_ms": 1234
  },
  "indicators": {
    "SMA": [1.0501, 1.0502, ...],
    "bollinger_upper": [1.0550, ...],
    "bollinger_lower": [1.0450, ...],
    "bollinger_signal": ["Neutral", "Compra", ...],
    "ema_12": [...],
    "ema_26": [...],
    "macd": [...],
    "signal": [...],
    "macd_cruce": ["No cruce", "Cruce Alcista", ...],
    "rsi": [45.2, 48.1, ...],
    "%K": [...],
    "%D": [...],
    "ATR": [...],
    "divergencia_macd_bull": [false, true, ...],
    "divergencia_rsi_bear": [false, false, ...],
    "soportes_dinamicos": [[1.0450, 1.0420], ...],
    "resistencias_dinamicas": [[1.0580, 1.0610], ...]
  }
}
```

## 💻 IMPLEMENTACIÓN

### Clase Principal

```python
class IndicatorsCache:
    """
    Sistema de caché inteligente para indicadores técnicos.
    
    Features:
    - Almacenamiento en GCS (permanente)
    - Metadata en Firestore (índices rápidos)
    - Cálculo incremental (solo velas nuevas)
    - Validación por hash (detecta cambios)
    - TTL configurable (4 horas default)
    """
    
    def __init__(self, bucket_name: str = "markettool"):
        self.bucket = storage.Client().bucket(bucket_name)
        self.db = firestore.Client()
    
    def get_or_calculate(self, symbol: str, tf: str, df_historicos: pd.DataFrame) -> dict:
        """
        Obtiene indicadores del caché o los calcula si es necesario.
        Lógica incremental automática.
        """
        pass
    
    def load(self, symbol: str, tf: str) -> Optional[dict]:
        """Carga indicadores cacheados desde GCS."""
        pass
    
    def save(self, symbol: str, tf: str, indicators: dict, df_historicos: pd.DataFrame):
        """Guarda indicadores en GCS + metadata en Firestore."""
        pass
    
    def invalidate(self, symbol: str, tf: str):
        """Invalida caché (forzar recálculo)."""
        pass
```

### Funciones de Hash

```python
def hash_dataframe(df: pd.DataFrame) -> str:
    """
    Genera hash SHA256 de un DataFrame para detectar cambios.
    Hash solo de: index + close prices (suficiente para detectar updates)
    """
    # Ordenar por index para consistencia
    df_sorted = df.sort_index()
    
    # Serializar: timestamps + close prices
    data_str = f"{df_sorted.index.tolist()}_{df_sorted['close'].tolist()}"
    
    return hashlib.sha256(data_str.encode()).hexdigest()[:16]  # 16 chars suficientes
```

### Merge Incremental

```python
def merge_indicators(cached: dict, new: dict, split_index: int) -> dict:
    """
    Combina indicadores cacheados + nuevos calculados.
    
    Args:
        cached: Indicadores antiguos (completos)
        new: Indicadores recién calculados (solo últimas velas con context)
        split_index: Índice donde empiezan los datos nuevos
    
    Returns:
        Indicadores combinados (cached[:split_index] + new[split_index:])
    """
    merged = {}
    
    for key in new.keys():
        if key in cached:
            # Mantener cache antiguo hasta split_index
            old_part = cached[key][:split_index]
            
            # Tomar nuevos valores desde split_index
            new_part = new[key][-(len(new[key]) - split_index):]
            
            # Combinar
            if isinstance(old_part, list):
                merged[key] = old_part + new_part
            else:
                # Para valores únicos, usar el nuevo
                merged[key] = new[key]
        else:
            # Indicador nuevo que no existía en cache
            merged[key] = new[key]
    
    return merged
```

## 📊 INTEGRACIÓN CON CÓDIGO EXISTENTE

### Modificar `calcular_indicadores()`

**Antes:**
```python
def calcular_indicadores(df, temporalidad):
    # ... cálculos pesados ...
    return df
```

**Después:**
```python
INDICATORS_CACHE = IndicatorsCache()

def calcular_indicadores(df, temporalidad, symbol=None):
    """
    Calcula indicadores con caché inteligente.
    Si symbol es None, calcula sin caché (compatibilidad).
    """
    if symbol is None:
        # Modo legacy: sin caché
        return calcular_indicadores_impl(df, temporalidad)
    
    # Usar caché
    indicators = INDICATORS_CACHE.get_or_calculate(symbol, temporalidad, df)
    
    # Aplicar indicadores al DataFrame (como antes)
    for col, values in indicators.items():
        if col not in ['soportes_dinamicos', 'resistencias_dinamicas']:
            df[col] = values
    
    return df
```

### Punto de Entrada Principal

Si calculas 50 activos en paralelo, ejemplo:

```python
def analizar_mercado_completo(activos, temporalidades):
    """
    Analiza múltiples activos con caché de indicadores.
    Reduce de 30 min a ~2-3 min.
    """
    
    resultados = []
    
    for activo in activos:
        for tf in temporalidades:
            # 1. Cargar históricos (ya optimizado)
            df = load_cached_history(activo, tf)
            
            # 2. Calcular indicadores CON CACHÉ
            df = calcular_indicadores(df, tf, symbol=activo)
            
            # 3. Resto del análisis (ponderaciones, etc.)
            resultado = procesar_analisis(df, activo, tf)
            resultados.append(resultado)
    
    return resultados
```

## 🎯 BENEFICIOS ESPERADOS

### Performance

| Escenario | Antes | Después | Mejora |
|-----------|-------|---------|--------|
| **Primera ejecución** | 30 min | 30 min | 0% (cold start) |
| **Ejecución subsecuente** | 30 min | **2-3 min** | **90% ↓** |
| **Update incremental** | 30 min | **30-60 seg** | **95% ↓** |

### Casos de Uso

1. **Bot ejecutándose cada hora:**
   - Primera vez: 30 min (caché vacío)
   - Subsecuentes: 1-2 min (solo recalcula últimas velas)

2. **Análisis manual:**
   - Primera vez: 30 min
   - Segunda vez (mismo día): 30 seg (caché caliente)

3. **Multi-pod deployment:**
   - Pod 1 calcula y guarda en GCS
   - Pod 2 reutiliza sin recalcular
   - Coordinación via Firestore metadata

## 🔧 CONFIGURACIÓN

### Variables de Entorno

```bash
# Habilitar caché de indicadores
INDICATORS_CACHE_ENABLED=true

# TTL del caché (horas)
INDICATORS_CACHE_TTL_HOURS=4

# Bucket GCS
GCS_BUCKET_NAME=markettool

# Forzar recálculo (debug)
INDICATORS_FORCE_RECALC=false
```

## 🚀 ROLLOUT

### Fase 1: Implementación (2-3 horas)
- [x] Crear clase `IndicatorsCache`
- [x] Implementar funciones de hash y merge
- [ ] Integrar con `calcular_indicadores()`
- [ ] Testing con 1 activo

### Fase 2: Validación (1 día)
- [ ] Probar con 10 activos
- [ ] Verificar consistencia de resultados
- [ ] Medir mejoras de performance
- [ ] Ajustar TTL y configuración

### Fase 3: Producción (1 semana)
- [ ] Deploy con todos los activos
- [ ] Monitorear uso de GCS
- [ ] Logs de hit/miss rate
- [ ] Optimizar estrategia de invalidación

## 🎯 MÉTRICAS DE ÉXITO

```python
# Logging automático en cada ejecución

{
  "cache_stats": {
    "total_symbols": 50,
    "cache_hits": 48,      # 96% hit rate
    "cache_misses": 2,     # 4% miss rate
    "incremental": 45,     # 90% solo recalculó velas nuevas
    "full_calc": 5,        # 10% cálculo completo
    "time_saved_sec": 1680, # 28 min ahorrados
    "gcs_reads": 50,
    "gcs_writes": 5
  }
}
```

## 🔍 DEBUGGING

### Forzar Recálculo

```python
# Invalida caché para un activo
INDICATORS_CACHE.invalidate("EURUSD", "1day")

# O desde terminal
curl -X POST http://localhost:5000/api/cache/invalidate \
  -d '{"symbol": "EURUSD", "timeframe": "1day"}'
```

### Ver Estado del Caché

```python
# Metadata desde Firestore
metadata = INDICATORS_CACHE.get_metadata("EURUSD", "1day")
print(metadata)

# O desde gcloud CLI
gsutil ls gs://markettool/indicators/
gsutil cat gs://markettool/indicators/EURUSD__1day.json | jq '.metadata'
```

---

**Status:** 📝 Design Complete - Ready for Implementation  
**Estimated Time Savings:** 90-95% en ejecuciones subsecuentes  
**Storage Cost:** ~$2/mes adicional en GCS  
**ROI:** Invaluable (reduce latencia de 30min → 2min)
