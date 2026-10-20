"""Logging setup for Chris."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

CHRIS_LOGGER = "chris"


def setup_logging(log_file: str | Path, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(CHRIS_LOGGER)
    logger.setLevel(level.upper())
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    has_console = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    if not has_console:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)
    if not has_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    return logger
