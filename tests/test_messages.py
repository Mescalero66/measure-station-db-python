from __future__ import annotations

import json
from datetime import UTC, datetime

from measure_station_db.messages import check

BASE = "measure-station"
TOPIC = "measure-station/mstn-2b/co2"
CO2 = {
    "station": "mstn-2b",
    "source": "co2",
    "sensor_type": "scd41",
    "ts": "2026-10-21T09:40:02.517+00:00",
    "ts_src": "ntp",
    "values": {"co2_ppm": 500},
}


def _payload(**changes) -> bytes:
    msg = {**CO2, **changes}
    return json.dumps({k: v for k, v in msg.items() if v is not None}).encode()


def test_every_documented_example_passes(station_examples):
    for name, example in station_examples.items():
        msg = check(example.topic, example.payload, BASE)
        assert msg.problems == [], name
        assert (msg.station, msg.source) == tuple(example.topic.split("/")[1:]), name
        assert msg.ts is not None and msg.values, name
        assert msg.size == len(example.payload), name


def test_unknown_keys_are_accepted():
    payload = _payload(values={"co2_ppm": 500, "new_key_ppm": 1}, extra_field="x")
    assert check(TOPIC, payload, BASE).problems == []


def test_status_topic_is_plain_text():
    assert check("measure-station/mstn-2b/status", b"online", BASE).status == "online"
    assert check("measure-station/mstn-2b/status", b"offline", BASE).status == "offline"
    bad = check("measure-station/mstn-2b/status", b'{"state":"online"}', BASE)
    assert bad.status is None and bad.problems


def test_problems_are_reported():
    cases = {
        "not valid JSON": (TOPIC, b"{not json"),
        "not a JSON object": (TOPIC, b"[1, 2]"),
        "doesn't match the topic": ("measure-station/mstn-2b/light", _payload()),
        "'values' is missing": (TOPIC, _payload(values=None)),
        "isn't an ISO 8601 time with a time zone": (TOPIC, _payload(ts="21/10/2026 09:40")),
        "with a time zone, e.g.": (TOPIC, _payload(ts="2026-10-21T09:40:02.517")),
        "isn't ntp, corrected or estimated": (TOPIC, _payload(ts_src="gps")),
        "without 'boot_id' and 'boot_ts'": (TOPIC, _payload(ts_src="estimated")),
        "'meta' is not an object": (TOPIC, _payload(meta=[1])),
        "unexpected topic": ("measure-station/mstn-2b/co2/extra", _payload()),
    }
    for expected, (topic, payload) in cases.items():
        problems = check(topic, payload, BASE).problems
        assert any(expected in p for p in problems), (expected, problems)


def test_estimated_with_boot_fields_is_fine():
    payload = _payload(ts_src="estimated", boot_id="b0a7", boot_ts=12.345)
    assert check(TOPIC, payload, BASE).problems == []


def test_other_iso_8601_times_are_accepted_as_utc():
    moment = datetime(2026, 10, 21, 9, 40, 2, tzinfo=UTC)
    for ts in (
        "2026-10-21T09:40:02Z",
        "2026-10-21T09:40:02+00:00",
        "2026-10-21T09:40:02.000Z",
        "2026-10-21T19:40:02+10:00",
    ):
        msg = check(TOPIC, _payload(ts=ts), BASE)
        assert msg.problems == [], ts
        assert msg.ts == moment and msg.ts.utcoffset().total_seconds() == 0, ts


def test_fields_are_parsed():
    payload = _payload(meta={"comp_src": "hpa"}, ts_src="estimated", boot_id="b0a7", boot_ts=12)
    msg = check(TOPIC, payload, BASE)
    assert msg.problems == []
    assert msg.sensor_type == "scd41" and msg.ts_src == "estimated"
    assert msg.ts == datetime(2026, 10, 21, 9, 40, 2, 517000, tzinfo=UTC)
    assert msg.values == {"co2_ppm": 500} and msg.meta == {"comp_src": "hpa"}
    assert (msg.boot_id, msg.boot_ts) == ("b0a7", 12.0)
    assert not msg.is_status
    assert check(TOPIC, _payload(), BASE).meta is None
