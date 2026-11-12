"""Generate ``docs/ERROR_BOOK.md`` from the canonical fault catalog (``vool.fault.v1``).

The ERROR_BOOK is a HUMAN VIEW of ``core/faults/catalog.py`` — it is generated FROM the catalog and
never forks it. Every row is stamped with the catalog schema, version and digest, so a doc that has
drifted from the taxonomy is detectable (``tests/test_error_book_matches_catalog.py`` fails closed).

Two generated files, both from the catalog:

* ``docs/ERROR_BOOK.md`` -- the reader's book, published on the docs site. The stamped header
  (schema, version, digest, count) and how to read an entry; one summary table per category,
  for scanning; then ONE SECTION PER CODE, headed ``### <code>`` -- the stable documentation
  anchor (the docs site turns the heading into ``#`` + ``doc_anchor(code)``). It names no
  internal module: what happened, what may already have happened, what to do next, retrying.
* ``docs/FAULT_BOUNDARIES.md`` -- the developer view, kept off the docs site: which module owns
  each code, and the boundary coverage matrix (every user-facing failure boundary and the
  closed code space that classifies it).

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
    RETRY_AFTER_CHANGE,
    RETRY_LATER,
    RETRY_NEVER,
    RETRY_NOW,
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

#: Retry guidance in the reader's voice, derived from the policy vocabulary -- never prose
#: a contributor typed per code (one meaning per policy, everywhere).
_RETRY_WORDS = {
    RETRY_NOW: "Trying again now may work.",
    RETRY_LATER: "Try again later: what stopped this clears with time.",
    RETRY_AFTER_CHANGE: "Try again after making the change this entry names.",
    RETRY_NEVER: "Do not send the same request again unchanged.",
}

_DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
_DOC_PATH = _DOCS / "ERROR_BOOK.md"
_DEV_DOC_PATH = _DOCS / "FAULT_BOUNDARIES.md"

_GENERATED_NOTE = (
    "<!-- GENERATED FILE — do not hand-edit. Regenerate with "
    "`python -m tools.generate_error_book`. -->"
)


def _cell(value: object) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def _stamp(faults: list[dict], digest: str) -> list[str]:
    return [
        f"- **Schema:** `{FAULT_SCHEMA}`",
        f"- **Catalog version:** `{CATALOG_VERSION}`",
        f"- **Catalog digest:** `{digest}`",
        f"- **Fault count:** {len(faults)}",
        "",
    ]


def _by_category(faults: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    by_category: dict[str, list[dict]] = {}
    for fault in faults:
        by_category.setdefault(str(fault.get("category") or "uncategorized"), []).append(fault)
    ordered = [c for c in _CATEGORY_ORDER if c in by_category]
    ordered += sorted(c for c in by_category if c not in _CATEGORY_ORDER)
    return ordered, by_category


def _code_section(fault: dict) -> list[str]:
    """The per-code reference block. The ``### <code>`` heading IS the stable anchor."""
    security = " · **Security-relevant:** yes" if fault.get("security_relevant") else ""
    return [
        f"### {fault['code']}",
        "",
        f"**{fault['title']}**",
        "",
        f"- **What happened:** {fault['user_message']}",
        f"- **What may already have happened:** {_EFFECT_WORDS.get(str(fault.get('effect') or ''), '')}",
        f"- **What to do next:** {fault['operator_action']}",
        f"- **Trying again:** {_RETRY_WORDS.get(str(fault.get('retry') or ''), '')}",
        f"- **Severity:** {fault['severity']} · **Category:** {fault['category']}{security}",
        "",
    ]


def build_error_book_markdown() -> str:
    """Return the reader's ERROR_BOOK markdown, derived entirely from the catalog."""
    catalog = export_catalog()
    faults = list(catalog["faults"])
    digest = catalog_digest()
    ordered_categories, by_category = _by_category(faults)

    lines: list[str] = [
        "# VOOL Error Book",
        "",
        _GENERATED_NOTE,
        "",
        "Every error VOOL can show you, generated from the app's own error catalog: the codes,",
        "messages and next steps below are exactly what the app shows.",
        "",
        *_stamp(faults, digest),
        "Each code is stable and never reused for a different meaning. Every code has its own",
        "section below whose heading is the code itself, so searching this page for the code as",
        "you were given it finds its section, and a link to the section is this page's address",
        "followed by `#` and the code with underscores as hyphens. Each section says what",
        "happened, what may already have happened, what to do next, and whether trying again",
        "can help. If a code you were given is not in this book, it is not from this version of",
        "VOOL: check which version showed it, or report it with the code as written.",
        "",
    ]

    for category in ordered_categories:
        rows = sorted(by_category[category], key=lambda f: str(f.get("code")))
        lines.append(f"## {category.capitalize()}")
        lines.append("")
        lines.append("| Code | Title | Severity | What may already have happened |")
        lines.append("|---|---|---|---|")
        for f in rows:
            lines.append(
                "| [`{code}`](#{anchor}) | {title} | {sev} | {effect} |".format(
                    code=_cell(f.get("code")),
                    anchor=_cell(f.get("doc_anchor")),
                    title=_cell(f.get("title")),
                    sev=_cell(f.get("severity")),
                    effect=_cell(_EFFECT_WORDS.get(str(f.get("effect") or ""), "")),
                )
            )
        lines.append("")

    # --- the per-code reference: one stable section per code ------------------------------
    lines.extend(
        [
            "## Reference by code",
            "",
            "Every code, in alphabetical order. The section heading is the code's stable anchor.",
            "",
        ]
    )
    for f in sorted(faults, key=lambda f: str(f.get("code"))):
        lines.extend(_code_section(f))

    return "\n".join(lines).rstrip("\n") + "\n"


def build_fault_boundaries_markdown() -> str:
    """Return the developer view: code owners and the boundary coverage matrix."""
    catalog = export_catalog()
    faults = list(catalog["faults"])
    digest = catalog_digest()
    ordered_categories, by_category = _by_category(faults)

    lines: list[str] = [
        "# Fault owners and boundary coverage",
        "",
        _GENERATED_NOTE,
        "",
        "The developer view of the fault catalog (`core/faults/catalog.py`): which module owns",
        "each code, and every user-facing failure boundary with the code space that classifies",
        "it. Readers want [the Error Book](ERROR_BOOK.md); this file stays off the docs site.",
        "",
        *_stamp(faults, digest),
        "## Code owners",
        "",
        "| Code | Category | Retry | Security | Owning module |",
        "|---|---|---|---|---|",
    ]
    for category in ordered_categories:
        for f in sorted(by_category[category], key=lambda f: str(f.get("code"))):
            lines.append(
                "| `{code}` | {cat} | {retry} | {sec} | `{auth}` |".format(
                    code=_cell(f.get("code")),
                    cat=_cell(f.get("category")),
                    retry=_cell(f.get("retry")),
                    sec="yes" if f.get("security_relevant") else "no",
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
    _DEV_DOC_PATH.write_text(build_fault_boundaries_markdown(), encoding="utf-8")
    print(f"wrote {_DEV_DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
