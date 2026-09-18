"""The C12 join gate: no release-critical row may rest on prose or on bare existence.

The mechanical law under test:

* every release-critical row of the acceptance matrix resolves against the tree —
  either its evidence names a path/symbol or offered intent that exists, or the
  row carries a TYPED authority (caller / served / refusal / obsolete) whose
  edges, tokens and node ids validate byte for byte;
* prose cannot satisfy a release-critical row: swapping a typed authority for the
  row's own evidence sentence must fall out of resolution and go red;
* EXISTENCE CANNOT EITHER. The sabotage below severs the actual evidence — an
  unrelated existing function named as the caller, the callee swapped for a real
  symbol nobody calls, a served token that lives only in a comment or in an
  unrelated test, a refusal pointed at a real function that refuses nothing, a
  named test whose body never asserts the claimed token. Each must fail, because
  a list of existing symbols is not a call chain.
"""
from __future__ import annotations

import copy
import json
import textwrap

import pytest

from tools.code_assistant_matrix_join import (
    MATRIX,
    gate,
    join,
    validate_authority,
)

# The eight release-critical rows that carried prose evidence at the frozen base,
# and the kind of authority that now stands under each.
TYPED_AUTHORITIES = {
    "generated/vendored/binary/oversized detection": "caller",
    "explain which files shaped the decision": "caller",
    "read exact ranges and encodings": "caller",
    "never push by default": "served",
    "typed plan with milestones and dependencies": "caller",
    "unsupported tools visible before failure": "caller",
    "every mutation and command attributable": "caller",
    "network / public writes / spending / destructive need their authority": "refusal",
}


def _rows():
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    return {r["capability"]: r for r in payload["rows"] if r.get("release_critical")}


def _authority_of(capability: str) -> dict:
    row = _rows()[capability]
    return copy.deepcopy(row["authority"])


def test_the_shipped_matrix_passes_the_release_gate():
    summary = join()
    assert summary["rows"] == 101
    assert summary["release_critical_prose_only"] == 0
    assert summary["release_critical_absent"] == 0
    assert summary["authorities"] == 8
    assert gate(summary) == 0


def test_each_prose_row_now_carries_the_expected_typed_authority():
    rows = _rows()
    for capability, kind in TYPED_AUTHORITIES.items():
        row = rows[capability]
        assert row["authority_resolved"] is True, capability
        assert row["authority"]["kind"] == kind, capability
        ok, why = validate_authority(row["authority"])
        assert ok, (capability, why)


@pytest.mark.parametrize("capability", sorted(TYPED_AUTHORITIES))
def test_sabotage_prose_cannot_replace_a_typed_reference(capability, tmp_path):
    """Replace a valid typed reference with the row's own prose sentence: the row
    must fall out of resolution and the gate must fail. A matrix that satisfies
    the gate with a sentence must be impossible to build."""
    row = _rows()[capability]
    prose = str(row["evidence"])
    assert prose.strip(), capability

    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    sabotaged = 0
    for candidate in payload["rows"]:
        if candidate.get("release_critical") and candidate.get("capability") == capability:
            candidate["authority"] = prose  # the exact sabotage under test
            sabotaged += 1
    assert sabotaged == 1
    poisoned = tmp_path / "ACCEPTANCE_MATRIX.json"
    poisoned.write_text(json.dumps(payload), encoding="utf-8")

    summary = join(matrix=poisoned)
    assert summary["release_critical_prose_only"] == 1
    assert gate(summary) == 1


# ------------------------------------------------------------------ caller edges


def test_sabotage_deleting_the_whole_edge_list_fails():
    """The empty-edge bypass: a caller or refusal authority with NO edges at all
    is a disconnected symbol, not a joined row. Deleting the entire caller chain
    must not ship green."""
    caller = _authority_of("generated/vendored/binary/oversized detection")
    ok, _ = validate_authority(caller)
    assert ok
    del caller["edges"]
    ok, why = validate_authority(caller)
    assert not ok and "got NoneType" in why

    empty = _authority_of("read exact ranges and encodings")
    empty["edges"] = []
    ok, why = validate_authority(empty)
    assert not ok and "requires at least one caller→callee edge" in why

    refusal = _authority_of(
        "network / public writes / spending / destructive need their authority"
    )
    ok, _ = validate_authority(refusal)
    assert ok
    del refusal["edges"]
    ok, why = validate_authority(refusal)
    assert not ok and "got NoneType" in why  # a disconnected refusal is not reachable

    # malformed edges fail as typed values instead of iterating or crashing
    for malformed in ("core/blackbox/recorder.py recorded_workspace_mutation",
                      {"path": "core/x.py", "symbol": "y"}, 42, [42], [None]):
        broken = _authority_of("every mutation and command attributable")
        broken["edges"] = malformed
        ok, why = validate_authority(broken)
        assert not ok, (malformed, why)


def test_sabotage_an_unrelated_existing_function_is_not_a_caller():
    """The e63de2ee falsifier: _email_read exists and is def-verified, but never
    references identity_from_context. Existence must not pass for an edge."""
    authority = _authority_of("every mutation and command attributable")
    ok, _ = validate_authority(authority)
    assert ok
    authority["edges"] = [["core/runtime_execution_tools.py", "_email_read"]]
    ok, why = validate_authority(authority)
    assert not ok and "edge severed" in why and "_email_read" in why


def test_sabotage_the_callee_swapped_for_a_real_uncalled_symbol():
    """Sever the edge from the other end: keep the true caller, swap the callee for
    a symbol that exists in the same module but is not called there."""
    authority = _authority_of("generated/vendored/binary/oversized detection")
    authority["symbol"] = "_is_probably_text"  # real, defined — and never called by the dispatcher
    ok, why = validate_authority(authority)
    assert not ok and "edge severed" in why


def test_dynamic_dispatch_edge_needs_the_call_site_token_and_a_behavioral_test(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "forged.py").write_text(
        "def forged_effect():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "core" / "forged_dispatcher.py").write_text(
        textwrap.dedent(
            """
            HANDLERS = {}


            def dispatch_effect(name):
                handler = HANDLERS[name]
                return handler()
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_forged.py").write_text(
        "def test_forged_effect_receipt():\n    assert True\n", encoding="utf-8"
    )

    edge = {
        "path": "core/forged_dispatcher.py",
        "symbol": "dispatch_effect",
        "call_token": '"forged_effect"',
        "behavior_test": "tests/test_forged.py::test_forged_effect_receipt",
    }
    authority = {
        "kind": "caller", "path": "core/forged.py", "symbol": "forged_effect",
        "edges": [edge],
    }
    # the token is absent from the dispatcher body: the alias claim is a lie
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "does not occur inside the body" in why

    # with the real call-site token inside the caller's own body, the edge resolves
    (tmp_path / "core" / "forged_dispatcher.py").write_text(
        textwrap.dedent(
            """
            HANDLERS = {"forged_effect": forged_effect}


            def dispatch_effect(name):
                handler = HANDLERS.get(name) or HANDLERS["forged_effect"]
                return handler()
            """
        ),
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why

    # and the behavioral test must be a real, defined node
    (tmp_path / "tests" / "test_forged.py").unlink()
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "behavior_test" in why


# ------------------------------------------------------------------ served proofs


def test_sabotage_the_served_token_in_an_unrelated_test_fails():
    """A real, defined, unrelated test: its body never contains repo.push, so the
    token binds to nothing."""
    authority = _authority_of("never push by default")
    authority["node_ids"] = [
        "tests/test_git_command_gate.py::test_destructive_git_requires_approval",
    ]
    ok, why = validate_authority(authority)
    assert not ok and "repo.push" in why and "body" in why


def test_sabotage_a_dead_reference_is_not_a_call(tmp_path):
    """Counterexample 1: the real call is removed and the callee survives only as
    an assignment / unused expression. A mention does not dispatch anything."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "dispatch.py").write_text(
        textwrap.dedent(
            """
            def dispatch_tool(name):
                handler = _read_file  # dead reference: assignment, never called
                return None


            def _read_file(path):
                return path
            """
        ),
        encoding="utf-8",
    )
    authority = {
        "kind": "caller",
        "path": "core/dispatch.py",
        "symbol": "_read_file",
        "edges": [["core/dispatch.py", "dispatch_tool"]],
    }
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "never CALLS" in why and "_read_file" in why

    # restoring the actual ast.Call is the only way the edge resolves
    (tmp_path / "core" / "dispatch.py").write_text(
        textwrap.dedent(
            """
            def dispatch_tool(name):
                return _read_file(name)


            def _read_file(path):
                return path
            """
        ),
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why


def test_sabotage_call_token_outside_the_caller_body_fails(tmp_path):
    """Counterexample 2: the call-site token occurs in a SIBLING function of the
    same file. The file is not the body; the edge must fail."""
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "dispatch.py").write_text(
        textwrap.dedent(
            '''
            def forged_effect():
                return 1


            HANDLERS = {"forged_effect": forged_effect}


            def some_other_function():
                return HANDLERS["forged_effect"]  # the token lives HERE...


            def dispatch_effect(name):
                handler = HANDLERS[name]          # ...not inside the declared caller
                return handler()
            '''
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_forged.py").write_text(
        "def test_forged_effect_receipt():\n"
        "    from core.dispatch import dispatch_effect\n"
        '    assert dispatch_effect("forged_effect") == 1\n',
        encoding="utf-8",
    )
    authority = {
        "kind": "caller",
        "path": "core/dispatch.py",
        "symbol": "forged_effect",
        "edges": [{
            "path": "core/dispatch.py",
            "symbol": "dispatch_effect",
            "call_token": '"forged_effect"',
            "behavior_test": "tests/test_forged.py::test_forged_effect_receipt",
        }],
    }
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "does not occur inside the body" in why and "dispatch_effect" in why

    # moving the call-site token INSIDE the declared caller's body resolves it
    (tmp_path / "core" / "dispatch.py").write_text(
        textwrap.dedent(
            '''
            def forged_effect():
                return 1


            HANDLERS = {"forged_effect": forged_effect}


            def dispatch_effect(name):
                handler = HANDLERS.get(name) or HANDLERS["forged_effect"]
                return handler()
            '''
        ),
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why


def test_sabotage_an_unasserted_token_does_not_prove_the_refusal(tmp_path):
    """Counterexample 3: the claimed token sits in an assignment, an unused string
    and a bare comparison — but never inside an actual assertion."""
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "gate.py").write_text(
        textwrap.dedent(
            """
            def forged_caller():
                return forged_decide()


            def forged_decide():
                return REQUIRE_APPROVAL
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_weak.py").write_text(
        "def test_weak():\n"
        '    effect = "pending_approval"\n'
        "    unused = 'pending_approval'\n"
        '    probe = effect == "pending_approval"\n',
        encoding="utf-8",
    )
    authority = {
        "kind": "refusal",
        "path": "core/gate.py",
        "symbol": "forged_decide",
        "status_token": "REQUIRE_APPROVAL",
        "before_dispatch_test": "tests/test_weak.py::test_weak",
        "test_token": "pending_approval",
        "edges": [["core/gate.py", "forged_caller"]],
    }
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "never ASSERTED" in why

    # a docstring mention is equally empty
    (tmp_path / "tests" / "test_weak.py").write_text(
        'def test_weak():\n'
        '    """pending_approval is the status the gate returns."""\n'
        "    assert True\n",
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "never ASSERTED" in why

    # only a genuine assertion binds the token
    (tmp_path / "tests" / "test_weak.py").write_text(
        'def test_weak():\n'
        '    effect = {"status": "pending_approval"}\n'
        '    assert effect["status"] == "pending_approval"\n',
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why


def test_served_assert_token_must_participate_in_a_real_assertion():
    """The served authority's asserted token must live inside an assert statement
    or assertion-helper call in a named node's body."""
    authority = _authority_of("never push by default")
    ok, why = validate_authority(authority)
    assert ok, why  # default_denied IS asserted by the force-push test

    unasserted = copy.deepcopy(authority)
    unasserted["assert_token"] = "repo.real_push_enabled"  # a policy flag, never asserted here
    ok, why = validate_authority(unasserted)
    assert not ok and "participates in no real assertion" in why


def test_authority_node_ids_are_genuinely_pytest_collectable():
    """Not merely AST defs: a real `pytest --collect-only` pass must collect every
    node id the authorities name (the git-gate node parametrizes)."""
    from tools.code_assistant_matrix_join import (
        _authority_node_ids,
        collect_node_ids,
    )

    rows = _rows()
    node_ids = sorted({
        str(n)
        for capability in TYPED_AUTHORITIES
        for n in _authority_node_ids(rows[capability]["authority"])
    })
    assert len(node_ids) == 4
    collected = collect_node_ids(node_ids)
    for node_id in node_ids:
        assert node_id in collected or any(
            c.startswith(node_id + "[") for c in collected
        ), node_id
    bogus = collect_node_ids(["tests/test_code_assistant_matrix_join.py::test_not_a_test"])
    assert not any("test_not_a_test" in c for c in bogus)


def test_sabotage_a_comment_only_token_is_not_code_evidence(tmp_path):
    (tmp_path / "tests").mkdir()
    comment_only = (
        "def test_push_is_gated():\n"
        "    # repo.push is contracted here (this line is a comment, not code)\n"
        "    assert True\n"
    )
    (tmp_path / "tests" / "test_forge.py").write_text(comment_only, encoding="utf-8")
    authority = {
        "kind": "served",
        "node_ids": ["tests/test_forge.py::test_push_is_gated"],
        "token": "repo.push",
        "assert_token": "default_denied",
    }
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "comments do not count" in why

    # the same file with the token in real code AND the asserted token inside a
    # real assertion is a different fact
    (tmp_path / "tests" / "test_forge.py").write_text(
        "def test_push_is_gated():\n"
        "    status = deny_push()\n"
        '    assert status == "default_denied"\n',
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "occurs in no named node's body" in why  # repo.push still absent

    (tmp_path / "tests" / "test_forge.py").write_text(
        "def test_push_is_gated():\n"
        '    intent = "repo.push"\n'
        "    status = deny_push()\n"
        '    assert status == "default_denied"\n',
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why


def test_sabotage_a_node_id_that_is_not_a_collectable_test():
    authority = _authority_of("never push by default")
    authority["node_ids"] = ["tests/test_set_tools.py::test_set_tools_round_trip"]
    ok, why = validate_authority(authority)
    assert not ok and "not defined" in why

    authority["node_ids"] = ["tests/gauntlet/test_contract_map_golden.py"]
    ok, why = validate_authority(authority)
    assert not ok and "node id" in why


# ------------------------------------------------------------------ refusal ties


def test_sabotage_a_real_function_that_refuses_nothing():
    """The e63de2ee falsifier: grant_internal_authority is real and def-verified —
    and produces no REQUIRE_APPROVAL. It must not pass for decide_tool_call."""
    authority = _authority_of("network / public writes / spending / destructive need their authority")
    authority["symbol"] = "grant_internal_authority"
    ok, why = validate_authority(authority)
    assert not ok and "does not produce the claimed refusal" in why


def test_sabotage_the_before_dispatch_test_must_assert_the_refusal_token(tmp_path):
    authority = _authority_of("network / public writes / spending / destructive need their authority")
    ok, why = validate_authority(authority)
    assert ok, why

    # a forged tree whose named test never asserts the refusal's token
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "forged_gate.py").write_text(
        "def forged_caller():\n"
        "    return forged_decide()\n"
        "\n"
        "\n"
        "def forged_decide():\n"
        "    return REQUIRE_APPROVAL\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_weak.py").write_text(
        "def test_unrelated():\n    assert True\n", encoding="utf-8"
    )
    authority = {
        "kind": "refusal",
        "path": "core/forged_gate.py",
        "symbol": "forged_decide",
        "status_token": "REQUIRE_APPROVAL",
        "before_dispatch_test": "tests/test_weak.py::test_unrelated",
        "test_token": "pending_approval",
        "edges": [["core/forged_gate.py", "forged_caller"]],
    }
    ok, why = validate_authority(authority, root=tmp_path)
    assert not ok and "never ASSERTED" in why

    # the same tree with the token genuinely asserted is a different fact
    (tmp_path / "tests" / "test_weak.py").write_text(
        'def test_unrelated():\n    assert effect.status == "pending_approval"\n',
        encoding="utf-8",
    )
    ok, why = validate_authority(authority, root=tmp_path)
    assert ok, why


# ------------------------------------------------------------------ typed hygiene


def test_sabotage_every_half_typed_authority_is_rejected():
    valid = _authority_of("every mutation and command attributable")
    ok, why = validate_authority(valid)
    assert ok, why

    wrong_path = copy.deepcopy(valid)
    wrong_path["path"] = "core/blackbox/identity_that_is_not_there.py"
    assert validate_authority(wrong_path) == (
        False, "caller path not in tree: core/blackbox/identity_that_is_not_there.py")

    wrong_symbol = copy.deepcopy(valid)
    wrong_symbol["symbol"] = "identity_from_somewhere_else"
    assert validate_authority(wrong_symbol)[0] is False

    prose = "one Blackbox turn id per task; fault rows join on turn_key"
    assert validate_authority(prose)[0] is False
    assert validate_authority(["core/blackbox/identity.py", "identity_from_context"])[0] is False

    unknown_kind = copy.deepcopy(valid)
    unknown_kind["kind"] = "we-assure-you"
    assert validate_authority(unknown_kind)[0] is False

    stale_key = copy.deepcopy(valid)
    stale_key["callers"] = valid["edges"]  # the pre-sls_05 looser shape
    ok, why = validate_authority(stale_key)
    assert not ok and "callers" in why and "unknown key" in why


def test_obsolete_authority_needs_a_law_and_a_replacement_that_resolves():
    obsolete_prose = {"kind": "obsolete", "law": "superseded by the typed plan"}
    assert validate_authority(obsolete_prose)[0] is False

    replacement = {
        "kind": "refusal",
        "path": "core/mode_permission_policy.py",
        "symbol": "_decide_tool_call",
        "status_token": "REQUIRE_APPROVAL",
        "before_dispatch_test": "tests/test_permission_authority_is_unconditional.py::test_modeless_outward_effect_is_not_dispatched",
        "test_token": "pending_approval",
        "edges": [
            ["core/mode_permission_policy.py", "decide_tool_call"],
        ],
    }
    obsolete_valid = {
        "kind": "obsolete",
        "law": "superseded by the mode matrix's typed approval hold",
        "replacement": replacement,
    }
    ok, why = validate_authority(obsolete_valid)
    assert ok, why

    obsolete_broken = {
        "kind": "obsolete",
        "law": "superseded",
        "replacement": {"kind": "refusal", "path": "core/none.py", "symbol": "x"},
    }
    assert validate_authority(obsolete_broken)[0] is False
