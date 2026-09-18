"""Adversarial pins for kernel Law 1 (core/kernel/obligations.py).

Every test here targets a way the law could be quietly hollowed out: shipping with open
work, laundering a refusal into softer text, mutating the record after commit, or losing
the undo plan. The audited 2026-08-19 defect — a currency fast path answering 1 of 4
requests and reporting success — is pinned by name at the bottom; it must be impossible
by construction, not merely unlikely.
"""
from __future__ import annotations

import dataclasses

import pytest

try:
    from core.kernel.obligations import (
        CommitRefused,
        CommitResult,
        Obligation,
        TurnTransaction,
    )
except ImportError:  # pragma: no cover
    # Parallel PoC lanes: core/kernel/__init__.py imports all four law modules, so until
    # the sibling laws land, importing Law 1 through the package fails on THEIR absence.
    # Load it directly by path so these tests judge only the code they pin.
    import importlib.util
    from pathlib import Path

    _path = Path(__file__).resolve().parents[1] / "core" / "kernel" / "obligations.py"
    _spec = importlib.util.spec_from_file_location("_kernel_obligations_law1", _path)
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    CommitRefused = _mod.CommitRefused
    CommitResult = _mod.CommitResult
    Obligation = _mod.Obligation
    TurnTransaction = _mod.TurnTransaction


def _ob(oid: str, description: str = "a request the turn took on", kind: str = "answer") -> Obligation:
    return Obligation(id=oid, description=description, kind=kind)


# ---------------------------------------------------------------------------------
# Obligation lifecycle
# ---------------------------------------------------------------------------------


def test_a_new_obligation_is_open_with_no_evidence_and_no_reason() -> None:
    ob = _ob("q1")
    assert ob.status == "open"
    assert ob.evidence_ref is None
    assert ob.reason is None


@pytest.mark.parametrize("bad_ref", ["", "   ", "\n\t", None, 7])
def test_close_without_a_real_evidence_ref_is_refused_and_leaves_it_open(bad_ref: object) -> None:
    ob = _ob("q1")
    with pytest.raises(ValueError):
        ob.close(bad_ref)  # type: ignore[arg-type]
    assert ob.status == "open"
    assert ob.evidence_ref is None


def test_close_records_the_evidence_ref() -> None:
    ob = _ob("q1")
    ob.close("receipt://turn-9/tool-3")
    assert ob.status == "closed"
    assert ob.evidence_ref == "receipt://turn-9/tool-3"


def test_double_close_is_refused_and_the_first_evidence_survives() -> None:
    ob = _ob("q1")
    ob.close("receipt://first")
    with pytest.raises(RuntimeError):
        ob.close("receipt://second")
    assert ob.evidence_ref == "receipt://first"
    assert ob.status == "closed"


@pytest.mark.parametrize("bad_reason", ["", "  ", None])
def test_declare_unanswerable_without_a_reason_is_refused(bad_reason: object) -> None:
    ob = _ob("q1")
    with pytest.raises(ValueError):
        ob.declare_unanswerable(bad_reason)  # type: ignore[arg-type]
    assert ob.status == "open"


def test_declare_unanswerable_records_the_reason() -> None:
    ob = _ob("q1")
    ob.declare_unanswerable("no live feed for this asset")
    assert ob.status == "declared_unanswerable"
    assert ob.reason == "no live feed for this asset"


def test_close_after_declare_and_declare_after_close_are_both_refused() -> None:
    declared = _ob("q1")
    declared.declare_unanswerable("no data source")
    with pytest.raises(RuntimeError):
        declared.close("receipt://late")
    assert declared.status == "declared_unanswerable"

    closed = _ob("q2")
    closed.close("receipt://done")
    with pytest.raises(RuntimeError):
        closed.declare_unanswerable("changed my mind")
    assert closed.status == "closed"


@pytest.mark.parametrize("field_name", ["id", "description", "kind"])
def test_an_obligation_with_a_blank_identity_field_cannot_be_born(field_name: str) -> None:
    kwargs = {"id": "q1", "description": "desc", "kind": "answer", field_name: "  "}
    with pytest.raises(ValueError):
        Obligation(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------
# TurnTransaction construction and by-id transitions
# ---------------------------------------------------------------------------------


def test_duplicate_obligation_ids_are_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="q1"):
        TurnTransaction("turn-1", [_ob("q1"), _ob("q2"), _ob("q1", "same id, other ask")])


def test_a_blank_turn_id_is_refused() -> None:
    with pytest.raises(ValueError):
        TurnTransaction("   ", [_ob("q1")])


def test_closing_an_unknown_id_is_refused_by_name() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    with pytest.raises(ValueError, match="ghost"):
        txn.close("ghost", "receipt://nowhere")
    with pytest.raises(ValueError, match="ghost"):
        txn.declare_unanswerable("ghost", "never asked")
    assert [ob.id for ob in txn.open_obligations()] == ["q1"]


def test_open_obligations_shrinks_as_work_closes() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1"), _ob("q2"), _ob("q3")])
    assert {ob.id for ob in txn.open_obligations()} == {"q1", "q2", "q3"}
    txn.close("q2", "receipt://q2")
    txn.declare_unanswerable("q3", "out of scope")
    assert [ob.id for ob in txn.open_obligations()] == ["q1"]


# ---------------------------------------------------------------------------------
# commit(): the only door marked "done"
# ---------------------------------------------------------------------------------


def test_commit_with_open_obligations_raises_naming_exactly_the_open_ids() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1"), _ob("q2"), _ob("q3")])
    txn.close("q1", "receipt://q1")
    with pytest.raises(CommitRefused) as exc:
        txn.commit()
    assert exc.value.open_ids == ("q2", "q3")
    message = str(exc.value)
    assert "q2" in message and "q3" in message
    assert "q1" not in message  # a closed obligation must not be named as open


def test_commit_refused_is_a_runtime_error_carrying_open_ids() -> None:
    err = CommitRefused(("a", "b"))
    assert isinstance(err, RuntimeError)
    assert err.open_ids == ("a", "b")


def test_a_refused_commit_leaves_the_transaction_live_for_repair() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1"), _ob("q2")])
    with pytest.raises(CommitRefused):
        txn.commit()
    txn.close("q1", "receipt://q1")
    txn.declare_unanswerable("q2", "no source")
    result = txn.commit()
    # LAW EVOLUTION 2026-08-20: declared items make the outcome "settled", not "committed" —
    # a live turn whose every obligation was declared printed COMMIT: committed and read as
    # success ("commit by giving up"). Committed is reserved for all-closed.
    assert result.status == "settled"


def test_an_empty_obligation_turn_commits_trivially() -> None:
    result = TurnTransaction("turn-1", []).commit()
    assert result.status == "committed"
    assert result.open == ()
    assert result.declared == ()
    assert "\n" not in result.manifest()


def test_commit_with_declared_items_is_settled_and_the_manifest_names_them() -> None:
    """LAW EVOLUTION 2026-08-20: 'settled' is the third outcome — nothing open, but not
    everything answered. Measured live: an all-declared turn printed 'committed'."""
    txn = TurnTransaction("turn-1", [_ob("q1"), _ob("q2")])
    txn.close("q1", "receipt://q1")
    txn.declare_unanswerable("q2", "no live feed")
    result = txn.commit()
    assert result.status == "settled"
    assert result.declared == (("q2", "no live feed"),)
    manifest = result.manifest()
    assert "q2" in manifest and "no live feed" in manifest
    assert "\n" not in manifest


def test_post_commit_mutation_of_transaction_or_obligations_raises() -> None:
    ob1, ob2 = _ob("q1"), _ob("q2")
    txn = TurnTransaction("turn-1", [ob1, ob2])
    txn.close("q1", "receipt://q1")
    txn.declare_unanswerable("q2", "no source")
    txn.commit()
    with pytest.raises(RuntimeError):
        txn.commit()
    with pytest.raises(RuntimeError):
        txn.commit_partial()
    with pytest.raises(RuntimeError):
        txn.journal_effect("tool", "late effect", "undo it")
    with pytest.raises(RuntimeError):
        txn.close("q1", "receipt://again")
    with pytest.raises(RuntimeError):
        txn.open_obligations()
    with pytest.raises(RuntimeError):
        txn.abort()
    # The obligations themselves are sealed — the record cannot drift after commit.
    fresh_looking = _ob("q3")
    txn2 = TurnTransaction("turn-2", [fresh_looking])
    txn2.commit_partial()
    with pytest.raises(RuntimeError):
        fresh_looking.close("receipt://after-the-fact")
    with pytest.raises(RuntimeError):
        fresh_looking.declare_unanswerable("after-the-fact excuse")


# ---------------------------------------------------------------------------------
# commit_partial(): the only door marked "shipped incomplete"
# ---------------------------------------------------------------------------------


def test_commit_partial_names_open_items_by_id_and_description() -> None:
    txn = TurnTransaction(
        "turn-1",
        [
            _ob("q1", "convert 100 RUB to EUR"),
            _ob("q2", "convert 50 USD to GBP"),
        ],
    )
    txn.close("q1", "receipt://q1")
    result = txn.commit_partial()
    assert result.status == "partial"
    assert result.open == ("q2",)
    manifest = result.manifest()
    assert manifest.startswith("partial")
    assert "q2" in manifest and "convert 50 USD to GBP" in manifest
    assert "\n" not in manifest


def test_commit_partial_with_nothing_open_is_refused_and_the_turn_stays_live() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    txn.close("q1", "receipt://q1")
    with pytest.raises(RuntimeError):
        txn.commit_partial()
    assert txn.commit().status == "committed"  # refusal did not finalize


def test_a_second_commit_after_commit_partial_raises() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    txn.commit_partial()
    with pytest.raises(RuntimeError):
        txn.commit()
    with pytest.raises(RuntimeError):
        txn.commit_partial()


# ---------------------------------------------------------------------------------
# journal + abort: the undo plan
# ---------------------------------------------------------------------------------


def test_abort_returns_compensations_in_reverse_order_as_the_undo_plan() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    txn.journal_effect("fs.write", "wrote draft.txt", "delete draft.txt")
    txn.journal_effect("fs.move", "moved a.txt to b.txt", "move b.txt back to a.txt")
    txn.journal_effect("proc.start", "started server", "stop server")
    undo = txn.abort()
    assert [entry.compensation for entry in undo] == [
        "stop server",
        "move b.txt back to a.txt",
        "delete draft.txt",
    ]
    assert undo[0].tool == "proc.start"
    assert undo[0].description == "started server"


def test_abort_with_an_empty_journal_returns_an_empty_undo_plan() -> None:
    assert TurnTransaction("turn-1", [_ob("q1")]).abort() == ()


@pytest.mark.parametrize("blank_slot", ["tool", "description", "compensation"])
def test_journal_effect_with_a_blank_field_is_refused_and_nothing_is_journaled(blank_slot: str) -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    kwargs = {"tool": "fs.write", "description": "wrote x", "compensation": "delete x", blank_slot: " "}
    with pytest.raises(ValueError):
        txn.journal_effect(**kwargs)
    assert txn.abort() == ()  # the refused entry must not linger as a phantom undo step


def test_every_method_after_abort_raises() -> None:
    txn = TurnTransaction("turn-1", [_ob("q1")])
    txn.abort()
    with pytest.raises(RuntimeError):
        txn.commit()
    with pytest.raises(RuntimeError):
        txn.commit_partial()
    with pytest.raises(RuntimeError):
        txn.abort()
    with pytest.raises(RuntimeError):
        txn.journal_effect("t", "d", "c")
    with pytest.raises(RuntimeError):
        txn.open_obligations()
    with pytest.raises(RuntimeError):
        txn.close("q1", "receipt://too-late")


# ---------------------------------------------------------------------------------
# CommitResult: the record itself resists tampering and mislabeling
# ---------------------------------------------------------------------------------


def test_commit_result_is_frozen() -> None:
    result = CommitResult(status="committed", open=(), declared=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "partial"  # type: ignore[misc]


def test_commit_result_refuses_an_unknown_status() -> None:
    with pytest.raises(ValueError):
        CommitResult(status="done", open=(), declared=())


def test_commit_result_refuses_the_committed_label_over_open_items() -> None:
    with pytest.raises(ValueError):
        CommitResult(status="committed", open=("q1",), declared=())


def test_commit_result_refuses_misaligned_open_descriptions() -> None:
    with pytest.raises(ValueError):
        CommitResult(
            status="partial",
            open=("q1", "q2"),
            declared=(),
            open_descriptions=("only one",),
        )


# ---------------------------------------------------------------------------------
# THE AUDITED SCENARIO (Fable5 audit, 2026-08-19): a currency fast path answered
# 1 of 4 conversion requests and reported the turn as a success. Under Law 1 that
# report is structurally impossible: commit() refuses, naming the three dropped
# requests, and the only shippable result is labelled partial and names them too.
# ---------------------------------------------------------------------------------


def test_AUDITED_SCENARIO_one_of_four_currency_answers_cannot_report_success() -> None:
    txn = TurnTransaction(
        "turn-currency",
        [
            _ob("rub_eur", "convert 100 RUB to EUR", kind="lookup"),
            _ob("usd_gbp", "convert 50 USD to GBP", kind="lookup"),
            _ob("gold", "price 1 oz of gold in USD", kind="lookup"),
            _ob("btc", "price 1 BTC in USD", kind="lookup"),
        ],
    )
    txn.close("rub_eur", "receipt://rates/rub-eur")

    with pytest.raises(CommitRefused) as exc:
        txn.commit()
    assert exc.value.open_ids == ("usd_gbp", "gold", "btc")
    refusal = str(exc.value)
    assert "usd_gbp" in refusal and "gold" in refusal and "btc" in refusal
    assert "rub_eur" not in refusal

    # The only legal ship is labelled partial and names every dropped request.
    result = txn.commit_partial()
    assert result.status == "partial"
    assert result.open == ("usd_gbp", "gold", "btc")
    manifest = result.manifest()
    assert manifest.startswith("partial")
    for oid, words in [
        ("usd_gbp", "convert 50 USD to GBP"),
        ("gold", "price 1 oz of gold in USD"),
        ("btc", "price 1 BTC in USD"),
    ]:
        assert oid in manifest and words in manifest
    assert "committed" not in manifest
