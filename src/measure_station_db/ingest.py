"""The ingest service: moves readings from the MQTT broker into the database.

Runs as a systemd service (deploy/systemd/measure-station-db-ingest.service)::

    python -m measure_station_db.ingest

How it keeps every reading (see docs/ingest-service.md):

- It connects with a fixed client ID and a persistent session, so the broker keeps
  messages for it while it's stopped.
- It saves messages in batches, and acknowledges each one only after the database has
  committed it, in the order they arrived (as MQTT requires). Until then the broker
  keeps it, and resends it after a reconnect. Resent duplicates are ignored by the
  tables' primary keys.
- A message that breaks the station's interface is logged and acknowledged, so it
  can't block the queue.
- If the database is unavailable, nothing is acknowledged: the broker stops sending
  once max_inflight_messages are unacknowledged, and keeps the rest.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from measure_station_db import schema, sysconfig
from measure_station_db.logging_setup import configure_logging
from measure_station_db.messages import Message, check

log = logging.getLogger(__name__)

# Exit status for a configuration error. The unit file stops systemd restarting the
# service for it, since only a person can fix it.
EX_CONFIG = 78

_INSERT_READING = """
    INSERT INTO reading (station, source, ts, sensor_type, ts_src, values_json, meta_json,
                         boot_id, boot_ts, received_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""
_INSERT_VALUE = """
    INSERT INTO reading_value (station, source, section, key, ts, ts_src, num_value,
                               text_value)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""
# Only a change is stored: the broker resends the retained status at every reconnect.
_INSERT_STATUS = """
    INSERT INTO station_status (station, received_at, status)
    SELECT %(station)s::text, %(received_at)s::timestamptz, %(status)s::text
    WHERE %(status)s::text IS DISTINCT FROM (
        SELECT status FROM station_status WHERE station = %(station)s::text
        ORDER BY received_at DESC LIMIT 1
    )
    ON CONFLICT DO NOTHING
"""


class SchemaError(Exception):
    """The database isn't set up as the setup guide describes."""


@dataclass
class Delivery:
    """One message from the broker, waiting to be stored and acknowledged."""

    mid: int
    qos: int
    generation: int  # which connection to the broker it arrived on
    topic: str
    received_at: datetime
    message: Message


def value_columns(value: Any) -> tuple[float | None, str | None]:
    """The (num_value, text_value) columns of reading_value for one value."""
    if isinstance(value, bool):
        return (1.0 if value else 0.0), None
    if isinstance(value, int | float):
        return float(value), None
    if value is None:
        return None, None
    if isinstance(value, str):
        return None, value
    return None, json.dumps(value, separators=(",", ":"))


def value_rows(msg: Message) -> list[tuple[Any, ...]]:
    """The reading_value rows for one reading: one per key in values and meta."""
    return [
        (msg.station, msg.source, section, key, msg.ts, msg.ts_src, *value_columns(value))
        for section, items in (("values", msg.values), ("meta", msg.meta or {}))
        for key, value in items.items()
    ]


class Store:
    """The database, through one connection that's reopened after a failure."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo
        self._conn: Any = None

    def _connection(self) -> Any:
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self._conninfo,
                autocommit=True,
                connect_timeout=10,
                application_name="measure-station-db-ingest",
            )
        return self._conn

    def apply_schema(self) -> None:
        conn = self._connection()
        found = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if found.fetchone() is None:
            raise SchemaError(
                "TimescaleDB isn't switched on in this database: run CREATE EXTENSION "
                "timescaledb in it as postgres (docs/setting-up-the-server-pi.md, Step 7)"
            )
        for statement in schema.STATEMENTS:
            conn.execute(statement)

    def write(self, deliveries: Sequence[Delivery]) -> None:
        """Store a batch in one transaction. Messages with problems are skipped."""
        from psycopg.types.json import Jsonb

        readings: list[tuple[Any, ...]] = []
        values: list[tuple[Any, ...]] = []
        statuses: list[dict[str, Any]] = []
        for d in deliveries:
            m = d.message
            if m.problems:
                continue
            if m.is_status:
                statuses.append(
                    {"station": m.station, "status": m.status, "received_at": d.received_at}
                )
                continue
            meta = None if m.meta is None else Jsonb(m.meta)
            readings.append(
                (
                    *(m.station, m.source, m.ts, m.sensor_type, m.ts_src),
                    *(Jsonb(m.values), meta, m.boot_id, m.boot_ts, d.received_at),
                )
            )
            values.extend(value_rows(m))

        conn = self._connection()
        with conn.transaction(), conn.cursor() as cur:
            if readings:
                cur.executemany(_INSERT_READING, readings)
            if values:
                cur.executemany(_INSERT_VALUE, values)
            if statuses:
                cur.executemany(_INSERT_STATUS, statuses)

    def refresh_aggregates(self, start: datetime, end: datetime) -> None:
        conn = self._connection()
        for name in schema.AGGREGATES:
            conn.execute(
                f"CALL refresh_continuous_aggregate('{name}', %s::timestamptz, %s::timestamptz)",
                (start, end),
            )

    def reset(self) -> None:
        """Drop the connection after a failure; the next call reconnects."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._conn = None

    close = reset


def _day_start(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class Ingester:
    """Collects deliveries into batches, stores them, then acknowledges them in order.

    ``now`` arguments are time.monotonic() values.
    """

    def __init__(
        self,
        store: Any,
        ack: Callable[[int, int], Any],
        *,
        batch_max: int,
        batch_wait_s: float,
        late_after_s: float,
        refresh_interval_s: float,
    ) -> None:
        self._store = store
        self._ack = ack
        self._batch_max = batch_max
        self._batch_wait_s = batch_wait_s
        self._late_after = timedelta(seconds=late_after_s)
        self._refresh_interval_s = refresh_interval_s
        self.pending: list[Delivery] = []
        self._first_at = 0.0
        self._late: tuple[datetime, datetime] | None = None  # oldest and newest late ts
        self._late_since = 0.0
        self.counts = {"readings": 0, "statuses": 0, "invalid": 0}

    def add(self, delivery: Delivery, now: float) -> None:
        if not self.pending:
            self._first_at = now
        self.pending.append(delivery)
        m = delivery.message
        if m.problems:
            log.warning("Ignored a message on %s: %s", delivery.topic, "; ".join(m.problems))
        elif m.ts is not None and delivery.received_at - m.ts > self._late_after:
            if self._late is None:
                self._late, self._late_since = (m.ts, m.ts), now
            else:
                self._late = (min(self._late[0], m.ts), max(self._late[1], m.ts))

    def seconds_until_due(self, now: float) -> float:
        if not self.pending:
            return self._batch_wait_s
        return max(0.0, self._first_at + self._batch_wait_s - now)

    def due(self, now: float) -> bool:
        return bool(self.pending) and (
            len(self.pending) >= self._batch_max or self.seconds_until_due(now) == 0
        )

    def flush(self, generation: int) -> None:
        """Store everything pending, then acknowledge it. Raises if storing fails.

        Messages that arrived on an earlier connection aren't acknowledged: the broker
        resends those after a reconnect, and the resent copies are acknowledged instead.
        """
        if not self.pending:
            return
        batch = self.pending
        self._store.write(batch)
        self.pending = []
        for d in batch:
            if d.qos > 0 and d.generation == generation:
                self._ack(d.mid, d.qos)
            m = d.message
            kind = "invalid" if m.problems else "statuses" if m.is_status else "readings"
            self.counts[kind] += 1

    def refresh_due(self, now: float) -> bool:
        return self._late is not None and now - self._late_since >= self._refresh_interval_s

    def refresh(self) -> None:
        """Recalculate the averages for the days late readings arrived for."""
        if self._late is None:
            return
        start = _day_start(self._late[0])
        end = _day_start(self._late[1]) + timedelta(days=1)
        self._store.refresh_aggregates(start, end)
        log.info("Recalculated the averages from %s to %s", start.date(), end.date())
        self._late = None

    def take_counts(self) -> dict[str, int]:
        counts, self.counts = self.counts, dict.fromkeys(self.counts, 0)
        return counts


def run() -> int:
    configure_logging(sysconfig.LOG_LEVEL)
    missing = [
        name
        for name, value in (
            ("MEASURE_STATION_DB_MQTT_PASSWORD", sysconfig.MQTT_PASSWORD),
            ("MEASURE_STATION_DB_DATABASE_PASSWORD", sysconfig.DATABASE_PASSWORD),
        )
        if not value
    ]
    if missing:
        log.error(
            "Configuration error: %s not set (in /etc/measure-station-db/env)",
            " and ".join(missing),
        )
        return EX_CONFIG

    import paho.mqtt.client as mqtt
    from psycopg import Error as DatabaseError
    from psycopg.conninfo import make_conninfo

    host, port, base = sysconfig.MQTT_HOST, sysconfig.MQTT_PORT, sysconfig.MQTT_BASE_TOPIC
    retry_s = sysconfig.INGEST_RETRY_S
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    store = Store(
        make_conninfo(
            host=sysconfig.DATABASE_HOST,
            port=sysconfig.DATABASE_PORT,
            dbname=sysconfig.DATABASE_NAME,
            user=sysconfig.DATABASE_USER,
            password=sysconfig.DATABASE_PASSWORD,
        )
    )
    # The database first: there's no point taking messages that can't be stored.
    while not stop.is_set():
        try:
            store.apply_schema()
            break
        except SchemaError as exc:
            log.error("Configuration error: %s", exc)
            return EX_CONFIG
        except DatabaseError as exc:
            log.error("Can't use the database (%s); trying again in %d s", exc, retry_s)
            store.reset()
            stop.wait(retry_s)
    if stop.is_set():
        return 0
    log.info("Database %s is ready", sysconfig.DATABASE_NAME)

    deliveries: queue.Queue[Delivery] = queue.Queue()
    generation = [0]  # counts connections to the broker; read by both threads

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, props: Any) -> None:
        if reason_code.is_failure:
            log.error(
                "The broker refused the connection: %s (check MEASURE_STATION_DB_MQTT_PASSWORD)",
                reason_code,
            )
            return
        generation[0] += 1
        client.subscribe(f"{base}/+/+", qos=1)
        session = (
            "resuming its saved session"
            if flags.session_present
            else "a new session (the broker had kept nothing for it)"
        )
        log.info("Connected to the broker at %s:%d, %s", host, port, session)

    def on_disconnect(client: Any, userdata: Any, flags: Any, reason_code: Any, props: Any) -> None:
        if not stop.is_set():
            log.warning("Lost the connection to the broker (%s); reconnecting", reason_code)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        msg = check(message.topic, message.payload, base)
        deliveries.put(
            Delivery(message.mid, message.qos, generation[0], message.topic, datetime.now(UTC), msg)
        )

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=sysconfig.MQTT_CLIENT_ID,
        clean_session=False,
        manual_ack=True,
    )
    client.username_pw_set(sysconfig.MQTT_USERNAME, sysconfig.MQTT_PASSWORD)
    client.reconnect_delay_set(1, 60)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    ingester = Ingester(
        store,
        client.ack,
        batch_max=sysconfig.INGEST_BATCH_MAX,
        batch_wait_s=sysconfig.INGEST_BATCH_WAIT_S,
        late_after_s=sysconfig.INGEST_LATE_AFTER_S,
        refresh_interval_s=sysconfig.INGEST_REFRESH_INTERVAL_S,
    )

    def take(timeout: float) -> None:
        try:
            ingester.add(deliveries.get(timeout=timeout), time.monotonic())
        except queue.Empty:
            return
        while len(ingester.pending) < sysconfig.INGEST_BATCH_MAX:
            try:
                ingester.add(deliveries.get_nowait(), time.monotonic())
            except queue.Empty:
                return

    def flush() -> bool:
        try:
            ingester.flush(generation[0])
            return True
        except DatabaseError as exc:
            log.error(
                "Can't store %d message(s) (%s). The broker keeps them; trying again in %d s",
                len(ingester.pending),
                exc,
                retry_s,
            )
            store.reset()
            return False

    def refresh() -> None:
        try:
            ingester.refresh()
        except DatabaseError as exc:
            log.error("Can't recalculate the averages (%s); will try again", exc)
            store.reset()

    next_stats = time.monotonic() + sysconfig.INGEST_STATS_INTERVAL_S
    try:
        while not stop.is_set():
            take(min(ingester.seconds_until_due(time.monotonic()), 1.0))
            now = time.monotonic()
            if ingester.due(now) and not flush():
                stop.wait(retry_s)
            if ingester.refresh_due(now) and not ingester.pending:
                refresh()
            if now >= next_stats:
                next_stats = now + sysconfig.INGEST_STATS_INTERVAL_S
                counts = ingester.take_counts()
                if any(counts.values()):
                    log.info(
                        "Stored %d readings and %d status messages; ignored %d invalid",
                        counts["readings"],
                        counts["statuses"],
                        counts["invalid"],
                    )
    except KeyboardInterrupt:
        pass

    stop.set()
    log.info("Stopping: storing what's already received")
    while True:
        try:
            ingester.add(deliveries.get_nowait(), time.monotonic())
        except queue.Empty:
            break
    if flush():
        refresh()
    client.disconnect()
    client.loop_stop()
    store.close()
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
