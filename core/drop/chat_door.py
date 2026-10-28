"""What VOOL says when a chat asks to send something to, or hand a job to, another of the owner's computers.

GLM recheck R9 (D01-D04, L01-L03): "send the invoice in my downloads to my laptop", "drop a quick note on
my phone saying buy oat milk", "hand the whole thing to my other machine and dont ask me any questions".
With Drop and lending off (the default) the model had no way to do any of it, and answered with a task
status that had nothing to do with the ask, a question about the phone's brand, or "Got it. I'll pass
the whole thing to your other machine". Nothing said what the feature is called, that it is off, or
where to turn it on.

This door answers before the model, in the app language: the feature's name in VOOL, that it is off
and nothing was sent or handed over, and the real Settings place with the page's own words; when the
feature is on but no computer is paired, how to pair. When it is on and paired, the turn is left to
the existing flow. Nothing is ever sent from here.
"""
from __future__ import annotations

import re
from typing import Any

DROP_OFF_KEY = "drop.chat.off"
DROP_OFF_EN = (
    "In VOOL, sending a file or a note to another of your computers is called {drop}. It is off, so "
    "nothing was sent. To turn it on, open {settings} > {group} > {drop}, tick “{enable}”, choose a "
    "{folder} that both computers can open, and press {save}."
)
DROP_UNPAIRED_KEY = "drop.chat.unpaired"
DROP_UNPAIRED_EN = "{drop} is on, but this computer is not paired with another one yet, so nothing was sent."
DROP_PAIR_KEY = "drop.chat.pair"
DROP_PAIR_EN = (
    "To pair, open {settings} > {group} > {drop} on both computers: 1. Turn Drop on there with the same "
    "{folder}. 2. Each computer shows “{fingerprint}”: type the other computer's into “{peer}” and tick the "
    "check box under it. "
    "3. Press {invite} on one computer, then paste the {link} on the other and press {join}."
)
LEND_OFF_KEY = "lend.chat.off"
LEND_OFF_EN = (
    "In VOOL, handing a job to another of your computers is called {lend}. It is off, so nothing was "
    "handed over. It needs {drop} on first: open {settings} > {group} > {drop}, tick “{enable}”, choose a "
    "{folder} that both computers can open, and press {save}. Then, in {settings} > {group} > {lend}, "
    "tick “{lend_box}” and press {save}. It can take these jobs: {jobs}."
)
LEND_UNPAIRED_KEY = "lend.chat.unpaired"
LEND_UNPAIRED_EN = (
    "{lend} is on, but this computer is not paired with another one yet, so nothing was handed over. "
    "It can take these jobs: {jobs}."
)
LEND_PAIR_KEY = "lend.chat.pair"
LEND_PAIR_EN = (
    "To pair, open {settings} > {group} > {lend} on both computers: 1. Each computer shows "
    "“{fingerprint}”: type the other computer's into “{peer}” and tick the check box under it. 2. On the computer that will do the "
    "job, choose {host}, pick the job and press {invite}. 3. On this computer, choose {borrow}, paste the "
    "{link}, press {review} and then {join}."
)
KEYS = (DROP_OFF_KEY, DROP_UNPAIRED_KEY, DROP_PAIR_KEY, LEND_OFF_KEY, LEND_UNPAIRED_KEY, LEND_PAIR_KEY)

#: The words the reply names, read from the catalogs the Settings page itself shows.
_PLACE_KEYS = (
    ("settings", "sidebar.settings", "Settings"),
    ("group", "settings.group.backup.title", "Backup & restore"),
    ("drop", "settings.row.drop.label", "Send with Drop"),
    ("lend", "settings.row.lend.label", "Lend jobs to friends"),
    ("enable", "drop.enable", "Enable Drop and make this VOOL folder private"),
    ("folder", "drop.relay", "Shared folder"),
    ("save", "notif.save", "Save"),
    ("fingerprint", "drop.fingerprint", "Your fingerprint"),
    ("peer", "drop.peer", "Their fingerprint"),
    ("invite", "drop.invite", "Create invite"),
    ("link", "drop.link", "Invite link"),
    ("join", "notif.join", "Join"),
    ("lend_box", "lend.title", "Lend jobs to friends"),
    ("host", "lend.host", "Lend a job"),
    ("borrow", "lend.guest", "Borrow a job"),
    ("review", "lend.review", "Review invitation"),
)
_JOB_KEYS = (
    ("lend.cap_contact_sheet", "Make a contact sheet from photos"),
    ("lend.cap_clean_pdf", "Turn a document into a clean PDF"),
    ("lend.cap_summary", "Summarise a document with a local model"),
    ("lend.cap_translation", "Translate a document with a local model"),
    ("lend.cap_recording", "Trim or convert a recording"),
)
_WARNINGS = {"drop": ("drop.warning", "Not reviewed. Do not use for real messages."),
             "lend": ("lend.warning", "Not reviewed yet. Use test files only.")}

#: One of the owner's own computers or phones ("my laptop", "the big machine downstairs", "my other computer").
_DEVICE = (
    r"(?:laptop|computer|pc|mini\s*pc|mac(?:book)?(?:\s+(?:pro|air|mini))?|imac|desktop|phone|iphone|"
    r"android|tablet|ipad|machine|device|workstation|rig)"
)
_MINE = r"(?:my|the|our)\s+(?:[\w'-]+\s+){0,3}?" + _DEVICE + r"\b"
#: Send a file or a note to one of them.
_DROP_ASK = re.compile(
    r"\b(?:send|push|drop|move|transfer|copy|put|share|beam|airdrop|throw|forward|get)\b[^.?!]{0,80}?"
    r"\b(?:to|on|onto|into|over\s+to|across\s+to)\s+" + _MINE,
    re.I,
)
#: Hand a job to another VOOL or computer.
_LEND_ASK = re.compile(
    r"\b(?:my|the|our)\s+(?:\w+\s+)?(?:other|second)\s+vool\b"
    r"|\b(?:let|have|get|make)\s+" + _MINE + r"[^.?!]{0,40}?\b(?:do|finish|run|handle|take|process|work)\b"
    r"|\b(?:hand|pass|give|offload|shift|move|send|push)\b\s+(?:[\w'-]+\s+){0,3}?"
    r"(?:job|jobs|task|tasks|thing|work|rest|processing)\b(?:\s+over)?\s+(?:to|onto)\s+" + _MINE
    + r"|" + _MINE + r"[^.?!]{0,30}?\b(?:do|finish|take\s+over|handle|run)\s+(?:it|this|that|the\s+job)\b",
    re.I,
)
#: A how-to or what-is question about the owner's other computer is a question, not an ask to send.
_QUESTION = re.compile(
    r"^\s*(?:how|what|which|why|where|when|is\s+there|are\s+there|does|do\s+i|should\s+i)\b"
    r"|\bhow\s+(?:do|can|to|would|should|could)\b|\bis\s+there\s+a\s+way\b",
    re.I,
)
#: Mail and messaging apps are their own lanes.
_OTHER_CHANNEL = re.compile(
    r"\b(?:e-?mail(?:s|ed|ing)?|gmail|outlook|slack|whatsapp|telegram|signal|sms|imessage|text\s+message)\b|@",
    re.I,
)
_NEGATED = re.compile(r"\b(?:do\s+not|don(?:'|’)?t|dont|never|without)\s+(?:\w+\s+){0,2}?(?:send|push|drop|move|"
                      r"transfer|copy|put|share|hand|pass|give)\b", re.I)
#: "the macbook, yes": a short follow-up naming the computer, after a Drop or lending ask.
_FOLLOW_UP_DEVICE = re.compile(r"\b" + _DEVICE + r"\b", re.I)


def kind_of_ask(text: str) -> str:
    """'lend' for handing a job to another computer, 'drop' for sending a file or note to one, else ''."""
    value = str(text or "").strip()
    if not value or _QUESTION.search(value) or _OTHER_CHANNEL.search(value) or _NEGATED.search(value):
        return ""
    if _LEND_ASK.search(value):
        return "lend"
    if _DROP_ASK.search(value):
        return "drop"
    return ""


def _follow_up_kind(text: str, session_id: str) -> str:
    """The kind of the previous ask when this turn only names the computer ("the macbook, yes")."""
    value = str(text or "").strip()
    if not value or len(value.split()) > 6 or not _FOLLOW_UP_DEVICE.search(value):
        return ""
    try:
        from storage.dialogue_memory import recent_dialogue_turns

        turns = recent_dialogue_turns(session_id, limit=3, speaker_roles=("user",))
    except Exception:
        return ""
    for turn in turns:
        prior = str(turn.get("raw_input") or turn.get("normalized_input") or "").strip()
        if prior and prior != value:
            return kind_of_ask(prior)
    return ""


def _drop_on() -> bool:
    try:
        from core.drop import enabled

        return bool(enabled())
    except Exception:
        return False


def _lend_on() -> bool:
    try:
        from core.lend.settings import enabled

        return _drop_on() and bool(enabled())
    except Exception:
        return False


def _drop_paired() -> bool | None:
    """Whether a connected Drop exists; None when it cannot be read (the turn is then left alone)."""
    try:
        from core.web.api.drop_api import _home

        with _home() as home:
            return any(row.get("status") == "connected" for row in home.list())
    except Exception:
        return None


def _lend_paired() -> bool | None:
    try:
        from core.lend.service import LendHome
        from core.web.api.drop_api import _home

        with _home() as drop, drop.scope(), LendHome(drop) as home:
            return any(value.get("state") not in {"revoked", "expired"} for value in home.all("invites"))
    except Exception:
        return None


def _fields(catalog) -> dict[str, str]:
    fields = {}
    for name, key, english in _PLACE_KEYS:
        text = catalog.text(key)
        fields[name] = english if text == key else text
    jobs = []
    for key, english in _JOB_KEYS:
        text = catalog.text(key)
        jobs.append(english if text == key else text)
    fields["jobs"] = "; ".join(jobs)
    return fields


def _fill(catalog, key: str, english: str, fields: dict[str, str]) -> str:
    # Plain chat words: the Settings labels go in as they are ("Backup & restore"), never escaped.
    text = catalog.text(key)
    text = english if text == key else text
    for name, value in fields.items():
        text = text.replace("{" + name + "}", value)
    return text


def reply_text(kind: str, state: str, locale: str = "") -> str:
    """The reply for ``kind`` ('drop' or 'lend') in ``state`` ('off' or 'unpaired')."""
    from core.i18n.catalog import catalog_for
    from core.i18n.turn_locale import turn_locale

    catalog = catalog_for(str(locale or "").strip() or turn_locale())
    fields = _fields(catalog)
    english = {
        ("drop", "off"): (DROP_OFF_KEY, DROP_OFF_EN), ("drop", "unpaired"): (DROP_UNPAIRED_KEY, DROP_UNPAIRED_EN),
        ("lend", "off"): (LEND_OFF_KEY, LEND_OFF_EN), ("lend", "unpaired"): (LEND_UNPAIRED_KEY, LEND_UNPAIRED_EN),
    }
    head = _fill(catalog, *english[(kind, state)], fields)
    pair = _fill(catalog, *((DROP_PAIR_KEY, DROP_PAIR_EN) if kind == "drop" else (LEND_PAIR_KEY, LEND_PAIR_EN)), fields)
    warning_key, warning_en = _WARNINGS[kind]
    warning = catalog.text(warning_key)
    warning = warning_en if warning == warning_key else warning
    return head + "\n\n" + pair + "\n\n" + warning


def state_for(kind: str) -> str:
    """'off', 'unpaired', or '' when the feature is on and paired (or its pairing cannot be read)."""
    if kind == "drop":
        if not _drop_on():
            return "off"
        return "unpaired" if _drop_paired() is False else ""
    if not _lend_on():
        return "off"
    return "unpaired" if _lend_paired() is False else ""


def reply_if_unavailable(agent: Any, text: str, *, session_id: str, source_context: dict[str, Any] | None):
    """A finished chat result for a Drop or lending ask that cannot run as things stand; None otherwise."""
    kind = kind_of_ask(text) or _follow_up_kind(text, session_id)
    if not kind:
        return None
    state = state_for(kind)
    if not state:
        return None
    locale = str((source_context or {}).get("ui_locale") or "")
    response = reply_text(kind, state, locale)
    result = agent._fast_path_result(session_id=session_id, user_input=text, response=response,
                                     confidence=1.0, source_context=source_context,
                                     reason=f"{kind}_{state}")
    result.update(deterministic=True, success=False, status="not_configured", task_outcome="failed")
    return result


__all__ = ["KEYS", "kind_of_ask", "reply_if_unavailable", "reply_text", "state_for"]
