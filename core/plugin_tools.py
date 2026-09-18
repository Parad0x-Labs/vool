"""Turn a plugin's declared tools into registered runtime tools, and run them.

This is the seam that makes VOOL extensible: a user writes one manifest, and their tool becomes
the same kind of object as a built-in — same catalog, same permission derivation, same execution
record, same answer binding. Nothing downstream needs to know it came from a plugin.

**The format already exists.** `~/Desktop/Vool-skills-plugins` ships `.codex-plugin/plugin.json`
with `name`, `version`, `keywords`, `skills` and an `interface` block, plus `skills/*/SKILL.md`
carrying Claude-Code-shaped frontmatter. Inventing a new manifest would orphan both installed
plugins for no gain, so `runtime` and `tools` are *added* to that file. A manifest without them
loads exactly as it does today.

**Why declaring is not optional.** A plugin tool that declares no `claim` cannot be checked by the
answer binder, so an answer could assert anything about what it returned. Declaring the block is
therefore required whenever a tool reports items or acts on a target — enforced at load, where the
author sees it, rather than discovered later by a user reading a fabricated answer.

**Declared permissions are checked against the declared class.** `permission_actions` is what the
permission controller reads for a plugin tool (the intent-string fallback cannot know a third-party
name), so a manifest could otherwise declare `workspace_write` and `read_files` in the same breath
and be allowed unprompted in Manual mode. A declaration outside its class is refused at load. The
kernel confinement around the child is what makes an author's *remaining* room to lie harmless:
a "read-only" tool that writes is stopped by the sandbox, not by its own honesty.

**Disabled is a state, not an absence.** A plugin the owner turned off still registers, marked
unsupported with the reason, so the census, the navigator and a model that names the tool all get
"disabled by the owner" rather than "no such tool".

Loading is fail-soft per plugin and strict per tool: one malformed pack is skipped with a reason
and the rest still load, but a malformed *tool inside a loading pack* fails the whole pack, because
half a plugin is worse than none.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core import json_schema_lite, tool_registry
from core.runtime_tool_contracts import RuntimeToolContract, ToolClaim

CONTRACT_VERSION = 1

# Mirrors the closed vocabularies the built-in contracts already use. A value outside these is an
# authoring error, not a new tier — a plugin must not be able to invent a security class.
_SIDE_EFFECT_CLASSES = frozenset(
    {
        "read_only",
        "workspace_write",
        "network_send",
        "network_publish",
        "media_generation",
        "credit_spend",
        "sandbox_command",
        "task_orchestration",
        "validation_command",
        "builder_state",
        "creative_state",
        "wallet_spend",
    }
)
_APPROVALS = frozenset({"none", "runtime_policy", "explicit_user_opt_in"})
_HANDLER_KINDS = frozenset({"subprocess"})

# Anything that can act must be gated at least by policy. A plugin declaring a mutating class with
# approval "none" is refused rather than quietly trusted.
_MUTATING_CLASSES = _SIDE_EFFECT_CLASSES - {"read_only"}

# The permission vocabulary (`core.mode_permission_policy.PermissionAction` values) a declaration
# may use, by side-effect class. Read-class actions are admissible under every class (a writing
# tool also reads); a mutating class must name at least one action from its own row, or nothing.
_READ_ACTIONS = frozenset({"read_files", "list_directories", "run_safe_commands", "public_read_only_retrieval"})
_ACTIONS_FOR_CLASS: dict[str, frozenset[str]] = {
    "read_only": _READ_ACTIONS,
    "workspace_write": frozenset({"create_files", "modify_files", "overwrite_existing_files", "delete_files"}),
    "network_send": frozenset({"use_network_access", "external_messages", "access_external_providers"}),
    "network_publish": frozenset({"use_network_access", "deployment", "access_external_providers"}),
    "media_generation": frozenset({"access_external_providers", "use_network_access"}),
    "credit_spend": frozenset({"financial_or_paid_actions"}),
    "wallet_spend": frozenset({"financial_or_paid_actions"}),
    "sandbox_command": frozenset({"run_side_effecting_commands", "run_safe_commands"}),
    "task_orchestration": frozenset({"run_side_effecting_commands"}),
    "validation_command": frozenset({"run_side_effecting_commands", "run_safe_commands"}),
    "builder_state": frozenset({"create_files", "modify_files"}),
    "creative_state": frozenset({"create_files", "modify_files"}),
}
_KNOWN_ACTIONS: frozenset[str] = frozenset().union(*_ACTIONS_FOR_CLASS.values()) | frozenset(
    {"use_browser_or_web_retrieval", "install_dependencies", "change_settings", "git_commit", "git_push"}
)

_lock = threading.RLock()
# plugin_id -> root directory of the pack that registered it. Dispatch needs the root to find the
# handler; nothing else about a contract says where on disk it came from.
_PLUGIN_ROOTS: dict[str, Path] = {}


class PluginManifestError(ValueError):
    """A manifest could not be loaded. The message is written for the plugin's author."""


@dataclass(frozen=True)
class LoadedPlugin:
    plugin_id: str
    version: str
    root: Path
    contracts: tuple[RuntimeToolContract, ...] = ()
    skills: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PluginManifestError(message)


def validate_permission_actions(side_effect_class: str, actions: Any, *, owner: str) -> tuple[str, ...]:
    """The declared permission actions, or a `ValueError` naming what is wrong.

    Shared by plugin manifests and MCP trust pins: both are third-party declarations that the
    permission controller will read as authority, so both are held to the same rule — every action
    must belong to the vocabulary, every action must be admissible under the declared class, and a
    mutating class may not hide behind read-only actions alone.
    """

    declared = tuple(str(x).strip() for x in (actions or ()) if str(x).strip())
    if not declared:
        return ()
    unknown = sorted(set(declared) - _KNOWN_ACTIONS)
    if unknown:
        raise ValueError(
            f"{owner}: permission_actions {unknown} are not permission actions the controller knows"
        )
    own = _ACTIONS_FOR_CLASS.get(str(side_effect_class), frozenset())
    admissible = own | _READ_ACTIONS
    outside = sorted(set(declared) - admissible)
    if outside:
        raise ValueError(
            f"{owner}: permission_actions {outside} are not admissible for side_effect_class "
            f"{side_effect_class!r} (allowed: {sorted(admissible)})"
        )
    if side_effect_class in _MUTATING_CLASSES and not (set(declared) & own):
        raise ValueError(
            f"{owner}: side_effect_class {side_effect_class!r} declares only read-class "
            f"permission_actions {sorted(declared)}; a tool that can change something must name "
            f"one of {sorted(own)} or declare none (and be prompted every time)"
        )
    return tuple(dict.fromkeys(declared))


def _claim_from(raw: Any, *, intent: str, schema: dict[str, Any]) -> ToolClaim:
    """Build the binder's claim block, insisting on one where an answer could go unchecked."""

    block = raw if isinstance(raw, dict) else {}
    claim = ToolClaim(
        target_argument=str(block.get("target_argument") or ""),
        resolved_target_key=str(block.get("resolved_target_key") or ""),
        result_items_key=str(block.get("result_items_key") or ""),
        cites=tuple(str(x) for x in (block.get("cites") or ()) if str(x).strip()),
        asserts_action=bool(block.get("asserts_action")),
    )
    annotated = any(
        json_schema_lite.annotations(schema, name)
        for name in (schema.get("properties") or {})
        if isinstance(schema.get("properties"), dict)
    )
    if annotated and not claim.is_declared:
        raise PluginManifestError(
            f"{intent}: a property declares an x-vool-* annotation, so `claim` must say which "
            "argument is the target — otherwise an answer about this tool cannot be checked"
        )
    return claim


def contract_from_tool(spec: Any, *, plugin_id: str, version: str) -> RuntimeToolContract:
    """Validate one `tools[]` entry and turn it into a runtime contract."""

    _require(isinstance(spec, dict), f"{plugin_id}: every entry in `tools` must be an object")
    intent = str(spec.get("intent") or "").strip()
    _require(bool(intent), f"{plugin_id}: a tool must declare an `intent`")
    _require(
        intent.startswith(f"{plugin_id}."),
        f"{intent!r}: a plugin's intents must be prefixed with its own name ({plugin_id}.<tool>) "
        "so two packs can never collide",
    )

    description = str(spec.get("description") or "").strip()
    _require(
        len(description) >= 12,
        f"{intent}: `description` is what the model reads to decide whether to call this tool; "
        "give it a real sentence",
    )

    schema = spec.get("input_schema")
    _require(isinstance(schema, dict), f"{intent}: `input_schema` must be a JSON Schema object")
    try:
        json_schema_lite.assert_supported(schema)
    except json_schema_lite.UnsupportedSchemaError as exc:
        raise PluginManifestError(f"{intent}: {exc}") from exc

    side_effect = str(spec.get("side_effect_class") or "").strip()
    _require(
        side_effect in _SIDE_EFFECT_CLASSES,
        f"{intent}: side_effect_class {side_effect!r} is not one of {sorted(_SIDE_EFFECT_CLASSES)}",
    )
    approval = str(spec.get("approval_requirement") or "").strip()
    _require(
        approval in _APPROVALS,
        f"{intent}: approval_requirement {approval!r} is not one of {sorted(_APPROVALS)}",
    )
    _require(
        not (side_effect in _MUTATING_CLASSES and approval == "none"),
        f"{intent}: a tool with side_effect_class {side_effect!r} can change something, so it "
        "cannot declare approval_requirement 'none'",
    )

    handler = spec.get("handler")
    _require(isinstance(handler, dict), f"{intent}: `handler` must be an object")
    kind = str((handler or {}).get("kind") or "").strip()
    _require(
        kind in _HANDLER_KINDS,
        f"{intent}: handler kind {kind!r} is not supported (supported: {sorted(_HANDLER_KINDS)})",
    )
    _require(bool(str((handler or {}).get("entry") or "").strip()), f"{intent}: handler needs an `entry`")
    env_allowlist = (handler or {}).get("env_allowlist") or ()
    _require(
        isinstance(env_allowlist, (list, tuple)) and all(isinstance(x, str) for x in env_allowlist),
        f"{intent}: handler.env_allowlist must be a list of environment variable NAMES",
    )
    network = bool((handler or {}).get("network", False))

    try:
        permission_actions = validate_permission_actions(
            side_effect, spec.get("permission_actions"), owner=intent
        )
    except ValueError as exc:
        raise PluginManifestError(str(exc)) from exc
    if network:
        _require(
            "use_network_access" in permission_actions or side_effect in {"network_send", "network_publish", "media_generation"},
            f"{intent}: handler.network is true, so the tool must declare a network side_effect_class "
            "or the use_network_access permission action",
        )

    claim = _claim_from(spec.get("claim"), intent=intent, schema=schema)
    if claim.asserts_action:
        _require(
            side_effect in _MUTATING_CLASSES,
            f"{intent}: claim.asserts_action is true but side_effect_class is {side_effect!r}; a "
            "read-only tool must not be able to back a claim that something changed",
        )
    mutation = spec.get("mutation")
    if mutation is not None:
        # The Blackbox coverage declaration (core/blackbox/coverage/capability.py). The registry
        # refuses a local-mutating tool without one; the loader only carries it -- a malformed
        # declaration fails registration with the registry's own typed reason.
        _require(isinstance(mutation, dict), f"{intent}: `mutation` must be an object")

    return RuntimeToolContract(
        intent=intent,
        description=description,
        tool_surface="plugin",
        capability_id=f"plugin.{plugin_id}",
        capability_claim=description,
        supported=True,
        unsupported_reason="",
        input_schema={k: "declared" for k in (schema.get("properties") or {})},
        output_schema={},
        side_effect_class=side_effect,
        approval_requirement=approval,
        timeout_policy="plugin_declared",
        retry_policy="none",
        artifact_emission="none",
        error_contract="returns_structured_error_result",
        handler=json.dumps(handler, sort_keys=True),
        json_schema=dict(schema),
        claim=claim,
        permission_actions=permission_actions,
        source=f"plugin:{plugin_id}",
        mutation=mutation if isinstance(mutation, dict) else None,
    )


def load_manifest(path: Path) -> LoadedPlugin:
    """Read one `.codex-plugin/plugin.json` and build its contracts. Does not register."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginManifestError(f"{path}: could not be read as JSON ({exc})") from exc
    _require(isinstance(raw, dict), f"{path}: manifest must be a JSON object")

    plugin_id = str(raw.get("name") or "").strip()
    _require(bool(plugin_id), f"{path}: manifest must declare a `name`")
    version = str(raw.get("version") or "0.0.0").strip()
    root = Path(path).resolve().parent.parent

    tools = raw.get("tools")
    if not tools:
        # The two installed plugins are exactly this: valid, loadable, no runtime tools yet.
        return LoadedPlugin(plugin_id=plugin_id, version=version, root=root)

    runtime = raw.get("runtime")
    _require(
        isinstance(runtime, dict),
        f"{plugin_id}: a manifest that declares `tools` must also declare a `runtime` block",
    )
    declared_version = (runtime or {}).get("contract_version")
    _require(
        declared_version == CONTRACT_VERSION,
        f"{plugin_id}: runtime.contract_version must be {CONTRACT_VERSION}, got "
        f"{declared_version!r}. A missing version is not defaulted — a manifest written against a "
        "different contract must fail loudly rather than load half-understood.",
    )
    _require(isinstance(tools, list), f"{plugin_id}: `tools` must be a list")

    contracts = tuple(
        contract_from_tool(item, plugin_id=plugin_id, version=version) for item in tools
    )
    seen: set[str] = set()
    for contract in contracts:
        _require(
            contract.intent not in seen,
            f"{plugin_id}: {contract.intent!r} is declared twice in this manifest",
        )
        seen.add(contract.intent)
    return LoadedPlugin(plugin_id=plugin_id, version=version, root=root, contracts=contracts)


def _owner_disabled(plugin_id: str) -> bool:
    try:
        from core.plugin_catalog import _disabled_ids

        return plugin_id in _disabled_ids()
    except Exception:
        return False


def disabled_reason(plugin_id: str) -> str:
    return f"plugin '{plugin_id}' is disabled by the owner (plugins_enabled.json); enable it to use its tools"


def forget_plugin_root(plugin_id: str) -> bool:
    """Drop one pack's dispatch root. Called when a lifecycle event withdraws it."""

    with _lock:
        return _PLUGIN_ROOTS.pop(str(plugin_id), None) is not None


def lifecycle_reason(plugin_id: str) -> str:
    from core.plugin_lifecycle import record_for

    record = record_for(plugin_id)
    if record is None:
        return (
            f"plugin '{plugin_id}' is present on disk but has never been installed; "
            "installed, verified and enabled are three separate acts and none has happened"
        )
    if record.revoked_at:
        return f"plugin '{plugin_id}' was revoked ({record.revoked_reason})"
    if not record.verified_digest:
        return f"plugin '{plugin_id}' is installed but not verified; its bytes are unattested"
    if not record.enabled:
        return f"plugin '{plugin_id}' is verified but not enabled"
    return (
        f"plugin '{plugin_id}' changed on disk after it was verified; re-verify it before its "
        "tools are offered again"
    )


def register_plugin(plugin: LoadedPlugin) -> tuple[RuntimeToolContract, ...]:
    """Register a loaded plugin's tools atomically, honouring the owner's enabled state.

    A disabled pack registers its contracts as unsupported with the reason, so the tool is a
    known, explained absence everywhere downstream rather than an unknown name. The same is now
    true of a pack that is merely PRESENT: presence is not installation, so an un-installed,
    unverified, disabled or revoked pack registers as an explained absence too.
    """

    if not plugin.contracts:
        return ()
    contracts = plugin.contracts
    from core.plugin_lifecycle import is_available

    if not is_available(plugin.plugin_id, root=Path(plugin.root)):
        reason = lifecycle_reason(plugin.plugin_id)
        contracts = tuple(replace(c, supported=False, unsupported_reason=reason) for c in contracts)
    elif not plugin.enabled or _owner_disabled(plugin.plugin_id):
        reason = disabled_reason(plugin.plugin_id)
        contracts = tuple(replace(c, supported=False, unsupported_reason=reason) for c in contracts)
    registered = tool_registry.register_all(contracts)
    with _lock:
        _PLUGIN_ROOTS[plugin.plugin_id] = Path(plugin.root)
    return registered


def discover_manifests(root: Path) -> tuple[Path, ...]:
    plugins_dir = Path(root) / "plugins"
    if not plugins_dir.is_dir():
        return ()
    found = [
        entry / ".codex-plugin" / "plugin.json"
        for entry in sorted(plugins_dir.iterdir())
        if entry.is_dir() and (entry / ".codex-plugin" / "plugin.json").is_file()
    ]
    return tuple(found)


def load_all(
    root: Path, *, manifests: Sequence[Path] | None = None
) -> tuple[tuple[LoadedPlugin, ...], tuple[str, ...]]:
    """Load every plugin under ``root``. One bad pack is skipped, the rest still load.

    Idempotent per process: a pack whose tools are already registered is reported as loaded
    without re-registering (the registry refuses duplicates loudly, and a reload is `reset()`).

    ``manifests`` is the listing a caller already holds -- the bounded storage probe's
    (`core.plugin_catalog.discover_and_register`) -- so the boot never walks the folder on the
    serving process's own thread. Absent, the folder is listed here as before.
    """

    loaded: list[LoadedPlugin] = []
    errors: list[str] = []
    known = set(tool_registry.registry_map())
    listing = tuple(Path(m) for m in manifests) if manifests is not None else discover_manifests(root)
    for manifest in listing:
        try:
            plugin = load_manifest(manifest)
            if plugin.contracts and all(c.intent in known for c in plugin.contracts):
                with _lock:
                    _PLUGIN_ROOTS.setdefault(plugin.plugin_id, Path(plugin.root))
                loaded.append(plugin)
                continue
            register_plugin(plugin)
            loaded.append(plugin)
        except (PluginManifestError, tool_registry.ToolRegistrationError) as exc:
            errors.append(str(exc))
    return tuple(loaded), tuple(errors)


def plugin_id_for_intent(intent: str) -> str:
    contract = tool_registry.tool_for_intent(intent)
    source = str(getattr(contract, "source", "") or "")
    return source[len("plugin:"):] if source.startswith("plugin:") else ""


def plugin_root_for(intent: str) -> Path | None:
    """Where the pack that registered `intent` lives, or None when it is not a plugin tool."""

    plugin_id = plugin_id_for_intent(intent)
    if not plugin_id:
        return None
    with _lock:
        return _PLUGIN_ROOTS.get(plugin_id)


def plugin_scratch_dir(plugin_id: str) -> Path:
    """The one directory outside its own root a plugin child may write.

    Deliberately NOT under `VOOL_HOME`: the kernel profile read-denies the key home to every
    confined child, and a scratch directory inside it would be unusable. Override the root with
    `VOOL_PLUGIN_SCRATCH_ROOT`.
    """

    override = str(os.environ.get("VOOL_PLUGIN_SCRATCH_ROOT") or "").strip()
    base = Path(override) if override else Path(tempfile.gettempdir()) / "vool-plugin-scratch"
    return base / str(plugin_id)


def confinement_mode() -> str:
    """Operator-side confinement mode for plugin children (never authored by a manifest)."""

    mode = str(os.environ.get("VOOL_PLUGIN_CONFINEMENT") or "auto").strip().lower()
    return mode if mode in {"auto", "heuristic_only"} else "auto"


def execute_plugin_contract(contract: RuntimeToolContract, arguments: dict[str, Any]):
    """Run a registered plugin contract's handler. Returns a `PluginToolResult`; never raises."""

    from core.plugin_executor import PluginToolResult, run_plugin_tool

    intent = str(contract.intent)
    plugin_id = str(contract.source or "")[len("plugin:"):]
    # INVOKE is its own lifecycle act. Enabled does not mean invoked, and a lifecycle that could
    # not tell the two apart would have no evidence for the stage it claims to have.
    if plugin_id:
        from core.plugin_lifecycle import note_invocation

        note_invocation(plugin_id, intent)
    root = plugin_root_for(intent)
    if root is None:
        return PluginToolResult(
            ok=False,
            status="handler_missing",
            error=f"no plugin root is registered for {intent}",
            observation={"intent": intent},
        )
    try:
        handler = json.loads(str(contract.handler or "{}"))
    except ValueError:
        handler = {}
    if not isinstance(handler, dict):
        handler = {}
    timeout = handler.get("timeout_seconds")
    try:
        timeout_seconds = float(timeout) if timeout is not None else 30.0
    except (TypeError, ValueError):
        timeout_seconds = 30.0
    return run_plugin_tool(
        intent=intent,
        arguments=dict(arguments or {}),
        handler=handler,
        schema=dict(contract.json_schema or {}),
        plugin_root=root,
        env_allowlist=tuple(str(x) for x in (handler.get("env_allowlist") or ())),
        scratch_dir=str(plugin_scratch_dir(plugin_id)),
        timeout_seconds=timeout_seconds,
        allow_network=bool(handler.get("network", False)),
        confinement=confinement_mode(),
        read_only=bool(getattr(contract, "side_effect_class", "") == "read_only"),
    )


def reset_roots() -> None:
    with _lock:
        _PLUGIN_ROOTS.clear()


__all__ = [
    "CONTRACT_VERSION",
    "LoadedPlugin",
    "PluginManifestError",
    "confinement_mode",
    "contract_from_tool",
    "disabled_reason",
    "discover_manifests",
    "execute_plugin_contract",
    "load_all",
    "load_manifest",
    "plugin_id_for_intent",
    "plugin_root_for",
    "plugin_scratch_dir",
    "register_plugin",
    "reset_roots",
    "validate_permission_actions",
]
