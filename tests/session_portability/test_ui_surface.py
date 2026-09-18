"""The real UI surface + the served doors the page uses.

The settings overlay carries an Export & Import card (encryption default, unencrypted warning,
preview-before-import, unknown-signer acknowledgement, restored-chat link). Behind it: the raw
bundle upload door, the import-preview door, the import door with confirm_untrusted, and the
download route confined to the home's session_bundles directory.
"""

from __future__ import annotations

import json

import pytest

from core import runtime_paths
from tests.session_portability import support
from tests.session_portability.support import SESSION

UPLOAD = "/api/session/bundle/upload"
PREVIEW_IMPORT = "/api/session/bundle/inspect-import"
IMPORT = "/api/session/bundle/import"
EXPORT = "/api/session/bundle/export"
DOWNLOAD = "/api/session/bundle/download"


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


def _raw(app, path, data, headers=None):
    from tests.asgi_harness import asgi_request

    hdrs = {"Content-Type": "application/octet-stream", "Host": "127.0.0.1"}
    hdrs.update(headers or {})
    status, _, body = asgi_request(app, method="POST", path=path, headers=hdrs, body=data)
    return status, json.loads(body or b"{}")


def _get(app, path):
    from tests.asgi_harness import asgi_request

    status, headers, body = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, headers, body


def test_chat_page_carries_the_bundle_card(app):
    status, _, body = _get(app, "/chat")
    assert status == 200
    html = body.decode()
    for marker in (
        "sessionBundleSection",
        "sbExportBtn",
        "sbEncrypt",
        "sbPlainWarn",
        "sbImportFile",
        "sbPreviewBtn",
        "sbPreviewBox",
        "sbConfirmForeign",
        "sbImportBtn",
        "sbOpenRestored",
        "sbExportPreview",
        "Export preview",
        "session/bundle/upload",
        "session/bundle/inspect-import",
        "session/bundle/download",
    ):
        assert marker in html, f"chat page is missing the bundle UI element: {marker}"


def test_upload_then_preview_then_import_flow(app):
    support.seed_turns()
    support.seed_tool_receipt()
    support.seed_attachment()

    status, exported = _post(app, EXPORT, {"session_id": SESSION})
    assert status == 200 and exported["ok"] is True
    bundle_bytes = open(exported["path"], "rb").read()

    status, staged = _raw(
        app, UPLOAD, bundle_bytes, {"X-Vool-Bundle-Name": "launch.voolsession"}
    )
    assert status == 201, staged
    assert staged["ok"] is True and staged["path"].endswith(".voolsession")

    status, preview = _post(app, PREVIEW_IMPORT, {"path": staged["path"]})
    assert status == 200, preview
    assert preview["ok"] is True
    assert preview["counts"]["turns"] == 2
    assert preview["counts"]["embedded_files"] == 1
    assert preview["signature"]["trusted"] is True
    # The serving home still hosts the source session, so the preview honestly reports the
    # collision and the derived id the import would land under.
    assert preview["conflicts"]["collision"] is True
    assert preview["conflicts"]["target_session_id"] != SESSION
    assert preview["needs_confirmation"] is False

    status, imported = _post(app, IMPORT, {"path": staged["path"]})
    assert status == 200 and imported["ok"] is True
    assert imported["counts"]["turns"] == 2


def test_preview_refuses_tampered_file_before_import(app):
    import zipfile

    from core.session_portability import api

    support.seed_turns()
    src = tmp_export(app)
    members = {}
    with zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)
    payload = json.loads(members["bundle.json"])
    payload["turns"][0]["assistant"] += " (edited)"
    members["bundle.json"] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    tampered = "/tmp/sb-tampered-%s.voolsession" % __import__("uuid").uuid4().hex
    with zipfile.ZipFile(tampered, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    status, preview = _post(app, PREVIEW_IMPORT, {"path": tampered})
    assert status == 200
    assert preview["ok"] is False
    assert preview["code"] in ("BUNDLE_TAMPERED", "BUNDLE_SIGNATURE_INVALID")


def tmp_export(app) -> str:
    status, exported = _post(app, EXPORT, {"session_id": SESSION})
    assert status == 200 and exported["ok"] is True
    return exported["path"]


def test_download_route_is_confined_to_session_bundles(app, tmp_path):
    import urllib.parse

    support.seed_turns()
    path = tmp_export(app)
    from pathlib import Path

    status, headers, body = _get(
        app, "/api/session/bundle/download?path=" + urllib.parse.quote(path)
    )
    assert status == 200
    assert bytes(body) == Path(path).read_bytes()

    secret = tmp_path / "secret.txt"
    secret.write_text("not a bundle")
    status, _, body = _get(
        app, "/api/session/bundle/download?path=" + urllib.parse.quote(str(secret))
    )
    assert status == 403


def test_import_route_accepts_confirm_untrusted(app):
    """The unknown-signer acknowledgement travels as an explicit boolean on the import body."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import base64
    import hashlib
    import zipfile

    from core.session_portability import signing

    support.seed_turns()
    src = tmp_export(app)
    members = {}
    with zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)

    foreign = Ed25519PrivateKey.generate()
    pub = foreign.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    manifest = members["manifest.json"]
    sig = {
        "format": signing.SIGNATURE_FORMAT,
        "algorithm": "ed25519",
        "signer_public_key": pub,
        "signer_fingerprint": signing.fingerprint_of(pub),
        "signed": "manifest.sha256",
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "signature": base64.b64encode(foreign.sign(manifest)).decode(),
    }
    members["signature.json"] = json.dumps(sig, sort_keys=True).encode()
    foreign_path = "/tmp/sb-foreign-%s.voolsession" % __import__("uuid").uuid4().hex
    with zipfile.ZipFile(foreign_path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    status, refused = _post(app, IMPORT, {"path": foreign_path})
    assert refused["ok"] is False and refused["code"] == "BUNDLE_UNTRUSTED_SIGNER"

    status, imported = _post(
        app, IMPORT, {"path": foreign_path, "confirm_untrusted": True}
    )
    assert status == 200 and imported["ok"] is True
    assert imported["trust"]["origin"] == "foreign"
    assert imported["trust"]["trusted"] is False
