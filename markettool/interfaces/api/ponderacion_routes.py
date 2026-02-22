"""Ponderación streaming routes for real-time cache updates."""

from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime
from flask import jsonify, request
from flask_sock import Sock


def register_ponderacion_routes(app, ponderacion_cache, ponderacion_history=None, ponderacion_alert=None) -> None:
    """Register ponderación API routes and WebSocket streaming.
    
    Args:
        app: Flask application instance
        ponderacion_cache: PonderacionCache instance (Redis-backed)
        ponderacion_history: PonderacionHistory instance (optional)
        ponderacion_alert: PonderacionAlert instance (optional)
    """
    
    # Initialize Sock for WebSocket support
    sock = Sock(app)
    
    # Keep track of connected clients for broadcasting
    _connected_clients = set()
    _clients_lock = asyncio.Lock()
    
    @app.route("/api/ponderacion/stats", methods=["GET"])
    def get_cache_stats():
        """Get cache statistics (hits, misses, Redis status)."""
        stats = ponderacion_cache.stats()
        return jsonify({
            "cache": stats,
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/ponderacion/invalidate", methods=["POST"])
    def invalidate_cache():
        """Manually invalidate a ponderación from cache.
        
        Request body:
        {
            "symbol": "BTCUSD",
            "timeframe": "1m"
        }
        """
        try:
            data = request.get_json()
            symbol = data.get("symbol", "").strip().upper()
            timeframe = data.get("timeframe", "").strip()
            
            if not symbol or not timeframe:
                return jsonify({"error": "Missing symbol or timeframe"}), 400
            
            success = ponderacion_cache.invalidate(symbol, timeframe)
            
            return jsonify({
                "success": success,
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp_utc": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @sock.route("/api/ponderacion/stream")
    def ponderacion_stream(ws):
        """WebSocket endpoint for real-time ponderación updates.
        
        Client connects and receives ponderación updates as they are calculated.
        Updates sent as JSON: {symbol, timeframe, score, timestamp_utc}
        """
        try:
            # Register client
            _connected_clients.add(ws)
            print(f"[WebSocket] Client connected. Total: {len(_connected_clients)}")
            
            # Send welcome message
            ws.send(_json.dumps({
                "type": "connected",
                "message": "Connected to ponderación stream",
                "timestamp_utc": datetime.utcnow().isoformat(),
            }))
            
            # Wait for client messages (keep connection alive)
            while True:
                try:
                    msg = ws.receive(timeout=30)  # Timeout to detect disconnections
                    if msg is None:
                        break
                    
                    # Echo back client message (heartbeat)
                    ws.send(_json.dumps({
                        "type": "pong",
                        "echo": msg,
                        "timestamp_utc": datetime.utcnow().isoformat(),
                    }))
                except Exception:
                    break
                    
        except Exception as e:
            print(f"[WebSocket] Error: {e}")
        finally:
            # Unregister client
            _connected_clients.discard(ws)
            print(f"[WebSocket] Client disconnected. Total: {len(_connected_clients)}")
    
    async def broadcast_ponderacion_update(symbol: str, timeframe: str, data: dict) -> None:
        """Broadcast ponderación update to all connected WebSocket clients.
        
        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe identifier
            data: Ponderación data to broadcast
        """
        if not _connected_clients:
            return  # No clients connected
        
        message = _json.dumps({
            "type": "ponderacion_update",
            "symbol": symbol,
            "timeframe": timeframe,
            "data": data,
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
        
        # Send to all connected clients
        disconnected = set()
        for ws in _connected_clients:
            try:
                ws.send(message)
            except Exception:
                disconnected.add(ws)
        
        # Remove disconnected clients
        for ws in disconnected:
            _connected_clients.discard(ws)
    
    # Expose broadcast function globally (will be called from ponderacion calculations)
    app.broadcast_ponderacion_update = broadcast_ponderacion_update
    
    # ===== PHASE 3: Historical & Alert Routes =====
    
    @app.route("/api/ponderacion/history", methods=["GET"])
    def get_ponderacion_history():
        """Get historical ponderacion records for analysis.
        
        Query params:
        - symbol: Required (e.g., 'BTCUSD')
        - timeframe: Required (e.g., '1m', '5m', '1h')
        - limit: Optional, default 100 (max 500)
        """
        if not ponderacion_history:
            return jsonify({"error": "History tracking not enabled"}), 503
        
        symbol = request.args.get("symbol", "").strip().upper()
        timeframe = request.args.get("timeframe", "").strip()
        limit = min(int(request.args.get("limit", 100)), 500)
        
        if not symbol or not timeframe:
            return jsonify({"error": "Missing symbol or timeframe"}), 400
        
        history = ponderacion_history.get_history(symbol, timeframe, limit=limit)
        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "records": history,
            "count": len(history),
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/ponderacion/momentum", methods=["GET"])
    def get_momentum():
        """Calculate momentum score for a symbol/timeframe.
        
        Query params:
        - symbol: Required
        - timeframe: Required
        - lookback: Optional, default 10 (how many candles to analyze)
        """
        if not ponderacion_history:
            return jsonify({"error": "History tracking not enabled"}), 503
        
        symbol = request.args.get("symbol", "").strip().upper()
        timeframe = request.args.get("timeframe", "").strip()
        lookback = int(request.args.get("lookback", 10))
        
        if not symbol or not timeframe:
            return jsonify({"error": "Missing symbol or timeframe"}), 400
        
        momentum = ponderacion_history.calculate_momentum(symbol, timeframe, lookback=lookback)
        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "momentum": momentum,
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/ponderacion/rank-change", methods=["GET"])
    def get_rank_change():
        """Get ranking change since last calculation.
        
        Query params:
        - symbol: Required
        - timeframe: Required
        """
        if not ponderacion_history:
            return jsonify({"error": "History tracking not enabled"}), 503
        
        symbol = request.args.get("symbol", "").strip().upper()
        timeframe = request.args.get("timeframe", "").strip()
        
        if not symbol or not timeframe:
            return jsonify({"error": "Missing symbol or timeframe"}), 400
        
        rank_change = ponderacion_history.get_rank_change(symbol, timeframe)
        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "rank_change": rank_change,
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/alerts", methods=["GET"])
    def get_alerts():
        """Get active alerts.
        
        Query params:
        - symbol: Optional, filter by symbol
        """
        if not ponderacion_alert:
            return jsonify({"error": "Alerts not enabled"}), 503
        
        symbol = request.args.get("symbol", "").strip().upper() or None
        alerts = ponderacion_alert.get_active_alerts(symbol=symbol)
        
        return jsonify({
            "alerts": alerts,
            "count": len(alerts),
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/alerts/<alert_id>", methods=["PUT"])
    def mark_alert_read(alert_id):
        """Mark alert as read."""
        if not ponderacion_alert:
            return jsonify({"error": "Alerts not enabled"}), 503
        
        ponderacion_alert.mark_alert_read(alert_id)
        return jsonify({"success": True, "alert_id": alert_id})


__all__ = ["register_ponderacion_routes"]

