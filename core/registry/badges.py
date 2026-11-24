"""The two team badges: "Tested by VOOL" and "Editor's Choice".

A badge is a line in one signed document, never a flag on a row or a word an author wrote. Three
signed pieces make it hold:

- the issuer list: the public keys of the people the owner appointed, signed with the offline root key;
- the badge document: which exact package (kind, id, version, checksum) carries which badge and until
  when, signed by two different people from that list with their own keys;
- the stamp: "this document, still current", signed by the signer's admission key and good for a week.

The admission key can only stamp. It cannot issue a badge, and neither can one person, a key that is
not on the list, or anyone who can write to the signer's repository. A badge matches one checksum, so
a new version shows none until two people sign for it. Anything that does not verify shows nothing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.registry.codec import HEX, IDENTITY, VERSION, RegistryError, canonical, decode, digest, require
from core.registry.trust import root_document, verify

SCHEMA = "vool.registry.badges.v1"
ISSUERS_SCHEMA = "vool.registry.badge_issuers.v1"
STAMP_SCHEMA = "vool.registry.badges_stamp.v1"
# The order the chips are drawn in.
BADGES = ("tested", "editors_choice")
# A badge runs out by itself; renewing one takes two signatures again.
ENTRY_SECONDS = {"tested": 180 * 86400, "editors_choice": 365 * 86400}
DOCUMENT_SECONDS = 90 * 86400
# How long a withdrawal can be held back by a server that keeps serving the last document it liked.
STAMP_SECONDS = 7 * 86400
SKEW = 300
MAX_BYTES = 1024 * 1024
# What the checksum is of: the registry package, a plugin's installed files, or a bundled skill's SKILL.md.
DIGEST_TYPES = {"skill": ("artifact", "skill-file"), "plugin": ("artifact", "package")}
PLATFORMS = ("macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64")
SHIPPED = ("badges.json", "badge-issuers.json")
_NOTHING = {"issuers": None, "badges": None, "stamp": None}


def _name(value, digest_type: str) -> bool:
    if not isinstance(value, str):
        return False
    if digest_type == "artifact":
        return len(value.split("/")) == 2 and all(IDENTITY.fullmatch(part) for part in value.split("/"))
    return 0 < len(value) <= 128 and value.isascii() and value[0].isalnum() and all(
        char.isalnum() or char in "._/-" for char in value)


def _times(value: dict, limit: int) -> bool:
    return (type(value.get("issued_at")) is int and type(value.get("expires")) is int and
            0 < value["issued_at"] < value["expires"] <= value["issued_at"] + limit)


# ---- the issuer list ----

def issuer_list(value, *, now: int, allow_expired: bool = False) -> dict:
    require(isinstance(value, dict) and set(value) == {"schema", "version", "expires", "keys", "threshold"} and
            value["schema"] == ISSUERS_SCHEMA, "unknown badge issuer list")
    require(type(value["version"]) is int and value["version"] > 0 and type(value["expires"]) is int and
            (allow_expired or now < value["expires"]), "the badge issuer list has run out")
    keys = value["keys"]
    require(isinstance(keys, dict) and 2 <= len(keys) <= 32 and
            all(isinstance(key, str) and isinstance(public, str) and HEX.fullmatch(key) and HEX.fullmatch(public) and
                key == digest({"ed25519": public}) for key, public in keys.items()), "invalid badge issuer key")
    # Never one person: a badge always takes two of the appointed people.
    require(type(value["threshold"]) is int and 2 <= value["threshold"] <= len(keys), "a badge needs two signers")
    return value


def issuers(envelope, root: dict, *, now: int, allow_expired: bool = False) -> dict:
    """The appointed people's keys, as the offline root key signed them."""
    require(isinstance(envelope, dict) and len(canonical(envelope)) <= 65536, "invalid badge issuer list")
    value = issuer_list(verify(envelope, root, "root"), now=now, allow_expired=allow_expired)
    # A key that already signs uploads, reviews or the list is never also a badge key.
    require(not set(value["keys"]) & set(root["keys"]), "a badge key must not be one of the registry's own keys")
    return value


def new_issuers(public_keys: list[str], *, version: int, now: int, lifetime: int) -> dict:
    """The unsigned list. The root holder signs it with core.registry.publisher.add_signature."""
    require(len(set(public_keys)) == len(public_keys), "the same key is named twice")
    require(all(isinstance(public, str) and HEX.fullmatch(public) for public in public_keys), "invalid badge issuer key")
    value = {"schema": ISSUERS_SCHEMA, "version": version, "expires": now + lifetime,
             "keys": {digest({"ed25519": public}): public for public in public_keys}, "threshold": 2}
    require(type(lifetime) is int and lifetime > 0, "invalid badge issuer lifetime")
    return {"signed": issuer_list(value, now=now), "signatures": []}


# ---- the badge document ----

def _entry(item, issued_at: int) -> tuple:
    require(isinstance(item, dict) and set(item) == {
        "badge", "kind", "id", "version", "digest_type", "sha256", "issued_at", "expires", "targets",
        "evidence_sha256", "issued_by", "note"}, "invalid badge entry")
    require(item["badge"] in BADGES and item["kind"] in DIGEST_TYPES and
            item["digest_type"] in DIGEST_TYPES[item["kind"]], "unknown badge or package kind")
    require(_name(item["id"], item["digest_type"]) and isinstance(item["version"], str) and
            bool(VERSION.fullmatch(item["version"])), "invalid badge package name or version")
    require(all(isinstance(item[key], str) and HEX.fullmatch(item[key]) for key in ("sha256", "evidence_sha256")),
            "invalid badge checksum")
    require(_times(item, ENTRY_SECONDS[item["badge"]]) and item["issued_at"] <= issued_at,
            "a badge lasts at most 180 days (Tested by VOOL) or 365 days (Editor's Choice)")
    targets = item["targets"]
    require(isinstance(targets, list) and len(targets) <= 32 and all(
        isinstance(target, dict) and set(target) == {"version", "platform"} and isinstance(target["version"], str) and
        0 < len(target["version"]) <= 64 and target["platform"] in PLATFORMS for target in targets) and
        len({(target["version"], target["platform"]) for target in targets}) == len(targets), "invalid badge targets")
    # "Tested" always says on which VOOL builds; a pick is not about a build.
    require(bool(targets) if item["badge"] == "tested" else not targets, "invalid badge targets")
    signers = item["issued_by"]
    require(isinstance(signers, list) and len(signers) == 2 and signers[0] != signers[1] and
            all(isinstance(key, str) and HEX.fullmatch(key) for key in signers), "a badge names its two signers")
    note = item["note"]
    require(isinstance(note, str) and len(note) <= 200 and note.isprintable() and not set(note) & set("<>"),
            "a badge note is at most 200 plain characters")
    return item["badge"], item["digest_type"], item["sha256"]


def document(value, *, now: int, allow_expired: bool = False) -> dict:
    require(isinstance(value, dict) and set(value) == {
        "schema", "sequence", "previous_sha256", "issued_at", "expires", "badges", "team_releases", "revoked"} and
        value["schema"] == SCHEMA, "unknown badge document")
    require(type(value["sequence"]) is int and value["sequence"] > 0 and isinstance(value["previous_sha256"], str) and
            (value["previous_sha256"] == "" or bool(HEX.fullmatch(value["previous_sha256"]))), "invalid badge sequence")
    require(_times(value, DOCUMENT_SECONDS) and value["issued_at"] <= now + SKEW, "invalid badge document dates")
    require(allow_expired or now < value["expires"], "the badge document has run out")
    for field in ("badges", "team_releases", "revoked"):
        require(isinstance(value[field], list) and len(value[field]) <= 4096, "unbounded badge document")
    seen = [_entry(item, value["issued_at"]) for item in value["badges"]]
    require(len(set(seen)) == len(seen), "the same badge is listed twice")
    require(all(isinstance(item, dict) and set(item) == {"id", "sha256"} and _name(item["id"], "artifact") and
                isinstance(item["sha256"], str) and HEX.fullmatch(item["sha256"]) for item in value["team_releases"]),
            "invalid team release")
    revoked = value["revoked"]
    require(all(isinstance(item, str) and HEX.fullmatch(item) for item in revoked) and len(set(revoked)) == len(revoked),
            "invalid withdrawn list")
    require(not {item[2] for item in seen} & set(revoked), "a withdrawn package cannot carry a badge")
    return value


def verify_document(envelope, issuer: dict, *, now: int, allow_expired: bool = False) -> dict:
    """The badge document, when two different appointed people signed these exact bytes."""
    require(isinstance(envelope, dict) and len(canonical(envelope)) <= MAX_BYTES, "badge document exceeds byte limit")
    # The list plays the part a role plays in the trust root: unknown keys count for nothing, one key counts once.
    value = verify(envelope, {"roles": {"badges": {"keyids": list(issuer["keys"]), "threshold": issuer["threshold"]}},
                              "keys": issuer["keys"]}, "badges")
    return document(value, now=now, allow_expired=allow_expired)


def new_document(*, sequence: int, previous: dict | None, entries: list[dict], revoked: list[str],
                 team_releases: list[dict] | None = None, now: int, lifetime: int = DOCUMENT_SECONDS) -> dict:
    """The unsigned document that follows `previous` (the signed part of the one in place, or None)."""
    value = {"schema": SCHEMA, "sequence": sequence, "previous_sha256": digest(previous) if previous else "",
             "issued_at": now, "expires": now + lifetime, "badges": entries,
             "team_releases": team_releases or [], "revoked": sorted(set(revoked))}
    return {"signed": document(value, now=now), "signatures": []}


# ---- the stamp ----

def new_stamp(signed_document: dict, *, now: int, lifetime: int = STAMP_SECONDS) -> dict:
    """Unsigned "still current" note for one badge document. The signer adds its admission key's signature."""
    require(type(lifetime) is int and 0 < lifetime <= STAMP_SECONDS, "invalid badge stamp lifetime")
    return {"signed": {"schema": STAMP_SCHEMA, "sequence": signed_document["sequence"],
                       "badges_sha256": digest(signed_document), "issued_at": now, "expires": now + lifetime},
            "signatures": []}


def stamp(envelope, root: dict, signed_document: dict, *, now: int) -> dict:
    require(isinstance(envelope, dict) and len(canonical(envelope)) <= 65536, "invalid badge stamp")
    value = verify(envelope, root, "admission")
    require(isinstance(value, dict) and set(value) == {"schema", "sequence", "badges_sha256", "issued_at", "expires"} and
            value["schema"] == STAMP_SCHEMA, "unknown badge stamp")
    require(_times(value, STAMP_SECONDS) and value["issued_at"] <= now + SKEW and now < value["expires"],
            "the badge stamp is out of date")
    require(value["sequence"] == signed_document["sequence"] and value["badges_sha256"] == digest(signed_document),
            "the badge stamp names another document")
    return value


def verify_all(value, root: dict, *, now: int) -> tuple[dict, dict, dict]:
    """(issuer list, badge document, stamp) from what the service serves, or a refusal. All three must hold now."""
    require(isinstance(value, dict) and set(value) == set(_NOTHING) and
            all(isinstance(value[key], dict) for key in _NOTHING), "no badge document")
    issuer = issuers(value["issuers"], root, now=now)
    signed = verify_document(value["badges"], issuer, now=now)
    return issuer, signed, stamp(value["stamp"], root, signed, now=now)


# ---- what a page may draw ----

def shown(signed: dict, issuer: dict, *, now: int, target: dict | None, withdrawn=frozenset()) -> dict:
    """{(digest_type, sha256): {"kind", "id", "version", "badges": [...]}} for the entries that hold right now."""
    result = {}
    build = (target.get("version"), target.get("platform")) if isinstance(target, dict) else None
    for item in signed["badges"]:
        if item["sha256"] in withdrawn or item["sha256"] in signed["revoked"]:
            continue
        if not (item["issued_at"] <= now + SKEW and now < item["expires"]):
            continue
        if not set(item["issued_by"]) <= set(issuer["keys"]):
            continue  # signed for by someone who is no longer appointed: it waits for two current signers
        if item["badge"] == "tested" and build not in {(entry["version"], entry["platform"]) for entry in item["targets"]}:
            continue  # tested, but not on this VOOL build
        row = result.setdefault((item["digest_type"], item["sha256"]),
                                {"kind": item["kind"], "id": item["id"], "version": item["version"], "badges": []})
        if (row["kind"], row["id"], row["version"]) == (item["kind"], item["id"], item["version"]):
            row["badges"].append(item["badge"])
    for row in result.values():
        row["badges"].sort(key=BADGES.index)
    return result


def badges_for(index: dict, digest_type: str, sha256, *, kind: str, package_id: str | None = None,
               version: str | None = None) -> list[str]:
    """The badges of one exact package. A different checksum, kind, name or version has none."""
    row = index.get((digest_type, sha256)) if isinstance(sha256, str) else None
    if not row or row["kind"] != kind:
        return []
    if (package_id is not None and row["id"] != package_id) or (version is not None and row["version"] != version):
        return []
    return list(row["badges"])


# ---- what this device keeps ----

def _tables(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS badge_state (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL, "
               "digest TEXT NOT NULL, issuers INTEGER NOT NULL, payload BLOB NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS badge_withdrawn (sha256 TEXT PRIMARY KEY)")


def keep(catalog, value, *, now: int) -> dict:
    """Verify what the service served and keep it as this device's badge document. Returns the document.

    The sequence never goes back, the issuer list never goes back, and a checksum that was ever
    withdrawn stays withdrawn here, whatever a later answer says.
    """
    root = root_document(catalog.root, now)
    issuer, signed, _ = verify_all(value, root, now=now)
    encoded = canonical(value)
    with catalog.lock:
        try:
            catalog.db.execute("BEGIN IMMEDIATE")
            _tables(catalog.db)
            row = catalog.db.execute("SELECT sequence,digest,issuers FROM badge_state WHERE id=1").fetchone()
            require(not row or issuer["version"] >= row[2], "badge issuer list rollback")
            require(not row or signed["sequence"] >= row[0], "badge document rollback")
            require(not row or signed["sequence"] != row[0] or digest(signed) == row[1], "badge document equivocation")
            catalog.db.executemany("INSERT OR IGNORE INTO badge_withdrawn VALUES (?)", [(item,) for item in signed["revoked"]])
            catalog.db.execute("INSERT OR REPLACE INTO badge_state VALUES (1,?,?,?,?)",
                               (signed["sequence"], digest(signed), issuer["version"], encoded))
            catalog.db.commit()
        except Exception:
            catalog.db.rollback()
            raise
    return signed


def _kept(db) -> tuple[int, dict | None, frozenset]:
    """(sequence, payload, withdrawn) as saved; (0, None, nothing) before the first verified document."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='badge_state'").fetchone():
        return 0, None, frozenset()
    withdrawn = frozenset(row[0] for row in db.execute("SELECT sha256 FROM badge_withdrawn"))
    row = db.execute("SELECT sequence,payload FROM badge_state WHERE id=1").fetchone()
    return (row[0], decode(row[1], 2 * MAX_BYTES), withdrawn) if row else (0, None, withdrawn)


def held(catalog, *, now: int, target: dict | None) -> dict:
    """What the kept document grants right now. Verified again on every read, so time alone takes badges away."""
    with catalog.lock:
        _, value, withdrawn = _kept(catalog.db)
    if value is None:
        return {}
    issuer, signed, _ = verify_all(value, root_document(catalog.root, now), now=now)
    return shown(signed, issuer, now=now, target=target, withdrawn=withdrawn)


def shipped(folder: Path) -> dict | None:
    """The badge document that came with this release, or None when the release carries none."""
    try:
        return {"badges": decode((folder / SHIPPED[0]).read_bytes(), MAX_BYTES),
                "issuers": decode((folder / SHIPPED[1]).read_bytes(), 65536)}
    except FileNotFoundError:
        return None


def local(root: dict, state: Path, folder: Path, *, now: int, target: dict | None) -> dict:
    """Badges for what is already on this device (bundled skills, installed plugins). No network, no writes.

    The document that came with the release counts for them past its own date, until this device has
    verified a newer one. From then on the newer one alone decides, by the same rules as Browse.
    """
    sequence, value, withdrawn = 0, None, frozenset()
    if state.is_file():
        db = sqlite3.connect(state.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            sequence, value, withdrawn = _kept(db)
        finally:
            db.close()
    try:
        released = shipped(folder)
        if released is not None:
            issuer = issuers(released["issuers"], root, now=now, allow_expired=True)
            signed = verify_document(released["badges"], issuer, now=now, allow_expired=True)
            if signed["sequence"] > sequence:
                return shown(signed, issuer, now=now, target=target, withdrawn=withdrawn)
    except (RegistryError, OSError):
        pass  # a shipped copy that does not verify counts for nothing; the kept document still decides
    if value is None:
        return {}
    try:
        issuer, signed, _ = verify_all(value, root, now=now)
    except RegistryError:
        return {}  # the kept document ran out or its stamp is stale: nothing, until the next check verifies one
    return shown(signed, issuer, now=now, target=target, withdrawn=withdrawn)
