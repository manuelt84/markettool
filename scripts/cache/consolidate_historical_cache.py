#!/usr/bin/env python3
"""Consolidate MarketTool historical cache snapshots from multiple pods.

The bake scripts copy cache folders from running containers into
backup/pod-cache/<timestamp>/<container>. This script reads those snapshots plus
the current project cache, merges compatible OHLCV files per symbol/timeframe,
writes the best consolidated historicos/*.json, and emits quality manifests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TF_STEPS_SECONDS = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1hour": 3600,
    "4hour": 14400,
    "1day": 86400,
    "1week": 604800,
    "1month": 2592000,
}


def normalize_tf(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1hour",
        "4h": "4hour",
        "1d": "1day",
        "1w": "1week",
        "1mo": "1month",
    }
    return aliases.get(raw, raw)


def safe_symbol_for_filename(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(symbol))


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Candidate:
    path: Path
    symbol: str
    tf: str
    rows: list[dict[str, Any]]


def extract_symbol_tf(path: Path, rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    name = path.name
    if name.endswith(".manifest.json") or not name.endswith(".json"):
        return None
    stem = name[:-5]
    if "__" in stem:
        symbol, tf = stem.rsplit("__", 1)
        return symbol.upper(), normalize_tf(tf)
    if "_" in stem:
        symbol, tf = stem.rsplit("_", 1)
        return symbol.upper(), normalize_tf(tf)
    if rows:
        first = rows[0]
        symbol = first.get("symbol") or first.get("ticker")
        tf = first.get("tf") or first.get("timeframe") or first.get("temporalidad")
        if symbol and tf:
            return str(symbol).upper(), normalize_tf(str(tf))
    return None


def load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else []
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("payload") or payload.get("rows") or []
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        time_value = item.get("time", item.get("date"))
        parsed = parse_time(time_value)
        if not parsed:
            continue
        row = {"time": format_time(parsed)}
        for column in ("open", "high", "low", "close", "volume"):
            value = item.get(column)
            if value is None:
                value = 0 if column == "volume" else None
            try:
                row[column] = float(value) if value is not None else None
            except (TypeError, ValueError):
                row[column] = None
        if row["close"] is None:
            continue
        rows.append(row)
    return rows


def manifest(symbol: str, tf: str, rows: list[dict[str, Any]], source_paths: list[str]) -> dict[str, Any]:
    times = sorted(parse_time(row["time"]) for row in rows if parse_time(row.get("time")))
    times = [t for t in times if t is not None]
    step_seconds = TF_STEPS_SECONDS.get(normalize_tf(tf))
    gap_count = 0
    max_gap_seconds = 0.0
    coverage_ratio = 0.0
    if times:
        unique_times = sorted(set(times))
        if len(unique_times) > 1 and step_seconds:
            diffs = [(b - a).total_seconds() for a, b in zip(unique_times, unique_times[1:])]
            gaps = [diff for diff in diffs if diff > step_seconds * 1.5]
            gap_count = len(gaps)
            max_gap_seconds = max(diffs) if diffs else 0.0
            expected_rows = int((unique_times[-1] - unique_times[0]).total_seconds() // step_seconds) + 1
            coverage_ratio = min(1.0, len(unique_times) / expected_rows) if expected_rows > 0 else 1.0
        else:
            coverage_ratio = 1.0
    return {
        "schema_version": 1,
        "symbol": symbol.upper(),
        "tf": normalize_tf(tf),
        "rows": len(rows),
        "first_ts": format_time(times[0]) if times else None,
        "last_ts": format_time(times[-1]) if times else None,
        "expected_step_ms": step_seconds * 1000 if step_seconds else None,
        "gap_count": gap_count,
        "max_gap_ms": int(max_gap_seconds * 1000),
        "coverage_ratio": round(coverage_ratio, 6),
        "source": "multi_pod_consolidated",
        "source_files": source_paths[:50],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def score_manifest(item: dict[str, Any]) -> tuple[Any, ...]:
    last_ts = parse_time(item.get("last_ts")) or datetime.fromtimestamp(0, tz=timezone.utc)
    return (
        last_ts.timestamp(),
        float(item.get("coverage_ratio") or 0),
        -int(item.get("gap_count") or 0),
        int(item.get("rows") or 0),
    )


def find_historicos_dirs(project_root: Path, snapshot_roots: list[Path]) -> list[Path]:
    dirs: list[Path] = []
    current = project_root / "historicos"
    if current.is_dir():
        dirs.append(current)
    for root in snapshot_roots:
        if not root.exists():
            continue
        if root.name == "historicos" and root.is_dir():
            dirs.append(root)
            continue
        for hist_dir in root.rglob("historicos"):
            if hist_dir.is_dir():
                dirs.append(hist_dir)
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in dirs:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(item)
    return unique


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=isinstance(payload, dict), indent=2 if isinstance(payload, dict) else None)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", type=Path)
    parser.add_argument("--snapshot-root", action="append", default=[], type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-rows", type=int, default=int(os.getenv("MAX_HISTORICO_CACHE_ROWS", "0")))
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    snapshot_roots = [root.resolve() for root in args.snapshot_root]
    historicos_dirs = find_historicos_dirs(project_root, snapshot_roots)
    if not historicos_dirs:
        print("No historicos directories found.")
        return 0

    groups: dict[tuple[str, str], list[Candidate]] = {}
    for hist_dir in historicos_dirs:
        for path in hist_dir.glob("*.json"):
            rows = load_rows(path)
            parsed = extract_symbol_tf(path, rows)
            if not parsed or not rows:
                continue
            symbol, tf = parsed
            groups.setdefault((symbol, tf), []).append(Candidate(path=path, symbol=symbol, tf=tf, rows=rows))

    out_dir = project_root / "historicos"
    changed = 0
    total_rows = 0
    for (symbol, tf), candidates in sorted(groups.items()):
        merged_by_time: dict[str, dict[str, Any]] = {}
        source_paths: list[str] = []
        for candidate in candidates:
            source_paths.append(str(candidate.path))
            for row in candidate.rows:
                merged_by_time[row["time"]] = row
        rows = [merged_by_time[key] for key in sorted(merged_by_time)]
        if args.max_rows and len(rows) > args.max_rows:
            rows = rows[-args.max_rows:]
        item_manifest = manifest(symbol, tf, rows, source_paths)
        current_path = out_dir / f"{safe_symbol_for_filename(symbol)}__{normalize_tf(tf)}.json"
        current_rows = load_rows(current_path) if current_path.exists() else []
        current_manifest = manifest(symbol, tf, current_rows, [str(current_path)]) if current_rows else {}
        total_rows += len(rows)
        if current_rows and score_manifest(current_manifest) > score_manifest(item_manifest):
            continue
        if args.dry_run:
            print(f"would write {symbol}/{tf}: rows={len(rows)} sources={len(candidates)} gaps={item_manifest['gap_count']} coverage={item_manifest['coverage_ratio']}")
            changed += 1
            continue
        if args.backup_dir and current_path.exists():
            backup_path = args.backup_dir / "historicos" / current_path.name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current_path, backup_path)
        atomic_json(current_path, rows)
        atomic_json(current_path.with_suffix(".manifest.json"), item_manifest)
        changed += 1

    mode = "dry-run" if args.dry_run else "written"
    print(f"{mode}: groups={len(groups)} changed={changed} scanned_dirs={len(historicos_dirs)} merged_rows={total_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
