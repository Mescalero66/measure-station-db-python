"""Parsing and checking the messages the stations send.

The rules are the station's interface (its docs/server-pi-interface.md), which is
fixed. Both the ingest service and the watch tool use this module, so they always agree
on what a valid message is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

STATUS_SOURCE = "status"

_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")
_REQUIRED: dict[str, type] = {
    "station": str,
    "source": str,
    "sensor_type": str,
    "ts": str,
    "ts_src": str,
    "values": dict,
}
_TS_SRC = {"ntp", "corrected", "estimated"}


@dataclass
class Message:
    """One received message, and anything wrong with it.

    A message with problems must not be stored. A status message (source ``status``) has
    only ``status`` set.
    """

    station: str | None
    source: str | None
    size: int
    status: str | None = None  # "online" or "offline", for the status topic
    sensor_type: str | None = None
    ts: datetime | None = None  # capture time, UTC
    ts_src: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] | None = None  # None when the message has no meta
    boot_id: str | None = None
    boot_ts: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def is_status(self) -> bool:
        return self.source == STATUS_SOURCE


def check(topic: str, payload: bytes, base_topic: str) -> Message:
    """Parse one message and check it against the station's interface."""
    levels = topic.split("/")
    if len(levels) != 3 or levels[0] != base_topic:
        return Message(None, None, len(payload), problems=[f"unexpected topic {topic!r}"])
    station, source = levels[1], levels[2]
    msg = Message(station, source, len(payload))

    if source == STATUS_SOURCE:
        text = payload.decode("utf-8", "replace")
        if text in ("online", "offline"):
            msg.status = text
        else:
            msg.problems.append(f"status is {text!r}, not 'online' or 'offline'")
        return msg

    try:
        data = json.loads(payload)
    except ValueError:
        msg.problems.append("not valid JSON")
        return msg
    if not isinstance(data, dict):
        msg.problems.append("not a JSON object")
        return msg

    for key, kind in _REQUIRED.items():
        if not isinstance(data.get(key), kind):
            msg.problems.append(f"'{key}' is missing or not a {kind.__name__}")
    if data.get("station") != station or data.get("source") != source:
        msg.problems.append("the payload's station or source doesn't match the topic")
    if not _ID.match(station) or not _ID.match(source):
        msg.problems.append("the station or source contains characters it mustn't")
    if isinstance(data.get("sensor_type"), str):
        msg.sensor_type = data["sensor_type"]
    if "meta" in data:
        if isinstance(data["meta"], dict):
            msg.meta = data["meta"]
        else:
            msg.problems.append("'meta' is not an object")

    ts = data.get("ts")
    if isinstance(ts, str):
        if _TS.match(ts):
            msg.ts = datetime.fromisoformat(ts)
        else:
            msg.problems.append(f"'ts' {ts!r} isn't in the form 2026-10-21T09:40:00.000+00:00")

    ts_src = data.get("ts_src")
    if isinstance(ts_src, str):
        msg.ts_src = ts_src
        if ts_src not in _TS_SRC:
            msg.problems.append(f"'ts_src' {ts_src!r} isn't ntp, corrected or estimated")
        elif ts_src == "estimated":
            boot_id, boot_ts = data.get("boot_id"), data.get("boot_ts")
            if isinstance(boot_id, str) and isinstance(boot_ts, int | float):
                msg.boot_id, msg.boot_ts = boot_id, float(boot_ts)
            else:
                msg.problems.append("an estimated 'ts' without 'boot_id' and 'boot_ts'")

    if isinstance(data.get("values"), dict):
        msg.values = data["values"]
    return msg
