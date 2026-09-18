"""The ONE versioned fault vocabulary: what can go wrong, as typed data.

Before this module, a failure's meaning lived in whatever prose the failing seam
interpolated -- "I couldn't get a live model response", "``permission_denied``",
"network fetch denied by the permission gateway: ...". Every consumer re-guessed
severity, retryability and operator guidance from those strings, the same failure wore
a different sentence at every boundary that touched it, and none of it was joinable to
the execution truth that witnessed the turn. This catalog is the replacement:

* every entry is a frozen :class:`FaultSpec` carrying its full operator contract --
  stable code, category, severity, retry policy, a safe user message that never
  interpolates exception text, the operator action, and the OWNING AUTHORITY whose
  boundary maps the failure;
* the catalog is CLOSED: an undeclared code does not exist (:class:`UnknownFaultCodeError`),
  and two entries may never collide -- a duplicate raises :class:`DuplicateFaultCodeError`
  at construction instead of silently overwriting (a dict literal would collapse the
  twin and nobody would ever know which contract survived);
* the catalog is VERSIONED: ``FAULT_SCHEMA`` rides on every durable record and export,
  so a consumer always knows which vocabulary it is reading, and
  :func:`catalog_digest` pins the exact content so a meaning change cannot masquerade
  as a no-op;
* entries carry ``security_relevant``: the security-event plane derives its
  observations from THIS declaration, so there is no second list to drift.

Adding a code is append-and-amend: never rename, never reuse a code for a new meaning
-- consumers and bug reports outlive the build that wrote them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

#: The schema identifier carried by every fault record, export and bug-report block.
FAULT_SCHEMA = "vool.fault.v1"

#: Bumped when an entry's MEANING changes (not when a code is added). Consumers pin on
#: schema + version when they must not silently reinterpret old records.
CATALOG_VERSION = 1

# --- categories: the coarse nature of the failure, one per code. ---
CATEGORY_POLICY = "policy"                # a permission/authority decision stopped it
CATEGORY_AVAILABILITY = "availability"    # something needed was not reachable or offered
CATEGORY_INTEGRITY = "integrity"          # records or claims failed their truth checks
CATEGORY_SECURITY = "security"            # a confinement/credential boundary held
CATEGORY_CANCELLATION = "cancellation"    # a cancellation, which is not a failure at all
CATEGORY_INTERNAL = "internal"            # unclassified -- honest, never guessed

# --- severity: how much attention the fault deserves, from the operator's chair. ---
SEVERITY_INFO = "info"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

# --- retry policy: what a retry would even do. A policy refusal re-run is a second
# refusal; a cancellation re-run overrides the user; an unknown failure re-run is a
# gamble the vocabulary refuses to recommend. ---
RETRY_NEVER = "never"
RETRY_NOW = "retry_now"
RETRY_LATER = "retry_later"
RETRY_AFTER_CHANGE = "retry_after_change"

# --- lifecycle: the fault's own handling state machine (see records.with_lifecycle). ---
LIFECYCLE_RAISED = "raised"      # mapped at the owning boundary, in flight
LIFECYCLE_SERVED = "served"      # the safe user message reached a surface
LIFECYCLE_RESOLVED = "resolved"  # a retry or an operator closed it

_ALLOWED_LIFECYCLE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    LIFECYCLE_RAISED: (LIFECYCLE_SERVED, LIFECYCLE_RESOLVED),
    LIFECYCLE_SERVED: (LIFECYCLE_RESOLVED,),
    LIFECYCLE_RESOLVED: (),  # terminal: a resolved fault does not silently reopen
}

# --- the stable codes. Names are the contract; string values are the wire form. ---
FAULT_PERMISSION_DENIED = "permission_denied"
FAULT_PROVIDER_UNAVAILABLE = "provider_unavailable"
FAULT_PROVIDER_EXHAUSTED = "provider_exhausted"
FAULT_TOOL_UNAVAILABLE = "tool_unavailable"
FAULT_TIMEOUT = "timeout"
FAULT_CANCELLED = "cancelled"
FAULT_CONFINEMENT_REFUSAL = "confinement_refusal"
FAULT_EVIDENCE_CORRUPTION = "evidence_corruption"
FAULT_UNSUPPORTED_CLAIM = "unsupported_claim"
FAULT_CREDENTIAL_FAILURE = "credential_failure"
FAULT_INTEGRITY_VERIFICATION_FAILURE = "integrity_verification_failure"
FAULT_UNKNOWN = "unknown"
# --- the wallet family (authority: core.wallet.*). Append-only, like everything above. ---
FAULT_WALLET_DISABLED = "wallet_disabled"
FAULT_WALLET_NETWORK_DISABLED = "wallet_network_disabled"
FAULT_WALLET_NOT_FOUND = "wallet_not_found"
FAULT_WALLET_SIGNING_UNAVAILABLE = "wallet_signing_unavailable"
FAULT_WALLET_SIGNATURE_INVALID = "wallet_signature_invalid"
FAULT_WALLET_CONFIRMATION_REQUIRED = "wallet_confirmation_required"
FAULT_WALLET_PIN_INVALID = "wallet_pin_invalid"
FAULT_WALLET_LIMIT_EXCEEDED = "wallet_limit_exceeded"
FAULT_WALLET_DUPLICATE_PAYMENT = "wallet_duplicate_payment"
FAULT_WALLET_APPROVAL_REJECTED = "wallet_approval_rejected"
FAULT_WALLET_X402_CAP_EXCEEDED = "wallet_x402_cap_exceeded"
FAULT_WALLET_CARD_DATA_REFUSED = "wallet_card_data_refused"
FAULT_WALLET_SIMULATION_FAILED = "wallet_simulation_failed"
FAULT_WALLET_BROADCAST_FAILED = "wallet_broadcast_failed"
FAULT_WALLET_LEGACY_SURFACE_RETIRED = "wallet_legacy_surface_retired"
FAULT_WALLET_EXPORT_REFUSED = "wallet_export_refused"
FAULT_WALLET_CHAIN_IDENTITY_MISMATCH = "wallet_chain_identity_mismatch"
FAULT_WALLET_OUTBOUND_REFUSED = "wallet_outbound_refused"
FAULT_EVM_POCKET_CUSTODY_UNAVAILABLE = "evm_pocket_custody_unavailable"
FAULT_X402_SCHEME_UNAVAILABLE = "x402_scheme_unavailable"
FAULT_WALLET_DEPENDENCY_UNAVAILABLE = "wallet_dependency_unavailable"
FAULT_WALLET_ENVIRONMENT_INACTIVE = "wallet_environment_inactive"
FAULT_WALLET_AMOUNT_INVALID = "wallet_amount_invalid"
FAULT_WALLET_CALLER_REFUSED = "wallet_caller_refused"
FAULT_WALLET_UNLOCK_THROTTLED = "wallet_unlock_throttled"
FAULT_WALLET_BACKUP_UNAVAILABLE = "wallet_backup_unavailable"
FAULT_WALLET_RECOVERY_REFUSED = "wallet_recovery_refused"
FAULT_WALLET_STORAGE_CLASS_REFUSED = "wallet_storage_class_refused"
FAULT_WALLET_SETUP_STATE_INVALID = "wallet_setup_state_invalid"
FAULT_WALLET_CREDENTIAL_MISMATCH = "wallet_credential_mismatch"
FAULT_WALLET_QUOTE_UNAVAILABLE = "wallet_quote_unavailable"
FAULT_WALLET_QUOTE_EXPIRED = "wallet_quote_expired"
FAULT_WALLET_QUOTE_MISMATCH = "wallet_quote_mismatch"
FAULT_WALLET_INSUFFICIENT_FUNDS = "wallet_insufficient_funds"
FAULT_WALLET_RECIPIENT_REFUSED = "wallet_recipient_refused"
FAULT_WALLET_REQUEST_AMBIGUOUS = "wallet_request_ambiguous"


class FaultVocabularyError(Exception):
    """Base class for vocabulary violations (unknown codes, duplicates, bad transitions)."""


class DuplicateFaultCodeError(FaultVocabularyError):
    """Two catalog entries claimed one code -- construction refuses rather than colliding."""


class UnknownFaultCodeError(FaultVocabularyError):
    """A code nobody declared was looked up -- the catalog is closed, never guessed."""


class InvalidFaultTransitionError(FaultVocabularyError):
    """A lifecycle transition the state machine does not allow (including reopening)."""


@dataclass(frozen=True)
class FaultSpec:
    """One fault family's operator contract. Frozen: meanings do not mutate in place."""

    code: str
    category: str
    severity: str
    retry: str
    #: Safe to show a user. NEVER carries exception text, paths or identifiers --
    #: those live (redacted) on the record, not in the vocabulary.
    user_message: str
    #: What the operator should do about this class of failure.
    operator_action: str
    #: The seam whose boundary owns mapping this failure. One authority per code.
    authority: str
    #: Whether recording this fault opens a security-event observation.
    security_relevant: bool = False

    @property
    def retryable(self) -> bool:
        return self.retry != RETRY_NEVER


def build_catalog(specs: tuple[FaultSpec, ...] | list[FaultSpec]) -> dict[str, FaultSpec]:
    """Index the entries, refusing duplicate codes instead of silently collapsing them.

    A plain dict literal would let a copy-pasted code overwrite its twin at import time
    and the survivor's contract would quietly become the truth. Construction validates.
    """
    catalog: dict[str, FaultSpec] = {}
    for spec in specs:
        code = str(spec.code or "").strip()
        if not code:
            raise FaultVocabularyError("a fault entry without a code cannot be indexed")
        if code in catalog:
            raise DuplicateFaultCodeError(f"fault code declared twice: {code}")
        catalog[code] = spec
    return catalog


#: The catalog. One entry per failure family the runtime maps at an owning boundary.
#: The ``authority`` names the module whose boundary owns the mapping -- the seam a
#: debugger walks to first, written down where the code can check it.
_SPECS: tuple[FaultSpec, ...] = (
    FaultSpec(
        code=FAULT_PERMISSION_DENIED,
        category=CATEGORY_POLICY,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_NEVER,
        user_message="That action was not permitted, so nothing was changed.",
        operator_action="Check the permission policy or approval settings that govern this action.",
        authority="core.runtime_execution_tools",
        security_relevant=True,
    ),
    FaultSpec(
        code=FAULT_PROVIDER_UNAVAILABLE,
        category=CATEGORY_AVAILABILITY,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_LATER,
        user_message="The model provider could not be reached. You can try again in a moment.",
        operator_action="Check the provider's status, network reachability, and configured endpoints.",
        authority="core.turn_model_call_ledger",
    ),
    FaultSpec(
        code=FAULT_PROVIDER_EXHAUSTED,
        category=CATEGORY_AVAILABILITY,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_LATER,
        user_message="The model provider's usage limit is reached. You can try again later.",
        operator_action="Check quota, rate limits and billing for the configured provider.",
        authority="core.turn_model_call_ledger",
    ),
    FaultSpec(
        code=FAULT_TOOL_UNAVAILABLE,
        category=CATEGORY_AVAILABILITY,
        severity=SEVERITY_LOW,
        retry=RETRY_AFTER_CHANGE,
        user_message=(
            "That tool is not available right now, so this part was not done. "
            "Enabling it is needed before you retry."
        ),
        operator_action="Enable the tool for this mode in settings, or choose a different approach.",
        authority="core.runtime_execution_tools",
    ),
    FaultSpec(
        code=FAULT_TIMEOUT,
        category=CATEGORY_AVAILABILITY,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_NOW,
        user_message="This took too long and was stopped. Trying again may succeed.",
        operator_action="If timeouts repeat, check network latency and provider responsiveness.",
        authority="core.remote_fetch_policy",
    ),
    FaultSpec(
        code=FAULT_CANCELLED,
        category=CATEGORY_CANCELLATION,
        severity=SEVERITY_INFO,
        retry=RETRY_NEVER,
        user_message="This was cancelled, so it did not finish.",
        operator_action="No action needed; start again if you want it done.",
        authority="core.remote_fetch_policy",
    ),
    FaultSpec(
        code=FAULT_CONFINEMENT_REFUSAL,
        category=CATEGORY_SECURITY,
        severity=SEVERITY_HIGH,
        retry=RETRY_NEVER,
        user_message=(
            "A file change outside the allowed scope was blocked. "
            "Nothing outside the scope was touched."
        ),
        operator_action="If this change is wanted, request it explicitly so it is inside the authorized scope.",
        authority="core.agent_runtime.builder.app_builder",
        security_relevant=True,
    ),
    FaultSpec(
        code=FAULT_EVIDENCE_CORRUPTION,
        category=CATEGORY_INTEGRITY,
        severity=SEVERITY_HIGH,
        retry=RETRY_NEVER,
        user_message=(
            "This answer's supporting records failed a consistency check, "
            "so treat the answer as unverified."
        ),
        operator_action="Investigate this turn's execution ledger against the runtime event stream.",
        authority="core.execution_truth",
        security_relevant=True,
    ),
    FaultSpec(
        code=FAULT_UNSUPPORTED_CLAIM,
        category=CATEGORY_INTEGRITY,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_NEVER,
        user_message=(
            "One or more statements were not supported by the retrieved sources and were withheld."
        ),
        operator_action="No action needed -- this is the grounding gate working. Check retrieval quality if frequent.",
        authority="core.grounding_publication",
    ),
    FaultSpec(
        code=FAULT_CREDENTIAL_FAILURE,
        category=CATEGORY_SECURITY,
        severity=SEVERITY_HIGH,
        retry=RETRY_NEVER,
        user_message="A stored credential could not be read. Re-enter it in settings to continue.",
        operator_action="The vault entry failed to decrypt; re-store the credential or check the vault key.",
        authority="core.credential_store",
        security_relevant=True,
    ),
    FaultSpec(
        code=FAULT_INTEGRITY_VERIFICATION_FAILURE,
        category=CATEGORY_INTEGRITY,
        severity=SEVERITY_CRITICAL,
        retry=RETRY_NEVER,
        user_message="A record failed its tamper-evidence check. Nothing was silently accepted.",
        operator_action="Treat the affected receipt chain as compromised; investigate before trusting its entries.",
        authority="core.contribution_proof",
        security_relevant=True,
    ),
    FaultSpec(
        code=FAULT_UNKNOWN,
        category=CATEGORY_INTERNAL,
        severity=SEVERITY_MEDIUM,
        retry=RETRY_NEVER,
        user_message="Something went wrong on this machine while handling that. You can try again.",
        operator_action="See the fault's redacted cause chain in diagnostics; an unknown cause is never retried blind.",
        authority="core.faults.mapping",
    ),
)


#: The wallet family. Every code's boundary is a core.wallet module; the user messages state
#: what did NOT happen (nothing sent, nothing stored) because that is what a payer needs first.
_WALLET_SPECS: tuple[FaultSpec, ...] = (
    FaultSpec(code=FAULT_WALLET_DISABLED, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="The wallet is switched off, so no payment was proposed or sent. Enable it first, then try again.",
              operator_action="Enable the wallet explicitly (VOOL_WALLET_ENABLED=1) only if payments are wanted on this install.",
              authority="core.wallet.custody"),
    FaultSpec(code=FAULT_WALLET_NETWORK_DISABLED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="That network is not one this wallet declares for this action, so nothing was created or sent.",
              operator_action="Use one of the declared network rows; undeclared chains, aliases and look-alike names are refused by construction.",
              authority="core.wallet.custody", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_ENVIRONMENT_INACTIVE, category=CATEGORY_SECURITY, severity=SEVERITY_MEDIUM, retry=RETRY_AFTER_CHANGE,
              user_message="That network belongs to the other network environment (Mainnet or Test networks), so nothing was created, proposed or signed. Switch in Settings, Crypto, Developer options, then try again.",
              operator_action="Choose the network environment deliberately in Crypto settings; accounts, balances and receipts never move between environments.",
              authority="core.wallet.environment", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_AMOUNT_INVALID, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="That amount is not an exact amount this coin can hold, so nothing was proposed. Check the amount, then try again.",
              operator_action="Send a plain decimal amount no more precise than the coin allows and within the pilot's per-transfer ceiling.",
              authority="core.wallet.amounts"),
    FaultSpec(code=FAULT_WALLET_CALLER_REFUSED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_AFTER_CHANGE,
              user_message="This wallet action can only be started from VOOL's own window on this computer, so nothing was changed, signed or sent. Open VOOL and try again there.",
              operator_action="Drive trusted wallet doors from the served page or the native window; a page on another origin, a framed page, a text/plain form and the command-dispatch projection are refused by design.",
              authority="core.wallet.caller_binding", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_UNLOCK_THROTTLED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_LATER,
              user_message="There were too many wrong PIN or password attempts, so this wallet is locked for a while and nothing was unlocked. Wait, then try again.",
              operator_action="Failed unlocks are counted durably per wallet and for the whole app, across restarts; the lock lifts on its own after the stated wait.",
              authority="core.wallet.pilot_custody", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_RECOVERY_REFUSED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_AFTER_CHANGE,
              user_message="That recovery proof does not fit this wallet, so nothing was changed: the saved key must be the backup VOOL showed for this exact address, in the format it was shown. Check it and try again, or use the other options.",
              operator_action="Recovery re-seals an existing wallet only from a backup that decodes to the wallet's own key (both derivations agree), or from an enrolled device secret released by fresh user presence; a wrong or foreign key is refused before any write and counted on the recovery throttle only.",
              authority="core.wallet.pilot_custody", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_BACKUP_UNAVAILABLE, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="This wallet's backup key was already shown once, so it cannot be shown again. If you did not save it, cancel setup and do not send funds to this address.",
              operator_action="A Crypto Pilot backup is released exactly once during setup; there is no second reveal and no export for pilot wallets.",
              authority="core.wallet.pilot_custody", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_STORAGE_CLASS_REFUSED, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_AFTER_CHANGE,
              user_message="This installation keeps its device key only in memory, so a wallet created here could not be opened after a restart. Nothing was created. Switch key storage to a lasting mode, then try again.",
              operator_action="Crypto Pilot wallets are refused while VOOL_KEY_STORAGE_MODE is ephemeral; use file or keyring storage.",
              authority="core.wallet.pilot_custody"),
    FaultSpec(code=FAULT_WALLET_SETUP_STATE_INVALID, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="That setup step does not apply to this wallet right now, so nothing changed. Check the wallet's setup status, then try again.",
              operator_action="Setup moves generating, awaiting backup, backup revealed, ready; cancel and resume never reveal a backup twice.",
              authority="core.wallet.pilot_custody"),
    FaultSpec(code=FAULT_WALLET_CREDENTIAL_MISMATCH, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="The two entries did not match, so nothing was created. Enter the same PIN or password twice, then try again.",
              operator_action="A Crypto Pilot credential is confirmed before any key material exists.",
              authority="core.wallet.pilot_custody"),
    FaultSpec(code=FAULT_WALLET_QUOTE_UNAVAILABLE, category=CATEGORY_AVAILABILITY, severity=SEVERITY_MEDIUM, retry=RETRY_LATER,
              user_message="The network did not give a complete answer for this transfer's balance or fee, so no approval was prepared and nothing was sent. Try again in a moment.",
              operator_action="Unknown chain data is never treated as zero; check the row's endpoint and its identity proof.",
              authority="core.wallet.quotes"),
    FaultSpec(code=FAULT_WALLET_QUOTE_EXPIRED, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="This transfer preview expired or was replaced, so it cannot be approved and nothing was sent. Refresh the preview, then try again.",
              operator_action="A quote lives for a short window and is superseded by a refresh or an environment switch; approval never carries over.",
              authority="core.wallet.quotes"),
    FaultSpec(code=FAULT_WALLET_REQUEST_AMBIGUOUS, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="More than one account or network fits this transfer, so nothing was proposed. Say which account or network you mean, then try again.",
              operator_action="A transfer names one account on one network; the candidates are listed in the fault context.",
              authority="core.wallet.transfers"),
    FaultSpec(code=FAULT_WALLET_QUOTE_MISMATCH, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="The approval no longer matches the transfer that was previewed, so nothing was signed or sent. Start again from a fresh preview.",
              operator_action="An approval binds one quote digest; a changed account, chain, recipient, amount, environment or fee ceiling invalidates it.",
              authority="core.wallet.quotes", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_INSUFFICIENT_FUNDS, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="The balance does not cover the amount plus the most this network fee can be, so nothing was prepared. Lower the amount or add funds, then try again.",
              operator_action="Spendable excludes other in-flight holds; a Solana remainder must be zero-free and at least the rent minimum in the pilot.",
              authority="core.wallet.quotes"),
    FaultSpec(code=FAULT_WALLET_RECIPIENT_REFUSED, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_AFTER_CHANGE,
              user_message="That recipient cannot safely receive this transfer on this network, so nothing was prepared. Check the address, then try again.",
              operator_action="Refused: a new Solana account below the rent minimum, an executable or off-curve account, a bad EIP-55 checksum, the zero address, precompiles and system contracts.",
              authority="core.wallet.quotes"),
    FaultSpec(code=FAULT_WALLET_NOT_FOUND, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="That wallet, proposal or destination was not recognised, so nothing was done. Check it and try again.",
              operator_action="Check the wallet id, proposal id or destination address and try again.",
              authority="core.wallet.custody"),
    FaultSpec(code=FAULT_WALLET_SIGNING_UNAVAILABLE, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_AFTER_CHANGE,
              user_message="This wallet cannot sign from here: it is watch-only or its external signer is not connected. Nothing was sent; connect a signer, then try again.",
              operator_action="Connect the external wallet, or create a VOOL Wallet after reading its warning.",
              authority="core.wallet.signers"),
    FaultSpec(code=FAULT_WALLET_SIGNATURE_INVALID, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="The signature returned for this payment did not match the wallet's key, so it was not broadcast.",
              operator_action="Check the connected signer; a mismatched signature is refused before broadcast.",
              authority="core.wallet.signers", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_CONFIRMATION_REQUIRED, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="That step needs the typed confirmation first, so nothing was changed. Confirm it, then try again.",
              operator_action="Read the warning and type the exact confirmation phrase to proceed.",
              authority="core.wallet.custody"),
    FaultSpec(code=FAULT_WALLET_PIN_INVALID, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="The PIN did not meet the policy or did not unlock the wallet, so nothing was signed. Check the PIN and try again.",
              operator_action="Use a 6 to 12 digit PIN; a wrong PIN leaves the sealed key untouched.",
              authority="core.wallet.custody"),
    FaultSpec(code=FAULT_WALLET_LIMIT_EXCEEDED, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_NEVER,
              user_message="That payment is above a spending limit, so it was not sent.",
              operator_action="Review the per-transaction, daily and per-destination limits before retrying.",
              authority="core.wallet.limits", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_DUPLICATE_PAYMENT, category=CATEGORY_INTEGRITY, severity=SEVERITY_MEDIUM, retry=RETRY_NEVER,
              user_message="This payment was already proposed or sent, so it was not sent again.",
              operator_action="Check the existing proposal's receipt; use a new idempotency key for a genuinely new payment.",
              authority="core.wallet.proposals", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_APPROVAL_REJECTED, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_NEVER,
              user_message="The approval for this payment was not accepted, so nothing was signed or sent.",
              operator_action="Approve the exact proposal with the owner's PIN or biometric; approvals do not transfer between proposals.",
              authority="core.wallet.lifecycle", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_X402_CAP_EXCEEDED, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM, retry=RETRY_NEVER,
              user_message="A paid resource asked for more than the automatic payment cap, so it was not paid.",
              operator_action="Raise VOOL_WALLET_X402_CAP_MINOR deliberately, or pay the resource by an explicit proposal.",
              authority="core.wallet.x402", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_CARD_DATA_REFUSED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="Card numbers and security codes are never stored here; only a provider token is accepted. Nothing was saved.",
              operator_action="Tokenize the card with the provider and register the token reference instead.",
              authority="core.wallet.cards", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_SIMULATION_FAILED, category=CATEGORY_AVAILABILITY, severity=SEVERITY_MEDIUM, retry=RETRY_LATER,
              user_message="The payment did not pass simulation, so it was not sent. Check the balance and destination, then try again.",
              operator_action="Check the balance, the destination and the test-network RPC, then propose again.",
              authority="core.wallet.lifecycle"),
    FaultSpec(code=FAULT_WALLET_BROADCAST_FAILED, category=CATEGORY_AVAILABILITY, severity=SEVERITY_MEDIUM, retry=RETRY_LATER,
              user_message="The payment could not be broadcast. Check its status before retrying.",
              operator_action="Check the test-network RPC and the proposal's receipt; the spend was not recorded as sent.",
              authority="core.wallet.lifecycle"),
    FaultSpec(code=FAULT_WALLET_LEGACY_SURFACE_RETIRED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="That money path is retired: every payment goes through the wallet's proposal and approval flow. Nothing was signed or sent.",
              operator_action="Use the wallet tools (wallet.propose) or the wallet API; the legacy signer, spender and exporter no longer exist.",
              authority="core.wallet.authority", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_EXPORT_REFUSED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="Private keys are never exported by this runtime. The recovery phrase is shown once at creation and nowhere else. Nothing was revealed.",
              operator_action="Recover a VOOL Wallet from the phrase written down at creation; there is no export door to enable.",
              authority="core.wallet.authority", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_CHAIN_IDENTITY_MISMATCH, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="The chain endpoint did not prove the identity of the network it claims to be, so nothing was signed or sent.",
              operator_action="Check the RPC endpoint for the network; a lying chain id or genesis is refused before any payment.",
              authority="core.wallet.chains", security_relevant=True),
    FaultSpec(code=FAULT_WALLET_OUTBOUND_REFUSED, category=CATEGORY_SECURITY, severity=SEVERITY_HIGH, retry=RETRY_NEVER,
              user_message="That request was not allowed to leave the wallet's approved endpoints, so no connection was made.",
              operator_action="Check the declared RPC origins and payment-resource policy; private, metadata and unapproved hosts are refused.",
              authority="core.wallet.outbound", security_relevant=True),
    FaultSpec(code=FAULT_EVM_POCKET_CUSTODY_UNAVAILABLE, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_NEVER,
              user_message="EVM keys cannot be held on this device: use watch-only or an external wallet signer. Nothing was created.",
              operator_action="Register the account watch-only or connect an external EVM signer (EIP-1193); in-process EVM custody does not exist in this build.",
              authority="core.wallet.custody"),
    FaultSpec(code=FAULT_WALLET_DEPENDENCY_UNAVAILABLE, category=CATEGORY_POLICY, severity=SEVERITY_MEDIUM,
              retry=RETRY_AFTER_CHANGE,
              user_message="This build cannot do EVM signing or ABI encoding: the Ethereum libraries are not installed on this machine. Nothing was signed or sent. Solana, custody and spend ceilings are unaffected; once the libraries are installed, try again.",
              operator_action="Install the EVM extras (eth-abi, eth-utils, eth-account) to enable the EVM lanes; every other wallet lane works without them.",
              authority="core.wallet.evm"),
    FaultSpec(code=FAULT_X402_SCHEME_UNAVAILABLE, category=CATEGORY_POLICY, severity=SEVERITY_LOW, retry=RETRY_AFTER_CHANGE,
              user_message="This payment offer needs a scheme this wallet cannot prove end to end, so nothing was signed. Try an offer whose scheme, token and facilitator are verified for this network.",
              operator_action="Use an offer whose scheme, token and facilitator are verified for the network; partial signing support is never improvised.",
              authority="core.wallet.x402"),
)

_CATALOG: dict[str, FaultSpec] = build_catalog((*_SPECS, *_WALLET_SPECS))


def get_spec(code: str) -> FaultSpec:
    """The contract for one code. Undeclared codes do not exist."""
    spec = _CATALOG.get(str(code or "").strip())
    if spec is None:
        raise UnknownFaultCodeError(f"no such fault code: {code!r}")
    return spec


def all_codes() -> tuple[str, ...]:
    """Every declared code, sorted, so consumers never depend on declaration order."""
    return tuple(sorted(_CATALOG))


def all_specs() -> tuple[FaultSpec, ...]:
    """Every declared contract, in the same stable order as :func:`all_codes`."""
    return tuple(_CATALOG[code] for code in all_codes())


def allowed_fault_transition(current: str, new: str) -> bool:
    """Whether the fault lifecycle may move from ``current`` to ``new``."""
    return str(new or "").strip() in _ALLOWED_LIFECYCLE_TRANSITIONS.get(str(current or "").strip(), ())


def export_catalog() -> dict[str, object]:
    """The machine-readable catalog: stable, sorted, JSON-safe, self-describing.

    This is the export other tools (dashboards, bug reports, external validators)
    read; its shape is part of the contract and only ever grows additively.
    """
    return {
        "schema": FAULT_SCHEMA,
        "catalog_version": CATALOG_VERSION,
        "faults": [
            {
                "code": spec.code,
                "category": spec.category,
                "severity": spec.severity,
                "retry": spec.retry,
                "retryable": spec.retryable,
                "user_message": spec.user_message,
                "operator_action": spec.operator_action,
                "authority": spec.authority,
                "security_relevant": spec.security_relevant,
            }
            for spec in all_specs()
        ],
    }


def catalog_digest() -> str:
    """A stable fingerprint of the catalog's exact content (sha256 of the canonical export)."""
    canonical = json.dumps(export_catalog(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CATALOG_VERSION",
    "CATEGORY_AVAILABILITY",
    "CATEGORY_CANCELLATION",
    "CATEGORY_INTEGRITY",
    "CATEGORY_INTERNAL",
    "CATEGORY_POLICY",
    "CATEGORY_SECURITY",
    "FAULT_CANCELLED",
    "FAULT_CONFINEMENT_REFUSAL",
    "FAULT_CREDENTIAL_FAILURE",
    "FAULT_EVIDENCE_CORRUPTION",
    "FAULT_INTEGRITY_VERIFICATION_FAILURE",
    "FAULT_PERMISSION_DENIED",
    "FAULT_PROVIDER_EXHAUSTED",
    "FAULT_PROVIDER_UNAVAILABLE",
    "FAULT_SCHEMA",
    "FAULT_TIMEOUT",
    "FAULT_TOOL_UNAVAILABLE",
    "FAULT_UNKNOWN",
    "FAULT_UNSUPPORTED_CLAIM",
    "LIFECYCLE_RAISED",
    "LIFECYCLE_RESOLVED",
    "LIFECYCLE_SERVED",
    "RETRY_AFTER_CHANGE",
    "RETRY_LATER",
    "RETRY_NEVER",
    "RETRY_NOW",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_INFO",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "DuplicateFaultCodeError",
    "FaultSpec",
    "FaultVocabularyError",
    "InvalidFaultTransitionError",
    "UnknownFaultCodeError",
    "all_codes",
    "all_specs",
    "allowed_fault_transition",
    "build_catalog",
    "catalog_digest",
    "export_catalog",
    "get_spec",
]
