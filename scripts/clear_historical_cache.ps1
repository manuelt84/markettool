# Script para limpiar cache de datos históricos con timestamps incorrectos
# Ejecutar DESPUÉS del timezone fix para forzar descarga de datos frescos

Write-Host "⚠️  ADVERTENCIA: Este script eliminará TODO el cache de datos históricos"
Write-Host "Los datos se volverán a descargar desde FMP con los timestamps corregidos"
Write-Host ""

$confirmation = Read-Host "¿Continuar? (escribe 'SI' para confirmar)"

if ($confirmation -ne "SI") {
    Write-Host "❌ Operación cancelada"
    exit 0
}

Write-Host ""
Write-Host "🧹 Limpiando cache en contenedores app1 y app2..."

# Limpiar cache en app1
Write-Host "  - Limpiando app1..."
docker exec app1 bash -c "rm -rf /app/cache/* /app/*.pt /app/data/historicos/*" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    ✅ app1 limpiado"
} else {
    Write-Host "    ⚠️  app1 no disponible o ya limpio"
}

# Limpiar cache en app2
Write-Host "  - Limpiando app2..."
docker exec app2 bash -c "rm -rf /app/cache/* /app/*.pt /app/data/historicos/*" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    ✅ app2 limpiado"
} else {
    Write-Host "    ⚠️  app2 no disponible o ya limpio"
}

Write-Host ""
Write-Host "✅ Cache local limpiado"
Write-Host ""
Write-Host "� Recreando carpetas necesarias..."
Write-Host "  - Creando forex_news..."
docker exec app1 bash -c "mkdir -p forex_news historicos cache data/historicos" 2>$null
docker exec app2 bash -c "mkdir -p forex_news historicos cache data/historicos" 2>$null
Write-Host "    ✅ Carpetas recreadas"
Write-Host ""
Write-Host "�📝 NOTA: Los datos en Google Cloud Storage (GCS) también deben limpiarse"
Write-Host "         Ejecuta desde dentro del contenedor:"
Write-Host "         docker exec app1 python -c 'from google.cloud import storage; ..."
Write-Host ""
Write-Host "🔄 Reiniciando contenedores para aplicar cambios..."
Write-Host ""

cd c:\projects\localNginx_Balancer\maquina-a_test
docker-compose restart app1 app2

Write-Host ""
Write-Host "✅ COMPLETADO"
Write-Host "   Los contenedores descargarán datos frescos con timestamps corregidos"
