---
description: Które modele AI działają z VOOL — modele lokalne na twoim własnym sprzęcie i modele w chmurze przez twoje konto u dostawcy.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Obsługiwane modele

VOOL nie zawiera żadnego modelu — pierwszy model lokalny jest pobierany, gdy użyjesz go po raz pierwszy.
Łączy się z wybranymi przez ciebie środowiskami uruchomieniowymi i dostawcami.

## Lokalne środowiska uruchomieniowe

Ollama, llama.cpp, vLLM i MLX są wykrywane automatycznie. Inne środowiska uruchomieniowe, które udostępniają
na localhost punkt końcowy zgodny z OpenAI, można wskazać ręcznie.

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
