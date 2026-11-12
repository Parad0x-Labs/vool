---
description: Wie du einen Fehler in VOOL liest, ob schon etwas passiert ist und wo jeder Code erklärt wird.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Fehler und Fehlercodes

Wenn etwas fehlschlägt, sagt VOOL in einfachen Worten, was passiert ist, was womöglich schon passiert ist
und was als Nächstes zu tun ist. Jeder Fehler hat außerdem einen festen Code. Du findest ihn in den
technischen Details unter einem Fehler, in der Befehlszeile (`wallet_export_refused: …`) und in
API-Antworten. Diese Seite erklärt, wie du einen Fehler liest. Das [Fehlerbuch](../ERROR_BOOK.md) erklärt
jeden Code.

## Einen Fehler lesen

Prüfe drei Dinge, bevor du es erneut versuchst:

1. **Was passiert ist.** Die Meldung. Sie enthält nie interne Pfade oder Geheimnisse.
2. **Was womöglich schon passiert ist.** Die meisten Ablehnungen greifen, bevor etwas gesendet oder geändert
   wird, und die Meldung sagt das. Manche Fehler können nicht wissen, ob eine Aktion abgeschlossen wurde, etwa
   eine Zeitüberschreitung, nachdem eine Anfrage hinausgegangen ist. Dann sagt die Meldung, dass das Ergebnis
   unbekannt ist, und bittet dich, es zu prüfen, bevor du es erneut versuchst. Eine Zahlung wird nie
   versehentlich zweimal gesendet: Ein zweites Senden derselben Zahlung wird mit
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) abgelehnt.
3. **Was als Nächstes zu tun ist.** Ein konkreter Schritt.

Der Code trägt die Bedeutung. Ein HTTP-Status wie 403 oder 429 sagt nur, wie es einer Anfrage unterwegs
ergangen ist, und zwei verschiedene Ablehnungen können denselben Status haben. Lies den Code, nicht den
Status.

## Codes und das Fehlerbuch

Ein Code wird nie umbenannt und nie für eine andere Bedeutung wiederverwendet. Das
[Fehlerbuch](../ERROR_BOOK.md) wird aus dem eigenen Fehlerkatalog der App erzeugt, daher sind die Codes,
Meldungen und nächsten Schritte dort genau das, was die App anzeigt. Jeder Code hat einen eigenen Abschnitt:
Suche auf der Seite nach dem Code, so wie er dir angezeigt wurde, oder verlinke den Abschnitt mit der Adresse
des Buchs, `#` und dem Code, in dem Unterstriche durch Bindestriche ersetzt sind, etwa
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Die Codes, die du am ehesten siehst:

| Code | Titel | Was womöglich schon passiert ist |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Berechtigung verweigert | Nichts wurde geändert. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Anbieter nicht erreichbar | Nichts wurde gesendet. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Limit des Anbieters erreicht | Die Anfrage hat den Anbieter erreicht. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Zeitüberschreitung | Unbekannt: Prüfe, bevor du es erneut versuchst. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Abgebrochen | Unbekannt: Ein Teil davon ist womöglich schon gelaufen. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Gespeicherter Schlüssel ließ sich nicht lesen | Nichts wurde gesendet oder geändert. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Unerwarteter Fehler | Unbekannt: Prüfe, bevor du es erneut versuchst. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Freigabe passt nicht mehr zur Vorschau | Nichts wurde signiert oder gesendet. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Zahlung konnte nicht übertragen werden | Unbekannt: Prüfe den Status der Zahlung, bevor du es erneut versuchst. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Zahlung bereits vorgeschlagen oder gesendet | Nichts Neues wurde gesendet; die frühere Zahlung ist festgehalten. |

## Fehler und andere Ergebnisse

VOOL hält diese Fälle auseinander, und seine Formulierungen tun es auch:

* **Ein Fehler.** Etwas ist fehlgeschlagen. Code und Meldung sagen, was, und was zu tun ist.
* **Wartet auf dich.** Eine Berechtigungsanfrage oder eine Zahlungsfreigabe ist offen. Nichts ist gelaufen,
  und nichts wird laufen, bis du antwortest. Siehe [Berechtigungen](../concepts/permissions.md).
* **Von einem Ausgabenlimit oder Preisschutz gestoppt.** Die Ablehnung geschieht, bevor der Anbieter
  kontaktiert wird, und die Meldung deutet nie an, der Anbieter sei gefragt worden. Siehe
  [Ausgabenlimits und Preisschutz](../guides/spending-limits.md).
* **Abgebrochen.** Es wurde von dir oder von VOOL gestoppt, bevor es fertig war. Ein Teil davon ist womöglich
  schon gelaufen, prüfe also, was sich geändert hat, bevor du es erneut startest.
* **Hier nicht möglich.** Dieser Build oder dieser Rechner kann es nicht, und die Meldung nennt, was es kann:
  etwa bei einem Zahlungsangebot, dessen Verfahren VOOL nicht von Anfang bis Ende prüfen kann, oder bei einem
  optionalen Extra, das nicht installiert ist.
* **Ergebnis unbekannt.** Die Aktion ist womöglich abgeschlossen worden, womöglich nicht, etwa eine Zahlung,
  deren Übertragung nie bestätigt wurde. Prüfe zuerst ihren Status. VOOL zahlt nie blind ein zweites Mal.

## Fehler mit eigenen Codes

Einige Teile von VOOL erklären ihre Fehler mit eigenen kurzen Codes, auf dem Bildschirm, auf dem der Fehler
passiert, und stehen nicht im Fehlerbuch: Anhänge (zum Beispiel eine Datei, die zu groß ist, oder
Zugangsdaten in einer eingefügten Datei), App-Updates, Projektordner, Modellauswahl, Ausgabenlimits,
Kontakte, Plugins und Skills, Spracheingabe, gespeicherte Daten und Fehlerberichte. Ihre Meldungen sagen auf
dieselbe Weise, was passiert ist und was zu tun ist. Siehe
[Chats und Anhänge](../concepts/chats-and-attachments.md),
[Updates und Migration](updates-migration.md) und
[Fehlerberichte und Datenschutz](../trust/bug-reporting.md).

## Ein Code, den du nicht kennst

Ein Code, der nicht im Fehlerbuch steht, stammt nicht aus dieser Version von VOOL. Prüfe, welche Version ihn
angezeigt hat, und suche dann im [Fehlerbuch](../ERROR_BOOK.md) dieser Version. Stammt er doch aus dieser
Version, melde ihn mit dem Code genau so, wie er geschrieben ist, und mit der Referenz (`fault-` und zwölf
Zeichen), wenn die Antwort eine enthielt. Ein Fehler, den VOOL nicht einordnen kann, wird als
[`unknown`](../ERROR_BOOK.md#unknown) angezeigt, nie als geratene Ursache.
Siehe [Fehlerberichte und Datenschutz](../trust/bug-reporting.md).

## Bestimmte Symptome beheben

Siehe [Fehlerbehebung](../help/troubleshooting.md) für Probleme mit Installation, Modellen und Plattformen,
und [Sicherung und Wiederherstellung](../help/backup-and-recovery.md), um deine Chats, dein Gedächtnis und
deine Wallet zurückzubekommen.
