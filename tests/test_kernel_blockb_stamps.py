"""BLOCK-B seam: effect stamps — a veto tears the capability for the WHOLE turn.

Signed block c31a...bd4b: stamps compile before dispatch from the full message
including negative constraints; explicit vetoes tear and NEVER re-arm within the
turn (lane repair, fallback, continuation all consult them); a quoted or example
veto tears nothing (G3-WB, N2); visibility is out-of-band only.
"""
from core.kernel import repl
from core.kernel.effects import EffectRunner


def test_veto_phrases_tear_web():
    for q in ("No web, no search, no tools for this turn.",
              "Do not browse or call any live-data tool.",
              "For this whole turn, web and tools are forbidden.",
              "Offline mode only: calculate.",
              "Answer from general knowledge only."):
        assert repl._compile_stamps(q)["web"] is False, q


def test_quoted_or_example_veto_tears_nothing():
    # G3-WB: an example frame does not apply; quoted data is not a control act
    q1 = 'Example constraint (do not apply): "NO WEB OR TOOLS." What is the capital of Kenya right now?'
    assert repl._compile_stamps(q1)["web"] is True
    q2 = 'Summarise the sentence "do not browse the internet" for me.'
    assert repl._compile_stamps(q2)["web"] is True


def test_torn_web_converts_lookup_to_knowledge_zero_fetches(monkeypatch):
    calls = []
    def fetch_spy(q):
        calls.append(q)
        return []
    def judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain semantic versioning",
                    "lane": "web_lookup", "query": "semantic versioning", "format": "",
                    "source_offset": 0, "resolves_carryover": ""}]}
        raise AssertionError(effect_id)
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "No web, no search, no tools. From general knowledge only, explain semantic versioning.",
        EffectRunner(mode="record"), fetch_spy, "test")
    assert calls == [], "a torn web stamp must produce ZERO fetches"
    assert "stamp: web torn" in transcript


def test_repair_cannot_rearm_torn_web(monkeypatch):
    """G3-LD: unstated-operand arithmetic under NO WEB refuses with a rendered
    reason instead of converting to a lookup."""
    calls = []
    def fetch_spy(q):
        calls.append(q)
        return []
    def judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "convert 10000 RUB to EUR",
                    "lane": "arithmetic", "query": "10000 / 94.54", "format": "",
                    "source_offset": 0, "resolves_carryover": ""}]}
        raise AssertionError(effect_id)
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "No web. Convert 10000 RUB to EUR.", EffectRunner(mode="record"), fetch_spy, "test")
    assert calls == []
    answer = repl._extract_answer(transcript)
    assert answer is not None and "web is disabled by your instruction" in answer


def test_stamps_ride_the_journal_not_answer_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-st")
    import json as _json

    from core.kernel.effects import EffectJournal
    tape = EffectJournal()
    tape._turn_stamps = {"web": False, "machine": True, "torn_by": "No web"}
    repl._journal_turn("q", "COMMIT\n\x01fine\x02", tape, mode="record", turn_index=1)
    row = _json.loads((tmp_path / "j.jsonl").read_text())
    assert row["stamps"] == {"web": False, "machine": True, "torn_by": "No web"}
    assert "torn" not in row["answer"]           # never in answer bytes
