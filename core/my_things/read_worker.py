"""One document read with private TMPDIR and umask, using the shared readers unchanged."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from core.artifact_readers import read
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable
from core.my_things.reading import _summarize
from core.runtime_paths import active_data_dir


def main():
    scratch = Path(os.environ["TMPDIR"]).resolve()
    root = (active_data_dir() / "my_things/read_scratch").resolve()
    if not scratch.is_relative_to(root) or Path(tempfile.gettempdir()).resolve() != scratch:
        raise RuntimeError("Private document scratch is unavailable.")
    try:
        result = _summarize(read(sys.stdin.buffer.read(), name=sys.argv[1], extension=sys.argv[2]))
    except ReaderUnavailable:
        result = {"error": "unavailable"}
    except (ReaderFailed, ReaderRefused):
        result = {"error": "unreadable"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
