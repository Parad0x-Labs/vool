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
    """The export FILE name is derived data, never the identity: a session id is a logical
    identity under the native namespace law (which accepts separators), and the derived name
    — bounded ASCII fragment + digest of the full id — makes every id shape safe at the join.
    This is the join-owner fix for the original escape: a planted traversal id steered the
    bundle file outside session_bundles."""
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
    assert len(out.name.encode("utf-8")) <= 255  # bounded derived name, any id shape
    # and the traversal fragment escaped nothing
    assert not (bundles_root.parent / "outside").exists()
    assert not (bundles_root.parent / "topic").exists()

    from core.session_portability import api as portability_api

    summary = portability_api.inspect_bundle(out)
    assert summary["session"]["session_id"] == evil  # the identity inside the bundle is intact


def test_native_slash_session_id_roundtrips_export_and_import(app, tmp_path):
    """The compatibility question the review named, resolved with the production-backed
    fixtures: 'review/topic' is a session id this product's namespace law accepts, so a real
    home can hold it. Served export and fresh-home import must both work, the identity must
    survive verbatim, and re-importing the exact bundle must refuse rather than fork history."""
    from pathlib import Path

    from core.session_portability import api as portability_api
    from core.session_portability.paths import scoped_home

    native = "review/topic"
    support.seed_turns(session_id=native)
    support.set_session_meta(session_id=native, title="native slash id")

    status, exported = _post(app, EXPORT, {"session_id": native})
    assert status == 200, exported
    assert exported["ok"] is True, exported

    out = Path(exported["path"])
    bundles_root = runtime_paths.data_path("session_bundles").resolve()
    assert out.is_file()
    out.resolve().relative_to(bundles_root)
    assert portability_api.inspect_bundle(out)["session"]["session_id"] == native

    fresh = tmp_path / "fresh-home"
    receipt = portability_api.import_bundle(out, home=fresh)
    assert receipt["ok"] is True, receipt
    assert receipt["imported_session_id"] == native  # no resident copy: identity verbatim

    from core.memory.entries import recent_conversation_events

    with scoped_home(fresh):
        turns = list(recent_conversation_events(native, limit=10))
    assert len(turns) == 2  # the conversation itself made the trip

    with pytest.raises(portability_api.PortabilityRefused) as err:
        portability_api.import_bundle(out, home=fresh)
    assert err.value.code == "BUNDLE_ALREADY_IMPORTED"  # idempotence preserved


def test_distinct_sessions_with_colliding_fragments_never_share_a_bundle_file(app):
    """Three distinct native ids whose sanitized fragments are identical ('review_topic')
    must still produce three distinct files: the derived name carries a digest of the FULL
    id, so no two sessions are conflated and no session silently loses its export."""
    from pathlib import Path

    ids = ("review/topic", "review topic", "review-topic")
    names = set()
    for sid in ids:
        support.seed_turns(session_id=sid)
        status, exported = _post(app, EXPORT, {"session_id": sid})
        assert status == 200, exported
        assert exported["ok"] is True, exported
        names.add(Path(exported["path"]).name)
    assert len(names) == len(ids)


def test_a_240_char_unicode_session_id_exports_a_bounded_derived_file(app):
    """The native length bound (240) and unicode identities are legal; the derived file name
    stays a bounded ASCII string whatever the identity looks like."""
    from pathlib import Path

    sid = "計画-" + "話" * 100 + "/topic"
    assert len(sid) <= 240
    support.seed_turns(session_id=sid)
    support.set_session_meta(session_id=sid, title="unicode long id")

    status, exported = _post(app, EXPORT, {"session_id": sid})
    assert status == 200, exported
    assert exported["ok"] is True, exported

    out = Path(exported["path"])
    assert len(out.name.encode("utf-8")) <= 255
    assert out.name.isascii()
    bundles_root = runtime_paths.data_path("session_bundles").resolve()
    out.resolve().relative_to(bundles_root)

    from core.session_portability import api as portability_api

    assert portability_api.inspect_bundle(out)["session"]["session_id"] == sid


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
