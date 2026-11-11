"""An agent's own small wallet: the only key that ever signs without the owner present.

The owner's wallets sign nothing unattended: each of their signatures opens a seal with the owner's PIN, password or
device prompt. An agent that pays on its own therefore gets a wallet of its own -- an ordinary Crypto Pilot wallet
(``core.wallet.pilot_custody``: same seal, same quote, same claim, same receipts) with three differences:

* **It is marked as the agent's** in the transaction that reserves it, so it is never taken for an owner's wallet:
  it never proves the owner (``custody.owner_proof_wallet`` skips it; ``custody.prove_owner`` on it asks for the
  owner's own credential instead), and its credential can be neither changed nor recovered into another.
* **Its credential is this machine's**, derived from the device secret for this one wallet and never stored, shown
  or accepted from a caller. Only :func:`signing_credential` hands it out, and only to the payment door of this
  package (``core.wallet.agent_pay``), after the agent's allowance said yes.
* **What it can lose is what the owner put in it.** The owner funds it from their own wallet with an ordinary
  approved transfer; nothing here moves money in.

What this does not defend against, stated plainly: a process running as this user that can read the device secret
can derive the credential and spend the agent wallet's balance without an allowance. That is the bound the owner
chose by funding it -- the balance -- and it is why the owner's own wallets are never opened this way.

Creating one is the owner's act: their wallet's credential is proven first, and with no owner wallet on this
machine there is nobody to prove and nothing is created.
"""
from __future__ import annotations

import base64
import re
import uuid
from typing import Any

from core.wallet import chains, custody, pilot_custody
from core.wallet.security import wallet_fault
from core.wallet.store import connection

AUTHORITY = "core.wallet.agent_wallets"

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9:_.\-]{0,63}$")
_CREDENTIAL_LABEL = "vool-agent-wallet-credential-v1"
_CREATION_PREFIX = "agent-wallet:"


def _fault(reason: str, *, source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault("wallet_setup_state_invalid", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def _clean_agent(agent_id: str, *, source_context: dict[str, Any] | None) -> str:
    clean = str(agent_id or "").strip().lower()
    if not _AGENT_ID.match(clean):
        raise _fault("unknown_agent", source_context=source_context)
    return clean


def _derive(creation_key: str) -> str:
    from network.signer import derive_local_secret

    material = derive_local_secret(f"{_CREDENTIAL_LABEL}|{creation_key}", length=32)
    # a password by the wallet's own policy (10-128 characters, not only digits); the prefix guarantees the letters
    return "agw1-" + base64.urlsafe_b64encode(bytes(material)).decode("ascii").rstrip("=")


def _row(wallet_id: str) -> dict[str, str] | None:
    with connection() as conn:
        found = conn.execute(
            "SELECT wallet_id, agent_id, network, public_key, setup_state, creation_key, agent_return_address, label, created_at FROM wallet_profiles "
            "WHERE wallet_id = ? AND COALESCE(agent_id, '') <> ''", (str(wallet_id or ""),)).fetchone()
    if not found:
        return None
    names = ("wallet_id", "agent_id", "network", "address", "setup_state", "creation_key", "return_address", "label", "created_at")
    return {name: str(value or "") for name, value in zip(names, found, strict=True)}


def _view(row: dict[str, str]) -> dict[str, Any]:
    return {name: row[name] for name in ("wallet_id", "agent_id", "network", "address", "setup_state", "return_address", "label", "created_at")} | {
        "ready": row["setup_state"] == pilot_custody.STATE_READY}


def _require(wallet_id: str, *, source_context: dict[str, Any] | None) -> dict[str, str]:
    row = _row(wallet_id)
    if row is None or not row["creation_key"].startswith(_CREATION_PREFIX):
        raise _fault("not_an_agent_wallet", source_context=source_context, wallet_id=str(wallet_id)[:64])
    return row


def _owner_return_address(network: str) -> str:
    """The owner's own oldest ready wallet on this network: where an agent wallet's balance goes back. "" when the
    owner holds none there (the balance then returns only to an address the owner names later)."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT network, public_key FROM wallet_profiles WHERE COALESCE(agent_id, '') = '' AND mode IN (?, ?) AND COALESCE(setup_state, '') IN ('', 'ready') "
            "AND public_key <> '' ORDER BY created_at ASC", (custody.MODE_POCKET_SEALED, custody.MODE_EXTERNAL_SIGNER)).fetchall()
    for stored_network, public_key in rows:
        try:  # an older wallet row keeps a legacy network name: one spelling per chain before comparing
            if chains.resolve_network(str(stored_network)).network == network:
                return str(public_key)
        except Exception:
            continue
    return ""


def create_agent_wallet(agent_id: str, *, network: str, credential: str = "", label: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create ``agent_id``'s wallet on ``network``. The owner's act: the credential is the OWNER's wallet credential.
    One per agent and network. It is created empty and not ready: the owner sees its backup key once
    (:func:`reveal_backup`) and confirms it (:func:`acknowledge_backup`) before it can pay, as for any wallet."""
    custody.require_enabled(source_context=source_context)
    agent = _clean_agent(agent_id, source_context=source_context)
    owner = custody.owner_proof_wallet()
    if owner is None:
        raise _fault("no_owner_wallet_to_prove", source_context=source_context)
    try:
        spec = chains.resolve_network(str(network or ""))
    except Exception:
        raise _fault("unknown_network", source_context=source_context) from None
    existing = wallet_for(agent, spec.network)
    if existing is not None:
        raise _fault("agent_wallet_exists", source_context=source_context, wallet_id=existing["wallet_id"])
    custody.prove_owner(owner.wallet_id, credential=credential, purpose="create a wallet for an agent", source_context=source_context)
    creation_key = f"{_CREATION_PREFIX}{agent}:{uuid.uuid4().hex}"
    machine = _derive(creation_key)
    try:
        created = pilot_custody.create_pilot_wallet(
            network=spec.network, method=custody.APPROVAL_PASSWORD, credential=machine, credential_confirmation=machine, creation_key=creation_key,
            label=str(label or f"Agent: {agent}")[:80], source_context=source_context, agent_id=agent, agent_return_address=_owner_return_address(spec.network))
    finally:
        del machine
    from core.wallet import receipts

    receipts.journal_security_event("wallet_agent_wallet_created", {"wallet_id": created["wallet_id"], "agent_id": agent, "network": spec.network, "address": created["address"]},
                                    source_context=source_context)
    return _view(_require(created["wallet_id"], source_context=source_context))


def reveal_backup(wallet_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The agent wallet's backup key, once, to the proven owner (their own wallet's credential)."""
    row = _require(wallet_id, source_context=source_context)
    custody.prove_owner(row["wallet_id"], credential=credential, purpose="show an agent wallet's backup key", source_context=source_context)
    return pilot_custody.reveal_pilot_backup(row["wallet_id"], credential=_derive(row["creation_key"]), source_context=source_context)


def acknowledge_backup(wallet_id: str, *, ack_token: str, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """"I've saved it": the wallet becomes ready. Only the reveal's own token acknowledges."""
    row = _require(wallet_id, source_context=source_context)
    if not str(ack_token or ""):
        raise _fault("acknowledgement_token_required", source_context=source_context)
    pilot_custody.acknowledge_pilot_backup(row["wallet_id"], ack_token=ack_token, source_context=source_context)
    return _view(_require(row["wallet_id"], source_context=source_context))


def signing_credential(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> str:
    """Package-private: the machine credential of a READY agent wallet, for ``core.wallet.agent_pay`` alone and only
    after the agent's allowance held the payment. Never returned to a caller outside this package, logged or stored."""
    row = _require(wallet_id, source_context=source_context)
    if row["setup_state"] != pilot_custody.STATE_READY:
        raise _fault("agent_wallet_not_ready", source_context=source_context, wallet_id=row["wallet_id"])
    return _derive(row["creation_key"])


def wallet_for(agent_id: str, network: str) -> dict[str, Any] | None:
    """The agent's wallet on this network (any setup state but cancelled), or None."""
    try:
        canonical = chains.resolve_network(str(network or "")).network
    except Exception:
        return None
    with connection() as conn:
        found = conn.execute("SELECT wallet_id FROM wallet_profiles WHERE agent_id = ? AND network = ? AND setup_state <> 'cancelled'",
                             (str(agent_id or "").strip().lower(), canonical)).fetchone()
    row = _row(found[0]) if found else None
    return _view(row) if row else None


def agent_wallet(wallet_id: str) -> dict[str, Any] | None:
    row = _row(wallet_id)
    return _view(row) if row else None


def list_agent_wallets() -> list[dict[str, Any]]:
    with connection() as conn:
        ids = [str(found[0]) for found in conn.execute("SELECT wallet_id FROM wallet_profiles WHERE COALESCE(agent_id, '') <> '' AND setup_state <> 'cancelled' ORDER BY created_at ASC")]
    return [_view(row) for row in map(_row, ids) if row]


__all__ = ["acknowledge_backup", "agent_wallet", "create_agent_wallet", "list_agent_wallets", "reveal_backup", "wallet_for"]
