"""The three safe folders are the same three in every app language, and on every disk.

Before: every lane spelled Desktop/Downloads/Documents as English words of its own. A folder ask in
German, Polish or Chinese named no folder, so the executor's list-directory admission refused the
model's `machine.list_directory` call; the card match read German function words ("welche ...
sind") as content and picked `find_largest`; and on a Linux box set up in German the real folder
(`~/Schreibtisch`, from xdg-user-dirs) was not one of the roots. One table now
(`core.safe_folder_names`) feeds the roots, the folder argument, the admission proof, the live-source
seat, the card match and the English-grammar lanes. The roots never grow.

These phrasings are this file's own, written for it; none is a measuring ask.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import safe_folder_names as names
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-folders", "turn_id": "t-folders", "action_policy": "allowed"}
REMOTE = {**OWNER, "_owner_local": False}

# (ask, root it names, the word as said) -- one or more per app language other than English.
ASKS = (
    ("Zeig mir bitte die Dateien auf dem Schreibtisch.", "desktop", "Schreibtisch"),
    ("Was habe ich im Download-Ordner gespeichert?", "downloads", "Download-Ordner"),
    ("Liste die Dateien in meinen Dokumenten auf.", "documents", "Dokumenten"),
    ("¿Qué archivos tengo en el escritorio?", "desktop", "escritorio"),
    ("muéstrame las carpetas de Descargas", "downloads", "Descargas"),
    ("¿Hay algún PDF en mis documentos? Busca en Documentos", "documents", "documentos"),
    ("Quels fichiers sont sur mon bureau ?", "desktop", "bureau"),
    ("Montre-moi le contenu du dossier Téléchargements", "downloads", "Téléchargements"),
    ("Que tenho na Área de Trabalho? Mostra os arquivos", "desktop", "Área de Trabalho"),
    ("Mostra-me os ficheiros nas Transferências", "downloads", "Transferências"),
    ("Jakie pliki mam na pulpicie?", "desktop", "pulpicie"),
    ("Pokaż pliki w folderze Pobrane", "downloads", "Pobrane"),
    ("Kokie failai yra darbalaukyje?", "desktop", "darbalaukyje"),
    ("Parodyk failus atsisiuntimuose", "downloads", "atsisiuntimuose"),
    ("Masaüstümde hangi dosyalar var?", "desktop", "Masaüstümde"),
    ("İndirilenler klasöründe ne var?", "downloads", "İndirilenler"),
    ("Trên màn hình nền có những tệp nào?", "desktop", "màn hình nền"),
    ("Какие файлы лежат на рабочем столе?", "desktop", "рабочем столе"),
    ("Покажи файлы в папке Загрузки", "downloads", "Загрузки"),
    ("Які файли є на робочому столі?", "desktop", "робочому столі"),
    ("Покажи файли в теці Документи", "documents", "Документи"),
    ("ما الملفات الموجودة على سطح المكتب؟", "desktop", "سطح المكتب"),
    ("אילו קבצים יש בהורדות?", "downloads", "בהורדות"),
    ("मेरे डेस्कटॉप पर कौन सी फ़ाइलें हैं?", "desktop", "डेस्कटॉप"),
    ("デスクトップにあるファイルを見せて", "desktop", "デスクトップ"),
    ("書類フォルダの中身は?", "documents", "書類"),
    ("바탕화면에 어떤 파일이 있어?", "desktop", "바탕화면"),
    ("下载文件夹里有哪些文件？", "downloads", "下载"),
    ("我的桌面上有什么文件", "desktop", "桌面"),
)

# Must not change: an ask that is not about one of the folders, or an English ask the existing
# lanes already serve exactly as before.
NOT_A_FOLDER_ASK = (
    "Ich habe einen neuen Schreibtisch gekauft.",  # a desk, said as a statement
    "pull the report from the credit bureau",  # "bureau" is an English word too
    "the preacher stood at the pulpit",
    "what is the capital of France",
    "Wie spät ist es in Tokio?",
)


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _list(path: str, context: dict):
    return execute_tool_intent(
        {"intent": "machine.list_directory", "arguments": {"path": path}},
        task_id="t-folders",
        session_id="s-folders",
        source_context=context,
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


# -- the table -----------------------------------------------------------------------------------


def test_every_app_language_names_all_three_folders() -> None:
    catalogs = {p.stem for p in Path("core/i18n/catalogs").glob("*.json")}
    assert set(names.LANGUAGES) == catalogs, "the table speaks exactly the app's 17 UI languages"
    for key in names.ROOT_KEYS:
        assert names.root_key_for_alias(names.ENGLISH_DIR_NAMES[key]) == key


@pytest.mark.parametrize(("text", "key", "said"), ASKS)
def test_the_folder_word_is_found_and_kept_as_said(text: str, key: str, said: str) -> None:
    mentions = names.root_mentions(text)
    assert mentions, f"no folder found in {text!r}"
    assert mentions[0].key == key
    assert mentions[0].said == said


@pytest.mark.parametrize("text", NOT_A_FOLDER_ASK)
def test_a_sentence_that_is_not_about_the_folder_does_not_ask_about_it(text: str) -> None:
    assert names.asks_about_a_root(text) is False


# -- where the folders are on disk ---------------------------------------------------------------


def _xdg(home: Path, body: str) -> None:
    (home / ".config").mkdir(exist_ok=True)
    (home / ".config" / "user-dirs.dirs").write_text(body, encoding="utf-8")


def test_linux_reads_the_localized_folders_from_user_dirs(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(names, "_platform", lambda: "linux")
    _xdg(home, '# written by xdg-user-dirs-update\nXDG_DESKTOP_DIR="$HOME/Schreibtisch"\n'
               'XDG_DOWNLOAD_DIR="$HOME/Downloads"\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n')
    assert names.safe_root_paths(home) == (
        (home / "Schreibtisch").resolve(), (home / "Downloads").resolve(), (home / "Dokumente").resolve(),
    )


def test_macos_keeps_the_english_disk_folders_even_with_a_user_dirs_file(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(names, "_platform", lambda: "darwin")
    _xdg(home, 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
    assert names.safe_root_paths(home) == tuple((home / n).resolve() for n in ("Desktop", "Downloads", "Documents"))


def test_linux_without_user_dirs_falls_back_to_the_english_names(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(names, "_platform", lambda: "linux")
    assert names.safe_root_paths(home) == tuple((home / n).resolve() for n in ("Desktop", "Downloads", "Documents"))


@pytest.mark.parametrize(
    "entry",
    (
        'XDG_DESKTOP_DIR="$HOME"',  # the home folder itself (xdg's own "disabled" value)
        'XDG_DESKTOP_DIR="$HOME/"',
        'XDG_DESKTOP_DIR="/etc"',  # outside home
        'XDG_DESKTOP_DIR="$HOME/.ssh"',  # a hidden folder
        'XDG_DESKTOP_DIR="$HOME/../other"',  # out of home by ..
        'XDG_DESKTOP_DIR="Desktop"',  # relative: not valid in user-dirs.dirs
        'XDG_DESKTOP_DIR="$HOME/link-out"',  # a symlink pointing out of home
    ),
)
def test_a_user_dirs_entry_that_would_widen_the_roots_is_ignored(entry: str, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "link-out").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(names, "_platform", lambda: "linux")
    _xdg(home, entry + "\n")
    roots = names.safe_root_paths(home)
    assert roots[0] == (home / "Desktop").resolve()
    assert len(roots) == 3
    for root in roots:
        assert root != home.resolve() and home.resolve() in root.parents


# -- the executor: the folder argument, the reply, the fence --------------------------------------


@pytest.mark.parametrize(("argument", "disk"), (("Schreibtisch", "Desktop"), ("~/Pobrane", "Downloads"),
                                                 ("mis Documentos", "Documents"), ("桌面", "Desktop")))
def test_a_spoken_folder_argument_lists_that_root_and_names_it_as_said(argument, disk, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / disk).mkdir()
    (home / disk / "rechnung-2026.pdf").write_bytes(b"%PDF-1.4\n")
    execution = _list(argument, {**OWNER, "_semantic_machine_list_directory_admitted": True})
    assert execution.ok and execution.status == "executed", execution.response_text
    assert "rechnung-2026.pdf" in execution.response_text
    said = names.root_mentions(argument)[0].said
    assert f"`{said}`" in execution.response_text
    assert str(home) not in execution.response_text, "a reply never prints a full path"


def test_on_a_german_linux_desktop_the_xdg_folder_is_the_one_listed(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(names, "_platform", lambda: "linux")
    _xdg(home, 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
    (home / "Schreibtisch").mkdir()
    (home / "Schreibtisch" / "urlaub.txt").write_text("x", encoding="utf-8")
    (home / "Desktop").mkdir()
    (home / "Desktop" / "not-the-desktop.txt").write_text("x", encoding="utf-8")
    for argument in ("Schreibtisch", "~/Schreibtisch", "~/Desktop", "desktop"):
        execution = _list(argument, {**OWNER, "_semantic_machine_list_directory_admitted": True})
        assert execution.ok, (argument, execution.response_text)
        assert "urlaub.txt" in execution.response_text and "not-the-desktop" not in execution.response_text


def test_english_folder_arguments_answer_exactly_as_before(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "Downloads").mkdir()
    (home / "Downloads" / "a.txt").write_text("x", encoding="utf-8")
    execution = _list("~/Downloads", {**OWNER, "_semantic_machine_list_directory_admitted": True})
    assert execution.response_text.startswith("Visible entries under `~/Downloads`:")


@pytest.mark.parametrize("argument", ("~/Bilder", "~/.ssh", "/etc", "~", "Schreibtisch/../../.."))
def test_the_roots_are_not_widened_by_a_spoken_name(argument: str, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "Bilder").mkdir()
    (home / ".ssh").mkdir()
    execution = _list(argument, {**OWNER, "_semantic_machine_list_directory_admitted": True})
    assert not execution.ok


def test_a_remote_turn_is_refused_the_listing_whatever_language_it_names_the_folder_in(tmp_path, monkeypatch) -> None:
    from core.agent_runtime.agent import _owner_folder_demand

    home = _home(tmp_path, monkeypatch)
    (home / "Desktop").mkdir()
    (home / "Desktop" / "geheim.txt").write_text("x", encoding="utf-8")
    for text, _key, _said in ASKS:
        assert _owner_folder_demand(text, REMOTE) is False
    execution = _list("Schreibtisch", REMOTE)
    assert not execution.ok and "geheim.txt" not in execution.response_text


# -- admission proof, live-source seat, card match -----------------------------------------------


@pytest.mark.parametrize(("text", "_key", "_said"), ASKS)
def test_the_admission_proof_accepts_the_owners_folder_ask_in_any_language(text, _key, _said) -> None:
    from core.agent_runtime.agent import _owner_folder_demand

    assert _owner_folder_demand(text, OWNER) is True


@pytest.mark.parametrize("text", NOT_A_FOLDER_ASK)
def test_the_admission_proof_still_refuses_a_sentence_that_is_not_a_folder_ask(text: str) -> None:
    from core.agent_runtime.agent import _owner_folder_demand

    assert _owner_folder_demand(text, OWNER) is False
    execution = _list("~/Desktop", {**OWNER, "_semantic_machine_list_directory_admitted": False})
    assert not execution.ok


@pytest.mark.parametrize(("text", "_key", "_said"), [a for a in ASKS if names.asks_about_a_root(a[0], need_noun=True)])
def test_a_folder_ask_naming_its_files_seats_the_listing(text, _key, _said) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_offer_assembly import assemble_tool_offer

    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER))
    offer = assemble_tool_offer(task_class="chat_conversation", toolset_hints=requirements.allowed_toolsets,
                                user_text=text, source_context=dict(OWNER))
    assert "machine.list_directory" in offer.intents


def test_most_folder_asks_name_their_files() -> None:
    assert sum(names.asks_about_a_root(a[0], need_noun=True) for a in ASKS) >= 25


@pytest.mark.parametrize("text", NOT_A_FOLDER_ASK)
def test_a_sentence_that_is_not_a_folder_ask_is_not_a_files_source(text: str) -> None:
    from core.tool_demand_signals import owner_live_sources

    assert "files" not in owner_live_sources(text)


@pytest.mark.parametrize(
    "text",
    (
        "Welche Dateien sind auf dem Schreibtisch",
        "Welche Dateien sind in meinen Dokumenten",
        "Welche Ordner sind im Download-Ordner",
    ),
)
def test_a_german_listing_ask_no_longer_matches_find_largest_on_its_function_words(text: str) -> None:
    from core.tool_card_router import match_cards

    assert "machine.find_largest" not in {m.intent for m in match_cards(text, semantic="off")}


@pytest.mark.parametrize(
    ("text", "intent"),
    (
        ("welche Dateien sind die größten auf dem Rechner", "machine.find_largest"),
        ("qué archivos ocupan más espacio en mi disco", "machine.find_largest"),
        ("list the files on my desktop", "machine.list_directory"),
        ("montre-moi les fichiers sur mon bureau", "machine.list_directory"),
    ),
)
def test_card_controls_still_match_their_own_card(text: str, intent: str) -> None:
    from core.tool_card_router import match_cards

    assert match_cards(text, semantic="off")[0].intent == intent


def test_a_localized_folder_word_meets_the_listing_cards_desktop_examples() -> None:
    from core.tool_card_router import _stems

    assert "desktop" in _stems("Pokaż pliki na pulpicie")
    assert "desktop" in _stems("桌面上的文件")


# -- the whole turn ------------------------------------------------------------------------------


class _Router:
    """Round 1 calls the listing with the folder as the person said it; round 2 answers."""

    def __init__(self, path: str):
        self.path = path
        self.rounds = 0

    def resolve_tool_intent(self, **_kwargs):
        from core.memory_first_router import ModelExecutionDecision

        self.rounds += 1
        intent, arguments = (
            ("machine.list_directory", {"path": self.path}) if self.rounds == 1
            else ("respond.direct", {"message": "Hier ist, was ich gefunden habe."})
        )
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": dict(arguments)},
            tool_calls=(SimpleNamespace(intent=intent, arguments=dict(arguments), call_id=f"c-{intent}", name=intent),),
            validation_state="validated", provider_id="p",
        )


def _drive(text: str, router: _Router, context: dict, workspace: Path):
    import uuid

    from apps.vool_agent import VoolAgent

    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    return agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input=text,
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=session,
        source_context={**context, "session_id": session, "runtime_session_id": session,
                        "workspace": str(workspace), "workspace_root": str(workspace)},
        surface="api",
    ) or {}


@pytest.mark.parametrize("path", ("Schreibtisch", "~/Schreibtisch"))
def test_a_german_desktop_ask_runs_the_listing_the_model_called(path: str, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    (home / "Desktop").mkdir()
    (home / "Desktop" / "reise-plan.txt").write_text("x", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    router = _Router(path)
    result = _drive("Zeig mir mal, was für Dateien auf dem Schreibtisch sind", router, OWNER, workspace)
    assert "machine.list_directory" in ((result.get("details") or {}).get("tool_steps") or [])
    reply = str(result.get("response") or "")
    assert "did not establish a machine or path target" not in reply
    assert "I can only inspect safe local directories" not in reply
    assert "reise-plan.txt" in reply, "the reply stands on the real listing"
    assert "`Schreibtisch`" in reply and str(home) not in reply
