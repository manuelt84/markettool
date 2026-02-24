"""Legacy service bundle used by legacy routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class LegacyServices:
    application: Any
    db: Any
    logger: Any
    update_cls: Any
    running_tasks: dict
    execution_tracker: Any
    estado_suscripcion: Callable[..., Any]
    es_administrador: Callable[..., bool]
    normalize_operatoria_payload: Callable[..., Any]
    temporalidades: Any
    ensure_globals_loaded: Callable[..., Any]
    filtrar_activos_por_moneda: Callable[..., Any]
    activos_ref: Any
    compute_lock_ttl: Callable[..., Any]
    acquire_user_lock: Callable[..., Any]
    release_user_lock: Callable[..., Any]
    mark_user_state: Callable[..., Any]
    obtener_opciones_usuario: Callable[..., Any]
    fs_crear_ejecucion: Callable[..., Any]
    fs_marcar_worker: Callable[..., Any]
    fs_finalizar_ejecucion: Callable[..., Any]
    fs_heartbeat: Callable[..., Any]
    user_config_cache: Any
    pytz_module: Any
    set_timezone_state: Callable[..., Any]
    clear_current_request_cfg: Callable[..., Any]
    ocupado_lock: Any
    es_grafico_de_velas: Callable[..., Any]
    analizar_con_yolo: Callable[..., Any]
    descontar_transaccion: Callable[..., Any]
    stop_events_ref: dict
    stop_events_lock: Any
    optimize_records_for_upload: Callable[..., Any]
    ejecutar_recurrente: Callable[..., Any]
    charge_monitoreo_per_call: Callable[..., Any]
    fetch_events_for: Callable[..., Any]
    filter_by_symbol_currencies: Callable[..., Any]
    hash_payload: Callable[..., Any]
    last_hash_ref: dict
    detect_new_results: Callable[..., Any]
    evaluar_evento_para_symbol: Callable[..., Any]
    norm_tf: Callable[..., Any]
    tf_is_enabled: Callable[..., Any]
    load_cache: Callable[..., Any]
    series_to_ms: Callable[..., Any]
    snap_and_dedupe_to_minutes: Callable[..., Any]
    densify_minutes: Callable[..., Any]
    maybe_tick_quote: Callable[..., Any]
    mon_cache_lock: Any
    maybe_refresh_from_gcs: Callable[..., Any]
    fs_touch_monitoreo: Callable[..., Any]
    tf_ms: Callable[..., Any]
    current_closed_bucket_start: Callable[..., Any]
    fetch_historical_range: Callable[..., Any]
    merge_bars_series: Callable[..., Any]
    backfill_internal_gaps: Callable[..., Any]
    bucket_name: str
    indicators_cache: Any
    cache_enabled: bool
    ttl_hours: int
    force_recalc: bool
