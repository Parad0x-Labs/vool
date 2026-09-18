from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from core import policy_engine
from storage.db import get_connection


@dataclass
class Persona:
    persona_id: str
    display_name: str
    spirit_anchor: str
    tone: str
    verbosity: str
    risk_tolerance: float
    explanation_depth: float
    execution_style: str
    strictness: float
    personality_locked: bool = True

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

# The persona row is SEEDED into the database, so the product name reached the model from a persisted
# row rather than from code -- the "database dependency" case in docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md.
# Changing the seed alone fixes only NEW installs; every existing install already has "Vool" stored.
# Map the legacy default at READ time (the row is never rewritten, so this stays reversible and a name
# the user deliberately chose is untouched), and change the seed for new installs.
_LEGACY_PERSONA_NAMES = {"vool"}


def _vool_display_name(stored: object) -> str:
    name = str(stored or "").strip()
    return "VOOL" if not name or name.lower() in _LEGACY_PERSONA_NAMES else name


def _vool_spirit_anchor(stored: object) -> str:
    """The seeded anchor describes the assistant by name ("Vool is a local-first ... intelligence"),
    and it is fed to the model as persona context, so it renames with the display name."""
    text = str(stored or "")
    return re.sub(r"\bNulla\b", "VOOL", text)


def load_active_persona(persona_id: str = "default") -> Persona:
    from storage.migrations import run_migrations

    run_migrations()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT persona_id, display_name, spirit_anchor, tone, verbosity,
                   risk_tolerance, explanation_depth, execution_style, strictness,
                   personality_locked
            FROM persona_profiles
            WHERE persona_id = ?
            LIMIT 1
            """,
            (persona_id,),
        ).fetchone()

        if not row:
            raise ValueError(f"Persona '{persona_id}' not found. Run migrations first.")

        persona = Persona(
            persona_id=row["persona_id"],
            display_name=_vool_display_name(row["display_name"]),
            spirit_anchor=_vool_spirit_anchor(row["spirit_anchor"]),
            tone=row["tone"],
            verbosity=row["verbosity"],
            risk_tolerance=float(row["risk_tolerance"]),
            explanation_depth=float(row["explanation_depth"]),
            execution_style=row["execution_style"],
            strictness=float(row["strictness"]),
            personality_locked=bool(row["personality_locked"]),
        )

        if policy_engine.get("personality.persona_core_locked", True):
            persona.personality_locked = True

        return persona
    finally:
        conn.close()

def update_local_persona(
    persona_id: str,
    *,
    display_name: str | None = None,
    tone: str | None = None,
    verbosity: str | None = None,
    risk_tolerance: float | None = None,
    explanation_depth: float | None = None,
    execution_style: str | None = None,
    strictness: float | None = None,
    spirit_anchor: str | None = None,
) -> None:
    """
    Local-only persona tuning.
    This must never be called from swarm/web handlers.
    """
    if not policy_engine.get("personality.allow_local_persona_tuning", True):
        raise PermissionError("Local persona tuning disabled by policy.")

    current = load_active_persona(persona_id)

    if current.personality_locked and policy_engine.get("personality.persona_core_locked", True):
        if spirit_anchor is not None:
            raise PermissionError("Spirit anchor is locked and cannot be changed automatically.")

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE persona_profiles
            SET display_name = ?,
                spirit_anchor = ?,
                tone = ?,
                verbosity = ?,
                risk_tolerance = ?,
                explanation_depth = ?,
                execution_style = ?,
                strictness = ?,
                updated_at = ?
            WHERE persona_id = ?
            """,
            (
                display_name if display_name is not None else current.display_name,
                current.spirit_anchor if spirit_anchor is None else spirit_anchor,
                tone if tone is not None else current.tone,
                verbosity if verbosity is not None else current.verbosity,
                risk_tolerance if risk_tolerance is not None else current.risk_tolerance,
                explanation_depth if explanation_depth is not None else current.explanation_depth,
                execution_style if execution_style is not None else current.execution_style,
                strictness if strictness is not None else current.strictness,
                _utcnow(),
                persona_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

def render_with_persona(text: str, persona: Persona) -> str:
    """
    Lightweight style wrapper for v1.
    Does NOT alter factual content, only presentation tone/shape.
    """
    text = text.strip()

    if persona.tone == "teacher":
        return f"{persona.display_name}: Let’s walk through it.\n\n{text}"
    if persona.tone == "calm":
        return f"{persona.display_name}: {text}"
    if persona.tone == "direct":
        return text
    if persona.tone == "savage":
        return f"{persona.display_name}: Straight answer.\n{text}"

    return text
