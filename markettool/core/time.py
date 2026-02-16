"""Time and timezone helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import pytz


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    idx = pd.to_datetime(out.index, utc=True, errors="coerce")
    out.index = idx
    if out.index.tz is None:
        out.index = out.index.tz_localize(pytz.UTC)
    return out


def get_local_tz() -> pytz.BaseTzInfo:
    return pytz.UTC
