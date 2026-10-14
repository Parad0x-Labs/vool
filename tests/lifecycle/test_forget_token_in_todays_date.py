"""A forgotten value that also appears in today's date withholds only the records that hold it.

Measured on 2026-10-14: after "forget 4", "my lead wall grade is 6." was never served again that day. The runtime
writes the day into every delivery (the "Today is 2026-10-14" header, each record's "(recorded: 2026-10-14)"), and
the forget gates matched the forgotten "4" inside those stamps, so every delivery read as carrying it. Built from
whatever today is, so the forgotten digit is always one the runtime's own stamps carry.
"""
from __future__ import annotations

from datetime import date

from tests.lifecycle.test_forget_sibling_salvage_20260929 import _capsule, fresh_profile  # fixture


def test_forgetting_a_digit_of_today_keeps_the_sibling_value(fresh_profile):
    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    today = date.today().isoformat()
    forgotten = today[-1]  # in the header and in every stamp written today
    kept = next(d for d in "9876543210" if d not in today)
    home, chat = fresh_profile, "salv-today"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    append_conversation_event(
        session_id=chat,
        user_input=f"My novice lane grade is {forgotten} and my lead wall grade is {kept}.",
        assistant_output="Noted.",
        source_context={"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": home},
        access_policy=policy)
    assert cr.forget_session_memory(chat, forgotten, access_policy=policy,
                                    source_context={"chat_id": chat, "runtime_home": home}) == 1

    capsule = _capsule(home, chat, "What grades do I still have on file?")
    assert f"lead wall grade is {kept}" in capsule, capsule
    assert "novice" not in capsule.lower(), capsule
    # The day stamps are still delivered; only the user's own words are held to the forget.
    assert today in capsule, capsule
