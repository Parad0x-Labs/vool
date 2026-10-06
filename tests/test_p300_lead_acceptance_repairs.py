"""Lead-owned pins for the two defects the independent acceptance pack found
on the frozen head (paired300-memory-repair-20260930, acceptance C3c + C5B).

1. Hedge-in-neighbouring-clause escape (C3c): a current-anchored volatile
   quantity shipped on a zero-observation turn whenever ANY hedge word fell
   inside the recognizer's +/-120-char window — even a hedge belonging to a
   different clause ("... is currently 21 degrees and usually quiet after
   eight"). The hedge exemption is now scoped to the value's own clause;
   a hedge directly on the value ("typically 24-27 C") still exempts the
   climate generalization.
2. Ordinal ask without second person (C5B): "What was the third eyepiece in
   my lunar ranking?" carried no assistant-output-ask cue, so the ordinal
   binding never engaged and the ask surfaced only the list headline. The
   cue grammar now also fires on an ordinal bound to an ordered-collection
   noun; probes stay assistant-role filtered (user-authored lists never
   bind). Native reopen case below uses a NEW world (harbor moorings), not
   the acceptance author's.
"""

from __future__ import annotations

import pytest


# ── 1. hedge clause-scoping ───────────────────────────────────────────────────


def test_hedge_in_neighbouring_clause_does_not_exempt_the_reading() -> None:
    from core.model_output_guard import unobserved_live_value_claims

    q = "Is tonight a good time to get a lifting session in at Ironworks?"
    for text, expected in [
        (
            "Tonight could work - the platform area is currently 21 degrees "
            "and usually quiet after eight.",
            ("temperature",),
        ),
        (
            "Tonight could work - the drop-in fee is currently 8 euros and it "
            "is usually quiet after eight.",
            ("price",),
        ),
        (
            "The platform area is currently 21 degrees, which usually feels "
            "mild.",
            ("temperature",),
        ),
        # the hedge owns the value: climate, not a reading — still exempt
        ("In August it is typically 24-27 C in the afternoons.", ()),
        ("It is usually 21 degrees in the hall at this hour.", ()),
    ]:
        kinds = unobserved_live_value_claims(text, user_turn_text=q)
        # conviction may carry additional kinds (a reading is also a measured
        # quantity); the expected kind must be present. An expected EMPTY set
        # must require an actually empty result — a subset assertion with an
        # empty expectation passes for every outcome and proves nothing.
        if expected:
            assert set(expected) <= set(kinds), (text, kinds, expected)
        else:
            assert kinds == (), (text, kinds, expected)


def test_same_clause_hedge_before_the_value_is_preserved_through_delivery() -> None:
    # Owner-review R09 mechanism, new world: the hedge stands directly before
    # the value with no clause boundary between them, so the value's clause
    # begins at the sentence start and the hedge must be inside it. The
    # generalization ships intact instead of being withdrawn as a reading.
    from core.agent_runtime.response import _validate_final_chat_output

    reply = "The kiln shed runs typically 18 degrees this week."
    context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": "hedge-preserve",
        "conversation_history": [
            {"role": "user", "content": "How warm does the kiln shed run these days?"}
        ],
    }
    assert _validate_final_chat_output(reply, source_context=context) == reply


def test_hedge_in_a_neighbouring_sentence_never_exempts_a_current_reading() -> None:
    # The clause may start at the sentence start only: a hedge in the previous
    # sentence must not exempt a separate current observation in the next one.
    from core.model_output_guard import (
        _LIVE_TEMPERATURE_RE,
        _value_clause,
        unobserved_live_value_claims,
    )

    body = "The usually quiet kiln shed is one thing. It reads currently 31 degrees."
    match = _LIVE_TEMPERATURE_RE.search(body)
    assert match is not None
    left, right = _value_clause(body, match.start(), match.end())
    assert "usually" not in body[left:right].lower(), body[left:right]
    kinds = unobserved_live_value_claims(body, user_turn_text="How warm is the kiln shed now?")
    assert "temperature" in kinds, kinds

    from core.agent_runtime.response import _validate_final_chat_output

    delivered = _validate_final_chat_output(
        body,
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "chat_id": "hedge-cross-sentence",
            "conversation_history": [
                {"role": "user", "content": "How warm is the kiln shed now?"}
            ],
        },
    )
    assert "31 degrees" not in delivered
    assert "not going to state" in delivered


def test_hedge_after_a_clause_boundary_still_never_exempts_the_reading() -> None:
    # Owner-review R10 mechanism, new subject: the "and"-joined hedge belongs
    # to the neighbouring clause and must not exempt the current reading.
    from core.agent_runtime.response import _validate_final_chat_output

    reply = "The glaze room is currently 30 degrees and the corridor is usually cool."
    delivered = _validate_final_chat_output(
        reply,
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "chat_id": "hedge-adjacent-clause",
            "conversation_history": [
                {"role": "user", "content": "How warm is the glaze room right now?"}
            ],
        },
    )
    assert "30 degrees" not in delivered
    assert "not going to state" in delivered


def test_hedge_escape_is_refused_through_the_delivery_seam() -> None:
    from apps.vool_agent import ChatTurnResult, VoolAgent, ResponseClass
    from core.agent_runtime.response import decorate_chat_response

    agent = VoolAgent(
        backend_name="pin-backend", device="p300-lead-pin", persona_id="default"
    )
    delivered = decorate_chat_response(
        agent,
        ChatTurnResult(
            text=(
                "Tonight could work - the platform area is currently 21 "
                "degrees and usually quiet after eight."
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="pin:live-hedge",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "chat_id": "pin:live-hedge",
            "conversation_history": [
                {
                    "role": "user",
                    "content": "Is tonight a good time to get a lifting session in?",
                }
            ],
        },
        include_hive_footer=False,
    )
    assert "21 degrees" not in delivered
    assert "can't verify" in delivered.lower() or "not going to state" in delivered.lower()


# ── 2. ordinal ask without second person ─────────────────────────────────────


def test_cue_grammar_matches_collection_ordinal_asks_only() -> None:
    from core.context_retrieval import _ASSISTANT_OUTPUT_ASK_RE

    for ask, want in [
        ("What was the third eyepiece in my lunar ranking?", True),
        ("What is the second entry in the tasting lineup?", True),
        ("Remind me of the fifth stretch in the warmup roster.", True),
        ("the 7th job in the list you provided?", True),
        ("What is the second largest city in Portugal?", False),
        ("How long have I been using my tracker?", False),
        ("What did the fourth chapter say about tariffs?", False),
    ]:
        assert bool(_ASSISTANT_OUTPUT_ASK_RE.search(ask)) is want, ask


@pytest.fixture()
def mooring_home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "mooring-profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    from storage.db import configure_default_db_path

    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    from storage.migrations import run_migrations

    run_migrations()
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    chat = "harbor-moorings"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    result = store_turn(
        chat,
        "Can you rank the four guest moorings for a shallow-draft sloop?",
        "Your mooring ranking, best first:\n"
        "1. The north jetty buoy has the deepest swing room.\n"
        "2. The quay wall slot is calmest in a westerly.\n"
        "3. The pilot ladder berth is easiest to approach at slack.\n"
        "4. The fuel dock corner is last resort for noise.",
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home)},
    )
    assert result["status"] in {"stored", "retained"}
    yield home, chat
    configure_default_db_path(None)
    configure_runtime_home(None)


def test_collection_ordinal_ask_binds_the_item_after_reopen(mooring_home) -> None:
    home, chat = mooring_home
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    question = "What was the third mooring in the ranking?"
    transcript, _ = bc.canonical_runtime_transcript(
        session_id=chat,
        source_context={
            "chat_id": chat,
            "runtime_home": str(home),
            "conversation_history": [{"role": "user", "content": question}],
        },
        current_user_text=question,
    )
    text = "\n".join(str(m.get("content") or "") for m in transcript)
    assert "pilot ladder berth" in text, text
    assistant_lines = [ln for ln in text.splitlines() if ln.startswith("- assistant said")]
    assert any("pilot ladder berth" in ln for ln in assistant_lines), text
    user_lines = [ln for ln in text.splitlines() if ln.startswith("- user said")]
    assert not any("pilot ladder berth" in ln for ln in user_lines), text


def test_user_authored_numbered_list_never_binds(mooring_home) -> None:
    home, chat = mooring_home
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import reset_retrieval_telemetry, store_turn
    from core.memory.entries import resolve_memory_access_policy

    other = "skippers-notes"
    ensure_chat_namespace(other, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=other)
    store_turn(
        other,
        "My own spares inventory, in order:\n1. The storm cover\n2. The spare "
        "anchor light\n3. The kedging line",
        "Noted — your spares inventory is saved.",
        access_policy=policy,
        source_context={"chat_id": other, "runtime_home": str(home)},
    )
    import core.bootstrap_context as bc

    reset_retrieval_telemetry()
    question = "What was the third item in my spares list?"
    transcript, _ = bc.canonical_runtime_transcript(
        session_id=other,
        source_context={
            "chat_id": other,
            "runtime_home": str(home),
            "conversation_history": [{"role": "user", "content": question}],
        },
        current_user_text=question,
    )
    text = "\n".join(str(m.get("content") or "") for m in transcript)
    # a user-authored list is never misattributed as assistant output; the
    # ordinal may bind only within assistant-role material
    assert not any(
        ln.startswith("- assistant said") and "kedging line" in ln
        for ln in text.splitlines()
    ), text
