"""The dev install must be able to collect the suite: every third-party module the
test suite's global fixtures import unconditionally is a declared dependency of the
base install or the dev extra.

RED ORIGIN (regression provenance, authored during the 2026-10-03 repair): the
Ubuntu 22.04 desktop lane of CI run 37039146436 (job 110944670391) never reached a
single test -- 50 setup errors, every one ``ModuleNotFoundError: No module named
'zstandard'``, all raised from the autouse Liquefy isolation fixture's
``from core.liquefy import hooks`` (tests/conftest.py) through ``core.liquefy.api``
to the vendored columnar codec's module-top ``import zstandard as zstd`` -- a
vendored file that may not be edited. The dev extra installs the suite's tools but
not that module; Ubuntu 24.04 passed the same workflow only because its
``--system-site-packages`` venv inherited zstandard 0.22.0 from the runner image,
an unrecorded inherited package rather than a declared dependency. The frozen red is
the captured CI job plus this file's pre-repair run; the repair declares the module
in the dev extra, beside the httpx line that closed the identical failure class for
``tests/test_daemon_survives_concurrent_load.py``.

This control is COLD by construction: it reads the DECLARATION (pyproject.toml)
against a static walk of the source Python actually executes at collection time --
conftest's own module and fixture-body imports, then module-level imports of the
first-party files they pull in (submodule from-imports resolved, and the
try/except-ImportError idiom recognized as the optional lane it is). No amount of
globally installed packaging can mask a missing declaration, and a module moved to
an optional extra the workflow does not install turns this red again.
"""
from __future__ import annotations

import re
import sys
from importlib.metadata import distributions
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The repository's own top-level packages ([tool.setuptools.packages.find] include).
_FIRST_PARTY = frozenset(
    {
        "adapters",
        "apps",
        "channels",
        "core",
        "installer",
        "network",
        "ops",
        "retrieval",
        "relay",
        "sandbox",
        "storage",
        "tools",
    }
)

_WALKER = "tests/_collection_import_walker.py"


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _declared_requirements() -> set[str]:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = project["project"]["dependencies"]
    deps += project["project"]["optional-dependencies"]["dev"]
    return {_normalize(re.split(r"[<>=!;\[ ]", requirement.strip(), maxsplit=1)[0]) for requirement in deps}


def _installed_module_map() -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for dist in distributions():
        name = _normalize(dist.metadata["Name"]) if dist.metadata["Name"] else ""
        for module in (dist.read_text("top_level.txt") or "").split():
            mapping.setdefault(module, set()).add(name)
    return mapping


def test_every_collection_import_is_a_declared_dependency_of_the_dev_install():
    import json
    import subprocess

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / _WALKER), str(REPO_ROOT), ",".join(sorted(_FIRST_PARTY))],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    collected: dict[str, list] = json.loads(result.stdout)
    assert collected, "the walker found no imports; it is broken and proves nothing"

    declared = _declared_requirements()
    installed = _installed_module_map()
    undeclared = {}
    for module, where in sorted(collected.items()):
        providers = {_normalize(module)} | installed.get(module, set())
        if not providers & declared:
            undeclared[module] = where
    assert not undeclared, (
        "modules the suite imports unconditionally at collection time but neither the "
        "base install nor the dev extra declares (an environment without them cannot "
        f"collect a single test): {undeclared}"
    )
