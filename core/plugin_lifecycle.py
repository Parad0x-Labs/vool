"""The plugin lifecycle, as durable state rather than as presence on disk.

Before this module, a plugin's whole lifecycle was: a directory exists under the plugins root,
one global flag is on, and the plugin id is absent from a deny-list. Presence WAS installation,
installation WAS availability, and the only subtraction was an opt-out store that failed OPEN --
a corrupt or truncated file re-enabled everything.

The stages this module makes real, each a separate fact with its own evidence:

    discover -> inspect -> install -> verify -> enable -> invoke -> update -> revoke -> uninstall

and the law that separates them:

    **installed never means available, permitted or invoked.**

`is_available` is the one predicate the tool-offer path consults, and it is a conjunction: the
plugin must be installed, verified against the digest it was verified AT, enabled, and not
revoked. A manifest edited after verification fails the digest check and drops out of the offer
until it is re-verified -- an update is a lifecycle event, not a silent reload.

Two further laws:

* **plugins and skills cannot grant authority.** `authority_floor` returns the permission
  actions a declared side-effect class implies no matter what the manifest says. The permission
  controller unions the floor with the declaration, so a manifest can be MORE specific and can
  never be cheaper than its own declared class -- the union is safe because the mode matrix takes
  the strictest effect over the action set.
* **credentials stay opaque.** A plugin record may name credential BINDINGS. It has no field that
  can hold a secret, `credential_bindings` returns handles only, and nothing here reads a value.

The store is atomic (mkstemp + os.replace, 0600) and fails CLOSED: an unreadable or malformed
store means nothing is installed, so nothing is offered. The previous store's failure mode was
the opposite, and the opposite of fail-closed on a permission surface is a vulnerability.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_FILENAME = "plugin_lifecycle.json"
SCHEMA = "vool.plugin_lifecycle.v1"

STAGE_DISCOVERED = "discovered"
STAGE_INSPECTED = "inspected"
STAGE_INSTALLED = "installed"
STAGE_VERIFIED = "verified"
STAGE_ENABLED = "enabled"
STAGE_REVOKED = "revoked"
STAGE_UNINSTALLED = "uninstalled"

#: The permission actions a side-effect class implies REGARDLESS of what a manifest declares.
#:
#: This is a FLOOR, not a ceiling. `core.plugin_tools.validate_permission_actions` already bounds
#: what may be DECLARED; this bounds what may be OMITTED. Each entry names the cheapest row a
#: class must not be able to sit in:
#:
#: * a `network_send` tool declaring only `use_network_access` would sit in Auto's ALLOW row while
#:   sending messages, which Auto PROMPTS for. The floor keeps `external_messages` on it;
#: * a `sandbox_command` tool declaring only `run_safe_commands` would sit in every mode's read
#:   row while running side-effecting commands;
#: * a spending class cannot be anything but a financial action.
#:
#: `workspace_write` is deliberately NOT floored to a blanket action. Its real hole is narrower and
#: is closed precisely by `WRITE_CLASSES` below: a declaration skips the argument-sensitive
#: derivation, so a plugin that declares `create_files` keeps the cheap row even when the target
#: already exists and the write is an OVERWRITE. Flooring the whole class instead would over-
#: declare every honest create-only tool and teach authors to route around the check.
AUTHORITY_FLOOR: dict[str, tuple[str, ...]] = {
    "read_only": ("read_files",),
    "network_send": ("external_messages",),
    "network_publish": ("deployment",),
    "media_generation": ("access_external_providers",),
    "credit_spend": ("financial_or_paid_actions",),
    "wallet_spend": ("financial_or_paid_actions",),
    "sandbox_command": ("run_side_effecting_commands",),
    "task_orchestration": ("run_side_effecting_commands",),
    "validation_command": ("run_side_effecting_commands",),
}

#: Classes whose declaration must still face the create-vs-overwrite question.
WRITE_CLASSES: frozenset[str] = frozenset({"workspace_write", "builder_state", "creative_state"})

#: A class nobody declared is not a free pass.
UNKNOWN_CLASS_FLOOR: tuple[str, ...] = ("unknown_side_effect",)

_lock = threading.RLock()


class LifecycleError(RuntimeError):
    """A lifecycle transition that is not allowed from the state the record is in."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_path() -> Path:
    override = str(os.environ.get("VOOL_PLUGIN_LIFECYCLE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    from core.runtime_paths import data_path

    return Path(data_path("")) / STORE_FILENAME


def manifest_digest(root: Path) -> str:
    """A digest over everything that decides what this plugin CAN DO.

    The manifest and every skill body: those are the files that declare tools, permission
    actions, handlers and prompt guidance. A digest that covered only the manifest would let a
    verified plugin change its skills afterwards.
    """

    digest = hashlib.sha256()
    base = Path(root)
    targets: list[Path] = []
    manifest = base / ".codex-plugin" / "plugin.json"
    if manifest.exists():
        targets.append(manifest)
    skills = base / "skills"
    if skills.is_dir():
        targets.extend(sorted(p for p in skills.rglob("SKILL.md") if p.is_file()))
    for path in targets:
        try:
            digest.update(str(path.relative_to(base)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        except OSError:
            # A file that cannot be read cannot be attested to. Marking the digest poisoned is
            # honest; returning a digest over what happened to be readable is not.
            return ""
    return digest.hexdigest() if targets else ""


@dataclass
class PluginRecord:
    plugin_id: str
    stage: str = STAGE_DISCOVERED
    source: str = ""
    root: str = ""
    version: str = ""
    installed_at: str = ""
    inspected_at: str = ""
    verified_at: str = ""
    verified_digest: str = ""
    enabled_at: str = ""
    enabled: bool = False
    revoked_at: str = ""
    revoked_reason: str = ""
    uninstalled_at: str = ""
    updated_at: str = ""
    declared_permissions: list[str] = field(default_factory=list)
    declared_dependencies: list[str] = field(default_factory=list)
    unresolved_dependencies: list[str] = field(default_factory=list)
    credential_bindings: list[str] = field(default_factory=list)
    invocations: int = 0
    last_invoked_at: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def note(self, event: str, **detail: Any) -> None:
        row = {"event": str(event), "at": _utcnow()}
        row.update({k: v for k, v in detail.items() if v is not None})
        self.evidence.append(row)
        del self.evidence[:-64]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_store() -> dict[str, Any]:
    """The store, or an EMPTY store. A store that cannot be trusted installs nothing."""

    path = store_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "plugins": {}}
    except Exception:
        return {"schema": SCHEMA, "plugins": {}, "unreadable": True}
    if not isinstance(payload, dict) or str(payload.get("schema") or "") != SCHEMA:
        return {"schema": SCHEMA, "plugins": {}, "unreadable": True}
    rows = payload.get("plugins")
    if not isinstance(rows, dict):
        return {"schema": SCHEMA, "plugins": {}, "unreadable": True}
    return {"schema": SCHEMA, "plugins": rows}


def _write_store(rows: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "updated": _utcnow(), "plugins": rows}
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".plugin_lifecycle-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _load(plugin_id: str) -> PluginRecord | None:
    row = _read_store()["plugins"].get(str(plugin_id))
    if not isinstance(row, dict):
        return None
    known = {f for f in PluginRecord.__dataclass_fields__}
    try:
        return PluginRecord(**{k: v for k, v in row.items() if k in known})
    except Exception:
        return None


def _save(record: PluginRecord) -> PluginRecord:
    with _lock:
        store = _read_store()
        rows = dict(store["plugins"])
        rows[record.plugin_id] = record.to_dict()
        _write_store(rows)
    return record


def records() -> list[PluginRecord]:
    out: list[PluginRecord] = []
    for pid in sorted(_read_store()["plugins"]):
        record = _load(pid)
        if record is not None:
            out.append(record)
    return out


def record_for(plugin_id: str) -> PluginRecord | None:
    return _load(plugin_id)


def reset_for_tests() -> None:
    with _lock:
        with contextlib.suppress(OSError):
            store_path().unlink()


# ---------------------------------------------------------------------------
# The stages
# ---------------------------------------------------------------------------


def discover(root: Path | None = None) -> list[dict[str, Any]]:
    """Every plugin pack on disk, with the lifecycle state each one is actually in.

    Discovery is an OBSERVATION. Finding a pack installs nothing and enables nothing; a pack
    that has never been installed comes back at stage `discovered` and is not offered.
    """

    from core.plugin_catalog import discovered_manifests, plugins_root
    from core.plugin_tools import discover_manifests

    base = Path(root) if root is not None else plugins_root()
    if base is None:
        return []
    found: list[dict[str, Any]] = []
    # An explicit root is walked directly (a test's temporary tree, an operator's argument). The
    # configured root comes from the bounded storage probe's listing, so the lifecycle snapshot
    # a console reads cannot hang on a folder that stalls (see core.plugin_catalog).
    listing = discover_manifests(Path(base)) if root is not None else discovered_manifests()
    for manifest in listing:
        pack = Path(manifest).parent.parent
        plugin_id = pack.name
        try:
            payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        record = _load(plugin_id)
        digest = manifest_digest(pack)
        found.append(
            {
                "plugin_id": str(payload.get("id") or plugin_id),
                "name": str(payload.get("name") or plugin_id),
                "version": str(payload.get("version") or ""),
                "root": str(pack),
                "manifest_digest": digest,
                "stage": record.stage if record else STAGE_DISCOVERED,
                "installed": bool(record and record.stage not in {STAGE_DISCOVERED, STAGE_UNINSTALLED}),
                "available": is_available(str(payload.get("id") or plugin_id), root=pack),
                "digest_matches_verified": bool(record and record.verified_digest and record.verified_digest == digest),
            }
        )
    return found


def inspect(plugin_id: str, *, root: Path) -> PluginRecord:
    """Read what the pack ASKS FOR -- permissions and dependencies -- without granting any of it."""

    pack = Path(root)
    manifest_path = pack / ".codex-plugin" / "plugin.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LifecycleError(f"{plugin_id}: the manifest could not be read ({type(exc).__name__})") from None
    record = _load(plugin_id) or PluginRecord(plugin_id=str(plugin_id))
    requested = [str(p).strip() for p in list(payload.get("permissions") or []) if str(p).strip()]
    dependencies = [str(d).strip() for d in list(payload.get("requires") or []) if str(d).strip()]
    bindings = [str(b).strip() for b in list(payload.get("credential_bindings") or []) if str(b).strip()]
    record.root = str(pack)
    record.version = str(payload.get("version") or "")
    record.declared_permissions = requested
    record.declared_dependencies = dependencies
    record.unresolved_dependencies = _unresolved(dependencies)
    record.credential_bindings = bindings
    record.inspected_at = _utcnow()
    if record.stage == STAGE_DISCOVERED:
        record.stage = STAGE_INSPECTED
    record.note(
        "inspected",
        requested_permissions=requested,
        dependencies=dependencies,
        unresolved=record.unresolved_dependencies,
        credential_bindings=bindings,
        note="requested, not granted",
    )
    return _save(record)


def _unresolved(dependencies: list[str]) -> list[str]:
    """Which declared dependencies are not satisfied by an installed, available plugin."""

    installed = {r.plugin_id for r in records() if r.stage in {STAGE_INSTALLED, STAGE_VERIFIED, STAGE_ENABLED}}
    return sorted({d for d in dependencies if d not in installed})


def install(plugin_id: str, *, root: Path, source: str = "local") -> PluginRecord:
    record = _load(plugin_id) or PluginRecord(plugin_id=str(plugin_id))
    if record.stage == STAGE_DISCOVERED:
        record = inspect(plugin_id, root=root)
    if record.unresolved_dependencies:
        raise LifecycleError(
            f"{plugin_id}: unresolved dependencies {record.unresolved_dependencies}; install them first"
        )
    record.root = str(root)
    record.source = str(source)
    record.stage = STAGE_INSTALLED
    record.installed_at = _utcnow()
    record.enabled = False
    record.verified_digest = ""
    record.verified_at = ""
    record.note("installed", source=source, note="installed is not available: verify and enable are separate acts")
    return _save(record)


def verify(plugin_id: str, *, root: Path | None = None, expected_digest: str = "") -> PluginRecord:
    """Attest the pack's bytes. What is verified is a DIGEST, and availability re-checks it."""

    record = _load(plugin_id)
    if record is None or record.stage in {STAGE_DISCOVERED, STAGE_UNINSTALLED}:
        raise LifecycleError(f"{plugin_id}: nothing installed to verify")
    pack = Path(root or record.root)
    digest = manifest_digest(pack)
    if not digest:
        record.note("verify_failed", reason="the pack's declaring files could not be read")
        _save(record)
        raise LifecycleError(f"{plugin_id}: the pack's declaring files could not be read, so nothing is attested")
    if expected_digest and expected_digest != digest:
        record.note("verify_failed", reason="digest mismatch", expected=expected_digest, actual=digest)
        _save(record)
        raise LifecycleError(f"{plugin_id}: digest mismatch — expected {expected_digest[:16]}, found {digest[:16]}")
    record.verified_digest = digest
    record.verified_at = _utcnow()
    record.stage = STAGE_VERIFIED
    record.note("verified", digest=digest, note="verified is not enabled")
    return _save(record)


def enable(plugin_id: str) -> PluginRecord:
    record = _load(plugin_id)
    if record is None:
        raise LifecycleError(f"{plugin_id}: nothing installed to enable")
    if record.stage != STAGE_VERIFIED and not (record.stage == STAGE_ENABLED and record.verified_digest):
        raise LifecycleError(f"{plugin_id}: cannot enable from stage `{record.stage}` — verify it first")
    if record.revoked_at:
        raise LifecycleError(f"{plugin_id}: revoked ({record.revoked_reason}); re-install before enabling")
    record.enabled = True
    record.enabled_at = _utcnow()
    record.stage = STAGE_ENABLED
    record.note("enabled")
    return _save(record)


def disable(plugin_id: str) -> PluginRecord:
    record = _load(plugin_id)
    if record is None:
        raise LifecycleError(f"{plugin_id}: nothing installed to disable")
    record.enabled = False
    record.stage = STAGE_VERIFIED if record.verified_digest else STAGE_INSTALLED
    record.note("disabled")
    return _save(record)


def note_invocation(plugin_id: str, intent: str) -> None:
    """Invocation is its own fact. Enabled does not mean invoked, and the receipt says which."""

    record = _load(plugin_id)
    if record is None:
        return
    record.invocations += 1
    record.last_invoked_at = _utcnow()
    record.note("invoked", intent=str(intent))
    _save(record)


def update(plugin_id: str, *, root: Path | None = None, version: str = "") -> PluginRecord:
    """An update RE-OPENS the lifecycle: the new bytes are unverified until verified again.

    This is why availability re-checks the digest rather than trusting a flag. Editing a manifest
    in place used to take effect with no re-verification and, because `load_all` skips a pack whose
    intents are already registered, sometimes with no effect at all until the process restarted.
    """

    record = _load(plugin_id)
    if record is None:
        raise LifecycleError(f"{plugin_id}: nothing installed to update")
    pack = Path(root or record.root)
    previous = record.verified_digest
    record.root = str(pack)
    record.version = str(version or record.version)
    record.verified_digest = ""
    record.verified_at = ""
    record.enabled = False
    record.stage = STAGE_INSTALLED
    record.updated_at = _utcnow()
    record.unresolved_dependencies = _unresolved(record.declared_dependencies)
    record.note("updated", previous_digest=previous, note="unverified and disabled until re-verified and re-enabled")
    _save(record)
    _invalidate_registration(plugin_id)
    return record


def revoke(plugin_id: str, *, reason: str = "") -> PluginRecord:
    """Withdraw authority immediately. The tools deregister; the record and its evidence stay."""

    record = _load(plugin_id)
    if record is None:
        raise LifecycleError(f"{plugin_id}: nothing installed to revoke")
    record.enabled = False
    record.revoked_at = _utcnow()
    record.revoked_reason = str(reason or "revoked by the operator")
    record.stage = STAGE_REVOKED
    record.note("revoked", reason=record.revoked_reason)
    _save(record)
    _invalidate_registration(plugin_id)
    return record


def uninstall(plugin_id: str) -> PluginRecord:
    record = _load(plugin_id)
    if record is None:
        raise LifecycleError(f"{plugin_id}: nothing installed to uninstall")
    record.enabled = False
    record.stage = STAGE_UNINSTALLED
    record.uninstalled_at = _utcnow()
    record.verified_digest = ""
    record.note("uninstalled", note="the record and its evidence are retained; the tools are not offered")
    _save(record)
    _invalidate_registration(plugin_id)
    return record


def _invalidate_registration(plugin_id: str) -> None:
    """Drop this plugin's contracts from the live registry so the change takes effect NOW."""

    try:
        from core import plugin_tools
        from core.tool_registry import contracts_from_source, unregister

        for contract in contracts_from_source(f"plugin:{plugin_id}"):
            unregister(contract.intent)
        plugin_tools.forget_plugin_root(plugin_id)
    except Exception:
        return


def is_available(plugin_id: str, *, root: Path | None = None) -> bool:
    """The ONE predicate the tool-offer path consults. A conjunction, checked every time.

    Installed AND verified AND enabled AND not revoked AND the bytes still hash to what was
    verified. Any missing conjunct means the plugin's tools are not offered to the model.
    """

    record = _load(plugin_id)
    if record is None:
        return False
    if record.stage != STAGE_ENABLED or not record.enabled:
        return False
    if record.revoked_at or (record.uninstalled_at and record.stage == STAGE_UNINSTALLED):
        return False
    if not record.verified_digest:
        return False
    digest = manifest_digest(Path(root or record.root))
    return bool(digest) and digest == record.verified_digest


def available_plugin_ids() -> frozenset[str]:
    return frozenset(r.plugin_id for r in records() if is_available(r.plugin_id))


def authority_floor(side_effect_class: str) -> tuple[str, ...]:
    """The permission actions a class implies whatever the manifest says.

    A write class returns () here -- its floor is the existence escalation in
    `effective_permission_actions`, not a blanket action. A class nobody recognises returns the
    unknown action, which the matrix denies: an unrecognised class is not a free pass.
    """

    clean = str(side_effect_class or "").strip()
    if clean in WRITE_CLASSES:
        return ()
    return AUTHORITY_FLOOR.get(clean, UNKNOWN_CLASS_FLOOR)


def effective_permission_actions(
    *,
    side_effect_class: str,
    declared: tuple[str, ...],
    source: str,
    target_exists: bool = False,
) -> tuple[str, ...]:
    """What the permission controller should classify a third-party tool as.

    For a builtin contract the declaration IS the answer -- it was written next to the code that
    runs, and the derivation that would second-guess it lives in the same repository. For anything
    else the declaration is unioned with two things it cannot opt out of: the floor its declared
    class implies, and -- for a write class whose target already exists -- the overwrite action.
    The union is the safe combinator because the mode matrix takes the STRICTEST effect over the
    action set, so this can only ever tighten a decision.
    """

    if str(source or "builtin") == "builtin":
        return tuple(declared)
    actions = set(declared) | set(authority_floor(side_effect_class))
    if target_exists and str(side_effect_class or "").strip() in WRITE_CLASSES:
        actions.add("overwrite_existing_files")
    return tuple(sorted(actions))


def evidence(plugin_id: str) -> list[dict[str, Any]]:
    record = _load(plugin_id)
    return list(record.evidence) if record else []


def lifecycle_snapshot() -> dict[str, Any]:
    rows = []
    for record in records():
        rows.append(
            {
                "plugin_id": record.plugin_id,
                "stage": record.stage,
                "enabled": record.enabled,
                "verified": bool(record.verified_digest),
                "available": is_available(record.plugin_id),
                "invocations": record.invocations,
                "unresolved_dependencies": list(record.unresolved_dependencies),
                "credential_bindings": list(record.credential_bindings),
                "revoked_at": record.revoked_at,
            }
        )
    return {"schema": SCHEMA, "plugins": rows, "available": sorted(available_plugin_ids())}


__all__ = [
    "AUTHORITY_FLOOR",
    "SCHEMA",
    "STAGE_DISCOVERED",
    "STAGE_ENABLED",
    "STAGE_INSPECTED",
    "STAGE_INSTALLED",
    "STAGE_REVOKED",
    "STAGE_UNINSTALLED",
    "STAGE_VERIFIED",
    "WRITE_CLASSES",
    "LifecycleError",
    "PluginRecord",
    "authority_floor",
    "available_plugin_ids",
    "disable",
    "discover",
    "effective_permission_actions",
    "enable",
    "evidence",
    "inspect",
    "install",
    "is_available",
    "lifecycle_snapshot",
    "manifest_digest",
    "note_invocation",
    "record_for",
    "records",
    "reset_for_tests",
    "revoke",
    "store_path",
    "uninstall",
    "update",
    "verify",
]
