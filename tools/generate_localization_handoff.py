"""Generate ``config/localization-handoff.json`` — the key/parameter manifest for the
localization worker.

The fault family's presentation keys are ``fault.<code>.message`` (mirrored byte-exactly in
``core/i18n/catalogs/en.json`` by a pinned test). This manifest lists every key the fault
registry implies, with its English source, its parameters (``{name}``-style placeholders found
in the message), its stability contract (codes are append-only; a message meaning change bumps
the catalog version), and the machine-independent status of the codes themselves (codes are
NEVER translated; only the presentation strings are).

Regenerate:  PYTHONPATH=<repo> python -m tools.generate_localization_handoff
"""

from __future__ import annotations

import json
import pathlib
import re

from core.faults.boundaries import BOUNDARIES
from core.faults.catalog import CATALOG_VERSION, FAULT_SCHEMA, all_specs

_OUT_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "localization-handoff.json"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def build_handoff() -> dict[str, object]:
    keys = []
    for spec in all_specs():
        keys.append(
            {
                "key": f"fault.{spec.code}.message",
                "english": spec.user_message,
                "parameters": sorted(set(_PLACEHOLDER_RE.findall(spec.user_message))),
                "context": (
                    f"Shown when the failure classified as '{spec.code}' reaches the user. "
                    f"Effect state '{spec.effect}' must remain truthful in any wording: never "
                    "translate an 'unknown outcome' into an implied 'nothing happened'."
                ),
            }
        )
    return {
        "schema": "vool.localization-handoff/1",
        "generated_for": "Worker B (localized strings & shared UI presentation)",
        "fault_schema": FAULT_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "contract": [
            "Machine codes (fault codes, effect states, storage states) are language-independent and never translated.",
            "Keys are append-only: a key is never renamed; a meaning change bumps catalog_version and re-handoff.",
            "A translated message must preserve the effect-state truth of the English source (no certainty the source does not claim).",
            "This manifest is generated from core/faults/catalog.py; regenerate after registry changes, never hand-edit.",
        ],
        "keys": keys,
        "boundary_states": [
            {"boundary": b.name, "codespace": b.codespace.kind}
            for b in BOUNDARIES
        ],
    }


def main() -> int:
    payload = build_handoff()
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_OUT_PATH} ({len(payload['keys'])} message keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
