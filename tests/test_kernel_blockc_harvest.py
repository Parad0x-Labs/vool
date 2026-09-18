"""BLOCK-C seam 6: harvest binding (signed block 60da1f5f).

t30: Warsaw=14 harvested from Helsinki's "next 14 days" — presence bound, not
attribute. t31: Git "2.47" from a stale blurb against an official-source
constraint. A live value binds only when the SOURCE TEXT attaches the asked
attribute to the number; a named official domain restricts receipts; a
latest-version claim needs latest-field context.
"""
from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims=None):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or []}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane, query=""):
    return {"description": desc, "lane": lane, "query": query, "format": "",
            "source_offset": 0, "resolves_carryover": ""}


def test_t30_replay_bare_number_cannot_be_a_temperature(monkeypatch):
    """'next 14 days' is not 14 degrees — the presence-only harvest rejects."""
    judge = _judge([_row("temperature in Warsaw", "web_lookup", "Warsaw temperature")],
                   [{"obligation_id": "ob1", "text": "Warsaw is at {n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What's the temperature in Warsaw right now?", EffectRunner(mode="record"),
        lambda q: [{"title": "Helsinki outlook", "snippet": "forecast for the next 14 days",
                    "url": "https://wx/h"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Warsaw is at" not in ans, f"the presence-only 14 must not ship as a temperature claim: {ans!r}"
    assert ans.startswith("Cannot answer"), f"the turn declares the gap: {ans!r}"
    assert "harvest binding" in ans      # the reason names the mechanism (it may quote the source context)


def test_unit_adjacent_temperature_still_grounds(monkeypatch):
    """Control: '14°C in Warsaw' attaches the unit — the harvest binds and ships."""
    judge = _judge([_row("temperature in Warsaw", "web_lookup", "Warsaw temperature")],
                   [{"obligation_id": "ob1", "text": "Warsaw is at {n1} degrees", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What's the temperature in Warsaw right now?", EffectRunner(mode="record"),
        lambda q: [{"title": "Warsaw weather", "snippet": "currently 14°C, clear",
                    "url": "https://wx/w"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "14" in ans, f"the unit-adjacent value ships: {ans!r}"


def test_t31_replay_official_domain_constraint_excludes_other_sources(monkeypatch):
    """The user NAMED git-scm.com — an off-domain blurb is ineligible; with no
    on-domain hit the turn declares it, never ships the stale number."""
    judge = _judge([_row("latest git version", "web_lookup", "git latest version")],
                   [{"obligation_id": "ob1", "text": "Git {n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the latest Git version according to the official git-scm.com?",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Some blog", "snippet": "Git 2.47 tutorial from last year",
                    "url": "https://blog.example/git"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "2.47" not in ans, f"the off-domain number must not ship: {ans!r}"
    assert "official domain git-scm.com" in transcript


def test_official_domain_hit_passes(monkeypatch):
    """Control: an on-domain hit survives the constraint and grounds the claim."""
    judge = _judge([_row("latest git version", "web_lookup", "git latest version")],
                   [{"obligation_id": "ob1", "text": "Git {n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the latest Git version according to the official git-scm.com?",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Git", "snippet": "latest release 2.51 download",
                    "url": "https://git-scm.com/downloads"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "2.51" in ans, f"the official-domain value ships: {ans!r}"


def test_freshness_gap_stale_context_declares_cannot_confirm(monkeypatch):
    """A 'latest version' ask with a source that never says latest/release
    around the number rejects the harvest instead of shipping stale."""
    judge = _judge([_row("latest zlib version", "web_lookup", "zlib latest version")],
                   [{"obligation_id": "ob1", "text": "zlib {n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the latest zlib version?", EffectRunner(mode="record"),
        lambda q: [{"title": "Archive", "snippet": "zlib 1.2 was used in the 2015 build",
                    "url": "https://old.example/z"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert ans.startswith("Cannot answer"), f"the turn declares the gap: {ans!r}"
    assert "freshness" in ans
    assert "[receipt:" not in ans, "no grounded claim shipped the stale number"
