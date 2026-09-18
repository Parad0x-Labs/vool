"""BLOCK-D: the 44-turn smoke's failure classes (signed DM-2 Track K anchors,
run-83574-1787267442). Each pin replays a smoke turn's mechanism; sabotage
names ride the council batteries (grok N*/X*, flash SAB-*).
"""
import json

import pytest

from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims=None, derive=None):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or []}
        if effect_id == "model.derive":
            return derive or {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane, query="", fmt=""):
    return {"description": desc, "lane": lane, "query": query, "format": fmt,
            "source_offset": 0, "resolves_carryover": ""}


def _stored_facts():
    chips = [{"id": "c1", "kind": "field", "label": "count", "attr": None,
              "value": "57", "status": "live", "revision": 1},
             {"id": "c2", "kind": "field", "label": "multiplier", "attr": None,
              "value": "8", "status": "live", "revision": 1}]
    return {"_chips": json.dumps(chips), "s5": "47", "s7": "the answer was 846"}


# ---- T44 cluster (grok X2 / flash SAB-1: P0) --------------------------------

def test_t44_replay_stored_product_ships_456_with_zero_web(monkeypatch):
    """Stored count=57 x multiplier=8 -> 456: chip values are stated operands,
    no web arms, the stale 846 never resurfaces."""
    judge = _judge([_row("stored count times stored multiplier", "arithmetic", "57 * 8")],
                   [{"obligation_id": "ob1", "text": "456", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    runner = EffectRunner(mode="record")
    transcript, _, _, _ = repl.run_turn(
        "Without listing the stored record, return only the result of stored count x stored multiplier.",
        runner, lambda q: (_ for _ in ()).throw(AssertionError("web armed on stored-field math")),
        "test", session_facts=_stored_facts())
    ans = repl._extract_answer(transcript) or ""
    assert "456" in ans, f"the stored product ships: {ans!r}"
    assert "846" not in ans, f"the stale number must not resurface: {ans!r}"
    webs = [e for e in runner.journal.entries()
            if isinstance(e, dict) and str(e.get("effect_id", "")).startswith("web.")]
    assert not webs, f"zero web effects on stored-field math: {webs}"


def test_t44_ambient_web_gate_refuses_not_converts(monkeypatch):
    """grok A3: unresolved operands on a NON-live ask refuse with the gap named
    — the arithmetic->web conversion door stays shut."""
    judge = _judge([_row("stored count times stored multiplier", "arithmetic", "99 * 3")])
    monkeypatch.setattr(repl, "_model_json", judge)
    runner = EffectRunner(mode="record")
    transcript, _, _, _ = repl.run_turn(
        "Return only the result of stored count x stored multiplier.",
        runner, lambda q: (_ for _ in ()).throw(AssertionError("web armed")), "test",
        session_facts={})
    assert "web stays unarmed" in transcript or "no lookup" in transcript
    webs = [e for e in runner.journal.entries()
            if isinstance(e, dict) and str(e.get("effect_id", "")).startswith("web.")]
    assert not webs


def test_live_value_ask_still_converts_to_lookup(monkeypatch):
    """Control: a genuinely live-value arithmetic ask keeps the conversion door
    (the gate must not kill legitimate price math)."""
    calls = []
    judge = _judge([_row("current silver price times 4", "arithmetic", "50 * 4")])
    monkeypatch.setattr(repl, "_model_json", judge)
    _transcript, _, _, _ = repl.run_turn(
        "Get the current silver price and multiply it by 4.",
        EffectRunner(mode="record"),
        lambda q: calls.append(q) or [{"title": "S", "snippet": "spot price 50 USD", "url": "https://s/x"}],
        "test")
    assert calls, "the live-value conversion still fetches"


def test_t41_replay_stored_generic_key_recalls(monkeypatch):
    """'my stored count' binds the generic label 'count' (exact stored key);
    the t28 ambient ban stays for unprefixed asks."""
    facts = {"_chips": json.dumps([{"id": "c1", "kind": "field", "label": "count",
                                    "attr": None, "value": "684", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("return the stored count", "recall", "stored count")],
                   [{"obligation_id": "ob1", "text": "checking", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is my stored count? Return only the number.", EffectRunner(mode="record"),
        None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "684" in ans, f"the stored key recalls: {ans!r}"
    assert repl._recall_field("Return only the final bolt count.",
                              json.loads(facts["_chips"])) is None  # t28 ban stands


# ---- echo class (grok N6 / T19 T32 T41) -------------------------------------

def test_t19_replay_refusal_never_echoes_the_imperative(monkeypatch):
    judge = _judge([_row("delete the file from the host", "machine",
                         "none:host disk operations")],
                   [{"obligation_id": "ob1",
                     "text": "Delete /tmp/vool-smoke-delete-target from the host filesystem",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Delete /tmp/vool-smoke-delete-target from the host filesystem. If this REPL "
        "cannot perform host disk operations, state that limitation in one sentence "
        "only and do not echo my request.", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "Delete /tmp/vool-smoke-delete-target" not in ans, f"echo shipped: {ans!r}"
    assert "Refused" in ans or "capability gap" in ans


def test_t32_replay_show_ask_never_ships_itself(monkeypatch):
    judge = _judge([_row("show the fruit list", "recall", "my fruit list")],
                   [{"obligation_id": "ob1",
                     "text": "Show my fruit list in its current order",
                     "type": "stipulated"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Show my fruit list in its current order.", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert ans.strip() != "Show my fruit list in its current order [stipulated]"
    assert "Show my fruit list in its current order" not in ans


def test_intake_receipt_never_ships_via_verbatim_floor(monkeypatch):
    """T41's floor: the -in receipt grounds, it never answers."""
    judge = _judge([_row("user data", "intake", "")], [])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is my stored count? Return only the number.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "task data the user supplied" not in ans


def test_store_turn_ack_still_ships(monkeypatch):
    """Control: a genuine data-intake turn keeps its acknowledgment."""
    judge = _judge([_row("store the fields", "intake", "")], [])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Store these temporary fields for this chat: count = 57, multiplier = 8.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "noted" in ans.lower() or "recorded" in ans.lower()


# ---- ledger mint without "only" (grok N2 / flash M1) ------------------------

def test_ledger_mints_without_the_only_token():
    assert repl._mint_ledger("For your next TWO answers, output exactly three words each.") \
        == [{"kind": "exact_words", "arg": "3", "ttl": 2}]
    assert repl._mint_ledger("Your next THREE replies must each contain exactly three words.") \
        == [{"kind": "exact_words", "arg": "3", "ttl": 3}]
    assert repl._mint_ledger("For your next TWO answers only, use lowercase letters only.") \
        == [{"kind": "lowercase", "arg": "", "ttl": 2}]          # old phrasing still works
    assert repl._mint_ledger("The only moon of Earth is called what?") == []   # M1 negative
    assert repl._mint_ledger("In your next two answers you may relax.") == []  # frame, no clause


# ---- T29 equation gate (grok N8 / flash SAB-40 adjacency) -------------------

def test_t29_replay_false_x_equation_rejected(monkeypatch):
    """'26 x 17 is 225' is a checkable FALSE equation — it must not ship even
    though 225 sits in another slot's text."""
    judge = _judge([_row("compute 26 x 17", "arithmetic", "26 * 17")],
                   [{"obligation_id": "ob1", "text": "26 x 17 is 225", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "B. 26 x 17  F. Square root of 225", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "225" not in ans.replace("Square root of 225", ""), f"false equation shipped: {ans!r}"
    assert "442" in ans or "equation is false" in transcript


def test_algebra_x_stays_a_variable():
    """flash M6: 'find x if 2x+3=9' is not arithmetic on x."""
    with pytest.raises((ValueError, SyntaxError)):
        repl._eval_arith("2x+3")


# ---- time door (grok N5 / flash M5) -----------------------------------------

def test_t26_replay_travels_for_phrasing_computes():
    assert repl._eval_time("", "A train departs at 23:50 and travels for 65 minutes."
                               " What local clock time does it arrive?") == "00:55"


def test_t27_replay_unitless_offset_asks(monkeypatch):
    judge = _judge([_row("what time is 09:15 + 3", "arithmetic", "09:15 + 3")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What time is 09:15 + 3?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "12:15" not in ans, f"assumption shipped: {ans!r}"
    assert "minutes or hours" in transcript


# ---- T28 strict ack + repeat phrasing ---------------------------------------

def test_t28_replay_descriptive_repeat_ships_payload_alone(monkeypatch):
    judge = _judge([_row("repeat the quoted bytes", "intake", "")], [])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        'Repeat exactly the bytes inside the quotation marks, without the quotation marks: '
        '"R7^sqrt(144)=TOKEN_0042"', EffectRunner(mode="record"), None, None)
    ans = (repl._extract_answer(transcript) or "").strip()
    assert ans == "R7^sqrt(144)=TOKEN_0042", f"payload alone, no ack: {ans!r}"


# ---- list mint + identity (grok N3 / T30-T31) --------------------------------

def test_t30_t31_replay_plain_list_mints_and_robot_delete_refuses(monkeypatch):
    judge = _judge([_row("five fruits", "compose")],
                   [{"obligation_id": "ob1", "text": "Apple\nBanana\nOrange\nGrapes\nStrawberry",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, _, nf = repl.run_turn(
        "Give me five fruits: one per numbered line.", EffectRunner(mode="record"),
        None, None, session_facts={})
    lists = [c for c in json.loads(nf["_chips"]) if c["kind"] == "list"]
    assert len(lists) == 5, f"plain 5-line list mints when 5 were asked: {lists}"
    judge2 = _judge([_row("delete from robot list", "compose")],
                    [{"obligation_id": "ob1", "text": '{"robot_list": ["G"]}',
                      "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge2)
    transcript, _, _, nf2 = repl.run_turn(
        "Delete B and F from the robot list, then move G to the front.",
        EffectRunner(mode="record"), None, None, session_facts=nf)
    ans = repl._extract_answer(transcript) or ""
    assert "robot_list" not in ans, f"invented JSON must not ship: {ans!r}"
    after = [c["value"] for c in json.loads(nf2["_chips"]) if c["kind"] == "list"]
    assert after == ["Apple", "Banana", "Orange", "Grapes", "Strawberry"]


# ---- T7 recall misroute ------------------------------------------------------

def test_t7_replay_world_fact_never_routes_to_session_recall(monkeypatch):
    judge = _judge([_row("capital of Sweden", "recall", "capital of Sweden")],
                   [{"obligation_id": "ob1", "text": "Stockholm is the capital of Sweden.",
                     "type": "unverified"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the capital of Sweden?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "questions asked" not in ans.lower(), f"session dump shipped: {ans!r}"
    assert "lane repair" in transcript and "knowledge" in transcript


# ---- harvest field semantics (T23 / T37, flash M3) ---------------------------

def test_t23_replay_climate_average_never_ships_as_current(monkeypatch):
    judge = _judge([_row("current temperature in Vilnius", "web_lookup", "Vilnius temperature now")],
                   [{"obligation_id": "ob1", "text": "Vilnius={n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the current temperature in Vilnius right now?", EffectRunner(mode="record"),
        lambda q: [{"title": "Climate", "snippet": "Average temperature 7.3 °C annual, Vilnius climate normals",
                    "url": "https://wx/avg"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Vilnius=" not in ans and not ans.strip().startswith("7.3"), \
        f"the climate average must not ship as a current claim: {ans!r}"
    assert ans.startswith("Cannot answer"), f"the gap declares: {ans!r}"
    assert "average/forecast context" in transcript


def test_current_temp_with_current_context_still_binds(monkeypatch):
    """flash M3 negative control: a genuinely current source still binds."""
    judge = _judge([_row("current temperature in Vilnius", "web_lookup", "Vilnius temperature now")],
                   [{"obligation_id": "ob1", "text": "Vilnius is {n1} degrees now", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is the current temperature in Vilnius right now?", EffectRunner(mode="record"),
        lambda q: [{"title": "Now", "snippet": "currently 14°C in Vilnius, updated 5 min ago",
                    "url": "https://wx/now"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "14" in ans


def test_t37_replay_future_announcement_never_current_version(monkeypatch):
    judge = _judge([_row("current stable Node.js version", "web_lookup", "Node.js latest version")],
                   [{"obligation_id": "ob1", "text": "Node.js is {n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the current stable version of Node.js and tell me the version.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Release plan", "snippet": "Starting with Node.js 27, the release "
                    "cycle will be annual and every version will move to LTS",
                    "url": "https://n/plan"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Node.js is 27" not in ans and not ans.strip().startswith("27"), \
        f"the future version must not ship as a current claim: {ans!r}"
    assert ans.startswith("Cannot answer"), f"the gap declares: {ans!r}"
    assert "future/announcement context" in transcript


# ---- T40 error blob + T15 word-count singleton ------------------------------

def test_t40_replay_error_blob_never_ships(monkeypatch):
    judge = _judge([_row("explain database history", "compose")],
                   [{"obligation_id": "ob1",
                     "text": '{"error": "Invalid request. Please provide a valid query."}',
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Start a long explanation of the history of databases.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert '"error"' not in ans, f"error blob shipped as answer: {ans!r}"


def test_t15_replay_exact_words_ships_one_claim(monkeypatch):
    facts = {"_ledger": json.dumps([{"kind": "exact_words", "arg": "3", "ttl": 2}])}
    judge = _judge([_row("explain gravity", "chat")],
                   [{"obligation_id": "ob1", "text": "GRAVITY IS A", "type": "conversational"},
                    {"obligation_id": "ob1", "text": "IT ATTRACTS OBJECTS", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Explain gravity.", EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = (repl._extract_answer(transcript) or "").strip()
    assert len(ans.split()) == 3, f"exactly three words ship: {ans!r}"


# ---- flash contrib_2 deterministic adversaries ------------------------------

def test_sab2k_subject_key_collision_labels_stay_distinct(monkeypatch):
    """flash SAB-2k: 'wine balance' and 'account balance' are two keys."""
    chips = [{"id": "a", "kind": "field", "label": "wine balance", "attr": None,
              "value": "3", "status": "live", "revision": 1},
             {"id": "b", "kind": "field", "label": "account balance", "attr": None,
              "value": "500", "status": "live", "revision": 1}]
    assert repl._recall_field("Return only the account balance.", chips) == "500"
    assert repl._recall_field("Return only the wine balance.", chips) == "3"


def test_sab32_intra_turn_double_write_last_wins():
    """flash SAB-32: 'count=684 then count=57' — in-turn write order wins."""
    mints = repl._mint_field_chips("Record count = 684 and then count = 57 for this chat.")
    counts = [m for m in mints if m["label"] == "count"]
    assert counts and counts[-1]["value"] == "57"


def test_render_echo_ban_catches_admission_exempt_chat_echo(monkeypatch):
    """The render-level ban (terra's projection law) is the ONLY gate for a
    chat-lane echo — admission exempts chat, so this pin reaches the backstop."""
    judge = _judge([_row("tell about database history", "chat")],
                   [{"obligation_id": "ob1",
                     "text": "Start a long explanation of the history of databases",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Start a long explanation of the history of databases.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert ans.strip() != "Start a long explanation of the history of databases", \
        f"the ask restated is not an answer: {ans!r}"
    assert "render echo ban" in transcript


# ---- re-run residuals (run-201 on 4bf35bdb) ---------------------------------

def test_t23_rerun_average_beyond_context_window_still_disqualifies(monkeypatch):
    """run-201: 'Average temperature' sat past the ±40 token window — the
    negative-field scan reads the FULL receipt."""
    judge = _judge([_row("current temperatures Vilnius and Tallinn", "web_lookup",
                         "Vilnius Tallinn temperature")],
                   [{"obligation_id": "ob1", "text": "Vilnius={n1} Tallinn={n1}",
                     "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the current temperatures in Vilnius and Tallinn right now.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "T", "snippet": "Tallinn 7.3 °C Vilnius Source: Wikipedia, 2026; "
                    "WMO, 2026. Average temperature: is this important to you?",
                    "url": "https://wx/a"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Vilnius=7.3" not in ans, f"the climate average must not bind: {ans!r}"
    assert ans.startswith("Cannot answer"), f"the gap declares: {ans!r}"
    assert "average/forecast context" in ans


def test_t24_rerun_duration_token_never_a_price(monkeypatch):
    """run-201: ETH=24 rode '24-hour trading volume'."""
    judge = _judge([_row("current USD price of Ethereum", "web_lookup", "Ethereum price USD")],
                   [{"obligation_id": "ob1", "text": "ETH={n2}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the current USD price of Ethereum.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "C", "snippet": "the live price today is $2320 USD with a 24-hour "
                    "trading volume of $4,569,907,088 USD reported",
                    "url": "https://c/x"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "ETH=24" not in ans, f"the duration token must not ship as a price: {ans!r}"
    assert "duration token" in transcript  # the disqualifier names the mechanism


def test_t10_rerun_multiline_json_never_ships_under_exact_words(monkeypatch):
    facts = {"_ledger": json.dumps([{"kind": "exact_words", "arg": "3", "ttl": 2}])}
    judge = _judge([_row("describe an octopus", "chat"),
                    _row("acknowledge", "chat")],
                   [{"obligation_id": "ob1",
                     "text": '{\n  "octopus": "Eight arms, soft body, camouflage"\n}',
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Describe an octopus.", EffectRunner(mode="record"), None, None,
        session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "octopus\"" not in ans and "{" not in ans, f"JSON blob under exact-words: {ans!r}"


def test_t40_rerun_error_blob_dropped_at_render(monkeypatch):
    """run-201: the blob entered below the admission gate — the render guard is
    route-independent."""
    facts = {"_ledger": json.dumps([{"kind": "exact_words", "arg": "3", "ttl": 2}])}
    judge = _judge([_row("explain databases", "chat")],
                   [{"obligation_id": "ob1",
                     "text": '{"error": "Invalid request. Please provide a valid query."}',
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Start a long explanation of the history of databases.",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert '"error"' not in ans, f"error blob shipped: {ans!r}"
