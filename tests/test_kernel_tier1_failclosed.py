"""TIER 1 (fail-closed extraction) — consensus block A, review-20260820-141451.

Codex amendment 1 / Grok condition 1: if extraction or contract parsing fails, the
turn's effect allowlist is EMPTY — it must never default to web_lookup or any other
effect lane. The old degraded path fell back to lane "web_lookup", turning an
extraction crash into a live search on the raw message (t15/S14: "extract smash ->
web"). The turn now declares a named FAILED terminal and runs no effect.
"""
from core.kernel import repl
from core.kernel.effects import EffectRunner


def test_extraction_failure_is_fail_closed_no_web_effect():
    """A degraded extraction must not reach the web. With a fetch spy AND a provider
    set (so the web_lookup lane WOULD fetch if it were taken), the spy is never called.
    Revert the fix (fallback lane -> web_lookup) and this assertion fails: the spy is
    called with the raw message — the exact crash-to-web the tier removes."""
    calls = []

    def fetch_spy(*a, **k):
        calls.append((a, k))
        return []

    transcript, _tape, _, _ = repl.run_turn(
        "Find the current FDA recall list for infant formula.",
        EffectRunner(mode="record"), fetch_spy, "brave")

    assert "extraction degraded" in transcript          # extraction did fail (no model)
    assert calls == [], "fail-closed: a degraded extraction must not reach the web"
    assert "fail-closed" in transcript                  # the named, no-effect terminal
    assert "no grounded lane" not in transcript         # NOT the web_lookup lane's message


def test_fail_closed_turn_runs_no_machine_effect_either():
    """Empty allowlist means NO effect lane — not web, not machine. The degraded turn
    records no tool execution on the tape beyond, at most, the failed declaration."""
    transcript, tape, _, _ = repl.run_turn(
        "Run pwd and report the working directory.",
        EffectRunner(mode="record"), None, None)
    assert "extraction degraded" in transcript
    assert "fail-closed" in transcript
    # No machine/tool op executed on the tape.
    kinds = [str(e.get("kind", e)) if isinstance(e, dict) else str(e) for e in tape.entries()]
    assert not any("machine" in k or "tool" in k or "web" in k for k in kinds), kinds
