"""Machine-generated census of every executable operator action.

Discovery is mechanical (AST/regex/tomllib over the real surfaces); no item is
hand-added. Classification is rule-driven with an explicit, reviewed override
table — the pin (census_snapshot.json) is the catalog of record.

Classes:
- REGISTRY_OWNED        declared in the Command Registry (or a generated
                        projection of it: vool verbs, /api/commands*)
- GENERATED_ADAPTER     legacy surface whose handler forwards through
                        execute_command — the CommandSpec owns name/schema/
                        permission/effects/faults; the legacy shape is preserved
- EXTERNAL_TRANSPORT_ONLY  machine/runtime/UI transport, satellite compat,
                        OS launcher, or frozen prose demand-routing — not an
                        operator-command surface (gated elsewhere where mutating)
- LEGACY_UNMIGRATED     release-critical operator action not yet owned/adapted
                        — ``vool commands --check`` FAILS while any exist
- DEAD                  verified orphaned/broken (never shipped or already
                        retired by an accepted audit)

Anti-unwiring: discovery ids must match the pin exactly — a new CLI action,
HTTP route, chat command or model tool that is not catalogued fails --check.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source_tree_available() -> bool:
    """The census DISCOVERS surfaces from the repo sources (a CI/lint power);
    an installed package ships the pin and verifies it without sources."""
    return (PROJECT_ROOT / "pyproject.toml").exists() and (PROJECT_ROOT / "apps" / "vool_cli.py").exists()
SNAPSHOT_PATH = Path(__file__).resolve().parent / "census_snapshot.json"

CLASSIFICATIONS = (
    "REGISTRY_OWNED",
    "GENERATED_ADAPTER",
    "EXTERNAL_TRANSPORT_ONLY",
    "LEGACY_UNMIGRATED",
    "DEAD",
)

# Surfaces whose GET pages/feeds are UI transports, not operator commands.
_PAGE_TRANSPORT_PREFIXES = (
    "/chat",
    "/task-rail",
    "/trace",
    "/web0",
    "/null-browser",
    "/media-editor",
    "/commands",  # registry projections themselves are REGISTRY_OWNED via override
)

# Machine/model-lane transports and satellite compatibility surfaces.
_MACHINE_TRANSPORT_PATTERNS = (
    r"^/v1/",
    r"^/api/tags",
    # ANCHORED. This was a bare `^/api/chat`, which swallowed the whole subtree:
    # every `/api/chat/...` surface read as machine-lane transport, including six
    # POST actions (pin, queue, session, cancel, dictation, attachments/remove).
    # A wildcard that absorbs surfaces nobody reviewed is exactly what let the
    # census certify a zero it had not earned. The chat TURN itself and its
    # streaming siblings are genuine model-lane transport and keep the rule; every
    # sub-path is now named individually in census_overrides, so a NEW
    # `/api/chat/...` surface appears as LEGACY_UNMIGRATED instead of vanishing.
    r"^/api/chat$",
    r"^/api/completions",
    r"^/api/generate",
    r"^/api/embeddings",
    r"^/healthz",
    r"^/api/health",
    r"^/api/version",
    r"^/api/webhook",
    r"^/api/relay",
    r"^/api/events",
    r"^/api/stream",
)


@dataclass(frozen=True)
class CensusItem:
    census_id: str            # stable: "<surface-kind>:<detail>"
    surface: str              # cli-script | cli-vool | cli-module | launcher | http | chat-repl | chat-family
    name: str
    classification: str = "LEGACY_UNMIGRATED"
    registry_command_id: str = ""
    note: str = ""
    # Ownership detail. Not in the pin (see `write_snapshot`, which picks its fields
    # explicitly) because the pin answers "did the surface set change"; these answer
    # "who owns this one, under what authority, and where is that shown to be true".
    handler: str = ""      #: "module:function" that actually serves it, where discovery can read it
    effects: str = ""      #: the owning command's effect class, from the registry
    permission: str = ""   #: the owning command's permission class, from the registry
    proof: tuple[str, ...] = ()  #: test files naming this exact route


# ---------------------------------------------------------------------------
# Discovery — mechanical, no hand-added items
# ---------------------------------------------------------------------------


def _pyproject_scripts() -> dict[str, str]:
    import tomllib

    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return dict(data.get("project", {}).get("scripts", {}))


def discover_cli() -> list[CensusItem]:
    items: list[CensusItem] = []

    # console scripts
    for name, entry in _pyproject_scripts().items():
        items.append(CensusItem(census_id=f"cli-script:{name}", surface="cli-script", name=entry))

    # vool argparse leaves (AST over apps/vool_cli.py); nested subparser
    # leaves are qualified by their parent (e.g. bug-report.receipts) so ids stay unique
    cli_path = PROJECT_ROOT / "apps" / "vool_cli.py"
    tree = ast.parse(cli_path.read_text())

    def _receiver_name(call: ast.Call) -> str:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id
        return ""

    subparser_parents: dict[str, str] = {}  # var -> parent leaf name
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "add_subparsers"
                and isinstance(node.targets[0], ast.Name)
            ):
                parent_var = _receiver_name(call)
                # resolve parent var to its add_parser name via a second rule below
                subparser_parents[node.targets[0].id] = parent_var

    leaf_names: dict[str, str] = {}  # var -> leaf name
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "add_parser":
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(node.targets[0], ast.Name):
                    leaf_names[node.targets[0].id] = call.args[0].value

    def _leaf_id(call: ast.Call) -> str | None:
        if not call.args:
            return None
        arg = call.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return None
        name = arg.value
        receiver = _receiver_name(call)
        parent_var = subparser_parents.get(receiver, "")
        parent_leaf = leaf_names.get(parent_var, "")
        return f"{parent_leaf}.{name}" if parent_leaf else name

    seen_leaves: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_parser"):
            continue
        leaf = _leaf_id(node)
        if leaf is None:
            continue
        census_id = f"cli-vool:{leaf}"
        if census_id in seen_leaves:
            continue
        seen_leaves.add(census_id)
        items.append(CensusItem(census_id=census_id, surface="cli-vool", name=leaf))

    # module CLIs for named operator areas (python -m ...)
    for module, verbs in (
        ("core.blackbox", ("status", "verify", "turns", "rollback")),
    ):
        main_path = PROJECT_ROOT / module.replace(".", "/") / "__main__.py"
        if main_path.exists():
            for verb in verbs:
                items.append(
                    CensusItem(census_id=f"cli-module:{module}:{verb}", surface="cli-module", name=f"python -m {module} {verb}")
                )

    # vool verbs (the registry CLI itself)
    for verb in ("help", "commands --json", "commands --check", "<command dispatch>"):
        items.append(CensusItem(census_id=f"cli-vool:{verb}", surface="cli-script", name=f"vool {verb}"))

    # OS launchers / recovery scripts at repo root
    for pattern in ("*.sh", "*.bat", "*.command", "*.ps1", "*.cmd", "*.vbs"):
        for path in sorted(PROJECT_ROOT.glob(pattern)):
            # the pin is committed: name the file, never the checkout it was scanned in
            items.append(CensusItem(census_id=f"launcher:{path.name}", surface="launcher", name=path.name))

    return items


#: Prefixes that select a RESPONSE FORMAT, not a route. Every `startswith("/v1/")`
#: on the POST side is inside the shared chat handler choosing the OpenAI shape over
#: the Ollama one; the real `/v1/...` paths are selected by `==` comparisons and are
#: already discovered as their own rows. Emitting a `/v1/*` family here would invent
#: a surface rather than reveal one.
_NOT_ROUTE_PREFIXES = frozenset({"/v1/"})

#: Dispatchers whose names carry no HTTP method, and the concrete path each serves.
#:
#: `dispatch_dictation` is invisible twice over: its name has no get/post/upload, so
#: the method map never includes it, AND it selects its route with a PREDICATE
#: (`is_raw_dictation_path(path)`) rather than a literal comparison, so even a walked
#: branch yields nothing. The path it serves is a module constant, read from the
#: source here rather than hard-coded, so a rename moves the row instead of silently
#: dropping it.
_NAMED_DISPATCHERS: dict[str, tuple[tuple[str, ...], str]] = {
    "dispatch_dictation": (("GET", "POST"), "RAW_DICTATION_PATH"),
}


def _module_constant(tree: ast.AST, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return ""


def _delegated_target_for(branch: ast.If) -> tuple[str, str] | None:
    """(module, function) a prefix branch hands the request to.

    The FUNCTION matters as much as the module: `wallet_api` holds both
    `handle_wallet_get` and `handle_wallet_post`, and reading the module as a whole
    attributes every POST-only route to GET as well. The branch names which one it
    calls, so that is what is read.
    """
    module = None
    for sub in ast.walk(branch):
        if isinstance(sub, ast.ImportFrom) and sub.module and sub.module.startswith("core.web.api."):
            module = sub.module
            for alias in sub.names:
                if alias.name.startswith("handle_"):
                    return module, alias.name
    return (module, "") if module else None


def _prefixes_in(test: ast.AST) -> list[str]:
    """Every `<x>.startswith("/...")` literal inside one branch test."""
    out: list[str] = []
    for node in ast.walk(test):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("/"):
                    out.append(arg.value)
    return out


def _module_route_literals(module: str, function: str = "") -> list[str]:
    """Concrete route paths the delegated HANDLER compares against itself."""
    rel = module.replace(".", "/") + ".py"
    path = PROJECT_ROOT / rel
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    scope: ast.AST = tree
    if function:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
                scope = node
                break
        else:
            return []
    found: list[str] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Compare):
            for comp in node.comparators:
                found.extend(v for v in _literals(comp) if v.startswith("/"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("/"):
                    # A nested prefix inside the delegate names a templated family.
                    found.append(arg.value.rstrip("/") + "/{id}")
    return sorted(set(found))


def _discover_delegated_families(tree: ast.AST, dispatchers: dict[str, str]) -> list[CensusItem]:
    items: list[CensusItem] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        method = dispatchers.get(node.name)
        if method is None:
            continue
        for branch in ast.walk(node):
            if not isinstance(branch, ast.If):
                continue
            prefixes = [p for p in _prefixes_in(branch.test) if p not in _NOT_ROUTE_PREFIXES]
            if not prefixes:
                continue
            target = _delegated_target_for(branch)
            sub_paths = _module_route_literals(*target) if target else []
            for prefix in prefixes:
                matched = [p for p in sub_paths if p.startswith(prefix)]
                served_by = ":".join(target) if target else f"core.web.api.service:{node.name}"
                if matched:
                    for concrete in matched:
                        items.append(
                            CensusItem(
                                census_id=f"http:{method}:{concrete}",
                                surface="http",
                                name=f"{method} {concrete}",
                                handler=served_by,
                            )
                        )
                else:
                    # Delegation we cannot read into is still a surface. It is
                    # catalogued as the family it is, never dropped.
                    family = prefix.rstrip("/") + "/*"
                    items.append(
                        CensusItem(
                            census_id=f"http:{method}:{family}",
                            surface="http",
                            name=f"{method} {family}",
                            handler=served_by,
                        )
                    )
    return items


def discover_http() -> list[CensusItem]:
    items: list[CensusItem] = []
    service_path = PROJECT_ROOT / "core" / "web" / "api" / "service.py"
    src = service_path.read_text()
    tree = ast.parse(src)

    dispatchers = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name.lower()
        if "dispatch" not in name:
            continue
        if "upload" in name:
            dispatchers[node.name] = "UPLOAD"
        elif "post" in name:
            dispatchers[node.name] = "POST"
        elif "get" in name:
            dispatchers[node.name] = "GET"
    # A dispatcher whose NAME carries no method still serves routes. `dispatch_dictation`
    # is one, and it was invisible for exactly that reason: the loop above needs
    # get/post/upload in the name, and this one has neither. It is mounted for both
    # methods (core/web/api/app.py), so both are catalogued.
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for extra, (methods, const_name) in _NAMED_DISPATCHERS.items():
        if extra not in defined:
            continue
        path_value = _module_constant(tree, const_name)
        if not path_value:
            continue
        for method in methods:
            items.append(
                CensusItem(
                    census_id=f"http:{method}:{path_value}",
                    surface="http",
                    name=f"{method} {path_value}",
                )
            )
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            continue
        method = dispatchers.get(node.name)
        if method is None:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.If):
                continue
            test = sub.test
            paths: list[str] = []
            if isinstance(test, ast.Compare):
                for comp in test.comparators:
                    paths.extend(_literals(comp))
            elif isinstance(test, ast.BoolOp):
                for value in test.values:
                    if isinstance(value, ast.Compare):
                        for comp in value.comparators:
                            paths.extend(_literals(comp))
            for path_value in paths:
                if not path_value.startswith("/"):
                    continue
                items.append(
                    CensusItem(census_id=f"http:{method}:{path_value}", surface="http", name=f"{method} {path_value}")
                )

    # PREFIX-DISPATCHED FAMILIES. A route selected by `normalized_path.startswith(...)`
    # is an ast.Call, so the literal walk above harvests nothing from it and the whole
    # family is invisible. Measured before this: 12 such families, hiding 55 concrete
    # routes -- including every wallet POST money route, every profile write, the mobile
    # device-authority surface and the OAuth callback intake -- while `commands --check`
    # reported LEGACY_UNMIGRATED: 0. A scanner that cannot see a surface must not be able
    # to certify that surface as owned.
    #
    # No allowlist: the prefix is followed into the module the branch DELEGATES to, and
    # that module's own route table is read. An unknown surface therefore appears as a
    # row rather than being silently absent.
    literal_ids = {item.census_id for item in items}
    for family in _discover_delegated_families(tree, dispatchers):
        # A `/prefix/*` row exists to represent surfaces the literal walk CANNOT
        # see. When the leaves under that prefix are already discovered as their
        # own rows, the family row would double-count them and hide nothing, so it
        # is dropped. `/api/bug-report/` is such a prefix: every leaf inside it is
        # selected by an `==` comparison and already has a row.
        if family.census_id.endswith("/*"):
            prefix = family.census_id[: -len("*")]
            if any(cid.startswith(prefix) and cid != family.census_id for cid in literal_ids):
                continue
        items.append(family)

    # de-duplicate (same path can appear in nested ifs)
    seen: set[str] = set()
    unique: list[CensusItem] = []
    for item in items:
        if item.census_id not in seen:
            seen.add(item.census_id)
            unique.append(item)
    return unique


def _literals(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in node.elts:
            out.extend(_literals(elt))
        return out
    return []


def discover_chat() -> list[CensusItem]:
    items: list[CensusItem] = []

    # REPL slash literals in apps/vool_chat.py (mechanical: string constants in
    # comparisons/startswith calls that begin with "/")
    chat_path = PROJECT_ROOT / "apps" / "vool_chat.py"
    src = chat_path.read_text()
    repl_literals: set[str] = set()
    for match in re.finditer(r"""["'](/[\w-]+)["']""", src):
        repl_literals.add(match.group(1))
    for literal in sorted(repl_literals):
        items.append(CensusItem(census_id=f"chat-repl:{literal}", surface="chat-repl", name=literal))

    # frozen prose demand-routing families (turn_frontdoor/fast_command_surface):
    # counted as FAMILIES — the product law freezes them as demand routing, not
    # command surfaces; each family is catalogued so nothing disappears.
    families = (
        "cloud",        # cloud model/key/models/status arg-forms
        "brakes",       # spend brakes on/off/status
        "prefs",        # preference set/list
        "new-family",   # /new-family
        "trace-family", # /trace-family
        "help",         # chat help
        "secret-intake",# bare-secret intake
        "slash-refusal",# catch-all unknown-slash refusal
    )
    for family in families:
        items.append(CensusItem(census_id=f"chat-family:{family}", surface="chat-family", name=family))

    return items


def discover_all() -> list[CensusItem]:
    return [*discover_cli(), *discover_http(), *discover_chat()]


# ---------------------------------------------------------------------------
# Classification rules + reviewed overrides
# ---------------------------------------------------------------------------

from core.command_registry.census_overrides import _ADAPTER_FAMILIES, OVERRIDES


def _default_classification(item: CensusItem, registry_surfaces: set[str]) -> tuple[str, str, str]:
    """Rule-driven default: (classification, registry_command_id, note)."""
    cid = item.census_id

    # registry projections
    if (
        cid.startswith("cli-vool:")
        or (cid.startswith("http:") and item.name.endswith("/api/commands"))
        or cid == "http:GET:/api/commands"
        # The prefix-family row for the registry's OWN projection. The scanner now
        # emits it; it is the same surface the two rules below already own.
        or cid == "http:GET:/api/commands/*"
    ):
        return "REGISTRY_OWNED", "", "registry CLI/route projection"
    if cid.startswith("http:") and item.name.split(" ", 1)[1].rstrip("/") in {
        "/api/commands",
        "/api/commands/schema",
        "/api/commands/palette",
        "/api/commands/chat",
        "/api/commands/dispatch",
    }:
        return "REGISTRY_OWNED", "", "registry API projection"
    if cid == "cli-script:vool":
        return "REGISTRY_OWNED", "", "the registry CLI"

    # machine/model transports, satellites, compat
    path = item.name.split(" ", 1)[1] if cid.startswith("http:") else ""
    for pattern in _MACHINE_TRANSPORT_PATTERNS:
        if re.match(pattern, path):
            return "EXTERNAL_TRANSPORT_ONLY", "", "machine/model-lane or satellite transport"
    for prefix in _PAGE_TRANSPORT_PREFIXES:
        if path == prefix or (path.startswith(prefix + "/") and cid.startswith("http:GET:")):
            if path.rstrip("/") not in {"/api/commands", "/api/commands/schema", "/api/commands/palette", "/api/commands/chat"}:
                return "EXTERNAL_TRANSPORT_ONLY", "", "UI page/feed transport"

    # OS launchers, installers, dev tooling
    if cid.startswith("launcher:"):
        return "EXTERNAL_TRANSPORT_ONLY", "", "OS launcher/installer transport"

    # frozen prose demand-routing families
    if cid.startswith("chat-family:"):
        return "EXTERNAL_TRANSPORT_ONLY", "", "frozen prose demand-routing (product law: not a command surface)"

    # REPL lifecycle chrome
    if cid in {"chat-repl:/exit", "chat-repl:/quit"}:
        return "EXTERNAL_TRANSPORT_ONLY", "", "REPL lifecycle chrome"

    # verified-dead entry points (accepted audit: blueprint census 2026-09-02)
    if cid in {"cli-script:vool-node"} or "vool_node" in item.name:
        return "DEAD", "", "orphaned entry point (verified audit)"

    return "LEGACY_UNMIGRATED", "", ""


def _authority_of(command_id: str) -> tuple[str, str]:
    """The owning command's effect and permission class, read from the registry.

    Read, never restated: a census that carried its own copy of an effect class
    would drift from the dispatcher that enforces it, and the drift would be
    invisible exactly because the census looks authoritative.
    """
    if not command_id:
        return "", ""
    try:
        from core.command_registry.registry import registry

        spec = registry().lookup(command_id)
    except Exception:
        return "", ""
    if spec is None:
        return "", ""
    permission = type(getattr(spec, "permission", None)).__name__
    return str(getattr(spec, "effects", "") or ""), ("" if permission == "NoneType" else permission)


def _proof_index() -> dict[str, tuple[str, ...]]:
    """route path -> test files naming it. One grep, not one per row."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "grep", "-l", "-E", r"\"/(api|media-editor|v1)/", "--", "tests/"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120,
        ).stdout
    except Exception:
        return {}
    index: dict[str, list[str]] = {}
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            body = (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"[\"'](/(?:api|media-editor|v1)/[A-Za-z0-9/_.\-]*)[\"']", body):
            index.setdefault(match.group(1), []).append(rel)
    return {path: tuple(sorted(set(files))[:4]) for path, files in index.items()}


def classify_all(items: list[CensusItem], registry_surfaces: set[str]) -> list[CensusItem]:
    out: list[CensusItem] = []
    proofs = _proof_index()
    for item in items:
        override = OVERRIDES.get(item.census_id)
        if override is not None:
            classification, reg_id, note = override
        else:
            classification, reg_id, note = _default_classification(item, registry_surfaces)
        effects, permission = _authority_of(reg_id)
        path = item.census_id.split(":", 2)[2] if item.census_id.count(":") >= 2 else ""
        out.append(
            CensusItem(
                census_id=item.census_id,
                surface=item.surface,
                name=item.name,
                classification=classification,
                registry_command_id=reg_id,
                note=note,
                handler=item.handler,
                effects=effects,
                permission=permission,
                proof=proofs.get(path, ()),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Census check (the anti-unwiring gate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CensusReport:
    ok: bool
    items: tuple[CensusItem, ...]
    findings: tuple[str, ...] = field(default_factory=tuple)

    def totals(self) -> dict[str, int]:
        totals = {c: 0 for c in CLASSIFICATIONS}
        for item in self.items:
            totals[item.classification] += 1
        return totals


def run_census(registry_surfaces: set[str] | None = None) -> CensusReport:
    if registry_surfaces is None:
        from core.command_registry.registry import registry

        registry_surfaces = set(registry().surface_map().keys())
    items = classify_all(discover_all(), registry_surfaces)
    return CensusReport(ok=True, items=tuple(items))


def load_pin() -> dict[str, Any]:
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text())


def census_check(registry_command_ids: set[str]) -> CensusReport:
    """Compare discovery against the pin; fail on uncatalogued, stale,
    LEGACY_UNMIGRATED, or adapter bindings that no longer exist.

    In an installed package (no source tree) the pin is verified standalone:
    totals consistent, no LEGACY_UNMIGRATED, every adapter binding names a
    registry command that exists in-process.
    """
    pin = load_pin()
    pinned_items: dict[str, Any] = pin.get("items", {}) if pin else {}
    findings: list[str] = []

    if not pinned_items:
        return CensusReport(
            ok=False,
            items=(),
            findings=tuple(["census snapshot missing — run: python -m core.command_registry.census --write"]),
        )

    if not _source_tree_available():
        items = tuple(
            CensusItem(
                census_id=census_id,
                surface=str(entry.get("surface") or ""),
                name=str(entry.get("name") or ""),
                classification=str(entry.get("classification") or ""),
                registry_command_id=str(entry.get("registry_command_id") or ""),
                note=str(entry.get("note") or ""),
            )
            for census_id, entry in pinned_items.items()
        )
        for census_id, entry in pinned_items.items():
            if entry.get("classification") == "LEGACY_UNMIGRATED":
                findings.append(f"legacy_unmigrated: {census_id} ({entry.get('name')})")
            reg_id = str(entry.get("registry_command_id") or "")
            if reg_id and registry_command_ids and reg_id not in registry_command_ids:
                findings.append(f"adapter binding broken: {census_id} -> {reg_id} (no such registry command)")
        return CensusReport(ok=not findings, items=items, findings=tuple(findings))

    report = run_census()

    discovered_ids = {i.census_id for i in report.items}
    for item in report.items:
        pinned = pinned_items.get(item.census_id)
        if pinned is None:
            findings.append(f"uncatalogued: {item.census_id} ({item.name}) — add it to the census or the registry")
            continue
        if pinned.get("classification") != item.classification:
            findings.append(
                f"classification drift: {item.census_id} pinned={pinned.get('classification')} discovered={item.classification}"
            )
        reg_id = pinned.get("registry_command_id") or ""
        if reg_id and reg_id not in registry_command_ids:
            findings.append(f"adapter binding broken: {item.census_id} -> {reg_id} (no such registry command)")
        for member in _ADAPTER_FAMILIES.get(item.census_id, ()):
            if registry_command_ids and member not in registry_command_ids:
                findings.append(
                    f"adapter family binding broken: {item.census_id} -> {member} (no such registry command)"
                )
        if item.classification == "LEGACY_UNMIGRATED":
            findings.append(f"legacy_unmigrated: {item.census_id} ({item.name}) — migrate or adapt before release")

    for pinned_id in pinned_items:
        if pinned_id not in discovered_ids:
            findings.append(f"stale pin entry: {pinned_id} no longer discovered — remove it")

    return CensusReport(ok=not findings, items=report.items, findings=tuple(findings))


def write_snapshot() -> CensusReport:
    report = run_census()
    payload = {
        "generated_by": "python -m core.command_registry.census --write",
        "classifications": list(CLASSIFICATIONS),
        "totals": report.totals(),
        "items": {
            item.census_id: {
                "surface": item.surface,
                "name": item.name,
                "classification": item.classification,
                "registry_command_id": item.registry_command_id,
                "note": item.note,
            }
            for item in sorted(report.items, key=lambda i: i.census_id)
        },
    }
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report


def ownership_table() -> list[dict[str, Any]]:
    """Every discovered surface as one row, with who owns it and under what authority.

    Separate from the pin on purpose. The pin answers "did the surface set change";
    this answers the question the closure actually asks -- transport, method/path,
    production handler, owning command or typed exception, permission/effect class,
    and the tests that name the route. Empty strings are real answers here: a row
    with no handler is one discovery could not read into, and saying so is the point.
    """
    report = run_census()
    rows = []
    for item in sorted(report.items, key=lambda i: i.census_id):
        parts = item.census_id.split(":", 2)
        rows.append(
            {
                "census_id": item.census_id,
                "transport": item.surface,
                "method": parts[1] if item.surface == "http" and len(parts) > 2 else "",
                "path": parts[2] if len(parts) > 2 else item.name,
                "handler": item.handler,
                "owning_command": item.registry_command_id,
                "classification": item.classification,
                "effects": item.effects,
                "permission": item.permission,
                "proof": list(item.proof),
                "note": item.note,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if "--table" in argv:
        rows = ownership_table()
        out = argv[argv.index("--table") + 1] if len(argv) > argv.index("--table") + 1 else ""
        payload = {
            "rows": rows,
            "totals": {
                "rows": len(rows),
                "http_rows": sum(1 for r in rows if r["transport"] == "http"),
                "with_owner": sum(1 for r in rows if r["owning_command"]),
                "with_handler": sum(1 for r in rows if r["handler"]),
                "with_effects": sum(1 for r in rows if r["effects"]),
                "with_proof": sum(1 for r in rows if r["proof"]),
                "legacy_unmigrated": sum(
                    1 for r in rows if r["classification"] == "LEGACY_UNMIGRATED"
                ),
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if out and not out.startswith("--"):
            Path(out).write_text(text, encoding="utf-8")
            print(json.dumps(payload["totals"], indent=2))
        else:
            print(text)
        return 0
    if "--write" in argv:
        report = write_snapshot()
        totals = report.totals()
        print(f"census snapshot written: {len(report.items)} items")
        for cls in CLASSIFICATIONS:
            print(f"  {cls}: {totals[cls]}")
        return 0
    report = census_check(set())  # registry ids filled by caller in-process
    for finding in report.findings:
        print(finding)
    print("census ok" if report.ok else "census FAILED")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
