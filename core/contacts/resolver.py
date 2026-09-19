"""Resolve the person or service a request names into one exact destination, or ask which one.

The one resolver every consumer uses: email draft recipients, transfer proposal recipients, calendar attendees, and a
messaging identity the user wants to see. It never sends, signs or pays.

Rules:

* A request carrying the destination itself (an address) is ``explicit``: it needs no saved contact. Saved contacts
  holding the same destination are reported beside it, never substituted for it.
* A name, alias or single name word that strongly matches exactly one active contact names that contact. Two or more
  strong matches is ``ambiguous_contact``. Close spellings are only suggestions (``not_found`` with suggestions).
* A name that looks like a different saved name (a UTS #39 confusable, core.contacts.identity: T0M for TOM, Cyrillic ТОМ)
  is never chosen. Every lookalike is offered beside the strong matches as ``ambiguous_contact``, each with what tells
  the names apart. A pending suggestion or a pending change never resolves.
* Within the contact, exactly one active endpoint of the requested kind that passes the request's filters (label,
  network, chain, family, environment, channel, account) is ``resolved``. Several is ``ambiguous_endpoint``; none is
  ``no_endpoint``. A wallet address saved for one network never answers for another network.
* A resolution carries a snapshot: the contact and endpoint ids, revisions, exact value and chain/channel identity and
  the endpoint fingerprint. Consumers bind the snapshot into their own approval and call ``verify_snapshot`` before
  acting, so a changed, removed or deleted destination needs a new review instead of silently moving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.contacts import identity, names, store
from core.contacts.endpoints import (
    KIND_EMAIL,
    KIND_LABELS,
    KIND_MESSAGING,
    KIND_PHONE,
    KIND_WALLET,
    KINDS,
    EndpointError,
    channel_key,
    channel_label,
    looks_like_email,
    looks_like_phone,
    network_display,
    normalize_email,
    normalize_messaging,
    normalize_phone,
    normalize_wallet,
)

STATUS_RESOLVED = "resolved"
STATUS_EXPLICIT = "explicit"
STATUS_AMBIGUOUS_CONTACT = "ambiguous_contact"
STATUS_AMBIGUOUS_ENDPOINT = "ambiguous_endpoint"
STATUS_NO_ENDPOINT = "no_endpoint"
STATUS_NOT_FOUND = "not_found"
STATUS_INVALID = "invalid"

SNAPSHOT_VERSION = 1
MAX_SUGGESTIONS = 5
_LABELLED_RE = re.compile(r"^(?P<name>.*\S)\s*\((?P<label>[^()]{1,40})\)\s*$")
_EVM_SHAPE_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SVM_SHAPE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass(frozen=True)
class Resolution:
    status: str
    kind: str
    query: str
    snapshot: dict[str, Any] | None = None
    choices: tuple[dict[str, Any], ...] = ()
    suggestions: tuple[dict[str, Any], ...] = ()
    message: str = ""
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_RESOLVED, STATUS_EXPLICIT)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "kind": self.kind, "query": self.query, "snapshot": self.snapshot,
                "choices": list(self.choices), "suggestions": list(self.suggestions), "message": self.message, "filters": dict(self.filters)}


# --- snapshots -------------------------------------------------------------------------------------------------------

def contact_snapshot(contact: dict[str, Any], endpoint: dict[str, Any], *, matched_by: str) -> dict[str, Any]:
    source = endpoint.get("source") or None
    return {
        "version": SNAPSHOT_VERSION, "resolution": "contact", "matched_by": matched_by,
        "contact_id": contact["contact_id"], "contact_revision": int(contact.get("revision") or 0), "display_name": contact.get("display_name", ""),
        "contact_kind": contact.get("kind", "person"),
        "endpoint_id": endpoint["endpoint_id"], "endpoint_revision": int(endpoint.get("revision") or 0), "kind": endpoint["kind"],
        "label": endpoint.get("label", ""), "value": endpoint["value"], "canonical": endpoint["canonical"],
        "chain_network": endpoint.get("chain_network", ""), "chain_family": endpoint.get("chain_family", ""),
        "chain_environment": endpoint.get("chain_environment", ""), "network_display": network_display(str(endpoint.get("chain_network") or "")),
        "channel": endpoint.get("channel", ""), "provider_account": endpoint.get("provider_account", ""),
        "verification": endpoint.get("verification", ""), "source": dict(source) if isinstance(source, dict) else None,
        "fingerprint": endpoint.get("fingerprint", ""),
    }


def explicit_snapshot(kind: str, value: str, canonical: str, *, chain_network: str = "", channel: str = "", provider_account: str = "",
                      saved_matches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION, "resolution": "explicit", "kind": kind, "value": value, "canonical": canonical,
        "chain_network": chain_network, "network_display": network_display(chain_network), "channel": channel, "provider_account": provider_account,
        "saved_matches": list(saved_matches or []), "fingerprint": "",
    }


def describe_snapshot(snapshot: dict[str, Any] | None) -> str:
    """One line for review cards and replies: who, which label, the exact destination and its network."""
    if not isinstance(snapshot, dict):
        return ""
    where = f" on {snapshot['network_display']}" if snapshot.get("network_display") else ""
    if snapshot.get("channel"):
        where = f" on {channel_label(snapshot['channel'])}" + (f" ({snapshot['provider_account']})" if snapshot.get("provider_account") else "")
    if snapshot.get("resolution") == "explicit":
        saved = [m.get("contact_display_name") for m in snapshot.get("saved_matches") or [] if m.get("contact_display_name")]
        tail = f" (also saved for {', '.join(saved)})" if saved else " (not a saved contact)"
        return f"{snapshot.get('value', '')}{where}{tail}"
    label = f" · {snapshot['label']}" if snapshot.get("label") else ""
    flag = " · imported, not verified" if snapshot.get("verification") == "imported_unverified" else ""
    return f"{snapshot.get('display_name', '')}{label} · {snapshot.get('value', '')}{where}{flag}"


@dataclass(frozen=True)
class SnapshotCheck:
    status: str
    ok: bool
    message: str
    current: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "message": self.message, "current": self.current}


def verify_snapshot(snapshot: Any) -> SnapshotCheck:
    """Whether the destination a snapshot bound is still exactly what Contacts holds."""
    if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
        return SnapshotCheck("unknown_snapshot", False, "The recipient record is not a Contacts snapshot this build understands.")
    if snapshot.get("resolution") == "explicit":
        return SnapshotCheck("explicit", True, "The destination was given explicitly; no saved contact is bound.")
    endpoint = store.get_endpoint(str(snapshot.get("endpoint_id") or ""))
    who = snapshot.get("display_name") or "the contact"
    if endpoint is None:
        return SnapshotCheck("endpoint_removed", False, f"The address saved for {who} no longer exists in Contacts.")
    if endpoint.get("contact_state") != "active":
        return SnapshotCheck("contact_deleted", False, f"{who} was deleted from Contacts after this was prepared.")
    if endpoint.get("state") != "active":
        return SnapshotCheck("endpoint_removed", False, f"The {KIND_LABELS.get(endpoint['kind'], 'address').lower()} saved for {who} was removed after this was prepared.")
    if endpoint.get("fingerprint") != snapshot.get("fingerprint"):
        return SnapshotCheck(
            "endpoint_changed", False,
            f"{who}'s saved {KIND_LABELS.get(endpoint['kind'], 'address').lower()} changed after this was prepared "
            f"(was {snapshot.get('value')}, now {endpoint.get('value')}). Review it again.",
            current=endpoint,
        )
    return SnapshotCheck("current", True, "The saved destination is unchanged.", current=endpoint)


# --- resolution --------------------------------------------------------------------------------------------------------

def _endpoint_choice(contact: dict[str, Any], endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "choose_with": endpoint["endpoint_id"], "contact_id": contact["contact_id"], "display_name": contact["display_name"], "endpoint_id": endpoint["endpoint_id"],
        "kind": endpoint["kind"], "label": endpoint.get("label", ""), "value": endpoint["value"], "network_display": endpoint.get("network_display", ""),
        "chain_network": endpoint.get("chain_network", ""), "channel": endpoint.get("channel", ""), "provider_account": endpoint.get("provider_account", ""),
        "verification": endpoint.get("verification", ""),
    }


def _endpoint_phrase(endpoint: dict[str, Any]) -> str:
    label = f"{endpoint['label']} " if endpoint.get("label") else ""
    where = f" on {endpoint['network_display']}" if endpoint.get("network_display") else ""
    if endpoint.get("kind") == KIND_MESSAGING:
        where = f" on {channel_label(endpoint.get('channel'))}" + (f" ({endpoint['provider_account']})" if endpoint.get("provider_account") else "")
    return f"{label}{endpoint['value']}{where}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _passes(endpoint: dict[str, Any], *, kind: str, label: str, network: str, chain: str, family: str, environment: str, channel: str, provider_account: str) -> bool:
    if endpoint.get("kind") != kind or endpoint.get("state") != "active":
        return False
    if label and names.name_key(endpoint.get("label")) != names.name_key(label):
        return False
    if kind == KIND_WALLET:
        if network and endpoint.get("chain_network") != network:
            return False
        if family and endpoint.get("chain_family") != family:
            return False
        if environment and endpoint.get("chain_environment") != environment:
            return False
        if chain:
            try:
                from core.wallet import chains as wallet_chains

                if wallet_chains.resolve_network(str(endpoint.get("chain_network") or "")).chain_key != chain:
                    return False
            except Exception:
                return False
    if kind == KIND_MESSAGING:
        if channel and endpoint.get("channel") != channel:
            return False
        if provider_account and str(endpoint.get("provider_account") or "").casefold() != provider_account.casefold():
            return False
    return True


def _filters_text(filters: dict[str, str]) -> str:
    parts = []
    if filters.get("label"):
        parts.append(f"labelled '{filters['label']}'")
    if filters.get("network"):
        parts.append(f"on {network_display(filters['network'])}")
    elif filters.get("chain"):
        parts.append(f"on {filters['chain'].capitalize()}")
    if filters.get("environment") and not filters.get("network"):
        parts.append("on a mainnet network" if filters["environment"] == "mainnet" else "on a test network")
    if filters.get("channel"):
        parts.append(f"on {channel_label(filters['channel'])}")
    if filters.get("provider_account"):
        parts.append(f"in {filters['provider_account']}")
    return " ".join(parts)


def _canonical_network(network: str) -> str:
    if not network:
        return ""
    try:
        from core.wallet import chains as wallet_chains

        return wallet_chains.resolve_network(network).network
    except Exception:
        return ""


def _lookalike_key(kind: str, canonical: str) -> tuple[str, str] | None:
    """The first and last four characters people compare by eye (after 0x for EVM; the local part for email)."""
    if kind == KIND_WALLET:
        body = canonical[2:] if canonical.startswith("0x") else canonical
        return (body[:4].lower(), body[-4:].lower()) if len(body) >= 12 else None
    if kind == KIND_EMAIL and "@" in canonical:
        local, domain = canonical.split("@", 1)
        return (local, domain[:4]) if local else None
    return None


def _lookalikes(kind: str, canonical: str) -> list[dict[str, Any]]:
    """Saved destinations that look like this one at a glance but are different (address poisoning, typo domains)."""
    key = _lookalike_key(kind, canonical)
    if key is None:
        return []
    try:
        contacts = store.active_contacts()
    except Exception:
        return []
    found = []
    for contact in contacts:
        for endpoint in contact["endpoints"]:
            if endpoint["kind"] != kind or endpoint["canonical"] == canonical:
                continue
            if _lookalike_key(kind, endpoint["canonical"]) == key:
                found.append({"contact_id": contact["contact_id"], "contact_display_name": contact["display_name"], "endpoint_id": endpoint["endpoint_id"],
                              "value": endpoint["value"], "network_display": endpoint.get("network_display", "")})
    return found[:5]


def _with_lookalikes(snapshot: dict[str, Any]) -> dict[str, Any]:
    similar = _lookalikes(snapshot["kind"], snapshot["canonical"])
    if similar:
        snapshot["lookalikes"] = similar
        names_seen = ", ".join(sorted({s["contact_display_name"] for s in similar}))
        snapshot["warning"] = (f"This is not a saved address, but it looks like one saved for {names_seen} "
                               f"({similar[0]['value']}). Compare every character before approving.")
    return snapshot


def _explicit(query: str, kind: str, filters: dict[str, str]) -> Resolution | None:
    text = query.strip()
    if kind == KIND_EMAIL and (looks_like_email(text)):
        value, canonical = normalize_email(text)
        snapshot = _with_lookalikes(explicit_snapshot(kind, value, canonical, saved_matches=_saved_matches(kind, canonical)))
        return Resolution(STATUS_EXPLICIT, kind, query, snapshot,
                          message=f"{value} was given directly." + (f" {snapshot['warning']}" if snapshot.get("warning") else ""), filters=filters)
    if kind == KIND_PHONE and looks_like_phone(text):
        value, canonical = normalize_phone(text)
        return Resolution(STATUS_EXPLICIT, kind, query, explicit_snapshot(kind, value, canonical, saved_matches=_saved_matches(kind, canonical)),
                          message=f"{value} was given directly.", filters=filters)
    if kind == KIND_WALLET and (_EVM_SHAPE_RE.match(text) or _SVM_SHAPE_RE.match(text)):
        network = filters.get("network", "")
        if not network:
            # the wallet owner picks the account and row; Contacts only reports saved entries with this exact value
            canonical = text.lower() if text.startswith("0x") else text
            snapshot = _with_lookalikes(explicit_snapshot(kind, text, canonical, saved_matches=_saved_matches(kind, canonical)))
            return Resolution(STATUS_EXPLICIT, kind, query, snapshot,
                              message=f"{text} was given directly." + (f" {snapshot['warning']}" if snapshot.get("warning") else ""), filters=filters)
        try:
            wallet = normalize_wallet(text, network=network)
        except EndpointError as exc:
            return Resolution(STATUS_INVALID, kind, query, message=exc.message, filters=filters)
        snapshot = _with_lookalikes(explicit_snapshot(kind, wallet.value, wallet.canonical, chain_network=wallet.chain_network,
                                                      saved_matches=_saved_matches(kind, wallet.canonical, chain_network=wallet.chain_network)))
        return Resolution(STATUS_EXPLICIT, kind, query, snapshot,
                          message=f"{wallet.value} on {wallet.network_display} was given directly." + (f" {snapshot['warning']}" if snapshot.get("warning") else ""),
                          filters=filters)
    if kind == KIND_MESSAGING and filters.get("channel") and (text.startswith("@") or ":" in text or looks_like_phone(text) or looks_like_email(text)):
        try:
            value, canonical, channel, account = normalize_messaging(text, channel=filters["channel"], provider_account=filters.get("provider_account", ""))
        except EndpointError as exc:
            return Resolution(STATUS_INVALID, kind, query, message=exc.message, filters=filters)
        return Resolution(STATUS_EXPLICIT, kind, query, explicit_snapshot(kind, value, canonical, channel=channel, provider_account=account,
                                                                         saved_matches=_saved_matches(kind, canonical)),
                          message=f"{value} on {channel_label(channel)} was given directly.", filters=filters)
    return None


def _saved_matches(kind: str, canonical: str, *, chain_network: str = "") -> list[dict[str, Any]]:
    try:
        rows = store.endpoints_with_identity(kind, canonical, chain_network=chain_network)
    except Exception:
        return []
    return [{"contact_id": r["contact_id"], "contact_display_name": r.get("contact_display_name", ""), "endpoint_id": r["endpoint_id"],
             "label": r.get("label", ""), "chain_network": r.get("chain_network", "")} for r in rows]


def resolve(query: Any, *, kind: str, label: str = "", network: str = "", chain: str = "", family: str = "", environment: str = "",
            channel: str = "", provider_account: str = "") -> Resolution:
    """Resolve ``query`` (an address, a name, an alias, ``Name (label)``, a ``ct-``/``ep-`` id) for one endpoint kind."""
    text = names.clean_display(query)
    wanted_kind = str(kind or "").strip().lower()
    if wanted_kind not in KINDS:
        return Resolution(STATUS_INVALID, wanted_kind, text, message="Say which kind of address is needed: email, phone, messaging or wallet.")
    filters = {k: v for k, v in {
        "label": names.clean_display(label), "network": _canonical_network(network) if network else "", "chain": str(chain or "").strip().lower(),
        "family": str(family or "").strip().lower(), "environment": str(environment or "").strip().lower(),
        "channel": channel_key(channel) if channel else "", "provider_account": names.clean_display(provider_account),
    }.items() if v}
    if network and not filters.get("network"):
        return Resolution(STATUS_INVALID, wanted_kind, text, message=f"'{names.clean_display(network)[:60]}' is not a network this wallet declares.", filters=filters)
    if not text:
        return Resolution(STATUS_INVALID, wanted_kind, text, message="Say who it is for.", filters=filters)
    explicit = _explicit(text, wanted_kind, filters)
    if explicit is not None:
        return explicit
    labelled = _LABELLED_RE.match(text)
    name_query = text
    if labelled and not filters.get("label"):
        name_query = labelled.group("name")
        filters["label"] = names.clean_display(labelled.group("label"))

    if text.startswith("ep-"):
        endpoint = store.get_endpoint(text)
        if endpoint is None or endpoint.get("state") != "active" or endpoint.get("contact_state") != "active":
            return Resolution(STATUS_NOT_FOUND, wanted_kind, text, message="That saved address no longer exists.", filters=filters)
        contact = store.get_contact(endpoint["contact_id"]) or {}
        current = next((e for e in contact.get("endpoints", []) if e["endpoint_id"] == text), None)
        if current is None or not _passes(current, kind=wanted_kind, **_filter_args(filters)):
            what = KIND_LABELS.get(wanted_kind, wanted_kind).lower()
            return Resolution(STATUS_NO_ENDPOINT, wanted_kind, text, message=f"That saved entry is not a {what} {_filters_text(filters)}".rstrip() + ".", filters=filters)
        return Resolution(STATUS_RESOLVED, wanted_kind, text, contact_snapshot(contact, current, matched_by=names.MATCH_ID),
                          message=f"Using {_endpoint_phrase(current)} for {contact['display_name']}.", filters=filters)

    contacts = store.active_contacts()
    strong: list[tuple[str, dict[str, Any]]] = []
    lookalikes: list[dict[str, Any]] = []
    if name_query.startswith("ct-"):
        strong = [(names.MATCH_ID, c) for c in contacts if c["contact_id"] == name_query]
    else:
        for contact in contacts:
            tier = names.match_tier(name_query, display_name=contact["display_name"], aliases=contact["aliases"])
            if tier:
                strong.append((tier, contact))
            elif identity.lookalike_of(name_query, display_name=contact["display_name"], aliases=contact["aliases"]):
                lookalikes.append(contact)
    if lookalikes:
        return _lookalike_choices(text, name_query, wanted_kind, strong, lookalikes, filters)
    if not strong:
        scored = []
        for contact in contacts:
            score = names.fuzzy_score(name_query, display_name=contact["display_name"], aliases=contact["aliases"])
            if score >= names.FUZZY_THRESHOLD:
                scored.append((score, contact))
        scored.sort(key=lambda item: (-item[0], item[1]["name_key"]))
        suggestions = tuple({"contact_id": c["contact_id"], "display_name": c["display_name"], "score": s, "choose_with": c["contact_id"]} for s, c in scored[:MAX_SUGGESTIONS])
        if suggestions:
            message = f"No saved contact is called '{name_query}'. Did you mean {_join([s['display_name'] for s in suggestions])}?"
        else:
            message = f"No saved contact is called '{name_query}'."
        return Resolution(STATUS_NOT_FOUND, wanted_kind, text, suggestions=suggestions, message=message, filters=filters)

    if len(strong) > 1:
        choices = []
        for tier, contact in sorted(strong, key=lambda item: (names.MATCH_RANK[item[0]], item[1]["name_key"])):
            eligible = [e for e in contact["endpoints"] if _passes(e, kind=wanted_kind, **_filter_args(filters))]
            choices.append({"choose_with": contact["contact_id"], "contact_id": contact["contact_id"], "display_name": contact["display_name"], "matched_by": tier,
                            "endpoints": [_endpoint_choice(contact, e) for e in eligible]})
        described = [f"{c['display_name']}" + (f" ({_join([e['value'] for e in c['endpoints']])})" if c["endpoints"] else "") for c in choices]
        return Resolution(STATUS_AMBIGUOUS_CONTACT, wanted_kind, text, choices=tuple(choices),
                          message=f"{len(choices)} saved contacts match '{name_query}': {_join(described)}. Which one?", filters=filters)

    tier, contact = strong[0]
    of_kind = [e for e in contact["endpoints"] if e.get("kind") == wanted_kind]
    eligible = [e for e in of_kind if _passes(e, kind=wanted_kind, **_filter_args(filters))]
    what = KIND_LABELS.get(wanted_kind, wanted_kind).lower()
    if not eligible:
        constraint = _filters_text(filters)
        if not of_kind:
            message = f"{contact['display_name']} has no {what} saved."
        else:
            saved = _join([_endpoint_phrase(e) for e in of_kind])
            message = f"{contact['display_name']} has no {what} {constraint}. Saved: {saved}.".replace("  ", " ")
        return Resolution(STATUS_NO_ENDPOINT, wanted_kind, text, choices=tuple(_endpoint_choice(contact, e) for e in of_kind), message=message, filters=filters)
    if len(eligible) > 1:
        return Resolution(STATUS_AMBIGUOUS_ENDPOINT, wanted_kind, text, choices=tuple(_endpoint_choice(contact, e) for e in eligible),
                          message=f"{contact['display_name']} has {len(eligible)} saved {what} entries: {_join([_endpoint_phrase(e) for e in eligible])}. Which one?",
                          filters=filters)
    endpoint = eligible[0]
    return Resolution(STATUS_RESOLVED, wanted_kind, text, contact_snapshot(contact, endpoint, matched_by=tier),
                      message=f"Using {_endpoint_phrase(endpoint)} for {contact['display_name']}.", filters=filters)


_SCRIPT_NAMES = {"Latn": "Latin", "Cyrl": "Cyrillic", "Grek": "Greek", "Arab": "Arabic", "Hebr": "Hebrew", "Hani": "Han", "Hira": "Hiragana",
                 "Kana": "Katakana", "Hang": "Hangul", "Deva": "Devanagari", "Armn": "Armenian", "Cher": "Cherokee", "Thai": "Thai"}


def _tell_apart(display_name: str) -> str:
    """What separates a name from the names it looks like: its scripts and whether it holds digits."""
    described = identity.describe(display_name)
    parts = []
    if described["scripts"]:
        parts.append(("mixed " if described["mixed_script"] else "") + " and ".join(_SCRIPT_NAMES.get(s, s) for s in described["scripts"]) + " letters")
    if any(ch.isdigit() for ch in display_name):
        parts.append("digits")
    return ", ".join(parts) or "no letters"


def _lookalike_choices(text: str, name_query: str, wanted_kind: str, strong: list[tuple[str, dict[str, Any]]], lookalikes: list[dict[str, Any]],
                       filters: dict[str, str]) -> Resolution:
    ranked = list(strong) + [(names.MATCH_CONFUSABLE, contact) for contact in lookalikes]
    ranked.sort(key=lambda item: (names.MATCH_RANK[item[0]], item[1]["name_key"], item[1]["contact_id"]))
    choices = []
    for tier, contact in ranked:
        eligible = [e for e in contact["endpoints"] if _passes(e, kind=wanted_kind, **_filter_args(filters))]
        choices.append({"choose_with": contact["contact_id"], "contact_id": contact["contact_id"], "display_name": contact["display_name"], "matched_by": tier,
                        "identity": identity.describe(contact["display_name"]), "tell_apart": _tell_apart(contact["display_name"]),
                        "endpoints": [_endpoint_choice(contact, e) for e in eligible]})
    described = [f"{c['display_name']} ({c['tell_apart']}" + (f"; {_join([e['value'] for e in c['endpoints']])}" if c["endpoints"] else "") + ")" for c in choices]
    if strong:
        message = f"'{name_query}' matches {len(choices)} saved contacts whose names look alike but are spelled with different characters: {_join(described)}. Which one?"
    else:
        message = f"No saved contact is called '{name_query}'. {_join(described)} looks like it but is spelled with different characters. Is that who you mean?"
    return Resolution(STATUS_AMBIGUOUS_CONTACT, wanted_kind, text, choices=tuple(choices), message=message, filters=filters)


def _filter_args(filters: dict[str, str]) -> dict[str, str]:
    return {key: filters.get(key, "") for key in ("label", "network", "chain", "family", "environment", "channel", "provider_account")}


__all__ = [
    "STATUS_AMBIGUOUS_CONTACT",
    "STATUS_AMBIGUOUS_ENDPOINT",
    "STATUS_EXPLICIT",
    "STATUS_INVALID",
    "STATUS_NOT_FOUND",
    "STATUS_NO_ENDPOINT",
    "STATUS_RESOLVED",
    "Resolution",
    "SnapshotCheck",
    "contact_snapshot",
    "describe_snapshot",
    "explicit_snapshot",
    "resolve",
    "verify_snapshot",
]
