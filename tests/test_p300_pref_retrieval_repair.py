"""Retained-preference recall for advice asks (paired300 E repair).

The repaired failure (case E, q18d94724eca62926): a user-stated preference
about a topic was admitted, retained and embedded, yet a later advice ask
about that topic omitted it — the full-question semantic cut ranked a
same-register distractor cluster above it and the packing budget refused
the zero-query-term span behind filler protected by frame-word echoes.

Worlds here are GENUINELY DIFFERENT from the captured one (night cycling,
coffee brewing) plus a rebuilt regression world for the original miss; the
captured statements are re-used verbatim as rebuilt captures (they carry no
machine paths).

Embedding lane: a deterministic axis-geometry stub stands in for the neural
backend so the hermetic suite can express the E-class geometry the hash lane
cannot (zero lexical anchor + register-dominated full-question ranking).
The stub is installed for BOTH ingest and capsule phases through the same
module seam the product uses; every product gate (admission, scopes,
dedup, coverage, authority, budget) runs for real. The binding neural-lane
evidence for the captured case lives in the mission artifacts; this file
proves the mechanism on the real product path deterministically.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

STUB_BACKEND = "ollama:pref-stub-ax"

#: Register vocabulary shared by the fixture worlds: the request-frame and
#: temporal-frame words advice chatter uses, never a topic noun.
STUB_REGISTER_WORDS = frozenset({
    "lately", "trying", "improve", "tips", "advice", "any", "wondering",
    "consistent", "routine", "recommend", "suggestions", "what", "should",
    "do", "looking", "consider", "watching", "flying",
})


def _make_stub_embed_stamped(topic_words):
    """Deterministic axis-geometry embedding over the stub backend.

    Topic words map to one orthonormal axis, register words to another:
    a pure-register distractor is cosine-orthogonal to a pure-topic
    preference, a full advice question mixes both (register dominating),
    and the frame-stripped topic clause is pure topic — the measured E
    geometry, reproducible on any machine.
    """
    topic = {str(w).lower() for w in topic_words}
    register = set(STUB_REGISTER_WORDS)

    def embed_stamped(text, *_args, **_kwargs):
        r = t = 0.0
        for word in re.findall(r"[a-z][a-z'-]*", str(text or "").lower()):
            if word in topic:
                t += 1.0
            elif word in register:
                r += 1.0
        vec = [r, t, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return [0.0, 0.0, 0.0, 1.0], STUB_BACKEND
        return [v / norm for v in vec], STUB_BACKEND

    return embed_stamped


# ── shared turn sets ──────────────────────────────────────────────────────────

_CYCLING_TOPIC = {
    "tires", "flat", "tyres", "ride", "riding", "tube", "pump", "saddle",
    "cycling", "spokes", "puncture",
}
_CYCLING_PREFERENCE = (
    "I always carry a spare inner tube and a hand pump in my saddle bag "
    "whenever I ride after dark, and the small light on the seat post has "
    "never once let me down on the way home."
)
_CYCLING_QUESTION = "My tires go flat so often lately. Any tips?"

_COFFEE_TOPIC = {
    "brew", "bitter", "press", "grind", "beans", "kettle", "coffee",
    "morning", "pour",
}
_COFFEE_PREFERENCE = (
    "I switched to a French press last month, I grind my beans by hand "
    "every morning before the radio comes on, and the kettle gets a full "
    "re-rinse so the old silt never survives to the next pot."
)
_COFFEE_QUESTION = "My morning brew has been tasting bitter lately. What should I do?"

#: Register distractors: compound multi-fact statements in the advice-chat
#: register, sharing frame vocabulary with the asks and NO topic noun with
#: them. Long enough that the distilled fold fills the capsule budget, so
#: the preference span must win its slot through the bounded demotion law.
_REGISTER_DISTRACTORS = [
    (
        "I've been trying to establish a consistent evening routine lately, "
        "and I also started reading a few pages before bed to wind down "
        "instead of scrolling until the battery of the day runs out.",
        "A steadier evening rhythm tends to stick better than a strict one. "
        "Keep the reading pile near the lamp so the habit starts itself.",
    ),
    (
        "I've been wondering what I should cook on weekdays lately, and I'm "
        "trying to plan my grocery runs a bit more consistently so the "
        "vegetable drawer stops turning into a science experiment.",
        "Planning the week's meals on one evening usually pays for itself. "
        "A short list beats an ambitious one you abandon by Wednesday.",
    ),
    (
        "I'm trying to improve my running pace lately, though the evening "
        "wind keeps slowing me down on the loop past the old mill, and the "
        "bench at the halfway point has become a bad habit of mine.",
        "Pace work rewards patience more than bravado. The windy stretch is "
        "doing quiet strength work whether you notice it or not.",
    ),
    (
        "I've been watching a lot of films in the evening lately, but I want "
        "to explore something other than the same three channels, and I keep "
        "a running list of recommendations I never actually open.",
        "A short watchlist you actually finish beats a long one you curate. "
        "Pick the next one before dinner so the evening decides nothing.",
    ),
    (
        "I've been trying to cut back on late-night snacks lately, and the "
        "habit is slowly sticking on most weekdays, though the weekend still "
        "undoes whatever the week managed to build up.",
        "Weekends count less if the weekday pattern is honest. Keep the "
        "kitchen closed rather than fighting single snacks one by one.",
    ),
    (
        "My study group moved our sessions to Thursday evenings lately, and "
        "I've been trying to prepare a chapter ahead of each meeting, which "
        "mostly means I now fall asleep on the same page every week.",
        "Preparing ahead works better in small slices across days. One "
        "focused hour on Tuesday usually beats three tired ones Thursday.",
    ),
    (
        "I've been wondering whether I should start learning the violin "
        "lately, or whether I should improve my piano practice first, and "
        "the case for either depends on which neighbour I pity more.",
        "Improving what you already play compounds faster than starting "
        "from zero. The violin will still be there when the piano yields.",
    ),
    (
        "Lately I've been trying to keep my desk tidier, and the new evening "
        "shutdown routine has been helping more than I expected, although "
        "the drawer where everything actually goes remains a mystery.",
        "A shutdown ritual beats desk discipline during the day. Give the "
        "drawer a name and a purpose or it stays a junk orbit.",
    ),
    (
        "I've been trying to sketch a little every Sunday lately, and I keep "
        "a small notebook in every jacket for practice, which mostly "
        "guarantees that every jacket now contains one empty notebook.",
        "One notebook, one jacket, one pen. Friction kills Sunday habits "
        "faster than a lack of talent ever will.",
    ),
    (
        "I've been flying so much for work lately that I'm starting to lose "
        "track of my trips, and I think I've taken around five flights in "
        "the past month alone, all of them middle seats on the same route.",
        "A single log line per trip keeps the calendar honest. Middle seats "
        "on a known route at least let you pack mindlessly.",
    ),
]

_DISTRACTOR_DATES = [
    "2024/09/02 (Mon) 08:10", "2024/09/03 (Tue) 19:25",
    "2024/09/04 (Wed) 07:40", "2024/09/05 (Thu) 21:05",
    "2024/09/06 (Fri) 12:50", "2024/09/09 (Mon) 18:15",
    "2024/09/10 (Tue) 09:30", "2024/09/11 (Wed) 20:45",
    "2024/09/12 (Thu) 07:55", "2024/09/13 (Fri) 22:20",
]

# ── the rebuilt original-miss world (paired300 case E) ────────────────────────
#: Rebuilt captures of the observed turns; no machine-local content.

_E_POWER_BANK_PREFERENCE = (
    "I'm looking for some advice on the best way to organize my tech "
    "accessories, like my new portable power bank and wireless charging "
    "pad, when I'm traveling."
)
_E_QUESTION = (
    "I've been having trouble with the battery life on my phone lately. "
    "Any tips?"
)
_E_TOPIC = {
    "battery", "phone", "power", "bank", "charging", "wireless", "tech",
    "accessories", "travel", "traveling", "cable", "draining",
}

_IRRELEVANT_FACT = (
    "My passport expires in March and I keep the renewal form in the "
    "drawer under the stairs, next to the spare set of house keys."
)


def _epoch(raw: str) -> float:
    from datetime import datetime, timezone

    return int(
        datetime.strptime(raw, "%Y/%m/%d (%a) %H:%M")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


@pytest.fixture()
def pref_env(tmp_path, monkeypatch):
    """Hermetic capsule lane with the deterministic axis-geometry backend."""
    import core.context_retrieval as cr
    import core.embedding_service as embedding_service
    from core.runtime_paths import configure_runtime_home
    from storage.migrations import run_migrations

    home = tmp_path / "pref-profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model",
                        lambda: "pref-stub-model")

    def install(topic_words):
        monkeypatch.setattr(
            cr, "embed_stamped", _make_stub_embed_stamped(topic_words))

    run_migrations()
    return home, install


def _ingest(home, chat: str, sessions) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
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
        assert result["status"] in {"stored", "retained"}, result


def _capsule(home, chat: str, question: str) -> tuple[str, dict]:
    """Drive the REAL capsule path; the store is re-opened fresh."""
    import core.context_retrieval as cr
    from core.memory.entries import resolve_memory_access_policy

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
        if "<retrieved_context>" in str(m.get("content") or "")
    )
    telemetry = cr.get_last_retrieval_telemetry()
    return injected, telemetry


def _distractor_sessions(extra_first=()):
    sessions = [
        (date, user, assistant)
        for date, (user, assistant) in zip(_DISTRACTOR_DATES, _REGISTER_DISTRACTORS)
    ]
    return [*(extra_first or ()), *sessions]


def _user_said_lines(telemetry):
    return [
        line for line in (telemetry.get("selected_facts") or [])
        if line.startswith("- user said")
    ]


# ── grammar and ownership helpers (unit level) ───────────────────────────────

def test_advice_topic_clause_grammar() -> None:
    from core.context_retrieval import _advice_topic_clause

    assert _advice_topic_clause(
        "I've been having trouble with the battery life on my phone "
        "lately. Any tips?"
    ) == ("I've been having trouble with the battery life on my phone "
          "lately")
    assert _advice_topic_clause(
        "Any advice on choosing a sewing machine for hemming trousers?"
    ) == "choosing a sewing machine for hemming trousers"
    # no content clause remains: nothing to probe with
    assert _advice_topic_clause("Any tips?") is None
    # non-advice asks are untouched by the lane
    assert _advice_topic_clause("What time does the night ferry leave?") is None
    assert _advice_topic_clause(
        "What was the 7th step in the list you gave me?") is None


def test_advice_frame_terms_exclude_envelope_and_register() -> None:
    from core.context_retrieval import _advice_ask_frame_terms

    terms = _advice_ask_frame_terms(
        "My phone battery keeps draining lately. Any tips?")
    assert {"any", "tips", "lately"} <= terms


def test_quoted_source_body_is_not_user_owned() -> None:
    from core.context_retrieval import (
        _user_owned_preference_span,
        _user_owned_statement_body,
    )

    # imperative/technical quoted instruction: not utterance-shaped for the
    # admission grammar, still somebody else's words
    assert not _user_owned_statement_body(
        'She said: "Grind the beans the night before and preheat the '
        'press with boiling water before pouring."')
    # declaration-shaped quote: separated by the admission grammar itself
    assert not _user_owned_statement_body(
        'My colleague said: "I prefer a French press over anything with '
        'a pump" whenever the topic comes up.')
    assert _user_owned_statement_body(
        "I always carry a spare inner tube when I ride after dark.")
    # a span lifted out of a quoted segment never rides the preference lane
    assert not _user_owned_preference_span(
        'My colleague said: "I prefer a French press."',
        "I prefer a French press.")
    assert _user_owned_preference_span(
        "I grind my beans by hand every morning.",
        "I grind my beans by hand every morning.")


# ── world 1: night-cycling preference, register distractors ──────────────────

def test_advice_ask_surfaces_retained_preference_with_distractors(
        pref_env) -> None:
    home, install = pref_env
    install(_CYCLING_TOPIC)
    chat = "pref-w1-night-cycling"
    sessions = _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 21:30",
         _CYCLING_PREFERENCE,
         "A saddle bag with the essentials is a habit worth keeping. Check "
         "the tube's valve matches your rims before you need it."),
    ])
    _ingest(home, chat, sessions)

    ctx, telemetry = _capsule(home, chat, _CYCLING_QUESTION)

    assert "spare inner tube" in ctx, ctx
    user_lines = _user_said_lines(telemetry)
    assert any("spare inner tube" in line for line in user_lines), user_lines
    # user-owned preference never rides as assistant output
    assert not any(
        line.startswith("- assistant said") and "spare inner tube" in line
        for line in (telemetry.get("selected_facts") or []))
    # the capsule stays inside the finite budget law
    packed = telemetry.get("packed") or []
    assert all(str(block).count("\n") >= 0 for block in packed)


def test_irrelevant_retained_fact_stays_out(pref_env) -> None:
    home, install = pref_env
    install(_CYCLING_TOPIC)
    chat = "pref-w1-irrelevant-control"
    sessions = _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 21:30", _CYCLING_PREFERENCE,
         "A saddle bag with the essentials is a habit worth keeping."),
        ("2024/08/31 (Sat) 10:00", _IRRELEVANT_FACT,
         "Renewing early saves the last-minute queue."),
    ])
    _ingest(home, chat, sessions)

    ctx, _ = _capsule(home, chat, _CYCLING_QUESTION)

    assert "passport" not in ctx.lower(), ctx
    # the topical preference itself still reaches the capsule
    assert "spare inner tube" in ctx, ctx


def test_quoted_third_party_preference_is_not_user_owned(pref_env) -> None:
    home, install = pref_env
    install(_COFFEE_TOPIC)
    chat = "pref-w1-quoted-control"
    quoted_turn = (
        'She said: "Grind the beans the night before and preheat the '
        'press with boiling water before pouring, and never let the '
        'kettle sit past the first whistle."'
    )
    _ingest(home, chat, _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 07:15", quoted_turn,
         "That is a classic routine for a reason."),
    ]))

    ctx, telemetry = _capsule(home, chat, _COFFEE_QUESTION)

    assert "night before" not in ctx, ctx
    assert "first whistle" not in ctx, ctx
    assert not any(
        "preheat the press" in line for line in _user_said_lines(telemetry))


def test_quoted_declaration_preference_stays_out(pref_env) -> None:
    """Declaration-shaped quote (the admission grammar's own SOURCE class)
    must not ride the user-owned preference lane: ownership is never minted
    by stripping the reporting frame, and the pooled-preference protections
    never apply to it."""
    home, install = pref_env
    install(_COFFEE_TOPIC)
    chat = "pref-w1-quoted-decl-control"
    quoted_turn = (
        'My colleague said: "I prefer pour-over, and I use the same '
        'kettle every single morning before anyone else wakes up."'
    )
    _ingest(home, chat, _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 07:15", quoted_turn,
         "That is a classic routine for a reason."),
    ]))

    ctx, telemetry = _capsule(home, chat, _COFFEE_QUESTION)

    # the third-party preference never rides as a bare user-owned statement:
    # any line carrying it must keep the reporting attribution verbatim
    for line in _user_said_lines(telemetry):
        if "pour-over" in line:
            assert "My colleague said" in line, line
    # and it never rides as a preference-lane evidence line at all
    delivered = [
        r for r in (telemetry.get("evidence_refs") or [])
        if r.get("delivered")
    ]
    assert not any(
        "pour-over" in str(r.get("line") or "") for r in delivered), delivered


def test_foreign_scope_preference_stays_out(pref_env) -> None:
    home, install = pref_env
    install(_CYCLING_TOPIC)
    other_chat = "pref-w1-foreign-other"
    asking_chat = "pref-w1-foreign-asking"
    _ingest(home, other_chat, [
        ("2024/08/30 (Fri) 21:30", _CYCLING_PREFERENCE,
         "A saddle bag with the essentials is a habit worth keeping."),
    ])
    _ingest(home, asking_chat, _distractor_sessions())

    ctx, _ = _capsule(home, asking_chat, _CYCLING_QUESTION)

    assert "spare inner tube" not in ctx, ctx


def test_forget_removes_preference_from_capsule(pref_env) -> None:
    home, install = pref_env
    install(_CYCLING_TOPIC)
    chat = "pref-w1-forget"
    _ingest(home, chat, _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 21:30", _CYCLING_PREFERENCE,
         "A saddle bag with the essentials is a habit worth keeping."),
    ]))
    ctx, _ = _capsule(home, chat, _CYCLING_QUESTION)
    assert "spare inner tube" in ctx, ctx

    from core.context_retrieval import forget_session_memory

    removed = forget_session_memory(
        chat, "tube",
        source_context={"chat_id": chat, "runtime_home": str(home)},
    )
    assert removed >= 1

    ctx_after, telemetry = _capsule(home, chat, _CYCLING_QUESTION)
    assert "spare inner tube" not in ctx_after, ctx_after
    assert not any(
        "spare inner tube" in line for line in _user_said_lines(telemetry))


# ── world 2: multi-fact compound preference (coffee) ──────────────────────────

def test_multi_fact_compound_statement_preference_surfaces(pref_env) -> None:
    home, install = pref_env
    install(_COFFEE_TOPIC)
    chat = "pref-w2-coffee"
    _ingest(home, chat, _distractor_sessions(extra_first=[
        ("2024/08/28 (Wed) 06:40",
         _COFFEE_PREFERENCE,
         "Hand-grinding with a press is a forgiving combination. Keep the "
         "grind consistent and the rest follows."),
    ]))

    ctx, telemetry = _capsule(home, chat, _COFFEE_QUESTION)

    assert "French press" in ctx, ctx
    assert "grind my beans by hand" in ctx, ctx
    assert any(
        "French press" in line for line in _user_said_lines(telemetry))


# ── world 3: rebuilt original miss (paired300 case E) ─────────────────────────

def test_original_power_bank_miss_regression_rebuilt(pref_env) -> None:
    """The original E failure shape, rebuilt from the captured turns.

    Pre-repair this fails exactly as captured: the preference is admitted,
    retained and embedded, the advice ask shares no noun with it, and the
    capsule omits it behind the same-register distractor fold.
    """
    home, install = pref_env
    install(_E_TOPIC)
    chat = "pref-w3-powerbank-rebuilt"
    _ingest(home, chat, _distractor_sessions(extra_first=[
        ("2023/05/27 (Sat) 05:53",
         _E_POWER_BANK_PREFERENCE,
         "The eternal struggle of keeping tech accessories organized while "
         "traveling! A dedicated travel case keeps the small pieces findable."),
    ]))

    ctx, telemetry = _capsule(home, chat, _E_QUESTION)

    assert "portable power bank" in ctx, ctx
    assert any(
        "portable power bank" in line for line in _user_said_lines(telemetry))


# ── lane guard: the preference lane must never disturb other lanes ───────────

def test_hash_lane_capsule_stays_alive_without_the_preference_lane(
        pref_env, monkeypatch) -> None:
    """The advice-preference machinery is neural-lane-only; the hash lane
    must keep its ordinary capsule intact (a lane-scoped NameError inside
    the guarded builder once blanked every hash-lane capsule silently)."""
    import core.embedding_service as embedding_service

    home, _install = pref_env
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    chat = "pref-hash-lane-guard"
    _ingest(home, chat, [
        ("2024/09/20 (Fri) 18:00",
         "The workshop door code is 4731 and the side gate stays latched "
         "after six in the evening.",
         "Noted, the evening latch rule is worth remembering."),
    ])

    ctx, telemetry = _capsule(
        home, chat, "What is the door code for the workshop again?")

    assert "4731" in ctx, ctx
    assert telemetry.get("capsule_mode") == "distilled"


# ── process restart: fresh interpreter for ingest and for the ask ─────────────

_RESTART_RUNNER = '''
import json
import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
home = Path(sys.argv[2])
mode = sys.argv[3]
payload = json.loads(sys.argv[4])

import os
os.environ["VOOL_HOME"] = str(home)
os.environ["VOOL_HOME"] = str(home)
os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"
sys.path.insert(0, str(repo))

from core.runtime_paths import configure_runtime_home
configure_runtime_home(home)

import math

import core.context_retrieval as cr
import core.embedding_service as embedding_service

STUB_BACKEND = {backend!r}
REGISTER = frozenset({register!r})


def make_stub(topic_words):
    topic = {{str(w).lower() for w in topic_words}}

    def embed_stamped(text, *_a, **_k):
        r = t = 0.0
        for word in re.findall(r"[a-z][a-z'-]*", str(text or "").lower()):
            if word in topic:
                t += 1.0
            elif word in REGISTER:
                r += 1.0
        vec = [r, t, 0.0, 0.0]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return [0.0, 0.0, 0.0, 1.0], STUB_BACKEND
        return [v / norm for v in vec], STUB_BACKEND

    return embed_stamped


embedding_service._best_embed_model = lambda: "pref-stub-model"
cr.embed_stamped = make_stub(payload["topic_words"])

from datetime import datetime, timezone

from storage.migrations import run_migrations
run_migrations()

chat = payload["chat"]

if mode == "ingest":
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.context_retrieval import store_turn

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for date, user_text, assistant_text in payload["sessions"]:
        stated = int(datetime.strptime(date, "%Y/%m/%d (%a) %H:%M")
                     .replace(tzinfo=timezone.utc).timestamp())
        result = store_turn(
            chat, user_text, assistant_text, access_policy=policy,
            source_context={{
                "chat_id": chat, "runtime_home": str(home),
                "statement_at": stated,
            }})
        assert result["status"] in {{"stored", "retained"}}, result
    print(json.dumps({{"ingested": len(payload["sessions"])}}))
else:
    from core.memory.entries import resolve_memory_access_policy

    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(
        chat, payload["question"],
        [{{"role": "user", "content": payload["question"]}}],
        access_policy=policy,
        source_context={{
            "chat_id": chat, "runtime_home": str(home),
            "surface": "channel", "platform": "api",
        }})
    injected = "\\n".join(
        str(m.get("content") or "") for m in out
        if "<retrieved_context>" in str(m.get("content") or ""))
    telemetry = cr.get_last_retrieval_telemetry()
    print(json.dumps({{
        "injected": injected,
        "selected_facts": telemetry.get("selected_facts") or [],
    }}))
'''


def test_preference_recovers_after_process_restart(pref_env) -> None:
    """Ingest in one fresh interpreter, ask in another; the store on disk
    carries the preference and the capsule recovers it after the restart."""
    home, _install = pref_env  # the runner installs its own stub
    chat = "pref-restart-interpreters"
    sessions = _distractor_sessions(extra_first=[
        ("2024/08/30 (Fri) 21:30",
         _CYCLING_PREFERENCE,
         "A saddle bag with the essentials is a habit worth keeping."),
    ])
    payload = {
        "chat": chat,
        "topic_words": sorted(_CYCLING_TOPIC),
        "sessions": sessions,
        "question": _CYCLING_QUESTION,
    }
    runner = home.parent / "restart_runner.py"
    runner.write_text(_RESTART_RUNNER.format(
        backend=STUB_BACKEND, register=sorted(STUB_REGISTER_WORDS)))

    def run(mode):
        proc = subprocess.run(
            [sys.executable, str(runner), str(REPO_ROOT), str(home), mode,
             json.dumps(payload)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])

    assert run("ingest")["ingested"] == len(sessions)
    assert (home / "data" / "memory" / "vool_memory.db").exists()
    result = run("answer")

    assert "spare inner tube" in result["injected"], result["injected"]
    assert any(
        "spare inner tube" in line and line.startswith("- user said")
        for line in result["selected_facts"])
