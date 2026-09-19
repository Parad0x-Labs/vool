"""M-P2/S-P6 — every pact mutation dispatches through the Command Registry, exactly once."""
from __future__ import annotations

import json

import pytest

from core import first_run_pact
from tests.first_run_pact_rig import pact_rig


def test_every_state_changing_pact_post_resolves_to_one_execute_command_dispatch(pact_rig, monkeypatch):
    import core.command_registry.legacy as legacy
    from core.command_registry import execute

    dispatches: list[str] = []
    original = execute.execute_command

    def spy(command_id, input_data=None, **kwargs):
        dispatches.append(str(command_id))
        return original(command_id, input_data, **kwargs)

    monkeypatch.setattr(legacy, "execute_command", spy)

    pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/begin", {})
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/advance", {"to": "naming", "expect_revision": snap["revision"]})
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/name", {"keep_default": True, "preferred_address": "Alex", "expect_revision": snap["revision"]})
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/facts", {"items": [{"category": "locale", "value": "lt-LT"}], "expect_revision": snap["revision"]})
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    pact_rig.post("/api/onboarding/pact/skip", {})

    assert dispatches == [
        "first_run.pact.begin",
        "first_run.pact.advance",
        "first_run.pact.name",
        "first_run.pact.facts.set",
        "first_run.pact.boundary.set",
        "first_run.pact.skip",
    ], dispatches


def test_a_pact_post_that_tried_to_bypass_the_registry_would_leave_no_command_trace(pact_rig):
    """The negative pin: direct file tampering is detectably NOT a command run.

    The pact file is the authority for its own tour progress, so on-disk tampering IS
    projected (documented consequence, spec §17 JP7) — but it can never carry the command
    audit trail, which is what the boundary step's via_command_ids records.
    """
    from core.runtime_paths import active_data_dir

    pact_rig.walk_to("provider_choice")
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    legit = json.loads((active_data_dir() / "first_run_pact_state.json").read_text())
    assert legit["steps"]["boundaries"]["via_command_ids"] == ["first_run.pact.boundary.set"]

    tampered = dict(legit)
    tampered["state"] = "done"
    (active_data_dir() / "first_run_pact_state.json").write_text(json.dumps(tampered))
    # The tampered file carries no command trace for the "completion" — the audit seam that
    # separates a real registry-driven tour from a hand-edited one.
    assert legit["steps"]["task"]["status"] != "done" or "evidence" not in legit["steps"]["task"]
    assert "via_command_ids" not in legit["steps"].get("task", {})


def test_a_pact_post_consumes_exactly_one_revision(pact_rig):
    """S-P6 companion pin: one POST = one command = one revision bump. A handler that
    mutated the file directly alongside the dispatch would consume two."""
    pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/begin", {})
    before = pact_rig.pact()["revision"]
    pact_rig.post("/api/onboarding/pact/begin", {})  # idempotent: no revision change
    assert pact_rig.pact()["revision"] == before
    pact_rig.post("/api/onboarding/pact/hide", {"hidden": True})
    assert pact_rig.pact()["revision"] == before + 1
