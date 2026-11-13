---
description: Jak podłączyć model AI do VOOL: model lokalny działający na twoim komputerze albo dostawcę chmurowego z twoim własnym kluczem API.
translation: machine
source_sha256: 53f84267e6c9d76240039e6119dfb654fc0e2f479f3bd293ad48232d2df0dd01
---

# Podłączanie modelu

VOOL nie zawiera żadnego modelu. Domyślnie wybiera model lokalny, a ty możesz wybrać inny;
ten wybór decyduje, czy żądanie zostaje na twoim komputerze, czy go opuszcza.

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
