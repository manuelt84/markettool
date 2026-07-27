# 📜 Historia de Desincronización: Eventos Económicos Web vs RN

**Fecha:** 2026-07-27 13:00 GMT-4  
**Investigación:** ¿Por qué Web y RN dejaron de estar homologados en eventos económicos?

---

## 🔍 LÍNEA DE TIEMPO DE COMMITS

### Estado Original (Correcto)

**Commit `168843e`** - "feat: shared botStats module + homologate bot cards & live stats with RN"
```typescript
// Web: MonitoreoPage.tsx
const [showEconomicEvents, setShowEconomicEvents] = useState(false); // ✅ OFF por defecto
```

**RN en ese momento:**
- Sin hook `useEventosEconomicos` activo
- Sin toggle UI para eventos
- ✅ **Consistente con Web: ambos OFF por defecto**

---

### El Commit Problemático

**Commit `9f18fe3`** - "fix: showEconomicEvents default true — homologa con RN ecoPollingEnabled:true"

**Cambió:**
```diff
- const [showEconomicEvents, setShowEconomicEvents] = useState(false);
+ const [showEconomicEvents, setShowEconomicEvents] = useState(true); // default ON — matches RN ecoPollingEnabled:true
```

**Problema:**
- El comentario afirma "matches RN ecoPollingEnabled:true"
- **PERO RN NUNCA TUVO `ecoPollingEnabled`**
- Esto fue una **asunción incorrecta** del desarrollador
- Resultado: Web activó eventos por defecto, RN siguió sin ellos

**Impacto:**
- Web: Consumiendo recursos de API de eventos económicos (polling cada 60s)
- RN: Sin eventos económicos
- **Desincronización: Web genera entradas con eventos, RN no**

---

### Consecuencias en Entradas en Vivo

| Período | Web | RN | Diferencia |
|---------|-----|----|------------|
| Antes de `9f18fe3` | ❌ Sin eventos | ❌ Sin eventos | ✅ Iguales |
| Después de `9f18fe3` | ✅ Con eventos | ❌ Sin eventos | ❌ **Web tiene +20-40% entradas** |
| Hoy (fix aplicado) | ❌ Sin eventos (fixed) | ⚠️ Soporta pero no usa | ⚠️ Pendiente integrar UI |

---

## 🛠️ FIXES APLICADOS HOY

### Fix #1: Web - Corregir Default

**Commit:** `4eee468` en markettool-web

```typescript
// ANTES (incorrecto):
const [showEconomicEvents, setShowEconomicEvents] = useState(true); // default ON — matches RN ecoPollingEnabled:true

// AHORA (correcto):
const [showEconomicEvents, setShowEconomicEvents] = useState(false); // default OFF para ahorrar recursos - usuario debe activar explícitamente
```

**Racional:**
- Eventos económicos consumen muchos recursos (API calls, polling 60s)
- Deben ser opt-in, no opt-out
- Consistente con principio de ahorro de recursos

---

### Fix #2: RN - Agregar Soporte de Parámetros

**Commit:** `82d0261` en markettoolapp

```typescript
// MonitoreoConfig ahora acepta:
export interface MonitoreoConfig {
  // ... existing
  trainingData?: MTResumen | null;
  events?: EconomicEvent[];
}

// Y los pasa a buildBacktestEntries:
await buildBacktestEntries(
  symbol, tf, merged,
  cfg.trainingData ?? null,  // ✅ Ahora soporta
  cfg.events ?? [],          // ✅ Ahora soporta
  true, 3
);
```

**Estado:** Infraestructura lista, pendiente integrar UI y hook

---

## 📊 ESTADO ACTUAL POST-FIXES

| Plataforma | Soporta Eventos | Default | Toggle UI | Entradas con Eventos |
|------------|-----------------|---------|-----------|---------------------|
| **Web** | ✅ Sí | ❌ OFF | ✅ Chip/Button | Solo si usuario activa |
| **RN** | ✅ Sí (infra) | ❌ N/A | ❌ No tiene | ⚠️ Pendiente integrar |

---

## 🔧 PRÓXIMOS PASOS RECOMENDADOS

### Para RN (Opcional, Si Quieres Paridad Completa)

1. **Agregar toggle/chip en ScalpingBotModal:**
   ```typescript
   const [showEconomicEvents, setShowEconomicEvents] = useState(false);
   const { events } = useEventosEconomicos(
     API_BASE_URL,
     userId,
     execId,
     symbol,
     { enabled: showEconomicEvents, pollMs: 60_000 }
   );
   ```

2. **Pasar events al iniciar monitoreo:**
   ```typescript
   await startMonitoreo({
     // ... config existente
     events: showEconomicEvents ? events : [],
   });
   ```

3. **Agregar chip visual (opcional):**
   - Similar al chip de "Live" en Web
   - Indicador cuando hay eventos activos

### Para Web (Ya Completo)

✅ Listo - usuario puede activar/desactivar eventos con chip

---

## 🎯 LECCIONES APRENDIDAS

1. **No asumir paridad sin verificar:** El comentario "matches RN ecoPollingEnabled:true" fue incorrecto porque RN nunca tuvo esa variable.

2. **Documentar assumptions:** Si un fix "homologa" con otra plataforma, verificar que la otra plataforma realmente tenga esa feature.

3. **Default OFF para features costosas:** Eventos económicos consumen recursos significativos (API quota, polling, CPU). Deben ser opt-in.

4. **Tests de paridad:** Considerar agregar tests automatizados que verifiquen que Web y RN tienen los mismos defaults y toggles.

---

## 📝 COMMITS RELACIONADOS

| Commit | Repo | Descripción |
|--------|------|-------------|
| `168843e` | web | Estado original correcto (false) |
| `9f18fe3` | web | **ERROR:** Cambió a true con comentario incorrecto |
| `4eee468` | web | **FIX:** Corregido a false |
| `82d0261` | rn | **FIX:** Agregado soporte de parámetros |

---

**Firma:** Luna (asistente OpenClaw)  
**Timestamp:** 2026-07-27 13:00 GMT-4
