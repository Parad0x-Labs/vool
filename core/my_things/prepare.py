"""Deterministic drafts from saved facts. No sender, network client or output cache."""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from datetime import date

from core.my_things.store import ThingsError


def warranty(record: dict, *, today: date | None = None) -> dict:
    end = record.get("warranty_end", "")
    if not end:
        return {"state": "unknown", "end": ""}
    end_date = date.fromisoformat(end)
    return {"state": "within" if end_date >= (today or date.today()) else "expired", "end": end, "basis": "saved end date; coverage and eligibility have not been verified"}


def _text(value):
    if not isinstance(value, str) or len(value) > 6000:
        raise ThingsError("bad_text", "Use a shorter description.")
    return value.strip()


def _specs(text):
    # Compare only explicit key:value lines. No inference of compatible connectors,
    # voltage ranges or standards from model numbers or product names.
    facts = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and value.strip():
            normalized = key.strip().casefold()
            if normalized in facts and facts[normalized] != value.strip():
                raise ThingsError("conflicting_specs", "The same detail has different values. Check the text before comparing.")
            facts[normalized] = value.strip()
    return facts


def prepare(record, *, kind, issue="", observed="", arrived=None):
    if kind not in {"return", "repair", "warranty", "delivery", "fits"}:
        raise ThingsError("bad_action", "Choose a return, repair, warranty or comparison.")
    issue, observed = _text(issue), _text(observed)
    result = {"draft_only": True, "sent": False}
    if kind == "warranty":
        return {**result, "warranty": warranty(record)}
    if kind in {"return", "repair"}:
        if not issue:
            raise ThingsError("issue_required", "Describe what happened before preparing a request.")
        facts = ["Item: " + record["name"]]
        for key, label in (("maker", "Maker"), ("model", "Model"), ("serial", "Serial"), ("bought_on", "Purchase date"), ("bought_from", "Seller"), ("price", "Price"), ("warranty_end", "Saved warranty end")):
            if record.get(key):
                facts.append(label + ": " + record[key])
        missing = [label for key, label in (("bought_on", "Purchase date"), ("bought_from", "Seller"), ("warranty_end", "Warranty end")) if not record.get(key)]
        draft = ("Draft request for " + kind + "\n\n" + "\n".join(facts) + "\n\nWhat happened:\n" + issue
                 + "\n\nPlease let me know the next steps and any documents you need.\n\n"
                 + "This draft does not establish eligibility for a return or warranty service.")
        return {**result, "draft": draft, "unknown": missing, "attachments": [{"name": f["name"], "kind": f["kind"]} for f in record.get("files", []) if f["kind"] in {"receipt", "photo"}]}
    if kind == "fits":
        saved, photographed = _specs(record.get("specs", "")), _specs(observed)
        matches, conflicts, unknown = [], [], []
        for key in sorted(set(saved) | set(photographed)):
            if key not in saved or key not in photographed:
                unknown.append(key)
            elif saved[key].casefold() == photographed[key].casefold():
                matches.append(key + ": " + saved[key])
            else:
                conflicts.append(key + ": " + saved[key] + " / " + photographed[key])
        if not saved or not photographed:
            unknown.append("Enter the saved specifications and the words on the box as detail: value lines.")
        unknown.append("Overall compatibility has not been verified; matching words alone do not prove that it fits.")
        return {**result, "matches": matches, "conflicts": conflicts, "unknown": unknown}
    expected = list(dict.fromkeys(line.strip() for line in record.get("order_items", "").splitlines() if line.strip()))
    if not expected:
        return {**result, "expected": [], "matches": [], "conflicts": [], "unknown": ["No order items were saved. Enter one item per line in the saved order details."]}
    if not isinstance(arrived, list) or any(not isinstance(v, str) or v not in expected for v in arrived):
        raise ThingsError("bad_arrived", "Choose arrived items from this order.")
    # Text on a packing slip is evidence of a listing, never of physical arrival.
    mentioned = [item for item in expected if re.search(r"(?<!\w)" + re.escape(item) + r"(?!\w)", observed, re.IGNORECASE)]
    missing = [item for item in expected if item not in arrived]
    return {**result, "expected": expected, "mentioned_on_note": mentioned, "confirmed_arrived": list(dict.fromkeys(arrived)), "not_confirmed_arrived": missing,
            "matches": mentioned, "conflicts": [], "unknown": missing}


def packet(record, *, kind, issue):
    if kind not in {"return", "repair"}:
        raise ThingsError("bad_action", "Choose a return or repair packet.")
    result = prepare(record, kind=kind, issue=issue)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("request-draft.txt", result["draft"])
        archive.writestr("facts.json", json.dumps({k: record[k] for k in ("name", "maker", "model", "serial", "bought_on", "bought_from", "price", "warranty_end") if record.get(k)}, ensure_ascii=False, indent=2))
        for file in record.get("files", []):
            if file["kind"] in {"receipt", "photo"}:
                archive.writestr("attachments/" + file["id"] + "-" + file["name"], base64.b64decode(file["data"], validate=True))
    return out.getvalue()
