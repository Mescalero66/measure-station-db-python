"""Backups: the whole database, exported every night into a restic repository.

Run by a systemd timer (deploy/systemd/measure-station-db-backup.timer)::

    python -m measure_station_db.backup              the nightly backup
    python -m measure_station_db.backup init         create the repository (once)
    python -m measure_station_db.backup restic ...   any restic command on the repository

By hand, use the ``measure-station-db-backup`` command that deploy/install.sh installs:
it runs this as the service user, with the passwords from /etc/measure-station-db/env.
See docs/backups.md.

- **The export** is ``pg_dump`` in its own format, uncompressed: restic compresses it, and
  an uncompressed export lets restic recognise the parts that haven't changed since last
  night, so each night stores only the new readings.
- **restic runs pg_dump itself** (``--stdin-from-command``), so if the export fails, no
  copy is saved. A partial copy can't be mistaken for a good one.
- **Then old copies are removed** (sysconfig.BACKUP_KEEP_*), and once a week restic
  checks that part of the stored data can be read back.
- **The copies are encrypted** with BACKUP_PASSWORD before they leave the Pi.
- **Switched off by default:** with BACKUP_ENABLED False, the nightly run logs that and
  stops. ``init`` and ``restic`` work either way, to set it up first.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import date

from measure_station_db import sysconfig
from measure_station_db.logging_setup import configure_logging

# Named in full: run with "python -m", this module's __name__ is "__main__".
log = logging.getLogger("measure_station_db.backup")

EX_CONFIG = 78  # a configuration error: needs a person, not a retry
EX_USAGE = 64

# Recorded in every copy instead of the Pi's hostname, so a replacement Pi carries on
# the same series of copies.
SNAPSHOT_HOST = "measure-station-db"
SNAPSHOT_TAG = "database"

USAGE = """\
Usage: measure-station-db-backup [init | restic ARGS...]
  (nothing)     make a backup now, as the nightly timer does
  init          create the backup repository (once)
  restic ARGS   run a restic command on the repository, e.g. "restic snapshots"
See docs/backups.md."""


def dump_filename() -> str:
    """The export's name inside each copy."""
    return f"{sysconfig.DATABASE_NAME}.dump"


def pg_dump_command() -> list[str]:
    """Export the whole database to stdout. The password goes in the environment."""
    return [
        "pg_dump",
        "--host",
        sysconfig.DATABASE_HOST,
        "--port",
        str(sysconfig.DATABASE_PORT),
        "--username",
        sysconfig.DATABASE_USER,
        "--dbname",
        sysconfig.DATABASE_NAME,
        "--no-password",
        "--format",
        "custom",
        "--compress",
        "0",
    ]


def restic_command(*args: str) -> list[str]:
    """A restic command on the configured repository."""
    cmd = [
        "restic",
        "--repo",
        sysconfig.BACKUP_REPOSITORY,
        "--cache-dir",
        sysconfig.BACKUP_CACHE_DIR,
        # Wait for another restic run (e.g. one started by hand) instead of failing.
        "--retry-lock",
        "30m",
    ]
    if sysconfig.BACKUP_REPOSITORY.startswith("sftp:"):
        ssh_args = " ".join(
            [
                f"-i {sysconfig.BACKUP_SSH_KEY}",
                "-o IdentitiesOnly=yes",
                # Never ask anything: there's nobody to answer at night.
                "-o BatchMode=yes",
                # Only the NAS whose key was recorded at setup (docs/backups.md).
                "-o StrictHostKeyChecking=yes",
                f"-o UserKnownHostsFile={sysconfig.BACKUP_KNOWN_HOSTS}",
                # Notice a NAS that stops answering mid-backup, rather than wait forever.
                "-o ServerAliveInterval=30",
            ]
        )
        cmd += ["--option", f"sftp.args={ssh_args}"]
    return [*cmd, *args]


def environment() -> dict[str, str]:
    """restic's and pg_dump's passwords, kept out of the command lines (which anyone on
    the Pi can see)."""
    env = dict(os.environ)
    env["RESTIC_PASSWORD"] = sysconfig.BACKUP_PASSWORD or ""
    env["PGPASSWORD"] = sysconfig.DATABASE_PASSWORD or ""
    return env


def config_problem(*, needs_database: bool) -> str | None:
    """What's missing before restic can run, or None."""
    if shutil.which("restic") is None:
        return "restic isn't installed (sudo apt install restic)"
    if needs_database and shutil.which("pg_dump") is None:
        return "pg_dump isn't installed (it comes with PostgreSQL)"
    if not sysconfig.BACKUP_PASSWORD:
        return "MEASURE_STATION_DB_BACKUP_PASSWORD not set (in /etc/measure-station-db/env)"
    if needs_database and not sysconfig.DATABASE_PASSWORD:
        return "MEASURE_STATION_DB_DATABASE_PASSWORD not set (in /etc/measure-station-db/env)"
    if sysconfig.BACKUP_REPOSITORY.startswith("sftp:"):
        for path in (sysconfig.BACKUP_SSH_KEY, sysconfig.BACKUP_KNOWN_HOSTS):
            if not os.path.isfile(path):
                return f"{path} is missing (docs/backups.md, Step 3)"
    return None


def size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError("unreachable")


def summary_line(output: str) -> str:
    """One log line from ``restic backup --json``'s summary message."""
    for line in output.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("message_type") == "summary":
            return (
                f"Backed up the database ({size(msg.get('total_bytes_processed', 0))}): "
                f"{size(msg.get('data_added', 0))} of new data stored, "
                f"in {msg.get('total_duration', 0):.0f} s, "
                f"as copy {msg.get('snapshot_id', '?')[:8]}"
            )
    return "Backed up the database"


def version_tags() -> list[str]:
    """Tags recording the PostgreSQL and TimescaleDB versions, e.g. ["postgresql-17.6",
    "timescaledb-2.29.2"]: a backup can only be restored into the same TimescaleDB
    version (docs/backups.md, "Restoring After a Failure")."""
    import psycopg

    with psycopg.connect(
        host=sysconfig.DATABASE_HOST,
        port=sysconfig.DATABASE_PORT,
        dbname=sysconfig.DATABASE_NAME,
        user=sysconfig.DATABASE_USER,
        password=sysconfig.DATABASE_PASSWORD,
        connect_timeout=10,
        application_name="measure-station-db-backup",
    ) as conn:
        row = conn.execute(
            "SELECT current_setting('server_version_num')::int,"
            " (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb')"
        ).fetchone()
    number, timescaledb = row if row else (0, None)
    tags = [f"postgresql-{number // 10000}.{number % 10000}"]
    if timescaledb:
        tags.append(f"timescaledb-{timescaledb}")
    return tags


def nightly(today: date | None = None) -> int:
    """Back up, remove old copies, and once a week check the stored data."""
    if not sysconfig.BACKUP_ENABLED:
        log.info("Backups are switched off (BACKUP_ENABLED in sysconfig.py); stopping")
        return 0
    problem = config_problem(needs_database=True)
    if problem:
        log.error("Configuration error: %s", problem)
        return EX_CONFIG
    env = environment()
    try:
        tags = [SNAPSHOT_TAG, *version_tags()]
    except Exception as exc:  # psycopg.Error, or anything else on the way
        log.error("Can't use the database (%s); no copy was saved", exc)
        return 1

    log.info(
        "Backing up %s (%s) to %s",
        sysconfig.DATABASE_NAME,
        ", ".join(tags[1:]),
        sysconfig.BACKUP_REPOSITORY,
    )
    backup = subprocess.run(
        restic_command(
            "backup",
            "--json",
            "--host",
            SNAPSHOT_HOST,
            "--tag",
            ",".join(tags),
            "--stdin-filename",
            dump_filename(),
            "--stdin-from-command",
            "--",
            *pg_dump_command(),
        ),
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if backup.returncode != 0:
        log.error(
            "The backup failed (restic exit code %d); no copy was saved. See the messages above",
            backup.returncode,
        )
        return 1
    log.info("%s", summary_line(backup.stdout))

    keep = [
        "--keep-daily",
        str(sysconfig.BACKUP_KEEP_DAILY),
        "--keep-weekly",
        str(sysconfig.BACKUP_KEEP_WEEKLY),
        "--keep-monthly",
        str(sysconfig.BACKUP_KEEP_MONTHLY),
    ]
    forget = subprocess.run(
        restic_command("forget", "--quiet", "--tag", SNAPSHOT_TAG, *keep, "--prune"),
        env=env,
        check=False,
    )
    if forget.returncode != 0:
        log.error(
            "Removing old copies failed (restic exit code %d). Tonight's copy is saved",
            forget.returncode,
        )
        return 1
    log.info(
        "Kept the copies of the last %d days, %d weeks and %d months",
        sysconfig.BACKUP_KEEP_DAILY,
        sysconfig.BACKUP_KEEP_WEEKLY,
        sysconfig.BACKUP_KEEP_MONTHLY,
    )

    if (today or date.today()).weekday() == sysconfig.BACKUP_CHECK_WEEKDAY:
        check = subprocess.run(
            restic_command("check", "--quiet", f"--read-data-subset={sysconfig.BACKUP_CHECK_READ}"),
            env=env,
            check=False,
        )
        if check.returncode != 0:
            log.error(
                "The weekly check found a problem with the stored copies (restic exit "
                "code %d). See docs/backups.md, Troubleshooting",
                check.returncode,
            )
            return 1
        log.info("Weekly check: the stored copies are readable")
    return 0


def by_hand(args: list[str]) -> int:
    """``init`` or ``restic ...``, run from the measure-station-db-backup command.

    Messages go to stderr: stdout may be a file (e.g. ``restic dump ... > file``).
    """
    problem = config_problem(needs_database=False)
    if problem:
        print(f"Configuration error: {problem}", file=sys.stderr)
        return EX_CONFIG
    restic_args = ["init"] if args[0] == "init" else args[1:]
    if not restic_args:
        print(USAGE, file=sys.stderr)
        return EX_USAGE
    return subprocess.run(restic_command(*restic_args), env=environment(), check=False).returncode


def run(args: list[str]) -> int:
    if not args:
        configure_logging(sysconfig.LOG_LEVEL)
        return nightly()
    if args[0] in ("init", "restic"):
        return by_hand(args)
    print(USAGE, file=sys.stderr)
    return EX_USAGE


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
