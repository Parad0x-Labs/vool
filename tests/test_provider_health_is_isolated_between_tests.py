"""Provider circuit-breaker state must not survive a test boundary -- and must still work inside one.

`core.model_health._HEALTH` is a module-level dict with no expiry of its own. `rank_providers`
subtracts 10.0 from any manifest whose circuit is open (`core/model_selection_policy.py`), while the
lane-fit margin separating the `general` tier from the `reasoning` tier for ordinary chat is 0.55 --
so a single circuit left open by an earlier test reverses the answer for every later test that ranks
providers. Measured: with `ollama-local:qwen2.5:7b` tripped,
`tests/test_per_task_model_switching.py::test_ordinary_chat_routes_to_the_general_model` selects
`deepseek-r1:14b`, which is exactly the reported intermittent failure.

Two halves are pinned here and they pull in opposite directions:

* `test_a_...` trips a real circuit through the real API and leaves it open on purpose.
  `test_b_...` asserts the very same provider id comes back clean. That pair is the boundary
  regression -- remove the `provider_health_reset` fixture from `tests/conftest.py` and
  `test_b_...` goes red.
* `test_recording_failures_inside_one_test_still_opens_the_circuit_and_changes_ranking` proves the
  fixture did not neuter the thing it isolates: within a single test, failures still open the
  circuit and the ranking still moves.

The trip uses `failure_threshold=1` with a 600s cooldown so the circuit cannot close on wall-clock
time and turn the boundary assertion into a coin flip.
"""
from __future__ import annotations

from core.model_health import (
    circuit_is_open,
    get_provider_health,
    record_provider_failure,
)
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, rank_providers
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import list_provider_manifests

# A provider id used by nothing else in the suite, so a green boundary assertion can only mean the
# reset ran -- never that some other module happened to clean up after itself.
CONTAMINATED_PROVIDER = "ollama-local:argus-isolation-probe"

# The real ids from the reported failure, so the ranking half exercises the actual regression shape.
GENERAL_PROVIDER = "ollama-local:qwen2.5:7b"

# Set by the first test, read by the second: a boundary assertion that runs BEFORE its contaminating
# partner would pass for the wrong reason, so the order is asserted rather than assumed.
_TRIPPED: list[str] = []

_COMMON = dict(
    source_type="http",
    adapter_type="local_qwen_provider",
    license_name="Apache-2.0",
    license_reference="https://www.apache.org/licenses/LICENSE-2.0",
    weight_location="user-supplied",
    weights_bundled=False,
    redistribution_allowed=True,
    runtime_dependency="ollama",
    capabilities=["summarize", "classify", "format", "structured_json", "code_complex", "long_context"],
    runtime_config={"base_url": "http://127.0.0.1:11434"},
    enabled=True,
)


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _register(model_name: str, bundle_role: str, orchestration_role: str = "drone") -> None:
    ModelRegistry().register_manifest(
        {
            **_COMMON,
            "provider_name": "ollama-local",
            "model_name": model_name,
            "metadata": {
                "deployment_class": "local",
                "bundle_role": bundle_role,
                "orchestration_role": orchestration_role,
            },
        }
    )


def _install_three_tier() -> None:
    run_migrations()
    _clear_manifests()
    _register("qwen3:0.6b", bundle_role="lightweight_utility")
    _register("qwen2.5:7b", bundle_role="general", orchestration_role="drone")
    _register("deepseek-r1:14b", bundle_role="reasoning", orchestration_role="queen")


def _ordinary_chat_top_model() -> str:
    ranked = rank_providers(
        list_provider_manifests(enabled_only=True),
        ModelSelectionRequest(task_kind="normalization_assist", output_mode="plain_text"),
    )
    assert ranked, "no provider ranked for ordinary chat"
    return ranked[0].model_name


def _trip(provider_id: str) -> None:
    """Open a circuit through the real API, with a cooldown long enough not to expire mid-suite."""
    record_provider_failure(
        provider_id,
        error="argus isolation probe",
        failure_threshold=1,
        cooldown_seconds=600,
    )


def test_a_a_failing_provider_leaves_its_circuit_open_within_the_test() -> None:
    """Contaminate on purpose. The circuit is genuinely open when this test ends."""
    _trip(CONTAMINATED_PROVIDER)
    _trip(GENERAL_PROVIDER)
    _TRIPPED.append(CONTAMINATED_PROVIDER)

    assert circuit_is_open(CONTAMINATED_PROVIDER) is True
    assert circuit_is_open(GENERAL_PROVIDER) is True
    assert get_provider_health(CONTAMINATED_PROVIDER).consecutive_failures == 1


def test_b_the_next_test_sees_a_clean_circuit_for_the_same_provider() -> None:
    """The boundary regression. Red the moment `provider_health_reset` stops running."""
    assert _TRIPPED == [CONTAMINATED_PROVIDER], (
        "the contaminating test must run before this one for the assertion below to mean anything; "
        f"saw {_TRIPPED!r}"
    )

    assert circuit_is_open(CONTAMINATED_PROVIDER) is False, (
        "a circuit opened by the previous test survived the test boundary -- "
        "core.model_health._HEALTH was not reset"
    )
    assert circuit_is_open(GENERAL_PROVIDER) is False, (
        "the previous test's open circuit on the general-tier provider survived; ordinary chat "
        "would now be routed by contaminated health state"
    )
    assert get_provider_health(CONTAMINATED_PROVIDER).consecutive_failures == 0
    assert get_provider_health(CONTAMINATED_PROVIDER).total_failures == 0


def test_c_ordinary_chat_still_routes_to_the_general_tier_after_a_contaminated_test() -> None:
    """The reported symptom itself, one boundary after the contamination that produced it."""
    _install_three_tier()
    assert _ordinary_chat_top_model() == "qwen2.5:7b"


def test_recording_failures_inside_one_test_still_opens_the_circuit_and_changes_ranking() -> None:
    """The fixture resets at the boundary, never inside a test -- so this still behaves normally."""
    _install_three_tier()
    assert _ordinary_chat_top_model() == "qwen2.5:7b"
    assert circuit_is_open(GENERAL_PROVIDER) is False

    # Five consecutive failures is the production default threshold; drive the real API rather than
    # writing to `_HEALTH`, so this fails if the breaker's own contract changes.
    for _ in range(5):
        record_provider_failure(GENERAL_PROVIDER, error="argus in-test probe")

    assert circuit_is_open(GENERAL_PROVIDER) is True
    assert _ordinary_chat_top_model() == "deepseek-r1:14b", (
        "an open circuit must still deprioritise its provider within the same test"
    )
