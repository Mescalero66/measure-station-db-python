"""Database tests. They run on the server Pi against the test database, and are skipped
elsewhere (docs/development.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from measure_station_db.ingest import Delivery, Store
from measure_station_db.messages import check

BASE = "measure-station"
NOW = datetime.now(UTC).replace(microsecond=0)
TOPIC = "measure-station/mstn-2b/co2"


@pytest.fixture
def store(test_db):
    s = Store(test_db)
    s.apply_schema()
    yield s
    s.close()


def query(store: Store, sql: str, *params) -> list[tuple]:
    return store._connection().execute(sql, params).fetchall()


def delivery(topic: str, payload: bytes, received_at: datetime = NOW) -> Delivery:
    return Delivery(1, 1, 1, topic, received_at, check(topic, payload, BASE), payload)


def co2(ts: datetime, ppm: int, ts_src: str = "ntp") -> Delivery:
    msg = {
        "station": "mstn-2b",
        "source": "co2",
        "sensor_type": "scd41",
        "ts": ts.isoformat(timespec="milliseconds"),
        "ts_src": ts_src,
        "values": {"co2_ppm": ppm},
    }
    if ts_src == "estimated":
        msg |= {"boot_id": "b0a7", "boot_ts": 12.5}
    return delivery(TOPIC, json.dumps(msg).encode())


def test_timescaledb_is_the_community_edition(store):
    # The Apache edition has no continuous aggregates or compression.
    assert query(store, "SHOW timescaledb.license") == [("timescale",)]


def test_the_schema_can_be_applied_again(store):
    store.apply_schema()


def test_the_examples_are_stored(store, station_examples):
    examples = station_examples.values()
    store.write([delivery(ex.topic, ex.payload) for ex in examples])
    assert query(store, "SELECT count(*) FROM reading") == [(len(examples),)]

    keys = 0
    for ex in examples:
        msg = json.loads(ex.payload)
        keys += len(msg["values"]) + len(msg.get("meta", {}))
    assert query(store, "SELECT count(*) FROM reading_value") == [(keys,)]

    row = query(
        store,
        "SELECT values_json, meta_json, sensor_type, ts_src, received_at FROM reading "
        "WHERE station = 'mstn-2b' AND source = 'co2'",
    )
    assert row == [
        (
            {"co2_ppm": 500, "temperature_c": 25.0, "humidity_pct": 37.0},
            {"pressure_hpa": 1007, "comp_src": "hpa"},
            "scd41",
            "ntp",
            NOW,
        )
    ]
    assert query(
        store,
        "SELECT num_value, text_value FROM reading_value WHERE source = 'co2' AND key = 'comp_src'",
    ) == [(None, "hpa")]


def test_duplicates_are_ignored(store, station_examples):
    batch = [delivery(ex.topic, ex.payload) for ex in station_examples.values()]
    store.write(batch)
    store.write(batch)
    assert query(store, "SELECT count(*) FROM reading") == [(len(batch),)]


def test_only_status_changes_are_stored(store):
    topic = "measure-station/mstn-2b/status"
    store.write(
        [
            delivery(topic, b"online", NOW),
            delivery(topic, b"online", NOW + timedelta(seconds=1)),  # resent at a reconnect
            delivery(topic, b"offline", NOW + timedelta(seconds=2)),
        ]
    )
    store.write([delivery(topic, b"offline", NOW + timedelta(seconds=3))])
    rows = query(store, "SELECT status FROM station_status ORDER BY received_at")
    assert rows == [("online",), ("offline",)]


def test_messages_with_problems_are_kept_as_received(store):
    store.write([delivery(TOPIC, b"{broken \xff")])
    assert query(store, "SELECT count(*) FROM reading") == [(0,)]
    rows = query(store, "SELECT received_at, topic, payload, problems FROM rejected_message")
    assert rows == [(NOW, TOPIC, b"{broken \xff", "not valid JSON")]


def test_averages_include_late_readings_after_a_refresh(store):
    hour = (NOW - timedelta(days=10)).replace(minute=0, second=0)
    store.write(
        [
            co2(hour + timedelta(minutes=1), 400),
            co2(hour + timedelta(minutes=2), 600),
            co2(hour + timedelta(minutes=3), 9999, ts_src="estimated"),  # left out
        ]
    )
    day = hour.replace(hour=0)
    store.refresh_aggregates(day, day + timedelta(days=1))
    hourly = query(
        store,
        "SELECT avg_value, min_value, max_value, n FROM value_hourly "
        "WHERE key = 'co2_ppm' AND bucket = %s",
        hour,
    )
    assert hourly == [(500.0, 400.0, 600.0, 2)]
    daily = query(store, "SELECT avg_value, n FROM value_daily WHERE key = 'co2_ppm'")
    assert daily == [(500.0, 2)]


def test_compression_and_refresh_policies_exist(store):
    jobs = query(
        store,
        "SELECT proc_name, hypertable_name FROM timescaledb_information.jobs "
        "WHERE proc_name IN ('policy_compression', 'policy_refresh_continuous_aggregate')",
    )
    assert sorted(name for name, _ in jobs) == [
        "policy_compression",
        "policy_compression",
        "policy_refresh_continuous_aggregate",
        "policy_refresh_continuous_aggregate",
    ]


def test_grafana_can_read_every_table(store):
    if query(store, "SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader'") == []:
        pytest.skip("no grafana_reader role in this PostgreSQL")
    tables = ("reading", "reading_value", "station_status", "rejected_message")
    for table in (*tables, "value_hourly", "value_daily"):
        allowed = query(store, "SELECT has_table_privilege('grafana_reader', %s, 'SELECT')", table)
        assert allowed == [(True,)], table
