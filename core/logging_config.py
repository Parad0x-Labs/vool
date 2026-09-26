"""Structured logging configuration for VOOL nodes.

Usage:
    from core.logging_config import setup_logging, get_logger
    setup_logging()
    logger = get_logger(__name__)
    logger.info("task_started", task_id="abc-123", task_type="research")
"""
from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON for structured log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge extra fields (set via logger.info("msg", extra={...}))
        for key in ("task_id", "peer_id", "event", "component", "details", "trace_id", "request_id"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
            # The class name and the chain of frames are the diagnosis; `str(exc)` alone
            # cannot say WHERE the raise came from (measured: the 2026-09-26 certification
            # 500 logged its message with no way to name the raising call).
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else ""
            payload["traceback"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(payload, default=str)


def setup_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure root logger with structured JSON output to stderr."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)


def route_logging_to_file(log_path: Any, *, level: str = "INFO", drop_console: bool = True) -> None:
    """Send root logging to a rotating JSON file, optionally dropping the console stream handlers.

    The API server runs as a background process whose stdout/stderr may be captured by a parent
    (launcher, daemon, benchmark) that does not drain the pipe. On Windows the pipe buffer is
    ~64KB, so once it fills, the next log write blocks — and if that write happens on the request
    thread, the whole turn wedges past a client's timeout. Routing runtime logs to a file (which
    never blocks) and dropping the console handler keeps the request path off the parent pipe.
    Best-effort: never raise, so a logging setup problem can't stop the server.
    """
    from logging.handlers import RotatingFileHandler

    try:
        from pathlib import Path

        path = Path(str(log_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        if drop_console:
            for handler in root.handlers[:]:
                # Drop console stream handlers (stdout/stderr); keep any existing file handlers.
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    root.removeHandler(handler)
        file_handler = RotatingFileHandler(
            str(path), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)
    except Exception:
        # Logging must never take down the server; leave the existing handlers in place.
        return


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Use module __name__ for best practice."""
    return logging.getLogger(name)
