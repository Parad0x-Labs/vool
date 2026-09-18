"""A fake provider registered by one test must never route a later test to localhost."""
from __future__ import annotations

from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest

CONTAMINATED_PROVIDER = "test-isolation-provider"
CONTAMINATED_MODEL = "must-not-survive"
_REGISTERED: list[str] = []


def test_a_a_fake_provider_is_available_inside_the_test_that_registered_it() -> None:
    ModelRegistry().register_manifest(
        {
            "provider_name": CONTAMINATED_PROVIDER,
            "model_name": CONTAMINATED_MODEL,
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "test-only",
            "license_reference": "test-only",
            "weight_location": "external",
            "runtime_dependency": "test-only",
            "capabilities": ["summarize"],
            "runtime_config": {"base_url": "http://127.0.0.1:1234"},
            "enabled": True,
        }
    )
    _REGISTERED.append(CONTAMINATED_PROVIDER)

    assert get_provider_manifest(CONTAMINATED_PROVIDER, CONTAMINATED_MODEL) is not None


def test_b_the_next_test_cannot_see_the_previous_tests_fake_provider() -> None:
    assert _REGISTERED == [CONTAMINATED_PROVIDER], (
        "the contaminating test must run before this boundary assertion"
    )
    assert get_provider_manifest(CONTAMINATED_PROVIDER, CONTAMINATED_MODEL) is None, (
        "a fake provider survived the test boundary and can route later tests to localhost"
    )
