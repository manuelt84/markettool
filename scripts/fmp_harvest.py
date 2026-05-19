#!/usr/bin/env python3
"""Controlled FMP data harvester.

Goals:
- Pull historical OHLCV in small windows, as FMP recommends for intraday data.
- Persist normalized payloads locally and optionally in GCS.
- Collect company dashboard fundamentals without mixing them with candle data.

The script is intentionally conservative. Use --dry-run first, then remove it
only after checking the generated plan.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests


FMP_BASE = "https://financialmodelingprep.com/stable/"

INTRADAY_ENDPOINTS = {
    "stock": "historical-chart/{interval}",
    "forex": "historical-chart/{interval}",
    "crypto": "historical-chart/{interval}",
    "commodity": "historical-chart/{interval}",
    "index": "historical-chart/{interval}",
}

EOD_ENDPOINTS = {
    "stock": "historical-price-eod/full",
    "forex": "historical-price-eod/full",
    "crypto": "historical-price-eod/full",
    "commodity": "historical-price-eod/full",
    "index": "historical-price-eod/full",
}

DASHBOARD_ENDPOINTS = {
    "profile": "profile",
    "quote": "quote",
    "market_cap": "market-cap",
    "historical_market_cap": "historical-market-cap",
    "key_metrics_ttm": "key-metrics-ttm",
    "ratios_ttm": "ratios-ttm",
    "financial_scores": "financial-scores",
    "enterprise_values": "enterprise-values",
    "income_statement": "income-statement",
    "balance_sheet": "balance-sheet-statement",
    "cash_flow": "cash-flow-statement",
}

BULK_ENDPOINTS = {
    "key_metrics_ttm": "key-metrics-ttm-bulk",
    "income_statement": "income-statement-bulk",
    "balance_sheet": "balance-sheet-statement-bulk",
}

SYMBOL_ENDPOINTS = {
    "stocks": "stock-list",
    "actively_trading": "actively-trading-list",
    "forex": "forex-list",
    "crypto": "cryptocurrency-list",
    "commodities": "commodities-list",
    "exchanges": "available-exchanges",
}

INTERVAL_TO_DELTA = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "30min": timedelta(minutes=30),
    "1hour": timedelta(hours=1),
    "4hour": timedelta(hours=4),
    "1day": timedelta(days=1),
}

DEFAULT_MAX_BARS_PER_CALL = {
    "1min": 240,
    "5min": 288,
    "15min": 384,
    "30min": 336,
    "1hour": 240,
    "4hour": 180,
    "1day": 365,
}


@dataclass(frozen=True)
class FetchJob:
    endpoint: str
    params: dict[str, Any]
    local_path: Path
    gcs_path: str | None = None
    data_kind: str = "generic"
    interval: str | None = None


class FmpHarvester:
    def __init__(
        self,
        *,
        api_key: str,
        out_dir: Path,
        bucket: str | None,
        throttle_seconds: float,
        dry_run: bool,
        timeout: int = 20,
    ) -> None:
        if not api_key:
            raise SystemExit("FMP_API_KEY is required")
        self.api_key = api_key
        self.out_dir = out_dir
        self.bucket = bucket
        self.throttle_seconds = max(0.0, throttle_seconds)
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = requests.Session()
        self._gcs_client = None

    def _request(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = urljoin(FMP_BASE, endpoint)
        req_params = dict(params)
        req_params["apikey"] = self.api_key
        if self.dry_run:
            safe_params = dict(req_params)
            safe_params["apikey"] = "[REDACTED]"
            return {"dry_run": True, "url": url, "params": safe_params}

        resp = self.session.get(url, params=req_params, timeout=self.timeout)
        if resp.status_code == 402:
            raise RuntimeError(f"FMP plan rejected endpoint: {endpoint}")
        resp.raise_for_status()
        if self.throttle_seconds:
            time.sleep(self.throttle_seconds)
        return resp.json()

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    def _upload_gcs(self, path: Path, gcs_path: str | None) -> None:
        if not self.bucket or not gcs_path or self.dry_run:
            return
        if self._gcs_client is None:
            from google.cloud import storage

            self._gcs_client = storage.Client()
        bucket = self._gcs_client.bucket(self.bucket)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(str(path), content_type="application/json")

    def run_jobs(self, jobs: Iterable[FetchJob]) -> list[dict[str, Any]]:
        manifest: list[dict[str, Any]] = []
        for job in jobs:
            payload = self._request(job.endpoint, job.params)
            normalized = self._normalize_payload(payload)
            self._write_json(job.local_path, normalized)
            self._upload_gcs(job.local_path, job.gcs_path)
            manifest.append(
                {
                    "endpoint": job.endpoint,
                    "params": self._safe_params(job.params),
                    "local_path": str(job.local_path),
                    "gcs_path": job.gcs_path,
                    "rows": len(normalized) if isinstance(normalized, list) else None,
                    "quality": self._quality_report(job, normalized),
                    "dry_run": self.dry_run,
                }
            )
        return manifest

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        return {k: ("[REDACTED]" if "key" in k.lower() else v) for k, v in params.items()}

    @staticmethod
    def _normalize_payload(payload: Any) -> Any:
        if isinstance(payload, dict) and "historical" in payload and isinstance(payload["historical"], list):
            return payload["historical"]
        if isinstance(payload, list):
            return payload
        return payload

    def _quality_report(self, job: FetchJob, payload: Any) -> dict[str, Any]:
        if self.dry_run:
            return {"status": "planned"}
        if job.data_kind != "ohlcv":
            if isinstance(payload, list):
                return {"status": "ok" if payload else "empty", "rows": len(payload)}
            return {"status": "ok" if payload else "empty"}
        if not isinstance(payload, list):
            return {"status": "invalid", "reason": "payload_not_list"}
        return validate_ohlcv(payload, job.interval or "1day")


def parse_date(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fmt_fmp(dt: datetime, date_only: bool = False) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")


def parse_bar_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def validate_ohlcv(rows: list[dict[str, Any]], interval: str) -> dict[str, Any]:
    timestamps: list[datetime] = []
    invalid_ohlc = 0
    invalid_time = 0
    duplicates = 0
    seen: set[int] = set()

    for row in rows:
        if not isinstance(row, dict):
            invalid_ohlc += 1
            continue
        ts = parse_bar_time(row.get("date") or row.get("time") or row.get("t"))
        if ts is None:
            invalid_time += 1
            continue
        ts_key = int(ts.timestamp())
        if ts_key in seen:
            duplicates += 1
        seen.add(ts_key)
        timestamps.append(ts)
        try:
            o = float(row.get("open", row.get("o")))
            h = float(row.get("high", row.get("h")))
            l = float(row.get("low", row.get("l")))
            c = float(row.get("close", row.get("c")))
            if not all(v == v and abs(v) != float("inf") for v in (o, h, l, c)):
                invalid_ohlc += 1
            elif h < max(o, c) or l > min(o, c):
                invalid_ohlc += 1
        except Exception:
            invalid_ohlc += 1

    timestamps = sorted(timestamps)
    gaps = 0
    largest_gap_bars = 0
    expected = INTERVAL_TO_DELTA.get(interval)
    if expected and len(timestamps) >= 2:
        step = expected.total_seconds()
        for prev, cur in zip(timestamps, timestamps[1:]):
            diff = (cur - prev).total_seconds()
            if diff > step * 1.5:
                missing = max(1, int(round(diff / step)) - 1)
                gaps += missing
                largest_gap_bars = max(largest_gap_bars, missing)

    valid_rows = max(0, len(rows) - invalid_ohlc - invalid_time)
    status = "ok"
    if not rows:
        status = "empty"
    elif invalid_ohlc or invalid_time:
        status = "suspect"
    return {
        "status": status,
        "rows": len(rows),
        "valid_rows": valid_rows,
        "invalid_ohlc": invalid_ohlc,
        "invalid_time": invalid_time,
        "duplicates": duplicates,
        "gaps": gaps,
        "largest_gap_bars": largest_gap_bars,
        "first_ts": timestamps[0].isoformat() if timestamps else None,
        "last_ts": timestamps[-1].isoformat() if timestamps else None,
    }


def iter_windows(start: datetime, end: datetime, interval: str, max_bars: int) -> Iterable[tuple[datetime, datetime]]:
    delta = INTERVAL_TO_DELTA[interval]
    span = delta * max(1, max_bars)
    cursor = start
    while cursor < end:
        nxt = min(end, cursor + span)
        yield cursor, nxt
        cursor = nxt + delta


def load_symbols(args: argparse.Namespace, harvester: FmpHarvester) -> list[str]:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file:
        raw = Path(args.symbols_file).read_text(encoding="utf-8")
        return [s.strip().upper() for s in raw.replace(",", "\n").splitlines() if s.strip()]
    if args.source_endpoint:
        payload = harvester._request(args.source_endpoint, {})
        rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
        symbols = []
        for row in rows:
            if isinstance(row, dict) and row.get("symbol"):
                symbols.append(str(row["symbol"]).strip().upper())
        return symbols[: args.limit] if args.limit else symbols
    raise SystemExit("Provide --symbols, --symbols-file, or --source-endpoint")


def build_price_jobs(args: argparse.Namespace, symbols: list[str]) -> list[FetchJob]:
    start = parse_date(args.from_date)
    end = parse_date(args.to_date)
    interval = args.interval
    asset_class = args.asset_class
    max_bars = args.max_bars or DEFAULT_MAX_BARS_PER_CALL.get(interval, 240)
    jobs: list[FetchJob] = []
    for symbol in symbols:
        if interval == "1day":
            endpoint = EOD_ENDPOINTS[asset_class]
            params = {"symbol": symbol, "from": fmt_fmp(start, True), "to": fmt_fmp(end, True)}
            rel = f"fmp/history/{asset_class}/{symbol}/1day/{args.from_date}_{args.to_date}.json"
            jobs.append(FetchJob(endpoint, params, args.out_dir / rel, rel if args.bucket else None, "ohlcv", interval))
            continue
        endpoint = INTRADAY_ENDPOINTS[asset_class].format(interval=interval)
        for window_start, window_end in iter_windows(start, end, interval, max_bars):
            stamp = f"{window_start.strftime('%Y%m%d%H%M')}_{window_end.strftime('%Y%m%d%H%M')}"
            params = {"symbol": symbol, "from": fmt_fmp(window_start), "to": fmt_fmp(window_end)}
            rel = f"fmp/history/{asset_class}/{symbol}/{interval}/{stamp}.json"
            jobs.append(FetchJob(endpoint, params, args.out_dir / rel, rel if args.bucket else None, "ohlcv", interval))
    return jobs


def build_dashboard_jobs(args: argparse.Namespace, symbols: list[str]) -> list[FetchJob]:
    jobs: list[FetchJob] = []
    endpoints = args.datasets.split(",") if args.datasets else DASHBOARD_ENDPOINTS.keys()
    for symbol in symbols:
        for name in endpoints:
            name = name.strip()
            if not name:
                continue
            endpoint = DASHBOARD_ENDPOINTS.get(name)
            if not endpoint:
                raise SystemExit(f"Unknown dashboard dataset: {name}")
            params: dict[str, Any] = {"symbol": symbol}
            if name in {"income_statement", "balance_sheet", "cash_flow", "key_metrics", "ratios"}:
                params["period"] = args.period
                params["limit"] = args.statement_limit
            rel = f"fmp/dashboard/company/{symbol}/{name}.json"
            jobs.append(FetchJob(endpoint, params, args.out_dir / rel, rel if args.bucket else None, "dashboard"))
    return jobs


def build_catalog_jobs(args: argparse.Namespace) -> list[FetchJob]:
    jobs = []
    for name, endpoint in SYMBOL_ENDPOINTS.items():
        rel = f"fmp/catalog/{name}.json"
        jobs.append(FetchJob(endpoint, {}, args.out_dir / rel, rel if args.bucket else None))
    return jobs


def write_manifest(out_dir: Path, manifest: list[dict[str, Any]]) -> Path:
    path = out_dir / "fmp" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled FMP historical/fundamental harvester")
    parser.add_argument("--api-key", default=os.getenv("FMP_API_KEY", ""))
    parser.add_argument("--out-dir", type=Path, default=Path(os.getenv("FMP_HARVEST_OUT_DIR", "./data/harvest")))
    parser.add_argument("--bucket", default=os.getenv("FMP_HARVEST_GCS_BUCKET", ""))
    parser.add_argument("--throttle-seconds", type=float, default=float(os.getenv("FMP_HARVEST_THROTTLE_SECONDS", "0.25")))
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)

    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog", help="Download symbol/catalog endpoints")
    catalog.set_defaults(builder=lambda a, h: build_catalog_jobs(a))

    prices = sub.add_parser("prices", help="Download OHLCV by controlled windows")
    prices.add_argument("--asset-class", choices=sorted(INTRADAY_ENDPOINTS), default="stock")
    prices.add_argument("--interval", choices=sorted(INTERVAL_TO_DELTA), default="1day")
    prices.add_argument("--from-date", required=True)
    prices.add_argument("--to-date", required=True)
    prices.add_argument("--max-bars", type=int)
    prices.add_argument("--symbols")
    prices.add_argument("--symbols-file")
    prices.add_argument("--source-endpoint")
    prices.add_argument("--limit", type=int)
    prices.set_defaults(builder=lambda a, h: build_price_jobs(a, load_symbols(a, h)))

    dashboard = sub.add_parser("dashboard", help="Download company dashboard fundamentals")
    dashboard.add_argument("--symbols")
    dashboard.add_argument("--symbols-file")
    dashboard.add_argument("--source-endpoint")
    dashboard.add_argument("--limit", type=int)
    dashboard.add_argument("--datasets", help="Comma-separated keys from DASHBOARD_ENDPOINTS")
    dashboard.add_argument("--period", choices=["annual", "quarter"], default="annual")
    dashboard.add_argument("--statement-limit", type=int, default=10)
    dashboard.set_defaults(builder=lambda a, h: build_dashboard_jobs(a, load_symbols(a, h)))

    args = parser.parse_args()
    harvester = FmpHarvester(
        api_key=args.api_key,
        out_dir=args.out_dir,
        bucket=args.bucket or None,
        throttle_seconds=args.throttle_seconds,
        dry_run=args.dry_run,
    )
    jobs = args.builder(args, harvester)
    manifest = harvester.run_jobs(jobs)
    manifest_path = write_manifest(args.out_dir, manifest)
    print(json.dumps({"jobs": len(manifest), "manifest": str(manifest_path), "dry_run": args.dry_run}, indent=2))


if __name__ == "__main__":
    main()
