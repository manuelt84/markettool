#!/usr/bin/env python3
"""
Script de prueba para verificar que el timezone fix está aplicado correctamente.
Hace llamadas directas a FMP API y verifica los timestamps.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import pytz

# Add parent directory to path
sys.path.insert(0, '/app')

from markettool.infra.fmp.client import FMPClient
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_timezone_fix():
    """Test que el timezone fix está funcionando correctamente."""
    
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        logger.error("❌ FMP_API_KEY no configurada")
        return False
    
    # Crear FMP client
    fmp = FMPClient(
        api_key=api_key,
        requests_per_minute=300,
        request_delay=0.2,
        intraday_source_tz="America/New_York",
        logger=logger
    )
    
    # Test: Obtener última hora de EURUSD
    logger.info("=" * 80)
    logger.info("🧪 PRUEBA: Obteniendo última hora de datos EURUSD")
    logger.info("=" * 80)
    
    now_utc = datetime.now(timezone.utc)
    one_hour_ago = now_utc - timedelta(hours=1)
    
    logger.info(f"")
    logger.info(f"📅 Hora actual UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📅 Hace 1 hora UTC: {one_hour_ago.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Convertir a NY time para comparar
    ny_tz = pytz.timezone("America/New_York")
    now_ny = now_utc.astimezone(ny_tz)
    one_hour_ago_ny = one_hour_ago.astimezone(ny_tz)
    
    logger.info(f"📅 Hora actual NY: {now_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"📅 Hace 1 hora NY: {one_hour_ago_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"")
    
    # Llamar a FMP API
    df = fmp.historical_intraday("EURUSD", "1min", one_hour_ago, now_utc)
    
    if df is None or df.empty:
        logger.error("❌ No se obtuvieron datos de FMP")
        return False
    
    logger.info(f"")
    logger.info(f"✅ Recibidas {len(df)} velas")
    logger.info(f"")
    
    # Verificar que las velas estén en el rango correcto
    first_candle_time = df.index[0]
    last_candle_time = df.index[-1]
    
    logger.info(f"📊 Primera vela: {first_candle_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"📊 Última vela: {last_candle_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"")
    
    # Verificar que la última vela es reciente (dentro de las últimas 2 horas)
    time_diff = now_utc - last_candle_time.tz_convert(timezone.utc)
    minutes_diff = time_diff.total_seconds() / 60
    
    logger.info(f"⏱️  Diferencia con hora actual: {minutes_diff:.1f} minutos")
    logger.info(f"")
    
    if minutes_diff > 120:  # Si la última vela es de hace más de 2 horas
        logger.error(f"❌ PROBLEMA: La última vela es de hace {minutes_diff:.1f} minutos")
        logger.error(f"   Esto indica que el timezone fix NO está aplicado correctamente")
        logger.error(f"   o que FMP está devolviendo datos desactualizados.")
        return False
    
    logger.info("=" * 80)
    logger.info("✅ TIMEZONE FIX FUNCIONANDO CORRECTAMENTE")
    logger.info("=" * 80)
    logger.info(f"   Las velas están en el rango temporal correcto")
    logger.info(f"   Última vela tiene {minutes_diff:.1f} minutos de antigüedad")
    
    return True

if __name__ == "__main__":
    try:
        success = test_timezone_fix()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception("❌ Error durante la prueba")
        sys.exit(1)
