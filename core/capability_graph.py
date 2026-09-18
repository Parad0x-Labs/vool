"""Hierarchical Capability Graph — VOOL's canonical capability discovery engine.

The Capability Graph answers the question:

    "What implementations exist that could satisfy capability requirement X?"

It does NOT:

    - decide what the user should be told (A2)
    - decide whether a tool may execute (A1)
    - decide the semantic route (A2)
    - authorise effects (A1)
    - bind final bytes (A2)

Authority is exactly one: CAPABILITY DISCOVERY.

==============================================================================
DATA MODEL
==============================================================================

A CapabilityId is a stable, hierarchical identifier:

    filesystem.write
    web.search
    image.generate
    email.send
    social.publish

A Capability belongs to one CapabilityFamily.  The family is the first segment
of the dot-separated ID::

    family:     filesystem
    capability: filesystem.write

An Implementation is a concrete runtime binding of a capability.  One
capability may have multiple implementations (e.g. web.search offered by Brave
and by a local index).  An implementation carries an ImplementationId that is
also stable and distinct from the capability ID.

==============================================================================
DISCOVERY FLOW
==============================================================================

Given a typed capability requirement:

    1. Resolve capability/family
    2. Enumerate registered implementations
    3. Remove unavailable implementations
    4. Remove implementations structurally incompatible with current mode
    5. Expose a bounded candidate set (default ≤ 8)

==============================================================================
INTEGRATION — production bootstrap contract
==============================================================================

The registry contracts (``core.runtime_tool_contracts``) are the source of
truth; this graph is their live index.  Importing this module registers ONLY
capabilities and families (``init_graph``) — implementations arrive through an
explicit, deterministic bootstrap from a registry snapshot:

    ``bootstrap_from_registry(snapshot=None)``
        Explicit production bootstrap.  Idempotent: repeated runs replace
        builtin-sourced rows in place (refreshing availability) and never
        clobber foreign-sourced rows (plugin/mcp/kas).  Called by the runtime
        boot path (``bootstrap_runtime_services``).

    ``ensure_registry_bootstrap()``
        Query-time boot invariant: ``discover``/``model_visible_specs`` call
        it so the graph can never SILENTLY serve discovery from an empty index
        while supported contracts exist.  It bootstraps only a fully-empty
        graph — a hand-assembled world (tests, embedders) is never overwritten.

    ``refresh_from_registry()`` / ``registry_epoch()``
        The typed refresh seam for POLICY changes.  Registry contracts can
        change availability at runtime (policy flags); a caller that mutates
        registry-visible policy calls ``refresh_from_registry()`` to re-index.

    ``indexed_registry_epoch()``
        The graph is the registry's LIVE projection: ``core.tool_registry``
        advances an epoch on every register/unregister/reset, the bootstrap
        records the epoch it indexed, and ``ensure_registry_bootstrap`` re-indexes
        whenever the two differ.  A plugin the API server loaded after boot, or an
        MCP server whose listing changed, seats in the next discovery with no
        caller knowing a refresh exists.  Rows sourced from the registry
        (``plugin:<id>``, ``mcp:<server>``) leave the graph when their contract
        leaves the registry; a hand-registered foreign row (source without a
        colon: tests, embedders, KAS) is never touched by the projection.

MCP tools reach the graph ONLY as registry contracts (``core.execution.mcp_bridge``
registers them); the former spec-fingerprint side channel is gone.

The graph does NOT replace the existing ``runtime_tool_specs()`` flat catalog.
It provides a FILTERED VIEW used when a capability requirement is known.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
#  Canonical Capability Families
# ---------------------------------------------------------------------------

# Families are bounded and intentionally small.  The ontology is a navigation
# hierarchy, not a philosophical taxonomy.
_CANONICAL_FAMILIES = frozenset(
    {
        "filesystem",    # read/write/manage files on the machine
        "workspace",     # read/write/manage the active workspace
        "web",           # search, fetch, browse the web
        "sandbox",       # run bounded local commands
        "email",         # send/receive email
        "contacts",      # saved people and services and their exact destinations
        "media",         # generate images, video, audio
        "knowledge",     # search and purchase knowledge
        "learning",      # promote procedures and patterns
        "hive",          # read/write Hive research tasks
        "payment",       # quote prices, settle payments
        "wallet",        # spend funds, sign transactions
        "operator",      # inspect and mutate the local machine
        "skill",         # author, validate, install skills
        "pdf",           # read and OCR PDF documents
        "marketplace",   # browse and purchase listings
        "runtime",       # respond, orchestrate, system internals
        "web0",          # Web0 site building and publishing
        "communication", # Discord, Telegram, social posting
        "orchestration", # task envelope orchestration
        "code",          # code execution and validation
        "system",        # configuration, preferences, settings
        "network",       # peer-to-peer, mesh, relay
        "plugin",        # third-party plugin extensions
        "mcp",           # Model Context Protocol server tools
        # Real contracted families that predate this ontology list; the census
        # (a6c8e3c4) found them live in the contracts with set.* enabled, so the
        # ontology must describe them rather than pretend they are not families.
        "set",           # named preference/setting packs
        "demo",          # guided demo journeys
        "x",             # X/Twitter trending reads
        "profile",       # the operator's typed profile (remember/forget/list)
        "repo",          # repository operations: PRs, CI truth, typed git, authorized push
    }
)

# ---------------------------------------------------------------------------
#  Identity Types
# ---------------------------------------------------------------------------


class CapabilityId(str):
    """Stable, hierarchical capability identifier.

    Example::

        filesystem.write
        web.search
        image.generate

    The first segment before the dot is the family name.
    """

    @property
    def family(self) -> str:
        return self.split(".", 1)[0] if "." in self else self

    @property
    def local_name(self) -> str:
        """The part after the family prefix, or the whole string if no dot."""
        return self.split(".", 1)[1] if "." in self else self


class ImplementationId(str):
    """Stable, unique identifier for a concrete implementation binding.

    Example::

        machine.write_file
        machine.read_file
        web.search.brave
        image.generate.local
        image.generate.cloud
    """


# ---------------------------------------------------------------------------
#  Data Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityFamily:
    """A bounded category of capabilities."""

    id: str              # e.g. "filesystem"
    label: str           # e.g. "Filesystem"
    description: str     # e.g. "Read, write, and manage files on the local machine"


@dataclass(frozen=True)
class Capability:
    """A stable, named capability that belongs to one family."""

    id: CapabilityId     # e.g. "filesystem.write"
    family: str          # e.g. "filesystem"
    label: str           # e.g. "Write files"
    description: str     # e.g. "Create, modify, and delete files and directories"

    def __post_init__(self) -> None:
        if not self.id or "." not in self.id:
            raise ValueError(f"CapabilityId must be hierarchical: {self.id!r}")
        if self.id.family != self.family:
            raise ValueError(
                f"Capability {self.id!r} family={self.id.family!r} "
                f"does not match declared family={self.family!r}"
            )


@dataclass(frozen=True)
class Implementation:
    """A concrete runtime binding of a capability.

    Multiple implementations may share the same capability_id.
    """

    id: ImplementationId                 # e.g. "machine.write_file"
    capability_id: CapabilityId          # e.g. "filesystem.write"
    tool_intent: str                     # e.g. "machine.write_file"
    label: str                           # Human-readable label
    provider: str                        # "builtin", "plugin:{id}", "mcp:{server}", "kas:{adapter}"
    source: str                          # "builtin", "plugin", "mcp", "kas"
    available: bool                      # Is the implementation currently available?
    availability_reason: str             # Why it is or isn't available
    schema_ref: str = ""                 # Optional reference to the JSON schema
    version: str = "1"                   # Implementation version
    read_only: bool = False              # No side effects — ranks ahead of mutations


@dataclass(frozen=True)
class CapabilityCandidate:
    """A single candidate returned by the discovery engine."""

    capability_id: CapabilityId
    implementation_id: ImplementationId
    tool_intent: str
    label: str
    available: bool
    availability_reason: str
    family: str
    match_kind: str                      # "exact", "family", "related"
    provider: str
    source: str
    read_only: bool = False


class UnavailableReason(str, Enum):
    """Canonical reasons an implementation is unavailable.

    These are distinct from authorization reasons.
    """
    NOT_INSTALLED = "not_installed"
    ADAPTER_DISCONNECTED = "adapter_disconnected"
    API_KEY_MISSING = "api_key_missing"
    OFFLINE_MODE = "offline_mode"
    DISABLED_BY_POLICY = "disabled_by_policy"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MISSING_DEPENDENCY = "missing_dependency"
    UNKNOWN = "unknown"


class CompatibilityKind(str, Enum):
    """How a capability requirement matches implementations."""
    EXACT = "exact"                      # capability_id matches exactly
    FAMILY = "family"                    # family matches, capability differs
    RELATED = "related"                  # related through declared relationships
    NONE = "none"                        # no match


# ---------------------------------------------------------------------------
#  Capability Graph
# ---------------------------------------------------------------------------

# Thread-safe global registry
_lock = threading.RLock()
_families: dict[str, CapabilityFamily] = {}
_capabilities: dict[CapabilityId, Capability] = {}
_implementations: dict[ImplementationId, Implementation] = {}
_capability_to_implementations: dict[CapabilityId, set[ImplementationId]] = {}
_family_to_capabilities: dict[str, set[CapabilityId]] = {}
_intent_to_capability: dict[str, CapabilityId] = {}
_related_capabilities: dict[CapabilityId, set[CapabilityId]] = {}

# Deterministic ranking state: first-registration ordinal per implementation.
# An implementation keeps its original ordinal across refreshes so ranking is
# stable for the lifetime of the process.
_registration_order: dict[ImplementationId, int] = {}
_next_registration_ordinal: int = 0

# Production bootstrap state (see INTEGRATION in the module docstring).
_bootstrapped: bool = False
_registry_epoch: int = 0
# The `core.tool_registry.registry_epoch()` the live index was last built from (-1: never).
_INDEXED_REGISTRY_EPOCH: int = -1
# Which intents were runnable when the live registry was last indexed, or None when the last index
# came from an explicit snapshot (a hand-assembled world is never re-indexed from the live registry).
_INDEXED_AVAILABILITY: tuple[tuple[str, bool], ...] | None = None
# Registry-projected sources carry a colon (`plugin:<id>`, `mcp:<server>`); only those rows are
# ever removed by a re-index. A hand-registered foreign row (`plugin`, `mcp`, `kas`) is not ours.
_PROJECTED_SOURCE_PREFIXES = ("plugin:", "mcp:")

# Default maximum candidates exposed per discovery request
_DEFAULT_MAX_CANDIDATES = 8


class CapabilityGraphBootstrapError(RuntimeError):
    """The registry snapshot has supported contracts but population yielded none."""


def register_family(family: CapabilityFamily) -> None:
    """Register a capability family."""
    with _lock:
        _families[family.id] = family
        _family_to_capabilities.setdefault(family.id, set())


def register_capability(capability: Capability) -> None:
    """Register a capability under its family."""
    with _lock:
        if capability.family not in _families:
            _families[capability.family] = CapabilityFamily(
                id=capability.family,
                label=capability.family.title(),
                description=f"{capability.family} capabilities",
            )
        _capabilities[capability.id] = capability
        _family_to_capabilities.setdefault(capability.family, set()).add(capability.id)


def _record_registration_order(impl_id: ImplementationId) -> None:
    """Assign a stable first-registration ordinal (idempotent). Caller holds _lock."""
    global _next_registration_ordinal
    if impl_id not in _registration_order:
        _registration_order[impl_id] = _next_registration_ordinal
        _next_registration_ordinal += 1


def register_implementation(impl: Implementation) -> None:
    """Register an implementation for a capability."""
    with _lock:
        if impl.capability_id not in _capabilities:
            raise ValueError(
                f"Capability {impl.capability_id!r} not registered. "
                f"Register the capability before its implementations."
            )
        _implementations[impl.id] = impl
        _capability_to_implementations.setdefault(impl.capability_id, set()).add(impl.id)
        _intent_to_capability[impl.tool_intent] = impl.capability_id
        _record_registration_order(impl.id)


def register_related_capability(source: CapabilityId, target: CapabilityId) -> None:
    """Declare that target is a related capability of source."""
    with _lock:
        _related_capabilities.setdefault(source, set()).add(target)


def family_for_capability(capability_id: CapabilityId) -> str | None:
    """Return the family name for a capability, or None if unknown."""
    cap = _capabilities.get(capability_id)
    return cap.family if cap else None


def implementations_for_capability(
    capability_id: CapabilityId,
) -> list[Implementation]:
    """Return all registered implementations for a capability."""
    with _lock:
        impl_ids = _capability_to_implementations.get(capability_id, set())
        return [_implementations[iid] for iid in impl_ids if iid in _implementations]


def capabilities_in_family(family: str) -> list[Capability]:
    """Return all capabilities in a family."""
    with _lock:
        cap_ids = _family_to_capabilities.get(family, set())
        return [_capabilities[cid] for cid in cap_ids if cid in _capabilities]


def capability_for_intent(intent: str) -> CapabilityId | None:
    """Return the capability ID for a tool intent, or None if unmapped."""
    return _intent_to_capability.get(intent)


def intent_for_implementation(impl_id: ImplementationId) -> str | None:
    """Return the tool intent for an implementation ID, or None."""
    impl = _implementations.get(impl_id)
    return impl.tool_intent if impl else None


def all_capabilities() -> list[Capability]:
    """Return all registered capabilities."""
    with _lock:
        return list(_capabilities.values())


def all_implementations() -> list[Implementation]:
    """Return all registered implementations."""
    with _lock:
        return list(_implementations.values())


def all_families() -> list[CapabilityFamily]:
    """Return all registered families."""
    with _lock:
        return list(_families.values())


def total_implementations() -> int:
    """Return the total number of registered implementations."""
    with _lock:
        return len(_implementations)


def total_capabilities() -> int:
    """Return the total number of registered capabilities."""
    with _lock:
        return len(_capabilities)


# ---------------------------------------------------------------------------
#  Discovery Engine
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryRequest:
    """A typed capability requirement for the discovery engine."""

    capability_id: CapabilityId | None = None
    family: str | None = None
    intent: str | None = None
    match_mode: str = "exact"            # "exact", "family", "related", "best_effort"


@dataclass
class DiscoveryResult:
    """The result of a discovery query."""

    candidates: list[CapabilityCandidate] = field(default_factory=list)
    total_candidates: int = 0
    total_implementations_in_graph: int = 0
    families_considered: list[str] = field(default_factory=list)
    capabilities_considered: list[str] = field(default_factory=list)
    match_kind: str = "none"
    truncated: bool = False
    max_candidates: int = _DEFAULT_MAX_CANDIDATES


def discover(
    request: DiscoveryRequest,
    *,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
    offline_mode: bool = False,
    available_only: bool = True,
) -> DiscoveryResult:
    """Run the discovery engine.

    Given a typed capability requirement, resolve candidates through the
    capability graph, filtering by availability and mode compatibility.

    The discovery engine NEVER:
        - calls a model
        - executes a tool
        - publishes or commits an answer
        - decides semantic intent
        - decides permission authority
    """
    ensure_registry_bootstrap()

    result = DiscoveryResult(
        max_candidates=max_candidates,
        total_implementations_in_graph=total_implementations(),
    )

    # Resolve the capability requirement
    match_kind = CompatibilityKind.NONE
    candidates: list[CapabilityCandidate] = []
    considered_families: set[str] = set()
    considered_capabilities: set[str] = set()

    # -- Step 1: Resolve by exact capability ID --
    if request.capability_id:
        cap = _capabilities.get(request.capability_id)
        if cap:
            considered_families.add(cap.family)
            considered_capabilities.add(str(cap.id))
            match_kind = CompatibilityKind.EXACT
            candidates = _build_candidates(
                cap.id, match_kind="exact",
                offline_mode=offline_mode,
                available_only=available_only,
            )

    # -- Step 2: Resolve by family (broaden search) --
    if not candidates and request.family:
        family = request.family.lower().strip()
        if family in _families:
            considered_families.add(family)
            match_kind = CompatibilityKind.FAMILY
            for cap_id in _family_to_capabilities.get(family, set()):
                considered_capabilities.add(str(cap_id))
                candidates.extend(
                    _build_candidates(
                        cap_id, match_kind="family",
                        offline_mode=offline_mode,
                        available_only=available_only,
                    )
                )

    # -- Step 3: Resolve by intent (legacy mapping) --
    if not candidates and request.intent:
        cap_id = _intent_to_capability.get(request.intent)
        if cap_id:
            considered_families.add(cap_id.family)
            considered_capabilities.add(str(cap_id))
            match_kind = CompatibilityKind.EXACT
            candidates = _build_candidates(
                cap_id, match_kind="exact",
                offline_mode=offline_mode,
                available_only=available_only,
            )

    # -- Step 4: Best-effort from family prefix --
    if not candidates and request.match_mode == "best_effort":
        if request.capability_id:
            family_name = request.capability_id.family
            if family_name in _families:
                considered_families.add(family_name)
                match_kind = CompatibilityKind.FAMILY
                for cap_id in _family_to_capabilities.get(family_name, set()):
                    considered_capabilities.add(str(cap_id))
                    candidates.extend(
                        _build_candidates(
                            cap_id, match_kind="family",
                            offline_mode=offline_mode,
                            available_only=available_only,
                        )
                    )

    # -- Step 5: Rank deterministically, then apply bounded exposure --
    # Without an explicit rank the truncation below would be drawn from set
    # iteration order — hash-randomized per process (measured: one seed gave
    # all workspace reads, another gave all writes).
    candidates = _ranked_candidates(candidates)
    truncated = len(candidates) > max_candidates
    exposed = candidates[:max_candidates]

    result.candidates = exposed
    result.total_candidates = len(candidates)
    result.families_considered = sorted(considered_families)
    result.capabilities_considered = sorted(considered_capabilities)
    result.match_kind = match_kind.value if isinstance(match_kind, CompatibilityKind) else "none"
    result.truncated = truncated

    return result


def _ranked_candidates(candidates: list[CapabilityCandidate]) -> list[CapabilityCandidate]:
    """Deterministic ranking: available first, read-only before mutations,
    then stable registration order (registry declaration order for builtins),
    with the implementation id as the final total-order tie-break."""
    return sorted(
        candidates,
        key=lambda c: (
            not c.available,
            not c.read_only,
            _registration_order.get(c.implementation_id, 1 << 30),
            str(c.implementation_id),
        ),
    )


def _build_candidates(
    capability_id: CapabilityId,
    *,
    match_kind: str,
    offline_mode: bool,
    available_only: bool,
) -> list[CapabilityCandidate]:
    """Build candidates for a single capability, applying filters."""
    candidates: list[CapabilityCandidate] = []
    impl_ids = _capability_to_implementations.get(capability_id, set())
    for impl_id in impl_ids:
        impl = _implementations.get(impl_id)
        if impl is None:
            continue

        # Determine availability
        is_available = impl.available
        reason = impl.availability_reason

        # Offline mode: exclude cloud-only implementations
        if offline_mode and impl.source == "kas":
            # KAS-backed implementations may be cloud-only
            is_available = False
            reason = "Cloud-only implementation is unavailable in offline mode"

        if available_only and not is_available:
            # Still track the candidate but mark it unavailable
            pass

        candidates.append(
            CapabilityCandidate(
                capability_id=capability_id,
                implementation_id=impl.id,
                tool_intent=impl.tool_intent,
                label=impl.label,
                available=is_available,
                availability_reason=reason,
                family=capability_id.family,
                match_kind=match_kind,
                provider=impl.provider,
                source=impl.source,
                read_only=impl.read_only,
            )
        )
    return candidates


# ---------------------------------------------------------------------------
#  Legacy Mapping — existing tool intents → canonical capabilities
# ---------------------------------------------------------------------------

# This mapping is derived from the existing RuntimeToolContract.capability_id
# values.  It maps each tool intent to its canonical CapabilityId.
_LEGACY_INTENT_TO_CAPABILITY: dict[str, str] = {
    # Workspace tools
    "workspace.list_tree": "workspace.read",
    "workspace.list_files": "workspace.read",
    "workspace.read_file": "workspace.read",
    "workspace.search_text": "workspace.read",
    "workspace.symbol_search": "workspace.read",
    "workspace.git_status": "workspace.git",
    "workspace.git_diff": "workspace.git",
    "workspace.git_summary": "workspace.git",
    "workspace.ensure_directory": "workspace.write",
    "workspace.write_file": "workspace.write",
    "workspace.replace_in_file": "workspace.write",
    "workspace.apply_unified_diff": "workspace.write",
    "workspace.rollback_last_change": "workspace.write",
    "workspace.run_tests": "workspace.validate",
    "workspace.run_lint": "workspace.validate",
    "workspace.run_formatter": "workspace.validate",
    "workspace.run_build": "workspace.validate",

    # Machine (filesystem) tools
    "machine.list_directory": "filesystem.read",
    "machine.inspect_specs": "filesystem.read",
    "machine.disk_usage": "filesystem.read",
    "machine.find_folder": "filesystem.read",
    "machine.find_file": "filesystem.read",
    "machine.find_largest": "filesystem.read",
    "machine.display_inspect": "filesystem.read",
    "machine.event_log_errors": "filesystem.read",
    "machine.list_processes": "filesystem.read",
    "machine.host_state": "filesystem.read",
    "machine.read_file": "filesystem.read",
    "machine.ensure_directory": "filesystem.write",
    "machine.write_file": "filesystem.write",
    "machine.move_path": "filesystem.write",

    # Sandbox tools
    "sandbox.run_command": "sandbox.command",

    # Web tools
    "web.search": "web.search",
    "web.ddg_instant": "web.search",
    "web.fetch": "web.read",
    "web.research": "web.search",
    "browser.render": "web.browser",

    # Email tools
    "email.send": "email.send",
    "email.read": "email.read",

    # Media tools
    "image.generate": "media.image_generation",
    "video.generate": "media.video_generation",

    # Knowledge tools
    "knowledge.search": "knowledge.search",
    "knowledge.purchase": "knowledge.marketplace",

    # Learning tools
    "learning.promote": "learning.procedure",

    # Hive tools
    "hive.list_available": "hive.read",
    "hive.list_research_queue": "hive.read",
    "hive.export_research_packet": "hive.read",
    "hive.search_artifacts": "hive.read",
    "hive.research_topic": "hive.write",
    "hive.create_topic": "hive.write",
    "hive.claim_task": "hive.write",
    "hive.post_progress": "hive.write",
    "hive.submit_result": "hive.write",

    # Payment tools
    "sell.quote": "payment.sell_quote",
    "pay.x402": "wallet.spend",
    "wallet.status": "wallet.read",
    "wallet.simulate": "wallet.read",
    "wallet.payment_status": "wallet.read",
    "wallet.propose": "wallet.propose",

    # Operator tools
    "operator.list_tools": "operator.read",
    "operator.inspect_processes": "operator.read",
    "operator.inspect_services": "operator.read",
    "operator.inspect_disk_usage": "operator.read",
    "operator.cleanup_temp_files": "operator.mutate",
    "operator.move_path": "operator.mutate",
    "operator.schedule_calendar_event": "operator.mutate",
    "operator.discord_post": "communication.social",
    "operator.telegram_send": "communication.social",

    # Skill tools
    "skill.create": "skill.author",
    "skill.list": "skill.author",
    "skill.validate": "skill.author",
    "skill.inspect": "skill.author",
    "skill.install": "skill.install",

    # PDF tools
    "pdf.extract_text": "pdf.read",
    "pdf.ocr": "pdf.read",

    # Orchestration tools
    "orchestration.execute_envelope": "orchestration.execute",

    # Web0 tools
    "web0.create_project": "web0.publish",
    "web0.open_builder_draft": "web0.publish",
    "web0.add_block": "web0.publish",
    "web0.add_gated_section": "web0.publish",
    "web0.fill_slots": "web0.publish",
    "web0.compile_preview": "web0.publish",
    "web0.encrypt_whole_site": "web0.publish",
    "web0.publish": "web0.publish",

    # Runtime tools
    "respond.direct": "runtime.respond",
    "set.preference": "system.config",

    # Plugin tools (generic pattern — specific plugins add their own)
    # "plugin.{id}.{tool}": "plugin.{id}",
}

# Related capabilities — when a capability is a close substitute
_LEGACY_RELATED: dict[str, list[str]] = {
    "workspace.read": ["workspace.write", "workspace.git", "workspace.validate"],
    "workspace.write": ["workspace.read", "workspace.git"],
    "workspace.git": ["workspace.read"],
    "workspace.validate": ["workspace.read", "sandbox.command"],
    "filesystem.read": ["filesystem.write"],
    "filesystem.write": ["filesystem.read"],
    "web.search": ["web.read", "web.browser"],
    "web.read": ["web.search"],
    "web.browser": ["web.search"],
    "sandbox.command": ["workspace.validate"],
    "operator.read": ["operator.mutate"],
    "operator.mutate": ["operator.read"],
    "media.image_generation": ["media.video_generation"],
    "media.video_generation": ["media.image_generation"],
    "hive.read": ["hive.write"],
    "hive.write": ["hive.read"],
    "skill.author": ["skill.install"],
    "skill.install": ["skill.author"],
    "email.send": ["email.read"],
    "email.read": ["email.send"],
}


def build_legacy_mapping() -> int:
    """Register all legacy tool intents into the capability graph.

    Returns the number of intents mapped.
    """
    count = 0
    for intent, cap_id_str in _LEGACY_INTENT_TO_CAPABILITY.items():
        cap_id = CapabilityId(cap_id_str)
        # Register the capability if not already registered
        if cap_id not in _capabilities:
            register_capability(
                Capability(
                    id=cap_id,
                    family=cap_id.family,
                    label=cap_id.local_name.replace("_", " ").title(),
                    description=f"{cap_id.local_name.replace('_', ' ')} capability",
                )
            )
        # Ensure the family is registered
        family = cap_id.family
        if family not in _families:
            register_family(
                CapabilityFamily(
                    id=family,
                    label=family.title(),
                    description=f"{family} capabilities",
                )
            )
        # Register related capabilities
        if cap_id_str in _LEGACY_RELATED:
            for related in _LEGACY_RELATED[cap_id_str]:
                related_cap_id = CapabilityId(related)
                if related_cap_id not in _capabilities:
                    register_capability(
                        Capability(
                            id=related_cap_id,
                            family=related_cap_id.family,
                            label=related_cap_id.local_name.replace("_", " ").title(),
                            description=f"{related_cap_id.local_name.replace('_', ' ')} capability",
                        )
                    )
                register_related_capability(cap_id, related_cap_id)

        # Register the intent-to-capability mapping
        _intent_to_capability[intent] = cap_id
        count += 1
    return count


def build_implementation_from_contract(
    intent: str,
    *,
    capability_id: str,
    source: str = "builtin",
    supported: bool = True,
    unsupported_reason: str = "",
    provider: str = "builtin",
) -> Implementation | None:
    """Build an Implementation from a tool intent and capability_id.

    This is used to populate the graph from existing RuntimeToolContract objects.
    """
    cap_id = CapabilityId(capability_id)
    if cap_id not in _capabilities:
        return None

    impl_id = ImplementationId(intent)
    impl = Implementation(
        id=impl_id,
        capability_id=cap_id,
        tool_intent=intent,
        label=intent.split(".")[-1].replace("_", " ").title(),
        provider=provider,
        source=source,
        available=supported,
        availability_reason=unsupported_reason if not supported else "Available",
    )
    with _lock:
        _implementations[impl.id] = impl
        _capability_to_implementations.setdefault(cap_id, set()).add(impl.id)
        _intent_to_capability[intent] = cap_id
        _record_registration_order(impl.id)
    return impl


# ---------------------------------------------------------------------------
#  Plugin / Skill Capability Declaration
# ---------------------------------------------------------------------------


def capabilities_for_skill(skill_name: str, requirements: list[str]) -> list[CapabilityId]:
    """Resolve a Skill's declared capability requirements.

    Returns the resolved CapabilityId values.  Unknown requirements are
    logged but do not raise — a Skill that declares an unknown capability
    is simply not matched by it.
    """
    resolved: list[CapabilityId] = []
    for req in requirements:
        cap_id = CapabilityId(req) if "." in req else CapabilityId(f"{req}.unknown")
        if cap_id in _capabilities:
            resolved.append(cap_id)
        else:
            # Try as a family name
            if req in _families:
                resolved.extend(cap.id for cap in capabilities_in_family(req))
    return resolved


# ---------------------------------------------------------------------------
#  Production bootstrap from the RuntimeToolContract registry snapshot
# ---------------------------------------------------------------------------


def registry_snapshot() -> list[Any]:
    """The registry snapshot the bootstrap indexes: builtins PLUS registered extras.

    This used to return only the builtin contract literals, which is half of why a
    registered plugin tool could never be seated (census a6c8e3c4): the bootstrap
    never saw the row. ``tool_registry.registered_tools()`` is the same builtin
    list with registered plugin contracts appended, so this changes nothing for a
    plugin-free runtime and everything for one with a pack loaded.
    """
    from core.tool_registry import registered_tools

    return list(registered_tools())


def bootstrap_from_registry(snapshot: list[Any] | None = None) -> int:
    """Explicit, deterministic, idempotent bootstrap from a registry snapshot.

    Every supported builtin contract is indexed exactly once (unsupported
    contracts are indexed too, marked unavailable with their truthful reason,
    so discovery can explain absence).  Duplicate intents in the snapshot
    collapse to their first occurrence.  Re-running replaces builtin-sourced
    rows in place — availability refresh — and never clobbers a row owned by
    a foreign source (plugin/mcp/kas).

    Boot invariant: if the snapshot carries supported contracts but population
    produces zero implementations, ``CapabilityGraphBootstrapError`` is raised
    — the graph must never silently start empty while supported contracts
    exist.

    Returns the number of contracts indexed (registered or refreshed).
    """
    global _bootstrapped, _registry_epoch, _INDEXED_REGISTRY_EPOCH, _INDEXED_AVAILABILITY

    live = snapshot is None
    contracts = registry_snapshot() if live else list(snapshot)
    if live:
        from core.tool_registry import registry_epoch as _live_epoch

        snapshot_epoch = _live_epoch()
    else:
        snapshot_epoch = _INDEXED_REGISTRY_EPOCH

    with _lock:
        count = 0
        seen_intents: set[str] = set()
        for contract in contracts:
            intent = str(getattr(contract, "intent", "") or "").strip()
            if not intent or intent in seen_intents:
                continue
            seen_intents.add(intent)

            # Legacy mapping is authoritative; the contract's own capability_id
            # is the fallback for intents the mapping predates.
            cap_id_str = _LEGACY_INTENT_TO_CAPABILITY.get(intent)
            if not cap_id_str:
                cap_id_str = str(getattr(contract, "capability_id", "") or "").strip()
            if not cap_id_str:
                continue

            supported = bool(getattr(contract, "supported", False))
            unsupported_reason = str(getattr(contract, "unsupported_reason", "") or "").strip()
            source = str(getattr(contract, "source", "builtin") or "builtin").strip()
            provider = "builtin" if source == "builtin" else source
            read_only = bool(getattr(contract, "read_only", False))

            cap_id = CapabilityId(cap_id_str)
            if cap_id not in _capabilities:
                register_capability(
                    Capability(
                        id=cap_id,
                        family=cap_id.family,
                        label=cap_id.local_name.replace("_", " ").title(),
                        description=f"{cap_id.local_name.replace('_', ' ')} capability",
                    )
                )

            impl_id = ImplementationId(intent)
            existing = _implementations.get(impl_id)
            if existing is not None and existing.source != source:
                # Collision control: a foreign source already owns this id.
                continue

            impl = Implementation(
                id=impl_id,
                capability_id=cap_id,
                tool_intent=intent,
                label=intent.split(".")[-1].replace("_", " ").title(),
                provider=provider,
                source=source,
                available=supported,
                availability_reason=unsupported_reason if not supported else "Available",
                read_only=read_only,
            )
            _implementations[impl.id] = impl
            _capability_to_implementations.setdefault(cap_id, set()).add(impl.id)
            _intent_to_capability[intent] = cap_id
            _record_registration_order(impl.id)
            count += 1

        # Registry-projected rows whose contract left the registry (a plugin unregistered,
        # an MCP server removed from the config) leave the projection with it.
        if live:
            gone = [
                (impl_id, impl)
                for impl_id, impl in _implementations.items()
                if str(impl.source).startswith(_PROJECTED_SOURCE_PREFIXES)
                and impl.tool_intent not in seen_intents
            ]
            for impl_id, impl in gone:
                _implementations.pop(impl_id, None)
                _capability_to_implementations.get(impl.capability_id, set()).discard(impl_id)
                if _intent_to_capability.get(impl.tool_intent) == impl.capability_id:
                    _intent_to_capability.pop(impl.tool_intent, None)

        supported_contracts = [
            c for c in contracts if bool(getattr(c, "supported", False))
        ]
        if supported_contracts and not _implementations:
            raise CapabilityGraphBootstrapError(
                f"registry snapshot has {len(supported_contracts)} supported contracts "
                f"but population produced zero implementations"
            )

        _bootstrapped = True
        _registry_epoch += 1
        _INDEXED_REGISTRY_EPOCH = snapshot_epoch
        _INDEXED_AVAILABILITY = _availability_fingerprint(contracts) if live else None
        return count


def ensure_registry_bootstrap() -> bool:
    """Query-time boot invariant: never serve discovery from a silently empty graph.

    Bootstraps only when no bootstrap has run this epoch AND the graph holds
    zero implementations — a hand-assembled world (tests, embedders that
    registered implementations directly) is left untouched.  The primary,
    deterministic production bootstrap is the explicit call in the runtime
    boot path; this guard is the safety net beneath it.

    Beyond the empty-graph guard this is also where the DYNAMIC tool sources
    reach the registry, and where the projection follows the registry epoch:

    - configured MCP servers register their tools as ``mcp:<server>`` contracts
      (``core.execution.mcp_bridge.sync_mcp_registry``);
    - plugin contracts load once per process when ``plugin_runtime_tools`` is
      explicitly enabled (default stays disabled);
    - if the registry epoch moved since the last index — by either of those, by
      the API server loading a pack, by anything — the graph re-indexes.

    Returns True when a bootstrap actually ran.
    """
    global _PLUGINS_LOADED
    _sync_dynamic_sources()
    if _bootstrapped:
        if _index_is_stale():
            bootstrap_from_registry()
            return True
        return False
    with _lock:
        if _bootstrapped or _implementations:
            return False
    bootstrap_from_registry()
    return True


_PLUGINS_LOADED = False


def indexed_registry_epoch() -> int:
    """The `core.tool_registry.registry_epoch()` the live index was last built from."""
    return _INDEXED_REGISTRY_EPOCH


def _availability_fingerprint(contracts: list[Any]) -> tuple[tuple[str, bool], ...]:
    """Which contracted intents are runnable. Contracts recompute ``supported`` from policy on every read."""
    return tuple(sorted({(str(getattr(item, "intent", "") or ""), bool(getattr(item, "supported", False)))
                         for item in contracts}))


def _index_is_stale() -> bool:
    """Whether the live registry, or the availability it reports, moved since the last live index.

    The registry epoch counts registrations. Availability also moves with POLICY, and a policy change
    bumps no epoch -- ``policy_engine.set_operator_policy_values`` reloads the policy and nothing else
    -- so an epoch-only check kept offer seats and the navigator on the availability the graph was
    first indexed under (a disabled tool still seatable, an enabled one unseatable) until something
    unrelated re-indexed it. Measured in email revision 5 (evidence 39a/39b order runs). Re-indexing
    replaces builtin rows in place; an index built from an explicit snapshot is left as assembled.
    """
    try:
        from core.tool_registry import registry_epoch as _live_epoch

        if _live_epoch() != _INDEXED_REGISTRY_EPOCH:
            return True
        if _INDEXED_AVAILABILITY is None:
            return False
        return _availability_fingerprint(registry_snapshot()) != _INDEXED_AVAILABILITY
    except Exception:
        return False


def _sync_dynamic_sources() -> None:
    """Bring MCP and plugin contracts into the registry (fail-soft, idempotent by design)."""
    try:
        from core.execution.mcp_bridge import mcp_tool_specs

        mcp_tool_specs()  # lists the servers once, then keeps the registry's mcp: rows in sync
    except Exception:
        pass
    _ensure_plugins_loaded()


def _ensure_plugins_loaded() -> None:
    """Load plugin contracts once per process when the runtime flag is explicitly on.

    The default remains disabled — this function must be a no-op then, and a
    later in-process enable still loads (the flag is re-checked until it has
    actually been on once). Loading registers the contracts in
    ``core.tool_registry``; the re-index that follows seats them in the graph
    under their own ``plugin.*`` capabilities. Fail-soft on every path: a broken
    pack or a missing plugins directory never costs the user their built-in tools.
    """
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    from core.runtime_flags import flag_enabled

    if not flag_enabled("plugin_runtime_tools"):
        return
    try:
        from core.plugin_catalog import (
            REQUEST_PROBE_BUDGET_S,
            STORAGE_ACCESSIBLE,
            STORAGE_MISSING,
            ensure_discovered,
        )

        # The bounded storage probe, not a directory walk on this (request-path) thread: a
        # folder that stalls costs at most `REQUEST_PROBE_BUDGET_S` here and is retried on a
        # later call, so a stalled-at-boot folder that answers later still gets its packs loaded.
        state = ensure_discovered(budget_s=REQUEST_PROBE_BUDGET_S, register=True)
        if str(state.get("state") or "") in {STORAGE_ACCESSIBLE, STORAGE_MISSING}:
            _PLUGINS_LOADED = True
            # The registry epoch moved; the caller's `ensure_registry_bootstrap`
            # re-indexes on the way out. Nothing here touches the graph directly.
    except Exception:
        pass


def refresh_from_registry() -> int:
    """Re-index the current registry snapshot (the typed refresh seam).

    Call after mutating registry-visible policy (e.g. enabling/disabling a
    tool lane) — nothing invalidates the graph automatically.  Advances
    ``registry_epoch()``.
    """
    return bootstrap_from_registry()


def registry_epoch() -> int:
    """Monotonic count of completed registry bootstraps/refreshes this process."""
    return _registry_epoch


def populate_from_tool_registry() -> int:
    """Back-compat alias for :func:`bootstrap_from_registry` (live snapshot)."""
    return bootstrap_from_registry()


# ---------------------------------------------------------------------------
#  Reset (for testing)
# ---------------------------------------------------------------------------


def reset() -> None:
    """Clear all graph state.  For tests and reloads."""
    global _bootstrapped, _registry_epoch, _next_registration_ordinal
    global _PLUGINS_LOADED, _INDEXED_REGISTRY_EPOCH, _INDEXED_AVAILABILITY
    with _lock:
        _families.clear()
        _capabilities.clear()
        _implementations.clear()
        _capability_to_implementations.clear()
        _family_to_capabilities.clear()
        _intent_to_capability.clear()
        _related_capabilities.clear()
        _registration_order.clear()
        _next_registration_ordinal = 0
        _bootstrapped = False
        _registry_epoch = 0
        _INDEXED_REGISTRY_EPOCH = -1
        _INDEXED_AVAILABILITY = None
        _PLUGINS_LOADED = False


# ---------------------------------------------------------------------------
#  Initialisation
# ---------------------------------------------------------------------------

def init_graph() -> int:
    """Initialise the capability graph with all legacy mappings.

    Returns the number of implementations registered.
    """
    reset()
    count = build_legacy_mapping()
    return count


# ---------------------------------------------------------------------------
#  Model-Visible Specs — bounded tool exposure for the served model path
# ---------------------------------------------------------------------------

# Mapping from toolset hint names (from ExecutionRequirements.allowed_toolsets)
# to capability families.  These are hints the classifier produces, not a
# separate ontology.
_TOOLSET_HINT_TO_FAMILY: dict[str, str] = {
    "workspace": "workspace",
    "web_search": "web",
    "web_fetch": "web",
    "market_prices": "web",
    "weather": "web",
    "filesystem": "filesystem",
    "sandbox": "sandbox",
    "email": "email",
    "contacts": "contacts",
    "media": "media",
    "knowledge": "knowledge",
    "hive": "hive",
    "operator": "operator",
    "skill": "skill",
    "pdf": "pdf",
    "orchestration": "orchestration",
    "web0": "web0",
    "payment": "payment",
    "wallet": "wallet",
    "communication": "communication",
    "learning": "learning",
    "system": "system",
    "code": "code",
    "network": "network",
    "mcp": "mcp",
}

# One representative tool per capability family for the family-navigation set.
# When no capability hint is available the model gets a small bounded set of
# family representatives instead of the full catalog.
#
# Every value MUST be a real contracted intent. The census at a6c8e3c4 found six
# phantom representatives (knowledge.search, operator.discord_post, learning.promote,
# set.preference, plus families with no contracted tools at all): their navigation
# seats vanished silently because nothing resolves them. ``assert_representatives_resolve``
# now makes that a startup failure instead of silent drift — the same law the
# task-class vocabulary already enforces for its own mapping.
_FAMILY_REPRESENTATIVE: dict[str, str] = {
    "workspace": "workspace.read_file",
    "filesystem": "machine.read_file",
    "web": "web.search",
    "sandbox": "sandbox.run_command",
    "email": "email.send",
    "contacts": "contacts.search",
    "media": "media.open",
    "operator": "operator.inspect_disk_usage",
    "skill": "skill.list",
    "pdf": "pdf.extract_text",
    "orchestration": "orchestration.execute_envelope",
    "web0": "web0.open_builder_draft",
    "payment": "sell.quote",
    "wallet": "pay.x402",
    "runtime": "respond.direct",
    "code": "sandbox.run_command",
    "set": "set.use",
    "marketplace": "marketplace.search_listings",
}

# Families without a static representative (mcp, plugin — their tools are dynamic)
# still get a navigation seat at seating time: the first available implementation
# in registration order. Seating handles that; this set only documents which
# families are expected to be dynamic.
_DYNAMIC_REPRESENTATIVE_FAMILIES: frozenset[str] = frozenset({"mcp", "plugin"})


def assert_representatives_resolve() -> None:
    """Every family representative must name a real contracted intent.

    Raises at startup (import) and in tests, never silently: a dead pointer costs
    its whole family a navigation seat, which is how email/media/knowledge/
    communication/learning/system disappeared from the model's map without any
    test noticing.
    """
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    problems: list[str] = []
    for family, intent in sorted(_FAMILY_REPRESENTATIVE.items()):
        if family not in _CANONICAL_FAMILIES:
            problems.append(f"family {family!r} is not canonical")
        if intent not in contracts:
            problems.append(f"family {family!r} representative {intent!r} is not a contracted tool")
    if problems:
        raise RuntimeError(
            "family representative map incoherent: " + "; ".join(problems)
        )


# Tools that are always included in the model-visible set regardless of hints.
# The tuple fixes the seating order (frozenset iteration is hash-randomized);
# the frozenset stays for membership checks and backward compatibility.
# ``capability.expand_family`` is the model's legal escalation past this bounded
# set: it asks for one family's tools on the NEXT round of the same turn.
_ALWAYS_VISIBLE_ORDER: tuple[str, ...] = (
    "respond.direct",
    "operator.list_tools",
    "capability.expand_family",
)
_ALWAYS_VISIBLE = frozenset(_ALWAYS_VISIBLE_ORDER)

# Hard bounds for the adaptive composition. The per-turn definition budget stays
# at ``max_candidates`` (8): reserved seats + explicit intents + family fill all
# share it, so a mixed demand splits the SAME budget rather than growing it.
# A mid-turn ``capability.expand_family`` call adds a bounded bonus per expanded
# family under a hard ceiling, so the long tail of a family the model explicitly
# asked for becomes callable without approaching the 58-definition crash surface.
_MAX_RESOLVED_FAMILIES = 3
_EXPANSION_MAX_FAMILIES = 2  # mirrors tool_offer_state._EXPANSION_MAX_FAMILIES
_EXPANSION_SEAT_BONUS = 16
_HARD_MAX_CANDIDATES = 24


def _intent_is_owner_only(intent: str) -> bool:
    """Whether this contracted intent is owner-only (never model-visible)."""
    try:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        contract = runtime_tool_contract_map().get(str(intent or ""))
        return bool(contract is not None and getattr(contract, "model_visible", True) is False)
    except Exception:
        return False


def model_visible_specs(
    *,
    capability_hint: str | None = None,
    family_hint: str | None = None,
    toolset_hints: tuple[str, ...] = (),
    offline_mode: bool = False,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
    user_text: str = "",
    explicit_intents: tuple[str, ...] = (),
    family_hints: tuple[str, ...] = (),
    source_context: dict[str, Any] | None = None,
    expanded_families: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Build a bounded set of tool specs for the served model/planner path.

    Discovers implementations through the capability graph and materialises
    schemas only for the bounded candidate set.  NEVER returns the full global
    tool catalog.

    Adaptive composition (census a6c8e3c4 — 33 of 79 contracts structurally
    unreachable, every workspace mutation among them):

    - ``user_text`` resolves deterministic demand signals: intents the user's
      own words name are seated BEFORE family ranking and cannot be evicted by
      the read-only-first ordering, and the families those words require all
      get seats (mixed demands no longer starve their second family).
    - ``explicit_intents`` / ``family_hints`` are the caller-typed equivalents,
      for seams that already know the answer.
    - ``source_context`` names the turn whose family EXPANSIONS apply: once the
      model called ``capability.expand_family`` in a turn, every later round's
      offer of that turn includes the family's ranked candidates. Reading them
      never clears them (``core.tool_offer_state``). ``expanded_families`` is the
      turn's set as the caller already read it, so one offer comes from one read.

    The total stays capped at ``max_candidates``: reserved seats, explicit
    intents and per-family fills all draw from the same bounded budget. Seating
    order is deterministic: reserved (fixed order) → explicit (signal order) →
    round-robin across required families (ranked, read-only first, declaration
    order as tie-break) → family-navigation fallback when nothing else seats.
    Unavailable implementations never seat; the navigator reports them as
    metadata instead.
    """
    from core.tool_intent_executor import runtime_tool_specs

    ensure_registry_bootstrap()

    # -- Step 1: Resolve the required family set (ordered, deduped, bounded) --
    families: list[str] = []

    def _add_family(name: str | None) -> None:
        clean = str(name or "").strip()
        if not clean or clean in families:
            return
        # Canonical families always resolve; a family an embedder registered in the
        # graph directly (synthetic worlds, tests) resolves too — the canonical set
        # is the production ontology, not a gate on who may build a graph.
        if clean in _CANONICAL_FAMILIES or clean in _families:
            families.append(clean)

    if family_hint:
        _add_family(family_hint)
    for hint in family_hints:
        _add_family(hint)
    if capability_hint and not families:
        dotted = str(capability_hint).strip()
        if "." in dotted:
            _add_family(CapabilityId(dotted).family)
    for hint in toolset_hints:
        mapped = _TOOLSET_HINT_TO_FAMILY.get(str(hint).lower().strip())
        if mapped:
            _add_family(mapped)

    explicit: list[str] = [str(i).strip() for i in explicit_intents if str(i).strip()]
    if user_text:
        from core.tool_demand_signals import resolve_demand_signals

        signals = resolve_demand_signals(user_text)
        for intent in signals.explicit_intents:
            if intent not in explicit:
                explicit.append(intent)
        for family in signals.required_families:
            _add_family(family)
    expansions: tuple[str, ...] = ()
    if expanded_families is not None:
        expansions = tuple(str(f).strip().lower() for f in expanded_families if str(f).strip())
    elif source_context is not None:
        from core.tool_offer_state import turn_family_expansions

        expansions = turn_family_expansions(source_context)
    for family in expansions:
        _add_family(family)

    families = families[:_MAX_RESOLVED_FAMILIES]
    explicit = explicit[: len(explicit)]  # bounded upstream by the signal resolver

    # An EXPLICIT mid-turn expansion widens the round's budget, bounded: the model
    # asked for one family's tools, so it gets that family's available set — the
    # ranked 6-seat fill would evict exactly the long-tail tools the model asked
    # to see. Still nowhere near the 58-definition always-on catalog, still one
    # turn, still ≤ 2 expanded families. The bonus is paid only for an expanded
    # family that seats in this round's family set: a family the resolved-family
    # cap truncated away seats nothing, and its bonus would silently widen the
    # other families' fill for every remaining round of the turn.
    seat_budget = max_candidates
    for _ in [family for family in expansions if family in families][:_EXPANSION_MAX_FAMILIES]:
        seat_budget += _EXPANSION_SEAT_BONUS
    seat_budget = min(seat_budget, _HARD_MAX_CANDIDATES)

    # -- Step 2: Materialise the spec map and availability truth --
    all_specs = runtime_tool_specs()
    spec_map = {str(spec.get("intent", "")).strip(): spec for spec in all_specs if spec.get("intent")}

    def _intent_available(intent: str) -> bool:
        impl = _implementations.get(ImplementationId(intent))
        if impl is None:
            # Catalog-only rows (uncontracted lane entries) are offered by the
            # live catalog itself; graph membership is not required for them.
            return intent in spec_map
        if str(impl.source).startswith(_PROJECTED_SOURCE_PREFIXES):
            # A plugin tool seats only while its lane is open (the catalog row
            # exists only while ``plugin_runtime_tools`` is enabled); an MCP tool
            # only while its server answers. A graph row left from an earlier
            # window must not out the tool after the lane closed.
            return impl.available and intent in spec_map
        return impl.available

    result_specs: list[dict[str, Any]] = []
    included: set[str] = set()

    def _seat(intent: str, candidate: CapabilityCandidate | None = None) -> None:
        if intent in included or len(result_specs) >= seat_budget:
            return
        if _intent_is_owner_only(intent):
            # An owner-only contract (an operator authorization mint) is NEVER offered to a
            # model -- its legal path is the owner-local surface, and the offer census counts
            # it there. Enforced here, not just declared.
            return
        spec = spec_map.get(intent)
        if spec is None and candidate is not None:
            # Candidate is registered only in the capability graph (synthetic
            # test tools, plugin/MCP tools whose spec is registry-built) —
            # minimal spec so it is still discoverable.
            spec = {
                "intent": intent,
                "description": candidate.label,
                "read_only": candidate.read_only,
                "arguments": {},
            }
        if spec is None:
            return
        result_specs.append(spec)
        included.add(intent)

    # -- Step 3: Reserved seats, then explicit intents (priority over ranking) --
    for intent in _ALWAYS_VISIBLE_ORDER:
        _seat(intent)
    for intent in explicit:
        if _intent_available(intent):
            _seat(intent)

    # -- Step 4: Round-robin family fill from ranked available candidates --
    family_queues: list[list[CapabilityCandidate]] = []
    for family in families:
        result = discover(
            DiscoveryRequest(family=family),
            # The discovery pool must be at least as wide as the seat budget, or
            # an expansion's bonus seats nothing discover() already truncated away.
            max_candidates=seat_budget + len(_ALWAYS_VISIBLE_ORDER),
            offline_mode=offline_mode,
        )
        family_queues.append([c for c in result.candidates if c.available])
    while len(result_specs) < seat_budget and any(family_queues):
        for queue in family_queues:
            if queue and len(result_specs) < seat_budget:
                candidate = queue.pop(0)
                if _intent_available(candidate.tool_intent):
                    _seat(candidate.tool_intent, candidate)

    # -- Step 5: Family-navigation fallback (no hint, or nothing seated) --
    if len(included) <= len(_ALWAYS_VISIBLE_ORDER):
        # Dynamic families (mcp, plugin) seat FIRST: they have no static
        # representative, and a configured MCP server or an enabled plugin that
        # lost the seat race to static representatives would be indistinguishable
        # from not being installed.
        rep_families = set(_FAMILY_REPRESENTATIVE)
        for impl_id in sorted(_registration_order, key=_registration_order.get):
            impl = _implementations.get(impl_id)
            if impl is None or not impl.available:
                continue
            family = str(impl.capability_id).split(".", 1)[0]
            if family in rep_families or family not in _DYNAMIC_REPRESENTATIVE_FAMILIES:
                continue
            if str(impl.source).startswith(_PROJECTED_SOURCE_PREFIXES) and impl.tool_intent not in spec_map:
                continue  # lane closed: its catalog row is absent
            _seat(impl.tool_intent, None)
            rep_families.add(family)
            if len(result_specs) >= seat_budget:
                break
        for rep_intent in _FAMILY_REPRESENTATIVE.values():
            if rep_intent in _ALWAYS_VISIBLE:
                continue
            if _intent_available(rep_intent):
                _seat(rep_intent)

    return result_specs


def legal_offer_paths_map() -> dict[str, tuple[str, ...]]:
    """Every intent the offer seam can seat, with the deterministic path(s) that seat it.

    Paths: ``always_visible`` (reserved seat), ``explicit_demand`` (a demand-signal rule or a
    plugin intent token names it), ``family_hint`` (seated when its family is hinted),
    ``expansion`` (seated by a same-turn ``capability.expand_family``), ``navigator``
    (listed as available by ``operator.list_tools``). An intent absent from this map has no
    legal path — the census reports it as ``no_offer_path`` instead of letting it drift silently.
    """
    import uuid as _uuid

    from core.tool_demand_signals import _RULES
    from core.tool_offer_state import begin_turn_navigation, end_turn_navigation, record_family_expansion

    ensure_registry_bootstrap()
    paths: dict[str, list[str]] = {}

    def _note(intent: str, path: str) -> None:
        bucket = paths.setdefault(intent, [])
        if path not in bucket:
            bucket.append(path)

    for intent in _ALWAYS_VISIBLE_ORDER:
        _note(intent, "always_visible")
    for intent, _family, _pattern in _RULES:
        _note(intent, "explicit_demand")
    with _lock:
        rows = list(_implementations.values())
    for impl in rows:
        if str(impl.source).startswith("plugin:") and impl.available:
            _note(impl.tool_intent, "explicit_demand")
    families = sorted({str(c.family) for c in _capabilities.values()} | set(_CANONICAL_FAMILIES))
    for family in families:
        for spec in model_visible_specs(family_hint=family):
            _note(str(spec.get("intent") or ""), "family_hint")
        context: dict[str, Any] = {"turn_id": f"legal-paths-{_uuid.uuid4().hex[:8]}"}
        scope = begin_turn_navigation(context)
        try:
            record_family_expansion(context, family)
            for spec in model_visible_specs(source_context=context):
                _note(str(spec.get("intent") or ""), "expansion")
        finally:
            end_turn_navigation(context, scope)
    try:
        from core.tool_navigator import catalog_family_table

        for row in catalog_family_table():
            for intent in row.get("available_intents") or row.get("example_intents") or []:
                _note(str(intent), "navigator")
    except Exception:
        pass
    paths.pop("", None)
    return {intent: tuple(found) for intent, found in paths.items()}


def family_hint_from_task_class(task_class: str) -> str | None:
    """Map a production task class to a capability family hint.

    Deterministic lookup into the ONE shared vocabulary
    (``core.task_class_vocabulary``) that the task router also consumes — it
    is NOT a second semantic router and carries no keyword routing.  Returns
    ``None`` for explicitly tool-neutral classes and for unknown classes;
    both keep the family-navigation fallback.
    """
    from core.task_class_vocabulary import family_for_task_class

    return family_for_task_class(task_class)


# Auto-initialise on import
_init_count = init_graph()

# The shared task-class vocabulary must partition the router's classes and
# stay inside the canonical ontology — raise at import, never drift silently.
from core.task_class_vocabulary import assert_vocabulary_coherent as _assert_vocab

_assert_vocab(_CANONICAL_FAMILIES)

# Same law for the family representatives: a dead pointer silently deletes a whole
# family's navigation seat, so it raises at import exactly like a vocabulary drift.
assert_representatives_resolve()


__all__ = [
    "_CANONICAL_FAMILIES",
    "_DEFAULT_MAX_CANDIDATES",
    "Capability",
    "CapabilityCandidate",
    "CapabilityFamily",
    "CapabilityGraphBootstrapError",
    "CapabilityId",
    "CompatibilityKind",
    "DiscoveryRequest",
    "DiscoveryResult",
    "Implementation",
    "ImplementationId",
    "UnavailableReason",
    "all_capabilities",
    "all_families",
    "all_implementations",
    "assert_representatives_resolve",
    "bootstrap_from_registry",
    "build_implementation_from_contract",
    "build_legacy_mapping",
    "capabilities_for_skill",
    "capabilities_in_family",
    "capability_for_intent",
    "discover",
    "ensure_registry_bootstrap",
    "family_for_capability",
    "family_hint_from_task_class",
    "implementations_for_capability",
    "indexed_registry_epoch",
    "init_graph",
    "intent_for_implementation",
    "legal_offer_paths_map",
    "model_visible_specs",
    "populate_from_tool_registry",
    "refresh_from_registry",
    "register_capability",
    "register_family",
    "register_implementation",
    "register_related_capability",
    "registry_epoch",
    "registry_snapshot",
    "reset",
    "total_capabilities",
    "total_implementations",
]
