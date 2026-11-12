---
description: Wie VOOL deine Dateien und API-Schlüssel schützt — Grenzen des Arbeitsbereichs, Speicherung von Zugangsdaten und was ein Plugin erreichen kann.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Sicherheitsmodell

## Grenzen

* **Ordnergrenzen** begrenzen den Dateizugriff. VOOL liest und ändert Dateien im Projektordner des Chats.
  Außerhalb davon kann VOOL deine sichtbaren Ordner in deinem persönlichen Ordner lesen und Dateien nur in
  „Schreibtisch“, „Downloads“ und „Dokumente“ ändern; versteckte Ordner, App- und Systemdaten sowie die eigenen
  Daten von VOOL werden nie gelesen. Siehe [Arbeitsbereiche](../concepts/workspaces.md).
* **Der Zugangsdatenspeicher** verwahrt Schlüssel. Schlüssel werden nicht in Konfigurationsdateien oder
  Protokolle geschrieben.
* **Werkzeugbestätigung** begrenzt Änderungen. Im Modus Manuell fragt jede Änderung vorher; im Modus Auto
  können alltägliche Bearbeitungen und Befehle automatisch laufen, während Löschen, Überschreiben,
  Installieren, Geldausgeben und das Ändern von Einstellungen weiterhin fragen. Siehe [Werkzeuge](../concepts/tools.md).

## Dein lokales Netzwerk

VOOL lauscht standardmäßig nur auf diesem Computer. Wenn du es so einstellst, dass es in deinem lokalen
Netzwerk lauscht, wird nur dieser Computer als du behandelt. Einstellungen, Dateien, Schlüssel, Werkzeuge und
private Funktionen antworten nur ihm.

Ein anderes Gerät im Netzwerk bekommt den einfachen Chat. Es kann außerdem jeweils eine öffentliche Webseite
lesen. Diese Seite muss im öffentlichen Internet liegen: Adressen auf diesem Computer oder in deinem lokalen
Netzwerk werden abgelehnt, und jede Weiterleitung wird auf dieselbe Weise geprüft. Websuchen, Recherche, der
Browser, Angebote, Zahlungen und deine bezahlten Schlüssel funktionieren nur auf diesem Computer.

## Discord-Brücke

Die Discord-Brücke antwortet nur ihrem Besitzer. Der erste Discord-Nutzer, der ihr einen Befehl sendet, wird
zum Besitzer. Um den Besitzer selbst festzulegen, setze `VOOL_DISCORD_OWNER_USER_ID` auf deine
Discord-Nutzer-ID; tu das für jeden Kanal, in den andere Leute schreiben können. Alle anderen erfahren einmal,
dass dieses VOOL an seinen Besitzer gebunden ist, und ihre Nachrichten werden nicht verarbeitet. Selbst die
Discord-Nachrichten des Besitzers bekommen dieselben Grenzen wie ein anderes Gerät im Netzwerk.

## Schlüssel

VOOL bewahrt deine Anbieterschlüssel in einer verschlüsselten Datei in seinem „runtime home“ auf diesem
Computer auf (siehe [Konfiguration](../reference/configuration.md)), die nur dein Benutzerkonto lesen kann.
Schlüssel, die VOOL auf einem Mac schon im macOS-Schlüsselbund aufbewahrt hat, bleiben dort lesbar. Ein
Schlüssel wird von deinem Rechner aus verwendet, um deinen Anbieter zu erreichen. Er wird nicht an Parad0x Labs
gesendet, und im Weg der Anfrage gibt es keinen von Parad0x betriebenen Endpunkt.

{% hint style="info" %}
Die verschlüsselte Datei ist durch die Dateiberechtigungen deines Benutzerkontos geschützt, nicht durch ein
eigenes Passwort. Lass die Festplattenverschlüsselung eingeschaltet (FileVault auf einem Mac), damit eine
verlorene oder gestohlene Festplatte deine Schlüssel nicht preisgibt.
{% endhint %}

## Plugins

Ein Plugin läuft mit den Zugriffsrechten der Laufzeitumgebung. Genau das macht Plugins nützlich, und genau
deshalb lohnt es sich, sie vor der Installation zu lesen. Siehe [Skills und Plugins](../guides/skills-and-plugins.md).

## Ein Problem melden

Melde Sicherheitsprobleme vertraulich über
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
nie in einem öffentlichen Issue. Gib die Version, die Plattform und die Schritte zum Nachstellen an.

{% hint style="warning" %}
Die macOS-Apps sind ad-hoc signiert und nicht notariell beglaubigt; der Linux-Tarball ist nicht signiert.
Siehe [Release-Status](release-status.md).
{% endhint %}
