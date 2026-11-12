---
description: Obszar roboczy to folder projektu, w którym pracuje VOOL. Poza nim VOOL może odczytywać widoczne foldery w twoim folderze domowym, a pliki zmieniać tylko w folderach Desktop, Downloads i Documents.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Obszary robocze

Obszar roboczy to folder projektu, w którym pracuje czat. VOOL odczytuje i zmienia tam pliki,
pytając najpierw zawsze wtedy, gdy wymaga tego twój tryb działania (zobacz [Narzędzia](tools.md)).

## Dodawanie i usuwanie

Wybierz folder przyciskiem **Wybierz folder projektu…** w czacie albo przyciskiem **+ Projekt** na
pasku bocznym czatu. Usunięcie projektu sprawia, że VOOL zapomina, czego się w nim nauczył, i przenosi jego czaty
z powrotem do sekcji Ogólne. Twoje pliki pozostają nietknięte.

## Twoje pozostałe foldery

Poza obszarem roboczym narzędzia plikowe VOOL mogą też sięgać do twojego folderu domowego, ze stałymi ograniczeniami:

| Foldery | Co może VOOL |
| --- | --- |
| Desktop, Downloads, Documents | Odczytywać i zmieniać pliki, pytając najpierw tak, jak określa twój tryb działania |
| Pictures, Movies (w Linuksie Videos), Music | Tylko odczytywać: wyświetlać zawartość, wyszukiwać, otwierać i mierzyć, nigdy nie zmieniać |
| Pozostałe widoczne foldery w folderze domowym, takie jak `~/Projects` | Tylko odczytywać |
| Ukryte foldery (takie jak `~/.ssh`), dane aplikacji i systemu (`Library`, `Applications`, `AppData`), własny folder danych VOOL | Nic |

VOOL zagląda do tych folderów tylko wtedy, gdy wymaga tego żądanie, na przykład „znajdź mój PDF z podatkami”
albo „który film w Movies jest największy”. Nie indeksuje twojego dysku w tle.

## Wskazówki dotyczące zakresu

* Kieruj obszar roboczy na projekt, a nie na swój folder domowy.
* Pliki z poświadczeniami, materiał kluczy i magazyny haseł trzymaj w menedżerze haseł albo w
  ukrytym folderze, a nie luzem w folderach Desktop, Downloads, Documents czy w folderze projektu.
* Używaj osobnego projektu dla każdej pracy, aby kontekst z jednej nie trafiał do drugiej.

{% hint style="warning" %}
Wszystko, co VOOL odczyta, w folderze projektu albo w twoich folderach domowych, może trafić do tego, co
jest wysyłane do twojego dostawcy, gdy wybrany jest model w chmurze. Ustalaj zakres świadomie.
{% endhint %}
