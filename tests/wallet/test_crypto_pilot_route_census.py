"""Crypto Pilot: the setup doors are individually registered routes in the command census.

Stage 3 dispatched the five setup doors from ``handle_wallet_post`` with ``path.startswith("/api/wallet/setup/")``. The
census reads a nested prefix inside a delegated handler as a templated family, so it catalogued one unregistered
``http:POST:/api/wallet/setup/{id}`` row (LEGACY_UNMIGRATED) and never saw the five doors, although each is registered
as a delegated command. The doors must be visible one by one and owned by their registered commands; an unknown setup
path keeps answering the same not-found body it answered before.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.safety]

SETUP_DOORS = ("create", "reveal", "acknowledge", "cancel", "resume")


def test_the_setup_doors_are_individually_registered_routes_not_an_unregistered_family():
    from core.command_registry.census import run_census

    rows = {item.census_id: item for item in run_census().items}
    assert "http:POST:/api/wallet/setup/{id}" not in rows, rows.get("http:POST:/api/wallet/setup/{id}")
    for door in SETUP_DOORS:
        row = rows.get(f"http:POST:/api/wallet/setup/{door}")
        assert row is not None, f"/api/wallet/setup/{door} is not catalogued"
        assert (row.classification, row.registry_command_id) == ("GENERATED_ADAPTER", f"wallet.setup.{door}"), row


def test_an_unknown_setup_path_still_answers_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.web.api import wallet_api

    wallet_api.reset_caller_binding_for_tests()
    headers = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}
    response = wallet_api.handle_wallet_post("/api/wallet/setup/unknown-door", {}, client_host="127.0.0.1", headers=headers)
    status = getattr(response, "status_code", None) or getattr(response, "status", None)
    body = getattr(response, "body", b"")
    payload = json.loads(body if isinstance(body, (bytes, bytearray, str)) else json.dumps(body))
    assert (status, payload) == (404, {"ok": False, "error": "not found"}), (status, payload)
    wallet_api.reset_caller_binding_for_tests()
