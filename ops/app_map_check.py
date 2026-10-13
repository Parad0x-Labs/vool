#!/usr/bin/env python3
"""Check docs/app-map.yaml against the code it maps.

Every reference a row makes must be real on the current tree:
  - ui element ids must appear in the served page sources
    (core/vool_chat_page.py, core/vool_settings_page.py, core/*_fragment.py);
  - route paths must exist in the route census
    (core/command_registry/census_snapshot.json);
  - tool ids must exist in the tool golden pin (tests/gauntlet/tool_surface_golden.json);
  - default keys must exist and their live value must equal the expected value
    (UserPreferences dataclass defaults in core/user_preferences.py;
    config/default_policy.yaml by dotted key);
  - test files must exist;
  - every census item and every pinned tool must belong to a row's `wired`
    or to an explicit `internal` entry.

One line per problem, then a summary. Exit 1 on any problem.
Standard library plus PyYAML only. Run from the repo root:
    python ops/app_map_check.py
"""

from __future__ import annotations

import ast
import contextlib
import glob
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "docs" / "app-map.yaml"
CENSUS_PATH = ROOT / "core" / "command_registry" / "census_snapshot.json"
GOLDEN_PATH = ROOT / "tests" / "gauntlet" / "tool_surface_golden.json"
PREFS_PATH = ROOT / "core" / "user_preferences.py"
POLICY_PATH = ROOT / "config" / "default_policy.yaml"

# Resolved against ROOT, not the working directory, so a run from elsewhere
# reads the same sources. A listed source that cannot be read is a problem.
PAGE_SOURCES = [
    "core/vool_chat_page.py",
    "core/vool_settings_page.py",
    *sorted(Path(p).relative_to(ROOT).as_posix() for p in glob.glob(str(ROOT / "core" / "*_fragment.py"))),
]

# Element id declarations matched in the page sources: HTML attributes, JS
# object keys, getElementById lookups and JS `.id = '...'` assignments.
UI_PATTERNS = [
    re.compile(r"""\bid="([^"]+)\""""),
    re.compile(r"""\bid='([^']+)'"""),
    re.compile(r"""\bid:\s*"([^"]+)\""""),
    re.compile(r"""\bid:\s*'([^']+)'"""),
    re.compile(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\.id\s*=\s*['"]([^'"]+)['"]"""),
]

SCHEMA = "vool.app-map.v1"
AREAS = {"chat-models", "memory-permissions", "tools", "crypto", "settings", "other"}
LABELS = {"TESTED", "IMPLEMENTED", "PROPOSED", "ASSUMED", "UNKNOWN"}
DEFAULT_FILES = {"user_preferences", "default_policy"}
NON_HTTP_SURFACES = {"chat-family", "chat-repl", "cli-module", "cli-script", "cli-vool", "launcher"}
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ROW_REQUIRED = ("id", "title", "area", "ui", "wired", "default", "tests", "label", "target_release")
ROW_KEYS = {*ROW_REQUIRED, "note"}
ROW_LISTS = ("ui", "wired", "default", "tests")
DEFAULT_KEYS = {"key", "file", "expected"}
INTERNAL_LISTS = ("routes", "census", "tools")
INTERNAL_KEYS = {"reason", *INTERNAL_LISTS}

problems: list[str] = []


def problem(line: str) -> None:
    problems.append(line)


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # unreadable map is the first problem
        print(f"PROBLEM cannot read {path.relative_to(ROOT)}: {exc}")
        sys.exit(1)


def census_refs() -> tuple[set[str], set[str]]:
    """Return (http route paths, non-http census item ids) from the census pin."""
    data = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    routes: set[str] = set()
    other: set[str] = set()
    for key in data.get("items", {}):
        parts = key.split(":", 2)
        if parts[0] == "http" and len(parts) == 3:
            routes.add(parts[2])
        else:
            other.add(key)
    return routes, other


def golden_tools() -> set[str]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return set(data.get("tools", {}))


def page_ids() -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {}
    for rel in PAGE_SOURCES:
        found: set[str] = set()
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except OSError as exc:
            problem(f"page source {rel} cannot be read: {exc}")
            continue
        for pattern in UI_PATTERNS:
            found.update(m.group(1) for m in pattern.finditer(text))
        ids[rel] = found
    return ids


def prefs_defaults() -> dict[str, object]:
    """Dataclass field defaults of UserPreferences, read from the source AST."""
    tree = ast.parse(PREFS_PATH.read_text(encoding="utf-8"))
    defaults: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "UserPreferences":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                    # A non-literal default (e.g. field(default_factory=...)) is left out,
                    # so a row naming it is reported instead of compared against a guess.
                    with contextlib.suppress(ValueError):
                        defaults[stmt.target.id] = ast.literal_eval(stmt.value)
            break
    return defaults


def policy_value(key: str):
    """Resolve a dotted key in the shipped default policy; None if absent."""
    node = load_yaml(POLICY_PATH)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def same_value(left, right) -> bool:
    """Strict equality: True is not 1, 0 is not False, 0 is not 0.0."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def valid_test_path(value) -> bool:
    if not isinstance(value, str) or not value.startswith("tests/") or not value.endswith(".py"):
        return False
    parts = Path(value).parts
    return ".." not in parts and (ROOT / value).is_file()


def classify_wired(entry: str) -> str:
    if entry.startswith("/"):
        return "route"
    surface = entry.split(":", 1)[0]
    if surface in NON_HTTP_SURFACES:
        return "census"
    return "tool"


def check_reference(kind: str, value: str, owner: str, routes: set[str], other: set[str], tools: set[str]) -> None:
    if kind == "route" and value not in routes:
        problem(f"row {owner}: wired route {value!r} is not in the route census")
    elif kind == "census" and value not in other:
        problem(f"row {owner}: wired census id {value!r} is not in the route census")
    elif kind == "tool" and value not in tools:
        problem(f"row {owner}: wired tool {value!r} is not in the tool golden pin")


def main() -> int:
    map_data = load_yaml(MAP_PATH)
    if not isinstance(map_data, dict):
        print(f"PROBLEM {MAP_PATH.relative_to(ROOT)} is not a mapping")
        return 1
    routes, other = census_refs()
    tools = golden_tools()
    ids_by_file = page_ids()
    known_ids: set[str] = set().union(*ids_by_file.values()) if ids_by_file else set()
    prefs = prefs_defaults()

    if map_data.get("schema") != SCHEMA:
        problem(f"map schema is {map_data.get('schema')!r}, expected {SCHEMA!r}")
    source = map_data.get("source_commit")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source or ""):
        problem("source_commit must be a full commit hash")

    rows = map_data.get("rows") or []
    internal = map_data.get("internal") or []
    if not rows:
        problem("map has no rows")

    wired_routes: set[str] = set()
    wired_census: set[str] = set()
    wired_tools: set[str] = set()
    seen_ids: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            problem(f"row {row!r} is not a mapping")
            continue
        rid = row.get("id", "<missing id>")
        for key in ROW_REQUIRED:
            if key not in row:
                problem(f"row {rid}: missing field {key!r}")
        for key in sorted(set(row) - ROW_KEYS):
            problem(f"row {rid}: unknown field {key!r}")
        for key in ROW_LISTS:
            if key in row and not isinstance(row[key], list):
                problem(f"row {rid}: field {key!r} must be a list")
                row[key] = []
        if not KEBAB_RE.match(str(rid)):
            problem(f"row id {rid!r} is not kebab-case")
        if rid in seen_ids:
            problem(f"duplicate row id {rid!r}")
        seen_ids.add(rid)

        if row.get("area") not in AREAS:
            problem(f"row {rid}: area {row.get('area')!r} is not one of {sorted(AREAS)}")
        if row.get("label") not in LABELS:
            problem(f"row {rid}: label {row.get('label')!r} is not one of {sorted(LABELS)}")
        release = row.get("target_release")
        if release not in ("0.7.0", "unknown"):
            problem(f"row {rid}: target_release {release!r} must be 0.7.0 or 'unknown'")
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            problem(f"row {rid}: missing title")

        ui = row.get("ui") or []
        for element in ui:
            if element == "chat":
                continue
            if element not in known_ids:
                problem(f"row {rid}: ui id {element!r} not found in any served page source")

        for entry in row.get("wired") or []:
            kind = classify_wired(str(entry))
            check_reference(kind, str(entry), rid, routes, other, tools)
            if kind == "route":
                wired_routes.add(str(entry))
            elif kind == "census":
                wired_census.add(str(entry))
            else:
                wired_tools.add(str(entry))

        for default in row.get("default") or []:
            if not isinstance(default, dict) or set(default) != DEFAULT_KEYS:
                problem(f"row {rid}: default entry {default!r} must have exactly key, file and expected")
                continue
            key = default.get("key")
            file_name = default.get("file")
            expected = default.get("expected")
            if file_name not in DEFAULT_FILES:
                problem(f"row {rid}: default file {file_name!r} must be one of {sorted(DEFAULT_FILES)}")
                continue
            if file_name == "user_preferences":
                if key not in prefs:
                    problem(f"row {rid}: default key {key!r} is not a UserPreferences field")
                elif not same_value(prefs[key], expected):
                    problem(
                        f"row {rid}: default {key!r} expected {expected!r} but "
                        f"core/user_preferences.py has {prefs[key]!r}"
                    )
            else:
                value, found = policy_value(str(key))
                if not found:
                    problem(f"row {rid}: default key {key!r} is absent from config/default_policy.yaml")
                elif not same_value(value, expected):
                    problem(
                        f"row {rid}: default {key!r} expected {expected!r} but "
                        f"config/default_policy.yaml has {value!r}"
                    )

        for test_path in row.get("tests") or []:
            if not valid_test_path(test_path):
                problem(f"row {rid}: test file {test_path!r} is not an existing tests/*.py file")

    # A reference is either a feature (some row's wired) or deliberately internal, never both.
    row_refs = wired_routes | wired_census | wired_tools
    internal_refs: set[str] = set()
    for entry in internal:
        if not isinstance(entry, dict):
            problem(f"internal entry {entry!r} is not a mapping")
            continue
        reason = entry.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            problem("internal entry without a reason")
        for key in sorted(set(entry) - INTERNAL_KEYS):
            problem(f"internal entry {reason!r}: unknown field {key!r}")
        if not any(entry.get(key) for key in INTERNAL_LISTS):
            problem(f"internal entry {reason!r}: lists no routes, census ids or tools")
        for key in INTERNAL_LISTS:
            if key in entry and not isinstance(entry[key], list):
                problem(f"internal entry {reason!r}: field {key!r} must be a list")
                entry[key] = []
        for route in entry.get("routes") or []:
            if route not in routes:
                problem(f"internal: route {route!r} is not in the route census")
            wired_routes.add(route)
        for census_id in entry.get("census") or []:
            if census_id not in other:
                problem(f"internal: census id {census_id!r} is not in the route census")
            wired_census.add(census_id)
        for tool in entry.get("tools") or []:
            if tool not in tools:
                problem(f"internal: tool {tool!r} is not in the tool golden pin")
            wired_tools.add(tool)
        for ref in [*(entry.get("routes") or []), *(entry.get("census") or []), *(entry.get("tools") or [])]:
            if ref in row_refs:
                problem(f"internal: {ref!r} is also wired by a row; it is either a feature or internal")
            if ref in internal_refs:
                problem(f"internal: {ref!r} is listed twice")
            internal_refs.add(ref)

    unmapped_routes = sorted(routes - wired_routes)
    unmapped_census = sorted(other - wired_census)
    unmapped_tools = sorted(tools - wired_tools)
    for route in unmapped_routes:
        problem(f"coverage: census route {route} is not in any row's wired or internal list")
    for census_id in unmapped_census:
        problem(f"coverage: census item {census_id} is not in any row's wired or internal list")
    for tool in unmapped_tools:
        problem(f"coverage: golden tool {tool} is not in any row's wired or internal list")

    print(f"app map check: {len(rows)} rows, {len(internal)} internal entries")
    print(
        f"sources: {len(routes)} census routes, {len(other)} other census items, "
        f"{len(tools)} pinned tools, {len(known_ids)} element ids in "
        f"{len(ids_by_file)} page sources"
    )
    print("matched id declarations: id=\"x\" / id='x' / id: \"x\" / id: 'x' / getElementById('x') / .id = 'x'")
    if problems:
        for line in problems:
            print(f"PROBLEM {line}")
        print(f"SUMMARY {len(problems)} problem(s)")
        return 1
    print("SUMMARY no problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
