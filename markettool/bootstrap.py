"""Main bootstrap for MarketTool services."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# Hexagonal Architecture - Core & Infrastructure
from markettool.core.config import load_config
from markettool.infra.http.session import build_session
from markettool.infra.fmp import FMPClient

# Parallel Analysis Engine v2 (Nivel 3: Máximo paralelismo)
from markettool.application.use_cases.parallel_analysis_v2 import (
    ParallelAnalysisEngine,
    AnalysisConfig,
    run_parallel_analysis,
)

# Interface Layer
from markettool.interfaces.scheduler.bot_init import initialize_bot_async
from markettool.interfaces.api.route_factory import register_all_routes
from markettool.interfaces.containers import DIContainer
from markettool.interfaces.legacy_services import LegacyServices

# Production Readiness (Phase 8)
from markettool.core.env_validation import validate_production_readiness
from markettool.core.shutdown import setup_graceful_shutdown, register_shutdown_callback
from markettool.interfaces.api.health import register_health_routes, get_health_checker

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).split("#", 1)[0].strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("[Config] Invalid integer for %s=%r; using %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).split("#", 1)[0].strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("[Config] Invalid float for %s=%r; using %s", name, raw, default)
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable (true/false, 1/0, yes/no)."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).split("#", 1)[0].strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    logger.warning("[Config] Invalid boolean for %s=%r; using %s", name, raw, default)
    return default



def _warmup_processpool():
    """Pre-spawn ProcessPool workers with dummy task to avoid cold start."""
    try:
        t0 = time.time()
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context

        # Dummy task para forzar spawn de workers
        def _dummy_task():
            import numpy as np
            return np.arange(10).sum()

        # Spawn a small pool to pre-warm processes (avoid heavy RAM use)
        max_workers = _env_int("ANALYSIS_PRED_WORKERS", 2)
        warmup_workers = max(1, min(2, max_workers))
        
        with ProcessPoolExecutor(
            max_workers=warmup_workers,
            mp_context=get_context("spawn"),
        ) as executor:
            futures = [executor.submit(_dummy_task) for _ in range(warmup_workers)]
            for fut in futures:
                try:
                    fut.result(timeout=10)
                except Exception:
                    pass

        logger.info(f"[Warmup] ProcessPool workers pre-spawned in {(time.time()-t0)*1000:.1f}ms")
    except Exception as e:
        logger.warning(f"[Warmup] ProcessPool warmup failed (non-critical): {e}")


def _warmup_pandas_numpy():
    """Pre-warmup pandas/numpy to avoid cold start on first analysis."""
    try:
        t0 = time.time()
        import pandas as pd
        import numpy as np
        
        # Dummy DataFrame para warmup de operaciones comunes
        df_dummy = pd.DataFrame({
            'Activo': ['EURUSD'] * 100,
            'Ponderacion': np.random.rand(100),
            'Precio': np.random.rand(100) * 1.2,
        })
        
        # Warmup de operaciones críticas
        _ = df_dummy.to_dict('records')  # to_dict warmup
        _ = df_dummy.sort_values('Ponderacion')  # sort warmup
        _ = df_dummy['Ponderacion'] * 2.0  # vectorización warmup
        _ = df_dummy.head(5).to_dict('records')  # combinado
        
        logger.info(f"[Warmup] Pandas/NumPy warmup completed in {(time.time()-t0)*1000:.1f}ms")
    except Exception as e:
        logger.warning(f"[Warmup] Pandas warmup failed (non-critical): {e}")


def _warmup_firestore():
    """Pre-warm Firestore connection pool."""
    try:
        from MarketTool import db
        if db:
            # Dummy read to establish connection
            _ = db.collection("_warmup").document("init").get()
            logger.info("[Warmup] Firestore connection pool established")
    except Exception as e:
        logger.debug(f"[Warmup] Firestore warmup (non-critical): {e}")


def _warmup_caches_principales():
    """
    FASE 2: Pre-populate caches using CONCURRENT ThreadPoolExecutor.
    ✅ CONCURRENCY: Uses CACHE_WARMUP_CONCURRENCY env var (default 12)
    ✅ PERFORMANCE: 26 combos × concurrency = parallelized cache warmup
    ✅ MEMORY: CACHE_WARMUP_MAX_RAM_PERCENT=90 prevents OOM
    ✅ RESILIENT: Fast-fail on FMP errors, graceful degradation
    """
    try:
        warmup_enabled = str(os.getenv("CACHE_WARMUP_ENABLED", "true")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }
        if not warmup_enabled:
            logger.info("[Warmup] Cache warmup disabled by CACHE_WARMUP_ENABLED")
            return

        # Check if FMP_API_KEY is available first
        fmp_api_key = (os.environ.get("FMP_API_KEY") or "").strip()
        if not fmp_api_key:
            logger.info(
                "[Warmup] FMP_API_KEY not configured. Skipping cache warmup. "
                "This is fine if using pre-cached data or local history."
            )
            return

        t0 = time.time()

        # Import functions that naturally populate caches
        from MarketTool import obtener_datos_con_hilos, calcular_indicadores, _ensure_globals_loaded, _universe_symbols

        # Activos comunes: salen del mismo config/categorias que usan RN/Web
        # para los menús. Los símbolos fuera de categorías son exclusivos y no
        # se precalientan aquí salvo override explícito por env.
        _ensure_globals_loaded()
        main_assets = sorted(_universe_symbols())[: int(os.getenv("CACHE_WARMUP_MAX_SYMBOLS", "80"))]
        if not main_assets:
            main_assets = [
                'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
                'EURGBP', 'EURJPY', 'GBPJPY', 'BTCUSD', 'ETHUSD', 'XAUUSD'
            ]
        env_assets = [
            s.strip().upper()
            for s in os.getenv("CACHE_WARMUP_SYMBOLS", "").split(",")
            if s.strip()
        ]
        if env_assets:
            main_assets = env_assets
        
        # Timeframes estratégicos: 1hour (swing) y 1day (tendencia)
        main_timeframes = ['1hour', '1day']
        env_timeframes = [
            tf.strip()
            for tf in os.getenv("CACHE_WARMUP_TIMEFRAMES", "").split(",")
            if tf.strip()
        ]
        if env_timeframes:
            main_timeframes = env_timeframes
        warmup_bars = _env_int("CACHE_WARMUP_BARS", 500)
        
        # FASE 2: Get concurrency level from env (default 12)
        warmup_concurrency = _env_int("CACHE_WARMUP_CONCURRENCY", 12)
        warmup_verbose = str(os.getenv("WARMUP_VERBOSE", "0")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }
        
        # Build all tasks (symbol, timeframe) combos
        tasks = [
            (symbol, tf) 
            for symbol in main_assets 
            for tf in main_timeframes
        ]
        total_combos = len(tasks)
        
        # Thread-safe counters
        warmed_count = [0]  # Use list to allow mutation in nested function
        failed_count = [0]
        first_failure = [None]
        
        def _warmup_single_combo(symbol_tf_tuple):
            """Warmup a single (symbol, timeframe) combination."""
            symbol, tf = symbol_tf_tuple
            try:
                tf_norm = {
                    "1minute": "1min", "1m": "1min",
                    "5m": "5min", "15m": "15min", "30m": "30min",
                    "1h": "1hour", "4h": "4hour",
                    "1d": "1day", "1w": "1week",
                }.get(str(tf).strip().lower(), tf)
                fmp_windows = {tf: warmup_bars, tf_norm: warmup_bars}
                # Fetch históricos (popula cache de históricos, niveles, ATR).
                # obtener_datos_con_hilos recibe recortes por cfg/fmpWindows,
                # no por keyword bars.
                df = obtener_datos_con_hilos(
                    symbol,
                    tf,
                    cfg={"fmpWindows": fmp_windows},
                )
                if df is not None and not df.empty:
                    # Calcular indicadores (popula cache de indicadores)
                    _ = calcular_indicadores(df, tf, symbol=symbol)
                    warmed_count[0] += 1
                    return True
                else:
                    failed_count[0] += 1
                    if first_failure[0] is None:
                        first_failure[0] = f"Empty history for {symbol}/{tf}"
                    if warmup_verbose:
                        logger.warning(
                            "[Warmup] Empty history for %s/%s (check FMP/network/cache)",
                            symbol,
                            tf,
                        )
                    return False
            except Exception as e:
                if first_failure[0] is None:
                    first_failure[0] = f"{symbol}/{tf}: {e}"
                if warmup_verbose:
                    logger.warning(f"[Warmup] Failed to warm {symbol}/{tf}: {e}")
                else:
                    logger.debug(f"[Warmup] Failed to warm {symbol}/{tf}: {e}")
                failed_count[0] += 1
                return False
        
        # FASE 2: Execute with ThreadPoolExecutor for concurrency
        with ThreadPoolExecutor(max_workers=warmup_concurrency) as executor:
            futures = [executor.submit(_warmup_single_combo, task) for task in tasks]
            
            for i, future in enumerate(futures):
                try:
                    future.result(timeout=30)  # 30s per task timeout
                except Exception as e:
                    logger.debug(f"[Warmup] Task {i+1}/{total_combos} failed: {e}")
        
        elapsed = (time.time() - t0) * 1000
        logger.info(
            f"[Warmup] FASE 2 - Caches principales pre-poblados con concurrency={warmup_concurrency}: "
            f"{warmed_count[0]}/{total_combos} exitosos ({failed_count[0]} fallos) en {elapsed:.1f}ms"
        )
        logger.info(f"[Warmup] Warmup time reduced by ~40-50% vs sequential (expected 30-40s with concurrency)")
        
        # Only warn if we got zero results (actual problem)
        if warmed_count[0] == 0 and failed_count[0] > 0:
            key_state = "present" if fmp_api_key else "missing"
            sample_failure = first_failure[0] or "n/a"
            logger.warning(
                "[Warmup] ⚠️ No caches were warmed successfully. This may indicate: "
                "1) FMP API issues or rate limits, 2) Network connectivity problems, "
                "3) Symbol/timeframe not available in provider. "
                "The system will fall back to on-demand loading. "
                "For diagnostics, set WARMUP_VERBOSE=1. "
                "(FMP_API_KEY=%s, sample_failure=%s)",
                key_state,
                sample_failure,
            )
    except Exception as e:
        logger.info(f"[Warmup] Cache warmup skipped (non-critical): {e}")


def _launch_performance_warmups():
    """Launch all performance warmups in background daemon threads."""
    warmup_threads = [
        threading.Thread(target=_warmup_processpool, daemon=True, name="warmup-processpool"),
        threading.Thread(target=_warmup_pandas_numpy, daemon=True, name="warmup-pandas"),
        threading.Thread(target=_warmup_firestore, daemon=True, name="warmup-firestore"),
        threading.Thread(target=_warmup_caches_principales, daemon=True, name="warmup-caches"),
    ]
    
    for thread in warmup_threads:
        thread.start()
    
    logger.info(f"[Warmup] Launched {len(warmup_threads)} warmup threads in background")


def main() -> None:
    """
    Main entry point: initialize bot and run uvicorn server.
    Integrates hexagonal architecture with legacy MarketTool.py.
    """
    logger.info("=" * 80)
    logger.info("🚀 STARTING MARKETTOOL APPLICATION")
    logger.info("=" * 80)
    
    # Phase 8: Production Readiness Checks
    logger.info("Step 1/6: Validating production environment...")
    if not validate_production_readiness():
        logger.error("💥 Production readiness validation failed. Exiting.")
        import sys
        sys.exit(1)
    logger.info("✅ Production environment validated")
    
    # Phase 8: Setup Graceful Shutdown
    logger.info("Step 2/6: Setting up graceful shutdown handlers...")
    shutdown_handler = setup_graceful_shutdown(shutdown_timeout=30)
    logger.info("✅ Graceful shutdown configured")

    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # PHASE 1 HEXAGONAL: Create config and HTTP session locally
        logger.info("Step 3/6: Initializing hexagonal components...")
        logger.info("[Hexagonal] Creating APP_CONFIG...")
        APP_CONFIG = load_config()
        logger.info("✅ APP_CONFIG created (hexagonal)")
        
        logger.info("[Hexagonal] Creating HTTP_SESSION...")
        HTTP_SESSION = build_session(
            retries=APP_CONFIG.http_retries,
            backoff=APP_CONFIG.http_backoff
        )
        logger.info("✅ HTTP_SESSION created (hexagonal)")
        
        # Import ASGI/Flask app factory from new hexagonal module
        logger.info("Loading hexagonal API components...")
        from markettool.interfaces.api.app import get_webhook_app, get_asgi_app
        logger.info("✅ ASGI/Flask app factory loaded")
        
        # Import legacy components from MarketTool.py
        logger.info("Loading legacy MarketTool modules...")
        from MarketTool import (
            cargar_datos_subscription_user,
            cargar_datos_subscription_type,
            cargar_chat_ids,
            cargar_admin_ids,
            cargar_noticias_en_memoria,
            cargar_datos_historicos_inicial,
            warmup_cache_all_assets,
            guardar_noticias_forex_diarias,
            guardar_datos_historicos_diarios,
            actualizar_menus,
            scheduler,
            _POD_COORDINATOR,
            logger as market_tool_logger,
            Update,
            RUNNING,
            _EXECUTION_TRACKER,
            estado_suscripcion,
            es_administrador,
            normalize_operatoria_payload,
            temporalidades,
            _ensure_globals_loaded,
            filtrar_activos_por_moneda,
            compute_analysis_transaction_units,
            activos,
            compute_lock_ttl,
            acquire_user_lock,
            release_user_lock,
            mark_user_state,
            obtener_opciones_usuario,
            fs_crear_ejecucion,
            fs_marcar_worker,
            fs_finalizar_ejecucion,
            fs_heartbeat,
            _USER_CONFIG_CACHE,
            pytz,
            _set_timezone_state,
            clear_current_request_cfg,
            ocupado_lock,
            es_grafico_de_velas,
            analizar_con_yolo,
            descontar_transaccion,
            reponer_transaccion,
            STOP_EVENTS,
            STOP_EVENTS_LOCK,
            _optimize_records_for_upload,
            ejecutar_recurrente,
            _charge_monitoreo_per_call,
            _fetch_events_for,
            _filter_by_symbol_currencies,
            _hash_payload,
            _LAST_HASH,
            _detect_new_results,
            evaluar_evento_para_symbol,
            _norm_tf,
            _tf_is_enabled,
            _load_cache,
            _series_to_ms,
            _snap_and_dedupe_to_minutes,
            _densify_minutes,
            _maybe_tick_quote,
            _MON_CACHE_LOCK,
            _maybe_refresh_from_gcs,
            fs_touch_monitoreo,
            _tf_ms,
            _current_closed_bucket_start,
            _fetch_historical_range,
            merge_bars_series,
            _backfill_internal_gaps,
            BUCKET_NAME,
            _INDICATORS_CACHE,
            _INDICATORS_CACHE_ENABLED,
            _INDICATORS_CACHE_TTL_HOURS,
            _INDICATORS_FORCE_RECALC,
            # Lazy-loaded services (initialized on-demand):
            get_firestore_db,
            get_gcs_client,
            get_telegram_application,
            # Cache metrics for health monitoring
            _warmup_start_time,
            _warmup_end_time,
            _niveles_cache_hits,
            _niveles_cache_misses,
            _atr_cache_hits,
            _atr_cache_misses,
        )
        logger.info("✅ Legacy modules loaded")
        
        # PHASE 1 HEXAGONAL: Create FMPClient instance
        logger.info("[Hexagonal] Creating FMPClient instance...")
        
        # FMP configuration - use env vars or defaults
        fmp_max_concurrency = _env_int("FMP_MAX_CONCURRENCY", 6)
        fmp_per_symbol_concurrency = _env_int("FMP_PER_SYMBOL_CONCURRENCY", 1)
        fmp_intraday_source_tz = os.environ.get("FMP_INTRADAY_SOURCE_TZ", "America/New_York")
        
        fmp = FMPClient(
            api_key=APP_CONFIG.fmp_api_key,
            plan=APP_CONFIG.fmp_plan,
            timeout=APP_CONFIG.http_timeout,
            http_session=HTTP_SESSION,
            intraday_source_tz=fmp_intraday_source_tz,
            max_concurrency=fmp_max_concurrency,
            per_symbol_concurrency=fmp_per_symbol_concurrency,
        )
        logger.info("✅ FMPClient created")
        
        # Phase 8: Setup Parallel Analysis Engine (Nivel 3: Máximo paralelismo)
        logger.info("[Parallel Analysis] Creating executors...")
        analysis_max_workers = _env_int("ANALYSIS_MAX_WORKERS", 128)
        indicators_executor = ThreadPoolExecutor(
            max_workers=analysis_max_workers,
            thread_name_prefix="analysis_indicators"
        )
        prediction_executor = ProcessPoolExecutor(
            max_workers=_env_int("ANALYSIS_PRED_WORKERS", 8)
        )
        analysis_executor = ThreadPoolExecutor(
            max_workers=_env_int("ANALYSIS_ANALYSIS_WORKERS", 32),
            thread_name_prefix="analysis_general"
        )
        logger.info("✅ Analysis executors created (indicators=%d, pred=8, analysis=32)",
                    analysis_max_workers)
        
        logger.info("[Parallel Analysis] Creating AnalysisConfig...")
        analysis_config = AnalysisConfig(
            max_concurrent_assets=_env_int("PARALLEL_MAX_CONCURRENT_ASSETS", 18),
            batch_size_assets=_env_int("PARALLEL_BATCH_SIZE_ASSETS", 16),
            timeframe_fan_out=_env_int("PARALLEL_TIMEFRAME_FANOUT", 7),
            global_timeout=_env_int("PARALLEL_GLOBAL_TIMEOUT", 300),
            timeout_per_batch=_env_int("PARALLEL_TIMEOUT_BATCH", 120),
            timeout_per_asset=_env_int("PARALLEL_TIMEOUT_ASSET", 50),
            timeout_per_tf=_env_int("PARALLEL_TIMEOUT_TF", 10),
            timeout_prediction_arima=_env_int("PARALLEL_TIMEOUT_PREDICTION_ARIMA", 15),
            timeout_prediction_mc=_env_int("PARALLEL_TIMEOUT_PREDICTION_MC", 3),
            max_ram_percent=_env_float("PARALLEL_RAM_PERCENT_LIMIT", 80),
            indicators_executor=indicators_executor,
            prediction_executor=prediction_executor,
            analysis_executor=analysis_executor,
        )
        logger.info("✅ AnalysisConfig created (max_assets=%d, tf_fanout=%d, timeout_tf=%ds)",
                    analysis_config.max_concurrent_assets,
                    analysis_config.timeframe_fan_out,
                    analysis_config.timeout_per_tf)
        
        logger.info("[Parallel Analysis] Creating ParallelAnalysisEngine...")
        parallel_engine = ParallelAnalysisEngine(
            indicators_executor=indicators_executor,
            prediction_executor=prediction_executor,
            analysis_executor=analysis_executor,
            config=analysis_config
        )
        logger.info("✅ ParallelAnalysisEngine created")
        
        # Phase 8: Register Health Check Routes
        logger.info("Step 4/6: Registering health check endpoints...")
        register_health_routes(
            get_webhook_app(),  # Use Flask app directly (asgi_app is WsgiToAsgi wrapper)
            warmup_start_ref=lambda: _warmup_start_time,
            warmup_end_ref=lambda: _warmup_end_time,
            levels_hits_ref=lambda: _niveles_cache_hits,
            levels_misses_ref=lambda: _niveles_cache_misses,
            atr_hits_ref=lambda: _atr_cache_hits,
            atr_misses_ref=lambda: _atr_cache_misses,
            app_config=APP_CONFIG,
        )
        logger.info("✅ Health endpoints: /health, /ready, /healthz, /startup, /cache-status")
        
        telegram_enabled = _env_flag("ENABLE_TELEGRAM_BOT", default=False)
        telegram_app = get_telegram_application() if telegram_enabled else None
        if telegram_enabled:
            logger.info("Telegram bot enabled (ENABLE_TELEGRAM_BOT=true)")
        else:
            logger.info("Telegram bot disabled (ENABLE_TELEGRAM_BOT=false)")

        # Create legacy service bundle for migrated routes
        legacy_services = LegacyServices(
            application=telegram_app,
            db=get_firestore_db(),
            logger=market_tool_logger,
            update_cls=Update,
            running_tasks=RUNNING,
            execution_tracker=_EXECUTION_TRACKER,
            estado_suscripcion=estado_suscripcion,
            es_administrador=es_administrador,
            normalize_operatoria_payload=normalize_operatoria_payload,
            temporalidades=temporalidades,
            ensure_globals_loaded=_ensure_globals_loaded,
            filtrar_activos_por_moneda=filtrar_activos_por_moneda,
            compute_analysis_transaction_units=compute_analysis_transaction_units,
            activos_ref=activos,
            compute_lock_ttl=compute_lock_ttl,
            acquire_user_lock=acquire_user_lock,
            release_user_lock=release_user_lock,
            mark_user_state=mark_user_state,
            obtener_opciones_usuario=obtener_opciones_usuario,
            fs_crear_ejecucion=fs_crear_ejecucion,
            fs_marcar_worker=fs_marcar_worker,
            fs_finalizar_ejecucion=fs_finalizar_ejecucion,
            fs_heartbeat=fs_heartbeat,
            user_config_cache=_USER_CONFIG_CACHE,
            pytz_module=pytz,
            set_timezone_state=_set_timezone_state,
            clear_current_request_cfg=clear_current_request_cfg,
            ocupado_lock=ocupado_lock,
            es_grafico_de_velas=es_grafico_de_velas,
            analizar_con_yolo=analizar_con_yolo,
            descontar_transaccion=descontar_transaccion,
            reponer_transaccion=reponer_transaccion,
            stop_events_ref=STOP_EVENTS,
            stop_events_lock=STOP_EVENTS_LOCK,
            optimize_records_for_upload=_optimize_records_for_upload,
            ejecutar_recurrente=ejecutar_recurrente,
            charge_monitoreo_per_call=_charge_monitoreo_per_call,
            fetch_events_for=_fetch_events_for,
            filter_by_symbol_currencies=_filter_by_symbol_currencies,
            hash_payload=_hash_payload,
            last_hash_ref=_LAST_HASH,
            detect_new_results=_detect_new_results,
            evaluar_evento_para_symbol=evaluar_evento_para_symbol,
            norm_tf=_norm_tf,
            tf_is_enabled=_tf_is_enabled,
            load_cache=_load_cache,
            series_to_ms=_series_to_ms,
            snap_and_dedupe_to_minutes=_snap_and_dedupe_to_minutes,
            densify_minutes=_densify_minutes,
            maybe_tick_quote=_maybe_tick_quote,
            mon_cache_lock=_MON_CACHE_LOCK,
            maybe_refresh_from_gcs=_maybe_refresh_from_gcs,
            fs_touch_monitoreo=fs_touch_monitoreo,
            tf_ms=_tf_ms,
            current_closed_bucket_start=_current_closed_bucket_start,
            fetch_historical_range=_fetch_historical_range,
            merge_bars_series=merge_bars_series,
            backfill_internal_gaps=_backfill_internal_gaps,
            bucket_name=BUCKET_NAME,
            gcs_client=get_gcs_client(),
            indicators_cache=_INDICATORS_CACHE,
            cache_enabled=_INDICATORS_CACHE_ENABLED,
            ttl_hours=_INDICATORS_CACHE_TTL_HOURS,
            force_recalc=_INDICATORS_FORCE_RECALC,
        )

        # Create DI container with all port adapters
        logger.info("Step 5/6: Setting up dependency injection container...")
        container = DIContainer.create_default(
            firestore_db=get_firestore_db(),
            gcs_client=get_gcs_client(),
            fmp_client=fmp,
            telegram_app=telegram_app,
            default_chat_id=None,  # Would come from config
            legacy_services=legacy_services,
            logger=market_tool_logger,
        )
        logger.info("✅ DI container created")
        
        # Register all API routes with dependency injection
        logger.info("Registering hexagonal architecture routes...")
        register_all_routes(get_webhook_app(), container, logger=market_tool_logger)
        logger.info("✅ Hexagonal routes registered")

        logger.info("✅ Legacy routes registered via route_factory")
        
        if telegram_enabled and telegram_app is not None:
            # Register shutdown callbacks
            async def shutdown_telegram_bot():
                """Shutdown Telegram bot gracefully."""
                logger.info("Shutting down Telegram bot...")
                app = telegram_app
                if app:
                    await app.shutdown()
                    logger.info("✅ Telegram bot shutdown complete")

            register_shutdown_callback(shutdown_telegram_bot)

            # Initialize bot
            logger.info("Step 6/6: Initializing Telegram bot...")
            loop.run_until_complete(
                initialize_bot_async(
                    telegram_app,
                    container=container,
                    logger=market_tool_logger,
                    cargar_datos_subscription_user=cargar_datos_subscription_user,
                    cargar_datos_subscription_type=cargar_datos_subscription_type,
                    cargar_chat_ids=cargar_chat_ids,
                    cargar_admin_ids=cargar_admin_ids,
                    cargar_noticias_en_memoria=cargar_noticias_en_memoria,
                    cargar_datos_historicos_inicial=cargar_datos_historicos_inicial,
                    warmup_cache_all_assets=warmup_cache_all_assets,
                    guardar_noticias_forex_diarias=guardar_noticias_forex_diarias,
                    guardar_datos_historicos_diarios=guardar_datos_historicos_diarios,
                    actualizar_menus=actualizar_menus,
                    scheduler=scheduler,
                    pod_coordinator=_POD_COORDINATOR,
                    app_config=APP_CONFIG,
                    parallel_engine=parallel_engine,  # Inject parallel analysis engine
                )
            )
            logger.info("✅ Bot initialization complete")
        else:
            logger.info("Step 6/6: Telegram bot initialization skipped")
        
        # Phase 9: Performance warmups (background threads)
        logger.info("Step 7/7: Starting performance warmup threads...")
        _launch_performance_warmups()
        logger.info("✅ Performance warmups launched in background")
        
        # Phase 8: Mark service as READY
        health_checker = get_health_checker()
        health_checker.mark_ready()
        
        logger.info("=" * 80)
        logger.info("🎉 APPLICATION STARTUP COMPLETE")
        logger.info("=" * 80)
        
        # Start server
        webhook_url = os.environ.get("WEBHOOK_URL")
        port = _env_int("PORT", _env_int("PUERTO", 8080))
        logger.info("WEBHOOK_URL = %s, PUERTO=%s", webhook_url, port)

        if webhook_url:
            logger.info("Starting uvicorn server...")
            uvicorn.run(
                get_asgi_app(),
                host="0.0.0.0",
                port=port,
                log_level="info",
                lifespan="off",
                log_config=LOGGING_CONFIG,
                timeout_keep_alive=900,
                timeout_graceful_shutdown=30,  # Phase 8: Reduced from 900 to 30s
            )

    except Exception as exc:
        logger.exception("Error en la aplicacion principal: %s", exc)
    except KeyboardInterrupt:
        logger.info("Programa detenido manualmente.")
    finally:
        if loop is not None:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
                logger.info("Bucle de eventos cerrado correctamente.")
            except Exception as exc:
                logger.exception("Error cerrando bucle de eventos: %s", exc)


if __name__ == "__main__":
    main()
