"""ROUND-001 pin — a plain-text compose call must not be JSON-constrained on the wire.

Frozen root (council/round-001/FIX_PLAN.md): `_ollama_chat` hard-coded `"format": "json"` into
EVERY request body, including the claimless-compose fallback whose system prompt reads "No JSON,
no wrapper, no commentary — output the piece itself only". Ollama's json mode constrains
generation to JSON grammar, so that caller's contract was unsatisfiable. A byte-exact turn shipped
`{"bytes": "VECTOR-6842"}` instead of `VECTOR-6842`.

Probe (council/round-001/R2/PROBE1_RAW.json) — same prompt, same model, temperature 0, only the
wire differing: with `"format": "json"` -> `{ }`; without -> `VECTOR-6842`.

SABOTAGE SEAM: revert `json_mode=False` at either compose call site in repl.py (leaving the
parameter defined) and `test_compose_fallback_does_not_force_json_on_the_wire` goes RED naming the
transport. These tests inspect the request BODY rather than a live model, so they are
deterministic and name the cause instead of a downstream symptom.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _bodies(monkeypatch):
    """Capture every request body _ollama_chat would put on the wire."""
    seen: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"message": {"content": "PIECE"}}).encode()

    def fake_urlopen(req, *a, **kw):
        seen.append(json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr(repl.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_json_callers_still_get_format_json(monkeypatch):
    """Negative control: the default is unchanged, so every JSON stage is untouched."""
    seen = _bodies(monkeypatch)
    repl._ollama_chat("sys", "user")
    assert seen[0].get("format") == "json"


def test_plaintext_caller_gets_no_format_field(monkeypatch):
    seen = _bodies(monkeypatch)
    repl._ollama_chat("sys", "user", json_mode=False)
    assert "format" not in seen[0], (
        f"a plain-text call must not be JSON-constrained on the wire; body was {seen[0]!r}")


def test_compose_fallback_does_not_force_json_on_the_wire(monkeypatch):
    """The load-bearing pin: drive run_turn to the claimless-compose fallback and assert the
    wire agreed with the plain-text system prompt. Reverting json_mode=False turns this red."""
    seen = _bodies(monkeypatch)

    def judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "write the piece", "lane": "compose",
                                     "query": "", "format": "", "source_offset": 0,
                                     "resolves_carryover": ""}]}
        if effect_id == "model.synthesize":
            return {"claims": []}          # claimless -> the fallback fires
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}

    monkeypatch.setattr(repl, "_model_json", judge)
    repl.run_turn("write me a short piece", EffectRunner(mode="record"), None, None)

    compose_bodies = [b for b in seen if any(
        "output the piece itself only" in str(m.get("content", ""))
        for m in b.get("messages", []))]
    assert compose_bodies, "the claimless-compose fallback never reached the wire"
    for body in compose_bodies:
        assert "format" not in body, (
            "the compose fallback declares 'No JSON, no wrapper' in its system prompt but the "
            f"wire still constrained it to JSON grammar: {body!r}")
