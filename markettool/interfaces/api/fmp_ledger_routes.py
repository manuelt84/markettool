"""FMP usage ledger routes."""

from __future__ import annotations

from flask import jsonify, request

from markettool.infra.fmp.ledger import get_fmp_ledger_summary, get_fmp_usage_policy


def _redact_private_fields(payload: dict) -> dict:
    out = dict(payload or {})
    recent = []
    for item in out.get("recent") or []:
        row = dict(item or {})
        if row.get("user_id"):
            row["user_id"] = "redacted"
        if row.get("exec_id"):
            row["exec_id"] = "redacted"
        recent.append(row)
    out["recent"] = recent
    return out


def register_fmp_ledger_routes(app) -> None:
    @app.route("/api/fmp-ledger/summary", methods=["GET"])
    def fmp_ledger_summary():
        limit = request.args.get("limit", "20")
        try:
            limit_recent = max(0, min(100, int(limit)))
        except Exception:
            limit_recent = 20
        return jsonify(_redact_private_fields(get_fmp_ledger_summary(limit_recent=limit_recent))), 200

    @app.route("/api/fmp-ledger/policy", methods=["GET"])
    def fmp_ledger_policy():
        return jsonify(get_fmp_usage_policy()), 200


__all__ = ["register_fmp_ledger_routes"]
