"""The verified local-model catalog: reference entries and their staleness, for the doctor check.

Which model a machine is OFFERED (the Settings Download button, the installer's pull, the boot
default, the tier ladder) is decided in one place, `core.local_model_suggestions`, checked against
each model card's licence and VOOL's own tool check on 2026-11-11. This catalog's rank-based
`top_picks_for_machine` is not used for any download: on a 24 GB Mac it ranks qwen3.8:27b (RAM-
refused there whenever the machine is busy) and gpt-oss:20b (fails VOOL's tool check) first.

Original note (2026-11-05):

Born 2026-11-05: the default tier table (`core.hardware_tier`) was a full generation behind
(all-qwen2.5) while newer, smaller-footprint generations sat verified on the ollama library —
and nothing in the product could notice. The law this module starts: every suggestion carries
its own verification — the library reference, the license as verified (or honestly
`license_verified: False`), the footprint, and the date the library was checked — so a doctor
check can warn when the list goes stale instead of the app silently rotting.

"Runs without registration" is part of the law: every entry here is pullable anonymously from
the ollama library (`ollama pull <tag>`, no account, no key, no gated Hugging Face download).
Verified against ollama.com/library on CATALOG_VERIFIED_AT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CATALOG_VERIFIED_AT = "2026-11-05"
#: How long a verified catalog entry stays trusted before the doctor warns. Suggestion rot is
#: the measured defect: the shipped default table pointed at a model generation that had been
#: superseded twice over, and nothing in the product ever noticed.
STALE_AFTER_MONTHS = 3


@dataclass(frozen=True)
class CatalogModel:
    tag: str
    footprint_gb: float
    license_name: str
    license_reference: str
    library_url: str
    #: The verification day's own editorial order among entries of the same fit class
    #: (1 = preferred first). It is part of what a catalog re-verification re-decides —
    #: never a hidden heuristic.
    rank: int = 99
    license_verified: bool = False
    context_window: str = ""
    notes: str = ""
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gated_footprint_gb(self) -> float:
        """What the resource governor requires free: weights + the load-safety floor."""
        from core.resource_governor import _LOAD_SAFETY_FLOOR_GB

        return self.footprint_gb + float(_LOAD_SAFETY_FLOOR_GB)


#: The entries verified against ollama.com/library on CATALOG_VERIFIED_AT. License honesty is
#: part of the entry: the gpt-oss page states Apache-2.0 (verified); the qwen3.x library pages
#: state no license on the day of the check, and the entry says so instead of guessing.
CATALOG: tuple[CatalogModel, ...] = (
    # --- verified 2026-11-05 (ollama.com/library) -----------------------------------------
    CatalogModel(
        tag="qwen3.8:27b",
        rank=1,
        footprint_gb=18.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.8",
        license_verified=False, library_url="https://ollama.com/library/qwen3.8",
        notes="Dense 27B, text+image, agentic/coding focus; strongest verified model that clears a 24GB RAM gate.",
        roles=("deep", "daily_large"),
    ),
    CatalogModel(
        tag="qwen3.6:27b",
        rank=3,
        footprint_gb=18.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.6",
        license_verified=False, library_url="https://ollama.com/library/qwen3.6",
        notes="Dense 27B middle ground.",
        roles=("deep",),
    ),
    CatalogModel(
        tag="gpt-oss:20b",
        rank=2,
        footprint_gb=14.0, context_window="128K",
        license_name="Apache-2.0", license_reference="https://ollama.com/library/gpt-oss",
        license_verified=True, library_url="https://ollama.com/library/gpt-oss",
        notes="OpenAI open-weight MoE (~3.6B active), MXFP4; strong agent/tool lane. Requires a certification run before it may author.",
        roles=("daily", "agent"),
    ),
    CatalogModel(
        tag="qwen3.6:35b",
        rank=4,
        footprint_gb=23.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.6",
        license_verified=False, library_url="https://ollama.com/library/qwen3.6",
        notes="35B 'latest'; clears a 24GB gate only marginally (23 + 1.5 floor > 24) — deep/GPU-offload lane.",
        roles=("deep_gpu",),
    ),
    CatalogModel(
        tag="qwen3.5:35b-a3b",
        rank=5,
        footprint_gb=23.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.5",
        license_verified=False, library_url="https://ollama.com/library/qwen3.5",
        notes="MoE, already in the bundle footprint table.",
        roles=("deep_gpu",),
    ),
    CatalogModel(
        tag="qwen3.5:27b",
        rank=6,
        footprint_gb=20.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.5",
        license_verified=False, library_url="https://ollama.com/library/qwen3.5",
        notes="27B class, 17-20GB download.",
        roles=("deep",),
    ),
    CatalogModel(
        tag="qwen3.5:9b",
        rank=7,
        footprint_gb=7.6, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.5",
        license_verified=False, library_url="https://ollama.com/library/qwen3.5",
        notes="Daily-class, one generation above qwen3:8b; slightly heavier footprint.",
        roles=("daily",),
    ),
    CatalogModel(
        tag="qwen3.5:4b",
        rank=10,
        footprint_gb=4.0, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.5",
        license_verified=False, library_url="https://ollama.com/library/qwen3.5",
        notes="Lite class.",
        roles=("lite",),
    ),
    CatalogModel(
        tag="qwen3.5:0.8b",
        rank=12,
        footprint_gb=1.3, context_window="256K",
        license_name="unverified", license_reference="https://ollama.com/library/qwen3.5",
        license_verified=False, library_url="https://ollama.com/library/qwen3.5",
        notes="Nano class.",
        roles=("nano",),
    ),
    # --- previously verified (the generations the product already ships) ------------------
    CatalogModel(
        tag="qwen3:8b",
        rank=9,
        footprint_gb=5.2, context_window="",
        license_name="Apache-2.0", license_reference="https://ollama.com/library/qwen3",
        license_verified=True, library_url="https://ollama.com/library/qwen3",
        notes="The measured workhorse of the 2026-11-05 served session.",
        roles=("daily",),
    ),
    CatalogModel(
        tag="qwen3:14b",
        rank=8,
        footprint_gb=9.3, context_window="",
        license_name="Apache-2.0", license_reference="https://ollama.com/library/qwen3",
        license_verified=True, library_url="https://ollama.com/library/qwen3",
        notes="Fits a 10GB VRAM card fully (9.3 < 10) where the qwen2.5:14b equivalent (16GB) partial-offloads.",
        roles=("daily_large", "deep"),
    ),
    CatalogModel(
        tag="qwen3:0.6b",
        rank=11,
        footprint_gb=0.7, context_window="",
        license_name="Apache-2.0", license_reference="https://ollama.com/library/qwen3",
        license_verified=True, library_url="https://ollama.com/library/qwen3",
        notes="Tiny lane; measured 15-25 tok/s on the shipped hosts.",
        roles=("tiny",),
    ),
    CatalogModel(
        tag="gpt-oss:120b",
        rank=13,
        footprint_gb=65.0, context_window="128K",
        license_name="Apache-2.0", license_reference="https://ollama.com/library/gpt-oss",
        license_verified=True, library_url="https://ollama.com/library/gpt-oss",
        notes="Large MoE for big-RAM hosts.",
        roles=("titan",),
    ),
)


def catalog_entry(tag: str) -> CatalogModel | None:
    wanted = str(tag or "").strip().lower()
    for entry in CATALOG:
        if entry.tag.lower() == wanted:
            return entry
    return None


def catalog_fits(entry: CatalogModel, ram_gb: float) -> bool:
    """The same arithmetic the resource governor enforces at load: footprint + floor."""
    return entry.gated_footprint_gb <= float(ram_gb)


def top_picks_for_machine(ram_gb: float, count: int = 2) -> list[CatalogModel]:
    """The top verified models this machine's RAM gate would actually admit.

    Ordered by the catalog's verification-day rank among entries whose gated footprint clears
    the machine's RAM — a suggestion the governor would refuse at load time is not a
    suggestion, it is a wasted download. This is what "download the top 2" means: the best two
    verified models the machine can really run.
    """
    fitting = [entry for entry in CATALOG if catalog_fits(entry, ram_gb)]
    fitting.sort(key=lambda entry: (entry.rank, entry.tag))
    return fitting[: max(1, int(count))]


def catalog_staleness(now: str = "") -> dict[str, object]:
    """How stale the verified catalog is, for the doctor check.

    The date is a stamp, not a live fetch: re-verification means someone actually visited the
    library pages and confirmed the entries again, then bumped CATALOG_VERIFIED_AT.
    """
    from datetime import date

    try:
        verified = date.fromisoformat(CATALOG_VERIFIED_AT)
    except ValueError:
        return {"stale": True, "reason": "catalog_verified_at_unparsable", "verified_at": CATALOG_VERIFIED_AT}
    today = date.fromisoformat(now) if now else date.today()
    months_old = (today.year - verified.year) * 12 + (today.month - verified.month)
    return {
        "verified_at": CATALOG_VERIFIED_AT,
        "months_old": months_old,
        "stale": months_old >= STALE_AFTER_MONTHS,
        "stale_after_months": STALE_AFTER_MONTHS,
    }


def certified_tags() -> set[str]:
    """Model names with a VERIFIED authoring run on this runtime (the certification store).

    Read is best-effort and exception-safe: no store, no table, or no rows yields an empty set
    and the caller keeps its non-certified default — the fence at authoring time stays the
    authority either way.
    """
    try:
        from storage.model_tool_certification_store import list_certification_runs

        names: set[str] = set()
        for run in list_certification_runs(limit=200):
            if str(run.get("state") or "") != "verified":
                continue
            name = str(run.get("model_name") or "").strip()
            if name:
                names.add(name)
        return names
    except Exception:
        return set()


__all__ = [
    "CATALOG",
    "CATALOG_VERIFIED_AT",
    "STALE_AFTER_MONTHS",
    "CatalogModel",
    "catalog_entry",
    "catalog_fits",
    "catalog_staleness",
    "certified_tags",
    "top_picks_for_machine",
]


def _main() -> None:
    """`python -m core.local_model_catalog --top-picks` prints the model the installer pulls.

    One line: this machine's daily model from the suggestion table (the same model the Settings
    Download button offers), sized by Apple memory, NVIDIA VRAM or CPU speed, capped by free disk,
    and on an Ollama older than 0.40 the previous-generation tag of the same class.
    """
    import sys

    if "--top-picks" not in sys.argv:
        print(__doc__ or "local model catalog")
        return
    from core.local_model_readiness import recommended_local_model

    tag = str(recommended_local_model().get("model_name") or "").strip()
    if tag:
        print(tag)


if __name__ == "__main__":
    _main()
