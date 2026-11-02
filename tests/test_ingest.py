from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from measure_station_db.ingest import Delivery, Ingester, value_columns, value_rows
from measure_station_db.messages import check

BASE = "measure-station"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class FakeStore:
    """Records what's written, into a log shared with the acknowledgements."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.fail = False

    def write(self, deliveries):
        if self.fail:
            raise ConnectionError("database down")
        self.events.append(("write", [d.mid for d in deliveries]))

    def refresh_aggregates(self, start, end):
        self.events.append(("refresh", start, end))


@pytest.fixture
def events() -> list:
    return []


@pytest.fixture
def ingester(events) -> Ingester:
    return Ingester(
        FakeStore(events),
        lambda mid, qos: events.append(("ack", mid)),
        batch_max=3,
        batch_wait_s=1.0,
        late_after_s=2 * 86400,
        refresh_interval_s=300,
    )


def reading(mid: int, *, age: timedelta = timedelta(seconds=2), generation: int = 1, qos: int = 1):
    ts = (NOW - age).isoformat(timespec="milliseconds")
    payload = {
        "station": "mstn-2b",
        "source": "co2",
        "sensor_type": "scd41",
        "ts": ts,
        "ts_src": "ntp",
        "values": {"co2_ppm": 500},
    }
    topic = "measure-station/mstn-2b/co2"
    raw_payload = json.dumps(payload).encode()
    return Delivery(mid, qos, generation, topic, NOW, check(topic, raw_payload, BASE), raw_payload)


def raw(mid: int, topic: str, payload: bytes) -> Delivery:
    return Delivery(mid, 1, 1, topic, NOW, check(topic, payload, BASE), payload)


def test_value_columns():
    assert value_columns(500) == (500.0, None)
    assert value_columns(25.0) == (25.0, None)
    assert value_columns(True) == (1.0, None)
    assert value_columns(False) == (0.0, None)
    assert value_columns("hpa") == (None, "hpa")
    assert value_columns(None) == (None, None)
    assert value_columns(["MQ4", "MQ7"]) == (None, '["MQ4","MQ7"]')


def test_value_rows_cover_values_and_meta(station_examples):
    ex = station_examples["co2"]
    rows = value_rows(check(ex.topic, ex.payload, BASE))
    ts = datetime(2026, 10, 21, 9, 40, 2, 517000, tzinfo=UTC)
    assert rows == [
        ("mstn-2b", "co2", "values", "co2_ppm", ts, "ntp", 500.0, None),
        ("mstn-2b", "co2", "values", "temperature_c", ts, "ntp", 25.0, None),
        ("mstn-2b", "co2", "values", "humidity_pct", ts, "ntp", 37.0, None),
        ("mstn-2b", "co2", "meta", "pressure_hpa", ts, "ntp", 1007.0, None),
        ("mstn-2b", "co2", "meta", "comp_src", ts, "ntp", None, "hpa"),
    ]


def test_every_example_gives_a_row_per_key(station_examples):
    for name, ex in station_examples.items():
        msg = check(ex.topic, ex.payload, BASE)
        expected = len(msg.values) + len(msg.meta or {})
        assert len(value_rows(msg)) == expected, name


def test_acknowledged_only_after_the_write_in_arrival_order(ingester, events):
    for mid in (7, 3, 5):
        ingester.add(reading(mid), now=0.0)
    assert ingester.due(0.0)  # batch_max reached
    ingester.flush(generation=1)
    assert events == [("write", [7, 3, 5]), ("ack", 7), ("ack", 3), ("ack", 5)]
    assert ingester.pending == []


def test_a_batch_is_due_after_the_wait(ingester):
    assert not ingester.due(0.0)
    ingester.add(reading(1), now=10.0)
    assert not ingester.due(10.5)
    assert ingester.seconds_until_due(10.5) == pytest.approx(0.5)
    assert ingester.due(11.0)


def test_nothing_is_acknowledged_when_the_write_fails(ingester, events):
    ingester._store.fail = True
    ingester.add(reading(1), now=0.0)
    ingester.add(reading(2), now=0.0)
    with pytest.raises(ConnectionError):
        ingester.flush(generation=1)
    assert events == []
    assert [d.mid for d in ingester.pending] == [1, 2]

    ingester._store.fail = False
    ingester.add(reading(3), now=5.0)
    ingester.flush(generation=1)
    assert events == [("write", [1, 2, 3]), ("ack", 1), ("ack", 2), ("ack", 3)]


def test_rejected_messages_are_acknowledged_in_order(ingester, events, caplog):
    ingester.add(reading(1), now=0.0)
    ingester.add(raw(2, "measure-station/mstn-2b/light", b"{broken"), now=0.0)
    ingester.add(reading(3), now=0.0)
    ingester.flush(generation=1)
    assert events[1:] == [("ack", 1), ("ack", 2), ("ack", 3)]
    assert (
        "Set aside a message on measure-station/mstn-2b/light in rejected_message: "
        "not valid JSON" in caplog.text
    )
    assert ingester.take_counts() == {"readings": 2, "statuses": 0, "rejected": 1}
    assert ingester.take_counts() == {"readings": 0, "statuses": 0, "rejected": 0}


def test_messages_from_an_earlier_connection_are_not_acknowledged(ingester, events):
    ingester.add(reading(1, generation=1), now=0.0)
    ingester.add(reading(1, generation=2), now=0.0)  # the broker's resend, same mid
    ingester.flush(generation=2)
    assert events == [("write", [1, 1]), ("ack", 1)]


def test_qos0_messages_are_not_acknowledged(ingester, events):
    ingester.add(reading(1, qos=0), now=0.0)
    ingester.flush(generation=1)
    assert events == [("write", [1])]


def test_status_messages_are_counted(ingester):
    ingester.add(raw(1, "measure-station/mstn-2b/status", b"online"), now=0.0)
    ingester.flush(generation=1)
    assert ingester.take_counts()["statuses"] == 1


def test_late_readings_have_their_days_recalculated(ingester, events):
    ingester.add(reading(1, age=timedelta(days=10)), now=0.0)
    ingester.add(reading(2, age=timedelta(days=4, hours=13)), now=1.0)
    ingester.add(reading(3), now=2.0)  # recent: the aggregates refresh it themselves
    ingester.flush(generation=1)
    assert not ingester.refresh_due(299.0)
    assert ingester.refresh_due(300.0)
    ingester.refresh()
    start = datetime(2026, 10, 23, tzinfo=UTC)  # the day of the oldest late reading
    end = datetime(2026, 10, 29, tzinfo=UTC)  # the day after the newest
    assert events[-1] == ("refresh", start, end)
    assert not ingester.refresh_due(1000.0)


def test_recent_readings_need_no_recalculation(ingester):
    ingester.add(reading(1, age=timedelta(days=1)), now=0.0)
    ingester.flush(generation=1)
    assert not ingester.refresh_due(10_000.0)
