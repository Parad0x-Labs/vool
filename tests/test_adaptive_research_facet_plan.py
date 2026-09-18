"""The adaptive research lane plans its queries from the REQUEST, never from a template word.

Measured 2026-09-06 (served probe, fixture transport): for "compare the VW Passat and the VW Golf
in detail: production periods, sales, regions, engines and prices" the lane issued the whole
question twice, then `topic comparison tradeoffs sources` (twice), then `compare overview reliable
sources` (twice) -- the literal word "topic" stood where the subject should have been, and no query
named a facet. Every round fetched the same top-two production pages, so five of the seven
available sources were never read.
"""
from __future__ import annotations

from core.curiosity_roamer import (
    CuriosityRoamer,
    _adaptive_next_step,
    _request_facets,
)

REQUEST = "Now compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines and prices."


def test_an_enumerated_comparison_yields_its_subject_and_its_facets() -> None:
    subject, facets = _request_facets(REQUEST)
    assert "passat" in subject.lower() and "golf" in subject.lower(), subject
    assert not subject.lower().startswith(("now", "compare")), subject
    assert facets == ["production periods", "sales", "regions", "engines", "prices"], facets


def test_a_request_without_an_enumerated_list_has_no_facets() -> None:
    subject, facets = _request_facets("what is the gold price right now?")
    assert facets == []
    subject, facets = _request_facets("Explain photosynthesis: the light reactions matter most.")
    assert facets == [], facets  # a sentence after a colon is not a facet list


def test_stage_queries_never_carry_a_template_word() -> None:
    decision = {"needs_compare": True, "needs_verify": False, "needs_specific_focus": False, "topic_hints": [], "strategy": "compare"}
    metrics = {"strength": "weak", "domains": ["autoarchive-fixture.org"], "domain_count": 1, "official_count": 0}
    step = _adaptive_next_step(decision=decision, metrics=metrics, already_broadened=False, already_narrowed=False,
                               already_compared=False, already_verified=False)
    assert step is None or "topic" not in step[1].split(), step
    step = _adaptive_next_step(decision=decision, metrics=metrics, already_broadened=False, already_narrowed=False,
                               already_compared=False, already_verified=False, subject_seed="VW Passat and the VW Golf")
    assert step is not None and step[1].startswith("VW Passat and the VW Golf"), step


def test_facet_queries_run_first_bounded_and_deduplicated(monkeypatch) -> None:
    """Drives the real roamer with the adapter's search replaced by a recorder."""
    from core import curiosity_roamer as roamer_module

    issued: list[str] = []

    def _fake_search(query, **_kwargs):
        issued.append(query)
        return [{"result_title": f"page for {query}", "result_url": f"https://fixture.example/{len(issued)}",
                 "summary": f"synthetic note about {query}", "origin_domain": "fixture.example"}]

    monkeypatch.setattr(roamer_module.WebAdapter, "planned_search_query", staticmethod(_fake_search))
    monkeypatch.setattr(roamer_module, "authorize_retrieval", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(roamer_module.policy_engine, "allow_web_fallback", lambda: True)
    import core.retrieval_authority_gate as gate_module
    monkeypatch.setattr(gate_module, "authorize_retrieval", lambda *a, **k: True)
    roamer = CuriosityRoamer()
    result = roamer.adaptive_research(
        task_id="t-facets", user_input=REQUEST,
        classification={"task_class": "research"}, interpretation=None, source_context={"surface": "openclaw", "allow_remote_fetch": True},
    )
    assert result.queries_run, "the lane ran no query"
    lowered = [q.lower() for q in result.queries_run]
    for facet in ("production periods", "sales", "regions", "engines", "prices"):
        assert any(facet in q and "passat" in q and "golf" in q for q in lowered), (facet, lowered)
    assert len(result.queries_run) <= 6, lowered
    assert not any("topic comparison" in q for q in lowered), lowered
    assert len(result.notes) == len({n["result_url"] for n in result.notes}), "notes are deduplicated by url"
