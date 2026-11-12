---
description: Gdzie VOOL przechowuje swoją konfigurację i co oznaczają ustawienia.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Konfiguracja

## Lokalizacje

VOOL przechowuje ustawienia, czaty, pamięć i zapisy w jednym folderze „runtime home”:

| Instalacja | Folder runtime home |
| --- | --- |
| Aplikacja na macOS | `~/Library/Application Support/VOOL/runtime` |
| Archiwum tar dla Linuksa i instalacje ze źródeł | `~/.vool_runtime` |
| Windows (eksperymentalnie, ze źródeł) | `%LOCALAPPDATA%\VOOL` |

Jeśli przed uruchomieniem VOOL ustawisz `VOOL_HOME`, VOOL użyje zamiast tego wskazanego folderu. Logi startowe
podają używany folder.

Klucze **nie** są zapisywane w plikach ustawień ani w logach. Są przechowywane w postaci zaszyfrowanej w osobnym
pliku poświadczeń w tym folderze runtime home, czytelnym tylko dla twojego konta użytkownika; klucze, które VOOL
już wcześniej przechowywał w pęku kluczy macOS na Macu, pozostają tam. Zobacz
[Model bezpieczeństwa](../trust/security.md#klucze).

## Ustawienia

Ustawienia zmieniasz w aplikacji w sekcji **Ustawienia**. Te, które decydują o tym, co VOOL może robić:

* **Model** — aktywny model, od którego zależy, czy tura działa w trybie LOKALNIE, czy CHMURA.
* **Obszar roboczy** — folder projektu, w którym pracuje czat; pozostałe foldery, do których VOOL może sięgać,
  opisuje strona [Obszary robocze](../concepts/workspaces.md).
* **Tryb działania** — wybierany w menu obok pola wiadomości, a nie tutaj. Ręczny pyta
  przed każdą zmianą; Auto pozwala na codzienne edycje i polecenia, natomiast usuwanie,
  nadpisywanie, instalowanie, wydawanie pieniędzy i zmiana ustawień nadal pytają. Zobacz
  [Uprawnienia](../concepts/permissions.md).
* **Dostęp do sieci i krypto** — oba pozostają wyłączone, dopóki ich nie włączysz.

VOOL nie ma ustawienia telemetrii produktu, ponieważ nie wysyła żadnej telemetrii produktu.

## Resetowanie

Zamknij VOOL i przenieś folder runtime home w inne miejsce. Pliki obszaru roboczego pozostają nietknięte; VOOL
uruchamia się ponownie ze świeżym profilem. Twoje zapisane klucze zostają przeniesione razem z tym folderem, więc
dodaj je ponownie w **Ustawienia → Klucze API**. Klucze przechowywane w pęku kluczy macOS pozostają tam, dopóki
nie usuniesz ich w aplikacji Dostęp do pęku kluczy.
