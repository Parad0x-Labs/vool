---
description: Ein Beleg ist die Aufzeichnung dessen, was ein Lauf tatsächlich getan hat.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Belege

Ein Beleg ist die dauerhafte Aufzeichnung eines Laufs: welche Werkzeuge aufgerufen wurden, mit welchen Eingaben,
und was jedes davon zurückgegeben hat.

## Warum es sie gibt

Die Zusammenfassung, die ein Agent von seiner eigenen Arbeit gibt, ist eine Behauptung. Der Beleg ist die
Aufzeichnung, an der du diese Behauptung prüfen kannst.

## Was darin steht

| Feld | Bedeutung |
| --- | --- |
| `tool` | Welche Aktion lief |
| `input` | Was sie bekommen hat |
| `result` | Was sie zurückgegeben hat |
| `time` | Wann sie lief |

Belege werden im „runtime home“ von VOOL auf diesem Computer aufbewahrt (siehe
[Konfiguration](../reference/configuration.md)), nicht in deinen Projektordnern. Ein Beleg verlässt deinen
Rechner nur in einem Fehlerbericht, den du dir in der Vorschau ansiehst und zu senden beschließt (siehe
[Fehlerberichte und Datenschutz](../trust/bug-reporting.md)).
