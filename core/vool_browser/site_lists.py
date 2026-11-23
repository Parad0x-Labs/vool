"""Lists of harmful sites for VOOL's browser: downloaded signed, checked, kept, and asked.

The lists live in the public VOOL Browser repository. A copy of VOOL takes an update only
when every one of these holds, in this order:

1. the signature over the exact bytes of the update's index matches a release key that
   shipped inside this VOOL. The key is never taken from the network;
2. the index has the expected shape, and its serial is not lower than the one already held,
   so an old signed update cannot be replayed to bring an old list back;
3. every file the index names arrived with exactly the stated size and SHA-256.

Then the new files replace the old ones all at once. On any failure the lists already held
stay in force and the next try is a day later.

What is kept is checked the same way each time it is read, so files changed on disk while
VOOL was closed are not used.

An update can only add block entries. It cannot unblock a site, change a setting or run
anything, and it can never block the hosts in ``NEVER_BLOCKED`` (the place the lists come
from, and platforms that a single bad page must not take down).

Laws kept here:

- **Nothing is fetched until the Browser is switched on**, and nothing at all while VOOL holds
  no release key.
- **The fetch goes through VOOL's one outbound door**, so Private web carries it when on and
  refuses it when its route is down.
- **Everyone downloads the same whole files.** The request names no site anyone visits and
  carries no identifier.
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import ipaddress
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.runtime_paths import active_data_dir

#: Where a signed release is published. Only the maintainer moves this branch.
SOURCE = "https://raw.githubusercontent.com/Parad0x-Labs/vool-browser/published/"

#: Ed25519 public keys (base64, 32 bytes each) of the people who may sign a list release.
#: A release signed by anything else is refused. While this is empty no update is asked for
#: and no list is in force.
RELEASE_KEYS: tuple[str, ...] = ()

SCHEMA = "vool.browser.manifest.v1"
INDEX_PATH = "manifest/manifest.json"
SIGNATURE_PATH = "manifest/manifest.json.sig"
CHECK_EVERY_SECONDS = 24 * 60 * 60
RETRY_UNREACHABLE_SECONDS = 60 * 60
FETCH_TIMEOUT_SECONDS = 30.0

MAX_INDEX_BYTES = 256 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES = 64

#: Hosts no list may block, whoever signed it.
NEVER_BLOCKED = frozenset({
    "github.com", "githubusercontent.com", "raw.githubusercontent.com", "objects.githubusercontent.com",
    "google.com", "youtube.com", "gmail.com", "apple.com", "icloud.com", "microsoft.com",
    "live.com", "outlook.com", "office.com", "wikipedia.org", "wikimedia.org", "mozilla.org",
    "duckduckgo.com", "amazon.com", "cloudflare.com", "dropbox.com", "gitlab.com",
    "torproject.org", "archive.org", "stackoverflow.com", "python.org", "pypi.org",
})

_PLAIN_PATH = re.compile(r"(lists|plugins)/[a-z0-9][a-z0-9._-]{0,80}\.(txt|json)")
_LIST_PATH = re.compile(r"lists/[a-z0-9][a-z0-9._-]{0,80}\.txt")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")

_lock = threading.RLock()
_refreshing = threading.Lock()
_cache: tuple[Any, Held] | None = None


class ListRefused(Exception):
    """An update that is not taken. The text is a stable code, not a sentence."""


class Held:
    """The lists in force: one checked release, or nothing."""

    __slots__ = ("hosts", "serial")

    def __init__(self, serial: int = 0, hosts: frozenset[str] = frozenset()) -> None:
        self.serial = serial
        self.hosts = hosts


def _dir() -> Path:
    return active_data_dir() / "browser" / "lists"


def _held_dir() -> Path:
    return _dir() / "held"


def _state_path() -> Path:
    return _dir() / "state.json"


def _state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(**changes: Any) -> None:
    data = {**_state(), **changes}
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".new")
    temp.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


# -- what a host entry may be -------------------------------------------------------------

def host_allowed(host: str) -> bool:
    """A bare public host name that may be a block entry."""
    if not host or len(host) > 253 or host != host.strip().lower():
        return False
    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit() or any(not _LABEL.fullmatch(label) for label in labels):
        return False
    if host in NEVER_BLOCKED:
        return False
    # Neither a name under a protected host nor a name above one.
    return not any(host.endswith("." + kept) or kept.endswith("." + host) for kept in NEVER_BLOCKED)


def read_hosts(raw: bytes) -> list[str]:
    """The block entries of one list file. A line that may not be an entry is left out."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise ListRefused("file") from None
    return [line for line in text.splitlines() if line and not line.startswith("#") and host_allowed(line)]


# -- checking a release -------------------------------------------------------------------

def _key_bytes() -> list[bytes]:
    keys = []
    for text in RELEASE_KEYS:
        with contextlib.suppress(ValueError, binascii.Error):
            raw = base64.b64decode(str(text).strip(), validate=True)
            if len(raw) == 32:
                keys.append(raw)
    return keys


def signed(index: bytes, signature: bytes) -> bool:
    """True when *signature* is a release key's signature over exactly *index*."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = base64.b64decode(signature.decode("ascii").strip(), validate=True)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False
    if len(raw) != 64:
        return False
    for key in _key_bytes():
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(raw, index)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def checked(index: bytes, signature: bytes, read: Callable[[str, int], bytes], *, not_below: int = 0) -> tuple[int, dict[str, bytes]]:
    """Check one release. *read(path, most)* hands over a named file, at most *most* bytes.

    Returns (serial, path -> bytes) or raises ListRefused. Nothing is read before the
    signature holds.
    """
    if not signed(index, signature):
        raise ListRefused("signature")
    try:
        manifest = json.loads(index.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        raise ListRefused("shape") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ListRefused("shape")
    serial = manifest.get("serial")
    if type(serial) is not int or serial < 1:
        raise ListRefused("shape")
    if serial < not_below:
        raise ListRefused("older")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
        raise ListRefused("shape")
    files: dict[str, bytes] = {}
    for item in entries:
        path = item.get("path") if isinstance(item, dict) else None
        size, digest = (item.get("bytes"), item.get("sha256")) if isinstance(item, dict) else (None, None)
        if (not isinstance(path, str) or not _PLAIN_PATH.fullmatch(path) or path in files
                or type(size) is not int or not 0 <= size <= MAX_FILE_BYTES
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ListRefused("shape")
        try:
            raw = read(path, size)
        except ListRefused:
            raise
        except Exception:
            raise ListRefused("file") from None
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ListRefused("file")
        files[path] = raw
    return serial, files


def _hosts_of(files: dict[str, bytes]) -> frozenset[str]:
    found: set[str] = set()
    for path, raw in files.items():
        if _LIST_PATH.fullmatch(path):
            found.update(read_hosts(raw))
    return frozenset(found)


# -- the lists in force -------------------------------------------------------------------

def _read_held() -> Held:
    held = _held_dir()
    try:
        index = (held / INDEX_PATH).read_bytes()
        signature = (held / SIGNATURE_PATH).read_bytes()
    except OSError:
        return Held()

    def read(path: str, most: int) -> bytes:
        with (held / path).open("rb") as handle:
            return handle.read(most + 1)

    try:
        serial, files = checked(index, signature, read)
        return Held(serial, _hosts_of(files))
    except ListRefused:
        # Changed on disk, or signed by a key this VOOL no longer trusts: not used.
        return Held()


def held() -> Held:
    global _cache
    with _lock:
        index = _held_dir() / INDEX_PATH
        try:
            stat = index.stat()
            stamp: Any = (str(index), stat.st_mtime_ns, stat.st_size, RELEASE_KEYS)
        except OSError:
            stamp = (str(index), None, None, RELEASE_KEYS)
        if _cache is None or _cache[0] != stamp:
            _cache = (stamp, _read_held())
        return _cache[1]


def hosts() -> frozenset[str]:
    return held().hosts


def listed(host: str) -> bool:
    """True when *host*, or a name it sits under, is a block entry."""
    entries = hosts()
    if not entries:
        return False
    name = str(host or "").strip().strip("[]").lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        pass
    with contextlib.suppress(UnicodeError):
        name = name.encode("idna").decode("ascii") if not name.isascii() else name
    labels = name.split(".")
    return any(".".join(labels[start:]) in entries for start in range(len(labels) - 1))


def listed_url(url: str) -> bool:
    try:
        return listed(urlsplit(str(url or "")).hostname or "")
    except ValueError:
        return False


# -- taking an update ---------------------------------------------------------------------

def _fetch(path: str, most: int) -> bytes:
    from core.remote_fetch_policy import open_remote_url

    with open_remote_url(SOURCE + path, headers={"User-Agent": "vool", "Accept": "*/*"},
                         timeout=FETCH_TIMEOUT_SECONDS, redirect_policy="refuse") as answer:
        raw = answer.read(most + 1)
    if len(raw) > most:
        raise ListRefused("file")
    return raw


def _keep(index: bytes, signature: bytes, files: dict[str, bytes]) -> None:
    """Swap the checked release in, all at once."""
    global _cache
    base = _dir()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    fresh, old = base / "held.new", base / "held.old"
    shutil.rmtree(fresh, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    for path, raw in {INDEX_PATH: index, SIGNATURE_PATH: signature, **files}.items():
        target = fresh / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(raw)
        target.chmod(0o600)
    with _lock:
        current = _held_dir()
        if current.exists():
            current.rename(old)
        fresh.rename(current)
        _cache = None
    shutil.rmtree(old, ignore_errors=True)


def due(now: float | None = None) -> bool:
    last = _state().get("checked")
    moment = time.time() if now is None else now
    return not isinstance(last, (int, float)) or moment - last >= CHECK_EVERY_SECONDS or moment < last


def refresh(*, force: bool = False) -> str:
    """Ask for today's lists when it is time. Returns what happened, as a stable word:

    ``no_key`` (this VOOL trusts no release key: nothing is asked), ``off`` (the Browser is
    not switched on), ``not_due``, ``busy``, ``unreachable``, ``refused``, ``unchanged`` or
    ``updated``.
    """
    if not _key_bytes():
        return "no_key"
    from . import live

    if not live.available():
        return "off"
    if not force and not due():
        return "not_due"
    if not _refreshing.acquire(blocking=False):
        return "busy"
    try:
        result = _refresh_once()
        # A release that was refused is asked for again a day later. A source that could not
        # be reached (no connection just then) is asked again within the hour.
        waited = CHECK_EVERY_SECONDS - RETRY_UNREACHABLE_SECONDS if result == "unreachable" else 0
        _write_state(checked=time.time() - waited, result=result)
        return result
    finally:
        _refreshing.release()


def _refresh_once() -> str:
    from core.effect_gateway import named_background_effect_scope

    have = held()
    floor = max(have.serial, int(_state().get("serial") or 0))
    with named_background_effect_scope("browser.site_lists"):
        try:
            index = _fetch(INDEX_PATH, MAX_INDEX_BYTES)
            signature = _fetch(SIGNATURE_PATH, 4096)
        except ListRefused:
            return "refused"
        except Exception:
            return "unreachable"
        if have.serial and index == _held_bytes(INDEX_PATH):
            return "unchanged"
        try:
            serial, files = checked(index, signature, _fetch, not_below=floor)
        except ListRefused:
            return "refused"
    if serial == have.serial:
        # The same serial with other contents is not a newer release.
        return "refused"
    _keep(index, signature, files)
    _write_state(serial=serial)
    return "updated"


def _held_bytes(path: str) -> bytes:
    try:
        return (_held_dir() / path).read_bytes()
    except OSError:
        return b""


def refresh_in_background(done: Callable[[str], None] | None = None) -> None:
    """Ask for an update without holding anything up. *done* hears what happened."""
    if not _key_bytes() or not due():
        return

    def run() -> None:
        result = "unreachable"
        with contextlib.suppress(Exception):
            result = refresh()
        if done is not None:
            with contextlib.suppress(Exception):
                done(result)

    threading.Thread(target=run, name="vool-site-lists", daemon=True).start()


def status() -> dict[str, Any]:
    """What the lists are right now, for the person."""
    have = held()
    state = _state()
    return {"on": bool(have.hosts), "sites": len(have.hosts), "serial": have.serial,
            "checked": state.get("checked") if isinstance(state.get("checked"), (int, float)) else None}


__all__ = [
    "RELEASE_KEYS",
    "SOURCE",
    "ListRefused",
    "checked",
    "due",
    "held",
    "host_allowed",
    "hosts",
    "listed",
    "listed_url",
    "read_hosts",
    "refresh",
    "refresh_in_background",
    "signed",
    "status",
]
