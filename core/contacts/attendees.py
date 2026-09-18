"""Calendar attendees a request names, resolved through Contacts (the calendar owner keeps invitations and receipts).

The calendar owner's law stays: only a request that asks for invitations has attendees, and an explicit address is an
attendee as written. This adds the saved people named in the invitation part of the request -- the words right after
"invite", "with", "attendees", "guests" or "participants", up to where the request moves on to the time, place or topic
("to", "for", "on", "at", "about", "tomorrow", ...), split on "and" and commas. Each named part resolves through the one
Contacts resolver to exactly one saved email address. A saved name that fits several contacts or entries, has no email,
or only resembles a saved name makes the proposal ask; a phrase that names no saved contact at all ("the team") is left
out, exactly as before. Nothing is guessed, and nothing here sends an invitation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.contacts import resolver
from core.contacts.endpoints import KIND_EMAIL, looks_like_email

_SLOT_RE = re.compile(
    r"\b(?:invite|inviting|invites|with|attendees?|guests?|participants?)\s*:?\s+(?P<slot>.+?)"
    r"(?=\s+(?:to|for|on|at|about|re|regarding|tomorrow|today|tonight|next|this|from|between|in|by|and\s+(?:save|add|note|remind)|so|because)\b|[.?!;]|$)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s*(?:,|&|\band\b|\bplus\b)\s*", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"^(?:the|my|our|a|an)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class AttendeeResolution:
    addresses: tuple[str, ...]
    snapshots: tuple[dict[str, Any], ...] = ()
    question: str = ""
    unresolved: tuple[dict[str, Any], ...] = ()
    labels: dict[str, str] = field(default_factory=dict)


def named_parts(text: str) -> list[str]:
    """The name-like parts of every invitation slot in the request, in order, without explicit addresses."""
    parts: list[str] = []
    for match in _SLOT_RE.finditer(str(text or "")):
        for piece in _SPLIT_RE.split(match.group("slot")):
            piece = piece.strip(" \t\"'()[]")
            if not piece or looks_like_email(piece) or "@" in piece:
                continue
            if piece not in parts:
                parts.append(piece)
    return parts


def attendees_from_request(text: str, *, explicit: list[str]) -> AttendeeResolution:
    addresses = [str(a).strip().lower() for a in explicit if str(a).strip()]
    snapshots: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    questions: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for part in named_parts(text):
        if looks_like_email(part) or any(part.lower() in str(address).lower() for address in explicit):
            # an address-shaped part is already an explicit attendee (parse_attendees owns it);
            # so is a FRAGMENT of one -- the slot splitter can cut an address at its @, leaving
            # the local part ('alex'), which as a name would match an unrelated saved contact
            # and silently invite someone the request never named (release consumer pin, Goal 2)
            continue
        resolution = resolver.resolve(part, kind=KIND_EMAIL)
        if resolution.status == resolver.STATUS_NOT_FOUND and not resolution.suggestions:
            stripped = _ARTICLE_RE.sub("", part)
            if stripped != part:
                resolution = resolver.resolve(stripped, kind=KIND_EMAIL)
            if resolution.status == resolver.STATUS_NOT_FOUND and not resolution.suggestions:
                continue  # not a saved contact's name: the calendar owner's rule for unnamed guests applies unchanged
        if resolution.ok and resolution.snapshot:
            address = str(resolution.snapshot["value"]).lower()
            if address not in addresses:
                addresses.append(address)
                snapshots.append(dict(resolution.snapshot))
                labels[address] = resolver.describe_snapshot(resolution.snapshot)
            continue
        questions.append(resolution.message)
        unresolved.append({"name": part, "status": resolution.status, "choices": list(resolution.choices), "suggestions": list(resolution.suggestions)})
    return AttendeeResolution(addresses=tuple(addresses), snapshots=tuple(snapshots), question=" ".join(questions), unresolved=tuple(unresolved), labels=labels)


__all__ = ["AttendeeResolution", "attendees_from_request", "named_parts"]
