---
description: Die fünf Modi, was jeder davon VOOL ohne Nachfrage erlaubt und was jede Freigabeschaltfläche abdeckt.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Berechtigungen

VOOL entscheidet, was laufen darf, bevor etwas läuft. Das Modell kann eine Aktion anfordern; der Modus, den du
gewählt hast, und deine Antworten entscheiden, ob sie stattfindet. Lesen wird anders behandelt als Ändern, und
Geheimnisse, Zahlungen und Sicherheitseinstellungen sind in jedem Modus geschützt.

## Die Modi

Wähle den Modus im Menü neben dem Nachrichtenfeld. Ein neuer Chat startet in **Manuell**.

| Modus | Läuft ohne Nachfrage | Fragt vorher | Nie |
| --- | --- | --- | --- |
| Manuell | Dateien lesen und auflisten, sichere Befehle und öffentliche, rein lesende Websuchen | Jede Änderung: Dateibearbeitungen, Befehle, Netzwerkaktionen, Einstellungen, Git, Deployment und Nachrichten | Gespeicherte Geheimnisse lesen |
| Bearbeitungen prüfen | Dasselbe wie Manuell | Dasselbe wie Manuell. Jeder Stapel von Bearbeitungen wird als prüfbare Änderung gezeigt, bevor er angewendet wird | Gespeicherte Geheimnisse lesen |
| Planen | Dateien lesen und auflisten sowie sichere Befehle | Nichts: Er liest nur und schlägt einen Plan vor | Jede Änderung, jede Netzwerkaktion |
| Auto | Dateien lesen, erstellen und bearbeiten, Befehle, Netzwerk- und Browseraktionen sowie Modellanbieter | Löschen, vorhandene Dateien überschreiben, Abhängigkeiten installieren, Anbietereinstellungen ändern, Git-Commit, -Push, -Merge und -Reset, Deployment, Nachrichten senden, Zahlungen und Einstellungen ändern | Gespeicherte Geheimnisse lesen, Sicherheitseinstellungen ändern und jede Aktion, deren Wirkung VOOL nicht bestimmen kann |
| Berechtigungen umgehen | Alles außer den vier geschützten Klassen | Zahlungen | Gespeicherte Geheimnisse lesen, Sicherheitseinstellungen ändern und jede Aktion, deren Wirkung VOOL nicht bestimmen kann |

Ein Modus, ein Skill oder ein Plugin kann das Angebot nur einschränken. Keiner von ihnen gewährt eine Aktion,
die der Modus nicht erlaubt.

## Auf eine Berechtigungsanfrage antworten

Wenn eine Aktion dich braucht, öffnet sich über dem Nachrichtenfeld eine Leiste **Freigabe erforderlich**. Sie
nennt die genaue Aktion: die Befehlszeile, die laufen wird, oder die Dateien, die sich ändern werden, ob sich die
Änderung rückgängig machen lässt, und eine Vorschau der Bearbeitungen. Nichts läuft, bevor du antwortest, und die
Anfrage wartet auf dich, auch wenn VOOL neu startet. Eine Anfrage, deren Zeit abgelaufen ist, wird als abgelaufen
angezeigt und kann nicht mehr freigegeben werden.

Die Leiste bietet nur die Möglichkeiten an, die zur Aktion passen:

| Schaltfläche | Was sie erlaubt |
| --- | --- |
| Einmal erlauben | Genau diese eine Aktion. |
| Für diese Aufgabe erlauben | Dieselbe genaue Aktion erneut während dieser Aufgabe, im selben Modus. Wird für Aktionen angeboten, die keine Dateibearbeitungen sind. |
| Alle *N* geplante Änderungen für diese Anfrage erlauben | Die Dateiänderungen, die diese Anfrage bereits geplant hat, genau die unter „Details ansehen“ aufgeführten Dateien und nichts anderes. |
| Arbeitsbereich-Bearbeitungen in diesem Chat erlauben | Lesen, neue Dateien und Bearbeitungen innerhalb des Arbeitsbereichs dieses Chats, für acht Stunden oder bis VOOL neu startet oder der Chat gelöscht wird. |
| In diesem Projekt erlauben | Lesen und Schreiben von Dateien innerhalb des Projektordners. Das bleibt für das Projekt bestehen, auch nach einem Neustart und in neuen Chats. |
| Details ansehen | Zeigt die ganze Anfrage, bevor du entscheidest. |
| Ablehnen | Die Aktion läuft nicht. |

Was du auch erlaubst: Löschen, Verschieben, Überschreiben, Befehle, Netzwerkaktionen, Geheimnisse und Zahlungen
fragen weiterhin jedes Mal einzeln, sofern der Modus selbst sie nicht erlaubt.

## Berechtigungen umgehen

Die Umgehung führt die verfügbaren Werkzeuge ohne einzelne Nachfragen aus, innerhalb eines Geltungsbereichs und
für eine Zeit, die du wählst:

* **Geltungsbereich:** nur die aktuelle Aufgabe, diese Chat-Sitzung oder dieses Projekt.
* **Dauer:** 15 oder 30 Minuten, 1, 2, 4 oder 8 Stunden, eine eigene Dauer von 15 bis 1440 Minuten
  oder *Bis ich es abschalte*, nur für diesen Chat.

Eine zeitlich begrenzte Umgehung funktioniert in jedem Chat. *Bis ich es abschalte* setzt voraus, dass der Chat mit
einem Projektordner verknüpft ist, und dieser Ordner darf nicht dein ganzer persönlicher Ordner, die oberste Ebene
der Festplatte oder ein Ordner sein, der die eigenen Daten von VOOL enthält. Solange die Umgehung an ist, zeigt ein
Banner ihren Geltungsbereich und wann sie endet, und sein **×** schaltet sie sofort ab; der Chat kehrt zu Manuell
zurück. Wird der Chat gelöscht, endet auch seine Umgehung. Die Umgehung gewährt nie Zugriff außerhalb des
Arbeitsbereichs und kann einem Modell nicht erlauben, seine eigenen Berechtigungen zu erweitern. Zahlungen fragen
weiterhin, und Geheimnisse und Sicherheitseinstellungen bleiben unerreichbar.

## Zahlungen und Ausgaben

Eine Aktion freizugeben, gibt nie eine Zahlung frei. Zahlungen laufen in jedem Modus über den eigenen Vorschlag
und die eigene Freigabe der Wallet, mit deren Limits. Bezahlte Modellaufrufe werden von deinen Ausgabenlimits und
deinem Preisschutz geprüft, bevor der Anbieter kontaktiert wird. Siehe [Wallet](../guides/wallet.md) und
[Ausgabenlimits und Preisschutz](../guides/spending-limits.md).

## Wenn eine Aktion abgelehnt wird

Eine abgelehnte Aktion hat nichts geändert, und die Meldung sagt, was abgelehnt wurde und warum. Die Codes, die du
sehen kannst, sind [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) für eine Änderung außerhalb des erlaubten
Ordners und [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) für ein Werkzeug, das dieser Modus nicht
anbietet. Siehe [Fehler und Fehlercodes](../reference/errors.md).
