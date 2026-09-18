"""Ingress must bind a namespace before request transcript can become authoritative.

The bug, measured through the running daemon on 2026-08-06 (build 6707acbc)
----------------------------------------------------------------------------
A user asked an EV research question, got a grounding failure, and asked "why?". The reply was
about the x402 spend lane, private keys and OS consent prompts -- material from project bootstrap
that had nothing to do with the question. A later "try again?" answered "I don't see a previous
action that failed".

The model was then asked what happened and explained that failed turns are dropped from history.
That explanation was wrong, and believing it would have sent the fix to the wrong layer. What
actually happened:

    resolve_semantic_access_policy(...)  ->  ValueError("chat namespace does not exist")
    canonical_runtime_transcript         ->  except (TypeError, ValueError): return [], "none"

Every `/api/chat` session whose id the daemon minted but never namespaced took that branch, and it
discarded the ENTIRE transcript -- including `conversation_history` that arrived in the request body
and needs no namespace at all, being the caller's own message array. Sessions
`openclaw:c6483bd21c2607286e98` and `openclaw:71d7fd7fc002d18f305c` both assembled 0 messages from a
request that carried the prior question AND the assistant's failure reply. With no dialogue left in
the prompt, bootstrap was the only material present, so the model answered from it.

Both reported symptoms follow from that one line: the follow-up had nothing to refer back to, and
the retry had no recorded failure to find.

The original repair admitted request transcript whenever namespace lookup failed. That restored
continuity but turned absence of authority into authority. The final contract keeps the useful
behavior at real ingress, which creates a canonical namespace first, while direct unregistered
reads fail closed.
"""

from __future__ import annotations

import pytest

from core.bootstrap_context import canonical_runtime_transcript
from core.context_scope import ContextAccessPolicy

EV_QUESTION = (
    "What is the best-selling battery-electric car model of all time worldwide? Compare the "
    "Tesla Model 3 and Nissan Leaf by cumulative global deliveries. Do not guess."
)
GROUNDING_FAILURE = "I checked, but I couldn't ground a confident answer from the evidence I found."

# Ids the daemon actually minted and never namespaced. Any unregistered id reproduces this; these
# two are the measured ones.
NAMESPACELESS_SESSION = "openclaw:c6483bd21c2607286e98"


def _context_with_history() -> dict[str, object]:
    return {
        "surface": "channel",
        "platform": "openclaw",
        "conversation_history": [
            {"role": "user", "content": EV_QUESTION},
            {"role": "assistant", "content": GROUNDING_FAILURE},
        ],
    }


def _bind_ingress_session(session_id: str) -> None:
    policy = ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context={"surface": "channel", "platform": "openclaw"},
    )
    assert policy.chat_id == session_id
    assert policy.namespace_state == "active"


def test_the_namespace_is_genuinely_absent() -> None:
    """Guards the premise. If this id ever gains a namespace the test below proves nothing."""
    from core.context_namespace import load_chat_namespace

    assert load_chat_namespace(NAMESPACELESS_SESSION) is None


def test_unregistered_request_history_fails_closed() -> None:
    transcript, source = canonical_runtime_transcript(
        session_id=NAMESPACELESS_SESSION,
        source_context=_context_with_history(),
        current_user_text="why?",
    )
    assert transcript == []
    assert source == "scope_denied"


def test_the_previous_exchange_survives_after_ingress_binds_namespace() -> None:
    session_id = "openclaw:ingress-bound-history"
    _bind_ingress_session(session_id)
    transcript, source = canonical_runtime_transcript(
        session_id=session_id,
        source_context={**_context_with_history(), "chat_id": session_id},
        current_user_text="why?",
    )
    assert transcript
    assert source == "client_conversation_history"

    roles = [message["role"] for message in transcript]
    assert "user" in roles and "assistant" in roles

    joined = " ".join(message["content"] for message in transcript)
    assert "Nissan Leaf" in joined, "the user's own question was dropped"
    assert "couldn't ground" in joined, (
        "the assistant's failure reply was dropped -- 'why?' and 'try again?' both need it to "
        "resolve, and its absence is why the retry reported no failed action"
    )


def test_the_current_turn_is_not_duplicated_into_the_transcript() -> None:
    """The follow-up itself must not be appended twice; it is passed separately as the live turn."""
    context = _context_with_history()
    context["conversation_history"] = [
        *context["conversation_history"],  # type: ignore[misc]
        {"role": "user", "content": "why?"},
    ]
    session_id = "openclaw:ingress-bound-current-turn"
    _bind_ingress_session(session_id)
    transcript, _ = canonical_runtime_transcript(
        session_id=session_id,
        source_context={**context, "chat_id": session_id},
        current_user_text="why?",
    )
    assert sum(1 for m in transcript if m["content"].strip() == "why?") == 0


def test_no_history_supplied_still_yields_nothing() -> None:
    """An unregistered empty request is a scope denial, not an empty authorized transcript."""
    transcript, source = canonical_runtime_transcript(
        session_id=NAMESPACELESS_SESSION,
        source_context={"surface": "channel", "platform": "openclaw"},
        current_user_text="why?",
    )
    assert transcript == []
    assert source == "scope_denied"


@pytest.mark.parametrize(
    "follow_up",
    ["why?", "try again?", "explain", "continue", "what happened?", "prove it"],
)
def test_every_short_follow_up_keeps_its_antecedent(follow_up: str) -> None:
    """Short follow-ups are precisely the turns with no content of their own to route on.

    Parametrised rather than written around "why?" alone: the defect belongs to the whole class of
    turns that carry no subject and depend entirely on the preceding exchange.
    """
    session_id = f"openclaw:ingress-bound:{follow_up}"
    _bind_ingress_session(session_id)
    transcript, _ = canonical_runtime_transcript(
        session_id=session_id,
        source_context={**_context_with_history(), "chat_id": session_id},
        current_user_text=follow_up,
    )
    joined = " ".join(message["content"] for message in transcript)
    assert "Nissan Leaf" in joined and "couldn't ground" in joined, (
        f"{follow_up!r} lost its antecedent, so it can only resolve against bootstrap context"
    )
