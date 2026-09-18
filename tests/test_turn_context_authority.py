"""Turn-context authority (C09) — scope, compile law, lifecycle, A8 gates.

The authority under test is ``core/turn_context.py``; it stands on the storage
law (``core/context_pages.py``), the append-only layout laws
(``core/context_layout.py``) and the A8 privacy gates (``core/finalization.py``).
Every invariant here is written so that sabotaging the law turns a named test
red: if a session can read another session's inferred page, if a frozen header
mutates within a generation, if erasure leaves bytes in a cache this authority
reaches, or if governed bytes are admitted/served after a WITHHOLD/ERASE, the
specific test named for that cause must fail.

Pages are content-addressed (kind + source + content IS the identity), so each
scenario uses DISTINCT content — sharing a hash would share pins across
scenarios, which is the law working, not a bug.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.context_layout import LayoutLawViolation
from core.context_pages import PageNotFoundError
from core.turn_context import (
    AdmissionClosedError,
    AdmissionNotFoundError,
    AdmissionRefusedError,
    AdmissionScopeError,
    TurnScope,
    admit_turn_context,
    archive_scope_pages,
    bump_context_generation,
    close_turn_scope,
    compile_turn_context,
    current_generation,
    erase_admission,
    inspect_admissions,
    note_governance_event,
    pin_admission,
    recall_page,
    release_admission_pin,
    supersede_admission,
    withhold_admission,
)

PRINCIPAL = "owner_local"


# ── Hermetic fixture (local, per file) ───────────────────────────────────────


@pytest.fixture()
def turn_ctx_env(tmp_path, monkeypatch):
    """Isolated runtime home + SQLite per test (the operator-profile rig shape)."""
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    db_path = tmp_path / "turn_ctx.db"
    sdb.configure_default_db_path(db_path)
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    yield {"home": home, "db_path": db_path}
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _scope(session_id: str, *, project_id: str = "", turn_id: str = "", principal: str = PRINCIPAL) -> TurnScope:
    return TurnScope(
        principal=principal,
        session_id=session_id,
        project_id=project_id,
        turn_id=turn_id,
    )


def _admit(content: str, session_id: str, **kwargs) -> dict:
    scope = kwargs.pop("scope", None) or _scope(
        session_id,
        project_id=kwargs.pop("project_id", ""),
        turn_id=kwargs.pop("turn_id", ""),
        principal=kwargs.pop("principal", PRINCIPAL),
    )
    return admit_turn_context(content, scope=scope, **kwargs)


def _mint_governed_finalization(text: str, turn_id: str) -> dict:
    """The established A8 mint pattern (tests/foundation/test_a8_privacy_*)."""
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id=turn_id, canonical_content=text)


def _exclusion_reasons(compilation) -> list[str]:
    return [str(entry.get("reason") or "") for entry in compilation.exclusions]


# ── 1. Scoped admission ──────────────────────────────────────────────────────


def test_admission_is_scoped_and_carries_full_provenance(turn_ctx_env):
    """CONTEXT IS COMPILED, NOT ACCUMULATED: every admission names WHO it
    belongs to (principal + session + project + turn + generation), and the
    authority refuses scope-less, unknown-origin, or empty admissions."""
    content = "scenario-one brine survey ledger keystone fermentation row"
    admitted = _admit(
        content,
        "sess-adm-1",
        project_id="proj-1",
        turn_id="turn-1",
    )
    assert admitted["admission_id"]
    assert len(admitted["page_hash"]) == 64
    assert admitted["pinned"] is True  # an open turn holds its page
    assert admitted["volatile"] is False
    assert admitted["deduplicated"] is False
    assert admitted["generation"] == 1

    records = inspect_admissions(PRINCIPAL, session_id="sess-adm-1")
    assert len(records) == 1
    record = records[0]
    assert record["admission_id"] == admitted["admission_id"]
    assert record["page_hash"] == admitted["page_hash"]
    assert record["origin"] == "inferred"
    assert record["source_kind"] == "chat_exchange"
    assert record["status"] == "active"
    assert record["bytes"] == len(content.encode("utf-8"))
    assert record["turn_id"] == "turn-1"
    assert record["generation"] == 1
    assert record["project_id"] == "proj-1"

    # Refusals are typed, loud, and store nothing.
    with pytest.raises(ValueError):
        admit_turn_context(
            "orphan principal", scope=TurnScope(principal="", session_id="sess-adm-1")
        )
    with pytest.raises(ValueError):
        admit_turn_context(
            "orphan session", scope=TurnScope(principal=PRINCIPAL, session_id="")
        )
    with pytest.raises(ValueError):
        _admit("unknown origin", "sess-adm-1", origin="channeled")
    with pytest.raises(ValueError):
        _admit("   ", "sess-adm-1")
    assert inspect_admissions(PRINCIPAL, session_id="sess-adm-1") == records


# ── 2. Cross-session isolation ───────────────────────────────────────────────


def test_inferred_pages_never_cross_sessions_even_with_matching_project(turn_ctx_env):
    """CONTEXT IS COMPILED, NOT ACCUMULATED (scope union): an inferred page of
    sess-A NEVER appears in a compile for sess-B — not with the same project_id,
    not with a matching query. Isolation is structural, not a relevance filter."""
    content = "scenario-two obsidian lattice rehearsal transcript unique"
    admitted = _admit(
        content,
        "sess-iso-a",
        project_id="proj-iso-shared",
        turn_id="turn-iso-a",
    )
    close_turn_scope(PRINCIPAL, "sess-iso-a", "turn-iso-a")

    # Positive control: the page IS servable in its own session (the query matches).
    own = compile_turn_context(
        PRINCIPAL,
        "sess-iso-a",
        project_id="proj-iso-shared",
        query_text="obsidian lattice rehearsal transcript",
    )
    assert [entry["admission_id"] for entry in own.receipt()["selected"]] == [
        admitted["admission_id"]
    ]

    compilation = compile_turn_context(
        PRINCIPAL,
        "sess-iso-B-different",
        project_id="proj-iso-shared",
        query_text="obsidian lattice rehearsal transcript",
    )
    assert compilation.receipt()["selected"] == []
    assert compilation.block_text == ""
    assert content not in compilation.block_text
    # Not selected, not excluded-as-irrelevant, not anywhere: another session's
    # inferred page is not even a named candidate for this session's receipt.
    served_ids = {entry["admission_id"] for entry in compilation.receipt()["selected"]}
    excluded_ids = {entry["admission_id"] for entry in compilation.exclusions}
    truncated_ids = {entry["admission_id"] for entry in compilation.truncations}
    assert admitted["admission_id"] not in served_ids | excluded_ids | truncated_ids


# ── 3. Explicit project sharing ──────────────────────────────────────────────


def test_explicit_origin_shares_across_sessions_of_one_project_only(turn_ctx_env):
    """Opt-in sharing is by ORIGIN, not by project string: a page admitted with
    origin='explicit' serves sibling sessions of the SAME project and never a
    different project's sessions."""
    content = "scenario-three heliotrope calibration signal memo annex"
    admitted = _admit(
        content,
        "sess-share-a",
        project_id="proj-share-1",
        turn_id="turn-share-a",
        origin="explicit",
    )
    close_turn_scope(PRINCIPAL, "sess-share-a", "turn-share-a")

    shared = compile_turn_context(
        PRINCIPAL,
        "sess-share-B-sibling",
        project_id="proj-share-1",
        query_text="heliotrope calibration signal",
    )
    selected = shared.receipt()["selected"]
    assert [entry["admission_id"] for entry in selected] == [admitted["admission_id"]]
    assert content in shared.block_text

    foreign_project = compile_turn_context(
        PRINCIPAL,
        "sess-share-B-sibling",
        project_id="proj-share-OTHER",
        query_text="heliotrope calibration signal",
    )
    assert foreign_project.receipt()["selected"] == []
    assert content not in foreign_project.block_text


# ── 4. Cross-principal isolation ─────────────────────────────────────────────


def test_pages_never_cross_principals_and_recall_refuses_foreign_scope(turn_ctx_env):
    """A page of owner_local is invisible to every other principal: scoped
    inspection, compiled context, and the recall surface all refuse — and the
    refusal never carries the governed bytes."""
    content = "scenario-four prismatic dossier quartet misprint folio"
    admitted = _admit(content, "sess-prin-a", turn_id="turn-prin-a")
    foreign = "channel:gateway-9"

    assert inspect_admissions(foreign) == []
    assert inspect_admissions(foreign, session_id="sess-prin-a") == []
    # Positive control: the owner principal DOES see its own page in this session.
    own = compile_turn_context(PRINCIPAL, "sess-prin-a", query_text="prismatic dossier quartet")
    assert [entry["admission_id"] for entry in own.receipt()["selected"]] == [
        admitted["admission_id"]
    ]

    foreign_compile = compile_turn_context(
        foreign, "sess-prin-a", query_text="prismatic dossier quartet"
    )
    assert foreign_compile.receipt()["selected"] == []
    assert content not in foreign_compile.block_text

    # The admission EXISTS; a foreign principal's recall must be refused, loudly.
    with pytest.raises((AdmissionScopeError, AdmissionNotFoundError)) as excinfo:
        recall_page(foreign, admitted["admission_id"])
    assert content not in str(excinfo.value)


# ── 5. Relevance gate ────────────────────────────────────────────────────────


def test_unpinned_pages_serve_on_relevant_queries_and_explain_irrelevance(turn_ctx_env):
    """Selection is deterministic and explainable: after a turn's obligation
    closes (pin released), a query sharing the page's terms selects it; an
    unrelated query EXCLUDES it and names the relevance score."""
    content = "scenario-five keystone fermentation vault cranking twelve drums"
    admitted = _admit(content, "sess-rel-1", turn_id="turn-rel-1")
    close_turn_scope(PRINCIPAL, "sess-rel-1", "turn-rel-1")

    relevant = compile_turn_context(
        PRINCIPAL, "sess-rel-1", query_text="keystone fermentation drums"
    )
    assert [entry["admission_id"] for entry in relevant.receipt()["selected"]] == [
        admitted["admission_id"]
    ]
    assert content in relevant.block_text

    unrelated = compile_turn_context(
        PRINCIPAL, "sess-rel-1", query_text="glacier origami jetpack"
    )
    assert unrelated.receipt()["selected"] == []
    reasons = _exclusion_reasons(unrelated)
    assert any(reason.startswith("irrelevant(") for reason in reasons), reasons
    assert content not in unrelated.block_text


# ── 6. Residency: pins bypass relevance, block archival, then recall ─────────


def test_open_turn_pin_holds_page_against_relevance_and_archival(turn_ctx_env):
    """A PAGE LEAVES ONLY WHEN ITS OBLIGATION CLOSES: an open turn's pin makes
    its page serve even on an unrelated query (residency beats relevance), and
    a manual pin holds the page against archival until released — after which
    the archived page still recalls VERBATIM."""
    # Part A — the turn pin bypasses relevance but never scope.
    pinned_content = "scenario-six-a marmot regatta compass bearing ledger"
    pinned = _admit(pinned_content, "sess-res-a", turn_id="turn-res-a")  # NOT closed
    served = compile_turn_context(
        PRINCIPAL, "sess-res-a", query_text="glacier origami jetpack"
    )
    selected = served.receipt()["selected"]
    assert [entry["admission_id"] for entry in selected] == [pinned["admission_id"]]
    assert selected[0]["pinned"] is True
    assert pinned_content in served.block_text

    # Part B — a manual (operator) pin holds the page against archival.
    held_content = "scenario-six-b wanderlust casserole blueprint archive"
    held = _admit(held_content, "sess-res-b")  # no turn pin of its own
    pin = pin_admission(PRINCIPAL, held["admission_id"], reason="held by an open review")

    first_sweep = archive_scope_pages(PRINCIPAL, "sess-res-b")
    assert all(r["page_hash"] != held["page_hash"] for r in first_sweep), (
        "residency law violated: a manually pinned page was archived"
    )

    release_admission_pin(PRINCIPAL, held["admission_id"], pin_id=pin["pin_id"])
    second_sweep = archive_scope_pages(PRINCIPAL, "sess-res-b")
    assert held["page_hash"] in [r["page_hash"] for r in second_sweep]

    # Archival changes residency, never content: recall is byte-identical.
    recalled = recall_page(PRINCIPAL, held["admission_id"])
    assert recalled["content"] == held_content


# ── 7. Budget: explicit truncation, never silent drops ───────────────────────


def test_budget_overflow_names_every_dropped_page_and_pinned_pages_serve_anyway(turn_ctx_env):
    """Overflow is EXPLICIT: pages that do not fit are named in truncations
    with reason budget_overflow, each carrying its admission identity, and no
    truncated page's bytes sneak into the served block. A pinned page over
    budget still serves — and the receipt says the budget was exceeded."""
    session = "sess-budget-1"
    admitted: dict[str, str] = {}  # admission_id -> content
    for index in range(15):
        content = (
            f"regatta overflow crate {index:02d} "
            + "manifest fragment line for the bounded window scenario " * 3
        )
        turn_id = f"turn-of-{index:02d}"
        result = _admit(content, session, turn_id=turn_id)
        admitted[result["admission_id"]] = content
        close_turn_scope(PRINCIPAL, session, turn_id)

    compilation = compile_turn_context(
        PRINCIPAL, session, query_text="regatta overflow crate", budget_chars=600
    )
    assert compilation.items, "a 600-char budget over 15 pages must still serve some"
    assert compilation.truncations, "13 of 15 pages cannot fit and must be named"
    for truncation in compilation.truncations:
        assert truncation["reason"] == "budget_overflow"
        assert truncation["admission_id"] in admitted
        assert truncation["page_hash"]
        assert admitted[truncation["admission_id"]] not in compilation.block_text
    truncated_ids = {t["admission_id"] for t in compilation.truncations}
    selected_ids = {item.admission_id for item in compilation.items}
    assert not truncated_ids & selected_ids
    assert len(truncated_ids) + len(selected_ids) == 15

    # Pinned-over-budget: the pinned page still serves, honestly receipted.
    huge_content = "milky turbine almanac " + ("y" * 4000)
    huge_session = "sess-pof-9"
    huge = _admit(huge_content, huge_session, turn_id="turn-pof-9")
    close_turn_scope(PRINCIPAL, huge_session, "turn-pof-9")
    pin = pin_admission(PRINCIPAL, huge["admission_id"], reason="operator hold")
    over = compile_turn_context(
        PRINCIPAL,
        huge_session,
        query_text="glacier origami jetpack",
        budget_chars=10,
    )
    assert [item.admission_id for item in over.items] == [huge["admission_id"]]
    assert over.items[0].pinned is True
    assert huge_content in over.block_text
    assert any(
        t["reason"] == "budget_exceeded_by_pinned_pages" for t in over.truncations
    ), over.truncations
    release_admission_pin(PRINCIPAL, huge["admission_id"], pin_id=pin["pin_id"])


# ── 8. Frozen header law ─────────────────────────────────────────────────────


def test_frozen_header_is_stable_within_a_generation_and_sabotage_raises(turn_ctx_env):
    """A FROZEN HEADER DOES NOT MUTATE WITHIN A GENERATION: two compilations of
    an unchanged frontier are stable and silent; a mutated frozen_hash (here:
    SQL sabotage standing in for a timestamp/counter sneaking into the stable
    prefix) raises LayoutLawViolation on the next compile; the legal escape is
    bump_context_generation, after which compilation works again."""
    content = "scenario-eight sable equinox prompter fence rail"
    admitted = _admit(content, "sess-frozen-1", turn_id="turn-frozen-1")
    close_turn_scope(PRINCIPAL, "sess-frozen-1", "turn-frozen-1")

    first = compile_turn_context(
        PRINCIPAL, "sess-frozen-1", query_text="sable equinox prompter"
    )
    second = compile_turn_context(
        PRINCIPAL, "sess-frozen-1", query_text="sable equinox prompter"
    )
    assert first.frozen_hash == second.frozen_hash
    assert first.frozen_hash
    assert second.layout_stable_within_generation is True

    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE turn_context_generations SET frozen_hash = ? "
            "WHERE principal = ? AND session_id = ?",
            ("deadbeef" * 8, PRINCIPAL, "sess-frozen-1"),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(LayoutLawViolation):
        compile_turn_context(PRINCIPAL, "sess-frozen-1", query_text="sable equinox prompter")

    bumped = bump_context_generation(
        PRINCIPAL, "sess-frozen-1", reason="frozen drift repaired", actor="test-operator"
    )
    assert bumped == 2
    assert current_generation(PRINCIPAL, "sess-frozen-1") == 2
    healed = compile_turn_context(
        PRINCIPAL, "sess-frozen-1", query_text="sable equinox prompter"
    )
    assert [item.admission_id for item in healed.items] == [admitted["admission_id"]]
    assert content in healed.block_text


# ── 9. Digest append-only ────────────────────────────────────────────────────


def test_governance_digest_only_grows_and_never_holds_page_bytes(turn_ctx_env):
    """GOVERNANCE HISTORY IS APPEND-ONLY: withhold and explicit governance
    events each append one line naming the event, earlier digest text survives
    byte-for-byte as a prefix of later digest text, and no digest line ever
    carries page content."""
    content = "scenario-nine atrium luggage theorem canal belfry"
    admitted = _admit(content, "sess-digest-1", turn_id="turn-digest-1")
    close_turn_scope(PRINCIPAL, "sess-digest-1", "turn-digest-1")

    baseline = compile_turn_context(
        PRINCIPAL, "sess-digest-1", query_text="atrium luggage theorem"
    )
    digest_before = baseline.digest_text

    withhold_admission(PRINCIPAL, admitted["admission_id"], reason="privacy hold")
    after_withhold = compile_turn_context(
        PRINCIPAL, "sess-digest-1", query_text="atrium luggage theorem"
    )
    digest_after_withhold = after_withhold.digest_text
    assert digest_after_withhold.startswith(digest_before)
    assert "withheld" in digest_after_withhold
    assert len(digest_after_withhold.splitlines()) == len(digest_before.splitlines()) + 1

    note_governance_event(
        PRINCIPAL,
        "sess-digest-1",
        event="audit_probe",
        admission_id=admitted["admission_id"],
        detail="growth probe",
    )
    after_note = compile_turn_context(
        PRINCIPAL, "sess-digest-1", query_text="atrium luggage theorem"
    )
    digest_after_note = after_note.digest_text
    assert digest_after_note.startswith(digest_after_withhold)
    assert "audit_probe" in digest_after_note
    assert len(digest_after_note.splitlines()) == len(digest_after_withhold.splitlines()) + 1

    # Digest lines are audit lines, never content.
    assert content not in digest_after_note
    assert "belfry" not in digest_after_note


# ── 10. Withhold ─────────────────────────────────────────────────────────────


def test_withhold_stops_serving_and_closes_recall_idempotently(turn_ctx_env):
    """WITHHELD/ERASED CONTEXT IS NEVER COMPILED: a withheld admission is
    excluded with reason withheld_by_privacy_action, its bytes leave the recall
    surface (AdmissionClosedError), and re-withholding is idempotent — no
    raise, no second governance event."""
    content = "scenario-ten cobalt heron dispatch memo shuttle"
    admitted = _admit(content, "sess-withhold-1", turn_id="turn-wh-1")
    close_turn_scope(PRINCIPAL, "sess-withhold-1", "turn-wh-1")

    result = withhold_admission(PRINCIPAL, admitted["admission_id"], reason="privacy hold")
    assert result["status"] == "withheld"

    compilation = compile_turn_context(
        PRINCIPAL, "sess-withhold-1", query_text="cobalt heron dispatch"
    )
    assert compilation.receipt()["selected"] == []
    assert "withheld_by_privacy_action" in _exclusion_reasons(compilation)
    assert content not in compilation.block_text

    with pytest.raises(AdmissionClosedError):
        recall_page(PRINCIPAL, admitted["admission_id"])

    repeat = withhold_admission(PRINCIPAL, admitted["admission_id"], reason="privacy hold")
    assert repeat["status"] == "withheld"
    assert repeat.get("idempotent") is True
    assert (
        len(inspect_admissions(PRINCIPAL, session_id="sess-withhold-1")) == 1
    )  # idempotent: no duplicate row, no new admission


# ── 11. Erasure ──────────────────────────────────────────────────────────────


def test_erase_releases_pins_and_removes_bytes_when_the_last_admission_lets_go(turn_ctx_env):
    """ERASED BYTES LEAVE EVERY CACHE WE REACH — and only the scopes that hold
    them: (a) erasing releases the turn pin, marks the admission erased, and
    drops the stored bytes when it was the last admission; (b) identical bytes
    admitted by ANOTHER session remain that session's lawful property until its
    own erasure; (c) an unknown admission is a typed NotFound, never a silent
    success."""
    # (a) last admission: pins released, status erased, bytes provably gone.
    content_a = "scenario-eleven-a umber ferrous glossary keel datum"
    admitted_a = _admit(content_a, "sess-erase-a", turn_id="turn-erase-a")
    result = erase_admission(PRINCIPAL, admitted_a["admission_id"], reason="operator forget")
    assert result["status"] == "erased"
    assert result["bytes_erased"] is True
    record = inspect_admissions(PRINCIPAL, session_id="sess-erase-a")[0]
    assert record["status"] == "erased"
    assert record["pinned"] is False  # the turn pin was released, not left dangling
    with pytest.raises(PageNotFoundError):
        from core.context_pages import page_in

        page_in(admitted_a["page_hash"])

    # (b) shared content across sessions: dedup gives one page_hash; each
    # scope's erasure only lets go of ITS admission.
    shared_content = "scenario-eleven-b veldt sorghum calendar folio grain"
    adm_a = _admit(shared_content, "sess-erase-shared-A", turn_id="turn-esa")
    adm_b = _admit(shared_content, "sess-erase-shared-B", turn_id="turn-esb")
    assert adm_a["page_hash"] == adm_b["page_hash"]
    assert adm_b["deduplicated"] is True
    close_turn_scope(PRINCIPAL, "sess-erase-shared-A", "turn-esa")
    close_turn_scope(PRINCIPAL, "sess-erase-shared-B", "turn-esb")

    erase_admission(PRINCIPAL, adm_a["admission_id"], reason="session A forget")
    sibling = compile_turn_context(
        PRINCIPAL, "sess-erase-shared-B", query_text="veldt sorghum calendar"
    )
    assert shared_content in sibling.block_text, (
        "erasing session A's admission must not reach session B's lawful copy"
    )

    erase_admission(PRINCIPAL, adm_b["admission_id"], reason="session B forget")
    with pytest.raises(PageNotFoundError):
        from core.context_pages import page_in

        page_in(adm_b["page_hash"])

    # (c) unknown admission: typed, loud.
    with pytest.raises(AdmissionNotFoundError):
        erase_admission(PRINCIPAL, "no-such-admission-id", reason="typo probe")


# ── 12. Supersede (edit control) ─────────────────────────────────────────────


def test_supersede_replaces_with_a_new_explicit_admission_never_a_rewrite(turn_ctx_env):
    """NOTHING IS EVER REWRITTEN — ONLY APPENDED OR ARCHIVED: a correction is a
    NEW explicit admission linked by metadata supersedes=<old>, the old
    admission stops compiling, and the compiled block serves the corrected
    text only."""
    original = "scenario-twelve flint dossier quartet misprint annex"
    corrected = "CORRECTED scenario-twelve flint dossier quartet appendix"
    admitted = _admit(original, "sess-supersede-1", turn_id="turn-sup-1")

    result = supersede_admission(
        PRINCIPAL, admitted["admission_id"], content=corrected, reason="typo repair"
    )
    assert result["status"] == "superseded"
    replacement_id = result["replacement_admission_id"]
    assert replacement_id != admitted["admission_id"]

    records = {r["admission_id"]: r for r in inspect_admissions(PRINCIPAL, session_id="sess-supersede-1")}
    assert records[admitted["admission_id"]]["status"] == "superseded"
    replacement = records[replacement_id]
    assert replacement["origin"] == "explicit"
    assert replacement["status"] == "active"
    assert replacement["metadata"]["supersedes"] == admitted["admission_id"]

    compilation = compile_turn_context(
        PRINCIPAL, "sess-supersede-1", query_text="flint dossier quartet"
    )
    served_ids = {entry["admission_id"] for entry in compilation.receipt()["selected"]}
    assert served_ids == {replacement_id}
    assert corrected in compilation.block_text
    assert original not in compilation.block_text


# ── 13. A8 write fence ───────────────────────────────────────────────────────


def test_governed_erased_bytes_are_refused_at_the_admission_door(turn_ctx_env):
    """THE A8 WRITE FENCE: bytes the authority has ERASED are refused at
    admission — typed AdmissionRefusedError, nothing stored, nothing inspected.
    Admission is the only enforcement point that works; the fence consults A8
    BEFORE any byte persists."""
    from core.finalization import AVAILABILITY_ERASED, set_availability

    text_x = "A8-FENCE-X regulated filament decree 7391 scenario-thirteen"
    commit = _mint_governed_finalization(text_x, turn_id="turn-fence-x")
    fid = commit["finalization_id"]
    assert set_availability(
        fid, AVAILABILITY_ERASED, reason="operator forget", governance_actor="owner"
    ) is True

    session = "sess-fence-x"
    before = inspect_admissions(PRINCIPAL, session_id=session, include_content=True)
    with pytest.raises(AdmissionRefusedError):
        _admit(text_x, session, turn_id="turn-fence-x-admit")
    after = inspect_admissions(PRINCIPAL, session_id=session, include_content=True)
    assert len(after) == len(before)
    assert all(entry.get("content") != text_x for entry in after)


# ── 14. A8 read gate ─────────────────────────────────────────────────────────


def test_served_pages_re_pass_the_a8_availability_gate_at_read_time(turn_ctx_env):
    """WITHHELD CONTEXT IS NEVER COMPILED — re-gated at READ time: a page whose
    text is governed and still AVAILABLE serves a turn; the moment its
    finalization is WITHHELD, the very next compile excludes it with an
    a8_availability reason. Serving re-asks A8, every time."""
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    text_y = "A8-READ-Y peregrine margin folio 8804 scenario-fourteen"
    commit = _mint_governed_finalization(text_y, turn_id="turn-read-y")
    fid = commit["finalization_id"]

    session = "sess-read-y"
    admitted = _admit(text_y, session, turn_id="turn-read-y-admit")
    close_turn_scope(PRINCIPAL, session, "turn-read-y-admit")

    available = compile_turn_context(
        PRINCIPAL, session, query_text="peregrine margin folio"
    )
    assert [item.admission_id for item in available.items] == [admitted["admission_id"]]
    assert text_y in available.block_text

    assert set_availability(
        fid, AVAILABILITY_WITHHELD, reason="privacy hold", governance_actor="owner"
    ) is True

    withheld = compile_turn_context(PRINCIPAL, session, query_text="peregrine margin folio")
    assert withheld.receipt()["selected"] == []
    reasons = _exclusion_reasons(withheld)
    assert any("a8_availability" in reason for reason in reasons), reasons
    assert any("withheld" in reason for reason in reasons), reasons
    assert text_y not in withheld.block_text


# ── 15. Restart persistence ──────────────────────────────────────────────────


def test_admissions_and_bytes_survive_a_restart_of_the_same_home(turn_ctx_env):
    """PERSISTENT retention is what makes context survive a restart: after the
    process reopens the SAME home and database (the rig's in-process restart),
    the scoped admission is still inspectable and the page still recalls and
    compiles to its exact admitted bytes."""
    content = "scenario-fifteen amber quarantine logbook revert channel"
    admitted = _admit(content, "sess-persist-1", turn_id="turn-persist-1")
    close_turn_scope(PRINCIPAL, "sess-persist-1", "turn-persist-1")

    # SIMULATE RESTART in-process: same files reopened, in-memory overrides re-set.
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from core.runtime_paths import configure_runtime_home
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    configure_runtime_home(turn_ctx_env["home"])
    sdb.configure_default_db_path(turn_ctx_env["db_path"])
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())

    records = inspect_admissions(PRINCIPAL, session_id="sess-persist-1")
    assert [r["admission_id"] for r in records] == [admitted["admission_id"]]

    recalled = recall_page(PRINCIPAL, admitted["admission_id"])
    assert recalled["content"] == content  # exact bytes, not a regeneration

    compilation = compile_turn_context(
        PRINCIPAL, "sess-persist-1", query_text="amber quarantine logbook"
    )
    assert content in compilation.block_text


# ── 16. Honest cache receipt ─────────────────────────────────────────────────


def test_receipt_never_claims_a_provider_cache_hit_it_did_not_measure(turn_ctx_env):
    """A CACHE HIT IS MEASURED, NEVER INFERRED: the compilation receipt stamps
    provider_cache as UNMEASURED and cache_hit_measured as False — an equal
    local layout hash is not evidence about what a provider kept resident."""
    content = "scenario-sixteen northern gimbal dockyard marline signal"
    admitted = _admit(content, "sess-receipt-1", turn_id="turn-receipt-1")
    close_turn_scope(PRINCIPAL, "sess-receipt-1", "turn-receipt-1")

    compilation = compile_turn_context(
        PRINCIPAL, "sess-receipt-1", query_text="northern gimbal dockyard"
    )
    receipt = compilation.receipt()
    assert receipt["provider_cache"] == "unmeasured"
    assert receipt["cache_hit_measured"] is False
    for key, value in receipt.items():
        if "hit" in str(key).lower():
            assert value is False, f"receipt claims a measured hit at {key!r}: {value!r}"
    assert receipt["selected"][0]["admission_id"] == admitted["admission_id"]


# ── 17. Generation counter ───────────────────────────────────────────────────


def test_generation_starts_at_one_and_bumps_are_receipted_in_the_digest(turn_ctx_env):
    """THE LEGAL ESCAPE IS bump_context_generation: a fresh session's generation
    is 1; a bump moves it to 2 and appends a generation_bumped line to the
    session's append-only digest — never a silent reset."""
    session = "sess-generation-1"
    assert current_generation(PRINCIPAL, session) == 1

    baseline = compile_turn_context(PRINCIPAL, session, query_text="anything at all")
    assert baseline.digest_text == ""

    bumped = bump_context_generation(PRINCIPAL, session, reason="model switch")
    assert bumped == 2
    assert current_generation(PRINCIPAL, session) == 2

    after = compile_turn_context(PRINCIPAL, session, query_text="anything at all")
    assert after.generation == 2
    assert after.digest_text.startswith(baseline.digest_text)
    assert "generation_bumped" in after.digest_text
    assert "generation=2" in after.digest_text
