"""Regression: the API server must not block the request path on an undrained parent
stdout/stderr pipe (Windows ~64KB pipe-buffer deadlock). route_logging_to_file sends
runtime logs to a file and drops the console stream handler, so heavy logging during a
turn writes to the file (which never blocks) instead of the parent pipe.
"""
from __future__ import annotations

import logging

from core.logging_config import route_logging_to_file


def _restore_root_handlers(saved):
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in saved:
        root.addHandler(handler)


def test_route_logging_to_file_drops_console_and_writes_to_file(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    stderr_handler = logging.StreamHandler()  # a console handler that could block on a full pipe
    root.addHandler(stderr_handler)
    log_path = tmp_path / "logs" / "vool_api.log"
    try:
        route_logging_to_file(log_path)

        # console stream handler dropped; a rotating file handler for our path installed
        assert stderr_handler not in root.handlers
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert any(str(getattr(h, "baseFilename", "")).endswith("vool_api.log") for h in file_handlers)
        # no console stream handler remains that could block on a full parent pipe
        assert not [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]

        logging.getLogger("vool.test").warning("a log line during a turn")
        for handler in root.handlers:
            handler.flush()
        assert log_path.exists()
        assert "a log line during a turn" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                handler.close()
        _restore_root_handlers(saved)
        root.setLevel(saved_level)


def test_route_logging_to_file_is_best_effort_on_bad_path(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        # A path whose parent cannot be created (a file used as a directory) must not raise.
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        route_logging_to_file(blocker / "sub" / "vool_api.log")  # should swallow the error
    finally:
        for handler in root.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                handler.close()
        _restore_root_handlers(saved)
