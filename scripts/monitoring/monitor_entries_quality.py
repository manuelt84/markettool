#!/usr/bin/env python3
"""
Monitor entry points quality and parallelism.

Usage:
    python scripts/monitor_entries_quality.py --logfile logs/app.log
    python scripts/monitor_entries_quality.py --follow         # Tail en tiempo real
"""

import argparse
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class EntryQualityMonitor:
    """Monitor entry points generation and quality metrics."""
    
    def __init__(self, logfile: Path):
        self.logfile = logfile
        self.entries_data: List[Dict] = []
        self.stats = {
            'total_assets': 0,
            'total_entries': 0,
            'avg_entries_per_asset': 0,
            'rrr_values': [],
            'score_values': [],
            'strategies': defaultdict(int),
            'low_quality_count': 0,  # RRR < 2.0
            'high_quality_count': 0,  # RRR >= 2.0
        }
        
    def parse_log(self, follow: bool = False):
        """Parse entry generation events from log file."""
        try:
            with open(self.logfile, 'r') as f:
                if follow:
                    f.seek(0, 2)  # Ir al final
                
                while True:
                    line = f.readline()
                    if not line:
                        if follow:
                            time.sleep(0.5)
                            continue
                        break
                    
                    self._process_line(line)
                    
        except FileNotFoundError:
            logger.error(f"Log file not found: {self.logfile}")
        except KeyboardInterrupt:
            logger.info("Monitoring stopped")
    
    def _process_line(self, line: str):
        """Process a single log line."""
        
        # Detectar entradas agregadas (formato: + AGREGADA LONG [pullback_S1] entry=... RRR=2.43)
        pattern = r'\+ AGREGADA (\w+) \[([^\]]+)\].*?entry=([\d.]+).*?RRR=([\d.]+).*?score=([-\d.]+)'
        match = re.search(pattern, line)
        if match:
            side, strategy, entry_price, rrr, score = match.groups()
            rrr = float(rrr)
            score = float(score)
            
            self.entries_data.append({
                'timestamp': self._extract_timestamp(line),
                'side': side,
                'strategy': strategy,
                'entry': float(entry_price),
                'rrr': rrr,
                'score': score,
            })
            
            self.stats['total_entries'] += 1
            self.stats['rrr_values'].append(rrr)
            self.stats['score_values'].append(score)
            self.stats['strategies'][strategy] += 1
            
            if rrr >= 2.0:
                self.stats['high_quality_count'] += 1
            else:
                self.stats['low_quality_count'] += 1
        
        # Detectar resumen de intentos (Intentos totales: X)
        if "Intentos totales:" in line:
            match = re.search(r'Intentos totales: (\d+)', line)
            if match:
                self.stats['total_assets'] += 1
    
    def _extract_timestamp(self, line: str) -> datetime:
        """Extract timestamp from log line."""
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        return datetime.now()
    
    def get_stats(self) -> Dict:
        """Calculate entry quality statistics."""
        if not self.entries_data:
            return {'error': 'No entries found'}
        
        rrr_vals = self.stats['rrr_values']
        score_vals = self.stats['score_values']
        
        return {
            'total_assets': self.stats['total_assets'],
            'total_entries': self.stats['total_entries'],
            'avg_per_asset': self.stats['total_entries'] / max(1, self.stats['total_assets']),
            'rrr': {
                'min': min(rrr_vals) if rrr_vals else 0,
                'max': max(rrr_vals) if rrr_vals else 0,
                'avg': sum(rrr_vals) / len(rrr_vals) if rrr_vals else 0,
            },
            'score': {
                'min': min(score_vals) if score_vals else 0,
                'max': max(score_vals) if score_vals else 0,
                'avg': sum(score_vals) / len(score_vals) if score_vals else 0,
            },
            'quality': {
                'high_quality_count': self.stats['high_quality_count'],
                'low_quality_count': self.stats['low_quality_count'],
                'high_quality_pct': (self.stats['high_quality_count'] / self.stats['total_entries'] * 100) if self.stats['total_entries'] else 0,
            },
            'strategies': dict(self.stats['strategies']),
        }
    
    def print_report(self):
        """Print formatted quality report."""
        stats = self.get_stats()
        
        if 'error' in stats:
            print(f"⚠️  {stats['error']}")
            return
        
        print("\n" + "="*70)
        print("🎯 ENTRY QUALITY MONITORING REPORT")
        print("="*70)
        
        print(f"\n📊 VOLUMEN")
        print(f"  • Total assets analizados: {stats['total_assets']}")
        print(f"  • Total entradas generadas: {stats['total_entries']}")
        avg = stats['avg_per_asset']
        print(f"  • Promedio por asset: {avg:.1f} (objetivo: 8-10)")
        if avg > 10:
            print(f"    ⚠️  ALTO - Considerar aumentar ENTRADA_MIN_RRR")
        elif avg < 5:
            print(f"    ⚠️  BAJO - Considerar bajar ENTRADA_MIN_RRR")
        
        rrr = stats['rrr']
        print(f"\n💰 RISK/REWARD RATIO")
        print(f"  • Mínimo: {rrr['min']:.2f}")
        print(f"  • Máximo: {rrr['max']:.2f}")
        print(f"  • Promedio: {rrr['avg']:.2f} (objetivo: >= 2.0)")
        if rrr['avg'] >= 2.0:
            print(f"    ✅ EXCELENTE - Entradas de alta calidad")
        elif rrr['avg'] >= 1.8:
            print(f"    ✅ BUENO - Entradas aceptables")
        else:
            print(f"    ⚠️  HACER CRECER - Aumentar ENTRADA_MIN_RRR")
        
        quality = stats['quality']
        high_pct = quality['high_quality_pct']
        print(f"\n✨ CALIDAD")
        print(f"  • RRR >= 2.0: {quality['high_quality_count']} ({high_pct:.1f}%)")
        print(f"  • RRR < 2.0: {quality['low_quality_count']} ({100-high_pct:.1f}%)")
        if high_pct > 90:
            print(f"    ✅ EXCELENTE - {high_pct:.0f}% de entradas premium")
        elif high_pct > 70:
            print(f"    ✅ BUENO - {high_pct:.0f}% de alta calidad")
        else:
            print(f"    ⚠️  REVISAR - Aumentar ENTRADA_MIN_RRR para mayor selectividad")
        
        scores = stats['score']
        print(f"\n🎲 SCORE (confluencia + RRR)")
        print(f"  • Mejor (más negativo = mejor): {scores['min']:.3f}")
        print(f"  • Peor: {scores['max']:.3f}")
        print(f"  • Promedio: {scores['avg']:.3f}")
        
        if stats['strategies']:
            print(f"\n📋 ESTRATEGIAS GENERADAS")
            for strat, count in sorted(stats['strategies'].items(), key=lambda x: -x[1])[:8]:
                print(f"  • {strat}: {count}")
        
        print("\n" + "="*70 + "\n")
    
    def print_top_entries(self, n: int = 5):
        """Print top N highest quality entries (best RRR)."""
        if not self.entries_data:
            return
        
        sorted_entries = sorted(self.entries_data, key=lambda x: -x['rrr'])[:n]
        
        print(f"\n🏆 TOP {n} MEJORES ENTRADAS (Mayor RRR)")
        print("-" * 70)
        for i, e in enumerate(sorted_entries, 1):
            print(f"{i}. [{e['side'].upper()}] {e['strategy']}")
            print(f"   Entry: {e['entry']:.6f} | RRR: {e['rrr']:.2f} | Score: {e['score']:.3f}")
        print("-" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor entry points quality'
    )
    parser.add_argument(
        '--logfile',
        type=Path,
        default=Path('logs/app.log'),
        help='Path to log file (default: logs/app.log)'
    )
    parser.add_argument(
        '--follow',
        action='store_true',
        help='Follow log file in real-time'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=10,
        help='Interval to print stats when following (seconds, default: 10)'
    )
    parser.add_argument(
        '--top',
        type=int,
        default=5,
        help='Show top N entries by RRR (default: 5)'
    )
    
    args = parser.parse_args()
    
    if not args.logfile.exists():
        print(f"❌ Log file not found: {args.logfile}")
        sys.exit(1)
    
    monitor = EntryQualityMonitor(args.logfile)
    
    try:
        if args.follow:
            print(f"👀 Monitoring: {args.logfile}")
            print("Press Ctrl+C to stop\n")
            
            last_report = time.time()
            while True:
                monitor.parse_log(follow=False)
                
                if time.time() - last_report >= args.interval:
                    monitor.print_report()
                    monitor.print_top_entries(args.top)
                    last_report = time.time()
                
                time.sleep(1)
        else:
            monitor.parse_log(follow=False)
            monitor.print_report()
            monitor.print_top_entries(args.top)
    
    except KeyboardInterrupt:
        print("\n\n✅ Monitoring stopped")
        monitor.print_report()


if __name__ == '__main__':
    main()
