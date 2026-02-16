"""Configuration loading and environment setup."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


@dataclass
class AppConfig:
    storage_format: str = field(default_factory=lambda: os.environ.get("STORAGE_FORMAT", "json").strip().lower())
    fmp_plan: str = field(default_factory=lambda: (os.environ.get("FMP_PLAN") or "premium").strip().lower())
    fmp_api_key: str = field(default_factory=lambda: os.environ.get("FMP_API_KEY", ""))
    http_timeout: int = field(default_factory=lambda: int(os.environ.get("HTTP_TIMEOUT", "10")))
    http_retries: int = field(default_factory=lambda: int(os.environ.get("HTTP_RETRIES", "3")))
    http_backoff: float = field(default_factory=lambda: float(os.environ.get("HTTP_BACKOFF", "1.8")))
    hist_dir: str = field(default_factory=lambda: os.environ.get("HIST_DIR", "historicos"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    econ_chunk_days: int = field(default_factory=lambda: int(os.environ.get("ECON_CHUNK_DAYS", "31")))
    cache_ttl_config: int = field(default_factory=lambda: int(os.environ.get("CACHE_TTL_CONFIG", "600")))
    cache_ttl_historicos: int = field(default_factory=lambda: int(os.environ.get("CACHE_TTL_HISTORICOS", "7200")))
    cache_max_size_historicos: int = field(default_factory=lambda: int(os.environ.get("CACHE_MAX_SIZE_HISTORICOS", "100")))
    cache_warmup_enabled: bool = field(default_factory=lambda: os.environ.get("CACHE_WARMUP_ENABLED", "true").lower() == "true")
    cache_warmup_blocking_startup: bool = field(default_factory=lambda: os.environ.get("CACHE_WARMUP_BLOCKING_STARTUP", "true").lower() == "true")
    cache_warmup_interval_minutes: int = field(default_factory=lambda: int(os.environ.get("CACHE_WARMUP_INTERVAL_MINUTES", "240")))
    cache_warmup_max_ram_percent: int = field(default_factory=lambda: int(os.environ.get("CACHE_WARMUP_MAX_RAM_PERCENT", "80")))
    cache_warmup_concurrency: int = field(default_factory=lambda: int(os.environ.get("CACHE_WARMUP_CONCURRENCY", "16")))
    cache_warmup_news_enabled: bool = field(default_factory=lambda: os.environ.get("CACHE_WARMUP_NEWS_ENABLED", "false").lower() == "true")
    cache_warmup_events_enabled: bool = field(default_factory=lambda: os.environ.get("CACHE_WARMUP_EVENTS_ENABLED", "false").lower() == "true")
    cache_warmup_news_limit: int = field(default_factory=lambda: int(os.environ.get("CACHE_WARMUP_NEWS_LIMIT", "1")))
    cache_warmup_leader_only: bool = field(default_factory=lambda: os.environ.get("CACHE_WARMUP_LEADER_ONLY", "false").lower() == "true")
    investing_scraping_enabled: bool = field(default_factory=lambda: os.environ.get("INVESTING_SCRAPING_ENABLED", "false").lower() == "true")


def early_load_env(argv: list[str] | None = None) -> None:
    """Load .env early, optionally using --env/-env path."""
    if load_dotenv is None:
        return

    args = argv or sys.argv
    env_path = None
    if ("--env" in args) or ("-env" in args):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--env", "-env", dest="env_path", default=".env")
        parsed, _ = parser.parse_known_args(args)
        env_path = parsed.env_path

    if env_path:
        load_dotenv(env_path)
        return

    if os.path.exists(".env"):
        load_dotenv(".env")


def load_config(argv: list[str] | None = None) -> AppConfig:
    """Load env and return a config instance."""
    early_load_env(argv)
    return AppConfig()
