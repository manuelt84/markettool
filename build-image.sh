#!/bin/bash
# ============================================================
# build-image.sh - Construye markettool:latest
# Uso: bash build-image.sh [--force] [--no-cache] [--skip-rescue]
#   --force       : no pregunta, reconstruye siempre
#   --no-cache    : reconstruye sin usar capas cacheadas
#   --skip-rescue : NO rescata cache desde containers (útil para rebuilds rápidos)
# 
# AUTO-RESCUE: Por defecto, rescata cache desde containers antes de build
# ============================================================

FORCE=false
NO_CACHE=false
SKIP_RESCUE=false

for arg in "$@"; do
  case $arg in
    --force) FORCE=true ;;
    --no-cache) NO_CACHE=true; NO_CACHE_FLAG="--no-cache" ;;
    --skip-rescue) SKIP_RESCUE=true ;;
  esac
done

cd /home/mtoro/projects/markettool

# ============================================================================
# AUTO-RESCUE: Rescatar cache antes de construir (a menos que se skip)
# ============================================================================
if [ "$SKIP_RESCUE" = false ]; then
  echo ""
  echo "📦 AUTO-RESCUE: Rescatando cache desde containers..."
  echo "   (usa --skip-rescue para omitir este paso)"
  echo ""
  
  RESCUE_SCRIPT="/home/mtoro/.openclaw/workspace/scripts/rescue-and-rebuild-markettool.sh"
  BAKE_SCRIPT="/home/mtoro/projects/localnginx_balancer/maquina-a_test/bake-markettool-cache.sh"
  
  # Intentar con rescue script primero, si no existe usar bake directamente
  if [ -f "$RESCUE_SCRIPT" ]; then
    # Ejecutar solo la parte de rescue (sin build)
    cd /home/mtoro/projects/localnginx_balancer/maquina-a_test
    export MT_CACHE_CONTAINERS="${MT_CACHE_CONTAINERS:-app1}"
    bash "$BAKE_SCRIPT" 2>&1 || echo "⚠️  No se pudo rescatar cache, continuando..."
    cd /home/mtoro/projects/markettool
  elif [ -f "$BAKE_SCRIPT" ]; then
    cd /home/mtoro/projects/localnginx_balancer/maquina-a_test
    export MT_CACHE_CONTAINERS="${MT_CACHE_CONTAINERS:-app1}"
    bash "$BAKE_SCRIPT" 2>&1 || echo "⚠️  No se pudo rescatar cache, continuando..."
    cd /home/mtoro/projects/markettool
  else
    echo "⚠️  No se encontró script de rescue, continuando sin rescatar..."
  fi
  
  echo ""
  echo "✅ Cache rescatado (si estaba disponible)"
  echo ""
fi

# Verificar si existe imagen
IMAGE_EXISTS=$(docker images -q markettool:latest 2>/dev/null)

if [ "$FORCE" = false ]; then
  if [ -n "$IMAGE_EXISTS" ]; then
    echo ""
    echo "📦 Ya existe una imagen markettool:latest"
    echo "   ¿Qué deseas hacer?"
    echo "   [1] Rebuild rápido (usa capas cacheadas, solo cambia código)"
    echo "   [2] Rebuild completo (reinstala librerías, más lento ~15min)"
    echo "   [3] Usar imagen existente (no rebuild)"
    echo ""
    # Si no hay TTY (ej: agente), usar opción 1 por defecto
    if [ ! -t 0 ]; then
      echo "   → Sin TTY detectado, usando opción 1 (rebuild rápido) por defecto"
      choice=1
    else
      read -rp "   Opción [1/2/3]: " choice
    fi
    case $choice in
      1) 
        echo "🔨 Rebuild rápido..." 
        docker build -t markettool:latest . 
        ;;
      2) 
        echo "🔨 Rebuild completo (sin caché)..." 
        docker build --no-cache -t markettool:latest . 
        ;;
      3) 
        echo "✅ Usando imagen existente" 
        exit 0
        ;;
      *) 
        echo "⚠️  Opción inválida, usando imagen existente" 
        exit 0
        ;;
    esac
  else
    echo "🔨 No existe imagen, construyendo por primera vez..."
    docker build -t markettool:latest .
  fi
else
  echo "🔨 Rebuild forzado..."
  docker build $NO_CACHE_FLAG -t markettool:latest .
fi

echo ""
echo "✅ Imagen construida exitosamente"
echo "   markettool:latest"
echo ""
docker images markettool:latest --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
