"""Watch the MQTT messages arriving from the stations, and check each one.

Run on the server Pi, then press Ctrl+C for a summary::

    python -m measure_station_db.watch

It prints one line per reading, checks every message against the station's interface
(the station's docs/server-pi-interface.md), and lists the sources it hasn't heard from.
It only listens: the broker sends it its own copy of each message, so it never takes
messages away from the ingest service. See docs/checking-station-messages.md.

The broker password is read from MEASURE_STATION_DB_MQTT_PASSWORD, or asked for.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from measure_station_db import sysconfig
from measure_station_db.messages import STATUS_SOURCE, Message, check

# Readings older than this are a backlog: counted, not printed one by one.
BACKLOG_AGE_S = 600
# A reading timestamped further ahead than this means the two Pis' clocks disagree.
FUTURE_TOLERANCE_S = 5
# How often to print the backlog count while a backlog arrives.
BACKLOG_NOTE_INTERVAL_S = 5


def format_age(seconds: float) -> str:
    """How long ago a reading was taken, e.g. '12 s old' or '3 h old'."""
    if seconds < 0:
        return f"{-seconds:.0f} s in the future"
    for limit, unit, size in ((90, "s", 1), (90 * 60, "min", 60), (48 * 3600, "h", 3600)):
        if seconds < limit:
            return f"{seconds / size:.0f} {unit} old"
    return f"{seconds / 86400:.0f} days old"


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


@dataclass
class SourceTally:
    received: int = 0
    problems: int = 0
    newest_ts: float | None = None


class Tally:
    """Everything received so far, for the live lines and the summary."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.sources: dict[tuple[str, str], SourceTally] = {}
        self.status: dict[str, str] = {}
        self.unexpected_topics = 0
        self.backlog = 0
        self._backlog_noted = 0.0

    def add(self, msg: Message, now: float) -> str | None:
        """Record one message, and return the line to print for it, if any."""
        clock = datetime.fromtimestamp(now).strftime("%H:%M:%S")
        if msg.station is None or msg.source is None:
            self.unexpected_topics += 1
            return f"{clock}  PROBLEM: {'; '.join(msg.problems)}"
        head = f"{clock}  {msg.station:<10} {msg.source:<13}"

        if msg.source == STATUS_SOURCE:
            if msg.status is not None:
                self.status[msg.station] = msg.status
                return f"{head} {msg.status}"
            return f"{head} PROBLEM: {'; '.join(msg.problems)}"

        tally = self.sources.setdefault((msg.station, msg.source), SourceTally())
        tally.received += 1
        ts = None if msg.ts is None else msg.ts.timestamp()
        age = None if ts is None else now - ts
        if age is not None and age < -FUTURE_TOLERANCE_S:
            msg.problems.append(
                "taken in the future: the station's and the server's clocks disagree"
            )
        if ts is not None and (tally.newest_ts is None or ts > tally.newest_ts):
            tally.newest_ts = ts
        if msg.problems:
            tally.problems += 1
            return f"{head} PROBLEM: {'; '.join(msg.problems)}"

        if age is not None and age > BACKLOG_AGE_S:
            self.backlog += 1
            if now - self._backlog_noted >= BACKLOG_NOTE_INTERVAL_S:
                self._backlog_noted = now
                plural = "s" if self.backlog != 1 else ""
                return (
                    f"{clock}  ... {self.backlog:,} older reading{plural} received so far "
                    "(a backlog: counted, not shown one by one)"
                )
            return None

        values = " ".join(f"{k}={json.dumps(v)}" for k, v in msg.values.items())
        if len(values) > 70:
            values = values[:67] + "..."
        assert age is not None
        return f"{head} {format_age(age):>10}  {msg.ts_src:<9} {msg.size:>4} B  {values}"

    def summary(self, now: float, intervals: dict[str, int]) -> list[str]:
        """The report printed when watching stops."""
        watched = now - self.started
        lines = [f"Watched for {format_duration(watched)}."]
        stations = sorted({s for s, _ in self.sources} | set(self.status))
        if not stations:
            lines.append(
                "Nothing was received from any station. "
                "See Troubleshooting in docs/checking-station-messages.md."
            )
            return lines

        total_problems = self.unexpected_topics
        for station in stations:
            seen = {src: t for (st, src), t in self.sources.items() if st == station}
            lines += ["", f"Station {station} (status: {self.status.get(station, 'not received')})"]
            lines.append(f"  {'Source':<14}{'Received':>9}  {'Newest reading':<18}Problems")
            for source in sorted(set(intervals) | set(seen)):
                t = seen.get(source)
                if t is None:
                    lines.append(f"  {source:<14}{0:>9}  {'-':<18}-")
                    continue
                newest = "-" if t.newest_ts is None else format_age(now - t.newest_ts)
                lines.append(f"  {source:<14}{t.received:>9}  {newest:<18}{t.problems}")
                total_problems += t.problems
            missing = [s for s in intervals if s not in seen]
            if missing:
                lines.append(f"  Not received: {', '.join(missing)}.")

        lines.append("")
        longest = max(intervals.values(), default=0)
        if watched < longest + 30:
            lines.append(
                f"Watch for at least {format_duration(longest + 30)} to hear from every source: "
                f"some send only every {longest} s."
            )
        if self.backlog:
            lines.append(f"{self.backlog:,} of the readings were a backlog (older than 10 min).")
        lines.append(
            f"{total_problems} message(s) had problems."
            if total_problems
            else "Every message followed the station's interface."
        )
        return lines


def main() -> None:
    import paho.mqtt.client as mqtt

    host, port = sysconfig.MQTT_HOST, sysconfig.MQTT_PORT
    username, base = sysconfig.MQTT_USERNAME, sysconfig.MQTT_BASE_TOPIC
    password = sysconfig.MQTT_PASSWORD or getpass.getpass(f"Broker password for {username}: ")

    tally = Tally(time.time())
    refused: list[str] = []

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, props: Any) -> None:
        if reason_code.is_failure:
            refused.append(str(reason_code))
            client.disconnect()
            return
        client.subscribe(f"{base}/+/+", qos=1)
        print(f"Connected to the broker at {host}:{port} as {username}.")
        print("Waiting for messages. Press Ctrl+C to stop and see the summary.\n", flush=True)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        line = tally.add(check(message.topic, message.payload, base), time.time())
        if line is not None:
            print(line, flush=True)

    # A client ID of its own, and a clean session: nothing is kept for it after it stops.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{username}-watch-{os.getpid()}",
        clean_session=True,
    )
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:
        sys.exit(f"Can't reach the broker at {host}:{port}: {exc}")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.disconnect()
    if refused:
        sys.exit(f"The broker refused the login for {username}: {refused[0]}.")
    print("\n" + "\n".join(tally.summary(time.time(), sysconfig.SOURCE_INTERVALS_S)))


if __name__ == "__main__":
    main()
