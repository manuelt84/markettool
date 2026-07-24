"""
Configuración centralizada para estrategia de caché.

Permite elegir qué capas de caché usar via variables de entorno.
"""

import os
from enum import Enum
from typing import List


class CacheBackend(Enum):
    """Opciones de backend de caché"""
    REDIS = "redis"
    GCS = "gcs"
    MEMORY = "memory"
    NONE = "none"


class CacheConfig:
    """
    Configuración centralizada del sistema de caché.
    
    Variables de entorno soportadas:
    - CACHE_STRATEGY: "redis_gcs" (default), "redis_only", "gcs_only", "memory_only"
    - REDIS_URL: URL de conexión a Redis (ej: redis://localhost:6379)
    - GCS_BUCKET: Nombre del bucket de GCS
    - CACHE_ENABLED: "true"/"false" para deshabilitar todo el caché
    
    Ejemplos:
        # Redis + GCS (recomendado para producción)
        export CACHE_STRATEGY="redis_gcs"
        export REDIS_URL="redis://localhost:6379"
        export GCS_BUCKET="my-bucket"
        
        # Solo Redis (desarrollo/testing)
        export CACHE_STRATEGY="redis_only"
        export REDIS_URL="redis://localhost:6379"
        
        # Solo GCS (sin Redis disponible)
        export CACHE_STRATEGY="gcs_only"
        export GCS_BUCKET="my-bucket"
        
        # Sin caché (testing)
        export CACHE_ENABLED="false"
    """
    
    # Estrategia global
    CACHE_ENABLED = str(os.getenv("CACHE_ENABLED", "true")).lower() == "true"
    CACHE_STRATEGY = str(os.getenv("CACHE_STRATEGY", "redis_gcs")).lower()
    
    # Redis
    REDIS_ENABLED = CACHE_ENABLED and "redis" in CACHE_STRATEGY
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # GCS
    _GCS_ENV_ENABLED = str(os.getenv("GCS_ENABLED", "true")).lower() == "true"
    _VPS_BACKEND_ENABLED = str(os.getenv("MARKETTOOL_CLOUD_BACKEND", "")).strip().lower() in {"vps", "postgres", "local", "filesystem", "fs", "vps_gcp", "vps-gcp", "vps_fallback_gcp", "vps-fallback-gcp"}
    GCS_ENABLED = (
        CACHE_ENABLED
        and _GCS_ENV_ENABLED
        and not _VPS_BACKEND_ENABLED
        and ("gcs" in CACHE_STRATEGY or "redis_gcs" in CACHE_STRATEGY)
    )
    GCS_BUCKET = os.getenv("GCS_BUCKET")
    
    # Memory cache (always available as fallback)
    MEMORY_ENABLED = True
    
    @classmethod
    def get_cache_layers(cls) -> List[CacheBackend]:
        """
        Retorna lista de capas de caché a usar en orden de preferencia.
        
        Returns:
            List[CacheBackend]: [Tier-0, Tier-1, Tier-2]
        """
        if not cls.CACHE_ENABLED:
            return [CacheBackend.NONE]
        
        layers = []
        
        # Tier-0: Redis (ultra-fast, volátil)
        if cls.REDIS_ENABLED:
            layers.append(CacheBackend.REDIS)
        
        # Tier-1: GCS (persistencia, confiable)
        if cls.GCS_ENABLED:
            layers.append(CacheBackend.GCS)
        
        # Tier-2: Memory (fallback rápido)
        if cls.MEMORY_ENABLED:
            layers.append(CacheBackend.MEMORY)
        
        # Si no hay ninguna capa, usar NONE
        if not layers:
            layers = [CacheBackend.NONE]
        
        return layers
    
    @classmethod
    def should_use_redis(cls) -> bool:
        """Retorna True si Redis debe ser usado."""
        return cls.REDIS_ENABLED
    
    @classmethod
    def should_use_gcs(cls) -> bool:
        """Retorna True si GCS debe ser usado."""
        return cls.GCS_ENABLED
    
    @classmethod
    def should_cache(cls) -> bool:
        """Retorna True si algún tipo de caché debe ser usado."""
        return cls.CACHE_ENABLED
    
    @classmethod
    def primary_backend(cls) -> CacheBackend:
        """
        Retorna el backend primario (Tier-0) a usar.
        
        Returns:
            CacheBackend: El backend principal (REDIS, GCS, MEMORY, o NONE)
        """
        layers = cls.get_cache_layers()
        return layers[0] if layers else CacheBackend.NONE
    
    @classmethod
    def validate(cls):
        """
        Valida la configuración e imprime warnings si hay problemas.
        """
        issues = []
        
        if not cls.CACHE_ENABLED:
            print("[CacheConfig] ADVERTENCIA: Cache completamente deshabilitado")
            return
        
        if "redis" in cls.CACHE_STRATEGY and not cls.REDIS_URL:
            issues.append("Redis habilitado pero REDIS_URL no configurada")
        
        if "gcs" in cls.CACHE_STRATEGY and not cls.GCS_BUCKET:
            issues.append("GCS habilitado pero GCS_BUCKET no configurada")
        
        if cls.CACHE_STRATEGY not in ["redis_gcs", "redis_only", "gcs_only", "memory_only"]:
            issues.append(f"CACHE_STRATEGY invalida: {cls.CACHE_STRATEGY}")
        
        if issues:
            print("[CacheConfig] ADVERTENCIAS:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            layers_str = ", ".join(x.value for x in cls.get_cache_layers())
            print(f"[CacheConfig] Estrategia: {cls.CACHE_STRATEGY}")
            print(f"[CacheConfig] Capas activas: {layers_str}")
            print(f"[CacheConfig] Backend primario: {cls.primary_backend().value}")


# Validar al importar
CacheConfig.validate()
