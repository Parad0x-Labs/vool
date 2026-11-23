"""Sending with Drop from chat: one tool, one file, one person, and words that never say "sent" when nothing was.

Drop audit 2026-11-23, finding 4: chat spotted a Drop ask only by English words, "my other VOOL" was read as lending,
and nothing stopped the model from saying a file was sent. The turn's judge (core.plugin_ask) now carries a Drop choice
for any language, and ``machine.send_with_drop`` is the only way a turn reaches Drop. It asks the owner first in every
mode, runs only for the owner at this computer, and each refusal says that nothing was sent. The judge is scripted here
(no model); real-model wording is measured on the packaged app, not in this file.
"""
from __future__ import annotations

import json
import os
import random

import pytest

from core import intent_arbiter, plugin_ask
from core import request_trust as rt
from core.capability_graph import model_visible_specs
from core.drop import chat_door, chat_tool
from core.drop.service import MAX_FILE, DropHome
from tests._credential_intelligence_support import isolated_home

OWNER = {rt.OWNER_LOCAL_KEY: True, "surface": "desktop", "platform": "api", "session_id": "chat-drop"}
KEYS = [key for key, _description, _seats in plugin_ask.BUILTIN_CHOICES]
FOREIGN_ASKS = ("Nusiųsk sutartį {n} Jonui per Drop", "アンナに見積書{n}.pdfを送って", "Envoie le fichier plan{n}.pdf à Luc sur son VOOL",
                "اِبعث ملف العقد {n} إلى سارة", "Перешли Оле файл отчёт{n}.docx на её компьютер")


@pytest.fixture
def desk(isolated_home, tmp_path, monkeypatch):
    """This computer (the running profile, Ana) and Ben's VOOL, sharing one folder. Drop is on."""
    from core.runtime_paths import active_config_home_dir

    isolated_home.chmod(0o700)  # Drop refuses a VOOL folder other users can read
    monkeypatch.setenv("VOOL_DROP", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    downloads = tmp_path / "user" / "Downloads"
    downloads.mkdir(parents=True)
    relay = tmp_path / "relay"
    relay.mkdir(mode=0o755)
    config = active_config_home_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / "drop.json").write_text(json.dumps({"enabled": True, "relay": str(relay), "allow_digest": False}))
    plugin_ask.forget()
    with DropHome(tmp_path / "ben", relay) as ben:
        ben.set_name("Ben")
        yield {"downloads": downloads, "ben": ben, "relay": relay, "tmp": tmp_path}
    plugin_ask.forget()


def _me():
    from core.web.api.drop_api import _home

    return _home()


def _friends(ben, name="Ben"):
    with _me() as me:
        me.set_name("Ana")
        me.contact_add(ben.me()["card"], name)
        ben.contact_add(me.me()["card"])
        ben.poll()
        me.poll()


def _send(arguments, context=None):
    from core.runtime_execution_tools import _machine_send_with_drop

    return _machine_send_with_drop(arguments, source_context=dict(OWNER if context is None else context))


def test_a_file_asked_for_in_chat_reaches_the_person_and_the_words_say_what_happened(desk):
    _friends(desk["ben"])
    data = os.urandom(90000)
    name = f"plans-{random.randint(100, 999)}.pdf"
    (desk["downloads"] / name).write_bytes(data)
    result = _send({"path": f"~/Downloads/{name}", "to": "ben", "note": "for Friday"})
    assert result.ok and result.status == "executed", result.response_text
    assert name in result.response_text and "Ben" in result.response_text and "Sent" in result.response_text
    # Not verified yet is said every time, so a first send is never mistaken for a checked one.
    assert "not verified" in result.response_text
    assert data[:48] not in b"".join(p.read_bytes() for p in desk["relay"].rglob("*") if p.is_file())
    desk["ben"].poll()
    (item,) = desk["ben"].inbox()
    assert item["name"] == name and item["person"]["name"] == "Ana"
    assert desk["ben"].file_bytes(item["sid"]) == (name, data)
    assert desk["ben"].show(item["sid"])["note"] == "for Friday"


def test_every_way_it_cannot_send_says_nothing_was_sent(desk, monkeypatch):
    ben = desk["ben"]
    (desk["downloads"] / "a.txt").write_text("a")
    # Nobody added yet: the answer says how to add a person, never "sent".
    result = _send({"path": "~/Downloads/a.txt", "to": "Ben"})
    assert not result.ok and "have not added anyone" in result.response_text and "nothing was sent" in result.response_text
    # Ana added Ben, Ben has not added Ana: nothing for the file to travel on.
    with _me() as me:
        me.set_name("Ana")
        me.contact_add(ben.me()["card"], "Ben")
    result = _send({"path": "~/Downloads/a.txt", "to": "Ben"})
    assert not result.ok and result.status == "contact_not_ready", result.status
    assert "Ben has not added your card yet" in result.response_text and "Nothing was sent" in result.response_text
    with _me() as me:
        ben.contact_add(me.me()["card"])
        ben.poll()
        me.poll()
    for arguments, status, said in (
        ({"path": "~/Downloads/missing.txt", "to": "Ben"}, "not_found", "There is no file"),
        ({"path": "/etc/hosts", "to": "Ben"}, "not_allowed", "outside the folders"),
        ({"path": "~/Downloads/a.txt", "to": "Zed"}, "which_person", "say which person"),
    ):
        result = _send(arguments)
        assert not result.ok and result.status == status and said in result.response_text, (arguments, result.response_text)
        assert "othing was sent" in result.response_text, result.response_text
    (desk["downloads"] / "big.bin").write_bytes(b"\0" * (MAX_FILE + 1))
    result = _send({"path": "~/Downloads/big.bin", "to": "Ben"})
    assert not result.ok and "larger than 10 MB" in result.response_text and "Nothing was sent" in result.response_text
    # A phone or chat-app turn never sends a file from this computer to another person.
    result = _send({"path": "~/Downloads/a.txt", "to": "Ben"}, {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False})
    assert not result.ok and result.status == "owner_only" and "Nothing was sent" in result.response_text
    # Off: refused before anything is read.
    monkeypatch.setenv("VOOL_DROP", "0")
    from core.runtime_paths import active_config_home_dir

    (active_config_home_dir() / "drop.json").write_text(json.dumps({"enabled": False, "relay": str(desk["relay"])}))
    result = _send({"path": "~/Downloads/a.txt", "to": "Ben"})
    assert not result.ok and "It is off, so nothing was sent" in result.response_text
    ben.poll()
    assert ben.inbox() == []


def test_a_name_two_people_could_answer_to_is_asked_about_never_guessed():
    people = [{"name": "Ben Carter", "tag": "river-lamp-stone", "fingerprint": "1"},
              {"name": "Ben Ortiz", "tag": "apple-moon-desk", "fingerprint": "2"},
              {"name": "Maria", "tag": "cloud-fox-ring", "fingerprint": "3"}]
    assert chat_tool.pick(people, "maria")[0]["fingerprint"] == "3"
    assert chat_tool.pick(people, "Ben Ortiz")[0]["fingerprint"] == "2"
    assert chat_tool.pick(people, "apple-moon-desk")[0]["fingerprint"] == "2"
    chosen, asked = chat_tool.pick(people, "Ben")
    assert chosen is None and {p["fingerprint"] for p in asked} == {"1", "2"}
    chosen, asked = chat_tool.pick(people, "Mariah")
    assert chosen is None and len(asked) == 3
    # No name given: one person in the list is that person, more than one is a question.
    assert chat_tool.pick(people[:1], "")[0]["fingerprint"] == "1"
    assert chat_tool.pick(people, "")[0] is None


def test_the_tool_asks_the_owner_first_in_every_mode_and_counts_as_leaving_the_computer():
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.tool_offer_assembly import is_egress_tool

    contract = runtime_tool_contract_map()["machine.send_with_drop"]
    assert contract.side_effect_class == "network_send"
    assert set(contract.permission_actions) == {"read_files", "external_messages"}
    assert contract.claim.asserts_action
    assert is_egress_tool("machine.send_with_drop")


@pytest.mark.parametrize("seed", range(5))
def test_a_drop_ask_in_another_language_gets_the_tool_or_a_plain_refusal(seed, desk, monkeypatch):
    rng = random.Random(f"drop-any-language-{seed}")
    text = FOREIGN_ASKS[seed].format(n=rng.randint(2, 9))
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", lambda _m, _c, **_k: (KEYS.index("drop") + 1,))
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "yes")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-drop")
    assert plugin_ask.judged_builtins(text) == ("drop",)
    offered = {str(spec.get("intent") or "") for spec in model_visible_specs(user_text=text, source_context=OWNER)}
    assert {"machine.send_with_drop", "machine.find_file"} <= offered, sorted(offered)

    class Agent:
        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    # On with nobody added: answered before the model, and the answer says nothing was sent.
    answer = chat_door.reply_if_unavailable(Agent(), text, session_id="chat-drop", source_context=OWNER)
    assert answer is not None and "nothing was sent" in answer["response"], answer
    # On with a person added: the door steps aside and the tool does the work.
    _friends(desk["ben"])
    assert chat_door.reply_if_unavailable(Agent(), text, session_id="chat-drop", source_context=OWNER) is None
