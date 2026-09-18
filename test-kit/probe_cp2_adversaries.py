"""CP2 probe — actual HEAD behavior on topic/context adversaries.

Run: ~/vool/vool-engine/.venv/bin/python -m pytest test-kit/probe_cp2_adversaries.py -q -s
Prints one line per probe: verdict INHERIT:<text> or DECLINE, plus plan operations, so the
audit records observed behavior before tests pin it. Not a regression test: an instrument.
"""
from __future__ import annotations

from core.execution_requirements import requirements_for
from core.live_data_continuation import continuation_inherits_live_data

from kit_lib import context, gold_thread, plan_operations, sid, weather_thread

GOLD = "what is gold price now?"
GOLD_ANS = "Gold: USD 4,476.60 per troy ounce. Source: [Yahoo Finance](https://finance.yahoo.com/quote/GC=F)."


def _report(label: str, text: str, source: dict) -> None:
    inherited = continuation_inherits_live_data(text, source_context=source)
    mode = requirements_for(text, source_context=source).answer_mode
    ops = plan_operations(text, source)
    verdict = f"INHERIT:{inherited!r}" if inherited else "DECLINE"
    print(f"[{label}] {text!r} -> {verdict} | mode={mode} | plan={ops}")


def test_probe_all_families():
    # --- Family 1: cross-domain nouns that _SCAFFOLDING treats as bare-nudge filler
    w = weather_thread("w1", ["Kaunas"], "Get weather for Kaunas.")
    _report("F1-weather→price", "and the price?", context(w, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", "and the price?")))
    _report("F1-weather→news", "what about the news?", context(w, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", "what about the news?")))
    g = gold_thread("g1")
    _report("F1-gold→weather", "and the weather there?", context(g, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "and the weather there?")))
    _report("F1-gold→temperature", "and the temperature?", context(g, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "and the temperature?")))

    # --- Family 2: market rebind on any alias mention in a short residue
    for label, text in [
        ("F2-quantity", "how much gold is there?"),
        ("F2-history", "tell me the history of gold"),
        ("F2-mining", "how is gold mined?"),
        ("F2-poem", "write a poem about gold"),
        ("F2-correction-inverted", "not gold, silver"),
        ("F2-correction-clean", "no, i meant silver"),
    ]:
        gg = gold_thread("g2")
        _report(label, text, context(gg, ("user", GOLD), ("assistant", GOLD_ANS), ("user", text)))

    # --- Family 3: weather rebind on Title-case residue
    for label, text in [
        ("F3-write-winter", "Write About Winter"),
        ("F3-code-python", "Code About Python Now"),
    ]:
        ww = weather_thread("w3", ["Kaunas"], "Get weather for Kaunas.")
        _report(label, text, context(ww, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", text)))

    # --- Family 4: aggregate -er/-est shape overbreadth
    wt = weather_thread("w4", ["Kaunas", "Tallinn"], "Compare weather in Kaunas and Tallinn.")
    for text in ["which one has a river?", "which one has water?", "which one is warmer?", "which one is warmest?"]:
        _report("F4-aggregate", text, context(wt, ("user", "Compare weather in Kaunas and Tallinn."), ("assistant", "Kaunas 18 C, Tallinn 12 C."), ("user", text)))

    # --- Family 5: clarification near-slot without verbatim restatement
    g5 = gold_thread("g5")
    _report("F5-near-slot", "and golf, them two, was that what i asked about?",
            context(g5, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "and golf, them two, was that what i asked about?")))
    # anagram arm
    g5b = gold_thread("g5b")
    _report("F5-anagram-more", "and More, them two, right?", context(g5b, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "and More, them two, right?")))

    # --- Positive controls (must keep inheriting/rebinding correctly)
    gp = gold_thread("gp")
    _report("POS-nudge", "and now?", context(gp, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "and now?")))
    gp2 = gold_thread("gp2")
    _report("POS-rebind", "what about silver?", context(gp2, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "what about silver?")))
    gp3 = gold_thread("gp3")
    _report("POS-compare", "compare gold vs silver price", context(gp3, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "compare gold vs silver price")))
    wt2 = weather_thread("wt2", ["Kaunas", "Tallinn"], "Compare weather in Kaunas and Tallinn.")
    _report("POS-typo-clarify", "i asked about kauans and talling weather, last question was follow up for them two right?",
            context(wt2, ("user", "Compare weather in Kaunas and Tallinn."), ("assistant", "Kaunas 18 C, Tallinn 12 C."),
                    ("user", "i asked about kauans and talling weather, last question was follow up for them two right?")))

    # --- Explicit return to an older topic (self-classifying, no continuation needed)
    gr = gold_thread("gr")
    _report("RET-explicit", "back to gold, what is the price?",
            context(gr, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "what about the weather in Rome?"), ("assistant", "Rome: 25 C."), ("user", "back to gold, what is the price?")))

    # --- Quoted prior question while asking something new
    gq = gold_thread("gq")
    _report("QUOTED-new-ask", 'you asked "what is gold price now?" - now do the same for silver vs platinum',
            context(gq, ("user", GOLD), ("assistant", GOLD_ANS), ("user", 'you asked "what is gold price now?" - now do the same for silver vs platinum')))

    # --- Multilingual nudges after an English live-data turn
    for label, text in [("ML-ru", "а сейчас?"), ("ML-es", "y ahora?"), ("ML-de", "und jetzt?"), ("ML-lt", "o dabar?")]:
        gm = gold_thread("gm")
        _report(label, text, context(gm, ("user", GOLD), ("assistant", GOLD_ANS), ("user", text)))

    # --- Weather→code, code→writing topic changes (served families named by the goal)
    wc = weather_thread("wc", ["Kaunas"], "Get weather for Kaunas.")
    _report("TOP-weather→code", "write me a python function that sorts a list",
            context(wc, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", "write me a python function that sorts a list")))
    gc = gold_thread("gc2")
    _report("TOP-gold→writing", "help me write a short poem about the sea",
            context(gc, ("user", GOLD), ("assistant", GOLD_ANS), ("user", "help me write a short poem about the sea")))
