---
description: So verbindest du ein KI-Modell mit VOOL: ein lokales Modell auf deinem eigenen Computer oder einen Cloud-Anbieter mit deinem eigenen API-Schlüssel.
translation: machine
source_sha256: fdc85c0cf7cf8399666b8dd3d49ddbfb7cbe65ca7800be4630d88bd86053987a
---

# Ein Modell verbinden

VOOL bringt kein Modell mit. Du wählst eins aus, und diese Wahl entscheidet, ob eine Anfrage
auf deinem Rechner bleibt oder ihn verlässt.

## Lokal

Wenn eine lokale Laufzeitumgebung installiert ist und läuft, erkennt VOOL sie und listet ihre Modelle auf.
Wählst du eins davon aus, zeigt die Anzeige Lokal. Details zur Einrichtung findest du unter
[Ein lokales Modell ausführen](../../../guides/local-models.md).

## Cloud

Füge unter **Einstellungen → API-Schlüssel** einen Anbieterschlüssel hinzu. Der Schlüssel wird in einer
verschlüsselten Datei auf diesem Computer aufbewahrt (siehe [Sicherheitsmodell](../../../trust/security.md#keys))
und wird verwendet, um Anfragen **von deinem Rechner direkt an den Anbieter** zu senden.

Wählst du ein Cloud-Modell aus, zeigt die Anzeige den Namen des Anbieters, und sie wechselt, bevor
eine Anfrage gestellt wird.

Den Ablauf speziell für OpenRouter beschreibt [OpenRouter verbinden](../../../guides/openrouter.md).

## Wechseln

Ein Modellwechsel wechselt den Modus. Die Anzeige ist maßgeblich: Zeigt sie Lokal,
wird die nächste Anfrage auf diesem Computer verarbeitet.
