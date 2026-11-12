"""The Operator Profile's seat in a chat turn.

One function is called at the turn front door for every user turn -- BEFORE the memory command
lane, so a profile-shaped "remember ..." lands on the typed authority instead of the free-text
memory store -- and it does exactly three things:

1. binds the turn's principal + chat for every later profile reader (a ContextVar, so the
   identity authority and the prompt assembler read the SAME resolution this turn used);
2. interprets the turn (:mod:`core.operator_profile_interpretation`) and applies the memory law
   through :mod:`core.operator_profile`: explicit -> persist and report; strong -> candidate;
   weak -> chat-local; contradiction -> conflict candidate; secrets -> refused. The NAME is
   learned the way a person learns one (``_apply_name``): said once it is saved, said again it
   is replaced, an introduction or a pasted letter fills an empty name, and nothing asks;
3. leaves the outcome on ``source_context["profile_observation"]`` so the transport door can put
   a ``vool_profile`` frame on the wire (chip / confirmation / "Used N preferences").

It claims the turn only when the turn is ENTIRELY profile-shaped and explicit (or a forget /
undo / show command). A mixed turn ("remember my name is X and I live in Vilnius") persists the
profile part, reports it, and hands the remainder on so the memory lane still sees its clause.
A turn with no profile content costs one interpretation pass and changes nothing.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any

from core import operator_profile as profile
from core.operator_profile_interpretation import ProfileProposal, interpret_profile_turn, name_beside_request

__all__ = [
    "bind_turn_scope",
    "clear_turn_scope",
    "current_turn_scope",
    "learn_name_from_mail",
    "observe_name_beside_request",
    "observe_profile_turn",
    "profile_frame_for_result",
    "turn_only_states_a_name",
]

_TURN_SCOPE: ContextVar[tuple[str, str, str]] = ContextVar("operator_profile_turn_scope", default=("", "", ""))


def bind_turn_scope(principal: str, session_id: str, project_id: str = ""):
    return _TURN_SCOPE.set((str(principal or ""), str(session_id or ""), str(project_id or "")))


def clear_turn_scope() -> None:
    """End-of-turn reset, called where the agent clears its execution context, so an off-turn
    reader never resolves against the previous turn's principal."""
    _TURN_SCOPE.set(("", "", ""))


def current_turn_scope() -> tuple[str, str, str]:
    """``(principal, session_id, project_id)`` of the turn in flight, or blanks off-turn."""
    return _TURN_SCOPE.get()


def _observation(source_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source_context, dict):
        return {"saved": [], "candidates": [], "conflicts": [], "used": [], "notices": []}
    obs = source_context.get("profile_observation")
    if not isinstance(obs, dict):
        obs = {"saved": [], "candidates": [], "conflicts": [], "used": [], "notices": []}
        source_context["profile_observation"] = obs
    return obs


def _candidate_payload(change: profile.ProfileChange) -> dict[str, Any]:
    item = change.item
    return {
        "candidate_id": item.item_id if item else "",
        "text": change.report,
        "category": item.category if item else "",
        "value": item.value_text if item else "",
        "actions": ["save", "edit", "only_this_chat"],
    }


def _conflict_payload(change: profile.ProfileChange) -> dict[str, Any]:
    item = change.item
    return {
        "candidate_id": item.item_id if item else "",
        "text": change.report,
        "category": item.category if item else "",
        "value": item.value_text if item else "",
        "current": change.previous.value_text if change.previous else "",
        "actions": ["replace", "scope", "keep"],
    }


#: Not a profile turn after all ("I'm not sure" names no name in use): nothing is said or saved.
_IGNORED = profile.ProfileChange("ignored", "")


def _same_name(left: str, right: str) -> bool:
    a, b = str(left or "").strip().lower(), str(right or "").strip().lower()
    return bool(a) and bool(b) and (a == b or a == b.split()[0] or b == a.split()[0])


def _standing_name(principal: str) -> profile.ProfileItem | None:
    """The name VOOL uses across chats (any scope but one chat's), or None."""
    for item in reversed(profile.list_items(principal)):
        if item.category == "preferred_name" and item.status == profile.STATUS_ACTIVE:
            return item
    return None


def _apply_name(proposal: ProfileProposal, *, principal: str, session_id: str, turn_id: str) -> profile.ProfileChange:
    """The name law. Stated -> saved, and a later statement replaces it (a correction always
    wins, nothing asks). An introduction fills an empty name; where another name already stands
    it applies to this chat only, unless it was said as a correction."""
    standing = _standing_name(principal)
    stated = proposal.strength in {"explicit", "strong"}
    correcting = bool(proposal.correction and standing is not None)
    if stated or (proposal.learnable and correcting):
        scope = proposal.scope if stated else "global"
        if correcting and scope == "global" and standing is not None:
            # "no, it's Sam" corrects the name wherever it stands (a work-only name stays work-only).
            scope = standing.scope
        change = profile.remember(
            principal, "preferred_name", proposal.value, scope=scope,
            session_id=session_id, turn_id=turn_id, origin="explicit", confidence=1.0, actor="chat",
            reason=f"stated: {proposal.clause[:120]}", replace=True,
        )
        if change.kind in {"saved", "updated"} and scope != "chat":
            # The name they just gave is the name in this chat too: an earlier this-chat-only
            # nickname must not keep outranking it here.
            for item in profile.list_items(principal, session_id=session_id):
                if item.category == "preferred_name" and item.scope == "chat":
                    profile.forget_item(item.item_id, actor="chat", reason="superseded by a stated name")
        if change.kind == "saved":
            # As the whole reply this passes the same gate every reply does, and that gate reads
            # "I'll call ..." as a promise of work not done; the line under an answer is not a reply.
            said = f"Got it, {change.item.value_text}. I'll remember that." if proposal.whole else f"I'll call you {change.item.value_text}."
            return profile.ProfileChange("saved", said, item=change.item)
        if change.kind == "updated" and change.previous is not None:
            return profile.ProfileChange(
                "updated", f"Got it, {change.item.value_text} it is. I won't call you {change.previous.value_text} any more.",
                item=change.item, previous=change.previous,
            )
        if change.kind == "unchanged" and change.item is not None:
            if change.item.origin == "inferred":
                # They confirmed a picked-up name by saying it themselves: it is theirs now.
                with contextlib.suppress(Exception):
                    profile.edit_item(change.item.item_id, change.item.value_text, expected_revision=change.item.revision,
                                      actor="chat", reason="confirmed by the user")
            if proposal.strength != "explicit":
                return _IGNORED
            return profile.ProfileChange("unchanged", f"I'm already calling you {change.item.value_text}.", item=change.item)
        return change
    if proposal.learnable:
        if standing is None:
            change = profile.learn_name(
                principal, proposal.value, source="an introduction in chat", session_id=session_id, turn_id=turn_id, own_words=True,
            )
            if change.kind == "saved":
                return profile.ProfileChange("saved", f"Nice to meet you, {change.item.value_text}.", item=change.item) if proposal.whole else change
        elif _same_name(standing.value_text, proposal.value):
            return _IGNORED
    change = profile.note_chat_local(
        principal, "preferred_name", proposal.value, session_id=session_id, turn_id=turn_id, confidence=0.5,
    )
    return change


def _reject_name(proposal: ProfileProposal, *, principal: str, session_id: str) -> profile.ProfileChange:
    """"I'm not Rick" / "that's not my name": drop the name in use. A named one applies only when
    it IS the name in use; the dropped name is then never guessed again (``name_guess_blocked``)."""
    current = profile.resolve(principal, "preferred_name", session_id=session_id)
    if current is None or (proposal.rejected and not _same_name(current.value_text, proposal.rejected)):
        return _IGNORED
    old = current.value_text
    last: profile.ProfileChange | None = None
    for item in profile.list_items(principal, session_id=session_id):
        if item.category != "preferred_name":
            continue
        # A nickname used in this chat only is dropped alone; the name that stands everywhere else
        # was not what they objected to.
        if current.scope == "chat" and item.item_id != current.item_id:
            continue
        last = profile.forget_item(item.item_id, actor="chat", reason="forget: not my name")
    return profile.ProfileChange(
        "forgotten", f"Sorry about that. I won't call you {old} any more.",
        item=last.item if last else None, previous=last.previous if last else None,
    )


def _apply(proposal: ProfileProposal, *, principal: str, session_id: str, turn_id: str) -> profile.ProfileChange:
    if proposal.category == "preferred_name" and proposal.action == "forget" and proposal.correction:
        return _reject_name(proposal, principal=principal, session_id=session_id)
    if proposal.category == "preferred_name" and proposal.action == "remember":
        return _apply_name(proposal, principal=principal, session_id=session_id, turn_id=turn_id)
    if proposal.action == "forget":
        return _forget(proposal, principal=principal, session_id=session_id)
    if proposal.action == "undo":
        return _undo(principal, session_id)
    if proposal.action == "show":
        return _show(principal, session_id)
    if proposal.strength == "explicit":
        return profile.remember(
            principal, proposal.category, proposal.value, scope=proposal.scope,
            session_id=session_id, turn_id=turn_id, origin="explicit", confidence=1.0, actor="chat",
            reason=f"explicit: {proposal.clause[:120]}",
        )
    if proposal.strength == "strong":
        return profile.propose_candidate(
            principal, proposal.category, proposal.value, session_id=session_id, turn_id=turn_id,
            confidence=0.85, reason=f"stated: {proposal.clause[:120]}",
        )
    return profile.note_chat_local(
        principal, proposal.category, proposal.value, session_id=session_id, turn_id=turn_id, confidence=0.5,
    )


def _forget(proposal: ProfileProposal, *, principal: str, session_id: str) -> profile.ProfileChange:
    items = profile.list_items(principal, session_id=session_id)
    if proposal.category != "*":
        items = [item for item in items if item.category == proposal.category]
    if not items:
        label = "anything" if proposal.category == "*" else profile.CATEGORIES.get(proposal.category, {}).get("label", proposal.category)
        return profile.ProfileChange("unchanged", f"Nothing saved for {label}, so there is nothing to forget.")
    reports = []
    last: profile.ProfileChange | None = None
    for item in items:
        last = profile.forget_item(item.item_id, actor="chat", reason=f"forget: {proposal.clause[:120]}")
        reports.append(last.report.split(". Say 'undo")[0])
    return profile.ProfileChange("forgotten", "; ".join(reports) + ". Say 'undo that' to restore.", item=last.item if last else None, previous=last.previous if last else None)


def _undo(principal: str, session_id: str) -> profile.ProfileChange:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT item_id FROM operator_profile_history WHERE principal = ? AND action IN ('create','update','forget','move_scope','restore','confirm_chat') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (principal,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return profile.ProfileChange("unchanged", "There is no profile change to undo.")
    return profile.restore_previous(str(row["item_id"]), actor="chat")


def _show(principal: str, session_id: str) -> profile.ProfileChange:
    items = profile.list_items(principal, session_id=session_id)
    if not items:
        return profile.ProfileChange("unchanged", "I don't have anything saved about you yet. Tell me what to remember, or open What VOOL remembers about you in Settings.")
    lines = [f"- {item.label}: {item.value_text} ({item.scope if item.scope != 'chat' else 'this chat only'}; {item.origin})" for item in items]
    return profile.ProfileChange("unchanged", "What I remember about you:\n" + "\n".join(lines))


def observe_name_beside_request(
    raw_user_input: str,
    effective_input: str = "",
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    access_policy: Any | None = None,
) -> dict[str, Any] | None:
    """Take a name said beside a request, before any lane splits the message into parts.

    Measured on a real build 2026-11-11 (qwen3:8b): "Hi, I'm Kofi, what is the capital of
    Portugal?" was answered "... Lisbon. Could not be answered: Hi, I'm Kofi" and the name was
    never learned -- the lanes that serve a several-part message run ahead of the profile seat and
    read the introduction as a request nobody could serve. The name is applied HERE, by the same
    law as the seat (which then leaves it alone for this turn).

    Returns None when the turn carries no such name. Otherwise ``{"raw", "effective", "single"}``
    (and ``"plan"`` when the lanes keep the message as written but should plan its parts without the name):
    the message with the name part blanked out (same length, so every position a lane computes
    still points into what the person wrote) for the several-part lanes, and whether one request
    at most is left beside the name (nothing for those lanes to split)."""
    if not isinstance(source_context, dict):
        return None
    try:
        principal = profile.principal_for_request(source_context)
        if not principal:
            return None
        from core.private_chat import turn_is_private
        from core.request_trust import turn_speaks_for_owner

        # A team agent's brief names nobody the owner is (core.request_trust.turn_speaks_for_owner).
        if turn_is_private(source_context, session_id) or not turn_speaks_for_owner(source_context, session_id):
            return None
        raw = str(raw_user_input or "")
        found = name_beside_request(raw)
        if found is None:
            return None
        proposal, _remainder, (start, end) = found
        project_id = str(getattr(access_policy, "project_id", "") or source_context.get("_trusted_project_id") or "")
        bind_turn_scope(principal, session_id, project_id)
        turn_id = str(source_context.get("cancel_turn_id") or source_context.get("_canonical_user_turn_id") or "")
        change = _apply(proposal, principal=principal, session_id=session_id, turn_id=turn_id)
        obs = _observation(source_context)
        obs["name_taken_beside_request"] = True
        if change.kind in {"saved", "updated", "forgotten"}:
            obs["saved"].append({**change.as_dict(), "category": "preferred_name", "item_id": change.item.item_id if change.item else ""})
            _record_receipt(change, proposal, session_id=session_id, source_context=source_context)
            # Counts as its first use: it is said under this answer, not announced again on the next turn.
            used_lines, used = profile.hydration_for_turn(principal, session_id=session_id, project_id=project_id)
            if used:
                source_context["profile_context_lines"] = used_lines
                source_context["profile_used"] = used
                obs["used"] = used
    except Exception:
        return None
    try:
        from core.agent_runtime.answer_coverage import demand_units

        blanked = raw[:start] + " " * (end - start) + raw[end:]
        asked = list(demand_units(raw))
        name_units = [u for u in asked if int(u.start) >= start and int(u.end) <= end]
        left = [(str(u.unit_id), int(u.start), int(u.end)) for u in asked if u not in name_units]
        after = [(str(u.unit_id), int(u.start), int(u.end)) for u in demand_units(blanked)]
        if name_units:
            # The introduction was taken (the profile holds it): it is not a request left unanswered.
            from core.conductor import obligation_ledger

            active = obligation_ledger.active_set()
            if active is not None:
                for unit in name_units:
                    obligation_ledger.record_slice_consumption(
                        active[0], active[1], unit_id=str(unit.unit_id), family="operator_profile",
                        evidence="slice_answer_record", excerpt="preferred_name",
                    )
        single = len(after) <= 1
        if left != after:
            # The requests beside the name would be numbered differently without it: the lanes keep
            # the message as written, so what they account for stays what was asked.
            # Only the planning of the parts reads the message without the name.
            return {"raw": raw, "effective": str(effective_input or ""), "single": single, "plan": blanked}
        effective = str(effective_input or "")
        if effective and effective != raw:
            again = name_beside_request(effective)
            effective = (effective[: again[2][0]] + " " * (again[2][1] - again[2][0]) + effective[again[2][1]:]) if again else blanked
        else:
            effective = blanked if effective else ""
        return {"raw": blanked, "effective": effective, "single": single}
    except Exception:
        return {"raw": str(raw_user_input or ""), "effective": str(effective_input or ""), "single": False}


def turn_only_states_a_name(raw_user_input: str) -> bool:
    """Whether the whole message is the person saying (or correcting, or taking back) their name.

    "my name isnt Agnes, its Sam" has a comma and reads, to the lanes that split a message into
    parts, like two requests: measured 2026-11-11 with a local model, a planning call ran for 25
    seconds before the profile seat answered "Got it, Sam it is." without using it. The seat
    answers such a turn by itself, so there is nothing for those lanes to plan."""
    try:
        proposals = interpret_profile_turn(str(raw_user_input or ""))
        if not proposals or any(p.category != "preferred_name" or not p.whole for p in proposals):
            return False
        return not _remaining_text(str(raw_user_input or ""), [p.clause for p in proposals]).strip()
    except Exception:
        return False


def observe_profile_turn(
    agent: Any,
    raw_user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    access_policy: Any | None = None,
) -> dict[str, Any] | None:
    """Apply the memory law to one turn. Returns ``{"result": fast_path_result | None,
    "remaining_text": str}`` or None when the turn carries no profile content."""
    principal = profile.principal_for_request(source_context)
    project_id = str(getattr(access_policy, "project_id", "") or (source_context or {}).get("_trusted_project_id") or "")
    bind_turn_scope(principal, session_id, project_id)
    from core.persistent_memory import whole_memory_erase_request

    if whole_memory_erase_request(raw_user_input):
        # "Forget everything you know about me" erases saved memory AND the profile: the memory lane owns that turn
        # and runs the profile's forget-all as part of it. Taken here, only the profile went (measured 2026-11-12).
        return None
    # Private chat (core.private_chat): the profile is still READ for this turn, but nothing the
    # turn says is saved to it (no standing instruction, no profile fact, no candidate chip).
    from core.private_chat import turn_is_private

    private = turn_is_private(source_context, session_id)
    # A team agent's turn (core.request_trust.turn_speaks_for_owner): its "user" side is the orchestrator's
    # brief, not the owner speaking. The profile is still READ for it, but nothing it says is saved (no
    # standing instruction, no profile fact, no candidate chip, no name from pasted text), and unlike a
    # private chat it cannot forget or undo what the owner saved either: only "show" is left.
    from core.request_trust import turn_speaks_for_owner

    agent_turn = not turn_speaks_for_owner(source_context, session_id)
    # Standing instructions ("from now on …", "always …", a correction of how answers look) are saved here,
    # on the same seat as the profile, so they reach every later chat in this workspace. Owner turns only; a
    # turn that only states an instruction still gets an answer (this never claims the turn).
    if principal and not private and not agent_turn:
        try:
            from core.standing_instructions import observe_turn as _observe_standing

            standing = _observe_standing(raw_user_input, source_context, principal=principal,
                                         project_id=project_id, session_id=session_id)
            if (standing["saved"] or standing["taken_back"]) and isinstance(source_context, dict):
                _observation(source_context)["standing_instructions"] = standing
        except Exception:
            pass
    try:
        proposals = interpret_profile_turn(raw_user_input)
        if agent_turn:
            proposals = [p for p in proposals if getattr(p, "action", "") == "show"]
        elif private:
            # Showing or forgetting what is already saved still works in a private chat.
            proposals = [p for p in proposals if getattr(p, "action", "") in {"forget", "undo", "show"}]
    except Exception:
        proposals = []
    name_taken = bool(((source_context or {}).get("profile_observation") or {}).get("name_taken_beside_request")) if isinstance(source_context, dict) else False
    turn_id = str((source_context or {}).get("cancel_turn_id") or (source_context or {}).get("_canonical_user_turn_id") or "")
    # A name VOOL picked up outside this chat (off the operator's own mail) is said out loud the
    # first time it is used, with an Undo -- never used silently, never asked about.
    unannounced = _unannounced_name(principal, session_id, project_id) if principal and not name_taken else None
    used_lines: list[str] = []
    used: list[dict[str, str]] = []

    def _hydrate() -> None:
        nonlocal used_lines, used
        try:
            used_lines, used = profile.hydration_for_turn(principal, session_id=session_id, project_id=project_id)
        except Exception:
            used_lines, used = [], []
        # The turn context is touched ONLY when the profile did something: an ordinary turn carries
        # no profile keys at all (byte-equivalence law, and the frozen front-door tests pin the
        # context they hand each lane).
        if used and isinstance(source_context, dict):
            source_context["profile_context_lines"] = used_lines
            source_context["profile_used"] = used
            _observation(source_context)["used"] = used

    if principal:
        _hydrate()
    if unannounced is not None:
        _observation(source_context)["saved"].append({
            "report": f"I'll call you {unannounced.value_text}.{_where_noticed(unannounced)}",
            "category": "preferred_name", "item_id": unannounced.item_id,
        })
    if principal and not private and not agent_turn and not any(p.category == "preferred_name" for p in proposals):
        # A pasted letter that names the operator (and agrees with the name on this computer's
        # account) teaches the name without a word about it from them.
        learned = _learn_name_from_pasted(raw_user_input, principal=principal, session_id=session_id, turn_id=turn_id)
        if learned is not None:
            _observation(source_context)["saved"].append({
                "report": f"I'll call you {learned.value_text}. I noticed the name in what you pasted.",
                "category": "preferred_name", "item_id": learned.item_id,
            })
            _hydrate()
    if name_taken:
        # Already applied for this turn by observe_name_beside_request.
        proposals = [p for p in proposals if p.category != "preferred_name"]
    if not proposals:
        return None
    if not principal:
        _observation(source_context)["notices"].append("Profile memory is unavailable on this surface.")
        return None
    obs = _observation(source_context)
    claim_lines: list[str] = []
    name_entries: list[dict[str, Any]] = []
    name_changed = False
    consumed: list[str] = []
    consumed_spans: list[tuple[int, int]] = []
    claims_turn = True
    for proposal in proposals:
        change = _apply(proposal, principal=principal, session_id=session_id, turn_id=turn_id)
        kind = change.kind
        if kind == "ignored":
            claims_turn = False
            continue
        if kind == "candidate":
            obs["candidates"].append(_candidate_payload(change))
            claims_turn = False
            continue
        if kind == "conflict":
            obs["conflicts"].append(_conflict_payload(change))
            claim_lines.append(change.report)
            consumed.append(proposal.clause)
            consumed_spans.append((proposal.source_start, proposal.source_end))
            continue
        if kind == "chat_local":
            claims_turn = False
            name_changed = name_changed or proposal.category == "preferred_name"
            continue
        if kind in {"saved", "updated", "forgotten"}:
            entry = {**change.as_dict(), "category": change.item.category if change.item else proposal.category, "item_id": change.item.item_id if change.item else ""}
            obs["saved"].append(entry)
            _record_receipt(change, proposal, session_id=session_id, source_context=source_context)
            if proposal.category == "preferred_name":
                name_changed = True
                if not proposal.whole:
                    # "I'm Rick, can you help me ...": the name is saved and the sentence stays in
                    # the turn, so the question still gets its answer.
                    claims_turn = False
                    continue
                name_entries.append(entry)
            claim_lines.append(change.report)
            consumed.append(proposal.clause)
            consumed_spans.append((proposal.source_start, proposal.source_end))
            continue
        if kind in {"refused_secret", "refused_sensitive", "refused_privacy", "refused_binding", "refused_invalid", "paused", "unchanged"}:
            if proposal.strength == "explicit" or proposal.action in {"forget", "undo", "show"}:
                claim_lines.append(change.report)
                consumed.append(proposal.clause)
                consumed_spans.append((proposal.source_start, proposal.source_end))
            else:
                obs["notices"].append(change.report)
                claims_turn = False
            continue
        claims_turn = False
    if name_changed:
        # The answer to THIS turn already uses the name it just taught (or stops using the one it dropped).
        if isinstance(source_context, dict):
            for key in ("profile_context_lines", "profile_used"):
                source_context.pop(key, None)
            obs.pop("used", None)
        _hydrate()
    remaining = _remaining_text(raw_user_input, consumed)
    memory_remaining = _remaining_memory_text(raw_user_input, consumed_spans)
    if not claim_lines:
        return {"result": None, "remaining_text": raw_user_input}
    if remaining.strip() and remaining.strip() != raw_user_input.strip():
        # A mixed turn: the profile part is done and reported; the rest continues on its own lane
        # and the report rides the response frame.
        obs["notices"].extend(claim_lines)
        return {"result": None, "remaining_text": remaining, "remaining_memory_text": memory_remaining}
    if not claims_turn and remaining.strip():
        obs["notices"].extend(claim_lines)
        return {"result": None, "remaining_text": raw_user_input}
    response = "\n".join(claim_lines)
    for entry in name_entries:
        # The reply itself says it; the line under it is just the Undo.
        entry["report"] = ""
    return {
        "result": agent._fast_path_result(
            session_id=session_id,
            user_input=raw_user_input,
            response=response,
            confidence=0.97,
            source_context=source_context,
            reason="operator_profile_command",
        ),
        "remaining_text": "",
    }


def _unannounced_name(principal: str, session_id: str, project_id: str) -> profile.ProfileItem | None:
    try:
        item = profile.resolve(principal, "preferred_name", session_id=session_id, project_id=project_id)
    except Exception:
        return None
    if item is None or item.origin != "inferred" or item.scope == "chat" or item.last_used_at:
        return None
    return item


def _where_noticed(item: profile.ProfileItem) -> str:
    try:
        rows = profile.history_for_item(item.item_id)
        reason = str(rows[0].get("reason") or "") if rows else ""
    except Exception:
        reason = ""
    if "own mail" in reason:
        return " I noticed the name on your email."
    if "pasted" in reason:
        return " I noticed the name in what you pasted."
    return ""


def _learn_name_from_pasted(raw_user_input: str, *, principal: str, session_id: str, turn_id: str) -> profile.ProfileItem | None:
    try:
        from core import name_learning

        # The computer account is the owner's: it says nothing about a phone or channel user.
        if principal != profile.OWNER_PRINCIPAL:
            return None
        if not name_learning.has_pasted_material(raw_user_input) or _standing_name(principal) is not None:
            return None
        name = name_learning.name_from_pasted_text(raw_user_input)
        if not name:
            return None
        change = profile.learn_name(principal, name, source="a pasted message", session_id=session_id, turn_id=turn_id)
    except Exception:
        return None
    return change.item if change.kind == "saved" else None


def learn_name_from_mail(messages: Any, *, source_context: dict[str, Any] | None) -> profile.ProfileItem | None:
    """Called after VOOL reads the operator's mail: a message to (or from) one of their own
    configured addresses carries the name people use for them. Owner turns only, never a private
    chat, and only into an empty name. It is announced on the next turn that uses it."""
    try:
        from core import name_learning
        from core.email_accounts import configured_accounts
        from core.private_chat import turn_is_private

        principal = profile.principal_for_request(source_context)
        context = source_context if isinstance(source_context, dict) else {}
        session_id = str(context.get("runtime_session_id") or context.get("chat_id") or context.get("session_id") or "")
        if not principal or principal != profile.OWNER_PRINCIPAL or turn_is_private(source_context, session_id):
            return None
        if _standing_name(principal) is not None:
            return None
        own = [str(getattr(record, "address", "") or "") for record in configured_accounts().values()]
        name = name_learning.name_from_own_mail(list(messages or []), own)
        if not name:
            return None
        change = profile.learn_name(principal, name, source="own mail", session_id=session_id)
    except Exception:
        return None
    return change.item if change.kind == "saved" else None


def _record_receipt(change: profile.ProfileChange, proposal: ProfileProposal, *, session_id: str, source_context: dict[str, Any] | None) -> None:
    """A profile write is real work with a durable record, so it carries the same executed
    receipt every other action carries: the final-action honesty gate judges "Saved to your
    profile" against this receipt, in the turn context AND in the durable receipt store."""
    item = change.item
    execution = {
        "executed": True,
        "ok": True,
        "status": "executed",
        "tool": "profile.remember" if change.kind != "forgotten" else "profile.forget",
        "item_id": item.item_id if item else "",
        "category": item.category if item else proposal.category,
        "revision": item.revision if item else 0,
        "kind": change.kind,
    }
    receipt = {"tool_name": execution["tool"], "intent": execution["tool"], "execution": execution, "status": "executed"}
    if isinstance(source_context, dict):
        receipts = source_context.get("tool_receipts")
        if not isinstance(receipts, list):
            receipts = []
            source_context["tool_receipts"] = receipts
        receipts.append(receipt)
    try:
        from core.runtime_continuity import build_tool_receipt_key, store_tool_receipt

        key = build_tool_receipt_key(
            checkpoint_id=str((source_context or {}).get("runtime_checkpoint_id") or session_id),
            step_index=int(item.revision if item else 0),
            intent=execution["tool"],
            arguments={"item_id": execution["item_id"], "kind": change.kind},
        )
        store_tool_receipt(
            receipt_key=key,
            session_id=session_id,
            checkpoint_id=str((source_context or {}).get("runtime_checkpoint_id") or ""),
            tool_name=execution["tool"],
            idempotency_key=f"{execution['item_id']}:{execution['revision']}:{change.kind}",
            arguments={"category": execution["category"], "scope": item.scope if item else proposal.scope},
            execution=execution,
        )
    except Exception:
        pass


def _remaining_memory_text(raw: str, consumed_spans: list[tuple[int, int]]) -> str:
    """Remove owner-consumed spans without rewriting the following command argument."""
    cursor = 0
    parts: list[str] = []
    for start, end in sorted(consumed_spans):
        if start < cursor or end < start:
            continue
        parts.append(raw[cursor:start])
        cursor = end
    parts.append(raw[cursor:])
    return "".join(parts).lstrip(" ,.!;\t\r\n").rstrip()


def _remaining_text(raw: str, consumed: list[str]) -> str:
    text = str(raw or "")
    for clause in consumed:
        clause = str(clause or "").strip()
        if clause and clause in text:
            text = text.replace(clause, " ", 1)
    text = " ".join(text.split())
    # a leftover directive fragment ("Remember that", "and") is not content
    import re

    text = re.sub(r"^(?:(?:and|also|please|remember|note|save|store|that|this|too)\b[\s,.!]*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:[\s,.!]*\b(?:and|also|too|please|remember that|remember)\b)+\s*[.!]?$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.!;")


def profile_frame_for_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The ``vool_profile`` wire frame for a finished turn, or None when nothing happened --
    an ordinary turn puts no profile key on the wire at all."""
    if not isinstance(result, dict):
        return None
    ctx = result.get("source_context") if isinstance(result.get("source_context"), dict) else {}
    obs = ctx.get("profile_observation") if isinstance(ctx.get("profile_observation"), dict) else None
    if not obs:
        return None
    frame: dict[str, Any] = {}
    for key in ("saved", "candidates", "conflicts", "used", "notices"):
        values = [v for v in list(obs.get(key) or []) if v]
        if key == "notices":
            # A change already shown with its Undo is not repeated as a second line.
            shown = {str(entry.get("report") or "") for entry in frame.get("saved") or [] if isinstance(entry, dict)}
            values = [v for v in values if str(v) not in shown]
        if values:
            frame[key] = values
    # A standing instruction is saved for every later chat in the workspace; without its own line
    # the user never learns it was kept, or that a take-back worked. It rides the "saved" list the
    # chat page already renders as the confirmation line (no item_id: undo is a take-back in chat).
    standing = obs.get("standing_instructions") if isinstance(obs.get("standing_instructions"), dict) else {}
    lines = [{"report": f"Saved for every chat: {text}"} for text in standing.get("saved") or [] if text]
    lines += [{"report": f"No longer following: {text}"} for text in standing.get("taken_back") or [] if text]
    if lines:
        frame["saved"] = list(frame.get("saved") or []) + lines
    return frame or None
