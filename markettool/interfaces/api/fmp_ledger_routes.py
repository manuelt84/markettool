"""FMP usage ledger routes."""

from __future__ import annotations

from flask import jsonify, request

from markettool.infra.fmp.ledger import get_fmp_ledger_summary


def register_fmp_ledger_routes(app) -> None:
    @app.route("/api/fmp-ledger/summary", methods=["GET"])
    def fmp_ledger_summary():
        limit = request.args.get("limit", "20")
        try:
            limit_recent = max(0, min(100, int(limit)))
        except Exception:
            limit_recent = 20
        return jsonify(get_fmp_ledger_summary(limit_recent=limit_recent)), 200


__all__ = ["register_fmp_ledger_routes"]

