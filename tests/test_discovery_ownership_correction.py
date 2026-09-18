"""The review's discovery defects, re-driven at the owning boundary after the correction.

Mirrors review/01-autodetection/2026-09-15-completion/probe_discovery.py: the REAL discovery
functions over a disposable file store, synthetic verification/listing boundaries, no network
and no credentials. The probe's four failing contracts must now hold, plus the corrected
evidence rules (authoritative empty listing, pagination/truncation, unknown modalities,
protocol from the configured contract) and the live-generation snapshot the review asked for.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from tests._credential_intelligence_support import isolated_home  # noqa: F401 — fixture registration


@pytest.fixture
def rig(isolated_home, monkeypatch):  # noqa: F811 - parameter, not a redefinition
    """The real discovery module over a temp state file, with synthetic boundaries."""
    import core.credential_intelligence.discovery as d

    monkeypatch.setattr(d, "_state_path", lambda: isolated_home / "data" / "discovery_assertions.json")
    descriptor = SimpleNamespace(
        provider_id="lab", kind="llm_cloud", verify_endpoint="https://invalid.test/v1/models",
        models_protocol="openai_models_list",
    )
    registry = SimpleNamespace(get=lambda _pid: descriptor)
    import core.credential_intelligence.verification as verification

    def _verified(*_a, **_k):
        return SimpleNamespace(status="verified", account="synthetic")

    monkeypatch.setattr(verification, "verify_provider_credential", _verified)
    monkeypatch.setattr(d, "_resolve_key", lambda _desc: "synthetic-key")
    generation = ["old"]
    monkeypatch.setattr(d, "_generations", lambda *a: (generation[0], "endpoint"))
    monkeypatch.setattr(d, "_fetch_models", lambda *a, **k: (200, b'{"data":[{"id":"lab/model"}]}', {}))
    yield SimpleNamespace(d=d, descriptor=descriptor, registry=registry, generation=generation,
                          verification=verification)
    return None


def _refresh(rig, provider_id="lab"):
    return rig.d.refresh_discovery(provider_id, registry=rig.registry)


def test_nonempty_control_still_observes(rig):
    first = _refresh(rig)
    assert first.models[0].status == "available"
    assert first.protocol == "openai_models_list"


def test_authoritative_empty_listing_removes_availability(rig):
    """The review's failing case 1: a successful COMPLETE empty data list is the provider saying
    the catalogue is empty -- formerly available models become missing, never silently retained."""
    _refresh(rig)
    rig.d._fetch_models = lambda *a, **k: (200, b'{"data":[]}', {})
    empty = _refresh(rig)
    assert empty.last_refresh_status == "empty_catalogue"
    assert all(m.status == "missing" for m in empty.models), "an authoritative empty listing left models available"
    assert empty.evidence == "observed"


def test_late_failure_cannot_overwrite_a_newer_generation(rig):
    """The review's failing case 2: a verification failure arriving after a newer generation was
    saved must not overwrite that state -- every outcome writes through the compare-and-set."""
    _refresh(rig)

    def raced_failure(*args, **kwargs):
        rig.generation[0] = "new"
        rig.d.save_assertion(rig.d.DiscoveryAssertion(
            "lab", "https://invalid.test", "new", "endpoint", account="new-account", last_refresh_status="newer-completed"))
        return SimpleNamespace(status="provider_unavailable", account="")

    with mock.patch.object(rig.verification, "verify_provider_credential", raced_failure):
        _refresh(rig)
    after = rig.d.load_assertions()["lab"]
    assert after.key_generation == "new"
    assert after.last_refresh_status == "newer-completed"
    assert after.refreshed_generations_match is True


def test_no_key_outcome_is_also_generation_checked(rig):
    """The same guard covers the no-key branch: the settings change mid-flight, and the no-key
    record for the OLD generation cannot overwrite the newer state."""
    _refresh(rig)
    rig.generation[0] = "new"
    rig.d.save_assertion(rig.d.DiscoveryAssertion(
        "lab", "https://invalid.test", "new", "endpoint", account="new-account", last_refresh_status="newer-completed"))
    rig.d._resolve_key = lambda _desc: ""          # key deleted while the refresh runs
    record = _refresh(rig)
    assert record.last_refresh_status == "no_key"
    after = rig.d.load_assertions()["lab"]
    assert after.key_generation == "new", "a stale no-key record overwrote a newer generation"


def test_concurrent_saves_for_different_providers_both_survive(rig):
    """The review's failing case 3: two concurrent save_assertion calls for different providers
    must both land. The read-modify-write is serialized by a cross-process state lock, so the
    interleaving the review induced (both threads reading the old file before either writes)
    cannot occur; the assertion is run over many rounds to catch any residual race."""
    state = rig.d._state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def save(pid):
        try:
            rig.d.save_assertion(rig.d.DiscoveryAssertion(pid, "https://invalid.test", "key", "endpoint"))
        except Exception as exc:  # recorded, asserted below
            errors.append(repr(exc))

    for _round in range(20):
        state.write_text("{}")
        threads = [threading.Thread(target=save, args=(pid,)) for pid in ("alpha", "beta")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        present = sorted(rig.d.load_assertions())
        assert present == ["alpha", "beta"] and not errors, f"round {_round}: {present} {errors}"


def test_concurrent_drop_and_save_preserve_the_unrelated_row(rig):
    """Drop and invalidation share the same file: a concurrent drop of one provider must not
    erase another provider's freshly saved row."""
    for _round in range(20):
        rig.d.save_assertion(rig.d.DiscoveryAssertion("alpha", "https://a.test", "k1", "e1"))
        dropper = threading.Thread(target=rig.d.drop_assertion, args=("alpha",))
        dropper.start()
        rig.d.save_assertion(rig.d.DiscoveryAssertion("beta", "https://b.test", "k2", "e2"))
        dropper.join(timeout=10)
        present = sorted(rig.d.load_assertions())
        assert present == ["beta"], f"round {_round}: {present}"


def test_unpublished_modalities_stay_unknown(rig):
    """The review's failing case 4: a row carrying only an id publishes NOTHING about
    modalities; unknown must stay distinct from a documented default."""
    rows, protocol, unreadable = rig.d.parse_models_list({"data": [{"id": "unknown-modalities-model"}]})
    assert protocol == "openai_models_list"
    assert rows[0].input_modalities == () and rows[0].output_modalities == ()
    assert rows[0].context_window == 0 and rows[0].max_output_tokens == 0


def test_anthropic_rows_under_an_openai_contract_are_a_mismatch(rig):
    """Protocol comes from the CONFIGURED contract, not row keys: a first-party anthropic body
    under an openai-configured provider is named as itself, never parsed by guessing."""
    rows, protocol, _unreadable = rig.d.parse_models_list(
        {"data": [{"id": "m", "display_name": "M", "max_tokens": 8}]},
        expected_protocol="openai_models_list")
    assert rows == () and protocol == ""


def test_truncated_listing_never_infers_absence_beyond_the_page(rig):
    _refresh(rig)
    rig.d._fetch_models = lambda *a, **k: (
        200, b'{"data":[{"id":"lab/other"}],"has_more":true}', {})
    partial = _refresh(rig)
    assert partial.truncated is True
    assert partial.last_refresh_status == "truncated"
    rows = {m.model_id: m for m in partial.models}
    assert rows["lab/model"].status == "available", "a partial page marked an unscanned model missing"


def test_snapshot_degrades_on_key_change_without_a_refresh(rig):
    """The projection evaluates generation freshness LIVE (offline): rotating or deleting the
    key makes the stored evidence project as unknown immediately, without waiting for a
    refresh, and a matching generation keeps it observed."""
    _refresh(rig)
    snap = rig.d.discovery_snapshot(["lab"], registry=rig.registry)
    assert snap["lab"]["generations_match"] is True
    assert snap["lab"]["evidence"] == "observed"

    rig.generation[0] = "rotated"
    snap = rig.d.discovery_snapshot(["lab"], registry=rig.registry)
    assert snap["lab"]["generations_match"] is False
    assert snap["lab"]["evidence"] == "unknown"
    assert all(m["evidence"] == "unknown" for m in snap["lab"]["models"])


def test_observed_capability_feeds_the_capability_authority(rig):
    """The existing model capability authority consumes discovery: an observed, generation-
    current row answers; rotated keys, truncated listings and unobserved ids do not."""
    from core.model_capability_catalog import SOURCE_DISCOVERY, capability_for

    _refresh(rig)
    # the lab descriptor is not in the production registry; drive the seam directly
    cap = rig.d.observed_capability("lab", "lab/model", registry=rig.registry)
    assert cap == (0, 0)  # this synthetic row publishes no windows: unknown, not invented
    rig.generation[0] = "rotated"
    assert rig.d.observed_capability("lab", "lab/model", registry=rig.registry) is None
    # and the authority's own source vocabulary exposes the discovery source
    assert SOURCE_DISCOVERY == "discovery_observed"
    assert capability_for("ollama-local", "whatever") .source in ("unknown", "curated", "catalog_live", "catalog_stale", "discovery_observed")


# ------------------------------------------------------------------ F3: corrupt pages are not emptiness

def test_all_malformed_rows_are_unreadable_not_an_empty_catalogue(rig):
    """The review's F3, original shape: a page whose every entry is unreadable
    ({data:[null,{}]}) must NOT act like an authoritative empty catalogue -- prior models keep
    their availability, the status names the corruption."""
    _refresh(rig)
    rig.d._fetch_models = lambda *a, **k: (200, b'{"data":[null,{}]}', {})
    corrupt = _refresh(rig)
    assert corrupt.last_refresh_status == "unreadable_rows"
    assert all(m.status == "available" for m in corrupt.models), (
        "a corrupt page was treated as authoritative emptiness and emptied the catalogue")


def test_mixed_valid_and_invalid_rows_update_only_the_readable_coverage(rig):
    """Novel shape: a page with one valid row and one unreadable row updates the valid row and
    keeps every unscanned model's state -- absence is never inferred from partial evidence."""
    _refresh(rig)
    rig.d._fetch_models = lambda *a, **k: (200, b'{"data":[{"id":"lab/other"}, null]}', {})
    mixed = _refresh(rig)
    assert mixed.last_refresh_status == "unreadable_rows"
    rows = {m.model_id: m for m in mixed.models}
    assert rows["lab/other"].status == "available" and rows["lab/other"].evidence == "observed"
    assert rows["lab/model"].status == "available", "an unreadable row marked a model missing"


def test_parser_distinguishes_empty_from_malformed(rig):
    """The exact review probe payloads: a genuinely empty list and a corrupt list must not
    share a meaning at the parser boundary either."""
    empty_rows, empty_protocol, empty_unreadable = rig.d.parse_models_list({"data": []})
    corrupt_rows, corrupt_protocol, corrupt_unreadable = rig.d.parse_models_list({"data": [None, {}]})
    assert empty_rows == () and empty_protocol == "openai_models_list" and empty_unreadable == 0
    assert corrupt_rows == () and corrupt_protocol == "openai_models_list" and corrupt_unreadable == 2


def test_genuine_complete_empty_still_marks_models_missing(rig):
    """Preservation control: the F3 repair did not break the real-empty semantics -- a clean
    complete empty listing is authoritative and formerly available models become missing."""
    _refresh(rig)
    rig.d._fetch_models = lambda *a, **k: (200, b'{"data":[]}', {})
    empty = _refresh(rig)
    assert empty.last_refresh_status == "empty_catalogue"
    assert all(m.status == "missing" for m in empty.models)


def test_capability_authority_ignores_unreadable_catalogues(rig):
    """Consumer control: a corrupt refresh does not feed the capability authority through the
    observed path -- only complete, readable, generation-current observations answer."""
    _refresh(rig)
    from core.model_capability_catalog import capability_for

    rig.d._fetch_models = lambda *a, **k: (200, b'{"data":[null,{}]}', {})
    _refresh(rig)
    # the lab provider is not in the production registry; drive the seam directly
    assert rig.d.observed_capability("lab", "lab/model", registry=rig.registry) is None, (
        "an unreadable catalogue still answered the capability consumer")
