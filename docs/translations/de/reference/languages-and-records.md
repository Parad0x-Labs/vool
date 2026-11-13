---
description: Sprachen, Barrierefreiheit, Aktivitätsverlauf, Belege und wie Antworten von Modellen überprüft werden.
translation: machine
source_sha256: 519707395e736b24ba96005597cca3bc59dcf12ca85d905c70f8a50884c5bba2
---

# Sprachen, Barrierefreiheit und Aufzeichnungen

## Sprachen der Oberfläche

Die Oberfläche von VOOL gibt es in mehreren Sprachen; neuere Texte können noch auf
Englisch erscheinen, bis eine Übersetzung da ist. Die Wahl der Sprache betrifft nur die Darstellung:
Berechtigungsentscheidungen, Ausgabebefugnisse und Fehlercodes sind **schon durch ihren Aufbau
sprachunabhängig** — eine übersetzte Fehlermeldung kann nie erweitern oder einschränken, was die maßgebliche
englische Instanz entschieden hat.

## Barrierefreiheit

* Tastaturkurzbefehle für die wichtigsten Abläufe (siehe [Tastaturkurzbefehle](shortcuts.md)).
* Helle und dunkle Darstellung in der Doku und in der App, die deiner Systemeinstellung folgt und sich von
  Hand umstellen lässt.
* Fehler werden als Text angegeben, nicht nur durch Farbe; zerstörerische Aktionen verlangen immer eine
  ausdrückliche Bestätigung, damit ein einzelner Fehlklick nicht reicht.

## Aktivität

Die Aktivitätsansicht ist der vollständige Verlauf dessen, was VOOL getan hat: Werkzeugaufrufe mit ihren
Eingaben und Ergebnissen, Freigaben, die du erteilt oder verweigert hast, und Zahlungen mit ihren Belegen.
Eine Gesprächsrunde, die von einem Schutz gestoppt wurde, zeigt den Stopp und den Schutz — eine Ablehnung ist
ein festgehaltenes Ereignis, keine Lücke.

## Belege

Aktionen mit Geld oder mit Bezug zu Ausgaben schreiben **Belege**: was vorgeschlagen, was freigegeben, was
gesendet und was abgewickelt wurde. Unbekannte Ergebnisse werden als unbekannt festgehalten — nie zu Erfolg
oder Misserfolg umerzählt. Das macht *„check its status before retrying“* umsetzbar: Der Beleg sagt dir, in
welchem Zustand die Zahlung tatsächlich ist.

## Status der Modellverifizierung

VOOL unterscheidet, was ein Modell **gesagt** hat, von dem, was die Laufzeitumgebung **bewiesen** hat:

* Werkzeugergebnisse werden mit ihren Ausführungsdatensätzen festgehalten; die Aussagen einer Antwort über
  Dateien, Zahlungen oder Daten sind an diese Datensätze gebunden, und nicht belegte Aussagen werden
  zurückgehalten.
* Belege tragen einen Verifizierungsstatus; wo ein Weg in diesem Build experimentell oder nicht überprüfbar
  ist, sagt der Status das, statt einen Beweis vorzutäuschen.

Siehe [Belege](../concepts/receipts.md) für das Konzept und [Fehler und Fehlercodes](errors.md) dafür, wie
Fehler eingeordnet werden.
