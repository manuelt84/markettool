"""
API Routes for MTF Analysis Results

Endpoint para consultar resultados de procesamiento asíncrono MTF.
Frontend pollea este endpoint con el cache_key recibido en la respuesta del análisis.
"""

import logging
from flask import Blueprint, request, jsonify
import redis
import json

logger = logging.getLogger(__name__)

# Blueprint for MTF results
mtf_results_bp = Blueprint('mtf_results', __name__, url_prefix='/api/v1/mtf')


@mtf_results_bp.route('/results/<cache_key>', methods=['GET'])
def get_mtf_results(cache_key: str):
    """
    Get MTF analysis results for a backtest job.
    
    Query params:
    - wait: (optional) If true, wait up to 30s for results to be ready
    
    Response formats:
    
    1. Processing (aún no está listo):
    {
        "status": "processing",
        "cache_key": "...",
        "message": "MTF analysis in progress"
    }
    
    2. Completed (resultados listos):
    {
        "status": "completed",
        "entries": [...],  # Entradas con MTF alignment aplicado
        "mtf_metadata": {
            "processed_count": 15,
            "boosted_count": 8,
            "total_entries": 42,
            "processed_at": "2026-07-31T21:45:00Z"
        }
    }
    
    3. Failed (error en procesamiento):
    {
        "status": "failed",
        "error": "Error message",
        "processed_at": "..."
    }
    
    4. Not found (clave no existe o expiró):
    {
        "status": "not_found",
        "message": "Results expired or never existed"
    }
    """
    try:
        wait = request.args.get('wait', 'false').lower() == 'true'
        redis_client = redis.Redis(host='localhost', port=6379, db=0)
        
        # Intentar obtener resultados
        data_json = redis_client.get(cache_key)
        
        if data_json:
            # Resultados encontrados
            data = json.loads(data_json)
            return jsonify({
                'status': 'completed',
                **data
            }), 200
        
        # Verificar si hay error
        error_json = redis_client.get(cache_key + ':error')
        if error_json:
            error_data = json.loads(error_json)
            return jsonify({
                'status': 'failed',
                **error_data
            }), 200
        
        # No encontrado → aún procesando o expiró
        if wait:
            # Polling por hasta 30 segundos
            import time
            start_time = time.time()
            while time.time() - start_time < 30:
                time.sleep(1)
                
                data_json = redis_client.get(cache_key)
                if data_json:
                    data = json.loads(data_json)
                    return jsonify({
                        'status': 'completed',
                        **data
                    }), 200
                
                error_json = redis_client.get(cache_key + ':error')
                if error_json:
                    error_data = json.loads(error_json)
                    return jsonify({
                        'status': 'failed',
                        **error_data
                    }), 200
            
            # Timeout
            return jsonify({
                'status': 'timeout',
                'message': 'Processing took longer than 30s, try again later'
            }), 202
        
        # Sin wait → retornar inmediatamente
        return jsonify({
            'status': 'processing',
            'cache_key': cache_key,
            'message': 'MTF analysis in progress, poll again or use wait=true'
        }), 202
        
    except Exception as e:
        logger.error("[MTF-Results] Error getting results for %s: %s", cache_key, e, exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@mtf_results_bp.route('/status/<symbol>/<timeframe>', methods=['GET'])
def get_mtf_status(symbol: str, timeframe: str):
    """
    Get status of all MTF jobs for a symbol/timeframe.
    
    Returns list of recent cache keys and their status.
    """
    try:
        redis_client = redis.Redis(host='localhost', port=6379, db=0)
        
        # Buscar claves que coincidan con el patrón
        pattern = f"markettool:backtest:mtf:{symbol}:{timeframe}:*"
        keys = redis_client.keys(pattern)
        
        results = []
        for key in keys:
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            
            # Verificar estado
            if redis_client.exists(key_str):
                status = 'completed'
            elif redis_client.exists(key_str + ':error'):
                status = 'failed'
            else:
                status = 'processing'
            
            # Obtener TTL
            ttl = redis_client.ttl(key_str)
            
            results.append({
                'cache_key': key_str,
                'status': status,
                'ttl_seconds': ttl,
            })
        
        # Ordenar por más reciente (menor TTL = más reciente)
        results.sort(key=lambda x: x['ttl_seconds'], reverse=True)
        
        return jsonify({
            'symbol': symbol,
            'timeframe': timeframe,
            'jobs': results[:10],  # Máximo 10 jobs más recientes
        }), 200
        
    except Exception as e:
        logger.error("[MTF-Status] Error getting status for %s/%s: %s", symbol, timeframe, e, exc_info=True)
        return jsonify({
            'error': str(e)
        }), 500
