"""Indicators cache system (multi-pod optimized)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import socket
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

import pandas as pd
from google.cloud import firestore
from google.cloud import storage
from requests.adapters import HTTPAdapter

from markettool.infra.fmp import normalize_tf
from markettool.core.cache_config import CACHE_CONFIG


logger = logging.getLogger("MarketTool")

_INDICATORS_CACHE_ENABLED = os.environ.get("INDICATORS_CACHE_ENABLED", "true").lower() == "true"
_INDICATORS_CACHE_TTL_HOURS = int(os.environ.get("INDICATORS_CACHE_TTL_HOURS", "8"))
_INDICATORS_FORCE_RECALC = os.environ.get("INDICATORS_FORCE_RECALC", "false").lower() == "true"
_INDICATORS_MEMORY_CACHE_SIZE = int(os.environ.get("INDICATORS_MEMORY_CACHE_SIZE", "10"))
_INDICATORS_LOCK_TIMEOUT_SEC = int(os.environ.get("INDICATORS_LOCK_TIMEOUT_SEC", "180"))

_VPS_BACKEND_ENABLED = os.environ.get("MARKETTOOL_CLOUD_BACKEND", "").strip().lower() == "vps"
_VPS_STORAGE_ROOT = os.environ.get("MARKETTOOL_VPS_STORAGE_ROOT", "/app/storage/markettool-json")
_GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "markettool_bucket")
_GCS_ENABLED = os.environ.get("GCS_ENABLED", "true").lower() == "true" and not _VPS_BACKEND_ENABLED
_GCS_POOL_CONNECTIONS = int(os.environ.get("GCS_POOL_CONNECTIONS", "64"))
_GCS_POOL_MAXSIZE = int(os.environ.get("GCS_POOL_MAXSIZE", "64"))
_FIRESTORE_CLIENT = None
_FIRESTORE_ENABLED = os.environ.get("FIRESTORE_ENABLED", "true").lower() == "true" and not _VPS_BACKEND_ENABLED

UTC = timezone.utc


def _get_firestore_client() -> Optional[firestore.Client]:
    global _FIRESTORE_CLIENT
    if not _FIRESTORE_ENABLED:
        return None

    try:
        if _FIRESTORE_CLIENT is None:
            if hasattr(firestore, "Client"):
                _FIRESTORE_CLIENT = firestore.Client()
            elif hasattr(firestore, "client"):
                _FIRESTORE_CLIENT = firestore.client()
            else:
                raise AttributeError("No Firestore client constructor found")
        return _FIRESTORE_CLIENT
    except Exception as exc:
        logger.warning("[IndicatorsCache] Firestore init failed: %s", exc)
        return None


def hash_dataframe(df: pd.DataFrame) -> str:
    """Genera hash corto del DataFrame para detectar cambios."""
    try:
        df_sorted = df.sort_index()
        timestamps = df_sorted.index.astype(str).tolist()[:100]
        closes = df_sorted["close"].round(6).tolist()[-100:]
        data_str = f"{len(df)}_{timestamps}_{closes}"
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]
    except Exception as exc:
        logger.warning("[IndicatorsCache] Error hashing DataFrame: %s", exc)
        return hashlib.sha256(f"{df.shape}_{time.time()}".encode()).hexdigest()[:16]


def _tune_storage_client(client: storage.Client) -> storage.Client:
    try:
        adapter = HTTPAdapter(
            pool_connections=_GCS_POOL_CONNECTIONS,
            pool_maxsize=_GCS_POOL_MAXSIZE,
            pool_block=False,
        )
        http = getattr(client, "_http", None)
        if http is not None and hasattr(http, "mount"):
            http.mount("https://", adapter)
            http.mount("http://", adapter)
    except Exception as exc:
        logger.debug("[IndicatorsCache] Could not tune GCS HTTP pool: %s", exc)
    return client


def merge_indicators_incremental(cached: dict, new: dict, split_index: int, window_context: int) -> dict:
    """Combina indicadores cacheados + nuevos calculados incrementalmente."""
    merged = {}
    for key in new.keys():
        if key not in cached:
            merged[key] = new[key]
            continue

        cached_val = cached[key]
        new_val = new[key]

        if isinstance(cached_val, list) and isinstance(new_val, list):
            old_part = cached_val[:split_index]
            new_part = new_val[window_context:] if len(new_val) > window_context else new_val
            merged[key] = old_part + new_part
        else:
            merged[key] = new_val

    return merged


class IndicatorsCache:
    """Cache de indicadores con coordinacion multi-pod."""

    def __init__(self, bucket_name: str | None = None, window_func: Callable[[str], int] | None = None):
        self.bucket_name = bucket_name or _GCS_BUCKET_NAME
        self._bucket = None
        self._db = None
        self._lock = threading.Lock()
        self._pod_id = socket.gethostname()
        self._window_func = window_func

        self._memory_cache = OrderedDict()
        self._memory_cache_max = _INDICATORS_MEMORY_CACHE_SIZE
        self._memory_cache_ttl_sec = CACHE_CONFIG['memory_ttl_seconds']  # Use unified config
        self._memory_cache_lock = threading.RLock()  # PROPOSAL 3: Thread-safe lock
        self._local_dir = os.environ.get(
            "INDICATORS_DIR",
            os.path.join(_VPS_STORAGE_ROOT, "indicators") if _VPS_BACKEND_ENABLED else "indicators",
        )

        self._enabled = _INDICATORS_CACHE_ENABLED

        logger.info(
            "[IndicatorsCache] Initialized (pod=%s, enabled=%s, ttl=%sh, mem_lru=%s, local_dir=%s)",
            self._pod_id,
            self._enabled,
            _INDICATORS_CACHE_TTL_HOURS,
            self._memory_cache_max,
            self._local_dir,
        )

    @property
    def bucket(self):
        if not _GCS_ENABLED:
            return None
        if self._bucket is None and self._enabled:
            try:
                self._bucket = _tune_storage_client(storage.Client()).bucket(self.bucket_name)
            except Exception as exc:
                logger.warning("[IndicatorsCache] GCS not available: %s", exc)
        return self._bucket

    @property
    def db(self):
        if self._db is None and self._enabled:
            self._db = _get_firestore_client()
        return self._db

    def _gcs_path(self, symbol: str, tf: str) -> str:
        return f"indicators/{symbol.upper()}__{normalize_tf(tf)}.json"

    def _metadata_doc_id(self, symbol: str, tf: str) -> str:
        return f"{symbol.upper()}__{normalize_tf(tf)}"

    def _local_path(self, symbol: str, tf: str) -> str:
        safe_symbol = str(symbol).upper().replace("/", "_")
        safe_tf = normalize_tf(tf).replace("/", "_")
        return os.path.join(self._local_dir, f"{safe_symbol}__{safe_tf}.json")

    def _load_local(self, symbol: str, tf: str) -> Optional[dict]:
        try:
            local_path = self._local_path(symbol, tf)
            if not os.path.exists(local_path):
                return None

            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "metadata" not in data or "indicators" not in data:
                logger.warning("[IndicatorsCache] Local invalid structure: %s/%s", symbol, tf)
                return None

            metadata = data["metadata"]
            last_update_raw = metadata.get("last_update_utc")
            if not last_update_raw:
                return None

            last_update = datetime.fromisoformat(str(last_update_raw).replace("Z", "+00:00"))
            age_hours = (datetime.now(UTC).replace(tzinfo=timezone.utc) - last_update).total_seconds() / 3600
            if age_hours > _INDICATORS_CACHE_TTL_HOURS:
                logger.info("[IndicatorsCache] Local stale (age=%.1fh): %s/%s", age_hours, symbol, tf)
                return None

            logger.debug("[IndicatorsCache] Local hit: %s/%s (age=%.1fh)", symbol, tf, age_hours)
            return data
        except Exception as exc:
            logger.debug("[IndicatorsCache] Local load error %s/%s: %s", symbol, tf, exc)
            return None

    def _save_local(self, symbol: str, tf: str, payload: dict) -> None:
        try:
            os.makedirs(self._local_dir, exist_ok=True)
            local_path = self._local_path(symbol, tf)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.debug("[IndicatorsCache] Local save error %s/%s: %s", symbol, tf, exc)

    def _memory_get(self, symbol: str, tf: str) -> Optional[dict]:
        cache_key = f"{symbol}_{tf}"
        with self._memory_cache_lock:  # PROPOSAL 3: Thread-safe lock
            if cache_key not in self._memory_cache:
                return None
            
            data, timestamp = self._memory_cache[cache_key]
            age = time.time() - timestamp
            
            # Check both TTL and data freshness
            if age < self._memory_cache_ttl_sec:
                self._memory_cache.move_to_end(cache_key)
                logger.debug("[IndicatorsCache] Memory hit: %s/%s (age=%.0fs)", symbol, tf, age)
                return data
            
            # Expired - remove from cache
            del self._memory_cache[cache_key]
            logger.debug("[IndicatorsCache] Memory expired: %s/%s (age=%.0fs > ttl=%ds)", 
                        symbol, tf, age, self._memory_cache_ttl_sec)
        return None

    def _memory_put(self, symbol: str, tf: str, data: dict) -> None:
        cache_key = f"{symbol}_{tf}"
        with self._memory_cache_lock:  # PROPOSAL 3: Thread-safe lock
            if cache_key in self._memory_cache:
                del self._memory_cache[cache_key]
            
            self._memory_cache[cache_key] = (data, time.time())
            
            # Enforce LRU eviction
            while len(self._memory_cache) > self._memory_cache_max:
                oldest_key = next(iter(self._memory_cache))
                del self._memory_cache[oldest_key]
                logger.debug("[IndicatorsCache] Evicted from memory (LRU): %s", oldest_key)

    def load(self, symbol: str, tf: str) -> Optional[dict]:
        if not self._enabled:
            return None

        mem_data = self._memory_get(symbol, tf)
        if mem_data is not None:
            return mem_data

        local_data = self._load_local(symbol, tf)
        if local_data is not None:
            self._memory_put(symbol, tf, local_data)
            return local_data

        try:
            if self.bucket is None:
                return None

            gcs_path = self._gcs_path(symbol, tf)
            blob = self.bucket.blob(gcs_path)
            if not blob.exists():
                logger.debug("[IndicatorsCache] Miss (not found in GCS): %s/%s", symbol, tf)
                return None

            data = json.loads(blob.download_as_text())
            if "metadata" not in data or "indicators" not in data:
                logger.warning("[IndicatorsCache] Invalid structure: %s/%s", symbol, tf)
                return None

            metadata = data["metadata"]
            last_update = datetime.fromisoformat(metadata["last_update_utc"].replace("Z", "+00:00"))
            age_hours = (datetime.now(UTC).replace(tzinfo=timezone.utc) - last_update).total_seconds() / 3600
            if age_hours > _INDICATORS_CACHE_TTL_HOURS:
                logger.info("[IndicatorsCache] Stale (age=%.1fh): %s/%s", age_hours, symbol, tf)
                return None

            self._memory_put(symbol, tf, data)
            self._save_local(symbol, tf, data)

            logger.info(
                "[IndicatorsCache] GCS hit: %s/%s (age=%.1fh, rows=%s, pod=%s)",
                symbol,
                tf,
                age_hours,
                metadata.get("rows_count"),
                self._pod_id,
            )
            return data

        except Exception as exc:
            logger.debug("[IndicatorsCache] Load error %s/%s: %s", symbol, tf, exc)
            return None

    def save(
        self,
        symbol: str,
        tf: str,
        indicators: dict,
        df_historicos: pd.DataFrame,
        calc_duration_ms: float = 0,
        analysis_audit: dict | None = None,
        last_calc_index: int | None = None,
    ) -> None:
        if not self._enabled:
            return

        try:
            with self._lock:
                now_utc = datetime.now(UTC).replace(tzinfo=timezone.utc)
                data_hash = hash_dataframe(df_historicos)

                audit = analysis_audit if isinstance(analysis_audit, dict) else {}
                payload_audit = {
                    "last_mode": audit.get("last_mode"),
                    "last_bootstrap_at": audit.get("last_bootstrap_at"),
                    "last_incremental_at": audit.get("last_incremental_at"),
                    "last_incremental_bars": audit.get("last_incremental_bars"),
                    "last_data_mismatch_at": audit.get("last_data_mismatch_at"),
                }

                # 📌 NEW: Track exact index where calculation ended (for incremental detection)
                final_calc_index = last_calc_index if last_calc_index is not None else len(df_historicos) - 1
                
                payload = {
                    "metadata": {
                        "symbol": symbol.upper(),
                        "timeframe": normalize_tf(tf),
                        "last_update_utc": now_utc.isoformat(),
                        "data_hash": data_hash,
                        "rows_count": len(df_historicos),
                        "last_calc_index": final_calc_index,
                        "calc_duration_ms": calc_duration_ms,
                        "indicators_list": list(indicators.keys()),
                        "analysis_audit": payload_audit,
                    },
                    "indicators": indicators,
                }

                self._save_local(symbol, tf, payload)

                if self.bucket is None or self.db is None:
                    logger.warning("[IndicatorsCache] GCS/Firestore not available, local cache saved only")
                else:
                    gcs_path = self._gcs_path(symbol, tf)
                    blob = self.bucket.blob(gcs_path)
                    blob.upload_from_string(
                        json.dumps(payload, default=str),
                        content_type="application/json",
                    )

                    doc_id = self._metadata_doc_id(symbol, tf)
                    self.db.collection("indicators_metadata").document(doc_id).set({
                        "symbol": symbol.upper(),
                        "timeframe": normalize_tf(tf),
                        "gcs_path": f"gs://{self.bucket_name}/{gcs_path}",
                        "last_update_utc": now_utc,
                        "data_hash": data_hash,
                        "rows_count": len(df_historicos),
                        "last_calc_index": final_calc_index,
                        "indicators_list": list(indicators.keys()),
                        "calc_duration_ms": calc_duration_ms,
                        "ttl_hours": _INDICATORS_CACHE_TTL_HOURS,
                        "is_valid": True,
                        "analysis_audit": {
                            "last_mode": audit.get("last_mode"),
                            "last_bootstrap_at": (now_utc if audit.get("last_mode") == "bootstrap" else audit.get("last_bootstrap_at")),
                            "last_incremental_at": (now_utc if audit.get("last_mode") == "incremental" else audit.get("last_incremental_at")),
                            "last_incremental_bars": audit.get("last_incremental_bars"),
                            "last_data_mismatch_at": (now_utc if audit.get("last_mode") == "data_mismatch" else audit.get("last_data_mismatch_at")),
                        },
                    }, merge=True)

                self._memory_put(symbol, tf, payload)

                logger.info(
                    "[IndicatorsCache] Saved: %s/%s (%d rows, %.0fms, pod=%s)",
                    symbol,
                    tf,
                    len(df_historicos),
                    calc_duration_ms,
                    self._pod_id,
                )

        except Exception as exc:
            logger.error("[IndicatorsCache] Save error %s/%s: %s", symbol, tf, exc)

    def invalidate(self, symbol: str, tf: str) -> None:
        try:
            cache_key = f"{symbol}_{tf}"
            if cache_key in self._memory_cache:
                del self._memory_cache[cache_key]

            if self.db:
                doc_id = self._metadata_doc_id(symbol, tf)
                self.db.collection("indicators_metadata").document(doc_id).update({
                    "is_valid": False,
                    "invalidated_at": datetime.now(UTC).replace(tzinfo=timezone.utc),
                })

            logger.info("[IndicatorsCache] Invalidated: %s/%s (pod=%s)", symbol, tf, self._pod_id)
        except Exception as exc:
            logger.warning("[IndicatorsCache] Invalidate error %s/%s: %s", symbol, tf, exc)

    def _acquire_lock(self, symbol: str, tf: str, timeout_sec: int | None = None) -> bool:
        if self.db is None:
            return True

        timeout_sec = timeout_sec or _INDICATORS_LOCK_TIMEOUT_SEC

        try:
            doc_id = self._metadata_doc_id(symbol, tf)
            doc_ref = self.db.collection("indicators_metadata").document(doc_id)
            now_utc = datetime.now(UTC).replace(tzinfo=timezone.utc)

            try:
                doc = doc_ref.get(timeout=5)
            except Exception as get_err:
                logger.debug("[IndicatorsCache] Firestore read timeout %s/%s, acquiring lock locally: %s", symbol, tf, get_err)
                return True

            if doc.exists:
                data = doc.to_dict()
                lock_pod = data.get("calculating_by_pod")
                lock_time = data.get("calculating_since")

                if lock_pod and lock_time:
                    if isinstance(lock_time, str):
                        lock_time = datetime.fromisoformat(lock_time.replace("Z", "+00:00"))
                    elif hasattr(lock_time, "timestamp"):
                        lock_time = datetime.fromtimestamp(lock_time.timestamp(), tz=timezone.utc)

                    age_sec = (now_utc - lock_time).total_seconds()

                    if age_sec < timeout_sec:
                        if lock_pod != self._pod_id:
                            logger.info("[IndicatorsCache] Lock held by %s: %s/%s (age=%.0fs)", lock_pod, symbol, tf, age_sec)
                            return False

            try:
                doc_ref.set({
                    "calculating_by_pod": self._pod_id,
                    "calculating_since": now_utc,
                    "lock_acquired_at": now_utc,
                }, merge=True, timeout=5)
            except Exception as set_err:
                logger.debug("[IndicatorsCache] Firestore write timeout %s/%s, proceeding without lock: %s", symbol, tf, set_err)
                return True

            logger.debug("[IndicatorsCache] Lock acquired: %s/%s (pod=%s)", symbol, tf, self._pod_id)
            return True

        except Exception as exc:
            logger.debug("[IndicatorsCache] Lock acquisition error %s/%s: %s", symbol, tf, exc)
            return True

    def _release_lock(self, symbol: str, tf: str) -> None:
        if self.db is None:
            return

        try:
            doc_id = self._metadata_doc_id(symbol, tf)
            doc_ref = self.db.collection("indicators_metadata").document(doc_id)

            try:
                doc = doc_ref.get(timeout=5)
            except Exception as get_err:
                logger.debug("[IndicatorsCache] Firestore read timeout on _release_lock %s/%s: %s", symbol, tf, get_err)
                return

            if doc.exists:
                data = doc.to_dict()
                if data.get("calculating_by_pod") == self._pod_id:
                    try:
                        doc_ref.update({
                            "calculating_by_pod": firestore.DELETE_FIELD,
                            "calculating_since": firestore.DELETE_FIELD,
                            "lock_released_at": datetime.now(UTC).replace(tzinfo=timezone.utc),
                        }, timeout=5)
                    except Exception as update_err:
                        logger.debug("[IndicatorsCache] Firestore write timeout on _release_lock %s/%s: %s", symbol, tf, update_err)
                        return

                    logger.debug("[IndicatorsCache] Lock released: %s/%s (pod=%s)", symbol, tf, self._pod_id)

        except Exception as exc:
            logger.debug("[IndicatorsCache] Lock release error %s/%s: %s", symbol, tf, exc)

    def get_calc_start_index(self, symbol: str, tf: str) -> int:
        """📌 NEW: Get the index from where incremental calc should start.
        
        Returns:
            Index of first row to recalculate (0 = full recalc, N = start from row N)
            Considers last_calc_index from cache metadata
        """
        try:
            cached = self.load(symbol, tf)
            if cached is None:
                return 0  # No cache, start from beginning
            
            metadata = cached.get("metadata", {})
            last_calc_index = metadata.get("last_calc_index", -1)
            rows_count = metadata.get("rows_count", 0)
            
            if last_calc_index < 0:
                return 0  # Invalid metadata, full recalc
            
            if last_calc_index >= rows_count - 1:
                return rows_count  # Already calculated all rows, nothing to do
            
            # Start from next row after last calculated
            # Apply window context for indicator dependencies
            if self._window_func:
                tf_norm = normalize_tf(tf)
                window = self._window_func(tf_norm)
                context_start = max(0, last_calc_index - window)
                return context_start
            else:
                return max(0, last_calc_index - 5)  # Default small context buffer
        except Exception as e:
            logger.debug(f"[IndicatorsCache] Error getting calc start index {symbol}/{tf}: {e}")
            return 0
    
    def _wait_for_lock_release(self, symbol: str, tf: str, max_wait_sec: int = 30) -> bool:
        if self.db is None:
            return False

        logger.info("[IndicatorsCache] Waiting for other pod (max=%ss): %s/%s", max_wait_sec, symbol, tf)

        start_time = time.time()
        check_interval = 1.0
        max_interval = 5.0

        while (time.time() - start_time) < max_wait_sec:
            try:
                doc_id = self._metadata_doc_id(symbol, tf)
                try:
                    doc = self.db.collection("indicators_metadata").document(doc_id).get(timeout=5)
                except Exception as fire_err:
                    logger.debug("[IndicatorsCache] Firestore timeout during wait %s/%s: %s", symbol, tf, fire_err)
                    break

                if doc.exists:
                    data = doc.to_dict()
                    lock_pod = data.get("calculating_by_pod")

                    if not lock_pod:
                        logger.info("[IndicatorsCache] Lock released by %s, loading: %s/%s", lock_pod, symbol, tf)
                        try:
                            cached = self.load(symbol, tf)
                            if cached is not None:
                                logger.info("[IndicatorsCache] Other pod result loaded: %s/%s", symbol, tf)
                                return True
                        except Exception as load_err:
                            logger.debug("[IndicatorsCache] Could not load result after wait: %s", load_err)
                            break

                check_interval = min(max_interval, check_interval * 1.5)
                jitter = random.uniform(0, check_interval * 0.1)
                time.sleep(check_interval + jitter)

            except Exception as exc:
                logger.debug("[IndicatorsCache] Wait loop exception %s/%s: %s", symbol, tf, exc)
                break

        logger.debug(
            "[IndicatorsCache] Wait ended (timeout OR error): %s/%s (waited %.1fs)",
            symbol,
            tf,
            time.time() - start_time,
        )
        return False

    def get_or_calculate(
        self,
        symbol: str,
        tf: str,
        df_historicos: pd.DataFrame,
        calc_func: Callable[[pd.DataFrame, str], pd.DataFrame],
    ) -> Tuple[pd.DataFrame, dict]:
        if not self._enabled or _INDICATORS_FORCE_RECALC:
            start_time = time.time()
            df_result = calc_func(df_historicos.copy(), tf)
            calc_time_ms = (time.time() - start_time) * 1000

            return df_result, {
                "cache_hit": False,
                "incremental": False,
                "calc_time_ms": calc_time_ms,
                "source": "full_calc_no_cache",
                "pod_id": self._pod_id,
            }

        cached = self.load(symbol, tf)

        if cached is None:
            lock_acquired = self._acquire_lock(symbol, tf)

            if not lock_acquired:
                logger.info("[IndicatorsCache] Another pod calculating: %s/%s (pod=%s)", symbol, tf, self._pod_id)

                if self._wait_for_lock_release(symbol, tf, max_wait_sec=200):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": False,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod",
                            "pod_id": self._pod_id,
                        }

                logger.warning("[IndicatorsCache] Wait failed, calculating anyway: %s/%s", symbol, tf)
                lock_acquired = True

            if lock_acquired:
                try:
                    logger.info("[IndicatorsCache] Cold start: %s/%s (pod=%s)", symbol, tf, self._pod_id)
                    start_time = time.time()
                    df_result = calc_func(df_historicos.copy(), tf)
                    calc_time_ms = (time.time() - start_time) * 1000

                    indicators = self._extract_indicators_from_df(df_result)
                    self.save(
                        symbol,
                        tf,
                        indicators,
                        df_historicos,
                        calc_time_ms,
                        analysis_audit={
                            "last_mode": "bootstrap",
                            "last_bootstrap_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        },
                    )

                    return df_result, {
                        "cache_hit": False,
                        "incremental": False,
                        "calc_time_ms": calc_time_ms,
                        "source": "full_calc_cold_start",
                        "pod_id": self._pod_id,
                    }
                finally:
                    self._release_lock(symbol, tf)

        cached_hash = cached["metadata"]["data_hash"]
        current_hash = hash_dataframe(df_historicos)
        cached_rows = cached["metadata"]["rows_count"]
        current_rows = len(df_historicos)

        if cached_hash == current_hash and cached_rows == current_rows:
            logger.info("[IndicatorsCache] Perfect hit: %s/%s (%d rows, pod=%s)", symbol, tf, current_rows, self._pod_id)
            df_result, override = self._apply_indicators_or_recalc(
                df_historicos,
                cached["indicators"],
                symbol,
                tf,
                calc_func,
            )
            if override is not None:
                return df_result, override

            return df_result, {
                "cache_hit": True,
                "incremental": False,
                "calc_time_ms": 0,
                "source": "cache_perfect_match",
                "cached_age_hours": (
                    datetime.now(UTC).replace(tzinfo=timezone.utc)
                    - datetime.fromisoformat(cached["metadata"]["last_update_utc"].replace("Z", "+00:00"))
                ).total_seconds()
                / 3600,
                "pod_id": self._pod_id,
            }

        if current_rows == cached_rows and current_rows > 0:
            lock_acquired = self._acquire_lock(symbol, tf)

            if not lock_acquired:
                if self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": True,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod_tail_refresh",
                            "pod_id": self._pod_id,
                        }

            if self._window_func is None:
                raise ValueError("window_func is required for incremental cache")

            try:
                window = self._window_func(tf)
                context_start = max(0, current_rows - window)
                df_to_calc = df_historicos.iloc[context_start:].copy()

                logger.info(
                    "[IndicatorsCache] Tail refresh: %s/%s (rows=%d, context=%d, pod=%s)",
                    symbol,
                    tf,
                    current_rows,
                    window,
                    self._pod_id,
                )

                start_time = time.time()
                df_partial = calc_func(df_to_calc, tf)
                calc_time_ms = (time.time() - start_time) * 1000

                indicators_partial = self._extract_indicators_from_df(df_partial)

                indicators_merged = merge_indicators_incremental(
                    cached["indicators"],
                    indicators_partial,
                    context_start,
                    0,
                )

                df_result, override = self._apply_indicators_or_recalc(
                    df_historicos,
                    indicators_merged,
                    symbol,
                    tf,
                    calc_func,
                )
                if override is not None:
                    return df_result, override

                self.save(
                    symbol,
                    tf,
                    indicators_merged,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "incremental",
                        "last_incremental_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        "last_incremental_bars": 0,
                    },
                    last_calc_index=current_rows - 1,  # 📌 Track exact calc endpoint
                )

                return df_result, {
                    "cache_hit": True,
                    "incremental": True,
                    "calc_time_ms": calc_time_ms,
                    "source": "incremental_tail_refresh",
                    "new_bars": 0,
                    "cached_rows": cached_rows,
                    "total_rows": current_rows,
                    "pod_id": self._pod_id,
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)

        if current_rows > cached_rows:
            lock_acquired = self._acquire_lock(symbol, tf)

            if not lock_acquired:
                if self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        df_result, override = self._apply_indicators_or_recalc(
                            df_historicos,
                            cached["indicators"],
                            symbol,
                            tf,
                            calc_func,
                        )
                        if override is not None:
                            return df_result, override
                        return df_result, {
                            "cache_hit": True,
                            "incremental": True,
                            "calc_time_ms": 0,
                            "source": "waited_for_other_pod_incremental",
                            "pod_id": self._pod_id,
                        }

            if self._window_func is None:
                raise ValueError("window_func is required for incremental cache")

            try:
                new_bars = current_rows - cached_rows
                window = self._window_func(tf)
                context_start = max(0, cached_rows - window)
                df_to_calc = df_historicos.iloc[context_start:].copy()

                logger.info(
                    "[IndicatorsCache] Incremental: %s/%s (+%d bars, context=%d, pod=%s)",
                    symbol,
                    tf,
                    new_bars,
                    window,
                    self._pod_id,
                )

                start_time = time.time()
                df_partial = calc_func(df_to_calc, tf)
                calc_time_ms = (time.time() - start_time) * 1000

                indicators_partial = self._extract_indicators_from_df(df_partial)

                indicators_merged = merge_indicators_incremental(
                    cached["indicators"],
                    indicators_partial,
                    cached_rows,
                    window,
                )

                df_result, override = self._apply_indicators_or_recalc(
                    df_historicos,
                    indicators_merged,
                    symbol,
                    tf,
                    calc_func,
                )
                if override is not None:
                    return df_result, override

                self.save(
                    symbol,
                    tf,
                    indicators_merged,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "incremental",
                        "last_incremental_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                        "last_incremental_bars": int(new_bars),
                    },
                    last_calc_index=current_rows - 1,  # 📌 Track exact calc endpoint
                )

                return df_result, {
                    "cache_hit": True,
                    "incremental": True,
                    "calc_time_ms": calc_time_ms,
                    "source": "incremental_update",
                    "new_bars": new_bars,
                    "cached_rows": cached_rows,
                    "total_rows": current_rows,
                    "pod_id": self._pod_id,
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)

        if 0 < current_rows < cached_rows:
            tail_indicators = self._slice_indicators_tail(cached["indicators"], current_rows)
            if tail_indicators is not None:
                df_result, override = self._apply_indicators_or_recalc(
                    df_historicos,
                    tail_indicators,
                    symbol,
                    tf,
                    calc_func,
                )
                if override is not None:
                    return df_result, override
                logger.info(
                    "[IndicatorsCache] Tail slice hit: %s/%s (cached=%d, current=%d, pod=%s)",
                    symbol,
                    tf,
                    cached_rows,
                    current_rows,
                    self._pod_id,
                )
                return df_result, {
                    "cache_hit": True,
                    "incremental": False,
                    "calc_time_ms": 0,
                    "source": "cache_tail_slice",
                    "cached_rows": cached_rows,
                    "total_rows": current_rows,
                    "pod_id": self._pod_id,
                }

        lock_acquired = self._acquire_lock(symbol, tf)

        try:
            logger.warning(
                "[IndicatorsCache] Data mismatch: %s/%s (cached=%d, current=%d, pod=%s)",
                symbol,
                tf,
                cached_rows,
                current_rows,
                self._pod_id,
            )
            start_time = time.time()
            df_result = calc_func(df_historicos.copy(), tf)
            calc_time_ms = (time.time() - start_time) * 1000

            indicators = self._extract_indicators_from_df(df_result)
            self.save(
                symbol,
                tf,
                indicators,
                df_historicos,
                calc_time_ms,
                analysis_audit={
                    "last_mode": "data_mismatch",
                    "last_data_mismatch_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                },
                last_calc_index=len(df_historicos) - 1,  # 📌 Track exact calc endpoint
            )

            return df_result, {
                "cache_hit": False,
                "incremental": False,
                "calc_time_ms": calc_time_ms,
                "source": "full_calc_data_mismatch",
                "pod_id": self._pod_id,
            }
        finally:
            if lock_acquired:
                self._release_lock(symbol, tf)

    def _extract_indicators_from_df(self, df: pd.DataFrame) -> dict:
        indicators = {}
        base_cols = {"open", "high", "low", "close", "volume", "time"}
        indicator_cols = [col for col in df.columns if col not in base_cols]

        for col in indicator_cols:
            try:
                values = df[col].tolist()
                values = [None if pd.isna(v) else v for v in values]
                indicators[col] = values
            except Exception as exc:
                logger.warning("[IndicatorsCache] Error extracting column %s: %s", col, exc)

        return indicators

    def _apply_indicators_to_df(self, df: pd.DataFrame, indicators: dict) -> pd.DataFrame:
        mismatch = False
        for col, values in indicators.items():
            try:
                if len(values) == len(df):
                    df[col] = values
                else:
                    logger.warning("[IndicatorsCache] Length mismatch for %s: %d vs %d", col, len(values), len(df))
                    mismatch = True
            except Exception as exc:
                logger.warning("[IndicatorsCache] Error applying column %s: %s", col, exc)

        if mismatch:
            raise ValueError("indicator_length_mismatch")
        return df

    def _slice_indicators_tail(self, indicators: dict, rows: int) -> dict | None:
        if rows <= 0:
            return None
        sliced: dict = {}
        for col, values in indicators.items():
            if not isinstance(values, list) or len(values) < rows:
                return None
            sliced[col] = values[-rows:]
        return sliced

    def _apply_indicators_or_recalc(
        self,
        df_historicos: pd.DataFrame,
        indicators: dict,
        symbol: str,
        tf: str,
        calc_func: Callable[[pd.DataFrame, str], pd.DataFrame],
    ) -> Tuple[pd.DataFrame, dict | None]:
        try:
            return self._apply_indicators_to_df(df_historicos.copy(), indicators), None
        except ValueError:
            lock_acquired = self._acquire_lock(symbol, tf)
            try:
                if not lock_acquired and self._wait_for_lock_release(symbol, tf, max_wait_sec=120):
                    cached = self.load(symbol, tf)
                    if cached is not None:
                        try:
                            return self._apply_indicators_to_df(df_historicos.copy(), cached["indicators"]), None
                        except ValueError:
                            pass

                logger.warning("[IndicatorsCache] Length mismatch triggers full recalc: %s/%s", symbol, tf)
                start_time = time.time()
                df_result = calc_func(df_historicos.copy(), tf)
                calc_time_ms = (time.time() - start_time) * 1000
                indicators_full = self._extract_indicators_from_df(df_result)
                self.save(
                    symbol,
                    tf,
                    indicators_full,
                    df_historicos,
                    calc_time_ms,
                    analysis_audit={
                        "last_mode": "data_mismatch",
                        "last_data_mismatch_at": datetime.now(UTC).replace(tzinfo=timezone.utc).isoformat(),
                    },
                )
                return df_result, {
                    "cache_hit": False,
                    "incremental": False,
                    "calc_time_ms": calc_time_ms,
                    "source": "full_calc_indicator_mismatch",
                    "pod_id": self._pod_id,
                }
            finally:
                if lock_acquired:
                    self._release_lock(symbol, tf)


__all__ = [
    "IndicatorsCache",
    "hash_dataframe",
    "merge_indicators_incremental",
    "_INDICATORS_CACHE_ENABLED",
    "_INDICATORS_CACHE_TTL_HOURS",
    "_INDICATORS_FORCE_RECALC",
    "_INDICATORS_MEMORY_CACHE_SIZE",
    "_INDICATORS_LOCK_TIMEOUT_SEC",
]
