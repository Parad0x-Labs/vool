"""An ask about a file sent with the chat, with nothing attached, is not the clean loop's.

CI on the round-8 head (bisected to 9c69f91b, the clean loop default ON):
tests/test_media_product_served.py::test_the_same_question_with_no_attachment_still_says_what_the_disk_lane_says.
"What is on screen on page 3 of the attached site-survey-vk73.pdf?" with no attachment was admitted
by the loop, whose brain answered from the model. The loop sees only the words and the owner's disk,
never attachments; with the loop off the disk-PDF lane answers plainly that the file is nowhere on
this computer. The route now leaves such an ask to the lanes that own it, and keeps plain files asks.
"""

from __future__ import annotations

import pytest

from core.clean_loop import route

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}


def _brain():
    return object()


@pytest.mark.parametrize(
    "ask",
    [
        "What is on screen on page 3 of the attached site-survey-vk73.pdf?",
        "what does the attached invoice say?",
        "summarise this attachment",
        "read the file I uploaded",
        "I've attached my cv, what's on page 2?",
        "check the attached pdf for the total",
    ],
)
def test_an_unbound_attachment_ask_is_left_to_the_attachment_and_disk_lanes(ask):
    asked_for_brain: list[bool] = []
    decision = route.decide(
        ask, source_context=OWNER, resolve_brain=lambda: asked_for_brain.append(True), env=ON
    )
    assert (decision.admit, decision.reason) == (False, "attachment_ask_nothing_bound"), ask
    assert not asked_for_brain


def test_a_bound_attachment_keeps_its_own_reason():
    bound = {**OWNER, "attachment_turn_id": "req:abc", "external_evidence": [{"kind": "text", "attachment_id": "a1"}]}
    decision = route.decide(
        "what does the attached invoice say?", source_context=bound, resolve_brain=_brain, env=ON
    )
    assert (decision.admit, decision.reason) == (False, "attachment_turn")


@pytest.mark.parametrize(
    "ask",
    [
        "whats on my desktop?",
        "which pdf in my downloads was attached to the plumber's message?",
        "find the report in my documents that has the floor plan attached",
        "which pdfs are in my downloads?",
    ],
)
def test_plain_files_asks_stay_with_the_loop(ask):
    decision = route.decide(ask, source_context=OWNER, resolve_brain=_brain, env=ON)
    assert decision.admit and decision.reason == "admitted", (ask, decision.reason)


# --- the answer the turn gets instead: the disk lane's own words, intact ---------------------------


class _RecordingAgent:
    def __init__(self) -> None:
        self.claims: list[dict] = []

    def _fast_path_result(self, *, user_input, response, confidence, source_context, reason, **_kw):
        self.claims.append({"response": response, "reason": reason})
        return {"response": response, "fast_path": reason}


def test_the_disk_lane_says_nothing_is_attached_and_names_the_file(tmp_path, monkeypatch):
    from core.agent_runtime import fast_paths_pdf

    monkeypatch.setenv("HOME", str(tmp_path))
    agent = _RecordingAgent()
    result = fast_paths_pdf.maybe_handle_pdf_read(
        agent,
        "What is on screen on page 3 of the attached site-survey-vk73.pdf?",
        session_id="unbound-attachment",
        source_context={"runtime_session_id": "unbound-attachment"},
    )
    assert result is not None
    reply = agent.claims[-1]["response"]
    assert reply.startswith("Nothing is attached to this message")
    assert "a file called site-survey-vk73.pdf anywhere" in reply
    assert "screen on page" not in reply


def test_a_file_called_x_is_not_a_denial_of_calling_x():
    """The disk lane's true "I could not find a file called X" was rewritten to "I cannot verify from
    runtime evidence whether a read of X occurred" (served, round-8 head, clean loop on and off)."""
    from core.agent_runtime.evidence_claim_binder import bind_response_to_evidence
    from core.runtime_evidence import collect_turn_evidence

    evidence = collect_turn_evidence(
        session_id="naming-called",
        source_context={"runtime_session_id": "naming-called", "cancel_turn_id": "t-naming"},
        remote_account_complete=False,
    )
    for sentence in (
        "I could not find a file called site-survey-vk73.pdf anywhere in ~/Downloads.",
        "There is nothing called budget.pdf in your Documents.",
    ):
        outcome = bind_response_to_evidence(sentence, evidence)
        assert not outcome.applied and outcome.response == sentence, sentence
    # A real denial of calling is still checked.
    denial = bind_response_to_evidence("I did not call example.com.", evidence)
    assert denial.applied and "cannot verify" in denial.response.lower()
