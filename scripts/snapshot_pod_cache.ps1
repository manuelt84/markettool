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

function Test-KubeConnection {
    param(
        [string]$DesiredContext,
        [string]$DesiredKubeconfig
    )

    if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
        throw "kubectl no esta instalado o no esta en PATH."
    }

    if ($DesiredKubeconfig -and $DesiredKubeconfig.Trim().Length -gt 0) {
        $resolvedKubeconfig = (Resolve-Path $DesiredKubeconfig).Path
        $env:KUBECONFIG = $resolvedKubeconfig
        Write-Host "Usando KUBECONFIG: $resolvedKubeconfig"
    }

    if ($DesiredContext -and $DesiredContext.Trim().Length -gt 0) {
        kubectl config use-context $DesiredContext | Out-Null
    }

    $ctx = (kubectl config current-context 2>$null)
    if (-not $ctx) {
        $all = (kubectl config get-contexts -o name 2>$null)
        $known = if ($all) { ($all -join ", ") } else { "(ninguno)" }
        throw "kubectl no tiene contexto activo. Contextos detectados: $known. Configura uno con 'kubectl config use-context <contexto>' o credenciales del cluster."
    }

    kubectl cluster-info --request-timeout=8s 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "No hay conexion al cluster para el contexto '$ctx'. Revisa credenciales (ej: gcloud container clusters get-credentials ...) y vuelve a intentar."
    }

    Write-Host "Contexto Kubernetes activo: $ctx"
}

function Test-DockerConnection {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker no esta instalado o no esta en PATH."
    }

    # Evita falsos negativos por warnings de stderr en 'docker info'.
    # 'docker version' normalmente no emite esos warnings y valida cliente+server.
    & docker version 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon no disponible. Verifica que Docker Desktop/Engine este corriendo."
    }
}

function Resolve-DockerContainer {
    param([string]$Preferred)

    if ($Preferred -and $Preferred.Trim().Length -gt 0) {
        $exists = docker ps --format "{{.Names}}" | Where-Object { $_ -eq $Preferred }
        if (-not $exists) {
            throw "No existe contenedor en ejecucion con nombre '$Preferred'."
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
        throw "No se encontro contenedor candidato de markettool. Usa -DockerContainer para indicarlo manualmente."
    }

    if ($candidates.Count -gt 1) {
        Write-Warning "Se encontraron varios contenedores candidatos: $($candidates -join ', '). Usando '$($candidates[0])'."
    }

    return $candidates[0]
}

function Snapshot-LocalCacheFromKube {
    param(
        [string]$NamespaceArg,
        [string]$SelectorArg,
        [string]$ContainerArg,
        [string]$TimestampArg,
        [string]$SnapshotLocalDirArg
    )

    Write-Host "[1/6] Resolviendo pod de $SelectorArg en namespace $NamespaceArg..."
    $pod = kubectl get pods -n $NamespaceArg -l $SelectorArg -o jsonpath="{.items[0].metadata.name}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo consultar pods en namespace '$NamespaceArg'. Verifica permisos RBAC y namespace."
    }
    if (-not $pod) {
        $pods = kubectl get pods -n $NamespaceArg --no-headers 2>$null
        $podsMsg = if ($pods) { "Pods en namespace '$NamespaceArg':`n$pods" } else { "No hay pods visibles en '$NamespaceArg'." }
        throw "No se encontro pod para selector '$SelectorArg' en namespace '$NamespaceArg'.`n$podsMsg"
    }

    Write-Host "Pod seleccionado: $pod"

    $archiveInPod = "/tmp/markettool-cache-$TimestampArg.tgz"
    $archiveLocal = Join-Path $SnapshotLocalDirArg "markettool-cache-$TimestampArg.tgz"

    Write-Host "[2/6] Creando archivo de cache dentro del pod..."
    $dirPaths = @()
    $dirCandidates = @("historicos", "forex_news", "indicators", "indicadores", "indicators_cache")
    foreach ($d in $dirCandidates) {
        $exists = kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "test -d /app/$d && echo YES || echo NO"
        if (($exists | Out-String).Trim() -eq "YES") { $dirPaths += "/app/$d" }
    }

    $histDir = kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "printenv HIST_DIR 2>/dev/null || true"
    $histDir = ($histDir | Out-String).Trim()
    if ($histDir) {
        $histPath = if ($histDir.StartsWith("/")) { $histDir } else { "/app/$histDir" }
        $existsHist = kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "test -d $histPath && echo YES || echo NO"
        if (($existsHist | Out-String).Trim() -eq "YES") { $dirPaths += $histPath }
    }

    if (-not $dirPaths -or $dirPaths.Count -eq 0) {
        Write-Warning "No hay carpetas de cache locales detectadas en el pod (/app/historicos, /app/forex_news, /app/indicators...)."
        return ""
    }

    $dirPathsArg = $dirPaths -join " "
    Write-Host "Carpetas detectadas en pod: $($dirPaths -join ', ')"
    kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "tar -czf $archiveInPod $dirPathsArg && ls -lh $archiveInPod" | Out-Host

    Write-Host "[3/6] Verificando si el archivo existe en el pod..."
    $exists = kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "test -f $archiveInPod && echo YES || echo NO"
    if ($exists.Trim() -ne "YES") {
        Write-Warning "No hay carpetas de cache locales en el pod (/app/historicos o /app/forex_news)."
        return ""
    }

    Write-Host "[4/6] Copiando archivo al workspace..."
    kubectl cp "${NamespaceArg}/${pod}:${archiveInPod}" $archiveLocal -c $ContainerArg

    Write-Host "[5/6] Limpiando archivo temporal del pod..."
    kubectl exec -n $NamespaceArg $pod -c $ContainerArg -- sh -lc "rm -f $archiveInPod" | Out-Null

    return $archiveLocal
}

function Snapshot-LocalCacheFromDocker {
    param(
        [string]$DockerContainerArg,
        [string]$TimestampArg,
        [string]$SnapshotLocalDirArg
    )

    Write-Host "[1/6] Resolviendo contenedor Docker..."
    $container = Resolve-DockerContainer -Preferred $DockerContainerArg
    Write-Host "Contenedor seleccionado: $container"

    $archiveInContainer = "/tmp/markettool-cache-$TimestampArg.tgz"
    $archiveLocal = Join-Path $SnapshotLocalDirArg "markettool-cache-$TimestampArg.tgz"

    Write-Host "[2/6] Creando archivo de cache dentro del contenedor..."
    $dirPaths = @()
    $dirCandidates = @("historicos", "forex_news", "indicators", "indicadores", "indicators_cache")
    foreach ($d in $dirCandidates) {
        $exists = docker exec $container sh -lc "test -d /app/$d && echo YES || echo NO"
        if (($exists | Out-String).Trim() -eq "YES") { $dirPaths += "/app/$d" }
    }

    $histDir = docker exec $container sh -lc "printenv HIST_DIR 2>/dev/null || true"
    $histDir = ($histDir | Out-String).Trim()
    if ($histDir) {
        $histPath = if ($histDir.StartsWith("/")) { $histDir } else { "/app/$histDir" }
        $existsHist = docker exec $container sh -lc "test -d $histPath && echo YES || echo NO"
        if (($existsHist | Out-String).Trim() -eq "YES") { $dirPaths += $histPath }
    }

    if (-not $dirPaths -or $dirPaths.Count -eq 0) {
        Write-Warning "No hay carpetas de cache locales detectadas en el contenedor (/app/historicos, /app/forex_news, /app/indicators...)."
        return ""
    }

    $dirPathsArg = $dirPaths -join " "
    Write-Host "Carpetas detectadas en contenedor: $($dirPaths -join ', ')"
    docker exec $container sh -lc "tar -czf $archiveInContainer $dirPathsArg && ls -lh $archiveInContainer" | Out-Host

    Write-Host "[3/6] Verificando si el archivo existe en el contenedor..."
    $exists = docker exec $container sh -lc "test -f $archiveInContainer && echo YES || echo NO"
    if ($exists.Trim() -ne "YES") {
        Write-Warning "No se pudo generar archivo de cache en el contenedor."
        return ""
    }

    Write-Host "[4/6] Copiando archivo al workspace..."
    docker cp "${container}:${archiveInContainer}" $archiveLocal

    Write-Host "[5/6] Limpiando archivo temporal del contenedor..."
    docker exec $container sh -lc "rm -f $archiveInContainer" | Out-Null

    return $archiveLocal
}

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

$isInteractivePrompt =
    (-not $PSBoundParameters.ContainsKey("Runtime")) -and
    (-not $PSBoundParameters.ContainsKey("SkipGcsCache"))

if ($isInteractivePrompt) {
    Write-Host ""
    Write-Host "¿Que deseas rescatar?"
    Write-Host "  1) Docker local (/app/historicos + /app/forex_news)"
    Write-Host "  2) GCP (bucket: historicos + indicators)"
    Write-Host "  3) Ambos (Docker + GCP)"

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
            throw "Seleccion invalida: '$choice'. Debe ser 1, 2 o 3."
        }
    }
}

$archiveLocal = ""

if ($doLocalCache) {
    if ($Runtime -eq "kube") {
        Write-Host "[0/6] Runtime seleccionado: Kubernetes"
        Test-KubeConnection -DesiredContext $KubeContext -DesiredKubeconfig $Kubeconfig
        $archiveLocal = Snapshot-LocalCacheFromKube -NamespaceArg $Namespace -SelectorArg $Selector -ContainerArg $Container -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
    } elseif ($Runtime -eq "docker") {
        Write-Host "[0/6] Runtime seleccionado: Docker"
        Test-DockerConnection
        $archiveLocal = Snapshot-LocalCacheFromDocker -DockerContainerArg $DockerContainer -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
    } else {
        Write-Host "[0/6] Runtime auto: intentando Kubernetes y fallback a Docker..."
        try {
            Test-KubeConnection -DesiredContext $KubeContext -DesiredKubeconfig $Kubeconfig
            $archiveLocal = Snapshot-LocalCacheFromKube -NamespaceArg $Namespace -SelectorArg $Selector -ContainerArg $Container -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
        } catch {
            Write-Warning "Kubernetes no disponible: $($_.Exception.Message)"
            Write-Host "Cambiando a runtime Docker..."
            Test-DockerConnection
            $archiveLocal = Snapshot-LocalCacheFromDocker -DockerContainerArg $DockerContainer -TimestampArg $timestamp -SnapshotLocalDirArg $snapshotLocalDir
        }
    }

    if (-not $archiveLocal) {
        Write-Warning "No se genero archivo local de cache."
    }
} else {
    Write-Host "[0/6] Omitiendo snapshot local (Docker/Kubernetes) por seleccion interactiva."
}

if ($BakeIntoProject -and $archiveLocal) {
    Write-Host "[6/8] Extrayendo cache local al proyecto (para bakear en la próxima imagen)..."
    tar -xzf $archiveLocal -C $root
    Write-Host "Cache extraída en:"
    Write-Host " - $(Join-Path $root "historicos")"
    Write-Host " - $(Join-Path $root "forex_news")"
    Write-Host "Siguiente build incluirá estas carpetas por COPY . . en Dockerfile."
} elseif ($archiveLocal) {
    Write-Host "[6/8] Snapshot local guardado en: $archiveLocal"
    $archiveLocalQuoted = '"' + $archiveLocal + '"'
    $rootQuoted = '"' + $root + '"'
    Write-Host "Para inyectarlo al proyecto y bakear imagen, ejecuta (Windows PowerShell):"
    Write-Host "tar -xzf $archiveLocalQuoted -C $rootQuoted"
    Write-Host "Alternativa (PowerShell puro):"
    Write-Host "tar -xzf $archiveLocalQuoted -C $rootQuoted"
} elseif ($BakeIntoProject) {
    Write-Warning "No se puede bakear cache local porque no se genero snapshot local."
}

if ($doGcsCache) {
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
    Write-Host "[7/8] Omitido backup de GCS por seleccion del usuario (-SkipGcsCache o modo interactivo)"
    Write-Host "[8/8] Snapshot guardado en: $snapshotDir"
}

Write-Host "Proceso completado."