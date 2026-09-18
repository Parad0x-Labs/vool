"""Task-class model-sufficiency feedback: bounded learned quality, not health, not weights.

What this is NOT:

* **Not generic health scoring.** ``core.model_health`` already counts transport failures and
  opens circuits; ``provider_error`` observations are deliberately ignored here. This module
  learns whether a provider produces *usable quality* for a task class -- the failure states a
  circuit breaker never sees (``synthesis_empty``, ``response_extraction_failed``,
  ``validator_rejected``) versus real completions (``success``), keyed by the selection request's
  task kind. Retrieval-stage failures are the task's fault and count for nothing.
* **Not weight training.** Nothing here touches model weights or claims to; it is a bounded,
  reversible adjustment input to the existing selector.

The evaluated rule (``sufficiency_adjustment``):

* an adjustment exists only with >= ``SUFFICIENCY_MIN_OBSERVATIONS`` fresh observations for the
  exact (task_kind, provider_id) pair -- one bad morning is not a verdict;
* observations older than ``SUFFICIENCY_FRESHNESS_DAYS`` stop counting;
* the value is capped to ``[-SUFFICIENCY_MAX_PENALTY, +SUFFICIENCY_MAX_BONUS]``;
* it is ADDITIVE ONLY: the selector applies it after every hard exclusion (paid gating,
  local-only, license, capability, trust), so it can reorder eligible manifests and nothing else.

Evidence sources:

* ``record_sufficiency_observation`` -- typed writer used by the operator/test path. The
  production writer seam (one call at turn finalize joining the turn's task kind, the turn-model
  ledger identity and the ``turn.trace_completed`` stage verdict) is specified in the module
  docstring below as PENDING INTEGRATION -- integration owns that seam's files.
* ``derive_sufficiency_observations`` -- pure derivation from the canonical session-event store
  (``model.call_completed`` x ``turn.trace_completed`` joined by ``turn_key``). Observations
  derived without a known task kind are returned for operator inspection but are NOT applied to
  ranking (``task_kind="unknown"`` never matches a selection request's task kind).

Smallest production-writer contract (PENDING INTEGRATION, reserved files):

    from core.learning.model_sufficiency import record_sufficiency_observation

    record_sufficiency_observation(
        task_kind=<task_kind of the ModelSelectionRequest that selected the provider>,
        provider_id=<provider_id from the turn model-call ledger identity>,
        model_id=<model_id from the same identity>,
        outcome="verified_success" | "quality_failure",   # mapped from TerminalState
        stage_state=<TerminalState.value of the turn>,
        turn_key=<turn_key>,                              # idempotency key
        session_id=<runtime_session_id>,
    )

Call it once per finalized turn that used a model. It must never raise into the turn.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path

from .policy import LearningPolicy

_STORE_LOCK = threading.RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _observations_path() -> Path:
    return data_path("learning", "sufficiency_observations.json")


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def record_sufficiency_observation(
    *,
    task_kind: str,
    provider_id: str,
    outcome: str,
    model_id: str = "",
    stage_state: str = "",
    turn_key: str = "",
    session_id: str = "",
    created_at: datetime | None = None,
    feedback_version: int | None = None,
) -> dict[str, Any] | None:
    """Record one typed sufficiency observation. Refuses unknown outcome vocabulary and blank
    identities rather than storing noise; deduplicates on turn_key; bounds the store. The
    feedback-record version is stamped per observation so a policy change never mixes old-shape
    feedback into new-rule rankings. Read-modify-write is cross-process safe (fcntl file lock)."""
    clean_kind = str(task_kind or "").strip().lower()
    clean_provider = str(provider_id or "").strip()
    clean_outcome = str(outcome or "").strip()
    if not clean_kind or not clean_provider:
        return None
    if clean_outcome not in LearningPolicy.SUFFICIENCY_OUTCOMES:
        return None
    observation = {
        "observation_id": f"suff-{uuid.uuid4().hex}",
        "task_kind": clean_kind,
        "provider_id": clean_provider,
        "model_id": str(model_id or "").strip(),
        "outcome": clean_outcome,
        "stage_state": str(stage_state or "").strip(),
        "turn_key": str(turn_key or "").strip(),
        "session_id": str(session_id or "").strip(),
        "created_at": (created_at or _utcnow()).isoformat(),
        "feedback_version": int(feedback_version if feedback_version is not None else LearningPolicy.FEEDBACK_VERSION),
    }
    from .procedure_shards import _StoreFileLock

    with _STORE_LOCK, _StoreFileLock(_observations_path()):
        observations = _load_observations_locked()
        if observation["turn_key"]:
            for existing in observations:
                if existing.get("turn_key") == observation["turn_key"] and existing.get("provider_id") == clean_provider:
                    return existing
        observations.append(observation)
        observations = observations[-LearningPolicy.SUFFICIENCY_MAX_OBSERVATIONS:]
        _save_observations_locked(observations)
    return observation


def reset_sufficiency_observations(*, provider_id: str = "", task_kind: str = "") -> int:
    """Operator correction path: drop matching observations. Returns how many were removed."""
    clean_provider = str(provider_id or "").strip()
    clean_kind = str(task_kind or "").strip().lower()
    from .procedure_shards import _StoreFileLock

    with _STORE_LOCK, _StoreFileLock(_observations_path()):
        observations = _load_observations_locked()
        kept = [
            item
            for item in observations
            if not (clean_provider in {"", item.get("provider_id")} and clean_kind in {"", item.get("task_kind")})
            and not (clean_provider == "" and clean_kind == "")
        ]
        removed = len(observations) - len(kept)
        if removed:
            _save_observations_locked(kept)
    return removed


def list_sufficiency_observations(*, provider_id: str = "", task_kind: str = "") -> list[dict[str, Any]]:
    with _STORE_LOCK:
        observations = _load_observations_locked()
    clean_provider = str(provider_id or "").strip()
    clean_kind = str(task_kind or "").strip().lower()
    if clean_provider:
        observations = [item for item in observations if item.get("provider_id") == clean_provider]
    if clean_kind:
        observations = [item for item in observations if item.get("task_kind") == clean_kind]
    return observations


def sufficiency_adjustment(
    *,
    task_kind: str,
    provider_id: str,
    model_id: str = "",
    now: datetime | None = None,
    observations: list[dict[str, Any]] | None = None,
) -> float:
    """The bounded evaluated rule. Returns 0.0 (never raises) when there is not enough fresh,
    task-class-specific evidence to say anything."""
    try:
        moment = now or _utcnow()
        clean_kind = str(task_kind or "").strip().lower()
        clean_provider = str(provider_id or "").strip()
        clean_model = str(model_id or "").strip()
        if not clean_kind or not clean_provider:
            return 0.0
        if observations is None:
            with _STORE_LOCK:
                observations = _load_observations_locked()
        cutoff = moment - timedelta(days=LearningPolicy.SUFFICIENCY_FRESHNESS_DAYS)
        # Exact (task_kind, provider, MODEL) identity: two models behind one provider must not
        # contaminate each other's learned quality. Feedback recorded under a different
        # feedback-record version is preserved for inspection but never mixes into rankings.
        fresh = [
            item
            for item in observations
            if str(item.get("task_kind") or "").strip().lower() == clean_kind
            and str(item.get("provider_id") or "").strip() == clean_provider
            and str(item.get("model_id") or "").strip() == clean_model
            and int(item.get("feedback_version") or 0) == LearningPolicy.FEEDBACK_VERSION
            and (_parse_iso(str(item.get("created_at") or "")) or moment) >= cutoff
        ]
        if len(fresh) < LearningPolicy.SUFFICIENCY_MIN_OBSERVATIONS:
            return 0.0
        successes = sum(1 for item in fresh if item.get("outcome") == LearningPolicy.SUFFICIENCY_OUTCOME_VERIFIED)
        failures = sum(1 for item in fresh if item.get("outcome") == LearningPolicy.SUFFICIENCY_OUTCOME_FAILURE)
        ratio = (successes - failures) / float(len(fresh))
        if ratio >= 0:
            return round(min(LearningPolicy.SUFFICIENCY_MAX_BONUS * ratio, LearningPolicy.SUFFICIENCY_MAX_BONUS), 4)
        return round(max(LearningPolicy.SUFFICIENCY_MAX_PENALTY * ratio, -LearningPolicy.SUFFICIENCY_MAX_PENALTY), 4)
    except Exception:
        # An unreadable learning store must degrade to the status-quo selection, never to a
        # bonus and never to a selection failure.
        return 0.0


def outcome_from_stage_state(stage_state: str) -> str:
    """Map a canonical TerminalState onto the learned-quality vocabulary.

    Deliberately partial: transport failures (provider_error) belong to model_health's circuit
    breaker, retrieval-stage states are the task's fault, and neither teaches this store.
    """
    value = str(stage_state or "").strip().lower()
    if value == LearningPolicy.SUFFICIENCY_SUCCESS_STATE:
        return LearningPolicy.SUFFICIENCY_OUTCOME_VERIFIED
    if value in LearningPolicy.SUFFICIENCY_QUALITY_FAILURE_STATES:
        return LearningPolicy.SUFFICIENCY_OUTCOME_FAILURE
    return ""


def derive_sufficiency_observations(
    session_events: list[dict[str, Any]],
    *,
    task_kind: str = "unknown",
) -> list[dict[str, Any]]:
    """Pure join of the canonical session events: provider/model identity per turn (from
    ``model.call_completed``) x the turn's terminal stage verdict (``turn.trace_completed``).

    Only turns where BOTH exist and the stage maps to a learned-quality outcome produce an
    observation. ``task_kind`` is not carried by these events today; derived observations default
    to ``unknown``, which the evaluator never matches -- they are for operator inspection until
    the pending writer seam keys them by the real task kind.
    """
    providers_by_turn: dict[str, dict[str, str]] = {}
    stage_by_turn: dict[str, str] = {}
    for event in session_events:
        if not isinstance(event, dict):
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        event_type = str(event.get("event_type") or details.get("event_type") or "").strip()
        turn_key = str(event.get("turn_key") or details.get("turn_key") or "").strip()
        if event_type == "model.call_completed":
            provider_id = str(details.get("provider_id") or "").strip()
            if turn_key and provider_id:
                providers_by_turn[turn_key] = {
                    "provider_id": provider_id,
                    "model_id": str(details.get("model_id") or "").strip(),
                }
        elif event_type == "turn.trace_completed":
            stage_verdict = details.get("stage_verdict") if isinstance(details.get("stage_verdict"), dict) else {}
            state = str(stage_verdict.get("state") or "").strip()
            if turn_key and state:
                stage_by_turn[turn_key] = state

    derived: list[dict[str, Any]] = []
    for turn_key, identity in sorted(providers_by_turn.items()):
        state = stage_by_turn.get(turn_key, "")
        outcome = outcome_from_stage_state(state)
        if not outcome:
            continue
        derived.append(
            {
                "task_kind": str(task_kind or "unknown").strip().lower(),
                "provider_id": identity["provider_id"],
                "model_id": identity["model_id"],
                "outcome": outcome,
                "stage_state": state,
                "turn_key": turn_key,
                "session_id": "",
                "created_at": _utcnow().isoformat(),
            }
        )
    return derived


def _load_observations_locked() -> list[dict[str, Any]]:
    path = _observations_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return []
    return [dict(item) for item in observations if isinstance(item, dict)]


def _save_observations_locked(observations: list[dict[str, Any]]) -> None:
    path = _observations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "vool.sufficiency_observations.v2", "observations": observations}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
