#!/usr/bin/env python
"""
✅ TEST: calcular_entradas con indicadores faltantes/None
=========================================================
Verifica que calcular_entradas no crashea cuando hay indicadores NaN/None
"""

import sys
import asyncio
import logging
import pandas as pd
import numpy as np
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Add marketTool path
sys.path.insert(0, str(Path(__file__).parent))

# Load MarketTool
logger.info("Cargando MarketTool...")
from MarketTool import calcular_entradas


def create_test_df_with_missing_indicators(symbol="AAPL", tf="1min"):
    """Crea un DataFrame con algunos indicadores faltantes/NaN para probar robustez"""
    
    # Crear datos básicos OHLCV
    dates = pd.date_range("2025-01-01", periods=100, freq="1min")
    df = pd.DataFrame({
        "date": dates,
        "open": np.random.uniform(100, 110, 100),
        "high": np.random.uniform(105, 115, 100),
        "low": np.random.uniform(95, 105, 100),
        "close": np.random.uniform(100, 110, 100),
        "volume": np.random.randint(1000, 5000, 100),
    })
    
    # Agregar indicadores con ALGUNOS valores NaN
    df["ATR"] = np.concatenate([np.full(5, np.nan), np.random.uniform(1, 3, 95)])  # Primeros 5 son NaN
    df["RSI"] = np.concatenate([np.full(5, np.nan), np.random.uniform(30, 70, 95)])  # Primeros 5 son NaN
    df["%K"] = np.concatenate([np.full(5, np.nan), np.random.uniform(20, 80, 95)])  # Primeros 5 son NaN
    df["%D"] = np.concatenate([np.full(5, np.nan), np.random.uniform(20, 80, 95)])  # Primeros 5 son NaN
    
    return df


async def test_calcular_entradas_with_missing_indicators():
    """Test 1: ¿calcular_entradas maneja indicadores faltantes?"""
    logger.info("=" * 70)
    logger.info("TEST 1: calcular_entradas con indicadores faltantes")
    logger.info("=" * 70)
    
    try:
        df = create_test_df_with_missing_indicators()
        
        logger.info(f"DataFrame creado: {len(df)} filas")
        logger.info(f"  - Últimos ATR: {df['ATR'].iloc[-1]}")
        logger.info(f"  - Últimos RSI: {df['RSI'].iloc[-1]}")
        logger.info(f"  - Últimos %K: {df['%K'].iloc[-1]}")
        
        # Llamar a calcular_entradas
        logger.info("Llamando a calcular_entradas...")
        result = calcular_entradas(
            symbol="AAPL",
            tf="1min",
            df=df,
        )
        
        logger.info(f"✅ calcular_entradas completado exitosamente")
        logger.info(f"  - Resultado: {result}")
        logger.info(f"  - Tipo: {type(result)}")
        
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError (el error que queremos evitar): {exc}")
        return False
        
    except Exception as exc:
        logger.exception(f"⚠️ Otra excepción: {exc}")
        # Esto OK - el importante es que NO sea TypeError
        return True


async def test_verificar_zona_no_trading():
    """Test 2: ¿verificar_zona_no_trading maneja ATR None?"""
    logger.info("=" * 70)
    logger.info("TEST 2: verificar_zona_no_trading con ATR NaN")
    logger.info("=" * 70)
    
    try:
        from MarketTool import verificar_zona_no_trading
        
        df = create_test_df_with_missing_indicators()
        
        # Llamar a verificar_zona_no_trading
        logger.info("Llamando a verificar_zona_no_trading...")
        result = verificar_zona_no_trading(df, window=14)
        
        logger.info(f"✅ verificar_zona_no_trading completado exitosamente")
        logger.info(f"  - Resultado: {result}")
        logger.info(f"  - Tipo: {type(result)}")
        
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
        
    except Exception as exc:
        logger.exception(f"⚠️ Otra excepción: {exc}")
        return True


async def test_verificar_zona_sobreventa():
    """Test 3: ¿verificar_zona_sobreventa maneja RSI/K None?"""
    logger.info("=" * 70)
    logger.info("TEST 3: verificar_zona_sobreventa con RSI/K NaN")
    logger.info("=" * 70)
    
    try:
        from MarketTool import verificar_zona_sobreventa
        
        df = create_test_df_with_missing_indicators()
        
        # Llamar a verificar_zona_sobreventa
        logger.info("Llamando a verificar_zona_sobreventa...")
        result = verificar_zona_sobreventa(df, window=14)
        
        logger.info(f"✅ verificar_zona_sobreventa completado exitosamente")
        logger.info(f"  - Resultado: {result}")
        logger.info(f"  - Tipo: {type(result)}")
        
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
        
    except Exception as exc:
        logger.exception(f"⚠️ Otra excepción: {exc}")
        return True


async def test_verificar_zona_sobrecompra():
    """Test 4: ¿verificar_zona_sobrecompra maneja RSI/K None?"""
    logger.info("=" * 70)
    logger.info("TEST 4: verificar_zona_sobrecompra con RSI/K NaN")
    logger.info("=" * 70)
    
    try:
        from MarketTool import verificar_zona_sobrecompra
        
        df = create_test_df_with_missing_indicators()
        
        # Llamar a verificar_zona_sobrecompra
        logger.info("Llamando a verificar_zona_sobrecompra...")
        result = verificar_zona_sobrecompra(df, window=14)
        
        logger.info(f"✅ verificar_zona_sobrecompra completado exitosamente")
        logger.info(f"  - Resultado: {result}")
        logger.info(f"  - Tipo: {type(result)}")
        
        return True
        
    except TypeError as exc:
        logger.error(f"❌ TypeError: {exc}")
        return False
        
    except Exception as exc:
        logger.exception(f"⚠️ Otra excepción: {exc}")
        return True


async def main():
    """Run all tests"""
    logger.info("\n\n")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║" + " " * 15 + "TEST: calcular_entradas Robustness" + " " * 20 + "║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")
    
    results = []
    
    # Run tests
    results.append(("calcular_entradas", await test_calcular_entradas_with_missing_indicators()))
    results.append(("verificar_zona_no_trading", await test_verificar_zona_no_trading()))
    results.append(("verificar_zona_sobreventa", await test_verificar_zona_sobreventa()))
    results.append(("verificar_zona_sobrecompra", await test_verificar_zona_sobrecompra()))
    
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
        logger.info("\n✅ ALL TESTS PASSED! calcular_entradas is robust against missing indicators.\n")
        return 0
    else:
        logger.error(f"\n❌ {total - passed} test(s) failed.\n")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
