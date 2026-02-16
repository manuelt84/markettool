# 📊 ARQUITECTURA MEJORADA: Históricos Permanentes con GCS

**Objetivo:** Reducir costos de FMP usando almacenamiento permanente en Google Cloud Storage

---

## 🔴 PROBLEMA ACTUAL

```
┌─────────────┐
│   Local FS  │  ← Temporal, se pierde entre deployments
│ historicos/ │    No escalable, no compartido entre instancias
└─────────────┘
      │
      │ cada consulta fallida → vuelve a FMP
      ↓
┌─────────────┐
│     FMP     │  ← Caro: payload consumido, transacciones limitadas
│  API Calls  │    Lento: 5-10s por request histórico
└─────────────┘
```

**Costos FMP típicos:**
- Cada query histórica = 1 transacción
- 100 activos × 5 temporalidades × 30 días = **15,000 transacciones/mes**
- A $0.01 por transacción = **$150/mes solo en históricos**

---

## ✅ SOLUCIÓN PROPUESTA

```
┌──────────────────┐
│  Local Caché     │  ← LazyHistoricosLoader (100 symbols, TTL 30min)
│  (LRU + TTL)     │    Fast: <10ms, Cheap: en memoria
└──────────────────┘
         ↑↓
         │ miss: carga de
         │ GCS si existe
         │
         ↓
┌──────────────────┐     ┌──────────────┐
│  GCS Storage     │────→│  Firestore   │
│  (Permanente)    │     │  Metadata    │
│  historicos/     │     │  Índices     │
└──────────────────┘     └──────────────┘
         ↑
         │ si data vieja
         │ o no existe
         │
         ↓
┌──────────────────┐
│  FMP API         │  ← Solo si antes el 30+ días OR nuevo
│  (último recurso)│    Reduce de 15k a ~500 calls/mes
└──────────────────┘
```

**Ahorro esperado:**
- Transacciones FMP: 15,000 → ~500/mes (**97% reduction**)
- Costo: $150 → $5/mes (**30x más barato**) 💰

---

## 🏗️ ARQUITECTURA DETALLADA

### Capas

```
┌─────────────────────────────────────┐
│  1. LazyHistoricosLoader (Memory)   │  ← Actual + TTL
│     Max 100 symbols, TTL 30min      │     Latency: <10ms
└─────────────────────────────────────┘
            ↓ (miss)
┌─────────────────────────────────────┐
│  2. GCS Storage Layer               │  ← NEW
│     gs://markettool/historicos/     │     Latency: 100-500ms
│     EURUSD_1day.json                │     Cost: ~$0.26/GB/mo
├─────────────────────────────────────┤
│  3. Firestore Metadata              │  ← NEW
│     - Last update timestamp         │     Latency: 50-100ms
│     - Data availability index       │     Cost: $0.06 per 100k reads
│     - Symbol-TF mapping             │
├─────────────────────────────────────┤
│  4. FMP API Fallback                │  ← REDUCED USAGE
│     Only for: new symbols, stale    │     Latency: 5-10s
│     data (>30 days old)             │     Cost: $5/mo (vs $150)
└─────────────────────────────────────┘
```

---

## 📋 IMPLEMENTACIÓN

### 1. GCS Storage Layer

**Path Structure:**
```
gs://markettool/
├── historicos/
│   └── live/
│       ├── EURUSD_1day.json      (actualizado cada día)
│       ├── EURUSD_4hour.json     (actualizado cada 4h)
│       ├── BTCUSD_1day.json
│       └── ...
│   └── archive/
│       └── EURUSD_1day_2026-02.json  (archivos por mes)
└── metadata/
    └── schema.json
```

**Ventajas GCS:**
- ✅ Cheap: $0.26/GB/mes (vs $0.006/GB local disk)
- ✅ Scalable: unlimited storage
- ✅ Shared: accesible desde múltiples instancias
- ✅ Versionado: audit trail automático
- ✅ CDN: integrable para servir archivos

---

### 2. Firestore Metadata

**Collection: `historicos_metadata`**
```json
{
  "doc_id": "EURUSD_1day",
  "symbol": "EURUSD",
  "timeframe": "1day",
  "gcs_path": "gs://markettool/historicos/live/EURUSD_1day.json",
  "last_update_utc": "2026-02-10T19:45:00Z",
  "rows_available": 365,
  "data_range": {
    "start": "2025-02-10T00:00:00Z",
    "end": "2026-02-10T23:59:59Z"
  },
  "ttl_days": 30,  # Archiva después de 30 días
  "is_stale": false,
  "update_frequency": "1day"
}
```

**Índices requeridos:**
```
Field: symbol (ASC) + update_frequency (ASC)
Field: last_update_utc (DESC)  # Para cleanup
Field: is_stale (ASC)  # Para encontrar qué actualizar
```

---

### 3. Update Strategy

**Smart Update Logic:**
```python
def get_historical_data(symbol, tf):
    # 1. Check local cache (LazyHistoricosLoader)
    cached = lazy_loader.get(symbol, tf)
    if cached and not is_stale(cached):
        return cached  # <10ms
    
    # 2. Check Firestore metadata
    metadata = firestore.get_metadata(symbol, tf)
    if metadata and not is_stale(metadata):
        # 3. Load from GCS
        df = gcs.load_json(metadata.gcs_path)
        cache.put(df)
        return df  # ~300ms, 0 cost
    
    # 4. Fallback to FMP (last resort)
    df = fmp.get_historical(symbol, tf)  # 5-10s, $$$$
    
    # 5. Save to both
    gcs.save_json(df)
    firestore.update_metadata(symbol, tf, GCS_path)
    cache.put(df)
    
    return df
```

---

### 4. Cleanup & Archival

**Automated Archival Process:**
```
Every Sunday 02:00 UTC:
  1. Find all historicos_metadata where TTL expired
  2. For each:
     a. Move gs://historicos/live/ → gs://historicos/archive/
     b. Compress with gzip
     c. Update metadata: archived=true
     d) Schedule deletion after 90 days
```

---

## 💻 CODIGO IMPLEMENTACION

### New Class: `GCSHistoricosBackend`

```python
class GCSHistoricosBackend:
    """
    Storage layer para historicos usando Google Cloud Storage.
    - Almacenamiento permanente y compartido
    - Metadata en Firestore para indexación rápida
    - Archival automático después de 30 días
    """
    
    def __init__(self, bucket_name: str = "markettool"):
        self.bucket = storage.bucket(bucket_name)
        self.db = db  # Firestore client
    
    def load(self, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        """Carga históricos desde GCS si están disponibles."""
        try:
            metadata = self._get_metadata(symbol, tf)
            if not metadata or self._is_stale(metadata):
                return None  # Necesita refresh desde FMP
            
            gcs_path = metadata.get("gcs_path")
            blob = self.bucket.blob(gcs_path)
            
            data = json.loads(blob.download_as_text())
            df = pd.DataFrame(data)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df.set_index("time").sort_index()
        except Exception as e:
            logger.debug(f"[GCS] Error loading {symbol}/{tf}: {e}")
            return None
    
    def save(self, symbol: str, tf: str, df: pd.DataFrame) -> str:
        """Guarda históricos en GCS con metadata en Firestore."""
        try:
            # Preparar datos
            payload = df.tail(1000).to_dict(orient="records")
            
            # Guardar en GCS
            gcs_path = f"historicos/live/{symbol}_{tf}.json"
            blob = self.bucket.blob(gcs_path)
            blob.upload_from_string(
                json.dumps(payload),
                content_type="application/json"
            )
            
            # Actualizar metadata en Firestore
            metadata_doc = f"{symbol}_{tf}"
            self.db.collection("historicos_metadata").document(metadata_doc).set({
                "symbol": symbol,
                "timeframe": tf,
                "gcs_path": gcs_path,
                "last_update_utc": datetime.utcnow(),
                "rows_available": len(df),
                "data_range": {
                    "start": df.index.min().isoformat(),
                    "end": df.index.max().isoformat()
                },
                "ttl_days": 30,
                "is_stale": False,
                "update_frequency": self._get_frequency(tf)
            }, merge=True)
            
            logger.info(f"[GCS] Saved {symbol}/{tf} to {gcs_path}")
            return gcs_path
        
        except Exception as e:
            logger.error(f"[GCS] Error saving {symbol}/{tf}: {e}")
            return None
    
    def _get_metadata(self, symbol: str, tf: str) -> dict:
        """Obtiene metadata desde Firestore."""
        try:
            doc = self.db.collection("historicos_metadata").document(f"{symbol}_{tf}").get()
            return doc.to_dict() if doc.exists else None
        except Exception:
            return None
    
    def _is_stale(self, metadata: dict) -> bool:
        """Verifica si los datos necesitan refresh."""
        if not metadata:
            return True
        
        last_update = pd.to_datetime(metadata.get("last_update_utc"))
        ttl_days = metadata.get("ttl_days", 30)
        
        return (datetime.utcnow() - last_update).days > ttl_days
    
    def _get_frequency(self, tf: str) -> str:
        """Determina frecuencia de actualización según timeframe."""
        return {
            "1min": "5min",
            "5min": "15min",
            "15min": "1hour",
            "30min": "4hour",
            "1hour": "1day",
            "4hour": "1day",
            "1day": "1week",
            "1week": "1month",
        }.get(tf, "1week")
```

---

## 🔄 INTEGRACIÓN CON CÓDIGO EXISTENTE

### Updated: `load_cached_history()`

```python
def load_cached_history(symbol: str, tf: str) -> pd.DataFrame:
    """
    Intenta cargar de: Local Cache → GCS → Fallback
    """
    # 1. Local cache (mismo como antes)
    try:
        df = _LAZY_HIST_LOADER.get(symbol, tf)
        if not df.empty:
            return df
    except Exception:
        pass
    
    # 2. GCS (NEW)
    try:
        df = _GCS_BACKEND.load(symbol, tf)
        if df is not None and not df.empty:
            _LAZY_HIST_LOADER.put(symbol, tf, df)  # Cache locally
            return df
    except Exception as e:
        logger.debug(f"[GCS] Load failed, will use FMP: {e}")
    
    # 3. Local files (backwards compat)
    primary = _hist_path(symbol, tf)
    # ... resto del código existente ...
```

### Updated: `save_cached_history()`

```python
def save_cached_history(symbol: str, tf: str, out: pd.DataFrame, **kwargs):
    """
    Guarda en: GCS (principal) + Local (backup)
    """
    if out is None or out.empty:
        return
    
    # 1. Guardar en GCS (permanente)
    try:
        gcs_path = _GCS_BACKEND.save(symbol, tf, out)
        logger.info(f"[History] Saved to GCS: {gcs_path}")
    except Exception as e:
        logger.warning(f"[History] GCS save failed: {e}")
    
    # 2. Guardar local como backup (código existente)
    try:
        # ... código existente ...
    except Exception as e:
        logger.warning(f"[History] Local save failed: {e}")
```

---

## 📊 COMPARATIVA: ANTES vs DESPUÉS

### Costo Mensual

| Operación | Antes | Después | Ahorro |
|-----------|-------|---------|--------|
| FMP Transacciones | 15,000 calls | 500 calls | **97%** ↓ |
| FMP Cost | $150 | $5 | **30x** cheaper |
| GCS Storage | $0 | $2 | - |
| GCS Egress | $0 | $3 | - |
| Firestore Reads | ~$0 | $4 | - |
| **TOTAL** | **$150** | **$14** | **91% SAVINGS** 💰 |

### Performance

| Scenario | Antes | Después | Mejora |
|----------|-------|---------|--------|
| Hit caché local | 10ms | 10ms | ✓ |
| Hit GCS | 5000ms (FMP) | 300ms | **16x** ⚡ |
| Miss FMP | 10000ms | 10000ms | (igual) |

---

## 🚀 ROLLOUT PLAN

### Phase 1: Setup (1 day)
- [ ] Create GCS bucket `markettool-historicos`
- [ ] Create Firestore index
- [ ] Implement GCSHistoricosBackend class
- [ ] Update load/save functions

### Phase 2: Migration (1 week)
- [ ] Dual-write to GCS + local
- [ ] Migrate existing historicos/ to GCS
- [ ] Test data integrity
- [ ] Monitor costs

### Phase 3: Cutover (1 week)
- [ ] Make GCS primary
- [ ] Remove local writes
- [ ] Deprecate historicos/ folder
- [ ] Archive old data

### Phase 4: Optimization (ongoing)
- [ ] Implement cleanup jobs
- [ ] Monitor access patterns
- [ ] Optimize compression
- [ ] Setup alerting

---

## 📈 EXPECTED OUTCOMES

✅ Reduce FMP costs from $150 to $14/mo (91% savings)  
✅ Reduce FMP transacciones from 15k to 500/mo (97% reduction)  
✅ Share historicos across multiple instances  
✅ Automatic backup & archival  
✅ Audit trail & versioning  
✅ Faster data access (300ms vs 10s)  
✅ Zero data loss  

---

**Estimated Implementation Time:** 6-8 hours  
**Complexity:** Medium (GCS API, Firestore indexing)  
**Risk:** Low (backwards compatible, can revert)  
**ROI:** $1,000+/year savings
