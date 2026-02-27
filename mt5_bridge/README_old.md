# MT5 HTTP Bridge - Instrucciones de Instalación

Este Expert Advisor (EA) permite que tu backend Python se conecte a MetaTrader 5 vía HTTP, eliminando la necesidad de la librería `MetaTrader5` que solo funciona en Windows.

## 📋 Arquitectura

```
React Native App (Android)
    ↓ HTTP
Backend Python (Linux - 170.239.86.106:8000)
    ↓ HTTP
MT5 HTTP Bridge EA (Windows - 170.239.86.106:8889)
    ↓ MT5 API
MetaTrader 5 Terminal
```

## 🚀 Instalación del EA en MetaTrader 5

### Paso 1: Copiar el archivo MQL5

1. Abre MetaTrader 5
2. Presiona `F4` o ve a **Tools → MetaQuotes Language Editor**
3. En el Editor, ve a **File → Open Data Folder**
4. Navega a la carpeta: `MQL5\Experts\`
5. Copia el archivo `MT5HttpBridge.mq5` en esa carpeta

### Paso 2: Compilar el EA

1. En el MetaEditor, abre `MT5HttpBridge.mq5`
2. Presiona `F7` o ve a **Tools → Compile**
3. Verifica que no haya errores de compilación
4. Se generará el archivo `MT5HttpBridge.ex5`

### Paso 3: Activar el EA en un gráfico

1. En MT5, abre cualquier gráfico (por ejemplo, EURUSD M5)
2. En el **Navigator** (Ctrl+N), ve a **Expert Advisors**
3. Arrastra `MT5HttpBridge` al gráfico
4. En la ventana de configuración:
   - **ServerPort**: `8889` (o el que prefieras)
   - **AllowedIP**: `0.0.0.0` (para permitir todas las IPs) o `170.239.86.106` (solo tu backend)
   - **MagicNumber**: `123456` (para identificar tus órdenes)
   - **EnableLogging**: `true` (para ver logs en el terminal)
5. Marca la casilla **"Allow DLL imports"**
6. Marca la casilla **"Allow WebRequest for listed URL"** y agrega: `http://170.239.86.106`
7. Haz clic en **OK**

### Paso 4: Verificar que el EA esté corriendo

1. Verifica que en la esquina superior derecha del gráfico aparezca una carita sonriente 😊
2. En la pestaña **Experts** del terminal de MT5 (Alt+T), deberías ver:
   ```
   === MT5 HTTP Bridge iniciando ===
   Puerto: 8889
   Magic Number: 123456
   ✅ Servidor HTTP escuchando en puerto 8889
   ```

### Paso 5: Abrir el puerto en el firewall (si es necesario)

Si tu backend está en otra máquina, asegúrate de que el puerto **8889** esté abierto:

#### Windows Firewall:
```powershell
New-NetFirewallRule -DisplayName "MT5 HTTP Bridge" -Direction Inbound -LocalPort 8889 -Protocol TCP -Action Allow
```

## 🧪 Probar la conexión

Desde tu backend Linux o cualquier máquina con acceso a `170.239.86.106`:

```bash
# Verificar estado
curl http://170.239.86.106:8889/status

# Obtener info de cuenta
curl http://170.239.86.106:8889/account_info

# Obtener info de símbolo
curl -X POST http://170.239.86.106:8889/symbol_info -H "Content-Type: application/json" -d '{"symbol":"EURUSD"}'
```

## 📡 Endpoints disponibles

### `GET /status`
Verifica si MT5 está conectado y trading habilitado.

**Response:**
```json
{
  "connected": true,
  "trade_allowed": true
}
```

### `GET /account_info`
Obtiene información de la cuenta MT5.

**Response:**
```json
{
  "login": 500296283,
  "balance": 10000.00,
  "equity": 10050.00,
  "margin": 200.00,
  "free_margin": 9850.00,
  "leverage": 100
}
```

### `POST /symbol_info`
Obtiene información de un símbolo específico.

**Request:**
```json
{
  "symbol": "EURUSD"
}
```

**Response:**
```json
{
  "symbol": "EURUSD",
  "bid": 1.09450,
  "ask": 1.09452,
  "digits": 5,
  "point": 0.00001,
  "spread": 2,
  "volume_min": 0.01,
  "volume_max": 100.00
}
```

### `POST /place_order`
Ejecuta una orden en MT5.

**Request:**
```json
{
  "symbol": "EURUSD",
  "volume": 0.1,
  "side": "BUY",
  "price": 1.0950,
  "sl": 1.0900,
  "tp": 1.1000,
  "deviation": 20,
  "comment": "Order from MarketTool app"
}
```

**Response (éxito):**
```json
{
  "success": true,
  "order_id": 123456789,
  "price": 1.09500,
  "volume": 0.10
}
```

**Response (error):**
```json
{
  "success": false,
  "error": "Order failed",
  "retcode": 10013,
  "comment": "Invalid request"
}
```

## 🔧 Configuración en el Backend Python

El archivo `broker_mt5_service.py` ya está configurado para usar el bridge HTTP:

```python
MT5_BRIDGE_URL = "http://170.239.86.106:8889"
```

Si cambias la IP o puerto, actualiza esta variable.

## ⚠️ Troubleshooting

### El EA no se conecta al puerto
- Verifica que el puerto 8889 no esté siendo usado por otra aplicación
- Asegúrate de que MT5 tenga permisos de red

### "Socket creation failed"
- Ejecuta MT5 como administrador
- Ve a **Tools → Options → Expert Advisors** y marca:
  - ✅ Allow automated trading
  - ✅ Allow DLL imports
  - ✅ Allow WebRequest

### El backend no puede conectar al EA
- Verifica que el firewall permita conexiones entrantes en el puerto 8889
- Prueba con `telnet 170.239.86.106 8889` desde el backend
- Revisa los logs del EA en la pestaña **Experts** de MT5

### Órdenes rechazadas
- Verifica que la cuenta tenga fondos suficientes
- Comprueba que el volumen esté dentro del rango permitido (volume_min - volume_max)
- Asegúrate de que el símbolo exista y esté habilitado para trading

## 📝 Logs

Todos los eventos se registran en:
- **MT5**: Pestaña **Experts** (Alt+T)
- **Backend Python**: Revisa los logs del servicio `broker_mt5_service.py`

## 🔒 Seguridad

**Importante**: Este EA escucha en HTTP sin autenticación. Para producción:

1. **Filtra por IP**: Cambia `AllowedIP` a la IP específica de tu backend
2. **Usa VPN/túnel**: Considera usar un túnel SSH o VPN entre el backend y MT5
3. **Implementa autenticación**: Puedes agregar un token en los headers HTTP

## ✅ Verificación Final

Tu setup está listo cuando:

1. ✅ El EA está corriendo en MT5 (carita sonriente 😊)
2. ✅ Los logs muestran: `✅ Servidor HTTP escuchando en puerto 8889`
3. ✅ `curl http://170.239.86.106:8889/status` retorna `{"connected": true}`
4. ✅ El backend Python puede conectar vía `/api/v1/broker/mt5/connect`
5. ✅ React Native puede crear órdenes desde la app

## 🎯 Próximos Pasos

Una vez instalado el EA:

1. Reinicia el backend Python para que use el nuevo servicio HTTP
2. Abre la app React Native
3. Ve a Configuración → Libertex MT5
4. Ingresa tus credenciales y presiona "Conectar"
5. Crea una orden desde cualquier entrada 🚀

---

**¿Problemas?** Revisa los logs del EA en MT5 y los logs del backend en `/var/log/markettool/`.
