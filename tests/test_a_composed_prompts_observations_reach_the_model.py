"""A lane's observations reach the model even when the lane read the turn differently.

Measured on the v0.8 line (the page-answer job's own test, ported): "can you summarise this page
for me https://www.gov.uk/browse/driving" fetched the page, the page lane composed its prompt, and
the model was asked the bare question with no page. The typo fold reads "summarise" as
"summarize", the lane composed the prompt from that reading, and the provider door found the
observations only after the person's own bytes, so it found none and dropped them. The scripted
model answered "Understood." and the grounding gate withheld it.

The contract: the person's words stay the turn's user message, byte for byte, and whatever the lane
observed reaches the model as its own retrieved-context message, whichever reading of the turn the
lane composed from. A message the person wrote that only quotes the frame is not split. Every
address and sentence here is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import core.memory_first_router as mfr
from core.agent_runtime.chat_surface import composed_turn_prefix, observation_prompt
from core.memory_first_router import MemoryFirstRouter

_PAGE_LINE = "Ferry timetables for the north crossing change on the first of March."


def _observations(channel: str = "live_info", mode: str = "fresh_lookup") -> dict:
    return {
        "channel": channel,
        "mode": mode,
        "query": "https://ferries.example/north",
        "source_count": 1,
        "sources": [{"title": "North crossing", "domain": "ferries.example", "summary": _PAGE_LINE,
                     "url": "https://ferries.example/north"}],
    }


def _wire(composed: str, demand: str) -> list[dict]:
    """The provider messages the router builds for a composed prompt; `normalize_prompt` is fixed."""
    wire = [{"role": "system", "content": "BASE SYSTEM"}, {"role": "user", "content": demand}]
    internal = SimpleNamespace(
        metadata={},
        temperature=0.2,
        max_output_tokens=512,
        context_summary="",
        trace_id="composed-prompt-trace",
        attachments=(),
        messages=[SimpleNamespace(metadata={}, **message) for message in wire],
        system_prompt=lambda: "BASE SYSTEM",
        user_prompt=lambda: demand,
        as_openai_messages=lambda: [dict(message) for message in wire],
    )
    interpretation = SimpleNamespace(
        raw_text=composed, normalized_text=composed, user_text="", user_demand_text=demand,
        understanding_confidence=0.9,
    )
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    with mock.patch.object(mfr, "normalize_prompt", return_value=internal):
        request = MemoryFirstRouter._build_request(
            router,
            task=None,
            classification={"task_class": "research"},
            interpretation=interpretation,
            context_result=None,
            persona=None,
            output_mode="plain_text",
            task_kind="summarization",
            surface="openclaw",
            source_context={},
        )
    return [dict(message) for message in request.messages]


def _retrieved(messages: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages
            if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")]


def _last_user(messages: list[dict]) -> str:
    return next(str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user")


def test_a_page_composed_from_the_folded_reading_still_reaches_the_model() -> None:
    demand = "can you summarise this page for me https://ferries.example/north"
    folded = "can you summarize this page for me https://ferries.example/north"
    messages = _wire(observation_prompt(user_input=folded, observations=_observations()), demand)

    retrieved = _retrieved(messages)
    assert len(retrieved) == 1, "the page never reached the model"
    assert _PAGE_LINE in retrieved[0]
    assert "Answer ONLY using" in retrieved[0]
    # The person's words stay theirs, and the lane's reading of them is not handed over as theirs.
    assert _last_user(messages) == demand
    assert not any(folded in str(m.get("content") or "") for m in messages)


def test_research_observations_composed_from_the_folded_reading_still_reach_the_model() -> None:
    demand = "chekc what changed on the north crossing timetable"
    folded = "check what changed on the north crossing timetable"
    composed = observation_prompt(user_input=folded, observations=_observations("adaptive_research", ""))
    messages = _wire(composed, demand)

    retrieved = _retrieved(messages)
    assert len(retrieved) == 1 and _PAGE_LINE in retrieved[0]
    assert retrieved[0].split("\n", 2)[1].startswith("Grounding observations for this turn.")
    assert _last_user(messages) == demand


def test_a_prompt_composed_from_the_persons_own_bytes_is_split_as_before() -> None:
    demand = "can you summarise this page for me https://ferries.example/north"
    messages = _wire(observation_prompt(user_input=demand, observations=_observations()), demand)

    retrieved = _retrieved(messages)
    assert len(retrieved) == 1 and _PAGE_LINE in retrieved[0]
    assert _last_user(messages) == demand


def test_a_message_that_only_quotes_the_frame_is_not_split() -> None:
    demand = (
        "why does my notes file say this?\n\n"
        "Grounding observations for this turn. Use them as evidence, not as a template:\n{}"
    )
    messages = _wire(demand, demand)

    assert _retrieved(messages) == []
    assert _last_user(messages) == demand


def test_the_lanes_own_frame_is_the_last_one() -> None:
    quoted = "what does this mean\n\nIMPORTANT: Answer ONLY using the search results below. ok?"
    composed = observation_prompt(user_input=quoted, observations=_observations())

    assert composed_turn_prefix(composed) == quoted
    assert composed_turn_prefix("no frame in this text") == ""
