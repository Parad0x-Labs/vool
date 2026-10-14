"""Private chat commands in VOOL's other app languages, and the app language of a chat turn.

:mod:`core.private_chat` owns the switch and reads English. This module adds the same commands in
the sixteen other languages the app ships a catalog for, read the same strict way:

* a command is a **whole message**, a **whole sentence**, or (turning private ON only) the **first
  clause** of a message or its **last clause**, never a loose substring. "Speicher das nicht, mein
  Gehalt ist 90k" turns it on; "speichere mein Passwort nie im Browser" does not;
* a request with **no object** ("nicht speichern", "no guardes", "保存しないで") counts only as the
  whole message, exactly like English "don't save": followed by other words it may be about a file;
* ending private chat, and asking whether it is on, count only as the whole message;
* "back to normal" / "save again" end private chat only while the chat is private;
* case, accents and diacritics are ignored (people type "dung luu chuyen nay", "konnen wir");
  Japanese, Chinese and Korean are compared without spaces.

Commands are recognised in every language whatever the app language is: people mix. Replies use
the app language (:func:`ui_locale_of`), which the chat door stamps on the turn from the page's
``vool_ui_locale`` cookie.

Phrases are authored here as small regular expressions written in plain, accented text; both
sides are folded the same way before matching, so a phrase never needs its unaccented twin. Only
literal text, spaces, ``(?:...)``, ``|`` and ``?`` are used in them.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

#: The source_context key the chat door sets from the page's language cookie. Kept generic (not
#: private-chat specific) so any fast-path reply can read the same value.
UI_LOCALE_KEY = "ui_locale"

ON = "on"
OFF = "off"
STATUS = "status"

#: Languages written without spaces between words: phrases and messages are compared spaceless.
_SPACELESS = frozenset({"ja", "zh-Hans", "ko"})

# ── the phrases ───────────────────────────────────────────────────────────────
#
# on        turn private ON; may also stand as a sentence, the first clause or the last clause
# on_whole  turn private ON only as the whole message (no object, or an idiom that is ambiguous
#           once other words follow)
# off       end private chat (whole message only)
# off_private  end private chat only while it is on (whole message only): "normal mode", "save again"
# status    ask whether it is on (whole message only)
# polite    words a command may start or end with ("bitte", "por favor")
# conj      words that end the first clause ("aber", "pero")
# reported  words that, ending the clause before a last-clause command, make it reported speech
# on_canonical / off_canonical  the phrases replies quote (the page's own words, from the catalog)

LANGUAGES: dict[str, dict[str, Any]] = {
    "de": {
        "on_canonical": "Privater Chat",
        "off_canonical": "Aufzeichnung wieder einschalten",
        "on": (
            "privater chat(?: an| bitte)?",
            "privater modus",
            "(?:starte|aktiviere|aktivier) (?:den )?privaten chat",
            "(?:mach|mache|machen sie) (?:den|diesen) chat privat",
            "(?:lass uns|lasst uns) privat (?:reden|sprechen|chatten)",
            "(?:können|könnten) wir privat (?:reden|sprechen|chatten)",
            "(?:speicher|speichere|speichern sie|sichere|sichern sie) (?:das|dies|es|das hier|den chat|diesen chat|dieses gespräch|unser gespräch|meine nachrichten) nicht(?: ab)?",
            "(?:zeichne|zeichnen sie|nimm|nehmen sie) (?:das|dies|es|das hier|den chat|diesen chat|dieses gespräch|unser gespräch|nichts) (?:nicht )?auf",
            "(?:merk|merke|merken sie) (?:dir|sich) (?:das|dies|es) nicht",
            "nichts (?:speichern|aufzeichnen|mitschreiben|abspeichern)",
            "(?:schreib|schreibe) nichts mit",
            "(?:das|es|dies) bleibt (?:unter|zwischen) uns",
            "nur (?:unter|zwischen) uns",
            "ich (?:möchte|will) nicht,? dass (?:du|sie) (?:das|dies|es|diesen chat|dieses gespräch) (?:speicherst|speichern|aufzeichnest|aufzeichnen)",
        ),
        "on_whole": (
            "nicht (?:speichern|abspeichern|aufzeichnen|mitschreiben)",
            "(?:mach|mache) (?:das|es) privat",
            "inkognito(?:-| )?modus",
        ),
        "off": (
            "aufzeichnung wieder (?:einschalten|an|ein|anschalten)",
            "(?:schalte|schalt|mach|mache|schalten sie) die aufzeichnung wieder (?:ein|an)",
            "(?:privaten chat|privaten modus) beenden",
            "(?:beende|beenden sie|verlasse|verlassen sie) den privaten (?:chat|modus)",
            "privater (?:chat|modus) aus",
            "(?:zeichne|zeichnen sie) wieder auf",
            "(?:du kannst|sie können) wieder speichern",
        ),
        "off_private": (
            "(?:wieder|weiter) speichern",
            "(?:speicher|speichere|speichern sie) (?:wieder|weiter)",
            "chat wieder speichern",
            "normaler (?:modus|chat)",
            "zurück zum normalen (?:chat|modus)",
            "(?:schalte|schalt|mach) es wieder an",
        ),
        "status": (
            "wird (?:das|das hier|dieser chat|der chat|unser gespräch) (?:gespeichert|aufgezeichnet)",
            "(?:zeichnest|speicherst) du (?:das |das hier |unser gespräch )?(?:auf|mit)?",
            "speicherst du (?:das|das hier|alles)",
            "speichern sie das",
            "sind wir privat",
            "ist (?:der|dieser) chat privat",
            "ist (?:der )?private(?:r)? (?:chat|modus) an",
        ),
        "polite": ("bitte", "danke", "hey", "hallo", "ok", "okay", "also", "jetzt", "ab jetzt", "vool"),
        "conj": ("aber", "und", "weil", "denn", "sondern"),
        "reported": ("sagt", "sagte", "gesagt", "schrieb", "geschrieben", "fragte", "gefragt", "wenn", "ob", "dass"),
    },
    "es": {
        "on_canonical": "chat privado",
        "off_canonical": "volver a activar el registro",
        "on": (
            "chat privado",
            "modo privado",
            "conversación privada",
            "(?:activa|activar|empieza|inicia) (?:el )?(?:chat|modo) privado",
            "(?:hazlo|hágalo) privado",
            "(?:haz|haga) (?:este chat|esta conversación) privad(?:o|a)",
            "no (?:guardes|guarde|grabes|grabe|registres|registre|almacenes|almacene) (?:esto|eso|nada|este chat|esta conversación|nada de esto|nada de esta conversación|mis mensajes)",
            "no me (?:grabes|grabe|registres|registre)",
            "(?:esto|eso) (?:queda|se queda) entre (?:nosotros|nosotras|tú y yo)",
            "solo entre (?:nosotros|nosotras|tú y yo)",
            "(?:hablamos|hablemos|podemos hablar|puedo hablar contigo|quiero hablar) en privado",
            "no quiero que (?:esto|eso|este chat|esta conversación) (?:se guarde|se grabe|quede guardad(?:o|a)|quede grabad(?:o|a)|quede registrad(?:o|a))",
            "(?:deja|deje|para|pare) de (?:grabar|guardar)",
        ),
        "on_whole": (
            "no (?:guardes|guarde|grabes|grabe|registres|registre)",
            "entre tú y yo",
            "modo incógnito",
        ),
        "off": (
            "(?:volver a|vuelve a|vuelva a) activar el registro",
            "(?:reactiva|reactive|reactivar) el registro",
            "(?:desactiva|desactive|desactivar|termina|termine|terminar|cierra|cierre|cerrar) (?:el )?(?:chat|modo) privado",
            "(?:sal|salga|salir) del (?:chat|modo) privado",
            "ya (?:puedes|puede) volver a guardar",
            "(?:puedes|puede) volver a guardar",
        ),
        "off_private": (
            "(?:vuelve|vuelva|volver) a guardar",
            "(?:guarda|guarde|guardar|graba|grabe|grabar) (?:otra vez|de nuevo)",
            "(?:sigue|siga) (?:grabando|guardando)",
            "ya (?:puedes|puede) guardar",
            "(?:chat|modo) normal",
            "(?:vuelve|vuelva|volver|volvamos) al (?:chat|modo) normal",
        ),
        "status": (
            "(?:estás|está) (?:grabando|guardando|registrando)(?: esto| este chat| esta conversación)?",
            "se está (?:grabando|guardando) (?:esto|el chat|este chat|esta conversación)",
            "(?:estamos en|está activado el|está activo el) (?:modo privado|chat privado)",
            "(?:el|este) chat es privado",
            "guardas todo lo que (?:digo|escribo)",
        ),
        "polite": ("por favor", "porfa", "gracias", "oye", "vale", "ok", "okey", "hola", "ahora", "vool"),
        "conj": ("pero", "y", "porque"),
        "reported": ("dijo", "dice", "escribió", "preguntó", "si", "cuando", "que"),
    },
    "fr": {
        "on_canonical": "discussion privée",
        "off_canonical": "réactiver l'enregistrement",
        "on": (
            "(?:discussion|conversation) privée",
            "(?:chat|mode) privé",
            "(?:active|activez|activer|démarre|démarrez|lance|lancez) (?:la discussion privée|le mode privé|le chat privé)",
            "(?:on peut|est-ce qu'on peut|pouvons-nous|on pourrait|je veux) (?:parler|discuter) en privé",
            "(?:parlons|discutons) en privé",
            "(?:ça|cela|ceci) reste entre nous",
            "(?:n'enregistre|n'enregistrez|enregistre|ne garde|ne gardez|ne sauvegarde|ne sauvegardez|ne retiens|ne retenez|ne mémorise|ne mémorisez) pas (?:ça|ceci|cela|cette discussion|cette conversation|mes messages)",
            "(?:n'enregistre|n'enregistrez|ne garde|ne gardez|ne sauvegarde|ne sauvegardez|ne retiens|ne retenez) rien",
            "je veux une (?:discussion|conversation) privée",
            "je ne veux pas que (?:tu enregistres|vous enregistriez) (?:ça|cela|cette conversation|cette discussion)",
            "(?:arrête|arrêtez) d'enregistrer",
        ),
        "on_whole": (
            "(?:n'enregistre|n'enregistrez|ne sauvegarde|ne sauvegardez) pas",
            "entre (?:toi|vous) et moi",
        ),
        "off": (
            "(?:réactiver|réactive|réactivez) l'enregistrement",
            "(?:remets|remettez|remettre) l'enregistrement",
            "(?:reprends|reprenez|reprendre) l'enregistrement",
            "(?:quitte|quittez|quitter|termine|terminez|terminer|arrête|arrêtez|arrêter|ferme|fermez|fermer|désactive|désactivez|désactiver) (?:la discussion privée|le mode privé|le chat privé)",
            "fin (?:du mode privé|de la discussion privée|du chat privé)",
            "(?:tu peux|vous pouvez) (?:réenregistrer|enregistrer à nouveau|enregistrer de nouveau)",
        ),
        "off_private": (
            "réenregistrer",
            "(?:enregistre|enregistrez) (?:à nouveau|de nouveau)",
            "(?:mode|discussion) normale?",
            "(?:retour|reviens|revenez|revenons) (?:au|en) mode normal",
        ),
        "status": (
            "(?:tu enregistres|vous enregistrez|est-ce que tu enregistres|est-ce que vous enregistrez)(?: ça| cette discussion| cette conversation)?",
            "(?:cette discussion|cette conversation|ce chat) est(?:-elle|-il)? enregistrée?",
            "est-ce que c'est enregistré",
            "on est en (?:privé|mode privé|discussion privée)",
        ),
        "polite": ("s'il te plaît", "s'il vous plaît", "stp", "svp", "merci", "ok", "d'accord", "eh", "bon", "alors", "maintenant", "vool"),
        "conj": ("mais", "et", "parce que", "car"),
        "reported": ("dit", "disait", "écrit", "écrivait", "demandé", "si", "quand", "que"),
    },
    "pt": {
        "on_canonical": "chat privado",
        "off_canonical": "voltar a ativar a gravação",
        "on": (
            "chat privado",
            "modo privado",
            "conversa privada",
            "(?:ativa|ative|ativar|inicia|inicie) o (?:chat|modo) privado",
            "(?:torna|torne|deixa|deixe) (?:isto|isso|este chat|esta conversa) privad(?:o|a)",
            "não (?:guardes|guarde|graves|grave|salves|salve|registes|registe|registre) (?:isto|isso|nada|este chat|esta conversa|nada disto|nada disso|nada desta conversa)",
            "(?:isto|isso) fica entre nós",
            "só entre nós",
            "(?:quero|queria|podemos) falar em privado",
            "não quero que (?:isto|isso|este chat|esta conversa) (?:seja|fique) (?:guardad|gravad|salv)(?:o|a)",
            "(?:deixa|deixe|para|pare) de (?:gravar|guardar)",
        ),
        "on_whole": (
            "não (?:guardes|guarde|graves|grave|salves|salve)",
            "modo incógnito",
        ),
        "off": (
            "(?:voltar a|volta a|volte a) ativar a gravação",
            "(?:reativa|reative|reativar) a gravação",
            "(?:sai|saia|sair) do (?:chat|modo) privado",
            "(?:termina|termine|terminar|desativa|desative|desativar|fecha|feche|encerra|encerre) o (?:chat|modo) privado",
            "acaba com o chat privado",
            "(?:podes|pode) voltar a (?:guardar|gravar|salvar)",
        ),
        "off_private": (
            "(?:volta|volte|torna|torne) a guardar",
            "(?:grava|grave|guarda|guarde|salva|salve) (?:outra vez|de novo)",
            "guardar de novo",
            "já (?:podes|pode) (?:gravar|guardar|salvar)",
            "(?:chat|modo) normal",
        ),
        "status": (
            "(?:estás|está) a (?:gravar|guardar)(?: isto| isso| este chat)?",
            "(?:estás|está) (?:gravando|salvando|guardando)(?: isto| isso| este chat)?",
            "(?:este chat|esta conversa) está a ser (?:gravad|guardad)(?:o|a)",
            "(?:este chat|esta conversa) está sendo (?:gravad|salv|guardad)(?:o|a)",
            "estamos em modo privado",
            "(?:este|o) chat é privado",
        ),
        "polite": ("por favor", "obrigado", "obrigada", "ok", "olá", "ei", "agora", "vool"),
        "conj": ("mas", "e", "porque"),
        "reported": ("disse", "diz", "escreveu", "perguntou", "se", "quando", "que"),
    },
    "pl": {
        "on_canonical": "prywatny czat",
        "off_canonical": "włącz ponownie zapisywanie",
        "on": (
            "prywatny czat",
            "tryb prywatny",
            "prywatna rozmowa",
            "(?:włącz|uruchom) (?:prywatny czat|tryb prywatny)",
            "zrób z tego prywatny czat",
            "(?:zrób|zróbmy) (?:ten czat|tę rozmowę) prywatn(?:ym|ą)",
            "nie (?:zapisuj|nagrywaj|zapamiętuj|zachowuj|zapisujcie) (?:tego|tej rozmowy|tego czatu|moich wiadomości|niczego|nic)",
            "nic nie (?:zapisuj|nagrywaj|zapamiętuj|zapisywać)",
            "(?:to )?zostaje między nami",
            "tylko między nami",
            "(?:chcę|chciałbym|chciałabym|możemy|czy możemy) (?:pogadać|porozmawiać) prywatnie",
            "bez (?:zapisywania|nagrywania)",
            "(?:przestań|przestańcie) (?:zapisywać|nagrywać)",
        ),
        "on_whole": ("nie (?:zapisuj|nagrywaj)",),
        "off": (
            "włącz (?:ponownie|znowu|z powrotem) zapisywanie",
            "włącz zapisywanie(?: ponownie| znowu)?",
            "(?:zakończ|wyłącz) (?:prywatny czat|tryb prywatny)",
            "możesz znowu zapisywać",
            "wróć do zapisywania",
        ),
        "off_private": (
            "zapisuj (?:znowu|dalej|ponownie)",
            "nagrywaj dalej",
            "zwykły czat",
            "(?:wracamy|wróć|wróćmy) do (?:normalnego|zwykłego) (?:czatu|trybu)",
            "(?:normalny tryb|tryb normalny)",
        ),
        "status": (
            "czy (?:to )?(?:nagrywasz|zapisujesz)(?: to| tę rozmowę| ten czat)?",
            "(?:zapisujesz|nagrywasz) (?:to|wszystko|tę rozmowę)",
            "czy (?:ten czat|ta rozmowa) jest (?:prywatny|prywatna|zapisywany|zapisywana|nagrywany|nagrywana)",
            "czy (?:to )?jest zapisywane",
            "czy jesteśmy w (?:trybie prywatnym|prywatnym czacie)",
        ),
        "polite": ("proszę", "dzięki", "dziękuję", "hej", "ok", "okej", "teraz", "vool"),
        "conj": ("ale", "i", "bo", "a"),
        "reported": ("powiedział", "powiedziała", "mówi", "napisał", "napisała", "zapytał", "zapytała", "jeśli", "gdy", "kiedy", "że"),
    },
    "ru": {
        "on_canonical": "приватный чат",
        "off_canonical": "снова включить запись",
        "on": (
            "приватный (?:чат|режим)",
            "(?:включи|включите) приватный (?:чат|режим)",
            "(?:сделай|сделайте) (?:этот чат|этот разговор|чат|разговор) приватным",
            "(?:не сохраняй|не сохраняйте|не записывай|не записывайте|не запоминай|не запоминайте) (?:это|этот чат|этот разговор|наш разговор|мои сообщения|ничего)",
            "ничего не (?:сохраняй|сохраняйте|записывай|записывайте|запоминай|запоминайте)",
            "это (?:останется )?между нами",
            "пусть (?:это )?останется между нами",
            "только между нами",
            "(?:хочу|можно|давай|давайте) поговорить приватно",
            "(?:давай|давайте) поговорим приватно",
            "можно приватно",
            "без записи",
            "(?:перестань|перестаньте|прекрати|прекратите) (?:записывать|сохранять)",
        ),
        "on_whole": (
            "не (?:сохраняй|сохраняйте|записывай|записывайте)",
            "режим инкогнито",
        ),
        "off": (
            "снова (?:включить|включи|включите) запись",
            "(?:включи|включите) (?:снова )?запись(?: снова)?",
            "(?:заверши|завершите|выключи|выключите|отключи|отключите|закончи|закончите) приватный (?:чат|режим)",
            "(?:выйди|выйдите) из приватного (?:чата|режима)",
            "(?:можешь|можете) снова (?:сохранять|записывать)",
        ),
        "off_private": (
            "снова (?:сохраняй|сохраняйте|сохранять)",
            "(?:сохрани|сохраняй) снова",
            "(?:продолжай|продолжайте) (?:записывать|сохранять)",
            "(?:записывай|сохраняй) дальше",
            "обычный (?:чат|режим)",
            "(?:вернись|вернитесь|вернёмся|давай вернёмся) к обычному (?:чату|режиму)",
        ),
        "status": (
            "(?:ты|вы) (?:это )?(?:записываешь|сохраняешь|записываете|сохраняете)(?: это)?",
            "(?:этот чат|этот разговор|это|всё) (?:сохраняется|записывается)",
            "мы в приватном (?:чате|режиме)",
            "(?:этот )?чат приватный",
        ),
        "polite": ("пожалуйста", "спасибо", "ок", "окей", "слушай", "эй", "теперь", "сейчас", "vool"),
        "conj": ("но", "и", "а", "потому что"),
        "reported": ("сказал", "сказала", "говорит", "написал", "написала", "спросил", "спросила", "если", "когда", "что"),
    },
    "uk": {
        "on_canonical": "приватний чат",
        "off_canonical": "знову ввімкнути запис",
        "on": (
            "приватний (?:чат|режим)",
            "(?:ввімкни|увімкни|ввімкніть|увімкніть) приватний (?:чат|режим)",
            "(?:зроби|зробіть) (?:цей чат|цю розмову|чат) (?:приватним|приватною)",
            "(?:не зберігай|не зберігайте|не записуй|не записуйте|не запам'ятовуй|не запам'ятовуйте) (?:це|цей чат|цю розмову|нашу розмову|мої повідомлення|нічого)",
            "нічого не (?:зберігай|зберігайте|записуй|записуйте|запам'ятовуй)",
            "це (?:залишиться )?між нами",
            "(?:хай|нехай) (?:це )?залишиться між нами",
            "тільки між нами",
            "(?:хочу|можна|давай|давайте) поговорити приватно",
            "(?:давай|давайте) поговоримо приватно",
            "можна приватно",
            "без запису",
            "(?:перестань|перестаньте|припини|припиніть) (?:записувати|зберігати)",
        ),
        "on_whole": (
            "не (?:зберігай|зберігайте|записуй|записуйте)",
            "режим інкогніто",
        ),
        "off": (
            "знову (?:ввімкнути|увімкнути|ввімкни|увімкни|ввімкніть|увімкніть) запис",
            "(?:ввімкни|увімкни|ввімкніть|увімкніть) (?:знову )?запис(?: знову)?",
            "(?:заверши|завершіть|вимкни|вимкніть) приватний (?:чат|режим)",
            "(?:вийди|вийдіть) з приватного (?:чату|режиму)",
            "(?:можеш|можете) знову (?:зберігати|записувати)",
        ),
        "off_private": (
            "знову (?:зберігай|зберігайте|зберігати)",
            "(?:продовжуй|продовжуйте) (?:записувати|зберігати)",
            "(?:зберігай|записуй) далі",
            "звичайний (?:чат|режим)",
            "(?:повернись|поверніться|повернімось|повернімося) до звичайного (?:чату|режиму)",
        ),
        "status": (
            "(?:ти|ви) (?:це )?(?:записуєш|зберігаєш|записуєте|зберігаєте)(?: це)?",
            "(?:цей чат|ця розмова|це|все) (?:зберігається|записується)",
            "ми в приватному (?:чаті|режимі)",
            "(?:цей )?чат приватний",
        ),
        "polite": ("будь ласка", "дякую", "ок", "окей", "слухай", "тепер", "зараз", "vool"),
        "conj": ("але", "і", "й", "а", "бо", "тому що"),
        "reported": ("сказав", "сказала", "каже", "написав", "написала", "спитав", "спитала", "якщо", "коли", "що"),
    },
    "lt": {
        "on_canonical": "privatus pokalbis",
        "off_canonical": "vėl įjungti įrašymą",
        "on": (
            "privatus (?:pokalbis|režimas)",
            "(?:įjunk|įjunkite) privatų (?:pokalbį|režimą)",
            "(?:padaryk|padarykite) šį pokalbį privatų",
            "(?:padaryk|padarykite) tai privačiu",
            "(?:neišsaugok|neišsaugokite|nesaugok|nesaugokite|neįrašinėk|neįrašinėkite|neįrašyk|neįrašykite) (?:to|šito|šio pokalbio|nieko|mano žinučių)",
            "(?:tai )?lieka tarp mūsų",
            "tik tarp mūsų",
            "(?:noriu|galime|gal galime) pasikalbėti privačiai",
            "be įrašymo",
            "(?:nustok|nustokite) (?:saugoti|įrašinėti)",
        ),
        "on_whole": ("(?:neišsaugok|neišsaugokite|nesaugok|nesaugokite|neįrašinėk|neįrašinėkite)",),
        "off": (
            "vėl įjungti įrašymą",
            "(?:vėl )?(?:įjunk|įjunkite) įrašymą(?: vėl)?",
            "(?:baik|baikite|baikime|išjunk|išjunkite) privatų (?:pokalbį|režimą)",
            "(?:gali|galite) vėl saugoti",
        ),
        "off_private": (
            "vėl (?:saugok|saugokite|saugoti)",
            "(?:tęsk|tęskite) (?:įrašymą|saugojimą)",
            "toliau (?:saugok|saugokite)",
            "(?:grįžkime|grįžk|grįžkite) (?:prie įprasto|į įprastą) (?:pokalbio|režimo|pokalbį|režimą)",
            "įprastas (?:pokalbis|režimas)",
        ),
        "status": (
            "ar (?:tu |jūs )?(?:įrašinėji|įrašai|saugai|išsaugai|įrašote|saugote|įrašinėjate)(?: tai| šį pokalbį| viską)?",
            "ar (?:šis )?pokalbis (?:saugomas|įrašomas|privatus)",
            "ar (?:mes )?(?:esame )?privačiame (?:režime|pokalbyje)",
            "ar viską (?:išsaugoji|saugai)",
        ),
        "polite": ("prašau", "ačiū", "labas", "ok", "gerai", "dabar", "vool"),
        "conj": ("bet", "ir", "nes"),
        "reported": ("sakė", "pasakė", "parašė", "paklausė", "jei", "jeigu", "kai", "kad"),
    },
    "tr": {
        "on_canonical": "özel sohbet",
        "off_canonical": "kaydı yeniden aç",
        "on": (
            "özel sohbet(?: olsun| aç| başlat)?",
            "özel mod",
            "(?:bunu|bu sohbeti) özel yap",
            "(?:bunu|bunları|bu sohbeti|bu konuşmayı|hiçbir şey|hiçbir şeyi|mesajlarımı) (?:kaydetme|kaydetmeyin|hatırlama|hatırlamayın)",
            "kaydetme bunları",
            "(?:bu )?aramızda kalsın",
            "kaydı kapat",
            "özel (?:konuşalım|konuşabilir miyiz)",
            "kaydetmeyi (?:bırak|durdur)",
        ),
        "on_whole": ("kaydetme", "kaydetmeyin"),
        "off": (
            "kaydı (?:yeniden|tekrar) aç",
            "(?:kaydı|kaydetmeyi) (?:yeniden|tekrar) başlat",
            "özel sohbeti (?:bitir|kapat|sonlandır)",
            "özel moddan çık",
            "(?:yeniden|tekrar) kaydedebilirsin",
        ),
        "off_private": (
            "(?:yeniden|tekrar) kaydet",
            "kaydetmeye devam et",
            "normal sohbet(?:e dönelim|e dön)?",
            "artık kaydedebilirsin",
        ),
        "status": (
            "(?:bunu |her şeyi )?kaydediyor musun(?:uz)?",
            "kayıt açık mı",
            "(?:bu sohbet|bu konuşma) kaydediliyor mu",
            "özel sohbette miyiz",
            "bu sohbet özel mi",
        ),
        "polite": ("lütfen", "teşekkürler", "sağol", "tamam", "ok", "hey", "şimdi", "vool"),
        "conj": ("ama", "ve", "çünkü", "fakat"),
        "reported": ("dedi", "diyor", "yazdı", "sordu", "eğer"),
    },
    "vi": {
        "on_canonical": "trò chuyện riêng tư",
        "off_canonical": "bật lại ghi",
        "on": (
            "trò chuyện riêng tư",
            "chế độ riêng tư",
            "(?:chuyển sang|bật) (?:chế độ riêng tư|trò chuyện riêng tư)",
            "(?:đừng|không được|xin đừng) (?:lưu|lưu lại|ghi|ghi lại|nhớ) (?:chuyện này|cái này|điều này|cuộc trò chuyện này|gì hết|gì cả|tin nhắn của tôi)",
            "(?:chúng ta|mình) nói chuyện riêng(?: tư)?",
            "nói chuyện riêng tư",
            "chuyện này (?:chỉ )?giữa (?:tụi mình|chúng ta|hai ta|hai chúng ta)(?: thôi)?",
            "(?:dừng|ngừng) (?:lưu|ghi lại)",
        ),
        "on_whole": ("(?:đừng|không được) (?:lưu|ghi lại)", "giữ bí mật"),
        "off": (
            "bật lại (?:ghi|ghi lại|lưu|chế độ lưu)",
            "bật ghi lại",
            "thoát (?:khỏi )?(?:chế độ riêng tư|trò chuyện riêng tư)",
            "(?:tắt|kết thúc) (?:chế độ riêng tư|trò chuyện riêng tư)",
        ),
        "off_private": (
            "lưu lại(?: từ đây| từ giờ)?",
            "cho lưu lại",
            "cứ lưu bình thường",
            "hết riêng tư rồi",
            "trò chuyện bình thường",
            "(?:quay lại|trở lại) (?:bình thường|chế độ bình thường)",
        ),
        "status": (
            "(?:bạn )?có đang (?:lưu|ghi|ghi lại)(?: lại)?(?: không)?",
            "cuộc trò chuyện này (?:có )?được lưu(?: không| chứ)?",
            "(?:mình|chúng ta) đang ở chế độ riêng tư(?: à| không)?",
            "lưu hết (?:đúng )?không",
        ),
        "polite": ("làm ơn", "nhé", "nha", "nhe", "ok", "oke", "cảm ơn", "giùm", "vool"),
        "conj": ("nhưng", "và", "vì"),
        "reported": ("nói", "bảo", "viết", "hỏi", "nếu", "khi", "rằng"),
    },
    "hi": {
        "on_canonical": "निजी चैट",
        "off_canonical": "रिकॉर्डिंग फिर से चालू करें",
        "on": (
            "(?:निजी|प्राइवेट) चैट(?: (?:शुरू|चालू) (?:करो|करें|कीजिए|कर दो))?",
            "(?:इसे|इस चैट को|यह चैट) (?:निजी|प्राइवेट) (?:बनाओ|बनाइए|बनाएं|बना दो|कर दो|करो)",
            "(?:इसे|यह|ये|यह बात|इस चैट को|कुछ भी) (?:मत सहेजो|मत सहेजिए|सेव मत करो|सेव मत कीजिए|रिकॉर्ड मत करो|रिकॉर्ड मत कीजिए|याद मत रखो|याद न रखें|याद मत रखिए)",
            "रिकॉर्डिंग बंद (?:करो|करें|कर दो|कर दीजिए|कीजिए)",
            "(?:यह|ये) (?:बात )?(?:सिर्फ )?हमारे बीच (?:में )?(?:ही )?रहे",
            "मैं (?:निजी|प्राइवेट) (?:बात|चैट) करना चाहत(?:ा|ी) हूँ",
            "(?:क्या )?हम (?:निजी|प्राइवेट) (?:में )?बात (?:करें|कर सकते हैं)",
        ),
        "on_whole": ("(?:मत सहेजो|मत सहेजिए|सेव मत करो|रिकॉर्ड मत करो)",),
        "off": (
            "रिकॉर्डिंग फिर से चालू (?:करें|करो|कर दो|कीजिए)",
            "(?:निजी|प्राइवेट) चैट (?:बंद|खत्म|ख़त्म|समाप्त) (?:करो|करें|कर दो|कीजिए)",
            "दोबारा सहेजना शुरू (?:करो|करें)",
        ),
        "off_private": (
            "फिर से (?:सहेजें|सहेजो|सेव करो)",
            "(?:वापस )?सामान्य (?:मोड|चैट)",
            "अब सब कुछ सहेजो",
            "सेव करना शुरू कर दो",
            "अब सेव कर सकते हो",
        ),
        "status": (
            "क्या (?:आप|तुम) (?:यह|ये|इसे) रिकॉर्ड कर रहे (?:हैं|हो)",
            "(?:क्या )?(?:यह|ये) (?:चैट )?(?:रिकॉर्ड|सेव) हो रह(?:ा|ी) है(?: क्या)?",
            "क्या (?:यह )?चैट सहेजी जा रही है",
            "क्या हम (?:निजी|प्राइवेट) चैट में हैं",
            "सब (?:सेव|सहेजा) हो रहा है",
        ),
        "polite": ("कृपया", "प्लीज़", "धन्यवाद", "ओके", "अब", "ज़रा", "vool"),
        "conj": ("लेकिन", "और", "क्योंकि", "पर"),
        "reported": ("कहा", "बोला", "बोली", "लिखा", "पूछा", "अगर", "जब", "कि"),
    },
    "ar": {
        "on_canonical": "محادثة خاصة",
        "off_canonical": "أعد تشغيل التسجيل",
        "on": (
            "(?:محادثة|دردشة) خاصة",
            "(?:وضع خاص|الوضع الخاص)",
            "(?:شغل|فعل|ابدأ) (?:المحادثة الخاصة|الوضع الخاص)",
            "اجعل (?:هذه المحادثة|المحادثة) خاصة",
            "لا (?:تحفظ|تسجل|تخزن|تتذكر) (?:هذا|هذه|ذلك|شيئا|أي شيء|المحادثة|هذه المحادثة|هذه الرسائل|رسائلي)",
            "لا تحفظ هذا في ذاكرتك",
            "(?:هذا )?بيني وبينك",
            "(?:يظل|يبقى) (?:هذا )?بيننا",
            "أريد التحدث (?:بخصوصية|على انفراد)",
            "(?:بدون|بلا) (?:تسجيل|حفظ)",
            "(?:توقف عن|أوقف) (?:التسجيل|الحفظ)",
        ),
        "on_whole": ("لا (?:تحفظ|تسجل)", "خليها خاصة", "بسرية"),
        "off": (
            "أعد تشغيل التسجيل",
            "(?:شغل|فعل) التسجيل (?:مرة أخرى|من جديد)",
            "(?:أوقف|أنه|أنهي|أغلق) (?:المحادثة الخاصة|الدردشة الخاصة|الوضع الخاص)",
            "يمكنك الحفظ من جديد",
            "استأنف التسجيل",
        ),
        "off_private": (
            "احفظ (?:من جديد|مجددا|مرة أخرى)",
            "استمر في الحفظ",
            "(?:عد إلى )?الوضع العادي",
            "المحادثة العادية",
            "حفظ مرة أخرى",
        ),
        "status": (
            "هل (?:تسجل|تحفظ) (?:هذا|هذه المحادثة|المحادثة)",
            "هل (?:هذه )?المحادثة خاصة",
            "هل يتم حفظ (?:ما أكتبه|هذه المحادثة|المحادثة)",
            "هل نحن في (?:وضع خاص|الوضع الخاص|محادثة خاصة)",
        ),
        "polite": ("من فضلك", "لو سمحت", "رجاء", "شكرا", "حسنا", "اوكي", "الآن", "vool"),
        "conj": ("لكن", "لأن"),
        "reported": ("قال", "قالت", "كتب", "كتبت", "سأل", "إذا", "عندما", "أن"),
    },
    "he": {
        "on_canonical": "צ'אט פרטי",
        "off_canonical": "הפעל שוב את ההקלטה",
        "on": (
            "צ'אט פרטי",
            "שיחה פרטית",
            "מצב פרטי",
            "(?:הפעל|תפעיל|הפעילי|תפעילי) (?:צ'אט פרטי|מצב פרטי)",
            "(?:תעשה|תעשי|עשה|עשי) את (?:זה|השיחה|הצ'אט) (?:פרטי|פרטית)",
            "(?:אל|לא) (?:תשמור|תשמרי|תקליט|תקליטי|תזכור|תזכרי|לשמור|להקליט) (?:את זה|כלום|את השיחה|את השיחה הזאת|את ההודעות שלי)",
            "(?:זה )?(?:נשאר|יישאר) בינינו",
            "רק בינינו",
            "(?:אני רוצה|אפשר) לדבר (?:בפרטי|בפרטיות)",
            "(?:בוא|בואי) נדבר (?:בפרטי|בפרטיות)",
            "בלי (?:לשמור|הקלטה|להקליט)",
            "(?:תפסיק|תפסיקי|הפסק) (?:להקליט|לשמור)",
        ),
        "on_whole": ("(?:אל|לא) (?:תשמור|תשמרי|תקליט|תקליטי)", "שיהיה פרטי"),
        "off": (
            "(?:הפעל|תפעיל|הפעילי|תפעילי) שוב את ההקלטה",
            "(?:צא|תצא|צאי|תצאי) מהצ'אט הפרטי",
            "(?:סיים|תסיים|סיימי|תסיימי) את הצ'אט הפרטי",
            "(?:בטל|תבטל|בטלי|תבטלי) את המצב הפרטי",
            "(?:אתה|את) (?:יכול|יכולה) לשמור שוב",
            "(?:תחזור|חזור|תחזרי|חזרי) להקליט",
        ),
        "off_private": (
            "(?:תשמור|שמור|תשמרי|שמרי) שוב",
            "לשמור שוב(?: מעכשיו)?",
            "(?:חזרה|חזור|תחזור) (?:לשיחה רגילה|למצב רגיל)",
            "מצב רגיל",
            "(?:תמשיך|המשך|תמשיכי) לשמור",
        ),
        "status": (
            "(?:אתה|את) (?:מקליט|שומר|מקליטה|שומרת) (?:את זה|את השיחה)",
            "(?:השיחה הזאת|הצ'אט הזה) (?:נשמר|נשמרת|מוקלט|מוקלטת)",
            "אנחנו בצ'אט פרטי",
            "הכל נשמר (?:פה|כאן)",
        ),
        "polite": ("בבקשה", "תודה", "אוקיי", "אוקי", "היי", "עכשיו", "vool"),
        "conj": ("אבל", "כי"),
        "reported": ("אמר", "אמרה", "כתב", "כתבה", "שאל", "שאלה", "אם"),
    },
    "ja": {
        "on_canonical": "プライベートチャット",
        "off_canonical": "記録を再開",
        "on": (
            "プライベートチャット(?:にして(?:ください)?|でお願いします|で|モード|を(?:オンにして|開始して)(?:ください)?)?",
            "(?:これ|これを|これは|この会話|この会話は|この会話を|このチャット|このチャットは|このチャットを|今の話|今の話は|ここからは)(?:保存|記録)しないで(?:ください|ね)?",
            "何も(?:保存|記録)しないで(?:ください)?",
            "(?:これは|これを|今の話は)?(?:メモ|覚え)(?:しないで|ないで)(?:ください)?",
            "ここだけの話(?:にして(?:ください)?|で(?:お願いします)?)?",
            "内緒で話したい",
            "ログを残さないで(?:ください)?",
            "プライベートな会話にしたい",
            "(?:記録|保存)を(?:止めて|オフにして|停止して)(?:ください)?",
        ),
        "on_whole": ("(?:保存|記録)しないで(?:ください)?",),
        "off": (
            "記録を再開(?:して(?:ください)?|します)?",
            "保存を再開(?:して(?:ください)?|します)?",
            "記録をオンにして(?:ください)?",
            "プライベートチャットを?終了(?:して(?:ください)?|します)?",
        ),
        "off_private": (
            "(?:また|もう一度)保存して(?:いいよ|ください)?",
            "保存していい(?:よ)?",
            "普通のチャットに戻して(?:ください)?",
            "いつも通り(?:に)?保存して(?:ください)?",
            "もう大丈夫、?保存して",
        ),
        "status": (
            "(?:これ|この会話|このチャット)(?:は)?(?:記録|保存)されて(?:る|いる|います|ます)(?:か)?",
            "保存されていますか",
            "今プライベートチャット(?:ですか|なの|中)?",
            "ログ残ってる",
        ),
        "polite": ("お願いします", "お願い", "ねえ", "じゃあ", "ちょっと", "vool"),
        "conj": (),
        "reported": ("言った", "言われた", "って", "と"),
    },
    "zh-Hans": {
        "on_canonical": "私密聊天",
        "off_canonical": "重新开启记录",
        "on": (
            "私密(?:聊天|模式)",
            "(?:开|开启|打开)私密(?:聊天|模式)",
            "(?:把|将)(?:这个)?聊天设(?:为|成)私密",
            "(?:不要|别|请勿|勿)(?:保存|记录|存|记下|记住)(?:这个|这些|这段对话|这次聊天|我说的话|任何内容)",
            "什么都(?:不要|别)(?:保存|记录|存)",
            "(?:这些|这个)别(?:保存|存|记)",
            "(?:咱们|我们)私聊",
            "这话就咱俩知道",
        ),
        "on_whole": ("(?:不要|别)(?:保存|记录|记下来)", "私聊"),
        "off": (
            "(?:重新)?(?:开启|打开)记录",
            "恢复(?:记录|保存)",
            "(?:退出|关闭|结束)私密(?:聊天|模式)",
            "不用私密了",
        ),
        "off_private": (
            "重新保存",
            "继续保存",
            "可以保存了",
            "回到普通聊天",
            "正常聊天就好",
            "开始保存",
        ),
        "status": (
            "你在(?:记录|保存)(?:这个)?吗",
            "(?:这个)?聊天(?:有)?在(?:被)?保存吗",
            "现在(?:是)?私密聊天(?:开着)?吗",
            "我们是在私密聊天吗",
            "我说的话都(?:保存|记录)了吗",
        ),
        "polite": ("请", "麻烦", "吧", "谢谢", "好吗", "vool"),
        "conj": (),
        "reported": ("说", "说过", "告诉"),
    },
    "ko": {
        "on_canonical": "비공개 채팅",
        "off_canonical": "기록 다시 켜기",
        "on": (
            "비공개 채팅(?:으로)?(?: 해줘| 해주세요| 켜줘| 켜주세요| 시작| 모드)?",
            "(?:이거|이건|이것은|이 대화는?|이 채팅은?|아무것도|여기 있는 얘기는|이 얘기는) (?:저장|기록)하지 마(?:세요|요)?",
            "기록 남기지 마(?:세요)?",
            "(?:이건 )?우리끼리만(?: 알자| 이야)?",
            "비밀로 해(?:줘|주세요)",
            "저장 안 되게 해(?:줘|주세요)",
            "이 대화(?:를)? 비공개로 해(?:줘|주세요)",
            "기록 꺼(?:줘|주세요)",
        ),
        "on_whole": ("(?:저장|기록)하지 마(?:세요)?",),
        "off": (
            "기록 다시 켜(?:기|줘|주세요)",
            "비공개 채팅(?:을)? (?:끝내줘|끝내주세요|종료|꺼줘|꺼주세요)",
            "저장 다시 켜(?:줘|주세요)",
            "다시 저장해도 돼(?:요)?",
        ),
        "off_private": (
            "다시 저장(?:해|해줘|해주세요)?",
            "이제 저장해도 돼(?:요)?",
            "일반 채팅으로 돌아가(?:자|줘)",
            "원래대로 저장해(?:줘|주세요)?",
        ),
        "status": (
            "(?:이거|지금|이 채팅|이 대화)(?:는)? (?:기록|저장)되고 있(?:어|나요|어요|니)",
            "지금 비공개 채팅이(?:야|에요|예요|니)",
            "다 저장되는 거야",
        ),
        "polite": ("제발", "저기", "그럼", "이제", "고마워", "감사합니다", "vool"),
        "conj": (),
        "reported": ("말했", "했어", "라고"),
    },
}


def supported_locales() -> tuple[str, ...]:
    return tuple(LANGUAGES)


# ── folding ───────────────────────────────────────────────────────────────────

_FOLD_MAP = str.maketrans(
    {
        "ı": "i", "ł": "l", "đ": "d", "ø": "o",
        "ى": "ي", "ة": "ه", "ـ": None,
        "’": "'", "‘": "'", "ʼ": "'", "`": "'", "׳": "'",
        "ँ": "ं", "़": None,
    }
)


def fold(text: str) -> str:
    """Case, accents and spelling variants a person leaves out, removed; spaces collapsed per line.

    Marks are dropped only on Latin, Greek, Cyrillic, Hebrew and Arabic letters (an accent or a
    vowel point); Devanagari vowel signs, Japanese voicing marks and Korean syllables are kept.
    """
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    kept: list[str] = []
    base = 0
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.combining(ch):
            if base < 0x0900 or 0x1E00 <= base <= 0x1EFF:
                continue
        else:
            base = ord(ch)
        kept.append(ch)
    folded = unicodedata.normalize("NFC", "".join(kept)).translate(_FOLD_MAP)
    return "\n".join(" ".join(line.split()) for line in folded.splitlines() if line.strip())


def _trim(text: str) -> str:
    """Strip spaces, punctuation and symbols (an emoji) from both ends; never a letter or a mark."""
    start, end = 0, len(text)
    while start < end and unicodedata.category(text[start])[0] in "PSZC":
        start += 1
    while end > start and unicodedata.category(text[end - 1])[0] in "PSZC":
        end -= 1
    return text[start:end]


# ── compiled matchers ─────────────────────────────────────────────────────────

_CLASSES = ("on", "on_whole", "off", "off_private", "status")


def _pattern(phrase: str, spaceless: bool) -> str:
    folded = fold(phrase)
    return folded.replace(" ", "") if spaceless else folded


@lru_cache(maxsize=1)
def _matchers() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spaceless in (False, True):
        group = {}
        locales = [tag for tag in LANGUAGES if (tag in _SPACELESS) == spaceless]
        for cls in _CLASSES:
            parts = [_pattern(p, spaceless) for tag in locales for p in LANGUAGES[tag][cls]]
            group[cls] = re.compile("^(?:" + "|".join(f"(?:{p})" for p in parts) + ")$") if parts else None
            group[cls + "_search"] = (
                re.compile(("" if spaceless else r"(?<!\w)") + "(?:" + "|".join(f"(?:{p})" for p in parts) + ")"
                           + ("" if spaceless else r"(?!\w)"))
                if parts else None
            )
        polite = sorted({_pattern(w, spaceless) for tag in locales for w in LANGUAGES[tag]["polite"]}, key=len, reverse=True)
        words = "|".join(re.escape(w) for w in polite)
        if spaceless:
            group["lead"] = re.compile(rf"^(?:(?:{words})[,!.、 ]*)+")
            group["tail"] = re.compile(rf"(?:[,!.、 ]*(?:{words}))+$")
        else:
            group["lead"] = re.compile(rf"^(?:(?:{words})(?:[ ,!.]+|$))+")
            group["tail"] = re.compile(rf"(?:[ ,!.]+(?:{words}))+$")
        out[spaceless] = group
    conj = sorted({fold(w) for tag in LANGUAGES for w in LANGUAGES[tag]["conj"]}, key=len, reverse=True)
    out["conj"] = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in conj) + r")(?!\w)")
    out["reported"] = {fold(w) for tag in LANGUAGES if tag not in _SPACELESS for w in LANGUAGES[tag]["reported"]}
    out["reported_spaceless"] = tuple(fold(w) for tag in _SPACELESS for w in LANGUAGES[tag]["reported"])
    return out


def _cores(candidate: str, group: dict[str, Any], spaceless: bool) -> tuple[str, ...]:
    """The candidate with its edge marks off, then also with polite words ("bitte") off."""
    text = _trim(candidate.replace("\n", " "))
    if spaceless:
        text = text.replace(" ", "")
    plain = text
    for _ in range(4):
        before = text
        text = _trim(group["lead"].sub("", text))
        text = _trim(group["tail"].sub("", text))
        if text == before:
            break
    return (plain, text) if text != plain else (plain,)


def _is(cls: str, candidate: str) -> bool:
    """True when the candidate, as typed or with polite words off, IS a phrase of ``cls``."""
    matchers = _matchers()
    for spaceless in (False, True):
        group = matchers[spaceless]
        pattern = group[cls]
        if pattern is None:
            continue
        if any(core and pattern.match(core) for core in _cores(candidate, group, spaceless)):
            return True
    return False


def _on(candidate: str) -> bool:
    return _is("on", candidate)


_SENTENCE_SPLIT = re.compile(r"[.!?;\n…。؟؛।॥]+")
_CLAUSE_SPLIT = re.compile(r"[,、،]")
_QUOTES_AND_COLONS = (":", '"', "'", "“", "”", "«", "»", "„", "「", "『")


def _reported(head: str) -> bool:
    head = head.rstrip()
    if not head or head.endswith(_QUOTES_AND_COLONS):
        return True
    matchers = _matchers()
    words = _trim(head).split()
    if words and words[-1] in matchers["reported"]:
        return True
    return head.replace(" ", "").endswith(matchers["reported_spaceless"])


def parse(text: str, *, currently_private: bool = False) -> tuple[str, str] | None:
    """``(action, rest)`` for a command in one of the other languages, or None.

    ``rest`` is the folded message without the command ("" when the message is only the command).
    """
    folded = fold(text)
    if not folded or len(folded) > 4000:
        return None
    whole = folded.replace("\n", " ")
    if _is("status", whole):
        return STATUS, ""
    if currently_private and _is("off_private", whole):
        return OFF, ""
    if _is("off", whole):
        return OFF, ""
    if _is("on", whole) or _is("on_whole", whole):
        return ON, ""
    sentences = [s for s in (_trim(part) for part in _SENTENCE_SPLIT.split(folded)) if s]
    if not sentences:
        return None
    if len(sentences) > 1:
        for index, sentence in enumerate(sentences):
            if _on(sentence):
                return ON, " ".join(sentences[:index] + sentences[index + 1:])
    first = sentences[0]
    clauses = _CLAUSE_SPLIT.split(first)
    if len(clauses) > 1 and _on(clauses[0]):
        return ON, _trim(" ".join([*clauses[1:], *sentences[1:]]))
    conj = _matchers()["conj"].search(first)
    if conj is not None and _on(first[: conj.start()]):
        return ON, _trim(" ".join([first[conj.end():], *sentences[1:]]))
    last = sentences[-1]
    clauses = _CLAUSE_SPLIT.split(last)
    if len(clauses) > 1 and _on(clauses[-1]) and not _reported(clauses[-2]):
        return ON, _trim(" ".join([*sentences[:-1], ",".join(clauses[:-1])]))
    return None


def mentions_privacy(text: str) -> bool:
    """Loose: any phrase that turns private chat on, anywhere in the message. Never switches
    anything; it only lets the model be told the chat is still being saved."""
    folded = fold(text).replace("\n", " ")
    if not folded:
        return False
    matchers = _matchers()
    for spaceless in (False, True):
        group = matchers[spaceless]
        haystack = folded.replace(" ", "") if spaceless else folded
        for cls in ("on", "on_whole"):
            pattern = group[cls + "_search"]
            if pattern is not None and pattern.search(haystack):
                return True
    return False


# ── the app language of a turn ───────────────────────────────────────────────


def ui_locale_of(source_context: Mapping[str, Any] | None) -> str:
    """The app language of this turn's page as a catalog tag; English when unknown."""
    context = source_context if isinstance(source_context, Mapping) else {}
    raw = str(context.get(UI_LOCALE_KEY) or "").strip()
    if not raw or raw.lower() == "en":
        return "en"
    try:
        from core.i18n.catalog import resolve_catalog_tag

        return resolve_catalog_tag(raw)
    except Exception:
        return "en"


def canonical(locale: str, which: str) -> str:
    """The phrase a reply quotes in ``locale``: ``which`` is "on" or "off"."""
    entry = LANGUAGES.get(locale)
    if entry is None:
        return "private chat" if which == "on" else "turn recording back on"
    return str(entry[f"{which}_canonical"])
