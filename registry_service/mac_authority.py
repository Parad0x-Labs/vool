"""The Skill Codex's signing authority. It runs on the owner's Mac, never on the server.

    python -m registry_service.mac_authority setup --dir DIR --server URL --catalog-key HEX --targets FILE
    python -m registry_service.mac_authority run --dir DIR [--once]
    python -m registry_service.mac_authority mark|unmark|highlight|unhighlight|remove PUBLISHER/NAME --dir DIR
    python -m registry_service.mac_authority suspend|unsuspend NAME --dir DIR
    python -m registry_service.mac_authority team NAME --github-id ID [--github-id ID ...] --dir DIR
    python -m registry_service.mac_authority targets --file FILE --dir DIR
    python -m registry_service.mac_authority status --dir DIR

`run` takes uploads waiting on the server, runs every check itself on the exact bytes and signs
only what passes. Hand actions (the "Tested by Parad0x Labs" mark, the Highlighted spot, removing
a skill, suspending a creator, team members) each need a code from the owner's authenticator app.
That code stops anyone without the owner's phone from using these commands; it cannot protect a
Mac that someone has fully taken over, because the keys live here too.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import functools
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from core.registry import checks
from core.registry.admission import validate_policy
from core.registry.codec import HEX, IDENTITY, RegistryError, canonical, decode, digest, require, version_key
from core.registry.publisher import add_signature, admission, authorize_catalog
from core.registry.trust import AUTHORIZATION_SECONDS, root_document, verify
from registry_service.authority import HEADER, MARKS_SCHEMA, keyid, marks_document, sign_request

PREFIX = "/registry/api"
ADMISSION_SECONDS = 30 * 86400 - 3600
RENEW_BEFORE = 5 * 86400
AUTHORIZE_BEFORE = 3 * 86400
ROOT_SECONDS = 2 * 365 * 86400
REVIEW_ATTEMPTS = 3
# Paid reviews per publisher name per month, so one account cannot use up everyone's budget.
REVIEWS_PER_CREATOR = 30
_FINDING_KEYS = {"check": 64, "path": 300, "reason": 600, "quote": 120}
STEP = 30


# ---- one-time codes (RFC 6238, as Google Authenticator uses them) ----

def totp(secret: bytes, counter: int) -> str:
    mac = hmac.new(secret, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    return f"{(int.from_bytes(mac[offset:offset + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def check_code(secret: bytes, code: str, *, now: int, last: int) -> int:
    """The counter the code belongs to; a code is good once, within a step either side of now."""
    code = "".join(str(code).split())
    require(len(code) == 6 and code.isdigit(), "type the 6-digit code from your authenticator app")
    for counter in (now // STEP, now // STEP - 1, now // STEP + 1):
        if counter > last and hmac.compare_digest(totp(secret, counter), code):
            return counter
    raise RegistryError("that code is wrong or already used; wait for the next one")


def otpauth(secret: bytes) -> str:
    label = urllib.parse.quote("Skill Codex:signing Mac")
    return (f"otpauth://totp/{label}?secret={base64.b32encode(secret).decode().rstrip('=')}"
            "&issuer=Skill%20Codex&digits=6&period=30")


# ---- private files ----

def _write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _private(path: Path) -> bytes:
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, path.name + " is missing or readable by others")
    return path.read_bytes()


def _raw_key(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _public(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _shown(findings: list) -> list:
    """Refusal reasons in the exact shape the service stores, so a refusal is never itself refused."""
    shown = []
    for item in findings[:200]:
        value = {key: " ".join(str(item[key]).split())[:limit] for key, limit in _FINDING_KEYS.items()
                 if isinstance(item.get(key), str) and item[key].strip()}
        value.setdefault("check", "package")
        value.setdefault("path", "package")
        value.setdefault("reason", "This skill can't be published.")
        if type(item.get("line")) is int and 0 < item["line"] <= 10 ** 7:
            value["line"] = item["line"]
        shown.append(value)
    return shown


def _exclusive(method):
    """One signer at a time on this folder, working from the state on disk now.

    The runner and a hand action are separate processes; without this, one could save an older state over
    the other's, and a removed skill could come back.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._held:
            return method(self, *args, **kwargs)
        descriptor = os.open(self.dir / "lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._held = True
            self.state = json.loads(_private(self.dir / "state.json"))
            return method(self, *args, **kwargs)
        finally:
            self._held = False
            os.close(descriptor)  # closing releases the lock
    return wrapper


class Authority:
    """Everything the signing Mac keeps, in one private folder."""

    def __init__(self, directory: Path, *, transport=None, review=None, clock=time.time):
        self.dir = Path(directory)
        require(self.dir.is_dir() and self.dir.stat().st_mode & 0o077 == 0, "the authority folder must be private (0700)")
        self.config = json.loads(_private(self.dir / "config.json"))
        self.root_envelope = decode(_private(self.dir / "trusted-root.json"), 65536)
        self.clock = clock
        self.root = root_document(self.root_envelope["signed"], self.now(), allow_expired=True)
        verify(self.root_envelope, self.root, "root")
        self.admission_key = Ed25519PrivateKey.from_private_bytes(_private(self.dir / "admission.key"))
        self.review_key = Ed25519PrivateKey.from_private_bytes(_private(self.dir / "review.key"))
        require(keyid(self.admission_key) in self.root["roles"]["admission"]["keyids"] and
                keyid(self.review_key) in self.root["roles"]["review"]["keyids"], "keys do not match the trust root")
        self.state = json.loads(_private(self.dir / "state.json"))
        self._held = False
        self.transport = transport or self._http
        self._review = review

    def now(self) -> int:
        return int(self.clock())

    def save(self) -> None:
        _write(self.dir / "state.json", json.dumps(self.state, sort_keys=True).encode())

    @property
    def review(self):
        if self._review is None:
            from registry_service.model_review import ModelReview

            settings = self.config["review"]
            self._review = ModelReview(settings["provider"], settings["model"], ledger=self.dir / "spend.json",
                                       monthly_cap=settings.get("monthly_cap", 20.0),
                                       key=os.environ.get("VOOL_AUTHORITY_OPENROUTER_KEY", ""))
        return self._review

    # ---- the server ----

    def _http(self, method: str, url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body if method == "POST" else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read(16 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            return error.code, error.read(65536)

    def call(self, method: str, path: str, value=None, *, raw: bool = False):
        body = b"" if value is None else canonical(value)
        headers = {HEADER: sign_request(self.admission_key, method, PREFIX + path, body, now=self.now())}
        if value is not None:
            headers["Content-Type"] = "application/json"
        status, data = self.transport(method, self.config["server"] + PREFIX + path, body, headers)
        if status != 200:
            try:
                message = decode(data, 65536).get("error", "")
            except (RegistryError, ValueError, AttributeError):
                message = ""
            raise RegistryError("the server refused: " + (message or f"HTTP {status}"))
        return data if raw else decode(data, 16 * 1024 * 1024)

    # ---- checks and signing ----

    def policy(self) -> dict:
        return checks.policy(self.state["targets"])

    def _texts(self, artifact: dict) -> dict[str, str]:
        pictures = checks._picture_paths(artifact)
        return {name: data.decode("utf-8") for name, data in artifact["files"].items() if name not in pictures}

    def _admit(self, raw: bytes, outcome: dict, verdict: dict) -> dict:
        policy = self.policy()
        require(validate_policy(policy) in self.root["policies"], "these checks are not the ones the trust root names")
        evidence = checks.evidence(outcome, policy, verdict, now=self.now())
        review = add_signature({"signed": evidence, "signatures": []}, self.review_key)
        envelope = admission(raw, policy, review, self.root, now=self.now(), lifetime=ADMISSION_SECONDS)
        return add_signature(envelope, self.admission_key)

    def check(self, raw: bytes, *, official: bool) -> dict:
        """{"refused": findings} or {"admission": envelope}, or {} while the model review is unavailable."""
        try:
            return self._check(raw, official=official)
        except Exception as exc:  # anything unexpected about one package refuses it, never stops the signer
            if isinstance(exc, (OSError, MemoryError)):
                raise  # this Mac's own trouble, not the package's
            print("Refused " + hashlib.sha256(raw).hexdigest() + " after an unexpected " + type(exc).__name__ + ": " +
                  " ".join(str(exc).split())[:300], file=sys.stderr)
            return {"refused": [{"check": "archive", "path": "package", "reason":
                                 "Our checks couldn't read this skill. Make it plainer, then upload it again."}]}

    def _clash(self, metadata: dict) -> str | None:
        """A version number this skill already used or passed, which the signed list cannot hold twice."""
        mine = [item["signed"]["metadata"] for item in self.state["entries"]] + \
            [item["admission"]["signed"]["metadata"] for item in self.state.get("pending", {}).values()]
        for other in mine:
            if other["id"] == metadata["id"] and version_key(other["version"]) >= version_key(metadata["version"]):
                return "Version " + metadata["version"] + " is not higher than " + other["version"] + \
                       ", which is already listed. Use a higher version number."
        return None

    def _check(self, raw: bytes, *, official: bool) -> dict:
        from registry_service.model_review import CapReached, NoAnswer, ReviewUnavailable

        outcome = checks.run(raw, self.dir / "scratch", targets=self.state["targets"], official=official)
        findings = [item for items in outcome["findings"].values() for item in items]
        if findings:
            return {"refused": findings[:200]}
        artifact = outcome["artifact"]
        clash = self._clash(artifact["metadata"])
        if clash and artifact["sha256"] not in {item["signed"]["artifact_sha256"] for item in self.state["entries"]}:
            return {"refused": [{"check": "source", "path": "registry.json", "reason": clash}]}
        cached = self.state["verdicts"].get(artifact["sha256"])
        if cached is not None and "refused" in cached:
            return {"refused": cached["refused"]}
        if cached is None:
            publisher = artifact["metadata"]["id"].split("/")[0]
            month = time.strftime("%Y-%m", time.gmtime(self.now()))
            used = self.state.setdefault("reviews", {}).setdefault(month, {})
            if not official and used.get(publisher, 0) >= REVIEWS_PER_CREATOR:
                return {"refused": [{"check": "instructions-secondary", "path": "package", "reason":
                                     "This account has used this month's checks. Upload it again next month."}]}
            self.state["reviews"] = {month: used}  # older months are done with
            try:
                result = self.review.review(artifact, self._texts(artifact))
            except (CapReached, NoAnswer):
                return {}  # nothing was answered: it waits, and nothing counts against it
            except ReviewUnavailable:
                used[publisher] = used.get(publisher, 0) + 1
                attempts = self.state["attempts"].get(artifact["sha256"], 0) + 1
                self.state["attempts"][artifact["sha256"]] = attempts
                if attempts < REVIEW_ATTEMPTS:
                    return {}
                return {"refused": [{"check": "instructions-secondary", "path": "package", "reason":
                        "Our checks couldn't finish reading this skill. Make it shorter and plainer, then upload it again."}]}
            except RegistryError as exc:
                return {"refused": [{"check": "instructions-secondary", "path": "package", "reason": str(exc)}]}
            used[publisher] = used.get(publisher, 0) + 1
            if result["verdict"] == "refuse":
                # The same bytes uploaded again get the same answer without another paid review.
                self.state["verdicts"][artifact["sha256"]] = {"refused": result["findings"]}
                return {"refused": result["findings"]}
            cached = {"model": self.review.name, "reviewed_at": self.now()}
            self.state["verdicts"][artifact["sha256"]] = cached
        return {"admission": self._admit(raw, outcome, {"model": cached["model"], "verdict": "pass",
                                                        "reviewed_at": cached["reviewed_at"]})}

    def _store(self, raw: bytes) -> None:
        folder = self.dir / "artifacts"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / (hashlib.sha256(raw).hexdigest() + ".json")
        if not path.exists():
            _write(path, raw)

    def _renew(self) -> bool:
        """Re-sign entries before they expire, or when the release list changed. Each is checked again first."""
        changed, kept = False, []
        for envelope in self.state["entries"]:
            entry = envelope["signed"]
            if entry["expires"] - self.now() > RENEW_BEFORE and entry["targets"] == self.state["targets"]:
                kept.append(envelope)
                continue
            changed = True
            raw = (self.dir / "artifacts" / (entry["artifact_sha256"] + ".json")).read_bytes()
            result = self.check(raw, official=entry["metadata"]["id"].split("/")[0] in self.state["marks"]["teams"])
            if "admission" in result:
                kept.append(result["admission"])
            elif "refused" in result:
                # Today's rules refuse it: it leaves the list rather than being re-signed.
                print("Dropped " + entry["metadata"]["id"] + " " + entry["metadata"]["version"] + ": " +
                      result["refused"][0]["reason"])
            elif entry["expires"] > self.now() + 3600:
                kept.append(envelope)  # the model review is unavailable; try again next run
        self.state["entries"] = kept
        return changed

    def publish(self) -> dict:
        # The epoch is spent before sending: a lost answer must never lead to a second, different list
        # under the same epoch, which the service would refuse as equivocation from then on.
        self.state["epoch"] += 1
        self.save()
        authorization = authorize_catalog(self.root, self.state["entries"], epoch=self.state["epoch"],
                                          revoked=self.state["revoked"], now=self.now(), lifetime=AUTHORIZATION_SECONDS)
        authorization = add_signature(authorization, self.admission_key)
        result = self.call("POST", "/authority/catalog", {"authorization": authorization,
                                                          "entries": self.state["entries"], "revoked": self.state["revoked"]})
        self.state["authorized_until"] = authorization["signed"]["expires"]
        self.state["unpublished"] = False
        self.save()
        return result

    def _deliver(self, report: dict) -> None:
        """Send admissions the service has not confirmed yet. Only confirmed ones join the signed list."""
        pending = self.state.setdefault("pending", {})
        for sha, item in sorted(pending.items()):
            try:
                self.call("POST", "/authority/submissions/" + item["id"], {"sha256": sha, "admission": item["admission"]})
            except RegistryError as exc:
                if "not waiting for checks" not in str(exc):
                    report["errors"].append(item["label"] + ": " + str(exc))
                    continue  # try again next run
                # Refused or gone on the service meanwhile (a suspension, say): it is not listed.
                report["errors"].append(item["label"] + ": no longer waiting on the service; not listed")
            else:
                self.state["entries"] = [envelope for envelope in self.state["entries"]
                                         if envelope["signed"]["artifact_sha256"] != sha] + [item["admission"]]
                self.state["unpublished"] = True
                report["admitted"].append(item["label"])
            del pending[sha]
            self.save()

    @_exclusive
    def run_once(self) -> dict:
        report = {"admitted": [], "refused": [], "waiting": [], "errors": []}
        self._deliver(report)
        for item in self.call("GET", "/authority/queue")["submissions"]:
            label = item["package_id"] + " " + item["version"]
            try:
                raw = self.call("GET", "/authority/submissions/" + item["id"], raw=True)
                require(hashlib.sha256(raw).hexdigest() == item["sha256"], "the server sent different bytes")
                # Whether a skill is the team's comes from this Mac's own signed marks, never from the service's reply.
                publisher = item["package_id"].split("/")[0]
                result = self.check(raw, official=publisher in self.state["marks"]["teams"])
                if "refused" in result:
                    self.call("POST", "/authority/submissions/" + item["id"], {"sha256": item["sha256"],
                                                                             "refused": _shown(result["refused"])})
                    report["refused"].append(label)
                elif "admission" in result:
                    self._store(raw)
                    # Saved before it is sent, so a crash or a lost answer is sent again, never forgotten.
                    self.state.setdefault("pending", {})[item["sha256"]] = {"id": item["id"], "label": label,
                                                                            "admission": result["admission"]}
                    self.save()
                    self._deliver(report)
                else:
                    report["waiting"].append(label)
            except RegistryError as exc:
                report["errors"].append(label + ": " + str(exc))  # this upload waits; the others go on
            self.save()
        if self._renew():
            self.state["unpublished"] = True
        if self.state.get("unpublished") or self.state.get("authorized_until", 0) - self.now() < AUTHORIZE_BEFORE:
            report["catalog"] = self.publish()
        self.save()
        return report

    # ---- hand actions ----

    @_exclusive
    def gate(self, code: str) -> None:
        secret = _private(self.dir / "totp.key")
        counter = check_code(secret, code, now=self.now(), last=self.state.get("totp_last", 0))
        self.state["totp_last"] = counter
        self.save()

    def _marks(self) -> dict:
        marks = self.state["marks"]
        value = marks_document({"schema": MARKS_SCHEMA, "sequence": marks["sequence"] + 1, "issued_at": self.now(),
                                **{key: marks[key] for key in ("tested", "highlighted", "suspended", "teams")}},
                               now=self.now())
        self.call("POST", "/authority/marks", add_signature({"signed": value, "signatures": []}, self.admission_key))
        marks["sequence"] = value["sequence"]
        self.save()
        return value

    def _listed(self, package_id: str) -> bool:
        return any(envelope["signed"]["metadata"]["id"] == package_id for envelope in self.state["entries"])

    def _newest(self, package_id: str) -> str:
        """The checksum of the version the Skill Codex shows for this skill."""
        versions = [envelope["signed"] for envelope in self.state["entries"] if envelope["signed"]["metadata"]["id"] == package_id]
        require(versions, "that skill is not in the Skill Codex")
        return max(versions, key=lambda entry: version_key(entry["metadata"]["version"]))["artifact_sha256"]

    @_exclusive
    def act(self, action: str, target: str, *, members: list[str] | None = None) -> str:
        marks = self.state["marks"]
        if action in {"mark", "unmark", "highlight", "unhighlight", "remove"}:
            parts = target.split("/")
            require(len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "name the skill as publisher/name")
        else:
            require(bool(IDENTITY.fullmatch(target)), "name the creator or team by its publisher name")
        if action == "mark":
            # The mark belongs to the version on show now; an update needs its own look.
            tested = target + "@" + self._newest(target)
            require(tested not in marks["tested"], "already done")
            marks["tested"] = sorted([item for item in marks["tested"] if item.partition("@")[0] != target] + [tested])
        elif action == "highlight":
            require(self._listed(target), "that skill is not in the Skill Codex")
            require(target not in marks["highlighted"], "already done")
            marks["highlighted"] = sorted([*marks["highlighted"], target])
        elif action in {"unmark", "unhighlight"}:
            field = "tested" if action == "unmark" else "highlighted"
            kept = [item for item in marks[field] if item.partition("@")[0] != target]
            require(len(kept) < len(marks[field]), "nothing to undo")
            marks[field] = kept
        elif action == "suspend":
            require(target not in marks["suspended"] and target not in marks["teams"], "already suspended, or a team")
            marks["suspended"] = sorted([*marks["suspended"], target])
        elif action == "unsuspend":
            require(target in marks["suspended"], "nothing to undo")
            marks["suspended"] = [item for item in marks["suspended"] if item != target]
        elif action == "team":
            require(members and all(member.isdigit() for member in members), "give each member's GitHub account number")
            marks["teams"] = {**marks["teams"], target: sorted(set(members))}
        elif action == "remove":
            removed = [envelope for envelope in self.state["entries"] if envelope["signed"]["metadata"]["id"] == target]
            require(removed, "that skill is not in the Skill Codex")
            self.state["entries"] = [envelope for envelope in self.state["entries"] if envelope not in removed]
            self.state["revoked"] = sorted(set(self.state["revoked"]) | {item["signed"]["artifact_sha256"] for item in removed})
            for field in ("tested", "highlighted"):
                marks[field] = [item for item in marks[field] if item.partition("@")[0] != target]
            self.publish()
        self._marks()
        return "Done."

    @_exclusive
    def set_targets(self, targets: list[dict]) -> str:
        require(isinstance(targets, list) and targets and canonical(targets), "list the released VOOL builds")
        require(validate_policy(checks.policy(targets)) in self.root["policies"], "invalid release list")
        self.state["targets"] = targets
        self.save()
        return "Saved. The next run re-signs every skill for these builds."


def setup(directory: Path, *, server: str, catalog_key: str, targets: list[dict], review: dict,
          passphrase: str, now: int) -> tuple[str, bytes]:
    """Create the keys, the trust root and the authenticator secret. Returns (otpauth URI, totp secret)."""
    parsed = urllib.parse.urlsplit(server)
    require(parsed.scheme == "https" and parsed.netloc and not parsed.path.strip("/") and not parsed.query,
            "the server must be a plain https:// address")
    require(bool(HEX.fullmatch(catalog_key)) and len(catalog_key) == 64, "paste the online key's public line")
    require(len(passphrase) >= 12, "the main key's passphrase needs at least 12 characters")
    require(review.get("provider") in {"openrouter", "ollama"} and review.get("model"), "choose the review model")
    directory.mkdir(mode=0o700)  # never reuse a folder: keys are made once
    keys = {role: Ed25519PrivateKey.generate() for role in ("root", "admission", "review")}
    public = {keyid(key): _public(key) for key in keys.values()}
    catalog_id = digest({"ed25519": catalog_key})
    public[catalog_id] = catalog_key
    roles = {role: {"keyids": [keyid(key)], "threshold": 1} for role, key in keys.items()}
    roles["catalog"] = {"keyids": [catalog_id], "threshold": 1}
    root = {"schema": "vool.registry.root.v1", "version": 1, "expires": now + ROOT_SECONDS, "keys": public,
            "roles": roles, "policies": [validate_policy(checks.policy(targets))]}
    root_document(root, now)
    envelope = add_signature({"signed": root, "signatures": []}, keys["root"])
    _write(directory / "root.pem", keys["root"].private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                                              BestAvailableEncryption(passphrase.encode())))
    _write(directory / "admission.key", _raw_key(keys["admission"]))
    _write(directory / "review.key", _raw_key(keys["review"]))
    secret = secrets.token_bytes(20)
    _write(directory / "totp.key", secret)
    _write(directory / "trusted-root.json", canonical(envelope))
    _write(directory / "config.json", json.dumps({"server": server.rstrip("/"), "review": review}, sort_keys=True).encode())
    _write(directory / "state.json", json.dumps({
        "epoch": 0, "entries": [], "revoked": [], "targets": targets, "verdicts": {}, "attempts": {}, "totp_last": 0,
        "authorized_until": 0, "marks": {"sequence": 0, "tested": [], "highlighted": [], "suspended": [], "teams": {}}},
        sort_keys=True).encode())
    return otpauth(secret), secret


def unlock_root(directory: Path, passphrase: str) -> Ed25519PrivateKey:
    """Only for a future root change; day-to-day signing never needs the main key."""
    key = load_pem_private_key(_private(directory / "root.pem"), password=passphrase.encode())
    require(isinstance(key, Ed25519PrivateKey), "invalid main key")
    return key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Skill Codex signing authority (runs on the owner's Mac)")
    parser.add_argument("command", choices=["setup", "run", "status", "targets", "mark", "unmark", "highlight",
                                            "unhighlight", "remove", "suspend", "unsuspend", "team"])
    parser.add_argument("target", nargs="?", default="")
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--server", default="")
    parser.add_argument("--catalog-key", default="")
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="")
    parser.add_argument("--cap", type=float, default=20.0)
    parser.add_argument("--github-id", action="append", default=[])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--every", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            require(args.targets is not None, "give the released builds with --targets")
            passphrase = getpass.getpass("Choose a passphrase for the main key: ")
            require(passphrase == getpass.getpass("Type it again: "), "the passphrases differ")
            uri, _secret = setup(args.dir, server=args.server, catalog_key=args.catalog_key,
                                targets=json.loads(args.targets.read_text()),
                                review={"provider": args.provider, "model": args.model, "monthly_cap": args.cap},
                                passphrase=passphrase, now=int(time.time()))
            print("Add this to your authenticator app (scan it as a QR code, or type the key):\n" + uri)
            authority = Authority(args.dir)
            authority.gate(input("Type the code your app shows now, to confirm: "))
            print("Set up. Copy trusted-root.json to the server and into the app release. Keep a copy of root.pem "
                  "and its passphrase offline, for example on a USB stick in a drawer.")
            return 0
        authority = Authority(args.dir)
        if args.command == "run":
            while True:
                try:
                    print(json.dumps(authority.run_once(), sort_keys=True), flush=True)
                except (RegistryError, OSError) as exc:
                    if args.once:
                        raise
                    # The service or the network is down: keep running and try again.
                    print("Will try again: " + (str(exc) if isinstance(exc, RegistryError) else type(exc).__name__), flush=True)
                if args.once:
                    return 0
                time.sleep(max(60, args.every))
        if args.command == "status":
            print(json.dumps({"skills": len(authority.state["entries"]), "revoked": len(authority.state["revoked"]),
                              "list_valid_until": authority.state.get("authorized_until", 0),
                              "review_spent_this_month": authority.review.spent() if authority.config["review"]["provider"]
                              == "openrouter" else 0, "marks": authority.state["marks"]}, indent=2, sort_keys=True))
            return 0
        authority.gate(getpass.getpass("Authenticator code: "))
        if args.command == "targets":
            print(authority.set_targets(json.loads(args.file.read_text())))
        else:
            print(authority.act(args.command, args.target, members=args.github_id))
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Refused: " + (str(exc) if isinstance(exc, RegistryError) else type(exc).__name__))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
