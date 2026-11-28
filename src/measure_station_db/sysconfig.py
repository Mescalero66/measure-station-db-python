"""Measure Station server configuration.

Every tunable setting for the server's programs lives in this file; the rest of the
package reads from here and never hard-codes values. After editing, restart the
service that uses it.

Secrets (e.g. passwords) are read from ``MEASURE_STATION_DB_*`` environment variables so
they stay out of version control.

Note: always import this as ``from measure_station_db import sysconfig``. Never put this
file's directory on ``sys.path`` directly, or it will shadow the standard library's
``sysconfig`` module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _env(name: str, default: str | None = None) -> str | None:
    """Read an optional ``MEASURE_STATION_DB_<name>`` environment override."""
    return os.environ.get(f"MEASURE_STATION_DB_{name}", default)


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
# Logs go to stdout, which systemd captures in the journal (journalctl -u <service>).
LOG_LEVEL: str = _env("LOG_LEVEL") or "INFO"

# --------------------------------------------------------------------------- #
# MQTT broker (Mosquitto, on this Pi; docs/setting-up-the-server-pi.md, Step 6)
# --------------------------------------------------------------------------- #
MQTT_HOST: str = _env("MQTT_HOST") or "localhost"
MQTT_PORT: int = int(_env("MQTT_PORT") or 1883)
# The ingest service's broker login. The watch tool uses it too, with its own client ID.
MQTT_USERNAME: str = _env("MQTT_USERNAME") or "measure-station-db"
MQTT_PASSWORD: str | None = _env("MQTT_PASSWORD")
# The stations' base topic (their MQTT_BASE_TOPIC). Every station uses this default.
MQTT_BASE_TOPIC: str = "measure-station"
# The ingest service's identity. The broker keeps messages for it while it's stopped
# (a persistent session), so it must never change, and nothing else may use it.
MQTT_CLIENT_ID: str = "measure-station-db"

# --------------------------------------------------------------------------- #
# Database (PostgreSQL + TimescaleDB, on this Pi; docs/setting-up-the-server-pi.md,
# Step 7)
# --------------------------------------------------------------------------- #
DATABASE_HOST: str = _env("DATABASE_HOST") or "localhost"
DATABASE_PORT: int = int(_env("DATABASE_PORT") or 5432)
DATABASE_NAME: str = _env("DATABASE_NAME") or "measure_station_db"
DATABASE_USER: str = _env("DATABASE_USER") or "ingest"
DATABASE_PASSWORD: str | None = _env("DATABASE_PASSWORD")

# --------------------------------------------------------------------------- #
# Ingest service
# --------------------------------------------------------------------------- #
# Messages are saved to the database in batches: when this many have arrived, or when
# the oldest has waited INGEST_BATCH_WAIT_S. Keep it below the broker's
# max_inflight_messages (1000): the broker sends no more until they're acknowledged.
INGEST_BATCH_MAX: int = 500
INGEST_BATCH_WAIT_S: float = 1.0
# The hourly and daily averages refresh themselves only for the last few days (see
# schema.py). For readings older than this, the ingest service recalculates their days
# itself, at most every INGEST_REFRESH_INTERVAL_S while late readings keep arriving.
INGEST_LATE_AFTER_S: int = 2 * 86400
INGEST_REFRESH_INTERVAL_S: int = 300
# How long to wait before trying again when the database can't be reached.
INGEST_RETRY_S: int = 10
# How often to log how much was stored.
INGEST_STATS_INTERVAL_S: int = 60

# --------------------------------------------------------------------------- #
# Grafana dashboards (docs/saving-dashboards.md)
# --------------------------------------------------------------------------- #
# Dashboards are designed in Grafana, in the browser. "python -m
# measure_station_db.dashboards save" keeps copies of them in this repo, and "restore"
# puts the copies back (e.g. after rebuilding the Pi).
# Grafana's address: the server's hostname (from the setup guide's Step 2) plus ".local",
# then ":3000". It works from the PC and from the Pi itself.
GRAFANA_URL: str = _env("GRAFANA_URL") or "http://mstn-db.local:3000"
# The login for "restore" ("save" needs none: anyone on the LAN may view dashboards).
GRAFANA_USERNAME: str = _env("GRAFANA_USERNAME") or "admin"
GRAFANA_PASSWORD: str | None = _env("GRAFANA_PASSWORD")
# Where the copies are kept: deploy/grafana/ in this repo.
GRAFANA_COPIES_DIR: Path = Path(__file__).resolve().parents[2] / "deploy" / "grafana"

# --------------------------------------------------------------------------- #
# Backups (docs/backups.md)
# --------------------------------------------------------------------------- #
# Every night the whole database is exported (pg_dump) into a restic repository: a
# folder of encrypted backup files, here on the NAS. restic stores only what changed,
# so many copies take little more space than one.
# Off by default. To switch it on, follow docs/backups.md, then set this to True.
BACKUP_ENABLED: bool = False
# Where the copies go. "sftp:<user>@<NAS address>:/<shared folder>/<folder>" for the NAS,
# or a plain folder path (e.g. a USB drive).
BACKUP_REPOSITORY: str = "sftp:measure-station-db@192.168.1.30:/backups/measure-station-db"
# The repository's password: it encrypts the copies. Lose it and they can't be read.
BACKUP_PASSWORD: str | None = _env("BACKUP_PASSWORD")
# The Pi's login key for the NAS, and the NAS's own key (checked at every connection).
BACKUP_SSH_KEY: str = "/etc/measure-station-db/backup-key"
BACKUP_KNOWN_HOSTS: str = "/etc/measure-station-db/backup-known-hosts"
# restic's local index cache (speeds up each run; safe to delete).
BACKUP_CACHE_DIR: str = "/var/cache/measure-station-db/restic"
# Which copies to keep: the last N days, plus one a week and one a month for longer.
BACKUP_KEEP_DAILY: int = 14
BACKUP_KEEP_WEEKLY: int = 8
BACKUP_KEEP_MONTHLY: int = 24
# One night a week (0 = Monday ... 6 = Sunday), also check that the copies can be read
# back, reading this share of the stored data (a different part each time).
BACKUP_CHECK_WEEKDAY: int = 6
BACKUP_CHECK_READ: str = "10%"

# --------------------------------------------------------------------------- #
# LED display and button (docs/led-display.md)
# --------------------------------------------------------------------------- #
# An 8-digit 7-segment display (DFRobot DFR0646) shows one value at a time; each press
# of the button (DFRobot DFR0029) moves to the next value in DISPLAY_ITEMS.
# Off by default. To switch it on, wire it up (docs/led-display.md), then set this to
# True and run: sudo systemctl restart measure-station-db-display
DISPLAY_ENABLED: bool = False
# The display: the Pi's I2C bus 1 (pins 3 and 5), at the DFR0646's default address.
DISPLAY_I2C_BUS: int = 1
DISPLAY_I2C_ADDRESS: int = 0x70
# 0 (dim, not off) to 15 (brightest).
DISPLAY_BRIGHTNESS: int = 8
# True if the display is mounted upside down.
DISPLAY_ROTATED: bool = False
# The button: its GPIO number (not the pin number: GPIO 17 is pin 11), and whether its
# signal goes high (True, as the DFR0029 does) or low when pressed.
DISPLAY_BUTTON_GPIO_CHIP: str = "/dev/gpiochip0"
DISPLAY_BUTTON_GPIO: int = 17
DISPLAY_BUTTON_PRESSED_HIGH: bool = True
# After a press, the value's label is shown on its own for this long.
DISPLAY_LABEL_S: float = 1.5
# How often the shown value is read again from the database.
DISPLAY_REFRESH_S: float = 10
# A value older than this shows as "----" (the sensor has stopped sending).
DISPLAY_STALE_S: float = 300

# The station whose values are shown (unless an item names another).
DISPLAY_STATION: str = "mstn-2b"
# The values, in button order. Options:
#   label     Shown after a press, and in front of the value when both fit in 8 digits.
#             Letters are approximations on 7 segments (e.g. "t", "rh", "co2", "Pm2.5").
#   source    The station's source (sensor), as in the database.
#   key       The value's name, as in the database (docs/viewing-data-in-grafana.md,
#             Step 2, lists them).
#   decimals  Optional: digits after the decimal point. Default 1.
#   section   Optional: "values" (the default) or "meta".
#   station   Optional: another station than DISPLAY_STATION.
DISPLAY_ITEMS: list[dict[str, Any]] = [
    {"label": "co2", "source": "co2", "key": "co2_ppm", "decimals": 0},
    {"label": "t", "source": "temperature", "key": "temperature_c", "decimals": 1},
    {"label": "rh", "source": "humidity", "key": "humidity_pct", "decimals": 0},
    {"label": "P", "source": "barometer", "key": "pressure_hpa", "decimals": 1},
    {"label": "Pm2.5", "source": "particulates", "key": "pm25_ugm3", "decimals": 1},
    {"label": "LUX", "source": "light", "key": "illuminance_lux", "decimals": 0},
]

# --------------------------------------------------------------------------- #
# Background music (docs/background-music.md)
# --------------------------------------------------------------------------- #
# Plays a folder of MP3s through the TV's speakers (over HDMI): one track per hour, in
# clock order from 12 am to 11 pm and round again, with each hour's weather picked at
# random. The hours are only names: they don't follow the real time of day. The files are
# named like "03 pm (Rain).mp3"; other names are ignored.
# Off by default. To switch it on, copy the music to the Pi (docs/background-music.md),
# then set this to True and run: sudo systemctl restart measure-station-db-music
MUSIC_ENABLED: bool = True
# The music folder (folders inside it count too).
MUSIC_DIR: str = "/srv/music"
# Each weather's chance of being picked for the next hour, as a share of the total (these
# add up to 100, but needn't). 0 means never. If an hour has no file for a weather, that
# weather's share goes to the others.
MUSIC_WEATHER_CHANCES: dict[str, float] = {"Sunny": 60, "Rain": 30, "Snow": 10}
# Where the sound goes: the TV, on the Pi's HDMI 0 port (the one next to the power socket).
MUSIC_AUDIO_DEVICE: str = "hdmi:CARD=vc4hdmi0,DEV=0"
# The track being played is noted in this file, so that after a restart it carries on.
MUSIC_BOOKMARK_FILE: str = "/var/lib/measure-station-db-music/bookmark"
# How long to wait before trying a track again when it can't be played (e.g. the TV's
# sound isn't available).
MUSIC_RETRY_S: float = 10

# --------------------------------------------------------------------------- #
# Modbus TCP devices (docs/modbus-devices.md)
# --------------------------------------------------------------------------- #
# The Modbus poller reads each enabled device's registers on a timer, and publishes them
# to the broker as an ordinary station message: each device is a "virtual station".
# Off by default. To switch it on: set this to True, enable a device below, then
# sudo systemctl restart measure-station-db-modbus
MODBUS_ENABLED: bool = False
# The poller's broker login. It may publish for any station (docs/modbus-devices.md).
MODBUS_MQTT_USERNAME: str = _env("MODBUS_MQTT_USERNAME") or "measure-station-db-modbus"
MODBUS_MQTT_PASSWORD: str | None = _env("MODBUS_MQTT_PASSWORD")
# Readings held in memory while the broker can't be reached (sent when it's back).
MODBUS_QUEUE_MAX: int = 10_000

# One entry per device. Device options:
#   enabled      True to read it.
#   station      Its station ID in the database (letters, digits, '.', '_', '-').
#   source       Its source name (same characters; not "status").
#   sensor_type  Optional; what to record as the hardware. Default "modbus".
#   host, port   Its IP address and Modbus TCP port (usually 502).
#   unit         Its unit ID (slave ID), 0-255. Often 1; a gateway uses one per device.
#   interval_s   How often to read it, in seconds.
#   timeout_s    Optional; how long to wait for an answer. Default 3.
#   max_gap      Optional; values up to this many registers apart are read in one request.
#                Default 0 (only neighbouring registers). Raise it only if the device
#                allows reading the registers in between.
#   values       The values to read: a list of entries with these options:
#     key        Its name in the database: <quantity>_<unit>, e.g. temperature_c.
#     register   Its address as sent on the wire (0-based): "40011" in a manual is
#                holding register 10.
#     type       "holding" (function 3, the default), "input" (4), "coil" (1) or
#                "discrete" (2). Coils and discrete inputs are published as true/false.
#     format     Registers only: "uint16", "int16", "uint32", "int32" or "float32".
#                32-bit values use two registers, high word first.
#     scale      Registers only, optional: multiplied in, e.g. 0.1 when 214 means 21.4.
#     section    Optional: "values" (the default; a measurement) or "meta" (a status
#                flag or setting).
MODBUS_DEVICES: list[dict[str, Any]] = [
    # An example: a temperature and humidity sensor. Change it to match your device.
    {
        "enabled": False,
        "station": "modbus-01",
        "source": "environment",
        "host": "192.168.1.23",
        "port": 502,
        "unit": 1,
        "interval_s": 60,
        "values": [
            {"key": "temperature_c", "register": 10, "format": "int16", "scale": 0.1},
            {"key": "humidity_pct", "register": 11, "format": "uint16", "scale": 0.1},
        ],
    },
]

# --------------------------------------------------------------------------- #
# Station sources
# --------------------------------------------------------------------------- #
# Every source a station sends, and how often, in seconds (the station's
# docs/server-pi-interface.md, "Topics"). Used to spot a source that has gone silent.
# A station may have some of them disabled.
SOURCE_INTERVALS_S: dict[str, int] = {
    "cpu": 60,
    "air-quality": 60,
    "barometer": 60,
    "co2": 60,
    "light": 60,
    "particulates": 60,
    "temperature": 60,
    "humidity": 60,
    "health": 60,
    "mqboard-mq2": 150,
    "mqboard-mq4": 150,
    "mqboard-mq5": 150,
    "mqboard-mq7": 150,
    "mqboard-mq8": 150,
    "mqboard-mq135": 150,
}
