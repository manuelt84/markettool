# Web Scraping de Eventos Económicos desde Investing.com

## Overview

Se ha implementado un sistema de web scraping desde **investing.com** para capturar eventos económicos en tiempo real, complementando (y opcionalmente reemplazando) el uso de FMP que puede ser más lento.

## Características

✅ **Rápido**: Ejecuta muy rápidamente comparado con FMP  
✅ **Datos en Tiempo Real**: Obtiene events directamente de investing.com  
✅ **Fallback Inteligente**: Si requests+BeautifulSoup falla, intenta con Playwright  
✅ **Integración Seamless**: Se integra automáticamente con `obtener_eventos_economicos()`  
✅ **Deduplicación**: Automáticamente deduplica eventos de múltiples fuentes  

## Componentes

### 1. `_investing_com_econ_fetch()` 
**Función Principal - Estrategia Rápida**

```python
_investing_com_econ_fetch(timeout=15) -> pd.DataFrame
```

- Intenta primero con **requests + BeautifulSoup** (muy rápido)
- Parsea la tabla HTML del calendario económico de investing.com
- Extrae: fecha, moneda, evento, actual, estimado, anterior, impacto
- Retorna DataFrame con timestamps en UTC
- Si falla, cae a Playwright automáticamente

**Ventajas**:
- ~2-3 segundos para obtener datos
- No necesita navegador completo
- Confiable si HTML es estable

### 2. `_investing_com_econ_fetch_playwright()`
**Fallback - Estrategia Robusta**

```python
_investing_com_econ_fetch_playwright() -> pd.DataFrame
```

- Usa **Playwright** para renderizar JavaScript
- Espera a que la página cargue completamente
- Similar parseamiento que la versión requests
- Más lento pero mucho más robusto

**Ventajas**:
- Maneja contenido dinámico cargado por JavaScript
- Más confiable para cambios en estructura HTML
- Garantiza que el calendario esté renderizado

**Desventajas**:
- Más lento (~10-15 segundos)
- Requiere navegador Chromium descargado

## Instalación

### Paso 1: Actualizar dependencias

```bash
pip install -r requirements.txt
```

Esto instala automáticamente:
- `beautifulsoup4>=4.12.0` - Para parsing HTML rápido
- `playwright>=1.40.0` - Para rendering de JavaScript (fallback)

### Paso 2: Instalar navegador Playwright (opcional pero recomendado)

Solo necesario si quieres usar el fallback de Playwright:

```bash
playwright install chromium
```

O simplemente déjalo correr - Playwright lo descargará automáticamente cuando sea necesario.

## Uso

### Automático (Recomendado)

La función ya está integrada en `obtener_eventos_economicos()`:

```python
# Esto ahora usa investing.com + FMP + investiny automáticamente
df = obtener_eventos_economicos()
print(df[['date', 'currency', 'event', 'impact']])
```

El flujo es:
1. **investing.com web scraping** (primero, más rápido)
2. **FMP API** (si está configurado)
3. **investiny** (fallback final)
4. **Deduplicación automática** de eventos duplicados

### Manual (Avanzado)

```python
from MarketTool import _investing_com_econ_fetch, _investing_com_econ_fetch_playwright

# Intentar scraping rápido
df = _investing_com_econ_fetch(timeout=15)

# O forzar Playwright
df = _investing_com_econ_fetch_playwright()
```

## Formato de Salida

```
┌─────────────────────────────────────────────────────────────────┐
│date (UTC) │ currency │ event        │ impact │ actual │ estimate│
├─────────────────────────────────────────────────────────────────┤
│2025-02-11 │ USD      │ CPI          │ High   │ 2.5%   │ 2.7%   │
│2025-02-11 │ EUR      │ Inflation    │ Medium │ 2.1%   │ 2.0%   │
│2025-02-12 │ GBP      │ GDP          │ High   │ 1.2%   │ 1.1%   │
└─────────────────────────────────────────────────────────────────┘
```

**Columnas**:
- `date`: Timestamp UTC del evento
- `currency`: Moneda del evento (USD, EUR, GBP, etc.)
- `event`: Nombre del evento económico
- `actual`: Valor real reportado (si disponible)
- `estimate`: Valor estimado (forecast)
- `previous`: Valor anterior
- `impact`: Impacto: "High", "Medium", "Low"
- `date_country`: Igual a date (para compatibilidad)

## Configuración con Variables de Entorno

En tu `.env`:

```env
# Si quieres desactivar FMP e usar solo investing.com
FMP_PLAN=starter

# Timeout para web scraping (segundos)
HTTP_TIMEOUT=15

# Retries automáticos
HTTP_RETRIES=3

# Chunk de días para queries históricas
ECON_CHUNK_DAYS=31
```

## Logging

El sistema registra automáticamente:

```
INFO:MarketTool:[Investing.com] GET https://www.investing.com/economic-calendar/ (timeout=15)
INFO:MarketTool:[Investing.com] status=200 en 2.345s
INFO:MarketTool:[Eventos] Got 45 events from investing.com scraping
INFO:MarketTool:[Eventos] Cached days: 10
```

## Performance

| Fuente         | Velocidad | Confiabilidad | Cobertura |
|---|---|---|---|
| investing.com  | ⚡ 2-3s   | ★★★★★       | Últimos 7d |
| FMP API        | 🐢 5-10s  | ★★★☆☆      | Histórico  |
| investiny      | 🟡 3-5s   | ★★★★☆      | Últimos 7d |

**Ventaja**: La combinación de las 3 fuentes da cobertura completa + redundancia

## Troubleshooting

### "BeautifulSoup4 not installed"

```bash
pip install beautifulsoup4
```

### "Playwright not installed" (solo si ves el fallback)

```bash
pip install playwright
playwright install chromium
```

### "No events found in HTML"

Posible que investing.com cambió su estructura HTML. El sistema caerá a Playwright automáticamente. Si persiste, contacta a soporte con error logs.

### "Date parsing failed"

El sistema detecta esto y cae a Playwright. Esto es normal.

### Bloqueado por investing.com

Si ves muchos errores HTTP 429 (rate limit):
- Aumenta `HTTP_TIMEOUT` en `.env`
- O establece `FMP_PLAN=premium` para usar solo FMP
- O implementa rotación de IPs/proxies

## Ejemplo Completo

```python
import pandas as pd
from MarketTool import obtener_eventos_economicos

# Obtener eventos
df = obtener_eventos_economicos()

# Filtrar solo eventos HIGH
high_impact = df[df['impact'] == 'High'].copy()

# Ordenar por fecha
high_impact = high_impact.sort_values('date')

# Ver próximos eventos USD
upcoming_usd = (
    high_impact[high_impact['currency'] == 'USD']
    .reset_index(drop=True)
    [['date', 'event', 'actual', 'estimate']]
)

print(upcoming_usd)
```

## Mejoras Futuras

- [ ] Agregar filtro por país/región
- [ ] Cache persistente en Firestore
- [ ] Notificaciones de eventos próximos (Telegram)
- [ ] Histórico de accuracy (actual vs estimate)
- [ ] EventoS analíticos (impacto de eventos en precios)

## Referencias

- **Investing.com**: https://www.investing.com/economic-calendar/
- **BeautifulSoup**: https://www.crummy.com/software/BeautifulSoup/
- **Playwright**: https://playwright.dev/python/
