#!/usr/bin/env python3
"""Localization audit: coverage, placeholder integrity, expansion budgets.

Checks per locale against the en baseline:
  1. coverage      — which baseline keys are missing
  2. placeholders  — every {var}/{plural} arg in en must exist in the translation
  3. provenance    — generated locales must carry human_reviewed=false
  4. expansion     — pseudo-locale inflation vs a 2x budget on short UI labels
                     (.tab/.button/.label style keys) so layout breakage is caught

Exit code is nonzero when hard checks fail, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "registry"

PLACEHOLDER_NAME = re.compile(r"\{([a-zA-Z_]\w*)[,}]")


def _args_of(template: str) -> set[str]:
    return set(PLACEHOLDER_NAME.findall(template))


def audit(registry_dir: Path = REGISTRY) -> tuple[dict, bool]:
    locales = {}
    for path in sorted(registry_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        locales[data.get("_meta", {}).get("locale", path.stem)] = data
    base = locales["en"]
    base_keys = set(base["strings"])
    problems: list[str] = []
    report: dict = {"locales": {}, "baseline_keys": len(base_keys)}

    short_label = re.compile(r"\.(tab|button|label)\.")

    for locale, data in sorted(locales.items()):
        if locale == "en":
            continue
        meta = data.get("_meta", {})
        entry: dict = {"label": meta.get("label", locale), "generated": meta.get("generated", False)}
        strings = data.get("strings", {})

        missing = sorted(base_keys - set(strings))
        extra = sorted(set(strings) - base_keys)
        entry["missing_keys"] = len(missing)
        entry["missing_sample"] = missing[:10]
        entry["extra_keys"] = extra

        bad_placeholders = []
        for key in base_keys & set(strings):
            want, got = _args_of(base["strings"][key]), _args_of(strings[key])
            if not want <= got:
                bad_placeholders.append({"key": key, "missing_vars": sorted(want - got)})
        entry["placeholder_errors"] = bad_placeholders

        if meta.get("generated") and meta.get("human_reviewed", False):
            problems.append(f"{locale}: generated locale claims human_reviewed without review record")
        entry["provenance_ok"] = not (meta.get("generated") and meta.get("human_reviewed"))

        # RTL flag consistency for torture fixture
        if locale.lower().startswith("ar"):
            entry["direction"] = "rtl"

        # expansion budget only applies to the pseudo-locale
        if locale == "qya-pseudo":
            ratios = []
            for key in base_keys:
                en_len = max(len(base["strings"][key]), 1)
                ratios.append(len(strings[key]) / en_len)
            mean_ratio = sum(ratios) / len(ratios)
            entry["mean_expansion_ratio"] = round(mean_ratio, 2)
            over = [
                k for k in base_keys
                if short_label.search(k) and len(strings[k]) / max(len(base["strings"][k]), 1) > 2.0
            ]
            entry["short_labels_over_budget"] = over

        hard_failures = bool(bad_placeholders)
        report["locales"][locale] = entry
        if hard_failures:
            problems.append(f"{locale}: placeholder mismatches: {bad_placeholders[:5]}")

    return report, not problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    report, ok = audit(args.registry)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("AUDIT:", "PASS" if ok else "FAIL", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
