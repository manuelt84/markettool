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

    @staticmethod
    def _extract_requested_assets(data: dict) -> tuple[list[str], object]:
        """Normalize requested assets from multiple payload contracts.

        Supported inputs (priority order):
        - activos_solicitados / activos / symbols / selected_symbols / selectedSymbols
        - categoria / category
        - activo
        """

        raw_payload = None
        requested_assets: list[str] = []

        def _append_from_value(value: object) -> None:
            if value is None:
                return
            if isinstance(value, str):
                requested_assets.extend(
                    [p.strip().upper() for p in value.split(",") if str(p).strip()]
                )
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str):
                        val = item.strip().upper()
                        if val:
                            requested_assets.append(val)
                        continue
                    if isinstance(item, dict):
                        sym = (
                            item.get("symbol")
                            or item.get("id")
                            or item.get("activo")
                            or item.get("ticker")
                        )
                        if sym is not None:
                            val = str(sym).strip().upper()
                            if val:
                                requested_assets.append(val)
                        continue
                    val = str(item).strip().upper()
                    if val:
                        requested_assets.append(val)
                return

            val = str(value).strip().upper()
            if val:
                requested_assets.append(val)

        assets_candidates = (
            "activos_solicitados",
            "activos",
            "symbols",
            "selected_symbols",
            "selectedSymbols",
        )

        for key in assets_candidates:
            value = data.get(key)
            if value is None:
                continue
            if raw_payload is None:
                raw_payload = value
            _append_from_value(value)

        if not requested_assets:
            category_value = data.get("categoria")
            if category_value is None:
                category_value = data.get("category")
            if category_value is not None:
                raw_payload = category_value if raw_payload is None else raw_payload
                _append_from_value(category_value)

        if not requested_assets:
            activo_fallback = data.get("activo")
            if activo_fallback is None:
                return [], raw_payload
            raw_payload = activo_fallback if raw_payload is None else raw_payload
            _append_from_value(activo_fallback)

        requested_assets = list(dict.fromkeys(requested_assets))
        return requested_assets, raw_payload

    async def ejecutar(self, data: dict | None) -> Tuple[dict, int]:
        chat_id_local = None
        lock_id = None
        data = data or {}

        try:
            acquired_lock = False
            task_scheduled = False
            user_id = str(data.get("user_id") or "").strip()
            chat_id = str(data.get("chat_id") or "").strip()
            chat_id_local = chat_id

            if not user_id:
                return {"status": "error", "message": "user_id es obligatorio"}, 400

            requested_assets, raw_payload = self._extract_requested_assets(data)
            if not requested_assets:
                return {
                    "status": "error",
                    "message": "Falta activos a analizar (activos_solicitados/activos/categoria/activo)",
                }, 400

            # De-duplicate preserving order for stable execution + billing estimate.
            requested_assets = list(dict.fromkeys(requested_assets))
            activo = ",".join(requested_assets)
            self._services.logger.info(
                "[/analisis/ejecutar] activos solicitados (raw->normalized): %s -> %s",
                raw_payload,
                requested_assets,
            )

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
                self._services.logger.info(
                    "[/analisis/ejecutar] activos resueltos tras filtro: %s",
                    list(activos_filtrados_est or []),
                )
                n_transacciones_req, billing_meta = self._services.compute_analysis_transaction_units(
                    list(activos_filtrados_est or []),
                    list(tfs_est or []),
                )
                self._services.logger.info(
                    "[/analisis/ejecutar] billing estimate: %s",
                    billing_meta,
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

            acquired_lock = True
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
                activos_solicitados=requested_assets,
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
                        lock_id=lock_id,
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

                    # Dispatch async backtest for all enriched files (fire-and-forget)
                    asyncio.create_task(self._run_post_analysis_backtest(exec_id, user_id))

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
                    try:
                        await asyncio.to_thread(
                            self._services.mark_user_state,
                            user_id=user_id or chat_id_local,
                            estado="disponible",
                        )
                    except Exception:
                        pass
                    try:
                        if acquired_lock and lock_id:
                            await asyncio.to_thread(
                                self._services.release_user_lock,
                                user_id=user_id,
                                chat_id=chat_id_local or None,
                                lock_id=lock_id,
                            )
                    except Exception:
                        pass
                    try:
                        if chat_id_local:
                            self._services.clear_current_request_cfg(chat_id_local)
                    except Exception:
                        pass

            task = asyncio.create_task(_runner())
            self._services.running_tasks[exec_id] = task
            task_scheduled = True

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
            if not task_scheduled:
                try:
                    await asyncio.to_thread(
                        self._services.mark_user_state,
                        user_id=user_id or chat_id_local,
                        estado="disponible",
                    )
                    if acquired_lock and lock_id:
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

    async def _run_post_analysis_backtest(self, exec_id: str, user_id: str | None) -> None:
        """Fire-and-forget: run backtest on all enriched files of an execution."""
        import re
        try:
            from markettool.application.services.backtesting_service import get_backtesting_service
            import json as _json
            from datetime import datetime as _dt

            db = getattr(self._services, "db", None)
            if not db:
                return

            bt_service = get_backtesting_service(logger=self._services.logger)

            # Read archivos_generados for this exec
            docs = await asyncio.to_thread(
                lambda: list(
                    db.collection("archivos_generados")
                    .where("exec_id", "==", exec_id)
                    .stream()
                )
            )

            enriched_files = []
            for doc in docs:
                data = doc.to_dict() or {}
                gcs_path = data.get("gcs_path") or data.get("metadata", {}).get("gcs_path") or ""
                if "_enriched.json" in gcs_path.lower():
                    enriched_files.append(data)

            if not enriched_files:
                self._services.logger.info(
                    "[PostBacktest] No enriched files found for exec_id=%s", exec_id
                )
                return

            # Get JSON storage bucket
            try:
                from markettool.infra.storage.vps_json_store import VpsJsonStore, vps_mode_enabled

                if vps_mode_enabled():
                    bucket = VpsJsonStore.from_env()
                else:
                    bucket_name = getattr(self._services, "gcs_bucket_name", None) or "markettool_bucket"
                    gcs_client = getattr(self._services, "gcs_client", None)
                    if gcs_client is None:
                        from google.cloud import storage as gcs_storage
                        gcs_client = gcs_storage.Client()
                    bucket = gcs_client.bucket(bucket_name)
            except Exception as exc:
                self._services.logger.warning("[PostBacktest] JSON storage not available: %s", exc)
                return

            for file_data in enriched_files:
                gcs_path = file_data.get("gcs_path") or file_data.get("metadata", {}).get("gcs_path") or ""
                # Extract symbol and timeframe from filename like BTCUSD_5m_enriched.json
                filename = gcs_path.split("/")[-1] if "/" in gcs_path else gcs_path
                match = re.match(r"^(.+?)_(\d+[mhHdDwW])_enriched\.json$", filename, re.IGNORECASE)
                if not match:
                    continue

                symbol = match.group(1).upper()
                timeframe = match.group(2).lower()
                doc_key = f"{exec_id}_{symbol}_{timeframe}"

                try:
                    # Write "running" status
                    await asyncio.to_thread(
                        db.collection("ejecuciones").document(exec_id).collection("backtest_results").document(f"{symbol}_{timeframe}").set,
                        {
                            "status": "running",
                            "exec_id": exec_id,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "user_id": user_id or "",
                            "created_at": int(_dt.now().timestamp() * 1000),
                        },
                    )

                    # Load enriched JSON from GCS
                    blob = bucket.blob(gcs_path)
                    raw = await asyncio.to_thread(blob.download_as_text)
                    enriched = _json.loads(raw)

                    candles = []
                    entries = []
                    if isinstance(enriched, list):
                        entries = enriched
                    elif isinstance(enriched, dict):
                        candles = enriched.get("candles") or enriched.get("series") or []
                        entries = (
                            enriched.get("entries")
                            or enriched.get("entradas")
                            or enriched.get("oportunidades")
                            or enriched.get("records")
                            or enriched.get("data")
                            or []
                        )

                    stats = await asyncio.to_thread(
                        bt_service.run_from_enriched,
                        candles=candles,
                        entries=entries,
                        symbol=symbol,
                        timeframe=timeframe,
                    )

                    # Update to completed
                    await asyncio.to_thread(
                        db.collection("ejecuciones").document(exec_id).collection("backtest_results").document(f"{symbol}_{timeframe}").update,
                        {
                            "status": "completed",
                            "stats": stats,
                            "completed_at": int(_dt.now().timestamp() * 1000),
                        },
                    )
                    self._services.logger.info(
                        "[PostBacktest] Completed backtest for %s/%s/%s", exec_id, symbol, timeframe
                    )
                except Exception as exc:
                    self._services.logger.warning(
                        "[PostBacktest] Failed backtest for %s/%s/%s: %s",
                        exec_id, symbol, timeframe, exc,
                    )
                    try:
                        await asyncio.to_thread(
                            db.collection("ejecuciones").document(exec_id).collection("backtest_results").document(f"{symbol}_{timeframe}").update,
                            {
                                "status": "failed",
                                "error": str(exc),
                                "completed_at": int(_dt.now().timestamp() * 1000),
                            },
                        )
                    except Exception:
                        pass

        except Exception as exc:
            self._services.logger.warning(
                "[PostBacktest] Top-level failure for exec_id=%s: %s", exec_id, exc
            )

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
                    gcs_path = data.get("gcs_path")
                    signed_url = data.get("signed_url") or data.get("metadata", {}).get("signed_url")
                    if not signed_url and isinstance(gcs_path, str) and gcs_path:
                        from markettool.infra.storage.vps_json_store import VpsJsonStore, vps_mode_enabled

                        signed_url = (
                            VpsJsonStore.from_env().public_url(gcs_path)
                            if vps_mode_enabled()
                            else f"https://storage.googleapis.com/markettool_bucket/{gcs_path}"
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
