---
description: Kontakte — gespeicherte Personen und Endpunkte, mit einer Prüfung von Änderungen, die jede Bearbeitung schützt.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Kontakte

Die Kontaktliste ist ein lokales, strukturiertes Adressbuch (Personen, ihre Endpunkte und Zahlungsaliasse), das
VOOL nutzen kann, statt dich eine Adresse erneut eintippen zu lassen. Weil ein Adressbuch Zahlungen und
Nachrichten speist, sind Bearbeitungen daran **geschützt**: geprüft, bestätigt und protokolliert.

## Geschützte Bearbeitungen

Einen gespeicherten Kontakt zu ändern, ist ein Ablauf in zwei Schritten:

1. **Prüfen** — die Änderung wird vorbereitet und angezeigt. Wenn sich der Eintrag geändert hat, seit du ihn
   geöffnet hast (ein anderes Fenster, ein anderes Gerät), wird die Änderung mit *„changed after this was
   reviewed; review it again. Nothing changed.“* abgelehnt.
2. **Bestätigen** — die Änderung wird nur mit deiner PIN oder deinem Passwort im Prüfbildschirm übernommen.

Jede Ablehnung sagt klar, dass **nichts gespeichert oder geändert wurde** — weil nichts gespeichert oder
geändert wurde. Wenn deine PIN akzeptiert wurde, das Speichern selbst aber fehlschlug, sagt die Meldung genau das:
Die PIN wurde akzeptiert; speichere erneut, bevor die Bestätigung abläuft.

## Schutzmechanismen

* Eine Änderung pro Kontakt zur selben Zeit; eine begrenzte Zahl ausstehender Änderungen.
* Einen Kontakt zu löschen, schreibt den Verlauf nicht stillschweigend um: Verweise auf einen gelöschten Kontakt
  werden als *„That contact was deleted“* gemeldet — nie stillschweigend neu aufgelöst.
* Importe werden nie teilweise angewendet: Wenn die Quelle nicht gelesen werden konnte, wurde nichts importiert.

## Warum diese Gestaltung

Eine vertippte Zahlungsadresse bedeutet Geld, das nicht zurückzuholen ist. Den Prüfschritt gibt es, damit keine
Modellrunde, keine Massenbearbeitung und kein veraltetes Fenster ändern kann, auf wen „Alice“ verweist, ohne dass
ein Mensch genau den sich ergebenden Eintrag bestätigt.
