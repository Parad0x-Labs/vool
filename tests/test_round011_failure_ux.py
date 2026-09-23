"""ROUND-011 FINAL UX CLOSURE pins — user-facing exact_words failure UX.

Frozen UX law (council/round-011/research/FINAL-CLOSURE-RECORD.md + operator mandate):
- INTERNAL TRUTH stays FORMAT_UNSATISFIED (settle reason, transcript, receipts, tests).
- A normal conversational user gets a short useful message naming the ACTUAL target N,
  plus the CLOSEST MODEL-AUTHORED attempt — never raw failure garbage.
- Closest-attempt selection is purely mechanical: abs(candidate_word_count - N),
  tie -> earliest original attempt. No inventing, deleting, reordering, merging,
  paraphrasing, or semantic ranking. The displayed candidate is verbatim.
- Cloud wording only when cloud is allowed/configured and not user-prohibited; the
  offline variant says "Try another model". Never recommend cloud when prohibited.
- No usable candidate -> short message only. Never fabricate.
- STRICT-OUTPUT EXCEPTION: when the ask demands bare output (ONLY the answer, nothing
  else, no explanation, exact/byte-exact), ZERO explanatory decoration enters the
  committed answer bytes; the failed candidate stays uncommitted; the human-readable
  explanation lives in the transcript/receipts (the typed failure surface).
- The +1 repair and the >=2 bounded-retry/fail-closed behavior are unchanged.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(rows, claim_seq):
    """claim_seq: list of claim-text lists, one per synth round (cycled if short)."""
    rounds = {"i": 0}

    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": rows}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            texts = claim_seq[min(rounds["i"], len(claim_seq) - 1)]
            rounds["i"] += 1
            return {"claims": [{"obligation_id": "ob1", "text": t, "type": "unverified"}
                               for t in texts]}
        raise AssertionError(effect_id)
    return fake


def _row(desc, fmt=""):
    return {"description": desc, "lane": "knowledge", "query": "", "format": fmt,
            "source_offset": 0, "resolves_carryover": ""}


def _turn(monkeypatch, question, rows, claim_seq, n, ttl=2, arbiter=None):
    if arbiter is not None:
        monkeypatch.setattr(repl, "_ARBITER_MODEL", arbiter)
    monkeypatch.setattr(repl, "_model_json", _judge(rows, claim_seq))
    facts = {repl._LEDGER_KEY: json.dumps([{"kind": "exact_words", "arg": str(n), "ttl": ttl}])}
    tr, journal, _l, facts_out = repl.run_turn(
        question, EffectRunner(mode="record"), None, None, session_facts=facts)
    return tr, repl._extract_answer(tr) or "", facts_out


MSG = "couldn\u2019t meet the exact"


# ===================================================== 1. internal truth preserved
def test_1_internal_failure_remains_FORMAT_UNSATISFIED(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7)
    assert "FORMAT_UNSATISFIED" in tr, "receipts/telemetry must keep the exact code"
    assert "best model effort 6 of 7 words" in tr


# ============================== 2. conversational failure: no raw garbage in the answer
def test_2_conversational_failure_is_friendly_not_raw(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7)
    assert MSG in ans and "7-word requirement" in ans
    assert "Cannot answer this turn" not in ans, "raw failure line must not face the user"
    assert "FAILED" not in ans and "FORMAT_UNSATISFIED" not in ans, (
        f"raw failure garbage in user-facing answer: {ans!r}")


# ===================================================== 3. actual target N appears
def test_3_actual_target_N_appears(monkeypatch):
    for n in (4, 6, 9):
        tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                           [_row("explain mirror fog")],
                           [["silver fogs"]], n)   # 2 words: always short of N>=4
        assert f"exact {n}-word requirement" in ans, f"wrong N for target {n}: {ans!r}"


# ================== 4/5/6/7. closest-attempt law (mechanical, verbatim, no ranking)
def test_4_closest_attempt_is_min_count_distance(monkeypatch):
    # candidates: 4 words (distance 3) and 6 words (distance 1) under N=7 -> the 6-word
    # one must be shown EVEN THOUGH the 4-word one is semantically nicer.
    cands = ["water vapor condenses",          # 3 words, distance 4
             "warm moist air condenses on glass"]  # 6 words, distance 1
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")], [cands], 7)
    assert "warm moist air condenses on glass" in ans
    assert "water vapor condenses\n" not in ans  # the farther candidate is not shown


def test_5_tie_selects_earliest_attempt(monkeypatch):
    # both candidates 5 words under N=7 (equal distance) -> earliest (first) wins
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")],
                       [["first five word answer here now",          # attempt 1
                         "second five word answer here now"]], 7)    # attempt 2
    assert "first five word answer here now" in ans
    assert "second five word answer here now" not in ans


def test_6_displayed_candidate_is_model_authored_and_unchanged(monkeypatch):
    cands = ["wood absorbs moisture swelling slowly"]
    tr, ans, _ = _turn(monkeypatch, "Why do doors swell?",
                       [_row("explain door swelling")], [cands], 7)
    # verbatim model text appears in the fallback; nothing added/edited/reordered
    assert "wood absorbs moisture swelling slowly" in ans
    shown = ans.split("\n")[-1]
    assert shown == "wood absorbs moisture swelling slowly", (
        f"closest attempt must be displayed verbatim, got: {shown!r}")


def test_7_no_semantic_ranking_only_count_distance(monkeypatch):
    # a later, semantically richer candidate at the SAME distance as an earlier bland
    # one must NOT displace it (no semantic ranking): earliest-at-equal-distance wins.
    tr, ans, _ = _turn(monkeypatch, "Why do doors swell?",
                       [_row("explain door swelling")],
                       [["bland short answer here now",          # 5 words, distance 2
                         "wood swells in humid weather"]], 7)    # 5 words, distance 2
    assert "bland short answer here now" in ans
    assert "wood swells in humid weather" not in ans


# ============================== 8. offline / no-cloud policy never recommends cloud
def test_8a_offline_question_never_recommends_cloud(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up? Answer offline, no web.",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7,
                       arbiter="openrouter/qwen2.5:7b")   # cloud lane configured
    assert "Try another model" in ans
    assert "cloud" not in ans.lower(), "cloud recommended despite the user's offline ask"


def test_8b_local_only_config_uses_non_cloud_wording(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7,
                       arbiter="qwen2.5:7b")              # local-only arbiter
    assert "Try another model" in ans
    assert "cloud" not in ans.lower()


def test_8c_cloud_configured_and_allowed_uses_cloud_wording(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Why do mirrors fog up?",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7,
                       arbiter="openrouter/qwen2.5:7b")
    assert "Try a cloud model" in ans


# ===================================================== 9. no-candidate case invents nothing
def test_9_no_candidate_case_invents_nothing(monkeypatch):
    # zero claims produced at all -> no closest attempt, short message only
    def empty_judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [_row("explain mirror fog")]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": []}
        raise AssertionError(effect_id)
    monkeypatch.setattr(repl, "_model_json", empty_judge)
    facts = {repl._LEDGER_KEY: json.dumps([{"kind": "exact_words", "arg": "7", "ttl": 2}])}
    tr, _j, _l, _f = repl.run_turn("Why do mirrors fog up?", EffectRunner(mode="record"),
                                    None, None, session_facts=facts)
    ans = repl._extract_answer(tr) or ""
    assert MSG in ans and "7-word requirement" in ans
    assert "closest attempt" not in ans, "no candidate existed; nothing may be shown"
    assert "FORMAT_UNSATISFIED" not in ans  # friendly surface


# ============================== 10/11. strict-output exception (regression pins)
def test_10_strict_output_gets_zero_explanatory_decoration(monkeypatch):
    # "ONLY the answer, nothing else" -> the friendly fallback must NOT enter the bytes
    tr, ans, _ = _turn(monkeypatch,
                       "Why do mirrors fog up? Reply with only the answer, nothing else.",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7)
    assert MSG not in ans, f"explanatory fallback leaked into strict-output bytes: {ans!r}"
    assert "closest attempt" not in ans
    assert "silver glass reflects warm breath badly" not in ans, (
        "the failed candidate remains uncommitted under strict output")


def test_11_strict_output_explanation_uses_the_typed_failure_surface(monkeypatch):
    tr, ans, _ = _turn(monkeypatch,
                       "Why do mirrors fog up? Reply with only the answer, nothing else.",
                       [_row("explain mirror fog")],
                       [["silver glass reflects warm breath badly"]], 7)
    # the human-readable/internal explanation lives in transcript + receipts, not bytes
    assert "FORMAT_UNSATISFIED" in tr and "best model effort 6 of 7 words" in tr
    assert "COMMIT: committed" not in tr


# ===================================================== 12. receipts retain the code
def test_12_receipts_and_telemetry_retain_FORMAT_UNSATISFIED(monkeypatch):
    # the receipt line is emitted into the transcript record (telemetry surface) and the
    # settle/transcript keep the code for BOTH the friendly and strict surfaces
    for question in ("Why do mirrors fog up?",
                     "Why do mirrors fog up? Reply with only the answer, nothing else."):
        tr, ans, _ = _turn(monkeypatch, question,
                           [_row("explain mirror fog")],
                           [["silver glass reflects warm breath badly"]], 7)
        assert "FORMAT_UNSATISFIED" in tr


# ============================== 13. retained +1 behavior unchanged
def test_13_plus_one_repair_still_converges(monkeypatch):
    state = {"fixed": True}

    def judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [_row("explain mirror fog")]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            if "add exactly 1 more word" in user and state["fixed"]:
                state["fixed"] = False
                return {"claims": [{"obligation_id": "ob1",
                                    "text": "silver glass reflects warm breath quite badly",
                                    "type": "unverified"}]}  # exactly 7
            return {"claims": [{"obligation_id": "ob1",
                                "text": "silver glass reflects warm breath bad",
                                "type": "unverified"}]}  # 6 of 7
        raise AssertionError(effect_id)
    monkeypatch.setattr(repl, "_model_json", judge)
    facts = {repl._LEDGER_KEY: json.dumps([{"kind": "exact_words", "arg": "7", "ttl": 2}])}
    tr, _j, _l, _f = repl.run_turn("Why do mirrors fog up?", EffectRunner(mode="record"),
                                    None, None, session_facts=facts)
    ans = repl._extract_answer(tr) or ""
    assert "COMMIT: committed" in tr
    assert ans.strip() == "silver glass reflects warm breath quite badly"
    assert MSG not in ans


# ============ 14. >=2 bounded retry / fail-closed behavior unchanged (UX surface swap only)
def test_14_ge2_still_fails_closed_with_bounded_retry(monkeypatch):
    tr, ans, facts = _turn(monkeypatch, "Why do mirrors fog up?",
                           [_row("explain mirror fog")],
                           [["wood absorbs moisture swelling"]], 7, ttl=3)  # always 4 of 7
    assert "COMMIT: committed" not in tr
    assert tr.count("claim rejected: 4 words against an exactly-7-words order") == 2, (
        "the bounded informed retries must still run")
    assert "FORMAT_UNSATISFIED" in tr
    assert MSG in ans
    assert json.loads(facts[repl._LEDGER_KEY]) == [
        {"kind": "exact_words", "arg": "7", "ttl": 2}], "TTL decrements exactly once"


# ============ 15. historical fixtures: round-008/010 families remain green
def test_15_round008_and_round010_families_green():
    # The full families run in their own files; here assert the load-bearing historical
    # invariants that this UX change must not disturb, driven directly.
    import os
    import subprocess
    import sys
    from pathlib import Path

    repository = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(repository))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_round008_exact_words_fail_closed.py",
         "tests/test_round010_exact_words_plus_one_repair.py",
         "tests/test_round004_underrun_fallback.py",
         "-p", "no:randomly"],
        capture_output=True, text=True, cwd=repository,
        env=env)
    assert r.returncode == 0, f"historical families red:\n{r.stdout[-1500:]}"
