"""Independent verification of the pairing-detection fix in `_challenge_finding`
(core/agent_runtime/stepped_audit.py). Written from scratch against the LIVE code, with fixtures
that do not appear anywhere in `tests/test_challenge_step_traces_both_sides_of_a_pair.py`: a
frame-based packer/unpacker (`pack`/`unpack`, not `compress`/`decompress`) for the round-trip case,
and a running-checksum reader/writer (`write_frame`/`read_frame`, not `compress`/`decompress`) for
the shared-state-variable case.

This does not execute anything from the audited project. It drives the real `_challenge_finding`
with the LLM call stubbed out (same technique the codebase already uses in
`tests/test_an_audit_obeys_the_permission_it_was_given.py` and in the sibling test file above: a
fake `agent.memory_router` that records the `ModelRequest` and returns a canned JSON verdict), and
inspects the constructed prompt text directly.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import (
    SteppedAuditBudget,
    SteppedFinding,
    _challenge_finding,
)

_MANIFEST = SimpleNamespace(provider_id="verify-provider", model_name="verify-model")
_LABEL = "the paired operation, for comparison"


class _StubRouter:
    """Records every request handed to it, replies with one fixed 'uncertain' verdict."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        return (
            None,
            SimpleNamespace(
                output_text=json.dumps({"verdict": "uncertain", "reason": "n/a", "counterexample": ""}),
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-verify",
                response_id="resp-verify",
            ),
            None,
        )


def _drive(evidence: AuditEvidence, finding: SteppedFinding) -> str:
    router = _StubRouter()
    agent = SimpleNamespace(memory_router=router)
    _challenge_finding(
        agent,
        manifest=_MANIFEST,
        task=SimpleNamespace(task_id="verify-task"),
        source_context={},
        evidence=evidence,
        finding=finding,
        budget=SteppedAuditBudget(),
        usages=[],
    )
    assert len(router.requests) == 1, (
        f"expected exactly one model call, got {len(router.requests)} -- an executed/deterministic "
        "shortcut fired instead of the model-challenge path this test targets"
    )
    return router.requests[0].prompt


# ---------------------------------------------------------------------------------------
# Fixture A -- frame packer/unpacker, NOT compress/decompress. Deliberately plantable
# empty-input round-trip defect: pack(b"") returns a header-only frame, unpack of that frame
# returns None instead of b"", so the round trip of empty input silently changes type.
# ---------------------------------------------------------------------------------------

_PACKER_SOURCE = (
    "class FramePacker:\n"
    "    def pack(self, payload):\n"
    "        if not payload:\n"
    '            return b"HDR0"\n'
    '        return b"HDR1" + payload\n'
    "\n"
    # Padding methods so the +/-8 line window around the cited `unpack` lines cannot reach
    # `pack`'s body by coincidence -- the same spacing technique used to make this a real test
    # of the paired-operation path rather than an accidental window overlap.
    + "".join(f"    def scratch_{i}(self):\n        return {i}\n" for i in range(1, 14))
    + "\n"
    "    def unpack(self, frame):\n"
    '        if frame == b"HDR0":\n'
    "            return None\n"
    '        return frame[4:]\n'
)
_PACKER_CITED_LINE = _PACKER_SOURCE.splitlines().index('        if frame == b"HDR0":') + 1
assert _PACKER_SOURCE.splitlines().index(
    "    def pack(self, payload):"
) + 1 < _PACKER_CITED_LINE - 8, "fixture spacing must keep pack's body out of the +/-8 window by construction"

_PACKER_FINDING = SteppedFinding(
    title="Unpacking an empty frame does not round-trip",
    file="framing.py",
    line_start=_PACKER_CITED_LINE,
    line_end=_PACKER_CITED_LINE + 1,
    cited_line_text='if frame == b"HDR0": return None',
    failure_scenario=(
        "The round trip of an empty payload through pack and unpack is broken: unpack returns "
        "None for the header pack produced from an empty payload, instead of the original empty "
        "bytes object."
    ),
    harm_class="integrity",
    suggested_fix="Make unpack return b\"\" for the empty-payload header instead of None.",
)


def test_packer_round_trip_claim_folds_in_pack_source() -> None:
    evidence = AuditEvidence(sources={"framing.py": _PACKER_SOURCE})

    prompt = _drive(evidence, _PACKER_FINDING)

    assert _LABEL in prompt.lower(), f"round-trip claim citing unpack must fold in pack -- prompt:\n{prompt}"
    assert "def pack" in prompt, "pack's own def line must reach the prompt verbatim"
    assert 'return b"hdr1" + payload' in prompt.lower(), (
        "pack's actual body -- the other half of the round trip -- must be present, not just its "
        "signature"
    )
    assert "def unpack" in prompt, "the original cited window (unpack) must still be present too"


# ---------------------------------------------------------------------------------------
# Fixture B -- shared-state-variable pattern via write_frame/read_frame (not compress/decompress,
# not last_code/last_size). A running checksum `_tally` is threaded across writes and reads; the
# claim alleges it must be reset after a sentinel frame, citing the writer side only.
# ---------------------------------------------------------------------------------------

_STREAM_SOURCE = (
    "SENTINEL = -1\n"
    "\n"
    "class ChecksumStream:\n"
    "    def write_frame(self, values):\n"
    "        tally = 0\n"
    "        out = []\n"
    "        for value in values:\n"
    "            if value is None:\n"
    "                out.append(SENTINEL)\n"
    "                continue\n"
    "            out.append(value - tally)\n"
    "            tally = value\n"
    "        return out\n"
    "\n"
    "    def read_frame(self, deltas):\n"
    "        tally = 0\n"
    "        out = []\n"
    "        for delta in deltas:\n"
    "            if delta == SENTINEL:\n"
    "                out.append(None)\n"
    "                continue\n"
    "            tally += delta\n"
    "            out.append(tally)\n"
    "        return out\n"
)
_STREAM_FINDING = SteppedFinding(
    title="tally is not reset after a sentinel frame",
    file="checksum_stream.py",
    line_start=8,
    line_end=9,
    cited_line_text="if value is None: out.append(SENTINEL)",
    failure_scenario=(
        "After a sentinel frame, the running tally variable should be reset to zero before the "
        "next real value is written; leaving it unreset means state carries over incorrectly "
        "across the sentinel."
    ),
    harm_class="integrity",
    suggested_fix="Reset tally to 0 inside the sentinel branch of write_frame.",
)


def test_state_reset_claim_folds_in_read_frame_source() -> None:
    evidence = AuditEvidence(sources={"checksum_stream.py": _STREAM_SOURCE})

    prompt = _drive(evidence, _STREAM_FINDING)

    assert _LABEL in prompt.lower(), f"reset claim citing write_frame must fold in read_frame -- prompt:\n{prompt}"
    assert "def read_frame" in prompt
    assert "tally += delta" in prompt, (
        "the reader's own use of tally -- the fact that shows a writer-side-only reset would "
        "desync the two sides -- must actually reach the prompt"
    )
    assert "def write_frame" in prompt


# ---------------------------------------------------------------------------------------
# Control -- an ordinary, non-paired claim on a completely unrelated fixture must see NO change:
# the prompt must be byte-identical to the pre-fix single-window template.
# ---------------------------------------------------------------------------------------

_PLAIN_SOURCE = (
    "class Averager:\n"
    "    def add(self, values):\n"
    "        total = 0\n"
    "        for v in values:\n"
    "            total = total + v\n"
    "        return total / len(values)\n"
)
_PLAIN_FINDING = SteppedFinding(
    title="Division by zero on an empty values list",
    file="averager.py",
    line_start=3,
    line_end=6,
    cited_line_text="return total / len(values)",
    failure_scenario="add divides by len(values) without checking for zero, so an empty list crashes.",
    harm_class="crash",
    suggested_fix="Guard against an empty values list before dividing.",
)


def test_ordinary_claim_prompt_is_byte_identical_to_the_single_window_template() -> None:
    evidence = AuditEvidence(sources={"averager.py": _PLAIN_SOURCE})

    prompt = _drive(evidence, _PLAIN_FINDING)

    assert _LABEL not in prompt.lower(), f"no pairing language at all, nothing should be folded in -- prompt:\n{prompt}"
    source_lines = _PLAIN_SOURCE.splitlines()
    window_start = max(1, _PLAIN_FINDING.line_start - 8)
    window_end = min(len(source_lines), _PLAIN_FINDING.line_end + 8)
    expected_window = "\n".join(
        f"{number:>6}: {source_lines[number - 1]}" for number in range(window_start, window_end + 1)
    )
    expected_prompt = (
        f"Proposed title: {_PLAIN_FINDING.title}\n"
        f"Proposed location: {_PLAIN_FINDING.file}:{_PLAIN_FINDING.line_start}-{_PLAIN_FINDING.line_end}\n"
        f"Proposed failure scenario: {_PLAIN_FINDING.failure_scenario}\n\n"
        f"Source window (real line numbers):\n{expected_window}\n\n"
        "Follow steps (a)-(d) from your instructions against this source. Attempt the smallest "
        "counterexample. Return the JSON verdict only."
    )
    assert prompt == expected_prompt, "an unpaired claim must reproduce the exact pre-fix prompt"
