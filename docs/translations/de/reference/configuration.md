---
description: Wo VOOL seine Konfiguration speichert und was die Einstellungen bedeuten.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Konfiguration

## Speicherorte

VOOL bewahrt seine Einstellungen, Chats, sein Gedächtnis und seine Aufzeichnungen in einem einzigen „runtime home“ auf:

| Installation | Runtime home |
| --- | --- |
| macOS-App | `~/Library/Application Support/VOOL/runtime` |
| Linux-Tarball und Installationen aus dem Quellcode | `~/.vool_runtime` |
| Windows (experimentell, aus dem Quellcode) | `%LOCALAPPDATA%\VOOL` |

Wenn du `VOOL_HOME` setzt, bevor du VOOL startest, wird stattdessen dieser Ordner verwendet. Die
Startprotokolle zeigen das verwendete „runtime home“.

Schlüssel werden **nicht** in Einstellungsdateien oder Protokolle geschrieben. Sie werden verschlüsselt in einer
eigenen Datei für Zugangsdaten in diesem „runtime home“ aufbewahrt, die nur dein Benutzerkonto lesen kann;
Schlüssel, die VOOL auf einem Mac schon im macOS-Schlüsselbund aufbewahrt hat, bleiben dort. Siehe
[Sicherheitsmodell](../trust/security.md#schlüssel).

## Einstellungen

Einstellungen änderst du in der App unter **Einstellungen**. Diese hier bestimmen, was VOOL tun darf:

* **Modell** — das aktive Modell, das bestimmt, ob eine Gesprächsrunde LOKAL oder CLOUD ist.
* **Arbeitsbereich** — der Projektordner, in dem ein Chat arbeitet; siehe [Arbeitsbereiche](../concepts/workspaces.md)
  für die anderen Ordner, die VOOL erreichen kann.
* **Betriebsmodus** — wird im Menü neben dem Nachrichtenfeld gewählt, nicht hier. Manuell fragt vor jeder
  Änderung; Auto lässt alltägliche Bearbeitungen und Befehle laufen, während Löschen, Überschreiben,
  Installieren, Geldausgeben und das Ändern von Einstellungen weiterhin fragen. Siehe
  [Berechtigungen](../concepts/permissions.md).
* **Webzugriff und Krypto** — beide bleiben aus, bis du sie einschaltest.

VOOL hat keine Einstellung für Produkttelemetrie, weil es keine Produkttelemetrie sendet.

## Zurücksetzen

Beende VOOL und verschiebe das „runtime home“ an einen anderen Ort. Dateien in Arbeitsbereichen bleiben
unberührt; VOOL startet wieder mit einem frischen Profil. Deine gespeicherten Schlüssel werden mit verschoben,
füge sie also unter **Einstellungen → API-Schlüssel** wieder hinzu. Schlüssel im macOS-Schlüsselbund bleiben
dort, bis du sie in der Schlüsselbundverwaltung entfernst.
