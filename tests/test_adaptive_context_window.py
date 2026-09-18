from __future__ import annotations

import json
from unittest import mock

import core.runtime_provider_defaults as provider_defaults
from core.runtime_install_profiles import installed_capacity_bucket
from core.runtime_provider_defaults import _ollama_context_sizing
from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role as ctx
from storage.model_provider_manifest import ModelProviderManifest


def _mock_registry():
    manifests: dict[tuple[str, str], ModelProviderManifest] = {}
    registry = mock.Mock()
    registry.get_manifest.side_effect = lambda provider_name, model_name: manifests.get((provider_name, model_name))
    registry.register_manifest.side_effect = lambda manifest: manifests.setdefault(
        (manifest.provider_name, manifest.model_name), manifest
    )
    return registry, manifests


def test_verified_bucket_enables_adaptive_sizing_by_default() -> None:
    assert ctx("general", model_tag="qwen3:8b", bucket="A", env={}) == 4096
    assert ctx("general", model_tag="qwen3:8b", bucket="B", env={}) == 6144
    assert ctx("general", model_tag="qwen3:8b", bucket="C", env={}) == 12288
    assert ctx("general", model_tag="qwen3:8b", bucket="D", env={}) == 24576


def test_explicit_opt_out_keeps_the_flat_baseline() -> None:
    for value in ("0", "false", "off"):
        assert ctx("general", model_tag="qwen3:8b", bucket="D", env={"VOOL_ADAPTIVE_CONTEXT": value}) == 4096
        assert ctx("heavy_reasoning", model_tag="qwen3:8b", bucket="E", env={"VOOL_ADAPTIVE_CONTEXT": value}) == 4096


def test_missing_or_unknown_bucket_uses_the_hardware_derived_bucket() -> None:
    expected_bucket = provider_defaults._hardware_context_bucket(env={})
    for bucket in ("", "?", "Z"):
        for role in ("lightweight_utility", "general", "heavy_reasoning", "coding"):
            assert ctx(role, model_tag="qwen3:8b", bucket=bucket, env={}) == ctx(
                role, model_tag="qwen3:8b", bucket=expected_bucket, env={}
            )


def test_unknown_model_size_keeps_baseline_even_with_verified_bucket() -> None:
    assert ctx("heavy_reasoning", model_tag="custom-model", bucket="E", env={}) == 4096
    assert ctx("heavy_reasoning", model_tag="", bucket="E", env={}) == 4096


def test_moe_expert_size_tag_fails_closed_without_proven_total_parameters() -> None:
    with mock.patch.object(provider_defaults, "model_metadata", return_value={}):
        assert provider_defaults._known_model_parameter_billions("mixtral:8x7b") is None
        assert provider_defaults._known_model_parameter_billions("mixtral:8x7b-instruct-v0.1") is None
        assert ctx("heavy_reasoning", model_tag="mixtral:8x7b", bucket="E", env={}) == 4096


def test_moe_registry_total_takes_precedence_over_expert_size_tag() -> None:
    with mock.patch.object(
        provider_defaults,
        "model_metadata",
        return_value={"parameter_count": "46.7B"},
    ):
        assert provider_defaults._known_model_parameter_billions("mixtral:8x7b") == 46.7
        assert ctx("heavy_reasoning", model_tag="mixtral:8x7b", bucket="E", env={}) == 8192


def test_reasoning_lane_is_not_inverted_on_verified_hardware() -> None:
    assert ctx("heavy_reasoning", model_tag="qwen3:8b", bucket="B", env={}) == 8192
    assert ctx("heavy_reasoning", model_tag="qwen3:8b", bucket="D", env={}) == 32768
    assert ctx("heavy_reasoning", model_tag="qwen3:8b", bucket="D", env={}) > ctx(
        "lightweight_utility", model_tag="qwen3:8b", bucket="D", env={}
    )


def test_total_parameter_count_clamps_each_hardware_bucket() -> None:
    expected = {
        "model:8b": {"A": 4096, "B": 8192, "C": 16384, "D": 32768, "E": 32768},
        "model:14b": {"A": 4096, "B": 4096, "C": 8192, "D": 16384, "E": 16384},
        "model:24b": {"A": 4096, "B": 4096, "C": 4096, "D": 8192, "E": 16384},
        "model:25b": {"A": 4096, "B": 4096, "C": 4096, "D": 8192, "E": 8192},
    }
    for model_tag, bucket_values in expected.items():
        for bucket, expected_num_ctx in bucket_values.items():
            assert ctx("heavy_reasoning", model_tag=model_tag, bucket=bucket, env={}) == expected_num_ctx


def test_q8_kv_growth_stays_inside_both_safe_ceilings() -> None:
    env = {"OLLAMA_KV_CACHE_TYPE": "q8_0"}
    assert ctx("general", model_tag="qwen3:8b", bucket="B", env=env) == 8192
    assert ctx("general", model_tag="qwen3:8b", bucket="C", env=env) == 16384
    assert ctx("general", model_tag="qwen3:14b", bucket="C", env=env) == 8192


def test_override_is_clamped_by_hardware_and_model() -> None:
    assert (
        ctx(
            "general",
            model_tag="qwen3:8b",
            bucket="B",
            env={"VOOL_OLLAMA_CONTEXT_WINDOW": "999999"},
        )
        == 8192
    )
    assert (
        ctx(
            "general",
            model_tag="qwen3:8b",
            bucket="B",
            env={"VOOL_OLLAMA_CONTEXT_WINDOW": "8000"},
        )
        == 8000
    )
    assert (
        ctx(
            "general",
            model_tag="qwen3:14b",
            bucket="D",
            env={"VOOL_OLLAMA_CONTEXT_WINDOW": "999999"},
        )
        == 16384
    )


def test_a_raising_bucket_reader_leaves_registration_on_a_safe_map(monkeypatch, tmp_path) -> None:
    # ensure_default_runtime_providers reads the persisted bucket; if that read raises, the
    # registered manifests must still carry a bounded window rather than failing the install.
    def boom(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(provider_defaults, "installed_capacity_bucket", boom)
    registry, manifests = _mock_registry()
    provider_defaults.ensure_default_runtime_providers(
        registry,
        model_tag="qwen2.5:7b",
        env={"VOOL_CONTEXT_BUCKET": "A"},
        install_profile="local-only",
        runtime_home=str(tmp_path),
    )
    manifest = manifests[("ollama-local", "qwen2.5:7b")]
    assert manifest.runtime_config["context_window"] == 4096
    assert manifest.runtime_config["prewarm"]["options"]["num_ctx"] == 4096


# --- persisted-bucket reader (cheap, no re-probe) ---------------------------------------------
def test_invalid_override_and_sizing_failure_fail_closed(monkeypatch) -> None:
    assert (
        ctx(
            "general",
            model_tag="qwen3:8b",
            bucket="B",
            env={"VOOL_OLLAMA_CONTEXT_WINDOW": "not-a-number"},
        )
        == 4096
    )
    monkeypatch.setattr(
        provider_defaults, "resolve_num_ctx", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    sizing = _ollama_context_sizing("general", model_tag="qwen3:8b", bucket="B", env={})
    assert sizing["selected_num_ctx"] == 4096
    assert sizing["context_sizing_policy"] == "flat_sizing_error"


def test_sizing_diagnostics_report_policy_bucket_model_and_selection() -> None:
    sizing = _ollama_context_sizing("general", model_tag="qwen3:8b", bucket="B", env={})
    assert sizing == {
        "context_sizing_policy": "adaptive_persisted_bucket",
        "capacity_bucket": "B",
        "model_parameter_billions": 8.0,
        "selected_num_ctx": 6144,
        "hardware_context_ceiling": 8192,
        "model_context_ceiling": 8192,
    }


def test_installed_capacity_bucket_reads_persisted_record(tmp_path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "install-profile.json").write_text(json.dumps({"capacity_bucket": "b"}), encoding="utf-8")
    assert installed_capacity_bucket(tmp_path) == "B"


def test_installed_capacity_bucket_missing_is_empty(tmp_path) -> None:
    assert installed_capacity_bucket(tmp_path) == ""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "install-profile.json").write_text(json.dumps({"capacity_bucket": "Z"}), encoding="utf-8")
    assert installed_capacity_bucket(tmp_path) == ""


# --- the override is a control on every path, not only the fully-proven one -------------------
# VOOL_OLLAMA_CONTEXT_WINDOW is documented as the per-machine escape hatch. Reading it only after
# the adaptive/bucket/model-size gates made it silently do nothing on the three paths below, which
# is indistinguishable to a user from the setting not existing.
def test_override_is_honored_when_adaptive_sizing_is_opted_out() -> None:
    env = {"VOOL_OLLAMA_CONTEXT_WINDOW": "8192", "VOOL_ADAPTIVE_CONTEXT": "0"}
    assert ctx("general", model_tag="qwen2.5:7b", bucket="D", env=env) == 8192
    # Opting out of AUTOMATIC sizing must not also discard an explicit window.
    assert _ollama_context_sizing("general", model_tag="qwen2.5:7b", bucket="D", env=env)[
        "context_sizing_policy"
    ] == "user_override"


def test_override_is_honored_for_an_unrecognized_model_tag_at_the_largest_model_ceiling() -> None:
    env = {"VOOL_OLLAMA_CONTEXT_WINDOW": "8192"}
    sizing = _ollama_context_sizing("general", model_tag="somevendor/custom:latest", bucket="D", env=env)
    assert sizing["selected_num_ctx"] == 8192  # bucket D, >24B row
    assert sizing["context_sizing_policy"] == "user_override_capped_unproven"
    # An unproven tag is bounded by the largest-model row, never by the <=8B row.
    assert ctx("general", model_tag="somevendor/custom:latest", bucket="D", env={"VOOL_OLLAMA_CONTEXT_WINDOW": "999999"}) == 8192


def test_override_without_a_persisted_bucket_is_capped_not_discarded() -> None:
    sizing = _ollama_context_sizing(
        "general",
        model_tag="qwen2.5:7b",
        bucket="",
        env={"VOOL_OLLAMA_CONTEXT_WINDOW": "8192", "VOOL_CONTEXT_BUCKET": "A"},
    )
    # An explicit machine bucket is enough to prove the override is safe even before an
    # install-profile record is persisted.
    assert sizing["selected_num_ctx"] == 4096
    assert sizing["context_sizing_policy"] == "user_override"


def test_override_can_size_down_for_a_constrained_card() -> None:
    # Giving VRAM back is the other half of the control; flooring at the 4096 baseline removed it.
    assert ctx("general", model_tag="qwen2.5:7b", bucket="B", env={"VOOL_OLLAMA_CONTEXT_WINDOW": "2048"}) == 2048
    assert ctx("general", model_tag="qwen2.5:7b", bucket="B", env={"VOOL_OLLAMA_CONTEXT_WINDOW": "16"}) == 1024


def test_unparseable_override_still_fails_closed_to_the_baseline() -> None:
    sizing = _ollama_context_sizing(
        "general", model_tag="qwen2.5:7b", bucket="D", env={"VOOL_OLLAMA_CONTEXT_WINDOW": "not-a-number"}
    )
    # A typo must not silently promote the machine to the wide adaptive window it would otherwise get.
    assert sizing["selected_num_ctx"] == 4096
    assert sizing["context_sizing_policy"] == "flat_invalid_override"
