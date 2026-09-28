"""The ONE modular seam: same core functions behind the HTTP routes and the CLI.

HTTP is driven through the REAL starlette app (the same door a browser meets); the CLI through
its real parser and dispatch. Both are thin over `core.session_portability.api`.
"""

from __future__ import annotations

import json

import pytest

from core import runtime_paths
from tests.session_portability import support
from tests.session_portability.support import SESSION

PREVIEW = "/api/session/bundle/preview"
EXPORT = "/api/session/bundle/export"
IMPORT = "/api/session/bundle/import"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def app():
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _post(app, path, payload, headers=None):
    from tests.asgi_harness import asgi_request

    hdrs = {"Content-Type": "application/json", "Host": "127.0.0.1", "Origin": "http://127.0.0.1"}
    hdrs.update(headers or {})
    status, _, body = asgi_request(
        app, method="POST", path=path, headers=hdrs, body=json.dumps(payload).encode()
    )
    return status, json.loads(body or b"{}")


@pytest.fixture()
def seeded():
    support.seed_turns()
    support.set_session_meta()
    support.seed_attachment()


def test_preview_route_serves_the_exact_export_plan(app, seeded):
    status, body = _post(app, PREVIEW, {"session_id": SESSION})
    assert status == 200, body
    assert body["session_id"] == SESSION
    assert body["counts"]["turns"] == 2


def test_export_route_writes_a_bundle_under_the_home(app, seeded):
    status, body = _post(app, EXPORT, {"session_id": SESSION})
    assert status == 200, body
    assert body["ok"] is True

    from pathlib import Path

    from core.session_portability import api

    path = Path(body["path"])
    assert path.is_file()
    summary = api.inspect_bundle(path)
    assert summary["session"]["session_id"] == SESSION


def test_export_route_sanitizes_a_path_syntax_session_id_into_the_home(app, tmp_path):
    """The export filename is DERIVED from the session id, and a session id is data a home's
    writers may have accepted (native kinds carry colons; nothing forbids other punctuation
    at birth). Path syntax is sanitized in the derived filename — never in the identity — so
    no session id can steer the bundle file outside session_bundles."""
    from pathlib import Path

    evil = "auto-research:topic/../../outside"
    support.seed_turns(session_id=evil)
    support.set_session_meta(session_id=evil, title="weird but real")

    status, body = _post(app, EXPORT, {"session_id": evil})
    assert status == 200, body
    assert body["ok"] is True, body

    out = Path(body["path"])
    bundles_root = runtime_paths.data_path("session_bundles").resolve()
    assert out.is_file()
    assert out.resolve().relative_to(bundles_root)  # the file is INSIDE the bundles root
    # and the traversal fragment escaped nothing
    assert not (bundles_root.parent / "outside").exists()
    assert not (bundles_root.parent / "topic").exists()

    from core.session_portability import api as portability_api

    summary = portability_api.inspect_bundle(out)
    assert summary["session"]["session_id"] == evil  # the identity inside the bundle is intact


def test_import_route_lands_a_bundle_in_this_home(app, seeded, tmp_path):
    from core.session_portability import api

    status, exported = _post(app, EXPORT, {"session_id": SESSION})
    assert status == 200, exported

    status, body = _post(app, IMPORT, {"path": exported["path"]})
    assert status == 200, body
    assert body["ok"] is True
    assert body["imported_session_id"] != SESSION

    from core.memory.entries import recent_conversation_events

    turns = list(recent_conversation_events(body["imported_session_id"], limit=10))
    assert len(turns) == 2


def test_import_route_refuses_a_missing_file_with_a_typed_error(app, seeded):
    status, body = _post(app, IMPORT, {"path": "/nonexistent/bundle.voolsession"})
    assert status == 200
    assert body.get("ok") is False
    assert body.get("code") == "BUNDLE_PATH_MISSING"


def test_cli_session_bundle_export_and_inspect(tmp_path, monkeypatch, capsys):
    from apps.vool_cli import main as cli_main

    support.seed_turns()
    support.seed_attachment()

    out = tmp_path / "cli.voolsession"
    rc = cli_main(
        [
            "session-bundle",
            "export",
            "--session-id",
            SESSION,
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()

    rc = cli_main(["session-bundle", "inspect", "--path", str(out)])
    assert rc == 0
    captured = capsys.readouterr().out
    assert SESSION in captured
