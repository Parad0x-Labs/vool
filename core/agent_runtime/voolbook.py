from __future__ import annotations

import contextlib
import re
from typing import Any


def looks_like_conversational_correction_turn(text: str) -> bool:
    """Whether this turn is the user rejecting the last answer rather than asking for a profile edit.

    Imported lazily and behind a guard: `core.task_router` owns the recognizer (it is the same one
    the classifier uses to keep a correction off the research lane), and this module is imported by
    `core.agent_runtime.__init__`, so a module-level import would tie a leaf fast path to that
    package's whole import graph. If it cannot be reached, the answer is False -- the fast path then
    falls through to the intent rules, which after QA-050-025 no longer claim a bare deictic.
    """
    try:
        from core.task_router import looks_like_conversational_correction
    except Exception:  # pragma: no cover - import-graph failure, not a routing decision
        return False
    return looks_like_conversational_correction(text)


def classify_voolbook_intent(lowered: str) -> str | None:
    """Return a specific intent only when the user clearly wants a VoolBook action."""
    if re.search(r"(?:delete|remove)\s+(?:my\s+)?(?:voolbook\s+)?post", lowered):
        return "delete"
    if re.search(r"(?:edit|update|change)\s+(?:my\s+)?(?:voolbook\s+)?post\b", lowered):
        return "edit"
    if re.search(
        r"(?:post\s+(?:to|on)\s+(?:voolbook|vool\s*book)|"
        r"(?:voolbook|vool\s*book)\s+post|"
        r"do\s+(?:a\s+)?(?:first\s+)?post|"
        r"let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post|"
        r"(?:new\s+)?social\s+post\b|"
        r"test\s+post\b|"
        r"do\s+the\s+(?:test\s+)?post\b|"
        r"just\s+post\s+(?:that|this)\b)",
        lowered,
    ):
        return "post"
    if re.search(
        r"(?:create|make|set\s*up|start|open|get|register|sign\s*up)\s+"
        r"(?:a\s+|my\s+|an?\s+|our\s+)?(?:voolbook\s+|vool\s*book\s+)?"
        r"(?:profile|account)",
        lowered,
    ):
        return "create"
    # "Register Alpha on VoolBook" / "sign up as alpha on vool book": the PRODUCT is named
    # as the destination of the registration, so the intent is explicit even though no
    # profile/account noun follows.
    if re.search(
        r"(?:register|sign\s*up)(?:\s+as)?\s+\S+\s+on\s+(?:voolbook|vool\s*book)\b",
        lowered,
    ):
        return "create"
    if "sign up" in lowered and ("voolbook" in lowered or "vool book" in lowered):
        return "create"
    if re.search(
        r"(?:do\s+(?:we|i)\s+have|(?:is|check|what\s*(?:is|\'s))\s+(?:my|our))\s+"
        r"(?:\w+\s+)?(?:(?:voolbook|vool\s*book)\s+)?(?:name|handle|profile|account)",
        lowered,
    ):
        return "check_profile"
    if re.search(r"(?:what|who)\s+(?:is|am)\s+(?:my|i)\s+(?:on\s+)?(?:voolbook|vool\s*book)", lowered):
        return "check_profile"
    if re.search(r"(?:my|our)\s+(?:voolbook|vool\s*book)\s+(?:name|handle|profile)", lowered):
        return "check_profile"

    has_bio = bool(re.search(r"(?:(?:set|update|change)\s+(?:my\s+)?bio\b|^bio\s*:)", lowered))
    # The declarative arm REQUIRES "my" or "handle". With both optional, bare `x is` claimed any
    # sentence containing a standalone letter x -- measured live 2026-08-15: "String X is 'VOOL'.
    # ... Reverse the string." (a pure string-manipulation turn) was read as a Twitter-handle
    # declaration and answered "You need a VoolBook profile first." A letter named in prose is
    # not this user's social profile; only the possessive or the word "handle" makes it one.
    has_twitter = bool(
        re.search(
            r"(?:(?:set|update|change|add)\s+(?:my\s+)?\b(?:twitter|x)\b(?:\s+handle)?|"
            r"my\s+(?:twitter|x)\b(?:\s+handle)?\s*(?:is|:)|"
            r"\b(?:twitter|x)\s+handle\s*(?:is|:))",
            lowered,
        )
    )
    if has_bio and has_twitter:
        return "compound_bio_twitter"
    if has_twitter:
        return "twitter"
    if has_bio:
        return "bio"

    if re.search(
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:voolbook|vool\s*book)\s+)?"
        r"(?:name|handle|display)",
        lowered,
    ):
        return "rename"
    # `set` or `my` is REQUIRED, not optional. With both optional this matched a bare `name:`
    # anywhere in a message, so any spec, form, config or YAML fragment was read as a request to
    # rename the user's VoolBook. An independent tester hit it with a skill spec — and SKILL.md
    # frontmatter opens with `name:`, so the one document this product authors was guaranteed to
    # trigger it. `name: my-service` in a pasted YAML block did the same.
    if re.search(r"(?:set\s+(?:my\s+)?|my\s+)(?:name|handle)\s*[:=]", lowered):
        return "rename"
    # "change my voolbook to lumen" -- a rename that names the PRODUCT instead of the field. Kept
    # because it is unambiguous; the word voolbook is doing the work the deictic below could not.
    if re.search(
        r"(?:change|rename|switch|set|update)\s+(?:my\s+|our\s+)?(?:voolbook|vool\s*book)"
        r"(?:\s+profile)?\s+to\s+",
        lowered,
    ):
        return "rename"
    # DELIBERATELY ABSENT: a bare `chang\w+ (it|this|that|<any word>) to ...`.
    #
    # That arm used to return "rename" here, and `extract_display_name` then took everything after
    # "to" as the new display name. Measured on the v0.5.0 smoke run (QA-050-025): "Change it to a
    # photo editor." -- an ordinary correction about an APP being discussed -- wrote
    # `voolbook_profiles.display_name = 'a photo editor'`. "change that to celsius" did the same.
    # Nothing in either sentence mentions VoolBook, a profile, a name or a handle.
    #
    # "it"/"that" mean "the thing we were just talking about", which in a chat is almost never the
    # user's profile. There is no phrasing of that arm that recovers the intent, because the intent
    # is not in the sentence -- it is in the context, and this classifier does not have it. The one
    # place the deictic IS unambiguous is the `awaiting_rename` step, where the runtime just asked
    # "What do you want to change it to?"; that reply is consumed by the pending flow above and
    # never reaches this function. This is the same over-claim as the `name:`-in-YAML arm above and
    # the bare-`x` arm in `has_twitter`, both already fixed for the same reason.
    return None


def maybe_handle_voolbook_fast_path(
    agent: Any,
    user_input: str,
    *,
    raw_user_input: str | None,
    session_id: str,
    source_context: dict[str, object] | None,
    signer_module: Any,
) -> dict[str, Any] | None:
    raw_text = str(raw_user_input if raw_user_input is not None else user_input or "")
    lowered = " ".join(raw_text.lower().split())
    effective_lowered = " ".join(str(user_input or "").lower().split())

    if (
        agent._looks_like_hive_topic_create_request(lowered)
        or agent._looks_like_hive_topic_update_request(lowered)
        or agent._looks_like_hive_topic_delete_request(lowered)
    ):
        return None

    # A turn that rejects the previous answer is about the conversation, never about the profile.
    # This declines BEFORE the pending branch on purpose: the v0.5.0 smoke run's correction ("No,
    # that is wrong. Lumen is a photo editor, not a note-taking app. Fix the description.") is the
    # exact shape a live `awaiting_rename` step would have swallowed whole and written as a display
    # name, and a fast path that has to guess should never be the thing that writes durable state.
    # Declining returns the turn to ordinary chat, where a correction belongs.
    if looks_like_conversational_correction_turn(raw_text) or looks_like_conversational_correction_turn(
        str(user_input or "")
    ):
        return None

    pending = agent._voolbook_pending.get(session_id)
    if pending:
        return agent._handle_voolbook_pending_step(
            raw_text,
            lowered,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    intent = agent._classify_voolbook_intent(lowered)
    if intent is None and effective_lowered != lowered:
        intent = agent._classify_voolbook_intent(effective_lowered)
    # A declined intent stays declined. Field-shaped data in another request is not
    # authority to mutate the social profile through a second, permissive parser.
    if intent is None:
        return None

    try:
        from core.voolbook_identity import get_profile, update_profile

        profile = get_profile(signer_module.get_local_peer_id())
    except Exception:
        profile = None

    if intent is None and profile and agent._looks_like_direct_social_post_request(lowered):
        return agent._handle_voolbook_post(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "post":
        return agent._handle_voolbook_post(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "delete":
        return agent._handle_voolbook_delete(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "edit":
        return agent._handle_voolbook_edit(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "twitter":
        if not profile:
            return agent._voolbook_result(session_id, raw_text, source_context, "You need a VoolBook profile first.")
        twitter_update = agent._extract_twitter_handle(raw_text)
        if twitter_update:
            try:
                update_profile(profile.peer_id, twitter_handle=twitter_update)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Twitter/X handle set to **@{twitter_update}**\n"
                    f"Visible on your VoolBook profile. Links to https://x.com/{twitter_update}",
                )
            except Exception as exc:
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to set Twitter handle: {exc}",
                )
        return agent._voolbook_result(
            session_id,
            raw_text,
            source_context,
            "What's the Twitter/X handle? Just the username, no @ needed.",
        )

    if intent == "bio":
        if not profile:
            return agent._voolbook_result(session_id, raw_text, source_context, "You need a VoolBook profile first.")
        bio_update = agent._extract_voolbook_bio_update(raw_text)
        if not bio_update:
            bio_update = re.sub(r"^bio\s*:\s*", "", raw_text.strip(), flags=re.IGNORECASE).strip()
        if bio_update:
            try:
                update_profile(profile.peer_id, bio=bio_update)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Bio updated: {bio_update}",
                )
            except Exception as exc:
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to update bio: {exc}",
                )
        return agent._voolbook_result(
            session_id,
            raw_text,
            source_context,
            "What do you want the bio to say?",
        )

    if intent == "compound_bio_twitter":
        if not profile:
            return agent._voolbook_result(session_id, raw_text, source_context, "You need a VoolBook profile first.")
        results = []
        bio_update = agent._extract_voolbook_bio_update(raw_text)
        if bio_update:
            try:
                update_profile(profile.peer_id, bio=bio_update)
                results.append(f"Bio updated: {bio_update}")
            except Exception as exc:
                results.append(f"Bio update failed: {exc}")
        twitter_update = agent._extract_twitter_handle(raw_text)
        if twitter_update:
            try:
                update_profile(profile.peer_id, twitter_handle=twitter_update)
                results.append(f"Twitter/X set to @{twitter_update}")
            except Exception as exc:
                results.append(f"Twitter update failed: {exc}")
        profile = get_profile(profile.peer_id)
        agent._sync_profile_to_hive(profile)
        return agent._voolbook_result(
            session_id,
            raw_text,
            source_context,
            "\n".join(results) if results else "Couldn't extract bio or twitter from your message.",
        )

    if intent == "rename":
        if not profile:
            return agent._voolbook_result(session_id, raw_text, source_context, "You need a VoolBook profile first.")
        desired_handle = agent._extract_handle_from_text(raw_text)
        display_name = agent._extract_display_name(raw_text)
        if display_name:
            try:
                update_profile(profile.peer_id, display_name=display_name)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Display name set to: {display_name}",
                )
            except Exception as exc:
                return agent._voolbook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to set display name: {exc}",
                )
        if desired_handle and desired_handle.lower() != profile.handle.lower():
            return agent._handle_voolbook_rename(
                desired_handle,
                profile,
                session_id=session_id,
                user_input=raw_text,
                source_context=source_context,
            )
        agent._voolbook_pending[session_id] = {"step": "awaiting_rename"}
        return agent._voolbook_result(
            session_id,
            raw_text,
            source_context,
            f"Current handle: **{profile.handle}**. What do you want to change it to?",
        )

    if intent in {"create", "check_profile"}:
        desired_handle = agent._extract_handle_from_text(raw_text)
        if profile:
            if desired_handle and desired_handle.lower() != profile.handle.lower():
                return agent._handle_voolbook_rename(
                    desired_handle,
                    profile,
                    session_id=session_id,
                    user_input=raw_text,
                    source_context=source_context,
                )
            display_info = (
                f"\nDisplay name: {profile.display_name}"
                if profile.display_name and profile.display_name != profile.handle
                else ""
            )
            twitter_display = f"\nTwitter/X: @{profile.twitter_handle}" if profile.twitter_handle else ""
            return agent._voolbook_result(
                session_id,
                raw_text,
                source_context,
                f"VoolBook profile active — handle: **{profile.handle}**{display_info}\n"
                f"Bio: {profile.bio or '(not set)'}{twitter_display}\n"
                f"Stats: {profile.post_count} posts, {profile.claim_count} topic claims.",
            )
        if desired_handle:
            agent._voolbook_pending[session_id] = {"step": "awaiting_handle"}
            return agent._voolbook_step_handle(
                desired_handle,
                desired_handle.lower(),
                session_id=session_id,
                source_context=source_context,
            )
        agent._voolbook_pending[session_id] = {"step": "awaiting_handle"}
        emoji_note = ""
        if "emoji" in lowered or "emojis" in lowered:
            emoji_note = "Handles are text-only. You can add emoji in the display name later.\n"
        return agent._voolbook_result(
            session_id,
            raw_text,
            source_context,
            "Let's set up your VoolBook profile.\n"
            f"{emoji_note}"
            "What handle would you like? Rules: 3-32 characters, letters, numbers, underscores, or hyphens.",
        )

    return None


def pending_reply_answers_question(step: str, user_input: str, agent: Any) -> bool:
    """Whether this message plausibly ANSWERS the pending step's own question.

    The owner rule (product decision, 2026-09-17): execution intent does not persist. A
    pending registration dialog may consume a reply to ITS question -- a handle, a bio, a
    post draft -- never a substantive new request. Measured live during the provider
    continuity test: a session with a half-finished registration answered "Write a
    JavaScript function called scoreRuns..." with "Registered as Alpha on VoolBook",
    because the awaiting-handle step swallowed the whole coding turn as the proposed
    handle. A new task means the operator moved on: the pending step is dropped and the
    turn returns to the ordinary lanes.

    Plausibility reuses the authorities the step itself already trusts -- the handle
    validator for `awaiting_handle`, and the shared build-instruction detector plus the
    message's own shape (code fences, requirement lists) for prose answers -- rather than
    a new vocabulary aimed at any wording.
    """
    text = str(user_input or "").strip()
    if not text:
        return False
    # A draft bio or post is conversational prose. Code and requirement lists are a task.
    has_code = "```" in text or "`" in text
    list_lines = sum(
        1
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\S", line)
    )
    if has_code or list_lines >= 2:
        return False
    word_count = len(text.split())
    if str(step) == "awaiting_handle":
        # A question about the pending flow itself ("can I add emojis next to the name?")
        # is part of this dialog -- the step's own rules-question recognizer says so.
        if agent._looks_like_voolbook_handle_rules_question(text, text.lower()):
            return True
        # The reply must be handle-shaped by the SAME validator that will accept it, or
        # carry an explicit "call me X" / "name: X" form. Anything else is not an answer:
        # a whole coding request is never a handle.
        candidate = agent._extract_handle_from_text(text)
        if candidate is None:
            stripped = text.strip().strip("\"'").strip()
            for prefix in (
                "name it ", "name is ", "call me ", "register ", "handle ", "name ", "use ",
                "set up this name ", "setup this name ", "set this name ", "setup name ",
                "set up name ",
            ):
                if text.lower().startswith(prefix):
                    stripped = text[len(prefix):].strip()
                    break
            candidate = stripped
        if candidate is None:
            return False
        from core.agent_name_registry import validate_agent_name

        valid, _reason = validate_agent_name(str(candidate).strip().strip("\"'.,!?"))
        return valid
    # Prose steps (bio, post draft): a draft is short conversational text. An imperative
    # build verb opening a longer message ("Write a JavaScript function called prepareTasks
    # that ...") is a new task, while the short replies this dialog actually takes ("ok
    # setup this name sls_0x", "make it funny") stay answers. The verb decision is the
    # shared build-instruction detector's own sentence reader. A direct arithmetic ask is
    # the same class of intruder: the frontdoor's math lane answers it, never this dialog.
    try:
        from core.agent_runtime.build_request_intent import imperative_build_sentences

        if imperative_build_sentences(text) and word_count >= 6:
            return False
    except Exception:
        pass
    try:
        from core.task_router import looks_like_direct_math_request

        if looks_like_direct_math_request(text):
            return False
    except Exception:
        pass
    return True


def handle_voolbook_pending_step(
    agent: Any,
    user_input: str,
    lowered: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    pending: dict[str, str],
    signer_module: Any,
) -> dict[str, Any] | None:
    if lowered in {"cancel", "nevermind", "stop", "no", "nah"}:
        agent._voolbook_pending.pop(session_id, None)
        return agent._voolbook_result(session_id, user_input, source_context, "VoolBook registration cancelled.")

    step = pending.get("step", "")

    if step and not pending_reply_answers_question(step, user_input, agent):
        # The operator sent a substantive new request, not an answer to the pending
        # question. Execution intent does not persist: the half-finished registration is
        # dropped and the turn returns to the ordinary lanes, which answer the actual task.
        agent._voolbook_pending.pop(session_id, None)
        return None

    if step == "awaiting_handle":
        return agent._voolbook_step_handle(
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
        )

    if step == "awaiting_bio":
        return agent._voolbook_step_bio(
            user_input,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    if step == "awaiting_post_content":
        agent._voolbook_pending.pop(session_id, None)
        content = user_input.strip()
        if not content:
            return agent._voolbook_result(session_id, user_input, source_context, "Post can't be empty.")
        try:
            from core.voolbook_identity import get_profile

            profile = get_profile(signer_module.get_local_peer_id())
        except Exception:
            profile = None
        if not profile:
            return agent._voolbook_result(session_id, user_input, source_context, "No VoolBook profile found.")
        return agent._execute_voolbook_post(
            content,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if step == "awaiting_post_confirmation":
        compact = " ".join(str(user_input or "").split()).strip().lower()
        if compact in {"no", "nah", "nope", "cancel", "stop"}:
            agent._voolbook_pending.pop(session_id, None)
            return agent._voolbook_result(session_id, user_input, source_context, "Okay, I won't post it.")
        if compact.startswith(("yes", "post it", "just post", "send it", "do it")) or agent._is_proceed_message(compact):
            agent._voolbook_pending.pop(session_id, None)
            content = str(pending.get("content") or "").strip()
            if not content:
                return agent._voolbook_result(
                    session_id,
                    user_input,
                    source_context,
                    "I lost the draft. Tell me the post text again.",
                )
            try:
                from core.voolbook_identity import get_profile

                profile = get_profile(signer_module.get_local_peer_id())
            except Exception:
                profile = None
            if not profile:
                return agent._voolbook_result(session_id, user_input, source_context, "No VoolBook profile found.")
            return agent._execute_voolbook_post(
                content,
                profile,
                session_id=session_id,
                source_context=source_context,
            )
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            "Reply `yes` to post it or `no` to cancel.",
        )

    if step == "awaiting_rename":
        agent._voolbook_pending.pop(session_id, None)
        new_name = user_input.strip()
        if not new_name:
            return agent._voolbook_result(session_id, user_input, source_context, "Name can't be empty.")
        try:
            from core.voolbook_identity import get_profile, update_profile

            profile = get_profile(signer_module.get_local_peer_id())
        except Exception:
            profile = None
        if not profile:
            return agent._voolbook_result(session_id, user_input, source_context, "No VoolBook profile found.")
        is_ascii_handle = bool(re.fullmatch(r"[A-Za-z0-9_\-]{3,32}", new_name))
        if is_ascii_handle:
            return agent._handle_voolbook_rename(
                new_name,
                profile,
                session_id=session_id,
                user_input=user_input,
                source_context=source_context,
            )
        try:
            update_profile(profile.peer_id, display_name=new_name[:64])
            profile = get_profile(profile.peer_id)
            agent._sync_profile_to_hive(profile)
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                f"Display name set to: {new_name[:64]}",
            )
        except Exception as exc:
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                f"Failed to set name: {exc}",
            )

    agent._voolbook_pending.pop(session_id, None)
    return None


def voolbook_step_handle(
    agent: Any,
    user_input: str,
    lowered: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    signer_module: Any,
) -> dict[str, Any]:
    handle = agent._extract_handle_from_text(user_input) or ""
    if not handle:
        if agent._looks_like_voolbook_handle_rules_question(user_input, lowered):
            agent._voolbook_pending[session_id] = {"step": "awaiting_handle"}
            emoji_note = ""
            if "emoji" in lowered or "emojis" in lowered:
                emoji_note = "Handles are text-only. You can add emoji in the display name later.\n"
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                "Let's set up your VoolBook profile.\n"
                f"{emoji_note}"
                "What handle would you like? Rules: 3-32 characters, letters, numbers, underscores, or hyphens.",
            )
        handle = agent._strip_context_subject_suffix(user_input).strip()
        for prefix in (
            "name it ",
            "name is ",
            "call me ",
            "register ",
            "handle ",
            "name ",
            "use ",
            "set up this name ",
            "setup this name ",
            "set this name ",
            "setup name ",
            "set up name ",
        ):
            if lowered.startswith(prefix):
                handle = agent._strip_context_subject_suffix(user_input).strip()[len(prefix) :].strip()
                break
    handle = handle.strip().strip("\"'").strip()

    from core.agent_name_registry import validate_agent_name

    valid, reason = validate_agent_name(handle)
    if not valid:
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            f"'{handle}' is not valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -):",
        )

    from core.agent_name_registry import get_peer_by_name
    from core.voolbook_identity import get_profile_by_handle

    if get_peer_by_name(handle) or get_profile_by_handle(handle):
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            f"'{handle}' is already taken. Try a different handle:",
        )

    try:
        from core.voolbook_identity import get_profile, register_voolbook_account

        peer_id = signer_module.get_local_peer_id()
        register_voolbook_account(handle, peer_id=peer_id)
        profile = get_profile(peer_id)
        if profile:
            agent._sync_profile_to_hive(profile)
    except Exception as exc:
        agent._voolbook_pending.pop(session_id, None)
        return agent._voolbook_result(session_id, user_input, source_context, f"Registration failed: {exc}")

    agent._voolbook_pending[session_id] = {"step": "awaiting_bio", "handle": handle}
    return agent._voolbook_result(
        session_id,
        user_input,
        source_context,
        f"Registered as **{handle}** on VoolBook!\n"
        "Want to set a bio? Type your bio, or say 'skip' to finish.",
    )


def voolbook_step_bio(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    pending: dict[str, str],
) -> dict[str, Any]:
    handle = pending.get("handle", "")
    agent._voolbook_pending.pop(session_id, None)
    lowered = user_input.strip().lower()
    if lowered in {"skip", "no", "later", "nah", "pass"}:
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            f"Profile ready! Handle: **{handle}**\n"
            "You can post with: 'post to VoolBook: <your message>'",
        )
    bio_text = agent._extract_voolbook_bio_update(user_input) or re.sub(
        r"^bio\s*[:=]\s*",
        "",
        agent._strip_context_subject_suffix(user_input).strip(),
        flags=re.IGNORECASE,
    ).strip()
    try:
        from core.voolbook_identity import get_profile_by_handle, update_profile

        profile = get_profile_by_handle(handle)
        if profile:
            update_profile(profile.peer_id, bio=bio_text[:500])
            profile = get_profile_by_handle(handle)
            agent._sync_profile_to_hive(profile)
    except Exception:
        pass
    return agent._voolbook_result(
        session_id,
        user_input,
        source_context,
        f"Profile ready! Handle: **{handle}**\nBio: {bio_text[:500]}\n"
        "You can post with: 'post to VoolBook: <your message>'",
    )


def handle_voolbook_post(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        agent._voolbook_pending[session_id] = {"step": "awaiting_handle"}
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            "You need a VoolBook profile first. What handle would you like?",
        )

    content = agent._extract_post_content(user_input)
    if not content:
        agent._voolbook_pending[session_id] = {"step": "awaiting_post_content"}
        return agent._voolbook_result(session_id, user_input, source_context, "What would you like to post?")

    return agent._execute_voolbook_post(
        content,
        profile,
        session_id=session_id,
        source_context=source_context,
    )


def execute_voolbook_post(
    agent: Any,
    content: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    clean_content = agent._strip_context_subject_suffix(content).strip()
    if not agent._is_substantive_post_content(clean_content):
        agent._voolbook_pending[session_id] = {"step": "awaiting_post_content"}
        return agent._voolbook_result(
            session_id,
            clean_content or content,
            source_context,
            "That doesn't include real post text yet. What should I post to VoolBook?",
        )
    try:
        from core.voolbook_identity import increment_post_count
        from storage.voolbook_store import create_post

        post = create_post(
            peer_id=profile.peer_id,
            handle=profile.handle,
            content=clean_content[:5000],
            post_type="social",
            origin_kind="ai",
            origin_channel="runtime_fast_path",
            origin_peer_id=profile.peer_id,
        )
        increment_post_count(profile.peer_id)
        sync_result = {"ok": False}
        with contextlib.suppress(Exception):
            sync_result = agent.public_hive_bridge.sync_voolbook_post(
                peer_id=profile.peer_id,
                handle=profile.handle,
                bio=profile.bio or "",
                content=clean_content[:5000],
                post_type="social",
                twitter_handle=profile.twitter_handle or "",
                display_name=profile.display_name or "",
            )
        display = profile.display_name or profile.handle
        sync_status = " (live on voolbook.com)" if sync_result.get("ok") else ""
        return agent._voolbook_result(
            session_id,
            clean_content,
            source_context,
            f"Posted to VoolBook as **{display}**{sync_status}:\n"
            f"> {clean_content[:200]}\n\n"
            f"Post ID: {post.post_id}",
        )
    except Exception as exc:
        return agent._voolbook_result(session_id, clean_content, source_context, f"Failed to post: {exc}")


def voolbook_result(
    agent: Any,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    response: str,
) -> dict[str, Any]:
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason="voolbook_fast_path",
    )


def sync_profile_to_hive(agent: Any, profile: Any) -> None:
    try:
        bridge = getattr(agent, "public_hive_bridge", None)
        if bridge is None:
            return
        bridge.sync_voolbook_profile(
            peer_id=profile.peer_id,
            handle=profile.handle,
            bio=profile.bio or "",
            display_name=profile.display_name or "",
            twitter_handle=profile.twitter_handle or "",
        )
    except Exception:
        pass


def is_voolbook_post_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"(?:post\s+(?:to|on)\s+(?:voolbook|vool\s*book)|"
            r"(?:voolbook|vool\s*book)\s+post|"
            r"do\s+(?:a\s+)?(?:first\s+|our\s+)?post|"
            r"let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post)",
            lowered,
        )
    )


def is_voolbook_delete_request(lowered: str) -> bool:
    return bool(re.search(r"(?:delete|remove)\s+(?:my\s+)?(?:voolbook\s+)?post", lowered))


def is_voolbook_edit_request(lowered: str) -> bool:
    return bool(re.search(r"(?:edit|update|change)\s+(?:my\s+)?(?:voolbook\s+)?post", lowered))


def handle_voolbook_delete(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        return agent._voolbook_result(session_id, user_input, source_context, "You need a VoolBook profile first.")
    post_id = agent._extract_post_id(user_input)
    if not post_id:
        try:
            from storage.voolbook_store import list_user_posts

            recent = list_user_posts(profile.handle, limit=5)
            social = [post for post in recent if post.post_type == "social"]
            if not social:
                return agent._voolbook_result(
                    session_id,
                    user_input,
                    source_context,
                    "You don't have any social posts to delete.",
                )
            if len(social) == 1:
                post_id = social[0].post_id
            else:
                lines = ["Which post do you want to delete?\n"]
                for post in social:
                    lines.append(f"- `{post.post_id}`: {post.content[:60]}...")
                lines.append("\nSay: delete post <post_id>")
                return agent._voolbook_result(session_id, user_input, source_context, "\n".join(lines))
        except Exception:
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                "Couldn't list your posts. Try: delete post <post_id>",
            )
    try:
        from storage.voolbook_store import delete_post

        ok = delete_post(post_id, profile.peer_id)
        if ok:
            with contextlib.suppress(Exception):
                agent.public_hive_bridge._post_json(
                    str(agent.public_hive_bridge.config.topic_target_url),
                    f"/v1/voolbook/post/{post_id}/delete",
                    {"voolbook_peer_id": profile.peer_id},
                )
            return agent._voolbook_result(session_id, user_input, source_context, f"Deleted post `{post_id}`.")
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            "Couldn't delete that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be deleted).",
        )
    except Exception as exc:
        return agent._voolbook_result(session_id, user_input, source_context, f"Delete failed: {exc}")


def handle_voolbook_edit(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        return agent._voolbook_result(session_id, user_input, source_context, "You need a VoolBook profile first.")
    post_id = agent._extract_post_id(user_input)
    new_content = agent._extract_edit_content(user_input)
    if not post_id or not new_content:
        try:
            from storage.voolbook_store import list_user_posts

            recent = list_user_posts(profile.handle, limit=5)
            social = [post for post in recent if post.post_type == "social"]
            if not social:
                return agent._voolbook_result(
                    session_id,
                    user_input,
                    source_context,
                    "You don't have any social posts to edit.",
                )
            lines = ["Specify the post and new content:\n"]
            for post in social:
                lines.append(f"- `{post.post_id}`: {post.content[:60]}...")
            lines.append("\nSay: edit post <post_id> to: <new content>")
            return agent._voolbook_result(session_id, user_input, source_context, "\n".join(lines))
        except Exception:
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                "Try: edit post <post_id> to: <new content>",
            )
    try:
        from storage.voolbook_store import update_post

        updated = update_post(post_id, profile.peer_id, new_content)
        if updated:
            with contextlib.suppress(Exception):
                agent.public_hive_bridge._post_json(
                    str(agent.public_hive_bridge.config.topic_target_url),
                    f"/v1/voolbook/post/{post_id}/edit",
                    {"voolbook_peer_id": profile.peer_id, "content": new_content},
                )
            return agent._voolbook_result(
                session_id,
                user_input,
                source_context,
                f"Updated post `{post_id}`:\n> {new_content[:200]}",
            )
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            "Couldn't edit that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be edited).",
        )
    except Exception as exc:
        return agent._voolbook_result(session_id, user_input, source_context, f"Edit failed: {exc}")


def extract_post_id(text: str) -> str:
    match = re.search(r"\b([a-f0-9]{12,16})\b", text)
    return match.group(1) if match else ""


def extract_edit_content(text: str) -> str:
    match = re.search(r"(?:to|with|new\s*content)\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip()[:5000] if match else ""


def is_voolbook_create_request(lowered: str) -> bool:
    if "sign up" in lowered:
        return True
    return bool(
        re.search(
            r"(?:create|make|set\s*up|start|open|get)\s+(?:a\s+|my\s+|an?\s+)?(?:voolbook\s+)?(?:profile|account)",
            lowered,
        )
    ) or bool(
        re.search(
            r"(?:register|sign\s*up)\s+(?:on|for|to|with)?\s*(?:voolbook|vool\s*book)",
            lowered,
        )
    )


def extract_voolbook_bio_update(text: str) -> str:
    for pattern in (
        r"(?:set|update|change)\s+(?:my\s+)?bio\s+(?:to\s+)?[\"'](.+?)[\"']",
        r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
        r"^bio\s*[:=]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
        r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)$",
        r"^bio\s*[:=]\s*(.+)$",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'").strip()
    return ""


def extract_twitter_handle(text: str) -> str:
    for pattern in (
        r"(?:set|update|add|change)\s+(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?(?:\s+(?:in|on)\s+(?:my\s+)?(?:voolbook|vool\s*book)\s+profile)?\s*(?:to|as|:)\s*@?([A-Za-z0-9_]{1,15})\b",
        r"(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?\s*(?:is|:)\s*@?([A-Za-z0-9_]{1,15})\b",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_handle_from_text(text: str) -> str | None:
    for pattern in (
        r"(?:set\s+)?(?:my\s+)?(?:profile\s+)?(?:name|handle)\s*[:=]\s*(\S+)",
        r"(?:my\s+)?(?:profile\s+)?(?:name|handle)\s+(?:there\s+)?(?:will\s+be|should\s+be|is|be)\s+(\S+)",
        r"(?:call|name)\s+me\s+(\S+)",
        r"(?:i\s+want\s+to\s+be|i\'?ll?\s+be|i\'?m)\s+(\S+)",
        r"(?:register|sign\s*up)\s+(?:as|with)\s+(\S+)",
        r"(?:change|rename|switch|set)\s+(?:my\s+)?(?:name|handle)\s+(?:to\s+)?(\S+)",
        r"(?:ok\s+)?(?:set\s*up|setup)\s+(?:this\s+)?(?:name|handle)\s+(?:to\s+)?(\S+)",
        r"(?:use|pick|choose)\s+(\S+)\s+(?:as\s+)?(?:my\s+)?(?:name|handle)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().strip("\"'.,!?")
            if len(candidate) >= 3:
                return candidate
    return None


def looks_like_voolbook_handle_rules_question(text: str, lowered: str) -> bool:
    if "?" not in text:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "emoji",
            "emojis",
            "text only",
            "can i",
            "could i",
            "do you know",
            "what are the rules",
            "rules",
            "letters",
            "numbers",
            "underscores",
            "hyphens",
        )
    )


def extract_post_content(text: str) -> str:
    raw = strip_context_subject_suffix(text)
    for pattern in (
        r"(?:post\s+(?:to|on)\s+(?:voolbook|vool\s*book)|(?:voolbook|vool\s*book)\s+post)\s*[:\-]\s*(.+)",
        r"(?:let.s|do)\s+(?:(?:do|a)\s+)?(?:a\s+|first\s+|our\s+)?post\s*[:\-]\s*(.+)",
        r"(?:first\s+(?:our\s+)?post|our\s+first\s+post)\s*[:\-]\s*(.+)",
        r"(?:post\s+new\s+social\s+post|new\s+social\s+post|social\s+post|test\s+post|do\s+the\s+(?:test\s+)?post)\s*[:\-]\s*(.+)",
        r"post\s+(?:it|this)\s*[:\-]\s*(.+)",
    ):
        match = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        if match:
            candidate = match.group(1).strip().strip("\"'").strip()
            return candidate if is_substantive_post_content(candidate) else ""
    for prefix in (
        "post to voolbook",
        "post on voolbook",
        "voolbook post",
        "post to vool book",
        "post on vool book",
        "vool book post",
    ):
        lowered = raw.lower()
        index = lowered.find(prefix)
        if index >= 0:
            after = raw[index + len(prefix) :].strip()
            if after and after[0] in ":- ":
                after = after[1:].strip()
            if after:
                candidate = after.strip("\"'").strip()
                return candidate if is_substantive_post_content(candidate) else ""
    return ""


def is_substantive_post_content(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    return bool(re.search(r"[A-Za-z0-9]", clean))


def looks_like_direct_social_post_request(lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact:
        return False
    return bool(
        re.search(
            r"(?:social\s+post|test\s+post|post\s+this|post\s+that|post\s+it|post\s+new\s+social\s+post|do\s+the\s+(?:test\s+)?post)",
            compact,
        )
    )


def strip_context_subject_suffix(text: str) -> str:
    raw = str(text or "")
    return re.sub(
        r"\s+Context subject:\s*[^.\n]+\.?\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()


def extract_display_name(text: str) -> str:
    """The new display name a message explicitly asks for, or "".

    Every pattern must name the field being changed. The two that did not -- `change (it|this|that)
    to <rest>` and a bare `chang\\w* <word> to <rest>` -- are gone: they turned "Change it to a photo
    editor." into a persistent profile write (QA-050-025), and they were the second half of the same
    defect as the classifier arm that routed the sentence here in the first place. A caller that has
    genuinely established the user is renaming their profile does not need them either: the
    `awaiting_rename` step takes the whole reply as the name.
    """
    for pattern in (
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:voolbook|vool\s*book)\s+)?(?:display\s+)?(?:name|handle)\s+(?:to\s+)(.+)",
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:voolbook|vool\s*book)\s+)?(?:display\s+)?(?:name|handle)\s*[:=]\s*(.+)",
        r"(?:change|rename|switch|set|update)\s+(?:my\s+|our\s+)?(?:voolbook|vool\s*book)(?:\s+profile)?\s+to\s+(.+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().strip("\"'.,!?").strip()
            if candidate:
                return candidate[:64]
    return ""


def handle_voolbook_rename(
    agent: Any,
    new_handle: str,
    profile: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    from core.agent_name_registry import validate_agent_name

    valid, reason = validate_agent_name(new_handle)
    if not valid:
        return agent._voolbook_result(
            session_id,
            user_input,
            source_context,
            f"'{new_handle}' isn't valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -).",
        )

    try:
        from core.voolbook_identity import rename_handle

        updated = rename_handle(profile.peer_id, new_handle)
    except ValueError as exc:
        return agent._voolbook_result(session_id, user_input, source_context, str(exc))
    except Exception as exc:
        return agent._voolbook_result(session_id, user_input, source_context, f"Rename failed: {exc}")

    return agent._voolbook_result(
        session_id,
        user_input,
        source_context,
        f"Done! Handle changed: **{profile.handle}** → **{updated.handle}**\n"
        "You can post with: 'post to VoolBook: <your message>'",
    )
