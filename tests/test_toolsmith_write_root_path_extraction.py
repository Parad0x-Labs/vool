"""TOOLSMITH path-extraction repair: D23 (relative paths mis-tokenized as rooted) and D16
(content payloads mis-read as write destinations), both in `write_root_honesty.py`.

D23 -- confirmed 2026-08-07: `_ROOTED_PATH_RE`'s negative lookbehind excluded word characters,
backtick/quote characters, `/`, `~`, `\\` and `-`, but not a bare `.`. A relative path like
`./foo.txt` or `../sibling.txt` has its own `/` preceded by exactly that unexcluded `.`, so the
regex matched STARTING at that slash and reported the relative path as the absolute path
`/foo.txt` or `/sibling.txt` -- which then failed the reachability check and preempted the whole
turn with a false "I can't write there" refusal, for a path that was never outside the workspace.

D16 -- confirmed 2026-08-07: the same scan ran over the ENTIRE turn text, including any file
content the user dictated. "Create vool_test_exec.sh with contents: #!/bin/sh" has a rooted-
looking `/bin/sh` inside the shebang line; nothing distinguished it from a destination, so it was
read as a request to write to `/bin/sh` and refused a turn that never asked to write there at all.

ANVIL round 2, same day: the first D16 fix (a single global truncation point at the first guessed
content marker) was itself confirmed insufficient two ways:
- F1: a shebang with no marker word in front of it ("create ./b.sh\n#!/bin/sh", "content
  #!/usr/bin/env python3" inline, "- #!/bin/zsh" after a dash) still false-refused.
- F2: truncating at the FIRST marker anywhere in the turn discarded a real destination named
  AFTER it ("I have a folder containing: old logs. Now create /tmp/x" never saw `/tmp/x` at all).
- F7: `create a config from https://example.com/x` mined the URL's `//example.com/x` authority
  section as a rooted destination.

Replaced with per-candidate classification (`_is_payload_span`): each rooted match is judged on
its own local context -- shebang-adjacency, code-fence membership, same-clause content marker --
never by where the scan happened to be cut off. `_is_url_authority` excludes URL authority
sections structurally, by recognizing a URI scheme immediately before a `//`.

Both are extraction bugs upstream of the planner, Toolsmith dispatch, canonical resolution, and
permission check -- fixed by tightening what counts as a rooted DESTINATION, never by relaxing
containment. `resolve_workspace_path`'s traversal defense is untouched and is regression-locked
below, with a real-dispatcher mutation test (not a mock-testing-itself) proving it.
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

import core.agent_runtime.write_root_honesty as write_root_honesty
from core.agent_runtime.write_root_honesty import (
    requested_rooted_paths,
    unreachable_write_target,
)
from core.execution.artifacts import _load_mutation_records, content_sha256, session_key_for_workspace
from core.execution.workspace_tools import resolve_workspace_path
from core.runtime_execution_tools import execute_runtime_tool


def _sc(workspace: str) -> dict[str, object]:
    return {"workspace": workspace}


# --------------------------------------------------------------------------------------
# D23 -- relative-path regression matrix
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "create ./foo.txt with hello",
        "create ./subdir/foo.txt with hello",
        "create subdir/foo.txt with hello",
        "modify ../sibling.txt please",
        "touch ../../sibling.txt now",
        "please build ./deep/nested/dir/file.txt for me",
    ],
)
def test_relative_paths_are_never_reported_as_unreachable(tmp_path, prompt: str) -> None:
    """None of these name a rooted destination at all -- the honesty gate must stay silent and
    let the real workspace-relative resolver (which DOES handle these) take the request."""
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == ""


def test_relative_paths_produce_no_rooted_candidates() -> None:
    """The extraction itself, not just the end-to-end refusal, must not manufacture a rooted
    path out of a relative one."""
    assert requested_rooted_paths("create ./foo.txt with hello") == []
    assert requested_rooted_paths("modify ../sibling.txt please") == []


def test_absolute_and_tilde_paths_still_behave_exactly_as_before(tmp_path) -> None:
    """The fix must not weaken the original gate: a genuinely rooted, unreachable absolute path
    is still caught, and a genuinely rooted, reachable `~` path is still let through."""
    sc = _sc(str(tmp_path))
    assert unreachable_write_target("create /tmp/vool_d23_probe_evil.txt with hello", source_context=sc) == (
        "/tmp/vool_d23_probe_evil.txt"
    )
    assert unreachable_write_target("create ~/Desktop/legit.txt with hello", source_context=sc) == ""


# --------------------------------------------------------------------------------------
# Traversal -- containment must not be weakened by this fix
# --------------------------------------------------------------------------------------


def test_traversal_escape_is_still_refused_by_real_containment(tmp_path) -> None:
    """`../../etc/passwd`-style escapes are not this gate's job (they are not ROOTED) and never
    were -- they are refused by `resolve_workspace_path`'s own containment check, which this
    change does not touch. A relative path that stays silent here must still be refused THERE.
    """
    workspace_root = tmp_path.resolve()
    assert unreachable_write_target(
        "create ../../etc/passwd_probe with hello", source_context=_sc(str(workspace_root))
    ) == ""
    with pytest.raises(ValueError, match="escapes the active workspace"):
        resolve_workspace_path("../../etc/passwd_probe", workspace_root=workspace_root)


# --------------------------------------------------------------------------------------
# D16 -- content is not addressing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        'Create vool_test_exec.sh with contents: #!/bin/sh\necho "original"',
        "Create readme.txt containing: see /etc/motd for the banner text",
        "Create notes.md that says: download it from https://example.com/a/b or /opt/legacy",
        "Create win_notes.txt with content: the old path was C:\\old\\install\\path",
        'Create script.sh with these contents:\n```\n#!/bin/sh\necho hi\n```',
    ],
)
def test_content_payload_paths_are_never_read_as_destinations(tmp_path, prompt: str) -> None:
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == ""


def test_destination_named_before_content_marker_is_still_caught(tmp_path) -> None:
    """The destination itself, named BEFORE the content marker, must still be checked."""
    prompt = "create /tmp/vool_d16_probe_evil.sh with contents: #!/bin/sh\necho hi"
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == "/tmp/vool_d16_probe_evil.sh"


# --------------------------------------------------------------------------------------
# F1 -- a shebang with no marker word in front of it must not false-refuse
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "create ./b.sh\n#!/bin/sh\necho hi",
        "write ./scripts/run.sh:\n#!/bin/bash\nset -e",
        "make ./a.sh executable, content #!/usr/bin/env python3",
        "create ./c.sh - #!/bin/zsh\necho hi",
        "create ./a.sh\n#!/bin/sh\necho hi",
        "create ./b.sh with contents:\n#!/bin/bash",
        "create ./c.py\n#!/usr/bin/env python3",
        "create ./d.sh - #!/bin/zsh",
    ],
)
def test_shebang_without_a_marker_word_never_false_refuses(tmp_path, prompt: str) -> None:
    """A shebang is payload evidence by what it IS (the two characters `#!` directly before the
    path), not by whether some marker word happens to sit in front of it somewhere in the turn."""
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == ""


# --------------------------------------------------------------------------------------
# F2 -- a real destination named AFTER a content marker must still be caught, not discarded
# just because a marker appeared earlier in the turn
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Please do the following:\n    - be careful\ncreate a file at /tmp/x",
        "I have a folder containing: old logs. Now create /tmp/x",
        "Run ```ls``` first, then create /tmp/x",
    ],
)
def test_destination_after_a_marker_is_not_discarded(tmp_path, prompt: str) -> None:
    """The old global-truncation design silently lost these. Per-candidate classification must
    catch `/tmp/x` regardless of what indentation, marker word, or code fence precedes it,
    because none of those things govern the `/tmp/x` candidate ITSELF."""
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == "/tmp/x"


# --------------------------------------------------------------------------------------
# R1 -- ANVIL round 3: multi-line dictated content must survive newline boundaries. The
# per-candidate design's ORIGINAL clause-bounded marker check ended a content marker's reach at
# the next newline or sentence, so line 2 onward of any multi-line dictated content (a script, a
# config file, a note) was mined as a destination the moment it crossed a newline -- the exact
# defect class D16 was supposed to close, reopened by the fix meant to close it.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        # Shell script.
        "Create a script whose content contains:\n\nprefix=/opt/app\nPREFIX=/usr/local\n"
        "path=/etc/thing\ncd /srv/app\nsee /var/log/app.log\nroot=/opt/data",
        # INI/config.
        "Write config.ini with contents:\nprefix=/opt/app\nPREFIX=/usr/local\npath=/etc/thing",
        # Plain text note.
        "Create note.txt containing:\nsee /var/log/app.log for details\nroot=/opt/data is the owner",
        # Multi-line JSON/YAML-like content.
        'Write data.json with contents:\n{\n  "path": "/etc/thing",\n  "root": "/opt/data"\n}',
        # Content containing several rooted paths.
        "Write a script with contents:\ncd /srv/app\nexport PATH=/opt/bin:/usr/local/bin\n"
        "exec /opt/app/run.sh",
    ],
)
def test_multiline_dictated_content_never_false_refuses(tmp_path, prompt: str) -> None:
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == ""


def test_multiline_content_followed_by_a_genuine_second_destination_is_still_found(tmp_path) -> None:
    """The required counterpart to the above: multi-line content ending, followed by an actually
    NEW requested destination, must not be swallowed by the content span either."""
    prompt = (
        "Create ./config.ini with contents:\nprefix=/opt/app\nPREFIX=/usr/local\n\n"
        "Now also save a backup at /tmp/vool_r1_second_dest.ini"
    )
    assert (
        unreachable_write_target(prompt, source_context=_sc(str(tmp_path)))
        == "/tmp/vool_r1_second_dest.ini"
    )


def test_multiline_content_provider_identity_is_exact(tmp_path) -> None:
    """The destination identity returned must be the EXACT candidate string, not a mangled or
    truncated form -- this is what an executor/provider keys off of."""
    prompt = "Write app.cfg with contents:\nprefix=/opt/app\n\nAlso write a copy at /tmp/vool_exact_id.cfg"
    assert (
        unreachable_write_target(prompt, source_context=_sc(str(tmp_path)))
        == "/tmp/vool_exact_id.cfg"
    )


# --------------------------------------------------------------------------------------
# F1 residuals -- POSIX-legal shebang whitespace, and natural unmarked content forms that must
# be recognized structurally (a small closed set of content-introducing phrases), not by
# suppressing every rooted string.
# --------------------------------------------------------------------------------------


def test_posix_legal_shebang_with_a_space_is_still_payload(tmp_path) -> None:
    assert unreachable_write_target(
        "create ./b.sh\n#! /bin/sh\necho hi", source_context=_sc(str(tmp_path))
    ) == ""


@pytest.mark.parametrize(
    "prompt",
    [
        "create ./notes.txt containing /etc/hosts",
        "create ./notes.txt that mentions /usr/local/bin",
        "add a line /opt/foo to ./notes.txt",
        "contents are /bin/sh",
    ],
)
def test_natural_unmarked_content_forms_never_false_refuse(tmp_path, prompt: str) -> None:
    assert unreachable_write_target(prompt, source_context=_sc(str(tmp_path))) == ""


def test_unmarked_forms_do_not_suppress_every_rooted_string(tmp_path) -> None:
    """Recognizing natural content phrasing must not become suppressing all rooted strings --
    an unreachable destination named plainly, with none of the content phrasing nearby, is still
    caught."""
    assert unreachable_write_target(
        "create /tmp/vool_f1_plain_dest.txt with hello", source_context=_sc(str(tmp_path))
    ) == "/tmp/vool_f1_plain_dest.txt"


# --------------------------------------------------------------------------------------
# F2 residual -- a destination named via a preposition AFTER inline dictated content, in the
# SAME clause as the content marker, must still be found.
# --------------------------------------------------------------------------------------


def test_destination_named_by_preposition_after_inline_content_is_found(tmp_path) -> None:
    assert (
        unreachable_write_target(
            "Create a file with contents: hello at /tmp/vool_f2_inline_dest.txt",
            source_context=_sc(str(tmp_path)),
        )
        == "/tmp/vool_f2_inline_dest.txt"
    )


def test_payload_rooted_path_is_distinguished_from_the_actual_destination_in_one_turn(tmp_path) -> None:
    """The same turn carries BOTH a payload rooted path and a real destination -- the payload
    must not be returned, and the real destination must be."""
    prompt = "Create a file with contents: /opt/legacy at /tmp/vool_f2_distinguish.txt"
    assert (
        unreachable_write_target(prompt, source_context=_sc(str(tmp_path)))
        == "/tmp/vool_f2_distinguish.txt"
    )


# --------------------------------------------------------------------------------------
# F7 -- a URL's authority section is not a filesystem destination
# --------------------------------------------------------------------------------------


def test_url_authority_is_never_a_rooted_candidate() -> None:
    assert requested_rooted_paths("create a config from https://example.com/x") == []


def test_url_in_the_turn_never_false_refuses(tmp_path) -> None:
    assert unreachable_write_target(
        "create a config from https://example.com/x", source_context=_sc(str(tmp_path))
    ) == ""


def test_url_does_not_mask_a_real_destination_in_the_same_turn(tmp_path) -> None:
    """A URL elsewhere in the turn must not swallow or interfere with a genuine destination."""
    prompt = "download the schema from https://example.com/x and save it to /tmp/vool_f7_probe.json"
    assert (
        unreachable_write_target(prompt, source_context=_sc(str(tmp_path)))
        == "/tmp/vool_f7_probe.json"
    )


# --------------------------------------------------------------------------------------
# Full disposable proof: the exact repro phrase, driven through the real dispatcher end to end.
# Create (relative-dotted path + shebang content) -> chmod -> modify -> hash -> rollback ->
# verify content, mode, and ledger. Touches no other file in the workspace.
# --------------------------------------------------------------------------------------


class ShebangAndRelativePathFullLifecycleTests(unittest.TestCase):
    def test_full_disposable_lifecycle_survives_the_confirmed_repro(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            workspace_root = Path(workspace_dir).resolve()
            source_context = _sc(str(workspace_root))
            prompt = 'Create ./vool_test_exec.sh with contents: #!/bin/sh\necho "original"'

            # The honesty gate must not preempt this turn at all.
            self.assertEqual(unreachable_write_target(prompt, source_context=source_context), "")

            content_v1 = '#!/bin/sh\necho "original"\n'
            created = execute_runtime_tool(
                "workspace.write_file",
                {"path": "./vool_test_exec.sh", "content": content_v1},
                source_context=source_context,
            )
            self.assertIsNotNone(created)
            self.assertTrue(created.ok, created.response_text)

            target = workspace_root / "vool_test_exec.sh"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), content_v1)
            self.assertEqual(content_sha256(target.read_text(encoding="utf-8")), content_sha256(content_v1))

            # External chmod -- rollback must restore this exact mode later, not whatever the
            # next write happens to leave behind.
            os.chmod(target, 0o755)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o755)

            content_v2 = '#!/bin/sh\necho "modified-version"\n'
            modified = execute_runtime_tool(
                "workspace.write_file",
                {
                    "path": "vool_test_exec.sh",
                    "content": content_v2,
                    "expected_hash": content_sha256(content_v1),
                },
                source_context=source_context,
            )
            self.assertIsNotNone(modified)
            self.assertTrue(modified.ok, modified.response_text)
            self.assertEqual(target.read_text(encoding="utf-8"), content_v2)

            rolled_back = execute_runtime_tool(
                "workspace.rollback_last_change", {}, source_context=source_context
            )
            self.assertIsNotNone(rolled_back)
            self.assertTrue(rolled_back.ok, rolled_back.response_text)
            self.assertEqual(target.read_text(encoding="utf-8"), content_v1)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o755)

            # Ledger evidence: the rolled-back record exists, is marked rolled back, and carries
            # the mode this rollback had to restore.
            session_key = session_key_for_workspace(None, workspace_root)
            records = _load_mutation_records(session_key)
            matching = [r for r in records if r.get("workspace_root") == str(workspace_root)]
            self.assertTrue(matching, "expected a mutation record for this workspace")
            last_record = matching[-1]
            self.assertIsNotNone(last_record.get("rolled_back_at"))
            self.assertEqual(last_record["changes"][0].get("before_mode"), 0o755)

            # Touched no other file in the workspace.
            self.assertEqual(
                sorted(p.name for p in workspace_root.iterdir()), ["vool_test_exec.sh"]
            )


# --------------------------------------------------------------------------------------
# Mutation / sabotage: each fix is load-bearing, not incidentally green.
# --------------------------------------------------------------------------------------

# The exact pre-repair regex -- lookbehind excludes everything the fixed one does EXCEPT `.`.
_PRE_REPAIR_ROOTED_PATH_RE = re.compile(
    r"(?<![\w`'\"/~\\-])(?P<path>(?:~(?=[/\\])|[A-Za-z]:[\\/]|/)[^\s`'\"<>|]*)"
)


class MutationSabotageTests(unittest.TestCase):
    def test_removing_dot_exclusion_turns_the_relative_path_test_red(self) -> None:
        """Mutation 1: put back the lookbehind exactly as it was before D23. The relative-path
        regression test must then fail, proving it exercises the fix and not a coincidence."""
        with mock.patch.object(write_root_honesty, "_ROOTED_PATH_RE", _PRE_REPAIR_ROOTED_PATH_RE):
            regressed = unreachable_write_target(
                "create ./foo.txt with hello", source_context=_sc(tempfile.gettempdir())
            )
        self.assertEqual(regressed, "/foo.txt", "sabotage did not reproduce the D23 symptom")

    def test_neutralizing_payload_classification_turns_the_shebang_test_red(self) -> None:
        """Mutation 2 (ANVIL round 2): make every candidate look like a destination again (the
        pre-repair behavior), regardless of shebang/fence/marker context. The shebang-content
        test must then fail."""
        with mock.patch.object(write_root_honesty, "_is_payload_span", lambda *a, **k: False):
            regressed = unreachable_write_target(
                'Create vool_test_exec.sh with contents: #!/bin/sh\necho "original"',
                source_context=_sc(tempfile.gettempdir()),
            )
        self.assertEqual(regressed, "/bin/sh", "sabotage did not reproduce the D16 symptom")

    def test_removing_url_authority_exclusion_turns_the_f7_test_red(self) -> None:
        """Mutation for F7: without `_is_url_authority`, the URL's `//host/...` section is mined
        as a rooted candidate again."""
        with mock.patch.object(write_root_honesty, "_is_url_authority", lambda *a, **k: False):
            regressed = unreachable_write_target(
                "create a config from https://example.com/x", source_context=_sc(tempfile.gettempdir())
            )
        self.assertEqual(regressed, "//example.com/x", "sabotage did not reproduce the F7 symptom")

    def test_collapsing_traversal_to_root_turns_the_containment_test_red(self) -> None:
        """Mutation 4 (ANVIL F8 repair): the ORIGINAL version of this test patched
        `resolve_workspace_path` and then called the mock directly -- it tested the mutation, not
        whether the real guarded call path (`execute_runtime_tool("workspace.write_file", ...)`)
        is actually protected by it. ANVIL confirmed that shape is vacuous: it stays green even
        when the real containment is broken, because nothing routes through the patched name.

        Fixed here by patching the exact symbol `core.runtime_execution_tools` imports containment
        through (`resolve_workspace_path_impl`) and driving the traversal attempt through the real
        dispatcher end to end. With real containment, `workspace.write_file` refuses the escape
        and nothing is written outside the workspace. With the broken collapse-`..`-to-root
        implementation substituted in, the exact same call SUCCEEDS and writes the file outside
        the workspace -- proving this test depends on the real containment, not on the mock."""

        def _broken_collapse_dotdot(raw_path, *, workspace_root):
            raw = str(raw_path or "")
            while raw.startswith("../") or raw.startswith("./"):
                raw = raw.split("/", 1)[1] if "/" in raw else ""
            return (workspace_root / raw).resolve()

        with tempfile.TemporaryDirectory() as outer, tempfile.TemporaryDirectory() as inner_parent:
            workspace_root = Path(inner_parent) / "workspace"
            workspace_root.mkdir()
            traversal_path = f"../../{Path(outer).name}/etc/passwd_probe"
            # What the broken resolver actually does with this input: it does not walk `..`
            # through real parents (that would be the genuine escape) -- it drops the leading
            # `..` segments and joins what's left onto the workspace root, silently re-rooting
            # the traversal instead of refusing it. That mis-resolution, not a literal escape
            # past the workspace boundary, is the exact defect this sabotage reproduces.
            collapsed_landing = workspace_root / Path(outer).name / "etc" / "passwd_probe"
            source_context = _sc(str(workspace_root))

            # Baseline: real containment refuses the traversal through the real dispatcher.
            baseline = execute_runtime_tool(
                "workspace.write_file",
                {"path": traversal_path, "content": "pwned"},
                source_context=source_context,
            )
            self.assertIsNotNone(baseline)
            self.assertFalse(baseline.ok)
            self.assertFalse(collapsed_landing.exists())

            # Sabotage: substitute the broken resolver at the exact name the dispatcher calls
            # through, then repeat the identical call via the identical guarded path.
            with mock.patch(
                "core.runtime_execution_tools.resolve_workspace_path_impl",
                side_effect=_broken_collapse_dotdot,
            ):
                sabotaged = execute_runtime_tool(
                    "workspace.write_file",
                    {"path": traversal_path, "content": "pwned"},
                    source_context=source_context,
                )

            self.assertIsNotNone(sabotaged)
            self.assertTrue(
                sabotaged.ok,
                "sabotage did not reproduce the mis-resolution through the real guarded call path",
            )
            self.assertTrue(
                collapsed_landing.exists(),
                "sabotage should have silently re-rooted the traversal through the real dispatcher",
            )

    def test_restoring_newline_as_unconditional_terminator_turns_multiline_content_red(self) -> None:
        """Mandatory mutation 1 (ANVIL round 3): simulate the pre-R1 clause-bounded marker reach,
        where a content marker's governance ends at the very next newline regardless of what
        follows -- exactly the defect this round closed. Multi-line dictated content must then be
        mined as a destination from line 2 onward."""

        def _newline_bounded_spans(text, candidate_starts):
            spans = []
            for marker in write_root_honesty._CONTENT_MARKER_RE.finditer(text):
                span_start = marker.end()
                newline_pos = text.find("\n", span_start)
                span_end = newline_pos if newline_pos != -1 else len(text)
                spans.append((span_start, span_end))
            return spans

        with mock.patch.object(
            write_root_honesty, "_content_marker_spans", _newline_bounded_spans
        ):
            regressed = unreachable_write_target(
                "Write config.ini with contents:\nprefix=/opt/app\nPREFIX=/usr/local",
                source_context=_sc(tempfile.gettempdir()),
            )
        self.assertEqual(regressed, "/opt/app", "sabotage did not reproduce the R1 symptom")

    def test_suppressing_the_preposition_bound_turns_f2_red(self) -> None:
        """Mandatory mutation 2: without a way to end a content span early, a marker's reach
        swallows a genuine destination named later in the SAME clause via a preposition ("...
        contents: hello AT /tmp/foo.txt") -- proving the preposition bound is what F2 depends on,
        not a lucky coincidence of the span design."""
        with mock.patch.object(
            write_root_honesty, "_is_destination_preposition_governed", lambda *a, **k: False
        ):
            regressed = unreachable_write_target(
                "Create a file with contents: hello at /tmp/vool_f2_mutation_probe.txt",
                source_context=_sc(tempfile.gettempdir()),
            )
        self.assertEqual(regressed, "", "sabotage did not reproduce the F2 symptom")


if __name__ == "__main__":
    unittest.main()
