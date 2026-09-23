"""Served /api/chat proofs for C19 automatic presentation selection.

Pattern of ``tests/test_skill_family_served_turns.py``: a REAL daemon
(``apps/vool_api_server.py`` subprocess, isolated ``VOOL_HOME``, no
Keychain) behind a scripted loopback provider that records every bound
request body. Proven here:

- S1a an advisory turn whose prose carries a tabular shape ELECTS it on a real
      /api/chat turn and the election record rides the commit's
      ``display_metadata`` (the rig's default lane is deliberately paid_cloud,
      so that turn is ratify-only by the spec's lane law);
- S1b THE SERVED REPAIR JOURNEY, self-contained in both orders: the daemon's
      own certified provider answers from 127.0.0.1 and is classified by the
      production ``provider_cost_class`` loopback heuristic — paid_cloud
      ratify-only FIRST (no repair call), then the reclassified lane runs
      election → one derived automatic constraint → one repair call →
      acceptance → the final post-gate bytes are the repaired table with
      every citation intact and the commit hash matching the served bytes,
      then the ORIGINAL metadata is restored (in ``finally``) and a second
      paid_cloud turn proves the original lane semantics again. The test
      uses the production registry read/write seams and depends on no
      particular execution order;
- S2  an ordinary prose advisory turn elects prose, triggers nothing, and
      binds no retry;
- S3  an explicit "show it as a table" turn stands the selection down
      (``explicit_request``) — the wire behavior itself is pinned unmodified
      by tests/test_skill_family_served_turns.py;
- S4  a not-an-answer draft stands the selection down (``refusal_signal``);
- S5  a grounding-REFUSED turn ships the post-gate re-stamp
      (``grounding_gate``) — proven at ``core.finalization`` through the SAME
      production writers the served path traverses (the M3 rig);
- S6  honesty: nothing on the elected turn's wire claims the user explicitly
      requested the shape, and the bound repair instruction carries only the
      automatic phrasing.
"""
from __future__ import annotations

import glob
import hashlib
import json

import pytest

from tests._served_skill_rig import (
    PROVIDER_MANIFEST_ID,
    ProviderState,
    ServedDaemon,
    make_provider_server,
)
from tests.test_native_skill_library import _iso_home

RETRY_MARKER = "using only the values already in your answer"

_PROSE_WITH_TABLE_SHAPE = (
    "Plan A: deductible €10 [receipt: read 1 file, 0 writes], coverage basic.\n"
    "Plan B: deductible €25, coverage full."
)
_REPAIRED_TABLE = (
    "| Plan | Deductible | Coverage |\n|---|---|---|\n"
    "| A | €10 [receipt: read 1 file, 0 writes] | basic |\n"
    "| B | €25 | full |"
)


@pytest.fixture(scope="module")
def selection_rig(tmp_path_factory):
    """One provider + one certified daemon shared by the selection proofs."""
    tmp = tmp_path_factory.mktemp("presentation-selection-rig")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    daemon = ServedDaemon(home, port).start()
    daemon.pin_provider_model()
    cert = daemon.certify_provider_model()
    result = cert.get("result") or cert
    assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"
    yield type("Rig", (), {"state": state, "daemon": daemon, "home": home, "tmp": tmp})
    daemon.stop()
    server.shutdown()


def _chat(rig, text: str, session: str, turn_id: str) -> dict:
    return rig.daemon.post(
        "/v1/chat/completions",
        {
            "model": PROVIDER_MANIFEST_ID,
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "mode": "manual",
            "session_id": session,
            "turn_id": turn_id,
        },
    )


def _selection_of(reply: dict) -> dict:
    commit = reply.get("vool_response_commit") or {}
    record = (commit.get("display_metadata") or {}).get("presentation_selection") or {}
    assert record, (
        "no presentation_selection in the served commit's display_metadata: "
        f"{json.dumps(commit)[:400]}"
    )
    return record


def test_s1_served_advisory_turn_elects_and_ships_the_record(selection_rig) -> None:
    """S1 (served half): a real /api/chat advisory turn whose prose carries a
    tabular shape ELECTS it, and the election record rides the commit's
    display_metadata. The rig's custom lane is deliberately paid_cloud, and
    the spec's lane law makes that lane ratify-only: the prose ships
    unchanged, once. The repair half of S1 (free_local lane) is proven at the
    router seam in tests/test_presentation_repair_router.py."""
    rig = selection_rig
    rig.state.requests.clear()
    rig.state.script = [
        {"final": _PROSE_WITH_TABLE_SHAPE},
        {"prompt_contains": RETRY_MARKER, "final": _REPAIRED_TABLE},
    ]
    reply = _chat(rig, "walk me through both plans in detail", "rig-select-1", "turn-sel-1")

    answer = str(reply["choices"][0]["message"].get("content") or "")
    assert "[receipt: read 1 file, 0 writes]" in answer, "citations must survive verbatim"

    record = _selection_of(reply)
    assert record["origin"] == "automatic"
    assert record["elected"] == "comparison_matrix"
    assert record["trigger"] == "subjects_ge2_shared_attributes_ge2"
    assert record["disabled_by"] is None
    assert record["gap_detected"] is True
    assert record["schema"] == "vool.presentation_selection.v1"

    # Ratify-only lane: the initial draft is the only provider call.
    real_turns = [r for r in rig.state.turn_requests]
    assert len(real_turns) == 1, f"a paid lane binds no repair, saw {len(real_turns)} calls"


def test_s2_ordinary_prose_advisory_turn_elects_prose_and_binds_no_retry(selection_rig) -> None:
    rig = selection_rig
    rig.state.requests.clear()
    rig.state.script = [
        {"final": "Stoicism is a school of Hellenistic philosophy that teaches virtue and self-control."}
    ]
    reply = _chat(rig, "what is stoicism? explain it in detail", "rig-select-2", "turn-sel-2")

    record = _selection_of(reply)
    assert record["elected"] == "prose"
    assert record["trigger"] == "none_justified"
    assert record["gap_detected"] is False
    assert record["disabled_by"] is None
    real_turns = [r for r in rig.state.turn_requests]
    assert len(real_turns) == 1, "a prose turn must not bind any repair call"


def test_s3_explicit_table_request_stands_the_selection_down(selection_rig) -> None:
    rig = selection_rig
    rig.state.requests.clear()
    rig.state.script = [
        {"final": "Sorted:\n\n| Order | Fruit |\n|---|---|\n| 1 | apple |\n| 2 | banana |"}
    ]
    reply = _chat(
        rig,
        "sort apple and banana alphabetically and show it as a table",
        "rig-select-3",
        "turn-sel-3",
    )

    record = _selection_of(reply)
    assert record["disabled_by"] == "explicit_request"
    assert record["elected"] is None
    answer = str(reply["choices"][0]["message"].get("content") or "")
    assert "| 2 | banana |" in answer


def test_s4_not_an_answer_draft_stands_the_selection_down(selection_rig) -> None:
    """A bare marker is an incomplete answer, so presentation selection stands down."""
    rig = selection_rig
    rig.state.requests.clear()
    rig.state.script = [{"final": "1."}]
    reply = _chat(rig, "tell me something interesting", "rig-select-4", "turn-sel-4")

    record = _selection_of(reply)
    provenance = reply["vool_response_commit"]["display_metadata"]["provenance"]
    assert provenance["route"] == "ordinary_plain_text_chat"
    assert record["disabled_by"] == "refusal_signal", json.dumps(reply)
    assert record["elected"] is None
    commit = reply["vool_response_commit"]
    assert commit["closure_verdict"]["demand_satisfied"] == 0
    assert commit["turn_result"]["fulfilled_obligations"] == 0


def test_s5_grounding_refused_turn_ships_the_post_gate_re_stamp(tmp_path, monkeypatch) -> None:
    """S5/T13 through the SAME production writers the served path traverses:
    a turn the M3 grounding gate REFUSES commits with the grounding_gate
    re-stamp — never a shape record describing refused bytes."""
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()

    from tests.test_unsupported_current_claims_cannot_publish_m3 import (
        RUST_FABRICATION,
        build_turn,
        publish,
    )

    context = build_turn()
    commit = publish(context, RUST_FABRICATION)
    publication = dict((commit.get("grounding_lifecycle") or {}).get("publication") or {})
    assert publication.get("state") == "refused"
    record = dict((commit.get("display_metadata") or {}).get("presentation_selection") or {})
    assert record["disabled_by"] == "grounding_gate"
    assert record["elected"] is None
    assert record["origin"] == "automatic"


def test_s6_automatic_guidance_honesty(selection_rig) -> None:
    """S6 (served half): on a real /api/chat turn whose prose carries a shape,
    the recorded wire carries no "explicitly requested" claim about the
    elected presentation — nothing on this lane claims the user asked for it.
    The bound repair instruction itself is asserted on the free_local router
    seam in tests/test_presentation_repair_router.py::test_s6_..."""
    rig = selection_rig
    rig.state.requests.clear()
    rig.state.script = [
        {"final": _PROSE_WITH_TABLE_SHAPE},
        {"prompt_contains": RETRY_MARKER, "final": _REPAIRED_TABLE},
    ]
    reply = _chat(rig, "walk me through both plans in detail", "rig-select-6", "turn-sel-6")
    record = _selection_of(reply)
    assert record["elected"] == "comparison_matrix"
    assert record["disabled_by"] is None

    for body in rig.state.turn_requests:
        blob = " ".join(
            str(message.get("content") or "") for message in (body.get("messages") or [])
        )
        if "presentation" in blob:
            assert "explicitly requested" not in blob, blob[:400]

def _daemon_db_path(rig) -> str:
    databases = sorted(glob.glob(str(rig.home / "data" / "*.db")))
    assert databases, "the daemon persisted no database in its isolated home"
    return databases[0]


def _read_daemon_manifest(rig):
    """Read the daemon's pinned provider manifest through the production
    registry read seam (``ModelRegistry.get_manifest`` over the daemon's own
    persisted row)."""
    from core.model_registry import ModelRegistry
    from storage.db import (
        active_default_db_path,
        configure_default_db_path,
        reset_default_connection,
    )

    previous = active_default_db_path()
    configure_default_db_path(_daemon_db_path(rig))
    try:
        reset_default_connection()
        manifest = ModelRegistry().get_manifest("custom-byok", "probe-model")
    finally:
        configure_default_db_path(previous)
        reset_default_connection()
    assert manifest is not None, "the pinned custom-byok manifest was not found"
    return manifest


def _register_daemon_manifest(rig, manifest) -> None:
    """TEST SETUP / TEARDOWN through the PRODUCTION registry write seam
    (``ModelRegistry.register_manifest`` -> ``upsert_provider_manifest``):
    this is how the runtime itself persists manifests; there is no bespoke
    SQL here. The test process points at the daemon's own database for the
    duration of the write, then restores the process DB binding."""
    from core.model_registry import ModelRegistry
    from storage.db import (
        active_default_db_path,
        configure_default_db_path,
        reset_default_connection,
    )

    previous = active_default_db_path()
    configure_default_db_path(_daemon_db_path(rig))
    try:
        reset_default_connection()
        ModelRegistry().register_manifest(manifest)
    finally:
        configure_default_db_path(previous)
        reset_default_connection()


def test_s1b_free_local_repair_journey_then_paid_cloud_restore(selection_rig) -> None:
    """S1b: the served repair journey, self-contained and order-independent.

    Three orders inside one journey, with the manifest's ORIGINAL metadata
    captured up front and restored in ``finally`` (assertion failure included):

    1. paid_cloud (the registrar's default) -> ratify-only: election ships,
       NO repair call, prose unchanged;
    2. genuinely free_local (the production loopback heuristic over the
       daemon's own manifest — see the reclassification note below) ->
       election -> ONE derived automatic constraint -> one repair call ->
       acceptance -> the final post-gate bytes are the repaired table;
    3. restored paid_cloud -> original lane semantics again: no repair call,
       prose unchanged, and the persisted row really carries the original
       classification.

    Reclassification note: the provider genuinely answers from
    ``http://127.0.0.1``; the registrar merely stamps custom BYOK lanes
    ``paid_cloud`` so burst-lane metering binds. Order 2 registers the same
    manifest WITHOUT that explicit stamp, so the production
    ``provider_cost_class`` classifies it by its real loopback base_url —
    exactly as it would any localhost provider. Certification (provider+model
    keyed) and the scripted provider are untouched; the registry reads the
    persisted row per call.
    """
    rig = selection_rig
    original = _read_daemon_manifest(rig)
    assert str((original.metadata or {}).get("cost_class") or "") == "paid_cloud", (
        "journey precondition broken: the rig lane is not the registrar's paid_cloud default"
    )
    reclassified = original.model_copy(
        update={
            "metadata": {
                **(original.metadata or {}),
                "cost_class": "free_local",
                "deployment_class": "local",
            }
        }
    )
    try:
        # ORDER 1 — paid_cloud: ratify-only. Election ships, no repair call.
        rig.state.requests.clear()
        rig.state.script = [{"final": _PROSE_WITH_TABLE_SHAPE}]
        reply = _chat(rig, "walk me through both plans in detail", "rig-select-fl", "turn-sel-fl-1")
        assert len(rig.state.turn_requests) == 1, (
            f"the paid_cloud lane must bind no repair call, saw {len(rig.state.turn_requests)}"
        )
        answer = str(reply["choices"][0]["message"].get("content") or "")
        assert "[receipt: read 1 file, 0 writes]" in answer
        record = _selection_of(reply)
        assert record["elected"] == "comparison_matrix"
        assert record["gap_detected"] is True
        assert record["fallback"] is None

        # ORDER 2 — genuinely free_local: the served repair journey.
        _register_daemon_manifest(rig, reclassified)
        flipped = _read_daemon_manifest(rig)
        assert (flipped.metadata or {}).get("cost_class") == "free_local", (
            "the production registry seam must persist the reclassification"
        )
        rig.state.requests.clear()
        rig.state.script = [
            {"final": _PROSE_WITH_TABLE_SHAPE},
            {"prompt_contains": RETRY_MARKER, "final": _REPAIRED_TABLE},
        ]
        reply = _chat(rig, "walk me through both plans in detail", "rig-select-fl", "turn-sel-fl-2")

        commit = dict(reply.get("vool_response_commit") or {})
        canonical = str(commit.get("canonical_content") or "")
        # The repaired table IS the shipped answer, citations byte-intact.
        assert "| A | €10 [receipt: read 1 file, 0 writes] | basic |" in canonical, canonical[:400]
        assert "coverage basic.\n" not in canonical.split("|")[0], (
            "the shipped bytes are the prose draft, not the repaired table"
        )
        # HASH-LAST: the commit covers exactly the bytes the wire served.
        assert commit.get("content_hash") == (
            "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )

        # Exactly ONE derived repair was bound on the free_local lane.
        real_turns = [r for r in rig.state.turn_requests]
        assert len(real_turns) == 2, f"expected draft + ONE repair, saw {len(real_turns)}"
        retry = real_turns[-1]
        assert retry.get("temperature") == 0.0
        # The re-derived selection budget: the candidate prose is ~110 chars,
        # so max(64, min(lane, 2*ceil(len/4))) floors at 64 — not the
        # degenerate max_words-keyed formula an election-derived contract
        # cannot fill.
        assert retry.get("max_tokens") == 64, retry.get("max_tokens")
        instruction = str((retry.get("messages") or [{}])[-1].get("content") or "")
        assert RETRY_MARKER in instruction
        assert "explicitly requested" not in instruction, instruction[:300]

        # The shipped record describes the POST-GATE bytes: the elected shape
        # is now present, so the record is a ratified one, not a gap.
        record = _selection_of(reply)
        assert record["origin"] == "automatic"
        assert record["elected"] in ("table", "comparison_matrix")
        assert record["trigger"], "the shipped record carries no trigger"
        assert record["disabled_by"] is None
        assert record["gap_detected"] is False
        assert record["fallback"] is None
    finally:
        # Restore the registrar's original metadata — assertion failure
        # included — so the rig lane and this journey are order-independent.
        _register_daemon_manifest(rig, original)

    # ORDER 3 — restored paid_cloud: the original lane semantics again.
    restored = _read_daemon_manifest(rig)
    assert (restored.metadata or {}).get("cost_class") == "paid_cloud", (
        "the restore must persist the original classification"
    )
    rig.state.requests.clear()
    rig.state.script = [{"final": _PROSE_WITH_TABLE_SHAPE}]
    reply = _chat(rig, "walk me through both plans in detail", "rig-select-fl", "turn-sel-fl-3")
    assert len(rig.state.turn_requests) == 1, (
        f"the restored paid_cloud lane must bind no repair call, saw {len(rig.state.turn_requests)}"
    )
    answer = str(reply["choices"][0]["message"].get("content") or "")
    assert "coverage basic." in answer and "| A |" not in answer, (
        "the restored lane must ship the prose unchanged"
    )
    record = _selection_of(reply)
    assert record["elected"] == "comparison_matrix"
    assert record["gap_detected"] is True
    assert record["disabled_by"] is None
