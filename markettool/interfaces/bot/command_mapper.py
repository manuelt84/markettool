"""Bot command mapper - maps Telegram commands to use cases."""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Any
from dataclasses import dataclass

from markettool.interfaces.containers import DIContainer


@dataclass
class CommandContext:
    """Context for command execution."""
    
    command: str
    user_id: str
    chat_id: str
    args: list
    kwargs: Dict[str, Any]
    container: DIContainer


class CommandMapper:
    """Maps Telegram bot commands to use case executions."""
    
    def __init__(self, container: DIContainer, logger: Optional[logging.Logger] = None):
        """
        Initialize command mapper.
        
        Args:
            container: DI container with use cases
            logger: Optional logger
        """
        self.container = container
        self.logger = logger or logging.getLogger(__name__)
        self.commands: Dict[str, Callable] = {}
        
        # Register default commands
        self._register_default_commands()
    
    def _register_default_commands(self):
        """Register default bot commands mapping to use cases."""
        # Historical data commands
        self.register('/historicos', self._handle_get_historicos)
        self.register('/hist', self._handle_get_historicos)
        
        # Quote commands
        self.register('/quote', self._handle_get_quote)
        self.register('/precio', self._handle_get_quote)
        
        # Analysis commands
        self.register('/analisis', self._handle_run_analysis)
        self.register('/analyze', self._handle_run_analysis)
        
        # Cache commands
        self.register('/calentar', self._handle_warm_cache)
        self.register('/warmup', self._handle_warm_cache)
        
        # System commands
        self.register('/ayuda', self._handle_help)
        self.register('/help', self._handle_help)
        self.register('/estado', self._handle_status)
    
    def register(self, command: str, handler: Callable) -> None:
        """
        Register a command handler.
        
        Args:
            command: Command name (e.g., '/historicos')
            handler: Handler function
        """
        self.commands[command.lower()] = handler
        self.logger.debug(f"Registered command: {command}")
    
    async def execute(self, context: CommandContext) -> str:
        """
        Execute a command.
        
        Args:
            context: Command context with command name and args
            
        Returns:
            Response message
        """
        command_lower = context.command.lower()
        
        if command_lower not in self.commands:
            return f"❌ Command not recognized: {context.command}\nTry /ayuda for help"
        
        try:
            handler = self.commands[command_lower]
            response = await handler(context)
            return response
        except Exception as e:
            self.logger.error(f"Error executing {context.command}: {e}", exc_info=True)
            return f"⚠️ Error: {str(e)}"
    
    # ==================== COMMAND HANDLERS ====================
    
    async def _handle_get_historicos(self, ctx: CommandContext) -> str:
        """Handle /historicos command."""
        if not ctx.args:
            return "Usage: /historicos SYMBOL [TIMEFRAME]\nExample: /historicos AAPL 1d"
        
        symbol = ctx.args[0].upper()
        timeframe = ctx.args[1] if len(ctx.args) > 1 else '1d'
        
        try:
            use_case = self.container.get_historicos
            historico = await use_case.execute(symbol=symbol, timeframe=timeframe)
            
            last_candle = historico.last_candle()
            response = (
                f"📊 <b>{symbol}</b> - {timeframe}\n"
                f"📈 Close: ${last_candle['close']:.4f}\n"
                f"📊 Volume: {last_candle['volume']:,.0f}\n"
                f"✅ Data updated"
            )
            return response
        
        except Exception as e:
            return f"❌ Error fetching historicos: {e}"
    
    async def _handle_get_quote(self, ctx: CommandContext) -> str:
        """Handle /quote command."""
        if not ctx.args:
            return "Usage: /quote SYMBOL\nExample: /quote AAPL"
        
        symbol = ctx.args[0].upper()
        
        try:
            use_case = self.container.get_quote
            quote = await use_case.execute(symbol=symbol)
            
            change_pct = f"{quote.change_pct:.2%}" if quote.change_pct else "N/A"
            response = (
                f"💹 <b>{quote.symbol}</b>\n"
                f"💰 Price: <code>${quote.price:.4f}</code>\n"
                f"📈 Change: {change_pct}\n"
                f"🕐 {quote.timestamp.strftime('%H:%M:%S UTC')}"
            )
            
            if quote.bid and quote.ask:
                spread = quote.ask - quote.bid
                response += f"\n📊 Bid/Ask: {quote.bid:.4f} / {quote.ask:.4f} (spread: {spread:.4f})"
            
            return response
        
        except Exception as e:
            return f"❌ Error fetching quote: {e}"
    
    async def _handle_run_analysis(self, ctx: CommandContext) -> str:
        """Handle /analisis command."""
        if not ctx.args:
            return "Usage: /analisis SYMBOL [TIMEFRAME]\nExample: /analisis AAPL"
        
        symbol = ctx.args[0].upper()
        timeframe = ctx.args[1] if len(ctx.args) > 1 else '1d'
        
        try:
            use_case = self.container.run_analysis
            result = await use_case.execute(symbol=symbol, timeframe=timeframe)
            
            signals = result.get('signals', [])
            signal_count = len(signals)
            
            response = f"📈 <b>Analysis for {symbol} ({timeframe})</b>\n"
            response += f"🎯 Signals found: {signal_count}\n"
            
            if signals:
                top_signal = signals[0]
                response += f"🔝 Top Signal: {top_signal.signal_type.value}\n"
                response += f"💪 Confidence: {top_signal.confidence:.1%}"
            else:
                response += "No signals at this time"
            
            return response
        
        except Exception as e:
            return f"❌ Error running analysis: {e}"
    
    async def _handle_warm_cache(self, ctx: CommandContext) -> str:
        """Handle /calentar (warmup cache) command."""
        symbols = ctx.args if ctx.args else ['AAPL', 'GOOGL', 'MSFT']
        
        try:
            use_case = self.container.warm_cache
            result = await use_case.execute(symbols=symbols)
            
            warmed = result.get('symbols_warmed', 0)
            duration = result.get('duration_seconds', 0)
            
            response = (
                f"📦 <b>Cache Warmup Complete</b>\n"
                f"✅ Symbols warmed: {warmed}\n"
                f"⏱️ Duration: {duration:.2f}s"
            )
            return response
        
        except Exception as e:
            return f"❌ Error warming cache: {e}"
    
    async def _handle_help(self, ctx: CommandContext) -> str:
        """Handle /ayuda (help) command."""
        help_text = (
            "<b>📚 Available Commands</b>\n\n"
            "/historicos SYMBOL - Get historical data\n"
            "/quote SYMBOL - Get current quote\n"
            "/analisis SYMBOL - Run market analysis\n"
            "/calentar [SYMBOLS] - Warm cache\n"
            "/estado - System status\n"
            "/ayuda - This help message"
        )
        return help_text
    
    async def _handle_status(self, ctx: CommandContext) -> str:
        """Handle /estado (status) command."""
        try:
            # Check if providers are available
            quote_uc = self.container.get_quote
            available = True
        except Exception:
            available = False
        
        status = (
            "<b>🟢 System Status</b>\n"
            f"Status: {'🟢 Online' if available else '🔴 Offline'}\n"
            "Quote Service: ✅\n"
            "Cache Service: ✅"
        )
        return status


async def process_telegram_message(
    message_text: str,
    user_id: str,
    chat_id: str,
    container: DIContainer,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Process a Telegram message and return response.
    
    Args:
        message_text: Raw message from user
        user_id: Telegram user ID
        chat_id: Telegram chat ID
        container: DI container
        logger: Optional logger
        
    Returns:
        Response message
    """
    mapper = CommandMapper(container, logger)
    
    # Parse command and args
    parts = message_text.strip().split()
    if not parts:
        return "❌ Empty message"
    
    command = parts[0]
    args = parts[1:]
    
    context = CommandContext(
        command=command,
        user_id=user_id,
        chat_id=chat_id,
        args=args,
        kwargs={},
        container=container,
    )
    
    return await mapper.execute(context)
