"""v14.6 hardening item 5 (ASTRA Pro review, 2026-10-07): a conclusion that needs the whole scope ("never", "the first",
"the latest", "all of them") is not delivered as fact when the evidence compiler shows only part of the scoped candidate
set (core.evidence_compiler.scoped_coverage). The guard reads the compiler's typed completeness (obligation class and
coverage) and the reply's own words. It never invents a value: an ABSENCE conclusion on a partial view is replaced by
a scoped statement; an EXTREMUM or LIST_ALL conclusion keeps its candidate and is scoped to the records seen; a view
with no candidate at all (nothing unseen) is exhaustive and the reply is untouched, so a clean refusal stays clean.
Contributor: sls_0x."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_ABSENCE_CONCLUSION_RE = re.compile(r"\b(?:never|no\s+(?:record|mention|note|sign|trace)\b|not\s+(?:mentioned|recorded|noted)|nothing\s+(?:in|about|on)\b|(?:didn'?t|did\s+not|haven'?t|have\s+not|hadn'?t)\s+(?:ever\s+)?(?:mention|say|tell|record|note|talk)|at\s+no\s+point|there\s+is\s+no\b|i\s+(?:can'?t|cannot|couldn'?t)\s+find\s+any)\b", re.IGNORECASE)
_EXTREMUM_CONCLUSION_RE = re.compile(r"\b(?:first|earliest|oldest|very\s+first|latest|last|most\s+recent(?:ly)?|newest|final)\b", re.IGNORECASE)
_LIST_CONCLUSION_RE = re.compile(r"\b(?:all\s+(?:of\s+)?(?:them|the|your)|every(?:thing)?|the\s+(?:complete|full|whole|entire)\s+(?:list|set)|the\s+only\b|in\s+total|altogether|that'?s\s+(?:all|everything|the\s+lot))\b", re.IGNORECASE)
_WITHDRAWAL_RE = re.compile(r"\b(?:records?\s+(?:i\s+can\s+see|shown|checked)|not\s+established|may\s+be\s+incomplete|do\s+not\s+settle)\b", re.IGNORECASE)


@dataclass
class CoverageDecision:
    executed: bool
    reason: str
    obligation_class: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    conclusion: str = ""
    text: str = ""
    changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"execution": "executed" if self.executed else "not_executed", "owner": "core.evidence_kernel.coverage_guard.guard_coverage", "reason": self.reason,
                "obligation_class": self.obligation_class, "coverage": dict(self.coverage), "conclusion": self.conclusion, "changed": self.changed}


def conclusion_kind(reply: str, obligation_class: str) -> str:
    """Which scope-wide conclusion the reply asserts, for the class the question has: '' when none."""
    r = str(reply or "")
    if _WITHDRAWAL_RE.search(r):
        return ""
    if obligation_class == "ABSENCE" and _ABSENCE_CONCLUSION_RE.search(r):
        return "absence"
    if obligation_class == "EXTREMUM" and _EXTREMUM_CONCLUSION_RE.search(r):
        return "extremum"
    if obligation_class == "LIST_ALL" and (_LIST_CONCLUSION_RE.search(r) or re.search(r"^\s*(?:[-*•]|\d+[.)])\s", r, re.MULTILINE)):
        return "list"
    if obligation_class == "AGGREGATE" and re.search(r"\b(?:in\s+total|altogether|total(?:s|led)?|sum|average|overall)\b", r, re.IGNORECASE):
        return "aggregate"
    return ""


def guard_coverage(*, reply: Any, compiler_telemetry: Mapping[str, Any] | None) -> CoverageDecision:
    tel = dict(compiler_telemetry or {})
    ob = tel.get("obligation") or {}
    cls = str(ob.get("class") or "")
    cov = dict(tel.get("coverage") or {})
    text = str(reply or "")
    if not cls or not cov:
        return CoverageDecision(False, "no_typed_completeness", text=text)
    if not ob.get("needs_coverage"):
        return CoverageDecision(False, "class_needs_no_coverage", cls, cov, text=text)
    if cov.get("exhaustive", True):
        return CoverageDecision(True, "scope_exhausted", cls, cov, text=text)
    kind = conclusion_kind(text, cls)
    if not kind:
        return CoverageDecision(True, "no_scope_wide_conclusion", cls, cov, text=text)
    shown, cands = int(cov.get("shown") or 0), int(cov.get("candidates") or 0)
    scope = f"{shown} of {cands} matching records"
    if kind == "absence":
        out = f"The records I can see ({scope}) do not settle whether you ever said that; the rest were not checked."
    elif kind == "extremum":
        out = text.rstrip() + f" (Among the {scope} I can see; an earlier or later one may exist in the records not shown, so this is not established as the first or the latest.)"
    elif kind == "list":
        out = text.rstrip() + f" (From {scope}; the list may be incomplete.)"
    else:
        out = text.rstrip() + f" (From {scope}; records not shown are not in this figure.)"
    return CoverageDecision(True, "qualified_" + kind, cls, cov, kind, out, out != text)
