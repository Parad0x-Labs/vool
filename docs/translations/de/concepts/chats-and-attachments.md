---
description: Chats, private Chats und die Dateien und langen eingefügten Texte, die du einbringst: Grenzen, was aufbewahrt wird und was abgelehnt werden kann.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Chats und Anhänge

Ein Chat ist ein Gespräch mit VOOL. Chats werden auf deinem Computer aufbewahrt. Ein Chat kann mit
einem Projektordner verknüpft sein, sodass seine Werkzeuge in diesem Ordner arbeiten (siehe [Arbeitsbereiche](workspaces.md)),
und du kannst Dateien als Anhänge einbringen.

## Dateien anhängen

Nutze **Anhängen** neben dem Nachrichtenfeld oder ziehe Dateien darauf. Jede Datei erscheint als Chip und
geht mit der nächsten Nachricht mit, die du sendest. Die Zeile unter dem Feld zeigt die Grenzen:

* Bis zu 8 Dateien pro Nachricht, 10 MB pro Datei und 25 MB pro Nachricht.
* Text- und Codedateien sowie PNG-, JPEG-, GIF- und WebP-Bilder. PDF, Word (DOCX), Archive wie
  ZIP und Video kommen hinzu, wenn dieser Computer hat, was er zum Lesen braucht. Die Dateiauswahl
  bietet nur an, was dieser Computer lesen kann.

Auch was das Modell bekommt, ist begrenzt: bis zu 120.000 Zeichen einer Datei und 240.000 aller
Dateien einer Nachricht. Eine längere Datei wird am Ende einer Zeile abgeschnitten, und das Protokoll vermerkt, wo.
Wenn das Modell, das du nutzt, keine Bilder lesen kann, sagt der Chip das, und das Bild wird nicht an es gesendet.

Bevor ein Bild gespeichert wird, werden seine versteckten Angaben entfernt: Kamera- und Standortdaten (EXIF,
XMP, IPTC) sowie Text, der in PNG- und WebP-Dateien steckt.

Eine Datei, die du auswählst, wird für diese eine Nachricht verwendet. Wenn die Antwort fertig ist, wird der Inhalt
der Datei gelöscht, und ein Beleg darüber wird aufbewahrt.

## Lange eingefügte Texte

Füge 4.000 Zeichen oder mehr ein, und VOOL bewahrt den Text als Dokument des Chats statt als Nachricht auf,
genau so, wie er eingefügt wurde. **Vorschau** zeigt seinen Anfang. Ein Dokument bleibt beim Chat, bis du
es oder den Chat löschst. Spätere Fragen im selben Chat können es nutzen: VOOL gibt dem Modell bis zu
60.000 Zeichen aus den früheren Dokumenten des Chats, die neuesten zuerst, und sagt, wo er abgeschnitten hat.

## Wenn eine Datei abgelehnt wird

Eine abgelehnte Datei wird nie stillschweigend verworfen. Ihr Chip bleibt und sagt, warum, und die Nachricht
kann erst gesendet werden, wenn du die Datei entfernst oder es erneut versuchst: *Entferne den fehlgeschlagenen
Anhang oder versuche ihn erneut, bevor du sendest.* Häufige Gründe:

* **Zu groß oder zu viele Dateien.** Entferne einige oder sende sie in zwei Nachrichten.
* **Kein Typ, den VOOL liest, oder nicht das, was der Name sagt.** Eine Datei mit der Endung `.txt`, die in
  Wirklichkeit ein Bild oder ein Programm ist, oder ein PDF, für das dieser Computer kein Leseprogramm hat,
  wird mit ihrem Grund abgelehnt.
* **Zugangsdaten darin.** Eine Datei oder ein eingefügter Text, der so aussieht, als enthielte er einen gültigen
  Schlüssel oder ein gültiges Token, etwa einen privaten Schlüsselblock oder ein Zugriffstoken, wird abgelehnt,
  und nichts wird gespeichert. Entferne das Geheimnis und hänge die Datei dann erneut an oder füge den Text erneut ein.

Diese Ablehnungen haben eigene kurze Codes, die mit der Meldung angezeigt werden. Sie stehen nicht im
[Fehlerbuch](../ERROR_BOOK.md), das die anderen Fehler der App abdeckt.

## Private Chats

Sag „Privater Chat“, „Speicher das nicht“ oder „Das bleibt unter uns“, und der Chat speichert ab dieser
Nachricht nichts mehr: keine Nachrichten, keine Zusammenfassung, kein Gedächtnis. Ein Banner zeigt *Privater Chat:
nichts wird gespeichert*, und jede private Nachricht trägt ein kleines Gespenst. Private Nachrichten kommen
nach einem Neuladen nicht zurück, und VOOL kann sich später nicht an sie erinnern. Sag „Aufzeichnung wieder
einschalten“ oder drücke **Aufzeichnung wieder einschalten**, um den Chat ab der nächsten Nachricht wieder zu
speichern. Ein privater Chat bleibt privat, auch nachdem VOOL neu gestartet ist.

## Einen Chat mitnehmen

**Exportieren** in der Kopfzeile des Chats speichert den ganzen Chat als Text oder Markdown, jede Nachricht,
wobei angehängte Dateien nur mit ihrem Namen aufgeführt werden, nie mit ihrem Inhalt. Um einen Chat auf einen
anderen Computer zu bringen oder eine signierte Kopie aufzubewahren, siehe
[Sicherung und Wiederherstellung](../help/backup-and-recovery.md).
