---
description: So nutzt du VOOL als KI-Programmierassistenten für deine eigene Codebasis — den Arbeitsbereich festlegen, Fragen zum Code stellen und Bearbeitungen prüfen.
translation: machine
source_sha256: 112dfd866a96b0f7fd22f6f57f7f6480d7ac293466fdf0260a74f3bc0b870036
---

# Mit VOOL programmieren

## Den Arbeitsbereich auf das Repository setzen

Richte den [Arbeitsbereich](../concepts/workspaces.md) auf das Stammverzeichnis des Projekts aus. VOOL liest die
Dateien, die er braucht, statt den ganzen Verzeichnisbaum zu laden.

## In Begriffen des Codes fragen

Wenn du eine Datei oder ein Symbol nennst, hat die Laufzeitumgebung einen Ausgangspunkt:

> Finde, wo in `src/auth/` das Sitzungs-Timeout festgelegt wird, und erkläre, was passiert, wenn es
> abläuft.

## Vor dem Anwenden prüfen

Im Modus Manuell fragt jede Bearbeitung vorher. Im Modus Auto können alltägliche Bearbeitungen und Befehle
automatisch laufen und erscheinen im Aktivitätsprotokoll, während Löschen und Überschreiben weiterhin fragen.
Committe vor einer großen Änderung, damit der Diff leicht zu lesen und leicht rückgängig zu machen ist.

## Lokal oder Cloud

Programmieren funktioniert in beiden Modi. Ein lokales 7B-Programmiermodell bewältigt die meisten alltäglichen
Bearbeitungen; größere Cloud-Modelle bewältigen längeres Nachdenken. Die Modusanzeige sagt dir, welcher Modus
aktiv ist, bevor du sendest.
