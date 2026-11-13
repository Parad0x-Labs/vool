---
description: Genau welche Daten VOOL sendet, wann und an wen. Was auf deinem Computer bleibt und was deinen KI-Anbieter erreicht.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Umgang mit Daten

## Die zwei Zustände

**Lokal** — das Modell und deine Dateien bleiben auf diesem Computer. Aktuelle Fakten wie
Wetter oder Preise kommen nur von öffentlichen Webdiensten, solange Websuchen eingeschaltet ist (bei einer frischen Installation ist es aus), und nie in Nur lokal.

**Cloud** — der ausgewählte Kontext wird direkt an den ausgewählten Anbieter gesendet.

Die Kopfzeile des Chats zeigt, auf welchem Weg die nächste Anfrage läuft — zum Beispiel **Auto** mit
**Lokal zuerst**, wenn VOOL Auto von diesem Computer aus antwortet.

## Was VOOL nicht tut

* Er leitet deine Prompts nicht über die Infrastruktur von Parad0x Labs.
* Er lädt deinen Arbeitsbereich nicht hoch.
* Er sendet deinen Schlüssel nirgendwohin außer an den Anbieter, zu dem er gehört.

## OpenRouter im Besonderen

Dein OpenRouter-Konto bleibt deins. VOOL sendet Anfragen direkt von diesem Computer
mit deinem Schlüssel. Parad0x leitet deine Prompts nicht weiter und bezahlt deine Nutzung nicht.
Echte VOOL-Nutzung kann in den öffentlichen, zusammengefassten OpenRouter-Statistiken von VOOL erscheinen,
aber einzelne Prompts und Identitäten werden nicht veröffentlicht.

## Telemetrie

VOOL sendet keine Produkttelemetrie. Ein Fehlerbericht verlässt deinen Rechner nur, wenn du ihn dir
in der Vorschau ansiehst und dich entscheidest, ihn zu senden; siehe [Fehlerberichte und Datenschutz](bug-reporting.md).

## Es gelten die Bedingungen deines Anbieters

Im Cloud-Modus erreichen deine Daten deinen Anbieter unter **dessen** Bedingungen für Aufbewahrung und
Training, nicht unter denen von VOOL. Lies die Bedingungen des Anbieters, den du auswählst.
