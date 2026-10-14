"""The signing Mac's door into the service: requests signed by the admission key, never a session.

The Mac is the authority. It pulls uploads waiting for checks, runs every check itself on the
exact bytes and sends back either the reasons a skill was refused or a signed admission. It
also sends the signed list of admitted skills, and one signed document with the hand-made
marks: "Tested by Parad0x Labs", the Highlighted spot, suspended creators and team members.
The service verifies every signature against the trust root before it changes anything; an
online key only re-stamps how fresh the list is.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.registry.codec import HEX, IDENTITY, RegistryError, canonical, digest, require

HEADER = "x-vool-authority"
SKEW = 300
AGREEMENT = "mit-own-work-v1"
AGREEMENT_TEXT = ("I made this skill, or have the right to share it, and I release it under the MIT licence. "
                  "Anyone may use, copy, change and share it.")
ACCOUNT_AGE = 14 * 86400
MARKS_SCHEMA = "vool.registry.marks.v1"
_FIELD = re.compile(r"^(keyid|ts|nonce|sig)=([A-Za-z0-9+/=_-]{1,128})$")

MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def mit_license(holder: str, *, now: int) -> str:
    return MIT.format(year=time.gmtime(now).tm_year, holder=holder)


def keyid(private_key: Ed25519PrivateKey) -> str:
    return digest({"ed25519": private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()})


def _message(method: str, path: str, ts: int, nonce: str, body: bytes) -> bytes:
    return canonical({"schema": "vool.registry.authority_request.v1", "method": method.upper(), "path": path,
                      "ts": ts, "nonce": nonce, "body_sha256": hashlib.sha256(body).hexdigest()})


def sign_request(private_key: Ed25519PrivateKey, method: str, path: str, body: bytes, *, now: int) -> str:
    """Header value for one request. The nonce makes a captured request useless a second time."""
    nonce = secrets.token_hex(16)
    sig = base64.b64encode(private_key.sign(_message(method, path, now, nonce, body))).decode()
    return f"keyid={keyid(private_key)},ts={now},nonce={nonce},sig={sig}"


def precheck_request(root: dict, header: str, *, now: int) -> dict:
    """Everything that can be checked before the body is read: shape, time and a known key."""
    require(isinstance(header, str) and 0 < len(header) <= 512, "authority signature required")
    fields = {}
    for part in header.split(","):
        match = _FIELD.match(part.strip())
        require(match and match.group(1) not in fields, "invalid authority signature")
        fields[match.group(1)] = match.group(2)
    require(set(fields) == {"keyid", "ts", "nonce", "sig"} and HEX.fullmatch(fields["keyid"]) and
            fields["ts"].isdigit() and re.fullmatch(r"[0-9a-f]{32}", fields["nonce"]), "invalid authority signature")
    require(abs(now - int(fields["ts"])) <= SKEW, "authority signature is stale; check the signing Mac's clock")
    require(fields["keyid"] in root["roles"]["admission"]["keyids"], "unknown authority key")
    return fields


def verify_request(root: dict, header: str, method: str, path: str, body: bytes, *, now: int, remember) -> str:
    """Return the signing key id, or refuse. `remember(nonce, expires)` must refuse a nonce seen before."""
    fields = precheck_request(root, header, now=now)
    ts = int(fields["ts"])
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(root["keys"][fields["keyid"]]))
        public.verify(base64.b64decode(fields["sig"], validate=True), _message(method, path, ts, fields["nonce"], body))
    except (InvalidSignature, ValueError, TypeError):
        raise RegistryError("invalid authority signature") from None
    remember(fields["nonce"], ts + 2 * SKEW)
    return fields["keyid"]


def marks_document(value: dict, *, now: int) -> dict:
    """The hand-made marks, as the Mac signs them. Everything not listed is unmarked."""
    require(isinstance(value, dict) and set(value) == {"schema", "sequence", "issued_at", "tested", "highlighted",
                                                       "suspended", "teams"} and value["schema"] == MARKS_SCHEMA,
            "invalid marks document")
    require(type(value["sequence"]) is int and value["sequence"] > 0 and type(value["issued_at"]) is int and
            value["issued_at"] <= now + SKEW, "invalid marks sequence")
    for field in ("tested", "highlighted"):
        items = value[field]
        require(isinstance(items, list) and len(items) <= 4096 and len(set(items)) == len(items), "invalid " + field)
        for item in items:
            # "Tested" names the exact version that was tested; an update drops the mark until it is tested again.
            name, _, sha = item.partition("@") if isinstance(item, str) else ("", "", "")
            require(len(name.split("/")) == 2 and all(IDENTITY.fullmatch(part) for part in name.split("/")) and
                    (bool(re.fullmatch(r"[0-9a-f]{64}", sha)) if field == "tested" else "@" not in item),
                    "invalid " + field)
    suspended = value["suspended"]
    require(isinstance(suspended, list) and len(suspended) <= 4096 and len(set(suspended)) == len(suspended) and
            all(isinstance(item, str) and IDENTITY.fullmatch(item) for item in suspended), "invalid suspended list")
    teams = value["teams"]
    require(isinstance(teams, dict) and len(teams) <= 16 and
            all(isinstance(name, str) and IDENTITY.fullmatch(name) and isinstance(members, list) and
                0 < len(members) <= 64 and len(set(members)) == len(members) and
                all(isinstance(member, str) and member.isdigit() and 0 < len(member) <= 20 for member in members)
                for name, members in teams.items()), "invalid teams")
    require(not set(teams) & set(suspended), "a team cannot be suspended")
    return value


def load_online_key(path) -> Ed25519PrivateKey:
    """The service's online catalog key: raw Ed25519 bytes in a 0600 file inside the private directory."""
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, "online catalog key is missing or readable by others")
    raw = path.read_bytes()
    require(len(raw) == 32, "invalid online catalog key")
    return Ed25519PrivateKey.from_private_bytes(raw)


def create_online_key(path) -> Ed25519PrivateKey:
    """Create the key once; an existing key is kept, never replaced."""
    import os

    if path.exists():
        return load_online_key(path)
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    return key


class OnlineCatalog:
    """Re-stamp the Mac's signed list with the online key and keep the service and catalog file current.

    The online key can only say "this list, still fresh"; the list and every entry in it carry the
    Mac's signatures, which the trusted catalog checks again on every accept.
    """

    LIFETIME = 6 * 3600

    def __init__(self, store, trust, key: Ed25519PrivateKey, catalog_file, target: dict):
        require(keyid(key) in trust.root["roles"]["catalog"]["keyids"], "the online key is not in the trust root")
        self.store, self.trust, self.key, self.catalog_file, self.target = store, trust, key, catalog_file, target
        # One re-stamp at a time: the timer and a new list from the Mac must not race for a sequence number.
        self.lock = threading.Lock()

    def _sign(self, entries: list, revoked: list, authorization: dict, *, now: int) -> dict:
        from core.registry.publisher import add_signature, catalog

        expires = authorization["signed"]["expires"]
        require(now < expires, "the signing Mac's list has expired; run the Mac signer")
        sequence = self.trust.db.execute("SELECT sequence FROM trust WHERE id=1").fetchone()[0] + 1
        snapshot = catalog(self.trust.root, entries, sequence=sequence, revoked=revoked, authorization=authorization,
                           now=now, lifetime=min(self.LIFETIME, expires - now))
        return add_signature(snapshot, self.key)

    def ingest(self, value: dict, *, now: int) -> dict:
        """Take a new signed list from the Mac: verify, re-stamp, publish what is new, write the catalog file."""
        require(isinstance(value, dict) and set(value) == {"authorization", "entries", "revoked"} and
                isinstance(value["entries"], list) and isinstance(value["revoked"], list), "invalid catalog update")
        with self.lock:
            snapshot = self._sign(value["entries"], value["revoked"], value["authorization"], now=now)
            accepted = self.trust.accept(snapshot, now=now)
            published = self._publish(now=now)
            self._write(snapshot)
        return {"sequence": accepted["sequence"], "expires": accepted["expires"], "published": published}

    def refresh(self, *, now: int) -> bool:
        """Re-stamp the current list when less than half its life is left. False when nothing was needed."""
        with self.lock:
            return self._refresh(now=now)

    def _refresh(self, *, now: int) -> bool:
        current = self.trust.current
        if current is None:
            from core.registry.codec import decode

            row = self.trust.db.execute("SELECT snapshot FROM trust WHERE id=1").fetchone()
            require(row and row[0], "no signed list yet; run the Mac signer")
            current = decode(row[0])["signed"]
        if self.trust.current is not None and current["expires"] - now > self.LIFETIME // 2:
            return False
        snapshot = self._sign(current["entries"], current["revoked"], current["authorization"], now=now)
        self.trust.accept(snapshot, now=now)
        self._publish(now=now)
        self._write(snapshot)
        return True

    def _publish(self, *, now: int) -> list[str]:
        published = []
        with self.store.connection() as db:
            stored = {row[0] for row in db.execute("SELECT sha256 FROM public_versions")}
        for envelope in self.trust.current["entries"]:
            entry = envelope["signed"]
            if entry["artifact_sha256"] in stored or entry["artifact_sha256"] in self.trust.current["revoked"]:
                continue
            raw = self.store.admitted_artifact(entry["artifact_sha256"])
            if raw is None:
                continue  # an older entry served from the artifact folder at start
            self.store.publish(self.trust, entry["metadata"]["id"], entry["metadata"]["version"], self.target, raw, now=now)
            published.append(entry["metadata"]["id"] + "@" + entry["metadata"]["version"])
        return published

    def _write(self, snapshot: dict) -> None:
        """Keep the catalog file in step, so a restart starts from the newest list, never an older one."""
        import os

        from core.registry.codec import decode

        directory = decode(self.catalog_file.read_bytes())
        temporary = self.catalog_file.with_name(self.catalog_file.name + ".new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical({**directory, "registry": snapshot}))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.catalog_file)
