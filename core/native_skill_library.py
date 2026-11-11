"""The native skill library: typed contracts, typed selection, operator config.

The root defect this module repairs (measured at a7b78e2a): the repo shipped exactly two
native skills — ``skills/media-studio`` and ``skills/vool-hive-mind`` — and nothing loaded
them. ``core.tool_offer_assembly`` scans only the external plugins tree, so both files were
documentation wearing the name of a capability.

This module is NOT a second registry. It feeds the ONE canonical injection seam
(``core.tool_offer_assembly.skill_guidance_for``) and the ONE canonical catalog projection
(``core.plugin_catalog.read_plugin_catalog``) with the in-repo native skills, parsed by the
same ``core.plugin_skills.parse_skill`` loader plugins use. What it adds is the missing half:
a **typed machine-readable contract** per skill and **typed selection** against the turn.

The contract lives in SKILL.md frontmatter (same file, same loader, richer keys):

    id, version                  identity; both required
    risk-class                   read_only | workspace_write | elevated
    task-families                subset of core.task_class_vocabulary.VALID_TASK_CLASSES
    capability-families          subset of core.capability_graph canonical families
    tool-intents                 subset of the runtime tool contracts (selection signal)
    permitted-tools              the tools this workflow uses (budget lever, NEVER a grant)
    prerequisites                typed: "tool:<intent>" | "binary:<name>" | "family:<name>"
    expected-outputs             typed output names the workflow produces
    verification                 typed evidence tokens from VERIFICATION_VOCABULARY
    stopping-conditions          typed conditions that end the workflow
    incompatible-with            skill ids that must not be co-selected
    priority                     tiebreak rank among equal-scored skills (lower = first)

**Selection reads the skill's own words.** A skill is selected from the turn's task class (the
production classifier's output), its demand signals (``core.tool_demand_signals``) and the ask's
words weighed against the words the skill's author wrote for it -- its id, description and
``triggers`` -- through the ONE lexical scorer the plugin lanes already use
(``core.plugin_skills.word_evidence``). No phrase list lives here: every word that can pick a
skill is in that skill's own SKILL.md. The class alone used to decide, and measured on the served
path it could not: "review the code changes in my project before I push", "my CI is red, fix
it" and "cut the first 10 seconds of this video" classify ``unknown`` and got nothing, while every
``general_advisory`` turn got the same evidence lens. ``select_native_skills`` states the law.

**A skill grants nothing.** ``permitted-tools`` narrows an offer (the budget lever
``core.tool_offer_assembly`` already applies) and can never seat a tool that is unavailable,
and the permission controller never reads skills. Pinned by test, not by prose.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RISK_CLASSES = ("read_only", "workspace_write", "elevated")

# The typed evidence vocabulary a contract's `verification` entries are drawn from. A closed
# set, not free prose: the load-time law below can then REQUIRE specific tokens of specific
# skills, and "the release gate verifies cumulative and served proof" becomes a check, not a
# wish.
VERIFICATION_VOCABULARY = frozenset(
    {
        "evidence_cited",
        "failing_test_reproduces",
        "root_cause_state_confirmed",
        "cumulative_suite_green",
        "served_proof",
        "sabotage_proof",
        "typed_receipts",
        "deterministic_evidence",
        "browser_verified",
        "benchmark_measured",
    }
)

# The load-time verification law: named skills MUST declare these tokens or their contract is
# refused. This is where "root-cause-repair rejects symptom-only closure" and "release-gate
# requires cumulative and served proof" live — as load-blocking checks, so a regression that
# edits the doctrine out of a SKILL.md fails the library load instead of silently shipping a
# weaker workflow.
REQUIRED_VERIFICATION: dict[str, frozenset[str]] = {
    "root-cause-repair": frozenset({"failing_test_reproduces", "root_cause_state_confirmed"}),
    "release-gate": frozenset({"cumulative_suite_green", "served_proof"}),
}

# A repair-class skill must carry a stopping condition that refuses symptom-only closure —
# the typed token the contract must contain (case-insensitive substring check on the joined
# stopping conditions + verification).
SYMPTOM_ONLY_CLOSURE_TOKEN = "symptom-only closure is not a stopping condition"

# A skill whose body teaches behavior the runtime must be able to trust carries a load-time
# BODY law: named doctrine tokens the body must contain, so a regression that edits the law
# out of a body fails the library load instead of silently shipping a weaker skill.
REQUIRED_BODY_TOKENS: dict[str, tuple[str, ...]] = {
    "x-editorial-studio": ("never publishes", "creates no files"),
}

_PREREQ_KINDS = ("tool:", "binary:", "family:", "plugin:")


@dataclass(frozen=True)
class SkillContract:
    id: str
    version: str
    name: str
    description: str
    risk_class: str
    task_families: tuple[str, ...]
    capability_families: tuple[str, ...]
    tool_intents: tuple[str, ...]
    permitted_tools: tuple[str, ...]
    prerequisites: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    verification: tuple[str, ...]
    stopping_conditions: tuple[str, ...]
    incompatible_with: tuple[str, ...]
    priority: int
    capabilities: tuple[str, ...] = ()
    #: The skill's declared KIND -- the `type:` key every SKILL.md already carries and the
    #: runtime used to ignore. It decides which LANE the contract competes in, so a
    #: whole-answer doctrine and a task workflow no longer take slots from each other.
    kind: str = "capability_skill"
    # Where this contract came from: "native" (the repo's own skills/), "plugin:<id>" (an
    # installed plugin shipping a typed frontmatter), or "mcp:<server>" (an MCP server shipping
    # the skill as a skill:// resource). Every source converges on THIS one frozen contract.
    source: str = "native"
    path: str = ""
    body: str = ""
    #: Who the skill is for. "developer" marks the skills the VOOL team builds VOOL with (code
    #: review, test runs, release gates ...): left out of a person's list and everyday turns.
    audience: str = ""
    #: The author's own words for when this skill applies (the same ``triggers`` key plugin
    #: skills use). Selection weighs them with the name and description against the ask.
    triggers: tuple[str, ...] = ()

    @property
    def task_family_set(self) -> frozenset[str]:
        return frozenset(self.task_families)


@dataclass(frozen=True)
class SkillContractError:
    """A contract that failed load-time validation, with the typed violations."""

    id: str
    path: str
    violations: tuple[str, ...]


@dataclass(frozen=True)
class LibraryLoad:
    contracts: tuple[SkillContract, ...]
    invalid: tuple[SkillContractError, ...]


@dataclass(frozen=True)
class SkillAvailabilityRecord:
    """Why a skill is or is not in this turn's context. Typed partial truth."""

    id: str
    state: str  # selected | disabled | prerequisite_missing | contract_invalid | below_cutoff | incompatible
    reason: str = ""
    version: str = ""


@dataclass(frozen=True)
class SkillSelection:
    selected: tuple[SkillContract, ...]
    records: tuple[SkillAvailabilityRecord, ...]
    # Ids seated by the task class alone because the ask's words seated nothing of that law.
    # Callers that gate on what the person asked (``tool_offer_assembly._people_safe``) read it.
    by_class_only: frozenset[str] = frozenset()


# Selection weights. Task class is the strongest typed signal (the production classifier's
# deliberate output); demand-signal matches are the user's own words mapped through the
# EXISTING deterministic rules. The ask's words, weighed against the skill's own card, add their
# lexical score on top (``select_native_skills``).
_WEIGHT_TASK_CLASS = 4
_WEIGHT_DEMAND = 2

# ---------------------------------------------------------------------------
# Kinds and lanes
#
# Every SKILL.md already declares `type:`. Nothing read it, so 26 contracts of four
# different kinds competed in ONE pool for two slots -- and the two measured
# eviction failures are both that: `answer-presentation` (a whole-answer doctrine
# claiming four broad advisory families) and an expert lens took both slots and
# pushed out the task skill the turn was actually about.
#
# A doctrine says HOW to write the answer. A task skill says WHAT to do. They were
# never meant to be substitutes, and ranking them against each other made the
# stronger-scoring one silently delete the other. They get separate lanes.
# ---------------------------------------------------------------------------

KIND_CAPABILITY = "capability_skill"
KIND_EXPERT_LENS = "expert_lens"
KIND_WORKFLOW = "workflow_skill"
KIND_EXTERNAL_BRIDGE = "external_bridge"
VALID_KINDS = (KIND_CAPABILITY, KIND_EXPERT_LENS, KIND_WORKFLOW, KIND_EXTERNAL_BRIDGE)

LANE_DOCTRINE = "doctrine"
LANE_TASK = "task"

_LANE_BY_KIND = {
    KIND_EXPERT_LENS: LANE_DOCTRINE,
    KIND_CAPABILITY: LANE_TASK,
    KIND_WORKFLOW: LANE_TASK,
    KIND_EXTERNAL_BRIDGE: LANE_TASK,
}


def _normalized_kind(value: Any) -> str:
    """An unknown or missing `type:` is a TASK skill.

    Failing to the task lane is the conservative default: a mis-declared skill
    competes with its peers as before rather than quietly gaining a reserved
    doctrine slot it was never granted.
    """
    cleaned = str(value or "").strip().lower().replace("-", "_")
    return cleaned if cleaned in VALID_KINDS else KIND_CAPABILITY


def lane_for(contract: SkillContract) -> str:
    return _LANE_BY_KIND.get(getattr(contract, "kind", KIND_CAPABILITY), LANE_TASK)


# ---------------------------------------------------------------------------
# Frontmatter -> contract
# ---------------------------------------------------------------------------

_LIST_KEYS = (
    "task-families",
    "capability-families",
    "tool-intents",
    "permitted-tools",
    "prerequisites",
    "expected-outputs",
    "verification",
    "stopping-conditions",
    "incompatible-with",
)


def _as_list(front: dict[str, Any], key: str) -> tuple[str, ...]:
    value = front.get(key) or ()
    if isinstance(value, str):
        value = value.strip("[]")
        items = [part.strip().strip("'\"") for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        items = []
    return tuple(item for item in items if item)


def contract_from_frontmatter(
    front: dict[str, Any], *, path: str = "", body: str = "", source: str = "native"
) -> SkillContract:
    return SkillContract(
        id=str(front.get("id") or "").strip(),
        version=str(front.get("version") or "").strip(),
        name=str(front.get("name") or front.get("id") or "").strip(),
        description=str(front.get("description") or "").strip(),
        risk_class=str(front.get("risk-class") or front.get("risk_class") or "").strip(),
        task_families=_as_list(front, "task-families"),
        capability_families=_as_list(front, "capability-families"),
        tool_intents=_as_list(front, "tool-intents"),
        permitted_tools=_as_list(front, "permitted-tools") or _as_list(front, "allowed-tools"),
        prerequisites=_as_list(front, "prerequisites"),
        expected_outputs=_as_list(front, "expected-outputs"),
        verification=_as_list(front, "verification"),
        stopping_conditions=_as_list(front, "stopping-conditions"),
        incompatible_with=_as_list(front, "incompatible-with"),
        priority=int(front.get("priority") or 100),
        capabilities=_as_list(front, "capabilities"),
        kind=_normalized_kind(front.get("type")),
        audience=str(front.get("audience") or "").strip().lower(),
        triggers=_as_list(front, "triggers"),
        source=str(source or "native"),
        path=str(path),
        body=str(body or ""),
    )


def contract_violations(
    contract: SkillContract, *, known_skill_ids: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Everything that makes this contract unusable, typed. Empty tuple = valid."""
    violations: list[str] = []
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", contract.id or ""):
        violations.append(f"id {contract.id!r} is not a kebab-case skill id")
    if not re.fullmatch(r"\d+\.\d+\.\d+", contract.version or ""):
        violations.append(f"version {contract.version!r} is not X.Y.Z")
    if contract.risk_class not in RISK_CLASSES:
        violations.append(f"risk-class {contract.risk_class!r} not in {RISK_CLASSES}")
    if not contract.task_families and not contract.capability_families:
        violations.append("at least one of task-families / capability-families is required")

    from core.task_class_vocabulary import VALID_TASK_CLASSES as _VTC

    unknown_tasks = sorted(set(contract.task_families) - set(_VTC))
    if unknown_tasks:
        violations.append(f"task-families not in the production vocabulary: {unknown_tasks}")

    try:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        known_intents = set(runtime_tool_contract_map())
    except Exception:
        known_intents = None
    if known_intents is not None:
        # Tools of a plugin the contract DECLARES as a prerequisite (`plugin:<id>`) are not
        # unknowns — they exist exactly when the pack is installed, which is what the
        # prerequisite gate judges at selection time. Namespace rule: an intent belongs to the
        # pack whose id prefixes it (`vool-database.query` -> plugin `vool-database`).
        declared_plugins = {
            prereq[len("plugin:"):].strip()
            for prereq in contract.prerequisites
            if prereq.startswith("plugin:")
        }

        def _plugin_declared(name: str) -> bool:
            return any(name == p or name.startswith(p + ".") for p in declared_plugins)

        bad_intents = sorted(
            name
            for name in (set(contract.tool_intents) | set(contract.permitted_tools))
            if name not in known_intents and not _plugin_declared(name)
        )
        if bad_intents:
            violations.append(f"tool names this runtime does not have: {bad_intents}")

    for prereq in contract.prerequisites:
        if not any(prereq.startswith(kind) for kind in _PREREQ_KINDS):
            violations.append(f"prerequisite {prereq!r} must start with one of {(_PREREQ_KINDS)}")

    unknown_verification = sorted(set(contract.verification) - VERIFICATION_VOCABULARY)
    if unknown_verification:
        violations.append(f"verification tokens outside the typed vocabulary: {unknown_verification}")

    if not contract.verification:
        violations.append("verification is required (what evidence proves this workflow ran)")
    if not contract.stopping_conditions:
        violations.append("stopping-conditions is required (what ends the workflow)")
    if not contract.expected_outputs:
        violations.append("expected-outputs is required (what the workflow produces)")

    required = REQUIRED_VERIFICATION.get(contract.id)
    if required and not required <= set(contract.verification):
        violations.append(
            f"verification law for {contract.id}: missing typed tokens "
            f"{sorted(required - set(contract.verification))}"
        )
    if contract.id == "root-cause-repair":
        joined = " ".join(list(contract.stopping_conditions) + list(contract.verification)).lower()
        if SYMPTOM_ONLY_CLOSURE_TOKEN not in joined:
            violations.append(
                "root-cause-repair must declare the stopping condition that refuses "
                "symptom-only closure"
            )
    if contract.risk_class == "elevated" and "served_proof" not in contract.verification:
        violations.append("elevated-risk skills must require served_proof in verification")

    for token in REQUIRED_BODY_TOKENS.get(contract.id, ()):
        # Whitespace-insensitive: markdown line wrapping must not hide doctrine.
        flattened_body = " ".join(str(contract.body or "").lower().split())
        if token.lower() not in flattened_body:
            violations.append(
                f"body law for {contract.id}: required doctrine token {token!r} is missing"
            )

    unknown_incompat = sorted(set(contract.incompatible_with) - set(known_skill_ids) - {contract.id})
    if unknown_incompat and known_skill_ids:
        violations.append(f"incompatible-with names skills outside the library: {unknown_incompat}")
    return tuple(violations)


# ---------------------------------------------------------------------------
# Library location and loading
# ---------------------------------------------------------------------------


def native_skills_root() -> Path:
    """The in-repo native skill library. ``skills/`` at the repo root, overridable for tests."""
    override = str(os.environ.get("VOOL_NATIVE_SKILLS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "skills"


def developer_skills_on() -> bool:
    """The developer setting: ``VOOL_DEVELOPER_SKILLS=1`` shows and seats the developer skills."""
    return str(os.environ.get("VOOL_DEVELOPER_SKILLS") or "").strip().lower() in {"1", "true", "yes", "on"}


def is_developer_skill(skill_id: str) -> bool:
    """Whether this id is one of VOOL's own developer skills (``audience: developer``).

    The skills the VOOL team builds VOOL with ship in the repo's library, but a person using
    VOOL for everyday things must never see them listed or have them steer a reply ("write a
    complaint to my landlord, the heating is broken" was classed as debugging and seated
    root-cause-repair, measured 2026-10-29). They stay loadable for the selection law; the
    person-facing list and the per-turn guidance decide with this mark.
    """
    cleaned = str(skill_id or "").strip().lower()
    if not cleaned:
        return False
    try:
        contracts = load_skill_contracts().contracts
    except Exception:
        return False
    return any(
        contract.audience == "developer" and cleaned in {contract.id.lower(), contract.name.lower()}
        for contract in contracts
    )


def shown_to_people(skill_id: str) -> bool:
    """Whether a person-facing list or a turn may carry this skill."""
    return developer_skills_on() or not is_developer_skill(skill_id)


def load_native_library(root: Path | None = None) -> LibraryLoad:
    """Parse and validate every native SKILL.md. Invalid contracts are refused, not injected."""
    from core.plugin_skills import parse_skill

    base = Path(root) if root is not None else native_skills_root()
    contracts: list[SkillContract] = []
    errors: list[SkillContractError] = []
    if not base.is_dir():
        return LibraryLoad(contracts=(), invalid=())

    # First pass collects ids so incompatible-with can be validated against the real library.
    parsed: list[tuple[SkillContract, Any]] = []
    for entry in sorted(base.iterdir()):
        candidate = entry / "SKILL.md" if entry.is_dir() else entry
        if candidate.name != "SKILL.md" or not candidate.is_file():
            continue
        skill = parse_skill(candidate)
        if skill is None:
            errors.append(
                SkillContractError(
                    id=entry.name if entry.is_dir() else candidate.stem,
                    path=str(candidate),
                    violations=("no YAML frontmatter",),
                )
            )
            continue
        front = _frontmatter_of(candidate)
        contract = contract_from_frontmatter(
            front, path=str(candidate), body=str(getattr(skill, "body", "") or "")
        )
        parsed.append((contract, skill))

    known_ids = frozenset(c.id for c, _ in parsed)
    for contract, _skill in parsed:
        violations = contract_violations(contract, known_skill_ids=known_ids)
        if violations:
            errors.append(SkillContractError(id=contract.id or "?", path=contract.path, violations=violations))
        else:
            contracts.append(contract)
    return LibraryLoad(contracts=tuple(contracts), invalid=tuple(errors))


# ---------------------------------------------------------------------------
# The ONE authority over ALL sources: native, plugin-typed, MCP
# ---------------------------------------------------------------------------

#: MCP-sourced skills require spawning the server subprocess, so their contracts are cached
#: until explicitly reset (config change or test reset). Keyed on the resolved config + mtime
#: so a config edit is honoured without a restart.
_MCP_SKILL_CACHE: dict[tuple[str, int], LibraryLoad] = {}
_CONTRACT_LOCK = None


def _contract_lock():
    global _CONTRACT_LOCK
    if _CONTRACT_LOCK is None:
        import threading

        _CONTRACT_LOCK = threading.RLock()
    return _CONTRACT_LOCK


def reset_contract_caches() -> None:
    """Drop every cached parse. Tests call this between worlds; a config edit calls it too."""
    with _contract_lock():
        _MCP_SKILL_CACHE.clear()


def parse_skill_text(text: str, *, source: str = "native", path: str = "") -> SkillContract | None:
    """Parse a SKILL.md delivered as TEXT (MCP resources), through the same frontmatter path."""

    from core.plugin_skills import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(str(text or ""))
    if not match:
        return None
    try:
        import yaml

        loaded = yaml.safe_load(match.group(1))
        front = loaded if isinstance(loaded, dict) else {}
    except Exception:
        front = {}
    body = (match.group(2) or "").strip()[:8000]
    return contract_from_frontmatter(front, path=path, body=body, source=source)


def _is_typed_frontmatter(front: dict[str, Any]) -> bool:
    """A plugin skill OPTS INTO the typed contract by declaring `id:`.

    Skills without it keep the legacy lexical behaviour untouched — the reconciliation
    converges sources that declare the contract, it does not retroactively invalidating
    every plugin that predates it.
    """
    return bool(str(front.get("id") or "").strip())


def load_plugin_typed_contracts() -> LibraryLoad:
    """Typed contracts declared by INSTALLED (enabled) plugins, validated by the same law."""
    from core.plugin_skills import parse_skill

    try:
        from core.tool_offer_assembly import _plugin_dirs

        dirs = _plugin_dirs()
    except Exception:
        dirs = ()
    contracts: list[SkillContract] = []
    errors: list[SkillContractError] = []
    for plugin_id, plugin_dir in dirs:
        skills_dir = plugin_dir / "skills"
        if not skills_dir.is_dir():
            continue
        source = f"plugin:{plugin_id}"
        for entry in sorted(skills_dir.iterdir()):
            candidate = entry / "SKILL.md" if entry.is_dir() else entry
            if candidate.name != "SKILL.md" or not candidate.is_file():
                continue
            front = _frontmatter_of(candidate)
            if not _is_typed_frontmatter(front):
                continue  # legacy plugin skill: lexical world, untouched
            skill = parse_skill(candidate)
            contract = contract_from_frontmatter(
                front,
                path=str(candidate),
                body=str(getattr(skill, "body", "") or "") if skill else "",
                source=source,
            )
            violations = contract_violations(contract)
            if violations:
                errors.append(
                    SkillContractError(id=contract.id or "?", path=contract.path, violations=violations)
                )
            else:
                contracts.append(contract)
    return LibraryLoad(contracts=tuple(contracts), invalid=tuple(errors))


#: The uri scheme an MCP server uses to ship a skill resource. Everything else it serves is
#: data under the bridge's framing rules; `skill://` entries opt into the SAME contract
#: validation every other source passes — server text becomes guidance only after validation.
MCP_SKILL_URI_SCHEME = "skill://"


def load_mcp_skill_contracts() -> LibraryLoad:
    """Skills shipped by configured MCP servers as ``skill://`` resources.

    Same parse, same validation law, same config store, same selection as every other
    source — discovery is the ONLY thing that differs.
    """
    try:
        from core.execution.mcp_bridge import _client_for, load_mcp_server_configs
    except Exception:
        return LibraryLoad(contracts=(), invalid=())

    config_path = ""
    try:
        from core.execution.mcp_bridge import _config_path

        raw_path = _config_path()
        config_path = str(raw_path or "")
    except Exception:
        pass
    cache_key = (config_path, 0)
    if config_path:
        try:
            import os

            cache_key = (config_path, int(os.stat(config_path).st_mtime))
        except OSError:
            cache_key = (config_path, 0)
    with _contract_lock():
        cached = _MCP_SKILL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    contracts: list[SkillContract] = []
    errors: list[SkillContractError] = []
    for cfg in load_mcp_server_configs():
        server = str(cfg.get("name") or "")
        client = _client_for(cfg["name"])
        if client is None:
            continue
        try:
            resources = client.list_resources()
        except Exception:
            continue  # a server without resource support ships no skills; not an error row
        for resource in resources:
            uri = str(getattr(resource, "uri", "") or "")
            if not uri.startswith(MCP_SKILL_URI_SCHEME):
                continue
            source = f"mcp:{server}"
            try:
                text = client.read_resource_text(uri)
            except Exception as exc:
                errors.append(
                    SkillContractError(
                        id=uri[len(MCP_SKILL_URI_SCHEME):], path=uri,
                        violations=(f"resource unreadable: {type(exc).__name__}",),
                    )
                )
                continue
            contract = parse_skill_text(text, source=source, path=uri)
            if contract is None:
                errors.append(
                    SkillContractError(id=uri[len(MCP_SKILL_URI_SCHEME):], path=uri,
                                       violations=("no YAML frontmatter",))
                )
                continue
            violations = contract_violations(contract)
            if violations:
                errors.append(
                    SkillContractError(id=contract.id or "?", path=uri, violations=violations)
                )
            else:
                contracts.append(contract)
    loaded = LibraryLoad(contracts=tuple(contracts), invalid=tuple(errors))
    with _contract_lock():
        _MCP_SKILL_CACHE[cache_key] = loaded
    return loaded


def load_skill_contracts() -> LibraryLoad:
    """EVERY skill this runtime can select: native + plugin-typed + MCP, one validated set."""
    native = load_native_library()
    plugin = load_plugin_typed_contracts()
    mcp = load_mcp_skill_contracts()
    seen: set[str] = set()
    contracts: list[SkillContract] = []
    conflicts: list[SkillContractError] = []
    for contract in (*native.contracts, *plugin.contracts, *mcp.contracts):
        if contract.id in seen:
            # First source wins deterministically (native > plugin > mcp by load order); a
            # duplicate id across sources is reported rather than silently shadowed.
            conflicts.append(
                SkillContractError(
                    id=contract.id, path=contract.path,
                    violations=(f"duplicate skill id across sources (kept first, dropped {contract.source})",),
                )
            )
            continue
        seen.add(contract.id)
        contracts.append(contract)
    return LibraryLoad(
        contracts=tuple(contracts),
        invalid=tuple((*native.invalid, *plugin.invalid, *mcp.invalid, *conflicts)),
    )


def _frontmatter_of(path: Path) -> dict[str, Any]:
    """The parsed YAML frontmatter of a SKILL.md, through the same loader the plugins use."""
    from core.plugin_skills import _FRONTMATTER_RE

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(match.group(1))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Operator configuration: enable / disable / inspect / inventory
# ---------------------------------------------------------------------------


def _enabled_store_path() -> Path:
    from core.runtime_paths import active_vool_home

    return active_vool_home() / "config" / "skills_enabled.json"


def _read_disabled_ids() -> set[str]:
    """Read from disk on EVERY call: configuration has no process-local copy, so a restart
    preserves it by construction and two turns cannot disagree about it."""
    try:
        data = json.loads(_enabled_store_path().read_text(encoding="utf-8"))
        return {str(x).strip() for x in (data.get("disabled") or []) if str(x).strip()}
    except Exception:
        return set()


def disabled_skill_ids() -> set[str]:
    """The operator's disabled set — the ONE disable authority, read live from disk.

    Public because the plugin-lexical ranker in ``core.tool_offer_assembly`` filters through
    this same store: a legacy (non-typed) installed skill is disabled by its NAME here, exactly
    like a typed contract is disabled by its id. Two stores would be two truths about "off".
    """
    return _read_disabled_ids()


def set_skill_enabled(skill_id: str, enabled: bool) -> dict[str, Any]:
    """Persist one skill's enabled state, WHATEVER source it came from (native, plugin, MCP,
    or a legacy installed skill — the last disabled by its name, read back by the same store)."""
    skill_id = str(skill_id or "").strip()
    library = load_skill_contracts()
    known = {c.id for c in library.contracts}
    if skill_id not in known:
        # Legacy installed skills (no typed frontmatter) are not in the contract library, but
        # they are real skills a real turn can select — they disable through the same store.
        try:
            from core.tool_offer_assembly import loaded_skills

            known |= {str(skill.name or "").strip() for skill in loaded_skills()}
        except Exception:
            pass
    if skill_id not in known:
        return {
            "status": "unknown_skill",
            "reason": f"no skill with id {skill_id!r} in any source",
            "known_ids": sorted(known)[:50],
        }
    disabled = _read_disabled_ids()
    if enabled:
        disabled.discard(skill_id)
    else:
        disabled.add(skill_id)
    path = _enabled_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"disabled": sorted(disabled)}), encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not persist skill state"}
    return {"status": "ok", "id": skill_id, "enabled": bool(enabled)}


def contract_to_dict(contract: SkillContract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "version": contract.version,
        "name": contract.name,
        "description": contract.description,
        "risk_class": contract.risk_class,
        "task_families": list(contract.task_families),
        "capability_families": list(contract.capability_families),
        "tool_intents": list(contract.tool_intents),
        "permitted_tools": list(contract.permitted_tools),
        "prerequisites": list(contract.prerequisites),
        "expected_outputs": list(contract.expected_outputs),
        "verification": list(contract.verification),
        "stopping_conditions": list(contract.stopping_conditions),
        "incompatible_with": list(contract.incompatible_with),
        "capabilities": list(contract.capabilities),
        "triggers": list(contract.triggers),
        "priority": contract.priority,
        "path": contract.path,
    }


def _unmet_capabilities(contract: SkillContract) -> tuple[str, ...]:
    """Which declared capabilities this runtime cannot honour, judged against the real graph.

    Gate adopted from the concurrent sibling lane's design (plugin_skills._absent_capabilities,
    preserved at e21c7956): a package whose declared capabilities the runtime cannot deliver is
    refused with the absence NAMED — never silently skipped, never injected as a promise the
    runtime cannot keep. Declaring is not granting; this is availability truth, not permission.
    """
    if not contract.capabilities:
        return ()
    try:
        from core.capability_graph import (
            capabilities_for_skill,
            ensure_registry_bootstrap,
            implementations_for_capability,
        )

        # Judge against the graph the runtime ACTUALLY bootstrapped — an unbootstrapped graph
        # answers "nothing exists", which would refuse every skill on a healthy machine.
        ensure_registry_bootstrap()
        absent: list[str] = []
        for requirement in contract.capabilities:
            resolved = capabilities_for_skill("", [requirement])
            available = any(
                implementation.available
                for capability in resolved
                for implementation in implementations_for_capability(capability)
            )
            if not resolved:
                # UNKNOWN, not unavailable. The distinction is the whole difference
                # between "this machine cannot do it today" and "this name is not a
                # capability at all, so the skill can never be selected on any
                # machine". The database skill sat unreachable for exactly this
                # reason and the record said only "unavailable capabilities", which
                # reads as an environment problem and hid a contract typo.
                absent.append(f"{requirement} (unknown capability id)")
            elif not available:
                absent.append(requirement)
        return tuple(absent)
    except Exception:
        # A graph that cannot answer is not a graph that has said "available" — refuse honestly.
        return tuple(contract.capabilities)


def _unmet_prerequisites(contract: SkillContract) -> list[str]:
    unmet: list[str] = []
    for prereq in contract.prerequisites:
        if prereq.startswith("tool:"):
            intent = prereq[len("tool:"):].strip()
            try:
                from core.runtime_tool_contracts import runtime_tool_contract_map

                if intent not in runtime_tool_contract_map():
                    unmet.append(prereq)
            except Exception:
                unmet.append(prereq)
        elif prereq.startswith("binary:"):
            if shutil.which(prereq[len("binary:"):].strip()) is None:
                unmet.append(prereq)
        elif prereq.startswith("family:"):
            family = prereq[len("family:"):].strip()
            try:
                from core.capability_graph import canonical_family_ids

                if family not in canonical_family_ids():
                    unmet.append(prereq)
            except Exception:
                unmet.append(prereq)
        elif prereq.startswith("plugin:"):
            plugin_id = prereq[len("plugin:"):].strip()
            try:
                from core.tool_offer_assembly import _plugin_dirs

                installed = {pid for pid, _dir in _plugin_dirs()}
            except Exception:
                installed = set()
            if plugin_id not in installed:
                unmet.append(prereq)
    return unmet


def inspect_skill(skill_id: str) -> dict[str, Any]:
    """The full typed contract of one skill (any source) plus its live state."""
    skill_id = str(skill_id or "").strip()
    library = load_skill_contracts()
    contract = next((c for c in library.contracts if c.id == skill_id), None)
    if contract is None:
        error = next((e for e in library.invalid if e.id == skill_id), None)
        if error is not None:
            return {
                "status": "invalid_contract",
                "id": skill_id,
                "source": error.path,
                "violations": list(error.violations),
                "path": error.path,
            }
        return {"status": "unknown_skill", "reason": f"no skill with id {skill_id!r} in any source"}
    unmet = _unmet_prerequisites(contract)
    absent_capabilities = _unmet_capabilities(contract)
    disabled = skill_id in _read_disabled_ids()
    available = not disabled and not unmet and not absent_capabilities
    reason = ""
    if disabled:
        reason = "disabled by operator"
    elif unmet:
        reason = f"unmet prerequisites: {unmet}"
    elif absent_capabilities:
        reason = f"unavailable capabilities: {absent_capabilities}"
    return {
        "status": "ok",
        "id": contract.id,
        "version": contract.version,
        "enabled": not disabled,
        "available": available,
        "reason": reason,
        "contract": contract_to_dict(contract),
    }


def skill_inventory(root: Path | None = None) -> list[dict[str, Any]]:
    """Every skill (native + plugin-typed + MCP) with version + live state: the projection
    the catalog and API read. `root` narrows to one native root (tests); None means all sources."""
    if root is not None:
        library = load_native_library(root)
    else:
        library = load_skill_contracts()
    disabled = _read_disabled_ids()
    rows: list[dict[str, Any]] = []
    for contract in library.contracts:
        unmet = _unmet_prerequisites(contract)
        absent_capabilities = _unmet_capabilities(contract)
        is_disabled = contract.id in disabled
        available = not is_disabled and not unmet and not absent_capabilities
        if is_disabled:
            reason = "disabled by operator"
        elif unmet:
            reason = f"unmet prerequisites: {unmet}"
        elif absent_capabilities:
            reason = f"unavailable capabilities: {absent_capabilities}"
        else:
            reason = ""
        rows.append(
            {
                "id": contract.id,
                "name": contract.name,
                "description": contract.description,
                "version": contract.version,
                "risk_class": contract.risk_class,
                "source": contract.source,
                "enabled": not is_disabled,
                "available": available,
                "reason": reason,
                "task_families": list(contract.task_families),
                "capability_families": list(contract.capability_families),
            }
        )
    for error in library.invalid:
        # An invalid row whose id a VALID contract already owns is the cross-source duplicate
        # report (native kept, plugin/mcp dropped) -- not a second state of that skill. The
        # inventory is keyed by id, so emitting it here would shadow the valid row for every
        # consumer that looks the skill up, presenting an available skill as broken. The
        # duplicate stays visible where it belongs: on LibraryLoad.invalid (the load's own
        # report) and on the catalog's duplicates/provenance fields.
        if any(contract.id == error.id for contract in library.contracts):
            continue
        rows.append(
            {
                "id": error.id,
                "version": "",
                "enabled": False,
                "available": False,
                "reason": "contract invalid: " + "; ".join(error.violations),
                "task_families": [],
                "capability_families": [],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Typed selection
# ---------------------------------------------------------------------------

DEFAULT_MAX_SELECTED = 3
#: Doctrine slots, held SEPARATELY from task slots. Two, because the measured
#: advisory pairing is the presentation doctrine plus the strongest lens for the class.
DEFAULT_MAX_DOCTRINE = 2


def skill_cards(contracts: Any = None) -> tuple[Any, ...]:
    """Each contract's CARD as a ``core.plugin_skills.Skill``: its id as the name, its
    description, and its triggers (hollow phrases left out, see ``_hollow_trigger``).

    What selection matches words against, and what the plugin lane counts as the built-in
    library when it judges how rare a word is. ``contracts`` defaults to every loaded contract.
    """
    from core.plugin_skills import Skill

    if contracts is None:
        try:
            contracts = load_skill_contracts().contracts
        except Exception:
            contracts = ()
    return tuple(
        Skill(
            name=contract.id,
            description=contract.description,
            triggers=tuple(trigger for trigger in contract.triggers if not _hollow_trigger(trigger)),
        )
        for contract in contracts
    )


def _word_evidence(contracts: list[SkillContract], user_text: str) -> dict[str, Any]:
    """contract id -> the ask's word evidence for it, read from the skill's OWN words.

    The card a skill is matched on is what its author wrote about when it applies: its id
    (the name a person says), its description (the "Use when ..." sentence) and its
    ``triggers``. Never its body: instructions are written for the model, and every body says
    "read", "run" and "report". The scoring is the one lexical path the plugin lanes already
    use (``core.plugin_skills.word_evidence``), with rarity judged across THIS turn's
    candidates, so a word every built-in skill carries says little and one word only one skill
    carries says a lot; its "none fits" guards (a question about the world, the subject after
    "about", kind-of-help words alone) apply here unchanged.
    """
    from core.plugin_skills import WordEvidence, _raw_tokens, word_evidence

    text = str(user_text or "")
    if not contracts or not text.strip():
        return {}
    found = dict(word_evidence(skill_cards(contracts), text))
    # A hollow trigger counts only as the author wrote it, word for word: "where in the code",
    # "before i push", "is it true". Said verbatim it is the author's own wording, so it anchors
    # the skill even inside a question.
    spoken = " " + " ".join(_raw_tokens(text)) + " "
    for contract in contracts:
        said = [
            trigger for trigger in contract.triggers
            if _hollow_trigger(trigger) and f" {' '.join(_raw_tokens(trigger))} " in spoken
        ]
        if not said:
            continue
        before = found.get(contract.id)
        found[contract.id] = WordEvidence(
            qualified=True,
            score=(before.score if before else 0.0) + _HOLLOW_PHRASE_WORTH * len(said),
            support=(before.support if before else 0.0) + _HOLLOW_PHRASE_WORTH * len(said),
            reasons=(*(before.reasons if before else ()), *(f'trigger "{trigger}"' for trigger in said)),
            anchored=True,
            distinct=max(1, before.distinct if before else 0),
        )
    return found


#: What a hollow trigger said word for word is worth: a whole multi-word trigger in the lexical
#: ranker is worth its phrase bonus plus its words, about one point.
_HOLLOW_PHRASE_WORTH = 1.0


def _words_pick(found: Any) -> bool:
    """The ask's words pick the skill.

    The lexical law qualifies it -- or it would, and only the general-question guard held it
    back while the ask still names the skill, carries a whole trigger, or meets its card on
    three or more different words. That guard keeps questions about the world ("why do cats
    sleep so much") away from everyday skills; "why does the checkout test fail on Windows?" or
    "which commit changed the payments file last week" are questions about the person's own work
    in the skill's own words.
    """
    if found is None:
        return False
    if found.qualified:
        return True
    return bool(getattr(found, "general", False) and (found.anchored or found.distinct >= 3))


def _hollow_trigger(trigger: str) -> bool:
    """A phrase that keeps one content word once its function words go.

    "why is this broken" was written as a phrase; read as the single word "broken" it claims
    "the boiler has been broken for a week" (measured: root-cause-repair seated on a letter to a
    landlord). A trigger the author wrote as one word stays a word; a phrase that leaves one word
    behind counts only when the ask says it word for word (``_word_evidence``).
    """
    from core.plugin_skills import _content_stems, _raw_tokens

    return len(_raw_tokens(trigger)) > 1 and len(set(_content_stems(trigger, particles=True))) <= 1


def _strong_words(found: Any) -> bool:
    """Words that say what the skill does, not one word it happens to share.

    The ask names the skill, carries one of its multi-word triggers whole, or meets its card on
    two or more different words, at least one of them from its name or triggers. One shared word
    ("review the book I just finished", "we're migrating to Spain") is ordinary English, and so
    are two words of a long description ("whether", "time"); those need the turn's class.
    """
    return bool(
        _words_pick(found)
        and (found.anchored or (found.distinct >= 2 and getattr(found, "keyed", True)))
    )


#: How a skill is seated when the turn has words (``_seating_law``).
LAW_TYPED = "typed"
LAW_TASK_WORDS = "task_words"
LAW_LENS_WORDS = "lens_words"


def _seating_law(contract: SkillContract) -> str:
    """Which evidence seats this skill on a turn that has words to read.

    ``task_words`` -- a TASK skill that wrote down its ``triggers``. It does a job, so the job
    must be asked for: the router's class is a guess at the turn's broad shape, and one class is
    claimed by many task skills (five claim ``debugging``), so the class alone cannot say which
    job. The words pick it; the class only backs them.

    ``lens_words`` -- an EXPERT LENS that wrote down its ``triggers``. A lens is, by its own
    contract, automatic on the turns of its class, so the class still seats it -- when the ask
    also shows it is that kind of turn: it shares the lens's words, or it is a yes-or-no /
    either-or question about the person's own case (the decision a lens weighs). Measured before
    this law: every ``general_advisory`` turn got the same evidence lens, "how long should I
    boil an egg" included, because the class alone seated whichever lens had the lowest priority.

    ``typed`` -- everything else keeps the typed law it was written against (class and demand):
    a doctrine or task skill that declares no ``triggers`` (the presentation doctrine, which is
    meant for every turn of its classes; an installed plugin's or MCP server's typed contract
    written before this law); and a skill whose prerequisite is a plugin pack, which works only
    through that pack's tools, whose typed demand rules own the line between doing the work and
    talking about it ("back up my database" is work, "what is a database index?" is context) --
    the pack's own copy of the skill is ranked by words in the plugin lane.
    """
    if not contract.triggers:
        return LAW_TYPED
    if getattr(contract, "kind", KIND_CAPABILITY) == KIND_EXPERT_LENS:
        return LAW_LENS_WORDS
    if any(prereq.startswith("plugin:") for prereq in contract.prerequisites):
        return LAW_TYPED
    return LAW_TASK_WORDS


_CLAUSE_RE = re.compile(r"[,.;:!?\n]+")


def _plain_question(user_text: str) -> bool:
    """A question about the world that points at nothing of the person's: "how long should I
    boil an egg", "the pipe under my sink is leaking, who should I call".

    The classifier still files such a question under a class ("general_advisory", "research",
    "security_hardening"), but when not one skill's words answer to it, the class is a guess
    about its topic, not a job: the model answers it plainly. A request ("write ...", "here are
    my notes, give me ..."), a question that is a request ("can you ...", "do a ..."), and a
    question about the person's own file, page or pasted material are not plain questions.
    """
    from core.plugin_skills import _QUESTION_OPENERS, _REQUEST_RE, _points_at_material, _raw_tokens

    tokens = _raw_tokens(user_text)
    if not tokens or _points_at_material(user_text, tokens, None):
        return False
    asked = False
    for clause in _CLAUSE_RE.split(str(user_text or "")):
        words = _raw_tokens(clause)
        if not words:
            continue
        if _REQUEST_RE.match(" ".join(words)) or (
            words[0] == "do" and len(words) > 1 and words[1] in {"a", "an", "some"}
        ):
            return False
        asked = asked or words[0] in _QUESTION_OPENERS
    return asked


def _verdict_question(user_text: str) -> bool:
    """A question that opens on its verb: "should I take ...", "is it worth ...", "can we ...".

    The same reading the lexical ranker uses for a yes-or-no question about the person's own
    case (``core.plugin_skills._VERDICT_OPENERS``); a request phrased as a question ("can you
    ...") and "do a ..." are requests, not verdicts.
    """
    from core.plugin_skills import _REQUEST_RE, _VERDICT_OPENERS, _raw_tokens

    tokens = _raw_tokens(user_text)
    if not tokens or tokens[0] not in _VERDICT_OPENERS:
        return False
    if _REQUEST_RE.match(" ".join(tokens)):
        return False
    return not (tokens[0] == "do" and len(tokens) > 1 and tokens[1] in {"a", "an", "some"})


def select_native_skills(
    *,
    task_class: str = "",
    user_text: str = "",
    limit: int = DEFAULT_MAX_SELECTED,
    doctrine_limit: int = DEFAULT_MAX_DOCTRINE,
    library: LibraryLoad | None = None,
) -> SkillSelection:
    """The selection decision for one turn. Pure function of the turn + disk config.

    Signals: the turn's task class (the production classifier's output), its demand signals
    (``core.tool_demand_signals`` -- explicit intents and required families), and the ask's own
    words weighed against each skill's own words (``_word_evidence``: id, description,
    ``triggers``). The candidate set is EVERY source: repo-native, plugin-typed and MCP-shipped
    skills -- one contract, one law, one selection.

    Who may take a seat on a turn with words (``_seating_law`` says which law a skill is under):

    - a TASK skill with ``triggers``: when its words pick it strongly (``_strong_words``), when
      its class backs words of its own card, or when a demand signal points at it alone (an
      intent or family no other candidate declares). Measured before (class alone): "my CI is
      red, fix it" classed ``unknown`` and got nothing, "open example.com and tell me what it
      says" got project-archaeology through a ``workspace.read_file`` demand seven skills share,
      and every ``debugging`` turn got whichever debugging skill had the lowest priority number;
    - an EXPERT LENS with ``triggers``: on its class when the ask shows it is that kind of turn
      (the lens's words, or a verdict question), or on strong words of its own;
    - everything else -- the presentation doctrine, trigger-less typed contracts, a plugin
      pack's skill -- on its class or demand, as before.

    Within a lane, an ask that names a task skill or carries a whole trigger of it ranks that
    skill first; otherwise typed weight plus word score, then the declared priority, then the
    id. The doctrine lane's first seat stays the one its class alone gives it. With no words at
    all (a caller that only knows the class) the class alone still seats, as it always did.
    """
    from core.tool_demand_signals import resolve_demand_signals

    library = library or load_skill_contracts()
    if not library.contracts and not library.invalid:
        return SkillSelection(selected=(), records=())

    cleaned_class = str(task_class or "").strip().lower()
    demands = resolve_demand_signals(str(user_text or ""))
    explicit_intents = frozenset(demands.explicit_intents)
    required_families = frozenset(demands.required_families)

    disabled = _read_disabled_ids()
    invalid_ids = {error.id: error for error in library.invalid}
    records: list[SkillAvailabilityRecord] = []
    scored: list[tuple[int, float, int, SkillContract]] = []
    class_rank: dict[str, tuple[int, int, str]] = {}

    for error in invalid_ids.values():
        records.append(
            SkillAvailabilityRecord(
                id=error.id,
                state="contract_invalid",
                reason="; ".join(error.violations),
            )
        )

    available: list[SkillContract] = []
    for contract in library.contracts:
        if contract.id in disabled:
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id, state="disabled", reason="disabled by operator",
                    version=contract.version,
                )
            )
            continue
        unmet = _unmet_prerequisites(contract)
        absent_capabilities = _unmet_capabilities(contract)
        if unmet or absent_capabilities:
            reason_bits = []
            if unmet:
                reason_bits.append(f"unmet prerequisites: {unmet}")
            if absent_capabilities:
                reason_bits.append(f"unavailable capabilities: {absent_capabilities}")
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id,
                    state="prerequisite_missing" if unmet else "capability_unavailable",
                    reason="; ".join(reason_bits),
                    version=contract.version,
                )
            )
            continue
        available.append(contract)

    from core.plugin_skills import _content_stems

    has_words = bool(_content_stems(str(user_text or "")))
    verdict = has_words and _verdict_question(str(user_text or ""))
    evidence = _word_evidence(available, str(user_text or "")) if has_words else {}
    # How many available candidates declare each demanded intent / family: a demand only one
    # skill declares points at that skill; one that seven skills declare points at none of them.
    claimants: dict[str, int] = {}
    for contract in available:
        for name in (explicit_intents & frozenset(contract.tool_intents)) | (
            required_families & frozenset(contract.capability_families)
        ):
            claimants[name] = claimants.get(name, 0) + 1

    # First pass: what each candidate's words (and typed signals) say on their own.
    judged: list[tuple] = []
    for contract in available:
        class_hit = bool(cleaned_class) and cleaned_class in contract.task_family_set
        intent_hits = explicit_intents & frozenset(contract.tool_intents)
        family_hits = required_families & frozenset(contract.capability_families)
        found = evidence.get(contract.id)
        law = _seating_law(contract)
        words_pick = _words_pick(found)
        word_worth = 0.0 if found is None else float(found.score if words_pick else found.support)
        typed = (
            (_WEIGHT_TASK_CLASS if class_hit else 0)
            + _WEIGHT_DEMAND * len(intent_hits)
            + _WEIGHT_DEMAND * len(family_hits)
        )
        if not has_words:
            # Nothing to read: the typed signals alone, as they always decided.
            seated = typed > 0
            reason = "no typed signal ties this skill to the turn"
        elif law == LAW_TYPED:
            # The typed law alone; a doctrine may also be picked by strong words of its own.
            seated = typed > 0 or (contract.kind == KIND_EXPERT_LENS and _strong_words(found))
            reason = "no typed signal ties this skill to the turn"
        elif law == LAW_LENS_WORDS:
            # A lens: strong words of its own seat it; its class seats it when the ask shows it
            # is that kind of turn (its words, or a verdict question).
            shaped = words_pick or (found is not None and found.distinct >= 2) or verdict
            seated = _strong_words(found) or (typed > 0 and shaped)
            reason = "the turn's class and words do not call for this lens"
        else:
            # A task skill: the words pick it and are strong on their own; or its class backs
            # words that pick it, or two different words of its own card; or a demand signal
            # points at it alone.
            singled_out = any(claimants.get(name, 0) == 1 for name in intent_hits | family_hits)
            backed = class_hit and (words_pick or (found is not None and found.distinct >= 2))
            seated = _strong_words(found) or backed or singled_out
            reason = "the ask's words do not ask for what this skill does"
        judged.append((contract, seated, class_hit, word_worth, typed, law, words_pick, reason, found))

    # Where the words are silent, the class still speaks. When the words seat no task skill and
    # no lens, the class seats the task skills and the lens it names, as it always did ("here
    # are my meeting notes, give me the action items" classed debugging; "Write an X article"
    # classed creative_ideation). Words decide WHICH skill when they say anything; and a plain
    # question no skill's words answer to is answered plainly (``_plain_question``).
    if has_words and not _plain_question(str(user_text or "")) and not any(
        seated and law in (LAW_TASK_WORDS, LAW_LENS_WORDS)
        for (_c, seated, _h, _w, _t, law, *_rest) in judged
    ):
        silent = {LAW_TASK_WORDS, LAW_LENS_WORDS}
    else:
        silent = set()

    by_class_only: set[str] = set()
    for contract, seated, class_hit, word_worth, typed, law, words_pick, reason, found in judged:
        if not seated and law in silent:
            seated = class_hit if law == LAW_TASK_WORDS else typed > 0
            if seated:
                by_class_only.add(contract.id)
        if seated:
            # Typed signals keep their weights and the words add their own worth, so among the
            # seated skills of a lane the one the ask describes best comes first. An ask that
            # names a task skill or carries one of its whole trigger phrases states the job
            # outright and ranks ahead of a skill its class merely shares ("give my last commit
            # a review" is classed debugging; the review skill, not the history one, is asked
            # for). A lens the words pick outright counts them twice: several lenses claim a
            # class, and the words say which one the turn is about. Lower declared priority
            # breaks exact ties; the id is the final deterministic word.
            anchored = bool(words_pick and found.anchored)
            tier = 0 if (anchored and has_words and law == LAW_TASK_WORDS) else 1
            if contract.kind == KIND_EXPERT_LENS and _strong_words(found):
                word_worth *= 2
            scored.append((tier, -(typed + word_worth), contract.priority, contract))
            class_rank[contract.id] = (-typed, contract.priority, contract.id)
        else:
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id, state="below_cutoff", reason=reason,
                    version=contract.version,
                )
            )

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3].id))
    ordered = [item[3] for item in scored]
    # The doctrine lane's FIRST seat stays the one its class alone gives it (the presentation
    # doctrine on an advisory turn, the security lens on a hardening turn): words choose which
    # lens fills the rest, they do not turn one lens into two at the doctrine's expense.
    doctrine_at = [i for i, contract in enumerate(ordered) if lane_for(contract) == LANE_DOCTRINE]
    by_class = [ordered[i] for i in doctrine_at if class_rank[ordered[i].id][0] < 0]
    if doctrine_at and by_class:
        first = min(by_class, key=lambda contract: class_rank[contract.id])
        rest = [ordered[i] for i in doctrine_at if ordered[i] is not first]
        for slot, contract in zip(doctrine_at, [first, *rest], strict=True):
            ordered[slot] = contract

    # Slots are PER LANE. A doctrine and a task skill are not substitutes, so they
    # never take each other's seat; within a lane the ranking is unchanged.
    lane_budget = {
        LANE_TASK: max(1, int(limit)),
        LANE_DOCTRINE: max(1, int(doctrine_limit)),
    }
    lane_taken: dict[str, int] = {LANE_TASK: 0, LANE_DOCTRINE: 0}

    selected: list[SkillContract] = []
    for contract in ordered:
        lane = lane_for(contract)
        if lane_taken[lane] >= lane_budget[lane]:
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id, state="below_cutoff",
                    reason=(
                        f"the {lane} lane's selection budget is {lane_budget[lane]}; "
                        "this skill ranked lower within its own lane"
                    ),
                    version=contract.version,
                )
            )
            continue
        blocked = frozenset(contract.incompatible_with) & {c.id for c in selected}
        if blocked:
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id,
                    state="incompatible",
                    reason=f"incompatible with co-selected skills: {sorted(blocked)}",
                    version=contract.version,
                )
            )
            continue
        selected.append(contract)
        lane_taken[lane] += 1

    return SkillSelection(
        selected=tuple(selected),
        records=tuple(records),
        by_class_only=frozenset(by_class_only & {contract.id for contract in selected}),
    )


def guidance_for_selection(
    selected: tuple[SkillContract, ...],
    *,
    task_chars: int | None = None,
    doctrine_chars: int | None = None,
) -> tuple[str, tuple[dict[str, Any], ...], tuple[str, ...]]:
    """(text, provenance rows, permitted-tools union) in the shape the offer seam renders.

    TWO LAWS, both previously broken:

    COMPLETE OR ABSENT -- never truncated. The old packer handed each skill whatever
    budget the previous one left and cut its body mid-word with an ellipsis. Measured
    on a real debugging turn, ``root-cause-repair`` reached the provider as **54 of
    1760 characters** -- its whole workflow, its tool-door rule and the symptom-only
    stopping condition gone -- while the ledger recorded "root-cause-repair v1.0.0
    influenced this turn". ``REQUIRED_BODY_TOKENS`` is enforced at LOAD against the
    full body and nothing re-checked what actually reached the wire, so a contract
    could pass its doctrine law and ship without the doctrine. A body that does not
    fit is now DROPPED and recorded, because half a doctrine is not a weaker
    doctrine, it is a different one.

    PER-LANE BUDGETS. Doctrine and task skills draw on separate character budgets
    for the same reason they hold separate slots.

    The provenance rows carry the SOURCE of every contract (``origin``: native |
    plugin | mcp, and the precise ``source`` string) plus what actually happened to
    it -- ``body_chars`` versus ``chars``, and ``complete`` -- so no row can claim a
    skill influenced a turn more than it did.
    """
    from core.tool_offer_assembly import (
        MAX_DOCTRINE_CHARS,
        MAX_SKILL_CHARS,
        render_complete_block,
    )

    blocks: list[str] = []
    provenance: list[dict[str, Any]] = []
    permitted: list[str] = []
    remaining = {
        LANE_TASK: int(MAX_SKILL_CHARS if task_chars is None else task_chars),
        LANE_DOCTRINE: int(MAX_DOCTRINE_CHARS if doctrine_chars is None else doctrine_chars),
    }
    for contract in selected:
        lane = lane_for(contract)
        block = render_complete_block(_AsSkill(contract), remaining[lane])
        origin = "native"
        if contract.source.startswith("plugin:"):
            origin = "plugin"
        elif contract.source.startswith("mcp:"):
            origin = "mcp"
        body_chars = len(str(contract.body or "").strip())
        if block is None:
            # Recorded, not silent: the turn's account says which skill did not fit
            # and how far over its lane's budget it was.
            provenance.append(
                {
                    "name": contract.id,
                    "plugin_id": contract.source if origin != "native" else "native-library",
                    "origin": origin,
                    "source": contract.source,
                    "version": contract.version,
                    "path": contract.path,
                    "kind": contract.kind,
                    "lane": lane,
                    "chars": 0,
                    "body_chars": body_chars,
                    "complete": False,
                    "dropped": "over_lane_char_budget",
                    "lane_chars_remaining": int(remaining[lane]),
                }
            )
            continue
        used = len(block)
        blocks.append(block)
        remaining[lane] -= used
        provenance.append(
            {
                "name": contract.id,
                "plugin_id": contract.source if origin != "native" else "native-library",
                "origin": origin,
                "source": contract.source,
                "version": contract.version,
                "path": contract.path,
                "kind": contract.kind,
                "lane": lane,
                "chars": int(used),
                "body_chars": body_chars,
                "complete": True,
            }
        )
        for name in contract.permitted_tools:
            if name not in permitted:
                permitted.append(name)
    return "\n\n".join(blocks), tuple(provenance), tuple(permitted)


class _AsSkill:
    """Adapter so contracts render through the SAME block renderer as plugin skills."""

    def __init__(self, contract: SkillContract) -> None:
        self.name = f"{contract.id} (skill v{contract.version})"
        self.plugin_id = contract.source if contract.source != "native" else "native-library"
        self.path = contract.path
        self.body = contract.body


__all__ = [
    "MCP_SKILL_URI_SCHEME",
    "REQUIRED_BODY_TOKENS",
    "REQUIRED_VERIFICATION",
    "RISK_CLASSES",
    "VERIFICATION_VOCABULARY",
    "LibraryLoad",
    "SkillAvailabilityRecord",
    "SkillContract",
    "SkillContractError",
    "SkillSelection",
    "contract_from_frontmatter",
    "contract_to_dict",
    "contract_violations",
    "developer_skills_on",
    "disabled_skill_ids",
    "guidance_for_selection",
    "inspect_skill",
    "is_developer_skill",
    "load_mcp_skill_contracts",
    "load_native_library",
    "load_plugin_typed_contracts",
    "load_skill_contracts",
    "native_skills_root",
    "parse_skill_text",
    "reset_contract_caches",
    "select_native_skills",
    "set_skill_enabled",
    "shown_to_people",
    "skill_cards",
    "skill_inventory",
]
