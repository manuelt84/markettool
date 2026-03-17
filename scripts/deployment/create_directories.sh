#!/bin/bash
# Script para crear todas las carpetas necesarias del sistema
# Útil después de limpiar cache o en primera instalación

echo "📁 Creando estructura de carpetas necesarias..."

# Carpetas principales
mkdir -p forex_news
mkdir -p historicos
mkdir -p cache
mkdir -p data
mkdir -p data/historicos
mkdir -p logs
mkdir -p models
mkdir -p models/easyocr

# Carpetas de cache por módulo
mkdir -p cache/indicators
mkdir -p cache/predictions
mkdir -p cache/analysis
mkdir -p cache/quotes
mkdir -p cache/historicos

# Carpetas de GCS local backup
mkdir -p gcs_backup

echo "✅ Estructura de carpetas creada:"
echo ""
tree -L 2 -d . 2>/dev/null || find . -maxdepth 2 -type d | grep -v "^\./\." | sort

echo ""
echo "✅ Sistema listo para operar"
