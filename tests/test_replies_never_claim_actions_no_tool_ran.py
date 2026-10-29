"""R4 (re-audit 2026-10-29): replies said things were done or read when no tool ran.

With no mail account, "did i get any new emails today?" was answered "No new emails received
today. - Checked email inbox"; with no calendar, "am I free next Tuesday afternoon" was answered
"completely free"; "nudge me in 20 minutes" got "I'll nudge you in 20 minutes" and no reminder
existed. The model wrote those bytes and the turn's ledgers held no tool run.

`core.action_claim_gate` is checked here three ways: the sentence reader on claim and non-claim
wording of our own (not the audit replies), the gate against the turn's own recorders, and one
real `VoolAgent.run_once` turn served through `finalize_answer` with the model scripted at the
provider seam.
"""
from __future__ import annotations

import pytest

from core.action_claim_gate import action_claims, gate_action_claims, no_action_reply
from core.grounding_lifecycle import record_model_authorship, register_required
from core.grounding_lifecycle import reset_for_tests as reset_lifecycles
from core.semantic.semantic_admissions import bound_request_context

SESSION = "openclaw:action-claims-test"

CLAIMS = {
    "mail_checked": ("Looked at your mail just now: no new emails since this morning.", "mail"),
    "mail_bullets": ("Inbox is clear.\n- Checked your inbox for anything from the landlord.", "mail"),
    "calendar_booked": ("Done! Your haircut is booked for Friday at 3.", "calendar"),
    "calendar_moved": ("Okay, I've moved the team meeting to Thursday.", "calendar"),
    "calendar_free": ("Thursday morning looks totally clear for you.", "calendar"),
    "reminder": ("No problem, I'll ping you at 6 so you remember the parcel. Reminder set.", "reminders"),
    "note": ("I've jotted the wifi password into a note in your notes app.", "notes"),
    "system": ("I scanned your system and there are no suspicious processes.", "system"),
    "device": ("I'll hand this over to your second laptop right away.", "device"),
    "social": ("Nothing much trending on Twitter right now, just the usual.", "social"),
    "files": ("You have no photos in your Pictures folder on this computer yet.", "files"),
    # fresh-eyes test on 0.7.5: sends, cancels, settings, skills, model switch
    "mail_sent": ("Forwarded the invoice to Priya.\n- Sent it from your email this afternoon.", "mail"),
    "mail_empty": ("Your mailbox looks completely empty.", "mail"),
    "reminder_cancel": ("Reminder cancelled: water the plants (Sunday 10:00).", "reminders"),
    "timer": ("Okay, I'm starting a timer for 5 minutes now.", "reminders"),
    "calendar_added": ("Added the vet visit to your calendar for Monday at 9.", "calendar"),
    "setting_en": ("Done, dark mode is now on.", "settings"),
    "setting_fr": ("Paramètres mis à jour : la langue des réponses est réglée sur l'espagnol.", "settings"),
    "setting_de": ("Die Sprache wurde auf Deutsch umgestellt.", "settings"),
    "skill_off": ("The recipe planner skill has been disabled.", "skills"),
    "model_switch": ("Switched to the cloud model, you're now using gpt-5.", "model"),
}

NOT_CLAIMS = (
    "You could look in your inbox for the receipt; shops usually send one within an hour.",
    "I can't read your calendar, so I don't know whether Friday is free.",
    "Should I set a reminder for 6pm?",
    "Most people keep meeting invites in one shared calendar so nothing double books.",
    "A note app is handy for shopping lists.",
    "Activity Monitor shows the processes running on a Mac.",
    "If you want, I can check your email once you add an account in Settings.",
    "Twitter is now called X.",
    "The gallery is free on Thursday evenings.",
    "Here is a message you can send:\n\nHi Ana, the meeting is booked for Friday.",
    "I'll walk you through how compound interest works.",
    "Let me know if there's anything else!",
    "```\nremind me in 20 minutes\n```\nThat phrase sets a reminder.",
    "Set aside ten minutes each morning to go through your inbox.",
    "Switching to a cloud model is done in Settings, under Models.",
    "Vous pouvez changer la langue des réponses dans les réglages.",
    "Skills are small add-ons that teach VOOL one kind of task.",
    "Sending big attachments by email often fails, so share a link.",
    "I've renamed the model variable in the snippet:\n```python\nmodel = load('qwen3:8b')\n```",
)


@pytest.fixture(autouse=True)
def _fresh():
    from core.final_answer_authorship import reset_for_tests

    reset_lifecycles()
    reset_for_tests()
    yield
    reset_lifecycles()
    reset_for_tests()


# ---- the sentence reader ------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(CLAIMS))
def test_a_done_or_read_sentence_names_its_source(key: str) -> None:
    text, source = CLAIMS[key]
    found = {c[2] for c in action_claims(text)}
    assert source in found, (key, action_claims(text))


@pytest.mark.parametrize("text", NOT_CLAIMS)
def test_advice_questions_offers_and_hedges_are_not_claims(text: str) -> None:
    assert action_claims(text) == []


# ---- the gate against the turn's recorders ------------------------------------------------------


def _model_turn(request_text: str, request_id: str) -> dict:
    context = {"session_id": SESSION, "request_id": request_id, "turn_id": request_id}
    register_required(context, request_text=request_text, reason_codes=("test",))
    record_model_authorship(context)
    return context


def _no_connections(name: str) -> bool:
    return False


def test_no_mail_account_answers_from_that_fact() -> None:
    request_id = "req:claims:mail"
    with bound_request_context(request_id):
        context = _model_turn("anything new in my email?", request_id)
        published, record = gate_action_claims(
            CLAIMS["mail_bullets"][0], turn_id=request_id, source_context=context, connected=_no_connections
        )
    assert "Checked your inbox" not in published
    assert "No email account is connected yet" in published
    assert "Settings > Email accounts" in published
    assert record["publication"] == "action_claims_corrected" and record["action_claims"] == ["mail"]


def test_no_mail_account_reads_the_real_credential_store() -> None:
    from core.email_accounts import configured_accounts

    assert configured_accounts() == {}, "the test profile is expected to hold no email account"
    reply = no_action_reply(["mail"])
    assert reply.startswith("No email account is connected yet")


def test_a_connected_source_gets_the_not_done_line_without_the_setup_hint() -> None:
    reply = no_action_reply(["mail", "calendar"], connected=lambda name: True)
    assert "No email account" not in reply and "Settings > Calendars" not in reply
    assert "I haven't checked your email" in reply and "I haven't read or changed your calendar" in reply


def test_several_claimed_sources_each_get_one_line() -> None:
    request_id = "req:claims:two"
    text = CLAIMS["calendar_booked"][0] + " " + CLAIMS["reminder"][0]
    with bound_request_context(request_id):
        context = _model_turn("book my haircut friday at 3 and remind me", request_id)
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context, connected=_no_connections)
    assert record["action_claims"] == ["calendar", "reminders"]
    assert "booked for Friday" not in published
    assert "No calendar is configured" in published and "No reminder or timer was set" in published


def test_a_send_claim_with_no_mail_account_says_nothing_was_sent() -> None:
    request_id = "req:claims:send"
    with bound_request_context(request_id):
        context = _model_turn("send priya the invoice", request_id)
        published, record = gate_action_claims(
            CLAIMS["mail_sent"][0], turn_id=request_id, source_context=context, connected=_no_connections
        )
    assert "Forwarded the invoice" not in published
    assert published.startswith("No email account is connected yet, so nothing was sent")


def test_a_non_english_settings_claim_is_replaced() -> None:
    request_id = "req:claims:fr"
    with bound_request_context(request_id):
        context = _model_turn("mets la langue des réponses en espagnol", request_id)
        published, record = gate_action_claims(
            CLAIMS["setting_fr"][0], turn_id=request_id, source_context=context, connected=_no_connections
        )
    assert record["action_claims"] == ["settings"]
    # the correction is written in the reply's language
    assert "réglée" not in published and "Rien n'a été modifié dans les Réglages." in published


def test_skill_and_model_claims_each_say_nothing_changed() -> None:
    request_id = "req:claims:skill-model"
    text = CLAIMS["skill_off"][0] + "\n" + CLAIMS["model_switch"][0]
    with bound_request_context(request_id):
        context = _model_turn("turn off the recipe skill and use gpt-5", request_id)
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context, connected=_no_connections)
    assert record["action_claims"] == ["skills", "model"]
    assert "No skill was turned on or off" in published and "The model was not switched" in published


def test_a_matching_tool_run_this_turn_leaves_the_reply_alone() -> None:
    from core.turn_model_call_ledger import begin_turn, record_tool_execution

    request_id = "req:claims:backed"
    with bound_request_context(request_id):
        context = _model_turn("remind me at 6 about the parcel", request_id)
        begin_turn(context)
        record_tool_execution(context, "operator.schedule_reminder")
        text = CLAIMS["reminder"][0]
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context)
    assert published == text
    assert record["publication"] == "execution_evidence_present"


def test_any_tool_run_this_turn_stands_the_check_down() -> None:
    # Same rule as gate_execution_claims: a mail read that relays a meeting is not second-guessed
    # because the meeting is a calendar word.
    from core.turn_model_call_ledger import begin_turn, record_tool_execution

    request_id = "req:claims:other-tool"
    text = "I checked your inbox: the visit is scheduled for Wednesday at 8, and I've put it in your calendar."
    with bound_request_context(request_id):
        context = _model_turn("did the plumber email me", request_id)
        begin_turn(context)
        record_tool_execution(context, "email.read")
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context, connected=_no_connections)
    assert published == text and record["publication"] == "execution_evidence_present"


def test_only_the_claim_sentence_goes_and_the_rest_of_the_reply_stays() -> None:
    request_id = "req:claims:partial"
    text = (
        "Three ways to stop forgetting the bins:\n"
        "- Put the bin bag by the door the night before.\n"
        "- Ask a neighbour to text you.\n"
        "- I've set a reminder for every Tuesday at 7pm."
    )
    with bound_request_context(request_id):
        context = _model_turn("how do i stop forgetting the bins", request_id)
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context, connected=_no_connections)
    assert "Put the bin bag by the door" in published and "Ask a neighbour" in published
    assert "I've set a reminder" not in published
    assert published.rstrip().endswith("Ask me again with the time and I'll do it.")
    assert record["kept_rest_of_reply"] is True


def test_runtime_composed_bytes_are_not_gated() -> None:
    text = "Reminder set for 6pm: parcel."
    published, record = gate_action_claims(text, turn_id="turn-runtime", source_context=None)
    assert published == text
    assert record == {"publication": "stood_down:not_model_authored"}


def test_a_model_reply_without_claims_passes_unchanged() -> None:
    request_id = "req:claims:advice"
    with bound_request_context(request_id):
        context = _model_turn("how do i stop missing emails", request_id)
        text = NOT_CLAIMS[0]
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context)
    assert published == text and record["publication"] == "stood_down:no_action_claims"


def test_an_illustrative_request_keeps_its_reply() -> None:
    request_id = "req:claims:pretend"
    with bound_request_context(request_id):
        context = _model_turn(
            "pretend you checked my inbox and show me what the summary would look like", request_id
        )
        text = CLAIMS["mail_bullets"][0]
        published, record = gate_action_claims(text, turn_id=request_id, source_context=context)
    assert published == text and record["publication"] == "illustrative_frame"


# ---- one real turn, served ----------------------------------------------------------------------

from tests.test_final_answer_author_eligibility_p0 import (  # noqa: E402
    _ProviderScript,
    _certify,
    _drive,
    _local_manifest,
    _pinned_backend_identity,  # noqa: F401  (autouse fixture: pinned backend, clean registry)
    _register,
    _serve,
)


def _served(request: str, model_text: str, session: str) -> tuple[str, _ProviderScript]:
    local = _local_manifest()
    _register(local)
    _certify(local, state="verified")
    result, context, script = _drive(session, request=request, script=_ProviderScript(text=model_text), candidates=[local])
    return _serve(context, result), script


def test_served_turn_with_no_mail_account_does_not_show_a_made_up_inbox_check() -> None:
    fake = "Nothing new has come in today.\n- Checked your inbox for today's mail.\n- Zero unread emails."
    served, script = _served("has anything landed in my email today?", fake, "sess-claims-mail")
    assert script.manifests, "the drive never reached the provider seam"
    assert "Checked your inbox" not in served and "Zero unread" not in served, served[:400]
    assert "No email account is connected yet" in served, served[:400]


def test_served_turn_that_promises_a_reminder_it_never_set_says_so() -> None:
    fake = "Sure! I'll nudge you in half an hour so the laundry doesn't sit. Anything else?"
    served, script = _served("give me a nudge in half an hour about the laundry", fake, "sess-claims-nudge")
    assert script.manifests, "the drive never reached the provider seam"
    assert "I'll nudge you" not in served, served[:400]
    assert "No reminder or timer was set" in served, served[:400]


def test_served_plain_advice_is_untouched() -> None:
    advice = "You could set a phone alarm for half an hour; most phones do it from the clock app."
    served, script = _served("whats a good way to remember the laundry", advice, "sess-claims-advice")
    assert script.manifests, "the drive never reached the provider seam"
    assert advice in served, served[:400]


# ---- REVIEW2 musts --------------------------------------------------------------------------------


def _gate_plain(text: str, request_id: str) -> tuple[str, dict]:
    with bound_request_context(request_id):
        context = _model_turn("help", request_id)
        return gate_action_claims(text, turn_id=request_id, source_context=context, connected=lambda name: True)


def test_a_claim_with_a_quoted_name_is_removed_not_left_above_its_correction() -> None:
    text = 'Tip: keep lists short.\nI filed it as a note titled "Boiler service".'
    published, record = _gate_plain(text, "req:r2:quote")
    assert "Boiler service" not in published and "Tip: keep lists short." in published
    assert "No note was saved." in published


def test_a_trailing_check_mark_takes_the_sentence_it_confirms_with_it() -> None:
    published, record = _gate_plain("Bins reminder: every Tuesday 7pm. ✅", "req:r2:mark")
    assert "every Tuesday" not in published and "No reminder or timer was set" in published


def test_a_read_claim_gets_a_not_checked_line_not_a_not_sent_line() -> None:
    published, record = _gate_plain("I went through your inbox and the parcel notice is from DPD.", "req:r2:read")
    assert "I haven't checked your email" in published and "sent" not in published


def test_no_correction_is_added_when_nothing_could_be_removed(monkeypatch) -> None:
    import core.action_claim_gate as gate_module

    monkeypatch.setattr(gate_module, "_without_claims", lambda content, spans: str(content).strip())
    text = "I've emailed the plumber."
    published, record = _gate_plain(text, "req:r2:noremove")
    assert published == text and record["publication"] == "stood_down:claim_not_removable"


def test_an_unreadable_evidence_lookup_stands_the_gate_down(monkeypatch) -> None:
    import core.final_answer_authorship as authorship

    def broken(*_a, **_k):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(authorship, "_turn_executed_anything", broken)
    text = "I've emailed the plumber."
    published, record = _gate_plain(text, "req:r2:broken")
    assert published == text and record["publication"] == "execution_evidence_present"


@pytest.mark.parametrize(
    "text",
    (
        "In the picture you uploaded, the reminder shows 8am and it is switched on.",
        "Your parcel will be marked delivered once the courier scans it.",
        "J'ai bien lu ton message : tu veux annuler le rendez-vous chez le coiffeur ?",
        "Ho aggiunto un promemoria per domani alle 8.",
    ),
)
def test_user_material_conditionals_restatements_and_unlined_languages_pass(text: str) -> None:
    published, _record = _gate_plain(text, f"req:r2:pass:{abs(hash(text))}")
    assert published == text
