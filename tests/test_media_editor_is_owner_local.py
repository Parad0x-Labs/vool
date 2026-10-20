"""Media Studio is the owner's own editor: a peer on the network never reaches it.

Before: the /media-editor doors had no owner check. A daemon started with --bind on a LAN address
(and a peer sending ``Host: 127.0.0.1``, which the host guard accepts) could open the editor page,
open any file path as a project, read its media back through the proxy and export to any path.
Now every /media-editor door, read or write, refuses a non-loopback peer before the editor code runs,
like the other owner-local doors; the owner's own loopback session is unchanged.
"""
from __future__ import annotations

import json

import pytest

from core.web.api import media_editor_api
from core.web.api.service import dispatch_get
from tests.first_run_pact_rig import pact_rig

PEERS = ["192.168.1.20", "10.9.9.9", "", "::ffff:192.0.2.7"]


@pytest.fixture
def editor_calls(monkeypatch):
    calls: list[str] = []

    def record(name):
        def handler(path, *args, **kwargs):
            calls.append(f"{name} {path}")
            return media_editor_api._ok({"recorded": True})
        return handler

    monkeypatch.setattr(media_editor_api, "handle_media_editor_post", record("post"))
    monkeypatch.setattr(media_editor_api, "handle_media_editor_get", record("get"))
    return calls


@pytest.mark.parametrize("peer", PEERS)
def test_a_network_peer_is_refused_at_every_media_editor_door(pact_rig, editor_calls, peer) -> None:
    for path in ("/media-editor", "/media-editor/project", "/media-editor/proxy"):
        response = dispatch_get(path=path, query={"project_id": ["p1"]}, runtime=pact_rig.runtime,
                                model_name="vool", client_host=peer)
        assert response.status == 403, (path, peer)
        assert json.loads(response.body)["error"] == "owner_local_required"
    for action in ("open", "op", "export", "undo"):
        status, payload = pact_rig.post(f"/media-editor/{action}",
                                        {"source_path": "/etc/hosts", "project_id": "p1", "output_path": "/tmp/x"},
                                        client_host=peer)
        assert status == 403 and payload["error"] == "owner_local_required", (action, peer)
    assert editor_calls == []


def test_the_owner_on_loopback_still_reaches_the_editor(pact_rig, editor_calls) -> None:
    for host in ("127.0.0.1", "::1"):
        response = dispatch_get(path="/media-editor/project", query={"project_id": ["p1"]},
                                runtime=pact_rig.runtime, model_name="vool", client_host=host)
        assert response.status == 200, host
        status, _ = pact_rig.post("/media-editor/open", {"source_path": "clip.mp4"}, client_host=host)
        assert status == 200, host
    assert editor_calls == ["get /media-editor/project", "post /media-editor/open",
                            "get /media-editor/project", "post /media-editor/open"]
