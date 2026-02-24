"""Telegram notifier adapter."""

from __future__ import annotations

import logging
from typing import List, Optional

from markettool.core.models.signal import Signal, SignalType
from markettool.core.ports.notifier import Notifier
from markettool.core.errors import NotificationError


class TelegramNotifier(Notifier):
    """
    Notifier that sends trading signals via Telegram.
    Acts as adapter between domain and Telegram infrastructure.
    """
    
    def __init__(
        self,
        telegram_app,
        chat_id: Optional[str] = None,
        chat_ids: Optional[List[str]] = None,
        enabled: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize Telegram notifier.
        
        Args:
            telegram_app: Telegram Application instance
            chat_id: Default chat ID for notifications
            chat_ids: List of chat IDs to notify
            enabled: Enable/disable notifications
            logger: Optional logger
        """
        self.telegram = telegram_app
        self.enabled = enabled
        self.logger = logger or logging.getLogger(__name__)
        
        # Build chat IDs list
        self.chat_ids: List[str] = []
        if chat_id:
            self.chat_ids.append(chat_id)
        if chat_ids:
            self.chat_ids.extend(chat_ids)
        
        if not self.chat_ids:
            self.logger.warning("No chat IDs configured for Telegram notifier")
    
    async def notify_signal(self, signal: Signal) -> None:
        """
        Send signal notification to configured chat IDs.
        
        Args:
            signal: Trading signal to notify
        """
        if not self.enabled:
            self.logger.debug("Telegram notifications disabled")
            return
        
        if not self.chat_ids:
            self.logger.warning("No chat IDs configured")
            return
        
        try:
            message = self._format_signal_message(signal)
            
            for chat_id in self.chat_ids:
                try:
                    await self.telegram.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                    self.logger.info(f"Signal notification sent to {chat_id}")
                except Exception as e:
                    self.logger.error(f"Failed to send message to {chat_id}: {e}")
        
        except Exception as e:
            self.logger.error(f"Failed to notify signal: {e}")
            raise NotificationError(f"Telegram notification failed: {e}")
    
    async def notify_analysis_complete(
        self,
        symbol: str,
        analysis_result: dict,
    ) -> None:
        """
        Notify that analysis completed for a symbol.
        
        Args:
            symbol: Trading symbol analyzed
            analysis_result: Analysis results
        """
        if not self.enabled or not self.chat_ids:
            return
        
        try:
            message = self._format_analysis_message(symbol, analysis_result)
            
            for chat_id in self.chat_ids:
                try:
                    await self.telegram.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to notify analysis for {chat_id}: {e}")
        
        except Exception as e:
            self.logger.error(f"Failed to notify analysis: {e}")
    
    async def notify_cache_warmed(
        self,
        symbols: List[str],
        duration_seconds: float,
    ) -> None:
        """
        Notify that cache was warmed.
        
        Args:
            symbols: Symbols that were cached
            duration_seconds: Time taken to warm
        """
        if not self.enabled or not self.chat_ids:
            return
        
        try:
            message = (
                "<b>📦 Cache Warmed</b>\n"
                f"🔢 Symbols: {len(symbols)}\n"
                f"⏱️ Duration: {duration_seconds:.2f}s\n"
                f"📊 Symbols: {', '.join(symbols[:10])}"
                f"{f'... +{len(symbols)-10} more' if len(symbols) > 10 else ''}"
            )
            
            for chat_id in self.chat_ids:
                try:
                    await self.telegram.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to notify cache warm: {e}")
        
        except Exception as e:
            self.logger.error(f"Failed to notify cache warm: {e}")
    
    async def notify_message(self, message: str, recipients: List[str]) -> None:
        """
        Send generic message to recipients.
        
        Args:
            message: Message to send
            recipients: List of recipient IDs
        """
        if not self.enabled:
            return
        
        chat_ids = recipients or self.chat_ids
        for chat_id in chat_ids:
            try:
                await self.telegram.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as e:
                self.logger.warning(f"Failed to send message to {chat_id}: {e}")
    
    async def notify_error(
        self,
        error_message: str,
        recipients: List[str],
    ) -> None:
        """
        Send error notification to recipients.
        
        Args:
            error_message: Error details
            recipients: List of recipient IDs
        """
        if not self.enabled:
            return
        
        message = (
            f"<b>⚠️ Error Notification</b>\n"
            f"<code>{error_message}</code>"
        )
        await self.notify_message(message, recipients)
    
    async def notify_price_alert(
        self,
        symbol: str,
        current_price: float,
        alert_price: float,
        recipients: List[str],
    ) -> None:
        """
        Send price alert notification.
        
        Args:
            symbol: Trading symbol
            current_price: Current market price
            alert_price: Alert threshold
            recipients: List of recipient IDs
        """
        if not self.enabled:
            return
        
        direction = "📈" if current_price > alert_price else "📉"
        message = (
            f"<b>{direction} Price Alert</b>\n"
            f"📊 Symbol: <code>{symbol}</code>\n"
            f"💵 Current: ${current_price:.4f}\n"
            f"🎯 Alert: ${alert_price:.4f}"
        )
        await self.notify_message(message, recipients)
    
    async def health_check(self) -> bool:
        """Check if Telegram connection is healthy."""
        try:
            return await self.is_available()
        except Exception:
            return False
    
    def _format_signal_message(self, signal: Signal) -> str:
        """Format trading signal as Telegram message."""
        emoji = self._get_signal_emoji(signal.signal_type)
        
        message = (
            f"<b>{emoji} Trading Signal</b>\n"
            f"📊 Symbol: <code>{signal.symbol}</code>\n"
            f"📈 Type: {signal.signal_type.value}\n"
            f"💪 Confidence: {signal.confidence:.1%}\n"
        )
        
        if signal.entry_price:
            message += f"🎯 Entry: ${signal.entry_price:.4f}\n"
        
        if signal.target_price:
            message += f"🚀 Target: ${signal.target_price:.4f}\n"
        
        if signal.indicators:
            message += "\n"
            for key, value in list(signal.indicators.items())[:5]:
                message += f"• {key}: {value}\n"
        
        return message
    
    def _format_analysis_message(self, symbol: str, result: dict) -> str:
        """Format analysis result as Telegram message."""
        message = (
            f"<b>📊 Analysis Complete</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
        )
        
        if "signal_count" in result:
            message += f"Signals: {result['signal_count']}\n"
        
        if "indicators" in result:
            message += f"Indicators: {len(result['indicators'])}\n"
        
        return message
    
    def _get_signal_emoji(self, signal_type: SignalType) -> str:
        """Get emoji for signal type."""
        if signal_type == SignalType.BUY:
            return "🟢"
        elif signal_type == SignalType.SELL:
            return "🔴"
        elif signal_type == SignalType.HOLD:
            return "⚪"
        else:
            return "❓"
    
    async def is_available(self) -> bool:
        """Check if Telegram bot is available."""
        try:
            if not self.telegram:
                return False
            # Could implement actual availability check
            return True
        except Exception:
            return False
    
    def add_chat_id(self, chat_id: str) -> None:
        """Add a chat ID to notify list."""
        if chat_id not in self.chat_ids:
            self.chat_ids.append(chat_id)
            self.logger.info(f"Added chat ID: {chat_id}")
    
    def remove_chat_id(self, chat_id: str) -> None:
        """Remove a chat ID from notify list."""
        if chat_id in self.chat_ids:
            self.chat_ids.remove(chat_id)
            self.logger.info(f"Removed chat ID: {chat_id}")
    
    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable notifications."""
        self.enabled = enabled
        self.logger.info(f"Notifications {'enabled' if enabled else 'disabled'}")
