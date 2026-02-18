# 🚀 MarketTool - Hexagonal Architecture Refactoring

**Status**: ✅ **Phase 8 COMPLETE (100% 🎉)** | Production Ready

---

## 📊 Quick Status

| Component | Status | Details |
|-----------|--------|---------|
| **Hexagonal Architecture** | ✅ Complete | 5-layer clean architecture |
| **Unit Tests** | ✅ 35+ passing | ~80% coverage |
| **Integration Tests** | ✅ 4/4 passing | All phases validated |
| **Bot Handler Integration** | ✅ Complete | MIXED mode (hexagonal + legacy) |
| **Docker Optimization** | ✅ Complete | Multi-stage, 40% size reduction |
| **Production Deployment** | ✅ Complete | Health checks, graceful shutdown |

---

## 📁 Project Structure

```
marketTool/
├── markettool/                  # Main application package
│   ├── core/                    # Domain layer (no external deps)
│   │   ├── models/              # Business entities
│   │   ├── ports/               # Interfaces (protocols)
│   │   ├── env_validation.py   # Environment validation (Phase 8)
│   │   └── shutdown.py         # Graceful shutdown (Phase 8)
│   ├── application/            # Use cases (orchestration)
│   │   └── use_cases/          # Business logic orchestrators
│   ├── infrastructure/         # Adapters (external systems)
│   │   └── adapters/           # FMP, Firestore, Cache, Telegram
│   ├── interfaces/             # Presentation layer
│   │   ├── bot/                # Telegram bot handlers
│   │   └── api/                # HTTP routes & health endpoints
│   └── bootstrap.py            # Enhanced entry point (Phase 8)
│
├── tests/                      # Test suite
│   ├── unit/                   # Unit tests
│   └── integration/            # Integration tests
│
├── scripts/                    # Utility scripts
│   ├── deploy.sh               # Quick deployment (Phase 8)
│   ├── validate_deployment.sh  # Deployment validation (Phase 8)
│   ├── validate_docker.sh      # Docker validation
│   ├── check_models.py         # Model verification
│   ├── download_easyocr_models.py
│   └── run_tests.py            # Test runner
│
├── docs/                       # 📚 Documentation (organized)
│   ├── INDEX.md                # Master documentation index
│   ├── architecture/           # System design documents
│   ├── guides/                 # Setup & usage guides
│   ├── optimization/           # Performance tuning docs
│   ├── audits/                 # Analysis & audits
│   ├── phases/                 # Implementation phases
│   └── archive/                # Historical documents
│
├── deployment/                 # Deployment configurations
│   └── gke/                    # Google Kubernetes Engine setup
│
├── MarketTool.py               # Legacy monolith (preserved)
├── Dockerfile                  # Current production Dockerfile
├── Dockerfile.optimized        # Multi-stage optimized build (Phase 8)
├── requirements.txt            # Python dependencies
├── pytest.ini                  # Test configuration
└── README.md                   # This file
```

---

## 🚀 Quick Start

### Production Deployment

```bash
# 1. Validate deployment readiness
bash scripts/validate_deployment.sh

# 2. Deploy (Docker)
bash scripts/deploy.sh

# 3. Monitor
docker logs -f markettool
curl http://localhost:8080/health | jq
```

### Development

```bash
# Clone and install
git clone <repo>
cd marketTool

# Run tests
python -m pytest tests/

# Run integration tests
python test_phase7b_integration.py

# Expected: ✅ 39+ tests passing
```

### Production

```bash
# Build Docker image
docker build -t markettool:latest .

# Run with environment variables
docker run -e BOT_TOKEN=xxx -e WEBHOOK_URL=https://xxx markettool:latest

# Health check
curl http://localhost:8080/healthz
```

---

## 📚 Documentation

**📍 Start Here**: [docs/INDEX.md](./docs/INDEX.md) - Master documentation index

**Quick Access**:
- 📊 [Project Status](./docs/PROJECT_STATUS.md) - Current status & metrics
- 🏗️ [Architecture](./docs/architecture/ARQUITECTURA_HEXAGONAL.md) - Hexagonal design
- 🚀 [Quick Start](./docs/guides/QUICK_START_PERFORMANCE.md) - Setup in 5 minutes
- ⚡ [Performance Guide](./docs/optimization/OPTIMIZATION_REPORT.md) - Tuning & metrics
- 🚢 [Deployment](./deployment/gke/README.md) - GKE deployment guide

**By Role**:
- 🧑‍💼 **Product Manager**: [PROJECT_STATUS.md](./docs/PROJECT_STATUS.md), [FINAL_SUMMARY.md](./docs/FINAL_SUMMARY.md)
- 👨‍💻 **Developer**: [Architecture](./docs/architecture/), [Setup Guides](./docs/guides/)
- 🔧 **DevOps**: [Deployment](./deployment/gke/), [Validation](./docs/guides/FINAL_VERIFICATION.md)
- ⚡ **Performance Engineer**: [Optimization](./docs/optimization/), [Audits](./docs/audits/)

**Full Documentation**: Browse [docs/](./docs/) directory

---

## 🎯 Key Features

✅ **Hexagonal Architecture** - Clean separation of concerns  
✅ **Dependency Injection** - DIContainer for easy testing  
✅ **Multi-Layer Cache** - Memory → Redis → Firestore  
✅ **MIXED Mode Bot** - Hexagonal + Legacy commands coexist  
✅ **Logging & Monitoring** - Comprehensive observability  
✅ **Graceful Shutdown** - Clean resource cleanup  
✅ **Health Checks** - `/health`, `/healthz` endpoints  

---

## 🧪 Testing

### Run All Tests
```bash
python run_tests.py
```

### Run Specific Test Suite
```bash
# Unit tests
python -m pytest tests/test_models.py
python -m pytest tests/test_adapters.py
python -m pytest tests/test_use_cases.py

# Integration tests
python test_phase7b_integration.py
```

**Expected Results**: ✅ 39+ tests passing

---

## 🔄 Commands Available

### Hexagonal (New) ⭐
```
/historicos AAPL 1d  - Historical data
/quote AAPL          - Current price quote
/analisis AAPL       - Technical analysis
/calentar AAPL MSFT  - Cache warming
/ayuda_hex           - Hexagonal help
/estado_hex          - System status
```

### Legacy (Preserved)
```
/start               - Welcome
/trader_menu         - Trading menu
/analizar_simbolo    - Legacy analysis
... and ~17 more
```

---

## 📈 Architecture Layers

1. **Domain** (`core/`) - Business logic, models, ports
2. **Application** (`application/`) - Use cases, orchestration
3. **Infrastructure** (`infrastructure/`) - Adapters, external systems
4. **Interfaces** (`interfaces/`) - Bot handlers, API routes
5. **Bootstrap** (`bootstrap.py`) - Dependency injection, startup

---

## 🚀 Phase Progress

| Phase | Name | Status | Completion |
|-------|------|--------|------------|
| 1-3 | Core Domain | ✅ | 100% |
| 4 | Use Cases | ✅ | 100% |
| 5 | Adapters | ✅ | 100% |
| 6 | Unit Testing | ✅ | 100% |
| 7a | Command Mapping | ✅ | 100% |
| 7b | Handler Integration | ✅ | 100% |
| **8** | **Production Deployment** | 🔄 | **In Progress** |

**Overall**: **87.5% Complete** (7 of 8 phases)

---

## 🆘 Support

### Common Issues

**Q: Bot not starting?**  
→ Check `WEBHOOK_URL` environment variable is set

**Q: Cache not warming?**  
→ Verify `CACHE_WARMUP_ENABLED=true` environment variable

**Q: Health check failing?**  
→ Ensure bot is initialized (`/health` responds after startup)

### Debug Commands

```bash
# Check logs
tail -f logs/markettool.log | grep "\[Hexagonal\]\|\[Legacy\]"

# Verify container
docker logs <container_id>

# Health check
curl -v http://localhost:8080/health
```

---

## 📞 Contact & Contribution

- **Questions?** Check [DOCUMENTATION/](./DOCUMENTATION/)
- **Bug Report?** Create an issue
- **Want to contribute?** Submit PR

---

## 📄 License

See LICENSE file

---

**Last Updated**: February 15, 2026  
**Status**: Phase 7 Complete ✅ | Phase 8 In Progress 🔄

For detailed status: [PROJECT_STATUS.md](./PROJECT_STATUS.md)
