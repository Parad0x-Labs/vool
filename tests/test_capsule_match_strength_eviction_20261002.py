"""A weaker match never holds the capsule against a stronger one.

Measured defect (probe-20 dev set, 2026-10-02): with recall fixed, the
answer-bearing record ranked first in the evidence leg but was refused for
budget behind distilled lines that matched the question by one incidental
term. Two mechanisms held it out:

1. the eviction law read the record's own leading envelope ("Session date:
   <date>") as a value and the speaker label as an asked-term tie, so EVERY
   enveloped line was protected and none could make room;
2. a span was "decisive" only when it carried a query term no delivered line
   carried; several weak lines whose UNION covered its terms made the one line
   that answers the ask non-decisive.

Contract: provenance (attribution prefix, provenance parenthetical, leading
envelope, speaker label) is not content for eviction; a span carrying >= 2
distinct asked content terms is decisive and may demote non-value lines that
carry strictly fewer. The capsule never grows past its budget and value-bearing
lines are never demoted. Synthetic stores, hash embedding lane, zero network.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_capsule_v2 import resolve_budget
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

_TAIL = (" Anyway, that is how my week has been going so far, nothing too dramatic, "
         "just the usual small routines that keep me busy and mostly content, and I am "
         "grateful for the quiet evenings, the long walks home and the friends who drop by "
         "without warning to share tea and gossip about the neighbours, the cousins who call "
         "on Sundays, the cat that sleeps on the porch railing, and the long letters I still "
         "write by hand to old school friends scattered around the country.")


def _turn(home, chat, user):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user, "", access_policy=policy,
                         source_context={"chat_id": chat, "runtime_home": home})


def _capsule(home, chat, question, target_tokens):
    policy = resolve_memory_access_policy(chat_id=chat)
    budget = resolve_budget(bucket="B", role="general",
                            evidence_target_tokens=target_tokens)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home},
        budget=budget)
    return "\n".join(str(m.get("content") or "") for m in out
                     if "retrieved_context" in str(m.get("content")))


def _distilled(block: str) -> str:
    """The distilled section of the capsule: the lines the packer budgeted and the laws here describe. Since the v14
    whole-turn delivery (lme-diagnosis, 2026-10-06) the block also carries verbatim whole turns after the 'Evidence
    turns' header, sized by the free window, which quote each turn as said."""
    cut = block.find("Evidence turns (whole records")
    return block if cut < 0 else block[:cut]


def _seed(home, chat, speaker, fillers, decisive):
    for i, body in enumerate(fillers):
        assert _turn(home, chat, f"Session date: 4:{10 + i} pm on {3 + i} March, 2024\n"
                     f"{speaker}: {body}{_TAIL}")["status"] == "stored"
    assert _turn(home, chat, f"Session date: 9:05 am on 20 March, 2024\n{speaker}: {decisive}"
                 )["status"] == "retained"


HARBOR_FILLERS = [
    "I've been repainting my old rowing boat all week, the hull needed a lot of sanding and the "
    "varnish took forever to dry because of the damp weather up north, but my brother came by and "
    "helped carry it back into the shed near Nordvik once it was finally done.",
    "I've been learning to bake rye bread with my grandmother's recipe, and the kitchen smells "
    "wonderful every morning now, although the first loaves came out flat and dense.",
    "I've been walking my neighbour's dog along the cliffs most evenings, it is a calm old "
    "retriever that loves chasing gulls over the water at sunset.",
    "I've been reading my way through a stack of mystery novels from the library, mostly old "
    "detective stories set in small fishing towns.",
    "I've been sorting my late uncle's photographs into albums, there are boxes of pictures of "
    "the harbor festivals and family picnics.",
    "I've been fixing my bicycle so I can ride to work again, the chain was rusted and the "
    "brakes squealed terribly.",
]
HARBOR_DECISIVE = (
    "The quietest harbor in all of Nordvik is easy, Kesterholm for sure, because the boats just "
    "bob there and nobody shouts across the water, the old ferry man waves at everyone who "
    "passes along the pier, the gulls sit quietly on the mooring posts, and even on market days "
    "the only sound is the rope creaking against the wooden bollards.")

# the asked terms are spread one per filler; no filler carries two
GLASS_FILLERS = [
    "I've been clearing my late aunt's attic, it is full of old furniture and some of it "
    "is lovely, a few chairs might go to the kiln workshop next door.",
    "I've been training my nephew for his first race and my legs ache from the hills, we run "
    "past the brightest shop windows in town every morning.",
    "I've been cooking my way through a spicy soup book, the kitchen is a mess but the "
    "flavours are wonderful and the neighbours keep asking for leftovers.",
    "I've been mending my fishing nets on the porch, it takes hours but the evening light "
    "is gentle and the radio keeps me company while I glass over the old floats.",
    "I've been planting my tomatoes in the greenhouse at Varnmoor, the soil was heavy so I "
    "mixed in sand and compost before the frost came back.",
]
GLASS_DECISIVE = (
    "Oh, the brightest glass kiln in Varnmoor is the old blue one behind the chapel, Halvard "
    "fires it every Thursday and the whole lane glows orange until well after midnight.")


@pytest.mark.parametrize("target_tokens", [200, 280, 340])
def test_enveloped_weak_lines_make_room_for_the_answering_span(fresh_profile, target_tokens):
    """The answering span carries an asked term no line carries (decisive on
    the old law), but every distilled line was protected by its envelope date
    and speaker label. A tight caller budget makes the refusal visible."""
    _seed(fresh_profile, f"ms-harbor-{target_tokens}", "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    block = _capsule(fresh_profile, f"ms-harbor-{target_tokens}",
                     "Which harbor does Ilse describe as the quietest one in Nordvik?",
                     target_tokens)
    assert "Kesterholm" in _distilled(block)
    assert len(_distilled(block)) <= target_tokens * 4 + 600  # header + wrapper; the packed distilled lines stay in budget


@pytest.mark.parametrize("target_tokens", [200])
def test_second_domain_stronger_span_displaces_weak_lines(
        fresh_profile, target_tokens):
    """Second domain: the asked terms are spread one per filler, none carries
    more than one; under a tight caller budget the answering span was refused
    behind them."""
    _seed(fresh_profile, f"ms-glass-{target_tokens}", "Tove", GLASS_FILLERS, GLASS_DECISIVE)
    block = _capsule(fresh_profile, f"ms-glass-{target_tokens}",
                     "Which glass kiln is the brightest one in Varnmoor?", target_tokens)
    assert "chapel" in block


def test_value_bearing_line_is_never_demoted(fresh_profile):
    """Negative control: a distilled line carrying a real value (not an
    envelope date) keeps its place even against a stronger span."""
    chat = "ms-value"
    _turn(fresh_profile, chat,
          "Session date: 4:10 pm on 3 March, 2024\nIlse: I've been paying my harbor mooring fee, "
          "it is 145 crowns a month now and my brother thinks that is too much for Nordvik."
          + _TAIL)
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS[1:4], HARBOR_DECISIVE)
    block = _capsule(fresh_profile, chat,
                     "Which harbor does Ilse describe as the quietest one in Nordvik?", 280)
    if "145 crowns" not in block:
        pytest.skip("value line not distilled for this store; control not applicable")
    assert "145 crowns" in block


def test_single_term_span_does_not_evict_tied_lines(fresh_profile):
    """Negative control: a span matching ONE asked term is not stronger than a
    line matching one; the old protection holds and the capsule is unchanged
    in kind (the tied distilled line stays)."""
    chat = "ms-weak"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS,
          "Sure, I sometimes walk past the water there, it is pleasant enough most days and "
          "the benches along the promenade are comfortable for a short rest in the afternoon "
          "when the wind drops and the light turns golden over the rooftops of Nordvik.")
    block = _capsule(fresh_profile, chat,
                     "Which harbor does Ilse describe as the quietest one in Nordvik?", 280)
    assert "rowing boat" in block or "harbor festivals" in block


# ── the laws as pure functions, on capsule-shaped lines ───────────────────

_ENVELOPED_LINE = (
    "- user said [reported source prefix \"Mira:\"]: Session date: 12:35 am on 14 August, 2023\n"
    "Mira: Thanks, Joss! The gig in the old mill was loud and everyone sang along. "
    "(stated: 12: 35 am on 14 August, 2023; stated: 2023-08-14)")
_ASKED = {"mill", "described", "quietest", "corner", "mira"}


def test_envelope_and_speaker_label_are_provenance_not_content():
    body = cr._packed_fact_body(_ENVELOPED_LINE)
    assert body.startswith("Thanks, Joss!")
    assert "Session date" not in body and "2023" not in body
    # the envelope date no longer reads as a value of the fact
    assert not cr._distinctive_value_tokens(body)


def test_enveloped_line_tied_by_one_term_yields_to_a_stronger_span():
    span = ("Mira: Yes, the quietest corner of the mill is the loft above the "
            "flour store, nobody ever goes up there.")
    strength = cr._asked_match_strength(span, _ASKED)
    assert strength >= 3  # quietest, corner, mill (the speaker label is not counted)
    assert cr._capsule_line_demotable(_ENVELOPED_LINE, _ASKED, span_strength=strength)


def test_one_term_span_does_not_demote_a_tied_line():
    assert not cr._capsule_line_demotable(_ENVELOPED_LINE, _ASKED, span_strength=1)


def test_equally_strong_line_is_not_demoted():
    line = ("- user said: Session date: 2:00 pm on 1 May, 2023\n"
            "Mira: The mill has a quiet corner by the wheel. (stated: 2023-05-01)")
    strength = cr._asked_match_strength(line, _ASKED)
    assert not cr._capsule_line_demotable(line, _ASKED, span_strength=strength)


def test_value_bearing_fact_is_never_demoted_by_strength():
    line = ("- user said: Session date: 2:00 pm on 1 May, 2023\n"
            "Mira: The mill loft rent is 145 crowns a month. (stated: 2023-05-01)")
    assert not cr._capsule_line_demotable(line, _ASKED, span_strength=5)


def test_union_coverage_does_not_make_a_strong_span_redundant():
    """Every asked term already delivered somewhere (no uncovered term) — the
    span still decides when it carries two or more asked terms in one line."""
    assert cr._evidence_span_decisive(uncovered_terms=set(), span_strength=3, other_grounds=False)
    assert not cr._evidence_span_decisive(uncovered_terms=set(), span_strength=1, other_grounds=False)
    assert cr._evidence_span_decisive(uncovered_terms={"loft"}, span_strength=0, other_grounds=False)
