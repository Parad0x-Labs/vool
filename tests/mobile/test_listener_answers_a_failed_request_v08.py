"""A request that fails inside the phone listener gets a 500, not a dropped connection.

CI #716 (v0.8 line, 55fc07008): a served pairing claim on the phone socket ended in
"Remote end closed connection without response". An exception inside a request went to the
server's error handler, which closed the socket with no answer, so the phone could not tell a
failure from a lost network. The log names the failure by type and place, never its message.
"""
from __future__ import annotations

import json
import logging
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from core.mobile_companion import listener as listener_module
from core.mobile_companion import sealing


@pytest.fixture
def served():
    manager = SimpleNamespace(running=True, runtime=None, model_name="")
    server = listener_module._Server(("127.0.0.1", 0), listener_module._handler(manager))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _post(base, path, body):
    request = Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read() or b"{}")


def test_a_failing_claim_is_answered_with_a_500_and_logged_without_its_message(served, monkeypatch, caplog):
    def _fails(*_args, **_kwargs):
        raise RuntimeError("pairing pr-secret-1234 could not be stored")

    monkeypatch.setattr(sealing, "dispatch", _fails)
    with caplog.at_level(logging.ERROR, logger=listener_module.logger.name):
        status, body = _post(served, "/api/mobile/pairing/claim", {"pairing_id": "pr-secret-1234"})
    assert status == 500 and body.get("code") == "internal_error", (status, body)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in logged and "test_listener_answers_a_failed_request_v08.py" in logged, logged
    assert "pr-secret-1234" not in logged


def test_an_ordinary_bad_body_is_still_a_400(served) -> None:
    request = Request(served + "/api/mobile/pairing/claim", data=b"not json", headers={"Content-Type": "application/json"})
    try:
        urlopen(request, timeout=10)
    except HTTPError as error:
        assert error.code == 400
    else:
        raise AssertionError("a bad body must be refused")
