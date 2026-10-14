"""A message in another language keeps its words: in the transcript, and in what the model reads.

`core.input_normalizer` rewrites English shorthand, English misspellings and English tool-word typos
so the routers can read sloppy English ("pls hlp im stuck", "how mcuh disk sapce"). Its result is
also the text the turn carries everywhere after intake: the chat transcript stores it as what the
user said, and the model is sent it as the user's message. Applied to other languages those tables
were translating words: German "im Büro" became "i am Büro", "passwort" became "password",
Portuguese "documentos" became "documents", and the rejoin spaced out an Arabic shadda and every
Hindi vowel sign ("निजी" -> "न ि ज ी").

Proved here at the unit (the normalizer and the input adapter), and over the real /api/chat door on
one isolated daemon: the transcript row the chat page reloads holds the exact typed text, and the
user message the model was sent holds it too. English shorthand and tool typos keep their
corrections, and a typo'd machine question still reaches its tool without a model call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

import core.input_normalizer as input_normalizer
from core.input_normalizer import normalize_user_text, reads_as_other_language
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _history, _Provider

REPO = Path(__file__).resolve().parents[1]

#: (UI locale cookie or None, typed text, the fragments the English tables used to make of it).
#: The cookie is what the chat page sends; the message language is read from the text itself.
OTHER_LANGUAGE_TURNS: list[tuple[str | None, str, tuple[str, ...]]] = [
    ("de", "ich bin morgen im Büro, erinnere mich bitte daran", ("i am Büro",)),
    ("de", "speichere mein passwort nie im browser", ("mein password", "i am browser")),
    ("de", "speichere die datei im ordner dokumente", ("i am ordner",)),
    ("es", "guarda este archivo en mis documentos", ("mis documents",)),
    ("es", "¿podemos mover la reunión al viernes?", ("¿ podemos",)),
    ("pt", "guarda este ficheiro nos documentos", ("nos documents",)),
    ("ar", "سجّل الاجتماع غدًا", ("سج ّ ل",)),
    ("hi", "निजी चैट कैसे काम करती है?", ("न ि ज ी",)),
    (None, "Lena ist im Büro und u bist zu Hause", ("i am Büro", "you bist")),
]

ENGLISH_CORRECTIONS = [
    ("pls hlp im stuck", "please help i am stuck"),
    ("how mcuh disk sapce do i havee left", "how much disk space do i have left"),
    ("wich apps are usign the most memroy", "which apps are using the most memory"),
    ("chekc the workspce please", "check the workspace please"),
    ("what does привет mean pls", "what does привет mean please"),
]


# ---------------------------------------------------------------- the normalizer itself


@pytest.mark.parametrize("_locale,typed,corrupted", OTHER_LANGUAGE_TURNS)
def test_a_message_in_another_language_keeps_every_word(_locale, typed, corrupted) -> None:
    result = normalize_user_text(typed)
    assert result.normalized_text == typed, (typed, result.normalized_text, result.replacements)
    assert result.replacements == {}
    for bad in corrupted:
        assert bad not in result.normalized_text


@pytest.mark.parametrize(
    "typed",
    [
        "Was machst du heute im Garten?",
        "hast du die Datei im Ordner gesehen",
        "por favor guarda la contraseña en el navegador",
        "je veux enregistrer la réunion de demain",
        "zapisz ten plik w dokumentach, prosze",
        "neišsaugok slaptažodžio naršyklėje",
        "bu dosyayı belgeler klasörüne kaydet",
        "đừng lưu mật khẩu vào trình duyệt",
        "dung luu chuyen nay nhe",
        "не сохраняй пароль в браузере",
        "このファイルを書類フォルダに保存して",
        "이 파일을 문서 폴더에 저장해줘",
        "תשמור את הקובץ בתיקיית מסמכים",
        "把这个文件保存到文档文件夹",
    ],
)
def test_a_distinct_family_of_other_languages_is_left_as_typed(typed) -> None:
    assert normalize_user_text(typed).normalized_text == typed


@pytest.mark.parametrize("typed,expected", ENGLISH_CORRECTIONS)
def test_english_shorthand_and_typos_are_still_corrected(typed, expected) -> None:
    assert not reads_as_other_language(typed), typed
    assert normalize_user_text(typed).normalized_text == expected


@pytest.mark.parametrize(
    "typed",
    [
        "what is the weather in Bogotá",
        "find a café near me",
        "the weather in São Paulo please",
        "im gonna be late, pls tell the team",
        "the process will die and im stuck",
        "book a hotel in Düsseldorf pls",
        "im lost, pls show the dir",
        "whats on the todo list, im late",
        "call at 5 est pls, im busy",
    ],
)
def test_an_english_message_naming_a_foreign_word_stays_english(typed) -> None:
    assert not reads_as_other_language(typed), typed


def test_one_piece_of_evidence_is_not_enough() -> None:
    """German "im" is also English shorthand, and one accented name is not a language."""
    assert not reads_as_other_language("im stuck")
    assert not reads_as_other_language("Zürich pls")
    assert reads_as_other_language("im Zürich")


def test_a_message_with_no_letters_is_not_another_language() -> None:
    assert not reads_as_other_language("")
    assert not reads_as_other_language("123 + 456 = ?")


def test_the_users_own_lexicon_still_applies() -> None:
    """A mapping the user taught is the user's words, not an English assumption."""
    result = normalize_user_text("schick es mir per tgx bitte", session_lexicon={"tgx": "telegram"})
    assert result.normalized_text == "schick es mir per telegram bitte"


def test_sabotage_without_the_language_gate_the_words_are_rewritten(monkeypatch) -> None:
    """The gate is load-bearing: turned off, the reported corruption comes straight back."""
    monkeypatch.setattr(input_normalizer, "reads_as_other_language", lambda _text: False)
    assert normalize_user_text("speichere mein passwort nie im browser").normalized_text == (
        "speichere mein password nie i am browser"
    )
    assert normalize_user_text("guarda este archivo en mis documentos").normalized_text == (
        "guarda este archivo en mis documents"
    )
    assert normalize_user_text("निजी चैट").normalized_text == "न ि ज ी च ै ट"


def test_sabotage_a_lossy_tokenizer_is_still_visible_on_another_language(monkeypatch) -> None:
    """Keeping the typed spacing must never hide a lost character: an ASCII-only tokenizer still
    shows its damage on a Cyrillic message instead of being papered over with the typed text."""
    import re

    monkeypatch.setattr(input_normalizer, "_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+|[^\w\s]"))
    monkeypatch.setattr(input_normalizer, "_WORD_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+"))
    assert normalize_user_text("привет, как дела?").normalized_text == ",?"


def test_the_input_adapter_hands_the_turn_the_typed_words() -> None:
    """The adapter hands the turn its working text (`effective_input` in the agent)."""
    from core.human_input_adapter import adapt_user_input

    german = adapt_user_input("speichere mein passwort nie im browser", session_id="normi18n-unit-de", persist=False)
    assert german.normalized_text == "speichere mein passwort nie im browser"
    english = adapt_user_input("pls hlp im stuck", session_id="normi18n-unit-en", persist=False)
    assert english.normalized_text == "please help i am stuck"


# ---------------------------------------------------------------- the real /api/chat door


def _stream(daemon: ServedDaemon, text: str, chat: str, locale: str | None) -> tuple[str, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if locale is not None:
        headers["Cookie"] = f"vool_ui_locale={locale}"
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": chat, "model": MODEL}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers=headers, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    return str(commit.get("canonical_content") or answer).strip(), commit


class _RecordingProvider(_Provider):
    """Keeps the user messages exactly as the runtime sent them to the model."""

    def __init__(self, table: dict[str, Any]) -> None:
        super().__init__(table)
        self.user_messages: list[str] = []

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        for message in body.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "user":
                self.user_messages.append(str(message.get("content") or ""))
        return super().reply(model, body)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("normi18n") / "home"
    provider = _RecordingProvider({MODEL: "Noted."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            yield daemon, provider


def _user_rows(daemon: ServedDaemon, chat: str) -> list[str]:
    return [str(m.get("content") or "") for m in _history(daemon, chat).get("messages", []) if m.get("role") == "user"]


def test_the_transcript_and_the_model_keep_the_words_as_typed(served) -> None:
    daemon, provider = served
    chat = "openclaw:" + "a7" * 10
    for index, (locale, typed, corrupted) in enumerate(OTHER_LANGUAGE_TURNS):
        before = len(provider.user_messages)
        _stream(daemon, typed, chat, locale)
        rows = _user_rows(daemon, chat)
        assert len(rows) == index + 1, (typed, rows)
        assert rows[-1] == typed, (locale, typed, rows[-1])
        sent = provider.user_messages[before:]
        assert sent, f"the model was never asked about {typed!r}"
        assert any(typed in message for message in sent), (typed, sent)
        for bad in corrupted:
            assert all(bad not in message for message in sent), (typed, bad, sent)


def test_english_shorthand_still_reaches_the_model_corrected(served) -> None:
    daemon, provider = served
    chat = "openclaw:" + "b8" * 10
    before = len(provider.user_messages)
    _stream(daemon, "pls hlp im stuck", chat, "en")
    sent = provider.user_messages[before:]
    assert any("please help i am stuck" in message for message in sent), sent
    assert _user_rows(daemon, chat) == ["please help i am stuck"]


def test_a_typoed_machine_question_still_reaches_its_tool(served) -> None:
    """Routing on corrected English is unchanged: the disk tool answers, with no model call,
    on a German page as on an English one (the page language is not the message language)."""
    daemon, provider = served
    for locale, chat in (("en", "openclaw:" + "c9" * 10), ("de", "openclaw:" + "d0" * 10)):
        before = len(provider.calls)
        answer, _commit = _stream(daemon, "how mcuh disk sapce do i havee left", chat, locale)
        assert len(provider.calls) == before, (locale, provider.calls[before:])
        assert "free" in answer.lower() or "available" in answer.lower(), (locale, answer)
