from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# The live database. The test_db fixture refuses to run against it.
LIVE_DBNAME = "measure_station_db"


@dataclass(frozen=True)
class StationExample:
    """One example message from the station's documentation, as it arrives on the wire."""

    name: str
    source_doc: str  # where it came from, in the station repo
    topic: str
    payload: bytes


def load_station_examples() -> dict[str, StationExample]:
    """The example messages documented by the station, keyed by name.

    Copied verbatim (compacted, as the station sends them) from
    github.com/Mescalero66/measure-station-python: docs/server-pi-interface.md and the
    pages in docs/sensors/. The station's interface is fixed, so these are the reference
    for what the server must accept. They're copied rather than read from the station
    repo, because the database tests run on the server Pi, where that repo isn't present.
    """
    path = Path(__file__).parent / "fixtures" / "station-examples.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: StationExample(name, e["from"], e["topic"], e["payload"].encode("utf-8"))
        for name, e in raw.items()
    }


@pytest.fixture
def station_examples() -> dict[str, StationExample]:
    return load_station_examples()


# Removes everything the schema creates, so each database test starts empty.
_DROP = [
    "DROP MATERIALIZED VIEW IF EXISTS value_hourly, value_daily CASCADE",
    "DROP TABLE IF EXISTS reading, reading_value, station_status CASCADE",
]


@pytest.fixture
def test_db() -> Iterator[str]:
    """The test database's connection string, or skip the test when there isn't one.

    Set MEASURE_STATION_DB_TEST_DSN to a libpq connection string for a separate test
    database (see docs/development.md). The server's tables are dropped first, so each
    test starts from an empty database. (Rolling back can't be used instead: TimescaleDB
    won't create a continuous aggregate inside a transaction.)
    """
    dsn = os.environ.get("MEASURE_STATION_DB_TEST_DSN")
    if not dsn:
        pytest.skip("no test database (MEASURE_STATION_DB_TEST_DSN is not set)")
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.info.dbname == LIVE_DBNAME:
            pytest.fail(f"refusing to run tests against the live database {LIVE_DBNAME!r}")
        for statement in _DROP:
            conn.execute(statement)
    yield dsn
