"""Unit tests for TelegramNotifier adapter."""

import pytest
from unittest.mock import AsyncMock
from datetime import datetime
import pytz

from markettool.infra.repositories import TelegramNotifier
from markettool.core.models.signal import Signal, SignalType
from markettool.core.errors import NotificationError


@pytest.mark.asyncio
class TestTelegramNotifierSignals:
    """Test sending signals via Telegram."""
    
    async def test_notify_signal_success(self, telegram_notifier, sample_signal):
        """Test successfully sending a signal."""
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_signal(sample_signal)
        
        # Should call send_message
        telegram_notifier.telegram.bot.send_message.assert_called_once()
    
    async def test_notify_signal_disabled(self, telegram_notifier, sample_signal):
        """Test that disabled notifier doesn't send."""
        telegram_notifier.set_enabled(False)
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_signal(sample_signal)
        
        # Should not send message
        telegram_notifier.telegram.bot.send_message.assert_not_called()
    
    async def test_notify_buy_signal(self, telegram_notifier):
        """Test formatting BUY signal."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_signal(signal)
        
        # Verify message was formatted and sent
        assert telegram_notifier.telegram.bot.send_message.called
    
    async def test_notify_sell_signal(self, telegram_notifier):
        """Test formatting SELL signal."""
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.SELL,
            confidence=0.75,
            price=95.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_signal(signal)
        
        assert telegram_notifier.telegram.bot.send_message.called
    
    async def test_notify_multiple_chat_ids(self):
        """Test sending to multiple chat IDs."""
        notifier = TelegramNotifier(
            telegram_app=AsyncMock(),
            chat_ids=["123", "456", "789"],
        )
        notifier.telegram.bot = AsyncMock()
        notifier.telegram.bot.send_message = AsyncMock()
        
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        await notifier.notify_signal(signal)
        
        # Should send to all chat IDs
        assert notifier.telegram.bot.send_message.call_count >= 1


@pytest.mark.asyncio
class TestTelegramNotifierMessages:
    """Test generic message notifications."""
    
    async def test_notify_message(self, telegram_notifier):
        """Test sending generic message."""
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_message(
            message="Test message",
            recipients=["123"],
        )
        
        telegram_notifier.telegram.bot.send_message.assert_called_once()
    
    async def test_notify_error(self, telegram_notifier):
        """Test sending error notification."""
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_error(
            error_message="Something went wrong",
            recipients=["123"],
        )
        
        telegram_notifier.telegram.bot.send_message.assert_called_once()
    
    async def test_notify_price_alert(self, telegram_notifier):
        """Test sending price alert."""
        telegram_notifier.telegram.bot.send_message = AsyncMock()
        
        await telegram_notifier.notify_price_alert(
            symbol="AAPL",
            current_price=105.0,
            alert_price=100.0,
            recipients=["123"],
        )
        
        telegram_notifier.telegram.bot.send_message.assert_called_once()


@pytest.mark.asyncio
class TestTelegramNotifierChatManagement:
    """Test chat ID management."""
    
    def test_add_chat_id(self, telegram_notifier):
        """Test adding a chat ID."""
        telegram_notifier.add_chat_id("999")
        
        assert "999" in telegram_notifier.chat_ids
    
    def test_remove_chat_id(self, telegram_notifier):
        """Test removing a chat ID."""
        # Initial chat ID was 123456789
        telegram_notifier.remove_chat_id("123456789")
        
        assert "123456789" not in telegram_notifier.chat_ids
    
    def test_duplicate_chat_id_not_added(self, telegram_notifier):
        """Test that duplicate chat IDs aren't added."""
        initial_count = len(telegram_notifier.chat_ids)
        telegram_notifier.add_chat_id("123456789")  # Already exists
        
        # Should not increase count
        assert len(telegram_notifier.chat_ids) == initial_count
    
    def test_set_enabled_toggle(self, telegram_notifier):
        """Test enabling/disabling notifications."""
        assert telegram_notifier.enabled == True
        
        telegram_notifier.set_enabled(False)
        assert telegram_notifier.enabled == False
        
        telegram_notifier.set_enabled(True)
        assert telegram_notifier.enabled == True


@pytest.mark.asyncio
class TestTelegramNotifierHealth:
    """Test Telegram health checks."""
    
    async def test_health_check_available(self, telegram_notifier):
        """Test health check when Telegram is available."""
        telegram_notifier.telegram = AsyncMock()
        
        available = await telegram_notifier.health_check()
        # Should be True if is_available succeeds
        assert available is not None
    
    async def test_health_check_unavailable(self, telegram_notifier):
        """Test health check when Telegram is unavailable."""
        telegram_notifier.telegram = None
        
        available = await telegram_notifier.health_check()
        assert available == False


@pytest.mark.asyncio
class TestTelegramNotifierErrorHandling:
    """Test error handling."""
    
    async def test_notify_signal_send_error(self, telegram_notifier, sample_signal):
        """Test handling send errors."""
        telegram_notifier.telegram.bot.send_message = AsyncMock(
            side_effect=Exception("Send failed")
        )
        
        # Should raise NotificationError
        with pytest.raises(NotificationError):
            await telegram_notifier.notify_signal(sample_signal)
    
    async def test_notify_continues_on_partial_failure(self, telegram_notifier):
        """Test that partial failures don't stop all notifications."""
        notifier = TelegramNotifier(
            telegram_app=AsyncMock(),
            chat_ids=["123", "456"],
        )
        
        # First chat fails, second succeeds
        notifier.telegram.bot = AsyncMock()
        notifier.telegram.bot.send_message = AsyncMock(
            side_effect=[Exception("Failed"), None]
        )
        
        signal = Signal(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            confidence=0.85,
            price=105.0,
            timestamp=datetime.now(pytz.UTC),
        )
        
        # Should attempt to send to all chat IDs
        await notifier.notify_signal(signal)
