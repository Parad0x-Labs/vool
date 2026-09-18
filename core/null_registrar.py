"""Build (but never sign) a null_registrar v2 Register instruction for a `.null` name.

Increment 1 of the direct-sign registration feature: this module is PURE + read-only.
It derives the on-chain accounts, decodes the live RegistryConfig to quote the exact cost,
and constructs the *unsigned* Register instruction bytes. It does NOT sign, submit, hold a
key, or move any SOL — signing + broadcast (behind the two-phase consent gate + OS-consent)
is a separate module.

Byte-exact spec sourced from web0-internal `programs/null_registrar` (matches the deployed
mainnet program NXgQ…): see the [[null-registrar-v2-register-spec]] memory. Account layout
verified against processor.rs `process_register`:

  Register (tag 0x02) data:  [0x02][name:64][arweave_txid:32][currency:1]   (currency 1=SOL)
  Accounts (always):  payer(signer,writable), domain PDA(writable), config PDA(writable),
                      system program
  Then, ONLY when the SOL fee > 0:  treasury(writable)
  Then, ALWAYS LAST:  owner_cap PDA(writable)  — [b"owner-cap", payer]
  Domain PDA seeds:  [b"null-domain", sha256(pad_name64(name))]
  Config PDA seeds:  [b"null-registry"]
"""

from __future__ import annotations

from dataclasses import dataclass

from core.null_resolver import (
    NULL_REGISTRAR_MAINNET,
    derive_domain_pda,
    find_program_address,
    pad_name64,
)

IX_REGISTER = 0x02
CURRENCY_SOL = 0x01
CURRENCY_NULL = 0x03

REGISTRY_SEED = b"null-registry"
OWNER_CAP_SEED = b"owner-cap"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"

# RegistryConfig (v2) layout — from state.rs, size 122.
_RC_DISC = 0x52  # 'R'
_RC_OFF_DISC = 0
_RC_OFF_SOL_FEE = 33
_RC_OFF_NULL_FEE = 41
_RC_OFF_TREASURY = 81
REGISTRY_CONFIG_SIZE = 122

# Account sizes (from state.rs), used to quote rent.
NULL_DOMAIN_SIZE = 314
OWNER_CAPACITY_SIZE = 36
_ARWEAVE_TXID_LEN = 32

# Name rules (from instruction.rs): direct Register needs 4-32 printable chars in [a-z0-9-].
# 1-3 char names are PREMIUM and are sold only through the null-auction (never direct Register).
MIN_NAME_LEN = 4
MAX_NAME_LEN = 32
# Per-wallet lifetime cap (processor.rs MAX_NAMES_PER_WALLET) — a 4th direct register reverts
# with CapacityExceeded. Premium/auction winners are exempt from this cap.
MAX_NAMES_PER_WALLET = 3
_ALLOWED_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

# OwnerCapacity account layout (from state.rs): disc[1]=0x4B | owner[32] | count[2] u16 LE | bump.
_OC_DISC = 0x4B  # 'K'
_OC_OFF_COUNT = 33


def validate_registrable_name(name: str) -> tuple[bool, str, bool]:
    """Whether `name` can be DIRECTLY registered. Returns (registrable, reason, is_premium).

    Mirrors the program's validate_name (4-32 chars, [a-z0-9-]). A 1-3 char name is premium
    (auction-only) — flagged so the caller can point the user to the null-auction instead of
    broadcasting a Register that reverts with NameTooShort.
    """
    clean = str(name or "").strip().lower()
    if clean.endswith(".null"):
        clean = clean[: -len(".null")]
    length = len(clean)
    if length == 0:
        return False, "empty name", False
    if any(c not in _ALLOWED_NAME_CHARS for c in clean):
        return False, "a .null name may only contain a-z, 0-9, and hyphen", False
    if length < MIN_NAME_LEN:
        return (
            False,
            f"`{clean}.null` is a premium 1-3 character name — those are sold only through the "
            "null-auction (an ascending English auction with a floor price), not direct registration",
            True,
        )
    if length > MAX_NAME_LEN:
        return False, f"a .null name can be at most {MAX_NAME_LEN} characters", False
    return True, "ok", False


def decode_owner_capacity(account_data: bytes | None) -> int | None:
    """The wallet's lifetime name count. An absent account means 0 (registered nothing yet);
    a present-but-malformed account returns None so the caller can fail closed."""
    if not account_data:
        return 0
    if len(account_data) < OWNER_CAPACITY_SIZE or account_data[0] != _OC_DISC:
        return None
    return int.from_bytes(account_data[_OC_OFF_COUNT : _OC_OFF_COUNT + 2], "little")


def _pubkey_to_bytes(pubkey: str) -> bytes | None:
    try:
        from solders.pubkey import Pubkey  # type: ignore

        return bytes(Pubkey.from_string(pubkey))
    except Exception:
        return None


def derive_config_pda(program_id: str = NULL_REGISTRAR_MAINNET) -> str | None:
    """The RegistryConfig PDA: seeds [b"null-registry"]."""
    found = find_program_address([REGISTRY_SEED], program_id)
    return found[0] if found else None


def derive_owner_cap_pda(owner_pubkey: str, program_id: str = NULL_REGISTRAR_MAINNET) -> str | None:
    """The per-wallet OwnerCapacity PDA: seeds [b"owner-cap", owner_pubkey_bytes]."""
    owner_bytes = _pubkey_to_bytes(owner_pubkey)
    if owner_bytes is None:
        return None
    found = find_program_address([OWNER_CAP_SEED, owner_bytes], program_id)
    return found[0] if found else None


@dataclass(frozen=True)
class RegistryConfig:
    sol_fee_lamports: int
    treasury: str  # base58 pubkey
    null_fee_amount: int = 0


def decode_registry_config(account_data: bytes) -> RegistryConfig | None:
    """Decode the SOL/NULL fees + treasury from raw RegistryConfig account bytes.

    Returns None on wrong size or discriminator so a caller never quotes from garbage.
    """
    if account_data is None or len(account_data) < REGISTRY_CONFIG_SIZE:
        return None
    if account_data[_RC_OFF_DISC] != _RC_DISC:
        return None
    sol_fee = int.from_bytes(account_data[_RC_OFF_SOL_FEE : _RC_OFF_SOL_FEE + 8], "little")
    null_fee = int.from_bytes(account_data[_RC_OFF_NULL_FEE : _RC_OFF_NULL_FEE + 8], "little")
    treasury_bytes = bytes(account_data[_RC_OFF_TREASURY : _RC_OFF_TREASURY + 32])
    try:
        from solders.pubkey import Pubkey  # type: ignore

        treasury = str(Pubkey(treasury_bytes))
    except Exception:
        return None
    return RegistryConfig(sol_fee_lamports=sol_fee, treasury=treasury, null_fee_amount=null_fee)


def build_register_instruction_data(name: str, *, arweave_txid: bytes | None = None, currency: int = CURRENCY_SOL) -> bytes | None:
    """The exact Register instruction data: [0x02][name:64][arweave_txid:32][currency:1]."""
    padded = pad_name64(name)
    if padded is None or len(padded) != 64:
        return None
    txid = bytes(arweave_txid or b"")
    if len(txid) == 0:
        txid = b"\x00" * _ARWEAVE_TXID_LEN
    if len(txid) != _ARWEAVE_TXID_LEN:
        return None
    if currency not in (CURRENCY_SOL, CURRENCY_NULL):
        return None
    return bytes([IX_REGISTER]) + padded + txid + bytes([currency])


@dataclass(frozen=True)
class AccountMeta:
    pubkey: str
    is_signer: bool
    is_writable: bool


def build_register_accounts(
    *, payer: str, domain_pda: str, config_pda: str, owner_cap: str, treasury: str = "", include_treasury: bool = False
) -> list[AccountMeta]:
    """Register accounts in program order.

    treasury is included ONLY when the SOL fee is non-zero (the free pilot omits it), and
    owner_cap is ALWAYS the last account — matching processor.rs `process_register`.
    """
    metas = [
        AccountMeta(payer, is_signer=True, is_writable=True),
        AccountMeta(domain_pda, is_signer=False, is_writable=True),
        AccountMeta(config_pda, is_signer=False, is_writable=True),
        AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    if include_treasury:
        metas.append(AccountMeta(treasury, is_signer=False, is_writable=True))
    metas.append(AccountMeta(owner_cap, is_signer=False, is_writable=True))
    return metas


@dataclass(frozen=True)
class RegisterPlan:
    """Everything needed to preview (and later sign) a registration — but not signed."""

    name: str
    program_id: str
    domain_pda: str
    config_pda: str
    owner_cap_pda: str
    owner: str
    treasury: str
    sol_fee_lamports: int
    rent_lamports: int
    owner_cap_rent_lamports: int
    instruction_data_hex: str
    accounts: list[AccountMeta]
    network: str = "mainnet-beta"

    @property
    def total_lamports(self) -> int:
        return int(self.sol_fee_lamports) + int(self.rent_lamports) + int(self.owner_cap_rent_lamports)

    @property
    def total_sol(self) -> float:
        return self.total_lamports / 1_000_000_000


def build_register_plan(
    *,
    name: str,
    owner_pubkey: str,
    config: RegistryConfig,
    rent_lamports: int,
    owner_cap_rent_lamports: int = 0,
    program_id: str = NULL_REGISTRAR_MAINNET,
) -> RegisterPlan | None:
    """Assemble a complete, unsigned registration plan for `name`.

    `config` (fees + treasury) and rent are read on-chain by the caller and passed in, so
    this stays pure and unit-testable. treasury is only wired in when a SOL fee applies, and
    the owner_cap PDA is always appended. owner_cap_rent_lamports is the (conservative)
    first-registration cost of creating the per-wallet capacity account.
    """
    data = build_register_instruction_data(name, currency=CURRENCY_SOL)
    if data is None:
        return None
    domain_pda = derive_domain_pda(name, program_id=program_id)
    config_pda = derive_config_pda(program_id)
    owner_cap_pda = derive_owner_cap_pda(owner_pubkey, program_id)
    if not domain_pda or not config_pda or not owner_cap_pda:
        return None
    include_treasury = int(config.sol_fee_lamports) > 0
    accounts = build_register_accounts(
        payer=owner_pubkey,
        domain_pda=domain_pda,
        config_pda=config_pda,
        owner_cap=owner_cap_pda,
        treasury=config.treasury,
        include_treasury=include_treasury,
    )
    return RegisterPlan(
        name=name,
        program_id=program_id,
        domain_pda=domain_pda,
        config_pda=config_pda,
        owner_cap_pda=owner_cap_pda,
        owner=owner_pubkey,
        treasury=config.treasury,
        sol_fee_lamports=int(config.sol_fee_lamports),
        rent_lamports=int(rent_lamports),
        owner_cap_rent_lamports=int(owner_cap_rent_lamports),
        instruction_data_hex=data.hex(),
        accounts=accounts,
    )
