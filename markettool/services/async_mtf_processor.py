"""
Async MTF Processor - Opción C (Post-Procesamiento Asíncrono)

Procesa análisis MTF para backtesting de forma asíncrona:
1. Respuesta inmediata al frontend sin MTF
2. Job en background calcula MTF alignment
3. Actualiza Redis con resultados enriquecidos
4. Notifica al frontend vía WebSocket/SSE (opcional)

Homologa 100% con frontend RN/Web.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import redis
import json

from markettool.application.services.strategy_activation_service import (
    get_strategy_activation_service,
    ActivationMode,
    MultiTFContext,
)

logger = logging.getLogger(__name__)


class AsyncMTFProcessor:
    """Procesador asíncrono de MTF para backtesting"""
    
    def __init__(self, redis_client=None):
        self.redis = redis_client or redis.Redis(host='localhost', port=6379, db=0)
        self.service = get_strategy_activation_service()
        self.processing_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
    
    async def start(self):
        """Iniciar worker de procesamiento asíncrono"""
        self._running = True
        logger.info("[MTF-Async] Worker iniciado")
        asyncio.create_task(self._process_loop())
    
    async def stop(self):
        """Detener worker"""
        self._running = False
        logger.info("[MTF-Async] Worker detenido")
    
    async def submit_job(self, symbol: str, timeframe: str, entries: List[Dict], 
                         multi_tf_context: Dict, cache_key: str):
        """
        Enviar job de procesamiento MTF a la cola.
        
        Args:
            symbol: Symbol name
            timeframe: Primary timeframe
            entries: Lista de entradas sin procesar MTF
            multi_tf_context: Contexto multi-TF con datos de HTF
            cache_key: Clave Redis para guardar resultados
        """
        job = {
            'symbol': symbol,
            'timeframe': timeframe,
            'entries': entries,
            'multi_tf_context': multi_tf_context,
            'cache_key': cache_key,
            'submitted_at': datetime.utcnow().isoformat(),
        }
        await self.processing_queue.put(job)
        logger.debug(
            "[MTF-Async] Job enviado: %s/%s (queue_size=%d)",
            symbol, timeframe, self.processing_queue.qsize()
        )
    
    async def _process_loop(self):
        """Loop principal de procesamiento"""
        while self._running:
            try:
                # Esperar job con timeout
                try:
                    job = await asyncio.wait_for(
                        self.processing_queue.get(),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Procesar job
                await self._process_job(job)
                
                # Marcar como completado
                self.processing_queue.task_done()
                
            except Exception as e:
                logger.error("[MTF-Async] Error en process_loop: %s", e, exc_info=True)
    
    async def _process_job(self, job: Dict[str, Any]):
        """
        Procesar un job de análisis MTF.
        
        Args:
            job: Dict con symbol, timeframe, entries, multi_tf_context, cache_key
        """
        symbol = job['symbol']
        timeframe = job['timeframe']
        entries = job['entries']
        mtf_ctx_dict = job['multi_tf_context']
        cache_key = job['cache_key']
        
        try:
            logger.info(
                "[MTF-Async] Procesando %s/%s (%d entradas)",
                symbol, timeframe, len(entries)
            )
            
            # Construir MultiTFContext
            mtf_ctx = MultiTFContext(
                enabled=mtf_ctx_dict.get('enabled', False),
                higher_tf_1=mtf_ctx_dict.get('higher_tf_1'),
                higher_tf_2=mtf_ctx_dict.get('higher_tf_2'),
                staleness_min=mtf_ctx_dict.get('staleness_min', 0.0),
                inference_available=mtf_ctx_dict.get('inference_available', False),
            )
            
            # Procesar cada entrada
            processed_count = 0
            boosted_count = 0
            
            for entry in entries:
                source_id = entry.get('source', '').replace('SOURCE_', '').lower()
                
                # Verificar si estrategia requiere MTF alignment
                config = self.service.get_strategy_config(source_id)
                if not config or not config.get('backtest', {}).get('requires_mtf_alignment', False):
                    continue
                
                # Deducir dirección primaria desde side de la entrada
                side = entry.get('side', '').lower()
                if 'long' in side or 'buy' in side or 'compra' in side:
                    primary_direction = 'bullish'
                elif 'short' in side or 'sell' in side or 'venta' in side:
                    primary_direction = 'bearish'
                else:
                    continue  # Neutral, skip
                
                # Calcular alineación MTF
                aligned_count, htf_directions = self.service.calculate_mtf_alignment(
                    primary_direction, mtf_ctx
                )
                
                # Calcular boost
                boost = self.service.calculate_mtf_alignment_boost(
                    source_id, aligned_count, ActivationMode.BACKTEST
                )
                
                # Aplicar boost al confidence
                if 'confidence' in entry and boost != 0:
                    old_conf = entry['confidence']
                    new_conf = round(old_conf * (1 + boost), 2)
                    entry['confidence'] = min(100, new_conf)
                    
                    # Metadata de MTF
                    entry['mtf_alignment'] = {
                        'aligned_count': aligned_count,
                        'total_tfs': 1 + (1 if mtf_ctx.higher_tf_1 else 0) + (1 if mtf_ctx.higher_tf_2 else 0),
                        'primary_direction': primary_direction,
                        'htf_directions': htf_directions,
                        'boost_applied': boost,
                        'processed_at': datetime.utcnow().isoformat(),
                    }
                    
                    processed_count += 1
                    if boost > 0:
                        boosted_count += 1
                    
                    logger.debug(
                        "[MTF-Async] %s/%s %s: boost=%+.0f%% (aligned=%d/3)",
                        symbol, timeframe, source_id, boost * 100, aligned_count
                    )
            
            # Guardar resultados en Redis
            enriched_data = {
                'entries': entries,
                'mtf_metadata': {
                    'processed_count': processed_count,
                    'boosted_count': boosted_count,
                    'total_entries': len(entries),
                    'processed_at': datetime.utcnow().isoformat(),
                    'status': 'completed',
                },
            }
            
            # Serializar y guardar en Redis (TTL: 1 hora)
            self.redis.setex(
                cache_key,
                3600,  # 1 hora
                json.dumps(enriched_data)
            )
            
            logger.info(
                "[MTF-Async] %s/%s completado: %d/%d procesadas, %d con boost (+guardado en Redis)",
                symbol, timeframe, processed_count, len(entries), boosted_count
            )
            
            # TODO: Notificar frontend vía WebSocket/SSE cuando esté implementado
            # await self._notify_frontend(symbol, timeframe, enriched_data)
            
        except Exception as e:
            logger.error(
                "[MTF-Async] Error procesando %s/%s: %s",
                symbol, timeframe, e, exc_info=True
            )
            
            # Guardar estado de error en Redis
            error_data = {
                'error': str(e),
                'processed_at': datetime.utcnow().isoformat(),
                'status': 'failed',
            }
            self.redis.setex(cache_key + ':error', 300, json.dumps(error_data))
    
    async def _notify_frontend(self, symbol: str, timeframe: str, data: Dict):
        """
        Notificar al frontend que el procesamiento MTF completó.
        
        TODO: Implementar cuando WebSocket/SSE esté disponible.
        Por ahora es un stub.
        """
        logger.debug(
            "[MTF-Async] Frontend notification (stub): %s/%s",
            symbol, timeframe
        )
        # Futura implementación:
        # - WebSocket: await websocket_manager.broadcast(f'mtf_complete:{symbol}:{timeframe}', data)
        # - SSE: await sse_manager.send(f'mtf_complete', data)


# Singleton instance
_async_mtf_processor: Optional[AsyncMTFProcessor] = None


def get_async_mtf_processor() -> AsyncMTFProcessor:
    """Obtener instancia singleton del procesador"""
    global _async_mtf_processor
    if _async_mtf_processor is None:
        _async_mtf_processor = AsyncMTFProcessor()
    return _async_mtf_processor


async def start_async_mtf_processor():
    """Iniciar el procesador asíncrono global"""
    processor = get_async_mtf_processor()
    await processor.start()


async def stop_async_mtf_processor():
    """Detener el procesador asíncrono global"""
    processor = get_async_mtf_processor()
    await processor.stop()
