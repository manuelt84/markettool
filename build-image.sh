#!/bin/bash
# ============================================================
# build-image.sh - Construye markettool:latest
# Uso: bash build-image.sh [--force] [--no-cache]
#   --force     : no pregunta, reconstruye siempre
#   --no-cache  : reconstruye sin usar capas cacheadas (instala librerías desde cero)
# ============================================================

FORCE=false
NO_CACHE=false

for arg in "$@"; do
  case $arg in
    --force) FORCE=true ;;
    --no-cache) NO_CACHE=false; NO_CACHE_FLAG="--no-cache" ;;
  esac
done

cd /home/mtoro/projects/markettool

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
      1) echo "🔨 Rebuild rápido..."; docker build -t markettool:latest . ;;
      2) echo "🔨 Rebuild completo (sin caché)..."; docker build --no-cache -t markettool:latest . ;;
      3) echo "✅ Usando imagen existente" ;;
      *) echo "⚠️  Opción inválida, usando imagen existente" ;;
    esac
  else
    echo "🔨 No existe imagen, construyendo por primera vez..."
    docker build -t markettool:latest .
  fi
else
  echo "🔨 Rebuild forzado..."
  docker build $NO_CACHE_FLAG -t markettool:latest .
fi
