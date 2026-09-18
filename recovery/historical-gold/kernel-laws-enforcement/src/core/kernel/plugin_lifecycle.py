"""Lifecycle freshness for the KAS Plugin Contract (pass #13).

The frozen contract (e288f48a) computes everything at INSTALL time. This module
answers one question WITHOUT touching it: can an installed plugin retain
authority or resources that were valid at install but are invalid NOW?

Core law: INSTALLATION IS NOT PERMANENT AUTHORIZATION. Every execution is
re-admitted against CURRENT state and an :class:`AdmissionTicket` pins exactly
which version/hash/grants/budget/policy-epoch an invocation was admitted under.
Historical tickets are immutable facts; later registry/policy mutations never
rewrite them.

Freshness models (each the smallest enforceable rule):

- BUDGET     effective = min(stored install budget, CURRENT ceiling, CURRENT
             overrides). Install-time generosity cannot survive a tightening.
- GRANTS     executed grants = stored grants MINUS CURRENTLY revoked capabilities,
             resolved through a broker rebuilt from CURRENT scopes every run —
             stale installation metadata cannot resurrect a handle.
- PUBLISHER  explicit, testable policy: a blocked publisher's INSTALLED code stops
             running (fail closed). Blocking gates execution, not just install.
- ROTATION   publisher identity is the stable id; the TrustLineage maps id ->
             ordered key generations. A rotated key verifies to the SAME
             publisher; an unknown key claiming the id does NOT.
- VERSION    identity = (plugin_id, version, module sha256): same version+different
             bytes is a DIFFERENT artifact and refused; supersede history makes
             rollback-by-replay impossible.
- REQUIRES   abstract machine-capability requirements revalidated against the
             CURRENT available set at every execution (Toolbelt drift).
- REVOKE     plugin revocation is a lifecycle policy event: it bumps the same
             epoch as every other mutation, is consulted at admission (twice),
             and makes previously issued tickets stale before any later I/O.
             UNINSTALL removes artifacts and fails closed identically; neither
             rewrites historical effect truth.
- CONCURRENT any policy/registry mutation bumps a monotonic epoch; admission
             samples the epoch twice (before checks, after checks, before any
             broker dispatch). A change in between aborts BEFORE effects — the
             simple lifecycle rule: an invocation runs entirely under one epoch.

Composition (no duplicate authority):
    LifecycleRegistry (freshness re-validation + ticket)
      -> PluginRegistry (frozen: identity/signature/install/grant truth)
        -> Authoritative Execution Boundary (pass #8: canonical intent->effect)
          -> WasmPluginSandbox (compute bounds)
            -> EffectBroker (scoped handles)
              -> EffectJournal (the ONLY execution truth)
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.kernel.plugin_contract import (
    PluginPackage,
    PluginRegistry,
    SigningError,
    _canonical,
)
from core.kernel.wasm_sandbox import Budget


class StaleExecutionRefused(RuntimeError):
    """Current state no longer admits what installation once did."""


class ConcurrentPolicyChange(StaleExecutionRefused):
    """State mutated between admission sampling points; nothing was dispatched."""


class _EpochGuardedBroker:
    """The TOCTOU closure (pass #13b): an AdmissionTicket is EVIDENCE of an
    admission decision, never a transferable stale permission token. Every real
    effect must re-prove, AT DISPATCH TIME, that the ticket's policy epoch is
    still current — checked inside the same trusted surface that performs the
    I/O, so there is no window between 'final check' and 'first external
    effect' in which a revoke/block/tighten can be outrun. Lifecycle establishes
    freshness inputs; this guard adds no second permission decision — it is the
    one currency fact evaluated where effects are born."""

    def __init__(self, inner, policy: "PlatformPolicy",
                 ticket: "AdmissionTicket") -> None:
        self._inner = inner
        self._policy = policy
        self._ticket = ticket

    def __getattr__(self, name):            # journal, receipts, grant, ...
        return getattr(self._inner, name)

    def invoke(self, handle_id, args):
        if self._policy.epoch != self._ticket.policy_epoch:
            raise StaleExecutionRefused(
                f"policy moved from epoch {self._ticket.policy_epoch} to "
                f"{self._policy.epoch} after admission — dispatch refused "
                "before any I/O")
        return self._inner.invoke(handle_id, args)


# ------------------------------------------------------------------ trust


class TrustLineage:
    """publisher_id -> ordered key generations. Rotation WITHOUT inventing PKI:
    a new generation verifies to the same publisher; a random key claiming the
    id verifies to nobody."""

    def __init__(self) -> None:
        self._gens: dict[str, list[str]] = {}       # newest last
        self._blocked: set[str] = set()

    def register(self, publisher_id: str, public_key_hex: str) -> None:
        gens = self._gens.setdefault(publisher_id, [])
        if public_key_hex not in gens:
            gens.append(public_key_hex)

    def rotate(self, publisher_id: str, new_public_key_hex: str) -> None:
        """Legitimate succession: appends a NEW generation for the SAME id."""
        if publisher_id not in self._gens:
            raise SigningError(f"cannot rotate unregistered publisher {publisher_id!r}")
        self.register(publisher_id, new_public_key_hex)

    def block(self, publisher_id: str) -> None:
        self._blocked.add(publisher_id)

    def unblock(self, publisher_id: str) -> None:
        self._blocked.discard(publisher_id)

    def blocked(self, publisher_id: str) -> bool:
        return publisher_id in self._blocked

    def signing_key_of(self, package: PluginPackage) -> str:
        """The generation that actually verifies the package — handed to the
        frozen registry so its independent check agrees with ours."""
        pid = str(package.manifest.get("publisher", ""))
        payload = (_canonical(package.manifest) + b"|" +
                   package.module_sha256.encode())
        sig = bytes.fromhex(package.signature)
        for gen in reversed(self._gens.get(pid, [])):
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(gen))
            try:
                key.verify(sig, payload)
                return gen
            except InvalidSignature:
                continue
        raise SigningError(f"no registered generation of {pid!r} verifies this package")

    def verify(self, package: PluginPackage) -> None:
        """Signature must verify under SOME generation of the CLAIMED publisher;
        the claimed publisher must be registered and not blocked."""
        pid = str(package.manifest.get("publisher", ""))
        gens = self._gens.get(pid)
        if not gens:
            raise SigningError(f"publisher {pid!r} is not registered")
        if self.blocked(pid):
            raise SigningError(f"publisher {pid!r} is BLOCKED — execution fails "
                               "closed even for installed packages")
        payload = (_canonical(package.manifest) + b"|" +
                   package.module_sha256.encode())
        sig = bytes.fromhex(package.signature)
        for gen in gens:                      # newest-last: current key first
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(gen))
            try:
                key.verify(sig, payload)
                return
            except InvalidSignature:
                continue
        raise SigningError(
            f"package does not verify under ANY registered generation of "
            f"{pid!r} — an unknown key claiming the id is not a successor")



# ------------------------------------------------------------------ policy


@dataclass
class PlatformPolicy:
    """Mutable platform-side truth. Every mutation bumps the epoch."""
    ceiling: Budget
    overrides: dict = field(default_factory=dict)
    revoked_capabilities: set = field(default_factory=set)
    available_requires: set = field(default_factory=set)
    epoch: int = 0

    def mutate(self) -> int:
        self.epoch += 1
        return self.epoch

    def effective_budget(self, stored: Budget) -> Budget:
        ov = self.overrides
        clamp = lambda k, req, ceil: min(req, ceil, int(ov.get(k, req)))  # noqa: E731
        return Budget(
            fuel=clamp("fuel", stored.fuel, self.ceiling.fuel),
            memory_bytes=clamp("memory_bytes", stored.memory_bytes,
                               self.ceiling.memory_bytes),
            max_broker_calls=clamp("max_calls", stored.max_broker_calls,
                                   self.ceiling.max_broker_calls),
            max_calls_per_handle=clamp("max_calls_per_handle",
                                       stored.max_calls_per_handle,
                                       self.ceiling.max_calls_per_handle),
            reply_bytes=clamp("reply_bytes", stored.reply_bytes,
                              self.ceiling.reply_bytes))


@dataclass(frozen=True)
class AdmissionTicket:
    """Immutable record of exactly what an invocation ran under."""
    plugin_id: str
    version: int
    module_sha256: str
    publisher: str
    granted: frozenset
    budget: Budget
    policy_epoch: int


# ------------------------------------------------------------------ registry


class LifecycleRegistry:
    """Composes freshness over the FROZEN PluginRegistry. No duplicate permission
    decision: the frozen registry remains the source of identity/install/grant
    truth; this layer only re-validates currency and mints tickets."""

    def __init__(self, registry: PluginRegistry, policy: PlatformPolicy,
                 trust: TrustLineage) -> None:
        self.registry = registry
        self.policy = policy
        self.trust = trust
        self._scopes: dict[tuple[str, int], dict] = {}
        self._lifecycle_revoked: set[str] = set()
        self.tickets: list[AdmissionTicket] = []   # append-only history

    def install(self, package: PluginPackage, *, grant_capabilities,
                grant_scopes=None, **kw) -> int:
        self.trust.verify(package)
        # Content hash is part of identity: the same (plugin_id, version) with
        # DIFFERENT bytes is a different artifact and must never silently
        # replace what was audited/installed (frozen registry permits benign
        # same-version reinstall — this layer closes it).
        pid = str(package.manifest["plugin_id"])
        ver = int(package.manifest["version"])
        existing = self.registry._installed.get((pid, ver))
        if existing is not None:
            from hashlib import sha256 as _sha
            if _sha(existing.module).hexdigest() != package.module_sha256:
                raise SigningError(
                    f"{pid!r} v{ver} already installed from DIFFERENT bytes "
                    f"({_sha(existing.module).hexdigest()[:12]}) — same-version "
                    "different-content is a different artifact and refused")
        self.policy.mutate()
        version = self.registry.install(
            package, publisher_public_key_hex=self.trust.signing_key_of(package),
            grant_capabilities=grant_capabilities,
            grant_scopes=grant_scopes or {}, **kw)
        pid = str(package.manifest["plugin_id"])
        latest = max(v for (p, v) in self.registry._installed if p == pid)
        self._scopes[(pid, latest)] = dict(grant_scopes or {})
        return version

    def update(self, package: PluginPackage, *, grant_capabilities, **kw) -> int:
        return self.install(package, grant_capabilities=grant_capabilities, **kw)

    def revoke_capability(self, cap: str) -> None:
        """GRANT FRESHNESS: a capability revoked at platform level goes dark for
        EVERY installed plugin on their next execution."""
        self.policy.revoked_capabilities.add(cap)
        self.policy.mutate()

    def revoke_plugin(self, plugin_id: str) -> None:
        """PLUGIN REVOKE LAW (closure #1): revoking a plugin is a policy event at
        the lifecycle authority — it adds the id to the lifecycle revoked set AND
        bumps the same epoch the dispatch guard checks. Consequences, all
        mechanical:
          - future ADMISSION of that plugin refuses (fail closed);
          - any previously issued ticket goes stale (epoch moved under it), so a
            guarded dispatch after the revoke performs ZERO I/O;
          - historical tickets and journal effects are never rewritten.
        REVOKE is identity-level and reversible via restore_plugin; UNINSTALL
        removes installed artifacts entirely. Both block future execution;
        neither touches effect history."""
        self._lifecycle_revoked.add(plugin_id)
        self.policy.mutate()

    def restore_plugin(self, plugin_id: str) -> None:
        self._lifecycle_revoked.discard(plugin_id)
        self.policy.mutate()

    def uninstall_plugin(self, plugin_id: str) -> None:
        """Remove every installed artifact of the plugin as a CURRENT-policy act
        (epoch-bumped). Historical package identity lives on in old tickets."""
        self.registry.uninstall(plugin_id)
        self._scopes = {k: v for k, v in self._scopes.items() if k[0] != plugin_id}
        self.policy.mutate()

    def block_publisher(self, publisher_id: str) -> None:
        """PUBLISHER FRESHNESS: blocking is a policy event — it must move the
        same epoch the dispatch guard checks, or a block landing after the
        final admission check could be outrun into a real effect."""
        self.trust.block(publisher_id)
        self.policy.mutate()

    def restore_capability(self, cap: str) -> None:
        self.policy.revoked_capabilities.discard(cap)
        self.policy.mutate()

    def set_available_requires(self, caps: set) -> None:
        self.policy.available_requires = set(caps)
        self.policy.mutate()

    def tighten_ceiling(self, ceiling: Budget) -> None:
        self.policy.ceiling = ceiling
        self.policy.mutate()

    # -- admission -------------------------------------------------

    def _admit(self, plugin_id: str, *, capability: str | None,
               _sample: int | None = None) -> tuple[AdmissionTicket, object]:
        epoch0 = self.policy.epoch
        # REVOKE IS CONSULTED HERE, at the single admission authority, sampled in
        # BOTH lifecycle-revoked state AND the frozen registry's own flag (so a
        # direct registry.revoke() cannot be bypassed through this path). The
        # second sample sits inside the same window as the closing epoch check.
        if plugin_id in self._lifecycle_revoked:
            raise StaleExecutionRefused(
                f"plugin {plugin_id!r} is REVOKED — revocation blocks future "
                "admission; historical effects are unchanged")
        if plugin_id in self.registry._revoked:
            raise StaleExecutionRefused(
                f"plugin {plugin_id!r} carries a frozen-registry revocation flag "
                "— lifecycle admission will not bypass it")
        inst = self.registry._installed[self._latest_key(plugin_id)]
        m = inst.manifest

        if self.trust.blocked(m.publisher):
            raise StaleExecutionRefused(
                f"publisher {m.publisher!r} was trusted at install and is blocked "
                "now — installation is not permanent authorization")

        missing = [r for r in m.requires if r not in self.policy.available_requires]
        if missing:
            raise StaleExecutionRefused(
                f"requires {missing}: satisfied at install, unavailable NOW "
                "(machine inventory drift)")

        grants = frozenset(inst.granted - self.policy.revoked_capabilities)
        if not grants:
            raise StaleExecutionRefused(
                f"all formerly-granted capabilities of {plugin_id!r} are currently "
                "revoked — stale installation metadata cannot resurrect them")
        cap = capability or sorted(grants)[0]
        if cap not in grants:
            raise StaleExecutionRefused(f"{cap!r} not currently granted")

        budget = self.policy.effective_budget(inst.budget)
        ticket = AdmissionTicket(
            plugin_id=plugin_id, version=m.version,
            module_sha256=hashlib.sha256(inst.module).hexdigest(),
            publisher=m.publisher, granted=grants, budget=budget,
            policy_epoch=epoch0)

        # Rebuild the broker from CURRENT scopes so renamed/transferred resources
        # resolve to today's world, not install-day's.
        from core.kernel.plugin_contract import _handle_for
        from core.kernel.broker import EffectBroker
        broker = EffectBroker()
        scopes = self._scopes.get((plugin_id, m.version), {})
        for c in sorted(grants):
            broker.grant(c, _handle_for(c, scopes.get(c, {})))
        if epoch0 != self.policy.epoch:           # sampled again AFTER all reads
            raise ConcurrentPolicyChange(
                "policy changed during admission — nothing was dispatched")
        if plugin_id in self._lifecycle_revoked or plugin_id in self.registry._revoked:
            # re-sampled inside the guarded window: revoke landing mid-admission
            # refuses here rather than minting a ticket for dead authority
            raise StaleExecutionRefused(
                f"plugin {plugin_id!r} revoked during admission — nothing dispatched")
        return ticket, (_EpochGuardedBroker(broker, self.policy, ticket), cap)

    #: deterministic synchronization point for tests: called after admission
    #: succeeds and before the sandbox runs. Production leaves it None.
    pre_dispatch_barrier: callable = None  # type: ignore[assignment]

    def execute(self, plugin_id: str, args_json: str, *,
                capability: str | None = None) -> tuple[str, dict]:
        ticket, (broker, cap) = self._admit(plugin_id, capability=capability)
        if self.pre_dispatch_barrier is not None:
            self.pre_dispatch_barrier(self)    # tests pause policy mutations here
        from core.kernel.wasm_sandbox import WasmPluginSandbox
        inst = self.registry._installed[self._latest_key(plugin_id)]
        sandbox = WasmPluginSandbox(broker, budget=ticket.budget)
        bound = sandbox.instantiate(inst.module)
        if inst.manifest.abi_style == "host-request":
            outcome = bound.call_with_request(cap, args_json,
                                              name=inst.manifest.entrypoint)
        else:
            outcome = bound.call(inst.manifest.entrypoint)
        self.tickets.append(ticket)
        return outcome.status, {"reply": bound.read_reply(), "outcome": outcome,
                                "ticket": ticket}

    def _latest_key(self, plugin_id: str):
        versions = sorted(v for (p, v) in self.registry._installed if p == plugin_id)
        if not versions:
            raise KeyError(f"{plugin_id!r}: nothing installed")
        return (plugin_id, versions[-1])
