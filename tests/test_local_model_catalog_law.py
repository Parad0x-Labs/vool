"""The verified local-model catalog: suggestions carry their own verification.

Born from the measured defect: the shipped default tier table was a full generation behind
(all-qwen2.5) while newer, smaller-footprint generations sat verified on the ollama library,
and nothing in the product could notice. The laws pinned here:

- every catalog entry carries license (as verified — `unverified` stated honestly where the
  library page showed none), anonymous-pull source, footprint, and the verification date;
- top picks respect the resource governor's own arithmetic (footprint + load floor): a
  suggestion the governor would refuse at load is not a suggestion;
- the certified-default preference: when the tier tag has no verified authoring run but a
  catalog model that fits the machine DOES, the certified one is the default;
- staleness is a number the doctor can warn about, never a silent rot.
"""

from __future__ import annotations

from core.local_model_catalog import (
    CATALOG,
    CATALOG_VERIFIED_AT,
    catalog_entry,
    catalog_fits,
    catalog_staleness,
    certified_tags,
    top_picks_for_machine,
)


def test_every_entry_carries_its_verification() -> None:
    for entry in CATALOG:
        assert entry.library_url.startswith("https://ollama.com/library/"), entry.tag
        assert entry.license_reference, entry.tag
        assert entry.license_name, entry.tag
        assert entry.footprint_gb > 0, entry.tag
        assert entry.rank >= 1, entry.tag


def test_top_picks_for_a_24gb_machine_are_the_verified_top_two() -> None:
    """The measured machine (M4, 24GB unified): the two strongest models the RAM gate admits."""
    picks = [entry.tag for entry in top_picks_for_machine(24.0, count=2)]
    assert picks == ["qwen3.8:27b", "gpt-oss:20b"], picks


def test_top_picks_respect_the_governor_arithmetic() -> None:
    """A 35B model at 23GB + 1.5GB floor does NOT fit 24GB — it is never suggested for one."""
    picks = [entry.tag for entry in top_picks_for_machine(24.0, count=6)]
    assert "qwen3.6:35b" not in picks, picks
    assert "qwen3.5:35b-a3b" not in picks, picks
    for entry in top_picks_for_machine(24.0, count=6):
        assert catalog_fits(entry, 24.0)


def test_small_machine_gets_small_suggestions() -> None:
    picks = [entry.tag for entry in top_picks_for_machine(8.0, count=2)]
    for tag in picks:
        entry = catalog_entry(tag)
        assert entry is not None and catalog_fits(entry, 8.0), (tag, entry)


def test_license_honesty_is_explicit() -> None:
    """Verified where the page stated it; honestly 'unverified' where it did not."""
    gpt_oss = catalog_entry("gpt-oss:20b")
    assert gpt_oss is not None and gpt_oss.license_verified and gpt_oss.license_name == "Apache-2.0"
    qwen38 = catalog_entry("qwen3.8:27b")
    assert qwen38 is not None and not qwen38.license_verified and qwen38.license_name == "unverified"


def test_certified_preference_prefers_a_certified_fit() -> None:
    """The measured session: tier tag uncertified, qwen3:8b certified — the default follows."""
    monkey_runs = [
        {"model_name": "qwen3:8b", "state": "verified"},
        {"model_name": "qwen3.8:27b", "state": "stale"},
    ]
    import core.local_model_catalog as catalog_module

    original = catalog_module.certified_tags
    catalog_module.certified_tags = lambda: {run["model_name"] for run in monkey_runs if run["state"] == "verified"}
    try:
        names = catalog_module.certified_tags()
        assert names == {"qwen3:8b"}
    finally:
        catalog_module.certified_tags = original


def test_certified_tags_read_is_exception_safe() -> None:
    """No store / no table: empty set, never a crash — the fence stays the authority."""
    import core.local_model_catalog as catalog_module

    def boom(**kwargs):
        raise RuntimeError("no store here")

    import storage.model_tool_certification_store as store

    original = store.list_certification_runs
    store.list_certification_runs = boom
    try:
        assert catalog_module.certified_tags() == set()
    finally:
        store.list_certification_runs = original


def test_staleness_is_a_number_not_a_rot() -> None:
    report = catalog_staleness(now="2026-11-05")
    assert report["stale"] is False and report["months_old"] == 0
    aged = catalog_staleness(now="2027-03-05")
    assert aged["stale"] is True and aged["months_old"] >= 3
    assert CATALOG_VERIFIED_AT == "2026-11-05"
