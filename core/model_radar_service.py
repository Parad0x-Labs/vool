"""Model Radar service — the one orchestration seam between feeds, store and UI.

``observe_feed`` is the single production entry point: adapters hand it typed
observations from a real fetch, it applies the authority's qualification, persists
new findings, and advances the recorded baseline. Its ordering laws:

- findings are persisted BEFORE the baseline advances, so a crash between the two
  can re-derive the finding (dedup absorbs it) but can never silently swallow
  qualified news by jumping the baseline past it;
- a finding suppressed ONLY by cadence (min_interval_hours) does NOT advance the
  baseline — the news stays pending until the interval lets it through;
- stale evidence and conflicted identities neither notify nor touch the baseline;
- an observation that yields NO qualified change advances the baseline normally.

Nothing here auto-switches anything. The radar reads prices and writes
notifications; model selection stays with the existing authorities
(core.cloud_model_control.set_cloud_model and its A11 paid gate).
"""

from __future__ import annotations

import os
import secrets
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.model_radar_feeds import FeedMalformedError  # re-exported for callers
from core.model_radar import (
    EVIDENCE_MAX_AGE_SECONDS,
    ModelObservation,
    RadarFinding,
    RadarPreferences,
    evidence_is_fresh,
    prices_conflict,
    qualify_observation,
)
from storage import model_radar as radar_store

TRY_ONCE_TTL_SECONDS = 15 * 60


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SuppressedQualification:
    provider_id: str
    model_id: str
    fingerprint: str
    reason_code: str
    detail: str


@dataclass(frozen=True)
class ConflictRecord:
    provider_id: str
    model_id: str
    detail: str
    observations: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ObserveResult:
    findings: tuple[RadarFinding, ...] = ()
    suppressed: tuple[SuppressedQualification, ...] = ()
    conflicts: tuple[ConflictRecord, ...] = ()
    recorded: int = 0


@dataclass(frozen=True)
class DismissResult:
    ok: bool
    fingerprint: str
    error: str = ""


@dataclass(frozen=True)
class TryOnceResult:
    ok: bool
    token: str = ""
    provider_id: str = ""
    model_id: str = ""
    display_name: str = ""
    expires_at: str = ""
    error: str = ""


def _dedupe_by_identity(observations: Sequence[ModelObservation]) -> dict[tuple[str, str], list[ModelObservation]]:
    grouped: dict[tuple[str, str], list[ModelObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.identity(), []).append(observation)
    return grouped


def _cross_feed_conflicts(identity: tuple[str, str], rows: list[ModelObservation]) -> list[tuple[str, str, ModelObservation, ModelObservation]]:
    """Simultaneous disagreements: within the incoming batch across source feeds."""
    clashes: list[tuple[str, str, ModelObservation, ModelObservation]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            left, right = rows[i], rows[j]
            if left.source_feed == right.source_feed:
                continue
            if prices_conflict(left.prices, right.prices):
                clashes.append((identity[0], identity[1], left, right))
    return clashes


def _conflicts_with_recorded(
    identity: tuple[str, str], observation: ModelObservation, recorded: ModelObservation | None
) -> bool:
    """A fresh recorded baseline from a DIFFERENT feed that materially disagrees."""
    if recorded is None or recorded.source_feed == observation.source_feed:
        return False
    if not evidence_is_fresh(recorded.evidence_fetched_at, now=datetime.now(timezone.utc)):
        return False
    return prices_conflict(recorded.prices, observation.prices)


def observe_feed(
    provider_id: str,
    observations: Iterable[ModelObservation],
    *,
    now: str | None = None,
) -> ObserveResult:
    """Judge one fetched feed against the recorded baseline. Never raises for bad
    news content (that is typed refusals); storage failures propagate so callers
    can keep their own LKG contract honest."""
    provider = str(provider_id or "").strip().lower()
    rows = tuple(observations)
    if not provider:
        raise ValueError("provider_id is required")
    for observation in rows:
        if observation.provider_id != provider:
            raise ValueError("observation provider mismatch")

    moment = now or _utcnow_iso()
    prefs = radar_store.load_preferences()
    grouped = _dedupe_by_identity(rows)

    findings: list[RadarFinding] = []
    suppressed: list[SuppressedQualification] = []
    conflicts: list[ConflictRecord] = []
    baseline_updates: list[ModelObservation] = []

    for identity in sorted(grouped):
        candidates = grouped[identity]
        observation = candidates[0]

        clashes = _cross_feed_conflicts(identity, candidates)
        recorded = radar_store.get_observation(identity[0], identity[1])
        for clash in clashes:
            conflicts.append(
                ConflictRecord(
                    provider_id=identity[0],
                    model_id=identity[1],
                    detail="simultaneous feeds disagree on price",
                    observations=(clash[2].to_dict(), clash[3].to_dict()),
                )
            )
        if len(candidates) > 1 and not clashes and prices_conflict(candidates[0].prices, candidates[1].prices):
            # Same source_feed reporting two different prices at once is just as
            # unknown as two feeds disagreeing.
            conflicts.append(
                ConflictRecord(
                    provider_id=identity[0],
                    model_id=identity[1],
                    detail="one feed reported contradictory prices",
                    observations=tuple(o.to_dict() for o in candidates[:2]),
                )
            )
        if conflicts and any(c.provider_id == identity[0] and c.model_id == identity[1] for c in conflicts):
            radar_store.record_conflict(
                identity[0],
                identity[1],
                {"detail": "conflicting observations", "rows": [o.to_dict() for o in candidates[:2]]},
            )
            continue  # unknown truth: no finding, no baseline change

        if not evidence_is_fresh(observation.evidence_fetched_at, now=moment):
            suppressed.append(
                SuppressedQualification(
                    provider_id=identity[0],
                    model_id=identity[1],
                    fingerprint="",
                    reason_code="stale_evidence",
                    detail="evidence outside the freshness window; baseline untouched",
                )
            )
            continue  # stale evidence must not become the recorded present

        if _conflicts_with_recorded(identity, observation, recorded):
            conflicts.append(
                ConflictRecord(
                    provider_id=identity[0],
                    model_id=identity[1],
                    detail="live observation disagrees with a fresh recorded baseline from another feed",
                    observations=(recorded.to_dict() if recorded else {}, observation.to_dict()),
                )
            )
            radar_store.record_conflict(
                identity[0],
                identity[1],
                {"detail": "cross-feed conflict with recorded baseline", "rows": [observation.to_dict()]},
            )
            continue

        from core.model_radar import finding_fingerprint

        fingerprint = finding_fingerprint(
            provider_id=observation.provider_id,
            model_id=observation.model_id,
            kind="state",
            prices=observation.prices,
            offer_kind=observation.offer_kind,
            expires_at=observation.expires_at,
        )

        if recorded is None:
            suppressed.append(
                SuppressedQualification(
                    provider_id=identity[0],
                    model_id=identity[1],
                    fingerprint="",
                    reason_code="no_baseline",
                    detail="first recorded sight of this identity; baseline starts now",
                )
            )
            baseline_updates.append(observation)
            continue

        verdict = qualify_observation(recorded, observation, now=moment, preferences=prefs)

        cadence_held = False
        if isinstance(verdict, RadarFinding):
            if radar_store.is_dismissed(verdict.fingerprint) or radar_store.finding_exists(verdict.fingerprint):
                suppressed.append(
                    SuppressedQualification(
                        provider_id=identity[0],
                        model_id=identity[1],
                        fingerprint=verdict.fingerprint,
                        reason_code="duplicate_event",
                        detail="this qualified state is already recorded",
                    )
                )
            elif _cadence_blocks(identity, prefs, moment):
                cadence_held = True
                suppressed.append(
                    SuppressedQualification(
                        provider_id=identity[0],
                        model_id=identity[1],
                        fingerprint=verdict.fingerprint,
                        reason_code="rate_limited",
                        detail="min_interval_hours since the last issued finding has not elapsed",
                    )
                )
            else:
                radar_store.insert_finding(verdict)
                findings.append(verdict)
        else:
            suppressed.append(
                SuppressedQualification(
                    provider_id=identity[0],
                    model_id=identity[1],
                    fingerprint=fingerprint,
                    reason_code=verdict.reason_code,
                    detail=verdict.detail,
                )
            )

        if not cadence_held:
            baseline_updates.append(observation)

    if baseline_updates:
        radar_store.record_observations(provider, baseline_updates)
    return ObserveResult(
        findings=tuple(findings),
        suppressed=tuple(suppressed),
        conflicts=tuple(conflicts),
        recorded=len(baseline_updates),
    )


def _cadence_blocks(identity: tuple[str, str], prefs: RadarPreferences, now: str) -> bool:
    if prefs.min_interval_hours <= 0:
        return False
    last = radar_store.last_issued_at(identity[0], identity[1])
    if not last:
        return False
    last_moment = _parse_iso(last)
    now_moment = _parse_iso(now) or datetime.now(timezone.utc)
    if last_moment is None:
        return False
    return (now_moment - last_moment) < timedelta(hours=prefs.min_interval_hours)


def unread_findings(limit: int = 50) -> list[RadarFinding]:
    return radar_store.unread_findings(limit=limit)


def list_findings(*, include_read: bool = True, include_dismissed: bool = False, limit: int = 50) -> list[RadarFinding]:
    return radar_store.list_findings(
        include_read=include_read, include_dismissed=include_dismissed, limit=limit
    )


def mark_viewed(fingerprints: Iterable[str]) -> int:
    return radar_store.mark_viewed(fingerprints)


def dismiss(fingerprint: str, *, now: str | None = None, origin: str = "ui") -> DismissResult:
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return DismissResult(ok=False, fingerprint="", error="fingerprint required")
    if not radar_store.finding_exists(fingerprint):
        return DismissResult(ok=False, fingerprint=fingerprint, error="unknown finding")
    ok = radar_store.dismiss_finding(fingerprint, now=now, origin=origin)
    return DismissResult(ok=ok, fingerprint=fingerprint, error="" if ok else "dismiss failed")


def get_preferences() -> RadarPreferences:
    return radar_store.load_preferences()


def save_preferences(preferences: RadarPreferences) -> RadarPreferences:
    stored = radar_store.save_preferences_row(preferences)
    return stored


def list_conflicts(limit: int = 20) -> list[dict[str, Any]]:
    return radar_store.list_conflicts(limit=limit)


def try_once(fingerprint: str, *, session_id: str) -> TryOnceResult:
    """Validate + receipt a one-turn trial of a finding's model. Pins nothing.

    The grant is a receipt with a 15-minute TTL that the UI consumes for exactly
    one turn; the model itself must still resolve in the CURRENT recorded
    observations (a delisted or never-recorded model refuses), and the actual turn
    runs through the ordinary /api/chat model field with all its existing gates.
    """
    fingerprint = str(fingerprint or "").strip()
    session_id = str(session_id or "").strip()
    if not fingerprint or not session_id:
        return TryOnceResult(ok=False, error="fingerprint and session_id are required")
    conn_findings = radar_store.list_findings(include_read=True, include_dismissed=False, limit=200)
    finding = next((f for f in conn_findings if f.fingerprint == fingerprint), None)
    if finding is None:
        return TryOnceResult(ok=False, error="finding not found or dismissed")
    current = radar_store.get_observation(finding.provider_id, finding.model_id)
    if current is None:
        return TryOnceResult(ok=False, error="model no longer present in the recorded catalog")
    if not evidence_is_fresh(current.evidence_fetched_at, now=_utcnow_iso()):
        return TryOnceResult(ok=False, error="recorded evidence for this model is stale; refresh the catalog first")
    token = "mrtry_" + secrets.token_hex(12)
    granted = _utcnow_iso()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=TRY_ONCE_TTL_SECONDS)).isoformat()
    radar_store.insert_try_once(
        token=token,
        fingerprint=fingerprint,
        session_id=session_id,
        provider_id=finding.provider_id,
        model_id=finding.model_id,
        granted_at=granted,
        expires_at=expires,
    )
    return TryOnceResult(
        ok=True,
        token=token,
        provider_id=finding.provider_id,
        model_id=finding.model_id,
        display_name=finding.display_name,
        expires_at=expires,
    )


def observe_openrouter_catalog(models: Iterable[Any], *, now: str | None = None) -> ObserveResult:
    """The production bridge: live OpenRouterModel rows -> observations -> authority."""
    from adapters.model_radar_feeds import openrouter_observations

    return observe_feed("openrouter", openrouter_observations(models), now=now)


# ---- the background observer ------------------------------------------------------------
#
# A quiet surface still needs ears: without a periodic fetch, the radar would only
# see the catalog when a human clicks Refresh. One daemon thread, started lazily by
# the first owner-local feed poll (the chat shell polls every minute), fetches
# hourly through the SAME outbound door as every other catalog read and observes
# the result. Failures are swallowed per-cycle — an outage costs one cycle, never
# the thread. VOOL_MODEL_RADAR_POLLER=0 disables it (tests, embedded runs).

_POLL_INTERVAL_SECONDS = 3600
_observer_lock = threading.Lock()
_observer_started = False


def _poll_openrouter_once() -> bool:
    """One observation cycle through the ordinary catalog door. Returns whether it ran."""
    try:
        from core.openrouter_catalog import refresh_openrouter_catalog

        models = refresh_openrouter_catalog()
        observe_openrouter_catalog(models)
        return True
    except Exception:
        return False


def _observer_loop() -> None:
    while True:
        threading.Event().wait(_POLL_INTERVAL_SECONDS)
        _poll_openrouter_once()


def ensure_background_observer() -> bool:
    """Start the hourly observer exactly once per process. No-op when disabled/started."""
    global _observer_started
    if str(os.environ.get("VOOL_MODEL_RADAR_POLLER") or "").strip().lower() in {"0", "off", "false"}:
        return False
    with _observer_lock:
        if _observer_started:
            return False
        _observer_started = True
    threading.Thread(target=_observer_loop, name="vool-model-radar-observer", daemon=True).start()
    return True


__all__ = [
    "EVIDENCE_MAX_AGE_SECONDS",
    "ConflictRecord",
    "DismissResult",
    "FeedMalformedError",
    "ObserveResult",
    "SuppressedQualification",
    "TryOnceResult",
    "dismiss",
    "ensure_background_observer",
    "get_preferences",
    "list_conflicts",
    "list_findings",
    "mark_viewed",
    "observe_feed",
    "observe_openrouter_catalog",
    "save_preferences",
    "try_once",
    "unread_findings",
]
