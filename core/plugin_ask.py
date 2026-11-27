"""Whether a turn asks for what one of the person's added plugins does, in any language and any words.

Every lane that decides whether an added plugin's tools reach the model reads one decision: the plugin family in
`core.tool_demand_signals.resolve_demand_signals` (then `core.execution_requirements`, the chat-first lane, the tool
offer's family expansion, the conductor and the files loop all follow it). That decision came only from the words
of the plugin's own manifest, which its maker wrote once, usually in English. Measured on the 0.8 release candidate
(a7b18403, 2026-11-22, real local model, a parcel lookup plugin enabled): "is shipment PX-3065-D at the pickup point
yet" reached the plugin; "Kur dabar yra mano siunta PX-3065-B?" and "my box PX-3065-B never came, any idea where
it's sitting?" were never offered it, and the model invented a location. A plugin is often added for a person's own
language; a closed English vocabulary cannot reach it.

So at the start of the turn, when the words do not already settle it and an added plugin has tools, the turn's judge
(`core.intent_arbiter.arbitrate_plugin_ask`: the loaded local model, or the owner's model bound for the turn) is
asked one multiple-choice question over the plugins' own descriptions, with the previous message for a short
follow-up. Its answer is recorded here for this turn's text, and the plugin family reads it beside the words. It
only widens what the model is OFFERED: which tool runs, if any, is still the model's call and the owner's
permissions. No judge, a failed call or an unreadable answer records nothing: the turn is what it was before.

The same question carries VOOL's own personal tools (reminders, calendar, contacts, mail, notes, sending a file to the
phone) when the English word rules seat none of them on an owner-local turn. Measured on 261a0389 (outside
review, 2026-11-22): a reminder, a contact, the calendar, unread mail and a phone send asked in Lithuanian, German,
Spanish or French reached the model without the tool, and so did English wordings the rules did not know ("what's
coming up that you're supposed to nudge me about"). The words stay the fast path: when they already seat one of these
tools, no model is asked about them. One question for both, more than one tool may be picked, and a failed call is
asked once more before the turn goes on without a judgement.

Writing a file in the open folder, running its tests and looking something up on the web are among those choices
too. Blind audit of 58f60bbf3 (2026-11-22, confirmed on the app from source with qwen3:8b): "Sukurk naują failą
pirkiniai.txt ir įrašyk į jį ..." was answered "There is no file at `pirkiniai.txt`", because the read lane takes
every named file as a read unless an English edit verb stands beside it, and no tool for writing was ever offered; a
Lithuanian web ask got no web tool. A judged files or run choice now stands that read lane down, as the English edit
verbs do, and seats the writing or running tools; a judged web choice seats the web tools.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any

#: Judgements kept, by the turn text they were made for. The current turn's text is judged again at every turn,
#: so an entry is only ever read stale for an earlier message of a chat (a follow-up's lookback).
_KEPT = 256
_LIFETIME_S = 1800.0
_JUDGED: OrderedDict[str, tuple[float, tuple[str, ...], tuple[Any, ...]]] = OrderedDict()
#: The built-in tools the judge picked, by turn text: (stamp, choice keys).
_BUILTINS: OrderedDict[str, tuple[float, tuple[str, ...]]] = OrderedDict()
#: The person's own words in front of a pasted mail or message, by turn text: (stamp, own words, "" when none). Kept
#: because a later reader may only have the text with its line breaks folded, and the paste is read by its lines.
_PASTED_OWN: OrderedDict[str, tuple[float, str]] = OrderedDict()
#: The built-in choices the judge's first reading picked, by turn text, before any yes/no of their own dropped one.
_FIRST_PICKS: OrderedDict[str, tuple[float, tuple[str, ...]]] = OrderedDict()

#: VOOL's own personal tools as the judge reads them: (key, description, the tools a pick seats). The descriptions say
#: what each does for the person, so a judge reading any language can tell a reminder from a calendar event. Saying
#: in the Reminders and Contacts choices what they are not ("remember a fact ... is not a reminder") drew qwen3:8b to
#: them: English keeps fell from 6/6 to 2/6 and recalls went to Contacts (probe19, Mac run19). Keep them short.
BUILTIN_CHOICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("reminders", "Reminders: VOOL itself reminds the person of something at a time they give; set, list, move or "
     "cancel such a reminder.",
     ("operator.schedule_reminder", "operator.list_reminders", "operator.move_reminder", "operator.cancel_reminder")),
    ("calendar", "Calendar: what is on the person's calendar and when they are free, or put an event on it.",
     ("operator.show_agenda", "operator.check_availability", "operator.schedule_calendar_event")),
    ("contacts", "Contacts: the people the person saved, with their phone numbers, emails and addresses; find, save, "
     "change or delete one.",
     ("contacts.search", "contacts.save", "contacts.update", "contacts.delete")),
    ("mail", "Email: read or search the person's own email inbox.", ("email.read",)),
    ("notes", "Notes: save a note for the person, or find and read the notes they saved.",
     ("operator.save_note", "operator.find_notes", "operator.show_note")),
    ("phone", "Send to phone: send a file from this computer to the person's phone.", ("machine.send_to_phone",)),
    ("drop", "Drop: send a file from this computer to another person's VOOL or to another of the person's own "
     "computers. Not to a phone, and not by email or a chat app.",
     ("machine.send_with_drop",)),
    ("things", "My things: the household things the person saved in VOOL (purchases, appliances and devices, with "
     "maker, model and serial number); find one, match a label to it, or prepare a return, repair or warranty request.",
     ("things.search", "things.match", "things.prepare")),
    ("changes", "Changes in the open folder's version history (git): what was edited since the last commit, which files "
     "are not committed yet, and the exact differences.",
     ("workspace.git_status", "workspace.git_diff")),
    ("files", "Make or change a file in the open folder: create a new file, write or add words or lines to a file, or "
     "change its text. Not for reading or showing what a file already says.",
     ("workspace.write_file", "workspace.replace_in_file")),
    ("read", "Read a file in the open folder: show or tell what a file already says, or answer a question from it.",
     ("workspace.read_file",)),
    # A person asks for "my bill", "my report" or "my CV", rarely for a file by its name. Read as only a file by its
    # name, "surask man pernai metų sąskaitą už elektrą" and the gate's "gal gali surasti mano žemės ūkio ataskaitą
    # už 2024 metus?" were picked as the web, and the reading without the web picked nothing (iMac, qwen3:8b, judge
    # probe on 56d5fb4a0: 36 of 45 -> 42 of 45, nothing that was right moved). Over the full list the judge reads only
    # the first _PLUGIN_ASK_BRIEF_CHARS of a choice, so this one is kept inside that and is read whole.
    ("find", "Find something the person keeps on this computer, a file or a folder (a document, report, bill, photo, "
     "spreadsheet or download of theirs), by its name or what it is.",
     ("machine.find_file", "machine.find_folder")),
    ("run", "Run in the open folder: run the project's tests, its linter or its formatter, and say what they printed.",
     ("workspace.run_tests", "workspace.run_lint", "workspace.run_formatter")),
    ("web", "Search the internet: only when the message asks to look something up online or on a web page, or asks "
     "for today's news, weather, prices, opening hours or timetables. Not for general knowledge, definitions or "
     "explanations.",
     ("web.search", "web.fetch")),
)
#: The choices that ask for something done to or with the open folder's files, not for a file to be shown.
WORKSPACE_WORK_CHOICES = frozenset({"files", "run"})
#: The choices about files and folders on this computer (found, read, written, run or compared): what they hold is
#: not something the person told VOOL (core.persistent_memory._recall_beside_a_file_pick).
COMPUTER_FILE_CHOICES = frozenset({"changes", "files", "read", "find", "run"})
_BUILTIN_SEATS = {key: seats for key, _description, seats in BUILTIN_CHOICES}


def computer_file_intents() -> frozenset[str]:
    """The tools the computer-file choices seat: each reads or works on files of this computer, never the web."""
    return frozenset(intent for key in COMPUTER_FILE_CHOICES for intent in _BUILTIN_SEATS.get(key, ()))
#: Choices a judge reads one for the other: both are something of the person's at a time they name. A pick of either
#: seats both, so the model is offered the tools of the one meant. Mac gate on 561e3e9d0 (installed app, qwen3:8b,
#: 2026-11-23): "parodyk mano priminimus" ("show my reminders"), right after a reminder was set, was judged a calendar
#: ask; with no reminder tool offered, the model wrote a calendar event out of the reminder it had just set.
_SEATED_TOGETHER: dict[str, tuple[str, ...]] = {"reminders": ("calendar",), "calendar": ("reminders",)}
#: VOOL's memory of the person, put to the same question after the tools. It is no tool the model runs: the memory
#: lane answers these turns itself (core.persistent_memory), and a pick seats nothing. They are choices here because the
#: memory lane's own readers know only English shapes. Measured on 58f60bbf3 (blind audit, local qwen3:8b): with no
#: memory choice, "ušmiršk palapinės faktą ..." ("forget the tent fact") was judged a Contacts ask and raised a
#: contacts.delete approval for "žalia palapinė", and the forget order itself was then saved as a memory; "Įsimink du
#: dalykus: ..." ("remember two things: ...") saved nothing. The recall choice names what such a question is about, not
#: only "what I told you": on the Mac (ff6e9085c) "pasakyk kokia mano dviracio spalva" ("tell me my bike's colour") and
#: "where do i keep my toolbox again?" were judged no memory ask, and a model answered them after the fact was forgotten.
#: Said that broadly, "nebeatsimink nieko apie mano palapinę, ištrink" ("don't remember anything about my tent, erase
#: it") was then judged a recall (Mac, d585608b6): each memory choice says what it is not.
MEMORY_CHOICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("memory_keep", "Memory, keep: the person asks VOOL to remember something about themselves, their life, "
     "people or things, for later. Putting something in their notes is Notes, not this.", ()),
    ("memory_recall", "Memory, recall: the person wants to be told a fact of their own life, people or things that only "
     "they could have told VOOL (the colour or place of something of theirs, a name, a date), or what they told it "
     "before. Asking VOOL to forget or erase it is not a recall, and the reminders, calendar, contacts or notes they "
     "have are those tools.", ()),
    ("memory_forget", "Memory, forget: the person asks VOOL to forget, erase, delete or no longer remember something "
     "they told it before. Asking VOOL not to keep what the message itself says is not a forget.", ()),
)
#: What the memory lane does with each memory choice, in the order a turn that picked several is read.
_MEMORY_KINDS = {"memory_forget": "forget", "memory_keep": "keep", "memory_recall": "recall"}
#: The owner's live sources (core.tool_demand_signals.owner_live_sources) that one of the choices above reads.
_LIVE_SOURCES_A_CHOICE_READS = frozenset({"notes", "calendar", "mail"})
_LOCK = threading.Lock()
_log = logging.getLogger(__name__)


def _key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


#: One judge choice per switched-on package, kept by what it was built from: (the package's folder, the approval the
#: person gave, the manifest file's change time and size) -> (the choice or None, when it was read).
_CHOICES: dict[str, tuple[tuple[Any, ...], tuple[str, str] | None, float]] = {}
#: A package the full check turned down is asked about again after this long, in case what stopped it has passed.
_RECHECK_S = 60.0
#: An author's description is cut to this before it becomes a choice (the question itself cuts each choice shorter).
_CHOICE_CHARS = 400


def switched_on_before(record) -> bool:
    """Whether the person ever switched this installed package on: an "enabled" entry since it was last removed.

    Read from the lifecycle record; nothing is hashed. An update keeps the answer (the person chose this plugin and
    is owed the reason it is off now); removing it and adding it again starts over. The record keeps its last
    entries only, so when neither entry is among them the package has a long history and the time it was last
    enabled is the answer."""
    from core import plugin_lifecycle

    if record is None or record.stage == plugin_lifecycle.STAGE_UNINSTALLED:
        return False
    for row in reversed(list(record.evidence or [])):
        event = str(row.get("event") or "") if isinstance(row, dict) else ""
        if event == "enabled":
            return True
        if event == "uninstalled":
            return False
    return bool(record.enabled_at)


def _installed_with_tools() -> list[tuple[str, str]]:
    """(plugin id, the choice the judge reads) for every package the person switched on that brings tools.

    A package that can run right now: installed, reviewed, consented to and enabled, with its files still the
    ones that were reviewed (`plugin_lifecycle.is_available`, the fact that decides whether its tools run). And a
    package the person switched on once and then turned off themselves, its files still the reviewed ones: they
    chose it, and when they ask for it in their own words VOOL must be able to say it is off instead of answering
    without it. A package nobody ever switched on is never listed. Before, every installed package was, so one
    nobody had agreed to put its maker's words into the judge's question on every turn.

    The choice is built here, as data: the plugin's name as VOOL would write it, then its maker's descriptions with
    everything taken out that could end the quote the question puts them in (`core.plugin_identity`).

    The full check hashes the package's files, so it is made once for a package as approved and as its manifest file
    now stands, not on every turn: a turn reads the lifecycle record and the file's change time. A package that is
    switched off, or whose record or manifest file changed, is read again.
    """
    from pathlib import Path

    from core import plugin_lifecycle
    from core.plugin_identity import prompt_data, shown_name
    from core.plugin_tools import load_manifest

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in plugin_lifecycle.records():
        # What the record alone says: cheap, and enough to leave out everything the person never switched on.
        on = (record.stage == plugin_lifecycle.STAGE_ENABLED and record.enabled is True and bool(record.approval_digest)
              and record.consented_approval == record.approval_digest)
        turned_off = (not on and record.stage == plugin_lifecycle.STAGE_VERIFIED and bool(record.verified_digest)
                      and switched_on_before(record))
        if not record.root or record.revoked_at or not (on or turned_off):
            continue
        manifest = Path(record.root) / ".codex-plugin" / "plugin.json"
        try:
            stat = manifest.stat()
        except OSError:
            continue
        seen.add(record.plugin_id)
        key = (record.root, record.approval_digest, on, stat.st_mtime_ns, stat.st_size)
        with _LOCK:
            kept = _CHOICES.get(record.plugin_id)
        if kept is not None and kept[0] == key and (kept[1] is not None or time.monotonic() - kept[2] < _RECHECK_S):
            choice = kept[1]
        else:
            choice = None
            try:
                if plugin_lifecycle.is_available(record.plugin_id) if on else _still_as_reviewed(record):
                    block = load_manifest(manifest).vool
                    tools = [tool for tool in (block or {}).get("tools") or () if isinstance(tool, dict)]
                    if block and tools:
                        said = " ".join(str(tool.get("description") or "") for tool in tools)
                        name = shown_name(block.get("name"), record.plugin_id) or "A plugin"
                        choice = (record.plugin_id,
                                  f"{name}: {prompt_data(str(block.get('description') or '') + ' ' + said, _CHOICE_CHARS)}")
            except Exception:
                choice = None   # unreadable: it is not put to the judge
            with _LOCK:
                _CHOICES[record.plugin_id] = (key, choice, time.monotonic())
        if choice is not None:
            rows.append(choice)
    with _LOCK:
        for plugin_id in [plugin_id for plugin_id in _CHOICES if plugin_id not in seen]:
            del _CHOICES[plugin_id]
    return rows


def _still_as_reviewed(record) -> bool:
    """Whether a package the person turned off still holds the files that were reviewed, and was not taken back."""
    from pathlib import Path

    from core import plugin_lifecycle

    if str(record.source).startswith("registry:"):
        from core.registry.native import runtime_allowed

        if not runtime_allowed(record):
            return False
    return plugin_lifecycle.manifest_digest(Path(record.root)) == record.verified_digest


def _installed_ids() -> set[str]:
    """The ids of every installed package, switched on or not, read from the lifecycle records alone. Only for
    telling that a message names a tool by its exact id; no word a maker wrote is read here."""
    try:
        from core import plugin_lifecycle

        return {record.plugin_id for record in plugin_lifecycle.records()
                if record.stage != plugin_lifecycle.STAGE_UNINSTALLED and record.root}
    except Exception:
        return set()


def _signature(rows: list[tuple[str, str]]) -> tuple[Any, ...]:
    return tuple(rows)


def judgement(text: str) -> tuple[str, ...] | None:
    """What the turn's judge said this text asks for: the plugins (empty when it asks for none of them), or None when
    nothing judged it (no judge, a failed call, an unreadable answer, a judgement too old, or plugins changed since)."""
    key = _key(text)
    if not key:
        return None
    with _LOCK:
        entry = _JUDGED.get(key)
    if entry is None:
        return None
    stamped, plugins, signature = entry
    if time.monotonic() - stamped > _LIFETIME_S:
        return None
    try:
        if _signature(_installed_with_tools()) != signature:
            return None
    except Exception:
        return None
    return plugins


def judged_plugins(text: str) -> tuple[str, ...]:
    return judgement(text) or ()


def _record(text: str, plugins: tuple[str, ...], signature: tuple[Any, ...]) -> None:
    key = _key(text)
    if not key:
        return
    with _LOCK:
        _JUDGED[key] = (time.monotonic(), plugins, signature)
        _JUDGED.move_to_end(key)
        while len(_JUDGED) > _KEPT:
            _JUDGED.popitem(last=False)


def _drop(text: str) -> None:
    with _LOCK:
        _JUDGED.pop(_key(text), None)
        _BUILTINS.pop(_key(text), None)


def forget() -> None:
    with _LOCK:
        _JUDGED.clear()
        _BUILTINS.clear()
        _PASTED_OWN.clear()
        _FIRST_PICKS.clear()
        _CHOICES.clear()


def _record_builtins(text: str, keys: tuple[str, ...]) -> None:
    key = _key(text)
    if not key:
        return
    with _LOCK:
        _BUILTINS[key] = (time.monotonic(), keys)
        _BUILTINS.move_to_end(key)
        while len(_BUILTINS) > _KEPT:
            _BUILTINS.popitem(last=False)


#: A phone number as people type one (seven digits or more, with the marks people put between them) or an email.
_CONTACT_VALUE_RE = re.compile(r"(?:\+?\d[\d ()./-]{5,}\d)|(?:[^\s@]+@[^\s@]+\.[^\s@]{2,})")


def _continues_a_contact_save(text: str, source_context: dict[str, Any]) -> bool:
    """Whether a message both readings call a keep is the contact save just before it, continued: the message
    right before it in this chat was judged a Contacts order, and this one carries what a contact holds (a phone
    number or an email). Such a message is the next contact, not a fact about the person's life.

    Measured on 02ff11496 (fresh audit): right after a contact was saved, "save another one too, Odette Marsh, +1 555
    0177" was kept as a memory line; no contact was made, "which Odette is my physio" was answered from that line, and
    "take Odette Marsh out of my contacts" offered to delete the other Odette."""
    previous = _previous_request(text, source_context if isinstance(source_context, dict) else {})
    if not previous or "contacts" not in judged_builtins(previous):
        return False
    return bool(_CONTACT_VALUE_RE.search(" ".join(str(text or "").split())))


def _memory_kind_confirmed(
    text: str, keys: tuple[str, ...], source_context: dict[str, Any], session_id: str,
    *, first_read_as_the_web: bool = False, dropped_a_pick: bool = False,
) -> tuple[str, ...]:
    """The tool judgement's picks with VOOL's memory decided by two readings: the tool judgement's own memory pick and
    the memory's own question (`core.intent_arbiter.arbitrate_memory_kind`), asked beside every tool judgement.

    Either reading alone was wrong too often on qwen3:8b with Lithuanian slips. Measured: the tool judgement read three
    of six Lithuanian recalls as Contacts or My things (probe of 7682b4155); the four-way question read two recalls the
    tool judgement had right as none, a forget as a keep and a poem request as a keep (probe of bef26afe4). So:
    - a recall only reads: either reading names it, and it is added beside any tool picked (the memory lane answers it
      when it names a held fact, and hands the turn on when it names none);
    - a keep writes: it stands only when both readings name it. With one reading and the keep step's own told-as-fact
      question to settle it, three Lithuanian forgets typed without accents ("pamirsk ka pasakojau apie savo valti")
      were kept as facts (probe of 32f3c9e38). A fact told without a judged keep is still the keep step's own to save;
    - a forget cannot be undone: it stands when both readings name it, or when one does and a yes/no forget question
      (`arbitrate_forget_order`) reads yes; it then still names its targets by a judgement of its own and erases only
      what shares the message's words;
    - two readings that name different memory kinds get the same yes/no forget question;
    - a keep or a forget never takes a tool's order ("remind me at 9 to ..."): neither stands beside a picked tool."""
    from core import intent_arbiter

    def asked(name: str, call, **extra) -> str | None:
        try:
            return call(text, chat_id=str(session_id or ""), source_context=source_context, **extra)
        except Exception as exc:
            _log.info("memory %s: judge call failed (%s)", name, type(exc).__name__)
            return None

    # Beside a picked tool, a recall stands only if this question names it: it waits as long as the tool ask did.
    kind = asked("kind", intent_arbiter.arbitrate_memory_kind,
                 **({"patient": True} if any(key not in _MEMORY_KINDS for key in keys) else {}))
    picked = {_MEMORY_KINDS[key] for key in keys if key in _MEMORY_KINDS}
    tools = tuple(key for key in keys if key not in _MEMORY_KINDS)
    told: list[str | None] = []

    def unconfirmed(verdict: str | None) -> bool:
        """A Reminders or Calendar pick its own question did not confirm. "no" always; no answer only on a message
        that tells a fact, where the pick would stop the fact's keep and hand the model a write tool (memory wiring
        audit of efb8e474c, B2: "she lands at 14:40 on the 9th" got a Manual-mode reminder card and nothing kept)."""
        if verdict == "no":
            return True
        if verdict is not None:
            return False
        if not told:
            told.append(asked("told as fact", intent_arbiter.arbitrate_told_as_fact))
        return told[0] == "yes"

    if "reminders" in tools:
        # A Reminders pick is confirmed by a yes/no of its own. On the gate iMac (3c92e8df3, qwen3:8b) "zapamiętaj: mój
        # szwagier ma psa o imieniu Burek" and a plainly told fact were picked Reminders beside a keep reading, and the
        # model wrote a reminder out of the fact with an approval card; on cf0d43952 "..., don't save that part" was
        # picked Reminders with no memory reading at all, and was answered as a failed reminder cancel. No answer
        # leaves the pick as it was.
        reminder_verdict = asked("reminder", intent_arbiter.arbitrate_reminder_ask)
        _log.info("tool ask: reminders pick, its own question read %s", reminder_verdict or "nothing")
        if unconfirmed(reminder_verdict):
            _log.info("tool ask: reminders pick not confirmed")
            tools = tuple(key for key in tools if key != "reminders")
            keys = tools + tuple(key for key in keys if key in _MEMORY_KINDS)
            if kind == "keep" or (not tools and kind not in {"recall", "forget"}):
                # The Reminders pick was the tool judgement's reading of "remember", or of a fact that names a time,
                # placed on the wrong choice: with it dropped, it is a keep reading (beside the memory question's own,
                # both readings name it; alone, the told-as-fact and forget questions below settle it).
                picked.add("keep")
    if "calendar" in tools:
        # A Calendar pick is confirmed by a three-way question of its own, like Reminders. iMac gate53 on efb8e474c
        # (qwen3.5:9b): "heads up, the vet moved <pet>'s checkup to thursday 15:20" was picked Calendar with no memory
        # reading, the model wrote a reminder nobody asked for and nothing was kept; on a6a5f29b7 such lines got the
        # Manual-mode approval card. No answer leaves the pick as it was.
        calendar_verdict = asked("calendar", intent_arbiter.arbitrate_calendar_ask)
        _log.info("tool ask: calendar pick, its own question read %s", calendar_verdict or "nothing")
        if unconfirmed(calendar_verdict):
            _log.info("tool ask: calendar pick not confirmed")
            tools = tuple(key for key in tools if key != "calendar")
            keys = tools + tuple(key for key in keys if key in _MEMORY_KINDS)
            if not tools:
                # The pick was the tool judgement's reading of news about an appointment: it stands as one keep
                # reading, which the told-as-fact and forget questions below settle.
                picked.add("keep")
    if dropped_a_pick and not tools and kind not in {"recall", "forget"}:
        # A tool pick its own question turned down was the tool judgement's misreading: of a message that tells a fact
        # it is one keep reading, which the told-as-fact and forget questions below settle (memory wiring audit B2).
        picked.add("keep")
    named = picked | ({kind} if kind in {"keep", "recall", "forget"} else set())
    decided = ""
    # Two readings that each name a different kind of memory ask are unsure of it: a forget typed without accents
    # or with a slip read as a recall by one and a keep by the other ("istrink, ka tau sakiau apie ...": tool
    # judgement recall, memory question keep, Mac run14 and run16). The yes/no forget question settles it as it
    # settles one reading of a forget. Two readings that agree are not asked it.
    disagree = bool(picked) and kind in {"keep", "recall", "forget"} and kind not in picked
    if not tools and ("forget" in named or disagree):
        agreed = kind == "forget" and "forget" in picked
        # A forget only one reading names is not one when the message itself tells a fact: "mano suo vardu
        # Wirbelwind, bet to neisimink" ("my dog is called ..., don't remember that") was read forget by the memory
        # question and yes by the forget question, and erased two unrelated facts (Mac run20, 786af505). The told-as-
        # fact question reads an order (forget, erase, delete) as no. The keep-wanted question, tried first, read
        # English forget orders as "don't keep" (probe18); the lasting-fact one read forget orders about a thing as a
        # fact (probe19).
        # A recall reading beside a forget reading says both read the message as about something told before; a message
        # that tells something new is read keep or none, never recall. With a recall reading the told-as-fact question
        # is not asked: it read plain forget orders ("ištrink iš atminties viską apie ...") as telling a fact and they
        # were answered as recalls (Mac run21, probe21, 91e89867).
        about_told = {"recall", "forget"} <= named
        if agreed or (asked("forget", intent_arbiter.arbitrate_forget_order) == "yes"
                      and (about_told or asked("told as fact", intent_arbiter.arbitrate_told_as_fact) != "yes")):
            decided = "forget"
    if (not decided and not tools and named & {"forget", "recall"} and _has_pending_reminders()
            and not (first_read_as_the_web and "forget" not in named)):
        # A recall reading of a message the judgement over the whole list (Reminders among its choices) read as the
        # web is not put to the reminder question: the message was first read as about the world, and the reminder
        # question reads a follow-up that points back at a world question as a reminder ask ("and in which city is
        # it?" after "who painted The Last Supper?": 4 of 8 such follow-ups read "remind", in four languages, with
        # and without the earlier message beside it; iMac probes g39 and g39b on f31c60b65, qwen3:8b). On iMac
        # gate39 that turn became a calendar approval card in an Offline chat. The recall goes to the memory lane,
        # which hands the turn on when nothing held answers it.
        # A word for reminder is also a word for memory in some languages ("Erinnerung"): "lösche meine Erinnerung"
        # ("delete my reminder"), with a reminder pending, was judged a memory forget and answered "I have nothing
        # saved about that", and the reminder stayed (iMac gate on 95fae99a0). With a reminder there to act on, a
        # forget or recall reading is asked whether the message is about a reminder.
        if asked("reminder", intent_arbiter.arbitrate_reminder_ask) == "yes":
            _log.info("memory kind judged: %s read as a reminder order", ",".join(sorted(named)))
            return ("reminders",)
    one_keep_reading = (kind == "keep" and not picked) or (picked == {"keep"} and kind not in {"keep", "recall", "forget"})
    if not decided and not tools and one_keep_reading:
        # One reading alone named a keep and the other named no memory ask. Either way round the same second reading
        # decides. Memory question keep, tool judgement nothing: "zapamiętaj proszę: moja
        # sąsiadka Ewa ma kota o imieniu Mruczek" was not kept and the model's "I'll remember that" was corrected to
        # "I did not write anything to memory" (iMac gate on 95fae99a0). One reading alone kept three accentless
        # Lithuanian forgets as facts (probe of 32f3c9e38), so a second reading decides: the message tells a fact and
        # is no forget order. Tool judgement keep, memory question none: "fyi my neighbour Rasa is training for a half
        # marathon in March" (iMac gate52 on a6a5f29b7, qwen3.5:9b) was answered as chat, nothing kept, and a new chat
        # said "I don't have that saved".
        if (asked("told as fact", intent_arbiter.arbitrate_told_as_fact) == "yes"
                and asked("forget", intent_arbiter.arbitrate_forget_order) != "yes"):
            picked.add("keep")
            kind = "keep"
        else:
            picked.discard("keep")
    if not decided and not tools and kind == "keep" and "keep" in picked:
        if _continues_a_contact_save(text, source_context):
            _log.info("memory kind judged: keep read as the contact save before it continued")
            return ("contacts",)
        decided = "keep"
    if not decided and "recall" in named and _judged_a_clock_ask(text):
        # The turn's own clock question read the message as asking the time or date now: the clock answers it, not
        # anything the person told VOOL. iMac gate28 on d2262de22: "Mis kell on praegu Varssavis?" was judged a recall
        # beside it, and the memory lane answered "I don't have that saved".
        _log.info("memory kind judged: recall read as a clock ask")
        named.discard("recall")
    if not decided and "recall" in named:
        decided = "recall"
    _log.info("memory kind judged: %s (tool judgement picked %s, memory question %s)", decided or "none",
              ",".join(keys) or "none", kind)
    return tools + ((f"memory_{decided}",) if decided else ())


def _judged_a_clock_ask(text: str) -> bool:
    try:
        from core.online_asks import judged_a_clock_ask

        return judged_a_clock_ask(text)
    except Exception:
        return False


def _has_pending_reminders() -> bool:
    """Whether the owner has any reminder still to come, in any chat. A store fault reads as none."""
    try:
        from core.operator.reminders import list_owner_reminders
        from storage.db import get_connection

        return bool(list_owner_reminders(get_connection_fn=get_connection))
    except Exception:
        return False


def judged_builtins(text: str) -> tuple[str, ...]:
    """The built-in personal tools (BUILTIN_CHOICES keys) the turn's judge said this text asks for; () when it said
    none, or nothing judged it. Read only; nothing here asks a model."""
    key = _key(text)
    if not key:
        return ()
    with _LOCK:
        entry = _BUILTINS.get(key)
    if entry is None or time.monotonic() - entry[0] > _LIFETIME_S:
        return ()
    return entry[1]


def judged_builtins_of_message_holding(part: str) -> tuple[str, ...]:
    """The built-in choices judged for the most recent judged message that holds `part` as a piece of its words (a
    clause the request reader cut out of it); () when none does. Read only.

    The judge reads the whole message; the lanes' own readers are asked about its clauses one by one
    (core.agent_runtime.demand_ownership). A clause of "Erstelle packliste.txt mit Zelt, Schlafsack und Lampe" was never
    judged on its own, so the read lane claimed "Erstelle packliste.txt mit Zelt" by its file name while the message
    was judged a write, and the rest of the list ran as a second request (N-012)."""
    key = _key(part)
    if not key:
        return ()
    now = time.monotonic()
    with _LOCK:
        entries = list(_BUILTINS.items())
    for message, (at, picked) in reversed(entries):
        if now - at <= _LIFETIME_S and key in message:
            return picked
    return ()


def _record_paste(text: str, own: str) -> None:
    key = _key(text)
    if not key:
        return
    with _LOCK:
        _PASTED_OWN[key] = (time.monotonic(), " ".join(str(own or "").split()))
        _PASTED_OWN.move_to_end(key)
        while len(_PASTED_OWN) > _KEPT:
            _PASTED_OWN.popitem(last=False)


def _record_first_picks(text: str, keys: tuple[str, ...]) -> None:
    key = _key(text)
    if not key:
        return
    with _LOCK:
        _FIRST_PICKS[key] = (time.monotonic(), tuple(keys))
        _FIRST_PICKS.move_to_end(key)
        while len(_FIRST_PICKS) > _KEPT:
            _FIRST_PICKS.popitem(last=False)


def first_picks(text: str) -> tuple[str, ...]:
    """The built-in choices the judge's first reading of this text picked, kept even when a yes/no of their own then
    dropped them; () when it picked none or nothing judged it. Read only."""
    key = _key(text)
    if not key:
        return ()
    with _LOCK:
        entry = _FIRST_PICKS.get(key)
    if entry is None or time.monotonic() - entry[0] > _LIFETIME_S:
        return ()
    return entry[1]


def own_words_over_a_paste(text: str) -> str | None:
    """The person's own words in front of a pasted mail or message this text holds ("" when it is only the paste), or
    None when it holds none. Read from the text's lines, or, for text whose line breaks were folded, from what the
    turn's judge read. Read only."""
    from core.memory.admission import holds_a_pasted_message, text_before_a_pasted_message

    if holds_a_pasted_message(str(text or "")):
        return " ".join(text_before_a_pasted_message(str(text or "")).split())
    key = _key(text)
    if not key:
        return None
    with _LOCK:
        entry = _PASTED_OWN.get(key)
    if entry is None or time.monotonic() - entry[0] > _LIFETIME_S:
        return None
    return entry[1]


def judged_at_all(text: str) -> bool:
    """Whether the turn's judge read this text (its answer may be none). Read only."""
    key = _key(text)
    if not key:
        return False
    with _LOCK:
        entry = _BUILTINS.get(key)
    return entry is not None and time.monotonic() - entry[0] <= _LIFETIME_S


def judged_memory_ask(text: str) -> str:
    """What the turn's judge read this text as asking of VOOL's memory: "forget", "keep" or "recall" (in that order
    when it picked several), or "" when it picked none or nothing judged it. Read only."""
    picked = set(judged_builtins(text))
    return next((kind for key, kind in _MEMORY_KINDS.items() if key in picked), "")


def judged_tool_ask(text: str) -> bool:
    """Whether the turn's judge read this text as asking for one of VOOL's own tools (a reminder, a contact, mail ...):
    an order to do something, not something said about the person's life. Read only."""
    return any(key in _BUILTIN_SEATS for key in judged_builtins(text))


def judged_builtin_seats(text: str) -> tuple[str, ...]:
    """The tools the judged built-in choices seat, in choice order, deduplicated."""
    seats: list[str] = []
    picked = judged_builtins(text)
    keys = [*picked, *(other for key in picked for other in _SEATED_TOGETHER.get(key, ()) if other not in picked)]
    for key in keys:
        for intent in _BUILTIN_SEATS.get(key, ()):
            if intent not in seats:
                seats.append(intent)
    return tuple(seats)


def judged_not_asked(text: str) -> frozenset[str]:
    """The tools of the built-in choices the turn's judge read this text against and did not pick (nor seated beside a
    pick); empty when nothing judged it. A tool no choice names is not in it: the judge was never asked about that
    one. Read only."""
    if not judged_at_all(text):
        return frozenset()
    shown = {intent for _key_, _description, seats in BUILTIN_CHOICES for intent in seats}
    return frozenset(shown - set(judged_builtin_seats(text)))


def _previous_request(text: str, source_context: dict[str, Any]) -> str:
    current = _key(text)
    earlier = [
        " ".join(str(message.get("content") or "").split())
        for message in list(source_context.get("conversation_history") or [])
        if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "user"
    ]
    while earlier and _key(earlier[-1]) == current:
        earlier.pop()   # the turn being read is usually the last message of its own history
    return earlier[-1] if earlier else ""


def judge_turn(text: str, *, also: str = "", source_context: Any, session_id: str = "") -> tuple[str, ...]:
    """Judge this turn's text once, before any lane reads the plugin family; `also` is the same turn's other
    spelling (the normalized text the lanes read), recorded with the same judgement. Never raises."""
    try:
        _drop(text)
        if str(also or "").strip():
            _drop(also)
        plugins = _judge_turn(str(text or ""), source_context if isinstance(source_context, dict) else {}, session_id)
        if str(also or "").strip() and _key(also) != _key(text):
            if plugins is not None:
                _record(also, plugins, _signature(_installed_with_tools()))
            with _LOCK:
                builtins = _BUILTINS.get(_key(text))
            if builtins is not None:
                _record_builtins(also, builtins[1])
        return plugins or ()
    except Exception as exc:
        _log.info("plugin ask: not judged (%s)", type(exc).__name__)
        return ()


def _plugins_named_by_tool_id(text: str, installed: set[str]) -> tuple[str, ...]:
    """The added plugins whose tools the text names by their exact ids (the demand seam's own reading)."""
    from core.plugin_tools import plugin_id_for_intent
    from core.tool_demand_signals import _plugin_intents

    named = (plugin_id_for_intent(intent) for intent in _plugin_intents(text))
    return tuple(dict.fromkeys(plugin_id for plugin_id in named if plugin_id and plugin_id in installed))


def _builtins_to_ask(text: str, source_context: dict[str, Any]) -> bool:
    """Whether the built-in personal tools go into the question: an owner-local turn whose words seat none of them.
    Read while this text's judgement is dropped, so the readers below see the words alone. A missing context is not
    owner-local, as for the lane release (core.execution_requirements._operator_action_demand)."""
    if not isinstance(source_context, dict) or not source_context:
        return False
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(source_context):
            return False
        # A memory command (remember, forget, the memory list) is the memory lane's own turn, answered before any
        # tool is offered: asking a model about tools for it only costs the turn a call (and a forget turn on a
        # machine whose model is down then waited on it).
        from core.persistent_memory import memory_command_owns_turn

        if memory_command_owns_turn(text):
            return False
        from core.tool_demand_signals import owner_live_sources, owner_local_operator_intents, resolve_demand_signals

        # A live source the words seat counts only when a choice below reads it (notes, calendar, mail): "files",
        # "disk" and "processes" are this computer's own reads, and an ask about them may still be one of these
        # tools ("what have I changed in this folder since the last commit" seats "files" and asks for git).
        if owner_local_operator_intents(text) or set(owner_live_sources(text)) & _LIVE_SOURCES_A_CHOICE_READS:
            return False
        signals = resolve_demand_signals(text)
    except Exception:
        return False
    seated = set(signals.explicit_intents)
    return not any(seated & set(seats) for seats in _BUILTIN_SEATS.values()) and not (
        {"contacts", "email"} & set(signals.required_families)
    )


def _web_confirmed(text: str, keys: tuple[str, ...], source_context: dict[str, Any], session_id: str) -> tuple[str, ...]:
    """The picked choices, with the web kept only when a yes/no of its own says the message needs the internet.

    Over the list, the judge picked the web for most plain questions in other languages ("Kas yra fotosintezė?", "Was
    ist ein schwarzes Loch?", 6 of 10 on the app from source with qwen3:8b, 2026-11-22), and with Web lookups off a
    web seat refuses the question instead of answering it. The yes/no (core.intent_arbiter.arbitrate_web_ask) read 17
    of 18 fresh asks right. No answer keeps the web out: the turn is what it was before the web was a choice."""
    if "web" not in keys:
        return keys
    if any(key in _MEMORY_KINDS for key in keys):
        # The same judgement read the message as about the person's own life (a memory choice): what only they could
        # have told VOOL is on no web page. Memory run17 (Mac, qwen3:8b, 2026-11-23): "prmiink, kokios spalvos mano
        # dviratis" ("remind me what colour my bike is") was picked web + memory, its web yes/no said yes, and the
        # reply was "Web lookups are off".
        _log.info("tool ask: web pick dropped beside a memory pick")
        return tuple(key for key in keys if key != "web")
    from core.online_asks import web_ask_verdict

    verdict = web_ask_verdict(text, source_context=source_context, session_id=str(session_id or ""))
    if verdict == "yes":
        return keys
    _log.info("tool ask: web pick not confirmed (%s)", verdict)
    return tuple(key for key in keys if key != "web")


#: Picks that act on this computer or on the person's saved records: each stands only on a yes/no of its own
#: (`_actions_confirmed`).
_ACTION_CHOICES_CONFIRMED = frozenset({"run", "files"})
#: Picks that no question of their own confirms (Reminders, Calendar, Find and the web have theirs). On a message the
#: told-as-fact question reads as telling a fact, each is put to the shared yes/no too: a pick stops the keep of the
#: fact, so an unchecked misreading kept nothing and handed the model a tool (memory wiring audit of efb8e474c, B2).
#: Only then, because the shared yes/no reads asks about the person's own things in Lithuanian as no (see Find below).
_CHOICES_CONFIRMED_BESIDE_A_TOLD_FACT = frozenset({"notes", "mail", "phone", "drop", "things", "changes", "read"})


def _actions_confirmed(
    text: str, keys: tuple[str, ...], source_context: dict[str, Any], session_id: str,
) -> tuple[str, ...]:
    """The picks, with a pick that runs something or writes a file kept only when a yes/no of its own says the message
    asks for it. iMac gate on cf0d43952 (qwen3:8b): "what is the name of my dentist's cat?" was picked Run, and the
    reply was a listing of the open folder. No answer keeps the pick.

    Contacts too, unless the message carries what a contact holds (a phone number or an email): on the iMac gate of
    f0a19600e "..., my squash partner ... owns a gecko named ..., keep that bit to yourself" was picked Contacts, the
    fact was written into a contact's notes behind two approval cards and the message was kept in memory; a recall
    about the person's own life ran a contact search."""
    from core import intent_arbiter

    told: list[str | None] = []

    def tells_a_fact() -> bool:
        if not told:
            try:
                told.append(intent_arbiter.arbitrate_told_as_fact(
                    text, chat_id=str(session_id or ""), source_context=source_context))
            except Exception as exc:
                _log.info("tool ask: told-as-fact not read (%s)", type(exc).__name__)
                told.append(None)
        return told[0] == "yes"

    kept = []
    for key in keys:
        if key == "contacts" and _CONTACT_VALUE_RE.search(" ".join(str(text or "").split())):
            kept.append(key)
            continue
        if key == "contacts":
            # Without a contact value in it, a Contacts pick is asked a question of its own: the shared yes/no read a
            # recall about the person's own number as a contacts ask, and the turn went to the model with the contact
            # tools instead of the memory lane (Intel app smoke of efb8e474c). No answer keeps the pick.
            try:
                verdict = intent_arbiter.arbitrate_contact_ask(text, chat_id=str(session_id or ""),
                                                               source_context=source_context)
            except Exception as exc:
                _log.info("tool ask: contacts pick not confirmed (%s)", type(exc).__name__)
                verdict = None
            if verdict == "no" or (verdict is None and tells_a_fact()):
                _log.info("tool ask: contacts pick dropped by its own question (%s)", verdict or "no answer")
                continue
            kept.append(key)
            continue
        if key == "find":
            # A Find pick is asked a yes/no of its own. Unasked, "per kokias šalis ji teka?" after a question about the
            # Volga was picked Find and answered "Local directory ~/Desktop does not exist" (iMac gate32 on 71cadd43f).
            # The shared yes/no reads a question about the person's own things as no, and dropped real Lithuanian finds;
            # this one kept 18 of 18 finds in eight languages and dropped 10 of 15 world questions (iMac probe g32,
            # qwen3:8b). No answer keeps the pick.
            try:
                verdict = intent_arbiter.arbitrate_find_ask(
                    text, previous=_previous_request(text, source_context), chat_id=str(session_id or ""),
                    source_context=source_context)
            except Exception as exc:
                _log.info("tool ask: find pick not confirmed (%s)", type(exc).__name__)
                verdict = None
            if verdict == "no":
                _log.info("tool ask: find pick dropped by its own yes/no")
                continue
            kept.append(key)
            continue
        beside_a_fact = key in _CHOICES_CONFIRMED_BESIDE_A_TOLD_FACT and tells_a_fact()
        if key in _ACTION_CHOICES_CONFIRMED or beside_a_fact:
            description = next((row[1] for row in BUILTIN_CHOICES if row[0] == key), "")
            try:
                verdict = intent_arbiter.arbitrate_choice_ask(
                    text, description, chat_id=str(session_id or ""), source_context=source_context)
            except Exception as exc:
                _log.info("tool ask: %s pick not confirmed (%s)", key, type(exc).__name__)
                verdict = None
            if verdict == "no" or (beside_a_fact and verdict != "yes"):
                # Beside a told fact only a yes keeps the pick: unconfirmed, it would stop the fact's keep.
                _log.info("tool ask: %s pick dropped by its own yes/no (%s)", key, verdict or "no answer")
                continue
        kept.append(key)
    return tuple(kept)


def _judged_again_without_the_web(
    text: str, builtins: list[tuple[str, str, tuple[str, ...]]], source_context: dict[str, Any], session_id: str
) -> tuple[str, ...]:
    """The built-in choices a second reading picks once the internet is out of the list, after the judge's only pick
    was the internet and the web's own yes/no said the message needs none.

    That pick was a misreading, and with nothing left the turn had no tool of the person's at all. Fresh audit of
    02ff11496 (2026-11-23, qwen3:8b): "kokius priminimus turiu?" ("which reminders do I have?") was judged the
    internet, the web yes/no said no, no reminder tool was offered, and the model told the person to use
    "operator.list_reminders" itself. Read again against the person's own tools, the message can find the one it
    means; a plain question picks none, as before. No answer keeps it at none."""
    rest = [row for row in builtins if row[0] != "web"]
    if not rest:
        return ()
    from core import intent_arbiter

    try:
        picked = intent_arbiter.arbitrate_plugin_ask(
            text,
            [row[1] for row in rest],
            previous=_previous_request(text, source_context),
            chat_id=str(session_id or ""),
            source_context=source_context,
        )
    except Exception as exc:
        _log.info("tool ask: second reading without the web failed (%s)", type(exc).__name__)
        return ()
    if isinstance(picked, int) and not isinstance(picked, bool):
        picked = (picked,) if picked else ()
    keys = tuple(dict.fromkeys(rest[n - 1][0] for n in (picked or ()) if 1 <= n <= len(rest)))
    _log.info("tool ask: read again without the web: %s", keys or "none")
    return keys


def _judge_turn(text: str, source_context: dict[str, Any], session_id: str) -> tuple[str, ...] | None:
    """The plugin judgement for this turn's text, recorded with the built-in one; None when nothing judged the plugins
    (the words then decide alone)."""
    if not text.strip():
        return None
    rows = _installed_with_tools()
    signature = _signature(rows)
    # A plugin tool named by its own id ("use text-digest-example.sha256 ...") is not a guess from vocabulary: the
    # words settle which plugin is asked for, and no model is asked. Asking anyway spent a model call on every such
    # turn, and on a plugin that is turned off it was a model call on a turn that must say so before any model is
    # asked (CI on fe98e849, test_plugin_served_journey: an inference after the plugin was disabled).
    # An installed plugin that is switched off counts here too (by its id alone, from the lifecycle records): its
    # words are no longer put to the judge, and naming its tool must still reach the lane that says it is off.
    known = {plugin_id for plugin_id, _description in rows} | _installed_ids()
    named = _plugins_named_by_tool_id(text, known) if known else ()
    if named:
        _record(text, named, signature)
        return named
    # A pasted email or message is material, not the ask: the judge (and the word readers deciding whether to ask it)
    # read the person's own words in front of it, with the paste shown as text already in the message
    # (core.memory.admission reads the paste by its shape, in any language).
    from core.memory.admission import holds_a_pasted_message, text_before_a_pasted_message

    pasted = holds_a_pasted_message(text)
    own = text_before_a_pasted_message(text) if pasted else text
    if pasted:
        _record_paste(text, own)
    if pasted and not own.strip():
        # Nothing but a pasted mail: no words of the person's own ask anything of a tool. iMac gate22 on 1e85219dc: a
        # bare pasted parcel mail was judged a file find and the Desktop was searched.
        _record_builtins(text, ())
        _log.info("tool ask: the message is only pasted material; no tool is asked for")
        if not rows:
            return None
        _record(text, (), signature)
        return ()
    material = text[text.find(own) + len(own):].strip() if pasted and own != text else ""
    builtins = [*BUILTIN_CHOICES, *MEMORY_CHOICES] if _builtins_to_ask(own if material else text, source_context) else []
    if not rows and not builtins:
        return None
    from core import intent_arbiter

    # Asked whatever the words matched. The plugins' own words, and other families' word rules, guess from English
    # vocabulary: "what does a tracking number usually look like?" named a parcel plugin by its word "tracking", and
    # "any sign of it?" was taken by a wallet rule on "sign" (both measured on a7b18403, 2026-11-22). The judge
    # reads the tools' descriptions and the whole message; its answer is what the families follow. The plugins come
    # first, every one of them, then the built-in tools.
    shown_plugins = rows[: max(0, intent_arbiter.PLUGIN_ASK_CHOICES - len(builtins))]
    choices = [description for _plugin_id, description in shown_plugins] + [row[1] for row in builtins]
    started = time.monotonic()
    picked = None
    for _attempt in range(2):
        # A failed call or an unreadable answer is asked once more: without a judgement a plugin or a built-in tool
        # asked for in another language is not offered at all (outside review of 261a0389, R5).
        try:
            picked = intent_arbiter.arbitrate_plugin_ask(
                own if material else text,
                choices,
                material=material,
                previous=_previous_request(text, source_context),
                chat_id=str(session_id or ""),
                source_context=source_context,
            )
        except Exception as exc:
            _log.info("tool ask: judge call failed (%s)", type(exc).__name__)
            picked = None
        if picked is not None:
            break
    if isinstance(picked, int) and not isinstance(picked, bool):
        picked = (picked,) if picked else ()
    _log.info(
        "tool ask judged: %s of %d plugin(s) and %d built-in tool(s) in %.0f ms",
        picked, len(shown_plugins), len(builtins), (time.monotonic() - started) * 1000,
    )
    if picked is None:
        _log.warning(
            "tool ask: not judged after a retry (%s); the words decide this turn alone",
            intent_arbiter.last_failure() or "no answer",
        )
        return None
    numbers = [number for number in picked if 1 <= number <= len(choices)]
    plugins = tuple(dict.fromkeys(shown_plugins[n - 1][0] for n in numbers if n <= len(shown_plugins)))
    if builtins:
        picked_keys = tuple(builtins[n - len(shown_plugins) - 1][0] for n in numbers if n > len(shown_plugins))
        _record_first_picks(text, picked_keys)
        keys = _web_confirmed(text, picked_keys, source_context, session_id)
        read_again = bool(picked_keys and not keys and not plugins)
        if read_again:
            keys = _judged_again_without_the_web(text, builtins, source_context, session_id)
        before = keys
        keys = _actions_confirmed(text, keys, source_context, session_id)
        _record_builtins(text, _memory_kind_confirmed(text, keys, source_context, session_id,
                                                      first_read_as_the_web=read_again,
                                                      dropped_a_pick=bool(set(before) - set(keys))))
    if not rows:
        return None
    _record(text, plugins, signature)
    return plugins
