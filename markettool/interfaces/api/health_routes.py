"""Health and cache status routes - DEPRECATED.

This module is superseded by health.py which provides comprehensive health checking.
Kept only for backward compatibility with routes like /cache-status and /.
"""

from __future__ import annotations

import socket
import time
from flask import jsonify


def register_health_routes(
    app,
    *,
    warmup_start_ref,
    warmup_end_ref,
    levels_hits_ref,
    levels_misses_ref,
    atr_hits_ref,
    atr_misses_ref,
    app_config,
) -> None:
    """
    DEPRECATED: Use health.py register_health_routes instead.
    Only registers /cache-status and / endpoints for backward compatibility.
    """
    
    @app.route("/cache-status", methods=["GET"])
    def cache_status():
        warmup_status = "not started"
        warmup_time_taken = None

        warmup_start_time = warmup_start_ref()
        warmup_end_time = warmup_end_ref()

        if warmup_start_time is not None:
            if warmup_end_time is not None:
                warmup_status = "completed"
                warmup_time_taken = warmup_end_time - warmup_start_time
            else:
                warmup_status = "in progress"
                warmup_time_taken = time.time() - warmup_start_time

        niveles_hits = levels_hits_ref()
        niveles_misses = levels_misses_ref()
        atr_hits = atr_hits_ref()
        atr_misses = atr_misses_ref()

        return jsonify({
            "status": "ok",
            "instance": socket.gethostname(),
            "warmup": {
                "status": warmup_status,
                "start_time": warmup_start_time,
                "end_time": warmup_end_time,
                "elapsed_seconds": warmup_time_taken,
            },
            "cache_stats": {
                "niveles_hits": niveles_hits,
                "niveles_misses": niveles_misses,
                "niveles_hit_rate": round(100 * niveles_hits / max(1, niveles_hits + niveles_misses), 1),
                "atr_hits": atr_hits,
                "atr_misses": atr_misses,
                "atr_hit_rate": round(100 * atr_hits / max(1, atr_hits + atr_misses), 1),
            },
            "warmup_config": {
                "enabled": app_config.cache_warmup_enabled,
                "blocking_startup": app_config.cache_warmup_blocking_startup,
                "leader_only": app_config.cache_warmup_leader_only,
                "concurrency": app_config.cache_warmup_concurrency,
                "max_ram_percent": app_config.cache_warmup_max_ram_percent,
            },
        }), 200

    @app.route("/", methods=["GET"])
    def index():
        return "El bot esta funcionando", 200
