"""Dry-run mode: a deterministic, byte-identical plan for CI.

Dry-run boots nothing, records nothing, and claims nothing: it validates the
journey specification and emits the plan artifacts a real capture would fill
in, with a fixed epoch and canonical JSON so two runs in different
directories produce byte-identical files. A CI lane can therefore assert the
studio's whole specification surface without a daemon, a browser, or ffmpeg —
and without ever emitting something that could be mistaken for a captured
demo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.demo_studio.journeys import JOURNEY_IDS_REQUIRED
from ops.demo_studio.runner import StudioConfig, StudioMode, run_journey


def _cfg(tmp_path: Path) -> StudioConfig:
    return StudioConfig(
        repo=Path(__file__).resolve().parent.parent,
        out_dir=tmp_path,
        mode=StudioMode.DRY_RUN,
        # Planning every registered journey (including live-gated ones) is
        # the point of the determinism lane; the refusal semantics for the
        # default config are tested separately below.
        allowed_requirements=("live_network", "operator_models"),
    )


@pytest.mark.parametrize("jid", sorted(JOURNEY_IDS_REQUIRED))
def test_dry_run_emits_a_deterministic_plan_per_journey(tmp_path: Path, jid: str) -> None:
    first = run_journey(jid, _cfg(tmp_path / "a"))
    second = run_journey(jid, _cfg(tmp_path / "b"))
    assert first.verdict == "planned", "a dry run plans; it never claims a capture"
    a_files = sorted((tmp_path / "a").rglob("*.json"))
    assert a_files, "dry-run must emit at least the plan manifest"
    for fa in a_files:
        rel = fa.relative_to(tmp_path / "a")
        fb = tmp_path / "b" / rel
        assert fb.is_file(), f"dry-run artifact set is not stable: {rel} missing in run two"
        assert fa.read_bytes() == fb.read_bytes(), (
            f"dry-run artifact {rel} is not byte-deterministic"
        )


def test_dry_run_manifest_labels_itself_and_binds_the_journey(tmp_path: Path) -> None:
    outcome = run_journey("local_chat", _cfg(tmp_path))
    manifest = json.loads((tmp_path / outcome.manifest_path).read_text(encoding="utf-8"))
    assert manifest["mode"] == "dry-run"
    assert manifest["journey"]["id"] == "local_chat"
    assert manifest["journey"]["spec_hash"], "the plan must bind the journey spec hash"
    assert manifest["honesty"]["captured"] is False, (
        "a dry-run manifest must never claim a capture"
    )
    assert manifest["honesty"]["video"] is False
    # Fixed epoch: no wall clock anywhere in a dry-run manifest.
    assert manifest["generated_at"] == "2026-09-02T00:00:00Z"


def test_dry_run_manifest_lists_the_step_plan(tmp_path: Path) -> None:
    run_journey("refused_dangerous_action", _cfg(tmp_path))
    manifests = list((tmp_path).rglob("*plan*.json")) + list((tmp_path).rglob("*manifest*.json"))
    assert manifests, "dry-run must write its plan manifest"
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    steps = payload["journey"]["steps"]
    assert isinstance(steps, list) and steps
    for row in steps:
        assert "id" in row and "action" in row, "plan steps must be typed rows"


def test_dry_run_boots_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    import ops.demo_studio.runner as runner_mod

    def _refuse(*a, **k):  # pragma: no cover - must never run
        calls.append("boot")
        raise AssertionError("dry-run attempted to boot the staged app")

    monkeypatch.setattr(runner_mod, "_boot_staged_app", _refuse, raising=False)
    run_journey("local_chat", _cfg(tmp_path))
    assert calls == [], "dry-run must not touch the boot seam"


def test_dry_run_refuses_unknown_journey_visibly(tmp_path: Path) -> None:
    from ops.demo_studio.runner import StudioRefusal

    with pytest.raises(StudioRefusal) as excinfo:
        run_journey("not_a_journey", _cfg(tmp_path))
    assert "not_a_journey" in str(excinfo.value)


def test_dry_run_refuses_live_gated_journeys_instead_of_faking_them(
    tmp_path: Path,
) -> None:
    from ops.demo_studio.runner import StudioRefusal

    # The DEFAULT config allows nothing: a live-gated journey must refuse.
    cfg = StudioConfig(
        repo=Path(__file__).resolve().parent.parent,
        out_dir=tmp_path,
        mode=StudioMode.DRY_RUN,
    )
    with pytest.raises(StudioRefusal) as excinfo:
        run_journey("live_search_answer_with_sources", cfg)
    assert "live_network" in str(excinfo.value), (
        "the refusal must name the missing live dependency — a silent fake "
        "search demo is the exact fraud this studio forbids"
    )


def test_dry_run_allowlists_live_dependencies_explicitly(tmp_path: Path) -> None:
    # An operator may consciously plan a live journey's dry-run by allowing
    # the dependency in config; the plan still captures nothing.
    cfg = StudioConfig(
        repo=Path(__file__).resolve().parent.parent,
        out_dir=tmp_path,
        mode=StudioMode.DRY_RUN,
        allowed_requirements=("live_network",),
    )
    outcome = run_journey("live_search_answer_with_sources", cfg)
    assert outcome.verdict == "planned"
