"""The one gate between a current-information answer and the wire.

WHY IT IS HERE AND NOWHERE ELSE
-------------------------------
`core.finalization.finalize_answer` is the single authority every semantic answer traverses
exactly once per turn, through all six transport doors. A grounding check anywhere else
certifies one door and leaves five open -- which is how a fabricated current answer with a
green retrieval receipt behind it reached users on ``a2308a26`` while three separate grounding
authorities were live in the same process.

WHAT IT DECIDES, AND FROM WHAT
------------------------------
Only for turns `core.execution_requirements` (M1) marked ``current_information_required``. Every
other turn -- ordinary chat, arithmetic, a file read, an explanation -- has no lifecycle row and
this module returns ``None`` before reading a byte. DIRECT and timeless answers are untouched by
construction, not by an exemption list.

For a current-information turn, the bytes may commit only when all of these hold at once:

1. **The answering model call named the bound evidence set.** Not "a retrieval happened" -- the
   receipt proves that and proves nothing else. The call's own context carried
   ``evidence_synthesis_binding.evidence_set_id``, recorded at call entry
   (`core.turn_model_call_ledger`). A call entered BEFORE anything was bound carries no id, so
   an answer written before its evidence existed fails here without anyone timing anything.
2. **Every publishable claim is supported by that exact bound set.** `core.claim_support`
   (M4) is recomputed HERE, over the bytes actually about to commit -- not over whatever the
   reasoning lane saw before the sealing transforms, the honesty passes and the response
   controls had their turn with the text.
3. **The identities match.** The lifecycle resolved for this turn, and its fence tuple is the
   current one (`core.grounding_lifecycle`).

THE THREE EXITS
---------------
* ``PUBLISHED``  -- coverage is full (or the answer asserts nothing testable). Bytes unchanged,
  links and formatting intact.
* ``PARTIAL``    -- some claims supported, some not. The unsupported lines are REMOVED and an
  explicit notice names what was withheld. A supported claim may not launder the fabricated
  sibling standing next to it, and the reader is told that something was withheld rather than
  handed a quietly shortened answer.
* ``REFUSED``    -- nothing is supported. The model's bytes do not ship at all; a typed
  statement naming the stage that failed does, followed by what the sources actually returned
  when the turn retrieved anything (`compose_grounded_report` -- source text only, no authored
  prose).

TYPED OBSERVATION LANES
-----------------------
A weather fetch, a live quote and a file read are not generations. When a turn made **no model
call at all**, its publishable bytes were composed by the runtime from a typed observation, and
that observation mints the support -- judged usable or not by
`core.observation_evidence`, the same authority `turn_ran_observations` already answers to. A
failed typed lookup mints nothing, which is the whole point: the measured incident invented a
Porto reading with the previous turn's timestamp on the strength of a receipt that said
``status='failed' source_count=0``. When a lane supplies the rows it observed, those rows are
matched like any other bound source.

IDEMPOTENCE
-----------
Finalization admits identical duplicates and refuses different-content re-finalization, so this
transform has to be a fixed point: gating already-gated bytes must return them byte-identically.
The notice is the marker -- content that already carries it is passed through untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.grounding_lifecycle import (
    EXIT_FAILED,
    EXIT_PARTIAL,
    EXIT_REFUSED,
    ORIGIN_BOUND_EVIDENCE,
    ORIGIN_COMPUTED_VALUE,
    ORIGIN_TYPED_OBSERVATION,
    STAGE_BOUND,
    STAGE_PUBLISHED,
    STAGE_RETRIEVED,
    STAGE_SUPPORTED,
    GroundingLifecycle,
)

#: The first words of the withheld-work notice. Load-bearing twice: it is what tells a reader
#: something was removed, and it is how this transform recognises its own output and stays a
#: fixed point.
UNSUPPORTED_WORK_NOTICE_LEAD = "Withheld from this answer:"

#: What ships when the bound evidence supports nothing in the answer. Never the model's bytes.
_REFUSAL_LEAD = (
    "I can't publish an answer to this: it needed current information, and nothing in what "
    "this turn actually retrieved supports the answer that was written."
)

_RE_PRESENTATION_REFUSAL_LEAD = (
    "I can't publish this re-presentation: it restates statements the previous answer had "
    "withheld, and the previous answer's published support does not cover them."
)
_RE_PRESENTATION_OF_WITHHELD_LEAD = (
    "I can't re-present the previous answer: it was withheld because nothing retrieved for it "
    "supported what was written, so there is nothing supported to reformat or shorten. Ask the "
    "question again and I will retrieve and answer it afresh."
)

#: Every lead `typed_refusal` can ship. A refusal is the runtime SAYING IT DECLINED; it is not
#: answer content, and it names the request verbatim so the reader knows which ask was declined.
#: That echo is right for a reader and wrong for a later prompt -- see `is_typed_refusal_text`.
_ALL_REFUSAL_LEADS = (
    _REFUSAL_LEAD,
    _RE_PRESENTATION_REFUSAL_LEAD,
    _RE_PRESENTATION_OF_WITHHELD_LEAD,
)


def is_typed_refusal_text(text: str) -> bool:
    """Whether `text` is a published refusal — from ANY family the runtime ships.

    Self-recognition, the same device `UNSUPPORTED_WORK_NOTICE_LEAD` already exists for: these are
    the runtime's OWN exported constants, not a phrase list aimed at user input, so the answer
    cannot drift with phrasing a model chose.

    The union matters, and that is measured, not defensive. A first version of this predicate knew
    only this module's three leads, and the served pack immediately showed the hole: acceptance
    turn 14 is refused by the AUTHORSHIP family ("I can't publish this answer: nothing this turn
    retrieved, computed or observed backs it"), which this module never emits, so turn 15 went on
    receiving turn 14's answer after the fix. One refusal family covered is not the class covered.
    Any module that publishes a refusal lead must be represented here.

    That includes the pinned-build refusal, whose first line names the selected model and quotes
    the provider's own typed error -- wording the evidence binder was reading as a model's
    attestation claim ("provider said ...") and replacing with a non-sequitur about model
    attestation, destroying the actionable reason the operator was refused for.
    """
    from core.agent_runtime.builder.pinned_generation import PINNED_BUILD_REFUSAL_LEAD
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    body = str(text or "").lstrip()
    leads = (*_ALL_REFUSAL_LEADS, UNCERTIFIED_AUTHOR_NOTICE_LEAD, PINNED_BUILD_REFUSAL_LEAD)
    return any(body.startswith(lead) for lead in leads)


_STAGE_EXPLANATION = {
    STAGE_RETRIEVED: (
        "The retrieval for this turn returned no usable rows, so there was nothing for the "
        "answer to be built from."
    ),
    STAGE_BOUND: (
        "This turn retrieved sources, but they never reached the model call that wrote the "
        "answer -- so the text is memory, not a reading of those sources. A retrieval receipt "
        "records that a search ran; it cannot record that anything found was used."
    ),
    STAGE_SUPPORTED: (
        "The sources this turn retrieved do not support the statements that were written."
    ),
}


@dataclass(frozen=True)
class PublicationVerdict:
    """What may ship, and the stage that decided it."""

    state: str
    content: str
    coverage: str = ""
    failed_stage: str = ""
    support_origin: str = ""
    evidence_set_id: str = ""
    supported_claim_count: int = 0
    withheld_claims: tuple[str, ...] = ()
    exempted_claims: tuple[str, ...] = ()
    claim_support: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def published(self) -> bool:
        return self.state == STAGE_PUBLISHED

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.grounding_publication.v1",
            "state": self.state,
            "coverage": self.coverage,
            "failed_stage": self.failed_stage,
            "support_origin": self.support_origin,
            "evidence_set_id": self.evidence_set_id,
            "supported_claim_count": self.supported_claim_count,
            "withheld_claim_count": len(self.withheld_claims),
            "withheld_claims": list(self.withheld_claims),
            "exempted_claim_count": len(self.exempted_claims),
            "exempted_claims": list(self.exempted_claims),
            "claim_support": dict(self.claim_support),
            "detail": self.detail,
        }


def _support_rows(lifecycle: GroundingLifecycle) -> tuple[list[dict[str, Any]], str]:
    """The ONLY rows a claim may be matched against, and where they came from.

    Bound evidence counts only when the answering call actually named it. A binding whose id
    no model call carried is a retrieval that did not reach synthesis, and its rows are not
    offered here -- that is the difference between "we fetched this" and "this answer is made
    of it", and collapsing the two is the defect the whole lane exists to remove.
    """

    rows: list[dict[str, Any]] = []
    origin = ""
    if lifecycle.bound_evidence_set_id and lifecycle.synthesis_referenced_bound_evidence:
        rows.extend(dict(note) for note in lifecycle.bound_notes)
        origin = ORIGIN_BOUND_EVIDENCE
    # The turn's OWN observations (a live quote, a weather reading) support the claims made from
    # them beside any bound web notes. The two origins were exclusive here, and a mixed turn that
    # bound a web search withheld the sentences restating the quotes the runtime itself fetched
    # (measured 2026-09-06: ETH via CoinGecko and gold via Yahoo listed under "withheld" while
    # the published text said "I can't give you real values").
    observed = [
        dict(entry)
        for entry in lifecycle.typed_observations
        if isinstance(entry, dict) and _row_carries_content(entry)
    ]
    if observed:
        rows.extend(observed)
        origin = origin or ORIGIN_TYPED_OBSERVATION
    # The turn's own deterministic computations support the claims restating them -- a
    # calculation's value came out of runtime code, not out of a model's imagination, and
    # refusing "5 + 5 = 10" because a SIBLING clause needed current information is the
    # whole-turn-refusal defect this union closes. Not observations: `lifecycle.retrieved`
    # and `bound` deliberately do not read the computed channel.
    computed = [
        dict(entry)
        for entry in lifecycle.computed_values
        if isinstance(entry, dict) and _row_carries_content(entry)
    ]
    if computed:
        rows.extend(computed)
        origin = origin or ORIGIN_COMPUTED_VALUE
    return rows, origin


def _row_carries_content(row: Any) -> bool:
    """Whether a row has any field `core.claim_support` reads as source content."""

    from core.claim_support import _CONTENT_FIELDS

    if not isinstance(row, dict):
        return False
    return any(str(row.get(name) or "").strip() for name in _CONTENT_FIELDS)


def _runtime_composed(lifecycle: GroundingLifecycle) -> bool:
    """True when no model wrote these bytes: runtime code composed them.

    The condition is the ABSENCE of a generation, and it is read from two independent recorders
    (`GroundingLifecycle.generated`) so a lane that reaches a provider outside the ledger's entry
    seam still cannot present its output as deterministic. A weather render, a live-quote render
    and a typed refusal are all code, and code cannot fabricate a reading -- holding them to a
    claim-match against the very observation they were built from would refuse the lanes whose
    evidence is the strongest in the runtime.
    """

    return not lifecycle.generated


def _segments_by_line(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Each original line with the claim segments M4 would cut from it, in order.

    Line-level, deliberately. `core.claim_support.segment_claims` strips URLs before cutting,
    so rebuilding an answer out of segments would silently drop every source link -- and a
    grounded answer that loses its citations has been damaged, not gated.

    Fenced-code lines carry NO segments: a program's lines are artifact structure, not sourced
    prose, and adjudicating them per line is what shredded a delivered script into per-line
    "statements". The block's outward endpoints are claimed separately, as units.
    """

    from core.claim_support import code_fence_line_mask, segment_claims

    raw = str(text or "")
    mask = code_fence_line_mask(raw)
    return [
        (line, () if masked else tuple(segment_claims(line)))
        for line, masked in zip(raw.splitlines(), mask, strict=True)
    ]


def _withheld_artifact_notice(endpoints: tuple[str, ...]) -> str:
    """The notice for a code artifact withheld whole over its endpoints.

    Names the unwitnessed endpoints and says the program was withheld rather than shipped with
    unverified outward facts -- a withheld artifact is actionable (retrieve the documentation
    and ask again); a shredded skeleton is neither.
    """
    from core.claim_support import ENDPOINT_CLAIM_PREFIX

    urls = [str(item)[len(ENDPOINT_CLAIM_PREFIX) :].strip() for item in endpoints]
    count = len(urls)
    return (
        f"Withheld {count} code artifact{'s' if count != 1 else ''} whole: the API endpoint"
        f"{'s' if count != 1 else ''} below {'are' if count != 1 else 'is'} not in the sources "
        "retrieved for this turn, and a program that sends bytes to an address nothing "
        "witnessed is not publishable on the strength of its shape alone.\n"
        + "\n".join(f"- {url}" for url in urls)
    )


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
WITHHELD_CELL = "(withheld: not supported)"
UNVERIFIED_CELL = "(not in the sources)"


def _table_line_kind(line: str, lines: list[tuple[str, tuple[str, ...]]], index: int) -> str:
    """'structure' for a markdown table's separator or header row, 'row' for a data row, '' else."""
    if not _TABLE_ROW_RE.match(line):
        return ""
    if _TABLE_SEPARATOR_RE.match(line):
        return "structure"
    if index + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[index + 1][0]):
        return "structure"
    return "row"


def _table_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _compose_table_row(
    line: str,
    segments: tuple[str, ...],
    supported: set[str],
    unsupported: set[str],
) -> tuple[str | None, list[str], list[str]]:
    """Adjudicate a markdown table row CELL by cell.

    A row packs several claims into one line ("| Production started | 1974 ... | 1973 ... |"); at
    line grain one withheld cell took the supported one down with it, so a fact the sources DID
    carry did not survive being tabulated. Here the first cell is the row's label and is kept
    while any other cell survives; a cell carrying an unsupported claim becomes `WITHHELD_CELL`,
    a cell whose every claim is uncertain becomes `UNVERIFIED_CELL`, and a supported cell (or a
    cell that asserts nothing checkable, such as a dash) stays as written. A row with nothing
    left but its label is dropped; what was replaced is reported, exactly as for prose lines.
    """
    cells = _table_cells(line)
    if len(cells) < 2:
        return None, list(s for s in segments if s in unsupported), list(s for s in segments if s not in supported and s not in unsupported)
    withheld: list[str] = []
    unverifiable: list[str] = []
    out: list[str] = [cells[0]]
    survivors = 0
    from core.claim_support import strip_emphasis_markers

    for cell in cells[1:]:
        cell_plain = strip_emphasis_markers(cell)
        cell_segments = [s for s in segments if s and s in cell_plain]
        if not cell_segments:
            out.append(cell)
            survivors += 1 if cell.strip() else 0
            continue
        cell_unsupported = [s for s in cell_segments if s in unsupported]
        if cell_unsupported:
            withheld.extend(cell_unsupported)
            out.append(WITHHELD_CELL)
            continue
        if any(s in supported for s in cell_segments):
            out.append(cell)
            survivors += 1
            continue
        unverifiable.extend(cell_segments)
        out.append(UNVERIFIED_CELL)
    if survivors == 0:
        # nothing supported or neutral survived beside the label: the row itself is withheld
        return None, withheld, unverifiable
    return "| " + " | ".join(out) + " |", withheld, unverifiable


def _locate_ignoring_markers(line: str, needle: str) -> tuple[int, int] | None:
    """(start, end) of `needle` in `line`, where the needle is a segment WITHOUT emphasis
    markers and the line still carries them ("**Prius**: Smoother ..." holds "Prius: Smoother ...").
    The span returned covers the original characters, markers inside it included."""

    from core.claim_support import _EMPHASIS_MARKER_RE

    plain_chars: list[str] = []
    index_map: list[int] = []
    position = 0
    for match in _EMPHASIS_MARKER_RE.finditer(line):
        for offset in range(position, match.start()):
            plain_chars.append(line[offset])
            index_map.append(offset)
        position = match.end()
    for offset in range(position, len(line)):
        plain_chars.append(line[offset])
        index_map.append(offset)
    plain = "".join(plain_chars)
    found = plain.find(needle)
    if found < 0:
        return None
    return index_map[found], index_map[found + len(needle) - 1] + 1


def _trim_unsupported_spans(line: str, unsupported_segments: list[str]) -> str | None:
    """`line` with each unsupported sentence removed where it stands, or None when any of them
    cannot be located verbatim (the caller then withholds the whole line, as before)."""
    out = str(line)
    for segment in unsupported_segments:
        needle = str(segment or "").strip()
        if not needle:
            return None
        span = _locate_ignoring_markers(out, needle)
        if span is None:
            return None
        start, end = span
        out = out[:start] + " " + out[end:]
    # Emphasis markers that wrapped a removed sentence are now empty pairs or lone tokens.
    out = re.sub(r"(\*\*|__|\*|`)\s*\1", " ", out)
    out = re.sub(r"(?<!\S)(?:\*\*|__|\*|`)(?!\S)", " ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    # A removed sentence leaves its neighbours' punctuation touching (".;" / ";."): one mark.
    out = re.sub(r"([.!?])\s*[;,]", r"\1", out)
    out = re.sub(r";\s*([.!?])", r"\1", out)
    return out.strip()


def _poisoned_artifact_lines(
    content: str, mask: list[bool], unsupported_endpoints: set[str]
) -> tuple[set[int], tuple[str, ...]]:
    """Line indexes of fence blocks whose asserted endpoints include an unsupported one.

    A code artifact is withheld WHOLE or shipped INTACT -- never shredded. Withholding follows
    the artifact's own outward facts: a block whose every endpoint is witnessed ships byte for
    byte; a block with one unwitnessed endpoint is not publishable on the strength of its
    shape, and the skeleton that would remain after line-level surgery is not a usable program.

    Returns (poisoned line indexes, one readable withheld entry per unwitnessed endpoint).
    """
    from core.claim_support import ENDPOINT_CLAIM_PREFIX, endpoint_claims_from_code

    poisoned: set[int] = set()
    entries: list[str] = []
    raw_lines = str(content or "").splitlines()
    index = 0
    while index < len(mask):
        if not mask[index]:
            index += 1
            continue
        end = index
        while end < len(mask) and mask[end]:
            end += 1
        block = raw_lines[index:end]
        claimed = [item for item in endpoint_claims_from_code(block) if item in unsupported_endpoints]
        if claimed:
            poisoned.update(range(index, end))
            for item in claimed:
                url = item[len(ENDPOINT_CLAIM_PREFIX) :].strip()
                entries.append(
                    f"code artifact withheld whole — its API endpoint {url} is not in the "
                    "sources retrieved for this turn"
                )
        index = end
    return poisoned, tuple(dict.fromkeys(entries))


def compose_partial_truth(
    content: str, claim_map: Any
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Keep the supported claims, drop the rest, and report BOTH kinds of drop.

    Deterministic. A line survives when it carries at least one supported claim and no
    unsupported one. Lines that assert nothing -- blank lines, lead-ins ending in a colon,
    bullets that are only a link -- survive as structure, and are trimmed at the ends where
    they would be left introducing content that is no longer there. A fenced code block is an
    ARTIFACT: it survives byte for byte when its outward endpoints are witnessed, and is
    withheld whole when one is not.

    Returns (body, withheld, unverifiable):

    * `withheld` -- lines that carried an UNSUPPORTED claim: the sources contradict or do
      not witness them. These are refusals, and the notice says so.
    * `unverifiable` -- lines whose every segment was `uncertain`: nothing to adjudicate
      against the sources. They do not travel with a partial answer either -- in company
      with a withheld fabrication they may be its launderers -- but they are REPORTED, not
      silently deleted: measured on the mixed-demand drive, a turn's supported weather
      lines shipped while the model's whole timeless paragraph vanished from the answer
      with no row, no notice and no refusal naming it. A reader who cannot see what was
      dropped cannot tell a trimmed answer from a complete one, which is the silence this
      gate exists to break.
    """
    from core.claim_support import ENDPOINT_CLAIM_PREFIX, code_fence_line_mask

    supported = {
        claim.text
        for claim in claim_map.claims
        if claim.status in ("supported", "exempt_stable_knowledge")
    }
    unsupported = {claim.text for claim in claim_map.claims if claim.status == "unsupported"}
    unsupported_endpoints = {
        text for text in unsupported if text.startswith(ENDPOINT_CLAIM_PREFIX)
    }
    kept: list[str] = []
    withheld: list[str] = []
    unverifiable: list[str] = []
    lines = _segments_by_line(content)
    poisoned, artifact_entries = _poisoned_artifact_lines(
        content, code_fence_line_mask(content), unsupported_endpoints
    )
    for index, (line, segments) in enumerate(lines):
        if index in poisoned:
            # The artifact this line belongs to asserted an endpoint nothing witnessed: the
            # block is withheld whole, and its notice entry is artifact-level, not per line.
            continue
        if not segments:
            kept.append(line)
            continue
        table_kind = _table_line_kind(line, lines, index)
        if table_kind == "structure":
            # A table's separator row and its header row assert nothing: they are the shape the
            # data rows hang on. Measured live 2026-09-06: adjudicating them as claims withheld the
            # header ("| Factor | VW Golf | VW Passat |") and set aside the separator, leaving one
            # orphaned data row -- a destroyed table, not a gated one.
            kept.append(line)
            continue
        if table_kind == "row":
            row_line, row_withheld, row_unverifiable = _compose_table_row(line, segments, supported, unsupported)
            withheld.extend(row_withheld)
            unverifiable.extend(row_unverifiable)
            if row_line is not None:
                kept.append(row_line)
            continue
        line_unsupported = [segment for segment in segments if segment in unsupported]
        if line_unsupported:
            withheld.extend(line_unsupported)
            # A prose line carrying several sentence-claims is adjudicated sentence by sentence,
            # exactly as a table row is cell by cell: the unsupported sentences are removed
            # where they stand and the supported ones keep their place, their order and their
            # links. Measured live 2026-09-06: one paragraph with six supported claims and one
            # unsupported sentence was dropped whole, the map forced to `none`, and the reader
            # got a full refusal. A sentence that cannot be located verbatim (a link inside it
            # -- segmentation strips URLs) keeps the old whole-line rule, so no link is ever
            # silently dropped.
            if any(segment in supported for segment in segments):
                trimmed = _trim_unsupported_spans(line, line_unsupported)
                if trimmed is not None and trimmed.strip():
                    kept.append(trimmed)
            continue
        if any(segment in supported for segment in segments):
            kept.append(line)
            continue
        # Every segment on this line is `uncertain`: prose with nothing to adjudicate. It
        # is not a supported claim, so it does not travel with the partial answer -- but it
        # is named in the notice rather than vanishing.
        unverifiable.extend(segments)
        continue
    kept = _drop_orphaned_headings(kept)
    while kept and (not kept[0].strip() or kept[0].rstrip().endswith(":")):
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    withheld.extend(artifact_entries)
    body = "\n".join(kept).strip()
    return body, tuple(dict.fromkeys(withheld)), tuple(dict.fromkeys(unverifiable))


_HEADING_LINE_RE = re.compile(r"^\s{0,3}(#{1,6})\s+\S")


def _drop_orphaned_headings(lines: list[str]) -> list[str]:
    """Headings survive as structure -- except a heading whose whole section was withheld, which
    would otherwise stand over nothing ("## Engines" with every engine sentence gone). A heading's
    section runs to the next heading of the same or a higher level, or to the end."""

    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HEADING_LINE_RE.match(line)
        if not match:
            out.append(line)
            index += 1
            continue
        level = len(match.group(1))
        end = index + 1
        while end < len(lines):
            nested = _HEADING_LINE_RE.match(lines[end])
            if nested and len(nested.group(1)) <= level:
                break
            end += 1
        section = lines[index + 1 : end]
        if any(item.strip() and not _HEADING_LINE_RE.match(item) for item in section):
            out.append(line)
            out.extend(_drop_orphaned_headings(section))
        index = end
    return out




def _support_scope(lifecycle: GroundingLifecycle) -> str:
    """What the notice says the withheld claims were adjudicated against."""
    from core.grounding_lifecycle import REASON_RE_PRESENTATION
    if REASON_RE_PRESENTATION in tuple(lifecycle.reason_codes or ()):
        return "the previous answer's published support does not cover"
    return "the sources retrieved for this turn do not support"


def unsupported_work_notice(
    withheld: tuple[str, ...],
    unverifiable: tuple[str, ...] = (),
    *,
    support_scope: str = "the sources retrieved for this turn do not support",
) -> str:
    """Name what was removed, and why. Deterministic, in the order the claims appeared.

    The count and the text of every withheld claim are stated. A notice that only said "some
    parts were removed" would leave the reader unable to tell a trimmed answer from a complete
    one, which is the same silence the gate exists to break.
    """

    if not withheld and not unverifiable:
        return ""
    parts: list[str] = []
    if withheld:
        head = (
            f"{UNSUPPORTED_WORK_NOTICE_LEAD} {len(withheld)} statement"
            f"{'' if len(withheld) == 1 else 's'} that {support_scope}. "
            "Everything above is carried by that support."
        )
        parts.append(head + "\n\n" + "\n".join(f"- {claim}" for claim in withheld))
    if unverifiable:
        parts.append(
            f"Also set aside: {len(unverifiable)} statement"
            f"{'' if len(unverifiable) == 1 else 's'} this turn's sources neither support nor "
            "contradict, so they were not published beside the supported material above:\n"
            + "\n".join(f"- {claim}" for claim in unverifiable)
        )
    return "\n\n".join(parts)


def typed_refusal(lifecycle: GroundingLifecycle, *, failed_stage: str) -> str:
    """What ships when nothing in the answer is supported. Names the stage, not a mood.

    When the turn did retrieve rows, what those rows actually said is appended through
    `core.evidence_binding.compose_grounded_report` -- every sentence in it is source text with
    its attribution, so the reader gets the observation the answer failed to use rather than an
    apology in its place.
    """

    from core.evidence_binding import compose_grounded_report
    from core.grounding_lifecycle import REASON_RE_PRESENTATION, REASON_RE_PRESENTATION_OF_WITHHELD

    subject = " ".join(str(lifecycle.request_text or "").split()).strip()
    if REASON_RE_PRESENTATION_OF_WITHHELD in tuple(lifecycle.reason_codes or ()):
        lead = _RE_PRESENTATION_OF_WITHHELD_LEAD
        return lead if not subject else f'{lead} (request: "{subject[:160]}")'
    if REASON_RE_PRESENTATION in tuple(lifecycle.reason_codes or ()):
        # No retrieval ran this turn: the support was the previous answer's published support,
        # and the reformatted bytes restated only what that answer had withheld.
        lead = _RE_PRESENTATION_REFUSAL_LEAD
        return lead if not subject else f'{lead} (request: "{subject[:160]}")'
    parts = [_REFUSAL_LEAD if not subject else f'{_REFUSAL_LEAD} (request: "{subject[:160]}")']
    explanation = _STAGE_EXPLANATION.get(failed_stage or "")
    if explanation:
        parts.append(explanation)
    report = compose_grounded_report(list(lifecycle.bound_notes))
    if report:
        parts.append(report)
    return "\n\n".join(parts)


def already_gated(content: str) -> bool:
    """Whether these bytes are this transform's own output, being finalized a second time."""

    return UNSUPPORTED_WORK_NOTICE_LEAD in str(content or "")


def _file_unsupported_claim_fault(lifecycle: GroundingLifecycle, claim_map: Any) -> None:
    """File the typed fault for claims this gate just withheld. Best-effort, never raises.

    The gate owns the mapping -- it is where "unsupported" is DECIDED, over the bytes
    actually committing. The record carries only a count and a one-way digest of the
    withheld texts: claim text itself is content and never rides a diagnostic.
    """
    try:
        import hashlib as _hashlib

        from core.faults.recorder import record_fault
        from core.faults.records import FaultRecord

        withheld = tuple(claim.text for claim in claim_map.unsupported_claims)
        if not withheld:
            return
        identity = lifecycle.identity
        digest = _hashlib.sha256("\x1f".join(withheld).encode("utf-8")).hexdigest()[:12]
        record_fault(
            FaultRecord.for_code(
                "unsupported_claim",
                authority="core.grounding_publication",
                turn_key=str(identity.turn_id or ""),
                session_id=str(identity.session_id or ""),
                dedupe=f"claims:{digest}",
                context={"claim_count": len(withheld), "stage": "supported"},
            )
        )
    except Exception:
        # The verdict stands on its own; the fault record is observability beside it.
        pass


def _stable_knowledge_exemptions(
    lifecycle: GroundingLifecycle, claim_map: Any
) -> list[tuple[Any, dict[str, Any]]]:
    """Unsupported claims the runtime's authoring policy may publish as stable knowledge.

    Eligibility has three independent anchors, none of them the generated text itself:

    * AUTHORITY — the entry carries `author_eligible`, stamped by the conductor's publisher
      from `core.final_answer_authorship.decide_final_answer_author`'s own verdict for the
      generation call that served this node (joined by the clause the briefing embeds; the
      join fails closed). The permission to answer general knowledge without sources is the
      SAME policy under which a DIRECT knowledge turn publishes with no grounding lifecycle
      at all — a planner naming a clause KNOW, or a model producing plausible text, creates
      no permission. An unmarked entry, or one whose author was ineligible, exempts nothing.
    * DEMAND — the claim's folded text sits INSIDE the rendered line this entry recorded for
      a SUCCEEDED open-authority knowledge node. A rewritten, trimmed or extended sentence is
      not the recorded render and is not exempt; an unsupported sibling beside an exempt line
      gains nothing from it.
    * SHAPE — evidence-required claims never gain exemption by sitting inside a
      knowledge-classified render. Three structural refusals, all reusing machinery the
      claim matcher already computed: a claim carrying a CURRENT-TRUTH marker
      (`currency_markers` — "now", "today", ...) or a freshness cue is current-information
      shaped; and a claim asserting a specific numeric that is not YEAR-LIKE is a
      situation-specific measurement ("a first edition costs 5000 USD") — exactly the class
      the open-authority rule itself refuses to license. Request-supplied numerics never
      reach `anchors.numerics` (the extractor excludes them), so this guard is about the
      numbers the answer INTRODUCED.
    """

    entries = [
        entry
        for entry in (lifecycle.stable_knowledge or ())
        if isinstance(entry, dict)
        and str(entry.get("summary") or "").strip()
        and entry.get("author_eligible") is True
    ]
    if not entries:
        return []
    from core.claim_support import _year_like, strip_emphasis_markers
    from core.conductor.operations import stable_knowledge_freshness_refuses

    def _fold_render(text: str) -> str:
        # The claim matcher segments an answer with emphasis and list markers removed
        # (`segment_claims`); a render recorded RAW ("3. **The Berlin Wall fell in 1989**.")
        # must be folded the same way or the demand join fails on formatting alone --
        # measured served (c5463c47, forced-decline turn 18 and the turn-14 comparison):
        # every claim inside an eligible render was withheld because of the markers.
        import re as _re

        plain = strip_emphasis_markers(str(text or ""))
        plain = _re.sub(r"(?m)^\s*(?:[-*•>]+|\d+[.)])\s+", "", plain)
        return " ".join(plain.split())

    lines = [(_fold_render(str(entry.get("summary") or "")), entry) for entry in entries]
    exempt: list[tuple[Any, dict[str, Any]]] = []
    # Unsupported AND uncertain claims are candidates: an `uncertain` claim is hedged prose the
    # matcher could not adjudicate ("electric cars usually cost more to buy"); inside an eligible
    # stable-knowledge render it is the author's own knowledge, published under the same policy
    # that publishes a DIRECT turn whole. The SHAPE guards below still judge it on its own.
    for claim in claim_map.claims:
        if str(getattr(claim, "status", "") or "") not in ("unsupported", "uncertain"):
            continue
        # A claim the matcher adjudicated as a VALUE or QUANTITY MISMATCH was measured against a
        # source that carries values for the subject and none agreed -- the turn's own evidence
        # had something to say and it disagreed. Stable knowledge is the author's licence for
        # what the sources do NOT discuss ("the Wall fell in 1989" against pages that never say);
        # it is not an override for a contradiction already on the record (measured: the cities
        # comparison's invented "Riga has a metro line since 2019" shipped under this exemption
        # while its year made the numeric guard pass).
        if any(
            reason in ("value_mismatch", "quantity_mismatch")
            for reason in (getattr(claim, "reasons", ()) or ())
        ):
            continue
        folded = _fold_render(str(claim.text or ""))
        if not folded:
            continue
        inside = next(((line, entry) for line, entry in lines if folded in line), None)
        if inside is None:
            continue
        if getattr(claim.anchors, "currency_markers", ()):
            continue
        if stable_knowledge_freshness_refuses(folded):
            continue
        introduced = set(getattr(claim.anchors, "numerics", ()) or ())
        if any(not _year_like(value) for value in introduced):
            continue
        exempt.append((claim, inside[1]))
    return exempt


def _claim_map_with_exemptions(
    claim_map: Any, exemptions: list[tuple[Any, dict[str, Any]]]
) -> Any:
    """The map rebuilt with exempted claims marked for the TRIM pass, never as supported.

    `compose_partial_truth` keeps lines whose claims are supported and trims the rest, so an
    exemption that wants its line kept must present as non-unsupported for that pass. The
    claim's STATUS becomes `exempt_stable_knowledge` (a distinct state the verdict records and
    `as_dict` shows) and its `supporting_sources` names the AUTHORITY that licensed it — the
    authoring policy and the demand's node — never a retrieval: model output is never
    presented as retrieved or independently verified, and `supported_claim_count` never
    counts an exempt claim. That is the accounting distinction between "answered under the
    authoring policy" and "factually verified by this turn's sources".
    """

    from dataclasses import replace as _dc_replace

    bound = {id(claim): entry for claim, entry in exemptions}
    claims = tuple(
        _dc_replace(
            claim,
            status="exempt_stable_knowledge",
            reasons=tuple(
                dict.fromkeys(
                    (
                        *claim.reasons,
                        "stable_knowledge_node:"
                        + str(bound[id(claim)].get("node_id") or ""),
                    )
                )
            ),
            supporting_sources=("authoring_policy:final_answer",),
        )
        if id(claim) in bound
        else claim
        for claim in claim_map.claims
    )
    supported = [c for c in claims if c.status == "supported"]
    unsupported = [c for c in claims if c.status == "unsupported"]
    has_exempt = any(c.status == "exempt_stable_knowledge" for c in claims)
    if unsupported:
        coverage = "partial" if (supported or has_exempt) else "none"
    elif supported or has_exempt:
        coverage = "full"
    else:
        coverage = "no_claims"
    return _dc_replace(claim_map, claims=claims, coverage=coverage)


def publication_verdict(lifecycle: GroundingLifecycle, content: str) -> PublicationVerdict:
    """Decide what a current-information turn may publish, from the lifecycle and the bytes.

    The claim map is computed here, over `content`, and never inherited from the reasoning
    lane: between that lane and this seam the text passes through the sealing transforms, the
    honesty passes and the response controls, and a verdict about the text as it used to be is
    a verdict about bytes nobody is shipping.
    """

    from core.claim_support import match_claims

    text = str(content or "")
    if already_gated(text):
        return PublicationVerdict(
            state=STAGE_PUBLISHED,
            content=text,
            coverage="already_gated",
            support_origin=lifecycle.support_origin,
            evidence_set_id=lifecycle.bound_evidence_set_id,
            detail="content already carries this turn's withheld-work notice",
        )

    rows, origin = _support_rows(lifecycle)
    from core.grounding_lifecycle import REASON_RE_PRESENTATION_OF_WITHHELD

    if REASON_RE_PRESENTATION_OF_WITHHELD in tuple(lifecycle.reason_codes or ()):
        # A re-presentation of an answer this session withheld: nothing supported exists to
        # reformat. Decided before the runtime-composed shortcut, because the shape-constraint
        # fallback ("No usable answer was produced for this request.", as a table) is runtime
        # code too and was exactly the misleading table measured live.
        return PublicationVerdict(
            state=EXIT_REFUSED,
            content=typed_refusal(lifecycle, failed_stage=STAGE_SUPPORTED),
            coverage="re_presentation_of_withheld",
            failed_stage=STAGE_SUPPORTED,
            support_origin="",
            evidence_set_id="",
            detail="the previous answer was withheld; a re-presentation has no supported rows",
        )

    # No generation on this turn: the bytes are runtime code's output -- a typed observation
    # rendered, or a typed refusal. Nothing here was written by a model, so there is nothing to
    # check for derivation from evidence.
    if _runtime_composed(lifecycle):
        return PublicationVerdict(
            state=STAGE_PUBLISHED,
            content=text,
            coverage="runtime_composed",
            support_origin=ORIGIN_TYPED_OBSERVATION if lifecycle.typed_observations else "",
            supported_claim_count=len(lifecycle.typed_observations),
            detail="no model call this turn; bytes composed by runtime code, not generated",
        )

    # A turn with NO support rows is refused before any claim is read -- unless the plan's (or the
    # plain lane's) authoring policy recorded eligible stable-knowledge renders: those claims are
    # judged by the exemption below, not by rows. Measured on the final pack (6c661fff, turn 14):
    # a comparison the authority read as STABLE was refused here as `no_sources` after an optional
    # enrichment search found nothing, and its per-unit entries could never reach the matcher.
    has_eligible_stable = any(
        isinstance(entry, dict)
        and str(entry.get("summary") or "").strip()
        and entry.get("author_eligible") is True
        for entry in (lifecycle.stable_knowledge or ())
    )
    if not rows and not has_eligible_stable:
        failed = STAGE_BOUND if lifecycle.retrieved else STAGE_RETRIEVED
        if lifecycle.bound_evidence_set_id and not lifecycle.synthesis_referenced_bound_evidence:
            failed = STAGE_BOUND
        return PublicationVerdict(
            state=EXIT_REFUSED if lifecycle.retrieval_outcome else EXIT_FAILED,
            content=typed_refusal(lifecycle, failed_stage=failed),
            coverage="no_sources",
            failed_stage=failed,
            support_origin="",
            evidence_set_id=lifecycle.bound_evidence_set_id,
            detail=f"retrieval_outcome={lifecycle.retrieval_outcome or 'none'}",
        )

    claim_map = match_claims(answer=text, notes=rows, request_text=lifecycle.request_text)
    # F43 — stable knowledge under the runtime's authoring policy. An unsupported claim the
    # plan rendered from an OPEN-authority knowledge node may publish WITHOUT sources, but
    # only when the authorship policy's own verdict (stamped on the entry at publish time,
    # joined to this node's call) says the writer was eligible: the planner naming a clause
    # KNOW and the model producing plausible text create no permission of their own. Applied
    # after matching and before any branch reads coverage, so every path below sees the same
    # adjusted map.
    exemptions = _stable_knowledge_exemptions(lifecycle, claim_map)
    if exemptions:
        claim_map = _claim_map_with_exemptions(claim_map, exemptions)
    coverage = claim_map.coverage
    supported = len(claim_map.supported_claims)
    exempted = tuple(claim.text for claim, _entry in exemptions)

    if coverage in {"full", "no_claims"}:
        detail = ""
        if exempted:
            detail = (
                f"{len(exempted)} claim(s) published on the plan's open-authority stable "
                "knowledge, not on retrieved sources"
            )
        return PublicationVerdict(
            state=STAGE_PUBLISHED,
            content=text,
            coverage=coverage,
            support_origin=origin,
            evidence_set_id=lifecycle.bound_evidence_set_id,
            supported_claim_count=supported,
            exempted_claims=exempted,
            claim_support=claim_map.as_dict(),
            detail=detail,
        )

    if coverage == "partial":
        body, withheld, unverifiable = compose_partial_truth(text, claim_map)
        notice = unsupported_work_notice(withheld, unverifiable, support_scope=_support_scope(lifecycle))
        _file_unsupported_claim_fault(lifecycle, claim_map)
        if body.strip():
            detail = ""
            if exempted:
                detail = (
                    f"{len(exempted)} claim(s) published on the plan's open-authority stable "
                    "knowledge, not on retrieved sources"
                )
            return PublicationVerdict(
                state=EXIT_PARTIAL,
                content=f"{body}\n\n{notice}" if notice else body,
                coverage=coverage,
                failed_stage=STAGE_SUPPORTED,
                support_origin=origin,
                evidence_set_id=lifecycle.bound_evidence_set_id,
                supported_claim_count=supported,
                withheld_claims=withheld,
                exempted_claims=exempted,
                claim_support=claim_map.as_dict(),
                detail=detail,
            )
        # Every supported claim lived on a line that also carried a fabricated one. Nothing
        # survives the split, so this is the none-supported case wearing a partial map.
        coverage = "none"

    _file_unsupported_claim_fault(lifecycle, claim_map)
    return PublicationVerdict(
        state=EXIT_REFUSED,
        content=typed_refusal(lifecycle, failed_stage=STAGE_SUPPORTED),
        coverage=coverage,
        failed_stage=STAGE_SUPPORTED,
        support_origin=origin,
        evidence_set_id=lifecycle.bound_evidence_set_id,
        supported_claim_count=0,
        withheld_claims=tuple(claim.text for claim in claim_map.unsupported_claims),
        claim_support=claim_map.as_dict(),
    )


def gate_publishable_content(content: str, *, turn_id: str = "", runtime_notice: bool = False) -> tuple[str, dict[str, Any]]:
    """THE gate, as `core.finalization` calls it: bytes in, publishable bytes plus the record out.

    Returns the content unchanged and an empty record for every turn that has no grounding
    lifecycle -- which is every turn M1 did not mark current-information. That is the DIRECT and
    timeless path, and it does not run a claim matcher, read a note or touch the text.
    """

    from core.grounding_lifecycle import lifecycle_for_publication, record_publication

    lifecycle = lifecycle_for_publication(turn_id=turn_id)
    if lifecycle is None:
        return str(content or ""), {}
    # The answer owner marks typed failure notices. A failed generation may have
    # opened this lifecycle, but its diagnostic is not an unsupported world claim.
    # Keep failure status and the original reason instead of claiming an answer existed.
    verdict = (PublicationVerdict(
        state=EXIT_FAILED, content=str(content or ""), coverage="runtime_notice",
        detail="runtime failure notice preserved; no answer certified",
    ) if runtime_notice else publication_verdict(lifecycle, str(content or "")))
    payload = verdict.as_dict()
    payload["lifecycle_id"] = lifecycle.lifecycle_id
    if verdict.content != str(content or ""):
        payload["transcript_amended"] = _amend_transcript(
            lifecycle, before=str(content or ""), after=verdict.content
        )
    record_publication(lifecycle.lifecycle_id, payload)
    record = dict(lifecycle.as_dict())
    record["publication"] = payload
    _emit_publication_event(lifecycle, record)
    return verdict.content, record


def _amend_transcript(
    lifecycle: GroundingLifecycle, *, before: str, after: str
) -> bool:
    """Make the stored transcript say what was actually published.

    The reasoning lane appends the assistant's answer to the conversation log BEFORE the
    commit seam runs, which was harmless while the served bytes were always the model's bytes.
    Once the gate can refuse or trim them it stops being harmless: measured on the isolated
    daemon, `/api/chat/history` returned the four fabricated Rust headlines while the wire had
    served the typed refusal -- so the text the gate refused was still in the record, still
    visible in the sidebar, and still available to be recalled into the NEXT turn as something
    this assistant had said.

    The row is matched by this turn's session AND by the exact pre-gate bytes, and only the
    most recent such row is rewritten -- an earlier turn that happened to produce identical
    text is not touched. A miss is REPORTED (`transcript_amended: false` in the publication
    record) rather than swallowed, because a store that disagrees with the wire has to be
    visible to be fixable.
    """

    session_id = lifecycle.identity.session_id
    if not session_id or before == after:
        return False
    try:
        from core.persistent_memory import has_staged_conversation_event
        from core.semantic.semantic_admissions import current_request_id

        if has_staged_conversation_event(current_request_id()):
            # Nothing was written yet: the lane staged its row for the commit boundary and the
            # seal writes it with the gated bytes. There is no draft in the store to amend.
            return True
    except Exception:
        pass
    try:
        from core.memory.files import conversation_log_path, load_jsonl, rewrite_jsonl

        rows = load_jsonl(conversation_log_path())
    except Exception:
        return False
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if not isinstance(row, dict):
            continue
        if str(row.get("session_id") or "") != session_id:
            continue
        if str(row.get("assistant") or "") != before:
            continue
        rows[index] = {**row, "assistant": after}
        try:
            rewrite_jsonl(conversation_log_path(), rows)
        except Exception:
            return False
        return True
    return False


def _emit_publication_event(lifecycle: GroundingLifecycle, record: dict[str, Any]) -> None:
    """Requirement 11: the terminal stage reaches Activity, not only the commit envelope.

    The commit already carries the whole lifecycle, and every earlier stage already emits its
    own row (`web_retrieval_started`/`completed` for RETRIEVED,
    `evidence_bound_to_synthesis` for BOUND). This is the last one, and it has to be
    reconstructed rather than passed: finalization holds no context, by design. The turn
    identity the lifecycle recorded at REQUIRED is enough to address the event to the right
    session and turn -- it is read back, never invented, and an identity that was never
    recorded emits nothing rather than a row addressed to nobody.
    """

    identity = lifecycle.identity
    if not identity.session_id:
        return
    stages = record.get("stages") or {}
    reached = [name for name, done in stages.items() if done]
    failed = str(record.get("failed_stage") or "")
    try:
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            {
                "session_id": identity.session_id,
                "runtime_session_id": identity.session_id,
                "cancel_turn_id": identity.turn_id,
                "request_id": identity.request_id,
            },
            event_type="grounding_publication",
            message=(
                f"Grounding lifecycle {record.get('publication', {}).get('state', 'unknown')}: "
                + (f"failed at {failed}." if failed else "every stage reached.")
            ),
            details={
                "schema": "vool.grounding_lifecycle.v1",
                "lifecycle_id": lifecycle.lifecycle_id,
                "stages_reached": reached,
                "failed_stage": failed,
                "state": record.get("publication", {}).get("state", ""),
                "evidence_set_id": lifecycle.bound_evidence_set_id,
                "support_origin": record.get("support_origin", ""),
                "supported_claim_count": record.get("publication", {}).get(
                    "supported_claim_count", 0
                ),
                "withheld_claim_count": record.get("publication", {}).get(
                    "withheld_claim_count", 0
                ),
            },
        )
    except Exception:
        return


__all__ = [
    "EXIT_FAILED",
    "EXIT_PARTIAL",
    "EXIT_REFUSED",
    "UNSUPPORTED_WORK_NOTICE_LEAD",
    "PublicationVerdict",
    "already_gated",
    "compose_partial_truth",
    "gate_publishable_content",
    "is_typed_refusal_text",
    "publication_verdict",
    "typed_refusal",
    "unsupported_work_notice",
]
