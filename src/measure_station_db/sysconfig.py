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
