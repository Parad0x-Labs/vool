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
  run's change to the state stops, so no list number or marks number is ever signed twice.
- The team's hand steps are lines in team-steps.txt, edited on GitHub behind its sign-in.
- Once a day every listed skill goes through today's checks again, and the release targets follow
  VOOL's published releases.

The main (root) key stays offline; it is only needed to change these keys.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import os
import re
import subprocess
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
from registry_service.mac_authority import ROOT_SECONDS, Authority, _exclusive, _public, _raw_key, _write

FOLDER = "authority"
STEPS = "team-steps.txt"
WORKFLOW = ".github/workflows/skill-codex.yml"
TEMPLATES = Path(__file__).parent / "github"
SECRETS = {"admission": "SKILL_CODEX_ADMISSION_KEY", "review": "SKILL_CODEX_REVIEW_KEY", "state": "SKILL_CODEX_STATE_KEY"}
SERVER = "SKILL_CODEX_SERVER"
# Kept sealed: a refusal's reasons can quote the refused skill, and counts per creator say who uploaded.
PRIVATE = ("verdicts", "attempts", "reviews")
_SEALED = b"SCP1"
_AAD = b"skill-codex private state v1"
BOT = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
VOOL = "Parad0x-Labs/vool"
# The builds VOOL reports for itself (core.registry.client.runtime_target); installing needs a released Mac build.
PLATFORMS = ("macos-arm64", "macos-x86_64")
RELEASES_FOLLOWED = 3
# Stay quiet about a down service while the signed list still has this long to run.
QUIET_WHILE = 2 * 86400
REMOVED = "This skill was taken off the Skill Codex. Ask the team before you publish it again."
_STEP_FORMS = {"tested": "tested publisher/name version", "highlighted": "highlighted publisher/name",
               "suspended": "suspended publisher", "removed": "removed publisher/name",
               "team": "team name github-account-number [github-account-number ...]"}


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

class Repository:
    """The checkout the workflow made. The signer's state goes back to GitHub before anything is sent."""

    def __init__(self, path: Path, *, branch: str = "main"):
        self.path, self.branch = Path(path), branch

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", "-C", str(self.path), "-c", "user.name=" + BOT[0], "-c", "user.email=" + BOT[1],
                                 "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, timeout=120)
        if check and result.returncode:
            raise RegistryError("git " + args[0] + " failed: " + " ".join(result.stderr.split())[:300])
        return result

    def start(self) -> None:
        """Begin from the newest state on GitHub; a run queued behind another must not start from older state."""
        self._git("fetch", "-q", "origin", self.branch)
        self._git("merge", "-q", "--ff-only", "FETCH_HEAD")

    def save(self, message: str) -> bool:
        """Commit and push the state folder; False when nothing changed."""
        self._git("add", "--", FOLDER)
        if self._git("diff", "--cached", "--quiet", "--", FOLDER, check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        for _ in range(3):
            if self._git("push", "-q", "origin", "HEAD:refs/heads/" + self.branch, check=False).returncode == 0:
                return True
            self._git("fetch", "-q", "origin", self.branch)
            # A state change from elsewhere means two signers ran at once: stop before anything is sent.
            if self._git("diff", "--name-only", "HEAD...FETCH_HEAD", "--", FOLDER).stdout.strip():
                raise RegistryError("another signer run changed the state meanwhile; this run stops before sending anything")
            self._git("rebase", "-q", "FETCH_HEAD")  # only the team's own files moved, team-steps.txt say
        raise RegistryError("the signer's state could not be pushed to GitHub")


# ---- the team's hand steps ----

def _skill(value: str) -> bool:
    parts = value.split("/")
    return len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts)


def parse_steps(text: str) -> dict:
    """team-steps.txt line by line. A line that doesn't read cleanly stops every step, so nothing half-applies."""
    require(len(text) <= 512 * 1024, STEPS + " is too long")
    wanted = {"tested": [], "highlighted": [], "suspended": [], "removed": [], "teams": {}}
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        step, args = words[0].lower(), words[1:]
        require(step in _STEP_FORMS, f"{STEPS} line {number}: \"{words[0][:40]}\" is not a step; use tested, "
                                     "highlighted, suspended, removed or team")
        form = f"{STEPS} line {number}: write it as \"{_STEP_FORMS[step]}\""
        if step == "tested":
            require(len(args) == 2 and _skill(args[0]) and bool(VERSION.fullmatch(args[1])), form)
            wanted["tested"].append((args[0], args[1]))
        elif step in {"highlighted", "removed"}:
            require(len(args) == 1 and _skill(args[0]), form)
            wanted[step].append(args[0])
        elif step == "suspended":
            require(len(args) == 1 and bool(IDENTITY.fullmatch(args[0])), form)
            wanted["suspended"].append(args[0])
        else:
            require(len(args) >= 2 and bool(IDENTITY.fullmatch(args[0])) and args[0] not in wanted["teams"] and
                    len(args) <= 65 and all(re.fullmatch(r"[0-9]{1,20}", member) for member in args[1:]), form)
            wanted["teams"][args[0]] = sorted(set(args[1:]))
    require(len(wanted["teams"]) <= 16, STEPS + ": at most 16 teams")
    return wanted


# ---- VOOL's releases ----

def _github(token: str):
    def get(path: str):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "skill-codex-signer"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request("https://api.github.com" + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read(4 * 1024 * 1024))
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
        public = json.loads((self.repo / FOLDER / "state.json").read_text())
        private = unseal(self._state_key, (self.repo / FOLDER / "private.bin").read_bytes())
        self._sealed = json.dumps(private, sort_keys=True)
        spend = private.pop("spend", {})
        require(isinstance(public, dict) and set(private) <= set(PRIVATE) and not set(public) & set(PRIVATE),
                "the signer's state files are mixed up")
        _write(self.dir / "spend.json", json.dumps(spend, sort_keys=True).encode())
        return {**public, **private}

    def save(self) -> None:
        private = {key: self.state[key] for key in PRIVATE if key in self.state}
        try:
            private["spend"] = json.loads((self.dir / "spend.json").read_text())
        except (OSError, ValueError):
            private["spend"] = {}
        _put(self.repo / FOLDER / "state.json", _public_json({key: value for key, value in self.state.items()
                                                              if key not in PRIVATE}))
        plain = json.dumps(private, sort_keys=True)
        if plain != self._sealed:  # sealing again only on a change keeps the history quiet
            _write(self.repo / FOLDER / "private.bin", seal(self._state_key, private))
            self._sealed = plain

    def checkpoint(self, message: str = "Skill Codex: state before sending") -> None:
        self.save()
        self.repository.save(message)

    def call(self, method: str, path: str, value=None, *, raw: bool = False):
        # What is about to be signed and sent is on GitHub first, so a lost run never sends it differently. A refusal
        # needs no such care: the service drops the upload, and the next run repeats nothing.
        if method != "GET" and not (isinstance(value, dict) and "refused" in value):
            self.checkpoint()
        return super().call(method, path, value, raw=raw)

    def _store(self, raw: bytes) -> None:
        pass  # the service keeps the exact bytes; _artifact fetches and checks them

    def _artifact(self, sha256: str) -> bytes:
        raw = self.call("GET", "/authority/artifacts/" + sha256, raw=True)
        require(hashlib.sha256(raw).hexdigest() == sha256, "the server sent different bytes")
        return raw

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.now()))

    def _marks(self) -> dict:
        # The number is used up before sending, as the list's epoch is: after a lost answer the next try
        # sends a higher one, which the service accepts, instead of the same one, which it refuses forever.
        marks = self.state["marks"]
        marks["sequence"] += 1
        self.state["marks_unsent"] = True
        value = marks_document({"schema": MARKS_SCHEMA, "sequence": marks["sequence"], "issued_at": self.now(),
                                **{key: marks[key] for key in ("tested", "highlighted", "suspended", "teams")}},
                               now=self.now())
        self.call("POST", "/authority/marks", add_signature({"signed": value, "signatures": []}, self.admission_key))
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

    def team_steps(self) -> dict:
        """Bring the marks and removals in line with team-steps.txt. A line that can't apply yet is reported."""
        path = self.repo / STEPS
        try:
            wanted = parse_steps(path.read_text(encoding="utf-8") if path.exists() else "")
        except (RegistryError, UnicodeDecodeError) as exc:
            # Nothing applies, and the last removals keep holding while the file is being fixed.
            self._removed = set(self.state.get("removed", []))
            return {"applied": False, "problems": [str(exc) if isinstance(exc, RegistryError) else STEPS + " is not plain text"]}
        problems = []
        self._removed = set(wanted["removed"])
        self.state["removed"] = sorted(self._removed)
        gone = [envelope for envelope in self.state["entries"] if envelope["signed"]["metadata"]["id"] in self._removed]
        if gone:
            self.state["entries"] = [envelope for envelope in self.state["entries"] if envelope not in gone]
            self.state["revoked"] = sorted(set(self.state["revoked"]) | {item["signed"]["artifact_sha256"] for item in gone})
            self.state["unpublished"] = True
            self.save()
        for sha, item in sorted(self.state.get("pending", {}).items()):
            if item["admission"]["signed"]["metadata"]["id"] in self._removed:
                del self.state["pending"][sha]
                with contextlib.suppress(RegistryError):  # gone from the service already: it is not listed either way
                    self.call("POST", "/authority/submissions/" + item["id"], {"sha256": sha, "refused": [
                        {"check": "source", "path": "package", "reason": REMOVED}]})
        listed: dict[str, dict[str, str]] = {}
        for envelope in self.state["entries"]:
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
        if self.state.get("marks_unsent") or any(marks[key] != value for key, value in new.items()):
            marks.update(new)
            self._marks()
        return {"applied": True, "problems": problems}

    def follow_releases(self) -> bool:
        """Once a day: sign for the newest published VOOL releases. True when the builds changed."""
        if self._releases is None or self.state.get("releases_checked") == self._today():
            return False
        targets = self._releases()
        require(validate_policy(checks.policy(targets)) in self.root["policies"], "the release list does not fit the trust root")
        self.state["releases_checked"] = self._today()
        changed = targets != self.state["targets"]
        self.state["targets"] = targets  # _renew signs every listed skill again for these builds
        self.save()
        return changed

    def recheck(self) -> list[str]:
        """Once a day: today's checks on every listed skill. One that fails them leaves the list."""
        if self.state.get("rechecked") == self._today():
            return []
        kept, dropped = [], []
        for envelope in self.state["entries"]:
            entry = envelope["signed"]
            raw = self._artifact(entry["artifact_sha256"])
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
        report = {"steps": self.team_steps(), "dropped": []}
        try:
            report["releases_changed"] = self.follow_releases()
        except RegistryError as exc:
            report["releases_waiting"] = str(exc)  # GitHub is down: the next run asks again
        try:
            report["dropped"] = self.recheck()
        except RegistryError as exc:
            report["recheck_waiting"] = str(exc)  # the service is down: the next run looks again
        try:
            report.update(super().run_once())
        except BaseException:
            with contextlib.suppress(Exception):
                self.checkpoint("Skill Codex: state after a stopped run")  # keep what was spent and decided
            raise
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
            **{key: report[key] for key in ("releases_waiting", "recheck_waiting") if key in report}}


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
    (folder / "state.json").write_bytes(_public_json({
        "epoch": 0, "entries": [], "revoked": [], "targets": targets, "authorized_until": 0,
        "marks": {"sequence": 0, "tested": [], "highlighted": [], "suspended": [], "teams": {}}}))
    (folder / "private.bin").write_bytes(seal(state_key, {"verdicts": {}, "attempts": {}, "reviews": {}, "spend": {}}))
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


def main(argv=None, *, transport=None, clock=time.time) -> int:
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
        repository = Repository(args.repo)
        repository.start()
        token = os.environ.get("GITHUB_TOKEN", "")
        authority = RepoAuthority(args.repo, repository=repository, releases=lambda: released_targets(token=token),
                                  transport=transport, clock=clock)
        try:
            print(json.dumps(quiet(authority.run_once()), sort_keys=True))
        except (RegistryError, OSError) as exc:
            if not service_down(authority, exc):
                raise
            print("The service didn't answer; the next run tries again.")
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Stopped: " + (str(exc) if isinstance(exc, RegistryError) else type(exc).__name__))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
