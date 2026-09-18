"""Contact endpoints: the concrete, separately identified ways to reach one contact.

An endpoint is one email address, one phone number, one messaging identity on a named channel, or one public wallet
address bound to one network. Each endpoint has its own id, a canonical identity used for matching and change
detection, and a verification state saying where it came from. Nothing here sends, dials or pays. A saved messaging
identity does not mean VOOL can deliver on that channel (``messaging_delivery``).

Wallet addresses are validated by the wallet owner (``core.wallet.chains`` and ``core.wallet.usepod.canonical_recipient``):
an EVM address is one identity per network (lowercase hex; EIP-55 case is a checksum and a wrong checksum is refused),
a Solana address keeps its exact case and must decode to 32 bytes. The same EVM-looking address saved for Base and for
Ethereum is two endpoints, never one payment destination.

Contacts never stores key material: every text field is refused when it carries a secret shape the runtime's redaction
authority recognises, a private-key shape, a key byte array, a run of recovery-phrase words or a labelled PIN/password.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from core.contacts.names import clean_display

KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_MESSAGING = "messaging"
KIND_WALLET = "wallet"
KINDS: tuple[str, ...] = (KIND_EMAIL, KIND_PHONE, KIND_MESSAGING, KIND_WALLET)
KIND_LABELS: dict[str, str] = {KIND_EMAIL: "Email", KIND_PHONE: "Phone", KIND_MESSAGING: "Messaging", KIND_WALLET: "Wallet address"}

#: typed by the owner (in Contacts, or in the owner's own chat message)
VERIFICATION_USER_ENTERED = "user_entered"
#: the owner confirmed a suggestion or an imported entry
VERIFICATION_USER_CONFIRMED = "user_confirmed"
#: copied from an address book source; VOOL has not checked that it reaches the person
VERIFICATION_IMPORTED = "imported_unverified"
VERIFICATIONS: tuple[str, ...] = (VERIFICATION_USER_ENTERED, VERIFICATION_USER_CONFIRMED, VERIFICATION_IMPORTED)

MAX_LABEL_CHARS = 40
MAX_VALUE_CHARS = 320
MAX_ACCOUNT_CHARS = 120

_KIND_ALIASES = {"e-mail": KIND_EMAIL, "mail": KIND_EMAIL, "tel": KIND_PHONE, "telephone": KIND_PHONE, "mobile": KIND_PHONE,
                 "message": KIND_MESSAGING, "im": KIND_MESSAGING, "chat": KIND_MESSAGING, "crypto": KIND_WALLET}

MESSAGING_CHANNELS: dict[str, dict[str, Any]] = {
    "telegram": {"label": "Telegram", "account_required": False, "hint": "a @username or a phone number"},
    "signal": {"label": "Signal", "account_required": False, "hint": "a phone number or a Signal username"},
    "whatsapp": {"label": "WhatsApp", "account_required": False, "hint": "a phone number"},
    "imessage": {"label": "iMessage", "account_required": False, "hint": "a phone number or an Apple Account email"},
    "sms": {"label": "SMS", "account_required": False, "hint": "a phone number"},
    "slack": {"label": "Slack", "account_required": True, "hint": "the member ID or handle inside that workspace"},
    "teams": {"label": "Microsoft Teams", "account_required": True, "hint": "the person's work address in that organisation"},
    "discord": {"label": "Discord", "account_required": False, "hint": "a Discord username"},
    "matrix": {"label": "Matrix", "account_required": False, "hint": "a full Matrix ID such as @name:example.org"},
    "other": {"label": "Other service", "account_required": True, "hint": "the identifier exactly as that service shows it"},
}
_CHANNEL_ALIASES = {"tg": "telegram", "wa": "whatsapp", "text": "sms", "ms teams": "teams", "microsoft teams": "teams", "element": "matrix"}


class EndpointError(ValueError):
    """A value that cannot be saved as this endpoint. ``reason`` is stable; ``message`` is for people."""

    def __init__(self, reason: str, message: str, *, field: str = "value") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.field = field

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message, "field": self.field}


# --- key material ------------------------------------------------------------------------------------------------

_HEX_KEY_RE = re.compile(r"(?<![0-9A-Za-z])(?:0x)?[0-9a-fA-F]{64}(?![0-9A-Za-z])")
_BYTE_ARRAY_RE = re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){31,}\d{1,3}\s*\]")
_LABELLED_PIN_RE = re.compile(r"(?i)\b(?:pin(?:\s*code)?|passcode|password|passwd)\b\s*(?:is\s+|[:=#-]\s*)\S{3,}")
_WORD_RE = re.compile(r"[A-Za-z]+")
RECOVERY_WORD_RUN = 12


def _recovery_word_run(value: str) -> int:
    from core.wallet.mnemonic import INDEX

    best = run = 0
    for word in _WORD_RE.findall(value.lower()):
        run = run + 1 if word in INDEX else 0
        best = max(best, run)
    return best


def secret_material_reason(text: object) -> str:
    """'' when the text carries no key material, else which shape was found. Used to refuse, never to store."""
    value = str(text or "")
    if not value.strip():
        return ""
    from core.secret_redaction import contains_secret

    if contains_secret(value):
        return "secret_shaped"
    if _HEX_KEY_RE.search(value):
        return "private_key_shaped"
    if _BYTE_ARRAY_RE.search(value):
        return "key_bytes_shaped"
    if _LABELLED_PIN_RE.search(value):
        return "pin_or_password_shaped"
    if _recovery_word_run(value) >= RECOVERY_WORD_RUN:
        return "recovery_phrase_shaped"
    return ""


def refuse_secret_material(text: object, *, field: str) -> None:
    reason = secret_material_reason(text)
    if reason:
        raise EndpointError(
            "secret_material_refused",
            "Contacts never stores private keys, recovery phrases, passwords or PINs. Nothing was saved.",
            field=field,
        )


# --- per kind ----------------------------------------------------------------------------------------------------

_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_DOMAIN_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_TLD_RE = re.compile(r"^(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59})$")
_PHONE_CHARS_RE = re.compile(r"^\+?[0-9][0-9\s().\-]*$")


def _plain(raw: object) -> str:
    return str(raw or "").strip()[:MAX_VALUE_CHARS]


def normalize_email(raw: object) -> tuple[str, str]:
    """``(value, canonical)``. ``Name <addr>`` and ``mailto:`` are accepted; the canonical form is lowercase."""
    text = _plain(raw)
    if "<" in text or ">" in text:
        text = parseaddr(text)[1].strip()
    if text.lower().startswith("mailto:"):
        text = text[7:].strip()
    invalid = EndpointError("invalid_email", f"'{text[:80]}' is not an email address.")
    if text.count("@") != 1 or len(text) > 254:
        raise invalid
    local, domain = text.split("@")
    domain = domain.rstrip(".")
    try:
        ascii_domain = domain.encode("idna").decode("ascii") if any(ord(ch) > 127 for ch in domain) else domain
    except UnicodeError:
        raise invalid from None
    labels = ascii_domain.split(".")
    if len(local) > 64 or not _LOCAL_RE.match(local) or len(labels) < 2:
        raise invalid
    if not all(_DOMAIN_LABEL_RE.match(label) for label in labels) or not _TLD_RE.match(labels[-1]):
        raise invalid
    return f"{local}@{domain.lower()}", f"{local.lower()}@{ascii_domain.lower()}"


def looks_like_email(raw: object) -> bool:
    try:
        normalize_email(raw)
    except EndpointError:
        return False
    return True


def normalize_phone(raw: object) -> tuple[str, str]:
    """``(value, canonical)``. No country is assumed: ``+`` stays only when the owner wrote it."""
    text = _plain(raw)
    if text.lower().startswith("tel:"):
        text = text[4:].strip()
    if not _PHONE_CHARS_RE.match(text):
        raise EndpointError("invalid_phone", f"'{text[:40]}' is not a phone number.")
    digits = re.sub(r"\D", "", text)
    if not 5 <= len(digits) <= 15:
        raise EndpointError("invalid_phone", f"'{text[:40]}' is not a phone number (5 to 15 digits).")
    return text, ("+" if text.startswith("+") else "") + digits


def looks_like_phone(raw: object) -> bool:
    try:
        normalize_phone(raw)
    except EndpointError:
        return False
    return True


def channel_key(channel: object) -> str:
    value = clean_display(channel).casefold()
    return _CHANNEL_ALIASES.get(value, value)


def channel_label(channel: object) -> str:
    return str(MESSAGING_CHANNELS.get(channel_key(channel), {}).get("label") or clean_display(channel))


def normalize_messaging(raw: object, *, channel: object, provider_account: object) -> tuple[str, str, str, str]:
    """``(value, canonical, channel, provider_account)`` for one messaging identity on one channel."""
    key = channel_key(channel)
    spec = MESSAGING_CHANNELS.get(key)
    if spec is None:
        raise EndpointError("messaging_channel_required", "Say which messaging service this identity belongs to.", field="channel")
    account = clean_display(provider_account)[:MAX_ACCOUNT_CHARS]
    if spec["account_required"] and not account:
        raise EndpointError(
            "messaging_account_required",
            f"A {spec['label']} identity only means something inside one workspace or account: name it.",
            field="provider_account",
        )
    text = _plain(raw)
    if not text:
        raise EndpointError("messaging_identity_required", f"Enter {spec['hint']}.")
    invalid = EndpointError("invalid_messaging_identity", f"'{text[:60]}' is not {spec['hint']} for {spec['label']}.")
    if key in {"whatsapp", "sms"}:
        value, canonical = normalize_phone(text)
    elif key in {"telegram", "signal", "imessage"} and looks_like_phone(text):
        value, canonical = normalize_phone(text)
    elif key == "telegram":
        handle = text.lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", handle):
            raise invalid
        value, canonical = f"@{handle}", handle.lower()
    elif key == "signal":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,31}\.\d{2,9}", text):
            raise invalid
        value, canonical = text, text.lower()
    elif key in {"imessage", "teams"}:
        try:
            value, canonical = normalize_email(text)
        except EndpointError:
            raise invalid from None
    elif key == "slack":
        if re.fullmatch(r"[UW][A-Z0-9]{6,20}", text):
            value, canonical = text, text
        elif re.fullmatch(r"@?[A-Za-z0-9._-]{1,80}", text):
            value, canonical = text, text.lstrip("@").lower()
        else:
            raise invalid
    elif key == "discord":
        if not (re.fullmatch(r"[A-Za-z0-9_.]{2,32}", text) or re.fullmatch(r"[^#@:]{2,32}#\d{4}", text)):
            raise invalid
        value, canonical = text, text.lower()
    elif key == "matrix":
        if not re.fullmatch(r"@[A-Za-z0-9._=\-/+]+:[A-Za-z0-9.\-]+(?::\d{1,5})?", text):
            raise invalid
        value, canonical = text, text.lower()
    else:
        value, canonical = text, clean_display(text).casefold()
    return value, canonical, key, account


@dataclass(frozen=True)
class WalletIdentity:
    value: str
    canonical: str
    chain_network: str
    chain_family: str
    chain_environment: str
    chain_key: str
    network_display: str


def normalize_wallet(raw: object, *, network: object) -> WalletIdentity:
    """One public address on one declared network, validated by the wallet owner's own rules."""
    text = str(raw or "").strip()
    wanted = clean_display(network)
    if not wanted:
        raise EndpointError("wallet_network_required", "A wallet address belongs to one network: choose the network.", field="network")
    from core.wallet import chains

    try:
        spec = chains.resolve_network(wanted)
    except Exception:
        raise EndpointError("wallet_network_unknown", f"'{wanted[:60]}' is not a network this wallet declares.", field="network") from None
    if spec.is_evm:
        if not chains.EVM_ADDRESS_RE.match(text):
            raise EndpointError("wallet_address_invalid", f"That is not an address on {spec.display_name}.")
        body = text[2:]
        if body != body.lower() and body != body.upper():
            try:
                from eth_utils import is_checksum_address
            except ImportError:
                raise EndpointError(
                    "wallet_checksum_unverifiable",
                    "This mixed-case address carries a checksum this build cannot check. Paste it in lowercase to save it unchecked.",
                ) from None
            if not is_checksum_address(text):
                raise EndpointError("wallet_address_checksum", "The address's capital letters do not match its checksum: a character is probably wrong.")
        if int(body, 16) == 0:
            raise EndpointError("wallet_address_invalid", "The zero address cannot receive a payment.")
    from core.wallet.usepod import canonical_recipient

    canonical = canonical_recipient(spec, text)
    if canonical is None:
        raise EndpointError("wallet_address_invalid", f"That is not an address on {spec.display_name}.")
    return WalletIdentity(
        value=text, canonical=canonical, chain_network=spec.network, chain_family=spec.family,
        chain_environment=spec.environment, chain_key=spec.chain_key, network_display=spec.display_name,
    )


def network_display(chain_network: str) -> str:
    if not chain_network:
        return ""
    try:
        from core.wallet import chains

        return chains.resolve_network(chain_network).display_name
    except Exception:
        return chain_network


# --- one endpoint --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedEndpoint:
    kind: str
    value: str
    canonical: str
    label: str = ""
    channel: str = ""
    provider_account: str = ""
    chain_network: str = ""
    chain_family: str = ""
    chain_environment: str = ""

    def identity(self) -> tuple[str, str, str, str, str]:
        """What makes two endpoints the same destination. The label is presentation, not identity."""
        return (self.kind, self.canonical, self.chain_network, self.channel, self.provider_account.casefold())


def kind_key(kind: object) -> str:
    value = clean_display(kind).casefold()
    return _KIND_ALIASES.get(value, value)


def normalize_endpoint(raw: Any) -> NormalizedEndpoint:
    """Validate one endpoint given as ``{kind, value, label?, channel?, provider_account?, network?}``."""
    if not isinstance(raw, dict):
        raise EndpointError("endpoint_malformed", "Each endpoint needs a kind and a value.", field="endpoint")
    kind = kind_key(raw.get("kind"))
    if kind not in KINDS:
        raise EndpointError("endpoint_kind_unknown", "An endpoint is an email, a phone, a messaging identity or a wallet address.", field="kind")
    label = clean_display(raw.get("label"))[:MAX_LABEL_CHARS]
    account_raw = raw.get("provider_account", raw.get("account", raw.get("workspace", "")))
    for field, text in (("value", raw.get("value")), ("label", label), ("provider_account", account_raw)):
        refuse_secret_material(text, field=field)
    if kind == KIND_EMAIL:
        value, canonical = normalize_email(raw.get("value"))
        return NormalizedEndpoint(kind=kind, value=value, canonical=canonical, label=label)
    if kind == KIND_PHONE:
        value, canonical = normalize_phone(raw.get("value"))
        return NormalizedEndpoint(kind=kind, value=value, canonical=canonical, label=label)
    if kind == KIND_MESSAGING:
        value, canonical, channel, account = normalize_messaging(raw.get("value"), channel=raw.get("channel"), provider_account=account_raw)
        return NormalizedEndpoint(kind=kind, value=value, canonical=canonical, label=label, channel=channel, provider_account=account)
    wallet = normalize_wallet(raw.get("value"), network=raw.get("network", raw.get("chain_network", "")))
    return NormalizedEndpoint(
        kind=kind, value=wallet.value, canonical=wallet.canonical, label=label, chain_network=wallet.chain_network,
        chain_family=wallet.chain_family, chain_environment=wallet.chain_environment,
    )


def endpoint_fingerprint(*, contact_id: str, endpoint_id: str, kind: str, canonical: str, chain_network: str = "",
                         channel: str = "", provider_account: str = "") -> str:
    """The destination identity an approval binds. A changed value, network, channel or account changes it; a label does not."""
    blob = json.dumps({
        "v": 1, "contact_id": contact_id, "endpoint_id": endpoint_id, "kind": kind, "canonical": canonical,
        "chain_network": chain_network, "channel": channel, "provider_account": provider_account.casefold(),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"contacts.endpoint|{blob}".encode()).hexdigest()


# --- delivery truth for messaging -----------------------------------------------------------------------------------

#: channel -> transport callable. Empty in this build: the Telegram and Discord bridges relay configured channels, not
#: people. A future person-directed transport registers here and its own owner keeps sending and receipts.
_MESSAGING_TRANSPORTS: dict[str, Any] = {}


def register_messaging_transport(channel: str, transport: Any) -> None:
    _MESSAGING_TRANSPORTS[channel_key(channel)] = transport


def messaging_delivery(channel: object) -> dict[str, Any]:
    key = channel_key(channel)
    label = channel_label(key)
    if key in _MESSAGING_TRANSPORTS:
        return {"available": True, "channel": key, "message": f"A {label} transport is registered; sending stays with that transport's own review."}
    return {
        "available": False, "channel": key, "reason": "no_person_transport",
        "message": f"VOOL can show and copy this {label} identity. It has no {label} transport that sends to a person in this build.",
    }
