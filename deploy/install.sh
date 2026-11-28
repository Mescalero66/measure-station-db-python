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
# The Modbus poller, the LED display and the music are installed and enabled too, but
# exit at once while MODBUS_ENABLED / DISPLAY_ENABLED / MUSIC_ENABLED are False in
# sysconfig.py (docs/modbus-devices.md, docs/led-display.md, docs/background-music.md).
SERVICES=(
    measure-station-db-ingest
    measure-station-db-modbus
    measure-station-db-display
    measure-station-db-music
)
# Started by a timer, not at boot. The backup stays off while BACKUP_ENABLED is False
# in sysconfig.py (docs/backups.md).
TIMED_SERVICES=(measure-station-db-backup)

log() { printf '\n==> %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root: sudo bash $0" >&2
    exit 1
fi

log "Installing system packages"
apt-get update
# mpg123 plays the background music; alsa-utils has aplay and speaker-test, for checking
# the TV's sound.
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git restic mpg123 alsa-utils

log "Creating service user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
# The LED display's I2C bus, its button's GPIO pin, and the sound for the music
# (Raspberry Pi OS's groups).
for group in i2c gpio audio; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" "$APP_USER"
    fi
done

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

log "Installing the backup command"
# restic's cache for backups run by hand (the service creates it too).
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "/var/cache/$APP_NAME"
sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@APP_USER@#$APP_USER#g" \
    "$APP_DIR/deploy/measure-station-db-backup" > /usr/local/bin/measure-station-db-backup
chmod 0755 /usr/local/bin/measure-station-db-backup

log "Installing systemd services"
install_unit() {
    sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@APP_USER@#$APP_USER#g" \
        "$APP_DIR/deploy/systemd/$1" > "/etc/systemd/system/$1"
}
for service in "${SERVICES[@]}"; do
    install_unit "$service.service"
done
for service in "${TIMED_SERVICES[@]}"; do
    install_unit "$service.service"
    install_unit "$service.timer"
done
systemctl daemon-reload
for service in "${SERVICES[@]}"; do
    systemctl enable "$service.service"
    # After an update: restart a service that's already running, so it runs the new code.
    systemctl try-restart "$service.service"
done
for service in "${TIMED_SERVICES[@]}"; do
    systemctl enable --now "$service.timer"
done

cat <<EOF

Installation complete.

Next steps (the first time):
  1. Enter the passwords:  sudo nano $ETC_DIR/env
  2. Start the service:    sudo systemctl start measure-station-db-ingest
  3. Check it:             systemctl status measure-station-db-ingest
     Follow its log:       journalctl -u measure-station-db-ingest -f

Modbus devices are off until switched on: see docs/modbus-devices.md.
Backups are off until switched on: see docs/backups.md.
The LED display is off until switched on: see docs/led-display.md.
The background music is off until switched on: see docs/background-music.md.
EOF
