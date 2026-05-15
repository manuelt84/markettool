"""Broker MT5 execution service with polling pattern (EA calls backend)."""

from __future__ import annotations

import logging
import uuid
import time
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum


logger = logging.getLogger(__name__)


class OrderType(Enum):
    """MT5 Order types."""
    MARKET_BUY = "BUY"
    MARKET_SELL = "SELL"
    LIMIT_BUY = "BUY_LIMIT"
    LIMIT_SELL = "SELL_LIMIT"


class MT5Environment(Enum):
    """MT5 server environments."""
    DEMO = "demo"
    REAL = "real"


class OrderState(Enum):
    """Order execution states to prevent duplicates."""
    PENDING = "pending"  # Waiting for EA to pick up
    IN_EXECUTION = "in_execution"  # Sent to EA, waiting for result
    COMPLETED = "completed"  # Successfully executed
    FAILED = "failed"  # Execution failed


@dataclass
class MT5OrderRequest:
    """MT5 order request parameters."""
    symbol: str  # e.g., "EURUSD"
    volume: float  # Size in lots
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET" or "LIMIT"
    entry_price: float  # Entry price (used for limit orders)
    stop_loss: Optional[float] = None  # SL price
    take_profit: Optional[float] = None  # TP price
    deviation: float = 20.0  # Max deviation in points
    magic: int = 0  # Magic number for order identification
    comment: str = ""  # Order comment


@dataclass
class MT5OrderResponse:
    """MT5 order response."""
    success: bool
    order_id: Optional[str] = None  # Changed to string (UUID)
    message: str = ""
    error_code: Optional[int] = None


@dataclass
class MT5CloseOrderRequest:
    """MT5 close order request parameters."""
    symbol: str  # e.g., "EURUSD"
    position_ticket: int  # MT5 position/order ticket to close
    volume: Optional[float] = None  # Volume to close (None = close all)
    comment: str = ""  # Close reason


@dataclass
class MT5CloseOrderResponse:
    """MT5 close order response."""
    success: bool
    order_id: Optional[str] = None  # UUID of the close request
    mt5_close_ticket: Optional[int] = None  # MT5 close order ticket
    message: str = ""
    error_code: Optional[int] = None


@dataclass
class PendingOrder:
    """Pending order waiting for EA to execute."""
    order_id: str  # UUID
    request: MT5OrderRequest
    timestamp: float = field(default_factory=time.time)
    result: Optional[dict] = None  # Result from EA
    state: OrderState = field(default=OrderState.PENDING)  # Order execution state
    execution_start_time: Optional[float] = None  # When sent to EA
    execution_lease_id: Optional[str] = None  # Unique ID assigned to this execution attempt


class BrokerMT5Service:
    """Service to handle MT5 broker connections and orders via polling pattern."""

    def __init__(self):
        """Initialize MT5 service."""
        self.is_connected = False
        self.current_environment = None
        self.current_account = None
        self.pending_orders: dict[str, PendingOrder] = {}  # order_id -> PendingOrder
        self.completed_orders: dict[str, PendingOrder] = {}  # Completed orders history
        self.ea_last_poll = None  # Last time EA polled
        self.ea_account_info = {}  # Last account info received from EA
        self.execution_timeout = 30  # Seconds before considering an execution "lost"

    @property
    def ea_online(self) -> bool:
        """Check if EA is online (polled recently)."""
        if self.ea_last_poll is None:
            return False
        return (time.time() - self.ea_last_poll) < 10  # Online if polled in last 10 seconds

    def connect(
        self,
        account_number: int,
        password: str,
        environment: MT5Environment = MT5Environment.DEMO,
        server: str = "ForexClub-MT5 Demo Server",
    ) -> bool:
        """
        Connect to MT5 server (registers credentials, EA manages actual connection).

        Args:
            account_number: MT5 account number
            password: MT5 password
            environment: DEMO or REAL environment
            server: MT5 server name

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Mark as connected - EA will handle actual MT5 connection
            self.is_connected = True
            self.current_environment = environment
            self.current_account = account_number
            logger.info(f"✅ MT5 service ready (account {account_number})")
            return True
        except Exception as e:
            logger.error(f"Exception during MT5 connection: {e}", exc_info=True)
            return False

    def disconnect(self) -> None:
        """Disconnect from MT5."""
        self.is_connected = False
        logger.info("✅ Disconnected from MT5")

    def place_order(self, order_request: MT5OrderRequest) -> MT5OrderResponse:
        """
        Queue an order for MT5 EA to execute.

        Args:
            order_request: Order parameters

        Returns:
            MT5OrderResponse with success status and order_id
        """
        # Note: We don't check is_connected here because polling pattern
        # works independently - EA will execute orders from the queue
        # even if credentials weren't explicitly set via connect()
        try:
            # Generate unique order ID
            order_id = str(uuid.uuid4())
            
            # Add to pending orders queue
            pending = PendingOrder(order_id=order_id, request=order_request)
            self.pending_orders[order_id] = pending
            
            logger.info(f"📋 Order {order_id} queued for EA execution")
            
            # Return success with order ID (EA will execute asynchronously)
            return MT5OrderResponse(
                success=True,
                order_id=order_id,
                message=f"Order queued successfully with ID {order_id}"
            )

        except Exception as e:
            logger.error(f"Error queuing order: {e}", exc_info=True)
            return MT5OrderResponse(success=False, message=str(e))
    
    def close_order(self, close_request: MT5CloseOrderRequest) -> MT5CloseOrderResponse:
        """
        Queue a close order for MT5 EA to execute.

        Args:
            close_request: Close order parameters

        Returns:
            MT5CloseOrderResponse with success status and order_id
        """
        try:
            # Generate unique close order ID
            order_id = str(uuid.uuid4())
            
            # Create a virtual "order" to track the close request
            # We'll reuse the existing structure but mark it as a close order
            close_order_req = MT5OrderRequest(
                symbol=close_request.symbol,
                volume=close_request.volume or 1.0,  # Dummy volume for close
                side="CLOSE",  # Special marker for close orders
                order_type="MARKET",
                entry_price=0.0,  # Not used for close
                stop_loss=None,
                take_profit=None,
                magic=0,
                comment=f"CLOSE_TICKET_{close_request.position_ticket}|{close_request.comment}"
            )
            
            # Add to pending orders queue
            pending = PendingOrder(order_id=order_id, request=close_order_req)
            self.pending_orders[order_id] = pending
            
            logger.info(f"📋 Close order {order_id} queued for EA execution (ticket: {close_request.position_ticket})")
            
            # Return success with order ID (EA will execute asynchronously)
            return MT5CloseOrderResponse(
                success=True,
                order_id=order_id,
                message=f"Close order queued successfully with ID {order_id}"
            )

        except Exception as e:
            logger.error(f"Error queuing close order: {e}", exc_info=True)
            return MT5CloseOrderResponse(success=False, message=str(e))
    
    def get_pending_order_for_ea(self) -> Optional[dict]:
        """
        Get next pending order for EA to execute.
        Called by EA during polling.
        
        Marks order as IN_EXECUTION to prevent duplicate execution by other EAs.
        Returns:
            Order dict with lease_id, or None if no pending orders
        """
        # Check for stale executions (EA crashed/timeout)
        self._check_stale_executions()
        
        # Update EA last poll time
        self.ea_last_poll = time.time()
        
        # Find first order in PENDING state (not already being executed)
        for order_id, pending in self.pending_orders.items():
            if pending.state == OrderState.PENDING:
                # Mark as IN_EXECUTION to prevent other EAs from getting this order
                pending.state = OrderState.IN_EXECUTION
                pending.execution_start_time = time.time()
                pending.execution_lease_id = str(uuid.uuid4())  # Unique lease ID for this execution attempt
                
                logger.info(f"📤 Sending order {order_id} to EA (lease: {pending.execution_lease_id[:8]}...)")
                
                return {
                    "order_id": order_id,
                    "lease_id": pending.execution_lease_id,  # EA sends this back with result
                    "symbol": pending.request.symbol,
                    "volume": pending.request.volume,
                    "side": pending.request.side,
                    "price": pending.request.entry_price,
                    "sl": pending.request.stop_loss or 0.0,
                    "tp": pending.request.take_profit or 0.0,
                    "deviation": int(pending.request.deviation),
                    "comment": pending.request.comment,
                }
        
        return None
    
    def _check_stale_executions(self) -> None:
        """
        Check for orders stuck in IN_EXECUTION state (EA crashed/timeout).
        Revert them to PENDING after execution_timeout seconds.
        """
        current_time = time.time()
        stale_orders = []
        
        for order_id, pending in self.pending_orders.items():
            if pending.state == OrderState.IN_EXECUTION:
                elapsed = current_time - pending.execution_start_time
                if elapsed > self.execution_timeout:
                    stale_orders.append((order_id, pending, elapsed))
        
        for order_id, pending, elapsed in stale_orders:
            logger.warning(
                f"⚠️  Order {order_id} stuck in execution for {int(elapsed)}s (EA timeout). "
                f"Reverting to PENDING for retry."
            )
            pending.state = OrderState.PENDING
            pending.execution_start_time = None
            pending.execution_lease_id = None
    
    def update_ea_account_info(self, account_info: dict) -> None:
        """Update account info received from EA."""
        # Si el EA envía open_positions, las guardamos separado para que la app pueda consultarlas
        if 'open_positions' in account_info:
            self.ea_open_positions = account_info.get('open_positions', [])
        self.ea_account_info = account_info
        self.ea_last_poll = time.time()

    def get_ea_open_positions(self) -> list:
        """Return the last list of open positions reported by the EA."""
        return getattr(self, 'ea_open_positions', [])
    
    def report_order_result(self, order_id: str, result: dict) -> None:
        """
        Report order execution result from EA.
        
        Args:
            order_id: Order UUID
            result: Result dict from EA (must contain "success" boolean and "lease_id")
        """
        if order_id not in self.pending_orders:
            logger.warning(f"⚠️  Received result for unknown order {order_id}")
            return
        
        pending = self.pending_orders[order_id]
        
        # Verify lease_id matches (security against stale results from timed-out EA)
        lease_id = result.get("lease_id")
        if lease_id != pending.execution_lease_id:
            logger.warning(
                f"⚠️  Order {order_id} result has mismatched lease_id "
                f"(expected: {pending.execution_lease_id[:8]}..., got: {lease_id[:8] if lease_id else 'None'}...). "
                f"Ignoring stale result."
            )
            return
        
        # Only accept results from IN_EXECUTION state
        if pending.state != OrderState.IN_EXECUTION:
            logger.warning(
                f"⚠️  Order {order_id} is in state {pending.state.value}, expected IN_EXECUTION. "
                f"Ignoring result."
            )
            return
        
        # Store result and mark as completed/failed
        pending.result = result
        success = result.get("success", False)
        pending.state = OrderState.COMPLETED if success else OrderState.FAILED
        
        # Move to completed orders
        self.completed_orders[order_id] = pending
        del self.pending_orders[order_id]
        
        if success:
            mt5_order_id = result.get("mt5_order_id", "unknown")
            original_volume = result.get("original_volume")
            actual_volume = result.get("volume")
            
            # Detectar si el volumen fue ajustado por limite
            volume_adjusted = False
            if original_volume and actual_volume and original_volume != actual_volume:
                volume_adjusted = True
                logger.warning(
                    f"⚠️  Volumen ajustado por limite del broker: {original_volume} → {actual_volume} "
                    f"(reducción de {((original_volume - actual_volume) / original_volume * 100):.1f}%)"
                )
            
            # Agregar información de ajuste al resultado
            result["volume_adjusted"] = volume_adjusted
            result["original_volume_requested"] = original_volume
            
            logger.info(f"✅ Order {order_id} executed successfully (MT5 ID: {mt5_order_id})")
        else:
            error = result.get("error", "Unknown error")
            retcode = result.get("retcode")
            logger.error(f"❌ Order {order_id} failed: {error} (retcode: {retcode})")
    
    def get_order_status(self, order_id: str) -> Optional[dict]:
        """
        Get status of a specific order.
        
        Returns:
            Order status dict or None if not found
        """
        if order_id in self.pending_orders:
            pending = self.pending_orders[order_id]
            if pending.result:
                return {
                    "status": "completed",
                    **pending.result
                }
            else:
                elapsed = time.time() - pending.timestamp
                return {
                    "status": "pending",
                    "message": f"Waiting for EA to execute (queued {int(elapsed)}s ago)",
                    "ea_online": self.ea_online
                }
        elif order_id in self.completed_orders:
            return {
                "status": "completed",
                **self.completed_orders[order_id].result
            }
        
        return None

    def get_account_info(self) -> dict:
        """Get MT5 account information (from last EA poll)."""
        if not self.ea_account_info:
            return {
                "account": self.current_account,
                "environment": self.current_environment.value if self.current_environment else "unknown",
                "ea_online": self.ea_online,
                "message": "No account info yet (waiting for EA to poll)"
            }
        
        return {
            **self.ea_account_info,
            "ea_online": self.ea_online,
        }

    def get_symbol_info(self, symbol: str) -> dict:
        """Get symbol information (placeholder - EA could provide this)."""
        return {
            "symbol": symbol,
            "message": "Symbol info available when EA polls",
            "ea_online": self.ea_online
        }


# Global service instance
_mt5_service: Optional[BrokerMT5Service] = None


def get_mt5_service() -> BrokerMT5Service:
    """Get or create MT5 service instance."""
    global _mt5_service
    if _mt5_service is None:
        _mt5_service = BrokerMT5Service()
    return _mt5_service
