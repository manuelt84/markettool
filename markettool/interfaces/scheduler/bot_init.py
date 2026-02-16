"""Bot initialization and scheduler setup."""

from __future__ import annotations

import asyncio
import logging
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
                await application.bot.set_webhook(full_webhook_url)
                logger.info("Webhook configurado correctamente!")
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
        """Async job: Parallel analysis engine (Nivel 3: máximo paralelismo)."""
        if not parallel_engine:
            return
        if not pod_coordinator.should_run_scheduled_task("parallel_analysis"):
            return
        
        try:
            from MarketTool import (
                load_cached_history,  # Función para cargar histórico
                cargar_activos_en_mercado,  # Activos disponibles
                calcular_entradas,  # Entry calculation
                guardar_seniales_a_firebase,  # Persist signals
            )
            
            logger.info("[Parallel Analysis] Starting parallel analysis batch...")
            symbols = await cargar_activos_en_mercado() if hasattr(cargar_activos_en_mercado, '__call__') else []
            
            if not symbols:
                logger.warning("[Parallel Analysis] No symbols to analyze")
                return
            
            # TFF ordenados por coste (mayor primero)
            tfs = parallel_engine.config.ordered_tfs
            
            # Run analysis
            results = await parallel_engine.analyze_assets_parallel(
                symbols=symbols,
                tfs=tfs,
                load_history_fn=load_cached_history,
                analyze_asset_fn=calcular_entradas,
                on_progress=lambda cur, total: logger.info(
                    "[Parallel Analysis] Progress: %d/%d", cur, total
                ),
            )
            
            logger.info("[Parallel Analysis] Batch complete: %d results", len(results))
            
            # Persist signals
            if results:
                await guardar_seniales_a_firebase(results)
                logger.info("[Parallel Analysis] Signals persisted to Firestore")
                
        except Exception as exc:
            logger.exception("[Parallel Analysis] Error in parallel analysis job: %s", exc)

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
