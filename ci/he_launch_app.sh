#!/usr/bin/env bash
# Launch the Contoso Traders app on localhost:3000 INSIDE a HyperExecute VM.
# Called from hyperexecute.yaml pre:. Kept as a script (not an inline pre: line)
# so the HE YAML parser never has to handle complex shell with colons/redirects.
set -uo pipefail

APP_DIR="src/ContosoTraders.Ui.Website"
LOG="/tmp/contoso-app.log"

echo "[he_launch_app] ensuring Node.js is available..."
if ! command -v node >/dev/null 2>&1; then
  echo "[he_launch_app] Node not found — installing Node 18 via nvm"
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm install 18
  nvm use 18
fi
node --version

echo "[he_launch_app] installing UI dependencies..."
cd "$APP_DIR" || { echo "[he_launch_app] ERROR: $APP_DIR not found"; exit 1; }
npm install --no-audit --no-fund

echo "[he_launch_app] starting React dev server (background)..."
BROWSER=none nohup npm start > "$LOG" 2>&1 &
disown || true

echo "[he_launch_app] waiting for localhost:3000..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:3000 -o /dev/null; then
    echo "[he_launch_app] app ready after $((i * 3))s"
    exit 0
  fi
  sleep 3
done

echo "[he_launch_app] ERROR: app did not start within 180s"
tail -40 "$LOG" || true
exit 1
