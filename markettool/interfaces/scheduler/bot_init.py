"""Bot initialization and scheduler setup."""

from __future__ import annotations

import asyncio
pass
import os
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from telegram.ext import Application


async def initialize_bot_async(
    application,
    *,
    container=None,
    logger,
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
    pod_coordinator,
    app_config,
    parallel_engine=None,  # Parallel Analysis Engine (optional)
) -> tuple:
    """
    Async initialization sequence for bot and cache.
    Returns: (subscriptions, subscriptions_type, clientes_chat_ids, admin_ids)
    """
    try:
        from markettool.interfaces.bot.handlers import register_bot_handlers
        
        logger.info("Inicializando la aplicacion de Telegram...")
        start_time = asyncio.get_event_loop().time()
        await application.initialize()
        logger.info(
            "Tiempo en inicializar aplicacion: %.2f segundos",
            asyncio.get_event_loop().time() - start_time,
        )

        # Register all bot handlers (with hexagonal architecture if container available)
        logger.info("Registrando handlers del bot...")
        if container:
            from markettool.interfaces.bot.telegram_handlers import register_handlers_with_app
            # Use "mixed" mode to coexist with legacy handlers
            await register_handlers_with_app(application, container, mode="mixed")
            logger.info("[Hexagonal] Handlers hexagonales registrados en modo MIXED")
            # ✅ Legacy handlers NOT registered - hexagonal version is sufficient
        else:
            # Fallback to legacy handlers only (if no container available)
            from markettool.interfaces.bot.handlers import register_bot_handlers
            register_bot_handlers(application, logger=logger)
            logger.info("[Legacy] Handlers legacy registrados (sin container)")

        logger.info("Cargando datos iniciales...")
        (subscriptions, subscriptions_type, clientes_chat_ids, admin_ids,) = (
            await asyncio.gather(
                cargar_datos_subscription_user(),
                cargar_datos_subscription_type(),
                cargar_chat_ids(),
                cargar_admin_ids(),
            )
        )

        logger.info("Cargando noticias y datos historicos...")
        await asyncio.gather(
            cargar_noticias_en_memoria(),
            cargar_datos_historicos_inicial(),
        )

        logger.info("[MultiPod] Initializing pod coordinator...")
        await pod_coordinator.try_become_leader()

        loop = asyncio.get_event_loop()
        await pod_coordinator.start_heartbeat(loop)

        if app_config.cache_warmup_enabled:
            if app_config.cache_warmup_blocking_startup:
                logger.info("[Warmup] Ejecutando pre-calentamiento bloqueante al startup...")
                await warmup_cache_all_assets("startup_blocking")
            else:
                logger.info("[Warmup] Programando pre-calentamiento en background...")
                asyncio.create_task(warmup_cache_all_assets("startup_background"))

        asyncio.create_task(guardar_noticias_forex_diarias())
        asyncio.create_task(guardar_datos_historicos_diarios())

        if pod_coordinator.should_run_scheduled_task("actualizar_menus_inicial"):
            asyncio.create_task(actualizar_menus(application))

        logger.info("Configurando scheduler de tareas periódicas...")
        setup_scheduler(
            application,
            logger=logger,
            scheduler=scheduler,
            actualizar_menus=actualizar_menus,
            warmup_cache_all_assets=warmup_cache_all_assets,
            pod_coordinator=pod_coordinator,
            app_config=app_config,
            parallel_engine=parallel_engine,
        )

        webhook_url = os.environ.get("WEBHOOK_URL")
        logger.info("WEBHOOK_URL = %s", webhook_url)
        if webhook_url:
            logger.info("Configurando webhook...")
            full_webhook_url = f"https://{webhook_url}/webhook"
            current_webhook = await application.bot.get_webhook_info()
            logger.info("EL Webhook configurado en telegram es: %s", current_webhook)
            if current_webhook.url != full_webhook_url:
                await application.bot.set_webhook(
                    full_webhook_url,
                    drop_pending_updates=True,  # Limpiar updates acumulados al re-registrar
                )
                logger.info("Webhook configurado correctamente (pending updates descartados)")
            else:
                logger.info("El webhook ya esta configurado.")

        await application.start()
        logger.info("Bot iniciado correctamente.")

        _warmup_start_time = asyncio.get_event_loop().time()
        return subscriptions, subscriptions_type, clientes_chat_ids, admin_ids

    except Exception as exc:
        logger.exception("Error durante inicializacion del bot: %s", exc)
        raise


def setup_scheduler(
    application: Application,
    *,
    logger,
    scheduler,
    actualizar_menus,
    warmup_cache_all_assets,
    pod_coordinator,
    app_config,
    parallel_engine=None,
    container=None,  # ✅ DI Container for hexagonal services
) -> None:
    """
    Set up APScheduler jobs for recurring tasks.
    Uses AsyncIOScheduler - jobs run in the same event loop (no thread-safe wrapper needed).
    """
    # Define async job functions (no need for asyncio.run_coroutine_threadsafe)
    async def _actualizar_menus_job():
        """Async job: actualizar menus with pod coordination."""
        if not pod_coordinator.should_run_scheduled_task("actualizar_menus"):
            return
        await actualizar_menus(application)  # Direct await (same event loop)

    async def _warmup_job():
        """Async job: cache warmup."""
        await warmup_cache_all_assets("scheduled")  # Direct await (same event loop)

    async def _parallel_analysis_job():
        """Async job: Parallel analysis engine v2 (3-level parallelism, 100x faster)."""
        if not parallel_engine:
            logger.warning("[Parallel Analysis] No parallel_engine available")
            return
        if not pod_coordinator.should_run_scheduled_task("parallel_analysis"):
            logger.debug("[Parallel Analysis] Skipped (pod coordination)")
            return
        
        try:
            from markettool.application.use_cases.parallel_analysis_v2 import run_parallel_analysis
            
            # ✅ Sprint 4: Using hexagonal services from DI Container
            logger.info("[Parallel Analysis v2] Starting parallel analysis batch (100x faster)...")
            
            # Get symbols to analyze (hexagonal way)
            symbols = None
            if container:
                try:
                    symbols = await container.get_market_symbols.execute()
                    logger.info(f"[Hexagonal] Loaded {len(symbols)} symbols from GetMarketSymbolsUseCase")
                except Exception as e:
                    logger.warning(f"[Parallel Analysis] Error loading symbols from container: {e}")
            
            if not symbols:
                # Fallback to legacy if container unavailable
                try:
                    from MarketTool import cargar_activos_en_mercado
                    symbols = cargar_activos_en_mercado()
                    if hasattr(symbols, '__await__'):
                        symbols = await symbols
                    logger.warning("[Legacy] Using MarketTool.cargar_activos_en_mercado (fallback)")
                except Exception as e:
                    logger.error(f"[Parallel Analysis] Failed to load symbols: {e}")
                    return
            
            if not symbols:
                logger.warning("[Parallel Analysis] No symbols to analyze, skipping batch")
                return
            
            # TF ordenados por coste computacional
            tfs = parallel_engine.config.ordered_tfs
            
            logger.info(f"[Parallel Analysis v2] Analyzing {len(symbols)} symbols × {len(tfs)} TF...")
            
            # Define load_history function (hexagonal or legacy)
            async def load_history_fn(symbol: str, tf: str):
                """Load historical data using hexagonal or legacy method."""
                if container:
                    try:
                        # ✅ Hexagonal: Use HistoryManager
                        from markettool.application.services.historicos_service import HistoryConfig
                        df = container.history_manager.get(symbol, tf, cfg=HistoryConfig(bars=500))
                        return df
                    except Exception as exc:
                        logger.warning(f"[Hexagonal] Failed to load {symbol}/{tf}: {exc}")
                
                # Fallback to legacy
                from MarketTool import load_cached_history
                return load_cached_history(symbol, tf)
            
            # Run parallel analysis using new v2 API
            results = await run_parallel_analysis(
                symbols=symbols,
                tfs=tfs,
                load_history_fn=load_history_fn,
                df_eventos=None,  # Optional market events dataframe
                cfg=parallel_engine.config,  # Use the configured AnalysisConfig
                on_progress=lambda s, tf, sig: logger.debug(
                    f"[Parallel Analysis v2] {s} {tf}: {sig.get('direction', 'N/A')}"
                ),
            )
            
            logger.info(f"[Parallel Analysis v2] ✅ Batch complete: {len(results)} symbols analyzed")
            
            # Persist signals to Firestore (hexagonal or legacy)
            if results:
                try:
                    if container:
                        # ✅ Hexagonal: Use SignalRepository
                        # Convert results to Signal objects
                        from markettool.core.models.signal import Signal, SignalType
                        from datetime import datetime, timezone
                        
                        signals = []
                        for result in results:
                            # Map result dict to Signal model
                            signal = Signal(
                                symbol=result.get('symbol', ''),
                                signal_type=SignalType(result.get('direction', 'NEUTRAL')),
                                timestamp=datetime.now(timezone.utc),
                                confidence=result.get('confidence', 0.5),
                                entry_price=result.get('entry_price'),
                                target_price=result.get('target_price'),
                                stop_loss=result.get('stop_loss'),
                                reason=result.get('reason'),
                                indicators=result.get('indicators', {}),
                                source="parallel_analysis_v2",
                                analysis_type="technical",
                            )
                            signals.append(signal)
                        
                        saved_count = await container.signal_repository.save_batch(signals)
                        logger.info(f"[Hexagonal] ✅ {saved_count} signals persisted to Firestore via SignalRepository")
                    else:
                        # Legacy fallback
                        from MarketTool import guardar_seniales_a_firebase
                        await guardar_seniales_a_firebase(results)
                        logger.info("[Legacy] ✅ Signals persisted to Firestore (legacy method)")
                        
                except Exception as e:
                    logger.exception("[Parallel Analysis v2] Error persisting signals: %s", e)
                    # Don't fail the job if persistence fails
                
        except Exception as exc:
            logger.exception("[Parallel Analysis v2] Error in parallel analysis job: %s", exc)

    try:
        scheduler.add_job(
            _actualizar_menus_job,
            IntervalTrigger(minutes=10),
        )
        logger.info("[Scheduler] Registrado job: actualizar_menus cada 10 minutos")

        scheduler.add_job(
            _warmup_job,
            IntervalTrigger(minutes=app_config.cache_warmup_interval_minutes),
        )
        logger.info(
            "[Scheduler] Registrado job: warmup_cache cada %d minutos",
            app_config.cache_warmup_interval_minutes,
        )

        if parallel_engine:
            scheduler.add_job(
                _parallel_analysis_job,
                IntervalTrigger(minutes=10),  # Every 10 minutes
                id="parallel_analysis_batch",
                replace_existing=True,
            )
            logger.info("[Scheduler] Registrado job: parallel_analysis cada 10 minutos")

        scheduler.start()
        logger.info("[Scheduler] AsyncIOScheduler iniciado (runs in event loop)")
    except Exception as exc:
        logger.exception("Error configurando scheduler: %s", exc)
        raise
