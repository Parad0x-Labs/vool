"""A span means something only against the text it was measured on. Proof 9.

Two integers resolve against any string long enough, so the failure this suite pins is silent by
nature: a span computed on the raw message and resolved on the normalized one returns a plausible
wrong substring rather than an error. Every case here is a way for two renderings of "the same"
message to disagree about offsets -- combining accents, astral-plane emoji, a normalization rewrite,
the same entity twice -- and every case must end in `SpanBindingError`, never in a substring.
"""
from __future__ import annotations

import unicodedata

import pytest

from core.semantic.canonical_text import (
    CANONICAL_REPRESENTATION,
    CanonicalText,
    CanonicalTextError,
    EntitySpan,
    SpanBindingError,
)

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)


def test_offsets_are_code_points_not_utf8_bytes() -> None:
    # "é" is 1 code point and 2 UTF-8 bytes; "👋" is 1 code point and 4 UTF-8 bytes. A byte-indexed
    # span would put the end of "café" at 5 and of the emoji at 4 -- both wrong for str slicing.
    canonical = CanonicalText.of("café 👋")
    assert canonical.length == 6, "length must count code points, not bytes"
    # 3 ASCII + 2 for "é" + 1 space + 4 for the emoji. Six code points, ten bytes -- the gap a
    # byte-indexed span would fall into.
    assert len(canonical.text.encode("utf-8")) == 10, "the byte length differs, which is the whole point"

    accent = canonical.find("café")
    assert accent is not None
    assert (accent.start, accent.end) == (0, 4)
    assert accent.resolve(canonical) == "café"

    wave = canonical.find("👋")
    assert wave is not None
    assert (wave.start, wave.end) == (5, 6), "an astral-plane emoji is ONE code point"
    assert wave.resolve(canonical) == "👋"


def test_an_astral_emoji_span_is_not_split_by_its_own_offsets() -> None:
    canonical = CanonicalText.of("ship 🚀 now")
    rocket = canonical.find("🚀")
    assert rocket is not None
    assert canonical.text[rocket.start : rocket.end] == "🚀"
    assert rocket.resolve(canonical) == canonical.text[rocket.start : rocket.end]


def test_decomposed_and_precomposed_accents_normalize_to_one_representation() -> None:
    # The same word from two keyboards: NFC "é" (1 code point) vs NFD "e" + U+0301 (2).
    precomposed = "Zürich café"
    decomposed = unicodedata.normalize("NFD", precomposed)
    assert precomposed != decomposed, "the two forms must genuinely differ before normalization"
    assert len(decomposed) > len(precomposed)

    left = CanonicalText.of(precomposed)
    right = CanonicalText.of(decomposed)
    assert left.digest == right.digest, "NFC is the declared form, so both inputs land on one text"
    assert left.length == right.length

    span = left.find("café")
    assert span is not None
    # Because both normalized to the same canonical text, a span from one binds against the other.
    assert span.resolve(right) == "café"


def test_a_span_refuses_to_resolve_against_a_different_representation() -> None:
    canonical = CanonicalText.of("weather in Tallinn")
    span = canonical.find("Tallinn")
    assert span is not None
    other = CanonicalText.of("weather in Tallinn", representation="user_text.nfd.v1")
    with pytest.raises(SpanBindingError) as excinfo:
        span.resolve(other)
    assert "user_text.nfc.v1" in str(excinfo.value)
    assert "user_text.nfd.v1" in str(excinfo.value)


def test_a_representation_cannot_claim_a_normal_form_it_does_not_apply() -> None:
    """A hostile-review finding. The representation name is the single field a span consults to
    decide whether it may bind, so a name that misdescribes the form makes every span over that text
    wrong in a way nothing can detect. It used to hard-code NFC while honouring any name given."""
    decomposed = CanonicalText.of("café", representation="user_text.nfd.v1")
    assert decomposed.text == unicodedata.normalize("NFD", "café")
    assert decomposed.length == 5, "NFD splits the accent, so the text really is longer"

    precomposed = CanonicalText.of("café")
    assert precomposed.length == 4
    assert precomposed.digest != decomposed.digest, "two forms, two identities"


def test_an_unknown_representation_is_refused_rather_than_served_under_the_default() -> None:
    with pytest.raises(CanonicalTextError, match="unknown representation"):
        CanonicalText.of("x", representation="normalized_text.v9")


def test_a_lone_surrogate_is_a_typed_refusal_not_a_codec_error() -> None:
    """`"\ud800"` is a valid `str` and invalid UTF-8. It used to escape as a raw UnicodeEncodeError
    from inside the digest -- a hostile input producing a codec error three frames down instead of a
    typed refusal from this module."""
    for hostile in ("\ud800", "abc\udfff", "\ud83d"):  # lone high, trailing low, truncated pair
        with pytest.raises(CanonicalTextError, match="UTF-8"):
            CanonicalText.of(hostile)


def test_overlaps_respects_the_complete_binding_identity() -> None:
    """Representation and digest were compared; text_length was not, so two spans claiming texts of
    different lengths compared as comparable."""
    left = EntitySpan(0, 3, "user_text.nfc.v1", "deadbeefdeadbeef", 10)
    right = EntitySpan(1, 4, "user_text.nfc.v1", "deadbeefdeadbeef", 999)
    assert left.binding != right.binding
    assert left.overlaps(right) is False
    same = EntitySpan(1, 4, "user_text.nfc.v1", "deadbeefdeadbeef", 10)
    assert left.overlaps(same) is True


def test_a_normalization_rewrite_invalidates_the_span_instead_of_shifting_it() -> None:
    # The runtime collapses whitespace on the way in. A span measured before that rewrite points
    # somewhere else afterwards -- here, four code points earlier.
    typed = "weather   in    Tallinn"
    raw = CanonicalText.of(typed)
    span = raw.find("Tallinn")
    assert span is not None
    assert span.resolve(raw) == "Tallinn"

    # The same rewrite `core.input_normalizer` performs on the way in.
    collapsed = CanonicalText.of(" ".join(typed.split()))
    assert collapsed.text == "weather in Tallinn"
    assert collapsed.length < raw.length, "the rewrite must actually move the offsets"
    with pytest.raises(SpanBindingError):
        span.resolve(collapsed)


def test_same_length_but_different_text_is_caught_by_the_digest() -> None:
    # The subtle one. "café" -> "cafe" keeps the code-point count identical, so a length check alone
    # passes and the span resolves to a real-looking wrong substring.
    left = CanonicalText.of("meet at the café now")
    right = CanonicalText.of("meet at the cafe now")
    assert left.length == right.length, "the lengths must match or this test proves nothing"
    span = left.find("café")
    assert span is not None
    with pytest.raises(SpanBindingError) as excinfo:
        span.resolve(right)
    assert "same length, different text" in str(excinfo.value)


def test_a_repeated_entity_produces_distinct_spans_that_both_resolve() -> None:
    canonical = CanonicalText.of("weather in Paris and Paris")
    spans = canonical.find_all("Paris")
    assert len(spans) == 2, "a repeat is two references, not one deduplicated one"
    assert spans[0] != spans[1]
    assert (spans[0].start, spans[1].start) == (11, 21)
    assert [span.resolve(canonical) for span in spans] == ["Paris", "Paris"]
    assert not spans[0].overlaps(spans[1])


def test_find_all_terminates_on_an_adjacent_repeat() -> None:
    canonical = CanonicalText.of("abababab")
    spans = canonical.find_all("ab")
    assert len(spans) == 4
    assert [span.start for span in spans] == [0, 2, 4, 6]


def test_spans_over_different_texts_never_report_overlap() -> None:
    left = CanonicalText.of("Tallinn and Riga")
    right = CanonicalText.of("Vilnius and Kaunas")
    left_span = left.find("Tallinn")
    right_span = right.find("Vilnius")
    assert left_span is not None and right_span is not None
    # Identical integer ranges, different messages. Comparing them as if they were comparable is how
    # one turn's entity gets attributed to another.
    assert (left_span.start, left_span.end) == (right_span.start, right_span.end)
    assert not left_span.overlaps(right_span)


def test_an_out_of_range_span_is_refused_rather_than_clamped() -> None:
    canonical = CanonicalText.of("short")
    span = EntitySpan(
        start=0,
        end=99,
        representation=canonical.representation,
        text_digest=canonical.digest,
        text_length=canonical.length,
    )
    # Python would silently return "short" for text[0:99]. That is the clamp this refuses.
    assert canonical.text[0:99] == "short"
    with pytest.raises(SpanBindingError):
        span.resolve(canonical)


def test_a_span_round_trips_through_a_receipt_row() -> None:
    canonical = CanonicalText.of("weather in Tallinn 🌧")
    span = canonical.find("Tallinn", kind="location")
    assert span is not None
    restored = EntitySpan.from_dict(span.to_dict())
    assert restored == span
    assert restored is not None and restored.resolve(canonical) == "Tallinn"


@pytest.mark.parametrize("payload", [None, {}, {"start": 0}, {"start": "x", "end": 1}, "not a dict"])
def test_an_unreadable_receipt_row_degrades_to_none_rather_than_raising(payload: object) -> None:
    # This reads persisted data written by an older build. An unparseable row means "that span is
    # unreadable", not "take the reader down".
    assert EntitySpan.from_dict(payload) is None


def test_the_canonical_identity_never_carries_the_message_text() -> None:
    canonical = CanonicalText.of("my password is hunter2 and my address is 12 Elm St")
    identity = canonical.to_dict()
    assert set(identity) == {"representation", "digest", "length"}
    assert "hunter2" not in str(identity)
    assert identity["representation"] == CANONICAL_REPRESENTATION


def test_a_search_speaks_the_representation_it_is_searching() -> None:
    """A hostile review searched an NFD haystack for an NFC needle and got None.

    The needle was folded to a hard-coded NFC while the haystack stayed decomposed, so the two could
    never match. Every haystack/needle form combination must find the word, and the offsets must
    describe the form actually stored -- NFD is genuinely longer, and the span says so.
    """
    nfd = CanonicalText.of("café", representation="user_text.nfd.v1")
    nfc = CanonicalText.of("café")
    decomposed_needle = unicodedata.normalize("NFD", "café")

    for haystack, needle, expected_end in (
        (nfc, "café", 4),
        (nfc, decomposed_needle, 4),
        (nfd, "café", 5),
        (nfd, decomposed_needle, 5),
    ):
        span = haystack.find(needle)
        assert span is not None, f"{haystack.representation} could not find {needle!r}"
        assert span.end == expected_end, "offsets must describe the stored form, not the needle's"
        assert span.resolve(haystack) == haystack.text

    # Combining marks, emoji and repeats under the decomposed form too.
    combining = CanonicalText.of("noël noël", representation="user_text.nfd.v1")
    assert len(combining.find_all("noël")) == 2
    emoji = CanonicalText.of("a 👨‍👩‍👧 b", representation="user_text.nfd.v1")
    assert emoji.find("👨‍👩‍👧") is not None


def test_the_public_constructor_cannot_be_handed_a_lie() -> None:
    """`of()` being safe was not enough -- the dataclass constructor is public too.

    A hostile review went around `of()` and built a `CanonicalText` holding NFC text labelled
    `user_text.nfd.v1`, a representation name this module has never heard of, a lone surrogate, and
    a digest belonging to some other string. Every span measured against such a value is wrong in
    the one field spans consult to decide whether they may bind.
    """
    import hashlib

    from core.semantic import canonical_text as module

    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd

    with pytest.raises(CanonicalTextError, match="not in NFD"):
        CanonicalText(representation="user_text.nfd.v1", text=nfc, digest=module._digest(nfc))
    with pytest.raises(CanonicalTextError, match="not in NFC"):
        CanonicalText(representation="user_text.nfc.v1", text=nfd, digest=module._digest(nfd))
    with pytest.raises(CanonicalTextError, match="unknown representation"):
        CanonicalText(representation="totally.invented.v9", text=nfc, digest=module._digest(nfc))
    with pytest.raises(CanonicalTextError, match="UTF-8"):
        CanonicalText(representation="user_text.nfc.v1", text="\ud800", digest="0" * 32)
    with pytest.raises(CanonicalTextError, match="digest does not describe"):
        CanonicalText(representation="user_text.nfc.v1", text=nfc,
                      digest=hashlib.sha256(b"some other string").hexdigest()[:32])

    # Controls: every construction that tells the truth is accepted, including the declared
    # no-normalization escape hatch, and `of()` is unchanged.
    assert CanonicalText(representation="user_text.nfc.v1", text=nfc, digest=module._digest(nfc)).length == 4
    assert CanonicalText(representation="user_text.nfd.v1", text=nfd, digest=module._digest(nfd)).length == 5
    assert CanonicalText(representation="user_text.raw.v1", text=nfd, digest=module._digest(nfd)).length == 5
    assert CanonicalText.of("café").length == 4
    assert CanonicalText.of("café", representation="user_text.nfd.v1").length == 5
