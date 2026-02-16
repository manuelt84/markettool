"""HTTP session factory with retry/backoff."""

from __future__ import annotations

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests


def build_session(retries: int, backoff: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=50, pool_connections=50)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
