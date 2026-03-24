#!/bin/bash
# ============================================================
# start-markettool-dev.sh — Entorno completo de desarrollo MarketTool
# Shortcut i3: Super+Alt+M
# ============================================================

export ANDROID_HOME=/home/mtoro/Android/Sdk
export PATH=$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH
export DISPLAY=:0
export XAUTHORITY=/home/mtoro/.Xauthority

LOGDIR=/tmp/markettool-dev
mkdir -p "$LOGDIR"

notify() {
  DISPLAY=:0 notify-send "MarketTool Dev" "$1" --icon=terminal 2>/dev/null || true
  echo "[$(date '+%H:%M:%S')] $1"
}

notify "🚀 Iniciando entorno MarketTool..."

# ── 1. Docker daemon ─────────────────────────────────────────
notify "🐳 Verificando Docker..."
# Intentar Docker Desktop primero, caer a dockerd del sistema
if docker context use desktop-linux > /dev/null 2>&1 && docker ps > /dev/null 2>&1; then
  notify "✅ Docker Desktop activo"
elif docker ps > /dev/null 2>&1; then
  notify "✅ Docker daemon activo"
else
  notify "🔄 Iniciando Docker Desktop..."
  systemctl --user start docker-desktop
  for i in $(seq 1 15); do
    docker context use desktop-linux > /dev/null 2>&1 && docker ps > /dev/null 2>&1 && break
    # Fallback: daemon del sistema
    sudo systemctl start docker > /dev/null 2>&1
    docker ps > /dev/null 2>&1 && break
    sleep 3
  done
fi

# ── 2. Levantar backend (maquina-a_test) ─────────────────────
notify "📦 Levantando backend..."
cd /home/mtoro/projects/localnginx_balancer/maquina-a_test
if ! docker compose ps 2>/dev/null | grep -q "healthy\|Up"; then
  docker compose up -d > "$LOGDIR/docker.log" 2>&1
  for i in $(seq 1 12); do
    docker compose ps 2>/dev/null | grep -q "nginx_global.*Up" && break
    sleep 5
  done
fi
notify "✅ Backend + Clínica web listo (nginx)"

# ── 3. Emulador Android ──────────────────────────────────────
notify "📱 Iniciando emulador..."
if ! adb devices 2>/dev/null | grep -q "emulator"; then
  nohup $ANDROID_HOME/emulator/emulator \
    -avd Pixel6_API34 \
    -no-snapshot-save \
    -gpu swiftshader_indirect \
    > "$LOGDIR/emulator.log" 2>&1 &

  # Esperar boot (max 90s)
  for i in $(seq 1 18); do
    result=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    [ "$result" = "1" ] && break
    sleep 5
  done
fi
notify "✅ Emulador listo"

# ── 4. Metro bundler ─────────────────────────────────────────
notify "⚡ Iniciando Metro..."
if ! ps aux | grep -q "[r]eact-native start"; then
  cd /home/mtoro/projects/markettoolapp
  nohup npx react-native start > "$LOGDIR/metro.log" 2>&1 &
  sleep 5
fi

# ── 5. Deploy app al emulador ────────────────────────────────
notify "📲 Desplegando app..."
cd /home/mtoro/projects/markettoolapp
npx react-native run-android --mode=debug > "$LOGDIR/deploy.log" 2>&1 &

# ── 6. Abrir VS Code con los proyectos ──────────────────────
notify "💻 Abriendo VS Code..."
sleep 2
code /home/mtoro/projects/markettool &
sleep 1
code /home/mtoro/projects/clinica-web &
sleep 1
code /home/mtoro/projects/clinica-api &

notify "✅ Entorno MarketTool + Clínica listo!"
