"""Gated sign + broadcast for a `.null` registration (Increment 2).

This is the ONLY place a registration is signed and submitted, and it is wrapped in a hard,
non-bypassable gate. A registration spends real SOL on Solana mainnet, so:

  * Phase 1 (preview) never signs — it derives the plan and quotes the exact cost.
  * Phase 2 (execute) proceeds ONLY when a `SpendGate` — populated exclusively from TRUSTED
    context (the executor/API layer, never the LLM) — carries allow_spend AND approve AND a
    wallet AND a spend cap that covers the live cost, AND a live OS-consent prompt (Windows
    Hello / CredUI) confirms at the machine. Any missing piece → refuse, fail-closed.
  * A hard lamport ceiling clamps the cost regardless of the caller's cap.
  * Availability is re-checked immediately before submit to avoid burning rent on a race.

Reuses the memo-anchor broadcast primitives (blockhash / RPC / confirm) and the wallet's
local ed25519 signer. No key ever leaves the wallet; no Parad0x server is in the path.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.null_registrar import (
    MAX_NAMES_PER_WALLET,
    NULL_DOMAIN_SIZE,
    OWNER_CAPACITY_SIZE,
    RegisterPlan,
    RegistryConfig,
    build_register_plan,
    decode_owner_capacity,
    decode_registry_config,
    derive_config_pda,
    derive_owner_cap_pda,
    validate_registrable_name,
)
from core.null_resolver import NULL_REGISTRAR_MAINNET, derive_domain_pda

# Hard ceiling: a single registration may never cost more than this, regardless of the
# caller-supplied cap. Pilot rent is ~0.003 SOL and go-live all-in is ~0.01 SOL, so 0.05 SOL
# leaves generous headroom while making a fat-fingered or malicious cap harmless.
MAX_REGISTER_LAMPORTS_CEILING = 50_000_000  # 0.05 SOL

logger = logging.getLogger("vool.register")


@dataclass
class SpendGate:
    """Spend authorization — MUST be filled only from trusted context, never model output."""

    allow_spend: bool = False
    approve: bool = False
    max_spend_lamports: int = 0
    wallet_present: bool = False


def gate_permits_spend(gate: SpendGate, cost_lamports: int) -> tuple[bool, str]:
    """Hard AND gate. Returns (permitted, reason). Never raises."""
    if not gate.wallet_present:
        return False, "no wallet available in trusted context"
    if not gate.allow_spend:
        return False, "allow_spend was not explicitly set"
    if not gate.approve:
        return False, "approve was not explicitly set"
    if gate.max_spend_lamports <= 0:
        return False, "no spend cap set"
    if cost_lamports > gate.max_spend_lamports:
        return False, f"cost {cost_lamports} lamports exceeds your cap {gate.max_spend_lamports}"
    if cost_lamports > MAX_REGISTER_LAMPORTS_CEILING:
        return False, f"cost {cost_lamports} lamports exceeds the hard ceiling {MAX_REGISTER_LAMPORTS_CEILING}"
    return True, "ok"


@dataclass
class RegisterOutcome:
    status: str  # "preview" | "action_required" | "refused" | "submitted" | "error"
    message: str = ""
    plan: RegisterPlan | None = None
    signature: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _build_register_message(plan: RegisterPlan, recent_blockhash: str) -> bytes:
    """Serialize the single-signer Register transaction message (pure, no signing)."""
    from solders.hash import Hash  # type: ignore
    from solders.instruction import AccountMeta as SAccountMeta  # type: ignore
    from solders.instruction import Instruction  # type: ignore
    from solders.message import Message  # type: ignore
    from solders.pubkey import Pubkey  # type: ignore

    metas = [
        SAccountMeta(pubkey=Pubkey.from_string(m.pubkey), is_signer=m.is_signer, is_writable=m.is_writable)
        for m in plan.accounts
    ]
    ix = Instruction(
        program_id=Pubkey.from_string(plan.program_id),
        accounts=metas,
        data=bytes.fromhex(plan.instruction_data_hex),
    )
    payer = Pubkey.from_string(plan.owner)
    msg = Message.new_with_blockhash([ix], payer, Hash.from_string(recent_blockhash))
    return bytes(msg)


def read_live_config(
    program_id: str = NULL_REGISTRAR_MAINNET,
    *,
    rpc: Callable[..., Any] | None = None,
) -> RegistryConfig | None:
    """Read the RegistryConfig PDA on-chain and decode the live treasury + SOL fee."""
    if rpc is None:
        from core.solana_anchor import _rpc_call as rpc  # type: ignore
    config_pda = derive_config_pda(program_id)
    if not config_pda:
        return None
    result = rpc("getAccountInfo", [config_pda, {"encoding": "base64"}])
    try:
        value = (result or {}).get("value") or {}
        data_field = value.get("data")
        b64 = data_field[0] if isinstance(data_field, list) else data_field
        raw = base64.b64decode(b64) if b64 else b""
    except Exception:
        return None
    return decode_registry_config(raw)


def read_rent_lamports(size: int = NULL_DOMAIN_SIZE, *, rpc: Callable[..., Any] | None = None) -> int | None:
    """getMinimumBalanceForRentExemption for the domain account size."""
    if rpc is None:
        from core.solana_anchor import _rpc_call as rpc  # type: ignore
    result = rpc("getMinimumBalanceForRentExemption", [int(size)])
    return int(result) if isinstance(result, int) else None


def _is_available(name: str, *, program_id: str) -> bool:
    """True if `name` has no on-chain record yet (safe to register)."""
    from core.null_resolver import resolve_null_domain

    try:
        return resolve_null_domain(name) is None
    except Exception:
        # Fail closed: if we cannot confirm availability, do not proceed to spend.
        return False


def read_owner_name_count(owner_pubkey: str, program_id: str, rpc: Callable[..., Any] | None) -> int | None:
    """The wallet's lifetime .null name count (for the 3-per-wallet cap). None if unreadable."""
    cap_pda = derive_owner_cap_pda(owner_pubkey, program_id)
    if not cap_pda:
        return None
    if rpc is None:
        from core.solana_anchor import _rpc_call as rpc  # type: ignore
    result = rpc("getAccountInfo", [cap_pda, {"encoding": "base64"}])
    try:
        value = (result or {}).get("value")
    except Exception:
        return None
    if not value:
        return 0  # account absent → wallet has registered nothing yet
    try:
        data_field = value.get("data")
        b64 = data_field[0] if isinstance(data_field, list) else data_field
        raw = base64.b64decode(b64) if b64 else b""
    except Exception:
        return None
    return decode_owner_capacity(raw)


def _plan_with_costs(
    name: str, owner_pubkey: str, program_id: str, rpc: Callable[..., Any] | None
) -> tuple[RegisterPlan | None, RegisterOutcome | None]:
    """Validate the name, enforce the per-wallet cap, read live config + BOTH rents, build the plan.

    Returns (plan, None) on success or (None, error_outcome). These guards mirror the program's
    own checks so VOOL never broadcasts a Register that reverts (burning the base fee): a 1-3
    char premium name (auction-only), a name over the wallet's 3-name cap, or a paid-mode config.
    """
    # 1-3 char names are premium (auction-only); >32 or bad charset can't be registered either.
    ok, reason, _is_premium = validate_registrable_name(name)
    if not ok:
        return None, RegisterOutcome(status="refused", message=reason)
    # Per-wallet lifetime cap: a 4th direct registration reverts with CapacityExceeded.
    count = read_owner_name_count(owner_pubkey, program_id, rpc)
    if count is None:
        return None, RegisterOutcome(status="error", message="could not verify your wallet's registration count on-chain")
    if count >= MAX_NAMES_PER_WALLET:
        return None, RegisterOutcome(
            status="refused",
            message=(
                f"your wallet already holds {count} directly-registered .null names — the cap is "
                f"{MAX_NAMES_PER_WALLET} (premium/auction names are exempt)"
            ),
        )
    config = read_live_config(program_id, rpc=rpc)
    if config is None:
        return None, RegisterOutcome(status="error", message="could not read the registrar config on-chain")
    # The program rejects a free SOL register when the registry is in paid mode (a NULL fee is
    # set but the SOL fee is 0) — surface that instead of broadcasting a tx that reverts.
    if config.sol_fee_lamports == 0 and config.null_fee_amount != 0:
        return None, RegisterOutcome(
            status="refused", message="SOL registration is not free under the current registrar config; a fee is required"
        )
    domain_rent = read_rent_lamports(NULL_DOMAIN_SIZE, rpc=rpc)
    owner_cap_rent = read_rent_lamports(OWNER_CAPACITY_SIZE, rpc=rpc)
    if domain_rent is None or owner_cap_rent is None:
        return None, RegisterOutcome(status="error", message="could not read the rent cost on-chain")
    plan = build_register_plan(
        name=name,
        owner_pubkey=owner_pubkey,
        config=config,
        rent_lamports=domain_rent,
        owner_cap_rent_lamports=owner_cap_rent,
        program_id=program_id,
    )
    if plan is None:
        return None, RegisterOutcome(status="error", message="could not assemble the registration")
    return plan, None


def preview_registration(
    name: str,
    owner_pubkey: str,
    *,
    program_id: str = NULL_REGISTRAR_MAINNET,
    rpc: Callable[..., Any] | None = None,
) -> RegisterOutcome:
    """Phase 1: quote the registration without signing anything."""
    if not derive_domain_pda(name, program_id=program_id):
        return RegisterOutcome(status="error", message=f"'{name}' is not a valid .null name")
    if not _is_available(name, program_id=program_id):
        return RegisterOutcome(status="refused", message=f"{name} is already registered")
    plan, err = _plan_with_costs(name, owner_pubkey, program_id, rpc)
    if err is not None:
        return err
    assert plan is not None
    return RegisterOutcome(
        status="preview",
        message=(
            f"Register {name} on Solana MAINNET for ~{plan.total_sol:.4f} SOL "
            f"(rent {plan.rent_lamports} + fee {plan.sol_fee_lamports} lamports). "
            f"Owner {plan.owner}. Program {plan.program_id}."
        ),
        plan=plan,
    )


def sign_and_broadcast_registration(plan: Any, wallet: Any, *, blockhash_fn: Any = None, rpc: Any = None) -> str:
    """RETIRED sign+broadcast tail of .null registration. Typed, receipt-backed refusal; never signs or sends."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("null_register_execute.sign_and_broadcast_registration")


def execute_registration(
    name: str,
    *,
    gate: SpendGate,
    wallet: Any,
    program_id: str = NULL_REGISTRAR_MAINNET,
    consent: Callable[[str], bool] | None = None,
    rpc: Callable[..., Any] | None = None,
    blockhash_fn: Callable[[], str | None] | None = None,
    policy_loader: Callable[[Any], tuple[Any, Any]] | None = None,
    recorder: Callable[[Any, int, float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> RegisterOutcome:
    """Phase 2: sign + broadcast ONLY if the full gate + user policy + OS consent allow it.

    `gate` must be built from trusted context. `wallet` is the VoolWallet (its key never
    leaves it). The user's spend policy (panic freeze + per-tx/daily/weekly caps) is loaded
    fresh and HMAC-verified, and the spend is recorded to the ledger (atomically, under a
    file lock) on success. `consent`/`rpc`/`blockhash_fn`/`policy_loader`/`recorder`/`now_fn`
    are injectable for tests; the defaults are the real OS-consent gate, RPC, and policy store.
    """
    if wallet is None or not gate.wallet_present:
        return RegisterOutcome(status="refused", message="no wallet available to sign")

    # Pre-submit availability recheck — never burn rent on a name taken in the meantime.
    if not _is_available(name, program_id=program_id):
        return RegisterOutcome(status="refused", message=f"{name} is already registered")

    plan, err = _plan_with_costs(name, wallet.pubkey, program_id, rpc)
    if err is not None:
        return err
    assert plan is not None

    permitted, reason = gate_permits_spend(gate, plan.total_lamports)
    if not permitted:
        return RegisterOutcome(status="action_required", message=reason, plan=plan)

    # User spend policy: panic freeze + per-tx/daily/weekly caps. Loaded fresh and
    # HMAC-verified; a tampered on-disk policy fails closed. Checked BEFORE we prompt the
    # human, so a frozen/over-cap wallet never even shows a consent prompt.
    if now_fn is None:
        import time

        now_fn = time.time
    if policy_loader is None:
        from core.wallet_spend_policy_store import load_policy_and_ledger as policy_loader
    if recorder is None:
        from core.wallet_spend_policy_store import record_spend as recorder
    now = float(now_fn())
    try:
        policy, ledger = policy_loader(wallet)
    except Exception as exc:
        return RegisterOutcome(
            status="blocked",
            message=f"spend policy could not be verified ({exc}); refusing to spend",
            plan=plan,
        )
    from core.wallet_spend_policy import check_spend_allowed

    allowed, policy_reason = check_spend_allowed(policy, ledger, plan.total_lamports, now)
    if not allowed:
        return RegisterOutcome(status="blocked", message=f"spend policy: {policy_reason}", plan=plan)

    # Sign + broadcast are the canonical wallet's alone: the tail is a typed, receipt-backed refusal
    # until .null registration is expressed as a wallet proposal (a generic-instruction proposal is
    # the next step). It refuses BEFORE the OS-consent dialog: prompting a human to approve a spend
    # this path cannot perform would be theatre. `consent` and `recorder` stay in the signature for
    # the canonical path (consent is then the wallet's own approval; the recorder its receipt).
    del consent, recorder
    try:
        sign_and_broadcast_registration(plan, wallet, blockhash_fn=blockhash_fn, rpc=rpc)
    except Exception as exc:
        code = getattr(exc, "code", "") or "wallet_legacy_surface_retired"
        return RegisterOutcome(status="refused", message=f"{code}: {getattr(exc, 'user_message', '') or exc}", plan=plan)
    return RegisterOutcome(status="error", message="the retired signing tail returned without refusing", plan=plan)


__all__ = [
    "MAX_REGISTER_LAMPORTS_CEILING",
    "RegisterOutcome",
    "SpendGate",
    "execute_registration",
    "gate_permits_spend",
    "preview_registration",
    "read_live_config",
    "read_rent_lamports",
]
