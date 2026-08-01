"""
Tests para Async MTF Processor - Opción C

Verifica:
1. Procesamiento asíncrono de jobs MTF
2. Cálculo correcto de boost por alineación
3. Guardado en Redis con TTL adecuado
4. Manejo de errores y fallbacks
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from markettool.services.async_mtf_processor import (
    AsyncMTFProcessor,
    get_async_mtf_processor,
    start_async_mtf_processor,
    stop_async_mtf_processor,
)


class TestAsyncMTFProcessor:
    """Tests unitarios para AsyncMTFProcessor"""

    @pytest.fixture
    def mock_redis(self):
        """Mock de Redis client"""
        redis_mock = Mock()
        redis_mock.setex = Mock()
        return redis_mock

    @pytest.fixture
    def processor(self, mock_redis):
        """Instancia del processor con Redis mockeado"""
        proc = AsyncMTFProcessor(redis_client=mock_redis)
        # Mockear strategy activation service
        proc.service = Mock()
        proc.service.get_strategy_config = Mock(return_value={
            'backtest': {
                'requires_mtf_alignment': True
            }
        })
        proc.service.calculate_mtf_alignment = Mock(return_value=(2, {'htf1': 'bullish', 'htf2': 'bullish'}))
        proc.service.calculate_mtf_alignment_boost = Mock(return_value=0.10)
        return proc

    @pytest.mark.asyncio
    async def test_submit_job_agrega_a_cola(self, processor):
        """Debería agregar job a la cola de procesamiento"""
        await processor.submit_job(
            symbol='BTCUSDT',
            timeframe='15m',
            entries=[{'source': 'ob', 'side': 'long', 'confidence': 75}],
            multi_tf_context={'enabled': True},
            cache_key='test:key:123'
        )
        
        assert processor.processing_queue.qsize() == 1
        
        job = await processor.processing_queue.get()
        assert job['symbol'] == 'BTCUSDT'
        assert job['timeframe'] == '15m'
        assert len(job['entries']) == 1
        assert job['cache_key'] == 'test:key:123'

    @pytest.mark.asyncio
    async def test_process_job_procesa_entradas_con_mtf(self, processor, mock_redis):
        """Debería procesar entradas aplicando MTF alignment boost"""
        job = {
            'symbol': 'BTCUSDT',
            'timeframe': '15m',
            'entries': [
                {'source': 'SOURCE_OB', 'side': 'long', 'confidence': 75},
                {'source': 'SOURCE_SMC', 'side': 'short', 'confidence': 60},
            ],
            'multi_tf_context': {
                'enabled': True,
                'higher_tf_1': {'closedCandles': []},
                'higher_tf_2': None,
                'staleness_min': 10,
            },
            'cache_key': 'test:key:456'
        }
        
        await processor._process_job(job)
        
        # Verificar que se llamó a calculate_mtf_alignment para cada entrada
        assert processor.service.calculate_mtf_alignment.called
        assert processor.service.calculate_mtf_alignment_boost.called
        
        # Verificar que se guardó en Redis
        assert mock_redis.setex.called
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == 'test:key:456'  # cache_key
        assert call_args[0][1] == 3600  # TTL 1 hora
        
        # Verificar contenido guardado
        saved_data = json.loads(call_args[0][2])
        assert 'entries' in saved_data
        assert 'mtf_metadata' in saved_data
        assert saved_data['mtf_metadata']['status'] == 'completed'
        assert 'processed_count' in saved_data['mtf_metadata']

    @pytest.mark.asyncio
    async def test_process_job_aplica_boost_correcto(self, processor):
        """Debería aplicar boost de 10% para aligned_count=2"""
        job = {
            'symbol': 'ETHUSDT',
            'timeframe': '15m',
            'entries': [{'source': 'SOURCE_OB', 'side': 'long', 'confidence': 70}],
            'multi_tf_context': {'enabled': True},
            'cache_key': 'test:key:boost'
        }
        
        # Configurar mock para retornar aligned_count=2 (boost=10%)
        processor.service.calculate_mtf_alignment = Mock(return_value=(2, {}))
        processor.service.calculate_mtf_alignment_boost = Mock(return_value=0.10)
        
        await processor._process_job(job)
        
        # Verificar que boost se aplicó: 70 * 1.10 = 77
        saved_data = json.loads(processor.redis.setex.call_args[0][2])
        entry = saved_data['entries'][0]
        assert entry['confidence'] == 77.0
        assert 'mtf_alignment' in entry
        assert entry['mtf_alignment']['boost_applied'] == 0.10

    @pytest.mark.asyncio
    async def test_process_job_skip_estrategias_sin_requires_mtf(self, processor):
        """Debería saltar estrategias que no requieren MTF alignment"""
        processor.service.get_strategy_config = Mock(return_value=None)
        
        job = {
            'symbol': 'BTCUSDT',
            'timeframe': '15m',
            'entries': [{'source': 'SOURCE_TECH', 'side': 'long', 'confidence': 65}],
            'multi_tf_context': {'enabled': True},
            'cache_key': 'test:key:skip'
        }
        
        await processor._process_job(job)
        
        # No debería llamar a calculate_mtf_alignment
        processor.service.calculate_mtf_alignment.assert_not_called()
        
        # Debería guardar entradas sin modificar
        saved_data = json.loads(processor.redis.setex.call_args[0][2])
        assert saved_data['mtf_metadata']['processed_count'] == 0

    @pytest.mark.asyncio
    async def test_process_job_maneja_errores_graciosamente(self, processor, mock_redis):
        """Debería manejar errores y guardar estado failed en Redis"""
        processor.service.calculate_mtf_alignment = Mock(side_effect=Exception("Test error"))
        
        job = {
            'symbol': 'BTCUSDT',
            'timeframe': '15m',
            'entries': [{'source': 'SOURCE_OB', 'side': 'long', 'confidence': 75}],
            'multi_tf_context': {'enabled': True},
            'cache_key': 'test:key:error'
        }
        
        await processor._process_job(job)
        
        # Debería guardar error en Redis
        assert mock_redis.setex.called
        # Buscar llamada para key de error
        error_calls = [c for c in mock_redis.setex.call_args_list if ':error' in str(c)]
        assert len(error_calls) > 0
        
        error_data = json.loads(error_calls[0][0][2])
        assert error_data['status'] == 'failed'
        assert 'Test error' in error_data['error']

    @pytest.mark.asyncio
    async def test_process_loop_procesa_jobs_continuamente(self, processor):
        """Debería procesar jobs de la cola continuamente"""
        # Agregar 2 jobs
        await processor.submit_job('BTC', '15m', [], {}, 'key1')
        await processor.submit_job('ETH', '15m', [], {}, 'key2')
        
        # Mockear _process_job para trackear llamadas
        processor._process_job = AsyncMock()
        
        # Iniciar loop por tiempo limitado
        processor._running = True
        try:
            await asyncio.wait_for(processor._process_loop(), timeout=1.0)
        except asyncio.TimeoutError:
            pass  # Expected timeout
        
        # Debería haber procesado ambos jobs
        assert processor._process_job.call_count >= 2

    @pytest.mark.asyncio
    async def test_start_stop_worker(self, processor):
        """Debería iniciar y detener worker correctamente"""
        assert processor._running is False
        
        await processor.start()
        assert processor._running is True
        
        await processor.stop()
        assert processor._running is False


class TestMTFAlignmentBoost:
    """Tests para cálculo de boost por alineación MTF"""

    @pytest.fixture
    def processor(self):
        """Processor con servicio real"""
        return AsyncMTFProcessor()

    def test_boost_para_aligned_3tf_deberia_ser_20porciento(self, processor):
        """Boost para 3 TFs alineados debería ser +20%"""
        from markettool.application.services.strategy_activation_service import ActivationMode
        
        # Simular config de Confluence
        config = {
            'backtest': {
                'mtf_alignment_boost': {
                    'aligned_3TF': 0.20,
                    'aligned_2TF': 0.10,
                    'aligned_1TF': -0.15,
                }
            }
        }
        
        processor.service.get_strategy_config = Mock(return_value=config)
        processor.service.calculate_mtf_alignment = Mock(return_value=(3, {}))
        processor.service.calculate_mtf_alignment_boost = Mock(
            side_effect=lambda src, count, mode: config['backtest']['mtf_alignment_boost'][f'aligned_{count}TF']
        )
        
        boost = processor.service.calculate_mtf_alignment_boost('confluence', 3, ActivationMode.BACKTEST)
        assert boost == 0.20

    def test_boost_para_aligned_1tf_deberia_ser_negativo(self, processor):
        """Boost para 1 TF alineado (HTFs opuestos) debería ser -15%"""
        config = {
            'backtest': {
                'mtf_alignment_boost': {
                    'aligned_3TF': 0.20,
                    'aligned_2TF': 0.10,
                    'aligned_1TF': -0.15,
                }
            }
        }
        
        processor.service.get_strategy_config = Mock(return_value=config)
        processor.service.calculate_mtf_alignment_boost = Mock(
            side_effect=lambda src, count, mode: config['backtest']['mtf_alignment_boost'][f'aligned_{count}TF']
        )
        
        boost = processor.service.calculate_mtf_alignment_boost('confluence', 1, ActivationMode.BACKTEST)
        assert boost == -0.15


class TestSingletonPattern:
    """Tests para singleton del processor global"""

    def teardown_method(self):
        """Resetear singleton después de cada test"""
        import markettool.services.async_mtf_processor as module
        module._async_mtf_processor = None
        module._worker_started = False

    def test_get_async_mtf_processor_retorna_singleton(self):
        """Debería retornar misma instancia en múltiples llamadas"""
        proc1 = get_async_mtf_processor()
        proc2 = get_async_mtf_processor()
        
        assert proc1 is proc2

    def test_get_async_mtf_processor_crea_instancia_si_no_existe(self):
        """Debería crear instancia si es primera llamada"""
        import markettool.services.async_mtf_processor as module
        module._async_mtf_processor = None
        
        proc = get_async_mtf_processor()
        assert proc is not None
        assert isinstance(proc, AsyncMTFProcessor)
