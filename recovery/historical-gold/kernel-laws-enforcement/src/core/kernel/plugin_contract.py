"""The KAS Plugin Contract — installation declares possible capability; runtime
grant determines actual capability.

Composed strictly above the existing substrate (WasmPluginSandbox -> EffectBroker ->
EffectJournal -> Budget); no new sandbox, journal, or permission engine.

The contract has four mechanically separate capability states:

    DECLARED   — what the signed manifest says the plugin CAN implement.
                 A claim, verified for authorship only. Claiming "*" gains nothing:
                 wildcards refuse at install inspection.
    INSTALLED  — the DECLARED subset a user/policy actually accepted at install time.
    GRANTED    — handles minted from INSTALLED capabilities (scoped per resource).
    EFFECTED   — receipts in Law 4's journal. The ONLY state that describes reality.

Authority flows one way: DECLARED ⊇ INSTALLED ⊇ GRANTED; effects require GRANTED
handles through the broker. Neither the manifest nor the plugin can move anything
upward.

Budgets: the package REQUESTS fuel/memory/calls/reply. Platform policy owns the
runtime Budget — clamped to min(requested, platform ceiling) and never read back
from plugin-supplied data at execution time.

Signing proves INTEGRITY AND AUTHORSHIP, nothing else: a validly-signed package has
no authority until installed and granted. Replay of an OLD signed version is refused
by monotonic per-plugin version tracking (downgrades are attacks too).

Lifecycle: inspect -> verify -> install(grants) -> execute -> update (re-grant
required for ANY new capability request) -> revoke/uninstall. Authority is validated
against CURRENT registry state at every invocation, never cached.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.kernel.broker import EffectBroker, FileReadHandle, HttpHandle
from core.kernel.wasm_sandbox import Budget, WasmPluginSandbox

__all__ = [
    "PluginManifest",
    "PluginPackage",
    "PluginRegistry",
    "SigningError",
    "generate_publisher_keypair",
    "sign_package",
    "verify_package",
]

ABI_VERSION = 1
PLATFORM_BUDGET_CEILING = Budget(fuel=5_000_000, memory_bytes=4_194_304,
                                 max_broker_calls=256,
                                 max_calls_per_handle=64, reply_bytes=65536)


class SigningError(ValueError):
    """Package integrity or authorship failed."""


def generate_publisher_keypair() -> tuple[str, str]:
    """Experimental keys only: returns (publisher_id_hex, private_key_hex)."""
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    priv = key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    return pub.hex(), priv.hex()


def _canonical(manifest: Mapping[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class PluginPackage:
    """Signed artifact: manifest + wasm bytes + publisher signature."""

    manifest: dict            # parsed manifest (never carries the signature itself)
    module: bytes             # wasm binary
    signature: str            # hex ed25519 over canonical(manifest)+sha256(module)

    @property
    def module_sha256(self) -> str:
        return hashlib.sha256(self.module).hexdigest()

    def to_json(self) -> str:
        return json.dumps({"manifest": self.manifest, "signature": self.signature,
                           "module_b64": self.module.hex()}, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "PluginPackage":
        raw = json.loads(text)
        return cls(manifest=dict(raw["manifest"]),
                   module=bytes.fromhex(raw["module_b64"]),
                   signature=str(raw["signature"]))


def sign_package(manifest: dict, module: bytes,
                 publisher_private_key_hex: str) -> PluginPackage:
    if "signature" in manifest:
        raise SigningError("manifest must not carry its own signature field")
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(publisher_private_key_hex))
    payload = _canonical(manifest) + b"|" + hashlib.sha256(module).hexdigest().encode()
    return PluginPackage(manifest=dict(manifest), module=module,
                         signature=key.sign(payload).hex())


def verify_package(package: PluginPackage,
                   publisher_public_key_hex: str) -> None:
    """Integrity + authorship. NOTHING about authority."""
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(publisher_public_key_hex))
    payload = _canonical(package.manifest) + b"|" + package.module_sha256.encode()
    try:
        key.verify(bytes.fromhex(package.signature), payload)
    except InvalidSignature as exc:
        raise SigningError("signature invalid: manifest or module was modified") from exc


# ---------------------------------------------------------------- manifest


class PluginManifest:
    """Validated view over the manifest dict. Refuses everything ambiguous."""

    def __init__(self, raw: Mapping[str, object]) -> None:
        required = ("plugin_id", "version", "abi_version", "entrypoint",
                    "declared_capabilities", "requested_budget",
                    "requires", "metadata")
        missing = [k for k in required if k not in raw]
        if missing:
            raise SigningError(f"manifest missing fields: {missing}")
        self.plugin_id = str(raw["plugin_id"])
        self.version = int(raw["version"])
        self.abi_version = int(raw["abi_version"])
        if self.abi_version != ABI_VERSION:
            raise SigningError(
                f"plugin targets abi {self.abi_version}, platform provides "
                f"{ABI_VERSION} — refusing cleanly rather than guessing")
        self.entrypoint = str(raw["entrypoint"])
        #: how the request reaches the guest: "wat-embedded" modules have the
        #: handle/args baked in at build time; "host-request" modules (the
        #: compiled-plugin SDK contract) receive them in memory as (hlen, alen).
        self.abi_style = str(raw.get("abi_style", "wat-embedded"))
        if self.abi_style not in ("wat-embedded", "host-request"):
            raise SigningError(f"unknown abi_style {self.abi_style!r}")
        caps = list(raw["declared_capabilities"])
        if any(not isinstance(c, str) or c.strip() == "" for c in caps):
            raise SigningError("declared_capabilities must be non-empty strings")
        if any("*" in c for c in caps):
            # A wildcard declaration is an attempt to pre-authorize the unknown.
            raise SigningError(
                "wildcard capability declarations refuse at INSTALL INSPECTION: "
                "installation declares POSSIBLE capability, never the unknown")
        self.declared_capabilities = tuple(sorted(set(caps)))
        rb = dict(raw["requested_budget"])       # REQUESTS, never authority
        self.requested_budget = Budget(
            fuel=int(rb.get("fuel", 200_000)),
            memory_bytes=int(rb.get("memory_bytes", 1_048_576)),
            max_broker_calls=int(rb.get("max_calls", 16)),
            max_calls_per_handle=int(rb.get("max_calls_per_handle", 8)),
            reply_bytes=int(rb.get("reply_bytes", 4096)))
        self.requires = tuple(str(r) for r in raw["requires"])   # Toolbelt's domain
        meta = dict(raw["metadata"])
        if len(json.dumps(meta)) > 4096:
            raise SigningError("metadata blob exceeds 4KiB — this is a manifest")
        self.metadata = meta
        self.publisher = str(raw.get("publisher", ""))

    @staticmethod
    def from_package(package: PluginPackage) -> "PluginManifest":
        return PluginManifest(package.manifest)


# ---------------------------------------------------------------- grants + registry


@dataclass
class _Install:
    manifest: PluginManifest
    granted: frozenset[str]
    budget: Budget
    module: bytes
    broker: EffectBroker = field(repr=False, compare=False)


def _handle_for(capability: str, scope: Mapping[str, object]):
    """Capability id -> scoped handle. THE platform-side mapping; plugins cannot
    choose their own handle shapes."""
    if capability == "repo.read":
        return FileReadHandle(patterns=tuple(scope.get("patterns", ())))
    if capability == "http.get":
        return HttpHandle(origin=str(scope.get("origin")),
                          methods=frozenset({"GET"}),
                          path_scope=tuple(scope.get("path_scope", ("/",))))
    raise KeyError(f"platform implements no handle shape for {capability!r}")


class PluginRegistry:
    """INSTALL DECLARES POSSIBLE. RUNTIME GRANT DETERMINES ACTUAL.

    Grants live HERE, keyed by current plugin version; every invocation validates
    against current state, so updates/revocations/uninstalls kill old authority
    immediately.
    """

    PLATFORM_MAX = PLATFORM_BUDGET_CEILING

    def __init__(self, platform_budget_ceiling: Budget | None = None) -> None:
        self.ceiling = platform_budget_ceiling or self.PLATFORM_MAX
        self._installed: dict[tuple[str, int], _Install] = {}
        self._latest_version: dict[str, int] = {}
        self._revoked: set[str] = set()

    def _budget_for(self, requested: Budget,
                    policy_overrides: Mapping[str, int]) -> Budget:
        def clamp(key: str, requested_v: int, ceiling_v: int) -> int:
            return min(requested_v, ceiling_v,
                       int(policy_overrides.get(key, requested_v)))
        return Budget(
            fuel=clamp("fuel", requested.fuel, self.ceiling.fuel),
            memory_bytes=clamp("memory_bytes", requested.memory_bytes,
                               self.ceiling.memory_bytes),
            max_broker_calls=clamp("max_calls", requested.max_broker_calls,
                                   self.ceiling.max_broker_calls),
            max_calls_per_handle=clamp("max_calls_per_handle",
                                       requested.max_calls_per_handle,
                                       self.ceiling.max_calls_per_handle),
            reply_bytes=clamp("reply_bytes", requested.reply_bytes,
                              self.ceiling.reply_bytes))

    def install(self, package: PluginPackage, *,
                publisher_public_key_hex: str,
                grant_capabilities: frozenset[str],
                grant_scopes: Mapping[str, Mapping[str, object]] | None = None,
                policy_overrides: Mapping[str, int] | None = None) -> int:
        grant_scopes = grant_scopes or {}
        policy_overrides = policy_overrides or {}
        verify_package(package, publisher_public_key_hex)
        manifest = PluginManifest.from_package(package)

        undeclared = set(grant_capabilities) - set(manifest.declared_capabilities)
        if undeclared:
            raise ValueError(
                f"cannot grant {sorted(undeclared)}: not DECLARED by the signed "
                "manifest — installation declares possible, runtime grant decides "
                "actual, and neither may invent the other")

        latest = self._latest_version.get(manifest.plugin_id)
        key = (manifest.plugin_id, manifest.version)
        if latest is not None and manifest.version <= latest and key not in self._installed:
            raise SigningError(
                f"replay/downgrade refused: plugin {manifest.plugin_id!r} already "
                f"at version {latest}; a signed older package may be an attack")

        broker = EffectBroker()
        for cap in sorted(grant_capabilities):
            broker.grant(cap, _handle_for(cap, grant_scopes.get(cap, {})))

        self._installed[key] = _Install(
            manifest, frozenset(grant_capabilities),
            self._budget_for(manifest.requested_budget, policy_overrides),
            package.module, broker)
        # Supersede: once a newer version exists, older installed entries are
        # gone — their replay can never pass as a "reinstall" later.
        for old_key in [k for k in self._installed if k[0] == manifest.plugin_id
                        and k[1] < manifest.version]:
            del self._installed[old_key]
        self._latest_version[manifest.plugin_id] = max(latest or 0, manifest.version)
        self._revoked.discard(manifest.plugin_id)
        return manifest.version

    def update(self, package: PluginPackage, *, publisher_public_key_hex: str,
               grant_capabilities: frozenset[str], **kwargs) -> int:
        """An update requesting NEW authority must NOT inherit it silently: the
        caller must pass the full NEW grant set explicitly — old grants do not
        carry over automatically."""
        plugin_id = str(package.manifest["plugin_id"])
        previous_latest = self._latest_version[plugin_id]
        new_version = int(package.manifest["version"])
        if new_version <= previous_latest:
            raise SigningError("update must increase version")
        return self.install(package, publisher_public_key_hex=publisher_public_key_hex,
                            grant_capabilities=grant_capabilities, **kwargs)

    def revoke(self, plugin_id: str) -> None:
        self._revoked.add(plugin_id)

    def uninstall(self, plugin_id: str, version: int | None = None) -> None:
        keys = [k for k in self._installed if k[0] == plugin_id
                and (version is None or k[1] == version)]
        for k in keys:
            del self._installed[k]

    def installed_grants(self, plugin_id: str) -> frozenset[str]:
        """The CURRENT granted subset of the latest installed version."""
        versions = sorted(v for (pid, v) in self._installed if pid == plugin_id)
        if not versions:
            raise KeyError(f"{plugin_id!r}: nothing installed")
        return self._installed[(plugin_id, versions[-1])].granted

    def execute(self, plugin_id: str, args_json: str, *,
                capability: str | None = None) -> tuple[str, dict]:
        """Run the LATEST installed, non-revoked version under its stored budget.
        Authority is validated against CURRENT state — stale ids from before an
        update, revocation, or uninstall fail closed here. The invoked capability
        must be in the CURRENT grant set (checked HERE, before any code runs)."""
        if plugin_id in self._revoked:
            raise RuntimeError(f"plugin {plugin_id!r} revoked — stale invocations "
                               "fail closed")
        versions = sorted(v for (pid, v) in self._installed if pid == plugin_id)
        if not versions:
            raise KeyError(f"{plugin_id!r}: uninstalled or never installed — stale "
                           "invocations fail closed")
        inst = self._installed[(plugin_id, versions[-1])]
        cap = capability or sorted(inst.granted)[0]
        if cap not in inst.granted:
            raise RuntimeError(
                f"{cap!r} is not GRANTED to {plugin_id!r} (granted: "
                f"{sorted(inst.granted)}) — undeclared/revoked invocation refused "
                "before any plugin code runs")
        inst = self._installed[(plugin_id, versions[-1])]
        sandbox = WasmPluginSandbox(inst.broker, budget=inst.budget)
        bound = sandbox.instantiate(inst.module)
        if inst.manifest.abi_style == "host-request":
            outcome = bound.call_with_request(cap, args_json,
                                              name=inst.manifest.entrypoint)
        else:
            outcome = bound.call(inst.manifest.entrypoint)
        return outcome.status, {"reply": bound.read_reply(), "outcome": outcome}
