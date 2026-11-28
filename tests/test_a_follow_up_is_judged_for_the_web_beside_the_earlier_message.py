"""The web question reads a follow-up beside the chat's earlier message.

iMac gate33 on f54729d9b (qwen3:8b, Web lookups off): after a question about the world's deepest lake, "which country
is it in?" read alone was judged to need the internet, and the reply was "Web lookups are off". Read beside the earlier
message it is general knowledge. iMac probe g33: 13 of 14 right with the earlier message, 11 of 14 alone, every live
ask (weather, scores, opening hours, timetables) still yes. Wordings are fresh.
"""
from __future__ import annotations

from core import intent_arbiter, online_asks


def test_the_prompt_carries_the_earlier_message_only_when_there_is_one() -> None:
    with_one = intent_arbiter._web_ask_prompt("and how long is it?", "which is the longest river in Asia?")
    assert 'Earlier: "which is the longest river in Asia?"' in with_one
    assert with_one.rstrip().split("\n")[-2] == 'Message: "and how long is it?" ->'
    alone = intent_arbiter._web_ask_prompt("and how long is it?")
    assert "Earlier:" not in alone and "points back" not in alone


def test_the_web_verdict_asks_with_the_persons_earlier_message(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(intent_arbiter, "_turn_key", lambda context: "")
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask",
                        lambda message, previous="", **_k: seen.append(previous) or "no")
    monkeypatch.setattr(online_asks, "_judged_about_the_persons_own", lambda text: False)
    context = {"conversation_history": [
        {"role": "user", "content": "kuris kalnas aukščiausias Alpėse?"},
        {"role": "assistant", "content": "Monblanas."},
        {"role": "user", "content": "kiek jis aukštas?"},
    ]}
    assert online_asks.web_ask_verdict("kiek jis aukštas?", source_context=context, session_id="chat-alps") == "no"
    assert seen == ["kuris kalnas aukščiausias Alpėse?"]


def test_a_first_message_is_asked_alone(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(intent_arbiter, "_turn_key", lambda context: "")
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask",
                        lambda message, previous="", **_k: seen.append(previous) or "yes")
    monkeypatch.setattr(online_asks, "_judged_about_the_persons_own", lambda text: False)
    monkeypatch.setattr(online_asks, "_asks_the_clock", lambda *_a, **_k: False)
    assert online_asks.web_ask_verdict("search online for ferry times to Bornholm", source_context={},
                                       session_id="chat-ferry") == "yes"
    assert seen == [""]


def test_the_web_question_reads_the_persons_own_files_as_no_web_page_s():
    """Benchmark on 21d4d4046 (qwen3:8b): a question about a scanned PDF in the home folder was answered "Web lookups
    are off". Probe g58 on the Mac: with this line 10 of 10 file questions read no, and 10 of 10 web questions yes."""
    prompt = intent_arbiter._web_ask_prompt("x")
    assert "their own files, folders, documents, notes, photos or scans on this computer" in prompt
