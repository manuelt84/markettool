# Phase 7: Bot Handler Integration - Visual Architecture

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           TELEGRAM USER                                  │
│                      (Sends /quote AAPL)                                │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     TELEGRAM BOT LIBRARY                                 │
│                   (telegram.ext.Application)                             │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   HANDLER REGISTRATION LAYER                             │
│              markettool/interfaces/scheduler/bot_init.py                 │
│                                                                          │
│  if container:                                                           │
│    ┌─────────────────────┐     ┌──────────────────────┐               │
│    │  HEXAGONAL HANDLERS │ +   │  LEGACY HANDLERS     │  (MIXED MODE)  │
│    │  (6 commands)       │     │  (~20 commands)      │               │
│    └─────────────────────┘     └──────────────────────┘               │
│  else:                                                                   │
│    └─────────────────────┐                                              │
│      │  LEGACY HANDLERS  │     (Fallback)                               │
│      └───────────────────┘                                              │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
            ┌────────────────┴────────────────┐
            │                                 │
            ▼                                 ▼
┌──────────────────────────┐    ┌───────────────────────────┐
│   HEXAGONAL HANDLERS     │    │   LEGACY HANDLERS         │
│   telegram_handlers.py   │    │   handlers.py             │
│                          │    │                           │
│  • handle_historicos()   │    │  • start()                │
│  • handle_quote()  ◄─────┼────┤  • trader_menu()          │
│  • handle_analisis()     │    │  • analizar_simbolo()     │
│  • handle_calentar()     │    │  • eventos_futuros()      │
│  • handle_ayuda_hex()    │    │  • noticias_user()        │
│  • handle_estado_hex()   │    │  • ... and more           │
└────────────┬─────────────┘    └───────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     COMMAND MAPPER                                       │
│              markettool/interfaces/bot/command_mapper.py                 │
│                                                                          │
│  process_telegram_message(message_text, user_id, chat_id, container)    │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────┐         │
│  │  CommandMapper.execute(CommandContext)                     │         │
│  │                                                             │         │
│  │  Maps command to handler:                                  │         │
│  │  • /historicos  → _handle_get_historicos()                 │         │
│  │  • /quote       → _handle_get_quote()                      │         │
│  │  • /analisis    → _handle_run_analysis()                   │         │
│  │  • /calentar    → _handle_warm_cache()                     │         │
│  │  • /ayuda       → _handle_help()                           │         │
│  │  • /estado      → _handle_status()                         │         │
│  └────────────────────────┬───────────────────────────────────┘         │
└───────────────────────────┼─────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DEPENDENCY INJECTION CONTAINER                       │
│              markettool/interfaces/containers.py                         │
│                                                                          │
│  DIContainer.create_default()                                            │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │  @property def get_quote(self) -> GetQuoteUseCase            │       │
│  │  @property def get_historicos(self) -> GetHistoricosUseCase  │       │
│  │  @property def run_analysis(self) -> RunAnalysisUseCase      │       │
│  │  @property def warm_cache(self) -> WarmCacheUseCase          │       │
│  └──────────────────────────┬───────────────────────────────────┘       │
└────────────────────────────┼────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     APPLICATION LAYER (USE CASES)                        │
│              markettool/application/use_cases/                           │
│                                                                          │
│  GetQuoteUseCase.execute(symbol="AAPL")                                 │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │  async def execute(self, symbol: str) -> Quote:              │       │
│  │      # Orchestration logic                                   │       │
│  │      quote = await self.quote_provider.get_quote(symbol)     │       │
│  │      return quote                                            │       │
│  └──────────────────────────┬───────────────────────────────────┘       │
└────────────────────────────┼────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DOMAIN PORTS (INTERFACES)                            │
│              markettool/core/ports/                                      │
│                                                                          │
│  class QuoteProvider(Protocol):                                          │
│      async def get_quote_by_symbol(symbol: str) -> Quote                │
│                                                                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     INFRASTRUCTURE ADAPTERS                              │
│              markettool/infrastructure/adapters/                         │
│                                                                          │
│  FMPQuoteProvider.get_quote_by_symbol("AAPL")                           │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │  1. Check MultiLayerCacheProvider                            │       │
│  │     ├─ Memory Cache (sub-second)                             │       │
│  │     ├─ Redis Cache (milliseconds)                            │       │
│  │     └─ Firestore Cache (100-300ms)                           │       │
│  │                                                               │       │
│  │  2. If not cached → Fetch from FMP API                       │       │
│  │     └─ https://financialmodelingprep.com/api/v3/quote/AAPL   │       │
│  │                                                               │       │
│  │  3. Save to all cache layers                                 │       │
│  │                                                               │       │
│  │  4. Return Quote model                                       │       │
│  └──────────────────────────┬───────────────────────────────────┘       │
└────────────────────────────┼────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DOMAIN MODELS                                        │
│              markettool/core/models/                                     │
│                                                                          │
│  @dataclass                                                              │
│  class Quote:                                                            │
│      symbol: str                                                         │
│      price: float                                                        │
│      change_pct: float                                                   │
│      timestamp: datetime                                                 │
│      bid: Optional[float]                                                │
│      ask: Optional[float]                                                │
│                                                                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼ (Return path)
┌─────────────────────────────────────────────────────────────────────────┐
│                     RESPONSE FORMATTING                                  │
│              command_mapper.py → _handle_get_quote()                     │
│                                                                          │
│  response = (                                                            │
│      f"💹 <b>{quote.symbol}</b>\n"                                       │
│      f"💰 Price: <code>${quote.price:.4f}</code>\n"                      │
│      f"📈 Change: {quote.change_pct:.2%}\n"                              │
│      f"🕐 {quote.timestamp.strftime('%H:%M:%S UTC')}"                    │
│  )                                                                       │
│                                                                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     TELEGRAM RESPONSE                                    │
│              telegram_handlers.py → handle_quote()                       │
│                                                                          │
│  await context.bot.send_message(                                         │
│      chat_id=chat_id,                                                    │
│      text=response,                                                      │
│      parse_mode=ParseMode.HTML                                           │
│  )                                                                       │
│                                                                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           TELEGRAM USER                                  │
│                                                                          │
│         Receives:                                                        │
│         💹 AAPL                                                          │
│         💰 Price: $150.2500                                              │
│         📈 Change: 2.50%                                                 │
│         🕐 12:00:00 UTC                                                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Handler Registration Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     BOOTSTRAP (bootstrap.py)                             │
│                                                                          │
│  1. container = DIContainer.create_default()                             │
│  2. application = Application.builder().token(BOT_TOKEN).build()         │
│  3. await initialize_bot_async(application, container=container, ...)    │
│                                                                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     BOT_INIT (bot_init.py)                               │
│                                                                          │
│  if container:    # ← Container detected (hexagonal mode)                │
│      ┌──────────────────────────────────────────────────────┐           │
│      │ Mode: MIXED (default)                                │           │
│      │                                                       │           │
│      │ Step 1: Register Hexagonal Handlers                  │           │
│      │ ────────────────────────────────────────             │           │
│      │ await register_handlers_with_app(                    │           │
│      │     application,                                     │           │
│      │     container,                                       │           │
│      │     mode="mixed"                                     │           │
│      │ )                                                    │           │
│      │                                                       │           │
│      │ Registers:                                           │           │
│      │   CommandHandler("historicos", handle_historicos)    │           │
│      │   CommandHandler("quote", handle_quote)              │           │
│      │   CommandHandler("analisis", handle_analisis)        │           │
│      │   CommandHandler("calentar", handle_calentar)        │           │
│      │   CommandHandler("ayuda_hex", handle_ayuda_hex)      │           │
│      │   CommandHandler("estado_hex", handle_estado_hex)    │           │
│      │   ErrorHandler(handle_error)                         │           │
│      │                                                       │           │
│      │ Step 2: Register Legacy Handlers                     │           │
│      │ ───────────────────────────────────                  │           │
│      │ register_bot_handlers(application, logger)           │           │
│      │                                                       │           │
│      │ Registers:                                           │           │
│      │   CommandHandler("start", start)                     │           │
│      │   CommandHandler("trader_menu", trader_menu)         │           │
│      │   CommandHandler("analizar_simbolo", ...)            │           │
│      │   ... (~20 more legacy commands)                     │           │
│      └──────────────────────────────────────────────────────┘           │
│                                                                          │
│  else:           # ← No container (legacy-only mode)                     │
│      ┌──────────────────────────────────────────────────────┐           │
│      │ fallback to legacy handlers only                     │           │
│      │ register_bot_handlers(application, logger)           │           │
│      └──────────────────────────────────────────────────────┘           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Mode Comparison

### MIXED Mode (Current) ⭐

```
Application Handlers:
├─ Hexagonal (6 commands)
│  ├─ /historicos  → container.get_historicos
│  ├─ /quote       → container.get_quote
│  ├─ /analisis    → container.run_analysis
│  ├─ /calentar    → container.warm_cache
│  ├─ /ayuda_hex   → help
│  └─ /estado_hex  → status
│
└─ Legacy (~20 commands)
   ├─ /start
   ├─ /trader_menu
   ├─ /analizar_simbolo
   ├─ /eventos_futuros
   ├─ /noticias_user
   └─ ... etc.
```

**Benefits**:
- ✅ Both systems work simultaneously
- ✅ No breaking changes
- ✅ Gradual migration path
- ✅ Safe rollback

---

### FULL Mode

```
Application Handlers:
└─ Hexagonal (8 commands)
   ├─ /start         → hexagonal start
   ├─ /historicos    → container.get_historicos
   ├─ /quote         → container.get_quote
   ├─ /analisis      → container.run_analysis
   ├─ /calentar      → container.warm_cache
   ├─ /ayuda_hex     → help
   ├─ /estado_hex    → status
   └─ [message_handler] → CommandMapper routing
```

**Benefits**:
- ✅ Complete hexagonal architecture
- ✅ No legacy dependencies
- ✅ Cleaner codebase
- ⚠️ Breaks legacy commands

---

### COMMANDS_ONLY Mode

```
Application Handlers:
├─ Hexagonal (6 commands)
│  ├─ /historicos  → container.get_historicos
│  ├─ /quote       → container.get_quote
│  ├─ /analisis    → container.run_analysis
│  ├─ /calentar    → container.warm_cache
│  ├─ /ayuda_hex   → help
│  └─ /estado_hex  → status
│
└─ Legacy (~20 commands)
   └─ ... all legacy commands
```

**Benefits**:
- ✅ Minimal hexagonal footprint
- ✅ Legacy handles /start, message handler, etc.
- ✅ Focused on specific commands

---

## Data Flow Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 1: PRESENTATION (Telegram Bot)                                │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ • User Input (/quote AAPL)                                      │ │
│ │ • Response Formatting (HTML)                                    │ │
│ │ • Error Display                                                 │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 2: INTERFACE (Telegram Handlers)                              │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ • telegram_handlers.py                                          │ │
│ │ • command_mapper.py                                             │ │
│ │ • CommandContext creation                                       │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 3: APPLICATION (Use Cases)                                    │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ • GetQuoteUseCase                                               │ │
│ │ • GetHistoricosUseCase                                          │ │
│ │ • RunAnalysisUseCase                                            │ │
│ │ • WarmCacheUseCase                                              │ │
│ │ • Business Logic Orchestration                                  │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 4: DOMAIN (Core Business Logic)                               │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ • Quote, Historico, Signal models                               │ │
│ │ • Ports (interfaces)                                            │ │
│ │ • Domain Errors                                                 │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 5: INFRASTRUCTURE (External Systems)                          │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ • FMPQuoteProvider (FMP API)                                    │ │
│ │ • FirestoreHistoricosRepository                                 │ │
│ │ • MultiLayerCacheProvider (Memory/Redis/Firestore)              │ │
│ │ • TelegramNotifier                                              │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Dependency Flow

```
Telegram Bot
    ↓
telegram_handlers.py
    ↓ (depends on)
command_mapper.py
    ↓ (depends on)
DIContainer
    ↓ (provides)
Use Cases (Application Layer)
    ↓ (depends on)
Ports (Domain Interfaces)
    ↑ (implemented by)
Adapters (Infrastructure)
```

**Key Principle**: Dependencies point INWARD (toward domain)
- Infrastructure depends on Domain (not vice versa)
- Application depends on Domain (not vice versa)
- Interfaces depend on Application & Domain

---

## Error Handling Flow

```
Exception in Adapter (e.g., FMP API timeout)
    ↓
Caught in Use Case
    ↓
Wrapped in DomainError (QuoteNotFoundError)
    ↓
Propagated to CommandMapper
    ↓
Caught in _handle_get_quote()
    ↓
Formatted as user-friendly message
    ↓
Returned to handle_quote()
    ↓
Sent to Telegram chat
    ↓
User sees: "❌ Error fetching quote: API timeout"
```

---

## Logging Flow

```
[Hexagonal] MIXED mode - hexagonal commands coexist with legacy
    ↓
[Hexagonal] Handlers hexagonales registrados en modo MIXED
    ↓
[Legacy] Handlers legacy registrados para compatibilidad
    ↓
[Hexagonal] Bot handlers registered successfully
    ↓
User sends: /quote AAPL
    ↓
[CommandMapper] Executing command: /quote with args: ['AAPL']
    ↓
[GetQuoteUseCase] Fetching quote for symbol: AAPL
    ↓
[FMPQuoteProvider] Cache HIT for AAPL (Memory layer)
    ↓
[CommandMapper] Command executed successfully
    ↓
User receives formatted quote
```

---

This visual architecture document shows the complete flow from user input to response, including all layers of the hexagonal architecture and how MIXED mode integrates both systems.
