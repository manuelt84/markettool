"""Ponderación streaming routes for real-time cache updates."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import threading
import uuid
from datetime import datetime
from flask import jsonify, request
from flask_sock import Sock


logger = logging.getLogger(__name__)


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
    instance_id = str(uuid.uuid4())
    pubsub_channel = os.getenv("PONDERACION_STREAM_CHANNEL", "ponderacion:stream:updates")
    redis_pubsub_enabled = os.getenv("PONDERACION_REDIS_PUBSUB_ENABLED", "true").lower() == "true"

    def _parse_int_query(name: str, default: int, *, min_value: int, max_value: int) -> tuple[int, str | None]:
        raw = request.args.get(name, default)
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return default, f"Invalid {name} parameter: must be integer"
        parsed = max(min_value, min(parsed, max_value))
        return parsed, None
    
    # Keep track of connected clients for broadcasting
    _connected_clients = set()
    _clients_lock = asyncio.Lock()

    redis_client = None
    if getattr(ponderacion_history, "redis_client", None) is not None:
        redis_client = ponderacion_history.redis_client
    elif getattr(ponderacion_alert, "redis_client", None) is not None:
        redis_client = ponderacion_alert.redis_client
    elif getattr(ponderacion_cache, "redis_client", None) is not None:
        redis_client = ponderacion_cache.redis_client

    def _broadcast_message_to_clients(message: str) -> None:
        disconnected = set()
        for ws in _connected_clients:
            try:
                ws.send(message)
            except Exception:
                disconnected.add(ws)
        for ws in disconnected:
            _connected_clients.discard(ws)

    def _start_pubsub_listener() -> None:
        if not redis_pubsub_enabled or redis_client is None:
            return
        if getattr(app, "_ponderacion_pubsub_listener_started", False):
            return

        def _listener() -> None:
            pubsub = None
            try:
                pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(pubsub_channel)
                logger.info("[PonderacionStream] Pub/Sub listener started on channel=%s", pubsub_channel)
                for message in pubsub.listen():
                    if not message or message.get("type") != "message":
                        continue
                    raw = message.get("data")
                    if raw is None:
                        continue
                    try:
                        payload = _json.loads(raw)
                    except Exception:
                        continue
                    if payload.get("origin") == instance_id:
                        continue
                    _broadcast_message_to_clients(_json.dumps(payload))
            except Exception as exc:
                logger.warning("[PonderacionStream] Pub/Sub listener stopped: %s", exc)
            finally:
                try:
                    if pubsub is not None:
                        pubsub.close()
                except Exception:
                    pass

        threading.Thread(target=_listener, daemon=True, name="ponderacion-pubsub-listener").start()
        app._ponderacion_pubsub_listener_started = True

    _start_pubsub_listener()
    
    @app.route("/api/ponderacion/stats", methods=["GET"])
    def get_ponderacion_cache_stats():
        """Get ponderación cache statistics (hits, misses, Redis status)."""
        stats = ponderacion_cache.stats()
        return jsonify({
            "cache": stats,
            "stream": {
                "connected_clients": len(_connected_clients),
                "redis_pubsub_enabled": redis_pubsub_enabled,
                "redis_connected": redis_client is not None,
                "pubsub_channel": pubsub_channel,
            },
            "timestamp_utc": datetime.utcnow().isoformat(),
        })

    @app.route("/api/ponderacion/stream/status", methods=["GET"])
    def get_ponderacion_stream_status():
        return jsonify({
            "connected_clients": len(_connected_clients),
            "redis_pubsub_enabled": redis_pubsub_enabled,
            "redis_connected": redis_client is not None,
            "pubsub_channel": pubsub_channel,
            "instance_id": instance_id,
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
        payload = {
            "type": "ponderacion_update",
            "symbol": symbol,
            "timeframe": timeframe,
            "data": data,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "origin": instance_id,
        }
        message = _json.dumps(payload)

        if _connected_clients:
            _broadcast_message_to_clients(message)

        if redis_pubsub_enabled and redis_client is not None:
            try:
                redis_client.publish(pubsub_channel, message)
            except Exception as exc:
                logger.warning("[PonderacionStream] Publish failed: %s", exc)
    
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
        timeframe = request.args.get("timeframe", "").strip().lower()
        limit, err = _parse_int_query("limit", 100, min_value=1, max_value=500)
        if err:
            return jsonify({"error": err}), 400
        
        if not symbol or not timeframe:
            return jsonify({"error": "Missing symbol or timeframe"}), 400
        
        history = ponderacion_history.get_history(symbol, timeframe, limit=limit)
        method_breakdown: dict[str, int] = {}
        for item in history:
            method = str(item.get("calculation_method") or "unknown")
            method_breakdown[method] = method_breakdown.get(method, 0) + 1

        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "records": history,
            "count": len(history),
            "schema": {
                "version": 2,
                "canonical_score_field": "canonical_score",
                "method_breakdown": method_breakdown,
            },
            "timestamp_utc": datetime.utcnow().isoformat(),
        })
    
    @app.route("/api/ponderacion/momentum", methods=["GET"])
    def get_momentum():
        """Calculate momentum score for a symbol/timeframe.
        
        Query params:
        - symbol: Required
        - timeframe: Required
        - lookback: Optional, default 10 (how many candles to analyze, 1-500)
        """
        if not ponderacion_history:
            return jsonify({"error": "History tracking not enabled"}), 503
        
        symbol = request.args.get("symbol", "").strip().upper()
        timeframe = request.args.get("timeframe", "").strip().lower()
        # Validate lookback bounds: 1-500
        lookback, err = _parse_int_query("lookback", 10, min_value=1, max_value=500)
        if err:
            return jsonify({"error": err}), 400
        
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
        timeframe = request.args.get("timeframe", "").strip().lower()
        
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

