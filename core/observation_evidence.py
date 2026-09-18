"""An attempt is not an observation.

The defect this module exists to close
--------------------------------------
Measured at eca76ff9 through the real `/api/chat` seam (`core.web.api.service.dispatch_post`), two
turns of one ordinary conversation::

    U: what's the weather in Vilnius right now?
    A: Vilnius: Clear, 17 C (today's high 19 C / low 13 C). Source: [wttr.in](...),
       observed 2026-08-07T12:00:00Z.          <- real fetch, real receipt, deterministic render

    U: and Porto?
       -> the continuation rebound correctly and planned weather_lookup(Porto)
       -> the fetch FAILED.  Activity: `tool_failed | live_data.weather_lookup`
       -> the receipt it wrote said, truthfully:
              status='failed'  source_count=0  failure_class='no observation returned'
    A: Porto: Cloudy, high 22 C, low 17 C right now. Source: wttr.in,
       observed 2026-08-07T12:00:00Z.          <- INVENTED, with the PREVIOUS turn's timestamp
                                                  and a source this turn never reached

Execution truth was right the whole way through. Every layer that *recorded* what happened recorded
a failure. The layers that *read* those records to decide "did this turn observe anything?" answered
from the PRESENCE of an entry and never looked at the fields inside it, so a failed lookup counted
as a successful one and the guards that exist for exactly this answer stood down:

    turn_ran_observations(ctx)            -> True   from [('failed', 0, 'no observation returned')]
    turn_has_current_evidence(...)        -> True   from the same failed receipt
    inspect_unsourced_current_claim(...)  -> requires_current=True   asserts_measured_value=True
                                             attributes_source=True  has_evidence=True -> NOT convicted

That last line is the whole bug in one frame. `core.unsourced_current_claim` had already decided the
turn needed a current observation, that the answer stated a measured value, and that it attributed a
source. It declined to convict on one input: whether the turn had evidence. The predicate answering
that question could not tell an attempt from an observation.

The invariant
-------------
**A failed retrieval may prove that an attempt occurred. It may never prove that the requested fact
was observed.** A same-turn evidence entry licenses a grounded claim only when the entry itself
records a successful, usable observation.

How success is judged, and why there is no vocabulary here
----------------------------------------------------------
Every channel writer in this runtime already records its own outcome. This module reads those
fields; it does not re-derive the outcome, and it does not carry a list of failure words:

  1. an explicit boolean ``ok`` -- authoritative when present. Written by the tool loop
     (`core.agent_runtime.response_policy_tool_history.tool_history_observation_payload`) and by
     `core.execution_records.ExecutionRecord`.
  2. a non-empty failure text -- ``failure_class`` / ``failure_reason`` / ``error``. Written by all
     three receipt lanes: `core.live_data_retrieval_receipts`, `core.retrieval_observability`,
     `core.fresh_data.fx`.
  3. a ``source_count`` of zero -- a retrieval that returned no sources observed nothing. Written by
     the two web-retrieval lanes, and an independent second signal from (2) on the same entry.

A ``status`` string is deliberately NOT consulted. The runtime's status vocabulary is open-ended and
lane-specific -- `core/execution/hive_tools.py` alone writes ``not_configured``, ``unreachable``,
``no_results``, ``executed``, ``empty``, ``listed``, ``missing_packet``, ``exported`` -- so any
failure set spelled out here would be a list shaped by whichever failures had been seen, and the
first lane to invent a new status would silently go back to counting as evidence. The three signals
above are structural and every writer already populates at least one of them.

The unknown direction is the safe one
-------------------------------------
An entry carrying NONE of those signals still counts as an observation. That is not laziness, it is
the direction that cannot invent a defect: material the user supplied with the request (attachments,
pasted files) and plain-string notes have no success field because nothing about them can fail, and
they are evidence in exactly the way `turn_has_current_evidence` has always held them to be. Only an
entry that AFFIRMATIVELY reports its own failure loses standing, so every path that is green today
because it observed something stays green for the same reason it always did.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Fields whose non-empty value IS the writer's own record that this entry failed. Three names
#: rather than one because the three receipt lanes were written independently; each populates the
#: name its own lane uses, and an entry is failed if any of them is filled in.
_FAILURE_TEXT_FIELDS = ("failure_class", "failure_reason", "error")

#: The explicit boolean outcome, when the writer records one. Checked first and answered outright:
#: a writer that says `ok` has already made this judgement and nothing here may overrule it.
_OK_FIELD = "ok"

#: How many sources a retrieval actually returned. Zero means the lookup came back with nothing to
#: observe, which is a failed observation whether or not the transport itself raised.
_SOURCE_COUNT_FIELD = "source_count"

#: Explicit non-freshness marks. A lane that RESTORES an earlier turn's evidence into this turn's
#: channels -- a recall ("check the original message and answer it"), a resume, a reconstruction
#: from persisted rows -- marks the restored entry itself, so no reader can mistake restored
#: history for a this-turn observation. Two shapes, because the writers differ:
#:
#:   ``fresh: False``      -- the writer's own word that this entry is not a current observation;
#:   ``lifecycle: "restored"`` -- the restored family's self-description, for entries whose
#:                            outcome fields describe the ORIGINAL fetch rather than the restore.
#:
#: Both are affirmative self-reports, so the unknown direction stays safe exactly as the failure
#: signals above are safe: an entry that marks nothing keeps the standing it always had. Only a
#: writer that says "this is restored history" loses standing -- and it loses it outright, before
#: the outcome fields are read, because a restored entry's `ok` describes the turn that originally
#: observed, never the turn that restored. Evidence is eligible only for the exact canonical turn
#: that consumed it; session membership alone never proves freshness.
_NON_FRESH_MARK = "fresh"
_RESTORED_LIFECYCLE = "restored"


def _field(entry: Any, name: str) -> Any:
    """`entry[name]` for a mapping, `entry.name` for an object, or None when absent.

    Both shapes are real on these channels: receipts and tool observations are dicts, while
    `core.execution_records.ExecutionRecord` is a frozen dataclass read through attributes.
    """

    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def records_a_usable_observation(entry: Any) -> bool:
    """Whether this one same-turn evidence entry records a SUCCESSFUL, USABLE observation.

    False only when the entry reports its own failure through one of the three structural signals
    documented at the top of this module. An entry with no such signal -- a plain-string note, a
    user-supplied attachment -- is an observation, because nothing about it ever claimed otherwise.
    """

    if entry is None:
        return False
    if isinstance(entry, str):
        # A bare note is content, not a lifecycle record: it is evidence exactly when it says
        # something. `turn_has_current_evidence` has always treated it this way.
        return bool(entry.strip())
    if isinstance(entry, (list, tuple, set, frozenset)):
        # A nested channel (a list of notes under one key) is usable when any member is.
        return any(records_a_usable_observation(item) for item in entry)

    # A restored entry is history before it is anything else. Checked before the outcome fields:
    # `ok` on a restored entry describes the turn that originally observed, and reading it here
    # would let turn A's successful fetch stand as turn B's observation.
    fresh = _field(entry, _NON_FRESH_MARK)
    if fresh is not None and not fresh:
        return False
    if str(_field(entry, "lifecycle") or "").strip().lower() == _RESTORED_LIFECYCLE:
        return False

    ok = _field(entry, _OK_FIELD)
    if isinstance(ok, bool):
        return ok

    for name in _FAILURE_TEXT_FIELDS:
        if str(_field(entry, name) or "").strip():
            return False

    count = _field(entry, _SOURCE_COUNT_FIELD)
    if isinstance(count, int) and not isinstance(count, bool):
        return count > 0

    # No outcome signal at all. Evidence if there is anything to it -- the pre-existing meaning of
    # "this channel has an entry", preserved deliberately for the shapes that cannot fail.
    if isinstance(entry, Mapping):
        return any(str(value or "").strip() for value in entry.values())
    return bool(entry)


def usable_observations(entries: Any) -> list[Any]:
    """The subset of `entries` that record a successful, usable observation."""

    if entries is None:
        return []
    if isinstance(entries, (str, Mapping)):
        entries = [entries]
    try:
        items = list(entries)
    except TypeError:
        items = [entries]
    return [item for item in items if records_a_usable_observation(item)]


def channel_has_a_usable_observation(value: Any) -> bool:
    """Whether one `source_context` evidence channel holds at least one usable observation.

    Accepts the two shapes these channels really take: a list of entries, or a single scalar/mapping
    stored directly under the key.
    """

    if value is None:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(records_a_usable_observation(item) for item in value)
    return records_a_usable_observation(value)


__all__ = [
    "channel_has_a_usable_observation",
    "records_a_usable_observation",
    "usable_observations",
]
