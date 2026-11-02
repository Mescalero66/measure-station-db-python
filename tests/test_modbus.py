from __future__ import annotations

import json
import logging
import socket
import struct
import threading

import pytest

from measure_station_db import modbus, sysconfig
from measure_station_db.messages import check
from measure_station_db.modbus import (
    Block,
    ConfigError,
    DevicePoller,
    Point,
    convert,
    parse_devices,
    plan_blocks,
)

BASE = "measure-station"


class FakeModbusDevice:
    """A Modbus TCP server on localhost, answering functions 1-4 from tables.

    ``tables`` maps a function code to {address: value}. Reading an address that isn't
    in the table gets exception 2 (illegal data address), as a real device does.
    """

    def __init__(self, tables: dict[int, dict[int, int]]) -> None:
        self.tables = tables
        self.requests: list[tuple[int, int, int, int]] = []  # unit, function, address, count
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            while True:
                request = b""
                while len(request) < 12:
                    try:
                        chunk = conn.recv(12 - len(request))
                    except OSError:
                        return
                    if not chunk:
                        return
                    request += chunk
                tid, _, _, unit, fc, address, count = struct.unpack(">HHHBBHH", request)
                self.requests.append((unit, fc, address, count))
                table = self.tables.get(fc, {})
                wanted = range(address, address + count)
                if any(a not in table for a in wanted):
                    pdu = bytes([fc | 0x80, 2])
                elif fc in (3, 4):
                    data = struct.pack(f">{count}H", *(table[a] for a in wanted))
                    pdu = bytes([fc, len(data)]) + data
                else:
                    data = bytearray((count + 7) // 8)
                    for i, a in enumerate(wanted):
                        data[i // 8] |= (table[a] & 1) << (i % 8)
                    pdu = bytes([fc, len(data)]) + bytes(data)
                conn.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)

    def stop(self) -> None:
        """The device goes away: refuse new connections and drop open ones."""
        self._server.close()
        for conn in self._conns:
            conn.close()


def device_settings(device_port: int, **changes) -> dict:
    settings = {
        "enabled": True,
        "station": "modbus-01",
        "source": "environment",
        "host": "127.0.0.1",
        "port": device_port,
        "unit": 7,
        "interval_s": 60,
        "timeout_s": 1,
        "values": [
            {"key": "temperature_c", "register": 10, "format": "int16", "scale": 0.1},
            {"key": "humidity_pct", "register": 11, "format": "uint16", "scale": 0.1},
            {"key": "energy_wh", "register": 20, "format": "uint32"},
            {"key": "power_w", "register": 5, "type": "input", "format": "float32"},
            {"key": "alarm", "register": 3, "type": "coil", "section": "meta"},
        ],
    }
    return settings | changes


TABLES = {
    3: {10: 0xFFF1, 11: 483, 20: 0x0001, 21: 0x86A0},  # -1.5 °C, 48.3 %, 100000 Wh
    4: {5: 0x4248, 6: 0x0000},  # 50.0 as float32
    1: {3: 1},
}


@pytest.fixture
def fake_device():
    device = FakeModbusDevice(TABLES)
    yield device
    device.stop()


def poller(port: int, published: list, synced: bool = True, **changes) -> DevicePoller:
    (device,) = parse_devices([device_settings(port, **changes)])
    return DevicePoller(
        device,
        lambda topic, payload, retain: published.append((topic, payload, retain)),
        BASE,
        synced=lambda: synced,
    )


# --- settings ---------------------------------------------------------------


def test_modbus_is_off_by_default():
    assert sysconfig.MODBUS_ENABLED is False
    assert parse_devices(sysconfig.MODBUS_DEVICES) == []  # the example is disabled


def test_the_sysconfig_example_is_valid_once_enabled():
    devices = parse_devices([{**d, "enabled": True} for d in sysconfig.MODBUS_DEVICES])
    assert [d.station for d in devices] == ["modbus-01"]


def test_disabled_devices_are_skipped_but_still_checked():
    assert parse_devices([device_settings(502, enabled=False)]) == []
    with pytest.raises(ConfigError, match="unknown option"):
        parse_devices([device_settings(502, enabled=False, hots="x")])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"station": "a/b"}, "'station' must be"),
        ({"source": "status"}, "can't be 'status'"),
        ({"host": ""}, "'host'"),
        ({"port": 0}, "'port'"),
        ({"unit": 300}, "'unit'"),
        ({"interval_s": 0}, "'interval_s'"),
        ({"max_gap": -1}, "'max_gap'"),
        ({"values": []}, "at least one value"),
        ({"values": [{"key": "Temp", "register": 1, "format": "int16"}]}, "'key'"),
        ({"values": [{"key": "t_c", "register": 1, "format": "int8"}]}, "'format'"),
        ({"values": [{"key": "t_c", "register": 1, "format": "int16", "scale": 0}]}, "'scale'"),
        ({"values": [{"key": "t_c", "register": -1, "format": "int16"}]}, "'register'"),
        ({"values": [{"key": "t_c", "register": 65535, "format": "int32"}]}, "'register'"),
        ({"values": [{"key": "t_c", "register": 1, "type": "x", "format": "int16"}]}, "'type'"),
        (
            {"values": [{"key": "a", "register": 1, "type": "coil", "format": "int16"}]},
            "true/false",
        ),
        ({"values": [{"key": "t_c", "register": 1, "format": "int16", "section": "x"}]}, "section"),
        ({"values": [{"key": "t_c", "register": 1, "format": "int16", "unit": "C"}]}, "unknown"),
        (
            {
                "values": [
                    {"key": "t_c", "register": 1, "format": "int16"},
                    {"key": "t_c", "register": 2, "format": "int16"},
                ]
            },
            "same key",
        ),
    ],
)
def test_bad_settings_are_reported(changes, message):
    with pytest.raises(ConfigError, match=message):
        parse_devices([device_settings(502, **changes)])


def test_two_devices_cannot_share_a_station_and_source():
    with pytest.raises(ConfigError, match="same station and source"):
        parse_devices([device_settings(502), device_settings(503)])


# --- reading plan and values ------------------------------------------------


def holding(register: int, fmt: str = "uint16") -> Point:
    return Point(f"v{register}", "values", "holding", register, fmt, 1)


def test_neighbouring_registers_are_read_together():
    points = (holding(10), holding(11), holding(12, "int32"), holding(20))
    assert plan_blocks(points, max_gap=0) == (Block("holding", 10, 4), Block("holding", 20, 1))
    assert plan_blocks(points, max_gap=6) == (Block("holding", 10, 11),)


def test_types_are_read_separately():
    points = (holding(1), Point("a", "meta", "coil", 1, None, 1))
    assert plan_blocks(points, max_gap=0) == (Block("coil", 1, 1), Block("holding", 1, 1))


def test_a_request_never_exceeds_125_registers():
    points = (holding(0), holding(124), holding(125))
    assert plan_blocks(points, max_gap=1000) == (Block("holding", 0, 125), Block("holding", 125, 1))


def test_convert():
    p = Point("t", "values", "holding", 0, "int16", 0.1)
    assert convert(p, [0xFFF1], 0) == -1.5
    assert convert(Point("n", "values", "holding", 0, "uint16", 1), [483], 0) == 483
    assert convert(Point("e", "values", "holding", 0, "uint32", 1), [1, 0x86A0], 0) == 100000
    assert convert(Point("k", "values", "holding", 0, "uint16", 0.001), [1234], 0) == 1.234
    assert convert(Point("f", "values", "input", 0, "float32", 1), [0x41BB, 0x3333], 0) == 23.4
    assert convert(Point("f", "values", "input", 0, "float32", 1), [0x7FC0, 0], 0) is None  # NaN
    assert convert(Point("a", "meta", "coil", 0, None, 1), [1], 0) is True


# --- polling a device -------------------------------------------------------


def test_a_reading_is_published_as_a_station_message(fake_device):
    published: list = []
    p = poller(fake_device.port, published)
    assert p.poll_once()

    (topic, payload, retain), status = published
    assert (topic, retain) == ("measure-station/modbus-01/environment", False)
    assert status == ("measure-station/modbus-01/status", b"online", True)
    msg = check(topic, payload, BASE)
    assert msg.problems == []
    assert msg.sensor_type == "modbus" and msg.ts_src == "ntp"
    assert msg.values == {
        "temperature_c": -1.5,
        "humidity_pct": 48.3,
        "energy_wh": 100000,
        "power_w": 50.0,
    }
    assert msg.meta == {"alarm": True}
    assert json.loads(payload)["ts"].endswith("+00:00")
    # Unit 7; one request per block: holding 10-11, holding 20-21, input 5-6, coil 3.
    assert sorted(fake_device.requests) == [
        (7, 1, 3, 1),
        (7, 3, 10, 2),
        (7, 3, 20, 2),
        (7, 4, 5, 2),
    ]


def test_status_is_published_only_when_it_changes(fake_device):
    published: list = []
    p = poller(fake_device.port, published)
    p.poll_once()
    p.poll_once()
    statuses = [payload for topic, payload, _ in published if topic.endswith("/status")]
    assert statuses == [b"online"]


def test_a_device_that_stops_answering_goes_offline(fake_device, caplog):
    published: list = []
    p = poller(fake_device.port, published)
    p.poll_once()
    fake_device.stop()
    published.clear()

    assert not p.poll_once()
    assert not p.poll_once()
    assert published == [("measure-station/modbus-01/status", b"offline", True)]
    assert caplog.text.count("isn't answering") == 1


def test_it_comes_back_online(fake_device):
    published: list = []
    p = poller(fake_device.port, published)
    fake_device.stop()
    assert not p.poll_once()

    again = FakeModbusDevice(TABLES)
    try:
        p2 = poller(again.port, published)
        p2.online = False  # as if it had been offline
        assert p2.poll_once()
        assert published[-1] == ("measure-station/modbus-01/status", b"online", True)
    finally:
        again.stop()


def test_a_modbus_exception_reply_is_a_failed_read(caplog):
    device = FakeModbusDevice({3: {10: 1}})  # register 11 and the rest are missing
    try:
        published: list = []
        assert not poller(device.port, published).poll_once()
        assert published == [("measure-station/modbus-01/status", b"offline", True)]
        assert "illegal data address" in caplog.text
    finally:
        device.stop()


def test_nothing_is_read_until_the_clock_is_set(fake_device):
    published: list = []
    assert not poller(fake_device.port, published, synced=False).poll_once()
    assert published == [] and fake_device.requests == []


# --- the service ------------------------------------------------------------


@pytest.fixture
def service_logs(monkeypatch, caplog):
    """Keep pytest's log capture: run() would otherwise replace the root log handlers."""
    monkeypatch.setattr(modbus, "configure_logging", lambda level: None)
    caplog.set_level(logging.INFO)
    return caplog


def test_the_service_stops_at_once_when_switched_off(monkeypatch, service_logs):
    caplog = service_logs
    monkeypatch.setattr(sysconfig, "MODBUS_ENABLED", False)
    assert modbus.run() == 0
    assert "switched off" in caplog.text


def test_the_service_stops_when_no_device_is_enabled(monkeypatch, service_logs):
    caplog = service_logs
    monkeypatch.setattr(sysconfig, "MODBUS_ENABLED", True)
    monkeypatch.setattr(sysconfig, "MODBUS_DEVICES", [device_settings(502, enabled=False)])
    assert modbus.run() == 0
    assert "No Modbus device is enabled" in caplog.text


def test_bad_settings_stop_the_service_with_a_configuration_error(monkeypatch, service_logs):
    caplog = service_logs
    monkeypatch.setattr(sysconfig, "MODBUS_ENABLED", True)
    monkeypatch.setattr(sysconfig, "MODBUS_DEVICES", [device_settings(502, port=0)])
    assert modbus.run() == modbus.EX_CONFIG
    assert "Configuration error" in caplog.text
