"""A follow-up keeps the previous clean-loop turn's evidence (round-5 fix C4), and only there.

The real hook, route, tools and home folder; the model is a scripted brain that answers from what
it is shown, so the test sees exactly which evidence reached it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.clean_loop import brain, hook, route

OWNER = {"surface": "cli"}


class _Agent:
    memory_router = object()

    @staticmethod
    def _fast_path_result(**kwargs):
        return {"response": kwargs["response"], "reason": kwargs["reason"]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    for name in ("Desktop", "Documents", "Downloads"):
        (home / name).mkdir(parents=True)
    (home / "Documents" / "kennel_plan.txt").write_text(
        "Kennel plan for the dog\nBilled yearly: 600.00\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(route.FLAG_ENV, "1")
    shown: list[str] = []

    def scripted(_router, _manifest, _context):
        def reply(messages):
            seen = "\n".join(m["content"] for m in messages)
            shown.append(seen)
            last = messages[-1]["content"]
            if "a month" in messages[1]["content"] if len(messages) > 1 else False:
                if "600.00" in seen:
                    return '{"answer": "That is 50.00 a month (600.00 a year)."}'
                return '{"answer": "I would need to look that up again."}'
            if "[tool result]" not in last:
                return '{"tool": "read_file", "path": "Documents/kennel_plan.txt"}'
            return '{"answer": "Yes, the kennel plan is billed once a year."}'

        return reply

    monkeypatch.setattr(brain, "resolve_local_manifest", lambda _router, _context: object())
    monkeypatch.setattr(brain, "resolve_cloud_manifest", lambda _router, _context: None)
    monkeypatch.setattr(brain, "model_label", lambda _manifest: ("local", "dev-model"))
    monkeypatch.setattr(brain, "make_brain", scripted)
    for session in ("follow-a", "follow-b"):
        hook._clear_evidence(session)
    return home, shown


def _ask(text: str, session: str, context: dict | None = None):
    return hook.maybe_answer(
        _Agent(), text=text, effective_input=text, session_id=session,
        source_context=dict(context if context is not None else OWNER),
    )


def test_a_follow_up_is_answered_from_the_first_turns_evidence(setup):
    _home, shown = setup
    first = _ask("is my kennel plan billed yearly?", "follow-a")
    assert first is not None and "once a year" in first["response"]
    second = _ask("so how much is that a month?", "follow-a")
    assert second is not None
    assert second["response"] == "That is 50.00 a month (600.00 a year)."
    # The amount reached the model only as turn 1's evidence: turn 1's answer never said it.
    assert "600.00" not in first["response"]
    assert "[Evidence from your previous clean-loop turn" in shown[-1]


def test_another_session_gets_none_of_it(setup):
    _home, shown = setup
    assert _ask("is my kennel plan billed yearly?", "follow-a") is not None
    route.note_clean_turn("follow-b")  # a follow-up there too, with nothing of its own read yet
    other = _ask("so how much is that a month?", "follow-b")
    assert other is not None and "600.00" not in other["response"]
    assert "600.00" not in shown[-1]


def test_a_turn_that_is_not_the_owners_own_gets_none_of_it(setup):
    _home, shown = setup
    assert _ask("is my kennel plan billed yearly?", "follow-a") is not None
    calls_before = len(shown)
    remote = _ask("so how much is that a month?", "follow-a", {"surface": "channel", "_owner_local": False})
    assert remote is None and len(shown) == calls_before
    assert hook._prior_evidence("follow-a", "so how much is that a month?", owner_local=True) == ""


def test_the_evidence_is_bounded(setup):
    long_seen = ["x" * 5000, "Documents/kennel_plan.txt\nBilled yearly: 600.00"]
    evidence = hook._turn_evidence(long_seen)
    assert len(evidence) <= 1500 and evidence.startswith("Documents/kennel_plan.txt")


def test_a_question_only_memory_can_answer_is_not_taken_by_the_loop(setup, tmp_path, monkeypatch):
    """A fact told to VOOL in chat lives in memory, which the loop cannot read: with no document
    holding its words, the question keeps going to the normal pipeline (and its memory lane)."""
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import add_memory_fact

    _home, shown = setup
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool-data"))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    ensure_chat_namespace("mem-1")
    assert add_memory_fact("Aunt Persimma keeps the spare gate fob for Ravelstow cottage.", session_id="mem-1")
    context = dict(OWNER)
    answered = hook.maybe_answer(
        _Agent(), text="Who keeps the spare gate fob for Ravelstow cottage?", effective_input="x",
        session_id="mem-1", source_context=context,
    )
    assert answered is None
    assert context["clean_local_loop_route"] == "not_about_own_files"
    assert shown == []


def test_a_document_admitted_turn_that_finds_nothing_goes_back_to_the_pipeline(setup, monkeypatch):
    """The other side: a document names the same place, so the turn is admitted by document words.
    The loop searches, does not find the fob, and the turn falls through to the normal pipeline
    (whose memory lane holds the fact) instead of answering "I didn't find it"."""
    home, _shown = setup
    (home / "Documents" / "places.txt").write_text("The cottage at Ravelstow has a blue door.\n", encoding="utf-8")
    replies = iter([
        '{"tool": "find", "query": "gate fob"}',
        '{"answer": "I didn\'t find it in your files."}',
    ])
    monkeypatch.setattr(brain, "make_brain", lambda *_args: lambda _messages: next(replies))
    context = dict(OWNER)
    answered = hook.maybe_answer(
        _Agent(), text="Who keeps the spare gate fob for Ravelstow cottage?", effective_input="x",
        session_id="mem-2", source_context=context,
    )
    assert answered is None
    assert context["clean_local_loop_route"] == "documents_found_nothing_fell_through"


def test_a_document_admitted_turn_that_finds_the_answer_is_answered(setup, monkeypatch):
    home, _shown = setup
    (home / "Documents" / "places.txt").write_text(
        "The cottage at Ravelstow has a blue door; the spare gate fob hangs by the boiler.\n", encoding="utf-8"
    )
    replies = iter([
        '{"tool": "find", "query": "gate fob"}',
        '{"answer": "The spare gate fob hangs by the boiler (Documents/places.txt)."}',
    ])
    monkeypatch.setattr(brain, "make_brain", lambda *_args: lambda _messages: next(replies))
    context = dict(OWNER)
    answered = hook.maybe_answer(
        _Agent(), text="Who keeps the spare gate fob for Ravelstow cottage?", effective_input="x",
        session_id="mem-3", source_context=context,
    )
    assert answered is not None and "boiler" in answered["response"]
