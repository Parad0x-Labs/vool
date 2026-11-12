---
description: Werkzeuge sind die Aktionen, die VOOL ausführen kann, und jede davon wird protokolliert.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Werkzeuge

Ein Werkzeug ist eine Aktion, die die Laufzeitumgebung in deinem Auftrag ausführen kann — eine Datei lesen,
einen Befehl ausführen, einen Ordner durchsuchen.

## Von Grund auf nachprüfbar

Jeder Werkzeugaufruf hält fest, was angefordert wurde, was lief und was zurückkam. Ein Lauf lässt sich
im Nachhinein nachlesen, ohne ihn erneut auszuführen.

## Berechtigung

Ob ein Werkzeug sofort läuft oder vorher fragt, hängt vom gewählten Modus und von der Aktion ab.
Ein neuer Chat startet in Manuell, wo jede Änderung vorher fragt. In Auto laufen das Erstellen und Bearbeiten
von Dateien, Befehle und Netzwerkaktionen ohne Nachfrage, während Löschen, Überschreiben, Installieren,
Git-Commits und -Pushes, Deployment, das Senden von Nachrichten, Zahlungen und das Ändern von Einstellungen
weiterhin fragen. In jedem Modus arbeiten Dateiwerkzeuge nur innerhalb des [Arbeitsbereichs](workspaces.md),
Such- und Abrufwerkzeuge lesen öffentliche Ressourcen, ohne etwas zu schreiben, und gespeicherte Geheimnisse
werden nie gelesen. [Berechtigungen](permissions.md) beschreibt alle fünf Modi und was jede
Freigabeschaltfläche erlaubt.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Siehe auch [Belege](receipts.md).
