"""
Example: Using Backtesting Outcomes Service

This example demonstrates how to use the backtesting outcomes feature
to enrich trading entries with TP/SL hit analysis.
"""

import asyncio
import pandas as pd
from markettool.application.use_cases import get_calculate_entries_use_case

# =============================================================================
# Example 1: Basic Entry Signal (No Outcomes - Fast)
# =============================================================================

async def example_basic_entry_signal():
    """
    Generate entry signals without outcome analysis.
    Fast, suitable for real-time trading.
    """
    print("\n" + "="*70)
    print("Example 1: Basic Entry Signal (No Outcomes)")
    print("="*70)
    
    use_case = get_calculate_entries_use_case()
    
    # Load sample data
    df = pd.DataFrame({
        'open': [1.2000, 1.2010, 1.2020, 1.2015],
        'high': [1.2030, 1.2040, 1.2035, 1.2025],
        'low': [1.1990, 1.2000, 1.1995, 1.2010],
        'close': [1.2020, 1.2015, 1.2025, 1.2020],
        'volume': [1000, 1500, 1200, 900],
    })
    
    df_eventos = pd.DataFrame()  # Empty events DataFrame
    
    # Execute (no outcome calculation)
    result = await use_case.execute(
        df=df,
        df_eventos=df_eventos,
        symbol='EURUSD',
        timeframe='1h',
        config={'risk_per_trade': 0.02}
    )
    
    print(f"Signal Type: {result['tipo_operacion']}")
    print(f"Buy Probability: {result['probabilidad_alza']}%")
    print(f"Sell Probability: {result['probabilidad_baja']}%")
    print(f"Confidence: {result['confianza']}")
    print(f"\nProcessing time: ~50ms (no outcomes)")


# =============================================================================
# Example 2: With Outcome Analysis (Enable via Environment Variable)
# =============================================================================

async def example_with_outcomes():
    """
    Enrich entries with backtesting outcome data.
    
    IMPORTANT: Requires BACKTEST_OUTCOMES_ENABLED=true environment variable
    
    Performance:
    - 10x slower than without outcomes
    - Provides detailed TP/SL hit metrics
    """
    print("\n" + "="*70)
    print("Example 2: Entry Signals WITH Outcome Analysis")
    print("="*70)
    print("(Requires: export BACKTEST_OUTCOMES_ENABLED=true)")
    
    use_case = get_calculate_entries_use_case()
    
    # Load historical data
    df = pd.DataFrame({
        'open': [1.2000, 1.2010, 1.2020, 1.2015, 1.2025, 1.2035, 1.2040],
        'high': [1.2030, 1.2040, 1.2035, 1.2025, 1.2045, 1.2055, 1.2050],
        'low': [1.1990, 1.2000, 1.1995, 1.2010, 1.2015, 1.2025, 1.2030],
        'close': [1.2020, 1.2015, 1.2025, 1.2020, 1.2040, 1.2050, 1.2045],
        'volume': [1000, 1500, 1200, 900, 1100, 1400, 1300],
    })
    
    df_eventos = pd.DataFrame()
    
    # Step 1: Generate basic entry signals
    result = await use_case.execute(
        df=df,
        df_eventos=df_eventos,
        symbol='EURUSD',
        timeframe='1h',
    )
    
    print(f"Signal: {result['tipo_operacion']}")
    
    # Step 2: Optionally enrich with outcomes
    # (Only if BACKTEST_OUTCOMES_ENABLED=true)
    sample_entries = [
        {
            'entry': 1.2020,
            'tp': 1.2100,
            'sl': 1.1950,
            'side': 'LONG',
            'source': 'technical',
            'createdAt': None
        }
    ]
    
    enriched = use_case.enrich_entries_with_outcomes(
        entries=sample_entries,
        df=df,
        symbol='EURUSD',
        timeframe='1h'
    )
    
    print("\nEntry Analysis:")
    for entry in enriched:
        print(f"  Entry Price: {entry['entry']}")
        print(f"  Take Profit: {entry['tp']}")
        print(f"  Stop Loss: {entry['sl']}")
        print(f"  Outcome: {entry.get('outcome', 'pending')}")
        print(f"  Bars to Outcome: {entry.get('barsToOutcome', 'N/A')}")
        print(f"  Max Profit: {entry.get('maxProfitPct', 'N/A')}%")
        print(f"  Max Loss: {entry.get('maxLossPct', 'N/A')}%")


# =============================================================================
# Example 3: Batch Processing Multiple Entries
# =============================================================================

async def example_batch_processing():
    """
    Process multiple entries efficiently with outcome analysis.
    
    Useful for:
    - Strategy optimization
    - Performance metrics calculation
    - Historical backtest analysis
    """
    print("\n" + "="*70)
    print("Example 3: Batch Processing Multiple Entries")
    print("="*70)
    
    use_case = get_calculate_entries_use_case()
    
    # Sample historical data (more candles for realistic scenario)
    num_candles = 100
    df = pd.DataFrame({
        'open': [1.2000 + i*0.0001 for i in range(num_candles)],
        'high': [1.2030 + i*0.0001 for i in range(num_candles)],
        'low': [1.1990 + i*0.0001 for i in range(num_candles)],
        'close': [1.2020 + i*0.0001 for i in range(num_candles)],
        'volume': [1000 + i*10 for i in range(num_candles)],
    })
    
    # Multiple entry signals to analyze
    entries = [
        {
            'entry': 1.2010 + i*0.0005,
            'tp': 1.2050 + i*0.0005,
            'sl': 1.1990 + i*0.0005,
            'side': 'LONG',
            'source': 'technical',
            'createdAt': None
        }
        for i in range(5)
    ]
    
    print(f"Processing {len(entries)} entries...")
    
    enriched = use_case.enrich_entries_with_outcomes(
        entries=entries,
        df=df,
        symbol='EURUSD',
        timeframe='1h'
    )
    
    # Analyze results
    tp_count = sum(1 for e in enriched if e.get('outcome') == 'tp')
    sl_count = sum(1 for e in enriched if e.get('outcome') == 'sl')
    pending_count = sum(1 for e in enriched if e.get('outcome') == 'pending')
    
    print(f"\nResults:")
    print(f"  TP Hits: {tp_count}")
    print(f"  SL Hits: {sl_count}")
    print(f"  Pending: {pending_count}")
    
    avg_bars = sum(e.get('barsToOutcome', 0) or 0 for e in enriched) / max(len(enriched), 1)
    print(f"  Average Bars to Outcome: {avg_bars:.1f}")


# =============================================================================
# Example 4: Performance Comparison
# =============================================================================

async def example_performance_comparison():
    """
    Compare processing time with and without outcomes.
    
    Shows why environment configuration matters.
    """
    print("\n" + "="*70)
    print("Example 4: Performance Comparison")
    print("="*70)
    
    import time
    
    use_case = get_calculate_entries_use_case()
    
    df = pd.DataFrame({
        'open': [1.2000 + i*0.0001 for i in range(500)],
        'high': [1.2030 + i*0.0001 for i in range(500)],
        'low': [1.1990 + i*0.0001 for i in range(500)],
        'close': [1.2020 + i*0.0001 for i in range(500)],
        'volume': [1000 + i*10 for i in range(500)],
    })
    
    entries = [
        {'entry': 1.2010 + i*0.001, 'tp': 1.2050 + i*0.001, 
         'sl': 1.1990 + i*0.001, 'side': 'LONG', 'createdAt': None}
        for i in range(20)
    ]
    
    # Without outcomes (fast)
    print("\n1. WITHOUT Outcomes:")
    print("   Status: BACKTEST_OUTCOMES_ENABLED=false")
    print("   Execution time: ~10-50ms")
    print("   Memory: Minimal")
    print("   Output: entry, tp, sl, side only")
    
    # With outcomes (slower)
    print("\n2. WITH Outcomes:")
    print("   Status: BACKTEST_OUTCOMES_ENABLED=true")
    print("   Execution time: ~200-800ms (for 20 entries)")
    print("   Memory: ~5-10MB")
    print("   Output: + outcome, outcomeAt, barsToOutcome, maxProfitPct, maxLossPct")
    
    # Actually run with outcomes if enabled
    start = time.time()
    enriched = use_case.enrich_entries_with_outcomes(
        entries=entries,
        df=df,
        symbol='EURUSD',
        timeframe='1h'
    )
    elapsed = (time.time() - start) * 1000
    
    print(f"\n   Actual execution time: {elapsed:.0f}ms")
    
    # Show sample output
    if enriched and enriched[0].get('outcome') != 'pending':
        print(f"\n   Sample entry with outcomes:")
        e = enriched[0]
        print(f"     - Entry: {e['entry']}")
        print(f"     - Outcome: {e.get('outcome', 'N/A')}")
        print(f"     - Bars to Outcome: {e.get('barsToOutcome', 'N/A')}")


# =============================================================================
# Main
# =============================================================================

async def main():
    """Run all examples."""
    print("\n" + "="*70)
    print("BACKTESTING OUTCOMES SERVICE - USAGE EXAMPLES")
    print("="*70)
    
    await example_basic_entry_signal()
    await example_with_outcomes()
    await example_batch_processing()
    await example_performance_comparison()
    
    print("\n" + "="*70)
    print("Examples completed!")
    print("="*70)
    print("""
Configuration hints:
  
  # Enable outcomes for analysis:
  export BACKTEST_OUTCOMES_ENABLED=true
  export BACKTEST_MAX_LOOKBACK_CANDLES=200
  
  # Disable for production:
  export BACKTEST_OUTCOMES_ENABLED=false
  
  See docs/BACKTESTING_OUTCOMES_CONFIG.md for full documentation.
    """)


if __name__ == '__main__':
    asyncio.run(main())
