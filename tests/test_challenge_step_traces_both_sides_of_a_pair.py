"""Two false positives survived `_challenge_finding` on one live audit in the same night, and both
share one root cause.

**False positive 1 (empty round-trip):** the model claimed decompression on empty input was broken
because of the compressor's own `if not lines: return b""` and the decompressor's early returns,
citing the decompress side. Verified by direct execution: `compress(b"")` and
`decompress(compress(b""))` both round-trip correctly. The claim was false, and `_challenge_finding`
gave it a supported/challenged status anyway.

**False positive 2 (raw-line state, more serious):** the model claimed `last_code`/`last_size` must
be reset to their defaults after a raw/unmatched line, citing the encoder side, and suggested that
exact fix. Verified by direct execution: the code as written is already correct (raw lines never
touch these variables on either side, symmetrically), and applying the suggested fix corrupts real
decoded output on the very next matched line.

Root cause: the challenge prompt hands the model only a narrow window (+/-8 lines) around the CITED
side of a claim about a ROUND TRIP or a PAIRED relationship (encoder vs. decoder, compress vs.
decompress, producer vs. consumer). The other side of the pair is very often not in that window, so
the challenge model cannot check whether the alleged asymmetry is real -- it never sees both sides.

This file proves the fix: a claim whose own text alleges a broken pair now gets the SIBLING
method's source (found structurally via `ast`, the same approach `_deterministic_source_contradiction`
already uses) folded into the challenge prompt alongside the existing window -- never instead of it.
An ordinary claim, or a pair-shaped claim with no resolvable sibling, gets exactly the prior
single-window prompt: this is additive, not a replacement path.

No execution of audited-project code happens anywhere in this fix or these tests -- only more of
the already-read source text is handed to the (mocked) challenge model, plus a stricter reasoning
sequence in the system prompt. `_challenge_finding`'s early decidable-claim execution shortcut
(`refuting_claim`, for `<empty literal>.<method>() raises <Error>` claims) and its deterministic
absolute-negation shortcut (`_deterministic_source_contradiction`) both still run first and can
short-circuit before any model call -- every fixture below is worded to avoid tripping either one,
so the assertions below are actually exercising the model-call path this fix changes.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import (
    SteppedAuditBudget,
    SteppedFinding,
    _challenge_finding,
)

MANIFEST = SimpleNamespace(provider_id="test-provider", model_name="test-model")

_PAIRED_LABEL = "the paired operation, for comparison"


class _RecordingRouter:
    """A minimal `_invoke_manifest`, in the same shape `_ScriptedRouter` in
    `tests/test_an_audit_obeys_the_permission_it_was_given.py` uses: it records every
    `ModelRequest` it was handed and returns one queued JSON verdict as a `SimpleNamespace`
    response, exactly what `_call_step` reads (`output_text`, `usage`, `provider_id`, ...).
    """

    def __init__(self, reply_json: dict) -> None:
        self.reply_json = dict(reply_json)
        self.requests: list[object] = []

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        return (
            None,
            SimpleNamespace(
                output_text=json.dumps(self.reply_json),
                usage={"prompt_tokens": 10, "completion_tokens": 10},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )


def _run_challenge(evidence: AuditEvidence, finding: SteppedFinding) -> tuple[object, str]:
    """Drive the real `_challenge_finding` against a fake router and return (verdict, prompt)."""
    router = _RecordingRouter({"verdict": "uncertain", "reason": "x", "counterexample": ""})
    agent = SimpleNamespace(memory_router=router)
    verdict = _challenge_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        evidence=evidence,
        finding=finding,
        budget=SteppedAuditBudget(),
        usages=[],
    )
    assert len(router.requests) == 1, (
        "the challenge step must have made exactly one model call for this to be a real test of "
        f"the constructed prompt (got {len(router.requests)})"
    )
    return verdict, router.requests[0].prompt


# --------------------------------------------------------------------------------------
# Fixture 1 -- the exact empty-round-trip shape from tonight's incident.
# --------------------------------------------------------------------------------------

_ROUND_TRIP_SOURCE = (
    "class Codec:\n"
    "    def compress(self, lines):\n"
    "        if not lines:\n"
    '            return b""\n'
    '        body = b"|".join(lines)\n'
    '        return b"CMP1" + body\n'
    "\n"
    # Filler methods, the same shape the real incident's fixture used (helper_N stubs) --
    # enough of them that the challenge window's +/-8 line pad around the cited lines below
    # does NOT reach back to compress's def or body. This is what makes the assertions below
    # prove the PAIRED-OPERATION path actually ran, rather than merely overlapping with the
    # existing window by coincidence of file size.
    + "".join(
        f"    def helper_{i}(self):\n        return {i}\n" for i in range(1, 16)
    )
    + "\n"
    "    def decompress(self, blob):\n"
    "        if not blob:\n"
    '            return b""\n'
    '        if blob.startswith(b"CMP1"):\n'
    "            blob = blob[4:]\n"
    "        return blob\n"
)
# Cited lines sit inside decompress's own early return -- computed, not hand-counted, so the
# fixture and the finding can never silently drift apart from each other.
_ROUND_TRIP_CITED_LINE = _ROUND_TRIP_SOURCE.splitlines().index("        if not blob:") + 1
assert (
    _ROUND_TRIP_SOURCE.splitlines().index("    def compress(self, lines):") + 1 < _ROUND_TRIP_CITED_LINE - 8
), "fixture must be spaced so the +/-8 challenge window cannot reach compress by coincidence"
_ROUND_TRIP_FINDING = SteppedFinding(
    title="Decompression fails on empty input",
    file="codec.py",
    line_start=_ROUND_TRIP_CITED_LINE,
    line_end=_ROUND_TRIP_CITED_LINE + 1,
    cited_line_text='if not blob: return b""',
    failure_scenario=(
        "Decompression fails on empty input: the round trip of an empty payload is broken "
        "because decompress's early return does not agree with what compress produced for the "
        "same empty payload."
    ),
    harm_class="integrity",
    suggested_fix="Make the two empty-input branches agree on the same encoded representation.",
)


def test_round_trip_claim_gets_the_compress_side_folded_into_the_prompt() -> None:
    evidence = AuditEvidence(sources={"codec.py": _ROUND_TRIP_SOURCE})

    _verdict, prompt = _run_challenge(evidence, _ROUND_TRIP_FINDING)

    assert _PAIRED_LABEL in prompt.lower(), (
        "a round-trip claim citing decompress must have compress's source folded in as the "
        f"paired operation -- prompt was:\n{prompt}"
    )
    assert "def compress" in prompt, "the sibling method's own def line must be present verbatim"
    assert 'return b"cmp1" + body' in prompt.lower(), (
        "the sibling's actual body -- not just its signature -- must reach the prompt, since that "
        "body is exactly what tonight's false positive never saw"
    )
    # The original cited window must still be present too -- this is additive, not a replacement.
    assert "def decompress" in prompt


# --------------------------------------------------------------------------------------
# Fixture 2 -- the raw-line state-reset shape, the more serious of the two incidents.
# --------------------------------------------------------------------------------------

_STATE_RESET_SOURCE = (
    "RAW = -1\n"
    "\n"
    "class LogCodec:\n"
    "    def compress(self, entries):\n"
    "        last_code = 0\n"
    "        last_size = 0\n"
    "        out = []\n"
    "        for entry in entries:\n"
    "            if entry is None:\n"
    "                out.append(RAW)\n"
    "                continue\n"
    "            out.append(entry.code - last_code)\n"
    "            out.append(entry.size - last_size)\n"
    "            last_code = entry.code\n"
    "            last_size = entry.size\n"
    "        return out\n"
    "\n"
    "    def decompress(self, deltas):\n"
    "        last_code = 0\n"
    "        last_size = 0\n"
    "        out = []\n"
    "        i = 0\n"
    "        while i < len(deltas):\n"
    "            if deltas[i] == RAW:\n"
    "                out.append(None)\n"
    "                i += 1\n"
    "                continue\n"
    "            last_code += deltas[i]\n"
    "            last_size += deltas[i + 1]\n"
    "            out.append((last_code, last_size))\n"
    "            i += 2\n"
    "        return out\n"
)
# Cited lines sit inside compress's raw-entry branch (lines 9-11, 1-indexed).
_STATE_RESET_FINDING = SteppedFinding(
    title="last_code and last_size are not reset after a raw entry",
    file="log_codec.py",
    line_start=9,
    line_end=11,
    cited_line_text="if entry is None: out.append(RAW)",
    failure_scenario=(
        "After an unmatched raw entry, last_code and last_size should be reset to their defaults "
        "before the next matched entry; leaving them unreset means state carries over incorrectly "
        "across the raw entry."
    ),
    harm_class="integrity",
    suggested_fix="Reset last_code and last_size to 0 inside the raw-entry branch of compress.",
)


def test_state_reset_claim_gets_the_decompress_side_folded_into_the_prompt() -> None:
    evidence = AuditEvidence(sources={"log_codec.py": _STATE_RESET_SOURCE})

    _verdict, prompt = _run_challenge(evidence, _STATE_RESET_FINDING)

    assert _PAIRED_LABEL in prompt.lower(), (
        "a reset-vs-retained-state claim citing compress must have decompress's source folded "
        f"in as the paired operation -- prompt was:\n{prompt}"
    )
    assert "def decompress" in prompt
    assert "last_code += deltas[i]" in prompt, (
        "the decoder's own use of last_code -- the fact that proves the suggested encoder-side "
        "reset would desync it -- must actually reach the prompt text"
    )
    assert "def compress" in prompt


# --------------------------------------------------------------------------------------
# Control 1 -- an ordinary claim with no round-trip/pairing language must see NO change at all.
# --------------------------------------------------------------------------------------

_ORDINARY_SOURCE = (
    "class Codec:\n"
    "    def compress(self, lines):\n"
    "        total = 0\n"
    "        for line in lines:\n"
    "            total += len(line)\n"
    "        return total\n"
)
_ORDINARY_FINDING = SteppedFinding(
    title="Off-by-one undercounts the final line",
    file="ordinary.py",
    line_start=3,
    line_end=5,
    cited_line_text="total += len(line)",
    failure_scenario=(
        "The loop starts total at zero and adds each line's length, but the accumulated total "
        "is short by one for any line containing a trailing null byte."
    ),
    harm_class="integrity",
    suggested_fix="Count the trailing null byte in the per-line length.",
)


def test_an_ordinary_claim_is_not_treated_as_a_pair_and_the_prompt_is_unchanged() -> None:
    evidence = AuditEvidence(sources={"ordinary.py": _ORDINARY_SOURCE})

    _verdict, prompt = _run_challenge(evidence, _ORDINARY_FINDING)

    assert _PAIRED_LABEL not in prompt.lower(), (
        "an ordinary, non-paired claim must not have any sibling source folded in -- prompt was:\n"
        + prompt
    )
    source_lines = _ORDINARY_SOURCE.splitlines()
    window_start = max(1, _ORDINARY_FINDING.line_start - 8)
    window_end = min(len(source_lines), _ORDINARY_FINDING.line_end + 8)
    expected_window = "\n".join(
        f"{number:>6}: {source_lines[number - 1]}"
        for number in range(window_start, window_end + 1)
    )
    expected_prompt = (
        f"Proposed title: {_ORDINARY_FINDING.title}\n"
        f"Proposed location: {_ORDINARY_FINDING.file}:"
        f"{_ORDINARY_FINDING.line_start}-{_ORDINARY_FINDING.line_end}\n"
        f"Proposed failure scenario: {_ORDINARY_FINDING.failure_scenario}\n\n"
        f"Source window (real line numbers):\n{expected_window}\n\n"
        "Follow steps (a)-(d) from your instructions against this source. Attempt the smallest "
        "counterexample. Return the JSON verdict only."
    )
    assert prompt == expected_prompt, (
        "a claim with no pairing language at all must produce byte-identical output to the "
        "single-window template -- no regression on ordinary claims"
    )


# --------------------------------------------------------------------------------------
# Control 2 -- a round-trip-SHAPED claim where no structural pair can be found must fall back
# cleanly to the single-window prompt, with no error.
# --------------------------------------------------------------------------------------

_NO_PAIR_SOURCE = (
    "def transform(data):\n"
    "    if not data:\n"
    "        return data\n"
    "    return data[::-1]\n"
)
_NO_PAIR_FINDING = SteppedFinding(
    title="The round trip of empty input is broken",
    file="no_class.py",
    line_start=2,
    line_end=3,
    cited_line_text="if not data: return data",
    failure_scenario=(
        "The round-trip of an empty payload through transform and its inverse does not preserve "
        "the original value."
    ),
    harm_class="integrity",
    suggested_fix="Make the empty-input branch agree with the inverse transform.",
)


def test_a_pair_shaped_claim_with_no_resolvable_sibling_falls_back_with_no_error() -> None:
    """`transform` is a bare module-level function, not a class method -- there is no sibling to
    find. This must not raise, and must fall back to exactly the single-window prompt."""
    evidence = AuditEvidence(sources={"no_class.py": _NO_PAIR_SOURCE})

    _verdict, prompt = _run_challenge(evidence, _NO_PAIR_FINDING)

    assert _PAIRED_LABEL not in prompt.lower(), (
        "no structural pair exists for a bare function -- nothing must be folded in, and no "
        f"error should have been raised getting here -- prompt was:\n{prompt}"
    )
    source_lines = _NO_PAIR_SOURCE.splitlines()
    window_start = max(1, _NO_PAIR_FINDING.line_start - 8)
    window_end = min(len(source_lines), _NO_PAIR_FINDING.line_end + 8)
    expected_window = "\n".join(
        f"{number:>6}: {source_lines[number - 1]}"
        for number in range(window_start, window_end + 1)
    )
    assert f"Source window (real line numbers):\n{expected_window}" in prompt


# --------------------------------------------------------------------------------------
# Detector-level checks, direct against the two regex/AST helpers -- worst-case inputs
# (CLAUDE.md 0.4), not just the two happy-path incident shapes above.
# --------------------------------------------------------------------------------------


def test_detector_direct_on_the_two_incident_claims() -> None:
    from core.agent_runtime.stepped_audit import _is_paired_operation_claim

    assert _is_paired_operation_claim(
        f"{_ROUND_TRIP_FINDING.title} {_ROUND_TRIP_FINDING.failure_scenario}"
    )
    assert _is_paired_operation_claim(
        f"{_STATE_RESET_FINDING.title} {_STATE_RESET_FINDING.failure_scenario}"
    )
    assert not _is_paired_operation_claim(
        f"{_ORDINARY_FINDING.title} {_ORDINARY_FINDING.failure_scenario}"
    )


def test_detector_does_not_false_positive_on_unrelated_use_of_the_bare_words() -> None:
    """Adversarial: 'write' and 'read' both appear in a LOT of ordinary prose that has nothing to
    do with a producer/consumer pairing. This is a known, accepted, bounded keyword detector (same
    posture as `_OUTPUT_INTEGRITY_HARM_RE`'s documented tradeoff) -- it is not claimed to be
    semantically perfect, only that the two real incident phrasings trip it and a plain unrelated
    sentence mentioning neither operation nor reset/pair vocabulary does not trip it by accident
    from words like 'ready' or 'writer'."""
    from core.agent_runtime.stepped_audit import _is_paired_operation_claim

    assert not _is_paired_operation_claim(
        "The writer object holds a reference that is never released, leaking memory over a long "
        "session."
    )
    assert not _is_paired_operation_claim(
        "The variable name is misleading and should be renamed for readability."
    )


def test_paired_source_returns_empty_for_a_non_python_file() -> None:
    """Item 3's other named case: a claim about a JavaScript file must fall back cleanly too."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    js_source = (
        "class Codec {\n"
        "  compress(lines) { if (!lines.length) return ''; return 'CMP1' + lines.join('|'); }\n"
        "  decompress(blob) { if (!blob) return ''; return blob; }\n"
        "}\n"
    )
    finding = SteppedFinding(
        title="Round trip of empty input is broken",
        file="codec.js",
        line_start=3,
        line_end=3,
        cited_line_text="decompress(blob) { if (!blob) return ''; return blob; }",
        failure_scenario="The round trip of an empty blob through compress/decompress is broken.",
    )
    evidence = AuditEvidence(sources={"codec.js": js_source})

    assert _paired_operation_source(evidence, finding) == ""


def test_paired_source_returns_empty_when_the_file_does_not_parse() -> None:
    """Adversarial: syntactically broken Python must not raise -- it must fall back like any other
    unresolvable case."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    broken_source = "class Codec:\n    def decompress(self, blob:\n        return blob\n"
    finding = SteppedFinding(
        title="Round trip of empty input is broken",
        file="broken.py",
        line_start=2,
        line_end=3,
        cited_line_text="def decompress(self, blob:",
        failure_scenario="The round trip of an empty blob through compress/decompress is broken.",
    )
    evidence = AuditEvidence(sources={"broken.py": broken_source})

    assert _paired_operation_source(evidence, finding) == ""


# --------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md section 6 / project mandate 0.7): this is asserted by re-running
# the same fixtures with the pairing machinery forced off, proving the assertions above actually
# depend on the fix and are not vacuously true. The real sabotage-and-restore drive (revert the
# diff, rerun, confirm failure naming the missing content, then restore) is done manually outside
# pytest as the task requires; this in-process control proves the same causal point without
# mutating the file under test.
# --------------------------------------------------------------------------------------


def test_sabotage_forcing_the_detector_off_reproduces_the_original_incident_shape() -> None:
    """If `_is_paired_operation_claim` always returned False (the pre-fix behavior), the round-trip
    prompt would contain no compress source at all -- reproducing exactly the incident's blind
    spot. This proves the passing assertions above are not vacuous."""
    import core.agent_runtime.stepped_audit as stepped_audit

    evidence = AuditEvidence(sources={"codec.py": _ROUND_TRIP_SOURCE})
    original = stepped_audit._is_paired_operation_claim
    stepped_audit._is_paired_operation_claim = lambda _text: False
    try:
        _verdict, sabotaged_prompt = _run_challenge(evidence, _ROUND_TRIP_FINDING)
    finally:
        stepped_audit._is_paired_operation_claim = original

    assert _PAIRED_LABEL not in sabotaged_prompt.lower(), (
        "with the detector forced off, the prompt must reproduce the ORIGINAL bug: no compress "
        "source reaches the challenge model at all"
    )
    assert "def compress" not in sabotaged_prompt, (
        "sabotage must actually remove the paired source -- if compress's def line is still "
        "present with the detector disabled, this control does not prove what it claims to"
    )

    # And the real (unsabotaged) path still works right after, proving the monkeypatch above did
    # not leak into other tests.
    _verdict, real_prompt = _run_challenge(evidence, _ROUND_TRIP_FINDING)
    assert _PAIRED_LABEL in real_prompt.lower()


# --------------------------------------------------------------------------------------
# QA defect 1 -- the sibling resolver picked the WRONG method (substring-containment, first
# match in class-body order) on the real incident file this whole fix was built to solve.
# --------------------------------------------------------------------------------------

_LIQUEFY_APACHE_PATH = (
    "/Users/example-user/Desktop/openclaw-skills/api/apache/liquefy_apache_repetition_v1.py"
)
# This is the real incident file the paired-operation fix was built against -- it lives in a
# sibling project on the machine that reproduced the incident, not in this repo, so it is not
# present on CI runners (or any other checkout). Skip rather than fail when it is absent; both
# tests still run for real, against the real file, on a machine that has it.
_skip_if_liquefy_apache_missing = pytest.mark.skipif(
    not os.path.exists(_LIQUEFY_APACHE_PATH),
    reason=f"real incident fixture not present on this machine: {_LIQUEFY_APACHE_PATH}",
)


@_skip_if_liquefy_apache_missing
def test_the_real_incident_file_resolves_the_sibling_to_decompress_not_zstd_decompress() -> None:
    """Confirmed live against the actual file: citing the real second-incident finding (the
    unreset `last_code`/`last_size` claim, citing `compress` lines 80-82) used to resolve to
    `_zstd_decompress` (an unrelated 3-line zstd wrapper at line 112) instead of the real
    `decompress` method (line 116, ~54 lines, containing the actual `last_code +=
    zigzag_dec(...)` / `last_size += zigzag_dec(...)` accumulation logic the claim is about) --
    because `_zstd_decompress` textually CONTAINS the substring `decompress` and appears earlier
    in the class body. This proves the token/score-based match now resolves to the real sibling.
    """
    from core.agent_runtime.stepped_audit import _paired_operation_source

    with open(_LIQUEFY_APACHE_PATH, encoding="utf-8") as handle:
        source = handle.read()

    finding = SteppedFinding(
        title="last_code and last_size are not reset after a raw entry",
        file=_LIQUEFY_APACHE_PATH,
        line_start=80,
        line_end=82,
        cited_line_text="self._flush_run(s_pats, last_pat_id, run_count); last_pat_id = -1; run_count = 0",
        failure_scenario=(
            "After an unmatched raw line, last_code and last_size should be reset to their "
            "defaults before the next matched line; leaving them unreset means state carries "
            "over incorrectly across the raw line on both compress and decompress."
        ),
        harm_class="integrity",
        suggested_fix="Reset last_code and last_size to 0 inside the raw-line branch of compress.",
    )
    evidence = AuditEvidence(sources={_LIQUEFY_APACHE_PATH: source})

    result = _paired_operation_source(evidence, finding)

    assert result, "the structural scan must resolve a sibling on this real file"
    assert "def decompress" in result, f"must resolve to the real `decompress` method, got:\n{result}"
    assert "last_code" in result and "zigzag_dec" in result, (
        "the real decompress method's own accumulation logic -- the actual subject of the claim "
        f"-- must be present in the resolved source, got:\n{result}"
    )
    assert "def _zstd_decompress" not in result, (
        "the wrong sibling (`_zstd_decompress`, a 3-line unrelated zstd wrapper that merely "
        f"contains the substring `decompress`) must not be what gets resolved, got:\n{result}"
    )


def test_exact_bare_name_beats_a_longer_token_match_in_general_not_just_on_the_one_file() -> None:
    """Synthetic class, not the real file: a `_zstd_decompress`-shaped method (extra token,
    textually first in the class body) sits alongside a real bare `decompress` method (exact
    token match, later in the body). The fix must prefer the exact bare-name match regardless of
    body order -- this is the general rule, not a fixture-specific coincidence."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    source = (
        "class Codec:\n"
        "    def compress(self, raw):\n"
        "        total = 0\n"
        "        for b in raw:\n"
        "            total += b\n"
        "        return total\n"
        "\n"
        # The decoy sits textually BEFORE the real sibling, same as the real incident.
        "    def _zstd_decompress(self, payload):\n"
        "        return payload\n"
        "\n"
        "    def decompress(self, blob):\n"
        "        last_code = 0\n"
        "        last_size = 0\n"
        "        out = []\n"
        "        for b in blob:\n"
        "            last_code += b\n"
        "            out.append(last_code)\n"
        "        return out\n"
    )
    finding = SteppedFinding(
        title="last_code is not reset",
        file="codec.py",
        line_start=2,
        line_end=6,
        cited_line_text="total += b",
        failure_scenario="last_code should be reset between runs but is not.",
        harm_class="integrity",
        suggested_fix="Reset last_code.",
    )
    evidence = AuditEvidence(sources={"codec.py": source})

    result = _paired_operation_source(evidence, finding)

    assert "def decompress" in result, f"must pick the exact bare-name sibling, got:\n{result}"
    assert "last_code += b" in result, f"must be the REAL decompress body, got:\n{result}"
    assert "def _zstd_decompress" not in result, (
        f"must not pick the extra-token decoy that merely contains the term, got:\n{result}"
    )


def test_sibling_match_score_direct_scores_bare_name_above_extra_token_name() -> None:
    """Detector-level check on the scoring function itself, worst-case-first (CLAUDE.md 0.4): an
    exact bare-term name scores strictly higher than a name carrying the term plus another
    segment, and a name that only contains the term as part of a larger unsplit word (no `_`
    boundary at all) does not match, since that would still be substring matching."""
    from core.agent_runtime.stepped_audit import _sibling_match_score

    terms = frozenset({"decompress"})
    assert _sibling_match_score("decompress", terms) == 2
    assert _sibling_match_score("_zstd_decompress", terms) == 1
    assert _sibling_match_score("decompress", terms) > _sibling_match_score("_zstd_decompress", terms)
    # No `_` boundary at all around the term inside a longer identifier: not a token match.
    assert _sibling_match_score("predecompressed", terms) is None
    assert _sibling_match_score("unrelated_method", terms) is None


# --------------------------------------------------------------------------------------
# QA defect 2 -- silent truncation with no marker, and a naive prefix slice keeps the wrong end
# of the method (dropping reset/cleanup logic that QA's fixture placed at the END of a ~90-line
# method).
# --------------------------------------------------------------------------------------


def test_truncation_over_the_cap_emits_an_explicit_marker_and_keeps_the_tail() -> None:
    from core.agent_runtime.stepped_audit import _PAIRED_OPERATION_CHARS, _paired_operation_source

    # A realistic ~90-line method, padded well past the 4000-char cap, with the relevant
    # last_code/last_size reset statements at the END -- the exact shape QA built to catch a
    # naive head-only slice dropping them silently.
    filler = "".join(f"        value_{i} = compute_step({i})\n" for i in range(1, 250))
    method_source = (
        "    def decompress(self, blob):\n"
        f"{filler}"
        "        last_code = 0\n"
        "        last_size = 0\n"
        "        return last_code, last_size\n"
    )
    source = "class Codec:\n" "    def compress(self, raw):\n" "        return raw\n" "\n" + method_source
    assert len(method_source) > _PAIRED_OPERATION_CHARS, "fixture must actually exceed the cap"

    finding = SteppedFinding(
        title="last_code is not reset",
        file="codec.py",
        line_start=2,
        line_end=3,
        cited_line_text="return raw",
        failure_scenario="last_code should be reset between runs but is not.",
        harm_class="integrity",
        suggested_fix="Reset last_code.",
    )
    evidence = AuditEvidence(sources={"codec.py": source})

    result = _paired_operation_source(evidence, finding)

    assert len(result) <= _PAIRED_OPERATION_CHARS, "the result must still respect the cap"
    assert "omitted" in result.lower(), (
        f"truncation must never be silent -- an explicit marker is required, got:\n{result}"
    )
    assert "def decompress" in result, "the signature line must survive truncation"
    assert "last_code = 0" in result and "last_size = 0" in result, (
        "the reset statements at the END of the method -- the whole point of QA's fixture -- must "
        f"survive truncation, not be silently dropped by a naive prefix slice, got:\n{result}"
    )


def test_a_paired_source_under_the_cap_is_returned_verbatim_with_no_marker() -> None:
    """Control: truncation machinery must not fire, and must add no marker, when the sibling
    source is already under the cap -- this is additive on overflow only."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    source = (
        "class Codec:\n"
        "    def compress(self, raw):\n"
        "        return raw\n"
        "\n"
        "    def decompress(self, blob):\n"
        "        return blob\n"
    )
    finding = SteppedFinding(
        title="Round trip is broken",
        file="codec.py",
        line_start=2,
        line_end=3,
        cited_line_text="return raw",
        failure_scenario="The round trip of a payload through compress/decompress is broken.",
        harm_class="integrity",
        suggested_fix="Fix it.",
    )
    evidence = AuditEvidence(sources={"codec.py": source})

    result = _paired_operation_source(evidence, finding)

    assert "omitted" not in result.lower()
    assert "def decompress" in result and "return blob" in result


# --------------------------------------------------------------------------------------
# QA defect 3 -- no reason-quality gate on 'refuted' verdicts, asymmetric with 'supported'.
# --------------------------------------------------------------------------------------


def test_a_refuted_verdict_with_no_reason_or_counterexample_downgrades_to_uncertain() -> None:
    """Mirrors the existing `supported`-with-empty-reason test: a `refuted` verdict is only as
    trustworthy as the explanation behind it. Before this fix, `_challenge_finding` accepted a
    bare `{"verdict": "refuted"}` at face value -- a weak model (or one handed a wrong/irrelevant
    sibling, defect 1's own failure mode) could silently kill a real finding with a shallow,
    unexplained refutation and nothing would catch it, unlike the `supported` path."""
    router = _RecordingRouter({"verdict": "refuted", "reason": "", "counterexample": ""})
    agent = SimpleNamespace(memory_router=router)
    evidence = AuditEvidence(sources={"ordinary.py": _ORDINARY_SOURCE})

    verdict = _challenge_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        evidence=evidence,
        finding=_ORDINARY_FINDING,
        budget=SteppedAuditBudget(),
        usages=[],
    )

    assert verdict.verdict == "uncertain", (
        f"an unexplained 'refuted' verdict must be downgraded to 'uncertain', got {verdict!r}"
    )
    assert "counterexample" in verdict.reason.lower() or "explan" in verdict.reason.lower()


def test_a_refuted_verdict_with_reason_but_no_counterexample_still_downgrades() -> None:
    """A prose reason alone is not enough for a refutation specifically -- this codebase's own
    convention (`ExecutedClaim.counterexample()` in `audit_claim_execution.py`) ties a
    counterexample to a refutation. A refutation asserting only "this looks fine" with no
    concrete counterexample is exactly the shallow kill defect 3 targets."""
    router = _RecordingRouter(
        {"verdict": "refuted", "reason": "the code already handles this case correctly", "counterexample": ""}
    )
    agent = SimpleNamespace(memory_router=router)
    evidence = AuditEvidence(sources={"ordinary.py": _ORDINARY_SOURCE})

    verdict = _challenge_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        evidence=evidence,
        finding=_ORDINARY_FINDING,
        budget=SteppedAuditBudget(),
        usages=[],
    )

    assert verdict.verdict == "uncertain"



# --------------------------------------------------------------------------------------
# QA round 3 -- `_best_sibling_match` picked the highest-scoring candidate, but silently broke a
# TIE at the top score by class-body order instead of reporting the ambiguity. Two real,
# confirmed reproductions: a bare `parse` decoy tying the real `deserialize` sibling at score 2,
# and two non-bare `_..._internal`-named methods tying at score 1. Both times the WRONG candidate
# happened to sit first in the class body and was returned with full, silent confidence -- the
# exact pre-fix failure shape this whole detector exists to prevent.
# --------------------------------------------------------------------------------------

_SERIALIZE_PARSE_TIE_SOURCE = (
    "class Config:\n"
    "    def serialize(self, data):\n"
    "        return str(data)\n"
    "\n"
    "    def parse(self, raw):\n"
    "        # Unrelated: parses a 'a=1;b=2' config string. Nothing to do with the real pair.\n"
    "        pairs = [p for p in raw.split(';') if p]\n"
    "        return dict(p.split('=') for p in pairs)\n"
    "\n"
    "    def deserialize(self, blob):\n"
    "        return eval(blob)\n"
)
# Cited line sits inside serialize's own body, computed rather than hand-counted so the fixture
# and the finding cannot silently drift apart.
_SERIALIZE_PARSE_TIE_CITED_LINE = (
    _SERIALIZE_PARSE_TIE_SOURCE.splitlines().index("        return str(data)") + 1
)


def test_bare_serialize_parse_deserialize_tie_falls_back_to_empty_not_the_parse_decoy() -> None:
    """QA round 3, reproduction 1: `parse` (an unrelated config-string parser, nothing to do with
    the real pair) and the real `deserialize` sibling both score as bare-name matches (score 2).
    `parse` sits first in class-body order. Before the tie-detection fix this silently won and
    was handed to the challenge model as "the paired operation" with no signal the pick was
    arbitrary; now the tie must fall back to "" -- the same safe fallback as "no plausible
    sibling found" -- never the decoy."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Serialized output is not the inverse of deserialize",
        file="config.py",
        line_start=_SERIALIZE_PARSE_TIE_CITED_LINE,
        line_end=_SERIALIZE_PARSE_TIE_CITED_LINE,
        cited_line_text="return str(data)",
        failure_scenario=(
            "The round trip of serialize and deserialize does not agree on how a value is "
            "represented."
        ),
        harm_class="integrity",
        suggested_fix="Make serialize and deserialize agree on one representation.",
    )
    evidence = AuditEvidence(sources={"config.py": _SERIALIZE_PARSE_TIE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert result == "", (
        "a genuine tie between the parse decoy (score 2) and the real deserialize sibling "
        f"(score 2) must fall back to the safe empty result, not silently pick either one -- "
        f"the pre-fix behavior would have returned the parse decoy here, got:\n{result}"
    )


_INTERNAL_NAMING_TIE_SOURCE = (
    "class Store:\n"
    "    def _write_internal(self, data):\n"
    "        self._buffer = data\n"
    "\n"
    "    def _legacy_read_internal(self, key):\n"
    "        # Unrelated: reads from a deprecated legacy cache format, nothing to do with the\n"
    "        # real pair.\n"
    "        return self._legacy_cache.get(key)\n"
    "\n"
    "    def _state_read_internal(self, key):\n"
    "        return self._buffer\n"
)
_INTERNAL_NAMING_TIE_CITED_LINE = (
    _INTERNAL_NAMING_TIE_SOURCE.splitlines().index("        self._buffer = data") + 1
)


def test_decorated_internal_naming_tie_falls_back_to_empty_not_the_decoy() -> None:
    """QA round 3, reproduction 2: neither candidate is a bare name -- `_legacy_read_internal`
    (an unrelated legacy-cache reader) and `_state_read_internal` (the real sibling) both score
    identically (score 1: both carry the bare term `read` plus extra tokens). The decoy sits
    first in class-body order. Before the tie-detection fix this silently won; now the tie must
    fall back to "" , never the decoy."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Write is not visible to the paired read",
        file="store.py",
        line_start=_INTERNAL_NAMING_TIE_CITED_LINE,
        line_end=_INTERNAL_NAMING_TIE_CITED_LINE,
        cited_line_text="self._buffer = data",
        failure_scenario=(
            "A value written by _write_internal is not visible to its paired read afterward."
        ),
        harm_class="integrity",
        suggested_fix="Make the write and its paired read agree on the same storage.",
    )
    evidence = AuditEvidence(sources={"store.py": _INTERNAL_NAMING_TIE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert result == "", (
        "a genuine tie between the _legacy_read_internal decoy (score 1) and the real "
        f"_state_read_internal sibling (score 1) must fall back to the safe empty result -- the "
        f"pre-fix behavior would have returned the decoy here, got:\n{result}"
    )


def test_the_real_incident_and_bare_name_tests_are_not_ties_and_still_pass() -> None:
    """Control, restated explicitly for this round: the real-incident-file test
    (`test_the_real_incident_file_resolves_the_sibling_to_decompress_not_zstd_decompress`) and the
    exact-bare-name-beats-decorated-name test
    (`test_exact_bare_name_beats_a_longer_token_match_in_general_not_just_on_the_one_file`) above
    are NOT ties -- `decompress` (score 2) strictly outscores `_zstd_decompress` (score 1) in
    both. The tie-detection fix must not treat a genuine, unambiguous winner as ambiguous. This
    test does not re-derive those two cases (they already run and are asserted above); it exists
    so a reader of this round's diff sees the non-regression stated as its own claim rather than
    inferred."""
    from core.agent_runtime.stepped_audit import _sibling_match_score

    terms = frozenset({"decompress"})
    assert _sibling_match_score("decompress", terms) != _sibling_match_score(
        "_zstd_decompress", terms
    ), "this fixture must be a genuine, unambiguous winner, not a tie, for the control to mean anything"


def test_best_sibling_match_returns_none_on_a_genuine_three_way_tie() -> None:
    """Direct unit test on the picker itself (CLAUDE.md 0.4 -- worst case, not the two-way
    incident shapes alone): three distinct candidates all reach the same top score. The picker
    must report the ambiguity as None rather than pick any of the three arbitrarily."""
    from core.agent_runtime.stepped_audit import _best_sibling_match, _sibling_match_score

    cited = SimpleNamespace(name="serialize")
    candidate_a = SimpleNamespace(name="parse")
    candidate_b = SimpleNamespace(name="deserialize")
    candidate_c = SimpleNamespace(name="deserialise")
    pair_terms = frozenset({"deserialize", "deserialise", "parse"})
    # Confirm the premise directly before asking the picker to resolve it: all three must
    # genuinely score identically, or this is not testing a tie at all.
    scores = {
        candidate.name: _sibling_match_score(candidate.name, pair_terms)
        for candidate in (candidate_a, candidate_b, candidate_c)
    }
    assert len(set(scores.values())) == 1 and None not in scores.values(), (
        f"fixture must be a genuine 3-way tie with no None scores, got {scores!r}"
    )

    result = _best_sibling_match(
        [cited, candidate_a, candidate_b, candidate_c], cited, pair_terms
    )

    assert result is None, f"a genuine 3-way tie at the top score must return None, got {result!r}"


def test_best_sibling_match_still_returns_the_sole_winner_when_scores_differ() -> None:
    """Control on the picker directly: when candidates do NOT share the top score, the picker
    must still return the actual winner -- the tie fix must not turn every multi-candidate scan
    into a None."""
    from core.agent_runtime.stepped_audit import _best_sibling_match

    cited = SimpleNamespace(name="compress")
    decoy = SimpleNamespace(name="_zstd_decompress")
    winner = SimpleNamespace(name="decompress")
    pair_terms = frozenset({"decompress"})

    result = _best_sibling_match([cited, decoy, winner], cited, pair_terms)

    assert result is winner, f"a genuine, unambiguous winner must still be returned, got {result!r}"


def test_a_refuted_verdict_with_both_reason_and_counterexample_is_accepted() -> None:
    """Control: the gate must not be so strict that it downgrades a genuinely well-supported
    refutation -- only an unexplained one."""
    router = _RecordingRouter(
        {
            "verdict": "refuted",
            "reason": "the loop already counts every line including any trailing null byte",
            "counterexample": "total(['a\\x00']) == 2, matching len('a\\x00')",
        }
    )
    agent = SimpleNamespace(memory_router=router)
    evidence = AuditEvidence(sources={"ordinary.py": _ORDINARY_SOURCE})

    verdict = _challenge_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        evidence=evidence,
        finding=_ORDINARY_FINDING,
        budget=SteppedAuditBudget(),
        usages=[],
    )

    assert verdict.verdict == "refuted"
    assert verdict.counterexample == "total(['a\\x00']) == 2, matching len('a\\x00')"


# --------------------------------------------------------------------------------------
# QA round 4 -- `_sibling_pair_terms` built its term set via SUBSTRING containment
# (`term in lowered`), unlike `_sibling_match_score` three lines below it, which deliberately
# documents whole-token matching specifically to avoid this class of bug. `'compress'` is a literal
# substring of `'decompress'`, and `'serialize'`/`'serialise'` are literal substrings of
# `'deserialize'`/`'deserialise'`, so citing the CONSUMER-side name of either pair family made the
# function match against ITSELF -- `_sibling_pair_terms('decompress')` used to return
# `frozenset({'compress', 'decompress'})` (including itself), and
# `_sibling_pair_terms('deserialize')` used to return
# `frozenset({'serialise', 'parse', 'deserialize', 'deserialise', 'serialize'})` (itself plus
# everything). This is worse than a false negative: with a class that has NO real `compress` method
# but an unrelated `_alt_decompress` (a different codec's decompressor), the polluted term set made
# `_alt_decompress` match as a fabricated "sibling" of `decompress` purely because the self-polluted
# term `decompress` is one of `_alt_decompress`'s own tokens -- with no real candidate present at
# all. Confirmed for two live shapes: an unrelated `_alt_decompress`, and an unrelated bare `parse`
# CLI-args parser standing in for a missing `serialize`.
# --------------------------------------------------------------------------------------


def test_sibling_pair_terms_does_not_pollute_itself_with_the_cited_name() -> None:
    """Direct unit test on the term-set builder: citing the CONSUMER side of a pair must never
    include its own name (or the other consumer-side spellings) in the returned term set --
    exactly the exact two term sets QA's confirmed finding names verbatim."""
    from core.agent_runtime.stepped_audit import _sibling_pair_terms

    decompress_terms = _sibling_pair_terms("decompress")
    assert decompress_terms == frozenset({"compress"}), (
        f"must be exactly {{'compress'}} with no self-pollution, got {decompress_terms!r}"
    )
    assert "decompress" not in decompress_terms

    deserialize_terms = _sibling_pair_terms("deserialize")
    assert deserialize_terms == frozenset({"serialize", "serialise"}), (
        "must be exactly {'serialize', 'serialise'} -- no 'deserialize', no 'deserialise', and no "
        f"'parse' via self-matching -- got {deserialize_terms!r}"
    )
    assert "deserialize" not in deserialize_terms
    assert "deserialise" not in deserialize_terms
    assert "parse" not in deserialize_terms


def test_sibling_pair_terms_citing_the_producer_side_still_includes_its_alternate_spellings() -> None:
    """Control on the same builder: citing the PRODUCER side (`serialize`, the right-hand term in
    the table) legitimately pulls in `parse` as one of the listed alternate spellings of the
    consumer side -- `parse` is not banned outright, only banned from appearing via self-matching
    when the cited name is itself on the consumer side."""
    from core.agent_runtime.stepped_audit import _sibling_pair_terms

    assert _sibling_pair_terms("serialize") == frozenset({"deserialize", "deserialise", "parse"})
    assert _sibling_pair_terms("compress") == frozenset({"decompress"})


_ALT_DECOMPRESS_DECOY_SOURCE = (
    "class Codec:\n"
    "    def decompress(self, blob):\n"
    "        if not blob:\n"
    '            return b""\n'
    "        return blob[4:]\n"
    "\n"
    "    def _alt_decompress(self, payload):\n"
    "        # Unrelated: a different codec's own decompressor, nothing to do with the real pair.\n"
    "        # No real `compress` method exists anywhere in this class.\n"
    "        return payload[8:]\n"
)
_ALT_DECOMPRESS_DECOY_CITED_LINE = (
    _ALT_DECOMPRESS_DECOY_SOURCE.splitlines().index("        if not blob:") + 1
)


def test_qa_repro_1_decompress_cited_with_only_an_unrelated_alt_decompress_sibling_yields_empty() -> None:
    """QA's confirmed finding, reproduction 1: a class with `decompress` cited and only an
    unrelated `_alt_decompress` sibling (no real `compress` method anywhere). Before the fix, the
    self-polluted term set `{'compress', 'decompress'}` made `_alt_decompress` match (its own
    `decompress` token intersects the polluted `decompress` term), fabricating a pairing with no
    real candidate present at all. Must resolve to the safe empty result."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of empty input is broken",
        file="codec.py",
        line_start=_ALT_DECOMPRESS_DECOY_CITED_LINE,
        line_end=_ALT_DECOMPRESS_DECOY_CITED_LINE + 1,
        cited_line_text='if not blob: return b""',
        failure_scenario=(
            "The round trip of an empty payload through compress/decompress is broken."
        ),
        harm_class="integrity",
        suggested_fix="Make the empty-input branches agree.",
    )
    evidence = AuditEvidence(sources={"codec.py": _ALT_DECOMPRESS_DECOY_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert result == "", (
        "no real `compress` method exists -- the unrelated `_alt_decompress` must never be "
        f"fabricated as a sibling, got:\n{result}"
    )


_BARE_PARSE_DECOY_SOURCE = (
    "class Config:\n"
    "    def deserialize(self, blob):\n"
    "        return eval(blob)\n"
    "\n"
    "    def parse(self, argv):\n"
    "        # Unrelated: a bare CLI-args parser, nothing to do with the real pair.\n"
    "        # No real `serialize` method exists anywhere in this class.\n"
    "        return {arg.split('=')[0]: arg.split('=')[1] for arg in argv if '=' in arg}\n"
)
_BARE_PARSE_DECOY_CITED_LINE = (
    _BARE_PARSE_DECOY_SOURCE.splitlines().index("        return eval(blob)") + 1
)


def test_qa_repro_2_deserialize_cited_with_only_an_unrelated_bare_parse_sibling_yields_empty() -> None:
    """QA's confirmed finding, reproduction 2: a class with `deserialize` cited and only an
    unrelated bare `parse` CLI-args parser sibling (no real `serialize` method anywhere). Before
    the fix, the self-polluted term set included `parse` itself (via `deserialize`'s own substring
    match against the pair table), so the unrelated bare `parse` scored a bare-name match (score 2)
    and was fabricated as the sibling with no real candidate present. Must resolve to the safe
    empty result."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Serialized output is not the inverse of deserialize",
        file="config.py",
        line_start=_BARE_PARSE_DECOY_CITED_LINE,
        line_end=_BARE_PARSE_DECOY_CITED_LINE,
        cited_line_text="return eval(blob)",
        failure_scenario=(
            "The round trip of serialize and deserialize does not agree on how a value is "
            "represented."
        ),
        harm_class="integrity",
        suggested_fix="Make serialize and deserialize agree on one representation.",
    )
    evidence = AuditEvidence(sources={"config.py": _BARE_PARSE_DECOY_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert result == "", (
        "no real `serialize` method exists -- the unrelated bare `parse` must never be fabricated "
        f"as a sibling, got:\n{result}"
    )


# --------------------------------------------------------------------------------------
# Controls -- genuine pairs (citing either side) must still resolve correctly. The fix must not
# regress the real, intended case while closing off the self-pollution false positive.
# --------------------------------------------------------------------------------------

_GENUINE_COMPRESS_PAIR_SOURCE = (
    "class Codec:\n"
    "    def compress(self, raw):\n"
    '        return b"CMP1" + raw\n'
    "\n"
    "    def decompress(self, blob):\n"
    "        return blob[4:]\n"
)


def test_control_genuine_compress_decompress_pair_still_resolves_citing_decompress() -> None:
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip is broken",
        file="codec.py",
        line_start=5,
        line_end=6,
        cited_line_text="return blob[4:]",
        failure_scenario="The round trip of compress/decompress is broken.",
        harm_class="integrity",
        suggested_fix="Fix it.",
    )
    evidence = AuditEvidence(sources={"codec.py": _GENUINE_COMPRESS_PAIR_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "def compress" in result and 'return b"CMP1" + raw' in result, (
        f"citing decompress must still resolve the real compress sibling, got:\n{result}"
    )


def test_control_genuine_compress_decompress_pair_still_resolves_citing_compress() -> None:
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip is broken",
        file="codec.py",
        line_start=2,
        line_end=3,
        cited_line_text='return b"CMP1" + raw',
        failure_scenario="The round trip of compress/decompress is broken.",
        harm_class="integrity",
        suggested_fix="Fix it.",
    )
    evidence = AuditEvidence(sources={"codec.py": _GENUINE_COMPRESS_PAIR_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "def decompress" in result and "return blob[4:]" in result, (
        f"citing compress must still resolve the real decompress sibling, got:\n{result}"
    )


_GENUINE_SERIALIZE_PAIR_SOURCE = (
    "class Config:\n"
    "    def serialize(self, data):\n"
    "        return str(data)\n"
    "\n"
    "    def deserialize(self, blob):\n"
    "        return eval(blob)\n"
)


def test_control_genuine_serialize_deserialize_pair_still_resolves_citing_deserialize() -> None:
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Serialized output is not the inverse of deserialize",
        file="config.py",
        line_start=6,
        line_end=6,
        cited_line_text="return eval(blob)",
        failure_scenario="The round trip of serialize/deserialize does not agree.",
        harm_class="integrity",
        suggested_fix="Make them agree.",
    )
    evidence = AuditEvidence(sources={"config.py": _GENUINE_SERIALIZE_PAIR_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "def serialize" in result and "return str(data)" in result, (
        f"citing deserialize must still resolve the real serialize sibling, got:\n{result}"
    )


def test_control_genuine_serialize_deserialize_pair_still_resolves_citing_serialize() -> None:
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Serialized output is not the inverse of deserialize",
        file="config.py",
        line_start=3,
        line_end=3,
        cited_line_text="return str(data)",
        failure_scenario="The round trip of serialize/deserialize does not agree.",
        harm_class="integrity",
        suggested_fix="Make them agree.",
    )
    evidence = AuditEvidence(sources={"config.py": _GENUINE_SERIALIZE_PAIR_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "def deserialize" in result and "return eval(blob)" in result, (
        f"citing serialize must still resolve the real deserialize sibling, got:\n{result}"
    )


# --------------------------------------------------------------------------------------
# Adversarial review + QA, independently reproduced against the live code from scratch, same
# defect: `_paired_operation_source` walked every `ast.ClassDef` via `ast.walk(tree)` (which visits
# OUTER classes before classes nested inside their methods) and committed to the FIRST class whose
# direct-child method's range contains the citation line, returning unconditionally -- never
# considering a more deeply nested class that ALSO contains the citation. A citation landing inside
# a locally-defined nested class's method (a real pattern already present in this codebase --
# `core/compute_mode.py`, `core/os_consent_gate.py`, ctypes.Structure subclasses defined inside
# functions) resolved against the OUTER class's own methods instead of the nested class's, handing
# the challenge model a real-but-structurally-unrelated method as "the paired operation" with full
# confidence.
#
# The fix: gather every class whose own direct-child method's range contains the citation, then let
# the NARROWEST such containing method win. This is orthogonal to the term-vocabulary fix (QA round
# 4, `_sibling_pair_terms`) -- the fabricated result reproduced identically whether that matching
# was substring- or token-based, since the defect is about WHICH CLASS gets consulted at all, not
# how names are scored once a class is chosen.
# --------------------------------------------------------------------------------------

_NESTED_CLASS_FIXTURE_SOURCE = (
    "class OuterCodec:\n"
    '    def compress(self, raw): return b"OUTER_COMPRESS_UNRELATED" + raw\n'
    "    def decompress(self, blob):\n"
    "        class InnerCodec:\n"
    '            def compress(self, raw): return b"INNER_REAL_PAIR" + raw\n'
    "            def decompress(self, inner_blob):\n"
    '                if not inner_blob: return b""\n'
    '                return inner_blob[len(b"INNER_REAL_PAIR"):]\n'
    "        return InnerCodec().decompress(blob)\n"
)
# Cited line sits inside InnerCodec.decompress -- the real, correctly-nested method -- computed
# rather than hand-counted so the fixture and the finding cannot silently drift apart.
_NESTED_CLASS_FIXTURE_CITED_LINE = (
    _NESTED_CLASS_FIXTURE_SOURCE.splitlines().index('                if not inner_blob: return b""') + 1
)


def test_citation_inside_a_nested_class_method_resolves_to_the_nested_siblings_not_the_outer_class() -> None:
    """The exact fixture both reviewers independently built: citing the line inside
    `InnerCodec.decompress` must resolve the sibling to `InnerCodec.compress` (the real,
    structurally-correct pair), never `OuterCodec.compress` (a real method, but structurally
    unrelated to the citation -- the pre-fix, first-class-in-walk-order fabrication)."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of empty input is broken",
        file="nested_codec.py",
        line_start=_NESTED_CLASS_FIXTURE_CITED_LINE,
        line_end=_NESTED_CLASS_FIXTURE_CITED_LINE,
        cited_line_text='if not inner_blob: return b""',
        failure_scenario=(
            "The round trip of an empty payload through compress/decompress is broken."
        ),
        harm_class="integrity",
        suggested_fix="Make the empty-input branches agree.",
    )
    evidence = AuditEvidence(sources={"nested_codec.py": _NESTED_CLASS_FIXTURE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_REAL_PAIR" in result, (
        f"must resolve to the real, correctly-nested InnerCodec.compress sibling, got:\n{result}"
    )
    assert "OUTER_COMPRESS_UNRELATED" not in result, (
        "must NOT fabricate OuterCodec.compress as the pair -- it is a real method but structurally "
        f"unrelated to a citation inside InnerCodec.decompress, got:\n{result}"
    )


@_skip_if_liquefy_apache_missing
def test_control_the_non_nested_real_incident_file_still_resolves_correctly() -> None:
    """Control, restated explicitly for this round: the existing real-incident-file test
    (`test_the_real_incident_file_resolves_the_sibling_to_decompress_not_zstd_decompress`, already
    defined and run above) has no nested classes at all and continues to pass unmodified in the
    same run -- the narrowest-containing-method selection changes nothing when there is only ever
    one candidate class per citation. This test does not re-derive that case; it exists so a reader
    of this round's diff sees the non-regression stated as its own claim rather than inferred."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    with open(_LIQUEFY_APACHE_PATH, encoding="utf-8") as handle:
        source = handle.read()
    finding = SteppedFinding(
        title="last_code and last_size are not reset after a raw entry",
        file=_LIQUEFY_APACHE_PATH,
        line_start=80,
        line_end=82,
        cited_line_text="self._flush_run(s_pats, last_pat_id, run_count); last_pat_id = -1; run_count = 0",
        failure_scenario=(
            "After an unmatched raw line, last_code and last_size should be reset to their "
            "defaults before the next matched line."
        ),
        harm_class="integrity",
        suggested_fix="Reset last_code and last_size to 0 inside the raw-line branch of compress.",
    )
    evidence = AuditEvidence(sources={_LIQUEFY_APACHE_PATH: source})

    result = _paired_operation_source(evidence, finding)

    assert "def decompress" in result and "def _zstd_decompress" not in result, (
        f"the simple, non-nested case must still resolve to the real decompress, got:\n{result}"
    )


_OUTER_SCOPE_CITATION_WITH_UNRELATED_NESTED_CLASS_SOURCE = (
    "class Handler:\n"
    "    def read(self, path):\n"
    "        class Helper:\n"
    "            def foo(self):\n"
    "                return 1\n"
    "            def bar(self):\n"
    "                return 2\n"
    "        return self._load(path)\n"
    "    def write(self, path, data):\n"
    "        return self._save(path, data)\n"
)
_OUTER_SCOPE_CITATION_LINE = (
    _OUTER_SCOPE_CITATION_WITH_UNRELATED_NESTED_CLASS_SOURCE.splitlines().index(
        "        return self._load(path)"
    )
    + 1
)


def test_citation_genuinely_in_the_outer_method_still_finds_the_outer_sibling_despite_a_nested_class() -> None:
    """Control the other direction: `Handler.read` has an unrelated nested class (`Helper`) defined
    inside its body, but the CITATION is on `read`'s own `return self._load(path)` line -- outside
    both of Helper's own method ranges. The fix must not overcorrect into always preferring
    nesting: since no nested class's own method actually spans the citation line, `Helper` produces
    no candidate at all, and `read`'s real sibling (`write`) must still be found."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Write is not visible to the paired read",
        file="handler.py",
        line_start=_OUTER_SCOPE_CITATION_LINE,
        line_end=_OUTER_SCOPE_CITATION_LINE,
        cited_line_text="return self._load(path)",
        failure_scenario=(
            "A value written by write is not visible to its paired read afterward."
        ),
        harm_class="integrity",
        suggested_fix="Make write and its paired read agree on the same storage.",
    )
    evidence = AuditEvidence(sources={"handler.py": _OUTER_SCOPE_CITATION_WITH_UNRELATED_NESTED_CLASS_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "def write" in result and "self._save(path, data)" in result, (
        f"the outer method's own real sibling must still be found, got:\n{result}"
    )


_THREE_LEVEL_NESTING_SOURCE = (
    "class Outer:\n"
    "    def process(self, data):\n"
    "        class Middle:\n"
    "            def process(self, data):\n"
    "                class Inner:\n"
    "                    def compress(self, raw):\n"
    '                        return b"INNER_COMPRESS" + raw\n'
    "\n"
    "                    def decompress(self, blob):\n"
    "                        if not blob:\n"
    '                            return b""\n'
    '                        return blob[len(b"INNER_COMPRESS"):]\n'
    "\n"
    "                return Inner().decompress(data)\n"
    "\n"
    "            def compress(self, raw):\n"
    '                return b"MIDDLE_COMPRESS_UNRELATED" + raw\n'
    "\n"
    "        return Middle().process(data)\n"
    "\n"
    "    def compress(self, raw):\n"
    '        return b"OUTER_COMPRESS_UNRELATED" + raw\n'
)
_THREE_LEVEL_NESTING_CITED_LINE = (
    _THREE_LEVEL_NESTING_SOURCE.splitlines().index("                        if not blob:") + 1
)


def test_three_levels_of_nesting_the_narrowest_range_still_wins_at_the_innermost_level() -> None:
    """Deeper than the reviewers' two-level fixture, to confirm 'narrowest range wins' generalizes
    beyond exactly two levels: `Outer.process` contains `Middle` (which contains `Inner`, which
    contains the citation). All three classes are structural candidates (each one's own containing
    method's range contains the citation line), but only `Inner.decompress`'s range is the
    narrowest, so `Inner.compress` -- not `Middle.compress` and not `Outer.compress`, both real but
    unrelated decoys at their own levels -- must be the resolved sibling."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of empty input is broken",
        file="deep.py",
        line_start=_THREE_LEVEL_NESTING_CITED_LINE,
        line_end=_THREE_LEVEL_NESTING_CITED_LINE,
        cited_line_text="if not blob:",
        failure_scenario=(
            "The round trip of an empty payload through compress/decompress is broken."
        ),
        harm_class="integrity",
        suggested_fix="Make the empty-input branches agree.",
    )
    evidence = AuditEvidence(sources={"deep.py": _THREE_LEVEL_NESTING_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_COMPRESS" in result, f"must resolve to the innermost real sibling, got:\n{result}"
    assert "MIDDLE_COMPRESS_UNRELATED" not in result, (
        f"must not fabricate the middle level's decoy, got:\n{result}"
    )
    assert "OUTER_COMPRESS_UNRELATED" not in result, (
        f"must not fabricate the outer level's decoy, got:\n{result}"
    )


# --------------------------------------------------------------------------------------
# Round 6 (this round): round 5's own adversarial AND QA reviewers, independently and using their
# own fresh fixtures, both reproduced a further defect in the SAME containment check round 5 added.
# `ast.FunctionDef.lineno` / `ast.AsyncFunctionDef.lineno` point at the `def`/`async def` line
# itself, never at a decorator line above it. A citation landing exactly on `@staticmethod`,
# `@property`, or any other decorator line therefore falls OUTSIDE a containment test built on the
# bare `lineno` -- even though the decorator is textually part of that method. The nested/inner
# class then contributes NO containment candidate at all for that citation, leaving only whichever
# OUTER class's method happens to textually span the same line (because the nested class sits
# inside that outer method's body) as the sole candidate -- which then wins by default and gets
# handed to the challenge model, with full confidence, as a real-but-structurally-unrelated "paired
# operation".
#
# The fix: every containment check in `_paired_operation_source` now tests against a method's
# EFFECTIVE start -- `min(member.lineno, *(d.lineno for d in member.decorator_list))` -- instead of
# the bare `member.lineno`. This applies both to resolving the CITED method itself (the citation can
# land on the cited method's own decorator line) and to the round-5 narrowest-containing-method
# tiebreak, so a decorated method's true range is never narrower, for this purpose, than its actual
# decorators.
# --------------------------------------------------------------------------------------

_DECORATOR_STATICMETHOD_FIXTURE_SOURCE = (
    "class BlockCipher:\n"
    '    def encode(self, raw): return "OUTER_ENCODE_DECOY"\n'
    "    def decode(self, blob):\n"
    "        class Inner:\n"
    "            @staticmethod\n"
    "            def encode(raw):\n"
    '                return "INNER_ENCODE_BODY"\n'
    "            @staticmethod\n"
    "            def decode(raw):\n"
    '                return "INNER_DECODE_BODY"\n'
    "        return Inner.decode(blob)\n"
)
_DECORATOR_STATICMETHOD_FIXTURE_LINES = _DECORATOR_STATICMETHOD_FIXTURE_SOURCE.splitlines()
# The `@staticmethod` line directly above `Inner.encode` -- ABOVE the `def` line, never on it.
_STATICMETHOD_DECORATOR_CITED_LINE = (
    _DECORATOR_STATICMETHOD_FIXTURE_LINES.index("            @staticmethod") + 1
)
# Control citation: a body line inside that same `Inner.encode`, for the no-regression check below.
_STATICMETHOD_BODY_CITED_LINE = (
    _DECORATOR_STATICMETHOD_FIXTURE_LINES.index('                return "INNER_ENCODE_BODY"') + 1
)


def test_citation_on_a_staticmethod_decorator_line_resolves_into_the_correct_nested_method() -> None:
    """The exact shape both round-5 reviewers independently reproduced against round 5's own fix:
    citing the `@staticmethod` line directly above `Inner.encode` (never the `def` line itself) must
    still resolve `Inner.encode` as the cited method and `Inner.decode` as its real sibling -- not
    `BlockCipher.encode` (a real method, but the OUTER class's decoy, reachable only because
    `BlockCipher.decode`'s own body happens to textually contain the entire nested `Inner` class,
    decorator lines included)."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of an archived blob is broken",
        file="block_cipher.py",
        line_start=_STATICMETHOD_DECORATOR_CITED_LINE,
        line_end=_STATICMETHOD_DECORATOR_CITED_LINE,
        cited_line_text="@staticmethod",
        failure_scenario="The round trip of a blob through encode/decode is broken.",
        harm_class="integrity",
        suggested_fix="Make the encode/decode branches agree.",
    )
    evidence = AuditEvidence(sources={"block_cipher.py": _DECORATOR_STATICMETHOD_FIXTURE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_DECODE_BODY" in result, (
        f"must resolve to the real, correctly-nested Inner.decode sibling, got:\n{result}"
    )
    assert "INNER_ENCODE_BODY" not in result, (
        f"must return the SIBLING (Inner.decode), not the cited method's own body, got:\n{result}"
    )
    assert "OUTER_ENCODE_DECOY" not in result, (
        "must NOT fall through to BlockCipher.encode just because the citation landed on a "
        f"decorator line the bare-lineno containment check could not see, got:\n{result}"
    )


def test_control_citation_on_a_body_line_of_the_same_decorated_method_still_resolves_correctly() -> None:
    """No-regression control for the fix above: citing an ordinary BODY line inside the same
    decorated `Inner.encode` (rather than its decorator line) already worked before this round and
    must keep resolving to the exact same, correct `Inner.decode` sibling."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of an archived blob is broken",
        file="block_cipher.py",
        line_start=_STATICMETHOD_BODY_CITED_LINE,
        line_end=_STATICMETHOD_BODY_CITED_LINE,
        cited_line_text='return "INNER_ENCODE_BODY"',
        failure_scenario="The round trip of a blob through encode/decode is broken.",
        harm_class="integrity",
        suggested_fix="Make the encode/decode branches agree.",
    )
    evidence = AuditEvidence(sources={"block_cipher.py": _DECORATOR_STATICMETHOD_FIXTURE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_DECODE_BODY" in result, (
        f"a body-line citation must still resolve to the real Inner.decode sibling, got:\n{result}"
    )
    assert "OUTER_ENCODE_DECOY" not in result, (
        f"a body-line citation must not regress into the outer decoy either, got:\n{result}"
    )


_DECORATOR_PROPERTY_FIXTURE_SOURCE = (
    "class RemoteStore:\n"
    '    def read(self, key): return "OUTER_READ_DECOY"\n'
    "    def write(self, key, value):\n"
    "        class Cache:\n"
    "            @property\n"
    "            def read(self):\n"
    '                return "INNER_READ_BODY"\n'
    "            def write(self, value):\n"
    '                self._value = "INNER_WRITE_BODY"\n'
    "        return Cache().write(value)\n"
)
_PROPERTY_DECORATOR_CITED_LINE = (
    _DECORATOR_PROPERTY_FIXTURE_SOURCE.splitlines().index("            @property") + 1
)


def test_citation_on_a_property_decorator_line_resolves_into_the_correct_nested_setter() -> None:
    """Same defect, `@property` instead of `@staticmethod`: citing the `@property` line directly
    above `Cache.read` must resolve `Cache.read` as the cited method and `Cache.write` as its real
    sibling -- not `RemoteStore.read` (the OUTER class's decoy, reachable only because
    `RemoteStore.write`'s own body textually contains the entire nested `Cache` class)."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="A cached value is not visible to its paired read",
        file="remote_store.py",
        line_start=_PROPERTY_DECORATOR_CITED_LINE,
        line_end=_PROPERTY_DECORATOR_CITED_LINE,
        cited_line_text="@property",
        failure_scenario="A value written by write is not visible to its paired read afterward.",
        harm_class="integrity",
        suggested_fix="Make write and its paired read agree on the same storage.",
    )
    evidence = AuditEvidence(sources={"remote_store.py": _DECORATOR_PROPERTY_FIXTURE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_WRITE_BODY" in result, (
        f"must resolve to the real, correctly-nested Cache.write sibling, got:\n{result}"
    )
    assert "INNER_READ_BODY" not in result, (
        f"must return the SIBLING (Cache.write), not the cited property's own body, got:\n{result}"
    )
    assert "OUTER_READ_DECOY" not in result, (
        "must NOT fall through to RemoteStore.read just because the citation landed on the "
        f"@property decorator line, got:\n{result}"
    )


_DECORATOR_STACKED_FIXTURE_SOURCE = (
    "class Vault:\n"
    '    def encode(self, raw): return "OUTER_ENCODE_DECOY"\n'
    "    def decode(self, blob):\n"
    "        class Sealed:\n"
    "            @staticmethod\n"
    "            @some_decorator\n"
    "            def encode(raw):\n"
    '                return "INNER_ENCODE_BODY"\n'
    "            @staticmethod\n"
    "            def decode(raw):\n"
    '                return "INNER_DECODE_BODY"\n'
    "        return Sealed.decode(blob)\n"
)
# The TOPMOST of the two stacked decorator lines above `Sealed.encode` -- two lines above the `def`
# line, not one -- so this specifically exercises `min()` over the WHOLE decorator list, not just
# the nearest decorator to the `def` line.
_STACKED_DECORATOR_TOPMOST_CITED_LINE = (
    _DECORATOR_STACKED_FIXTURE_SOURCE.splitlines().index("            @staticmethod") + 1
)


def test_citation_on_the_topmost_of_two_stacked_decorator_lines_still_resolves_correctly() -> None:
    """Two decorators stacked above one `def` (`@staticmethod` then `@some_decorator`, both real
    Python). Citing the TOPMOST decorator line -- two lines above the `def`, not one -- must still
    resolve `Sealed.encode` as the cited method and `Sealed.decode` as its real sibling. A fix that
    only reaches the single NEAREST decorator (e.g. `member.decorator_list[-1].lineno`, rather than
    `min()` over the whole list) would still miss this citation and fall through to the same outer
    decoy `Vault.encode` was built to catch."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="Round trip of a sealed blob is broken",
        file="vault.py",
        line_start=_STACKED_DECORATOR_TOPMOST_CITED_LINE,
        line_end=_STACKED_DECORATOR_TOPMOST_CITED_LINE,
        cited_line_text="@staticmethod",
        failure_scenario="The round trip of a blob through encode/decode is broken.",
        harm_class="integrity",
        suggested_fix="Make the encode/decode branches agree.",
    )
    evidence = AuditEvidence(sources={"vault.py": _DECORATOR_STACKED_FIXTURE_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "INNER_DECODE_BODY" in result, (
        f"must resolve to the real, correctly-nested Sealed.decode sibling, got:\n{result}"
    )
    assert "INNER_ENCODE_BODY" not in result, (
        f"must return the SIBLING (Sealed.decode), not the cited method's own body, got:\n{result}"
    )
    assert "OUTER_ENCODE_DECOY" not in result, (
        "must NOT fall through to Vault.encode just because the citation landed two lines above "
        f"the `def` line, on the topmost of two stacked decorators, got:\n{result}"
    )


# --------------------------------------------------------------------------------------
# Round 7 (adversarial review, one step past round 6): round 6 applied `_effective_start_line`
# everywhere a method's range is tested for CONTAINMENT, but missed the one place a method's range
# is used for TEXT EXTRACTION -- `start = max(1, int(sibling.lineno))`, building the numbered
# snippet handed to the challenge model. When the RESOLVED SIBLING (not the cited method) itself
# carries a decorator, `int(sibling.lineno)` still points at its `def` line, so the sibling's own
# `@staticmethod`/`@property`/etc. line is silently dropped from "the paired operation, for
# comparison". This needs no nesting and no decorator anywhere near the CITATION -- a flat,
# non-nested class with a plain body-line citation on an undecorated method is enough to reproduce
# it, since the defect is entirely about the SIBLING's own line range, not the cited method's.
#
# The fix: `start = max(1, _effective_start_line(sibling))`, reusing the same helper round 6
# already built rather than adding a second one.
# --------------------------------------------------------------------------------------

_DECORATED_SIBLING_EXTRACTION_SOURCE = (
    "class SignalConduit:\n"
    "    @staticmethod\n"
    "    def write(raw):\n"
    '        return "WRITE_BODY"\n'
    "    def read(self, blob):\n"
    '        return "READ_BODY"\n'
)
_DECORATED_SIBLING_EXTRACTION_CITED_LINE = (
    _DECORATED_SIBLING_EXTRACTION_SOURCE.splitlines().index('        return "READ_BODY"') + 1
)


def test_a_decorated_siblings_own_decorator_line_survives_source_extraction() -> None:
    """Adversarial review, round 7: citing `read`'s own body line (no decorator anywhere near the
    citation, no nesting at all) resolves the sibling to `write` -- but `write` is itself decorated
    with `@staticmethod`. The extracted "full numbered source" must include that `@staticmethod`
    line, not just `def write(raw):` onward, since a decorator changes calling convention/semantics
    and the challenge prompt explicitly hands this snippet over as the paired operation to reason
    about."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    finding = SteppedFinding(
        title="A written value is not visible to its paired read",
        file="signal_conduit.py",
        line_start=_DECORATED_SIBLING_EXTRACTION_CITED_LINE,
        line_end=_DECORATED_SIBLING_EXTRACTION_CITED_LINE,
        cited_line_text='return "READ_BODY"',
        failure_scenario="A value written is not visible to its paired read afterward.",
        harm_class="integrity",
        suggested_fix="Make write and its paired read agree on the same storage.",
    )
    evidence = AuditEvidence(sources={"signal_conduit.py": _DECORATED_SIBLING_EXTRACTION_SOURCE})

    result = _paired_operation_source(evidence, finding)

    assert "WRITE_BODY" in result, f"must resolve to the real write sibling, got:\n{result}"
    assert "@staticmethod" in result, (
        "the resolved sibling's own decorator line must survive extraction -- dropping it hides a "
        f"calling-convention detail from the exact prompt this mechanism exists to inform, got:\n{result}"
    )
    lines = result.splitlines()
    assert lines[0].strip().endswith("@staticmethod"), (
        f"the decorator must be the FIRST line of the extracted snippet, not merely present "
        f"somewhere in it, got:\n{result}"
    )


def test_control_an_undecorated_siblings_extraction_is_unaffected() -> None:
    """No-regression control: when the resolved sibling has no decorator at all, extraction starts
    at its bare `def` line exactly as before -- this fix only ever extends the range, never shifts
    it for the ordinary case."""
    from core.agent_runtime.stepped_audit import _paired_operation_source

    source = (
        "class PlainConduit:\n"
        "    def write(self, raw):\n"
        '        return "WRITE_BODY"\n'
        "    def read(self, blob):\n"
        '        return "READ_BODY"\n'
    )
    cited_line = source.splitlines().index('        return "READ_BODY"') + 1
    finding = SteppedFinding(
        title="A written value is not visible to its paired read",
        file="plain_conduit.py",
        line_start=cited_line,
        line_end=cited_line,
        cited_line_text='return "READ_BODY"',
        failure_scenario="A value written is not visible to its paired read afterward.",
        harm_class="integrity",
        suggested_fix="Make write and its paired read agree on the same storage.",
    )
    evidence = AuditEvidence(sources={"plain_conduit.py": source})

    result = _paired_operation_source(evidence, finding)

    first_line = result.splitlines()[0]
    assert first_line.split(": ", 1)[1].strip() == "def write(self, raw):", (
        f"an undecorated sibling's extraction must start exactly at its `def` line, got:\n{result}"
    )
