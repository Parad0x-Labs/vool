"""Generate ``docs/ERROR_BOOK.md`` from the canonical fault catalog (``vool.fault.v1``).

The ERROR_BOOK is a HUMAN VIEW of ``core/faults/catalog.py`` — it is generated FROM the catalog and
never forks it. Every row is stamped with the catalog schema, version and digest, so a doc that has
drifted from the taxonomy is detectable (``tests/test_error_book_matches_catalog.py`` fails closed).

Regenerate:  PYTHONPATH=<repo> python -m tools.generate_error_book
"""
from __future__ import annotations

import pathlib

from core.faults.boundaries import BOUNDARIES
from core.faults.catalog import (
    CATALOG_VERSION,
    EFFECT_CONTACTED,
    EFFECT_LOCAL,
    EFFECT_NONE,
    EFFECT_UNCERTAIN,
    FAULT_SCHEMA,
    catalog_digest,
    export_catalog,
)

# Category display order — stable, so the doc diff is minimal across regenerations.
_CATEGORY_ORDER = ("security", "integrity", "policy", "availability", "cancellation", "internal")

#: The effect column, worded for a reader deciding whether to retry: never a guess, never
#: certainty the code cannot claim.
_EFFECT_WORDS = {
    EFFECT_NONE: "Nothing was sent, signed or changed",
    EFFECT_CONTACTED: "A request reached an external service; no payment moved",
    EFFECT_UNCERTAIN: "Outcome unknown — check state before retrying",
    EFFECT_LOCAL: "A local record/change happened",
}

_DOC_PATH = pathlib.Path(__file__).resolve().parents[1] / "docs" / "ERROR_BOOK.md"


def _yn(value: object) -> str:
    return "yes" if value else "no"


def _cell(value: object) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def build_error_book_markdown() -> str:
    """Return the full ERROR_BOOK markdown, derived entirely from the catalog."""
    catalog = export_catalog()
    faults = list(catalog["faults"])
    digest = catalog_digest()

    by_category: dict[str, list[dict]] = {}
    for fault in faults:
        by_category.setdefault(str(fault.get("category") or "uncategorized"), []).append(fault)
    ordered_categories = [c for c in _CATEGORY_ORDER if c in by_category]
    ordered_categories += sorted(c for c in by_category if c not in _CATEGORY_ORDER)

    lines: list[str] = [
        "# VOOL Error Book",
        "",
        "<!-- GENERATED FILE — do not hand-edit. Regenerate with "
        "`python -m tools.generate_error_book`. -->",
        "",
        "Every fault VOOL can surface, as a human-readable view of the one canonical fault catalog",
        "(`core/faults/catalog.py`). This document is *derived from* that catalog, not a second copy",
        "of it: the codes, messages and actions below are exactly what the runtime emits.",
        "",
        f"- **Schema:** `{FAULT_SCHEMA}`",
        f"- **Catalog version:** `{CATALOG_VERSION}`",
        f"- **Catalog digest:** `{digest}`",
        f"- **Fault count:** {len(faults)}",
        "",
        "A fault's `code` is stable and safe to match on; `user_message` is what a person sees;",
        "`operator_action` is what to do about it; `authority` names the module that owns the fault.",
        "",
    ]

    for category in ordered_categories:
        rows = sorted(by_category[category], key=lambda f: str(f.get("code")))
        lines.append(f"## {category}")
        lines.append("")
        lines.append(
            "| Code | Severity | Retryable | Retry | Security | Meaning | What may already have happened | Operator action | Authority |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for f in rows:
            lines.append(
                "| `{code}` | {sev} | {retryable} | {retry} | {sec} | {msg} | {effect} | {action} | `{auth}` |".format(
                    code=_cell(f.get("code")),
                    sev=_cell(f.get("severity")),
                    retryable=_yn(f.get("retryable")),
                    retry=_cell(f.get("retry")),
                    sec=_yn(f.get("security_relevant")),
                    msg=_cell(f.get("user_message")),
                    effect=_cell(_EFFECT_WORDS.get(str(f.get("effect") or ""), "")),
                    action=_cell(f.get("operator_action")),
                    auth=_cell(f.get("authority")),
                )
            )
        lines.append("")

    # --- the coverage matrix: every user-facing failure boundary, classified or not -----------
    lines.extend(
        [
            "## Boundary coverage",
            "",
            "Every user-facing failure boundary and the code space that classifies it. A boundary",
            "keeps its OWN closed vocabulary when it predates or sits beside `vool.fault.v1`; what",
            "this matrix promises is that none is silently unclassified -- a boundary with no code",
            "space would have to show that here.",
            "",
            "| Boundary | Classified by | Codes | Where it is mapped | What the user reads | Recovery | Fault records |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for boundary in BOUNDARIES:
        codes = ", ".join(f"`{code}`" for code in boundary.codespace.codes) or "—"
        lines.append(
            "| {name} | {kind} | {codes} | {mapped} | {surface} | {recovery} | {records} |".format(
                name=_cell(boundary.name),
                kind=_cell(boundary.codespace.kind),
                codes=_cell(codes),
                mapped=_cell(boundary.mapped_at),
                surface=_cell(boundary.user_surface),
                recovery=_cell(boundary.recovery),
                records="yes" if boundary.files_fault_records else "own code space",
            )
        )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    _DOC_PATH.write_text(build_error_book_markdown(), encoding="utf-8")
    print(f"wrote {_DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
