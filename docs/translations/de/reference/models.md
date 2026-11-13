---
description: Welche KI-Modelle mit VOOL funktionieren — lokale Modelle auf deiner eigenen Hardware und Cloud-Modelle über dein Konto bei einem Anbieter.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Unterstützte Modelle

VOOL bringt kein Modell mit — das erste lokale Modell wird heruntergeladen, wenn du es zum ersten Mal
verwendest. Es verbindet sich mit Laufzeitumgebungen und Anbietern, die du wählst.

## Lokale Laufzeitumgebungen

Ollama, llama.cpp, vLLM und MLX werden automatisch erkannt. Andere Laufzeitumgebungen, die einen
OpenAI-kompatiblen Endpunkt auf localhost bereitstellen, kannst du von Hand eintragen.

## Cloud-Anbieter

Cloud-Modelle werden mit deinem Schlüssel von deinem Rechner aus erreicht. Wählst du eines aus, wechselt die
App in den Cloud-Modus (die Modusanzeige zeigt den Namen des Anbieters) — siehe [Lokal und Cloud](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) ist die breiteste einzelne Verbindung: ein Schlüssel, viele
Anbieter.

## Auswahl

| Arbeit | Sinnvolle Wahl |
| --- | --- |
| Private Dateien, kein Netzwerk | Lokal, 7B oder größer |
| Alltägliches Programmieren | Lokales Programmiermodell mit 7–14B |
| Langes Schlussfolgern | Ein größeres Cloud-Modell |
| Kostenkontrolle | Zuerst lokal, Cloud, wenn es sich lohnt |
