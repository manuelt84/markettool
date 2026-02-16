# 📦 snapshot_pod_cache.ps1 - Guía de Uso

## 🎯 Propósito

Extrae y guarda la cache de MarketTool desde pods de Kubernetes o contenedores Docker en ejecución, incluyendo:
- `historicos/` - Datos históricos OHLCV
- `forex_news/` - Noticias de Forex
- `indicators/` o `indicadores/` - Indicadores técnicos calculados
- `indicators_cache/` - Cache de indicadores

## ✨ Mejoras Recientes (Febrero 2026)

✅ **Extracción automática** - Usa `-BakeIntoProject` para extraer directo al proyecto  
✅ **Sin directorio `app/`** - Los archivos se extraen directamente en la raíz  
✅ **Backup automático** - Respalda directorios existentes antes de reemplazar  
✅ **Modo interactivo mejorado** - Pregunta si deseas extraer automáticamente  
✅ **Información detallada** - Muestra cantidad de archivos y tamaños  

---

## 🚀 Uso Rápido

### Modo Interactivo (Recomendado)

```powershell
# Ejecutar sin parámetros
.\scripts\snapshot_pod_cache.ps1
```

El script te preguntará:
1. ¿Qué descargar? (Docker local, GCP, o ambos)
2. ¿Extraer automáticamente al proyecto?

### Modo Automático

```powershell
# Descargar desde Docker y extraer automáticamente
.\scripts\snapshot_pod_cache.ps1 -Runtime docker -BakeIntoProject

# Solo descargar desde Kubernetes sin extraer
.\scripts\snapshot_pod_cache.ps1 -Runtime kube -SkipGcsCache

# Descargar desde contenedor específico
.\scripts\snapshot_pod_cache.ps1 -DockerContainer app3 -BakeIntoProject
```

---

## 📋 Parámetros

| Parámetro | Valores | Por Defecto | Descripción |
|-----------|---------|-------------|-------------|
| `-Runtime` | `auto`, `kube`, `docker` | `auto` | Fuente de datos |
| `-BakeIntoProject` | Switch | `false` | Extraer automáticamente al proyecto |
| `-SkipGcsCache` | Switch | `false` | Omitir descarga desde GCS |
| `-DockerContainer` | String | (auto) | Nombre del contenedor Docker |
| `-Namespace` | String | `default` | Namespace de Kubernetes |
| `-Selector` | String | `app=markettool` | Selector de pods |
| `-BucketName` | String | `markettool_bucket` | Nombre del bucket GCS |
| `-IncludeExecArtifacts` | Switch | `false` | Incluir `analisis/exec` de GCS |

---

## 📂 ¿Qué Hace?

### 1. Descarga desde Docker/Kubernetes

Crea un archivo `.tgz` con las carpetas de cache del contenedor/pod:

```
backup/pod-cache/20260216_145030/
└── local/
    └── markettool-cache-20260216_145030.tgz
```

### 2. Extracción Automática (con `-BakeIntoProject`)

**Antes:**
```
markettool-cache.tgz
├── app/              ❌ Carpeta extra
│   ├── historicos/
│   ├── forex_news/
│   └── indicators/
```

**Después (con mejoras):**
```
markettool-cache.tgz
├── historicos/       ✅ Directo al root
├── forex_news/       ✅ Sin carpeta app/
└── indicators/       ✅ Listo para usar
```

**Al extraer con `-BakeIntoProject`:**

1. ✅ **Backup automático** de directorios existentes en:
   ```
   backup/pre-snapshot-20260216_145030/
   ├── historicos/
   ├── forex_news/
   └── indicators/
   ```

2. ✅ **Extrae directo a raíz** del proyecto:
   ```
   c:\projects\marketTool\
   ├── historicos/       ← Actualizado
   ├── forex_news/       ← Actualizado
   └── indicators/       ← Actualizado
   ```

3. ✅ **Muestra información**:
   ```
   ✓ historicos (1,234 archivos, 2.5 GB)
   ✓ forex_news (567 archivos, 450 MB)
   ✓ indicators (890 archivos, 1.2 GB)
   ```

---

## 🔄 Workflow Típico

### Workflow 1: Extraer Cache para Desarrollo Local

```powershell
# 1. Descargar y extraer desde Docker
.\scripts\snapshot_pod_cache.ps1 -Runtime docker -BakeIntoProject -SkipGcsCache

# 2. Los directorios ya están actualizados en:
#    c:\projects\marketTool\historicos
#    c:\projects\marketTool\forex_news
#    c:\projects\marketTool\indicators

# 3. Correr la app localmente (ya tiene cache)
python .\MarketTool.py
```

### Workflow 2: Bakear Cache en Docker Image

```powershell
# 1. Descargar y extraer desde producción
.\scripts\snapshot_pod_cache.ps1 -Runtime kube -BakeIntoProject

# 2. Build Docker (incluirá las carpetas actualizadas)
docker build -t markettool:with-cache -f Dockerfile.optimized .

# 3. La imagen ya tiene historicos y forex_news pre-cargados
docker run markettool:with-cache
```

### Workflow 3: Backup Completo (Docker + GCS)

```powershell
# 1. Modo interactivo
.\scripts\snapshot_pod_cache.ps1

# 2. Selecciona opción 3 (Ambos)
# 3. Confirma extracción automática: S

# Resultado:
# backup/pod-cache/20260216_145030/
# ├── local/
# │   └── markettool-cache-20260216_145030.tgz
# └── gcs/
#     ├── historicos/
#     └── indicators/
```

---

## 🎨 Ejemplo de Salida

### Con `-BakeIntoProject`

```
════════════════════════════════════════════
   📦 SNAPSHOT DE CACHE - MarketTool
════════════════════════════════════════════

[0/6] Runtime seleccionado: Docker
[1/6] Resolviendo contenedor Docker...
Contenedor seleccionado: app3
[2/6] Creando archivo de cache dentro del contenedor...
Carpetas detectadas en contenedor: /app/historicos, /app/forex_news, /app/indicators
[3/6] Verificando si el archivo existe en el contenedor...
[4/6] Copiando archivo al workspace...
[5/6] Limpiando archivo temporal del contenedor...
[6/8] Extrayendo cache local al proyecto automáticamente...
  📦 Creando backup de directorios existentes en: backup/pre-snapshot-20260216_145030
    - Respaldando historicos...
    - Respaldando forex_news...
  📂 Extrayendo archivos de cache al proyecto...

✅ Cache extraída exitosamente en el proyecto:
   ✓ historicos (1,234 archivos, 2.5 GB)
   ✓ forex_news (567 archivos, 450 MB)
   ✓ indicators (890 archivos, 1.2 GB)

🔨 El próximo build de Docker incluirá estos archivos de cache
   (COPY . . en Dockerfile copiará las carpetas actualizadas)

⚠️  Backup de directorios previos guardado en:
   c:\projects\marketTool\backup\pre-snapshot-20260216_145030

════════════════════════════════════════════
   ✅ SNAPSHOT COMPLETADO
════════════════════════════════════════════

Guardado en: c:\projects\marketTool\backup\pod-cache\20260216_145030

🎉 Proceso completado exitosamente
```

---

## ⚠️ Notas Importantes

### Backup Automático
- ✅ Se crea backup antes de reemplazar
- ✅ Ubicación: `backup/pre-snapshot-<timestamp>/`
- ✅ Incluye solo directorios que existían previamente

### Requisitos
- **Para Docker**: Docker Desktop/Engine corriendo
- **Para Kubernetes**: `kubectl` configurado con contexto activo
- **Para GCS**: `gsutil` y credenciales GCP configuradas
- **Para extracción**: `tar` en PATH (Windows 10+ lo incluye)

### Compatibilidad
- ✅ Windows PowerShell 5.1+
- ✅ PowerShell Core 7+
- ✅ Windows 10/11 (tar integrado)
- ✅ Linux/macOS (con PowerShell Core)

---

## 🐛 Troubleshooting

### "No existe contenedor en ejecución"
```powershell
# Ver contenedores activos
docker ps

# Especificar contenedor manualmente
.\scripts\snapshot_pod_cache.ps1 -DockerContainer app3 -BakeIntoProject
```

### "Docker daemon no disponible"
```powershell
# Iniciar Docker Desktop
# Luego verificar:
docker version
```

### "No se pudo consultar pods"
```powershell
# Verificar contexto Kubernetes
kubectl config current-context

# Ver pods disponibles
kubectl get pods -n default

# Usar contexto específico
.\scripts\snapshot_pod_cache.ps1 -KubeContext my-cluster -BakeIntoProject
```

### "Error al extraer archivo tar"
```powershell
# Verificar que tar está disponible
tar --version

# Extraer manualmente si falla
tar -xzf "backup\pod-cache\...\markettool-cache-....tgz" -C "c:\projects\marketTool"
```

---

## 🎯 Tips Pro

### 1. Alias para Uso Frecuente

```powershell
# Agregar a tu $PROFILE
function Snapshot-Cache {
    .\scripts\snapshot_pod_cache.ps1 -Runtime docker -BakeIntoProject -SkipGcsCache
}

# Usar:
Snapshot-Cache
```

### 2. Programar Backups Automáticos

```powershell
# Crear Task Scheduler para backup diario
# Comando: pwsh -File "c:\projects\marketTool\scripts\snapshot_pod_cache.ps1" -Runtime kube -SkipGcsCache
```

### 3. Verificar Cache Extraída

```powershell
# Ver archivos en historicos
Get-ChildItem -Path historicos -Recurse | Measure-Object

# Ver tamaño total
Get-ChildItem -Path historicos,forex_news,indicators -Recurse -File | 
    Measure-Object -Property Length -Sum | 
    Select-Object @{N="TotalGB";E={[math]::Round($_.Sum/1GB,2)}}
```

---

## 📝 Changelog

### v2.0 (Febrero 2026)
- ✅ Extracción automática sin carpeta `app/`
- ✅ Backup automático de directorios existentes
- ✅ Modo interactivo mejorado
- ✅ Información detallada de archivos y tamaños
- ✅ Mensajes con colores y emojis
- ✅ Documentación completa

### v1.0 (Anterior)
- ✅ Descarga desde Kubernetes/Docker
- ✅ Descarga desde GCS
- ✅ Modo interactivo básico

---

## 📞 Soporte

Para problemas o sugerencias, consulta:
- `DOCUMENTATION/` - Documentación del proyecto
- `PROJECT_STATUS.md` - Estado general
- Este README

---

**Última actualización**: Febrero 16, 2026  
**Versión**: 2.0 (Phase 8 Complete)
