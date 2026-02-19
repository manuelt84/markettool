"""Legacy analisis use case."""

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
from typing import Tuple

import requests


class LegacyAnalisisUseCase:
    def __init__(self, services):
        self._services = services

    async def ejecutar(self, data: dict | None) -> Tuple[dict, int]:
        chat_id_local = None
        lock_id = None
        data = data or {}

        try:
            user_id = str(data.get("user_id") or "").strip()
            chat_id = str(data.get("chat_id") or "").strip()
            chat_id_local = chat_id

            if not user_id:
                return {"status": "error", "message": "user_id es obligatorio"}, 400

            activo = data.get("activo")
            if activo is None:
                return {"status": "error", "message": "Falta 'activo'"}, 400

            origen = (data.get("origen") or "app").lower()

            n_transacciones_req = 1
            try:
                raw_cfg = None
                if isinstance(data.get("setup"), dict):
                    raw_cfg = data["setup"]
                elif isinstance(data.get("operatoria"), dict):
                    op = data["operatoria"]
                    raw_cfg = op.get("config", op)
                op_cfg_est = self._services.normalize_operatoria_payload(raw_cfg) if raw_cfg else None
                tfs_est = (op_cfg_est or {}).get("tfs") or self._services.temporalidades

                await asyncio.to_thread(self._services.ensure_globals_loaded)
                activos_filtrados_est = await asyncio.to_thread(
                    self._services.filtrar_activos_por_moneda, self._services.activos_ref, activo
                )
                n_transacciones_req = max(
                    1,
                    len(list(activos_filtrados_est or [])) * len(list(tfs_est or [])),
                )
            except Exception as exc:
                self._services.logger.debug(
                    "[analisis/ejecutar] No se pudo estimar n_transacciones, fallback=1: %s",
                    exc,
                )
                n_transacciones_req = 1

            kwargs = {"user_id": user_id} if user_id else {"chat_id": chat_id}
            estado_sub = await self._services.estado_suscripcion(
                **kwargs,
                numero_transacciones=n_transacciones_req,
                origen=origen,
            )
            if not self._services.es_administrador(user_id or chat_id):
                if estado_sub == "transacciones_insuficientes":
                    return {
                        "status": "error",
                        "code": "INSUFFICIENT_TRANSACTIONS",
                        "message": "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete.",
                    }, 402
                if estado_sub != "activa":
                    return {
                        "status": "error",
                        "message": "Suscripcion inactiva o insuficiente",
                    }, 403

            lock_id = uuid.uuid4().hex
            lock_ttl = self._services.compute_lock_ttl(1)
            acquired_lock = await asyncio.to_thread(
                self._services.acquire_user_lock,
                user_id=user_id,
                chat_id=chat_id or None,
                lock_id=lock_id,
                ttl_seconds=lock_ttl,
            )
            if not acquired_lock:
                return {"status": "busy", "message": "Ya tienes un analisis en ejecucion."}, 409

            await asyncio.to_thread(
                self._services.mark_user_state, user_id=user_id or chat_id, estado="ocupado"
            )

            opciones_usuario = await self._services.obtener_opciones_usuario(user_id, origen="app")
            if (not opciones_usuario) and chat_id:
                try:
                    opciones_usuario = await self._services.obtener_opciones_usuario(
                        chat_id, origen="telegram"
                    )
                except Exception:
                    opciones_usuario = opciones_usuario or []

            is_admin = self._services.es_administrador(user_id) or (
                chat_id and self._services.es_administrador(chat_id)
            )

            if (not is_admin) and not any(
                o in (opciones_usuario or [])
                for o in ("analisis basico", "analisis premium", "analisis avanzado")
            ):
                return {"status": "error", "message": "No tienes permisos para esta operacion"}, 403

            raw_cfg = None
            if isinstance(data.get("setup"), dict):
                raw_cfg = data["setup"]
            elif isinstance(data.get("operatoria"), dict):
                op = data["operatoria"]
                raw_cfg = op.get("config", op)
            op_cfg = self._services.normalize_operatoria_payload(raw_cfg) if raw_cfg else None

            exec_id = await asyncio.to_thread(
                self._services.fs_crear_ejecucion,
                user_id=user_id,
                chat_id=chat_id or None,
                activos_solicitados=[activo],
                origen="app",
                opciones_usuario=opciones_usuario,
            )

            await asyncio.to_thread(
                self._services.fs_marcar_worker,
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
            dummy_context = type("DummyContext", (), {"bot": self._services.application.bot})()

            def _load_user_config(uid):
                user_ref = self._services.db.collection("user_ids").document(uid)
                cfg_ref = user_ref.collection("user_config").document("current")
                doc_user, doc_cfg = list(self._services.db.get_all([user_ref, cfg_ref]))
                tz_name = (doc_user.to_dict() or {}).get("timezone") or "UTC"
                cfg = doc_cfg.to_dict() or {}
                return {"config": cfg, "timezone": tz_name}

            cached_data = self._services.user_config_cache.get_or_load(user_id, _load_user_config)
            cfg = cached_data.get("config", {})
            tz_name = cached_data.get("timezone", "UTC")

            try:
                timezone_country = self._services.pytz_module.timezone(tz_name)
            except self._services.pytz_module.UnknownTimeZoneError:
                timezone_country = self._services.pytz_module.UTC
                tz_name = "UTC"
            self._services.set_timezone_state(tz_name, timezone_country)

            async def _runner():
                try:
                    await self._services.execution_tracker.register(
                        exec_id, user_id or chat_id, "analisis_simbolo"
                    )

                    urls_local = await self._services.ejecutar_recurrente(
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
                            self._services.db.collection("ejecuciones").document(exec_id).get
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
                            await self._services.execution_tracker.complete(exec_id, tracker_status)
                            return urls_local
                    except Exception:
                        pass

                    await asyncio.to_thread(
                        self._services.fs_finalizar_ejecucion,
                        exec_id,
                        "completado",
                        {"urls": urls_local},
                    )
                    await self._services.execution_tracker.complete(exec_id, "completed")
                    return urls_local
                except asyncio.CancelledError:
                    await asyncio.to_thread(
                        self._services.fs_finalizar_ejecucion,
                        exec_id,
                        "stopped",
                        {"detalle": "detenido_por_usuario"},
                    )
                    await self._services.execution_tracker.complete(exec_id, "cancelled")
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self._services.fs_finalizar_ejecucion,
                        exec_id,
                        "fallido",
                        {"error": str(exc)},
                    )
                    await self._services.execution_tracker.complete(exec_id, "failed")
                    raise
                finally:
                    self._services.running_tasks.pop(exec_id, None)

            task = asyncio.create_task(_runner())
            self._services.running_tasks[exec_id] = task

            async def _hb():
                try:
                    while not task.done():
                        await asyncio.sleep(8)
                        await asyncio.to_thread(self._services.fs_heartbeat, exec_id)

                        if await self._services.execution_tracker.should_cancel(exec_id):
                            self._services.logger.warning(
                                "[ExecutionTracker] Cancelacion solicitada para %s",
                                exec_id,
                            )
                            task.cancel()
                            break
                except Exception:
                    pass

            asyncio.create_task(_hb())

            return {"status": "accepted", "exec_id": exec_id}, 202

        except Exception as exc:
            self._services.logger.error("Error en /analisis/ejecutar: %s", exc)
            logging.exception("Error en /analisis/ejecutar")
            try:
                if "exec_id" in locals():
                    await asyncio.to_thread(
                        self._services.fs_finalizar_ejecucion,
                        exec_id,
                        "fallido",
                        {"error": str(exc)},
                    )
            except Exception:
                pass
            return {"status": "error", "message": str(exc)}, 500
        finally:
            try:
                await asyncio.to_thread(
                    self._services.mark_user_state,
                    user_id=user_id or chat_id_local,
                    estado="disponible",
                )
                if lock_id:
                    await asyncio.to_thread(
                        self._services.release_user_lock,
                        user_id=user_id,
                        chat_id=chat_id_local or None,
                        lock_id=lock_id,
                    )
                if chat_id_local:
                    self._services.clear_current_request_cfg(chat_id_local)
            except Exception:
                pass

    def resultados(self, exec_id: str, mode: str) -> Tuple[dict, int]:
        try:
            exec_id = (exec_id or "").strip()
            mode = (mode or "core").strip().lower()

            if not exec_id:
                return {"status": "error", "message": "exec_id es obligatorio"}, 400

            if mode not in ("core", "extended", "full"):
                mode = "core"

            docs = list(
                self._services.db.collection("archivos_generados")
                .where("exec_id", "==", exec_id)
                .stream()
            )

            if not docs:
                return {
                    "status": "error",
                    "message": f"No results for exec_id={exec_id}",
                }, 404

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
                            filtered_records = self._services.optimize_records_for_upload(
                                raw_records, upload_mode=mode
                            )
                            file_info["records_count"] = len(filtered_records)
                            file_info["size_est_kb"] = len(json.dumps(filtered_records)) / 1024
                            file_info["preview"] = filtered_records[:5] if filtered_records else []
                except Exception as exc:
                    self._services.logger.debug(
                        "[resultados] No se pudo procesar %s: %s", nombre, exc
                    )
                    file_info["error"] = str(exc)

                result["files"].append(file_info)

            return result, 200

        except Exception as exc:
            self._services.logger.exception("Error en /analisis/resultados")
            return {"status": "error", "message": str(exc)}, 500

    async def stop(self, exec_id: str) -> Tuple[dict, int]:
        try:
            exec_id = str(exec_id or "").strip()
            if not exec_id:
                return {"status": "error", "message": "exec_id es obligatorio"}, 400

            task = self._services.running_tasks.get(exec_id)
            if not task:
                doc = self._services.db.collection("ejecuciones").document(exec_id).get()
                estado = (doc.to_dict() or {}).get("estado")
                if estado in {"stopped", "completed", "fallido"}:
                    return {"status": "ok", "exec_id": exec_id, "already": estado}, 200
                return {"status": "error", "message": "exec_id no encontrado en este worker"}, 404

            await asyncio.to_thread(
                self._services.fs_marcar_worker,
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
                self._services.fs_marcar_worker,
                exec_id,
                estado="stopped",
                detalles_worker={
                    "stopped_at": int(time.time()),
                    "stopped_by": "user/app",
                },
            )

            return {"status": "ok", "exec_id": exec_id, "stopped": True}, 200

        except Exception as exc:
            logging.exception("Error en /analisis/stop")
            return {"status": "error", "message": str(exc)}, 500

    async def imagen(self, form, json_body, files) -> Tuple[dict, int]:
        ruta_local = None
        ruta_salida = None
        acquired_lock = False
        exec_id = None
        user_id = None
        chat_id = None

        try:
            form = form or {}
            json_body = json_body or {}
            user_id = str(form.get("user_id") or json_body.get("user_id") or "").strip()
            chat_id = str(form.get("chat_id") or json_body.get("chat_id") or "").strip()

            if not user_id:
                return {"status": "error", "message": "user_id es obligatorio"}, 400
            if "imagen" not in files:
                return {"status": "error", "message": "Falta archivo 'imagen'"}, 400

            if self._services.ocupado_lock.locked():
                return "Estoy ocupado", 503
            await asyncio.to_thread(self._services.ocupado_lock.acquire)
            acquired_lock = True

            estado_sub = await self._services.estado_suscripcion(
                user_id=user_id, numero_transacciones=1, origen="app"
            )
            if not self._services.es_administrador(user_id or chat_id):
                if estado_sub == "transacciones_insuficientes":
                    return {
                        "status": "error",
                        "code": "INSUFFICIENT_TRANSACTIONS",
                        "message": "No cuenta con la cuota de transacciones requerida. Por favor, adquiere un paquete.",
                    }, 402
                if estado_sub != "activa":
                    return {"status": "error", "message": "Suscripcion inactiva o insuficiente"}, 403

            await asyncio.to_thread(
                self._services.mark_user_state, user_id=user_id or chat_id, estado="ocupado"
            )

            exec_id = (form.get("exec_id") or json_body.get("exec_id") or uuid.uuid4().hex)
            os.makedirs("imagenes", exist_ok=True)
            os.makedirs("procesadas", exist_ok=True)

            imagen = files["imagen"]
            ruta_local = os.path.join("imagenes", f"{exec_id}.jpg")
            await asyncio.to_thread(imagen.save, ruta_local)

            ts = int(time.time())
            self._services.db.collection("ejecuciones").document(exec_id).set(
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
                self._services.fs_marcar_worker,
                exec_id,
                estado="running",
                worker_addr=os.getenv("WORKER_ADDR"),
                detalles_worker={"pid": os.getpid(), "tipo": "imagen", "origen": "app"},
            )

            stop_evt = self._services.stop_events_ref.setdefault(exec_id, threading.Event())
            self._services.running_tasks[exec_id] = asyncio.current_task()

            await self._services.execution_tracker.register(exec_id, user_id or chat_id, "analisis_grafico")

            try:
                self._services.mark_user_state(user_id=user_id, estado="esperando_grafico_ia")
            except Exception:
                pass

            es_chart = await asyncio.to_thread(self._services.es_grafico_de_velas, ruta_local)
            if not es_chart:
                await asyncio.to_thread(self._services.fs_marcar_worker, exec_id, estado="fallido")
                self._services.db.collection("ejecuciones").document(exec_id).set(
                    {
                        "estado": "fallido",
                        "resumen": {"message": "No parece ser un grafico de velas"},
                        "updated_at": int(time.time()),
                    },
                    merge=True,
                )
                return {"status": "error", "message": "No parece ser un grafico de velas"}, 400

            include_tech = self._services.es_administrador(user_id or chat_id)

            try:
                res = await asyncio.to_thread(
                    self._services.analizar_con_yolo,
                    ruta_local,
                    stop_cb=stop_evt.is_set,
                    include_tech=include_tech,
                    user_id=user_id,
                )
            except TypeError:
                res = await asyncio.to_thread(self._services.analizar_con_yolo, ruta_local)

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
                await asyncio.to_thread(self._services.fs_marcar_worker, exec_id, estado="fallido")
                self._services.db.collection("ejecuciones").document(exec_id).set(
                    {
                        "estado": "fallido",
                        "resumen": {"message": "No se genero imagen procesada"},
                        "updated_at": int(time.time()),
                    },
                    merge=True,
                )
                return {"status": "error", "message": "No se genero imagen procesada"}, 500

            with open(ruta_salida, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode("utf-8")

            try:
                if not self._services.es_administrador(user_id or chat_id):
                    success, mensaje = await self._services.descontar_transaccion(user_id, 1)
                    if not success:
                        self._services.db.collection("ejecuciones").document(exec_id).set(
                            {"billing_warn": mensaje}, merge=True
                        )
            except Exception as cobro_exc:
                self._services.logger.warning("[IA] Error en cobro: %s", cobro_exc)

            await asyncio.to_thread(self._services.fs_marcar_worker, exec_id, estado="completed")

            resumen = {
                "message": texto_resultado,
                "imagen_base64": img_base64,
                "entradas": entradas_payload or {},
            }

            self._services.db.collection("ejecuciones").document(exec_id).set(
                {
                    "estado": "completed",
                    "resumen": resumen,
                    "updated_at": int(time.time()),
                },
                merge=True,
            )

            return {
                "status": "ok",
                "exec_id": exec_id,
                "message": texto_resultado,
                "imagen_base64": img_base64,
                "entradas": entradas_payload or {},
            }, 200

        except asyncio.CancelledError:
            await asyncio.to_thread(self._services.fs_marcar_worker, exec_id, estado="stopped")
            self._services.db.collection("ejecuciones").document(exec_id).set(
                {"estado": "stopped", "updated_at": int(time.time())}, merge=True
            )
            return {"status": "stopped", "exec_id": exec_id}, 200

        except Exception as exc:
            self._services.logger.exception("Error en /analisis/imagen")
            if exec_id:
                try:
                    await asyncio.to_thread(
                        self._services.fs_marcar_worker,
                        exec_id,
                        estado="fallido",
                        detalles_worker={"error": str(exc)},
                    )
                    self._services.db.collection("ejecuciones").document(exec_id).set(
                        {
                            "estado": "fallido",
                            "error": str(exc),
                            "updated_at": int(time.time()),
                        },
                        merge=True,
                    )
                except Exception:
                    pass
            return {"status": "error", "message": str(exc)}, 500

        finally:
            self._services.running_tasks.pop(exec_id, None)
            self._services.stop_events_ref.pop(exec_id, None)
            try:
                await asyncio.to_thread(
                    self._services.mark_user_state,
                    user_id=user_id or chat_id,
                    estado="disponible",
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
            if acquired_lock and self._services.ocupado_lock.locked():
                try:
                    self._services.ocupado_lock.release()
                except Exception:
                    pass
