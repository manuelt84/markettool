"""
API Routes for Risk Management Services

Phase 8: Microservices/API - Expose risk management via REST endpoints.

Services exposed:
- Position sizing (fixed fractional + Kelly Criterion)
- Drawdown risk analysis
- Portfolio exposure calculation
- Risk metrics calculation
"""

import logging
from typing import Dict, Any, List, Optional
from flask import Blueprint, request, jsonify

from markettool.application.services import get_risk_service

logger = logging.getLogger(__name__)

# Blueprint for risk management services
risk_bp = Blueprint('risk_management', __name__, url_prefix='/api/v1/risk')


# ==================== POSITION SIZING ROUTES ====================

@risk_bp.route('/position-size', methods=['POST'])
def calculate_position_size():
    """
    Calculate optimal position size using fixed fractional and/or Kelly Criterion.
    
    Request body:
    {
        "account_balance": float,
        "entry_price": float,
        "stop_loss": float,
        "take_profit": float,
        "risk_pct": float (optional, default 0.02),
        "use_kelly": bool (optional, default false),
        "win_rate": float (optional, default 0.55),
        "avg_win": float (optional, default 1.0),
        "avg_loss": float (optional, default 0.9)
    }
    
    Response:
    {
        "position_size": float,
        "risk_amount": float,
        "reward_amount": float,
        "risk_reward_ratio": float,
        "kelly_fraction": float,
        "position_size_kelly": float,
        "max_loss_pct": float,
        "expectancy": float,
        "warning": str (optional)
    }
    """
    try:
        data = request.get_json()
        
        account_balance = data.get('account_balance')
        entry_price = data.get('entry_price')
        stop_loss = data.get('stop_loss')
        take_profit = data.get('take_profit')
        risk_pct = data.get('risk_pct', 0.02)
        use_kelly = data.get('use_kelly', False)
        win_rate = data.get('win_rate', 0.55)
        avg_win = data.get('avg_win', 1.0)
        avg_loss = data.get('avg_loss', 0.9)
        
        # Validate inputs
        if not all([account_balance, entry_price, stop_loss, take_profit]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        risk_service = get_risk_service()
        metrics = risk_service.calculate_position_size(
            account_balance=account_balance,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_pct=risk_pct,
            use_kelly=use_kelly,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss
        )
        
        return jsonify({
            'position_size': float(metrics.position_size),
            'risk_amount': float(metrics.risk_amount),
            'reward_amount': float(metrics.reward_amount),
            'risk_reward_ratio': float(metrics.risk_reward_ratio),
            'kelly_fraction': float(metrics.kelly_fraction),
            'position_size_kelly': float(metrics.position_size_kelly),
            'max_loss_pct': float(metrics.max_loss_pct),
            'expectancy': float(metrics.expectancy),
            'warning': metrics.warning,
        }), 200
    
    except Exception as e:
        logger.error(f"Position size error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== DRAWDOWN ROUTES ====================

@risk_bp.route('/drawdown-risk', methods=['POST'])
def calculate_drawdown_risk():
    """
    Calculate risk of drawdown from consecutive losses.
    
    Request body:
    {
        "account_balance": float,
        "consecutive_losses": int (optional, default 5),
        "loss_per_trade": float (optional, default 0.02)
    }
    
    Response:
    {
        "max_drawdown_pct": float,
        "max_drawdown_amount": float,
        "balance_after_drawdown": float,
        "recovery_trades_needed": int,
        "recovery_trades_win_rate": float
    }
    """
    try:
        data = request.get_json()
        
        account_balance = data.get('account_balance')
        consecutive_losses = data.get('consecutive_losses', 5)
        loss_per_trade = data.get('loss_per_trade', 0.02)
        
        if not account_balance:
            return jsonify({'error': 'Missing account_balance'}), 400
        
        risk_service = get_risk_service()
        result = risk_service.calculate_drawdown_risk(
            account_balance=account_balance,
            consecutive_losses=consecutive_losses,
            loss_per_trade=loss_per_trade
        )
        
        return jsonify({
            'consecutive_losses': consecutive_losses,
            'loss_per_trade_pct': loss_per_trade,
            'max_drawdown_pct': float(result.get('max_drawdown_pct', 0)),
            'max_drawdown_amount': float(result.get('max_drawdown_amount', 0)),
            'balance_after_drawdown': float(result.get('balance_after_drawdown', 0)),
            'recovery_trades_needed': int(result.get('recovery_trades_needed', 0)),
            'recovery_trades_win_rate': float(result.get('recovery_trades_win_rate', 0.55)),
        }), 200
    
    except Exception as e:
        logger.error(f"Drawdown risk error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== PORTFOLIO EXPOSURE ROUTES ====================

@risk_bp.route('/portfolio-exposure', methods=['POST'])
def calculate_portfolio_exposure():
    """
    Calculate total portfolio exposure across multiple positions.
    
    Request body:
    {
        "account_balance": float,
        "positions": [
            {
                "symbol": str,
                "quantity": float,
                "entry_price": float,
                "current_price": float,
                "position_type": "long|short" (optional, default 'long')
            },
            ...
        ]
    }
    
    Response:
    {
        "total_exposure_pct": float,
        "total_exposure_amount": float,
        "positions_count": int,
        "long_positions": int,
        "short_positions": int,
        "average_position_size": float,
        "max_position_size": float,
        "min_position_size": float,
        "concentration_risk": float,
        "is_over_exposed": bool,
        "available_margin": float
    }
    """
    try:
        data = request.get_json()
        
        account_balance = data.get('account_balance')
        positions = data.get('positions', [])
        
        if not account_balance:
            return jsonify({'error': 'Missing account_balance'}), 400
        
        if not positions:
            return jsonify({'error': 'No positions provided'}), 400
        
        risk_service = get_risk_service()
        result = risk_service.calculate_portfolio_exposure(
            positions=positions,
            account_balance=account_balance
        )
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"Portfolio exposure error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== BATCH RISK ANALYSIS ROUTES ====================

@risk_bp.route('/batch-analysis', methods=['POST'])
def batch_risk_analysis():
    """
    Perform risk analysis on multiple scenarios.
    
    Request body:
    {
        "account_balance": float,
        "scenarios": [
            {
                "symbol": str,
                "entry_price": float,
                "stop_loss": float,
                "take_profit": float,
                "quantity": float (optional)
            },
            ...
        ]
    }
    
    Response:
    {
        "results": [
            {
                "symbol": str,
                "position_size": float,
                "risk_reward_ratio": float,
                ...
            },
            ...
        ],
        "portfolio_summary": {
            "total_risk": float,
            "total_reward": float,
            "combined_rrr": float
        }
    }
    """
    try:
        data = request.get_json()
        
        account_balance = data.get('account_balance')
        scenarios = data.get('scenarios', [])
        
        if not account_balance or not scenarios:
            return jsonify({'error': 'Missing account_balance or scenarios'}), 400
        
        risk_service = get_risk_service()
        results = []
        total_risk = 0
        total_reward = 0
        
        for scenario in scenarios:
            try:
                metrics = risk_service.calculate_position_size(
                    account_balance=account_balance,
                    entry_price=scenario.get('entry_price'),
                    stop_loss=scenario.get('stop_loss'),
                    take_profit=scenario.get('take_profit'),
                )
                
                results.append({
                    'symbol': scenario.get('symbol'),
                    'position_size': float(metrics.position_size),
                    'risk_amount': float(metrics.risk_amount),
                    'reward_amount': float(metrics.reward_amount),
                    'risk_reward_ratio': float(metrics.risk_reward_ratio),
                })
                
                total_risk += metrics.risk_amount
                total_reward += metrics.reward_amount
            
            except Exception as sc_e:
                logger.warning(f"Scenario {scenario.get('symbol')} error: {sc_e}")
                results.append({
                    'symbol': scenario.get('symbol'),
                    'error': str(sc_e)
                })
        
        combined_rrr = total_reward / total_risk if total_risk > 0 else 0
        
        return jsonify({
            'results': results,
            'portfolio_summary': {
                'total_risk': float(total_risk),
                'total_reward': float(total_reward),
                'combined_rrr': float(combined_rrr),
                'average_position_rrr': float(sum(r.get('risk_reward_ratio', 1) for r in results if 'risk_reward_ratio' in r) / max(1, len([r for r in results if 'risk_reward_ratio' in r])))
            }
        }), 200
    
    except Exception as e:
        logger.error(f"Batch risk analysis error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== KELLY CRITERION ROUTES ====================

@risk_bp.route('/kelly-criterion', methods=['POST'])
def analyze_kelly_criterion():
    """
    Detailed Kelly Criterion analysis.
    
    Request body:
    {
        "account_balance": float,
        "win_rate": float (0.0-1.0),
        "average_win": float,
        "average_loss": float,
        "entry_price": float,
        "stop_loss": float,
        "take_profit": float
    }
    
    Response:
    {
        "kelly_fraction": float,
        "kelly_percentage": float,
        "recommended_position_size": float,
        "risk_assessment": str,
        "notes": [str, ...]
    }
    """
    try:
        data = request.get_json()
        
        account_balance = data.get('account_balance')
        win_rate = data.get('win_rate')
        avg_win = data.get('average_win')
        avg_loss = data.get('average_loss')
        entry_price = data.get('entry_price')
        stop_loss = data.get('stop_loss')
        take_profit = data.get('take_profit')
        
        if not all([account_balance, win_rate, avg_win, avg_loss, entry_price, stop_loss, take_profit]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        risk_service = get_risk_service()
        metrics = risk_service.calculate_position_size(
            account_balance=account_balance,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            use_kelly=True,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss
        )
        
        # Generate Kelly assessment notes
        notes = []
        if metrics.kelly_fraction < 0.01:
            notes.append("Kelly fraction < 1% - Not recommended to trade")
        elif metrics.kelly_fraction <= 0.05:
            notes.append("Kelly fraction 1-5% - Very conservative sizing")
        elif metrics.kelly_fraction <= 0.10:
            notes.append("Kelly fraction 5-10% - Conservative sizing")
        elif metrics.kelly_fraction <= 0.25:
            notes.append("Kelly fraction 10-25% - Moderate sizing")
        else:
            notes.append("Kelly fraction > 25% - Aggressive sizing")
        
        if metrics.expectancy <= 0:
            notes.append("Expectancy <= 0 - Strategy is not profitable")
        elif metrics.expectancy < 0.1:
            notes.append("Low expectancy - Consider improving strategy")
        
        return jsonify({
            'kelly_fraction': float(metrics.kelly_fraction),
            'kelly_percentage': float(metrics.kelly_fraction * 100),
            'recommended_position_size': float(metrics.position_size_kelly),
            'expectancy': float(metrics.expectancy),
            'risk_reward_ratio': float(metrics.risk_reward_ratio),
            'risk_assessment': 'Conservative' if metrics.kelly_fraction < 0.10 else 'Moderate' if metrics.kelly_fraction < 0.25 else 'Aggressive',
            'notes': notes,
        }), 200
    
    except Exception as e:
        logger.error(f"Kelly criterion error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== REGISTRATION ====================

def register_risk_management_routes(app):
    """Register risk management blueprint with Flask app."""
    app.register_blueprint(risk_bp)
    logger.info("Registered risk management routes")
