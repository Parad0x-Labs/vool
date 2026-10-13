"""Explain overlaps with native authority; these notices never grant or replace it."""
from __future__ import annotations

import re

AUTHORITY_NOTICE = (
    "Skills contribute instructions, not authority. VOOL's native rules, owner settings, "
    "budgets, currency handling, permissions and approval requirements take precedence. "
    "A skill cannot grant access, raise a spending limit or approve an action."
)

_CONTROLS = {
    "spending": (r"\b(?:spend(?:ing)?|budget|payment|daily limit|cost cap|wallet)\b",
        "Spending instructions overlap VOOL's native budgets and spend approval. Configured limits, freezes and approval requirements prevail.",
        ["core/effect_budget.py", "core/spend_authorization.py", "core/wallet_spend_policy.py"]),
    "currency": (r"\b(?:currenc(?:y|ies)|usdc|usd|eur|sol|bitcoin|zcash)\b",
        "Currency preferences overlap VOOL's native currency and payment handling. A skill cannot authorize a payment or enable an unsupported asset.",
        ["core/currency_travel_spend.py", "core/spend_authorization.py"]),
    "permissions": (r"\b(?:permission|allowed.tools|filesystem|credential|secret|network access)\b",
        "Requested tools and permissions overlap native access controls. Declarations describe requests; only VOOL's owner authorization can grant them.",
        ["core/plugin_lifecycle.py", "core/plugin_executor.py", "core/mode_permission_policy.py"]),
    "approval": (r"\b(?:approv(?:al|e)|consent|confirmation|automatic execution)\b",
        "Approval instructions overlap native approval controls. A skill's text is never an owner approval or OS consent.",
        ["core/spend_authorization.py", "core/os_consent_gate.py", "core/effect_gateway.py"]),
    "model-settings": (r"\b(?:model selection|provider|api key|model budget)\b",
        "Model preferences remain subject to owner configuration, provider availability and native spending limits.",
        ["core/model_selection_policy.py", "core/model_spend_ledger.py"]),
}
_OVERRIDE = re.compile(
    r"\b(?:override|ignore|bypass|disable|remove|supersede)\b.{0,80}\b"
    r"(?:vool|native rules|core rules|spend(?:ing)? approval|budget(?:s)?|permission checks|consent checks)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(?:never|do not|don't|must not|cannot|can't|refuse to|prevent|avoid)\b", re.IGNORECASE)
CONTROL_IDS = frozenset(_CONTROLS)


def notices(controls) -> list[dict]:
    return [{"control": control, "classification": "native_control_overlap", "severity": "info",
             "message": _CONTROLS[control][1], "native_owners": _CONTROLS[control][2], "grants_authority": False}
            for control in sorted(set(controls)) if control in _CONTROLS]


def analyze(files: dict[str, bytes]) -> dict:
    text_files = {}
    for name, raw in files.items():
        if name.endswith((".md", ".json", ".yaml", ".yml", ".txt")):
            try:
                text_files[name] = raw.decode("utf-8")
            except UnicodeError:
                continue
    combined = "\n".join(text_files.values())
    overlaps = notices(control for control, (pattern, _, _) in _CONTROLS.items() if re.search(pattern, combined, re.IGNORECASE))
    conflicts = []
    for name, text in text_files.items():
        for clause in re.split(r"[\n.!?;]", text):
            match = _OVERRIDE.search(clause)
            if match and not _NEGATED.search(clause[:match.start()]):
                conflicts.append({"check": "native-boundaries", "path": name,
                    "reason": "Possible instruction to override a native control. Revise it; independent instruction reviews are also required."})
                break
    return {"authority_notice": AUTHORITY_NOTICE, "native_control_overlaps": overlaps,
            "conflicts": conflicts, "coverage": "heuristic notices; independent policy review and native effect gates remain required"}
