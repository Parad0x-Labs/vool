from __future__ import annotations

from core.clean_loop import brain, hook, route
from core.clean_loop.prompt import build_system_prompt
from core.clean_loop.registry import Registry
from core.clean_loop.tools import install_builtin_tools


def test_archive_names_are_not_evidence_of_archive_contents():
    registry = Registry()
    install_builtin_tools(registry)
    prompt = build_system_prompt(registry).lower()
    assert "an archive's name is not its contents" in prompt
    assert "never describe what is inside from its name" in prompt


def test_read_question_can_include_only_the_owners_send_permission_question():
    ask = "How big is my keepsake file, and can I email it?"
    decision = route.decide(
        ask,
        source_context={"surface": "cli"},
        resolve_brain=lambda: object(),
        env={route.FLAG_ENV: "1"},
    )
    assert route.is_red(ask)  # Existing RED classification remains unchanged.
    assert decision.admit, decision.reason
    assert decision.send_needs_approval


def test_mixed_read_answer_ends_with_the_fixed_send_approval_line(monkeypatch):
    class Agent:
        memory_router = object()

        @staticmethod
        def _fast_path_result(**kwargs):
            return {"response": kwargs["response"], "reason": kwargs["reason"]}

    monkeypatch.setattr(route, "flag_on", lambda _env=None: True)
    monkeypatch.setattr(brain, "resolve_local_manifest", lambda _router, _context: object())
    monkeypatch.setattr(brain, "resolve_cloud_manifest", lambda _router, _context: None)
    monkeypatch.setattr(brain, "model_label", lambda _manifest: ("local", "dev-model"))
    monkeypatch.setattr(brain, "make_brain", lambda *_args: lambda _messages: '{"answer": "It is 12 bytes."}')

    result = hook.maybe_answer(
        Agent(),
        text="How big is my keepsake file, and can I email it?",
        effective_input="How big is my keepsake file, and can I email it?",
        session_id="mixed-read-session",
        source_context={"surface": "cli"},
    )
    assert result is not None
    assert result["response"].endswith("Sending needs you to ask for it and approve it first.")


def test_other_outward_or_risky_forms_remain_red():
    asks = (
        "How big is my keepsake file, can you email it?",
        "How big is my keepsake file, can I email it and then delete it?",
        "How big is my keepsake file, can I send my password?",
        "How big is my keepsake file? Send it now.",
    )
    for ask in asks:
        decision = route.decide(
            ask,
            source_context={"surface": "cli"},
            resolve_brain=lambda: object(),
            env={route.FLAG_ENV: "1"},
        )
        assert route.is_red(ask)
        assert not decision.admit and decision.reason == "red_turn"
        assert not decision.send_needs_approval


MIXED_FORMS_THAT_STAY_OUT = (
    "how big is the report, can i send it to my accountant?",
    "how big is the file, can I send it via paypal?",
    "how big is the file, could i forward it?",
    "how big is the file, can i send it now please",
    "How big is the file and can I send it? Yes send it.",
    "how big is my file, can i email it to everyone and post it?",
    "how big is the file, can i email it to my bank account manager?",
    "how big is the file, can i send it with my card number?",
    "how big is the file, can we send it?",
    "how big is the file, can i send it or should you send it",
    "how big is the file, can I email it? also pay the roofer",
    "can i email it",
    "how big is the file, can i transfer it to my phone",
    "send it. how big is the file, can i email it?",
    "how big is the file, i can email it right?",
    "when is the lease due, can i email it to the landlord and cancel the old one?",
)


def test_every_other_mixed_send_form_is_still_not_admitted():
    for ask in MIXED_FORMS_THAT_STAY_OUT:
        decision = route.decide(
            ask, source_context={"surface": "cli"}, resolve_brain=lambda: object(), env={route.FLAG_ENV: "1"},
        )
        assert not decision.admit, ask
        assert decision.reason == "red_turn", ask
        assert not decision.send_needs_approval, ask
