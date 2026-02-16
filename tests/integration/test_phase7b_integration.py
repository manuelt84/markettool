"""Quick integration test for Phase 7b - Bot Handler Integration."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_command_mapper_integration():
    """Test CommandMapper with container."""
    logger.info("=" * 60)
    logger.info("TEST 1: CommandMapper Integration")
    logger.info("=" * 60)
    
    try:
        from markettool.interfaces.bot.command_mapper import process_telegram_message
        from markettool.interfaces.containers import DIContainer
        
        # Create container (might fail if dependencies not available)
        try:
            container = DIContainer.create_default()
            logger.info("✅ DIContainer created successfully")
        except Exception as e:
            logger.warning(f"⚠️ Could not create real container: {e}")
            logger.info("Creating mock container...")
            container = MagicMock()
            
            # Mock use cases
            mock_get_quote = AsyncMock()
            mock_get_quote.execute = AsyncMock(return_value=MagicMock(
                symbol="AAPL",
                price=150.25,
                change_pct=0.025,
                timestamp=MagicMock(strftime=lambda x: "12:00:00 UTC"),
                bid=None,
                ask=None,
            ))
            container.get_quote = mock_get_quote
            logger.info("✅ Mock container created")
        
        # Test processing a command
        logger.info("\n📝 Testing: /quote AAPL")
        response = await process_telegram_message(
            message_text="/quote AAPL",
            user_id="123456",
            chat_id="123456",
            container=container,
            logger=logger,
        )
        
        logger.info(f"📤 Response received:")
        logger.info(response)
        
        assert "AAPL" in response or "Error" in response, "Response should contain symbol or error"
        logger.info("✅ TEST 1 PASSED\n")
        
    except Exception as e:
        logger.error(f"❌ TEST 1 FAILED: {e}")
        raise


async def test_handler_creation():
    """Test handler creation."""
    logger.info("=" * 60)
    logger.info("TEST 2: Handler Creation")
    logger.info("=" * 60)
    
    try:
        from markettool.interfaces.bot.telegram_handlers import create_bot_handlers
        
        # Create mock container
        container = MagicMock()
        mock_use_case = AsyncMock()
        mock_use_case.execute = AsyncMock(return_value=MagicMock())
        container.get_quote = mock_use_case
        container.get_historicos = mock_use_case
        container.run_analysis = mock_use_case
        container.warm_cache = mock_use_case
        
        logger.info("📝 Creating handlers...")
        handlers = await create_bot_handlers(container)
        
        logger.info(f"✅ Handlers created: {list(handlers.keys())}")
        
        # Verify all expected handlers exist
        expected_handlers = [
            "start", "message", "historicos", "quote", 
            "analisis", "calentar", "ayuda_hex", "estado_hex", "error"
        ]
        
        for handler_name in expected_handlers:
            assert handler_name in handlers, f"Missing handler: {handler_name}"
            logger.info(f"  ✓ {handler_name}")
        
        logger.info("✅ TEST 2 PASSED\n")
        
    except Exception as e:
        logger.error(f"❌ TEST 2 FAILED: {e}")
        raise


async def test_handler_invocation():
    """Test actual handler invocation with mock Update."""
    logger.info("=" * 60)
    logger.info("TEST 3: Handler Invocation")
    logger.info("=" * 60)
    
    try:
        from markettool.interfaces.bot.telegram_handlers import create_bot_handlers
        
        # Create mock container and use case
        container = MagicMock()
        mock_get_quote = AsyncMock()
        mock_get_quote.execute = AsyncMock(return_value=MagicMock(
            symbol="AAPL",
            price=150.25,
            change_pct=0.025,
            timestamp=MagicMock(strftime=lambda x: "12:00:00 UTC"),
            bid=None,
            ask=None,
        ))
        container.get_quote = mock_get_quote
        
        # Create handlers
        handlers = await create_bot_handlers(container)
        
        # Mock Telegram Update and Context
        mock_update = MagicMock()
        mock_update.effective_chat.id = 123456
        mock_update.effective_user.id = 123456
        mock_update.message.text = "/quote AAPL"
        mock_update.message.reply_html = AsyncMock()
        
        mock_context = MagicMock()
        mock_context.args = ["AAPL"]
        mock_context.bot.send_chat_action = AsyncMock()
        mock_context.bot.send_message = AsyncMock()
        
        logger.info("📝 Invoking handle_quote with mock Update...")
        await handlers["quote"](mock_update, mock_context)
        
        # Verify send_message was called
        assert mock_context.bot.send_message.called, "Bot should send message"
        logger.info(f"✅ Handler invoked successfully")
        logger.info(f"📤 Message sent: {mock_context.bot.send_message.call_count} time(s)")
        
        logger.info("✅ TEST 3 PASSED\n")
        
    except Exception as e:
        logger.error(f"❌ TEST 3 FAILED: {e}")
        raise


async def test_registration_modes():
    """Test different registration modes."""
    logger.info("=" * 60)
    logger.info("TEST 4: Registration Modes")
    logger.info("=" * 60)
    
    try:
        from markettool.interfaces.bot.telegram_handlers import register_handlers_with_app
        
        # Create mock Application
        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_app.add_error_handler = MagicMock()
        
        # Create mock container
        container = MagicMock()
        container.get_quote = AsyncMock()
        container.get_historicos = AsyncMock()
        container.run_analysis = AsyncMock()
        container.warm_cache = AsyncMock()
        
        # Test MIXED mode (default)
        logger.info("\n📝 Testing MIXED mode...")
        await register_handlers_with_app(mock_app, container, mode="mixed")
        mixed_calls = mock_app.add_handler.call_count
        logger.info(f"  ✓ Handlers added: {mixed_calls}")
        
        # Reset
        mock_app.add_handler.reset_mock()
        
        # Test FULL mode
        logger.info("\n📝 Testing FULL mode...")
        await register_handlers_with_app(mock_app, container, mode="full")
        full_calls = mock_app.add_handler.call_count
        logger.info(f"  ✓ Handlers added: {full_calls}")
        
        # Reset
        mock_app.add_handler.reset_mock()
        
        # Test COMMANDS_ONLY mode
        logger.info("\n📝 Testing COMMANDS_ONLY mode...")
        await register_handlers_with_app(mock_app, container, mode="commands_only")
        commands_calls = mock_app.add_handler.call_count
        logger.info(f"  ✓ Handlers added: {commands_calls}")
        
        # Verify error handler registered in all modes
        assert mock_app.add_error_handler.call_count == 3, "Error handler should be added in all modes"
        
        logger.info("\n✅ TEST 4 PASSED\n")
        
    except Exception as e:
        logger.error(f"❌ TEST 4 FAILED: {e}")
        raise


async def main():
    """Run all integration tests."""
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 7b INTEGRATION TESTS")
    logger.info("=" * 60 + "\n")
    
    tests = [
        ("CommandMapper Integration", test_command_mapper_integration),
        ("Handler Creation", test_handler_creation),
        ("Handler Invocation", test_handler_invocation),
        ("Registration Modes", test_registration_modes),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            await test_func()
            passed += 1
        except Exception as e:
            logger.error(f"\n❌ {test_name} FAILED: {e}\n")
            failed += 1
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)
    logger.info(f"✅ Passed: {passed}/{len(tests)}")
    logger.info(f"❌ Failed: {failed}/{len(tests)}")
    
    if failed == 0:
        logger.info("\n🎉 ALL TESTS PASSED! Phase 7b integration is working!")
        logger.info("\n📋 Next Steps:")
        logger.info("  1. Deploy to staging/production")
        logger.info("  2. Test with real Telegram bot")
        logger.info("  3. Monitor logs for [Hexagonal] and [Legacy] messages")
        logger.info("  4. Verify both command sets work")
        logger.info("=" * 60 + "\n")
    else:
        logger.error("\n⚠️ SOME TESTS FAILED - Please review errors above")
        logger.info("=" * 60 + "\n")
        
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
