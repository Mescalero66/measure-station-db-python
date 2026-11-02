#!/usr/bin/env bash
# Install or update the Measure Station server's own programs on the server Pi.
#
# Usage (from a checkout at /opt/measure-station-db):
#     sudo bash /opt/measure-station-db/deploy/install.sh
#
# Safe to re-run: every step is idempotent. Run it again after every `git pull`.
# The services it relies on (Mosquitto, PostgreSQL + TimescaleDB, Grafana) are set up
# by hand first: see docs/setting-up-the-server-pi.md.
set -euo pipefail

APP_NAME=measure-station-db
APP_USER=measure-station-db
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$APP_DIR/.venv"
ETC_DIR="/etc/$APP_NAME"
# The Modbus poller is installed and enabled too, but exits at once while
# MODBUS_ENABLED is False in sysconfig.py (docs/modbus-devices.md).
SERVICES=(measure-station-db-ingest measure-station-db-modbus)

log() { printf '\n==> %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root: sudo bash $0" >&2
    exit 1
fi

log "Installing system packages"
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git

log "Creating service user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

log "Creating Python virtual environment at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
# Editable install: edits to src/measure_station_db/sysconfig.py apply on service restart.
"$VENV_DIR/bin/pip" install -e "$APP_DIR"
# Pre-compile bytecode: the services can't write __pycache__ (ProtectSystem=strict).
"$VENV_DIR/bin/python" -m compileall -q "$APP_DIR/src"

log "Installing configuration in $ETC_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$ETC_DIR"
if [[ ! -f "$ETC_DIR/env" ]]; then
    install -m 0640 -o root -g "$APP_USER" "$APP_DIR/deploy/env.example" "$ETC_DIR/env"
fi

log "Installing systemd services"
for service in "${SERVICES[@]}"; do
    sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@APP_USER@#$APP_USER#g" \
        "$APP_DIR/deploy/systemd/$service.service" > "/etc/systemd/system/$service.service"
done
systemctl daemon-reload
for service in "${SERVICES[@]}"; do
    systemctl enable "$service.service"
    # After an update: restart a service that's already running, so it runs the new code.
    systemctl try-restart "$service.service"
done

cat <<EOF

Installation complete.

Next steps (the first time):
  1. Enter the passwords:  sudo nano $ETC_DIR/env
  2. Start the service:    sudo systemctl start measure-station-db-ingest
  3. Check it:             systemctl status measure-station-db-ingest
     Follow its log:       journalctl -u measure-station-db-ingest -f

Modbus devices are off until switched on: see docs/modbus-devices.md.
EOF
