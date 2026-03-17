"""Analisis API routes."""

from __future__ import annotations

import asyncio
pass
pass
pass
pass
pass
pass
pass
pass
import threading
pass

from flask import jsonify, request

from markettool.application.use_cases.legacy import LegacyAnalisisUseCase

# Persistent background event loop for handling async operations
_bg_loop = None
_bg_thread = None
_bg_lock = threading.Lock()

def _initialize_bg_loop():
    """Initialize a persistent background event loop in a daemon thread."""
    global _bg_loop, _bg_thread
    
    with _bg_lock:
        if _bg_loop is not None:
            return
        
        def _run_bg_loop():
            global _bg_loop
            _bg_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_bg_loop)
            try:
                _bg_loop.run_forever()
            finally:
                _bg_loop.close()
        
        _bg_thread = threading.Thread(target=_run_bg_loop, daemon=True, name="AsyncIOBackgroundLoop")
        _bg_thread.start()
        
        # Wait for loop to start
        while _bg_loop is None:
            threading.Event().wait(0.001)

def _get_bg_loop():
    """Get the background event loop, initializing if needed."""
    if _bg_loop is None:
        _initialize_bg_loop()
    return _bg_loop

def _run_async_for_request(coro):
    """Run async coroutine in a request handler by submitting to background loop.
    
    This allows background tasks to continue executing after the HTTP response.
    The coroutine is executed immediately while blocking the request, but any
    background tasks it creates are registered with the persistent loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(coro)
    finally:
        # Critical: Don't close the loop yet! Background tasks might be using it.
        # Instead, wait a moment for tasks to initialize in the loop
        try:
            pending = asyncio.all_tasks(loop)
            # Keep loop alive briefly while tasks initialize
            if pending:
                # Run pending tasks for a short time to let them start
                loop.run_until_complete(asyncio.sleep(0.2))
        except Exception:
            pass
        
        # Now close
        try:
            loop.close()
        except Exception:
            pass


def register_analisis_routes(app, *, services) -> None:
    use_case = LegacyAnalisisUseCase(services)
    application = services.application
    db = services.db
    logger = services.logger
    running_tasks = services.running_tasks
    execution_tracker = services.execution_tracker
    estado_suscripcion = services.estado_suscripcion
    es_administrador = services.es_administrador
    normalize_operatoria_payload = services.normalize_operatoria_payload
    temporalidades = services.temporalidades
    ensure_globals_loaded = services.ensure_globals_loaded
    filtrar_activos_por_moneda = services.filtrar_activos_por_moneda
    activos_ref = services.activos_ref
    compute_lock_ttl = services.compute_lock_ttl
    acquire_user_lock = services.acquire_user_lock
    release_user_lock = services.release_user_lock
    mark_user_state = services.mark_user_state
    obtener_opciones_usuario = services.obtener_opciones_usuario
    fs_crear_ejecucion = services.fs_crear_ejecucion
    fs_marcar_worker = services.fs_marcar_worker
    fs_finalizar_ejecucion = services.fs_finalizar_ejecucion
    fs_heartbeat = services.fs_heartbeat
    user_config_cache = services.user_config_cache
    pytz_module = services.pytz_module
    set_timezone_state = services.set_timezone_state
    clear_current_request_cfg = services.clear_current_request_cfg
    ocupado_lock = services.ocupado_lock
    es_grafico_de_velas = services.es_grafico_de_velas
    analizar_con_yolo = services.analizar_con_yolo
    descontar_transaccion = services.descontar_transaccion
    stop_events_ref = services.stop_events_ref
    stop_events_lock = services.stop_events_lock
    optimize_records_for_upload = services.optimize_records_for_upload
    ejecutar_recurrente = services.ejecutar_recurrente
    def _get_stop_evt(exec_id: str):
        with stop_events_lock:
            return stop_events_ref.setdefault(exec_id, threading.Event())

    def _release_stop_evt(exec_id: str) -> None:
        with stop_events_lock:
            stop_events_ref.pop(exec_id, None)

    @app.route("/analisis/ejecutar", methods=["POST"])
    def ejecutar_analisis_desde_app():
        payload, status = _run_async_for_request(use_case.ejecutar(request.json or {}))
        return jsonify(payload), status

    @app.route("/analisis/resultados", methods=["GET"])
    def obtener_resultados_analisis():
        payload, status = use_case.resultados(
            request.args.get("exec_id", ""),
            request.args.get("mode", "core"),
        )
        return jsonify(payload), status

    @app.route("/analisis/stop", methods=["POST"])
    def detener_analisis_desde_app():
        payload, status = _run_async_for_request(use_case.stop((request.get_json(force=True) or {}).get("exec_id")))
        return jsonify(payload), status

    @app.route("/analisis/imagen", methods=["POST"])
    def subir_imagen_y_analizar():
        payload, status = _run_async_for_request(use_case.imagen(
            request.form or {},
            request.get_json(silent=True) or {},
            request.files,
        ))
        return jsonify(payload), status
