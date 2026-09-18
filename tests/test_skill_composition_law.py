"""Composition without eviction, and provenance that cannot overstate influence.

Two measured defects motivated this file.

SILENT TRUNCATION. The packer handed each selected skill whatever budget the
previous one left and cut its body mid-word. On a real debugging turn
``root-cause-repair`` reached the provider as **54 of 1760 characters** -- its
workflow, its tool-door rule and the symptom-only stopping condition all gone --
while the durable ledger recorded ``root-cause-repair v1.0.0`` as having
influenced the turn. ``REQUIRED_BODY_TOKENS`` is enforced at LOAD against the
full body, and nothing re-checked what actually reached the wire, so a contract
could pass its doctrine law and still ship without the doctrine.

ONE POOL FOR FOUR KINDS. Every SKILL.md declares ``type:`` and the runtime never
read it, so a whole-answer doctrine and a task workflow competed for the same two
slots. ``answer-presentation`` claims four broad advisory families; it and a lens
took both seats and pushed out the skill the turn was actually about.

The laws here are the fix stated as assertions: complete or absent, lanes do not
evict each other, and no provenance row claims a skill the wire did not carry.
"""
from __future__ import annotations

import pytest

from core.native_skill_library import (
    LANE_DOCTRINE,
    LANE_TASK,
    guidance_for_selection,
    lane_for,
    load_skill_contracts,
    select_native_skills,
)
from core.tool_offer_assembly import (
    MAX_DOCTRINE_CHARS,
    MAX_SKILL_CHARS,
    MAX_TOTAL_SKILL_CHARS,
)

DEBUGGING_TURN = (
    "A unit test in my project fails after my change. Diagnose the root cause "
    "before proposing any fix. Use the project archaeology skill for the file history."
)
EDITORIAL_TURN = "Write an X article from these notes"


def _rows(task_class: str, user_text: str) -> tuple[str, dict[str, dict]]:
    selection = select_native_skills(task_class=task_class, user_text=user_text)
    text, provenance, _ = guidance_for_selection(selection.selected)
    return text, {row["name"]: row for row in provenance}


# ── complete or absent ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("task_class", "user_text"),
    [("debugging", DEBUGGING_TURN), ("creative_ideation", EDITORIAL_TURN)],
)
def test_every_bound_skill_body_is_whole(task_class: str, user_text: str) -> None:
    """What reaches the wire is the SKILL.md body byte-for-byte, or nothing."""
    library = {c.id: c for c in load_skill_contracts().contracts}
    text, rows = _rows(task_class, user_text)
    assert rows, f"{task_class} selected nothing"
    for name, row in rows.items():
        if not row["complete"]:
            continue
        body = str(library[name].body or "").strip()
        assert body and body in text, (
            f"{name} is recorded complete but its full body is not in the bound text"
        )
    # And nothing was ellipsised into the prompt.
    assert "…" not in text or all(
        "…" in str(library[n].body or "") for n, r in rows.items() if r["complete"]
    ), "a truncation ellipsis reached the bound guidance"


def test_a_body_that_does_not_fit_is_dropped_and_recorded_never_cut() -> None:
    """The whole point: absence is legible, truncation is not."""
    selection = select_native_skills(task_class="debugging", user_text=DEBUGGING_TURN)
    assert len(selection.selected) >= 2, [c.id for c in selection.selected]

    # A budget that fits the first body and not the second.
    first = selection.selected[0]
    tight = len(str(first.body or "").strip()) + 200
    text, provenance, _ = guidance_for_selection(selection.selected, task_chars=tight)
    rows = {row["name"]: row for row in provenance}

    dropped = [r for r in rows.values() if not r["complete"]]
    assert dropped, f"nothing was dropped at a deliberately tight budget: {rows}"
    for row in dropped:
        assert row["dropped"] == "over_lane_char_budget", row
        assert row["chars"] == 0, row
        assert row["body_chars"] > 0, row
    # Whatever DID render is still whole.
    for row in rows.values():
        if row["complete"]:
            assert row["chars"] >= row["body_chars"], row
    assert "…" not in text, "a body was cut instead of dropped"


# ── lanes do not evict each other ────────────────────────────────────────────


def test_the_presentation_doctrine_no_longer_evicts_the_task_skill() -> None:
    """The measured CP13 failure, asserted directly.

    answer-presentation + a lens used to take both slots on a creative_ideation
    turn and push out x-editorial-studio, which has no task family and can score
    at most 2. They now hold different lanes, so all three arrive.
    """
    _, rows = _rows("creative_ideation", EDITORIAL_TURN)
    assert {"answer-presentation", "x-editorial-studio"} <= set(rows), sorted(rows)
    assert rows["answer-presentation"]["lane"] == LANE_DOCTRINE
    assert rows["x-editorial-studio"]["lane"] == LANE_TASK
    assert all(rows[n]["complete"] for n in ("answer-presentation", "x-editorial-studio"))


def test_the_recon_skill_no_longer_evicts_the_repair_doctrine() -> None:
    """The measured CP6 failure: archaeology and the repair skills compose."""
    _, rows = _rows("debugging", DEBUGGING_TURN)
    assert {"project-archaeology", "root-cause-repair", "bug-reproduction"} <= set(rows), sorted(rows)
    assert all(
        rows[n]["complete"]
        for n in ("project-archaeology", "root-cause-repair", "bug-reproduction")
    ), rows


def test_an_irrelevant_skill_is_still_excluded() -> None:
    """Composition is not permission to include everything."""
    _, rows = _rows("debugging", DEBUGGING_TURN)
    for absent in ("release-gate", "x-editorial-studio", "media-studio", "browser"):
        assert absent not in rows, f"{absent} entered a debugging turn: {sorted(rows)}"


# ── bounds ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("task_class", "user_text"),
    [
        ("debugging", DEBUGGING_TURN),
        ("creative_ideation", EDITORIAL_TURN),
        ("business_advisory", "how should I price this"),
        ("research", "find me sources on this"),
        ("security_hardening", "review this for security problems"),
    ],
)
def test_each_lane_stays_inside_its_own_budget(task_class: str, user_text: str) -> None:
    text, rows = _rows(task_class, user_text)
    assert len(text) <= MAX_TOTAL_SKILL_CHARS, len(text)
    per_lane = {LANE_TASK: 0, LANE_DOCTRINE: 0}
    for row in rows.values():
        per_lane[row["lane"]] += int(row["chars"])
    assert per_lane[LANE_TASK] <= MAX_SKILL_CHARS, per_lane
    assert per_lane[LANE_DOCTRINE] <= MAX_DOCTRINE_CHARS, per_lane


def test_selection_is_deterministic_across_repeated_calls() -> None:
    """Same typed inputs, same answer -- including the order it renders in."""
    first = _rows("debugging", DEBUGGING_TURN)
    for _ in range(4):
        assert _rows("debugging", DEBUGGING_TURN) == first


def test_every_contract_lands_in_a_known_lane() -> None:
    for contract in load_skill_contracts().contracts:
        assert lane_for(contract) in (LANE_TASK, LANE_DOCTRINE), contract.id


# ── provenance cannot overstate influence ────────────────────────────────────


def test_provenance_never_claims_a_skill_the_wire_did_not_carry() -> None:
    """A dropped skill and an injected one must not record identically.

    This is the ledger half of the truncation defect: the row said
    "root-cause-repair v1.0.0 influenced this turn" while 54 characters of it
    reached the provider.
    """
    from core.tool_offer_assembly import skill_provenance_rows

    selection = select_native_skills(task_class="debugging", user_text=DEBUGGING_TURN)
    first = selection.selected[0]
    tight = len(str(first.body or "").strip()) + 200
    text, provenance, _ = guidance_for_selection(selection.selected, task_chars=tight)

    durable = {row["name"]: row for row in skill_provenance_rows(provenance)}
    assert durable, provenance
    for name, row in durable.items():
        assert "complete" in row, row
        if row["complete"]:
            continue
        # The durable record must say it did not arrive, and the text must agree.
        assert row["chars"] == 0, row
        body = next(c.body for c in selection.selected if c.id == name).strip()
        assert body not in text, f"{name} recorded as dropped but its body is in the text"


def test_the_durable_row_carries_version_and_lane_for_every_source() -> None:
    from core.tool_offer_assembly import skill_provenance_rows

    _, rows = _rows("creative_ideation", EDITORIAL_TURN)
    for row in skill_provenance_rows(list(rows.values())):
        assert row["name"] and row["version"], row
        assert row["lane"] in (LANE_TASK, LANE_DOCTRINE), row
        assert row["kind"], row


# ── the guards are load-bearing ──────────────────────────────────────────────


def test_sabotage_restoring_truncation_reintroduces_the_partial_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Put the cutting renderer back and a partial body ships again.

    Names the exact seam -- the complete-or-none renderer -- rather than
    disabling packing wholesale, so the sabotage cannot be satisfied by some
    other refusal.
    """
    from core import tool_offer_assembly

    def _truncating(skill, budget):
        body = str(getattr(skill, "body", "") or "").strip()
        if not body:
            return None
        if len(body) > int(budget):
            body = body[: max(0, int(budget) - 1)].rstrip() + "…"
        return f"[skill '{skill.name}']\n{body}"

    monkeypatch.setattr(tool_offer_assembly, "render_complete_block", _truncating)

    selection = select_native_skills(task_class="debugging", user_text=DEBUGGING_TURN)
    first = selection.selected[0]
    tight = len(str(first.body or "").strip()) + 200
    text, provenance, _ = guidance_for_selection(selection.selected, task_chars=tight)

    assert "…" in text, (
        "sabotage no-op: the truncating renderer produced no cut body, so the "
        "complete-or-absent law is not what prevents one"
    )
    assert all(row["complete"] for row in provenance), (
        "with truncation restored every row claims completeness again -- exactly the "
        "ledger overstatement the law removes"
    )


def test_sabotage_collapsing_the_lanes_restores_the_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the PRIOR configuration exactly and the eviction comes back.

    The defect had two halves and the sabotage names both: one undifferentiated
    pool, and the two slots that pool held. Collapsing the lanes alone does not
    reproduce it at the current slot count -- on this turn there are exactly three
    candidates and three task seats, so they all still fit. That is worth stating
    rather than hiding behind a sabotage tuned to bite: the lane split is what
    stops doctrine and task skills competing, and the slot count is what decides
    whether the competition has a casualty.
    """
    from core import native_skill_library as nsl

    monkeypatch.setattr(nsl, "_LANE_BY_KIND", dict.fromkeys(nsl.VALID_KINDS, nsl.LANE_TASK))

    # Half one alone: still fine, because three seats hold three candidates.
    still_fits = [
        c.id
        for c in nsl.select_native_skills(
            task_class="creative_ideation", user_text=EDITORIAL_TURN
        ).selected
    ]
    assert "x-editorial-studio" in still_fits, still_fits

    # Both halves -- one pool of two seats, the configuration that shipped:
    evicted = [
        c.id
        for c in nsl.select_native_skills(
            task_class="creative_ideation", user_text=EDITORIAL_TURN, limit=2, doctrine_limit=2
        ).selected
    ]
    assert evicted == ["answer-presentation", "lens-ux"], evicted
    assert "x-editorial-studio" not in evicted, (
        "sabotage no-op: one pool of two seats did not evict the task skill, so the "
        f"composition fix is not what prevents it (chosen={evicted})"
    )
