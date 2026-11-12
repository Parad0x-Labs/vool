"""Real document readers must never stage saved plaintext outside the private home."""
from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.artifact_readers.office import OLE_MAGIC, doc_converter_available
from core.artifact_readers.pdf import decoders_available
from tests.my_things.test_served import call, daemon, file
from tests.reader_fixtures import legacy_doc, native_text_pdf


@pytest.mark.parametrize("extension", ["pdf", "doc"])
def test_served_document_scratch_modes_cleanup_refusal_and_erase(daemon, extension):
    base, home = daemon
    marker = "ELMWOOD-78319"
    available = decoders_available()["text_layer"] if extension == "pdf" else doc_converter_available()
    data = native_text_pdf(marker=marker) if extension == "pdf" else legacy_doc(marker=marker) or OLE_MAGIC + b"\0" * 4096
    root = home / "data/my_things/read_scratch"
    observed = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call, base, "save", {"fields": {"name": "Saved document"}, "file": file("receipt." + extension, data), "confirm": True})
        while not pending.done():
            for path in root.rglob("input." + extension) if root.exists() else []:
                try:
                    assert path.resolve().is_relative_to(home.resolve())
                    assert path.stat().st_mode & 0o777 == 0o600
                    assert path.parent.stat().st_mode & 0o777 == 0o700
                    observed.append(str(path.relative_to(home)))
                except FileNotFoundError:
                    pass
            time.sleep(.001)
        status, saved, _ = pending.result()
    assert status == 200, saved
    if available:
        assert observed, "the actual decoder input must be observed under the home"
        assert call(base, "forget", {"target": marker})[1]["matches"][0]["id"] == saved["thing"]["id"]
    assert not list(root.rglob("*"))
    broken = b"%PDF-1.7\nnot a complete PDF" if extension == "pdf" else OLE_MAGIC + b"\0" * 4096
    result = call(base, "read", {"file": file("broken." + extension, broken)})[1]["reading"]
    assert result["state"] in {"unreadable", "unavailable"}, result
    assert not list(root.rglob("*")), "refused document bytes must also be removed"
    assert call(base, "delete", {"id": saved["thing"]["id"], "revision": 1, "confirm": True})[0] == 200
    for path in home.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert marker.encode() not in raw and marker.encode("utf-16le") not in raw
            assert base64.b64encode(marker.encode()) not in raw


def test_document_worker_uses_the_configured_profile_without_changing_server_temp(tmp_path, monkeypatch):
    import os
    import tempfile

    from core.my_things.reading import read_local
    from core.runtime_paths import active_vool_home, configure_runtime_home

    prior_home = active_vool_home()
    env_home = tmp_path / "environment-home"
    selected_home = tmp_path / "selected-home"
    monkeypatch.setenv("VOOL_HOME", str(env_home))
    original_tmpdir = os.environ.get("TMPDIR")
    original_temp = tempfile.gettempdir()
    configure_runtime_home(selected_home)
    try:
        reading = read_local(native_text_pdf(marker="WILLOW-38294"), "receipt.pdf")
        assert reading["state"] == ("read" if decoders_available()["text_layer"] else "unavailable"), reading
        assert not list((selected_home / "data/my_things/read_scratch").rglob("*"))
        assert not env_home.exists()
        assert os.environ.get("TMPDIR") == original_tmpdir and tempfile.gettempdir() == original_temp
    finally:
        configure_runtime_home(prior_home)
