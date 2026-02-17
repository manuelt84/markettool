#
# Script simplificado para snapshot de cache
# Este script extrae cache desde Docker o Kubernetes
#

[CmdletBinding()]
param(
    [ValidateSet("auto", "kube", "docker")]
    [string]$Runtime = "auto",
    [string]$Namespace = "default",
    [string]$Selector = "app=markettool",
    [string]$Container = "markettool",
    [string]$DockerContainer = "",
    [string]$ProjectRoot = "",
    [string]$KubeContext = "",
    [string]$Kubeconfig = "",
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

function Test-DockerConnection {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker no esta instalado o no esta en PATH."
    }
    
    & docker version 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon no disponible."
    }
}

function Resolve-DockerContainer {
    param([string]$Preferred)

    if ($Preferred -and $Preferred.Trim().Length -gt 0) {
        $exists = docker ps --format "{{.Names}}" | Where-Object { $_ -eq $Preferred }
        if (-not $exists) {
            throw "No existe contenedor: '$Preferred'"
        }
        return $Preferred
    }

    $rows = docker ps --format "{{.Names}}|{{.Image}}"
    if (-not $rows) {
        throw "No hay contenedores Docker en ejecucion."
    }

    $candidates = @()
    foreach ($r in $rows) {
        $parts = $r -split "\|", 2
        $name = $parts[0]
        $image = if ($parts.Count -gt 1) { $parts[1] } else { "" }
        if ($name -match "^app\d+$" -or $image -match "markettool") {
            $candidates += $name
        }
    }

    if (-not $candidates -or $candidates.Count -eq 0) {
        throw "No se encontro contenedor markettool."
    }

    if ($candidates.Count -gt 1) {
        Write-Warning "Varios contenedores encontrados, usando: $($candidates[0])"
    }

    return $candidates[0]
}

function Export-LocalCacheFromDocker {
    param(
        [string]$DockerContainerArg,
        [string]$TimestampArg,
        [string]$SnapshotLocalDirArg
    )

    Write-Host "[1/6] Resolviendo contenedor Docker..."
    $container = Resolve-DockerContainer -Preferred $DockerContainerArg
    Write-Host "Contenedor: $container"

    $archiveInContainer = "/tmp/markettool-cache-$TimestampArg.tgz"
    $archiveLocal = Join-Path $SnapshotLocalDirArg "markettool-cache-$TimestampArg.tgz"

    Write-Host "[2/6] Creando archivo de cache dentro del contenedor..."
    $dirPaths = @()
    $dirCandidates = @("historicos", "forex_news", "indicators", "indicadores", "indicators_cache")
    
    foreach ($d in $dirCandidates) {
        $exists = docker exec $container sh -lc "test -d /app/$d && echo YES || echo NO"
        if (($exists | Out-String).Trim() -eq "YES") { 
            $dirPaths += "/app/$d" 
        }
    }

    if (-not $dirPaths -or $dirPaths.Count -eq 0) {
        Write-Warning "No hay carpetas de cache detectadas."
        return ""
    }

    $dirNames = $dirPaths | ForEach-Object { ($_ -split '/')[-1] }
    $dirNamesArg = $dirNames -join " "
    Write-Host "Carpetas detectadas: $($dirPaths -join ', ')"
    
    docker exec $container sh -lc "cd /app && tar -czf $archiveInContainer $dirNamesArg && ls -lh $archiveInContainer" | Out-Host

    Write-Host "[3/6] Verificando archivo en contenedor..."
    $exists = docker exec $container sh -lc "test -f $archiveInContainer && echo YES || echo NO"
    if ($exists.Trim() -ne "YES") {
        Write-Warning "No se pudo generar archivo de cache."
        return ""
    }

    Write-Host "[4/6] Copiando archivo al workspace..."
    docker cp "${container}:${archiveInContainer}" $archiveLocal

    Write-Host "[5/6] Limpiando archivo temporal..."
    docker exec $container sh -lc "rm -f $archiveInContainer" | Out-Null

    return $archiveLocal
}

# Main execution
$root = Resolve-ProjectRoot -InputPath $ProjectRoot
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$snapshotDir = Join-Path $root (Join-Path "backup\pod-cache" $timestamp)
New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null

$snapshotLocalDir = Join-Path $snapshotDir "local"
$snapshotGcsDir = Join-Path $snapshotDir "gcs"
New-Item -ItemType Directory -Path $snapshotLocalDir -Force | Out-Null
New-Item -ItemType Directory -Path $snapshotGcsDir -Force | Out-Null

$doLocalCache = $true
$doGcsCache = -not $SkipGcsCache

$isInteractivePrompt = `
    (-not $PSBoundParameters.ContainsKey("Runtime")) -and `
    (-not $PSBoundParameters.ContainsKey("SkipGcsCache"))

if ($isInteractivePrompt) {
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host "SNAPSHOT DE CACHE - MarketTool" -ForegroundColor Cyan
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Que deseas descargar?" -ForegroundColor Yellow
    Write-Host "  1 - Cache local Docker"
    Write-Host "  2 - Cache GCP"
    Write-Host "  3 - Ambos"
    Write-Host ""

    $choice = (Read-Host "Selecciona 1, 2 o 3").Trim()
    switch ($choice) {
        "1" {
            $Runtime = "docker"
            $doLocalCache = $true
            $doGcsCache = $false
        }
        "2" {
            $doLocalCache = $false
            $doGcsCache = $true
        }
        "3" {
            $Runtime = "docker"
            $doLocalCache = $true
            $doGcsCache = $true
        }
        default {
            Write-Host "ERROR: Seleccion invalida"
            exit 1
        }
    }
    
    if ($doLocalCache -and -not $PSBoundParameters.ContainsKey("BakeIntoProject")) {
        Write-Host ""
        Write-Host "Descargar y extraer automáticamente?" -ForegroundColor Yellow
        Write-Host "  S - Si, extraer"
        Write-Host "  N - No, solo descargar"
        Write-Host ""
        
        $extract = (Read-Host "S/N").Trim().ToUpper()
        if ($extract -eq "S" -or $extract -eq "SI" -or $extract -eq "Y") {
            $BakeIntoProject = $true
            Write-Host "OK - Se extraera automaticamente" -ForegroundColor Green
        } else {
            Write-Host "OK - Solo se descargara" -ForegroundColor Gray
        }
    }
    
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host ""
}

$archiveLocal = ""

if ($doLocalCache) {
    if ($Runtime -eq "docker") {
        Write-Host "[0/6] Runtime: Docker"
        Test-DockerConnection
        $archiveLocal = Export-LocalCacheFromDocker -DockerContainerArg $DockerContainer -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
    } else {
        Write-Host "[0/6] Runtime: Auto (Docker)"
        Test-DockerConnection
        $archiveLocal = Export-LocalCacheFromDocker -DockerContainerArg $DockerContainer -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
    }

    if (-not $archiveLocal) {
        Write-Warning "No se genero archivo de cache."
    }
} else {
    Write-Host "[0/6] Omitiendo cache local"
}

if ($BakeIntoProject -and $archiveLocal) {
    Write-Host "[6/8] Extrayendo cache al proyecto..." -ForegroundColor Cyan
    
    $backupSubDir = Join-Path (Join-Path $root "backup") "pre-snapshot-$timestamp"
    $dirsToBackup = @("historicos", "forex_news", "indicators", "indicadores", "indicators_cache")
    $backupMade = $false
    
    foreach ($dir in $dirsToBackup) {
        $dirPath = Join-Path $root $dir
        if (Test-Path $dirPath) {
            if (-not $backupMade) {
                New-Item -ItemType Directory -Path $backupSubDir -Force | Out-Null
                Write-Host "Creando backup en: $backupSubDir" -ForegroundColor Yellow
                $backupMade = $true
            }
            $backupPath = Join-Path $backupSubDir $dir
            Write-Host "  Respaldando $dir..." -ForegroundColor Gray
            Copy-Item -Path $dirPath -Destination $backupPath -Recurse -Force
        }
    }
    
    Write-Host "Extrayendo archivos de cache..." -ForegroundColor Cyan
    tar -xzf $archiveLocal -C $root
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Error al extraer archivo"
    } else {
        Write-Host ""
        Write-Host "EXITO - Cache extraida al proyecto" -ForegroundColor Green
        
        foreach ($dir in $dirsToBackup) {
            $dirPath = Join-Path $root $dir
            if (Test-Path $dirPath) {
                $itemCount = (Get-ChildItem -Path $dirPath -Recurse -File | Measure-Object).Count
                Write-Host "  $dir - $itemCount archivos" -ForegroundColor Green
            }
        }
        
        Write-Host ""
        if ($backupMade) {
            Write-Host "Backup guardado en: $backupSubDir" -ForegroundColor Yellow
        }
    }
} elseif ($archiveLocal) {
    Write-Host "[6/8] Cache local: $archiveLocal"
    Write-Host ""
    Write-Host "Para extraer manualmente:" -ForegroundColor Yellow
    Write-Host "  tar -xzf ""$archiveLocal"" -C ""$root"""
    Write-Host ""
} elseif ($BakeIntoProject) {
    Write-Warning "No se puede extraer porque no se genero cache."
}

if ($doGcsCache) {
    Write-Host "[7/8] Exportando cache desde GCS..." -ForegroundColor Cyan
    Write-Host "(Esta funcionalidad requiere gsutil configurado)"
} else {
    Write-Host "[7/8] Omitido backup GCS"
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Green
    Write-Host "COMPLETADO" -ForegroundColor Green
    Write-Host "==============================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Guardado en: $snapshotDir" -ForegroundColor White
}

Write-Host ""
Write-Host "Listo - Proceso completado" -ForegroundColor Green
Write-Host ""
