---
description: Jak VOOL chroni twoje pliki i klucze API — granice obszaru roboczego, przechowywanie poświadczeń i to, do czego może sięgnąć wtyczka.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Model bezpieczeństwa

## Granice

* **Ograniczenia folderów** wyznaczają granice dostępu do plików. VOOL odczytuje i zmienia pliki w folderze
  projektu czatu. Poza nim VOOL może odczytywać widoczne foldery w twoim folderze domowym, a pliki zmieniać tylko
  w folderach Desktop, Downloads i Documents; ukryte foldery, dane aplikacji i systemu oraz własne dane VOOL
  nigdy nie są odczytywane. Zobacz [Obszary robocze](../concepts/workspaces.md).
* **Magazyn poświadczeń** przechowuje klucze. Klucze nie są zapisywane w plikach konfiguracyjnych ani w logach.
* **Potwierdzanie narzędzi** wyznacza granice zmian. W trybie Ręczny każda zmiana najpierw pyta; w trybie Auto
  codzienne edycje i polecenia mogą działać automatycznie, natomiast usuwanie, nadpisywanie, instalowanie,
  wydawanie pieniędzy i zmiana ustawień nadal pytają. Zobacz [Narzędzia](../concepts/tools.md).

## Twoja sieć lokalna

Domyślnie VOOL nasłuchuje tylko na tym komputerze. Gdy ustawisz nasłuchiwanie w sieci lokalnej, tylko ten
komputer jest traktowany jako ty. Ustawienia, pliki, klucze, narzędzia i funkcje prywatne odpowiadają tylko
jemu.

Inne urządzenie w sieci dostaje zwykły czat. Może też odczytywać jedną publiczną stronę internetową naraz.
Ta strona musi być w publicznym internecie: adresy na tym komputerze lub w twojej sieci lokalnej są odrzucane,
a każde przekierowanie jest sprawdzane w ten sam sposób. Wyszukiwanie w sieci, badania, przeglądarka,
wyceny, płatności i twoje płatne klucze działają tylko na tym komputerze.

## Most Discord

Most Discord odpowiada tylko swojemu właścicielowi. Właścicielem zostaje pierwszy użytkownik Discord, który
wyśle mu polecenie. Aby samodzielnie wybrać właściciela, ustaw `VOOL_DISCORD_OWNER_USER_ID` na swój
identyfikator użytkownika Discord; zrób to dla każdego kanału, w którym mogą pisać inne osoby. Wszyscy pozostali
jeden raz dostają informację, że ten VOOL jest zablokowany dla swojego właściciela, a ich wiadomości nie są
przetwarzane. Nawet wiadomości właściciela z Discord podlegają tym samym ograniczeniom co inne urządzenie
w sieci.

## Klucze

VOOL przechowuje klucze twoich dostawców w zaszyfrowanym pliku w swoim folderze runtime home na tym komputerze
(zobacz [Konfiguracja](../reference/configuration.md)), czytelnym tylko dla twojego konta użytkownika.
Klucze, które VOOL już wcześniej przechowywał w pęku kluczy macOS na Macu, nadal można tam odczytać. Klucz
jest używany z twojego komputera do łączenia się z twoim dostawcą. Nie jest wysyłany do Parad0x Labs, a na
drodze żądania nie ma żadnego punktu końcowego obsługiwanego przez Parad0x.

{% hint style="info" %}
Zaszyfrowany plik jest chroniony uprawnieniami plików twojego konta użytkownika, a nie osobnym
hasłem. Miej włączone szyfrowanie dysku (FileVault na Macu), aby zgubiony lub skradziony dysk nie
ujawnił twoich kluczy.
{% endhint %}

## Wtyczki

Wtyczka działa z dostępem środowiska uruchomieniowego. To właśnie czyni wtyczki użytecznymi i właśnie dlatego
warto je przeczytać przed instalacją. Zobacz [Umiejętności i wtyczki](../guides/skills-and-plugins.md).

## Zgłaszanie problemu

Problemy z bezpieczeństwem zgłaszaj prywatnie przez
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
nigdy w publicznym zgłoszeniu. Podaj wersję, platformę i kroki pozwalające odtworzyć problem.

{% hint style="warning" %}
Aplikacje na macOS są podpisane ad hoc i nie są poświadczone notarialnie; archiwum tar dla Linuksa nie jest
podpisane. Zobacz [Stan wydań](release-status.md).
{% endhint %}
