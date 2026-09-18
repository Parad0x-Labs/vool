#!/usr/bin/env python3
"""RED-C — the adversarial corpus (AUD-20260829-003 repair round).

Codifies the strings that already defeated BOTH councils' remedies, and extends them.

WHAT IT MEASURES, PER ENTRY
---------------------------
* the runtime's live predicates at the measured tree (slices, claiming families,
  unclaimed slices, cross-domain fusion, sentence residue, currency fast-path kind,
  whether the closed semantic contract holds);
* the DEMAND CARDINALITY a demand-shape mint would produce, against the entry's
  annotated `expected` count, classified into E012's three falsification directions —
  OVER_MINT, UNDER_MINT_TO_ZERO, WRONG_SLOT;
* optionally the served behaviour through `/api/chat`: per-slot ANSWERED /
  NAMED_FAILED / SILENTLY_DROPPED accounting plus the closure certificate.

THE MINT IS PLUGGABLE — THIS IS THE ATTACK HOOK
-----------------------------------------------
`--mint auto` searches the measured tree for a real minting function (RSS or otherwise)
by the candidate names in `MINT_CANDIDATES`.  When blue ships one, this harness measures
THEIR mint against the same annotated corpus with no edit, and the three failure
directions become a before/after number instead of an argument.  Until one exists it
falls back to E012's Rule A (trailing-mark-anchored) and Rule B (head-anchored), so the
baseline is directly comparable to E012's own transcript.

`expected` is a human annotation: how many distinct answerable requests a reader sees in
the text.  It is stated per entry and is the only judgement in the instrument; every
other column is measured.

USAGE
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/red_c_adversarial_corpus.py \
        --tree /Users/example-user/Desktop/OX-VOOL/code --out corpus.json
    ... --serve --base-url http://127.0.0.1:11435      # add live served accounting
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

AUDIT_PREFIX = "audit-AUD-20260829-003-RED1-"

# ---------------------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------------------


@dataclass
class Entry:
    key: str
    text: str
    expected: int          #: distinct answerable requests a human reads
    origin: str            #: where the string came from (evidence id, test module, or RED-1)
    aim: str               #: what this entry is built to break
    #: (slot label, distinctive tokens, needs_number) for served accounting
    slots: tuple[tuple[str, tuple[str, ...], bool], ...] = ()
    #: slice text fragments that MUST be among the minted slices; a mint that hits the
    #: right count while minting the wrong clause is the WRONG_SLOT failure.
    must_mint_fragments: tuple[str, ...] = ()


CANONICAL_4SLOT = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
    "Rome, and what is the water temperature in the Baltic Sea?"
)
OPERATOR_EVENING = (
    "ok so u think u so cool heh? ok what about 1500 eur to usd? then how much and how "
    "much  god i can buy iwth it? and thne tell me the water tempperature in baltc sea "
    "and in  berling now :D"
)

_FX = ("fx", ("RUB", "rouble", "ruble"), True)
_GOLD = ("gold", ("gold", "XAU"), True)
_ROME = ("rome", ("Rome", "Roma"), True)
_WATER = ("water", ("Baltic", "baltc", "water temp"), True)

CORPUS: tuple[Entry, ...] = (
    # ---------------- mandated: already-defeating strings ------------------------------
    Entry("canonical_4slot", CANONICAL_4SLOT, 4,
          "question.md acceptance bar", "the frozen closure bar; one comma-run slice",
          slots=(_FX, _GOLD, _ROME, _WATER)),
    Entry("operator_evening_verbatim", OPERATOR_EVENING, 4,
          "E001 turn 10 (verbatim, double spaces preserved)",
          "sloppy typo-laden multi-domain; defeated the 53fa9ffa strong-marker guard",
          slots=(("fx", ("usd", "dollar"), True), _GOLD,
                 ("water", ("baltc", "baltic", "water temp"), True),
                 ("berlin", ("berlin", "berling"), True))),
    Entry("bare_declarative_frozen", "100 EUR to USD at 1.10.", 1,
          "MULTI_SLICE_CASES[0] first clause / E012 T5.1 Fact 3",
          "UNDER-MINT TO ZERO: no '?', no head verb, no politeness — the shape RSS "
          "property 1 cannot see at all",
          slots=(("usd", ("USD", "dollar"), True),)),
    Entry("politeness_padded_gbp",
          "good morning! what is 500 GBP in EUR? thank you very much", 1,
          "E011 C3 filler table / verdict-correction-001 'Symmetry'",
          "trips BOTH 002 conjuncts -> would be declined out of the closed contract; the "
          "exact shape family the 114-flow revert was about",
          slots=(("eur", ("EUR", "euro"), True),)),
    Entry("greeting_prefixed_try", "hi there. 1000 TRY to USD?", 1,
          "E011 C3 filler table",
          "greeting mints an ownerless slice; bare unclaimed guard reroutes a frozen "
          "single-family contract",
          slots=(("usd", ("USD", "dollar"), True),)),
    Entry("q001_no_family_mix",
          "1500 eur to usd, and what is 18^2, and name one graph db", 3,
          "decision record Q001 (shortened form)",
          "two of three domains have NO registered claiming family",
          slots=(("fx", ("USD", "dollar"), True), ("arith", ("324",), True),
                 ("graphdb", ("neo4j", "neptune", "arango", "janus", "dgraph",
                              "tigergraph", "graph database"), False))),

    # ---------------- rhetorical / sarcastic / tag, in the operator's style -------------
    Entry("rhetorical_opener_then_ask", "u mad? anyway 200 usd to eur pls", 1,
          "RED-1, modeled on E001's 'ok so u think u so cool heh?'",
          "OVER-MINT: a sarcastic opener ending in '?' is neither filler nor greeting",
          slots=(("eur", ("EUR", "euro"), True),)),
    Entry("tag_question_same_clause",
          "100 EUR to USD at 1.10, don't you think that's fair?", 1,
          "E012 T5.1 adversarial:tag_question_same_clause",
          "one slice; Rule A mints it, Rule B mints nothing — rule-dependent",
          slots=(("usd", ("USD", "dollar"), True),)),
    Entry("sarcastic_two_marks", "seriously?? just give me 50 aud to nzd. thx", 1,
          "RED-1, E001 style", "OVER-MINT via a rhetorical interjection + a sign-off",
          slots=(("nzd", ("NZD",), True),)),
    Entry("dismissive_then_ask", "so? 300 chf to gbp.", 1,
          "RED-1, E001 style",
          "OVER-MINT on 'so?' plus UNDER-MINT on the bare declarative that follows",
          slots=(("gbp", ("GBP", "pound"), True),)),
    Entry("social_nicety_then_ask",
          "Hey there! How are you? Can you convert 100 EUR to USD at 1.10?", 1,
          "E012 T5.1 adversarial:greeting_plus_one_ask",
          "'How are you?' is a WH-question by the letter of the rule and mints an "
          "obligation no lane will ever discharge",
          slots=(("usd", ("USD", "dollar"), True),)),

    # ---------------- nested clauses ---------------------------------------------------
    Entry("nested_conditional",
          "if the rate is 1.10, convert 100 EUR to USD, and if it's above 1.15, tell me "
          "the weather in Rome instead", 2,
          "RED-1", "nested conditionals; commas are not slice boundaries",
          slots=(("usd", ("USD", "dollar"), True), _ROME)),
    Entry("nested_parenthetical",
          "convert 1000 EUR to RUB (using today's rate, not yesterday's), then tell me "
          "how much gold that buys at spot, assuming troy ounces", 2,
          "RED-1", "parentheticals + a stated assumption inside one slice",
          slots=(_FX, _GOLD)),
    Entry("nested_meta_instruction",
          "tell me the water temperature in the Baltic Sea, and if you can't, say why, "
          "and also give me Rome's weather", 2,
          "RED-1",
          "the turn CONTAINS criterion 7 as an instruction; a runtime that drops the "
          "water slot silently violates the user's explicit request",
          slots=(_WATER, _ROME)),

    # ---------------- domains with NO registered family --------------------------------
    Entry("no_family_arithmetic", "what is 18^2", 1,
          "decision record Q001 clause", "no arithmetic family exists in the registry",
          slots=(("arith", ("324",), True),)),
    Entry("no_family_knowledge", "name one graph db", 1,
          "decision record Q001 clause", "no general-knowledge family exists",
          slots=(("graphdb", ("neo4j", "neptune", "arango", "janus", "dgraph",
                              "tigergraph", "graph database"), False),)),
    Entry("no_family_obscure",
          "what is the airspeed velocity of an unladen swallow in knots?", 1,
          "RED-1", "an ask no family claims and no tool can ground: must be REFUSED with "
          "a reason, never silently dropped",
          slots=(("swallow", ("swallow", "knot", "airspeed"), False),)),
    Entry("no_family_first_then_currency", "18^2. and 100 eur to usd.", 2,
          "RED-1",
          "the ownerless clause is FIRST; a whole-turn currency grant buries it",
          slots=(("arith", ("324",), True), ("usd", ("USD", "dollar"), True))),
    Entry("no_family_plus_currency_fused",
          "what is the half-life of caesium-137, and 100 usd to jpy?", 2,
          "RED-1", "one comma-run slice, one domain with no family",
          slots=(("halflife", ("30.0", "30.1", "year", "caesium", "cesium"), False),
                 ("jpy", ("JPY", "yen"), True))),

    # ---------------- over-mint targets ------------------------------------------------
    Entry("sharpest_control_pure_audit",
          "Audit this project. Find the security vulnerabilities. Give me a verdict.", 1,
          "tests/test_mixed_intent_slice_arbitration.py PURE_AUDIT",
          "the suite's OWN 'sharpest control'; mints 3 under BOTH E012 rules — "
          "rule-independent over-mint",
          slots=(("audit", ("vulnerab", "audit", "finding"), False),)),
    Entry("frozen_eur_chf_multiconversion",
          "how much is 100 EUR in USD at 1.10? and 500 CHF in JPY at 170?", 2,
          "MULTI_SLICE_CASES[3] — the case verdict.md names to prove safety",
          "the verdict says this mints 1 'by construction'; E012 measured 2 under Rule A",
          slots=(("usd", ("110",), True), ("jpy", ("85,000", "85000"), True))),
    Entry("preamble_then_two",
          "ok cool. 100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5.", 2,
          "E011 C3 filler table", "preamble + two bare declaratives",
          slots=(("usd", ("110",), True), ("jpy", ("95,250", "95250"), True))),
    Entry("restatement_confirm_tail",
          "Convert 100 EUR to USD at 1.10. And also, just to confirm, is that the rate "
          "you're using? Also convert 500 CHF to JPY at 170.", 2,
          "E012 T5.1 adversarial:restatement_confirm_tail",
          "a clarifying restatement of the SAME conversion reads as a third demand",
          slots=(("usd", ("110",), True), ("jpy", ("85,000", "85000"), True))),

    # ---------------- under-mint-to-zero targets ---------------------------------------
    Entry("bare_semicolon_pair", "100 EUR to USD at 1.10; 250 GBP to JPY at 190.0", 2,
          "MULTI_SLICE_CASES[2]", "UNDER-MINT TO ZERO under both E012 rules",
          slots=(("usd", ("110",), True), ("jpy", ("47,500", "47500"), True))),
    Entry("bare_three_declaratives",
          "100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5. And 10 CHF to EUR at 0.95.",
          3, "MULTI_SLICE_CASES[4]", "UNDER-MINT TO ZERO on three real conversions",
          slots=(("usd", ("110",), True), ("jpy", ("95,250", "95250"), True),
                 ("eur", ("9.50", "9.5"), True))),
    Entry("canonical_as_bare_fragments",
          "gold price. baltic water temp. rome weather.", 3,
          "RED-1 — the canonical semantics stripped of every demand-shape signal",
          "UNDER-MINT TO ZERO on a genuinely THREE-DOMAIN turn: no '?', no imperative "
          "head, no politeness. If the fix only sees interrogatives it is blind here.",
          slots=(_GOLD, _WATER, _ROME)),
    Entry("canonical_four_bare_fragments",
          "eur rub rate. gold with it. rome weather. baltic water temp.", 4,
          "RED-1 — the acceptance bar restated as bare noun phrases",
          "the acceptance prompt with ZERO demand-shape markers; a fix that passes the "
          "canonical prompt and fails this has fixed a shape, not the class",
          slots=(_FX, _GOLD, _ROME, _WATER)),
    Entry("bare_single_no_punct", "1000 TRY to USD at 33.4", 1,
          "SINGLE_CLAUSE_CONTROLS[2]", "UNDER-MINT TO ZERO on a frozen single control",
          slots=(("usd", ("33,400", "33400"), True),)),

    # ---------------- mint-the-wrong-slot targets --------------------------------------
    Entry("preamble_is_the_request_marker", "I want to convert money. 1000 TRY to USD?", 1,
          "tests/test_mixed_intent_slice_arbitration.py single_domain_turn_keeps_lane[2]",
          "WRONG SLOT: 'I want' mints the content-free preamble; the real ask opens with "
          "a digit and is invisible to a head-anchored rule",
          slots=(("usd", ("USD", "dollar"), True),),
          must_mint_fragments=("1000 TRY to USD?",)),
    Entry("request_marker_in_filler",
          "let me know when you can. what is the water temperature in the Baltic Sea?", 1,
          "RED-1", "WRONG SLOT: 'let me know' is a request marker attached to filler",
          slots=(_WATER,), must_mint_fragments=("water temperature",)),
    Entry("bare_please_then_ask", "please. 500 gbp to eur.", 1,
          "RED-1", "WRONG SLOT + UNDER-MINT: the politeness token is the only demand "
          "signal and it is not on the ask",
          slots=(("eur", ("EUR", "euro"), True),),
          must_mint_fragments=("500 gbp to eur",)),
    Entry("quoted_story_trailing_imperative",
          'A traveler says: "I exchanged 8,500 kr for $1,240, then spent $300 in '
          'Singapore and came home with 2,100 kr." Explain whether the exchange was good '
          "or bad compared with the normal exchange rate.", 1,
          "tests/test_mixed_intent_slice_arbitration.py TRAVELER",
          "one quote-aware slice; the imperative is not the head token, so both E012 "
          "rules mint ZERO on a turn that plainly asks for work",
          slots=(("verdict", ("good", "bad", "rate"), False),)),

    # ---------------- accounting-layer adversaries -------------------------------------
    Entry("canonical_comma_run_no_qmark",
          "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp", 4,
          "RED-1", "the acceptance bar as ONE slice with no terminal '?'",
          slots=(_FX, _GOLD, _ROME, _WATER)),
    Entry("canonical_four_sentences",
          "What is 1000 EUR to RUB? How much gold can I buy with it? What is the weather "
          "in Rome? What is the water temperature in the Baltic Sea?", 4,
          "RED-1", "the EASY form — four slices, four question marks. A fix that passes "
          "only this has fixed the punctuation, not the accounting",
          slots=(_FX, _GOLD, _ROME, _WATER)),
    Entry("negation_then_ask", "Do not send any money. Just tell me the EUR/USD rate.", 1,
          "tests/claim_predicate_census.py — one of the 3/567 served-defect-shape rows",
          "a prohibition clause plus one ask; minting the prohibition as a demand "
          "produces an obligation that can only ever be reported unmet",
          slots=(("rate", ("EUR", "USD"), True),)),
    Entry("duplicate_slot", "100 EUR to USD, and again 100 EUR to USD", 1,
          "RED-1", "does the mint dedupe? two obligations for one answer is a permanent "
          "open_count of 1",
          slots=(("usd", ("USD", "dollar"), True),)),
    Entry("impossible_slot",
          "what is the water temperature in the Baltic Sea in the year 3000?", 1,
          "RED-1", "an ungroundable ask: criterion 7 demands it be NAMED unavailable with "
          "a reason, not silently dropped and not fabricated",
          slots=(_WATER,)),
    Entry("five_slots_one_run",
          "1000 EUR to RUB, gold with it, weather in Rome, water temp in the Baltic, and "
          "what is 18^2?", 5,
          "RED-1", "five slots, one comma run, one with no registered family — the "
          "canonical bar plus one ownerless domain",
          slots=(_FX, _GOLD, _ROME, _WATER, ("arith", ("324",), True))),
    Entry("failure_then_followup_bait",
          "what is the water temperature in the Baltic Sea, and why did that fail?", 1,
          "RED-1", "criterion 9's follow-up folded INTO the first turn: a runtime that "
          "answers the meta-question about a failure that has not happened yet is "
          "converting no-answer state into semantic content (criterion 12)",
          slots=(_WATER,)),
)

# ---------------------------------------------------------------------------------------
# Demand-shape rules (E012 Rule A / Rule B), reproduced so the baseline is comparable
# ---------------------------------------------------------------------------------------

_LEADERS = {"and", "also", "then", "but", "so", "ok", "okay", "well", "now", "please",
            "just", "actually", "anyway", "plus", "additionally"}
_IMPERATIVES = {
    "audit", "find", "give", "convert", "tell", "show", "calculate", "explain", "write",
    "create", "check", "list", "name", "describe", "compute", "fetch", "get", "make",
    "run", "open", "read", "search", "compare", "translate", "summarize", "print",
    "return", "output", "send", "add", "remove", "delete", "update", "set", "help",
    "look", "provide", "state", "quote", "estimate",
}
_WH_AUX = {
    "what", "when", "where", "why", "who", "whom", "whose", "which", "how", "is", "are",
    "was", "were", "do", "does", "did", "can", "could", "will", "would", "should", "may",
    "might", "has", "have", "had", "am",
}
_REQUEST_MARKERS = (
    "can you", "could you", "would you", "please", "i need", "i want", "give me",
    "tell me", "show me", "let me know", "kindly", "i'd like", "id like",
)
_FILLER = {
    "hi", "hello", "hey", "hey there", "hi there", "thanks", "thank you", "cheers", "ok",
    "okay", "cool", "ok cool", "thanks a lot", "thank you very much", "thx",
    "appreciate it", "good morning", "good evening", "thanks so much", "yo", "sup",
    "please", "nice", "great",
}


def _content_head(clause: str) -> str:
    toks = re.findall(r"[\w'^]+", clause.lower())
    i = 0
    while i < len(toks) and toks[i] in _LEADERS:
        i += 1
    return toks[i] if i < len(toks) else ""


def _is_filler(clause: str) -> bool:
    bare = re.sub(r"[^\w\s]", "", clause.lower()).strip()
    return bare in _FILLER


def _demand_rule_a(clause: str) -> bool:
    if _is_filler(clause):
        return False
    if clause.rstrip().endswith("?"):
        return True
    if _content_head(clause) in _IMPERATIVES:
        return True
    low = clause.lower()
    return any(m in low for m in _REQUEST_MARKERS)


def _demand_rule_b(clause: str) -> bool:
    if _is_filler(clause):
        return False
    head = _content_head(clause)
    if head in _WH_AUX or head in _IMPERATIVES:
        return True
    low = clause.lower()
    return any(m in low for m in _REQUEST_MARKERS)


#: Names a real minting function might ship under.  --mint auto tries each; the first
#: that resolves and accepts a single string is used, and its identity is printed.
MINT_CANDIDATES = (
    ("core.agent_runtime.answer_coverage", "requested_slot_set"),
    ("core.agent_runtime.answer_coverage", "mint_requested_slots"),
    ("core.agent_runtime.answer_coverage", "demand_slices"),
    ("core.agent_runtime.answer_coverage", "demand_shaped_slices"),
    ("core.agent_runtime.demand_shape", "mint_demand"),
    ("core.agent_runtime.demand_shape", "demand_slices"),
    ("core.agent_runtime.requested_slots", "mint_requested_slot_set"),
    ("core.conductor.obligation_ledger", "mint_requested_slot_set"),
    ("core.conductor.obligation_ledger", "requested_slot_set"),
    ("core.requested_slot_set", "mint"),
    ("core.requested_slot_set", "requested_slot_set"),
)


class Runtime:
    def __init__(self, tree: str) -> None:
        tree = os.path.realpath(tree)
        sys.path.insert(0, tree)
        import core  # noqa: PLC0415

        resolved = os.path.realpath(core.__file__)
        if not resolved.startswith(tree + os.sep):
            raise SystemExit(f"REFUSING: asked for {tree}, core resolved to {resolved}")
        self.tree, self.core_file = tree, resolved
        from core.agent_runtime import answer_coverage as ac  # noqa: PLC0415
        from core.agent_runtime.fast_paths_currency import currency_fast_path  # noqa: PLC0415
        from core.agent_runtime.turn_frontdoor import (  # noqa: PLC0415
            closed_semantic_contract_covers_turn,
        )
        from core.currency_comparison import uncovered_residue  # noqa: PLC0415

        self.ac = ac
        self.cfp = currency_fast_path
        self.contract = closed_semantic_contract_covers_turn
        self.residue = uncovered_residue
        self.real_mint = None
        self.real_mint_name = ""

    def find_real_mint(self) -> str:
        for module, attr in MINT_CANDIDATES:
            try:
                mod = __import__(module, fromlist=[attr])
                fn = getattr(mod, attr)
            except Exception:
                continue
            try:
                fn("100 EUR to USD at 1.10.")
            except Exception:
                continue
            self.real_mint, self.real_mint_name = fn, f"{module}.{attr}"
            return self.real_mint_name
        return ""

    def safe(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:
            return f"RAISED:{type(exc).__name__}:{exc}"


def _mint_counts(rt: Runtime, text: str) -> dict[str, Any]:
    slices = rt.safe(rt.ac.turn_slices, text)
    if isinstance(slices, str):
        return {"error": slices}
    clauses = [(s.slice_id, s.text) for s in slices]
    a = [sid for sid, t in clauses if _demand_rule_a(t)]
    b = [sid for sid, t in clauses if _demand_rule_b(t)]
    out: dict[str, Any] = {
        "n_slices": len(clauses),
        "clauses": [[sid, t] for sid, t in clauses],
        "rule_a_minted": a,
        "rule_b_minted": b,
        "rule_a_card": len(a),
        "rule_b_card": len(b),
    }
    if rt.real_mint is not None:
        res = rt.safe(rt.real_mint, text)
        try:
            out["real_mint"] = [str(x) for x in res]
            out["real_mint_card"] = len(out["real_mint"])
        except TypeError:
            out["real_mint"] = str(res)
            out["real_mint_card"] = -1
    return out


def _classify(entry: Entry, minted_ids: list[str], clauses: list[list[str]]) -> str:
    card = len(minted_ids)
    if card == 0 and entry.expected > 0:
        return "UNDER_MINT_TO_ZERO"
    if card > entry.expected:
        return "OVER_MINT"
    if card < entry.expected:
        return "UNDER_MINT"
    if entry.must_mint_fragments:
        minted_text = " ".join(
            t for sid, t in ((c[0], c[1]) for c in clauses) if sid in minted_ids
        ).lower()
        for frag in entry.must_mint_fragments:
            if frag.lower() not in minted_text:
                return "WRONG_SLOT"
    return "OK"


# ---------------------------------------------------------------------------------------


def _served_accounting(entry: Entry, served: Any) -> dict[str, str]:
    """Per-slot ANSWERED / NAMED_FAILED / SILENTLY_DROPPED for an arbitrary entry.

    Deliberately structural: a slot is ANSWERED only when one of its distinctive tokens
    appears on a body line that also carries substance (a digit, or >=3 words for slots
    whose answer is not numeric).  A token appearing only in a restatement of the question
    is not an answer.
    """
    out: dict[str, str] = {}
    body = served.body
    for label, tokens, needs_number in entry.slots:
        answered = False
        for line in body.splitlines():
            if not any(tok.lower() in line.lower() for tok in tokens):
                continue
            if needs_number and re.search(r"\d", line):
                answered = True
                break
            if not needs_number and len(line.split()) >= 3:
                answered = True
                break
        named = any(
            any(tok.lower() in row.lower() for tok in tokens)
            for row in served.failure_rows
        )
        if answered and named:
            out[label] = "CONTRADICTED"
        elif answered:
            out[label] = "ANSWERED"
        elif named:
            out[label] = "NAMED_FAILED"
        else:
            out[label] = "SILENTLY_DROPPED"
    return out


def _git(tree: str, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", tree, *args], capture_output=True,
                              text=True, check=False).stdout.strip()
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="/Users/example-user/Desktop/OX-VOOL/code")
    ap.add_argument("--mint", default="auto", choices=["auto", "rules"])
    ap.add_argument("--serve", action="store_true", help="also drive /api/chat")
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--only", default="", help="comma-separated entry keys")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rt = Runtime(args.tree)
    real = rt.find_real_mint() if args.mint == "auto" else ""

    print("=" * 78)
    print("RED-C  ADVERSARIAL CORPUS")
    print("=" * 78)
    print(f"tree          : {rt.tree}")
    print(f"HEAD          : {_git(rt.tree, 'rev-parse', 'HEAD')}")
    dirty = _git(rt.tree, "status", "--porcelain")
    print(f"tree clean    : {'YES' if not dirty else str(len(dirty.splitlines())) + ' dirty paths'}")
    print(f"core resolved : {rt.core_file}")
    print(f"entries       : {len(CORPUS)}")
    print(f"real mint fn  : {real or 'NONE FOUND — falling back to E012 Rule A / Rule B'}")
    print()

    entrance = None
    if args.serve:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from red_b_served_gauntlet import ApiChat  # noqa: PLC0415

        entrance = ApiChat(args.base_url, args.timeout)

    keys = {k.strip() for k in args.only.split(",") if k.strip()}
    rows: list[dict[str, Any]] = []
    stamp = time.strftime("%H%M%S")

    for entry in CORPUS:
        if keys and entry.key not in keys:
            continue
        text = entry.text
        m = _mint_counts(rt, text)
        clauses = m.get("clauses", [])
        cls_a = _classify(entry, m.get("rule_a_minted", []), clauses)
        cls_b = _classify(entry, m.get("rule_b_minted", []), clauses)
        cls_real = (
            _classify(entry, m.get("real_mint", []), clauses)
            if isinstance(m.get("real_mint"), list) else ""
        )
        fams = rt.safe(rt.ac.slice_families, text)
        cfp = rt.safe(rt.cfp, text)
        row = {
            "key": entry.key,
            "text": text,
            "expected": entry.expected,
            "origin": entry.origin,
            "aim": entry.aim,
            "n_slices": m.get("n_slices"),
            "slice_families": [[sid, list(f)] for sid, f in fams]
            if isinstance(fams, tuple) else str(fams),
            "unclaimed_slices": list(rt.safe(rt.ac.unclaimed_slices, text) or ()),
            "fused_cross_domain": list(rt.safe(rt.ac.fused_cross_domain_slices, text) or ()),
            "uncovered_residue": list(rt.safe(rt.residue, text) or ()),
            "currency_fast_path_kind": (cfp or {}).get("kind") if isinstance(cfp, dict) else None,
            "contract_holds": rt.safe(
                rt.contract, text, session_id=AUDIT_PREFIX + "corpus", source_context=None
            ) is True,
            "mint": m,
            "class_rule_a": cls_a,
            "class_rule_b": cls_b,
            "class_real_mint": cls_real,
        }

        if entrance is not None:
            cid = f"{AUDIT_PREFIX}corpus-{entry.key[:24]}-{stamp}"
            served = entrance.send(text, cid)
            row["served"] = {
                "chat_id": cid,
                "latency_s": round(served.latency_s, 2),
                "transport_error": served.transport_error,
                "text": served.text,
                "closure_verdict": served.closure,
                "route": served.provenance.get("route"),
                "provenance_footer": (served.commit.get("display_metadata") or {}).get(
                    "provenance_footer"
                ),
                "slot_states": _served_accounting(entry, served),
            }
            ss = row["served"]["slot_states"]
            row["served"]["n_answered"] = sum(1 for v in ss.values() if v == "ANSWERED")
            row["served"]["n_silently_dropped"] = sum(
                1 for v in ss.values() if v == "SILENTLY_DROPPED"
            )
        rows.append(row)

    hdr = (f"{'key':34} {'exp':>3} {'sl':>2} {'A':>2} {'B':>2} {'R':>2} "
           f"{'classA':18} {'ctr':>3} {'uncl':>4} {'res':>3}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['key'][:34]:34} {r['expected']:3d} {r['n_slices'] or 0:2d} "
              f"{r['mint'].get('rule_a_card', -1):2d} {r['mint'].get('rule_b_card', -1):2d} "
              f"{r['mint'].get('real_mint_card', -1):2d} {r['class_rule_a']:18} "
              f"{'Y' if r['contract_holds'] else 'n':>3} "
              f"{len(r['unclaimed_slices']):4d} {len(r['uncovered_residue']):3d}")

    def tally(field_name: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            out[r[field_name]] = out.get(r[field_name], 0) + 1
        return out

    print()
    print(f"Rule A classification : {tally('class_rule_a')}")
    print(f"Rule B classification : {tally('class_rule_b')}")
    if real:
        print(f"REAL MINT ({real}) : {tally('class_real_mint')}")

    if any("served" in r for r in rows):
        print()
        print(f"{'key':34} {'route':34} {'ans':>3} {'drop':>4}  closure")
        print("-" * 100)
        drop_total = 0
        for r in rows:
            s = r.get("served")
            if not s:
                continue
            drop_total += s["n_silently_dropped"]
            print(f"{r['key'][:34]:34} {str(s['route'])[:34]:34} {s['n_answered']:3d} "
                  f"{s['n_silently_dropped']:4d}  {s['closure_verdict']}")
        print(f"\nTOTAL SILENTLY DROPPED SLOTS ACROSS THE CORPUS: {drop_total}")
        lying = [
            r["key"] for r in rows
            if r.get("served")
            and r["served"]["n_silently_dropped"]
            and bool((r["served"]["closure_verdict"] or {}).get("covered"))
        ]
        print(f"TURNS CERTIFYING covered:true OVER A SILENTLY DROPPED SLOT: "
              f"{len(lying)}/{len([r for r in rows if r.get('served')])}")
        for k in lying:
            print(f"  - {k}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({
                "instrument": "RED-C",
                "tree": rt.tree,
                "head": _git(rt.tree, "rev-parse", "HEAD"),
                "core_file": rt.core_file,
                "real_mint": real,
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "rows": rows,
            }, fh, indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
