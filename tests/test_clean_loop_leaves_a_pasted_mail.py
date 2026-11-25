"""A pasted mail or message is material to work on, not the owner's ask: the files loop's word checks read only the
owner's own lines in front of it.

iMac gate21 on 006e4a888: an Italian "what is this about" over a pasted shop mail was admitted to the files loop
because the sender's address held "service", and a Portuguese one was read as a write order from the mail's "move
your appointment". All names, places and addresses are invented.
"""
from __future__ import annotations

import pytest

from core.clean_loop import route
from tests.test_memory_forget_resurrection_p0 import isolated_home

OWNER = {"_owner_local": True}
WORDS = {"invoice", "repair", "service", "garden", "appointment", "receipt"}


def _decide(text: str, session: str = "pasted-mail") -> route.RouteDecision:
    from core import plugin_ask

    plugin_ask.forget()
    return route.decide(text, source_context=OWNER, session_id=session, resolve_brain=lambda: object(), env={},
                        file_words=lambda: WORDS)


def _mail(ask: str, body: str) -> str:
    return f"{ask}\n\nFrom: Orchard Repairs <service@example.net>\nSubject: About your order\n\n{body}"


@pytest.mark.parametrize("ask, body", [
    ("Wat staat hier eigenlijk in?", "We will move the delivery to Thursday and save the old receipt for you."),
    ("Czego dotyczy ta wiadomość?", "Please create an account to see the repair invoice; the service is done."),
    ("Riassumimi questo, grazie.", "Your garden appointment is on Monday; we put the invoice in the box."),
])
def test_the_words_of_a_pasted_mail_do_not_send_a_summary_to_the_files(isolated_home, ask, body):
    decision = _decide(_mail(ask, body))
    assert not decision.admit, decision
    assert decision.reason not in {"write_turn", "run_turn"}, decision


def test_an_ask_of_the_owners_own_over_a_paste_is_still_read(isolated_home):
    decision = _decide(_mail("Save this mail as a file in my documents folder.", "Thanks for your order."))
    assert decision.reason == "write_turn", decision


def test_a_message_that_is_only_a_paste_asks_the_files_nothing(isolated_home):
    text = "From: Orchard Repairs <service@example.net>\nSubject: Invoice\n\nYour repair invoice is attached."
    decision = _decide(text)
    assert not decision.admit and decision.reason == "pasted_material", decision
