from __future__ import annotations

import hashlib

import pytest

from tests.live.runtime_model_gauntlet import (
    FIXTURE_CASE_COUNTS,
    FIXTURES,
    load_cases,
    parser,
)

# The attachments did not end with a newline and contained three invisible trailing spaces.
# Fixtures use canonical text-file form: strip line-end spaces and add one terminal newline.
# These hashes freeze that reviewable representation without making ``git diff --check`` red.
FIXTURE_SHA256 = {
    5: "6aa44aaea1cebdfbb3a9e64bad1c0344f9741641fd600607b17f08ae784bac3a",
    6: "a8c6bbfb04f29b77be36abc76204245a906727d28d0b638dfb338e4a8f96db5d",
    7: "e2ba17eed3d1dc439a3cfe72a9145a824e38670f4a02c27726b494aabb2295e9",
}
FIXTURE_BYTE_COUNTS = {5: 6124, 6: 5806, 7: 6002}


def _parse_args(*sets: int):
    return parser().parse_args(
        [
            "--sets",
            *(str(number) for number in sets),
            "--model",
            "vool-local-only",
            "--lane",
            "fixture-test",
            "--report",
            "/tmp/runtime-model-gauntlet-fixture-test.json",
        ]
    )


@pytest.mark.parametrize("set_number", (5, 6, 7))
def test_additive_fixture_matches_its_canonical_frozen_hash(set_number: int) -> None:
    committed = FIXTURES[set_number].read_text(encoding="utf-8")

    assert committed.endswith("\n")
    fixture_text = committed.removesuffix("\n")
    assert all(line == line.rstrip() for line in fixture_text.splitlines())
    fixture_bytes = fixture_text.encode("utf-8")
    assert len(fixture_bytes) == FIXTURE_BYTE_COUNTS[set_number]
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        FIXTURE_SHA256[set_number]
    )


def test_additive_sets_have_thirty_distinct_prompts_and_monotonic_ids() -> None:
    cases = load_cases((5, 6, 7))

    assert len(cases) == 90
    assert len({case.case_id for case in cases}) == 90
    assert len({case.prompt for case in cases}) == 90
    for set_number in (5, 6, 7):
        set_cases = [case for case in cases if case.set_number == set_number]
        assert [case.prompt_number for case in set_cases] == list(range(1, 31))
        assert [case.case_id for case in set_cases] == [
            f"set{set_number}-{prompt_number:02d}" for prompt_number in range(1, 31)
        ]


def test_original_set_ids_and_counts_remain_backward_compatible() -> None:
    assert {number: FIXTURE_CASE_COUNTS[number] for number in (1, 2, 3, 4)} == {
        1: 30,
        2: 30,
        3: 30,
        4: 15,
    }
    original = load_cases((1, 2, 3, 4))
    assert len(original) == 105
    assert original[0].case_id == "set1-01"
    assert original[-1].case_id == "set4-15"
    assert len({case.case_id for case in original}) == 105


def test_cli_accepts_original_and_additive_set_identifiers() -> None:
    old = _parse_args(1, 2, 3, 4)
    additive = _parse_args(5, 6, 7)
    cumulative = _parse_args(1, 2, 3, 4, 5, 6, 7)

    assert old.sets == ["1", "2", "3", "4"]
    assert additive.sets == ["5", "6", "7"]
    assert cumulative.sets == [str(number) for number in range(1, 8)]


def test_cli_still_rejects_unknown_set_identifiers() -> None:
    with pytest.raises(SystemExit):
        _parse_args(8)
