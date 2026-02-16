"""Telegram bot command handlers integrated with hexagonal architecture."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from markettool.interfaces.bot.command_mapper import process_telegram_message

if TYPE_CHECKING:
    from markettool.interfaces.containers import DIContainer


logger = logging.getLogger(__name__)


async def create_bot_handlers(container: DIContainer) -> dict:
    """
    Create bot handlers that use the hexagonal architecture.
    
    Args:
        container: DI container with use cases
        
    Returns:
        Dictionary of handler functions for various commands
    """
    
    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        message = (
            "👋 <b>Welcome to MarketTool Bot</b>\n\n"
            "This bot provides real-time market analysis and trading signals.\n\n"
            "<b>Hexagonal Commands:</b>\n"
            "• /historicos [SYMBOL] [TIMEFRAME] - Get historical data\n"
            "• /quote [SYMBOL] - Get current price quote\n"
            "• /analisis [SYMBOL] - Run technical analysis\n"
            "• /calentar [SYMBOLS...] - Warm cache for symbols\n"
            "• /ayuda_hex - Show hexagonal help\n"
            "• /estado_hex - System status\n\n"
            "Type /ayuda_hex for detailed command info."
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"User {user_id} started bot (hexagonal)")
    
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle all text messages - map to use cases."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        message_text = update.message.text
        
        if not message_text:
            return
        
        try:
            # Show typing indicator
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            # Process message through command mapper
            response = await process_telegram_message(
                message_text=message_text,
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            # Send response
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
            
            logger.debug(f"User {user_id}: {message_text[:50]}")
        
        except Exception as e:
            logger.error(f"Error handling message from {user_id}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ An error occurred while processing your request. Please try again.",
            )
    
    async def handle_historicos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /historicos command - get historical OHLCV data."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            # Build message with args
            args_str = " ".join(context.args) if context.args else ""
            message_text = f"/historicos {args_str}".strip()
            
            response = await process_telegram_message(
                message_text=message_text,
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /historicos: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error retrieving historical data",
            )
    
    async def handle_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /quote command - get current price quote."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            args_str = " ".join(context.args) if context.args else ""
            message_text = f"/quote {args_str}".strip()
            
            response = await process_telegram_message(
                message_text=message_text,
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /quote: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error retrieving quote",
            )
    
    async def handle_analisis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /analisis command - run technical analysis."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            args_str = " ".join(context.args) if context.args else ""
            message_text = f"/analisis {args_str}".strip()
            
            response = await process_telegram_message(
                message_text=message_text,
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /analisis: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error running analysis",
            )
    
    async def handle_calentar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /calentar command - warm cache for symbols."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            args_str = " ".join(context.args) if context.args else ""
            message_text = f"/calentar {args_str}".strip()
            
            response = await process_telegram_message(
                message_text=message_text,
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /calentar: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error warming cache",
            )
    
    async def handle_ayuda_hex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ayuda_hex command - show hexagonal help."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            response = await process_telegram_message(
                message_text="/ayuda",
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /ayuda_hex: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error showing help",
            )
    
    async def handle_estado_hex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /estado_hex command - show system status."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            
            response = await process_telegram_message(
                message_text="/estado",
                user_id=str(user_id),
                chat_id=str(chat_id),
                container=container,
                logger=logger,
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=response,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error in /estado_hex: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Error retrieving status",
            )
    
    async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors caused by updates."""
        logger.error(f"Update {update} caused error {context.error}")
    
    return {
        "start": handle_start,
        "message": handle_message,
        "historicos": handle_historicos,
        "quote": handle_quote,
        "analisis": handle_analisis,
        "calentar": handle_calentar,
        "ayuda_hex": handle_ayuda_hex,
        "estado_hex": handle_estado_hex,
        "error": handle_error,
    }


async def register_handlers_with_app(
    application,
    container: DIContainer,
    *,
    mode: str = "mixed",
) -> None:
    """
    Register bot handlers with telegram.ext Application.
    
    Args:
        application: telegram.ext Application instance
        container: DI container with use cases
        mode: Registration mode:
            - "mixed" (default): Register hexagonal commands alongside legacy handlers
            - "full": Register all handlers including /start and message handler
            - "commands_only": Register only specific hexagonal commands
    """
    from telegram.ext import CommandHandler, MessageHandler, filters
    
    handlers = await create_bot_handlers(container)
    
    if mode == "full":
        # Full hexagonal mode - register all handlers (replaces legacy)
        application.add_handler(CommandHandler("start", handlers["start"]))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["message"])
        )
        application.add_handler(CommandHandler("historicos", handlers["historicos"]))
        application.add_handler(CommandHandler("quote", handlers["quote"]))
        application.add_handler(CommandHandler("analisis", handlers["analisis"]))
        application.add_handler(CommandHandler("calentar", handlers["calentar"]))
        application.add_handler(CommandHandler("ayuda_hex", handlers["ayuda_hex"]))
        application.add_handler(CommandHandler("estado_hex", handlers["estado_hex"]))
        logger.info("[Hexagonal] FULL mode - all handlers registered")
        
    elif mode == "commands_only":
        # Only register hexagonal commands (no /start, no message handler)
        application.add_handler(CommandHandler("historicos", handlers["historicos"]))
        application.add_handler(CommandHandler("quote", handlers["quote"]))
        application.add_handler(CommandHandler("analisis", handlers["analisis"]))
        application.add_handler(CommandHandler("calentar", handlers["calentar"]))
        application.add_handler(CommandHandler("ayuda_hex", handlers["ayuda_hex"]))
        application.add_handler(CommandHandler("estado_hex", handlers["estado_hex"]))
        logger.info("[Hexagonal] COMMANDS_ONLY mode - specific commands registered")
        
    else:  # mixed mode (default)
        # Register hexagonal commands alongside legacy handlers
        # Use _hex suffix to avoid collisions with potential legacy commands
        application.add_handler(CommandHandler("historicos", handlers["historicos"]))
        application.add_handler(CommandHandler("quote", handlers["quote"]))
        application.add_handler(CommandHandler("analisis", handlers["analisis"]))
        application.add_handler(CommandHandler("calentar", handlers["calentar"]))
        application.add_handler(CommandHandler("ayuda_hex", handlers["ayuda_hex"]))
        application.add_handler(CommandHandler("estado_hex", handlers["estado_hex"]))
        logger.info("[Hexagonal] MIXED mode - hexagonal commands coexist with legacy")
    
    # Always register error handler
    application.add_error_handler(handlers["error"])
    
    logger.info("[Hexagonal] Bot handlers registered successfully")
