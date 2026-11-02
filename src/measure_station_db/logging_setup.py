"""Logging configuration, shared by all of the server's programs."""

from __future__ import annotations

import logging
import os
import sys


def configure_logging(level: str) -> None:
    """Log to stdout, which systemd captures in the journal."""
    # journald timestamps every line itself; avoid duplicating it.
    under_journald = "JOURNAL_STREAM" in os.environ
    fmt = "%(levelname)s [%(name)s] %(message)s"
    if not under_journald:
        fmt = "%(asctime)s " + fmt

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
