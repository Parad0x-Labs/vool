"""The one table that says which local model each computer is offered.

Covers every machine class from a 4 GB laptop to a 24 GB graphics card, the free-disk cap, the
fallback for an Ollama too old to pull the current tags, and the licence and source fields that
make each pick checkable.
"""

from __future__ import annotations

import pytest

from core.local_model_suggestions import (
    CURRENT_MODELS_MIN_OLLAMA,
    PREVIOUS_GENERATION_DOWNLOAD_GB,
    SUGGESTED_MODELS,
    TINY_HELPER_MODEL,
    download_gb,
    machine_kind,
    ollama_too_old,
    parse_ollama_version,
    suggest_local_models,
    suggested_tags,
)


def _apple(ram_gb: float) -> dict[str, object]:
    return {"accelerator": "mps", "accelerator_status": "usable", "ram_gb": ram_gb, "vram_gb": 0.0}


def _cpu(ram_gb: float) -> dict[str, object]:
    return {"accelerator": "cpu", "accelerator_status": "", "ram_gb": ram_gb, "vram_gb": 0.0}


def _nvidia(vram_gb: float, ram_gb: float = 32.0) -> dict[str, object]:
    return {"accelerator": "cuda", "accelerator_status": "usable", "ram_gb": ram_gb, "vram_gb": vram_gb}


@pytest.mark.parametrize(
    ("ram_gb", "daily", "deep"),
    [
        (4.0, "qwen3.5:0.8b", ""),
        (8.0, "qwen3.5:2b", "qwen3.5:4b"),
        (16.0, "qwen3.5:4b", "qwen3.5:9b"),
        (18.0, "qwen3.5:9b", ""),
        (24.0, "qwen3.5:9b", ""),
        (32.0, "qwen3.5:9b", "qwen3.8:27b"),
        (36.0, "qwen3:30b-instruct", "qwen3.8:27b"),
        (48.0, "qwen3.6:35b", "qwen3.8:27b"),
        (128.0, "qwen3.6:35b", "qwen3.8:27b"),
    ],
)
def test_every_apple_silicon_size_gets_a_current_model(ram_gb: float, daily: str, deep: str) -> None:
    pick = suggest_local_models(_apple(ram_gb))
    assert (pick.daily, pick.deep, pick.tiny, pick.machine_kind) == (daily, deep, TINY_HELPER_MODEL, "apple")


def test_a_24_gb_mac_never_gets_a_daily_model_its_memory_refused() -> None:
    # qwen3.8:27b (18 GB) was RAM-refused on the 24 GB Mac whenever it was busy, so it may not be
    # the model that answers every turn there.
    assert suggest_local_models(_apple(24.0)).daily != "qwen3.8:27b"


@pytest.mark.parametrize(
    ("ram_gb", "daily", "deep"),
    [
        (4.0, "qwen3.5:0.8b", ""),
        (7.7, "qwen3.5:2b", "qwen3.5:4b"),
        (15.6, "qwen3.5:4b", "qwen3.5:9b"),
        (31.8, "qwen3.5:4b", "qwen3.5:9b"),
        (64.0, "qwen3.6:35b", "qwen3.8:27b"),
    ],
)
def test_a_computer_without_a_usable_gpu_stays_on_a_model_the_cpu_can_run(ram_gb: float, daily: str, deep: str) -> None:
    pick = suggest_local_models(_cpu(ram_gb))
    assert (pick.daily, pick.deep, pick.machine_kind) == (daily, deep, "cpu")


@pytest.mark.parametrize(
    ("vram_gb", "daily", "deep"),
    [
        (6.0, "qwen3.5:4b", "qwen3.5:9b"),
        (8.0, "qwen3.5:4b", "qwen3.5:9b"),
        (12.0, "qwen3.5:9b", ""),
        (16.0, "qwen3.5:9b", "qwen3.8:27b"),
        (24.0, "qwen3:30b-instruct", "qwen3.8:27b"),
    ],
)
def test_an_nvidia_card_is_sized_by_its_own_memory(vram_gb: float, daily: str, deep: str) -> None:
    pick = suggest_local_models(_nvidia(vram_gb))
    assert (pick.daily, pick.deep, pick.machine_kind) == (daily, deep, "nvidia")
    assert pick.sized_by_gb == vram_gb


def test_the_daily_model_fits_inside_the_card_it_is_picked_for() -> None:
    for vram_gb in (6.0, 8.0, 10.0, 12.0, 16.0, 24.0):
        daily = suggest_local_models(_nvidia(vram_gb)).daily
        assert download_gb(daily) < vram_gb, (vram_gb, daily)


@pytest.mark.parametrize(
    "probe",
    [
        {"accelerator": "cuda", "accelerator_status": "usable", "ram_gb": 16.0, "vram_gb": 4.0},
        {"accelerator": "cuda", "accelerator_status": "driver_missing", "ram_gb": 16.0, "vram_gb": 12.0},
        {"accelerator": "directml", "accelerator_status": "usable", "ram_gb": 16.0, "vram_gb": 12.0},
        {},
    ],
)
def test_small_unready_or_unverified_cards_size_like_a_cpu_machine(probe: dict[str, object]) -> None:
    assert machine_kind(probe) == "cpu"


def test_free_disk_steps_the_suggestion_down_to_a_download_that_lands() -> None:
    roomy = suggest_local_models(_apple(48.0), free_disk_gb=500.0)
    assert (roomy.daily, roomy.deep) == ("qwen3.6:35b", "qwen3.8:27b")

    tight = suggest_local_models(_apple(48.0), free_disk_gb=15.0)
    assert tight.daily == "qwen3.5:9b"
    assert tight.deep == ""  # 27b does not fit 15 GB either

    nearly_full = suggest_local_models(_apple(48.0), free_disk_gb=6.0)
    assert nearly_full.daily == "qwen3.5:0.8b"


def test_unknown_free_disk_never_blocks_a_suggestion() -> None:
    assert suggest_local_models(_apple(48.0), free_disk_gb=None).daily == "qwen3.6:35b"
    assert suggest_local_models(_apple(48.0), free_disk_gb=0.0).daily == "qwen3.6:35b"


@pytest.mark.parametrize(
    ("raw", "parsed", "too_old"),
    [
        ("0.40.3", (0, 40, 3), False),
        ("ollama version is 0.40.0", (0, 40, 0), False),
        ("0.21.2", (0, 21, 2), True),
        ("0.39.9", (0, 39, 9), True),
        ("1.0.0", (1, 0, 0), False),
        ("", None, False),
        ("unknown", None, False),
    ],
)
def test_ollama_version_reads_and_only_a_known_old_one_counts(raw: str, parsed: object, too_old: bool) -> None:
    assert parse_ollama_version(raw) == parsed
    assert ollama_too_old(raw) is too_old
    assert CURRENT_MODELS_MIN_OLLAMA == (0, 40, 0)


def test_an_old_ollama_gets_the_previous_generation_and_is_told_what_updating_unlocks() -> None:
    pick = suggest_local_models(_apple(16.0), ollama_version="0.21.2")
    assert (pick.daily, pick.deep) == ("qwen3:4b", "qwen3:8b")
    assert pick.ollama_update_unlocks == "qwen3.5:4b"

    current = suggest_local_models(_apple(16.0), ollama_version="0.40.3")
    assert (current.daily, current.ollama_update_unlocks) == ("qwen3.5:4b", "")


def test_every_suggested_model_is_apache_licensed_and_names_its_sources() -> None:
    for tag, entry in SUGGESTED_MODELS.items():
        assert entry.license_name == "Apache-2.0", tag
        assert entry.license_reference.startswith("https://huggingface.co/Qwen/"), tag
        assert entry.library_url.startswith("https://ollama.com/library/"), tag
        assert entry.download_gb > 0.0, tag
        assert entry.tool_evidence, tag
        assert entry.previous_generation in PREVIOUS_GENERATION_DOWNLOAD_GB, tag


def test_every_ladder_tag_has_a_known_download_size() -> None:
    for probe in [_apple(gb) for gb in (4, 8, 16, 24, 32, 48)] + [_cpu(gb) for gb in (4, 8, 16, 64)] + [
        _nvidia(gb) for gb in (6, 12, 16, 24)
    ]:
        for version in ("0.40.3", "0.21.2"):
            pick = suggest_local_models(probe, ollama_version=version)
            for tag in pick.models:
                if tag == TINY_HELPER_MODEL:
                    continue
                assert tag in suggested_tags(), tag
                assert download_gb(tag) > 0.0, tag


def test_the_bundle_list_has_no_duplicates_and_puts_the_helper_first() -> None:
    pick = suggest_local_models(_apple(24.0))
    assert pick.models == (TINY_HELPER_MODEL, "qwen3.5:9b")
    as_dict = pick.to_dict()
    assert as_dict["daily"] == "qwen3.5:9b"
    assert as_dict["verified_at"]


def test_boot_adopts_only_suggested_tags_and_tries_this_computers_pick_first() -> None:
    from core.local_model_readiness import boot_adoptable_tags

    tags = boot_adoptable_tags(probe=_apple(24.0))
    assert tags[0] == "qwen3.5:9b"
    assert set(tags) == set(suggested_tags())
    assert len(tags) == len(set(tags))
    # An upgrade that pulled last generation's qwen3:8b can still be made ready on boot.
    assert "qwen3:8b" in tags
    sizes = [download_gb(tag) for tag in tags[1:]]
    assert sizes == sorted(sizes)


def test_the_download_button_offers_the_table_pick_and_its_bigger_model() -> None:
    from core.local_model_readiness import is_recommended_tag, recommended_local_model

    rec = recommended_local_model(probe=_cpu(15.6), free_disk_gb=200.0, ollama_version="0.40.3")
    assert rec["model_name"] == "qwen3.5:4b"
    # The size this computer will download: the GGUF build off Apple Silicon.
    assert rec["download_gb"] == SUGGESTED_MODELS["qwen3.5:4b"].gguf_download_gb == 3.3
    assert rec["machine_kind"] == "cpu"
    assert rec["bigger"] == {"model_name": "qwen3.5:9b", "download_gb": 6.6}
    assert "ollama_update_unlocks" not in rec

    old = recommended_local_model(probe=_cpu(15.6), free_disk_gb=200.0, ollama_version="0.21.2")
    assert old["model_name"] == "qwen3:4b"
    assert old["ollama_update_unlocks"] == "qwen3.5:4b"

    assert is_recommended_tag("qwen3.5:9b") and is_recommended_tag("qwen3:8b")
    assert not is_recommended_tag("gpt-oss:20b") and not is_recommended_tag("qwen2.5:7b")


def test_the_download_size_shown_is_the_one_this_computer_gets() -> None:
    from core.local_model_readiness import recommended_local_model
    from core.local_model_suggestions import download_gb_for

    for tag, entry in SUGGESTED_MODELS.items():
        # A Mac may get the bigger MLX build, so free-disk checks and the Mac label use the larger.
        assert 0.0 < entry.gguf_download_gb <= entry.download_gb, tag
        assert download_gb_for(tag, "apple") == entry.download_gb, tag
        assert download_gb_for(tag, "nvidia") == download_gb_for(tag, "cpu") == entry.gguf_download_gb, tag
    mac = recommended_local_model(probe=_apple(16.0), free_disk_gb=200.0, ollama_version="0.40.3")
    assert (mac["model_name"], mac["download_gb"]) == ("qwen3.5:4b", 4.0)
    old = recommended_local_model(probe=_cpu(15.6), free_disk_gb=200.0, ollama_version="0.21.2")
    assert (old["model_name"], old["download_gb"]) == ("qwen3:4b", 2.5)


@pytest.mark.parametrize(
    ("probe", "daily"),
    [
        # 24 GB Mac: the 30B (~18.2 GiB at 8K, 16.7 GiB of it on the GPU when measured there)
        # cannot stay loaded beside VOOL's helper model.
        (_apple(24.0), "qwen3.5:9b"),
        # 32 GB Mac: macOS gives the GPU about 21 to 24 GB, too little beside the helper and recall models.
        (_apple(32.0), "qwen3.5:9b"),
        (_apple(36.0), "qwen3:30b-instruct"),
        (_nvidia(16.0), "qwen3.5:9b"),
        (_nvidia(24.0), "qwen3:30b-instruct"),
        (_cpu(31.8), "qwen3.5:4b"),
    ],
)
def test_the_30b_is_the_daily_model_wherever_it_can_stay_loaded(probe: dict[str, object], daily: str) -> None:
    # sls 21:24Z Nov 12: given by default, nothing to tune; so no separate faster offer remains.
    pick = suggest_local_models(probe, free_disk_gb=500.0, ollama_version="0.40.3")
    assert pick.daily == daily
    assert pick.fast == ""
    entry = SUGGESTED_MODELS["qwen3:30b-instruct"]
    assert (entry.architecture, entry.active_parameter_count) == ("moe", "3.3B")
    assert entry.license_reference == "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507"


def test_settings_offers_no_separate_faster_model() -> None:
    from core.local_model_readiness import recommended_local_model

    for probe in (_apple(24.0), _apple(36.0), _nvidia(24.0)):
        rec = recommended_local_model(probe=probe, free_disk_gb=500.0, ollama_version="0.40.3")
        assert "faster" not in rec
    assert recommended_local_model(probe=_apple(36.0), free_disk_gb=500.0, ollama_version="0.40.3")["model_name"] == "qwen3:30b-instruct"
