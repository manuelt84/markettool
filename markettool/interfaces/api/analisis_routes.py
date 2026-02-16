"""Analisis API routes."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import time
import uuid
import threading
from typing import Any

import requests
from flask import jsonify, request


def register_analisis_routes(
    app,
    *,
    application,
    db,
    logger,
    running_tasks: dict,
    execution_tracker,
    estado_suscripcion,
    es_administrador,
    normalize_operatoria_payload,
    temporalidades,
    ensure_globals_loaded,
    filtrar_activos_por_moneda,
    activos_ref,
    compute_lock_ttl,
    acquire_user_lock,
    release_user_lock,
    mark_user_state,
    obtener_opciones_usuario,
    fs_crear_ejecucion,
    fs_marcar_worker,
    fs_finalizar_ejecucion,
    fs_heartbeat,
    user_config_cache,
    pytz_module,
    set_timezone_state,
    clear_current_request_cfg,
    ocupado_lock,
    es_grafico_de_velas,
    analizar_con_yolo,
    descontar_transaccion,
    stop_events_ref: dict,
    stop_events_lock,
    optimize_records_for_upload,
    ejecutar_recurrente,
) -> None:
    def _get_stop_evt(exec_id: str):
        with stop_events_lock:
            return stop_events_ref.setdefault(exec_id, threading.Event())

    def _release_stop_evt(exec_id: str) -> None:
        with stop_events_lock:
            stop_events_ref.pop(exec_id, None)

    @app.route("/analisis/ejecutar", methods=["POST"])
    async def ejecutar_analisis_desde_app():
        chat_id_local = None
        lock_id = None
        try:
            data = request.json or {}
            user_id = str(data.get("user_id") or "").strip()
            chat_id = str(data.get("chat_id") or "").strip()
            chat_id_local = chat_id

            if not user_id:
                return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400

            activo = data.get("activo")
            if activo is None:
                return jsonify({"status": "error", "message": "Falta 'activo'"}), 400

            origen = (data.get("origen") or "app").lower()

            n_transacciones_req = 1
            try:
                raw_cfg = None
                if isinstance(data.get("setup"), dict):
                    raw_cfg = data["setup"]
                elif isinstance(data.get("operatoria"), dict):
                    op = data["operatoria"]
                    raw_cfg = op.get("config", op)
                op_cfg_est = normalize_operatoria_payload(raw_cfg) if raw_cfg else None
                tfs_est = (op_cfg_est or {}).get("tfs") or temporalidades

                await asyncio.to_thread(ensure_globals_loaded)
                activos_filtrados_est = await asyncio.to_thread(
                    filtrar_activos_por_moneda, activos_ref, activo
                )
                n_transacciones_req = max(
                    1,
                    len(list(activos_filtrados_est or [])) * len(list(tfs_est or [])),
                )
            except Exception as exc:
                logger.debug(
                    "[analisis/ejecutar] No se pudo estimar n_transacciones, fallback=1: %s",
                    exc,
                )
                n_transacciones_req = 1

            kwargs = {"user_id": user_id} if user_id else {"chat_id": chat_id}
            estado_sub = await estado_suscripcion(
                **kwargs,
                numero_transacciones=n_transacciones_req,
                origen=origen,
            )
            if not es_administrador(user_id or chat_id):
                if estado_sub == "transacciones_insuficientes":
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "code": "INSUFFICIENT_TRANSACTIONS",
                                "message": "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete.",
                            }
                        ),
                        402,
                    )
                if estado_sub != "activa":
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Suscripcion inactiva o insuficiente",
                            }
                        ),
                        403,
                    )

            lock_id = uuid.uuid4().hex
            lock_ttl = compute_lock_ttl(1)
            acquired_lock = await asyncio.to_thread(
                acquire_user_lock,
                user_id=user_id,
                chat_id=chat_id or None,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired_lock:
                return jsonify({"status": "busy", "message": "Ya tienes un analisis en ejecucion."}), 409

            await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id, estado="ocupado")

            opciones_usuario = await obtener_opciones_usuario(user_id, origen="app")
            if (not opciones_usuario) and chat_id:
                try:
                    opciones_usuario = await obtener_opciones_usuario(
                        chat_id, origen="telegram"
                    )
                except Exception:
                    opciones_usuario = opciones_usuario or []

            is_admin = es_administrador(user_id) or (chat_id and es_administrador(chat_id))

            if (not is_admin) and not any(
                o in (opciones_usuario or [])
                for o in ("analisis basico", "analisis premium", "analisis avanzado")
            ):
                return (
                    jsonify({"status": "error", "message": "No tienes permisos para esta operacion"}),
                    403,
                )

            raw_cfg = None
            if isinstance(data.get("setup"), dict):
                raw_cfg = data["setup"]
            elif isinstance(data.get("operatoria"), dict):
                op = data["operatoria"]
                raw_cfg = op.get("config", op)
            op_cfg = normalize_operatoria_payload(raw_cfg) if raw_cfg else None

            exec_id = await asyncio.to_thread(
                fs_crear_ejecucion,
                user_id=user_id,
                chat_id=chat_id or None,
                activos_solicitados=[activo],
                origen="app",
                opciones_usuario=opciones_usuario,
            )

            await asyncio.to_thread(
                fs_marcar_worker,
                exec_id,
                estado="running",
                worker_addr=os.getenv("WORKER_ADDR"),
                detalles_worker={
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "image": os.getenv("DOCKER_IMAGE", "markettool:latest"),
                },
            )

            dummy_update = type(
                "DummyUpdate",
                (),
                {
                    "effective_chat": type("DummyChat", (), {"id": chat_id})(),
                    "callback_query": None,
                    "effective_user": type(
                        "DummyUser", (), {"first_name": "AppUser", "id": chat_id}
                    )(),
                },
            )()
            dummy_context = type("DummyContext", (), {"bot": application.bot})()

            def _load_user_config(uid):
                user_ref = db.collection("user_ids").document(uid)
                cfg_ref = user_ref.collection("user_config").document("current")
                doc_user, doc_cfg = list(db.get_all([user_ref, cfg_ref]))
                tz_name = (doc_user.to_dict() or {}).get("timezone") or "UTC"
                cfg = doc_cfg.to_dict() or {}
                return {"config": cfg, "timezone": tz_name}

            cached_data = user_config_cache.get_or_load(user_id, _load_user_config)
            cfg = cached_data.get("config", {})
            tz_name = cached_data.get("timezone", "UTC")

            try:
                timezone_country = pytz_module.timezone(tz_name)
            except pytz_module.UnknownTimeZoneError:
                timezone_country = pytz_module.UTC
                tz_name = "UTC"
            set_timezone_state(tz_name, timezone_country)

            async def _runner():
                try:
                    await execution_tracker.register(
                        exec_id, user_id or chat_id, "analisis_simbolo"
                    )

                    urls_local = await ejecutar_recurrente(
                        dummy_context,
                        dummy_update,
                        activo,
                        chat_id,
                        opciones_usuario,
                        user_id=user_id,
                        origen="app",
                        exec_id=exec_id,
                        operatoria_cfg=op_cfg,
                        cfg=cfg,
                    )

                    try:
                        snap = await asyncio.to_thread(
                            db.collection("ejecuciones").document(exec_id).get
                        )
                        curr = snap.to_dict() if snap and snap.exists else {}
                        estado_curr = str((curr or {}).get("estado") or "").lower()
                        resumen_curr = (curr or {}).get("resumen") or {}
                        error_curr = str(
                            (resumen_curr or {}).get("error")
                            or (curr or {}).get("error")
                            or (curr or {}).get("message")
                            or ""
                        ).strip()

                        if estado_curr in {
                            "fallido",
                            "failed",
                            "stopped",
                            "detenido",
                            "cancelado",
                            "canceled",
                        } or error_curr:
                            tracker_status = (
                                "cancelled"
                                if estado_curr
                                in {"stopped", "detenido", "cancelado", "canceled"}
                                else "failed"
                            )
                            await execution_tracker.complete(exec_id, tracker_status)
                            return urls_local
                    except Exception:
                        pass

                    await asyncio.to_thread(
                        fs_finalizar_ejecucion, exec_id, "completado", {"urls": urls_local}
                    )
                    await execution_tracker.complete(exec_id, "completed")
                    return urls_local
                except asyncio.CancelledError:
                    await asyncio.to_thread(
                        fs_finalizar_ejecucion,
                        exec_id,
                        "stopped",
                        {"detalle": "detenido_por_usuario"},
                    )
                    await execution_tracker.complete(exec_id, "cancelled")
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        fs_finalizar_ejecucion, exec_id, "fallido", {"error": str(exc)}
                    )
                    await execution_tracker.complete(exec_id, "failed")
                    raise
                finally:
                    running_tasks.pop(exec_id, None)

            task = asyncio.create_task(_runner())
            running_tasks[exec_id] = task

            async def _hb():
                try:
                    while not task.done():
                        await asyncio.sleep(8)
                        await asyncio.to_thread(fs_heartbeat, exec_id)

                        if await execution_tracker.should_cancel(exec_id):
                            logger.warning(
                                "[ExecutionTracker] Cancelacion solicitada para %s",
                                exec_id,
                            )
                            task.cancel()
                            break
                except Exception:
                    pass

            asyncio.create_task(_hb())

            return jsonify({"status": "accepted", "exec_id": exec_id}), 202

        except Exception as exc:
            logger.error("Error en /analisis/ejecutar: %s", exc)
            logging.exception("Error en /analisis/ejecutar")
            try:
                if "exec_id" in locals():
                    await asyncio.to_thread(
                        fs_finalizar_ejecucion, exec_id, "fallido", {"error": str(exc)}
                    )
            except Exception:
                pass
            return jsonify({"status": "error", "message": str(exc)}), 500
        finally:
            try:
                await asyncio.to_thread(
                    mark_user_state, user_id=user_id or chat_id_local, estado="disponible"
                )
                if lock_id:
                    await asyncio.to_thread(
                        release_user_lock,
                        user_id=user_id,
                        chat_id=chat_id_local or None,
                        lock_id=lock_id,
                    )
                if chat_id_local:
                    clear_current_request_cfg(chat_id_local)
            except Exception:
                pass

    @app.route("/analisis/resultados", methods=["GET"])
    def obtener_resultados_analisis():
        try:
            exec_id = request.args.get("exec_id", "").strip()
            mode = request.args.get("mode", "core").strip().lower()

            if not exec_id:
                return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400

            if mode not in ("core", "extended", "full"):
                mode = "core"

            docs = list(
                db.collection("archivos_generados")
                .where("exec_id", "==", exec_id)
                .stream()
            )

            if not docs:
                return (
                    jsonify({"status": "error", "message": f"No results for exec_id={exec_id}"}),
                    404,
                )

            result = {
                "status": "ok",
                "exec_id": exec_id,
                "mode": mode,
                "files": [],
            }

            for doc in docs:
                data = doc.to_dict() or {}
                nombre = data.get("metadata", {}).get("nombre") or data.get("gcs_path", "")

                if not nombre or not nombre.endswith(".json"):
                    continue
                if "_ordenados" not in nombre and "_oportunidades" not in nombre:
                    continue

                file_info = {
                    "id": doc.id,
                    "nombre": nombre,
                    "gcs_path": data.get("gcs_path"),
                    "tipo": data.get("tipo"),
                    "created_at": data.get("created_at"),
                }

                try:
                    signed_url = data.get("metadata", {}).get("signed_url") or data.get(
                        "gcs_path"
                    )
                    if signed_url:
                        resp = requests.get(signed_url, timeout=10)
                        if resp.status_code == 200:
                            payload = resp.json()
                            raw_records = payload if isinstance(payload, list) else [payload]
                            filtered_records = optimize_records_for_upload(
                                raw_records, upload_mode=mode
                            )
                            file_info["records_count"] = len(filtered_records)
                            file_info["size_est_kb"] = len(json.dumps(filtered_records)) / 1024
                            file_info["preview"] = (
                                filtered_records[:5] if filtered_records else []
                            )
                except Exception as exc:
                    logger.debug("[resultados] No se pudo procesar %s: %s", nombre, exc)
                    file_info["error"] = str(exc)

                result["files"].append(file_info)

            return jsonify(result), 200

        except Exception as exc:
            logger.exception("Error en /analisis/resultados")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/analisis/stop", methods=["POST"])
    async def detener_analisis_desde_app():
        try:
            body = request.get_json(force=True) or {}
            exec_id = str(body.get("exec_id") or "").strip()
            if not exec_id:
                return jsonify({"status": "error", "message": "exec_id es obligatorio"}), 400

            task = running_tasks.get(exec_id)
            if not task:
                doc = db.collection("ejecuciones").document(exec_id).get()
                estado = (doc.to_dict() or {}).get("estado")
                if estado in {"stopped", "completed", "fallido"}:
                    return jsonify({"status": "ok", "exec_id": exec_id, "already": estado}), 200
                return (
                    jsonify({"status": "error", "message": "exec_id no encontrado en este worker"}),
                    404,
                )

            await asyncio.to_thread(
                fs_marcar_worker,
                exec_id,
                estado="stop_requested",
                detalles_worker={
                    "stop_requested_at": int(time.time()),
                    "stop_origin": "user/app",
                },
            )

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            await asyncio.to_thread(
                fs_marcar_worker,
                exec_id,
                estado="stopped",
                detalles_worker={"stopped_at": int(time.time()), "stopped_by": "user/app"},
            )

            return jsonify({"status": "ok", "exec_id": exec_id, "stopped": True}), 200

        except Exception as exc:
            logging.exception("Error en /analisis/stop")
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/analisis/imagen", methods=["POST"])
    async def subir_imagen_y_analizar():
        ruta_local = None
        ruta_salida = None
        acquired_lock = False
        exec_id = None
        user_id = None
        chat_id = None

        try:
            form = request.form or {}
            j = request.get_json(silent=True) or {}
            user_id = str(form.get("user_id") or j.get("user_id") or "").strip()
            chat_id = str(form.get("chat_id") or j.get("chat_id") or "").strip()

            if not user_id:
                return jsonify({"status": "error", "message": "user_id es obligatorio"}), 400
            if "imagen" not in request.files:
                return jsonify({"status": "error", "message": "Falta archivo 'imagen'"}), 400

            if ocupado_lock.locked():
                return "Estoy ocupado", 503
            await asyncio.to_thread(ocupado_lock.acquire)
            acquired_lock = True

            estado_sub = await estado_suscripcion(
                user_id=user_id, numero_transacciones=1, origen="app"
            )
            if not es_administrador(user_id or chat_id):
                if estado_sub == "transacciones_insuficientes":
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "code": "INSUFFICIENT_TRANSACTIONS",
                                "message": "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete.",
                            }
                        ),
                        402,
                    )
                if estado_sub != "activa":
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Suscripcion inactiva o insuficiente",
                            }
                        ),
                        403,
                    )

            await asyncio.to_thread(mark_user_state, user_id=user_id or chat_id, estado="ocupado")

            exec_id = (form.get("exec_id") or j.get("exec_id") or uuid.uuid4().hex)
            os.makedirs("imagenes", exist_ok=True)
            os.makedirs("procesadas", exist_ok=True)

            imagen = request.files["imagen"]
            ruta_local = os.path.join("imagenes", f"{exec_id}.jpg")
            await asyncio.to_thread(imagen.save, ruta_local)

            ts = int(time.time())
            db.collection("ejecuciones").document(exec_id).set(
                {
                    "estado": "running",
                    "tipo": "analisis_imagen",
                    "user_id": user_id,
                    "created_at": ts,
                    "updated_at": ts,
                },
                merge=True,
            )

            await asyncio.to_thread(
                fs_marcar_worker,
                exec_id,
                estado="running",
                worker_addr=os.getenv("WORKER_ADDR"),
                detalles_worker={"pid": os.getpid(), "tipo": "imagen", "origen": "app"},
            )

            stop_evt = _get_stop_evt(exec_id)
            running_tasks[exec_id] = asyncio.current_task()

            await execution_tracker.register(exec_id, user_id or chat_id, "analisis_grafico")

            try:
                mark_user_state(user_id=user_id, estado="esperando_grafico_ia")
            except Exception:
                pass

            es_chart = await asyncio.to_thread(es_grafico_de_velas, ruta_local)
            if not es_chart:
                await asyncio.to_thread(fs_marcar_worker, exec_id, estado="fallido")
                db.collection("ejecuciones").document(exec_id).set(
                    {
                        "estado": "fallido",
                        "resumen": {"message": "No parece ser un grafico de velas"},
                        "updated_at": int(time.time()),
                    },
                    merge=True,
                )
                return (
                    jsonify({"status": "error", "message": "No parece ser un grafico de velas"}),
                    400,
                )

            include_tech = es_administrador(user_id or chat_id)

            try:
                res = await asyncio.to_thread(
                    analizar_con_yolo,
                    ruta_local,
                    stop_cb=stop_evt.is_set,
                    include_tech=include_tech,
                    user_id=user_id,
                )
            except TypeError:
                res = await asyncio.to_thread(analizar_con_yolo, ruta_local)

            entradas_payload = {}
            if isinstance(res, tuple) and len(res) == 3:
                ruta_salida, texto_resultado, entradas_payload = res
            elif isinstance(res, tuple) and len(res) == 2:
                ruta_salida, texto_resultado = res
                entradas_payload = {}
            else:
                raise ValueError(
                    f"analizar_con_yolo devolvio formato inesperado: {type(res)} / {res}"
                )

            if stop_evt.is_set():
                raise asyncio.CancelledError()

            if not ruta_salida or not os.path.exists(ruta_salida):
                await asyncio.to_thread(fs_marcar_worker, exec_id, estado="fallido")
                db.collection("ejecuciones").document(exec_id).set(
                    {
                        "estado": "fallido",
                        "resumen": {"message": "No se genero imagen procesada"},
                        "updated_at": int(time.time()),
                    },
                    merge=True,
                )
                return (
                    jsonify({"status": "error", "message": "No se genero imagen procesada"}),
                    500,
                )

            with open(ruta_salida, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode("utf-8")

            try:
                if not es_administrador(user_id or chat_id):
                    success, mensaje = await descontar_transaccion(user_id, 1)
                    if not success:
                        db.collection("ejecuciones").document(exec_id).set(
                            {"billing_warn": mensaje}, merge=True
                        )
            except Exception as cobro_exc:
                logger.warning("[IA] Error en cobro: %s", cobro_exc)

            await asyncio.to_thread(fs_marcar_worker, exec_id, estado="completed")

            resumen = {
                "message": texto_resultado,
                "imagen_base64": img_base64,
                "entradas": entradas_payload or {},
            }

            db.collection("ejecuciones").document(exec_id).set(
                {
                    "estado": "completed",
                    "resumen": resumen,
                    "updated_at": int(time.time()),
                },
                merge=True,
            )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "exec_id": exec_id,
                        "message": texto_resultado,
                        "imagen_base64": img_base64,
                        "entradas": entradas_payload or {},
                    }
                ),
                200,
            )

        except asyncio.CancelledError:
            await asyncio.to_thread(fs_marcar_worker, exec_id, estado="stopped")
            db.collection("ejecuciones").document(exec_id).set(
                {"estado": "stopped", "updated_at": int(time.time())}, merge=True
            )
            return jsonify({"status": "stopped", "exec_id": exec_id}), 200

        except Exception as exc:
            logger.exception("Error en /analisis/imagen")
            if exec_id:
                try:
                    await asyncio.to_thread(
                        fs_marcar_worker, exec_id, estado="fallido", detalles_worker={"error": str(exc)}
                    )
                    db.collection("ejecuciones").document(exec_id).set(
                        {"estado": "fallido", "error": str(exc), "updated_at": int(time.time())},
                        merge=True,
                    )
                except Exception:
                    pass
            return jsonify({"status": "error", "message": str(exc)}), 500

        finally:
            running_tasks.pop(exec_id, None)
            _release_stop_evt(exec_id)
            try:
                await asyncio.to_thread(
                    mark_user_state, user_id=user_id or chat_id, estado="disponible"
                )
            except Exception:
                pass
            try:
                if ruta_local and os.path.exists(ruta_local):
                    os.remove(ruta_local)
                if ruta_salida and os.path.exists(ruta_salida):
                    os.remove(ruta_salida)
            except Exception:
                pass
            if acquired_lock and ocupado_lock.locked():
                try:
                    ocupado_lock.release()
                except Exception:
                    pass
