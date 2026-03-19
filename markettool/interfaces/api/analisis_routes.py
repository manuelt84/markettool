"""Analisis API routes."""

from __future__ import annotations

import asyncio
import json
pass
pass
pass
pass
pass
pass
pass
pass
import threading
import time
pass

from flask import Response, jsonify, request, stream_with_context

from markettool.application.use_cases.legacy import LegacyAnalisisUseCase

_bg_loop = None
_bg_thread = None
_bg_lock = threading.Lock()
_bg_ready = threading.Event()


_TERMINAL_STATES = {
    "completed",
    "completado",
    "failed",
    "fallido",
    "stopped",
    "detenido",
    "cancelled",
    "cancelado",
    "canceled",
}


def _sse_event(event: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=True, default=str)
    return f"event: {event}\ndata: {body}\n\n"


def _wants_streaming() -> bool:
    q = str(request.args.get("stream") or "").strip().lower()
    if q in {"1", "true", "yes", "on"}:
        return True

    accept = str(request.headers.get("Accept") or "").lower()
    return "text/event-stream" in accept


def _bg_loop_worker() -> None:
    global _bg_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bg_loop = loop
    _bg_ready.set()
    loop.run_forever()


def _ensure_bg_loop():
    global _bg_thread
    with _bg_lock:
        if _bg_loop is not None and _bg_loop.is_running():
            return _bg_loop
        _bg_ready.clear()
        _bg_thread = threading.Thread(
            target=_bg_loop_worker,
            daemon=True,
            name="analisis-bg-loop",
        )
        _bg_thread.start()

    if not _bg_ready.wait(timeout=5):
        raise RuntimeError("No se pudo iniciar event loop de analisis")
    return _bg_loop

def _run_async_for_request(coro):
    """Run coroutine on a persistent loop so spawned tasks survive HTTP request."""
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()


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

    def _stream_analisis(payload_in: dict):
        payload, status = _run_async_for_request(use_case.ejecutar(payload_in))
        if status != 202:
            yield _sse_event(
                "error",
                {
                    "status": "error",
                    "http_status": status,
                    "payload": payload,
                },
            )
            return

        exec_id = (
            (payload or {}).get("exec_id")
            or (payload or {}).get("execution_id")
            or (payload or {}).get("id")
        )
        if not exec_id:
            yield _sse_event(
                "error",
                {
                    "status": "error",
                    "http_status": 500,
                    "message": "No se recibio exec_id para streaming.",
                    "payload": payload,
                },
            )
            return

        exec_id = str(exec_id)
        yield _sse_event("accepted", {"status": "accepted", "exec_id": exec_id})

        last_state = None
        last_progress_hash = None
        missing_since = None

        while True:
            try:
                snap = db.collection("ejecuciones").document(exec_id).get()
            except Exception as exc:
                yield _sse_event(
                    "warning",
                    {
                        "exec_id": exec_id,
                        "message": "firestore_read_error",
                        "detail": str(exc),
                    },
                )
                time.sleep(1.5)
                continue

            if not snap.exists:
                now_s = time.time()
                if missing_since is None:
                    missing_since = now_s
                elif now_s - missing_since > 20:
                    yield _sse_event(
                        "error",
                        {
                            "exec_id": exec_id,
                            "message": "execution_not_found",
                        },
                    )
                    break

                yield ": waiting_execution_doc\n\n"
                time.sleep(1.0)
                continue

            missing_since = None
            data = snap.to_dict() or {}

            estado = str(data.get("estado") or "").strip().lower()
            if estado and estado != last_state:
                last_state = estado
                yield _sse_event("state", {"exec_id": exec_id, "estado": estado})

            progress = data.get("progress")
            if isinstance(progress, dict):
                p_hash = json.dumps(progress, sort_keys=True, ensure_ascii=True, default=str)
                if p_hash != last_progress_hash:
                    last_progress_hash = p_hash
                    yield _sse_event(
                        "progress",
                        {
                            "exec_id": exec_id,
                            "progress": progress,
                        },
                    )

            if estado in _TERMINAL_STATES:
                resumen = data.get("resumen") if isinstance(data.get("resumen"), dict) else {}
                urls = resumen.get("urls") if isinstance(resumen, dict) else None
                if not isinstance(urls, list):
                    urls = []

                yield _sse_event(
                    "done",
                    {
                        "exec_id": exec_id,
                        "estado": estado,
                        "urls": urls,
                        "resumen": resumen,
                        "error": (
                            (resumen or {}).get("error")
                            or data.get("error")
                            or data.get("message")
                            or ""
                        ),
                    },
                )
                break

            yield ": keepalive\n\n"
            time.sleep(1.0)

    @app.route("/analisis/ejecutar", methods=["POST"])
    def ejecutar_analisis_desde_app():
        payload_in = request.json or {}
        if _wants_streaming():
            return Response(
                stream_with_context(_stream_analisis(payload_in)),
                status=200,
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        payload, status = _run_async_for_request(use_case.ejecutar(payload_in))
        return jsonify(payload), status

    @app.route("/analisis/ejecutar/stream", methods=["POST"])
    def ejecutar_analisis_stream_desde_app():
        payload_in = request.json or {}
        return Response(
            stream_with_context(_stream_analisis(payload_in)),
            status=200,
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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
