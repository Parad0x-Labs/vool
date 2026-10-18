"""The Skill Codex signer as a GitHub Actions job, so nobody's computer has to stay on.

    python -m registry_service.github_authority setup --repo DIR --github OWNER/NAME --catalog-key HEX \\
        --model MODEL [--cap 20] --offline DIR [--targets FILE]
    python -m registry_service.github_authority run --repo DIR

It is the signer in registry_service.mac_authority, run every few minutes by the workflow in
registry_service/github/ inside a public repository that only the team can change. What differs is
where things are kept:

- The online keys, and the key to the private part of the state, come from that repository's Actions
  secrets. setup puts them there through `gh` and never writes or prints them.
- The state lives in the repository. authority/state.json is in the open: the signed list, the marks
  and the release targets, all of which are published anyway. authority/private.bin is sealed with
  AES-GCM: the model's verdicts (whose reasons can quote a refused skill), counts per creator and spending.
- Every change is committed and pushed before anything is sent to the service. A run that finds another
  run's change to the state stops before it sends anything, so no list number or marks number is ever
  signed twice.
- The team's hand steps are lines in team-steps.txt, edited on GitHub behind its sign-in.
- Once a day every listed skill goes through today's checks again, and the release targets follow
  VOOL's published releases.

The main (root) key stays offline; it is only needed to change these keys.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, PrivateFormat

from core.registry import checks
from core.registry.admission import validate_policy
from core.registry.artifact import inspect
from core.registry.codec import COMMIT, HEX, IDENTITY, VERSION, RegistryError, canonical, decode, digest, require
from core.registry.publisher import add_signature
from core.registry.trust import root_document
from registry_service.authority import MARKS_SCHEMA, keyid, marks_document
from registry_service.http_transport import RedirectRefused, open_request
from registry_service.mac_authority import (
    ROOT_SECONDS,
    Authority,
    MissingArtifact,
    _exclusive,
    _public,
    _raw_key,
    _write,
)
from registry_service.operations import ACTIONS as OPERATOR_ACTIONS

FOLDER = "authority"
STEPS = "team-steps.txt"
WORKFLOW = ".github/workflows/skill-codex.yml"
TEMPLATES = Path(__file__).parent / "github"
SECRETS = {"admission": "SKILL_CODEX_ADMISSION_KEY", "review": "SKILL_CODEX_REVIEW_KEY", "state": "SKILL_CODEX_STATE_KEY"}
SERVER = "SKILL_CODEX_SERVER"
# Kept sealed: a refusal's reasons can quote the refused skill, and counts per creator say who uploaded.
# Uploads that are not listed (yet) are nobody else's business: a waiting admission can still be refused.
PRIVATE = ("verdicts", "attempts", "reviews", "pending")
_SEALED = b"SCP1"
_AAD = b"skill-codex private state v1"
BOT = ("sls_0x", "240776969+Parad0x-Labs@users.noreply.github.com")
VOOL = "Parad0x-Labs/vool"
GITHUB_API = "https://api.github.com"
# The builds VOOL reports for itself (core.registry.client.runtime_target); installing needs a released Mac build.
PLATFORMS = ("macos-arm64", "macos-x86_64")
RELEASES_FOLLOWED = 3
# Stay quiet about a down service while the signed list still has this long to run.
QUIET_WHILE = 2 * 86400
REMOVED = "This skill was taken off the Skill Codex. Ask the team before you publish it again."
_STEP_FORMS = {"tested": "tested publisher/name version", "highlighted": "highlighted publisher/name",
               "suspended": "suspended publisher", "removed": "removed publisher/name",
               "team": "team name github-account-number [github-account-number ...]",
               "build": "build version full-commit-id"}
_STEP_FORMS.update({name: name + " target" for name in OPERATOR_ACTIONS})
_STEP_FORMS.update({"picture": "picture publisher/name sha256", "official": "official publisher",
                    "featured": "featured publisher/name", "editors-pick": "editors-pick publisher/name",
                    "reviewer": "reviewer publisher team|community",
                    "review": "review publisher/name version reviewer YYYY-MM-DD"})


# ---- the sealed part of the state ----

def seal(key: bytes, value: dict) -> bytes:
    nonce = os.urandom(12)
    return _SEALED + nonce + AESGCM(key).encrypt(nonce, json.dumps(value, sort_keys=True).encode(), _AAD)


def unseal(key: bytes, data: bytes) -> dict:
    require(data[:4] == _SEALED and len(data) > 32, FOLDER + "/private.bin is not the signer's sealed state")
    try:
        value = json.loads(AESGCM(key).decrypt(data[4:16], data[16:], _AAD))
    except InvalidTag:
        raise RegistryError(FOLDER + "/private.bin does not open with " + SECRETS["state"]) from None
    require(isinstance(value, dict), FOLDER + "/private.bin is not the signer's sealed state")
    return value


def _secret(env, name: str) -> bytes:
    value = env.get(name, "")
    require(bool(HEX.fullmatch(value)), "the " + name + " secret is missing or is not 64 hex characters")
    return bytes.fromhex(value)


def _server(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    require(parsed.scheme == "https" and bool(parsed.netloc) and not parsed.path.strip("/") and not parsed.query and
            not parsed.fragment, "the " + SERVER + " secret must be the service's plain https:// address")
    return value.strip().rstrip("/")


def _put(path: Path, data: bytes) -> None:
    """Write only when the bytes change, so an unchanged state makes no commit."""
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    _write(path, data)


def _public_json(value) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


# ---- the repository ----

class Superseded(Exception):
    """Another signer run changed the state first. This run stops before it sends anything.

    Not a RegistryError, so no handler for one upload's trouble catches it and lets the run go on.
    """


class Repository:
    """The checkout the workflow made. The signer's state goes back to GitHub before anything is sent."""

    def __init__(self, path: Path, *, branch: str = "main", token: str = ""):
        self.path, self.branch = Path(path), branch
        # The checkout keeps no credentials, so the install step can't push; only this process holds the token,
        # passed to git in its environment and never written to the checkout.
        self._env = None
        if token:
            header = "AUTHORIZATION: basic " + base64.b64encode(b"x-access-token:" + token.encode()).decode()
            self._env = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                         "GIT_CONFIG_VALUE_0": header}

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", "-C", str(self.path), "-c", "user.name=" + BOT[0], "-c", "user.email=" + BOT[1],
                                 "-c", "commit.gpgsign=false", "-c", "http.followRedirects=false", *args], capture_output=True, text=True, timeout=120,
                                env=self._env)
        if check and result.returncode:
            if re.search(r"(?:HTTP |returned error: )30[12378]\b", result.stderr):
                raise RegistryError("git redirect refused")
            raise RegistryError("git " + args[0] + " failed: " + " ".join(result.stderr.split())[:300])
        return result

    def start(self) -> None:
        """Begin from the newest state on GitHub; a run queued behind another must not start from older state."""
        self._git("fetch", "-q", "origin", self.branch)
        self._git("merge", "-q", "--ff-only", "FETCH_HEAD")

    def fresh(self) -> None:
        """Stop when another run changed the state on GitHub since this run began: two signers ran at once."""
        self._git("fetch", "-q", "origin", self.branch)
        if self._git("diff", "--name-only", "HEAD...FETCH_HEAD", "--", FOLDER).stdout.strip():
            raise Superseded("another signer run changed the state meanwhile; this run stopped before sending anything")

    def save(self, message: str) -> bool:
        """Commit and push the state folder; False when nothing changed. Always looks at GitHub first."""
        self.fresh()
        self._git("add", "--", FOLDER)
        if self._git("diff", "--cached", "--quiet", "--", FOLDER, check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        for _ in range(3):
            if self._git("push", "-q", "origin", "HEAD:refs/heads/" + self.branch, check=False).returncode == 0:
                return True
            self.fresh()
            self._git("rebase", "-q", "FETCH_HEAD")  # only the team's own files moved, team-steps.txt say
        raise RegistryError("the signer's state could not be pushed to GitHub")


# ---- the team's hand steps ----

def _skill(value: str) -> bool:
    parts = value.split("/")
    return len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts)


def parse_steps(text: str) -> dict:
    """team-steps.txt line by line. A line that doesn't read cleanly stops every step, so nothing half-applies."""
    require(len(text) <= 512 * 1024, STEPS + " is too long")
    wanted = {"tested": [], "highlighted": [], "suspended": [], "removed": [], "teams": {}, "builds": []}
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        step, args = words[0].lower(), words[1:]
        require(step in _STEP_FORMS, f"{STEPS} line {number}: this is not a step; use a form listed in the file")
        form = f"{STEPS} line {number}: write it as \"{_STEP_FORMS[step]}\""
        if step in OPERATOR_ACTIONS:
            from registry_service.operations import validate

            require(len(args) == 1, form)
            operation = {"action": step, "target": args[0]}
            try:
                validate([*wanted.get("operations", []), operation])
            except RegistryError:
                raise RegistryError(form) from None
            wanted.setdefault("operations", []).append(operation)
        elif step in {"picture", "official", "featured", "editors-pick", "reviewer", "review"}:
            curation = wanted.setdefault("curation", {"schema": "vool.registry.curation.v1", "official_namespaces": [],
                "featured": [], "editors_picks": [], "reviewers": {}, "reviews": [], "approved_pictures": []})
            if step == "picture":
                require(len(args) == 2 and _skill(args[0]) and HEX.fullmatch(args[1]), form)
                curation["approved_pictures"].append({"id": args[0], "sha256": args[1]})
            elif step in {"official", "featured", "editors-pick"}:
                require(len(args) == 1 and (IDENTITY.fullmatch(args[0]) if step == "official" else _skill(args[0])), form)
                curation[{"official": "official_namespaces", "featured": "featured", "editors-pick": "editors_picks"}[step]].append(args[0])
            elif step == "reviewer":
                require(len(args) == 2 and IDENTITY.fullmatch(args[0]) and args[1] in {"team", "community"} and
                        args[0] not in curation["reviewers"], form)
                curation["reviewers"][args[0]] = args[1]
            else:
                require(len(args) == 4 and _skill(args[0]) and VERSION.fullmatch(args[1]) and
                        IDENTITY.fullmatch(args[2]) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args[3]), form)
                curation["reviews"].append(dict(zip(("id", "version", "reviewer", "date"), args, strict=True)))
        elif step == "tested":
            require(len(args) == 2 and _skill(args[0]) and bool(VERSION.fullmatch(args[1])), form)
            wanted["tested"].append((args[0], args[1]))
        elif step in {"highlighted", "removed"}:
            require(len(args) == 1 and _skill(args[0]), form)
            wanted[step].append(args[0])
        elif step == "suspended":
            require(len(args) == 1 and (bool(IDENTITY.fullmatch(args[0])) or _skill(args[0])), form)
            wanted["removed" if _skill(args[0]) else "suspended"].append(args[0])
        elif step == "build":
            require(len(args) == 2 and bool(VERSION.fullmatch(args[0])) and bool(COMMIT.fullmatch(args[1])), form)
            if [args[0], args[1]] not in wanted["builds"]:
                wanted["builds"].append([args[0], args[1]])
        else:
            require(len(args) >= 2 and bool(IDENTITY.fullmatch(args[0])) and args[0] not in wanted["teams"] and
                    len(args) <= 65 and all(re.fullmatch(r"[0-9]{1,20}", member) for member in args[1:]), form)
            wanted["teams"][args[0]] = sorted(set(args[1:]))
    require(len(wanted["teams"]) <= 16, STEPS + ": at most 16 teams")
    require(len(wanted["builds"]) <= 8, STEPS + ": at most 8 builds")
    require(all(len(wanted[field]) <= 4096 for field in ("tested", "highlighted", "suspended", "removed")),
            STEPS + ": too many steps")
    if "curation" in wanted:
        from registry_service.curation import validate_curation

        validate_curation(wanted["curation"])
    require(len(canonical(wanted)) <= 40000, STEPS + ": too many steps for one signed update")
    return wanted


# ---- VOOL's releases ----

def _github(token: str):
    def get(path: str):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "skill-codex-signer"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(GITHUB_API + path, headers=headers)
        try:
            with open_request(request, service="GitHub", timeout=30) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        except RedirectRefused:
            raise
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise RegistryError("GitHub could not list VOOL's releases: " + type(exc).__name__) from None
    return get


def released_targets(*, token: str = "", request=None) -> list[dict]:
    """The newest published VOOL releases, as the builds VOOL reports for itself."""
    get = request or _github(token)
    releases = get("/repos/" + VOOL + "/releases?per_page=30")
    require(isinstance(releases, list), "GitHub sent no release list")
    tags = sorted(((item["published_at"], item["tag_name"]) for item in releases
                   if isinstance(item, dict) and item.get("draft") is False and isinstance(item.get("published_at"), str)
                   and isinstance(item.get("tag_name"), str) and item["tag_name"].startswith("v")
                   and VERSION.fullmatch(item["tag_name"][1:])), reverse=True)
    require(bool(tags), "VOOL has no published release")
    targets = []
    for _, tag in tags[:RELEASES_FOLLOWED]:
        target = get("/repos/" + VOOL + "/git/ref/tags/" + urllib.parse.quote(tag, safe=""))
        target = target.get("object", {}) if isinstance(target, dict) else {}
        for _ in range(4):  # an annotated tag points at a tag object first
            if target.get("type") != "tag":
                break
            target = get("/repos/" + VOOL + "/git/tags/" + str(target.get("sha", ""))[:40])
            target = target.get("object", {}) if isinstance(target, dict) else {}
        require(target.get("type") == "commit" and bool(COMMIT.fullmatch(str(target.get("sha", "")))),
                "release " + tag + " does not name a commit")
        targets += [{"version": tag[1:], "commit": target["sha"], "platform": platform} for platform in PLATFORMS]
    return targets


# ---- the signer ----

class RepoAuthority(Authority):
    """The signer with its keys in GitHub's secrets and its state in the repository it runs in."""

    def __init__(self, repo: Path, *, env=None, transport=None, review=None, clock=time.time, releases=None,
                 repository: Repository | None = None):
        env = os.environ if env is None else env
        self.repo = Path(repo)
        self.repository = repository or Repository(self.repo)
        # This run's scratch, and the review's spend ledger while it runs; the workflow's machine is thrown away.
        self.dir = Path(tempfile.mkdtemp(prefix="skill-codex-"))
        self._state_key = _secret(env, SECRETS["state"])
        self._releases = releases
        self._sealed = ""
        self._removed: set[str] = set()
        # Without its key every clean upload would be refused for a reason that is the team's, not the creator's.
        self._openrouter = env.get("VOOL_AUTHORITY_OPENROUTER_KEY", "")
        require(review is not None or bool(self._openrouter.strip()),
                "the OPENROUTER_API_KEY secret is missing, so no upload can be reviewed")
        config = json.loads((self.repo / FOLDER / "config.json").read_text())
        require(isinstance(config, dict) and set(config) == {"review"}, FOLDER + "/config.json holds only the review settings")
        config["server"] = _server(env.get(SERVER, ""))
        self._begin(config, decode((self.repo / FOLDER / "trusted-root.json").read_bytes(), 65536),
                    Ed25519PrivateKey.from_private_bytes(_secret(env, SECRETS["admission"])),
                    Ed25519PrivateKey.from_private_bytes(_secret(env, SECRETS["review"])),
                    transport=transport, review=review, clock=clock)

    # ---- where the state is kept ----

    def _locked(self):
        # The workflow's concurrency group runs one signer at a time; Repository.save stops a second one.
        return contextlib.nullcontext()

    def _load_state(self) -> dict:
        require(self.repository._git("diff", "--quiet", "HEAD", "--", FOLDER + "/state.json",
                                     check=False).returncode == 0, "the signer's public state was edited by hand")
        public = decode((self.repo / FOLDER / "state.json").read_bytes())
        private = unseal(self._state_key, (self.repo / FOLDER / "private.bin").read_bytes())
        self._sealed = json.dumps(private, sort_keys=True)
        binding = private.pop("public_sha256", None)
        require(binding is None or binding == digest(public), "the signer's public state differs from its sealed state")
        spend = private.pop("spend", {})
        require(isinstance(public, dict) and set(private) <= set(PRIVATE) and not set(public) & set(PRIVATE),
                "the signer's state files are mixed up")
        self._validate_state(public)
        require(isinstance(spend, dict) and all(isinstance(key, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}", key) and
                type(value) in {float, int} and 0 <= value < float("inf") for key, value in spend.items()),
                "invalid sealed spend state")
        _write(self.dir / "spend.json", json.dumps(spend, sort_keys=True).encode())
        return {**public, **private}

    def _validate_state(self, value: dict) -> None:
        from core.registry.trust import validate_entry, verify

        required = {"epoch", "entries", "revoked", "targets", "authorized_until", "marks"}
        optional = {"released", "builds", "removed", "unpublished", "marks_unsent", "releases_checked", "rechecked", "moderation"}
        require(required <= set(value) <= required | optional and type(value["epoch"]) is int and value["epoch"] >= 0 and
                type(value["authorized_until"]) is int and value["authorized_until"] >= 0 and
                isinstance(value["entries"], list) and len(value["entries"]) <= 4096 and
                isinstance(value["revoked"], list) and len(value["revoked"]) <= 4096 and
                all(isinstance(item, str) and HEX.fullmatch(item) for item in value["revoked"]), "invalid public signer state")
        require(validate_policy(checks.policy(value["targets"])) in self.root["policies"], "invalid signer target state")
        for envelope in value["entries"]:
            entry = verify(envelope, self.root, "admission")
            validate_entry(entry, self.root, min(self.now(), entry["expires"] - 1))
        marks = value["marks"]
        require(isinstance(marks, dict) and type(marks.get("sequence")) is int and marks["sequence"] >= 0,
                "invalid signer marks state")
        marks_document({**marks, "schema": MARKS_SCHEMA, "sequence": max(1, marks["sequence"]), "issued_at": self.now()},
                       now=self.now())

    def save(self) -> None:
        private = {key: self.state[key] for key in PRIVATE if key in self.state}
        try:
            private["spend"] = json.loads((self.dir / "spend.json").read_text())
        except (OSError, ValueError):
            raise RegistryError("the review spend state cannot be read") from None
        public = {key: value for key, value in self.state.items() if key not in PRIVATE}
        private["public_sha256"] = digest(public)
        _put(self.repo / FOLDER / "state.json", _public_json(public))
        plain = json.dumps(private, sort_keys=True)
        if plain != self._sealed:  # sealing again only on a change keeps the history quiet
            _write(self.repo / FOLDER / "private.bin", seal(self._state_key, private))
            self._sealed = plain

    def checkpoint(self, message: str = "Skill Codex: state before sending") -> None:
        self.save()
        self.repository.save(message)

    def review_checkpoint(self) -> None:
        self.checkpoint("Skill Codex: review state before next upload")

    def call(self, method: str, path: str, value=None, *, raw: bool = False):
        # What is about to be signed and sent is on GitHub first, so a lost run never sends it differently and the
        # spending behind it is kept; a run another run overtook stops here, before it sends anything.
        if method != "GET":
            self.checkpoint()
        return super().call(method, path, value, raw=raw)

    def _model_key(self) -> str:
        return self._openrouter

    # The run log of a public repository is public: it never names an upload or a skill.
    def _said_unreadable(self, raw: bytes, exc: Exception) -> None:
        print("Refused an upload after an unexpected " + type(exc).__name__, file=sys.stderr)

    def _said_dropped(self, entry: dict, reason: str) -> None:
        print("Dropped a listed skill", file=sys.stderr)

    def _store(self, raw: bytes) -> None:
        pass  # the service keeps the exact bytes; _artifact fetches and checks them

    def _artifact(self, sha256: str) -> bytes:
        try:
            raw = self.call("GET", "/authority/artifacts/" + sha256, raw=True)
        except RegistryError as exc:
            if str(exc) == "the server refused: that skill is not stored here":
                raise MissingArtifact(str(exc)) from None  # a backup restore, say: that one skill leaves the list
            raise
        require(hashlib.sha256(raw).hexdigest() == sha256, "the server sent different bytes")
        return raw

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.now()))

    def _marks(self) -> dict:
        # The number is used up before sending, as the list's epoch is: after a lost answer the next try
        # sends a higher one, which the service accepts, instead of the same one, which it refuses forever.
        marks = self.state["marks"]
        value = marks_document({"schema": MARKS_SCHEMA, "sequence": marks["sequence"] + 1, "issued_at": self.now(),
                                **{key: marks[key] for key in ("tested", "highlighted", "suspended", "teams")},
                                **{key: marks[key] for key in ("operations", "curation") if key in marks}},
                               now=self.now())
        envelope = add_signature({"signed": value, "signatures": []}, self.admission_key)
        require(len(canonical(envelope)) <= 65536, "signed marks update exceeds byte limit; reduce the steps")
        marks["sequence"] = value["sequence"]
        self.state["marks_unsent"] = True
        self.call("POST", "/authority/marks", envelope)
        self.state.pop("marks_unsent")
        self.save()
        return value

    def _check(self, raw: bytes, *, official: bool) -> dict:
        try:
            package_id = inspect(raw)["metadata"]["id"]
        except (RegistryError, ValueError, TypeError, KeyError):
            package_id = None  # the checks themselves say why it can't be read
        if package_id in self._removed:
            return {"refused": [{"check": "source", "path": "package", "reason": REMOVED}]}  # before any paid review
        return super()._check(raw, official=official)

    # ---- what a run does besides the uploads ----

    def _read_steps(self) -> dict:
        path = self.repo / STEPS
        try:
            return parse_steps(path.read_text(encoding="utf-8") if path.exists() else "")
        except UnicodeDecodeError:
            raise RegistryError(STEPS + " is not plain text") from None

    def team_steps(self, wanted: dict | None = None) -> dict:
        """Apply a completely parsed set of operator steps, or stop before changing anything."""
        wanted = self._read_steps() if wanted is None else wanted
        problems = []
        self._removed = set(wanted["removed"]) | {
            item["signed"]["metadata"]["id"] for item in self.state["entries"]
            if item["signed"]["metadata"]["id"].split("/")[0] in set(wanted["suspended"]) - set(wanted["teams"])}
        gone = [envelope for envelope in self.state["entries"] if envelope["signed"]["metadata"]["id"] in self._removed]
        listed: dict[str, dict[str, str]] = {}
        for envelope in (item for item in self.state["entries"] if item not in gone):
            entry = envelope["signed"]
            listed.setdefault(entry["metadata"]["id"], {})[entry["metadata"]["version"]] = entry["artifact_sha256"]
        tested, highlighted, suspended = [], [], []
        for package_id, version in wanted["tested"]:
            if version in listed.get(package_id, {}):
                tested.append(package_id + "@" + listed[package_id][version])
            else:
                problems.append(f"tested {package_id} {version}: that version is not in the Skill Codex")
        for package_id in wanted["highlighted"]:
            if package_id in listed:
                highlighted.append(package_id)
            else:
                problems.append(f"highlighted {package_id}: that skill is not in the Skill Codex")
        for name in wanted["suspended"]:
            if name in wanted["teams"]:
                problems.append(f"suspended {name}: a team can't be suspended")
            else:
                suspended.append(name)
        new = {"tested": sorted(set(tested)), "highlighted": sorted(set(highlighted)), "suspended": sorted(set(suspended)),
               "teams": wanted["teams"]}
        marks = self.state["marks"]
        if "operations" in wanted or "operations" in marks:
            new["operations"] = wanted.get("operations", [])
        if "curation" in wanted or "curation" in marks:
            new["curation"] = wanted.get("curation", {"schema": "vool.registry.curation.v1", "official_namespaces": [],
                "featured": [], "editors_picks": [], "reviewers": {}, "reviews": [], "approved_pictures": []})
        if self.state.get("marks_unsent") or any(marks.get(key) != value for key, value in new.items()):
            marks.update(new)
            self._marks()
        # A bad operator target rolls back the service transaction above. It must not first remove
        # entries or revoke versions in the signer's durable state either.
        self.state["builds"] = wanted["builds"]
        self.state["removed"] = sorted(self._removed)
        if gone:
            self.state["entries"] = [envelope for envelope in self.state["entries"] if envelope not in gone]
            self.state["revoked"] = sorted(set(self.state["revoked"]) | {item["signed"]["artifact_sha256"] for item in gone})
            self.state["unpublished"] = True
            self.save()
        for sha, item in sorted(self.state.get("pending", {}).items()):
            if item["admission"]["signed"]["metadata"]["id"] in self._removed:
                del self.state["pending"][sha]
                with contextlib.suppress(RegistryError):
                    self.call("POST", "/authority/submissions/" + item["id"], {"sha256": sha, "refused": [
                        {"check": "source", "path": "package", "reason": REMOVED}]})
        return {"applied": True, "problems": problems}

    def _discover_releases(self) -> list | None:
        if self._releases is None or self.state.get("releases_checked") == self._today():
            return None
        try:
            released = self._releases()
            require(validate_policy(checks.policy(released)) in self.root["policies"],
                    "the release list does not fit the trust root")
        except Exception:
            raise RegistryError("release discovery failed; the next run retries") from None
        return released

    def follow_releases(self, released: list | None = None) -> bool:
        """Persist only a completely validated release discovery."""
        released = self._discover_releases() if released is None else released
        if released is not None:
            self.state["released"] = released
            self.state["releases_checked"] = self._today()
            self.save()
        return self._sign_for()

    def _sign_for(self) -> bool:
        """The builds the list is signed for: the followed releases, and any the team named to test before release."""
        released = self.state.get("released", self.state["targets"])
        named = [{"version": version, "commit": commit, "platform": platform}
                 for version, commit in self.state.get("builds", []) for platform in PLATFORMS]
        targets = released + [target for target in named if target not in released]
        if targets == self.state["targets"]:
            return False
        require(validate_policy(checks.policy(targets)) in self.root["policies"], "these builds do not fit the trust root")
        self.state["targets"] = targets  # _renew signs every listed skill again for these builds
        self.save()
        return True

    def recheck(self) -> list[str]:
        """Once a day: today's checks on every listed skill. One that fails them leaves the list."""
        if self.state.get("rechecked") == self._today():
            return []
        kept, dropped = [], []
        for envelope in self.state["entries"]:
            entry = envelope["signed"]
            try:
                raw = self._artifact(entry["artifact_sha256"])
            except MissingArtifact:
                dropped.append(envelope)  # nobody can download it any more
                continue
            official = entry["metadata"]["id"].split("/")[0] in self.state["marks"]["teams"]
            try:
                outcome = checks.run(raw, self.dir / "scratch", targets=self.state["targets"], official=official)
                clean = not any(outcome["findings"].values())
            except Exception as exc:  # checks that can't finish on a listed skill don't keep it listed
                if isinstance(exc, (OSError, MemoryError)):
                    raise
                clean = False
            (kept if clean else dropped).append(envelope)
        if dropped:
            self.state["entries"] = kept
            self.state["unpublished"] = True
        self.state["rechecked"] = self._today()
        self.save()
        return [item["signed"]["metadata"]["id"] + " " + item["signed"]["metadata"]["version"] for item in dropped]

    def _summary(self) -> str:
        return f"Skill Codex: {len(self.state['entries'])} skills listed, list {self.state['epoch']}"

    @_exclusive
    def run_once(self) -> dict:
        # All local instructions and required sources must be readable before any signed write.
        wanted = self._read_steps()
        released = self._discover_releases()
        queue = self.upload_queue()
        prepared = self.read_uploads(queue)
        from registry_service.operations import summary

        moderation = summary(self.call("GET", "/authority/moderation"))
        report = {"steps": self.team_steps(wanted), "dropped": []}
        self.state["moderation"] = moderation
        report["releases_changed"] = self.follow_releases(released)
        report["dropped"] = self.recheck()
        try:
            report.update(super().run_once(queue=queue, prepared=prepared))
        except BaseException:
            with contextlib.suppress(Exception):
                self.checkpoint("Skill Codex: state after a stopped run")  # keep what was spent and decided
            raise
        if report.get("stopped"):
            self.checkpoint("Skill Codex: review state before retry")
            return report
        from registry_service.publication import publish

        try:
            presentation = self.call("GET", "/presentation")
        except RegistryError as exc:
            if str(exc) != "the server refused: catalog freshness expired":
                raise
            presentation = {"marks": None, "metrics": []}
            report["presentation_waiting"] = True
        publish(self.repo / FOLDER, self.state["entries"], self.state["revoked"], self.state["marks"],
                artifact=self._artifact, presentation=presentation)
        self.checkpoint(self._summary())
        return report


def service_down(authority: RepoAuthority, exc: Exception) -> bool:
    """The service being unreachable stays quiet while the signed list has days left; after that the run turns red."""
    down = isinstance(exc, OSError) or bool(re.fullmatch(r"the server refused: HTTP 5[0-9][0-9]", str(exc)))
    return down and authority.state.get("authorized_until", 0) - authority.now() > QUIET_WHILE


def quiet(report: dict) -> dict:
    """What the public run log shows: counts, never which uploads were refused or are waiting."""
    return {"admitted": len(report.get("admitted", [])), "refused": len(report.get("refused", [])),
            "waiting": len(report.get("waiting", [])), "errors": len(report.get("errors", [])),
            "dropped": len(report.get("dropped", [])), "list_published": "catalog" in report,
            "team_steps": report["steps"]["problems"] + ([] if report["steps"]["applied"] else ["nothing applied"]),
            **{key: "The source could not be checked; the next run tries again."
               for key in ("releases_waiting", "recheck_waiting") if key in report}}


# ---- setup ----

def setup(repo: Path, *, github: str, catalog_key: str, targets: list[dict], review: dict, passphrase: str,
          offline: Path, vool_commit: str, now: int, set_secret) -> dict:
    """Make the keys and the repository's files. The online keys go straight into GitHub's secrets."""
    repo, offline = Path(repo), Path(offline)
    require(bool(re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", github)), "name the repository as OWNER/NAME")
    require((repo / ".git").exists(), "--repo must be a checkout of the new repository")
    require(not (repo / FOLDER).exists() and not (repo / ".gitattributes").exists(),
            "this repository already has a signer; keys are made once")
    require(not offline.exists() and not offline.resolve().is_relative_to(repo.resolve()),
            "--offline must be a new folder outside the repository, for the main key")
    require(bool(HEX.fullmatch(catalog_key)), "paste the online key's public line")
    require(len(passphrase) >= 12, "the main key's passphrase needs at least 12 characters")
    require(review.get("provider") == "openrouter" and bool(review.get("model")), "choose the review model")
    require(bool(COMMIT.fullmatch(vool_commit)), "the VOOL commit must be one full commit id")
    keys = {role: Ed25519PrivateKey.generate() for role in ("root", "admission", "review")}
    state_key = os.urandom(32)
    public = {keyid(key): _public(key) for key in keys.values()}
    catalog_id = digest({"ed25519": catalog_key})
    public[catalog_id] = catalog_key
    roles = {role: {"keyids": [keyid(key)], "threshold": 1} for role, key in keys.items()}
    roles["catalog"] = {"keyids": [catalog_id], "threshold": 1}
    root = {"schema": "vool.registry.root.v1", "version": 1, "expires": now + ROOT_SECONDS, "keys": public,
            "roles": roles, "policies": [validate_policy(checks.policy(targets))]}
    root_document(root, now)
    envelope = add_signature({"signed": root, "signatures": []}, keys["root"])
    # GitHub holds the online keys before anything is written, so a failure here leaves nothing behind.
    set_secret(SECRETS["admission"], _raw_key(keys["admission"]).hex())
    set_secret(SECRETS["review"], _raw_key(keys["review"]).hex())
    set_secret(SECRETS["state"], state_key.hex())
    offline.mkdir(mode=0o700, parents=True)
    _write(offline / "root.pem", keys["root"].private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                                           BestAvailableEncryption(passphrase.encode())))
    _write(offline / "trusted-root.json", canonical(envelope))
    folder = repo / FOLDER
    folder.mkdir()
    (folder / "trusted-root.json").write_bytes(canonical(envelope))
    (folder / "config.json").write_bytes(_public_json({"review": review}))
    (folder / "vool-commit").write_text(vool_commit + "\n")
    state = {
        "epoch": 0, "entries": [], "revoked": [], "targets": targets, "released": targets, "authorized_until": 0,
        "marks": {"sequence": 0, "tested": [], "highlighted": [], "suspended": [], "teams": {}}}
    (folder / "state.json").write_bytes(_public_json(state))
    (folder / "private.bin").write_bytes(seal(state_key, {"verdicts": {}, "attempts": {}, "reviews": {}, "spend": {},
                                                         "public_sha256": digest(state)}))
    (repo / ".gitattributes").write_text(FOLDER + "/private.bin binary\n")
    (repo / STEPS).write_bytes((TEMPLATES / STEPS).read_bytes())
    (repo / WORKFLOW).parent.mkdir(parents=True, exist_ok=True)
    (repo / WORKFLOW).write_bytes((TEMPLATES / "skill-codex.yml").read_bytes())
    if not (repo / "README.md").exists():
        (repo / "README.md").write_bytes((TEMPLATES / "README.md").read_bytes())
    return envelope


def gh_secret(github: str):
    """Sets one Actions secret through the GitHub CLI; the value goes in on stdin and is never shown."""
    def put(name: str, value: str) -> None:
        result = subprocess.run(["gh", "secret", "set", name, "--repo", github], input=value, text=True,
                                capture_output=True, timeout=120)
        require(result.returncode == 0, "gh could not set the " + name + " secret; sign gh in as an owner of " + github)
    return put


def main(argv=None, *, transport=None, clock=time.time, releases=None) -> int:
    parser = argparse.ArgumentParser(description="Skill Codex signer for GitHub Actions")
    parser.add_argument("command", choices=["setup", "run"])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--github", default="")
    parser.add_argument("--catalog-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--cap", type=float, default=20.0)
    parser.add_argument("--offline", type=Path)
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--vool-commit", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            require(args.offline is not None, "give a new folder for the main key with --offline")
            commit = args.vool_commit or subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse",
                                                         "HEAD"], capture_output=True, text=True).stdout.strip()
            targets = json.loads(args.targets.read_text()) if args.targets else released_targets(
                token=os.environ.get("GITHUB_TOKEN", ""))
            passphrase = getpass.getpass("Choose a passphrase for the main key: ")
            require(passphrase == getpass.getpass("Type it again: "), "the passphrases differ")
            setup(args.repo, github=args.github, catalog_key=args.catalog_key, targets=targets,
                  review={"provider": "openrouter", "model": args.model, "monthly_cap": args.cap}, passphrase=passphrase,
                  offline=args.offline, vool_commit=commit, now=int(time.time()), set_secret=gh_secret(args.github))
            print("Set up. GitHub now holds the signing keys. Next:\n"
                  f"1. Add the service address and the review key: gh secret set {SERVER} --repo {args.github}, then "
                  f"gh secret set OPENROUTER_API_KEY --repo {args.github} (each asks for its value).\n"
                  f"2. Commit and push everything in {args.repo}.\n"
                  f"3. Copy {FOLDER}/trusted-root.json to the service and into the app release.\n"
                  f"4. Keep {args.offline} (the main key) and its passphrase offline, for example on a USB stick.")
            return 0
        token = os.environ.get("GITHUB_TOKEN", "")
        repository = Repository(args.repo, token=token)
        repository.start()
        authority = RepoAuthority(args.repo, repository=repository, transport=transport, clock=clock,
                                  releases=releases or (lambda: released_targets(token=token)))
        try:
            report = authority.run_once()
            if report.get("stopped"):
                print("Stopped: " + report["stopped"])
                return 1
            print(json.dumps(quiet(report), sort_keys=True))
        except Superseded:
            print("Another signer run saved first; this run stopped before sending anything.")
        except (RegistryError, OSError) as exc:
            if not service_down(authority, exc):
                raise
            print("The service didn't answer; the next run tries again.")
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, RegistryError) else type(exc).__name__
        if message.startswith("the server refused:"):
            message = "the service refused a signed request; check its configuration and operator steps"
        if isinstance(exc, OSError) and exc.strerror:
            message += ": " + exc.strerror  # the system's reason, such as a read-only drive; never a path or a value
        print("Stopped: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
