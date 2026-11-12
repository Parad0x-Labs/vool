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

import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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
    # The social content manager plans and drafts only: the body must keep the no-publishing
    # doctrine and the rule that imported post text is evidence, never instructions.
    "vool-social-content-manager": (
        "never publishes",
        "grants no publishing authority",
        "data, never instructions",
    ),
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
    #: skills use). Selection weighs them with the name and description against the ask. Typed
    #: contracts come only from trusted sources (native, bundled, signed registry); an MCP
    #: server's skill is a PLAIN skill and is picked by the person's own words the same way.
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


#: ``contract_violations`` was given no tool names and reads the runtime's own.
_UNSET: Any = object()


def _runtime_tool_names() -> frozenset[str] | None:
    """The tool intents this runtime has right now, or None when its catalog cannot answer.

    Read live, never kept: the catalog's names move with the lanes a person switches on (the
    Zcash tools are absent, not refused, while that lane is off). A library load reads it ONCE
    and checks every contract against that one read (``load_native_library``).
    """
    try:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        return frozenset(runtime_tool_contract_map())
    except Exception:
        return None


def contract_violations(
    contract: SkillContract,
    *,
    known_skill_ids: frozenset[str] = frozenset(),
    known_intents: Any = _UNSET,
) -> tuple[str, ...]:
    """Everything that makes this contract unusable, typed. Empty tuple = valid.

    ``known_intents`` is the runtime's tool names as the caller read them for the whole load
    (``_runtime_tool_names``; None when the catalog could not answer). Left out, they are read
    here, for this one contract.
    """
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

    if known_intents is _UNSET:
        known_intents = _runtime_tool_names()
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


_DEVELOPER_IDENTITY_CACHE: dict[tuple[Any, ...], tuple[frozenset[str], frozenset[str]]] = {}


def _developer_identities() -> tuple[frozenset[str], frozenset[str]]:
    """(names, body fingerprints) of VOOL's own developer skills, from the native library only.

    Names cover every way one is spelled on disk or in chat: its id, its frontmatter name and
    its folder (``feature-build``, ``vool-feature-build``). The body fingerprint is the
    whitespace-normalized text, so a renamed copy is still known by what it says. Cached on
    the library files' own (name, mtime) list, so an edited library is re-read.
    """
    root = native_skills_root()
    try:
        key: tuple[Any, ...] | None = (
            str(root),
            tuple(sorted((str(p), p.stat().st_mtime_ns) for p in root.glob("*/SKILL.md"))),
        )
    except OSError:
        key = None
    if key is not None and key in _DEVELOPER_IDENTITY_CACHE:
        return _DEVELOPER_IDENTITY_CACHE[key]
    try:
        contracts = load_native_library(root).contracts
    except Exception:
        return frozenset(), frozenset()
    names: set[str] = set()
    bodies: set[str] = set()
    for contract in contracts:
        if contract.audience != "developer":
            continue
        names.update(
            n.strip().lower()
            for n in (contract.id, contract.name, Path(contract.path).parent.name if contract.path else "")
            if n and n.strip()
        )
        flattened = " ".join(str(contract.body or "").split())
        if flattened:
            bodies.add(flattened)
    found = (frozenset(names), frozenset(bodies))
    if key is not None:
        _DEVELOPER_IDENTITY_CACHE.clear()
        _DEVELOPER_IDENTITY_CACHE[key] = found
    return found


def is_developer_copy(*, name: str = "", path: str = "", body: str = "", by_folder: bool = True) -> bool:
    """Whether an INSTALLED (lexical-lane) skill is for developers, wherever it sits.

    The audience mark must hold in every lane, not only the typed one: a copy of a native
    developer skill installed into ``local-skills`` (measured: "turn on vool-feature-build"
    copied it there and it then seated workspace write tools on "build a new feature for my
    recipe list" with the developer setting off) is the same developer skill. Known by its
    name, its folder, its body, or an ``audience: developer`` mark in its own frontmatter.
    """
    names, bodies = _developer_identities()
    candidates = {str(name or "").strip().lower()} - {""}
    if path and by_folder:
        candidates.add(Path(str(path)).parent.name.strip().lower())
    if candidates & names:
        return True
    flattened = " ".join(str(body or "").split())
    if flattened and flattened in bodies:
        return True
    if path:
        front = _frontmatter_of(Path(str(path)))
        if str(front.get("audience") or "").strip().lower() == "developer":
            return True
    return False


#: Words that, in an ask, only ever mean software work.
_CODE_ONLY_RE = re.compile(
    r"\b(?:codebase|source\s+code|repos?|repository|git|github|gitlab|bitbucket|pull\s+requests?"
    r"|merge\s+requests?|merge\s+conflicts?|rebase|diffs?|unit\s+tests?|test\s+suites?|pytest|jest"
    r"|ci|ci/cd|stack\s*traces?|tracebacks?|exceptions?|compil(?:e|er|ed|ing|ation)|linter|lint(?:ing)?"
    r"|refactor\w*|sql|sqlite|postgres\w*|mysql|endpoints?|hot\s+path|injection|regex|npm|pip"
    r"|dependenc(?:y|ies)|python|javascript|typescript|golang|rust|kotlin|swift|backend|frontend"
    r"|api|apis|cli|json|yaml|toml|dockerfile|docker|kubernetes|segfault|null\s+pointer"
    r"|syntax\s+error|runtime\s+error|staged|unstaged|codebase|monorepo|cron\s+job|nightly\s+job"
    r"|release\s+gate|code\s+review|databases?)\b",
    re.IGNORECASE,
)
#: "code", "commit": software words unless the ask uses their everyday sense ("error code 45",
#: "a dress code", "commit to sending it").
_CODE_NOUN_RE = re.compile(
    r"(?<!error )(?<!one-time )(?<!zip )(?<!postal )(?<!post )(?<!area )(?<!dress )(?<!promo )(?<!discount )"
    r"(?<!voucher )(?<!coupon )(?<!bar )(?<!qr )(?<!morse )(?<!tax )(?<!country )(?<!access )"
    r"(?<!door )(?<!gift )(?<!booking )(?<!confirmation )(?<!verification )(?<!security )"
    r"\bcode\b(?!\s*#?\d)",
    re.IGNORECASE,
)
_COMMIT_RE = re.compile(r"\bcommits?\b(?!\s+(?:to|myself|ourselves|yourself)\b)|\bcommitted\s+(?:it|this|that|the)\b",
                        re.IGNORECASE)
#: Words with a software sense AND an everyday one ("a branch of the bank", "the parcel was
#: deployed", "the build of the house", "a bug in my argument"): two different ones together
#: make software work; one alone does not.
_CODE_LEANING_RE = re.compile(
    r"\b(?:branch(?:es)?|builds?|deploy\w*|pipelines?|tests?|testing|release[sd]?|features?"
    r"|bugs?|debug\w*|functions?|modules?|schemas?|databases?|query|queries|migrat\w*|secrets?"
    r"|audit\w*|profil\w*|benchmark\w*|vulnerab\w*|implement\w*|scripts?|servers?|apps?"
    r"|crash\w*|regressions?|merge[sd]?|push(?:ed|ing)?|staging|production|shippable|patch(?:es)?"
    r"|install\w*|config\w*|files?|logs?|parser|snippet|handler|uploader|loader|generator)\b",
    re.IGNORECASE,
)
#: What software work says about its object: it fails, breaks, runs, passes, is slow or red.
_CODE_STATE_RE = re.compile(
    r"\b(?:fail\w*|broken|breaks|red|green|run|runs|rerun|running|fix|fixed|crash\w*|slow"
    r"|throws?|pass|passes|passing|errors?(?!\s+(?:code|number)\b)|reproduce|speed\s+up|takes\s+ages|add|adding|implement)\b",
    re.IGNORECASE,
)
#: A name only software has: a path or file with an extension, snake_case, camelCase, `ticks`.
_CODE_NAME_RE = re.compile(
    r"\b[\w-]+/[\w./-]+|\b\w+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|kt|swift|c|cpp|h|cs|php|sh|sql"
    r"|yaml|yml|toml|json|ini|cfg|lock|md)\b|\b[a-z]+_[a-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z]+\b|`[^`]+`"
)


def about_code(user_text: str) -> bool:
    """Whether the ask is about software work: a word only software has ("diff", "traceback",
    "the repo"), "code" or "commit" in their software sense, a name only software has
    (``orders.py``, ``test_checkout_total``), or two different words that lean that way
    ("the pipeline is red since the last merge"). "Review my cover letter before I commit to
    sending it", "the self-checkout showed error code 45" and "my parcel was deployed to the
    wrong depot" use everyday senses and are not. One leaning word counts when the ask says
    what software does ("the nightly build is broken", "run the tests again")."""
    text = str(user_text or "")
    if _CODE_ONLY_RE.search(text) or _CODE_NOUN_RE.search(text) or _COMMIT_RE.search(text):
        return True
    if _CODE_NAME_RE.search(text):
        return True
    leaning = {match.group(0).lower().rstrip("s") for match in _CODE_LEANING_RE.finditer(text)}
    return len(leaning) >= 2 or bool(leaning and _CODE_STATE_RE.search(text))


def everyday_sense_only(user_text: str) -> bool:
    """The ask says developer words, but only in their everyday senses: "before I commit to
    sending it", "error code 17", "the bugs in my reasoning". An ask without any such word is
    not this (the skill's own words decide it); an ask about software work is not either."""
    text = str(user_text or "")
    says = bool(
        _CODE_LEANING_RE.search(text) or _CODE_ONLY_RE.search(text) or re.search(r"\b(?:code|commit\w*)\b", text, re.I)
    )
    return says and not about_code(text)


def shown_to_people(skill_id: str) -> bool:
    """Whether a person-facing list or a turn may carry this skill."""
    return developer_skills_on() or not is_developer_skill(skill_id)


def load_native_library(root: Path | None = None) -> LibraryLoad:
    """Parse and validate every native SKILL.md. Invalid contracts are refused, not injected.

    Every load judges every contract again, against ONE read of the runtime's tool names; only
    the parse of a file whose text has not changed is reused (``_read_skill_file``). Measured on
    CI #681 (v0.8 c8ae4380) and on b9530c3c: each load parsed every file's header twice and rebuilt the
    whole tool catalog (218 contracts) once per contract just to read its names, about two loads
    a turn, and the semantic replay corpus ran past its shard budget with the stack here.
    """
    base = Path(root) if root is not None else native_skills_root()
    contracts: list[SkillContract] = []
    errors: list[SkillContractError] = []
    if not base.is_dir():
        return LibraryLoad(contracts=(), invalid=())

    # First pass collects ids so incompatible-with can be validated against the real library.
    parsed: list[SkillContract] = []
    for entry in sorted(base.iterdir()):
        candidate = entry / "SKILL.md" if entry.is_dir() else entry
        if candidate.name != "SKILL.md" or not candidate.is_file():
            continue
        read = _read_skill_file(candidate)
        if read is None:
            errors.append(
                SkillContractError(
                    id=entry.name if entry.is_dir() else candidate.stem,
                    path=str(candidate),
                    violations=("no YAML frontmatter",),
                )
            )
            continue
        front, body = read
        parsed.append(contract_from_frontmatter(front, path=str(candidate), body=body))

    known_ids = frozenset(c.id for c in parsed)
    tool_names = _runtime_tool_names() if parsed else None
    for contract in parsed:
        violations = contract_violations(contract, known_skill_ids=known_ids, known_intents=tool_names)
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
        _SKILL_FILE_PARSES.clear()


def parse_skill_text(text: str, *, source: str = "native", path: str = "") -> SkillContract | None:
    """Parse a SKILL.md delivered as TEXT (MCP resources), through the same frontmatter path."""

    from core.plugin_skills import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(str(text or ""))
    if not match:
        return None
    try:
        from core.skill_frontmatter import load_header

        # The one bounded header reader: no alias expansion, capped size and depth (red team #6).
        front = load_header(match.group(1))
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


#: Why a typed frontmatter from anywhere else is not a contract. A typed contract is seated by
#: the turn's task class alone -- with no word from the person -- so it is a standing
#: instruction in every such turn's system prompt. Only VOOL's own library, the packs VOOL
#: ships, and Skill Codex packages whose signed admission still verifies may hold one.
UNTRUSTED_TYPED_SOURCE = (
    "typed keys ignored: {source} is not VOOL's own library, a pack VOOL ships, or a signed "
    "Skill Codex package, so this skill is used only as a plain skill when the person's own "
    "words match it"
)


def typed_source_trusted(plugin_id: str, plugin_dir: Path) -> bool:
    """Whether a pack may contribute TYPED contracts (auto-seated by task class).

    Two pack sources qualify, decided from where the pack is and what verifies it, never from
    anything the pack says about itself:

    - a pack VOOL ships (``core.plugin_catalog.bundled_plugin_dirs``: the app's own read-only
      tree; an installed pack of the same id never displaces it), and
    - a Skill Codex install (``registry-<id>``) whose lifecycle record is a registry record and
      whose signed admission still verifies (``core.plugin_lifecycle.is_available`` runs
      ``core.registry.native.runtime_allowed``: signature, revocation, digest).

    Everything else -- ``local-skills`` (where chat and zip imports land), packs a person built,
    GitHub/Browse add-ons, chat and project skills -- fails closed.
    """
    pid = str(plugin_id or "")
    try:
        resolved = Path(plugin_dir).resolve()
    except OSError:
        return False
    try:
        from core.plugin_catalog import bundled_plugin_dirs

        if any(resolved == Path(b).resolve() for b in bundled_plugin_dirs()):
            return True
    except Exception:
        pass
    if pid.startswith("registry-"):
        try:
            from core.plugin_lifecycle import is_available, record_for

            record = record_for(pid)
            return (
                record is not None
                and str(getattr(record, "source", "") or "").startswith("registry:")
                and bool(is_available(pid, root=Path(plugin_dir)))
            )
        except Exception:
            return False
    return False


def load_plugin_typed_contracts() -> LibraryLoad:
    """Typed contracts declared by INSTALLED (enabled) plugins, validated by the same law.

    Only packs ``typed_source_trusted`` accepts contribute contracts. A typed frontmatter in
    any other pack is reported (so its author can see why it never seats by task class) and its
    skill stays in the lexical lane, where only the person's own words select it.
    """
    try:
        from core.tool_offer_assembly import _plugin_dirs

        dirs = _plugin_dirs()
    except Exception:
        dirs = ()
    contracts: list[SkillContract] = []
    errors: list[SkillContractError] = []
    tool_names: Any = _UNSET  # read once, at the first typed skill
    for plugin_id, plugin_dir in dirs:
        skills_dir = plugin_dir / "skills"
        if not skills_dir.is_dir():
            continue
        source = f"plugin:{plugin_id}"
        trusted = typed_source_trusted(plugin_id, plugin_dir)
        for entry in sorted(skills_dir.iterdir()):
            candidate = entry / "SKILL.md" if entry.is_dir() else entry
            if candidate.name != "SKILL.md" or not candidate.is_file():
                continue
            read = _read_skill_file(candidate)
            front = read[0] if read is not None else {}
            if not _is_typed_frontmatter(front):
                continue  # legacy plugin skill: lexical world, untouched
            contract = contract_from_frontmatter(
                front,
                path=str(candidate),
                body=read[1] if read is not None else "",
                source=source,
            )
            if tool_names is _UNSET:
                tool_names = _runtime_tool_names()
            violations = contract_violations(contract, known_intents=tool_names)
            if not trusted:
                errors.append(
                    SkillContractError(
                        id=contract.id or "?", path=contract.path,
                        violations=(UNTRUSTED_TYPED_SOURCE.format(source=source), *violations),
                    )
                )
                continue
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
    tool_names: Any = _UNSET  # read once, at the first skill a server ships
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
            if tool_names is _UNSET:
                tool_names = _runtime_tool_names()
            violations = contract_violations(contract, known_intents=tool_names)
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


def mcp_lexical_skills() -> tuple[Any, ...]:
    """The VALID skills MCP servers ship, as PLAIN skills: picked only by the person's words.

    An MCP server's ``skill://`` text is never a typed contract (``load_skill_contracts``
    reports it instead), but a valid one is still a skill the person configured a server for,
    so it joins the lexical lane every installed plain skill is in: rendered only when the
    words match, fenced and labelled as third-party text, and its tool list can only narrow.
    An invalid one stays refused with its typed violations and is not offered here at all.
    """
    from core.plugin_skills import Skill

    try:
        contracts = load_mcp_skill_contracts().contracts
    except Exception:
        return ()
    return tuple(
        Skill(
            name=contract.id,
            description=contract.description,
            body=str(contract.body or "").strip(),
            allowed_tools=tuple(contract.permitted_tools),
            triggers=tuple(contract.triggers),
            plugin_id=contract.source,
            path=contract.path,
            version=contract.version,
        )
        for contract in contracts
        if contract.source.startswith("mcp:") and str(contract.body or "").strip()
    )


def load_skill_contracts() -> LibraryLoad:
    """EVERY typed skill this runtime can select: native + trusted plugin-typed, one validated
    set. Untrusted typed sources (other packs, MCP servers) are reported on ``invalid``."""
    native = load_native_library()
    plugin = load_plugin_typed_contracts()
    mcp = load_mcp_skill_contracts()
    # An MCP server's skill:// text is a third party's, delivered at run time by a process VOOL
    # does not ship or verify: it is none of the three trusted typed sources, so it is reported
    # and never selected by task class.
    mcp_refused = tuple(
        SkillContractError(
            id=contract.id or "?", path=contract.path,
            violations=(UNTRUSTED_TYPED_SOURCE.format(source=contract.source),),
        )
        for contract in mcp.contracts
    )
    seen: set[str] = set()
    contracts: list[SkillContract] = []
    conflicts: list[SkillContractError] = []
    for contract in (*native.contracts, *plugin.contracts):
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
        invalid=tuple((*native.invalid, *plugin.invalid, *mcp.invalid, *mcp_refused, *conflicts)),
    )


def _frontmatter_of(path: Path) -> dict[str, Any]:
    """The parsed YAML frontmatter of a SKILL.md, through the same loader the plugins use."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    return _frontmatter_from_text(text)


def _frontmatter_from_text(text: str) -> dict[str, Any]:
    from core.plugin_skills import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        from core.skill_frontmatter import load_header

        return load_header(match.group(1))
    except Exception:
        return {}


#: Each SKILL.md parsed once per TEXT: ``{path: (sha256 of the text, frontmatter, body)}``, the
#: body None when the skill loader refuses the file. Keyed on the text itself, not on a clock or
#: a size, so an edit is seen by the next load however fast it follows the last one.
_SKILL_FILE_PARSES: dict[str, tuple[str, dict[str, Any], str | None]] = {}
_SKILL_FILE_PARSES_MAX = 1024


def _read_skill_file(path: Path) -> tuple[dict[str, Any], str] | None:
    """(frontmatter, body) of one SKILL.md, or None when the skill loader refuses it.

    What ``core.plugin_skills.parse_skill`` and ``_frontmatter_of`` each read, from one read of
    the file. The file is read on every call; its header is parsed again only when the text
    differs from the last parse of that path.
    """
    from core.plugin_skills import parse_skill_content

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = str(path)
    with _contract_lock():
        cached = _SKILL_FILE_PARSES.get(key)
    if cached is None or cached[0] != digest:
        skill = parse_skill_content(text, path=Path(path))
        body = None if skill is None else str(getattr(skill, "body", "") or "")
        cached = (digest, _frontmatter_from_text(text), body)
        with _contract_lock():
            if len(_SKILL_FILE_PARSES) >= _SKILL_FILE_PARSES_MAX:
                _SKILL_FILE_PARSES.clear()
            _SKILL_FILE_PARSES[key] = cached
    _digest, front, body = cached
    if body is None:
        return None
    return dict(front), body


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


#: What bringing the capability graph up to date said, for the pass over the library under way
#: (a selection, the inventory): empty until a skill of the pass needs the graph. Outside a
#: pass there is none, and each check brings the graph up to date itself.
_GRAPH_PASS: ContextVar[list[bool] | None] = ContextVar("vool_skill_graph_pass", default=None)


@contextmanager
def _one_graph_check_per_pass():
    """Bring the capability graph up to the live registry ONCE for a pass over the library.

    Each up-to-date check reads the whole tool catalog (``capability_graph._index_is_stale``),
    and a selection made it once per skill that declares capabilities: 13 catalog reads on
    every selection, measured on the v0.8 replay corpus.
    """
    token = _GRAPH_PASS.set([])
    try:
        yield
    finally:
        _GRAPH_PASS.reset(token)


def _graph_up_to_date() -> None:
    """Bring the capability graph up to the live registry: once per pass, or now outside one.

    Raises when it cannot be brought up to date, for the first skill that asks and for every
    later one of the same pass.
    """
    from core.capability_graph import ensure_registry_bootstrap

    held = _GRAPH_PASS.get()
    if held is None:
        ensure_registry_bootstrap()
        return
    if not held:
        try:
            ensure_registry_bootstrap()
        except Exception:
            held.append(False)
            raise
        held.append(True)
    elif not held[0]:
        raise RuntimeError("the capability graph could not be brought up to date")


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
        from core.capability_graph import capabilities_for_skill, implementations_for_capability

        # Judge against the graph the runtime ACTUALLY bootstrapped — an unbootstrapped graph
        # answers "nothing exists", which would refuse every skill on a healthy machine.
        _graph_up_to_date()
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


#: The plain sentence for a skill that needs something this computer does not have, when no
#: more specific sentence names it. The typed reason (``reason``) stays for developers.
_PLAIN_UNAVAILABLE_TEXT = "It needs a part of VOOL that isn't on this computer yet."


def plain_unavailable_reason(unmet: Any = (), absent_capabilities: Any = ()) -> str:
    """Why a skill cannot run here, in words a person reads ("" when nothing is missing).

    ``reason`` keeps the internal form ("unavailable capabilities: ('media.video.split (unknown
    capability id)', ...)"), which is what a developer needs and what the selection records hold.
    That form reached people through the skill list, skill.inspect and the Skills catalog; this is
    what those show instead. Media (the media-studio skill on a computer without ffmpeg or the
    nebula-media worker, which includes every packaged app today) names the media tools.
    """
    needs = [str(item) for item in (*tuple(unmet or ()), *tuple(absent_capabilities or ()))]
    if not needs:
        return ""
    if any(item.startswith("media.") or "ffmpeg" in item or "ffprobe" in item or item.startswith("tool:media.")
           for item in needs):
        try:
            from core.media_capabilities import media_unavailable_text

            return media_unavailable_text()
        except Exception:
            return "Video editing needs VOOL's media tools (ffmpeg), which aren't on this computer yet."
    return _PLAIN_UNAVAILABLE_TEXT


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
                # Said in plain words wherever this is shown (skill.inspect replies with it).
                "reason": "; ".join(error.violations),
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
        "plain_reason": "" if disabled else plain_unavailable_reason(unmet, absent_capabilities),
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
    # One capability-graph check for the whole inventory, not one per skill.
    with _one_graph_check_per_pass():
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
                    "plain_reason": "" if is_disabled else plain_unavailable_reason(unmet, absent_capabilities),
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
#: Expert lenses on a turn with words: one (``select_native_skills``).
_MAX_LENSES_BY_WORDS = 1


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


def _lens_words(found: Any) -> bool:
    """A lens's own words in the ask, even one, even inside a question.

    A task skill needs more than a shared word, and none at all on a question about the world:
    the model answers "is sms login secure enough" without a procedure. A lens is not a
    procedure; it is how to weigh exactly such a question, and one of its own concern words
    ("secure", "claim", "pricing", "cluttered") says which weighing the question is about. A
    whole trigger or a word of the lens's name counts; words the ask merely shares with the
    card ("time", "set") do not.
    """
    if found is None or not (found.qualified or getattr(found, "general", False)):
        return False
    return bool(found.anchored or any(
        reason.startswith(('trigger "', "word from the skill's name")) for reason in found.reasons
    ))


_ALTERNATIVES_RE = re.compile(r"\b(?:or|versus|vs)\b")
_CHOOSING_RE = re.compile(
    r"\b(?:decide|deciding|decision|choose|choosing|pick|torn|dilemma|weigh|weighing|undecided)\b"
)
_ASKING_RE = re.compile(r"^(?:which|what|whether|should|would|could|can|is|are|do|does|shall)\b"
                        r"|\b(?:should|shall) (?:i|we)\b|\bwhich (?:one|option|first|is)\b")
_WEIGHING_RE = re.compile(
    r"\b(?:good|bad|smart|wise|sensible|better|right) (?:idea|move|choice|call|plan|time)\b"
    r"|\bis it (?:wise|smart|sensible|worth|better|too late|too risky|risky)\b"
    r"|\b(?:smarter|wiser|worth it)\b"
)
_OWN_CASE_RE = re.compile(r"\b(?:i|we|my|our|me|us|i'm|im)\b")


def _decision_shape(user_text: str) -> bool:
    """The ask weighs a course of action: "should I lease or buy", "which first, the roof or the
    kitchen", "is it wise to quit to open a bakery", "I'm torn between a gap year and uni".

    Read from the grammar of choosing, not from a topic: alternatives the person asks to choose
    between (an "or" in their question about their own case, or "between" with a word of
    choosing), or a course of action put up for judgement ("a good idea", "is it worth",
    "smarter"). "should I wear a jacket today?" offers no alternative and weighs no course, so
    it is a plain verdict; "what's the difference between a crocodile and an alligator" chooses
    nothing.
    """
    from core.plugin_skills import _raw_tokens

    tokens = _raw_tokens(user_text)
    if not tokens:
        return False
    spoken = " ".join(tokens)
    if _WEIGHING_RE.search(spoken):
        return True
    choosing = bool(_CHOOSING_RE.search(spoken))
    if choosing and ("between" in tokens or _ALTERNATIVES_RE.search(spoken)):
        return True
    asking = bool(_ASKING_RE.search(spoken)) or choosing
    return bool(asking and _ALTERNATIVES_RE.search(spoken) and _OWN_CASE_RE.search(spoken))


_ADVICE_RE = re.compile(
    r"\b(?:how (?:do|can|could|should|would|to)|help me|advice|advise|thoughts on|what do you think"
    r"|can i trust|is (?:that|this|it) (?:right|true|accurate|safe|secure|ok|okay))\b"
)


def _asks_for_judgement(user_text: str) -> bool:
    """The ask wants something weighed: it is a question ("?", or a clause that opens on a
    question word), asks how to, or asks for advice or a verdict. A lens weighs; an ask that
    only names a thing to do ("sabotage proof lifecycle for my workspace", "write the
    invoice") is not asking for a weighing, whatever words it shares with a lens."""
    from core.plugin_skills import _QUESTION_OPENERS, _raw_tokens

    text = str(user_text or "")
    if "?" in text or _ADVICE_RE.search(" ".join(_raw_tokens(text))):
        return True
    for clause in _CLAUSE_RE.split(text):
        words = _raw_tokens(clause)
        if words and words[0] in _QUESTION_OPENERS:
            return True
    return False


def _answers_decisions(contract: SkillContract) -> bool:
    """The lens whose own card says it is for decision-shaped turns."""
    return contract.kind == KIND_EXPERT_LENS and "decision-shaped" in str(contract.description or "").lower()


def _instruction_head(user_text: str) -> str:
    """The ask without the material it carries: "summarise this paragraph: <text>" is a request
    about "this paragraph"; the words of the text after the colon are what the work is done to,
    not what work is asked. Only a head that points at that material ("this", "these", "the
    following", "here") is cut; "which is smarter right now: paying off ... or investing" keeps
    its whole question."""
    from core.plugin_skills import _DEICTICS, _MATERIAL_WORDS, _raw_tokens

    text = str(user_text or "")
    match = re.search(r":\s+", text)
    if not match:
        return text
    head, rest = text[: match.start()], text[match.end():]
    head_tokens = _raw_tokens(head)
    if len(_raw_tokens(rest)) < 3 or not head_tokens or len(head_tokens) > 12:
        return text
    if not any(token in _DEICTICS or token in _MATERIAL_WORDS for token in head_tokens):
        return text
    return head


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


def select_native_skills(
    *,
    task_class: str = "",
    user_text: str = "",
    limit: int = DEFAULT_MAX_SELECTED,
    doctrine_limit: int = DEFAULT_MAX_DOCTRINE,
    library: LibraryLoad | None = None,
    session_intents: Iterable[str] = (),
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
      (its words, or a decision shape), on one concern word of its own (``_lens_words``), or,
      for the lens whose card declares decisions, on a decision-shaped ask (``_decision_shape``);
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
    # ``session_intents``: tools the chat has already seated (a follow-up inside a database chat
    # carries no database word of its own, "has Tomas given everything back"); they count as
    # demand so the skill and its catalog of real names ride along with the follow-up.
    explicit_intents = frozenset(demands.explicit_intents) | frozenset(
        str(name) for name in session_intents or ()
    )
    required_families = frozenset(demands.required_families)

    disabled = _read_disabled_ids()
    invalid_ids = {error.id: error for error in library.invalid}
    records: list[SkillAvailabilityRecord] = []
    scored: list[tuple[int, float, float, int, SkillContract]] = []
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
    # One capability-graph check for the whole selection, not one per skill.
    with _one_graph_check_per_pass():
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
    # An ask that carries its material is read by its request, not by the material's words.
    asked = _instruction_head(str(user_text or ""))
    carries_material = asked != str(user_text or "")
    decision = has_words and _decision_shape(asked)
    judgement = has_words and (decision or _asks_for_judgement(asked))
    everyday_words = has_words and everyday_sense_only(asked)
    evidence = _word_evidence(available, asked) if has_words else {}
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
            # A lens: its name or a whole trigger seats it; on an ask that wants something weighed,
            # one of its concern words is enough (``_lens_words``); a
            # decision-shaped ask seats the lens that declares itself for decisions; its class
            # seats it when the ask shows it is that kind of turn.
            shaped = _lens_words(found) or decision
            seated = (
                (found is not None and _words_pick(found) and found.anchored)
                or (judgement and _lens_words(found))
                or (decision and _answers_decisions(contract))
                or (typed > 0 and shaped)
            )
            reason = "the turn's class and words do not call for this lens"
        else:
            # A task skill: the words pick it and are strong on their own; or its class backs
            # words that pick it, or two different words of its own card; or a demand signal
            # points at it alone.
            singled_out = any(claimants.get(name, 0) == 1 for name in intent_hits | family_hits)
            backed = class_hit and (words_pick or (found is not None and found.distinct >= 2))
            # A developer skill's words do not count on an ask that uses developer words only in
            # their everyday senses: "review my cover letter before I commit to sending it" says
            # "review" and "commit" and is a letter (``everyday_sense_only``).
            worded = _strong_words(found) and not (contract.audience == "developer" and everyday_words)
            seated = worded or backed or singled_out
            reason = "the ask's words do not ask for what this skill does"
        judged.append((contract, seated, class_hit, word_worth, typed, law, words_pick, reason, found))

    # Where the words are silent, the class still speaks. When the words seat no task skill and
    # no lens, the class seats the task skills and the lens it names, as it always did ("here
    # are my meeting notes, give me the action items" classed debugging; "Write an X article"
    # classed creative_ideation). Words decide WHICH skill when they say anything; and a plain
    # question no skill's words answer to is answered plainly (``_plain_question``).
    if has_words and not carries_material and not _plain_question(str(user_text or "")) and not any(
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
            # The class was misled by developer words used in their everyday senses ("the
            # self-checkout said error code 45" classed debugging): no developer skill on it.
            if seated and contract.audience == "developer" and everyday_words:
                seated = False
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
            # A lens its own words seat ranks ahead of one only the class or the decision shape
            # seats: "should we keep one database or split into services" is a decision, and the
            # words say the decision is about architecture.
            tier = 0 if (
                has_words and (
                    (anchored and law == LAW_TASK_WORDS)
                    or (law == LAW_LENS_WORDS and _lens_words(found))
                )
            ) else 1
            if contract.kind == KIND_EXPERT_LENS and _strong_words(found):
                word_worth *= 2
            # Among lenses its words seat, the one the ask's words say most about leads; a class
            # several lenses share only breaks the tie ("the search page is confusing for
            # customers" is about the page, whatever the class).
            lead = word_worth if (law == LAW_LENS_WORDS and tier == 0) else 0.0
            scored.append((tier, -lead, -(typed + word_worth), contract.priority, contract))
            class_rank[contract.id] = (-typed, contract.priority, contract.id)
        else:
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id, state="below_cutoff", reason=reason,
                    version=contract.version,
                )
            )

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4].id))
    ordered = [item[4] for item in scored]
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
    lens_taken = 0
    for contract in ordered:
        lane = lane_for(contract)
        word_lens = _seating_law(contract) == LAW_LENS_WORDS
        if has_words and word_lens and lens_taken >= _MAX_LENSES_BY_WORDS:
            # One way of weighing a question per turn: once the ask's words can seat a lens
            # without its class, two lenses would otherwise share the doctrine lane's seats on
            # any ask that brushes two concerns ("customers" and "safe"); the best-ranked one
            # is the one the question is about.
            records.append(
                SkillAvailabilityRecord(
                    id=contract.id, state="below_cutoff",
                    reason="one expert lens per turn; a lens the ask is more about ranked first",
                    version=contract.version,
                )
            )
            continue
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
        lens_taken += word_lens

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
        if contract.id == "database":
            from core.database_work_state import database_catalog_block

            catalog = database_catalog_block()
            if catalog and len(block) + len(catalog) + 2 <= remaining[lane]:
                block = block + "\n\n" + catalog
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
    "mcp_lexical_skills",
    "native_skills_root",
    "parse_skill_text",
    "plain_unavailable_reason",
    "reset_contract_caches",
    "select_native_skills",
    "set_skill_enabled",
    "shown_to_people",
    "skill_cards",
    "skill_inventory",
]
