#!/usr/bin/env python
"""
📊 PERFORMANCE MONITORING SCRIPT
=================================
Monitorea mejoras en paralelismo y latencia
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timedelta

# Log file to monitor
LOG_FILE = "logs/app.log"


def parse_gather_time(line):
    """Extrae tiempo de gather() completado"""
    match = re.search(r"gather\(\) completado en (\d+\.?\d*)", line)
    if match:
        return float(match.group(1))
    return None


def parse_promedio(line):
    """Extrae promedio por task"""
    match = re.search(r"promedio: (\d+)ms", line)
    if match:
        return int(match.group(1))
    return None


def parse_paralelismo(line):
    """Extrae paralelismo efectivo"""
    match = re.search(r"paralelismo efectivo: ([\d.]+)x", line)
    if match:
        return float(match.group(1))
    return None


def parse_tareas(line):
    """Extrae número de tareas"""
    match = re.search(r"(\d+) tasks totales", line)
    if match:
        return int(match.group(1))
    return None


def parse_analisis_lento(line):
    """Extrae análisis lento: [Analisis] Lento: XXXX/YY ZZs"""
    match = re.search(r"Lento: (\w+)/(\w+) ([\d.]+)s", line)
    if match:
        symbol, tf, duration = match.groups()
        return (symbol, tf, float(duration))
    return None


def parse_cache_stats(line):
    """Extrae estadísticas de caché"""
    match = re.search(r"\[Cache\] Niveles: (\d+) hits \+ (\d+) misses = ([\d.]+)%", line)
    if match:
        hits, misses, percent = match.groups()
        return (int(hits), int(misses), float(percent))
    return None


def main():
    """Main monitoring function"""
    if not os.path.exists(LOG_FILE):
        print(f"❌ Log file not found: {LOG_FILE}")
        sys.exit(1)
    
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 18 + "📊 PERFORMANCE MONITORING" + " " * 25 + "║")
    print("╚" + "═" * 68 + "╝")
    
    with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    # Últimas 5000 líneas
    recent_lines = lines[-5000:]
    
    # Statistics
    gather_times = []
    promedios = []
    paralelismos = []
    tareas_list = []
    analisis_lentos = []
    cache_stats = []
    
    for line in recent_lines:
        t = parse_gather_time(line)
        if t:
            gather_times.append(t)
        
        p = parse_promedio(line)
        if p:
            promedios.append(p)
        
        par = parse_paralelismo(line)
        if par:
            paralelismos.append(par)
        
        tsk = parse_tareas(line)
        if tsk:
            tareas_list.append(tsk)
        
        slow = parse_analisis_lento(line)
        if slow:
            analisis_lentos.append(slow)
        
        cache = parse_cache_stats(line)
        if cache:
            cache_stats.append(cache)
    
    # Print results
    print("\n📈 GATHER() STATISTICS")
    print("═" * 70)
    if gather_times:
        print(f"Total executions: {len(gather_times)}")
        print(f"Last: {gather_times[-1]:.1f}s")
        print(f"Average: {sum(gather_times)/len(gather_times):.1f}s")
        print(f"Min: {min(gather_times):.1f}s, Max: {max(gather_times):.1f}s")
        
        # Trend
        if len(gather_times) >= 2:
            trend = gather_times[-1] - gather_times[-2]
            icon = "📉" if trend < 0 else "📈"
            print(f"{icon} Trend: {trend:+.1f}s")
    else:
        print("⚠️  No gather() data found")
    
    print("\n⏱️  AVERAGE TIME PER TASK")
    print("═" * 70)
    if promedios:
        print(f"Total executions: {len(promedios)}")
        print(f"Last: {promedios[-1]}ms")
        print(f"Average: {sum(promedios)/len(promedios):.0f}ms")
        print(f"Min: {min(promedios)}ms, Max: {max(promedios)}ms")
        
        # Improvement check
        if len(promedios) >= 2:
            improvement = (promedios[0] - promedios[-1]) / promedios[0] * 100
            if improvement > 5:
                print(f"✅ Improvement: {improvement:.1f}% faster")
            elif improvement < -5:
                print(f"⚠️  Regression: {improvement:.1f}% slower")
    else:
        print("⚠️  No average time data found")
    
    print("\n🔄 EFFECTIVE PARALLELISM")
    print("═" * 70)
    if paralelismos:
        print(f"Total executions: {len(paralelismos)}")
        print(f"Last: {paralelismos[-1]:.2f}x")
        print(f"Average: {sum(paralelismos)/len(paralelismos):.2f}x")
        
        if paralelismos[-1] >= 4:
            print(f"✅ Good parallelism (4+x)")
        elif paralelismos[-1] >= 2:
            print(f"⚠️  Moderate parallelism (2-4x)")
        else:
            print(f"❌ Poor parallelism (< 2x)")
    else:
        print("⚠️  No parallelism data found")
    
    print("\n⚠️  SLOW ANALYSIS (> 50s)")
    print("═" * 70)
    if analisis_lentos:
        slow_only = [a for a in analisis_lentos if a[2] > 50]
        print(f"Total slow: {len(slow_only)} (out of {len(analisis_lentos)})")
        
        if slow_only:
            print(f"Worst: {slow_only[0][0]}/{slow_only[0][1]} ({slow_only[0][2]:.1f}s)")
            print(f"Recent slow analyses:")
            for sym, tf, dur in slow_only[-10:]:
                print(f"  • {sym}/{tf}: {dur:.1f}s")
        else:
            print("✅ No slow analyses found!")
    else:
        print("⚠️  No analysis data found")
    
    print("\n💾 CACHE PERFORMANCE")
    print("═" * 70)
    if cache_stats:
        last_hits, last_misses, last_percent = cache_stats[-1]
        total_hits = sum(h for h, _, _ in cache_stats)
        total_misses = sum(m for _, m, _ in cache_stats)
        overall_percent = total_hits / (total_hits + total_misses) * 100 if (total_hits + total_misses) > 0 else 0
        
        print(f"Last execution: {last_percent:.1f}% hits ({last_hits} hits, {last_misses} misses)")
        print(f"Overall: {overall_percent:.1f}% hits")
        
        if last_percent > 20:
            print(f"✅ Good cache hit rate")
        elif last_percent > 5:
            print(f"⚠️  Low cache hit rate")
        else:
            print(f"❌ Very low cache hit rate")
    else:
        print("⚠️  No cache data found")
    
    print("\n📊 SUMMARY")
    print("═" * 70)
    
    # Check if optimizations are working
    checks = []
    
    if gather_times and gather_times[-1] < 120:
        checks.append("✅ Gather time < 120s")
    elif gather_times and gather_times[-1] < 163:
        checks.append("✅ Gather time improved (< 163s)")
    else:
        checks.append("❌ Gather time not improved")
    
    if promedios and promedios[-1] < 400:
        checks.append("✅ Avg task time < 400ms")
    elif promedios and promedios[-1] < 637:
        checks.append("✅ Avg task time improved (< 637ms)")
    else:
        checks.append("❌ Avg task time not improved")
    
    if paralelismos and paralelismos[-1] >= 3:
        checks.append("✅ Parallelism >= 3x")
    elif paralelismos and paralelismos[-1] >= 1.6:
        checks.append("⚠️  Parallelism improved but < 3x")
    else:
        checks.append("❌ Parallelism not improved")
    
    slow_count = len([a for a in analisis_lentos if a[2] > 50]) if analisis_lentos else 0
    total_count = len(analisis_lentos) if analisis_lentos else 0
    slow_percent = slow_count / total_count * 100 if total_count > 0 else 0
    
    if slow_percent < 10:
        checks.append(f"✅ Few slow analyses ({slow_percent:.1f}%)")
    else:
        checks.append(f"⚠️  Many slow analyses ({slow_percent:.1f}%)")
    
    for check in checks:
        print(check)
    
    print("\n" + "═" * 70)
    print(f"Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
