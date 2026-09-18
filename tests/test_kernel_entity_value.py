"""Entity-value binding on live web (operator live round 05756cc8; Terra+Flash
consult report_terra.md / report_flash.md). A labeled per-entity slot binds
only from a source that names that entity; a value from a foreign source or a
different obligation's computation is rejected; shared-comparison snippets pass.
The five gate cases both seats mandated before shipping.
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


def _row(desc, lane, query="", fmt=""):
    return {"description": desc, "lane": lane, "query": query, "format": fmt,
            "source_offset": 0, "resolves_carryover": ""}


def test_gate1_reject_wrong_entity_eth_from_btc_snippet(monkeypatch):
    """Flash #1: a BTC-only snippet must not fill ETH=<price> (live ETH=19)."""
    judge = _judge([_row("current BTC and ETH prices", "web_lookup", "BTC ETH price USD",
                         fmt="BTC= ; ETH=")],
                   [{"obligation_id": "ob1", "text": "BTC={n1}", "type": "observed"},
                    {"obligation_id": "ob1", "text": "ETH={n2}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Compare the current BTC and ETH prices in USD. Return exactly: BTC= ; ETH=",
        EffectRunner(mode="record"),
        lambda q: [{"title": "BTC", "snippet": "Bitcoin BTC price is currently 69289 USD right now",
                    "url": "https://c/btc"}], "test")
    ans = repl._extract_answer(transcript) or ""
    # outcome: ETH never ships a BTC-source number (whichever gate catches it —
    # entity-ownership or the harvest freshness gate; both agree here).
    assert "ETH=19" not in ans and "ETH=69289" not in ans, \
        f"ETH must not bind a BTC-source number: {ans!r}"


def test_gate2_preserve_query_ownership(monkeypatch):
    """Flash #2: a value whose only source names a DIFFERENT city cannot fill
    this city's slot."""
    judge = _judge([_row("temperatures in Riga and Warsaw", "web_lookup", "Riga Warsaw temp",
                         fmt="Riga= ; Warsaw=")],
                   [{"obligation_id": "ob1", "text": "Riga={n1}", "type": "observed"},
                    {"obligation_id": "ob1", "text": "Warsaw={n2}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up temperatures in Riga and Warsaw. Return: Riga= ; Warsaw=",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Riga", "snippet": "Riga is currently 14 degrees", "url": "https://w/r"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Riga=14" in ans, f"Riga binds its own source: {ans!r}"
    assert "Warsaw=14" not in ans, f"Warsaw must not borrow Riga's number: {ans!r}"


def test_gate3_shared_comparison_snippet_binds_both(monkeypatch):
    """Terra+Flash regression guard: a snippet naming BOTH entities with a
    shared value binds both — the gate must not false-reject it."""
    judge = _judge([_row("temperatures in Prague and Vienna", "web_lookup", "Prague Vienna temp",
                         fmt="Prague= ; Vienna=")],
                   [{"obligation_id": "ob1", "text": "Prague={n1}", "type": "observed"},
                    {"obligation_id": "ob1", "text": "Vienna={n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Prague and Vienna temperatures. Return: Prague= ; Vienna=",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Weather", "snippet": "Prague and Vienna are both currently 19 degrees",
                    "url": "https://w/pv"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Prague=19" in ans and "Vienna=19" in ans, \
        f"a shared-comparison snippet binds both entities: {ans!r}"


def test_gate4_single_entity_ask_not_gated(monkeypatch):
    """Terra: a single-entity ask (no confusion) must not be false-rejected even
    when the snippet names the entity only in the title."""
    judge = _judge([_row("gold price", "web_lookup", "gold price", fmt="")],
                   [{"obligation_id": "ob1", "text": "Gold is {n1} USD", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the current gold price?", EffectRunner(mode="record"),
        lambda q: [{"title": "Gold", "snippet": "spot 2400 USD", "url": "https://g/x"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "2400" in ans, f"a single-entity ask still ships: {ans!r}"


def test_gate5_unresolved_slot_declares_not_guesses(monkeypatch):
    """Terra: with no source for a slot's entity, the slot is unresolved — never
    a borrowed value."""
    judge = _judge([_row("temperatures in Kaunas and Oslo", "web_lookup", "Kaunas Oslo temp",
                         fmt="Kaunas= ; Oslo=")],
                   [{"obligation_id": "ob1", "text": "Kaunas={n1}", "type": "observed"},
                    {"obligation_id": "ob1", "text": "Oslo={n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Temperatures in Kaunas and Oslo. Return: Kaunas= ; Oslo=",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Kaunas", "snippet": "Kaunas is 12 degrees now", "url": "https://w/k"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Oslo=12" not in ans, f"Oslo has no source — must not borrow Kaunas's 12: {ans!r}"
