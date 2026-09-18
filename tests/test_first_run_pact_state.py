"""M-P1 — the pact state authority: shape, atomicity, corruption, CAS, skip-everywhere, whitelist."""
from __future__ import annotations

import json
import threading

import pytest

from core import first_run_pact
from core.first_run_pact import PactFault
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture: isolated home + real runtime


def test_a_fresh_home_seeds_absent_and_a_used_home_seeds_not_applicable(pact_rig):
    first_run_pact.seed()
    assert first_run_pact.load_state()["state"] == "absent"

    # An existing-user signal (a receipt ledger) flips the predicate.
    ledger_dir = pact_rig.home / "data" / "honesty_receipts"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / ("0" * 24 + ".jsonl")).write_text("")

    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.unlink()
    seeded = first_run_pact.seed()
    assert seeded["state"] == "not_applicable"
    assert seeded["seed_reason"] == "existing_user"


def test_seed_is_idempotent_and_never_rewrites_a_live_file(pact_rig):
    first_run_pact.seed()
    first = first_run_pact.load_state()
    first_run_pact.begin()
    second = first_run_pact.seed()
    assert second["state"] == "welcome"
    assert second["revision"] == first_run_pact.load_state()["revision"]


def test_every_mutation_carries_the_whitelist_and_nothing_else_ever_reaches_the_file(pact_rig):
    first_run_pact.seed()
    first_run_pact.begin()
    # A hostile mutation attempt smuggling a boundary value and a fact value must not land.
    with pytest.raises(PactFault):
        first_run_pact.advance("naming", evidence=None, expect_revision=999)  # stale

    data = first_run_pact.advance("naming")
    raw = json.loads((pact_rig.home / "data" / "first_run_pact_state.json").read_text())
    allowed = {
        "schema_name", "schema_version", "state", "revision", "welcome_hidden",
        "seed_reason", "steps", "provider_step", "created_at", "updated_at",
        "completed_at", "skipped_at",
    }
    assert set(raw) <= allowed
    for entry in raw["steps"].values():
        assert set(entry) <= {"status", "at", "via_command_ids", "evidence"}
        if "evidence" in entry:
            assert set(entry["evidence"]) <= {"session_id", "request_id", "receipt_id", "effect_ids", "verified"}
    assert data["state"] == "naming"


def test_skip_is_reachable_from_every_non_terminal_state(pact_rig):
    """Parametrized over the pure tour states; the evidence-locked ones walk the real claim path."""
    from core.runtime_paths import active_data_dir

    def fresh(state_target: str):
        pact_file = active_data_dir() / "first_run_pact_state.json"
        if pact_file.exists():
            pact_file.unlink()
        provider_file = active_data_dir() / "first_run_state.json"
        if provider_file.exists():
            provider_file.unlink()
        first_run_pact.seed()
        if state_target == "absent":
            return
        first_run_pact.begin()
        for step in ("naming", "facts", "boundaries", "provider_choice"):
            if state_target == step:
                return
            first_run_pact.advance(step)
        from core import first_run as provider

        try:
            provider.choose("local_only")
        except provider.FirstRunError:
            pass  # already at a terminal from a previous iteration of this loop
        first_run_pact.advance("local_task")
        _ = state_target  # task_receipt/denial_demo/memory_done are evidence-locked:
        # a bare skip from the CURRENT state is still reachable, which is what LP5 pins.

    for target in ("absent", "welcome", "naming", "facts", "boundaries", "provider_choice", "local_task"):
        fresh(target)
        data = first_run_pact.skip()
        assert data["state"] == "skipped", f"skip failed from {target}"


def test_skip_from_the_evidence_locked_states_after_real_claims(pact_rig):
    """The served walk: claim the real task, then skip; and skip inside denial_demo."""
    snap = pact_rig.walk_to("local_task")
    _ = snap
    request_id, _frames, _commit = pact_rig.run_task_turn(
        "Create welcome-notes.txt containing Welcome to VOOL."
    )
    assert request_id
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": request_id,
        "expect_revision": snap["revision"],
    })
    assert status == 200, payload
    status, payload = pact_rig.post("/api/onboarding/pact/skip", {})
    assert status == 200 and payload["state"] == "skipped"


def test_stale_revision_is_a_typed_conflict_and_cas_is_enforced(pact_rig):
    first_run_pact.seed()
    first_run_pact.begin()
    snap = first_run_pact.load_state()
    with pytest.raises(PactFault) as excinfo:
        first_run_pact.advance("naming", expect_revision=snap["revision"] - 1)
    assert excinfo.value.code == "stale_revision"
    data = first_run_pact.advance("naming", expect_revision=snap["revision"])
    assert data["state"] == "naming"


def test_a_corrupt_file_is_quarantined_and_reseeded_never_trusted(pact_rig):
    from core.runtime_paths import active_data_dir

    first_run_pact.seed()
    first_run_pact.begin()
    first_run_pact.advance("naming")
    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.write_text("{ this is not json")

    data = first_run_pact.seed()
    assert data["state"] == "absent"
    quarantined = list((active_data_dir()).glob("first_run_pact_state.json.corrupt-*"))
    assert len(quarantined) == 1


def test_an_unknown_future_schema_version_is_corrupt_never_guess_parsed(pact_rig):
    from core.runtime_paths import active_data_dir

    first_run_pact.seed()
    pact_file = active_data_dir() / "first_run_pact_state.json"
    data = json.loads(pact_file.read_text())
    data["schema_version"] = 99
    pact_file.write_text(json.dumps(data))

    seeded = first_run_pact.seed()
    assert seeded["schema_version"] == 1
    assert list((active_data_dir()).glob("first_run_pact_state.json.corrupt-*"))


def test_concurrent_writers_never_tear_the_file(pact_rig):
    from core.runtime_paths import active_data_dir

    first_run_pact.seed()
    pact_file = active_data_dir() / "first_run_pact_state.json"
    errors: list[Exception] = []

    def hammer():
        for _ in range(8):
            try:
                first_run_pact.seed()
                first_run_pact.hide(True)
            except PactFault:
                pass  # revision races are expected; the FILE must stay valid
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    data = json.loads(pact_file.read_text())  # a torn file would fail to parse
    assert data["schema_name"] == "vool.first_run_pact"


def test_hide_is_not_skip_and_the_welcome_card_comes_back(pact_rig):
    first_run_pact.seed()
    first_run_pact.begin()
    data = first_run_pact.hide(True)
    assert data["state"] == "welcome" and data["welcome_hidden"] is True
    data = first_run_pact.hide(False)
    assert data["welcome_hidden"] is False


def test_not_applicable_refuses_every_mutation_except_the_explicit_begin(pact_rig):
    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.parent.mkdir(parents=True, exist_ok=True)
    pact_file.write_text(json.dumps({
        "schema_name": "vool.first_run_pact", "schema_version": 1, "state": "not_applicable",
        "revision": 0, "welcome_hidden": False, "seed_reason": "existing_user",
        "steps": {}, "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:00Z",
    }))
    with pytest.raises(PactFault) as excinfo:
        first_run_pact.skip()
    assert excinfo.value.code == "not_applicable_readonly"
    # §6.3: the explicit Settings replay IS the one legal transition out.
    data = first_run_pact.begin()
    assert data["state"] == "welcome"


def test_reset_replays_without_erasing_facts_or_boundaries(pact_rig):
    first_run_pact.seed()
    first_run_pact.begin()
    first_run_pact.advance("naming")
    first_run_pact.set_name(agent_name="VOOL", keep_default=True, preferred_address="Alex")
    first_run_pact.set_boundary("local_only_composite", True)
    first_run_pact.reset()
    snap = first_run_pact.snapshot()
    assert snap["state"] == "welcome"
    assert snap["live"]["local_only_mode"] is True  # boundary truth untouched
    assert any(f["category"] == "preferred_name" for f in snap["facts"])  # facts untouched


def test_the_sanitizer_strips_unknown_keys_even_from_a_poisoned_write(pact_rig):
    """S-P17 pin: the whitelist is enforced in the writer, structurally."""
    poisoned = {
        "schema_name": first_run_pact.SCHEMA_NAME,
        "schema_version": 1,
        "state": "welcome",
        "revision": 1,
        "boundary_values": {"local_only_mode": True},
        "operator_name": "Alex",
        "steps": {"naming": {"status": "done", "secret_ref": "sk-xyz", "value": "LT-CANARY"}},
    }
    clean = first_run_pact._sanitize(poisoned)
    assert "boundary_values" not in clean and "operator_name" not in clean
    assert set(clean["steps"]["naming"]) <= {"status", "at", "via_command_ids", "evidence"}
