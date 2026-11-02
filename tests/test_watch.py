from __future__ import annotations

import json
from datetime import datetime

from measure_station_db import sysconfig
from measure_station_db.messages import check
from measure_station_db.watch import BACKLOG_AGE_S, Tally, format_age

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
CO2_TS = datetime.fromisoformat(CO2["ts"]).timestamp()


def _payload(**changes) -> bytes:
    msg = {**CO2, **changes}
    return json.dumps({k: v for k, v in msg.items() if v is not None}).encode()


def test_a_recent_reading_gets_a_line():
    tally = Tally(started=CO2_TS)
    line = tally.add(check(TOPIC, _payload(), BASE), now=CO2_TS + 3)
    assert line is not None
    assert "mstn-2b" in line and "co2" in line and "3 s old" in line and "co2_ppm=500" in line
    assert tally.sources["mstn-2b", "co2"].received == 1


def test_a_backlog_is_counted_not_printed_line_by_line():
    tally = Tally(started=CO2_TS)
    now = CO2_TS + BACKLOG_AGE_S + 100
    lines = [tally.add(check(TOPIC, _payload(), BASE), now=now + i / 100) for i in range(50)]
    printed = [line for line in lines if line is not None]
    assert len(printed) == 1 and "1 older reading received" in printed[0]
    assert tally.backlog == 50
    assert tally.sources["mstn-2b", "co2"].received == 50


def test_a_reading_from_the_future_is_a_problem():
    tally = Tally(started=CO2_TS)
    line = tally.add(check(TOPIC, _payload(), BASE), now=CO2_TS - 60)
    assert line is not None and "clocks disagree" in line
    assert tally.sources["mstn-2b", "co2"].problems == 1


def test_summary_lists_missing_sources():
    tally = Tally(started=CO2_TS - 300)
    tally.add(check("measure-station/mstn-2b/status", b"online", BASE), now=CO2_TS)
    tally.add(check(TOPIC, _payload(), BASE), now=CO2_TS + 1)
    text = "\n".join(tally.summary(CO2_TS + 2, sysconfig.SOURCE_INTERVALS_S))
    assert "Station mstn-2b (status: online)" in text
    assert "Not received: cpu, air-quality," in text
    assert "Every message followed the station's interface." in text
    assert "Watch for at least" not in text  # watched for 5 minutes


def test_summary_with_nothing_received():
    text = "\n".join(Tally(started=0).summary(10, sysconfig.SOURCE_INTERVALS_S))
    assert "Nothing was received" in text
    assert "Watched for 10 s." in text


def test_summary_asks_for_a_longer_watch():
    tally = Tally(started=CO2_TS)
    tally.add(check(TOPIC, _payload(), BASE), now=CO2_TS + 1)
    text = "\n".join(tally.summary(CO2_TS + 60, sysconfig.SOURCE_INTERVALS_S))
    assert "Watch for at least 3 min 0 s" in text


def test_format_age():
    assert format_age(12) == "12 s old"
    assert format_age(600) == "10 min old"
    assert format_age(3 * 3600) == "3 h old"
    assert format_age(5 * 86400) == "5 days old"
    assert format_age(-30) == "30 s in the future"
