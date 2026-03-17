#!/usr/bin/env python3
"""
Monitor GCP uploads performance and parallelism.

Usage:
    python scripts/monitor_gcp_uploads.py --logfile logs/app.log
    python scripts/monitor_gcp_uploads.py --follow         # Tail en tiempo real
"""

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class GCPUploadMonitor:
    """Monitor GCP upload operations and parallelism metrics."""
    
    def __init__(self, logfile: Path):
        self.logfile = logfile
        self.upload_events: List[Dict] = []
        self.upload_times: Dict[str, float] = {}
        self.concurrent_uploads: List[int] = []
        self.batch_stats: Dict[str, float] = defaultdict(float)
        
    def parse_log(self, follow: bool = False):
        """Parse upload events from log file."""
        patterns = {
            'upload_start': r'\[Upload\].*Uploading.*?(\d+)\s+uploads',
            'upload_complete': r'\[Upload\].*Uploaded in\s+(\d+\.?\d*)\s*ms',
            'batch_start': r'Batch upload start.*?(\d+)\s+items',
            'batch_complete': r'Batch upload complete.*?(\d+\.?\d*)\s*ms',
            'concurrent': r'Concurrent uploads:\s+(\d+)',
            'timeout': r'Upload timeout.*?:\s+(.*?)$',
            'error': r'Upload\s+(?:error|failed).*?:\s+(.*?)$',
        }
        
        try:
            with open(self.logfile, 'r') as f:
                # Buscar posición si es follow
                if follow:
                    f.seek(0, 2)  # Ir al final
                
                while True:
                    line = f.readline()
                    if not line:
                        if follow:
                            time.sleep(0.5)
                            continue
                        break
                    
                    self._process_line(line, patterns)
                    
        except FileNotFoundError:
            logger.error(f"Log file not found: {self.logfile}")
        except KeyboardInterrupt:
            logger.info("Monitoring stopped")
    
    def _process_line(self, line: str, patterns: Dict[str, str]):
        """Process a single log line."""
        timestamp = self._extract_timestamp(line)
        
        # Upload start
        if 'Uploading' in line:
            match = re.search(patterns['upload_start'], line)
            if match:
                count = int(match.group(1))
                self.upload_events.append({
                    'type': 'upload_start',
                    'timestamp': timestamp,
                    'count': count,
                })
        
        # Upload complete
        if 'Uploaded in' in line or 'upload' in line.lower() and 'ms' in line:
            match = re.search(patterns['upload_complete'], line)
            if match:
                duration_ms = float(match.group(1))
                self.upload_events.append({
                    'type': 'upload_complete',
                    'timestamp': timestamp,
                    'duration_ms': duration_ms,
                })
        
        # Batch operations
        if 'batch_upload' in line.lower():
            if 'start' in line.lower():
                match = re.search(patterns['batch_start'], line)
                if match:
                    items = int(match.group(1))
                    self.upload_events.append({
                        'type': 'batch_start',
                        'timestamp': timestamp,
                        'items': items,
                    })
            elif 'complete' in line.lower():
                match = re.search(patterns['batch_complete'], line)
                if match:
                    duration_ms = float(match.group(1))
                    self.upload_events.append({
                        'type': 'batch_complete',
                        'timestamp': timestamp,
                        'duration_ms': duration_ms,
                    })
        
        # Concurrent metrics
        if 'concurrent' in line.lower():
            match = re.search(patterns['concurrent'], line, re.IGNORECASE)
            if match:
                concurrent = int(match.group(1))
                self.concurrent_uploads.append(concurrent)
        
        # Errors and timeouts
        if 'timeout' in line.lower():
            logger.warning(f"Upload timeout detected: {line.strip()}")
            self.batch_stats['timeouts'] += 1
        
        if 'error' in line.lower() or 'failed' in line.lower():
            logger.warning(f"Upload error detected: {line.strip()}")
            self.batch_stats['errors'] += 1
    
    def _extract_timestamp(self, line: str) -> datetime:
        """Extract timestamp from log line."""
        # Assume format like: 2026-02-16 12:34:56.789
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        return datetime.now()
    
    def get_stats(self) -> Dict:
        """Calculate upload statistics."""
        if not self.upload_events:
            return {'error': 'No upload events found'}
        
        total_events = len(self.upload_events)
        upload_starts = sum(1 for e in self.upload_events if e['type'] == 'upload_start')
        upload_completes = sum(1 for e in self.upload_events if e['type'] == 'upload_complete')
        batch_starts = sum(1 for e in self.upload_events if e['type'] == 'batch_start')
        
        durations = [
            e['duration_ms'] 
            for e in self.upload_events 
            if 'duration_ms' in e
        ]
        
        concurrent_stats = {
            'max': max(self.concurrent_uploads) if self.concurrent_uploads else 0,
            'avg': sum(self.concurrent_uploads) / len(self.concurrent_uploads) if self.concurrent_uploads else 0,
            'min': min(self.concurrent_uploads) if self.concurrent_uploads else 0,
        }
        
        return {
            'total_events': total_events,
            'upload_sessions': upload_starts,
            'completed': upload_completes,
            'batch_operations': batch_starts,
            'duration_stats': {
                'min_ms': min(durations) if durations else 0,
                'max_ms': max(durations) if durations else 0,
                'avg_ms': sum(durations) / len(durations) if durations else 0,
                'total_ms': sum(durations) if durations else 0,
            },
            'concurrency': concurrent_stats,
            'errors': int(self.batch_stats.get('errors', 0)),
            'timeouts': int(self.batch_stats.get('timeouts', 0)),
        }
    
    def print_report(self):
        """Print formatted report."""
        stats = self.get_stats()
        
        if 'error' in stats:
            print(f"⚠️  {stats['error']}")
            return
        
        print("\n" + "="*70)
        print("📤 GCP UPLOAD MONITORING REPORT")
        print("="*70)
        
        print(f"\n📊 EVENTOS TOTALES")
        print(f"  • Total eventos: {stats['total_events']}")
        print(f"  • Sesiones de upload: {stats['upload_sessions']}")
        print(f"  • Completadas: {stats['completed']}")
        print(f"  • Operaciones batch: {stats['batch_operations']}")
        
        dur = stats['duration_stats']
        print(f"\n⏱️  DURACIÓN DE UPLOADS")
        print(f"  • Mínimo: {dur['min_ms']:.0f}ms")
        print(f"  • Máximo: {dur['max_ms']:.0f}ms")
        print(f"  • Promedio: {dur['avg_ms']:.0f}ms")
        print(f"  • Total acumulado: {dur['total_ms']:.0f}ms ({dur['total_ms']/1000:.1f}s)")
        
        conc = stats['concurrency']
        print(f"\n🔀 CONCURRENCIA")
        print(f"  • Máximo simultáneo: {conc['max']}")
        print(f"  • Promedio: {conc['avg']:.1f}")
        print(f"  • Mínimo: {conc['min']}")
        
        print(f"\n⚠️  ERRORES")
        print(f"  • Timeouts: {stats['timeouts']}")
        print(f"  • Errores: {stats['errors']}")
        
        # Estimaciones
        if stats['upload_sessions'] > 0:
            avg_per_session = stats['completed'] / stats['upload_sessions']
            print(f"\n📈 MÉTRICAS DERIVADAS")
            print(f"  • Uploads por sesión: {avg_per_session:.1f}")
            print(f"  • Sesiones esperadas en 1h: {3600000 / (dur['total_ms'] / stats['upload_sessions']) if stats['upload_sessions'] else 0:.0f}")
        
        print("\n" + "="*70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor GCP uploads parallelism and performance'
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
        help='Follow log file in real-time (tail -f mode)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='Interval to print stats when following (seconds, default: 5)'
    )
    
    args = parser.parse_args()
    
    if not args.logfile.exists():
        print(f"❌ Log file not found: {args.logfile}")
        print(f"Please ensure the bot is running and logs to: {args.logfile}")
        sys.exit(1)
    
    monitor = GCPUploadMonitor(args.logfile)
    
    try:
        if args.follow:
            print(f"👀 Siguiendo log: {args.logfile}")
            print("Presiona Ctrl+C para salir\n")
            
            last_report = time.time()
            while True:
                monitor.parse_log(follow=False)
                
                if time.time() - last_report >= args.interval:
                    monitor.print_report()
                    last_report = time.time()
                
                time.sleep(1)
        else:
            monitor.parse_log(follow=False)
            monitor.print_report()
    
    except KeyboardInterrupt:
        print("\n\n✅ Monitoreo detenido")
        monitor.print_report()


if __name__ == '__main__':
    main()
