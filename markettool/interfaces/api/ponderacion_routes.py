"""Ponderación streaming routes for real-time cache updates."""

from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime
from flask import jsonify, request
from flask_sock import Sock


def register_ponderacion_routes(app, ponderacion_cache) -> None:
    """Register ponderación API routes and WebSocket streaming.
    
    Args:
        app: Flask application instance
        ponderacion_cache: PonderacionCache instance (Redis-backed)
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


__all__ = ["register_ponderacion_routes"]
