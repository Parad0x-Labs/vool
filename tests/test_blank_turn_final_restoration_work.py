from __future__ import annotations

import json
from unittest import mock

import pytest

import core.agent_runtime.agent as agent_module
from core import media_ingestion, runtime_continuity
from core.agent_runtime.checkpoints import prepare_runtime_checkpoint
from core.agent_runtime.request_authority import (
    REQUEST_PROVENANCE_KEY,
    TURN_EVIDENCE_ITEMS_MAX,
    request_provenance_for_visible_user_text,
)
from core.media_ingestion import ingest_media_evidence
from core.runtime_continuity import (
    create_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_failed_checkpoint,
    latest_resumable_checkpoint,
    resume_runtime_checkpoint,
    runtime_checkpoint_restoration_scope,
    update_runtime_checkpoint,
)


class _ResumeAgent:
    def _looks_like_explicit_resume_request(self, text: str) -> bool:
        return str(text or "").strip().lower() in {"continue", "resume", "try again"}

    def _is_proceed_message(self, text: str) -> bool:
        return str(text or "").strip().lower() in {"continue", "resume"}

    def _resume_request_key(self, text: str) -> str:
        return " ".join(str(text or "").lower().split())


def _poisoned_checkpoint(
    *,
    session_id: str,
    item_count: int,
    shape: str,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
    request_text = "summarise the supplied evidence"
    provenance = request_provenance_for_visible_user_text(
        request_text,
        session_id=session_id,
    )
    checkpoint = create_runtime_checkpoint(
        session_id=session_id,
        request_text=request_text,
        source_context={REQUEST_PROVENANCE_KEY: provenance},
    )
    source_items = [
        {"kind": "text", "origin": "restored", "index": index}
        for index in range(item_count)
    ]
    state_items = (
        source_items
        if shape == "same_both"
        else [
            {"kind": "text", "origin": "state", "index": index}
            for index in range(item_count)
        ]
    )
    source_context: dict[str, object] = {
        "marker": "source",
        REQUEST_PROVENANCE_KEY: provenance,
    }
    loop_source_context: dict[str, object] = {"marker": "state-loop"}
    if shape in {"source_only", "same_both", "different_both"}:
        source_context["external_evidence"] = source_items
    if shape in {"state_only", "same_both", "different_both"}:
        loop_source_context["external_evidence"] = state_items
    conn = runtime_continuity._conn()
    try:
        conn.execute(
            """
            UPDATE runtime_checkpoints
            SET status = 'interrupted', source_context_json = ?, state_json = ?
            WHERE checkpoint_id = ?
            """,
            (
                json.dumps(source_context, sort_keys=True),
                json.dumps(
                    {
                        "marker": "state",
                        "loop_source_context": loop_source_context,
                    },
                    sort_keys=True,
                ),
                checkpoint["checkpoint_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return str(checkpoint["checkpoint_id"]), source_items, state_items


def _resume_and_measure(
    *,
    session_id: str,
    checkpoint_id: str,
    fresh_evidence: list[dict[str, object]] | None = None,
) -> tuple[dict, list[object], object]:
    decoded: list[object] = []
    real_decode = runtime_continuity._decode_checkpoint_evidence_row

    def decode_spy(value, value_type):
        decoded.append(value)
        return real_decode(value, value_type)

    with runtime_checkpoint_restoration_scope() as scope, mock.patch.object(
        runtime_continuity,
        "_decode_checkpoint_evidence_row",
        side_effect=decode_spy,
    ):
        resumed = prepare_runtime_checkpoint(
            _ResumeAgent(),
            session_id=session_id,
            raw_user_input="continue",
            effective_input="continue",
            source_context={"external_evidence": list(fresh_evidence or [])},
            latest_resumable_checkpoint_fn=latest_resumable_checkpoint,
            resume_runtime_checkpoint_fn=resume_runtime_checkpoint,
            create_runtime_checkpoint_fn=create_runtime_checkpoint,
            latest_failed_checkpoint_fn=latest_failed_checkpoint,
        )
        # These are real later consumers in the production call graph. They must reuse the view
        # restored by the resume decision instead of earning another evidence allowance.
        assert get_runtime_checkpoint(checkpoint_id) is not None
        assert update_runtime_checkpoint(checkpoint_id, task_class="restoration-attack") is not None
        restored = get_runtime_checkpoint(checkpoint_id)

    assert restored is not None
    return resumed, decoded, scope


@pytest.mark.parametrize("item_count", [100, 1_000, 50_000])
@pytest.mark.parametrize(
    "shape",
    ["source_only", "state_only", "same_both", "different_both"],
)
def test_one_resume_turn_has_one_total_checkpoint_evidence_work_budget(
    item_count: int,
    shape: str,
) -> None:
    session_id = f"whole-resume-{shape}-{item_count}"
    checkpoint_id, source_items, state_items = _poisoned_checkpoint(
        session_id=session_id,
        item_count=item_count,
        shape=shape,
    )

    resumed, decoded, scope = _resume_and_measure(
        session_id=session_id,
        checkpoint_id=checkpoint_id,
    )

    assert resumed["state"] == "resumed"
    assert len(decoded) == TURN_EVIDENCE_ITEMS_MAX
    assert scope.evidence_work == TURN_EVIDENCE_ITEMS_MAX
    assert scope.durable_reads == 1
    checkpoint = resumed["checkpoint"]
    restored_source = checkpoint["source_context"]["external_evidence"]
    restored_loop = checkpoint["state"]["loop_source_context"]["external_evidence"]
    expected = source_items if shape != "state_only" else state_items
    assert restored_source == expected[:TURN_EVIDENCE_ITEMS_MAX]
    assert restored_loop == restored_source


def test_conflicting_stored_representations_use_source_context_authority_without_union() -> None:
    checkpoint_id, source_items, _state_items = _poisoned_checkpoint(
        session_id="conflicting-restoration-authority",
        item_count=40,
        shape="different_both",
    )

    resumed, decoded, scope = _resume_and_measure(
        session_id="conflicting-restoration-authority",
        checkpoint_id=checkpoint_id,
    )

    restored = resumed["checkpoint"]["source_context"]["external_evidence"]
    loop_restored = resumed["checkpoint"]["state"]["loop_source_context"]["external_evidence"]
    assert restored == source_items
    assert loop_restored == source_items
    assert len(decoded) == 40
    assert scope.evidence_work == 40
    assert scope.durable_reads == 1


@pytest.mark.parametrize(
    "restored_count,fresh_count,url_count,expected_restored,expected_fresh,expected_urls",
    [
        (40, 40, 50, 40, 24, 0),
        (64, 40, 10, 64, 0, 0),
        (30, 20, 50, 30, 20, 14),
    ],
)
def test_whole_resume_work_bound_preserves_shared_exposed_evidence_precedence(
    restored_count: int,
    fresh_count: int,
    url_count: int,
    expected_restored: int,
    expected_fresh: int,
    expected_urls: int,
) -> None:
    session_id = f"whole-precedence-{restored_count}-{fresh_count}-{url_count}"
    checkpoint_id, _source_items, _state_items = _poisoned_checkpoint(
        session_id=session_id,
        item_count=restored_count,
        shape="same_both",
    )
    fresh = [
        {"kind": "text", "origin": "fresh", "index": index}
        for index in range(fresh_count)
    ]

    resumed, decoded, scope = _resume_and_measure(
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        fresh_evidence=fresh,
    )
    normalized: list[object] = []
    real_normalize = media_ingestion._normalize_item

    def normalize_spy(item, **kwargs):
        normalized.append(item)
        return real_normalize(item, **kwargs)

    urls = " ".join(
        f"https://example.invalid/context-{index}" for index in range(url_count)
    )
    with mock.patch.object(
        media_ingestion,
        "_normalize_item",
        side_effect=normalize_spy,
    ), mock.patch.object(media_ingestion, "record_media_evidence", return_value="entry"):
        ingest_media_evidence(
            task_id="task",
            trace_id="trace",
            user_input=urls,
            source_context=resumed["source_context"],
        )

    assert len(decoded) == restored_count
    assert scope.evidence_work == restored_count
    assert scope.durable_reads == 1
    assert [item.get("origin") for item in normalized[:expected_restored]] == [
        "restored"
    ] * expected_restored
    assert [
        item.get("origin")
        for item in normalized[
            expected_restored : expected_restored + expected_fresh
        ]
    ] == ["fresh"] * expected_fresh
    assert len(normalized) == expected_restored + expected_fresh + expected_urls


def test_agent_run_once_owns_the_restoration_scope_for_all_checkpoint_consumers(
    make_agent,
) -> None:
    checkpoint_id, _source_items, _state_items = _poisoned_checkpoint(
        session_id="agent-owned-restoration-scope",
        item_count=1_000,
        shape="same_both",
    )
    decoded: list[object] = []
    real_decode = runtime_continuity._decode_checkpoint_evidence_row

    def inner_spy(*_args, **_kwargs):
        for _ in range(4):
            assert get_runtime_checkpoint(checkpoint_id) is not None
        return {"ok": True}

    agent = make_agent()
    with mock.patch.object(agent, "_run_once_inner", side_effect=inner_spy), mock.patch.object(
        agent_module,
        "sweep_stale_checkpoints_if_due",
        return_value=0,
    ), mock.patch.object(
        runtime_continuity,
        "_decode_checkpoint_evidence_row",
        side_effect=lambda value, value_type: (
            decoded.append(value),
            real_decode(value, value_type),
        )[1],
    ):
        result = agent.run_once(
            "continue",
            session_id_override="agent-owned-restoration-scope",
            source_context={},
        )

    assert result == {"ok": True}
    assert len(decoded) == TURN_EVIDENCE_ITEMS_MAX


def test_turn_scope_does_not_refund_evidence_work_for_a_second_checkpoint_read() -> None:
    first_id, _source_items, _state_items = _poisoned_checkpoint(
        session_id="scope-budget-first",
        item_count=1_000,
        shape="same_both",
    )
    second_id, _source_items, _state_items = _poisoned_checkpoint(
        session_id="scope-budget-second",
        item_count=1_000,
        shape="same_both",
    )
    decoded: list[object] = []
    real_decode = runtime_continuity._decode_checkpoint_evidence_row

    with runtime_checkpoint_restoration_scope() as scope, mock.patch.object(
        runtime_continuity,
        "_decode_checkpoint_evidence_row",
        side_effect=lambda value, value_type: (
            decoded.append(value),
            real_decode(value, value_type),
        )[1],
    ):
        assert get_runtime_checkpoint(first_id) is not None
        assert get_runtime_checkpoint(second_id) is not None

    assert len(decoded) == TURN_EVIDENCE_ITEMS_MAX
    assert scope.evidence_work == TURN_EVIDENCE_ITEMS_MAX
    assert scope.durable_reads == 2
