#!/usr/bin/env python3
"""Dashboard de estado de sincronización MarketTool.

Genera reporte JSON/HTML del estado de todos los procesos de sync:
- Firestore → PostgreSQL
- PostgreSQL → Firestore  
- Local → GCS

Uso:
  python3 sync_status_dashboard.py --output json > status.json
  python3 sync_status_dashboard.py --output html > status.html
  python3 sync_status_dashboard.py --send-telegram "TOKEN" "CHAT_ID"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None


LOG_DIR = Path("/var/log/markettool")
STATE_FILE = Path.home() / ".openclaw/workspace/gcs_local_sync_state.json"


def get_last_run_time(script_name: str) -> Optional[datetime]:
    """Obtener timestamp de última ejecución exitosa del script."""
    log_file = LOG_DIR / f"{script_name.replace('.sh', '.log')}"
    
    if not log_file.exists():
        return None
    
    try:
        # Buscar última línea con "Starting" o timestamp
        with open(log_file, 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if 'Starting' in line or 'starting' in line:
                    # Extraer timestamp del formato [2026-08-01T02:00:00-04:00]
                    start = line.find('[')
                    end = line.find(']')
                    if start >= 0 and end > start:
                        ts_str = line[start+1:end]
                        return datetime.fromisoformat(ts_str)
    except Exception:
        pass
    
    return None


def get_last_exit_code(script_name: str) -> int:
    """Obtener código de salida de la última ejecución."""
    log_file = LOG_DIR / f"{script_name.replace('.sh', '.log')}"
    
    if not log_file.exists():
        return -1
    
    try:
        # Buscar último "completed" o "failed"
        with open(log_file, 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if 'completed successfully' in line.lower():
                    return 0
                elif 'failed' in line.lower():
                    return 1
    except Exception:
        pass
    
    return -1


def count_docs_synced_last_run(script_name: str) -> int:
    """Contar documentos sincronizados en la última ejecución."""
    log_file = LOG_DIR / f"{script_name.replace('.sh', '.log')}"
    
    if not log_file.exists():
        return 0
    
    try:
        count = 0
        with open(log_file, 'r') as f:
            for line in f:
                if 'WRITE' in line or 'UPDATE' in line:
                    count += 1
        return count
    except Exception:
        return 0


def get_errors_from_log(script_name: str, hours: int = 24) -> List[str]:
    """Obtener errores de las últimas N horas."""
    log_file = LOG_DIR / f"{script_name.replace('.sh', '.log')}"
    errors = []
    
    if not log_file.exists():
        return errors
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    try:
        with open(log_file, 'r') as f:
            for line in f:
                # Verificar si la línea es reciente
                start = line.find('[')
                end = line.find(']')
                if start >= 0 and end > start:
                    try:
                        ts_str = line[start+1:end]
                        ts = datetime.fromisoformat(ts_str)
                        if ts < cutoff:
                            continue
                    except:
                        pass
                
                # Verificar si es error
                if 'ERROR' in line or 'FAILED' in line.upper() or '❌' in line:
                    errors.append(line.strip())
    except Exception:
        pass
    
    return errors[:10]  # Máximo 10 errores


def get_gcs_sync_status() -> Dict[str, Any]:
    """Obtener estado de sincronización Local → GCS."""
    status = {
        "last_run": None,
        "status": "unknown",
        "files_uploaded": 0,
        "bytes_transferred": 0,
        "errors": [],
    }
    
    # Leer estado desde state file
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                status["last_run"] = data.get("last_sync")
                status["files_uploaded"] = data.get("last_upload_count", 0)
        except Exception:
            pass
    
    # Contar archivos subidos en log
    log_file = LOG_DIR / "gcs_local_sync.log"
    if log_file.exists():
        try:
            with open(log_file, 'r') as f:
                content = f.read()
                status["files_uploaded"] = content.count("UPLOADED:")
                status["bytes_transferred"] = content.count("UPLOADED:") * 100 * 1024  # Estimado
        except Exception:
            pass
    
    # Determinar estado
    if status["last_run"]:
        last_run_dt = datetime.fromisoformat(status["last_run"])
        age_hours = (datetime.now(timezone.utc) - last_run_dt).total_seconds() / 3600
        
        if age_hours < 7:  # Menos de 7 horas desde última sync (debería ser cada 6h)
            status["status"] = "ok"
        else:
            status["status"] = "warning"
    
    status["errors"] = get_errors_from_log("gcs_local_sync")
    
    return status


def query_postgres_integrity() -> Dict[str, Any]:
    """Consultar integridad desde PostgreSQL."""
    result = {
        "count_mismatches": 0,
        "hash_mismatches": 0,
        "last_validation": None,
    }
    
    # Intentar leer desde log de validación
    log_file = LOG_DIR / "firestore_sync.log"
    if log_file.exists():
        try:
            with open(log_file, 'r') as f:
                for line in reversed(f.readlines()):
                    if 'VALIDACIÓN EXITOSA' in line:
                        result["last_validation"] = "passed"
                        break
                    elif 'VALIDACIÓN FALLÓ' in line:
                        result["last_validation"] = "failed"
                        # Extraer número de issues
                        if '(' in line and 'issues' in line:
                            start = line.find('(') + 1
                            end = line.find(' ', start)
                            if end > start:
                                try:
                                    result["hash_mismatches"] = int(line[start:end])
                                except:
                                    pass
                        break
        except Exception:
            pass
    
    return result


def generate_report() -> Dict[str, Any]:
    """Generar reporte completo de estado."""
    now = datetime.now(timezone.utc)
    
    report = {
        "generated_at": now.isoformat(),
        "firestore_to_postgres": {
            "last_run": get_last_run_time("cron_sync_firestore"),
            "status": "ok" if get_last_exit_code("cron_sync_firestore") == 0 else "error",
            "docs_synced": count_docs_synced_last_run("cron_sync_firestore"),
            "errors": get_errors_from_log("firestore_sync"),
        },
        "postgres_to_firestore": {
            "last_run": get_last_run_time("cron_sync_postgres_to_firestore"),
            "status": "ok" if get_last_exit_code("cron_sync_postgres_to_firestore") == 0 else "error",
            "docs_synced": count_docs_synced_last_run("cron_sync_postgres_to_firestore"),
            "errors": get_errors_from_log("postgres_reverse_sync"),
        },
        "local_to_gcs": get_gcs_sync_status(),
        "integrity_checks": query_postgres_integrity(),
    }
    
    # Convertir datetimes a strings para JSON
    def convert_dates(obj):
        if isinstance(obj, dict):
            return {k: convert_dates(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_dates(v) for v in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj
    
    return convert_dates(report)


def generate_html(report: Dict[str, Any]) -> str:
    """Generar HTML simple del dashboard."""
    
    def status_card(title: str, data: Dict[str, Any]) -> str:
        status = data.get("status", "unknown")
        color = {"ok": "success", "error": "danger", "warning": "warning"}.get(status, "neutral")
        
        html = f"""
  <div class="card {color}">
    <h2>{title}</h2>
"""
        
        for key, value in data.items():
            if key == "errors" and value:
                html += f"    <p>Errores recientes: {len(value)}</p>\n"
            elif key != "status":
                html += f"    <p>{key.replace('_', ' ').title()}: {value}</p>\n"
        
        html += "  </div>\n"
        return html
    
    html = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MarketTool Sync Status</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
    h1 { color: #333; }
    .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .card.success { border-left: 4px solid #28a745; }
    .card.error { border-left: 4px solid #dc3545; }
    .card.warning { border-left: 4px solid #ffc107; }
    .card.neutral { border-left: 4px solid #6c757d; }
    .timestamp { color: #666; font-size: 0.9em; }
  </style>
</head>
<body>
  <h1>📊 Estado de Sincronización MarketTool</h1>
  <p class="timestamp">Generado: """ + report["generated_at"] + """</p>
"""
    
    html += status_card("Firestore → PostgreSQL", report["firestore_to_postgres"])
    html += status_card("PostgreSQL → Firestore", report["postgres_to_firestore"])
    html += status_card("Local → GCS", report["local_to_gcs"])
    html += status_card("Integridad", report["integrity_checks"])
    
    html += """
</body>
</html>
"""
    return html


def send_telegram(token: str, chat_id: str, report: Dict[str, Any]):
    """Enviar reporte a Telegram."""
    if not requests:
        print("ERROR: requests library not installed. Run: pip install requests")
        return False
    
    # Formatear mensaje
    fs_status = report["firestore_to_postgres"]["status"]
    pg_status = report["postgres_to_firestore"]["status"]
    gcs_status = report["local_to_gcs"]["status"]
    
    emoji = {"ok": "✅", "error": "❌", "warning": "⚠️"}.get
    
    message = f"""📊 *Estado de Sincronización MarketTool*

{emoji(fs_status)} Firestore → PG: {fs_status.upper()}
{emoji(pg_status)} PG → Firestore: {pg_status.upper()}
{emoji(gcs_status)} Local → GCS: {gcs_status.upper()}

Última actualización: {report['generated_at']}
"""
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending to Telegram: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", choices=["json", "html"], default="json")
    parser.add_argument("--send-telegram", nargs=2, metavar=("TOKEN", "CHAT_ID"))
    parser.add_argument("--output-file", type=Path)
    args = parser.parse_args()
    
    # Generar reporte
    report = generate_report()
    
    # Formatear output
    if args.output == "json":
        output = json.dumps(report, indent=2, ensure_ascii=False)
    else:
        output = generate_html(report)
    
    # Enviar o guardar
    if args.send_telegram:
        token, chat_id = args.send_telegram
        if send_telegram(token, chat_id, report):
            print("✅ Report sent to Telegram")
            return 0
        else:
            print("❌ Failed to send to Telegram")
            return 1
    
    if args.output_file:
        args.output_file.write_text(output, encoding="utf-8")
        print(f"✅ Report saved to {args.output_file}")
    else:
        print(output)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
