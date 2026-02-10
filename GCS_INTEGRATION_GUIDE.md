# 📦 GCS Integration for Permanent Historical Storage

## Overview

MarketTool now supports **permanent historical data storage** using Google Cloud Storage (GCS) with **multi-pod coordination** via Firestore metadata. This reduces FMP API costs by ~97% by caching downloaded data indefinitely and prevents duplicate API calls in multi-pod deployments.

---

## Architecture

### Single Pod
```
App
  ↓
load_cached_history()
  ├─ Try 1: LazyHistoricosLoader (Memory, <10ms) ✅ HIT
  │         (100 symbols max, TTL 30min local)
  │
  ├─ Try 2: GCS (Permanent, 300-500ms) ✅ HIT
  │         (Unlimited symbols, permanent)
  │         └─ Cache locally after load
  │
  ├─ Try 3: Local files (Backup, <100ms)
  │         CSV/JSON in historicos/ folder
  │
  └─ Try 4: FMP API (Expensive, 5-10s) ❌ MISS
            (Only if not found in other sources)
```

### Multi-Pod (NEW: With Firestore Metadata Coordination)
```
Pod 1 ──┐
Pod 2 ──┼──> Firestore Metadata (shared TTL) ──> GCS Data
Pod 3 ──┘

Workflow:
1. Pod A requests EURUSD/1day
   ├─ Memory cache miss
   ├─ Firestore metadata: "not found, fetch from FMP"
   ├─ Pod A fetches from FMP
   ├─ Saves to GCS + Firestore metadata (TTL=30min, update_time=now)
   
2. Pod B requests same EURUSD/1day (100ms after Pod A)
   ├─ Memory cache miss
   ├─ Firestore metadata: "valid, TTL expires in 29.8min"
   ├─ Pod B loads from GCS (300ms)
   └─ NO FMP CALL! 💰 Saved $0.01
   
3. Pod C requests same EURUSD/1day (31min after Pod A)
   ├─ Memory cache miss
   ├─ Firestore metadata: "STALE, TTL expired"
   ├─ Pod C fetches from FMP
   ├─ Saves to GCS + Firestore metadata (TTL reset to 30min)
```

**Result:** In multi-pod, Firestore metadata acts as a **shared TTL coordinator**
- Only first pod per symbol makes FMP call
- Other pods see "fresh" flag in Firestore → load from GCS
- **97% FMP reduction + no duplicate calls**

---

## Setup

### 1. Enable GCS (Environment Variables)

```bash
# Enable/disable GCS (default: true)
export GCS_ENABLED=true

# GCS bucket name (default: markettool)
export GCS_BUCKET_NAME=markettool

# Google Cloud credentials (automatically detected)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json

# Enable/disable Firestore metadata layer (default: true)
export FIRESTORE_ENABLED=true

# (Optional) Pod name for audit trail
export POD_NAME=pod-1
```

### 2. Create GCS Bucket

```bash
gsutil mb -l us-central1 gs://markettool-historicos
```

### 3. Create Firestore Collection & Indexes

**Collection:** `historicos_metadata`

**Documents:** One per symbol/timeframe combination
```
historicos_metadata/
  EURUSD_1day
  EURUSD_4hour
  BTCUSD_1day
  ...
```

**Automatic index** (optional but recommended for scale):
```bash
gcloud firestore indexes create --collection='historicos_metadata' \
  --field-config field-path='last_update_utc',order='DESCENDING' \
  --field-config field-path='ttl_seconds',order='ASCENDING'
```

### 4. Configure Bucket (Optional but Recommended)

**Lifecycle rules** to auto-delete old backups:
```bash
cat > lifecycle.json << EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 90}  # Delete after 90 days
      }
    ]
  }
}
EOF

gsutil lifecycle set lifecycle.json gs://markettool-historicos
```

---

## API Reference

### load_from_gcs(symbol: str, tf: str) → Optional[pd.DataFrame]

**Load historical data from GCS.**

```python
from MarketTool import load_from_gcs

# Load EURUSD daily data from GCS
df = load_from_gcs("EURUSD", "1day")
if df is not None:
    print(f"Loaded {len(df)} rows from GCS")
else:
    print("Data not in GCS, will need to fetch from FMP")
```

**Returns:**
- `pd.DataFrame` if file exists in GCS with columns: `[open, high, low, close, volume]`
- `None` if file not found or GCS disabled

**File location (in GCS):**
```
gs://markettool-historicos/historicos/{SYMBOL}__{TIMEFRAME}.json
```

Example: `gs://markettool-historicos/historicos/EURUSD__1day.json`

---

### save_to_gcs(symbol: str, tf: str, df: pd.DataFrame) → bool

**Save historical data to GCS (permanently).**

```python
from MarketTool import save_to_gcs

# Normally called automatically by save_cached_history()
# But can be called manually:
result = save_to_gcs("EURUSD", "1day", df)
print(f"Saved to GCS: {result}")
```

**Parameters:**
- `symbol`: Trading symbol (e.g., "EURUSD", "BTCUSD")
- `tf`: Timeframe (e.g., "1day", "4hour", "1min")
- `df`: DataFrame with OHLCV data (must have `open`, `high`, `low`, `close`, `volume` columns)

**Returns:**
- `True` if upload succeeded
- `False` if upload failed or GCS disabled

**Note:** Automatically called by `save_cached_history()`, no need to call manually.

---

### get_historicos_metadata(symbol: str, tf: str) → Optional[Dict]

**Get shared metadata from Firestore (multi-pod coordination).**

```python
from MarketTool import get_historicos_metadata

# Check if data is fresh across all pods
metadata = get_historicos_metadata("EURUSD", "1day")
if metadata:
    print(f"Last update: {metadata['last_update_utc']}")
    print(f"TTL seconds: {metadata['ttl_seconds']}")
    print(f"Is stale: {metadata.get('is_stale', False)}")
else:
    print("No metadata found (new symbol)")
```

**Returns:**
- `Dict` with metadata if found:
  ```python
  {
      "symbol": "EURUSD",
      "timeframe": "1day",
      "gcs_path": "gs://markettool/historicos/EURUSD__1day.json",
      "last_update_utc": Timestamp(2024-01-10T15:30:00Z),
      "rows_available": 365,
      "ttl_seconds": 1800,
      "is_stale": False,
      "updated_by_pod": "pod-1"
  }
  ```
- `None` if not found or Firestore disabled

---

### set_historicos_metadata(symbol: str, tf: str, gcs_path: str, rows_count: int, ttl_seconds: int = 1800) → bool

**Save shared metadata to Firestore (multi-pod coordination).**

```python
from MarketTool import set_historicos_metadata

# Normally called automatically by save_cached_history()
# But can be called manually:
success = set_historicos_metadata(
    "EURUSD", "1day",
    "gs://markettool/historicos/EURUSD__1day.json",
    rows_count=365,
    ttl_seconds=1800  # 30 minutes
)
print(f"Metadata saved: {success}")
```

**Parameters:**
- `symbol`: Trading symbol
- `tf`: Timeframe
- `gcs_path`: Path in GCS where data is stored
- `rows_count`: Number of rows available
- `ttl_seconds`: TTL in seconds (shared across all pods)

**Returns:**
- `True` if metadata saved successfully
- `False` if save failed or Firestore disabled

**Note:** Automatically called by `save_cached_history()` after GCS upload.

---

### is_metadata_stale(metadata: Dict) → bool

**Check if metadata TTL has expired (multi-pod safe).**

```python
from MarketTool import get_historicos_metadata, is_metadata_stale

metadata = get_historicos_metadata("EURUSD", "1day")
if metadata and not is_metadata_stale(metadata):
    print("Data is still fresh, can load from GCS")
else:
    print("Data is stale, need to fetch from FMP")
```

**Parameters:**
- `metadata`: Dict returned by `get_historicos_metadata()`

**Returns:**
- `True` if TTL expired
- `False` if TTL still valid

---

### Data Flow

### Reading Historical Data (Multi-Pod Aware)

```python
# When you call obtener_datos_historicos() or similar:

df = load_cached_history("EURUSD", "1day")  # ← Called internally

# Search order (multi-pod coordinated):
# 1. LazyHistoricosLoader cache     (in-memory LRU, 30 min TTL, pod-local)
# 2. Firestore metadata             (shared TTL, prevents dup FMP calls) ← NEW
#    ├─ If TTL valid: Skip FMP, load from GCS
#    └─ If TTL expired: Allow FMP fetch on this pod
# 3. GCS storage                    (permanent cloud storage)
# 4. Local files                    (backup/legacy)
# 5. FMP API                        (expensive, last resort)
```

**Multi-pod example:**
```
Time 0:00 - Pod A requests EURUSD/1day
├─ LazyLoader miss
├─ Firestore metadata: not found
├─ GCS miss
├─ Local miss
├─ FMP: fetch (5s, $0.01)
├─ Save to GCS + Firestore metadata (ttl=30min)
└─ Return to Pod A

Time 0:10 - Pod B requests same EURUSD/1day
├─ LazyLoader miss
├─ Firestore metadata: FOUND! (9min remaining until TTL expires)
├─ GCS: load saved data (300ms)
├─ Cache locally
└─ Return to Pod B
   NO FMP CALL! Saved $0.01 and 5s latency

Time 0:35 - Pod C requests same EURUSD/1day
├─ LazyLoader miss
├─ Firestore metadata: STALE (TTL expired 5min ago)
├─ FMP: fetch (5s, $0.01)
├─ Update Firestore metadata (reset TTL to 30min)
└─ Return to Pod C
```

### Writing Historical Data (Multi-Pod Aware)

```python
# When new data is fetched:

save_cached_history("EURUSD", "1day", df)  # ← Called internally

# Saves to (in order):
# 1. GCS storage              (permanent) ✅ PRIMARY
# 2. Firestore metadata       (shared TTL) ✅ NEW - tells other pods "fresh"
# 3. Local temp file          (backup)    ✅ SECONDARY
```

**Multi-pod synchronization:**
- Pod A fetches from FMP, saves to GCS + Firestore
- Pod B reads Firestore: "EURUSD/1day is fresh, don't call FMP"
- Pod B loads from GCS (300ms), not FMP (5000ms)
- **Result:** 16x faster + zero duplicate API calls

---

## Cost Comparison

### Before GCS
```
Scenario: 100 symbols × 5 timeframes = 500 data series

Monthly FMP transactions:      15,000 calls
Cost per transaction:          $0.01
Total FMP cost:                $150/month

Storage:                       $0 (temporary files)
TOTAL:                         $150/month
```

### After GCS
```
Scenario: Same 100 symbols × 5 timeframes

Monthly FMP transactions:      ~500 calls (97% reduction!)
Cost per transaction:          $0.01
FMP cost:                      $5/month

GCS storage (100 symbols):     $2/month
GCS egress:                    $3/month
TOTAL:                         $10/month

SAVINGS:                       $140/month (93% reduction!) 💰
```

---

## Monitoring

### Check GCS Files

```bash
# List all historical files in GCS
gsutil ls -r gs://markettool-historicos/historicos/

# Check specific symbol
gsutil ls gs://markettool-historicos/historicos/EURUSD*

# Download a file to verify
gsutil cp gs://markettool-historicos/historicos/EURUSD__1day.json ./
```

### Check Logs

```python
import logging

# Enable debug logging to see GCS operations
logging.getLogger().setLevel(logging.DEBUG)

# When loading data:
# [GCS] Loaded EURUSD/1day from gs://markettool-historicos/historicos/EURUSD__1day.json (365 rows)

# When saving data:
# [GCS] Saved EURUSD/1day to gs://markettool-historicos/historicos/EURUSD__1day.json (365 rows)
```

---

## Troubleshooting

### GCS Not Connecting

**Problem:** Logs show "GCS disabled" or "Client initialization failed"

**Solutions:**
1. Verify credentials:
   ```bash
   gcloud auth application-default print-access-token
   ```

2. Check environment:
   ```bash
   echo $GCS_ENABLED
   echo $GCS_BUCKET_NAME
   echo $GOOGLE_APPLICATION_CREDENTIALS
   ```

3. Verify bucket permissions:
   ```bash
   gsutil iam ch serviceAccount:YOUR_SA@project.iam.gserviceaccount.com:roles/storage.objectAdmin gs://markettool
   ```

### Upload Fails

**Problem:** "Failed to save {symbol}/{tf}: ..."

**Solutions:**
1. Check bucket exists: `gsutil ls gs://markettool`
2. Check permissions: User/service account needs `storage.objects.create`
3. Check disk space: Last 1000 rows must be serializeable to JSON
4. Check network: GCS upload requires internet connection

### Download Fails

**Problem:** "GCS Load failed for {symbol}/{tf}: ..."

**Solutions:**
1. Verify file exists in GCS
2. Check blob name format: `historicos/{SYMBOL}__{TIMEFRAME}.json`
3. Verify bucket readable (may need IAM permissions)
4. This is not fatal: will fall back to local files or FMP

---

## Migration from Local Files to GCS

### Automatic Migration

The first time `save_cached_history()` is called with GCS enabled, data is saved to both:
- **GCS** (new permanent storage)
- **Local temp file** (legacy backup)

### Manual Migration

To manually migrate existing local files to GCS:

```python
import os
import glob
from MarketTool import save_to_gcs, normalize_tf, _safe_symbol_for_filename

# Migrate all local files to GCS
hist_dir = "historicos"
for filepath in glob.glob(f"{hist_dir}/**/*.json", recursive=True):
    # Parse symbol/timeframe from filename
    filename = os.path.basename(filepath)
    # Your parsing logic...
    
    # Load and save to GCS
    df = pd.read_json(filepath)
    save_to_gcs(symbol, tf, df)
    print(f"✅ Migrated {symbol}/{tf}")
```

---

## Performance

### Latency Comparison

| Operation | Latency | Notes |
|-----------|---------|-------|
| Memory cache hit | <10ms | LazyHistoricosLoader |
| GCS load | 300-500ms | Network + deserialization |
| Local file load | <100ms | Disk I/O |
| FMP API call | 5-10s | Network + API server |

### Throughput

With enabled GCS caching:
- **First request per symbol:** ~10s (full FMP download)
- **Subsequent local requests:** <10ms (memory cache)
- **Requests after TTL expiry:** 300-500ms (GCS load instead of re-downloading from FMP)
- **After 30 min (TTL):** Reload from GCS, not FMP

---

## Configuration

### AppConfig

GCS behavior is controlled by AppConfig class:

```python
# In MarketTool.py
class AppConfig:
    cache_ttl_historicos: int = 1800  # 30 minutes (LazyHistoricosLoader TTL)
    cache_max_size_historicos: int = 100  # Max symbols in memory
    # GCS config (via environment variables):
    # - GCS_ENABLED (default: true)
    # - GCS_BUCKET_NAME (default: markettool)
```

To disable GCS globally:
```bash
export GCS_ENABLED=false
```

---

## Advanced

### Custom GCS Paths

To use different directory structure in GCS, modify the path in functions:

```python
# Current: gs://markettool/historicos/{SYMBOL}__{TF}.json
# Custom: gs://markettool/data/v2/{SYMBOL}/{TF}.json

# Edit load_from_gcs() and save_to_gcs() functions
gcs_path = f"data/v2/{safe_sym}/{safe_tf}.json"
```

### Compression

To enable gzip compression (save ~70% storage):

```python
import gzip

# In save_to_gcs():
gcs_path = f"historicos/{symbol}_{tf}.json.gz"
blob.upload_from_string(
    gzip.compress(json_data.encode('utf-8')),
    content_type="application/gzip"
)

# In load_from_gcs():
json_data = gzip.decompress(blob.download_as_bytes()).decode('utf-8')
```

---

## Support

For issues or questions:
1. Check logs: Look for `[GCS]` prefix messages
2. Verify GCS setup: Run `gsutil ls gs://markettool-historicos`
3. Check credentials: Run `gcloud auth application-default print-access-token`
4. Review environment: Verify `GCS_ENABLED` and `GCS_BUCKET_NAME` variables
