"""core/school/policy.py — the scoped policy hierarchy, resolved by intersection.

Layers (goal §6), each stored as JSON at its owning entity:

    SCHOOL (admin)  →  CLASS  →  LESSON (teacher, active window)  →  ASSIGNMENT

A layer may only NARROW. Each field resolves independently:

- ``tool_families``  : set intersection; a layer that names families REMOVES
                       anything not named by it (absent = no constraint).
- ``locality``       : ``local_only`` < ``cloud_allowed`` — the strictest wins.
- ``allowed_models`` : set intersection of (provider, model) pairs.
- ``max_assistance`` : L0–L10 — the minimum wins.
- ``assessment``     : True anywhere → True (assessment floors stack on).

The resolved :class:`EffectiveSchoolPolicy` is frozen at ingress per turn and is
the ONLY object downstream seams read (routing fences, prohibitions, the
assistance gate). Prompt text is never the control (goal §26).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.school import store

# Tool families the school layer can govern. The edition floor (money lanes)
# is BELOW this hierarchy — no school policy can re-add what the edition
# mechanically lacks (goal §4).
GOVERNED_FAMILIES = ("web", "browser", "sandbox", "workspace", "machine", "image", "video", "media")

LOCALITY_LADDER = {"local_only": 0, "cloud_allowed": 1}


def _families(value: Any) -> tuple[str, ...] | None:
    """A PRESENT list is a constraint — even an empty one.

    ``tool_families: []`` means the layer allows NO governed tool family
    (the narrowest reading); an absent field means no constraint. Filtering
    to the governed vocabulary happens after that decision, so a list of
    only-unknown names also constrains to nothing.
    """
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(
        str(item).strip().lower()
        for item in value
        if str(item).strip().lower() in GOVERNED_FAMILIES
    )


def _models(value: Any) -> frozenset[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    items = frozenset(
        f"{str(p).strip().lower()}:{str(m).strip().lower()}"
        for p, m in (entry if isinstance(entry, (list, tuple)) else str(entry).split(":", 1)
                     for entry in value)
        if str(p).strip() and str(m).strip()
    )
    return items or None


def _max_assistance(value: Any) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if 0 <= level <= 10 else None


@dataclass(frozen=True)
class EffectiveSchoolPolicy:
    """The one resolved policy a school turn executes under."""

    school_id: str
    class_id: str = ""
    lesson_id: str = ""
    assignment_id: str = ""
    student_user_id: str = ""
    # Effective values after intersection:
    tool_families: frozenset[str] = frozenset(GOVERNED_FAMILIES)
    locality: str = "cloud_allowed"
    allowed_models: frozenset[str] = frozenset()
    model_allowlist_active: bool = False
    max_assistance: int = 10
    assessment: bool = False
    # Provenance (goal §27 lifecycle): which layers constrained what.
    layers: tuple[dict, ...] = field(default=())

    def permits_family(self, family: str) -> bool:
        return str(family or "").strip().lower() in self.tool_families

    def model_allowed(self, provider_id: str, model_id: str) -> bool:
        if not self.model_allowlist_active:
            return True
        key = f"{str(provider_id or '').strip().lower()}:{str(model_id or '').strip().lower()}"
        return key in self.allowed_models

    def to_context(self) -> dict:
        """The server-stamped projection that rides source_context (reserved key)."""
        return {
            "school_id": self.school_id,
            "class_id": self.class_id,
            "lesson_id": self.lesson_id,
            "assignment_id": self.assignment_id,
            "student_user_id": self.student_user_id,
            "tool_families": sorted(self.tool_families),
            "locality": self.locality,
            "allowed_models": sorted(self.allowed_models),
            "model_allowlist_active": self.model_allowlist_active,
            "max_assistance": self.max_assistance,
            "assessment": self.assessment,
        }


def resolve_policy(
    *,
    school_id: str,
    class_id: str = "",
    lesson_id: str = "",
    assignment_id: str = "",
    student_user_id: str = "",
) -> EffectiveSchoolPolicy:
    """Resolve the effective policy by intersecting every present layer."""
    school = store.get_school(school_id) or {}
    layers_raw: list[tuple[str, dict]] = [("school", school.get("policy") or {})]
    klass = store.get_class(class_id) if class_id else None
    if klass:
        layers_raw.append(("class", klass.get("policy") or {}))
    lesson = store.get_lesson(lesson_id) if lesson_id else None
    if lesson and lesson.get("status") == "active":
        layers_raw.append(("lesson", lesson.get("policy") or {}))
    elif lesson:
        # An ended lesson contributes NOTHING — its grant expired with it
        # (goal §4/§27): the student falls back to class/school policy.
        layers_raw.append(("lesson:expired", {}))
    assignment = store.get_assignment(assignment_id) if assignment_id else None
    if assignment:
        layers_raw.append(("assignment", assignment.get("policy") or {}))

    families: frozenset[str] | None = None
    locality: str | None = None
    models: frozenset[str] | None = None
    allowlist_active = False
    max_assistance: int | None = None
    assessment = False
    provenance: list[dict] = []

    for layer_name, payload in layers_raw:
        if not isinstance(payload, dict) or not payload:
            continue
        constrained: list[str] = []
        fam = _families(payload.get("tool_families"))
        if fam is not None:
            families = frozenset(fam) if families is None else (families & frozenset(fam))
            constrained.append("tool_families")
        loc = str(payload.get("locality") or "").strip().lower()
        if loc in LOCALITY_LADDER:
            locality = loc if locality is None else min(locality, loc, key=lambda l: LOCALITY_LADDER[l])
            constrained.append("locality")
        mods = _models(payload.get("allowed_models"))
        if mods is not None:
            models = mods if models is None else (models & mods)
            allowlist_active = True
            constrained.append("allowed_models")
        level = _max_assistance(payload.get("max_assistance"))
        if level is not None:
            max_assistance = level if max_assistance is None else min(max_assistance, level)
            constrained.append("max_assistance")
        if bool(payload.get("assessment")):
            assessment = True
            constrained.append("assessment")
        if constrained:
            provenance.append({"layer": layer_name, "constrained": constrained})

    return EffectiveSchoolPolicy(
        school_id=school_id,
        class_id=class_id,
        lesson_id=lesson_id,
        assignment_id=assignment_id,
        student_user_id=student_user_id,
        tool_families=families if families is not None else frozenset(GOVERNED_FAMILIES),
        locality=locality or "cloud_allowed",
        allowed_models=models or frozenset(),
        model_allowlist_active=allowlist_active,
        max_assistance=max_assistance if max_assistance is not None else 10,
        assessment=assessment,
        layers=tuple(provenance),
    )


def school_teacher_policy(school_id: str) -> EffectiveSchoolPolicy:
    """Teachers/admins chat under the school layer only (no lesson ceiling)."""
    return resolve_policy(school_id=school_id)
