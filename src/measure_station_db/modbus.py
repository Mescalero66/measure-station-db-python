"""The Modbus poller: reads Modbus TCP devices and publishes them as station messages.

Runs as a systemd service (deploy/systemd/measure-station-db-modbus.service)::

    python -m measure_station_db.modbus

Each enabled device in sysconfig.MODBUS_DEVICES becomes a "virtual station": every
interval_s its registers are read, and the values are published to the broker on
``measure-station/<station>/<source>``, in the station's own message format. The ingest
service then stores them like any station's readings. See docs/modbus-devices.md.

- **Switched off by default.** With MODBUS_ENABLED False, or no device enabled, it logs
  that and exits. The unit file only restarts it after a failure, so it stays stopped.
- **Each device is read in its own thread,** so a slow or missing device can't delay the
  others. Neighbouring registers are read in one request (see the max_gap option).
- **A failed read publishes nothing,** like a station's failed sensor. The device's
  retained ``status`` topic says ``offline`` until it answers again, and ``online`` then.
- **Readings are timestamped by this Pi's clock,** which has no battery. Polling waits
  until NTP has set it, so no reading gets a wrong time.
- **While the broker is unreachable,** readings wait in memory (up to MODBUS_QUEUE_MAX)
  and are sent when it's back. They're lost if the poller itself restarts meanwhile.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from measure_station_db import sysconfig
from measure_station_db.drivers.modbus_tcp import (
    FORMATS,
    REG_TYPES,
    ModbusError,
    ModbusTcpClient,
    decode,
)
from measure_station_db.logging_setup import configure_logging
from measure_station_db.messages import STATUS_SOURCE

log = logging.getLogger(__name__)

EX_CONFIG = 78  # a configuration error: the unit file doesn't restart the service

# The settings' register types -> the driver's names for them.
TYPES = {
    "holding": "Holding registers",
    "input": "Input registers",
    "coil": "Coils",
    "discrete": "Discrete inputs",
}
_BIT_TYPES = {"coil", "discrete"}
_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_DEVICE_OPTIONS = {
    "enabled",
    "station",
    "source",
    "sensor_type",
    "host",
    "port",
    "unit",
    "interval_s",
    "timeout_s",
    "max_gap",
    "values",
}
_VALUE_OPTIONS = {"key", "register", "type", "format", "scale", "section"}
# systemd-timesyncd creates this file once it has set the clock.
_TIME_SYNCED_FLAG = Path("/run/systemd/timesync/synchronized")


class ConfigError(ValueError):
    """A device's settings are wrong."""


@dataclass(frozen=True)
class Point:
    """One configured value: where it is, and how to turn its registers into a value."""

    key: str
    section: str  # "values" or "meta"
    type: str  # a TYPES key
    register: int
    format: str | None  # None for coils and discrete inputs
    scale: float | int

    @property
    def size(self) -> int:
        """How many registers (or bits) it takes."""
        return 2 if self.format is not None and self.format.endswith("32") else 1


@dataclass(frozen=True)
class Block:
    """One read request: count registers (or bits) from start."""

    type: str
    start: int
    count: int


@dataclass(frozen=True)
class Device:
    station: str
    source: str
    sensor_type: str
    host: str
    port: int
    unit: int
    interval_s: float
    timeout_s: float
    points: tuple[Point, ...]
    blocks: tuple[Block, ...]

    @property
    def name(self) -> str:
        return f"{self.station}/{self.source} ({self.host}:{self.port} unit {self.unit})"


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _point(where: str, raw: Any) -> Point:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a {{...}} entry")
    unknown = set(raw) - _VALUE_OPTIONS
    if unknown:
        raise ConfigError(f"{where}: unknown option(s) {', '.join(sorted(unknown))}")
    key = raw.get("key")
    if not isinstance(key, str) or not _KEY.match(key):
        raise ConfigError(f"{where}: 'key' must be lowercase letters, digits and _")
    where = f"{where} ({key})"
    kind = raw.get("type", "holding")
    if kind not in TYPES:
        raise ConfigError(f"{where}: 'type' must be one of {', '.join(TYPES)}")
    fmt = raw.get("format")
    if kind in _BIT_TYPES:
        if fmt is not None or "scale" in raw:
            raise ConfigError(f"{where}: a {kind} is true/false: leave out 'format' and 'scale'")
    elif fmt not in FORMATS:
        raise ConfigError(f"{where}: 'format' must be one of {', '.join(FORMATS)}")
    scale = raw.get("scale", 1)
    if not _number(scale) or scale == 0:
        raise ConfigError(f"{where}: 'scale' must be a number other than 0")
    section = raw.get("section", "values")
    if section not in ("values", "meta"):
        raise ConfigError(f"{where}: 'section' must be 'values' or 'meta'")
    point = Point(key, section, kind, raw.get("register"), fmt, scale)  # type: ignore[arg-type]
    if not isinstance(point.register, int) or isinstance(point.register, bool):
        raise ConfigError(f"{where}: 'register' must be a whole number")
    if not 0 <= point.register <= 0x10000 - point.size:
        raise ConfigError(f"{where}: 'register' must be 0 to {0x10000 - point.size}")
    return point


def plan_blocks(points: tuple[Point, ...], max_gap: int) -> tuple[Block, ...]:
    """The read requests for these points: as few as the device's limits allow.

    Points of one type are read together when at most max_gap registers lie between
    them, up to the most one request may read (125 registers or 2000 bits).
    """
    blocks: list[Block] = []
    for kind in sorted({p.type for p in points}):
        limit = REG_TYPES[TYPES[kind]][3]
        spans = sorted((p.register, p.register + p.size - 1) for p in points if p.type == kind)
        start, end = spans[0]
        for first, last in spans[1:]:
            if first - end - 1 <= max_gap and max(end, last) - start + 1 <= limit:
                end = max(end, last)
            else:
                blocks.append(Block(kind, start, end - start + 1))
                start, end = first, last
        blocks.append(Block(kind, start, end - start + 1))
    return tuple(blocks)


def parse_devices(raw_devices: list[dict[str, Any]]) -> list[Device]:
    """The enabled devices, checked. Raises ConfigError naming what's wrong."""
    devices: list[Device] = []
    for n, raw in enumerate(raw_devices, 1):
        where = f"MODBUS_DEVICES entry {n}"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a {{...}} entry")
        unknown = set(raw) - _DEVICE_OPTIONS
        if unknown:
            raise ConfigError(f"{where}: unknown option(s) {', '.join(sorted(unknown))}")
        if not raw.get("enabled", False):
            continue
        station, source = raw.get("station"), raw.get("source")
        for name, value in (("station", station), ("source", source)):
            if not isinstance(value, str) or not _ID.match(value):
                raise ConfigError(f"{where}: '{name}' must be letters, digits, '.', '_' or '-'")
        if source == STATUS_SOURCE:
            raise ConfigError(f"{where}: 'source' can't be '{STATUS_SOURCE}'")
        where = f"{where} ({station}/{source})"
        host = raw.get("host")
        if not isinstance(host, str) or not host:
            raise ConfigError(f"{where}: 'host' must be an IP address or host name")
        port, unit = raw.get("port", 502), raw.get("unit", 1)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigError(f"{where}: 'port' must be 1 to 65535")
        if not isinstance(unit, int) or not 0 <= unit <= 255:
            raise ConfigError(f"{where}: 'unit' must be 0 to 255")
        interval, timeout = raw.get("interval_s"), raw.get("timeout_s", 3)
        if not _number(interval) or interval <= 0:
            raise ConfigError(f"{where}: 'interval_s' must be a number of seconds above 0")
        if not _number(timeout) or timeout <= 0:
            raise ConfigError(f"{where}: 'timeout_s' must be a number of seconds above 0")
        max_gap = raw.get("max_gap", 0)
        if not isinstance(max_gap, int) or isinstance(max_gap, bool) or max_gap < 0:
            raise ConfigError(f"{where}: 'max_gap' must be a whole number, 0 or more")
        sensor_type = raw.get("sensor_type", "modbus")
        if not isinstance(sensor_type, str) or not sensor_type:
            raise ConfigError(f"{where}: 'sensor_type' must be text")
        raw_values = raw.get("values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ConfigError(f"{where}: 'values' must list at least one value")
        points = tuple(_point(f"{where}, value {i}", v) for i, v in enumerate(raw_values, 1))
        seen = [(p.section, p.key) for p in points]
        if len(set(seen)) != len(seen):
            raise ConfigError(f"{where}: two values have the same key")
        if any((d.station, d.source) == (station, source) for d in devices):
            raise ConfigError(f"{where}: another enabled device has the same station and source")
        devices.append(
            Device(
                *(station, source, sensor_type, host, port, unit, interval, timeout),
                points,
                plan_blocks(points, max_gap),
            )
        )
    return devices


def _places(scale: float | int) -> int:
    """Decimal places a scaled value can have: 0.1 -> 1, 0.25 -> 2, 10 -> 0."""
    exponent = Decimal(repr(scale)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def convert(point: Point, data: list[int], offset: int) -> Any:
    """One point's value from its block's raw registers (or bits); None if unreadable."""
    if point.format is None:
        return bool(data[offset])
    raw = decode(data, offset, point.format)
    if raw is None:
        return None
    value = raw * point.scale
    if point.format == "float32":
        return float(f"{value:.7g}")  # a float32 holds about 7 significant digits
    if isinstance(value, float):
        return round(value, _places(point.scale))
    return value


def read_values(device: Device, client: Any) -> dict[str, dict[str, Any]]:
    """Read every block from the device; the values and meta to publish."""
    data = {
        block: client.read(REG_TYPES[TYPES[block.type]][0], block.start, block.count)
        for block in device.blocks
    }
    sections: dict[str, dict[str, Any]] = {"values": {}, "meta": {}}
    for point in device.points:
        block = next(
            b
            for b in device.blocks
            if b.type == point.type and b.start <= point.register < b.start + b.count
        )
        sections[point.section][point.key] = convert(
            point, data[block], point.register - block.start
        )
    return sections


def encode(device: Device, sections: dict[str, dict[str, Any]], when: datetime) -> bytes:
    """A station message (the station's docs/server-pi-interface.md format)."""
    msg: dict[str, Any] = {
        "station": device.station,
        "source": device.source,
        "sensor_type": device.sensor_type,
        "ts": when.astimezone(UTC).isoformat(timespec="milliseconds"),
        "ts_src": "ntp",
        "values": sections["values"],
    }
    if sections["meta"]:
        msg["meta"] = sections["meta"]
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def clock_synced() -> bool:
    """Whether NTP has set this Pi's clock (always True off Linux, for development)."""
    return not sys.platform.startswith("linux") or _TIME_SYNCED_FLAG.exists()


Publish = Callable[[str, bytes, bool], None]  # topic, payload, retain


class DevicePoller:
    """Reads one device on a timer and publishes what it reads."""

    def __init__(
        self,
        device: Device,
        publish: Publish,
        base_topic: str,
        *,
        connect: Callable[..., Any] = ModbusTcpClient,
        synced: Callable[[], bool] = clock_synced,
    ) -> None:
        self.device = device
        self._publish = publish
        self._topic = f"{base_topic}/{device.station}/{device.source}"
        self._status_topic = f"{base_topic}/{device.station}/{STATUS_SOURCE}"
        self._connect = connect
        self._synced = synced
        self._client: Any = None
        self.online: bool | None = None  # None until the first read
        self._waiting_logged = False

    def poll_once(self) -> bool:
        """Read the device once and publish the result. True if it answered."""
        d = self.device
        if not self._synced():
            if not self._waiting_logged:
                log.info("%s: waiting for NTP to set the clock before reading", d.name)
                self._waiting_logged = True
            return False
        try:
            if self._client is None:
                self._client = self._connect(d.host, d.port, d.unit, d.timeout_s)
            sections = read_values(d, self._client)
        except (OSError, ModbusError) as exc:
            self._close()
            if self.online is not False:
                log.warning("%s isn't answering: %s", d.name, exc)
                self._set_status(False)
            return False
        self._publish(self._topic, encode(d, sections, datetime.now(UTC)), False)
        if self.online is not True:
            log.info("%s is answering", d.name)
            self._set_status(True)
        return True

    def run(self, stop: threading.Event) -> None:
        """Poll on a fixed cadence until stop is set, then report offline."""
        next_due = time.monotonic()
        while not stop.wait(max(0.0, next_due - time.monotonic())):
            next_due += self.device.interval_s
            if next_due < time.monotonic():  # fell behind (a slow device): don't catch up
                next_due = time.monotonic() + self.device.interval_s
            self.poll_once()
        self._close()
        if self.online:
            self._set_status(False)

    def _set_status(self, online: bool) -> None:
        self.online = online
        self._publish(self._status_topic, b"online" if online else b"offline", True)

    def _close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(OSError):
                self._client.close()
            self._client = None


def run() -> int:
    configure_logging(sysconfig.LOG_LEVEL)
    if not sysconfig.MODBUS_ENABLED:
        log.info("Modbus polling is switched off (MODBUS_ENABLED in sysconfig.py); stopping")
        return 0
    try:
        devices = parse_devices(sysconfig.MODBUS_DEVICES)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EX_CONFIG
    if not devices:
        log.info("No Modbus device is enabled (MODBUS_DEVICES in sysconfig.py); stopping")
        return 0
    if not sysconfig.MODBUS_MQTT_PASSWORD:
        log.error(
            "Configuration error: MEASURE_STATION_DB_MODBUS_MQTT_PASSWORD not set "
            "(in /etc/measure-station-db/env)"
        )
        return EX_CONFIG

    import paho.mqtt.client as mqtt

    host, port = sysconfig.MQTT_HOST, sysconfig.MQTT_PORT
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, props: Any) -> None:
        if reason_code.is_failure:
            log.error(
                "The broker refused the connection: %s "
                "(check MEASURE_STATION_DB_MODBUS_MQTT_PASSWORD)",
                reason_code,
            )
        else:
            log.info("Connected to the broker at %s:%d", host, port)

    def on_disconnect(client: Any, userdata: Any, flags: Any, reason_code: Any, props: Any) -> None:
        if not stop.is_set():
            log.warning("Lost the connection to the broker (%s); reconnecting", reason_code)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=sysconfig.MODBUS_MQTT_USERNAME)
    client.username_pw_set(sysconfig.MODBUS_MQTT_USERNAME, sysconfig.MODBUS_MQTT_PASSWORD)
    client.max_queued_messages_set(sysconfig.MODBUS_QUEUE_MAX)
    client.reconnect_delay_set(1, 60)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    def publish(topic: str, payload: bytes, retain: bool) -> None:
        # QoS 1: paho keeps it until the broker confirms, resending after a reconnect.
        info = client.publish(topic, payload, qos=1, retain=retain)
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            log.warning(
                "Dropped a message for %s: %d are already waiting for the broker",
                topic,
                sysconfig.MODBUS_QUEUE_MAX,
            )

    pollers = [DevicePoller(d, publish, sysconfig.MQTT_BASE_TOPIC) for d in devices]
    threads = [
        threading.Thread(target=p.run, args=(stop,), name=p.device.station, daemon=True)
        for p in pollers
    ]
    for p in pollers:
        log.info(
            "Reading %s every %g s: %d value(s) in %d request(s)",
            p.device.name,
            p.device.interval_s,
            len(p.device.points),
            len(p.device.blocks),
        )
    for t in threads:
        t.start()
    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        stop.set()
    log.info("Stopping")
    for t in threads:
        t.join(timeout=10)
    time.sleep(0.5)  # let the "offline" statuses go out
    client.disconnect()
    client.loop_stop()
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
