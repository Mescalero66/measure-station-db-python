"""The database's tables, applied by the ingest service every time it starts.

Every statement must be safe to repeat. They run one at a time with autocommit, because
TimescaleDB won't create a continuous aggregate inside a transaction.

Tables:
    reading:         one row per message, as received. The source of truth: everything
                     else can be rebuilt from it.
    reading_value:   one row per value in ``values`` and ``meta``, for charts. Numbers
                     and booleans (as 1 or 0) go in num_value; text, and the odd list, in
                     text_value; null leaves both empty.
    station_status:  each change of a station's ``status`` (online/offline), with the
                     time the server stored it. The station's status carries no time.
    value_hourly,    average, minimum, maximum and count of each numeric series per hour
    value_daily:     and per (UTC) day. TimescaleDB keeps them up to date. Readings with
                     an ``estimated`` time are left out: their time can be weeks wrong.

The aggregates' automatic refresh only looks back a few days (START_OFFSET below). The
ingest service refreshes older periods itself when late readings arrive
(sysconfig.INGEST_LATE_AFTER_S must be shorter than the hourly START_OFFSET).

Data is kept forever. Chunks older than COMPRESS_AFTER are compressed; that's longer
than a station's longest backlog (about 3 months), so late readings don't land in
compressed chunks.
"""

from __future__ import annotations

CHUNK = "INTERVAL '7 days'"
COMPRESS_AFTER = "INTERVAL '120 days'"
HOURLY_START_OFFSET = "INTERVAL '3 days'"
DAILY_START_OFFSET = "INTERVAL '7 days'"

AGGREGATES = ("value_hourly", "value_daily")


def _aggregate(name: str, bucket: str) -> str:
    return f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {name}
        WITH (timescaledb.continuous) AS
        SELECT station, source, section, key,
               time_bucket(INTERVAL '{bucket}', ts) AS bucket,
               avg(num_value) AS avg_value,
               min(num_value) AS min_value,
               max(num_value) AS max_value,
               count(num_value) AS n
        FROM reading_value
        WHERE num_value IS NOT NULL AND ts_src <> 'estimated'
        GROUP BY station, source, section, key, bucket
        WITH NO DATA
    """


def _compression(table: str, segment_by: str) -> str:
    # Changing compression settings fails once chunks are compressed, so only the first
    # run (before the policy exists) sets them.
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM timescaledb_information.jobs
                WHERE proc_name = 'policy_compression' AND hypertable_name = '{table}'
            ) THEN
                ALTER TABLE {table} SET (
                    timescaledb.enable_columnstore = true,
                    timescaledb.segmentby = '{segment_by}',
                    timescaledb.orderby = 'ts DESC'
                );
                CALL add_columnstore_policy('{table}', after => {COMPRESS_AFTER});
            END IF;
        END
        $$
    """


STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS reading (
        station     TEXT             NOT NULL,
        source      TEXT             NOT NULL,
        ts          TIMESTAMPTZ      NOT NULL,
        sensor_type TEXT             NOT NULL,
        ts_src      TEXT             NOT NULL,
        values_json JSONB            NOT NULL,
        meta_json   JSONB,
        boot_id     TEXT,
        boot_ts     DOUBLE PRECISION,
        received_at TIMESTAMPTZ      NOT NULL,
        PRIMARY KEY (station, source, ts)
    )
    """,
    f"SELECT create_hypertable('reading', by_range('ts', {CHUNK}), if_not_exists => TRUE)",
    """
    CREATE TABLE IF NOT EXISTS reading_value (
        station    TEXT             NOT NULL,
        source     TEXT             NOT NULL,
        section    TEXT             NOT NULL,
        key        TEXT             NOT NULL,
        ts         TIMESTAMPTZ      NOT NULL,
        ts_src     TEXT             NOT NULL,
        num_value  DOUBLE PRECISION,
        text_value TEXT,
        PRIMARY KEY (station, source, section, key, ts)
    )
    """,
    f"SELECT create_hypertable('reading_value', by_range('ts', {CHUNK}), if_not_exists => TRUE)",
    """
    CREATE TABLE IF NOT EXISTS station_status (
        station     TEXT        NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        status      TEXT        NOT NULL,
        PRIMARY KEY (station, received_at)
    )
    """,
    _aggregate("value_hourly", "1 hour"),
    _aggregate("value_daily", "1 day"),
    f"""
    SELECT add_continuous_aggregate_policy('value_hourly',
        start_offset => {HOURLY_START_OFFSET}, end_offset => INTERVAL '1 hour',
        schedule_interval => INTERVAL '30 minutes', if_not_exists => TRUE)
    """,
    f"""
    SELECT add_continuous_aggregate_policy('value_daily',
        start_offset => {DAILY_START_OFFSET}, end_offset => INTERVAL '1 day',
        schedule_interval => INTERVAL '6 hours', if_not_exists => TRUE)
    """,
    _compression("reading", "station, source"),
    _compression("reading_value", "station, source, section, key"),
]
