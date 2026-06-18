from __future__ import annotations

from markettool.application.services.bot_orchestrator_service import BotOrchestratorService


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
