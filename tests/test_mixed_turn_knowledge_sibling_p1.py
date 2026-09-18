"""P1 — a mixed turn's stable-knowledge renders publish under the runtime's authoring policy.

THE MEASURED DEFECT (served 2026-09-09, build 07aaaede; FINDINGS F43)
--------------------------------------------------------------------
After the F41 repair, "Answer all three: … In what year did the Berlin Wall fall?" GENERATED
the correct statement — "The Berlin Wall fell in 1989." — and the gate then shipped it inside
"Withheld from this answer: 1 statement that the sources retrieved for this turn do not
support". Same for "The novel 1984 was written by Orwell" beside a live BTC price. The plan
typed those clauses KNOW with OPEN numeric authority; the turn-level publication gate matches
every claim against retrieved notes and had no per-clause channel.

THE AUTHORITY BOUNDARY (the contract under test)
------------------------------------------------
Permission to answer general knowledge without sources is NOT created by this channel. It is
the existing `core.final_answer_authorship` policy — the same one under which a DIRECT
knowledge turn publishes with no grounding lifecycle at all — and the exemption may apply only
when that policy's per-turn record blesses the writer. A planner naming a clause KNOW, or a
model producing plausible text, creates no permission. The exemption binds to the exact turn
(lifecycle identity), the exact demand (the recorded node's render), and the exact claims
(contained text, currency-free, freshness-free). Accounting never presents exempt claims as
retrieved or verified: `exempt_stable_knowledge` is a state of its own and
`supported_claim_count` never counts one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.conductor.evidence import (
    RUNTIME_STABLE_KNOWLEDGE_CHANNEL,
    conductor_stable_knowledge,
    publish_conductor_stable_knowledge,
)
from core.conductor.registry import OperationSpec, register_operation, unregister_operation
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import _support_rows, publication_verdict


def _knowledge_outcome(
    node_id: str = "know-1",
    rendered: str = "The Berlin Wall fell in 1989.",
    authority: str = "open_knowledge",
    succeeded: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        node=SimpleNamespace(
            node_id=node_id,
            operation="factual_explanation",
            request_text="In what year did the Berlin Wall fall?",
            arguments={"numeric_authority": authority, "clause": "In what year did the Berlin Wall fall?"},
        ),
        succeeded=succeeded,
        rendered=rendered if succeeded else "",
        result={"text": rendered},
    )


def _weather_outcome() -> SimpleNamespace:
    return SimpleNamespace(
        node=SimpleNamespace(
            node_id="w1", operation="weather_lookup", request_text="weather in Kaunas"
        ),
        succeeded=True,
        rendered="Kaunas, Lithuania: Patchy rain nearby, 18.0°C",
        result={"temperature_c": 18.0, "label": "Kaunas", "source": "wttr.in"},
    )


# ------------------------------------------------------------------ the stable-knowledge channel


def test_succeeded_open_authority_knowledge_nodes_record_their_rendered_line() -> None:
    rows = conductor_stable_knowledge([_knowledge_outcome()])
    assert [row["summary"] for row in rows] == ["The Berlin Wall fell in 1989."]
    assert rows[0]["ok"] is True
    assert rows[0]["node_id"] == "know-1"


def test_failed_and_non_knowledge_nodes_record_nothing_closed_authority_does() -> None:
    """AMENDED with the channel's own reason: the node's numeric AUTHORITY classifies the
    REQUEST's shape ("1984" in a title makes a clause closed), not the render's safety — a
    closed node's non-numeric render is recorded, because every numeric/current/currency
    protection is enforced per CLAIM at the gate. Failed nodes still publish nothing."""
    failed = _knowledge_outcome(node_id="k-f", succeeded=False)
    closed = _knowledge_outcome(node_id="k-c", authority="closed_evidence")
    rows = conductor_stable_knowledge([failed, closed, _weather_outcome()])
    assert [row["node_id"] for row in rows] == ["k-c"]


def test_publish_is_idempotent_by_node_id() -> None:
    context: dict = {}
    assert publish_conductor_stable_knowledge(context, [_knowledge_outcome()]) != []
    assert publish_conductor_stable_knowledge(context, [_knowledge_outcome()]) == []
    assert len(context[RUNTIME_STABLE_KNOWLEDGE_CHANNEL]) == 1


def test_the_channel_is_not_support_rows() -> None:
    """The false-absolution guard: the model's own render may never certify itself as a
    source. `_support_rows` must not offer the stable-knowledge channel under any origin."""
    lifecycle = GroundingLifecycle(
        lifecycle_id="sk-1",
        identity=TurnIdentity(),
        request_text="mixed turn",
        retrieval_outcome="partial",
        model_authored=True,
        stable_knowledge=({"summary": "The Berlin Wall fell in 1989."},),
    )
    rows, origin = _support_rows(lifecycle)
    assert rows == [] and origin == "", (rows, origin)


def test_a_second_know_family_server_is_refused_at_registration() -> None:
    """Two operations declaring the plan's KNOW-family server is a configuration ambiguity
    the registry contract refuses to resolve by order — first-wins is not fail-closed."""
    spec = OperationSpec(
        name="know-family-imposter",
        description="test imposter",
        expand_arguments=lambda text: [],
        run=lambda node, ctx: {},
        render=lambda node, result: "",
        serves_named_know_clauses=True,
    )
    try:
        with pytest.raises(ValueError, match="singleton role"):
            register_operation(spec)
    finally:
        unregister_operation(spec.name)


# ------------------------------------------------- the publication gate's per-claim exemption


def _gated_lifecycle(**overrides) -> GroundingLifecycle:
    base = dict(
        lifecycle_id="mixed-turn-sk-1",
        identity=TurnIdentity(),
        request_text=(
            "What is the current Bitcoin price in USD and who wrote the novel 1984?"
        ),
        retrieval_outcome="partial",
        model_authored=True,
        typed_observations=(
            {
                "summary": "Bitcoin: 78503.0 USD, +0.15% 24h (source: CoinGecko)",
                "origin_domain": "coingecko.com",
                "ok": True,
            },
        ),
    )
    base.update(overrides)
    return GroundingLifecycle(**base)


BTC_LINE = "Bitcoin: 78503.0 USD, +0.15% 24h (source: CoinGecko)"
KNOW_LINE = "The novel 1984 was written by George Orwell."
MIXED_CONTENT = f"{BTC_LINE}\n{KNOW_LINE}"

#: The entry shape the conductor's publisher stamps when the authorship policy's verdict for
#: the node's own generation call was ELIGIBLE (joined by the clause the briefing embeds).
_AUTHORIZED_ENTRY = {
    "summary": KNOW_LINE,
    "node_id": "know-1",
    "author_eligible": True,
    "author": "ollama-local:qwen3:8b",
}


@pytest.fixture()
def eligible_authorship() -> dict[str, Any]:
    return _AUTHORIZED_ENTRY


def _no_authorship_entry() -> dict[str, Any]:
    """The publisher's fail-closed shape: no verdict joined, nothing stamped."""
    return {"summary": KNOW_LINE, "node_id": "know-1"}


def _ineligible_authorship_entry() -> dict[str, Any]:
    return {
        "summary": KNOW_LINE,
        "node_id": "know-1",
        "author_eligible": False,
        "author": "ollama-local:qwen2.5:7b",
    }


def test_the_knowledge_sibling_publishes_under_the_authoring_policy(
    eligible_authorship: dict[str, Any],
) -> None:
    lifecycle = _gated_lifecycle(stable_knowledge=(eligible_authorship,))
    verdict = publication_verdict(lifecycle, MIXED_CONTENT)
    assert verdict.state == "published", (verdict.state, verdict.content)
    assert KNOW_LINE in verdict.content and BTC_LINE in verdict.content
    assert "Withheld from this answer" not in verdict.content
    # ACCOUNTING: answered under the authoring policy, never factually verified.
    assert verdict.supported_claim_count == 1  # the BTC line only
    assert verdict.exempted_claims == (KNOW_LINE,)
    exempt_row = next(
        row
        for row in verdict.claim_support.get("claims", [])
        if "Orwell" in str(row.get("text") or "")
    )
    assert exempt_row["status"] == "exempt_stable_knowledge"
    assert exempt_row["supporting_sources"] == ["authoring_policy:final_answer"]
    assert any("know-1" in reason for reason in exempt_row["reasons"])


def test_no_authorship_verdict_means_no_exemption() -> None:
    """The permission is the policy's to give. An entry with no joined verdict (the
    publisher's fail-closed shape) exempts nothing — the planner's KNOW naming and the
    plausible text create nothing on their own."""
    lifecycle = _gated_lifecycle(stable_knowledge=(_no_authorship_entry(),))
    verdict = publication_verdict(lifecycle, MIXED_CONTENT)
    assert verdict.state != "published"
    assert verdict.exempted_claims == ()
    notice_at = verdict.content.find("Withheld from this answer:")
    assert KNOW_LINE in verdict.content[notice_at:]


def test_an_ineligible_author_means_no_exemption() -> None:
    """An uncertified author's bytes are the authorship gate's subject; the grounding gate
    must not launder them through a knowledge exemption."""
    lifecycle = _gated_lifecycle(stable_knowledge=(_ineligible_authorship_entry(),))
    verdict = publication_verdict(lifecycle, MIXED_CONTENT)
    assert verdict.state != "published"
    assert verdict.exempted_claims == ()


def test_an_unsupported_sibling_is_not_authorized_by_an_exempt_neighbour(
    eligible_authorship: dict[str, Any],
) -> None:
    """Containment is the binding: only claims INSIDE the recorded render gain anything.
    A plausible sentence the plan never rendered stays withheld beside an exempt line."""
    sibling = "George Orwell also wrote Animal Farm in 1945."
    content = f"{BTC_LINE}\n{KNOW_LINE}\n{sibling}"
    lifecycle = _gated_lifecycle(stable_knowledge=(eligible_authorship,))
    verdict = publication_verdict(lifecycle, content)
    assert verdict.state == "partial", verdict.state
    assert KNOW_LINE in verdict.content.split("Withheld")[0]
    notice_at = verdict.content.find("Withheld from this answer:")
    assert sibling in verdict.content[notice_at:]


def test_a_current_bound_claim_inside_a_knowledge_line_is_never_exempt(
    eligible_authorship: dict[str, Any],
) -> None:
    current_line = "The current population of Springfield is approximately 50000."
    content = f"{BTC_LINE}\n{current_line}"
    lifecycle = _gated_lifecycle(
        stable_knowledge=({"summary": current_line, "node_id": "know-2",
                           "author_eligible": True, "author": "ollama-local:qwen3:8b"},)
    )
    verdict = publication_verdict(lifecycle, content)
    assert verdict.state != "published", verdict.content
    notice_at = verdict.content.find("Withheld from this answer:")
    assert "population" in verdict.content[notice_at:]


def test_a_currency_claim_inside_a_knowledge_line_is_never_exempt(
    eligible_authorship: dict[str, Any],
) -> None:
    price_line = "The novel 1984 was written by George Orwell. A first edition costs 5000.0 USD."
    content = f"{BTC_LINE}\n{price_line}"
    lifecycle = _gated_lifecycle(
        stable_knowledge=({"summary": price_line, "node_id": "know-3",
                           "author_eligible": True, "author": "ollama-local:qwen3:8b"},)
    )
    verdict = publication_verdict(lifecycle, content)
    assert verdict.state != "published", verdict.content


def test_a_gated_turn_with_no_channels_still_refuses(
    eligible_authorship: dict[str, Any],
) -> None:
    content = KNOW_LINE
    verdict = publication_verdict(_gated_lifecycle(), content)
    assert verdict.state == "refused", verdict.content
    # The typed refusal QUOTES the request (which names the novel 1984); what must not appear
    # is the unsupported ANSWER's own text.
    assert "Orwell" not in verdict.content


def test_the_publisher_joins_the_authorship_verdict_by_clause_and_fails_closed() -> None:
    """The entry's authority is stamped only when an ELIGIBLE generation call's prompt
    carries this node's demand text. A verdict for a DIFFERENT clause, or an ineligible
    author, leaves the entry unmarked — and an unmarked entry can never exempt."""
    context: dict = {
        "conductor_generation_authorship": [
            {
                "prompt": "Briefing for the plan. The part of it you are answering: "
                          "In what year did the Berlin Wall fall?",
                "provider_id": "ollama-local:qwen3:8b",
                "eligible": True,
                "reason": "certified_local",
            },
            {
                "prompt": "Briefing for the plan. The part of it you are answering: "
                          "who wrote the novel 1984?",
                "provider_id": "ollama-local:qwen2.5:7b",
                "eligible": False,
                "reason": "uncertified_author",
            },
        ]
    }
    stamped = publish_conductor_stable_knowledge(context, [_knowledge_outcome(node_id="know-b")])
    assert stamped and stamped[0].get("author_eligible") is True
    assert stamped[0].get("author") == "ollama-local:qwen3:8b"

    context2: dict = {
        "conductor_generation_authorship": [
            {
                "prompt": "Briefing for a different clause entirely.",
                "provider_id": "ollama-local:qwen3:8b",
                "eligible": True,
                "reason": "certified_local",
            }
        ]
    }
    unstamped = publish_conductor_stable_knowledge(context2, [_knowledge_outcome(node_id="know-c")])
    assert unstamped and "author_eligible" not in unstamped[0]
