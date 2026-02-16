"""
📊 PARALLEL ANALYSIS ENGINE - Máximo paralelismo en 3 niveles
============================================================
Nivel 1: Multi-Asset (root orchestrator)
Nivel 2: Multi-Timeframe (per asset)
Nivel 3: Entry Calculation (parallel indicators + predictions)
"""

import asyncio
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
import time
import psutil

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    """Configuración de paralelismo para análisis"""
    # Nivel 1: Multi-asset
    max_concurrent_assets: int = 18         # Máx activos simultáneoas (intermediate: 18)
    batch_size_assets: int = 16             # Activos por batch (aumentado)
    
    # Nivel 2: Multi-timeframe
    timeframe_fan_out: int = 7              # TF paralelos por activo (intermediate: 7)
    ordered_tfs: List[str] = None           # TF ordenados por coste
    
    # Nivel 3: Entry calculation
    entry_calc_workers: int = 4             # Workers para entrada/activo
    predict_workers_arima: int = 3          # ARIMA predicciones (aumentado)
    predict_workers_mc: int = 4             # Monte Carlo (aumentado)
    
    # Timeouts y límites
    global_timeout: int = 300               # 5 minutos total
    timeout_per_batch: int = 120            # 2 minutos por batch
    timeout_per_asset: int = 50             # 50 segundos por activo (reducido de 60)
    timeout_per_tf: int = 10                # 10 segundos por TF (reducido de 15 para ser más agresivo)
    timeout_prediction_arima: int = 7       # 7 segundos máximo por predicción ARIMA (aumentado de 5)
    timeout_prediction_mc: int = 3          # 3 segundos máximo por predicción Monte Carlo
    
    # Memory management
    max_ram_percent: float = 80             # Pausa si > 80% RAM
    early_exit_confidence: float = 0.85     # Exit si confidence > 85%
    
    def __post_init__(self):
        if self.ordered_tfs is None:
            # Ordena TF por coste computacional (mayor primero)
            self.ordered_tfs = sorted(
                ['1min', '5min', '15min', '30min', '1hour', '4hour', '1day', '1week'],
                key=lambda tf: {
                    '1min': 1000, '5min': 800, '15min': 600,
                    '30min': 400, '1hour': 300, '4hour': 150,
                    '1day': 50, '1week': 20
                }.get(tf, 100),
                reverse=True  # Mayor coste primero (llenar workers)
            )


class ParallelAnalysisEngine:
    """Motor de análisis paralelo en 3 niveles"""
    
    def __init__(
        self,
        indicators_executor=None,
        prediction_executor=None,
        analysis_executor=None,
        config: Optional[AnalysisConfig] = None
    ):
        """
        Args:
            indicators_executor: ThreadPoolExecutor para indicadores
            prediction_executor: ProcessPoolExecutor para predicciones
            analysis_executor: ThreadPoolExecutor para análisis general
            config: Configuración de paralelismo
        """
        self.indicators_executor = indicators_executor
        self.prediction_executor = prediction_executor
        self.analysis_executor = analysis_executor
        self.config = config or AnalysisConfig()
        
        # Semáforos por nivel
        self.asset_sem = asyncio.Semaphore(self.config.max_concurrent_assets)
        self.tf_sem = asyncio.Semaphore(self.config.timeframe_fan_out)
        self.predict_sem_arima = asyncio.Semaphore(self.config.predict_workers_arima)
        self.predict_sem_mc = asyncio.Semaphore(self.config.predict_workers_mc)
    
    # =========================================================================
    # NIVEL 1: MULTI-ASSET (ROOT ORCHESTRATOR)
    # =========================================================================
    
    async def analyze_assets_parallel(
        self,
        symbols: List[str],
        tfs: List[str],
        load_history_fn,
        analyze_asset_fn,
        on_progress=None
    ) -> Dict[str, any]:
        """
        Raíz de la pirámide: analiza múltiples activos en paralelo.
        
        Args:
            symbols: Lista de símbolos a analizar
            tfs: Lista de timeframes
            load_history_fn: async fn(symbol, tf) -> DataFrame
            analyze_asset_fn: async fn(symbol, tfs, histdata) -> SignalSet
            on_progress: Callback con (completed, total)
        
        Returns:
            {symbol: signals_dict, ...}
        """
        logger.info(f"[Parallel] Iniciando análisis de {len(symbols)} activos")
        
        results = {}
        completed = 0
        
        async def _analyze_one_asset(symbol: str):
            nonlocal completed
            
            async with self.asset_sem:
                try:
                    # Pre-fetch históricos EN PARALELO
                    hist_data = await self._prefetch_historical_data(symbol, tfs, load_history_fn)
                    
                    # Análisis del activo (nivel 2)
                    signals = await self._analyze_asset_timeframes(
                        symbol, tfs, hist_data, analyze_asset_fn
                    )
                    
                    completed += 1
                    if on_progress:
                        on_progress(completed, len(symbols))
                    
                    return signals
                
                except asyncio.TimeoutError:
                    logger.warning(f"[Parallel] Asset timeout: {symbol}")
                    return None
                except Exception as e:
                    logger.error(f"[Parallel] Asset error {symbol}: {e}")
                    return None
        
        # Memory guard: pausa si RAM > 80%
        async def _check_memory():
            while True:
                mem_pct = psutil.virtual_memory().percent
                if mem_pct > self.config.max_ram_percent:
                    logger.warning(f"[Parallel] Memory {mem_pct:.1f}% > {self.config.max_ram_percent}%, pausing")
                    await asyncio.sleep(5)
                else:
                    break
        
        # Lanzar análisis en batches
        for batch_idx in range(0, len(symbols), self.config.batch_size_assets):
            await _check_memory()  # Verificar memoria antes de batch
            
            batch_symbols = symbols[batch_idx:batch_idx + self.config.batch_size_assets]
            logger.info(f"[Parallel] Batch {batch_idx // self.config.batch_size_assets + 1}: {len(batch_symbols)} activos")
            
            try:
                batch_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_analyze_one_asset(s) for s in batch_symbols],
                        return_exceptions=True
                    ),
                    timeout=self.config.timeout_per_batch
                )
                
                for symbol, signal_result in zip(batch_symbols, batch_results):
                    if not isinstance(signal_result, Exception):
                        results[symbol] = signal_result
            
            except asyncio.TimeoutError:
                logger.warning(f"[Parallel] Batch timeout, partial results")
                results.update({s: None for s in batch_symbols})
        
        logger.info(f"[Parallel] Completado: {len(results)} activos con resultados")
        return results
    
    # =========================================================================
    # NIVEL 2: MULTI-TIMEFRAME (PER ASSET)
    # =========================================================================
    
    async def _prefetch_historical_data(
        self,
        symbol: str,
        tfs: List[str],
        load_history_fn
    ) -> Dict[str, pd.DataFrame]:
        """
        Pre-carga datos históricos de TODOS los TF en paralelo.
        Esto maximiza IO parallelism antes del análisis CPU-bound.
        """
        logger.debug(f"[Prefetch] Cargando históricos: {symbol} × {len(tfs)} TF")
        
        async def _load_one_tf(tf: str):
            try:
                return await load_history_fn(symbol, tf)
            except Exception as e:
                logger.debug(f"[Prefetch] Error {symbol}/{tf}: {e}")
                return None
        
        results = await asyncio.gather(
            *[_load_one_tf(tf) for tf in tfs],
            return_exceptions=True
        )
        
        return {tfs[i]: results[i] for i in range(len(tfs))}
    
    async def _analyze_asset_timeframes(
        self,
        symbol: str,
        tfs: List[str],
        hist_data: Dict[str, pd.DataFrame],
        analyze_asset_fn
    ) -> Dict:
        """
        Analiza múltiples timeframes para un activo en paralelo.
        
        OPTIMIZACIONES:
        - Ordena TF por coste (mayor primero, para llenar workers)
        - Early exit si confidence > threshold
        - Limita a timeframe_fan_out simultáneoas
        """
        logger.debug(f"[Asset] Analizando {symbol}: {len(tfs)} TF")
        
        # Filtrar TF que tengan datos
        valid_tfs = [
            tf for tf in self.config.ordered_tfs
            if tf in tfs and hist_data.get(tf) is not None and not hist_data[tf].empty
        ]
        
        results_by_tf = {}
        strongest_signal = None
        
        async def _analyze_one_tf(tf: str):
            async with self.tf_sem:
                try:
                    hist = hist_data[tf]
                    
                    # Análisis del TF (nivel 3)
                    signal = await self._analyze_tf_entry_signals(
                        symbol, tf, hist, analyze_asset_fn
                    )
                    
                    # Early exit si confidence muy alta
                    nonlocal strongest_signal
                    if signal and signal.get('confidence', 0) > self.config.early_exit_confidence:
                        logger.info(f"[Asset] Early exit {symbol}/{tf}: confidence={signal['confidence']:.2f}")
                        strongest_signal = signal
                    
                    return signal
                
                except asyncio.TimeoutError:
                    logger.warning(f"[Asset] TF timeout: {symbol}/{tf}")
                    return None
                except Exception as e:
                    logger.error(f"[Asset] TF error {symbol}/{tf}: {e}")
                    return None
        
        # Lanzar análisis de TF en paralelo
        tasks = [_analyze_one_tf(tf) for tf in valid_tfs]
        tf_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for tf, result in zip(valid_tfs, tf_results):
            if not isinstance(result, Exception) and result:
                results_by_tf[tf] = result
                
                # Parar si ya tenemos signal fuerte (early exit)
                if strongest_signal:
                    logger.info(f"[Asset] Deteniendo análisis de {symbol} por early exit")
                    break
        
        return results_by_tf
    
    # =========================================================================
    # NIVEL 3: ENTRY CALCULATION (PARALLEL INDICATORS + PREDICTIONS)
    # =========================================================================
    
    async def _analyze_tf_entry_signals(
        self,
        symbol: str,
        tf: str,
        df: pd.DataFrame,
        analyze_asset_fn
    ) -> Optional[Dict]:
        """
        Paraleliza al máximo los cálculos dentro de UN timeframe:
        
        PARALELO:
        1) Indicadores técnicos (I/O bound: caché + cálculo rápido)
        2) Detección de patrones (CPU moderate: YOLO en thread)
        3) Predicciones (CPU heavy: ARIMA + Monte Carlo en process)
        4) Confirmación en Firestore (I/O bound)
        
        TODO al mismo tiempo con asyncio.gather()
        """
        logger.debug(f"[TF] Analizando {symbol}/{tf}")
        
        loop = asyncio.get_event_loop()
        
        # ---- TAREA 1: Indicadores Técnicos ----
        async def _get_indicators():
            try:
                # Intenta caché primero (I/O en thread)
                if self.indicators_executor:
                    ind = await loop.run_in_executor(
                        self.indicators_executor,
                        self._load_indicators_cached,
                        symbol, tf
                    )
                    if ind is not None:
                        logger.debug(f"[TF] Indicators from cache: {symbol}/{tf}")
                        return ind
                
                # Computa si no encuentra en caché
                indicators = await loop.run_in_executor(
                    self.analysis_executor,
                    self._compute_indicators_fast,
                    df, tf
                )
                return indicators
            except Exception as e:
                logger.debug(f"[TF] Indicators failed {symbol}/{tf}: {e}")
                return None
        
        # ---- TAREA 2: Detección Patrones (YOLO) ----
        async def _detect_patterns():
            try:
                if self.analysis_executor:
                    patterns = await loop.run_in_executor(
                        self.analysis_executor,
                        self._detect_candle_patterns,
                        df, symbol, tf
                    )
                    return patterns
                return []
            except Exception as e:
                logger.debug(f"[TF] Pattern detection failed {symbol}/{tf}: {e}")
                return []
        
        # ---- TAREA 3: Predicciones ----
        async def _predict_movements():
            try:
                predictions = {'arima': None, 'monte_carlo': None}
                
                # ARIMA en thread pool con semáforo
                if self.analysis_executor:
                    async with self.predict_sem_arima:
                        arima_pred = await loop.run_in_executor(
                            self.analysis_executor,
                            self._predict_arima,
                            df, tf, symbol
                        )
                        predictions['arima'] = arima_pred
                
                # Monte Carlo en process pool CON semáforo
                if self.prediction_executor:
                    async with self.predict_sem_mc:
                        mc_scenarios = await loop.run_in_executor(
                            self.prediction_executor,
                            self._generate_monte_carlo,
                            df, symbol, tf
                        )
                        predictions['monte_carlo'] = mc_scenarios
                
                return predictions
            except Exception as e:
                logger.debug(f"[TF] Predictions failed {symbol}/{tf}: {e}")
                return {'arima': None, 'monte_carlo': None}
        
        # ---- TAREA 4: Confirmación Firestore ----
        async def _get_entry_history():
            try:
                if self.analysis_executor:
                    history = await loop.run_in_executor(
                        self.analysis_executor,
                        self._get_recent_entries,
                        symbol, tf
                    )
                    return history
                return []
            except Exception as e:
                logger.debug(f"[TF] Entry history failed {symbol}/{tf}: {e}")
                return []
        
        # ============================================
        # EJECUCIÓN EN PARALELO MÁXIMO
        # ============================================
        try:
            indicators, patterns, predictions, entry_history = await asyncio.wait_for(
                asyncio.gather(
                    _get_indicators(),
                    _detect_patterns(),
                    _predict_movements(),
                    _get_entry_history(),
                    return_exceptions=True
                ),
                timeout=self.config.timeout_per_tf
            )
        except asyncio.TimeoutError:
            logger.warning(f"[TF] Analysis timeout {symbol}/{tf}")
            return None
        
        # ---- SÍNTESIS DE SEÑALES ----
        signal = self._synthesize_signal(
            symbol, tf, indicators, patterns, predictions, entry_history, df
        )
        
        return signal
    
    # =========================================================================
    # HELPERS: Las funciones reales del análisis (pueden ser async o sync)
    # =========================================================================
    
    def _load_indicators_cached(self, symbol: str, tf: str) -> Optional[Dict]:
        """Carga indicadores del caché. Sincrónico (en executor)."""
        # TODO: Implementar con _INDICATORS_CACHE
        return None
    
    def _compute_indicators_fast(self, df: pd.DataFrame, tf: str) -> Dict:
        """Computa indicadores rápidos. Sincrónico (en executor)."""
        # TODO: RSI, SMA, Bollinger, MACD, etc.
        return {
            'rsi': None,
            'sma': None,
            'bollinger': None,
            'macd': None,
        }
    
    def _detect_candle_patterns(self, df: pd.DataFrame, symbol: str, tf: str) -> List[Dict]:
        """Detecta patrones con YOLO. Sincrónico (en executor)."""
        # TODO: Ejecutar modelo YOLO
        return []
    
    def _predict_arima(self, df: pd.DataFrame, tf: str, symbol: str) -> Optional[Dict]:
        """Predicción ARIMA. Sincrónico (en executor)."""
        # TODO: ARIMA forecast
        return None
    
    def _generate_monte_carlo(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[Dict]:
        """Monte Carlo scenarios. Sincrónico (en executor, procesa CPU-heavy)."""
        # TODO: MC scenarios (en process pool)
        return None
    
    def _get_recent_entries(self, symbol: str, tf: str) -> List[Dict]:
        """Obtiene entradas recientes de Firestore. Sincrónico (en executor)."""
        # TODO: Consultar Firestore
        return []
    
    def _synthesize_signal(
        self,
        symbol: str, tf: str,
        indicators: Optional[Dict],
        patterns: List[Dict],
        predictions: Dict,
        entry_history: List[Dict],
        df: pd.DataFrame
    ) -> Optional[Dict]:
        """Sintetiza la señal final a partir de todos los insumos."""
        
        confidence = 0.0
        signal_type = None
        reasons = []
        
        # Indicadores técnicos
        if indicators and isinstance(indicators, dict):
            if indicators.get('rsi') and indicators['rsi'] < 30:
                confidence += 0.3
                signal_type = 'BUY'
                reasons.append("RSI oversold")
            elif indicators.get('rsi') and indicators['rsi'] > 70:
                confidence += 0.3
                signal_type = 'SELL'
                reasons.append("RSI overbought")
        
        # Patrones
        if patterns:
            confidence += 0.2 * len(patterns)
            signal_type = 'BUY' if 'bullish' in str(patterns).lower() else 'SELL'
            reasons.append(f"{len(patterns)} patrones detectados")
        
        # Predicciones
        if predictions.get('arima'):
            confidence += 0.25
            reasons.append("ARIMA predicción confirmada")
        if predictions.get('monte_carlo'):
            confidence += 0.15
            reasons.append("Monte Carlo scenarios positivos")
        
        # Regularización por historial reciente
        if entry_history:
            # Si ya hay entradas recientes, reduce confidence para evitar falsas señales
            confidence *= 0.7
            reasons.append("Entrada reciente (dampening aplicado)")
        
        # Clamp confidence a [0, 1]
        confidence = min(1.0, max(0.0, confidence))
        
        if confidence > 0.5 and signal_type:
            return {
                'symbol': symbol,
                'timeframe': tf,
                'type': signal_type,
                'confidence': confidence,
                'timestamp': pd.Timestamp.now('UTC').isoformat(),
                'reasons': reasons,
                'indicators': indicators,
                'patterns': patterns,
                'predictions': predictions,
            }
        
        return None


# ========================================================================
# HELPER: Función pública para usar en el scheduler
# ========================================================================

async def run_full_analysis_parallel(
    symbols: List[str],
    tfs: List[str],
    config: Optional[AnalysisConfig] = None,
    # Dependencies (importadas en bootstrap.py)
    load_history_fn=None,
    analyze_asset_fn=None,
    indicators_executor=None,
    prediction_executor=None,
    analysis_executor=None,
) -> Dict[str, Dict]:
    """
    Ejecuta análisis completo con paralelismo máximo.
    
    Uso en scheduler:
    ```python
    async def _update_signals_job():
        symbols = ['AAPL', 'MSFT', 'TSLA', ...]
        tfs = ['1min', '5min', '1hour', '1day']
        results = await run_full_analysis_parallel(
            symbols, tfs,
            load_history_fn=load_cached_history,
            analyze_asset_fn=...,
            indicators_executor=_ANALYSIS_EXECUTOR,
            prediction_executor=_ANALYSIS_PRED_EXECUTOR,
            analysis_executor=_ANALYSIS_EXECUTOR,
        )
    ```
    """
    
    config = config or AnalysisConfig()
    engine = ParallelAnalysisEngine(
        indicators_executor=indicators_executor,
        prediction_executor=prediction_executor,
        analysis_executor=analysis_executor,
        config=config
    )
    
    start_time = time.time()
    
    try:
        results = await engine.analyze_assets_parallel(
            symbols, tfs,
            load_history_fn, analyze_asset_fn,
            on_progress=lambda c, t: logger.info(f"Progress: {c}/{t}")
        )
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Full analysis complete in {elapsed:.1f}s ({len(results)} assets)")
        
        return results
    
    except Exception as e:
        logger.error(f"Full analysis failed: {e}", exc_info=True)
        return {}
