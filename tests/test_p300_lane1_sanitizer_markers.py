"""Lane 1 / Case A: orchestration-marker recognition vs ordinary word substrings.

The measured failure (2026-09-30 LongMemEval case q55854fc024eaae7a): the marker `graph`
was matched as a plain substring, so `autographed` convicted every substantive sentence of
a correct abstention and only the final sentence shipped. The repair splits leak markers
into compound markers (snake_case machinery vocabulary -- substring semantics unchanged)
and AMBIGUOUS markers (`graph`), which convict only in structural positions (a mapping key
or a quoted structural token) because that is what real orchestration material looks like;
bare prose words and substrings inside ordinary words are not leaks.

The regression rows restate the captured case's mechanism with the captured text
(development/reproduction evidence). The fresh rows were frozen -- inputs and expected
outcomes hashed in the mission freeze file -- before their first execution, and use
different domains (photography, graph theory, biography, lint tooling) than the captured
case (autographed sports memorabilia).
"""
from __future__ import annotations

from apps.vool_agent import ChatTurnResult, VoolAgent, ResponseClass
from core.agent_runtime.response import (
    _ambiguous_leak_marker_hits,
    _orchestration_fragment_marker_pattern,
    _strip_orchestration_fragments,
    humanize_orchestration_leak,
)


def _agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="lane1-case-a", persona_id="default")


def _deliver(agent: VoolAgent, text: str) -> str:
    return agent._decorate_chat_response(
        ChatTurnResult(text=text, response_class=ResponseClass.GENERIC_CONVERSATION),
        session_id="lane1:case-a",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        include_hive_footer=False,
    )


# --------------------------------------------------------------------- regression


def test_captured_abstention_survives_intact() -> None:
    # The captured raw reader reply (correct abstention): every substantive clause must
    # reach the user, not only the trailing sentence.
    reply = (
        "I've got no record of any autographed footballs in your collection. Your notes only "
        "mention autographed baseballs — 15 added in your first three months, later up to 20. "
        "If you've started collecting footballs, let me know and I'll track them."
    )
    assert _deliver(_agent(), reply) == reply


def test_ambiguous_marker_word_substring_does_not_trigger() -> None:
    # The recognition seam itself: `autographed`, `photographed`, `biography` carry the
    # substring; none of them is a structural `graph` key.
    for prose in (
        "I've got no record of any autographed footballs.",
        "The photographed bridges are catalogued.",
        "The biography section mentions nobody called Tesla.",
    ):
        assert _ambiguous_leak_marker_hits(prose) is False


def test_structural_graph_shapes_still_trigger() -> None:
    for structured in (
        '{"graph": {"nodes": 3}}',
        "{'graph': {'nodes': 3}}",
        "{graph: 3}",
        "{'a': 1, 'graph': {'b': 2}}",
    ):
        assert _ambiguous_leak_marker_hits(structured) is True


def test_compound_markers_keep_substring_semantics() -> None:
    # Unchanged for the unambiguous class: any occurrence of the snake_case compound is
    # orchestration vocabulary, whatever surrounds it.
    pattern = _orchestration_fragment_marker_pattern()
    assert pattern.search("plan notes: routing_requirements applied") is not None
    assert pattern.search("capacity_state ready") is not None
    assert pattern.search("merge_strategy winner") is not None


# ---------------------------------------------------------------------- fresh rows
# Frozen as A-F1..A-F5 in FRESH-CASES-FREEZE.json before first execution.


def test_fresh_photography_abstention_survives_intact() -> None:
    reply = (
        "I have no record of any photographed lighthouses in your album. Your entries only "
        "cover photographed railway bridges — 9 added in your first spring, later up to 14. "
        "If you've started photographing lighthouses, tell me and I'll keep count."
    )
    assert _deliver(_agent(), reply) == reply


def test_fresh_graph_theory_discussion_survives_intact() -> None:
    # Word-boundary `graph` as a bare prose word is ordinary content, not a leak.
    reply = (
        "You've been working through graph theory since October. Your notes mention Euler "
        "paths twice and bipartite matching once. Nothing in them touches knot theory yet."
    )
    assert _deliver(_agent(), reply) == reply


def test_fresh_biography_abstention_survives_intact() -> None:
    reply = (
        "Your reading log shows no biography of Tesla. It lists a biography of Curie and one "
        "of Lovelace, both finished in February. Tell me if you add the Tesla one and I'll "
        "track it."
    )
    assert _deliver(_agent(), reply) == reply


def test_fresh_envelope_json_is_stripped_beside_ordinary_content() -> None:
    delivered = _deliver(
        _agent(),
        'The lint run finished clean.\n\n{"task_envelope":{"task_id":"wq-77"},"graph":{"nodes":3}}',
    )
    assert delivered == "The lint run finished clean."


def test_fresh_standalone_structured_graph_dump_is_humanized() -> None:
    delivered = _deliver(
        _agent(), '{"graph": {"node_count": 3, "edges": [["a","b"]]}}'
    )
    assert "node_count" not in delivered
    assert delivered  # a runtime notice, never raw JSON


# ------------------------------------------------------------------ mechanism pins


def test_fragment_stripper_keeps_prose_and_drops_structured_graph() -> None:
    text = (
        "You've been working through graph theory.\n\n"
        '{"task_envelope":{"task_id":"wq-77"},"graph":{"nodes":3}}'
    )
    assert _strip_orchestration_fragments(text) == "You've been working through graph theory."


def test_humanize_returns_none_for_pure_prose_with_marker_substrings() -> None:
    agent = _agent()
    assert (
        humanize_orchestration_leak(
            agent,
            "I've got no record of any autographed footballs in your collection.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        )
        is None
    )
