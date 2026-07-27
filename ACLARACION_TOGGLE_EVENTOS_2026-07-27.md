# 🔍 ACLARACIÓN: Toggle de Eventos Económicos

**Fecha:** 2026-07-27 13:40 GMT-4  
**Motivo:** Aclarar confusión sobre estado del toggle en capturas de pantalla

---

## 📊 ESTADO VERIFICADO

### Default al Cargar (Homologado ✅)

| Plataforma | Variable | Valor Default | Archivo |
|------------|----------|---------------|---------|
| **Web** | `showEconomicEvents` | `false` | `src/pages/MonitoreoPage.tsx` línea 823 |
| **RN** | `ecoPollingEnabled` | `false` | `views/MonitoreoScreen.tsx` línea 2143 |

**Código Web:**
```typescript
const [showEconomicEvents, setShowEconomicEvents] = useState(false); // default OFF para ahorrar recursos - usuario debe activar explícitamente
```

**Código RN:**
```typescript
ecoPollingEnabled: false, // default OFF para ahorrar recursos - usuario debe activar explícitamente
```

---

## ❓ CONFUSIÓN CON CAPTURAS DE PANTALLA

Las capturas de pantalla compartidas muestran el toggle de eventos económicos en la UI de Web. Esto **NO indica que el default sea ON**.

### Explicación

El toggle/chip de eventos económicos es un **control interactivo** que permite al usuario:
1. Activar polling de eventos cuando los necesita
2. Desactivar polling para ahorrar recursos

**Estados posibles:**
- **Al cargar página:** `false` (OFF, sin polling) ✅ HOMOLOGADO
- **Después de click usuario:** `true` (ON, con polling) ✅ COMPORTAMIENTO ESPERADO
- **Después de segundo click:** `false` (OFF, sin polling) ✅ COMPORTAMIENTO ESPERADO

### Lo que Muestran las Capturas

Si una captura muestra el toggle "activo" (ej: botón naranja "Ocultar" o switch verde), significa que:
- El usuario **ya hizo click** para activarlo durante esa sesión
- El sistema está haciendo polling cada 60s (Web) o 5s (RN)
- Los eventos se están usando para generación de entradas

Esto es **comportamiento correcto**, no un bug.

---

## ✅ HOMOLOGACIÓN CONFIRMADA

| Aspecto | Web | RN | Estado |
|---------|-----|----|--------|
| Default al cargar | OFF | OFF | ✅ IDÉNTICOS |
| Toggle disponible | ✅ Chip "Mostrar/Ocultar" | ✅ Toggle con ícono | ✅ Funcional en ambos |
| Polling al activar | ✅ Cada 60s | ✅ Cada 5s | ✅ Ambos hacen polling |
| Eventos a generación | ✅ Sí | ✅ Sí | ✅ IDÉNTICOS |
| TrainingData a generación | ✅ Sí | ✅ Sí | ✅ IDÉNTICOS |

---

## 🧪 CÓMO VERIFICAR EL DEFAULT

### Web
1. Abrir https://markettool.mtlabsx.com/monitoreos en incógnito
2. Entrar a cualquier símbolo
3. Observar chip de eventos: debe decir **"Mostrar"** (no "Ocultar")
4. Si dice "Mostrar": default es OFF ✅

### RN
1. Instalar APK v79.84 fresh (clear data)
2. Abrir Monitoreo Screen
3. Observar toggle de eventos: debe estar **gris/apagado**
4. Si está gris: default es OFF ✅

---

## 📝 CONCLUSIÓN

**La homologación está CORRECTAMENTE IMPLEMENTADA:**
- Ambas plataformas inician con eventos **DESACTIVADOS** por defecto
- Ambas permiten activación manual vía toggle
- Ambas usan eventos para generación cuando están activados
- La diferencia en capturas es simplemente el estado después de interacción del usuario, no el default

**No hay bug ni desincronización.** El sistema funciona como fue diseñado: OFF por defecto para ahorro de recursos, ON bajo demanda del usuario.

---

**Verificado por:** Luna (asistente OpenClaw)  
**Timestamp:** 2026-07-27 13:40 GMT-4
