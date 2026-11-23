"""Sending with Drop from a chat turn: the one place a model's tool call reaches Drop.

The call names a file in the owner's safe folders, the person, and an optional note. What
comes back is what happened, never what was hoped: Drop off, nobody added, no such person,
or sent (with the file's name and size and who to). The model never sees the file's bytes
and nothing here reads a received Drop: received content is data and stays in Settings.
"""
from __future__ import annotations

from pathlib import Path

from core.drop import DropError

# What a refusal says. Short, plain, and each names what to do next.
REFUSALS = {
    "CONTACT_NOT_READY": "{name} has not added your card yet, or the shared folder has not synced. Nothing was sent. "
                         "Send them your card from Settings, then ask again.",
    "CONTACT_BUSY": "{name}'s VOOL has not been open since your last files, so it cannot take another yet. "
                    "Nothing was sent. Ask again after their VOOL has been open once.",
    "INPUT_LIMIT": "That file is larger than 10 MB, the most one Drop carries. Nothing was sent.",
    "INVALID_FILENAME": "That file's name cannot be sent with Drop. Nothing was sent. Rename it and ask again.",
    "NOTE_LIMIT": "The note is too long for a Drop. Nothing was sent.",
    "MISSING_RELAY": "Drop's shared folder is not there any more. Nothing was sent. Choose it again in Settings.",
    "RELAY_NOT_FOLDER": "Drop's shared folder is not a usable folder. Nothing was sent. Choose it again in Settings.",
    "RELAY_REQUIRED": "Drop has no shared folder yet. Nothing was sent. Choose one in Settings.",
    "VAULT_KEY_UNAVAILABLE": "VOOL could not read the key that protects Drop on this computer. Nothing was sent. "
                             "Unlock your keychain and ask again.",
}


def pick(contacts: list[dict], wanted: str) -> tuple[dict | None, list[dict]]:
    """The one person the words name, or (None, the people it could be).

    Only a whole-name or whole-word match of exactly one person chooses. Anything weaker,
    and any name two people could answer to, is asked about: a file must never go to a
    look-alike of the person meant."""
    from core.contacts.names import name_key, tokens

    key = name_key(wanted)
    if not key:
        return (contacts[0], []) if len(contacts) == 1 else (None, contacts)
    exact = [p for p in contacts if name_key(p["name"]) == key or p["tag"] == key]
    if len(exact) == 1:
        return exact[0], []
    words = set(tokens(wanted))
    partial = [p for p in contacts if words and (words & set(tokens(p["name"])))]
    if not exact and len(partial) == 1:
        return partial[0], []
    return None, exact or partial or contacts


def send(path: Path, to: str, note: str) -> dict:
    """Send one file. Returns {"ok": True, name, size_bytes, sha256, person, verified, sid}
    or {"ok": False, "status", "text"}."""
    from core.web.api.drop_api import _home

    person: dict | None = None
    try:
        with _home() as home:
            contacts = home.contacts()
            if not contacts:
                return {"ok": False, "status": "unpaired", "text": ""}
            person, choices = pick(contacts, to)
            if person is None:
                names = ", ".join(f"{p['name']} ({p['tag']})" for p in choices[:8])
                return {"ok": False, "status": "which_person",
                        "text": f"Nothing was sent: say which person to send it to. In your Drop list: {names}."}
            try:
                data = path.read_bytes()
            except OSError:
                return {"ok": False, "status": "not_found", "text": "That file could not be read. Nothing was sent."}
            sid, manifest = home.send_to(person["fingerprint"], str(note or ""), path.name, data)
            return {"ok": True, "sid": sid, "name": manifest["name"], "size_bytes": manifest["size"],
                    "sha256": manifest["sha256"], "person": person["name"], "tag": person["tag"],
                    "verified": bool(person["verified"])}
    except DropError as exc:
        if exc.code == "DROP_DISABLED":
            return {"ok": False, "status": "off", "text": ""}
        text = REFUSALS.get(exc.code, "Drop could not send it. Nothing was sent.")
        name = (person or {}).get("name") or "That person"
        return {"ok": False, "status": exc.code.lower(), "text": text.replace("{name}", name)}
