"""
API Routes for Signal Validation Services

Phase 8: Microservices/API - Expose signal validation via REST endpoints.

Services exposed:
- Confluence evaluation (multi-indicator scoring)
- Zone validation (trading zone safety checks)
"""

import logging
pass
from flask import Blueprint, request, jsonify

from markettool.application.services import (
    get_confluence_service,
    get_zone_validator,
)

logger = logging.getLogger(__name__)

# Blueprint for signal validation services
validation_bp = Blueprint('signal_validation', __name__, url_prefix='/api/v1/validation')


# ==================== CONFLUENCE EVALUATION ROUTES ====================

@validation_bp.route('/confluence', methods=['POST'])
def evaluate_confluence():
    """
    Evaluate signal confluence across multiple indicators.
    
    Request body:
    {
        "signals": {
            "RSI": "BUY",
            "MACD": "BUY",
            "Bollinger": "SELL",
            ...
        },
        "weights": {
            "RSI": 0.4,
            "MACD": 0.4,
            "Bollinger": 0.2
        } (optional, defaults to equal weights),
        "min_confluence": int (optional, default 3)
    }
    
    Response:
    {
        "signal_direction": "BUY|SELL|NEUTRAL",
        "confluence_count": int,
        "confluence_pct": float,
        "confluence_level": "very_weak|weak|moderate|strong|very_strong",
        "confluent_signals": [str, ...],
        "conflicting_signals": [str, ...],
        "confidence_score": float (0.0-1.0),
        "recommendation": str
    }
    """
    try:
        data = request.get_json()
        
        signals = data.get('signals')
        weights = data.get('weights')
        min_confluence = data.get('min_confluence', 3)
        
        if not signals or not isinstance(signals, dict):
            return jsonify({'error': 'Invalid or missing signals'}), 400
        
        confluence_service = get_confluence_service()
        result = confluence_service.evaluate_signals(
            technical_signals=signals,
            weights=weights,
            min_confluence=min_confluence
        )
        
        return jsonify({
            'signal_direction': result.signal_direction,
            'confluence_count': result.confluence_count,
            'confluence_pct': float(result.confluence_pct),
            'confluence_level': result.confluence_level,
            'confluent_signals': result.confluent_signals,
            'conflicting_signals': result.conflicting_signals,
            'confidence_score': float(result.confidence_score),
            'recommendation': result.recommendation,
            'metadata': result.metadata,
        }), 200
    
    except Exception as e:
        logger.error(f"Confluence evaluation error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@validation_bp.route('/confluence/multi-timeframe', methods=['POST'])
def evaluate_multi_timeframe_confluence():
    """
    Evaluate signal confluence across multiple timeframes.
    
    Request body:
    {
        "signals_by_timeframe": {
            "1h": {
                "RSI": "BUY",
                "MACD": "BUY",
                ...
            },
            "4h": {
                "RSI": "SELL",
                "MACD": "NEUTRAL",
                ...
            },
            ...
        },
        "timeframe_weights": {
            "1h": 0.33,
            "4h": 0.33,
            "1day": 0.34
        } (optional)
    }
    
    Response:
    {
        "timeframe_results": {
            "1h": {...},
            "4h": {...},
            ...
            "COMBINED": {...}
        }
    }
    """
    try:
        data = request.get_json()
        
        signals_by_tf = data.get('signals_by_timeframe')
        tf_weights = data.get('timeframe_weights')
        
        if not signals_by_tf:
            return jsonify({'error': 'Missing signals_by_timeframe'}), 400
        
        confluence_service = get_confluence_service()
        results = confluence_service.evaluate_multi_timeframe(
            signals_by_tf=signals_by_tf,
            tf_weights=tf_weights
        )
        
        # Convert results to JSON-serializable format
        json_results = {}
        for tf, result in results.items():
            json_results[tf] = {
                'signal_direction': result.signal_direction,
                'confluence_count': result.confluence_count,
                'confluence_pct': float(result.confluence_pct),
                'confluence_level': result.confluence_level,
                'confidence_score': float(result.confidence_score),
                'recommendation': result.recommendation,
            }
        
        return jsonify({'timeframe_results': json_results}), 200
    
    except Exception as e:
        logger.error(f"Multi-timeframe confluence error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== ZONE VALIDATION ROUTES ====================

@validation_bp.route('/trading-zone', methods=['POST'])
def validate_trading_zone():
    """
    Validate if current price conditions allow trading.
    
    Request body:
    {
        "current_price": float,
        "rsi": float (optional, 0-100),
        "atr": float (optional),
        "support_levels": [float, ...] (optional),
        "resistance_levels": [float, ...] (optional),
        "recent_high": float (optional),
        "recent_low": float (optional),
        "recent_atr_avg": float (optional)
    }
    
    Response:
    {
        "is_valid": bool,
        "zone_violations": [str, ...],
        "zone_type": str (optional),
        "reason": str,
        "confidence": float (0.0-1.0)
    }
    """
    try:
        data = request.get_json()
        
        current_price = data.get('current_price')
        if current_price is None:
            return jsonify({'error': 'Missing current_price'}), 400
        
        zone_validator = get_zone_validator()
        result = zone_validator.validate_trading_zone(
            current_price=current_price,
            rsi=data.get('rsi'),
            atr=data.get('atr'),
            support_levels=data.get('support_levels'),
            resistance_levels=data.get('resistance_levels'),
            recent_high=data.get('recent_high'),
            recent_low=data.get('recent_low'),
            recent_atr_avg=data.get('recent_atr_avg'),
        )
        
        return jsonify({
            'is_valid': result.is_valid,
            'zone_violations': result.zone_violations,
            'zone_type': result.zone_type,
            'reason': result.reason,
            'confidence': float(result.confidence),
            'metadata': result.metadata or {},
        }), 200
    
    except Exception as e:
        logger.error(f"Trading zone validation error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@validation_bp.route('/trading-hours', methods=['POST'])
def check_trading_hours():
    """
    Check if current time is in a no-trading window.
    
    Request body:
    {
        "current_time": str (ISO format, optional - defaults to now),
        "no_trade_windows": [
            [start_hour, end_hour],
            ...
        ] (optional, defaults to standard blackout windows)
    }
    
    Response:
    {
        "is_valid": bool,
        "zone_violations": [str, ...],
        "reason": str,
        "current_hour": int,
        "in_blackout": bool
    }
    """
    try:
        from datetime import datetime, timezone
        
        data = request.get_json()
        
        # Parse current time
        current_time_str = data.get('current_time')
        if current_time_str:
            try:
                current_time = datetime.fromisoformat(current_time_str)
            except ValueError:
                return jsonify({'error': 'Invalid ISO format for current_time'}), 400
        else:
            current_time = datetime.now(timezone.utc)
        
        # Convert UTC to aware datetime if needed
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        
        no_trade_windows = data.get('no_trade_windows')
        
        zone_validator = get_zone_validator()
        result = zone_validator.check_no_trading_hours(
            current_time=current_time,
            no_trade_windows=no_trade_windows
        )
        
        return jsonify({
            'is_valid': result.is_valid,
            'zone_violations': result.zone_violations,
            'reason': result.reason,
            'confidence': float(result.confidence),
            'current_hour': result.metadata.get('current_hour'),
            'in_blackout': result.metadata.get('in_blackout'),
        }), 200
    
    except Exception as e:
        logger.error(f"Trading hours check error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@validation_bp.route('/multi-condition', methods=['POST'])
def validate_multi_condition():
    """
    Aggregate multiple validation conditions.
    
    Request body:
    {
        "validations": {
            "price_valid": true,
            "rsi_valid": false,
            "volume_valid": true,
            ...
        }
    }
    
    Response:
    {
        "is_valid": bool,
        "zone_violations": [str, ...],
        "reason": str,
        "confidence": float (0.0-1.0),
        "passed_conditions": int,
        "total_conditions": int
    }
    """
    try:
        data = request.get_json()
        
        validations = data.get('validations')
        if not validations or not isinstance(validations, dict):
            return jsonify({'error': 'Invalid or missing validations dict'}), 400
        
        zone_validator = get_zone_validator()
        result = zone_validator.validate_multi_condition(validations)
        
        passed = sum(1 for v in validations.values() if v)
        total = len(validations)
        
        return jsonify({
            'is_valid': result.is_valid,
            'zone_violations': result.zone_violations,
            'reason': result.reason,
            'confidence': float(result.confidence),
            'passed_conditions': passed,
            'total_conditions': total,
            'condition_details': validations,
        }), 200
    
    except Exception as e:
        logger.error(f"Multi-condition validation error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== COMBINED VALIDATION ROUTES ====================

@validation_bp.route('/complete-validation', methods=['POST'])
def complete_validation():
    """
    Comprehensive validation combining confluence and zones.
    
    Request body:
    {
        "signals": {...},
        "current_price": float,
        "rsi": float,
        "support_levels": [float, ...],
        "resistance_levels": [float, ...],
        ...
    }
    
    Response:
    {
        "confluence_result": {...},
        "zone_result": {...},
        "overall_valid": bool,
        "overall_confidence": float,
        "trading_recommendation": str
    }
    """
    try:
        data = request.get_json()
        
        # Validate confluence
        confluence_service = get_confluence_service()
        signals = data.get('signals', {})
        confluence_result = confluence_service.evaluate_signals(signals) if signals else None
        
        # Validate zones
        zone_validator = get_zone_validator()
        zone_result = zone_validator.validate_trading_zone(
            current_price=data.get('current_price'),
            rsi=data.get('rsi'),
            atr=data.get('atr'),
            support_levels=data.get('support_levels'),
            resistance_levels=data.get('resistance_levels'),
            recent_high=data.get('recent_high'),
            recent_low=data.get('recent_low'),
            recent_atr_avg=data.get('recent_atr_avg'),
        )
        
        # Combine results
        overall_valid = zone_result.is_valid
        if confluence_result:
            overall_confidence = (float(confluence_result.confidence_score) + float(zone_result.confidence)) / 2
        else:
            overall_confidence = float(zone_result.confidence)
        
        # Generate recommendation
        if not overall_valid:
            trading_recommendation = "DO NOT TRADE - Zone violations detected"
        elif confluence_result and confluence_result.confidence_score < 0.5:
            trading_recommendation = "CAUTION - Low signal confluence"
        elif confluence_result and confluence_result.signal_direction == 'NEUTRAL':
            trading_recommendation = "NEUTRAL - Insufficient signal strength"
        else:
            direction = confluence_result.signal_direction if confluence_result else 'NEUTRAL'
            trading_recommendation = f"OK TO TRADE - {direction} signal"
        
        return jsonify({
            'confluence_result': {
                'signal_direction': confluence_result.signal_direction if confluence_result else 'N/A',
                'confidence_score': float(confluence_result.confidence_score) if confluence_result else 0.0,
                'confluence_level': confluence_result.confluence_level if confluence_result else 'N/A',
            } if confluence_result else None,
            'zone_result': {
                'is_valid': zone_result.is_valid,
                'reason': zone_result.reason,
                'confidence': float(zone_result.confidence),
            },
            'overall_valid': overall_valid,
            'overall_confidence': float(overall_confidence),
            'trading_recommendation': trading_recommendation,
        }), 200
    
    except Exception as e:
        logger.error(f"Complete validation error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== REGISTRATION ====================

def register_signal_validation_routes(app):
    """Register signal validation blueprint with Flask app."""
    app.register_blueprint(validation_bp)
    logger.info("Registered signal validation routes")
