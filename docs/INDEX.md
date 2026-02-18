# 📚 MarketTool - Índice de Documentación

> **Última actualización**: Febrero 2026  
> **Versión**: Production v8.0+  
> **Arquitectura**: Hexagonal (Clean Architecture)

---

## 🚀 Inicio Rápido

| Documento | Descripción | Audiencia |
|-----------|-------------|-----------|
| [README.md](README.md) | Visión general del proyecto | Todos |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Estado actual del sistema | PM/Tech Lead |
| [guides/QUICK_START_PERFORMANCE.md](guides/QUICK_START_PERFORMANCE.md) | Setup rápido (5 min) | Developers |
| [deployment/gke/README.md](../deployment/gke/README.md) | Despliegue en GKE | DevOps |

---

## 🏗️ Arquitectura

### Diseño del Sistema
- **[architecture/ARQUITECTURA_HEXAGONAL.md](architecture/ARQUITECTURA_HEXAGONAL.md)** - Diseño hexagonal completo
- **[architecture/INDICE_DOCUMENTACION_COMPLETA.md](architecture/INDICE_DOCUMENTACION_COMPLETA.md)** - Índice detallado arquitectura
- **[architecture/MULTIPOD_COORDINATION.md](architecture/MULTIPOD_COORDINATION.md)** - Coordinación multi-pod
- **[architecture/ANALYSIS_FLOW_AUDIT.md](architecture/ANALYSIS_FLOW_AUDIT.md)** - Flujo de análisis

### Cambios y Refactorización
- **[architecture/CAMBIOS_REALIZADOS.md](architecture/CAMBIOS_REALIZADOS.md)** - Historial de cambios
- **[architecture/REFACTORING_SUMMARY.md](architecture/REFACTORING_SUMMARY.md)** - Resumen refactorización
- **[architecture/IMPLEMENTACION_FASES_COMPLETADA.md](architecture/IMPLEMENTACION_FASES_COMPLETADA.md)** - Fases completadas

---

## 📖 Guías de Uso

### Setup y Configuración
- **[guides/LOCAL_TESTING_GUIDE.md](guides/LOCAL_TESTING_GUIDE.md)** - Testing local
- **[guides/GCS_INTEGRATION_GUIDE.md](guides/GCS_INTEGRATION_GUIDE.md)** - Integración Google Cloud Storage
- **[guides/PRE_BUILD_CHECKLIST.md](guides/PRE_BUILD_CHECKLIST.md)** - Checklist pre-build
- **[guides/DEPLOYMENT_STEPS.md](guides/DEPLOYMENT_STEPS.md)** - Pasos de despliegue

### Operación y Mantenimiento
- **[guides/STARTUP_FIXES.md](guides/STARTUP_FIXES.md)** - Troubleshooting startup
- **[guides/VERIFY_CHANGES.md](guides/VERIFY_CHANGES.md)** - Scripts de validación
- **[guides/TESTING_Y_VALIDACION.md](guides/TESTING_Y_VALIDACION.md)** - Procedimientos de testing
- **[guides/FINAL_VERIFICATION.md](guides/FINAL_VERIFICATION.md)** - Verificación deployment

### Referencias Rápidas
- **[guides/QUICK_REFERENCE.md](guides/QUICK_REFERENCE.md)** - Lookup rápido
- **[guides/CODE_CORRECTIONS_CHECKLIST.md](guides/CODE_CORRECTIONS_CHECKLIST.md)** - Calidad de código
- **[guides/snapshot_pod_cache_guide.md](guides/snapshot_pod_cache_guide.md)** - Snapshot de cache

---

## ⚡ Optimización y Performance

### Reportes de Performance
- **[optimization/OPTIMIZATION_REPORT.md](optimization/OPTIMIZATION_REPORT.md)** - Reporte principal
- **[optimization/PERFORMANCE_OPTIMIZATION_FINAL.md](optimization/PERFORMANCE_OPTIMIZATION_FINAL.md)** - Optimizaciones finales
- **[optimization/OPTIMIZATION_PERFORMANCE.md](optimization/OPTIMIZATION_PERFORMANCE.md)** - Métricas de performance

### Implementaciones Específicas
- **[optimization/PARALLEL_ENTRIES_IMPLEMENTATION.md](optimization/PARALLEL_ENTRIES_IMPLEMENTATION.md)** - Paralelización de entries
- **[optimization/PARALLEL_GCP_UPLOADS_IMPLEMENTATION.md](optimization/PARALLEL_GCP_UPLOADS_IMPLEMENTATION.md)** - Uploads paralelos GCP
- **[optimization/PIPELINE_OPTIMIZATION_EXECUTIVE_SUMMARY.md](optimization/PIPELINE_OPTIMIZATION_EXECUTIVE_SUMMARY.md)** - Pipeline optimization

### Cache y Almacenamiento
- **[optimization/INDICATORS_CACHE_DESIGN.md](optimization/INDICATORS_CACHE_DESIGN.md)** - Diseño cache indicadores
- **[optimization/INDICATORS_CACHE_MULTIPOD.md](optimization/INDICATORS_CACHE_MULTIPOD.md)** - Cache multi-pod
- **[optimization/PERMANENT_HISTORICOS_DESIGN.md](optimization/PERMANENT_HISTORICOS_DESIGN.md)** - Diseño históricos permanentes
- **[optimization/CACHE_OPTIMIZATION_ROADMAP.md](optimization/CACHE_OPTIMIZATION_ROADMAP.md)** - Roadmap optimización cache

### Fixes Específicos
- **[optimization/FIX_STALE_1M_CANDLES.md](optimization/FIX_STALE_1M_CANDLES.md)** - Fix velas 1min stale
- **[optimization/FIX_EVENT_CACHING_NGINX_TIMEOUT.md](optimization/FIX_EVENT_CACHING_NGINX_TIMEOUT.md)** - Fix timeout nginx
- **[optimization/ENTRADA_QUALITY_OPTIMIZATION.md](optimization/ENTRADA_QUALITY_OPTIMIZATION.md)** - Calidad de entradas

---

## 🔍 Auditorías y Análisis

### Auditorías de Sistema
- **[audits/PARALELISMO_AUDIT_COMPLETO.md](audits/PARALELISMO_AUDIT_COMPLETO.md)** - Auditoría paralelismo
- **[audits/TRADER_AUDIT_REPORT.md](audits/TRADER_AUDIT_REPORT.md)** - Auditoría lógica trading
- **[audits/RESUMEN_AUDITORIA.md](audits/RESUMEN_AUDITORIA.md)** - Resumen de auditorías

### Seguridad y Concurrencia
- **[audits/THREAD_SAFETY_FIXES.md](audits/THREAD_SAFETY_FIXES.md)** - Fixes thread-safety
- **[ANALISIS_CONCURRENCIA_THREADS.md](ANALISIS_CONCURRENCIA_THREADS.md)** - Análisis concurrencia

### Problemas Específicos
- **[audits/RESOURCETRACKER_FIX.md](audits/RESOURCETRACKER_FIX.md)** - Fix ResourceTracker
- **[architecture/BOTTLENECK_AUDIT.md](architecture/BOTTLENECK_AUDIT.md)** - Auditoría bottlenecks
- **[architecture/MULTIPOD_ISSUES_ANALYSIS.md](architecture/MULTIPOD_ISSUES_ANALYSIS.md)** - Análisis issues multi-pod

---

## 📋 Fases de Implementación

### Fases Completas
- **[phases/PHASE1_COMPLETE.md](phases/PHASE1_COMPLETE.md)** - Domain models, ports, errors
- **[phases/PHASE2_GCS_COMPLETE.md](phases/PHASE2_GCS_COMPLETE.md)** - Integración GCS
- **[phases/PHASE_5_COMPLETION_REPORT.md](phases/PHASE_5_COMPLETION_REPORT.md)** - Implementación adapters
- **[phases/PHASES_6_7_SUMMARY.md](phases/PHASES_6_7_SUMMARY.md)** - Unit tests + bot integration
- **[phases/PHASE_7_COMPLETE_SUMMARY.md](phases/PHASE_7_COMPLETE_SUMMARY.md)** - Resumen fase 7
- **[phases/PHASE_7_READY_FOR_PRODUCTION.md](phases/PHASE_7_READY_FOR_PRODUCTION.md)** - Production ready

### Documentación de Fases
- **[phases/PHASE_7_DOCUMENTATION_INDEX.md](phases/PHASE_7_DOCUMENTATION_INDEX.md)** - Índice fase 7
- **[phases/PHASE_7_ARCHITECTURE_DIAGRAMS.md](phases/PHASE_7_ARCHITECTURE_DIAGRAMS.md)** - Diagramas arquitectura
- **[phases/PHASE_7a_COMMAND_MAPPING_COMPLETE.md](phases/PHASE_7a_COMMAND_MAPPING_COMPLETE.md)** - Mapeo comandos
- **[phases/PHASE_7b_HANDLER_INTEGRATION_COMPLETE.md](phases/PHASE_7b_HANDLER_INTEGRATION_COMPLETE.md)** - Integración handlers

---

## 🚢 Deployment

### GKE (Google Kubernetes Engine)
- **[../deployment/gke/README.md](../deployment/gke/README.md)** - Setup GKE
- **[../deployment/gke/GKE_DEPLOYMENT_GUIDE.md](../deployment/gke/GKE_DEPLOYMENT_GUIDE.md)** - Guía completa deployment

### Verificación y Testing
- **[optimization/POST_DOCKER_VALIDATION.md](optimization/POST_DOCKER_VALIDATION.md)** - Validación post-Docker
- **[guides/FINAL_VERIFICATION.md](guides/FINAL_VERIFICATION.md)** - Verificación final

---

## 📦 Documentos Adicionales

### Resúmenes y Reportes
- **[FINAL_SUMMARY.md](FINAL_SUMMARY.md)** - Resumen final del proyecto
- **[ERROR_FIXES_SUMMARY.md](ERROR_FIXES_SUMMARY.md)** - Resumen de fixes
- **[LOGIC_ERRORS_FINAL_REPORT.md](LOGIC_ERRORS_FINAL_REPORT.md)** - Reporte errores lógica
- **[PROJECT_ORGANIZATION.md](PROJECT_ORGANIZATION.md)** - Organización del proyecto

### Análisis Comparativos
- **[LEGACY_VS_HEXAGONAL_ANALYSIS.md](LEGACY_VS_HEXAGONAL_ANALYSIS.md)** - Comparativa legacy vs hexagonal
- **[IMPLEMENTACION_COMPLETA_PARALELISMO.md](IMPLEMENTACION_COMPLETA_PARALELISMO.md)** - Implementación paralelismo
- **[GUIA_INTEGRACION_PARALELISMO.md](GUIA_INTEGRACION_PARALELISMO.md)** - Guía integración paralelismo

### En Raíz del Proyecto
- **[../README.md](../README.md)** - README principal del proyecto
- **[../OPTIMIZACIONES_PENDIENTES.md](../OPTIMIZACIONES_PENDIENTES.md)** - Backlog de optimizaciones
- **[../PROJECT_STATUS.md](../PROJECT_STATUS.md)** - Status del proyecto

---

## 📁 Archivo Histórico

Documentos obsoletos o de fases anteriores:
- **[archive/](archive/)** - Documentos históricos y obsoletos
- **[archive/TIMEZONE_AUDIT_COMPLETION_REPORT.md](archive/TIMEZONE_AUDIT_COMPLETION_REPORT.md)**
- **[archive/SESSION_FINAL_COMPLETE.md](archive/SESSION_FINAL_COMPLETE.md)**
- **[archive/THREADING_ISSUES.md](archive/THREADING_ISSUES.md)**
- **[archive/THREADING_FIXES_COMPLETED.md](archive/THREADING_FIXES_COMPLETED.md)**

---

## 🔗 Navegación Rápida

### Por Rol
- **🧑‍💼 Product Manager**: [PROJECT_STATUS.md](PROJECT_STATUS.md), [FINAL_SUMMARY.md](FINAL_SUMMARY.md)
- **👨‍💻 Developer**: [guides/QUICK_START_PERFORMANCE.md](guides/QUICK_START_PERFORMANCE.md), [architecture/ARQUITECTURA_HEXAGONAL.md](architecture/ARQUITECTURA_HEXAGONAL.md)
- **🔧 DevOps**: [deployment/gke/README.md](../deployment/gke/README.md), [guides/DEPLOYMENT_STEPS.md](guides/DEPLOYMENT_STEPS.md)
- **⚡ Performance Engineer**: [optimization/OPTIMIZATION_REPORT.md](optimization/OPTIMIZATION_REPORT.md), [audits/PARALELISMO_AUDIT_COMPLETO.md](audits/PARALELISMO_AUDIT_COMPLETO.md)

### Por Tarea
- **🚀 Setup inicial**: [guides/LOCAL_TESTING_GUIDE.md](guides/LOCAL_TESTING_GUIDE.md)
- **🐛 Debugging**: [guides/STARTUP_FIXES.md](guides/STARTUP_FIXES.md), [guides/VERIFY_CHANGES.md](guides/VERIFY_CHANGES.md)
- **📊 Performance tuning**: [optimization/OPTIMIZATION_REPORT.md](optimization/OPTIMIZATION_REPORT.md)
- **🎯 Understanding architecture**: [architecture/ARQUITECTURA_HEXAGONAL.md](architecture/ARQUITECTURA_HEXAGONAL.md)

---

## 📊 Estado del Proyecto

- ✅ **Fase 1-7**: Completadas
- ✅ **Arquitectura Hexagonal**: Implementada
- ✅ **Multi-pod**: Producción estable
- ✅ **Performance**: Optimizado (12-15s análisis completo)
- 🚀 **Deployment**: GKE en producción

---

**Documentación mantenida por**: Equipo MarketTool  
**Última revisión**: Febrero 2026
