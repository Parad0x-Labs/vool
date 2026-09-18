"""BLOCK-D2: the operator's live-round failure classes (2026-08-21 session,
triple-attested with the independent audit and run-201): atomic lane repair,
terminal carryover lifecycle, setter ack, enumerative coverage, page fallback.
"""
import json

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


def test_lane_repair_is_atomic_tirana_ships(monkeypatch):
    """Live lane-8A: a recall-mislabeled knowledge slot repaired pre-mint —
    the recall content-anchoring check never sees it and the answer ships."""
    judge = _judge([_row("Capital of Albania", "recall", "Capital of Albania")],
                   [{"obligation_id": "ob1", "text": "Tirana", "type": "unverified"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Capital of Albania?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "Tirana" in ans, f"the repaired slot ships its answer: {ans!r}"
    assert "recall claim rejected" not in transcript, "no stale recall semantics survive"
    assert "lane repair (pre-mint)" in transcript


def test_terminal_states_never_carry(monkeypatch):
    """Live lane-6: a declared-unanswerable turn leaves NOTHING in the
    carryover — terminal is terminal."""
    judge = _judge([_row("temperatures in Riga and Warsaw", "web_lookup", "Riga Warsaw temperature")],
                   [{"obligation_id": "ob1", "text": "Riga={n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, still_open, _ = repl.run_turn(
        "Look up the current temperatures in Riga and Warsaw right now.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "F", "snippet": "10-Day Weather Forecast for Riga, next 3 days quality",
                    "url": "https://wx/f"}], "test")
    assert still_open == [], f"declared-unanswerable must not carry: {still_open}"


def test_open_partial_still_carries(monkeypatch):
    """Control: a genuinely OPEN partial (web ask degraded to the excerpt
    floor) still carries — the lifecycle fix must not delete future work."""
    judge = _judge([_row("EPL standings leader", "web_lookup", "EPL standings")],
                   [{"obligation_id": "ob1", "text": "The table looks close this year",
                     "type": "unverified"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, still_open, _ = repl.run_turn(
        "Who leads the EPL standings right now?", EffectRunner(mode="record"),
        lambda q: [{"title": "EPL", "snippet": "matchday coverage and articles",
                    "url": "https://epl/x"}], "test")
    assert any("EPL" in c["description"] or "standings" in c["description"]
               for c in still_open), f"an open partial is future work: {still_open}"


def test_setter_turn_acks_and_mints_no_chips(monkeypatch):
    """Live lane-2: the setter ships a deterministic ack, never a truncated
    echo, and never mints chips from its own render; the constraint still
    applies from the NEXT answer."""
    judge = _judge([_row("use three words", "compose")],
                   [{"obligation_id": "ob1", "text": "use exactly three", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, _, nf = repl.run_turn(
        "For your next TWO answers, use exactly three words each.",
        EffectRunner(mode="record"), None, None, session_facts={})
    ans_facts = nf
    chips = json.loads(ans_facts.get("_chips", "[]"))
    assert not [c for c in chips if c.get("kind") == "list"], f"no chips from the setter: {chips}"
    ledger = json.loads(ans_facts.get("_ledger", "[]"))
    assert ledger and ledger[0]["kind"] == "exact_words", "the mint still installs"
    judge2 = _judge([_row("describe a desert", "chat")],
                    [{"obligation_id": "ob1", "text": "Hot, dry, sandy", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge2)
    transcript2, _, _, _ = repl.run_turn(
        "Describe a desert.", EffectRunner(mode="record"), None, None, session_facts=nf)
    ans2 = (repl._extract_answer(transcript2) or "").strip()
    assert len(ans2.split()) == 3, f"the constraint applies from the next answer: {ans2!r}"


def test_setter_ack_renders_deterministically(monkeypatch):
    judge = _judge([_row("use three words", "compose")],
                   [{"obligation_id": "ob1", "text": "use exactly three", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "For your next TWO answers, use exactly three words each.",
        EffectRunner(mode="record"), None, None, session_facts={})
    ans = repl._extract_answer(transcript) or ""
    assert "applies to your next" in ans, f"deterministic ack ships: {ans!r}"
    assert ans.strip() != "use exactly three"


def test_enumerative_coverage_readds_ec2(monkeypatch):
    """Live lane-4: EC2 dropped while RX-731/2FA/Neo4j shipped — the kernel
    re-adds the user's own identifier tokens; the intake 'noted' ack stays out
    of an extraction question."""
    judge = _judge([_row("How many records total", "arithmetic", "46 + 29"),
                    _row("Which identifiers or product names appeared", "intake")],
                   [{"obligation_id": "ob2", "text": "RX-731, 2FA, Neo4j", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "An EC2 task tagged RX-731 used 2FA and processed 46 records before Neo4j "
        "processed another 29. How many records were processed in total, and which "
        "identifiers or product names appeared?",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "EC2" in ans, f"the dropped identifier is re-added: {ans!r}"
    assert "noted — recorded" not in ans, f"no intake ack on an extraction question: {ans!r}"


def test_page_fallback_tries_next_source(monkeypatch):
    """Live lane-6: a 403 on the first page falls back to the next hit instead
    of dying."""
    fetched = []

    def fake_page(url):
        fetched.append(url)
        if "blocked" in url:
            raise RuntimeError("HTTP Error 403: Forbidden")
        return "Warsaw current temperature 19°C right now, updated 5 minutes ago"

    monkeypatch.setattr(repl, "_fetch_page_text", fake_page)
    judge = _judge([_row("temperature in Warsaw", "web_lookup", "Warsaw temperature now")],
                   [{"obligation_id": "ob1", "text": "Warsaw is {n1} degrees now", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the current temperature in Warsaw right now?",
        EffectRunner(mode="record"),
        lambda q: [{"title": "A", "snippet": "10-Day forecast, next 3 days", "url": "https://blocked/a"},
                   {"title": "B", "snippet": "next 14 days outlook", "url": "https://ok/b"}], "test")
    assert len(fetched) >= 2, f"the fallback tried the next source: {fetched}"
    assert "trying the next source" in transcript
