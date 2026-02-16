"""Telegram notification client."""

from __future__ import annotations

import logging
from typing import List, Optional

from markettool.core.models.signal import Signal
from markettool.core.errors import NotificationError


class TelegramClient:
    """
    Telegram bot client for sending notifications.
    Wraps python-telegram-bot with domain-aware message formatting.
    """
    
    def __init__(
        self,
        token: str,
        application = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize Telegram client.
        
        Args:
            token: Bot token from @BotFather
            application: Telegram Application instance (optional, for API use)
            logger: Optional logger
        """
        self.token = token
        self.application = application
        self.logger = logger or logging.getLogger(__name__)
    
    async def send_message(
        self,
        chat_id: int | str,
        message: str,
        parse_mode: str = "HTML",
    ) -> Optional[dict]:
        """
        Send text message to chat.
        
        Args:
            chat_id: Telegram chat ID
            message: Message text
            parse_mode: Message format (HTML, Markdown, MarkdownV2, default)
            
        Returns:
            Message response or None if failed
        """
        try:
            if self.application:
                msg = await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=parse_mode,
                )
                return {"message_id": msg.message_id, "status": "ok"}
            else:
                self.logger.error("No Telegram application configured")
                return None
        except Exception as e:
            raise NotificationError(f"Failed to send message: {e}")
    
    async def notify_signal(
        self,
        signal: Signal,
        chat_id: int | str,
    ) -> None:
        """
        Send signal notification in user-friendly format.
        
        Args:
            signal: Trading signal to notify
            chat_id: Telegram chat ID
        """
        msg = self._format_signal_message(signal)
        await self.send_message(chat_id, msg)
    
    async def notify_message(
        self,
        message: str,
        recipients: List[int | str],
    ) -> None:
        """Send message to multiple recipients."""
        for chat_id in recipients:
            try:
                await self.send_message(chat_id, message)
            except Exception as e:
                self.logger.error(f"Failed to send to {chat_id}: {e}")
    
    async def notify_error(
        self,
        error_message: str,
        recipients: List[int | str],
    ) -> None:
        """Send error notification."""
        msg = f"⚠️ <b>Error</b>\n{error_message}"
        await self.notify_message(msg, recipients)
    
    async def notify_price_alert(
        self,
        symbol: str,
        current_price: float,
        alert_price: float,
        recipients: List[int | str],
    ) -> None:
        """Send price alert notification."""
        direction = "↑" if current_price > alert_price else "↓"
        msg = (
            f"🚨 <b>Price Alert</b> {direction}\n"
            f"<b>{symbol}</b>\n"
            f"Current: {current_price}\n"
            f"Alert: {alert_price}"
        )
        await self.notify_message(msg, recipients)
    
    async def is_available(self) -> bool:
        """Check if bot can connect."""
        try:
            if self.application:
                me = await self.application.bot.get_me()
                return me is not None
        except Exception as e:
            self.logger.error(f"Bot availability check failed: {e}")
        return False
    
    async def health_check(self) -> dict:
        """Get bot health status."""
        try:
            if self.application:
                me = await self.application.bot.get_me()
                return {
                    "status": "ok",
                    "bot_name": me.username if me else None,
                }
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    @staticmethod
    def _format_signal_message(signal: Signal) -> str:
        """Format signal as message."""
        emoji = "📈" if signal.is_bullish else "📉"
        return (
            f"{emoji} <b>{signal.signal_type.value}</b> {signal.symbol}\n"
            f"Confidence: {signal.confidence:.0%}\n"
            f"Reason: {signal.reason or 'N/A'}"
        )
