from __future__ import annotations

import time

from cryptography.fernet import Fernet

from markettool.application.services.broker_session_store import BrokerSessionStore


def test_broker_session_store_encrypts_payload(tmp_path):
    store = BrokerSessionStore(tmp_path / "sessions.json", key=Fernet.generate_key())

    saved = store.save_session(
        user_id="u1",
        broker="libertex",
        session_payload={
            "csrf_token": "token-123",
            "session_cookies": {"SID": "cookie-123"},
            "base_url": "https://app.libertex.org",
        },
    )

    raw = (tmp_path / "sessions.json").read_text(encoding="utf-8")
    assert "token-123" not in raw
    assert "cookie-123" not in raw
    assert saved["broker"] == "libertex"

    restored = store.get_session(saved["id"])
    assert restored is not None
    assert restored["csrf_token"] == "token-123"
    assert restored["session_cookies"]["SID"] == "cookie-123"


def test_broker_session_store_get_latest(tmp_path):
    store = BrokerSessionStore(tmp_path / "sessions.json", key=Fernet.generate_key())
    store.save_session(user_id="u1", broker="libertex", account_hint="a", session_payload={"csrf_token": "a"})
    time.sleep(0.002)
    second = store.save_session(user_id="u1", broker="libertex", account_hint="b", session_payload={"csrf_token": "b"})

    latest = store.get_latest_session("u1", "libertex")

    assert latest is not None
    assert latest[0] == second["id"]
    assert latest[1]["csrf_token"] == "b"
