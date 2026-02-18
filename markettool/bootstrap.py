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

# Production Readiness (Phase 8)
from markettool.core.env_validation import validate_production_readiness
from markettool.core.shutdown import setup_graceful_shutdown, register_shutdown_callback
from markettool.interfaces.api.health import register_health_routes, get_health_checker

logger = logging.getLogger(__name__)


def _warmup_processpool():
    """Pre-spawn ProcessPool workers with dummy task to avoid cold start."""
    try:
        t0 = time.time()
        from markettool.domain.analysis.parallel_engine import _get_or_create_executor
        
        # Dummy task para forzar spawn de workers
        def _dummy_task():
            import numpy as np
            return np.arange(10).sum()
        
        # Crear executors (esto triggerea spawn)
        prediccion_executor = _get_or_create_executor("prediccion")
        
        # Submit dummy tasks para 2 de 4 workers (no todos - ahorro RAM)
        futures = [prediccion_executor.submit(_dummy_task) for _ in range(2)]
        
        # No esperar - daemon thread
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
    Pre-populate caches for most traded assets to avoid cold start.
    ✅ EXPANDED: Cubre principales majors, cruces, crypto y commodities.
    """
    try:
        t0 = time.time()
        
        # Import functions that naturally populate caches
        from MarketTool import obtener_datos_con_hilos, calcular_indicadores
        
        # ✅ EXPANDED WARMUP: Activos más líquidos y frecuentemente analizados
        # Categorías:
        # - Majors (7): EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD
        # - Cruces (3): EURGBP, EURJPY, GBPJPY
        # - Crypto (2): BTCUSD, ETHUSD
        # - Commodities (1): XAUUSD (oro)
        # Total: 13 activos × 2 timeframes = 26 combinaciones
        main_assets = [
            # Forex Majors (más volumen y liquidez)
            'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
            # Cruces importantes
            'EURGBP', 'EURJPY', 'GBPJPY',
            # Crypto
            'BTCUSD', 'ETHUSD',
            # Commodities
            'XAUUSD'
        ]
        
        # Timeframes estratégicos: 1hour (swing) y 1day (tendencia)
        main_timeframes = ['1hour', '1day']
        
        warmed_count = 0
        failed_count = 0
        total_combos = len(main_assets) * len(main_timeframes)
        
        for symbol in main_assets:
            for tf in main_timeframes:
                try:
                    # Fetch históricos (popula cache de históricos, niveles, ATR)
                    df = obtener_datos_con_hilos(symbol, tf, bars=500)
                    if df is not None and not df.empty:
                        # Calcular indicadores (popula cache de indicadores)
                        _ = calcular_indicadores(df, tf, symbol=symbol)
                        warmed_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    logger.debug(f"[Warmup] Failed to warm {symbol}/{tf}: {e}")
                    failed_count += 1
                    
                # Yield para no bloquear event loop
                time.sleep(0.01)
        
        elapsed = (time.time() - t0) * 1000
        logger.info(
            f"[Warmup] Caches principales pre-poblados: {warmed_count}/{total_combos} exitosos "
            f"({failed_count} fallos) en {elapsed:.1f}ms"
        )
    except Exception as e:
        logger.warning(f"[Warmup] Cache warmup failed (non-critical): {e}")


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
            # Lazy-loaded services (initialized on-demand):
            get_firestore_db,
            get_gcs_client,
            get_telegram_application,
            get_webhook_app,
            get_asgi_app,
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
        fmp_max_concurrency = int(os.environ.get("FMP_MAX_CONCURRENCY", "6"))
        fmp_per_symbol_concurrency = int(os.environ.get("FMP_PER_SYMBOL_CONCURRENCY", "1"))
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
        analysis_max_workers = int(os.environ.get("ANALYSIS_MAX_WORKERS", "128"))
        indicators_executor = ThreadPoolExecutor(
            max_workers=analysis_max_workers,
            thread_name_prefix="analysis_indicators"
        )
        prediction_executor = ProcessPoolExecutor(
            max_workers=int(os.environ.get("ANALYSIS_PRED_WORKERS", "8"))
        )
        analysis_executor = ThreadPoolExecutor(
            max_workers=int(os.environ.get("ANALYSIS_ANALYSIS_WORKERS", "32")),
            thread_name_prefix="analysis_general"
        )
        logger.info("✅ Analysis executors created (indicators=%d, pred=8, analysis=32)",
                    analysis_max_workers)
        
        logger.info("[Parallel Analysis] Creating AnalysisConfig...")
        analysis_config = AnalysisConfig(
            max_concurrent_assets=int(os.environ.get("PARALLEL_MAX_CONCURRENT_ASSETS", "18")),
            batch_size_assets=int(os.environ.get("PARALLEL_BATCH_SIZE_ASSETS", "16")),
            timeframe_fan_out=int(os.environ.get("PARALLEL_TIMEFRAME_FANOUT", "7")),
            global_timeout=int(os.environ.get("PARALLEL_GLOBAL_TIMEOUT", "300")),
            timeout_per_batch=int(os.environ.get("PARALLEL_TIMEOUT_BATCH", "120")),
            timeout_per_asset=int(os.environ.get("PARALLEL_TIMEOUT_ASSET", "50")),
            timeout_per_tf=int(os.environ.get("PARALLEL_TIMEOUT_TF", "10")),
            timeout_prediction_arima=int(os.environ.get("PARALLEL_TIMEOUT_PREDICTION_ARIMA", "15")),
            timeout_prediction_mc=int(os.environ.get("PARALLEL_TIMEOUT_PREDICTION_MC", "3")),
            max_ram_percent=float(os.environ.get("PARALLEL_RAM_PERCENT_LIMIT", "80")),
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
        
        # Create DI container with all port adapters
        logger.info("Step 5/6: Setting up dependency injection container...")
        container = DIContainer.create_default(
            firestore_db=get_firestore_db(),
            gcs_client=get_gcs_client(),
            fmp_client=fmp,
            telegram_app=get_telegram_application(),
            default_chat_id=None,  # Would come from config
            logger=market_tool_logger,
        )
        logger.info("✅ DI container created")
        
        # Register all API routes with dependency injection
        logger.info("Registering hexagonal architecture routes...")
        register_all_routes(get_webhook_app(), container, logger=market_tool_logger)
        logger.info("✅ Hexagonal routes registered")
        
        # Register shutdown callbacks
        async def shutdown_telegram_bot():
            """Shutdown Telegram bot gracefully."""
            logger.info("Shutting down Telegram bot...")
            app = get_telegram_application()
            if app:
                await app.shutdown()
                logger.info("✅ Telegram bot shutdown complete")
        
        register_shutdown_callback(shutdown_telegram_bot)
        
        # Initialize bot
        logger.info("Step 6/6: Initializing Telegram bot...")
        loop.run_until_complete(
            initialize_bot_async(
                get_telegram_application(),
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
        port = int(os.environ.get("PORT", os.environ.get("PUERTO", 8080)))
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
