"""Static walker for tests/test_dev_extra_declares_collection_imports.py.

Prints, as JSON, every third-party top-level module the test suite imports
unconditionally at COLLECTION time: tests/conftest.py's own imports (module level
and inside its fixture bodies -- autouse fixture setup is part of collection), then
the module-level imports of every first-party file those reach, recursively, with
`from pkg import submodule` resolved to the submodule file. Imports inside a
try/except ImportError block are the optional-lane idiom and are not required.

Deterministic and environment-independent: it reads source, imports nothing from
the project, and is run as a bare script by the controlling test.
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
FIRST_PARTY = frozenset(sys.argv[2].split(","))
#: The suite's own package: first-party for walking, never a dependency.
OWN_PACKAGES = frozenset({"tests"})
seen: set[Path] = set()
third: dict[str, list] = {}


def _resolve(module: str, names: tuple[str, ...] = ()) -> list[Path]:
    top = module.split(".")[0]
    if top not in FIRST_PARTY:
        return []
    base = ROOT.joinpath(*module.split("."))
    files = [candidate for candidate in (base.with_suffix(".py"), base / "__init__.py") if candidate.exists()]
    # `from pkg import submodule` executes the package AND that submodule.
    files += [base / f"{name}.py" for name in names if (base / f"{name}.py").exists()]
    return files


def _walk(path: Path, include_bodies: bool) -> None:
    if path in seen:
        return
    seen.add(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = path.parent
    optional: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
            (isinstance(handler.type, ast.Name) and handler.type.id in ("ImportError", "ModuleNotFoundError"))
            or handler.type is None
            for handler in node.handlers
        ):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    optional.add(id(sub))
    for node in ast.walk(tree):
        if not include_bodies and getattr(node, "col_offset", 0) > 0:
            continue
        targets: list[tuple[str, tuple[str, ...]]] = []
        if isinstance(node, ast.Import) and id(node) not in optional:
            targets = [(alias.name, ()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and id(node) not in optional:
            if node.level == 0:
                targets = [(node.module or "", tuple(alias.name for alias in node.names))]
            else:
                # Relative: `from . import x` / `from .pkg import x`, against this file's package.
                parts = package.relative_to(ROOT).parts
                if len(parts) < node.level - 1:
                    continue
                base_parts = parts[: len(parts) - (node.level - 1)]
                module_parts = base_parts + (tuple(node.module.split(".")) if node.module else ())
                targets = [(".".join(module_parts), tuple(alias.name for alias in node.names))]
        for module, names in targets:
            if not module:
                continue
            top = module.split(".")[0]
            if top in FIRST_PARTY:
                for found in _resolve(module, names):
                    _walk(found, include_bodies=False)
            elif top not in sys.stdlib_module_names and top != "__future__" and top not in OWN_PACKAGES:
                third.setdefault(top, [str(path.relative_to(ROOT)), node.lineno])


_walk(ROOT / "tests" / "conftest.py", include_bodies=True)
json.dump(third, sys.stdout)
