"""R1e — canonical demand ownership BEFORE lane execution.

THE MEASURED DEFECT (base 1543a597, hermetic)
---------------------------------------------
A message containing any live-data entity was claimed WHOLE by the live-data lane
before the planner ever saw it:

    "Explain how a hash table works and tell me the weather in Kaunas"
      -> "Kaunas: Sunny, 28 C."                     (the explanation VANISHED)
    "tell me the weather in Atlantisxyzabc123 and explain entropy briefly"
      -> a weather table row "Explain Entropy Briefly | Sunny | 28"
         (the explanation clause became a CITY and was served fake weather)

The same whole-turn claim ran through the currency family: a conversion clause plus an
explanation clause was answered with the conversion lane's own reply and the
explanation vanished. The whole-text recognizers fuse a verb from one clause with an
entity from another into a claim no clause makes — the exact defect class
``core.agent_runtime.answer_coverage`` documents for the front door, one level up at
the turn cascade.

THE CONTRACT
------------
The turn's demand units (``answer_coverage.demand_units`` — the RSS grain, minted from
the request text and never from family recognition) are read BEFORE a deterministic
lane may end the external turn:

* a lane that accounts for EVERY unit may claim the whole turn (pure turns keep
  their existing paths — the direct live fast path, the normal answer path);
* a lane that covers ONE unit of several PROPOSES that coverage and the runtime
  executes every unit through its owning lane as a planned sub-turn (R1d's chain,
  slots and recall), then synthesizes ONE answer that names partial failure
  truthfully (``turn_planner.merge_outcomes``);
* a text no unit-reader recognizes as live/currency work is general demand and no
  narrow lane may swallow it — a city name alone admits nothing.

THE PROBES ARE THE LANES' OWN READERS, not a new vocabulary: the live capability is
``core.execution_requirements._live_data_classification`` (the exact recognizers
``requirements_for`` consults for the whole message), and the currency capability is
the front door's own ``coverage_for(text, FAMILY_CURRENCY)`` claimed-slice reading. No
weather-specific routing exists here.

R1f — ONE REGISTRY. The lanes that participate in demand coverage are read from
``core.lane_registry.active_catalog()``: this module holds NO private lane list, only
the capability IMPLEMENTATIONS the catalog's names resolve to (lazily, so
lane_registry never imports agent_runtime). An unknown capability name raises — it
must never silently read as "covers everything". Tests inject synthetic lanes purely:
a scoped catalog (lane_registry.scoped_catalog) plus, for novel capabilities, a scoped
capability table (scoped_coverage_capabilities below) — no process-global mutation.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from core.lane_registry import (
    COVERAGE_COMPOSITE_PLAN,
    COVERAGE_FALLBACK_PLAN,
    COVERAGE_NONE,
    ROLE_COMPOSITE_PLANNER,
    ROLE_FALLBACK,
    find_spec,
)

#: Lane ids match the registry vocabulary (core.lane_registry) so recorded proposals
#: mediate under the kernel's existing ordering.
LANE_LIVE_DATA = "live_data_typed_plan"
LANE_CURRENCY = "currency_frontdoor"
LANE_WORKSPACE_READ = "workspace_read_fast_path"
LANE_DIRECT_MATH = "direct_math_fast_path"

#: Capability names that deliberately declare no per-unit coverage (see
#: lane_registry.LaneSpec). Reserved here so a typo'd name cannot hide among them.
_RESERVED_COVERAGE_NAMES = frozenset({COVERAGE_NONE, COVERAGE_COMPOSITE_PLAN, COVERAGE_FALLBACK_PLAN})


def _live_data_covers(unit_text: str) -> bool:
    """Whether the live-data family's own recognizers read THIS unit as a live lookup."""
    try:
        from core.execution_requirements import _live_data_classification

        return _live_data_classification(str(unit_text or "")) is not None
    except Exception:
        return False


def _currency_covers(unit_text: str) -> bool:
    """Whether the currency family claims THIS unit as its own request.

    ``covers_whole_turn`` alone is NOT a claim: on a single-clause text the scope reads
    whole-turn even when the family claims nothing (empty ``consumed``). The claim is
    the pair — scope whole-turn AND at least one claimed slice.
    """
    try:
        from core.agent_runtime.answer_coverage import FAMILY_CURRENCY, coverage_for

        coverage = coverage_for(str(unit_text or ""), FAMILY_CURRENCY)
        return coverage.covers_whole_turn and bool(coverage.consumed)
    except Exception:
        return False


def _workspace_read_covers(unit_text: str) -> bool:
    """Whether the workspace-read lane's OWN reader claims THIS unit as a file read.

    P0 MIXED-DEMAND. The probe is `_direct_workspace_read_request` — the same
    recognizer used by the workspace read lane to decide whether it owns a read —
    for the same reason the live capability reuses
    `_live_data_classification`: a second, parallel recognizer would drift from the
    lane it speaks for, and the drift would show up as a lane claiming units it
    cannot serve (or declining ones it can).
    """
    # "run `python3 app.py`" names a file but asks to EXECUTE it: a command step, never a read.
    if re.match(r"^\s*(?:and\s+|then\s+|also\s+)?(?:run|execute|start|launch|retry|rerun|test)\b", str(unit_text or ""), re.IGNORECASE):
        return False
    # "mention trie.py as an example file name" is decided by the lane's OWN recognizer
    # (`fast_paths_utility.names_files_only_in_speech`, consulted by the probe below) -- one
    # recognizer for the front door and the registry, never two that drift.
    # Coverage is consulted while execution units are being interpreted. The
    # multi-file planner refines line windows through those same units, so using
    # it here creates a cycle. Its first-file recognizer owns the identical
    # yes/no admission without performing execution-dependent refinement.
    try:
        from core.agent_runtime.fast_paths_utility import (
            _direct_workspace_read_request,
        )

        return bool(_direct_workspace_read_request(str(unit_text or "")))
    except Exception:
        return False


def _arithmetic_covers(unit_text: str) -> bool:
    """Whether the deterministic math lane claims THIS unit as an exact computation.

    `core.task_router` owns arithmetic recognition for the whole runtime; this asks
    it, rather than re-deriving what an expression looks like. Both the symbolic
    ("37 x 19") and the worded ("multiply 37 by 19") readers count — they are two
    entry points to ONE lane, and a unit either lane can compute is a unit that lane
    covers.
    """
    text = str(unit_text or "")
    try:
        from core.task_router import (
            looks_like_direct_math_request,
            looks_like_word_math_request,
        )

        return bool(looks_like_direct_math_request(text)) or bool(
            looks_like_word_math_request(text)
        )
    except Exception:
        return False


def _workspace_write_covers(unit_text: str) -> bool:
    """Whether the single-file write family's OWN authority mints a LITERAL write for THIS unit.

    P0 SIMPLE-FILE-WRITE. The probe is `core.execution.write_demand.resolve_write_demand` —
    the one typed literal-versus-brief write-demand authority the builder's workflow lane
    consumes — for the same reason the read capability reuses `_direct_workspace_read_requests`
    and the live capability reuses `_live_data_classification`: a second, parallel recognizer
    would drift from the lane it speaks for, and the drift would show up as a lane claiming
    units it cannot serve (or declining ones it can). A unit this probe claims is exactly a
    unit a deterministic `workspace.write_file` plan can execute with zero model calls; a unit
    whose content classifies as a BRIEF stays unclaimed — the builder owns it under EXACT
    scope.
    """
    try:
        from core.execution.write_demand import resolve_write_demand

        # A unit that OPENS with a continuation marker ("Then create summary.txt ...") is a
        # fragment of one composite workspace chain, not a standalone demand: the builder's
        # whole-turn workflow serves the folder bootstrap, every write and the trailing
        # listing together, so claiming the fragment shreds a chain a single lane serves
        # whole. Claim only units that begin a request of their own.
        if re.match(r"^\s*(?:then|and|also|next|afterwards|after\s+that|finally)\b", str(unit_text or ""), re.IGNORECASE):
            return False
        # The builder's OWN doors, per unit: an agentic build ("Create a small Python project called
        # StormWatch with a README and one app.py file") or an explicit file request is this
        # family's request even when its content is a brief, so a whole-message binder of another
        # lane cannot claim it as a step of its own.
        try:
            from core.agent_runtime.fast_paths_builder import looks_like_explicit_workspace_file_request
            from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

            if looks_like_agentic_build_request(str(unit_text or "")) or looks_like_explicit_workspace_file_request(str(unit_text or "")):
                return True
        except Exception:
            pass
        demand = resolve_write_demand(str(unit_text or ""))
        if demand is None or not demand.is_literal:
            return False
        # A unit that ALSO carries a folder bootstrap ("Create a folder named X. Inside it
        # create notes.txt ...") is one composite workspace workflow: the builder's
        # whole-turn workflow serves the bootstrap, the writes AND the trailing listing, so
        # no truncation is prevented by claiming the write slice and shredding the rest to
        # uncovered units. Claim only pure write units.
        return not demand.directory
    except Exception:
        return False


def _clock_covers(unit_text: str) -> bool:
    """Whether the clock lane claims THIS unit as a runtime-fact read.

    The clock is a fact the runtime holds and no model does — the lane's own
    words. The probe re-uses the lane's OWN detector pieces (`_ASKS_DATE_RE`,
    `_time_word_is_the_clock`, `extract_utility_timezone`) rather than a second
    recognizer, so the capability can never drift from the lane it speaks for.
    A SUPPLIED absolute time ("when it is 3:00 PM in New York") is a conversion
    the clock lane declines, and so is an unresolvable place — both already
    decline inside the lane, and the probe agrees by refusing to claim.
    """
    text = str(unit_text or "").strip().lower()
    if not text:
        return False
    cleaned = text.strip(" \t\r\n?!.,")
    try:
        from core.agent_runtime.fast_paths_utility import (
            _ASKS_DATE_RE,
            _EVENT_TIME_RE,
            _time_word_is_the_clock,
            extract_utility_timezone,
        )

        timezone, _label = extract_utility_timezone(cleaned)
        has_time_word = _time_word_is_the_clock(cleaned, has_timezone=bool(timezone))
        asks_date = bool(_ASKS_DATE_RE.search(cleaned))
        asks_time = bool(
            any(
                marker in cleaned
                for marker in (
                    "what time is it",
                    "what's the time",
                    "current time",
                    "time now",
                    "what time is now",
                    "what time now",
                )
            )
            or (
                has_time_word
                and any(
                    marker in cleaned
                    for marker in ("what", "now", "current", "right now")
                )
                and not _EVENT_TIME_RE.search(cleaned)
            )
            or (bool(timezone) and has_time_word)
        )
        return asks_date or asks_time
    except Exception:
        return False


#: The capability implementations the catalog's coverage names resolve to. Lazy by
#: construction — the functions above import their heavy dependencies inside the call.
def _workspace_audit_covers(unit_text: str) -> bool:
    """The audit lane's OWN door (`looks_like_code_audit_request`) read per unit. The lane admitted
    "check my project, see how many monolith files it has" while the catalog named no lane for it,
    so the whole-turn law read three unowned units and the model answered with no files read
    (census 2026-09-07, `LANE_ADMITS_CATALOG_BLIND`)."""
    try:
        from core.agent_runtime.workspace_audit import looks_like_code_audit_request

        return bool(looks_like_code_audit_request(str(unit_text or "")))
    except Exception:
        return False


def _machine_fact_covers(unit_text: str) -> bool:
    """The machine-fact lane's OWN gate (`local_fact_capability_required`): a this-machine FACT
    question that must be tool-backed -- largest files, disk consumers, free space."""
    try:
        from core.execution.constants import local_fact_capability_required

        return local_fact_capability_required(str(unit_text or "")) is not None
    except Exception:
        return False


def _operator_action_covers(unit_text: str) -> bool:
    """Ask the operator lane's own parser before it may claim a demand."""
    from core.operator.parser import parse_operator_action_intent

    return parse_operator_action_intent(str(unit_text or "")) is not None


_COVERAGE_CAPABILITIES: dict[str, Any] = {
    "live_data": _live_data_covers,
    "currency": _currency_covers,
    "workspace_read": _workspace_read_covers,
    "workspace_write": _workspace_write_covers,
    "workspace_audit": _workspace_audit_covers,
    "machine_fact": _machine_fact_covers,
    "arithmetic": _arithmetic_covers,
    "clock": _clock_covers,
    "operator_action": _operator_action_covers,
}

#: The pure-injection seam for NOVEL capabilities (tests, embedders). Empty means
#: "only the production table is in force".
_CAPABILITY_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar(
    "lane_coverage_capability_scope", default=None
)


#: THE FAMILY BINDERS -- a coverage capability's reading of a WHOLE request at the MINT grain,
#: beside the per-unit probe above. A probe answers "does this family read THIS fragment as its
#: work?"; a binder answers "over the whole text, which minted units does this family's own plan
#: serve?". The two differ exactly where a coordination shares a predicate: no probe reads a bare
#: "gold" as a quote request, but the live family's typed plan over "gold and silver price" binds
#: `gold` to u1 and `silver` to u2 -- the plan is the family's own reader at unit grain
#: (`core.live_data_plan._bind_unit_ids`), not a second recognizer. Keyed by capability NAME so
#: the catalog stays the only lane list; a capability with no binder is read by its probe alone.
#: Scoped (injected) capabilities never get a production binder: an injected reading is the
#: whole reading.
def _live_data_binds(text: str, units: Any) -> frozenset[str]:
    """The MINT units the live family's own typed plan binds a subtask to, over the whole text."""
    minted = tuple(units or ())
    if len(minted) < 2:
        return frozenset()
    from core.agent_runtime.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(
        str(text or ""),
        plan_id="coverage-binder",
        attempt_id="coverage-binder",
        canonical_units=minted,
    )
    if plan is None:
        return frozenset()
    return frozenset(
        str(unit_id)
        for task in getattr(plan, "subtasks", ()) or ()
        for unit_id in (getattr(task, "unit_ids", ()) or ())
    )


#: Binder verdicts: KIND "request" (the lane serves this unit), "step" (a request chained to the
#: previous step the same binder bound -- a workflow's run / replace / retry), or a non-request
#: kind ("context", "enumeration", "literal") that DEMOTES a fragment the heuristics minted as a
#: request but the lane reads as its own material (a travel story's narration, a write's values).
BIND_STEP = "step"


def _workspace_audit_binds(text: str, units: Any) -> dict[str, str]:
    """A message the audit lane admits WHOLE: every request-shaped fragment and every imperative
    step is a facet of one inspection, answered in one report ("check my project, see how many
    monolith files it has and suggest which ones can be split")."""
    from core.agent_runtime.answer_coverage import _ANAPHORS, KIND_REQUEST, _unit_tokens, imperative_step
    from core.agent_runtime.workspace_audit import (
        _AUDIT_SCOPE_DEMONSTRATIVE_RE,
        _CODE_STRUCTURE_EVIDENCE_RE,
        looks_like_code_audit_request,
    )

    body = str(text or "")
    if not looks_like_code_audit_request(body):
        return {}
    out: dict[str, str] = {}
    bound_before = False
    for unit in units:
        if getattr(unit, "kind", KIND_REQUEST) != KIND_REQUEST and not imperative_step(unit.text):
            continue
        fragment = str(unit.text or "")
        admitted = looks_like_code_audit_request(fragment)
        tied = bound_before and (
            bool(_CODE_STRUCTURE_EVIDENCE_RE.search(fragment))
            or bool(_AUDIT_SCOPE_DEMONSTRATIVE_RE.search(fragment))
            or any(token in _ANAPHORS for token in _unit_tokens(fragment))
        )
        if admitted or (tied and imperative_step(fragment)):
            out[unit.unit_id] = BIND_STEP
            bound_before = True
        else:
            bound_before = bound_before and not opens_a_fresh_request_safe(fragment)
    return out


def opens_a_fresh_request_safe(fragment: str) -> bool:
    try:
        from core.agent_runtime.answer_coverage import opens_a_fresh_request

        return bool(opens_a_fresh_request(fragment))
    except Exception:
        return False


def _build_specification_blocks(text: str) -> tuple[tuple[int, int], ...]:
    """Lists introduced by a heading belong to the leading project instruction.

    Keep source spans: a command described in a bot's requirements is material,
    not a command to execute now. Unheaded prose outside a list stays independent.
    """
    from core.agent_runtime.builder.mutation_scope import object_phrase_widens

    first = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    if not object_phrase_widens(first):
        return ()
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    spans: list[tuple[int, int]] = []
    list_item = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
    for index, line in enumerate(lines):
        if not line.rstrip().endswith(":") or list_item.match(line):
            continue
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor == len(lines) or not list_item.match(lines[cursor]):
            continue
        while cursor < len(lines):
            child = lines[cursor]
            if child.strip() and not (list_item.match(child) or child[0].isspace()):
                break
            cursor += 1
        end = offsets[cursor] if cursor < len(lines) else len(text)
        spans.append((offsets[index], end))
    return tuple(spans)


def _workspace_write_binds(text: str, units: Any) -> dict[str, str]:
    """The builder's OWN admission over the whole message. A LITERAL write ("Create exactly three
    files: a.txt, b.txt, c.txt. Put ONE, TWO, THREE respectively.") is one request whose other
    fragments are its values and file names; an explicit-file or agentic workflow ("run
    `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry") is a chain of steps the
    builder runs with the retry history intact. Neither is a set of independent demands."""
    from core.agent_runtime.answer_coverage import KIND_CONSTRAINT, KIND_ENUMERATION, KIND_REQUEST, imperative_step

    body = str(text or "")
    literal = None
    try:
        from core.execution.write_demand import resolve_write_demand

        demand = resolve_write_demand(body)
        if demand is not None and getattr(demand, "is_literal", False) and demand.write_payloads():
            literal = demand
    except Exception:
        literal = None
    try:
        from core.agent_runtime.fast_paths_builder import looks_like_explicit_workspace_file_request
        from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

        workflow = looks_like_explicit_workspace_file_request(body) or looks_like_agentic_build_request(body)
    except Exception:
        workflow = False
    from core.agent_runtime.answer_coverage import KIND_CONTEXT, opens_a_fresh_request

    out: dict[str, str] = {}
    material = _write_material_tokens(literal) if literal is not None else set()
    if not workflow and literal is None:
        # The builder's own reading of the WHOLE message: "I want to build a community bot ...
        # How would you design this system?" names the subject of a design question, not a job.
        # The per-unit door still admits the opener on its own words, so the family says here
        # that the opener is narration of this message. Measured 2026-09-16 (candidate 035dea9b):
        # the opener was claimed, the turn read mixed, and the builder ran the first sentence as
        # a sub-turn against the pinned paid model.
        try:
            from core.agent_runtime.build_request_intent import intention_under_deliberation
        except Exception:
            intention_under_deliberation = None  # type: ignore[assignment]
        if intention_under_deliberation is not None:
            for unit in units:
                if getattr(unit, "kind", KIND_REQUEST) != KIND_REQUEST:
                    continue
                fragment = str(unit.text or "")
                if _workspace_write_covers(fragment) and intention_under_deliberation(fragment, whole_text=body):
                    out[unit.unit_id] = KIND_CONTEXT
            if out:
                return out
    if workflow:
        # The whole message is the builder's: every imperative fragment is a step of ONE job
        # ("Create a folder named X. Inside it create notes.txt with the line first note. Then
        # create summary.txt ... Then list the folder contents."); a fragment that only carries a
        # step's value or file name is that step's material. Decided before the literal reading
        # below, which would otherwise keep the first step and leave the rest as loose demands
        # (measured 2026-09-08: the chain read as MIXED and fell to advice).
        from core.agent_runtime.answer_coverage import _LOCATIVE_LEADER_RE

        specification_blocks = _build_specification_blocks(body)
        lead_taken = False
        for unit in units:
            kind = getattr(unit, "kind", KIND_REQUEST)
            if kind == KIND_CONSTRAINT:
                continue
            fragment = str(unit.text or "")
            placed = _LOCATIVE_LEADER_RE.sub("", fragment, count=1)
            if any(start <= unit.start < end for start, end in specification_blocks):
                out[unit.unit_id] = KIND_ENUMERATION
            elif kind == KIND_REQUEST and not lead_taken:
                # The first request is the job's head whatever its wording.
                out[unit.unit_id] = KIND_REQUEST
                lead_taken = True
            elif material and kind == KIND_REQUEST and _is_write_material(fragment, material):
                # A literal write's values and file names ("Put ONE, TWO, THREE respectively.") are
                # its material even when they open with a verb.
                out[unit.unit_id] = KIND_ENUMERATION
            elif (
                kind == KIND_REQUEST
                and re.match(r"^\s*(?:and|or|plus)\b", fragment, re.IGNORECASE)
                and _TARGET_FILE_RE.search(fragment)
            ):
                # "write a Trie class with insert and search into trie.py": the coordinated tail
                # names the build's TARGET file -- its material, whatever verb starts it.
                out[unit.unit_id] = KIND_ENUMERATION
            elif kind == KIND_REQUEST and _prose_head(fragment) and not _FILE_TOKEN_RE.search(fragment):
                # "run pytest and explain what a fixture is": the explanation is a demand of its
                # own, not a step of the job. Left to its own lane.
                continue
            elif imperative_step(fragment) or opens_a_fresh_request(fragment) or (
                placed != fragment and opens_a_fresh_request(placed)
            ):
                # A step, including one placed by a locative lead-in ("Inside it create notes.txt").
                out[unit.unit_id] = BIND_STEP
            elif kind == KIND_REQUEST and _FILE_TOKEN_RE.search(fragment):
                out[unit.unit_id] = KIND_ENUMERATION
        return out
    if literal is not None:
        lead_taken = False
        for unit in units:
            kind = getattr(unit, "kind", KIND_REQUEST)
            if kind == KIND_CONSTRAINT:
                continue
            if kind == KIND_REQUEST and not lead_taken:
                out[unit.unit_id] = KIND_REQUEST
                lead_taken = True
            elif kind == KIND_REQUEST and _is_write_material(unit.text, material):
                out[unit.unit_id] = KIND_ENUMERATION
        return out
    # A build inside a mixed message ("... Create a small Python project called StormWatch with a
    # README and one app.py file."): the fragments coordinated to the admitted build that only name
    # its files are the build's own list, not demands of their own. A fragment that opens a fresh
    # request ends the list.
    in_build = False
    for unit in units:
        kind = getattr(unit, "kind", KIND_REQUEST)
        if kind == KIND_CONSTRAINT:
            continue
        fragment = str(unit.text or "")
        if kind == KIND_REQUEST and _workspace_write_covers(fragment):
            in_build = True
            continue
        if not in_build:
            continue
        if (
            kind == KIND_REQUEST
            and re.match(r"^\s*(?:and|or|plus)\b", fragment, re.IGNORECASE)
            and _TARGET_FILE_RE.search(fragment)
        ):
            # "write a Trie class with insert and search into trie.py": the coordinated tail names
            # the build's own TARGET file ("into trie.py") -- material of the build, whatever verb
            # it happens to start with ("search" is a method here, not a demand). "and then run
            # app.py" names no target and stays the step it is.
            out[unit.unit_id] = KIND_ENUMERATION
            continue
        if imperative_step(fragment):
            in_build = False
            continue
        if opens_a_fresh_request(fragment):
            in_build = False
            continue
        if kind == KIND_REQUEST and _FILE_TOKEN_RE.search(fragment):
            out[unit.unit_id] = KIND_ENUMERATION
        else:
            in_build = False
    return out


_FILE_TOKEN_RE = re.compile(r"\b[\w./-]+\.(?:py|js|ts|tsx|md|txt|json|yaml|yml|toml|html|css|sh)\b")
_PROSE_HEAD_RE = re.compile(
    r"^\s*(?:and\s+|then\s+|also\s+|plus\s+)?(?:explain|describe|tell\s+me|summari[sz]e|compare|what|why|how|when|where|which|who)\b",
    re.IGNORECASE,
)


def _prose_head(fragment: str) -> bool:
    """A fragment that asks for an explanation or an answer in words, not for an effect."""
    return bool(_PROSE_HEAD_RE.match(str(fragment or "")))


_TARGET_FILE_RE = re.compile(
    r"\b(?:into|in|to|inside|under|at)\s+(?:the\s+)?[\w./-]+\.(?:py|js|ts|tsx|md|txt|json|yaml|yml|toml|html|css|sh)\b",
    re.IGNORECASE,
)


_WRITE_MATERIAL_WORDS = frozenset(
    {"put", "with", "respectively", "and", "containing", "content", "contents", "text", "into", "in",
     "each", "the", "them", "file", "files", "to", "of", "a", "an", "then", "also"}
)


def _write_material_tokens(demand: Any) -> set[str]:
    """The tokens a literal write's own parse consists of: its file names and its contents."""
    tokens: set[str] = set()
    for item in getattr(demand, "items", ()) or ():
        path = str(getattr(item, "path", "") or "")
        for part in re.findall(r"[a-z0-9_.\-/]+", path.casefold()):
            tokens.add(part)
            tokens.add(part.rsplit("/", 1)[-1])
        for part in re.findall(r"[a-z0-9_.\-]+", str(getattr(item, "content", "") or "").casefold()):
            tokens.add(part)
    return tokens


def _is_write_material(unit_text: str, material: set[str]) -> bool:
    """A fragment whose every content token is one of the write's file names, contents or
    connective words ("b.txt", "Put ONE, TWO", "THREE respectively.") is the write's material,
    never a demand of its own."""
    words = [
        word.strip(".,;:")
        for word in re.findall(r"[a-z0-9_.\-/]+", str(unit_text or "").casefold())
        if word.strip(".,;:")
    ]
    return bool(words) and all(word in material or word in _WRITE_MATERIAL_WORDS for word in words)


#: Imperatives a travel story asks of the lane that parses it whole: "Name both", "work out what
#: is left", "compute the remainder", "convert it". A prose head about another subject ("describe
#: how a diesel engine works") is never one of these.
_TRAVEL_IMPERATIVE_HEADS = frozenset(
    {"name", "calculate", "compute", "work", "figure", "convert", "determine", "give", "show", "tell", "find", "state"}
)


def _currency_binds(text: str, units: Any) -> dict[str, str]:
    """The travel story the typed currency contract parses WHOLE: its question is the request,
    its narration ("I hold 2,500 units ... and fly to Bangkok ... With 1 EUR = 38 THB") is context."""
    from core.agent_runtime.answer_coverage import KIND_CONTEXT, KIND_REQUEST
    from core.currency_travel_spend import travel_spend_intent

    body = str(text or "")
    if travel_spend_intent(body) is None:
        return {}
    from core.agent_runtime.answer_coverage import imperative_step, opens_a_fresh_request

    out: dict[str, str] = {}
    for unit in units:
        if getattr(unit, "kind", KIND_REQUEST) != KIND_REQUEST:
            continue
        lowered = str(unit.text or "").casefold()
        asks = lowered.rstrip().endswith("?") or bool(
            re.search(r"\b(?:how much|how many|what remains|what is left|what do i have|left over|remaining|enough|name the currenc)", lowered)
        )
        # An imperative of the story's own arithmetic ("Name both currencies", "and calculate the
        # deficit or remainder", "work out what is left in CAD") is the lane's request as much as
        # its question is; an unrelated head-bearing request stays its own demand.
        money_work = bool(
            re.search(
                r"\b(?:currenc(?:y|ies)|money|remainder|deficit|left(?:\s+over)?|convert|exchange|rate|cost|pay|afford|spend|"
                r"buy|budget|dollars?|euros?|pounds?|kron[ae]r?|units?)\b",
                str(unit.text or ""),
                re.IGNORECASE,
            )
            or re.search(r"\b[A-Z]{3}\b", str(unit.text or ""))  # an ISO code, case-sensitive
        )
        head_tokens = re.findall(r"[a-z]+", lowered)
        head = next((tok for tok in head_tokens if tok not in {"and", "then", "also", "please", "now", "so"}), "")
        travel_imperative = head in _TRAVEL_IMPERATIVE_HEADS and not re.match(
            r"^\s*(?:and\s+|then\s+|also\s+)?(?:explain|describe|summari[sz]e)\b", lowered
        )
        if asks or ((money_work or travel_imperative) and (opens_a_fresh_request(unit.text) or imperative_step(unit.text) or travel_imperative)):
            out[unit.unit_id] = KIND_REQUEST
        elif not opens_a_fresh_request(unit.text) and not imperative_step(unit.text):
            # narration a lane's probe happened to admit ("With 1 EUR = 38 THB"); a head-bearing
            # request ("explain the steps of photosynthesis") is its own demand and is left alone
            out[unit.unit_id] = KIND_CONTEXT
    return out


def _operator_action_binds(text: str, units: Any) -> frozenset[str]:
    """The units the OPERATOR lane's own reading of the WHOLE text takes as its arguments.

    Follow-up references are the reason this binder exists. 'option 1, propose
    "Project review"' mints two units -- the bare reference and the proposal -- and the
    reference parses as nothing on its own, so the per-unit probe cannot claim it and
    the turn fell to the mixed executor, which executed each fragment apart from its
    reference. The operator lane reads the WHOLE request (that is its authority), so the
    binder asks that reading which fragments it takes: the offered slot, a folder or an
    account, a title, a payload, a due time.

    MEASURED 2026-09-15 (in-process, production catalog, 1d58a641): this binder returned EVERY
    minted unit whenever the parser claimed the whole text. 'open my Apple note "Plan", Ukraine
    situation' and 'Ukraine situation, open my Apple note "Plan"' became one execution unit only
    the operator lane read, so it could end the turn alone and the topic vanished. A fragment is
    bound only when the lane's reading depends on it: blanking it changes what the lane would read
    (`core.operator.request_reading.fragments_the_request_reads`).
    """
    minted = tuple(units or ())
    if len(minted) < 2:
        return frozenset()
    from core.operator.request_reading import fragments_the_request_reads

    read = fragments_the_request_reads(
        str(text or ""), [(int(getattr(unit, "start", 0)), int(getattr(unit, "end", 0))) for unit in minted]
    )
    return frozenset(
        str(getattr(unit, "unit_id", "") or "") for unit, reads in zip(minted, read, strict=True) if reads
    )


def _sequence_arithmetic_binds(text: str, units: Any) -> dict[str, str]:
    """A closed sequence computation owns its declaration, steps and result ask."""
    from core.agent_runtime.answer_coverage import imperative_step
    from core.task_router import evaluate_sequence_ops_request

    if evaluate_sequence_ops_request(text) is None:
        return {}
    return {
        unit.unit_id: BIND_STEP for unit in units
        if getattr(unit, "kind", "request") == "request" or imperative_step(unit.text)
    }


_COVERAGE_BINDERS: dict[str, Any] = {
    "arithmetic": _sequence_arithmetic_binds,
    "live_data": _live_data_binds,
    "workspace_audit": _workspace_audit_binds,
    "workspace_write": _workspace_write_binds,
    "currency": _currency_binds,
    "operator_action": _operator_action_binds,
}


#: Coverage names that are ONE domain for the whole-message rule: a per-unit claim by the read
#: probe on "b.txt" or "run `python3 app.py`" must not block the write/workflow binder that owns
#: the message they are fragments of. Mirrors `answer_coverage._FAMILY_DOMAIN_GROUPS`.
_COVERAGE_DOMAIN_GROUPS: dict[str, str] = {
    "workspace_read": "workspace",
    "workspace_write": "workspace",
    "workspace_audit": "workspace",
}


def _foreign_claims(claimed: set[str], coverage: str, unit_text: str = "", verdict: str = "request") -> set[str]:
    """Claims by other lanes that keep a whole-message binder off a fragment. A fragment that opens
    a request of its own ("Create a small Python project called StormWatch ...") belongs to whichever
    lane's door claims it, same domain group or not; a headless fragment ("b.txt", "then retry") is
    only kept off by a claim from another domain. A fragment the binder reads as its own MATERIAL or
    context ("and search into trie.py" -- the build's method list and target file) is likewise
    only kept off by another domain: the read probe's claim on the write's own file name is the
    very overlap the domain groups exist for."""
    from core.agent_runtime.answer_coverage import KIND_REQUEST, imperative_step, opens_a_fresh_request

    others = {name for name in claimed if name != coverage}
    demotes = str(verdict or KIND_REQUEST) not in (KIND_REQUEST, BIND_STEP)
    if unit_text and not demotes and (opens_a_fresh_request(unit_text) or imperative_step(unit_text)):
        return others
    own = _COVERAGE_DOMAIN_GROUPS.get(coverage, coverage)
    return {name for name in others if _COVERAGE_DOMAIN_GROUPS.get(name, name) != own}


def whole_message_bindings(text: str, units: Any) -> dict[str, str]:
    """Every binder's verdict on the interpretation's fragments, keyed by unit id, in catalog
    precedence order (the first lane to bind a fragment keeps it). A fragment another lane's own
    per-unit door claims is never bound by a whole-message lane: "Audit this project. Also what's
    the weather in Berlin?" keeps its weather unit for the live lane."""
    from core.lane_registry import active_catalog

    minted = tuple(units or ())
    probes = [
        (spec.lane_id, spec.coverage, impl)
        for spec in active_catalog()
        if (impl := _capability_impl(spec.coverage)) is not None
    ]
    claimed_by: dict[str, set[str]] = {}
    texts: dict[str, str] = {unit.unit_id: str(unit.text or "") for unit in minted}
    for unit in minted:
        if getattr(unit, "kind", "request") != "request":
            continue
        claimed_by[unit.unit_id] = {
            coverage for _lane_id, coverage, probe in probes if _probe_claims(probe, unit.text)
        }
    out: dict[str, str] = {}
    for spec in active_catalog():
        binder = _capability_binder(spec.coverage)
        if binder is None:
            continue
        try:
            bound = binder(str(text or ""), minted)
        except Exception:
            continue
        if isinstance(bound, (set, frozenset, list, tuple)):
            bound = {str(unit_id): "request" for unit_id in bound}
        for unit_id, kind in dict(bound or {}).items():
            if _foreign_claims(claimed_by.get(unit_id, set()), spec.coverage, texts.get(unit_id, ""), str(kind)):
                continue
            out.setdefault(str(unit_id), str(kind))
    return out


def binding_lanes(text: str, units: Any) -> dict[str, frozenset[str]]:
    """Which lanes' binders bound each unit -- the coverage a whole-message admission contributes."""
    from core.lane_registry import active_catalog

    minted = tuple(units or ())
    probes = [
        (spec.coverage, impl)
        for spec in active_catalog()
        if (impl := _capability_impl(spec.coverage)) is not None
    ]
    claimed_by: dict[str, set[str]] = {}
    texts: dict[str, str] = {unit.unit_id: str(unit.text or "") for unit in minted}
    for unit in minted:
        if getattr(unit, "kind", "request") != "request":
            continue
        claimed_by[unit.unit_id] = {coverage for coverage, probe in probes if _probe_claims(probe, unit.text)}
    lanes: dict[str, set[str]] = {}
    for spec in active_catalog():
        binder = _capability_binder(spec.coverage)
        if binder is None:
            continue
        try:
            bound = binder(str(text or ""), minted)
        except Exception:
            continue
        if isinstance(bound, (set, frozenset, list, tuple)):
            bound = {str(unit_id): "request" for unit_id in bound}
        for unit_id, kind in dict(bound or {}).items():
            if str(kind) not in ("request", BIND_STEP):
                continue
            if _foreign_claims(claimed_by.get(unit_id, set()), spec.coverage, texts.get(unit_id, "")):
                continue
            lanes.setdefault(str(unit_id), set()).add(spec.lane_id)
    return {unit_id: frozenset(ids) for unit_id, ids in lanes.items()}


def _capability_binder(name: str):
    """The whole-text binder for a PRODUCTION capability, or None (no binder, a reserved
    declaration, or an injected capability -- whose probe is its whole reading)."""
    wanted = str(name or "")
    scoped = _CAPABILITY_SCOPE.get()
    if scoped is not None and scoped.get(wanted) is not _COVERAGE_CAPABILITIES.get(wanted):
        return None
    return _COVERAGE_BINDERS.get(wanted)


def _mint_unit_service(text: str, units: Any) -> tuple[dict[str, frozenset[str]], bool]:
    """Which catalog lanes' OWN readers serve each MINT unit: the per-unit probe, plus the
    family's binder over the whole text.

    Returns ``(served, readers_intact)``. A probe that raises reads as "does not claim", like
    `_probe_claims` everywhere else. A BINDER that raises is different: the family's whole-text
    reading is UNAVAILABLE, not empty, and a grain that treated it as empty would split a
    same-family coordination ("price for bitcoin and ethereum") into two demands on the strength
    of a reader that failed -- measured: the fail-closed invariant test patches the plan builder
    to raise, and the turn left the live lane's own typed refusal for a demand-owned split.
    ``readers_intact`` is False in that case, and `execution_unit_spans` keeps the pre-binder
    behaviour (every headless fragment rides) for the turn: accounting may never break the lane
    it accounts for."""
    from core.lane_registry import active_catalog

    probes: list[tuple[str, Any]] = []
    bound: dict[str, frozenset[str]] = {}
    readers_intact = True
    for spec in active_catalog():
        impl = _capability_impl(spec.coverage)
        if impl is None:
            continue
        probes.append((spec.lane_id, impl))
        binder = _capability_binder(spec.coverage)
        if binder is None:
            continue
        try:
            bound[spec.lane_id] = frozenset(binder(text, units))
        except Exception:
            readers_intact = False
    served: dict[str, frozenset[str]] = {}
    for unit in units:
        lanes = {lane_id for lane_id, probe in probes if _probe_claims(probe, unit.text)}
        lanes.update(lane_id for lane_id, ids in bound.items() if unit.unit_id in ids)
        served[unit.unit_id] = frozenset(lanes)
    return served, readers_intact


#: Shapes that CONTINUE the request before them rather than opening one of their own. Read from
#: the fragment's own grammar, never from a topic list: an elliptical continuation opens with a
#: preposition ("... and then TO gold", "..., IN celsius please") or points back at the request
#: with a pronoun ("gold WITH IT", "and ONE app.py file"). A bare noun phrase with neither
#: ("and oil prices", "and Ukraine situation") stands on its own.
_CONTINUATION_PREPOSITIONS = frozenset(
    {
        "to", "in", "at", "for", "from", "with", "of", "on", "by", "into", "about", "via",
        "per", "against", "under", "over", "as", "than", "vs", "versus",
    }
)
_CONTINUATION_ANAPHORS = frozenset(
    {"it", "its", "that", "this", "them", "those", "these", "there", "one", "ones", "same"}
)


def _continues_the_request_before_it(fragment: str) -> bool:
    """Whether a headless fragment is, by its own shape, a continuation of what precedes it."""
    from core.agent_runtime.answer_coverage import (
        _DEMAND_UNIT_SPLIT_CONNECTORS,
        _SPLIT_LEADERS,
        _unit_tokens,
    )

    tokens = _unit_tokens(str(fragment or ""))
    lead = 0
    while lead < len(tokens) and (
        tokens[lead] in _DEMAND_UNIT_SPLIT_CONNECTORS or tokens[lead] in _SPLIT_LEADERS
    ):
        lead += 1
    body = tokens[lead:]
    if not body:
        return True
    if body[0] in _CONTINUATION_PREPOSITIONS:
        return True
    if len(body) == 1:
        # A bare request verb ("and compare", "and summarize") carries no object of its own: it
        # is the predicate applied to what the request before it named. The mint grain reads it
        # the same way (`answer_coverage._opens_a_demand`), so the two grains agree.
        from core.agent_runtime.answer_coverage import _is_demand_head

        if _is_demand_head(body[0]):
            return True
    return any(token in _CONTINUATION_ANAPHORS for token in body)


def _rides_the_request_before_it(
    unit: Any, group: list[Any], served: dict[str, frozenset[str]], riders: frozenset[str]
) -> bool:
    """THE EXECUTION-BOUNDARY LAW for a fragment that opens no demand head (R1e, amended).

    MEASURED (served, f8937f80; validation-logs/two-intent-routing-20260907): "get me latest on
    Iran and oil prices pls" minted two units, but the grain fused the headless "oil prices"
    fragment into the Iran request on the reading that every headless fragment is a
    continuation. Coverage then saw ONE unit, the live-data lane was allowed to end the turn,
    its plan bound only the oil unit, and the news half surfaced as "not dispatched" under the
    quote -- reported by the census, never routed. The same intent with a question-shaped
    second clause split and served both halves: a class defect in the grain, not a wording.

    A headless fragment rides the request before it only when it is a continuation OF it:

    * a rider (names no thing of its own -- "and more detail please");
    * an elliptical continuation by shape (`_continues_the_request_before_it`);
    * a fragment the SAME family serves -- read through the family's own probe and binder, so
      "gold" + "and silver price" is one typed plan, and "weather in Rome" + "and the Baltic
      water temp" is one live family;
    * general prose beside general prose -- no lane reads either side, and one answering lane
      keeps the context whole (the R1g pasted-text and "explain X and draft Y" shapes).

    A fragment one family serves beside a request that family does NOT serve is a second
    request, on either side of the conjunction ("oil prices and the latest on Iran" splits the
    same way), and so is an unserved, object-bearing fragment beside a served request
    ("bitcoin price and Ukraine situation"). Both then read `mixed`, the demand-owned plan
    executes each unit through its owning lane, and no lane may end the turn alone.
    """
    if unit.unit_id in riders:
        return True
    if _continues_the_request_before_it(unit.text):
        return True
    own = served.get(unit.unit_id, frozenset())
    carried: frozenset[str] = frozenset().union(
        *(served.get(member.unit_id, frozenset()) for member in group)
    )
    if own:
        return bool(own & carried)
    return not carried


def _opens_as_a_coordinate(fragment: str) -> bool:
    """Whether a fragment opens as a COORDINATE of what precedes it: one of the connectors the mint
    itself cuts at ("and", "also", "plus", "then") stands before its head.

    Politeness and discourse leaders ("please", "just", "now") coordinate nothing. The connectors
    are the mint's own (`answer_coverage._DEMAND_UNIT_SPLIT_CONNECTORS`, `_SEQUENCE_CONNECTORS`),
    read under the mint's one-typo budget for four-letter words ("thne"), so both grains read one
    boundary.
    """
    from core.agent_runtime.answer_coverage import (
        _DEMAND_UNIT_SPLIT_CONNECTORS,
        _SEQUENCE_CONNECTORS,
        _SPLIT_LEADERS,
        _near_miss,
        _unit_tokens,
    )

    connectors = (*_DEMAND_UNIT_SPLIT_CONNECTORS, *_SEQUENCE_CONNECTORS)
    for token in _unit_tokens(str(fragment or "")):
        if token in connectors or (
            len(token) >= 4 and any(_near_miss(token, word) for word in connectors if len(word) >= 4)
        ):
            return True
        if token not in _SPLIT_LEADERS and not (
            len(token) >= 4 and any(_near_miss(token, leader) for leader in _SPLIT_LEADERS if len(leader) >= 4)
        ):
            return False
    return False


def _leads_into_the_request_after_it(
    group: list[Any], unit: Any, served: dict[str, frozenset[str]]
) -> bool:
    """THE EXECUTION-BOUNDARY LAW for fragments that LEAD a request (R1e, amended 2026-09-15).

    MEASURED (served, d6a398af and 4f18cfdb; `VoolAgent.run_once`, model stand-in, synthetic
    Notes runner): 'in the Personal folder, open my Apple note "Plan"' minted two request units.
    "open" is a demand head, so a boundary opened before it, and the folder fragment -- which
    opens no demand and which no lane serves on its own -- became an execution unit of its own.
    The turn ended as `deterministic:demand_owned_mixed_turn` ('open my Apple note "Plan" (could
    not be completed)') and the Notes owner was never reached. Worded with a verb that is not a
    head ('in the Work folder, rename ...') the same scope rode `_rides_the_request_before_it`
    and reached Notes: a class defect in the grain, not a wording. That law only looks back, and
    a leading fragment has no request before it.

    A group that holds no request of its own -- no member opened a demand -- rides the request
    that opens right after it when the readers read it as part of that request, never as a
    second one:

    * the group sits in the request's own sentence (its slice): a sentence that ends before the
      request is no fronted part of it ("About the Iran deal. What is the gold price?" is a topic
      beside a quote);
    * the request does not open as a coordinate of it (`_opens_as_a_coordinate`): "about the
      Iran situation, and what is the gold price" is two requests;
    * no lane serves a member on its own (`a_registered_lane_claims`): "for 100 EUR in USD, what
      is the gold price" is a conversion beside a quote;
    * and then either every member FRONTS the request -- it opens with a preposition
      (`_fronts_an_adjunct`: "for tomorrow, what is the weather in Rome") -- or a family that
      serves the request binds the group too over the whole text ('Personal folder, open my
      Apple note "Plan"' is one request to the operator lane's own parser).

    Deliberately narrower than the look-back law, measured over the suite's own wordings
    (tests/execution_grain_census.py). A leading clause that only carries a pronoun is a request
    of its own ("draft a limerick about it, what is the gold price"), so the anaphor reading does
    not front anything. General prose beside general prose is not batched forward: every such
    merge the census found was read by no lane before or after, so it changed no route. A bare
    noun phrase beside a request whose family does not bind it ("Ukraine situation, what is the
    bitcoin price") stays apart: the turn reads `mixed` and no lane may end it alone.
    """
    from core.agent_runtime.answer_coverage import opens_a_fresh_request

    if any(getattr(member, "slice_id", "") != getattr(unit, "slice_id", "") for member in group):
        return False
    if any(opens_a_fresh_request(member.text) or _output_verb_names_real_work(member.text) for member in group):
        return False
    if _opens_as_a_coordinate(unit.text):
        return False
    if any(a_registered_lane_claims(member.text) for member in group):
        return False
    if all(_fronts_an_adjunct(member.text) for member in group):
        return True
    carried: frozenset[str] = frozenset().union(
        *(served.get(member.unit_id, frozenset()) for member in group)
    )
    return bool(carried & served.get(unit.unit_id, frozenset()))


def _opens_a_sequenced_step(fragment: str) -> bool:
    """Whether a fragment opens a next STEP of the request before it: a sequencing
    connector of the mint's own ("then") stands in its leading run of connectors and
    leaders ("and then in the Personal folder", "then to gold"). Read under the mint's
    one-typo budget for four-letter words, exactly as `_opens_as_a_coordinate` reads the
    coordinating connectors, so both grains cut one boundary."""
    from core.agent_runtime.answer_coverage import (
        _DEMAND_UNIT_SPLIT_CONNECTORS,
        _SEQUENCE_CONNECTORS,
        _SPLIT_LEADERS,
        _near_miss,
        _unit_tokens,
    )

    leaders = (*_DEMAND_UNIT_SPLIT_CONNECTORS, *_SPLIT_LEADERS)
    for token in _unit_tokens(str(fragment or "")):
        if token in _SEQUENCE_CONNECTORS or (
            len(token) >= 4 and any(_near_miss(token, word) for word in _SEQUENCE_CONNECTORS)
        ):
            return True
        if token not in leaders and not (
            len(token) >= 4 and any(_near_miss(token, word) for word in leaders if len(word) >= 4)
        ):
            return False
    return False


def _begins_its_sentence(fragment: Any, units: Any) -> bool:
    """Whether a mint unit is the FIRST unit of its sentence (`slice_id`): no minted
    unit of the same slice stands before it. Read through `answer_coverage.turn_slices`
    via the slice id the mint itself assigned, so both grains read one boundary."""
    return not any(
        getattr(other, "slice_id", "") == getattr(fragment, "slice_id", "")
        and int(getattr(other, "start", 0)) < int(getattr(fragment, "start", 0))
        for other in units
    )


def _holds_a_request_of_its_own(member: Any, fresh_by_id: dict[str, bool]) -> bool:
    """Whether a mint member stands as a request: it opened a demand head, or a registered
    lane claims its text as its work ("gold price pls" opens no head, yet the live-data
    lane reads it -- a request of its own beside a scope fragment)."""
    return fresh_by_id.get(member.unit_id, False) or a_registered_lane_claims(member.text)


def _opens_the_clause_of_the_request_after_it(
    group: list[Any], unit: Any, served: dict[str, frozenset[str]], units: Any,
    fresh_by_id: dict[str, bool], request_before: bool,
) -> list[Any] | None:
    """THE EXECUTION-BOUNDARY LAW for a fragment BETWEEN two requests (2026-09-15).

    MEASURED (served, `VoolAgent.run_once`, model stand-in, synthetic Notes runner;
    grain probe identical at 4f18cfdb and 1d58a641): 'check the gold price, and in the
    Personal folder, open my Apple note "Plan"' grouped the folder fragment into the
    gold request on its shape alone (`_continues_the_request_before_it` reads a
    preposition-led fragment as a continuation of whatever precedes it), so the Notes
    owner received 'open my Apple note "Plan"' without its folder and, with three notes
    titled "Plan", answered 'open my Apple note "Plan" (could not be completed)'. The
    same wording across a sentence boundary crossed its sentence backwards.

    Between two requests the preposition shape reads BOTH ways -- a trailing adjunct of
    the request before it ('weather in Rome, in celsius please') or a fronted adjunct of
    the request after it -- so the shape does not decide. A run of fragments rides the
    request after it when it OPENS A CLAUSE of that request: its first fragment begins
    the request's own sentence (`turn_slices`), or it opens the coordinated conjunct the
    request continues (the mint's connectors; a sequencing leader opens a step of the
    request BEFORE it instead); every later member of the run fronts another adjunct of
    that clause; the family that serves the request binds every member over the whole
    text (its own binder, never a shape guess); and a request of its own stands before
    the run. Otherwise the look-back law decides, as before.

    Returns the longest suffix of `group` that rides `unit`, or None. Deliberately
    narrower than the look-back law: a comma-led fragment that opens no clause
    ('check the gold price, in the Personal folder, open ...'), a mistyped coordinator
    the mint's one-typo budget does not read ('annd'), and a fragment the request's
    family does not read ('and in celsius' beside a Notes request) all stay where the
    look-back law puts them.
    """
    if not request_before:
        return None
    # the trailing fragment run: the members after the group's last request
    cut = len(group)
    while cut > 0 and not _holds_a_request_of_its_own(group[cut - 1], fresh_by_id):
        cut -= 1
    run = group[cut:]
    if not run:
        return None
    for start in range(len(run)):
        candidate = run[start:]
        first = candidate[0]
        if getattr(first, "slice_id", "") != getattr(unit, "slice_id", ""):
            continue
        # the request must not open as a coordinate of the fragment
        # ("..., and what is the gold price?" is a conjunct of its own)
        if _opens_as_a_coordinate(unit.text):
            continue
        if _opens_a_sequenced_step(first.text):
            continue
        opens_the_clause = _begins_its_sentence(first, units) or _opens_as_a_coordinate(first.text)
        if not opens_the_clause:
            continue
        if any(
            _opens_a_sequenced_step(member.text)
            or not (
                _fronts_an_adjunct(member.text)
                or _opens_as_a_coordinate(member.text)
                or _begins_its_sentence(member, units)
            )
            for member in candidate
        ):
            continue
        after = served.get(unit.unit_id, frozenset())
        if not after:
            continue
        if all(served.get(member.unit_id, frozenset()) & after for member in candidate):
            return candidate
    return None


def _fronts_an_adjunct(fragment: str) -> bool:
    """Whether a fragment is, by its own shape, a FRONTED ADJUNCT: after its connectors it opens
    with a preposition ("in the Personal folder", "for tomorrow", "and in the Work folder").

    The same grammar `_continues_the_request_before_it` reads, without its pronoun and bare-verb
    readings: a fragment AFTER a request that points back with a pronoun continues it, but a
    fragment BEFORE one that carries a pronoun is a clause of its own.
    """
    from core.agent_runtime.answer_coverage import (
        _DEMAND_UNIT_SPLIT_CONNECTORS,
        _SPLIT_LEADERS,
        _unit_tokens,
    )

    tokens = _unit_tokens(str(fragment or ""))
    lead = 0
    while lead < len(tokens) and (
        tokens[lead] in _DEMAND_UNIT_SPLIT_CONNECTORS or tokens[lead] in _SPLIT_LEADERS
    ):
        lead += 1
    return lead < len(tokens) and tokens[lead] in _CONTINUATION_PREPOSITIONS


def is_coverage_capability(name: str) -> bool:
    """Whether `name` is a per-unit demand-coverage capability (not a reserved
    plan/none declaration, not an unknown string)."""
    wanted = str(name or "")
    if wanted in _RESERVED_COVERAGE_NAMES:
        return False
    if wanted in _COVERAGE_CAPABILITIES:
        return True
    return wanted in (_CAPABILITY_SCOPE.get() or {})


@contextmanager
def scoped_coverage_capabilities(capabilities: dict[str, Any]):
    """Inject novel capability implementations for the current context — PURE.

    Composes with the production table (injection wins on a name clash) and
    restores the previous scope on exit; no process-global state is mutated."""
    merged = dict(_COVERAGE_CAPABILITIES)
    merged.update({str(k): v for k, v in dict(capabilities).items()})
    token = _CAPABILITY_SCOPE.set(merged)
    try:
        yield merged
    finally:
        _CAPABILITY_SCOPE.reset(token)


def _capability_impl(name: str):
    """The implementation for a catalog-declared coverage name, or None for the
    reserved no-coverage declarations. UNKNOWN names fail TYPED — an unimplemented
    capability must never silently read as 'covers everything' (or nothing)."""
    wanted = str(name or "")
    if wanted in _RESERVED_COVERAGE_NAMES:
        return None
    scoped = _CAPABILITY_SCOPE.get()
    if scoped is not None and wanted in scoped:
        return scoped[wanted]
    if wanted in _COVERAGE_CAPABILITIES:
        return _COVERAGE_CAPABILITIES[wanted]
    raise TypeError(
        f"unknown demand-coverage capability {wanted!r}: no implementation is "
        "registered (see core.agent_runtime.demand_ownership._COVERAGE_CAPABILITIES "
        "or scoped_coverage_capabilities)"
    )


@dataclass(frozen=True)
class DemandCoverage:
    """The turn's units, and which deterministic lane (if any) reads each one."""

    units: tuple[tuple[str, str], ...]
    #: For each unit (same order): the lane ids whose probe claims it.
    per_unit_lanes: tuple[tuple[str, ...], ...]

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def mixed(self) -> bool:
        """True when no single lane accounts for every unit, yet some lane accounts
        for some: the shape a whole-turn claim would truncate.

        Pure shapes are deliberately NOT mixed: no unit covered at all is a pure
        general turn (the normal answer path), and a lane common to every unit is a
        whole-turn claim that lane may keep (contested readings of one unit settle in
        the registry, never here).
        """
        if self.unit_count < 2:
            return False
        if not any(lanes for lanes in self.per_unit_lanes):
            return False
        intersection = set(_all_lane_ids())
        for lanes in self.per_unit_lanes:
            if not lanes:
                intersection.clear()
                break
            intersection &= set(lanes)
        return not any(
            (spec := find_spec(lane_id)) is not None
            and (spec.max_units is None or self.unit_count <= spec.max_units)
            for lane_id in intersection
        )

    def lane_unit_ids(self, lane_id: str) -> tuple[str, ...]:
        """The unit ids `lane_id`'s probe claims — the unit-scoped proposal."""
        return tuple(
            unit_id
            for (unit_id, _text), lanes in zip(self.units, self.per_unit_lanes, strict=True)
            if lane_id in lanes
        )


def _all_lane_ids() -> tuple[str, ...]:
    from core.lane_registry import active_catalog

    return tuple(
        spec.lane_id
        for spec in active_catalog()
        if is_coverage_capability(spec.coverage)
    )


def execution_units(text: str) -> tuple[tuple[str, str], ...]:
    """The turn's EXECUTION units: demand units merged at context-fused boundaries.

    The mint (`demand_units`) sub-splits permissively — for the render gate, the
    finest unanswered slot. Executing that grain would shred requests whose
    fragments share context, in BOTH measured directions:

    * WITHIN a slice: "Compare 500 kr, $500" + "and ¥500 for a traveler in
      Copenhagen and Shanghai" are ONE comparison whose amounts and anchors landed
      apart; running them as isolated sub-turns lost the anchor ("`kr` is unresolved
      without a country anchor").
    * ACROSS slices: a live-data request followed by an output-shape paragraph
      ("Return the results as exactly two tables: …") is one request plus its
      rendering instructions; the shape fragments mint as units but execute as
      nothing on their own.

    So an execution boundary exists where the following unit opens with a DEMAND
    HEAD — a fresh request ("…and tell me…", "…plus explain…", "Explain FX vs
    PPP.") — regardless of punctuation, and never inside an unclosed parenthesis
    ("one for markets (asset, price," + "24-hour change, source)" is a column list:
    "price" is a head-shaped WORD there, not a request opener). A fragment with no
    head merges into the request it RIDES — and only into one it rides: the lanes'
    own readers decide (`_rides_the_request_before_it`), because "gold and silver
    price" is one typed plan while "latest on Iran and oil prices" is a news demand
    beside a quote the live family would otherwise swallow (measured served at
    f8937f80, "not dispatched"). Fragments with no request before them ride the request
    AFTER them by the same readers (`_leads_into_the_request_after_it`): 'in the Personal
    folder, open my Apple note "Plan"' is one Notes request, never a folder fragment that
    no lane runs. The merged text is the ORIGINAL span — first unit's
    start to last unit's end — never a re-join, so paragraph and sentence boundaries
    survive into the sub-turn that needs them."""
    return tuple((unit.unit_id, unit.text) for unit in execution_unit_spans(text))


#: Verbs that ask for OUTPUT rather than naming work. They are deliberately absent
#: from `answer_coverage._DEMAND_HEADS`, because a fragment opening with one is
#: usually a rendering instruction for work already requested ("Return the results
#: as exactly two tables: …") and promoting them there would shred a request from
#: its own formatting.
_OUTPUT_VERBS = frozenset(
    {
        "return", "output", "print", "display", "produce", "emit", "render",
    }
)


def _output_verb_names_real_work(fragment: str) -> bool:
    """Whether an OUTPUT-verb fragment is a fresh request rather than a render note.

    The head vocabulary cannot answer this, and that is the point: "Return the
    results as exactly two tables" and "Return exactly the first line of bravo.txt"
    open with the same verb and are opposite things. The difference is not lexical —
    it is whether the fragment names work some lane can actually do — so the question
    is put to the CANONICAL CAPABILITY REGISTRY, which is the one component that
    knows. A family the registry learns to execute later becomes a family whose
    output-verb requests split correctly, with no edit to a word list.

    Without this, "Return exactly the second line of alpha.txt. Return exactly the
    first line of bravo.txt." fused into ONE execution unit: two same-family demands
    sharing a single identity, so only one could be dispatched, reported or repaired.
    """
    text = str(fragment or "").strip()
    if not text:
        return False
    from core.agent_runtime.answer_coverage import _unit_tokens

    tokens = _unit_tokens(text)
    if not tokens or tokens[0] not in _OUTPUT_VERBS:
        return False
    return any(
        _probe_claims(impl, text)
        for impl in (
            _capability_impl(spec.coverage)
            for spec in _active_specs()
        )
        if impl is not None
    )


def a_registered_lane_claims(fragment: str) -> bool:
    """Whether any lane in the CANONICAL CAPABILITY REGISTRY claims `fragment` as its work.

    The ungated form of the probe `_output_verb_names_real_work` already runs: same
    registry, same authority, without the "first token is an output verb" precondition.
    It answers "would some lane execute this on its own", never "is this a demand" --
    a fragment no lane claims returns False whether it is a continuation or a family
    the catalog has not learned yet.

    Exists so a CLAIMANT can ask the question about a unit it does not serve, without
    reaching into this module's privates or growing a second copy of the probe loop.
    Fails CLOSED to False on an empty or raising registry, so a caller that gates a
    refusal on it degrades to its own prior behaviour rather than refusing everything.
    """
    text = str(fragment or "").strip()
    if not text:
        return False
    return any(
        _probe_claims(impl, text)
        for impl in (
            _capability_impl(spec.coverage)
            for spec in _active_specs()
        )
        if impl is not None
    )


def _active_specs():
    from core.lane_registry import active_catalog

    return active_catalog()


@dataclass(frozen=True)
class ExecutionUnitSpan:
    """One execution unit with its range over the ORIGINAL request text.

    `member_unit_ids` are the REQUEST units this execution unit carries (the accounting grain):
    a dispatch or receipt for the execution unit is a dispatch or receipt for every member, so a
    request batched with its neighbours is never reported "not dispatched" under the answer that
    served it."""

    unit_id: str
    text: str
    start: int
    end: int
    member_unit_ids: tuple[str, ...] = ()


SHADOW_DISAGREEMENTS: deque[dict[str, Any]] = deque(maxlen=200)


def execution_unit_spans(text: str) -> tuple[ExecutionUnitSpan, ...]:
    """The EXECUTION grain: one span per REQUEST unit of the interpretation, covering the request
    and everything attached to it (its context, constraints, literals and enumerations), so the
    lane that runs it receives the whole request text. The pre-interpretation grouping rules
    (`_legacy_execution_unit_spans`) run in shadow: a disagreement is recorded, never dispatched."""
    from core.agent_runtime.answer_coverage import interpret_request

    value = str(text or "")
    interpretation = interpret_request(value)
    requests = interpretation.requests
    from core.inline_payload import turn_supplies_its_own_content

    if requests and turn_supplies_its_own_content(value):
        # An analysis of SUPPLIED material is one answering operation, whether or not the
        # message also forbids tools. Its code, data rows, questions and answer scaffold share
        # one context; a code line or a price row inside the paste is not a filesystem command
        # or a market lookup for a lane to serve on its own. Measured 2026-09-16 (candidate
        # 035dea9b): the same pasted evaluation, without its "Do NOT use tools" lines, minted
        # fifty request units -- code lines among them -- and read as a mixed turn claimed by the
        # audit and live-data lanes. Keep the accounting obligations intact.
        return (ExecutionUnitSpan(
            unit_id=requests[0].unit_id, text=value.strip(), start=0, end=len(value),
            member_unit_ids=tuple(unit.unit_id for unit in requests),
        ),)
    # Batching for dispatch, over REQUEST units only: consecutive requests one lane serves, or that
    # no lane reads (general prose beside general prose), run as one execution unit -- the lane or
    # the model keeps the shared context; requests different lanes serve stay apart (R1e). The
    # accounting grain is untouched: each request stays its own obligation.
    grouped = _legacy_execution_unit_spans(value, requests) if requests else ()
    spans: list[ExecutionUnitSpan] = []
    for group in grouped:
        members = [unit for unit in requests if group.start <= unit.start and unit.end <= group.end]
        start, end = group.start, group.end
        for member in members:
            member_start, member_end = interpretation.group_span(member.unit_id)
            start, end = min(start, member_start), max(end, member_end)
        spans.append(
            ExecutionUnitSpan(
                unit_id=members[0].unit_id if members else group.unit_id,
                text=value[start:end].strip(), start=int(start), end=int(end),
                member_unit_ids=tuple(unit.unit_id for unit in members) or (group.unit_id,),
            )
        )
    try:
        shadow = _legacy_execution_unit_spans(value, interpretation.units)
        if len(shadow) != len(spans):
            SHADOW_DISAGREEMENTS.append(
                {"text_length": len(value), "interpretation": len(spans), "legacy_over_fragments": len(shadow)}
            )
            logging.getLogger("vool.interpretation").debug(
                "interpretation_shadow_disagreement interpretation=%d legacy=%d text_length=%d",
                len(spans), len(shadow), len(value),
            )
    except Exception:
        pass
    return tuple(spans)


def execution_unit_members(text: str) -> dict[str, tuple[str, ...]]:
    """execution unit id -> the request unit ids it carries (itself first)."""
    return {span.unit_id: span.member_unit_ids or (span.unit_id,) for span in execution_unit_spans(text)}


def _legacy_execution_unit_spans(text: str, minted: Any) -> tuple[ExecutionUnitSpan, ...]:
    """The grouping law that preceded the interpretation (R1e, amended) -- SHADOW ONLY.

    The EXECUTION grain, not the mint grain, and the difference is load-bearing for
    any lane deciding "could somebody else run this demand?": the mint sub-splits
    permissively, so a continuation fragment like "and one app.py file." becomes its
    own slot and reads as a workspace file reference — a demand nobody actually
    asked for. Execution units merge such fragments into the request they ride,
    which is exactly the grain the demand-owned plan dispatches.
    """
    from core.agent_runtime.answer_coverage import (
        opens_a_fresh_request,
        units_without_own_object,
    )

    value = str(text or "")
    units = tuple(minted or ())
    served, readers_intact = _mint_unit_service(value, units)
    riders = frozenset(units_without_own_object(value))
    # Whether any lane reads any unit: the shape the demand-owned plan dispatches unit by unit.
    somebody_serves = readers_intact and any(served.values())
    groups: list[list[Any]] = []
    fresh_by_id: dict[str, bool] = {}
    for unit in units:
        opens_fresh = opens_a_fresh_request(unit.text) or _output_verb_names_real_work(
            unit.text
        )
        if (
            groups
            and opens_fresh
            and somebody_serves
            and not served.get(unit.unit_id)
            and not any(served.get(member.unit_id) for member in groups[-1])
        ):
            # GENERAL PROSE BESIDE GENERAL PROSE, in a turn some lane reads elsewhere: the answer
            # headings of one request ("How would you design this system? Explain the
            # architecture, how permissions should work, ...") run as ONE sub-turn whose model
            # keeps the shared context. Measured 2026-09-16 (candidate 035dea9b): each heading
            # became its own paid sub-turn -- six attempts for one design answer, one of them
            # asking the model "Explain the architecture" with nothing to explain. The accounting
            # grain is untouched: every heading stays a member obligation of the unit that ran.
            groups[-1].append(unit)
            continue
        # A single-action parser cannot batch another complete action into its
        # subject, even when the two requests belong to the same capability.
        opens_fresh = opens_fresh or (bool(groups) and any(
            spec.max_units == 1 and (probe := _capability_impl(spec.coverage)) is not None
            and _probe_claims(probe, unit.text)
            and any(_probe_claims(probe, member.text) for member in groups[-1])
            for spec in _active_specs()
        ))
        fresh_by_id[unit.unit_id] = opens_fresh
        inside_parens = False
        if groups:
            before = value[groups[-1][0].start : unit.start]
            inside_parens = before.count("(") > before.count(")")
        if groups and (not opens_fresh or inside_parens):
            # A headless fragment merges into the request it rides -- and ONLY into a request
            # it rides (`_rides_the_request_before_it`): a fragment some family serves beside
            # a request that family does not serve is a second request, not a continuation.
            if (
                inside_parens
                or not readers_intact
                or _rides_the_request_before_it(unit, groups[-1], served, riders)
            ):
                groups[-1].append(unit)
                continue
        elif groups:
            request_before = any(
                _holds_a_request_of_its_own(member, fresh_by_id)
                for earlier in groups for member in earlier
            )
            # A fragment BETWEEN two requests rides the request whose clause it opens --
            # and only that request (`_opens_the_clause_of_the_request_after_it`): the
            # look-back reading stays for every fragment that opens no clause of it.
            carried = _opens_the_clause_of_the_request_after_it(
                groups[-1], unit, served, units, fresh_by_id, request_before
            )
            if carried is not None:
                split = len(groups[-1]) - len(carried)
                carried, groups[-1] = groups[-1][split:], groups[-1][:split]
                if groups[-1]:
                    groups.append([*carried, unit])
                else:
                    groups[-1] = [*carried, unit]
                continue
            # Fragments that opened no demand and follow no request LEAD the request that
            # opens after them ("in the Personal folder, open my Apple note ..."): they
            # ride it -- and only a request they lead
            # (`_leads_into_the_request_after_it`); a second request stays apart.
            if not request_before and _leads_into_the_request_after_it(groups[-1], unit, served):
                groups[-1].append(unit)
                continue
        groups.append([unit])
    return tuple(
        ExecutionUnitSpan(
            unit_id=f"u{index + 1}",
            text=value[group[0].start : group[-1].end].strip(),
            start=int(group[0].start),
            end=int(group[-1].end),
        )
        for index, group in enumerate(groups)
    )


def demand_coverage(text: str) -> DemandCoverage:
    """Mint the turn's demand units and read each one through the ACTIVE CATALOG.

    Every catalog spec declaring a per-unit capability contributes its lane id
    wherever its capability claims the unit; 'none'/composite/fallback
    declarations contribute nothing here. An unknown capability name raises
    TypeError — see `_capability_impl`."""
    from core.lane_registry import active_catalog

    probes = tuple(
        (spec.lane_id, impl)
        for spec in active_catalog()
        if (impl := _capability_impl(spec.coverage)) is not None
    )
    from core.agent_runtime.answer_coverage import interpret_request

    value = str(text or "")
    units = execution_units(value)
    bound = binding_lanes(value, interpret_request(value).units)
    per_unit = tuple(
        tuple(
            dict.fromkeys(
                [lane_id for lane_id, probe in probes if _probe_claims(probe, unit_text)]
                + sorted(bound.get(unit_id, frozenset()))
            )
        )
        for unit_id, unit_text in units
    )
    return DemandCoverage(units=units, per_unit_lanes=per_unit)


def _probe_claims(probe: object, unit_text: str) -> bool:
    try:
        return bool(probe(unit_text))  # type: ignore[operator]
    except Exception:
        return False


def lane_may_claim_whole_turn(text: str, lane_id: str) -> bool:
    """Whether `lane_id` may END the external turn `text` — the R1f finalize law.

    A lane declared `terminal=False` can never finalize — any shape, any role. A
    single-unit turn is claimable by any terminal lane (pure turns keep their
    paths; an unregistered lane serving one request is the pre-existing world).
    On a MULTI-unit turn the catalog decides, and only three kinds of lane may
    finish it:

    * a coverage-declaring lane whose capability covers EVERY unit;
    * a declared composite planner / fallback — it owns a complete unit plan;
    * nobody else: an UNREGISTERED lane, or a registered lane whose coverage is
      'none' (the single-unit-limited arm of the trichotomy), cannot finalize a
      multi-unit turn. That omission is exactly how a lane left out of the
      registries would swallow mixed demand.
    """
    spec = find_spec(lane_id)
    if spec is not None and not spec.terminal:
        # The terminal field is a LAW, not documentation: a lane declared
        # non-terminal cannot END an external turn at all — not on a single-unit
        # turn, and not through the composite-planner or fallback roles.
        return False
    coverage = demand_coverage(text)
    if coverage.unit_count < 2:
        return True
    if spec is None:
        return False
    if spec.max_units is not None and coverage.unit_count > spec.max_units:
        return False
    if spec.role in (ROLE_COMPOSITE_PLANNER, ROLE_FALLBACK):
        return True
    if not is_coverage_capability(spec.coverage):
        return False
    # Covers EVERY unit, or no claim: a lane covering ZERO units of a multi-unit
    # turn is a lane that covers none of the demand trying to end the turn —
    # not "its own admission still decides".
    return len(coverage.lane_unit_ids(lane_id)) == coverage.unit_count


# =====================================================================
# P0 MIXED-DEMAND — the per-demand execution ledger.
#
# Counting demand units proved a turn was UNDERSTOOD; it never proved a unit was
# RUN. At base a mixed turn could mint three units, report three, and execute one
# — the accounting and the execution were separate stories and only the accounting
# was checked. These records join them: one row per demand, carrying the unit's
# stable identity, the capability that was selected for it, whether an execution
# was actually attempted, and the typed state it ended in. "Unsupported" is a
# STATE here, not an absence: a demand nobody covers is reported as such rather
# than quietly missing from the ledger.
# =====================================================================

#: Typed terminal states. A demand ends in exactly one of them.
DEMAND_EXECUTED = "executed"
DEMAND_FAILED = "failed"
DEMAND_PENDING_APPROVAL = "pending_approval"
DEMAND_NOT_ATTEMPTED = "not_attempted"
#: P0 POLICY CONSERVATION — the demand's serving capability was refused by the
#: PARENT's frozen constraints before dispatch. Distinct from `failed`: the
#: unit was never handed to its lane because the turn's own authority
#: prohibited it, and the refusal carries the parent's reason codes.
DEMAND_REFUSED = "refused"

_DEMAND_TERMINAL_STATES = frozenset(
    {
        DEMAND_EXECUTED,
        DEMAND_FAILED,
        DEMAND_PENDING_APPROVAL,
        DEMAND_NOT_ATTEMPTED,
        DEMAND_REFUSED,
    }
)

#: The capability recorded for a demand no registered lane claims. Named, not empty:
#: an unsupported demand must be visible as unsupported.
CAPABILITY_UNSUPPORTED = "unsupported"

#: Something executed this demand and the runtime cannot name what. Distinct from
#: `unsupported` on purpose: "nobody can serve this" and "somebody served it and we
#: failed to record who" are different facts, and collapsing the second into the
#: first is what produced a served interpretation filed as unsupported. A row
#: carrying this is a gap in the executor record, visible as one, rather than either
#: a fabricated capability or a ledger that silently fails to exist.
CAPABILITY_UNATTRIBUTED = "unattributed_executor"


@dataclass(frozen=True)
class DemandRecord:
    """One demand's identity, selected capability, attempt and terminal state."""

    demand_id: str
    request: str
    capability: str
    lane_id: str
    attempted: bool
    terminal_state: str
    #: P0 policy conservation: the parent reason codes (+ conservation marker)
    #: a REFUSED demand carries, so a receipt can name WHY it was refused and
    #: not merely that it was.
    refusal_reasons: tuple[str, ...] = ()
    #: The REQUEST units this execution unit carries (itself first). Receipts and dispatch rows
    #: are filed for every member, so a request batched with its neighbours is accounted for by
    #: the answer that served it instead of surfacing as "not dispatched" beneath it.
    member_unit_ids: tuple[str, ...] = ()

    @property
    def accounted_unit_ids(self) -> tuple[str, ...]:
        members = tuple(str(unit) for unit in self.member_unit_ids if str(unit or "").strip())
        return members if members else (self.demand_id,)

    def __post_init__(self) -> None:
        if not str(self.demand_id or "").strip():
            raise ValueError("DemandRecord.demand_id must be a non-empty string")
        if self.terminal_state not in _DEMAND_TERMINAL_STATES:
            raise ValueError(
                f"DemandRecord.terminal_state must be one of "
                f"{sorted(_DEMAND_TERMINAL_STATES)}, got {self.terminal_state!r}"
            )
        # An attempt that produced a terminal state, or a state that admits no
        # attempt: the two cannot disagree. A row claiming `executed` while nothing
        # was dispatched is exactly the "counted, not run" defect this ledger exists
        # to make impossible to write down. A REFUSED demand was carried INTO its
        # serving decision and ended there — that is an attempt outcome too.
        if not self.attempted and self.terminal_state != DEMAND_NOT_ATTEMPTED:
            raise ValueError(
                f"DemandRecord {self.demand_id!r} reports terminal state "
                f"{self.terminal_state!r} with no execution attempt"
            )
        if self.attempted and self.terminal_state == DEMAND_NOT_ATTEMPTED:
            raise ValueError(
                f"DemandRecord {self.demand_id!r} was attempted but reports "
                f"{DEMAND_NOT_ATTEMPTED!r}"
            )
        # CAPABILITY TRUTH. "Unsupported" means nothing in the runtime served this
        # demand. A row saying that beside `executed` is self-refuting, and it was
        # written for real: the served ledger recorded the interpretation demand as
        # `capability=unsupported` with a SUCCEEDED dispatch and an answer receipt,
        # while a model had plainly just answered it. Something executed it, so
        # something is its capability — and if the ledger cannot name what, the
        # honest record is a FAILED demand, never a supported-by-nobody success.
        if self.capability == CAPABILITY_UNSUPPORTED and self.terminal_state in (
            DEMAND_EXECUTED,
            DEMAND_PENDING_APPROVAL,
        ):
            raise ValueError(
                f"DemandRecord {self.demand_id!r} reports terminal state "
                f"{self.terminal_state!r} with capability {CAPABILITY_UNSUPPORTED!r}: "
                "a demand nothing supports cannot have been served"
            )

    @property
    def supported(self) -> bool:
        """Whether a registered lane's capability claimed this demand."""
        return self.capability != CAPABILITY_UNSUPPORTED


def selected_capability(lane_id: str) -> str:
    """The coverage capability name the ACTIVE CATALOG declares for `lane_id`."""
    spec = find_spec(str(lane_id or ""))
    return str(spec.coverage) if spec is not None else CAPABILITY_UNSUPPORTED


def executor_capability(executor: Any) -> tuple[str, str]:
    """(lane_id, capability) for the lane a sub-turn's OWN route says served it.

    The route is a fact the sub-turn stamped on itself; `lane_registry` owns the
    route -> lane resolution (`finalization_family`) and the lane -> executed
    capability resolution (`executed_capability`). Nothing is matched by wording
    here and no vocabulary lives here — an unrecognized route resolves to ("", "")
    and the caller falls back to the pre-dispatch claim, which is the honest answer
    when the runtime cannot name what ran.
    """
    if not isinstance(executor, dict):
        return "", ""
    from core.lane_registry import executed_capability, finalization_family

    lane_id = finalization_family(
        str(executor.get("route_reason") or ""), route=str(executor.get("route") or "")
    )
    if not lane_id:
        return "", ""
    return str(lane_id), str(executed_capability(lane_id) or "")


def conserved_unit_refusals(
    coverage: DemandCoverage, source_context: dict[str, Any] | None
) -> dict[int, Any]:
    """P0 POLICY CONSERVATION at the dispatch seam.

    For every unit of a demand plan, the parent's frozen prohibitions (read
    from the canonical request the context carries) are intersected with the
    capability the registry selected for that unit: parent authority ∩ child
    capability. A refused unit is returned as {task index: ChildPolicyDecision}
    and must NEVER be dispatched — its demand ends typed REFUSED, while the
    units the parent did not freeze execute normally.

    The capability question goes to the REGISTRY's own selection
    (`_highest_precedence` over the claiming probes, then the catalog's
    declared coverage name) — no vocabulary lives here, and a unit no lane
    claims is a general demand whose serving consumes nothing the parent can
    have frozen, so it is never refused by this seam.
    """
    from core.turn_prohibitions import child_demand_policy, conserved_request_prohibitions

    parent = conserved_request_prohibitions(source_context)
    if parent.empty:
        return {}
    refusals: dict[int, Any] = {}
    for index, ((_unit_id, _unit_text), lanes) in enumerate(
        zip(coverage.units, coverage.per_unit_lanes, strict=True)
    ):
        lane_id = _highest_precedence(lanes)
        if not lane_id:
            continue
        decision = child_demand_policy(
            parent, selected_capability(lane_id), coverage.units[index][1]
        )
        if decision.refused:
            refusals[index] = decision
    return refusals


def demand_records(
    text: str,
    outcomes: Any = None,
    executors: Any = None,
    refusals: Any = None,
) -> tuple[DemandRecord, ...]:
    """The turn's demand ledger, one row per unit, in the order they were written.

    `outcomes` is the planner's `TaskOutcome` sequence when the plan ran, or None
    before dispatch. Rows are keyed POSITIONALLY to the unit mint — the same
    property `units_as_plan` relies on — so a unit's identity is stable across a
    retry of the same turn and a resumed plan reports the same ids it started with.
    An outcome that never arrived leaves its demand `not_attempted`; it is never
    dropped, because a missing row reads as a demand that did not exist.

    `refusals` is the conservation seam's {task index: ChildPolicyDecision} map:
    a refused demand ends DEMAND_REFUSED with the parent's reason codes, and no
    executor record can ever file it as served.
    """
    coverage = demand_coverage(str(text or ""))
    try:
        members_by_unit = execution_unit_members(str(text or ""))
    except Exception:
        members_by_unit = {}
    executor_by_index = dict(executors or {})
    refusal_by_index = dict(refusals or {})
    by_index: dict[int, Any] = {}
    for outcome in tuple(outcomes or ()):
        task = getattr(outcome, "task", None)
        index = getattr(task, "index", None)
        if isinstance(index, int):
            by_index[index] = outcome

    records: list[DemandRecord] = []
    for index, ((unit_id, unit_text), lanes) in enumerate(
        zip(coverage.units, coverage.per_unit_lanes, strict=True)
    ):
        # Contested readings settle by the registry's precedence, never here — the
        # same authority `mediate` uses, so the ledger names the lane that would
        # actually own the unit.
        lane_id = _highest_precedence(lanes)
        capability = selected_capability(lane_id) if lane_id else CAPABILITY_UNSUPPORTED
        outcome = by_index.get(index)
        refusal = refusal_by_index.get(index)
        if outcome is None and refusal is not None:
            attempted, state = True, DEMAND_REFUSED
        elif outcome is None:
            attempted, state = False, DEMAND_NOT_ATTEMPTED
        elif str(getattr(outcome, "outcome_kind", "") or "") == "refused":
            attempted, state = True, DEMAND_REFUSED
        elif getattr(outcome, "needs_approval", False):
            attempted, state = True, DEMAND_PENDING_APPROVAL
        elif getattr(outcome, "ok", False):
            attempted, state = True, DEMAND_EXECUTED
        else:
            attempted, state = True, DEMAND_FAILED
        # WHAT RAN beats WHAT WOULD HAVE. The pre-dispatch claim is the registry's
        # deterministic reading; once a sub-turn has actually run, its own route
        # names the lane that served this demand, and that is the fact a discharge
        # ledger owes the reader. The two agree for a deterministic demand; they
        # diverge exactly where the demand fell through to the model, which is the
        # case that used to be filed as "unsupported" while plainly being answered.
        executed_lane, executed_cap = executor_capability(executor_by_index.get(index))
        if state == DEMAND_REFUSED:
            # A refused demand was not served by anybody; the refusal's own
            # decision names the parent authority, and the registry's selected
            # capability stays as the honest "what would have run".
            pass
        elif executed_lane and executed_cap:
            lane_id, capability = executed_lane, executed_cap
        elif capability == CAPABILITY_UNSUPPORTED and state in (
            DEMAND_EXECUTED,
            DEMAND_PENDING_APPROVAL,
        ):
            # It ran and the route did not resolve. Say exactly that.
            capability = CAPABILITY_UNATTRIBUTED
        refusal_reasons = tuple(
            getattr(refusal, "reason_codes", ()) or ()
        ) if refusal else ()
        records.append(
            DemandRecord(
                demand_id=unit_id,
                request=unit_text,
                capability=capability,
                lane_id=lane_id,
                attempted=attempted,
                terminal_state=state,
                refusal_reasons=refusal_reasons,
                member_unit_ids=tuple(members_by_unit.get(unit_id, ()) or (unit_id,)),
            )
        )
    return tuple(records)


def registered_lane_for(text: str) -> str:
    """The lane whose declared capability claims `text`, or "" when none does.

    The single question a composite planner asks about a demand it cannot execute
    itself: does the CANONICAL REGISTRY say somebody else can? Answered from the
    active catalog, so a family the registry learns later is answered correctly
    with no edit here.
    """
    value = str(text or "")
    if not value.strip():
        return ""
    coverage = demand_coverage(value)
    claimants: list[str] = []
    for lanes in coverage.per_unit_lanes:
        claimants.extend(lanes)
    return _highest_precedence(tuple(dict.fromkeys(claimants)))


def _highest_precedence(lane_ids: tuple[str, ...]) -> str:
    """The catalog-earliest lane among `lane_ids` — the registry's own ordering."""
    from core.lane_registry import active_catalog

    order = {spec.lane_id: spec.precedence for spec in active_catalog()}
    known = [lane_id for lane_id in lane_ids if lane_id in order]
    if not known:
        return str(lane_ids[0]) if lane_ids else ""
    return min(known, key=lambda lane_id: order[lane_id])


def registered_owner_ahead_of(text: str, lane_id: str, *, capability: str | tuple[str, ...] = "") -> str:
    """The registered lane that owns every demand unit of `text` ahead of `lane_id`, or "".

    Catalog precedence is the only contested-claim ordering, but `lane_may_claim_whole_turn`
    grants any terminal lane a single-unit turn, so a lane the front door happens to run first
    can end a turn the catalog ranks another lane ahead for. Measured on the served path
    (2026-09-15): 'rename my Apple note "Plan" in the Work folder to "Plan v2"' is one unit that
    `operator_action_dispatch` (precedence 48) covers, yet the folder-overview arm of
    `turn_frontdoor_deterministic` (50) ran before operator dispatch, read "the Work folder" as
    the bound workspace and answered with a directory listing. The Notes bridge was never called.

    An owner is a terminal lane whose per-unit capability claims EVERY unit, within its
    `max_units`, ranked strictly ahead of `lane_id`; an unregistered `lane_id` ranks after the
    whole catalog. `capability` names what the asking lane serves, one capability or several: a
    lane in the same coverage domain group reads the same subject, so it is a sibling reading and
    never an owner here ("check this work folder" is both an audit and an overview, and the front
    door already orders those). Callers own the fail-soft direction; this raises whatever a probe
    raises.

    Precedence orders the claims a lane DECLARES. When `lane_id` declares a per-unit capability
    that covers none of this turn's units, the arm asking is claiming outside that declaration,
    and it ranks where the catalog ranks undeclared deterministic claims: the front-door tier that
    `finalization_family` gives every unmapped deterministic route. Measured on the served path
    (2026-09-15): the search arm of `workspace_read_fast_path` (42) answered 'append to my Apple
    note "Ideas" with "count how many python files are in this project"' with "No text matches for
    "Ideas" were found in the workspace." The read capability claimed no unit; the operator lane's
    claimed the only one. Asked at 42, the operator lane (48) never ranked ahead of that claim.
    """
    from core.lane_registry import active_catalog, finalization_family

    value = str(text or "")
    if not value.strip():
        return ""
    names = (capability,) if isinstance(capability, str) else tuple(capability or ())
    own_domains = {_COVERAGE_DOMAIN_GROUPS.get(name, name) for name in names if name}
    coverage = demand_coverage(value)
    if not coverage.unit_count:
        return ""
    asking = find_spec(lane_id)
    if asking is not None and is_coverage_capability(asking.coverage) and not coverage.lane_unit_ids(lane_id):
        asking = find_spec(finalization_family("", route="deterministic:") or "")
    common = set(coverage.per_unit_lanes[0])
    for lanes in coverage.per_unit_lanes[1:]:
        common &= set(lanes)
    owners = [
        spec
        for spec in active_catalog()
        if spec.lane_id in common
        and spec.lane_id != lane_id
        and spec.terminal
        and (spec.max_units is None or coverage.unit_count <= spec.max_units)
        and (asking is None or spec.precedence < asking.precedence)
    ]
    if not owners:
        return ""
    # The catalog's owner is the top-ranked lane covering the whole turn. When that lane reads the
    # asker's own subject, the turn belongs to a sibling reading, not to a lower-ranked lane of
    # another domain: "How much disk space is left on this machine?" is covered by the machine-fact
    # lane (43) and the operator lane (48), and it is the machine-fact lane's.
    owner = min(owners, key=lambda spec: spec.precedence)
    return "" if _COVERAGE_DOMAIN_GROUPS.get(owner.coverage, owner.coverage) in own_domains else owner.lane_id


def units_as_plan(text: str):
    """The demand units as planner tasks — the deterministic plan-before-execute split.

    Each task's request is the unit's own text (the user's words, sub-split at
    coordination boundaries by the mint), so nothing a model could invent can enter
    here and nothing the user wrote is left without an owner: every unit becomes a
    task, and ``run_plan`` executes each exactly once under the turn's chain.

    A SOFTWARE-AUTHORING turn never becomes a plan: its bullet list is one artifact's
    change spec, and executing the fragments as sub-turns is the measured defect (2026-09-17:
    "Continue the previous implementation. Add these changes: ..." carved into six sub-turns
    that each lost the code they modified). The register here is the same one `plan_turn`
    and the executor's eligibility phase consult, so no seam can disagree about what coding is.
    """
    from core.agent_runtime.turn_planner import PlannedTask

    try:
        from core.agent_runtime.grounded_mode import is_software_authoring_request

        if is_software_authoring_request(str(text or "")):
            return []
    except Exception:
        pass
    coverage = demand_coverage(text)
    return [
        PlannedTask(index=index, request=unit_text)
        for index, (_unit_id, unit_text) in enumerate(coverage.units)
    ]


__all__ = [
    "CAPABILITY_UNATTRIBUTED",
    "CAPABILITY_UNSUPPORTED",
    "DEMAND_EXECUTED",
    "DEMAND_FAILED",
    "DEMAND_NOT_ATTEMPTED",
    "DEMAND_PENDING_APPROVAL",
    "DEMAND_REFUSED",
    "LANE_CURRENCY",
    "LANE_DIRECT_MATH",
    "LANE_LIVE_DATA",
    "LANE_WORKSPACE_READ",
    "DemandCoverage",
    "DemandRecord",
    "ExecutionUnitSpan",
    "conserved_unit_refusals",
    "demand_coverage",
    "demand_records",
    "execution_unit_spans",
    "executor_capability",
    "is_coverage_capability",
    "lane_may_claim_whole_turn",
    "registered_lane_for",
    "registered_owner_ahead_of",
    "scoped_coverage_capabilities",
    "selected_capability",
    "units_as_plan",
]
