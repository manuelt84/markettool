#!/usr/bin/env python3
"""
Helper script para visualizar estrategia de caché.

⚠️ IMPORTANTE: Este script solo MUESTRA la configuración actual.
   Para cambiar la estrategia, edita .env manualmente.

Uso:
    python3 set_cache_strategy.py --show   # Ver estrategia actual
    python3 set_cache_strategy.py --list   # Listar opciones disponibles
    
Para cambiar estrategia, edita .env:
    nano .env  # Busca CACHE_STRATEGY y modifícala
"""

import sys
import os
from pathlib import Path
from typing import Dict, Optional


STRATEGIES: Dict[str, Dict[str, str]] = {
    "redis_gcs": {
        "CACHE_ENABLED": "true",
        "CACHE_STRATEGY": "redis_gcs",
        "REDIS_URL": "redis://localhost:6379",
        "GCS_BUCKET": "market-tool-historical-data",
    },
    "redis_only": {
        "CACHE_ENABLED": "true",
        "CACHE_STRATEGY": "redis_only",
        "REDIS_URL": "redis://localhost:6379",
    },
    "gcs_only": {
        "CACHE_ENABLED": "true",
        "CACHE_STRATEGY": "gcs_only",
        "GCS_BUCKET": "market-tool-historical-data",
    },
    "memory_only": {
        "CACHE_ENABLED": "true",
        "CACHE_STRATEGY": "memory_only",
    },
    "disabled": {
        "CACHE_ENABLED": "false",
    },
}

DESCRIPTIONS: Dict[str, str] = {
    "redis_gcs": "Producción: Redis (Tier-0) + GCS (Tier-1) + Cálculo (Tier-2)",
    "redis_only": "Desarrollo: Redis (Tier-0) + Cálculo (Tier-1) [sin GCS]",
    "gcs_only": "Sin Redis: GCS (Tier-0) + Cálculo (Tier-1)",
    "memory_only": "Testing: Memory (Tier-0) + Cálculo (Tier-1) [sin persistencia]",
    "disabled": "Sin caché: Cálculo directo (debugging)",
}

ENV_FILE = Path(__file__).parent / ".env"


def read_env_file() -> Dict[str, str]:
    """Leer variables del .env"""
    if not ENV_FILE.exists():
        return {}
    
    env = {}
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
    return env


def write_env_file(env: Dict[str, str], strategy: str):
    """Escribir variables al .env"""
    with open(ENV_FILE, "w") as f:
        f.write(f"# Estrategia: {DESCRIPTIONS[strategy]}\n")
        f.write(f"# Generado automáticamente por set_cache_strategy.py\n\n")
        
        for key, value in env.items():
            f.write(f"{key}={value}\n")


def show_current_strategy():
    """Mostrar estrategia actual"""
    env = read_env_file()
    current_strategy = env.get("CACHE_STRATEGY", "unknown")
    
    print(f"\n📊 Estrategia actual: {current_strategy}")
    print(f"   {DESCRIPTIONS.get(current_strategy, 'No configurada')}\n")
    
    print("   Variables activas:")
    for key, value in sorted(env.items()):
        if key.startswith("CACHE_") or key in ["REDIS_URL", "GCS_BUCKET"]:
            print(f"     {key}={value}")


def list_strategies():
    """Listar todas las estrategias disponibles"""
    print("\n📋 Estrategias disponibles:\n")
    for strategy in STRATEGIES.keys():
        print(f"  ✓ {strategy:15} - {DESCRIPTIONS[strategy]}")
    print()


def set_strategy(strategy: str):
    """Cambiar a una estrategia específica"""
    if strategy not in STRATEGIES:
        print(f"\n❌ Estrategia '{strategy}' desconocida.")
        print(f"\n   Estrategias válidas:")
        for s in STRATEGIES.keys():
            print(f"     - {s}")
        return False
    
    # Leer env actual (para preservar otras variables)
    env = read_env_file()
    
    # Actualizar variables de estrategia
    new_config = STRATEGIES[strategy]
    for key, value in new_config.items():
        env[key] = value
    
    # Remover variables que no aplican
    for config_strategy, config_vars in STRATEGIES.items():
        if config_strategy != strategy:
            for key in config_vars.keys():
                if key not in new_config:
                    env.pop(key, None)
    
    # Escribir archivo
    write_env_file(env, strategy)
    
    print(f"\n✅ Estrategia cambiada a: {strategy}")
    print(f"   {DESCRIPTIONS[strategy]}\n")
    print(f"   Variables guardadas en: {ENV_FILE}\n")
    
    return True


def main():
    """Main entry point"""
    if len(sys.argv) == 1:
        show_current_strategy()
        return
    
    command = sys.argv[1]
    
    if command == "--list":
        list_strategies()
    elif command == "--show":
        show_current_strategy()
    elif command == "--help" or command == "-h":
        print(__doc__)
    else:
        print(f"\n❌ Para cambiar estrategia, edita .env manualmente:")
        print(f"   nano .env")
        print(f"   # Busca CACHE_STRATEGY y cámbiala a: {command}\n")
        print(f"📋 Estrategias disponibles: redis_gcs, redis_only, gcs_only, memory_only, disabled\n")


if __name__ == "__main__":
    main()
