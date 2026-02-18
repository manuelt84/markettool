"""
API Routes for Hexagonal Analysis Services

Phase 8: Microservices/API - Expose hexagonal services via REST endpoints.

Services exposed:
- Support/Resistance Analysis
- Fundamental Analysis
- Entry Signal Calculation
"""

import logging
from typing import Dict, Any, Optional, List
from flask import Blueprint, request, jsonify
import pandas as pd
import numpy as np

from markettool.application.services import (
    get_sr_service,
    get_fundamental_service,
)
from markettool.application.adapters import get_analyzer
from markettool.application.use_cases import get_calculate_entries_use_case

logger = logging.getLogger(__name__)

# Blueprint for hexagonal analysis services
analysis_bp = Blueprint('hexagonal_analysis', __name__, url_prefix='/api/v1/analysis')


# ==================== SUPPORT/RESISTANCE ROUTES ====================

@analysis_bp.route('/support-resistance', methods=['POST'])
def analyze_support_resistance():
    """
    Calculate support and resistance levels.
    
    Request body:
    {
        "close": [float, ...],  # Close prices
        "high": [float, ...],   # High prices
        "low": [float, ...],    # Low prices
        "volume": [float, ...], # Volume
        "window": int (optional, default 50),
        "atr_multiplier": float (optional, default 2.0),
        "min_levels": int (optional, default 2)
    }
    
    Response:
    {
        "supports": [float, ...],
        "resistances": [float, ...],
        "atr": float,
        "s1": float, "s2": float,
        "r1": float, "r2": float,
        "structured_levels": {...}
    }
    """
    try:
        data = request.get_json()
        
        # Build DataFrame
        df = pd.DataFrame({
            'close': data.get('close', []),
            'high': data.get('high', []),
            'low': data.get('low', []),
            'volume': data.get('volume', []),
        })
        
        if df.empty:
            return jsonify({'error': 'Empty price data'}), 400
        
        # Get S/R service and calculate
        sr_service = get_sr_service()
        window = data.get('window', 50)
        atr_multiplier = data.get('atr_multiplier', 2.0)
        min_levels = data.get('min_levels', 2)
        
        sr_levels = sr_service.calculate_support_resistance(
            df,
            window=min(window, len(df)),
            atr_multiplier=atr_multiplier,
            min_levels=min_levels
        )
        
        # Get key levels
        key_levels = sr_service.get_key_levels(
            df,
            sr_levels.supports,
            sr_levels.resistances,
            atr_threshold=atr_multiplier,
            max_levels=2
        )
        
        return jsonify({
            'supports': [float(x) for x in sr_levels.supports],
            'resistances': [float(x) for x in sr_levels.resistances],
            'atr': float(sr_levels.atr),
            's1': float(key_levels.get('s1', 0)),
            's2': float(key_levels.get('s2', 0)),
            'r1': float(key_levels.get('r1', 0)),
            'r2': float(key_levels.get('r2', 0)),
            'structured_levels': key_levels,
        }), 200
    
    except Exception as e:
        logger.error(f"S/R analysis error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@analysis_bp.route('/range-detection', methods=['POST'])
def detect_zigzag_range():
    """
    Detect if price is in range or trending.
    
    Request body:
    {
        "close": [float, ...],
        "high": [float, ...],
        "low": [float, ...]
    }
    
    Response:
    {
        "is_range": bool,
        "structure": "range|trending|undefined",
        "zigzag_levels": [...]
    }
    """
    try:
        data = request.get_json()
        
        df = pd.DataFrame({
            'close': data.get('close', []),
            'high': data.get('high', []),
            'low': data.get('low', []),
        })
        
        if df.empty:
            return jsonify({'error': 'Empty price data'}), 400
        
        sr_service = get_sr_service()
        range_result = sr_service.detect_zigzag_range(df)
        
        return jsonify({
            'is_range': bool(range_result.is_range),
            'structure': range_result.structure,
            'zigzag_direction': getattr(range_result, 'direction', 'unknown'),
            'confidence': getattr(range_result, 'confidence', 0.5),
        }), 200
    
    except Exception as e:
        logger.error(f"Range detection error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== FUNDAMENTAL ANALYSIS ROUTES ====================

@analysis_bp.route('/fundamental', methods=['POST'])
def analyze_fundamental():
    """
    Perform fundamental analysis with economic event impact.
    
    Request body:
    {
        "symbol": str,
        "timeframe": str,
        "events": [{date, event_name, impact, ...}],
        "base_probability": float (optional, default 50.0)
    }
    
    Response:
    {
        "adjusted_probability": float,
        "base_probability": float,
        "impact_factor": float,
        "events_count": int,
        "next_major_events": [...]
    }
    """
    try:
        data = request.get_json()
        
        symbol = data.get('symbol')
        timeframe = data.get('timeframe')
        base_prob = data.get('base_probability', 50.0)
        
        if not symbol or not timeframe:
            return jsonify({'error': 'Missing symbol or timeframe'}), 400
        
        # Build events DataFrame
        events_data = data.get('events', [])
        df_eventos = pd.DataFrame(events_data) if events_data else pd.DataFrame()
        
        fund_service = get_fundamental_service()
        adjusted_prob, metadata = fund_service.adjust_probability_with_events(
            base_prob,
            df_eventos,
            symbol,
            timeframe,
            date_start=data.get('date_start'),
            date_end=data.get('date_end')
        )
        
        return jsonify({
            'adjusted_probability': float(adjusted_prob),
            'base_probability': float(base_prob),
            'impact_factor': metadata.get('impact', 0),
            'events_count': metadata.get('events_found', 0),
            'metadata': metadata,
        }), 200
    
    except Exception as e:
        logger.error(f"Fundamental analysis error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== TECHNICAL ANALYSIS ROUTES ====================

@analysis_bp.route('/technical-indicators', methods=['POST'])
def compute_technical_indicators():
    """
    Compute all technical indicators.
    
    Request body:
    {
        "close": [float, ...],
        "high": [float, ...],
        "low": [float, ...],
        "open": [float, ...],
        "volume": [float, ...]
    }
    
    Response:
    {
        "rsi": float,
        "macd": float,
        "macd_signal": float,
        "macd_histogram": float,
        "bollinger_upper": float,
        "bollinger_middle": float,
        "bollinger_lower": float,
        "atr": float,
        "stochastic_k": float,
        "stochastic_d": float,
        ...
    }
    """
    try:
        data = request.get_json()
        
        df = pd.DataFrame({
            'close': data.get('close', []),
            'high': data.get('high', []),
            'low': data.get('low', []),
            'open': data.get('open', []),
            'volume': data.get('volume', []),
        })
        
        if df.empty:
            return jsonify({'error': 'Empty price data'}), 400
        
        analyzer = get_analyzer()
        indicators = analyzer.compute_all_indicators(df)
        
        # Convert numpy types to native Python types for JSON serialization
        result = {k: float(v) if isinstance(v, (np.floating, float)) else v 
                  for k, v in indicators.items()}
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"Technical analysis error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== ENTRY SIGNALS ROUTES ====================

@analysis_bp.route('/entry-signals', methods=['POST'])
def calculate_entry_signals():
    """
    Calculate complete entry signals using hexagonal analysis.
    
    Request body:
    {
        "close": [float, ...],
        "high": [float, ...],
        "low": [float, ...],
        "open": [float, ...],
        "volume": [float, ...],
        "symbol": str,
        "timeframe": str,
        "events": [{...}] (optional),
        "config": {...} (optional)
    }
    
    Response:
    {
        "tipo_operacion": "Compra|Venta|Neutral",
        "probabilidad_alza": float,
        "probabilidad_baja": float,
        "probabilidad_tecnica": float,
        "probabilidad_fundamental": float,
        "confianza": float,
        "niveles": {"s1": float, "s2": float, "r1": float, "r2": float},
        "atr": float,
        "is_range": bool,
        "structure": str
    }
    """
    try:
        data = request.get_json()
        
        # Build price DataFrame
        df = pd.DataFrame({
            'close': data.get('close', []),
            'high': data.get('high', []),
            'low': data.get('low', []),
            'open': data.get('open', []),
            'volume': data.get('volume', []),
        })
        
        # Build events DataFrame
        events_data = data.get('events', [])
        df_eventos = pd.DataFrame(events_data) if events_data else pd.DataFrame()
        
        if df.empty:
            return jsonify({'error': 'Empty price data'}), 400
        
        symbol = data.get('symbol', 'UNKNOWN')
        timeframe = data.get('timeframe', '1day')
        config = data.get('config', {})
        
        # Use sync wrapper to call async use case from sync context
        from MarketTool import calcular_entradas_sync_wrapper
        
        result = calcular_entradas_sync_wrapper(
            df, df_eventos, symbol, timeframe,
            cfg=config
        )
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"Entry signals error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== BATCH ANALYSIS ROUTES ====================

@analysis_bp.route('/batch-analysis', methods=['POST'])
def batch_analysis():
    """
    Analyze multiple symbols/timeframes in one request.
    
    Request body:
    {
        "symbols": [
            {
                "symbol": str,
                "timeframe": str,
                "close": [float, ...],
                "high": [float, ...],
                ...
            },
            ...
        ],
        "analysis_type": "full|sr|technical|fundamental"
    }
    
    Response:
    {
        "results": [
            {
                "symbol": str,
                "timeframe": str,
                "status": "success|error",
                "data": {...}
            },
            ...
        ],
        "summary": {
            "total": int,
            "successful": int,
            "failed": int
        }
    }
    """
    try:
        data = request.get_json()
        
        symbols = data.get('symbols', [])
        analysis_type = data.get('analysis_type', 'full')
        
        if not symbols:
            return jsonify({'error': 'No symbols provided'}), 400
        
        results = []
        for sym_data in symbols:
            try:
                symbol = sym_data.get('symbol')
                timeframe = sym_data.get('timeframe')
                
                df = pd.DataFrame({
                    'close': sym_data.get('close', []),
                    'high': sym_data.get('high', []),
                    'low': sym_data.get('low', []),
                    'open': sym_data.get('open', []),
                    'volume': sym_data.get('volume', []),
                })
                
                if df.empty:
                    results.append({
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'status': 'error',
                        'error': 'Empty data'
                    })
                    continue
                
                # Perform analysis based on type
                sr_service = get_sr_service()
                sr_result = sr_service.calculate_support_resistance(df)
                
                result_data = {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'status': 'success',
                    'data': {
                        'supports': [float(x) for x in sr_result.supports],
                        'resistances': [float(x) for x in sr_result.resistances],
                        'atr': float(sr_result.atr),
                    }
                }
                
                results.append(result_data)
            
            except Exception as sym_e:
                logger.warning(f"Batch analysis error for {sym_data.get('symbol')}: {sym_e}")
                results.append({
                    'symbol': sym_data.get('symbol'),
                    'timeframe': sym_data.get('timeframe'),
                    'status': 'error',
                    'error': str(sym_e)
                })
        
        successful = sum(1 for r in results if r.get('status') == 'success')
        
        return jsonify({
            'results': results,
            'summary': {
                'total': len(results),
                'successful': successful,
                'failed': len(results) - successful
            }
        }), 200
    
    except Exception as e:
        logger.error(f"Batch analysis error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== REGISTRATION ====================

def register_hexagonal_analysis_routes(app):
    """Register hexagonal analysis blueprint with Flask app."""
    app.register_blueprint(analysis_bp)
    logger.info("Registered hexagonal analysis routes")
