---
description: Które modele AI działają z VOOL — modele lokalne na twoim własnym sprzęcie i modele w chmurze przez twoje konto u dostawcy.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Obsługiwane modele

VOOL nie zawiera żadnego modelu. Łączy się z wybranymi przez ciebie środowiskami uruchomieniowymi i dostawcami.

## Lokalne środowiska uruchomieniowe

Można używać dowolnego środowiska uruchomieniowego, które udostępnia na localhost punkt końcowy zgodny z OpenAI.
Popularne środowiska są wykrywane automatycznie.

## Dostawcy chmurowi

Z modelami w chmurze VOOL łączy się z użyciem twojego klucza, z twojego komputera. Wybranie jednego z nich
przełącza aplikację w tryb chmury (wskaźnik pokazuje nazwę dostawcy) — zobacz [Lokalnie i w chmurze](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) to najszersze pojedyncze połączenie: jeden klucz, wielu
dostawców.

## Wybór

| Praca | Rozsądny wybór |
| --- | --- |
| Prywatne pliki, bez sieci | Model lokalny, 7B lub większy |
| Codzienne programowanie | Lokalny model do programowania 7–14B |
| Długie rozumowanie | Większy model w chmurze |
| Kontrola kosztów | Najpierw lokalnie, chmura wtedy, gdy się opłaca |
