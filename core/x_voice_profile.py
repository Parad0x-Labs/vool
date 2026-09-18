"""Operator-approved X voice evidence: the ONE store of how the operator writes.

Laws (each enforced here, each pinned by test):

- **Operator-approved only.** A sample enters through an explicit act (module API, HTTP
  door, or the operator editing their own VOOL_HOME config) — never inferred from chat
  history, never from any writer the operator did not approve. Identity, biography or
  experiences are NEVER inferred from samples: the injected evidence is surface style
  statistics plus verbatim excerpts the operator approved, nothing more.
- **Scoped.** Scopes are ``global`` and ``project:<key>``. A project-scoped turn resolves
  its own scope first, then global; a scope never receives another scope's samples.
- **Restart-preserving by construction.** The disk is the store: every read hits the file,
  there is no process-local copy to lose or to leak between turns. Writes are atomic
  (tmp + fsync + rename).
- **Versioned.** Every change bumps the scope's integer version; the version served into a
  draft is the ``XDrafT.voice_profile_version``.
- **Privacy-scanned.** A sample that carries secret material or machine-identity markers is
  REFUSED at write time with a typed refusal; the render path re-checks before anything
  reaches a prompt.
- **Inspect / edit / reset / pause.** Typed operations for all four; pause stops injection
  without destroying the operator's evidence.
- **Bounded.** At most MAX_PROMPT_CHARS of voice evidence enters any prompt.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import user_runtime_default

STORE_FILENAME = "config/x_voice_profiles.json"

MAX_SAMPLES_PER_SCOPE = 12
MAX_SAMPLE_CHARS = 2_000
MAX_PROMPT_CHARS = 1_200
MAX_EXCERPT_CHARS = 240
MAX_EXCERPTS = 3

# Absolute-path shapes that must never travel inside voice evidence or copy.
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:/Users?/[\w.\-]+/|/home/[\w.\-]+/|[A-Za-z]:\\Users\\[\w.\-]+\\)"
)

# The demand-signal intent (core.tool_demand_signals) that marks a turn as asking for voice.
VOICE_INTENT = "editorial.voice"


@dataclass(frozen=True)
class VoiceSample:
    id: str
    text: str
    added_at: str
    source: str


# --- store primitives: the disk is the store ---------------------------------------


def _store_path(home: Path | None = None) -> Path:
    base = Path(home) if home is not None else Path(
        os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or user_runtime_default()
    )
    return base / STORE_FILENAME


def _read_store(home: Path | None = None) -> dict[str, Any]:
    """Read from disk on EVERY call: restart-preservation and cross-turn honesty by
    construction, exactly like the skills_enabled store."""
    try:
        data = json.loads(_store_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_store(store: dict[str, Any], home: Path | None = None) -> None:
    path = _store_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(store, ensure_ascii=False, indent=1))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _scope_state(store: dict[str, Any], scope: str) -> dict[str, Any]:
    scopes = store.get("scopes")
    if not isinstance(scopes, dict):
        scopes = {}
        store["scopes"] = scopes
    state = scopes.get(scope)
    if not isinstance(state, dict):
        state = {"version": 0, "samples": [], "paused": False}
        scopes[scope] = state
    return state


def _privacy_refusal(text: str) -> str:
    from core.privacy_guard import text_privacy_risks
    from core.secret_redaction import contains_secret

    if contains_secret(text):
        return "secret_material"
    if _ABSOLUTE_PATH_PATTERN.search(text):
        return "local_path"
    try:
        if text_privacy_risks(text):
            return "privacy_risk"
    except Exception:
        return "privacy_risk"
    return ""


# --- typed operations ---------------------------------------------------------------


def add_samples(
    scope: str,
    texts: list[str],
    *,
    source: str = "operator",
    home: Path | None = None,
) -> dict[str, Any]:
    """Add operator-approved samples. Privacy-scanned BEFORE anything is stored."""
    scope = _clean_scope(scope)
    store = _read_store(home)
    state = _scope_state(store, scope)
    added: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for index, raw in enumerate(list(texts or [])):
        text = " ".join(str(raw or "").split())
        if not text:
            refused.append({"index": index, "reason": "empty"})
            continue
        if len(text) > MAX_SAMPLE_CHARS:
            refused.append({"index": index, "reason": "too_long"})
            continue
        reason = _privacy_refusal(text)
        if reason:
            refused.append({"index": index, "reason": reason})
            continue
        sample = VoiceSample(
            id=f"s{int(time.time() * 1000) % 10**10}-{len(state['samples']) + len(added)}",
            text=text,
            added_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            source=str(source or "operator"),
        )
        added.append(
            {"id": sample.id, "text": sample.text, "added_at": sample.added_at,
             "source": sample.source}
        )
    if added:
        state["samples"] = (list(state["samples"]) + added)[-MAX_SAMPLES_PER_SCOPE:]
        state["version"] = int(state.get("version") or 0) + 1
    if added or refused:
        try:
            _write_store(store, home)
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not persist"}
    status = "ok" if added and not refused else ("refused" if refused and not added else "partial")
    return {
        "status": status,
        "scope": scope,
        "version": state["version"],
        "added": len(added),
        "refused": refused,
    }


def remove_sample(scope: str, sample_id: str, *, home: Path | None = None) -> dict[str, Any]:
    scope = _clean_scope(scope)
    store = _read_store(home)
    state = _scope_state(store, scope)
    kept = [s for s in state["samples"] if s.get("id") != str(sample_id)]
    if len(kept) == len(state["samples"]):
        return {"status": "unknown_sample", "scope": scope}
    state["samples"] = kept
    state["version"] = int(state.get("version") or 0) + 1
    _write_store(store, home)
    return {"status": "ok", "scope": scope, "version": state["version"]}


def reset_scope(scope: str, *, home: Path | None = None) -> dict[str, Any]:
    """Forget every sample in the scope. The version keeps rising: resets are auditable."""
    scope = _clean_scope(scope)
    store = _read_store(home)
    state = _scope_state(store, scope)
    state["samples"] = []
    state["paused"] = False
    state["version"] = int(state.get("version") or 0) + 1
    _write_store(store, home)
    return {"status": "ok", "scope": scope, "version": state["version"]}


def set_paused(scope: str, paused: bool, *, home: Path | None = None) -> dict[str, Any]:
    scope = _clean_scope(scope)
    store = _read_store(home)
    state = _scope_state(store, scope)
    state["paused"] = bool(paused)
    _write_store(store, home)
    return {"status": "ok", "scope": scope, "paused": bool(paused)}


def inspect(scope: str, *, home: Path | None = None) -> dict[str, Any]:
    """The operator's own data, back to the operator."""
    scope = _clean_scope(scope)
    state = _scope_state(_read_store(home), scope)
    return {
        "scope": scope,
        "version": int(state.get("version") or 0),
        "paused": bool(state.get("paused")),
        "samples": [dict(s) for s in state["samples"]],
    }


def voice_profile_version(scope: str, *, home: Path | None = None) -> int | None:
    """The scope's version, or None when the operator has never touched this scope.

    Every mutation bumps the version — including removals and resets — so the version a
    draft was written under always names an exact store state.
    """
    state = _scope_state(_read_store(home), _clean_scope(scope))
    return int(state.get("version") or 0) or None


# --- turn integration ----------------------------------------------------------------


def _clean_scope(scope: str) -> str:
    cleaned = str(scope or "").strip()
    if cleaned.startswith("project:") and len(cleaned) > len("project:"):
        return cleaned
    return "global"


def turn_scope(source_context: dict[str, Any] | None) -> str:
    """The voice scope a turn resolves against.

    A turn EXPLICITLY bound to a project resolves that project's scope; a turn with no
    project binding (the server stamps a default workspace path on ordinary chats) resolves
    ``global`` — a default working folder is not a project the operator scoped evidence for.
    """
    ctx = source_context if isinstance(source_context, dict) else {}
    binding = str(ctx.get("workspace_binding") or "").strip().lower()
    if binding == "default":
        return "global"
    for key in ("workspace", "workspace_id", "project"):
        value = str(ctx.get(key) or "").strip()
        if value:
            return _clean_scope(f"project:{value}")
    return "global"


def _style_facts(samples: list[dict[str, Any]]) -> list[str]:
    """Deterministic surface statistics over approved samples — never biography."""
    joined = " ".join(str(s.get("text") or "") for s in samples)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", joined) if s.strip()]
    words = joined.split()
    facts: list[str] = []
    if sentences:
        avg = sum(len(s.split()) for s in sentences) / len(sentences)
        facts.append(f"average sentence ~{avg:.0f} words")
    if words:
        exclam = joined.count("!") / max(1, len(words))
        if exclam > 0.02:
            facts.append("uses exclamations")
        first_person = sum(
            1 for w in words if w.lower().strip(".,!?'\"") in ("i", "i'm", "i've", "my", "me", "we", "our")
        )
        if first_person / max(1, len(words)) > 0.03:
            facts.append("writes in first person")
        if "#" in joined:
            facts.append("uses hashtags")
        if "..." in joined or "…" in joined:
            facts.append("uses ellipses")
    if not facts:
        facts.append("no strong surface habits detected in the approved samples")
    return facts


def voice_segment_for_turn(
    user_text: str, *, source_context: dict[str, Any] | None = None, home: Path | None = None
) -> str:
    """The bounded voice-evidence block for this turn, or "" when the turn must carry none.

    Injected only when the turn itself asks for voice (the editorial.voice demand signal),
    the scope has approved samples, and the scope is not paused.
    """
    from core.tool_demand_signals import resolve_demand_signals

    demands = resolve_demand_signals(str(user_text or ""))
    if VOICE_INTENT not in demands.explicit_intents:
        return ""

    scope = turn_scope(source_context)
    state = _scope_state(_read_store(home), scope)
    samples = [s for s in state["samples"] if str(s.get("text") or "").strip()]
    if not samples or state.get("paused"):
        return ""

    excerpts = []
    for sample in list(reversed(samples))[:MAX_EXCERPTS]:
        text = str(sample["text"])[:MAX_EXCERPT_CHARS]
        excerpts.append(f'  - "{text}"')
    segment = (
        f'[x-editorial voice profile v{state.get("version")} (scope: {scope}) — '
        "operator-approved samples only; never infer biography, identity or experiences; "
        "match the surface style of the excerpts]\n"
        f"Style facts: {'; '.join(_style_facts(samples))}.\n"
        "Approved excerpts:\n" + "\n".join(excerpts)
    )
    if len(segment) > MAX_PROMPT_CHARS:
        segment = segment[: MAX_PROMPT_CHARS - 1].rstrip() + "…"
    from core.privacy_guard import text_privacy_risks
    from core.secret_redaction import contains_secret

    if contains_secret(segment) or _ABSOLUTE_PATH_PATTERN.search(segment):
        return ""  # a scanned store should never trip here; refuse honestly if it does
    try:
        if text_privacy_risks(segment):
            return ""
    except Exception:
        return ""
    return segment


__all__ = [
    "MAX_EXCERPTS",
    "MAX_PROMPT_CHARS",
    "MAX_SAMPLES_PER_SCOPE",
    "MAX_SAMPLE_CHARS",
    "STORE_FILENAME",
    "VOICE_INTENT",
    "VoiceSample",
    "add_samples",
    "inspect",
    "remove_sample",
    "reset_scope",
    "set_paused",
    "turn_scope",
    "voice_profile_version",
    "voice_segment_for_turn",
]
