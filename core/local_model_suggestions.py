"""Which local model VOOL suggests for this computer: one table, checked against primary sources.

Every place that names a local model for a machine reads this table: the Settings Download
button, the installer's bundle, the boot default, and the hardware-tier fallback. Before this,
those places carried three different lists, and the shipped ones were a year behind (qwen2.5 from
September 2024 on the tier ladder, qwen3 from April 2025 on the one-click button).

How a pick is made:
  * Apple Silicon is sized by total memory, which the GPU shares with macOS, VOOL and the apps.
    The daily model has to load on a busy machine (the resource governor needs its size plus a
    1.5 GB floor free), so it stays near or under a third of memory. A 24 GB Mac measured an
    18.6 GiB GPU working-set cap, and qwen3.8:27b (18 GB) was RAM-refused there whenever the
    machine was busy: that model is the "bigger jobs" pick from 32 GB, never the daily one below.
  * A computer with no usable GPU runs the model on the CPU, where speed, not memory, is the
    limit: a dense 9B answers at a few tokens a second there, so CPU machines stay on the 4B
    until they have the memory for the 35B mixture-of-experts model, which only computes 3B per
    token and so outruns the dense 9B on a CPU.
  * A usable NVIDIA card is sized by its own memory: the daily model must fit in VRAM whole
    (qwen3:8b at 5.2 GB already part-offloaded on an 8 GB card with 5.9 GB free, ~47 s a turn).
  * Free disk always caps the pick: a download that cannot land is not a suggestion.

The faster model (Qwen3-30B-A3B-Instruct-2507, Ollama `qwen3:30b-instruct`) is a mixture of experts
that computes 3.3B of its 30.5B weights per token. On a 24 GB M4 this architecture decoded at ~30 tok/s
at an 8K window (measured 2026-11-12 on qwen3:30b-a3b, the same architecture at the same Q4_K_M
quantisation), where a dense model has to stream all of its weights for every token. It is offered
next to the daily model, not in its place: the daily model scores higher on tool use (Qwen3.5-9B
BFCL-V4 66.1 / TAU2 79.1 against the 30B's own card, BFCL-v3 65.1 / TAU2-Retail 57.0), and the 30B
needs room beside VOOL's helper model (~2.3 GiB at its 16K window) and its recall model. Ollama loads
a model next to others only when it fits in 80% of the GPU memory still free, and unloads an idle
model otherwise (server/sched.go). The 30B takes ~18.2 GiB at 8K. macOS gives the GPU only part of a
Mac's memory: on the 24 GB M4, 16.7 GiB of the 30B fit, and the published figures for a 32 GB Mac
are about 21 to 24 GB, so on either the helper could not stay loaded beside the 30B and turns would
reload one of them. So it is offered from 36 GB of Mac memory and on graphics cards with 22 GB or
more, never on a computer without a usable GPU (no CPU speed measured).

The small helper model (tiny_fast) stays qwen3:0.6b: Qwen3.5-0.8B is about twice the download and
scores about the same on tool calls (BFCL-V4 25.3 on Qwen's card vs 23.9 for Qwen3-0.6B on the BFCL
board), so swapping it would cost every machine a download for no measured gain.

Sources, checked on VERIFIED_AT (licences from each model card's own licence field):
  * Qwen3.5 0.8B/2B/4B/9B: huggingface.co/Qwen/Qwen3.5-{0.8B,2B,4B,9B}, Apache-2.0, Feb 2026.
    BFCL-V4: 25.3 / 43.6 / 50.3 / 66.1; TAU2-Bench: 11.6 / 48.8 / 79.9 / 79.1 (vendor-reported).
    For comparison, Qwen3-8B (today's default) scores 42.6 on the BFCL-V4 board
    (gorilla.cs.berkeley.edu, updated 2026-04-12) and Qwen3-4B-2507 39.9 on Qwen's own card.
  * Qwen3.6-35B-A3B: huggingface.co/Qwen/Qwen3.6-35B-A3B, Apache-2.0, Apr 2026, 35B total / 3B
    active. Same card vs Gemma4-26B-A4B: TAU3 67.2 vs 59.0, MCPMark 37.0 vs 14.2, Tool Decathlon
    26.9 vs 12.0, MCP-Atlas 62.8 vs 50.0.
  * Qwen3.8-27B: huggingface.co/Qwen/Qwen3.8-27B, Apache-2.0, Aug 2026, dense. Its card beats
    Qwen3.6-27B on every agent row (e.g. Terminal Bench 2.1 73.0 vs 63.4, SWE-bench Pro 61.7 vs
    53.5). It passed VOOL's tool check on the 24 GB Mac (local 0.7.8 handover, 2026-11-06).
  * Ollama tags and download sizes: ollama.com/library/{qwen3.5,qwen3.6,qwen3.8}/tags. The
    largest size Ollama lists for a tag is used (on Apple, Ollama may serve the MLX build, which
    is the bigger one). All three families are in Ollama 0.40.0's release notes; VOOL's macOS app
    bundles Ollama 0.40.3 (ops/macos_build_inputs.py).
Left out on purpose: gpt-oss:20b (Apache-2.0, but it failed VOOL's tool check on the 24 GB Mac:
harmony-format parallel calls), Nemotron 3.5 Bolt (OpenMDW-1.1 licence), Qwen3.8-Flash-Next
(Qwen Community License), LFM3 (LFM Open License; needs Ollama 0.40.12), MiMo-V2.6 (needs Ollama
0.40.15), Gemma 4 (Apache-2.0, but at each size its card's Tau2 trails the Qwen pick: E4B 42.2 at
6.6 GB vs Qwen3.5-4B 79.9 at 3.3 GB; the two cards use different Tau2 setups, so this is a lean,
not a measurement).

An Ollama older than 0.40 cannot pull these tags (the registry answered 412 to 0.21.2 for
qwen3.8). When the local Ollama says it is older, the suggestion falls back to the qwen3 tag of
the same class and says that updating Ollama unlocks the newer model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VERIFIED_AT = "2026-11-11"

#: The first Ollama release whose notes list qwen3.5, qwen3.6, qwen3.8 and gemma4 together.
CURRENT_MODELS_MIN_OLLAMA: tuple[int, int, int] = (0, 40, 0)

#: The small helper model every bundle carries (see the module docstring for why it stays).
TINY_HELPER_MODEL = "qwen3:0.6b"


@dataclass(frozen=True)
class SuggestedModel:
    tag: str
    #: Largest download Ollama lists for this tag, in GB (an Apple Silicon Mac may get the bigger
    #: MLX build). Free-disk checks use this one, so a pick never runs a disk out.
    download_gb: float
    #: What every other computer pulls (the GGUF build): the total of the tag's layers in the
    #: Ollama registry manifest, checked 2026-11-11. The Download button shows this size there.
    gguf_download_gb: float
    parameter_count: str
    architecture: str  # "dense" | "moe"
    active_parameter_count: str
    license_name: str
    license_reference: str
    library_url: str
    #: The qwen3 tag of the same class, offered when the local Ollama is older than 0.40.
    previous_generation: str
    tool_evidence: str


SUGGESTED_MODELS: dict[str, SuggestedModel] = {
    item.tag: item
    for item in (
        SuggestedModel(
            tag="qwen3.5:0.8b", download_gb=1.3, gguf_download_gb=1.3, parameter_count="0.8B", architecture="dense",
            active_parameter_count="", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.5-0.8B",
            library_url="https://ollama.com/library/qwen3.5", previous_generation="qwen3:0.6b",
            tool_evidence="BFCL-V4 25.3, TAU2 11.6 (Qwen card); only for machines too small for more.",
        ),
        SuggestedModel(
            tag="qwen3.5:2b", download_gb=3.1, gguf_download_gb=2.7, parameter_count="2B", architecture="dense",
            active_parameter_count="", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.5-2B",
            library_url="https://ollama.com/library/qwen3.5", previous_generation="qwen3:1.7b",
            tool_evidence="BFCL-V4 43.6, TAU2 48.8 (Qwen card) vs Qwen3-4B-2507 39.9 / 43.2.",
        ),
        SuggestedModel(
            tag="qwen3.5:4b", download_gb=4.0, gguf_download_gb=3.3, parameter_count="4B", architecture="dense",
            active_parameter_count="", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.5-4B",
            library_url="https://ollama.com/library/qwen3.5", previous_generation="qwen3:4b",
            tool_evidence="BFCL-V4 50.3, TAU2 79.9 (Qwen card) vs Qwen3-8B 42.6 on the BFCL board.",
        ),
        SuggestedModel(
            tag="qwen3.5:9b", download_gb=7.6, gguf_download_gb=6.6, parameter_count="9B", architecture="dense",
            active_parameter_count="", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.5-9B",
            library_url="https://ollama.com/library/qwen3.5", previous_generation="qwen3:8b",
            tool_evidence="BFCL-V4 66.1, TAU2 79.1 (Qwen card) vs Qwen3-8B 42.6 on the BFCL board.",
        ),
        SuggestedModel(
            tag="qwen3.6:35b", download_gb=24.0, gguf_download_gb=22.6, parameter_count="35B", architecture="moe",
            active_parameter_count="3B", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.6-35B-A3B",
            library_url="https://ollama.com/library/qwen3.6", previous_generation="qwen3:30b",
            tool_evidence="TAU3 67.2, MCPMark 37.0, MCP-Atlas 62.8 (Qwen card).",
        ),
        SuggestedModel(
            tag="qwen3:30b-instruct", download_gb=19.0, gguf_download_gb=18.6, parameter_count="30B",
            architecture="moe", active_parameter_count="3.3B", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
            library_url="https://ollama.com/library/qwen3", previous_generation="qwen3:30b-instruct",
            tool_evidence="BFCL-v3 65.1, TAU2-Retail 57.0 (Qwen card); non-thinking only; ~30 tok/s at an 8K window on a 24 GB M4.",
        ),
        SuggestedModel(
            tag="qwen3.8:27b", download_gb=18.0, gguf_download_gb=17.7, parameter_count="27B", architecture="dense",
            active_parameter_count="", license_name="Apache-2.0",
            license_reference="https://huggingface.co/Qwen/Qwen3.8-27B",
            library_url="https://ollama.com/library/qwen3.8", previous_generation="qwen3:32b",
            tool_evidence="Terminal Bench 2.1 73.0, SWE-bench Pro 61.7 (Qwen card); passed VOOL's tool check on a 24 GB Mac.",
        ),
    )
}

#: Previous-generation tags offered on an Ollama older than 0.40 (sizes from ollama.com/library/qwen3).
PREVIOUS_GENERATION_DOWNLOAD_GB: dict[str, float] = {
    "qwen3:0.6b": 0.7,
    "qwen3:1.7b": 1.4,
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3:30b": 19.0,
    "qwen3:30b-instruct": 19.0,
    "qwen3:32b": 20.0,
}

# Ladders, biggest first: (minimum memory in GB, daily model). Memory is total RAM for Apple
# Silicon and for CPU machines, and the card's own VRAM for a usable NVIDIA GPU. The thresholds
# sit just under the sizes machines are sold with (a "16 GB" PC reports ~15.6), so each class
# lands on its row.
_APPLE_DAILY: tuple[tuple[float, str], ...] = (
    (44.0, "qwen3.6:35b"),
    (17.0, "qwen3.5:9b"),
    (11.0, "qwen3.5:4b"),
    (5.5, "qwen3.5:2b"),
    (0.0, "qwen3.5:0.8b"),
)
_CPU_DAILY: tuple[tuple[float, str], ...] = (
    (44.0, "qwen3.6:35b"),
    (11.0, "qwen3.5:4b"),
    (5.5, "qwen3.5:2b"),
    (0.0, "qwen3.5:0.8b"),
)
_NVIDIA_DAILY: tuple[tuple[float, str], ...] = (
    (22.0, "qwen3.8:27b"),
    (10.0, "qwen3.5:9b"),
    (6.0, "qwen3.5:4b"),
)
# "Bigger jobs" (the bundle's deep_overnight role): smarter but slower or heavier than the daily
# model, used for queued work. "" means the daily model is already the biggest that fits.
_APPLE_DEEP: tuple[tuple[float, str], ...] = (
    (30.0, "qwen3.8:27b"),
    (17.0, ""),
    (11.0, "qwen3.5:9b"),
    (5.5, "qwen3.5:4b"),
    (0.0, ""),
)
_CPU_DEEP: tuple[tuple[float, str], ...] = (
    (44.0, "qwen3.8:27b"),
    (14.0, "qwen3.5:9b"),
    (11.0, ""),
    (7.5, "qwen3.5:4b"),
    (0.0, ""),
)
_NVIDIA_DEEP: tuple[tuple[float, str], ...] = (
    (22.0, ""),
    (15.0, "qwen3.8:27b"),
    (10.0, ""),
    (6.0, "qwen3.5:9b"),
)

# "Faster" (Settings offers it next to the daily model): the 30B mixture of experts, only where it
# can stay loaded beside VOOL's helper model (see the module docstring). "" means no faster pick.
_APPLE_FAST: tuple[tuple[float, str], ...] = (
    (44.0, ""),  # the daily qwen3.6:35b is already a 3B-active mixture of experts
    (34.0, "qwen3:30b-instruct"),  # 36 GB Macs; 32 GB is too tight (module docstring)
    (0.0, ""),
)
_NVIDIA_FAST: tuple[tuple[float, str], ...] = (
    (22.0, "qwen3:30b-instruct"),
    (0.0, ""),
)
_CPU_FAST: tuple[tuple[float, str], ...] = ((0.0, ""),)

#: Room kept free on the model disk beyond the download itself.
_DISK_MARGIN_GB = 4.0


@dataclass(frozen=True)
class LocalModelSuggestion:
    daily: str
    deep: str
    tiny: str
    machine_kind: str  # "apple" | "nvidia" | "cpu"
    sized_by_gb: float
    #: Set when the local Ollama is older than 0.40 and the qwen3 fallback was picked.
    ollama_update_unlocks: str = ""
    #: The faster model Settings offers next to the daily one ("" when none fits this computer).
    #: An offer only: it is not part of the bundle, so `models` leaves it out.
    fast: str = ""

    @property
    def models(self) -> tuple[str, ...]:
        """Every model the bundle carries, tiny first, without duplicates."""
        seen: list[str] = []
        for tag in (self.tiny, self.daily, self.deep):
            if tag and tag not in seen:
                seen.append(tag)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily": self.daily,
            "deep": self.deep,
            "tiny": self.tiny,
            "fast": self.fast,
            "machine_kind": self.machine_kind,
            "sized_by_gb": round(self.sized_by_gb, 1),
            "ollama_update_unlocks": self.ollama_update_unlocks,
            "verified_at": VERIFIED_AT,
        }


def _probe_value(probe: Any, name: str) -> Any:
    if isinstance(probe, Mapping):
        return probe.get(name)
    return getattr(probe, name, None)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def machine_kind(probe: Any) -> str:
    """apple / nvidia / cpu: which ladder sizes this machine.

    NVIDIA means a card the hardware probe classed usable for CUDA with at least 6 GB: Ollama
    loads the model into that card's memory without any extra setup. A probe that names CUDA with
    no status counts as usable, as `core.hardware_tier.tier_summary` reads it (the probe only
    names CUDA for a card it could use). AMD/DirectML/Vulkan cards are not a lane VOOL has
    verified, so they size like a CPU machine, as the bundle code does.
    """
    accelerator = str(_probe_value(probe, "accelerator") or "").strip().lower()
    if accelerator == "mps":
        return "apple"
    status = str(_probe_value(probe, "accelerator_status") or "").strip().lower() or "usable"
    if accelerator == "cuda" and status == "usable" and _as_float(_probe_value(probe, "vram_gb")) >= 6.0:
        return "nvidia"
    return "cpu"


def _pick(ladder: tuple[tuple[float, str], ...], memory_gb: float) -> str:
    for minimum, tag in ladder:
        if memory_gb >= minimum:
            return tag
    return ladder[-1][1]


def download_gb(tag: str) -> float:
    entry = SUGGESTED_MODELS.get(str(tag or "").strip())
    if entry is not None:
        return entry.download_gb
    return PREVIOUS_GENERATION_DOWNLOAD_GB.get(str(tag or "").strip(), 0.0)


def download_gb_for(tag: str, machine_kind: str) -> float:
    """The download size this computer will get: the larger listed size on Apple Silicon (Ollama
    may serve the MLX build there), the GGUF size everywhere else."""
    entry = SUGGESTED_MODELS.get(str(tag or "").strip())
    if entry is not None and str(machine_kind or "").strip() != "apple":
        return entry.gguf_download_gb
    return download_gb(tag)


def _fits_disk(tag: str, free_disk_gb: float | None) -> bool:
    if free_disk_gb is None or free_disk_gb <= 0.0:
        return True  # unknown free space never blocks a suggestion
    return download_gb(tag) + _DISK_MARGIN_GB <= free_disk_gb


def _step_down_for_disk(ladder: tuple[tuple[float, str], ...], tag: str, free_disk_gb: float | None) -> str:
    if _fits_disk(tag, free_disk_gb):
        return tag
    tags = [item for _minimum, item in ladder if item]
    start = tags.index(tag) + 1 if tag in tags else 0
    for smaller in tags[start:]:
        if _fits_disk(smaller, free_disk_gb):
            return smaller
    return tags[-1] if tags else tag


def parse_ollama_version(raw: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(raw or ""))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def ollama_too_old(raw_version: str) -> bool:
    """True only for a KNOWN version older than 0.40; an unknown version is not held against it."""
    parsed = parse_ollama_version(raw_version)
    return parsed is not None and parsed < CURRENT_MODELS_MIN_OLLAMA


def suggest_local_models(
    probe: Any,
    *,
    free_disk_gb: float | None = None,
    ollama_version: str = "",
) -> LocalModelSuggestion:
    """The daily, bigger-jobs and helper models this machine should get, and its faster offer."""
    kind = machine_kind(probe)
    if kind == "apple":
        sized_by = _as_float(_probe_value(probe, "ram_gb"))
        daily_ladder, deep_ladder, fast_ladder = _APPLE_DAILY, _APPLE_DEEP, _APPLE_FAST
    elif kind == "nvidia":
        sized_by = _as_float(_probe_value(probe, "vram_gb"))
        daily_ladder, deep_ladder, fast_ladder = _NVIDIA_DAILY, _NVIDIA_DEEP, _NVIDIA_FAST
    else:
        sized_by = _as_float(_probe_value(probe, "ram_gb"))
        daily_ladder, deep_ladder, fast_ladder = _CPU_DAILY, _CPU_DEEP, _CPU_FAST
    daily = _step_down_for_disk(daily_ladder, _pick(daily_ladder, sized_by), free_disk_gb)
    deep = _pick(deep_ladder, sized_by)
    if deep and (deep == daily or not _fits_disk(deep, free_disk_gb)):
        deep = ""
    fast = _pick(fast_ladder, sized_by)
    if fast and (fast in {daily, deep} or not _fits_disk(fast, free_disk_gb)):
        fast = ""
    unlocks = ""
    if ollama_too_old(ollama_version):
        unlocks = daily
        daily = SUGGESTED_MODELS[daily].previous_generation
        deep = SUGGESTED_MODELS[deep].previous_generation if deep else ""
        if deep == daily:
            deep = ""
        # qwen3:30b-instruct is a qwen3 tag already, so an older Ollama pulls the same model.
        fast = SUGGESTED_MODELS[fast].previous_generation if fast else ""
    return LocalModelSuggestion(
        daily=daily,
        deep=deep,
        tiny=TINY_HELPER_MODEL,
        machine_kind=kind,
        sized_by_gb=sized_by,
        ollama_update_unlocks=unlocks,
        fast=fast,
    )


def suggested_tags() -> frozenset[str]:
    """Every tag this table can suggest, current and previous generation."""
    return frozenset(SUGGESTED_MODELS) | frozenset(PREVIOUS_GENERATION_DOWNLOAD_GB)


__all__ = [
    "CURRENT_MODELS_MIN_OLLAMA",
    "PREVIOUS_GENERATION_DOWNLOAD_GB",
    "SUGGESTED_MODELS",
    "TINY_HELPER_MODEL",
    "VERIFIED_AT",
    "LocalModelSuggestion",
    "SuggestedModel",
    "download_gb",
    "machine_kind",
    "ollama_too_old",
    "parse_ollama_version",
    "suggest_local_models",
    "suggested_tags",
]
