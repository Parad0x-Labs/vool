"""The person's own wallets by name and role: one Main wallet per network, everyday wallets, burners.

Every wallet is already its own key with its own backup and its own approval credential. What this module adds is
which of them VOOL may use by itself, and under what name:

* **Main** -- one per network, pinned. Payments VOOL starts without being told which wallet (contacts, top-ups,
  streaming payments, a chat payment) come from the Main wallet of their network. A wallet created later never takes
  its place: before the first role is written anywhere, the wallet VOOL already used as its default is pinned as Main
  and marked default (:func:`freeze`), so an existing person's payments keep going where they went.
* **Everyday** -- a further wallet of the person's. Used only when the person names it.
* **Burner** -- a wallet for one-off payments. Never picked by VOOL, never Main. Otherwise a normal wallet at the
  person's full disposal (owner 2026-11-12): once it has paid, the page says so, and the person can empty it into
  another wallet and delete it whenever they like.
* An agent's own wallet (``core.wallet.agent_wallets``) and the Skills & plugins wallet are not the person's: they
  carry no role here and are never Main.

Renaming is the person's act at this computer and loosens nothing, so it needs no credential. Making a wallet Main
moves where unattended-by-choice payments go, so it needs that wallet's own credential.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.wallet_roles"

KIND_MAIN = "main"
KIND_EVERYDAY = "everyday"
KIND_BURNER = "burner"
CREATABLE_KINDS = (KIND_EVERYDAY, KIND_BURNER)

NAME_MAX = 40
#: A wallet the person deleted: its sealed key is wiped and it is listed nowhere; its payment history stays.
STATE_REMOVED = "removed"
DEFAULT_MAIN_NAME = "Main"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _fault(reason: str, *, code: str = "wallet_setup_state_invalid", source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault(code, authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def clean_name(value: Any) -> str:
    """A wallet name as the person typed it: one line, no control characters, at most :data:`NAME_MAX` characters.
    "" when nothing printable is left."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    return text[:NAME_MAX].strip()


def _canonical(network: str) -> str:
    from core.wallet import chains

    return chains.resolve_network(str(network or "")).network


def _owner_rows(conn: Any) -> list[tuple[str, str, str, str, int, str]]:
    """(wallet_id, network, created_at, mode, is_default, setup_state) of every wallet that is the person's own: not an
    agent's, not cancelled, able to sign."""
    return [
        (str(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4] or 0), str(r[5] or ""))
        for r in conn.execute(
            "SELECT wallet_id, network, created_at, mode, is_default, COALESCE(setup_state, '') FROM wallet_profiles "
            "WHERE COALESCE(agent_id, '') = '' AND COALESCE(setup_state, '') NOT IN ('cancelled', 'removed') AND mode <> 'watch_only' ORDER BY created_at ASC")
    ]


def _kinds(conn: Any) -> dict[str, str]:
    return {str(r[0]): str(r[1]) for r in conn.execute("SELECT wallet_id, kind FROM wallet_roles")}


def _mains(conn: Any) -> dict[str, str]:
    return {str(r[0]): str(r[1]) for r in conn.execute("SELECT network, wallet_id FROM wallet_main")}


def _legacy_default(rows: list[tuple[str, str, str, str, int, str]], kinds: dict[str, str]) -> str:
    """The wallet ``custody.default_wallet`` answered before roles existed: the one marked default, else the newest."""
    usable = [r for r in rows if kinds.get(r[0]) != KIND_BURNER]
    marked = [r for r in usable if r[4]]
    if marked:
        return marked[-1][0]
    return usable[-1][0] if usable else ""


def freeze(conn: Any) -> None:
    """Pin today's choices before a new wallet or a role change can move them. Idempotent; on the caller's connection.

    * Exactly one of the person's wallets is marked default: the one VOOL used as default until now.
    * Every network the person holds a wallet on has a Main: the default when it lives there, else the oldest
      non-burner wallet there.
    """
    rows = _owner_rows(conn)
    if not rows:
        return
    kinds = _kinds(conn)
    default = _legacy_default(rows, kinds)
    marked = [r[0] for r in rows if r[4]]
    if default and marked != [default]:
        conn.execute("UPDATE wallet_profiles SET is_default = CASE WHEN wallet_id = ? THEN 1 ELSE 0 END", (default,))
    mains = _mains(conn)
    by_network: dict[str, list[tuple[str, str, str, str, int, str]]] = {}
    for row in rows:
        try:
            by_network.setdefault(_canonical(row[1]), []).append(row)
        except Exception:
            continue
    now = utcnow()
    for network, members in by_network.items():
        current = mains.get(network, "")
        if current and any(m[0] == current for m in members) and kinds.get(current) != KIND_BURNER:
            continue
        candidates = [m for m in members if kinds.get(m[0]) != KIND_BURNER]
        if not candidates:
            continue
        chosen = default if any(m[0] == default for m in candidates) else candidates[0][0]
        conn.execute("INSERT INTO wallet_main (network, wallet_id, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(network) DO UPDATE SET wallet_id = excluded.wallet_id, updated_at = excluded.updated_at", (network, chosen, now))


def record_new_wallet(conn: Any, wallet_id: str, *, kind: str) -> None:
    """A new wallet of the person's was just inserted on ``conn``: write its kind. The pins were frozen by the caller
    before the insert, so this wallet becomes Main only when its network had no wallet of the person's before."""
    chosen = kind if kind in CREATABLE_KINDS else KIND_EVERYDAY
    conn.execute("INSERT OR REPLACE INTO wallet_roles (wallet_id, kind, updated_at) VALUES (?, ?, ?)", (str(wallet_id), chosen, utcnow()))


def kind_of(wallet_id: str, *, conn: Any = None) -> str:
    """'main', 'everyday' or 'burner' for the person's own wallets; '' for an agent's or unknown wallet."""
    def read(c: Any) -> str:
        row = c.execute("SELECT network, COALESCE(agent_id, ''), COALESCE(setup_state, '') FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
        if row is None or str(row[1]) or str(row[2]) == "cancelled":
            return ""
        stored = c.execute("SELECT kind FROM wallet_roles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
        if stored is not None and str(stored[0]) == KIND_BURNER:
            return KIND_BURNER
        try:
            network = _canonical(str(row[0]))
        except Exception:
            return KIND_EVERYDAY
        return KIND_MAIN if main_wallet_id(network, conn=c) == str(wallet_id) else KIND_EVERYDAY

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def is_burner(wallet_id: str, *, conn: Any = None) -> bool:
    def read(c: Any) -> bool:
        row = c.execute("SELECT kind FROM wallet_roles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
        return row is not None and str(row[0]) == KIND_BURNER

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def main_wallet_id(network: str, *, conn: Any = None) -> str:
    """The Main wallet of ``network`` ('' when the person holds none there). Read only: a pin missing on an upgraded
    database answers by the same rule :func:`freeze` would write, so reading never moves anything."""
    def read(c: Any) -> str:
        try:
            canonical = _canonical(network)
        except Exception:
            return ""
        rows = _owner_rows(c)
        kinds = _kinds(c)
        members = []
        for r in rows:
            try:
                if _canonical(r[1]) == canonical and kinds.get(r[0]) != KIND_BURNER:
                    members.append(r)
            except Exception:
                continue
        if not members:
            return ""
        pinned = _mains(c).get(canonical, "")
        if pinned and any(m[0] == pinned for m in members):
            return pinned
        default = _legacy_default(rows, kinds)
        return default if any(m[0] == default for m in members) else members[0][0]

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def burner_used(wallet_id: str, *, conn: Any = None) -> bool:
    """Whether a payment has gone out of (or is on its way out of) this wallet: a held or settled spend with an
    amount. A payment that was released, or failed on chain with nothing moved, does not use a burner up."""
    def read(c: Any) -> bool:
        return c.execute("SELECT 1 FROM wallet_spend_ledger WHERE wallet_id = ? AND state IN ('reserved', 'settled') AND amount_minor > 0 LIMIT 1",
                         (str(wallet_id),)).fetchone() is not None

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def _require_owner_wallet(conn: Any, wallet_id: str, *, source_context: dict[str, Any] | None) -> tuple[str, str]:
    row = conn.execute("SELECT network, COALESCE(agent_id, ''), COALESCE(setup_state, ''), mode FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    if row is None or str(row[2]) in ("cancelled", STATE_REMOVED):
        raise _fault("wallet_not_found", code="wallet_not_found", source_context=source_context, wallet_id=str(wallet_id)[:64])
    return str(row[0]), str(row[1])


def rename(wallet_id: str, name: Any, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Give one of this computer's wallets a new name (the person's own, an agent's, or the Skills & plugins wallet)."""
    clean = clean_name(name)
    if not clean:
        raise _fault("name_required", source_context=source_context)
    with connection() as conn:
        _require_owner_wallet(conn, wallet_id, source_context=source_context)
        conn.execute("UPDATE wallet_profiles SET label = ? WHERE wallet_id = ?", (clean, str(wallet_id)))
    from core.wallet import receipts

    receipts.journal_security_event("wallet_renamed", {"wallet_id": str(wallet_id), "label": clean}, source_context=source_context)
    return {"wallet_id": str(wallet_id), "label": clean}


def make_main(wallet_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Make one of the person's everyday wallets the Main wallet of its network. Proven with that wallet's own
    credential: from now on, payments VOOL starts without being told which wallet come from it."""
    from core.wallet import custody, limits

    with connection() as conn:
        network, agent = _require_owner_wallet(conn, wallet_id, source_context=source_context)
    if agent:
        raise _fault("agent_wallet_cannot_be_main", source_context=source_context)
    if is_burner(wallet_id):
        raise _fault("burner_cannot_be_main", source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    if profile.mode == custody.MODE_WATCH_ONLY:
        raise _fault("watch_only_cannot_be_main", source_context=source_context)
    if custody.setup_state(wallet_id) not in {"", "ready"}:
        raise _fault("setup_not_finished", source_context=source_context)
    custody.prove_owner(wallet_id, credential=credential, purpose="make this wallet your main wallet", source_context=source_context)
    canonical = _canonical(network)
    with connection() as conn:
        limits._begin_immediate(conn)
        freeze(conn)
        previous = main_wallet_id(canonical, conn=conn)
        conn.execute("INSERT INTO wallet_main (network, wallet_id, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(network) DO UPDATE SET wallet_id = excluded.wallet_id, updated_at = excluded.updated_at", (canonical, str(wallet_id), utcnow()))
        was_default = conn.execute("SELECT is_default FROM wallet_profiles WHERE wallet_id = ?", (previous,)).fetchone()
        if previous and was_default is not None and int(was_default[0] or 0):
            # the wallet-wide default follows its network's Main: what VOOL used without being told moves with it
            conn.execute("UPDATE wallet_profiles SET is_default = CASE WHEN wallet_id = ? THEN 1 ELSE 0 END", (str(wallet_id),))
    from core.wallet import receipts

    receipts.journal_security_event("wallet_main_changed", {"wallet_id": str(wallet_id), "network": canonical, "previous": previous}, source_context=source_context)
    return {"wallet_id": str(wallet_id), "network": canonical, "previous": previous}


def remove_wallet(wallet_id: str, *, credential: str = "", backup_confirmed: bool = False, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete one of the person's own wallets from this computer: its sealed key is wiped and it is no longer listed.
    The coins stay on the chain at its address; only its backup key (or a Keychain backup) can reach them again.

    The person's act on one wallet: its own credential, and their confirmation that they hold its backup or emptied
    it. Refused while a payment from it is still on its way or waiting for approval, so no claim is left without its
    key. When it was Main, the oldest remaining wallet on its network becomes Main."""
    from core.wallet import custody, limits, proposals, transfers

    with connection() as conn:
        network, agent = _require_owner_wallet(conn, wallet_id, source_context=source_context)
    if agent:
        raise _fault("agent_wallet_not_removed_here", source_context=source_context)
    if not backup_confirmed:
        raise _fault("backup_not_confirmed", source_context=source_context)
    custody.prove_owner(wallet_id, credential=credential, purpose="delete this wallet", source_context=source_context)
    if any(str(t.get("wallet_id") or "") == str(wallet_id) for t in transfers.in_flight()):
        raise _fault("payment_in_flight", source_context=source_context)
    waiting = proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL, limit=500) + proposals.list_proposals(state=proposals.STATE_AWAITING_SIGNATURE, limit=500)
    if any(p.wallet_id == str(wallet_id) for p in waiting):
        raise _fault("payment_waiting", source_context=source_context)
    with connection() as conn:
        limits._begin_immediate(conn)
        freeze(conn)
        conn.execute("UPDATE wallet_profiles SET setup_state = ?, sealed_blob = '', device_sealed_blob = '', pin_hash = '', pin_salt = '', is_default = 0 "
                     "WHERE wallet_id = ?", (STATE_REMOVED, str(wallet_id)))
        conn.execute("DELETE FROM wallet_main WHERE wallet_id = ?", (str(wallet_id),))
        freeze(conn)
    from core.wallet import receipts

    receipts.journal_security_event("wallet_removed", {"wallet_id": str(wallet_id), "network": network}, source_context=source_context)
    return {"wallet_id": str(wallet_id), "removed": True}


def describe(entry: dict[str, Any], *, conn: Any) -> dict[str, Any]:
    """The role fields a wallet listing carries: kind, is_main, burner_used."""
    wallet_id = str(entry.get("wallet_id") or "")
    if str(entry.get("agent_id") or ""):
        return {"kind": "", "is_main": False, "burner_used": False, "keychain_backup": False, "usb_backup": None}
    from core.wallet import keychain_backup, usb_backup

    kind = kind_of(wallet_id, conn=conn)
    return {"kind": kind, "is_main": kind == KIND_MAIN, "burner_used": kind == KIND_BURNER and burner_used(wallet_id, conn=conn),
            "keychain_backup": keychain_backup.saved_for(wallet_id, conn=conn),
            "usb_backup": usb_backup.saved_on(wallet_id, conn=conn)}


__all__ = [
    "CREATABLE_KINDS", "KIND_BURNER", "KIND_EVERYDAY", "KIND_MAIN", "NAME_MAX", "burner_used", "clean_name", "describe", "freeze",
    "is_burner", "kind_of", "main_wallet_id", "make_main", "record_new_wallet", "remove_wallet", "rename",
]
