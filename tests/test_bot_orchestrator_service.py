from __future__ import annotations

from markettool.application.services.bot_orchestrator_service import BotOrchestratorService
from markettool.interfaces.api.bot_orchestrator_routes import (
    _libertex_form_payload,
    _libertex_headers,
    _passes_backend_execution_config,
)


def test_bot_orchestrator_deduplicates_by_idempotency_key(tmp_path):
    service = BotOrchestratorService(tmp_path / "bot_state.json")

    payload = {
        "idempotency_key": "u1:trading:EURUSD:1m:long:e1",
        "user_id": "u1",
        "bot_type": "trading",
        "action": "open",
        "broker": "mt5",
        "entry": {
            "id": "e1",
            "symbol": "EUR/USD",
            "timeframe": "1m",
            "side": "long",
            "entry": 1.1,
            "sl": 1.0,
            "tp": 1.2,
        },
    }

    first, created_first = service.create_order(payload)
    second, created_second = service.create_order(payload)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert len(service.list_orders(user_id="u1")) == 1


def test_bot_orchestrator_redacts_session_material(tmp_path):
    service = BotOrchestratorService(tmp_path / "bot_state.json")

    order, _ = service.create_order(
        {
            "user_id": "u1",
            "broker": "libertex",
            "libertex_session": {
                "csrf_token": "secret-token",
                "session_cookies": {"SID": "secret-cookie"},
                "base_url": "https://app.libertex.org",
            },
            "entry": {"id": "e1", "symbol": "EURUSD", "side": "buy"},
        }
    )

    saved = service.list_orders(user_id="u1")[0]
    assert saved["id"] == order.id
    assert saved["request"]["libertex_session"] == "[redacted]"


def test_libertex_backend_request_reuses_saved_webview_context():
    session = {
        "base_url": "https://app.libertex.org",
        "csrf_token": "csrf-123",
        "session_cookies": {"instanceId": "inst-1", "SID": "cookie-1"},
        "user_agent": "MarketTool WebView",
    }

    headers = _libertex_headers(
        session,
        "csrf-123",
        content_type="application/x-www-form-urlencoded; charset=UTF-8",
    )
    form = _libertex_form_payload({"symbol": "NZDCAD", "sumInv": 20}, "csrf-123")

    assert headers["User-Agent"] == "MarketTool WebView"
    assert headers["X-CSRF-Token"] == "csrf-123"
    assert headers["X-FX-Instance-Id"] == "inst-1"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert form["symbol"] == "NZDCAD"
    assert form["csrfToken"] == "csrf-123"
    assert "clientRequestTime" in form


def test_bot_orchestrator_upserts_position_from_order(tmp_path):
    service = BotOrchestratorService(tmp_path / "bot_state.json")
    order, _ = service.create_order(
        {
            "user_id": "u1",
            "bot_type": "scalping",
            "broker": "mt5",
            "entry": {
                "id": "entry-1",
                "symbol": "EURUSD",
                "timeframe": "5m",
                "side": "short",
                "entry": 1.09,
                "sl": 1.1,
                "tp": 1.08,
            },
        }
    )

    position = service.upsert_position_from_order(order, {"mt5_order_id": 123456})
    positions = service.list_positions(user_id="u1", status="open")

    assert position.id == "mt5:123456"
    assert positions[0]["broker_position_id"] == "123456"
    assert positions[0]["symbol"] == "EURUSD"
    assert positions[0]["side"] == "sell"


def test_backend_daemon_rejects_symbol_exposure_across_bot_types(tmp_path):
    service = BotOrchestratorService(tmp_path / "bot_state.json")
    existing_order, _ = service.create_order(
        {
            "user_id": "u1",
            "bot_type": "trading",
            "broker": "mt5",
            "entry": {"id": "trading-1", "symbol": "EUR/USD", "timeframe": "5m", "side": "long"},
        }
    )
    service.upsert_position_from_order(existing_order, {"mt5_order_id": 777})

    accepted, reason = _passes_backend_execution_config(
        {"id": "scalp-1", "symbol": "EURUSD", "timeframe": "1m", "side": "short", "confidence": 90, "rr": 2},
        [],
        {},
        service,
        user_id="u1",
        bot_type="scalping",
        broker="mt5",
        exec_id="exec-scalping",
        symbol="EURUSD",
    )

    assert accepted is False
    assert reason == "symbol already has open backend exposure"


def test_backend_daemon_rejects_symbol_exposure_from_pending_order(tmp_path):
    service = BotOrchestratorService(tmp_path / "bot_state.json")
    service.create_order(
        {
            "user_id": "u1",
            "bot_type": "strategy",
            "broker": "mt5",
            "entry": {"id": "strategy-1", "symbol": "NZD/CAD", "timeframe": "15m", "side": "long"},
        }
    )

    accepted, reason = _passes_backend_execution_config(
        {"id": "trading-1", "symbol": "NZDCAD", "timeframe": "5m", "side": "long", "confidence": 90, "rr": 2},
        [],
        {},
        service,
        user_id="u1",
        bot_type="trading",
        broker="mt5",
        exec_id="exec-trading",
        symbol="NZDCAD",
    )

    assert accepted is False
    assert reason == "symbol already has open backend exposure"
