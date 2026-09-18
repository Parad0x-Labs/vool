"""BLOCK-E RED FIXTURES — the delta review's OPEN class, PROVEN red-on-HEAD.

These encode the correct behavior for the calc-token / unbound-number-bus class
(delta review dm-delta-review-20260821, 3/3 converged). They are EXPECTED TO
FAIL on c2219d65 — that is the build gate: a fix is authorized only once these
bite here, and it ships only once it makes them green. Three negative controls
stay GREEN and box the fix against over-correction: real unit-adjacent
temperature still binds (K1), user-stated arithmetic still ships (K4), and a
MATCHING-entity session recall still binds (K2 rejects on entity mismatch only).
Assertions ban the known overfit cheats too (T22: fabricated currency; T4:
operand-hidden verdict) so a surface patch cannot green a RED without fixing the
class. (Pre-verified by an independent skeptic panel, 2026-08-21.)

All five fixtures Grok signed at Round 3 (round_3_grok.md §6: "RED-T18, RED-T22,
RED-T30, RED-T33, RED-T4 … must actually fail HEAD") now reproduce through the
real run_turn path against the exact tape values from
round0_raw_evidence/hardmix_repl_sessions.jsonl (run-24126-1787307717):
  T18 row17, T22 row21, T30 row29, T33 row32 (TEMPERATURE=86 from s18/Athens,
  live Budapest was 85), T4 row3 (Jacket needed: 79 > 10, 10 = "10 day" duration).

Grok/Pro/Fable converged package under test:
  K1 exclusive attribute binding (local unit-window + entity + one-attr-per-number)
  K2 keyed session facts (entity+attribute match to be an operand)
  K4 harvest-reject retracts the calc that consumed the bad token
  non-unit slots (capital/recall/enum) ineligible for unlabeled numeric values
"""
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


# ---- RED-T18: elevation must not fill a temperature slot -----------------
def test_RED_t18_elevation_not_temperature(monkeypatch):
    judge = _judge(
        [_row("current temperature in Budapest", "web_lookup", "Budapest temperature",
              fmt="Budapest= ; Krakow=")],
        synth_claims=[{"obligation_id": "ob1", "text": "Budapest={n1}", "type": "observed"}],
        derive={"computations": [{"obligation_id": "ob1", "label": "Budapest Temperature",
                                  "expression": "{n1}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the current temperature in Budapest. Return: Budapest= ; Krakow=",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Budapest WU", "snippet": "Budapest Elev 134 ft, currently 78 °F",
                    "url": "https://wu/bud"}], "test")
    ans = repl._extract_answer(transcript) or ""
    # Target the BINDING, not any mention: the correct fix REJECTS 134 and names it
    # in a rejection diagnostic ("harvest binding: 134 at 'Elev 134 ft'"), so a bare
    # "134 not in ans" would trip on the diagnostic even though nothing is bound.
    # What must never ship is 134 BOUND as a temperature slot value.
    assert "=134" not in ans and "= 134" not in ans, \
        f"RED: elevation 134 must not BIND as a temperature slot value: {ans!r}"


# ---- RED-T22: population must not fill a capital slot --------------------
def test_RED_t22_population_not_capital(monkeypatch):
    judge = _judge(
        [_row("Capital of Bosnia and Herzegovina", "web_lookup", "capital Bosnia")],
        synth_claims=[{"obligation_id": "ob1", "text": "Sarajevo is {n1} RUB", "type": "observed"}],
        derive={"computations": [{"obligation_id": "ob1", "label": "Capital of Bosnia",
                                  "expression": "{n1}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "A. Capital of Bosnia and Herzegovina", EffectRunner(mode="record"),
        lambda q: [{"title": "Sarajevo", "snippet": "Sarajevo is the capital; population 275524",
                    "url": "https://x/sa"}], "test")
    ans = repl._extract_answer(transcript) or ""
    # Target the BINDING, not any mention: the correct fix drops the fabrication and
    # may ship the raw source as an evidence excerpt ("... population 275524 ..."),
    # so a bare "275524 not in ans" would trip on that honest excerpt. What must
    # never ship is 275524 BOUND as Sarajevo's value or a fabricated currency.
    assert "is 275524" not in ans and "RUB" not in ans, \
        f"RED: population/currency must not BIND to the capital slot: {ans!r}"


# ---- RED-T30: recall of an unobtained value must not fabricate -----------
def test_RED_t30_fabricated_recall(monkeypatch):
    facts = {"s6": "the weather was cold", "s9": "1"}
    judge = _judge(
        [_row("the Solana price from turn 1", "knowledge", "prior Solana price")],
        synth_claims=[{"obligation_id": "ob1",
                       "text": "The exact Solana price reported in TURN 1 is {n1} USD.",
                       "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Return only the exact Solana price you reported in TURN 1. Do not perform another lookup.",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "is 1 USD" not in ans and "1 USD" not in ans, \
        f"RED: an unobtained recall must not fabricate a number: {ans!r}"


# ---- RED-T4: a harvest-rejected duration must not ride a calc ------------
# Real tape row3 (turn 4): "Munich … 10 day outlook" harvested 10 (a duration);
# derive computed {temp} > {duration}; the calc re-cited its OWN tokens
# (ob2-calc1, exempt from the harvest gate) so "Jacket needed: 79 > 10 = true"
# shipped. K4: the harvest-reject of 10 must retract the calc that consumed it.
def test_RED_t4_harvest_reject_retracts_calc(monkeypatch):
    judge = _judge(
        [_row("Look up the current weather in Munich", "web_lookup", "Munich weather"),
         {"description": "whether I need a jacket today", "lane": "chat", "query": "",
          "format": "", "source_offset": 23, "resolves_carryover": ""}],
        synth_claims=[{"obligation_id": "ob2", "text": "Jacket needed: {n3} > {n4} = true",
                       "type": "observed"}],
        derive={"computations": [{"obligation_id": "ob2", "label": "Jacket needed",
                                  "expression": "{n1} > {n2}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the current weather in Munich and tell me whether I need a jacket today.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Munich weather", "snippet": "Munich currently 79 °F, 10 day outlook",
                    "url": "https://wu/munich"}], "test")
    ans = repl._extract_answer(transcript) or ""
    # SEMANTIC check, not a substring denylist: the calc that consumed the
    # harvest-rejected duration (ob2-calc1) must not survive to the shipped answer
    # in ANY render. Every render-reword cheat ("Jacket needed: yes / true /
    # required", verdict-as-"1") still cites [receipt:ob2-calc1]; only a genuine
    # K4 retraction removes that receipt. So a fix that keeps the calc but rewords
    # the output cannot pass — it must actually retract.
    assert "79 > 10" not in ans and "receipt:ob2-calc1" not in ans, \
        f"RED: the duration calc (ob2-calc1) must be retracted, not re-rendered: {ans!r}"


# ---- RED-T33: a prior-turn session temp must not fill another city's slot -
# Real tape row32 (turn 33): a labeled TEMPERATURE= slot for Budapest bound 86 —
# the stale Athens temperature from turn 9 (session fact s18), NOT the live
# Budapest page (which read 85). K2: a session value carries (entity, attr) from
# mint time; an ATHENS-minted value is ineligible for a BUDAPEST slot.
# s18 explicitly names Athens so the entity-MISMATCH mechanism is what fails
# here (86 laundering through the derive path, which — unlike synthesis — does
# not exclude s* refs). This fixture and its pairing control
# test_control_matching_entity_session_recall are a SINGLE-VARIABLE discriminator:
# both feed the identical Athens s18=86 through the identical derive route with NO
# web source; the ONLY difference is the queried entity (Budapest here, Athens in
# the control). So the only fix that greens this RED while keeping the control
# green is one keyed on entity match — a blanket "ban session numbers" or a
# "prefer web when present" heuristic cannot pass both. (The live-85-beats-86
# competition from the real tape is not reproduced in the synthetic harness — the
# session value preempts before a web competitor is weighed; it validates on the
# hardmix re-run. The class — a foreign-entity value filling a slot — is faithful.)
def test_RED_t33_stale_session_temp_not_other_city(monkeypatch):
    facts = {"s18": "Athens temperature is 86 °F"}
    judge = _judge(
        [_row("Budapest booking total and current temperature", "arithmetic",
              "Budapest temperature",
              fmt="BOOKING_ID= ; TOTAL_COST= ; TECHNOLOGIES= ; TEMPERATURE=")],
        synth_claims=[{"obligation_id": "ob2", "text": "TEMPERATURE={n1}", "type": "observed"}],
        derive={"computations": [{"obligation_id": "ob2", "label": "Temperature",
                                  "expression": "{n1}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Booking in Budapest. Also look up Budapest's current temperature. Return: TEMPERATURE=",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "86" not in ans, \
        f"RED: an Athens session temp (86) must not fill Budapest's slot: {ans!r}"






def test_control_real_temperature_still_binds(monkeypatch):
    judge = _judge([_row("current temperature in Vilnius", "web_lookup", "Vilnius temp",
                         fmt="Vilnius=")],
                   synth_claims=[{"obligation_id": "ob1", "text": "Vilnius={n1}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Current temperature in Vilnius. Return: Vilnius=", EffectRunner(mode="record"),
        lambda q: [{"title": "Vilnius", "snippet": "Vilnius is currently 14 °C", "url": "https://w/v"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "14" in ans, f"CONTROL: a real unit-adjacent temperature must still bind: {ans!r}"


def test_control_user_arithmetic_still_binds(monkeypatch):
    # Guards K4 from over-retracting: a calc whose operands are user-stated
    # numbers (no harvest reject) must still ship its result.
    judge = _judge([_row("936 / 18", "arithmetic", "")],
                   synth_claims=[{"obligation_id": "ob1", "text": "936 / 18 = {n3}",
                                  "type": "observed"}],
                   derive={"computations": [{"obligation_id": "ob1", "label": "Quotient",
                                             "expression": "{n1} / {n2}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What is 936 / 18?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "52" in ans, f"CONTROL: user-stated arithmetic must still bind: {ans!r}"


def test_control_matching_entity_session_recall(monkeypatch):
    # SINGLE-VARIABLE pair with RED-T33: identical Athens s18=86, identical derive
    # route, identical fmt/obligation, NO web — the ONLY change is the queried
    # entity (Athens == the session entity), so this MUST stay green. Therefore
    # the only fix that greens T33 while keeping this green is one keyed on entity
    # MATCH; a blanket "ban session numbers" or "prefer web" rule reds this.
    facts = {"s18": "Athens temperature is 86 °F"}
    judge = _judge(
        [_row("Athens booking total and current temperature", "arithmetic",
              "Athens temperature",
              fmt="BOOKING_ID= ; TOTAL_COST= ; TECHNOLOGIES= ; TEMPERATURE=")],
        synth_claims=[{"obligation_id": "ob2", "text": "TEMPERATURE={n1}", "type": "observed"}],
        derive={"computations": [{"obligation_id": "ob2", "label": "Temperature",
                                  "expression": "{n1}"}], "missing": []})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Booking in Athens. Also look up Athens's current temperature. Return: TEMPERATURE=",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "86" in ans, f"CONTROL: a matching-entity session recall must still bind: {ans!r}"


# ---- LIVE control (mixed-40 root fix, CH1/CH7): a $-price and a temperature in
# the SAME turn must BOTH ground — the ° gate must not bleed onto the $ price
# (obligation-scoped ask), and the BTC ticker slot must accept a "Bitcoin" source
# (ticker alias). This is the real-snippet negative control the stubbed RED
# fixtures lacked (test-vs-served divergence, HY3 FC-A).
def test_live_price_grounds_beside_temperature(monkeypatch):
    judge = _judge(
        [_row("current temperature in Vilnius", "web_lookup", "Vilnius temperature",
              fmt="Vilnius= ; BTC="),
         _row("current BTC price in USD", "web_lookup", "bitcoin price usd")],
        synth_claims=[{"obligation_id": "ob1", "text": "Vilnius={n1}", "type": "observed"},
                      {"obligation_id": "ob2", "text": "BTC={n2}", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)

    def fetch(q):
        if "itcoin" in q.lower() or "btc" in q.lower():
            return [{"title": "CMC", "snippet": "The live Bitcoin price today is $74,402.55 USD",
                     "url": "https://cmc/btc"}]
        return [{"title": "AW", "snippet": "Vilnius currently Partly sunny with a temperature of 69°",
                 "url": "https://aw/v"}]
    transcript, _, _, _ = repl.run_turn(
        "Current temperature in Vilnius and current BTC price in USD. Return: Vilnius= ; BTC=",
        EffectRunner(mode="record"), fetch, "test")
    ans = repl._extract_answer(transcript) or ""
    assert "Vilnius=69" in ans and "74402.55" in ans, \
        f"CONTROL: temp and $-price must both ground beside each other: {ans!r}"


# ---- LIVE control (CH1/CH7): an FX rate grounds beside a co-asked temperature.
# EUR/USD is unitless — the ° gate must not touch it (obligation-scoped ask).
def test_live_fx_rate_grounds_beside_temperature(monkeypatch):
    judge = _judge(
        [_row("current EUR/USD exchange rate", "web_lookup", "eur usd rate"),
         _row("current temperature in Frankfurt", "web_lookup", "Frankfurt temperature")],
        synth_claims=[{"obligation_id": "ob1", "text": "The current EUR/USD rate is {n1}.",
                       "type": "observed"},
                      {"obligation_id": "ob2", "text": "Frankfurt is {n2}°.", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)

    def fetch(q):
        if "eur" in q.lower() or "usd" in q.lower() or "rate" in q.lower():
            return [{"title": "X", "snippet": "The live EUR/USD exchange rate is 1.17011 (Euro to US Dollar)",
                     "url": "https://x/fx"}]
        return [{"title": "AW", "snippet": "Frankfurt currently a temperature of 66°",
                 "url": "https://aw/f"}]
    transcript, _, _, _ = repl.run_turn(
        "Current EUR/USD rate and temperature in Frankfurt.",
        EffectRunner(mode="record"), fetch, "test")
    ans = repl._extract_answer(transcript) or ""
    assert "1.17011" in ans, f"CONTROL: an FX rate must ground beside a temperature ask: {ans!r}"


# ---- OVER-FIRE guard (protective core stays): a VOLUME number must NOT ship as a
# price. The asserted-unit guard rejects "{n} USD" when the number's source window
# carries no currency (S26: a 24h-volume figure claimed as a price).
def test_volume_not_shipped_as_price(monkeypatch):
    judge = _judge(
        [_row("current BTC price in USD", "web_lookup", "bitcoin price")],
        synth_claims=[{"obligation_id": "ob1", "text": "BTC price is {n1} USD", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Current BTC price in USD.", EffectRunner(mode="record"),
        lambda q: [{"title": "V", "snippet": "Bitcoin 24-hour trading volume is 69797689537 tokens moved",
                    "url": "https://v/btc"}], "test")
    ans = repl._extract_answer(transcript) or ""
    # Target the price BINDING, not the honest evidence excerpt (which may quote the
    # raw source "volume is 69797689537 tokens"): the volume number must not ship
    # WEARING a USD price unit.
    assert "69797689537 USD" not in ans and "price is 69797689537" not in ans and "24 USD" not in ans, \
        f"OVER-FIRE GUARD: a volume figure must not BIND as a USD price: {ans!r}"
