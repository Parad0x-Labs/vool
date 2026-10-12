"""Threshold-signed admissions and short-lived catalog snapshots.

This VOOL protocol is deliberately versioned independently of TUF. Trust roots
are shipped or installed by the operator, never learned from the catalog server.
The online catalog key cannot sign an admission or rotate the offline root.
"""
from __future__ import annotations

import sqlite3
import threading
from functools import wraps
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.registry.codec import HEX, RegistryError, canonical, digest, require, unbase64


def serialized(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with self.lock:
            return function(self, *args, **kwargs)
    return call


def verify(envelope: dict, root: dict, role: str) -> dict:
    require(isinstance(envelope, dict) and set(envelope) == {"signed", "signatures"} and
            isinstance(envelope["signed"], dict), "invalid signed envelope")
    signatures = envelope["signatures"]
    require(isinstance(signatures, list) and len(signatures) <= 32, "invalid signatures")
    rule = root["roles"][role]
    valid, seen = set(), set()
    for item in signatures:
        require(isinstance(item, dict) and set(item) == {"keyid", "sig"}, "invalid signature fields")
        keyid = item["keyid"]
        require(isinstance(keyid, str) and keyid not in seen, "duplicate signature")
        seen.add(keyid)
        if keyid not in rule["keyids"]:
            continue
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(root["keys"][keyid]))
            key.verify(unbase64(item["sig"]), canonical(envelope["signed"]))
            valid.add(keyid)
        except (InvalidSignature, ValueError, TypeError):
            raise RegistryError("invalid registry signature") from None
    require(len(valid) >= rule["threshold"], "signature threshold not met")
    return envelope["signed"]


def root_document(value: dict, now: int, *, allow_expired: bool = False) -> dict:
    require(isinstance(value, dict) and set(value) == {
        "schema", "version", "expires", "keys", "roles", "policies"} and
        value["schema"] == "vool.registry.root.v1", "unknown trust root")
    require(type(value["version"]) is int and value["version"] > 0 and
            type(value["expires"]) is int and (allow_expired or value["expires"] > now), "expired or invalid root")
    require(isinstance(value["keys"], dict) and 0 < len(value["keys"]) <= 32 and
            all(HEX.fullmatch(str(key)) and HEX.fullmatch(str(public)) and
                key == digest({"ed25519": public}) for key, public in value["keys"].items()), "invalid public key")
    require(isinstance(value["roles"], dict) and set(value["roles"]) == {"root", "admission", "review", "catalog"},
            "missing trust role")
    used = set()
    for role, rule in value["roles"].items():
        require(isinstance(rule, dict) and set(rule) == {"keyids", "threshold"} and
                isinstance(rule["keyids"], list) and len(set(rule["keyids"])) == len(rule["keyids"]) and
                set(rule["keyids"]) <= set(value["keys"]) and type(rule["threshold"]) is int and
                (1 if role == "catalog" else 2) <= rule["threshold"] <= len(rule["keyids"]), "unsafe signing threshold")
        require(not used.intersection(rule["keyids"]), "online, review and offline signing roles must be separated")
        used.update(rule["keyids"])
    require(isinstance(value["policies"], list) and value["policies"] and
            all(HEX.fullmatch(str(item)) for item in value["policies"]), "root has no approved policies")
    return value


class TrustedCatalog:
    """Persist monotonic versions and exact metadata hashes transactionally."""

    def __init__(self, trusted_root: dict, state: Path, *, now: int):
        root = root_document(trusted_root.get("signed"), now, allow_expired=True)
        verify(trusted_root, root, "root")
        state.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(state, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS trust (id INTEGER PRIMARY KEY CHECK(id=1), root BLOB, sequence INTEGER, snapshot BLOB)")
        from core.registry.codec import decode

        with self.db:
            row = self.db.execute("SELECT root FROM trust WHERE id=1").fetchone()
            if row:
                stored = decode(row[0])
                # The cache is private local authority, like the installed lifecycle
                # store. Remote roots reach it only through dual-signed rotation.
                self.root = root_document(stored["signed"], now, allow_expired=True)
                verify(stored, self.root, "root")
            else:
                self.db.execute("INSERT INTO trust VALUES (1, ?, 0, NULL)", (canonical(trusted_root),))
                self.root = root
        self.current = None
        if root["version"] > self.root["version"]:
            self.rotate(trusted_root, now=now)

    @serialized
    def close(self):
        self.db.close()

    @serialized
    def rotate(self, envelope: dict, *, now: int) -> None:
        new = root_document(envelope.get("signed"), now)
        require(new["version"] == self.root["version"] + 1, "root rotation must be sequential")
        verify(envelope, self.root, "root")
        verify(envelope, new, "root")
        with self.db:
            row = self.db.execute("SELECT root FROM trust WHERE id=1").fetchone()
            from core.registry.codec import decode

            require(decode(row[0])["signed"]["version"] == self.root["version"], "concurrent root rotation")
            self.db.execute("UPDATE trust SET root=?, sequence=0, snapshot=NULL WHERE id=1", (canonical(envelope),))
        self.root, self.current = new, None

    @serialized
    def accept(self, envelope: dict, *, now: int) -> dict:
        root_document(self.root, now)
        require(len(canonical(envelope)) <= 4 * 1024 * 1024, "catalog exceeds byte limit")
        value = verify(envelope, self.root, "catalog")
        require(set(value) == {"schema", "root_version", "sequence", "issued_at", "expires", "entries", "revoked", "authorization"} and
                value["schema"] == "vool.registry.catalog.v1" and value["root_version"] == self.root["version"],
                "unknown catalog or root version")
        require(type(value["sequence"]) is int and value["sequence"] > 0 and
                type(value["issued_at"]) is int and type(value["expires"]) is int and
                value["issued_at"] <= now < value["expires"] <= value["issued_at"] + 48 * 3600, "stale catalog")
        require(isinstance(value["entries"], list) and len(value["entries"]) <= 4096 and
                isinstance(value["revoked"], list) and len(value["revoked"]) <= 4096 and
                all(HEX.fullmatch(str(item)) for item in value["revoked"]), "unbounded catalog")
        authorization = verify(value["authorization"], self.root, "admission")
        require(set(authorization) == {"schema", "root_version", "epoch", "issued_at", "expires", "entries_sha256", "revoked_sha256"} and
                authorization["schema"] == "vool.registry.catalog_authorization.v1" and
                authorization["root_version"] == self.root["version"] and type(authorization["epoch"]) is int and
                authorization["epoch"] > 0 and type(authorization["issued_at"]) is int and type(authorization["expires"]) is int and
                authorization["issued_at"] <= now < authorization["expires"] <= authorization["issued_at"] + 86400 and
                value["expires"] <= authorization["expires"] and authorization["entries_sha256"] == digest(value["entries"]) and
                authorization["revoked_sha256"] == digest(value["revoked"]), "offline catalog authorization mismatch or expiry")
        seen = set()
        for envelope_entry in value["entries"]:
            entry = verify(envelope_entry, self.root, "admission")
            validate_entry(entry, self.root, now)
            key = (entry["metadata"]["id"], entry["metadata"]["version"])
            require(key not in seen, "duplicate package version")
            seen.add(key)
        from core.registry.codec import decode

        encoded = canonical(envelope)
        # BEGIN IMMEDIATE prevents two clients racing a higher and lower sequence.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT root, sequence, snapshot FROM trust WHERE id=1").fetchone()
            require(decode(row[0])["signed"]["version"] == self.root["version"], "trust root changed")
            require(value["sequence"] >= row[1], "catalog rollback")
            require(value["sequence"] != row[1] or row[2] == encoded, "catalog equivocation")
            self.db.execute("CREATE TABLE IF NOT EXISTS epochs (root_version INTEGER PRIMARY KEY, epoch INTEGER, authorization TEXT)")
            epoch = self.db.execute("SELECT epoch,authorization FROM epochs WHERE root_version=?", (self.root["version"],)).fetchone()
            require(not epoch or authorization["epoch"] >= epoch[0], "offline catalog rollback")
            require(not epoch or authorization["epoch"] != epoch[0] or digest(authorization) == epoch[1], "offline catalog equivocation")
            self.db.execute("INSERT OR REPLACE INTO epochs VALUES (?,?,?)", (self.root["version"], authorization["epoch"], digest(authorization)))
            # Revocations are sticky across snapshots and key rotations.
            self.db.execute("CREATE TABLE IF NOT EXISTS revoked (digest TEXT PRIMARY KEY)")
            self.db.executemany("INSERT OR IGNORE INTO revoked VALUES (?)", [(item,) for item in value["revoked"]])
            self.db.execute("UPDATE trust SET sequence=?, snapshot=? WHERE id=1", (value["sequence"], encoded))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.current = value
        return value

    @serialized
    def resolve(self, package_id: str, version: str, *, target: dict, now: int) -> dict:
        if self.current is None:
            from core.registry.codec import decode

            row = self.db.execute("SELECT snapshot FROM trust WHERE id=1").fetchone()
            require(row and row[0], "no verified catalog")
            self.accept(decode(row[0]), now=now)
        root_document(self.root, now)
        from core.registry.codec import decode

        stored_root = self.db.execute("SELECT root FROM trust WHERE id=1").fetchone()[0]
        require(decode(stored_root)["signed"]["version"] == self.root["version"], "trust root changed; reload client")
        require(now < self.current["expires"], "catalog freshness expired; refresh before installing")
        for envelope in self.current["entries"]:
            entry = verify(envelope, self.root, "admission")
            if (entry["metadata"]["id"], entry["metadata"]["version"]) == (package_id, version):
                validate_entry(entry, self.root, now)
                require(target in entry["targets"], "this exact VOOL build and platform has not passed compatibility")
                require(not self.db.execute("SELECT 1 FROM revoked WHERE digest=?", (entry["artifact_sha256"],)).fetchone(),
                        "package version is revoked")
                return entry
        raise RegistryError("package version is not in the approved catalog")


def validate_entry(entry: dict, root: dict, now: int) -> None:
    from core.registry.artifact import metadata

    require(isinstance(entry, dict) and set(entry) == {
        "schema", "metadata", "artifact_sha256", "artifact_length", "native_digest", "plugin_id",
        "dependencies", "policy_sha256", "evidence_sha256", "targets", "publication", "native_rule_review", "issued_at", "expires"} and
        entry["schema"] == "vool.registry.admission.v1", "invalid admission")
    metadata(entry["metadata"])
    from core.registry.admission import publication

    publication(entry["publication"], entry["artifact_sha256"], entry["metadata"]["kind"])
    from core.registry.rules import CONTROL_IDS

    native = entry["native_rule_review"]
    require(isinstance(native, dict) and set(native) == {"obeys_native_rules", "overlaps", "conflicts"} and
            native["obeys_native_rules"] is True and native["conflicts"] == [] and isinstance(native["overlaps"], list) and
            all(isinstance(control, str) and control in CONTROL_IDS for control in native["overlaps"]),
            "admission has no valid native rule review")
    require(type(entry["issued_at"]) is int and type(entry["expires"]) is int and
            entry["issued_at"] <= now < entry["expires"] <= entry["issued_at"] + 30 * 86400, "expired admission")
    require(all(HEX.fullmatch(str(entry[key])) for key in ("artifact_sha256", "native_digest", "policy_sha256", "evidence_sha256")) and
            entry["policy_sha256"] in root["policies"], "untrusted admission policy or hash")
    require(type(entry["artifact_length"]) is int and 0 < entry["artifact_length"] <= 24 * 1024 * 1024 and
            isinstance(entry["dependencies"], dict) and len(entry["dependencies"]) <= 64 and
            all(HEX.fullmatch(str(pin)) for pin in entry["dependencies"].values()), "unbounded admission")
    from core.registry.artifact import native_identity
    from core.registry.codec import COMMIT, IDENTITY

    require(entry["plugin_id"] == native_identity(entry["metadata"]["id"]) and
            all(IDENTITY.fullmatch(str(key)) for key in entry["dependencies"]), "invalid native plugin identity")
    targets = entry["targets"]
    require(isinstance(targets, list) and 0 < len(targets) <= 32 and
            len({digest(target) for target in targets}) == len(targets), "invalid target matrix")
    for target in targets:
        require(isinstance(target, dict) and set(target) == {"version", "commit", "platform"} and
                isinstance(target["version"], str) and target["version"] and
                bool(COMMIT.fullmatch(str(target["commit"]))) and
                target["platform"] in {"macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64"},
                "invalid compatibility target")
