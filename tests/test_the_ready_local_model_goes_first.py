"""The model the person made ready (Use this model) is the one VOOL uses first.

Measured on the 0.7.7 local-model branch (solo recheck, Oct 28): with only qwen3:8b ready, boot
logged "Daily Auto boot primary aligned with installed routing policy: qwen3:8b -> qwen2.5:7b",
and every turn first offered the refused qwen2.5:7b and then fell back. When both failed, the
reply blamed qwen2.5:7b and told the person to "switch the model selector to Auto".

1. Boot, each turn's plan and the planner skip a not-ready local model as primary while a ready
   one exists, and keep it as a fallback.
2. With no ready local model nothing changes.
3. The one-model-tried failure reply speaks plainly and names the model that actually ran.
"""

from __future__ import annotations

from types import SimpleNamespace

from core import final_answer_authorship as authorship
from core.agent_runtime.memory_runtime import _single_candidate_failure_hint
from core.local_inference_autopilot import (
    prioritize_daily_residency_capabilities,
    select_daily_residency_model_tag,
)
from core.model_registry import ModelProviderManifest
from core.web.api import runtime as web_runtime

INSTALLED = ["qwen2.5:7b", "qwen3:8b"]


def _manifest(model_name: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"runtime_family": "ollama"},
    )


def _ready_only(*ready: str):
    def certification(manifest, **_kwargs):
        name = manifest.model_name
        return authorship.AuthorCertification(
            certified=name in ready,
            state="verified" if name in ready else "unknown",
            source=authorship.CERTIFICATION_SOURCE_MEASURED,
            provider_id=manifest.provider_id,
        )

    return certification


def test_without_a_ready_model_the_daily_choice_is_unchanged() -> None:
    # The scoring that caused the report: the non-thinking 7B wins on its own.
    assert select_daily_residency_model_tag(INSTALLED, default_model_tag="qwen3:8b") == "qwen2.5:7b"


def test_boot_picks_the_ready_model_over_the_one_daily_scoring_likes() -> None:
    picked = select_daily_residency_model_tag(
        INSTALLED, default_model_tag="qwen3:8b", unready_local_models={"qwen2.5:7b"}
    )
    assert picked == "qwen3:8b"


def test_a_not_ready_model_stays_a_fallback_not_the_primary() -> None:
    from core.provider_routing import provider_capability_truth_for_manifest

    truth = tuple(provider_capability_truth_for_manifest(_manifest(name)) for name in INSTALLED)
    ordered = prioritize_daily_residency_capabilities(truth, unready_local_models={"qwen2.5:7b"})
    assert [item.model_id for item in ordered] == ["qwen3:8b", "qwen2.5:7b"]


def test_only_a_ready_model_makes_the_others_skippable(monkeypatch) -> None:
    manifests = [_manifest(name) for name in INSTALLED]
    monkeypatch.setattr(authorship, "author_certification", _ready_only())
    assert authorship.unready_local_model_names(manifests) == frozenset()
    monkeypatch.setattr(authorship, "author_certification", _ready_only("qwen3:8b"))
    assert authorship.unready_local_model_names(manifests) == frozenset({"qwen2.5:7b"})


def test_boot_alignment_reads_readiness_for_the_installed_models(monkeypatch) -> None:
    monkeypatch.setattr(web_runtime, "local_models_enabled", lambda env=None: True)
    monkeypatch.setattr(web_runtime, "_automatic_ollama_access_allowed", lambda env: True)
    monkeypatch.setattr(
        web_runtime,
        "installed_ollama_model_inventory",
        lambda **kwargs: tuple(SimpleNamespace(name=name, size_bytes=0) for name in INSTALLED),
    )
    monkeypatch.setattr(
        web_runtime.ModelRegistry,
        "list_manifests",
        lambda self, enabled_only=False, limit=256: [_manifest(name) for name in INSTALLED],
    )
    monkeypatch.setattr(authorship, "author_certification", _ready_only("qwen3:8b"))
    assert web_runtime.candidate_aware_daily_runtime_model_tag("qwen3:8b", env={}) == "qwen3:8b"
    monkeypatch.setattr(authorship, "author_certification", _ready_only())
    assert web_runtime.candidate_aware_daily_runtime_model_tag("qwen3:8b", env={}) == "qwen2.5:7b"


def test_the_one_model_tried_reply_is_plain_and_names_the_model_that_ran() -> None:
    execution = SimpleNamespace(
        source="all_ranked_providers_failed",
        details={
            "attempted": ["ollama-local:qwen3:8b"],
            "ranked_candidates": ["ollama-local:qwen3:8b", "ollama-local:qwen2.5:7b"],
        },
    )
    message = _single_candidate_failure_hint(execution)
    assert message.startswith(authorship.UNCERTIFIED_AUTHOR_NOTICE_LEAD)
    assert "qwen3:8b" in message and "qwen2.5" not in message
    for word in ("selector", "Auto", "provider", "ranked", "ollama-local"):
        assert word not in message, word


# --- a profile that never checked any model gets one ready on boot -------------------------------


def _boot_ready(states: dict[str, str], outcomes: dict[str, str], primary: str = "qwen3:8b"):
    from core.local_model_readiness import ready_local_models

    ran: list[str] = []

    def run_check(manifest):
        ran.append(manifest.model_name)
        return {"state": outcomes[manifest.model_name]}

    rows = ready_local_models(
        [_manifest(name) for name in INSTALLED],
        primary_model=primary,
        status_of=lambda m: (
            states[m.model_name] if isinstance(states.get(m.model_name), dict)
            else {"state": states.get(m.model_name, "unknown")}
        ),
        run_check=run_check,
    )
    return rows, ran


def test_a_never_checked_profile_checks_the_primary_first_and_stops_when_it_is_ready() -> None:
    rows, ran = _boot_ready({}, {"qwen3:8b": "verified", "qwen2.5:7b": "incompatible"})
    assert ran == ["qwen3:8b"]
    assert rows == [{"model": "qwen3:8b", "readiness": "ready"}]


def test_boot_checks_the_recommended_model_before_the_daily_guess() -> None:
    # The cloud re-audit's fresh boots aligned to qwen2.5:7b before anything was checked; on a
    # CPU-only box a doomed check of it costs minutes, so the recommended qwen3:8b goes first.
    rows, ran = _boot_ready({}, {"qwen2.5:7b": "incompatible", "qwen3:8b": "verified"}, primary="qwen2.5:7b")
    assert ran == ["qwen3:8b"]
    assert rows == [{"model": "qwen3:8b", "readiness": "ready"}]


def test_when_the_recommended_model_cannot_answer_boot_tries_the_primary_next() -> None:
    rows, ran = _boot_ready({}, {"qwen3:8b": "incompatible", "qwen2.5:7b": "verified"}, primary="qwen2.5:7b")
    assert ran == ["qwen3:8b", "qwen2.5:7b"]
    assert rows[-1] == {"model": "qwen2.5:7b", "readiness": "ready"}


_FAILED_ON_SKILL = {
    "state": "degraded",
    "stages": {"single_tool_call": {"state": "failed", "failure_code": "missing_tool_calls"}},
}
_TIMED_OUT = {
    "state": "degraded",
    "stages": {"single_tool_call": {"state": "failed", "failure_code": "timeout"}},
}


def test_nothing_runs_when_a_model_is_already_ready_or_already_failed() -> None:
    assert _boot_ready({"qwen2.5:7b": "verified"}, {}) == ([], [])
    rows, ran = _boot_ready({"qwen3:8b": "incompatible", "qwen2.5:7b": _FAILED_ON_SKILL}, {})
    assert ran == [] and rows == []


def test_a_check_that_timed_out_on_a_busy_computer_is_run_again_on_boot() -> None:
    # Fresh-eyes test, Oct 29: one timed-out check on a busy machine left VOOL refusing everything.
    rows, ran = _boot_ready({"qwen3:8b": _TIMED_OUT, "qwen2.5:7b": _FAILED_ON_SKILL}, {"qwen3:8b": "verified"})
    assert ran == ["qwen3:8b"] and rows[0]["readiness"] == "ready"


def test_an_upgrade_rechecks_a_model_checked_under_an_older_build() -> None:
    rows, ran = _boot_ready({"qwen3:8b": "stale", "qwen2.5:7b": "incompatible"}, {"qwen3:8b": "verified"})
    assert ran == ["qwen3:8b"] and rows[0]["readiness"] == "ready"


def test_a_remote_lane_is_never_checked_on_boot() -> None:
    from core.local_model_readiness import ready_local_models

    remote = ModelProviderManifest(
        provider_name="ollama-lan",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="openai_compatible",
        runtime_config={"base_url": "http://10.0.0.9:11434"},
    )
    ran: list[str] = []
    ready_local_models([remote], status_of=lambda m: {"state": "unknown"}, run_check=lambda m: ran.append(m) or {})
    assert ran == []


# --- the turn plan, and what VOOL says it runs, name the ready model ------------------------------


def test_auto_ranking_puts_a_ready_local_model_before_an_unready_one(monkeypatch) -> None:
    from core import provider_routing

    ranked = [_manifest("qwen2.5:7b"), _manifest("qwen3:8b")]
    monkeypatch.setattr(authorship, "author_certification", _ready_only("qwen3:8b"))
    ordered = provider_routing._ready_local_models_first(ranked, certified=provider_routing._cached_ready)
    assert [m.model_name for m in ordered] == ["qwen3:8b", "qwen2.5:7b"]
    monkeypatch.setattr(authorship, "author_certification", _ready_only())
    ordered = provider_routing._ready_local_models_first(ranked, certified=provider_routing._cached_ready)
    assert [m.model_name for m in ordered] == ["qwen2.5:7b", "qwen3:8b"]


def test_what_vool_says_it_runs_is_the_ready_model(monkeypatch) -> None:
    from core.local_model_readiness import model_in_use

    monkeypatch.setattr(
        "core.model_registry.ModelRegistry.list_manifests",
        lambda self, enabled_only=False, limit=256: [_manifest(name) for name in INSTALLED],
    )
    monkeypatch.setattr(authorship, "author_certification", _ready_only("qwen3:8b"))
    assert model_in_use("qwen2.5:7b") == "qwen3:8b"
    assert model_in_use("qwen3:8b") == "qwen3:8b"
    monkeypatch.setattr(authorship, "author_certification", _ready_only())
    assert model_in_use("qwen2.5:7b") == "qwen2.5:7b"


def test_the_builder_generates_with_an_installed_ready_model(monkeypatch) -> None:
    # Cloud re-audit N4: the builder used the machine recommendation qwen3:4b, never pulled, and
    # every "make a one page site" ask failed in seconds with "no usable file list".
    from core.local_model_readiness import local_model_for_work

    monkeypatch.setattr(
        "core.model_registry.ModelRegistry.list_manifests",
        lambda self, enabled_only=False, limit=256: [_manifest(name) for name in INSTALLED],
    )
    monkeypatch.setattr(authorship, "author_certification", _ready_only("qwen3:8b"))
    assert local_model_for_work("qwen3:4b") == "qwen3:8b"
    monkeypatch.setattr(authorship, "author_certification", _ready_only())
    assert local_model_for_work("qwen3:4b") == "qwen2.5:7b"
    assert local_model_for_work("qwen3:8b") == "qwen3:8b"
    monkeypatch.setattr(
        "core.model_registry.ModelRegistry.list_manifests", lambda self, enabled_only=False, limit=256: []
    )
    assert local_model_for_work("qwen3:4b") == "qwen3:4b"


def test_while_a_model_is_being_made_ready_the_reply_says_so(monkeypatch) -> None:
    monkeypatch.setattr(authorship, "_local_model_being_checked", lambda: "qwen3:8b")
    text = authorship.author_refusal_text(reason="no_eligible_author", author="ollama-local:qwen2.5:7b")
    assert "still getting qwen3:8b ready" in text and "qwen2.5" not in text
    assert "Use this model" not in text
    monkeypatch.setattr(authorship, "_local_model_being_checked", lambda: "")
    assert "qwen2.5:7b isn’t ready" in authorship.author_refusal_text(
        reason="no_eligible_author", author="ollama-local:qwen2.5:7b"
    )
