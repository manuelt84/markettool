"""Graceful shutdown handler for production deployments."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class GracefulShutdownHandler:
    """
    Handles graceful shutdown on SIGTERM/SIGINT.
    
    Ensures:
    - Active requests complete
    - Resources are cleaned up
    - Services are stopped properly
    """
    
    def __init__(self, shutdown_timeout: int = 30) -> None:
        """
        Initialize shutdown handler.
        
        Args:
            shutdown_timeout: Maximum seconds to wait for graceful shutdown
        """
        self._shutdown_timeout = shutdown_timeout
        self._shutdown_callbacks: List[Callable] = []
        self._is_shutting_down = False
        self._shutdown_start_time: Optional[float] = None
    
    def register_shutdown_callback(self, callback: Callable) -> None:
        """
        Register a callback to be called during shutdown.
        
        Callbacks will be called in reverse order (LIFO).
        
        Args:
            callback: Function to call during shutdown (can be sync or async)
        """
        self._shutdown_callbacks.append(callback)
        logger.debug(f"Registered shutdown callback: {callback.__name__}")
    
    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._is_shutting_down
    
    async def _execute_shutdown_callbacks(self) -> None:
        """Execute all registered shutdown callbacks in reverse order."""
        logger.info("Executing shutdown callbacks...")
        
        # Execute in reverse order (LIFO)
        for callback in reversed(self._shutdown_callbacks):
            try:
                callback_name = getattr(callback, '__name__', str(callback))
                logger.info(f"Calling shutdown callback: {callback_name}")
                
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
                
                logger.info(f"✅ Completed: {callback_name}")
            except Exception as exc:
                logger.exception(f"Error in shutdown callback {callback_name}: {exc}")
    
    async def shutdown(self, sig: Optional[signal.Signals] = None) -> None:
        """
        Perform graceful shutdown.
        
        Args:
            sig: Signal that triggered shutdown (if any)
        """
        if self._is_shutting_down:
            logger.warning("Shutdown already in progress, ignoring duplicate signal")
            return
        
        self._is_shutting_down = True
        self._shutdown_start_time = time.time()
        
        signal_name = sig.name if sig else "UNKNOWN"
        logger.info("=" * 80)
        logger.info(f"🛑 GRACEFUL SHUTDOWN INITIATED (signal: {signal_name})")
        logger.info("=" * 80)
        
        try:
            # Mark service as not ready (stop accepting new requests)
            try:
                from markettool.interfaces.api.health import get_health_checker
                health_checker = get_health_checker()
                health_checker.mark_not_ready()
                logger.info("✅ Service marked as NOT READY")
            except Exception as exc:
                logger.warning(f"Could not mark service as not ready: {exc}")
            
            # Wait a bit for load balancers to notice
            logger.info("Waiting 2s for load balancers to stop routing traffic...")
            await asyncio.sleep(2)
            
            # Execute shutdown callbacks
            await asyncio.wait_for(
                self._execute_shutdown_callbacks(),
                timeout=self._shutdown_timeout - 2  # Reserve 2s for final cleanup
            )
            
            # Cancel all remaining tasks
            logger.info("Cancelling remaining tasks...")
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            
            if tasks:
                logger.info(f"Cancelling {len(tasks)} tasks...")
                for task in tasks:
                    task.cancel()
                
                # Wait for tasks to finish cancellation
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("✅ All tasks cancelled")
            else:
                logger.info("No tasks to cancel")
            
            elapsed = time.time() - self._shutdown_start_time
            logger.info("=" * 80)
            logger.info(f"✅ GRACEFUL SHUTDOWN COMPLETED ({elapsed:.2f}s)")
            logger.info("=" * 80)
        
        except asyncio.TimeoutError:
            elapsed = time.time() - self._shutdown_start_time
            logger.error("=" * 80)
            logger.error(f"⏱️  SHUTDOWN TIMEOUT ({elapsed:.2f}s)")
            logger.error("=" * 80)
            logger.error("Force exiting due to timeout")
        
        except Exception as exc:
            logger.exception(f"Error during graceful shutdown: {exc}")
        
        finally:
            # Last resort: exit
            logger.info("Exiting process...")
            sys.exit(0)
    
    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for SIGTERM and SIGINT."""
        
        def signal_handler(sig: signal.Signals, frame) -> None:
            """Handle shutdown signals."""
            logger.info(f"Received signal {sig.name}, initiating graceful shutdown...")
            
            # Create a new event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Schedule shutdown coroutine
            loop.create_task(self.shutdown(sig))
        
        # Register handlers
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        logger.info("✅ Signal handlers registered (SIGTERM, SIGINT)")


# Global shutdown handler instance
_shutdown_handler: Optional[GracefulShutdownHandler] = None


def get_shutdown_handler(shutdown_timeout: int = 30) -> GracefulShutdownHandler:
    """
    Get or create global shutdown handler.
    
    Args:
        shutdown_timeout: Maximum seconds to wait for graceful shutdown
    
    Returns:
        Global shutdown handler instance
    """
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdownHandler(shutdown_timeout=shutdown_timeout)
    return _shutdown_handler


def register_shutdown_callback(callback: Callable) -> None:
    """
    Register a callback to be called during shutdown.
    
    Args:
        callback: Function to call during shutdown (can be sync or async)
    """
    handler = get_shutdown_handler()
    handler.register_shutdown_callback(callback)


def setup_graceful_shutdown(shutdown_timeout: int = 30) -> GracefulShutdownHandler:
    """
    Setup graceful shutdown handlers.
    
    Args:
        shutdown_timeout: Maximum seconds to wait for graceful shutdown
    
    Returns:
        Shutdown handler instance
    """
    handler = get_shutdown_handler(shutdown_timeout=shutdown_timeout)
    handler.setup_signal_handlers()
    return handler
