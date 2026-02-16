"""Port for notifications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models.signal import Signal


class Notifier(ABC):
    """
    Port for sending notifications.
    Implementations can use email, Telegram, webhooks, etc.
    """
    
    @abstractmethod
    async def notify_signal(self, signal: Signal, recipients: List[str]) -> None:
        """
        Send signal notification to recipients.
        
        Args:
            signal: Trading signal to notify about
            recipients: List of recipient IDs/addresses
        """
        pass
    
    @abstractmethod
    async def notify_message(self, message: str, recipients: List[str]) -> None:
        """
        Send generic message to recipients.
        
        Args:
            message: Message to send
            recipients: List of recipient IDs/addresses
        """
        pass
    
    @abstractmethod
    async def notify_error(self, error_message: str, recipients: List[str]) -> None:
        """
        Send error notification to recipients.
        
        Args:
            error_message: Error details
            recipients: List of recipient IDs/addresses
        """
        pass
    
    @abstractmethod
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
            current_price: Current price
            alert_price: Price that triggered alert
            recipients: List of recipient IDs/addresses
        """
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if notification service is available."""
        pass
    
    @abstractmethod
    async def health_check(self) -> dict:
        """Get health status of notification service."""
        pass
