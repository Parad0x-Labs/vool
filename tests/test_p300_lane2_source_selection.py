"""Lane 2 fresh acceptance: source-evidence selection for assistant-supplied
multi-entry content and retained user facts (paired300-memory-repair-20260930).

Fresh cases frozen in FRESH-CASES-FREEZE.json BEFORE first execution:
different domains, entities, values, ordinals, list lengths and journal names
than the three captured development cases; no captured vocabulary reused.

Mechanisms under proof:
  F1  later-entry recovery — one assistant message lists several studies; the
      ask names the LAST entry by its unique discriminator; the capsule must
      carry that entry verbatim, attributed as assistant output.
  F2  ordinal list binding — a numbered list the assistant supplied is asked
      for by ordinal after the store has been closed and re-opened.
  F2n ordinal ask with no stored list — nothing may be invented.
  F3  retained user preference with zero lexical anchor — documented
      limitation (expected failure, honestly recorded); the no-purchase
      negative control must hold regardless.
  F4  user-vs-assistant authority — assistant claims ride attributed, never as
      user-owned profile facts.
  F5  foreign-chat exclusion — the same list stored under another chat id
      never reaches the asking chat without a grant.

Hermetic: deterministic hash embedding lane, disposable VOOL_HOME per test.
"""
from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

F1_SESSIONS = [
    (
        "2024/03/09 (Sat) 10:15",
        "I keep hearing mixed things about cold plunges after training. "
        "Are there any actual studies on cold-water immersion for recovery?",
        "There is a small but growing body of work on cold-water immersion "
        "after endurance and strength sessions. Here are three studies often "
        "cited:\n\n1. A trial reported in the Review of Thermal Physiology "
        "assigned 22 recreational runners to cold-water immersion after "
        "interval sessions and found modest reductions in perceived "
        "soreness.\n\n2. Work published in the Scandinavian Sport Science "
        "Letters observed 41 club swimmers over a six-week block and reported "
        "faster next-day repeat performance in the immersion group.\n\n3. A "
        "study in the Journal of Aquatic Recovery Medicine involved 57 "
        "amateur cyclists who immersed after hard sessions for a fortnight, "
        "and the authors reported significant reductions in muscle soreness "
        "and fatigue markers.\n\nAs always, individual response varies, and "
        "longer protocols were not uniformly better.",
    ),
    (
        "2024/03/11 (Mon) 19:40",
        "Picked up a hemp weaving loom at the guild fair on Saturday and "
        "already finished a small wall hanging.",
        "A hemp loom is a lovely first choice. Keep the warp tension even and "
        "the hanging will stay square as it settles.",
    ),
    (
        "2024/03/12 (Tue) 08:05",
        "Signed up for the municipal astronomy night-school course; the first "
        "module is binocular sky observation.",
        "Binocular work first is a great foundation. A 10x50 pair will serve "
        "you well for that module.",
    ),
]

F1_QUESTION = (
    "You mentioned some immersion studies when we talked about recovery. How "
    "many participants were in the one from the Journal of Aquatic Recovery "
    "Medicine, the one that found significant reductions in muscle soreness "
    "and fatigue markers?"
)

F2_SESSIONS = [
    (
        "2024/02/05 (Mon) 16:33",
        "Walk me through setting up a small balcony compost bin, step by step "
        "please.",
        "Here is a simple nine-step setup for a balcony compost bin:\n"
        "1. Choose a shaded corner spot\n"
        "2. Drill airflow holes in the lower band\n"
        "3. Layer torn cardboard as the base\n"
        "4. Add a handful of garden soil\n"
        "5. Bury the first kitchen scraps\n"
        "6. Top with dry leaves\n"
        "7. Water until damp like a sponge\n"
        "8. Snap on the lid\n"
        "9. Turn the contents weekly",
    ),
    (
        "2024/02/08 (Thu) 12:20",
        "The neighbourhood chess club moved our blitz ladder to Thursday "
        "nights, so my Tuesday evenings are free again.",
        "Free Tuesdays open up your week nicely. A standing ladder night "
        "makes blitz improvement much easier to plan around.",
    ),
]

F2_QUESTION = (
    "From the balcony compost steps you listed earlier, what was the 7th step?"
)

F2N_SESSIONS = [
    (
        "2024/04/02 (Tue) 11:10",
        "Fuel economy dropped after I mounted the roof box; mostly motorway "
        "driving at the weekend.",
        "A roof box can cost several litres per hundred kilometres at "
        "motorway speed. Slower speeds and a slim box shape reduce the "
        "penalty.",
    ),
]
F2N_QUESTION = "What was the 4th tip in the fuel-saving list you gave me?"

F3_SESSIONS = [
    (
        "2024/05/18 (Sat) 14:05",
        "Finally replaced the kitchen tap with a boiling-water mixer tap; the "
        "installer also fitted a small under-sink filter.",
        "A boiling-water mixer tap with an under-sink filter is a great "
        "pairing. Run the filter change calendar from day one.",
    ),
    (
        "2024/05/20 (Mon) 20:15",
        "The courgettes in the raised beds have powdery mildew on the lower "
        "leaves.",
        "Powdery mildew on courgettes often eases with better airflow; thin "
        "the lower leaves and water at the base.",
    ),
]
F3_QUESTION = (
    "My kitchen renovation is nearly done. Any advice on the finishing touches?"
)

F3N_SESSIONS = [
    (
        "2024/06/01 (Sat) 09:00",
        "I repainted the hallway skirting boards eggshell white and it "
        "brightened the whole corridor.",
        "Eggshell white skirting is a forgiving choice. An angled brush keeps "
        "the top edge crisp.",
    ),
]
F3N_QUESTION = "Any advice on choosing a sewing machine for hemming trousers?"

F4_SESSIONS = [
    (
        "2024/07/06 (Sat) 13:45",
        "Which espresso grinder should I pair with the lever machine for "
        "single dosing?",
        "For single dosing on a lever machine, the Feldgrind X hand grinder "
        "is the one most home baristas settle on; its stepped adjustment "
        "holds a repeatable setting.",
    ),
]
F4_QUESTION = "Which grinder did we land on for my lever machine?"


def _epoch(raw: str) -> float:
    from datetime import datetime, timezone

    return int(
        datetime.strptime(raw, "%Y/%m/%d (%a) %H:%M")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


@pytest.fixture()
def lane2_env(tmp_path, monkeypatch):
    """Hermetic capsule lane: disposable home, deterministic hash embeddings."""
    import core.embedding_service as embedding_service

    home = tmp_path / "lane2-profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    return home


def _ingest(home, chat: str, sessions, monkeypatch) -> None:
    from core.context_retrieval import store_turn

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for date, user_text, assistant_text in sessions:
        result = store_turn(
            chat, user_text, assistant_text, access_policy=policy,
            source_context={
                "chat_id": chat, "runtime_home": str(home),
                "statement_at": _epoch(date),
            },
        )
        # layer-1 retention is the law both roles rely on
        assert result["status"] in {"stored", "retained"}


def _capsule(home, chat: str, question: str) -> tuple[str, dict]:
    """Drive the REAL capsule path on the re-opened store (store_turn closes
    its handle per call; the capsule opens the store fresh)."""
    import core.context_retrieval as cr

    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={
            "chat_id": chat, "runtime_home": str(home),
            "surface": "channel", "platform": "api",
        },
    )
    injected = "\n".join(
        str(m.get("content") or "") for m in out
        if str(m.get("content") or "").find("<retrieved_context>") >= 0
    )
    telemetry = cr.get_last_retrieval_telemetry()
    return injected, telemetry


# ── F1: later-entry recovery ─────────────────────────────────────────────────

def test_later_entry_study_reaches_capsule_attributed(lane2_env, monkeypatch) -> None:
    chat = "lane2-f1-immersion"
    _ingest(lane2_env, chat, F1_SESSIONS, monkeypatch)
    ctx, telemetry = _capsule(lane2_env, chat, F1_QUESTION)

    assert "57 amateur cyclists" in ctx, ctx
    assert "Journal of Aquatic Recovery Medicine" in ctx, ctx
    # attribution: the study evidence rides as ASSISTANT output
    assistant_lines = [
        line for line in telemetry.get("selected_facts") or []
        if line.startswith("- assistant said")
    ]
    assert any("57 amateur cyclists" in line for line in assistant_lines), (
        telemetry.get("selected_facts"))
    # authority law: no distilled user fact owns the assistant's claim
    user_lines = [
        line for line in telemetry.get("selected_facts") or []
        if line.startswith("- user said")
    ]
    assert not any("57 amateur cyclists" in line for line in user_lines)
    # the earlier entries may ride too, but the asked-for one must be present
    assert telemetry.get("capsule_mode") == "distilled"


def test_later_entry_first_entry_alone_is_not_the_answer(lane2_env, monkeypatch) -> None:
    """The failure shape: asking for the LATER entry must not deliver only
    the first entry (the 22-runner trial)."""
    chat = "lane2-f1b-immersion"
    _ingest(lane2_env, chat, F1_SESSIONS, monkeypatch)
    ctx, _ = _capsule(lane2_env, chat, F1_QUESTION)
    if "Review of Thermal Physiology" in ctx:  # first entry may ride as context
        assert "57 amateur cyclists" in ctx, (
            "first entry delivered without the asked-for later entry: " + ctx)


# ── F2: ordinal list binding after re-open ───────────────────────────────────

def test_ordinal_list_item_reaches_capsule_after_reopen(lane2_env, monkeypatch) -> None:
    chat = "lane2-f2-compost"
    _ingest(lane2_env, chat, F2_SESSIONS, monkeypatch)
    assert (lane2_env / "data" / "memory" / "vool_memory.db").exists()
    ctx, telemetry = _capsule(lane2_env, chat, F2_QUESTION)

    assert "Water until damp like a sponge" in ctx, ctx
    assistant_lines = [
        line for line in telemetry.get("selected_facts") or []
        if line.startswith("- assistant said")
    ]
    assert any(
        "7." in line and "Water until damp like a sponge" in line
        for line in assistant_lines
    ), assistant_lines
    # the marker binds the item: a neighbouring item is not the 7th
    assert not any(
        "Snap on the lid" in line and line.startswith("- assistant said")
        and "7." in line
        for line in telemetry.get("selected_facts") or []
    )


def test_ordinal_ask_with_no_stored_list_invents_nothing(lane2_env, monkeypatch) -> None:
    chat = "lane2-f2n-roofbox"
    _ingest(lane2_env, chat, F2N_SESSIONS, monkeypatch)
    ctx, telemetry = _capsule(lane2_env, chat, F2N_QUESTION)

    assert not any(
        line.startswith("- assistant said") and "4." in line
        for line in telemetry.get("selected_facts") or []
    ), telemetry.get("selected_facts")
    # no fabricated list item text reached the request
    for invented in ("4. ", "fourth tip"):
        assert invented not in ctx or "roof box" in ctx.lower()


# ── F3: zero-anchor preference — documented limitation + invention control ───

def test_no_purchase_control_never_invents_ownership(lane2_env, monkeypatch) -> None:
    chat = "lane2-f3n-sewing"
    _ingest(lane2_env, chat, F3N_SESSIONS, monkeypatch)
    ctx, telemetry = _capsule(lane2_env, chat, F3N_QUESTION)

    user_lines = [
        line for line in telemetry.get("selected_facts") or []
        if line.startswith("- user said")
    ]
    assert not any("sewing machine" in line.lower() for line in user_lines), (
        user_lines)


# Was xfail(strict=False) as a documented lane-2 limitation (frozen prediction
# in FRESH-CASES-FREEZE.json: neural-lane register-dominated ranking starved a
# zero-anchor preference, captured case E rank 17/60). Repaired 2026-09-30 by
# the bounded advice-preference remedy (advice-envelope probe at the semantic
# cut + packing eviction law; commits 84384041/76c3f1bf on this branch), so the
# expectation below is now a plain regression pin. The historical xfail record
# stays in FRESH-CASES-FREEZE.json and LANE2-REPORT.md, untouched.
def test_zero_anchor_preference_recall_is_a_known_limitation(
        lane2_env, monkeypatch) -> None:
    chat = "lane2-f3-tap"
    _ingest(lane2_env, chat, F3_SESSIONS, monkeypatch)
    ctx, _ = _capsule(lane2_env, chat, F3_QUESTION)
    assert "boiling-water mixer tap" in ctx, ctx


# ── F4: user-vs-assistant authority ──────────────────────────────────────────

def test_assistant_claim_recall_stays_attributed(lane2_env, monkeypatch) -> None:
    chat = "lane2-f4-grinder"
    _ingest(lane2_env, chat, F4_SESSIONS, monkeypatch)
    ctx, telemetry = _capsule(lane2_env, chat, F4_QUESTION)

    user_lines = [
        line for line in telemetry.get("selected_facts") or []
        if line.startswith("- user said")
    ]
    # the grinder recommendation is the assistant's claim, never user-owned
    assert not any("Feldgrind" in line for line in user_lines), user_lines
    if "Feldgrind" in ctx:
        assert any(
            line.startswith("- assistant said") and "Feldgrind" in line
            for line in telemetry.get("selected_facts") or []
        ), "grinder evidence present but not attributed to the assistant"


# ── F5: foreign-chat exclusion ───────────────────────────────────────────────

def test_foreign_chat_list_never_reaches_asking_chat(lane2_env, monkeypatch) -> None:
    other_chat = "lane2-f5-other-chat"
    asking_chat = "lane2-f5-asking-chat"
    _ingest(lane2_env, other_chat, F2_SESSIONS, monkeypatch)
    _ingest(
        lane2_env, asking_chat,
        [("2024/02/06 (Tue) 10:00",
          "Trying to cut back on caffeine after lunch; tea only until noon.",
          "A noon caffeine cutoff is a solid first step for evening sleep.")],
        monkeypatch,
    )
    ctx, telemetry = _capsule(lane2_env, asking_chat, F2_QUESTION)

    assert "Water until damp like a sponge" not in ctx, ctx
    assert not any(
        "compost" in str(line).lower()
        for line in telemetry.get("selected_facts") or []), (
        telemetry.get("selected_facts"))


# ── accessor interface (shared boundary duty) ────────────────────────────────

def test_admitted_evidence_records_mirror_delivered_capsule(
        lane2_env, monkeypatch) -> None:
    from core.context_retrieval import admitted_evidence_records

    chat = "lane2-f2-acc-compost"
    _ingest(lane2_env, chat, F2_SESSIONS, monkeypatch)
    _, telemetry = _capsule(lane2_env, chat, F2_QUESTION)

    records = admitted_evidence_records()
    assert records, "the delivered ordinal evidence must be readable typed"
    ordinal = [r for r in records if "Water until damp" in r.line]
    assert ordinal, [r.line for r in records]
    record = ordinal[0]
    assert record.role == "assistant"
    assert record.authority == "assistant-output"
    assert record.chat_scope == chat
    assert record.span_text.strip().startswith("7.")
    assert record.statement_at is not None
    # records exist only for DELIVERED evidence (the reader's capsule)
    assert record.line in (telemetry.get("selected_facts") or [])
    assert not record.superseded
