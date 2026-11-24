"""The two team badges, issued by hand: "Tested by VOOL" and "Editor's Choice".

    python -m registry_service.badges keygen  --out FILE
    python -m registry_service.badges issuers --root-key root.pem --trusted-root trusted-root.json \\
        --key PUBLIC-LINE --key PUBLIC-LINE [--key ...] --out badge-issuers.json [--previous badge-issuers.json]
    python -m registry_service.badges propose --file badges.txt --issuers badge-issuers.json --state authority/state.json \\
        --by KEY-ID --by KEY-ID --out badges.json [--previous badges.json] [--renew NAME ...]
    python -m registry_service.badges propose --retest VOOL-VERSION --previous badges.json
    python -m registry_service.badges sign    --key FILE badges.json [--issuers badge-issuers.json]
    python -m registry_service.badges check   badges.json --issuers badge-issuers.json --trusted-root trusted-root.json

The owner holds the main (root) key offline and uses it here only to sign the list of people he
appoints (`issuers`). Each appointed person makes a key of their own (`keygen`), protected by a
passphrase, and keeps it on their own computer. A badge document (`propose`) counts once two of them
have signed it (`sign`), each on their own computer. The signer job and the service only pass the
signed file on: no badge key is ever in a workflow, an environment variable or on the server.

badges.txt, one badge per line (a line starting with # is a note):

    tested publisher/name 1.2.0 0.8.0 macos-arm64,macos-x86_64 evidence.json
    editors-choice publisher/name 1.2.0 "why it was picked"
    tested plugin:plugin-id 1.0.0 sha256:<64 hex> 0.8.0 macos-arm64 evidence.json
    editors-choice bundled:skill-id 1.0.0 sha256:<64 hex> "why it was picked"
    withdrawn <64 hex>

A badge holds for the exact version and checksum on its line, and runs out: Tested by VOOL after 180
days, Editor's Choice after 365. Nothing renews by itself.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import shlex
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from core.registry import badges
from core.registry.codec import HEX, VERSION, RegistryError, canonical, decode, digest, require
from core.registry.publisher import add_signature
from core.registry.trust import root_document, verify

LABELS = {"tested": "Tested by VOOL", "editors_choice": "Editor's Choice"}
_WORDS = {"tested": "tested", "editors-choice": "editors_choice"}
_FORMS = ('tested publisher/name VERSION VOOL-VERSION[,VOOL-VERSION] PLATFORM[,PLATFORM] EVIDENCE-FILE ["note"]',
          'editors-choice publisher/name VERSION "why it was picked"', "withdrawn <64 hex checksum>")


def _public(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _keyid(key: Ed25519PrivateKey) -> str:
    return digest({"ed25519": _public(key)})


def _put(path: Path, data: bytes) -> None:
    """Signed, public files: written whole or not at all."""
    temporary = path.with_name(path.name + ".new")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _unlock(path: Path) -> Ed25519PrivateKey:
    """A person's key from its file. The passphrase is typed, never passed on the command line or in a variable."""
    try:
        pem = path.read_bytes()
    except OSError:
        raise RegistryError(f"the key file {path.name} cannot be read") from None
    require(b"ENCRYPTED PRIVATE KEY" in pem and len(pem) <= 16384,
            f"{path.name} is not a passphrase-protected key; make one with keygen")
    try:
        key = load_pem_private_key(pem, password=getpass.getpass(f"Passphrase for {path.name}: ").encode())
    except (ValueError, TypeError):
        raise RegistryError("that passphrase does not open the key") from None
    require(isinstance(key, Ed25519PrivateKey), f"{path.name} is not a badge key")
    return key


def _read(path: Path, limit: int = badges.MAX_BYTES):
    try:
        return decode(path.read_bytes(), limit)
    except OSError:
        raise RegistryError(f"{path.name} cannot be read") from None


def _day(moment: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(moment))


# ---- keygen ----

def keygen(out: Path) -> str:
    require(not out.exists(), f"{out.name} already exists; a key is made once")
    passphrase = getpass.getpass("Choose a passphrase for your badge key: ")
    require(len(passphrase) >= 12, "the passphrase needs at least 12 characters")
    require(passphrase == getpass.getpass("Type it again: "), "the passphrases differ")
    key = Ed25519PrivateKey.generate()
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase.encode())))
    return (f"Made {out.name}. Keep it and its passphrase on your own computer only.\n"
            f"Public line (send this to the owner): {_public(key)}\n"
            f"Key id: {_keyid(key)}")


# ---- issuers ----

def make_issuers(args, now: int) -> str:
    root_envelope = _read(args.trusted_root, 65536)
    root = root_document(root_envelope.get("signed") if isinstance(root_envelope, dict) else None, now)
    verify(root_envelope, root, "root")
    require(len(args.key) >= 2, "name at least two people's public lines with --key; a badge always takes two")
    version = 1
    if args.previous:
        version = badges.issuer_list(_read(args.previous, 65536).get("signed"), now=now, allow_expired=True)["version"] + 1
    envelope = badges.new_issuers(args.key, version=version, now=now, lifetime=args.days * 86400)
    key = _unlock(args.root_key)
    require(_keyid(key) in root["roles"]["root"]["keyids"], "that is not the main (root) key of this trust root")
    envelope = add_signature(envelope, key)
    lines = [f"Issuer list {version}, good until {_day(envelope['signed']['expires'])}. Appointed keys:"]
    lines += ["  " + keyid for keyid in envelope["signed"]["keys"]]
    try:
        badges.issuers(envelope, root, now=now)
        lines.append(f"Signed with the main key and written to {args.out.name}.")
    except RegistryError as exc:
        require(str(exc) == "signature threshold not met", str(exc))
        lines.append(f"Written to {args.out.name} with one main-key signature. This trust root needs more: add each with "
                     "python -m core.registry.publisher FILE --encrypted-key KEY --output NEW-FILE")
    _put(args.out, canonical(envelope))
    return "\n".join(lines)


# ---- propose ----

def _signers(named: list[str], issuer: dict) -> list[str]:
    require(len(named) == 2, "name the two people who will sign with --by KEY-ID --by KEY-ID")
    found = []
    for name in named:
        matches = [keyid for keyid in issuer["keys"] if len(name) >= 8 and keyid.startswith(name.lower())]
        require(len(matches) == 1, f"--by {name} is not one key id on the issuer list (give at least its first 8 characters)")
        found.append(matches[0])
    require(found[0] != found[1], "--by names the same person twice; a badge takes two different people")
    return found


def _listed(state: dict | None, package_id: str, version: str) -> tuple[str, str]:
    """(kind, checksum) of a listed version, from the signer's own state file."""
    require(state is not None, "give the signer's authority/state.json with --state, so the checksum is the listed one")
    for envelope in state.get("entries", []):
        entry = envelope.get("signed", {})
        info = entry.get("metadata", {})
        if (info.get("id"), info.get("version")) == (package_id, version):
            return info["kind"], entry["artifact_sha256"]
    raise RegistryError(f"{package_id} {version} is not in the Skill Codex list")


def read_lines(text: str, folder: Path, state: dict | None) -> tuple[list[dict], list[str]]:
    """(wanted badges without dates or signers, withdrawn checksums) from badges.txt."""
    wanted, withdrawn = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            try:
                words = shlex.split(raw)
            except ValueError:
                raise RegistryError("a quote is not closed") from None
            if words[0] == "withdrawn":
                require(len(words) == 2 and bool(HEX.fullmatch(words[1])), "write it as: " + _FORMS[2])
                withdrawn.append(words[1])
                continue
            require(words[0] in _WORDS, "a line starts with tested, editors-choice or withdrawn")
            badge = _WORDS[words[0]]
            require(len(words) >= 3, "write it as: " + _FORMS[0 if badge == "tested" else 1])
            subject, version, rest = words[1], words[2], words[3:]
            require(bool(VERSION.fullmatch(version)), f"{version} is not a version such as 1.2.0")
            if subject.startswith(("plugin:", "bundled:")):
                kind, digest_type = ("plugin", "package") if subject.startswith("plugin:") else ("skill", "skill-file")
                package_id = subject.split(":", 1)[1]
                require(bool(rest) and rest[0].startswith("sha256:") and bool(HEX.fullmatch(rest[0][7:])),
                        "after the version, give the checksum as sha256:<64 hex>")
                sha256, rest = rest[0][7:], rest[1:]
            else:
                package_id, digest_type = subject, "artifact"
                kind, sha256 = _listed(state, package_id, version)
            if badge == "tested":
                require(len(rest) in (3, 4), "write it as: " + _FORMS[0])
                targets = [{"version": built, "platform": platform} for built in rest[0].split(",")
                           for platform in rest[1].split(",")]
                try:
                    evidence = hashlib.sha256((folder / rest[2]).read_bytes()).hexdigest()
                except OSError:
                    raise RegistryError(f"the test record {rest[2]} cannot be read") from None
                note = rest[3] if len(rest) == 4 else ""
            else:
                require(len(rest) == 1 and bool(rest[0].strip()), "write it as: " + _FORMS[1])
                targets, note = [], rest[0]
                evidence = hashlib.sha256(note.encode()).hexdigest()
            wanted.append({"badge": badge, "kind": kind, "id": package_id, "version": version, "digest_type": digest_type,
                           "sha256": sha256, "targets": targets, "evidence_sha256": evidence, "note": note})
        except RegistryError as exc:
            raise RegistryError(f"badges.txt line {number}: {exc}") from None
    return wanted, withdrawn


def propose(args, now: int) -> str:
    issuer = badges.issuer_list(_read(args.issuers, 65536).get("signed"), now=now)
    previous = None
    if args.previous:
        previous = badges.verify_document(_read(args.previous), issuer, now=now, allow_expired=True)
    if args.retest:
        require(previous is not None, "give the current badges.json with --previous")
        waiting = [item for item in previous["badges"] if item["badge"] == "tested" and now < item["expires"] and
                   args.retest not in {target["version"] for target in item["targets"]}]
        lines = [f"{item['id']} {item['version']} (tested on {', '.join(sorted({t['version'] for t in item['targets']}))})"
                 for item in waiting]
        return "\n".join([f"Not yet tested on VOOL {args.retest}: {len(waiting)}", *lines, "Nothing was changed." + (
            " Test each one, add the version to its line in badges.txt, then propose." if waiting else "")])
    require(args.file is not None and args.out is not None, "give --file badges.txt and --out badges.json")
    signers = _signers(args.by, issuer)
    state = _read(args.state, 16 * 1024 * 1024) if args.state else None
    try:
        text = args.file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise RegistryError(f"{args.file.name} cannot be read") from None
    wanted, withdrawn = read_lines(text, args.file.parent, state)
    revoked = sorted(set(withdrawn) | set(previous["revoked"] if previous else []))
    before = {(item["badge"], item["digest_type"], item["sha256"]): item for item in (previous["badges"] if previous else [])}
    entries, new, kept = [], [], []
    for item in wanted:
        require(item["sha256"] not in revoked, f"{item['id']} {item['version']} was withdrawn; a withdrawn checksum never "
                "carries a badge again. Take its line out, or publish a new version")
        old = before.get((item["badge"], item["digest_type"], item["sha256"]))
        same = old is not None and all(old[key] == value for key, value in item.items())
        if same and item["id"] not in args.renew:
            require(now < old["expires"], f"the {LABELS[item['badge']]} badge of {item['id']} {item['version']} ran out on "
                    f"{_day(old['expires'])}. Add --renew {item['id']} to issue it again, or take its line out")
            entries.append(old)
            kept.append(item)
        else:
            entries.append({**item, "issued_at": now, "expires": now + badges.ENTRY_SECONDS[item["badge"]],
                            "issued_by": signers})
            new.append(item)
    listed = {(item["badge"], item["digest_type"], item["sha256"]) for item in wanted}
    dropped = [item for key, item in before.items() if key not in listed]
    envelope = badges.new_document(sequence=previous["sequence"] + 1 if previous else 1, previous=previous, entries=entries,
                                   revoked=revoked, team_releases=previous["team_releases"] if previous else [], now=now)
    _put(args.out, canonical(envelope))
    lines = [f"Badge document {envelope['signed']['sequence']} written to {args.out.name}, unsigned. "
             f"It runs out on {_day(envelope['signed']['expires'])}."]
    lines += [f"  new: {LABELS[item['badge']]}  {item['id']} {item['version']}" for item in new]
    lines += [f"  kept: {LABELS[item['badge']]}  {item['id']} {item['version']}" for item in kept]
    lines += [f"  dropped: {LABELS[item['badge']]}  {item['id']} {item['version']}" for item in dropped]
    lines += [f"  withdrawn: {item}" for item in withdrawn]
    lines.append("Next: each of the two people named with --by runs `sign --key THEIR-KEY " + args.out.name +
                 "` on their own computer.")
    return "\n".join(lines)


# ---- sign and check ----

def sign(args, now: int) -> str:
    envelope = _read(args.document)
    issuer = badges.issuer_list(_read(args.issuers or args.document.with_name(badges.SHIPPED[1]), 65536).get("signed"), now=now)
    require(isinstance(envelope, dict) and set(envelope) == {"signed", "signatures"}, f"{args.document.name} is not a badge document")
    signed = badges.document(envelope["signed"], now=now)
    key = _unlock(args.key)
    require(_keyid(key) in issuer["keys"], "this key is not on the issuer list, so its signature would count for nothing")
    envelope = add_signature(envelope, key)
    count = len({item["keyid"] for item in envelope["signatures"]} & set(issuer["keys"]))
    _put(args.document, canonical(envelope))
    if count < issuer["threshold"]:
        return (f"Signed badge document {signed['sequence']}: {count} of {issuer['threshold']} signatures. "
                "Send the file to the second person to sign.")
    badges.verify_document(envelope, issuer, now=now)
    return (f"Signed badge document {signed['sequence']}: {count} of {issuer['threshold']} signatures. It is complete. "
            "Run check, then put it in the signer repository as badges/badges.json through a pull request.")


def check(args, now: int) -> str:
    root_envelope = _read(args.trusted_root, 65536)
    root = root_document(root_envelope.get("signed") if isinstance(root_envelope, dict) else None, now)
    verify(root_envelope, root, "root")
    issuer = badges.issuers(_read(args.issuers, 65536), root, now=now)
    envelope = _read(args.document)
    require(isinstance(envelope, dict) and isinstance(envelope.get("signatures"), list), f"{args.document.name} is not a badge document")
    count = len({item.get("keyid") for item in envelope["signatures"] if isinstance(item, dict)} & set(issuer["keys"]))
    require(count >= issuer["threshold"], f"{count} of {issuer['threshold']} appointed people have signed this document. "
            f"Each one runs: sign --key THEIR-KEY {args.document.name}")
    signed = badges.verify_document(envelope, issuer, now=now)
    lines = [f"Badge document {signed['sequence']} holds: two appointed people signed it. It runs out on {_day(signed['expires'])}."]
    for item in signed["badges"]:
        left = (item["expires"] - now) // 86400
        state = f"{left} days left" if left >= 0 else "ran out"
        if not set(item["issued_by"]) <= set(issuer["keys"]):
            state = "not shown: one of its signers is no longer appointed"
        built = " on VOOL " + ", ".join(sorted({target["version"] for target in item["targets"]})) if item["targets"] else ""
        lines.append(f"  {LABELS[item['badge']]}  {item['id']} {item['version']}{built}  ({state})")
    lines.append(f"  withdrawn checksums: {len(signed['revoked'])}")
    return "\n".join(lines)


def main(argv=None, *, clock=time.time) -> int:
    parser = argparse.ArgumentParser(description="Issue the two team badges. Each person signs on their own computer.")
    parser.add_argument("command", choices=["keygen", "issuers", "propose", "sign", "check"])
    parser.add_argument("document", nargs="?", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--key", action="append", default=[])
    parser.add_argument("--root-key", type=Path)
    parser.add_argument("--trusted-root", type=Path)
    parser.add_argument("--issuers", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--by", action="append", default=[])
    parser.add_argument("--renew", action="append", default=[])
    parser.add_argument("--retest", default="")
    args = parser.parse_args(argv)
    now = int(clock())
    try:
        if args.command == "keygen":
            require(args.out is not None, "give the new key file with --out")
            print(keygen(args.out))
        elif args.command == "issuers":
            require(args.root_key is not None and args.trusted_root is not None and args.out is not None,
                    "give --root-key, --trusted-root and --out")
            require(0 < args.days <= 730, "--days is between 1 and 730")
            print(make_issuers(args, now))
        elif args.command == "propose":
            require(args.issuers is not None, "give the issuer list with --issuers")
            print(propose(args, now))
        elif args.command == "sign":
            require(args.document is not None and len(args.key) == 1, "give the badge document and one --key FILE")
            args.key = Path(args.key[0])
            print(sign(args, now))
        else:
            require(args.document is not None and args.issuers is not None and args.trusted_root is not None,
                    "give the badge document, --issuers and --trusted-root")
            print(check(args, now))
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print("Refused: " + (str(exc) if isinstance(exc, RegistryError) else type(exc).__name__))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
