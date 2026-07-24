"""Encrypted broker session storage for backend-owned execution."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_store_path() -> Path:
    raw = os.getenv("MARKETTOOL_BOT_SESSION_STORE_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path("data") / "broker_sessions.json"


def _default_key_path() -> Path:
    raw = os.getenv("MARKETTOOL_BOT_SESSION_KEY_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path("data") / "broker_session.key"


def _load_or_create_fernet_key() -> bytes:
    env_key = os.getenv("MARKETTOOL_BOT_SESSION_KEY", "").strip()
    if env_key:
        raw = env_key.encode("utf-8")
        try:
            Fernet(raw)
            return raw
        except Exception:
            digest = hashlib.sha256(raw).digest()
            return base64.urlsafe_b64encode(digest)

    path = _default_key_path()
    if path.exists():
        return path.read_bytes().strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return key


class BrokerSessionStore:
    def __init__(self, path: Path | None = None, key: bytes | None = None) -> None:
        self.path = path or _default_store_path()
        self._fernet = Fernet(key or _load_or_create_fernet_key())
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return {"sessions": {}}
            data.setdefault("sessions", {})
            return data
        except Exception:
            return {"sessions": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        tmp.replace(self.path)

    def _session_id(self, user_id: str, broker: str, account_hint: str = "default") -> str:
        base = f"{user_id}:{broker}:{account_hint}".encode("utf-8")
        return hashlib.sha256(base).hexdigest()[:24]

    def save_session(
        self,
        *,
        user_id: str,
        broker: str,
        session_payload: dict[str, Any],
        account_hint: str = "default",
        expires_at: int | None = None,
    ) -> dict[str, Any]:
        session_id = self._session_id(user_id, broker, account_hint)
        encrypted = self._fernet.encrypt(json.dumps(session_payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        meta = {
            "id": session_id,
            "user_id": user_id,
            "broker": broker.lower(),
            "account_hint": account_hint,
            "created_at": _now_ms(),
            "updated_at": _now_ms(),
            "expires_at": expires_at,
            "encrypted_payload": encrypted,
        }
        with self._lock:
            data = self._load()
            existing = data["sessions"].get(session_id) or {}
            if existing.get("created_at"):
                meta["created_at"] = existing["created_at"]
            data["sessions"][session_id] = meta
            self._save(data)
        public = dict(meta)
        public.pop("encrypted_payload", None)
        return public

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            raw = self._load()["sessions"].get(session_id)
        if not raw:
            return None
        expires_at = raw.get("expires_at")
        if expires_at and int(expires_at) <= _now_ms():
            return None
        encrypted = str(raw.get("encrypted_payload") or "")
        if not encrypted:
            return None
        decrypted = self._fernet.decrypt(encrypted.encode("utf-8"))
        payload = json.loads(decrypted.decode("utf-8"))
        return payload if isinstance(payload, dict) else None

    def get_latest_session(self, user_id: str, broker: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            sessions = list(self._load()["sessions"].values())
        candidates = [
            item for item in sessions
            if item.get("user_id") == user_id and str(item.get("broker") or "").lower() == broker.lower()
        ]
        candidates.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
        for item in candidates:
            payload = self.get_session(str(item.get("id")))
            if payload:
                return str(item.get("id")), payload
        return None

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._load()["sessions"].values())
        if user_id:
            sessions = [item for item in sessions if item.get("user_id") == user_id]
        public = []
        for item in sessions:
            clone = dict(item)
            clone.pop("encrypted_payload", None)
            public.append(clone)
        return sorted(public, key=lambda item: int(item.get("updated_at") or 0), reverse=True)


_STORE: BrokerSessionStore | None = None


def get_broker_session_store() -> BrokerSessionStore:
    global _STORE
    if _STORE is None:
        _STORE = BrokerSessionStore()
    return _STORE
