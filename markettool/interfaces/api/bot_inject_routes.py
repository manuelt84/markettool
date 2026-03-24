"""
Bot Trade Injection API
Endpoint unificado para inyectar operaciones sin WebView.
Soporta MT5 (vía EA existente) y Libertex (vía HTTP directo a su API interna).
"""
from __future__ import annotations

import logging

import requests
from flask import request, jsonify

logger = logging.getLogger(__name__)


def register_bot_inject_routes(app) -> None:

    @app.route("/api/v1/bot/inject-trade", methods=["POST"])
    def inject_trade():
        """
        Inyectar una operación de trading sin WebView.

        Request JSON:
        {
          "broker": "mt5" | "libertex" | "manual",
          "symbol": "EURUSD",
          "side": "buy" | "sell",
          "volume": 0.1,
          "entry_price": 1.0950,
          "stop_loss": 1.0900,
          "take_profit": 1.1000,
          "comment": "MarketTool Web - auto inject",

          // Solo para Libertex:
          "libertex_session": {
            "csrf_token": "...",
            "session_cookies": {...},
            "base_url": "https://trading.libertex.com"
          },

          // Solo para MT5:
          "mt5_magic": 12345,
          "mt5_deviation": 20
        }
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        broker = data.get("broker", "mt5").lower()

        if broker == "mt5":
            return _inject_mt5(data)
        elif broker == "libertex":
            return _inject_libertex(data)
        elif broker == "manual":
            return jsonify({
                "status": "manual",
                "message": "Manual execution required",
                "trade_details": {
                    "symbol": data.get("symbol"),
                    "side": data.get("side"),
                    "entry": data.get("entry_price"),
                    "sl": data.get("stop_loss"),
                    "tp": data.get("take_profit"),
                }
            }), 200
        else:
            return jsonify({"error": f"Unknown broker: {broker}"}), 400

    def _inject_mt5(data: dict):
        """Delegar al endpoint MT5 existente."""
        try:
            from markettool.interfaces.api.mt5_routes import get_mt5_service  # type: ignore[import]
            from markettool.domain.services.mt5_service import MT5OrderRequest  # type: ignore[import]

            service = get_mt5_service()
            order = MT5OrderRequest(
                symbol=data.get("symbol", "EURUSD"),
                volume=float(data.get("volume", 0.01)),
                side=data.get("side", "BUY").upper(),
                order_type="MARKET",
                entry_price=float(data.get("entry_price", 0)),
                stop_loss=float(data["stop_loss"]) if data.get("stop_loss") else None,
                take_profit=float(data["take_profit"]) if data.get("take_profit") else None,
                deviation=float(data.get("mt5_deviation", 20)),
                magic=int(data.get("mt5_magic", 0)),
                comment=data.get("comment", "MarketTool Web"),
            )
            response = service.place_order(order)
            return jsonify({
                "status": "success" if response.success else "failed",
                "broker": "mt5",
                "order_id": str(response.order_id) if response.order_id else None,
                "message": response.message,
            }), 200 if response.success else 400
        except Exception as e:
            logger.error("MT5 inject error: %s", e, exc_info=True)
            return jsonify({"status": "failed", "error": str(e)}), 500

    def _inject_libertex(data: dict):
        """
        Inyectar operación en Libertex vía HTTP directo.
        El backend actúa como proxy para evitar CORS.
        """
        session_data = data.get("libertex_session", {})
        base_url = session_data.get("base_url", "https://trading.libertex.com")
        csrf_token = session_data.get("csrf_token", "")
        cookies = session_data.get("session_cookies", {})

        if not csrf_token:
            return jsonify({"error": "libertex_session.csrf_token is required"}), 400

        side = data.get("side", "buy").lower()
        direction = "buy" if side == "buy" else "sell"
        symbol = data.get("symbol", "EURUSD")
        volume = float(data.get("volume", 0.01))

        payload: dict = {
            "instrumentId": symbol,
            "direction": direction,
            "amount": volume,
            "leverage": int(data.get("leverage", 1)),
        }
        if data.get("stop_loss"):
            payload["stopLoss"] = float(data["stop_loss"])
        if data.get("take_profit"):
            payload["takeProfit"] = float(data["take_profit"])

        headers = {
            "X-Token": csrf_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resp = requests.post(
                f"{base_url}/spa/investing/open-position",
                json=payload,
                headers=headers,
                cookies=cookies,
                timeout=10,
            )
            result = resp.json()

            if result.get("status") == "ok":
                invest_id = result.get("result", {}).get("investId")
                return jsonify({
                    "status": "success",
                    "broker": "libertex",
                    "invest_id": invest_id,
                    "open_commission": result.get("result", {}).get("openCommission"),
                    "message": f"Position opened: investId={invest_id}",
                    "raw": result.get("result"),
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "broker": "libertex",
                    "message": str(result.get("messages") or result.get("error") or "Unknown error"),
                    "raw": result,
                }), 400
        except Exception as e:
            logger.error("Libertex inject error: %s", e, exc_info=True)
            return jsonify({"status": "failed", "error": str(e)}), 500

    @app.route("/api/v1/bot/close-trade", methods=["POST"])
    def close_trade():
        """Cerrar posición por broker."""
        data = request.get_json()
        broker = (data or {}).get("broker", "mt5").lower()

        if broker == "libertex":
            session_data = (data or {}).get("libertex_session", {})
            invest_id = (data or {}).get("invest_id")
            base_url = session_data.get("base_url", "https://trading.libertex.com")
            csrf_token = session_data.get("csrf_token", "")
            cookies = session_data.get("session_cookies", {})

            if not invest_id:
                return jsonify({"error": "invest_id required"}), 400

            try:
                resp = requests.post(
                    f"{base_url}/spa/investing/close-position",
                    json={"investId": invest_id},
                    headers={"X-Token": csrf_token, "Content-Type": "application/json"},
                    cookies=cookies,
                    timeout=10,
                )
                result = resp.json()
                if result.get("status") == "ok":
                    r = result.get("result", {})
                    return jsonify({
                        "status": "success",
                        "broker": "libertex",
                        "invest_id": invest_id,
                        "net_pnl": r.get("netPnL"),
                        "close_price": r.get("closePrice"),
                    }), 200
                else:
                    return jsonify({"status": "failed", "raw": result}), 400
            except Exception as e:
                return jsonify({"status": "failed", "error": str(e)}), 500

        # MT5 → redirect
        return jsonify({"redirect": "use /api/v1/broker/mt5/close-order"}), 307

    @app.route("/api/v1/bot/session-status", methods=["POST"])
    def bot_session_status():
        """Verificar si las credenciales de sesión de Libertex son válidas."""
        data = request.get_json()
        session_data = (data or {}).get("libertex_session", {})
        base_url = session_data.get("base_url", "https://trading.libertex.com")
        csrf_token = session_data.get("csrf_token", "")
        cookies = session_data.get("session_cookies", {})

        try:
            resp = requests.get(
                f"{base_url}/spa/accounts/profile",
                headers={"X-Token": csrf_token},
                cookies=cookies,
                timeout=5,
            )
            if resp.status_code == 200:
                return jsonify({"status": "valid", "profile": resp.json()}), 200
            return jsonify({"status": "invalid", "http_status": resp.status_code}), 200
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500
