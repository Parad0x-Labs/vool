"""Private chat in VOOL's sixteen other app languages, proven over the real /api/chat door.

One isolated daemon with a scripted local model; every turn is sent the way the chat page sends it,
with the page's ``vool_ui_locale`` cookie. For each language: its canonical words turn private chat
on and off, other everyday ways of asking switch it too, asking whether it is on answers, and
ordinary sentences that only mention saving or privacy switch nothing and are saved as usual.
Replies come in the cookie's language (English with no cookie or an unknown one), commands are
read in any language whatever the page shows, and a private turn in German leaves none of its
words on disk.

Phrases are authored here per language (reviewed from a translation draft), not read from the
parser's own tables: these are the words people type.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from core.i18n.catalog import catalog_for
from core.private_chat import parse_private_command
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _files_holding, _history, _Provider

REPO = Path(__file__).resolve().parents[1]
SECRET = "Wexlorind"  # authored marker for the German private turn; appears nowhere else in the repo

#: locale -> on/off canonicals (the page's own words), three more ways to turn it on, a status
#: question, a "back to normal" that only ends it while private, and ten sentences that are NOT
#: commands (they mention saving, recording or privacy) and must be saved as usual.
CASES: dict[str, dict[str, Any]] = {
    "ar": {
        "on": "محادثة خاصة", "off": "أعد تشغيل التسجيل",
        "more_on": ["لا تحفظ هذا", "هذا بيني وبينك", "بدون تسجيل"],
        "status": "هل تحفظ المحادثة؟", "back": "الوضع العادي",
        "not": ["احفظ هذا الملف في مجلد المستندات", "لا تحفظ كلمة المرور في المتصفح", "سجّل الاجتماع غدًا",
                "كيف تعمل المحادثة الخاصة؟", "هاتفي لا يسجل الفيديو", "شركتي تحفظ كل المكالمات", "احفظ لي رقم هاتفه",
                "هل يمكنك تسجيل مقطع صوتي؟", "حُذف تسجيل الأمس", "المحادثات الخاصة مشفرة؟"],
    },
    "de": {
        "on": "Privater Chat", "off": "Aufzeichnung wieder einschalten",
        "more_on": ["speicher das nicht", "lass uns privat reden", "das bleibt unter uns"],
        "status": "wird das hier gespeichert?", "back": "normaler Modus",
        "not": ["speichere die datei im ordner dokumente",
                "ich hab das gespräch mit meinem chef aufgezeichnet, soll ich es dir schicken?",
                "mein chef speichert alles, sogar private chats", "wie funktioniert der private chat?",
                "was ist ein privater chat?", "kannst du diese seite als pdf speichern",
                "speichere mein passwort nie im browser",
                "im inkognito-modus vom browser wird der verlauf nicht gespeichert",
                "zeichne das meeting morgen um 10 auf", "spiel die aufzeichnung von gestern nochmal ab"],
    },
    "es": {
        "on": "chat privado", "off": "volver a activar el registro",
        "more_on": ["no guardes esto", "¿podemos hablar en privado?", "esto queda entre nosotros"],
        "status": "¿estás guardando esto?", "back": "modo normal",
        "not": ["guarda este archivo en el escritorio", "no guardes la contraseña en el navegador",
                "graba el partido de esta noche", "cómo funciona el chat privado?", "mi empresa graba las llamadas, es legal?",
                "abre una ventana de incógnito en el navegador", "puedes grabar la reunión de mañana?",
                "mi hermano no guarda nada de sus chats", "qué significa modo privado?", "graba un audio y mándamelo"],
    },
    "fr": {
        "on": "discussion privée", "off": "réactiver l'enregistrement",
        "more_on": ["n'enregistre pas ça", "ça reste entre nous", "on peut parler en privé ?"],
        "status": "tu enregistres ?", "back": "mode normal",
        "not": ["enregistre la réunion de 14h", "n'enregistre pas mes mots de passe dans le navigateur",
                "garde ce fichier pour moi", "comment marche la discussion privée ?",
                "mon entreprise enregistre tous les appels", "tu peux enregistrer l'émission ce soir ?",
                "les discussions privées sont-elles chiffrées ?", "garde mon numéro",
                "mets la vidéo en mode privé sur youtube", "il enregistre tout ce que je dis"],
    },
    "he": {
        "on": "צ'אט פרטי", "off": "הפעל שוב את ההקלטה",
        "more_on": ["אל תשמור את זה", "זה נשאר בינינו", "אפשר לדבר בפרטי?"],
        "status": "השיחה הזאת נשמרת?", "back": "מצב רגיל",
        "not": ["תשמור את הקובץ בתיקיית מסמכים", "אל תשמור את הסיסמה בדפדפן", "תקליט את הפגישה מחר",
                "איך עובד הצ'אט הפרטי?", "המקליט בטלפון שלי לא עובד", "הבוס שומר הכל אצלו", "תשמור לי את המקום",
                "ההקלטה מאתמול נמחקה", "אתה יכול לשמור את התמונה הזאת?", "שומרים את השיחות בוואטסאפ?"],
    },
    "hi": {
        "on": "निजी चैट", "off": "रिकॉर्डिंग फिर से चालू करें",
        "more_on": ["इसे मत सहेजो", "यह बात हमारे बीच ही रहे", "कृपया इसे याद न रखें"],
        "status": "क्या यह चैट सहेजी जा रही है?", "back": "सामान्य मोड",
        "not": ["इस फ़ाइल को डॉक्युमेंट्स में सहेजो", "पासवर्ड ब्राउज़र में सेव मत करो", "कल की मीटिंग रिकॉर्ड करना",
                "निजी चैट कैसे काम करती है?", "मेरा फ़ोन रिकॉर्डिंग नहीं कर रहा", "मेरी कंपनी सारी कॉल रिकॉर्ड करती है",
                "यह नंबर सहेज लो", "क्या तुम आवाज़ रिकॉर्ड कर सकते हो?", "कल की रिकॉर्डिंग डिलीट हो गई",
                "व्हाट्सऐप चैट सेव करता है क्या?"],
    },
    "ja": {
        "on": "プライベートチャット", "off": "記録を再開",
        "more_on": ["これを保存しないで", "ここだけの話にしてください", "何も保存しないで"],
        "status": "このチャットは保存されてますか", "back": "普通のチャットに戻して",
        "not": ["このファイルを書類フォルダに保存して", "パスワードをブラウザに保存しないで", "明日の会議を録音して",
                "プライベートチャットってどういう機能？", "昨日の録音を再生して", "この画像を保存して",
                "会社は電話を全部録音してるらしい", "プライベートチャットは暗号化されますか", "この場所は保存しておいて",
                "ツイッターのアカウントを非公開にする方法は？"],
    },
    "ko": {
        "on": "비공개 채팅", "off": "기록 다시 켜기",
        "more_on": ["이거 저장하지 마", "이건 우리끼리만", "아무것도 저장하지 마세요"],
        "status": "이 채팅 저장되고 있어요?", "back": "다시 저장",
        "not": ["이 파일을 문서 폴더에 저장해줘", "비밀번호는 브라우저에 저장하지 마", "내일 회의 녹음해줘",
                "비공개 채팅 어떻게 켜는 거야?", "어제 녹음 다시 틀어줘", "이 사진 저장해줘", "회사에서 전화 다 녹음한대",
                "비공개 채팅은 암호화 되나요?", "인스타 계정 비공개로 바꾸고 싶어", "이 위치 저장해둬"],
    },
    "lt": {
        "on": "privatus pokalbis", "off": "vėl įjungti įrašymą",
        "more_on": ["neišsaugok to", "tik tarp mūsų", "neirasyk to"],
        "status": "ar šis pokalbis saugomas?", "back": "vėl saugoti",
        "not": ["išsaugok šį failą dokumentų aplanke", "neišsaugok slaptažodžio naršyklėje", "įrašyk rytojaus susitikimą",
                "kaip veikia privatus pokalbis?", "mano telefonas neįrašinėja vaizdo", "mano įmonė įrašinėja visus skambučius",
                "išsaugok man jo numerį", "ar gali įrašyti balso žinutę?", "vakarykštis įrašas dingo",
                "ar privatūs pokalbiai šifruojami?"],
    },
    "pl": {
        "on": "prywatny czat", "off": "włącz ponownie zapisywanie",
        "more_on": ["nie zapisuj tego", "tylko między nami", "możemy pogadać prywatnie?"],
        "status": "czy zapisujesz tę rozmowę?", "back": "zwykły czat",
        "not": ["zapisz ten plik w dokumentach", "nie zapisuj hasła w przeglądarce", "nagraj jutrzejsze spotkanie",
                "jak działa prywatny czat?", "mój telefon nie nagrywa", "firma nagrywa wszystkie rozmowy",
                "zapisz mi jego numer", "możesz nagrać notatkę głosową?", "wczorajsze nagranie się usunęło",
                "czy prywatne czaty są szyfrowane?"],
    },
    "pt": {
        "on": "chat privado", "off": "voltar a ativar a gravação",
        "more_on": ["não guarde isso", "isto fica entre nós", "podemos falar em privado?"],
        "status": "estás a gravar isto?", "back": "guardar de novo",
        "not": ["guarda este ficheiro nos documentos", "não salve a senha no navegador", "grava a reunião de amanhã",
                "como funciona o chat privado?", "o meu telemóvel não grava vídeo", "a minha empresa grava todas as chamadas",
                "guarda-me o número dele", "podes gravar uma mensagem de voz?", "a gravação de ontem apagou-se",
                "os chats privados são encriptados?"],
    },
    "ru": {
        "on": "приватный чат", "off": "снова включить запись",
        "more_on": ["не сохраняй это", "это между нами", "без записи"],
        "status": "ты это записываешь?", "back": "обычный чат",
        "not": ["сохрани этот файл в документы", "не сохраняй пароль в браузере", "запиши завтрашнюю встречу",
                "как работает приватный чат?", "мой телефон не записывает видео", "работа записывает все звонки",
                "сохрани мне его номер", "запиши голосовое сообщение", "вчерашняя запись удалилась",
                "приватные чаты шифруются?"],
    },
    "tr": {
        "on": "özel sohbet", "off": "kaydı yeniden aç",
        "more_on": ["bunu kaydetme", "aramızda kalsın", "ozel konusabilir miyiz?"],
        "status": "bu sohbet kaydediliyor mu?", "back": "normal sohbet",
        "not": ["bu dosyayı belgeler klasörüne kaydet", "şifreyi tarayıcıya kaydetme", "yarınki toplantıyı kaydet",
                "özel sohbet nasıl çalışıyor?", "telefonum video kaydetmiyor", "şirket tüm aramaları kaydediyor",
                "numarasını kaydet", "sesli mesaj kaydedebilir misin?", "dünkü kayıt silinmiş", "özel sohbetler şifreli mi?"],
    },
    "uk": {
        "on": "приватний чат", "off": "знову ввімкнути запис",
        "more_on": ["не зберігай це", "це між нами", "можна приватно?"],
        "status": "цей чат зберігається?", "back": "звичайний чат",
        "not": ["збережи цей файл у документи", "не зберігай пароль у браузері", "запиши завтрашню зустріч",
                "як працює приватний чат?", "мій телефон не пише відео", "робота записує всі дзвінки",
                "збережи мені його номер", "запиши голосове повідомлення", "вчорашній запис видалився",
                "приватні чати шифруються?"],
    },
    "vi": {
        "on": "trò chuyện riêng tư", "off": "bật lại ghi",
        "more_on": ["đừng lưu chuyện này", "dung luu chuyen nay", "chuyện này giữa tụi mình thôi"],
        "status": "cuộc trò chuyện này được lưu chứ?", "back": "lưu lại",
        "not": ["lưu file này vào thư mục tài liệu", "đừng lưu mật khẩu vào trình duyệt", "ghi lại cuộc họp ngày mai",
                "trò chuyện riêng tư hoạt động thế nào?", "điện thoại tôi không quay được video",
                "công ty ghi lại tất cả cuộc gọi", "lưu giùm số của anh ấy", "ghi âm giùm đoạn này",
                "bản ghi hôm qua bị xóa rồi", "trò chuyện riêng tư có mã hóa không?"],
    },
    "zh-Hans": {
        "on": "私密聊天", "off": "重新开启记录",
        "more_on": ["不要保存这个", "咱们私聊吧", "什么都不要保存"],
        "status": "这个聊天有在保存吗？", "back": "重新保存",
        "not": ["把这个文件保存到文档文件夹", "不要在浏览器里保存密码", "记录一下明天的会议", "私密聊天怎么用？",
                "我手机录不了视频", "公司把电话都录音了", "帮我存一下这个链接", "把这段录音再放一遍",
                "私密聊天是加密的吗？", "把微博账号设成私密"],
    },
}


def _reply(locale: str, name: str) -> str:
    return catalog_for(locale).text(f"private.reply.{name}")


def _stream(daemon: ServedDaemon, text: str, chat: str, locale: str | None) -> tuple[str, dict[str, Any]]:
    """One streamed turn, as the page sends it, with the page's language cookie (or none)."""
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


def _private(commit: dict[str, Any]) -> bool:
    return (commit.get("display_metadata") or {}).get("private_chat") is True


def _on_now(commit: dict[str, Any]) -> Any:
    return (commit.get("display_metadata") or {}).get("private_chat_on")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("pclang") / "home"
    provider = _Provider({MODEL: "Sounds lovely."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            yield daemon, home


def test_every_language_has_its_own_replies():
    """Control for the reply assertions below: each language's catalog really serves its own words."""
    for locale in CASES:
        for name in ("on", "off", "already_off", "status_on", "status_off", "remember_refused"):
            assert _reply(locale, name) != _reply("en", name), (locale, name)


@pytest.mark.parametrize("locale", sorted(CASES))
def test_private_chat_switches_in_each_language_over_the_chat_door(served, locale):
    daemon, _home = served
    case = CASES[locale]
    chat = "openclaw:" + (locale.replace("-", "").lower() * 20)[:20].encode().hex()[:20]

    # Ordinary sentences that mention saving, recording or privacy: nothing switches, all are saved.
    for text in case["not"]:
        _, commit = _stream(daemon, text, chat, locale)
        assert not _private(commit), (locale, text)
    saved = _history(daemon, chat)
    assert "private_chat" not in saved
    # Every one of them is in the transcript, one user row each. (Rows are compared by count: the
    # stored transcript rewrites some words of other languages, e.g. "im" -> "i am", which is not
    # what this test is about.)
    users = [m for m in saved.get("messages", []) if m.get("role") == "user"]
    assert len(users) == len(case["not"]), (locale, len(users), users)

    # Asked while saving; a "back to normal" outside private chat is not a command.
    answer, _ = _stream(daemon, case["status"], chat, locale)
    assert answer == _reply(locale, "status_off"), (locale, answer)
    _, commit = _stream(daemon, case["back"], chat, locale)
    assert not _private(commit)

    # The canonical words, then the status question while private, then the canonical way out.
    answer, commit = _stream(daemon, case["on"], chat, locale)
    assert answer == _reply(locale, "on"), (locale, answer)
    assert _private(commit) and _on_now(commit) is True
    answer, commit = _stream(daemon, case["status"], chat, locale)
    assert answer == _reply(locale, "status_on"), (locale, answer)
    assert _private(commit)
    answer, commit = _stream(daemon, case["off"], chat, locale)
    assert answer == _reply(locale, "off"), (locale, answer)
    assert _on_now(commit) is False

    # Three more everyday ways in, each ended by the canonical words or "back to normal".
    for index, text in enumerate(case["more_on"]):
        answer, commit = _stream(daemon, text, chat, locale)
        assert answer == _reply(locale, "on"), (locale, text, answer)
        assert _private(commit) and _on_now(commit) is True
        way_out = case["back"] if index == 0 else case["off"]
        answer, commit = _stream(daemon, way_out, chat, locale)
        assert answer == _reply(locale, "off"), (locale, way_out, answer)
        assert _on_now(commit) is False
    assert "private_chat" not in _history(daemon, chat)


def test_the_reply_follows_the_page_language_not_the_command_language(served):
    daemon, _home = served
    chat = "openclaw:" + "5e" * 10
    # German words on a Japanese page: switched, answered in Japanese.
    answer, commit = _stream(daemon, "speicher das nicht", chat, "ja")
    assert answer == _reply("ja", "on") and _private(commit)
    # English words on a Russian page: the English parser switches it, the reply is Russian.
    answer, _ = _stream(daemon, "turn recording back on", chat, "ru")
    assert answer == _reply("ru", "off")
    # No cookie, and a language the app does not ship: English.
    answer, _ = _stream(daemon, "приватный чат", chat, None)
    assert answer == _reply("en", "on")
    answer, _ = _stream(daemon, "приватный чат", chat, "xx")
    assert answer == _reply("en", "already_on")
    answer, _ = _stream(daemon, "Aufzeichnung wieder einschalten", chat, "zh")
    assert answer == _reply("zh-Hans", "off")


def test_a_private_turn_in_german_leaves_none_of_its_words(served):
    daemon, home = served
    chat = "openclaw:" + "4d" * 10
    _stream(daemon, "Mein Hund heißt Bello.", chat, "de")
    answer, commit = _stream(daemon, f"Speicher das nicht, die Feier ist in der {SECRET}-Halle.", chat, "de")
    assert _private(commit) and _on_now(commit) is True, answer
    # The memory command is read in English; the refusal answers in the page's language.
    refused, _ = _stream(daemon, f"remember that the party is at the {SECRET} hall", chat, "de")
    assert refused == _reply("de", "remember_refused"), refused
    history = json.dumps(_history(daemon, chat), ensure_ascii=False)
    assert SECRET not in history and "Bello" in history
    answer, _ = _stream(daemon, "Aufzeichnung wieder einschalten", chat, "de")
    assert answer == _reply("de", "off")
    assert _files_holding(home, SECRET) == []


def test_an_objectless_request_followed_by_more_words_is_left_to_the_model():
    """Same rule as English "don't save, ...": with no object it may be about a file, so only the
    whole message switches. The model is still told the chat is being saved (tests/test_private_chat.py)."""
    assert parse_private_command("bitte nicht speichern, mein Gehalt ist 90k") is None
    assert parse_private_command("bitte nicht speichern").action == "on"
    assert parse_private_command("speicher das nicht, mein Gehalt ist 90k").action == "on"
