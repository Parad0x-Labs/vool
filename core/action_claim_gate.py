"""Model replies that say an action was done, or a live source was read, when no tool ran.

Measured 2026-10-29 (preview 0.7.7 re-audit and the 0.7.5 fresh-eyes test, qwen3:8b on CPU, no
mail account, no calendar): the model used no tool and the reply still said "No new emails
received today. - Checked email inbox", "your accountant thing is scheduled for the 3rd at 11",
"I'll nudge you in 20 minutes", "Sent the reply via email.", "Langue définie à français.".
Nothing was read, saved, sent or changed.

`gate_execution_claims` (final_answer_authorship) withholds a described TOOL RUN ("Tool: ...").
These replies name no tool; they state the outcome. This gate reads the same evidence and the
plain-sentence forms, one sentence at a time:

- a done or about-to-happen action by VOOL ("I'll add", "I sent", "Email sent.", "✅ Sent",
  "Your tweet is posted", "J'ai envoyé l'e-mail", "Te recordaré a las 9"),
- a report of a read ("Checked ...", "You've got 4 unread emails", "Nothing suspicious is
  running", "Tuesday afternoon is completely free"),

and only when the sentence names one of the live sources below. A sentence never counts when it
asks, offers, advises or hedges; explains how something works; relays what the user's own text
says ("your email says", "you mentioned", "d'après", "según", "laut"); rests on a premise the
user gave ("since your flight is booked"); recaps an earlier turn ("earlier", "a few minutes
ago"); or sits inside a draft, a quotation or a code block.

The gate stands down entirely when ANY tool ran this turn (as `gate_execution_claims` does), when
the provider's own tools were on for the turn, and for an illustrative request. Otherwise only
the claim sentences are removed and a plain correction line per source is added, in the reply's
language (English, French, Spanish or German); when nothing useful is left the line stands alone.
"""
from __future__ import annotations

import re
from typing import Any, Callable

_A = "['’]"  # apostrophe, straight or curly

# ---- live sources ------------------------------------------------------------------------------
_SOURCES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "mail",
        re.compile(
            r"\b(?:e-?mails?|emailed|inbox(?:es)?|mailbox|mail|unread|courriels?|bo[iî]te de r[ée]ception"
            r"|correos?(?: electr[óo]nicos?)?|bandeja de entrada|posteingang|nachricht(?:en)?)\b",
            re.I,
        ),
    ),
    (
        "calendar",
        re.compile(
            r"\b(?:calendar|agenda|appointments?|meetings?|events?|scheduled|rescheduled|booked|haircut"
            r"|calendrier|rendez-vous|r[ée]union|calendario|citas?|reuni[óo]n|kalender|termine?|besprechung)\b",
            re.I,
        ),
    ),
    (
        "reminders",
        re.compile(
            r"\b(?:reminders?|nudge|alarm|timer|remind(?:ing)? you|rappels?|minuteur|vous rappellerai|te rappellerai"
            r"|recordatorios?|temporizador|recordar[ée]|erinnerung(?:en)?|erinnere dich|erinnere sie|wecker)\b",
            re.I,
        ),
    ),
    (
        "notes",
        re.compile(
            r"\b(?:notes? app|apple notes|your notes|to (?:your )?notes|a note|the note|note added|note saved"
            r"|vos notes|une note|tus notas|una nota|notiz(?:en)?)\b",
            re.I,
        ),
    ),
    (
        "system",
        re.compile(
            r"\b(?:system scan|your system|processes|threats?|(?:scan|check) of (?:your|this) (?:computer|machine|mac|pc|system)|running (?:on|in) (?:the background|your (?:machine|computer|mac|pc))"
            r"|background (?:apps|tasks|processes)|malware|virus(?:es)?)\b",
            re.I,
        ),
    ),
    (
        "device",
        re.compile(r"\b(?:other|second|another) (?:machine|computer|device|laptop|mac|pc|vool)\b", re.I),
    ),
    (
        "social",
        re.compile(r"\b(?:twitter|tweets?|tweeted|retweets?|x\.com|to x|on x)\b", re.I),
    ),
    (
        "texts",
        re.compile(r"\b(?:texted|text message|sms|whatsapp|imessage|(?:i" + _A + r"ll|i will|let me) text)\b", re.I),
    ),
    (
        "settings",
        re.compile(
            r"\b(?:settings?|answer language|language|dark mode|theme|langue(?: des r[ée]ponses)?|r[ée]glages?"
            r"|param[èe]tres?|idioma|configuraci[óo]n|sprache|einstellungen?)\b",
            re.I,
        ),
    ),
    (
        "skills",
        re.compile(r"\b(?:skills?|comp[ée]tences?|habilidad(?:es)?|f[äa]higkeit(?:en)?)\b", re.I),
    ),
    (
        "model",
        re.compile(
            r"\b(?:(?:local|cloud|ai|language|chat|default) model|gpt-[\w.]+|qwen[\w.:]*|llama[\w.:]*|ollama"
            r"|mod[èe]le (?:local|cloud)|modelo (?:local|en la nube)|(?:lokale|cloud-)modell)\b",
            re.I,
        ),
    ),
    (
        "files",
        re.compile(
            r"\b(?:videos?|photos?|pictures?|screenshots?|images?|pdfs?|files?|folders?|documents folder|downloads(?: folder)?|desktop)\b"
            r"(?:[^.?!\n]{0,40}\b(?:on here|on (?:this|your) (?:computer|mac|pc|machine)|in your|from your|your desktop"
            r"|your downloads))"
            r"|\byour (?:downloads|desktop|documents|pictures) (?:folder)?",
            re.I,
        ),
    ),
)

# ---- claim forms -------------------------------------------------------------------------------
_DONE_PARTICIPLES = (
    r"sent|emailed|texted|tweeted|messaged|posted|added|put|scheduled|rescheduled|booked|set(?: up)?|saved|noted"
    r"|created|moved|updated|changed|cancel+ed|deleted|removed|forwarded|archived|cleaned(?: up)?|started"
    r"|turned (?:on|off)|switched (?:on|off|to|back)|enabled|disabled|activated|deactivated|installed|logged"
    r"|blocked(?: out)?|pencil+ed(?: in)?|jotted(?: down)?|checked|scanned|searched|replied|passed(?: on| along)?"
    r"|handed (?:off|over)"
)
_ACT_STEMS = (
    r"add|put|set|schedul|book|creat|sav|jot|nudg|remind|ping|mov|adjust|chang|updat|reschedul"
    r"|cancel|delet|remov|pass|send|sent|forward|hand|check|scan|look|search|edit|replac|log|mark"
    r"|block|pencil|note|turn|switch|enabl|disabl|activat|deactivat|repli|start|email|text|post|tweet"
    r"|clean|archiv|messag"
)
_ADVERBS = r"(?:(?:also|now|just|quickly|already|successfully|gone and|went ahead and|go ahead and|make sure to)\s+)*"
_FIRST_PERSON_ACTION = re.compile(
    r"\b(?:i" + _A + r"ll|i will|i" + _A + r"ve|i have|i" + _A + r"m going to|i am going to|let me|i" + _A + r"m|i am|i)\s+"
    + _ADVERBS + r"(?:" + _ACT_STEMS + r")\w*",
    re.I,
)
_PASSIVE_DONE = re.compile(
    r"\b(?:your|it|that|this|the)\b[^.?!\n]{0,60}?\b(?:is|are|has been|have been|was|were|" + _A + r"s|" + _A + r"re)\s+"
    r"(?:now\s+|all\s+|successfully\s+)?(?:" + _DONE_PARTICIPLES + r")\b"
    r"|\b(?:went out|is (?:now )?(?:in|on) your (?:calendar|schedule|agenda)|is now (?:on|off|set|active|running)"
    r"|(?:is|are) (?:on|off) now"
    r"|(?:timer|reminder|alarm) is (?:running|on|active|ticking)|is (?:now )?live"
    r"|you" + _A + r"ll (?:get|receive) (?:a |an )?(?:\w+ )?(?:reminder|notification|ping|nudge|alert))\b",
    re.I,
)
_NOUN_DONE = re.compile(
    r"\b(?:reminder|note|event|appointment|alarm|timer|meeting|email|e-mail|message|mail|calendar|tweet|post"
    r"|invoice|reply|inbox)\s*:?\s+(?:has been\s+|is\s+|was\s+)?(?:" + _DONE_PARTICIPLES + r")\b",
    re.I,
)
# Starts with the done verb and no subject: "Sent the reply via email.", "Added dentist to calendar.",
# "Switching back to the local model.", "Booked: haircut". Bare "Set"/"Turn"/"Switch"/"Put" read as
# advice ("Set aside ten minutes ...") and are left out.
_SUBJECTLESS_DONE = re.compile(
    r"^(?:sent|emailed|texted|tweeted|posted|replied|added|scheduled|rescheduled|booked|cancel+ed|saved"
    r"|created|moved|updated|deleted|removed|forwarded|archived|cleaned|enabled|disabled|activated"
    r"|deactivated|turned (?:on|off)|switched (?:on|off|to|back)|changed|started|now running|now using|composed|confirmed)\b"
    r"|^(?:switching|changing|turning|sending|cancel+ing|adding|scheduling|moving|enabling|disabling"
    r"|reminding you|posting|forwarding)\b"
    r"(?![^.!?\n]*\b(?:is|are|can|takes|means|lets|needs|requires|works|fails|often|usually|should|will"
    r"|may|might|helps|could|would|tends)\b)",
    re.I,
)
_DONE_MARK = re.compile(r"^[\s*_]*(?:done|all set|c" + _A + r"est fait|listo|hecho|erledigt|fertig)\b|[✅✔]", re.I)
_READ_REPORT = re.compile(
    r"\bi (?:ran|did|performed|completed) (?:a |an )?(?:quick |full |deep )?(?:scan|check|search)\b"
    r"|(?:^|\bi(?:" + _A + r"ve| have)?\s+)(?:just\s+)?(?:checked|scanned|looked (?:through|at|in|over)|searched"
    r"|went through|reviewed)\b"
    r"|\bbased on (?:the|a|my|your) (?:current |latest |recent |quick |live )?(?:system |inbox |calendar )?"
    r"(?:scan|check|search|inbox|calendar|feed)\b"
    r"|\byou(?:" + _A + r"ve got| have got| have| got)\s+(?:\d+|no|a few|some|several|one|two|three|four|five"
    r"|a couple of)\s+(?:new\s+|unread\s+)?(?:e-?mails?|messages?|meetings?|events?|appointments?|reminders?"
    r"|notes?|videos?|photos?|files?)\b"
    r"|\binbox:\s*\d+"
    r"|\b(?:no|nothing) (?:\w+ ){0,2}(?:processes|apps|threats|malware)\b[^.!?\n]{0,20}\b(?:active|running|found)\b"
    r"|\b(?:workspace|system|computer|machine|mac|pc) is (?:secure|safe|clean)\b"
    r"|(?:^|\byour )(?:inbox|mailbox|calendar|schedule|agenda|system|machine|computer) (?:is|looks) (?:completely |totally |pretty )?"
    r"(?:empty|clear|free|clean)\b",
    re.I,
)
_NONE_FOUND = re.compile(
    r"^(?:nope|no|nah)?[\s,.!—-]*(?:no|nothing|none|zero|there (?:are|is) no|there" + _A + r"s no"
    r"|you have no|you don" + _A + r"t have any|you haven" + _A + r"t (?:got|received) any)\b",
    re.I,
)
_REPORT_MARKER = re.compile(
    r"\b(?:found|received|new|unread|in your|on your|on here|running|blowing up|trending|scheduled"
    r"|today|tonight|yet|right now|at the moment|this (?:week|morning|afternoon|evening))\b",
    re.I,
)
_TIME_WORD = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|tonight|weekend"
    r"|morning|afternoon|evening|day|week|schedule|calendar)"
)
_FREE_SLOT = re.compile(
    r"^(?:next |this |on |your )?" + _TIME_WORD + r"\b[^.?!\n]{0,40}\b(?:is|are|looks?)\s+"
    r"(?:completely |totally |entirely |wide |pretty |mostly |fully )?(?:free|clear|open|booked|busy)\b",
    re.I,
)
# The other answer languages VOOL ships most (fr, es, de): a done participle or a first-person
# future, said by VOOL (first person), about the user's things (possessive), or as a done-mark.
_FOREIGN_DONE = re.compile(
    r"\b(?:d[ée]fini(?:e|s|es)?|activé(?:e|s|es)?|d[ée]sactivé(?:e|s|es)?|r[ée]glé(?:e|s|es)?"
    r"|changé(?:e|s|es)?|modifié(?:e|s|es)?|envoyé(?:e|s|es)?|annulé(?:e|s|es)?"
    r"|ajouté(?:e|s|es)?|programmé(?:e|s|es)?|enregistré(?:e|s|es)?|supprimé(?:e|s|es)?"
    r"|configurad[oa]s?|establecid[oa]s?|activad[oa]s?|desactivad[oa]s?|enviad[oa]s?|cancelad[oa]s?"
    r"|añadid[oa]s?|agregad[oa]s?|programad[oa]s?|cambiad[oa]s?|guardad[oa]s?|borrad[oa]s?"
    r"|eingestellt|aktiviert|deaktiviert|gesendet|geschickt|verschickt|abgesagt|storniert|hinzugef[üu]gt"
    r"|gesetzt|ge[äa]ndert|gespeichert|umgestellt|eingetragen|gel[öo]scht|verschoben|verlegt|eingeplant|erstellt|angelegt"
    r"|d[ée]placé(?:e|s|es)?|créé(?:e|s|es)?|movid[oa]s?|cread[oa]s?|eliminad[oa]s?"
    r"|j" + _A + r"enverrai|j" + _A + r"ajouterai|je (?:vous|te) rappellerai|recordar[ée]|enviar[ée]|añadir[ée]"
    r"|programar[ée]|ich erinnere|ich schicke|ich sende|ich trage|mis(?:e|es)? [àa] jour|actualizad[oa]s?|aktualisiert)\b",
    re.I,
)
_FOREIGN_SPEAKER = re.compile(
    r"\b(?:j" + _A + r"ai|je|nous avons|he|hemos|te|ich|wir haben|votre|vos|ton|ta|tes|tu|tus|su|sus|dein|deine"
    r"|deinen|ihr|ihre|ihren|c" + _A + r"est fait|listo|hecho|erledigt|fertig|est maintenant|ist jetzt|ahora es"
    r"|ist im|est dans|est sur|est[aá] en|der termin|die e-mail|el correo|la cita|le rendez-vous"
    r"|a [ée]t[ée]|ont [ée]t[ée]|ha sido|han sido|fue|fueron|wurde|wurden|ist|sind|est|sont)\b",
    re.I,
)
_FOREIGN_STATE = re.compile(r"\b(?:est maintenant|ist jetzt|ahora es|es ahora)\b", re.I)

# ---- what is never a claim ---------------------------------------------------------------------
_NOT_A_CLAIM = re.compile(
    # hedges, offers, advice, questions about what to do
    r"\b(?:can" + _A + r"?t|cannot|could not|couldn" + _A + r"t|unable|not able|don" + _A + r"t have (?:access|a way)"
    r"|no access|haven" + _A + r"t|have not|didn" + _A + r"t|did not|not connected|isn" + _A + r"t connected"
    r"|not set up|if you|once you|when you|as soon as|after you|you can|you could|you might|you may|you should"
    r"|would you like|do you want|want me to|should i|shall i|i can (?:help|add|set|check)|i could|i would"
    r"|i" + _A + r"d|let me know|tell me|just say|ask me"
    # approval-pending and drafts
    r"|approv\w*|draft\w*|prepared|queued|pending|nothing (?:is|was|has been) sent|not sent|until you|review it"
    # the user's own text, and premises they gave
    r"|says|said|wrote|writes|you pasted|you shared|you mentioned|you told me|you said|according to"
    r"|in the (?:text|message|email|note|mail) (?:above|you)|the (?:text|message|email|note|mail) you"
    r"|since your|because your|as your|now that your|given (?:that )?your"
    # recaps of earlier turns
    r"|earlier|previously|above|as (?:i|we) (?:mentioned|did|said|discussed)|like i said|ago|yesterday"
    r"|last (?:time|week|night)|recap|so far today"
    # how things work, not what was done
    r"|booked (?:out|up)|sold out|fully booked"
    r"|let me (?:check|see|think|confirm)(?=\s*[:,])|let me (?:check|see|confirm) (?:if|whether|that)"
    r"|automatically|whenever|by default|locally|usually|normally|typically|generally|in your browser"
    r"|in this code|this (?:function|script|code|method|loop)|the (?:function|script|code|loop|method|scheduler)"
    r"|vool (?:can|will|keeps|stores|schedules|switches|sends|checks|saves)"
    # fr / es / de: attribution, hedges, recaps, how-it-works
    r"|d" + _A + r"apr[èe]s|selon|seg[úu]n|laut|dice|dit|sagt|schreibt|ne peux pas|n" + _A + r"ai pas|pas pu"
    r"|vous pouvez|pouvez-vous|si vous|no puedo|no he|puedes|si quieres|kann nicht|konnte nicht"
    r"|nicht m[öo]glich|sie k[öo]nnen|wenn sie|wenn du|du kannst|kann|k[öo]nnen|wird|werden|peut|peuvent"
    r"|puede|pueden|se puede|automatiquement|localement|autom[aá]ticamente|automatisch|lokal"
    r"|plus t[ôo]t|tout [àa] l" + _A + r"heure|anteriormente|hace|vorhin|fr[üu]her|gestern|zuvor)\b",
    re.I,
)
# A line that introduces text for the user to send or keep. The block it opens (until a sign-off,
# a line where the assistant speaks again, or the end) is the user's text, not a claim.
_DRAFT_INTRO = re.compile(
    r"(?:draft|write|wording|reply|message|email|template|example|suggestion|version|send|text|say)\w*"
    r"[^\n]{0,20}[:—-]\s*$|^\s*(?:draft|subject|objet|asunto|betreff)\s*:",
    re.I,
)
_RESUME = re.compile(r"^\s*(?:let me know|want me|should i|i can|if you|feel free|shall i|would you)\b", re.I)
_SIGN_OFF = re.compile(
    r"^\s*(?:best|thanks|thank you|cheers|regards|kind regards|warm regards|love|see you|cordialement"
    r"|saludos|gr[üu][ßs]e|viele gr[üu][ßs]e)\b",
    re.I,
)
_QUOTED = re.compile(r"\"[^\"\n]{3,}\"|“[^”\n]{3,}”|«[^»\n]{3,}»|„[^“\n]{3,}“")
_SENTENCE_SPLIT = re.compile(
    r"(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bSt\.)(?<!\bvs\.)(?<!\bMrs\.)(?<!\be\.g\.)(?<!\bi\.e\.)"
    r"(?<=[.!?])\s+|(?<=;)\s+|\n+"
)
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_LEAD_FILLER = re.compile(
    r"^[\s>*_#`\-•✅✔]*(?:(?:got it|sure(?: thing)?|okay|ok|done|alright|all set|great|will do"
    r"|perfect|no problem)\b[\s,.!:;*—–-]*)*",
    re.I,
)
_CODE_REPLY_SOURCES = frozenset({"settings", "skills", "model", "files"})


_ABBREVIATION = re.compile(r"\b(Dr|Mr|Mrs|Ms|St|vs)\.", re.I)


def _claim_form(sentence: str) -> str:
    sentence = _ABBREVIATION.sub(r"\1", sentence)
    body = _LEAD_FILLER.sub("", sentence).strip(" *_")
    if not sentence.strip() or sentence.rstrip().endswith("?") or _NOT_A_CLAIM.search(sentence):
        return ""
    if _DONE_MARK.search(sentence):
        return "action"
    if not body:
        return ""
    if _FIRST_PERSON_ACTION.search(body) or _PASSIVE_DONE.search(body) or _NOUN_DONE.search(body):
        return "action"
    if _SUBJECTLESS_DONE.search(body):
        return "action"
    if _READ_REPORT.search(body):
        return "read"
    if _NONE_FOUND.search(body) and _REPORT_MARKER.search(body):
        return "read"
    if _FREE_SLOT.search(body):
        return "read"
    foreign = _FOREIGN_DONE.search(body)
    if foreign and (_FOREIGN_SPEAKER.search(body) or len(body[: foreign.start()].split()) <= 2):
        # "Langue définie à français." -- the done participle right after its noun is a report.
        return "action"
    if _FOREIGN_STATE.search(body):
        return "action"
    return ""


def _sources_of(sentence: str, form: str) -> list[str]:
    found = [name for name, words in _SOURCES if words.search(sentence)]
    if not found and form == "read" and _FREE_SLOT.search(_LEAD_FILLER.sub("", sentence).strip()):
        found = ["calendar"]
    if not found and form == "action" and _DONE_MARK.search(sentence):
        found = ["generic"]
    return found


def _readable_lines(content: str) -> list[str]:
    """The lines that speak for VOOL: drafts, quotations and code blocks left out."""
    out: list[str] = []
    in_fence = False
    in_draft = False
    for line in str(content or "").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.lstrip().startswith(">"):
            continue
        if in_draft:
            if _SIGN_OFF.search(line):
                in_draft = False
                continue
            if not _RESUME.search(line):
                continue
            in_draft = False
        if _DRAFT_INTRO.search(line):
            in_draft = True
            continue
        out.append(_INLINE_CODE.sub(" ", _QUOTED.sub(" ", line)))
    return out


def action_claims(content: str) -> list[tuple[str, str, str]]:
    """`(sentence, form, source)` for every sentence of ``content`` that says an action on, or a
    read of, a live source happened. ``form`` is "action" or "read"."""
    raw = str(content or "")
    has_code = bool(_FENCE.search(raw))
    claims: list[tuple[str, str, str]] = []
    unnamed: list[str] = []
    for line in _readable_lines(raw):
        for part in _SENTENCE_SPLIT.split(line):
            sentence = part.strip()
            if not sentence:
                continue
            form = _claim_form(sentence)
            if not form:
                continue
            sources = _sources_of(sentence, form)
            if not sources:
                unnamed.append(sentence)
                if _NOUN_DONE.search(sentence):
                    # "Message sent to Jo." names what was done, not where: the request decides.
                    claims.append((sentence, form, "generic"))
                    unnamed.pop()
            for source in sources:
                if has_code and source in _CODE_REPLY_SOURCES:
                    continue
                claims.append((sentence, form, source))
    if claims and unnamed:
        # "Forwarded the invoice to Priya. Sent it from your email." -- a done-sentence that names
        # no source belongs to the claim the rest of the reply makes.
        first = next(c[2] for c in claims if c[2] != "generic") if any(c[2] != "generic" for c in claims) else claims[0][2]
        claims = [(s, _claim_form(s), first) for s in unnamed] + claims
    return claims


# ---- the correction lines, in the reply's language ---------------------------------------------
_LINES: dict[str, dict[str, str]] = {
    "en": {
        "mail_none_read": "No email account is connected yet, so I haven't checked any inbox. You can add one under Settings > Email accounts.",
        "mail_none_send": "No email account is connected yet, so nothing was sent. You can add one under Settings > Email accounts.",
        "mail_read": "I haven't checked your email in this reply. Ask me to check your email and I'll read it.",
        "mail_send": "No email was sent. Ask me to draft it and I'll show it to you before anything goes out.",
        "calendar_none": "No calendar is configured or chosen in Settings yet, so I haven't read or changed your schedule. You can connect one under Settings > Calendars.",
        "calendar": "I haven't read or changed your calendar. Ask again with the day and time and I'll use the calendar.",
        "reminders": "No reminder or timer was set, changed or cancelled. Ask me again with the time and I'll do it.",
        "notes": "No note was saved. Ask me to add it as a note and I'll save it.",
        "system": "I haven't scanned this computer or looked at what's running. Ask me to list the running processes and I'll check.",
        "device": "Nothing was sent to another machine.",
        "social": "I haven't looked at or posted anything on X (Twitter). Ask me to look it up and I'll check.",
        "texts": "No text message was sent.",
        "settings": "Nothing in Settings was changed. You can change it in Settings (the answer language is under Answer language).",
        "skills": "No skill was turned on or off. You can do that in the skills settings.",
        "model": "The model was not switched. You can choose the model in Settings → Models.",
        "files": "I haven't looked through or changed your files. Ask me to search your files and I'll look.",
        "generic": "Nothing was done yet: no tool ran for this request. Ask again and I'll do it with the right tool.",
    },
    "fr": {
        "mail_none_read": "Aucun compte e-mail n'est encore connecté, je n'ai donc consulté aucune boîte de réception. Vous pouvez en ajouter un dans Réglages > Comptes e-mail.",
        "mail_none_send": "Aucun compte e-mail n'est encore connecté, donc rien n'a été envoyé. Vous pouvez en ajouter un dans Réglages > Comptes e-mail.",
        "mail_read": "Je n'ai pas consulté vos e-mails dans cette réponse. Demandez-moi de vérifier vos e-mails et je les lirai.",
        "mail_send": "Aucun e-mail n'a été envoyé. Demandez-moi un brouillon et je vous le montrerai avant tout envoi.",
        "calendar_none": "Aucun calendrier n'est encore configuré dans les Réglages, je n'ai donc ni lu ni modifié votre agenda. Vous pouvez en connecter un dans Réglages > Calendriers.",
        "calendar": "Je n'ai ni lu ni modifié votre calendrier. Redemandez avec le jour et l'heure et j'utiliserai le calendrier.",
        "reminders": "Aucun rappel ni minuteur n'a été créé, modifié ou annulé. Redemandez avec l'heure et je le ferai.",
        "notes": "Aucune note n'a été enregistrée. Demandez-moi de l'ajouter comme note et je l'enregistrerai.",
        "system": "Je n'ai pas analysé cet ordinateur ni regardé ce qui tourne. Demandez-moi la liste des processus et je vérifierai.",
        "device": "Rien n'a été envoyé à une autre machine.",
        "social": "Je n'ai rien consulté ni publié sur X (Twitter). Demandez-moi de chercher et je vérifierai.",
        "texts": "Aucun SMS n'a été envoyé.",
        "settings": "Rien n'a été modifié dans les Réglages. Vous pouvez le changer dans les Réglages (la langue des réponses est sous Langue des réponses).",
        "skills": "Aucune compétence n'a été activée ou désactivée. Vous pouvez le faire dans les réglages des compétences.",
        "model": "Le modèle n'a pas été changé. Vous pouvez choisir le modèle dans Réglages → Modèles.",
        "files": "Je n'ai ni parcouru ni modifié vos fichiers. Demandez-moi de chercher dans vos fichiers et je regarderai.",
        "generic": "Rien n'a encore été fait : aucun outil n'a été utilisé pour cette demande. Redemandez et je le ferai avec le bon outil.",
    },
    "es": {
        "mail_none_read": "Todavía no hay ninguna cuenta de correo conectada, así que no he revisado ninguna bandeja de entrada. Puedes añadir una en Ajustes > Cuentas de correo.",
        "mail_none_send": "Todavía no hay ninguna cuenta de correo conectada, así que no se ha enviado nada. Puedes añadir una en Ajustes > Cuentas de correo.",
        "mail_read": "No he revisado tu correo en esta respuesta. Pídeme que revise tu correo y lo leeré.",
        "mail_send": "No se ha enviado ningún correo. Pídeme un borrador y te lo mostraré antes de enviar nada.",
        "calendar_none": "Todavía no hay ningún calendario configurado en Ajustes, así que no he leído ni cambiado tu agenda. Puedes conectar uno en Ajustes > Calendarios.",
        "calendar": "No he leído ni cambiado tu calendario. Vuelve a pedirlo con el día y la hora y usaré el calendario.",
        "reminders": "No se ha creado, cambiado ni cancelado ningún recordatorio ni temporizador. Vuelve a pedirlo con la hora y lo haré.",
        "notes": "No se ha guardado ninguna nota. Pídeme que la añada como nota y la guardaré.",
        "system": "No he analizado este ordenador ni mirado qué se está ejecutando. Pídeme la lista de procesos y lo comprobaré.",
        "device": "No se ha enviado nada a otra máquina.",
        "social": "No he mirado ni publicado nada en X (Twitter). Pídeme que lo busque y lo comprobaré.",
        "texts": "No se ha enviado ningún mensaje de texto.",
        "settings": "No se ha cambiado nada en Ajustes. Puedes cambiarlo en Ajustes (el idioma de las respuestas está en Idioma de respuesta).",
        "skills": "No se ha activado ni desactivado ninguna habilidad. Puedes hacerlo en los ajustes de habilidades.",
        "model": "No se ha cambiado el modelo. Puedes elegir el modelo en Ajustes → Modelos.",
        "files": "No he revisado ni cambiado tus archivos. Pídeme que busque en tus archivos y lo miraré.",
        "generic": "Todavía no se ha hecho nada: no se usó ninguna herramienta para esta petición. Vuelve a pedirlo y lo haré con la herramienta adecuada.",
    },
    "de": {
        "mail_none_read": "Es ist noch kein E-Mail-Konto verbunden, daher habe ich keinen Posteingang geprüft. Du kannst eines unter Einstellungen > E-Mail-Konten hinzufügen.",
        "mail_none_send": "Es ist noch kein E-Mail-Konto verbunden, daher wurde nichts gesendet. Du kannst eines unter Einstellungen > E-Mail-Konten hinzufügen.",
        "mail_read": "Ich habe deine E-Mails in dieser Antwort nicht geprüft. Bitte mich, deine E-Mails zu prüfen, und ich lese sie.",
        "mail_send": "Es wurde keine E-Mail gesendet. Bitte mich um einen Entwurf, und ich zeige ihn dir, bevor etwas rausgeht.",
        "calendar_none": "In den Einstellungen ist noch kein Kalender eingerichtet, daher habe ich deinen Kalender weder gelesen noch geändert. Du kannst einen unter Einstellungen > Kalender verbinden.",
        "calendar": "Ich habe deinen Kalender weder gelesen noch geändert. Frag noch einmal mit Tag und Uhrzeit, und ich nutze den Kalender.",
        "reminders": "Es wurde keine Erinnerung und kein Timer gesetzt, geändert oder gelöscht. Frag noch einmal mit der Uhrzeit, und ich mache es.",
        "notes": "Es wurde keine Notiz gespeichert. Bitte mich, sie als Notiz hinzuzufügen, und ich speichere sie.",
        "system": "Ich habe diesen Computer nicht geprüft und nicht nachgesehen, was läuft. Bitte mich um die Liste der Prozesse, und ich sehe nach.",
        "device": "Es wurde nichts an ein anderes Gerät gesendet.",
        "social": "Ich habe auf X (Twitter) nichts angesehen oder gepostet. Bitte mich, es nachzuschlagen, und ich sehe nach.",
        "texts": "Es wurde keine SMS gesendet.",
        "settings": "In den Einstellungen wurde nichts geändert. Du kannst es in den Einstellungen ändern (die Antwortsprache steht unter Antwortsprache).",
        "skills": "Es wurde keine Fähigkeit ein- oder ausgeschaltet. Das geht in den Fähigkeiten-Einstellungen.",
        "model": "Das Modell wurde nicht gewechselt. Du kannst das Modell unter Einstellungen → Modelle wählen.",
        "files": "Ich habe deine Dateien weder durchsucht noch geändert. Bitte mich, deine Dateien zu durchsuchen, und ich sehe nach.",
        "generic": "Es wurde noch nichts gemacht: Für diese Anfrage lief kein Werkzeug. Frag noch einmal, und ich mache es mit dem richtigen Werkzeug.",
    },
}
_LANG_WORDS = {
    "fr": re.compile(r"\b(?:le|la|les|des|est|et|vous|votre|je|j" + _A + r"ai|pour|une|pas|dans|sur|à)\b", re.I),
    "es": re.compile(r"\b(?:el|los|las|es|y|tu|te|he|para|una|por|del|en|que|con|ya)\b", re.I),
    "de": re.compile(r"\b(?:der|die|das|ist|und|ich|du|dich|dein|deine|habe|für|nicht|ein|eine|im|um|wurde)\b", re.I),
}
_EN_WORDS = re.compile(r"\b(?:the|is|and|you|your|i|to|for|a|an|it|in|on|of|was|have)\b", re.I)


def reply_language(text: str) -> str:
    """'en', 'fr', 'es' or 'de': the language the reply is written in, else the turn's app
    language when it is one of those, else English."""
    body = str(text or "")
    counts = {lang: len(words.findall(body)) for lang, words in _LANG_WORDS.items()}
    english = len(_EN_WORDS.findall(body))
    lang, count = max(counts.items(), key=lambda kv: kv[1])
    if count >= 2 and count > english:
        return lang
    if english:
        return "en"
    try:
        from core.i18n.turn_locale import turn_locale

        bound = str(turn_locale() or "").split("-")[0].lower()
        if bound in _LINES:
            return bound
    except Exception:
        pass
    return "en"


# ---- what is connected -------------------------------------------------------------------------


def _mail_connected() -> bool | None:
    try:
        from core.email_accounts import configured_accounts

        return bool(configured_accounts())
    except Exception:
        return None


def _calendar_connected() -> bool | None:
    try:
        from core.operator.calendar_provider import load_read_provider_config

        config = load_read_provider_config()
        return bool(config is not None and config.configured)
    except Exception:
        return None


def _connected_reader() -> Callable[[str], bool | None]:
    cache: dict[str, bool | None] = {}
    readers = {"mail": _mail_connected, "calendar": _calendar_connected}

    def read(name: str) -> bool | None:
        if name not in cache:
            cache[name] = readers[name]() if name in readers else None
        return cache[name]

    return read


def _line_key(source: str, forms: frozenset[str], connected: Callable[[str], bool | None]) -> str:
    if source == "mail":
        sent = "action" in forms
        if connected("mail") is False:
            return "mail_none_send" if sent else "mail_none_read"
        return "mail_send" if sent else "mail_read"
    if source == "calendar":
        return "calendar_none" if connected("calendar") is False else "calendar"
    return source if source in _LINES["en"] else "generic"


def no_action_reply(
    sources: list[str],
    *,
    connected: Callable[[str], bool | None] | None = None,
    forms: dict[str, set[str]] | None = None,
    language: str = "en",
) -> str:
    """The plain correction, one line per claimed source, in ``language``."""
    read = connected or _connected_reader()
    table = _LINES.get(language) or _LINES["en"]
    form_map = dict(forms or {})
    keys = [_line_key(s, frozenset(form_map.get(s) or {"read"}), read) for s in dict.fromkeys(sources)]
    return "\n\n".join(table[k] for k in dict.fromkeys(keys))


# ---- evidence ----------------------------------------------------------------------------------


def _any_execution(turn_id: str, source_context: Any) -> bool:
    """Any tool run, retrieval, typed observation or effect this turn, or the provider's own tools
    on for it. Matches `gate_execution_claims`: a turn that ran anything is not second-guessed."""
    try:
        from core.final_answer_authorship import _turn_executed_anything

        if _turn_executed_anything(turn_id, source_context):
            return True
    except Exception:
        pass
    if isinstance(source_context, dict):
        if source_context.get("runtime_tool_observations") or source_context.get("fresh_data_retrieval_receipts"):
            return True
        try:
            from core.model_own_tools import turn_stamped

            if turn_stamped(source_context):
                return True
        except Exception:
            pass
    return False


_FLUFF = re.compile(
    r"^(?:let me know|is there anything else|anything else|just let me know|hope (?:this|that) helps"
    r"|want me to do anything else|would you like)",
    re.I,
)


def _without_claims(content: str, claimed: set[str]) -> str:
    """``content`` with the claimed sentences taken out; '' when only filler would be left."""
    kept_lines: list[str] = []
    for line in str(content or "").split("\n"):
        if not line.strip():
            kept_lines.append("")
            continue
        parts = re.split(r"(?<=[.!?;])\s+", line)
        kept = [p for p in parts if p.strip() and p.strip() not in claimed
                and _LEAD_FILLER.sub("", p).strip(" *_-") not in {s.strip() for s in claimed}]
        if not kept:
            continue
        if len(kept) == len(parts):
            kept_lines.append(line)
        else:
            text = " ".join(kept).strip()
            bullet = re.match(r"^(\s*(?:[-*•]|\d+[.)])\s+)", line)
            if bullet and not text.startswith(bullet.group(1).strip()):
                text = bullet.group(1) + text
            kept_lines.append(text)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    meaningful = [
        s for s in _SENTENCE_SPLIT.split(body)
        if _LEAD_FILLER.sub("", s).strip(" -*_.!,") and not _FLUFF.match(_LEAD_FILLER.sub("", s).strip())
    ]
    return body if meaningful else ""


def gate_action_claims(
    content: str,
    *,
    turn_id: str = "",
    source_context: Any = None,
    request_text: str = "",
    connected: Callable[[str], bool | None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Correct a MODEL-AUTHORED reply that says it did or read something no tool did this turn.

    The claim sentences are removed and a plain line per source is added in the reply's
    language; the rest of the reply is kept. Stands down for runtime-composed bytes, an
    illustrative request, and any execution this turn.
    """
    from core.final_answer_authorship import model_authored_turn

    text = str(content or "")
    authored, recorded_request = model_authored_turn(turn_id=turn_id, source_context=source_context)
    if not authored:
        return text, {"publication": "stood_down:not_model_authored"}
    claims = action_claims(text)
    if not claims:
        return text, {"publication": "stood_down:no_action_claims"}
    sources = list(dict.fromkeys(c[2] for c in claims))
    request = str(request_text or "") or recorded_request
    try:
        from core.stipulated_frame import has_stipulated_frame

        if request and has_stipulated_frame(request):
            return text, {"action_claims": sources, "publication": "illustrative_frame"}
    except Exception:
        pass
    if _any_execution(turn_id, source_context):
        return text, {"action_claims": sources, "publication": "execution_evidence_present"}
    if "generic" in sources and request:
        # "✅ Sent" names nothing; the request says what it was about.
        from_request = [name for name, words in _SOURCES if words.search(request)]
        if from_request:
            claims = [(c[0], c[1], from_request[0] if c[2] == "generic" else c[2]) for c in claims]
            sources = list(dict.fromkeys(c[2] for c in claims))
    forms: dict[str, set[str]] = {}
    for _sentence, form, source in claims:
        forms.setdefault(source, set()).add(form)
    language = reply_language(text)
    correction = no_action_reply(sources, connected=connected, forms=forms, language=language)
    remainder = _without_claims(text, {c[0] for c in claims})
    published = f"{remainder}\n\n{correction}" if remainder else correction
    return published, {
        "action_claims": sources,
        "claimed_sentences": [c[0] for c in claims][:6],
        "publication": "action_claims_corrected",
        "language": language,
        "kept_rest_of_reply": bool(remainder),
    }


__all__ = ["action_claims", "gate_action_claims", "no_action_reply", "reply_language"]
