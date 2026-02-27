//+------------------------------------------------------------------+
//|                                           MT5HttpBridge.mq5       |
//|                        Expert Advisor para ejecutar órdenes      |
//|                        Compatible con backend Python MarketTool   |
//|                        MODO: Polling (EA consulta al backend)     |
//+------------------------------------------------------------------+
#property copyright "MarketTool 2026"
#property version   "2.00"
#property strict

// Parámetros del EA
input string BackendURL = "http://170.239.86.106";  // URL del backend Python
input int    PollingInterval = 2000;                      // Intervalo de polling en ms
input int    MagicNumber = 123456;                        // Magic number para identificar órdenes
input bool   EnableLogging = true;                        // Activar logs

// Variables globales
datetime last_poll_time = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== MT5 HTTP Bridge iniciando (Modo Polling) ===");
   Print("Backend URL: ", BackendURL);
   Print("Polling Interval: ", PollingInterval, " ms");
   Print("Magic Number: ", MagicNumber);
   
   // Verificar que WebRequest esté habilitado para el backend
   string allowed_urls = BackendURL + "/";
   
   Print("✅ EA listo para consultar backend cada ", PollingInterval/1000, " segundos");
   Print("⚠️  IMPORTANTE: Agrega '", BackendURL, "' a las URLs permitidas en Tools > Options > Expert Advisors");
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("MT5 HTTP Bridge detenido");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Verificar si es momento de hacer polling (cada PollingInterval ms)
   datetime current_time = TimeLocal();
   
   if(current_time - last_poll_time >= PollingInterval / 1000)
   {
      last_poll_time = current_time;
      CheckForPendingOrders();
   }
}

//+------------------------------------------------------------------+
//| Consultar backend por órdenes pendientes                         |
//+------------------------------------------------------------------+
void CheckForPendingOrders()
{
   string url = BackendURL + "/api/v1/broker/mt5/poll";
   
   // Enviar info de cuenta para que backend sepa que estamos activos
   string account_info = GetAccountInfoJSON();
   
   char post_data[];
   char result_data[];
   string result_headers;
   
   StringToCharArray(account_info, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data) - 1); // Remover null terminator
   
   // Hacer request HTTP POST
   int timeout = 5000; // 5 segundos
   int res = WebRequest(
      "POST",
      url,
      "Content-Type: application/json\r\n",
      timeout,
      post_data,
      result_data,
      result_headers
   );
   
   if(res == -1)
   {
      int error_code = GetLastError();
      if(EnableLogging)
         Print("❌ WebRequest failed. Error: ", error_code, ". Verifica que ", BackendURL, " esté en URLs permitidas.");
      return;
   }
   
   if(res == 200)
   {
      // Parsear respuesta JSON
      string response = CharArrayToString(result_data, 0, WHOLE_ARRAY, CP_UTF8);
      
      if(EnableLogging && StringLen(response) > 10)
         Print("✅ Respuesta del backend: ", StringSubstr(response, 0, 200));
      
      ProcessBackendResponse(response);
   }
   else if(EnableLogging)
   {
      Print("⚠️  Backend retornó código: ", res);
   }
}

//+------------------------------------------------------------------+
//| Procesar respuesta del backend                                   |
//+------------------------------------------------------------------+
void ProcessBackendResponse(string response)
{
   // Extraer si hay orden pendiente
   string has_order = ExtractJSONValue(response, "has_pending_order");
   
   if(has_order == "true")
   {
      if(EnableLogging)
         Print("📋 Orden pendiente detectada, ejecutando...");
      
      // Extraer parámetros de la orden
      string order_data = ExtractJSONObject(response, "order");
      
      if(StringLen(order_data) > 0)
      {
         string result = ExecuteOrder(order_data);
         
         // Reportar resultado al backend
         ReportOrderResult(result);
      }
   }
}

//+------------------------------------------------------------------+
//| Ejecutar orden desde JSON                                        |
//+------------------------------------------------------------------+
string ExecuteOrder(string order_json)
{
   // Parse JSON manualmente (simple parser)
   string symbol = ExtractJSONValue(order_json, "symbol");
   double volume = StringToDouble(ExtractJSONValue(order_json, "volume"));
   string side = ExtractJSONValue(order_json, "side");
   double price = StringToDouble(ExtractJSONValue(order_json, "price"));
   double sl = StringToDouble(ExtractJSONValue(order_json, "sl"));
   double tp = StringToDouble(ExtractJSONValue(order_json, "tp"));
   int deviation = (int)StringToInteger(ExtractJSONValue(order_json, "deviation"));
   string comment = ExtractJSONValue(order_json, "comment");
   string order_id = ExtractJSONValue(order_json, "order_id");
   string lease_id = ExtractJSONValue(order_json, "lease_id");  // Lease ID para anti-duplicados
   
   // Validar símbolo
   if(!SymbolSelect(symbol, true))
   {
      string error_response = StringFormat(
         "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Symbol not found\"}",
         order_id, lease_id
      );
      return error_response;
   }
   
   // Normalizar volumen según especificaciones del símbolo
   volume = NormalizeVolume(symbol, volume);
   
   if(volume <= 0)
   {
      string error_response = StringFormat(
         "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Invalid volume after normalization\"}",
         order_id, lease_id
      );
      return error_response;
   }
   
   // Determinar tipo de orden
   ENUM_ORDER_TYPE order_type;
   if(side == "BUY")
      order_type = ORDER_TYPE_BUY;
   else if(side == "SELL")
      order_type = ORDER_TYPE_SELL;
   else
   {
      string error_response = StringFormat(
         "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Invalid side\"}",
         order_id, lease_id
      );
      return error_response;
   }
   
   // Preparar request
   MqlTradeRequest req;
   MqlTradeResult result;
   
   ZeroMemory(req);
   ZeroMemory(result);
   
   req.action = TRADE_ACTION_DEAL;
   req.symbol = symbol;
   req.volume = volume;
   req.type = order_type;
   req.price = (price > 0) ? price : (order_type == ORDER_TYPE_BUY ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID));
   req.sl = sl;
   req.tp = tp;
   req.deviation = deviation;
   req.magic = MagicNumber;
   req.comment = comment;
   // Usar el filling mode soportado por el simbolo
   ENUM_ORDER_TYPE_FILLING filling = GetSupportedFillingMode(symbol);
   req.type_filling = filling;
   
   // Validar y ajustar SL antes de intentar ejecutar
   double market_price = SymbolInfoDouble(symbol, side == "BUY" ? SYMBOL_ASK : SYMBOL_BID);
   double order_price = market_price;
   bool sl_was_adjusted = AdjustMinimumStopLoss(symbol, order_type, market_price, sl);

   if(EnableLogging)
      Print("📤 Enviando orden: ", symbol, " ", side, " Vol:", volume, " Price:", order_price, " SL:", sl, " TP:", tp);
   
   string response = "";
   int retry_count = 0;
   double temp_volume = volume;
   
   // Intentar con reintentos automáticos para limite de volumen
   while(retry_count < 3)
   {
      ZeroMemory(req);
      ZeroMemory(result);
      
      req.action = TRADE_ACTION_DEAL;
      req.symbol = symbol;
      req.volume = temp_volume;
      req.type = order_type;
      req.price = order_price;
      req.sl = sl;
      req.tp = tp;
      req.deviation = deviation;
      req.magic = MagicNumber;
      req.comment = comment;
      req.type_filling = filling;
      
      bool sent = OrderSend(req, result);
      
      if(result.retcode == TRADE_RETCODE_DONE)
      {
         // Construir respuesta con información de ajuste de SL si fue necesario
         if(sl_was_adjusted)
            response = StringFormat(
               "{\"success\": true, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"mt5_order_id\": %d, \"price\": %.5f, \"volume\": %.2f, \"original_volume\": %.2f, \"sl_adjusted\": true, \"adjusted_sl\": %.5f}",
               order_id, lease_id, result.order, result.price, result.volume, volume, sl
            );
         else
            response = StringFormat(
               "{\"success\": true, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"mt5_order_id\": %d, \"price\": %.5f, \"volume\": %.2f, \"original_volume\": %.2f, \"sl_adjusted\": false}",
               order_id, lease_id, result.order, result.price, result.volume, volume
            );
         Print("✅ Orden ejecutada: ", response);
         return response;
      }
      else if(result.retcode == 10034 && retry_count < 2)  // Volume limit reached
      {
         temp_volume = temp_volume * 0.5;  // Reducir a la mitad
         if(EnableLogging)
            Print("⚠️  Limite de volumen alcanzado. Reintentando con volumen reducido: ", temp_volume);
         retry_count++;
         Sleep(500);  // Esperar 500ms antes de reintentar
         continue;
      }
      else
      {
         if(sl_was_adjusted)
            response = StringFormat(
               "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Order failed\", \"retcode\": %d, \"comment\": \"%s\", \"sl_adjusted\": true, \"adjusted_sl\": %.5f}",
               order_id, lease_id, result.retcode, result.comment, sl
            );
         else
            response = StringFormat(
               "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Order failed\", \"retcode\": %d, \"comment\": \"%s\"}",
               order_id, lease_id, result.retcode, result.comment
            );
         Print("❌ Orden fallida: ", response);
         return response;
      }
   }
   
   // Si llega aqui, agoto todos los reintentos
   if(sl_was_adjusted)
      response = StringFormat(
         "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Order failed after retries\", \"retcode\": 10034, \"comment\": \"Volume limit reached even after reduction\", \"sl_adjusted\": true, \"adjusted_sl\": %.5f}",
         order_id, lease_id, sl
      );
   else
      response = StringFormat(
         "{\"success\": false, \"order_id\": \"%s\", \"lease_id\": \"%s\", \"error\": \"Order failed after retries\", \"retcode\": 10034, \"comment\": \"Volume limit reached even after reduction\"}",
         order_id, lease_id
      );
   Print("❌ Orden fallida después de reintentos: ", response);
   return response;
}

//+------------------------------------------------------------------+
//| Validar y ajustar SL con distancia mínima requerida (Libertex)   |
//+------------------------------------------------------------------+
bool AdjustMinimumStopLoss(string symbol, ENUM_ORDER_TYPE order_type, double price, double &sl)
{
   // Usar la distancia minima del broker (stops/freezes level) y un fallback conservador
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   int stops_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   int freeze_level = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   int min_level = (stops_level > freeze_level) ? stops_level : freeze_level;

   double pip = (digits == 3 || digits == 5) ? point * 10.0 : point;
   double fallback_distance = 3.0 * pip; // 3 pips minimo si el broker no reporta niveles
   double min_distance = MathMax(min_level * point, fallback_distance);

   bool was_adjusted = false;

   if(sl > 0)  // Si SL esta definido
   {
      double sl_distance = MathAbs(price - sl);
      if(sl_distance < min_distance)
      {
         double original_sl = sl;

         // Ajustar SL para que tenga distancia minima
         if(order_type == ORDER_TYPE_BUY)
            sl = price - min_distance;  // Para compra, SL debe estar debajo
         else
            sl = price + min_distance;  // Para venta, SL debe estar arriba

         was_adjusted = true;

         if(EnableLogging)
            Print("⚠️  SL ajustado automaticamente: ", original_sl, " → ", sl,
                  " (min_dist: ", min_distance, ", stops: ", stops_level, ", freeze: ", freeze_level, ")");
      }
   }

   return was_adjusted;
}

//+------------------------------------------------------------------+
//| Obtener filling mode soportado                                  |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetSupportedFillingMode(string symbol)
{
   // SYMBOL_FILLING_MODE devuelve un bitmask de los modos soportados
   int fill_mode = (int)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   
   if(EnableLogging)
      Print("🔍 Filling modes para ", symbol, ": ", fill_mode);
   
   // Verificar cada modo soportado usando bitwise AND
   // SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2, SYMBOL_FILLING_RETURN = 4
   
   if((fill_mode & 1) != 0)  // Soporta FOK
   {
      if(EnableLogging)
         Print("✅ Usando ORDER_FILLING_FOK para ", symbol);
      return ORDER_FILLING_FOK;
   }
   
   if((fill_mode & 2) != 0)  // Soporta IOC
   {
      if(EnableLogging)
         Print("✅ Usando ORDER_FILLING_IOC para ", symbol);
      return ORDER_FILLING_IOC;
   }
   
   if((fill_mode & 4) != 0)  // Soporta RETURN
   {
      if(EnableLogging)
         Print("✅ Usando ORDER_FILLING_RETURN para ", symbol);
      return ORDER_FILLING_RETURN;
   }
   
   // Por defecto, usar RETURN
   if(EnableLogging)
      Print("⚠️  No se detectó filling mode para ", symbol, ". Usando RETURN");
   
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Normalizar volumen según especificaciones del símbolo            |
//+------------------------------------------------------------------+
double NormalizeVolume(string symbol, double volume)
{
   double vol_min = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vol_max = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   if(EnableLogging)
      Print("📊 Volumen original: ", volume, " (Min: ", vol_min, ", Max: ", vol_max, ", Step: ", vol_step, ")");
   
   // Verificar mínimo
   if(volume < vol_min)
   {
      volume = vol_min;
      if(EnableLogging)
         Print("⚠️  Volumen ajustado al mínimo: ", volume);
   }
   
   // Verificar máximo
   if(volume > vol_max)
   {
      volume = vol_max;
      if(EnableLogging)
         Print("⚠️  Volumen ajustado al máximo: ", volume);
   }
   
   // Normalizar según step
   if(vol_step > 0)
   {
      volume = MathRound(volume / vol_step) * vol_step;
      volume = NormalizeDouble(volume, 2); // Normalizar a 2 decimales
   }
   
   if(EnableLogging)
      Print("✅ Volumen normalizado: ", volume);
   
   return volume;
}

//+------------------------------------------------------------------+
//| Reportar resultado al backend                                    |
//+------------------------------------------------------------------+
void ReportOrderResult(string result_json)
{
   string url = BackendURL + "/api/v1/broker/mt5/result";
   
   char post_data[];
   char result_data[];
   string result_headers;
   
   StringToCharArray(result_json, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(post_data, ArraySize(post_data) - 1);
   
   int timeout = 5000;
   int res = WebRequest(
      "POST",
      url,
      "Content-Type: application/json\r\n",
      timeout,
      post_data,
      result_data,
      result_headers
   );
   
   if(res == 200)
   {
      if(EnableLogging)
         Print("✅ Resultado reportado al backend");
   }
   else if(EnableLogging)
   {
      Print("⚠️  No se pudo reportar resultado al backend. Código: ", res);
   }
}

//+------------------------------------------------------------------+
//| Obtener info de cuenta en formato JSON                           |
//+------------------------------------------------------------------+
string GetAccountInfoJSON()
{
   string json = StringFormat(
      "{\"login\": %d, \"balance\": %.2f, \"equity\": %.2f, \"margin\": %.2f, \"free_margin\": %.2f, \"leverage\": %d, \"connected\": %s}",
      AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      AccountInfoInteger(ACCOUNT_LEVERAGE),
      TerminalInfoInteger(TERMINAL_CONNECTED) ? "true" : "false"
   );
   
   return json;
}

//+------------------------------------------------------------------+
//| Extraer valor de JSON (parser simple)                            |
//+------------------------------------------------------------------+
string ExtractJSONValue(string json, string key)
{
   string search_key = "\"" + key + "\"";
   int pos_key = StringFind(json, search_key);
   
   if(pos_key < 0)
      return "";
   
   int pos_colon = StringFind(json, ":", pos_key);
   if(pos_colon < 0)
      return "";
   
   pos_colon++;
   
   // Saltar espacios
   while(pos_colon < StringLen(json) && (StringGetCharacter(json, pos_colon) == ' ' || StringGetCharacter(json, pos_colon) == '\t'))
      pos_colon++;
   
   // Determinar si es string (entre comillas) o número
   if(StringGetCharacter(json, pos_colon) == '\"')
   {
      // Es string
      pos_colon++;
      int pos_end = StringFind(json, "\"", pos_colon);
      if(pos_end < 0)
         return "";
      return StringSubstr(json, pos_colon, pos_end - pos_colon);
   }
   else
   {
      // Es número o booleano
      int pos_end = pos_colon;
      while(pos_end < StringLen(json))
      {
         ushort ch = StringGetCharacter(json, pos_end);
         if(ch == ',' || ch == '}' || ch == ' ' || ch == '\r' || ch == '\n')
            break;
         pos_end++;
      }
      return StringSubstr(json, pos_colon, pos_end - pos_colon);
   }
}

//+------------------------------------------------------------------+
//| Extraer objeto JSON                                              |
//+------------------------------------------------------------------+
string ExtractJSONObject(string json, string key)
{
   string search_key = "\"" + key + "\"";
   int pos_key = StringFind(json, search_key);
   
   if(pos_key < 0)
      return "";
   
   int pos_start = StringFind(json, "{", pos_key);
   if(pos_start < 0)
      return "";
   
   // Contar llaves para encontrar el cierre
   int brace_count = 1;
   int pos = pos_start + 1;
   
   while(pos < StringLen(json) && brace_count > 0)
   {
      ushort ch = StringGetCharacter(json, pos);
      if(ch == '{')
         brace_count++;
      else if(ch == '}')
         brace_count--;
      pos++;
   }
   
   if(brace_count == 0)
      return StringSubstr(json, pos_start, pos - pos_start);
   
   return "";
}

//+------------------------------------------------------------------+
