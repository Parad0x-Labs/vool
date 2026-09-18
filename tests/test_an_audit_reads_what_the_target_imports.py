"""An audit that cannot see a file's imports nominates the absence it can see.

Measured live 2026-08-03 against `api/apache/liquefy_apache_repetition_v1.py`. The audit gave the
model that file alone — 210 lines — and the model returned:

    "Decompression crash on valid input due to missing varint helpers ... the module imports
     unpack_varint_buf and zigzag_dec from liquefy_primitives, but those functions are not defined
     in the provided source tree."

All four named helpers ARE defined, in `api/liquefy_primitives.py`, one directory up. The model had
no tools, so it could not open the import to check its own hypothesis; and the adversarial checker
was handed the same single file, so it could not refute the claim either. The turn shipped a false
positive as an "unproven candidate".

The collector already resolved the INBOUND direction — `workspace.search_text` finds every file
that references the target. The outbound direction was never resolved, and its own comment said so:
*"the answer needs no import parsing"*. True for who-references-me; false for what-I-depend-on.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.workspace_audit import _imported_local_modules

FIXTURE = """\
import time
import socket
import io
import zstandard as zstd
import sys
import struct
import json
from collections import defaultdict
from common_zstd import make_cctx
from liquefy_primitives import pack_varint, unpack_varint_buf, zigzag_dec, AdaptiveSearchIndex
"""


def test_the_local_imports_are_found() -> None:
    """The two that matter here are project modules, and they are LAST in the file."""

    found = _imported_local_modules(FIXTURE)

    assert "liquefy_primitives" in found
    assert "common_zstd" in found


def test_the_list_is_not_truncated_before_the_local_imports() -> None:
    """The regression that made the first version of this fix useless.

    Local imports come after the standard library, so a small cap drops exactly the ones worth
    reading. The first implementation reused `_SYMBOL_QUERY_LIMIT` (4) and returned
    `['time', 'socket', 'io', 'zstandard']` — cutting `liquefy_primitives`, the one file that would
    have refuted the finding.

    These cost nothing to keep: the caller matches them against paths it has already listed, so a
    stem matching no project file is discarded for free. The cap in `_target_symbols` exists
    because each symbol there costs a `search_text` call. Different constraint, different rule.
    """

    found = _imported_local_modules(FIXTURE)

    assert len(found) > 4
    assert found.index("liquefy_primitives") > 4, "fixture no longer exercises the ordering"


def test_both_import_forms_are_read() -> None:
    found = _imported_local_modules("import alpha\nfrom bravo import thing\n")

    assert found == ["alpha", "bravo"]


def test_a_dotted_path_resolves_to_its_stem() -> None:
    """`from core.execution.workspace_tools import x` has to match `workspace_tools.py`."""

    assert _imported_local_modules("from core.execution.workspace_tools import x") == [
        "workspace_tools"
    ]


def test_relative_imports_are_skipped() -> None:
    """A leading dot is a package-relative import; its stem does not name a findable path here."""

    assert _imported_local_modules("from . import sibling\nfrom .mod import y") == []


def test_duplicate_imports_appear_once() -> None:
    found = _imported_local_modules("import alpha\nfrom alpha import thing\nimport alpha\n")

    assert found == ["alpha"]


@pytest.mark.parametrize("source", ["", "   ", "# just a comment\n", "not python at all {{"])
def test_unparseable_or_empty_source_yields_nothing(source: str) -> None:
    """The collector must not fail an audit because a file did not parse."""

    assert _imported_local_modules(source) == []


def test_standard_library_names_are_returned_and_harmlessly_ignored() -> None:
    """No allowlist is maintained, on purpose.

    `socket` and `json` are returned, match no path in the project, and are discarded by the caller
    for free. An allowlist of stdlib module names would be a second thing to keep current, and
    would be wrong the first time a project shadowed one.
    """

    found = _imported_local_modules(FIXTURE)

    assert "socket" in found and "json" in found


# --------------------------------------------------------------------------------------
# The wiring. Testing the extractor alone passes whether or not the collector ever calls it.
# --------------------------------------------------------------------------------------


def test_the_collector_pulls_the_imported_module_into_audit_scope(tmp_path, monkeypatch) -> None:
    """Drives the real collector and asserts on the SCOPE it builds.

    The extractor tests above pass whether or not `run_workspace_audit` ever calls it — removing
    the call left every one of them green. This asserts the wiring: that the imported module
    reaches `referencing`, which is what puts it in front of the model.

    It asserts on the scope rather than on the audit report, because the report is a summary and
    an earlier version of this test passed on the filename appearing in the embedded `list_tree`
    listing — the name is there whether or not the file was ever opened.
    """

    import uuid

    import core.agent_runtime.workspace_audit as wa

    root = tmp_path / "packer"
    (root / "api").mkdir(parents=True)
    (root / "api" / "helpers.py").write_text(
        "def unpack_varint_buf(buf, pos):\n    return 0, pos\n", encoding="utf-8"
    )
    (root / "api" / "engine.py").write_text(
        "import struct\n"
        "from helpers import unpack_varint_buf\n\n\n"
        "class Engine:\n"
        "    def decode(self, blob):\n"
        "        return unpack_varint_buf(blob, 0)\n",
        encoding="utf-8",
    )

    seen: dict = {}
    real_scope = wa._related_to_target

    def _capture(target, all_paths, referencing):
        seen["referencing"] = set(referencing)
        seen["scope"] = real_scope(target, all_paths, referencing)
        return seen["scope"]

    monkeypatch.setattr(wa, "_related_to_target", _capture)
    monkeypatch.setattr("core.runtime_task_events.emit_runtime_event", lambda *a, **k: None)

    class _NoFastAnswer:
        def _fast_path_result(self, **kwargs):
            raise AssertionError("the audit must continue to the answer model")

    wa.maybe_handle_workspace_audit_request(
        _NoFastAnswer(),
        "api/engine.py - audit this file please",
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_surface="api",
        source_context={
            "surface": "api",
            "workspace": str(root),
            "workspace_root": str(root),
            "workspace_binding": "project",
            "project_id": root.name,
        },
    )

    assert "api/helpers.py" in seen.get("referencing", set()), (
        "the module the target imports never entered audit scope, so a model asked whether "
        "`unpack_varint_buf` exists still cannot tell"
    )
    assert "api/helpers.py" in seen.get("scope", [])


# --------------------------------------------------------------------------------------
# Reading the module is half the job. The model has to SEE it.
# --------------------------------------------------------------------------------------


def _sources() -> dict:
    return {
        "api/engine.py": (
            "import struct\n"
            "from helpers import unpack_varint_buf, MAGIC\n\n"
            "class Engine:\n    pass\n"
        ),
        "api/helpers.py": (
            'MAGIC = b"\\x28\\xb5"\n'
            "LOWER_case_ignored = 1\n"
            "def unpack_varint_buf(data, pos):\n    return 0, pos\n"
            "def never_imported_by_target(x):\n    return x\n"
            "class Index:\n    pass\n"
        ),
        "README.md": "# not a module\n",
    }


def test_the_imported_module_signatures_reach_the_prompt() -> None:
    """Collecting the file was only half the fix.

    Measured live 2026-08-03: after the collector began READING `api/liquefy_primitives.py`, its
    content still never reached the nomination prompt — `_supporting_context` surfaced the project
    contract and the tests and nothing else. The model was seeing the import STATEMENT, not the
    module, and could still not confirm the names existed.
    """

    from core.agent_runtime.stepped_audit import _imported_module_signatures

    rendered = _imported_module_signatures(_sources(), "api/engine.py")

    assert "api/helpers.py" in rendered
    assert "def unpack_varint_buf" in rendered


def test_a_symbol_the_target_does_not_import_is_still_shown() -> None:
    """The tell that exposed the half-fix.

    `zigzag_enc` is defined in the real module and NOT on the target's import line. It was absent
    from the prompt while the imported names were present — which proved the names were coming from
    the import statement rather than from the module. Showing the module's full surface is what
    makes the difference detectable.
    """

    from core.agent_runtime.stepped_audit import _imported_module_signatures

    rendered = _imported_module_signatures(_sources(), "api/engine.py")

    assert "def never_imported_by_target" in rendered


def test_a_module_level_constant_is_shown_like_a_function() -> None:
    """An import can name a constant as readily as a callable.

    `ZSTD_MAGIC = b"..."` is imported by the real target. A signature list that only collected
    `def`/`class` left the model unable to confirm that name existed — and the first version of
    this did exactly that, because the regex was written with a doubled backslash and matched a
    literal `\\s` instead of whitespace.
    """

    from core.agent_runtime.stepped_audit import _imported_module_signatures

    rendered = _imported_module_signatures(_sources(), "api/engine.py")

    assert "MAGIC" in rendered
    assert "LOWER_case_ignored" not in rendered, "a mixed-case name is not a module constant"


def test_unrelated_files_are_not_pasted_into_the_prompt() -> None:
    """The excerpt budget belongs to the target. Only what it imports earns space."""

    from core.agent_runtime.stepped_audit import _imported_module_signatures

    rendered = _imported_module_signatures(_sources(), "api/engine.py")

    assert "README" not in rendered


def test_a_target_with_no_local_imports_adds_nothing() -> None:
    from core.agent_runtime.stepped_audit import _imported_module_signatures

    sources = {"api/solo.py": "import struct\n\nclass A:\n    pass\n", "api/other.py": "def x():\n    pass\n"}

    assert _imported_module_signatures(sources, "api/solo.py") == ""


def test_supporting_context_carries_the_signatures_into_the_prompt() -> None:
    """Through `_supporting_context`, which is what the nomination prompt actually concatenates.

    Every test above calls `_imported_module_signatures` directly, so all of them pass whether or
    not `_supporting_context` ever calls it — removing the call left them green. This asserts the
    join, which is the only reason any of it reaches a model.
    """

    from types import SimpleNamespace

    from core.agent_runtime.stepped_audit import _supporting_context

    evidence = SimpleNamespace(sources=_sources())

    rendered = _supporting_context(evidence, "api/engine.py")

    assert "def unpack_varint_buf" in rendered, (
        "the imported module's signatures never reached the prompt the model is sent"
    )
