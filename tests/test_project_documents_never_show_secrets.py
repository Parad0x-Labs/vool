"""Project document asks never show a secret, and still show ordinary lists (v0.8, 2026-11-12).

Measured on the v0.8 candidate in served project chats: "read my ledger backup file" printed a
12-word recovery phrase and "what's on my passwords list?" printed the passwords. Those two are
pinned first, then new wordings, file names and layouts: recovery phrases numbered, in columns,
hyphen-joined, in capitals, after a label; API keys, password fields and tables, config files and
wallet notes. Ordinary lists of twelve and more items are still shown, because the phrase check is
the wallet's own word list and not the list shape. Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

import core.agent_runtime.project_documents as lane

SEED = ["orbit", "velvet", "canyon", "ladder", "pepper", "island",
        "frost", "humble", "rocket", "maple", "quiz", "anchor"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("core.context_namespace.authoritative_chat_workspace", lambda _sid: (str(tmp_path), "project"))
    return tmp_path


def _ask(project, files, question):
    for name, text in files.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text(text, encoding="utf-8")
    return lane.project_document_answer(question, session_id="s") or ""


def test_the_measured_ledger_backup_phrase_is_not_shown(project):
    answer = _ask(project, {"ledger_backup.txt": " ".join(SEED) + "\n",
                            "notes.md": "Boiler service is booked for 4 March.\n"}, "read my ledger backup file")
    assert "ledger_backup.txt" in answer and "canyon" not in answer.lower(), answer


def test_the_measured_passwords_list_is_not_shown(project):
    answer = _ask(project, {"passwords_list.txt": "gmail Hunter2xq81\nbank Zz-81kk\n"}, "what's on my passwords list?")
    assert "passwords_list.txt" in answer, answer
    assert "Hunter2xq81" not in answer and "Zz-81kk" not in answer, answer


@pytest.mark.parametrize("layout", [
    "\n".join(f"{i}. {word}" for i, word in enumerate(SEED, 1)),
    "\n".join(f"{i + 1}. {SEED[i]}    {i + 7}. {SEED[i + 6]}" for i in range(6)),
    "-".join(SEED),
    " ".join(word.upper() for word in SEED),
    "Words to keep safe\n" + ", ".join(SEED),
    "Old phone: " + " ".join(SEED) + "\nPicked up the new case on Monday.",
])
@pytest.mark.parametrize("name,question", [
    ("old-phone-backup.txt", "show me my old phone backup file"),
    ("crypto_notes.md", "what's in my crypto notes?"),
])
def test_a_recovery_phrase_in_any_layout_is_not_shown(project, layout, name, question):
    answer = _ask(project, {name: layout + "\n"}, question)
    assert name in answer, answer
    assert "canyon" not in answer.lower() and "velvet" not in answer.lower(), answer


@pytest.mark.parametrize("files,question,secret", [
    ({"api_keys.txt": "openai sk-proj-Qx81Zt0000000000000000000000\n"}, "open my api keys file", "Qx81Zt"),
    ({"config.json": '{"region": "eu", "api_key": "live_9f8e7d6c5b4a3f2e1d0c"}\n'}, "show me the config file",
     "9f8e7d6c5b4a3f2e1d0c"),
    ({"server-settings.txt": "HOST=example.internal\nDB_PASSWORD=Gl4cier-Pump-77\n"}, "read my server settings file",
     "Gl4cier-Pump-77"),
    ({"accounts.csv": "site,user,password\nbank,amy,Zz-81kk\nmail,amy,Hunter2xq81\n"}, "what's on my accounts list?",
     "Hunter2xq81"),
    ({"wallet_notes.md": "Main wallet: the words live in the safe.\n" + " ".join(SEED) + "\n"}, "read my wallet notes",
     "canyon"),
    ({"Passwörter.txt": "bank Zz-81kk\n"}, "show me my Passwörter file", "Zz-81kk"),
    ({"wifi-and-alarm.txt": "wifi password: Larch-Close-2291\nalarm code 4471\n"},
     "what's in my wifi and alarm notes?", "Larch-Close-2291"),
])
def test_keys_passwords_and_wallet_notes_are_not_shown(project, files, question, secret):
    answer = _ask(project, files, question)
    assert next(iter(files)) in answer, answer
    assert secret not in answer, answer


def test_which_files_never_finds_a_word_inside_a_withheld_file(project):
    answer = _ask(project, {"ledger_backup.txt": " ".join(SEED) + "\n", "trip.md": "Grand Canyon in May.\n"},
                  "which of my files mentions canyon?")
    assert "trip.md" in answer and "ledger_backup.txt" not in answer, answer


def test_where_did_i_write_a_password_never_quotes_it(project):
    answer = _ask(project, {"passwords_list.txt": "gmail Hunter2xq81\n",
                            "todo.md": "Change the gmail password next week.\n"},
                  "where did i write down the gmail password")
    assert "Hunter2xq81" not in answer, answer


@pytest.mark.parametrize("name,text,question,shown", [
    ("groceries.md", "- oats\n- lentils\n- milk\n- eggs\n- bread\n- apples\n- rice\n- beans\n- cheese\n- onions\n"
     "- garlic\n- butter\n- tomatoes\n", "what's on my groceries list?", "lentils"),
    ("packing-list.md", "# Packing\n- tent\n- headlamp\n- water filter\n- sleeping bag\n- stove\n- matches\n"
     "- rain jacket\n- socks\n- gloves\n- map\n- compass\n- snacks\n", "what's on my packing list?", "headlamp"),
    ("party-guests.txt", "Anna, Mark, Lisa, Tom, Jane, Paul, Sara, Mike, Eva, Nina, Otto, Ruth\n",
     "read me my party guests list", "Otto"),
    ("fruit-order.txt", "apple banana orange lemon mango grape melon peach cherry lime kiwi plum\n",
     "what's in my fruit order file?", "kiwi"),
    ("home-tasks.md", "Change the wifi password next week.\nBook the boiler service.\n",
     "what's on my home tasks list?", "wifi password"),
])
def test_ordinary_lists_are_still_shown(project, name, text, question, shown):
    answer = _ask(project, {name: text}, question)
    assert name in answer and shown in answer, answer
