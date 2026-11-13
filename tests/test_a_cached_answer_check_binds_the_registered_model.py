"""A cached answer-check verdict belongs to the model registration it was read for.

CI #681 (2026-11-13, served fault-wording journey on the v0.8 line): boot names the model in use
before the person's lane is registered, so ``author_certification`` cached a verdict for the boot
default lane (no model digest declared: "unknown"). The lane was then registered again as the build
the person certified, under the same provider id and endpoint, and every turn for the rest of the
cache lifetime was refused as "hasn't passed VOOL's answer check yet" without calling the model.

The cache now keys on the identity the certification fingerprint binds from the registration
(adapter, model digest, chat template, quantization) as well as the endpoint. The store and the
reader here are the real ones; the endpoint is a closed loopback port, so the live identity read
finds nothing, as on a machine whose model app is not answering.
"""
from __future__ import annotations

import pytest

from core import final_answer_authorship as authorship
from core import local_model_tool_certification as certification
from storage.model_provider_manifest import ModelProviderManifest
from tests._authorship_certification import certify_for_authorship

CLOSED_ENDPOINT = "http://127.0.0.1:9"


def _lane(**metadata: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="answer-check-cache:2b",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": CLOSED_ENDPOINT, "timeout_seconds": 5},
        metadata={"runtime_family": "ollama", "cost_class": "free_local", **metadata},
    )


@pytest.fixture(autouse=True)
def _fresh_cache():
    authorship.reset_for_tests()
    yield
    authorship.reset_for_tests()


@pytest.fixture
def reads(monkeypatch) -> list[str]:
    """Every time the authority reads the store instead of its cache (a spy: the real reader runs)."""
    seen: list[str] = []
    real = certification.certification_status

    def spy(manifest, **kwargs):
        seen.append(str((manifest.metadata or {}).get("model_digest") or ""))
        return real(manifest, **kwargs)

    monkeypatch.setattr(certification, "certification_status", spy)
    return seen


def test_a_lane_registered_again_as_the_certified_build_is_not_refused_from_the_boot_verdict(reads) -> None:
    boot_default = _lane()
    assert certification.certification_applies(boot_default), "precondition: a loopback lane the probe reaches"
    assert not authorship.author_certification(boot_default).certified, "precondition: nothing certified at boot"
    assert reads == [""]

    certified_build = _lane(model_digest="sha256:cache-test", chat_template_hash="tmpl", quantization="q4_K_M")
    # A run recorded by another process (a test helper, the CLI): nothing invalidates this cache.
    certify_for_authorship(certified_build)
    reads.clear()

    verdict = authorship.author_certification(certified_build)
    assert verdict.certified and verdict.state == "verified", verdict
    assert reads == ["sha256:cache-test"], "the certified build was answered from the boot lane's verdict"


def test_a_certified_build_does_not_lend_its_verdict_to_another_build_of_the_lane(reads) -> None:
    certified_build = _lane(model_digest="sha256:cache-test", chat_template_hash="tmpl", quantization="q4_K_M")
    certify_for_authorship(certified_build)
    reads.clear()
    assert authorship.author_certification(certified_build).certified, "precondition: the measured build"

    other_build = _lane(model_digest="sha256:another-build", chat_template_hash="tmpl", quantization="q4_K_M")
    verdict = authorship.author_certification(other_build)
    assert not verdict.certified, "a build never measured was served the measured build's verdict"
    assert reads == ["sha256:cache-test", "sha256:another-build"], reads


def test_the_same_registration_is_still_answered_from_the_cache(reads) -> None:
    lane = _lane(model_digest="sha256:cache-test", chat_template_hash="tmpl", quantization="q4_K_M")
    certify_for_authorship(lane)
    reads.clear()

    first = authorship.author_certification(lane)
    again = authorship.author_certification(_lane(model_digest="sha256:cache-test", chat_template_hash="tmpl", quantization="q4_K_M"))
    assert first.certified and again.certified
    assert reads == ["sha256:cache-test"], "the serving path read the store twice inside the cache lifetime"
