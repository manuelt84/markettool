# Fix Backend Live - Deploy Instructions

## Problema Diagnosticado
- Rama `prod-no-live` no tenía `live_entries_routes.py` → endpoints 404
- `ENABLE_WORKING_LIVE=false` → live-candle endpoint deshabilitado
- Live-entries nunca se registraban en route_factory.py

## Solución Aplicada
✅ Merge `master` → `prod-no-live` completado
✅ `ENABLE_WORKING_LIVE=true` agregado al .env
✅ `live_entries_routes.py` ahora se registra en route_factory.py

## Comandos para Ejecutar en Máquina A (170.239.86.106)

```bash
# 1. Pull del código actualizado
cd /home/mtoro/projects/markettool
git pull origin prod-no-live

# 2. Rebuild de la imagen Docker (usa capas cacheadas, ~2-5 min)
bash build-image.sh --force

# 3. Restart del contenedor app1
cd /home/mtoro/projects/localnginx_balancer/maquina-a_test
sudo docker compose restart app1

# 4. Verificar logs
sudo docker logs app1 --tail 50 | grep -E "live|Live|LIVE|route"

# 5. Testear endpoints
curl -s http://localhost:8101/monitoreo/live-candle?symbol=DOTUSD&timeframe=1min | jq
curl -s http://localhost:8101/monitoreo/live-entries?exec_id=test&symbol=DOTUSD&tfs=1min | jq
```

## Variables de Entorno Clave
- `ENABLE_WORKING_LIVE=true` ← live-candle endpoint habilitado
- `ENABLE_BROKER_EXECUTION=false` ← broker MT5 deshabilitado (seguridad)
- `MARKET_POOL_ENABLED=true` ← market pool activo

## Ramas Git
```
master              → Latest con todas las features
prod-no-live*       → Prod actualizada (merge completado)
restore/before-rollback-20260717-204350 → Backup
```

## Commits Relevantes
- `245a2d2` merge: bring live_entries_routes and master improvements into prod-no-live
- `5dea3bd` config: enable ENABLE_WORKING_LIVE=true for live-candle endpoint
