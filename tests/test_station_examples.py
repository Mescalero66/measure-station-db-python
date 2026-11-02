"""Checks that the copied station examples match the interface's rules.

Later tests feed these examples to the server, so a mistake in copying them would hide
real bugs. The rules are from the station's docs/server-pi-interface.md.
"""

from __future__ import annotations

import json
import re

ID = re.compile(r"^[A-Za-z0-9_.-]+$")
TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")
REQUIRED = {"station", "source", "sensor_type", "ts", "ts_src", "values"}
OPTIONAL = {"meta", "boot_id", "boot_ts"}


def test_every_source_with_a_documented_example_is_present(station_examples):
    # cpu has no JSON example in the station's docs.
    assert set(station_examples) == {
        "air-quality",
        "barometer",
        "co2",
        "health",
        "humidity",
        "light",
        "mqboard-mq4",
        "particulates-sen62",
        "particulates-sen65",
        "temperature",
    }


def test_examples_follow_the_interface(station_examples):
    for name, example in station_examples.items():
        msg = json.loads(example.payload)
        assert REQUIRED <= msg.keys() <= REQUIRED | OPTIONAL, name
        assert ID.match(msg["station"]) and ID.match(msg["source"]), name
        assert example.topic == f"measure-station/{msg['station']}/{msg['source']}", name
        assert msg["source"] != "status", name
        assert TS.match(msg["ts"]), name
        assert msg["ts_src"] in {"ntp", "corrected", "estimated"}, name
        assert isinstance(msg["values"], dict) and msg["values"], name
        assert isinstance(msg.get("meta", {}), dict), name


def test_examples_are_compact_like_the_station_sends(station_examples):
    for name, example in station_examples.items():
        msg = json.loads(example.payload)
        compact = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert example.payload == compact, name
