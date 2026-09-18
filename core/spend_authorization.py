"""
core/spend_authorization.py
===========================
One shared spend-authorization brake for every money lane.

The SOL ``.null``-register lane is the only lane that fully gates a spend (owner session +
panic freeze + per-tx/daily/weekly caps + a live OS-consent prompt). This composes the same
controls into one helper so the USDC x402 lanes and receipt anchoring gate the SAME way,
instead of signing with no brake.

Controls
--------
* **owner-local** — ALWAYS enforced and NOT user-disableable: a remote channel message or a
  forged request can never authorize a spend. Turning this off would only ever help an
  attacker, never the owner, so there is no switch for it.
* **freeze** — the panic freeze (``/stopx402``) blocks every spend instantly.
* **caps** — per-tx/daily/weekly SOL caps (opt-in; ``0`` = no cap, the default). USDC
  cumulative caps are a documented follow-up; until then the per-spend OS approval, which
  shows the exact amount, is the bound.
* **OS consent (the brake)** — a live Windows Hello / Touch ID / polkit approval before the
  spend. ON by default. The owner may turn per-spend approval OFF (unattended spending), but
  doing so is itself OS-consent-gated and warns — an injected instruction cannot disable it.

Fails CLOSED: a non-owner request, a missing/tampered policy, or a declined/unavailable
consent all refuse the spend.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("vool.spend_auth")

_SETTING_FILE = "spend_brake.json"


def _default_consent(reason: str) -> bool:
    from core.os_consent_gate import require_os_user_consent

    return require_os_user_consent(reason)


# ---------------------------------------------------------------------------
# Per-spend approval toggle (unsigned setting; disabling it is consent-gated)
# ---------------------------------------------------------------------------
#
# This lives OUTSIDE the HMAC-signed SpendPolicy on purpose: adding a field to that dataclass
# would change its canonical JSON and invalidate every existing on-disk signature (fail-closed
# -> locked wallets). The consent brake defends against the *agent* being tricked into
# spending, not against a disk attacker (who already holds the key), and disabling it requires
# a live OS approval, so an unsigned flag is consistent with that threat model.


def _setting_path() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / _SETTING_FILE).resolve()


def spend_consent_required() -> bool:
    """Whether a live OS approval is required before each spend. Default True (brake ON).

    Fail SAFE: an absent or unreadable setting -> True (approval required), never silently off.
    """
    path = _setting_path()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
        return bool(data.get("require_consent", True))
    except Exception as exc:
        logger.warning("spend brake setting unreadable (%s); requiring consent", exc)
        return True


def set_spend_consent_required(
    enabled: bool, *, consent_fn: Callable[[str], bool] | None = None
) -> tuple[bool, str]:
    """Turn per-spend OS approval ON or OFF. Returns ``(changed, message)``.

    Turning it OFF is itself gated behind a live OS approval — an injected chat instruction
    cannot disable the brake without a real human confirming at the OS prompt — and the
    returned message is a warning. Turning it ON needs no consent (it only makes spending
    safer). A persist failure leaves the setting unchanged and reports it.
    """
    enabled = bool(enabled)
    if not enabled:
        consent = consent_fn or _default_consent
        try:
            if not consent(
                "Turn OFF the per-spend approval prompt — let VOOL spend your wallet without "
                "asking each time"
            ):
                return False, "Left per-spend approval ON — the OS confirmation was declined."
        except Exception:
            return False, "Left per-spend approval ON — no OS confirmation was available."

    if not _write_setting({"require_consent": enabled}):
        return False, "Could not save the setting; per-spend approval is unchanged."

    if enabled:
        return True, "Per-spend approval is ON. Every wallet spend needs a live OS approval."
    return True, (
        "WARNING: per-spend approval is now OFF — VOOL can spend your wallet without asking "
        "each time. Only your own machine can still trigger a spend and the emergency freeze "
        "still works, but turn on daily spend caps as a backstop. Re-enable any time."
    )


def _write_setting(data: dict[str, Any]) -> bool:
    path = _setting_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning("spend brake setting write failed (%s)", exc)
        with contextlib.suppress(Exception):
            if tmp.exists():
                tmp.unlink()
        return False


# ---------------------------------------------------------------------------
# The shared brake
# ---------------------------------------------------------------------------


def require_spend_authorized(
    *,
    asset: str,
    amount: int,
    recipient: str,
    reason: str,
    owner_local: bool,
    wallet: Any | None = None,
    consent_fn: Callable[[str], bool] | None = None,
    policy_loader: Callable[[Any], tuple[Any, Any]] | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Authorize a spend of ``amount`` (in the asset's base unit — lamports for SOL, micro-USDC
    for USDC) to ``recipient``. Returns ``(allowed, reason)``; never raises.

    Composes owner-local (always) + panic freeze + SOL caps (opt-in) + a live OS-consent
    approval (unless the owner disabled per-spend approval). Fails closed on every uncertainty.
    """
    asset = str(asset or "").strip().upper()
    if int(amount) <= 0:
        return False, "amount must be positive"

    # 1. Owner-local — always, not disableable.
    if not owner_local:
        return False, "a spend can only be authorized from the machine owner's own local session"

    # 2. Load the spend policy + ledger, HMAC-verified; a tampered/absent policy fails closed.
    # No legacy wallet is loaded here: the policy store keys its integrity off the device secret.
    if policy_loader is None:
        from core.wallet_spend_policy_store import load_policy_and_ledger as policy_loader
    try:
        policy, ledger = policy_loader(wallet)
    except Exception as exc:
        return False, f"spend policy could not be verified ({exc}); refusing to spend"

    # 3. Freeze + caps.
    moment = float(time.time() if now is None else now)
    if asset == "SOL":
        from core.wallet_spend_policy import check_spend_allowed

        ok, why = check_spend_allowed(policy, ledger, int(amount), moment)
        if not ok:
            return False, f"spend policy: {why}"
    else:
        # Freeze applies to every asset; SOL-denominated caps do not translate to USDC, so
        # cumulative USDC caps are a documented opt-in follow-up. The per-spend OS approval
        # below (which names the exact amount) is the bound until then.
        if getattr(policy, "frozen", False):
            return False, "wallet is frozen (panic freeze active)"

    # 4. Live OS consent (the brake), unless the owner turned per-spend approval off.
    if spend_consent_required():
        consent = consent_fn or _default_consent
        try:
            if not consent(reason):
                return False, "OS consent was declined"
        except Exception:
            return False, "OS consent is unavailable; refusing to spend"

    return True, "ok"


__all__ = [
    "require_spend_authorized",
    "set_spend_consent_required",
    "spend_consent_required",
]
