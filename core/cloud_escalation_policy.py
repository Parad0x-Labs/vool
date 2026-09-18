"""
core/cloud_escalation_policy.py
===============================
Policy for BYOK "burst to cloud" routing.

Local-first is the default: trivial turns (``hello``, small edits) stay on the free
local model. When the router decides a task exceeds the local lane, THIS policy
decides whether to burst to the user's *own* cloud model (bring-your-own-key):

* ``off``  — never escalate; stay local always (the default).
* ``ask``  — escalate only after explicit per-call permission.
* ``auto`` — escalate automatically, subject to the monetary reservation at dispatch.

Why this is NOT the wallet spend policy
---------------------------------------
This guards the user's *own* cloud spend — their key, their provider, their money,
paid directly to the provider. Nothing is in VOOL custody, so this is a plain
preference file, not the HMAC-sealed :mod:`core.wallet_spend_policy_store`. The
threat model is "don't let auto-mode silently burn more of my own cloud credits
than I meant to," not "stop an attacker moving funds." An attacker with local write
access could use the stored key directly, so signing the cap file would add no real
protection — the honest control is that the *default is* ``off`` and cloud is
strictly opt-in.

Legacy call counts are retained as usage statistics only. Monetary caps are enforced
by the spend reservation owner; no call-count quota applies.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("vool.cloud_escalation")

# Serializes the daily-counter read-modify-write so concurrent worker threads (the API runs
# handlers in a threadpool) cannot lose a usage increment.
_COUNTER_LOCK = threading.Lock()


@contextlib.contextmanager
def _counter_file_lock():
    """Best-effort cross-process advisory lock on a sidecar ``.lock`` file, held around the
    read-modify-write of the JSON store so two daemon processes cannot interleave a counter or
    policy write and clobber each other. Fail-soft: if locking is unavailable on the platform,
    yields WITHOUT a lock (a preference/counter write must never abort a turn). The single
    daemon is the supported config; this only hardens the unsupported multi-process case.
    """
    lock_path = _store_path().with_name(_store_path().name + ".lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")  # noqa: SIM115 - released in finally
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception:  # pragma: no cover - locking unavailable -> proceed unlocked
            pass
        yield
    except Exception:  # pragma: no cover - never break a turn over a lock file
        yield
    finally:
        if handle is not None:
            with contextlib.suppress(Exception):
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                handle.close()

MODE_OFF = "off"
MODE_ASK = "ask"
MODE_AUTO = "auto"
_MODES = (MODE_OFF, MODE_ASK, MODE_AUTO)

# What auto-mode does once the daily cap is spent.
ON_CAP_ASK = "ask"
ON_CAP_LOCAL = "local"
_ON_CAP = (ON_CAP_ASK, ON_CAP_LOCAL)

# Routing actions this policy can return for an escalation-eligible task.
ACTION_LOCAL = "local"  # keep it on the free local model
ACTION_ASK = "ask"      # surface a permission prompt before spending
ACTION_CLOUD = "cloud"  # burst to the user's cloud model now

_DEFAULT_DAILY_CAP = 25
_FILENAME = "cloud_escalation.json"


@dataclass(frozen=True)
class CloudEscalationPolicy:
    """User preference for bursting escalation-eligible tasks to a BYOK cloud model."""

    mode: str = MODE_OFF
    daily_cap: int = _DEFAULT_DAILY_CAP  # legacy storage only; no call-count quota
    on_cap_reached: str = ON_CAP_ASK     # what auto mode does once the cap is spent
    free_cloud_enabled: bool = False
    # The user's chosen cloud model id (e.g. "deepseek/deepseek-chat-v3-0324:free"). Empty means
    # "use the default". Persisted here so the choice survives restarts and is honored by provider
    # registration, not just by the session that set it.
    model: str = ""
    # The verified-free OpenRouter model VOOL Auto may use after the local lane cannot serve the
    # turn. ``auto`` means choose the strongest eligible free model from the live catalog. This is
    # deliberately separate from ``model``: manually pinning a paid answer model must never mutate
    # Auto into a paid route or silently replace the operator's free fallback preference.
    auto_free_model: str = "auto"
    # The active cloud provider id (e.g. "openai", "anthropic"). Empty = legacy behavior (resolve
    # the active provider from which credential slot is keyed, defaulting to OpenRouter). Lets a
    # direct-provider model route + meter on the correct lane instead of always OpenRouter.
    provider: str = ""

    def normalized(self) -> CloudEscalationPolicy:
        """Coerce to valid values, failing safe: unknown mode -> off, bad cap -> default."""
        mode = str(self.mode or "").strip().lower()
        if mode not in _MODES:
            mode = MODE_OFF
        on_cap = str(self.on_cap_reached or "").strip().lower()
        if on_cap not in _ON_CAP:
            on_cap = ON_CAP_ASK
        try:
            cap = int(self.daily_cap)
        except (TypeError, ValueError):
            cap = _DEFAULT_DAILY_CAP
        if cap < 0:
            cap = 0
        model = str(self.model or "").strip()
        # A model id is provider-native ("vendor/name[:tag]" or a bare id); anything else fails
        # safe to default.
        if len(model) > 128 or not re.fullmatch(r"[A-Za-z0-9._:/-]*", model):
            model = ""
        auto_free_model = str(self.auto_free_model or "auto").strip()
        if auto_free_model.lower() == "auto" or (
            len(auto_free_model) > 128
            or not re.fullmatch(r"[A-Za-z0-9._:/-]+", auto_free_model)
            or not auto_free_model.lower().endswith(":free")
        ):
            auto_free_model = "auto"
        provider = str(self.provider or "").strip().lower()
        # An unknown provider fails safe to "" (legacy = resolve-from-keyed-slot).
        from core.cloud_providers import PROVIDERS

        if provider not in PROVIDERS:
            provider = ""
        return CloudEscalationPolicy(
            mode=mode,
            daily_cap=cap,
            on_cap_reached=on_cap,
            free_cloud_enabled=bool(self.free_cloud_enabled),
            model=model,
            auto_free_model=auto_free_model,
            provider=provider,
        )

    def to_dict(self) -> dict[str, Any]:
        p = self.normalized()
        return {
            "mode": p.mode,
            "daily_cap": p.daily_cap,
            "on_cap_reached": p.on_cap_reached,
            "free_cloud_enabled": p.free_cloud_enabled,
            "model": p.model,
            "auto_free_model": p.auto_free_model,
            "provider": p.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CloudEscalationPolicy:
        data = data or {}
        return cls(
            mode=data.get("mode", MODE_OFF),
            daily_cap=data.get("daily_cap", _DEFAULT_DAILY_CAP),
            on_cap_reached=data.get("on_cap_reached", ON_CAP_ASK),
            free_cloud_enabled=bool(data.get("free_cloud_enabled", False)),
            model=data.get("model", ""),
            auto_free_model=data.get("auto_free_model", "auto"),
            provider=data.get("provider", ""),
        ).normalized()


@dataclass(frozen=True)
class EscalationDecision:
    """The routing verdict for an escalation-eligible task."""

    action: str   # ACTION_LOCAL | ACTION_ASK | ACTION_CLOUD
    reason: str
    used_today: int = 0
    daily_cap: int = 0


def _utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def decide_escalation(
    policy: CloudEscalationPolicy,
    used_today: int,
    *,
    now: float | None = None,
) -> EscalationDecision:
    """Decide what to do with a task the router has already judged too big for local.

    Call this ONLY once the local lane has been ruled insufficient; it answers the
    single question "may we spend the user's cloud key for this one, and how".

    * ``off``  -> stay local (cloud disabled).
    * ``ask``  -> require a permission prompt.
    * ``auto`` -> eligible for cloud, subject to the execution owner's monetary budget.

    Legacy daily_cap/on_cap_reached fields no longer restrict calls. This routing decision
    grants no money: the normal paid reservation and permission checks still run.
    """
    p = policy.normalized()
    used = max(0, int(used_today))

    if p.mode == MODE_OFF:
        return EscalationDecision(ACTION_LOCAL, "cloud escalation is off (local-only)", used, p.daily_cap)

    if p.mode == MODE_ASK:
        return EscalationDecision(ACTION_ASK, "cloud escalation requires permission", used, p.daily_cap)

    return EscalationDecision(
        ACTION_CLOUD, "auto escalation subject to monetary budget", used, 0
    )


# ---------------------------------------------------------------------------
# Persistence — plain JSON preference + a per-day escalation counter.
# ---------------------------------------------------------------------------


def _store_path() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / _FILENAME).resolve()


def _read_raw() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        # Fail SAFE: an unreadable file falls back to the local-only default, never to cloud.
        logger.warning("cloud escalation store unreadable (%s); using local-only defaults", exc)
        return {}


def _write_raw(data: dict[str, Any]) -> bool:
    """Persist the store atomically. Returns True on success; on any I/O error logs a warning
    and returns False rather than raising, so a preference or counter write can never abort a
    chat turn (mirroring the fail-safe :func:`_read_raw`). Uses a unique temp name so two
    concurrent writers do not collide on the same tmp path.
    """
    path = _store_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning("cloud escalation store write failed (%s); preference not persisted", exc)
        with contextlib.suppress(Exception):
            if tmp.exists():
                tmp.unlink()
        return False


def is_configured() -> bool:
    """Whether the escalation gate should bind for this user.

    True once the user has explicitly saved a policy. Until then the feature is DORMANT:
    callers keep whatever paid-fallback behavior they had before, so cloud escalation is
    strictly opt-in and adding this module changes nothing until the user sets a mode.

    A store file that EXISTS but is unparseable is treated as configured (fail closed): a
    corrupt store then drives the gate through load_policy() (which defaults to ``off`` ->
    local) rather than reverting an opted-in user to paid-eligible.
    """
    path = _store_path()
    if not path.exists():
        return False  # truly absent -> dormant, prior behavior preserved
    if _read_raw().get("policy"):
        return True  # an explicit policy is saved
    # File exists but no policy surfaced. Distinguish parseable-without-policy (dormant) from
    # corrupt/unparseable (fail closed as configured -> gate enforces the off default).
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return bool(isinstance(parsed, dict) and parsed.get("policy"))
    except Exception:
        return True


def counter_store_is_corrupt() -> bool:
    """Whether the store exists but cannot be parsed.

    ``_read_raw`` deliberately flattens "absent" and "corrupt" to ``{}`` so a broken file degrades
    to the local-only defaults instead of raising mid-turn. Spend gating needs to tell those two
    apart: absent means a fresh install, while corrupt means the stored routing and approval
    policy cannot be trusted. Callers that authorize spend fail closed until it is repaired (see
    :func:`core.paid_call_reservation._daily_call_cap`).
    """
    path = _store_path()
    try:
        if not path.exists():
            return False
        return not isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        # Unparseable, or unreadable on disk: either way the counter cannot be trusted.
        return True


def load_policy() -> CloudEscalationPolicy:
    """Load the saved policy, defaulting to local-only (``off``)."""
    return CloudEscalationPolicy.from_dict(_read_raw().get("policy"))


def save_policy(policy: CloudEscalationPolicy) -> CloudEscalationPolicy:
    """Persist the policy (normalized). Returns the stored value.

    The read-modify-write is held under the same thread + cross-process locks as the counter so
    a concurrent ``record_escalation`` cannot read the store, then have this write land in
    between, and lose the counter update (or vice versa) — both mutate the one JSON store.
    """
    p = policy.normalized()
    with _COUNTER_LOCK, _counter_file_lock():
        raw = _read_raw()
        raw["policy"] = p.to_dict()
        _write_raw(raw)
    return p


def chat_model_selection(session_id: str) -> dict[str, str]:
    """A chat pin never inherits the global provider pin or another chat's choice."""
    row = _read_raw().get("chat_models", {}).get(session_id, {})
    return {"model": str(row.get("model", "")), "provider": str(row.get("provider", ""))}


def chat_model_is_selected(provider: str, model: str) -> bool:
    return any(
        row.get("provider") == provider and row.get("model") == model
        for row in _read_raw().get("chat_models", {}).values()
        if isinstance(row, dict)
    )


def selected_chat_models(provider: str) -> tuple[str, ...]:
    """Persisted explicit choices to restore after this provider's key is re-enabled."""
    return tuple(sorted({
        row["model"] for row in _read_raw().get("chat_models", {}).values()
        if isinstance(row, dict) and row.get("provider") == provider
        and isinstance(row.get("model"), str) and row["model"].strip()
        and row["model"].lower() not in {"auto", "default", "reset"}
    }))


def save_chat_model_selection(session_id: str, *, model: str, provider: str, revision: int = 0) -> bool:
    with _COUNTER_LOCK, _counter_file_lock():
        raw = _read_raw()
        choices = raw.setdefault("chat_models", {})
        if revision and revision < int(choices.get(session_id, {}).get("revision", 0)):
            return False
        choices[session_id] = {"model": model, "provider": provider, "revision": revision}
        return _write_raw(raw)


def used_today(*, now: float | None = None) -> int:
    """Cloud escalations recorded so far today (UTC). Resets automatically each day."""
    ts = time.time() if now is None else now
    usage = _read_raw().get("usage") or {}
    if str(usage.get("date") or "") != _utc_day(ts):
        return 0
    try:
        return max(0, int(usage.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def record_escalation(*, now: float | None = None) -> int:
    """Count one cloud escalation against today's cap. Returns the new day total.

    Call this only when an escalation actually goes to cloud (action == cloud, or a
    granted ``ask``), so the daily cap tracks real spend, not offers.

    The read-modify-write is serialized under ``_COUNTER_LOCK`` so two concurrent bursts
    cannot both read the same count and each write ``N+1`` (losing an increment, which would
    let the cap be silently overshot). A failed persist is logged: the cap is a best-effort
    per-day budget, and the residual window between the gate's ``used_today`` check and this
    record means a handful of in-flight bursts can still overshoot slightly — an accepted,
    documented soft bound for the user's own spend, not an attacker-facing hard limit.
    """
    ts = time.time() if now is None else now
    day = _utc_day(ts)
    with _COUNTER_LOCK, _counter_file_lock():
        raw = _read_raw()
        usage = raw.get("usage") or {}
        count = 0
        if str(usage.get("date") or "") == day:
            try:
                count = max(0, int(usage.get("count") or 0))
            except (TypeError, ValueError):
                count = 0
        count += 1
        raw["usage"] = {"date": day, "count": count}
        if not _write_raw(raw):
            logger.warning("cloud escalation count not persisted; daily cap may under-count today")
        return count


def set_mode(mode: str) -> CloudEscalationPolicy:
    """Convenience: change only the mode, keeping cap/on_cap_reached."""
    return save_policy(replace(load_policy(), mode=mode))


def set_free_cloud_enabled(enabled: bool) -> CloudEscalationPolicy:
    """Enable or disable the zero-cost cloud lane without authorizing paid inference."""
    return save_policy(replace(load_policy(), free_cloud_enabled=bool(enabled)))


__all__ = [
    "ACTION_ASK",
    "ACTION_CLOUD",
    "ACTION_LOCAL",
    "MODE_ASK",
    "MODE_AUTO",
    "MODE_OFF",
    "ON_CAP_ASK",
    "ON_CAP_LOCAL",
    "CloudEscalationPolicy",
    "EscalationDecision",
    "counter_store_is_corrupt",
    "decide_escalation",
    "is_configured",
    "load_policy",
    "record_escalation",
    "save_policy",
    "set_free_cloud_enabled",
    "set_mode",
    "used_today",
]
