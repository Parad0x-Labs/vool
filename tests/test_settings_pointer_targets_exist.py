"""Every "Settings → X" direction in person-facing text leads somewhere real.

A direction that names a group or row that does not exist on the Settings page sends the
person hunting through menus for nothing. This test inventories the groups and rows the
Settings page actually renders (core.vool_settings_page.settings_groups), then scans every
catalog value and every string literal the python modules build replies from, and requires
each "Settings → X" / "Settings > X" pointer to name one of those titles or rows (deeper
hops name rows or real controls; a hop may be the leading words of a longer real label).
Directions into the computer's own System Settings are not the app's Settings page and are
left alone.

One island is still blocked and pinned elsewhere: core/agent_runtime/memory_runtime.py still
says "Settings → API Keys" because memory code is out of bounds for the job that renamed the
group and tests/test_cloud_only_path.py:212 pins that wording. Those pointers are tolerated
ONLY from that file, and only while they still exist -- the count is asserted so a future fix
must come back here and shrink the allowance.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from core.vool_settings_page import settings_groups

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
ADAPTERS = REPO / "adapters"
CATALOGS = CORE / "i18n" / "catalogs"
LOCALES = ["ar", "de", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans"]

# Tolerated nowhere-direction islands: banned memory code whose wording a test pins; the
# provider adapter line pinned by tests/test_provider_credential_dispatch.py:74; and the
# no-route notice pinned by tests/test_cloud_only_path.py:212 (the reply embeds this text).
BLOCKED_ISLAND_FILES = {
    "core/agent_runtime/memory_runtime.py",
    "adapters/openai_compatible_adapter.py",
    "core/cloud_only_default.py",
}

# Third-party app menus that happen to be named "Settings": the sentence names the other
# app right before the path, so the direction is not into this app's Settings page.
THIRD_PARTY_MARKERS = ("Phantom",)

# "System Settings → ..." / "macOS Settings → ..." point at the computer's settings, not ours.
POINTER_RE = re.compile(r"(?<!System )(?<!macOS )Settings\s*[→>]\s*([^→>,.;:!?)\"'”’\n]+)")
HOP_SPLIT_RE = re.compile(r"[→>]")


def _hops(text: str) -> list[str]:
    return [h.strip() for h in HOP_SPLIT_RE.split(text) if h.strip()]


def _settings_inventory() -> tuple[set[str], set[str]]:
    titles: set[str] = set()
    rows: set[str] = set()
    for group in settings_groups():
        titles.add(str(group.get("title", "")).strip())
        for row in group.get("rows", []):
            label = str(row.get("label", "")).strip()
            if label:
                rows.add(label)
    return titles, rows


def _names_something_real(hop: str, inventory: set[str]) -> bool:
    """A hop lands if it is a real title/row/control, the leading words of a longer real
    one ("UsePod spending" of "UsePod spending approval"), or a real one followed by the
    sentence continuing ("Your keys first" of "Your keys")."""
    if hop in inventory:
        return True
    return any(item.startswith(hop) or hop.startswith(item) for item in inventory)


def _catalog_pointers() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for name in ["en", *LOCALES]:
        data = json.loads((CATALOGS / f"{name}.json").read_text(encoding="utf-8"))
        for key, value in data["messages"].items():
            if any(marker in str(value) for marker in THIRD_PARTY_MARKERS):
                continue
            for match in POINTER_RE.finditer(str(value)):
                found.append((f"catalogs/{name}.json", key, match.group(1)))
    return found


def _py_string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append(node.value)
    return out


def _py_pointers() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in sorted(CORE.rglob("*.py")) + sorted(ADAPTERS.rglob("*.py")):
        rel = str(path.relative_to(REPO))
        for text in _py_string_constants(path):
            if any(marker in text for marker in THIRD_PARTY_MARKERS):
                continue
            for match in POINTER_RE.finditer(text):
                found.append((rel, f"line text: {match.group(0)!r}", match.group(1)))
    return found


def test_every_settings_pointer_names_a_group_row_or_real_control() -> None:
    titles, rows = _settings_inventory()
    # Real controls deeper inside groups that directions legitimately name. Each is proven
    # present in the page source so this set cannot quietly rot.
    page_src = (CORE / "vool_settings_page.py").read_text(encoding="utf-8")
    controls = {"Save key", "X", "UsePod", "UsePod spending"}
    for control in controls:
        assert control in page_src, f"curated control {control!r} no longer exists on the page"
    groups_ok = titles | rows | controls
    deeper_ok = groups_ok

    problems: list[str] = []
    for source, where, target in _catalog_pointers() + _py_pointers():
        hops = _hops(target)
        if not hops:
            continue
        first = hops[0]
        if not _names_something_real(first, groups_ok):
            if first.startswith("API Keys") and source in BLOCKED_ISLAND_FILES:
                continue
            problems.append(f"{source} {where}: 'Settings → {target.strip()}' names no group or row on the page")
            continue
        for hop in hops[1:]:
            if not _names_something_real(hop, deeper_ok):
                problems.append(f"{source} {where}: hop {hop!r} names no row or control")
    assert not problems, "\n".join(problems)


def test_the_blocked_island_is_still_just_the_pinned_wording() -> None:
    """The tolerated islands must not grow: nowhere else may still say the old name, and
    every island file must still carry its pinned hit, so a fix must shrink the set."""
    pointers = [(source, _hops(target)) for source, _, target in _py_pointers()]
    stray = [
        (source, hops[0])
        for source, hops in pointers
        if hops and hops[0].startswith("API Keys") and source not in BLOCKED_ISLAND_FILES
    ]
    assert not stray, f"new nowhere-directions appeared outside the pinned islands: {stray[:5]}"
    for island_file in BLOCKED_ISLAND_FILES:
        still = any(
            source == island_file and hops and hops[0].startswith("API Keys") for source, hops in pointers
        )
        assert still, f"{island_file} no longer says the old name: shrink BLOCKED_ISLAND_FILES in this test"
