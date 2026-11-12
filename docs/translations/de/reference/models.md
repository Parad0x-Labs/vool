---
description: Welche KI-Modelle mit VOOL funktionieren — lokale Modelle auf deiner eigenen Hardware und Cloud-Modelle über dein Konto bei einem Anbieter.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Unterstützte Modelle

VOOL bringt kein Modell mit. Es verbindet sich mit Laufzeitumgebungen und Anbietern, die du wählst.

## Lokale Laufzeitumgebungen

Jede Laufzeitumgebung, die einen OpenAI-kompatiblen Endpunkt auf localhost bereitstellt, ist nutzbar. Die
gängigen werden automatisch erkannt.

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
