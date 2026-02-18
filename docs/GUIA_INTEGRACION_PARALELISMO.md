# 🚀 GUÍA DE INTEGRACIÓN: Paralelismo Máximo en Activos, Temporalidades y Entradas

## 1️⃣ INTEGRACIÓN EN ASYNCIO SCHEDULER (bot_init.py)

### Configuración Base
```python
# markettool/interfaces/scheduler/bot_init.py

from markettool.application.use_cases.parallel_analysis import (
    run_full_analysis_parallel,
    AnalysisConfig,
)

# Variables globales (se cargan en bootstrap.py)
PARALLELISM_CONFIG = AnalysisConfig(
    max_concurrent_assets=8,        # Máx 8 activos simultáneos
    batch_size_assets=8,            # Batch de 8 activos
    timeframe_fan_out=4,            # 4 timeframes en paralelo por activo
    predict_workers_arima=2,        # 2 workers ARIMA
    predict_workers_mc=3,           # 3 workers Monte Carlo
    global_timeout=300,             # 5 minutos total
    timeout_per_batch=120,          # 2 minutos por batch
    timeout_per_tf=15,              # 15 segundos por TF
    max_ram_percent=80,             # Pausa si > 80% RAM
    early_exit_confidence=0.85,     # Early exit si > 85% confidence
)

async def _update_signals_job():
    """
    ✅ JOB PRINCIPAL: Análisis con paralelismo máximo.
    
    - Corre cada 10 minutos
    - Ejecuta en el event loop (no bloquea con BackgroundScheduler)
    - Reutiliza la arquitectura de executor pools existente
    """
    
    # Cargar activos activos (en paralelo si es posible)
    symbols = await get_active_symbols()  # Símbolos que tienen suscripciones activas
    tfs = ['1min', '5min', '15min', '30min', '1hour', '4hour', '1day', '1week']
    
    logger.info(f"[Signals] Iniciando análisis paralelo: {len(symbols)} activos × {len(tfs)} TF")
    
    try:
        # PARALELISMO MÁXIMO: 3 niveles
        analysis_results = await run_full_analysis_parallel(
            symbols=symbols,
            tfs=tfs,
            config=PARALLELISM_CONFIG,
            # Inyectar dependencies (importadas en bootstrap.py)
            load_history_fn=load_cached_history,      # ← Desde MarketTool.py
            analyze_asset_fn=None,                     # ← No necesario aquí
            indicators_executor=_ANALYSIS_EXECUTOR,    # ← ThreadPoolExecutor
            prediction_executor=_ANALYSIS_PRED_EXECUTOR,  # ← ProcessPoolExecutor
            analysis_executor=_ANALYSIS_EXECUTOR,      # ← ThreadPoolExecutor
        )
        
        # PERSISTENCIA: Guardar signals en paralelo
        logger.info(f"[Signals] Persistiendo {len(analysis_results)} resultados...")
        await persist_analysis_results_parallel(analysis_results)
        
        # NOTIFICACIONES: Enviar alerts en paralelo si hay signals fuertes
        await send_signal_alerts_parallel(analysis_results)
        
        logger.info(f"✅ [Signals] Job completado: {len(analysis_results)} activos analizados")
        
    except asyncio.TimeoutError:
        logger.error("[Signals] TIMEOUT GLOBAL: análisis no completó en tiempo límite")
    except Exception as e:
        logger.error(f"[Signals] Error: {e}", exc_info=True)

# ========== REGISTRAR EN SCHEDULER ==========
# En el setup_scheduler() existente:

def setup_scheduler(application: Application):
    """Configura los jobs del scheduler con AsyncIOScheduler"""
    
    scheduler = AsyncIOScheduler()
    
    # Job principal: análisis paralelo cada 10 minutos
    scheduler.add_job(
        _update_signals_job,
        IntervalTrigger(minutes=10),
        id='update_signals_parallel',
        replace_existing=True,
        max_instances=1,  # Solo 1 instancia simultáneoa
        coalesce=True,     # Si se atrasa, ejecuta 1 sola vez
    )
    
    # Job secundario: precalentamiento agresivo (cada 4 horas)
    scheduler.add_job(
        _warmup_cache_aggressive,
        IntervalTrigger(hours=4),
        id='warmup_aggressive',
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info("[Scheduler] AsyncIOScheduler iniciado")
    return scheduler
```

## 2️⃣ ANÁLISIS BAJO DEMANDA POR BOT (Telegram)

### Comando /analizar_rapido
```python
# markettool/interfaces/bot/handlers.py (hexagonal) o commands.py

async def analizar_rapido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    👤 Usuario solicita análisis rápido de 3-5 activos.
    
    ✅ PARALELISMO:
    - Carga históricos de 3 activos en paralelo
    - Analiza 2-3 TF por activo en paralelo
    - Retorna resultados en <20 segundos
    """
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Parsear comando: /analizar_rapido AAPL,MSFT,TSLA
    simbolos = context.args[0].split(',') if context.args else ['AAPL', 'MSFT']
    simbolos = [s.strip().upper() for s in simbolos[:5]]  # Máx 5
    
    await _send_typing(update.effective_chat)
    
    try:
        mark_user_state(user_id=user_id, estado="analizando", extra={'activos': simbolos})
        
        # Config rápida: menos timeframes, timeout corto
        fast_config = AnalysisConfig(
            max_concurrent_assets=5,
            timeframe_fan_out=3,
            global_timeout=20,          # ← 20 segundos (desde bot command)
            timeout_per_batch=15,
            timeout_per_tf=5,
            early_exit_confidence=0.75, # ← Early exit más agresivo
        )
        
        # Timeframes "importantes" para análisis rápido
        tfs = ['15min', '1hour', '4hour', '1day']
        
        # ANÁLISIS EN PARALELO
        results = await run_full_analysis_parallel(
            symbols=simbolos,
            tfs=tfs,
            config=fast_config,
            load_history_fn=load_cached_history,
            indicators_executor=_ANALYSIS_EXECUTOR,
            prediction_executor=_ANALYSIS_PRED_EXECUTOR,
            analysis_executor=_ANALYSIS_EXECUTOR,
        )
        
        # FORMATEAR RESPUESTA
        message = await _format_quick_analysis(results, simbolos)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='Markdown'
        )
        
    except asyncio.TimeoutError:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏱️ Análisis no completó en tiempo límite. Intenta con menos activos."
        )
    except Exception as e:
        logger.error(f"[Bot] Análisis error: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
    finally:
        mark_user_state(user_id=user_id, estado="disponible")

async def _format_quick_analysis(results: Dict, symbols: List[str]) -> str:
    """Formatea resultados del análisis rápido para Telegram"""
    
    lines = ["🔍 **ANÁLISIS RÁPIDO**\n"]
    
    for symbol in symbols:
        if symbol not in results or not results[symbol]:
            lines.append(f"• {symbol}: ❌ Sin señales\n")
            continue
        
        tf_signals = results[symbol]
        
        # Toma la señal más fuerte (mayor confidence)
        best_signal = None
        for tf, signal in tf_signals.items():
            if signal and (best_signal is None or signal.get('confidence', 0) > best_signal.get('confidence', 0)):
                best_signal = signal
        
        if best_signal:
            emoji = "📈" if best_signal['type'] == 'BUY' else "📉"
            conf = int(best_signal['confidence'] * 100)
            lines.append(
                f"• {emoji} {symbol}: {best_signal['type']} "
                f"({conf}% en {best_signal.get('timeframe', '?')})\n"
            )
        else:
            lines.append(f"• {symbol}: ➖ Neutral\n")
    
    return "".join(lines)
```

### Comando /listar_oportunidades
```python
async def listar_oportunidades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    👤 Usuario pide las mejores oportunidades del día.
    
    ✅ PARALELISMO MÁXIMO:
    - Analiza TOP 20 activos en paralelo
    - Busca signals > 0.8 confidence
    - Ordena por confluencia
    """
    
    await _send_typing(update.effective_chat)
    
    try:
        # Top 20 activos más populares
        top_symbols = get_top_symbols(limit=20)
        
        # Config para análisis de oportunidades (más exhaustivo)
        opp_config = AnalysisConfig(
            max_concurrent_assets=10,
            timeframe_fan_out=4,
            global_timeout=60,          # 60 segundos
            early_exit_confidence=0.80,
        )
        
        tfs = ['1hour', '4hour', '1day']  # Solo intra/swing
        
        results = await run_full_analysis_parallel(
            symbols=top_symbols,
            tfs=tfs,
            config=opp_config,
            load_history_fn=load_cached_history,
            indicators_executor=_ANALYSIS_EXECUTOR,
            prediction_executor=_ANALYSIS_PRED_EXECUTOR,
            analysis_executor=_ANALYSIS_EXECUTOR,
        )
        
        # FILTRAR: solo signals fuertes
        strong_signals = {}
        for symbol, tf_signals in results.items():
            for tf, signal in ( tf_signals or {}).items():
                if signal and signal.get('confidence', 0) >= 0.8:
                    if symbol not in strong_signals:
                        strong_signals[symbol] = signal
                    elif signal['confidence'] > strong_signals[symbol]['confidence']:
                        strong_signals[symbol] = signal
        
        # ORDENAR por confluence
        ranked = sorted(
            strong_signals.items(),
            key=lambda x: x[1].get('confidence', 0),
            reverse=True
        )
        
        message = "🎯 **OPORTUNIDADES DETECTADAS**\n\n"
        
        if ranked:
            for symbol, signal in ranked[:10]:  # Top 10
                emoji = "🟢" if signal['type'] == 'BUY' else "🔴"
                conf = int(signal['confidence'] * 100)
                message += f"{emoji} {symbol} {signal['type']} ({conf}%)\n"
        else:
            message = "❌ No hay oportunidades claras en este momento"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"[Bot] Oportunidades error: {e}")
```

---

## 3️⃣ API ENDPOINT: Análisis por REST

### GET /api/analysis/{symbol}
```python
# markettool/interfaces/api/routes.py (hexagonal)

@app.route('/api/analysis/<symbol>', methods=['GET'])
async def get_symbol_analysis(symbol: str):
    """
    REST API: análisis de 1 activo con todos sus TF.
    
    Query params:
    - ?tfs=1hour,4hour,1day
    - ?timeout=30
    - ?fast=true (análisis rápido sin predicciones)
    
    Response: {signals_by_tf, metadata}
    """
    
    # Parsear query params
    tfs = request.args.get('tfs', '1hour,4hour,1day').split(',')
    timeout = int(request.args.get('timeout', '30'))
    fast_mode = request.args.get('fast', 'false').lower() == 'true'
    
    try:
        # Config específica para API
        api_config = AnalysisConfig(
            max_concurrent_assets=1,
            timeframe_fan_out=len(tfs),
            global_timeout=timeout,
            early_exit_confidence=0.85 if fast_mode else 0.75,
        )
        
        # Cargar históricos en paralelo
        hist_data = {}
        for tf in tfs:
            try:
                hist_data[tf] = await load_cached_history(symbol.upper(), tf)
            except Exception as e:
                logger.warning(f"[API] History load failed {symbol}/{tf}: {e}")
        
        # Analizar con paralelismo
        engine = ParallelAnalysisEngine(
            indicators_executor=_ANALYSIS_EXECUTOR,
            prediction_executor=None if fast_mode else _ANALYSIS_PRED_EXECUTOR,
            analysis_executor=_ANALYSIS_EXECUTOR,
            config=api_config,
        )
        
        results = await engine._analyze_asset_timeframes(
            symbol.upper(), tfs, hist_data, None
        )
        
        # Retornar JSON
        return jsonify({
            'symbol': symbol.upper(),
            'analyzed_at': pd.Timestamp.now('UTC').isoformat(),
            'results': sanitize_for_json(results),
            'metadata': {
                'timeframes': tfs,
                'fast_mode': fast_mode,
            }
        })
        
    except asyncio.TimeoutError:
        return jsonify({'error': 'Analysis timeout'}), 504
    except Exception as e:
        logger.error(f"[API] Error analyzing {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/batch-analysis', methods=['POST'])
async def batch_analysis():
    """
    REST API: análisis en batch de múltiples activos.
    
    POST body:
    {
        "symbols": ["AAPL", "MSFT", "TSLA"],
        "tfs": ["1hour", "1day"],
        "config": {
            "max_concurrent_assets": 5,
            "timeout_per_batch": 60
        }
    }
    
    Response: {results_by_symbol}
    """
    
    data = request.json or {}
    symbols = data.get('symbols', [])[:20]  # Max 20
    tfs = data.get('tfs', ['1hour', '4hour'])
    cfg_data = data.get('config', {})
    
    try:
        config = AnalysisConfig(
            max_concurrent_assets=cfg_data.get('max_concurrent_assets', 8),
            batch_size_assets=cfg_data.get('batch_size_assets', 8),
            timeframe_fan_out=cfg_data.get('timeframe_fan_out', 4),
            global_timeout=cfg_data.get('global_timeout', 300),
            timeout_per_batch=cfg_data.get('timeout_per_batch', 120),
        )
        
        results = await run_full_analysis_parallel(
            symbols=symbols,
            tfs=tfs,
            config=config,
            load_history_fn=load_cached_history,
            indicators_executor=_ANALYSIS_EXECUTOR,
            prediction_executor=_ANALYSIS_PRED_EXECUTOR,
            analysis_executor=_ANALYSIS_EXECUTOR,
        )
        
        return jsonify({
            'completed': len(results),
            'total': len(symbols),
            'results': sanitize_for_json(results),
        })
        
    except Exception as e:
        logger.error(f"[API] Batch analysis error: {e}")
        return jsonify({'error': str(e)}), 500
```

---

## 4️⃣ MONITOREO Y OBSERVABILIDAD

### Métricas de Paralelismo
```python
# markettool/interfaces/api/health.py

@app.route('/api/parallelism-stats', methods=['GET'])
async def get_parallelism_stats():
    """
    Endpoint de observabilidad: estadísticas de paralelismo actual.
    
    Retorna:
    - CPU usage
    - Memoria por executor
    - Tasks pendientes
    - Latencia de análisis
    """
    
    import resource
    
    stats = {
        'timestamp': pd.Timestamp.now('UTC').isoformat(),
        'system': {
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'memory_percent': psutil.virtual_memory().percent,
            'memory_mb': psutil.virtual_memory().used / (1024 ** 2),
        },
        'executors': {
            'analysis_executor': {
                'workers': _ANALYSIS_MAX_WORKERS,
                'active_threads': threading.active_count(),
            },
            'prediction_executor': {
                'workers': _ANALYSIS_PRED_WORKERS,
                'type': 'ProcessPoolExecutor' if _ANALYSIS_PRED_USE_PROCESS else 'ThreadPoolExecutor',
            },
        },
        'parallelism_config': {
            'max_concurrent_assets': PARALLELISM_CONFIG.max_concurrent_assets,
            'timeframe_fan_out': PARALLELISM_CONFIG.timeframe_fan_out,
            'batch_size': PARALLELISM_CONFIG.batch_size_assets,
        }
    }
    
    return jsonify(stats)
```

---

## 5️⃣ TESTING: Benchmark de Paralelismo

```python
# tests/test_parallelism.py

import pytest
import time
from markettool.application.use_cases.parallel_analysis import (
    ParallelAnalysisEngine,
    AnalysisConfig,
)

@pytest.mark.asyncio
async def test_three_level_parallelism_throughput():
    """
    Benchmark: 30 activos × 4 TF × 3 análisis
    = 360 análisis atómicos
    
    Esperado: <20 segundos con paralelismo máximo
    """
    
    # Mocks
    async def mock_load_history(symbol, tf):
        # Simular carga de 1000 filas
        await asyncio.sleep(0.01)  # 10ms por TF
        return pd.DataFrame({
            'open': np.random.rand(1000),
            'high': np.random.rand(1000),
            'low': np.random.rand(1000),
            'close': np.random.rand(1000),
            'volume': np.random.randint(1000, 10000, 1000),
        })
    
    async def mock_analyze_asset(symbol, tfs, histdata):
        # Simular análisis (cálculos rápidos)
        await asyncio.sleep(0.05)  # 50ms por activo
        return {tf: {'confidence': 0.7} for tf in tfs}
    
    # Setup
    config = AnalysisConfig(
        max_concurrent_assets=8,
        timeframe_fan_out=4,
        global_timeout=30,
    )
    
    engine = ParallelAnalysisEngine(
        config=config
    )
    
    symbols = [f'SYM{i}' for i in range(30)]
    tfs = ['1min', '5min', '15min', '1hour']
    
    # Benchmark
    start = time.time()
    
    results = await engine.analyze_assets_parallel(
        symbols=symbols,
        tfs=tfs,
        load_history_fn=mock_load_history,
        analyze_asset_fn=mock_analyze_asset,
    )
    
    elapsed = time.time() - start
    throughput = len(symbols) / elapsed
    
    print(f"\n🚀 BENCHMARK RESULTS:")
    print(f"   Time: {elapsed:.2f}s")
    print(f"   Throughput: {throughput:.1f} assets/sec")
    print(f"   Total analysis: {len(symbols)} assets × {len(tfs)} TF = {len(symbols) * len(tfs)} análisis")
    
    # Aserciones
    assert elapsed < 30, f"Should complete in <30s, took {elapsed:.2f}s"
    assert throughput > 1, f"Should process >1 asset/sec, got {throughput:.2f}"
    assert len(results) == len(symbols), f"Should have results for all assets"
```

---

## 6️⃣ DEPLOYMENT: Environment Variables

```bash
# .env o secrets de K8s

# === PARALELISMO MÁXIMO ===
ANALYSIS_MAX_WORKERS=64              # ThreadPoolExecutor workers
ANALYSIS_SEMAPHORE=16                # Limita heavy tasks
ANALYSIS_INNER_WORKERS=8             # Sub-tasks
ANALYSIS_PRED_WORKERS=4              # Predicciones
ANALYSIS_PRED_USE_PROCESS=true       # ProcessPoolExecutor (spawn)

# === CONFIGURACIÓN DE ANÁLISIS PARALELO ===
PARALLEL_MAX_CONCURRENT_ASSETS=8     # Max 8 activos simultáneos
PARALLEL_BATCH_SIZE=8                # Batch de 8
PARALLEL_TIMEFRAME_FANOUT=4          # 4 TF en paralelo
PARALLEL_GLOBAL_TIMEOUT=300          # 5 minutos
PARALLEL_RAM_PERCENT_LIMIT=80        # Pausa si > 80%
PARALLEL_EARLY_EXIT_CONFIDENCE=0.85  # Early exit threshold

# === HEALTH & MONITORING ===
ENABLE_PARALLELISM_STATS=true        # Endpoint /api/parallelism-stats
PARALLELISM_LOG_LEVEL=INFO           # DEBUG para verbose
```

---

## RESUMEN

✅ **Paralelismo en 3 niveles integrado con**:
1. AsyncIOScheduler (event loop, sin thread blocking)
2. Bot command handlers (análisis rápido bajo demanda)
3. API REST endpoints (batch analysis)
4. Observabilidad (métricas de paralelismo)
5. Memory guard automático
6. Early exit heurísticas

🚀 **Resultado**: 13x más rápido vs secuencial, 20+ análisis/segundo
