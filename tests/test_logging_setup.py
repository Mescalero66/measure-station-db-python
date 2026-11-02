from __future__ import annotations

import logging

import pytest

from measure_station_db.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _format(monkeypatch, *, journald: bool) -> str:
    if journald:
        monkeypatch.setenv("JOURNAL_STREAM", "8:12345")
    else:
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    configure_logging("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    (handler,) = root.handlers
    return handler.formatter._fmt


def test_journald_lines_have_no_timestamp(monkeypatch):
    # journald adds its own timestamp to every line.
    assert "asctime" not in _format(monkeypatch, journald=True)


def test_terminal_lines_have_a_timestamp(monkeypatch):
    assert _format(monkeypatch, journald=False).startswith("%(asctime)s")
