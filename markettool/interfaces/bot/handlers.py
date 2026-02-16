"""Bot message handlers and commands registration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application


def register_bot_handlers(application: Application, logger: logging.Logger) -> None:
    """
    Register all Telegram bot command and message handlers.
    Dynamically imports handler functions from MarketTool module.
    """
    try:
        # Import handlers from MarketTool
        from MarketTool import (
            start,
            stop,
            trader_menu,
            menu,
            seleccionar_par,
            manejar_respuesta_fechas,
            # manejar_button_callback,  # ❌ LEGACY: Function not found in MarketTool.py - will use volver_al_menu fallback
            ia_grafico,
            analizar_simbolo,
            eventos_futuros,
            noticias_user,
            noticias_admin,
            noticias_general,
            verificar_suscripcion,
            agregar_suscripcion,
            eliminar_suscripcion,
            listar_suscripciones,
            menu_zonas_horarias,
            descargar_manual,
            comando_reset_menu,
            set_timezone,
            enviar_mensaje,
            cancelar_suscripcion,
            cancelar_zonas_horarias,
            cancelar_envio_mensaje,
            confirmar_envio,
            recibir_usuario_especifico,
            volver_al_menu,
        )
        
        from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, Filters
        
        # --- Command handlers ---
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop))
        application.add_handler(CommandHandler("trader_menu", trader_menu))
        application.add_handler(CommandHandler("ia_grafico", ia_grafico))
        application.add_handler(CommandHandler("analizar_simbolo", analizar_simbolo))
        application.add_handler(CommandHandler("eventos_futuros", eventos_futuros))
        application.add_handler(CommandHandler("noticias_user", noticias_user))
        application.add_handler(CommandHandler("noticias_admin", noticias_admin))
        application.add_handler(CommandHandler("noticias_general", noticias_general))
        application.add_handler(CommandHandler("verificar_suscripcion", verificar_suscripcion))
        application.add_handler(CommandHandler("agregar_suscripcion", agregar_suscripcion))
        application.add_handler(CommandHandler("eliminar_suscripcion", eliminar_suscripcion))
        application.add_handler(CommandHandler("listar_suscripciones", listar_suscripciones))
        application.add_handler(CommandHandler("set_timezone", set_timezone))
        application.add_handler(CommandHandler("descargar_manual", descargar_manual))
        application.add_handler(CommandHandler("menu_reset", comando_reset_menu))
        application.add_handler(CommandHandler("enviar_mensaje", enviar_mensaje))

        # --- Message handlers ---
        application.add_handler(
            MessageHandler(Filters.TEXT & ~Filters.COMMAND, manejar_respuesta_fechas)
        )

        # --- Callback query handlers ---
        # manejar_button_callback is LEGACY and not found - fallback to volver_al_menu
        application.add_handler(
            CallbackQueryHandler(volver_al_menu, pattern=r".*_par_.*")
        )
        application.add_handler(
            CallbackQueryHandler(volver_al_menu, pattern=r".*_menu_.*")
        )
        application.add_handler(
            CallbackQueryHandler(set_timezone, pattern=r".*_timezone_.*")
        )
        application.add_handler(
            CallbackQueryHandler(cancelar_suscripcion, pattern=r".*_suscripciones_cancelar")
        )
        application.add_handler(
            CallbackQueryHandler(cancelar_zonas_horarias, pattern=r".*_zonas_horarias_cancelar")
        )
        application.add_handler(
            CallbackQueryHandler(cancelar_envio_mensaje, pattern=r"cancelar_envio_mensaje")
        )
        application.add_handler(
            CallbackQueryHandler(confirmar_envio, pattern=r"confirmar_envio")
        )
        application.add_handler(
            CallbackQueryHandler(volver_al_menu, pattern=r".*_volver.*")
        )

        logger.info("[Bot] Handlers registrados exitosamente")

    except Exception as exc:
        logger.exception("Error registrando handlers del bot: %s", exc)
        raise
