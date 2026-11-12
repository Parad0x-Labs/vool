---
description: Jak podłączyć model AI do VOOL: model lokalny działający na twoim komputerze albo dostawcę chmurowego z twoim własnym kluczem API.
translation: machine
source_sha256: fdc85c0cf7cf8399666b8dd3d49ddbfb7cbe65ca7800be4630d88bd86053987a
---

# Podłączanie modelu

VOOL nie zawiera żadnego modelu. Wybierasz go sam, a ten wybór decyduje, czy żądanie
zostaje na twoim komputerze, czy go opuszcza.

## Lokalnie

Jeśli lokalne środowisko uruchomieniowe jest zainstalowane i działa, VOOL je wykrywa i wyświetla listę jego modeli.
Wybranie jednego z nich sprawia, że wskaźnik pokazuje Lokalnie. Szczegóły konfiguracji są w sekcji
[Uruchamianie modelu lokalnego](../guides/local-models.md).

## Chmura

Dodaj klucz dostawcy w **Ustawienia → Klucze API**. Klucz jest przechowywany w zaszyfrowanym pliku na tym
komputerze (zobacz [Model bezpieczeństwa](../trust/security.md#klucze)) i służy do wysyłania żądań
**z twojego komputera bezpośrednio do dostawcy**.

Wybranie modelu w chmurze sprawia, że wskaźnik pokazuje nazwę dostawcy, i zmienia się on, zanim
zostanie wysłane jakiekolwiek żądanie.

Proces dotyczący konkretnie OpenRouter opisuje sekcja [Podłączanie OpenRouter](../guides/openrouter.md).

## Przełączanie

Zmiana modelu zmienia tryb. Wskaźnik jest rozstrzygający: jeśli pokazuje Lokalnie,
następne żądanie jest obsługiwane na tym komputerze.
