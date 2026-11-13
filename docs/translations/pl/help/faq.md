---
description: Częste pytania o VOOL — czy działa offline, gdzie przechowywany jest twój klucz API, czy jest darmowy i jakie systemy obsługuje.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# FAQ

## Czy VOOL działa offline?

Tak, z modelem lokalnym, dla twoich czatów i plików. Modele w chmurze z definicji wymagają połączenia
z siecią. Bieżące informacje, takie jak pogoda czy ceny, pochodzą z publicznych usług internetowych tylko wtedy, gdy włączone jest „Wyszukiwanie w sieci” (po świeżej instalacji jest wyłączone), i nigdy w trybie „Tylko lokalnie”.

## Czy potrzebuję klucza do chmury?

Nie. Do korzystania z aplikacji wystarczy model lokalny.

## Gdzie przechowywany jest mój klucz?

W zaszyfrowanym pliku w folderze runtime home VOOL na twoim komputerze, czytelnym tylko dla twojego konta
użytkownika. Klucze, które VOOL już wcześniej przechowywał w pęku kluczy macOS na Macu, pozostają tam. Klucze
pozostają na twoim komputerze. Zobacz [Model bezpieczeństwa](../trust/security.md#klucze).

## Czy Parad0x widzi moje prompty?

Nie w trybie lokalnym i również nie w trybie chmury — żądania do chmury idą z twojego komputera do
twojego dostawcy. Zobacz [Przetwarzanie danych](../trust/data-handling.md).

## Dlaczego system ostrzega mnie, gdy otwieram aplikację?

Aplikacje na macOS są podpisane ad hoc, ale nie są jeszcze poświadczone notarialnie, więc macOS prosi
o potwierdzenie pierwszego otwarcia. Kroki opisuje strona [Instalacja](../getting-started/install.md#pierwsze-otwarcie-na-macu).
Zobacz [Stan wydań](../trust/release-status.md).

## Czy mogę używać własnego modelu?

Tak. Dowolnego środowiska uruchomieniowego, które udostępnia lokalny punkt końcowy zgodny z OpenAI. Zobacz
[Obsługiwane modele](../reference/models.md).

## Czy jest darmowy?

Aplikacja jest darmowa w okresie bety. Za korzystanie z modeli w chmurze rozlicza cię twój
dostawca, na twoim koncie.
