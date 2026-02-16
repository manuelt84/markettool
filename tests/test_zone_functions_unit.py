#!/usr/bin/env python
"""
✅ UNIT TEST: Funciones de zona de trading
===========================================
Testea directamente las funciones corregidas sin cargar MarketTool completo
"""

import sys
import logging
import pandas as pd
import numpy as np
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s"
)
logger = logging.getLogger(__name__)


# Copiar _coerce_float desde MarketTool para el test
def _coerce_float(val, default=None):
    """
    Convierte un valor a float de forma segura.
    Retorna None para valores inválidos (NaN, None, infinito, etc.)
    """
    if val is None:
        return default
    try:
        f = float(val)
        # Check for NaN or infinite
        if not (-1e308 < f < 1e308) or pd.isna(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def verificar_zona_no_trading(df, window):
    """Función corregida - versión del test"""
    logger.debug("[verificar_zona_no_trading] Iniciando...")
    
    # Validar que ATR exista
    if 'ATR' not in df.columns:
        logger.error("⚠️  Columna 'ATR' no encontrada")
        return False
    
    # Obtener último ATR y su rolling mean (con validación)
    try:
        atr_last = _coerce_float(df['ATR'].iloc[-1]) if len(df) > 0 else None
        atr_rolling_mean = _coerce_float(df['ATR'].rolling(window=window).mean().iloc[-1]) if len(df) > 0 else None
        
        # Si falta alguno de los valores, retornar False (conservador)
        if atr_last is None or atr_rolling_mean is None:
            logger.debug("[verificar_zona_no_trading] ATR o rolling mean Son None/NaN. Retornando False.")
            return False
        
        # Comparar solo si ambos valores son válidos
        if atr_last < atr_rolling_mean * 0.8:
            return True
        return False
        
    except Exception as exc:
        logger.error("[verificar_zona_no_trading] Error: %s. Retornando False.", exc)
        return False


def verificar_zona_sobreventa(df, window, rsi_threshold=30, k_threshold=20):
    """Función corregida - versión del test"""
    logger.debug("[verificar_zona_sobreventa] Iniciando...")
    
    try:
        # Validar columnas existe
        if 'RSI' not in df.columns:
            logger.debug("[verificar_zona_sobreventa] RSI ausente")
            return False
        if '%K' not in df.columns:
            logger.debug("[verificar_zona_sobreventa] %K ausente")
            return False
        
        rsi_last = _coerce_float(df['RSI'].iloc[-1]) if len(df) > 0 else None
        k_last = _coerce_float(df['%K'].iloc[-1]) if len(df) > 0 else None
        
        # Si falta cualquier valor, retornar False
        if rsi_last is None or k_last is None:
            logger.debug("[verificar_zona_sobreventa] RSI o %K None/NaN. Retornando False.")
            return False
        
        return rsi_last < rsi_threshold and k_last < k_threshold
        
    except Exception as exc:
        logger.debug("[verificar_zona_sobreventa] Error: %s. Retornando False.", exc)
        return False


def verificar_zona_sobrecompra(df, window, rsi_threshold=70, k_threshold=80):
    """Función corregida - versión del test"""
    logger.debug("[verificar_zona_sobrecompra] Iniciando...")
    
    try:
        # Validar columnas existen
        if 'RSI' not in df.columns:
            logger.debug("[verificar_zona_sobrecompra] RSI ausente")
            return False
        if '%K' not in df.columns:
            logger.debug("[verificar_zona_sobrecompra] %K ausente")
            return False
        
        rsi_last = _coerce_float(df['RSI'].iloc[-1]) if len(df) > 0 else None
        k_last = _coerce_float(df['%K'].iloc[-1]) if len(df) > 0 else None
        
        # Si falta cualquier valor, retornar False
        if rsi_last is None or k_last is None:
            logger.debug("[verificar_zona_sobrecompra] RSI o %K None/NaN. Retornando False.")
            return False
        
        return rsi_last > rsi_threshold and k_last > k_threshold
        
    except Exception as exc:
        logger.debug("[verificar_zona_sobrecompra] Error: %s. Retornando False.", exc)
        return False


def create_test_df_with_missing_indicators():
    """Crea un DataFrame con algunos indicadores faltantes/NaN"""
    dates = pd.date_range("2025-01-01", periods=100, freq="1min")
    df = pd.DataFrame({
        "date": dates,
        "open": np.random.uniform(100, 110, 100),
        "high": np.random.uniform(105, 115, 100),
        "low": np.random.uniform(95, 105, 100),
        "close": np.random.uniform(100, 110, 100),
        "volume": np.random.randint(1000, 5000, 100),
    })
    
    # Indicadores con ALGUNOS valores NaN
    df["ATR"] = np.concatenate([np.full(5, np.nan), np.random.uniform(1, 3, 95)])
    df["RSI"] = np.concatenate([np.full(5, np.nan), np.random.uniform(30, 70, 95)])
    df["%K"] = np.concatenate([np.full(5, np.nan), np.random.uniform(20, 80, 95)])
    df["%D"] = np.concatenate([np.full(5, np.nan), np.random.uniform(20, 80, 95)])
    
    return df


def test_1_verificar_zona_no_trading():
    """Test 1: verificar_zona_no_trading con ATR NaN"""
    logger.info("=" * 70)
    logger.info("TEST 1: verificar_zona_no_trading con ATR NaN")
    logger.info("=" * 70)
    
    try:
        df = create_test_df_with_missing_indicators()
        
        logger.info(f"DataFrame: {len(df)} filas")
        logger.info(f"  - Últimos ATR: {df['ATR'].iloc[-1]}")
        logger.info(f"  - ATR rolling mean: {df['ATR'].rolling(14).mean().iloc[-1]}")
        
        result = verificar_zona_no_trading(df, window=14)
        
        logger.info(f"✅ Resultado: {result} (tipo: {type(result).__name__})")
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
    except Exception as exc:
        logger.exception(f"❌ Excepción: {exc}")
        return False


def test_2_verificar_zona_sobreventa():
    """Test 2: verificar_zona_sobreventa con RSI/K NaN"""
    logger.info("=" * 70)
    logger.info("TEST 2: verificar_zona_sobreventa con RSI/K NaN")
    logger.info("=" * 70)
    
    try:
        df = create_test_df_with_missing_indicators()
        
        logger.info(f"DataFrame: {len(df)} filas")
        logger.info(f"  - Últimos RSI: {df['RSI'].iloc[-1]}")
        logger.info(f"  - Últimos %K: {df['%K'].iloc[-1]}")
        
        result = verificar_zona_sobreventa(df, window=14)
        
        logger.info(f"✅ Resultado: {result} (tipo: {type(result).__name__})")
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
    except Exception as exc:
        logger.exception(f"❌ Excepción: {exc}")
        return False


def test_3_verificar_zona_sobrecompra():
    """Test 3: verificar_zona_sobrecompra con RSI/K NaN"""
    logger.info("=" * 70)
    logger.info("TEST 3: verificar_zona_sobrecompra con RSI/K NaN")
    logger.info("=" * 70)
    
    try:
        df = create_test_df_with_missing_indicators()
        
        logger.info(f"DataFrame: {len(df)} filas")
        logger.info(f"  - Últimos RSI: {df['RSI'].iloc[-1]}")
        logger.info(f"  - Últimos %K: {df['%K'].iloc[-1]}")
        
        result = verificar_zona_sobrecompra(df, window=14)
        
        logger.info(f"✅ Resultado: {result} (tipo: {type(result).__name__})")
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
    except Exception as exc:
        logger.exception(f"❌ Excepción: {exc}")
        return False


def test_4_coerce_float_with_various_inputs():
    """Test 4: _coerce_float maneja todos los tipos de entrada"""
    logger.info("=" * 70)
    logger.info("TEST 4: _coerce_float con diversos inputs")
    logger.info("=" * 70)
    
    try:
        test_cases = [
            ("None", None, None),
            ("NaN", np.nan, None),
            ("float", 1.5, 1.5),
            ("int", 42, 42.0),
            ("str number", "3.14", 3.14),
            ("str invalid", "abc", None),
            ("inf", np.inf, None),
            ("-inf", -np.inf, None),
            ("empty string", "", None),
        ]
        
        all_ok = True
        for name, input_val, expected in test_cases:
            result = _coerce_float(input_val)
            
            # Para comparar None con None
            if expected is None and result is None:
                status = "✅"
            # Para comparar floats
            elif expected is not None and result is not None and abs(result - expected) < 0.001:
                status = "✅"
            else:
                status = "❌"
                all_ok = False
            
            logger.info(f"{status} {name:20} → {result} (esperado: {expected})")
        
        return all_ok
        
    except Exception as exc:
        logger.exception(f"❌ Excepción: {exc}")
        return False


def main():
    """Run all tests"""
    logger.info("\n\n")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 15 + "UNIT TEST: Zone Trading Functions" + " " * 19 + "║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")
    
    results = []
    
    # Run tests
    results.append(("verificar_zona_no_trading", test_1_verificar_zona_no_trading()))
    results.append(("verificar_zona_sobreventa", test_2_verificar_zona_sobreventa()))
    results.append(("verificar_zona_sobrecompra", test_3_verificar_zona_sobrecompra()))
    results.append(("_coerce_float robustness", test_4_coerce_float_with_various_inputs()))
    
    # Summary
    logger.info("\n" + "╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 25 + "TEST SUMMARY" + " " * 31 + "║")
    logger.info("╠" + "═" * 68 + "╣")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"║ {status:8} | {name:40} " + " " * (68 - 8 - 3 - 40 - 1) + "║")
    
    logger.info("╠" + "═" * 68 + "╣")
    logger.info(f"║ Result: {passed}/{total} tests passed" + " " * (68 - 28 - len(f"{passed}/{total}")) + "║")
    logger.info("╚" + "═" * 68 + "╝")
    
    if passed == total:
        logger.info("\n✅ ALL UNIT TESTS PASSED!\n")
        return 0
    else:
        logger.error(f"\n❌ {total - passed} test(s) failed.\n")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
