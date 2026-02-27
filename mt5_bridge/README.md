# MT5 HTTP Bridge con Patrón de Polling

## 📋 Descripción

Expert Advisor (EA) en MQL5 que implementa un **patrón de polling** para ejecutar órdenes de trading desde el backend de MarketTool.

### ✅ ¿Por qué Polling en lugar de Servidor HTTP?

MQL5 **NO soporta** crear servidores TCP (`SocketBind`, `SocketListen`, `SocketAccept` no existen). Los sockets en MQL5 solo funcionan en modo cliente.

Por lo tanto, el patrón implementado es:
1. **EA (MT5)** consulta periódicamente al **backend Python** cada 2 segundos
2. **Backend** responde con órdenes pendientes (si las hay)
3. **EA** ejecuta la orden en MT5
4. **EA** reporta el resultado de vuelta al backend

```
┌──────────────────┐          ┌───────────────────┐          ┌────────────────┐
│  React Native    │          │  Backend Python   │          │  MT5 Terminal  │
│   (Android)      │          │  (170.239.86.106) │          │    + EA        │
└▲─────────────────┘          └▲──────────────────┘          └▲───────────────┘
 │                              │                              │
 │ 1. Place Order              │ 3. Polling (cada 2s)        │
 ├─────────────────────────────>│                             │
 │                              │ 4. ¿Órdenes pendientes?    │
 │                              ├────────────────────────────>│
 │                              │                             │
 │                              │ 5. Sí, órden: {symbol, ...} │
 │                              <──────────────────────────────┤
 │                              │                             │
 │                              │ 6. OrderSend() ejecuta      │
 │                              │                             o
 │                              │ 7. Resultado: {success...}  │
 │ 2. OrderID: uuid            <──────────────────────────────┤
 <──────────────────────────────┤                             │
```

## 🚀 Instalación

### Paso 1: Configurar el EA en MT5

1. Abre **MetaEditor** en MT5 (presiona F4)
2. Crea un nuevo Expert Advisor:
   - File > New > Expert Advisor (template)
   - Nombre: `MT5HttpBridge`
3. Reemplaza el contenido con el código de `MT5HttpBridge.mq5`
4. Presiona **F7** para compilar
5. Verifica que compile sin errores (0 errors)

### Paso 2: Configurar Parámetros del EA

Al arrastrar el EA a un gráfico, configura:

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| `BackendURL` | `http://170.239.86.106:8000` | URL del backend Python |
| `PollingInterval` | `2000` | Intervalo de consulta en ms (2 segundos) |
| `MagicNumber` | `123456` | Magic number para identificar órdenes |
| `EnableLogging` | `true` | Activar logs en Expert |

### Paso 3: Habilitar WebRequest en MT5

**MUY IMPORTANTE**: MT5 requiere whitelist de URLs para WebRequest.

1. En MT5: `Tools` > `Options` > `Expert Advisors`
2. Marca: ✅ `Allow WebRequest for listed URL`
3. Agrega la URL del backend:
   ```
   http://170.239.86.106:8000
   ```
4. Click `OK`

### Paso 4: Adjuntar EA al Gráfico

1. En MT5, abre cualquier gráfico (ej: EURUSD M5)
2. Presiona `Ctrl+N` para abrir Navigator
3. Navega a: `Expert Advisors` > `MT5HttpBridge`
4. Arrastra el EA al gráfico
5. Verifica configuración y click `OK`

### Paso 5: Verificar que el EA está corriendo

Busca en la pestaña **Expert** del terminal:
✅ `=== MT5 HTTP Bridge iniciando (Modo Polling) ===`
✅ `Backend URL: http://170.239.86.106:8000`
✅ `EA listo para consultar backend cada 2 segundos`

También deberías ver un **emoticono** 😊 en la esquina superior derecha del gráfico.

## 📡 Endpoints del Backend

### 1. `/api/v1/broker/mt5/poll` - EA polling endpoint

**Request** (POST desde EA cada 2 segundos):
```json
{
  "login": 500296283,
  "balance": 10000.00,
  "equity": 10050.00,
  "margin": 100.00,
  "free_margin": 9950.00,
  "leverage": 100,
  "connected": true
}
```

**Response** (desde backend):
```json
{
  "has_pending_order": true,
  "order": {
    "order_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "symbol": "EURUSD",
    "volume": 0.1,
    "side": "BUY",
    "price": 1.0950,
    "sl": 1.0900,
    "tp": 1.1000,
    "deviation": 20,
    "comment": "Entry from MarketTool"
  }
}
```

### 2. `/api/v1/broker/mt5/result` - EA result reporting

**Request** (POST desde EA después de ejecutar):

✅ **Éxito**:
```json
{
  "success": true,
  "order_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "mt5_order_id": 123456789,
  "price": 1.09503,
  "volume": 0.1
}
```

❌ **Error**:
```json
{
  "success": false,
  "order_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "error": "Order failed",
  "retcode": 10015,
  "comment": "Invalid stops"
}
```

### 3. `/api/v1/broker/mt5/place-order` - App order placement

**Request** (POST desde React Native):
```json
{
  "symbol": "EURUSD",
  "volume": 0.1,
  "side": "BUY",
  "order_type": "MARKET",
  "entry_price": 1.0950,
  "stop_loss": 1.0900,
  "take_profit": 1.1000,
  "deviation": 20,
  "magic": 0,
  "comment": "Entry from app"
}
```

**Response**:
```json
{
  "status": "success",
  "orderId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "message": "Order queued successfully with ID f47ac10b-..."
}
```

### 4. `/api/v1/broker/mt5/order-status/<order_id>` - Check order status

**Request** (GET):
```
GET /api/v1/broker/mt5/order-status/f47ac10b-58cc-4372-a567-0e02b2c3d479
```

**Response** (pending):
```json
{
  "status": "pending",
  "message": "Waiting for EA to execute (queued 3s ago)",
  "ea_online": true
}
```

**Response** (completed):
```json
{
  "status": "completed",
  "success": true,
  "mt5_order_id": 123456789,
  "price": 1.09503,
  "volume": 0.1
}
```

## 🧪 Testing

### Test 1: Verificar que EA está online

```powershell
# Desde el backend o cualquier máquina
curl -X POST http://170.239.86.106:8000/api/v1/broker/mt5/poll `
  -H "Content-Type: application/json" `
  -d '{}'
```

**Esperado**: `{"has_pending_order": false}`

### Test 2: Crear una orden de prueba

```powershell
curl -X POST http://170.239.86.106:8000/api/v1/broker/mt5/place-order `
  -H "Content-Type: application/json" `
  -d '{
    "symbol": "EURUSD",
    "volume": 0.01,
    "side": "BUY",
    "order_type": "MARKET",
    "entry_price": 0,
    "stop_loss": 0,
    "take_profit": 0,
    "deviation": 20,
    "comment": "Test order"
  }'
```

**Esperado**: 
```json
{
  "status": "success",
  "orderId": "uuid...",
  "message": "Order queued successfully..."
}
```

### Test 3: Verificar que EA ejecutó la orden

Espera 3 segundos (siguiente polling) y verifica en los logs de MT5:
- ✅ `📋 Orden pendiente detectada, ejecutando...`
- ✅ `✅ Orden ejecutada: {"success": true, ...}`

## 🐛 Troubleshooting

### Error: `WebRequest failed. Error: 4060`

**Causa**: URL no está en whitelist de MT5

**Solución**:
1. MT5 > Tools > Options > Expert Advisors
2. Agrega: `http://170.239.86.106:8000`
3. Reinicia el EA (quítalo y vuelve a adjuntarlo al gráfico)

### Error: `EA online: false`

**Causa**: EA no está consultando al backend

**Solución**:
1. Verifica que el EA esté corriendo (debe aparecer 😊 en el gráfico)
2. Verifica que no haya errores en Expert Journal
3. Verifica connectivity: `ping 170.239.86.106` desde el servidor de MT5

### Orden queda en "pending" indefinidamente

**Causa**: EA no pudo ejecutar la orden

**Solución**:
1. Verifica logs en Expert Journal para ver el error específico
2. Verifica que el símbolo exista en MT5: `EURUSD` (no `EUR/USD`)
3. Verifica que el volumen sea válido (mínimo 0.01 para Libertex)
4. Verifica stops: SL/TP deben estar a una distancia mínima del precio

### Órdenes no aparecen en MT5

**Causa**: Múltiples posibles

**Solución**:
1. Verifica que Trade está habilitado: Tools > Options > Expert Advisors > ✅ Allow Algorithmic Trading
2. Verifica que el broker permite trading automatizado
3. Verifica en la pestaña Trade que no haya órdenes rechazadas

## 🔒 Seguridad

### Recomendaciones

1. **Firewall**: El backend NO necesita abrir ningún puerto (el EA inicia las conexiones)
2. **HTTPS**: Para ambiente de producción, usa HTTPS en el backend
3. **Autenticación**: Considera agregar un token de autenticación en los headers
4. **Validación**: El backend valida parámetros (volúmenes, precios, etc.)
5. **Rate limiting**: EA consulta cada 2 segundos (configurable)

### Ejemplo de autenticación (opcional)

Agrega un header personalizado en el EA:

```mql5
// En CheckForPendingOrders()
string headers = "Content-Type: application/json\r\n";
headers += "Authorization: Bearer tu_token_secreto\r\n";

int res = Web Request(
   "POST",
   url,
   headers,  // <- Headers con autenticación
   timeout,
   post_data,
   result_data,
   result_headers
);
```

Y valida en el backend:

```python
@app.route("/api/v1/broker/mt5/poll", methods=["POST"])
def mt5_poll():
    # Verificar token
    auth_header = request.headers.get("Authorization")
    if auth_header != "Bearer tu_token_secreto":
        return jsonify({"error": "Unauthorized"}), 401
    
    # ... resto del código
```

## 📊 Monitoreo

### Logs del EA (MT5 Expert Journal)

```
2026.02.26 10:30:00   MT5HttpBridge (EURUSD,M5)  === MT5 HTTP Bridge iniciando (Modo Polling) ===
2026.02.26 10:30:00   MT5HttpBridge (EURUSD,M5)  Backend URL: http://170.239.86.106:8000
2026.02.26 10:30:00   MT5HttpBridge (EURUSD,M5)  ✅ EA listo para consultar backend cada 2 segundos
2026.02.26 10:30:02   MT5HttpBridge (EURUSD,M5)  ✅ Respuesta del backend: {"has_pending_order": false}
2026.02.26 10:30:35   MT5HttpBridge (EURUSD,M5)  📋 Orden pendiente detectada, ejecutando...
2026.02.26 10:30:35   MT5HttpBridge (EURUSD,M5)  ✅ Orden ejecutada: {"success": true, "order_id": "uuid...", ...}
2026.02.26 10:30:35   MT5HttpBridge (EURUSD,M5)  ✅ Resultado reportado al backend
```

### Logs del Backend (Python)

```
2026-02-26 10:30:02 INFO  📋 Order f47ac10b-... queued for EA execution
2026-02-26 10:30:04 INFO  📤 Sending order f47ac10b-... to EA
2026-02-26 10:30:35 INFO  ✅ Order f47ac10b-... executed successfully (MT5 ID: 123456789)
```

## 🔧 Configuración Avanzada

### Cambiar intervalo de polling

En el EA, ajusta `PollingInterval`:
- `1000` = 1 segundo (más rápido, más carga)
- `2000` = 2 segundos (recomendado)
- `5000` = 5 segundos (más lento, menos carga)

### Múltiples EAs (multi-cuenta)

Puedes correr múltiples instancias del EA en diferentes cuentas, pero cada una debe tener un `MagicNumber` distinto:
- Cuenta 1: `MagicNumber = 123456`
- Cuenta 2: `MagicNumber = 234567`

### Timeout de órdenes

Las órdenes en cola no expiran automáticamente. Para implementar timeout, modifica el backend para revisar el timestamp de `PendingOrder`.

## 📝 Changelog

### v2.0 (2026-02-26)
- ✅ Implementado patrón de polling (EA consulta backend)
- ✅ Backend ahora no necesita exponer puertos
- ✅ Uso de WebRequest en lugar de sockets
- ✅ Mayor compatibilidad con MQL5
- ❌ Removido servidor HTTP (no soportado por MQL5)

### v1.0 (inicial)
- ❌ Intento fallido de crear servidor HTTP en MQL5
- ❌ SocketBind/SocketListen no existen en MQL5

## 📚 Referencias

- [MQL5 WebRequest Documentation](https://www.mql5.com/en/docs/network/webrequest)
- [MQL5 Socket Functions](https://www.mql5.com/en/docs/network)
- [MT5 Order Placement](https://www.mql5.com/en/docs/trading/ordersend)

## 🤝 Support

Para problemas o preguntas:
1. Revisa los logs del EA en MT5 Expert Journal
2. Revisa los logs del backend Python
3. Consulta la sección Troubleshooting
