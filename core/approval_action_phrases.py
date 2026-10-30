"""The person-facing words of an approval ask, one keyed phrase per action.

The ask must say what will happen in everyday words ("VOOL wants to make a new folder called
Receipts."), never a tool's dotted id and never its developer-facing contract description
("Create a directory inside a safe local directory...", "Run one bounded shell command...").
The English source of every phrase lives here AND byte-identical in en.json under
``approval.action.<intent>`` (a test pins the mirror), so the page, the payload and the
transcript mint all read the same words a person reads.

Coverage law: every intent that can REQUIRE APPROVAL under the mode-permission matrix has a
keyed phrase; a tool without one (a plugin, an MCP tool, anything unknown, plus any builtin
that can never ask) gets the one general sentence -- by construction, never its id, never a
contract description. The words avoid the vocabulary of the contracts on purpose: a person
reads "folder", "file", "email", "command"; not "directory", "workspace", "shell", "HTTP".
"""

from __future__ import annotations

from typing import Any, Mapping

#: intent -> the action in everyday words (the English catalog source).
APPROVAL_ACTION_PHRASES: dict[str, str] = {
    "ai.paid_ask": "ask a paid AI service one question",
    "code.task.rollback": "undo all the changes this task made",
    "code.task.step": "carry out one step of this task",
    "contacts.delete": "delete a saved contact",
    "contacts.save": "save a contact",
    "contacts.update": "change a saved contact",
    "email.draft.reconcile": "check whether a sent draft really went out",
    "email.draft.send": "send an email you already approved",
    "email.open": "open one email",
    "email.read": "read your email",
    "email.reply": "reply to an email",
    "email.send": "send an email",
    "file.deliver": "make a file for you to download",
    "image.generate": "make a picture",
    "machine.ensure_directory": "make a new folder",
    "machine.move_path": "move or rename a file",
    "machine.write_file": "save changes to a file",
    "market.collect": "collect trading data about a coin",
    "marketplace.purchase_knowledge": "buy access to a knowledge listing",
    "marketplace.search_listings": "search the marketplace listings",
    "media.edit": "make one edit to a media project",
    "media.export": "save the edited project as a new file",
    "media.open": "open a media file for editing",
    "media.redo": "redo the last undone edit",
    "media.undo": "undo the last edit",
    "operator.check_availability": "check when you are free on a given day",
    "operator.cleanup_temp_files": "delete temporary files from this computer",
    "operator.inspect_calendar_event": "show one event from your calendar",
    "operator.list_calendars": "list your calendars",
    "operator.move_path": "move or rename a file",
    "operator.propose_calendar_event": "add an event to your calendar",
    "operator.save_note": "save a note",
    "operator.schedule_calendar_event": "add an event to your calendar",
    "operator.search_calendar_event": "search your calendar",
    "operator.show_agenda": "show what your day looks like",
    "operator.update_calendar_event": "change a calendar event",
    "orchestration.execute_envelope": "run a helper agent task",
    "pay.x402": "make a crypto payment",
    "profile.forget": "forget something it knows about you",
    "profile.remember": "remember something about you",
    "repo.ci.artifacts": "list the files a build produced",
    "repo.ci.cancel": "cancel a running build",
    "repo.ci.jobs": "list the builds for this change",
    "repo.ci.log": "show a build's log",
    "repo.ci.rerun": "run a build again",
    "repo.diff": "show the changes of the linked pull request",
    "repo.git": "make a git change (a commit, branch or tag)",
    "repo.issue": "read one issue from the code site",
    "repo.issue.comments": "read the comments on one issue",
    "repo.pr.authorize": "approve a pull request plan",
    "repo.pr.comment": "post a comment on a pull request or issue",
    "repo.pr.create": "open a draft pull request",
    "repo.pr.request": "plan a draft pull request",
    "repo.pr.update": "change a pull request's title or description",
    "repo.push": "send the saved changes to the shared code site",
    "repo.push.authorize": "approve a push plan",
    "repo.step": "carry out one step of this session",
    "repo.verify_remote": "check what the code site actually received",
    "respond.direct": "answer you directly",
    "sandbox.run_command": "run a command on this computer",
    "set.save": "save a creative set",
    "skill.create": "write a new skill",
    "skill.export": "package a skill to share",
    "skill.import": "bring in a skill from a file",
    "skill.install": "turn on a skill",
    "skill.pack": "bundle skills into a pack",
    "skill.remove": "turn off a skill",
    "skill.rollback": "restore an older version of a skill",
    "token.flow": "look at a coin's trading activity right now",
    "token.launches": "list the newest coins created",
    "token.performance": "show how a coin's price has moved",
    "token.research": "research a coin from public data",
    "video.generate": "make a video",
    "vool-browser.cancel": "stop the background browser",
    "vool-browser.checkout.begin": "fill in your name and address at a shop's checkout",
    "vool-browser.checkout.handoff": "hand the payment over to you",
    "vool-browser.download": "download a file for the browser session",
    "vool-browser.order.reconcile": "check whether your order went through",
    "vool-browser.profile.confirm": "save your confirmed checkout details",
    "vool-browser.screenshot": "take a picture of the page the browser is on",
    "vool-browser.session.close": "close the background browser",
    "vool-browser.session.open": "open a web page in a private browser",
    "vool-browser.upload": "attach files to a form on the page",
    "vool-browser.upload.stage": "prepare files to be uploaded",
    "wallet.spend": "spend money from the wallet",
    "watch.add": "start watching a wallet or a coin price",
    "watch.list": "list your watches",
    "watch.pause": "pause a watch",
    "watch.remove": "stop watching a wallet or a coin",
    "watch.resume": "resume a watch",
    "web.chain_lookup": "look up blockchain data",
    "web0.add_block": "add a piece to a web page",
    "web0.add_gated_section": "add a locked section to a web page",
    "web0.compile_preview": "build a preview of the web page",
    "web0.create_project": "start a new website",
    "web0.encrypt_whole_site": "lock the whole site's text behind a wallet check",
    "web0.fill_slots": "fill in the site's text slots",
    "web0.open_builder_draft": "open a draft of the web page",
    "web0.publish": "publish the site online",
    "workspace.apply_unified_diff": "apply a set of changes to files",
    "workspace.ensure_directory": "make a new folder",
    "workspace.replace_in_file": "replace text inside a file",
    "workspace.rollback_last_change": "undo the last change it made here",
    "workspace.run_formatter": "tidy up the code's layout",
    "workspace.run_lint": "check the code for style problems",
    "workspace.run_tests": "run the tests",
    "workspace.write_file": "save changes to a file",
    "x.account": "look at your connected X account's details",
    "x.action.propose": "post, like or reply on X as you",
    "x.dms": "read your X direct messages",
    "x.follows": "list an account's followers on X",
    "x.lists": "look at X lists",
    "x.post": "read posts on X",
    "x.search": "search posts on X",
    "x.spaces": "look at X Spaces (live audio rooms)",
    "x.token_callers": "find who posted about a coin on X",
    "x.trending": "see what is trending on X",
    "x.user": "look up an X account",
    "x.user_posts": "read an account's posts on X",
}

#: intent -> how the ask names the target when it knows one. "name" reads "called Receipts"
#: (folders, files, contacts); "to" reads "to ann@example.com" (people written to). Intents
#: absent from this table name no target: their phrase already says everything.
APPROVAL_TARGET_KINDS: dict[str, str] = {
    "contacts.delete": "name",
    "contacts.save": "name",
    "contacts.update": "name",
    "email.draft.send": "to",
    "email.reply": "to",
    "email.send": "to",
    "file.deliver": "name",
    "machine.ensure_directory": "name",
    "machine.move_path": "name",
    "machine.write_file": "name",
    "media.export": "name",
    "media.open": "name",
    "operator.move_path": "name",
    "set.save": "name",
    "skill.create": "name",
    "web0.create_project": "name",
    "workspace.apply_unified_diff": "name",
    "workspace.ensure_directory": "name",
    "workspace.replace_in_file": "name",
    "workspace.write_file": "name",
}

_TARGET_CONNECTOR_KEYS = {"name": "approval.target.called", "to": "approval.target.to"}

_GENERAL_APPROVAL_ASK = "This request needs your approval first."


def approval_action_key(intent: str) -> str:
    """The catalog key of the phrase, or "" when the intent has no keyed phrase."""
    name = str(intent or "").strip().lower()
    return f"approval.action.{name}" if name in APPROVAL_ACTION_PHRASES else ""


def approval_action_phrase(intent: str) -> str:
    """The action in everyday words (English catalog source); "" when none is keyed."""
    return APPROVAL_ACTION_PHRASES.get(str(intent or "").strip().lower(), "")


def approval_target_kind(intent: str) -> str:
    return APPROVAL_TARGET_KINDS.get(str(intent or "").strip().lower(), "")


def _short_name(value: str) -> str:
    """The everyday name of a path-like target: its own last segment, never a whole path
    (a path names folders the person never gave), and never empty."""
    text = str(value or "").strip().rstrip("/\\")
    if not text:
        return ""
    return text.replace("\\", "/").rsplit("/", 1)[-1].strip()


def approval_target_name(intent: str, arguments: Mapping[str, Any] | None) -> str:
    """The target's own name (folder name, file name, recipient), when the ask can know it.

    Only the name a person gave or would recognise: the LAST segment of a path-like
    argument, or the address a message goes to. Never a path, never an id.
    """
    args = arguments if isinstance(arguments, Mapping) else {}
    kind = approval_target_kind(intent)
    if kind == "to":
        raw = args.get("to")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        return _short_name(str(raw or args.get("recipient") or ""))
    if kind == "name":
        for key in ("path", "target", "destination"):
            raw = args.get(key)
            if isinstance(raw, (list, tuple)):
                raw = raw[0] if raw else ""
            name = _short_name(str(raw or ""))
            if name:
                return name
    return ""


def _catalog_text(locale: str, key: str, english: str, **params: Any) -> str:
    """The key's text in the page's app language; the English source on any failure.

    Placeholders are filled by plain replacement, not the catalog's HTML-escaping formatter:
    this text is read as chat and card prose, where an escaped "&amp;" would be visible
    garbage, not markup.
    """
    def _fill(template: str) -> str:
        text = template
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
        return text

    if not locale or locale == "en":
        return _fill(english)
    try:
        from core.i18n.catalog import catalog_for

        catalog = catalog_for(locale)
        if key not in catalog.locale_keys:
            return _fill(english)
        return _fill(catalog.text(key))
    except Exception:
        return _fill(english)


def localized_target_words(
    intent: str, locale: str, arguments: Mapping[str, Any] | None
) -> str:
    """The target clause ("called Receipts", "to ann@example.com") in the app language."""
    kind = approval_target_kind(intent)
    name = approval_target_name(intent, arguments)
    if not kind or not name:
        return ""
    key = _TARGET_CONNECTOR_KEYS[kind]
    english = {"name": "called {name}", "to": "to {name}"}[kind]
    return _catalog_text(locale, key, english, name=name)


def localized_action_words(
    intent: str, locale: str, arguments: Mapping[str, Any] | None = None
) -> str:
    """The action phrase plus its target clause ("make a new folder called Receipts").

    "" when the intent has no keyed phrase: the caller then uses its general fallback.
    """
    phrase = approval_action_phrase(intent)
    if not phrase:
        return ""
    words = _catalog_text(locale, approval_action_key(intent), phrase)
    target = localized_target_words(intent, locale, arguments)
    return f"{words} {target}".strip() if target else words


def localized_approval_ask(
    intent: str, locale: str, arguments: Mapping[str, Any] | None = None
) -> str:
    """The leading sentences of the approval ask in the app language.

    "VOOL wants to make a new folder called Receipts. This needs your approval first."
    A tool without a keyed phrase gets the one general sentence -- never its id, never a
    contract description.
    """
    words = localized_action_words(intent, locale, None)
    if not words:
        return _catalog_text(locale, "approval.general", _GENERAL_APPROVAL_ASK)
    target = localized_target_words(intent, locale, arguments)
    if target:
        lead = _catalog_text(
            locale, "approval.ask_wants_named", "VOOL wants to {action} {target}.",
            action=words, target=target,
        )
    else:
        lead = _catalog_text(locale, "approval.ask_wants", "VOOL wants to {action}.", action=words)
    tail = _catalog_text(locale, "approval.needs_first", "This needs your approval first.")
    # The sentences carry their own terminal punctuation in each locale's catalog values
    # ("。" in Japanese, "." elsewhere), so they join with a space, not an ASCII ". ".
    return f"{lead} {tail}"
