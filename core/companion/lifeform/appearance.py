"""Deterministic appearance: genome -> render spec -> pinned visual manifest.

The same canonical lifeform JSON must reconstruct the same appearance anywhere.
``render_spec`` is pure, integer-only, and seeded exclusively from
``genesis_seed`` + stage + the pinned asset/palette versions. The manifest hash
pins (genome, stage, layer order, palette, compositor) — byte-identical pixels
are a verification, not a promise. Growth rings are milestone labels rendered
as structural details; they carry no dates (privacy law L8).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

#: fixed layer order, bottom -> top (research/creature_visual_system.md)
LAYER_ORDER = (
    "underglow", "aura_back", "orbitals_back", "body", "body_detail",
    "growth_rings", "core", "face", "crest", "modules_l", "modules_r",
    "orbitals_front", "mutation", "rarity", "outline",
)

CANVAS = 64
ICON_PASS = 32
PALETTE = "VOO-32@1"
COMPOSITOR = "@1"

ARCHETYPES = ("MONOLITH", "FORGE", "HALO", "MESH", "WISP", "DRIFT", "SHROUD",
              "LATTICE")
LINEAGES = ("LUMEN", "MYCEL", "FERRO", "SIGNAL", "NOCT", "HYBRID")


def _splitmix64_stream(seed_int: int):
    state = seed_int & ((1 << 64) - 1)

    def next_u64() -> int:
        nonlocal state
        state = (state + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        z = state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
        return z ^ (z >> 31)

    return next_u64


def _seed_int(genesis_seed: str) -> int:
    return int(hashlib.sha256(genesis_seed.encode("utf-8")).hexdigest()[:16], 16)


def stage_layers(stage: str) -> tuple[str, ...]:
    """Which layers exist at each stage — growth is additive, never a swap."""
    if stage == "seed":
        return ("underglow", "body", "growth_rings", "face", "outline")
    if stage == "protoform":
        return ("underglow", "aura_back", "body", "growth_rings", "core",
                "face", "crest", "outline")
    if stage == "form":
        return (*LAYER_ORDER[:11], "outline")
    return LAYER_ORDER  # specialized / ascended: full stack


def render_spec(doc_or_state: Any, *, stage: str, genesis_seed: str,
                archetype: str | None = None, lineage: str | None = None,
                rings: int = 0) -> dict[str, Any]:
    """Pure genome -> layer parameter mapping. Same inputs -> same dict."""
    nxt = _splitmix64_stream(_seed_int(genesis_seed))
    next_u64 = nxt
    archetype = archetype if archetype in ARCHETYPES else "WISP"
    lineage = lineage if lineage in LINEAGES else "HYBRID"
    spec = {
        "canvas": CANVAS,
        "icon_pass": ICON_PASS,
        "palette": PALETTE,
        "compositor": COMPOSITOR,
        "stage": stage,
        "archetype": archetype,
        "lineage": lineage,
        "palette_remap": int(next_u64() % 8),
        "layers": {},
    }
    layers = stage_layers(stage)
    for layer in LAYER_ORDER:
        if layer not in layers:
            continue
        if layer == "body":
            spec["layers"][layer] = {
                "form": archetype.lower(),
                "mass_pct": 52 + int(next_u64() % 9),
                "silhouette_seed": int(next_u64() & 0xFFFF),
            }
        elif layer == "core":
            spec["layers"][layer] = {
                "kind": ("tetra_stack" if archetype in ("MONOLITH", "FORGE")
                         else "orbit_heart"),
                "rings": 1 + int(next_u64() % 3),
            }
        elif layer == "face":
            spec["layers"][layer] = {
                "eyes": 1 + int(next_u64() % 2),
                "style": "cursor_blink",
            }
        elif layer == "crest":
            spec["layers"][layer] = {
                "kind": ("lens_spike" if archetype == "MONOLITH" else "antenna"),
                "height_pct": 8 + int(next_u64() % 10),
            }
        elif layer == "orbitals_front" or layer == "orbitals_back":
            count = int(next_u64() % 4)  # 0..3 orbitals
            spec["layers"][layer] = {
                "count": count if stage not in ("seed",) else 0,
                "kind": "shard",
                "speed_pct": 30 + int(next_u64() % 60),
            }
        elif layer == "aura_back":
            spec["layers"][layer] = {
                "kind": ("signal_halo" if lineage in ("LUMEN", "HYBRID")
                         else "dark_shroud" if lineage == "NOCT" else "mycel_veil"),
                "radius_pct": 18 + int(next_u64() % 12),
            }
        elif layer == "growth_rings":
            spec["layers"][layer] = {
                # milestone labels only — never dates (privacy law L8)
                "rings": ["ring"] * max(0, int(rings)),
                "style": "dla_dendrite",
            }
        elif layer == "modules_l" or layer == "modules_r":
            spec["layers"][layer] = {
                "kind": "quill" if int(next_u64() % 2) else "none",
            }
        else:
            spec["layers"][layer] = {"present": True}
    return spec


def manifest(render_spec_doc: dict[str, Any]) -> str:
    canonical = json.dumps(render_spec_doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
