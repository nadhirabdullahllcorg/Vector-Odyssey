"""
Structured, single-file logging for one VO_EA process -- Phase 10's own
acceptance gate says "one process, one config, one log."

Deliberately stdlib logging rather than a new dependency: this project
only takes a dependency where a piece genuinely needs one (see
pyproject.toml's own comment about keeping the dashboard's web-framework
dependency optional), and stdlib logging is sufficient for one process
writing one file.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOGGER_NAME = "vo_ea"


def configure_logging(path: str | Path, level: str = "INFO") -> logging.Logger:
    """
    Configures the "vo_ea" logger to write to `path` (creating parent
    directories as needed) and returns it. Also attaches a stream handler
    so the same lines are visible on stdout during development -- mirrors
    VO_Transport.mqh's own "Print() AND write to file" choice on the MQL5
    side, for the same reason: a live view during development should not
    cost a durable record, or vice versa.

    Idempotent: calling this again (e.g. in a test) replaces the
    handlers rather than accumulating duplicate ones.
    """
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger
