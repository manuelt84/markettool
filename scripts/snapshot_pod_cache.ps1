[CmdletBinding()]
param(
    [string]$Namespace = "default",
    [string]$Selector = "app=markettool",
    [string]$Container = "markettool",
    [string]$ProjectRoot = "",
    [switch]$BakeIntoProject,
    [string]$BucketName = "markettool_bucket",
    [switch]$SkipGcsCache,
    [switch]$IncludeExecArtifacts
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
    param([string]$InputPath)
    if ($InputPath -and $InputPath.Trim().Length -gt 0) {
        return (Resolve-Path $InputPath).Path
    }
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$root = Resolve-ProjectRoot -InputPath $ProjectRoot
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$snapshotDir = Join-Path $root (Join-Path "backup\pod-cache" $timestamp)
New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null

$snapshotLocalDir = Join-Path $snapshotDir "local"
$snapshotGcsDir = Join-Path $snapshotDir "gcs"
New-Item -ItemType Directory -Path $snapshotLocalDir -Force | Out-Null
New-Item -ItemType Directory -Path $snapshotGcsDir -Force | Out-Null

Write-Host "[1/6] Resolviendo pod de $Selector en namespace $Namespace..."
$pod = kubectl get pods -n $Namespace -l $Selector -o jsonpath="{.items[0].metadata.name}"
if (-not $pod) {
    throw "No se encontró pod para selector '$Selector' en namespace '$Namespace'."
}

Write-Host "Pod seleccionado: $pod"

$archiveInPod = "/tmp/markettool-cache-$timestamp.tgz"
$archiveLocal = Join-Path $snapshotLocalDir "markettool-cache-$timestamp.tgz"

Write-Host "[2/6] Creando archivo de cache dentro del pod..."
$createCmd = @"
set -e
cd /app
dirs=""
[ -d historicos ] && dirs="$dirs historicos"
[ -d forex_news ] && dirs="$dirs forex_news"
if [ -z "$dirs" ]; then
  echo "NO_CACHE_DIRS"
  exit 0
fi
tar -czf $archiveInPod $dirs
ls -lh $archiveInPod
"@

kubectl exec -n $Namespace $pod -c $Container -- sh -lc $createCmd | Out-Host

Write-Host "[3/6] Verificando si el archivo existe en el pod..."
$exists = kubectl exec -n $Namespace $pod -c $Container -- sh -lc "test -f $archiveInPod && echo YES || echo NO"
if ($exists.Trim() -ne "YES") {
    Write-Warning "No hay carpetas de cache locales en el pod (/app/historicos o /app/forex_news)."
    exit 0
}

Write-Host "[4/6] Copiando archivo al workspace..."
kubectl cp "${Namespace}/${pod}:${archiveInPod}" $archiveLocal -c $Container

Write-Host "[5/6] Limpiando archivo temporal del pod..."
kubectl exec -n $Namespace $pod -c $Container -- sh -lc "rm -f $archiveInPod" | Out-Null

if ($BakeIntoProject) {
    Write-Host "[6/8] Extrayendo cache local al proyecto (para bakear en la próxima imagen)..."
    tar -xzf $archiveLocal -C $root
    Write-Host "Cache extraída en:"
    Write-Host " - $(Join-Path $root "historicos")"
    Write-Host " - $(Join-Path $root "forex_news")"
    Write-Host "Siguiente build incluirá estas carpetas por COPY . . en Dockerfile."
} else {
    Write-Host "[6/8] Snapshot local guardado en: $archiveLocal"
    Write-Host "Para inyectarlo al proyecto y bakear imagen, ejecuta:"
    Write-Host "tar -xzf $archiveLocal -C $root"
}

if (-not $SkipGcsCache) {
    Write-Host "[7/8] Exportando caché persistente desde GCS ($BucketName)..."

    $prefixes = @("historicos", "indicators")
    if ($IncludeExecArtifacts) {
        $prefixes += "analisis/exec"
    }

    foreach ($prefix in $prefixes) {
        $safePrefix = $prefix -replace "[\\/:]", "_"
        $targetDir = Join-Path $snapshotGcsDir $safePrefix
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

        $uri = "gs://$BucketName/$prefix"
        Write-Host " - Copiando $uri -> $targetDir"

        try {
            gsutil -m cp -r "$uri/**" $targetDir 2>$null
        } catch {
            Write-Warning "No se pudo copiar $uri (puede no existir o no tener permisos)."
        }
    }

    Write-Host "[8/8] Snapshot completo guardado en: $snapshotDir"
    Write-Host "Incluye: local(historicos/forex_news) + gcs(historicos/indicators$(if($IncludeExecArtifacts){'/analisis_exec'} else {''}))"
} else {
    Write-Host "[7/8] Omitido backup de GCS por -SkipGcsCache"
    Write-Host "[8/8] Snapshot guardado en: $snapshotDir"
}

Write-Host "Proceso completado."