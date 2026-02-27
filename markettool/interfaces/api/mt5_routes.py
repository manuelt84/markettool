"""MT5 Broker API routes with polling pattern support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from flask import request, jsonify

from markettool.application.services.broker_mt5_service import (
    get_mt5_service,
    MT5OrderRequest,
    MT5Environment,
)

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


def register_mt5_routes(app: Flask) -> None:
    """Register MT5 broker API routes."""

    @app.route("/api/v1/broker/mt5/connect", methods=["POST"])
    def mt5_connect():
        """
        Connect to MT5 broker.
        
        Request JSON:
        {
            "account_number": 500296283,
            "password": "iyOOaB2%",
            "environment": "demo",  # or "real"
            "server": "ForexClub-MT5 Demo Server"
        }
        """
        try:
            data = request.get_json()
            
            if not data:
                return jsonify({"error": "No JSON data provided"}), 400
            
            account = data.get("account_number")
            password = data.get("password")
            environment_str = data.get("environment", "demo")
            server = data.get("server", "ForexClub-MT5 Demo Server")
            
            if not account or not password:
                return jsonify({"error": "account_number and password required"}), 400
            
            # Parse environment
            try:
                environment = MT5Environment[environment_str.upper()]
            except KeyError:
                return jsonify({"error": f"Invalid environment: {environment_str}"}), 400
            
            # Connect
            service = get_mt5_service()
            success = service.connect(
                account_number=account,
                password=password,
                environment=environment,
                server=server,
            )
            
            if success:
                account_info = service.get_account_info()
                return jsonify({
                    "status": "connected",
                    "account": account,
                    "server": server,
                    "environment": environment_str,
                    "accountInfo": account_info,
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "error": "Failed to connect to MT5 broker"
                }), 401

        except Exception as e:
            logger.error(f"MT5 connect error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/disconnect", methods=["POST"])
    def mt5_disconnect():
        """Disconnect from MT5 broker."""
        try:
            service = get_mt5_service()
            service.disconnect()
            return jsonify({"status": "disconnected"}), 200
        except Exception as e:
            logger.error(f"MT5 disconnect error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/place-order", methods=["POST", "OPTIONS"])
    def mt5_place_order():
        """
        Place an order on MT5 (queues for EA to execute).
        
        Request JSON:
        {
            "symbol": "EURUSD",
            "volume": 0.1,
            "side": "BUY",
            "order_type": "MARKET",
            "entry_price": 1.0950,
            "stop_loss": 1.0900,
            "take_profit": 1.1000,
            "deviation": 20,
            "magic": 0,
            "comment": "Entry from MarketTool app"
        }
        """
        # Handle CORS preflight requests
        if request.method == "OPTIONS":
            logger.debug("📋 Handling CORS preflight for /place-order")
            return jsonify({}), 200
        
        logger.info(f"📥 Received /place-order request from {request.remote_addr}")
        logger.debug(f"   Headers: {dict(request.headers)}")
        
        try:
            data = request.get_json()
            logger.debug(f"   Body: {data}")
            
            if not data:
                logger.warning("❌ No JSON data provided")
                return jsonify({"error": "No JSON data provided"}), 400
            
            service = get_mt5_service()
            
            # Build order request (polling works without explicit connection)
            order = MT5OrderRequest(
                symbol=data.get("symbol", "EURUSD"),
                volume=float(data.get("volume", 0.1)),
                side=data.get("side", "BUY"),
                order_type=data.get("order_type", "MARKET"),
                entry_price=float(data.get("entry_price", 0.0)),
                stop_loss=float(data.get("stop_loss")) if data.get("stop_loss") else None,
                take_profit=float(data.get("take_profit")) if data.get("take_profit") else None,
                deviation=float(data.get("deviation", 20.0)),
                magic=int(data.get("magic", 0)),
                comment=data.get("comment", ""),
            )
            logger.info(f"   Order: {order.symbol} {order.side} {order.volume} lot(s)")
            
            # Place order (will be queued for EA)
            response = service.place_order(order)
            
            if response.success:
                logger.info(f"✅ Order queued successfully: {response.order_id}")
                return jsonify({
                    "status": "success",
                    "orderId": response.order_id,
                    "message": response.message,
                }), 200
            else:
                logger.warning(f"❌ Order placement failed: {response.message}")
                return jsonify({
                    "status": "failed",
                    "error": response.message,
                    "errorCode": response.error_code,
                }), 400

        except Exception as e:
            logger.error(f"❌ MT5 place order error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/account-info", methods=["GET"])
    def mt5_account_info():
        """Get MT5 account information."""
        try:
            service = get_mt5_service()
            
            # Get account info if available from EA polling
            info = service.get_account_info()
            return jsonify(info), 200

        except Exception as e:
            logger.error(f"MT5 account info error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/symbol-info/<symbol>", methods=["GET"])
    def mt5_symbol_info(symbol: str):
        """Get MT5 symbol information."""
        try:
            service = get_mt5_service()
            
            # Get symbol info if available from EA polling
            info = service.get_symbol_info(symbol)
            return jsonify(info), 200

        except Exception as e:
            logger.error(f"MT5 symbol info error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/status", methods=["GET"])
    def mt5_status():
        """Get MT5 connection status."""
        try:
            service = get_mt5_service()
            
            return jsonify({
                "connected": True,  # Polling mode is always ready to accept orders
                "ea_online": service.ea_online,  # EA actually connected to MT5
                "environment": service.current_environment.value if service.current_environment else "Demo",
                "account": service.current_account,
                "pending_orders_count": len(service.pending_orders),
            }), 200

        except Exception as e:
            logger.error(f"MT5 status error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/broker/mt5/poll", methods=["POST"])
    def mt5_poll():
        """
        EA polling endpoint - EA calls this to check for pending orders.
        
        Request JSON (from EA):
        {
            "login": 500296283,
            "balance": 10000.00,
            "equity": 10050.00,
            "margin": 100.00,
            "free_margin": 9950.00,
            "leverage": 100,
            "connected": true
        }
        
        Response JSON:
        {
            "has_pending_order": true,
            "order": {
                "order_id": "uuid...",
                "symbol": "EURUSD",
                "volume": 0.1,
                "side": "BUY",
                "price": 1.0950,
                "sl": 1.0900,
                "tp": 1.1000,
                "deviation": 20,
                "comment": "Entry from app"
            }
        }
        """
        try:
            data = request.get_json()
            service = get_mt5_service()
            
            # Update EA account info
            if data:
                service.update_ea_account_info(data)
            
            # Check for pending order
            pending_order = service.get_pending_order_for_ea()
            
            if pending_order:
                return jsonify({
                    "has_pending_order": True,
                    "order": pending_order
                }), 200
            else:
                return jsonify({
                    "has_pending_order": False
                }), 200
        
        except Exception as e:
            logger.error(f"MT5 poll error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/v1/broker/mt5/result", methods=["POST"])
    def mt5_result():
        """
        EA result reporting endpoint - EA calls this after executing an order.
        
        Request JSON (from EA):
        {
            "success": true,
            "order_id": "uuid...",
            "mt5_order_id": 123456,
            "price": 1.09503,
            "volume": 0.1,
            "original_volume": 0.2  (opcional, si fue ajustado)
        }
        
        OR
        
        {
            "success": false,
            "order_id": "uuid...",
            "error": "Order failed",
            "retcode": 10034,
            "comment": "Volume limit reached"
        }
        """
        try:
            data = request.get_json()
            
            if not data or "order_id" not in data:
                return jsonify({"error": "order_id required"}), 400
            
            service = get_mt5_service()
            service.report_order_result(data["order_id"], data)
            
            # Preparar respuesta informativa
            response_data = {
                "status": "received",
                "order_id": data["order_id"]
            }
            
            # Si la orden fue exitosa, informar sobre ajuste de volumen
            if data.get("success"):
                original_vol = data.get("original_volume")
                actual_vol = data.get("volume")
                if original_vol and actual_vol and original_vol != actual_vol:
                    response_data["volume_adjusted"] = True
                    response_data["reduction_percentage"] = ((original_vol - actual_vol) / original_vol * 100)
                    response_data["message"] = f"Order executed with volume adjustment: {original_vol} → {actual_vol}"
            
            return jsonify(response_data), 200
        
        except Exception as e:
            logger.error(f"MT5 result error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/v1/broker/mt5/order-status/<order_id>", methods=["GET"])
    def mt5_order_status(order_id: str):
        """Get status of a specific order by UUID."""
        try:
            service = get_mt5_service()
            status = service.get_order_status(order_id)
            
            if status:
                return jsonify(status), 200
            else:
                return jsonify({"error": "Order not found"}), 404
        
        except Exception as e:
            logger.error(f"MT5 order status error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    logger.info("✅ MT5 broker routes registered")
