"""SCALPEL fixture repair, failure class G, 2026-08-06: producer/consumer domain analysis, applied
to the real `AdaptiveSearchIndex` / `grep()` in the sibling evidence workspace.

Read-only against `/Users/example-user/Desktop/openclaw-skills` -- that workspace is evidence,
never modified. This drives the REAL, unmodified `LiquefyApacheRepetitionV1`/`AdaptiveSearchIndex`
classes, imported by path, and does not assume the outcome: it builds a real Apache-log corpus,
forces the CUSTOM indexed format (not the raw-Zstd fallback the compressor prefers whenever raw
compression alone already clears its 12x threshold), and compares the public `grep()` against a
reference full-decompress-and-substring-scan for both the fields the index actually represents
(IP, request, referrer, user agent — the only fields `unique_tokens.add(...)` ever sees) and fields
its own producer never gave it (timestamp, status code, response size — stored as delta-encoded
varints, never as searchable text).

Skipped, not failed, if the sibling evidence workspace is not present on this machine (a fresh
checkout without the sibling fixture) or the `zstandard` package is unavailable in this venv --
both make this test meaningless to run, not a code failure to report.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import sys

import pytest

from core.agent_runtime.candidate_sanity import ProducerConsumerAnalysis

_SIBLING_API = "/Users/example-user/Desktop/openclaw-skills/api"
_SIBLING_APACHE = "/Users/example-user/Desktop/openclaw-skills/api/apache"


def _load_codec():
    if _SIBLING_API not in sys.path:
        sys.path.insert(0, _SIBLING_API)
    if _SIBLING_APACHE not in sys.path:
        sys.path.insert(0, _SIBLING_APACHE)
    try:
        from liquefy_apache_repetition_v1 import LiquefyApacheRepetitionV1
    except ImportError:
        pytest.skip("sibling evidence workspace or its zstandard dependency is not available here")
    return LiquefyApacheRepetitionV1()


def _token(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:24]


def _build_forced_custom_format_blob(codec):
    """A real Apache-log corpus, compressed through the REAL, unmodified compressor, with the
    custom indexed format forced rather than assumed.

    `compress()` always tries raw zstd first and returns it immediately whenever it alone already
    reaches a 12x ratio (`liquefy_apache_repetition_v1.py`'s own early-exit) -- true for most small
    synthetic corpora, which would silently test the WRONG code path (the raw-Zstd fallback grep()
    branch, not the indexed one this analysis is about). The codec's own `raw_cctx` is wrapped for
    this one call only, inflating what THAT comparison sees so the size-comparison at the end of
    `compress()` picks the custom candidate — the real custom-format construction and the real
    `AdaptiveSearchIndex` still run unmodified; only the raw-path early exit is denied a false win.
    """
    lines = []
    for i in range(80):
        ip = f"10.{i % 255}.{(i * 3) % 255}.{(i * 7) % 255}"
        req = f"GET /resource/{_token(f'req{i}')} HTTP/1.1"
        ref = f"https://example.com/{_token(f'ref{i}')}"
        ua = f"Agent/{_token(f'ua{i}')}"
        ts = f"06/Aug/2026:12:{i % 60:02d}:{(i * 7) % 60:02d} +0000"
        status = 200 + (i % 5)
        size = 1000 + i * 13
        lines.append(f'{ip} - - [{ts}] "{req}" {status} {size} "{ref}" "{ua}"\r\n'.encode("latin-1"))
    raw = b"".join(lines)

    real_raw_compress = codec.raw_cctx.compress

    class _DenyRawEarlyExit:
        def compress(self, data):
            real = real_raw_compress(data)
            return real + b"\x00" * (len(real) * 20)

    codec.raw_cctx = _DenyRawEarlyExit()
    blob = codec.compress(raw)
    return raw, blob


def _grep_output(codec, blob: bytes, query: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        codec.grep(blob, query)
    return buf.getvalue()


def _grep_reports_found(output: str) -> bool:
    return "NOT FOUND" not in output


@pytest.fixture(scope="module")
def forced_corpus():
    codec = _load_codec()
    raw, blob = _build_forced_custom_format_blob(codec)
    if not blob.startswith(b"UNI\x01"):
        pytest.skip("could not force the custom indexed format on this build of the codec")
    reference = codec.decompress(blob)
    assert reference == raw, "the codec's own round-trip is broken on this synthetic corpus"
    return codec, raw, blob


def test_the_real_writer_round_trips_this_corpus_exactly(forced_corpus):
    """Control: the corpus itself is valid input the real writer produces and the real reader
    reconstructs byte-for-byte -- any mismatch found below is about grep()'s index shortcut, not
    about a malformed or synthetic blob (the exact distinction failure class E asked for)."""
    codec, raw, blob = forced_corpus
    assert codec.decompress(blob) == raw


@pytest.mark.parametrize(
    "field",
    ["ip", "request", "referrer", "user_agent"],
)
def test_indexed_fields_are_found_by_grep_when_queried_by_full_value(forced_corpus, field):
    """The fields `AdaptiveSearchIndex`'s producer actually represents (`unique_tokens.add(ip)`,
    `.add(req)`, `.add(ref)`, `.add(ua)` — the whole field value, not a substring of it)."""
    codec, raw, blob = forced_corpus
    i = 7
    values = {
        "ip": f"10.{i % 255}.{(i * 3) % 255}.{(i * 7) % 255}",
        "request": f"GET /resource/{_token(f'req{i}')} HTTP/1.1",
        "referrer": f"https://example.com/{_token(f'ref{i}')}",
        "user_agent": f"Agent/{_token(f'ua{i}')}",
    }
    query = values[field]
    reference_hit = query.encode("latin-1") in raw
    grep_hit = _grep_reports_found(_grep_output(codec, blob, query))

    assert reference_hit, "test corpus construction bug: the query isn't even in the raw data"
    assert grep_hit == reference_hit, (
        f"grep() disagreed with the full reference scan for an INDEXED field ({field}): "
        f"reference={reference_hit} grep={grep_hit}"
    )


@pytest.mark.parametrize(
    ("field", "confidence"),
    [("timestamp", "asserted"), ("status_code", "asserted"), ("response_size", "asserted")],
)
def test_omitted_domain_fields_are_a_confirmed_producer_consumer_mismatch(forced_corpus, field, confidence):
    """The fields the producer's own token set never sees at all: timestamp/status/size are stored
    as delta-encoded varints in `s_code`/`s_size`/pattern columns, never passed to `idx.add(...)`.
    Confirmed, not assumed: a real query that IS present in the real reference decompression must
    be reported ABSENT by grep()'s fast-skip for this to count as the genuine mismatch this test
    exists to prove — if the real behavior ever changed to fall back and find it anyway, this
    assertion would correctly fail rather than silently agree with a claim that stopped being true.
    """
    codec, raw, blob = forced_corpus
    i = 7
    values = {
        "timestamp": f"12:{i % 60:02d}:{(i * 7) % 60:02d}",
        "status_code": str(200 + (i % 5)),
        "response_size": str(1000 + i * 13),
    }
    query = values[field]
    reference_hit = query.encode("latin-1") in raw
    grep_output = _grep_output(codec, blob, query)
    grep_hit = _grep_reports_found(grep_output)

    assert reference_hit, "test corpus construction bug: the query isn't even in the raw data"
    assert not grep_hit, (
        f"expected the confirmed mismatch (grep() fast-skips a field the index never represents) "
        f"but grep() actually found it this time -- the claim needs re-verifying, not asserting: "
        f"{grep_output!r}"
    )
    assert "FAST SKIP" in grep_output, (
        "the negative result must be the index's own fast-skip, not some other code path"
    )


def test_the_mismatch_is_structured_as_a_producer_consumer_analysis(forced_corpus):
    """The reusable structure this finding is reported through — not prose that may or may not
    cover all six questions the operator's instruction asks for."""
    analysis = ProducerConsumerAnalysis(
        producer="AdaptiveSearchIndex.add() calls in LiquefyApacheRepetitionV1.compress()",
        represented_domain="whole-value tokens of ip, request, referrer, and user-agent from "
        "regex-matched Apache log lines only",
        omitted_domain="timestamp, status code, response size (stored as delta-encoded varints, "
        "never tokenized), substrings of any indexed field, and any non-matching raw line content",
        consumer="LiquefyApacheRepetitionV1.grep()",
        consumer_assumption="a negative AdaptiveSearchIndex.maybe_has() result means the query "
        "does not appear anywhere in the decompressed content",
        absence_is_authoritative=True,
        affected_public_operation="grep(blob, query) -- silently under-reports matches for any "
        "query outside the indexed domain, printing 'NOT FOUND (FAST SKIP)' with no fallback scan",
    )
    assert analysis.is_a_genuine_mismatch
    codec, _raw, blob = forced_corpus
    grep_output = _grep_output(codec, blob, "12:07:49")
    assert "FAST SKIP" in grep_output, "the analysis's own claim must match what the code just did"


def test_producer_consumer_analysis_is_not_a_mismatch_when_absence_is_advisory() -> None:
    """A consumer that falls back to a full check on a negative result is not the defect shape --
    an incomplete producer is only a problem when the consumer trusts its silence as proof."""
    analysis = ProducerConsumerAnalysis(
        producer="a Bloom filter that indexes only field A",
        represented_domain="field A",
        omitted_domain="field B",
        consumer="a search function",
        consumer_assumption="a negative result means 'probably not present, verify with a full scan'",
        absence_is_authoritative=False,
        affected_public_operation="search()",
    )
    assert not analysis.is_a_genuine_mismatch


def test_producer_consumer_analysis_is_not_a_mismatch_when_nothing_is_omitted() -> None:
    """A complete producer (nothing omitted) cannot produce this defect shape, no matter how the
    consumer treats a negative result -- there is nothing for the consumer to miss."""
    analysis = ProducerConsumerAnalysis(
        producer="a complete index",
        represented_domain="everything",
        omitted_domain="",
        consumer="a search function",
        consumer_assumption="a negative result is final",
        absence_is_authoritative=True,
        affected_public_operation="search()",
    )
    assert not analysis.is_a_genuine_mismatch


def test_sabotage_treating_every_omission_as_a_mismatch_regardless_of_authority_is_caught() -> None:
    """Reverts ONLY the `absence_is_authoritative` half of the check -- flags every producer with
    ANY omitted domain as a mismatch, even when the consumer treats it advisorily -- and proves
    that reproduces a false positive on the exact advisory-fallback shape; then proves the real
    property differs from it."""
    advisory = ProducerConsumerAnalysis(
        producer="a Bloom filter that indexes only field A",
        represented_domain="field A",
        omitted_domain="field B",
        consumer="a search function",
        consumer_assumption="a negative result means 'probably not present, verify with a full scan'",
        absence_is_authoritative=False,
        affected_public_operation="search()",
    )

    def _sabotaged_is_mismatch(a: ProducerConsumerAnalysis) -> bool:
        return bool(a.omitted_domain.strip())  # SABOTAGE: ignores absence_is_authoritative entirely

    assert _sabotaged_is_mismatch(advisory) is True, "sabotage setup failed to reproduce the false positive"
    assert advisory.is_a_genuine_mismatch is False, (
        "the real property must not flag an advisory (fallback-checked) omission as a mismatch"
    )
