"""Which modules can open a socket, counted rather than remembered.

`core.remote_fetch_policy.open_remote` calls itself "the ONE outbound HTTP door", and its own
docstring records the day that claim was false: `searxng_client.py` had its own socket, so a turn
that FORBADE remote fetching still reached the endpoint with the user's query and no call appeared
in the effect account. The repair was a door. What was missing was anything that could tell you
whether the door was still the only one.

The two tests that pin the door's property are module-scoped -- they drive `tools/web/web_research`
and assert on that lane. Nothing was tree-scoped, which is why a raw `urlopen` could be added
anywhere and nothing would notice.

This module counts them. It walks the source tree for constructs that reach the network directly
and reports every module holding one. `KNOWN_DIRECT_EGRESS` is the ledger of the ones that exist
today, each with the reason it is still there; the test asserts the scan finds EXACTLY that set.
A new one fails the gate. Removing one fails it too -- and the fix is to delete the entry, which
is a change somebody has to make deliberately, in a diff, with the reason attached.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: Roots that ship as product code. Tests, proofs and validation logs are excluded: a test that
#: opens a socket is a test's business, and pinning them here would make the ledger noise.
SCANNED_ROOTS: tuple[str, ...] = ("core", "adapters", "relay", "tools", "retrieval", "network", "storage", "apps")

#: The module that IS the door, and the one place a raw transport call is the point.
DOOR_MODULES: frozenset[str] = frozenset({"core/remote_fetch_policy.py"})

#: Constructs that reach the network without crossing the door.
DIRECT_EGRESS_CALLS: frozenset[str] = frozenset(
    {
        "urllib.request.urlopen",
        "urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "requests.request",
        "requests.head",
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
    }
)


#: Importing one of these into product code is itself a transport capability, whether or not the
#: scan can see the call.
TRANSPORT_MODULES: frozenset[str] = frozenset({"requests", "httpx", "aiohttp", "websockets"})


@dataclass(frozen=True)
class EgressSite:
    path: str
    line: int
    construct: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.construct}"


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def scan_file(path: Path, *, repo_root: Path) -> list[EgressSite]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    relative = str(path.relative_to(repo_root))
    sites: list[EgressSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name and (name in DIRECT_EGRESS_CALLS or name.endswith(".urlopen")):
                sites.append(EgressSite(path=relative, line=node.lineno, construct=f"{name}(...)"))
            continue
        # An import counts too. `core/cloud_transport.py` binds `requests.request` to an attribute
        # and calls it through `self._requester(...)`, which no call-name scan can see; the import
        # is what it cannot hide. Catching the import is what keeps this census from under-counting
        # exactly the highest-volume egress in the product.
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = str(alias.name).split(".")[0]
                if top in TRANSPORT_MODULES:
                    sites.append(EgressSite(path=relative, line=node.lineno, construct=f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and not node.level:
            top = str(node.module or "").split(".")[0]
            if top in TRANSPORT_MODULES:
                sites.append(EgressSite(path=relative, line=node.lineno, construct=f"from {node.module} import ..."))
    return sites


def scan_tree(repo_root: Path | None = None) -> list[EgressSite]:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    sites: list[EgressSite] = []
    for top in SCANNED_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(root))
            if relative in DOOR_MODULES:
                continue
            sites.extend(scan_file(path, repo_root=root))
    return sites


def modules_with_direct_egress(repo_root: Path | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for site in scan_tree(repo_root):
        counts[site.path] = counts.get(site.path, 0) + 1
    return counts


__all__ = [
    "DIRECT_EGRESS_CALLS",
    "DOOR_MODULES",
    "SCANNED_ROOTS",
    "TRANSPORT_MODULES",
    "EgressSite",
    "modules_with_direct_egress",
    "scan_file",
    "scan_tree",
]
