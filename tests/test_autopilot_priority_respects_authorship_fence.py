"""The autopilot's pick must not ride ahead of an author the fence will refuse.

Measured 2026-11-03 (build 8af687ca, two served chats): the daily lane ranked the seeded
uncertified local default first every turn; the precall authorship fence refused it in 0.0 s;
the loop fell through to the certified model; nothing remembered, so every turn of every chat
paid the same two-lane failure. Ordering is not authority — the fence is. These tests pin the
ordering rule against that authority: an uncertified pick keeps its place in the list but never
ahead of a certified author; every pick the fence does not bind keeps today's exact behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.memory_first_router import _prioritize_autopilot_selection


def _manifest(provider_id: str) -> SimpleNamespace:
    return SimpleNamespace(provider_id=provider_id, model_name=provider_id)


def _patch(monkeypatch, certified: set[str]) -> None:
    """Point the ranking read at a fixed certification verdict table.

    The function under test imports the authority inside its body, so patching the authority
    module attribute reaches the call. The table mimics the real authority's shape: a local
    model reads its certification state, anything the certification question does not arise for
    (cloud lanes) reads True. The fence itself is untouched everywhere else.
    """
    import core.final_answer_authorship as authority

    def authority_read(manifest) -> bool:
        provider_id = str(getattr(manifest, "provider_id", "") or "")
        if not provider_id.startswith("ollama-local:"):
            return True  # certification does not apply to this lane
        return provider_id in certified

    monkeypatch.setattr(
        authority,
        "local_manifest_authorship_certified",
        authority_read,
    )


def test_uncertified_pick_never_rides_ahead_of_a_certified_author(monkeypatch) -> None:
    _patch(monkeypatch, certified={"ollama-local:qwen3:8b"})
    ranked = [
        _manifest("ollama-local:qwen2.5:7b"),
        _manifest("ollama-local:qwen3:8b"),
    ]
    ordered = _prioritize_autopilot_selection(ranked, "ollama-local:qwen2.5:7b")
    assert [m.provider_id for m in ordered] == [
        "ollama-local:qwen3:8b",
        "ollama-local:qwen2.5:7b",
    ]


def test_certified_pick_still_rides_first(monkeypatch) -> None:
    _patch(monkeypatch, certified={"ollama-local:qwen3:8b"})
    ranked = [
        _manifest("ollama-local:qwen2.5:7b"),
        _manifest("ollama-local:qwen3:8b"),
    ]
    ordered = _prioritize_autopilot_selection(ranked, "ollama-local:qwen3:8b")
    assert ordered[0].provider_id == "ollama-local:qwen3:8b"


def test_pick_certification_does_not_apply_keeps_first_place(monkeypatch) -> None:
    """A pick the certification question does not arise for (cloud) keeps today's exact order."""
    _patch(monkeypatch, certified=set())
    ranked = [
        _manifest("ollama-local:qwen3:8b"),
        _manifest("openrouter:nvidia/nemotron:free"),
    ]
    ordered = _prioritize_autopilot_selection(ranked, "openrouter:nvidia/nemotron:free")
    assert ordered[0].provider_id == "openrouter:nvidia/nemotron:free"


def test_all_refused_keeps_ranked_order(monkeypatch) -> None:
    """No certified author available: the order is unchanged and the fence stays the authority."""
    _patch(monkeypatch, certified=set())
    ranked = [
        _manifest("ollama-local:qwen2.5:7b"),
        _manifest("ollama-local:qwen3:8b"),
    ]
    ordered = _prioritize_autopilot_selection(ranked, "ollama-local:qwen2.5:7b")
    assert [m.provider_id for m in ordered] == [
        "ollama-local:qwen2.5:7b",
        "ollama-local:qwen3:8b",
    ]


def test_pick_absent_from_ranking_is_unchanged(monkeypatch) -> None:
    _patch(monkeypatch, certified=set())
    ranked = [_manifest("ollama-local:qwen3:8b"), _manifest("ollama-local:qwen2.5:7b")]
    ordered = _prioritize_autopilot_selection(ranked, "ollama-local:missing")
    assert [m.provider_id for m in ordered] == [m.provider_id for m in ranked]
