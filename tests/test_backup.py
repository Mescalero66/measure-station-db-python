"""The backup program: its restic and pg_dump commands, and what it does when one fails.

restic and pg_dump aren't run: subprocess.run is replaced by a recorder.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import date

import pytest

from measure_station_db import backup, sysconfig

SUNDAY = date(2026, 11, 22)
MONDAY = date(2026, 11, 23)
VERSION_TAGS = ["postgresql-17.6", "timescaledb-2.29.2"]


class Recorder:
    """Stands in for subprocess.run: records each command, and fails the chosen ones."""

    def __init__(self, fail: str | None = None, stdout: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail = fail
        self.stdout = stdout

    def __call__(self, cmd, env=None, **kwargs):
        self.calls.append((cmd, env))
        code = 1 if _verb(cmd) == self.fail else 0
        return subprocess.CompletedProcess(cmd, code, stdout=self.stdout)

    @property
    def verbs(self) -> list[str]:
        return [_verb(cmd) for cmd, _ in self.calls]


_GLOBAL_OPTIONS = {"--repo", "--cache-dir", "--retry-lock", "--option"}


def _verb(cmd: list[str]) -> str:
    """The restic command (backup, forget, ...), skipping the global options' values."""
    values = {cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a in _GLOBAL_OPTIONS}
    return next(a for a in cmd[1:] if not a.startswith("-") and a not in values)


@pytest.fixture
def configured(monkeypatch, tmp_path, caplog):
    """Backups switched on, with every file and program present."""
    key = tmp_path / "backup-key"
    hosts = tmp_path / "backup-known-hosts"
    key.write_text("key")
    hosts.write_text("nas ssh-ed25519 AAAA")
    monkeypatch.setattr(sysconfig, "BACKUP_ENABLED", True)
    monkeypatch.setattr(sysconfig, "BACKUP_REPOSITORY", "sftp:backup@192.168.1.30:/backups/msdb")
    monkeypatch.setattr(sysconfig, "BACKUP_PASSWORD", "restic-secret")
    monkeypatch.setattr(sysconfig, "DATABASE_PASSWORD", "db-secret")
    monkeypatch.setattr(sysconfig, "BACKUP_SSH_KEY", str(key))
    monkeypatch.setattr(sysconfig, "BACKUP_KNOWN_HOSTS", str(hosts))
    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(backup, "version_tags", lambda: VERSION_TAGS)
    # Keep pytest's log capture: run() would otherwise replace the root log handlers.
    monkeypatch.setattr(backup, "configure_logging", lambda level: None)
    caplog.set_level(logging.INFO)
    return caplog


def record(monkeypatch, **kwargs) -> Recorder:
    recorder = Recorder(**kwargs)
    monkeypatch.setattr(backup.subprocess, "run", recorder)
    return recorder


# --------------------------------------------------------------------------- #
# The nightly run
# --------------------------------------------------------------------------- #


def test_switched_off_does_nothing(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "BACKUP_ENABLED", False)
    recorder = record(monkeypatch)
    assert backup.nightly(MONDAY) == 0
    assert recorder.calls == []
    assert "switched off" in configured.text


def test_each_copy_records_the_database_versions(monkeypatch, configured):
    # TimescaleDB restores only into its own version, so the copy must say which it was.
    recorder = record(monkeypatch)
    backup.nightly(MONDAY)
    cmd, _ = recorder.calls[0]
    assert cmd[cmd.index("--tag") + 1] == ",".join([backup.SNAPSHOT_TAG, *VERSION_TAGS])
    assert "timescaledb-2.29.2" in configured.text


def test_version_tags_come_from_the_database(monkeypatch, test_db):
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(test_db)
    monkeypatch.setattr(sysconfig, "DATABASE_HOST", params.get("host", "localhost"))
    monkeypatch.setattr(sysconfig, "DATABASE_PORT", int(params.get("port", 5432)))
    monkeypatch.setattr(sysconfig, "DATABASE_NAME", params["dbname"])
    monkeypatch.setattr(sysconfig, "DATABASE_USER", params.get("user", "ingest"))
    monkeypatch.setattr(sysconfig, "DATABASE_PASSWORD", params.get("password"))
    postgresql, timescaledb = backup.version_tags()
    assert re.fullmatch(r"postgresql-\d+\.\d+", postgresql)
    assert re.fullmatch(r"timescaledb-\d+\.\d+\.\d+", timescaledb)


def test_an_unreachable_database_saves_no_copy(monkeypatch, configured):
    def refuse():
        raise OSError("connection refused")

    monkeypatch.setattr(backup, "version_tags", refuse)
    recorder = record(monkeypatch)
    assert backup.nightly(MONDAY) == 1
    assert recorder.calls == []
    assert "Can't use the database (connection refused)" in configured.text


def test_a_night_backs_up_then_removes_old_copies(monkeypatch, configured):
    recorder = record(monkeypatch)
    assert backup.nightly(MONDAY) == 0
    assert recorder.verbs == ["backup", "forget"]


def test_the_weekly_check_runs_on_its_day_only(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "BACKUP_CHECK_WEEKDAY", 6)
    recorder = record(monkeypatch)
    assert backup.nightly(SUNDAY) == 0
    assert recorder.verbs == ["backup", "forget", "check"]
    check = recorder.calls[2][0]
    assert f"--read-data-subset={sysconfig.BACKUP_CHECK_READ}" in check


def test_restic_runs_pg_dump_itself(monkeypatch, configured):
    recorder = record(monkeypatch)
    backup.nightly(MONDAY)
    cmd, _ = recorder.calls[0]
    # restic starts pg_dump and saves nothing if it fails: no pipe that hides a failure.
    assert "--stdin-from-command" in cmd
    dump = cmd[cmd.index("--") + 1 :]
    assert dump == backup.pg_dump_command()
    assert cmd[cmd.index("--stdin-filename") + 1] == f"{sysconfig.DATABASE_NAME}.dump"
    assert cmd[cmd.index("--host") + 1] == backup.SNAPSHOT_HOST


def test_the_export_is_uncompressed_so_unchanged_data_is_stored_once(monkeypatch, configured):
    dump = backup.pg_dump_command()
    assert dump[dump.index("--format") + 1] == "custom"
    assert dump[dump.index("--compress") + 1] == "0"
    assert "--no-password" in dump


def test_passwords_go_in_the_environment_not_the_command_line(monkeypatch, configured):
    recorder = record(monkeypatch)
    backup.nightly(SUNDAY)
    for cmd, env in recorder.calls:
        assert not any("secret" in a for a in cmd)
        assert env["RESTIC_PASSWORD"] == "restic-secret"
        assert env["PGPASSWORD"] == "db-secret"


def test_old_copies_are_removed_by_the_keep_settings(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "BACKUP_KEEP_DAILY", 3)
    monkeypatch.setattr(sysconfig, "BACKUP_KEEP_WEEKLY", 2)
    monkeypatch.setattr(sysconfig, "BACKUP_KEEP_MONTHLY", 1)
    recorder = record(monkeypatch)
    backup.nightly(MONDAY)
    forget = recorder.calls[1][0]
    pairs = {forget[i]: forget[i + 1] for i in range(len(forget) - 1)}
    assert pairs["--keep-daily"] == "3"
    assert pairs["--keep-weekly"] == "2"
    assert pairs["--keep-monthly"] == "1"
    assert pairs["--tag"] == backup.SNAPSHOT_TAG
    assert "--prune" in forget


def test_a_failed_backup_keeps_every_old_copy(monkeypatch, configured):
    recorder = record(monkeypatch, fail="backup")
    assert backup.nightly(SUNDAY) == 1
    # Nothing is removed after a failure: the old copies may be the only good ones.
    assert recorder.verbs == ["backup"]
    assert "no copy was saved" in configured.text


def test_a_failed_check_is_reported(monkeypatch, configured):
    record(monkeypatch, fail="check")
    assert backup.nightly(SUNDAY) == 1
    assert "weekly check found a problem" in configured.text


def test_the_log_summarises_restics_report(monkeypatch, configured):
    summary = {
        "message_type": "summary",
        "data_added": 3 * 1024 * 1024,
        "total_bytes_processed": 2.5 * 1024**3,
        "total_duration": 241.7,
        "snapshot_id": "4f8e2a1bc0ffee",
    }
    record(monkeypatch, stdout='{"message_type":"status"}\nnot json\n' + json.dumps(summary))
    backup.nightly(MONDAY)
    assert (
        "Backed up the database (2.5 GiB): 3.0 MiB of new data stored, in 242 s, as copy 4f8e2a1b"
    ) in configured.text


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("BACKUP_PASSWORD", None), "MEASURE_STATION_DB_BACKUP_PASSWORD not set"),
        (("DATABASE_PASSWORD", None), "MEASURE_STATION_DB_DATABASE_PASSWORD not set"),
        (("BACKUP_SSH_KEY", "/nonexistent/backup-key"), "/nonexistent/backup-key is missing"),
    ],
)
def test_missing_settings_are_a_configuration_error(monkeypatch, configured, change, message):
    monkeypatch.setattr(sysconfig, *change)
    recorder = record(monkeypatch)
    assert backup.nightly(MONDAY) == backup.EX_CONFIG
    assert recorder.calls == []
    assert message in configured.text


def test_restic_must_be_installed(monkeypatch, configured):
    monkeypatch.setattr(backup.shutil, "which", lambda name: None if name == "restic" else name)
    record(monkeypatch)
    assert backup.nightly(MONDAY) == backup.EX_CONFIG
    assert "sudo apt install restic" in configured.text


# --------------------------------------------------------------------------- #
# The repository
# --------------------------------------------------------------------------- #


def test_the_nas_is_reached_with_the_key_and_its_recorded_host_key(monkeypatch, configured):
    cmd = backup.restic_command("snapshots")
    assert cmd[cmd.index("--repo") + 1] == sysconfig.BACKUP_REPOSITORY
    ssh = cmd[cmd.index("--option") + 1]
    assert ssh.startswith("sftp.args=")
    assert f"-i {sysconfig.BACKUP_SSH_KEY}" in ssh
    assert f"-o UserKnownHostsFile={sysconfig.BACKUP_KNOWN_HOSTS}" in ssh
    assert "-o StrictHostKeyChecking=yes" in ssh
    assert "-o BatchMode=yes" in ssh
    assert cmd[-1] == "snapshots"


def test_a_folder_repository_needs_no_ssh(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "BACKUP_REPOSITORY", "/mnt/usb/measure-station-db")
    monkeypatch.setattr(sysconfig, "BACKUP_SSH_KEY", "/nonexistent")
    assert "--option" not in backup.restic_command("snapshots")
    assert backup.config_problem(needs_database=True) is None


# --------------------------------------------------------------------------- #
# By hand
# --------------------------------------------------------------------------- #


def test_init_creates_the_repository_even_while_switched_off(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "BACKUP_ENABLED", False)
    recorder = record(monkeypatch)
    assert backup.run(["init"]) == 0
    assert recorder.verbs == ["init"]


def test_any_restic_command_runs_on_the_repository(monkeypatch, configured):
    recorder = record(monkeypatch)
    assert backup.run(["restic", "snapshots", "--compact"]) == 0
    cmd, env = recorder.calls[0]
    assert cmd == backup.restic_command("snapshots", "--compact")
    assert env["RESTIC_PASSWORD"] == "restic-secret"


def test_restics_exit_code_is_passed_on(monkeypatch, configured):
    record(monkeypatch, fail="check")
    assert backup.run(["restic", "check"]) == 1


def test_by_hand_problems_go_to_stderr_not_stdout(monkeypatch, configured, capsys):
    # stdout may be a file: sudo measure-station-db-backup restic dump ... > file
    monkeypatch.setattr(sysconfig, "BACKUP_PASSWORD", None)
    record(monkeypatch)
    assert backup.run(["restic", "snapshots"]) == backup.EX_CONFIG
    out, err = capsys.readouterr()
    assert out == ""
    assert "MEASURE_STATION_DB_BACKUP_PASSWORD not set" in err


def test_by_hand_needs_no_database_password(monkeypatch, configured):
    monkeypatch.setattr(sysconfig, "DATABASE_PASSWORD", None)
    record(monkeypatch)
    assert backup.run(["restic", "snapshots"]) == 0


@pytest.mark.parametrize("args", [["restic"], ["backup"], ["--help"]])
def test_unknown_or_incomplete_commands_show_the_usage(monkeypatch, configured, capsys, args):
    recorder = record(monkeypatch)
    assert backup.run(args) == backup.EX_USAGE
    assert recorder.calls == []
    assert "Usage: measure-station-db-backup" in capsys.readouterr().err
