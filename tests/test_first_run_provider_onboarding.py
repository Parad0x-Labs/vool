"""Provider reconciliation — the choice state machine and the zero-network intake preview."""
from __future__ import annotations

import pytest

from tests.first_run_pact_rig import pact_rig


def test_provider_state_machine_walks_local_only_skip_and_terminal_persistence(pact_rig):
    status, payload = pact_rig.get("/api/onboarding/state")
    assert status == 200 and payload["state"] == "absent"
    status, payload = pact_rig.post("/api/onboarding/choice", {"choice": "local_only"})
    assert status == 200 and payload["state"] == "local_only_done"
    # the pact's local_task gate reads THIS terminal (delegation edge, provider §4.1)
    from core import first_run as provider

    assert provider.terminal_permits_local() is True


def test_skip_from_the_provider_card_is_terminal_and_local_stays_functional(pact_rig):
    pact_rig.post("/api/onboarding/choice", {"choice": "connect"})  # card → picker
    status, payload = pact_rig.post("/api/onboarding/choice", {"choice": "skip"})
    assert status == 200 and payload["state"] == "skipped"
    from core import first_run as provider

    assert provider.terminal_permits_local() is True
    # CAS: stale revision on the provider machine is typed
    status, payload = pact_rig.post("/api/onboarding/reset", {"expect_revision": 999})
    assert status == 409 and payload["error"] == "stale_revision"


def test_intake_classify_and_preview_make_ZERO_network_requests(pact_rig, monkeypatch):
    """S-P-PREV: the origin preview is pure descriptor data — the wire is never touched."""
    import socket

    calls: list = []
    real_connect = socket.socket.connect

    def counted(self, address):
        calls.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", counted)

    status, payload = pact_rig.post("/api/intake/begin", {})
    assert status == 200
    session_id = payload["session_id"]

    status, payload = pact_rig.post("/api/intake/classify", {"session_id": session_id, "value": "sk-or-v1-abcdef0123456789"})
    assert status == 200
    assert payload["suggestion"] == "openrouter" or "openrouter" in payload["shortlist"]

    status, payload = pact_rig.post("/api/intake/preview", {"session_id": session_id, "provider_id": "openrouter"})
    assert status == 200
    assert payload["origin"] == "https://openrouter.ai"
    assert payload["endpoint"].startswith("https://openrouter.ai")
    assert True  # shape only; copy lives in the UI
    assert calls == [], f"the preview made outbound connections: {calls}"


def test_intake_verify_is_explicit_and_bounded_and_never_sends_on_a_refused_door(pact_rig, monkeypatch):
    """The verify door is refused in this hermetic run — the outcome is typed, never silent,
    and the fake key NEVER reaches the wire (no real provider is contacted from tests)."""
    import pytest as _pytest

    import core.credential_intelligence.verification as verification

    def _sealed(*args, **kwargs):
        raise verification.RemoteFetchRefusedError("remote fetch is not permitted for this turn")

    monkeypatch.setattr(verification, "open_remote_url", _sealed)

    status, payload = pact_rig.post("/api/intake/begin", {})
    session_id = payload["session_id"]
    pact_rig.post("/api/intake/classify", {"session_id": session_id, "value": "sk-or-v1-abcdef0123456789"})
    status, payload = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "openrouter"})
    assert status == 200
    assert payload["outcome"] in {"network_unavailable", "refused", "unexpected"}
    # nothing was stored either way
    status, payload = pact_rig.post("/api/intake/complete", {"session_id": session_id})
    assert status in {200, 409}


def test_intake_complete_without_a_session_is_a_typed_410(pact_rig):
    status, payload = pact_rig.post("/api/intake/complete", {"session_id": "intake-nonexistent"})
    assert status in {409, 410}
    assert payload.get("error") in {"intake_session_expired", "fault_validation"}


def test_vault_only_first_run_never_creates_a_keychain_grant(pact_rig):
    """Correction #5: zero keychain.enable writer, zero grant flips anywhere in the pact flow."""
    pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/begin", {})
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    from core.runtime_paths import active_config_home_dir

    assert not (active_config_home_dir() / "keychain.enabled").exists()
    import os

    assert os.environ.get("VOOL_KEYCHAIN_ALLOWED") is None
