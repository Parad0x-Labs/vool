"""A6 resolution surface (NIA-006): the UNKNOWN-unstick endpoints.

The store already knew how to resolve unresolved effects (mechanical/provider/
user, never model) — but nothing could even LIST the stuck rows, and the owner
had no way to submit a user resolution. These tests pin the new surface:

- GET  /api/runtime/unresolved-effects          — list active rows + history
- POST /api/runtime/unresolved-effects/resolve  — owner resolves with evidence

Deterministic: seeds the store directly (reserve → dispatch → unknown), zero
model calls, zero network. Storage isolation comes from the conftest reset.
"""
from __future__ import annotations

import json

import pytest

from core.runtime_continuity import (
    find_active_unresolved_effect,
    list_active_unresolved_effects,
    list_unresolved_effect_resolutions,
    mark_effect_dispatched,
    reserve_logical_effect,
)
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

_INTENT = "channel.send_message"
_ARGS = {"to": "bob", "text": "a6-surface proof"}


@pytest.fixture()
def stuck_effect():
    """One UNKNOWN effect: dispatched, outcome unprovable — blocks forever until resolved."""
    reservation = reserve_logical_effect(intent=_INTENT, arguments=dict(_ARGS))
    effect_id = reservation["logical_effect_id"]
    instance = reservation["effect_instance_id"]
    assert mark_effect_dispatched(
        logical_effect_id=effect_id, effect_instance_id=instance, claimed_by="test"
    )
    from core.runtime_continuity import classify_effect_outcome

    assert classify_effect_outcome(
        logical_effect_id=effect_id,
        effect_instance_id=instance,
        outcome="unknown",
        reason="provider dropped the connection mid-send",
    )
    return effect_id


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get():
    return dispatch_get(
        path="/api/runtime/unresolved-effects", query={}, runtime=_rt(), model_name="vool"
    )


def _post(body, client_host="127.0.0.1"):
    return dispatch_post(
        path="/api/runtime/unresolved-effects/resolve",
        body=body,
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_lister_returns_the_stuck_row(stuck_effect):
    rows = list_active_unresolved_effects()
    assert any(r["logical_effect_id"] == stuck_effect for r in rows)
    resp = _get()
    assert resp.status == 200
    payload = _payload(resp)
    assert payload["ok"] is True
    match = [r for r in payload["unresolved_effects"] if r["logical_effect_id"] == stuck_effect]
    assert match and match[0]["tool_name"] == _INTENT
    assert "resolutions" in match[0]


def test_user_resolution_releases_the_block(stuck_effect):
    resp = _post(
        {
            "logical_effect_id": stuck_effect,
            "resolution": "CONFIRMED_FAILED_SAFE_TO_RETRY",
            "source": "user",
            "evidence": "owner checked the channel: no message was sent",
        }
    )
    assert resp.status == 200, _payload(resp)
    assert _payload(resp)["ok"] is True
    # The block is gone and the immutable history records who resolved and why.
    assert find_active_unresolved_effect(stuck_effect) is None
    history = list_unresolved_effect_resolutions(stuck_effect)
    assert history and history[-1]["source"] == "user"
    assert history[-1]["resolved_by"].startswith("owner:")


def test_model_is_refused_as_a_source(stuck_effect):
    resp = _post(
        {
            "logical_effect_id": stuck_effect,
            "resolution": "CONFIRMED_APPLIED",
            "source": "model",
            "evidence": "the model thinks it worked",
        }
    )
    assert resp.status == 400
    # And nothing changed — the row is still stuck, exactly as before.
    assert find_active_unresolved_effect(stuck_effect) is not None


def test_resolution_without_evidence_is_refused(stuck_effect):
    resp = _post(
        {
            "logical_effect_id": stuck_effect,
            "resolution": "CONFIRMED_APPLIED",
            "source": "user",
            "evidence": "",
        }
    )
    assert resp.status == 400
    assert "evidence" in _payload(resp)["error"]
    assert find_active_unresolved_effect(stuck_effect) is not None


def test_resolve_is_owner_local_only(stuck_effect):
    resp = _post(
        {
            "logical_effect_id": stuck_effect,
            "resolution": "CONFIRMED_APPLIED",
            "source": "user",
            "evidence": "remote peer should not be able to do this",
        },
        client_host="203.0.113.9",
    )
    assert resp.status == 403
    assert _payload(resp)["error"] == "owner_local_required"


def test_unknown_effect_is_a_clean_404():
    resp = _post(
        {
            "logical_effect_id": "lef-doesnotexist",
            "resolution": "CONFIRMED_APPLIED",
            "source": "user",
            "evidence": "n/a",
        }
    )
    assert resp.status == 404
